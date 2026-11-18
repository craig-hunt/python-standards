"""The defaults and the small contracts nothing else exercises.

Every value here has a default that only fires when a caller omits the
argument, so an ordinary test passing one explicitly never executes it. A
wrong default then ships, works everywhere the caller is explicit, and
surprises whoever first relies on it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from pythonstandards import constants
from pythonstandards.analysis.freshness import (
    THRESHOLD_DAYS,
    THRESHOLD_MAJOR,
    THRESHOLD_MINOR,
    findings_for,
)
from pythonstandards.config import AppConfig, RegistryConfig, Secrets, load_config
from pythonstandards.models import Digest, FindingType, Observation, RunResult, agent_today
from pythonstandards.monitors.registry_monitor import RegistryMonitor
from tests.conftest import make_finding, make_observation

ROOT = Path(__file__).resolve().parents[2]


# --- the default argument on every entry point ---


def test_load_config_defaults_to_the_projects_own_file(monkeypatch):
    """The default path must be the file the repository actually ships."""
    monkeypatch.chdir(ROOT)
    config, _ = load_config()
    assert config.registry.base_url == constants.DEFAULT_REGISTRY_BASE_URL


def test_the_default_config_filename_exists():
    assert (ROOT / "config.yaml").exists()


def test_findings_for_defaults_to_the_agents_clock(app_config):
    """Omitting the date must use the agent's clock, not the machine's."""
    old = make_observation(
        pinned_version="1.0.0",
        latest_version="1.0.0",
        latest_released_on=agent_today() - __import__("datetime").timedelta(days=4000),
    )
    assert FindingType.RELEASE_STALE in {
        finding.finding_type for finding in findings_for(old, app_config)
    }


def test_a_finding_defaults_its_first_seen_to_today():
    from pythonstandards.models import Finding, Severity

    finding = Finding(
        finding_type=FindingType.MINOR_BEHIND,
        severity=Severity.LOW,
        subject="x",
        message="m",
        detail="d",
        recommendation="r",
    )
    assert finding.first_seen == agent_today()


def test_an_observation_defaults_its_date_to_today():
    assert Observation(name="x", pinned_version="1.0").observed_on == agent_today()


def test_a_digest_defaults_its_date_to_today():
    assert Digest().report_date == agent_today()


def test_a_run_result_timestamps_itself_with_a_zone():
    """A naive datetime lands in a timestamptz as though it were UTC, which is
    a silent error of however many hours the machine sits from UTC."""
    stamp = RunResult().run_date
    assert stamp.tzinfo is not None
    assert stamp.utcoffset() == UTC.utcoffset(None)


def test_the_agent_clock_is_utc_rather_than_local():
    assert agent_today() == datetime.now(UTC).date()


# --- the documented fallbacks, which config.yaml mirrors ---


@pytest.mark.parametrize(
    "name,constant",
    [
        (THRESHOLD_MAJOR, constants.DEFAULT_MAJOR_VERSIONS_BEHIND),
        (THRESHOLD_MINOR, constants.DEFAULT_MINOR_VERSIONS_BEHIND),
        (THRESHOLD_DAYS, constants.DEFAULT_DAYS_SINCE_RELEASE),
    ],
)
def test_each_threshold_falls_back_to_its_named_constant(name, constant):
    assert AppConfig().threshold(name, constant) == constant


def test_the_registry_defaults_match_the_named_constants():
    registry = RegistryConfig()
    assert registry.base_url == constants.DEFAULT_REGISTRY_BASE_URL
    assert registry.request_timeout_seconds == constants.DEFAULT_REQUEST_TIMEOUT_SECONDS
    assert registry.request_deadline_seconds == constants.DEFAULT_REQUEST_DEADLINE_SECONDS
    assert registry.max_response_bytes == constants.DEFAULT_MAX_RESPONSE_BYTES
    assert registry.delay_between_requests_s == constants.DEFAULT_REQUEST_DELAY_SECONDS


def test_the_reporting_defaults_match_the_named_constants():
    config = AppConfig()
    assert config.output_dir == Path(constants.DEFAULT_OUTPUT_DIR)
    assert config.observation_days == constants.DEFAULT_OBSERVATION_DAYS
    assert config.retention_enabled is True


def test_the_smtp_port_default_is_the_named_constant(monkeypatch):
    monkeypatch.delenv(constants.ENV_SMTP_PORT, raising=False)
    assert Secrets.from_env().smtp_port == int(constants.DEFAULT_SMTP_PORT)


def test_every_configured_value_has_a_matching_constant():
    """config.yaml and constants.py must not disagree about a default.

    A reader who deletes a key gets the constant, so the two being different
    means deleting a key silently changes behavior.
    """
    import yaml

    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["registry"]["base_url"] == constants.DEFAULT_REGISTRY_BASE_URL
    assert raw["registry"]["max_response_bytes"] == constants.DEFAULT_MAX_RESPONSE_BYTES
    assert raw["retention"]["observation_days"] == constants.DEFAULT_OBSERVATION_DAYS
    assert raw["reporting"]["output_dir"] == constants.DEFAULT_OUTPUT_DIR


# --- the protocol constants, which a registry response is read through ---


def test_the_package_url_uses_every_part_of_its_template(app_config):
    app_config.registry.base_url = "https://index.test/api"
    assert RegistryMonitor(app_config)._url("pkg") == "https://index.test/api/pkg/json"


def test_the_user_agent_identifies_this_project():
    """A public index is owed a way to find whoever is calling it."""
    assert "python-standards" in constants.USER_AGENT
    assert "http" in constants.USER_AGENT


def test_the_status_constants_are_the_ones_the_code_branches_on():
    assert constants.STATUS_OK == 200
    assert constants.STATUS_NOT_FOUND == 404
    assert 429 in constants.RATE_LIMIT_STATUSES
    assert constants.STATUS_OK not in constants.RATE_LIMIT_STATUSES


def test_the_exit_codes_are_what_a_scheduler_expects():
    assert constants.EXIT_OK == 0
    assert constants.EXIT_FAILED != 0


def test_the_schema_table_names_match_the_database_contract():
    from pythonstandards.db import Database

    assert set(constants.SCHEMA_TABLES) == set(Database.REQUIRED_COLUMNS)


# --- small behaviors nothing else reaches ---


def test_a_finding_carries_its_subject_into_its_identity():
    first = make_finding(subject="alpha")
    second = make_finding(subject="beta")
    assert first.id != second.id


def test_an_observation_with_an_error_and_a_version_is_still_unusable():
    """An error means the reading cannot be trusted, whatever else arrived."""
    assert not make_observation(error="partial failure", latest_version="2.0.0").ok


def test_the_report_filename_carries_the_digest_date(app_config, secrets):
    from pythonstandards.agents.report_agent import ReportAgent

    agent = ReportAgent(app_config, secrets)
    digest = Digest(report_date=date(2026, 5, 4))
    path = agent.write("body", digest)
    assert "2026-05-04" in path.name
    assert path.suffix == ".md"
