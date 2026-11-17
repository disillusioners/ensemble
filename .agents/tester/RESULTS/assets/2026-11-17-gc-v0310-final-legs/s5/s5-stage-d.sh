#!/bin/bash
set -uo pipefail
STATE=/tmp/gc_v0310_smoke_state.env
. "$STATE"
T="$EVID/s5-transcript.txt"
WT=/home/nea/ensemble-src-wt-group-chat-release
DPW=$(grep -m1 '^POSTGRES_PASSWORD=' "$WT/.env" | cut -d= -f2-)
CLONE=ensemble_dev_scratch_s5
export PGPASSWORD="$DPW"
pq(){ psql -h 127.0.0.1 -p 5432 -U ensemble_dev_app -d "$1" -w -Atc "$2"; }
log(){ printf '### %s :: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$T"; }

log "=== STAGE D: post-boot proofs (clone $CLONE) ==="
{
  echo "### POST-BOOT shape of $CLONE ###"
  echo "-- room tables + column counts --"
  pq "$CLONE" "select c.relname||' cols='||(select count(*) from information_schema.columns ic where ic.table_schema='public' and ic.table_name=c.relname) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relname in ('chatrooms','chatroom_members','room_messages') order by 1;"
  echo "-- room_messages column list --"
  pq "$CLONE" "select column_name||' '||data_type from information_schema.columns where table_schema='public' and table_name='room_messages' order by ordinal_position;"
  echo "-- the 4 new columns present? --"
  pq "$CLONE" "select string_agg(column_name, ',' order by column_name) from information_schema.columns where table_schema='public' and table_name='room_messages' and column_name in ('source_message_id','trigger','member','resolution_warnings');"
  echo "-- room row counts --"
  pq "$CLONE" "select 'chatrooms='||(select count(*) from chatrooms)||' chatroom_members='||(select count(*) from chatroom_members)||' room_messages='||(select count(*) from room_messages);"
  echo "-- uq_room_messages_proposal_dedup (index) --"
  pq "$CLONE" "select indexname||' def='||indexdef from pg_indexes where schemaname='public' and indexname like '%proposal_dedup%';"
  echo "-- uq_room_messages_proposal_dedup (constraint) --"
  pq "$CLONE" "select conname||' '||pg_get_constraintdef(oid) from pg_constraint where conname like '%proposal_dedup%';"
  echo "-- room UNIQUE indexes --"
  pq "$CLONE" "select indexname from pg_indexes where schemaname='public' and tablename in ('chatrooms','chatroom_members','room_messages') and indexdef like '%UNIQUE%' order by 1;"
  echo "-- total public table count (post-boot) --"
  pq "$CLONE" "select count(*) from pg_tables where schemaname='public';"
  echo "-- schemas list --"
  pq "$CLONE" "select nspname from pg_namespace where nspname not like 'pg_%' and nspname <> 'information_schema' order by 1;"
  echo "-- gc_pg_probe% residue --"
  pq "$CLONE" "select count(*) from pg_namespace where nspname like 'gc_pg_probe%';"
} > "$EVID/postboot-clone-proofs.txt" 2>&1
cat "$EVID/postboot-clone-proofs.txt" >> "$T"

log "=== STAGE D: SOURCE ensemble_dev_scratch — MUST BE UNCHANGED ==="
{
  echo "-- room cols (expect 9/10/10 untouched) --"
  pq ensemble_dev_scratch "select table_name||'='||count(*) as c from information_schema.columns where table_schema='public' and table_name in ('chatrooms','chatroom_members','room_messages') group by table_name order by 1;"
  echo "-- room rows --"
  pq ensemble_dev_scratch "select 'chatrooms='||(select count(*) from chatrooms)||' chatroom_members='||(select count(*) from chatroom_members)||' room_messages='||(select count(*) from room_messages);"
  echo "-- owners histogram (expect ensemble=55 ensemble_dev_app=3) --"
  pq ensemble_dev_scratch "select tableowner||'='||count(*) as c from pg_tables where schemaname='public' group by tableowner order by 1;"
  echo "-- table count (expect 58) --"
  pq ensemble_dev_scratch "select count(*) from pg_tables where schemaname='public';"
  echo "-- schemas --"
  pq ensemble_dev_scratch "select nspname from pg_namespace where nspname not like 'pg_%' and nspname <> 'information_schema' order by 1;"
  echo "-- proposal-dedup index in source? (expect 0 = untouched) --"
  pq ensemble_dev_scratch "select count(*) from pg_indexes where schemaname='public' and indexname like '%proposal_dedup%';"
} > "$EVID/source-scratchdb-after.txt" 2>&1
cat "$EVID/source-scratchdb-after.txt" >> "$T"
unset PGPASSWORD

log "=== STAGE D: strict termination census (criterion 5) ==="
{
  echo "-- SIGTERM/SIGKILL occurrences (stdout then stderr) --"; grep -cE 'SIGTERM|SIGKILL' "$SCRATCH/boot.stdout.txt" "$SCRATCH/boot.stderr.txt"
  echo "-- word 'killed' in stderr --"; grep -ciE '\bkilled\b' "$SCRATCH/boot.stderr.txt"
  echo "-- reaped=<n> histogram --"; grep -oE 'reaped=[0-9]+' "$SCRATCH/boot.stderr.txt" | sort | uniq -c
  echo "-- boot_subprocess_sweep / stale-subprocess mentions --"; grep -icE 'boot_subprocess_sweep|stale subprocess|subprocess sweep' "$SCRATCH/boot.stderr.txt"
  echo "-- bash reaper master-switch lines --"; grep -iE 'bash reaper' "$SCRATCH/boot.stderr.txt"
  echo "-- RAM guard mentions --"; grep -icE 'ram_guard|ram guard|ram_breach|ram_killed' "$SCRATCH/boot.stderr.txt"
  echo "-- lines with an actual termination verb --"; grep -inE 'terminated|terminating|SIGKILL|SIGTERM|reaped=[1-9]' "$SCRATCH/boot.stderr.txt" | head -20
} > "$EVID/termination-census.txt" 2>&1
cat "$EVID/termination-census.txt" >> "$T"

log "=== STAGE D: tool vocabulary ==="
{
  echo "-- tool-categories files in payload --"; find "$SCRATCH/payload" -name 'tool-categories.json' 2>/dev/null | head -5
  for f in $(find "$SCRATCH/payload" -name 'tool-categories.json' 2>/dev/null); do
    echo "-- file: $f"; echo "   hits=\$(grep -c '\"room\"' $f)"; grep -o '"room"' "$f" | head -3; grep -o '.\{0,140\}"room".\{0,200\}' "$f" | head -3
  done
} > "$EVID/tool-vocab.txt" 2>&1
cat "$EVID/tool-vocab.txt" >> "$T"
log "=== STAGE D COMPLETE ==="
