"""The analysis layer, where every threshold earns a test at its boundary.

Each test names the behavior rather than the function, because a failure
should read as a statement about the agent rather than as a function name
somebody then has to go and read.
"""

from __future__ import annotations

from datetime import date

import pytest

from pythonstandards.analysis.freshness import (
    THRESHOLD_DAYS,
    THRESHOLD_MAJOR,
    THRESHOLD_MINOR,
    analyze,
    findings_for,
    rank,
)
from pythonstandards.models import FindingType, Severity
from tests.conftest import TODAY, make_observation


def details(findings) -> set[str]:
    return {finding.detail for finding in findings}


def types(findings) -> set[FindingType]:
    return {finding.finding_type for finding in findings}


# --- a pin that is current ---


def test_a_current_pin_produces_nothing(app_config):
    observation = make_observation(pinned_version="1.2.3", latest_version="1.2.3")
    assert findings_for(observation, app_config, TODAY) == []


def test_a_pin_ahead_of_the_index_produces_nothing(app_config):
    """A local build or a pre-release can sit ahead, which is not a finding."""
    observation = make_observation(pinned_version="2.0.0", latest_version="1.9.0")
    assert findings_for(observation, app_config, TODAY) == []


# --- the major threshold ---


def test_one_major_behind_reports_at_the_configured_limit(app_config):
    observation = make_observation(pinned_version="1.0.0", latest_version="2.0.0")
    findings = findings_for(observation, app_config, TODAY)
    assert types(findings) == {FindingType.MAJOR_BEHIND}
    assert findings[0].severity == Severity.HIGH


def test_the_major_limit_is_read_from_configuration(app_config):
    """Raising the limit must stop the finding, or the threshold is decorative."""
    app_config.freshness[THRESHOLD_MAJOR] = 2
    observation = make_observation(pinned_version="1.0.0", latest_version="2.0.0")
    assert FindingType.MAJOR_BEHIND not in types(findings_for(observation, app_config, TODAY))

    observation = make_observation(pinned_version="1.0.0", latest_version="3.0.0")
    assert FindingType.MAJOR_BEHIND in types(findings_for(observation, app_config, TODAY))


def test_the_major_finding_names_the_gap(app_config):
    observation = make_observation(pinned_version="1.0.0", latest_version="3.0.0")
    finding = findings_for(observation, app_config, TODAY)[0]
    assert "2 major behind" in finding.message
    assert finding.detail == "major_3"


# --- the minor threshold, and why it is an alternative rather than an addition ---


def test_minor_reports_only_at_or_past_the_limit(app_config):
    app_config.freshness[THRESHOLD_MINOR] = 3
    for minor, expected in ((2, False), (3, True), (9, True)):
        observation = make_observation(pinned_version="1.0.0", latest_version=f"1.{minor}.0")
        reported = FindingType.MINOR_BEHIND in types(findings_for(observation, app_config, TODAY))
        assert reported is expected, minor


def test_a_major_gap_suppresses_the_minor_finding(app_config):
    """A pin two majors behind is also many minors behind.

    Reporting both adds a line that tells the reader nothing the first line
    did not already say.
    """
    observation = make_observation(pinned_version="1.0.0", latest_version="3.7.0")
    assert types(findings_for(observation, app_config, TODAY)) == {FindingType.MAJOR_BEHIND}


def test_a_minor_gap_across_majors_is_not_counted(app_config):
    """Comparing minors between different majors measures nothing.

    1.9 to 2.1 is not "eight minors ahead"; the numbering restarted.
    """
    app_config.freshness[THRESHOLD_MAJOR] = 99
    app_config.freshness[THRESHOLD_MINOR] = 3
    observation = make_observation(pinned_version="1.9.0", latest_version="2.1.0")
    assert FindingType.MINOR_BEHIND not in types(findings_for(observation, app_config, TODAY))


# --- staleness of the release itself ---


