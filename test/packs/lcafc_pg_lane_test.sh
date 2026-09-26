#!/usr/bin/env bash
# Test Pack: lcafc_pg_lane — LCA False Completion fix PG-lane merge gate (Job 6a).
#
# Purpose: PG-lane merge gate for incident 7d4a3bd9 (correct-judge-override class).
# Worktree branch feature/lca-false-complete-fixes @ d5c50994c61ba6c0a762c9dd128d9aa5603eca2a.
# Delta touches daemon/graph.py (gate region), daemon/services/attestation_gate.py,
# daemon/services/attestation_resolver.py, daemon/services/attestation_judge_timeout_resolver.py,
# daemon/services/mission_resolver.py, daemon/services/work_resolver.py,
# daemon/services/work_notifier.py, daemon/services/job_feedback_observer.py,
# daemon/services/instance_messaging.py, daemon/routers/jobs_crud.py,
# daemon/routers/missions.py — PG-visible mission/job/service surfaces. This pack proves the
# attestation+descendants PG surface stays GREEN with the false-completion fix landed.
#
# Scope (glob-grounded):
#   `ls tests/postgres/ | grep -iE 'attest|lca|mission|work_res|work_notifier'`
#   matches exactly ONE file: tests/postgres/test_attestation_live_descendants_pg_lca.py (21 tests).
#   The other 37 tests/postgres/ files cover unrelated subsystems — out of scope, EXCLUDED.
#
# Drift-pin (MANDATORY, FAILs the pack on mismatch):
#   - branch = feature/lca-false-complete-fixes
#   - d5c50994 is an ancestor of HEAD (it IS HEAD)
#   - HEAD ≠ some foreign branch tip (we pin to d5c50994 directly)
#
# Stack:
#   - Disposable PG14 on 127.0.0.1:15432 (verified free; band 10000-19999; 5432 left to
#     foreign ensemble_dev)
#   - DB: ensemble_test owned by role ensemble (initdb bootstrap superuser; -A trust)
#   - NEVER touch 8088 (ensemble self-system), 8079 (prod daemon), 5432 (foreign PG)
#
# Run:  timeout 300 bash test/packs/lcafc_pg_lane_test.sh
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal pytest): `timeout 280 <pytest>` wrapping pytest, plus
#                                   pytest --timeout=240 per-test (innate pytest-timeout)
# Hard cap: 5 minutes per pack execution.
#
# CLEAN SHUTDOWN via EXIT trap: pg_ctl stop + verify port 15432 freed + rm -rf PGDATA.
# No process leaks.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PG_BIN="/opt/homebrew/opt/postgresql@14/bin"

ANCHOR_COMMIT="d5c50994c61ba6c0a762c9dd128d9aa5603eca2a"
ANCHOR_SHORT="d5c50994"
BRANCH_REQUIRED="feature/lca-false-complete-fixes"

PG_PORT=15432
PG_USER="ensemble"
PG_DB="ensemble_test"
PGDATA="/tmp/lcafc-pgdata"
PG_LOG="/tmp/lcafc-pg.log"

PG_RUN_BY_US=0
PACK_RC=0
START_TS=$(date +%s)
INTERNAL_DEADLINE=$((START_TS + 270))   # leave cleanup room before outer timeout 300

# ── helpers ─────────────────────────────────────────────────────────────
ok()    { echo "[PASS] $*"; }
bad()   { echo "[FAIL] $*"; PACK_RC=1; }
note()  { echo "[INFO] $*"; }

time_left() { [ "$(date +%s)" -lt "$INTERNAL_DEADLINE" ]; }

cleanup() {
  local rc=$?
  echo ""
  echo "===== CLEANUP (rc=$rc, pack_rc=$PACK_RC) ====="
  # PG teardown (only if we started it)
  if [ "$PG_RUN_BY_US" = "1" ]; then
    if [ -d "$PGDATA" ]; then
      note "PG teardown: pg_ctl stop on port $PG_PORT"
      "$PG_BIN/pg_ctl" -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
      sleep 1
    fi
    # Verify port freed
    if lsof -nP -i ":$PG_PORT" >/dev/null 2>&1; then
      note "port $PG_PORT still bound — sending SIGKILL to PG processes"
      "$PG_BIN/pg_ctl" -D "$PGDATA" kill -KILL >/dev/null 2>&1 || true
      sleep 1
    fi
    if lsof -nP -i ":$PG_PORT" >/dev/null 2>&1; then
      bad "port $PG_PORT STILL bound after teardown"
    else
      ok "port $PG_PORT freed"
    fi
    # Remove PGDATA
    if [ -d "$PGDATA" ]; then
      note "rm -rf $PGDATA"
      rm -rf "$PGDATA"
      ok "PGDATA removed"
    else
      note "PGDATA already gone"
    fi
    # Best-effort remove pg_log if it landed in /tmp
    [ -f "$PG_LOG" ] && rm -f "$PG_LOG" 2>/dev/null || true
  fi
  echo "===== CLEANUP DONE ====="
}

