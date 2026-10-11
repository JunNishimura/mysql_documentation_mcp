"""HTTP access to dev.mysql.com (docs/02_design.md §5.1)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from mysql_documentation_mcp.config import Settings
from mysql_documentation_mcp.core.urls import is_allowed_url

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 502, 503, 504}
_MAX_RETRY_AFTER_SECONDS = 30.0


class FetchError(ValueError):
    """A page could not be retrieved. The message is safe to show to the agent."""


class OfflineError(FetchError):
    pass


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str  # URL after redirects
    status_code: int  # 304 when the conditional request found no change
    content: bytes
    etag: str | None
    last_modified: str | None

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class _HostRateLimiter:
    """Keeps at least ``interval`` seconds between request starts."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._last_start = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last_start + self._interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_start = time.monotonic()


async def _reject_disallowed_redirect(response: httpx.Response) -> None:
    if response.is_redirect:
        target = str(response.url.join(response.headers.get("location", "")))
        if not is_allowed_url(target):
            raise FetchError(f"Refusing to follow redirect from {response.url} to {target}")


class Fetcher:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._limiters: dict[str, _HostRateLimiter] = {}

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=True,
                max_redirects=5,
                timeout=self._settings.request_timeout,
                headers={"User-Agent": self._settings.user_agent},
                event_hooks={"response": [_reject_disallowed_redirect]},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _limiter(self, url: str) -> _HostRateLimiter:
        host = httpx.URL(url).host
        if host not in self._limiters:
            self._limiters[host] = _HostRateLimiter(self._settings.min_request_interval)
        return self._limiters[host]

    async def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        """GET ``url``. Pass ``etag``/``last_modified`` to revalidate a cached copy."""
        if self._settings.offline:
            raise OfflineError(f"Offline mode (MYSQL_DOC_OFFLINE) is enabled; cannot fetch {url}")
        if not is_allowed_url(url):
            raise FetchError(f"Refusing to fetch URL outside the allowlist: {url}")

        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        client = self._get_client()
        attempt = 0
        while True:
            await self._limiter(url).wait()
            try:
                response = await client.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt >= self._settings.max_retries:
                    raise FetchError(f"Failed to fetch {url}: {e!r}") from e
                delay = self._backoff(attempt, None)
            except httpx.HTTPError as e:
                raise FetchError(f"Failed to fetch {url}: {e!r}") from e
            else:
                if response.status_code in _RETRY_STATUS and attempt < self._settings.max_retries:
                    delay = self._backoff(attempt, response.headers.get("retry-after"))
                else:
                    return self._to_result(url, response)
            attempt += 1
            logger.info("Retrying %s in %.1fs (attempt %d)", url, delay, attempt)
            await asyncio.sleep(delay)

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), _MAX_RETRY_AFTER_SECONDS)
            except ValueError:
                pass
        return self._settings.retry_backoff_base * (2**attempt)

    @staticmethod
    def _to_result(url: str, response: httpx.Response) -> FetchResult:
        final_url = str(response.url)
        if response.status_code == 404:
            raise FetchError(f"Page not found (404): {final_url}")
        if response.status_code == 403:
            raise FetchError(
                f"Failed to fetch {final_url}: HTTP 403. The site rejects some User-Agents; "
                "if this persists, set MYSQL_DOC_USER_AGENT to a value the site accepts."
            )
        if response.status_code >= 400:
            raise FetchError(f"Failed to fetch {final_url}: HTTP {response.status_code}")
        return FetchResult(
            requested_url=url,
            final_url=final_url,
            status_code=response.status_code,
            content=b"" if response.status_code == 304 else response.content,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )
