"""Is each job real, still open, and recent? Asks the company's own job board.

Every job here comes from a company's applicant-tracking system (Greenhouse, Lever, Ashby,
SmartRecruiters, Oracle), the same place the company's careers page reads from. So one call
per job tells us whether the posting is still live (a removed job returns "not found"), when
it was first published, and for Greenhouse and Oracle, the application deadline.

Freshness caps (settings, days since the job was first published):
- up to FRESH_JOB_DAYS (30): fresh, the best time to apply;
- up to STALE_JOB_DAYS (60): aging, still worth it if the fit is good;
- older: likely an evergreen or slow-moving opening. Shown as old, sorted last, and left
  out of "autofill the top N ready jobs".
A closed job still marked Ready to apply is moved to Withdrawn, with the reason in Notes.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx

from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker

log = logging.getLogger(__name__)

READY = ("Lead", "Tailored")
# Wording job pages use once a role is filled or taken down.
CLOSED_TEXT = re.compile(
    r"no longer (?:accepting applications|available|open)|(?:position|role|job) (?:has been|is) (?:filled|closed)"
    r"|job (?:is )?(?:not found|closed|expired)|(?:job|position|role|posting) (?:has )?(?:expired|closed)"
    r"|this job is no longer",
    re.I,
)


@dataclass
class Check:
    state: str                      # "Open" | "Closed" | "Unknown"
    posted: date | None = None
    detail: str = ""


@dataclass
class Result:
    id: str
    company: str
    role: str
    check: Check
    withdrawn: bool = False         # was Ready, the job closed, so it moved to Withdrawn


def _date(v) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):  # Lever: milliseconds since 1970
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def ats_ref(url: str, lead_key: str = "") -> tuple[str, str, str] | None:
    """(system, board, job id) from the key `discover` saved, else from the job URL."""
    if lead_key and lead_key.count(":") >= 2:
        ats, board, job = lead_key.split(":", 2)
        if ats in ("greenhouse", "lever", "ashby", "smartrecruiters"):
            return ats, board, job
    u = urlparse(url or "")
    host, q = u.netloc.lower(), parse_qs(u.query)
    parts = [p for p in u.path.split("/") if p]
    if host.endswith("greenhouse.io"):
        if "for" in q and "token" in q:
            return "greenhouse", q["for"][0], q["token"][0]
        if len(parts) >= 3 and parts[1] == "jobs":
            return "greenhouse", parts[0], parts[2]
    if host == "jobs.lever.co" and len(parts) >= 2:
        return "lever", parts[0], parts[1]
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return "ashby", parts[0], parts[1]
    if host == "jobs.smartrecruiters.com" and len(parts) >= 2:
        return "smartrecruiters", parts[0], parts[1].split("-")[0]
    if host.endswith(".oraclecloud.com") and (m := re.search(r"/sites/([^/]+)/job/(\d+)", u.path)):
        return "oracle", f"{host}|{m[1]}", m[2]
    return None


def check_posting(client: httpx.Client, url: str, lead_key: str = "", today: date | None = None,
                  ashby_boards: dict | None = None) -> Check:
    today = today or date.today()
    ref = ats_ref(url, lead_key)
    try:
        if ref is None:
            return _check_page(client, url)
        ats, board, job = ref
        if ats == "greenhouse":
            r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}")
            if r.status_code == 404:
                return Check("Closed", detail="taken down from the company's Greenhouse board")
            r.raise_for_status()
            j = r.json()
            posted, deadline = _date(j.get("first_published") or j.get("updated_at")), _date(j.get("application_deadline"))
            if deadline and deadline < today:
                return Check("Closed", posted, f"applications closed on {deadline:%d %b %Y}")
            return Check("Open", posted, f"apply by {deadline:%d %b}" if deadline else "")
        if ats == "lever":
            r = client.get(f"https://api.lever.co/v0/postings/{board}/{job}")
            if r.status_code == 404:
                return Check("Closed", detail="taken down from the company's Lever board")
            r.raise_for_status()
            return Check("Open", _date(r.json().get("createdAt")))
        if ats == "smartrecruiters":
            r = client.get(f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{job}")
            if r.status_code == 404:
                return Check("Closed", detail="taken down from the company's SmartRecruiters board")
            r.raise_for_status()
            j = r.json()
            if j.get("active") is False:
                return Check("Closed", _date(j.get("releasedDate")), "marked inactive")
            return Check("Open", _date(j.get("releasedDate")))
        if ats == "ashby":
            boards = ashby_boards if ashby_boards is not None else {}
            if board not in boards:
                r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
                r.raise_for_status()
                boards[board] = {str(x.get("id")): x for x in r.json().get("jobs", [])}
            j = boards[board].get(job)
            if j is None:
                return Check("Closed", detail="taken down from the company's Ashby board")
            return Check("Open", _date(j.get("publishedAt")))
        if ats == "oracle":
            host, site = board.split("|", 1)
            r = client.get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
                           params={"expand": "all", "onlyData": "true", "finder": f'ById;Id="{job}",siteNumber={site}'})
            r.raise_for_status()
            items = r.json().get("items") or []
            if not items:
                return Check("Closed", detail="taken down from the company's careers site")
            posted, end = _date(items[0].get("ExternalPostedStartDate")), _date(items[0].get("ExternalPostedEndDate"))
            if end and end < today:
                return Check("Closed", posted, f"applications closed on {end:%d %b %Y}")
            return Check("Open", posted, f"apply by {end:%d %b}" if end else "")
    except (httpx.HTTPError, ValueError) as exc:
        return Check("Unknown", detail=f"couldn't reach the job board ({type(exc).__name__})")
    return Check("Unknown")


def _check_page(client: httpx.Client, url: str) -> Check:
    host = urlparse(url or "").netloc.lower()
    if not str(url or "").startswith("http"):
        return Check("Unknown", detail="no job link")
    if "linkedin.com" in host or "naukri.com" in host:
        return Check("Unknown", detail="LinkedIn and Naukri can't be checked automatically; open the link")
    r = client.get(url)
    if r.status_code in (404, 410):
        return Check("Closed", detail=f"the job page is gone (HTTP {r.status_code})")
    if CLOSED_TEXT.search(r.text[:300_000]):
        return Check("Closed", detail="the job page says it's closed")
    return Check("Unknown", detail="the page loads, but its job board can't be asked directly")


def freshness(posted: date | None, today: date, fresh_days: int, stale_days: int) -> str:
    """fresh | aging | old | unknown, by days since the job was first published."""
    if posted is None:
        return "unknown"
    age = (today - posted).days
    return "fresh" if age <= fresh_days else "aging" if age <= stale_days else "old"


def _lead_keys(tracker: Tracker) -> dict[str, str]:
    keys = {}
    for r in range(2, tracker.leads.max_row + 1):
        try:
            lead = tracker.lead(r)
        except KeyError:
            continue
        if lead["url"] and lead["key"]:
            keys[str(lead["url"])] = str(lead["key"])
    return keys


def verify_applications(settings: JobhuntSettings, ids: list[str] | None = None, *, lock: threading.Lock | None = None,
                        client: httpx.Client | None = None, today: date | None = None, progress=None) -> list[Result]:
    """Check the given applications (default: every Ready one) and record the results in the tracker.

    The tracker is only read and written under `lock`; the network calls happen outside it."""
    lock = lock or threading.Lock()
    today = today or date.today()
    with lock:
        tracker = Tracker(settings.tracker_path)
        keys = _lead_keys(tracker)
        wanted = {i.upper() for i in ids} if ids else None
        todo = [(a["id"], a["company"], a["role"] or "", str(a["url"] or ""), keys.get(str(a["url"] or ""), ""))
                for a in tracker.applications()
                if (wanted is None and a["status"] in READY) or (wanted is not None and str(a["id"]).upper() in wanted)]
    own = client is None
    client = client or httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "jobhunt (personal job search)"})
    ashby: dict = {}
    done, count_lock = [0], threading.Lock()

    def one(item) -> Result:
        app_id, company, role, url, key = item
        res = Result(app_id, company, role, check_posting(client, url, key, today, ashby))
        with count_lock:
            done[0] += 1
            if progress:
                progress(done[0], len(todo), company)
        return res

    # Several boards at once: one slow board (Lever can take a minute) no longer holds up the rest.
    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(one, todo))
    finally:
        if own:
            client.close()
    with lock:
        tracker = Tracker(settings.tracker_path)
        for res in results:
            app = tracker.find(res.id)
            if app is None:
                continue
            c = res.check
            values = {"posting": f"{c.state}: {c.detail}" if c.detail else c.state, "checked": today}
            if res.check.posted:
                values["posted"] = res.check.posted
            tracker.update(res.id, **values)
            if res.check.state == "Closed" and app["status"] in READY:
                tracker.move(res.id, "Withdrawn", today, note=f"job closed: {res.check.detail}", via="check")
                res.withdrawn = True
        tracker.save()
    return results


@dataclass
class VerifyJob:
    """A background check run by the dashboard server; the page polls `status()`."""

    state: str = "idle"             # idle | running | done | error
    done: int = 0
    total: int = 0
    message: str = ""
    closed: list[str] = field(default_factory=list)

    def status(self) -> dict:
        return {"state": self.state, "done": self.done, "total": self.total, "message": self.message,
                "closed": self.closed}


def summary(results: list[Result], today: date, fresh_days: int, stale_days: int) -> str:
    counts = {"Open": 0, "Closed": 0, "Unknown": 0}
    old = 0
    for r in results:
        counts[r.check.state] += 1
        if r.check.state == "Open" and freshness(r.check.posted, today, fresh_days, stale_days) == "old":
            old += 1
    parts = [f"{counts['Open']} still open", f"{counts['Closed']} closed"]
    if counts["Unknown"]:
        parts.append(f"{counts['Unknown']} couldn't be checked")
    if old:
        parts.append(f"{old} open but posted over {stale_days} days ago")
    return ", ".join(parts) + "."
