"""Job sources for your own search: India and remote roles, with remote/hybrid/office and country.

The Fresher Jobs India pipeline's fetchers keep only India locations. These keep remote roles
too, and put the board's own remote/hybrid/office field and country into the location line,
so `locations.classify` can rank them (remote India, remote open to India, hybrid, office).

Remote job boards (each asks to be credited and linked to; every lead keeps the board's name as
its source and links to the board's listing):
- Remotive: at most 4 requests a day, so it's fetched at most every 6 hours.
- Jobicy, Remote OK, Himalayas, We Work Remotely (RSS), Working Nomads: one request per feed,
  cached for a few hours; Himalayas' search is rate-limited, and is skipped (not retried) when busy.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.models import RawPost
from jobhunt.locations import _REMOTE
from pipeline.filters import is_india, looks_senior
from pipeline.sources.base import Source
from pipeline.sources.company_boards import AshbySource, GreenhouseSource, LeverSource, SmartRecruitersSource, _ts

log = logging.getLogger(__name__)

COUNTRY = {"IN": "India", "US": "United States", "GB": "United Kingdom", "CA": "Canada", "DE": "Germany",
           "SG": "Singapore", "AU": "Australia", "NL": "Netherlands", "FR": "France", "IE": "Ireland", "ES": "Spain"}


def _keep(title: str, location: str) -> bool:
    """Worth scoring: India or remote, and not a senior title. `discover` does the fine filtering."""
    return not looks_senior(title) and (is_india(location) or bool(_REMOTE.search(location)))


def _join(*parts: str) -> str:
    return " · ".join(p for p in (x.strip() for x in parts if x) if p)


class Greenhouse(GreenhouseSource):
    def fetch_company(self, slug, company):
        data = self.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
        out = []
        for j in data.get("jobs", []):
            location = (j.get("location") or {}).get("name", "")
            offices = ", ".join(o.get("name", "") for o in j.get("offices") or [] if o.get("name"))
            if offices and offices.lower() not in location.lower():
                location = _join(location, offices)
            if not _keep(j["title"], location):
                continue
            out.append(RawPost(source=self.name, source_id=f"{slug}:{j['id']}", url=j["absolute_url"], title=j["title"],
                               company=company, location=location, description=html.unescape(j.get("content") or ""),
                               posted_at=_ts(j.get("first_published") or j.get("updated_at"))))
        return out


class Lever(LeverSource):
    def fetch_company(self, slug, company):
        data = self.get_json(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
        out = []
        for j in data:
            cats = j.get("categories") or {}
            location = _join(", ".join(cats.get("allLocations") or [cats.get("location") or ""]),
                             j.get("workplaceType") or "", COUNTRY.get(j.get("country") or "", j.get("country") or ""))
            if not _keep(j["text"], location):
                continue
            # Some postings only fill the HTML fields; discover turns HTML into text.
            parts = [j.get("descriptionPlain") or j.get("description") or ""]
            for block in j.get("lists") or []:
                parts.append(f"{block.get('text', '')}\n{block.get('content', '')}")
            parts.append(j.get("additionalPlain") or j.get("additional") or "")
            out.append(RawPost(source=self.name, source_id=f"{slug}:{j['id']}", url=j["hostedUrl"], title=j["text"],
                               company=company, location=location, description="\n".join(parts),
                               posted_at=_ts(j.get("createdAt"))))
        return out


class Ashby(AshbySource):
    def fetch_company(self, slug, company):
        data = self.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        out = []
        for j in data.get("jobs", []):
            places = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
            country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or ""
            mode = j.get("workplaceType") or ("Remote" if j.get("isRemote") else "")
            location = _join(", ".join(p for p in places if p), mode, country)
            if not _keep(j["title"], location):
                continue
            out.append(RawPost(source=self.name, source_id=f"{slug}:{j['id']}", url=j["jobUrl"], title=j["title"],
                               company=company, location=location,
                               description=j.get("descriptionPlain") or j.get("descriptionHtml") or "",
                               posted_at=_ts(j.get("publishedAt"))))
        return out


class SmartRecruiters(SmartRecruitersSource):
    """India postings only (the API filters by country); the pipeline version already adds
    ", Remote" for remote India roles."""


# ---------- remote job boards ----------

class _RemoteBoard(Source):
    min_interval_hours = 0.0  # boards that ask for few requests a day set this

    def __init__(self, client: httpx.Client, cache_dir: Path | None = None):
        super().__init__(client)
        self.cache_dir = cache_dir
        self.failed: list[str] = []

    def fetch(self) -> list[RawPost]:
        cache = self.cache_dir / f"board_{self.name}.json" if self.cache_dir else None
        if cache and cache.exists() and time.time() - cache.stat().st_mtime < self.min_interval_hours * 3600:
            return [RawPost.model_validate(x) for x in json.loads(cache.read_text(encoding="utf-8"))]
        posts = [p for p in self.fetch_board() if _keep(p.title, p.location)]
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps([json.loads(p.model_dump_json()) for p in posts]), encoding="utf-8")
        return posts

    def fetch_board(self) -> list[RawPost]:
        raise NotImplementedError


class Remotive(_RemoteBoard):
    name = "remotive"
    min_interval_hours = 6.0  # Remotive asks for at most 4 requests a day

    def fetch_board(self):
        data = self.get_json("https://remotive.com/api/remote-jobs", params={"category": "software-dev"})
        return [RawPost(source=self.name, source_id=f"remotive:{j['id']}", url=j["url"], title=j["title"],
                        company=j.get("company_name") or "", location=_join("Remote", j.get("candidate_required_location") or ""),
                        description=j.get("description") or "", posted_at=_ts(j.get("publication_date")))
                for j in data.get("jobs", [])]


class Jobicy(_RemoteBoard):
    name = "jobicy"
    min_interval_hours = 3.0

    def fetch_board(self):
        data = self.get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 100, "industry": "dev"})
        return [RawPost(source=self.name, source_id=f"jobicy:{j['id']}", url=j["url"], title=html.unescape(j["jobTitle"]),
                        company=j.get("companyName") or "", location=_join("Remote", j.get("jobGeo") or ""),
                        description=j.get("jobDescription") or "", posted_at=_ts(j.get("pubDate")))
                for j in data.get("jobs", [])]


class RemoteOK(_RemoteBoard):
    name = "remoteok"
    min_interval_hours = 3.0

    def fetch_board(self):
        data = self.get_json("https://remoteok.com/api")
        out = []
        for j in data[1:] if isinstance(data, list) else []:  # item 0 is the terms of use
            if not j.get("position"):
                continue
            out.append(RawPost(source=self.name, source_id=f"remoteok:{j['id']}", url=j.get("url") or "",
                               title=html.unescape(j["position"]), company=j.get("company") or "",
                               location=_join("Remote", j.get("location") or "Worldwide"),
                               description=j.get("description") or "", posted_at=_ts(j.get("date"))))
        return out


class Himalayas(_RemoteBoard):
    name = "himalayas"
    min_interval_hours = 6.0
    queries = ("software engineer", "backend engineer", "full stack engineer", "ai engineer")

    def fetch_board(self):
        out = []
        for q in self.queries:
            self._throttle()
            r = self.client.get("https://himalayas.app/jobs/api/search", params={"q": q, "country": "India"})
            if r.status_code == 429:  # busy: skip rather than hammer it
                self.failed.append("rate-limited, try later")
                break
            r.raise_for_status()
            for j in r.json().get("jobs", []):
                restr = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
                posted = j.get("pubDate")
                out.append(RawPost(source=self.name, source_id=f"himalayas:{j.get('guid') or j['applicationLink']}",
                                   url=j.get("applicationLink") or "", title=j["title"], company=j.get("companyName") or "",
                                   location=_join("Remote", restr), description=j.get("description") or "",
                                   posted_at=datetime.fromtimestamp(posted, tz=timezone.utc) if isinstance(posted, (int, float))
                                   else _ts(posted)))
            time.sleep(2)
        return out


class WeWorkRemotely(_RemoteBoard):
    name = "weworkremotely"
    min_interval_hours = 3.0
    feeds = ("remote-back-end-programming-jobs", "remote-full-stack-programming-jobs", "remote-programming-jobs")

    def fetch_board(self):
        out = []
        for feed in self.feeds:
            self._throttle()
            r = self.client.get(f"https://weworkremotely.com/categories/{feed}.rss")
            r.raise_for_status()
            for item in re.findall(r"<item>(.*?)</item>", r.text, re.S):
                field = lambda tag: html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(rf"<{tag}>(.*?)</{tag}>", item, re.S)
                                                                                    or [None, ""])[1])).strip()
                company, _, title = field("title").partition(": ")
                if not title:
                    company, title = "", company
                try:
                    posted = parsedate_to_datetime(field("pubDate"))
                except (TypeError, ValueError):
                    posted = None
                out.append(RawPost(source=self.name, source_id=f"wwr:{field('guid') or field('link')}", url=field("link"),
                                   title=title, company=company, location=_join("Remote", field("region") or "Worldwide"),
                                   description=field("description"), posted_at=posted))
        return out


class WorkingNomads(_RemoteBoard):
    name = "workingnomads"
    min_interval_hours = 3.0

    def fetch_board(self):
        data = self.get_json("https://www.workingnomads.com/api/exposed_jobs/")
        return [RawPost(source=self.name, source_id=f"wn:{j.get('url')}", url=j.get("url") or "", title=j.get("title") or "",
                        company=j.get("company_name") or "", location=_join("Remote", j.get("location") or "Worldwide"),
                        description=j.get("description") or "", posted_at=_ts(j.get("pub_date")))
                for j in data if (j.get("category_name") or "").lower() == "development"]
