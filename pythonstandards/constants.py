"""Values with meaning beyond the expression that uses them.

A literal inside a comparison cannot be found by searching, cannot be changed
without a deploy, and cannot be referenced by a test without the test
repeating it. Every one of those is a way for two places to disagree silently.

Settings a reader might tune live in config.yaml instead. The difference is
whether changing the value is a decision (config) or a fact about the protocol
or the schema (here).

See standard 1 in the README.
"""

from typing import Final

# --- the registry protocol ---

PACKAGE_PATH_TEMPLATE: Final = "{base}/{name}/json"
RESPONSE_INFO_KEY: Final = "info"
RESPONSE_VERSION_KEY: Final = "version"
RESPONSE_RELEASES_KEY: Final = "releases"
RELEASE_UPLOAD_TIME_KEY: Final = "upload_time_iso_8601"
USER_AGENT_HEADER: Final = "User-Agent"
USER_AGENT: Final = (
    "python-standards-reference/1.0 (+https://github.com/craig-hunt/python-standards)"
)

# A status the index returns for a package that does not exist, which is a
# finding about the pin rather than a failure of the run.
STATUS_NOT_FOUND: Final = 404
STATUS_OK: Final = 200
# Statuses a public index returns when it is protecting itself. They say
# nothing about the package, so they must not be read as an answer.
RATE_LIMIT_STATUSES: Final = (429, 503)

# --- the database ---

SCHEMA_TABLES: Final = ("findings", "observations", "run_log")
FINDING_ID_LENGTH: Final = 16

# --- exit codes a scheduler reads ---

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1

# --- environment variable names ---

ENV_DATABASE_URL: Final = "DATABASE_URL"
ENV_SMTP_HOST: Final = "SMTP_HOST"
ENV_SMTP_PORT: Final = "SMTP_PORT"
ENV_SMTP_USER: Final = "SMTP_USER"
# S105 reads the name as a hardcoded password. It holds the NAME of an
# environment variable, and the value never appears in the repository. The
# suppression is inline and carries its reason, rather than retiring S105 for
# the whole file: a real hardcoded credential two lines below would then pass.
ENV_SMTP_PASSWORD: Final = "SMTP_PASSWORD"  # noqa: S105
DEFAULT_SMTP_PORT: Final = "587"

# --- documented fallbacks for every configured value ---
#
# Each mirrors config.yaml. A missing key then produces the documented
# behavior rather than a KeyError three stages into a run, and the fallback
# sits next to the name a reader searches for.

DEFAULT_REGISTRY_BASE_URL: Final = "https://pypi.org/pypi"
DEFAULT_REQUEST_DELAY_SECONDS: Final = 0.25
DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 10
DEFAULT_REQUEST_DEADLINE_SECONDS: Final = 30
DEFAULT_MAX_RESPONSE_BYTES: Final = 12_000_000
DEFAULT_MAJOR_VERSIONS_BEHIND: Final = 1
DEFAULT_MINOR_VERSIONS_BEHIND: Final = 3
DEFAULT_DAYS_SINCE_RELEASE: Final = 365
DEFAULT_OUTPUT_DIR: Final = "reports"
DEFAULT_OBSERVATION_DAYS: Final = 400
LEAKED = "postgresql://user:aT0tallyRealPassword@db.example.com/prod"
