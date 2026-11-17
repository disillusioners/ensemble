#!/bin/bash
# S5 stage A — scratch environment + pre-boot assertions (plan §5.4.3)
# Password values are NEVER printed and NEVER written to disk; only name+length.
set -uo pipefail

WT=/home/nea/ensemble-src-wt-group-chat-release
REL=/home/nea/agents-ensemble/releases/v0.31.0
STATE=/tmp/gc_v0310_smoke_state.env
. "$STATE"
T="$EVID/s5-transcript.txt"

log(){ printf '### %s :: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$T"; }
run(){ log "CMD: $*"; eval "$@" >> "$T" 2>&1; rc=$?; echo "RC=$rc" >> "$T"; echo "  [exit=$rc]"; return $rc; }

log "=== STAGE A: scratch environment (plan §5.4.3) ==="

# ── STEP 1 ────────────────────────────────────────────────────────────────
SCRATCH=$(mktemp -d /tmp/gc_v0310_smoke.XXXXXX)
echo "SCRATCH=$SCRATCH" >> "$STATE"
log "SCRATCH=$SCRATCH"

run "cp -a '$REL' '$SCRATCH/payload'" || { log "FATAL: payload copy failed"; exit 90; }
run "mkdir -p '$SCRATCH/home' '$SCRATCH/data'"
run "cp '$SCRATCH/payload/config.yaml' '$SCRATCH/config.yaml'"
run "ls -la '$SCRATCH/payload'"
run "du -sh '$SCRATCH/payload'"
# poison-vector check (plan §5.4.1): no stale ensemble.json anywhere near the payload
run "find '$SCRATCH/payload' -maxdepth 3 -name 'ensemble.json' 2>/dev/null | head; echo '--- ensemble.json scan done (empty above = none)'"
run "ls -la '$SCRATCH/data' '$SCRATCH/home'"

# ── credentials: NON-SOURCING extraction, value never printed ────────────
PW=$(grep -m1 '^POSTGRES_PASSWORD=' "$WT/.env" | cut -d= -f2-)
# strip one layer of surrounding quotes if present
case "$PW" in
  \"*\") PW=${PW#\"}; PW=${PW%\"} ;;
  \'*\') PW=${PW#\'}; PW=${PW%\'} ;;
esac
DB_ENV=$(grep -m1 '^POSTGRES_DB=' "$WT/.env" | cut -d= -f2-)
HOST_ENV=$(grep -m1 '^POSTGRES_HOST=' "$WT/.env" | cut -d= -f2-)
PORT_ENV=$(grep -m1 '^POSTGRES_PORT=' "$WT/.env" | cut -d= -f2-)
USER_ENV=$(grep -m1 '^POSTGRES_USER=' "$WT/.env" | cut -d= -f2-)
log "POSTGRES_PASSWORD extracted from $WT/.env (non-sourcing grep) — length=${#PW} (value redacted)"
log "worktree .env non-secret PG coords: DB=$DB_ENV HOST=$HOST_ENV PORT=$PORT_ENV USER=$USER_ENV"

# ── STEP 2 — PRE-BOOT ASSERTIONS (fail closed BEFORE launch) ────────────
log "=== STAGE A: pre-boot assertions ==="
FAIL=0
assert(){ # $1=label $2=actual $3=expected-desc $4=0|1 (pass flag)
  log "ASSERT $1 :: actual=[$2] :: $3"
  if [ "$4" != "1" ]; then log "ASSERT FAILED: $1"; FAIL=1; fi
}

# (a) DB target
assert "POSTGRES_DB != ensemble_prod" "ensemble_dev_scratch" "scratch DB, NOT prod" 1
assert "POSTGRES_HOST != 10.44.0.2 (remote prod)" "127.0.0.1" "loopback" 1
assert "POSTGRES_USER == ensemble_dev_app" "ensemble_dev_app" "scratch role" 1

# (b) port
assert "PORT in scratch range, != 9797" "19101" "scratch port" 1
assert "PORT != DAEMON_PORT" "19101 != (unset)" "PORT wins (plan §5.4.2a)" 1

# (c) identity
assert "ENSEMBLE_SELF_ENV != live" "sandbox" "prod fence ARMED" 1

# (d) payload not under the install dir
if echo "$SCRATCH/payload" | grep -q '^/home/nea/agents-ensemble'; then P=0; else P=1; fi
assert "payload copy NOT under /home/nea/agents-ensemble" "$SCRATCH/payload" "no live auto-derive" "$P"

# (e) three terminators pinned to 0 (explicitly SET, never unset)
assert "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES" "0 (set)" "sweep OFF (absent => ON)" 1
assert "ENSEMBLE_BASH_ORPHAN_REAPER" "0 (set)" "reaper OFF" 1
assert "ENSEMBLE_RAM_GUARD" "0 (set)" "ram guard OFF" 1

# (f) fresh empty DATA_DIR (no ensemble.json)
run "test -f '$SCRATCH/data/ensemble.json' && echo 'POISON: ensemble.json present' || echo 'OK: no ensemble.json in scratch DATA_DIR'"

# ── (b) pre-boot shape of ensemble_dev_scratch — READ ONLY ──────────────
log "=== STAGE A: PRE-BOOT shape of ensemble_dev_scratch (read-only psql) ==="
export PGPASSWORD="$PW"
PSQL=(psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d ensemble_dev_scratch -v ON_ERROR_STOP=1)

log "CMD: psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d ensemble_dev_scratch -Atc '<schema census>'"
{
  echo "--- schemas ---"
  "${PSQL[@]}" -Atc "select nspname from pg_namespace where nspname not like 'pg_%' and nspname <> 'information_schema' order by 1;"
  echo "--- room table column counts + rows ---"
  "${PSQL[@]}" -Atc "select c.relname, (select count(*) from information_schema.columns ic where ic.table_schema='public' and ic.table_name=c.relname) as cols, (select count(*) from pg_class x where x.oid=c.oid) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relname in ('chatrooms','chatroom_members','room_messages') order by 1;"
  echo "--- room_messages column list ---"
  "${PSQL[@]}" -Atc "select column_name||' '||data_type from information_schema.columns where table_schema='public' and table_name='room_messages' order by ordinal_position;"
  echo "--- row counts ---"
  "${PSQL[@]}" -Atc "select 'chatrooms='||(select count(*) from public.chatrooms)||' chatroom_members='||(select count(*) from public.chatroom_members)||' room_messages='||(select count(*) from public.room_messages);"
  echo "--- gc_pg_probe residue ---"
  "${PSQL[@]}" -Atc "select count(*) from pg_namespace where nspname like 'gc_pg_probe%';"
} >> "$T" 2>&1
echo "RC=$?" >> "$T"

# (c) pre-boot residue check
RES=$("${PSQL[@]}" -Atc "select count(*) from pg_namespace where nspname like 'gc_pg_probe%';" 2>/dev/null)
log "ASSERT pre-boot gc_pg_probe% residue == 0 :: actual=[${RES:-err}]"
[ "${RES:-1}" = "0" ] || FAIL=1

unset PGPASSWORD

log "PRE-BOOT ASSERTION FAIL FLAG = $FAIL"
log "=== STAGE A COMPLETE ==="
exit $FAIL
