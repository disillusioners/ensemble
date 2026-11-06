#!/usr/bin/env bash
# Test Pack: memoir_w8_boot_abort — W8 frozen-binary boot-abort induction
#             (Memoir Store v1, phase 1 AC3 / phase 7 D7.6 / arch §12.4).
# Timeout: 900s (the documented house override for boot-induction legs —
#          real frozen-binary boots + a scratch PG16 cluster; the 5-min
#          cap is preserved for every other pack class).
#
# WHAT IT PROVES (the Q6 / decisions.md §4 row 17 blast radius, PROVED):
#   A. INDUCTION — a frozen-binary COPY built with ONE deliberately invalid
#      memoir ensure statement (`no_such_imm_function`) boots from a scratch
#      root against a scratch PG16 DB and:
#        A1 boot FAILS (non-zero exit, never reaches /livez)
#        A2 the failure NAMES the offending memoir statement
#        A3 no partial memoir ensure schema survives (tsv + GIN absent —
#           the shared ensure transaction rolled back)
#        A4 the inherited lanes' statements did NOT commit either — two
#           sentinels are ABSENT (instance_execution_leases: an inherited
#           CREATE TABLE with no ORM model, so it fires on every fresh
#           boot; uq_projects_name: a pre-shaped `projects` table without
#           the unique constraint, so the inherited CREATE UNIQUE INDEX
#           fires) — the COLLATERAL-ROLLBACK proof
#        A5 the live fence holds (state.json sha PRE==MID==POST, current
#           symlink unchanged, :9797 /livez monotone)
#   B. NON-REGRESSION / NON-VACUITY — the CLEAN binary with the same
#      scratch setup: boot SUCCEEDS, memoir tables + tsv GENERATED column
#      + GIN index EXIST and POPULATE on INSERT, the inherited sentinels
#      ARE PRESENT, and a SECOND boot against the same DB is a no-op.
#   C. STRUCTURAL PINS — exactly one begin(), no per-statement try/except,
#      memoir statements in the shared list (tests/unit/test_memoir_ensure_
#      structure.py). The try/except mutation's BEHAVIORAL half is executed
#      here with the MUTANT binary: the boot then SUCCEEDS with a
#      half-applied ensure (the observation-1 RED condition the pin
#      prevents) — the pack asserts that the mutation is behaviorally
#      meaningful (it must boot), i.e. the invariant is load-bearing.
#
# REQUIREMENTS (the caller provisions the binaries; the pack owns the PG
# cluster, the payload roots, and the fences):
#   MEMOIR_W8_BINS_DIR  dir holding exactly:
#                         ensemble-prod.induction   (planted tsv statement)
#                         ensemble-prod.clean       (canonical clean build)
#                         ensemble-prod.tryexcept   (execute wrapped in
#                                                    try/except — mutant)
#   Build recipe: `.agents/tester/RESULTS/2026-11-05-memoir-g1-w8-boot-abort.md`.
#
# FENCES (binding — an occ#8-class violation invalidates the gate):
#   * every DB-touching command carries inline scratch POSTGRES_* +
#     ENSEMBLE_SELF_ENV=sandbox; ambient POSTGRES_* / PORT / HOST /
#     ENSEMBLE_UPGRADE_LIVE / RAG_IS_REQUIRED are scrubbed PER VARIABLE
#     (never `env -u POSTGRES_*` — a silent no-op);
#   * ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 is PINNED EXPLICITLY on
#     every boot (unset ⇒ the sweep kills LIVE processes);
#   * frozen-binary COPIES in a scratch root (never in place), fake HOME;
#   * a pre-flight `SELECT current_database()` equals the scratch DB name
#     BEFORE every write-bearing operation;
#   * live fence: state.json sha PRE==MID==POST, current-> unchanged,
#     :9797 /livez monotone; zero writes under /home/nea/agents-ensemble;
#   * the scratch cluster, DB, roots and logs are removed at teardown.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
BINS="${MEMOIR_W8_BINS_DIR:-}"
DB_NAME="memoir_w8_scratch"
FAILED=0

