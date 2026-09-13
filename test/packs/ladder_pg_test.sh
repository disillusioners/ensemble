#!/usr/bin/env bash
# Test Pack: ladder_pg_test — durable loop-breaker rung under REAL PostgreSQL
#
# Purpose: PG-mode durability confirmation for the hallucination-recovery
# ladder Phase 1. Mirrors the SQLite-side canary
# (tests/unit/test_symptom_repair_mid_superstep_canary.py) on a real
# AsyncPostgresSaver checkpoint store to catch schema / engine clauses
# that SQLite hides (bytea / jsonb round-trip, PG-specific conditional
# reservation, etc.).
#
# Covering file (exactly 1):
#   1. tests/postgres/test_symptom_repair_ladder_pg.py  (1 test)
#                                                   ----
#                                                   1 test total
#
# Branch:        feature/hallucination-recovery-ladder
# Engine:        daemon/services/symptom_repair_engine.py
# Env var:       LADDER_PG_CONNINFO  (set by THIS file; the test file reads
#                 it directly — NOT conftest's PG_TEST_* family which is
#                 for other tests/postgres/ suites)
# Skip rules:    (a) if LADDER_PG_CONNINFO empty → test raises pytest.skip
#                    with explicit message ("NOT a pass, an explicit skip");
#                 (b) conftest skips all -m postgres tests if PYTEST_XDIST_WORKER
#                    is set (schema contention under worker parallelism).
#
# Disposable PG14 recipe (house blueprint (f) in core architecture):
#   initdb -A trust on /tmp/ladder-pg14-pgdata
#   pg_ctl start on port 15432 (>10000, non-prod — never touches ports <10000
#            and never touches port 8088)
#   createdb ensemble_rl_p1
#   expose LADDER_PG_CONNINFO=postgresql://ensemble@127.0.0.1:15432/ensemble_rl_p1
#   NOTE: must be libpq-style `postgresql://` (NOT `postgresql+asyncpg://`) —
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): subprocess `timeout 240s pytest ...`
# Hard cap: 5 minutes per pack execution (skill rule).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Disposable-PG knobs — namespaced under /tmp so they're obvious.
PGDATA="/tmp/ladder-pg14-pgdata"
PG_PORT=15432
PG_DB="ensemble_rl_p1"
PG_USER="ensemble"
PG_LOG="/tmp/ladder-pg14-pgdata.log"
PG_CONNINFO="postgresql://${PG_USER}@127.0.0.1:${PG_PORT}/${PG_DB}"

# Tracks whether PG was started by THIS run (controls teardown).
_REUSED=0

# Best-effort PG teardown — runs on any exit (normal, signal, error).
# Respects _REUSED so we don't kill a pre-existing cluster.
cleanup_pg() {
    local rc=$?
    if [ "$_REUSED" -eq 0 ] && [ -d "$PGDATA" ]; then
        echo "" >&2
        echo "[teardown] stopping PG + removing $PGDATA (rc=$rc)" >&2
        pg_ctl -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
        rm -rf "$PGDATA" "$PG_LOG" >/dev/null 2>&1 || true
    fi
    return $rc
}
trap cleanup_pg EXIT INT TERM

cd "$PROJECT_DIR"

echo "=== Test Pack: ladder_pg_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo ""

# xdist guard — conftest auto-skips postgres tests if this is set.
unset PYTEST_XDIST_WORKER || true

# Belt and suspenders: scrub prod-related env vars so daemon-side modules
# that auto-connect on import (persistence.py env-detect) cannot bleed
# into the disposable PG or into prod.
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL || true

# REDIRECT conftest's PG_* away from the shared ensemble_test DB (where
# role 'ensemble' lacks CREATE on schema public per blueprint (m)) and
# toward our disposable PG. The conftest builds its URL from
# PG_TEST_HOST/PORT/DB/USER/PASSWORD (conftest.py:68-74).
# Password is unused because -A trust is set on the disposable cluster,
# but the URL shape requires a password slot.
export PG_TEST_HOST=127.0.0.1
export PG_TEST_PORT=$PG_PORT
export PG_TEST_DB=$PG_DB
export PG_TEST_USER=$PG_USER
export PG_TEST_PASSWORD=ensemble_dev

# === PG SETUP ============================================================
echo ""
echo "=== PG Setup ==="

# Stale data dir from a prior botched run? scrub first.
if [ ! -d "$PGDATA" ] && [ -f "/tmp/ladder-pg14-pgdata/postmaster.pid" ]; then
    echo "[cleanup] stale $PGDATA detected, removing before initdb"
    rm -rf "$PGDATA" "$PG_LOG"
fi

if pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
    echo "[setup] PG already listening on port $PG_PORT — reusing"
    _REUSED=1
else
    echo "[setup] initdb -A trust -U $PG_USER $PGDATA"
    initdb -A trust -U "$PG_USER" "$PGDATA" >/dev/null
    echo "[setup] pg_ctl start -o \"-p $PG_PORT\" -l $PG_LOG"
    pg_ctl -D "$PGDATA" -o "-p $PG_PORT" -l "$PG_LOG" start

    for _i in $(seq 1 20); do
        if pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then break; fi
        sleep 1
    done
    if ! pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
        echo "[fatal] PG did not become ready on port $PG_PORT within 20s"
        if [ -f "$PG_LOG" ]; then tail -25 "$PG_LOG"; fi
        exit 1
    fi
    _REUSED=0
fi

# Ensure the DB exists (idempotent — second pack run sees it already).
if ! psql -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -lqtA 2>/dev/null \
       | grep -q "^${PG_DB}|"; then
    echo "[setup] createdb -h 127.0.0.1 -p $PG_PORT -U $PG_USER $PG_DB"
    createdb -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" "$PG_DB"
fi

export LADDER_PG_CONNINFO="$PG_CONNINFO"
echo "[setup] LADDER_PG_CONNINFO=$LADDER_PG_CONNINFO"

# Sanity: confirm the env var is visible to the same python pytest will use.
echo "[verify] env probe: $(uv run python -c 'import os; print(repr(os.environ.get("LADDER_PG_CONNINFO"))[:90])' 2>/dev/null || echo '<probe failed>')"

# === DRIFT-PIN ===========================================================
echo ""
echo "[drift] head: $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
if ! git diff --cached --quiet; then
    echo "[warn] staged index dirty — listing diff for forensics:"
    git diff --cached --stat
fi

# === PYTEST (single invocation, 240s inner timeout) =====================
echo ""
echo "=== Pytest (Layer-2: 240s internal) ==="

PYTEST_RC=0
timeout 240s uv run python -m pytest \
    tests/postgres/test_symptom_repair_ladder_pg.py \
    --override-ini="addopts=" -m postgres -v --tb=short \
    || PYTEST_RC=$?

# === REPORT ==============================================================
echo ""
echo "=== Report ==="
case "$PYTEST_RC" in
    0)   echo "RESULT: PASS" ;;
    124) echo "RESULT: TIMEOUT" ;;
    *)   echo "RESULT: FAIL (exit=$PYTEST_RC)" ;;
esac

exit $PYTEST_RC
