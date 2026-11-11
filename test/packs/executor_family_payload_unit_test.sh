#!/usr/bin/env bash
# test/packs/executor_family_payload_unit_test.sh
#
# Pack: executor_family_payload_unit_test
# Scope: the D3 EXECUTOR SCRIPT FAMILY layout pin — the release payload and
# the flat install must carry scripts/upgrade/* + scripts/stop-ensemble.sh +
# scripts/install-dir-guard.sh at the canonical payload-relative paths, with
# the stop-script SIBLING the promote stop step resolves present AND
# executable, the family under the release's manifest/sha coverage, NO plist
# in the payload, and the PIPELINE_SCRIPTS_REV stamp covering the shipped
# set. Wraps exactly one suite: tests/test_executor_family_payload.sh.
#
# The suite is HERMETIC: every fixture is built under a fresh mktemp dir
# under $TMPDIR (a synthetic git repo for the stamp-coverage legs). It never
# reads or writes the live install dir, never runs make install/uninstall/
# deploy-live/deploy-demo, never executes scripts/stop-ensemble.sh, and
# never touches a daemon. `make -n install` inside the suite is a DRY RUN
# (make prints the recipe, executes nothing).
#
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

PACK_NAME="executor_family_payload_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
timeout 120s bash tests/test_executor_family_payload.sh 2>&1 | tee "$OUT"
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
