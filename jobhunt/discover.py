"""Find openings that fit the profile, using the same sources as the Fresher Jobs India pipeline.

Company ATS boards (Greenhouse, Lever, Ashby, SmartRecruiters; no key needed) plus Adzuna
when ADZUNA_APP_ID/KEY are set, with experienced-role queries instead of fresher ones.
Each post is filtered (software role, not senior, not a fresher/intern post, at most
`max_years` required) and scored by keyword coverage against the profile. Results go to
the Leads sheet, best first, and each description is saved so `apply --lead` works offline.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from jobhunt.keywords import DOMAIN, slug, years_required
from jobhunt.profile import Profile
from jobhunt.tailor import keyword_coverage, pick_track

log = logging.getLogger(__name__)

DISCOVER_QUERIES = [
    "node.js developer", "nestjs developer", "backend engineer node", "backend developer typescript",
    "full stack developer react node", "next.js developer", "react native developer", "mern stack developer",
    "ai engineer llm", "genai engineer", "software engineer typescript",
]

TRACK_LABELS = {"backend": "Backend", "fullstack": "Full Stack", "ai": "AI Engineer", "mobile": "Mobile"}

# Boards on top of pipeline/sources/boards.py that hire experienced engineers in India. Kept here,
# not in the pipeline, so the public fresher site is unchanged. Probed 26 Nov 2026 (India openings).
EXTRA_GREENHOUSE = {"mongodb": "MongoDB", "databricks": "Databricks", "inmobi": "InMobi", "twilio": "Twilio",
                    "truecaller": "Truecaller", "airbnb": "Airbnb", "purestorage": "Pure Storage", "fivetran": "Fivetran",
                    "toast": "Toast", "netskope": "Netskope", "agoda": "Agoda", "appian": "Appian", "zoominfo": "ZoomInfo",
                    "coinbase": "Coinbase", "sumologic": "Sumo Logic", "samsara": "Samsara", "newrelic": "New Relic"}
EXTRA_LEVER = {"paytm": "Paytm", "hevodata": "Hevo Data", "mindtickle": "Mindtickle", "fampay": "FamPay",
               "pocketfm": "Pocket FM"}
EXTRA_ASHBY: dict[str, str] = {"notion": "Notion"}
EXTRA_SMARTRECRUITERS = {"Freshworks": "Freshworks"}

# Approximate total headcount (public estimates, 2025-26) so `discover --min-employees` can keep
# established companies. Rough numbers: check LinkedIn before relying on one.
EMPLOYEES = {
    "Rubrik": 3000, "Stripe": 8000, "HackerRank": 500, "Glance": 1000, "Zscaler": 7000, "Okta": 6000, "Elastic": 3000,
    "GitLab": 2000, "Sigmoid": 1000, "Razorpay": 3000, "Groww": 2000, "Datadog": 6000, "Druva": 1000,
    "Observe.AI": 300, "Zeta": 1500, "Meesho": 2000, "CRED": 1000, "Sarvam AI": 200, "Composio": 50, "Plane": 100,
    "Atlan": 400, "Bosch": 400000, "Swiggy": 5000, "NielsenIQ": 30000, "Experian": 20000, "Sutherland": 38000,
    "ServiceNow": 20000, "MongoDB": 5000, "Databricks": 7000, "InMobi": 1500, "Twilio": 5500, "Truecaller": 450,
    "Airbnb": 7000, "Paytm": 10000, "Hevo Data": 250, "Mindtickle": 600, "FamPay": 150, "Pure Storage": 6000,
    "Fivetran": 1300, "Toast": 5500, "Netskope": 3000, "Agoda": 7000, "Appian": 2000, "ZoomInfo": 3500,
    "Coinbase": 3500, "Sumo Logic": 900, "Samsara": 3000, "New Relic": 2500, "Freshworks": 4500, "Pocket FM": 1000,
    "Notion": 1000,
}

_SOFTWARE_TITLE = re.compile(
    r"engineer|developer|\bsde\b|programmer|full[ -]?stack|back[ -]?end|front[ -]?end|mobile|software|\bai\b|\bllm\b|genai",
    re.I,
)
# Titles that are the wrong kind of role or the wrong stack for this profile.
_SKIP_TITLE = re.compile(
    r"\bintern|trainee|apprentice|fresher|graduate|\bqa\b|quality|\btest|sdet|support|sales|pre-?sales|data analyst"
    r"|\bgtm\b|people systems|customer (?:success|experience)|power (?:bi|automate|apps)|\bbi\b|asset management"
    r"|specialist|measurement|\bnpd\b|devsecops|ml ?ops|research|database migration|stibo|oracle|deep learning"
    r"|data scientist|devops|\bsre\b|site reliability|security|embedded|hardware|firmware|manager|director|\blead\b"
    r"|principal|(?<!technical )staff|head|architect|salesforce|\bsap\b|\bjava\b|\.net|\bphp\b|golang|\bgo\b|ruby|c\+\+|scala"
    r"|mainframe|network|verification|vlsi|analog|mechanical|electrical",
    re.I,
)


def score_post(profile: Profile, post, *, max_years: int = 4, title_filter: str | None = None,
               min_employees: int = 0) -> dict | None:
    """A Leads-sheet row for a post worth looking at, or None."""
    from pipeline.filters import looks_senior
    from pipeline.normalize import clean_title, html_to_text

    # "IN_Bosch Rexroth India_Test Automation" -> "Test Automation"; underscores hide word boundaries.
    title = re.sub(r"_+", " ", clean_title(post.title or "")).strip()
    if not _SOFTWARE_TITLE.search(title) or _SKIP_TITLE.search(title) or looks_senior(title):
        return None
    if title_filter and not re.search(title_filter, title, re.I):
        return None
    if min_employees and EMPLOYEES.get(post.company, 0) < min_employees:
        return None
    desc = html_to_text(post.description or "")
    years = years_required(desc)
    if years is not None and years > max_years:
        return None
    cov = keyword_coverage(profile, desc, title)
    core = [m for m in cov["matched"] if m not in DOMAIN]
    if len(core) < 3:  # one or two shared keywords is noise, not a fit
        return None
    asked = [t for t in cov["matched"] + cov["missing"] if t not in DOMAIN]
    core_pct = round(100 * len(core) / len(asked)) if asked else 0
    track = pick_track(profile, desc, title)
    pattern = profile.tracks[track].title_pattern
    title_bonus = 15 if pattern and re.search(pattern, title, re.I) else 0
    # Coverage alone overrates vague posts (1 keyword of 1 = 100%), so breadth of the match counts too.
    breadth = 30 * min(len(core), 6) / 6
    score = min(100, round(0.55 * core_pct + breadth + title_bonus))
    posted = post.posted_at.date() if post.posted_at else None
    return {
        "key": f"{post.source}:{post.source_id}",
        "company": post.company,
        "title": title,
        "location": post.location,
        "exp": f"{years}+ yrs" if years is not None else "",
        "employees": EMPLOYEES.get(post.company),
        "score": score,
        "track": TRACK_LABELS[track],
        "matched": ", ".join(cov["matched"]),
        "missing": ", ".join(cov["missing"]),
        "source": post.source,
        "url": post.url,
        "posted": posted,
        "found": date.today(),
        "description": desc,
    }


def fetch_posts(sources: list[str]) -> tuple[list, list[str]]:
    """Run the pipeline's fetchers. Returns (posts, errors)."""
    from pipeline.sources.aggregators import AdzunaSource
    from pipeline.sources.base import make_client
    from pipeline.sources.company_boards import AshbySource, GreenhouseSource, LeverSource, SmartRecruitersSource

    class DiscoverAdzuna(AdzunaSource):
        queries = DISCOVER_QUERIES

    class Greenhouse(GreenhouseSource):
        companies = {**GreenhouseSource.companies, **EXTRA_GREENHOUSE}

    class Lever(LeverSource):
        companies = {**LeverSource.companies, **EXTRA_LEVER}

    class Ashby(AshbySource):
        companies = {**AshbySource.companies, **EXTRA_ASHBY}

    class SmartRecruiters(SmartRecruitersSource):
        companies = {**SmartRecruitersSource.companies, **EXTRA_SMARTRECRUITERS}

    classes = {"greenhouse": Greenhouse, "lever": Lever, "ashby": Ashby,
               "smartrecruiters": SmartRecruiters, "adzuna": DiscoverAdzuna}
    posts, errors = [], []
    with make_client() as client:
        for name in sources:
            cls = classes.get(name)
            if cls is None:
                errors.append(f"unknown source {name}")
                continue
            src = cls(client)
            if not src.enabled:
                errors.append(f"{name}: skipped (no API key in .env)")
                continue
            try:
                got = src.fetch()
                posts.extend(got)
                log.info("%s: %d posts", name, len(got))
                for failed in getattr(src, "failed", []):
                    errors.append(f"{name}/{failed}")
            except Exception as exc:  # one dead source shouldn't stop the rest
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return posts, errors


