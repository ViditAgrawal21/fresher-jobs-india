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

from jobhunt.discover import lead_description
from jobhunt.keywords import DOMAIN
from jobhunt.profile import load_profile
from jobhunt.settings import JobhuntSettings
from jobhunt.tailor import keyword_coverage
from jobhunt.tracker import WEEK_TARGETS, Tracker

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


def build_data(settings: JobhuntSettings, today: date | None = None) -> dict:
    today = today or date.today()
    profile = load_profile(settings.profile_path)
    tracker = Tracker(settings.tracker_path)
    data_dir = settings.data_dir

    apps = []
    for a in tracker.applications():
        folder = (data_dir / str(a["resume"])).parent if a["resume"] else None
        jd_path = folder / "job_description.txt" if folder else None
        if jd_path and jd_path.exists():
            cov = keyword_coverage(profile, jd_path.read_text(encoding="utf-8"), a["role"] or "")
            matched, missing, match = cov["matched"], cov["missing"], cov["coverage_pct"]
        else:  # typed by hand or added from an email: use whatever the row has
            matched, missing, match = _split(a["matched"]), _split(a["missing"]), a["match"]

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
            "resume": str(a["resume"]).replace("\\", "/") if a["resume"] and (data_dir / str(a["resume"])).exists() else None,
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
        leads.append({
            "row": r, "company": lead["company"], "title": lead["title"] or "", "location": lead["location"] or "",
            "exp": lead["exp"] or "", "score": lead["score"] or 0, "track": lead["track"] or "Other",
            "matched": _split(lead["matched"]), "missing": _split(lead["missing"]), "url": lead["url"] or "",
            "posted": _iso(lead["posted"]), "found": _iso(lead["found"]), "decision": lead["decision"] or "New",
            "has_jd": bool(lead_description(data_dir / "leads", str(lead["key"]))),
        })
    leads.sort(key=lambda x: x["score"], reverse=True)

    return {
        "name": profile.name,
        "today": today.isoformat(),
        "generated": datetime.now().strftime("%d %b %Y, %H:%M"),
        "follow_days": settings.followup_after_days,
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
    return _env.get_template("dashboard.html.j2").render(data_json=Markup(payload), name=data["name"], artifact=artifact)


def write_dashboard(settings: JobhuntSettings) -> Path:
    out = settings.data_dir / "dashboard.html"
    out.write_text(render(build_data(settings)), encoding="utf-8")
    return out

