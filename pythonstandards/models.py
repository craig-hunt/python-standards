"""The domain's types, and nothing that fetches, decides or persists.

Pydantic rather than dataclasses or dicts, because a value arriving from a
registry response or a database row gets validated at the boundary instead of
failing three layers later with a message about a NoneType. Every docstring
carries the reasoning rather than restating the field names, which the fields
already state.

See standards 4 and 5 in the README.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def agent_today() -> date:
    """Today in UTC, matching the database's CURRENT_DATE.

    date.today() reads the machine's local zone. A scheduled container runs
    UTC while a workstation does not, and the database compares against
    CURRENT_DATE, so mixing the two gives one run two notions of today:
    retention windows, weekday decisions and finding dates can then disagree
    by a day, and a test suite comparing against local time passes only while
    the two happen to match.
    """
    return datetime.now(UTC).date()


class Severity(StrEnum):
    """Ordered worst first, so a sort on the value ranks correctly.

    StrEnum rather than an int enum, because the value lands in a database
    column and in a report, where "high" reads and 2 does not. StrEnum rather
    than the older str-and-Enum pair, which Python 3.11 superseded and ruff's
    UP042 reports.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_ORDER: tuple[Severity, ...] = (Severity.HIGH, Severity.MEDIUM, Severity.LOW)


class FindingType(StrEnum):
    """What kind of problem a finding describes.

    The value is a stable key: it reaches the database, the finding id and the
    report. Renaming one orphans every stored row that carries it, so the
    value stays even when the wording in a message changes.
    """

    MAJOR_BEHIND = "pin_major_versions_behind"
    MINOR_BEHIND = "pin_minor_versions_behind"
    RELEASE_STALE = "pin_release_stale"
    PACKAGE_MISSING = "pin_package_not_in_index"
    PIN_UNPARSEABLE = "pin_version_unparseable"


class Observation(BaseModel):
    """What the registry said about one package on one day.

    Kept separately from a finding because the two answer different questions.
    An observation is a measurement and never changes; a finding is a judgment
    about measurements and has a lifecycle. Storing only findings would make
    "when did this pin fall behind" unanswerable.
    """

    name: str
    pinned_version: str
    latest_version: str = ""
    latest_released_on: date | None = None
    observed_on: date = Field(default_factory=agent_today)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.latest_version)


class Finding(BaseModel):
    """One actionable judgment about one pin.

    `id` hashes the stable identity rather than the payload, so the same
    problem reported on consecutive runs reconciles to one row and keeps its
    first_seen date. A finding whose message is reworded while its type,
    subject and detail hold steady stays the same finding, which is what lets
    the report say how long something has been true.
    """

    finding_type: FindingType
    severity: Severity
    subject: str
    message: str
    detail: str
    recommendation: str
    first_seen: date = Field(default_factory=agent_today)

    @property
    def id(self) -> str:
        """A short digest of type, subject and detail.

        Truncated because the full digest is noise in a report and in a log
        line, and sixteen hex characters carry enough room that a collision
        between two of this agent's findings is not a practical concern.
        """
        from pythonstandards.constants import FINDING_ID_LENGTH

        material = f"{self.finding_type.value}|{self.subject}|{self.detail}"
        return hashlib.sha256(material.encode()).hexdigest()[:FINDING_ID_LENGTH]


class RunResult(BaseModel):
    """What one run did, written whether it succeeded or not.

    A run that fails still records the attempt. A scheduler reading only
    successful runs cannot tell a failing agent from one that never started,
    and those need different responses.
    """

    run_date: datetime = Field(default_factory=lambda: datetime.now(UTC))
    packages_checked: int = 0
    findings_total: int = 0
    findings_new: int = 0
    findings_resolved: int = 0
    duration_seconds: int = 0
    status: str = "success"
    error_log: str = ""
    notes: list[str] = Field(default_factory=list)


class Digest(BaseModel):
    """Everything one report renders, assembled before any rendering starts.

    The report agent receives this rather than reaching back into the database
    or the monitors. A template that can query is a template that can fail
    halfway through writing a file.
    """

    report_date: date = Field(default_factory=agent_today)
    new_findings: list[Finding] = Field(default_factory=list)
    open_findings: list[Finding] = Field(default_factory=list)
    resolved_findings: list[Finding] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
