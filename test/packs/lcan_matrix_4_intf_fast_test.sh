#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 4/10, Set A integration, probe-verified fast tier)
# Pack: lcan_matrix_4_intf_fast_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 6 files / 13 collected tests; measured 173s total @ per-test 80s
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_4_intf_fast_test.sh  (from worktree root)
# Per-test override: timeout=120
# Probe evidence 2026-09-18: in_graph 15s, fail_open 17s, nudge_chaos 20s, stale_watermark 28s, mid_work 46s, ledger_reset 47s (all rc=0 standalone).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_4_intf_fast_test"
FILES=(
tests/integration/test_attestation_in_graph_nudge_flow.py
tests/integration/test_attestation_fail_open.py
tests/integration/test_attestation_nudge_chaos.py
tests/integration/test_attestation_stale_watermark.py
tests/integration/test_attestation_mid_work_report_testcase.py
tests/integration/test_attestation_ledger_reset.py
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
