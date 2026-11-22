#!/usr/bin/env bash
# F2 measurement-evidence collection — engine boot posture GATED block.
# FENCED dev boots at pristine lane base 8a1045c02. ZERO tracked-file edits.
# Usage: .f2-transcripts/f2_boot.sh <idx> <port>
set -u

WT=/home/nea/ensemble-src-wt-engine-flipprep
IDX="$1"
PORT="$2"
OUT="$WT/.f2-transcripts"
T="$OUT/boot-${IDX}.txt"
cd "$WT" || exit 1

{
  echo "=============================================================="
  echo "F2 EVIDENCE TRANSCRIPT — boot-${IDX}"
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "worktree=$WT"
  echo "git_head=$(git rev-parse HEAD)"
  echo "git_branch=$(git rev-parse --abbrev-ref HEAD)"
  echo "git_status_short_begin>>>"
  git status --short
  echo "git_status_short_end<<<"
  echo "python=$("$WT/.venv/bin/python" -V 2>&1)"
  echo "--------------------------------------------------------------"
  echo "LAUNCH COMMAND (verbatim; POSTGRES_USER/POSTGRES_PASSWORD sourced"
  echo "from ./.env and deliberately NOT echoed):"
  cat <<'CMD'
cd /home/nea/ensemble-src-wt-engine-flipprep && \
  set -a; . ./.env; set +a; \
  export PORT=<PORT> DAEMON_PORT=<PORT> \
    POSTGRES_DB=ensemble_dev POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 \
    ENSEMBLE_SELF_ENV=dev \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    ENSEMBLE_DB_AMBIENT_ALLOW=1 \
    DATA_DIR=./data_dev ENSEMBLE_DATA_DIR=./data_dev PERSISTENCE_DB_PATH=./data_dev/instances.db \
    .venv/bin/python -m daemon
CMD
  echo "--------------------------------------------------------------"
  echo "EFFECTIVE FENCE (as seen by the launched process):"
  env -i HOME="$HOME" PATH="$PATH" bash -c '
    set -a; . ./.env; set +a
    export PORT='"$PORT"' DAEMON_PORT='"$PORT"' \
      POSTGRES_DB=ensemble_dev POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 \
      ENSEMBLE_SELF_ENV=dev \
      ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
      ENSEMBLE_DB_AMBIENT_ALLOW=1 \
      DATA_DIR=./data_dev ENSEMBLE_DATA_DIR=./data_dev PERSISTENCE_DB_PATH=./data_dev/instances.db
    for v in PORT DAEMON_PORT POSTGRES_DB POSTGRES_HOST POSTGRES_PORT POSTGRES_USER ENSEMBLE_SELF_ENV \
             ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES ENSEMBLE_DB_AMBIENT_ALLOW DATA_DIR ENSEMBLE_DATA_DIR PERSISTENCE_DB_PATH; do
      if [ "$v" = "POSTGRES_USER" ]; then echo "$v=<set-from-.env>"; else eval "echo \"$v=\$$v\""; fi
    done
    if [ "$POSTGRES_DB" = "ensemble_prod" ]; then echo "FENCE_VIOLATION=POSTGRES_DB_IS_PROD"; exit 1; fi
    if [ "$ENSEMBLE_SELF_ENV" != "dev" ]; then echo "FENCE_VIOLATION=SELF_ENV_NOT_DEV"; exit 1; fi
    if [ "$PORT" = "8079" ] || [ "$PORT" = "9797" ]; then echo "FENCE_VIOLATION=PORT_FORBIDDEN"; exit 1; fi
    echo "FENCE_ASSERT=PASS"
  ' 2>&1
  echo "--------------------------------------------------------------"
  echo "DAEMON STDOUT+STDERR FOLLOWS"
  echo "=============================================================="
} > "$T"

# ---- launch (fresh PORT per boot) -------------------------------------
(
  set -a; . ./.env; set +a
  export PORT="$PORT" DAEMON_PORT="$PORT" \
    POSTGRES_DB=ensemble_dev POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 \
    ENSEMBLE_SELF_ENV=dev \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    ENSEMBLE_DB_AMBIENT_ALLOW=1 \
    DATA_DIR=./data_dev ENSEMBLE_DATA_DIR=./data_dev PERSISTENCE_DB_PATH=./data_dev/instances.db
  exec .venv/bin/python -m daemon
) >> "$T" 2>&1 &

