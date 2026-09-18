#!/usr/bin/env bash
# Test Pack: lcan_boot_smoke_test — LCA advisory-note-removal merge gate, SEGMENT 2 (boot smoke).
#
# Purpose: boot-smoke of the advisory-note-removal branch
# (feature/lca-remove-advisory-note @ 0a4fccb1, base 858b1038): the deleted
# Child Report Check note-mint producer (commit 6a695b8f) is GONE — the
# A-band now scans the leader's in-context ``internal_report:<child_iid>``
# HumanMessages at gate-evaluation time (commit 1ad924d7), and a child-lie
# completion fires the deny+nudge → allow arc through the REAL fused path
# with NO minted advisory note.
#
# Architecture under test:
#   - NO Child Report Check note is minted anywhere. The catalog-hits come
#     from the TRANSCRIPT (the child's report text in the leader window),
#     not from a separately minted note.
#   - A-band scan path: daemon/services/attestation_resolver_activation.py
#     collect_source_a_signals scans internal_report: messages for the
#     17-pattern CHILD_TERMINAL_PROMISE_MARKERS catalog at gate time.
#   - Expected arc: child LIES on terminal report (catalog markers in its
#     outgoing text) → A-band fires from transcript scan → fused judge
#     invoked → judge#1 returns not_complete → deny+nudge → leader's
#     second attempt → judge#2 returns complete → ALLOW + END.
#
# - real daemon process (uvicorn, NO --reload; NEVER dev.sh — hits prod defaults)
# - bound to 127.0.0.1:15800 (custom port via DAEMON_PORT knob + uvicorn --port,
#   both kept consistent)
# - mock OpenAI-compatible LLM endpoint on 127.0.0.1:15820
#   (test/packs/lcan_helpers/mock_llm.py — child-LIE variant:
#   developer turns emit text containing catalog markers)
# - disposable PG14 on 127.0.0.1:15810 (disposable band; NEVER 5432/15432)
# - attestation at ENFORCE DEFAULT: NO ENSEMBLE_LEADER_ATTESTATION_* env set
# - A-BAND WITNESS: resolver_eval rows must show a_advisory_present=True
#   AND a_notes>=1 AND terms_fired includes "a_suspicion" — the A-band
#   fired from the transcript scan (not a minted note). The full chain on
#   the deny row: judge_invoked=True judge_verdict=not_complete
#   resolver_outcome=deny_nudge. The allow row: judge_verdict=complete
#   resolver_outcome=allow.
# - NO-MINT WITNESS: zero occurrences of "Child Report Check" or
#   CONTEXT_KIND_CHILD_REPORT_CHECK in the daemon stdout that look like a
#   mint/delivery (a log line that merely defines the legacy enum constant
#   in source code is not a mint — code definitions don't show in
#   stdout; we grep stdout only).
#
# Drift pin: HEAD == 0a4fccb1 AND 858b1038 is an ancestor (base).
#
# PORT SAFETY: 5432 (prod PG), 8079 (dev API), 8088 (self-system) NEVER
# touched. Daemon port 15800 must be FREE pre-boot — if occupied by an
# unrecorded PID the pack FAILS with lsof evidence (never kills unknown
# owners). 15432 (foreign PG) and 15433 (other-lane PG) NEVER touched.
#
# Run:  timeout 300 bash test/packs/lcan_boot_smoke_test.sh
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer): `timeout 300` wrapper by the caller
#   Layer 2 (inner): INTERNAL_DEADLINE = start + 280s enforced in every
#                    wait loop; breach → RESULT: TIMEOUT, exit 124
# Hard cap: 5 minutes per pack execution.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
MOCK_LLM_SCRIPT="$SCRIPT_DIR/lcan_helpers/mock_llm.py"

