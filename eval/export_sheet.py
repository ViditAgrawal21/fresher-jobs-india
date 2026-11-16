"""Build the working sheet for the submission (upload the .xlsx to Google Sheets).

    python -m eval.export_sheet

Tabs: Read me | Jobs (live) | All collected | Eval 50 | Accuracy | Cost | Runs | Prompts | AI tools | Sources
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from core import db
from eval.sample import LABELS_XLSX, PREDICTIONS_CSV
from eval.score import RESULTS, load_labels
from pipeline.tagger import PRICES, PROMPT_DIR, load_prompt

OUT = RESULTS / "working_sheet.xlsx"

AI_TOOLS = [
    ("Claude Haiku 5.5 (claude-haiku-5-5)", "Production tagger: role, cities, work mode, job type, is_fresher for every new post",
     "Structured outputs (JSON schema from Pydantic), 20 posts per request, effort=low. Prompt: Prompts tab."),
    ("Claude Sonnet 5.5 (claude-sonnet-5-5)", "Comparison model on the same 50 hand-labelled posts",
     "Same prompt and schema; used to decide whether the 20x price buys real accuracy."),
    ("Claude Code", "Pair-programming: scaffolding, tests, deploy script, docs drafts",
     "Architecture, source choices (ToS), dedup rules, taxonomy, eval method and every fix in the build log were my decisions and were reviewed by me."),
    ("Neon CLI", "Created the Postgres project and wrote connection strings to .env.local", ""),
]

SOURCES = [
    ("Adzuna India", "Official REST API (free key)", "Listings may be published with Adzuna credit + link; we show 'Jobs by Adzuna' and link via their redirect URL", "Main volume"),
    ("Jooble (dropped)", "Official REST API (key on request)", "API is provided for this purpose", "Dropped: links blocked by Cloudflare for visitors, and US jobs labelled India"),
    ("Greenhouse boards", "Public Job Board API (boards-api.greenhouse.io)", "Public, documented, no auth; made for companies to publish jobs", "14 companies"),
    ("Lever boards", "Public Postings API (api.lever.co/v0/postings)", "Public, documented, no auth", "3 companies"),
    ("Ashby boards", "Public Posting API (api.ashbyhq.com/posting-api)", "Public, documented, no auth", "4 companies"),
    ("SmartRecruiters", "Public Posting API (api.smartrecruiters.com/v1)", "Public, documented, no auth", "6 companies"),
    ("LinkedIn, Naukri, Indeed, Internshala, Glassdoor, foundit, Unstop", "Not used", "Terms prohibit automated collection", "Deliberately excluded"),
]


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list], widths: list[int] | None = None):
    ws = wb.create_sheet(title)
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths or [], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    return ws


def main() -> None:
    with db.connect() as conn:
        live = conn.execute(
            """
            select id, title, company, tags->>'role' role, tags->'cities' cities, tags->>'job_type' job_type,
                   tags->>'work_mode' work_mode, source, url, coalesce(posted_at, first_seen_at)::date posted
            from jobs j
            where is_active and duplicate_of is null and tags is not null
              and coalesce((tags->>'in_india')::boolean, true) and (tags->>'is_fresher')::boolean
            order by posted desc, id desc
            """
        ).fetchall()
        collected = conn.execute(
            """
            select id, source, title, company, location_raw, url, duplicate_of, tags, first_seen_at::date first_seen
            from jobs where is_active order by id
            """
        ).fetchall()
        cost = conn.execute(
            """
            select purpose, model, prompt_version, count(*) calls, sum(n_posts) posts,
                   sum(input_tokens) input_tokens, sum(output_tokens) output_tokens, sum(cost_usd) cost_usd
            from llm_calls where ok group by 1, 2, 3 order by 1, 2, 3
            """
        ).fetchall()
        runs = conn.execute("select id, started_at, finished_at, status, stats, errors from runs order by id").fetchall()

    wb = Workbook()
    readme = wb.active
    readme.title = "Read me"
    for line in [
        "Fresher Jobs India: working sheet",
        "Live site: https://beep.viditagrawal.in   Repo: https://github.com/ViditAgrawal21/fresher-jobs-india",
        "",
        "Jobs (live): what the site shows (active, not a duplicate, tagged, open to freshers)",
        "All collected: every active post with why it is or isn't shown",
        "Eval 50: my hand labels vs the model's tags",
        "Accuracy: role / city accuracy with 95% intervals, plus Haiku vs Sonnet",
        "Cost: real token counts from every LLM call, and cost per 1,000 posts",
        "Runs: every pipeline run (daily at 06:00 IST)",
        "Prompts: the exact prompts used",
        "AI tools: what each AI tool did",
        "Sources: where data comes from and why it's allowed",
    ]:
        readme.append([line])
    readme["A1"].font = Font(bold=True, size=14)
    readme.column_dimensions["A"].width = 110

    _sheet(wb, "Jobs (live)", ["id", "title", "company", "role", "cities", "job type", "work mode", "source", "posted", "url"],
           [[r["id"], r["title"], r["company"], r["role"], ", ".join(r["cities"]), r["job_type"], r["work_mode"],
             r["source"], r["posted"], r["url"]] for r in live],
           [7, 45, 22, 26, 26, 13, 11, 15, 11, 50])

    def status(r):
        if r["duplicate_of"]:
            return f"duplicate of #{r['duplicate_of']}"
        if r["tags"] is None:
            return "not tagged yet"
        if r["tags"].get("in_india") is False:
            return "hidden: outside India"
        return "shown" if r["tags"]["is_fresher"] else "hidden: not for freshers"

    _sheet(wb, "All collected", ["id", "status", "source", "title", "company", "location (raw)", "role", "cities", "first seen", "url"],
           [[r["id"], status(r), r["source"], r["title"], r["company"], r["location_raw"],
             (r["tags"] or {}).get("role"), ", ".join((r["tags"] or {}).get("cities", [])), r["first_seen"], r["url"]]
            for r in collected],
           [7, 22, 15, 45, 22, 26, 26, 26, 11, 50])

    if LABELS_XLSX.exists() and PREDICTIONS_CSV.exists():
        labels = load_labels(LABELS_XLSX)
        with PREDICTIONS_CSV.open(encoding="utf-8") as f:
            preds = {int(r["job_id"]): r for r in csv.DictReader(f)}
        rows = []
        for job_id, l in labels.items():
            p = preds.get(job_id, {})
            p_cities = set(filter(None, p.get("cities", "").split(";")))
            rows.append([job_id, l["title"], l["role"], p.get("role"), "✓" if l["role"] == p.get("role") else "✗",
                         "; ".join(sorted(l["cities"])), "; ".join(sorted(p_cities)), "✓" if set(l["cities"]) == p_cities else "✗",
                         l["is_fresher"], p.get("is_fresher"), l["notes"]])
        _sheet(wb, "Eval 50", ["job_id", "title", "my role", "model role", "role ok", "my cities", "model cities", "city ok",
                               "my is_fresher", "model is_fresher", "notes"], rows, [8, 40, 24, 24, 8, 24, 24, 8, 12, 14, 40])

    summary_path = RESULTS / "summary.json"
    if summary_path.exists():
        s = json.loads(summary_path.read_text(encoding="utf-8"))
        rows = [[s["model"], s["prompt_version"], s["n"], s["role_accuracy"], f"{s['role_ci'][0]}–{s['role_ci'][1]}",
                 s["city_accuracy"], f"{s['city_ci'][0]}–{s['city_ci'][1]}", s["both_accuracy"], s.get("fresher_precision"), "production tags"]]
        for c in s.get("compare", []):
            rows.append([c["model"], c["prompt_version"], c["n"], c["role_accuracy"], f"{c['role_ci'][0]}–{c['role_ci'][1]}",
                         c["city_accuracy"], f"{c['city_ci'][0]}–{c['city_ci'][1]}", c["both_accuracy"], c.get("fresher_precision"),
                         f"re-tagged, ${c['cost_per_1000_usd']} per 1,000 posts"])
        _sheet(wb, "Accuracy", ["model", "prompt", "n", "role acc", "role 95% CI", "city acc", "city 95% CI", "both", "fresher precision", "note"],
               rows, [22, 8, 5, 9, 13, 9, 13, 8, 15, 36])

    cost_rows = []
    for r in cost:
        per_1000 = float(r["cost_usd"]) / r["posts"] * 1000 if r["posts"] else None
        cost_rows.append([r["purpose"], r["model"], r["prompt_version"], r["calls"], r["posts"], r["input_tokens"],
                          r["output_tokens"], round(float(r["cost_usd"]), 6), round(per_1000, 4) if per_1000 else None])
    ws = _sheet(wb, "Cost", ["purpose", "model", "prompt", "calls", "posts", "input tokens", "output tokens", "cost (USD)", "USD per 1,000 posts"],
                cost_rows, [10, 22, 8, 7, 7, 13, 13, 11, 18])
    ws.append([])
    ws.append(["Formula: (input_tokens x input price + output_tokens x output price) / 1,000,000, summed over every call, divided by posts tagged."])
    for model, (pin, pout) in PRICES.items():
        ws.append([f"{model}: ${pin} / 1M input, ${pout} / 1M output"])
    ws.append(["Infra: Cloud Run job + service, Cloud Scheduler and Neon all sit inside free tiers at this volume, so about $0/month."])

    _sheet(wb, "Runs", ["run", "started (UTC)", "finished (UTC)", "status", "stats", "errors"],
           [[r["id"], r["started_at"].replace(tzinfo=None), r["finished_at"].replace(tzinfo=None) if r["finished_at"] else None,
             r["status"], json.dumps(r["stats"]), json.dumps(r["errors"])] for r in runs],
           [6, 20, 20, 9, 90, 60])

    ws = _sheet(wb, "Prompts", ["version", "prompt (exactly as sent; the posts follow in the user message)"],
                [[p.stem.replace("tagger_", ""), load_prompt(p.stem.replace("tagger_", ""))] for p in sorted(PROMPT_DIR.glob("tagger_*.md"))],
                [10, 150])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    _sheet(wb, "AI tools", ["tool", "used for", "notes"], [list(r) for r in AI_TOOLS], [34, 60, 90])
    _sheet(wb, "Sources", ["source", "access", "terms", "use"], [list(r) for r in SOURCES], [34, 44, 70, 24])

    RESULTS.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"wrote {OUT} ({len(live)} live jobs, {len(collected)} collected)")


if __name__ == "__main__":
    main()
