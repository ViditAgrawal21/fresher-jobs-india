from __future__ import annotations

import hashlib
import re
import warnings
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from core.models import RawPost
from core.taxonomy import cities_in, strip_cities

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

MAX_DESCRIPTION_CHARS = 1500  # enough for role/city/experience; keeps LLM input small

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
# Only true tracking params. NOT gh_jid: companies that host Greenhouse jobs on their own
# careers page (Elastic, Stripe) put the job id there -- stripping it made every job one URL.
_TRACKING_PARAMS = re.compile(r"^(utm_|gh_src|lever-|source$|ref$|src$|trk|fbclid|gclid)", re.I)

_COMPANY_NOISE = {
    "private", "pvt", "limited", "ltd", "inc", "llp", "llc", "corp", "corporation", "co",
    "india", "technologies", "technology", "tech", "solutions", "services", "software",
    "group", "global", "the", "and",
}
# words that change between copies of the same post but not its meaning
_TITLE_NOISE = {"hiring", "urgent", "urgently", "opening", "openings", "job", "jobs", "position", "role", "wfh",
                "remote", "hybrid", "onsite", "immediate", "joiner", "joiners"}


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    if "<" in raw:
        raw = BeautifulSoup(raw, "html.parser").get_text(" ")
    return _WS_RE.sub(" ", raw).strip()


def trim(text: str, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind(" ")] if " " in cut else cut


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), ""))


def company_norm(name: str) -> str:
    words = _NON_ALNUM_RE.sub(" ", (name or "").lower()).split()
    kept = [w for w in words if w not in _COMPANY_NOISE]
    return " ".join(kept or words)


def title_norm(title: str) -> str:
    # drop city names that some boards append ("Data Analyst - Bengaluru")
    text = strip_cities((title or "").lower())
    words =[w for w in _NON_ALNUM_RE.sub(" ", text).split() if w not in _TITLE_NOISE]
    return " ".join(words)


def city_key(location: str) -> str:
    cities = sorted(cities_in(location))
    return ",".join(cities) if cities else "unknown"


def to_row(post: RawPost) -> dict:
    """RawPost -> dict matching the `jobs` table columns used by core.db.upsert_jobs."""
    title = html_to_text(post.title)
    company = html_to_text(post.company) or "Unknown"
    location = html_to_text(post.location)
    description = None if post.description is None else trim(html_to_text(post.description))
    comp_norm = company_norm(company)

    content_hash = None
    if description is not None:
        content_hash = hashlib.sha1(f"{title}|{company}|{location}|{description}".encode()).hexdigest()

    return {
        "source": post.source,
        "source_id": post.source_id,
        "url": post.url,
        "canonical_url": canonical_url(post.url),
        "title": title,
        "company": company,
        "company_norm": comp_norm,
        "location_raw": location,
        "description": description,
        "posted_at": post.posted_at,
        "content_hash": content_hash,
        "dedup_key": f"{comp_norm}|{title_norm(title)}|{city_key(location)}",
    }
