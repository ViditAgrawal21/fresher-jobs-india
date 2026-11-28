import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from core.models import RawPost
from jobhunt import sources
from jobhunt.discover import discover, score_post
from jobhunt.fetch_jd import FetchError, fetch_job
from jobhunt.locations import classify, is_english
from jobhunt.profile import load_profile
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker, create_workbook

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")
PROFILE = load_profile(Path(__file__).resolve().parents[1] / "jobhunt" / "profile.yaml")
TODAY = date(2026, 11, 27)
CITIES = "Pune, Mumbai, Bengaluru"


# ---------- where: your order of preference ----------

@pytest.mark.parametrize("loc,text,ok,rank", [
    ("Remote - India", "", True, 0),
    ("Bengaluru · remote · India", "", True, 0),
    ("India · Remote", "", True, 0),
    ("Remote · Worldwide", "", True, 1),
    ("Remote · APAC", "", True, 1),
    ("Remote", "", True, 1),
    ("Pune, India (Hybrid)", "", True, 2),
    ("Navi Mumbai · hybrid · India", "", True, 2),
    ("Bangalore, IN", "", True, 3),
    ("Bengaluru, Karnataka, India", "", True, 3),
    ("Hyderabad, India", "", False, 8),          # not one of your cities
    ("London, GB", "", False, 9),               # office abroad
    ("Remote · United States", "", False, 9),   # remote, but for US residents
    ("Remote", "Applicants must be based in the United States.", False, 9),
    ("Remote · Worldwide", "You must be authorized to work in the US.", False, 9),
    ("Remote", "We hire in the US and India.", True, 1),
    ("Remote · Europe, EMEA", "", False, 9),
])
def test_classify(loc, text, ok, rank):
    w = classify(loc, text, CITIES)
    assert (w.ok, w.rank) == (ok, rank), w


def test_preferred_cities_and_abroad_switch():
    assert classify("Hyderabad, India", "", "Hyderabad").ok
    assert not classify("Remote · Worldwide", "", CITIES, remote_abroad=False).ok
    assert classify("Remote - India", "", CITIES, remote_abroad=False).ok


def test_is_english():
    en = "We are hiring a backend engineer to join our team. You will build APIs and work with the product team. " * 6
    de = "Wir suchen einen Backend Entwickler für unser Team. Du arbeitest mit der Plattform und die Produkt und das ist für uns. " * 6
    assert is_english(en) and not is_english(de) and is_english("Short ad")


# ---------- the boards ----------

def client(routes: dict) -> httpx.Client:
    def handler(request):
        for prefix, body in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_remote_boards_and_location_lines(tmp_path):
    c = client({
        "https://remotive.com/api/remote-jobs": {"jobs": [
            {"id": 1, "url": "https://remotive.com/j/1", "title": "Backend Engineer", "company_name": "Acme",
             "candidate_required_location": "Worldwide", "description": "<p>Node.js</p>", "publication_date": "2026-11-25T10:00:00"},
            {"id": 2, "url": "https://remotive.com/j/2", "title": "Senior Staff Engineer", "company_name": "Acme",
             "candidate_required_location": "USA", "description": "", "publication_date": "2026-11-25T10:00:00"}]},
        "https://remoteok.com/api": [{"legal": "terms"}, {"id": 9, "url": "https://remoteok.com/j/9", "position": "Full Stack Developer",
                                                          "company": "Beta", "location": "", "description": "x", "date": "2026-11-26T00:00:00+00:00"}],
        "https://api.lever.co/v0/postings/acme": [{"id": "a", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/acme/a",
                                                   "categories": {"location": "Pune"}, "workplaceType": "hybrid", "country": "IN",
                                                   "descriptionPlain": "Node", "createdAt": 1795000000000},
                                                  {"id": "b", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/acme/b",
                                                   "categories": {"location": "Austin"}, "workplaceType": "onsite", "country": "US",
                                                   "descriptionPlain": "Node", "createdAt": 1795000000000}],
        "https://api.ashbyhq.com/posting-api/job-board/acme": {"jobs": [
            {"id": "r", "title": "AI Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/r", "location": "Remote", "isRemote": True,
             "workplaceType": "Remote", "address": {"postalAddress": {"addressCountry": "United States"}},
             "descriptionPlain": "LLM", "publishedAt": "2026-11-20T00:00:00Z"}]},
    })
    remotive = sources.Remotive(c, tmp_path).fetch()
    assert [p.location for p in remotive] == ["Remote · Worldwide"]  # the senior title is dropped
    assert (tmp_path / "board_remotive.json").exists()
    assert sources.Remotive(c, tmp_path).fetch()[0].title == "Backend Engineer"  # cached: no second request needed
    assert sources.RemoteOK(c, tmp_path).fetch()[0].location == "Remote · Worldwide"
    lever = sources.Lever(c).fetch_company("acme", "Acme")
    assert [p.location for p in lever] == ["Pune · hybrid · India"]  # Austin onsite isn't India or remote
    ashby = sources.Ashby(c).fetch_company("acme", "Acme")
    assert ashby[0].location == "Remote · Remote · United States"
    assert not classify(ashby[0].location, "", CITIES).ok  # a US-only remote role


