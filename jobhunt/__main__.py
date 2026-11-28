"""Personal job-hunt toolkit. Run `python -m jobhunt --help`.

  init         create the Excel tracker
  resume       render the untailored resume for a track
  apply        tailor a resume to one posting, render the PDF, add a tracker row
  applied      mark an application as submitted
  status       move an application to another status
  list         show tracked applications
  followups    applications with no reply after N days, with a follow-up draft
  sync-mail    read Gmail and update replies / statuses
  discover     find matching openings on company job boards (and Adzuna), posted in the last 30 days
  verify       check Ready jobs are still open on the company's board, and how old they are
  dashboard    build the HTML dashboard: funnel, weekly targets, skills in demand, every resume
  serve        the dashboard on http://127.0.0.1:8765: board moves save to the tracker, autofill buttons
  autofill     fill application forms in a browser window for you to check and submit
  export-bundle / import-bundle   move applications and job matches between computers
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from jobhunt.settings import get_settings
from jobhunt.tracker import STATUSES, Tracker, create_workbook


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def cmd_init(args, s) -> None:
    if s.tracker_path.exists() and not args.force:
        _fail(f"{s.tracker_path} already exists (use --force to overwrite it, which erases your data)")
    create_workbook(s.tracker_path)
    print(f"Created {s.tracker_path}")


def cmd_resume(args, s) -> None:
    from jobhunt.profile import load_profile, base_resume
    from jobhunt.render import Renderer, ats_check, fit_one_page

    profile = load_profile(s.profile_path)
    tracks = list(profile.tracks) if args.track == "all" else [args.track]
    with Renderer(s.chromium_path) as r:
        for track in tracks:
            out = s.data_dir / "base" / f"{profile.name.replace(' ', '_')}_Resume_{track}.pdf"
            doc, pages, cuts = fit_one_page(base_resume(profile, track, [s.candidate_phone, s.candidate_email]), r, out)
            problems = ats_check(out, doc)
            print(f"{out}  ({pages} page{'s' * (pages > 1)}{'; ' + '; '.join(cuts) if cuts else ''})"
                  + (f"  ATS: {'; '.join(problems)}" if problems else ""))
    if not (s.candidate_phone and s.candidate_email):
        print("note: CANDIDATE_PHONE / CANDIDATE_EMAIL aren't set in .env, so the resume has no contact line")


def _read_jd(args, s) -> tuple[str, dict]:
    meta: dict = {}
    if args.lead:
        from jobhunt.discover import lead_description

        t = Tracker(s.tracker_path)
        lead = t.lead(args.lead)
        meta = {"company": lead["company"], "role": lead["title"], "url": lead["url"], "location": lead["location"],
                "source": "Discover feed"}
        jd = lead_description(s.data_dir / "leads", lead["key"])
        if not jd:
            _fail("no saved description for that lead; run discover again or pass --jd")
        return jd, meta
    if args.jd_url:
        import httpx
        from pipeline.normalize import html_to_text

        try:
            resp = httpx.get(args.jd_url, follow_redirects=True, timeout=30,
                             headers={"User-Agent": "Mozilla/5.0 (job application helper)"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            _fail(f"couldn't fetch {args.jd_url} ({exc}). LinkedIn/Naukri block this: save the text to a file and use --jd")
        meta["url"] = args.jd_url
        return html_to_text(resp.text), meta
    if args.jd == "-":
        return sys.stdin.read(), meta
    if args.jd:
        return Path(args.jd).read_text(encoding="utf-8"), meta
    _fail("give the posting with --jd FILE, --jd - (stdin), --jd-url URL, or --lead ROW")


def cmd_apply(args, s) -> None:
    from jobhunt.apply import prepare_application

    jd, meta = _read_jd(args, s)
    company = args.company or meta.get("company")
    role = args.role or meta.get("role")
    if not company or not role:
        _fail("--company and --role are required")
    try:
        out = prepare_application(
            s, jd=jd, company=company, role=role, url=args.url or meta.get("url", ""),
            location=args.location or meta.get("location", ""), source=args.source or meta.get("source", ""),
            offline=args.offline, variant=args.variant, applied=args.applied, applied_with=args.applied_with or "",
            replace=args.replace,
        )
    except (ValueError, RuntimeError) as exc:
        _fail(str(exc))
    if args.lead:
        t = Tracker(s.tracker_path)
        t.set_lead_decision(args.lead, "Applied" if args.applied else "Apply")
        t.save()
    fit = out["result"].tailoring.fit
    print(f"{out['id']}  {company} / {role}")
    print(f"  fit {fit.score}/100 ({fit.verdict}), tailored by {out['result'].source}")
    print(f"  resume  {out['pdf']}")
    print(f"  report  {out['folder'] / 'report.md'}")
    if fit.gaps:
        print(f"  gaps    {', '.join(fit.gaps)}")
    for w in out["result"].warnings:
        print(f"  guard   {w}")
    if out["ats"]:
        print(f"  ATS     {'; '.join(out['ats'])}")
    if not args.applied:
        print(f"  after you submit: python -m jobhunt applied {out['id']}")


def cmd_applied(args, s) -> None:
    t = Tracker(s.tracker_path)
    values = {"status": "Applied", "date_applied": _date(args.date) or date.today()}
    if args.applied_with or s.candidate_email:
        values["applied_with"] = args.applied_with or s.candidate_email
    if args.source:
        values["source"] = args.source
    try:
        t.update(args.id, **values)
    except KeyError as exc:
        _fail(str(exc))
    t.save()
    print(f"{args.id.upper()} marked Applied on {values['date_applied']:%d %b %Y}")


def cmd_status(args, s) -> None:
    status = next((x for x in STATUSES if x.lower() == args.status.lower()), None)
    if status is None:
        _fail(f"unknown status {args.status!r}; one of: {', '.join(STATUSES)}")
    t = Tracker(s.tracker_path)
    values = {"status": status}
    if args.next:
        values["next_step"] = args.next
    if args.next_date:
        values["next_step_date"] = _date(args.next_date)
    try:
        t.update(args.id, **values)
        if args.note:
            t.append_note(args.id, f"[{date.today():%d %b}] {args.note}")
    except KeyError as exc:
        _fail(str(exc))
    t.save()
    print(f"{args.id.upper()} -> {status}")


def _fmt_date(d) -> str:
    if isinstance(d, datetime):
        d = d.date()
    return f"{d:%d %b}" if d else "-"


def cmd_list(args, s) -> None:
    t = Tracker(s.tracker_path)
    rows = t.applications()
    if args.status:
        rows = [a for a in rows if (a["status"] or "").lower() == args.status.lower()]
    if not rows:
        print("No applications yet.")
        return
    print(f"{'ID':5} {'Status':13} {'Applied':8} {'Reply':5} {'Fit':4} Company / Role")
    for a in rows:
        print(f"{a['id'] or '':5} {a['status'] or '':13} {_fmt_date(a['date_applied']):8} "
              f"{a['email_received'] or '':5} {str(a['fit'] or ''):4} {a['company']} / {a['role']}")


def cmd_followups(args, s) -> None:
    t = Tracker(s.tracker_path)
    due = t.followups(args.days or s.followup_after_days)
    if not due:
        print("Nothing to follow up on.")
        return
    for a in due:
        applied = a["date_applied"].date() if isinstance(a["date_applied"], datetime) else a["date_applied"]
        print(f"{a['id']}  {a['company']} / {a['role']}  applied {_fmt_date(applied)} "
              f"({(date.today() - applied).days} days, no reply)")
    from jobhunt.profile import load_profile

    first_name = load_profile(s.profile_path).name.split()[0]
    print("\nDraft (edit before sending; keep it to the facts on your resume):\n")
    print("Hi <name>, I applied for the <role> role on <date> and wanted to check in. <One line: your strongest "
          "proof for this role, e.g. the Node.js/NestJS backends you shipped end to end.> Is there anything else "
          f"I can share to help with the review? Thanks, {first_name}")


def cmd_sync_mail(args, s) -> None:
    from jobhunt import mailsync

    address, _ = s.gmail_login
    print(f"Reading {address or 'your Gmail'}...")
    try:
        out = mailsync.run_sync(s, since=_date(args.since), dry_run=args.dry_run, auto_add=not args.no_add)
    except RuntimeError as exc:
        _fail(str(exc))
    report, mails = out["report"], out["mails"]
    for mail, new in report.added:
        print(f"  + new application from email: {new['company']} / {new['role']} ({new['source']})")
    for c in report.changes:
        arrow = f"{c.old_status} -> {c.new_status}" if c.new_status else "no status change"
        print(f"  {c.app_id} {c.company}: {c.signal or 'reply'} ({arrow}) | {c.mail.subject[:70]}"
              + (f"  [{c.note}]" if c.note else ""))
    for mail, companies in report.ambiguous:
        print(f"  ? matches several companies ({', '.join(companies)}): {mail.subject[:70]}")
    for mail in report.unmatched:
        print(f"  ? recruiting email for an untracked company: {mail.sender[:40]} | {mail.subject[:60]}")
    if args.dry_run:
        print(f"Dry run: {len(report.changes)} change(s) not written.")
        return
    print(f"Done: {len(mails)} emails since {out['since']:%d %b} read, {len(report.changes)} matched to applications, "
          f"{len(report.added)} added, {len(report.conflicts)} conflict(s) to check by hand.")


def cmd_discover(args, s) -> None:
    from jobhunt.discover import SOURCE_LABELS, run_discover

    if args.cached:
        print("Re-scoring the posts fetched in the last 12 hours (drop --cached to refetch)...")
    else:
        print("Searching company job boards and remote job boards (takes a few minutes; Lever is slow)...")
    out = run_discover(s, sources=args.sources, cached=args.cached, max_age_days=args.max_age_days,
                       max_years=args.max_years, min_score=args.min_score, limit=args.limit, title_filter=args.title,
                       min_employees=args.min_employees, max_employees=args.max_employees, wider=args.wider,
                       progress=lambda name, i, n: print(f"  {i + 1}/{n} {SOURCE_LABELS.get(name, name)}"))
    for e in out["errors"]:
        print(f"  warning: {e}")
    rows = out["rows"]
    print(f"{out['posts']} jobs checked; {len(rows)} match your profile, posted in the last {out['max_age']} days, "
          f"in your locations ({s.preferred_cities}{', or remote' if s.remote_abroad else ''}); "
          f"{out['added']} new added to the Leads sheet.")
    for r in rows[:15]:
        age = f"{(date.today() - r['posted']).days}d ago" if r["posted"] else "date not given"
        print(f"  {r['score']:3}  {r['company']} / {r['title']}  [{r['where']}; {r['exp'] or 'exp not stated'}; {age}]")


def cmd_verify(args, s) -> None:
    from jobhunt import verify

    today = date.today()
    print("Checking each job on the company's own job board...")
    results = verify.verify_applications(
        s, [i.upper() for i in args.ids] or None, today=today,
        progress=lambda n, total, company: print(f"  {n}/{total} {company}", end="\r", flush=True))
    print(" " * 60, end="\r")
    for r in results:
        c = r.check
        fresh = verify.freshness(c.posted, today, s.fresh_job_days, s.stale_job_days)
        age = f"posted {(today - c.posted).days}d ago ({fresh})" if c.posted else "post date unknown"
        tail = "  -> moved to Withdrawn" if r.withdrawn else ""
        print(f"  {r.id}  {c.state:<7} {r.company[:18]:<18} {age:<28} {c.detail}{tail}")
    print(verify.summary(results, today, s.fresh_job_days, s.stale_job_days))


def cmd_import_status(args, s) -> None:
    """Apply status changes made on the hosted dashboard: a JSON list of {id, status, date[, note]}."""
    import json

    t = Tracker(s.tracker_path)
    changes = json.loads(Path(args.file).read_text(encoding="utf-8"))
    for c in changes:
        app = t.find(str(c.get("id", "")))
        before = app["status"] if app else None
        try:
            changed = t.move(str(c.get("id", "")), str(c.get("status", "")), _date(c.get("date")), c.get("note") or "")
        except KeyError:
            print(f"  skipped {c.get('id')}: unknown status or ID")
            continue
        if changed:
            print(f"  {app['id']} {app['company']}: {before} -> {t.find(app['id'])['status']}")
    t.save()


def cmd_dashboard(args, s) -> None:
    from jobhunt.dashboard import write_dashboard

    out = write_dashboard(s)
    print(f"Dashboard: {out}")
    if args.open:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())


def cmd_serve(args, s) -> None:
    from jobhunt.server import serve

    serve(s, port=args.port, open_browser=not args.no_open)


def cmd_autofill(args, s) -> None:
    from jobhunt import autofill

    autofill.load_answers(s)  # creates jobhunt/data/answers.yaml on first use
    ids = autofill.ready_ids(s, args.limit) if args.all_ready else [i.upper() for i in args.ids]
    if not ids:
        _fail("give application IDs (e.g. A010 A031) or --all-ready")
    jobs = autofill.plan(s, ids)
    if not jobs:
        _fail("none of those have a company apply link (LinkedIn and Naukri jobs are applied to by hand)")
    skipped = sorted(set(ids) - {j["id"] for j in jobs})
    print("Opening: " + ", ".join(f"{j['id']} {j['company']}" for j in jobs)
          + (f"\nSkipped (no company apply link, or LinkedIn/Naukri): {', '.join(skipped)}" if skipped else ""))
    print("Each form is submitted for you once every required question is answered; the rest wait for you."
          if args.submit else "Check every field, fill the orange ones, then click Submit yourself. Close the window when done.")
    session = autofill.run(s, [j["id"] for j in jobs], headless=args.headless, minutes=args.minutes, auto=args.submit,
                           on_submitted=lambda app_id: autofill.mark_submitted(s, app_id))
    for app_id, st in session.jobs.items():
        print(f"  {app_id}: {st['note']}" + (f" Unanswered: {'; '.join(st['missing'])}" if st.get("missing") else ""))
    print(session.message)


def cmd_export_bundle(args, s) -> None:
    from jobhunt.bundle import export_bundle

    try:
        out = export_bundle(s, [i.upper() for i in args.ids], args.leads or [], Path(args.out))
    except KeyError as exc:
        _fail(str(exc))
    print(f"{out['path']}: {out['apps']} application(s), {out['leads']} job match(es)")


def cmd_import_bundle(args, s) -> None:
    from jobhunt.bundle import find_bundles, import_bundle

    path = Path(args.file) if args.file else None
    if path is None or not path.exists():
        found = find_bundles([path.parent] if path is not None else None)
        if path is not None:
            # Browsers rename repeat downloads ("pune-picks (1).zip"): take the newest with the same start.
            same = [f for f in found if f.name.lower().startswith(path.stem.lower())]
            found = same or found
            print(f"{path} doesn't exist.")
        if not found:
            _fail("no bundle found in Downloads, Desktop, Documents or this folder. Click the zip's card in the "
                  "Claude chat and choose Download (or save it), then run this again, or give its full path.")
        path = found[0]
        print(f"Using {path}")
    try:
        out = import_bundle(s, path)
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, OSError) as exc:
        _fail(str(exc))
    for old, new, company, role in out["added"]:
        print(f"  + {new}  {company} / {role}  (was {old})")
    for line in out["skipped"]:
        print(f"  - skipped {line}")
    print(f"{len(out['added'])} application(s) added to Ready to apply, {out['leads']} new job match(es).")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m jobhunt", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("init", help="create the Excel tracker")
    a.add_argument("--force", action="store_true")

    a = sub.add_parser("resume", help="render the untailored resume for a track")
    a.add_argument("--track", default="all", choices=["all", "backend", "fullstack", "ai", "mobile"])

    a = sub.add_parser("apply", help="tailor a resume to one posting and track it")
    a.add_argument("--jd", help="file with the job description ('-' for stdin)")
    a.add_argument("--jd-url", help="fetch the posting from a URL (company/ATS pages; not LinkedIn/Naukri)")
    a.add_argument("--lead", type=int, help="row number on the Leads sheet")
    a.add_argument("--company")
    a.add_argument("--role")
    a.add_argument("--url", help="job link to store in the tracker")
    a.add_argument("--location", default="")
    a.add_argument("--source", default="", help="Naukri, LinkedIn, Company site, Referral, ...")
    a.add_argument("--offline", action="store_true", help="keyword tailoring only, no Claude call")
    a.add_argument("--variant", type=Path, help="use a tailoring JSON you wrote or edited (schema: tailoring.json)")
    a.add_argument("--applied", action="store_true", help="you already submitted it: mark Applied today")
    a.add_argument("--with", dest="applied_with", help="email address you applied with")
    a.add_argument("--replace", metavar="ID", help="re-tailor an existing application (same ID and folder)")

    a = sub.add_parser("applied", help="mark an application as submitted")
    a.add_argument("id")
    a.add_argument("--date", help="YYYY-MM-DD, default today")
    a.add_argument("--with", dest="applied_with")
    a.add_argument("--source")

    a = sub.add_parser("status", help="change an application's status")
    a.add_argument("id")
    a.add_argument("status", help=", ".join(STATUSES))
    a.add_argument("--note")
    a.add_argument("--next", help="next step, e.g. 'Round 2: system design'")
    a.add_argument("--next-date", help="YYYY-MM-DD")

    a = sub.add_parser("list", help="show applications")
    a.add_argument("--status")

    a = sub.add_parser("followups", help="applications that need a nudge")
    a.add_argument("--days", type=int)

    a = sub.add_parser("sync-mail", help="read Gmail and update the tracker")
    a.add_argument("--since", help="YYYY-MM-DD, default: last sync")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--no-add", action="store_true",
                   help="don't create rows for 'application received' emails about untracked jobs")

    a = sub.add_parser("discover", help="find matching openings")
    a.add_argument("--sources", default="greenhouse,lever,ashby,smartrecruiters,remotive,jobicy,remoteok,himalayas,"
                                        "weworkremotely,workingnomads,adzuna")
    a.add_argument("--wider", action="store_true", help="also jobs up to STALE_JOB_DAYS old and in other Indian cities")
    a.add_argument("--max-years", type=int, default=4)
    a.add_argument("--min-score", type=int, default=35)
    a.add_argument("--limit", type=int, default=150)
    a.add_argument("--title", help="regex the job title must match, e.g. 'software engineer|backend|ai engineer'")
    a.add_argument("--min-employees", type=int, default=0, help="keep companies with at least this many employees (approx)")
    a.add_argument("--max-employees", type=int, default=0, help="keep companies with at most this many employees (approx)")
    a.add_argument("--cached", action="store_true", help="re-score the last fetch (under 12 hours old) instead of refetching")
    a.add_argument("--max-age-days", type=int, help="skip jobs published longer ago than this (default FRESH_JOB_DAYS, 30)")

    a = sub.add_parser("verify", help="check jobs are still open on the company's board, and how old they are")
    a.add_argument("ids", nargs="*", help="application IDs (default: every Ready to apply job)")

    a = sub.add_parser("import-status", help="apply status changes saved on the hosted dashboard (JSON file)")
    a.add_argument("file")

    a = sub.add_parser("dashboard", help="build the HTML dashboard (jobhunt/data/dashboard.html)")
    a.add_argument("--open", action="store_true", help="open it in your browser")

    a = sub.add_parser("export-bundle", help="pack applications and job matches into a zip for another computer")
    a.add_argument("ids", nargs="*", help="application IDs")
    a.add_argument("--leads", type=int, nargs="*", help="Leads sheet row numbers")
    a.add_argument("--out", required=True)

    a = sub.add_parser("import-bundle", help="add the applications and job matches in a bundle zip to your tracker")
    a.add_argument("file", nargs="?", help="the zip (or unzipped folder); default: the newest one in Downloads etc.")

    a = sub.add_parser("serve", help="run the dashboard on localhost, with saving and autofill")
    a.add_argument("--port", type=int, default=8765)
    a.add_argument("--no-open", action="store_true", help="don't open the browser")

    a = sub.add_parser("autofill", help="fill application forms for you to check and submit")
    a.add_argument("ids", nargs="*", help="application IDs, e.g. A010 A031")
    a.add_argument("--all-ready", action="store_true", help="the best ready-to-apply jobs (see --limit)")
    a.add_argument("--limit", type=int, default=5)
    a.add_argument("--minutes", type=int, default=45, help="close the browser after this long")
    a.add_argument("--submit", action="store_true",
                   help="submit each form for you once every required question is answered (questions.yaml)")
    a.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)

    args = p.parse_args(argv)
    s = get_settings()
    {"init": cmd_init, "resume": cmd_resume, "apply": cmd_apply, "applied": cmd_applied, "status": cmd_status,
     "list": cmd_list, "followups": cmd_followups, "sync-mail": cmd_sync_mail, "discover": cmd_discover,
     "dashboard": cmd_dashboard, "import-status": cmd_import_status, "serve": cmd_serve, "verify": cmd_verify,
     "autofill": cmd_autofill,
     "export-bundle": cmd_export_bundle, "import-bundle": cmd_import_bundle}[args.cmd](args, s)


if __name__ == "__main__":
    main()
