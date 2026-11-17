#!/bin/bash
# S5 stage B v2 — THE BOOT.
#   B1: plan §5.4.3 recipe VERBATIM (foreground, exit-code capture)
#   B2: same recipe + the fence's own sanctioned knob ENSEMBLE_DB_AMBIENT_ALLOW=1
#       (DISCLOSED deviation — the plan text omits it; house harness template sets it)
set -uo pipefail

STATE=/tmp/gc_v0310_smoke_state.env
. "$STATE"
T="$EVID/s5-transcript.txt"
WT=/home/nea/ensemble-src-wt-group-chat-release

log(){ printf '### %s :: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$T"; }
run(){ log "CMD: $*"; eval "$@" >> "$T" 2>&1; rc=$?; echo "RC=$rc" >> "$T"; echo "  [exit=$rc]"; return $rc; }

PW=$(grep -m1 '^POSTGRES_PASSWORD=' "$WT/.env" | cut -d= -f2-)
case "$PW" in
  \"*\") PW=${PW#\"}; PW=${PW%\"} ;;
  \'*\') PW=${PW#\'}; PW=${PW%\'} ;;
esac

# ── the shared env form (per-variable scrub, plan §5.4.1) ────────────────
mkcmd(){ # $1 = extra knob ("" for none)
cat <<EOF
env -u POSTGRES_URL -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB \
    -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u DATABASE_URL -u DATABASE_URL_POSTGRES \
    -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD -u PGPASSFILE \
    -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE -u PGSSLCERT -u PGSSLKEY \
    -u PGSSLROOTCERT -u PGCONNECT_TIMEOUT -u PGAPPNAME -u PGOPTIONS -u PGDATA \
    -u PORT -u DAEMON_PORT -u HOST -u LIMITS_METRICS_LISTENER_PORT \
    -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR -u DATA_DIR \
    -u PLANE_MCP_WORKSPACE_SLUG -u PLANE_BASE_URL \
    HOME='$SCRATCH/home' \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    ENSEMBLE_BASH_ORPHAN_REAPER=0 \
    ENSEMBLE_RAM_GUARD=0 \
    ENSEMBLE_SELF_ENV=sandbox \
    ENSEMBLE_CONFIG='$SCRATCH/config.yaml' \
    ENSEMBLE_DATA_DIR='$SCRATCH/data' \
    PORT=19101 HOST=127.0.0.1 \
    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=ensemble_dev_scratch \
    POSTGRES_USER=ensemble_dev_app POSTGRES_PASSWORD='$PW' \
    $1 setsid timeout -k 5 30 '$SCRATCH/payload/ensemble-prod'
EOF
}

# ── B1: PLAN RECIPE VERBATIM (foreground, exit code capture) ─────────────
log "=== STAGE B1: plan §5.4.3 recipe VERBATIM (no additional knobs) ==="
CMD1=$(mkcmd "")
log "CMD (REDACTED — password withheld): $(echo "$CMD1" | sed "s/POSTGRES_PASSWORD='[^']*'/POSTGRES_PASSWORD='<REDACTED len=${#PW}>'/")"
BOOT1=$(echo "$CMD1" | tr '\n' ' ')
eval "$BOOT1" > "$SCRATCH/bootB1.stdout.txt" 2> "$SCRATCH/bootB1.stderr.txt"
RC1=$?
log "B1 VERBATIM RECIPE EXIT CODE = $RC1  (78 = DB-resolution guard refusal, pre-TCP)"
log "--- B1 stderr ---"
cat "$SCRATCH/bootB1.stderr.txt" >> "$T"
log "--- B1 stdout ---"
cat "$SCRATCH/bootB1.stdout.txt" >> "$T"
echo "B1_RC=$RC1" >> "$STATE"

# ── B2: recipe + fence-sanctioned knob (DISCLOSED DEVIATION) ─────────────
log "=== STAGE B2: plan recipe + ENSEMBLE_DB_AMBIENT_ALLOW=1 (DISCLOSED DEVIATION) ==="
log "DEVIATION: plan §5.4.3 omits ENSEMBLE_DB_AMBIENT_ALLOW=1; the payload's own guard message"
log "          prescribes it; the house harness template (tests/support/wedge_smoke.py:581) sets it."
log "          It grants ONLY the tier-2 ambient-first-boot sanction and is NEVER consulted by"
log "          assert_prod_dsn_sanctioned — the prod-name fence stays ARMED."

DAEMON_PID=""
LIVEZ_BODY=""; READYZ_BODY=""; LIVEZ_CODE=""; READYZ_CODE=""; LIVZ_FIRST=""; RDYZ_FIRST=""

CMD2=$(mkcmd "ENSEMBLE_DB_AMBIENT_ALLOW=1")
log "CMD (REDACTED — password withheld): $(echo "$CMD2" | sed "s/POSTGRES_PASSWORD='[^']*'/POSTGRES_PASSWORD='<REDACTED len=${#PW}>'/")"
BOOT2=$(echo "$CMD2" | tr '\n' ' ')

XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemd-run --user --scope --quiet --unit=gc-s5-smoke \
  /bin/bash -c "$BOOT2" > "$SCRATCH/boot.stdout.txt" 2> "$SCRATCH/boot.stderr.txt" &
BOOT_PID=$!
echo "BOOT_PID=$BOOT_PID" >> "$STATE"
log "boot launched in background: systemd-run client pid=$BOOT_PID, scope unit gc-s5-smoke"

for i in $(seq 1 34); do
  if [ -z "$LIVEZ_BODY" ]; then
    LB=$(curl -s -o /tmp/.s5_livez.$$ -w '%{http_code}' --max-time 2 http://127.0.0.1:19101/livez 2>/dev/null || echo "000")
    if [ "$LB" = "200" ]; then
      LIVEZ_BODY=$(cat /tmp/.s5_livez.$$); LIVEZ_CODE="$LB"; LIVZ_FIRST=$i
      log "livez FIRST SUCCESS t=${i}s HTTP 200 body: $LIVEZ_BODY"
      cp /tmp/.s5_livez.$$ "$EVID/livez-body.json" 2>/dev/null
    fi
  fi
  if [ -z "$READYZ_BODY" ]; then
    RB=$(curl -s -o /tmp/.s5_readyz.$$ -w '%{http_code}' --max-time 2 http://127.0.0.1:19101/readyz 2>/dev/null || echo "000")
    if [ "$RB" = "200" ]; then
      READYZ_BODY=$(cat /tmp/.s5_readyz.$$); READYZ_CODE="$RB"; RDYZ_FIRST=$i
      log "readyz FIRST SUCCESS t=${i}s HTTP 200 body: $READYZ_BODY"
      cp /tmp/.s5_readyz.$$ "$EVID/readyz-body.json" 2>/dev/null
    fi
  fi
  if [ -z "$DAEMON_PID" ]; then
    P=$(ss -ltnp 2>/dev/null | grep ':19101' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
    if [ -n "${P:-}" ]; then
      DAEMON_PID="$P"
      log "ISOLATION PROOF — listener pid=$DAEMON_PID"
      log "  /proc/$DAEMON_PID/cgroup :: $(cat /proc/$DAEMON_PID/cgroup 2>/dev/null | tr '\n' ' ')"
      log "  /proc/$DAEMON_PID/exe   :: $(readlink -f /proc/$DAEMON_PID/exe 2>/dev/null)"
      { echo "pid=$DAEMON_PID"; echo "cgroup:"; cat /proc/$DAEMON_PID/cgroup 2>/dev/null; echo "exe: $(readlink -f /proc/$DAEMON_PID/exe 2>/dev/null)"; } > "$EVID/cgroup-isolation.txt"
    fi
  fi
  if [ -n "$LIVEZ_BODY" ] && [ -n "$READYZ_BODY" ]; then break; fi
  sleep 1
done
rm -f /tmp/.s5_livez.$$ /tmp/.s5_readyz.$$
log "poll window done: livez=${LIVEZ_CODE:-none}(first@${LIVZ_FIRST:-n/a}s) readyz=${READYZ_CODE:-none}(first@${RDYZ_FIRST:-n/a}s)"

log "waiting for the plan's 30s bound to expire..."
WAIT=0
while kill -0 "$BOOT_PID" 2>/dev/null && [ "$WAIT" -lt 45 ]; do sleep 1; WAIT=$((WAIT+1)); done
if kill -0 "$BOOT_PID" 2>/dev/null; then log "WARNING: boot client still alive after ${WAIT}s"; else log "boot client exited after ~${WAIT}s wait"; fi
wait "$BOOT_PID" 2>/dev/null; BOOT_RC=$?
log "B2 boot exit rc=$BOOT_RC (124 = killed by the plan's timeout -k 5 30 bound)"

run "ss -ltn | grep 19101 || echo 'CONFIRMED: no listener on 19101'"
run "pgrep -af '$SCRATCH/payload/ensemble-prod' || echo 'CONFIRMED: no scratch payload process remains'"

log "--- boot.stdout.txt (last 80 lines) ---"; tail -80 "$SCRATCH/boot.stdout.txt" >> "$T" 2>&1
log "--- boot.stderr.txt (last 80 lines) ---"; tail -80 "$SCRATCH/boot.stderr.txt" >> "$T" 2>&1
N1=$(grep -ciE 'sweep|reaper|kill' "$SCRATCH/boot.stdout.txt" 2>/dev/null || true); N1=${N1:-0}
N2=$(grep -ciE 'sweep|reaper|kill' "$SCRATCH/boot.stderr.txt" 2>/dev/null || true); N2=${N2:-0}
log "sweep|reaper|kill census: stdout=$N1 stderr=$N2"
{ echo "--- stdout matches ---"; grep -inE 'sweep|reaper|kill' "$SCRATCH/boot.stdout.txt" 2>/dev/null | head -40;
  echo "--- stderr matches ---"; grep -inE 'sweep|reaper|kill' "$SCRATCH/boot.stderr.txt" 2>/dev/null | head -40; } >> "$T"

log "livez body: ${LIVEZ_BODY:-<NONE>}"
log "readyz body: ${READYZ_BODY:-<NONE>}"
log "=== STAGE B COMPLETE ==="
exit 0
