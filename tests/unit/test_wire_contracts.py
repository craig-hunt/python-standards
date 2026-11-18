"""The shapes this agent hands to somebody else's library.

A wrong constant here is invisible in an ordinary test: the code runs, the
object is built, and the mistake surfaces in a mail client's rendering or in a
request the index answers differently. These assert the wire shape itself.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from pythonstandards.agents.report_agent import ReportAgent
from pythonstandards.config import Secrets
from pythonstandards.constants import (
    ENV_DATABASE_URL,
    ENV_SMTP_HOST,
    ENV_SMTP_PASSWORD,
    ENV_SMTP_USER,
    USER_AGENT,
    USER_AGENT_HEADER,
)
from pythonstandards.models import Digest
from pythonstandards.monitors.registry_monitor import RegistryMonitor

# --- the email the agent builds ---


def sent_message(app_config, secrets, recipients=("to@example.test",)):
    with patch("smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__.return_value = server
        ReportAgent(app_config, secrets).send("the body", Digest(), list(recipients))
    return server.send_message.call_args[0][0]


def test_the_message_is_multipart_alternative(app_config, secrets):
    """A client picks a part by subtype. The wrong subtype makes it pick
    nothing, and the reader sees an attachment rather than a digest."""
    message = sent_message(app_config, secrets)
    assert message.get_content_type() == "multipart/alternative"


def test_the_message_carries_the_configured_sender_and_recipients(app_config, secrets):
    message = sent_message(app_config, secrets, ["a@example.test", "b@example.test"])
    assert message["From"] == secrets.smtp_user
    assert message["To"] == "a@example.test, b@example.test"


def test_the_subject_names_the_digest_date(app_config, secrets):
    digest = Digest()
    with patch("smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__.return_value = server
        ReportAgent(app_config, secrets).send("body", digest, ["to@example.test"])
    message = server.send_message.call_args[0][0]
    assert digest.report_date.isoformat() in message["Subject"]


def test_the_body_reaches_the_message(app_config, secrets):
    message = sent_message(app_config, secrets)
    payload = message.get_payload()[0].get_payload()
    assert "the body" in payload


def test_the_body_part_is_plain_text(app_config, secrets):
    """Markdown rendered as text/html would show its own syntax to the reader."""
    message = sent_message(app_config, secrets)
    assert message.get_payload()[0].get_content_type() == "text/plain"


def test_authentication_uses_the_configured_credentials(app_config, secrets):
    with patch("smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__.return_value = server
        ReportAgent(app_config, secrets).send("body", Digest(), ["to@example.test"])
    server.login.assert_called_once_with(secrets.smtp_user, secrets.smtp_password)


# --- what the agent asks the index ---


def captured_request(app_config, handler_result=None):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return handler_result or httpx.Response(
            200, json={"info": {"version": "1.0.0"}, "releases": {"1.0.0": []}}
        )

    monitor = RegistryMonitor(app_config)
    monitor._client = lambda: httpx.AsyncClient(
        headers={USER_AGENT_HEADER: USER_AGENT},
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    return monitor, seen


async def test_the_agent_asks_with_get(app_config):
    """A HEAD would return no body, and a POST would be refused."""
    monitor, seen = captured_request(app_config)
    await monitor.run({"pkg": "1.0.0"})
    assert [request.method for request in seen] == ["GET"]


async def test_the_agent_identifies_itself(app_config):
    """A public index is owed a way to find whoever is calling it."""
    monitor, seen = captured_request(app_config)
    await monitor.run({"pkg": "1.0.0"})
    assert seen[0].headers[USER_AGENT_HEADER] == USER_AGENT


async def test_the_agent_asks_for_the_package_it_was_given(app_config):
    monitor, seen = captured_request(app_config)
    await monitor.run({"specific-name": "1.0.0"})
    assert "specific-name" in str(seen[0].url)


# --- the transfer limits, at their boundaries ---


@pytest.mark.parametrize(
    "body_size,cap,expect_truncated",
    [(10, 1000, False), (1000, 1000, True), (5000, 1000, True)],
)
async def test_the_cap_applies_at_its_boundary(app_config, body_size, cap, expect_truncated):
    app_config.registry.max_response_bytes = cap
    monitor = RegistryMonitor(app_config)
    monitor._client = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * body_size)
        ),
        follow_redirects=False,
    )
    observations = await monitor.run({"pkg": "1.0.0"})
    truncated = "truncated" in observations[0].error
    assert truncated is expect_truncated


async def test_a_truncated_body_is_never_longer_than_the_cap(app_config):
    app_config.registry.max_response_bytes = 64
    monitor = RegistryMonitor(app_config)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"y" * 5000)),
        follow_redirects=False,
    ) as client:
        response, truncated = await monitor._read_capped(client, "https://index.test/x")

    assert truncated is True
    assert len(response.content) <= 64


async def test_an_untruncated_body_reports_no_truncation(app_config):
    app_config.registry.max_response_bytes = 1_000_000
    monitor = RegistryMonitor(app_config)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"small")),
        follow_redirects=False,
    ) as client:
        response, truncated = await monitor._read_capped(client, "https://index.test/x")

    assert truncated is False
    assert response.content == b"small"


async def test_the_status_survives_the_rebuilt_response(app_config):
    """The response is reassembled from streamed chunks, so the status has to
    be carried across deliberately."""
    monitor = RegistryMonitor(app_config)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(418, content=b"x")),
        follow_redirects=False,
    ) as client:
        response, _ = await monitor._read_capped(client, "https://index.test/x")

    assert response.status_code == 418


# --- the environment, which supplies every secret ---


@pytest.mark.parametrize(
    "variable,attribute",
    [
        (ENV_DATABASE_URL, "database_url"),
        (ENV_SMTP_HOST, "smtp_host"),
        (ENV_SMTP_USER, "smtp_user"),
        (ENV_SMTP_PASSWORD, "smtp_password"),
    ],
)
def test_each_secret_reads_its_own_variable(monkeypatch, variable, attribute):
    monkeypatch.setenv(variable, f"value-for-{attribute}")
    assert getattr(Secrets.from_env(), attribute) == f"value-for-{attribute}"


@pytest.mark.parametrize("attribute", ["database_url", "smtp_host", "smtp_user", "smtp_password"])
def test_an_unset_secret_is_an_empty_string_rather_than_none(monkeypatch, attribute):
    """None would reach psycopg and smtplib as a value rather than an absence,
    and each reports that differently and unhelpfully."""
    for variable in (ENV_DATABASE_URL, ENV_SMTP_HOST, ENV_SMTP_USER, ENV_SMTP_PASSWORD):
        monkeypatch.delenv(variable, raising=False)
    assert getattr(Secrets.from_env(), attribute) == ""
