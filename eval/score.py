"""Score the tagger against hand labels.

    python -m eval.score                                    # production tags (frozen at sampling)
    python -m eval.score --compare claude-sonnet-5-5        # also re-tag the same posts with other model(s)
    python -m eval.score --compare claude-haiku-5-5 --prompt v2   # try a new prompt on the same posts
    python -m eval.score --labels path/to/download.csv      # labels exported from Google Sheets

Accuracy definitions:
  role   - exact match with the hand label
  city   - exact match of the *set* of cities (["Pune"] vs ["Pune","Mumbai"] is wrong)
  both   - role and city both right
  fresher precision - of posts the model said freshers can apply to, how many really are
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook

from core import db
from core.taxonomy import normalize_city_label
from eval.sample import LABELS_XLSX, PREDICTIONS_CSV
from pipeline.tagger import Tagger

RESULTS = Path(__file__).parent / "results"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a proportion. With n=50, +-10 points is normal; say so instead of quoting one number."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


def city_set(value: str) -> frozenset[str]:
    parts = [p for chunk in (value or "").split(";") for p in chunk.split(",")]
    return frozenset(c for c in (normalize_city_label(p) for p in parts) if c)


def _bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    s = str(value or "").strip().lower()
    return {"true": True, "yes": True, "y": True, "1": True, "false": False, "no": False, "n": False, "0": False}.get(s)


def _review_rows(rows: list[dict]) -> dict[int, dict]:
    """Review sheet (eval/reference.py): the reviewer's fix wins, otherwise the draft stands."""
    labels = {}
    for r in rows:
        if not r.get("job_id"):
            continue
        verdict = str(r.get("Correct? (Y/N)") or "").strip().upper()
        fixed_role = str(r.get("Fixed role") or "").strip()
        fixed_cities = str(r.get("Fixed cities") or "").strip()
        fixed_fresher = _bool(r.get("Fixed is_fresher"))
        labels[int(r["job_id"])] = {
            "title": r.get("title", ""),
            "role": fixed_role or str(r.get("draft role") or "").strip(),
            "cities": city_set(fixed_cities or str(r.get("draft cities") or "")),
            "is_fresher": fixed_fresher if fixed_fresher is not None else _bool(r.get("draft is_fresher")),
            "notes": r.get("notes") or "",
            "reviewed": verdict in ("Y", "N"),
            "corrected": verdict == "N",
        }
    return labels


def load_labels(path: Path) -> dict[int, dict]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    else:
        wb = load_workbook(path, data_only=True)
        ws = wb["Review"] if "Review" in wb.sheetnames else wb["Label"]
        it = ws.iter_rows(values_only=True)
        headers = [str(h) for h in next(it)]
        rows = [dict(zip(headers, r)) for r in it]
    if rows and "draft role" in rows[0]:
        return _review_rows(rows)
    labels = {}
    for r in rows:
        role = (r.get("YOUR role") or "").strip()
        if not r.get("job_id") or not role:
            continue  # not labelled yet
        labels[int(r["job_id"])] = {
            "title": r.get("title", ""),
            "role": role,
            "cities": city_set(str(r.get("YOUR cities (separate with ;)") or "")),
            "is_fresher": _bool(r.get("YOUR is_fresher")),
            "notes": r.get("notes") or "",
            "reviewed": True,
            "corrected": False,
        }
    return labels


def load_frozen_predictions() -> dict[int, dict]:
    with PREDICTIONS_CSV.open(encoding="utf-8") as f:
        return {
            int(r["job_id"]): {
                "model": r["model"], "prompt_version": r["prompt_version"], "role": r["role"],
                "cities": city_set(r["cities"]), "is_fresher": r["is_fresher"] == "True",
            }
            for r in csv.DictReader(f)
        }


def score(preds: dict[int, dict], labels: dict[int, dict]) -> tuple[dict, list[dict]]:
    ids = [i for i in labels if i in preds]
    n = len(ids)
    role_ok = sum(preds[i]["role"] == labels[i]["role"] for i in ids)
    city_ok = sum(preds[i]["cities"] == labels[i]["cities"] for i in ids)
    both_ok = sum(preds[i]["role"] == labels[i]["role"] and preds[i]["cities"] == labels[i]["cities"] for i in ids)
    said_fresher = [i for i in ids if preds[i]["is_fresher"] and labels[i]["is_fresher"] is not None]
    fresher_ok = sum(labels[i]["is_fresher"] for i in said_fresher)

    errors = []
    for i in ids:
        p, l = preds[i], labels[i]
        if p["role"] != l["role"]:
            errors.append({"job_id": i, "title": l["title"], "field": "role", "model": p["role"], "human": l["role"], "notes": l["notes"]})
        if p["cities"] != l["cities"]:
            errors.append({"job_id": i, "title": l["title"], "field": "cities", "model": ";".join(sorted(p["cities"])),
                           "human": ";".join(sorted(l["cities"])), "notes": l["notes"]})
        if p["is_fresher"] and l["is_fresher"] is False:
            errors.append({"job_id": i, "title": l["title"], "field": "is_fresher", "model": "True", "human": "False", "notes": l["notes"]})

    metrics = {
        "n": n,
        "role_accuracy": round(role_ok / n, 3) if n else 0,
        "city_accuracy": round(city_ok / n, 3) if n else 0,
        "both_accuracy": round(both_ok / n, 3) if n else 0,
        "fresher_precision": round(fresher_ok / len(said_fresher), 3) if said_fresher else None,
        "role_ci": wilson(role_ok, n),
        "city_ci": wilson(city_ok, n),
        "both_ci": wilson(both_ok, n),
    }
    return metrics, errors


