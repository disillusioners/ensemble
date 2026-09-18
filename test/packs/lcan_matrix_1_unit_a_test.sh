#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 1/10, Set A unit+tools slice A)
# Pack: lcan_matrix_1_unit_a_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 21 files / 489 collected tests; est 60-150s
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 240s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_1_unit_a_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_1_unit_a_test"
FILES=(
tests/unit/test_attestation_b_redaction_stage3.py
tests/unit/test_attestation_conditional_gate_outcomes.py
tests/unit/test_attestation_conditional_scanner.py
tests/unit/test_attestation_construction_site_sweep.py
tests/unit/test_attestation_dry_logging.py
tests/unit/test_attestation_epoch_replay.py
tests/unit/test_attestation_fail_open.py
tests/unit/test_attestation_fused_judge.py
tests/unit/test_attestation_fused_judge_truncation.py
tests/unit/test_attestation_gate.py
tests/unit/test_attestation_judge_resolver.py
tests/unit/test_attestation_judge_wiring.py
tests/unit/test_attestation_ledger.py
tests/unit/test_attestation_ledger_failopen.py
tests/unit/test_attestation_marker_scanner.py
tests/unit/test_attestation_marker_supersede_lca.py
tests/unit/test_attestation_marker_wiring.py
tests/unit/test_attestation_nudge_inject.py
tests/unit/test_attestation_report_judge.py
tests/unit/test_attestation_resolver.py
tests/unit/test_attestation_resolver_activation.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 240 uv run python -m pytest "${FILES[@]}" --tb=short -q
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
