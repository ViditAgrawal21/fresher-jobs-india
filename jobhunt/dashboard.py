"""`python -m jobhunt dashboard`: one self-contained HTML page over the tracker and application folders.

Everything is computed from local files (tracker.xlsx, applications/*/job_description.txt,
leads/*.txt) and embedded as JSON, so the page opens offline from disk, and links to each
tailored resume, report and job description work as plain relative links.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from jobhunt.discover import EMPLOYEES, lead_description, size_band
from jobhunt.keywords import DOMAIN
from jobhunt.locations import classify
from jobhunt.profile import load_profile
from jobhunt.settings import JobhuntSettings
from jobhunt.tailor import keyword_coverage
from jobhunt.tracker import WEEK_TARGETS, Tracker
from jobhunt.prep import prep_path
from jobhunt.verify import freshness

_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)


def _iso(v) -> str | None:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return None


def _split(v) -> list[str]:
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def _weeks(tracker: Tracker) -> list[dict]:
    """Weekly targets as edited in the workbook's Dashboard sheet, else the built-in plan."""
    ws = tracker.wb["Dashboard"]
    out = []
    started = False
    for r in range(1, ws.max_row + 1):
        a, b = ws.cell(row=r, column=1).value, ws.cell(row=r, column=2).value
        if a == "Week of (Mon)":
            started = True
            continue
        if started:
            if not isinstance(a, (date, datetime)):
                break
            out.append({"week": _iso(a), "target": b if isinstance(b, (int, float)) else 0})
    return out or [{"week": d.isoformat(), "target": t} for d, t in WEEK_TARGETS]


def _where_rank(label: str) -> int:
    """The location rank from a stored label (see locations.py)."""
    if label.startswith("Remote (India)"):
        return 0
    if label.startswith("Remote"):
        return 1
    if "(wider search)" in label:
        return 4
    if label.startswith("Hybrid"):
        return 2
    return 3


