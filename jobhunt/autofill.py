"""Fill job application forms in a real browser window, and stop before Submit.

For each application it opens the posting's apply form, types your standard answers
(jobhunt/data/answers.yaml) into the fields it recognises by their labels, attaches that
job's tailored resume, and outlines what it filled (green) and what still needs you
(orange). You read every field, answer the rest, solve any CAPTCHA and click Submit
yourself. When a "thank you for applying" page shows up, the application is marked
Submitted in the tracker.

It never clicks Submit and never touches LinkedIn or Naukri apply flows: those sites
forbid automated applications, and a wrong answer sent in your name costs more than
the minute it takes to check.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml

from jobhunt.profile import load_profile
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker

log = logging.getLogger(__name__)

ANSWERS_TEMPLATE = """\
# Answers that autofill types into application forms. Check them once, and fill the blanks
# you want answered for you. Blank answers are left for you in each form. This file is
# git-ignored (jobhunt/data/) because it holds salary details. Email, phone and links come
# from .env and profile.yaml.
first_name: {first}
last_name: {last}
preferred_name: {first}
country: India
phone_country_code: "+91"
city: ""                        # e.g. Pune
location: ""                    # e.g. Pune, Maharashtra, India
location_preference: ""         # in order, e.g. Bengaluru, Pune, Hyderabad or Remote
current_company: {company}
current_title: {title}
total_experience_years: ""      # e.g. "2.5"
relevant_experience_years: ""
notice_period_days: ""          # e.g. "30"
current_ctc_lpa: ""             # in lakhs per year, e.g. "8"
expected_ctc_lpa: ""            # check before you apply
willing_to_relocate: "Yes"
authorized_to_work_in_india: "Yes"
needs_sponsorship: "No"
heard_of_company_before: "Yes"
school: {school}
degree: {degree}
discipline: {discipline}
education_start_month: ""
education_start_year: ""        # e.g. "2020"
education_end_month: {end_month}
education_end_year: {end_year}
"""

# (label pattern, answer key). First match wins, so specific patterns come first.
# A key of None means "recognised, but leave it for you".
RULES: list[tuple[str, str | None]] = [
    (r"preferred (first )?name", "preferred_name"),
    (r"first\s*name|given name", "first_name"),
    (r"last\s*name|surname|family name", "last_name"),
    (r"^(full\s*)?name\b|your name|candidate name|legal name", "full_name"),
    (r"e-?mail", "email"),
    (r"country code", "phone_country_code"),
    (r"phone|mobile|contact number", "phone"),
    (r"linked\s?in", "linkedin"),
    (r"github", "github"),
    (r"naukri", None),
    (r"portfolio|personal (site|website)|^website|other url", "portfolio"),
    (r"current (company|employer|organi[sz]ation)|^company( name)?\b|^organi[sz]ation", "current_company"),
    (r"current (designation|title|role|position)|^job title|^designation", "current_title"),
    (r"notice period", "notice_period_days"),
    (r"current (ctc|salary|compensation|annual)", "current_ctc_lpa"),
    (r"expected (ctc|salary|compensation)|salary expectation", "expected_ctc_lpa"),
    (r"relevant (professional |work )?experience", "relevant_experience_years"),
    (r"(total|overall) (professional |work )?experience|years of (professional |work )?experience"
     r"|^(professional |work )?experience\s*\(?(in )?years", "total_experience_years"),
    (r"location preference|preferred (work )?location", "location_preference"),
    (r"relocat", "willing_to_relocate"),
    (r"(authori[sz]ed|eligible|legally).{0,30}work|work authori[sz]ation|right to work", "authorized_to_work_in_india"),
    (r"sponsor", "needs_sponsorship"),
    (r"how did you (hear|come to know|find|learn)|where did you (hear|find)|^source\b", "how_did_you_hear"),
    (r"(know|heard) (about|of) .{0,40} before", "heard_of_company_before"),
    (r"\bcity\b", "city"),
    (r"^country\b", "country"),
    (r"location|where are you (based|located)", "location"),
    (r"school|university|college|institution", "school"),
    (r"degree", "degree"),
    (r"discipline|field of study|major|branch", "discipline"),
    (r"start (date )?month", "education_start_month"),
    (r"start (date )?year", "education_start_year"),
    (r"(end|graduation) (date )?month", "education_end_month"),
    (r"(end|graduation) (date )?year", "education_end_year"),
    (r"cover letter|additional information|anything else|message to (the )?hiring", "cover_note"),
]
_RULES = [(re.compile(p, re.I), k) for p, k in RULES]

CONFIRMATION = re.compile(
    r"thank(s| you) for (applying|your application|your interest)|application (has been |was )?(submitted|received)"
    r"|we('ve| have) received your application|your application is (complete|in)",
    re.I,
)

# Collects every form field with the text a person would read as its label.
_FIELDS_JS = """() => {
  const all = [...document.querySelectorAll('input, textarea, select')];
  const labelOf = e => {
    let t = '';
    if (e.labels && e.labels.length) t = [...e.labels].map(l => l.innerText).join(' ');
    if (!t && e.getAttribute('aria-labelledby'))
      t = e.getAttribute('aria-labelledby').split(/\\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
    if (!t) t = e.getAttribute('aria-label') || '';
    if (!t) { const box = e.closest('fieldset, li, .field, .form-group, [class*="field"], [class*="question"]');
              const l = box && box.querySelector('label, legend'); if (l) t = l.innerText; }
    if (!t) t = e.placeholder || e.name || '';
    return t.replace(/\\s+/g, ' ').replace(/[*✱]/g, '').trim();
  };
  const stamp = Date.now().toString(36);
  return all.map((e, n) => {
    const i = e.dataset.jhIdx || (e.dataset.jhIdx = stamp + '-' + n);
    const isCombo = e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete') === 'list';
    // A dropdown widget shows its choice in a sibling element, not in the input's value.
    const box = isCombo && e.closest('[class*="control"], [class*="value-container"], [class*="ValueContainer"]');
    const shown = box && box.querySelector('[class*="single-value"], [class*="multi-value"], [class*="singleValue"]');
    return {
    i, tag: e.tagName.toLowerCase(), type: (e.type || '').toLowerCase(), name: e.name || '', id: e.id || '',
    label: labelOf(e).slice(0, 160),
    // checkVisibility also catches visibility:hidden, e.g. the search box of a closed phone-country picker.
    visible: e.type === 'file' || (e.checkVisibility ? e.checkVisibility({ visibilityProperty: true })
                                                     : !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)),
    empty: e.type === 'file' ? !(e.files && e.files.length) : !((e.value || '').trim() || (shown && shown.innerText.trim())),
    combobox: e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete') === 'list'
              || /select__input|autocomplete/.test(e.className || ''),
    required: e.required || e.getAttribute('aria-required') === 'true',
    touched: e.dataset.jobhunt === '1',
  }; });
}"""

_MARK_JS = """([filled, todo]) => {
  const byIdx = i => document.querySelector('[data-jh-idx="' + i + '"]');
  for (const i of filled) { const e = byIdx(i); if (e) { e.dataset.jobhunt = '1'; e.style.outline = '2px solid #0aac23'; } }
  for (const i of todo) { const e = byIdx(i); if (e && !e.dataset.jobhunt) e.style.outline = '2px solid #f18a59'; }
  if (!document.getElementById('jobhunt-banner')) {
    const b = document.createElement('div');
    b.id = 'jobhunt-banner';
    b.textContent = 'jobhunt filled the green fields. Check every field, answer the orange ones, then submit yourself.';
    b.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;background:#191919;color:#fff;' +
                      'font:14px system-ui;padding:10px 16px;text-align:center';
    document.body.appendChild(b);
  }
}"""


def answers_path(settings: JobhuntSettings) -> Path:
    return settings.data_dir / "answers.yaml"


def _answers_from_profile(profile) -> str:
    """A first answers.yaml with what profile.yaml already says; the rest is left blank."""
    from datetime import datetime

    q = json.dumps  # a JSON string is a valid YAML scalar, whatever punctuation it holds
    first, _, last = profile.name.partition(" ")
    job = profile.experience[0] if profile.experience else None
    edu = profile.education[0] if profile.education else None
    degree, _, discipline = (edu.degree if edu else "").partition(" - ")
    try:
        end = datetime.strptime(edu.date.strip(), "%b %Y") if edu else None
    except ValueError:
        end = None
    return ANSWERS_TEMPLATE.format(
        first=q(first), last=q(last), company=q(job.company if job else ""), title=q(job.title if job else ""),
        school=q(edu.school if edu else ""),
        degree=q("Bachelor's Degree" if degree.startswith(("B.", "B ", "Bachelor")) else degree.strip()),
        discipline=q(discipline.strip()), end_month=q(f"{end:%B}" if end else ""), end_year=q(f"{end:%Y}" if end else ""))


def load_answers(settings: JobhuntSettings) -> dict:
    """Your saved answers, plus email, phone and links. Creates the file on first use."""
    profile = load_profile(settings.profile_path)
    path = answers_path(settings)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_answers_from_profile(profile), encoding="utf-8")
    answers = {k: ("" if v is None else str(v)) for k, v in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items()}
    links = {l.label.lower(): l.url for l in profile.links}
    answers.setdefault("full_name", profile.name)
    answers.setdefault("email", settings.candidate_email)
    answers.setdefault("phone", settings.candidate_phone)
    answers.setdefault("linkedin", links.get("linkedin", ""))
    answers.setdefault("github", links.get("github", ""))
    answers.setdefault("portfolio", links.get("portfolio", ""))
    return answers


def answer_for(label: str, answers: dict) -> tuple[str | None, str]:
    """(answer key, value) for a field label; key None when no rule matches."""
    for rx, key in _RULES:
        if rx.search(label):
            return key, (answers.get(key, "") if key else "")
    return None, ""


def apply_url(url: str, lead_keys: dict[str, str] | None = None) -> str:
    """The page with the application form for a posting URL."""
    u = urlparse(url)
    host, path = u.netloc.lower(), u.path.rstrip("/")
    # Greenhouse: the embed page is the bare form. The normal job page redirects to the company's
    # own careers site for some companies, where the form hides behind an Apply button or an iframe.
    if host in ("job-boards.greenhouse.io", "boards.greenhouse.io"):
        m = re.match(r"/([\w-]+)/jobs/(\d+)$", path)
        return f"https://job-boards.greenhouse.io/embed/job_app?for={m[1]}&token={m[2]}" if m else url
    if "gh_jid" in u.query and lead_keys:
        key = lead_keys.get(url, "")
        if key.startswith("greenhouse:"):
            _, slug, job_id = key.split(":", 2)
            return f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={job_id}"
        return url
    if host == "jobs.lever.co" and not path.endswith("/apply"):
        return f"https://jobs.lever.co{path}/apply"
    if host == "jobs.ashbyhq.com" and not path.endswith("/application"):
        return f"https://jobs.ashbyhq.com{path}/application"
    return url


def _choose_option(select, value: str) -> bool:
    options = select.evaluate("s => [...s.options].map(o => o.text.trim())")
    want = value.strip().lower()
    for opt in options:
        if opt.lower() == want or (want in ("yes", "no") and opt.lower().startswith(want)):
            select.select_option(label=opt)
            return True
    for opt in options:
        if want and want in opt.lower():
            select.select_option(label=opt)
            return True
    return False


_NUMERIC_KEYS = {"total_experience_years", "relevant_experience_years", "notice_period_days",
                 "current_ctc_lpa", "expected_ctc_lpa"}
_CITY_ALIASES = {"bengaluru": "bangalore", "bangalore": "bengaluru", "gurugram": "gurgaon", "gurgaon": "gurugram"}
_HEARD = {"company careers page": ("career", "website", "company site", "job board"), "linkedin": ("linkedin",)}


def _pick(options: list[str], value: str, key: str | None) -> int | None:
    """Index of the dropdown option that best matches an answer, or None if none fits well."""
    from rapidfuzz import fuzz

    opts = [o.strip() for o in options]
    low = [o.lower() for o in opts]
    v = value.strip().lower()
    if not v or not opts:
        return None
    if key in _NUMERIC_KEYS and (nums := re.findall(r"\d+(?:\.\d+)?", v)):
        x = float(nums[0])
        best = None
        for i, o in enumerate(low):
            if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)", o):
                lo, hi = float(m.group(1)), float(m.group(2))
                if lo <= x <= hi and (best is None or hi - lo < best[1]):
                    best = (i, hi - lo, lo)
            elif (m := re.search(r"(\d+(?:\.\d+)?)\s*\+", o)) and x >= float(m.group(1)):
                lo = float(m.group(1))
                if best is None or (best[1] >= 99 and lo > best[2]):  # the highest "N+" you still meet
                    best = (i, 99, lo)
        if best:
            return best[0]
    for i, o in enumerate(low):
        if o == v or (v in ("yes", "no") and o.startswith(v)):
            return i
    if key == "how_did_you_hear":
        for word in _HEARD.get(v, (v,)):
            for i, o in enumerate(low):
                if word in o:
                    return i
    if key in ("location_preference", "location", "city"):
        cities = [c.strip() for c in re.split(r",|/|\bor\b|\band\b", v) if c.strip()]
        for c in cities:  # your order of preference, each city with its other spelling
            for name in (c, _CITY_ALIASES.get(c, c)):
                for i, o in enumerate(low):
                    if name in o:
                        return i
    scored = [(fuzz.token_set_ratio(v, o), i) for i, o in enumerate(low)]
    score, i = max(scored)
    return i if score >= 85 else None


def _search_term(value: str, key: str | None) -> str:
    """What to type into a searchable dropdown: a short prefix, since long text filters everything out."""
    if key in _NUMERIC_KEYS or key == "how_did_you_hear":
        return ""
    first = re.split(r",|\bor\b", value)[0].strip()
    return " ".join(first.split()[:2])


# Per-action timeout. Playwright's default (30 s) turns one stuck widget into minutes per pass.
ACTION_MS = 4000


def _close_menu(loc) -> None:
    """Close a dropdown's menu. An open menu covers the fields below it and swallows their clicks."""
    for step in (lambda: loc.press("Escape", timeout=ACTION_MS), lambda: loc.evaluate("e => e.blur()")):
        try:
            step()
        except Exception:
            pass