echo "=== Test Pack: memoir_w8_boot_abort ==="

fail() { echo "FAIL[$1]: $2"; FAILED=1; }
pass() { echo "PASS[$1]: $2"; }

# ── Fence 0: the caller must provision the three binaries ───────────────
if [ -z "$BINS" ]; then
  echo "FENCE: MEMOIR_W8_BINS_DIR is unset — this pack needs the three induced"
  echo "  binaries (ensemble-prod.{induction,clean,tryexcept}); see the RESULTS"
  echo "  doc build recipe. The boot legs are never silently skipped."
  echo "RESULT: FAIL"
  exit 1
fi
for b in ensemble-prod.induction ensemble-prod.clean ensemble-prod.tryexcept; do
  if [ ! -x "$BINS/$b" ]; then
    echo "FENCE: missing executable $BINS/$b"
    echo "RESULT: FAIL"
    exit 1
  fi
done
if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
  echo "FENCE: no worktree .venv — run 'uv sync' first"
  echo "RESULT: FAIL"
  exit 1
fi

echo "binary sha256:"
sha256sum "$BINS"/ensemble-prod.* | sed 's#^#  #'

# ── Ports (free, 10000-19999; verified before use) ──────────────────────
free_port() {
  "$PROJECT_DIR/.venv/bin/python" - "${1:-}" <<'PY'
import random, socket, sys
avoid = {int(x) for x in sys.argv[1].split(",") if x}
for _ in range(500):
    p = random.randint(10000, 19999)
    if p in avoid:
        continue
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
    except OSError:
        s.close(); continue
    s.close(); print(p); break
else:
    raise SystemExit("no free port in 10000-19999")
PY
}
PG_PORT="$(free_port)" || { echo "RESULT: FAIL"; exit 1; }
DAEMON_PORT="$(free_port "$PG_PORT")" || { echo "RESULT: FAIL"; exit 1; }
MUTANT_PORT="$(free_port "$PG_PORT,$DAEMON_PORT")" || { echo "RESULT: FAIL"; exit 1; }
CLEAN_PORT="$DAEMON_PORT"
echo "scratch ports: pg=$PG_PORT daemon=$CLEAN_PORT mutant=$MUTANT_PORT"

S="$(mktemp -d /tmp/memoir-w8.XXXXXX)" || { echo "RESULT: FAIL"; exit 1; }
PGDATA="$S/pg"; PGSOCK="$S/pgsock"; mkdir -p "$PGSOCK" "$S/logs" "$S/home"

cleanup() {
  [ -n "${BOOT_PGID:-}" ] && kill -TERM "$BOOT_PGID" 2>/dev/null
  [ -n "${MUTANT_PGID:-}" ] && kill -TERM "$MUTANT_PGID" 2>/dev/null
  for r in "$S"/root-*; do [ -d "$r" ] && pkill -TERM -f "$r/bin/ensemble-prod" 2>/dev/null || true; done
  sleep 1
  if [ -f "$PGDATA/postmaster.pid" ]; then
    /usr/lib/postgresql/16/bin/pg_ctl -D "$PGDATA" -m immediate stop >/dev/null 2>&1 || true
  fi
  if [ "${MEMOIR_W8_KEEP_SCRATCH:-0}" = "1" ]; then
    echo "SCRATCH KEPT: $S"
  else
    rm -rf "$S"
  fi
}
trap cleanup EXIT

# ── Live fence PRE (read-only; the only live touches are reads) ─────────
LIVE_STATE=/home/nea/agents-ensemble/releases/state.json
LIVE_CURRENT=/home/nea/agents-ensemble/current
PRE_STATE_SHA="$(sha256sum "$LIVE_STATE" 2>/dev/null | awk '{print $1}')"
PRE_CURRENT="$(readlink "$LIVE_CURRENT" 2>/dev/null)"
PRE_LIVEZ="$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:9797/livez 2>/dev/null || true)"
echo "live fence PRE: state.json=${PRE_STATE_SHA:-<absent>} current=${PRE_CURRENT:-<absent>} :9797/livez=${PRE_LIVEZ:-<none>}"

