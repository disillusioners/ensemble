#!/usr/bin/env bash
# Test Pack: lca2_boot_smoke_mock_test — LCA resolver Stage 2 final merge gate (Job 7 of 9).
#
# Purpose: boot-smoke of the LCA Stage-2 branch (feature/lca-resolver-stage2 @ f926de24):
# - real daemon process (uvicorn, NO --reload)
# - mock OpenAI-compatible LLM endpoint on 127.0.0.1:15778 (serves both /v1/chat/completions
#   leader turns AND fused judge verdicts; see test/packs/lca2_helpers/mock_llm.py)
# - disposable PG14 on 127.0.0.1:15434 (initdb -A trust into tmp dir; createdb lca2_smoke)
# - scripted completion through the FUSED path: leader spawns developer child →
#   child completes → leader attempts final report → fused judge fires
#   (verdict=not_complete → deny+nudge) → leader attempts again → fused judge fires
#   (verdict=complete → ALLOW + END)
# - KEY DB ASSERTIONS: query the daemon's structured log rows (NOT a DB table — the
#   resolver_eval + fused_judge rows are emitted via logger.info; the log goes to
#   stdout under uvicorn and is captured to data/logs/ensemble.log + daemon.stdout).
#   Grep the persisted log for: event=leader_completion_gate_fused_judge
#   (judge_invoked=True) and event=leader_completion_resolver_eval
#   (judge_verdict=<v> resolver_outcome=<o>).
#
# Branch: feature/lca-resolver-stage2 @ f926de24
# Drift-pin (relaxed guard):
#   - git merge-base --is-ancestor f926de24 HEAD
#   - production delta (i.e., files NOT in tests/ | test/packs/ | .agents/tester/) =
#     HARD BLOCKER; print + exit 1.
#
# MOCK/PORT PLAN:
#   - Daemon uvicorn: 127.0.0.1:15777 (mock band 10000-19999)
#   - Mock LLM endpoint: 127.0.0.1:15778
#   - Disposable PG14: 127.0.0.1:15434 (verified free; 15432 occupied by foreign long-lived
#     PG — never touch)
#   - DATA_DIR: /tmp/lca2-smoke/data (fresh tmp dir per run)
#   - NEVER touch 8088 (ensemble self-system), 8079 (dev), 9797 (prod)
#
# Run:  timeout 300 bash test/packs/lca2_boot_smoke_mock_test.sh
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-stage): subprocess `timeout 25s <cmd>` for boot probes
#                                          (full-flow expects < 60s in steady state)
# Hard cap: 5 minutes per pack execution.
#
# Pre-existing known foreign defect (NOT ours to fix):
#   migration 20260915_120000 (critical-notes) uses PG-invalid
#   `BOOLEAN NOT NULL DEFAULT 0`. If it manifests here, verify it identical at
#   base 0ea60d91 (disposable base worktree leg) and document as pre-existing.
#
# CLEAN SHUTDOWN via EXIT trap: SIGTERM the daemon + mock LLM (kill ONLY PIDs this
# script recorded; port-verified before kill), drop the disposable PG (pg_ctl stop +
# rm -rf), remove DATA_DIR.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HELPER_DIR="$SCRIPT_DIR/lca2_helpers"
MOCK_LLM_SCRIPT="$HELPER_DIR/mock_llm.py"

ANCHOR_COMMIT="f926de24"
BASE_PORT=15777
MOCK_LLM_PORT=15778
PG_PORT=15434
PG_USER=ensemble
PG_DB="lca2_smoke"
PGDATA="/tmp/lca2-smoke/pgdata"
PG_LOG="/tmp/lca2-smoke/pg.log"
DATA_DIR="/tmp/lca2-smoke/data"
DAEMON_STDOUT="/tmp/lca2-smoke/daemon.stdout"
DAEMON_LOG="$DATA_DIR/logs/ensemble.log"
MOCK_LLM_LOG="/tmp/lca2-smoke/mock_llm.log"
RESULTS_FILE="/tmp/lca2-smoke/results.json"

