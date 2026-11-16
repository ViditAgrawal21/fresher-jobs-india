from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from api import queries
from core.config import get_settings

log = logging.getLogger("api")

ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = ROOT / "web" / "dist"
EVAL_SUMMARY = ROOT / "eval" / "results" / "summary.json"

pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = ConnectionPool(
        get_settings().database_url,
        min_size=1,
        max_size=4,
        # Neon's pooled endpoint is PgBouncer in transaction mode -> no server-side prepared statements.
        kwargs={"row_factory": dict_row, "autocommit": True, "prepare_threshold": None},
        open=True,
    )
    yield
    pool.close()


app = FastAPI(title="Fresher Jobs India", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


def _fetch(sql: str, params: dict | None = None) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(sql, params or {}).fetchall()


@app.get("/healthz")
def healthz():
    _fetch("select 1")
    return {"ok": True}


@app.get("/api/jobs")
def search_jobs(
    response: Response,
    q: Annotated[str | None, Query(max_length=100)] = None,
    role: Annotated[list[str] | None, Query()] = None,
    city: Annotated[list[str] | None, Query()] = None,
    job_type: Annotated[list[str] | None, Query()] = None,
    days: Annotated[int | None, Query(ge=1, le=60)] = None,
    sort: Literal["relevance", "newest"] | None = None,
    page: Annotated[int, Query(ge=1, le=100)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    q = (q or "").strip() or None
    params = {
        "q": q,
        "q_like": f"%{q}%" if q else None,
        "roles": role or None,
        "cities": city or None,
        "types": job_type or None,
        "days": days,
        "sort": sort or ("relevance" if q else "newest"),
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    rows = _fetch(queries.SEARCH_JOBS, params)
    total = rows[0]["total"] if rows else 0
    for r in rows:
        r.pop("total", None)
    response.headers["Cache-Control"] = "public, max-age=60"
    return {"total": total, "page": page, "page_size": page_size, "items": rows}


@app.get("/api/facets")
def facets(response: Response):
    out: dict[str, list[dict]] = {"role": [], "city": [], "job_type": [], "source": []}
    for r in _fetch(queries.FACETS):
        out[r["facet"]].append({"value": r["value"], "count": r["n"]})
    response.headers["Cache-Control"] = "public, max-age=300"
    return out


@app.get("/api/stats")
def stats(response: Response):
    totals = _fetch(queries.TOTALS)[0]
    last = _fetch(queries.LAST_GOOD_RUN)
    runs = _fetch(queries.RECENT_RUNS, {"limit": 14})

    cost_rows = _fetch(queries.TAGGING_COST)
    posts = sum(r["posts"] or 0 for r in cost_rows)
    cost = float(sum(r["cost_usd"] or 0 for r in cost_rows))
    cost_block = {
        "total_usd": round(cost, 4),
        "posts_tagged": posts,
        "per_1000_posts_usd": round(cost / posts * 1000, 4) if posts else None,
        "by_model": [{**r, "cost_usd": float(r["cost_usd"] or 0)} for r in cost_rows],
    }

    eval_summary = json.loads(EVAL_SUMMARY.read_text(encoding="utf-8")) if EVAL_SUMMARY.exists() else None

    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "totals": totals,
        "last_refresh": last[0] if last else None,
        "runs": runs,
        "cost": cost_block,
        "eval": eval_summary,
    }


# ---- frontend ----
# In production the React app is on Vercel, which proxies /api/* here. Locally, after
# `npm run build`, this same app serves web/dist so everything runs on one port.
if not WEB_DIST.exists():

    @app.get("/", include_in_schema=False)
    def root():
        return {"service": "fresher-jobs-api", "site": "https://beep.viditagrawal.in", "docs": "/api/docs"}

else:
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        file = WEB_DIST / path
        if path and file.is_file() and WEB_DIST in file.resolve().parents:
            return FileResponse(file)
        return FileResponse(WEB_DIST / "index.html")