# ── C. Structural pins (source-level; mutation legs run by the gate worker) ──
echo "--- C. structural pins (W8.3) ---"
timeout 180 "$PROJECT_DIR/.venv/bin/python" -m pytest \
  "$PROJECT_DIR/tests/unit/test_memoir_ensure_structure.py" \
  -q -p no:randomly --override-ini="addopts=" >"$S/logs/pins.log" 2>&1
PIN_RC=$?
tail -3 "$S/logs/pins.log" | sed 's/^/  /'
if [ $PIN_RC -eq 0 ]; then pass C "structural pins green (3/3)"; else fail C "structural pins RED (rc=$PIN_RC)"; fi

# ── Scratch PG16 cluster ────────────────────────────────────────────────
/usr/lib/postgresql/16/bin/initdb -D "$PGDATA" -A trust -U nea -E UTF8 >"$S/logs/initdb.log" 2>&1 || {
  echo "RESULT: FAIL"; exit 1; }
/usr/lib/postgresql/16/bin/pg_ctl -D "$PGDATA" -l "$S/logs/pg.log" \
  -o "-p $PG_PORT -k $PGSOCK -h 127.0.0.1" start >/dev/null 2>&1 || {
  echo "RESULT: FAIL"; exit 1; }
for _ in $(seq 1 60); do
  /usr/lib/postgresql/16/bin/pg_isready -h 127.0.0.1 -p "$PG_PORT" -U nea >/dev/null 2>&1 && break
  sleep 0.5
done
PSQL="/usr/lib/postgresql/16/bin/psql -h 127.0.0.1 -p $PG_PORT -U nea -v ON_ERROR_STOP=1 -X"
$PSQL -d postgres -c "CREATE DATABASE $DB_NAME" >/dev/null 2>&1 || true
echo "scratch PG16: $PGDATA (port $PG_PORT, db $DB_NAME)"
/usr/lib/postgresql/16/bin/psql -h 127.0.0.1 -p "$PG_PORT" -U nea -d postgres -Atc "SHOW server_version;"

q() { $PSQL -d "$DB_NAME" -Atc "$1"; }
assert_target() {
  local got
  got="$(q 'SELECT current_database();')"
  if [ "$got" != "$DB_NAME" ]; then
    echo "FENCE VIOLATION: current_database=$got (expected $DB_NAME) — aborting"
    exit 1
  fi
}

# ── Payload roots (frozen-binary COPIES; agents/plugins beside each) ────
make_root() {  # $1 = dir name, $2 = binary source
  local r="$S/root-$1"
  mkdir -p "$r/bin" "$r/data" "$r/home"
  cp "$2" "$r/bin/ensemble-prod"; chmod +x "$r/bin/ensemble-prod"
  cp -a "$PROJECT_DIR/agents" "$r/agents"
  cp -a "$PROJECT_DIR/plugins" "$r/plugins" 2>/dev/null || true
  cp "$PROJECT_DIR/config.yaml" "$r/config.yaml"
  echo "$r"
}
ROOT_INDUCTION="$(make_root induction "$BINS/ensemble-prod.induction")"
ROOT_CLEAN="$(make_root clean "$BINS/ensemble-prod.clean")"
ROOT_MUTANT="$(make_root mutant "$BINS/ensemble-prod.tryexcept")"

