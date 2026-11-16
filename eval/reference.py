"""Draft reference labels for the 50 sampled posts with a stronger model, for a human to review.

    python -m eval.reference

Claude Opus 5.5 labels each post with the same rules (prompt) but at high effort, and it
never sees what Haiku said. The output is a review sheet: one row per post with Opus's
answer and a column to confirm it or write the correct value. Haiku's tags are not in the
sheet, so the reviewer is not anchored by the system being graded.

Writes eval/data/eval50_review.xlsx. Score with:  python -m eval.score --labels eval/data/eval50_review.xlsx
"""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from core import db
from core.taxonomy import CITY_VALUES, ROLES
from eval.sample import DATA, PREDICTIONS_CSV
from pipeline.tagger import Tagger

REVIEW_XLSX = DATA / "eval50_review.xlsx"
REFERENCE_MODEL = "claude-opus-5-5"

# No server-side model fallback here on purpose: if Opus refused a post we want that row
# flagged for manual labelling, not silently answered by a different model.


def main() -> None:
    with PREDICTIONS_CSV.open(encoding="utf-8") as f:
        ids = [int(r["job_id"]) for r in csv.DictReader(f)]

    with db.connect() as conn:
        posts = conn.execute(
            "select id, title, company, location_raw, description, url from jobs where id = any(%s)", (ids,)
        ).fetchall()
    by_id = {p["id"]: p for p in posts}

    tagger = Tagger(model=REFERENCE_MODEL, effort="high", batch_size=10, workers=3, max_tokens=16000)
    result = tagger.tag([by_id[i] for i in ids if i in by_id])
    with db.connect() as conn:
        for call in result.calls:
            db.log_llm_call(conn, run_id=None, purpose="eval", **vars(call))

    wb = Workbook()
    guide = wb.active
    guide.title = "How to review"
    for line in [
        "Each row is one sampled post with a DRAFT label from Claude Opus 5.5 (a stronger model than the one being graded).",
        "The production model's (Haiku's) answers are not in this file, so you aren't anchored by the thing being graded.",
        "",
        "For every row: read the post (open the url if the description is short), then",
        "  - if the draft is right, set 'Correct?' to Y",
        "  - if anything is wrong, set 'Correct?' to N and fill ONLY the fields that are wrong in the 'Fixed ...' columns",
        "Role must be one of the Taxonomy values (dropdown). Cities: separate with ';' (Bangalore/Bengaluru both fine).",
        "Use 'notes' for anything ambiguous. Ambiguous cases are good material for the write-up and the interview.",
        "",
        "When done:  python -m eval.score --labels eval/data/eval50_review.xlsx --compare claude-sonnet-5-5",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 130

    ws = wb.create_sheet("Review")
    headers = ["job_id", "title", "company", "location", "description (first 700 chars)", "url",
               "draft role", "draft cities", "draft is_fresher", "draft in_india",
               "Correct? (Y/N)", "Fixed role", "Fixed cities", "Fixed is_fresher", "notes"]
    ws.append(headers)
    missing = []
    for i in ids:
        p = by_id.get(i)
        if p is None:
            continue
        t = result.tags.get(i)
        if t is None:
            missing.append(i)
        cities = [t.other_city if c == "Other" and t.other_city else c for c in t.cities] if t else []
        ws.append([
            i, p["title"], p["company"], p["location_raw"], (p["description"] or "")[:700], p["url"],
            t.role if t else "", "; ".join(cities), t.is_fresher if t else "", t.in_india if t else "",
            None, None, None, None, None,
        ])

    widths = (8, 34, 18, 22, 60, 16, 24, 22, 10, 10, 11, 24, 22, 12, 30)
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(1, col).column_letter].width = width
    for cell in ws[1]:
        cell.font = Font(bold=True)
    yellow = PatternFill("solid", fgColor="FFF4CC")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        for cell in row[10:14]:
            cell.fill = yellow
    ws.freeze_panes = "C2"

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

    last = ws.max_row
    yn = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    role_dv = DataValidation(type="list", formula1=f"=Taxonomy!$A$2:$A${len(ROLES) + 1}", allow_blank=True)
    tf = DataValidation(type="list", formula1='"TRUE,FALSE"', allow_blank=True)
    for dv, rng in ((yn, f"K2:K{last}"), (role_dv, f"L2:L{last}"), (tf, f"N2:N{last}")):
        ws.add_data_validation(dv)
        dv.add(rng)

    wb.save(REVIEW_XLSX)
    print(f"reference labels for {len(result.tags)}/{len(ids)} posts, ${result.cost:.4f}")
    if missing:
        print(f"no draft for {missing}: label those by hand")
    print(f"review this: {REVIEW_XLSX}")


if __name__ == "__main__":
    main()
