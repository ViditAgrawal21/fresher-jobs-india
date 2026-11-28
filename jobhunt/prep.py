"""Interview prep sheets: one page per job, made when it reaches Assessment or Interviewing.

Each sheet (applications/<job>/interview_prep.md, also shown on the dashboard) has what the role
is about, the stories from your own work that fit it best, likely technical and behavioural
questions with hints drawn from your profile, topics to revise (gaps first) and questions to
ask them.

With ANTHROPIC_API_KEY, Claude writes it from the JD and your profile (about $0.05 a sheet);
without one, it's built from a question bank for the skills the JD names. Hints only use facts
from profile.yaml either way: read them as reminders, not scripts.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

from jobhunt.keywords import DOMAIN, extract_terms, slug, years_required
from jobhunt.profile import Profile, load_profile
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker

log = logging.getLogger(__name__)

PREP_FILE = "interview_prep.md"
PREP_STAGES = ("Assessment", "Interviewing")


class PrepQuestion(BaseModel):
    question: str
    why: str = Field(description="Why they'd ask it, from the JD")
    answer_hint: str = Field(description="What to say, using only facts from the candidate's profile")


class PrepStory(BaseModel):
    title: str
    bullet_ids: list[str] = Field(description="Profile bullet IDs this story uses")
    how_to_tell: str = Field(description="Situation, what you did, result: in 2-3 sentences, profile facts only")


class PrepSheet(BaseModel):
    role_summary: str = Field(description="2-3 sentences: what this role needs, from the JD")
    focus_areas: list[str] = Field(description="What the interview will most likely test, most likely first")
    technical_questions: list[PrepQuestion]
    behavioural_questions: list[PrepQuestion]
    stories: list[PrepStory]
    revise: list[str] = Field(description="Topics to revise before the interview; gaps in the profile first")
    questions_to_ask: list[str]


# ---------- the offline sheet ----------

QUESTION_BANK: dict[str, list[str]] = {
    "Node.js": ["How does the Node.js event loop work, and what blocks it?", "How do you handle errors in async code and unhandled promise rejections?",
                "Streams vs reading a whole file into memory: when and why?"],
    "TypeScript": ["Interfaces vs types; when do you use generics?", "How do you type an API response you don't fully trust?"],
    "JavaScript": ["Explain closures and a real bug they caused or fixed.", "What happens with `this` in arrow functions vs normal functions?"],
    "NestJS": ["How do modules, providers and dependency injection fit together in NestJS?", "Where do guards, interceptors and pipes run in a request?"],
    "Express.js": ["How does middleware order matter in Express?", "How would you structure error handling across routes?"],
    "React": ["How does React decide what to re-render, and how do you avoid needless renders?", "useEffect pitfalls: dependencies, cleanup, race conditions.",
              "Where should state live: local, context, or a store?"],
    "Next.js": ["Server vs client components: what runs where?", "SSR, SSG and ISR: when would you pick each?"],
    "React Native": ["How do you find and fix a slow screen in React Native?", "How did you handle auth tokens and offline data on mobile?"],
    "Python": ["Generators vs lists: when does it matter?", "How do you manage dependencies and virtual environments in a project?"],
    "FastAPI": ["How does FastAPI use type hints and Pydantic for validation?", "Sync vs async endpoints: what changes?"],
    "Django": ["How does the Django ORM avoid N+1 queries (select_related / prefetch_related)?"],
    "PostgreSQL": ["How do indexes work, and when does a query not use one?", "Transactions and isolation levels: a case where it mattered.",
                   "How do you read an EXPLAIN plan?"],
    "MySQL": ["How do you find and fix a slow query?"],
    "MongoDB": ["Embedding vs referencing documents: how do you decide?", "Which indexes would you add for a given query?"],
    "SQL": ["Write a query: top N per group (window functions).", "INNER vs LEFT JOIN with an example."],
    "Redis": ["Caching patterns: cache-aside, TTLs, and invalidation.", "What else is Redis good for: queues, rate limits, locks?"],
    "BullMQ": ["How do retries, backoff and dead jobs work in a job queue?"],
    "Kafka": ["Topics, partitions and consumer groups: how is ordering kept?", "At-least-once vs exactly-once delivery."],
    "Message Queues": ["Why put a queue between two services? What can go wrong?"],
    "REST APIs": ["Design the endpoints for a resource, with pagination and errors.", "Idempotency: how do you make a POST safe to retry?"],
    "GraphQL": ["GraphQL vs REST: trade-offs, and the N+1 problem in resolvers."],
    "Microservices": ["When is a monolith the better choice?", "How do services talk, and how do you handle one being down?"],
    "WebSockets": ["How would you scale WebSocket connections across servers?"],
    "Authentication": ["Sessions vs JWTs; how do refresh tokens work?", "How do you store passwords and secrets safely?"],
    "Docker": ["What goes into a good Dockerfile (layers, image size, non-root)?"],
    "Kubernetes": ["Pods, deployments, services: how does a request reach your app?"],
    "AWS": ["Which AWS services have you used, and how did you pick them?", "How do IAM roles and least privilege work?"],
    "GCP": ["How does Cloud Run scale, and what are cold starts?"],
    "CI/CD": ["Walk through a pipeline you set up: tests, builds, deploys, rollbacks."],
    "System Design": ["Design a URL shortener / rate limiter / notification service.", "How would you scale a read-heavy API?",
                      "Where would you add caching, queues and a CDN?"],
    "Data Structures & Algorithms": ["Arrays and hash maps: two-sum style problems.", "Sliding window / two pointers.", "BFS/DFS on a graph or tree."],
    "LLM": ["How do you make LLM output reliable enough for production?", "How do you control cost and latency of LLM calls?"],
    "RAG": ["Walk through a RAG pipeline: chunking, embeddings, retrieval, generation.", "How do you know retrieval is good? How do you evaluate it?",
            "What do you do when the model answers beyond the retrieved context?"],
    "AI Agents": ["What makes an agent different from a single LLM call?", "How do you stop loops and bad tool calls?"],
    "Prompt Engineering": ["How do you iterate on a prompt without fooling yourself?"],
    "LLM Evals": ["How did you build an eval set, and what did it change?"],
    "Vector Databases": ["How does approximate nearest-neighbour search work, and what are its limits?"],
    "Embeddings": ["How do you choose an embedding model and chunk size?"],
    "MCP": ["What is MCP, and how would you expose a tool through it?"],
    "Structured Outputs": ["How do you get reliable JSON out of an LLM?"],
    "Linux": ["How do you debug a process that's using too much CPU or memory?"],
    "Unit Testing": ["What do you unit test vs integration test?"],
    "Java": ["(Gap) Core Java and Spring Boot basics: be honest about your level and how you'd ramp up."],
    "Go": ["(Gap) Go basics: goroutines and channels; show how you'd learn it fast."],
}

BEHAVIOURAL = [
    ("Tell me about yourself.", "The opener sets the tone", "Two minutes: current role and what you own, the project closest to this role, why this job now."),
    ("Tell me about a project you led end to end.", "They want ownership", "Pick the story below that matches the role best; say what you decided, not just what you built."),
    ("Describe a hard bug or production issue and how you solved it.", "Debugging under pressure",
     "Situation, how you narrowed it down, the fix, and what you changed so it doesn't happen again."),
    ("A time you disagreed with a client or teammate.", "Collaboration", "Show you listened, used data, and kept the relationship."),
    ("What would you do in your first 90 days here?", "Planning and fit", "Learn the codebase and users, ship something small early, then own a piece."),
    ("Why {company}?", "Motivation", "Tie what they build (from the JD) to what you've built and want to do next."),
]

QUESTIONS_TO_ASK = [
    "What would a great first three months look like in this role?",
    "How is the team set up, and who would I work with day to day?",
    "How do you ship: code review, testing, deploys, on-call?",
    "What's the hardest technical problem the team is working on right now?",
    "What does the interview process look like from here?",
]


def _ranked_bullets(profile: Profile, terms: set[str]) -> list[tuple[str, str, set[str]]]:
    """Profile bullets ranked by how many of the JD's skills they show: (id, text, skills)."""
    out = []
    for item in list(profile.experience) + list(profile.projects):
        for b in item.bullets:
            hit = extract_terms(b.text) & terms
            if hit:
                out.append((b.id, b.text, hit))
    return sorted(out, key=lambda x: -len(x[2]))