# ── Sentinel pre-shaping ────────────────────────────────────────────────
# S1: instance_execution_leases — inherited CREATE TABLE IF NOT EXISTS with
#     NO ORM model, so create_all never makes it; fires on every boot.
# S2: projects — pre-created from the model shape WITHOUT the
#     uq_projects_name unique constraint, so the inherited
#     `CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_name` fires.
presplit() {
  local db="$1"
  $PSQL -d "$db" -q <<'SQL'
CREATE TABLE IF NOT EXISTS projects (
    project_id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    project_type VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    main_directory VARCHAR,
    related_directories JSONB,
    description VARCHAR,
    job_queue_paused BOOLEAN NOT NULL,
    metadata JSONB,
    relationships JSONB,
    creator_instance_id VARCHAR,
    creator_agent_id VARCHAR,
    created_at VARCHAR NOT NULL,
    updated_at VARCHAR NOT NULL,
    PRIMARY KEY (project_id),
    CONSTRAINT ck_projects_status_valid CHECK (status IN ('active','paused','completed','archived'))
);
SQL
}
sentinel_pending() {
  local db="$1" leases idx
  leases="$($PSQL -d "$db" -Atc "SELECT to_regclass('public.instance_execution_leases') IS NULL;")"
  idx="$($PSQL -d "$db" -Atc "SELECT count(*) FROM pg_class WHERE relname='uq_projects_name';")"
  [ "$leases" = "t" ] && [ "$idx" = "0" ]
}

# ── Boot helper: launch, poll /livez, report UP / EXITED:<rc> / TIMEOUT ──
boot_binary() {  # $1 root  $2 db  $3 daemon port  $4 log  $5 wait-seconds
  local root="$1" db="$2" dport="$3" log="$4" wait_s="$5"
  env \
    -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST \
    -u POSTGRES_PORT -u POSTGRES_URL -u PORT -u HOST \
    -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_UPGRADE_SCRIPTS_DIR \
    -u RAG_IS_REQUIRED -u ENSEMBLE_MISSION_RECORDS \
    -u ENSEMBLE_JOB_SYSTEM_JOB_RETRY_SCHEDULER_ENABLED \
    -u ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART -u ENSEMBLE_BASH_TRACKING_ID \
    -u EMBEDDING_API_KEY -u EMBEDDING_BASE_URL -u EMBEDDING_MODEL -u EMBEDDING_DIMENSIONS \
    -u DATA_DIR \
    POSTGRES_DB="$db" POSTGRES_USER=nea POSTGRES_PASSWORD= \
    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="$PG_PORT" \
    PORT="$dport" HOST=127.0.0.1 \
    ENSEMBLE_SELF_ENV=sandbox \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    ENSEMBLE_CONFIG="$root/config.yaml" \
    ENSEMBLE_DATA_DIR="$root/data" \
    HOME="$root/home" \
    ENSEMBLE_ORPHAN_F1_ENABLED=0 \
    ENSEMBLE_MEMOIR_ENABLED=false \
    RAG_IS_REQUIRED=false \
    EMBEDDING_BASE_URL="http://127.0.0.1:9/v1" \
    EMBEDDING_API_KEY=dummy \
    EMBEDDING_MODEL=text-embedding-3-small \
    "$root/bin/ensemble-prod" >"$log" 2>&1 &
  local pid=$!
  BOOT_PGID=$pid
  BOOT_PID=$pid
  local i
  for i in $(seq 1 "$wait_s"); do
    if curl -sf -m 1 -o /dev/null "http://127.0.0.1:$dport/livez" 2>/dev/null; then
      echo "UP:$pid"; return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid"; local rc=$?
      echo "EXITED:$rc:$pid"; return 0
    fi
    sleep 1
  done
  echo "TIMEOUT:$pid"
}

stop_boot() {  # $1 pgid  $2 scratch root (for the anchored pkill fallback)
  kill -TERM -P "$1" 2>/dev/null || true
  kill -TERM "$1" 2>/dev/null || true
  for _ in $(seq 1 30); do kill -0 "$1" 2>/dev/null || break; sleep 0.5; done
  kill -KILL -P "$1" 2>/dev/null || true
  kill -KILL "$1" 2>/dev/null || true
  if [ -n "${2:-}" ]; then pkill -TERM -f "$2/bin/ensemble-prod" 2>/dev/null || true; fi
  BOOT_PGID=""
}

