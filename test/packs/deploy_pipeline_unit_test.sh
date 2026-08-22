#!/usr/bin/env bash
# test/packs/deploy_pipeline_unit_test.sh
#
# Pack: deploy_pipeline_unit_test
# Scope: scripts/deploy.sh pipeline suite (Auto-Restart Phase 1) —
#   demo/live pipeline, health gates (/livez <=60s, /readyz <=120s),
#   ENSEMBLE_DEPLOY_LIVE=1 live guard. Wraps exactly one suite:
#   tests/test_deploy.sh.
#   NOTE: the suite builds fake fixture trees and sets
#   POSTGRES_PASSWORD=fake-password-for-tests internally — suite-internal
#   fixtures, intentionally untouched by this wrapper.
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

PACK_NAME="deploy_pipeline_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
timeout 120s bash tests/test_deploy.sh 2>&1 | tee "$OUT"
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
