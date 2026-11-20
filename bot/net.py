"""Shared aiohttp client with timeouts, size limits and conditional GET for feeds."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiohttp

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MAX_BODY_BYTES = 8 * 1024 * 1024


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

    async def fetch_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        try:
            async with self._client.get(url, params=params,
                                        headers={"Accept": "application/json"}) as resp:
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

    async def fetch_text(self, url: str, *, timeout: float | None = None) -> str:
        """Download a web page (an article) and decode it."""
        try:
            async with self._client.get(
                url, headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
                **_timeout(timeout),
            ) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status}")
                body = await self._read(resp)
                try:
                    encoding = resp.get_encoding()
                except RuntimeError:
                    encoding = "utf-8"
        except TimeoutError:
            raise FetchError("таймаут") from None
        except aiohttp.ClientError as exc:
            raise FetchError(_describe(exc)) from exc
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    async def fetch_bytes(self, url: str, *, timeout: float | None = None) -> bytes:
        """Download a binary file (a logo or a photo)."""
        try:
            async with self._client.get(url, headers={"Accept": "image/*,*/*;q=0.5"},
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
