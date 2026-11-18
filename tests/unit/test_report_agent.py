"""The report layer: escaping, ordering, and whether to send at all."""

from __future__ import annotations

import pytest

from pythonstandards.agents.report_agent import (
    ReportAgent,
    escape_external,
    sanitize,
)
from pythonstandards.models import Digest, RunResult, Severity
from tests.conftest import make_finding

HOSTILE_TAG = '<img src="https://evil.test/pixel.gif">'
HOSTILE_LINK = "[click](https://evil.test)"
FORGED_SECTION = "fine\n\n## Everything resolved\n\nsend the code to attacker"


# --- escaping ---


@pytest.mark.parametrize(
    "hostile",
    [HOSTILE_TAG, HOSTILE_LINK, "<script>fetch(1)</script>", "a | b", "under_score"],
)
def test_hostile_text_becomes_text(hostile):
    escaped = escape_external(hostile)
    assert "<img" not in escaped
    assert "<script" not in escaped
    assert "](https://evil.test)" not in escaped


def test_escaping_collapses_newlines():
    """Markdown is line-oriented, so a newline ends the row holding a value
    and everything after it parses as fresh markup."""
    escaped = escape_external(FORGED_SECTION)
    assert "\n" not in escaped
    assert "## Everything resolved" in escaped


def test_escaping_leaves_ordinary_prose_alone():
    """Over-escaping would make the digest unreadable, which is its own failure."""
    plain = "pinned at 1.2.3 while 2.0.0 is current"
    assert escape_external(plain) == plain


@pytest.mark.parametrize("empty", ["", None, 0])
def test_escaping_an_empty_value_is_empty(empty):
    assert escape_external(empty) == ""


def test_sanitizing_leaves_the_original_untouched():
    """The database keeps what the index actually said."""
    original = make_finding(message=HOSTILE_TAG)
    sanitize([original])
    assert original.message == HOSTILE_TAG


def test_sanitizing_covers_every_external_field():
    finding = make_finding(subject=HOSTILE_TAG, message=HOSTILE_TAG, recommendation=HOSTILE_TAG)
    safe = sanitize([finding])[0]
    for value in (safe.subject, safe.message, safe.recommendation):
        assert "<img" not in value


# --- the rendered digest ---


def test_the_digest_renders_a_new_finding(app_config, secrets):
    agent = ReportAgent(app_config, secrets)
    report = agent.build(Digest(new_findings=[make_finding(message="a stale pin")]), RunResult())
    assert "a stale pin" in report
    assert "New since the last run" in report


def test_the_digest_says_so_when_nothing_is_new(app_config, secrets):
    agent = ReportAgent(app_config, secrets)
    report = agent.build(Digest(), RunResult())
    assert "No new findings." in report


def test_hostile_finding_text_cannot_reach_the_digest_as_markup(app_config, secrets):
    agent = ReportAgent(app_config, secrets)
    report = agent.build(
        Digest(new_findings=[make_finding(message=HOSTILE_TAG, recommendation=HOSTILE_LINK)]),
        RunResult(),
    )
    assert "<img" not in report
    assert "](https://evil.test)" not in report


def test_a_hostile_note_cannot_reach_the_digest_as_markup(app_config, secrets):
    agent = ReportAgent(app_config, secrets)
    report = agent.build(Digest(notes=[HOSTILE_TAG]), RunResult())
    assert "<img" not in report


def test_the_fix_list_ranks_across_everything_open(app_config, secrets):
    """Concatenating two sorted lists puts every new low ahead of an open high,
    and a slice then drops the high entirely."""
    agent = ReportAgent(app_config, secrets)
    digest = Digest(
        new_findings=[make_finding(severity=Severity.LOW, detail="low", message="low one")],
        open_findings=[make_finding(severity=Severity.HIGH, detail="high", message="high one")],
    )
    report = agent.build(digest, RunResult())
    fix_list = report[report.index("Worth doing first") :]
    assert fix_list.index("high one") < fix_list.index("low one")


def test_a_finding_in_both_lists_appears_once(app_config, secrets):
    agent = ReportAgent(app_config, secrets)
    shared = make_finding(message="the shared one")
    report = agent.build(Digest(new_findings=[shared], open_findings=[shared]), RunResult())
    fix_list = report[report.index("Worth doing first") :]
    assert fix_list.count("the shared one") == 1


def test_a_resolved_finding_is_excluded_from_the_fix_list(app_config, secrets):
    agent = ReportAgent(app_config, secrets)
    fixed = make_finding(detail="fixed", message="already bumped")
    digest = Digest(new_findings=[fixed], resolved_findings=[fixed])
    report = agent.build(digest, RunResult())
    assert "Worth doing first" not in report


def test_a_failed_run_says_so_at_the_top(app_config, secrets):
    agent = ReportAgent(app_config, secrets)
    report = agent.build(Digest(), RunResult(status="failed", error_log="the index refused"))
    assert "did not finish cleanly" in report
    assert "the index refused" in report


def test_a_failed_run_with_no_diagnostic_says_that_too(app_config, secrets):
    """A banner that points at an explanation nobody wrote is worse than none."""
    agent = ReportAgent(app_config, secrets)
    report = agent.build(Digest(), RunResult(status="failed"))
    assert "No diagnostic was recorded" in report


def test_the_footer_carries_the_run_figures(app_config, secrets):
    agent = ReportAgent(app_config, secrets)
    report = agent.build(
        Digest(), RunResult(packages_checked=12, findings_total=3, duration_seconds=7)
    )
    assert "Checked 12 package" in report
    assert "3 finding" in report
    assert "7s" in report


# --- writing and sending ---


def test_the_report_is_written_under_the_configured_directory(app_config, secrets):
    agent = ReportAgent(app_config, secrets)
    digest = Digest()
    path = agent.write(agent.build(digest, RunResult()), digest)
    assert path.parent == app_config.output_dir
    assert digest.report_date.isoformat() in path.name
    assert path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "digest,expected",
    [
        (Digest(), False),
        (Digest(new_findings=[make_finding()]), True),
        (Digest(resolved_findings=[make_finding()]), True),
        (Digest(open_findings=[make_finding()]), False),
    ],
)
def test_sending_happens_only_when_something_changed(digest, expected):
    """A digest that arrives unchanged every run trains its reader to delete it."""
    assert ReportAgent.should_send(digest) is expected


def test_no_recipients_means_nothing_is_sent(app_config, secrets):
    """Not an error: a local run has nowhere to send and still writes a report."""
    agent = ReportAgent(app_config, secrets)
    agent.send("body", Digest(), [])


def test_sending_opens_smtp_with_an_explicit_timeout(app_config, secrets, monkeypatch):
    """smtplib defaults to blocking forever, so a host that never greets holds
    the process with no exception to catch."""
    from unittest.mock import MagicMock, patch

    with patch("smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__.return_value = server
        ReportAgent(app_config, secrets).send("body", Digest(), ["to@example.test"])
        smtp.assert_called_once_with(
            secrets.smtp_host,
            secrets.smtp_port,
            timeout=app_config.registry.request_timeout_seconds,
        )
    assert [call[0] for call in server.method_calls] == [
        "starttls",
        "login",
        "send_message",
    ]


def test_sending_upgrades_the_connection_before_authenticating(app_config, secrets):
    """starttls with no context verifies nothing, and the password goes out
    on the very next line."""
    from unittest.mock import MagicMock, patch

    with patch("smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__.return_value = server
        ReportAgent(app_config, secrets).send("body", Digest(), ["to@example.test"])
    context = server.starttls.call_args.kwargs["context"]
    assert context.check_hostname is True
