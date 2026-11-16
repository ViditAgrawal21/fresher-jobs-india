from types import SimpleNamespace

import pytest

from core.models import JobTags, TagBatch
from pipeline.tagger import Tagger, cost_usd, load_prompt


def _tags(id, role="Software Engineering", cities=("Bengaluru",)):
    return JobTags(id=id, role=role, cities=list(cities), work_mode="onsite",
                   job_type="internship", in_india=True, is_fresher=True, confidence="high")


def _usage(inp=1000, out=200):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0)


class FakeMessages:
    """Answers each parse() call with whatever `responder(batch_ids)` returns."""

    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def parse(self, **kwargs):
        content = kwargs["messages"][0]["content"]
        ids = [int(line.split('"id": ')[1].split(",")[0]) for line in content.splitlines() if line.startswith("{")]
        self.calls.append(ids)
        results = self.responder(ids)
        return SimpleNamespace(usage=_usage(), stop_reason="end_turn", parsed_output=TagBatch(results=results))


def make_tagger(responder, batch_size=3):
    client = SimpleNamespace(messages=FakeMessages(responder))
    return Tagger(model="claude-haiku-5-5", batch_size=batch_size, workers=1, client=client), client.messages


def posts(*ids):
    return [{"id": i, "title": f"Job {i}", "company": "X", "location_raw": "Pune", "description": ""} for i in ids]


def test_prompt_template_renders():
    prompt = load_prompt("v2")
    assert "Bengaluru" in prompt and "Software Engineering" in prompt
    assert "{" in prompt  # the JSON examples survived str.format


def test_all_posts_tagged_in_batches():
    tagger, fake = make_tagger(lambda ids: [_tags(i) for i in ids])
    result = tagger.tag(posts(1, 2, 3, 4))
    assert set(result.tags) == {1, 2, 3, 4}
    assert fake.calls == [[1, 2, 3], [4]]


def test_missing_ids_are_retried_alone_and_invented_ids_ignored():
    def responder(ids):
        if len(ids) > 1:  # drop the last post, invent an id
            return [_tags(i) for i in ids[:-1]] + [_tags(999)]
        return [_tags(ids[0])]

    tagger, fake = make_tagger(responder)
    result = tagger.tag(posts(1, 2, 3))
    assert set(result.tags) == {1, 2, 3}
    assert 999 not in result.tags
    assert fake.calls[-1] == [3]


def test_empty_cities_become_unspecified_low_confidence():
    tagger, _ = make_tagger(lambda ids: [_tags(i, cities=()) for i in ids])
    t = tagger.tag(posts(1)).tags[1]
    assert t.cities == ["Unspecified"] and t.confidence == "low"


def test_cost_maths():
    # 1M input + 1M output on Haiku 5.5 = $0.10 + $0.50
    assert cost_usd("claude-haiku-5-5", _usage(1_000_000, 1_000_000)) == pytest.approx(0.60)


def test_on_batch_sees_every_batch_including_retries():
    def responder(ids):
        return [_tags(i) for i in ids if i != 2] if len(ids) > 1 else [_tags(ids[0])]

    tagger, _ = make_tagger(responder)
    saved = []
    result = tagger.tag(posts(1, 2, 3, 4), on_batch=lambda tags, call: saved.append(sorted(tags)))
    assert saved == [[1, 3], [4], [2]]  # batch [1,2,3] minus the skipped 2, batch [4], then 2 retried alone
    assert set(result.tags) == {1, 2, 3, 4}
