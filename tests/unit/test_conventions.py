"""Tests that enforce the README's claims about the repository itself.

A standard stated in prose and checked by nobody is a standard that erodes.
These parse the source rather than grepping it, because a string search also
matches the rule's own description in a docstring.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "pythonstandards"
TESTS = ROOT / "tests"

# These tests assert properties of the REPOSITORY, not of the code under test,
# so they have nothing to say inside a mutation workspace. mutmut copies the
# package and rewrites it with deliberately wrong variants, including ones
# that replace a constant with the literal it names, which is exactly what
# these rules forbid. Reading those as violations would fail the whole
# mutation run during stats collection, which is what happened the first time.
#
# Skipping rather than excluding the file: the rules still run on every
# ordinary suite, which is where they catch a real violation.
IN_MUTATION_WORKSPACE = ROOT.name == "mutants"
repository_only = pytest.mark.skipif(
    IN_MUTATION_WORKSPACE,
    reason="asserts a property of the repository, which a mutation copy is not",
)


def python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in str(path))


# --- standard 1: no literal escapes a named constant ---


ALLOWED_BARE_NUMBERS = {0, 1, -1, 2, 100}


@repository_only
def test_no_magic_number_sits_inside_a_comparison():
    """A threshold inside a comparison cannot be found by searching, changed
    without a deploy, or referenced by a test without repeating it."""
    offenders: list[str] = []
    for path in python_files(PACKAGE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for operand in [node.left, *node.comparators]:
                if (
                    isinstance(operand, ast.Constant)
                    and isinstance(operand.value, int)
                    and not isinstance(operand.value, bool)
                    and operand.value not in ALLOWED_BARE_NUMBERS
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{operand.lineno}: {operand.value}")
    assert offenders == [], "numbers compared against directly: " + ", ".join(offenders)


@repository_only
def test_every_environment_variable_name_is_a_constant():
    """os.getenv with a literal means the name exists in two places."""
    offenders: list[str] = []
    for path in python_files(PACKAGE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = getattr(target, "attr", None) or getattr(target, "id", None)
            if name not in ("getenv", "environ"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], "literal variable names: " + ", ".join(offenders)


# --- standard 7: nothing that looks like a credential in the tree ---


CREDENTIAL_SHAPES = (
    re.compile(r"postgres(?:ql)?://[^\s:]+:[^\s@]+@", re.I),
    re.compile(r"\b(?:api[_-]?key|secret|token)\s*=\s*['\"][A-Za-z0-9/+_-]{16,}['\"]", re.I),
)


@repository_only
def test_nothing_in_the_tree_is_shaped_like_a_live_credential():
    """A secret in the tree reaches an image, a history and a code search."""
    offenders: list[str] = []
    for path in [*python_files(PACKAGE), *python_files(TESTS)]:
        text = path.read_text(encoding="utf-8")
        for pattern in CREDENTIAL_SHAPES:
            for match in pattern.finditer(text):
                # Fixtures need credential-SHAPED values to exercise the
                # code that handles them, so one marker exempts them. The
                # marker is deliberately unmistakable: anything a person
                # might mistake for a working credential still fails.
                if "not-a-real" in match.group(0):
                    continue
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert offenders == [], "credential-shaped strings: " + ", ".join(offenders)


# --- standard 4: layering ---


LAYER_RULES = {
    "analysis": ("httpx", "psycopg", "smtplib", "jinja2"),
    "monitors": ("psycopg", "smtplib", "jinja2"),
    "models": ("httpx", "psycopg", "smtplib", "jinja2"),
}


@repository_only
@pytest.mark.parametrize("layer,forbidden", LAYER_RULES.items())
def test_a_layer_imports_nothing_from_the_layers_it_must_not_know_about(layer, forbidden):
    """A layer that fetches and decides has already eroded, and the erosion is
    invisible until something needs changing."""
    target = PACKAGE / layer
    paths = python_files(target) if target.is_dir() else [PACKAGE / f"{layer}.py"]
    offenders: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in forbidden:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
    assert offenders == [], f"{layer} reached past its layer: " + ", ".join(offenders)


@repository_only
def test_the_analysis_layer_makes_no_network_call():
    """Pure analysis is what makes every threshold testable without a network."""
    source = (PACKAGE / "analysis" / "freshness.py").read_text(encoding="utf-8")
    for forbidden in ("httpx", "requests", "urlopen", "socket"):
        assert forbidden not in source, forbidden


# --- standard 11: every finding type is reachable ---


@repository_only
def test_every_finding_type_is_constructed_somewhere():
    """A type nobody emits is a type nobody tests, and it drifts."""
    from pythonstandards.models import FindingType

    source = "\n".join(path.read_text(encoding="utf-8") for path in python_files(PACKAGE))
    unused = [
        member.name
        for member in FindingType
        if f"FindingType.{member.name}" not in source.replace(f"    {member.name} = ", "")
    ]
    assert unused == [], f"finding types nothing emits: {unused}"


# --- standard 12: the schema script matches the code's requirements ---


@repository_only
def test_the_generated_schema_section_is_current():
    """Hand-maintaining the ALTER list fails: on a sibling project it carried
    five lines while the code required seventy-five columns, so the repair the
    agent prescribed ran cleanly and changed nothing."""
    import subprocess
    import sys

    before = (ROOT / "sql" / "create_tables.sql").read_text(encoding="utf-8")
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "generate_schema_alters.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    after = (ROOT / "sql" / "create_tables.sql").read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert before == after, "sql/create_tables.sql is stale. Run scripts/generate_schema_alters.py."


@repository_only
def test_the_schema_gate_covers_every_required_column():
    from pythonstandards.db import Database

    schema = (ROOT / "sql" / "create_tables.sql").read_text(encoding="utf-8")
    for table, columns in Database.REQUIRED_COLUMNS.items():
        for column in columns:
            assert f"('{table}', '{column}')" in schema, f"{table}.{column}"


# --- the repository's own hygiene ---


@repository_only
def test_no_test_writes_inside_the_repository(app_config):
    """A fixture aimed at the project's own output directory leaves files
    beside the real runs' output, where test output becomes indistinguishable
    from a real report."""
    assert not app_config.output_dir.resolve().is_relative_to(ROOT)


@repository_only
def test_the_text_character_gate_is_present_and_invoked_through_bash():
    """A /mnt/c checkout cannot record an executable bit, so CI has to name
    the interpreter rather than relying on the file's mode."""
    assert (ROOT / "scripts" / "check-text-characters.sh").exists()
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "bash scripts/check-text-characters.sh" in makefile


