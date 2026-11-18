"""The exit-code policy, which is the one thing a scheduler reads."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from pythonstandards.__main__ import HardFailure, main, parse_args, read_pins
from pythonstandards.constants import EXIT_FAILED, EXIT_OK

# --- reading the pins ---


def test_exact_pins_are_read(requirements_file):
    pins, _ = read_pins(requirements_file)
    assert pins["httpx"] == "0.28.1"


def test_an_extras_marker_is_stripped_from_the_package_name(requirements_file):
    """Asking an index about psycopg[binary] returns nothing at all."""
    pins, _ = read_pins(requirements_file)
    assert pins["psycopg"] == "3.2.3"
    assert not any("[" in name for name in pins)


def test_a_trailing_comment_is_not_part_of_the_version(requirements_file):
    pins, _ = read_pins(requirements_file)
    assert pins["psycopg"] == "3.2.3"


@pytest.mark.parametrize("unreadable", ["ruff", "-e ./local"])
def test_a_requirement_this_agent_cannot_compare_is_noted(requirements_file, unreadable):
    """A range cannot be compared against a current release, and silence about
    it would let a reader believe every pin was checked."""
    pins, notes = read_pins(requirements_file)
    assert unreadable.split("[")[0].strip("-e ") not in pins or unreadable == "ruff"
    assert any("cannot read" in note for note in notes)


def test_a_missing_file_raises_rather_than_returning(tmp_path):
    """A bare return exits zero, which a scheduler reads as success."""
    with pytest.raises(HardFailure) as raised:
        read_pins(tmp_path / "absent.txt")
    assert "does not exist" in str(raised.value)


def test_a_file_with_no_exact_pin_raises(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("ruff>=0.16\n# nothing pinned\n", encoding="utf-8")
    with pytest.raises(HardFailure) as raised:
        read_pins(path)
    assert "no exact pins" in str(raised.value)


def test_an_empty_file_raises(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("", encoding="utf-8")
    with pytest.raises(HardFailure):
        read_pins(path)


# --- the arguments ---


def test_the_defaults_name_the_project_files():
    args = parse_args([])
    assert args.config == "config.yaml"
    assert args.requirements == "requirements.txt"
    assert args.dry_run is False
    assert args.skip_notify is False


def test_every_flag_is_accepted():
    args = parse_args(["--dry-run", "--skip-notify", "--verbose"])
    assert args.dry_run and args.skip_notify and args.verbose


# --- the exit code ---


def _write_project(tmp_path: Path, pins: str) -> tuple[Path, Path]:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(pins, encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "registry:",
                "  delay_between_requests_s: 0",
                "freshness: {}",
                f"reporting:\n  output_dir: {tmp_path / 'reports'}",
                "retention:\n  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    return config, requirements


def test_a_clean_run_exits_zero(tmp_path, monkeypatch):
    config, requirements = _write_project(tmp_path, "httpx==0.28.1\n")
    body = {"info": {"version": "0.28.1"}, "releases": {"0.28.1": []}}

    def fake_client(self):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
            follow_redirects=False,
        )

    monkeypatch.setattr(
        "pythonstandards.monitors.registry_monitor.RegistryMonitor._client", fake_client
    )
    code = main(
        ["--config", str(config), "--requirements", str(requirements), "--dry-run", "--skip-notify"]
    )
    assert code == EXIT_OK


def test_a_run_that_measured_nothing_exits_non_zero(tmp_path, monkeypatch):
    """Every package failing means the index was unreachable, not that every
    package is missing. Reporting that as clean would say the pins are fine
    when nothing was checked."""
    config, requirements = _write_project(tmp_path, "httpx==0.28.1\npydantic==2.14.3\n")

    def fake_client(self):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
            follow_redirects=False,
        )

    monkeypatch.setattr(
        "pythonstandards.monitors.registry_monitor.RegistryMonitor._client", fake_client
    )
    code = main(
        ["--config", str(config), "--requirements", str(requirements), "--dry-run", "--skip-notify"]
    )
    assert code == EXIT_FAILED


def test_a_missing_requirements_file_exits_non_zero(tmp_path):
    config, _ = _write_project(tmp_path, "httpx==0.28.1\n")
    code = main(
        [
            "--config",
            str(config),
            "--requirements",
            str(tmp_path / "absent.txt"),
            "--dry-run",
            "--skip-notify",
        ]
    )
    assert code == EXIT_FAILED


def test_an_unexpected_error_exits_non_zero_rather_than_tracing_out(tmp_path, monkeypatch):
    """An unhandled exception also exits non-zero, but leaves a traceback as
    its only explanation, which is a worse thing to find at seven in the
    morning."""
    config, requirements = _write_project(tmp_path, "httpx==0.28.1\n")

    def explode(self, pins):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr("pythonstandards.monitors.registry_monitor.RegistryMonitor.run", explode)
    code = main(
        ["--config", str(config), "--requirements", str(requirements), "--dry-run", "--skip-notify"]
    )
    assert code == EXIT_FAILED


def test_the_exit_codes_are_the_documented_ones():
    assert EXIT_OK == 0
    assert EXIT_FAILED == 1
