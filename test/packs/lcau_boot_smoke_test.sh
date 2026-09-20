#!/usr/bin/env bash
# Test Pack: lcau_boot_smoke_test — LCA user-intent merge gate, SEGMENT 2 (boot smoke).
#
# Purpose: boot-smoke of the user-intent branch (feature/lca-judge-user-intent @ 47b56df8,
# base a6442bff): the fused judge bundle must carry SOURCE U (the user's original request)
# on a completion that is ALLOWED through the REAL graph + fused judge path.
#
# - real daemon process (uvicorn, NO --reload; NEVER dev.sh — hits prod defaults)
# - bound to 127.0.0.1:18079 via BOTH the config knob (daemon/config.py DaemonConfig,
#   env_prefix DAEMON_ → DAEMON_PORT) and the uvicorn --port flag (kept consistent)
# - mock OpenAI-compatible LLM endpoint (reuses test/packs/lca2_helpers/mock_llm.py
#   READ-ONLY — its role-detection key "fused evidence bundle" is intact at 47b56df8;
#   verdict scripting: judge#1 not_complete → deny+nudge, judge#2 complete → allow+END)
# - disposable PG14 on the first free port of 15434-15439 (disposable band; NEVER 5432)
# - attestation at ENFORCE DEFAULT: NO ENSEMBLE_LEADER_ATTESTATION_* env is set
# - U-PRESENT WITNESS: event=leader_completion_resolver_eval rows must carry
#   bundle_u_chars>0 AND user_message_included=True; the completing pass must show
#   the full chain bundle_u_chars>0 user_message_included=True judge_invoked=True
#   judge_verdict=complete resolver_outcome=allow
#
# Drift pin: HEAD == 47b56df8 AND a6442bff is an ancestor. (Unlike the lca2 pack,
# a non-empty production delta vs base is EXPECTED here — it IS the feature.)
#
# PORT SAFETY: 5432 (prod PG), 8079 (prod API), 8088 (self-system) NEVER touched.
# Daemon port 18079 must be FREE pre-boot — if occupied by an unrecorded PID the
# pack FAILS with lsof evidence (never kills unknown owners).
#
# Run:  timeout 300 bash test/packs/lcau_boot_smoke_test.sh
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer): `timeout 300` wrapper by the caller
#   Layer 2 (inner): INTERNAL_DEADLINE = start + 270s enforced in every wait loop;
#                    breach → RESULT: TIMEOUT, exit 124
# Hard cap: 5 minutes per pack execution.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
MOCK_LLM_SCRIPT="$SCRIPT_DIR/lca2_helpers/mock_llm.py"

PIN_COMMIT="e0d15e93"   # stale-A-fix tip (was 47b56df8 user-intent tip); BASE_COMMIT a6442bff is still ancestor
BASE_COMMIT="a6442bff"
DAEMON_PORT=18079
PG_USER=ensemble
PG_DB="lcau_smoke"
SCRATCH="/tmp/lcau-boot-smoke"
PGDATA="$SCRATCH/pgdata"
PG_LOG="$SCRATCH/pg.log"
DATA_DIR="$SCRATCH/data"
DAEMON_STDOUT="$SCRATCH/daemon.stdout"
DAEMON_LOG="$DATA_DIR/logs/ensemble.log"
MOCK_LLM_LOG="$SCRATCH/mock_llm.log"
PIDS_FILE="$SCRATCH/pids.txt"
MOCK_LLM_PORT=""   # resolved in preflight (15780-15789 first free)
PG_PORT=""         # resolved in preflight (15434-15439 first free)

DAEMON_PID=""
MOCK_LLM_PID=""
PG_RUN_BY_US=0
TIMEOUT_FLAG=0

PASS=0
FAIL=0
START_TS=$(date +%s)
INTERNAL_DEADLINE=$((START_TS + 270))   # ≤280s internal deadline; cleanup room before outer 300

# ── helpers ─────────────────────────────────────────────────────────────
ok()   { echo "[PASS] $*"; PASS=$((PASS+1)); }
bad()  { echo "[FAIL] $*"; FAIL=$((FAIL+1)); }
note() { echo "[INFO] $*"; }

time_left() { [ "$(date +%s)" -lt "$INTERNAL_DEADLINE" ]; }

