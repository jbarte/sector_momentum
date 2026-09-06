# Sanctioned way to run anything that needs a secret.
#
# `op run` reads .env, resolves any op:// references against 1Password, and
# injects the results into the child process only — nothing is written to
# disk and nothing is printed. Plain (non-reference) values pass straight
# through, so these targets work both before and after .env is migrated to
# 1Password Environments; there is no flag day.
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
