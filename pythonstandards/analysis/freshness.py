"""Turns observations into findings.

Pure: it receives measurements and returns judgments, makes no request, reads
no clock beyond the one passed to it, and touches no database. That is what
makes every threshold testable without a network or a container.

See standards 1, 4 and 9 in the README.
"""

from __future__ import annotations

from datetime import date

from packaging.version import InvalidVersion, Version

from pythonstandards.config import AppConfig
from pythonstandards.constants import (
    DEFAULT_DAYS_SINCE_RELEASE,
    DEFAULT_MAJOR_VERSIONS_BEHIND,
    DEFAULT_MINOR_VERSIONS_BEHIND,
)
from pythonstandards.models import Finding, FindingType, Observation, Severity, agent_today

# The threshold names, as constants, because the analysis reads them and a
# test asserts on them. Repeating the string in both is how the two drift.
THRESHOLD_MAJOR = "major_versions_behind"
THRESHOLD_MINOR = "minor_versions_behind"
THRESHOLD_DAYS = "days_since_release"


def _parse(raw: str) -> Version | None:
    """A version, or None when the string is not one.

    A pin can be anything a person typed. Returning None makes an unparseable
    pin a finding about the pin, which is useful, rather than an exception
    partway through a run, which is not.
    """
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def findings_for(
    observation: Observation, config: AppConfig, today: date | None = None
) -> list[Finding]:
    """Every judgment this one observation supports.

    `today` is a parameter rather than a call to the clock, so the staleness
    rule is testable at an exact age instead of only on the day a test runs.
    """
    today = today or agent_today()

    if not observation.ok:
        return [
            Finding(
                finding_type=FindingType.PACKAGE_MISSING,
                severity=Severity.MEDIUM,
                subject=observation.name,
                message=f"{observation.name} could not be checked: {observation.error}",
                detail="unanswered",
                recommendation=(
                    "Confirm the package name and that the index is reachable. A pin "
                    "nobody can check is a pin nobody can audit."
                ),
            )
        ]

    pinned = _parse(observation.pinned_version)
    latest = _parse(observation.latest_version)
    if pinned is None or latest is None:
        unreadable = observation.pinned_version if pinned is None else observation.latest_version
        return [
            Finding(
                finding_type=FindingType.PIN_UNPARSEABLE,
                severity=Severity.LOW,
                subject=observation.name,
                message=f"{observation.name} carries a version that does not parse: {unreadable}",
                detail="unparseable",
                recommendation=(
                    "Use a version the packaging specification accepts, so tooling can "
                    "compare it rather than guessing."
                ),
            )
        ]

    findings: list[Finding] = []

    major_gap = latest.major - pinned.major
    major_limit = config.threshold(THRESHOLD_MAJOR, DEFAULT_MAJOR_VERSIONS_BEHIND)
    if major_gap >= major_limit:
        findings.append(
            Finding(
                finding_type=FindingType.MAJOR_BEHIND,
                severity=Severity.HIGH,
                subject=observation.name,
                message=(
                    f"{observation.name} is pinned at {pinned} while {latest} is current, "
                    f"{major_gap} major behind"
                ),
                detail=f"major_{latest.major}",
                recommendation=(
                    "Read the release notes before bumping. A major release is where the "
                    "breaking changes live, which is also why leaving it is the expensive "
                    "option."
                ),
            )
        )
    else:
        # Minor and major are reported as alternatives rather than together. A
        # pin two majors behind is also many minors behind, and saying both
        # adds a line that tells the reader nothing new.
        minor_gap = latest.minor - pinned.minor if latest.major == pinned.major else 0
        minor_limit = config.threshold(THRESHOLD_MINOR, DEFAULT_MINOR_VERSIONS_BEHIND)
        if minor_gap >= minor_limit:
            findings.append(
                Finding(
                    finding_type=FindingType.MINOR_BEHIND,
                    severity=Severity.MEDIUM,
                    subject=observation.name,
                    message=(
                        f"{observation.name} is pinned at {pinned} while {latest} is current, "
                        f"{minor_gap} minor behind"
                    ),
                    detail=f"minor_{latest.major}.{latest.minor}",
                    recommendation=(
                        "Minor releases carry fixes this project is not receiving. Bump and "
                        "run the suite."
                    ),
                )
            )

    if observation.latest_released_on is not None:
        age_days = (today - observation.latest_released_on).days
        age_limit = config.threshold(THRESHOLD_DAYS, DEFAULT_DAYS_SINCE_RELEASE)
        if age_days >= age_limit:
            findings.append(
                Finding(
                    finding_type=FindingType.RELEASE_STALE,
                    severity=Severity.LOW,
                    subject=observation.name,
                    message=(f"the current release of {observation.name} is {age_days} days old"),
                    detail="release_stale",
                    recommendation=(
                        "Nothing is wrong with the pin. The project itself has gone quiet, "
                        "which is worth knowing before depending on it further."
                    ),
                )
            )

    return findings


def analyze(
    observations: list[Observation], config: AppConfig, today: date | None = None
) -> list[Finding]:
    """Every finding across every observation, ordered worst first."""
    findings: list[Finding] = []
    for observation in observations:
        findings.extend(findings_for(observation, config, today))
    return rank(findings)


def rank(findings: list[Finding]) -> list[Finding]:
    """Severity first, then subject, so the top of a list is worth doing first.

    Sorting by the enum's declaration order rather than alphabetically: "high"
    sorts after "low" as a string, which would put the least urgent finding at
    the top of every report.
    """
    from pythonstandards.models import SEVERITY_ORDER

    order = {severity: index for index, severity in enumerate(SEVERITY_ORDER)}
    return sorted(findings, key=lambda f: (order[f.severity], f.subject, f.detail))
