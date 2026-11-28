"""`python -m jobhunt serve`: the dashboard on http://127.0.0.1:8765, wired to your files.

Unlike the static dashboard.html, this page:
- saves every board move straight into tracker.xlsx;
- opens each tailored resume, report and job description;
- checks every Ready job is still open on the company's own job board (on start, once a
  day, and from the "Check jobs are still open" button), and skips closed jobs;
- finds more jobs ("Find more jobs": company and remote job boards, ranked by how well the JD
  matches your profile and by your location preferences), and tailors a resume to any match or
  to a job you add by its link;
- runs autofill, either for one job (the Autofill button on a Ready card) or for the top N
  fresh ready jobs. A browser window opens with one tab per job and the form filled in. You check
  each form and click Submit yourself; a thank-you page marks the job Submitted.

The server listens on 127.0.0.1 only and refuses requests from other sites, so nothing on
your network or in another browser tab can read or change the tracker.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
from dataclasses import dataclass, field
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from jobhunt import autofill, verify
from jobhunt.dashboard import build_data, render
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765
# Files the dashboard links to. Anything else in jobhunt/data (answers, browser profile) is never served.
_TYPES = {".pdf": "application/pdf", ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


class Move(BaseModel):
    id: str = Field(min_length=1, max_length=12)
    status: str = Field(min_length=1, max_length=20)
    note: str = Field("", max_length=300)


class TailorRequest(BaseModel):
    """A lead row from "New job matches", or a job you add: its link, and/or its pasted description."""

    row: int | None = Field(None, ge=2)
    url: str = Field("", max_length=2000)
    company: str = Field("", max_length=120)
    role: str = Field("", max_length=200)
    jd: str = Field("", max_length=60000)


@dataclass
class Task:
    """One background job the page shows: a search for more jobs, or tailoring one resume."""

    id: int
    kind: str                       # discover | tailor
    label: str
    state: str = "queued"           # queued | running | done | error
    message: str = ""
    app_id: str = ""

    def status(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state,
                "message": self.message, "app_id": self.app_id}


class DiscoverRequest(BaseModel):
    wider: bool = False  # also jobs up to STALE_JOB_DAYS old, and other Indian cities


class AutofillRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=10)
    all_ready: bool = False
    limit: int = Field(5, ge=1, le=10)
    submit: bool = False  # submit each form for you once every required question is answered


def create_app(settings: JobhuntSettings, *, runner=None, port: int = DEFAULT_PORT, checker=None,
               check_on_start: bool = False, finder=None, tailorer=None, mailer=None) -> FastAPI:
    """`runner`, `checker`, `finder` and `tailorer` replace autofill.run, verify.verify_applications,
    discover.run_discover and tailor_job in tests."""
    app = FastAPI(title="jobhunt", docs_url=None, redoc_url=None, openapi_url=None)
    lock = threading.Lock()  # one reader/writer of tracker.xlsx at a time
    job: dict = {"session": None, "thread": None}
    runner = runner or autofill.run
    checker = checker or verify.verify_applications
    vjob = {"job": verify.VerifyJob(), "thread": None}
    mail = {"state": "idle", "message": "", "changes": 0, "added": 0, "thread": None}
    tasks: list[Task] = []
    work: queue.Queue = queue.Queue()  # tailoring runs one at a time, in the order you click
    worker: dict = {"thread": None}

    def finder_default(progress, wider=False):
        from jobhunt.discover import run_discover
        return run_discover(settings, lock=lock, progress=progress, wider=wider)

    finder = finder or finder_default
    tailorer = tailorer or (lambda req, task: tailor_job(settings, req, task, lock))
    files_root = (settings.data_dir / "applications").resolve()
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "testserver"}

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        # A web page you visit can send requests to localhost. A Host check stops DNS-rebinding
        # reads; an Origin check stops other sites' forms and scripts from changing anything.
        if request.headers.get("host", "") not in hosts:
            return JSONResponse({"detail": "Unknown host"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method != "GET" and origin and origin.split("://", 1)[-1] not in hosts:
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        return await call_next(request)

    def data() -> dict:
        with lock:
            d = build_data(settings)
        d["mode"] = "server"
        return d

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(render(data()), headers={"Cache-Control": "no-store"})

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/api/data")
    def api_data():
        return JSONResponse(data(), headers={"Cache-Control": "no-store"})

    @app.post("/api/status")
    def api_status(m: Move):
        with lock:
            t = Tracker(settings.tracker_path)
            try:
                changed = t.move(m.id, m.status, note=m.note)
            except KeyError as exc:
                raise HTTPException(404, str(exc).strip("'\"")) from exc
            if changed:
                try:
                    t.save()
                except RuntimeError as exc:  # tracker.xlsx is open in Excel (Windows locks it)
                    raise HTTPException(423, str(exc)) from exc
        return {"ok": True, "changed": changed}

    def start_check() -> verify.VerifyJob:
        if vjob["thread"] is not None and vjob["thread"].is_alive():
            return vjob["job"]
        job_ = verify.VerifyJob(state="running")

        def progress(n, total, company):
            job_.done, job_.total = n - 1, total

        def work():
            try:
                results = checker(settings, lock=lock, progress=progress)
                job_.done = job_.total = len(results)
                job_.closed = [r.id for r in results if r.withdrawn]
                job_.message = verify.summary(results, date.today(), settings.fresh_job_days, settings.stale_job_days)
                if job_.closed:
                    job_.message += f" Moved to Withdrawn: {', '.join(job_.closed)}."
                job_.state = "done"
            except Exception as exc:  # offline, or the tracker is open in Excel
                job_.state, job_.message = "error", str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                log.warning("posting check failed: %s", exc)

        vjob.update(job=job_, thread=threading.Thread(target=work, daemon=True, name="verify"))
        vjob["thread"].start()
        return job_

    @app.get("/api/verify")
    def verify_status():
        return vjob["job"].status()

    @app.post("/api/verify")
    def verify_start():
        return start_check().status()

    # ---------- replies from companies (Gmail) ----------
    def mail_default():
        from jobhunt.mailsync import run_sync

        def progress(done, total):
            mail["message"] = f"Reading job-related emails: {done} of {total}…"
        return run_sync(settings, lock=lock, progress=progress)

    mailer_fn = mailer or mail_default

    def start_mail() -> dict:
        if mail["thread"] is not None and mail["thread"].is_alive():
            return mail_status()

        def work():
            mail.update(state="running", message="Reading your Gmail for replies…")
            try:
                out = mailer_fn()
                rep_ = out["report"]
                mail.update(state="done", changes=len(rep_.changes), added=len(rep_.added),
                            message=(f"Checked job emails since {out['since']:%d %b}: {len(out['mails'])} new, "
                                     f"{len(rep_.changes)} matched your applications"
                                     + (f", {len(rep_.added)} new applications found in confirmation emails" if rep_.added else "")
                                     + "."))
            except Exception as exc:  # wrong App Password, IMAP off, offline: say which
                mail.update(state="error", message=str(exc).splitlines()[0] if str(exc) else type(exc).__name__)

        mail["thread"] = threading.Thread(target=work, daemon=True, name="mail")
        mail["thread"].start()
        return mail_status()

    def mail_status() -> dict:
        return {k: mail[k] for k in ("state", "message", "changes", "added")} | {"configured": all(settings.gmail_login)}

    @app.get("/api/report.xlsx")
    def report_download():
        from jobhunt.report import build_report

        with lock:
            body = build_report(settings)
        name = f"job-search-report-{date.today().isoformat()}.xlsx"
        return Response(body, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.get("/api/sync-mail")
    def sync_mail_status():
        return mail_status()

    @app.post("/api/sync-mail")
    def sync_mail_start():
        if not all(settings.gmail_login):
            raise HTTPException(400, "Add GMAIL_APP_PASSWORD (a 16-letter Google App Password) and CANDIDATE_EMAIL to .env, "
                                     "then restart the dashboard.")
        return start_mail()

    if check_on_start and _needs_check(settings):
        start_check()
    if check_on_start and all(settings.gmail_login):
        start_mail()

    # ---------- find more jobs / tailor a resume ----------
    def new_task(kind: str, label: str) -> Task:
        t = Task(id=len(tasks) + 1, kind=kind, label=label)
        tasks.append(t)
        del tasks[:-30]
        return t

    @app.post("/api/discover")
    def discover_start(req: DiscoverRequest | None = None):
        wider = bool(req and req.wider)
        running = next((t for t in tasks if t.kind == "discover" and t.state in ("queued", "running")), None)
        if running:
            return running.status()
        task = new_task("discover", "Find more jobs (wider search)" if wider else "Find more jobs")

        def run():
            from jobhunt.discover import SOURCE_LABELS

            task.state, task.message = "running", "Starting the search…"
            try:
                out = finder(lambda name, i, n: setattr(task, "message",
                                                         f"Searching {SOURCE_LABELS.get(name, name)} ({i + 1} of {n})…"),
                             wider=wider)
                task.message = (f"Checked {out['posts']} jobs: {len(out['rows'])} match your profile and locations, "
                                f"{out['added']} new below.")
                task.state = "done"
            except Exception as exc:
                task.state, task.message = "error", f"The search stopped: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}"
                log.exception("discover failed")

        threading.Thread(target=run, daemon=True, name="discover").start()
        return task.status()

    def tailor_worker():
        while True:
            req, task = work.get()
            task.state, task.message = "running", "Reading the job description…"
            try:
                tailorer(req, task)
                task.state = "done"
            except Exception as exc:
                task.state = "error"
                task.message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                log.warning("tailoring failed: %s", exc)
            finally:
                work.task_done()

    @app.post("/api/tailor")
    def tailor_start(req: TailorRequest):
        if req.row is None and not req.url.strip() and len(req.jd.strip()) < 200:
            raise HTTPException(400, "Paste the job's link, or its full description.")
        if req.row is not None:
            with lock:
                try:
                    lead = Tracker(settings.tracker_path).lead(req.row)
                except KeyError as exc:
                    raise HTTPException(404, "That match isn't on the Leads sheet any more.") from exc
            label = f"{lead['company']}: {lead['title']}"
        else:
            label = " ".join(x for x in (req.company, req.role) if x) or req.url.strip()[:80] or "Pasted job"
        task = new_task("tailor", label)
        work.put((req, task))
        if worker["thread"] is None or not worker["thread"].is_alive():
            worker["thread"] = threading.Thread(target=tailor_worker, daemon=True, name="tailor")
            worker["thread"].start()
        return task.status()

    @app.get("/api/tasks")
    def tasks_status():
        return {"tasks": [t.status() for t in tasks]}

    @app.post("/api/leads/{row}/skip")
    def lead_skip(row: int):
        with lock:
            t = Tracker(settings.tracker_path)
            try:
                t.lead(row)
            except KeyError as exc:
                raise HTTPException(404, "No such match") from exc
            t.set_lead_decision(row, "Skip")
            try:
                t.save()
            except RuntimeError as exc:
                raise HTTPException(423, str(exc)) from exc
        return {"ok": True}

    @app.get("/leads/{row}/jd")
    def lead_jd(row: int):
        from jobhunt.discover import lead_description

        try:
            lead = Tracker(settings.tracker_path).lead(row)
        except KeyError as exc:
            raise HTTPException(404, "Not found") from exc
        text = lead_description(settings.data_dir / "leads", str(lead["key"]))
        if not text:
            raise HTTPException(404, "No saved description for this job")
        head = f"{lead['company']}: {lead['title']}\n{lead['location'] or ''}\n{lead['url'] or ''}\n\n"
        return Response(head + text, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/autofill")
    def autofill_status():
        s = job["session"]
        return s.status() if s else {"ids": [], "state": "idle", "message": "", "submitted": [], "filled": {}}

    @app.post("/api/autofill")
    def autofill_start(req: AutofillRequest):
        if job["thread"] is not None and job["thread"].is_alive():
            raise HTTPException(409, "Autofill is already running. Finish those tabs or close the browser window first.")
        if req.all_ready:
            with lock:
                ids = autofill.ready_ids(settings, req.limit)
        else:
            ids = [i.strip().upper() for i in req.ids if i.strip()]
        jobs = autofill.plan(settings, ids)
        if not jobs:
            raise HTTPException(400, "Nothing to autofill: these jobs have no company apply link "
                                     "(LinkedIn and Naukri jobs are applied to by hand).")
        session = autofill.Session(ids=[j["id"] for j in jobs], auto=req.submit)
        session.message = "Checking these jobs are still open…"

        def submitted(app_id: str) -> None:
            try:
                autofill.mark_submitted(settings, app_id)
            except RuntimeError as exc:
                session.message = f"{app_id} was submitted but not saved: {exc}"

        def work() -> None:
            # A job can close between the daily check and now: ask its board once more first.
            try:
                results = checker(settings, session.ids, lock=lock)
                closed = [r.id for r in results if r.check.state == "Closed"]
            except Exception as exc:
                log.warning("pre-autofill check failed: %s", exc)
                closed = []
            if closed:
                session.ids = [i for i in session.ids if i not in closed]
            if not session.ids:
                session.state = "done"
                session.message = f"Not opened: {', '.join(closed)} closed on the company's job board (moved to Withdrawn)."
                return
            runner(settings, session.ids, session, on_submitted=submitted, lock=lock, auto=req.submit)
            if closed:
                session.message += f" Skipped {', '.join(closed)}: the job closed."

        thread = threading.Thread(target=work, daemon=True, name="autofill")
        job.update(session=session, thread=thread)
        thread.start()
        return session.status()

    @app.get("/applications/{path:path}")
    def application_file(path: str):
        target = (files_root / path).resolve()
        if not target.is_relative_to(files_root) or not target.is_file() or target.suffix.lower() not in _TYPES:
            raise HTTPException(404, "Not found")
        return FileResponse(target, media_type=_TYPES[target.suffix.lower()], filename=target.name,
                            content_disposition_type="inline", headers={"Cache-Control": "no-store"})

    return app


def tailor_job(settings: JobhuntSettings, req: TailorRequest, task: Task, lock: threading.Lock) -> None:
    """Get the JD (saved with the lead, fetched from the link, or pasted), tailor a resume to it and
    add it to Ready to apply. Claude does the tailoring when ANTHROPIC_API_KEY is set; otherwise it's
    keyword-based."""
    from jobhunt.apply import prepare_application
    from jobhunt.discover import lead_description
    from jobhunt.fetch_jd import fetch_job
    from jobhunt.locations import classify

    job = {"company": req.company.strip(), "role": req.role.strip(), "location": "", "jd": req.jd.strip(),
           "posted": None, "url": req.url.strip(), "source": "Company site"}
    if req.row is not None:
        with lock:
            lead = Tracker(settings.tracker_path).lead(req.row)
        job.update(company=lead["company"], role=lead["title"], location=lead["location"] or "", url=lead["url"] or "",
                   jd=lead_description(settings.data_dir / "leads", str(lead["key"])), source=str(lead["source"] or "").title(),
                   posted=lead["posted"].date() if hasattr(lead["posted"], "date") else lead["posted"])
        if not job["jd"]:
            got = fetch_job(job["url"], str(lead["key"] or ""))
            job["jd"] = got["jd"]
    elif job["url"] and len(job["jd"]) < 200:
        got = fetch_job(job["url"])
        job.update(jd=got["jd"], posted=got["posted"], location=got["location"],
                   company=job["company"] or got["company"], role=job["role"] or got["role"])
    if not job["company"] or not job["role"]:
        raise ValueError("Add the company name and the job title, then try again.")
    where = classify(job["location"], job["jd"], settings.preferred_cities, settings.remote_abroad) if job["location"] else None
    task.label = f"{job['company']}: {job['role']}"
    task.message = ("Tailoring your resume to the JD with Claude…" if settings.anthropic_api_key
                    else "Tailoring your resume to the JD (keyword matching; add ANTHROPIC_API_KEY for Claude)…")
    out = prepare_application(settings, jd=job["jd"], company=job["company"], role=job["role"], url=job["url"],
                              location=job["location"], source=job["source"], offline=not settings.anthropic_api_key,
                              lock=lock)
    with lock:
        t = Tracker(settings.tracker_path)
        values = {"posting": "Open", "checked": date.today()}
        if job["posted"]:
            values["posted"] = job["posted"]
        t.update(out["id"], **values)
        if req.row is not None:
            t.set_lead_decision(req.row, "Apply")
        t.save()
    fit = out["result"].tailoring.fit
    task.app_id = out["id"]
    task.message = (f"{out['id']} is in Ready to apply: fit {fit.score}/100, JD keyword match "
                    f"{out['result'].keyword_coverage['coverage_pct']}%"
                    + (f", {where.label}" if where and where.ok else "") + ".")


def _needs_check(settings: JobhuntSettings) -> bool:
    """True when a Ready job hasn't been checked today."""
    try:
        today = date.today()
        for a in Tracker(settings.tracker_path).applications():
            if a["status"] in verify.READY:
                checked = a["checked"].date() if hasattr(a["checked"], "date") else a["checked"]
                if checked != today:
                    return True
    except Exception as exc:
        log.warning("couldn't read the tracker: %s", exc)
    return False


def _free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port between {start} and {start + 19}")


def serve(settings: JobhuntSettings, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    import webbrowser

    import uvicorn

    if not settings.tracker_path.exists():
        print(f"No tracker at {settings.tracker_path} yet: starting with an empty one. "
              "If your data is in a zip, unzip it into jobhunt/data/ and restart.")
    port = _free_port(port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Dashboard: {url}\nKeep this window open while you use it. Ctrl+C stops it.")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(settings, port=port, check_on_start=True), host="127.0.0.1", port=port, log_level="warning")
