#!/usr/bin/env bash
# Test Pack: lca3_pg_attestation_integration_test — LCA attestation live-descendants
# dialect canary under REAL PostgreSQL.
#
# Purpose: PG-mode confirmation for the LCA Stage 3 attestation suite
# (tests/postgres/test_attestation_live_descendants_pg_lca.py — 21 tests).
# This is the LCA resolver Stage 3 final merge gate PG lane: every test in
# the attestation set must pass under a real PG engine after the Stage 3
# R1-R8 retirement / single-path resolver endstate, so the dialect canary
# contract is honored on the merge path.
#
# Covering file (exactly 1):
#   1. tests/postgres/test_attestation_live_descendants_pg_lca.py  (21 tests)
#                                                            ----
#                                                            21 tests total
#
# Branch:        feature/lca-resolver-stage3  @  8a7b5272  (FROZEN — verify,
#                never rebase under a running gate)
# Parent recipe: test/packs/lca2_pg_attestation_integration_test.sh (Stage 2
#                final gate, proven disposable-PG recipe — this pack is a
#                stage3 adaptation, not a re-derivation)
# Engine focus:  daemon/manager.py (live-descendants resolver, Stage 3
#                single-path endstate after R1-R8 retirement)
# Env vars:      PG_TEST_HOST/PORT/DB/USER/PASSWORD (conftest.py:68-74 builds
#                PG_URL from these — NOT ENSEMBLE_TEST_PG_URL; verified by
#                reading conftest.py, do not assume)
#
# Disposable PG14 recipe (house blueprint (f) in core architecture):
#   initdb -A trust on /tmp/lca3-pgdata
#   pg_ctl start on port 15433 (verified FREE at dispatch; fallback band
#             15435-15439 per gate spec; never touches prod/dev ports
#             8079/8088/9797 or the foreign PG on 15432)
#   createdb ensemble_test_stage3
#   expose PG_TEST_HOST/PORT/DB/USER/PASSWORD → disposable PG
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): subprocess `timeout 240s pytest ...`
# Hard cap: 5 minutes per pack execution (skill rule).
#
# Pre-existing known foreign defect (NOT ours to fix):
#   migration 20260915_120000 (critical-notes) uses PG-invalid
#   `BOOLEAN NOT NULL DEFAULT 0`. If it manifests here, verify it identical
#   at the Stage 2 gate and document as pre-existing (do NOT fix).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Disposable-PG knobs — namespaced under /tmp so they're obvious.
PGDATA="/tmp/lca3-pgdata"
PG_PORT=15433
PG_DB="ensemble_test_stage3"
PG_USER="ensemble"
PG_LOG="/tmp/lca3-pgdata.log"

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

echo "=== Test Pack: lca3_pg_attestation_integration_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo ""

# xdist guard — conftest auto-skips postgres tests if this is set.
unset PYTEST_XDIST_WORKER || true

# Belt and suspenders: scrub prod-related env vars so daemon-side modules
# that auto-connect on import (persistence.py env-detect) cannot bleed
# into the disposable PG or into prod. SSL vars scrubbed per house recipe.
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL || true
unset SSL_CERT_FILE SSL_CERT_DIR || true

# REDIRECT conftest's PG_* away from any shared DB and toward our
# disposable PG. The conftest builds its URL from
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

if pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
    echo "[setup] PG already listening on port $PG_PORT — reusing"
    _REUSED=1
else
    # Port free but a stale data dir from a prior botched run may remain
    # (initdb refuses a non-empty target). Our /tmp namespace, ours to scrub.
    if [ -d "$PGDATA" ]; then
        echo "[cleanup] stale $PGDATA detected (port $PG_PORT free), removing before initdb"
        rm -rf "$PGDATA" "$PG_LOG"
    fi
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
