"""Regenerate the ALTER block and the completeness gate in create_tables.sql.

CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so a
database created by an earlier release needs an ALTER for every column added
since. The agent hard-fails when missing_schema() reports a gap and tells the
reader to run create_tables.sql, which means that file has to be able to close
any gap it names. Hand-maintaining the ALTER list already failed on a sibling
project: it carried five lines for two tables while the code enforced
seventy-five columns across seven, so the prescribed repair ran cleanly and
changed nothing.

Generating both sections from the CREATE TABLE statements and from
Database.REQUIRED_COLUMNS means neither can drift from the schema it describes.
A test fails if this output is stale.

Run: python scripts/generate_schema_alters.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pythonstandards.db import Database

SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "create_tables.sql"
ALTER_BEGIN = "-- BEGIN GENERATED ALTERS"
ALTER_END = "-- END GENERATED ALTERS"
GATE_BEGIN = "-- BEGIN GENERATED GATE"
GATE_END = "-- END GENERATED GATE"


def table_definitions(sql: str) -> dict[str, dict[str, str]]:
    definitions: dict[str, dict[str, str]] = {}
    for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", sql, re.S):
        columns: dict[str, str] = {}
        for raw in body.split("\n"):
            line = raw.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            if re.match(r"^(PRIMARY KEY|UNIQUE|CONSTRAINT|FOREIGN KEY|CHECK)\b", line, re.I):
                continue
            match = re.match(r"^(\w+)\s+(.*)$", line)
            if match:
                columns[match.group(1)] = match.group(2).strip().rstrip(",")
        definitions[table] = columns
    return definitions


def render_alters(definitions: dict[str, dict[str, str]]) -> str:
    lines: list[str] = []
    for table, columns in definitions.items():
        lines.append(f"-- {table}")
        for column, declaration in columns.items():
            if re.search(r"\bPRIMARY KEY\b|\bGENERATED\b|\bserial\b", declaration, re.I):
                lines.append(f"--   {column}: identity column, created with the table")
                continue
            if re.search(r"\bNOT NULL\b", declaration, re.I) and not re.search(
                r"\bDEFAULT\b", declaration, re.I
            ):
                # Postgres refuses to add one of these to a populated table.
                lines.append(
                    f"--   {column}: NOT NULL without a default; the gate below reports it by name"
                )
                continue
            lines.append(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {declaration};")
        lines.append("")
    return "\n".join(lines).rstrip()


# The guidance a failed gate prints. Assembled from pieces here because
# Postgres wants one string, while a 275-character source line is a lint
# finding, and the message is the only thing a reader gets when the gate fires.
GATE_GUIDANCE = " ".join(
    [
        "Those columns are NOT NULL without a default, so Postgres will not",
        "add them to a populated table. Recreate the affected table, or add",
        "each column with an explicit default and then drop the default.",
    ]
)


def render_gate() -> str:
    """The completeness check that closes the schema script.

    S608 reads the f-string below as a query built by interpolation. It builds
    DDL into a file that a person runs later, and the only interpolated values
    are the table and column names in Database.REQUIRED_COLUMNS, which are
    this code's own constants. Nothing here connects to anything or executes.
    """
    rows = ",\n".join(
        f"        ('{table}', '{column}')"
        for table, columns in sorted(Database.REQUIRED_COLUMNS.items())
        for column in sorted(columns)
    )
    return f"""DO $$
DECLARE
    gap text;
BEGIN
    SELECT string_agg(r.t || '.' || r.c, ', ' ORDER BY r.t, r.c)
      INTO gap
      FROM (VALUES
{rows}
      ) AS r(t, c)
     WHERE NOT EXISTS (
         SELECT 1 FROM information_schema.columns ic
          WHERE ic.table_schema = current_schema()
            AND ic.table_name = r.t
            AND ic.column_name = r.c
     );

    IF gap IS NOT NULL THEN
        RAISE EXCEPTION 'Schema still incomplete after this script: % {GATE_GUIDANCE}', gap;
    END IF;

    RAISE NOTICE 'Schema complete: every column the agent requires is present.';
END $$;"""  # noqa: S608


def replace_between(text: str, begin: str, end: str, body: str) -> str:
    start = text.index(begin) + len(begin)
    finish = text.index(end)
    return text[:start] + "\n" + body + "\n" + text[finish:]


def build(sql: str) -> str:
    sql = replace_between(sql, ALTER_BEGIN, ALTER_END, render_alters(table_definitions(sql)))
    return replace_between(sql, GATE_BEGIN, GATE_END, render_gate())


def main() -> int:
    sql = SCHEMA.read_text(encoding="utf-8")
    rebuilt = build(sql)
    if rebuilt == sql:
        print("create_tables.sql already up to date")
        return 0
    SCHEMA.write_text(rebuilt, encoding="utf-8", newline="")
    print(f"regenerated {SCHEMA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
