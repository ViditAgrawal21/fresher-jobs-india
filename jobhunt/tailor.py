"""Tailor the master profile to one job posting.

Two ways to produce a `Tailoring` (which bullets, in what order, reworded how):
  * `llm_tailor`     - Claude reads the posting and the profile (structured output).
  * `offline_tailor` - keyword overlap only; free and instant, no rewording.

Either way the result goes through `apply_tailoring`, a deterministic guard that reverts
any reworded line that adds a technology or a number the profile doesn't back up, drops
unknown ids, and keeps every job on the page. The LLM can choose and phrase; it can't invent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from jobhunt.keywords import GENERIC, extract_terms, first_positions, numbers_in, years_required
from jobhunt.profile import (
    TRACKS, Profile, ResumeDoc, Track, job_entry, ordered_skills, project_entry, track_order,
)

log = logging.getLogger(__name__)

PROMPT_VERSION = "tailor_v1"
_PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")

# Models that accept server-side refusal fallbacks (fallbacks="default").
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2"}

# Keywords that point at each track, on top of the track's title pattern.
TRACK_TERMS: dict[str, set[str]] = {
    "backend": {"Node.js", "NestJS", "Express.js", "REST APIs", "Microservices", "PostgreSQL", "MySQL", "MongoDB",
                "Redis", "Kafka", "RabbitMQ", "Message Queues", "System Design", "Go", "Java", "Spring Boot",
                "GraphQL", "gRPC", "Authentication", "Docker", "Kubernetes", "AWS", "GCP", "Serverless"},
    "fullstack": {"React", "Next.js", "Node.js", "TypeScript", "JavaScript", "Redux", "Tailwind CSS", "HTML", "CSS",
                  "Angular", "Vue", "MongoDB", "Express.js", "GraphQL"},
    "ai": {"LLM", "Generative AI", "RAG", "LangChain", "LlamaIndex", "OpenAI", "Claude", "Vector Databases",
           "Embeddings", "Prompt Engineering", "AI Agents", "MCP", "Tool Calling", "Machine Learning", "NLP",
           "LLM Evals", "Structured Outputs", "Hugging Face", "PyTorch"},
    "mobile": {"React Native", "Flutter", "Android", "iOS", "Expo", "Kotlin", "Swift", "Dart"},
}


# Domain words the extractor finds that aren't skills you'd say you "work with".
DOMAIN_TERMS = {"Payments", "Multi-tenant SaaS", "Real-time", "IoT", "Authentication", "REST APIs", "Data Pipelines"}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ---------- the tailoring schema (also the LLM's structured output) ----------

class BulletPick(BaseModel):
    source_id: str = Field(description="id of a bullet from this entry's bullet bank")
    text: str = Field(description="that bullet reworded for this posting; same facts, nothing added")


class EntryPick(BaseModel):
    id: str = Field(description="job or project id from the profile")
    bullets: list[BulletPick]


class Fit(BaseModel):
    score: int = Field(description="0-100: how well the candidate meets the must-have requirements")
    verdict: Literal["strong", "good", "stretch", "skip"]
    years_required: str
    matched: list[str]
    gaps: list[str]
    red_flags: list[str]


class Tailoring(BaseModel):
    track: Track
    headline: str
    summary: str
    skill_order: list[str]
    skills_first: list[str]
    experience: list[EntryPick]
    projects: list[EntryPick]
    fit: Fit
    recruiter_note: str


@dataclass
class TailorResult:
    tailoring: Tailoring
    doc: ResumeDoc
    source: Literal["llm", "offline", "manual"]
    warnings: list[str] = field(default_factory=list)
    keyword_coverage: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


class TailorError(RuntimeError):
    pass


# ---------- track choice ----------

def pick_track(profile: Profile, jd: str, role: str) -> str:
    terms = extract_terms(jd + "\n" + role)
    best, best_score = "backend", -1.0
    for t in TRACKS:
        score = 2 * len(terms & TRACK_TERMS[t])
        pattern = profile.tracks[t].title_pattern
        if pattern and re.search(pattern, role or "", re.I):
            score += 6
        if score > best_score:
            best, best_score = t, score
    return best


def keyword_coverage(profile: Profile, jd: str, role: str = "") -> dict:
    """Deterministic match of posting keywords against what the profile supports."""
    text = role + "\n" + jd
    jd_terms = extract_terms(text)
    supported = profile.supported_terms()
    specific = jd_terms - GENERIC or jd_terms
    pos = first_positions(text)
    first = lambda term: (pos.get(term, len(text)), term)  # noqa: E731 - order by what the post says first
    matched = sorted(specific & supported, key=first)
    missing = sorted(specific - supported, key=first)
    pct = round(100 * len(matched) / len(specific)) if specific else 0
    return {"matched": matched, "missing": missing, "coverage_pct": pct, "years_required": years_required(jd)}


# ---------- offline (keyword) tailoring ----------

def offline_tailor(profile: Profile, jd: str, company: str, role: str) -> Tailoring:
    jd_terms = extract_terms(jd + "\n" + role)
    track = pick_track(profile, jd, role)
    spec = profile.tracks[track]

    def bullet_score(b) -> float:
        return len(extract_terms(b.text) & jd_terms) + (0.5 if track in b.tags else 0)

    experience = []
    for job in profile.experience:
        ranked = sorted(job.bullets, key=bullet_score, reverse=True)  # stable: ties keep profile order
        experience.append(EntryPick(id=job.id, bullets=[BulletPick(source_id=b.id, text=b.text) for b in ranked]))

    def project_score(p) -> float:
        text = " ".join([p.stack_line, *p.stack, *(b.text for b in p.bullets)])
        rank_bonus = (len(spec.projects) - spec.projects.index(p.id)) * 0.4 if p.id in spec.projects else 0
        return len(extract_terms(text) & jd_terms) + rank_bonus

    projects = sorted(profile.projects, key=project_score, reverse=True)[:4]
    picks = [EntryPick(id=p.id, bullets=[BulletPick(source_id=b.id, text=b.text)
                                         for b in sorted(p.bullets, key=bullet_score, reverse=True)])
             for p in projects]

    skills_first = [s for s in profile.all_skills() if extract_terms(s) & jd_terms][:8]
    cov = keyword_coverage(profile, jd, role)
    years = cov["years_required"]
    score = cov["coverage_pct"]
    red_flags = []
    if years is not None and years > 4:
        score -= 25
        red_flags.append(f"asks for {years}+ years")
    score = max(0, min(100, score))
    verdict = "strong" if score >= 70 else "good" if score >= 50 else "stretch" if score >= 30 else "skip"
    top = [m for m in cov["matched"] if m not in DOMAIN_TERMS][:3] or skills_first[:3]
    note = (
        f"Hi, I've applied for the {role} role at {company}. I'm a software engineer with "
        f"{profile.years_of_experience} of experience"
        + (f", working hands-on with {_join(top)}" if top else "")
        + ". At Sakalya Technologies I led two products end to end, and I built Fresher Jobs India, "
        "an LLM-tagged job search pipeline with a measured eval. Happy to walk you through either."
    )
    return Tailoring(
        track=track,
        headline=spec.headline,
        summary=profile.track_summary(track),
        skill_order=spec.skill_order,
        skills_first=skills_first,
        experience=experience,
        projects=picks,
        fit=Fit(score=score, verdict=verdict,
                years_required=f"{years}+ years" if years is not None else "not stated",
                matched=cov["matched"], gaps=cov["missing"], red_flags=red_flags),
        recruiter_note=note,
    )


# ---------- Claude tailoring ----------

def _profile_for_prompt(profile: Profile) -> str:
    data = {
        "name": profile.name,
        "years_of_experience": profile.years_of_experience,
        "skills": [g.model_dump() for g in profile.skills],
        "experience": [
            {"id": j.id, "company": j.company, "title": j.title, "dates": f"{j.start} - {j.end}", "stack": j.stack,
             "bullets": [{"id": b.id, "text": b.text} for b in j.bullets]}
            for j in profile.experience
        ],
        "projects": [
            {"id": p.id, "name": p.name, "stack": p.stack or [p.stack_line],
             "bullets": [{"id": b.id, "text": b.text} for b in p.bullets]}
            for p in profile.projects
        ],
        "education": [e.model_dump() for e in profile.education],
        "achievements": profile.achievements,
        "tracks": {k: {"headline": v.headline, "summary": profile.track_summary(k)} for k, v in profile.tracks.items()},
    }
    return json.dumps(data, indent=1, ensure_ascii=False)


def llm_tailor(profile: Profile, jd: str, company: str, role: str, *, model: str, effort: str,
               api_key: str = "") -> tuple[Tailoring, dict]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key or None)
    system = f"{_PROMPT}\n\n<profile>\n{_profile_for_prompt(profile)}\n</profile>"
    user = (f"Company: {company}\nRole: {role}\n\n<job_description>\n{jd.strip()}\n</job_description>\n\n"
            "Tailor the resume for this posting.")
    kwargs = dict(
        model=model,
        max_tokens=16000,
        # The profile is identical on every call, so it's cached after the first application.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=Tailoring,
        output_config={"effort": effort},
    )
    try:
        if model in _FALLBACK_MODELS:
            # If a safety classifier declines, the API retries on a fallback model in the same call.
            resp = client.beta.messages.parse(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = client.messages.parse(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise TailorError("Anthropic API key missing or invalid (set ANTHROPIC_API_KEY in .env)") from exc
    except anthropic.RateLimitError as exc:
        raise TailorError("rate limited by the Anthropic API; try again in a minute") from exc
    except anthropic.APIStatusError as exc:
        raise TailorError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise TailorError("couldn't reach the Anthropic API (network)") from exc
    except TypeError as exc:  # the SDK raises TypeError when it finds no credentials at all
        if "authentication" in str(exc).lower():
            raise TailorError("no Anthropic API key (set ANTHROPIC_API_KEY in .env)") from exc
        raise

    if resp.stop_reason == "refusal":
        raise TailorError("the model declined this posting")
    if resp.stop_reason != "end_turn" or resp.parsed_output is None:
        raise TailorError(f"no usable output (stop_reason={resp.stop_reason})")
    u = resp.usage
    usage = {
        "model": resp.model,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "request_id": getattr(resp, "_request_id", None),
    }
    return resp.parsed_output, usage


# ---------- the fact guard ----------

def _unsafe(new: str, source: str, allowed_terms: set[str], allowed_numbers: set[str], *,
            max_len: int | None = None, min_similarity: int | None = 45) -> str | None:
    """Why a reworded line can't be used, or None if it's safe."""
    new = (new or "").strip()
    if not new:
        return "empty"
    added = extract_terms(new) - extract_terms(source) - allowed_terms
    if added:
        return f"adds {', '.join(sorted(added))}"
    nums = numbers_in(new) - numbers_in(source) - allowed_numbers
    if nums:
        return f"adds number {', '.join(sorted(nums))}"
    limit = max_len if max_len is not None else int(len(source) * 1.35) + 40
    if len(new) > limit:
        return "too long"
    if min_similarity is not None and fuzz.token_set_ratio(new.lower(), source.lower()) < min_similarity:
        return "drifted from the original"
    return None


def apply_tailoring(profile: Profile, t: Tailoring, contact: list[str]) -> tuple[ResumeDoc, list[str]]:
    """Turn a Tailoring into a ResumeDoc, reverting anything the profile can't back up."""
    warnings: list[str] = []
    track = t.track if t.track in profile.tracks else "backend"
    spec = profile.tracks[track]
    supported = profile.supported_terms()
    known_numbers = profile.known_numbers()

    headline = t.headline.strip()
    if why := _unsafe(headline, spec.headline, supported, set(), max_len=100, min_similarity=None):
        warnings.append(f"headline reverted ({why})")
        headline = spec.headline

    summary = t.summary.strip()
    default_summary = profile.track_summary(track)
    if why := _unsafe(summary, default_summary, supported, known_numbers, max_len=420, min_similarity=None):
        warnings.append(f"summary reverted ({why})")
        summary = default_summary

    # Jobs: every job stays, in profile (chronological) order.
    picks = {p.id: p for p in t.experience}
    experience = []
    for job in profile.experience:
        allowed = extract_terms(" ".join(job.stack))
        ids, texts = _checked_bullets(picks.get(job.id), {b.id: b.text for b in job.bullets}, allowed, job.company, warnings)
        if not ids:
            if job.id not in picks:
                warnings.append(f"{job.company}: not in tailoring, used default bullets")
            ids, texts = track_order(job.bullets, track), {}
        experience.append(job_entry(job, ids, texts))

    projects = []
    for pick in t.projects:
        project = profile.project(pick.id)
        if project is None:
            warnings.append(f"unknown project id {pick.id!r} dropped")
            continue
        if any(e.id == project.id for e in projects):
            continue
        allowed = extract_terms(" ".join(project.stack) + " " + project.stack_line)
        ids, texts = _checked_bullets(pick, {b.id: b.text for b in project.bullets}, allowed, project.name, warnings)
        if not ids:
            ids, texts = track_order(project.bullets, track), {}
        projects.append(project_entry(project, ids, texts))
        if len(projects) == 4:
            break
    if not projects:
        warnings.append("no valid projects in tailoring, used the track's defaults")
        projects = [project_entry(p, track_order(p.bullets, track))
                    for p in (profile.project(pid) for pid in spec.projects) if p]

    known_skills = {s.lower() for s in profile.all_skills()}
    first = [s for s in t.skills_first if s.lower() in known_skills]
    if dropped := [s for s in t.skills_first if s.lower() not in known_skills]:
        warnings.append(f"skills not in profile ignored: {', '.join(dropped)}")
    categories = [g.category for g in profile.skills]
    order = [c for c in t.skill_order if c in categories] + [c for c in spec.skill_order if c not in t.skill_order]

    doc = ResumeDoc(
        name=profile.name,
        headline=headline,
        contact=[c for c in contact if c],
        links=profile.links,
        summary=summary,
        skills=ordered_skills(profile, order, first),
        experience=experience,
        projects=projects,
        education=profile.education,
        achievements=profile.achievements,
    )
    return doc, warnings


