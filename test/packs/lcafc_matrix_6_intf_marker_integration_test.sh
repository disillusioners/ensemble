#!/usr/bin/env bash
# LCA false-completion merge gate — MATRIX pack (pack 6/9, Set A integration, marker family + addopts override)
# Pack: lcafc_matrix_6_intf_marker_integration_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9 attestation false-completion fixes; 32 files +2981/-161).
# Splitter-generated 2026-09-26. 4 files / 40 collected tests (with override); est 60-180s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcafc_matrix_6_intf_marker_integration_test.sh  (from worktree root)
# Marker override: --override-ini="addopts=" (test_lcan_childlie_e2e.py AND
# test_lcancheck_b2f4dae9_regression.py BOTH carry pytestmark = pytest.mark.integration
# and are deselected by the default addopts `-m 'not integration and not postgres'`;
# an EMPTY addopts removes the deselection filter so all 4 files collect cleanly:
# marker_routing_lca (16) + stage2_failopen (18) + childlie_e2e (3) + b2f4dae9_regression (3) = 40).
# Per-test override: --override-ini="timeout=120" so scenario matrices don't trip the 30s default.
# Composition: the directly-touched marker routing surface (marker_routing_lca -115/-115;
# stage2_failopen -114/-114) + the childlie E2E regression pair (3 tests each).
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

PACK="lcafc_matrix_6_intf_marker_integration_test"
FILES=(
tests/integration/test_attestation_marker_routing_lca.py
tests/integration/test_attestation_stage2_failopen.py
tests/integration/test_lcan_childlie_e2e.py
tests/integration/test_lcancheck_b2f4dae9_regression.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="addopts=" --override-ini="timeout=120"
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
