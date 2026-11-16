from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from core.config import get_settings
from core.models import RawPost

log = logging.getLogger(__name__)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": get_settings().user_agent, "Accept": "application/json"},
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    )


class Source(ABC):
    """A place we collect posts from. Subclasses implement fetch().

    `delay` is a politeness gap between requests to the same host.
    """

    name: str
    delay: float = 0.5
    read_timeout: float = 30.0  # some APIs (Lever) are slow; they override this

    def __init__(self, client: httpx.Client):
        self.client = client
        self._last_call = 0.0

    @property
    def enabled(self) -> bool:
        """False when the source can't run (e.g. no API key). Disabled sources are skipped
        and don't count as healthy, so their existing posts aren't expired."""
        return True

    @abstractmethod
    def fetch(self) -> list[RawPost]: ...

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def get_json(self, url: str, **kwargs) -> dict | list:
        self._throttle()
        kwargs.setdefault("timeout", httpx.Timeout(self.read_timeout, connect=10.0))
        resp = self.client.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def post_json(self, url: str, **kwargs) -> dict | list:
        self._throttle()
        kwargs.setdefault("timeout", httpx.Timeout(self.read_timeout, connect=10.0))
        resp = self.client.post(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