def _checked_bullets(pick: EntryPick | None, bank: dict[str, str], allowed_terms: set[str], where: str,
                     warnings: list[str]) -> tuple[list[str], dict[str, str]]:
    if pick is None:
        return [], {}
    ids, texts = [], {}
    for b in pick.bullets:
        if b.source_id not in bank:
            warnings.append(f"{where}: unknown bullet id {b.source_id!r} dropped")
            continue
        if b.source_id in ids:
            continue
        ids.append(b.source_id)
        source = bank[b.source_id]
        if why := _unsafe(b.text, source, allowed_terms, set()):
            warnings.append(f"{where}: kept original wording of {b.source_id} (rewrite {why})")
        else:
            texts[b.source_id] = b.text.strip()
    return ids, texts


# ---------- entry point ----------

def tailor(profile: Profile, jd: str, company: str, role: str, contact: list[str], *, offline: bool = False,
           model: str = "claude-opus-5-5", effort: str = "medium", api_key: str = "",
           manual: Tailoring | None = None) -> TailorResult:
    usage: dict = {}
    notes: list[str] = []
    if manual is not None:
        t, source = manual, "manual"
    elif offline:
        t, source = offline_tailor(profile, jd, company, role), "offline"
    else:
        try:
            t, usage = llm_tailor(profile, jd, company, role, model=model, effort=effort, api_key=api_key)
            source = "llm"
        except TailorError as exc:
            notes.append(f"Claude tailoring unavailable ({exc}); used offline keyword tailoring")
            t, source = offline_tailor(profile, jd, company, role), "offline"
    doc, warnings = apply_tailoring(profile, t, contact)
    return TailorResult(tailoring=t, doc=doc, source=source, warnings=notes + warnings,
                        keyword_coverage=keyword_coverage(profile, jd, role), usage=usage)
