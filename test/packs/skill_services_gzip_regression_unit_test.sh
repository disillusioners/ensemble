#!/usr/bin/env bash
# Test Pack: skill_services_gzip_regression_unit_test - LLM-gzip merge-gate
# regression for the 3 modified skill services.
#
# The LLM-gzip feature modified three skill services to resolve an HTTP
# client via `from .llm_gzip import resolve_gzip_client` at their LLM
# call seams:
#   - daemon/services/skill_search_service.py     (+37)
#   - daemon/services/skill_embedding_service.py (+75)
#   - daemon/services/skill_evolution_service.py (+38)
#
# This pack runs the 4 canonical suites for those services (the 3
# service-level suites under tests/services/ + the manager-level init
# wiring test under tests/manager/) as a single-shot merge-gate
# regression.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 120s` pytest guard
#
# RESULT-echo discipline (avoid the repo "set -e RESULT-echo flaw"):
# `set -e` fires on the pytest non-zero exit BEFORE the next command runs
# - even an `EXIT_CODE=$?` standalone assignment is too late to suppress
# it. The standard idiom is `|| EXIT_CODE=$?` which places the assignment
# in a list-context (`||`) that `set -e` exempts: when pytest exits 0
# the `||` clause is skipped and EXIT_CODE stays at its prior value
# (initialized to 0 above); when pytest exits non-zero the `||` clause
# captures that exit code into EXIT_CODE. The if/elif chain below then
# echoes the actual outcome (PASS / FAIL / TIMEOUT).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# --- SSL cleanup (mirror buffer_response_header_family_unit_test.sh) ---
unset SSL_CERT_FILE
unset SSL_CERT_DIR

echo "=== Test Pack: skill_services_gzip_regression_unit_test ==="

cd "$PROJECT_DIR"

EXIT_CODE=0
timeout 120s .venv/bin/pytest \
  tests/services/test_skill_search_service.py \
  tests/services/test_skill_embedding_service.py \
  tests/services/test_skill_evolution_service.py \
  tests/manager/test_skill_service_init.py \
  --tb=short -q 2>&1 || EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
