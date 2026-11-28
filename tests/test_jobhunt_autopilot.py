from datetime import datetime, timedelta, timezone

import pytest

from jobhunt.autopilot import Autopilot
from jobhunt.mailsync import MailItem, SyncReport, _add_to_review, dismiss_review, link_review, load_state, process, save_state
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker, create_workbook

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _pilot(tmp_path, **kw):
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path), **kw)
    calls = {"mail": 0, "verify": 0, "discover": 0, "tailor": []}

    def starter(kind, result=None):
        def go():
            calls[kind] += 1
            return result
        return go
    p = Autopilot(s, start_mail=starter("mail"), start_check=starter("verify"), start_discover=starter("discover"),
                  queue_tailor=calls["tailor"].append)
    return p, calls, s


def test_runs_each_job_when_due_and_survives_a_restart(tmp_path):
    p, calls, s = _pilot(tmp_path)
    t0 = 1_800_000_000
    assert p.tick(t0) == ["mail", "verify", "discover"]
    assert p.tick(t0 + 3600) == []                          # nothing due an hour later
    assert p.tick(t0 + 2 * 3600 + 1) == ["mail"]            # email every 2 hours
    assert p.tick(t0 + 12 * 3600 + 1) == ["mail", "verify"]
    again, _, _ = _pilot(tmp_path)                           # restart: remembers the last runs
    assert again.tick(t0 + 13 * 3600) == []
    p.set_on(False)
    assert p.tick(t0 + 99 * 3600) == []
    assert _pilot(tmp_path)[0].status()["on"] is False


def test_no_gmail_password_means_try_later(tmp_path):
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path))
    p = Autopilot(s, start_mail=lambda: False, start_check=lambda: None, start_discover=lambda: None,
                  queue_tailor=lambda r: None)
    assert p.tick(1_800_000_000) == ["verify", "discover"] and "mail" not in p.state["last"]


def test_tailors_only_the_best_fresh_matches_in_your_places_within_the_daily_limit(tmp_path):
    p, calls, _ = _pilot(tmp_path, auto_tailor_per_day=2, auto_tailor_min_score=70, fresh_job_days=30)
    leads = [{"row": 2, "score": 90, "where_rank": 1, "age": 3, "decision": "New"},
             {"row": 3, "score": 95, "where_rank": 4, "age": 3, "decision": "New"},    # wider search: other city
             {"row": 4, "score": 60, "where_rank": 0, "age": 1, "decision": "New"},    # match below 70%
             {"row": 5, "score": 80, "where_rank": 0, "age": 45, "decision": "New"},   # too old
             {"row": 6, "score": 85, "where_rank": 2, "age": 2, "decision": "New"},
             {"row": 7, "score": 75, "where_rank": 3, "age": 1, "decision": "New"}]
    now = datetime(2026, 11, 28, 10).timestamp()
    assert p.after_discover(leads, now) == [2, 6] and calls["tailor"] == [2, 6]
    assert p.after_discover(leads, now + 60) == []           # today's 2 are used
    assert p.after_discover([leads[-1]], now + 86400) == [7]  # tomorrow: a fresh allowance


# ---------- emails it couldn't place ----------

def mail(sender, subject, body="", mid="<1@x>"):
    return MailItem(message_id=mid, sender=sender, subject=subject, body=body,
                    received=datetime(2026, 11, 30, 10, tzinfo=timezone.utc))


def test_link_an_email_once_and_its_domain_matches_from_then_on(tmp_path):
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path))
    create_workbook(s.tracker_path)
    t = Tracker(s.tracker_path)
    t.add_application(company="Zeta Labs", role="SDE", status="Applied", date_applied=datetime(2026, 11, 25).date())
    t.save()
    first = mail("Priya <priya@talentcrew.io>", "Interview invitation", "We'd like to schedule an interview with you.")
    report = SyncReport(unmatched=[first])
    state = load_state(tmp_path / "mail_state.json")
    _add_to_review(state, report)
    save_state(tmp_path / "mail_state.json", state)

    out = link_review(s, "<1@x>", "A001")
    assert out["remembered"] == "talentcrew.io" and out["status"] == "Interviewing"
    assert Tracker(s.tracker_path).find("A001")["email_received"] == "Yes"
    state = load_state(tmp_path / "mail_state.json")
    assert state["review"] == [] and state["domains"] == {"talentcrew.io": "A001"}

    # The next email from that domain is placed by itself.
    t = Tracker(s.tracker_path)
    rep = process(t, [mail("Priya <priya@talentcrew.io>", "Next round", "Please book a slot.", "<2@x>")],
                  domains=state["domains"])
    assert [c.app_id for c in rep.changes] == ["A001"]


def test_shared_domains_are_never_remembered(tmp_path):
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path))
    create_workbook(s.tracker_path)
    t = Tracker(s.tracker_path)
    t.add_application(company="Acme", role="SDE", status="Applied")
    t.save()
    state = load_state(tmp_path / "mail_state.json")
    _add_to_review(state, SyncReport(unmatched=[mail("no-reply@us.greenhouse-mail.io", "Thank you for applying")]))
    save_state(tmp_path / "mail_state.json", state)
    assert link_review(s, "<1@x>", "A001")["remembered"] == ""
    state = load_state(tmp_path / "mail_state.json")
    _add_to_review(state, SyncReport(unmatched=[mail("x@y.com", "Hello", mid="<9@x>")]))
    save_state(tmp_path / "mail_state.json", state)
    dismiss_review(s, "<9@x>")
    assert load_state(tmp_path / "mail_state.json")["review"] == []


def test_dashboard_endpoints_for_autopilot_and_email_review(tmp_path):
    from fastapi.testclient import TestClient

    from jobhunt.server import create_app

    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path))
    create_workbook(s.tracker_path)
    t = Tracker(s.tracker_path)
    t.add_application(company="Zeta Labs", role="SDE", status="Applied")
    t.save()
    state = load_state(tmp_path / "mail_state.json")
    _add_to_review(state, SyncReport(unmatched=[mail("Priya <priya@talentcrew.io>", "Interview invitation", "Let's talk")]))
    save_state(tmp_path / "mail_state.json", state)
    c = TestClient(create_app(s, checker=lambda *a, **k: []))
    assert c.post("/api/autopilot", json={"on": False}).json()["on"] is False
    assert c.get("/api/mail-review").json()["items"][0]["subject"] == "Interview invitation"
    r = c.post("/api/mail-review/link", json={"message_id": "<1@x>", "app_id": "A001"})
    assert r.status_code == 200 and r.json()["remembered"] == "talentcrew.io"
    assert c.get("/api/mail-review").json()["items"] == []
    assert c.post("/api/mail-review/link", json={"message_id": "<1@x>", "app_id": "A001"}).status_code == 404
