#!/usr/bin/env bash
# Test Pack: skill_revival_pg_dialect_unit_test — the LIVE-PG leg for the
# skill-system-revival Phase 1 dialect surfaces.
#
# WHY THIS PACK EXISTS
# --------------------
# The Phase-1 lane's riskiest construct is a PG/SQLite DIALECT SPLIT:
#
#   * the E1 durable re-fix burst guard counts COMMITTED
#     ``skill_evolution`` FIX admissions keyed on the SKILL IDENTITY read
#     out of the job metadata JSON column — ``metadata->>'skill_id'`` on
#     PostgreSQL vs ``json_extract(metadata, '$.skill_id')`` on SQLite
#     (``daemon/repositories/job_queue/repository.py``,
#     ``build_fix_admission_count_statement``);
#   * the E4/A4 lane writes BOTH ``skills.is_active`` AND
#     ``skills.status`` in ONE statement (the sanctioned dual-column
#     writer) and measures divergence with a ``GROUP BY (name,
#     project_id)`` + ``count(DISTINCT content)`` aggregation
#     (``SkillRepository.within_identity_key_divergence``).
#
# Until now EVERY test of those constructs ran on SQLite ONLY.  A split
# that is right on one backend and wrong on the other is a SILENT,
# production-only failure — and the burst guard is a SAFETY guard.
# The pinned PG leg lives in
# ``tests/postgres/test_skill_revival_pg_dialect.py``.
#
# THIS PACK ALSO REPLACES THE ROT ``skill_evolution_pg_test``
# ----------------------------------------------------------
# ``test/packs/skill_evolution_pg_test.sh`` was STRUCTURALLY ROT: it
# invoked ``tests/unit/test_auto_load_skills.py`` — DELETED at
# ``eeef8845a`` (which PREDATES this lane's base ``ae5bbafed``) — so
# under ``set -euo pipefail`` it aborted with rc=4 BEFORE printing any
# ``RESULT:`` line, and the pack's own "don't fail" branch was
# unreachable.  Even repaired, it would have run three SQLite-only files
# with ``-m postgres`` and collected ZERO postgres-marked tests, so it
# never verified PostgreSQL at all.  It is RETIRED (non-failing, with the
# reason printed) and THIS pack is the replacement.
#
# THE DISPOSABLE POSTGRESQL CLUSTER (house PG discipline)
# ---------------------------------------------------------
# The pack provisions its OWN native PostgreSQL cluster (Docker is
# BANNED; no shared/live server is ever contacted):
#
#   1. ``initdb -A trust`` into a ``mktemp -d`` datadir;
#   2. start it on a FREE PRIVATE PORT with ``-k <datadir>`` so it does
#      not touch the system socket dir;
#   3. ``createdb`` the scratch DB OVER TCP (not a /tmp socket);
#   4. point ``PG_TEST_*`` at it and print ``SELECT current_database()``
#      as the PRE-RUN PROOF;
#   5. tear it down (stop the cluster, remove the datadir) on EVERY exit
#      path.
#
# The scratch DB name is the conftest's own allowlisted
# ``ensemble_test`` — the SAME name the house PG packs use — so the
# shared ``tests/postgres/conftest.py`` fence accepts it without any
# allowlist widening.  The fence (not this script) owns the refusal of
# ``ensemble_prod`` / ``ensemble_dev`` / ``ensemble_prod_test``.
#
# WHY THE PORT MATTERS: the ambient environment on this host carries
# ``POSTGRES_HOST=10.44.0.2 POSTGRES_DB=ensemble_prod`` — the LIVE
# host — and a local 5432 also listens (it may hold live-shaped
# databases).  This pack therefore (a) picks a port OUTSIDE the
# well-known ones, (b) asserts the port is FREE before starting, and
# (c) scrubs every ``POSTGRES_*`` variable PER VARIABLE (the
# ``env -u POSTGRES_*`` GLOB form is a SILENT NO-OP — bash does not
# glob env names).
#
# SERIAL BY CONTRACT: the shared PG conftest skips every postgres-marked
# test when ``PYTEST_XDIST_WORKER`` is set (concurrent ``create_all``
# races on shared tables).  This pack unsets it and asserts it is unset.
#
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
set -euo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${SKILL_REVIVAL_PG_BUDGET_S:-240}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
PIN="tests/postgres/test_skill_revival_pg_dialect.py"

