"""Renders the digest and decides whether it is worth sending.

Everything a template renders arrives already escaped. A package name, a
version string and an index's error text all come from somewhere else, and
Python-Markdown passes raw HTML through untouched, so an unescaped value
becomes live markup in an email a reader trusts.

See standards 6 and 9 in the README.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from pythonstandards.analysis.freshness import rank
from pythonstandards.config import AppConfig, Secrets
from pythonstandards.models import Digest, Finding, RunResult

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "digest.md"
REPORT_FILENAME_TEMPLATE = "digest_{date}.md"

# Characters that turn an external string into markup or a Markdown construct.
_HTML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
# Only the constructs that could become a link, an image, emphasis, code or a
# broken table cell. Escaping every period as well would render identically
# while making the plain-text part unreadable.
_MARKDOWN_SPECIALS = set("\\`*_[]()!|")


def escape_external(value: object) -> str:
    """Render an untrusted string as one line of text, in Markdown and HTML.

    Collapsing whitespace is part of the escaping rather than cosmetic:
    Markdown is line-oriented, so a newline inside a value ends the table row
    holding it and everything after it parses as fresh block-level markup. A
    value carrying two newlines and a heading can forge a whole section of the
    report.
    """
    if not value:
        return ""
    flattened = " ".join(str(value).split())
    escaped = "".join(_HTML_ESCAPES.get(char, char) for char in flattened)
    return "".join("\\" + char if char in _MARKDOWN_SPECIALS else char for char in escaped)


def sanitize(findings: list[Finding]) -> list[Finding]:
    """Copies with every externally sourced field escaped.

    The stored findings keep their real text. Only what reaches a template is
    escaped, so the database still holds what the index actually said.
    """
    return [
        finding.model_copy(
            update={
                "subject": escape_external(finding.subject),
                "message": escape_external(finding.message),
                "recommendation": escape_external(finding.recommendation),
            }
        )
        for finding in findings
    ]


class ReportAgent:
    """Builds the digest, writes it, and sends it only when it says something."""

    def __init__(self, config: AppConfig, secrets: Secrets) -> None:
        self.config = config
        self.secrets = secrets
        templates = Path(__file__).resolve().parents[1] / "reporting" / "templates"
        self._jinja = Environment(
            loader=FileSystemLoader(templates),
            # A missing variable raises rather than rendering as empty. A
            # digest with a silently blank section is worse than no digest,
            # because nobody can tell the difference between nothing to report
            # and a template that lost its variable.
            undefined=StrictUndefined,
            # S701 wants autoescape on, and it is wrong for this template.
            # Jinja's autoescape is HTML escaping, and this template renders
            # MARKDOWN: turning it on would write &amp; and &lt; into a
            # document a reader opens as text, corrupting the output rather
            # than protecting it, and it would escape the markup the template
            # itself writes.
            #
            # What replaces it: escape_external runs over every externally
            # sourced value before it reaches a variable, which is stricter
            # than HTML escaping because it also neutralizes the Markdown
            # constructs autoescape knows nothing about. The suppression is
            # inline and carries its reason rather than retiring S701 for the
            # file, so a second Environment added here still has to justify
            # itself.
            autoescape=False,  # noqa: S701
            trim_blocks=False,
            lstrip_blocks=False,
        )

    def build(self, digest: Digest, run_result: RunResult) -> str:
        """The rendered Markdown, with every external value already escaped."""
        open_now = self._open_snapshot(digest)
        return self._jinja.get_template(TEMPLATE_NAME).render(
            site_name=self.config.reporting.get("site_name", "Dependency digest"),
            report_date=digest.report_date.isoformat(),
            new_findings=sanitize(rank(digest.new_findings)),
            resolved_findings=sanitize(digest.resolved_findings),
            # One ranked list over everything open, not two sorted lists
            # concatenated: joining them puts every new low-severity finding
            # ahead of an existing high one, and a slice then drops the high.
            fix_list=sanitize(rank(open_now)),
            notes=[escape_external(note) for note in digest.notes],
            run_result=run_result,
        )

    @staticmethod
    def _open_snapshot(digest: Digest) -> list[Finding]:
        """Everything open right now, each appearing once."""
        resolved = {finding.id for finding in digest.resolved_findings}
        seen: set[str] = set()
        snapshot: list[Finding] = []
        for finding in list(digest.new_findings) + list(digest.open_findings):
            if finding.id in resolved or finding.id in seen:
                continue
            seen.add(finding.id)
            snapshot.append(finding)
        return snapshot

    def write(self, report: str, digest: Digest) -> Path:
        directory = self.config.output_dir
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / REPORT_FILENAME_TEMPLATE.format(date=digest.report_date.isoformat())
        path.write_text(report, encoding="utf-8")
        logger.info("report written to %s", path)
        return path

    @staticmethod
    def should_send(digest: Digest) -> bool:
        """Send only when something changed.

        A digest that arrives every run with the same list trains its reader
        to delete it unread, which costs more than sending nothing.
        """
        return bool(digest.new_findings or digest.resolved_findings)

    def send(self, report: str, digest: Digest, recipients: list[str]) -> None:
        """Deliver the digest, or raise so a scheduler sees the failure."""
        if not recipients:
            logger.info("no recipients configured, so nothing was sent")
            return

        message = MIMEMultipart("alternative")
        message["Subject"] = f"Dependency digest, {digest.report_date.isoformat()}"
        message["From"] = self.secrets.smtp_user
        message["To"] = ", ".join(recipients)
        message.attach(MIMEText(report, "plain"))

        # An explicit timeout. smtplib defaults to the socket's global
        # default, which is None, meaning block forever: a host that accepts
        # the connection and never greets holds the process here indefinitely,
        # with no exception to catch and no next run.
        with smtplib.SMTP(
            self.secrets.smtp_host,
            self.secrets.smtp_port,
            timeout=self.config.registry.request_timeout_seconds,
        ) as server:
            # An explicit default context requires certificate-chain and
            # hostname validation. starttls() with no context uses the legacy
            # SMTP context, which verifies nothing, so an active intermediary
            # could present any certificate and receive the password sent on
            # the very next line.
            server.starttls(context=ssl.create_default_context())
            server.login(self.secrets.smtp_user, self.secrets.smtp_password)
            server.send_message(message)
        logger.info("digest sent to %d recipient(s)", len(recipients))
