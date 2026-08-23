#!/usr/bin/env bash
# test/packs/release_journal_unit_test.sh
#
# Pack: release_journal_unit_test
# Scope: Self-Restart/Self-Upgrade Phase 2 (P2.1) release/upgrade pipeline
#   unit suite — scripts/upgrade/ journal atomicity + torn-write detection,
#   cap 3/24h + cooldown + quarantine boundary math, manifest integrity +
#   version-smoke refusal, live-guard matrix (TARGET=live w/o
#   ENSEMBLE_UPGRADE_LIVE=1 → exit 78), no-.env-in-release, idempotent
#   re-stage. Wraps exactly one suite: tests/test_release_journal.sh.
#   The suite builds throwaway git-tagged fixture repos + stub binaries —
#   no real PyInstaller build, no daemon, no DB, no live contact.
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

PACK_NAME="release_journal_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
timeout 120s bash tests/test_release_journal.sh 2>&1 | tee "$OUT"
RC=$?

SUMMARY="$(grep -E '== summary: [0-9]+ passed' "$OUT" | tail -1)"
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
