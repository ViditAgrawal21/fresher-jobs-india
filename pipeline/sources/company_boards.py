"""Public job-board APIs that companies use to publish openings on their own career pages.

None of these need a key. We keep only India posts whose title isn't obviously senior;
the LLM makes the final fresher call.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from core.models import RawPost
from pipeline.filters import is_india, looks_senior
from pipeline.sources import boards
from pipeline.sources.base import Source

log = logging.getLogger(__name__)


def _ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):  # Lever sends epoch millis
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _wanted(title: str, location: str) -> bool:
    return is_india(location) and not looks_senior(title)


class _BoardSource(Source):
    companies: dict[str, str] = {}

    def fetch(self) -> list[RawPost]:
        posts: list[RawPost] = []
        self.failed: list[str] = []
        for slug, company in self.companies.items():
            try:
                got = self.fetch_company(slug, company)
                posts.extend(got)
                log.info("%s/%s: %d India entry-level candidates", self.name, slug, len(got))
            except Exception as exc:  # one dead board shouldn't sink the rest
                log.warning("%s/%s failed: %s", self.name, slug, exc)
                self.failed.append(f"{slug}: {type(exc).__name__}")
        return posts

    def fetch_company(self, slug: str, company: str) -> list[RawPost]:
        raise NotImplementedError


class GreenhouseSource(_BoardSource):
    name = "greenhouse"
    companies = boards.GREENHOUSE

    def fetch_company(self, slug, company):
        data = self.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
        out = []
        for j in data.get("jobs", []):
            location = (j.get("location") or {}).get("name", "")
            if not _wanted(j["title"], location):
                continue
            out.append(RawPost(
                source=self.name,
                source_id=f"{slug}:{j['id']}",
                url=j["absolute_url"],
                title=j["title"],
                company=company,
                location=location,
                # Greenhouse double-encodes: the HTML itself arrives entity-escaped.
                description=html.unescape(j.get("content") or ""),
                posted_at=_ts(j.get("first_published") or j.get("updated_at")),
            ))
        return out


class LeverSource(_BoardSource):
    name = "lever"
    companies = boards.LEVER
    # api.lever.co regularly takes 20-80s to answer (measured 16 Nov); a 30s timeout
    # just made us retry the same slow request four times.
    read_timeout = 90.0

    def fetch_company(self, slug, company):
        data = self.get_json(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
        out = []
        for j in data:
            cats = j.get("categories") or {}
            location = ", ".join(cats.get("allLocations") or [cats.get("location") or ""])
            if not _wanted(j["text"], location):
                continue
            # Requirements (where "0-2 years" usually lives) are in `lists`, not the description.
            # Some boards (hevodata, mindtickle; seen 28 Nov) leave every *Plain field and
            # `lists` empty and only fill the HTML fields, so fall back to those. HTML is
            # fine here: normalize.to_row runs html_to_text before anything is stored.
            parts = [j.get("descriptionPlain") or j.get("description") or j.get("descriptionBody") or ""]
            for block in j.get("lists") or []:
                parts.append(f"{block.get('text', '')}\n{block.get('content', '')}")
            parts.append(j.get("additionalPlain") or j.get("additional") or "")
            out.append(RawPost(
                source=self.name,
                source_id=f"{slug}:{j['id']}",
                url=j["hostedUrl"],
                title=j["text"],
                company=company,
                location=location,
                description="\n".join(parts),
                posted_at=_ts(j.get("createdAt")),
            ))
        return out


class AshbySource(_BoardSource):
    name = "ashby"
    companies = boards.ASHBY

    def fetch_company(self, slug, company):
        data = self.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        out = []
        for j in data.get("jobs", []):
            locations = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
            location = ", ".join(l for l in locations if l)
            if not _wanted(j["title"], location):
                continue
            out.append(RawPost(
                source=self.name,
                source_id=f"{slug}:{j['id']}",
                url=j["jobUrl"],
                title=j["title"],
                company=company,
                location=location,
                description=j.get("descriptionPlain") or j.get("descriptionHtml") or "",
                posted_at=_ts(j.get("publishedAt")),
            ))
        return out


class SmartRecruitersSource(_BoardSource):
    """List endpoint has no description, so each new posting needs one detail call.
    We skip the detail call for postings already in the DB (known_ids)."""

    name = "smartrecruiters"
    companies = boards.SMARTRECRUITERS
    max_details_per_company = 120
    skip_levels = {"mid_senior_level", "director", "executive"}

    def __init__(self, client, known_ids: set[str] | None = None):
        super().__init__(client)
        self.known_ids = known_ids or set()

    def fetch_company(self, slug, company):
        base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        listing, offset = [], 0
        while True:
            page = self.get_json(base, params={"country": "in", "limit": 100, "offset": offset})
            listing += page.get("content", [])
            offset += 100
            if offset >= page.get("totalFound", 0) or offset >= 1500:
                break

        out, details_fetched = [], 0
        for j in listing:
            loc = j.get("location") or {}
            location = ", ".join(x for x in (loc.get("city"), loc.get("region"), "India") if x)
            if loc.get("remote"):
                location += ", Remote"
            level = (j.get("experienceLevel") or {}).get("id", "")
            if level in self.skip_levels or not _wanted(j["name"], location):
                continue

            source_id = f"{slug}:{j['id']}"
            description: str | None = None  # None = already stored, don't overwrite
            if source_id not in self.known_ids:
                if details_fetched >= self.max_details_per_company:
                    continue
                detail = self.get_json(f"{base}/{j['id']}")
                details_fetched += 1
                sections = (detail.get("jobAd") or {}).get("sections") or {}
                description = "\n".join(
                    (sections.get(k) or {}).get("text", "")
                    for k in ("jobDescription", "qualifications", "additionalInformation")
                )
            out.append(RawPost(
                source=self.name,
                source_id=source_id,
                url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                title=j["name"],
                company=company,
                location=location,
                description=description,
                posted_at=_ts(j.get("releasedDate")),
            ))
        return out
