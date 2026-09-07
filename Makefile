# Sanctioned way to run anything that needs a secret.
#
# .env is a mount created by the 1Password MCP server's create_local_env_file
# (1password.dev/environments/local-env-file), tied to the sector_momentum
# Environment. It already delivers resolved values, not op:// references —
# confirmed 2026-09-06 by running `make build` with 1Password's CLI
# integration (Settings → Developer → "Integrate with 1Password CLI") turned
# OFF and it still worked. `op run` here is a pass-through: it reads already-
# resolved values from the mount and injects them into the child process
# only, without ever needing the CLI's account-wide shared session.
#
# THIS MATTERS BEYOND THIS REPO: that CLI integration toggle is account-wide,
# not per-project — turning it on for any reason exposes every vault in the
# tenant (including client vaults, for a consultancy account) to every shell
# on the machine, this one included. Never turn it on to make a `make`
# target work. If a future need genuinely requires op://-reference
# resolution (op run against literal op:// strings, not a mount), use a
# vault-scoped OP_SERVICE_ACCOUNT_TOKEN instead — never the shared CLI
# session.
#
# .claude/settings.json denies Claude Code direct `op`, `env`, `printenv`
# and .env reads. These targets are the narrow hole in that wall: they run
# the app WITH secrets while offering no route that prints one. Keep them
# that way — a target like `op run -- env` would defeat the whole point.
#
# You are not restricted by any of this; the deny list applies to Claude
# Code's tool use, not to your own shell.

OP_RUN := op run --env-file=.env --

.PHONY: help build scan test restore restore-list restore-local

help:  ## Show the available targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-14s %s\n", $$1, $$2}'

build: ## Rebuild the dashboard into docs/ (reads the DB)
	$(OP_RUN) python3 dashboard/build.py

scan:  ## Run a full scan — WRITES to the live database
	$(OP_RUN) python3 scan.py

restore: ## Restore the latest DB backup — DESTRUCTIVE, overwrites live data
	$(OP_RUN) python3 restore.py

restore-list: ## List available Storage backups without restoring
	$(OP_RUN) python3 restore.py --list

restore-local: ## Restore from a local backup dir — DESTRUCTIVE. Usage: make restore-local DIR=path/to/backup
	$(OP_RUN) python3 restore.py --local $(DIR)

test:  ## Run the test suite (needs no secrets; DB-backed tests skip)
	python3 -m pytest -q