def _fill_combobox(page, loc, value: str, key: str | None) -> bool:
    opts = page.locator("[role='option']")
    loc.click(timeout=ACTION_MS)
    page.wait_for_timeout(500)
    idx = _pick(opts.all_inner_texts()[:400], value, key)
    term = _search_term(value, key)
    if idx is None and term:
        loc.fill(term, timeout=ACTION_MS)
        page.wait_for_timeout(1200)
        idx = _pick(opts.all_inner_texts()[:400], value, key)
    if idx is None:
        try:
            loc.fill("", timeout=ACTION_MS)  # clear the search text before closing, or it reopens the menu
        except Exception:
            pass
        _close_menu(loc)
        return False
    opts.nth(idx).click(timeout=ACTION_MS)
    return True


def fill_page(page, answers: dict, resume: Path | None, done: set | None = None) -> dict:
    """Fill recognised empty fields on the current page. Returns what it did.

    `done` remembers the questions already answered on this tab, so a field a form
    re-renders (dropdowns do) isn't typed into again on the next pass.
    """
    done = done if done is not None else set()
    fields = page.evaluate(_FIELDS_JS)
    filled, todo, skipped = [], [], []
    for f in fields:
        if f["touched"] or not f["visible"] or f["type"] in ("hidden", "submit", "button", "checkbox", "radio", "search"):
            continue
        label = f"{f['label']} {f['name']} {f['id']}".strip() if f["type"] == "file" else f["label"]
        loc = page.locator(f'[data-jh-idx="{f["i"]}"]')
        memo = (f["type"], label.lower())
        if memo in done and f["type"] != "file":
            continue
        try:
            if f["type"] == "file":
                if resume and f["empty"] and re.search(r"resume|cv", label, re.I) and not re.search(r"cover", label, re.I):
                    loc.set_input_files(str(resume))
                    filled.append(f["i"])
                    done.add(memo)
                continue
            if not f["empty"]:
                continue
            key, value = answer_for(label, answers)
            if not value:
                if f["required"] and label:
                    todo.append(f["i"])
                if key or f["required"]:
                    skipped.append(label)
                continue
            if f["tag"] == "select":
                if _choose_option(loc, value):
                    filled.append(f["i"]); done.add(memo)
                else:
                    todo.append(f["i"])
            elif f["combobox"]:
                (filled if _fill_combobox(page, loc, value, key) else todo).append(f["i"])
                done.add(memo)  # tried once either way; never retried on later passes
            else:
                loc.fill(value)
                filled.append(f["i"]); done.add(memo)
        except Exception as exc:  # one awkward widget shouldn't stop the rest
            log.debug("could not fill %r: %s", label, str(exc).splitlines()[0] if str(exc) else exc)
            todo.append(f["i"])
            if f["combobox"]:
                _close_menu(loc)
    if filled or todo:
        page.evaluate(_MARK_JS, [filled, todo])
    return {"filled": len(filled), "todo": len(todo), "skipped": skipped}


