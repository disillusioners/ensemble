#!/usr/bin/env bash
# LCA false-completion merge gate — MATRIX pack (pack 4/9, Set A integration, fast-light slice A)
# Pack: lcafc_matrix_4_intf_fast_a_integration_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9 attestation false-completion fixes; 32 files +2981/-161).
# Splitter-generated 2026-09-26. 10 files / 16 collected tests; est 30-90s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcafc_matrix_4_intf_fast_a_integration_test.sh  (from worktree root)
# Per-test override: timeout=120
# Composition: 1-test / 2-test attestation fast-light surface (e2e pairs, bound_escalation,
# compaction, config, corpus_replay, delegation_allow, fail_open, ledger_reset, nudge_chaos).
# lcancheck_matrix_4_intf_fast_a analog (2026-09-23, 20 t / 159s = ~8s/t).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin: must be on the gate branch with d5c50994 as ancestor and zero production diff
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "feature/lca-false-complete-fixes" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != feature/lca-false-complete-fixes)"; exit 1
fi
if ! git merge-base --is-ancestor d5c50994 HEAD; then
  echo "RESULT: FAIL (DRIFT — d5c50994 not ancestor of HEAD)"; exit 1
fi
if [ -n "$(git diff d5c50994 HEAD -- daemon/ frontend/ scripts/ migrations/)" ]; then
  echo "RESULT: FAIL (DRIFT — daemon/, frontend/, scripts/, or migrations/ has diff vs d5c50994)"; exit 1
fi

PACK="lcafc_matrix_4_intf_fast_a_integration_test"
FILES=(
tests/integration/test_attestation_attest_first_e2e.py
tests/integration/test_attestation_attest_first_e2e_independent.py
tests/integration/test_attestation_bound_escalation.py
tests/integration/test_attestation_compaction.py
tests/integration/test_attestation_config.py
tests/integration/test_attestation_corpus_replay.py
tests/integration/test_attestation_delegation_allow.py
tests/integration/test_attestation_fail_open.py
tests/integration/test_attestation_ledger_reset.py
tests/integration/test_attestation_nudge_chaos.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=120"
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