echo "=== Test Pack: skill_revival_pg_dialect_unit_test (LIVE PostgreSQL) ==="
echo "REPO_ROOT=$REPO_ROOT"

# ── the per-variable env scrub (the GLOB form is a silent NO-OP) ────────
SCRUB=(env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD
       -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_URL
       -u DATABASE_URL -u PGHOST -u PGPORT -u PGDATABASE
       -u PGUSER -u PGPASSWORD -u PGSSLMODE
       -u PORT -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG)

elapsed_s() { echo $(( ${SECONDS:-0} - PACK_START )); }
check_budget() {
  local elapsed
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (internal timer: wall ${elapsed}s > budget ${BUDGET_S}s ${1:-})"
    exit 124
  fi
}

if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
if [ ! -f "$PIN" ]; then
  echo "RESULT: FAIL (MISSING-ARTIFACT — $PIN)"
  exit 1
fi

# ── locate the PostgreSQL binaries (Docker is BANNED; native only) ────
PG_BINDIR=""
for cand in /usr/lib/postgresql/16/bin /usr/lib/postgresql/15/bin \
            /usr/local/pgsql/bin /opt/homebrew/opt/postgresql@16/bin; do
  if [ -x "$cand/initdb" ] && [ -x "$cand/pg_ctl" ] && [ -x "$cand/createdb" ]; then
    PG_BINDIR="$cand"
    break
  fi
done
if [ -z "$PG_BINDIR" ] && command -v initdb >/dev/null 2>&1; then
  PG_BINDIR="$(dirname "$(command -v initdb)")"
fi
if [ -z "$PG_BINDIR" ]; then
  echo "RESULT: FAIL (MISSING-POSTGRES-BINARIES — no native initdb/pg_ctl/createdb found;"
  echo "        the live-PG leg is NOT VERIFIED on this host)"
  exit 1
fi
export PATH="$PG_BINDIR:$PATH"
echo "PG_BINDIR=$PG_BINDIR"

# ── the disposable cluster ─────────────────────────────────────────────
PGDATA_DIR="$(mktemp -d /tmp/skill-revival-pg-XXXXXX)"
PG_PORT="${SKILL_REVIVAL_PG_PORT:-54333}"
SCRATCH_DB="ensemble_test"
SCRATCH_USER="ensemble"
SCRATCH_PASSWORD="ensemble_dev"

cluster_cleanup() {
  if [ -n "${PGDATA_DIR:-}" ] && [ -d "$PGDATA_DIR/data" ]; then
    "$PG_BINDIR/pg_ctl" -D "$PGDATA_DIR/data" -m immediate stop >/dev/null 2>&1 || true
  fi
  if [ -n "${PGDATA_DIR:-}" ]; then
    rm -rf "$PGDATA_DIR"
  fi
}
trap cluster_cleanup EXIT INT TERM

# The port MUST be free — never adopt (or clobber) a server that is
# already listening there.
if "$PG_BINDIR/pg_isready" -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
  echo "RESULT: FAIL (PORT-BUSY — ${PG_PORT} is already serving; refusing to touch it)"
  exit 1
fi

echo "--- setup: initdb -A trust into a mktemp datadir ---"
"$PG_BINDIR/initdb" -A trust -U "$SCRATCH_USER" "$PGDATA_DIR/data" >"$PGDATA_DIR/initdb.log" 2>&1 || {
  echo "RESULT: FAIL (INITDB — see $PGDATA_DIR/initdb.log)"
  tail -20 "$PGDATA_DIR/initdb.log" || true
  exit 1
}

echo "--- setup: pg_ctl start on 127.0.0.1:${PG_PORT} (private socket dir) ---"
"$PG_BINDIR/pg_ctl" -D "$PGDATA_DIR/data" \
  -o "-p ${PG_PORT} -k ${PGDATA_DIR} -c listen_addresses=127.0.0.1" \
  -l "$PGDATA_DIR/pg.log" start >"$PGDATA_DIR/pgctl.log" 2>&1 || {
  echo "RESULT: FAIL (PG_CTL-START — see $PGDATA_DIR/pg.log)"
  tail -20 "$PGDATA_DIR/pg.log" 2>/dev/null || true
  exit 1
}

pg_ready=0
for _ in $(seq 1 30); do
  if "$PG_BINDIR/pg_isready" -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
    pg_ready=1
    break
  fi
  sleep 1
