"""`Download Excel report` / `python -m jobhunt report`: everything the dashboard shows, in one workbook.

Sheets: Summary (headline numbers), Pipeline (stages and funnel, with charts), Applications
(every application and its tracking), Weekly (sent against your targets, with a chart), Skills
demand (what postings ask for, have vs gap), Response rates (by match % and by source), Job
matches (leads), Email log and Questions (what forms asked that still needs your answer).

The counts on Summary, Pipeline, Weekly and Response rates are Excel formulas over the
Applications sheet, so they stay right if you edit a status there. The numbers follow the same
rules as the dashboard: "sent" means an applied date or a status past Ready; "responded" means a
reply, assessment, interview, offer or rejection (or an email that says so).
"""

from __future__ import annotations

import io
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import BODY_FONT, BOLD, DATE_FMT, FONT, HEADER_FILL, HEADER_FONT, TITLE_FONT, Tracker

RESPONDED = {"Viewed", "Replied", "Assessment", "Interviewing", "Offer", "Accepted", "Rejected"}
SIGNAL_RESP = {"Viewed", "Assessment", "Interview", "Offer", "Rejected"}
INTERVIEW = {"Assessment", "Interviewing", "Offer", "Accepted"}
STAGES = [("Ready to apply", {"Lead", "Tailored"}), ("Submitted", {"Applied", "Acknowledged"}),
          ("Replied", {"Replied", "Viewed"}), ("Assessment", {"Assessment"}), ("Interviewing", {"Interviewing"}),
          ("Offer", {"Offer", "Accepted"}), ("Closed", {"Rejected", "Ghosted", "Withdrawn"})]
NOTE_FONT = Font(name=FONT, size=9, italic=True, color="595959")
INPUT_FONT = Font(name=FONT, size=10, color="0000FF")  # numbers you can change (weekly targets)
LINK_FONT = Font(name=FONT, size=10, color="0563C1", underline="single")


def _d(iso: str | None) -> date | None:
    return date.fromisoformat(iso) if iso else None


def _stage(status: str) -> str:
    return next((name for name, sts in STAGES if status in sts), "Other")


def _sent(a: dict) -> bool:
    return bool(a["applied"]) or a["status"] not in ("Lead", "Tailored", "Withdrawn")


def _responded(a: dict) -> bool:
    return a["status"] in RESPONDED or a["signal"] in SIGNAL_RESP


def _header(ws, row: int, names: list[str], widths: list[int] | None = None) -> None:
    for c, name in enumerate(names, start=1):
        cell = ws.cell(row=row, column=c, value=name)
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        if widths:
            ws.column_dimensions[get_column_letter(c)].width = widths[c - 1]


def _table(ws, name: str, first_row: int, last_row: int, n_cols: int) -> None:
    if last_row <= first_row:  # an Excel table needs at least one data row
        return
    ref = f"A{first_row}:{get_column_letter(n_cols)}{last_row}"
    t = Table(displayName=name, ref=ref)
    t.tableStyleInfo = TableStyleInfo(name="TableStyleLight9", showRowStripes=True)
    ws.add_table(t)


def _title(ws, text: str, note: str) -> None:
    ws["A1"] = text
    ws["A1"].font = TITLE_FONT
    ws["A2"] = note
    ws["A2"].font = NOTE_FONT


