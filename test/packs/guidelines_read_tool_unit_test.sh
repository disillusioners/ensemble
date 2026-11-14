#!/usr/bin/env bash
# Test Pack: guidelines — the guideline-read-tool registration + payload seam
#             (guideline-read-tool commission, 2026-11-13, phases 1–3;
#              registered by phase 4 as a THIN WRAPPER — zero test logic
#              lives here; the assertions are owned by the two suites).
#
# Runs the two suites this lane shipped:
#   1. tests/unit/tools/test_guidelines_registration.py (pytest)
#      - the registration pins (test-matrix A): AST discovery, decorator
#        order, CATEGORY_MODULES path, DYNAMIC/KNOWN names, extend-order,
#        privileged membership + exact set + count 22, meta.json ALLOW
#        census (EXACTLY ari/leader/devops/sonic-leader),
#        GUIDELINES_AUTHORIZED_AGENTS, gate-order-in-source, warm-list
#      - allow gates (B) + parser fail-soft (C) + verbatim round-trip +
#        typed refusals + write-path absence
#      - the tool-side doctrine lines pinned VERBATIM (docstring +
#        CATEGORY_DOC + _full_doc_)
#   2. tests/test_guidelines_payload.sh (hermetic bash, D1–D7)
#      - the phase-3 stage seam: flat guidelines/ payload,
#        guidelines_tree_sha256 + guidelines_manifest, stage-local
#        re-hash leg (green + negative), precondition guard, mktemp
#        fixture discipline (live install NEVER touched)
#
# Fences: no PG, no live DB, no live install paths; the payload suite
# builds every fixture under mktemp. Both legs are explicitly bounded
# (long-tool advisory: stdin redirected, timeout per leg).
set -uo pipefail

unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: guidelines_read_tool ==="

cd "$PROJECT_DIR"

RC=0

echo "--- leg 1: tests/unit/tools/test_guidelines_registration.py ---"
timeout 180s .venv/bin/pytest \
  tests/unit/tools/test_guidelines_registration.py \
  --tb=short -q -p no:warnings < /dev/null 2>&1
LEG1=$?
if [ $LEG1 -eq 124 ]; then
  echo "RESULT: TIMEOUT (leg 1)"
  exit 124
elif [ $LEG1 -ne 0 ]; then
  RC=1
fi

echo "--- leg 2: tests/test_guidelines_payload.sh ---"
timeout 480s bash tests/test_guidelines_payload.sh < /dev/null 2>&1
LEG2=$?
if [ $LEG2 -eq 124 ]; then
  echo "RESULT: TIMEOUT (leg 2)"
  exit 124
elif [ $LEG2 -ne 0 ]; then
  RC=1
fi

if [ $RC -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
