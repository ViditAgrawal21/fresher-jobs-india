"""Daily pipeline: fetch -> normalize -> upsert -> dedup -> tag new -> expire stale.

    python -m pipeline.run                     # full run
    python -m pipeline.run --dry-run           # fetch + normalize only, no DB writes
    python -m pipeline.run --sources greenhouse,lever
    python -m pipeline.run --retag             # drop all tags and re-tag (after a prompt change)

Safe to re-run: posts are upserted on (source, source_id) and only untagged posts
hit the LLM, so a second run on the same day costs ~nothing.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from core import db
from core.config import get_settings
from core.models import RawPost
from pipeline import dedup
from pipeline.filters import is_placeholder_post
from pipeline.normalize import to_row
from pipeline.sources.aggregators import AdzunaSource, JoobleSource
from pipeline.sources.base import Source, make_client
from pipeline.sources.company_boards import AshbySource, GreenhouseSource, LeverSource, SmartRecruitersSource
from pipeline.tagger import Tagger

log = logging.getLogger("pipeline")

ALL_SOURCES = ["adzuna", "jooble", "greenhouse", "lever", "ashby", "smartrecruiters"]
# Jooble is off by default: its job links are blocked by Cloudflare for visitors coming from
# its API, and it passed off US jobs as India (build log #10). Still runnable with --sources jooble.
DEFAULT_SOURCES = [s for s in ALL_SOURCES if s != "jooble"]


def build_sources(names: list[str], client, known_sr_ids: set[str] | None = None) -> list[Source]:
    factories = {
        "adzuna": lambda: AdzunaSource(client),
        "jooble": lambda: JoobleSource(client),
        "greenhouse": lambda: GreenhouseSource(client),
        "lever": lambda: LeverSource(client),
        "ashby": lambda: AshbySource(client),
        "smartrecruiters": lambda: SmartRecruitersSource(client, known_ids=known_sr_ids),
    }
    return [factories[n]() for n in names]


def _fetch_one(source: Source) -> tuple[Source, list[RawPost] | None, Exception | None, float]:
    started = time.monotonic()
    try:
        return source, source.fetch(), None, time.monotonic() - started
    except Exception as exc:
        log.exception("%s failed", source.name)
        return source, None, exc, time.monotonic() - started


def fetch_all(sources: list[Source]) -> tuple[list[RawPost], dict, list, list[str]]:
    """Sources hit different hosts, so they run in parallel; each keeps its own politeness delay.
    Run time is the slowest source, not the sum (Lever alone can take minutes)."""
    posts, stats, errors, healthy = [], {}, [], []
    for source in sources:
        if not source.enabled:
            log.warning("%s: disabled (missing API key), skipping", source.name)
            stats[f"disabled.{source.name}"] = True
    sources = [s for s in sources if s.enabled]
    with ThreadPoolExecutor(max_workers=max(len(sources), 1)) as pool:
        for source, got, exc, secs in pool.map(_fetch_one, sources):
            if exc is not None:
                errors.append({"source": source.name, "error": f"{type(exc).__name__}: {exc}"[:500]})
                continue
            posts.extend(got)
            healthy.append(source.name)
            if getattr(source, "failed", None):
                errors.append({"source": source.name, "error": "boards failed: " + ", ".join(source.failed)})
            stats[f"fetched.{source.name}"] = len(got)
            stats[f"seconds.{source.name}"] = round(secs, 1)
            log.info("%s: %d posts in %.1fs", source.name, len(got), secs)
    return posts, stats, errors, healthy


def run(source_names: list[str], dry_run: bool = False, retag: bool = False, tag_limit: int | None = None) -> int:
    settings = get_settings()
    http = make_client()

    if dry_run:
        posts, stats, errors, _ = fetch_all(build_sources(source_names, http))
        rows = [to_row(p) for p in posts]
        print(f"fetched {len(rows)} posts: {dict(Counter(r['source'] for r in rows))}")
        for r in rows[:10]:
            print(f"  [{r['source']}] {r['title']} | {r['company']} | {r['location_raw']}")
        if errors:
            print("errors:", errors)
        return 0

    # Short-lived connections per phase. Fetching can take minutes, and Neon drops idle
    # connections when the compute scales to zero -- holding one open across the fetch
    # is what killed run #1 (see docs/BUILD_LOG.md).
    db.migrate()  # idempotent (create ... if not exists), so a fresh deploy needs no separate step
    with db.connect() as conn:
        orphaned = db.close_orphaned_runs(conn)
        run_id = db.start_run(conn)
        known_sr = db.known_source_ids(conn, "smartrecruiters") if "smartrecruiters" in source_names else set()
    if orphaned:
        log.warning("marked %d unfinished earlier run(s) as failed", orphaned)
    log.info("run %d started", run_id)

    posts, stats, errors, healthy = fetch_all(build_sources(source_names, http, known_sr))
    real = [p for p in posts if not is_placeholder_post(p.title, p.company)]
    stats["dropped.placeholder"] = len(posts) - len(real)
    posts = real

    try:
        with db.connect() as conn:
            # A source can return the same id twice (e.g. one job matching two queries).
            rows = list({(r["source"], r["source_id"]): r for r in map(to_row, posts)}.values())
            stats["new"] = db.upsert_jobs(conn, rows, run_id)

            active = db.active_jobs_for_dedup(conn)
            groups = dedup.find_duplicates(active)
            stats["dedup_updates"] = db.set_duplicates(conn, dedup.changed(active, groups))
            stats["active"] = len(active)
            stats["duplicates"] = sum(1 for v in groups.values() if v is not None)

            if retag:
                log.info("cleared tags on %d posts", db.clear_tags(conn))
            pending = db.jobs_to_tag(conn, limit=tag_limit)

        stats["tagged"], stats["llm_cost_usd"] = 0, 0.0
        if pending and not settings.anthropic_api_key:
            log.warning("ANTHROPIC_API_KEY not set: %d posts left untagged", len(pending))
            errors.append({"source": "tagger", "error": "ANTHROPIC_API_KEY not set"})
        elif pending:
            tagger = Tagger()

            def save_batch(tags, call):
                # Persist each batch as soon as it returns (short connection, with retries).
                with db.connect() as conn:
                    db.save_tags(conn, [(i, t.model_dump(exclude={"id"})) for i, t in tags.items()],
                                 tagger.model, tagger.prompt_version)
                    db.log_llm_call(conn, run_id=run_id, purpose="tagging", **vars(call))

            result = tagger.tag(pending, on_batch=save_batch)  # no DB connection held while waiting
            stats["tagged"] = len(result.tags)
            stats["tag_failed"] = len(pending) - len(result.tags)
            stats["llm_cost_usd"] = round(result.cost, 6)
            if stats["tag_failed"]:
                errors.append({"source": "tagger", "error": f"{stats['tag_failed']} posts left untagged"})

        with db.connect() as conn:
            stats["expired"] = db.expire_stale(conn, healthy, settings.stale_after_days, settings.max_post_age_days)
            status = "ok" if not errors else ("partial" if healthy else "failed")
            db.finish_run(conn, run_id, status, stats, errors)
    except Exception as exc:
        log.exception("run %d crashed", run_id)
        errors.append({"source": "pipeline", "error": f"{type(exc).__name__}: {exc}"[:500]})
        with db.connect() as conn:  # fresh connection: the old one may be the thing that died
            db.finish_run(conn, run_id, "failed", stats, errors)
        return 1

    log.info("run %d %s: %s", run_id, status, stats)
    return 0 if status != "failed" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", default=",".join(DEFAULT_SOURCES))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retag", action="store_true")
    parser.add_argument("--tag-limit", type=int, default=None, help="tag at most N posts (for testing)")
    args = parser.parse_args()

    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    names = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(names) - set(ALL_SOURCES)
    if unknown:
        parser.error(f"unknown sources: {', '.join(sorted(unknown))}")
    sys.exit(run(names, dry_run=args.dry_run, retag=args.retag, tag_limit=args.tag_limit))


if __name__ == "__main__":
    main()
