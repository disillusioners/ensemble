#!/bin/bash
# S5 stage E — TEARDOWN + ZERO-RESIDUE sweep.
# Order per commission: processes FIRST, then dirs. Kill only after verifying
# /proc/<pid>/exe resolves under $SCRATCH/payload.
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
run(){ log "CMD: $*"; eval "$@" >> "$T" 2>&1; rc=$?; echo "RC=$rc" >> "$T"; echo "  [exit=$rc]"; return $rc; }

log "=== STAGE E: teardown ==="

# ── 1. processes FIRST ─────────────────────────────────────────────────
run "ss -ltn | grep 19101 || echo 'CONFIRMED: no listener on 19101'"
run "pgrep -af '$SCRATCH/payload/ensemble-prod' || echo 'CONFIRMED: no scratch payload process remains'"
run "pgrep -af 'gc-s5-smoke' || echo 'CONFIRMED: no scope-named process remains'"

# any surviving process must be killed ONLY if its exe resolves under $SCRATCH/payload
SURV=$(pgrep -f "$SCRATCH/payload" 2>/dev/null | tr '\n' ' ')
if [ -n "${SURV// /}" ]; then
  for p in $SURV; do
    EXE=$(readlink -f /proc/$p/exe 2>/dev/null)
    case "$EXE" in
      "$SCRATCH"/*) log "SURVIVOR pid=$p exe=$EXE -> under \$SCRATCH, terminating"; kill -TERM "$p" 2>/dev/null ;;
      *) log "SURVIVOR pid=$p exe=$EXE -> NOT under \$SCRATCH, NOT killing (fence)" ;;
    esac
  done
  sleep 2
else
  log "no surviving scratch processes (no kill issued)"
fi

# ── 2. copy key evidence OUT of the scratch tree BEFORE rm ─────────────
for f in boot.stdout.txt boot.stderr.txt; do
  [ -f "$SCRATCH/$f" ] && cp "$SCRATCH/$f" "$EVID/$f" && log "copied $f -> $EVID/$f ($(wc -c < "$EVID/$f") bytes)"
done
cp "$EVID/s5-transcript.txt" "$EVID/s5-transcript-copy.txt" 2>/dev/null

# ── 3. out-of-tree residue: the port-claim files MY smoke created ──────
PC=/run/user/1000/ensemble-port-claims
if [ -d "$PC" ]; then
  for f in "$PC"/any-19101.json "$PC"/port-ownership-refusal.json.seen-*; do
    if [ -e "$f" ]; then
      if grep -q 'gc_v0310_smoke' "$f" 2>/dev/null || grep -q '2026-11-17T14:2' "$f" 2>/dev/null; then
        log "removing smoke-owned port-claim residue: $f"
        rm -f "$f"
      else
        log "NOT removing (not attributable to this smoke): $f"
      fi
    fi
  done
  run "ls -la $PC"
fi

# ── 4. drop the clone DB (this leg's own artifact) ─────────────────────
run "psql -d postgres -w -c \"DROP DATABASE IF EXISTS $CLONE;\""
run "psql -d postgres -w -Atc \"select count(*) from pg_database where datname='$CLONE';\""

# ── 5. residue sweep in the DBs ────────────────────────────────────────
{
  echo "-- source ensemble_dev_scratch: gc_pg_probe% residue --"
  pq ensemble_dev_scratch "select count(*) from pg_namespace where nspname like 'gc_pg_probe%';"
  echo "-- source ensemble_dev_scratch: room cols (expect 9/10/10 untouched) --"
  pq ensemble_dev_scratch "select table_name||'='||count(*) as c from information_schema.columns where table_schema='public' and table_name in ('chatrooms','chatroom_members','room_messages') group by table_name order by 1;"
  echo "-- source ensemble_dev_scratch: owners (expect ensemble=55 dev=3) --"
  pq ensemble_dev_scratch "select tableowner||'='||count(*) as c from pg_tables where schemaname='public' group by tableowner order by 1;"
  echo "-- source ensemble_dev_scratch: table count (expect 58) --"
  pq ensemble_dev_scratch "select count(*) from pg_tables where schemaname='public';"
  echo "-- source ensemble_dev_scratch: room rows (expect 0/0/0) --"
  pq ensemble_dev_scratch "select 'chatrooms='||(select count(*) from chatrooms)||' members='||(select count(*) from chatroom_members)||' messages='||(select count(*) from room_messages);"
  echo "-- databases named ensemble_dev_scratch* on the local cluster --"
  psql -d postgres -w -Atc "select datname||' owner='||pg_get_userbyid(datdba) from pg_database where datname like 'ensemble_dev_scratch%' order by 1;"
  echo "-- gc_pg_probe% across ALL local databases (expect 0 rows) --"
  psql -d postgres -w -Atc "select datname from pg_database where datname not in ('template0','template1') and not datistemplate;"
} > "$EVID/residue-sweep.txt" 2>&1
cat "$EVID/residue-sweep.txt" >> "$T"
unset PGPASSWORD

# ── 6. rm -rf the scratch tree ─────────────────────────────────────────
run "du -sh '$SCRATCH'"
rm -rf "$SCRATCH"
run "test -d '$SCRATCH' && echo 'SCRATCH STILL PRESENT' || echo 'CONFIRMED: scratch tree removed'"

# ── 7. zero-residue verification ───────────────────────────────────────
{
  echo "-- /tmp/gc_v0310_smoke.* remaining (expect ONLY the evidence dir) --"
  ls -d /tmp/gc_v0310_smoke.* 2>/dev/null
  echo "-- count --"; ls -d /tmp/gc_v0310_smoke.* 2>/dev/null | wc -l
  echo "-- evidence dir contents --"; ls -la "$EVID"
} > "$EVID/zero-residue.txt" 2>&1
cat "$EVID/zero-residue.txt" >> "$T"
log "=== STAGE E COMPLETE ==="
exit 0