def offline_sheet(profile: Profile, jd: str, company: str, role: str) -> PrepSheet:
    terms = extract_terms(jd) - DOMAIN
    have = profile.supported_terms()
    matched = sorted(terms & have, key=lambda t: jd.lower().find(t.lower()) if t.lower() in jd.lower() else 99999)
    missing = sorted(terms - have)
    bullets = _ranked_bullets(profile, terms)
    best_for = {t: next((b for b in bullets if t in b[2]), None) for t in matched}
    tech = []
    for t in matched[:8] + missing[:3]:
        for q in QUESTION_BANK.get(t, [])[:2]:
            b = best_for.get(t)
            hint = (f"Use your own example: {b[1]}" if b else
                    f"Not on your profile yet: learn the basics of {t} and say how you'd ramp up.")
            tech.append(PrepQuestion(question=q, why=f"The JD asks for {t}.", answer_hint=hint))
    if "System Design" not in terms:
        tech.append(PrepQuestion(question=QUESTION_BANK["System Design"][1], why="Most SDE-2 loops include a design round.",
                                 answer_hint="Start from requirements and numbers; draw the API, data model and where caching and queues go."))
    tech.append(PrepQuestion(question=QUESTION_BANK["Data Structures & Algorithms"][0], why="Most loops include a coding round.",
                             answer_hint="Talk through brute force first, then optimise; state time and space complexity."))
    stories = [PrepStory(title=", ".join(sorted(hit))[:60], bullet_ids=[bid], how_to_tell=text) for bid, text, hit in bullets[:4]]
    years = years_required(jd)
    return PrepSheet(
        role_summary=(f"{company} is hiring a {role}" + (f" with {years}+ years of experience" if years else "")
                      + (f". The JD leans on {', '.join(matched[:5])}" if matched else "")
                      + (f"; less familiar ground for you: {', '.join(missing[:4])}." if missing else ".")),
        focus_areas=(matched[:6] or ["Fundamentals of the role"]) + ["Data Structures & Algorithms", "System Design"],
        technical_questions=tech[:14],
        behavioural_questions=[PrepQuestion(question=q.replace("{company}", company), why=w, answer_hint=h) for q, w, h in BEHAVIOURAL],
        stories=stories,
        revise=[f"{t} (gap: basics and one small exercise)" for t in missing[:5]] + [f"{t} (refresh)" for t in matched[:6]]
               + ["DSA: arrays, hash maps, two pointers, sliding window, trees/graphs", "System design: caching, queues, scaling reads"],
        questions_to_ask=QUESTIONS_TO_ASK,
    )