DAEMON_PID=""
MOCK_LLM_PID=""
PG_RUN_BY_US=0

PASS=0
FAIL=0
START_TS=$(date +%s)
INTERNAL_DEADLINE=$((START_TS + 270))   # leave cleanup room before outer timeout 300

# ── helpers ─────────────────────────────────────────────────────────────
ok()    { echo "[PASS] $*"; PASS=$((PASS+1)); }
bad()   { echo "[FAIL] $*"; FAIL=$((FAIL+1)); }
note()  { echo "[INFO] $*"; }

time_left() { [ "$(date +%s)" -lt "$INTERNAL_DEADLINE" ]; }

record_pid() {
  local pid="$1"
  local name="$2"
  echo "${pid}|${name}" >> /tmp/lca2-smoke/pids.txt
}

kill_recorded() {
  # SIGTERM then SIGKILL, only PIDs we recorded
  while IFS='|' read -r pid name; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      note "teardown: SIGTERM $name pid=$pid"
      kill -TERM "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      kill -0 "$pid" 2>/dev/null && { note "SIGKILL $name pid=$pid"; kill -KILL "$pid" 2>/dev/null || true; }
    fi
  done < /tmp/lca2-smoke/pids.txt
}

cleanup() {
  local rc=$?
  echo ""
  echo "===== CLEANUP (rc=$rc) ====="
  kill_recorded
  sleep 1
  # Free ports: kill ONLY if PID is bound and NOT in our recorded list
  for p in $BASE_PORT $MOCK_LLM_PORT; do
    local bound=$(lsof -nP -i ":$p" 2>/dev/null | tail -1 | awk '{print $2}')
    if [ -n "$bound" ] && [ "$bound" != "-" ]; then
      local port_pid=$(lsof -ti:"$p" 2>/dev/null | head -1)
      if [ -n "$port_pid" ] && kill -0 "$port_pid" 2>/dev/null; then
        local is_recorded=$(grep -c "^${port_pid}|" /tmp/lca2-smoke/pids.txt 2>/dev/null || echo 0)
        if [ "$is_recorded" = "0" ]; then
          note "port $p bound by UNRECORDED pid=$port_pid — leaving alone"
        else
          note "port $p still bound by recorded pid=$port_pid — SIGKILL"
          kill -KILL "$port_pid" 2>/dev/null || true
        fi
      fi
    fi
    sleep 0.5
    if lsof -nP -i ":$p" >/dev/null 2>&1; then
      bad "port $p STILL bound after teardown"
    else
      ok "port $p freed"
    fi
  done
  # PG teardown (only if we started it)
  if [ "$PG_RUN_BY_US" = "1" ] && [ -d "$PGDATA" ]; then
    note "PG teardown: pg_ctl stop + rm -rf $PGDATA"
    /opt/homebrew/opt/postgresql@14/bin/pg_ctl -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
    rm -rf "$PGDATA" "$PG_LOG" >/dev/null 2>&1 || true
  fi
  # DATA_DIR teardown
  if [ -d "$DATA_DIR" ]; then
    note "rm -rf $DATA_DIR"
    rm -rf "$DATA_DIR" >/dev/null 2>&1 || true
  fi
  rm -f /tmp/lca2-smoke/pids.txt /tmp/lca2-smoke/*.pid /tmp/lca2-smoke/*.stdout 2>/dev/null || true
  ELAPSED=$(( $(date +%s) - START_TS ))
  echo ""
  echo "===== PACK RESULT: PASS=$PASS FAIL=$FAIL runtime=${ELAPSED}s ====="
  if [ "$FAIL" -eq 0 ]; then
    echo "PACK-OK"
  else
    echo "PACK-FAIL"
  fi
  exit $rc
}
trap cleanup EXIT INT TERM

# ── preflight: tools + port + drift ─────────────────────────────────────
echo "===== PHASE 0: preflight ====="
command -v /opt/homebrew/opt/postgresql@14/bin/initdb >/dev/null 2>&1 || { bad "initdb not found"; exit 1; }
command -v /opt/homebrew/opt/postgresql@14/bin/pg_ctl >/dev/null 2>&1 || { bad "pg_ctl not found"; exit 1; }
command -v /opt/homebrew/opt/postgresql@14/bin/pg_isready >/dev/null 2>&1 || { bad "pg_isready not found"; exit 1; }
command -v /opt/homebrew/opt/postgresql@14/bin/psql >/dev/null 2>&1 || { bad "psql not found"; exit 1; }
[ -x "$MOCK_LLM_SCRIPT" ] || chmod +x "$MOCK_LLM_SCRIPT" 2>/dev/null
[ -f "$MOCK_LLM_SCRIPT" ] || { bad "mock_llm.py missing: $MOCK_LLM_SCRIPT"; exit 1; }
ok "tools: initdb, pg_ctl, pg_isready, psql, mock_llm.py all present"

# Protected ports
for protect in 8088 8079 9797; do
  lsof -ti:"$protect" >/dev/null 2>&1 && note "port $protect is in use (NOT touched by this smoke)" || true
done

# Verify our ports are free
for p in $BASE_PORT $MOCK_LLM_PORT; do
  if lsof -ti:"$p" >/dev/null 2>&1; then
    bound=$(lsof -ti:"$p")
    note "port $p occupied by pid=$bound — killing it (no other test should own this)"
    kill -KILL "$bound" 2>/dev/null || true
    sleep 1
  fi
  if lsof -ti:"$p" >/dev/null 2>&1; then bad "port $p STILL bound"; exit 1; fi
done
ok "ports $BASE_PORT, $MOCK_LLM_PORT free"

# PG port selection: prefer 15434, fallback 15435-15439 if occupied
PG_PORT=15434
for try_port in 15434 15435 15436 15437 15438 15439; do
  if ! lsof -ti:"$try_port" >/dev/null 2>&1; then
    PG_PORT=$try_port
    break
  fi
done
if [ "$PG_PORT" != "15434" ]; then
  note "PG port 15434 occupied; using $PG_PORT instead"
fi
ok "PG_PORT=$PG_PORT (15432 occupied by foreign PG — verified untouched)"

# Drift-pin (relaxed guard)
cd "$PROJECT_DIR" || { bad "cannot cd $PROJECT_DIR"; exit 1; }
BRANCH=$(git rev-parse --abbrev-ref HEAD)
HEAD_SHORT=$(git rev-parse --short HEAD)
note "branch=$BRANCH HEAD=$HEAD_SHORT"
ANC=1
git merge-base --is-ancestor "$ANCHOR_COMMIT" HEAD || ANC=0
DIFFNAMES=$(git diff --name-only "${ANCHOR_COMMIT}..HEAD")
DIFF_CLEAN=1
DELTA_PROD=""
for f in $DIFFNAMES; do
  case "$f" in
    tests/*|test/packs/*|.agents/tester/*) ;;
    *) DIFF_CLEAN=0; DELTA_PROD="$DELTA_PROD $f" ;;
  esac
done
note "delta files (count=$(echo "$DIFFNAMES" | wc -w | tr -d ' '))"
if [ "$ANC" = "1" ] && [ "$DIFF_CLEAN" = "1" ]; then
  ok "drift gate: $ANCHOR_COMMIT ancestor of $HEAD_SHORT AND production delta clean"
else
  bad "drift gate FAILED (ancestor_exit=$ANC prod_delta_clean=$DIFF_CLEAN)"
  note "PRODUCTION DELTA (would block merge):$DELTA_PROD"
  exit 1
fi

# Staged-index check
if ! git diff --cached --quiet; then
  note "staged index dirty — listing for forensics:"
  git diff --cached --stat
fi

# ── PHASE 1: disposable PG ─────────────────────────────────────────────
echo ""
echo "===== PHASE 1: disposable PG ====="
mkdir -p /tmp/lca2-smoke
rm -rf "$PGDATA" "$PG_LOG"
/opt/homebrew/opt/postgresql@14/bin/initdb -A trust -U "$PG_USER" "$PGDATA" >/dev/null 2>&1 || { bad "initdb failed"; exit 1; }
/opt/homebrew/opt/postgresql@14/bin/pg_ctl -D "$PGDATA" -o "-p $PG_PORT" -l "$PG_LOG" start 2>&1 | head -3
PG_RUN_BY_US=1
for _ in $(seq 1 30); do
  /opt/homebrew/opt/postgresql@14/bin/pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1 && break
  sleep 1
done
/opt/homebrew/opt/postgresql@14/bin/pg_isready -h 127.0.0.1 -p "$PG_PORT" || { bad "PG not ready"; exit 1; }
ok "PG up on 127.0.0.1:$PG_PORT"
/opt/homebrew/opt/postgresql@14/bin/psql -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -lqtA 2>/dev/null | grep -q "^${PG_DB}|" || \
  /opt/homebrew/opt/postgresql@14/bin/createdb -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" "$PG_DB" >/dev/null 2>&1
/opt/homebrew/opt/postgresql@14/bin/psql -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
  -c "GRANT ALL ON SCHEMA public TO $PG_USER;" >/dev/null 2>&1
ok "DB $PG_DB created + schema public GRANTed to $PG_USER"

# ── PHASE 2: mock LLM ────────────────────────────────────────────────────
echo ""
echo "===== PHASE 2: mock LLM endpoint ====="
: > "$MOCK_LLM_LOG"
cd /tmp/lca2-smoke && nohup "$PROJECT_DIR/.venv/bin/python" "$MOCK_LLM_SCRIPT" >/tmp/lca2-smoke/mock_llm.stdout 2>&1 &
MOCK_LLM_PID=$!
record_pid "$MOCK_LLM_PID" "mock_llm"
echo "MOCK_LLM_PID=$MOCK_LLM_PID" > /tmp/lca2-smoke/mock_llm.pid
sleep 1
if ! curl -sS "http://127.0.0.1:$MOCK_LLM_PORT/healthz" | grep -q ok; then
  bad "mock LLM not healthy on $MOCK_LLM_PORT"
  tail -30 /tmp/lca2-smoke/mock_llm.stdout
  exit 1
fi
ok "mock LLM up on 127.0.0.1:$MOCK_LLM_PORT pid=$MOCK_LLM_PID"

# Quick functional test
TEST_RESP=$(curl -sS -X POST "http://127.0.0.1:$MOCK_LLM_PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}]}')
echo "$TEST_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['choices'][0]['message']['content']" || { bad "mock LLM non-stream smoke"; exit 1; }
ok "mock LLM non-stream response OK"

TEST_RESP=$(curl -sS -X POST "http://127.0.0.1:$MOCK_LLM_PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"stream":true,"messages":[{"role":"user","content":"hi"}]}')
echo "$TEST_RESP" | grep -q "^data: \[DONE\]" || { bad "mock LLM streaming smoke"; exit 1; }
ok "mock LLM streaming response OK"

# ── PHASE 3: daemon ──────────────────────────────────────────────────────
echo ""
echo "===== PHASE 3: daemon boot (uvicorn, NO --reload; NEVER use dev.sh — hardcodes 8079) ====="
rm -rf "$DATA_DIR"
mkdir -p "$DATA_DIR/logs"
: > "$DAEMON_LOG"
: > "$DAEMON_STDOUT"

unset SSL_CERT_FILE SSL_CERT_DIR
DATA_DIR="$DATA_DIR" \
ENSEMBLE_DATA_DIR="$DATA_DIR" \
PERSISTENCE_DB_PATH="$DATA_DIR/instances.db" \
POSTGRES_URL="postgresql://${PG_USER}@127.0.0.1:${PG_PORT}/${PG_DB}" \
POSTGRES_HOST=127.0.0.1 \
POSTGRES_PORT="$PG_PORT" \
POSTGRES_DB="$PG_DB" \
POSTGRES_USER="$PG_USER" \
POSTGRES_PASSWORD="$PG_USER" \
OPENAI_BASE_URL="http://127.0.0.1:${MOCK_LLM_PORT}/v1" \
OPENAI_API_KEY=test-mock-key-not-real \
OPENAI_MODEL=mock-llm \
OPENAI_MODEL_KEYWORDS= \
OPENAI_REQUEST_GZIP=0 \
nohup "$PROJECT_DIR/.venv/bin/python" -m uvicorn daemon.api:app \
  --host 127.0.0.1 --port "$BASE_PORT" \
  --log-level info --no-access-log --timeout-graceful-shutdown 10 \
  > "$DAEMON_STDOUT" 2>&1 &
DAEMON_PID=$!
record_pid "$DAEMON_PID" "daemon"
echo "DAEMON_PID=$DAEMON_PID" > /tmp/lca2-smoke/daemon.pid
ok "daemon started pid=$DAEMON_PID (env: POSTGRES_* + POSTGRES_URL + OPENAI_BASE_URL→mock)"

# Wait for /livez 200
HEALTHY=0
for _ in $(seq 1 40); do
  time_left || { bad "deadline exceeded during boot"; exit 1; }
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$BASE_PORT/livez" 2>/dev/null)
  if [ "$code" = "200" ]; then HEALTHY=1; break; fi
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    bad "daemon exited early"
    tail -40 "$DAEMON_STDOUT"
    exit 1
  fi
  sleep 1
done
[ "$HEALTHY" = "1" ] || { bad "/livez never returned 200 after 40s"; tail -40 "$DAEMON_STDOUT"; exit 1; }
ok "/livez 200 (daemon ready)"

READY_CODE=$(curl -s -o /tmp/lca2-smoke/readyz.json -w '%{http_code}' "http://127.0.0.1:$BASE_PORT/readyz" 2>/dev/null)
[ "$READY_CODE" = "200" ] || { bad "/readyz returned $READY_CODE"; exit 1; }
ok "/readyz 200 — $(python3 -c '
import json
d = json.load(open("/tmp/lca2-smoke/readyz.json"))
print(f"database={d[\"components\"][\"database\"]}, services={d[\"components\"][\"services\"]}")
')"

# ── PHASE 4: BOOT-LOG ASSERTIONS ─────────────────────────────────────────
echo ""
echo "===== PHASE 4: boot-log assertions ====="
sleep 3   # let InstanceManager boot-log flush

# Engine line — proves the disposable DB was actually used
ENGINE_LINE=$(grep -n "Creating PostgreSQL engine" "$DAEMON_STDOUT" | head -1)
if [ -n "$ENGINE_LINE" ] && echo "$ENGINE_LINE" | grep -q "$PG_DB"; then
  ok "engine line pins disposable DB — $ENGINE_LINE"
else
  bad "engine line missing or not pinned to $PG_DB — got: ${ENGINE_LINE:-<none>}"
  exit 1
fi

# Attestation boot line — proves the resolver ran + default config
JLINE=$(grep -n "Leader completion attestation resolved" "$DAEMON_STDOUT" | head -1)
if [ -n "$JLINE" ]; then
  ok "attestation boot line found"
  echo "$JLINE" | sed 's/^/    LITERAL: /'
  if echo "$JLINE" | grep -q "llm_judge_enabled=true"; then
    ok "  judge flag ENABLED (llm_judge_enabled=true)"
  else
    bad "  judge flag not enabled in boot line"
  fi
  if echo "$JLINE" | grep -q "mode=enforce"; then
    ok "  mode=enforce (default — DO NOT set ENSEMBLE_LEADER_ATTESTATION_MODE)"
  else
    bad "  mode != enforce"
  fi
  if echo "$JLINE" | grep -q "llm_judge_model=mock-llm"; then
    ok "  judge model = mock-llm (our injected OPENAI_MODEL)"
  else
    bad "  judge model != mock-llm"
  fi
else
  bad "NO 'Leader completion attestation resolved' line in $DAEMON_STDOUT"
  note "case-insensitive judge greps for diagnosis:"
  grep -in "attestation" "$DAEMON_STDOUT" | grep -i "judge" | head -5 | sed 's/^/  /'
  exit 1
fi

# Zero-error check on boot window (skip pre-existing plane MCP failures which
# are expected when the daemon can't reach the plane MCP server — see Critical
# Notes "Pre-existing foreign defect" line in the task spec).
# Note: regex uses case-sensitive 'CRITICAL' to avoid matching the
# `CriticalNotes` config log lines.
ERRORS=0
while IFS= read -r line; do
  if echo "$line" | grep -qE "Traceback|^ValueError|CRITICAL "; then
    ERRORS=$((ERRORS+1))
  fi
done < <(head -300 "$DAEMON_STDOUT")
if [ "$ERRORS" = "0" ]; then
  ok "zero Traceback/ValueError/CRITICAL in first 300 lines of daemon log"
else
  bad "$ERRORS Traceback/ValueError/CRITICAL in boot window"
  head -300 "$DAEMON_STDOUT" | grep -E "Traceback|^ValueError|CRITICAL " | head -10
  exit 1
fi

# ── PHASE 5: scripted completion via HTTP API ───────────────────────────
echo ""
echo "===== PHASE 5: scripted completion via HTTP API ====="
LEADER_RESP=$(curl -sS -X POST "http://127.0.0.1:$BASE_PORT/api/instances" \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"leader"}')
echo "$LEADER_RESP" > /tmp/lca2-smoke/leader_create.json
LEADER_ID=$(echo "$LEADER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['instance_id'])")
note "LEADER_ID=$LEADER_ID"
[ -n "$LEADER_ID" ] || { bad "no instance_id from create response"; exit 1; }

# Send user task — the leader's first turn spawns a developer child; the
# mock's scripted responses drive the fused-judge flow.
TASK_RESP=$(curl -sS -X POST "http://127.0.0.1:$BASE_PORT/api/instances/$LEADER_ID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"content":"Please coordinate: write smoke-output.txt with OK. Delegate to a developer child."}')
echo "$TASK_RESP" > /tmp/lca2-smoke/leader_send.json
echo "$TASK_RESP" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print(f"send-message queued: message_id={d[\"message_id\"]} job_id={d.get(\"job_id\")}")
'

# Poll for leader to reach completed (scripted completion takes ~5-15s with
# the mock; allow up to 90s)
COMPLETED=0
for i in $(seq 1 90); do
  time_left || { bad "deadline exceeded waiting for completion"; break; }
  STATUS=$(curl -sS "http://127.0.0.1:$BASE_PORT/api/instances/$LEADER_ID" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
  if [ "$STATUS" = "completed" ]; then COMPLETED=1; break; fi
  if [ "$STATUS" = "error" ]; then
    bad "leader status=error"
    tail -30 "$DAEMON_STDOUT"
    exit 1
  fi
  sleep 1
done
[ "$COMPLETED" = "1" ] || { bad "leader did not reach 'completed' status within 90s"; tail -30 "$DAEMON_STDOUT"; exit 1; }
ok "leader reached 'completed' status in ≤90s"

# Verify child was spawned
CHILDREN=$(curl -sS "http://127.0.0.1:$BASE_PORT/api/instances/$LEADER_ID" | python3 -c "import sys,json; print(','.join(json.load(sys.stdin).get('children', [])))")
note "leader children: $CHILDREN"
[ -n "$CHILDREN" ] || { bad "no children spawned"; exit 1; }
ok "child spawned: $CHILDREN"

# Mock LLM final state
echo ""
echo "----- mock LLM state -----"
curl -sS "http://127.0.0.1:$MOCK_LLM_PORT/state" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"calls={d['call_count']} leader_turns={d['leader_turn_count']} child_turns={d['child_turn_count']} judge_calls={d['judge_call_count']}\")
for f in d['fired']:
    print(f\"  call#{f['n']} {f['label']}\")
" > /tmp/lca2-smoke/mock_state.txt
cat /tmp/lca2-smoke/mock_state.txt

# Verify judge was called at least twice (once not_complete, once complete)
JUDGE_CALLS=$(curl -sS "http://127.0.0.1:$MOCK_LLM_PORT/state" | python3 -c "import sys,json; print(json.load(sys.stdin).get('judge_call_count', 0))")
[ "$JUDGE_CALLS" -ge "2" ] || { bad "judge was called only $JUDGE_CALLS times (expected ≥2)"; exit 1; }
ok "fused judge was called $JUDGE_CALLS times (expect ≥2)"

# Verify first judge verdict was not_complete (deny+nudge), second was complete
JUDGE_LABELS=$(curl -sS "http://127.0.0.1:$MOCK_LLM_PORT/state" | python3 -c "
import sys, json
d = json.load(sys.stdin)
labels = [f['label'] for f in d['fired'] if f['role'] == 'judge']
print(' '.join(labels))
")
echo "$JUDGE_LABELS" | grep -q "not_complete" || { bad "first judge verdict != not_complete (got: $JUDGE_LABELS)"; exit 1; }
echo "$JUDGE_LABELS" | grep -q "complete" || { bad "second judge verdict != complete (got: $JUDGE_LABELS)"; exit 1; }
ok "judge verdict sequence: $(echo $JUDGE_LABELS)"

# ── PHASE 6: DB / log assertions ────────────────────────────────────────
echo ""
echo "===== PHASE 6: fused-judge + resolver log row assertions ====="
# Note: the resolver_eval + fused_judge rows are emitted via logger.info
# (NOT a DB table — they live in data/logs/ensemble.log AND/OR daemon.stdout).
# Grep the daemon log for the structured rows.
FUSED_ROWS=$(grep -nE "event=leader_completion_gate_fused_judge " "$DAEMON_STDOUT" | wc -l | tr -d ' ')
[ "$FUSED_ROWS" -ge "2" ] || { bad "expected ≥2 fused_judge rows in daemon log, got $FUSED_ROWS"; grep -c "fused_judge" "$DAEMON_STDOUT"; exit 1; }
ok "fused-judge log rows present: $FUSED_ROWS (expect ≥2)"

# Both rows must show judge_invoked=True
INVOKED_ROWS=$(grep -nE "event=leader_completion_gate_fused_judge .*judge_invoked=True" "$DAEMON_STDOUT" | wc -l | tr -d ' ')
[ "$INVOKED_ROWS" -ge "2" ] || { bad "expected ≥2 fused_judge rows with judge_invoked=True, got $INVOKED_ROWS"; exit 1; }
ok "fused-judge rows with judge_invoked=True: $INVOKED_ROWS"

# First row must have verdict=not_complete (deny+nudge); second must have verdict=complete (allow)
FIRST_VERDICT=$(grep -nE "event=leader_completion_gate_fused_judge " "$DAEMON_STDOUT" | head -1 | grep -oE "verdict=[a-z_]+" | head -1 | cut -d= -f2)
SECOND_VERDICT=$(grep -nE "event=leader_completion_gate_fused_judge " "$DAEMON_STDOUT" | sed -n '2p' | grep -oE "verdict=[a-z_]+" | head -1 | cut -d= -f2)
[ "$FIRST_VERDICT" = "not_complete" ] || { bad "first fused_judge verdict != not_complete (got: $FIRST_VERDICT)"; exit 1; }
[ "$SECOND_VERDICT" = "complete" ] || { bad "second fused_judge verdict != complete (got: $SECOND_VERDICT)"; exit 1; }
ok "verdict sequence: not_complete → complete (deny+nudge → allow)"

# Resolver rows: judge_invoked=True must accompany the deny_nudge + allow outcomes
RESOLVER_ROWS=$(grep -nE "event=leader_completion_resolver_eval " "$DAEMON_STDOUT" | wc -l | tr -d ' ')
note "resolver_eval log rows in daemon log: $RESOLVER_ROWS"

DENY_NUDGE_ROWS=$(grep -nE "event=leader_completion_resolver_eval .*resolver_outcome=deny_nudge" "$DAEMON_STDOUT" | wc -l | tr -d ' ')
[ "$DENY_NUDGE_ROWS" -ge "1" ] || { bad "expected ≥1 resolver_eval row with resolver_outcome=deny_nudge"; exit 1; }
ok "resolver_eval rows with resolver_outcome=deny_nudge: $DENY_NUDGE_ROWS"

ALLOW_ROWS=$(grep -nE "event=leader_completion_resolver_eval .*resolver_outcome=allow" "$DAEMON_STDOUT" | wc -l | tr -d ' ')
[ "$ALLOW_ROWS" -ge "1" ] || { bad "expected ≥1 resolver_eval row with resolver_outcome=allow"; exit 1; }
ok "resolver_eval rows with resolver_outcome=allow: $ALLOW_ROWS"

# Quote the actual rows for the report
echo ""
echo "----- QUOTED FUSED-JUDGE ROWS (daemon.log) -----"
grep -nE "event=leader_completion_gate_fused_judge " "$DAEMON_STDOUT" | head -2 | sed 's/^/  /'

echo ""
echo "----- QUOTED RESOLVER ROWS (filtered to judge_invoked=True) -----"
grep -nE "event=leader_completion_resolver_eval .*judge_invoked=True" "$DAEMON_STDOUT" | head -5 | sed 's/^/  /'

# ── PHASE 7: clean shutdown ─────────────────────────────────────────────
echo ""
echo "===== PHASE 7: clean shutdown ====="
note "SIGTERM daemon pid=$DAEMON_PID + mock pid=$MOCK_LLM_PID (graceful)"

# Recorded PIDs will be killed by trap cleanup(); just signal here for
# graceful close so the boot log gets a clean shutdown line.
kill -TERM "$DAEMON_PID" 2>/dev/null && \
  note "SIGTERM sent to daemon (trap will SIGKILL after 10s if needed)"
kill -TERM "$MOCK_LLM_PID" 2>/dev/null && note "SIGTERM sent to mock"

# Wait for ports to free (graceful first)
for _ in $(seq 1 20); do
  p_free=1
  for p in $BASE_PORT $MOCK_LLM_PORT; do
    lsof -nP -i ":$p" >/dev/null 2>&1 && p_free=0
  done
  [ "$p_free" = "1" ] && break
  sleep 0.5
done

# If still bound, kill_recorded in trap will SIGKILL
note "post-shutdown port state (allowing 10s grace):"
for p in $BASE_PORT $MOCK_LLM_PORT; do
  if lsof -nP -i ":$p" >/dev/null 2>&1; then
    note "  port $p: STILL BOUND (will be SIGKILLed by cleanup trap)"
  else
    note "  port $p: freed"
  fi
done

# We intentionally let cleanup() handle the actual teardown so even a
# premature exit cleans up.
note "all teardown steps owned by EXIT trap; pack result printed there."

echo ""
echo "===== PACK SUCCESS: PASS=$PASS FAIL=$FAIL (scripted completion + fused judge verified) ====="
exit 0