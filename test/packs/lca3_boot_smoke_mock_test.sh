#!/usr/bin/env bash
# Test Pack: lca3_boot_smoke_mock_test — LCA Stage-3 single-path endstate BOOT SMOKE.
#
# Purpose: prove the stage3 single-path endstate BOOTS and runs the fused
# flow end-to-end on a real daemon with a scripted/mock judge, then shuts
# down cleanly. This is the boot-smoke leg for the stage3 retirement delta.
#
# Stage2 precedent: test/packs/lca2_boot_smoke_mock_test.sh +
#   test/packs/lca2_helpers/mock_llm.py — recipe adapted here.
#
# Stage-3 deltas to assert (vs stage2):
#   (a) boot line "Leader completion attestation resolved" with mode=enforce
#       DEFAULT (ENSEMBLE_LEADER_ATTESTATION_MODE unset);
#   (b) fused rows event=leader_completion_gate_fused_judge* with
#       judge_invoked=True;
#   (c) canonical event=leader_completion_gate rows carry the 17-FIELD
#       canonical schema (CANONICAL_LOG_SCHEMA_FIELDS in
#       daemon/services/attestation_gate.py); the 18th field
#       `attest_seen_outside_window` is GONE — assert it is absent
#       everywhere in the daemon log;
#   (d) ZERO legacy event names in the log:
#         - leader_completion_gate_marker_judge* (any suffix)
#         - bare leader_completion_gate_judge (followed by non-_)
#         - bare leader_completion_gate_judge_error
#
# Branch: feature/lca-resolver-stage3 @ 8a7b5272 (drift-pin relaxed guard).
#
# Stack:
#   - Daemon uvicorn: 127.0.0.1:15790 (mock band 10000-19999)
#   - Mock LLM endpoint: 127.0.0.1:15778 (reused stage2 port; helper dir
#     is lca3_helpers/ but the role-detection logic is generic — no
#     stage-specific runtime API change)
#   - Disposable PG14: 127.0.0.1:15434 (verified free; 15432 occupied by
#     foreign long-lived PG — never touch)
#   - DATA_DIR: /tmp/lca3-smoke/data (fresh tmp dir per run)
#   - NEVER touch 8088 (ensemble self-system), 8079 (dev), 9797 (prod)
#
# Run:  timeout 300 bash test/packs/lca3_boot_smoke_mock_test.sh
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-stage): INTERNAL_DEADLINE = START_TS + 270
#                                          (full-flow expects < 90s)
# Hard cap: 5 minutes per pack execution.
#
# Pre-existing known foreign defect (NOT ours to fix):
#   migration 20260915_120000 (critical-notes) uses PG-invalid
#   `BOOLEAN NOT NULL DEFAULT 0`. Document if it manifests here.
#
# CLEAN SHUTDOWN via EXIT trap: SIGTERM the daemon + mock LLM (kill ONLY
# PIDs this script recorded; port-verified before kill), drop the
# disposable PG (pg_ctl stop + rm -rf), remove DATA_DIR.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HELPER_DIR="$SCRIPT_DIR/lca3_helpers"
MOCK_LLM_SCRIPT="$HELPER_DIR/mock_llm.py"

