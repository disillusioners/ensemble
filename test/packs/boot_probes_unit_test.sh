#!/usr/bin/env bash
# test/packs/boot_probes_unit_test.sh
#
# Pack: boot_probes_unit_test
# Scope: boot path + health probes seam (Auto-Restart Phase 1) —
#   /livez and /readyz probe routes, exit-75/78 boot preflight,
#   timeout-graceful-shutdown wiring, reasoning-echo ClassVar
#   coexistence in __main__. ONE pytest invocation covering exactly
#   two files of the same seam:
#     - tests/test_health_probes.py
#     - tests/test_main_entry.py
#   Repo default addopts stay in effect (pyproject: -m 'not integration
#   and not postgres', per-test timeout=30s via pytest-timeout) — no -m
#   filters are added or overridden here.
# Internal watchdog (Layer 2): 120s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Transparent wrapper: no test deselection, no modification; inner pytest
# exit code is propagated as-is.
# Ref: agents-ensemble test-pack skill (dual-layer timeout, explicit RESULT).

set -u
cd "$(dirname "$0")/../.." || {
    echo "FAIL: cannot cd to repo root"
    echo "RESULT: FAIL"
    exit 1
}

PACK_NAME="boot_probes_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
timeout 120s .venv/bin/pytest \
    tests/test_health_probes.py \
    tests/test_main_entry.py \
    --tb=short -q 2>&1 | tee "$OUT"
RC=$?

SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
rm -f "$OUT"
if [ -n "$SUMMARY" ]; then echo "SUMMARY: $SUMMARY"; fi

echo
if [ "$RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
elif [ "$RC" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL (exit=${RC})"
    exit 1
fi
