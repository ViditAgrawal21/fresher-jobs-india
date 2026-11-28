import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from jobhunt import followup, prep
from jobhunt.mailsync import load_state
from jobhunt.profile import load_profile
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker, create_workbook

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")
PROFILE = load_profile(Path(__file__).resolve().parents[1] / "jobhunt" / "profile.yaml")
JD = ("We're hiring a Backend Engineer with 2+ years of experience. You'll build REST APIs in Node.js and TypeScript on "
      "PostgreSQL and Redis, deploy with Docker on AWS, and work on our RAG-based assistant. Kafka experience is a plus. "
      "You care about system design and testing. " * 2)


# ---------- interview prep ----------

def test_offline_sheet_covers_the_jd_with_your_own_stories():
    sheet = prep.offline_sheet(PROFILE, JD, "Acme", "Backend Engineer")
    qs = " ".join(q.question for q in sheet.technical_questions)
    assert "event loop" in qs and "indexes" in qs and "RAG" in qs          # Node.js, PostgreSQL, RAG
    assert any("Kafka" in t and "gap" in t for t in sheet.revise)          # not on the profile: revise first
    assert sheet.stories and all(s.bullet_ids for s in sheet.stories)
    assert sheet.behavioural_questions[0].question == "Tell me about yourself."
    assert any("Why Acme?" == q.question for q in sheet.behavioural_questions)
    md = prep.render_markdown(sheet, company="Acme", role="Backend Engineer", stage="Interviewing", next_step="Round 1",
                              url="https://x/1", made_by="question bank", profile=PROFILE)
    for heading in ("## What they want", "## Your stories", "## Technical questions", "## Revise before the interview",
                    "## Questions to ask them"):
        assert heading in md


def test_prep_page_is_safe_html():
    page = prep.markdown_to_html("# Prep\n- **Bold** <script>alert(1)</script>\n  - nested https://x.io\n- [ ] revise", "t")
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert '<input type="checkbox">' in page and '<a href="https://x.io"' in page and "<strong>Bold</strong>" in page


@pytest.fixture
def settings(tmp_path: Path) -> JobhuntSettings:
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path), anthropic_api_key="", candidate_email="me@example.com",
                        candidate_phone="+91 90000 00000", gmail_address="me@example.com", gmail_app_password="abcdefghijklmnop")
    create_workbook(s.tracker_path)
    t = Tracker(s.tracker_path)
    folder = tmp_path / "applications" / "A001_acme_backend"
    folder.mkdir(parents=True)
    (folder / "Resume.pdf").write_bytes(b"%PDF")
    (folder / "job_description.txt").write_text(JD, encoding="utf-8")
    t.add_application(company="Acme", role="Backend Engineer", status="Applied", resume="applications/A001_acme_backend/Resume.pdf",
                      date_applied=date.today() - timedelta(days=9), matched="Node.js, PostgreSQL, Redis",
                      last_email_from="Priya Sharma <priya@acme.com>")
    t.add_application(company="Beta", role="SDE", status="Applied", date_applied=date.today() - timedelta(days=10),
                      last_email_from="Beta Careers <no-reply@beta.com>")
    t.add_application(company="Gamma", role="SDE", status="Applied", date_applied=date.today() - timedelta(days=2))
    t.save()
    return s


def test_make_prep_writes_the_sheet_and_falls_back_from_claude(settings, monkeypatch):
    from jobhunt.tailor import TailorError

    path = prep.make_prep(settings, "A001")
    assert path.name == "interview_prep.md" and "Acme, Backend Engineer" in path.read_text(encoding="utf-8")
    assert prep.prep_path(settings, Tracker(settings.tracker_path).find("A001").values) == path

    def boom(*a, **k):
        raise TailorError("rate limited")
    monkeypatch.setattr(prep, "claude_sheet", boom)
    again = prep.make_prep(settings, "A001", use_claude=True)
    assert "(question bank)" in again.read_text(encoding="utf-8")
    other = prep.make_prep(settings, "A002")  # no folder yet: one is made for it
    assert other.parent.name.startswith("A002_")


def test_moving_to_interviewing_makes_a_prep_sheet(settings):
    from fastapi.testclient import TestClient

    from jobhunt.server import create_app

    c = TestClient(create_app(settings, checker=lambda *a, **k: []))
    assert c.post("/api/status", json={"id": "A001", "status": "Interviewing"}).status_code == 200
    for _ in range(50):
        if any(t["kind"] == "prep" and t["state"] == "done" for t in c.get("/api/tasks").json()["tasks"]):
            break
        threading.Event().wait(0.1)
    page = c.get("/prep/A001")
    assert page.status_code == 200 and "Technical questions" in page.text
    assert c.get("/api/data").json()["apps"][0]["prep"] == "prep/A001"
    assert c.get("/prep/A003").status_code == 404


# ---------- follow-up drafts ----------

def test_recipient_and_email_text(settings):
    t = Tracker(settings.tracker_path)
    a1, a2 = t.find("A001"), t.find("A002")
    assert followup.recipient(a1) == ("priya@acme.com", "Priya")
    assert followup.recipient(a2) == ("", "")  # never a no-reply address
    msg = followup.compose(a1, settings)
    body = msg.get_content()
    assert msg["To"] == "priya@acme.com" and "Backend Engineer" in msg["Subject"]
    assert body.startswith("Hi Priya,") and "Node.js, PostgreSQL and Redis" in body and "+91 90000 00000" in body
    assert "To" not in followup.compose(a2, settings)


class FakeImap:
    appended: list = []

    def __init__(self, host):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pw):
        pass

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/Entw&APw-rfe"']

    def append(self, box, flags, when, raw):
        FakeImap.appended.append((box, flags, raw))
        return "OK", [b"APPEND completed"]


def test_drafts_go_to_gmail_once_a_week(settings, monkeypatch):
    monkeypatch.setattr(followup.imaplib, "IMAP4_SSL", FakeImap)
    FakeImap.appended.clear()
    assert [a["id"] for a in followup.due(settings)] == ["A001", "A002"]   # A003 is only 2 days old
    done = followup.create_drafts(settings)
    assert [d["id"] for d in done] == ["A001", "A002"]
    box, flags, raw = FakeImap.appended[0]
    assert box == '"[Gmail]/Entw&APw-rfe"' and flags == "(\\Draft)" and b"Following up on my application" in raw
    t = Tracker(settings.tracker_path)
    assert t.find("A001")["next_step"].startswith("Send the follow-up") and "drafted in Gmail" in t.find("A001")["notes"]
    assert set(load_state(settings.data_dir / "mail_state.json")["drafted"]) == {"A001", "A002"}
    assert followup.due(settings) == []                                      # not again this week
    # A week later the first two are due again, and A003 has now waited long enough too.
    assert [a["id"] for a in followup.due(settings, date.today() + timedelta(days=8))] == ["A001", "A002", "A003"]


def test_autopilot_drafts_follow_ups_daily(tmp_path):
    from jobhunt.autopilot import Autopilot

    runs = []
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path))
    p = Autopilot(s, start_mail=lambda: None, start_check=lambda: None, start_discover=lambda: None,
                  queue_tailor=lambda r: None, start_followups=lambda: runs.append(1))
    t0 = 1_800_000_000
    assert "followups" in p.tick(t0) and p.tick(t0 + 3600) == []
    assert "followups" in p.tick(t0 + 24 * 3600 + 1) and len(runs) == 2
