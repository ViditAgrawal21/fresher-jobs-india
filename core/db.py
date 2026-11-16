from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.config import get_settings

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


log = logging.getLogger(__name__)


def connect(url: str | None = None, attempts: int = 4) -> psycopg.Connection:
    """Open a connection, retrying transient failures (DNS blip, Neon waking from scale-to-zero).
    A failed connect after the LLM step once threw away a whole run's tags (build log #7)."""
    url = url or get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    for attempt in range(1, attempts + 1):
        try:
            # prepare_threshold=None: Neon's pooled endpoint is PgBouncer (transaction mode).
            return psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=15, prepare_threshold=None)
        except psycopg.OperationalError as exc:
            if attempt == attempts:
                raise
            wait = 2 ** attempt
            log.warning("db connect failed (attempt %d/%d), retrying in %ds: %s", attempt, attempts, wait, str(exc).strip()[:120])
            time.sleep(wait)
    raise AssertionError("unreachable")


def migrate() -> None:
    with connect(get_settings().migrations_url) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


# ---------- runs ----------

def close_orphaned_runs(conn: psycopg.Connection) -> int:
    """A run whose process died never gets finish_run(). Close those out so the
    stats page doesn't show a run "in progress" forever."""
    return conn.execute(
        """
        update runs
        set status = 'failed', finished_at = now(),
            errors = errors || '[{"source": "pipeline", "error": "run never finished (process died)"}]'::jsonb
        where status = 'running' and started_at < now() - interval '1 hour'
        """
    ).rowcount


def start_run(conn: psycopg.Connection) -> int:
    return conn.execute("insert into runs default values returning id").fetchone()["id"]


def finish_run(conn: psycopg.Connection, run_id: int, status: str, stats: dict, errors: list) -> None:
    conn.execute(
        "update runs set finished_at = now(), status = %s, stats = %s, errors = %s where id = %s",
        (status, Jsonb(stats), Jsonb(errors), run_id),
    )


# ---------- jobs ----------

def known_source_ids(conn: psycopg.Connection, source: str) -> set[str]:
    rows = conn.execute("select source_id from jobs where source = %s", (source,)).fetchall()
    return {r["source_id"] for r in rows}


_UPSERT = """
insert into jobs (source, source_id, url, canonical_url, title, company, company_norm, location_raw,
                  description, posted_at, last_seen_run, content_hash, dedup_key)
values (%(source)s, %(source_id)s, %(url)s, %(canonical_url)s, %(title)s, %(company)s, %(company_norm)s,
        %(location_raw)s, coalesce(%(description)s::text, ''), %(posted_at)s, %(run_id)s,
        coalesce(%(content_hash)s::text, ''), %(dedup_key)s)
on conflict (source, source_id) do update set
    last_seen_at  = now(),
    last_seen_run = excluded.last_seen_run,
    is_active     = true,
    url           = excluded.url,
    canonical_url = excluded.canonical_url,
    title         = excluded.title,
    company       = excluded.company,
    company_norm  = excluded.company_norm,
    location_raw  = excluded.location_raw,
    dedup_key     = excluded.dedup_key,
    posted_at     = coalesce(excluded.posted_at, jobs.posted_at),
    description   = case when %(has_details)s then excluded.description else jobs.description end,
    content_hash  = case when %(has_details)s then excluded.content_hash else jobs.content_hash end,
    -- post text changed since we tagged it -> drop tags so it gets re-tagged
    tags          = case when not %(has_details)s or jobs.content_hash = excluded.content_hash
                         then jobs.tags else null end
returning (xmax = 0) as inserted
"""


def upsert_jobs(conn: psycopg.Connection, rows: list[dict], run_id: int) -> int:
    """Insert or refresh jobs. Returns how many were new."""
    if not rows:
        return 0
    params = [{**r, "run_id": run_id, "has_details": r["description"] is not None} for r in rows]
    inserted = 0
    with conn.cursor() as cur:
        cur.executemany(_UPSERT, params, returning=True)
        while True:
            row = cur.fetchone()
            inserted += bool(row and row["inserted"])
            if not cur.nextset():
                break
    return inserted


def active_jobs_for_dedup(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """
        select id, source, canonical_url, company_norm, title, location_raw,
               length(description) as desc_len, first_seen_at, duplicate_of
        from jobs where is_active
        """
    ).fetchall()


def set_duplicates(conn: psycopg.Connection, changes: Iterable[tuple[int, int | None]]) -> int:
    changes = list(changes)
    if changes:
        with conn.cursor() as cur:
            cur.executemany("update jobs set duplicate_of = %s where id = %s", [(dup, i) for i, dup in changes])
    return len(changes)


def jobs_to_tag(conn: psycopg.Connection, limit: int | None = None) -> list[dict]:
    sql = """
        select id, title, company, location_raw, description
        from jobs
        where is_active and duplicate_of is null and tags is null
        order by id
    """
    if limit:
        sql += " limit %s"
        return conn.execute(sql, (limit,)).fetchall()
    return conn.execute(sql).fetchall()


def save_tags(conn: psycopg.Connection, items: list[tuple[int, dict]], model: str, prompt_version: str) -> None:
    if not items:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """update jobs set tags = %s, tag_model = %s, tag_prompt_version = %s, tagged_at = now()
               where id = %s""",
            [(Jsonb(tags), model, prompt_version, job_id) for job_id, tags in items],
        )


def clear_tags(conn: psycopg.Connection) -> int:
    return conn.execute("update jobs set tags = null where is_active").rowcount


# Aggregators keep listing dead jobs, so for them an old post date means "probably filled".
# Company boards remove a job when it closes -- if it's still listed, it's open, however old.
AGE_LIMITED_SOURCES = ["adzuna", "jooble"]


def expire_stale(conn: psycopg.Connection, healthy_sources: list[str], stale_days: int, max_age_days: int) -> int:
    """Hide jobs we stopped seeing. Only for sources that ran fine this time, so one
    broken source doesn't wipe its posts off the site."""
    return conn.execute(
        """
        update jobs set is_active = false
        where is_active and (
            (source = any(%s) and last_seen_at < now() - make_interval(days => %s))
            or (source = any(%s) and posted_at < now() - make_interval(days => %s))
        )
        """,
        (healthy_sources, stale_days, AGE_LIMITED_SOURCES, max_age_days),
    ).rowcount


# ---------- llm usage ----------

def log_llm_call(conn: psycopg.Connection, **fields: Any) -> None:
    cols = ", ".join(fields)
    vals = ", ".join(f"%({k})s" for k in fields)
    conn.execute(f"insert into llm_calls ({cols}) values ({vals})", fields)


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
