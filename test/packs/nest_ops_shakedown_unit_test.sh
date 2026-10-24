#!/usr/bin/env bash
# Test Pack: nest_ops_shakedown_unit_test — the nest-ops shakedown defect
# patch: the REAL-PostgreSQL read-side husk fleet + the ops-side e2e pins.
#
# Scope: EXACTLY two files (38 tests):
#   - tests/integration/test_nestops_husk_fleet_real_pg.py (18 tests):
#     the TWO-TIER >window fixture (2 real-spawn husks + 1,001 filler),
#     the §3.5 leg table, the F3xF4 coupling, F2 census, F4a truncated,
#     and the T4.8 scan-cost measurement (D-20 arbiter).
#   - tests/integration/test_nestops_ops_real_pg.py (20 tests): F11 e2e
#     both directions through the REAL order (job_create -> dispatch ->
#     claim -> funnel -> protected act) and F6/F7/F10 on a real audit
#     ledger.
#
# Branch pin: fix/nest-ops-shakedown-defects.
# Base: b257842c9 (origin/latest at lane start); ladder M = cdf348057.
#
# TEST-ENV ONLY fence:
#   - Disposable PostgreSQL LOGIN role + database on the LOCAL PG16
#     test server (PG_TEST_HOST/PORT, default localhost:5432), created
#     and DROPPED per module. NEVER ensemble_prod, never the live
#     daemon on :9797.
#   - The AMBIENT POSTGRES_* values are scrubbed PER-VARIABLE below
#     (the `env -u POSTGRES_*` glob idiom is a silent NO-OP — house
#     critical note); the fixtures pin the five variables explicitly to
#     the disposable pair in-process.
#   - No $HOME-derived live path is ever resolved (tmp_path-scoped).
#   - The husk UUIDs are fixtures only — never dispatched to.
#
# Dual-layer timeout (per .agents/tester/rules/ensure.md:6 + :45 — the
# dual-layer rule; NOT the retired :16-20 citation):
#   - Layer 1 (command-level): caller wraps with `timeout 330`
#   - Layer 2 (script-internal): `timeout 300s` on the pytest process
#     (38 real-PG tests; ~150 s observed in calibration on the local
#     PG16 — the disposable-DB lifecycle + 8 real spawns dominate;
#     300 s is a 2x margin, inside the 5-min pack cap).
#   - Per-test: `--timeout=120` (the pyproject 30 s default kills the
#     real-spawn tests prematurely).
#
# PREREQUISITE (the pack conventions gap, arch §11.5): this pack runs
# `.venv/bin/pytest` from the IN-WORKTREE virtualenv — a fresh worktree
# has NO .venv (house rule: lazy per-worktree `uv sync`, NEVER reuse
# another checkout's interpreter). The pack ASSERTS .venv exists and
# refuses loudly otherwise; document `uv sync --frozen` as the fix.
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail
IFS=$'\n\t'
export PATH="/home/nea/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

# The .venv prerequisite (arch §11.5): refuse to run on a foreign
# interpreter — a missing in-worktree .venv means `uv sync --frozen`.
if [ ! -x ".venv/bin/pytest" ]; then
  echo "RESULT: FAIL — no in-worktree .venv (run: uv sync --frozen; never reuse another checkout's interpreter)"
  exit 1
fi

echo "=== Test Pack: nest_ops_shakedown_unit_test (real-PG, 38 tests, 2 files) ==="

# Layer 2 (script-internal): 300s hard cap; ambient POSTGRES_* scrubbed
# PER-VARIABLE (never the glob idiom).
set +e
timeout 300s env \
  -u POSTGRES_DB \
  -u POSTGRES_USER \
  -u POSTGRES_PASSWORD \
  -u POSTGRES_HOST \
  -u POSTGRES_PORT \
  -u POSTGRES_URL \
  .venv/bin/pytest \
  tests/integration/test_nestops_husk_fleet_real_pg.py \
  tests/integration/test_nestops_ops_real_pg.py \
  --tb=short -q \
  --override-ini="addopts=" \
  -m integration \
  --timeout=120 \
  -p no:cacheprovider 2>&1
RC=$?
set -e

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  echo "Exit: $RC"
  exit 1
fi
