# Python Standards

**A reference implementation of the Python conventions I hold teams to.** Every file here demonstrates a standard, and the agent they build runs for real: it asks a package index about the versions this project pins, decides which pins have fallen behind, persists what it found with a lifecycle, and reports a digest.

Its companions, [go-standards](https://github.com/craig-hunt/go-standards), [java-standards](https://github.com/craig-hunt/java-standards), [rust-standards](https://github.com/craig-hunt/rust-standards) and [csharp-service-standards](https://github.com/craig-hunt/csharp-service-standards), apply the same discipline to services. Those four implement one shared domain as a JSON API, which is how a reader compares a rule across languages.

**This one diverges on purpose.** Python earns a standards repository here because every agent I run is written in it: scheduled processes that fetch, decide, report and persist, with no request to serve. The rules that matter for those are about exit codes a scheduler reads, secrets that never touch the repository, and layers that stay separate under pressure. A tasks API would demonstrate none of them, so the reference is an agent.

**Platform.** Linux or WSL with Docker and Python 3.12. Commands appear in bash form.

---

## The standards

### 1. No literal escapes a named constant, tests included

Every value carrying meaning beyond its immediate expression lives in a named constant or in configuration. Environment variable names, protocol keys, status codes, thresholds, exit codes.

```python
# Wrong
if response.status_code == 429:
    ...
os.getenv("SMTP_PASSWORD", "")

# Right
if response.status_code in RATE_LIMIT_STATUSES:
    ...
os.getenv(ENV_SMTP_PASSWORD, "")
```

The distinction between `constants.py` and `config.yaml` is whether changing the value is a decision or a fact. A threshold somebody might tune is configuration. The name of a field in a registry's JSON is a constant.

Tests reference the production constant rather than repeating its value, so a renamed key fails once rather than drifting silently. `tests/conftest.py` holds one fixture per shape for the same reason.

**Two layers enforce it.** `tests/unit/test_conventions.py` parses every module and fails on an integer compared against directly or an environment variable named by a literal. Ruff adds the rest. Review covers what remains.

### 2. Twelve-factor processes

| Factor | Where it shows |
| --- | --- |
| Codebase | One repository, one package, one entry point |
| Dependencies | `requirements.txt` pins exact versions, never ranges |
| Config | `config.yaml` for settings, the environment for secrets, never the code |
| Backing services | The database is a URL; nothing assumes it is local |
| Build, release, run | The image is built once and run unchanged |
| Processes | Stateless between runs; every fact lives in Postgres |
| Disposability | A run can be killed at any point and the next one is correct |
| Dev/prod parity | `compose.yaml` runs the same Postgres version as deployment |
| Logs | Written to stdout as an event stream, never to a file |

A range in a requirements file lets an upgrade arrive between a passing CI run and a deploy, which makes the artifact that shipped different from the one that was tested. A pinned version narrows that; a hash closes it, because a version can still be re-uploaded under the same number or served by a mirror nobody audited.

**The install path has no fallback, deliberately.** An install that failed its hash check and quietly fell back to an unverified one would still print success, so a reader would believe the artifacts were verified when nothing had checked them. A test fails if that fallback ever returns.

### 3. Warnings fail the build, and the rule set is pinned

`ruff.toml` names each rule family explicitly rather than inheriting a preset. A preset changes with every release, so an upgrade can add a rule nobody chose or retire one the README still claims.

`pytest.ini` sets `filterwarnings = error`. A warning nobody reads costs nothing to ignore, so they fail instead. That caught an unset `asyncio_default_fixture_loop_scope` on the first run of this project, which would otherwise have changed behavior silently in a later release.

**Nothing is ignored globally.** Two suppressions exist, both inline and both carrying their reason: one where a constant holding an environment variable's *name* reads as a hardcoded password, and one where Jinja autoescape would HTML-escape a Markdown document and corrupt it. An exception that cannot explain itself does not get written.

### 4. Layering, enforced by a test rather than by intention

Four layers, and a dependency that points the wrong way is a test failure:

| Layer | Answers | Must not |
| --- | --- | --- |
| `monitors/` | What did the registry say | Decide what it means, persist, report |
| `analysis/` | What counts as a problem | Make a request, touch a database |
| `agents/` | What reaches a person | Decide what is true |
| `db.py` | What is remembered | Any of the above |

`test_conventions.py` parses each layer's imports and fails when one reaches past itself. A layer that fetches and decides has already eroded, and the erosion stays invisible until somebody needs to change one of the two.

The analysis layer is pure: it receives measurements and a date, and returns judgments. That is what makes every threshold testable at its exact boundary without a network, a clock or a container.

### 5. Types over primitives where a domain exists

Pydantic models rather than dicts. A value arriving from a registry response gets validated at the boundary instead of failing three layers later with a message about a `NoneType`.

`Observation` and `Finding` are deliberately separate. An observation is a measurement and never changes; a finding is a judgment about measurements and has a lifecycle. Storing only findings makes "when did this pin fall behind" unanswerable.

A finding's `id` hashes its identity, never its payload, so a reworded message does not create a second row for one problem.

### 6. Everything external is escaped before it reaches a reader

A package name, a version string and an index's error text all come from somewhere else. Markdown is line-oriented, so a newline inside a value ends the table row holding it and everything after parses as fresh markup: a value carrying two newlines and a heading can forge a whole section of a report somebody trusts.

`escape_external` neutralizes the HTML characters and the Markdown constructs that build links, images, emphasis and table cells, and collapses whitespace. The stored finding keeps its real text; only what reaches a template is escaped.

### 7. Secrets never enter the repository

Locally they come from an env file under `~/.agent-secrets/`, outside the tree and outside anything a build copies. In a container they arrive as the platform's own secrets. A secret in the tree reaches an image, a git history and a code search, and removing it afterwards does not unpublish it.

`Secrets` marks every field `repr=False`. One `logger.debug` of the object, or one traceback rendering locals, would otherwise write both passwords into a log. `test_conventions.py` fails on any credential-shaped string in the tree, with one unmistakable marker exempting the fixtures that need the shape.

### 8. The schema script can repair any database it describes

`CREATE TABLE IF NOT EXISTS` leaves an existing table alone, which means it also leaves a missing column alone. `sql/create_tables.sql` therefore carries an `ALTER` for every column, generated from its own `CREATE TABLE` statements by `scripts/generate_schema_alters.py`, and ends with a gate that names any remaining gap and exits non-zero.

Hand-maintaining that list fails. On a sibling project it carried five `ALTER` lines while the code required seventy-five columns, so the repair the agent prescribed ran cleanly, changed nothing, and left the agent hard-failing every run with an instruction that could not work. A test fails when the generated section is stale.

The agent checks the schema before any stage. A mismatch found there costs nothing; the same mismatch found at the final write has already spent the run.

### 9. Limits on everything a stranger controls

A remote host decides how much it sends and how slowly. Three limits apply to every request:

- **A split timeout**, because one number bounds each operation separately and a host sending a byte before each read deadline holds a connection open indefinitely.
- **A wall-clock deadline** on the whole transfer, which httpx does not offer.
- **A byte cap**, applied while streaming rather than after, so the download itself is bounded.

When the cap bites, the agent says it truncated the response. Attributing its own cut to the index names the wrong culprit, which is what happened when a real payload exceeded the original cap and was reported as a malformed response.

### 10. Raise rather than return on an unrecoverable failure

A bare return exits zero, and a scheduler reads zero as success. A failure that returns is a failure nobody hears about.

`HardFailure` is raised when the run's output cannot be trusted: no requirements file, no exact pins, a schema older than the code, or every package unreadable. That last one matters most. If no package could be read, the index was unreachable; reporting that as a clean run tells a reader their pins are fine when nothing was checked.

`main` is the only place that decides an exit code, and every failure path logs why before returning it.

### 11. Mutation testing gates the build at 70%

Coverage says a line ran. Mutation says a test would have noticed had the line been wrong, which is the only one of the two a reader can act on.

`scripts/mutation-gate.sh` reads killed apart from timed-out. mutmut's summary counts a timeout alongside a kill, and a mutant that timed out was never shown to be caught by anything; it was shown to be slow. Counting it as a kill inflates the score by exactly the number of mutants nobody tested.

**Prose sits outside the denominator.** A mutant that rewords a log line or a recommendation is unkillable without a test that pins the copy, and pinning copy makes an editorial pass fail the suite while proving nothing about behavior. The exclusion patterns in `setup.cfg` stay narrow on purpose: matching every string would also exclude the behavioral ones, a finding's type, its detail key, an environment variable name, and those must stay in.

### 12. One authority on formatting

`ruff format`, checked in CI. No second formatter, no per-file style, no argument about it in review.

### 13. Comments explain why, never what

The code says what it does. A comment earns its place when it carries a constraint, an invariant, or the reason a non-obvious choice is the right one. Docstrings on the models carry the reasoning rather than restating the field names.

Every suppression in this repository names its reason. A reader who disagrees can argue with the reason rather than guessing at it.

### 14. The verify chain is one command, and CI runs the same one

`make verify` runs lint, format, the text-character gate and the suite. CI runs the same commands rather than inlining its own, because a workflow that inlines drifts from what anybody runs locally and the drift stays invisible until CI fails on something that passed.

The text-character gate fails on characters that mark machine drafting: the em-dash, the invisible space family, curly quotes, the ellipsis character. It is invoked as `bash scripts/...` rather than executed directly, because a checkout on a filesystem that cannot record an executable bit still has to run it.

---

## Running it

```bash
make venv install
make verify

# Against the live index, writing a report and touching no database
.venv/bin/python -m pythonstandards --dry-run --skip-notify
```

With a database:

```bash
docker compose up -d postgres
psql "$DATABASE_URL" -f sql/create_tables.sql
.venv/bin/python -m pythonstandards
```

## What this repository does not cover

SQL has no standards repository, and the conventions behind the stored procedures and the idempotent DDL in the agent suite live only in those projects. That is a gap, named here rather than left for somebody to discover.
