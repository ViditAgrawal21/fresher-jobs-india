"""Job aggregators with official APIs. Both need a free key; without one the source is skipped."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from core.config import get_settings
from core.models import RawPost
from pipeline.filters import looks_senior
from pipeline.sources.base import Source

log = logging.getLogger(__name__)

# What a fresher would type. Each query is one API call per page.
FRESHER_QUERIES = [
    "fresher",
    "freshers",
    "intern",
    "internship",
    "graduate trainee",
    "trainee",
    "entry level",
    "junior",
    "apprentice",
    "0-1 years",
]

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


class AdzunaSource(Source):
    """https://developer.adzuna.com — India is country code `in`.
    Terms: every listing we show must credit Adzuna and link back (we use their redirect_url)."""

    name = "adzuna"
    delay = 1.0
    queries: list[str] = FRESHER_QUERIES  # jobhunt.discover swaps in experienced-role queries

    @property
    def enabled(self) -> bool:
        s = get_settings()
        return bool(s.adzuna_app_id and s.adzuna_app_key)

    def fetch(self) -> list[RawPost]:
        s = get_settings()
        seen: dict[str, RawPost] = {}
        for query in self.queries:
            for page in range(1, s.adzuna_pages_per_query + 1):
                data = self.get_json(
                    f"https://api.adzuna.com/v1/api/jobs/in/search/{page}",
                    params={
                        "app_id": s.adzuna_app_id,
                        "app_key": s.adzuna_app_key,
                        "what": query,
                        "what_exclude": "senior manager lead director principal head",
                        "max_days_old": 30,
                        "results_per_page": 50,
                        "sort_by": "date",
                        "content-type": "application/json",
                    },
                )
                results = data.get("results", [])
                for j in results:
                    title = _strip_tags(j.get("title", ""))
                    if looks_senior(title):
                        continue
                    seen[str(j["id"])] = RawPost(
                        source=self.name,
                        source_id=str(j["id"]),
                        url=j["redirect_url"],
                        title=title,
                        company=(j.get("company") or {}).get("display_name", "") or "Unknown",
                        location=(j.get("location") or {}).get("display_name", ""),
                        description=_strip_tags(j.get("description", "")),
                        posted_at=_parse_dt(j.get("created")),
                    )
                if len(results) < 50:
                    break
        return list(seen.values())


class JoobleSource(Source):
    """https://jooble.org/api/about — key is issued on request."""

    name = "jooble"
    delay = 1.0

    @property
    def enabled(self) -> bool:
        return bool(get_settings().jooble_api_key)

    def fetch(self) -> list[RawPost]:
        key = get_settings().jooble_api_key
        seen: dict[str, RawPost] = {}
        for query in FRESHER_QUERIES:
            data = self.post_json(f"https://jooble.org/api/{key}", json={"keywords": query, "location": "India", "page": 1})
            for j in data.get("jobs", []):
                title = _strip_tags(j.get("title", ""))
                if looks_senior(title):
                    continue
                seen[str(j["id"])] = RawPost(
                    source=self.name,
                    source_id=str(j["id"]),
                    url=j["link"],
                    title=title,
                    company=j.get("company") or "Unknown",
                    location=j.get("location") or "",
                    description=_strip_tags(j.get("snippet", "")),
                    posted_at=_parse_dt(j.get("updated")),
                )
        return list(seen.values())


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
