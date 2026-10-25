#!/usr/bin/env bash
# Test Pack: notify_user — the ari-voice behavioural unit surface
#             (ari-notify-user commission, 2026-10-25, base 7af1d2632d).
# Timeout: 3 minutes (180s) — target < 2 min runtime
#
# Runs the tool + registration suites added by this lane:
#   1. tests/unit/test_notify_user_tool.py (36 tests)
#      - the D1 resolution chain: the 13-edge census (architecture §4)
#      - the rate gates: C3 commit-before-count under the B2 lock, the
#        N3 budget predicate (accepted-class only; refusals never
#        consume; the allow-direction probe), the N4 crash-orphan
#        sweep, the restart-durability sim
#      - failure paths: timeout (delivery UNKNOWN, never retried),
#        definite failure after the single retry (D10), N2 fail-closed,
#        N9 kill-switch, the ari-only literal-name bypass control
#      - W10 security properties: gate concurrency (structural
#        barrier), cross-instance shared-cap, ledger completeness +
#        body-never-cleartext
#   2. tests/unit/test_notify_user_registration.py (14 tests)
#      - the three-step registration seam: AST discovery, decorator
#        order, CATEGORY_MODULES daemon.tools.* path, extends-order,
#        exact-set 16, count 16, ari-only census over agents/*/meta.json,
#        the layer-3 gate-order pin
#
# NOT included here: the W10 MUTATION legs — they mutate production
# sources and live in their own pack:
#   test/packs/notify_user_mutation_legs.sh (5 legs, RED→GREEN each).
#
# Fences: temp-file SQLite only (no PG, no live DB); the notify env
# knobs are scrubbed per-variable by an autouse fixture; no live
# install paths are resolved.
set -euo pipefail

# ─── SSL cleanup ──────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: notify_user ==="

cd "$PROJECT_DIR"

timeout 180s .venv/bin/pytest \
  tests/unit/test_notify_user_tool.py \
  tests/unit/test_notify_user_registration.py \
  --tb=short -q -p no:warnings 2>&1

EXIT_CODE=$?

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