trap cleanup EXIT

# ── banner ──────────────────────────────────────────────────────────────
echo "=== Test Pack: lcafc_pg_lane ==="
echo "=== worktree: $PROJECT_DIR ==="
echo "=== branch:   $(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null) ==="
echo "=== head:     $(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null) ==="
echo "=== anchor:   $ANCHOR_COMMIT ==="

# ── 1. DRIFT-PIN ────────────────────────────────────────────────────────
echo ""
echo "----- DRIFT-PIN -----"

CURRENT_BRANCH="$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ "$CURRENT_BRANCH" = "$BRANCH_REQUIRED" ]; then
  ok "branch = $BRANCH_REQUIRED"
else
  bad "branch mismatch: expected $BRANCH_REQUIRED, got ${CURRENT_BRANCH:-<none>}"
  echo "RESULT: FAIL (DRIFT)"; exit 1
fi

CURRENT_HEAD="$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null)"
if git -C "$PROJECT_DIR" merge-base --is-ancestor "$ANCHOR_COMMIT" HEAD 2>/dev/null; then
  ok "ancestor: $ANCHOR_SHORT is an ancestor of HEAD ($CURRENT_HEAD)"
else
  bad "ancestor: $ANCHOR_SHORT is NOT an ancestor of HEAD ($CURRENT_HEAD)"
  echo "RESULT: FAIL (DRIFT)"; exit 1
fi

# Production delta must remain EMPTY (daemon/ scripts/ migrations/ untouched since the anchor).
# Test-only commits (test/packs/, .agents/, etc.) on top are allowed and expected.
PROD_DIFF_LINES=$(git -C "$PROJECT_DIR" diff "$ANCHOR_COMMIT" HEAD -- daemon/ scripts/ migrations/ | wc -l | tr -d ' ')
PROD_DIFF_FILES=$(git -C "$PROJECT_DIR" diff "$ANCHOR_COMMIT" HEAD --name-only -- daemon/ scripts/ migrations/ | wc -l | tr -d ' ')
if [ "$PROD_DIFF_LINES" = "0" ] && [ "$PROD_DIFF_FILES" = "0" ]; then
  ok "production delta EMPTY (daemon/ scripts/ migrations/ vs $ANCHOR_SHORT)"
else
  bad "production delta NON-EMPTY: $PROD_DIFF_FILES file(s), $PROD_DIFF_LINES line(s)"
  echo "RESULT: FAIL (DRIFT)"; exit 1
fi

# ── 2. ENVIRONMENT GUARDS ───────────────────────────────────────────────
echo ""
echo "----- ENVIRONMENT -----"

# Tool checks
for t in initdb pg_ctl pg_isready psql createdb createuser; do
  if command -v "$PG_BIN/$t" >/dev/null 2>&1; then
    ok "tool present: $PG_BIN/$t"
  else
    bad "tool missing: $PG_BIN/$t"
    echo "RESULT: FAIL (ENV)"; exit 1
  fi
done

# Port safety: ensure 15432 is free BEFORE we start
if lsof -nP -i ":$PG_PORT" >/dev/null 2>&1; then
  bad "port $PG_PORT ALREADY bound — refusing to start (something else owns it)"
  echo "RESULT: FAIL (PORT)"; exit 1
else
  ok "port $PG_PORT free"
fi

# Forbidden port sanity (just log, never act on them)
for forbidden in 8088 8079 5432; do
  if lsof -nP -i ":$forbidden" >/dev/null 2>&1; then
    note "foreign process on $forbidden — leaving alone (not in our scope)"
  fi
done

# ── 3. BRING-UP DISPOSABLE PG ───────────────────────────────────────────
echo ""
echo "----- BRING-UP PG on $PG_PORT -----"

# Fresh PGDATA — rm -rf if exists
if [ -d "$PGDATA" ]; then
  note "removing stale PGDATA: $PGDATA"
  rm -rf "$PGDATA"
fi
mkdir -p "$PGDATA"

# initdb -A trust, bootstrap superuser = ensemble
"$PG_BIN/initdb" -A trust -U "$PG_USER" "$PGDATA" >/dev/null 2>&1
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  bad "initdb failed — $PGDATA/PG_VERSION missing"
  echo "RESULT: FAIL (PG_INITDB)"; exit 1
