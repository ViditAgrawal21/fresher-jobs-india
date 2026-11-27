"""The Excel tracker: one workbook holding applications, leads, the email log and a live dashboard.

The workbook is the system of record and stays editable by hand in Excel. Code only
appends rows and fills specific cells, and saves atomically. Charts are avoided on
purpose: openpyxl drops charts when it re-saves a workbook, while formulas,
validation and conditional formatting survive.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=10)
BODY_FONT = Font(name=FONT, size=10)
INPUT_FONT = Font(name=FONT, size=10, color="0000FF")
TITLE_FONT = Font(name=FONT, bold=True, size=14)
BOLD = Font(name=FONT, bold=True, size=10)
THIN = Side(style="thin", color="BFBFBF")
DATE_FMT = "dd-mmm-yyyy"
FORMULA_ROWS = 1000  # rows pre-filled with formulas / validation so hand-typed rows work too

# Funnel order. Higher = further along. Terminal statuses are handled separately.
# "Replied" = a person answered (set by hand or from the dashboard); "Acknowledged" = an automatic receipt.
STATUSES = ["Lead", "Tailored", "Applied", "Acknowledged", "Viewed", "Replied", "Assessment", "Interviewing",
            "Offer", "Accepted", "Rejected", "Ghosted", "Withdrawn"]
STAGE = {"Lead": 0, "Tailored": 1, "Applied": 2, "Acknowledged": 3, "Viewed": 3, "Replied": 3, "Assessment": 4,
         "Interviewing": 5, "Offer": 6, "Accepted": 7}
TERMINAL = {"Accepted", "Rejected", "Withdrawn"}
SENT = [s for s in STATUSES if s not in ("Lead", "Tailored")]
TRACK_LABELS = ["Backend", "Full Stack", "AI Engineer", "Mobile", "Founding", "Other"]
SOURCES = ["Naukri", "LinkedIn", "Company site", "Referral", "Instahyre", "Wellfound", "Cutshort", "Hirist",
           "Foundit", "YC Work at a Startup", "Email / cold", "Discover feed", "Other"]
SIGNALS = ["Acknowledged", "Viewed", "Assessment", "Interview", "Offer", "Rejected", "Other"]
LEAD_DECISIONS = ["New", "Apply", "Applied", "Skip"]

# (header, width, key). `key` is how code refers to the column.
APP_COLUMNS = [
    ("ID", 7, "id"),
    ("Date Added", 12, "date_added"),
    ("Company", 20, "company"),
    ("Role", 28, "role"),
    ("Track", 11, "track"),
    ("Location", 14, "location"),
    ("Work Mode", 10, "work_mode"),
    ("Source", 13, "source"),
    ("Job URL", 30, "url"),
    ("Exp Required", 11, "exp_required"),
    ("CTC Range (LPA)", 12, "ctc"),
    ("Fit Score", 8, "fit"),
    ("Match %", 8, "match"),
    ("Matched Keywords", 30, "matched"),
    ("Missing Keywords", 24, "missing"),
    ("Resume File", 34, "resume"),
    ("Status", 13, "status"),
    ("Date Applied", 12, "date_applied"),
    ("Applied With (email)", 24, "applied_with"),
    ("Referral", 14, "referral"),
    ("Email Received?", 10, "email_received"),
    ("Last Email Date", 12, "last_email_date"),
    ("Last Email From", 24, "last_email_from"),
    ("Last Email Subject", 36, "last_email_subject"),
    ("Email Signal", 12, "email_signal"),
    ("Next Step", 22, "next_step"),
    ("Next Step Date", 12, "next_step_date"),
    ("Days Since Applied", 9, "days_since"),
    ("Follow-up", 11, "followup"),
    ("Recruiter / Contact", 20, "contact"),
    ("Notes", 40, "notes"),
    # Filled by `verify`: when the company first published the job, and whether it's still open.
    ("Job Posted On", 12, "posted"),
    ("Job Still Open?", 11, "posting"),
    ("Last Checked", 12, "checked"),
]
COL = {key: i + 1 for i, (_, _, key) in enumerate(APP_COLUMNS)}
DATE_KEYS = {"date_added", "date_applied", "last_email_date", "next_step_date", "posted", "checked"}
FORMULA_KEYS = {"days_since", "followup"}

LEAD_COLUMNS = [
    ("Found On", 12, "found"), ("Company", 20, "company"), ("Title", 34, "title"), ("Location", 18, "location"),
    ("Exp Required", 10, "exp"), ("Match %", 8, "score"), ("Track", 11, "track"), ("Matched Skills", 36, "matched"),
    ("Missing Skills", 26, "missing"), ("Source", 12, "source"), ("URL", 36, "url"), ("Posted", 12, "posted"),
    ("Decision", 10, "decision"), ("Lead Key", 30, "key"), ("Employees (approx)", 11, "employees"),
]
LCOL = {key: i + 1 for i, (_, _, key) in enumerate(LEAD_COLUMNS)}

MAIL_COLUMNS = [
    ("Received", 17, "received"), ("From", 30, "from"), ("Subject", 50, "subject"), ("App ID", 8, "app_id"),
    ("Company", 18, "company"), ("Signal", 12, "signal"), ("Action", 40, "action"), ("Message-ID", 40, "message_id"),
]

# Weekly targets from the Dec-Mar playbook (blue = inputs you can change).
WEEK_TARGETS = [(date(2026, 11, 30), 5)] + [(date(2026, 12, 7) + timedelta(weeks=i), 20) for i in range(4)] \
    + [(date(2027, 1, 4) + timedelta(weeks=i), 25) for i in range(4)] \
    + [(date(2027, 2, 1) + timedelta(weeks=i), 20) for i in range(4)] \
    + [(date(2027, 3, 1) + timedelta(weeks=i), 10) for i in range(5)]

STATUS_COLORS = {
    "Applied": "DDEBF7", "Acknowledged": "E2EFDA", "Viewed": "E2EFDA", "Replied": "E2EFDA", "Assessment": "FFF2CC",
    "Interviewing": "FCE4D6", "Offer": "C6EFCE", "Accepted": "A9D08E", "Rejected": "F4CCCC",
    "Ghosted": "EDEDED", "Withdrawn": "EDEDED", "Tailored": "F2F2F2",
}

# A starter target list. Boards marked "feed" are fetched by `python -m jobhunt discover`.
TARGETS = [
    ("Razorpay", "Fintech, product", "Node/Go backends, payments", "https://boards.greenhouse.io/razorpaysoftwareprivatelimited", "feed"),
    ("Groww", "Fintech, product", "Large-scale backend + web", "https://boards.greenhouse.io/groww", "feed"),
    ("CRED", "Fintech, product", "High bar, high pay", "https://jobs.lever.co/cred", "feed"),
    ("Meesho", "E-commerce, product", "Backend and full stack at scale", "https://jobs.lever.co/meesho", "feed"),
    ("Zeta", "Fintech, product", "Backend and platform roles", "https://jobs.lever.co/zeta", "feed"),
    ("Swiggy", "Consumer, product", "Backend, mobile and full stack", "https://jobs.smartrecruiters.com/Swiggy", "feed"),
    ("Sarvam AI", "AI-first", "Indian LLMs; AI engineer roles", "https://jobs.ashbyhq.com/sarvam", "feed"),
    ("Composio", "AI-first", "Agent tooling, MCP; TS/Python", "https://jobs.ashbyhq.com/composio", "feed"),
    ("Plane", "Dev tools, open source", "Full stack (React, Python)", "https://jobs.ashbyhq.com/plane", "feed"),
    ("Atlan", "Data SaaS", "Backend and full stack", "https://jobs.ashbyhq.com/atlan", "feed"),
    ("Glance", "Consumer AI", "Backend, AI products", "https://boards.greenhouse.io/glance", "feed"),
    ("HackerRank", "Dev tools", "Full stack, AI features", "https://boards.greenhouse.io/hackerrank", "feed"),
    ("Observe.AI", "AI-first", "LLM products for contact centres", "https://boards.greenhouse.io/observeai", "feed"),
    ("Druva", "SaaS, GCC-style", "Backend and cloud", "https://boards.greenhouse.io/druva", "feed"),
    ("Sigmoid", "Data engineering", "Data and backend roles", "https://boards.greenhouse.io/sigmoid", "feed"),
    ("Paytm", "Fintech, product", "190 India openings when probed", "https://jobs.lever.co/paytm", "feed"),
    ("MongoDB", "Databases, GCC", "Backend and cloud roles in Gurugram/Bengaluru", "https://boards.greenhouse.io/mongodb", "feed"),
    ("Databricks", "Data + AI, GCC", "High pay; backend and platform", "https://boards.greenhouse.io/databricks", "feed"),
    ("Hevo Data", "Data SaaS", "Backend and data engineering", "https://jobs.lever.co/hevodata", "feed"),
    ("InMobi", "Adtech, product", "Backend at scale", "https://boards.greenhouse.io/inmobi", "feed"),
    ("Mindtickle", "SaaS", "Full stack and backend", "https://jobs.lever.co/mindtickle", "feed"),
    ("Twilio", "Communications, GCC", "Backend and platform", "https://boards.greenhouse.io/twilio", "feed"),
    ("FamPay", "Fintech", "Mobile and backend", "https://jobs.lever.co/fampay", "feed"),
    ("Truecaller", "Consumer", "Mobile and backend", "https://boards.greenhouse.io/truecaller", "feed"),
    ("Postman", "Dev tools", "Node.js-heavy product company", "", ""),
    ("BrowserStack", "Dev tools", "Full stack, Node.js", "", ""),
    ("Freshworks", "SaaS", "Full stack and backend", "", ""),
    ("Chargebee", "SaaS", "Backend, billing systems", "", ""),
    ("Juspay", "Fintech", "Payments, functional programming", "", ""),
    ("Setu", "Fintech infra", "APIs and backend", "", ""),
    ("PhonePe", "Fintech", "Backend and mobile", "", ""),
    ("Zomato / Blinkit", "Consumer", "Backend and mobile", "", ""),
    ("Hasura", "Dev tools", "GraphQL, TypeScript", "", ""),
]


def _header(ws, columns, row=1):
    for i, (name, width, _) in enumerate(columns, start=1):
        c = ws.cell(row=row, column=i, value=name)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[row].height = 30
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    ws.auto_filter.ref = f"A{row}:{get_column_letter(len(columns))}{row}"


def _list_validation(ws, values, col_letter, first=2, last=FORMULA_ROWS + 1, source=None):
    formula = source or '"' + ",".join(values) + '"'
    dv = DataValidation(type="list", formula1=formula, allow_blank=True, showDropDown=False)
    dv.add(f"{col_letter}{first}:{col_letter}{last}")
    ws.add_data_validation(dv)


def _app_formulas(r: int, col: dict[str, int] = COL) -> dict[str, str]:
    st, applied, mail = (f"{get_column_letter(col[k])}{r}" for k in ("status", "date_applied", "email_received"))
    return {
        "days_since": f'=IF({applied}="","",TODAY()-{applied})',
        "followup": (f'=IF(AND(OR({st}="Applied",{st}="Viewed"),{applied}<>"",TODAY()-{applied}>=Lists!$E$2,'
                     f'{mail}<>"Yes"),"Follow up","")'),
    }


def create_workbook(path: Path) -> None:
    wb = Workbook()

    # Lists (dropdown sources + settings). Kept visible but last.
    lists = wb.active
    lists.title = "Lists"

    ws = wb.create_sheet("Applications", 0)
    _header(ws, APP_COLUMNS)
    for r in range(2, FORMULA_ROWS + 2):
        for key, formula in _app_formulas(r).items():
            c = ws.cell(row=r, column=COL[key], value=formula)
            c.font = BODY_FONT
            if key == "days_since":
                c.number_format = "0"
        for key in DATE_KEYS:
            ws.cell(row=r, column=COL[key]).number_format = DATE_FMT
    L = {k: get_column_letter(v) for k, v in COL.items()}
    _list_validation(ws, STATUSES, L["status"], source=f"Lists!$A$2:$A${len(STATUSES) + 1}")
    _list_validation(ws, TRACK_LABELS, L["track"], source=f"Lists!$B$2:$B${len(TRACK_LABELS) + 1}")
    _list_validation(ws, SOURCES, L["source"], source=f"Lists!$C$2:$C${len(SOURCES) + 1}")
    _list_validation(ws, ["Yes", "No"], L["email_received"])
    _list_validation(ws, SIGNALS, L["email_signal"], source=f"Lists!$D$2:$D${len(SIGNALS) + 1}")
    _list_validation(ws, ["Onsite", "Hybrid", "Remote"], L["work_mode"])
    rng = f"A2:{get_column_letter(len(APP_COLUMNS))}{FORMULA_ROWS + 1}"
    for status, color in STATUS_COLORS.items():
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f'${L["status"]}2="{status}"'],
                                                       fill=PatternFill("solid", fgColor=color)))
    ws.conditional_formatting.add(f'{L["followup"]}2:{L["followup"]}{FORMULA_ROWS + 1}',
                                  CellIsRule(operator="equal", formula=['"Follow up"'],
                                             font=Font(name=FONT, bold=True, color="C00000")))

    # Leads
    leads = wb.create_sheet("Leads", 1)
    _header(leads, LEAD_COLUMNS)
    LL = {k: get_column_letter(v) for k, v in LCOL.items()}
    _list_validation(leads, LEAD_DECISIONS, LL["decision"])
    leads.conditional_formatting.add(f'{LL["score"]}2:{LL["score"]}{FORMULA_ROWS + 1}',
                                     DataBarRule(start_type="num", start_value=0, end_type="num", end_value=100,
                                                 color="5B9BD5"))

    # Email log
    mail = wb.create_sheet("Email Log", 2)
    _header(mail, MAIL_COLUMNS)

    _dashboard(wb.create_sheet("Dashboard", 0))
    _targets(wb.create_sheet("Targets", 3))
    _how_to(wb.create_sheet("How to use"))

    # Lists content
    lists["A1"], lists["B1"], lists["C1"], lists["D1"] = "Status", "Track", "Source", "Email Signal"
    lists["E1"] = "Follow up after (days)"
    for i, v in enumerate(STATUSES, start=2):
        lists.cell(row=i, column=1, value=v)
    for i, v in enumerate(TRACK_LABELS, start=2):
        lists.cell(row=i, column=2, value=v)
    for i, v in enumerate(SOURCES, start=2):
        lists.cell(row=i, column=3, value=v)
    for i, v in enumerate(SIGNALS, start=2):
        lists.cell(row=i, column=4, value=v)
    for row in lists.iter_rows(min_row=2):
        for c in row:
            c.font = BODY_FONT
    lists["E2"] = 7
    lists["E2"].font = INPUT_FONT
    lists["E2"].comment = Comment("Days after applying with no email before a row shows 'Follow up'. Change freely.", "jobhunt")
    for c in "ABCDE":
        lists.column_dimensions[c].width = 22
        lists[f"{c}1"].font = BOLD
    wb.move_sheet("Lists", offset=len(wb.sheetnames) - 1 - wb.sheetnames.index("Lists"))
    _save(wb, path)


def _dashboard(ws) -> None:
    A = "Applications"
    st = f"{A}!${get_column_letter(COL['status'])}:${get_column_letter(COL['status'])}"
    co = f"{A}!${get_column_letter(COL['company'])}:${get_column_letter(COL['company'])}"
    em = f"{A}!${get_column_letter(COL['email_received'])}:${get_column_letter(COL['email_received'])}"
    ap = f"{A}!${get_column_letter(COL['date_applied'])}:${get_column_letter(COL['date_applied'])}"
    src = f"{A}!${get_column_letter(COL['source'])}:${get_column_letter(COL['source'])}"
    trk = f"{A}!${get_column_letter(COL['track'])}:${get_column_letter(COL['track'])}"
    fu = f"{A}!${get_column_letter(COL['followup'])}:${get_column_letter(COL['followup'])}"

    ws["A1"] = "Job Search Dashboard"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Everything here is a formula over the Applications sheet. Edit the blue weekly targets if your plan changes."
    ws["A2"].font = Font(name=FONT, italic=True, size=9, color="595959")

    sent = "+".join(f'COUNTIF({st},"{s}")' for s in SENT)
    interviews = "+".join(f'COUNTIF({st},"{s}")' for s in ("Assessment", "Interviewing", "Offer", "Accepted"))
    kpis = [
        ("Tracked rows", f'=COUNTIF({co},"?*")-1', "0"),
        ("Applications sent", f"={sent}", "0"),
        ("Replies received (any email)", f'=COUNTIF({em},"Yes")', "0"),
        ("Reply rate", "=IF(B5=0,0,B6/B5)", "0.0%"),
        ("Assessments + interviews + offers", f"={interviews}", "0"),
        ("Interview rate", "=IF(B5=0,0,B8/B5)", "0.0%"),
        ("Offers", f'=COUNTIF({st},"Offer")+COUNTIF({st},"Accepted")', "0"),
        ("Rejected", f'=COUNTIF({st},"Rejected")', "0"),
        ("Follow-ups due", f'=COUNTIF({fu},"Follow up")', "0"),
        ("Sent this week", f'=COUNTIFS({ap},">="&(TODAY()-WEEKDAY(TODAY(),3)),{ap},"<"&(TODAY()-WEEKDAY(TODAY(),3)+7))', "0"),
    ]
    ws["A3"], ws["B3"] = "Metric", "Value"
    for c in ("A3", "B3"):
        ws[c].font, ws[c].fill = HEADER_FONT, HEADER_FILL
    for i, (label, formula, fmt) in enumerate(kpis, start=4):
        ws.cell(row=i, column=1, value=label).font = BODY_FONT
        c = ws.cell(row=i, column=2, value=formula)
        c.font, c.number_format = BOLD, fmt

    # Status breakdown
    ws["D3"], ws["E3"] = "Status", "Count"
    for c in ("D3", "E3"):
        ws[c].font, ws[c].fill = HEADER_FONT, HEADER_FILL
    for i, s in enumerate(STATUSES, start=4):
        ws.cell(row=i, column=4, value=s).font = BODY_FONT
        ws.cell(row=i, column=5, value=f'=COUNTIF({st},D{i})').font = BODY_FONT
    ws.conditional_formatting.add(f"E4:E{3 + len(STATUSES)}", DataBarRule(start_type="min", end_type="max", color="5B9BD5"))

    # By source
    ws["G3"], ws["H3"], ws["I3"], ws["J3"] = "Source", "Sent", "Replies", "Reply rate"
    for c in ("G3", "H3", "I3", "J3"):
        ws[c].font, ws[c].fill = HEADER_FONT, HEADER_FILL
    for i, s in enumerate(SOURCES, start=4):
        ws.cell(row=i, column=7, value=s).font = BODY_FONT
        ws.cell(row=i, column=8, value=f'=COUNTIFS({src},G{i},{ap},"<>")').font = BODY_FONT
        ws.cell(row=i, column=9, value=f'=COUNTIFS({src},G{i},{em},"Yes")').font = BODY_FONT
        c = ws.cell(row=i, column=10, value=f"=IF(H{i}=0,0,I{i}/H{i})")
        c.font, c.number_format = BODY_FONT, "0%"

    # By track
    start = 6 + len(SOURCES)
    ws.cell(row=start, column=7, value="Track")
    ws.cell(row=start, column=8, value="Sent")
    ws.cell(row=start, column=9, value="Replies")
    ws.cell(row=start, column=10, value="Interviews")
    for col in range(7, 11):
        ws.cell(row=start, column=col).font = HEADER_FONT
        ws.cell(row=start, column=col).fill = HEADER_FILL
    for i, t in enumerate(TRACK_LABELS, start=start + 1):
        ws.cell(row=i, column=7, value=t).font = BODY_FONT
        ws.cell(row=i, column=8, value=f'=COUNTIFS({trk},G{i},{ap},"<>")').font = BODY_FONT
        ws.cell(row=i, column=9, value=f'=COUNTIFS({trk},G{i},{em},"Yes")').font = BODY_FONT
        ws.cell(row=i, column=10, value="=" + "+".join(
            f'COUNTIFS({trk},G{i},{st},"{s}")' for s in ("Assessment", "Interviewing", "Offer", "Accepted"))).font = BODY_FONT

    # Weekly plan vs actual
    wstart = 17
    heads = ["Week of (Mon)", "Target", "Sent", "Gap"]
    for j, h in enumerate(heads, start=1):
        c = ws.cell(row=wstart, column=j, value=h)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
    for i, (monday, target) in enumerate(WEEK_TARGETS, start=wstart + 1):
        d = ws.cell(row=i, column=1, value=monday)
        d.number_format, d.font = DATE_FMT, BODY_FONT
        t = ws.cell(row=i, column=2, value=target)
        t.font = INPUT_FONT
        ws.cell(row=i, column=3, value=f'=COUNTIFS({ap},">="&A{i},{ap},"<"&(A{i}+7))').font = BODY_FONT
        g = ws.cell(row=i, column=4, value=f'=IF(A{i}>TODAY(),"",C{i}-B{i})')  # only weeks that have started
        g.font, g.number_format = BODY_FONT, "+0;-0;0"
    last = wstart + len(WEEK_TARGETS)
    ws.cell(row=wstart, column=2).comment = Comment(
        "Targets from the Job Switch Playbook: 5 in setup week, 20/week in Dec, 25/week in Jan, "
        "20/week in Feb, 10/week in Mar. Blue = input, change freely.", "jobhunt")
    ws.conditional_formatting.add(f"C{wstart + 1}:C{last}", DataBarRule(start_type="num", start_value=0,
                                                                         end_type="num", end_value=30, color="70AD47"))
    ws.conditional_formatting.add(f"D{wstart + 1}:D{last}", CellIsRule(operator="lessThan", formula=["0"],
                                                                       font=Font(name=FONT, color="C00000")))
    tot = last + 1
    ws.cell(row=tot, column=1, value="Total").font = BOLD
    ws.cell(row=tot, column=2, value=f"=SUM(B{wstart + 1}:B{last})").font = BOLD
    ws.cell(row=tot, column=3, value=f"=SUM(C{wstart + 1}:C{last})").font = BOLD
    ws.cell(row=tot, column=4, value=f"=SUM(D{wstart + 1}:D{last})").font = BOLD
    ws.cell(row=tot, column=4).number_format = "+0;-0;0"

    for col, width in zip("ABCDEFGHIJ", (34, 12, 2, 14, 9, 2, 22, 9, 9, 11)):
        ws.column_dimensions[col].width = width
    ws.column_dimensions["C"].width = 9
    ws.column_dimensions["D"].width = 14


def _targets(ws) -> None:
    cols = [("Company", 22, "c"), ("Category", 20, "cat"), ("Why", 34, "why"), ("Careers / job board", 48, "url"),
            ("In discover feed", 10, "feed"), ("Referral contact", 22, "ref"), ("Status", 14, "st"), ("Notes", 30, "n")]
    _header(ws, cols)
    for r, (company, cat, why, url, feed) in enumerate(TARGETS, start=2):
        for col, v in enumerate((company, cat, why, url, "Yes" if feed else ""), start=1):
            ws.cell(row=r, column=col, value=v).font = BODY_FONT
    _list_validation(ws, ["Researching", "Asked for referral", "Applied", "Not hiring", "Skip"], "G", last=200)
    ws.cell(row=1, column=4).comment = Comment(
        "Rows marked 'feed' are the ATS boards python -m jobhunt discover checks. Others: find their careers page "
        "and add more. Add a company to the feed in pipeline/sources/boards.py.", "jobhunt")


def _how_to(ws) -> None:
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 110
    rows = [
        ("How this tracker works", ""),
        ("", ""),
        ("Sheet", "What it's for"),
        ("Applications", "One row per job. The `jobhunt apply` command adds rows; you can also type rows by hand."),
        ("Leads", "Jobs found by `jobhunt discover`, best match first. Set Decision to Apply, then run `jobhunt apply --lead <row>`."),
        ("Email Log", "Every recruiting email `jobhunt sync-mail` matched to an application, and what it changed."),
        ("Dashboard", "Live counts, reply and interview rates, and weekly targets vs actual. Formulas only."),
        ("Targets", "Companies worth a referral ask. Track who you asked and when."),
        ("Lists", "Dropdown values and the follow-up threshold (Lists!E2, default 7 days)."),
        ("", ""),
        ("Columns you fill in", "Status (after you submit: Applied), Date Applied, Applied With (email), Referral, Next Step, "
                                "Next Step Date, Recruiter / Contact, Notes, CTC Range."),
        ("Filled automatically", "ID, Date Added, Company, Role, Track, Job URL, Fit Score, Match %, Matched/Missing Keywords, Resume File (by `apply`); "
                                 "Email Received?, Last Email Date/From/Subject, Email Signal and Status upgrades (by `sync-mail`)."),
        ("Formulas, don't type", "Days Since Applied, Follow-up (shows 'Follow up' after 7 days with no email)."),
        ("", ""),
        ("Status meanings", "Lead = found, not tailored · Tailored = resume ready, not sent · Applied = submitted · "
                            "Acknowledged/Viewed = they replied or opened it · Assessment = test/OA · Interviewing = "
                            "any interview round · Offer · Accepted · Rejected · Ghosted (no reply after ~3 weeks) · Withdrawn"),
        ("Email sync rules", "sync-mail only moves a status forward (never back), never sets Accepted, and logs a conflict "
                             "instead of overwriting an Offer. It needs a Gmail App Password in .env."),
        ("", ""),
        ("Example row (format only)", ""),
    ]
    for r, (a, b) in enumerate(rows, start=1):
        ws.cell(row=r, column=1, value=a).font = BOLD if a and not b or r == 3 else BODY_FONT
        c = ws.cell(row=r, column=2, value=b)
        c.font = BOLD if r == 3 else BODY_FONT
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws["A1"].font = TITLE_FONT
    ex_row = len(rows) + 1
    example = {
        "id": "A001", "date_added": date(2026, 12, 8), "company": "Example Fintech", "role": "SDE 2 - Backend (Node.js)",
        "track": "Backend", "location": "Bengaluru", "work_mode": "Hybrid", "source": "LinkedIn",
        "url": "https://example.com/jobs/123", "exp_required": "2+ years", "ctc": "14-20", "fit": 78, "match": 75,
        "matched": "Node.js, TypeScript, NestJS, PostgreSQL, Redis, AWS", "missing": "Kafka, GraphQL",
        "resume": "applications/A001_example-fintech_sde-2-backend/Vidit_Agrawal_Resume.pdf", "status": "Interviewing",
        "date_applied": date(2026, 12, 8), "applied_with": "you@gmail.com", "referral": "Priya (ex-PCCOE)",
        "email_received": "Yes", "last_email_date": date(2026, 12, 15), "last_email_from": "talent@example.com",
        "last_email_subject": "Interview invitation: SDE 2 Backend", "email_signal": "Interview",
        "next_step": "Round 1: DSA + LLD", "next_step_date": date(2026, 12, 19), "days_since": 18, "followup": "",
        "contact": "Ankit, Talent team", "notes": "Asked about notice period; said 30 days",
    }
    for i, (name, width, key) in enumerate(APP_COLUMNS, start=1):
        h = ws.cell(row=ex_row, column=i, value=name)
        h.font, h.fill = HEADER_FONT, HEADER_FILL
        v = ws.cell(row=ex_row + 1, column=i, value=example.get(key))
        v.font = BODY_FONT
        if key in DATE_KEYS:
            v.number_format = DATE_FMT
        if i > 2:
            ws.column_dimensions[get_column_letter(i)].width = max(width, 12)


def _save(wb, path: Path) -> None:
    """Write to a temp file, then replace, so a crash never leaves half a workbook."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        wb.save(tmp)
        os.replace(tmp, path)
    except PermissionError as exc:
        os.unlink(tmp)
        raise RuntimeError(f"Can't write {path}: close it in Excel and run the command again.") from exc


