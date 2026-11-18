"""Persistence, and no fetching, judging or reporting.

Every statement binds its parameters. Every schema requirement the code relies
on is declared here and checked before a run does any work, because a schema
mismatch discovered at the final write has already spent the run's time and
the index's goodwill.

See standards 4, 8 and 9 in the README.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import ClassVar

import psycopg

from pythonstandards.config import Secrets
from pythonstandards.models import Finding, FindingType, Observation, RunResult, Severity

logger = logging.getLogger(__name__)


class Database:
    """A thin layer over psycopg, with one method per question asked of it.

    Not an ORM: the statements are the schema's contract and reading them is
    how somebody confirms what this agent writes. A generated query hides that
    behind a layer nobody inspects during an incident.
    """

    # Every column the code reads or writes. Checked before the first stage,
    # so an older database fails with the list of what is missing rather than
    # with a column error after the work is done.
    # ClassVar, because RUF012 is right: a bare dict annotation on a class
    # attribute reads as an instance field with a shared mutable default, and
    # the next person to add __init__ handling would get one dict shared
    # across every instance. This is a constant about the schema.
    REQUIRED_COLUMNS: ClassVar[dict[str, tuple[str, ...]]] = {
        "findings": (
            "finding_id",
            "finding_type",
            "severity",
            "subject",
            "message",
            "detail",
            "recommendation",
            "first_seen",
            "last_seen",
            "resolved_at",
            "occurrences",
        ),
        "observations": (
            "name",
            "pinned_version",
            "latest_version",
            "latest_released_on",
            "observed_on",
            "error",
        ),
        "run_log": (
            "run_date",
            "packages_checked",
            "findings_total",
            "findings_new",
            "findings_resolved",
            "duration_seconds",
            "status",
            "error_log",
            "notes",
        ),
    }

    def __init__(self, secrets: Secrets) -> None:
        self._dsn = secrets.database_url

    def _open(self) -> psycopg.Connection:
        if not self._dsn:
            raise ValueError("DATABASE_URL is not set. See the README on where secrets live.")
        return psycopg.connect(self._dsn)

    def missing_schema(self) -> list[str]:
        """Required columns the database does not have, as table.column.

        Run before any stage. A mismatch found here costs nothing; the same
        mismatch found at the final write costs the whole run, and a reader
        then has to work out which of several stages failed.
        """
        missing: list[str] = []
        with self._open() as conn:
            cursor = conn.cursor()
            for table, columns in sorted(self.REQUIRED_COLUMNS.items()):
                cursor.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                     WHERE table_schema = current_schema() AND table_name = %s
                    """,
                    (table,),
                )
                present = {row[0] for row in cursor.fetchall()}
                if not present:
                    missing.append(f"{table} (the whole table)")
                    continue
                missing.extend(f"{table}.{column}" for column in columns if column not in present)
        return missing

    def save_observations(self, observations: list[Observation]) -> None:
        """One row per package per day, replacing the day's earlier reading."""
        if not observations:
            return
        with self._open() as conn:
            cursor = conn.cursor()
            for observation in observations:
                cursor.execute(
                    """
                    INSERT INTO observations (
                        name, pinned_version, latest_version, latest_released_on,
                        observed_on, error
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name, observed_on) DO UPDATE SET
                        pinned_version = EXCLUDED.pinned_version,
                        latest_version = EXCLUDED.latest_version,
                        latest_released_on = EXCLUDED.latest_released_on,
                        error = EXCLUDED.error
                    """,
                    (
                        observation.name,
                        observation.pinned_version,
                        observation.latest_version,
                        observation.latest_released_on,
                        observation.observed_on,
                        observation.error,
                    ),
                )
            conn.commit()

    def reconcile_findings(
        self, findings: list[Finding], run_date: date
    ) -> tuple[list[Finding], list[Finding], list[Finding]]:
        """Persist this run's findings and classify them against what came before.

        Returns new, still-open and newly-resolved findings. A finding absent
        from this run closes, which is how a bump shows up in the digest
        without anybody recording it by hand.

        Every pin is checked on every run here, so absence genuinely means
        fixed. An agent that samples instead would need to bound this by what
        it actually examined, or it would report a defect as repaired merely
        because nothing looked at it.
        """
        # Deduplicate first. The upsert runs once per finding, so a repeated id
        # would increment occurrences twice and report one problem twice as new.
        seen: set[str] = set()
        deduped: list[Finding] = []
        for finding in findings:
            if finding.id in seen:
                continue
            seen.add(finding.id)
            deduped.append(finding)

        current_ids = [finding.id for finding in deduped]

        with self._open() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT finding_id FROM findings WHERE resolved_at IS NULL")
            previously_open = {row[0] for row in cursor.fetchall()}

            for finding in deduped:
                cursor.execute(
                    """
                    INSERT INTO findings (
                        finding_id, finding_type, severity, subject, message, detail,
                        recommendation, first_seen, last_seen, occurrences
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    ON CONFLICT (finding_id) DO UPDATE SET
                        severity = EXCLUDED.severity,
                        message = EXCLUDED.message,
                        recommendation = EXCLUDED.recommendation,
                        last_seen = EXCLUDED.last_seen,
                        resolved_at = NULL,
                        occurrences = findings.occurrences + 1
                    """,
                    (
                        finding.id,
                        finding.finding_type.value,
                        finding.severity.value,
                        finding.subject,
                        finding.message,
                        finding.detail,
                        finding.recommendation,
                        run_date,
                        run_date,
                    ),
                )

            resolved_rows: list[tuple] = []
            stale = previously_open - set(current_ids)
            if stale:
                cursor.execute(
                    """
                    UPDATE findings SET resolved_at = %s
                     WHERE finding_id = ANY(%s) AND resolved_at IS NULL
                    RETURNING finding_type, severity, subject, message, detail,
                              recommendation, first_seen
                    """,
                    (run_date, list(stale)),
                )
                resolved_rows = cursor.fetchall()
            conn.commit()

        new = [f for f in deduped if f.id not in previously_open]
        still_open = [f for f in deduped if f.id in previously_open]
        resolved = [
            Finding(
                finding_type=FindingType(row[0]),
                severity=Severity(row[1]),
                subject=row[2],
                message=row[3],
                detail=row[4],
                recommendation=row[5],
                first_seen=row[6],
            )
            for row in resolved_rows
        ]
        return new, still_open, resolved

    def count_runs(self) -> int:
        with self._open() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM run_log")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def claim_run(self, run_date: datetime) -> int | None:
        """Record that a run started, before it does anything else.

        log_run writes the outcome at the end and updates this row. Writing
        only at the end means a crash in between erases the run's existence,
        and count_runs is the agent's only notion of its own history.
        """
        with self._open() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO run_log (run_date, status) VALUES (%s, 'started') RETURNING id",
                (run_date,),
            )
            row = cursor.fetchone()
            conn.commit()
            return int(row[0]) if row else None

    def log_run(self, result: RunResult, run_id: int | None = None) -> None:
        """Write the finished result, updating the claimed row when there is one."""
        with self._open() as conn:
            cursor = conn.cursor()
            if run_id is not None:
                cursor.execute(
                    """
                    UPDATE run_log SET
                        run_date = %s, packages_checked = %s, findings_total = %s,
                        findings_new = %s, findings_resolved = %s,
                        duration_seconds = %s, status = %s, error_log = %s, notes = %s
                     WHERE id = %s
                    """,
                    (
                        result.run_date,
                        result.packages_checked,
                        result.findings_total,
                        result.findings_new,
                        result.findings_resolved,
                        result.duration_seconds,
                        result.status,
                        result.error_log,
                        "; ".join(result.notes),
                        run_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO run_log (
                        run_date, packages_checked, findings_total, findings_new,
                        findings_resolved, duration_seconds, status, error_log, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        result.run_date,
                        result.packages_checked,
                        result.findings_total,
                        result.findings_new,
                        result.findings_resolved,
                        result.duration_seconds,
                        result.status,
                        result.error_log,
                        "; ".join(result.notes),
                    ),
                )
            conn.commit()

    def prune(self, observation_days: int) -> int:
        """Drop observations past the retention window, returning the row count.

        Findings and runs are never pruned: both are small, and they carry the
        history that makes a trend visible. Observations are the volume, and a
        year of them answers every question asked of them.
        """
        with self._open() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM observations WHERE observed_on < CURRENT_DATE - %s",
                (observation_days,),
            )
            pruned = cursor.rowcount
            conn.commit()
        logger.info("pruned %d observation row(s)", pruned)
        return pruned
