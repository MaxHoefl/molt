from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

import aiohttp

from src.constants import HTTP_TIMEOUT_SECONDS, USER_AGENT


@dataclass(frozen=True)
class HttpResponse:
    status: int
    data: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def not_found(self) -> bool:
        return self.status == 404


class HttpError(Exception):
    """Raised when a request could not be completed at all (transport, timeout, bad payload)."""


class HttpSession(ABC):
    """Minimal JSON-over-HTTP surface, narrow enough for tests to substitute."""

    @abstractmethod
    async def get_json(self, url: str, params: dict[str, str] | None = None) -> HttpResponse:
        raise NotImplementedError()

    @abstractmethod
    async def post_json(self, url: str, payload: dict[str, Any]) -> HttpResponse:
        raise NotImplementedError()


class AiohttpSession(HttpSession):
    """Live HTTP session, polite by construction: bounded concurrency and an identifying User-Agent."""

    def __init__(
        self,
        timeout_seconds: float = HTTP_TIMEOUT_SECONDS,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> AiohttpSession:
        self._session = aiohttp.ClientSession(timeout=self._timeout, headers=self._headers)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def get_json(self, url: str, params: dict[str, str] | None = None) -> HttpResponse:
        return await self._request("GET", url, params=params)

    async def post_json(self, url: str, payload: dict[str, Any]) -> HttpResponse:
        return await self._request("POST", url, json=payload)

    async def _request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        if self._session is None:
            raise HttpError("AiohttpSession must be entered as an async context manager before use")
        try:
            async with self._session.request(method, url, **kwargs) as response:
                return HttpResponse(
                    status=response.status,
                    data=await self._decode(response),
                    headers=dict(response.headers),
                )
        except asyncio.TimeoutError as e:
            raise HttpError(f"{method} {url} timed out") from e
        except aiohttp.ClientError as e:
            raise HttpError(f"{method} {url} failed: {e}") from e

    @staticmethod
    async def _decode(response: aiohttp.ClientResponse) -> Any:
        if not (200 <= response.status < 300):
            return None
        try:
            return await response.json(content_type=None)
        except ValueError as e:
            raise HttpError(f"{response.url} returned a non-JSON body") from e


class MeteredSession(HttpSession):
    """Wraps another session to bound in-flight requests and count how many were made.

    Keeping the politeness cap here rather than inside AiohttpSession means the same
    bound applies to whatever session the enricher was handed.
    """

    def __init__(self, session: HttpSession, max_concurrency: int) -> None:
        self._session = session
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._requests = 0
        self._peak_in_flight = 0
        self._in_flight = 0

    @property
    def requests(self) -> int:
        return self._requests

    @property
    def peak_in_flight(self) -> int:
        return self._peak_in_flight

    async def get_json(self, url: str, params: dict[str, str] | None = None) -> HttpResponse:
        async with self._gate():
            return await self._session.get_json(url, params)

    async def post_json(self, url: str, payload: dict[str, Any]) -> HttpResponse:
        async with self._gate():
            return await self._session.post_json(url, payload)

    @asynccontextmanager
    async def _gate(self) -> AsyncIterator[None]:
        async with self._semaphore:
            self._requests += 1
            self._in_flight += 1
            self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
            try:
                yield
            finally:
                self._in_flight -= 1
