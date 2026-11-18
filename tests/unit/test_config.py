"""Configuration and secrets, including what must never be printed."""

from __future__ import annotations

from pathlib import Path

import pytest

from pythonstandards.config import AppConfig, Secrets, load_config, load_env_file
from pythonstandards.constants import (
    DEFAULT_MAJOR_VERSIONS_BEHIND,
    DEFAULT_REGISTRY_BASE_URL,
    ENV_DATABASE_URL,
    ENV_SMTP_PORT,
)


def test_a_threshold_falls_back_to_its_documented_default():
    """A missing key must produce the documented behavior rather than a
    KeyError three stages into a run."""
    assert AppConfig().threshold("absent", DEFAULT_MAJOR_VERSIONS_BEHIND) == (
        DEFAULT_MAJOR_VERSIONS_BEHIND
    )


def test_a_configured_threshold_wins():
    config = AppConfig(freshness={"major_versions_behind": 4})
    assert config.threshold("major_versions_behind", DEFAULT_MAJOR_VERSIONS_BEHIND) == 4


def test_the_project_config_parses_and_carries_the_index():
    config, _ = load_config(Path("config.yaml"))
    assert config.registry.base_url == DEFAULT_REGISTRY_BASE_URL
    assert config.observation_days > 0
    assert config.retention_enabled is True


def test_an_empty_config_file_yields_the_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    config, _ = load_config(path)
    assert config.registry.base_url == DEFAULT_REGISTRY_BASE_URL


def test_a_config_with_null_sections_yields_the_defaults(tmp_path):
    """A key present with no value is how a half-edited YAML file looks."""
    path = tmp_path / "config.yaml"
    path.write_text("registry:\nfreshness:\nreporting:\nretention:\n", encoding="utf-8")
    config, _ = load_config(path)
    assert config.registry.base_url == DEFAULT_REGISTRY_BASE_URL
    assert config.freshness == {}


def test_the_secrets_repr_prints_nothing():
    """One logger.debug of this object, or one traceback rendering locals,
    would otherwise write both passwords into a log."""
    secrets = Secrets(
        database_url="postgresql://user:not-a-real-password@host/db",
        smtp_password="not-a-real-password-either",
        smtp_user="agent@example.test",
    )
    printed = repr(secrets)
    for value in ("not-a-real-password", "agent@example.test"):
        assert value not in printed, printed


def test_an_empty_port_falls_back_rather_than_crashing(monkeypatch):
    """A platform secret set to empty is not the same as unset, and int("")
    raises before the first stage with a message naming nothing."""
    monkeypatch.setenv(ENV_SMTP_PORT, "")
    assert Secrets.from_env().smtp_port == 587
    monkeypatch.setenv(ENV_SMTP_PORT, "2525")
    assert Secrets.from_env().smtp_port == 2525


def test_secrets_come_from_the_environment(monkeypatch):
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://localhost/x")
    assert Secrets.from_env().database_url == "postgresql://localhost/x"


def test_an_absent_env_file_is_the_normal_case(tmp_path):
    """A container receives its secrets from the platform and has no file."""
    load_env_file(tmp_path / "nothing.env")


def test_an_env_file_does_not_overwrite_the_environment(tmp_path, monkeypatch):
    """A value the platform set is the one that should be used."""
    monkeypatch.setenv("PYTHON_STANDARDS_PROBE", "from-platform")
    path = tmp_path / "secrets.env"
    path.write_text("PYTHON_STANDARDS_PROBE=from-file\n", encoding="utf-8")
    load_env_file(path)
    import os

    assert os.environ["PYTHON_STANDARDS_PROBE"] == "from-platform"


def test_an_env_file_sets_what_the_environment_lacks(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHON_STANDARDS_PROBE", raising=False)
    path = tmp_path / "secrets.env"
    path.write_text(
        "# a comment\n\nPYTHON_STANDARDS_PROBE=from-file\nmalformed-line\n",
        encoding="utf-8",
    )
    load_env_file(path)
    import os

    assert os.environ["PYTHON_STANDARDS_PROBE"] == "from-file"


def test_the_env_path_sits_outside_the_repository():
    """A secret in the tree reaches an image, a history and a code search, and
    removing it afterwards does not unpublish it."""
    from pythonstandards.config import ENV_PATH

    repository = Path(__file__).resolve().parents[2]
    assert not ENV_PATH.is_relative_to(repository)


@pytest.mark.parametrize("field", ["database_url", "smtp_host", "smtp_user", "smtp_password"])
def test_every_secret_field_is_excluded_from_the_repr(field):
    """repr=False on each, so adding a field cannot quietly start printing."""
    import dataclasses

    names = {f.name: f for f in dataclasses.fields(Secrets)}
    assert names[field].repr is False
