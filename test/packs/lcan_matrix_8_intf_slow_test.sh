#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 8/10, Set A integration, remaining mock-fast scenarios)
# Pack: lcan_matrix_8_intf_slow_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 16 files / 79 collected tests; est 30-120s (calibration-backed)
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_8_intf_slow_test.sh  (from worktree root)
# Per-test override: timeout=120
# Calibration 2026-09-18: heaviest class members ran 3s/file; per-test 120 covers scenario matrices. No probe data per-file — first runner execution is the timing truth.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_8_intf_slow_test"
FILES=(
tests/integration/test_attestation_compaction.py
tests/integration/test_attestation_config.py
tests/integration/test_attestation_corpus_replay.py
tests/integration/test_attestation_dry_mode.py
tests/integration/test_attestation_marker_bound_enforcement_lca.py
tests/integration/test_attestation_marker_routing_lca.py
tests/integration/test_attestation_nudge_supersede_lca.py
tests/integration/test_attestation_o1_boot_assert.py
tests/integration/test_attestation_observability.py
tests/integration/test_attestation_performance.py
tests/integration/test_attestation_stage3_zoo_test.py
tests/integration/test_attestation_user_answer_pending_lca.py
tests/integration/test_empty_guard_checkpoint_roundtrip.py
tests/integration/test_lcau_incident_e2e.py
tests/integration/test_long_tool_nudge_e2e.py
tests/integration/test_service_tool_flag_off_byte_identical.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=120"
RC=$?
END=$(date +%s)
echo "Pack inner runtime: $((END-START))s"
if [ $RC -eq 124 ]; then
  echo "RESULT: TIMEOUT"; exit 124
elif [ $RC -eq 0 ]; then
  echo "RESULT: PASS"; exit 0
else
  echo "RESULT: FAIL"; exit 1
fi