PIN_COMMIT="0a4fccb1"
BASE_COMMIT="858b1038"
DAEMON_PORT=15800
PG_USER=ensemble
PG_DB="lcan_smoke"
SCRATCH="/tmp/lcan-boot-smoke"
PGDATA="$SCRATCH/pgdata"
PG_LOG="$SCRATCH/pg.log"
DATA_DIR="$SCRATCH/data"
DAEMON_STDOUT="$SCRATCH/daemon.stdout"
DAEMON_LOG="$DATA_DIR/logs/ensemble.log"
MOCK_LLM_LOG="$SCRATCH/mock_llm.log"
PIDS_FILE="$SCRATCH/pids.txt"
MOCK_LLM_PORT=15820   # lcan-specific disposable
PG_PORT=15810         # lcan-specific disposable

DAEMON_PID=""
MOCK_LLM_PID=""
PG_RUN_BY_US=0
TIMEOUT_FLAG=0

PASS=0
FAIL=0
START_TS=$(date +%s)
INTERNAL_DEADLINE=$((START_TS + 280))   # ≤280s internal deadline; cleanup room before outer 300

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
  rm -f "$PIDS_FILE" 2>/dev/null || true
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
for protect in 5432 8079 8088 15432 15433 9797; do
  if lsof -ti:"$protect" >/dev/null 2>&1; then
    note "protected port $protect in use (NOT touched by this pack)"
  fi
done

# Daemon port 15800: must be free. NEVER kill an unknown owner.
if lsof -ti:"$DAEMON_PORT" >/dev/null 2>&1; then
  bad "daemon port $DAEMON_PORT occupied by pid=$(lsof -ti:"$DAEMON_PORT" | head -1) — refusing to start"
  lsof -nP -i ":$DAEMON_PORT" | sed 's/^/    /'
  exit 1
fi
ok "daemon port $DAEMON_PORT free"

# PG port 15810: must be free.
if lsof -ti:"$PG_PORT" >/dev/null 2>&1; then
  bad "PG port $PG_PORT occupied by pid=$(lsof -ti:"$PG_PORT" | head -1) — refusing to start"
  lsof -nP -i ":$PG_PORT" | sed 's/^/    /'
  exit 1
fi
ok "PG_PORT=$PG_PORT free"

# Mock LLM port 15820: must be free.
if lsof -ti:"$MOCK_LLM_PORT" >/dev/null 2>&1; then
  bad "mock-LLM port $MOCK_LLM_PORT occupied by pid=$(lsof -ti:"$MOCK_LLM_PORT" | head -1) — refusing to start"
  lsof -nP -i ":$MOCK_LLM_PORT" | sed 's/^/    /'
  exit 1
fi
ok "MOCK_LLM_PORT=$MOCK_LLM_PORT free"

# Drift pin: exact commit + base ancestry
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

# ── PHASE 2: mock LLM ────────────────────────────────────────────────────
echo ""
echo "===== PHASE 2: mock LLM on 127.0.0.1:$MOCK_LLM_PORT ====="
: > "$MOCK_LLM_LOG"
# Direct background launch (NO subshell wrap): `VAR=x nohup cmd &` is a simple
# command, so $! IS the python PID. (Defect seen on first run of similar
# packs: a `( cd … && … & echo $! )` wrap captured the SUBSHELL pid instead
# and orphaned the real listener.)
MOCK_LLM_PORT="$MOCK_LLM_PORT" MOCK_LLM_LOG="$MOCK_LLM_LOG" MOCK_LLM_MAX_LIFETIME_S=275 \
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
# OVERRIDES for the prod-valued shell env (POSTGRES_* → disposable PG;
# DAEMON_PORT → 15800). Attestation: NO ENSEMBLE_LEADER_ATTESTATION_* vars
# set — enforce + LLM judge defaults (proves the default is ENFORCE).
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
  echo "$JLINE" | grep -q "attestation_enabled=true" \
    && ok "attestation_enabled=true (default active)" \
    || bad "attestation_enabled != true"
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

