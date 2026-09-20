#!/usr/bin/env bash
# LCA stale-A (B1) merge gate — PG lane (attestation live-descendants under
# REAL PostgreSQL on a DISPOSABLE cluster).
# Pack: lca_stale_a_pg_lane_test
# Worktree: feature/lca-stale-a-fix @ e0d15e93 (base a6442bff lineage).
# Covering files (globbed at runtime — currently exactly 1):
#   tests/postgres/test_attestation*.py  (today: test_attestation_live_
#   descendants_pg_lca.py; any future attestation-named PG file joins)
# Disposable PG14 recipe:
#   lsof guard: port 15433 MUST be free — fail fast if occupied (NEVER picks
#             another port silently; port 15432 is a FOREIGN postgres — never
#             touched; 8079/8088 untouchable).
#   initdb -A trust -U postgres into a mktemp -d scratch dir under /tmp
#   pg_ctl start -o "-p 15433"
#   createdb ensemble_test; CREATE ROLE ensemble (conftest default) +
#   GRANT CREATE ON SCHEMA public TO ensemble (PG14 public schema still
#   grants CREATE to PUBLIC by default; the grant is explicit per lane spec)
#   export ALL FIVE PG_TEST_HOST/PORT/DB/USER/PASSWORD (tests/postgres/
#   conftest.py:68-74 builds PG_URL from exactly these).
# Teardown (trap): pg_ctl stop -m fast, remove scratch dir, verify 15433
# freed. No kill of any foreign process, ever.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard
# around the pytest leg.
# Invocation contract: timeout 300 bash test/packs/lca_stale_a_pg_lane_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca_stale_a_pg_lane_test"
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@14/bin}"
PORT=15433
PG_DB="ensemble_test"
ROLE_NAME="ensemble"
ROLE_PASSWORD="ensemble_dev"
SCRATCH="$(mktemp -d /tmp/lca-stale-a-pg.XXXXXX)"
PG_LOG="${SCRATCH}/pg.log"
RC=1

echo "=== Test Pack: ${PACK} ==="
echo "Drift pin: $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
echo "Scratch PGDATA: ${SCRATCH}"

# ── Teardown trap: stop PG, remove scratch, verify port freed ─────────
teardown() {
  local rc=$?
  echo ""
  echo "=== Teardown ==="
  if [ -f "${SCRATCH}/postmaster.pid" ] || pg_isready -h 127.0.0.1 -p "$PORT" >/dev/null 2>&1; then
    "${PG_BIN}/pg_ctl" -D "$SCRATCH" stop -m fast >/dev/null 2>&1 || true
  fi
  rm -rf "$SCRATCH"
  if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[teardown][warn] port ${PORT} STILL LISTENING after stop — investigate manually (no kill performed)"
  else
    echo "[teardown] port ${PORT} verified freed; scratch dir removed"
  fi
  return $rc
}
trap teardown EXIT INT TERM

# ── Preflight: binaries + port guard (fail fast, NO silent re-port) ───
for bin in initdb pg_ctl createdb psql pg_isready; do
  if [ ! -x "${PG_BIN}/${bin}" ]; then
    echo "[fatal] ${PG_BIN}/${bin} not found — set PG_BIN to a PG14 bin dir"; exit 1
  fi
done
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[fatal] port ${PORT} is OCCUPIED — refusing to start (15432 is a"
  echo "        foreign postgres and must never be touched; no alternate port)"
  lsof -iTCP:"$PORT" -sTCP:LISTEN | head -3
  exit 1
fi
echo "[preflight] port ${PORT} free; PG14 binaries at ${PG_BIN}"

# Scrub prod-bleed + xdist env (mirrors lca2_pg_attestation lane discipline).
unset PYTEST_XDIST_WORKER || true
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL || true
unset SSL_CERT_FILE SSL_CERT_DIR || true

# ── Disposable cluster bring-up ────────────────────────────────────────
echo ""
echo "=== PG Setup (disposable, port ${PORT}) ==="
"${PG_BIN}/initdb" -A trust -U postgres -E UTF8 -D "$SCRATCH" >/dev/null || { echo "[fatal] initdb failed"; exit 1; }
"${PG_BIN}/pg_ctl" -D "$SCRATCH" -o "-p $PORT" -l "$PG_LOG" start -w >/dev/null || { echo "[fatal] pg_ctl start failed"; tail -20 "$PG_LOG"; exit 1; }
for _i in $(seq 1 20); do
  "${PG_BIN}/pg_isready" -h 127.0.0.1 -p "$PORT" >/dev/null 2>&1 && break
  sleep 1
done
if ! "${PG_BIN}/pg_isready" -h 127.0.0.1 -p "$PORT" >/dev/null 2>&1; then
  echo "[fatal] PG not ready on ${PORT} within 20s"; tail -25 "$PG_LOG"; exit 1
fi
"${PG_BIN}/createdb" -h 127.0.0.1 -p "$PORT" -U postgres "$PG_DB" || { echo "[fatal] createdb failed"; exit 1; }
"${PG_BIN}/psql" -h 127.0.0.1 -p "$PORT" -U postgres -d "$PG_DB" -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${ROLE_NAME}') THEN
    CREATE ROLE ${ROLE_NAME} LOGIN PASSWORD '${ROLE_PASSWORD}';
  END IF;
END \$\$;
GRANT CREATE ON SCHEMA public TO ${ROLE_NAME};
SQL
[ $? -eq 0 ] || { echo "[fatal] role/grant setup failed"; exit 1; }
echo "[setup] cluster up on ${PORT}; db=${PG_DB}; role=${ROLE_NAME} (matches conftest defaults)"

# ── Conftest-facing env (ALL FIVE) ────────────────────────────────────
export PG_TEST_HOST=127.0.0.1
export PG_TEST_PORT="$PORT"
export PG_TEST_DB="$PG_DB"
export PG_TEST_USER="$ROLE_NAME"
export PG_TEST_PASSWORD="$ROLE_PASSWORD"
echo "[env] PG_TEST_HOST=$PG_TEST_HOST PG_TEST_PORT=$PG_TEST_PORT PG_TEST_DB=$PG_TEST_DB PG_TEST_USER=$PG_TEST_USER PG_TEST_PASSWORD=***"

# ── Attestation-named PG files (glob — include any future siblings) ───
FILES=(tests/postgres/test_attestation*.py)
if [ ! -e "${FILES[0]}" ]; then
  echo "[fatal] no tests/postgres/test_attestation*.py found"; exit 1
fi
echo "Files: ${FILES[*]}"

echo ""
echo "=== Pytest (inner 280s cap) ==="
START=$(date +%s)
timeout 280 uv run python -m pytest "${FILES[@]}" \
  --override-ini="addopts=" -m postgres --tb=short -q
RC=$?
END=$(date +%s)
echo "Pytest runtime: $((END-START))s"

echo ""
if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
elif [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
else
  echo "RESULT: FAIL (exit=$RC)"
fi
exit $RC
