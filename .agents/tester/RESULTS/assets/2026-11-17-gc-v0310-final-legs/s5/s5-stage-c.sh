#!/bin/bash
# S5 stage C — scratch fixture prep.
#   C1: snapshot ensemble_dev_scratch state (owners/grants/shape/rows) for the "unchanged" proof
#   C2: create ensemble_dev_scratch_s5 as a TEMPLATE clone of ensemble_dev_scratch
#   C3: transfer the clone's objects to ensemble_dev_app (the commission's role)
#   C4: verify the clone's shape == source shape (58 tables, room tables 10 cols, 0 rows)
# Rationale: ensemble_dev_app owns only the 3 room tables; the other 55 are owned by the
# superuser role `ensemble` => no REFERENCES (create_all aborts on project_future) and
# ALTER is not grantable for tables => the dev role cannot complete a boot in place.
# The clone keeps the commission's ROLE + PASSWORD + the pre-P3/P5 room shape (so the
# 10->14 _ensure_postgres_columns upgrade path is still exercised) and leaves the shared
# ensemble_dev_scratch byte-for-byte as found.
set -uo pipefail
STATE=/tmp/gc_v0310_smoke_state.env
. "$STATE"
T="$EVID/s5-transcript.txt"
WT=/home/nea/ensemble-src-wt-group-chat-release
DPW=$(grep -m1 '^POSTGRES_PASSWORD=' "$WT/.env" | cut -d= -f2-)
CLONE=ensemble_dev_scratch_s5
echo "CLONE=$CLONE" >> "$STATE"

log(){ printf '### %s :: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$T"; }
run(){ log "CMD: $*"; eval "$@" >> "$T" 2>&1; rc=$?; echo "RC=$rc" >> "$T"; echo "  [exit=$rc]"; return $rc; }

log "=== STAGE C: scratch fixture prep ==="

# ── C1: snapshot the SOURCE scratch DB state (evidence + later unchanged-proof) ──
export PGPASSWORD="$DPW"
Q="psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d ensemble_dev_scratch -w -Atc"
{
  echo "### SOURCE ensemble_dev_scratch SNAPSHOT (pre-boot) ###"
  echo "-- db owner --"; "${Q}" "select d.datname||' owner='||pg_get_userbyid(d.datdba) from pg_database d where d.datname='ensemble_dev_scratch';"
  echo "-- schema owner --"; "${Q}" "select nspname||' owner='||pg_get_userbyid(nspowner) from pg_namespace where nspname='public';"
  echo "-- table count --"; "${Q}" "select count(*) from pg_tables where schemaname='public';"
  echo "-- owners histogram --"; "${Q}" "select tableowner||'='||count(*) from pg_tables where schemaname='public' group by 1 order by 1;"
  echo "-- room table columns --"; "${Q}" "select table_name||' cols='||count(*) from information_schema.columns where table_schema='public' and table_name in ('chatrooms','chatroom_members','room_messages') group by 1 order by 1;"
  echo "-- room row counts --"; "${Q}" "select 'chatrooms='||(select count(*) from chatrooms)||' chatroom_members='||(select count(*) from chatroom_members)||' room_messages='||(select count(*) from room_messages);"
  echo "-- role_table_grants for ensemble_dev_app --"; "${Q}" "select table_name||' '||privilege_type from information_schema.role_table_grants where grantee='ensemble_dev_app' order by 1;"
  echo "-- role_usage_grants for ensemble_dev_app --"; "${Q}" "select object_name||' '||privilege_type from information_schema.role_usage_grants where grantee='ensemble_dev_app' order by 1;"
  echo "-- schemas --"; "${Q}" "select nspname from pg_namespace where nspname not like 'pg_%' and nspname <> 'information_schema' order by 1;"
} > "$EVID/source-scratchdb-before.txt" 2>&1
cat "$EVID/source-scratchdb-before.txt" >> "$T"
unset PGPASSWORD

# ── C2/C3: clone + ownership transfer (superuser via unix-socket peer auth as OS user nea) ──
run "psql -d postgres -w -Atc \"select count(*) from pg_stat_activity where datname='ensemble_dev_scratch';\""
run "psql -d postgres -w -c \"CREATE DATABASE $CLONE TEMPLATE ensemble_dev_scratch;\""
run "psql -d $CLONE -w -c \"ALTER DATABASE $CLONE OWNER TO ensemble_dev_app;\""
run "psql -d $CLONE -w -c \"ALTER SCHEMA public OWNER TO ensemble_dev_app;\""
run "psql -d $CLONE -w -c \"DO \\\$\\\$ DECLARE r record; BEGIN FOR r IN SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND pg_get_userbyid(c.relowner) <> 'ensemble_dev_app' AND c.relkind IN ('r','p','S') LOOP EXECUTE format('ALTER %s public.%I OWNER TO ensemble_dev_app', CASE r.relkind WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, r.relname); END LOOP; END \\\$\\\$;\""

# ── C4: verify the clone matches the source shape ───────────────────────
export PGPASSWORD="$DPW"
{
  echo "### CLONE $CLONE SNAPSHOT (pre-boot) ###"
  echo "-- db/schema owner --"; psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d "$CLONE" -w -Atc "select d.datname||' owner='||pg_get_userbyid(d.datdba) from pg_database d where d.datname='$CLONE';"
  psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d "$CLONE" -w -Atc "select nspname||' owner='||pg_get_userbyid(nspowner) from pg_namespace where nspname='public';"
  echo "-- table count --"; psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d "$CLONE" -w -Atc "select count(*) from pg_tables where schemaname='public';"
  echo "-- owners histogram --"; psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d "$CLONE" -w -Atc "select tableowner||'='||count(*) from pg_tables where schemaname='public' group by 1 order by 1;"
  echo "-- room table columns (expect 10/9/10) --"; psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d "$CLONE" -w -Atc "select table_name||' cols='||count(*) from information_schema.columns where table_schema='public' and table_name in ('chatrooms','chatroom_members','room_messages') group by 1 order by 1;"
  echo "-- room row counts (expect 0) --"; psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d "$CLONE" -w -Atc "select 'chatrooms='||(select count(*) from chatrooms)||' chatroom_members='||(select count(*) from chatroom_members)||' room_messages='||(select count(*) from room_messages);"
  echo "-- gc_pg_probe residue --"; psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d "$CLONE" -w -Atc "select count(*) from pg_namespace where nspname like 'gc_pg_probe%';"
} > "$EVID/clone-scratchdb-before.txt" 2>&1
cat "$EVID/clone-scratchdb-before.txt" >> "$T"
unset PGPASSWORD
log "=== STAGE C COMPLETE ==="
exit 0