def is_confirmation(page) -> bool:
    try:
        return bool(CONFIRMATION.search(page.inner_text("body", timeout=2000)[:5000]))
    except Exception:
        return False


@dataclass
class Session:
    """One autofill run; the dashboard server polls `status()`."""

    ids: list[str]
    state: str = "starting"          # starting | running | done | error
    message: str = ""
    submitted: list[str] = field(default_factory=list)
    filled: dict = field(default_factory=dict)

    def status(self) -> dict:
        return {"ids": self.ids, "state": self.state, "message": self.message, "submitted": self.submitted,
                "filled": self.filled}


def autofillable(url) -> bool:
    """A company or ATS apply page. LinkedIn and Naukri forbid automated applications: apply there yourself."""
    url = str(url or "")
    host = urlparse(url).netloc.lower()
    return url.startswith("https://") and not any(h in host for h in ("linkedin.com", "naukri.com"))


def plan(settings: JobhuntSettings, ids: list[str]) -> list[dict]:
    """The applications to open: id, company, apply URL, resume path and cover note."""
    tracker = Tracker(settings.tracker_path)
    lead_keys = {}
    for r in range(2, tracker.leads.max_row + 1):
        try:
            lead = tracker.lead(r)
        except KeyError:
            continue
        lead_keys[str(lead["url"])] = str(lead["key"])
    out = []
    for app_id in ids:
        app = tracker.find(app_id)
        if app is None or not autofillable(app["url"]):
            continue
        resume = settings.data_dir / str(app["resume"]) if app["resume"] else None
        note = ""
        if resume and (resume.parent / "tailoring.json").exists():
            note = json.loads((resume.parent / "tailoring.json").read_text(encoding="utf-8")).get("recruiter_note", "")
        out.append({"id": app["id"], "company": app["company"], "url": apply_url(str(app["url"]), lead_keys),
                    "resume": resume if resume and resume.exists() else None, "note": note,
                    "source": app["source"] or ""})
    return out


