from pathlib import Path
from types import SimpleNamespace

import pytest

from jobhunt import tailor as tailor_mod
from jobhunt.keywords import extract_terms, numbers_in, years_required
from jobhunt.profile import load_profile
from jobhunt.tailor import BulletPick, EntryPick, Fit, Tailoring, apply_tailoring, offline_tailor, pick_track

PROFILE = load_profile(Path(__file__).resolve().parents[1] / "jobhunt" / "profile.yaml")

BACKEND_JD = """Software Engineer II - Backend (Node.js). 2-4 years of experience building REST APIs with
Node.js/NestJS and TypeScript, PostgreSQL, Redis, Kafka, Docker and AWS. Microservices and system design.
Nice to have: GraphQL."""
AI_JD = """AI Engineer: build LLM agents with RAG, embeddings and a vector database (pgvector), tool calling and
evals. Python and TypeScript. 1+ years of experience shipping LLM features."""
MOBILE_JD = """React Native Developer. 2+ years of experience with React Native, Expo, Redux and REST APIs.
Android and iOS release experience."""


# ---------- keywords ----------

def test_aliases_map_to_one_term():
    assert {"Node.js", "NestJS", "PostgreSQL"} <= extract_terms("Node, NodeJS, nest.js and Postgres")


def test_lookalikes_are_not_confused():
    assert "Java" not in extract_terms("JavaScript and TypeScript")
    assert "React" not in extract_terms("React Native developer")
    assert "React Native" in extract_terms("React Native developer")
    assert "Go" not in extract_terms("Go through the docs")
    assert "Go" in extract_terms("Go developer, Python")
    assert "Machine Learning" not in extract_terms("HTML and CSS")


def test_numbers_ignore_tech_names():
    assert numbers_in("ESP32 over S3 and EC2") == set()
    assert {"84%", "1,937", "$0.0985", "20x"} <= numbers_in("84% on 1,937 posts at $0.0985, 20x the cost")


@pytest.mark.parametrize("text,expected", [
    ("2-4 years of experience in Node.js", 2),
    ("3+ years of experience overall; 1+ year with Docker", 3),
    ("Experience: 5 to 8 yrs", 5),
    ("We were founded 10 years ago", None),
    ("no requirement stated", None),
])
def test_years_required(text, expected):
    assert years_required(text) == expected


# ---------- track + offline tailoring ----------

@pytest.mark.parametrize("jd,role,track", [
    (BACKEND_JD, "SDE 2 Backend", "backend"),
    (AI_JD, "AI Engineer", "ai"),
    (MOBILE_JD, "React Native Developer", "mobile"),
])
def test_pick_track(jd, role, track):
    assert pick_track(PROFILE, jd, role) == track


def test_offline_tailor_is_honest_about_gaps():
    t = offline_tailor(PROFILE, BACKEND_JD, "Acme", "SDE 2 Backend")
    assert t.track == "backend"
    assert {"Kafka", "GraphQL"} <= set(t.fit.gaps)
    assert "Node.js" in t.fit.matched
    assert t.fit.years_required == "2+ years"
    assert {e.id for e in t.experience} == {j.id for j in PROFILE.experience}
    assert "Kafka" not in t.recruiter_note


def test_offline_tailor_flags_too_senior():
    t = offline_tailor(PROFILE, BACKEND_JD.replace("2-4 years", "6+ years"), "Acme", "Backend Engineer")
    assert any("6+" in f for f in t.fit.red_flags)


# ---------- the guard ----------

def _tailoring(**overrides) -> Tailoring:
    t = offline_tailor(PROFILE, BACKEND_JD, "Acme", "SDE 2 Backend")
    return t.model_copy(update=overrides)


def test_guard_reverts_invented_tech_and_numbers():
    t = _tailoring()
    t.experience[0].bullets[0] = BulletPick(source_id="sakalya-lead",
                                            text="Led a team of 5 building Urdu India and MyDwarpal on Kafka.")
    t.summary = "Backend engineer with 3+ years of experience on Kafka and Kubernetes."
    doc, warnings = apply_tailoring(PROFILE, t, ["+91 0000000000", "me@example.com"])
    original = PROFILE.job("sakalya").bullets[0].text
    assert doc.experience[0].bullets[0] == original
    assert doc.summary == PROFILE.track_summary("backend")
    assert any("summary reverted" in w for w in warnings)


def test_guard_accepts_faithful_rewording():
    t = _tailoring()
    reworded = "Designed REST APIs, authentication and database workflows, with AWS S3 media storage and GCP Cloud Run deployments."
    t.experience[0].bullets = [BulletPick(source_id="sakalya-infra", text=reworded)]
    doc, warnings = apply_tailoring(PROFILE, t, [])
    assert doc.experience[0].bullets == [reworded]
    assert not [w for w in warnings if "sakalya-infra" in w]


def test_guard_drops_unknown_ids_and_skills_but_keeps_every_job():
    t = _tailoring(skills_first=["Kafka", "NestJS"],
                   experience=[EntryPick(id="sakalya", bullets=[BulletPick(source_id="made-up", text="Scaled to 1M users")])],
                   projects=[EntryPick(id="nope", bullets=[])])
    doc, warnings = apply_tailoring(PROFILE, t, [])
    assert [e.id for e in doc.experience] == [j.id for j in PROFILE.experience]
    assert doc.experience[0].bullets  # fell back to real bullets
    assert all("1M" not in b for b in doc.experience[0].bullets)
    assert doc.projects  # fell back to the track's projects
    backend = next(g for g in doc.skills if g.category == "Backend")
    assert backend.items[0] == "NestJS"
    assert all("Kafka" not in g.items for g in doc.skills)
    assert any("Kafka" in w for w in warnings)


# ---------- Claude path, with a fake client ----------

class _FakeMessages:
    def __init__(self, parsed, calls):
        self.parsed, self.calls = parsed, calls

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1200, output_tokens=900, cache_read_input_tokens=3000,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.parsed, usage=usage,
                               model=kwargs["model"], _request_id="req_test")


def test_llm_path_sends_cached_profile_and_fallbacks(monkeypatch):
    import anthropic

    parsed = _tailoring(headline="Backend Engineer | Node.js, NestJS, PostgreSQL | AI Engineering")
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages(parsed, calls)
            self.beta = SimpleNamespace(messages=_FakeMessages(parsed, calls))

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    result = tailor_mod.tailor(PROFILE, BACKEND_JD, "Acme", "SDE 2", [], model="claude-opus-5-5", effort="medium")
    assert result.source == "llm"
    assert result.doc.headline.startswith("Backend Engineer")
    call = calls[0]
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "sakalya-lead" in call["system"][0]["text"]
    assert "<job_description>" in call["messages"][0]["content"]
    assert result.usage["cache_read_tokens"] == 3000


def test_llm_failure_falls_back_to_offline(monkeypatch):
    def boom(*a, **k):
        raise tailor_mod.TailorError("no Anthropic API key")

    monkeypatch.setattr(tailor_mod, "llm_tailor", boom)
    result = tailor_mod.tailor(PROFILE, BACKEND_JD, "Acme", "SDE 2", [])
    assert result.source == "offline"
    assert "offline" in result.warnings[0]


def test_fit_model_roundtrips_as_json():
    t = _tailoring(fit=Fit(score=70, verdict="good", years_required="2+ years", matched=[], gaps=[], red_flags=[]))
    assert Tailoring.model_validate_json(t.model_dump_json()) == t
