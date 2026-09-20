#!/usr/bin/env bash
# LCA stale-A (B1) merge gate — LIVE fused-judge pack (softened-subordination
# carve-outs must NOT reopen the false-rescue channel).
# Pack: lca_stale_a_judge_live_test
# Worktree: feature/lca-stale-a-fix @ e0d15e93 (base a6442bff lineage).
# Driver:  tests/integration/test_lca_stale_a_judge_live.py  (2 scenarios)
#   S1 resolved-stale + genuine report  -> verdict "complete"
#   S2 genuine child-lie + claim        -> verdict "not_complete"
# REAL judge, REAL prompt, REAL assemble_fused_bundle — NO stubbing.
# Budget: 2 live LLM calls (one per scenario; per-attempt cap 90s inside the
# driver, retry-once-on-timeout worst case 180s per test).
# Env: credentials inherited (OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL).
# A wrong verdict is a FAIL — never a skip. An auth/network failure (no
# verdict produced) SKIPs as LIVE-JUDGE-ENV-BLOCKED and this pack reports
# PASS-with-skips + an "ENV-BLOCKED: <detail>" line (via -s -ra streaming).
# Per-test timeout: 200s (--override-ini="timeout=200"); addopts cleared so
# the integration-marked driver actually runs.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard.
# Invocation contract: timeout 300 bash test/packs/lca_stale_a_judge_live_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca_stale_a_judge_live_test"
DRIVER="tests/integration/test_lca_stale_a_judge_live.py"
echo "=== Test Pack: ${PACK} ==="
echo "Driver: ${DRIVER} (2 live-judge scenarios)"
echo "Drift pin: $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
START=$(date +%s)
timeout 280 uv run python -m pytest "$DRIVER" \
  --override-ini="addopts=" --override-ini="timeout=200" \
  --tb=short -q -s -ra
RC=$?
END=$(date +%s)
echo "Pack inner runtime: $((END-START))s"
if [ $RC -eq 124 ]; then
  echo "RESULT: TIMEOUT"; exit 124
elif [ $RC -eq 0 ]; then
  # Pass-with-skips (env-blocked) still reports PASS per convention; the
  # ENV-BLOCKED lines are already in the streamed output above.
  echo "RESULT: PASS"; exit 0
else
  echo "RESULT: FAIL (exit=$RC)"; exit 1
fi
