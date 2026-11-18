"""Asks a package index what the current version of each pin is.

This layer fetches and parses. It does not decide what counts as stale, does
not write to the database and does not format anything, so the one thing it
can get wrong is reading the registry.

See standard 4 in the README.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from pythonstandards.config import AppConfig
from pythonstandards.constants import (
    PACKAGE_PATH_TEMPLATE,
    RATE_LIMIT_STATUSES,
    RELEASE_UPLOAD_TIME_KEY,
    RESPONSE_INFO_KEY,
    RESPONSE_RELEASES_KEY,
    RESPONSE_VERSION_KEY,
    STATUS_NOT_FOUND,
    STATUS_OK,
    USER_AGENT,
    USER_AGENT_HEADER,
)
from pythonstandards.models import Observation

logger = logging.getLogger(__name__)

# Everything a request to somebody else's URL can raise. httpx.InvalidURL,
# CookieConflict and StreamError do NOT inherit from httpx.HTTPError, so a
# guard naming HTTPError alone lets them through and one malformed URL ends the
# run. ValueError and UnicodeError cover the standard library's URL parsing,
# which raises a bare ValueError on an unbalanced bracket in the authority.
REQUEST_ERRORS = (
    httpx.HTTPError,
    httpx.InvalidURL,
    httpx.CookieConflict,
    httpx.StreamError,
    ValueError,
    UnicodeError,
)


class RegistryMonitor:
    """One observation per pin, with a failure recorded rather than raised.

    A single package the index cannot answer about is information, not a
    reason to abandon the run: the other pins still have answers worth
    reporting. A failure that means no pin can be checked, such as the index
    being unreachable throughout, surfaces through every observation carrying
    an error, which the caller treats as a hard failure.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.notes: list[str] = []

    def _timeout(self) -> httpx.Timeout:
        """Connect, read, write and pool bounded separately.

        One number sets the per-operation timeout, which a host evades by
        sending a byte before each read deadline expires. Splitting the budget
        caps the connect and pool waits too, and the caller enforces the
        wall-clock deadline that httpx does not offer.
        """
        per_operation = float(self.config.registry.request_timeout_seconds)
        return httpx.Timeout(
            connect=per_operation,
            read=per_operation,
            write=per_operation,
            pool=per_operation,
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={USER_AGENT_HEADER: USER_AGENT},
            timeout=self._timeout(),
            follow_redirects=False,
        )

    async def _pause(self) -> None:
        """Pace the requests, because a public index owes nobody a quota."""
        import asyncio

        delay = float(self.config.registry.delay_between_requests_s)
        if delay > 0:
            await asyncio.sleep(delay)

    def _url(self, name: str) -> str:
        return PACKAGE_PATH_TEMPLATE.format(base=self.config.registry.base_url, name=name)

    async def _read_capped(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[httpx.Response, bool]:
        """GET, reading at most max_response_bytes and stopping at the deadline.

        A plain get() buffers the whole body before any cap can apply, so the
        download happens whatever the cap says. Streaming lets both limits
        apply to the transfer itself.

        Returns the response and whether it was cut short, because a caller
        must not read a truncated body as the index's answer.

        The transfer headers do not travel to the returned response, because
        iter_bytes yields DECODED bytes: copying Content-Encoding would make
        httpx try to decompress already-decompressed text, and a stale
        Content-Length would contradict a truncated body.
        """
        cap = self.config.registry.max_response_bytes
        deadline = float(self.config.registry.request_deadline_seconds)
        started = time.monotonic()
        chunks: list[bytes] = []
        size = 0

        truncated = False
        async with client.stream("GET", url) as streamed:
            async for chunk in streamed.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= cap or time.monotonic() - started > deadline:
                    truncated = True
                    break
            headers = httpx.Headers(
                [
                    (name, value)
                    for name, value in streamed.headers.items()
                    if name.lower() not in ("content-encoding", "content-length")
                ]
            )
            status = streamed.status_code

        response = httpx.Response(
            status_code=status,
            headers=headers,
            content=b"".join(chunks)[:cap],
            request=httpx.Request("GET", url),
        )
        # Truncation must be reported as truncation. A JSON body cut mid-string
        # fails to parse, and attributing that to the index says the index
        # returned something malformed when the agent is what cut it. Observed
        # against a real index whose payload for one package exceeds the cap.
        return response, truncated

    @staticmethod
    def _latest_release_date(payload: dict, version: str) -> datetime | None:
        """When the registry says that version was published.

        Returns None rather than raising on anything unexpected. The field is
        optional in practice, and a missing publication date costs one
        finding type while a raised exception costs the whole run.
        """
        releases = payload.get(RESPONSE_RELEASES_KEY) or {}
        files = releases.get(version) or []
        stamps = [
            entry.get(RELEASE_UPLOAD_TIME_KEY)
            for entry in files
            if isinstance(entry, dict) and entry.get(RELEASE_UPLOAD_TIME_KEY)
        ]
        for stamp in sorted(stamps):
            try:
                return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                continue
        return None

    async def observe(
        self, client: httpx.AsyncClient, name: str, pinned_version: str
    ) -> Observation:
        """One package, one observation, never an exception."""
        url = self._url(name)
        try:
            response, truncated = await self._read_capped(client, url)
        except REQUEST_ERRORS as exc:
            return Observation(name=name, pinned_version=pinned_version, error=str(exc))
        if truncated:
            self.notes.append(
                f"the index response for {name} exceeded a transfer limit and was truncated"
            )
            return Observation(
                name=name,
                pinned_version=pinned_version,
                error=(
                    "this agent truncated the response before it finished, so the "
                    "version could not be read. Raise max_response_bytes."
                ),
            )

        if response.status_code == STATUS_NOT_FOUND:
            return Observation(
                name=name,
                pinned_version=pinned_version,
                error=f"the index has no package named {name}",
            )
        if response.status_code in RATE_LIMIT_STATUSES:
            # A protective status says nothing about the package, so it must
            # not be read as an answer about it.
            self.notes.append(f"the index rate limited the request for {name}")
            return Observation(
                name=name,
                pinned_version=pinned_version,
                error=f"the index returned {response.status_code}, which is not an answer",
            )
        if response.status_code != STATUS_OK:
            return Observation(
                name=name,
                pinned_version=pinned_version,
                error=f"the index returned HTTP {response.status_code}",
            )

        # A 200 does not promise JSON. A proxy or a maintenance page answers
        # 200 with HTML, and json() raises a ValueError subclass that is not a
        # request error, so it escapes the guard above.
        try:
            payload = response.json()
        except ValueError as exc:
            return Observation(
                name=name,
                pinned_version=pinned_version,
                error=f"the index returned a body that is not JSON: {exc}",
            )
        if not isinstance(payload, dict):
            return Observation(
                name=name,
                pinned_version=pinned_version,
                error=f"the index returned {type(payload).__name__} rather than an object",
            )

        info = payload.get(RESPONSE_INFO_KEY) or {}
        latest = str(info.get(RESPONSE_VERSION_KEY) or "")
        released = self._latest_release_date(payload, latest) if latest else None
        return Observation(
            name=name,
            pinned_version=pinned_version,
            latest_version=latest,
            latest_released_on=released.date() if released else None,
        )

    async def run(self, pins: dict[str, str]) -> list[Observation]:
        """Every pin, paced, with each failure recorded against its package."""
        observations: list[Observation] = []
        async with self._client() as client:
            for index, (name, pinned) in enumerate(sorted(pins.items())):
                if index:
                    await self._pause()
                observations.append(await self.observe(client, name, pinned))
        logger.info("observed %d package(s)", len(observations))
        return observations
