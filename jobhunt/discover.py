"""Find openings that fit the profile: company job boards, remote job boards and Adzuna.

Company ATS boards (Greenhouse, Lever, Ashby, SmartRecruiters; no key needed), the remote boards
in jobhunt/sources.py (Remotive, Jobicy, Remote OK, Himalayas), plus Adzuna when
ADZUNA_APP_ID/KEY are set, with experienced-role queries instead of fresher ones. Locations are
ranked by jobhunt/locations.py: remote in India, remote open to India, then hybrid and office
jobs in your preferred cities; everything else is left out.
Each post is filtered (software role, not senior, not a fresher/intern post, at most
`max_years` required, published in the last `max_age_days`, posted by the employer itself
rather than a recruiter, and free of scam signs) and scored by keyword coverage against
the profile. Results go to
the Leads sheet, best first, and each description is saved so `apply --lead` works offline.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from jobhunt.keywords import DOMAIN, slug, years_required
from jobhunt.locations import classify, is_english
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
EXTRA_GREENHOUSE = {"nice": "NICE", "tracelinkinc": "TraceLink", "commvault": "Commvault", "pubmatic": "PubMatic", "wikimedia": "Wikimedia Foundation", "slice": "slice", "vercel": "Vercel",
                    "cockroachlabs": "Cockroach Labs", "planetscale": "PlanetScale", "consensys": "Consensys",
                    "mongodb": "MongoDB", "databricks": "Databricks", "inmobi": "InMobi", "twilio": "Twilio",
                    "truecaller": "Truecaller", "airbnb": "Airbnb", "purestorage": "Pure Storage", "fivetran": "Fivetran",
                    "toast": "Toast", "netskope": "Netskope", "agoda": "Agoda", "appian": "Appian", "zoominfo": "ZoomInfo",
                    "coinbase": "Coinbase", "sumologic": "Sumo Logic", "samsara": "Samsara", "newrelic": "New Relic",
                    "zenoti": "Zenoti", "cloudsek": "CloudSEK", "harnessinc": "Harness", "contentstack": "Contentstack"}
EXTRA_LEVER = {"spotify": "Spotify", "binance": "Binance", "paytm": "Paytm", "hevodata": "Hevo Data", "mindtickle": "Mindtickle", "fampay": "FamPay",
               "pocketfm": "Pocket FM", "acceldata": "Acceldata"}
# Probed 27 Nov 2026: boards with India or remote software openings (remote ones are kept only if
# they're open to India; see locations.py).
EXTRA_ASHBY: dict[str, str] = {
    "notion": "Notion", "spotdraft": "SpotDraft", "cursor": "Cursor", "replit": "Replit", "fireworks": "Fireworks AI",
    "writer": "Writer", "vanta": "Vanta", "perplexity": "Perplexity", "workos": "WorkOS", "coder": "Coder",
    "n8n": "n8n", "resend": "Resend", "linear": "Linear", "langchain": "LangChain", "railway": "Railway",
    "airbyte": "Airbyte", "pinecone": "Pinecone", "posthog": "PostHog", "infisical": "Infisical", "zapier": "Zapier",
    "knock": "Knock", "weaviate": "Weaviate", "neon": "Neon", "doppler": "Doppler", "inngest": "Inngest",
}
EXTRA_SMARTRECRUITERS = {"Freshworks": "Freshworks", "Continental": "Continental", "aristanetworks": "Arista Networks"}

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
    "Notion": 1000, "Zenoti": 1200, "CloudSEK": 300, "Harness": 1200, "Contentstack": 500, "Acceldata": 300,
    "SpotDraft": 200, "Akamai": 10000,
    "PubMatic": 1000, "Wikimedia Foundation": 700, "slice": 1000, "Vercel": 600, "Cockroach Labs": 500,
    "PlanetScale": 100, "Consensys": 800, "Spotify": 9000, "Binance": 7000, "Cursor": 300, "Replit": 200,
    "Fireworks AI": 150, "Writer": 300, "Vanta": 800, "Perplexity": 250, "WorkOS": 150, "Coder": 150, "n8n": 200,
    "Resend": 30, "Linear": 100, "LangChain": 150, "Railway": 50, "Airbyte": 200, "Pinecone": 150, "PostHog": 100,
    "Infisical": 40, "Zapier": 800, "Knock": 50, "Weaviate": 150, "Neon": 150, "Doppler": 50, "Inngest": 40,
    "Continental": 190000, "NICE": 8500, "TraceLink": 800, "Commvault": 3000, "Arista Networks": 4000,
}


def size_band(employees: int | None) -> tuple[int, str]:
    """(priority, label): 500-1,000 employees is the main target, then other mid-size firms."""
    if employees is None:
        return 3, "size unknown"
    if 500 <= employees <= 1000:
        return 0, "500-1,000"
    if 300 <= employees <= 2000:
        return 1, "mid-size"
    if employees > 2000:
        return 2, "large"
    return 3, "small"

# Authenticity. Company job boards are the employer's own system; aggregator listings (Adzuna)
# also carry recruiters, staffing firms and the odd scam, so those are dropped.
_RECRUITER = re.compile(
    r"recruit|staffing|placement|manpower|consultan|outsourc|hr services|talent (?:solutions|acquisition|hub)"
    r"|hiring (?:partner|agency)|job ?(?:portal|board)|careers? (?:hub|point)|^(?:confidential|unknown|n/?a|private)$",
    re.I,
)
_SCAM = re.compile(
    r"registration fee|security deposit|(?:pay|deposit) (?:a |the |an )?(?:fee|amount)|processing fee|training fee"
    r"|refundable|(?:whatsapp|telegram) (?:your )?(?:cv|resume)|earn (?:rs\.?|₹|\$|inr) ?\d[\d,]* (?:per|a|/) ?(?:day|week)"
    r"|no interview",
    re.I,
)
_EMPLOYER_SOURCES = {"greenhouse", "lever", "ashby", "smartrecruiters"}


def looks_authentic(post, desc: str) -> bool:
    """Posted by the employer itself, with no recruiter or scam signs."""
    company = (post.company or "").strip()
    if not company or _RECRUITER.search(company):
        return False
    return not _SCAM.search(desc)


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
               min_employees: int = 0, max_employees: int = 0, max_age_days: int = 0,
               today: date | None = None, cities: str | None = None, remote_abroad: bool = True,
               other_cities: bool = False) -> dict | None:
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
    if max_employees and EMPLOYEES.get(post.company, 10**9) > max_employees:
        return None
    posted = post.posted_at.date() if post.posted_at else None
    if max_age_days:
        if posted and ((today or date.today()) - posted).days > max_age_days:
            return None  # too old: probably filled, or an evergreen posting
        if posted is None and post.source not in _EMPLOYER_SOURCES:
            return None  # an aggregator listing with no date can't be shown to be current
    desc = html_to_text(post.description or "")
    if not looks_authentic(post, desc) or not is_english(desc):
        return None
    # cities=None keeps every location (the Fresher Jobs India boards are India-only already).
    where = classify(post.location or "", desc, cities, remote_abroad, other_cities) if cities is not None else None
    if where is not None and not where.ok:
        return None
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
    return {
        "key": f"{post.source}:{post.source_id}",
        "company": post.company,
        "title": title,
        "location": post.location,
        "exp": f"{years}+ yrs" if years is not None else "",
        "employees": EMPLOYEES.get(post.company),
        "where": where.label if where else "", "where_rank": where.rank if where else 3,
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


ALL_SOURCES = "greenhouse,lever,ashby,smartrecruiters,remotive,jobicy,remoteok,himalayas,weworkremotely,workingnomads,adzuna"


def fetch_posts(sources: list[str], cache_dir: Path | None = None, progress=None) -> tuple[list, list[str]]:
    """Run the fetchers. Returns (posts, errors). `progress(name, i, n)` is called before each source."""
    from jobhunt import sources as js
    from pipeline.sources.aggregators import AdzunaSource
    from pipeline.sources.base import make_client

    class DiscoverAdzuna(AdzunaSource):
        queries = DISCOVER_QUERIES

    class Greenhouse(js.Greenhouse):
        companies = {**js.Greenhouse.companies, **EXTRA_GREENHOUSE}

    class Lever(js.Lever):
        companies = {**js.Lever.companies, **EXTRA_LEVER}

    class Ashby(js.Ashby):
        companies = {**js.Ashby.companies, **EXTRA_ASHBY}

    class SmartRecruiters(js.SmartRecruiters):
        companies = {**js.SmartRecruiters.companies, **EXTRA_SMARTRECRUITERS}

    classes = {"greenhouse": Greenhouse, "lever": Lever, "ashby": Ashby, "smartrecruiters": SmartRecruiters,
               "remotive": js.Remotive, "jobicy": js.Jobicy, "remoteok": js.RemoteOK, "himalayas": js.Himalayas,
               "weworkremotely": js.WeWorkRemotely, "workingnomads": js.WorkingNomads,
               "adzuna": DiscoverAdzuna}
    posts, errors = [], []
    with make_client() as client:
        for i, name in enumerate(sources):
            if progress:
                progress(name, i, len(sources))
            cls = classes.get(name)
            if cls is None:
                errors.append(f"unknown source {name}")
                continue
            src = cls(client, cache_dir) if issubclass(cls, js._RemoteBoard) else cls(client)
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
             title_filter: str | None = None, min_employees: int = 0, max_employees: int = 0,
             max_age_days: int = 0, cities: str | None = None, remote_abroad: bool = True,
             other_cities: bool = False) -> list[dict]:
    """Score posts, keep the good ones (best first) and save their descriptions for `apply --lead`."""
    rows, seen = [], set()
    for post in posts:
        row = score_post(profile, post, max_years=max_years, title_filter=title_filter, min_employees=min_employees,
                         max_employees=max_employees, max_age_days=max_age_days, cities=cities,
                         remote_abroad=remote_abroad, other_cities=other_cities)
        if row is None or row["score"] < min_score:
            continue
        # The same job is often on the company's board and on a remote board: keep the first.
        same = (row["company"].lower().strip(), row["title"].lower().strip())
        if row["url"] in seen or same in seen:
            continue
        seen.update({row["url"], same})
        rows.append(row)
    # Best JD match first; then your preferred kind of location; then the newest posting.
    rows.sort(key=lambda r: (-r["score"], r["where_rank"], -(r["posted"] or date.min).toordinal()))
    leads_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        (leads_dir / f"{slug(row['key'], 80)}.txt").write_text(row["description"], encoding="utf-8")
    return rows


def lead_description(leads_dir: Path, key: str) -> str:
    path = leads_dir / f"{slug(key, 80)}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


SOURCE_LABELS = {"greenhouse": "company boards on Greenhouse", "lever": "company boards on Lever",
                 "ashby": "company boards on Ashby", "smartrecruiters": "company boards on SmartRecruiters",
                 "remotive": "Remotive (remote jobs)", "jobicy": "Jobicy (remote jobs)", "remoteok": "Remote OK",
                 "himalayas": "Himalayas (remote jobs)", "weworkremotely": "We Work Remotely",
                 "workingnomads": "Working Nomads", "adzuna": "Adzuna (India job ads)"}


def run_discover(settings, *, sources: str = ALL_SOURCES, cached: bool = False, max_age_days: int | None = None,
                 max_years: int = 4, min_score: int = 35, limit: int = 200, title_filter: str | None = None,
                 min_employees: int = 0, max_employees: int = 0, lock=None, progress=None, wider: bool = False) -> dict:
    """Fetch (or reuse the last fetch), score, and add new matches to the Leads sheet.

    `wider` also takes jobs up to STALE_JOB_DAYS old and in other Indian cities (ranked last)."""
    from contextlib import nullcontext

    from jobhunt.profile import load_profile
    from jobhunt.tracker import Tracker

    profile = load_profile(settings.profile_path)
    cache = settings.data_dir / "posts_cache.json"
    posts = load_cache(cache) if cached else None
    errors: list[str] = []
    if posts is None:
        posts, errors = fetch_posts([x.strip() for x in sources.split(",") if x.strip()],
                                    cache_dir=settings.data_dir / "board_cache", progress=progress)
        save_cache(posts, cache)
    max_age = (settings.stale_job_days if wider else settings.fresh_job_days) if max_age_days is None else max_age_days
    rows = discover(profile, posts, settings.data_dir / "leads", max_years=max_years, min_score=min_score,
                    title_filter=title_filter, min_employees=min_employees, max_employees=max_employees,
                    max_age_days=max_age, cities=settings.preferred_cities, remote_abroad=settings.remote_abroad,
                    other_cities=wider)
    with lock or nullcontext():
        t = Tracker(settings.tracker_path)
        added = t.add_leads(rows[:limit])
        t.save()
    return {"posts": len(posts), "rows": rows, "added": added, "errors": errors, "max_age": max_age}