# ---------- Claude ----------

_PROMPT = """You help a software engineer prepare for one job interview. Write a prep sheet from the job
description and the candidate's profile.

Rules:
- Every answer hint and story uses only facts in <profile> (cite bullet IDs in stories). Never invent
  numbers, employers, projects or skills. Where the profile has no evidence, say so and suggest how to
  prepare honestly.
- Questions should be the ones this JD makes likely: its stack, its domain, its seniority. 8-12 technical,
  5-6 behavioural (always include "Tell me about yourself"), 3-4 stories, 4-6 questions to ask them.
- Topics to revise: gaps first, then what to refresh; concrete (e.g. "PostgreSQL isolation levels"), not vague.
- Plain, direct English. No filler."""


def claude_sheet(profile: Profile, jd: str, company: str, role: str, stage: str, settings: JobhuntSettings) -> PrepSheet:
    import anthropic

    from jobhunt.tailor import TailorError, _profile_for_prompt

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    kwargs = dict(
        model=settings.tailor_model,
        max_tokens=16000,
        system=[{"type": "text", "text": f"{_PROMPT}\n\n<profile>\n{_profile_for_prompt(profile)}\n</profile>",
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Company: {company}\nRole: {role}\nStage: {stage}\n\n"
                                              f"<job_description>\n{jd.strip()}\n</job_description>\n\nWrite the prep sheet."}],
        output_format=PrepSheet,
        output_config={"effort": settings.tailor_effort},
    )
    try:
        if settings.tailor_model in ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-2", "claude-fable-5-1", "claude-sonnet-5-5"):
            # If a safety classifier declines, the API retries on a fallback model in the same call.
            resp = client.beta.messages.parse(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = client.messages.parse(**kwargs)
    except anthropic.APIError as exc:
        raise TailorError(f"Claude couldn't write the prep sheet: {exc}") from exc
    except TypeError as exc:  # no credentials at all
        raise TailorError("no Anthropic API key") from exc
    if resp.stop_reason != "end_turn" or resp.parsed_output is None:
        raise TailorError(f"no usable prep sheet (stop_reason={resp.stop_reason})")
    return resp.parsed_output


# ---------- writing it ----------

def render_markdown(sheet: PrepSheet, *, company: str, role: str, stage: str, next_step: str, url: str, made_by: str,
                    profile: Profile) -> str:
    texts = {b.id: b.text for item in list(profile.experience) + list(profile.projects) for b in item.bullets}
    out = [f"# Interview prep: {company}, {role}", "",
           f"Stage: **{stage}**" + (f" · Next: {next_step}" if next_step else "") + f" · Made {date.today():%d %b %Y} ({made_by})"]
    if url:
        out.append(f"Job: {url}")
    out += ["", "## What they want", "", sheet.role_summary, "", "**Likely focus:** " + ", ".join(sheet.focus_areas), "",
            "## Your stories (have these ready)", ""]
    for s in sheet.stories:
        out.append(f"- **{s.title}**: {s.how_to_tell}")
        for bid in s.bullet_ids:
            if bid in texts and texts[bid] != s.how_to_tell:
                out.append(f"  - From your resume: {texts[bid]}")
    out += ["", "## Technical questions", ""]
    for q in sheet.technical_questions:
        out += [f"- **{q.question}**", f"  - Why: {q.why}", f"  - Your angle: {q.answer_hint}"]
    out += ["", "## Behavioural questions", ""]
    for q in sheet.behavioural_questions:
        out += [f"- **{q.question}**", f"  - Your angle: {q.answer_hint}"]
    out += ["", "## Revise before the interview", ""] + [f"- [ ] {t}" for t in sheet.revise]
    out += ["", "## Questions to ask them", ""] + [f"- {q}" for q in sheet.questions_to_ask]
    out += ["", "## On the day", "", "- [ ] Re-read the JD and your tailored resume for this job",
            "- [ ] Test camera, mic and screen sharing; keep a code editor open", "- [ ] Water, notebook, a quiet room",
            "", "_Hints use only facts from your profile. Say them in your own words._", ""]
    return "\n".join(out)


def make_prep(settings: JobhuntSettings, app_id: str, *, use_claude: bool | None = None, lock=None) -> Path:
    """Write (or rewrite) interview_prep.md for an application and return its path."""
    from contextlib import nullcontext

    from jobhunt.tailor import TailorError

    with lock or nullcontext():
        t = Tracker(settings.tracker_path)
        app = t.find(app_id)
        if app is None:
            raise KeyError(f"No application {app_id}")
        app = dict(app.values)
    profile = load_profile(settings.profile_path)
    if app.get("resume"):
        folder = (settings.data_dir / str(app["resume"])).parent
    else:
        folder = settings.data_dir / "applications" / f"{app['id']}_{slug(app['company'] or '', 24)}_{slug(app['role'] or '', 32)}"
    folder.mkdir(parents=True, exist_ok=True)
    jd_path = folder / "job_description.txt"
    jd = jd_path.read_text(encoding="utf-8") if jd_path.exists() else ""
    if len(jd) < 200 and app.get("url"):
        try:
            from jobhunt.fetch_jd import fetch_job
            jd = fetch_job(str(app["url"]))["jd"]
            jd_path.write_text(jd, encoding="utf-8")
        except Exception as exc:  # LinkedIn, a closed job...: fall back to what the tracker knows
            log.info("no JD for %s: %s", app_id, exc)
    if len(jd) < 200:
        jd = f"{app.get('role') or ''} at {app.get('company') or ''}. Skills: {app.get('matched') or ''}, {app.get('missing') or ''}"
    company, role, stage = app.get("company") or "", app.get("role") or "", app.get("status") or ""
    use_claude = bool(settings.anthropic_api_key) if use_claude is None else use_claude
    made_by = "Claude" if use_claude else "question bank"
    try:
        sheet = claude_sheet(profile, jd, company, role, stage, settings) if use_claude else offline_sheet(profile, jd, company, role)
    except TailorError as exc:
        log.warning("prep via Claude failed, using the question bank: %s", exc)
        sheet, made_by = offline_sheet(profile, jd, company, role), "question bank"
    md = render_markdown(sheet, company=company, role=role, stage=stage, next_step=str(app.get("next_step") or ""),
                         url=str(app.get("url") or ""), made_by=made_by, profile=profile)
    path = folder / PREP_FILE
    path.write_text(md, encoding="utf-8")
    if not app.get("resume"):
        with lock or nullcontext():
            t = Tracker(settings.tracker_path)
            t.append_note(app["id"], f"[{date.today():%d %b}] interview prep sheet: applications/{folder.name}/{PREP_FILE}")
            t.save()
    return path


def prep_path(settings: JobhuntSettings, app: dict) -> Path | None:
    """Where an application's prep sheet is, if it has one."""
    folder = None
    if app.get("resume"):
        folder = (settings.data_dir / str(app["resume"])).parent
    else:
        root = settings.data_dir / "applications"
        hits = sorted(root.glob(f"{app.get('id')}_*")) if root.exists() and app.get("id") else []
        folder = hits[0] if hits else None
    p = folder / PREP_FILE if folder else None
    return p if p and p.exists() else None


def markdown_to_html(md: str, title: str) -> str:
    """A small, safe Markdown subset (headings, bullets, checkboxes, bold, links) as a printable page."""
    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"<em>\1</em>", s)
        return re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener">\1</a>', s)

    body, depth = [], 0
    for line in md.splitlines():
        m = re.match(r"^(\s*)- (\[ \] )?(.*)$", line)
        if m:
            level = 1 + len(m.group(1)) // 2
            while depth < level:
                body.append("<ul>"); depth += 1
            while depth > level:
                body.append("</ul>"); depth -= 1
            box = '<input type="checkbox"> ' if m.group(2) else ""
            body.append(f"<li>{box}{inline(m.group(3))}</li>")
            continue
        while depth:
            body.append("</ul>"); depth -= 1
        if line.startswith("## "):
            body.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.strip():
            body.append(f"<p>{inline(line)}</p>")
    while depth:
        body.append("</ul>"); depth -= 1
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
:root {{ --ink: #1a1a1a; --muted: #555; --line: #ddd; --bg: #fff; --accent: #2563eb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ink: #eee; --muted: #aaa; --line: #333; --bg: #111; --accent: #60a5fa; }} }}
body {{ background: var(--bg); color: var(--ink); font: 15px/1.55 system-ui, sans-serif; max-width: 820px; margin: 0 auto; padding: 24px 16px 48px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }} h2 {{ font-size: 17px; margin: 22px 0 6px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }}
ul {{ margin: 4px 0; padding-left: 20px; }} li {{ margin: 3px 0; }} p {{ margin: 6px 0; }} a {{ color: var(--accent); }}
em {{ color: var(--muted); }} @media print {{ body {{ font-size: 12px; padding: 0; }} a {{ color: inherit; }} }}
</style></head><body>{''.join(body)}</body></html>"""