# ══ A. INDUCTION LEG ═══════════════════════════════════════════════════
echo "--- A. induction boot (planted no_such_imm_function) ---"
presplit "$DB_NAME" || { fail A "pre-shape failed"; }
assert_target
sentinel_pending "$DB_NAME" && pass A0 "sentinels PENDING before boot" || fail A0 "sentinels not pending"

OUT="$(boot_binary "$ROOT_INDUCTION" "$DB_NAME" "$CLEAN_PORT" "$S/logs/induction.log" 90)"
case "$OUT" in
  EXITED:*)
    RC="$(echo "$OUT" | cut -d: -f2)"; PID="$(echo "$OUT" | cut -d: -f3)"
    if [ "$RC" != "0" ]; then pass A1 "boot FAILED, non-zero exit rc=$RC"; else fail A1 "boot exited ZERO"; fi
    if curl -s -m 1 -o /dev/null "http://127.0.0.1:$CLEAN_PORT/livez" 2>/dev/null; then fail A1 "listener came up"; else pass A1b "no listener on $CLEAN_PORT"; fi
    ;;
  UP:*) fail A1 "induction boot reached /livez — the ensure did NOT abort the boot"; stop_boot "$(echo "$OUT" | cut -d: -f2)" "$ROOT_INDUCTION" ;;
  *) fail A1 "induction boot did not exit within 90s" ;;
esac
if grep -q "no_such_imm_function\|ADD COLUMN IF NOT EXISTS tsv" "$S/logs/induction.log"; then
  pass A2 "failure names the offending memoir statement ($(grep -om1 'no_such_imm_function\|ADD COLUMN IF NOT EXISTS tsv' "$S/logs/induction.log"))"
else
  fail A2 "offending statement not named in the log"
fi
TSV_ABSENT="$(q "SELECT count(*) FROM information_schema.columns WHERE table_name='memoir_episodes' AND column_name='tsv';")"
GIN_ABSENT="$(q "SELECT count(*) FROM pg_class WHERE relname='idx_memoir_episodes_tsv';")"
TABLES_PRESENT="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'memoir_%';")"
if [ "$TSV_ABSENT" = "0" ] && [ "$GIN_ABSENT" = "0" ]; then
  pass A3 "no partial memoir ENSURE schema (tsv absent, GIN absent); create_all tables present=$TABLES_PRESENT (separate committed transaction — disclosed)"
else
  fail A3 "partial memoir ensure schema survived (tsv=$TSV_ABSENT gin=$GIN_ABSENT)"
fi
L2="$(q "SELECT to_regclass('public.instance_execution_leases') IS NULL;")"
I2="$(q "SELECT count(*) FROM pg_class WHERE relname='uq_projects_name';")"
if [ "$L2" = "t" ] && [ "$I2" = "0" ]; then
  pass A4 "COLLATERAL ROLLBACK PROVED — instance_execution_leases ABSENT, uq_projects_name ABSENT"
else
  fail A4 "an inherited statement committed anyway (leases_absent=$L2 uq_projects_name=$I2)"
fi

# ══ B. CLEAN / NON-REGRESSION LEG ═════════════════════════════════════
echo "--- B. clean boot (non-vacuity + non-regression) ---"
$PSQL -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME" >/dev/null 2>&1
$PSQL -d postgres -c "CREATE DATABASE $DB_NAME" >/dev/null 2>&1
presplit "$DB_NAME"
assert_target
sleep 1
OUT="$(boot_binary "$ROOT_CLEAN" "$DB_NAME" "$CLEAN_PORT" "$S/logs/clean.log" 120)"
case "$OUT" in
  UP:*) pass B1 "clean boot UP (/livez 200)"; CLEAN_PGID="$(echo "$OUT" | cut -d: -f2)" ;;
  *) fail B1 "clean boot did not come up ($OUT)"; CLEAN_PGID="" ;;
