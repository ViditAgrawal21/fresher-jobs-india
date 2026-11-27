"""`python -m jobhunt serve`: the dashboard on http://127.0.0.1:8765, wired to your files.

Unlike the static dashboard.html, this page:
- saves every board move straight into tracker.xlsx;
- opens each tailored resume, report and job description;
- checks every Ready job is still open on the company's own job board (on start, once a
  day, and from the "Check jobs are still open" button), and skips closed jobs;
- runs autofill, either for one job (the Autofill button on a Ready card) or for the top N
  fresh ready jobs. A browser window opens with one tab per job and the form filled in. You check
  each form and click Submit yourself; a thank-you page marks the job Submitted.

The server listens on 127.0.0.1 only and refuses requests from other sites, so nothing on
your network or in another browser tab can read or change the tracker.
"""

from __future__ import annotations

import logging
import socket
import threading
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


class AutofillRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=10)
    all_ready: bool = False
    limit: int = Field(5, ge=1, le=10)


def create_app(settings: JobhuntSettings, *, runner=None, port: int = DEFAULT_PORT, checker=None,
               check_on_start: bool = False) -> FastAPI:
    """`runner` replaces autofill.run and `checker` verify.verify_applications in tests."""
    app = FastAPI(title="jobhunt", docs_url=None, redoc_url=None, openapi_url=None)
    lock = threading.Lock()  # one reader/writer of tracker.xlsx at a time
    job: dict = {"session": None, "thread": None}
    runner = runner or autofill.run
    checker = checker or verify.verify_applications
    vjob = {"job": verify.VerifyJob(), "thread": None}
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

    if check_on_start and _needs_check(settings):
        start_check()

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
        session = autofill.Session(ids=[j["id"] for j in jobs])
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
            runner(settings, session.ids, session, on_submitted=submitted, lock=lock)
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
