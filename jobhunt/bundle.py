"""Move applications and job matches from one copy of your data to another, in one zip.

For example, from a Claude cloud session to your laptop:
    python -m jobhunt export-bundle A039 A040 --leads 12 15 --out picks.zip     (where they were made)
    python -m jobhunt import-bundle picks.zip                                     (on your laptop)

Each application keeps its tailored resume, report, JD and tailoring, and gets the next free ID
in the tracker it's imported into. Jobs already in that tracker (same link) are skipped.
"""

from __future__ import annotations

import json
import zipfile
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from jobhunt.keywords import slug
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import APP_COLUMNS, DATE_KEYS, FORMULA_KEYS, Tracker

VERSION = 1
_FILE_TYPES = {".pdf", ".md", ".txt", ".json"}
# Columns that describe the job and your tailoring; status history stays with each tracker.
_APP_KEYS = [k for _, _, k in APP_COLUMNS if k not in FORMULA_KEYS and k != "id"]


def _plain(v):
    return v.date().isoformat() if isinstance(v, datetime) else v.isoformat() if isinstance(v, date) else v


def export_bundle(settings: JobhuntSettings, ids: list[str], lead_rows: list[int], out: Path) -> dict:
    tracker = Tracker(settings.tracker_path)
    apps, leads = [], []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for app_id in ids:
            app = tracker.find(app_id)
            if app is None:
                raise KeyError(f"no application {app_id}")
            row = {k: _plain(app[k]) for k in _APP_KEYS if app[k] not in (None, "")}
            row["old_id"] = app["id"]
            if app["resume"]:
                folder = (settings.data_dir / str(app["resume"])).parent
                row["folder"] = folder.name
                row["resume_name"] = Path(str(app["resume"])).name
                for f in sorted(folder.iterdir()):
                    if f.is_file() and f.suffix.lower() in _FILE_TYPES:
                        z.write(f, f"applications/{folder.name}/{f.name}")
            apps.append(row)
        for r in lead_rows:
            lead = {k: _plain(v) for k, v in tracker.lead(r).items() if v not in (None, "")}
            jd = settings.data_dir / "leads" / f"{slug(str(lead['key']), 80)}.txt"
            if jd.exists():
                z.write(jd, f"leads/{jd.name}")
            leads.append(lead)
        z.writestr("bundle.json", json.dumps({"version": VERSION, "apps": apps, "leads": leads}, indent=1))
    return {"apps": len(apps), "leads": len(leads), "path": out}


def _safe(name: str, top: str) -> PurePosixPath | None:
    """A zip member under `top/`, with no absolute path or '..' parts, of a file type we keep."""
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != top or p.suffix.lower() not in _FILE_TYPES:
        return None
    return p


def import_bundle(settings: JobhuntSettings, path: Path, lock=None) -> dict:
    from contextlib import nullcontext

    with zipfile.ZipFile(path) as z, (lock or nullcontext()):
        meta = json.loads(z.read("bundle.json"))
        if meta.get("version") != VERSION:
            raise ValueError("This bundle was made by a different version of jobhunt: update with git pull first.")
        tracker = Tracker(settings.tracker_path)
        known = {str(a["url"]) for a in tracker.applications() if a["url"]}
        added, skipped = [], []
        members = z.namelist()
        for app in meta.get("apps", []):
            if app.get("url") and app["url"] in known:
                skipped.append(f"{app.get('old_id')} {app.get('company')}: already in your tracker")
                continue
            new_id = tracker.next_id()
            values = {k: v for k, v in app.items() if k in _APP_KEYS}
            for k in DATE_KEYS & values.keys():
                values[k] = date.fromisoformat(str(values[k])[:10])
            if app.get("folder"):
                rest = app["folder"].split("_", 1)[1] if "_" in app["folder"] else app["folder"]
                target = settings.data_dir / "applications" / f"{new_id}_{rest}"
                target.mkdir(parents=True, exist_ok=True)
                for m in members:
                    p = _safe(m, "applications")
                    if p is not None and len(p.parts) == 3 and p.parts[1] == app["folder"]:
                        (target / p.name).write_bytes(z.read(m))
                values["resume"] = f"applications/{target.name}/{app.get('resume_name', '')}"
            tracker.add_application(id=new_id, **values)
            tracker.append_note(new_id, f"[{date.today():%d %b}] imported from a bundle (was {app.get('old_id')})")
            known.add(str(app.get("url")))
            added.append((app.get("old_id"), new_id, app.get("company"), app.get("role")))
        leads = []
        for lead in meta.get("leads", []):
            for k in ("posted", "found"):
                if lead.get(k):
                    lead[k] = date.fromisoformat(str(lead[k])[:10])
            leads.append(lead)
        n_leads = tracker.add_leads(leads)
        for m in members:
            p = _safe(m, "leads")
            if p is not None and len(p.parts) == 2:
                out = settings.data_dir / "leads" / p.name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(z.read(m))
        tracker.save()
    return {"added": added, "skipped": skipped, "leads": n_leads}