fi
ok "initdb complete ($PGDATA, PG_VERSION $(cat "$PGDATA/PG_VERSION"))"

# Start PG bound to 15432 ONLY (NEVER other ports)
"$PG_BIN/pg_ctl" -D "$PGDATA" -o "-p $PG_PORT" -l "$PG_LOG" start >/dev/null 2>&1
PG_RUN_BY_US=1

# Wait for ready (max 20s)
for _ in $(seq 1 40); do
  if "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
  ok "PG ready on 127.0.0.1:$PG_PORT"
else
  bad "PG did not become ready on port $PG_PORT"
  echo "===== pg.log tail ====="
  tail -30 "$PG_LOG" 2>/dev/null || true
  echo "RESULT: FAIL (PG_READY)"; exit 1
fi

# Ensure role ensemble exists (initdb -U creates it as bootstrap superuser; idempotent re-create
# swallows "already exists" so we don't misclassify the run).
"$PG_BIN/createuser" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -s "$PG_USER" 2>/dev/null || true
ok "role ensemble exists (bootstrap superuser)"

# Create the test DB if missing
if ! "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -lqtA 2>/dev/null | grep -q "^${PG_DB}|"; then
  "$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -O "$PG_USER" "$PG_DB" >/dev/null 2>&1
fi
"$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -lqtA 2>/dev/null | grep -q "^${PG_DB}|" \
  && ok "database $PG_DB exists on port $PG_PORT" \
  || { bad "database $PG_DB missing after createdb"; echo "RESULT: FAIL (PG_DB)"; exit 1; }

# ── 4. RUN THE PACK ─────────────────────────────────────────────────────
echo ""
echo "----- RUN: tests/postgres/test_attestation_live_descendants_pg_lca.py (21 tests) -----"

# Snapshot port BEFORE the run, to detect any leak AFTER teardown
PORT_BEFORE_PYTEST_BIND_PIDS=$(lsof -nP -i ":$PG_PORT" 2>/dev/null | tail -n +2 | awk '{print $2}' | sort -u | tr '\n' ' ')
note "PG port $PG_PORT bound by PIDs (pre-pytest): ${PORT_BEFORE_PYTEST_BIND_PIDS:-<none>}"

cd "$PROJECT_DIR"

# Layer-2 timeout: 280s wraps pytest (innate test-pack invariant)
# pytest --timeout=240 = per-test internal layer
# Layer-1 timeout (300s) wraps the whole script via the calling shell command
PYTEST_TIMEOUT_EXIT=0
timeout 280 env \
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
  -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_TEST_PG_URL \
  PG_TEST_HOST=localhost \
  PG_TEST_PORT="$PG_PORT" \
  PG_TEST_DB="$PG_DB" \
  PG_TEST_USER="$PG_USER" \
  PG_TEST_PASSWORD=ensemble_dev \
  uv run python -m pytest \
    tests/postgres/test_attestation_live_descendants_pg_lca.py \
    --override-ini="addopts=" \
    -m postgres -q --tb=short --timeout=240 \
  > /tmp/lcafc-pytest.out 2>&1
PYTEST_RC=$?

if [ "$PYTEST_RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  echo "--- pytest output (tail 60) ---"
  tail -60 /tmp/lcafc-pytest.out 2>/dev/null || true
  PACK_RC=124
  bad "pytest timed out (exit 124, layer-2 280s cap)"
elif [ "$PYTEST_RC" -eq 0 ]; then
  ok "pytest exit 0 (all tests passed)"
  # Capture the passed-count line for the report
  TAIL=$(tail -n 5 /tmp/lcafc-pytest.out | tr -d '\r')
  echo "$TAIL"
else
  echo "RESULT: FAIL"
  echo "--- pytest output (full) ---"
  cat /tmp/lcafc-pytest.out
  PACK_RC=1
  bad "pytest exit $PYTEST_RC"
fi

# Persist the pytest output for the report
cp /tmp/lcafc-pytest.out /tmp/lcafc-pytest.final.out 2>/dev/null || true

END_TS=$(date +%s)
RUNTIME=$((END_TS - START_TS))
note "pack runtime: ${RUNTIME}s"

echo ""
echo "===== SUMMARY ====="
echo "pack_rc: $PACK_RC"
echo "runtime: ${RUNTIME}s"

# Final RESULT line (read by the wrapper) — if PACK_RC is still 0 here, it's PASS
if [ "$PACK_RC" = "0" ]; then
  echo "RESULT: PASS"
else
  # PACK_RC carries 124 (TIMEOUT) or 1 (FAIL) — already echoed above
  :
fi

exit "$PACK_RC"
