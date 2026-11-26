from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobhunt.mailsync import MailItem, classify, company_key, next_status, parse_message, process
from jobhunt.tracker import Tracker, create_workbook

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture
def tracker(tmp_path: Path) -> Tracker:
    path = tmp_path / "tracker.xlsx"
    create_workbook(path)
    t = Tracker(path)
    t.add_application(company="Razorpay Software Pvt. Ltd.", role="SDE 2 Backend", status="Applied",
                      date_applied=date.today() - timedelta(days=10))
    t.add_application(company="Groww", role="Full Stack Engineer", status="Applied", date_applied=date.today())
    t.add_application(company="Sarvam AI", role="AI Engineer", status="Offer", date_applied=date.today())
    t.save()
    return Tracker(path)


def mail(sender: str, subject: str, body: str = "", minutes: int = 0) -> MailItem:
    return MailItem(message_id=f"<{subject}-{minutes}@x>", sender=sender, subject=subject, body=body,
                    received=datetime(2026, 12, 1, 10, tzinfo=timezone.utc) + timedelta(minutes=minutes))


# ---------- tracker ----------

def test_ids_followups_and_reopen(tracker: Tracker):
    assert [a["id"] for a in tracker.applications()] == ["A001", "A002", "A003"]
    assert tracker.next_id() == "A004"
    assert [a["company"] for a in tracker.followups(7)] == ["Razorpay Software Pvt. Ltd."]
    tracker.update("a002", status="Interviewing", next_step="Round 1")
    tracker.save()
    again = Tracker(tracker.path)
    assert again.find("A002")["status"] == "Interviewing"
    # validation and formulas survive an openpyxl round trip
    assert again.apps.data_validations.dataValidation
    assert str(again.apps.cell(row=2, column=again.col["days_since"]).value).startswith("=IF(")


def test_leads_are_deduplicated(tracker: Tracker):
    lead = {"key": "lever:cred:1", "company": "CRED", "title": "Backend Engineer", "url": "https://jobs.lever.co/cred/1",
            "score": 71}
    assert tracker.add_leads([lead, lead]) == 1
    assert tracker.add_leads([lead]) == 0
    assert tracker.lead(2)["company"] == "CRED"


# ---------- classification ----------

@pytest.mark.parametrize("subject,body,signal", [
    ("Thank you for applying to Razorpay", "We have received your application.", "Acknowledged"),
    ("Your application for SDE 2", "Unfortunately, we will not be moving forward with your application. "
                                   "Thank you for your interest.", "Rejected"),
    ("Interview invitation: SDE 2 Backend", "We'd like to schedule a technical interview.", "Interview"),
    ("Next steps", "Please complete the HackerRank online assessment within 3 days.", "Assessment"),
    ("Offer letter - Razorpay", "We are pleased to offer you the role.", "Offer"),
    ("Razorpay viewed your application", "", "Viewed"),
    ("Big Diwali sale", "Get 50% off", None),
])
def test_classify(subject, body, signal):
    assert classify(mail("x@y.com", subject, body)) == signal


def test_status_only_moves_forward():
    assert next_status("Applied", "Interview") == ("Interviewing", "")
    assert next_status("Interviewing", "Acknowledged") == (None, "")
    assert next_status("Offer", "Rejected")[0] is None
    assert next_status("Rejected", "Interview")[1].startswith("conflict")
    assert next_status("Accepted", "Offer")[0] is None
    assert next_status("Ghosted", "Interview")[0] == "Interviewing"


def test_company_key():
    assert company_key("Razorpay Software Pvt. Ltd.") == "razorpay"
    assert company_key("Sarvam AI") == "sarvam ai"


# ---------- process ----------

def test_process_updates_matching_rows(tracker: Tracker):
    mails = [
        mail("Razorpay Talent <careers@razorpay.com>", "Interview invitation: SDE 2 Backend",
             "We'd like to schedule a technical interview next week.", 0),
        mail("Groww Hiring <no-reply@groww.in>", "Update on your application",
             "Unfortunately, we will not be moving forward.", 1),
        mail("Sarvam AI <talent@sarvam.ai>", "Your application", "We regret to inform you...", 2),
        mail("Swiggy <offers@swiggy.in>", "Weekend deals", "Order now", 3),
        mail("Some Corp Careers <careers@somecorp.com>", "Interview invitation", "schedule a call", 4),
    ]
    report = process(tracker, mails)
    tracker.save()
    t = Tracker(tracker.path)
    razor, groww, sarvam = (t.find(i) for i in ("A001", "A002", "A003"))
    assert razor["status"] == "Interviewing" and razor["email_received"] == "Yes"
    assert razor["email_signal"] == "Interview"
    assert groww["status"] == "Rejected"
    assert sarvam["status"] == "Offer"  # rejection after an offer is a conflict, not an overwrite
    assert len(report.conflicts) == 1
    assert len(report.unmatched) == 1  # Some Corp isn't tracked
    assert t.mail.max_row == 4  # header + 3 logged emails (the promo is ignored)


def test_dry_run_writes_nothing(tracker: Tracker):
    process(tracker, [mail("careers@razorpay.com", "Interview invitation", "schedule an interview")], dry_run=True)
    assert tracker.find("A001")["status"] == "Applied"


def test_parse_message_reads_html_and_encoded_headers():
    raw = (b"From: =?utf-8?q?Groww_Talent?= <talent@groww.in>\r\nSubject: =?utf-8?q?Interview_invite?=\r\n"
           b"Date: Tue, 01 Dec 2026 10:00:00 +0530\r\nMessage-ID: <abc@groww.in>\r\nContent-Type: text/html\r\n\r\n"
           b"<p>We would like to <b>schedule an interview</b>.</p>")
    m = parse_message(raw)
    assert m.sender.startswith("Groww Talent") and m.subject == "Interview invite"
    assert "schedule an interview" in m.body
    assert classify(m) == "Interview"


@pytest.mark.parametrize("sender,subject,company,role,source", [
    ("LinkedIn <jobs-noreply@linkedin.com>", "Vidit, your application was sent to Postman", "Postman", "(role not in email)", "LinkedIn"),
    ("Naukri <info@naukri.com>", "You applied for Backend Developer at Juspay Technologies", "Juspay Technologies",
     "Backend Developer", "Naukri"),
    ("Chargebee Hiring Team <no-reply@greenhouse.io>", "Thank you for your application", "Chargebee", "(role not in email)",
     "Company site"),
])
def test_application_from_email(sender, subject, company, role, source):
    from jobhunt.mailsync import application_from_email
    got = application_from_email(mail(sender, subject, "We have received your application."))
    assert got == {"company": company, "role": role, "source": source}


def test_confirmation_for_untracked_job_adds_a_row(tracker: Tracker):
    m = mail("LinkedIn <jobs-noreply@linkedin.com>", "Vidit, your application was sent to Postman",
             "Your application was sent to Postman.")
    report = process(tracker, [m])
    assert [n["company"] for _, n in report.added] == ["Postman"]
    row = tracker.find("A004")
    assert row["company"] == "Postman" and row["status"] == "Applied" and row["source"] == "LinkedIn"
    # a promo from an untracked company adds nothing
    process(tracker, [mail("Swiggy <offers@swiggy.in>", "Thank you for your order", "Order received")])
    assert tracker.find("A005") is None
