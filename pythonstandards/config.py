"""Settings and secrets, read once at the edge of the process.

Two sources, kept apart on purpose. Settings live in config.yaml and belong in
the repository, because a reader should be able to see what the agent will do
without running it. Secrets never enter the repository: locally they come from
an env file outside the tree, and in a container they arrive as the platform's
own secrets.

See standards 2 and 7 in the README.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pythonstandards.constants import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_OBSERVATION_DAYS,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REGISTRY_BASE_URL,
    DEFAULT_REQUEST_DEADLINE_SECONDS,
    DEFAULT_REQUEST_DELAY_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_SMTP_PORT,
    ENV_DATABASE_URL,
    ENV_SMTP_HOST,
    ENV_SMTP_PASSWORD,
    ENV_SMTP_PORT,
    ENV_SMTP_USER,
)

# Outside the repository, and outside any directory a build copies. A secret
# in the tree reaches a container image, a git history and a code search, and
# removing it afterwards does not unpublish it.
ENV_PATH = Path.home() / ".agent-secrets" / "python-standards.env"


@dataclass
class RegistryConfig:
    base_url: str = DEFAULT_REGISTRY_BASE_URL
    delay_between_requests_s: float = DEFAULT_REQUEST_DELAY_SECONDS
    request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    # A per-operation timeout is not a deadline. A host sending one byte before
    # each read deadline expires holds a connection open for as long as it
    # likes, so the caller bounds the whole transfer separately.
    request_deadline_seconds: int = DEFAULT_REQUEST_DEADLINE_SECONDS
    # Nothing bounds what a remote host returns, and a parser builds a
    # structure several times the size of its input.
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES


@dataclass
class AppConfig:
    """Everything config.yaml carries, with a documented fallback per value."""

    registry: RegistryConfig = field(default_factory=RegistryConfig)
    freshness: dict[str, Any] = field(default_factory=dict)
    reporting: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)

    def threshold(self, name: str, default: int) -> int:
        """A freshness threshold, or the documented fallback.

        Reading through one accessor means a missing key produces the
        documented behavior rather than a KeyError partway through a run, and
        it gives a test one place to override.
        """
        return int(self.freshness.get(name, default))

    @property
    def output_dir(self) -> Path:
        return Path(str(self.reporting.get("output_dir", DEFAULT_OUTPUT_DIR)))

    @property
    def observation_days(self) -> int:
        return int(self.retention.get("observation_days", DEFAULT_OBSERVATION_DAYS))

    @property
    def retention_enabled(self) -> bool:
        return bool(self.retention.get("enabled", True))


@dataclass
class Secrets:
    """Credentials, with every field excluded from the repr.

    A dataclass prints every field by default, so one logger.debug of this
    object, or one traceback that renders locals, would write the database
    password and the SMTP password into a log. Nothing stringifies it today;
    repr=False means nothing can.
    """

    database_url: str = field(default="", repr=False)
    smtp_host: str = field(default="", repr=False)
    smtp_port: int = field(default=int(DEFAULT_SMTP_PORT), repr=False)
    smtp_user: str = field(default="", repr=False)
    smtp_password: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> Secrets:
        return cls(
            database_url=os.getenv(ENV_DATABASE_URL, ""),
            smtp_host=os.getenv(ENV_SMTP_HOST, ""),
            # `or` rather than a default argument: a platform secret set to an
            # empty string is not the same as an unset one, and int("") raises
            # before the first stage with a message naming nothing.
            smtp_port=int(os.getenv(ENV_SMTP_PORT) or DEFAULT_SMTP_PORT),
            smtp_user=os.getenv(ENV_SMTP_USER, ""),
            smtp_password=os.getenv(ENV_SMTP_PASSWORD, ""),
        )


def load_env_file(path: Path = ENV_PATH) -> None:
    """Load an env file if it exists, without overwriting the environment.

    A container receives its secrets from the platform and has no file, so an
    absent file is the normal case rather than an error. Existing values win,
    because a value the platform set is the one that should be used.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip())


def load_config(path: str | Path = "config.yaml") -> tuple[AppConfig, Secrets]:
    """Settings and secrets together, because every caller needs both."""
    load_env_file()
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    registry = raw.get("registry") or {}
    return (
        AppConfig(
            registry=RegistryConfig(
                base_url=str(registry.get("base_url", DEFAULT_REGISTRY_BASE_URL)),
                delay_between_requests_s=float(
                    registry.get("delay_between_requests_s", DEFAULT_REQUEST_DELAY_SECONDS)
                ),
                request_timeout_seconds=int(
                    registry.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)
                ),
                request_deadline_seconds=int(
                    registry.get("request_deadline_seconds", DEFAULT_REQUEST_DEADLINE_SECONDS)
                ),
                max_response_bytes=int(
                    registry.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES)
                ),
            ),
            freshness=raw.get("freshness") or {},
            reporting=raw.get("reporting") or {},
            retention=raw.get("retention") or {},
        ),
        Secrets.from_env(),
    )