def build_data(settings: JobhuntSettings, today: date | None = None) -> dict:
    today = today or date.today()
    profile = load_profile(settings.profile_path)
    tracker = Tracker(settings.tracker_path)
    data_dir = settings.data_dir

    fresh_days, stale_days = settings.fresh_job_days, settings.stale_job_days
    lead_posted = {}
    for r in range(2, tracker.leads.max_row + 1):
        try:
            lead = tracker.lead(r)
        except KeyError:
            continue
        if lead["url"] and lead["posted"]:
            lead_posted[str(lead["url"])] = lead["posted"]

    apps = []
    for a in tracker.applications():
        folder = (data_dir / str(a["resume"])).parent if a["resume"] else None
        jd_path = folder / "job_description.txt" if folder else None
        if jd_path and jd_path.exists():
            cov = keyword_coverage(profile, jd_path.read_text(encoding="utf-8"), a["role"] or "")
            matched, missing, match = cov["matched"], cov["missing"], cov["coverage_pct"]
        else:  # typed by hand or added from an email: use whatever the row has
            matched, missing, match = _split(a["matched"]), _split(a["missing"]), a["match"]

        posted = a["posted"] or lead_posted.get(str(a["url"] or ""))
        posted = posted.date() if isinstance(posted, datetime) else posted
        posting = str(a["posting"] or "")
        fresh = freshness(posted, today, fresh_days, stale_days)
        size_rank = size_band(EMPLOYEES.get(a["company"]))[0]
        where = classify(str(a["location"]), "", settings.preferred_cities, settings.remote_abroad) if a["location"] else None
        where_rank = where.rank if where and where.ok else 4

        def rel(name: str) -> str | None:
            return f"{folder.relative_to(data_dir).as_posix()}/{name}" if folder and (folder / name).exists() else None

        apps.append({
            "id": a["id"], "company": a["company"], "role": a["role"] or "", "track": a["track"] or "Other",
            "status": a["status"] or "Tailored", "source": a["source"] or "Other",
            "added": _iso(a["date_added"]), "applied": _iso(a["date_applied"]),
            "email": a["email_received"] == "Yes", "signal": a["email_signal"] or "",
            "last_email": _iso(a["last_email_date"]), "subject": a["last_email_subject"] or "",
            "next": a["next_step"] or "", "next_date": _iso(a["next_step_date"]),
            "fit": a["fit"] if isinstance(a["fit"], (int, float)) else None,
            "match": match if isinstance(match, (int, float)) else None,
            "matched": matched, "missing": missing,
            "url": a["url"] or "",
            "employees": EMPLOYEES.get(a["company"]),
            "size": size_band(EMPLOYEES.get(a["company"]))[1],
            # Ordering hint for the board: 500-1,000 employees first, then other mid-size, large, small;
            # jobs advised against go last.
            "flag": 9 if "Not recommended" in str(a["notes"] or "") else size_rank,
            # When the company published it, whether `verify` found it still open, and the age bucket.
            "posted": _iso(posted), "age": (today - posted).days if posted else None, "fresh": fresh,
            "posting": posting.split(":")[0].strip(), "posting_note": posting.partition(":")[2].strip(),
            "checked": _iso(a["checked"]),
            # Ready-to-apply order: fresh jobs first, then aging/undated, then old; inside each,
            # by company size band; advised-against jobs last.
            # A job with an application deadline goes first: it won't wait.
            # Inside each age bucket: your location preference, then company size.
            "rank": 999 if "Not recommended" in str(a["notes"] or "") else
                    -1 if posting.startswith("Open: apply by") else
                    {"fresh": 0, "aging": 100, "unknown": 100, "old": 200}[fresh] + 10 * where_rank + size_rank,
            "where": where.label if where else "", "where_ok": bool(where and where.ok),
            "resume": str(a["resume"]).replace("\\", "/") if a["resume"] and (data_dir / str(a["resume"])).exists() else None,
            "prep": f"prep/{a['id']}" if prep_path(settings, a.values) else None,
            "report": rel("report.md"), "jd": rel("job_description.txt"),
        })

    applied_urls = {a["url"] for a in apps if a["url"]}
    leads = []
    for r in range(2, tracker.leads.max_row + 1):
        try:
            lead = tracker.lead(r)
        except KeyError:
            continue
        if lead["decision"] in ("Skip", "Applied") or lead["url"] in applied_urls:
            continue  # skipped, or already has a tailored resume in Applications
        lp = lead["posted"].date() if isinstance(lead["posted"], datetime) else lead["posted"]
        if lp and (today - lp).days > stale_days:
            continue  # too old to be worth a tailored resume
        where_label = lead.get("where") or ""
        w = classify(str(lead["location"] or ""), "", settings.preferred_cities, settings.remote_abroad)
        if not where_label:
            if not w.ok:
                continue  # found before location preferences existed, and not in one of your places
            where_label = w.label
        leads.append({
            "row": r, "company": lead["company"], "title": lead["title"] or "", "location": lead["location"] or "",
            "where": where_label, "where_rank": _where_rank(where_label), "source": str(lead["source"] or ""),
            "exp": lead["exp"] or "", "score": lead["score"] or 0, "track": lead["track"] or "Other",
            "size": size_band(EMPLOYEES.get(lead["company"]))[1],
            "matched": _split(lead["matched"]), "missing": _split(lead["missing"]), "url": lead["url"] or "",
            "posted": _iso(lp), "age": (today - lp).days if lp else None, "found": _iso(lead["found"]),
            "decision": lead["decision"] or "New",
            "has_jd": bool(lead_description(data_dir / "leads", str(lead["key"]))),
        })
    # Best JD match first, then your preferred kind of location, then the newest.
    leads.sort(key=lambda x: (-x["score"], x["where_rank"], x["age"] if x["age"] is not None else 999))

    return {
        "name": profile.name,
        "today": today.isoformat(),
        "generated": datetime.now().strftime("%d %b %Y, %H:%M"),
        # Status changes saved from the hosted dashboard after this moment override the tracker's.
        "built_ms": int(datetime.now().timestamp() * 1000),
        "follow_days": settings.followup_after_days,
        "fresh_days": fresh_days, "stale_days": stale_days, "cities": settings.preferred_cities,
        "remote_abroad": settings.remote_abroad, "claude": bool(settings.anthropic_api_key),
        "supported": sorted(profile.supported_terms()),
        "domain": sorted(DOMAIN),
        "apps": apps,
        "leads": leads,
        "weeks": _weeks(tracker),
    }


def render(data: dict, artifact: bool = False) -> str:
    """The page. `artifact=True` leaves out the html/head/body skeleton and the theme button,
    for hosts that wrap the page themselves and own the theme (a claude.ai artifact)."""
    # JSON inside <script>: escape "<" so no string in the data can close the tag.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return _env.get_template("dashboard.html.j2").render(data_json=Markup(payload), name=data["name"], artifact=artifact,
                                                         fresh_days=data.get("fresh_days", 30),
                                                         stale_days=data.get("stale_days", 60))


def write_dashboard(settings: JobhuntSettings) -> Path:
    out = settings.data_dir / "dashboard.html"
    out.write_text(render(build_data(settings)), encoding="utf-8")
    return out

