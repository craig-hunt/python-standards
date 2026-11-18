"""The persistence layer, against a stub cursor rather than a database.

The statements themselves are the schema's contract, so these tests assert on
the SQL and the parameters. A test against a live database would also pass
with the wrong column bound to the wrong value.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pythonstandards.config import Secrets
from pythonstandards.db import Database
from pythonstandards.models import RunResult
from tests.conftest import make_finding, make_observation

RUN_DATE = date(2026, 11, 18)


class StubCursor:
    """Records every statement, and answers from a scripted result set."""

    def __init__(self, script):
        self.script = list(script)
        self.statements: list[str] = []
        self.params: list[tuple] = []
        self._rows: list[tuple] = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))
        self.params.append(params)
        self._rows = self.script.pop(0) if self.script else []
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class StubConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def wire(script=None):
    cursor = StubCursor(script or [])
    connection = StubConnection(cursor)
    db = Database(Secrets(database_url="postgresql://localhost/x"))
    db._open = lambda: connection
    return db, cursor, connection


def statements_matching(cursor, fragment):
    return [sql for sql in cursor.statements if fragment in sql]


# --- the connection ---


def test_an_empty_dsn_raises_rather_than_connecting():
    """A blank DATABASE_URL would otherwise reach psycopg as a local socket."""
    db = Database(Secrets(database_url=""))
    with pytest.raises(ValueError, match="DATABASE_URL"):
        db._open()


# --- the schema check ---


def test_a_complete_schema_reports_nothing_missing():
    present = [
        [(column,) for column in columns]
        for _, columns in sorted(Database.REQUIRED_COLUMNS.items())
    ]
    db, _, _ = wire(present)
    assert db.missing_schema() == []


def test_a_missing_column_is_named_with_its_table():
    tables = sorted(Database.REQUIRED_COLUMNS.items())
    script = []
    for index, (_, columns) in enumerate(tables):
        rows = [(column,) for column in columns]
        if index == 0:
            rows = rows[1:]
        script.append(rows)
    db, _, _ = wire(script)
    missing = db.missing_schema()
    assert len(missing) == 1
    assert "." in missing[0]


def test_an_absent_table_is_reported_as_a_whole():
    """A table nobody created is one finding, not one per column."""
    tables = sorted(Database.REQUIRED_COLUMNS.items())
    script = [
        [] if index == 0 else [(column,) for column in columns]
        for index, (_, columns) in enumerate(tables)
    ]
    db, _, _ = wire(script)
    missing = db.missing_schema()
    assert missing == [f"{tables[0][0]} (the whole table)"]


# --- observations ---


def test_an_observation_is_written_with_every_field():
    db, cursor, connection = wire([[]])
    observation = make_observation()
    db.save_observations([observation])
    params = cursor.params[0]
    for expected in (
        observation.name,
        observation.pinned_version,
        observation.latest_version,
        observation.latest_released_on,
        observation.observed_on,
    ):
        assert expected in params, expected
    assert connection.commits == 1


def test_a_second_reading_on_one_day_replaces_the_first():
    """One row per package per day, so a re-run does not double the history."""
    db, cursor, _ = wire([[]])
    db.save_observations([make_observation()])
    sql = cursor.statements[0]
    assert "ON CONFLICT (name, observed_on) DO UPDATE" in sql


def test_no_observations_means_no_statement():
    db, cursor, _ = wire()
    db.save_observations([])
    assert cursor.statements == []


# --- the finding lifecycle ---


def test_a_finding_absent_from_the_open_set_is_new():
    db, _, _ = wire([[], []])
    finding = make_finding()
    new, still_open, resolved = db.reconcile_findings([finding], RUN_DATE)
    assert [f.id for f in new] == [finding.id]
    assert still_open == [] and resolved == []


def test_a_finding_already_open_is_still_open_rather_than_new():
    finding = make_finding()
    db, _, _ = wire([[(finding.id,)], []])
    new, still_open, _ = db.reconcile_findings([finding], RUN_DATE)
    assert new == []
    assert [f.id for f in still_open] == [finding.id]


def test_a_finding_that_went_away_resolves():
    """Absence means fixed here, because every pin is checked on every run."""
    gone = make_finding()
    db, cursor, _ = wire(
        [
            [(gone.id,)],
            [
                (
                    gone.finding_type.value,
                    gone.severity.value,
                    gone.subject,
                    gone.message,
                    gone.detail,
                    gone.recommendation,
                    gone.first_seen,
                )
            ],
        ]
    )
    _, _, resolved = db.reconcile_findings([], RUN_DATE)
    assert [f.detail for f in resolved] == [gone.detail]
    assert statements_matching(cursor, "SET resolved_at")


def test_the_resolve_statement_is_skipped_when_nothing_went_away():
    finding = make_finding()
    db, cursor, _ = wire([[(finding.id,)], []])
    db.reconcile_findings([finding], RUN_DATE)
    assert statements_matching(cursor, "SET resolved_at") == []


def test_a_repeated_finding_in_one_run_is_written_once():
    """The upsert runs once per finding, so a duplicate would increment
    occurrences twice and report one problem twice as new."""
    finding = make_finding()
    db, cursor, _ = wire([[], []])
    new, _, _ = db.reconcile_findings([finding, finding], RUN_DATE)
    assert [f.id for f in new] == [finding.id]
    assert len(statements_matching(cursor, "INSERT INTO findings")) == 1


def test_two_different_findings_are_both_written():
    db, cursor, _ = wire([[], []])
    first, second = make_finding(detail="a"), make_finding(detail="b")
    new, _, _ = db.reconcile_findings([first, second], RUN_DATE)
    assert {f.detail for f in new} == {"a", "b"}
    assert len(statements_matching(cursor, "INSERT INTO findings")) == 2


def test_a_reappearing_finding_clears_its_resolution():
    db, cursor, _ = wire([[], []])
    db.reconcile_findings([make_finding()], RUN_DATE)
    sql = statements_matching(cursor, "INSERT INTO findings")[0]
    assert "resolved_at = NULL" in sql
    assert "occurrences = findings.occurrences + 1" in sql


def test_the_insert_binds_the_findings_own_values():
    db, cursor, _ = wire([[], []])
    finding = make_finding()
    db.reconcile_findings([finding], RUN_DATE)
    params = cursor.params[1]
    for expected in (finding.id, finding.subject, finding.detail, finding.message, RUN_DATE):
        assert expected in params, expected


# --- the run log ---


def test_a_run_is_claimed_before_the_work():
    """count_runs is the agent's only notion of its own history, so writing it
    last would let a late crash erase the run entirely."""
    db, cursor, _ = wire([[(7,)]])
    assert db.claim_run(datetime.now(UTC)) == 7
    assert "INSERT INTO run_log" in cursor.statements[0]
    assert "'started'" in cursor.statements[0]


def test_a_claimed_run_is_updated_rather_than_duplicated():
    db, cursor, _ = wire([[]])
    db.log_run(RunResult(packages_checked=4), run_id=7)
    assert statements_matching(cursor, "UPDATE run_log")
    assert statements_matching(cursor, "INSERT INTO run_log") == []
    assert 7 in cursor.params[0]


def test_a_run_without_a_claim_is_inserted():
    db, cursor, _ = wire([[]])
    db.log_run(RunResult(packages_checked=4))
    assert statements_matching(cursor, "INSERT INTO run_log")


def test_the_run_log_carries_the_notes_as_one_field():
    db, cursor, _ = wire([[]])
    db.log_run(RunResult(notes=["first", "second"]))
    assert "first; second" in cursor.params[0]


def test_counting_runs_reads_the_table():
    db, cursor, _ = wire([[(3,)]])
    assert db.count_runs() == 3
    assert "count(*)" in cursor.statements[0]


def test_counting_runs_on_an_empty_table_is_zero():
    db, _, _ = wire([[]])
    assert db.count_runs() == 0


# --- retention ---


def test_pruning_deletes_past_the_window_and_reports_the_count():
    db, cursor, _ = wire([[(1,), (2,)]])
    assert db.prune(400) == 2
    assert "DELETE FROM observations" in cursor.statements[0]
    assert 400 in cursor.params[0]


def test_pruning_touches_neither_findings_nor_runs():
    """Both are small and carry the history that makes a trend visible."""
    db, cursor, _ = wire([[]])
    db.prune(400)
    assert statements_matching(cursor, "DELETE FROM findings") == []
    assert statements_matching(cursor, "DELETE FROM run_log") == []


# --- the contract itself ---


def test_every_required_table_is_one_the_schema_creates():
    from pathlib import Path

    schema = (Path(__file__).resolve().parents[2] / "sql" / "create_tables.sql").read_text(
        encoding="utf-8"
    )
    for table in Database.REQUIRED_COLUMNS:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema, table
