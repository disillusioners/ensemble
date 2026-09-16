#!/usr/bin/env bash
# Test Pack: lca2_pg_attestation_integration_test — LCA attestation live-descendants
# dialect canary under REAL PostgreSQL.
#
# Purpose: PG-mode confirmation for the LCA Stage 2 attestation suite
# (tests/postgres/test_attestation_live_descendants_pg_lca.py — 21 tests).
# This is the LCA resolver Stage 2 final merge gate (Job 6 of 9): every test
# in the attestation set must pass under a real PG engine so the dialect
# canary contract is honored on the merge path.
#
# Covering file (exactly 1):
#   1. tests/postgres/test_attestation_live_descendants_pg_lca.py  (21 tests)
#                                                            ----
#                                                            21 tests total
#
# Branch:        feature/lca-resolver-stage2  @  f926de24
# Engine focus:  daemon/manager.py  (count_live_descendants facade +
#                 _dormant_descendants_with_work_en_route helper)
# Migration:     d950d2c8 (incident b08f40fe amendment: idle-orphan descendants
#                 are NOT live; two-set live facade introduced)
# Env vars:      PG_TEST_HOST/PORT/DB/USER/PASSWORD (conftest.py:68-74 builds
#                 PG_URL from these — NOT ENSEMBLE_TEST_PG_URL; per task spec
#                 "verify, do not assume" we read the conftest to confirm
#                 the exact env var name honored)
#
# Disposable PG14 recipe (house blueprint (f) in core architecture):
#   initdb -A trust on /tmp/lca2-pgdata
#   pg_ctl start on port 15433 (15432 occupied by ladder PG; 15433 chosen
#             from the disposable 15432-15439 band; never touches prod/dev
#             ports 8079/8088/9797)
#   createdb ensemble_test_stage2
#   expose PG_TEST_HOST/PORT/DB/USER/PASSWORD → disposable PG
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): subprocess `timeout 240s pytest ...`
# Hard cap: 5 minutes per pack execution (skill rule).
#
# Pre-existing known foreign defect (NOT ours to fix):
#   migration 20260915_120000 (critical-notes) uses PG-invalid
#   `BOOLEAN NOT NULL DEFAULT 0`. If it manifests here, verify it identical at
#   base 0ea60d91 (disposable base worktree leg) and document as pre-existing.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Disposable-PG knobs — namespaced under /tmp so they're obvious.
PGDATA="/tmp/lca2-pgdata"
PG_PORT=15433
PG_DB="ensemble_test_stage2"
PG_USER="ensemble"
PG_LOG="/tmp/lca2-pgdata.log"

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

echo "=== Test Pack: lca2_pg_attestation_integration_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo ""

# xdist guard — conftest auto-skips postgres tests if this is set.
unset PYTEST_XDIST_WORKER || true

# Belt and suspenders: scrub prod-related env vars so daemon-side modules
# that auto-connect on import (persistence.py env-detect) cannot bleed
# into the disposable PG or into prod. SSL vars scrubbed per task spec.
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL || true
unset SSL_CERT_FILE SSL_CERT_DIR || true

# REDIRECT conftest's PG_* away from the shared ensemble_test DB (where
# role 'ensemble' lacks CREATE on schema public per blueprint (m)) and
# toward our disposable PG. The conftest builds its URL from
# PG_TEST_HOST/PORT/DB/USER/PASSWORD (conftest.py:68-74). Password is unused
# because -A trust is set on the disposable cluster, but the URL shape
# requires a password slot.
export PG_TEST_HOST=127.0.0.1
export PG_TEST_PORT=$PG_PORT
export PG_TEST_DB=$PG_DB
export PG_TEST_USER=$PG_USER
export PG_TEST_PASSWORD=ensemble_dev

# === PG SETUP ============================================================
echo ""
echo "=== PG Setup ==="

# Stale data dir from a prior botched run? scrub first.
if [ ! -d "$PGDATA" ] && [ -f "/tmp/lca2-pgdata/postmaster.pid" ]; then
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

echo "[setup] PG_TEST_URL=postgresql://${PG_USER}@127.0.0.1:${PG_PORT}/${PG_DB}"

# Sanity: confirm the env vars are visible to the same python pytest will use.
echo "[verify] env probe: $(uv run python -c 'import os; print(os.environ.get("PG_TEST_PORT"))' 2>/dev/null || echo '<probe failed>')"

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
    tests/postgres/test_attestation_live_descendants_pg_lca.py \
    --override-ini="addopts=" -m postgres --tb=short -q \
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
