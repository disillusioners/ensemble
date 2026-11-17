#!/bin/bash
# S5 stage B3 — THE BOOT (evidence run).
# Disclosed deviations from plan §5.4.3 (all logged in the transcript):
#   D1: + ENSEMBLE_DB_AMBIENT_ALLOW=1   (fence-sanctioned; plan omits it -> exit 78)
#   D2: + family-wide ambient ENSEMBLE_* scrub (plan's 35-name list omits it;
#       the 7 ambient ENSEMBLE_AUTH_* turn auth ON with a non-same-origin
#       redirect base -> exit 78 at the auth preflight)
#   D3: bound timeout -k 5 30 -> -k 5 $BOUND  (source-verified: ~29s lifespan
#       startup with the port bound-but-not-listening => 30s cannot reach livez)
set -uo pipefail

STATE=/tmp/gc_v0310_smoke_state.env
. "$STATE"
T="$EVID/s5-transcript.txt"
WT=/home/nea/ensemble-src-wt-group-chat-release
BOUND=180

log(){ printf '### %s :: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$T"; }
run(){ log "CMD: $*"; eval "$@" >> "$T" 2>&1; rc=$?; echo "RC=$rc" >> "$T"; echo "  [exit=$rc]"; return $rc; }

PW=$(grep -m1 '^POSTGRES_PASSWORD=' "$WT/.env" | cut -d= -f2-)
case "$PW" in
  \"*\") PW=${PW#\"}; PW=${PW%\"} ;;
  \'*\") PW=${PW#\'}; PW=${PW%\'} ;;
esac

log "=== STAGE B3: BOOT (evidence run) ==="
log "D1/D2/D3 disclosed above; bound=${BOUND}s"

# ── family-wide ambient ENSEMBLE_* scrub (house doctrine: FAMILY-WIDE, not enumerated) ──
ENS_SCRUB=""
for n in $(env | sed -n 's/^\(ENSEMBLE_[A-Za-z0-9_]*\)=.*/\1/p' | sort -u); do
  ENS_SCRUB="$ENS_SCRUB -u $n"
done
log "D2 ambient ENSEMBLE_* names scrubbed (count=$(echo $ENS_SCRUB | wc -w)):"
log "D2 names: $(echo $ENS_SCRUB | tr ' ' '\n' | grep -v '^$' | sed 's/^-u //' | sort | tr '\n' ' ')"

REAL="env -u POSTGRES_URL -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB \
    -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u DATABASE_URL -u DATABASE_URL_POSTGRES \
    -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD -u PGPASSFILE \
    -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE -u PGSSLCERT -u PGSSLKEY \
    -u PGSSLROOTCERT -u PGCONNECT_TIMEOUT -u PGAPPNAME -u PGOPTIONS -u PGDATA \
    -u PORT -u DAEMON_PORT -u HOST -u LIMITS_METRICS_LISTENER_PORT \
    -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR -u DATA_DIR \
    -u PLANE_MCP_WORKSPACE_SLUG -u PLANE_BASE_URL \
    $ENS_SCRUB \
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
    ENSEMBLE_DB_AMBIENT_ALLOW=1 \
    setsid timeout -k 5 $BOUND '$SCRATCH/payload/ensemble-prod'"

log "CMD (REDACTED — password withheld): $(echo "$REAL" | tr '\n' ' ' | sed "s/POSTGRES_PASSWORD='[^']*'/POSTGRES_PASSWORD='<REDACTED len=${#PW}>'/")"

DAEMON_PID=""; LIVEZ_BODY=""; READYZ_BODY=""; LIVEZ_CODE=""; READYZ_CODE=""; LIVZ_FIRST=""; RDYZ_FIRST=""
T0=$(date +%s)

XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemd-run --user --scope --quiet --unit=gc-s5-smoke \
  /bin/bash -c "$REAL" > "$SCRATCH/boot.stdout.txt" 2> "$SCRATCH/boot.stderr.txt" &
BOOT_PID=$!
echo "BOOT_PID=$BOOT_PID" >> "$STATE"
log "boot launched in background: systemd-run client pid=$BOOT_PID, scope unit gc-s5-smoke"

for i in $(seq 1 $((BOUND+5))); do
  if [ -z "$LIVEZ_BODY" ]; then
    LB=$(curl -s -o /tmp/.s5_livez.$$ -w '%{http_code}' --max-time 2 http://127.0.0.1:19101/livez 2>/dev/null || echo "000")
    if [ "$LB" = "200" ]; then
      LIVEZ_BODY=$(cat /tmp/.s5_livez.$$); LIVEZ_CODE="$LB"; LIVZ_FIRST=$(( $(date +%s) - T0 ))
      log "livez FIRST SUCCESS t=${LIVZ_FIRST}s HTTP 200 body: $LIVEZ_BODY"
      cp /tmp/.s5_livez.$$ "$EVID/livez-body.json" 2>/dev/null
    fi
  fi
  if [ -z "$READYZ_BODY" ]; then
    RB=$(curl -s -o /tmp/.s5_readyz.$$ -w '%{http_code}' --max-time 2 http://127.0.0.1:19101/readyz 2>/dev/null || echo "000")
    if [ "$RB" = "200" ]; then
      READYZ_BODY=$(cat /tmp/.s5_readyz.$$); READYZ_CODE="$RB"; RDYZ_FIRST=$(( $(date +%s) - T0 ))
      log "readyz FIRST SUCCESS t=${RDYZ_FIRST}s HTTP 200 body: $READYZ_BODY"
      cp /tmp/.s5_readyz.$$ "$EVID/readyz-body.json" 2>/dev/null
    fi
  fi
  if [ -z "$DAEMON_PID" ]; then
    P=$(ss -ltnp 2>/dev/null | grep ':19101' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
    if [ -n "${P:-}" ]; then
      DAEMON_PID="$P"
      log "ISOLATION PROOF — listener pid=$DAEMON_PID (t=$(( $(date +%s) - T0 ))s)"
      log "  /proc/$DAEMON_PID/cgroup :: $(cat /proc/$DAEMON_PID/cgroup 2>/dev/null | tr '\n' ' ')"
      log "  /proc/$DAEMON_PID/exe   :: $(readlink -f /proc/$DAEMON_PID/exe 2>/dev/null)"
      { echo "pid=$DAEMON_PID"; echo "cgroup:"; cat /proc/$DAEMON_PID/cgroup 2>/dev/null; echo "exe: $(readlink -f /proc/$DAEMON_PID/exe 2>/dev/null)"; } > "$EVID/cgroup-isolation.txt"
    fi
  fi
  if [ -n "$LIVEZ_BODY" ] && [ -n "$READYZ_BODY" ]; then log "both probes green at t=$(( $(date +%s) - T0 ))s"; break; fi
  if ! kill -0 "$BOOT_PID" 2>/dev/null; then log "boot client exited at t=$(( $(date +%s) - T0 ))s (poll aborted)"; break; fi
  sleep 1
done
rm -f /tmp/.s5_livez.$$ /tmp/.s5_readyz.$$
log "poll done: livez=${LIVEZ_CODE:-none}(first@${LIVZ_FIRST:-n/a}s) readyz=${READYZ_CODE:-none}(first@${RDYZ_FIRST:-n/a}s)"

log "waiting for the ${BOUND}s bound to expire (or the daemon to exit)..."
WAIT=0
while kill -0 "$BOOT_PID" 2>/dev/null && [ "$WAIT" -lt 30 ]; do sleep 1; WAIT=$((WAIT+1)); done
if kill -0 "$BOOT_PID" 2>/dev/null; then
  log "boot client still alive after ${WAIT}s — sending SIGTERM to the systemd-run client only (scope teardown)"
  kill -TERM "$BOOT_PID" 2>/dev/null
  sleep 3
fi
wait "$BOOT_PID" 2>/dev/null; BOOT_RC=$?
log "boot exit rc=$BOOT_RC"

run "ss -ltn | grep 19101 || echo 'CONFIRMED: no listener on 19101'"
run "pgrep -af '$SCRATCH/payload/ensemble-prod' || echo 'CONFIRMED: no scratch payload process remains'"
run "cat /proc/self/cgroup"

log "--- boot.stdout.txt (full) ---"; cat "$SCRATCH/boot.stdout.txt" >> "$T" 2>&1
log "--- boot.stderr.txt (full) ---"; cat "$SCRATCH/boot.stderr.txt" >> "$T" 2>&1
N1=$(grep -ciE 'sweep|reaper|kill' "$SCRATCH/boot.stdout.txt" 2>/dev/null || true); N1=${N1:-0}
N2=$(grep -ciE 'sweep|reaper|kill' "$SCRATCH/boot.stderr.txt" 2>/dev/null || true); N2=${N2:-0}
log "sweep|reaper|kill census: stdout=$N1 stderr=$N2"
{ echo "--- stdout matches ---"; grep -inE 'sweep|reaper|kill' "$SCRATCH/boot.stdout.txt" 2>/dev/null | head -40;
  echo "--- stderr matches ---"; grep -inE 'sweep|reaper|kill' "$SCRATCH/boot.stderr.txt" 2>/dev/null | head -40; } >> "$T"

# hygiene: did the ambient auth client secret leak into the scratch logs?
SEC=$(printenv ENSEMBLE_AUTH_CLIENT_SECRET 2>/dev/null || true)
if [ -n "$SEC" ]; then
  H1=$(grep -cF "$SEC" "$SCRATCH/boot.stdout.txt" 2>/dev/null || true); H1=${H1:-0}
  H2=$(grep -cF "$SEC" "$SCRATCH/boot.stderr.txt" 2>/dev/null || true); H2=${H2:-0}
  log "HYGIENE: ambient ENSEMBLE_AUTH_CLIENT_SECRET occurrences in scratch boot logs: stdout=$H1 stderr=$H2 (0 = not leaked)"
fi

log "livez body: ${LIVEZ_BODY:-<NONE>}"
log "readyz body: ${READYZ_BODY:-<NONE>}"
log "=== STAGE B3 COMPLETE ==="
exit 0
