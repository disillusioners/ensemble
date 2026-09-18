#!/usr/bin/env bash
# LCA user-intent merge gate — MATRIX INTEGRATION-SLOW lane (Sub-pack intf-s3)
# Pack: lcau_matrix_intf_s3_integration_test
# Tip: 47b56df8 (feature/lca-judge-user-intent, base a6442bff, clean).
# 3 heavyweight REAL-LLM-gated files: 2 tests @ ~64s, 1 test @ ~45s, 3 parametrized @ ~41s each
#   = est total 6 invocations, ~150-220s.
# Judge-LLM calls are mandatory by design (tests assert judge_invoked=True) — do NOT disable judge via env.
# Per-test pytest-timeout override documented: measured 64s / 45s / 41s under default 30s per-test cap;
#   repo precedent lca2_matrix_10..15 uses timeout=300 for this class; we use timeout=240 here per gate spec.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcau_matrix_intf_s3_integration_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcau_matrix_intf_s3_integration_test"
FILES=(
  tests/integration/test_attestation_incident_acceptance_lca.py
  tests/integration/test_attestation_ledger_reset.py
  tests/integration/test_attestation_mode_tri_state.py
)
# Belt-and-braces: some hosts export vendored cert bundles that conflict with the judge's TLS chain.
unset SSL_CERT_FILE SSL_CERT_DIR || true
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=240"
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