def retag(ids: list[int], model: str, prompt_version: str) -> tuple[dict[int, dict], float, list]:
    with db.connect() as conn:
        posts = conn.execute(
            "select id, title, company, location_raw, description from jobs where id = any(%s) order by id", (ids,)
        ).fetchall()
    tagger = Tagger(model=model, prompt_version=prompt_version)
    result = tagger.tag(posts)
    with db.connect() as conn:
        for call in result.calls:
            db.log_llm_call(conn, run_id=None, purpose="eval", **vars(call))
    preds = {
        i: {"model": model, "prompt_version": prompt_version, "role": t.role,
            "cities": frozenset(t.cities), "is_fresher": t.is_fresher}
        for i, t in result.tags.items()
    }
    per_1000 = result.cost / len(posts) * 1000 if posts else 0.0
    return preds, per_1000, result.calls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", type=Path, default=LABELS_XLSX)
    ap.add_argument("--compare", nargs="*", default=[], help="models to re-tag the same posts with")
    ap.add_argument("--prompt", default=None, help="prompt version for --compare runs (default: same as production)")
    args = ap.parse_args()

    labels = load_labels(args.labels)
    if not labels:
        raise SystemExit(f"no labelled rows in {args.labels}; fill in the 'YOUR ...' columns first")
    frozen = load_frozen_predictions()
    base_model = next(iter(frozen.values()))["model"]
    base_prompt = next(iter(frozen.values()))["prompt_version"]

    metrics, errors = score(frozen, labels)
    reviewed = sum(l.get("reviewed", False) for l in labels.values())
    corrected = sum(l.get("corrected", False) for l in labels.values())
    label_source = (
        "Claude Opus 5.5 draft, reviewed by hand" if "review" in args.labels.name else "labelled by hand, blind"
    )
    summary = {"model": base_model, "prompt_version": base_prompt, **metrics,
               "labels": {"source": label_source, "reviewed": reviewed, "corrected": corrected},
               "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "compare": []}
    if reviewed < len(labels):
        print(f"WARNING: only {reviewed}/{len(labels)} rows reviewed; the rest use the unreviewed draft label")

    print(f"\n{base_model} / prompt {base_prompt}  (n={metrics['n']})")
    print(f"  role  {metrics['role_accuracy']:.0%}  CI {metrics['role_ci'][0]:.0%}-{metrics['role_ci'][1]:.0%}")
    print(f"  city  {metrics['city_accuracy']:.0%}  CI {metrics['city_ci'][0]:.0%}-{metrics['city_ci'][1]:.0%}")
    print(f"  both  {metrics['both_accuracy']:.0%}")
    if metrics["fresher_precision"] is not None:
        print(f"  fresher precision {metrics['fresher_precision']:.0%}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    args.prompt = args.prompt or base_prompt
    for model in args.compare:
        preds, per_1000, _ = retag(list(labels), model, args.prompt)
        m, errs = score(preds, labels)
        summary["compare"].append({"model": model, "prompt_version": args.prompt, **m, "cost_per_1000_usd": round(per_1000, 4)})
        print(f"\n{model} / prompt {args.prompt}  (n={m['n']}, ${per_1000:.4f} per 1,000 posts)")
        print(f"  role {m['role_accuracy']:.0%}  city {m['city_accuracy']:.0%}  both {m['both_accuracy']:.0%}")
        with (RESULTS / f"errors_{model}_{args.prompt}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["job_id", "title", "field", "model", "human", "notes"])
            w.writeheader()
            w.writerows(errs)

    with (RESULTS / "errors.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["job_id", "title", "field", "model", "human", "notes"])
        w.writeheader()
        w.writerows(errors)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {RESULTS / 'summary.json'} and errors.csv ({len(errors)} errors)")


if __name__ == "__main__":
    main()