def build_report(settings: JobhuntSettings, today: date | None = None) -> bytes:
    from jobhunt.dashboard import build_data
    from jobhunt.questions import QuestionBank

    today = today or date.today()
    data = build_data(settings, today)
    apps, leads = data["apps"], data["leads"]
    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True  # Excel works the formulas out when you open the file

    # ---------- Applications: the data every other sheet counts from ----------
    ws_a = wb.active
    ws_a.title = "Applications"
    cols = ["ID", "Company", "Role", "Track", "Status", "Stage", "Source", "Where", "Company size", "Job posted",
            "Job age (days)", "Job still open?", "Fit score", "Match %", "Date added", "Date applied",
            "Days since applied", "Sent?", "Responded?", "Interview stage?", "Offer?", "Follow-up due?",
            "Email received?", "Last email", "Email signal", "Last email subject", "Next step", "Next step date",
            "Skills matched", "Skills missing", "Job link", "Resume file"]
    widths = [7, 18, 34, 11, 13, 14, 13, 20, 12, 12, 9, 10, 8, 8, 12, 12, 9, 8, 10, 10, 8, 10, 9, 12, 12, 34, 22, 12,
              34, 26, 30, 30]
    _title(ws_a, "Applications", f"Every application in tracker.xlsx on {today:%d %b %Y}. Sent?, Responded? and the other "
                                 "Yes/No columns drive the counts on the other sheets.")
    _header(ws_a, 4, cols, widths)
    C = {name: i + 1 for i, name in enumerate(cols)}
    L = {name: get_column_letter(i) for name, i in C.items()}
    r = 4
    for a in sorted(apps, key=lambda x: x["id"] or ""):
        r += 1
        applied = _d(a["applied"])
        sent, resp = _sent(a), _responded(a)
        follow = a["status"] in ("Applied", "Acknowledged") and applied is not None and \
            (today - applied).days >= settings.followup_after_days and not resp
        values = {
            "ID": a["id"], "Company": a["company"], "Role": a["role"], "Track": a["track"], "Status": a["status"],
            "Stage": _stage(a["status"]), "Source": a["source"], "Where": a.get("where") or "", "Company size": a["size"],
            "Job posted": _d(a.get("posted")), "Job age (days)": a.get("age"), "Job still open?": a.get("posting") or "",
            "Fit score": a["fit"], "Match %": (a["match"] / 100) if isinstance(a["match"], (int, float)) else None,
            "Date added": _d(a["added"]), "Date applied": applied,
            "Sent?": "Yes" if sent else "No", "Responded?": "Yes" if sent and resp else "No",
            "Interview stage?": "Yes" if a["status"] in INTERVIEW else "No",
            "Offer?": "Yes" if a["status"] in ("Offer", "Accepted") else "No",
            "Follow-up due?": "Yes" if follow else "No",
            "Email received?": "Yes" if a["email"] else "No", "Last email": _d(a["last_email"]),
            "Email signal": a["signal"], "Last email subject": a["subject"], "Next step": a["next"],
            "Next step date": _d(a["next_date"]), "Skills matched": ", ".join(a["matched"]),
            "Skills missing": ", ".join(a["missing"]), "Job link": a["url"],
            "Resume file": str(settings.data_dir / a["resume"]) if a["resume"] else "",
        }
        for name, v in values.items():
            c = ws_a.cell(row=r, column=C[name], value=v)
            c.font = BODY_FONT
        # Days since applied: a formula, so it keeps counting when you open the file next week.
        ws_a.cell(row=r, column=C["Days since applied"],
                  value=f'=IF({L["Date applied"]}{r}="","",TODAY()-{L["Date applied"]}{r})').font = BODY_FONT
        for name in ("Job posted", "Date added", "Date applied", "Last email", "Next step date"):
            ws_a.cell(row=r, column=C[name]).number_format = DATE_FMT
        ws_a.cell(row=r, column=C["Match %"]).number_format = "0%"
        if a["url"]:
            link = ws_a.cell(row=r, column=C["Job link"])
            link.hyperlink, link.font = a["url"], LINK_FONT
    last_app = r
    ws_a.freeze_panes = "C5"
    _table(ws_a, "Applications", 4, last_app, len(cols))
    rng = lambda name: f"Applications!${L[name]}$5:${L[name]}${max(last_app, 5)}"  # noqa: E731

    # ---------- Summary ----------
    ws_s = wb.create_sheet("Summary", 0)
    _title(ws_s, f"Job search report: {data['name']}", f"From tracker.xlsx on {today:%d %b %Y}. Counts are formulas over "
                                                       "the Applications sheet.")
    ws_s.column_dimensions["A"].width, ws_s.column_dimensions["B"].width, ws_s.column_dimensions["C"].width = 30, 12, 52
    _header(ws_s, 4, ["Measure", "Value", "What it means"])
    week_start = today - timedelta(days=today.weekday())
    kpis = [
        ("Applications tracked", f"=COUNTA({rng('ID')})", "Every row on Applications"),
        ("Ready to apply", f'=COUNTIF({rng("Stage")},"Ready to apply")', "Tailored resume ready, not sent yet"),
        ("Applications sent", f'=COUNTIF({rng("Sent?")},"Yes")', "Submitted, or moved past Ready"),
        ("Responses", f'=COUNTIF({rng("Responded?")},"Yes")', "Replies, tests, interviews, offers and rejections"),
        ("Response rate", "=IFERROR(B8/B7,0)", "Responses / applications sent"),
        ("Interview stage", f'=COUNTIF({rng("Interview stage?")},"Yes")', "Assessment, interviewing, offer or accepted"),
        ("Interview rate", "=IFERROR(B10/B7,0)", "Interview stage / applications sent"),
        ("Offers", f'=COUNTIF({rng("Offer?")},"Yes")', "Goal: one offer by 31 Mar"),
        ("Average match %", f'=IFERROR(AVERAGEIFS({rng("Match %")},{rng("Sent?")},"Yes"),AVERAGE({rng("Match %")}))',
         "Keyword match of sent jobs (of all tailored jobs before any are sent)"),
        ("Follow-ups due", f'=COUNTIF({rng("Follow-up due?")},"Yes")',
         f"Sent {settings.followup_after_days}+ days ago with no reply"),
        ("Sent this week", f'=COUNTIFS({rng("Date applied")},">="&DATE({week_start.year},{week_start.month},{week_start.day}))',
         f"Since Monday {week_start:%d %b}"),
        ("New job matches waiting", len(leads), "On the Job matches sheet"),
    ]
    for i, (label, value, note) in enumerate(kpis, start=5):
        ws_s.cell(row=i, column=1, value=label).font = BOLD
        c = ws_s.cell(row=i, column=2, value=value)
        c.font = BODY_FONT
        if label in ("Response rate", "Interview rate", "Average match %"):
            c.number_format = "0%"
        ws_s.cell(row=i, column=3, value=note).font = NOTE_FONT

    # ---------- Pipeline + funnel ----------
    ws_p = wb.create_sheet("Pipeline", 1)
    _title(ws_p, "Pipeline", "Applications by stage, and how far sent applications got.")
    ws_p.column_dimensions["A"].width, ws_p.column_dimensions["B"].width, ws_p.column_dimensions["C"].width = 22, 14, 14
    _header(ws_p, 4, ["Stage", "Applications", "Share"])
    for i, (name, _) in enumerate(STAGES, start=5):
        ws_p.cell(row=i, column=1, value=name).font = BODY_FONT
        ws_p.cell(row=i, column=2, value=f'=COUNTIF({rng("Stage")},A{i})').font = BODY_FONT
        c = ws_p.cell(row=i, column=3, value=f"=IFERROR(B{i}/SUM($B$5:$B${4 + len(STAGES)}),0)")
        c.font, c.number_format = BODY_FONT, "0%"
    end = 4 + len(STAGES)
    f0 = end + 3
    ws_p.cell(row=f0 - 1, column=1, value="Funnel (sent applications)").font = BOLD
    _header(ws_p, f0, ["Step", "Applications", "% of sent"])
    funnel = [("Sent", f'=COUNTIF({rng("Sent?")},"Yes")'), ("Responded", f'=COUNTIF({rng("Responded?")},"Yes")'),
              ("Interview stage", f'=COUNTIF({rng("Interview stage?")},"Yes")'), ("Offer", f'=COUNTIF({rng("Offer?")},"Yes")')]
    for i, (name, formula) in enumerate(funnel, start=f0 + 1):
        ws_p.cell(row=i, column=1, value=name).font = BODY_FONT
        ws_p.cell(row=i, column=2, value=formula).font = BODY_FONT
        c = ws_p.cell(row=i, column=3, value=f"=IFERROR(B{i}/$B${f0 + 1},0)")
        c.font, c.number_format = BODY_FONT, "0%"
    for title, top, bottom, anchor in (("Applications by stage", 5, end, "E3"), ("Funnel", f0 + 1, f0 + 4, "E20")):
        ch = BarChart()
        ch.type, ch.title, ch.legend, ch.height, ch.width = "bar", title, None, 7, 14
        ch.add_data(Reference(ws_p, min_col=2, min_row=top - 1 if top == 5 else f0, max_row=bottom), titles_from_data=True)
        ch.set_categories(Reference(ws_p, min_col=1, min_row=top, max_row=bottom))
        ch.y_axis.majorGridlines = None
        ch.x_axis.scaling.orientation = "maxMin"  # first stage at the top
        ws_p.add_chart(ch, anchor)

    # ---------- Weekly ----------
    ws_w = wb.create_sheet("Weekly", 2)
    _title(ws_w, "Applications per week", "Sent each week against your target. Blue targets are yours to change.")
    for col, w in zip("ABCD", (14, 10, 10, 12)):
        ws_w.column_dimensions[col].width = w
    _header(ws_w, 4, ["Week of (Mon)", "Target", "Sent", "Vs target"])
    for i, wk in enumerate(data["weeks"], start=5):
        start = _d(wk["week"])
        ws_w.cell(row=i, column=1, value=start).number_format = DATE_FMT
        t = ws_w.cell(row=i, column=2, value=wk["target"])
        t.font = INPUT_FONT
        ws_w.cell(row=i, column=3, value=f'=COUNTIFS({rng("Date applied")},">="&A{i},{rng("Date applied")},"<"&(A{i}+7))').font = BODY_FONT
        ws_w.cell(row=i, column=4, value=f"=C{i}-B{i}").font = BODY_FONT
    w_end = 4 + len(data["weeks"])
    if data["weeks"]:
        bar = BarChart()
        bar.title, bar.height, bar.width = "Sent vs target per week", 8, 22
        bar.add_data(Reference(ws_w, min_col=3, min_row=4, max_row=w_end), titles_from_data=True)
        bar.set_categories(Reference(ws_w, min_col=1, min_row=5, max_row=w_end))
        line = LineChart()
        line.add_data(Reference(ws_w, min_col=2, min_row=4, max_row=w_end), titles_from_data=True)
        bar += line
        ws_w.add_chart(bar, "F3")

    # ---------- Skills demand ----------
    ws_k = wb.create_sheet("Skills demand", 3)
    _title(ws_k, "What the postings ask for", "Skills named in your applications' job descriptions and in new job matches.")
    for col, w in zip("ABCDE", (24, 16, 14, 14, 16)):
        ws_k.column_dimensions[col].width = w
    _header(ws_k, 4, ["Skill", "In applications", "In job matches", "Total postings", "On your profile?"])
    have, domain = set(data["supported"]), set(data["domain"])
    in_apps = Counter(t for a in apps for t in a["matched"] + a["missing"] if t not in domain)
    in_leads = Counter(t for l in leads for t in l["matched"] + l["missing"] if t not in domain)
    skills = sorted(set(in_apps) | set(in_leads), key=lambda t: (-(in_apps[t] + in_leads[t]), t))
    for i, t in enumerate(skills, start=5):
        ws_k.cell(row=i, column=1, value=t).font = BODY_FONT
        ws_k.cell(row=i, column=2, value=in_apps[t]).font = BODY_FONT
        ws_k.cell(row=i, column=3, value=in_leads[t]).font = BODY_FONT
        ws_k.cell(row=i, column=4, value=f"=B{i}+C{i}").font = BODY_FONT
        c = ws_k.cell(row=i, column=5, value="Yes" if t in have else "Gap")
        c.font = BODY_FONT if t in have else Font(name=FONT, size=10, bold=True, color="C00000")
    k_end = 4 + len(skills)
    _table(ws_k, "Skills", 4, k_end, 5)
    if skills:
        ch = BarChart()
        ch.type, ch.title, ch.legend, ch.height, ch.width = "bar", "Most-asked skills (top 16)", None, 10, 16
        ch.add_data(Reference(ws_k, min_col=4, min_row=4, max_row=min(k_end, 20)), titles_from_data=True)
        ch.set_categories(Reference(ws_k, min_col=1, min_row=5, max_row=min(k_end, 20)))
        ch.x_axis.scaling.orientation = "maxMin"
        ws_k.add_chart(ch, "G3")

    # ---------- Response rates ----------
    ws_r = wb.create_sheet("Response rates", 4)
    _title(ws_r, "Response rates", "Do better-matched jobs reply more, and which sources answer? About 10 sent "
                                   "applications per row before a rate means much.")
    for col, w in zip("ABCD", (22, 10, 12, 14)):
        ws_r.column_dimensions[col].width = w
    _header(ws_r, 4, ["Match band", "Sent", "Responded", "Response rate"])
    bands = [("80–100%", 0.80, 1.01), ("60–79%", 0.60, 0.80), ("40–59%", 0.40, 0.60), ("Under 40%", 0.0, 0.40)]
    for i, (label, lo, hi) in enumerate(bands, start=5):
        crit = f'{rng("Match %")},">={lo}",{rng("Match %")},"<{hi}"'
        ws_r.cell(row=i, column=1, value=label).font = BODY_FONT
        ws_r.cell(row=i, column=2, value=f'=COUNTIFS({crit},{rng("Sent?")},"Yes")').font = BODY_FONT
        ws_r.cell(row=i, column=3, value=f'=COUNTIFS({crit},{rng("Responded?")},"Yes")').font = BODY_FONT
        c = ws_r.cell(row=i, column=4, value=f"=IFERROR(C{i}/B{i},0)")
        c.font, c.number_format = BODY_FONT, "0%"
    s0 = 5 + len(bands) + 2
    _header(ws_r, s0, ["Source", "Sent", "Responded", "Response rate"])
    sources = sorted({a["source"] or "Other" for a in apps})
    for i, src in enumerate(sources, start=s0 + 1):
        ws_r.cell(row=i, column=1, value=src).font = BODY_FONT
        ws_r.cell(row=i, column=2, value=f'=COUNTIFS({rng("Source")},A{i},{rng("Sent?")},"Yes")').font = BODY_FONT
        ws_r.cell(row=i, column=3, value=f'=COUNTIFS({rng("Source")},A{i},{rng("Responded?")},"Yes")').font = BODY_FONT
        c = ws_r.cell(row=i, column=4, value=f"=IFERROR(C{i}/B{i},0)")
        c.font, c.number_format = BODY_FONT, "0%"

    # ---------- Job matches ----------
    ws_l = wb.create_sheet("Job matches")
    _title(ws_l, "New job matches", "Found by Find more jobs, best JD match first. Not applied to or skipped yet.")
    lcols = ["JD match %", "Company", "Role", "Where", "Location", "Posted", "Age (days)", "Experience asked",
             "Skills matched", "Skills missing", "Source", "Link"]
    _header(ws_l, 4, lcols, [10, 18, 36, 22, 24, 12, 9, 11, 34, 26, 12, 40])
    for i, l in enumerate(leads, start=5):
        row = [(l["score"] or 0) / 100, l["company"], l["title"], l.get("where") or "", l["location"], _d(l["posted"]),
               l.get("age"), l["exp"], ", ".join(l["matched"]), ", ".join(l["missing"]), l.get("source") or "", l["url"]]
        for c, v in enumerate(row, start=1):
            ws_l.cell(row=i, column=c, value=v).font = BODY_FONT
        ws_l.cell(row=i, column=1).number_format = "0%"
        ws_l.cell(row=i, column=6).number_format = DATE_FMT
        if l["url"]:
            ws_l.cell(row=i, column=12).hyperlink = l["url"]
            ws_l.cell(row=i, column=12).font = LINK_FONT
    ws_l.freeze_panes = "C5"
    _table(ws_l, "JobMatches", 4, 4 + len(leads), len(lcols))

    # ---------- Email log ----------
    ws_e = wb.create_sheet("Email log")
    _title(ws_e, "Email log", "Emails from companies that the Gmail sync matched to your applications.")
    tracker = Tracker(settings.tracker_path)
    src = tracker.mail
    n_cols = src.max_column
    rows = [[src.cell(row=r_, column=c).value for c in range(1, n_cols + 1)] for r_ in range(1, src.max_row + 1)]
    rows = [r_ for r_ in rows if any(v not in (None, "") for v in r_)]
    if rows:
        _header(ws_e, 4, [str(v) if v not in (None, "") else f"Column {c}" for c, v in enumerate(rows[0], start=1)], [18, 30, 50, 8, 18, 12, 40, 30][:n_cols] + [14] * max(0, n_cols - 8))
        for i, values in enumerate(rows[1:], start=5):
            for c, v in enumerate(values, start=1):
                cell = ws_e.cell(row=i, column=c, value=v)
                cell.font = BODY_FONT
                if isinstance(v, date):
                    cell.number_format = DATE_FMT + " hh:mm"
        _table(ws_e, "EmailLog", 4, 3 + len(rows), n_cols)

    # ---------- Questions ----------
    ws_q = wb.create_sheet("Questions")
    _title(ws_q, "Questions application forms asked", "From questions.yaml. Required ones without an answer stop "
                                                       "auto-apply: answer them in that file.")
    _header(ws_q, 4, ["Question", "Your answer", "Required", "Status", "Asked by"], [70, 50, 10, 16, 30])
    bank = QuestionBank(settings.data_dir / "questions.yaml")
    for i, q in enumerate(bank.items, start=5):
        status = "Needs your answer" if q.get("answer") in (None, "") else \
                 "Draft: review it" if q.get("reviewed") is False else "Answered"
        for c, v in enumerate([q.get("ask"), q.get("answer") or "", "Yes" if q.get("required") else "No", status,
                               ", ".join(q.get("seen") or [])], start=1):
            cell = ws_q.cell(row=i, column=c, value=v)
            cell.font = BODY_FONT
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    _table(ws_q, "Questions", 4, 4 + len(bank.items), 5)

    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = ws.title not in ("Summary",)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def write_report(settings: JobhuntSettings, out: Path | None = None, today: date | None = None) -> Path:
    today = today or date.today()
    out = out or settings.data_dir / "reports" / f"job-search-report-{today.isoformat()}.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_report(settings, today))
    return out
