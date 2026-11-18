"""Fixtures, one per shape, so a change to a shape lands in one file.

Every fixture that a test might write through points somewhere temporary. A
fixture aimed at the project's own output directory means the suite leaves
files beside the real runs' output, which pollutes the directory and makes
test output indistinguishable from a real report.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from pythonstandards.config import AppConfig, RegistryConfig, Secrets
from pythonstandards.models import Finding, FindingType, Observation, Severity

# Fixture values as constants, referenced by every test that needs them. A
# test repeating a literal is a test that keeps passing after the production
# constant it was meant to track has changed.
PACKAGE = "examplelib"
PINNED = "1.2.3"
LATEST = "1.2.3"
TODAY = date(2026, 11, 18)
RELEASED = date(2026, 11, 1)


@pytest.fixture
def app_config() -> AppConfig:
    """Settings with the delay removed, so a test never sleeps."""
    return AppConfig(
        registry=RegistryConfig(delay_between_requests_s=0),
        freshness={
            "major_versions_behind": 1,
            "minor_versions_behind": 3,
            "days_since_release": 365,
        },
        reporting={
            # Temporary, never the project's reports directory.
            "output_dir": tempfile.mkdtemp(prefix="python-standards-test-"),
            "site_name": "Reference agent",
        },
        retention={"enabled": True, "observation_days": 400},
    )


@pytest.fixture
def secrets() -> Secrets:
    """Credentials shaped like real ones and valid nowhere."""
    return Secrets(
        database_url="postgresql://user:not-a-real-password@localhost/none",
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_user="agent@example.test",
        smtp_password="not-a-real-password",
    )


def make_observation(**overrides) -> Observation:
    values = {
        "name": PACKAGE,
        "pinned_version": PINNED,
        "latest_version": LATEST,
        "latest_released_on": RELEASED,
        "observed_on": TODAY,
    }
    values.update(overrides)
    return Observation(**values)


def make_finding(**overrides) -> Finding:
    values = {
        "finding_type": FindingType.MINOR_BEHIND,
        "severity": Severity.MEDIUM,
        "subject": PACKAGE,
        "message": "a finding",
        "detail": "minor_1.5",
        "recommendation": "bump it",
        "first_seen": TODAY,
    }
    values.update(overrides)
    return Finding(**values)


@pytest.fixture
def requirements_file() -> Path:
    """A requirements file in a temporary directory, with the shapes that matter."""
    directory = Path(tempfile.mkdtemp(prefix="python-standards-reqs-"))
    path = directory / "requirements.txt"
    path.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "httpx==0.28.1",
                "psycopg[binary]==3.2.3  # an extras marker and a trailing comment",
                "ruff>=0.16  # a range, which cannot be compared",
                "-e ./local  # an editable install",
            ]
        ),
        encoding="utf-8",
    )
    return path