# ── PHASE 5: scripted CHILD-LIE completion via HTTP API ─────────────────
echo ""
echo "===== PHASE 5: scripted child-lie completion (real path → fused judge → deny → allow) ====="
LEADER_RESP=$(curl -sS -X POST "http://127.0.0.1:$DAEMON_PORT/api/instances" \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"leader"}')
echo "$LEADER_RESP" > "$SCRATCH/leader_create.json"
LEADER_ID=$(echo "$LEADER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['instance_id'])" 2>/dev/null)
[ -n "$LEADER_ID" ] || { bad "no instance_id from create response: $LEADER_RESP"; exit 1; }
note "LEADER_ID=$LEADER_ID"

# The user request — triggers child delegation + child-lie + A-band scan.
USER_QUESTION="Please have a developer delegate write the smoke deliverable smoke-output.txt with the single word 'ok' and tell me when it's done. This is the lcan advisory-note-removal boot-smoke."
TASK_RESP=$(curl -sS -X POST "http://127.0.0.1:$DAEMON_PORT/api/instances/$LEADER_ID/messages" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "import json,sys; print(json.dumps({'content': sys.argv[1]}))" "$USER_QUESTION")")
echo "$TASK_RESP" > "$SCRATCH/leader_send.json"
echo "$TASK_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'send-message queued: message_id={d[\"message_id\"]} job_id={d.get(\"job_id\")}')" \
  || { bad "send-message failed: $TASK_RESP"; exit 1; }

