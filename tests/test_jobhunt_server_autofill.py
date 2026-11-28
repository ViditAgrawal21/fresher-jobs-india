import os
import threading
from pathlib import Path

import pytest

from jobhunt import autofill
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker, create_workbook

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


# ---------- choosing answers ----------

@pytest.mark.parametrize("options,value,key,want", [
    (["0-1 years", "1-3 years", "3-5 years"], "2.3", "total_experience_years", "1-3 years"),
    (["1+ Years", "2+ Years", "5+ Years"], "2.3", "total_experience_years", "2+ Years"),
    (["Yes, I am", "No"], "No", "needs_sponsorship", "No"),
    (["LinkedIn", "Company Website", "Referral"], "Company careers page", "how_did_you_hear", "Company Website"),
    (["Bangalore", "Gurgaon", "Pune"], "Bengaluru, Pune, Hyderabad or Remote", "location_preference", "Bangalore"),
    (["Mumbai", "Delhi"], "Bengaluru, Pune", "location_preference", None),
])
def test_pick_dropdown_option(options, value, key, want):
    i = autofill._pick(options, value, key)
    assert (options[i] if i is not None else None) == want


@pytest.mark.parametrize("label,key", [
    ("First Name", "first_name"), ("Preferred First Name", "preferred_name"), ("Email address", "email"),
    ("LinkedIn Profile", "linkedin"), ("Current CTC (in LPA)", "current_ctc_lpa"),
    ("Total years of experience", "total_experience_years"), ("How did you hear about us?", "how_did_you_hear"),
    ("Naukri profile", None),
])
def test_answer_for_maps_labels(label, key):
    answers = {k: "x" for k in ("first_name", "preferred_name", "email", "linkedin", "current_ctc_lpa",
                                "total_experience_years", "how_did_you_hear")}
    assert autofill.answer_for(label, answers)[0] == key


def test_apply_url_and_autofillable():
    assert autofill.apply_url("https://jobs.lever.co/meesho/abc") == "https://jobs.lever.co/meesho/abc/apply"
    assert autofill.apply_url("https://jobs.ashbyhq.com/notion/x") == "https://jobs.ashbyhq.com/notion/x/application"
    gh = "https://www.sigmoid.com/careers/?gh_jid=123"
    embed = "https://job-boards.greenhouse.io/embed/job_app?for=sigmoid&token=123"
    assert autofill.apply_url(gh, {gh: "greenhouse:sigmoid:123"}) == embed
    assert autofill.apply_url("https://job-boards.greenhouse.io/sigmoid/jobs/123") == embed
    assert autofill.apply_url(embed) == embed
    eu = "https://boards.eu.greenhouse.io/nice/jobs/5044602101?gh_jid=5044602101"
    assert autofill.apply_url(eu, {eu: "greenhouse:nice:5044602101"}) == eu
    assert autofill.autofillable("https://job-boards.greenhouse.io/x/jobs/1")
    assert not autofill.autofillable("https://www.linkedin.com/jobs/view/1")
    assert not autofill.autofillable("https://www.naukri.com/job-listings-x")
    assert not autofill.autofillable("http://example.com/apply")
    assert not autofill.autofillable(None)


# ---------- filling a real page (local HTML, no network) ----------

FORM = """<!doctype html><html><body><form id="f" onsubmit="window.sent = true; return false">
<label for="fn">First Name *</label><input id="fn" required>
<label for="ln">Last Name</label><input id="ln">
<label for="em">Email</label><input id="em" type="email">
<label for="li">LinkedIn Profile</label><input id="li">
<label for="np">Notice period (days)</label><input id="np" required>
<label for="cv">Resume/CV</label><input id="cv" type="file">
<label for="cl">Cover letter</label><input id="cl2" type="file" aria-label="Cover letter file">
<label for="rl">Are you willing to relocate?</label><select id="rl"><option></option><option>Yes</option><option>No</option></select>
<label id="exl">Total years of experience</label>
<div class="field"><input id="ex" role="combobox" aria-labelledby="exl" autocomplete="off">
<ul id="exo" hidden><li role="option">0-1 years</li><li role="option">1-3 years</li><li role="option">3-5 years</li></ul></div>
<button type="submit">Submit application</button>
</form><script>
const ex = document.getElementById('ex'), exo = document.getElementById('exo');
ex.addEventListener('click', () => { exo.hidden = false; });
exo.addEventListener('click', e => { if (e.target.getAttribute('role') === 'option') { ex.value = e.target.textContent; exo.hidden = true; } });
</script></body></html>"""


