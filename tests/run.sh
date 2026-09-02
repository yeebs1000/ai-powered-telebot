#!/usr/bin/env bash
# Run the test suite.
#
#   tests/run.sh          offline tests only — no network, no model, no keys
#   tests/run.sh --all    also the tests that need a reachable model endpoint
#
# Tests are plain scripts that assert and print, not a pytest suite: they are
# meant to be readable as documentation of what the bot guarantees, and to run
# from a clean checkout with nothing but the requirements installed.
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi

ALL=0
[ "${1:-}" = "--all" ] && ALL=1

# Offline tests must not reach the network. These make the provider resolvable
# without one: a base_url is set, but nothing calls it.
export AI_PROVIDER="${AI_PROVIDER:-local}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:1/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-test}"
export AI_MODEL="${AI_MODEL:-test-model}"
export TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-test}"

pass=0; fail=0; skip=0; failed=()
for t in tests/t_*.py; do
  name=$(basename "$t" .py)
  if grep -q '^REQUIRES_MODEL' "$t" && [ "$ALL" -eq 0 ]; then
    printf '  %-14s SKIP (needs a model; run with --all)\n' "$name"
    skip=$((skip + 1)); continue
  fi
  if out=$("$PY" "$t" 2>&1); then
    printf '  %-14s ok\n' "$name"
    pass=$((pass + 1))
  else
    printf '  %-14s FAIL\n' "$name"
    echo "$out" | tail -15 | sed 's/^/      /'
    fail=$((fail + 1)); failed+=("$name")
  fi
done

echo
echo "  $pass passed, $fail failed, $skip skipped"
[ "$fail" -eq 0 ] || { echo "  failed: ${failed[*]}"; exit 1; }