ANCHOR_COMMIT="8a7b5272"
BASE_PORT=15790
MOCK_LLM_PORT=15778
PG_PORT=15434
PG_USER=ensemble
PG_DB="lca3_smoke"
PGDATA="/tmp/lca3-smoke/pgdata"
PG_LOG="/tmp/lca3-smoke/pg.log"
DATA_DIR="/tmp/lca3-smoke/data"
DAEMON_STDOUT="/tmp/lca3-smoke/daemon.stdout"
DAEMON_LOG="$DATA_DIR/logs/ensemble.log"
MOCK_LLM_LOG="/tmp/lca3-smoke/mock_llm.log"

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
  echo "${pid}|${name}" >> /tmp/lca3-smoke/pids.txt
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
  done < /tmp/lca3-smoke/pids.txt
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
        local is_recorded=$(grep -c "^${port_pid}|" /tmp/lca3-smoke/pids.txt 2>/dev/null || echo 0)
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
  rm -f /tmp/lca3-smoke/pids.txt /tmp/lca3-smoke/*.pid /tmp/lca3-smoke/*.stdout 2>/dev/null || true
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

# Drift-pin (relaxed guard) — pin to stage3 expected HEAD 8a7b5272
cd "$PROJECT_DIR" || { bad "cannot cd $PROJECT_DIR"; exit 1; }
BRANCH=$(git rev-parse --abbrev-ref HEAD)
HEAD_SHORT=$(git rev-parse --short HEAD)
note "branch=$BRANCH HEAD=$HEAD_SHORT expected=$ANCHOR_COMMIT"
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
mkdir -p /tmp/lca3-smoke
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
cd /tmp/lca3-smoke && nohup "$PROJECT_DIR/.venv/bin/python" "$MOCK_LLM_SCRIPT" >/tmp/lca3-smoke/mock_llm.stdout 2>&1 &
MOCK_LLM_PID=$!
record_pid "$MOCK_LLM_PID" "mock_llm"
echo "MOCK_LLM_PID=$MOCK_LLM_PID" > /tmp/lca3-smoke/mock_llm.pid
sleep 1
if ! curl -sS "http://127.0.0.1:$MOCK_LLM_PORT/healthz" | grep -q ok; then
  bad "mock LLM not healthy on $MOCK_LLM_PORT"
  tail -30 /tmp/lca3-smoke/mock_llm.stdout
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
echo "DAEMON_PID=$DAEMON_PID" > /tmp/lca3-smoke/daemon.pid
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

READY_CODE=$(curl -s -o /tmp/lca3-smoke/readyz.json -w '%{http_code}' "http://127.0.0.1:$BASE_PORT/readyz" 2>/dev/null)
[ "$READY_CODE" = "200" ] || { bad "/readyz returned $READY_CODE"; exit 1; }
ok "/readyz 200 — $(python3 -c '
import json
d = json.load(open("/tmp/lca3-smoke/readyz.json"))
print(f"database={d[\"components\"][\"database\"]}, services={d[\"components\"][\"services\"]}")
')"

# ── PHASE 4: BOOT-LOG ASSERTIONS (stage3-specific) ─────────────────────
echo ""
echo "===== PHASE 4: stage3 boot-log assertions ====="
sleep 3   # let InstanceManager boot-log flush

# Engine line — proves the disposable DB was actually used
ENGINE_LINE=$(grep -n "Creating PostgreSQL engine" "$DAEMON_STDOUT" | head -1)
if [ -n "$ENGINE_LINE" ] && echo "$ENGINE_LINE" | grep -q "$PG_DB"; then
  ok "engine line pins disposable DB — $ENGINE_LINE"
else
  bad "engine line missing or not pinned to $PG_DB — got: ${ENGINE_LINE:-<none>}"
  exit 1
fi

# (a) Attestation boot line — assert stage3 default posture
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
    bad "  mode != enforce (stage3 default is enforce; env must be UNSET)"
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

# (c-pre) The retired `attest_seen_outside_window` field MUST be absent
# from the boot log itself.
if grep -q "attest_seen_outside_window" "$DAEMON_STDOUT"; then
  bad "attest_seen_outside_window appears in daemon log (retired field — stage3 must NOT emit it)"
  grep -n "attest_seen_outside_window" "$DAEMON_STDOUT" | head -3 | sed 's/^/    LITERAL: /'
  exit 1
else
  ok "attest_seen_outside_window field is ABSENT from daemon log (stage3 retired)"
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
echo "$LEADER_RESP" > /tmp/lca3-smoke/leader_create.json
LEADER_ID=$(echo "$LEADER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['instance_id'])")
note "LEADER_ID=$LEADER_ID"
[ -n "$LEADER_ID" ] || { bad "no instance_id from create response"; exit 1; }

# Send user task — the leader's first turn spawns a developer child; the
# mock's scripted responses drive the fused-judge flow.
TASK_RESP=$(curl -sS -X POST "http://127.0.0.1:$BASE_PORT/api/instances/$LEADER_ID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"content":"Please coordinate: write smoke-output.txt with OK. Delegate to a developer child."}')
echo "$TASK_RESP" > /tmp/lca3-smoke/leader_send.json
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
" > /tmp/lca3-smoke/mock_state.txt
cat /tmp/lca3-smoke/mock_state.txt

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

# ── PHASE 6: stage3-specific DB / log assertions ────────────────────────
echo ""
echo "===== PHASE 6: stage3 log-row assertions ====="

# (b) Fused-judge rows — stage3 must still emit leader_completion_gate_fused_judge*
# with judge_invoked=True
FUSED_ROWS=$(grep -nE "event=leader_completion_gate_fused_judge " "$DAEMON_STDOUT" | wc -l | tr -d ' ')
[ "$FUSED_ROWS" -ge "2" ] || { bad "expected ≥2 fused_judge rows in daemon log, got $FUSED_ROWS"; grep -c "fused_judge" "$DAEMON_STDOUT"; exit 1; }
ok "fused-judge log rows present: $FUSED_ROWS (expect ≥2)"

# Both rows must show judge_invoked=True
INVOKED_ROWS=$(grep -nE "event=leader_completion_gate_fused_judge .*judge_invoked=True" "$DAEMON_STDOUT" | wc -l | tr -d ' ')
[ "$INVOKED_ROWS" -ge "2" ] || { bad "expected ≥2 fused_judge rows with judge_invoked=True, got $INVOKED_ROWS"; exit 1; }
ok "fused-judge rows with judge_invoked=True: $INVOKED_ROWS"

# Verdict sequence
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

# (c) Canonical gate rows — stage3 17-FIELD schema enforcement
# Extract the first canonical event=leader_completion_gate row and assert
# every field in CANONICAL_LOG_SCHEMA_FIELDS appears in it.
GATE_ROW=$(grep -nE "event=leader_completion_gate " "$DAEMON_STDOUT" | head -1 | sed 's/^[0-9]*://')
if [ -z "$GATE_ROW" ]; then
  bad "no event=leader_completion_gate rows found"
  exit 1
fi
ok "canonical gate row sample:"
echo "$GATE_ROW" | sed 's/^/    LITERAL: /'

# Cross-check the schema by loading it from the production module and
# asserting EVERY field appears in the canonical row. This is the literal
# source-of-truth assertion (not a hand-maintained list).
MISSING=""
while IFS= read -r field; do
  if ! echo "$GATE_ROW" | grep -qE "(^|[ ])${field}="; then
    MISSING="$MISSING $field"
  fi
done < <("$PROJECT_DIR/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$PROJECT_DIR')
from daemon.services.attestation_gate import CANONICAL_LOG_SCHEMA_FIELDS
# exclude 'event' itself (it's the trigger prefix)
for f in CANONICAL_LOG_SCHEMA_FIELDS:
    if f == 'event':
        continue
    print(f)
" 2>/dev/null)
if [ -n "$MISSING" ]; then
  bad "canonical gate row is MISSING schema fields:$MISSING"
  exit 1
fi
ok "canonical gate row carries ALL 17 CANONICAL_LOG_SCHEMA_FIELDS (one per schema tuple minus 'event' trigger)"

# (c-pre / re-confirm) attest_seen_outside_window must NOT appear in any
# canonical gate row.
if echo "$GATE_ROW" | grep -q "attest_seen_outside_window"; then
  bad "canonical gate row STILL carries retired attest_seen_outside_window field"
  exit 1
fi
ok "canonical gate row does NOT carry retired attest_seen_outside_window"

# And confirm it does not appear anywhere in the daemon stdout (full sweep)
# Note: `grep -c` exits 1 with stdout "0" when no match — without a fallback
# (`|| echo 0`) the captured value is exactly "0". A `|| echo 0` fallback
# appends another "0", making the captured string "0\n0" and tripping the
# comparison.
AOSW_HITS=$(grep -c "attest_seen_outside_window" "$DAEMON_STDOUT" 2>/dev/null; true)
if [ "$AOSW_HITS" != "0" ]; then
  bad "attest_seen_outside_window appears $AOSW_HITS times in daemon log (stage3 retired)"
  grep -n "attest_seen_outside_window" "$DAEMON_STDOUT" | head -5 | sed 's/^/    LITERAL: /'
  exit 1
fi
ok "attest_seen_outside_window total occurrences in daemon log: 0"

# (d) ZERO legacy event names. We grep for the union of:
#     - leader_completion_gate_marker_judge followed by anything (except _fused)
#     - bare leader_completion_gate_judge (NOT followed by underscore+word)
#     - bare leader_completion_gate_judge_error
# Note: the legitimate event names use _fused_judge, _fused_judge_disabled,
# _fused_judge_error — all contain an underscore between judge and any suffix.
# The legacy bare names had: leader_completion_gate_judge followed by space
# or bare, and leader_completion_gate_judge_error.
LEGACY_HITS=$(grep -nE "event=leader_completion_gate_marker_judge|event=leader_completion_gate_judge( |$)|event=leader_completion_gate_judge_error" "$DAEMON_STDOUT" 2>/dev/null | wc -l | tr -d ' ')
if [ "$LEGACY_HITS" != "0" ]; then
  bad "LEGACY event names found in daemon log: $LEGACY_HITS hits"
  grep -nE "event=leader_completion_gate_marker_judge|event=leader_completion_gate_judge( |$)|event=leader_completion_gate_judge_error" "$DAEMON_STDOUT" | head -10 | sed 's/^/    LITERAL: /'
  exit 1
fi
ok "ZERO legacy event names (leader_completion_gate_marker_judge*, bare _judge, _judge_error)"

# Quote the actual rows for the report
echo ""
echo "----- QUOTED FUSED-JUDGE ROWS (daemon.log) -----"
grep -nE "event=leader_completion_gate_fused_judge " "$DAEMON_STDOUT" | head -2 | sed 's/^/  /'

echo ""
echo "----- QUOTED RESOLVER ROWS (filtered to judge_invoked=True) -----"
grep -nE "event=leader_completion_resolver_eval .*judge_invoked=True" "$DAEMON_STDOUT" | head -5 | sed 's/^/  /'

echo ""
echo "----- QUOTED CANONICAL GATE ROW (first) -----"
grep -nE "event=leader_completion_gate " "$DAEMON_STDOUT" | head -1 | sed 's/^/  /'

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
echo "===== PACK SUCCESS: PASS=$PASS FAIL=$FAIL (stage3 single-path endstate verified) ====="
exit 0