def ready_ids(settings: JobhuntSettings, limit: int = 5) -> list[str]:
    """Tailored-but-not-sent applications, best first (the order the dashboard board shows)."""
    from jobhunt.dashboard import build_data

    data = build_data(settings)
    # Not advised against, not closed, not older than STALE_JOB_DAYS: fresh jobs first.
    ready = [a for a in data["apps"] if a["status"] in ("Lead", "Tailored") and a["flag"] < 9 and a["fresh"] != "old"
             and a["posting"] != "Closed" and autofillable(a["url"])]
    ready.sort(key=lambda a: (a["rank"], -(a["fit"] or 0)))
    return [a["id"] for a in ready[:limit]]


def run(settings: JobhuntSettings, ids: list[str], session: Session | None = None, *, headless: bool = False,
        minutes: int = 45, on_submitted=None, lock: threading.Lock | None = None) -> Session:
    """Open each application in its own tab, keep filling as forms change, and record submissions."""
    from playwright.sync_api import sync_playwright

    session = session or Session(ids=ids)
    jobs = plan(settings, ids)
    if not jobs:
        session.state, session.message = "done", "Nothing to open: those applications have no company apply link."
        return session
    answers = load_answers(settings)
    lock = lock or threading.Lock()
    profile_dir = settings.data_dir / "browser-profile"
    try:
        with sync_playwright() as p:
            kwargs = {"executable_path": settings.chromium_path} if settings.chromium_path else {}
            ctx = p.chromium.launch_persistent_context(str(profile_dir), headless=headless, viewport=None, **kwargs)
            tabs = []
            for job in jobs:
                page = ctx.new_page()
                page.set_default_timeout(ACTION_MS)
                try:
                    page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
                except Exception as exc:
                    log.warning("couldn't open %s: %s", job["url"], exc)
                ans = dict(answers, cover_note=job["note"],
                           how_did_you_hear=answers.get("how_did_you_hear") or
                           ("LinkedIn" if job["source"] == "LinkedIn" else "Company careers page"))
                tabs.append((page, job, ans, set()))
            for blank in [pg for pg in ctx.pages if pg.url in ("about:blank", "")]:
                if len(ctx.pages) > 1:
                    blank.close()
            session.state = "running"
            session.message = (f"{len(tabs)} application tab(s) open. Check each form, answer the orange fields, "
                               "solve any CAPTCHA and click Submit yourself.")
            deadline = time.time() + minutes * 60
            while time.time() < deadline:
                open_tabs = [t for t in tabs if not t[0].is_closed()]
                if not open_tabs:
                    break
                for pg, job, ans, done in open_tabs:
                    if job["id"] in session.submitted:
                        continue
                    try:
                        res = fill_page(pg, ans, job["resume"], done)
                        session.filled[job["id"]] = session.filled.get(job["id"], 0) + res["filled"]
                        if is_confirmation(pg):
                            session.submitted.append(job["id"])
                            if on_submitted:
                                with lock:
                                    on_submitted(job["id"])
                    except Exception as exc:  # the page navigated or closed mid-fill
                        log.debug("tab %s: %s", job["id"], exc)
                open_tabs[0][0].wait_for_timeout(2000)
            ctx.close()
        session.state = "done"
        session.message = (f"Done. Submitted: {', '.join(session.submitted) or 'none detected'}. "
                           "If you submitted one that didn't show a thank-you page, mark it submitted yourself.")
    except Exception as exc:
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        if "Executable doesn't exist" in str(exc):
            first = "Chromium isn't installed for Playwright. Run: python -m playwright install chromium"
        elif "XServer" in str(exc) or "headed browser" in str(exc):
            first = "This computer has no screen to show the browser window on."
        session.state, session.message = "error", f"Autofill stopped: {first}"
        log.exception("autofill failed")
    return session


def mark_submitted(settings: JobhuntSettings, app_id: str) -> None:
    tracker = Tracker(settings.tracker_path)
    app = tracker.find(app_id)
    if app is None:
        return
    values = {"status": "Applied", "applied_with": settings.candidate_email or None}
    if not app["date_applied"]:
        values["date_applied"] = date.today()
    tracker.update(app_id, **values)
    tracker.append_note(app_id, f"[{date.today():%d %b}] submitted (confirmation page seen during autofill)")
    tracker.save()
