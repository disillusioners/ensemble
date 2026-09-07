#!/bin/bash
# 2026-09-07 LCA judge boot smoke — merge gate Job 6/6
# Branch feature/leader-completion-attestation @ d6e30d9d (relaxed drift gate allows
# H = d6e30d9d + .agents/tester/RESULTS/ evidence commits only).
#
# Asserts, on a REAL daemon boot against a DISPOSABLE PG database:
#   (i)   boot-log judge-flag resolution line (llm_judge_enabled=true)
#   (ii)  resolved judge model (OPENAI_MODEL_KEYWORDS -> OPENAI_MODEL fallback)
# plus: dev.sh graceful-shutdown static check, clean shutdown, zero leftovers.
# Deny-drive (step 7) is SKIPPED by design — see SKIP note in PHASE 6.
#
# Run:  timeout 300 bash .agents/tester/RESULTS/2026-09-07-lca-judge-boot-smoke.sh
# Safety: kills ONLY pids this script recorded; never touches port 8088 or
#         ensemble_prod; DB name is pinned to ensemble_smoke_lcajudge_d6e30d9d.

set -u
SCRIPT_START=$(date +%s)
INTERNAL_DEADLINE=$((SCRIPT_START + 270))   # leave cleanup room before outer timeout 300

WORKTREE="/Users/nguyenminhkha/All/Code/opensource-projects/ens-lca-judge"
BASE_COMMIT="d6e30d9d"
DB="ensemble_smoke_lcajudge_d6e30d9d"
PGHOST="localhost"; PGPORT="5432"; PGUSER="ensemble"
DSN="postgresql://${PGUSER}@${PGHOST}:${PGPORT}/${DB}"
SMOKE_DATA="$WORKTREE/data_smoke_lcajudge"
PIDFILE="/tmp/lca_judge_smoke_pids.txt"
OUTLOG="/tmp/lca_judge_boot_stdout.log"
: > "$PIDFILE"

PASS=0; FAIL=0
DAEMON_PID=""; CHILD_PIDS=""; PORT=""; BOOTLOG=""

ok()   { echo "[PASS] $*"; PASS=$((PASS+1)); }
bad()  { echo "[FAIL] $*"; FAIL=$((FAIL+1)); }
note() { echo "[INFO] $*"; }
time_left() { [ "$(date +%s)" -lt "$INTERNAL_DEADLINE" ]; }

cleanup() {
  echo ""
  echo "===== CLEANUP (SIGTERM/SIGKILL only pids THIS smoke recorded) ====="
  if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
    kill -TERM "$DAEMON_PID" 2>/dev/null
    for _ in $(seq 1 24); do
      kill -0 "$DAEMON_PID" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$DAEMON_PID" 2>/dev/null; then
      note "SIGTERM insufficient -> SIGKILL $DAEMON_PID"
      kill -KILL "$DAEMON_PID" 2>/dev/null
    fi
  fi
  for p in $CHILD_PIDS; do
    if kill -0 "$p" 2>/dev/null; then
      note "sweeping recorded child pid $p"
      kill -TERM "$p" 2>/dev/null; sleep 1
      kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null
    fi
  done
  sleep 1
  if lsof -nP -i ":${PORT:-8079}" >/dev/null 2>&1; then
    bad "port ${PORT:-8079} STILL BOUND after shutdown"
  else
    ok "port ${PORT:-8079} freed"
  fi
  if psql -h "$PGHOST" -p "$PGPORT" -d postgres -tc \
       "SELECT 1 FROM pg_database WHERE datname='$DB'" 2>/dev/null | grep -q 1; then
    if psql -h "$PGHOST" -p "$PGPORT" -d postgres -c "DROP DATABASE IF EXISTS \"$DB\"" >/dev/null 2>&1; then
      ok "dropped disposable DB $DB"
    else
      bad "could not drop $DB"
    fi
  else
    note "DB $DB absent at cleanup (already dropped or never created)"
  fi
  ELAPSED=$(( $(date +%s) - SCRIPT_START ))
  echo ""
  echo "===== SMOKE RESULT: PASS=$PASS FAIL=$FAIL runtime=${ELAPSED}s ====="
  [ "$FAIL" -eq 0 ] || echo "SMOKE-FAIL"
}
trap cleanup EXIT INT TERM