deadline_hit() {
  TIMEOUT_FLAG=1
  bad "internal deadline (${INTERNAL_DEADLINE}s) exceeded"
}

record_pid() { echo "$1|$2" >> "$PIDS_FILE"; }

kill_recorded() {
  while IFS='|' read -r pid name; do
    [ -n "$pid" ] || continue
    if kill -0 "$pid" 2>/dev/null; then
      note "teardown: SIGTERM $name pid=$pid"
      kill -TERM "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      kill -0 "$pid" 2>/dev/null && { note "teardown: SIGKILL $name pid=$pid"; kill -KILL "$pid" 2>/dev/null || true; }
    fi
  done < "$PIDS_FILE"
}

cleanup() {
  local rc=$?
  if [ "$TIMEOUT_FLAG" = "1" ] && [ "$rc" = "0" ]; then rc=124; fi
  echo ""
  echo "===== CLEANUP (rc=$rc) ====="
  kill_recorded
  sleep 1
  # Port-freedom verification for ports we own (kill ONLY recorded PIDs)
  for p in $DAEMON_PORT $MOCK_LLM_PORT; do
    [ -n "$p" ] || continue
    local port_pid
    port_pid=$(lsof -ti:"$p" 2>/dev/null | head -1)
    if [ -n "$port_pid" ]; then
      if grep -q "^${port_pid}|" "$PIDS_FILE" 2>/dev/null; then
        note "port $p still bound by recorded pid=$port_pid — SIGKILL"
        kill -KILL "$port_pid" 2>/dev/null || true
        sleep 1
      else
        note "port $p bound by UNRECORDED pid=$port_pid — leaving alone"
      fi
    fi
    if lsof -nP -i ":$p" >/dev/null 2>&1; then
      bad "port $p STILL bound after teardown"
    else
      ok "port $p freed (lsof verified)"
    fi
  done
  # PG teardown (only if we started it)
  if [ "$PG_RUN_BY_US" = "1" ] && [ -d "$PGDATA" ]; then
    note "PG teardown: pg_ctl stop + rm -rf $PGDATA"
    /opt/homebrew/opt/postgresql@14/bin/pg_ctl -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
    rm -rf "$PGDATA" "$PG_LOG" >/dev/null 2>&1 || true
  fi
  if [ -d "$PGDATA" ]; then bad "PGDATA $PGDATA not removed"; else ok "PGDATA removed"; fi
  if [ -d "$DATA_DIR" ]; then
    note "rm -rf $DATA_DIR"
    rm -rf "$DATA_DIR" >/dev/null 2>&1 || true
  fi
  if [ -d "$DATA_DIR" ]; then bad "DATA_DIR $DATA_DIR not removed"; else ok "DATA_DIR removed"; fi
  # Orphan check: no recorded PID may survive
  ORPHANS=0
  while IFS='|' read -r pid name; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      bad "orphan PID alive: $name pid=$pid"
      ORPHANS=$((ORPHANS+1))
    fi
  done < "$PIDS_FILE"
  [ "$ORPHANS" = "0" ] && ok "no orphan PIDs from this pack" || true
  rm -f "$PIDS_FILE" "$SCRATCH"/*.pid 2>/dev/null || true
  ELAPSED=$(( $(date +%s) - START_TS ))
  echo ""
  echo "===== PACK RESULT: PASS=$PASS FAIL=$FAIL runtime=${ELAPSED}s ====="
  if [ "$TIMEOUT_FLAG" = "1" ]; then
    echo "RESULT: TIMEOUT"
    rc=124
  elif [ "$FAIL" -eq 0 ]; then
    echo "RESULT: PASS"
    rc=0
  else
    echo "RESULT: FAIL"
    rc=1
  fi
  exit $rc
}
trap cleanup EXIT INT TERM

# ── PHASE 0: preflight ──────────────────────────────────────────────────
echo "===== PHASE 0: preflight ====="
mkdir -p "$SCRATCH"
: > "$PIDS_FILE"

for tool in initdb pg_ctl pg_isready psql createdb; do
  command -v "/opt/homebrew/opt/postgresql@14/bin/$tool" >/dev/null 2>&1 || { bad "PG14 tool missing: $tool"; exit 1; }
done
[ -f "$MOCK_LLM_SCRIPT" ] || { bad "mock_llm.py missing: $MOCK_LLM_SCRIPT"; exit 1; }
[ -x "$PROJECT_DIR/.venv/bin/python" ] || { bad ".venv/bin/python missing"; exit 1; }
ok "tools present (PG14 bin, .venv python, mock_llm.py)"

# Protected ports — never touched, just observed
for protect in 5432 8079 8088; do
  if lsof -ti:"$protect" >/dev/null 2>&1; then
    note "protected port $protect in use (NOT touched by this pack)"
  fi
done

# Daemon port 18079: must be free. NEVER kill an unknown owner.
if lsof -ti:"$DAEMON_PORT" >/dev/null 2>&1; then
  bad "daemon port $DAEMON_PORT occupied by pid=$(lsof -ti:"$DAEMON_PORT" | head -1) — refusing to start"
  lsof -nP -i ":$DAEMON_PORT" | sed 's/^/    /'
  exit 1
fi
ok "daemon port $DAEMON_PORT free"

# PG port: first free in 15434-15439 (15433 belongs to the lcau/lca2 PG lane; 15432 foreign)
PG_PORT=""
for try in 15434 15435 15436 15437 15438 15439; do
  if ! lsof -ti:"$try" >/dev/null 2>&1 && ! /opt/homebrew/opt/postgresql@14/bin/pg_isready -h 127.0.0.1 -p "$try" >/dev/null 2>&1; then
    PG_PORT=$try
    break
  fi
done
[ -n "$PG_PORT" ] || { bad "no free PG port in 15434-15439"; exit 1; }
ok "PG_PORT=$PG_PORT"

# Mock LLM port: first free in 15780-15789 (15777/15778 belong to lca2 packs)
MOCK_LLM_PORT=""
for try in 15780 15781 15782 15783 15784 15785 15786 15787 15788 15789; do
  if ! lsof -ti:"$try" >/dev/null 2>&1; then
    MOCK_LLM_PORT=$try
    break
  fi
done
[ -n "$MOCK_LLM_PORT" ] || { bad "no free mock-LLM port in 15780-15789"; exit 1; }
ok "MOCK_LLM_PORT=$MOCK_LLM_PORT"

# Drift pin: exact commit + base ancestry (production delta vs base IS the feature)
cd "$PROJECT_DIR" || { bad "cannot cd $PROJECT_DIR"; exit 1; }
HEAD_SHORT=$(git rev-parse --short HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
note "branch=$BRANCH HEAD=$HEAD_SHORT"
[ "$HEAD_SHORT" = "$PIN_COMMIT" ] || { bad "HEAD=$HEAD_SHORT != pinned $PIN_COMMIT"; exit 1; }
git merge-base --is-ancestor "$BASE_COMMIT" HEAD || { bad "$BASE_COMMIT not ancestor of HEAD"; exit 1; }
ok "drift pin: HEAD==$PIN_COMMIT on $BRANCH, base $BASE_COMMIT is ancestor"

# ── PHASE 1: disposable PG ──────────────────────────────────────────────
echo ""
echo "===== PHASE 1: disposable PG14 on 127.0.0.1:$PG_PORT ====="
rm -rf "$PGDATA" "$PG_LOG"
/opt/homebrew/opt/postgresql@14/bin/initdb -A trust -U "$PG_USER" "$PGDATA" >/dev/null 2>&1 || { bad "initdb failed"; exit 1; }
/opt/homebrew/opt/postgresql@14/bin/pg_ctl -D "$PGDATA" -o "-p $PG_PORT" -l "$PG_LOG" start >/dev/null 2>&1 || { bad "pg_ctl start failed"; tail -20 "$PG_LOG"; exit 1; }
PG_RUN_BY_US=1
for _ in $(seq 1 30); do
  time_left || { deadline_hit; exit 1; }
  /opt/homebrew/opt/postgresql@14/bin/pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1 && break
  sleep 1
done
/opt/homebrew/opt/postgresql@14/bin/pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1 || { bad "PG not ready on $PG_PORT"; tail -20 "$PG_LOG"; exit 1; }
ok "PG up on 127.0.0.1:$PG_PORT"
/opt/homebrew/opt/postgresql@14/bin/createdb -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" "$PG_DB" >/dev/null 2>&1
/opt/homebrew/opt/postgresql@14/bin/psql -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
  -c "GRANT ALL ON SCHEMA public TO $PG_USER;" >/dev/null 2>&1
ok "DB $PG_DB created + schema public granted"

# ── PHASE 2: mock LLM ─────────────────────────────────────────────────────────
echo ""
echo "===== PHASE 2: mock LLM on 127.0.0.1:$MOCK_LLM_PORT ====="
: > "$MOCK_LLM_LOG"
# Direct background launch (NO subshell wrap): `VAR=x nohup cmd &` is a simple
# command, so $! IS the python PID. The earlier `( cd … && … & echo $! )` wrap
# captured the SUBSHELL pid instead and orphaned the real listener (defect seen
# on first run: unrecorded pid still bound to the mock port).
MOCK_LLM_PORT="$MOCK_LLM_PORT" MOCK_LLM_LOG="$MOCK_LLM_LOG" MOCK_LLM_MAX_LIFETIME_S=260 \
  nohup "$PROJECT_DIR/.venv/bin/python" "$MOCK_LLM_SCRIPT" > "$SCRATCH/mock_llm.stdout" 2>&1 &
MOCK_LLM_PID=$!
record_pid "$MOCK_LLM_PID" "mock_llm"
echo "$MOCK_LLM_PID" > "$SCRATCH/mock_llm.pid"
sleep 1
MOCK_OK=0
for _ in $(seq 1 10); do
  time_left || { deadline_hit; exit 1; }
  curl -sS "http://127.0.0.1:$MOCK_LLM_PORT/healthz" 2>/dev/null | grep -q ok && { MOCK_OK=1; break; }
  sleep 1
done
[ "$MOCK_OK" = "1" ] || { bad "mock LLM not healthy on $MOCK_LLM_PORT"; tail -30 "$SCRATCH/mock_llm.stdout"; exit 1; }
ok "mock LLM up on 127.0.0.1:$MOCK_LLM_PORT pid=$MOCK_LLM_PID"

# ── PHASE 3: daemon boot ────────────────────────────────────────────────
echo ""
echo "===== PHASE 3: daemon boot → 127.0.0.1:$DAEMON_PORT (enforce default, disposable PG) ====="
rm -rf "$DATA_DIR"
mkdir -p "$DATA_DIR/logs"
: > "$DAEMON_STDOUT"

unset SSL_CERT_FILE SSL_CERT_DIR
# OVERRIDES for the prod-valued shell env (POSTGRES_* → disposable PG; DAEMON_PORT → 18079).
# Attestation: NO ENSEMBLE_LEADER_ATTESTATION_* vars set — enforce + LLM judge defaults.
DATA_DIR="$DATA_DIR" \
ENSEMBLE_DATA_DIR="$DATA_DIR" \
PERSISTENCE_DB_PATH="$DATA_DIR/instances.db" \
POSTGRES_URL="postgresql://${PG_USER}@127.0.0.1:${PG_PORT}/${PG_DB}" \
POSTGRES_HOST=127.0.0.1 \
POSTGRES_PORT="$PG_PORT" \
POSTGRES_DB="$PG_DB" \
POSTGRES_USER="$PG_USER" \
POSTGRES_PASSWORD="$PG_USER" \
DAEMON_PORT="$DAEMON_PORT" \
OPENAI_BASE_URL="http://127.0.0.1:${MOCK_LLM_PORT}/v1" \
OPENAI_API_KEY=test-mock-key-not-real \
OPENAI_MODEL=mock-llm \
OPENAI_MODEL_KEYWORDS= \
OPENAI_REQUEST_GZIP=0 \
nohup "$PROJECT_DIR/.venv/bin/python" -m uvicorn daemon.api:app \
  --host 127.0.0.1 --port "$DAEMON_PORT" \
  --log-level info --no-access-log --timeout-graceful-shutdown 10 \
  > "$DAEMON_STDOUT" 2>&1 &
DAEMON_PID=$!
record_pid "$DAEMON_PID" "daemon"
echo "$DAEMON_PID" > "$SCRATCH/daemon.pid"
ok "daemon started pid=$DAEMON_PID (POSTGRES_*→disposable :$PG_PORT, DAEMON_PORT=$DAEMON_PORT, OPENAI→mock)"

HEALTHY=0
for _ in $(seq 1 60); do
  time_left || { deadline_hit; exit 1; }
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$DAEMON_PORT/livez" 2>/dev/null)
  [ "$code" = "200" ] && { HEALTHY=1; break; }
  kill -0 "$DAEMON_PID" 2>/dev/null || { bad "daemon exited early during boot"; tail -40 "$DAEMON_STDOUT"; exit 1; }
  sleep 1
done
[ "$HEALTHY" = "1" ] || { bad "/livez never returned 200 within 60s"; tail -40 "$DAEMON_STDOUT"; exit 1; }
ok "/livez 200 on $DAEMON_PORT"

READY_CODE=$(curl -s -o "$SCRATCH/readyz.json" -w '%{http_code}' "http://127.0.0.1:$DAEMON_PORT/readyz" 2>/dev/null)
[ "$READY_CODE" = "200" ] || { bad "/readyz returned $READY_CODE"; exit 1; }
ok "/readyz 200"

# ── PHASE 4: boot-log assertions ────────────────────────────────────────
echo ""
echo "===== PHASE 4: boot-log assertions ====="
sleep 3

ENGINE_LINE=$(grep "Creating PostgreSQL engine" "$DAEMON_STDOUT" | head -1)
if [ -n "$ENGINE_LINE" ] && echo "$ENGINE_LINE" | grep -q "$PG_DB"; then
  ok "engine line pins disposable DB $PG_DB"
else
  bad "engine line missing or not pinned to $PG_DB — got: ${ENGINE_LINE:-<none>}"
  exit 1
fi

JLINE=$(grep "Leader completion attestation resolved" "$DAEMON_STDOUT" | head -1)
if [ -n "$JLINE" ]; then
  ok "attestation boot line: $JLINE"
  echo "$JLINE" | grep -q "mode=enforce" \
    && ok "attestation mode=enforce (default active)" \
    || bad "attestation mode != enforce"
  echo "$JLINE" | grep -q "llm_judge_enabled=true" \
    && ok "llm judge enabled (default)" \
    || bad "llm_judge_enabled != true"
  echo "$JLINE" | grep -q "llm_judge_model=mock-llm" \
    && ok "judge model = mock-llm" \
    || bad "judge model != mock-llm"
else
  bad "NO 'Leader completion attestation resolved' line"
  grep -i "attestation" "$DAEMON_STDOUT" | head -5 | sed 's/^/    /'
  exit 1
fi

# No disabling markers anywhere in boot window
if grep -qE "ENSEMBLE_LEADER_ATTESTATION_MODE=(log|dry|off|disabled)" "$DAEMON_STDOUT"; then
  bad "attestation disabling marker found in boot log"
else
  ok "no attestation disabling markers in boot log"
fi

# Boot cleanliness: zero Traceback/CRITICAL/^ValueError in first 300 lines
ERRORS=0
while IFS= read -r line; do
  if echo "$line" | grep -qE "Traceback|CRITICAL |^ValueError"; then
    ERRORS=$((ERRORS+1))
  fi
done < <(head -300 "$DAEMON_STDOUT")
if [ "$ERRORS" = "0" ]; then
  ok "boot log verdict: 0 Traceback/CRITICAL/^ValueError in first 300 lines"
else
  bad "boot log verdict: $ERRORS Traceback/CRITICAL/^ValueError lines in boot window"
  head -300 "$DAEMON_STDOUT" | grep -E "Traceback|CRITICAL |^ValueError" | head -10 | sed 's/^/    /'
  exit 1
fi

# ── PHASE 5: scripted U-present completion via HTTP API ─────────────────
echo ""
echo "===== PHASE 5: scripted completion (real user question → fused judge → allow) ====="
LEADER_RESP=$(curl -sS -X POST "http://127.0.0.1:$DAEMON_PORT/api/instances" \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"leader"}')
echo "$LEADER_RESP" > "$SCRATCH/leader_create.json"
LEADER_ID=$(echo "$LEADER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['instance_id'])" 2>/dev/null)
[ -n "$LEADER_ID" ] || { bad "no instance_id from create response: $LEADER_RESP"; exit 1; }
note "LEADER_ID=$LEADER_ID"

# The REAL user question — this is the text SOURCE U must carry to the judge.
USER_QUESTION="Our on-call runbook says the smoke flag lives in a file named lcau-smoke-answer.txt. Please have your team write the single word OK into that file, then tell me it is done."
TASK_RESP=$(curl -sS -X POST "http://127.0.0.1:$DAEMON_PORT/api/instances/$LEADER_ID/messages" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "import json,sys; print(json.dumps({'content': sys.argv[1]}))" "$USER_QUESTION")")
echo "$TASK_RESP" > "$SCRATCH/leader_send.json"
echo "$TASK_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'send-message queued: message_id={d[\"message_id\"]} job_id={d.get(\"job_id\")}')" \
  || { bad "send-message failed: $TASK_RESP"; exit 1; }

COMPLETED=0
for _ in $(seq 1 120); do
  time_left || { deadline_hit; break; }
  STATUS=$(curl -sS "http://127.0.0.1:$DAEMON_PORT/api/instances/$LEADER_ID" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
  if [ "$STATUS" = "completed" ]; then COMPLETED=1; break; fi
  if [ "$STATUS" = "error" ]; then
    bad "leader status=error"
    tail -30 "$DAEMON_STDOUT"
    exit 1
  fi
  sleep 1
done
if [ "$COMPLETED" = "1" ]; then
  ok "leader reached 'completed' (completion allowed — no nudge loop)"
else
  bad "leader did not reach 'completed' (status=$STATUS)"
  tail -30 "$DAEMON_STDOUT"
  exit 1
fi

CHILDREN=$(curl -sS "http://127.0.0.1:$DAEMON_PORT/api/instances/$LEADER_ID" \
  | python3 -c "import sys,json; print(','.join(json.load(sys.stdin).get('children', [])))" 2>/dev/null)
[ -n "$CHILDREN" ] || { bad "no children spawned"; exit 1; }
ok "child spawned: $CHILDREN"

# Mock state: judge called ≥2 (deny pass + allow pass), verdict sequence correct
MOCK_STATE=$(curl -sS "http://127.0.0.1:$MOCK_LLM_PORT/state")
JUDGE_CALLS=$(echo "$MOCK_STATE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('judge_call_count', 0))")
[ "$JUDGE_CALLS" -ge "2" ] 2>/dev/null || { bad "judge called $JUDGE_CALLS times (expected ≥2)"; echo "$MOCK_STATE" | head -3; exit 1; }
ok "fused judge called $JUDGE_CALLS times (≥2)"
JUDGE_LABELS=$(echo "$MOCK_STATE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(' '.join(f['label'] for f in d['fired'] if f['role'] == 'judge'))")
echo "$JUDGE_LABELS" | grep -q "not_complete" || { bad "first judge verdict != not_complete (got: $JUDGE_LABELS)"; exit 1; }
echo "$JUDGE_LABELS" | grep -q "complete" || { bad "second judge verdict != complete (got: $JUDGE_LABELS)"; exit 1; }
ok "judge verdict sequence: $JUDGE_LABELS"

# ── PHASE 6: U-PRESENT WITNESS (resolver_eval log rows) ─────────────────
echo ""
echo "===== PHASE 6: U-present witness + fused-path log rows ====="

# Sanity: resolver_eval rows exist at all (grep anchored on trailing token —
# `resolver_eval ` is a PREFIX of `resolver_eval_error`; space-anchor mandated)
RESOLVER_ROWS=$(grep -cE "event=leader_completion_resolver_eval " "$DAEMON_STDOUT" || true)
[ "$RESOLVER_ROWS" -ge "2" ] || { bad "expected ≥2 resolver_eval rows, got $RESOLVER_ROWS"; exit 1; }
ok "resolver_eval rows present: $RESOLVER_ROWS"

# W1 — U present on a fused pass: bundle_u_chars>0 AND user_message_included=True
W1_ROWS=$(grep -E "event=leader_completion_resolver_eval .*bundle_u_chars=[1-9][0-9]* user_message_included=True" "$DAEMON_STDOUT" | wc -l | tr -d ' ')
if [ "$W1_ROWS" -ge "1" ]; then
  ok "W1: $W1_ROWS resolver_eval row(s) with bundle_u_chars>0 + user_message_included=True"
else
  bad "W1 FAILED: no resolver_eval row with bundle_u_chars>0 user_message_included=True"
  grep -E "event=leader_completion_resolver_eval " "$DAEMON_STDOUT" | head -5 | sed 's/^/    /'
  exit 1
fi

# W2 — the completing pass itself: U present + judge invoked + verdict complete + allow
W2_LINE=$(grep -E "event=leader_completion_resolver_eval .*bundle_u_chars=[1-9][0-9]* user_message_included=True judge_invoked=True judge_verdict=complete resolver_outcome=allow" "$DAEMON_STDOUT" | head -1)
if [ -n "$W2_LINE" ]; then
  ok "W2: completion ALLOWED through fused path WITH U PRESENT"
else
  bad "W2 FAILED: no allow row with U-present + judge_invoked + verdict=complete"
  grep -E "event=leader_completion_resolver_eval .*resolver_outcome=allow" "$DAEMON_STDOUT" | head -3 | sed 's/^/    /'
  exit 1
fi

# Deny+nudge happened exactly through the fused path (not a bypass), allow present
DENY_ROWS=$(grep -cE "event=leader_completion_resolver_eval .*resolver_outcome=deny_nudge" "$DAEMON_STDOUT" || true)
ALLOW_ROWS=$(grep -cE "event=leader_completion_resolver_eval .*resolver_outcome=allow" "$DAEMON_STDOUT" || true)
[ "$DENY_ROWS" -ge "1" ] || { bad "expected ≥1 deny_nudge row"; exit 1; }
[ "$ALLOW_ROWS" -ge "1" ] || { bad "expected ≥1 allow row"; exit 1; }
ok "deny_nudge rows: $DENY_ROWS, allow rows: $ALLOW_ROWS (deny→allow, no loop)"

# Fused-judge rows (the judge call site evidence)
FUSED_ROWS=$(grep -cE "event=leader_completion_gate_fused_judge .*judge_invoked=True" "$DAEMON_STDOUT" || true)
[ "$FUSED_ROWS" -ge "2" ] || { bad "expected ≥2 fused_judge rows with judge_invoked=True, got $FUSED_ROWS"; exit 1; }
ok "fused_judge rows with judge_invoked=True: $FUSED_ROWS"

# ── WITNESS QUOTES (report evidence) ────────────────────────────────────
echo ""
echo "----- WITNESS: U-present allow row (W2) -----"
echo "  $W2_LINE"
echo ""
echo "----- WITNESS: all U-present rows (W1, first 3) -----"
grep -E "event=leader_completion_resolver_eval .*bundle_u_chars=[1-9][0-9]* user_message_included=True" "$DAEMON_STDOUT" | head -3 | sed 's/^/  /'

# ── PHASE 7: clean shutdown ─────────────────────────────────────────────
echo ""
echo "===== PHASE 7: clean shutdown (SIGTERM → exit, ports freed) ====="
kill -TERM "$DAEMON_PID" 2>/dev/null && note "SIGTERM sent to daemon pid=$DAEMON_PID"
kill -TERM "$MOCK_LLM_PID" 2>/dev/null && note "SIGTERM sent to mock pid=$MOCK_LLM_PID"

for _ in $(seq 1 20); do
  time_left || { deadline_hit; break; }
  p_free=1
  for p in $DAEMON_PORT $MOCK_LLM_PORT; do
    lsof -nP -i ":$p" >/dev/null 2>&1 && p_free=0
  done
  [ "$p_free" = "1" ] && break
  sleep 0.5
done

SHUTDOWN_CLEAN=1
if kill -0 "$DAEMON_PID" 2>/dev/null; then
  note "daemon still alive after 10s grace — cleanup trap will SIGKILL"
  SHUTDOWN_CLEAN=0
fi
for p in $DAEMON_PORT $MOCK_LLM_PORT; do
  if lsof -nP -i ":$p" >/dev/null 2>&1; then
    note "port $p STILL BOUND pre-cleanup (pid=$(lsof -ti:"$p" | head -1))"
  else
    ok "port $p freed after SIGTERM (lsof verified)"
  fi
done
[ "$SHUTDOWN_CLEAN" = "1" ] && ok "daemon exited on SIGTERM" || bad "daemon needed SIGKILL"

# teardown (PG stop, dir removals, orphan check, final RESULT line) owned by EXIT trap
note "all teardown steps owned by EXIT trap"
exit 0
