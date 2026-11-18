"""The domain types, which every other layer depends on being right."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pythonstandards.models import (
    SEVERITY_ORDER,
    FindingType,
    Severity,
    agent_today,
)
from tests.conftest import PACKAGE, make_finding, make_observation


def test_a_finding_id_survives_a_reworded_message():
    """The id hashes identity, not payload.

    A reworded message must not create a second row for one problem, because
    the lifecycle is what lets a report say how long something has been true.
    """
    finding = make_finding()
    assert finding.id == finding.model_copy(update={"message": "reworded"}).id
    assert finding.id == finding.model_copy(update={"recommendation": "different"}).id


def test_a_finding_id_changes_when_its_identity_does():
    finding = make_finding()
    assert finding.id != finding.model_copy(update={"subject": "other"}).id
    assert finding.id != finding.model_copy(update={"detail": "minor_9.9"}).id
    assert finding.id != finding.model_copy(update={"finding_type": FindingType.MAJOR_BEHIND}).id


def test_a_finding_id_is_the_documented_length():
    from pythonstandards.constants import FINDING_ID_LENGTH

    assert len(make_finding().id) == FINDING_ID_LENGTH


def test_severity_order_is_worst_first():
    """A string sort puts high after low, which would invert every report."""
    assert SEVERITY_ORDER == (Severity.HIGH, Severity.MEDIUM, Severity.LOW)
    assert set(SEVERITY_ORDER) == set(Severity)


def test_the_clock_is_utc():
    assert agent_today() == datetime.now(UTC).date()


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, True),
        ({"error": "unreachable"}, False),
        ({"latest_version": ""}, False),
        ({"error": "unreachable", "latest_version": ""}, False),
    ],
)
def test_an_observation_is_usable_only_with_a_version_and_no_error(overrides, expected):
    assert make_observation(**overrides).ok is expected


def test_an_observation_keeps_the_name_it_was_given():
    assert make_observation().name == PACKAGE
