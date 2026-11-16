"""Draw the 50 posts to hand-check and write a blind labelling sheet.

    python -m eval.sample                  # 50 posts, seed 42
    python -m eval.sample --n 50 --seed 7

Writes:
  eval/data/eval50_labels.xlsx       <- you fill this in (model output is NOT in it, so you aren't anchored)
  eval/data/eval50_predictions.csv   <- the model's tags at sampling time, frozen for scoring

Sampling is stratified by source so every source is represented roughly in proportion.
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from core import db
from core.taxonomy import CITY_VALUES, ROLES

DATA = Path(__file__).parent / "data"
LABELS_XLSX = DATA / "eval50_labels.xlsx"
PREDICTIONS_CSV = DATA / "eval50_predictions.csv"

PRED_FIELDS = ["job_id", "model", "prompt_version", "role", "cities", "other_city", "is_fresher", "job_type", "work_mode", "confidence"]


def stratified(rows: list[dict], n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_source[r["source"]].append(r)
    for group in by_source.values():
        rng.shuffle(group)
    total = len(rows)
    # proportional allocation, at least 1 per source
    quota = {s: max(1, round(n * len(g) / total)) for s, g in by_source.items()}
    while sum(quota.values()) > n:
        biggest = max(quota, key=quota.get)
        quota[biggest] -= 1
    while sum(quota.values()) < n:
        roomiest = max(by_source, key=lambda s: len(by_source[s]) - quota[s])
        quota[roomiest] += 1
    picked = [r for s, g in by_source.items() for r in g[: quota[s]]]
    rng.shuffle(picked)
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with db.connect() as conn:
        rows = conn.execute(
            """
            select id, source, title, company, location_raw, url, description, tags, tag_model, tag_prompt_version
            from jobs
            where is_active and duplicate_of is null and tags is not null and (tags->>'is_fresher')::boolean
            order by id
            """
        ).fetchall()
    if len(rows) < args.n:
        raise SystemExit(f"only {len(rows)} tagged posts available, need {args.n}")

    sample = stratified(rows, args.n, args.seed)
    DATA.mkdir(parents=True, exist_ok=True)

    with PREDICTIONS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PRED_FIELDS)
        w.writeheader()
        for r in sample:
            t = r["tags"]
            w.writerow({
                "job_id": r["id"], "model": r["tag_model"], "prompt_version": r["tag_prompt_version"],
                "role": t["role"], "cities": ";".join(t["cities"]), "other_city": t.get("other_city") or "",
                "is_fresher": t["is_fresher"], "job_type": t["job_type"], "work_mode": t["work_mode"],
                "confidence": t["confidence"],
            })

    wb = Workbook()
    ws = wb.active
    ws.title = "Label"
    headers = ["job_id", "title", "company", "location", "description (first 700 chars)", "url",
               "YOUR role", "YOUR cities (separate with ;)", "YOUR is_fresher", "notes"]
    ws.append(headers)
    for r in sample:
        ws.append([r["id"], r["title"], r["company"], r["location_raw"], (r["description"] or "")[:700], r["url"],
                   None, None, None, None])

    bold, fill = Font(bold=True), PatternFill("solid", fgColor="FFF4CC")
    for col, width in zip("ABCDEFGHIJ", (8, 34, 18, 22, 70, 18, 26, 26, 12, 30)):
        ws.column_dimensions[col].width = width
    for cell in ws[1]:
        cell.font = bold
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        for cell in row[6:9]:
            cell.fill = fill
    ws.freeze_panes = "B2"

    tax = wb.create_sheet("Taxonomy")
    tax.append(["role", "what belongs in it"])
    for name, desc in ROLES.items():
        tax.append([name, desc])
    tax.append([])
    tax.append(["cities (use these spellings)"])
    for c in CITY_VALUES:
        tax.append([c])
    tax.column_dimensions["A"].width = 30
    tax.column_dimensions["B"].width = 90

    last = len(sample) + 1
    role_dv = DataValidation(type="list", formula1=f"=Taxonomy!$A$2:$A${len(ROLES) + 1}", allow_blank=True)
    bool_dv = DataValidation(type="list", formula1='"TRUE,FALSE"', allow_blank=True)
    ws.add_data_validation(role_dv)
    ws.add_data_validation(bool_dv)
    role_dv.add(f"G2:G{last}")
    bool_dv.add(f"I2:I{last}")

    guide = wb.create_sheet("How to label", 0)
    for line in [
        "Label each post yourself BEFORE looking at what the model said (its answers aren't in this file).",
        "1. Open the url if the description is too short to judge.",
        "2. YOUR role: pick from the dropdown (definitions on the Taxonomy tab).",
        "3. YOUR cities: every city the job can be done from, separated by ';'. Use Remote / Pan India / Unspecified when that's the truth.",
        "   Bangalore -> Bengaluru, Gurgaon -> Gurugram are normalized automatically, so spelling slips are fine.",
        "4. YOUR is_fresher: TRUE if someone with 0-1 years of experience can apply.",
        "5. notes: anything ambiguous. Ambiguous cases are useful for the write-up.",
        "Then: python -m eval.score",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 130

    wb.save(LABELS_XLSX)
    by_src = defaultdict(int)
    for r in sample:
        by_src[r["source"]] += 1
    print(f"sampled {len(sample)} of {len(rows)} posts: {dict(by_src)}")
    print(f"label this: {LABELS_XLSX}")


if __name__ == "__main__":
    main()
