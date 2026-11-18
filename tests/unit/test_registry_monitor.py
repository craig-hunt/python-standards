"""The fetch layer, against a mock transport rather than the real index.

Every case here is one a public index actually produces. A monitor that only
handles the happy path fails on a Tuesday when the index is busy, and the
failure arrives as a traceback in a scheduler's log.
"""

from __future__ import annotations

import httpx
import pytest

from pythonstandards.constants import RATE_LIMIT_STATUSES
from pythonstandards.monitors.registry_monitor import REQUEST_ERRORS, RegistryMonitor
from tests.conftest import PACKAGE, PINNED

LATEST = "2.1.0"
RELEASED = "2026-03-04T12:00:00Z"


def payload(version: str = LATEST, released: str | None = RELEASED) -> dict:
    files = [{"upload_time_iso_8601": released}] if released else []
    return {"info": {"version": version}, "releases": {version: files}}


def monitor_with(app_config, handler) -> RegistryMonitor:
    monitor = RegistryMonitor(app_config)
    monitor._client = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    return monitor


async def observe_one(app_config, handler):
    monitor = monitor_with(app_config, handler)
    observations = await monitor.run({PACKAGE: PINNED})
    return observations[0], monitor


# --- the answer the agent wants ---


async def test_a_good_response_yields_a_version_and_a_date(app_config):
    observation, _ = await observe_one(
        app_config, lambda request: httpx.Response(200, json=payload())
    )
    assert observation.ok
    assert observation.latest_version == LATEST
    assert observation.latest_released_on.isoformat() == "2026-03-04"


async def test_a_response_without_a_publication_date_still_yields_a_version(app_config):
    observation, _ = await observe_one(
        app_config, lambda request: httpx.Response(200, json=payload(released=None))
    )
    assert observation.ok
    assert observation.latest_released_on is None


async def test_the_earliest_upload_is_the_release_date(app_config):
    """A release has many files uploaded over minutes; the first is the release."""
    body = {
        "info": {"version": LATEST},
        "releases": {
            LATEST: [
                {"upload_time_iso_8601": "2026-03-04T12:30:00Z"},
                {"upload_time_iso_8601": "2026-03-04T12:00:00Z"},
            ]
        },
    }
    observation, _ = await observe_one(app_config, lambda request: httpx.Response(200, json=body))
    assert observation.latest_released_on.isoformat() == "2026-03-04"


# --- answers that are not answers ---


async def test_an_unknown_package_is_a_finding_about_the_pin(app_config):
    observation, _ = await observe_one(app_config, lambda request: httpx.Response(404))
    assert not observation.ok
    assert PACKAGE in observation.error


@pytest.mark.parametrize("status", RATE_LIMIT_STATUSES)
async def test_a_protective_status_is_never_read_as_an_answer(app_config, status):
    """A 429 says the index is busy, not that the package is missing."""
    observation, monitor = await observe_one(app_config, lambda request: httpx.Response(status))
    assert not observation.ok
    assert "not an answer" in observation.error
    assert any("rate limited" in note for note in monitor.notes)


async def test_an_unexpected_status_reports_the_status(app_config):
    observation, _ = await observe_one(app_config, lambda request: httpx.Response(500))
    assert "HTTP 500" in observation.error


async def test_a_two_hundred_carrying_html_is_not_read_as_json(app_config):
    """A proxy or a maintenance page answers 200 with HTML."""
    observation, _ = await observe_one(
        app_config, lambda request: httpx.Response(200, text="<html>maintenance</html>")
    )
    assert "not JSON" in observation.error


async def test_a_json_array_rather_than_an_object_is_rejected(app_config):
    observation, _ = await observe_one(
        app_config, lambda request: httpx.Response(200, json=[1, 2, 3])
    )
    assert "rather than an object" in observation.error


async def test_a_response_missing_the_version_field_is_not_usable(app_config):
    observation, _ = await observe_one(
        app_config, lambda request: httpx.Response(200, json={"info": {}})
    )
    assert not observation.ok


# --- limits that protect the process ---


async def test_a_truncated_response_says_it_was_truncated(app_config):
    """Attributing the agent's own cut to the index would be a false finding.

    A real index payload exceeded the original cap and was reported as a
    malformed response, which named the wrong culprit.
    """
    app_config.registry.max_response_bytes = 50
    observation, monitor = await observe_one(
        app_config, lambda request: httpx.Response(200, content=b"x" * 5000)
    )
    assert "truncated" in observation.error
    assert "max_response_bytes" in observation.error
    assert any("truncated" in note for note in monitor.notes)


