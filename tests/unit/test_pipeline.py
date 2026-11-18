"""The pipeline end to end, with the index mocked and no database.

These exercise run_pipeline itself rather than its parts, because the handoffs
between stages are where a refactor breaks something every unit test still
passes on.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from pythonstandards.__main__ import run_pipeline
from pythonstandards.models import FindingType


def write_project(tmp_path: Path, pins: str, **settings) -> list[str]:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(pins, encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "registry:",
                "  delay_between_requests_s: 0",
                "freshness:",
                f"  major_versions_behind: {settings.get('major', 1)}",
                f"  minor_versions_behind: {settings.get('minor', 3)}",
                f"  days_since_release: {settings.get('days', 365)}",
                "reporting:",
                f"  output_dir: {tmp_path / 'reports'}",
                "  site_name: Test agent",
                "retention:",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    return ["--config", str(config), "--requirements", str(requirements)]


def mock_index(monkeypatch, handler):
    def fake_client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(
        "pythonstandards.monitors.registry_monitor.RegistryMonitor._client", fake_client
    )


def args_for(argv: list[str]):
    from pythonstandards.__main__ import parse_args

    return parse_args([*argv, "--dry-run", "--skip-notify"])


def response_for(version: str, released: str | None = None):
    files = [{"upload_time_iso_8601": released}] if released else []
    return {"info": {"version": version}, "releases": {version: files}}


async def test_a_current_pin_produces_no_findings(tmp_path, monkeypatch):
    argv = write_project(tmp_path, "httpx==0.28.1\n")
    mock_index(
        monkeypatch,
        lambda request: httpx.Response(200, json=response_for("0.28.1")),
    )
    result = await run_pipeline(args_for(argv))
    assert result.packages_checked == 1
    assert result.findings_total == 0
    assert result.status == "success"


async def test_a_pin_behind_produces_a_finding_and_a_report(tmp_path, monkeypatch):
    argv = write_project(tmp_path, "httpx==0.27.0\n")
    mock_index(monkeypatch, lambda request: httpx.Response(200, json=response_for("2.0.0")))
    result = await run_pipeline(args_for(argv))
    assert result.findings_new == 1
    written = list((tmp_path / "reports").glob("digest_*.md"))
    assert len(written) == 1
    assert "httpx" in written[0].read_text(encoding="utf-8")


async def test_the_report_is_written_even_when_nothing_is_wrong(tmp_path, monkeypatch):
    """A report that only appears on bad news cannot be used to confirm the
    agent ran at all."""
    argv = write_project(tmp_path, "httpx==0.28.1\n")
    mock_index(monkeypatch, lambda request: httpx.Response(200, json=response_for("0.28.1")))
    await run_pipeline(args_for(argv))
    assert list((tmp_path / "reports").glob("digest_*.md"))


async def test_a_requirement_the_agent_cannot_read_becomes_a_note(tmp_path, monkeypatch):
    argv = write_project(tmp_path, "httpx==0.28.1\nruff>=0.16\n")
    mock_index(monkeypatch, lambda request: httpx.Response(200, json=response_for("0.28.1")))
    result = await run_pipeline(args_for(argv))
    assert result.packages_checked == 1
    assert any("cannot read" in note for note in result.notes)


async def test_one_unreadable_package_does_not_fail_the_run(tmp_path, monkeypatch):
    argv = write_project(tmp_path, "httpx==0.28.1\nghost==1.0\n")

    def handler(request):
        if "ghost" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, json=response_for("0.28.1"))

    mock_index(monkeypatch, handler)
    result = await run_pipeline(args_for(argv))
    assert result.status == "success"
    assert result.findings_total == 1


async def test_every_package_failing_is_a_hard_failure(tmp_path, monkeypatch):
    """Reporting that as clean would say the pins are fine when nothing was
    checked, which is the one thing worse than reporting nothing."""
    from pythonstandards.__main__ import HardFailure

    argv = write_project(tmp_path, "httpx==0.28.1\npydantic==2.14.3\n")
    mock_index(monkeypatch, lambda request: httpx.Response(503))
    with pytest.raises(HardFailure, match="measured nothing"):
        await run_pipeline(args_for(argv))


async def test_the_run_records_how_long_it_took(tmp_path, monkeypatch):
    argv = write_project(tmp_path, "httpx==0.28.1\n")
    mock_index(monkeypatch, lambda request: httpx.Response(200, json=response_for("0.28.1")))
    result = await run_pipeline(args_for(argv))
    assert result.duration_seconds >= 0


async def test_the_configured_thresholds_reach_the_analysis(tmp_path, monkeypatch):
    """A threshold in config.yaml that the pipeline ignores is decorative."""
    argv = write_project(tmp_path, "httpx==1.0.0\n", major=99, minor=99)
    mock_index(monkeypatch, lambda request: httpx.Response(200, json=response_for("5.0.0")))
    result = await run_pipeline(args_for(argv))
    assert result.findings_total == 0


async def test_a_stale_release_is_reported_through_the_pipeline(tmp_path, monkeypatch):
    argv = write_project(tmp_path, "httpx==1.0.0\n", days=1)
    mock_index(
        monkeypatch,
        lambda request: httpx.Response(200, json=response_for("1.0.0", "2020-01-01T00:00:00Z")),
    )
    result = await run_pipeline(args_for(argv))
    assert result.findings_new == 1


async def test_a_dry_run_touches_no_database(tmp_path, monkeypatch):
    """A dry run with no DATABASE_URL must still complete."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    argv = write_project(tmp_path, "httpx==0.27.0\n")
    mock_index(monkeypatch, lambda request: httpx.Response(200, json=response_for("2.0.0")))
    result = await run_pipeline(args_for(argv))
    assert result.findings_new == 1


async def test_an_extras_marker_reaches_the_index_without_its_brackets(tmp_path, monkeypatch):
    requested: list[str] = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, json=response_for("3.2.3"))

    argv = write_project(tmp_path, "psycopg[binary]==3.2.3\n")
    mock_index(monkeypatch, handler)
    await run_pipeline(args_for(argv))
    assert requested == ["https://pypi.org/pypi/psycopg/json"]


async def test_the_digest_names_every_finding_type_it_can_produce(tmp_path, monkeypatch):
    """One run that exercises each branch, so the report template is proven
    against every shape rather than only the common one."""
    pins = "\n".join(["major==1.0.0", "minor==1.0.0", "stale==1.0.0", "ghost==1.0.0"])
    argv = write_project(tmp_path, pins + "\n", days=1)

    def handler(request):
        url = str(request.url)
        if "ghost" in url:
            return httpx.Response(404)
        if "major" in url:
            return httpx.Response(200, json=response_for("3.0.0"))
        if "minor" in url:
            return httpx.Response(200, json=response_for("1.9.0"))
        return httpx.Response(200, json=response_for("1.0.0", "2019-01-01T00:00:00Z"))

    mock_index(monkeypatch, handler)
    result = await run_pipeline(args_for(argv))
    report = next((tmp_path / "reports").glob("digest_*.md")).read_text(encoding="utf-8")
    assert result.findings_new >= 4
    for expected in ("major", "minor", "stale", "ghost"):
        assert expected in report, expected


def test_every_finding_type_has_a_branch_that_emits_it():
    """A type nothing emits is a type nothing tests."""
    source = Path(
        __import__("pythonstandards.analysis.freshness", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8")
    for member in FindingType:
        assert f"FindingType.{member.name}" in source, member.name