GOOD = "<p>2-4 years of experience with Node.js, NestJS, TypeScript, PostgreSQL and Redis on AWS.</p>"


def post(location, company="Acme", title="Backend Engineer (Node.js)", source="greenhouse", sid="1"):
    return RawPost(source=source, source_id=f"{company}:{sid}", url=f"https://boards.greenhouse.io/{company}/{sid}",
                   title=title, company=company, location=location, description=GOOD,
                   posted_at=datetime(2026, 11, 20, tzinfo=timezone.utc))


def test_discover_ranks_by_match_then_place_and_dedupes(tmp_path):
    posts = [post("Bengaluru, India", sid="1"), post("Remote - India", company="Beta", sid="2"),
             post("Remote · United States", company="Gamma", sid="3"), post("Hyderabad, India", company="Delta", sid="4"),
             post("Remote · Worldwide", company="Beta", sid="5", source="remotive")]  # same job as Beta's board post
    rows = discover(PROFILE, posts, tmp_path, cities=CITIES)
    assert [(r["company"], r["where"]) for r in rows] == [("Beta", "Remote (India)"), ("Acme", "Bengaluru (office)")]
    assert score_post(PROFILE, post("Hyderabad, India")) is not None  # cities=None: no location filter (old callers)


# ---------- a job from its link ----------

def test_fetch_job_from_board_links():
    c = client({
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1": {"company_name": "Acme", "title": "Backend Engineer",
                                                                   "location": {"name": "Pune, India"},
                                                                   "content": "&lt;p&gt;Node.js and Postgres&lt;/p&gt;",
                                                                   "first_published": "2026-11-20T00:00:00Z"},
        "https://api.lever.co/v0/postings/meesho/x": {"text": "SDE", "descriptionPlain": "Build things", "lists": [
            {"text": "Requirements", "content": "<li>2+ years</li>"}], "categories": {"location": "Bengaluru"}, "workplaceType": "hybrid"},
    })
    gh = fetch_job("https://job-boards.greenhouse.io/acme/jobs/1", client=c)
    assert (gh["company"], gh["role"], gh["location"], gh["posted"]) == ("Acme", "Backend Engineer", "Pune, India", date(2026, 11, 20))
    assert "Node.js and Postgres" in gh["jd"]
    lv = fetch_job("https://jobs.lever.co/meesho/x", client=c)
    assert lv["company"] == "Meesho" and "2+ years" in lv["jd"] and lv["location"] == "Bengaluru · hybrid"
    with pytest.raises(FetchError, match="LinkedIn"):
        fetch_job("https://www.linkedin.com/jobs/view/1", client=c)
    with pytest.raises(FetchError, match="closed"):
        fetch_job("https://job-boards.greenhouse.io/acme/jobs/2", client=c)


# ---------- the dashboard server ----------

@pytest.fixture
def settings(tmp_path: Path) -> JobhuntSettings:
    s = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path), candidate_email="me@example.com", anthropic_api_key="")
    create_workbook(s.tracker_path)
    t = Tracker(s.tracker_path)
    t.add_leads([{"key": "greenhouse:acme:1", "company": "Acme", "title": "Backend Engineer", "location": "Remote - India",
                  "score": 80, "track": "Backend", "matched": "Node.js", "missing": "", "source": "greenhouse",
                  "url": "https://job-boards.greenhouse.io/acme/jobs/1", "posted": TODAY - timedelta(days=2),
                  "where": "Remote (India)"}])
    t.save()
    (tmp_path / "leads").mkdir()
    from jobhunt.keywords import slug
    (tmp_path / "leads" / f"{slug('greenhouse:acme:1', 80)}.txt").write_text("Node.js backend role " * 20, encoding="utf-8")
    return s