COMPLETED=0
LAST_STATUS=""
for _ in $(seq 1 150); do
  time_left || { deadline_hit; break; }
  STATUS=$(curl -sS "http://127.0.0.1:$DAEMON_PORT/api/instances/$LEADER_ID" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
  LAST_STATUS="$STATUS"
  if [ "$STATUS" = "completed" ]; then COMPLETED=1; break; fi
  if [ "$STATUS" = "error" ]; then
    bad "leader status=error"
    tail -30 "$DAEMON_STDOUT"
    exit 1
  fi
  sleep 1
done
if [ "$COMPLETED" = "1" ]; then
  ok "leader reached 'completed' (full arc: spawn → child-lie → deny+nudge → attest → ALLOW)"
else
  bad "leader did not reach 'completed' (status=$LAST_STATUS)"
  tail -40 "$DAEMON_STDOUT"
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

# Child-lie evidence: at least one developer turn emitted the LIE
CHILD_LIES=$(echo "$MOCK_STATE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(sum(1 for f in d['fired'] if f['role'] == 'coder' and 'LIE' in f['label']))")
[ "$CHILD_LIES" -ge "1" ] 2>/dev/null || { bad "no child LIE detected (child didn't emit text with catalog markers)"; exit 1; }
ok "child LIE detected: $CHILD_LIES turn(s) emitted catalog-marker text"

# ── PHASE 6: A-BAND WITNESS (resolver_eval log rows) ────────────────────
echo ""
echo "===== PHASE 6: A-band witness + no-mint assertion ====="

# Sanity: resolver_eval rows exist at all (grep anchored on trailing token —
# `resolver_eval ` is a PREFIX of `resolver_eval_error`; space-anchor mandated)
RESOLVER_ROWS=$(grep -cE "event=leader_completion_resolver_eval " "$DAEMON_STDOUT" || true)
[ "$RESOLVER_ROWS" -ge "2" ] || { bad "expected ≥2 resolver_eval rows, got $RESOLVER_ROWS"; exit 1; }
ok "resolver_eval rows present: $RESOLVER_ROWS"

# W1 — A-band fired: a_advisory_present=True AND a_notes>=1 on the deny pass
# (the A-band fires from the transcript scan; the rows MUST show the lie
# produced evidence). The deny row carries the full chain. The grep is
# order-independent (the daemon emits fields in production-determined
# order — a_kwargs_seen comes before resolver_outcome) so we use Python
# to assert the conjunction from the structured row, not positional regex.
W1_DENY=$(python3 -c "
import re, sys
path = '$DAEMON_STDOUT'
pat = re.compile(r'event=leader_completion_resolver_eval ')
needles = ('resolver_outcome=deny_nudge', 'a_advisory_present=True', re.compile(r'a_notes=[1-9][0-9]*'))
hits = []
for line in open(path):
    if 'event=leader_completion_resolver_eval ' not in line:
        continue
    if 'resolver_outcome=deny_nudge' not in line:
        continue
    if 'a_advisory_present=True' not in line:
        continue
    if not re.search(r'a_notes=[1-9][0-9]*', line):
        continue
    hits.append(line.rstrip())
print('\n'.join(hits[:3]))
")
if [ -n "$W1_DENY" ]; then
  ok "W1: A-band fired from transcript scan (a_advisory_present=True, a_notes>=1) on deny pass"
else
  bad "W1 FAILED: no deny_nudge row with a_advisory_present=True and a_notes>=1"
  grep -E "event=leader_completion_resolver_eval .*resolver_outcome=deny_nudge" "$DAEMON_STDOUT" | head -3 | sed 's/^/    /'
  exit 1
fi

# W2 — terms_fired includes a_suspicion (the term that fired A-band).
# Order-independent check via Python — the daemon emits terms_fired BEFORE
# a_advisory_present (see W1 row ordering), so a positional grep would
# miss the conjunction. Python walks every resolver_eval row, parses the
# structured fields, and verifies the per-row conjunction.
W2_TERMS=$(python3 -c "
import re, sys
path = '$DAEMON_STDOUT'
hits = []
for line in open(path):
    if 'event=leader_completion_resolver_eval ' not in line:
        continue
    if 'a_advisory_present=True' not in line:
        continue
    m = re.search(r'terms_fired=([^\s]+)', line)
    if not m:
        continue
    if 'a_suspicion' in m.group(1).split(','):
        hits.append(line.rstrip())
print('\n'.join(hits[:3]))
")
if [ -n "$W2_TERMS" ]; then
  ok "W2: terms_fired includes a_suspicion (A-band term fired)"
else
  bad "W2 FAILED: no resolver_eval row with a_suspicion in terms_fired"
  grep -E "event=leader_completion_resolver_eval .*a_advisory_present=True" "$DAEMON_STDOUT" | head -3 | sed 's/^/    /'
  exit 1
fi

# W3 — judge invoked on the deny pass + verdict not_complete.
# Same order-independent parsing pattern.
W3_DENY=$(python3 -c "
import re, sys
path = '$DAEMON_STDOUT'
hits = []
for line in open(path):
    if 'event=leader_completion_resolver_eval ' not in line:
        continue
    if 'resolver_outcome=deny_nudge' not in line:
        continue
    if 'judge_invoked=True' not in line:
        continue
    if 'judge_verdict=not_complete' not in line:
        continue
    hits.append(line.rstrip())
print('\n'.join(hits[:1]))
")
if [ -n "$W3_DENY" ]; then
  ok "W3: judge_invoked=True judge_verdict=not_complete on deny pass (fused path engaged)"
else
  bad "W3 FAILED: no deny_nudge row with judge_invoked=True judge_verdict=not_complete"
  grep -E "event=leader_completion_resolver_eval .*resolver_outcome=deny_nudge" "$DAEMON_STDOUT" | head -3 | sed 's/^/    /'
  exit 1
fi

# W4 — fused judge event itself emitted (event=leader_completion_gate_fused_judge)
FUSED_ROWS=$(grep -cE "event=leader_completion_gate_fused_judge .*judge_invoked=True" "$DAEMON_STDOUT" || true)
[ "$FUSED_ROWS" -ge "2" ] || { bad "expected ≥2 fused_judge rows with judge_invoked=True, got $FUSED_ROWS"; exit 1; }
ok "fused_judge rows with judge_invoked=True: $FUSED_ROWS"

# W5 — the completing pass: ALLOW through the fused path
W5_ALLOW=$(grep -E "event=leader_completion_resolver_eval .*judge_invoked=True .*judge_verdict=complete .*resolver_outcome=allow" "$DAEMON_STDOUT" | head -1)
if [ -n "$W5_ALLOW" ]; then
  ok "W5: completion ALLOWED through fused path (judge_verdict=complete, resolver_outcome=allow)"
else
  bad "W5 FAILED: no allow row with judge_invoked=True judge_verdict=complete"
  grep -E "event=leader_completion_resolver_eval .*resolver_outcome=allow" "$DAEMON_STDOUT" | head -3 | sed 's/^/    /'
  exit 1
fi

# ── PHASE 7: NO-MINT assertion ──────────────────────────────────────────
echo ""
echo "===== PHASE 7: NO-MINT assertion (zero minted Child Report Check notes) ====="

# The literal constant CONTEXT_KIND_CHILD_REPORT_CHECK = "child_report_check"
# is KEPT in source code (daemon/services/context_messages.py:119, by
# design per the merge gate's defense-in-depth plan). A line in source
# that defines the constant is NOT a mint — it never makes it to the
# leader's message queue. We grep daemon STDOUT only (the live runtime
# view); source code lines live in source files, not stdout.

MINT_LINES=$(grep -nE "Child Report Check|CONTEXT_KIND_CHILD_REPORT_CHECK" "$DAEMON_STDOUT" 2>/dev/null || true)
if [ -z "$MINT_LINES" ]; then
  ok "NO-MINT: 0 occurrences of 'Child Report Check' / CONTEXT_KIND_CHILD_REPORT_CHECK in daemon stdout"
else
  # Classify each hit — any mint/delivery/inject/add is a HARD FAIL.
  # Acceptable hits (defense-in-depth: source code references like the
  # enum definition) don't appear in stdout (they're Python source);
  # any stdout hit is a runtime mint. Be strict here — the gate is
  # "zero note mint in stdout".
  bad "NO-MINT FAIL: $MINT_LINES" >/dev/null  # count placeholder
  MINT_COUNT=$(echo -n "$MINT_LINES" | grep -c "^" || true)
  bad "NO-MINT: $MINT_COUNT hit(s) in daemon stdout (any runtime mint is a HARD FAIL):"
  echo "$MINT_LINES" | head -10 | sed 's/^/    /'
  # Strict gate: zero hits in stdout = pass. Anything else = fail.
  exit 1
fi

# ── PHASE 8: data/ shadow check (the ensemble.log inside DATA_DIR) ──────
echo ""
echo "===== PHASE 8: secondary no-mint check (ensemble.log inside DATA_DIR) ====="
if [ -f "$DAEMON_LOG" ]; then
  SHADOW_HITS=$(grep -cE "Child Report Check|CONTEXT_KIND_CHILD_REPORT_CHECK" "$DAEMON_LOG" 2>/dev/null || true)
  if [ "$SHADOW_HITS" = "0" ]; then
    ok "NO-MINT (data/logs/ensemble.log): 0 hits in shadow log"
  else
    bad "NO-MINT FAIL: $SHADOW_HITS hit(s) in data/logs/ensemble.log"
    grep -nE "Child Report Check|CONTEXT_KIND_CHILD_REPORT_CHECK" "$DAEMON_LOG" | head -5 | sed 's/^/    /'
    exit 1
  fi
else
  note "no $DAEMON_LOG — skipped (stdout-only check above is authoritative)"
fi

# ── WITNESS QUOTES (report evidence) ────────────────────────────────────
echo ""
echo "----- WITNESS: W3 deny row (full chain) -----"
echo "  $W3_DENY"
echo ""
echo "----- WITNESS: W5 allow row -----"
echo "  $W5_ALLOW"
echo ""
echo "----- WITNESS: terms_fired evidence (W2) -----"
echo "  $W2_TERMS"

# ── PHASE 9: clean shutdown ─────────────────────────────────────────────
echo ""
echo "===== PHASE 9: clean shutdown (SIGTERM → exit, ports freed) ====="
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
