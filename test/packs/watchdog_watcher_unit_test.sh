#!/usr/bin/env bash
# test/packs/watchdog_watcher_unit_test.sh
#
# Pack: watchdog_watcher_unit_test
# Scope: watchdog-watcher launchd agent suite (Auto-Restart Phase 1, m3) —
#   observation-only latch, exit-0 on unresolvable INSTALL_DIR,
#   explicit-zero tunable rejection. Wraps exactly one suite:
#   tests/test_watchdog_watcher.sh.
#   NOTE: the suite starts tiny fake livez HTTP servers on dynamic high
#   ports — suite-internal fixtures, intentionally untouched by this
#   wrapper; their lifecycle is managed by the suite itself.
# Internal watchdog (Layer 2): 120s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Transparent wrapper: no test deselection, no modification; inner suite
# exit code is propagated as-is.
# Ref: agents-ensemble test-pack skill (dual-layer timeout, explicit RESULT).

set -u
cd "$(dirname "$0")/../.." || {
    echo "FAIL: cannot cd to repo root"
    echo "RESULT: FAIL"
    exit 1
}

PACK_NAME="watchdog_watcher_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
timeout 120s bash tests/test_watchdog_watcher.sh 2>&1 | tee "$OUT"
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