def _wait(c, kind):
    for _ in range(50):
        ts = [t for t in c.get("/api/tasks").json()["tasks"] if t["kind"] == kind]
        if ts and ts[-1]["state"] in ("done", "error"):
            return ts[-1]
        threading.Event().wait(0.1)
    raise AssertionError("task didn't finish")


def _client(settings, **kw):
    from fastapi.testclient import TestClient

    from jobhunt.server import create_app

    return TestClient(create_app(settings, checker=lambda *a, **k: [], **kw))


def test_find_more_jobs_and_tailor_from_the_page(settings):
    wide = []

    def finder(progress, wider=False):
        progress("remotive", 0, 1)
        wide.append(wider)
        return {"posts": 120, "rows": [{}] * 7, "added": 5, "errors": []}

    got = []

    def tailorer(req, task):
        got.append((req.row, req.url, req.jd[:5]))
        task.message, task.app_id = "A001 is in Ready to apply", "A001"

    c = _client(settings, finder=finder, tailorer=tailorer)
    data = c.get("/api/data").json()
    lead = data["leads"][0]
    assert (lead["where"], lead["where_rank"], lead["has_jd"]) == ("Remote (India)", 0, True)
    assert c.get(f"/leads/{lead['row']}/jd").text.startswith("Acme: Backend Engineer")

    assert c.post("/api/discover", json={}).status_code == 200
    assert "120 jobs" in _wait(c, "discover")["message"]
    assert c.post("/api/discover", json={"wider": True}).status_code == 200
    _wait(c, "discover")
    assert wide == [False, True]

    assert c.post("/api/tailor", json={"row": lead["row"]}).status_code == 200
    assert _wait(c, "tailor")["state"] == "done"
    assert c.post("/api/tailor", json={"url": "https://jobs.lever.co/meesho/x"}).status_code == 200
    _wait(c, "tailor")
    assert c.post("/api/tailor", json={"jd": "too short"}).status_code == 400
    assert got == [(lead["row"], "", ""), (None, "https://jobs.lever.co/meesho/x", "")]

    assert c.post(f"/api/leads/{lead['row']}/skip", json={}).status_code == 200
    assert c.get("/api/data").json()["leads"] == []
    assert c.post("/api/leads/99/skip", json={}).status_code == 404


def test_tailor_job_end_to_end_offline(settings, monkeypatch):
    """The real tailoring path with the PDF step stubbed: lead -> application in Ready."""
    from jobhunt import server

    calls = {}

    def fake_prepare(s, **kw):
        calls.update(kw)
        t = Tracker(s.tracker_path)
        app_id = t.add_application(company=kw["company"], role=kw["role"], url=kw["url"], status="Tailored")
        t.save()

        class R:
            tailoring = type("T", (), {"fit": type("F", (), {"score": 77})()})()
            keyword_coverage = {"coverage_pct": 70}
        return {"id": app_id, "result": R()}

    monkeypatch.setattr("jobhunt.apply.prepare_application", fake_prepare)
    task = server.Task(1, "tailor", "x")
    server.tailor_job(settings, server.TailorRequest(row=2), task, threading.Lock())
    assert calls["offline"] is True and calls["company"] == "Acme" and "Node.js" in calls["jd"]
    t = Tracker(settings.tracker_path)
    app = t.find("A001")
    assert app["posting"] == "Open" and app["posted"].date() == TODAY - timedelta(days=2)
    assert t.lead(2)["decision"] == "Apply"
    assert "fit 77/100" in task.message and "Remote (India)" in task.message


def test_wider_search_takes_other_cities_last():
    w = classify("Hyderabad, India", "", CITIES, other_cities=True)
    assert (w.ok, w.rank, w.label) == (True, 4, "Hyderabad (wider search)")
    assert classify("Pune, India", "", CITIES, other_cities=True).rank == 3


