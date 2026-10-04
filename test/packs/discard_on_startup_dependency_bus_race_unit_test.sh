#!/usr/bin/env bash
# test/packs/discard_on_startup_dependency_bus_race_unit_test.sh
#
# Pack: discard_on_startup_dependency_bus_race_unit_test
# Scope: F-1 (durability-f1-f2 / phase1) bus gate + wipe-side
#   predicate — 7 unit tests + 5 helper tests pinning both
#   seams (plan §1, §1a, §2, §13b, §13c).
#
# Tests in tests/unit/services/test_discard_on_startup_dependency_bus_race.py:
#   * _has_truthy_error helper (plan §1.2) — 5 tests
#   * test_none_error_does_not_flip_parent_error (S2; plan §1)
#   * test_real_error_flips_parent_error (S3; plan §1)
#   * test_terminal_auto_continued_survives_clear (S4; plan §2)
#   * test_terminal_no_marker_deleted_by_clear (S5; plan §2)
#   * test_boot_sequence_mock_candidates_one (S6; plan §2)
#   * test_double_restart_no_double_continue (S1; plan §2)
#   * test_boot_auto_continued_preserve_kill_switch (S7; W-3 / §13c
#     — pins BOTH ON (preserve) AND OFF (delete) paths for
#     ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE)
#
# Internal watchdog (Layer 2): 110s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Transparent wrapper: no test deselection, no modification; inner pytest
# exit code is propagated as-is. Mirrors the
# auto_continue_boot_pass_unit_test pack wrapper (verbatim structure).

set -u
cd "$(dirname "$0")/../.." || {
    echo "FAIL: cannot cd to repo root"
    echo "RESULT: FAIL"
    exit 1
}

PACK_NAME="discard_on_startup_dependency_bus_race_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
    tests/unit/services/test_discard_on_startup_dependency_bus_race.py \
    --tb=short -q 2>&1 | tee "$OUT"
EXIT_CODE=$?
rm -f "$OUT"

echo
if [ "$EXIT_CODE" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
elif [ "$EXIT_CODE" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL (exit=${EXIT_CODE})"
    exit 1
fi
