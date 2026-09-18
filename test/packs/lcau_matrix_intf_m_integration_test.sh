#!/usr/bin/env bash
# LCA user-intent merge gate — MATRIX INTEGRATION-SLOW lane (Sub-pack intf-m)
# Pack: lcau_matrix_intf_m_integration_test
# Tip: 47b56df8 (feature/lca-judge-user-intent, base a6442bff, clean).
# 5 mid-tier REAL-LLM-gated files: 6+2+2+1+2 = 13 tests @ ~16-33s each,
#   = est total 130-190s (~30s avg).
# Judge-LLM calls are mandatory by design (tests assert judge_invoked=True) — do NOT disable judge via env.
# Per-test pytest-timeout override --override-ini="timeout=120": measured 16-33s/test under default
#   30s per-test cap would false-TIMEOUT; repo precedent lca2_matrix_10..15 uses timeout=300 for
#   the same class, we use timeout=120 here per gate spec (mid-tier ≤ s2/s3's 240).
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcau_matrix_intf_m_integration_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcau_matrix_intf_m_integration_test"
FILES=(
  tests/integration/test_attestation_mid_work_report_testcase.py
  tests/integration/test_attestation_delegation_allow.py
  tests/integration/test_attestation_stale_watermark.py
  tests/integration/test_attestation_nudge_chaos.py
  tests/integration/test_attestation_fail_open.py
)
# Belt-and-braces: some hosts export vendored cert bundles that conflict with the judge's TLS chain.
unset SSL_CERT_FILE SSL_CERT_DIR || true
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
