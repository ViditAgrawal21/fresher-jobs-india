"""`python -m jobhunt serve`: the dashboard on http://127.0.0.1:8765, wired to your files.

Unlike the static dashboard.html, this page:
- saves every board move straight into tracker.xlsx;
- opens each tailored resume, report and job description;
- runs autofill, either for one job (the Autofill button on a Ready card) or for the top N
  ready jobs. A browser window opens with one tab per job and the form filled in. You check
  each form and click Submit yourself; a thank-you page marks the job Submitted.

The server listens on 127.0.0.1 only and refuses requests from other sites, so nothing on
your network or in another browser tab can read or change the tracker.
"""

from __future__ import annotations

import logging
import socket
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from jobhunt import autofill
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


def create_app(settings: JobhuntSettings, *, runner=None, port: int = DEFAULT_PORT) -> FastAPI:
    """`runner` replaces autofill.run in tests."""
    app = FastAPI(title="jobhunt", docs_url=None, redoc_url=None, openapi_url=None)
    lock = threading.Lock()  # one reader/writer of tracker.xlsx at a time
    job: dict = {"session": None, "thread": None}
    runner = runner or autofill.run
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

        def submitted(app_id: str) -> None:
            try:
                autofill.mark_submitted(settings, app_id)
            except RuntimeError as exc:
                session.message = f"{app_id} was submitted but not saved: {exc}"

        thread = threading.Thread(target=runner, args=(settings, session.ids, session),
                                  kwargs={"on_submitted": submitted, "lock": lock}, daemon=True, name="autofill")
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
    uvicorn.run(create_app(settings, port=port), host="127.0.0.1", port=port, log_level="warning")