done
if [ "$pg_ready" -ne 1 ]; then
  echo "RESULT: FAIL (PG-NOT-READY on 127.0.0.1:${PG_PORT} — see $PGDATA_DIR/pg.log)"
  tail -20 "$PGDATA_DIR/pg.log" 2>/dev/null || true
  exit 1
fi

echo "--- setup: createdb over TCP (not a /tmp socket) ---"
"$PG_BINDIR/createdb" -h 127.0.0.1 -p "$PG_PORT" -U "$SCRATCH_USER" "$SCRATCH_DB" || {
  echo "RESULT: FAIL (CREATEDB over TCP)"
  exit 1
}

# ── the belt: the scratch DB name must be the allowlisted one ─────────
if [ "$SCRATCH_DB" != "ensemble_test" ]; then
  echo "RESULT: FAIL (SCRATCH-NAME — '$SCRATCH_DB' is not the allowlisted scratch DB)"
  exit 1
fi

export PG_TEST_HOST=127.0.0.1
export PG_TEST_PORT="$PG_PORT"
export PG_TEST_DB="$SCRATCH_DB"
export PG_TEST_USER="$SCRATCH_USER"
export PG_TEST_PASSWORD="$SCRATCH_PASSWORD"

# ── the PRE-RUN PROOF: which database are we ACTUALLY on? ────────────
echo "--- pre-run proof: SELECT current_database() ---"
CURRENT_DB="$("${SCRUB[@]}" "$PG_BINDIR/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$SCRATCH_USER" -d "$SCRATCH_DB" -tAc 'SELECT current_database();' 2>/dev/null || echo '<query failed>')"
echo "current_database() = ${CURRENT_DB}"
if [ "$CURRENT_DB" != "$SCRATCH_DB" ]; then
  echo "RESULT: FAIL (PRE-RUN-PROOF — expected '${SCRATCH_DB}', got '${CURRENT_DB}')"
  exit 1
fi
case "$CURRENT_DB" in
  ensemble_prod|ensemble_dev|ensemble_prod_test)
    echo "RESULT: FAIL (LIVE-DB-REFUSAL — '${CURRENT_DB}' is a live database)"
    exit 1
    ;;
esac

# ── xdist guard: PG legs are SERIAL by contract ──────────────────────
unset PYTEST_XDIST_WORKER || true
if [ -n "${PYTEST_XDIST_WORKER:-}" ]; then
  echo "RESULT: FAIL (XDISST — PYTEST_XDIST_WORKER must be unset for a PG leg)"
  exit 1
fi

# ── the leg ───────────────────────────────────────────────────────────
LOG_DIR="${SKILL_REVIVAL_PG_LOG_DIR:-/tmp/skill-revival-pg-gate}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/pg_dialect.log"

echo "--- run: pytest ${PIN} (serial, -m postgres) ---"
PYTEST_RC=0
"${SCRUB[@]}" "$PY" -m pytest "$PIN" \
  --override-ini="addopts=" -m postgres --timeout=120 \
  -q --tb=short -p no:cacheprovider > "$LOG" 2>&1 || PYTEST_RC=$?

tail -12 "$LOG" | sed 's/^/  /'
# pytest's `-q` short summary INDENTS the FAILED lines — the house
# classifier is `grep '^FAILED'`, so strip the leading whitespace first.
grep -E "^[[:space:]]*FAILED " "$LOG" | sed 's/^[[:space:]]*/  /' || true

# The exit code is read FIRST so a RED leg is reported as RED (with its
# FAILED list) rather than as a collection problem.
if [ "$PYTEST_RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT (pytest rc=124)"
  exit 124
fi
if [ "$PYTEST_RC" -eq 5 ]; then
  echo "RESULT: FAIL (NO-COLLECTION — pytest matched no runnable test;"
  echo "        the live-PG leg did NOT execute)"
  exit 1
fi
if [ "$PYTEST_RC" -ne 0 ]; then
  echo "RESULT: FAIL (pytest rc=${PYTEST_RC} — the FAILED list is above)"
  exit 1
fi

# The VACUOUS-GREEN guard: a green rc=0 whose summary carries no
# "N passed" with N>=1 did not actually run the leg.
if ! grep -qE "^[1-9][0-9]* passed" "$LOG"; then
  echo "RESULT: FAIL (VACUOUS — the summary does not carry 'N passed' with N >= 1;"
  echo "        the live-PG leg did NOT execute)"
  exit 1
fi

check_budget "after the leg"
echo "RESULT: PASS (live-PG dialect leg; $(elapsed_s)s; scratch cluster torn down)"
exit 0
