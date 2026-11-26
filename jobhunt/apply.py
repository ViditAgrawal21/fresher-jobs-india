"""One application, end to end: tailor -> one-page PDF -> ATS check -> files -> tracker row."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jobhunt.keywords import slug, years_required
from jobhunt.profile import load_profile
from jobhunt.render import Renderer, ats_check, fit_one_page
from jobhunt.settings import JobhuntSettings
from jobhunt.tailor import Tailoring, TailorResult, tailor
from jobhunt.tracker import Tracker

TRACK_LABELS = {"backend": "Backend", "fullstack": "Full Stack", "ai": "AI Engineer", "mobile": "Mobile"}
# USD per 1M tokens (input, output); cache reads 0.1x input, cache writes 1.25x input.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
          "claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0)}


def _cost(usage: dict) -> float | None:
    price = PRICES.get(usage.get("model", ""))
    if not price:
        return None
    p_in, p_out = price
    return (usage["input_tokens"] * p_in + usage["cache_write_tokens"] * p_in * 1.25
            + usage["cache_read_tokens"] * p_in * 0.1 + usage["output_tokens"] * p_out) / 1e6


def prepare_application(settings: JobhuntSettings, *, jd: str, company: str, role: str, url: str = "",
                        location: str = "", source: str = "", offline: bool = False, variant: Path | None = None,
                        applied: bool = False, applied_with: str = "") -> dict:
    if len(jd.strip()) < 200:
        raise ValueError("The job description looks too short (under 200 characters). Paste the full posting.")
    profile = load_profile(settings.profile_path)
    contact = [settings.candidate_phone, settings.candidate_email]
    manual = Tailoring.model_validate_json(Path(variant).read_text(encoding="utf-8")) if variant else None
    result = tailor(profile, jd, company, role, contact, offline=offline, model=settings.tailor_model,
                    effort=settings.tailor_effort, api_key=settings.anthropic_api_key, manual=manual)

    tracker = Tracker(settings.tracker_path)  # opened before writing files, so a locked workbook fails early
    app_id = tracker.next_id()
    folder = settings.data_dir / "applications" / f"{app_id}_{slug(company, 24)}_{slug(role, 32)}"
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / f"{profile.name.replace(' ', '_')}_Resume.pdf"
    with Renderer(settings.chromium_path) as renderer:
        doc, pages, cuts = fit_one_page(result.doc, renderer, pdf)
    ats = ats_check(pdf, doc)

    (folder / "job_description.txt").write_text(jd.strip() + "\n", encoding="utf-8")
    (folder / "tailoring.json").write_text(result.tailoring.model_dump_json(indent=1), encoding="utf-8")
    report = _report(result, profile, doc, company, role, url, pages, cuts, ats)
    (folder / "report.md").write_text(report, encoding="utf-8")

    years = years_required(jd)
    fit = result.tailoring.fit
    tracker.add_application(
        id=app_id, company=company, role=role, track=TRACK_LABELS.get(result.tailoring.track, "Other"),
        location=location, source=source or None, url=url or None,
        exp_required=f"{years}+ years" if years is not None else fit.years_required,
        fit=fit.score, resume=str(pdf.relative_to(settings.data_dir)),
        status="Applied" if applied else "Tailored",
        date_applied=date.today() if applied else None,
        applied_with=applied_with or (settings.candidate_email if applied else None),
    )
    tracker.save()
    return {"id": app_id, "folder": folder, "pdf": pdf, "pages": pages, "cuts": cuts, "ats": ats,
            "result": result, "report": report}


def _report(result: TailorResult, profile, doc, company, role, url, pages, cuts, ats) -> str:
    t, fit, cov = result.tailoring, result.tailoring.fit, result.keyword_coverage
    lines = [f"# {company}: {role}", ""]
    if url:
        lines += [url, ""]
    lines += [
        f"**Fit {fit.score}/100, {fit.verdict}.** Years asked: {fit.years_required}. Track: {TRACK_LABELS.get(t.track, t.track)}. "
        f"Tailored by: {result.source}.",
        "",
        f"- Matched: {', '.join(fit.matched) or '-'}",
        f"- Gaps: {', '.join(fit.gaps) or '-'}",
        f"- Red flags: {', '.join(fit.red_flags) or '-'}",
        f"- Keyword coverage (deterministic): {cov['coverage_pct']}% "
        f"(matched {', '.join(cov['matched']) or '-'}; missing {', '.join(cov['missing']) or '-'})",
        "",
        "## Message for the recruiter",
        "",
        t.recruiter_note,
        "",
        "## What changed from your profile",
        "",
        f"- Headline: {doc.headline}",
        f"- Summary: {doc.summary}",
    ]
    originals = {b.id: b.text for j in profile.experience for b in j.bullets}
    originals |= {b.id: b.text for p in profile.projects for b in p.bullets}
    for entry in doc.experience + doc.projects:
        for bid, text in zip(entry.bullet_ids, entry.bullets):
            if text != originals.get(bid):
                lines += [f"- {entry.heading} / {bid}:", f"  - was: {originals.get(bid)}", f"  - now: {text}"]
    lines += ["", "## Checks", "",
              f"- Pages: {pages}" + (f" (cuts: {'; '.join(cuts)})" if cuts else ""),
              f"- ATS text layer: {'clean' if not ats else '; '.join(ats)}"]
    lines += [f"- Guard: {w}" for w in result.warnings] or ["- Guard: nothing reverted"]
    if result.usage:
        cost = _cost(result.usage)
        lines.append(f"- Claude: {result.usage['model']}, {result.usage['input_tokens']} in "
                     f"(+{result.usage['cache_read_tokens']} cached) / {result.usage['output_tokens']} out tokens"
                     + (f", about ${cost:.3f}" if cost is not None else ""))
    lines += ["", "Read the changes above before you send it: you are the last check on every claim.", ""]
    return "\n".join(lines)

