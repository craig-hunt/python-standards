"""The entry point, and the only place that decides the process exit code.

A scheduler reads the exit code and nothing else. That makes this file the
one place where the difference between "found nothing" and "could not look"
has to be correct, because those produce the same silence everywhere else.

See standard 10 in the README.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from pythonstandards.agents.report_agent import ReportAgent
from pythonstandards.analysis.freshness import analyze
from pythonstandards.config import AppConfig, load_config
from pythonstandards.constants import EXIT_FAILED, EXIT_OK
from pythonstandards.db import Database
from pythonstandards.models import Digest, RunResult, agent_today
from pythonstandards.monitors.registry_monitor import RegistryMonitor

logger = logging.getLogger(__name__)

REQUIREMENTS_FILENAME = "requirements.txt"
# A requirement line this agent understands. Anything else, an editable
# install or a URL, is skipped with a note rather than guessed at.
PIN_SEPARATOR = "=="


class HardFailure(Exception):
    """A condition that makes the run's output untrustworthy.

    Raised rather than returned. A bare return exits zero, and a scheduler
    reads zero as success, so a failure that returns is a failure nobody
    hears about. This is the distinction standard 10 exists for.
    """


def read_pins(path: Path) -> tuple[dict[str, str], list[str]]:
    """Exact pins from a requirements file, plus a note per line skipped.

    Raises when the file is absent or holds no pin at all: the agent's entire
    subject is those pins, so a run without them has nothing to say and must
    not report success.
    """
    if not path.exists():
        raise HardFailure(
            f"{path} does not exist, so there are no pins to check. "
            "The agent reads the project's own requirements file."
        )

    pins: dict[str, str] = {}
    notes: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if PIN_SEPARATOR not in line:
            notes.append(f"skipped a requirement this agent cannot read: {line}")
            continue
        name, _, version = line.partition(PIN_SEPARATOR)
        # An extras marker belongs to the install, not to the package's name
        # in the index, and asking the index about "psycopg[binary]" returns
        # nothing at all.
        pins[name.split("[", 1)[0].strip()] = version.strip()

    if not pins:
        raise HardFailure(
            f"{path} carries no exact pins, so there is nothing to check. "
            "A range cannot be compared against the current release."
        )
    return pins, notes


async def run_pipeline(args: argparse.Namespace) -> RunResult:
    """Fetch, analyze, persist, report. Each stage hands off and stops there."""
    started = time.monotonic()
    config, secrets = load_config(args.config)
    result = RunResult(run_date=datetime.now(UTC))

    persist: Database | None = None
    run_id: int | None = None
    if not args.dry_run:
        persist = Database(secrets)
        # Before any work. A schema mismatch found here costs nothing; the
        # same mismatch found at the final write has already spent the run.
        missing = persist.missing_schema()
        if missing:
            result.status = "failed"
            raise HardFailure(
                "the database schema is older than this code. Missing: "
                + ", ".join(missing)
                + ". Run sql/create_tables.sql, which adds only what is absent."
            )
        run_id = persist.claim_run(result.run_date)

    pins, pin_notes = read_pins(Path(args.requirements))
    result.notes.extend(pin_notes)

    monitor = RegistryMonitor(config)
    observations = await monitor.run(pins)
    result.packages_checked = len(observations)
    result.notes.extend(monitor.notes)

    # Every observation failing means the index was unreachable throughout,
    # not that every package is missing. Reporting that as a clean run would
    # tell a reader their pins are fine when nothing was checked.
    if observations and not any(observation.ok for observation in observations):
        result.status = "failed"
        raise HardFailure(
            f"not one of {len(observations)} package(s) could be read from the index, "
            "so this run measured nothing. Treating it as a result would report "
            "healthy pins that nobody checked."
        )

    findings = analyze(observations, config, agent_today())

    digest = Digest(report_date=agent_today(), observations=observations)
    if persist:
        persist.save_observations(observations)
        new, still_open, resolved = persist.reconcile_findings(findings, digest.report_date)
        if config.retention_enabled:
            persist.prune(config.observation_days)
    else:
        new, still_open, resolved = findings, [], []

    digest.new_findings = new
    digest.open_findings = still_open
    digest.resolved_findings = resolved
    digest.notes = list(result.notes)

    result.findings_total = len(new) + len(still_open)
    result.findings_new = len(new)
    result.findings_resolved = len(resolved)
    result.duration_seconds = int(time.monotonic() - started)

    agent = ReportAgent(config, secrets)
    report = agent.build(digest, result)
    agent.write(report, digest)
    if not args.skip_notify and agent.should_send(digest):
        agent.send(report, digest, _recipients(config))
    elif not args.skip_notify:
        logger.info("nothing changed since the last run, so no digest was sent")

    if persist:
        persist.log_run(result, run_id)
    logger.info(
        "done in %ds: %d package(s), %d finding(s) open (%d new, %d resolved)",
        result.duration_seconds,
        result.packages_checked,
        result.findings_total,
        result.findings_new,
        result.findings_resolved,
    )
    return result


def _recipients(config: AppConfig) -> list[str]:
    raw = config.reporting.get("recipients") or []
    return [str(address) for address in raw]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pythonstandards")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--requirements", default=REQUIREMENTS_FILENAME)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read the index and write a report, touching no database",
    )
    parser.add_argument("--skip-notify", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """The process's only exit point.

    Every failure path returns EXIT_FAILED, and every one logs why first. An
    unhandled exception would also exit non-zero, but with a traceback as its
    only explanation, which is a worse thing to find in a scheduler's log at
    seven in the morning.
    """
    import asyncio

    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        asyncio.run(run_pipeline(args))
    except HardFailure as exc:
        logger.error("run failed: %s", exc)
        return EXIT_FAILED
    except Exception:
        logger.exception("run failed with an unexpected error")
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