echo "===== PHASE 0: preflight (relaxed drift gate) ====="
cd "$WORKTREE" || { bad "cannot cd worktree"; exit 1; }
BRANCH=$(git rev-parse --abbrev-ref HEAD)
HEAD_SHORT=$(git rev-parse --short HEAD)
note "branch=$BRANCH HEAD=$HEAD_SHORT"
git merge-base --is-ancestor "$BASE_COMMIT" HEAD; ANC=$?
DIFFNAMES=$(git diff --name-only "${BASE_COMMIT}..HEAD")
DIFF_CLEAN=1
if [ -n "$DIFFNAMES" ]; then
  if echo "$DIFFNAMES" | grep -qv '^\.agents/tester/RESULTS/'; then DIFF_CLEAN=0; fi
  note "delta files:"; echo "$DIFFNAMES" | sed 's/^/  /'
fi
if [ "$ANC" -eq 0 ] && [ "$DIFF_CLEAN" -eq 1 ]; then
  ok "drift gate: $BASE_COMMIT ancestor of $HEAD_SHORT AND delta only .agents/tester/RESULTS/"
else
  bad "drift gate FAILED (ancestor_exit=$ANC diff_clean=$DIFF_CLEAN)"
  exit 1
fi

echo "===== PHASE 1: port pick (never 8088) ====="
for p in 8079 8090 8091; do
  if ! lsof -nP -i ":$p" >/dev/null 2>&1; then PORT=$p; break; fi
done
if [ -n "$PORT" ]; then note "using dev-range port $PORT (verified free)"; else
  bad "no free port among 8079/8090/8091"; exit 1; fi
if lsof -nP -i :8088 >/dev/null 2>&1; then note ":8088 occupied (self-system) — UNTOUCHED by this smoke"; fi

echo "===== PHASE 2: disposable PG ====="
if psql -h "$PGHOST" -p "$PGPORT" -d postgres -tc \
     "SELECT 1 FROM pg_database WHERE datname='$DB'" 2>/dev/null | grep -q 1; then
  note "$DB pre-exists (stale) — dropping"
  psql -h "$PGHOST" -p "$PGPORT" -d postgres -c "DROP DATABASE \"$DB\"" || { bad "drop stale $DB"; exit 1; }
fi
if psql -h "$PGHOST" -p "$PGPORT" -d postgres -c \
     "CREATE DATABASE \"$DB\" OWNER \"$PGUSER\"" >/dev/null 2>&1; then
  ok "created disposable DB $DB (owner $PGUSER, host $PGHOST:$PGPORT)"
else
  bad "CREATE DATABASE $DB failed"; exit 1
fi

echo "===== PHASE 3: boot REAL daemon from worktree (uvicorn, NO --reload) ====="
note "deviation from dev.sh: --reload omitted — siblings commit RESULTS/ evidence files
  concurrently; a reloader watching *.py could restart the daemon mid-smoke.
  Everything else mirrors dev.sh (port env, log level, --no-access-log,
  --timeout-graceful-shutdown 10, DATA_DIR/ENSEMBLE_DATA_DIR/PERSISTENCE_DB_PATH)."
rm -rf "$SMOKE_DATA"; mkdir -p "$SMOKE_DATA"
nohup env \
  POSTGRES_URL="$DSN" \
  POSTGRES_HOST="$PGHOST" \
  POSTGRES_PORT="$PGPORT" \
  POSTGRES_DB="$DB" \
  POSTGRES_USER="$PGUSER" \
  DATA_DIR="$SMOKE_DATA" \
  ENSEMBLE_DATA_DIR="$SMOKE_DATA" \
  PERSISTENCE_DB_PATH="$SMOKE_DATA/instances.db" \
  PORT="$PORT" \
  .venv/bin/python -m uvicorn daemon.api:app \
    --host 127.0.0.1 --port "$PORT" \
    --log-level info --no-access-log --timeout-graceful-shutdown 10 \
  > "$OUTLOG" 2>&1 &
DAEMON_PID=$!
echo "$DAEMON_PID" >> "$PIDFILE"
note "boot pid=$DAEMON_PID dsn=$DSN (disposable; POSTGRES_URL covers persistence.py resolver,
  POSTGRES_HOST/PORT/DB/USER cover repositories/factory.py::create_postgres_engine —
  that engine does NOT read POSTGRES_URL; run#1 lesson 2026-09-07)"

echo "===== PHASE 4: health poll (/livez) ====="
HEALTHY=0; i=0
while time_left; do
  i=$((i+1))
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/livez" 2>/dev/null)
  if [ "$code" = "200" ]; then HEALTHY=1; break; fi
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    note "daemon exited early; stdout tail:"; tail -40 "$OUTLOG"
    break
  fi
  sleep 1
done
if [ "$HEALTHY" = "1" ]; then ok "/livez 200 after ~${i}s"; else
  bad "daemon never healthy on :$PORT"; exit 1; fi