PID=$!
START_MS=$(date +%s%3N)
{
  echo "--------------------------------------------------------------"
  echo "RECORDED_PID=$PID"
  echo "RECORDED_PID_START=$(ps -o lstart= -p "$PID" 2>/dev/null)"
  echo "RECORDED_PID_CMD=$(tr '\0' ' ' < /proc/"$PID"/cmdline 2>/dev/null)"
  echo "LAUNCH_EPOCH_MS=$START_MS"
  echo "CHILDREN_AT_LAUNCH=$(pgrep -P "$PID" 2>/dev/null | tr '\n' ' ')"
} >> "$T"

# ---- poll livez then readyz every 2s ----------------------------------
LIVEZ_AT=""; READYZ_AT=""; POSTURE_SEEN=0
for i in $(seq 1 120); do
  sleep 2
  NOW=$(date +%s%3N)
  if [ -z "$LIVEZ_AT" ]; then
    if curl -sf -m 3 "http://127.0.0.1:${PORT}/livez" -o "$OUT/.livez-${IDX}.json" 2>/dev/null; then
      LIVEZ_AT=$((NOW - START_MS))
      echo "PROBE livez GREEN at +${LIVEZ_AT}ms (t+${i}x2s) body=$(cat "$OUT/.livez-${IDX}.json" 2>/dev/null)" >> "$T"
    fi
  fi
  if [ -n "$LIVEZ_AT" ] && [ -z "$READYZ_AT" ]; then
    if curl -sf -m 5 "http://127.0.0.1:${PORT}/readyz" -o "$OUT/.readyz-${IDX}.json" 2>/dev/null; then
      READYZ_AT=$((NOW - START_MS))
      echo "PROBE readyz GREEN at +${READYZ_AT}ms (t+${i}x2s) body=$(cat "$OUT/.readyz-${IDX}.json" 2>/dev/null)" >> "$T"
    fi
  fi
  # posture line present?
  if grep -q "engine boot posture" "$T" 2>/dev/null; then POSTURE_SEEN=1; fi
  if [ -n "$READYZ_AT" ] && [ "$POSTURE_SEEN" = "1" ]; then break; fi
  # early exit if the daemon died
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "PROBE daemon process died before readyz (pid $PID gone at +$((NOW-START_MS))ms)" >> "$T"
    break
  fi
done

# ---- posture extraction ----------------------------------------------
POSTURE_LINE=$(grep -h "engine boot posture" "$T" 2>/dev/null | head -1)
BOUND_LINE=$(grep -h "Uvicorn running on" "$T" 2>/dev/null | head -1)

# ---- terminate ONLY the recorded PID ----------------------------------
kill -TERM "$PID" 2>/dev/null
DEAD=0
for i in $(seq 1 15); do
  sleep 1
  if ! kill -0 "$PID" 2>/dev/null; then DEAD=1; break; fi
done
if [ "$DEAD" = "0" ]; then
  echo "TERM did not stop pid $PID within 15s -> SIGKILL" >> "$T"
  kill -KILL "$PID" 2>/dev/null
  for i in $(seq 1 10); do sleep 1; kill -0 "$PID" 2>/dev/null || { DEAD=1; break; }; done
fi
wait "$PID" 2>/dev/null
RC=$?

{
  echo "--------------------------------------------------------------"
  echo "TERMINATION: pid=$PID dead=$DEAD exit_code=$RC"
  echo "PORT_STILL_LISTENING=$( (ss -ltn 2>/dev/null | grep -c ":${PORT} ") )"
  echo "LIVEZ_GREEN_MS=${LIVEZ_AT:-NEVER}"
  echo "READYZ_GREEN_MS=${READYZ_AT:-NEVER}"
  echo "POSTURE_LINE=${POSTURE_LINE:-MISSING}"
  echo "BOUND_LINE=${BOUND_LINE:-MISSING}"
  echo "PROD_REFERENCE_SCAN=$(grep -c 'ensemble_prod' "$WT/.f2-transcripts/boot-${IDX}.txt" 2>/dev/null || true) hits_in_transcript"
  echo "PERSISTED_ENSEMBLE_JSON=$( [ -f "$WT/data_dev/ensemble.json" ] && tr -d '\n' < "$WT/data_dev/ensemble.json" || echo ABSENT )"
  echo "PERSISTED_JSON_PROD_HITS=$( [ -f "$WT/data_dev/ensemble.json" ] && grep -c 'ensemble_prod' "$WT/data_dev/ensemble.json" || echo 0 )"
  echo "REPO_DATA_DIR_EXISTS=$([ -d "$WT/data" ] && echo YES || echo NO)"
  echo "ended_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "=============================================================="
} >> "$T"

echo "boot-${IDX} port=${PORT} pid=${PID} dead=${DEAD} rc=${RC} livez=${LIVEZ_AT:-NEVER}ms readyz=${READYZ_AT:-NEVER}ms"
echo "  posture: ${POSTURE_LINE:-MISSING}"
