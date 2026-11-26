import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.models import RawPost
from jobhunt.discover import discover, lead_description, score_post
from jobhunt.profile import base_resume, load_profile

PROFILE = load_profile(Path(__file__).resolve().parents[1] / "jobhunt" / "profile.yaml")


def post(title: str, description: str, source_id: str = "1") -> RawPost:
    return RawPost(source="greenhouse", source_id=f"acme:{source_id}", url=f"https://boards.greenhouse.io/acme/{source_id}",
                   title=title, company="Acme", location="Bengaluru, India", description=description,
                   posted_at=datetime(2026, 11, 20, tzinfo=timezone.utc))


GOOD = "<p>2-4 years of experience with Node.js, NestJS, TypeScript, PostgreSQL and Redis on AWS.</p>"


def test_scores_a_matching_backend_role():
    row = score_post(PROFILE, post("Backend Engineer (Node.js)", GOOD))
    assert row and row["score"] >= 60 and row["track"] == "Backend" and row["exp"] == "2+ yrs"


@pytest.mark.parametrize("title,desc", [
    ("Backend Engineer", "6+ years of experience with Node.js"),       # too senior
    ("Software Engineering Intern", GOOD),                              # intern
    ("Java Developer", "3 years of experience in Java and Spring"),    # wrong stack
    ("Account Executive", GOOD),                                       # not software
    ("Backend Engineer", "2 years of experience with Elixir and Erlang"),  # nothing matches
])
def test_filters_out_bad_fits(title, desc):
    assert score_post(PROFILE, post(title, desc)) is None


def test_discover_sorts_and_saves_descriptions(tmp_path):
    posts = [post("Software Engineer", "1+ years of experience with Python", "a"),
             post("Backend Engineer (Node.js)", GOOD, "b")]
    rows = discover(PROFILE, posts, tmp_path, min_score=0)
    assert [r["title"] for r in rows][0] == "Backend Engineer (Node.js)"
    assert "NestJS" in lead_description(tmp_path, rows[0]["key"])


def _chromium() -> str:
    path = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    return path if os.path.exists(path) else ""


def test_base_resume_renders_to_one_clean_page(tmp_path):
    pytest.importorskip("playwright")
    from jobhunt.render import Renderer, ats_check, fit_one_page

    try:
        renderer = Renderer(_chromium()).__enter__()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    try:
        doc = base_resume(PROFILE, "backend", ["+91 9999999999", "me@example.com"])
        out = tmp_path / "resume.pdf"
        fitted, pages, _ = fit_one_page(doc, renderer, out)
    finally:
        renderer.__exit__(None, None, None)
    assert pages == 1
    assert ats_check(out, fitted) == []
    assert fitted.projects[0].heading == "Fresher Jobs India"
    assert len(fitted.projects[0].bullets) >= 3
