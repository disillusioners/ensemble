#!/usr/bin/env bash
# Test Pack: resource_monitor_flusher_bind_pg_test — the resource
# monitor's FLUSHER BIND PATH against REAL PostgreSQL.
#
# Scope: tests/postgres/test_resource_monitor_flusher_bind_pg.py —
#   * the REAL sampler row travels through the REAL flusher into REAL
#     PostgreSQL (the chain that was DARK in production);
#   * EVERY column the flusher binds is type-audited against the LIVE
#     information_schema type AND against a HARD-CODED expected type
#     (B7a — the bound-column list is derived from the flusher SOURCE,
#     so a model mutation can no longer hide a drift);
#   * EVERY bound T2 column is SELECTed back and value-asserted (B7b),
#     including the written graph_cache_present=False, the
#     value-asserted bucket_ts and the estimated DEFAULT-TRUE path;
#   * the DEGRADED SENTINEL row (the B1 evidence path for a dropped
#     sample) lands in T1 with its marker and its NULL metrics, and
#     does NOT pollute the sample tiers.
#
# Marker flags (mandatory — the default addopts EXCLUDE the postgres
# marker, so a run without them collects 0 tests and reports a
# VACUOUS GREEN):
#   --override-ini="addopts="   → drop the default -m filter
#   -m postgres                 → select the postgres-marked tests
#
# Scratch discipline: the target is a SANCTIONED scratch database
# (the repo's hard allowlist: ensemble_dev_test_only /
# ensemble_test_only). The test module's own fence refuses anything
# else BEFORE the session-scoped drop_all can touch the target. The
# database must already EXIST — the pack fails loudly (never a silent
# skip) when it does not.
#
# Every PG_* / PG_TEST_* variable is set EXPLICITLY for the pytest
# process (an ambient POSTGRES_* is never trusted and never inherited
# as the target).
#
# Usage: bash test/packs/resource_monitor_flusher_bind_pg_test.sh
# Per-leg pytest rc: 0 ok / 1 test failure / 5 nothing collected
#   (collection error, or the -m postgres filter matched nothing) /
#   124 leg timeout (outer `timeout 300s`).
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. FAIL covers ANY rc!=0
#   (incl. 5) AND a summary missing "N passed" with N>=1 (the explicit
#   collected-count check — GREEN-AT-ZERO IS A FAILURE).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: resource_monitor_flusher_bind_pg_test ==="

cd "$PROJECT_DIR"

# --- scratch database -------------------------------------------------------
PG_TEST_HOST="${PG_TEST_HOST:-localhost}"
PG_TEST_PORT="${PG_TEST_PORT:-5432}"
PG_TEST_DB="${PG_TEST_DB:-ensemble_test_only}"
PG_TEST_USER="${PG_TEST_USER:-ensemble}"
PG_TEST_PASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}"

if [ ! -x ".venv/bin/pytest" ]; then
  echo "RESULT: FAIL (no .venv/bin/pytest in ${PROJECT_DIR})"
  exit 1
fi

# The scratch DB must pre-exist. Fail LOUDLY instead of letting the
# pytest probe SKIP the whole module (a skip is a vacuous green).
if ! PGPASSWORD="$PG_TEST_PASSWORD" psql -h "$PG_TEST_HOST" -p "$PG_TEST_PORT" \
      -U "$PG_TEST_USER" -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname = '${PG_TEST_DB}'" 2>/dev/null \
      | grep -q 1; then
  echo "RESULT: FAIL (scratch database '${PG_TEST_DB}' does not exist on ${PG_TEST_HOST}:${PG_TEST_PORT} — create it first; the pack never creates or drops it)"
  exit 1
fi

LOG="$(mktemp -t rm_flusher_bind_pg.XXXXXX)"
trap 'rm -f "$LOG"' EXIT

# Run the leg NON-FATALLY and capture its rc: under `set -euo pipefail`
# a failing leg would kill the pack at the pytest line itself, leaving
# the RESULT labels below unreachable. A red leg still fails the pack
# at the checks below (fail-closed preserved).
set +e
timeout 300s env \
  PG_TEST_HOST="$PG_TEST_HOST" \
  PG_TEST_PORT="$PG_TEST_PORT" \
  PG_TEST_DB="$PG_TEST_DB" \
  PG_TEST_USER="$PG_TEST_USER" \
  PG_TEST_PASSWORD="$PG_TEST_PASSWORD" \
  .venv/bin/pytest \
    tests/postgres/test_resource_monitor_flusher_bind_pg.py \
    --override-ini="addopts=" -m postgres -q --tb=short -p no:randomly \
    >"$LOG" 2>&1
RC="$?"
set -e

cat "$LOG"

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
fi

# Explicit ran-count assertion: the summary must show "N passed" with
# N>=1. "no tests ran" (pytest rc=5 no-collect), a collection error and
# a fully-skipped module all land here with a loud label instead of
# relying on pytest's rc semantics alone.
SUMMARY="$(grep -Eo '[0-9]+ passed' "$LOG" | tail -1 || true)"
case "$SUMMARY" in
  [0-9]*) PASSED_N="${SUMMARY%% *}" ;;
  *)      PASSED_N="" ;;
esac
if [ -z "$PASSED_N" ] || [ "$PASSED_N" -lt 1 ]; then
  echo "RESULT: FAIL (rc=${RC}, collected-count check: no \"N passed\" N>=1 in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "$RC" -ne 0 ]; then
  echo "RESULT: FAIL (rc=${RC}, ${PASSED_N} passed before failure)"
  exit 1
fi

echo "[leg ok] ${PASSED_N} passed (rc=${RC})"
echo "RESULT: PASS"
exit 0