async def test_a_body_under_the_cap_is_read_whole(app_config):
    app_config.registry.max_response_bytes = 1_000_000
    observation, monitor = await observe_one(
        app_config, lambda request: httpx.Response(200, json=payload())
    )
    assert observation.ok
    assert monitor.notes == []


async def test_a_compressed_body_is_not_decoded_twice(app_config):
    """aiter_bytes yields decoded bytes, so the transfer headers must not travel.

    Copying Content-Encoding onto the rebuilt response would make httpx try to
    gunzip already-gunzipped text and raise.
    """
    import gzip
    import json

    body = json.dumps(payload()).encode()

    def handler(request):
        return httpx.Response(
            200,
            content=gzip.compress(body),
            headers={"content-encoding": "gzip", "content-type": "application/json"},
        )

    observation, _ = await observe_one(app_config, handler)
    assert observation.ok
    assert observation.latest_version == LATEST


# --- failures that must not end the run ---


def test_the_error_tuple_covers_what_is_not_an_http_error():
    """httpx.InvalidURL and friends do not inherit from httpx.HTTPError.

    A guard naming HTTPError alone lets them through, and one malformed URL
    then ends the whole run.
    """
    for cls in (httpx.InvalidURL, httpx.CookieConflict, httpx.StreamError):
        assert not issubclass(cls, httpx.HTTPError), cls
        assert cls in REQUEST_ERRORS, cls
    assert httpx.HTTPError in REQUEST_ERRORS
    assert ValueError in REQUEST_ERRORS


async def test_a_transport_failure_is_recorded_against_its_package(app_config):
    def handler(request):
        raise httpx.ConnectError("name resolution failed")

    observation, _ = await observe_one(app_config, handler)
    assert "name resolution failed" in observation.error


async def test_a_malformed_base_url_does_not_raise(app_config):
    app_config.registry.base_url = "https://example.com:bad"
    monitor = monitor_with(app_config, lambda request: httpx.Response(200, json=payload()))
    observations = await monitor.run({PACKAGE: PINNED})
    assert observations[0].error


# --- the whole pass ---


async def test_one_failing_package_does_not_cost_the_others(app_config):
    """A single unanswerable pin is information, not a reason to abandon."""

    def handler(request):
        if "ghost" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, json=payload())

    monitor = monitor_with(app_config, handler)
    observations = await monitor.run({"ghost": "1.0", "good": "1.0"})
    assert [observation.ok for observation in sorted(observations, key=lambda o: o.name)] == [
        False,
        True,
    ]


async def test_every_pin_is_observed(app_config):
    monitor = monitor_with(app_config, lambda request: httpx.Response(200, json=payload()))
    pins = {f"pkg{index}": "1.0.0" for index in range(5)}
    assert len(await monitor.run(pins)) == len(pins)


async def test_no_pins_means_no_requests(app_config):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, json=payload())

    monitor = monitor_with(app_config, handler)
    assert await monitor.run({}) == []
    assert requested == []


async def test_the_pass_paces_itself(app_config, monkeypatch):
    """A public index owes nobody a quota, so the monitor does not discover
    the limit by hitting it."""
    import asyncio

    slept: list[float] = []

    async def record(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    app_config.registry.delay_between_requests_s = 0.25
    monitor = monitor_with(app_config, lambda request: httpx.Response(200, json=payload()))
    await monitor.run({"a": "1.0", "b": "1.0", "c": "1.0"})
    # One pause between each pair, never before the first request.
    assert slept == [0.25, 0.25]


def test_the_timeout_bounds_every_phase(app_config):
    """One number sets the per-operation timeout, which a trickling host evades."""
    app_config.registry.request_timeout_seconds = 7
    timeout = RegistryMonitor(app_config)._timeout()
    assert timeout.connect == 7
    assert timeout.read == 7
    assert timeout.write == 7
    assert timeout.pool == 7


def test_the_url_is_built_from_the_configured_base(app_config):
    app_config.registry.base_url = "https://index.example.test/pypi"
    assert RegistryMonitor(app_config)._url("httpx") == (
        "https://index.example.test/pypi/httpx/json"
    )
