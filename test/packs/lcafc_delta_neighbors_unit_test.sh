#!/usr/bin/env bash
# LCA false-completion merge gate — DELTA-NEIGHBORS pack (non-attestation direct-coverage slice)
# Pack: lcafc_delta_neighbors_unit_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9). Closes the blast-radius gap: existing tests DIRECTLY covering the
# delta's NON-attestation production modules, which live OUTSIDE the attestation matrix glob.
# Delta-touched modules covered here: job_feedback_observer.py (+83), work_notifier.py (+16),
# work_resolver.py (+33). Built 2026-09-26. 10 files / 118 collected tests; est ~118s
# (1s/t unit per brief formula; matrix_1 probe showed unit tests ms-fast, so actual << est).
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 170s guard below (brief: <=170s).
# Invocation contract: timeout 300 bash test/packs/lcafc_delta_neighbors_unit_test.sh  (from worktree root)
#
# Glob enumeration (P = priority tier; P1 > P2 > P3 > P4):
#   P1 job_feedback_observer (ls tests/job_queue/ | grep -iE 'feedback|observer' -> 4 files):
#     test_job_feedback_observer.py 48t, test_job_feedback_observer_eventbus_pairing.py 9t,
#     test_observer_hardening_f13_f14_f15.py 13t, test_phase2_feedback_verify.py 12t = 82t.
#   P2 work_resolver (glob '*work_resolver*'):
#     INCLUDED secondary: partial_collapse 11t, query_budget 6t, no_drift_warning 5t = 22t.
#   P3 work_notifier (glob '*work_notifier*'):
#     INCLUDED: defect1b_pin 3t, defect1_round3_pin 4t, n1_pin 7t = 14t.
#   EXCLUDED (out-of-scope-with-reason, budget ~120s exhausted at 118t):
#     - unit/services/test_work_resolver.py 76t: flagship alone would push cum to 158s > 120s;
#       family still covered by the 3 smaller P2 files (22t).
#     - integration/test_work_resolver_dead_letter_binding.py 4t @ ~7s/t = 28s: cum would hit 132s.
#     - job_queue/test_work_notifier_defect5_pins.py 6t + defect1_pins.py 8t: budget spent (118s).
#     - P4 mission surfaces: unit/services/test_mission_resolver.py 63t + unit/routers/test_missions_api.py 42t
#       (est 105s): lowest priority tier per brief; attestation-surface mission read-points already
#       covered by lcafc_surface_readpoints pack (other gate lane).
#   NOT included by design (attestation files — covered by matrix gate lanes):
#     test_lca_false_complete_fixes.py, test_lcafc_surface_readpoints.py, test_attestation_*.py,
#     integration replay e2e. No *jobs_crud* filename matches exist in tests/ (glob B empty for that pattern).
# All included files: no pytestmark (no addopts override needed; matrix_6 handling not applicable).
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

PACK="lcafc_delta_neighbors_unit_test"
FILES=(
tests/job_queue/test_job_feedback_observer.py
tests/job_queue/test_job_feedback_observer_eventbus_pairing.py
tests/job_queue/test_observer_hardening_f13_f14_f15.py
tests/job_queue/test_phase2_feedback_verify.py
tests/unit/services/test_work_resolver_partial_collapse.py
tests/unit/services/test_work_resolver_query_budget.py
tests/unit/test_work_resolver_no_drift_warning.py
tests/job_queue/test_work_notifier_defect1b_pin.py
tests/job_queue/test_work_notifier_defect1_round3_pin.py
tests/job_queue/test_work_notifier_n1_pin.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 170 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q
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
