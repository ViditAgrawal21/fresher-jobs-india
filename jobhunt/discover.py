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

from jobhunt.keywords import slug, years_required
from jobhunt.profile import Profile
from jobhunt.tailor import keyword_coverage, pick_track

log = logging.getLogger(__name__)

DISCOVER_QUERIES = [
    "node.js developer", "nestjs developer", "backend engineer node", "backend developer typescript",
    "full stack developer react node", "next.js developer", "react native developer", "mern stack developer",
    "ai engineer llm", "genai engineer", "software engineer typescript",
]

TRACK_LABELS = {"backend": "Backend", "fullstack": "Full Stack", "ai": "AI Engineer", "mobile": "Mobile"}

_SOFTWARE_TITLE = re.compile(
    r"engineer|developer|\bsde\b|programmer|full[ -]?stack|back[ -]?end|front[ -]?end|mobile|software|\bai\b|\bllm\b|genai",
    re.I,
)
# Titles that are the wrong kind of role or the wrong stack for this profile.
_SKIP_TITLE = re.compile(
    r"\bintern|trainee|apprentice|fresher|graduate|\bqa\b|quality|\btest|sdet|support|sales|pre-?sales|data analyst"
    r"|data scientist|devops|\bsre\b|site reliability|security|embedded|hardware|firmware|manager|director|\blead\b"
    r"|principal|staff|head|architect|salesforce|\bsap\b|\bjava\b|\.net|\bphp\b|golang|\bgo\b|ruby|c\+\+|scala"
    r"|mainframe|network|verification|vlsi|analog|mechanical|electrical",
    re.I,
)


def score_post(profile: Profile, post, *, max_years: int = 4) -> dict | None:
    """A Leads-sheet row for a post worth looking at, or None."""
    from pipeline.normalize import html_to_text

    title = post.title or ""
    if not _SOFTWARE_TITLE.search(title) or _SKIP_TITLE.search(title):
        return None
    desc = html_to_text(post.description or "")
    years = years_required(desc)
    if years is not None and years > max_years:
        return None
    cov = keyword_coverage(profile, desc, title)
    if not cov["matched"]:
        return None
    track = pick_track(profile, desc, title)
    pattern = profile.tracks[track].title_pattern
    title_bonus = 15 if pattern and re.search(pattern, title, re.I) else 0
    # Coverage alone overrates vague posts (1 keyword of 1 = 100%), so breadth of the match counts too.
    breadth = 30 * min(len(cov["matched"]), 6) / 6
    score = min(100, round(0.55 * cov["coverage_pct"] + breadth + title_bonus))
    posted = post.posted_at.date() if post.posted_at else None
    return {
        "key": f"{post.source}:{post.source_id}",
        "company": post.company,
        "title": title,
        "location": post.location,
        "exp": f"{years}+ yrs" if years is not None else "",
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

    classes = {"greenhouse": GreenhouseSource, "lever": LeverSource, "ashby": AshbySource,
               "smartrecruiters": SmartRecruitersSource, "adzuna": DiscoverAdzuna}
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


def discover(profile: Profile, posts: list, leads_dir: Path, *, max_years: int = 4, min_score: int = 35) -> list[dict]:
    """Score posts, keep the good ones (best first) and save their descriptions for `apply --lead`."""
    rows, seen = [], set()
    for post in posts:
        row = score_post(profile, post, max_years=max_years)
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
