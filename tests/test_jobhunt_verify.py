from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from core.models import RawPost
from jobhunt import verify
from jobhunt.discover import score_post
from jobhunt.profile import load_profile
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker, create_workbook

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")
TODAY = date(2026, 11, 27)


def board(routes: dict):
    """An httpx client whose responses come from `routes` (url prefix -> (status, json))."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for prefix, (status, body) in routes.items():
            if url.startswith(prefix):
                return httpx.Response(status, json=body)
        raise httpx.ConnectError("offline", request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("url,key,want", [
    ("https://job-boards.greenhouse.io/gitlab/jobs/123", "", ("greenhouse", "gitlab", "123")),
    ("https://job-boards.greenhouse.io/embed/job_app?for=sigmoid&token=9", "", ("greenhouse", "sigmoid", "9")),
    ("https://www.mongodb.com/careers/job/?gh_jid=8083366", "greenhouse:mongodb:8083366", ("greenhouse", "mongodb", "8083366")),
    ("https://jobs.lever.co/meesho/abc-def/apply", "", ("lever", "meesho", "abc-def")),
    ("https://jobs.ashbyhq.com/notion/uuid-1", "", ("ashby", "notion", "uuid-1")),
    ("https://jobs.smartrecruiters.com/BoschGroup/744000160600719-software-engineer", "", ("smartrecruiters", "BoschGroup", "744000160600719")),
    ("https://fa-x.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/4658?utm=x", "",
     ("oracle", "fa-x.fa.ocs.oraclecloud.com|CX_1", "4658")),
    ("https://www.linkedin.com/jobs/view/1", "", None),
])
def test_ats_ref(url, key, want):
    assert verify.ats_ref(url, key) == want


def test_greenhouse_open_closed_and_deadline():
    gh = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/"
    c = board({gh + "1": (200, {"first_published": "2026-11-20T10:00:00-05:00", "application_deadline": None}),
               gh + "2": (404, {"error": "Job not found"}),
               gh + "3": (200, {"first_published": "2026-10-01T00:00:00Z", "application_deadline": "2026-11-20"})})
    url = "https://job-boards.greenhouse.io/acme/jobs/"
    ok = verify.check_posting(c, url + "1", today=TODAY)
    assert ok.state == "Open" and ok.posted == date(2026, 11, 20)
    assert verify.check_posting(c, url + "2", today=TODAY).state == "Closed"
    late = verify.check_posting(c, url + "3", today=TODAY)
    assert late.state == "Closed" and "20 Nov" in late.detail


def test_other_boards():
    ms = int(datetime(2026, 11, 18, tzinfo=timezone.utc).timestamp() * 1000)
    c = board({
        "https://api.lever.co/v0/postings/meesho/a": (200, {"createdAt": ms}),
        "https://api.lever.co/v0/postings/meesho/b": (404, {"ok": False}),
        "https://api.ashbyhq.com/posting-api/job-board/notion": (200, {"jobs": [{"id": "x", "publishedAt": "2026-09-15T00:00:00Z"}]}),
        "https://api.smartrecruiters.com/v1/companies/Bosch/postings/7": (200, {"active": False, "releasedDate": "2026-11-09T00:00:00Z"}),
        "https://o.oraclecloud.com/hcmRestApi": (200, {"items": []}),
    })
    assert verify.check_posting(c, "https://jobs.lever.co/meesho/a", today=TODAY).posted == date(2026, 11, 18)
    assert verify.check_posting(c, "https://jobs.lever.co/meesho/b", today=TODAY).state == "Closed"
    cache: dict = {}
    assert verify.check_posting(c, "https://jobs.ashbyhq.com/notion/x", today=TODAY, ashby_boards=cache).state == "Open"
    assert verify.check_posting(c, "https://jobs.ashbyhq.com/notion/gone", today=TODAY, ashby_boards=cache).state == "Closed"
    assert verify.check_posting(c, "https://jobs.smartrecruiters.com/Bosch/7-se", today=TODAY).state == "Closed"
    assert verify.check_posting(c, "https://o.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1", today=TODAY).state == "Closed"
    assert verify.check_posting(c, "https://www.linkedin.com/jobs/view/1", today=TODAY).state == "Unknown"
    assert verify.check_posting(c, "https://jobs.lever.co/unreachable/z", today=TODAY).state == "Unknown"  # offline


def test_freshness_buckets():
    f = lambda days: verify.freshness(TODAY - timedelta(days=days), TODAY, 30, 60)
    assert (f(3), f(30), f(31), f(60), f(61), verify.freshness(None, TODAY, 30, 60)) == \
        ("fresh", "fresh", "aging", "aging", "old", "unknown")


def test_verify_records_results_and_withdraws_closed_jobs(tmp_path: Path):
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path))
    create_workbook(s.tracker_path)
    t = Tracker(s.tracker_path)
    t.add_application(company="Open Co", role="SDE", status="Tailored", url="https://jobs.lever.co/open/a")
    t.add_application(company="Gone Co", role="SDE", status="Tailored", url="https://jobs.lever.co/gone/b")
    t.add_application(company="Sent Co", role="SDE", status="Applied", url="https://jobs.lever.co/gone/c")
    t.save()
    ms = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)
    c = board({"https://api.lever.co/v0/postings/open/a": (200, {"createdAt": ms}),
               "https://api.lever.co/v0/postings/gone/": (404, {"ok": False})})
    results = verify.verify_applications(s, client=c, today=TODAY)
    assert [r.id for r in results] == ["A001", "A002"]  # only jobs not sent yet
    t = Tracker(s.tracker_path)
    a1, a2, a3 = t.find("A001"), t.find("A002"), t.find("A003")
    assert a1["posting"] == "Open" and a1["posted"].date() == date(2026, 11, 1) and a1["checked"].date() == TODAY
    assert a2["status"] == "Withdrawn" and "job closed" in a2["notes"] and a2["posting"].startswith("Closed")
    assert a3["status"] == "Applied" and a3["posting"] is None
    assert verify.summary(results, TODAY, 30, 60) == "1 still open, 1 closed."


# ---------- discover: freshness cap and authenticity ----------

PROFILE = load_profile(Path(__file__).resolve().parents[1] / "jobhunt" / "profile.yaml")
GOOD = "<p>2-4 years of experience with Node.js, NestJS, TypeScript, PostgreSQL and Redis on AWS.</p>"


def post(company="Acme", desc=GOOD, days_old=5, source="greenhouse"):
    when = datetime(2026, 11, 27, tzinfo=timezone.utc) - timedelta(days=days_old) if days_old is not None else None
    return RawPost(source=source, source_id=f"acme:{company}:{days_old}", url="https://boards.greenhouse.io/acme/1",
                   title="Backend Engineer (Node.js)", company=company, location="Bengaluru", description=desc,
                   posted_at=when)


def test_discover_keeps_fresh_employer_posts_only():
    keep = lambda p: score_post(PROFILE, p, max_age_days=30, today=TODAY) is not None
    assert keep(post())
    assert not keep(post(days_old=45))                                  # too old
    assert keep(post(days_old=None))                                    # company board, date not given
    assert not keep(post(days_old=None, source="adzuna"))               # aggregator, date not given
    assert not keep(post(company="TalentHub Staffing Solutions", source="adzuna"))  # recruiter, not the employer
    assert not keep(post(company="Confidential", source="adzuna"))
    assert not keep(post(desc=GOOD + "<p>A refundable registration fee of Rs 2000 applies.</p>", source="adzuna"))
    assert score_post(PROFILE, post(days_old=45), today=TODAY) is not None  # no cap given: no age filter