def test_an_old_release_reports_at_the_configured_age(app_config):
    app_config.freshness[THRESHOLD_DAYS] = 365
    observation = make_observation(
        pinned_version="1.0.0",
        latest_version="1.0.0",
        latest_released_on=TODAY - __import__("datetime").timedelta(days=365),
    )
    assert FindingType.RELEASE_STALE in types(findings_for(observation, app_config, TODAY))


def test_a_release_one_day_short_of_the_limit_is_quiet(app_config):
    app_config.freshness[THRESHOLD_DAYS] = 365
    observation = make_observation(
        pinned_version="1.0.0",
        latest_version="1.0.0",
        latest_released_on=TODAY - __import__("datetime").timedelta(days=364),
    )
    assert findings_for(observation, app_config, TODAY) == []


def test_no_publication_date_reports_no_staleness(app_config):
    """An absent date is unknown, not old."""
    observation = make_observation(
        pinned_version="1.0.0", latest_version="1.0.0", latest_released_on=None
    )
    assert FindingType.RELEASE_STALE not in types(findings_for(observation, app_config, TODAY))


def test_staleness_and_a_version_gap_both_report(app_config):
    """These are independent facts, so both belong in the digest."""
    observation = make_observation(
        pinned_version="1.0.0",
        latest_version="2.0.0",
        latest_released_on=date(2024, 1, 1),
    )
    assert types(findings_for(observation, app_config, TODAY)) == {
        FindingType.MAJOR_BEHIND,
        FindingType.RELEASE_STALE,
    }


# --- what the agent could not read ---


def test_an_unanswered_observation_reports_the_reason(app_config):
    observation = make_observation(error="the index has no package named examplelib")
    findings = findings_for(observation, app_config, TODAY)
    assert types(findings) == {FindingType.PACKAGE_MISSING}
    assert "no package named" in findings[0].message


@pytest.mark.parametrize("pinned,latest", [("not-a-version", "1.0.0"), ("1.0.0", "???")])
def test_a_version_that_does_not_parse_is_a_finding_not_a_crash(app_config, pinned, latest):
    """A pin is whatever somebody typed, and the agent still has to run."""
    observation = make_observation(pinned_version=pinned, latest_version=latest)
    findings = findings_for(observation, app_config, TODAY)
    assert types(findings) == {FindingType.PIN_UNPARSEABLE}
    assert findings[0].severity == Severity.LOW


def test_an_unanswered_observation_reports_nothing_else(app_config):
    """Nothing is known about the version, so no version judgment is possible."""
    observation = make_observation(error="unreachable", latest_version="")
    assert details(findings_for(observation, app_config, TODAY)) == {"unanswered"}


# --- ordering ---


def test_ranking_puts_the_worst_first(app_config):
    observations = [
        make_observation(
            name="stale",
            pinned_version="1.0.0",
            latest_version="1.0.0",
            latest_released_on=date(2020, 1, 1),
        ),
        make_observation(name="major", pinned_version="1.0.0", latest_version="2.0.0"),
    ]
    findings = analyze(observations, app_config, TODAY)
    assert [f.severity for f in findings] == [Severity.HIGH, Severity.LOW]


def test_ranking_is_stable_for_equal_severities(app_config):
    """Equal severity sorts by subject, so the order does not shuffle run to run."""
    observations = [
        make_observation(name="zeta", pinned_version="1.0.0", latest_version="2.0.0"),
        make_observation(name="alpha", pinned_version="1.0.0", latest_version="2.0.0"),
    ]
    findings = analyze(observations, app_config, TODAY)
    assert [f.subject for f in findings] == ["alpha", "zeta"]


def test_ranking_an_empty_list_is_empty():
    assert rank([]) == []


def test_analyze_covers_every_observation(app_config):
    observations = [
        make_observation(name=f"pkg{index}", pinned_version="1.0.0", latest_version="2.0.0")
        for index in range(4)
    ]
    assert len(analyze(observations, app_config, TODAY)) == 4