def _chromium() -> str:
    path = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    return path if os.path.exists(path) else ""


@pytest.fixture
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        p = sync_api.sync_playwright().start()
        kwargs = {"executable_path": _chromium()} if _chromium() else {}
        browser = p.chromium.launch(**kwargs)
    except Exception as exc:  # no browser installed
        pytest.skip(str(exc))
    pg = browser.new_page()
    yield pg
    browser.close()
    p.stop()


def test_fill_page_fills_known_fields_and_never_submits(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
    page.set_content(FORM)
    answers = {"first_name": "Vidit", "last_name": "Agrawal", "email": "me@example.com",
               "linkedin": "https://linkedin.com/in/x", "notice_period_days": "", "willing_to_relocate": "Yes",
               "total_experience_years": "2.3"}
    done: set = set()
    res = autofill.fill_page(page, answers, resume, done)
    val = lambda sel: page.eval_on_selector(sel, "e => e.value")
    assert (val("#fn"), val("#ln"), val("#em"), val("#rl"), val("#ex")) == \
        ("Vidit", "Agrawal", "me@example.com", "Yes", "1-3 years")
    assert page.eval_on_selector("#cv", "e => e.files.length") == 1
    assert page.eval_on_selector("#cl2", "e => e.files.length") == 0  # never the cover-letter slot
    assert val("#np") == "" and res["todo"] >= 1  # blank answer: left (orange) for you
    assert page.evaluate("() => window.sent") is None  # Submit is never clicked
    assert page.locator("#jobhunt-banner").count() == 1
    # A second pass (the loop runs every 2 s) doesn't retype anything.
    page.fill("#fn", "Changed by me")
    autofill.fill_page(page, answers, resume, done)
    assert val("#fn") == "Changed by me"


def test_confirmation_page_detected(page):
    page.set_content("<h1>Thank you for applying!</h1><p>We'll be in touch.</p>")
    assert autofill.is_confirmation(page)
    page.set_content("<h1>Apply for Backend Engineer</h1>")
    assert not autofill.is_confirmation(page)


# ---------- the local dashboard server ----------

@pytest.fixture
def settings(tmp_path: Path) -> JobhuntSettings:
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path), candidate_email="me@example.com",
                        anthropic_api_key="", gmail_app_password="")
    create_workbook(s.tracker_path)
    t = Tracker(s.tracker_path)
    folder = tmp_path / "applications" / "A001_acme"
    folder.mkdir(parents=True)
    (folder / "Resume.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (folder / "report.md").write_text("# Acme\nFit 80", encoding="utf-8")
    (folder / "tailoring.json").write_text('{"recruiter_note": "hi"}', encoding="utf-8")
    t.add_application(company="Acme", role="Backend Engineer", status="Tailored", fit=80,
                      url="https://job-boards.greenhouse.io/acme/jobs/1", resume="applications/A001_acme/Resume.pdf")
    t.add_application(company="Beta", role="SDE", status="Tailored", fit=90,
                      url="https://www.linkedin.com/jobs/view/2")
    t.save()
    (tmp_path / "answers.yaml").write_text("first_name: Vidit\n", encoding="utf-8")
    return s


def _no_check(settings, ids=None, **kw):
    return []  # no network in tests


def _client(settings, runner=None, checker=_no_check):
    from fastapi.testclient import TestClient

    from jobhunt.server import create_app

    return TestClient(create_app(settings, runner=runner, checker=checker))


def test_server_page_data_and_files(settings):
    c = _client(settings)
    r = c.get("/")
    assert r.status_code == 200 and '"mode": "server"' in r.text
    apps = {a["id"]: a for a in c.get("/api/data").json()["apps"]}
    assert apps["A001"]["resume"] == "applications/A001_acme/Resume.pdf" and apps["A001"]["report"]
    pdf = c.get("/applications/A001_acme/Resume.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline")
    assert c.get("/applications/A001_acme/report.md").text.startswith("# Acme")
    # Only resumes, reports and JDs inside applications/ are served.
    assert c.get("/applications/A001_acme/tailoring.json").status_code == 404
    assert c.get("/applications/../answers.yaml").status_code == 404
    assert c.get("/applications/A001_acme/%2e%2e/%2e%2e/answers.yaml").status_code == 404
    assert c.get("/applications/A001_acme/missing.pdf").status_code == 404


def test_server_saves_moves_to_the_tracker(settings):
    c = _client(settings)
    r = c.post("/api/status", json={"id": "a001", "status": "Applied"})
    assert r.status_code == 200 and r.json()["changed"]
    app = Tracker(settings.tracker_path).find("A001")
    assert app["status"] == "Applied" and app["date_applied"] and "dashboard: Tailored -> Applied" in app["notes"]
    assert not c.post("/api/status", json={"id": "A001", "status": "applied"}).json()["changed"]
    assert c.post("/api/status", json={"id": "A999", "status": "Applied"}).status_code == 404
    assert c.post("/api/status", json={"id": "A001", "status": "Hired!"}).status_code == 404


def test_server_refuses_other_sites(settings):
    c = _client(settings)
    evil = c.post("/api/status", json={"id": "A001", "status": "Applied"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert c.get("/api/data", headers={"Host": "evil.example"}).status_code == 403
    assert Tracker(settings.tracker_path).find("A001")["status"] == "Tailored"


def test_server_autofill_runs_and_marks_submissions(settings):
    started, release = threading.Event(), threading.Event()
    calls = []

    def fake_run(s, ids, session, *, on_submitted=None, lock=None):
        calls.append(ids)
        session.state = "running"
        started.set()
        release.wait(5)
        session.submitted.append(ids[0])
        with lock:
            on_submitted(ids[0])
        session.state = "done"
        return session

    c = _client(settings, runner=fake_run)
    # LinkedIn jobs aren't autofilled.
    assert c.post("/api/autofill", json={"ids": ["A002"]}).status_code == 400
    r = c.post("/api/autofill", json={"all_ready": True, "limit": 5})
    assert r.status_code == 200 and r.json()["ids"] == ["A001"]
    assert started.wait(5)
    assert c.get("/api/autofill").json()["state"] == "running"
    assert c.post("/api/autofill", json={"ids": ["A001"]}).status_code == 409  # one run at a time
    release.set()
    for _ in range(50):
        if c.get("/api/autofill").json()["state"] == "done":
            break
        threading.Event().wait(0.1)
    assert c.get("/api/autofill").json()["submitted"] == ["A001"]
    assert calls == [["A001"]]
    app = Tracker(settings.tracker_path).find("A001")
    assert app["status"] == "Applied" and app["applied_with"] == "me@example.com"


def test_first_answers_file_comes_from_the_profile_and_leaves_salary_blank(tmp_path):
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path), candidate_email="me@example.com",
                        candidate_phone="+91 90000 00000")
    answers = autofill.load_answers(s)
    assert (tmp_path / "answers.yaml").exists()
    assert answers["first_name"] and answers["school"] and answers["education_end_year"].isdigit()
    assert answers["current_ctc_lpa"] == answers["expected_ctc_lpa"] == ""
    assert answers["email"] == "me@example.com" and answers["phone"] == "+91 90000 00000"


def test_server_skips_jobs_that_closed_before_autofill(settings):
    from jobhunt import verify

    def checker(s, ids=None, *, lock=None, **kw):
        res = verify.Result("A001", "Acme", "Backend Engineer", verify.Check("Closed", detail="taken down"), withdrawn=True)
        return [res]

    opened = []
    c = _client(settings, runner=lambda *a, **kw: opened.append(a[1]), checker=checker)
    assert c.post("/api/autofill", json={"ids": ["A001"]}).status_code == 200
    for _ in range(50):
        if c.get("/api/autofill").json()["state"] == "done":
            break
        threading.Event().wait(0.1)
    assert opened == [] and "closed" in c.get("/api/autofill").json()["message"]


def test_server_runs_the_posting_check(settings):
    from jobhunt import verify

    seen = []

    def checker(s, ids=None, *, lock=None, progress=None, **kw):
        seen.append(ids)
        progress(1, 1, "Acme")
        return [verify.Result("A001", "Acme", "Backend Engineer", verify.Check("Open", None))]

    c = _client(settings, checker=checker)
    assert c.post("/api/verify", json={}).status_code == 200
    for _ in range(50):
        if c.get("/api/verify").json()["state"] == "done":
            break
        threading.Event().wait(0.1)
    j = c.get("/api/verify").json()
    assert seen == [None] and j["state"] == "done" and "1 still open" in j["message"]
