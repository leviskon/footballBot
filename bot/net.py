"""Shared aiohttp client with timeouts, size limits and conditional GET for feeds."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MAX_BODY_BYTES = 8 * 1024 * 1024
# A page that stops sending data for this long, or sends it slower than MIN_RATE bytes/s,
# is used as far as it arrived.
STALL_SEC = 3.0
MIN_RATE = 8 * 1024


class FetchError(Exception):
    """Network problem, bad HTTP status or an unusable response."""


@dataclass(frozen=True, slots=True)
class FeedResponse:
    not_modified: bool
    body: bytes = b""
    content_type: str = ""


class HttpClient:
    def __init__(self, timeout: float) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout, sock_connect=min(10.0, timeout))
        self._session: aiohttp.ClientSession | None = None
        self._validators: dict[str, dict[str, str]] = {}

    async def start(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
            )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def _client(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("HttpClient.start() was not called")
        return self._session

    async def fetch_feed(self, url: str) -> FeedResponse:
        headers = {
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, "
                      "text/xml;q=0.9, */*;q=0.5",
            **self._validators.get(url, {}),
        }
        try:
            async with self._client.get(url, headers=headers) as resp:
                if resp.status == 304:
                    return FeedResponse(not_modified=True)
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status}")
                body = await self._read(resp)
                validators = {}
                if etag := resp.headers.get("ETag"):
                    validators["If-None-Match"] = etag
                if modified := resp.headers.get("Last-Modified"):
                    validators["If-Modified-Since"] = modified
                self._validators[url] = validators
                return FeedResponse(False, body, resp.headers.get("Content-Type", ""))
        except TimeoutError:
            raise FetchError("таймаут") from None
        except aiohttp.ClientError as exc:
            raise FetchError(_describe(exc)) from exc

    async def fetch_json(self, url: str, params: dict[str, str] | None = None, *,
                         headers: dict[str, str] | None = None) -> Any:
        try:
            async with self._client.get(url, params=params,
                                        headers={"Accept": "application/json",
                                                 **(headers or {})}) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status}")
                body = await self._read(resp)
        except TimeoutError:
            raise FetchError("таймаут") from None
        except aiohttp.ClientError as exc:
            raise FetchError(_describe(exc)) from exc
        try:
            return json.loads(body)
        except ValueError as exc:
            raise FetchError("ответ не является JSON") from exc

    async def fetch_text(self, url: str, *, timeout: float = 20) -> str:
        """Download a web page (an article) and decode it.

        Some sites send pages to foreign servers very slowly. When the transfer stalls or
        trickles slower than ``MIN_RATE`` for ``STALL_SEC`` (or ``timeout`` runs out) after part of the page has arrived, that part
        is returned: the picture and the beginning of the article are at the top of the page.
        """
        chunks: list[bytes] = []
        charset = None
        try:
            async with asyncio.timeout(timeout):
                async with self._client.get(
                    url, headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=min(10.0, timeout)),
                ) as resp:
                    if resp.status != 200:
                        raise FetchError(f"HTTP {resp.status}")
                    charset = resp.charset
                    size = 0
                    loop = asyncio.get_running_loop()
                    window_start, window_size = loop.time(), 0
                    while size <= MAX_BODY_BYTES:
                        try:
                            chunk = await asyncio.wait_for(resp.content.readany(),
                                                           STALL_SEC if chunks else None)
                        except TimeoutError:
                            chunk = None  # stalled
                        if chunk == b"":
                            break  # the whole page has arrived
                        if chunk:
                            size += len(chunk)
                            chunks.append(chunk)
                        # once part of the page is here, a stalled or trickling transfer
                        # (some sites throttle servers abroad to ~250 bytes/s) ends the read
                        if loop.time() - window_start >= STALL_SEC or chunk is None:
                            if size - window_size < MIN_RATE * STALL_SEC:
                                log.info("Страница %s грузится медленно — беру первые %d КБ",
                                         url, size // 1024)
                                break
                            window_start, window_size = loop.time(), size
        except TimeoutError:
            if not chunks:
                raise FetchError("таймаут") from None
            log.info("Страница %s грузится медленно — беру первые %d КБ", url,
                     sum(map(len, chunks)) // 1024)
        except aiohttp.ClientError as exc:
            if not chunks:
                raise FetchError(_describe(exc)) from exc
        body = b"".join(chunks)
        encoding = charset or _meta_charset(body) or "utf-8"
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    async def fetch_bytes(self, url: str, *, timeout: float | None = None,
                          headers: dict[str, str] | None = None) -> bytes:
        """Download a binary file (a logo or a photo)."""
        try:
            async with self._client.get(url, headers={"Accept": "image/*,*/*;q=0.5",
                                                      **(headers or {})},
                                        **_timeout(timeout)) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status}")
                return await self._read(resp)
        except TimeoutError:
            raise FetchError("таймаут") from None
        except aiohttp.ClientError as exc:
            raise FetchError(_describe(exc)) from exc

    async def post_json(self, url: str, payload: Any, *, headers: dict[str, str] | None = None,
                        timeout: float | None = None) -> tuple[int, Any]:
        """POST a JSON body. Returns ``(status, parsed JSON or None)``; raises only on network
        errors, so the caller can read API error messages from non-200 answers."""
        try:
            async with self._client.post(
                url, json=payload, headers={"Accept": "application/json", **(headers or {})},
                **_timeout(timeout),
            ) as resp:
                body = await self._read(resp)
                status = resp.status
        except TimeoutError:
            raise FetchError("таймаут") from None
        except aiohttp.ClientError as exc:
            raise FetchError(_describe(exc)) from exc
        try:
            return status, json.loads(body)
        except ValueError:
            return status, None

    @staticmethod
    async def _read(resp: aiohttp.ClientResponse) -> bytes:
        if resp.content_length is not None and resp.content_length > MAX_BODY_BYTES:
            raise FetchError("слишком большой ответ")
        chunks: list[bytes] = []
        size = 0
        async for chunk in resp.content.iter_chunked(64 * 1024):
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise FetchError("слишком большой ответ")
            chunks.append(chunk)
        return b"".join(chunks)


_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.IGNORECASE)


def _meta_charset(body: bytes) -> str | None:
    match = _META_CHARSET_RE.search(body[:4096])
    return match.group(1).decode("ascii") if match else None


def _timeout(seconds: float | None) -> dict[str, aiohttp.ClientTimeout]:
    """Per-request timeout kwargs. Passing ``timeout=None`` to aiohttp would disable it."""
    return {"timeout": aiohttp.ClientTimeout(total=seconds)} if seconds else {}


def _describe(exc: aiohttp.ClientError) -> str:
    if isinstance(exc, aiohttp.ClientConnectorError):
        return "нет соединения с сайтом"
    if isinstance(exc, aiohttp.ClientResponseError):
        return f"HTTP {exc.status}"
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
