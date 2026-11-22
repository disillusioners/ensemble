#!/usr/bin/env bash
# Test Pack: engine_observability_unit_test — Engine Arc P6 (T6.7)
#
# Purpose: the P6 observability substrate — the pure read model
# (projection.py: frozen view shapes, chart_hint derivation, zero-run
# honesty, cursor paging, convergence union with per-field sources, the
# resolved switch block, the bounded runs tail, the T6.3 deferral record)
# + the router (three GETs, method freeze {GET}, scope matrix 401/403/200,
# AUTH-OFF refusal byte-comparable to the metrics door, off-loop build)
# + the T6.3 census ceremony leg (the metrics-family adjudication pack
# incl. the daemon-diff confinement pin, GREEN after the union) + the
# MANAGED_KEYS re-baseline + the F-2 maintenance block-shape fence.
#
# PACKS.md REGISTRATION IS P7's (phase6-plan.md T6.7) — this file ships
# unregistered by design.
#
# Expected test count: 93 (5 targets: projection 23, routes 20, census 48,
# settings_resolution gate node 1) — exact count varies with the census
# file's growth in other lanes; the RESULT line is the contract.
# Timeout: 3 minutes (the census file reads the git diff; unit class).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: engine_observability_unit_test ==="

cd "$PROJECT_DIR"

# FAIL-PATH REACHABILITY: under `set -euo pipefail` a bare non-zero
# pytest would abort the script BEFORE `EXIT_CODE=$?`, making the
# RESULT: FAIL / RESULT: TIMEOUT branches below unreachable — exactly
# when the contract matters. Capture the status in an OR-list instead.
EXIT_CODE=0
timeout 180s .venv/bin/pytest \
  tests/unit/services/engine/test_projection.py \
  tests/api/test_engine_routes.py \
  tests/unit/metrics/test_metrics_family_adjudication.py \
  "tests/unit/test_maintenance_idle_starvation.py::TestSkipStreakTelemetry::test_job_metrics_exposes_one_block_per_job" \
  "tests/test_settings_resolution.py::TestManagedKeysConsistency::test_every_entry_is_consistent" \
  --override-ini="addopts=" --tb=short -q 2>&1 || EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL (exit $EXIT_CODE)"
  exit $EXIT_CODE
fi