def test_wwr_and_working_nomads(tmp_path):
    rss = """<rss><channel><item><title>Acme: Backend Engineer</title><region>Anywhere in the World</region>
    <description><![CDATA[<p>Node.js</p>]]></description><pubDate>Thu, 26 Nov 2026 10:00:00 +0000</pubDate>
    <link>https://weworkremotely.com/remote-jobs/acme-backend</link><guid>https://weworkremotely.com/remote-jobs/acme-backend</guid></item></channel></rss>"""

    def handler(request):
        if "weworkremotely" in str(request.url):
            return httpx.Response(200, text=rss)
        return httpx.Response(200, json=[{"title": "Full Stack Developer", "company_name": "Beta", "category_name": "Development",
                                          "location": "EU, India, US", "url": "https://wn/1", "description": "x",
                                          "pub_date": "2026-11-27T10:00:00-05:00"},
                                         {"title": "Account Executive", "company_name": "Beta", "category_name": "Sales",
                                          "location": "Global", "url": "https://wn/2", "description": "x", "pub_date": None}])
    c = httpx.Client(transport=httpx.MockTransport(handler))
    wwr = sources.WeWorkRemotely(c, tmp_path).fetch()
    assert (wwr[0].company, wwr[0].title, wwr[0].location) == ("Acme", "Backend Engineer", "Remote · Anywhere in the World")
    assert wwr[0].posted_at.date() == date(2026, 11, 26)
    wn = sources.WorkingNomads(c, tmp_path).fetch()
    assert [p.title for p in wn] == ["Full Stack Developer"] and classify(wn[0].location, "", CITIES).rank == 0


def test_bundle_moves_applications_and_matches(tmp_path):
    from jobhunt.bundle import export_bundle, import_bundle

    src = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path / "a"))
    dst = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path / "b"))
    for s in (src, dst):
        create_workbook(s.tracker_path)
    folder = src.data_dir / "applications" / "A001_nice_software-engineer"
    folder.mkdir(parents=True)
    (folder / "Resume.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "report.md").write_text("# NICE", encoding="utf-8")
    t = Tracker(src.tracker_path)
    t.add_application(company="NICE", role="Software Engineer", url="https://x/nice/1", status="Tailored", fit=86,
                      resume="applications/A001_nice_software-engineer/Resume.pdf", posted=date(2026, 11, 25))
    t.add_leads([{"key": "greenhouse:nice:2", "company": "NICE", "title": "SE CX", "url": "https://x/nice/2",
                  "posted": date(2026, 11, 23), "where": "Pune (office)", "score": 60}])
    t.save()
    from jobhunt.keywords import slug
    (src.data_dir / "leads").mkdir()
    (src.data_dir / "leads" / f"{slug('greenhouse:nice:2', 80)}.txt").write_text("JD text", encoding="utf-8")
    t = Tracker(dst.tracker_path)
    t.add_application(company="Old Co", role="SDE", url="https://x/old", status="Applied")
    t.save()

    out = export_bundle(src, ["A001"], [2], tmp_path / "b.zip")
    assert (out["apps"], out["leads"]) == (1, 1)
    res = import_bundle(dst, tmp_path / "b.zip")
    assert res["added"] == [("A001", "A002", "NICE", "Software Engineer")] and res["leads"] == 1
    t = Tracker(dst.tracker_path)
    app = t.find("A002")
    assert app["status"] == "Tailored" and app["fit"] == 86 and app["posted"].date() == date(2026, 11, 25)
    assert (dst.data_dir / str(app["resume"])).read_bytes().startswith(b"%PDF")
    assert (dst.data_dir / "applications" / "A002_nice_software-engineer" / "report.md").exists()
    assert t.lead(2)["where"] == "Pune (office)" and (dst.data_dir / "leads" / f"{slug('greenhouse:nice:2', 80)}.txt").exists()
    again = import_bundle(dst, tmp_path / "b.zip")
    assert again["added"] == [] and "already" in again["skipped"][0] and again["leads"] == 0


def test_bundle_ignores_unsafe_paths(tmp_path):
    import json
    import zipfile

    from jobhunt.bundle import import_bundle

    dst = JobhuntSettings(_env_file=None, jobhunt_data_dir=str(tmp_path / "b"))
    create_workbook(dst.tracker_path)
    with zipfile.ZipFile(tmp_path / "evil.zip", "w") as z:
        z.writestr("bundle.json", json.dumps({"version": 1, "apps": [{"company": "X", "role": "Y", "folder": "A1_x",
                                                                        "resume_name": "r.pdf"}], "leads": []}))
        z.writestr("applications/A1_x/../../../escape.txt", "no")
        z.writestr("applications/A1_x/run.exe", "no")
        z.writestr("applications/A1_x/r.pdf", "%PDF")
    import_bundle(dst, tmp_path / "evil.zip")
    assert not (tmp_path / "escape.txt").exists()
    files = sorted(p.name for p in (dst.data_dir / "applications").rglob("*") if p.is_file())
    assert files == ["r.pdf"]
