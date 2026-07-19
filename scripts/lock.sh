#!/usr/bin/env bash
# Regenerate both lockfiles for the platform CI actually runs on (Ubuntu x86_64).
#
# The flags matter — omitting them has broken production twice (2026-07-18/19):
#   --python-platform : compiling on macOS resolves mac wheels (torch capped at
#                       2.2.2 on Intel targets, which cascaded into a yfinance
#                       downgrade that broke all price fetches).
#   --upgrade         : uv pip compile otherwise preserves versions already in
#                       the output file (torch stayed at 2.2.2 even after the
#                       platform fix, breaking transformers' >=2.4 floor).
set -euo pipefail
cd "$(dirname "$0")/.."

uv pip compile requirements.txt \
  --python-version 3.11 \
  --python-platform x86_64-unknown-linux-gnu \
  --upgrade \
  -o requirements.lock

uv pip compile requirements-dev.txt \
  --python-version 3.11 \
  --python-platform x86_64-unknown-linux-gnu \
  --upgrade \
  -o requirements-dev.lock

echo "--- key pins ---"
grep -E "^(yfinance|requests|torch|transformers|urllib3)==" requirements.lock
