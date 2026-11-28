from datetime import date, timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook

from jobhunt.report import build_report
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker, create_workbook

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def test_report_has_every_sheet_and_live_formulas(tmp_path):
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path))
    create_workbook(s.tracker_path)
    t = Tracker(s.tracker_path)
    today = date(2026, 11, 28)
    t.add_application(company="Acme", role="SDE", status="Applied", date_applied=today - timedelta(days=9),
                      source="Company site", url="https://x/1")
    t.add_application(company="Beta", role="SDE", status="Interviewing", date_applied=today - timedelta(days=3),
                      source="LinkedIn", url="https://x/2")
    t.add_application(company="Gamma", role="SDE", status="Tailored", url="https://x/3")
    t.save()
    wb = load_workbook(BytesIO(build_report(s, today)))
    assert wb.sheetnames == ["Summary", "Pipeline", "Weekly", "Skills demand", "Response rates", "Applications",
                             "Job matches", "Email log", "Questions"]
    apps = wb["Applications"]
    header = [c.value for c in apps[4]]
    rows = {apps.cell(r, 1).value: {h: apps.cell(r, i + 1).value for i, h in enumerate(header)} for r in range(5, 8)}
    assert rows["A001"]["Sent?"] == "Yes" and rows["A001"]["Follow-up due?"] == "Yes"   # 9 days, no reply
    assert rows["A002"]["Responded?"] == "Yes" and rows["A002"]["Interview stage?"] == "Yes"
    assert rows["A003"]["Sent?"] == "No" and rows["A003"]["Stage"] == "Ready to apply"
    summary = {wb["Summary"].cell(r, 1).value: wb["Summary"].cell(r, 2).value for r in range(5, 17)}
    assert summary["Applications sent"].startswith("=COUNTIF(Applications!")
    assert summary["Response rate"] == "=IFERROR(B8/B7,0)"
    assert sum(len(ws._charts) for ws in wb.worksheets) >= 3