# ---------- reading and updating ----------

@dataclass
class AppRow:
    row: int
    values: dict

    def __getitem__(self, key):
        return self.values.get(key)


class Tracker:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            create_workbook(self.path)
        self.wb = load_workbook(self.path)
        self.apps = self.wb["Applications"]
        self.leads = self.wb["Leads"]
        self.mail = self.wb["Email Log"]
        self.col = self._column_map()
        self._upgrade_status_list()
        for name, width, key in LEAD_COLUMNS:  # columns added to Leads in newer versions
            h = self.leads.cell(row=1, column=LCOL[key])
            if h.value is None:
                h.value, h.font, h.fill = name, HEADER_FONT, HEADER_FILL
                self.leads.column_dimensions[get_column_letter(LCOL[key])].width = width

    def _upgrade_status_list(self) -> None:
        """Workbooks made before a status was added: refresh the Status dropdown source and its range."""
        lists = self.wb["Lists"]
        if [lists.cell(row=i + 2, column=1).value for i in range(len(STATUSES))] == STATUSES:
            return
        for i, v in enumerate(STATUSES, start=2):
            lists.cell(row=i, column=1, value=v).font = BODY_FONT
        for dv in self.apps.data_validations.dataValidation:
            if dv.formula1 and dv.formula1.startswith("Lists!$A$2"):
                dv.formula1 = f"Lists!$A$2:$A${len(STATUSES) + 1}"

    def _column_map(self) -> dict[str, int]:
        """Columns found by header name, so moving columns in Excel doesn't break anything.
        Columns added in newer versions are appended to older workbooks."""
        by_header = {name: key for name, _, key in APP_COLUMNS}
        col: dict[str, int] = {}
        last = 0
        for c in range(1, self.apps.max_column + 1):
            header = self.apps.cell(row=1, column=c).value
            if header:
                last = c
                if header in by_header:
                    col[by_header[header]] = c
        for name, width, key in APP_COLUMNS:
            if key not in col:
                last += 1
                h = self.apps.cell(row=1, column=last, value=name)
                h.font, h.fill = HEADER_FONT, HEADER_FILL
                h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                self.apps.column_dimensions[get_column_letter(last)].width = width
                col[key] = last
        return col

    def save(self) -> None:
        _save(self.wb, self.path)

    # --- applications ---
    def applications(self) -> list[AppRow]:
        out = []
        for r in range(2, self.apps.max_row + 1):
            company = self.apps.cell(row=r, column=self.col["company"]).value
            if not company:
                continue
            values = {key: self.apps.cell(row=r, column=self.col[key]).value for _, _, key in APP_COLUMNS
                      if key not in FORMULA_KEYS}
            out.append(AppRow(r, values))
        return out

    def find(self, app_id: str) -> AppRow | None:
        app_id = app_id.strip().upper()
        return next((a for a in self.applications() if str(a["id"] or "").upper() == app_id), None)

    def _first_empty_row(self) -> int:
        r = 2
        while self.apps.cell(row=r, column=self.col["company"]).value:
            r += 1
        return r

    def next_id(self) -> str:
        nums = [int(str(a["id"])[1:]) for a in self.applications()
                if a["id"] and str(a["id"]).upper().startswith("A") and str(a["id"])[1:].isdigit()]
        return f"A{(max(nums) + 1) if nums else 1:03d}"

    def add_application(self, **values) -> str:
        r = self._first_empty_row()
        app_id = values.pop("id", None) or self.next_id()
        values.setdefault("date_added", date.today())
        values.setdefault("status", "Tailored")
        values.setdefault("email_received", "No")
        self.apps.cell(row=r, column=self.col["id"], value=app_id).font = BODY_FONT
        for key, v in values.items():
            self._set(r, key, v)
        for key, formula in _app_formulas(r, self.col).items():  # rows past the pre-filled block
            if self.apps.cell(row=r, column=self.col[key]).value is None:
                self.apps.cell(row=r, column=self.col[key], value=formula).font = BODY_FONT
        return app_id

    def update(self, app_id: str, **values) -> AppRow:
        app = self.find(app_id)
        if app is None:
            raise KeyError(f"no application with ID {app_id}")
        for key, v in values.items():
            self._set(app.row, key, v)
        return self.find(app_id)

    def move(self, app_id: str, status: str, day: date | None = None, note: str = "", via: str = "dashboard") -> bool:
        """Change an application's stage the way the dashboard does: sets the applied date when it
        leaves Ready, records a reply for later stages, and logs the change in Notes.
        Returns False when nothing changed. Raises KeyError for an unknown ID or status."""
        status = next((x for x in STATUSES if x.lower() == str(status).lower()), None)
        app = self.find(app_id)
        if status is None or app is None:
            raise KeyError(f"unknown status or ID: {app_id} -> {status}")
        day = day or date.today()
        values: dict = {"status": status}
        if status not in ("Lead", "Tailored", "Withdrawn") and not app["date_applied"]:
            values["date_applied"] = day
        if status in ("Replied", "Assessment", "Interviewing", "Offer", "Accepted", "Rejected"):
            values["email_received"] = "Yes"
        if app["status"] == status and all(app[k] == v for k, v in values.items() if k != "status"):
            return False
        self.update(app["id"], **values)
        self.append_note(app["id"], f"[{day:%d %b}] {via}: {app['status']} -> {status}" + (f" ({note})" if note else ""))
        return True

    def append_note(self, app_id: str, note: str) -> None:
        app = self.find(app_id)
        old = app["notes"] or ""
        self._set(app.row, "notes", f"{old}\n{note}".strip())

    def _set(self, r: int, key: str, v) -> None:
        if key not in self.col or key in FORMULA_KEYS:
            raise KeyError(f"unknown or formula column {key!r}")
        if isinstance(v, datetime):
            v = v.date()
        c = self.apps.cell(row=r, column=self.col[key], value=v)
        c.font = BODY_FONT
        if key in DATE_KEYS:
            c.number_format = DATE_FMT
        if key == "url" and v:
            c.hyperlink = str(v)
            c.font = Font(name=FONT, size=10, color="0563C1", underline="single")

    def followups(self, after_days: int = 7, today: date | None = None) -> list[AppRow]:
        today = today or date.today()
        out = []
        for a in self.applications():
            applied = a["date_applied"]
            if isinstance(applied, datetime):
                applied = applied.date()
            if a["status"] in ("Applied", "Viewed") and applied and (today - applied).days >= after_days \
                    and a["email_received"] != "Yes":
                out.append(a)
        return out

    # --- leads ---
    def lead_keys(self) -> set[str]:
        return {str(self.leads.cell(row=r, column=LCOL["key"]).value)
                for r in range(2, self.leads.max_row + 1) if self.leads.cell(row=r, column=LCOL["key"]).value}

    def add_leads(self, leads: list[dict]) -> int:
        known = self.lead_keys() | {str(a["url"]) for a in self.applications() if a["url"]}
        r = 2
        while self.leads.cell(row=r, column=1).value:
            r += 1
        added = 0
        for lead in leads:
            if lead["key"] in known or lead.get("url") in known:
                continue
            for key, v in {**lead, "found": lead.get("found") or date.today(), "decision": lead.get("decision", "New")}.items():
                if key not in LCOL:
                    continue
                c = self.leads.cell(row=r, column=LCOL[key], value=v)
                c.font = BODY_FONT
                if key in ("found", "posted"):
                    c.number_format = DATE_FMT
                if key == "url" and v:
                    c.hyperlink = v
                    c.font = Font(name=FONT, size=10, color="0563C1", underline="single")
            known.add(lead["key"])
            r += 1
            added += 1
        return added

    def lead(self, row: int) -> dict:
        if row < 2 or not self.leads.cell(row=row, column=LCOL["company"]).value:
            raise KeyError(f"no lead on row {row} of the Leads sheet")
        return {key: self.leads.cell(row=row, column=i).value for key, i in LCOL.items()}

    def set_lead_decision(self, row: int, decision: str) -> None:
        self.leads.cell(row=row, column=LCOL["decision"], value=decision).font = BODY_FONT

    # --- email log ---
    def log_email(self, **values) -> None:
        r = self.mail.max_row + 1
        for i, (_, _, key) in enumerate(MAIL_COLUMNS, start=1):
            c = self.mail.cell(row=r, column=i, value=values.get(key))
            c.font = BODY_FONT
            if key == "received":
                c.number_format = "dd-mmm-yyyy hh:mm"