def save_cache(posts: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[\n" + ",\n".join(p.model_dump_json() for p in posts) + "\n]", encoding="utf-8")


def load_cache(path: Path, max_age_hours: float = 12) -> list | None:
    """Posts from the last fetch, if it's recent enough to re-score without refetching."""
    import json
    import time

    from core.models import RawPost

    if not path.exists() or time.time() - path.stat().st_mtime > max_age_hours * 3600:
        return None
    return [RawPost.model_validate(x) for x in json.loads(path.read_text(encoding="utf-8"))]


def discover(profile: Profile, posts: list, leads_dir: Path, *, max_years: int = 4, min_score: int = 35,
             title_filter: str | None = None, min_employees: int = 0) -> list[dict]:
    """Score posts, keep the good ones (best first) and save their descriptions for `apply --lead`."""
    rows, seen = [], set()
    for post in posts:
        row = score_post(profile, post, max_years=max_years, title_filter=title_filter, min_employees=min_employees)
        if row is None or row["score"] < min_score or row["url"] in seen:
            continue
        seen.add(row["url"])
        rows.append(row)
    rows.sort(key=lambda r: r["score"], reverse=True)
    leads_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        (leads_dir / f"{slug(row['key'], 80)}.txt").write_text(row["description"], encoding="utf-8")
    return rows


def lead_description(leads_dir: Path, key: str) -> str:
    path = leads_dir / f"{slug(key, 80)}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""
