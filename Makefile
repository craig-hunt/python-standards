# One entry point per check, so CI and a developer run the same command.
#
# A workflow that inlines its commands drifts from what anybody runs locally,
# and the drift is invisible until CI fails on something that passed here.
# See standard 14 in the README.

PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: help venv install lock lint format-check text-check test mutation schema-check verify clean

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | sort

venv: ## Create the virtual environment
	$(PYTHON) -m venv $(VENV)

install: ## Install from the hash-pinned lock, and only from it
	@# No fallback. A fallback to requirements.txt would install without hash
	@# verification while the command still printed success, so a reader would
	@# believe the artifacts were verified when nothing checked them.
	$(BIN)/pip install --require-hashes --no-deps -r requirements.lock

lock: ## Regenerate requirements.lock from requirements.txt
	bash scripts/generate-lock.sh

lint: ## Fail on any lint finding
	$(BIN)/ruff check pythonstandards tests scripts

format-check: ## Fail when formatting differs from the one authority
	$(BIN)/ruff format --check pythonstandards tests scripts

text-check: ## Fail on characters that mark machine drafting
	@# Named through bash rather than executed directly: a checkout on a
	@# filesystem that cannot record an executable bit still runs this.
	bash scripts/check-text-characters.sh .

schema-check: ## Fail when the generated schema section is stale
	$(BIN)/python scripts/generate_schema_alters.py
	@git diff --quiet sql/create_tables.sql \
		|| (echo 'sql/create_tables.sql was stale; the generator updated it' && exit 1)

test: ## Run the suite
	$(BIN)/python -m pytest

mutation: ## Run mutation testing; the gate lives in docs/MUTATION.md
	$(BIN)/python -m mutmut run

verify: lint format-check text-check test ## Everything CI runs

clean:
	rm -rf $(VENV) mutants .pytest_cache .ruff_cache reports
