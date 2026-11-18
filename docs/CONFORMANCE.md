# What this repository claims, and where to check it

**AUTHOR** Craig Hunt

A standard stated in prose and checked by nobody erodes. This table maps each
numbered standard in the README to the thing that enforces it, so a reader can
confirm the claim rather than trust it.

| # | Standard | Enforced by |
| --- | --- | --- |
| 1 | No literal escapes a named constant | `tests/unit/test_conventions.py`, which parses every module and fails on an integer compared against directly or an environment variable named by a literal, plus ruff's own rules |
| 2 | Twelve-factor processes | `requirements.txt` pins exactly; `test_config.py` proves settings and secrets arrive from different places; the Dockerfile copies no secret |
| 3 | Warnings fail the build, rule set pinned | `ruff.toml` names each family; `pytest.ini` sets `filterwarnings = error`; CI fails on any finding |
| 4 | Layering | `test_conventions.py` parses each layer's imports and fails when one reaches past itself |
| 5 | Types over primitives | `test_models.py`, including that a finding's identity survives a reworded message |
| 6 | External text is escaped | `test_report_agent.py`, including a value carrying newlines and a heading, which would otherwise forge a section |
| 7 | Secrets never enter the repository | `test_conventions.py` fails on any credential-shaped string; `test_config.py` proves every `Secrets` field is excluded from the repr |
| 8 | The schema script can repair what it describes | `test_conventions.py` regenerates the file and fails when it differs; the gate inside the script fails on any column it cannot add |
| 9 | Limits on everything a stranger controls | `test_registry_monitor.py`, including a truncated body, a compressed body and a split timeout |
| 10 | Raise rather than return | `test_entrypoint.py`, which asserts the exit code for each failure path |
| 11 | Mutation gates the build at 70% | `scripts/mutation-gate.sh`, which reads killed apart from timed out |
| 12 | One authority on formatting | `ruff format --check` in CI |
| 13 | Comments explain why | Review. Not mechanically checkable, and claiming otherwise would be the kind of overstatement the rest of this table exists to avoid |
| 14 | One verify chain, CI runs the same one | The Makefile defines each target; the workflow runs the same commands |

## What is not covered

**SQL.** The stored-procedure header conventions, the idempotent DDL pattern
and the container-verified script discipline used across the agent suite have
no repository. This one borrows the generated-ALTER approach and the
completeness gate, and that is the whole of it. Named here rather than left to
be discovered.

**Comment quality.** Standard 13 is enforced by review alone. A linter can
find a comment; it cannot tell whether the comment says why.