curl -s -o /dev/null -w "/docs http_code=%{http_code}\n" "http://127.0.0.1:$PORT/docs"
note "/livez body: $(curl -s "http://127.0.0.1:$PORT/livez" | head -c 200)"
CHILD_PIDS=$(pgrep -P "$DAEMON_PID" 2>/dev/null | tr '\n' ' ')
for c in $CHILD_PIDS; do echo "$c" >> "$PIDFILE"; done
note "recorded uvicorn child pids: ${CHILD_PIDS:-<none>}"

echo "===== PHASE 5: BOOT-LOG ASSERTIONS (MUST-deliver) ====="
BOOTLOG="$WORKTREE/data/logs/ensemble.log"
[ -f "$BOOTLOG" ] || BOOTLOG="$SMOKE_DATA/logs/ensemble.log"
[ -f "$BOOTLOG" ] || BOOTLOG="$OUTLOG"
note "bootlog under assertion: $BOOTLOG ($(wc -l < "$BOOTLOG" | tr -d ' ') lines)"
sleep 2   # let InstanceManager boot-log flush
BOOTLOG="$WORKTREE/data/logs/ensemble.log"
[ -f "$BOOTLOG" ] || BOOTLOG="$SMOKE_DATA/logs/ensemble.log"
[ -f "$BOOTLOG" ] || BOOTLOG="$OUTLOG"
note "re-check after flush: $BOOTLOG ($(wc -l < "$BOOTLOG" | tr -d ' ') lines)"

# (0) engine line proves the disposable DB was actually used (TRAP guard)
ENGINE_LINE=$(grep -n "Creating PostgreSQL engine" "$BOOTLOG" | head -1)
if [ -n "$ENGINE_LINE" ] && echo "$ENGINE_LINE" | grep -q "$DB"; then
  ok "engine line pins disposable DB — $ENGINE_LINE"
else
  bad "engine line missing or not pinned to $DB — got: ${ENGINE_LINE:-<none>}"
fi

# (i) judge-flag resolution line
JLINE=$(grep -n "Leader completion attestation resolved" "$BOOTLOG" | head -1)
if [ -n "$JLINE" ]; then
  ok "attestation boot line found:"
  echo "$JLINE" | sed 's/^/    LITERAL: /'
  if echo "$JLINE" | grep -q "llm_judge_enabled=true"; then
    ok "judge flag ENABLED in boot line (llm_judge_enabled=true)"
  else
    bad "judge flag not enabled in boot line"
  fi
else
  bad "NO 'Leader completion attestation resolved' line in $BOOTLOG"
  note "case-insensitive judge greps for diagnosis:"
  grep -in "attestation" "$BOOTLOG" | grep -i "judge" | head -5 | sed 's/^/  /'
  grep -inE "judge.*enabled|LLM_JUDGE" "$BOOTLOG" | head -5 | sed 's/^/  /'
fi

# (ii) resolved judge model on the same line
MODEL=$(echo "$JLINE" | sed -E 's/.*llm_judge_model=([^ ]+).*/\1/')
if [ -n "$MODEL" ] && [ "$MODEL" != "$JLINE" ] && [ "$MODEL" != "<disabled>" ] && [ "$MODEL" != "<unresolved>" ]; then
  ok "resolved judge model = $MODEL"
else
  bad "resolved judge model absent/placeholder — got: '${MODEL:-<none>}'"
fi
note "env echo on line (expected ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=<unset> => default ON):"
echo "$JLINE" | grep -o "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=[^)]*" | sed 's/^/  /'

echo "===== PHASE 6: deny-drive — SKIPPED (documented escape clause) ====="
note "SKIP REASON: the judge deny path fires only when a REAL delegated leader
  mission runs to an un-attested end (multi-minute multi-instance LLM drive).
  (a) the <=300s budget is consumed by boot+assertions; (b) OPENAI_BASE_URL is a
  single shared endpoint for agent AND judge traffic — stubbing it starves the
  leader's own planning calls (no per-model base-url split for the quick model),
  so 'cheap' is not achievable. Boot-log confirmation above is the MUST-deliver
  and covers both judge flag and resolved model."

echo "===== PHASE 7: dev.sh graceful-shutdown static check ====="
GS_LINE=$(grep -n -- "--timeout-graceful-shutdown 10" "$WORKTREE/dev.sh" | head -1)
if [ -n "$GS_LINE" ]; then ok "dev.sh graceful-shutdown flag present: $GS_LINE"; else
  bad "--timeout-graceful-shutdown 10 NOT found in dev.sh"; fi

echo "===== PHASE 8: clean shutdown (via EXIT trap) + leftover checks ====="
note "shutdown executed by cleanup trap; post-verification in orchestrator notes"
exit 0