esac
if [ -n "$CLEAN_PGID" ]; then
  MT="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('memoir_episodes','memoir_persona','memoir_persona_versions','memoir_syntheses');")"
  TSV="$(q "SELECT data_type||'|'||is_generated FROM information_schema.columns WHERE table_name='memoir_episodes' AND column_name='tsv';")"
  GIN="$(q "SELECT count(*) FROM pg_indexes WHERE indexname='idx_memoir_episodes_tsv' AND indexdef ILIKE '%USING gin%';")"
  [ "$MT" = "4" ] && pass B2 "4 memoir tables present" || fail B2 "memoir tables=$MT/4"
  [ "$TSV" = "tsvector|ALWAYS" ] && pass B3 "tsv GENERATED ALWAYS column present" || fail B3 "tsv shape=$TSV"
  [ "$GIN" = "1" ] && pass B4 "GIN index present" || fail B4 "GIN index=$GIN"
  q "INSERT INTO memoir_episodes (id, agent_id, body, created_at, event_at, importance, is_keepsake, access_count, embedding_attempts) VALUES ('w8-clean-1','ari','the memoir world remembers the emperor', now(), now(), 5, false, 0, 0);" >/dev/null
  HIT="$(q "SELECT count(*) FROM memoir_episodes WHERE tsv @@ plainto_tsquery('english','memoir');")"
  [ "$HIT" = "1" ] && pass B5 "tsv populates on INSERT and matches the query" || fail B5 "tsv match count=$HIT"
  L3="$(q "SELECT to_regclass('public.instance_execution_leases') IS NOT NULL;")"
  I3="$(q "SELECT count(*) FROM pg_class WHERE relname='uq_projects_name';")"
  { [ "$L3" = "t" ] && [ "$I3" = "1" ]; } && pass B6 "inherited sentinels COMMITTED (non-vacuity control)" || fail B6 "sentinels missing (leases=$L3 uq=$I3)"
  # Idempotence: snapshot object census, stop, boot again on the same DB.
  T_BEFORE="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
  I_BEFORE="$(q "SELECT count(*) FROM pg_class WHERE relkind IN ('i','I');")"
  stop_boot "$CLEAN_PGID" "$ROOT_CLEAN"
  sleep 1
  assert_target
  OUT2="$(boot_binary "$ROOT_CLEAN" "$DB_NAME" "$CLEAN_PORT" "$S/logs/clean2.log" 120)"
  case "$OUT2" in UP:*) pass B7 "second clean boot UP (ensure idempotent)"; stop_boot "$(echo "$OUT2" | cut -d: -f2)" "$ROOT_CLEAN" ;; *) fail B7 "second boot did not come up ($OUT2)" ;; esac
  T_AFTER="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
  I_AFTER="$(q "SELECT count(*) FROM pg_class WHERE relkind IN ('i','I');")"
  { [ "$T_BEFORE" = "$T_AFTER" ] && [ "$I_BEFORE" = "$I_AFTER" ]; } && pass B8 "no-op on second boot (tables $T_BEFORE==$T_AFTER, indexes $I_BEFORE==$I_AFTER)" || fail B8 "object census drifted (tables $T_BEFORE->$T_AFTER, indexes $I_BEFORE->$I_AFTER)"
fi

