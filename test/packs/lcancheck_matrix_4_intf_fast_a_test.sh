#!/usr/bin/env bash
# LCA Completion Check Note removal merge gate — MATRIX pack (pack 4/9, Set A integration, mock-fast slice A)
# Pack: lcancheck_matrix_4_intf_fast_a_test
# Gate: feature/lca-remove-check-note @ ff9eb849 (delta 6bf7bed7..ff9eb849, 1 commit).
# Splitter-generated 2026-09-23. 9 files / 20 collected tests; est 30-90s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 240s guard below.
# Invocation contract: timeout 300 bash test/packs/lcancheck_matrix_4_intf_fast_a_test.sh  (from worktree root)
# Per-test override: timeout=120
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin: must be on the gate branch with ff9eb849 as ancestor and zero daemon/scripts diff
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "feature/lca-remove-check-note" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != feature/lca-remove-check-note)"; exit 1
fi
if ! git merge-base --is-ancestor ff9eb849 HEAD; then
  echo "RESULT: FAIL (DRIFT — ff9eb849 not ancestor of HEAD)"; exit 1
fi
if [ -n "$(git diff ff9eb849 HEAD -- daemon/ scripts/)" ]; then
  echo "RESULT: FAIL (DRIFT — daemon/ or scripts/ has diff vs ff9eb849)"; exit 1
fi

PACK="lcancheck_matrix_4_intf_fast_a_test"
FILES=(
tests/integration/test_attestation_attest_first_e2e.py
tests/integration/test_attestation_attest_first_e2e_independent.py
tests/integration/test_attestation_bound_escalation.py
tests/integration/test_attestation_compaction.py
tests/integration/test_attestation_config.py
tests/integration/test_attestation_corpus_replay.py
tests/integration/test_attestation_delegation_allow.py
tests/integration/test_attestation_dry_mode.py
tests/integration/test_attestation_fail_open.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 240 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=120"
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
