"""LLM tagging: role, cities, work mode, job type, is_fresher.

One request tags a batch of posts (default 20) so the system prompt (~2k tokens)
is paid once per batch instead of once per post. Output is constrained to the
Pydantic schema in core/models.py via structured outputs, so we never parse free text.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from core.config import get_settings
from core.models import JobTags, TagBatch
from core.taxonomy import CITY_VALUES, ROLES, UNSPECIFIED

log = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"
DEFAULT_PROMPT_VERSION = "v1"

# USD per 1M tokens (input, output). Anthropic list prices, Nov 2026.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def load_prompt(version: str = DEFAULT_PROMPT_VERSION) -> str:
    template = (PROMPT_DIR / f"tagger_{version}.md").read_text(encoding="utf-8")
    roles = "\n".join(f"- {name}: {desc}" for name, desc in ROLES.items())
    cities = ", ".join(CITY_VALUES)
    return template.format(roles=roles, cities=cities)


def cost_usd(model: str, usage) -> float:
    p_in, p_out = PRICES.get(model, PRICES["claude-haiku-5-5"])
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * p_in
        + cache_write * p_in * 1.25
        + cache_read * p_in * 0.1
        + usage.output_tokens * p_out
    ) / 1_000_000


def _post_line(post: dict) -> str:
    return json.dumps(
        {
            "id": post["id"],
            "title": post["title"],
            "company": post["company"],
            "location": post.get("location_raw", ""),
            "description": post.get("description", ""),
        },
        ensure_ascii=False,
    )


@dataclass
class CallLog:
    model: str
    prompt_version: str
    n_posts: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    ok: bool = True
    error: str | None = None


@dataclass
class TagResult:
    tags: dict[int, JobTags] = field(default_factory=dict)
    calls: list[CallLog] = field(default_factory=list)

    @property
    def cost(self) -> float:
        return sum(c.cost_usd for c in self.calls)


class Tagger:
    def __init__(self, model: str | None = None, prompt_version: str = DEFAULT_PROMPT_VERSION,
                 batch_size: int | None = None, workers: int | None = None, client=None):
        s = get_settings()
        self.model = model or s.tagger_model
        self.prompt_version = prompt_version
        self.batch_size = batch_size or s.tagger_batch_size
        self.workers = workers or s.tagger_workers
        self.system_prompt = load_prompt(prompt_version)
        self.client = client or anthropic.Anthropic(api_key=s.anthropic_api_key or None, max_retries=4)

    def tag(self, posts: list[dict]) -> TagResult:
        result = TagResult()
        batches = [posts[i : i + self.batch_size] for i in range(0, len(posts), self.batch_size)]
        with ThreadPoolExecutor(self.workers) as pool:
            for tags, call in pool.map(self._call, batches):
                result.tags.update(tags)
                result.calls.append(call)

        # Anything the model skipped or that failed gets one more try on its own.
        missing = [p for p in posts if p["id"] not in result.tags]
        if missing:
            log.warning("retrying %d posts one by one", len(missing))
            for post in missing:
                tags, call = self._call([post])
                result.tags.update(tags)
                result.calls.append(call)
        return result

    def _call(self, batch: list[dict]) -> tuple[dict[int, JobTags], CallLog]:
        call = CallLog(model=self.model, prompt_version=self.prompt_version, n_posts=len(batch))
        ids = {p["id"] for p in batch}
        user = "<posts>\n" + "\n".join(_post_line(p) for p in batch) + f"\n</posts>\n\nTag all {len(batch)} posts."
        started = time.monotonic()
        try:
            resp = self.client.messages.parse(
                model=self.model,
                max_tokens=8000,
                system=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=TagBatch,
                output_config={"effort": "low"},
            )
        except anthropic.APIError as exc:
            call.ok, call.error = False, f"{type(exc).__name__}: {exc}"[:500]
            log.error("tagging call failed (%d posts): %s", len(batch), call.error)
            return {}, call
        finally:
            call.latency_ms = int((time.monotonic() - started) * 1000)

        u = resp.usage
        call.input_tokens = u.input_tokens
        call.output_tokens = u.output_tokens
        call.cache_read_tokens = getattr(u, "cache_read_input_tokens", 0) or 0
        call.cache_write_tokens = getattr(u, "cache_creation_input_tokens", 0) or 0
        call.cost_usd = cost_usd(self.model, u)

        if resp.stop_reason != "end_turn" or resp.parsed_output is None:
            call.ok, call.error = False, f"stop_reason={resp.stop_reason}"
            log.error("tagging call returned no usable output: %s", call.error)
            return {}, call

        tags: dict[int, JobTags] = {}
        for item in resp.parsed_output.results:
            if item.id not in ids:  # model invented or mangled an id
                continue
            if not item.cities:
                item.cities, item.confidence = [UNSPECIFIED], "low"
            tags[item.id] = item
        return tags, call