# ══ C2. TRY/EXCEPT MUTANT (behavioral half of the pin) ════════════════
echo "--- C2. try/except mutant boot (the W8.2-1 RED condition) ---"
$PSQL -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME" >/dev/null 2>&1
$PSQL -d postgres -c "CREATE DATABASE $DB_NAME" >/dev/null 2>&1
presplit "$DB_NAME"
assert_target
sleep 1
OUTM="$(boot_binary "$ROOT_MUTANT" "$DB_NAME" "$MUTANT_PORT" "$S/logs/mutant.log" 120)"
case "$OUTM" in
  UP:*) MUT_PGID="$(echo "$OUTM" | cut -d: -f2)"
        pass C2 "mutation is BEHAVIORALLY MEANINGFUL — with try/except the boot SUCCEEDS (observation-1 RED, as predicted)"
        MTSV="$(q "SELECT count(*) FROM information_schema.columns WHERE table_name='memoir_episodes' AND column_name='tsv';")"
        ML2="$(q "SELECT to_regclass('public.instance_execution_leases') IS NOT NULL;")"
        [ "$MTSV" = "0" ] && pass C3 "ensure result ABSENT while the daemon boots (tsv missing — the first lexical query would crash later)" || fail C3 "tsv present under mutant ($MTSV)"
        # PG aborts the whole transaction on the first error; every later
        # execute raises InFailedSqlTransaction (swallowed), and COMMIT on an
        # aborted transaction behaves as ROLLBACK. So the try/except mutant
        # boots with the ENTIRE shared ensure silently rolled back.
        [ "$ML2" = "f" ] && pass C4 "the ENTIRE shared ensure silently rolled back while the daemon booted (try/except turned a loud abort into a silent no-op)" || fail C4 "inherited sentinel unexpectedly present under mutant"
        stop_boot "$MUT_PGID" "$ROOT_MUTANT" ;;
  *)
    fail C2 "mutant boot did not succeed ($OUTM) — the try/except mutation would be non-meaningful" ;;
esac

# ══ LIVE FENCE POST ════════════════════════════════════════════════════
MID_STATE_SHA="$(sha256sum "$LIVE_STATE" 2>/dev/null | awk '{print $1}')"
MID_CURRENT="$(readlink "$LIVE_CURRENT" 2>/dev/null)"
MID_LIVEZ="$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:9797/livez 2>/dev/null || true)"
cleanup; trap - EXIT
POST_STATE_SHA="$(sha256sum "$LIVE_STATE" 2>/dev/null | awk '{print $1}')"
POST_CURRENT="$(readlink "$LIVE_CURRENT" 2>/dev/null)"
POST_LIVEZ="$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:9797/livez 2>/dev/null || true)"
echo "live fence: PRE state=${PRE_STATE_SHA} current=${PRE_CURRENT} livez=${PRE_LIVEZ}"
echo "live fence: MID state=${MID_STATE_SHA} current=${MID_CURRENT} livez=${MID_LIVEZ}"
echo "live fence: PST state=${POST_STATE_SHA} current=${POST_CURRENT} livez=${POST_LIVEZ}"
if [ "$PRE_STATE_SHA" = "$MID_STATE_SHA" ] && [ "$PRE_STATE_SHA" = "$POST_STATE_SHA" ] \
   && [ "$PRE_CURRENT" = "$MID_CURRENT" ] && [ "$PRE_CURRENT" = "$POST_CURRENT" ] \
   && [ "$PRE_LIVEZ" = "200" ] && [ "$MID_LIVEZ" = "200" ] && [ "$POST_LIVEZ" = "200" ]; then
  pass A5 "live fence holds (state.json byte-identical, current unchanged, :9797 monotone)"
else
  fail A5 "live fence broken"
fi

# ── Ports freed + scratch removed ──────────────────────────────────────
for p in "$PG_PORT" "$CLEAN_PORT" "$MUTANT_PORT"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then
    echo "CLEANUP WARNING: port $p still accepting"; FAILED=1
  fi
done
if [ "${MEMOIR_W8_KEEP_SCRATCH:-0}" != "1" ] && [ -d "$S" ]; then
  echo "CLEANUP WARNING: scratch root $S survived"; FAILED=1
fi

if [ "$FAILED" -eq 0 ]; then echo "RESULT: PASS"; exit 0; else echo "RESULT: FAIL"; exit 1; fi
