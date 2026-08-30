#!/usr/bin/env bash
# Test Pack: pg_pending_watchers_probe_test — REAL-PG behavioral probe
# of the task-26303 prod bug fix (commit a77647bf).
#
# Background. Prod v0.11.3 log 2026-08-30 09:25:25:
#   psycopg UndefinedFunction (varchar = integer) at bus.pending_watchers
#   — SQLAlchemy binds int param against varchar column source_task_id
#   (models.py Column(String,64)) via fetch_pending_for_source
#   (repository.py:143-145) ← pending_watchers (dependency_bus.py:935-937)
#   ← _pattern_f_check_bus_pending (job_recovery_service.py:3208).
# Fix (a77647bf): str(task_id) at the call site.
#
# Spec: this probe is the RUNTIME PROOF that the fix shape works on
# real PostgreSQL — Part A of the headline task probes production
# (read-only); Part C is the repeatable fallback on ensemble_test.
#
# Three probes (real PG, throwaway schema gate_pg_probe):
#   P1 — REPRO INT bind against varchar source_task_id → UndefinedFunction
#   P2 — FIX-SHAPE STR bind against varchar source_task_id → clean
#   P3 — Cleanup DROP SCHEMA IF EXISTS gate_pg_probe CASCADE in finally
#
# TEST-ENV ONLY. NEVER accepts prod credentials. PG_TEST_PASSWORD
# defaults to ensemble_dev (matches tests/postgres/conftest.py:68-74).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 180s` around the python process
#     (probe target <3 min; the python script additionally self-guards
#     with signal.alarm(150) and exits 124 on its own timer).
#
# Skip-with-PASS-note if PG unreachable (env-dependent). Set
# PG_PROBE_FAIL_ON_NO_PG=1 to fail instead of skip.
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -u
cd "$(dirname "$0")/../.."
PROJECT_DIR="$(pwd)"

echo "=== Test Pack: pg_pending_watchers_probe_test ==="
echo "(PG Pending-Watchers Type-Binding Probe — task 26303, fix a77647bf)"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python3
fi

# PG_TEST_* env defaults mirror tests/postgres/conftest.py:68-74
export PG_TEST_HOST="${PG_TEST_HOST:-localhost}"
export PG_TEST_PORT="${PG_TEST_PORT:-5432}"
export PG_TEST_DB="${PG_TEST_DB:-ensemble_test}"
export PG_TEST_USER="${PG_TEST_USER:-ensemble}"
export PG_TEST_PASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}"

# Layer 2 (script-internal): 180s hard cap on the python process.
timeout 180s "$PY" test/packs/pg_pending_watchers_probe_test.py 2>&1
RC=$?

# The python script emits its own "RESULT: PASS|FAIL|TIMEOUT" line;
# we keep this mapping as a safety net (in case the script is killed
# before printing its own result, e.g. via `kill -9`).
if [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
elif [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
else
  echo "RESULT: FAIL"
  exit 1
fi