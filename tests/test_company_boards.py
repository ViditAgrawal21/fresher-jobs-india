import httpx

from pipeline.normalize import to_row
from pipeline.sources.company_boards import LeverSource


def _lever_posting(**overrides) -> dict:
    """Shape of an api.lever.co/v0/postings/{slug}?mode=json entry."""
    posting = {
        "id": "21fbe701-e71b-4f45-b865-a497a62a6482",
        "text": "Associate SDE- Pune",
        "hostedUrl": "https://jobs.lever.co/hevodata/21fbe701-e71b-4f45-b865-a497a62a6482",
        "createdAt": 1795610219265,
        "categories": {"location": "Pune, Maharashtra", "allLocations": ["Pune, Maharashtra"]},
        "description": "", "descriptionPlain": "",
        "descriptionBody": "", "descriptionBodyPlain": "",
        "opening": "", "openingPlain": "",
        "additional": "", "additionalPlain": "",
        "lists": [],
    }
    posting.update(overrides)
    return posting


def _fetch(postings: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/postings/hevodata"
        return httpx.Response(200, json=postings)

    source = LeverSource(httpx.Client(transport=httpx.MockTransport(handler)))
    source.delay = 0
    return source.fetch_company("hevodata", "Hevo Data")


def test_lever_falls_back_to_html_when_plain_fields_empty():
    # hevodata/mindtickle (28 Nov 2026): only the HTML fields are filled.
    posts = _fetch([_lever_posting(
        description="<div><p><strong>What you&rsquo;ll bring:</strong></p><ul><li>0-1 years of experience in Python</li></ul></div>",
        descriptionBody="<div><ul><li>0-1 years of experience in Python</li></ul></div>",
        additional="<p>Based out of our Baner office.</p>",
    )])

    assert len(posts) == 1
    assert "0-1 years of experience in Python" in posts[0].description
    assert "Baner office" in posts[0].description

    # what gets stored is plain text
    stored = to_row(posts[0])["description"]
    assert "<" not in stored
    assert "What you’ll bring: 0-1 years of experience in Python" in stored
    assert "Based out of our Baner office." in stored


def test_lever_prefers_plain_fields_and_keeps_lists():
    posts = _fetch([_lever_posting(
        description="<p>HTML copy</p>",
        descriptionPlain="Plain copy",
        additional="<p>HTML extra</p>",
        additionalPlain="Plain extra",
        lists=[{"text": "Requirements", "content": "<li>0-2 years of experience</li>"}],
    )])

    desc = posts[0].description
    assert "Plain copy" in desc and "Plain extra" in desc
    assert "HTML copy" not in desc and "HTML extra" not in desc
    assert "0-2 years of experience" in to_row(posts[0])["description"]
