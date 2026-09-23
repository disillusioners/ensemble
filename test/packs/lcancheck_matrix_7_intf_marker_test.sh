#!/usr/bin/env bash
# LCA Completion Check Note removal merge gate — MATRIX pack (pack 7/9, Set A integration, delta-touched surface + childlie)
# Pack: lcancheck_matrix_7_intf_marker_test
# Gate: feature/lca-remove-check-note @ ff9eb849 (delta 6bf7bed7..ff9eb849, 1 commit).
# Splitter-generated 2026-09-23. 3 files / 37 collected tests (16+18+3 with override); est 60-180s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcancheck_matrix_7_intf_marker_test.sh  (from worktree root)
# Per-test override: timeout=120
# Marker override: --override-ini="addopts=" (test_lcan_childlie_e2e.py has pytestmark =
# pytest.mark.integration and is deselected by the default addopts `-m 'not integration and
# not postgres'`; an EMPTY addopts removes the deselection filter so all 3 files collect
# cleanly: marker_routing_lca (16) + stage2_failopen (18) + childlie_e2e (3) = 37 tests).
# Per-test timeout set via a second --override-ini="timeout=120" so the heavy marker-routing
# scenario matrices don't trip the 30s default. This trio IS the delta's directly-touched
# integration surface (marker_routing -115/-115 line shift; stage2_failopen -114/-114;
# childlie_e2e -29/-29).
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

PACK="lcancheck_matrix_7_intf_marker_test"
FILES=(
tests/integration/test_attestation_marker_routing_lca.py
tests/integration/test_attestation_stage2_failopen.py
tests/integration/test_lcan_childlie_e2e.py
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