# --- the lock, which the install path depends on ---


@repository_only
def test_the_lock_covers_every_pinned_requirement():
    """A requirement absent from the lock cannot be installed, because the
    install path has no fallback. The two drifting apart breaks the build
    rather than quietly installing something unverified, which is the trade
    this repository chose."""
    pinned = {
        line.split("==")[0].split("[")[0].strip().lower().replace("_", "-")
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.strip().startswith("#")
    }
    locked = {
        line.split("==")[0].strip().lower().replace("_", "-")
        for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.strip().startswith("#")
    }
    missing = sorted(pinned - locked)
    assert missing == [], f"pinned but not locked: {missing}"


@repository_only
def test_every_locked_release_carries_at_least_one_hash():
    """A release line with no hash installs unverified while the file still
    looks like a lock."""
    text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    releases = re.findall(r"^([a-zA-Z0-9._-]+==[^\s\\]+)\s*\\?\s*$", text, re.M)
    assert releases, "the lock names no releases at all"
    for release in releases:
        start = text.index(release)
        following = text[start : start + 600].splitlines()[1:2]
        assert following and "--hash=sha256:" in following[0], release


@repository_only
def test_the_install_path_has_no_silent_fallback():
    """A fallback would install without hash verification while still
    reporting success, so a reader would believe the artifacts were checked."""
    for name in ("Makefile", "Dockerfile"):
        text = (ROOT / name).read_text(encoding="utf-8")
        install_lines = [
            line
            for line in text.splitlines()
            if "pip install" in line and not line.strip().startswith("#")
        ]
        assert install_lines, name
        for line in install_lines:
            assert "2>/dev/null" not in line, f"{name}: {line.strip()}"
            assert "||" not in line, f"{name}: {line.strip()}"
            assert "--require-hashes" in line, f"{name}: {line.strip()}"
