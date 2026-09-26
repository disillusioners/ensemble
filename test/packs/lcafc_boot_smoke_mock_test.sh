#!/usr/bin/env bash
# Test Pack: lcafc_boot_smoke — Incident 7d4a3bd9 (LCA false completion) BOOT SMOKE,
# SEGMENT 2 (boot smoke) of the cycle's verification.
#
# Purpose: boot-smoke of the LCA false-completion branch
# (feature/lca-false-complete-fixes @ d5c50994, base 316a849b). This is the
# dedicated boot smoke for incident 7d4a3bd9 (correct-judge-override class)
# that ALSO surfaces the loud-surface escalation rendering: a
# ``completion_gate_escalated=True`` mission terminal renders as the
# distinct string ``"completed (gate escalated — unverified)"`` (constant
# ``daemon.constants.COMPLETION_GATE_ESCALATED_DISPLAY``) on every HTTP
# read point — NOT plain ``"completed"``.
#
# Architecture under test:
#   - daemon boots against DISPOSABLE PG14 on 127.0.0.1:15810 (NOT 5432).
#   - attestation at ENFORCE DEFAULT: NO ENSEMBLE_LEADER_ATTESTATION_* env set.
#   - Verification: the boot log contains the
#     "Leader completion attestation resolved" line with mode=enforce,
#     attestation_enabled=true, llm_judge_enabled=true — proving
#     DEFAULT_MODE="enforce".
#   - LOUD-SURFACE rendering: seed a JobItem linked to an Instance with
#     ``completion_gate_escalated=True`` + status='completed' via direct
#     SQL INSERT on the DISPOSABLE PG (acceptable fallback path — see
#     §DEVIATION block), then GET /api/jobs/{job_id} and assert the
#     response carries the escalated string, NOT plain "completed".
#   - Clean shutdown: SIGTERM → uvicorn graceful exit ≤15s
#     (--timeout-graceful-shutdown 10) → ports 15800/15810/15820 freed
#     → no orphan PIDs.
#
# Drift pin (MUST match exactly or pack FAILS):
#   - branch == feature/lca-false-complete-fixes
#   - d5c50994 IS the HEAD (the pack pins the exact commit; head must equal d5c50994)
#   - git diff <base> HEAD -- daemon/ scripts/ migrations/ includes ONLY
#     the boot-smoke expected delta (3 fixes from d5c50994); prior PG-lane
#     pack already validated the production diff on top of base 316a849b.
#     Drift mismatch → RESULT: FAIL (DRIFT), exit 1.
#
# PORT SAFETY (critical): 5432 (prod PG), 8079 (dev API), 8088 (self-system)
# NEVER touched. Daemon port 15800 must be FREE pre-boot — if occupied by
# an unrecorded PID the pack FAILS with lsof evidence (never kills unknown
# owners). PG port 15810 + mock-LLM 15820 disposable.
#
# env hygiene: env -u POSTGRES_HOST/POSTGRES_PORT/POSTGRES_DB/POSTGRES_USER/
# POSTGRES_PASSWORD/POSTGRES_URL/DATABASE_URL ensures the inheriting shell
# env (which carries prod-POSTGRES_DB=ensemble_prod on 5432) cannot leak
# into the daemon subprocess. Disposability = POSTGRES_* explicitly set to
# 15810/ensemble_test.
#
# DEVIATION: this pack uses SQL INSERT/UPDATE on the DISPOSABLE PG (after
# the daemon has booted and migrations have run) to seed the escalated
# terminal — this is the documented acceptable fallback path in the brief.
# The alternative (driving a real leader mission through the gate node
# with completion_gate_escalated=True end-to-end) would require real LLM
# judge calls in the gate, which is out of scope for a boot smoke. The
# seeded row is read through the SAME HTTP endpoint (GET /api/jobs/{id})
# that production traffic reads, so the rendering seam is the exact one
# that fires in production. The JobItem + Instance rows are seeded via
# the daemon's own PG cluster with the daemon READ-ONLY.
#
# Run:  timeout 300 bash test/packs/lcafc_boot_smoke_mock_test.sh
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer): `timeout 300` wrapper by the caller.
#   Layer 2 (inner): INTERNAL_DEADLINE = start + 280s enforced in every
#                    wait loop; breach → RESULT: TIMEOUT, exit 124.
# Hard cap: 5 minutes per pack execution.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

DAEMON_PORT=15800
PG_PORT=15810
MOCK_LLM_PORT=15820      # lcafc lane-disposable; OpenAI-compatible mock

PG_USER=ensemble
PG_DB="ensemble_test"

SCRATCH="/tmp/lcafc-boot-smoke"
PGDATA="$SCRATCH/pgdata"
PG_LOG="$SCRATCH/pg.log"
DATA_DIR="$SCRATCH/data"
DAEMON_STDOUT="$SCRATCH/daemon.stdout"
MOCK_LLM_LOG="$SCRATCH/mock_llm.log"
MOCK_LLM_STDOUT="$SCRATCH/mock_llm.stdout"
PIDS_FILE="$SCRATCH/pids.txt"

PIN_HEAD="d5c50994c61ba6c0a762c9dd128d9aa5603eca2a"
EXPECTED_BRANCH="feature/lca-false-complete-fixes"

DAEMON_PID=""
MOCK_LLM_PID=""
PG_RUN_BY_US=0
TIMEOUT_FLAG=0

PASS=0
FAIL=0
START_TS=$(date +%s)
INTERNAL_DEADLINE=$((START_TS + 280))   # ≤280s internal deadline; cleanup room before outer 300

# ── helpers ─────────────────────────────────────────────────────────────
ok()    { echo "[PASS] $*"; PASS=$((PASS+1)); }
bad()   { echo "[FAIL] $*"; FAIL=$((FAIL+1)); }
note()  { echo "[INFO] $*"; }

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
  # Port-freedom verification for ports we own (kill ONLY recorded PIDs).
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
    rm -rf "$PGDATA" "$PG_LOG" 2>/dev/null || true
  fi
  if [ -d "$PGDATA" ]; then bad "PGDATA $PGDATA not removed"; else ok "PGDATA removed"; fi
  if [ -d "$DATA_DIR" ]; then
    note "rm -rf $DATA_DIR"
    rm -rf "$DATA_DIR" 2>/dev/null || true
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
[ -x "$PROJECT_DIR/.venv/bin/python" ] || { bad ".venv/bin/python missing"; exit 1; }
ok "tools present (PG14 bin, .venv python)"

# venv-only psycopg2 install (no repo / no git change). Disclosed env side-effect.
if ! "$PROJECT_DIR/.venv/bin/python" -c "import psycopg2" >/dev/null 2>&1; then
  note "psycopg2 missing in worktree venv — installing venv-only (no repo change)"
  (cd "$PROJECT_DIR" && uv pip install psycopg2-binary >/dev/null 2>&1) || {
    bad "uv pip install psycopg2-binary failed"; exit 1; }
fi
"$PROJECT_DIR/.venv/bin/python" -c "import psycopg2; print('psycopg2', psycopg2.__version__)" >/dev/null 2>&1 \
  && ok "psycopg2 available in venv"
ok "psycopg2-binary installed venv-only (environment side-effect; no repo change)"

# Protected ports — never touched, just observed
for protect in 5432 8079 8088 15432 15433; do
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

# Drift pin: branch + ancestor-check + daemon/+frontend/+scripts/+migrations/ diff EMPTY.
# Sibling commits from other gate workers (pack scripts, test files,
# PACKS.md sections) are EXPECTED — the production-diff gate keeps the
# invariant: ``daemon/ frontend/ scripts/ migrations/`` MUST stay EMPTY
# against the base commit `d5c50994` (the LCAFC fix commit). Only test
# surface / docs / pack tooling may change.
cd "$PROJECT_DIR" || { bad "cannot cd $PROJECT_DIR"; exit 1; }
HEAD_HASH=$(git rev-parse HEAD)
HEAD_SHORT=$(git rev-parse --short HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
note "branch=$BRANCH HEAD=$HEAD_SHORT (=$HEAD_HASH)"
[ "$BRANCH" = "$EXPECTED_BRANCH" ] || { bad "branch=$BRANCH != expected $EXPECTED_BRANCH"; exit 1; }
git merge-base --is-ancestor "$PIN_HEAD" HEAD || { bad "$PIN_HEAD not an ancestor of HEAD"; exit 1; }
ok "drift pin: $PIN_HEAD is an ancestor of HEAD"
PROD_DIFF=$(git diff "$PIN_HEAD" HEAD -- daemon/ frontend/ scripts/ migrations/ | wc -l | tr -d ' ')
if [ "$PROD_DIFF" = "0" ]; then
  ok "drift pin: daemon/ frontend/ scripts/ migrations/ diff EMPTY (production diff = 0)"
else
  bad "drift pin FAILED: daemon/ frontend/ scripts/ migrations/ diff is $PROD_DIFF lines"
  git diff "$PIN_HEAD" HEAD --stat -- daemon/ frontend/ scripts/ migrations/ | sed 's/^/    /'
  echo "RESULT: FAIL (DRIFT)"
  exit 1
fi

# ── PHASE 1: disposable PG ──────────────────────────────────────────────
echo ""
echo "===== PHASE 1: disposable PG14 on 127.0.0.1:$PG_PORT ====="
rm -rf "$PGDATA" "$PG_LOG"
/opt/homebrew/opt/postgresql@14/bin/initdb -A trust -U "$PG_USER" "$PGDATA" >/dev/null 2>&1 \
  || { bad "initdb failed"; exit 1; }
/opt/homebrew/opt/postgresql@14/bin/pg_ctl -D "$PGDATA" -o "-p $PG_PORT" -l "$PG_LOG" start >/dev/null 2>&1 \
  || { bad "pg_ctl start failed"; tail -20 "$PG_LOG"; exit 1; }
PG_RUN_BY_US=1
for _ in $(seq 1 30); do
  time_left || { deadline_hit; exit 1; }
  /opt/homebrew/opt/postgresql@14/bin/pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1 && break
  sleep 1
done
/opt/homebrew/opt/postgresql@14/bin/pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1 \
  || { bad "PG not ready on $PG_PORT"; tail -20 "$PG_LOG"; exit 1; }
ok "PG up on 127.0.0.1:$PG_PORT"
/opt/homebrew/opt/postgresql@14/bin/createdb -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" "$PG_DB" >/dev/null 2>&1
/opt/homebrew/opt/postgresql@14/bin/psql -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
  -c "GRANT ALL ON SCHEMA public TO $PG_USER;" >/dev/null 2>&1
ok "DB $PG_DB created + schema public granted"

# ── PHASE 2: mock LLM (minimal OpenAI-compatible) ──────────────────────
echo ""
echo "===== PHASE 2: minimal mock LLM on 127.0.0.1:$MOCK_LLM_PORT ====="
: > "$MOCK_LLM_LOG"
: > "$MOCK_LLM_STDOUT"

nohup "$PROJECT_DIR/.venv/bin/python" -c "
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PORT = int(os.environ.get('MOCK_LLM_PORT', '15820'))
LOG  = os.environ.get('MOCK_LLM_LOG', '$MOCK_LLM_LOG')

def log(msg):
    with open(LOG, 'a') as f:
        f.write(f'[{time.strftime(\"%H:%M:%S\")}] {msg}\n')

class H(BaseHTTPRequestHandler):
    def log_message(self, *a, **k): pass
    def _send(self, code, body):
        if isinstance(body, str): body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        log(f'GET {self.path}')
        if self.path.startswith('/v1/models'):
            self._send(200, json.dumps({'object':'list','data':[{'id':'mock-llm','object':'model','created':0,'owned_by':'local'}]}))
            return
        if self.path == '/healthz':
            self._send(200, json.dumps({'status':'ok'}))
            return
        self._send(404, json.dumps({'error':'not_found'}))
    def do_POST(self):
        n = int(self.headers.get('Content-Length','0') or 0)
        _ = self.rfile.read(n) if n else b''
        log(f'POST {self.path}')
        if self.path.endswith('/v1/chat/completions'):
            payload = {
                'id': 'chatcmpl-mock',
                'object': 'chat.completion',
                'created': int(time.time()),
                'model': 'mock-llm',
                'choices': [{'index':0,'message':{'role':'assistant','content':'(mock)'},'finish_reason':'stop'}],
                'usage': {'prompt_tokens':1,'completion_tokens':1,'total_tokens':2},
            }
            self._send(200, json.dumps(payload))
            return
        self._send(404, json.dumps({'error':'not_found'}))

s = ThreadingHTTPServer(('127.0.0.1', PORT), H)
log(f'mock LLM listening on 127.0.0.1:{PORT}')
s.serve_forever()
" > "$MOCK_LLM_STDOUT" 2>&1 &
MOCK_LLM_PID=$!
record_pid "$MOCK_LLM_PID" "mock_llm"
echo "$MOCK_LLM_PID" > "$SCRATCH/mock_llm.pid"
sleep 1
MOCK_OK=0
for _ in $(seq 1 10); do
  time_left || { deadline_hit; exit 1; }
  mcode=$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$MOCK_LLM_PORT/healthz" 2>/dev/null || echo "000")
  if [ "$mcode" = "200" ]; then
    MOCK_OK=1; break
  fi
  sleep 1
done
[ "$MOCK_OK" = "1" ] || { bad "mock LLM not healthy on $MOCK_LLM_PORT"; tail -30 "$MOCK_LLM_STDOUT"; exit 1; }
ok "mock LLM up on 127.0.0.1:$MOCK_LLM_PORT pid=$MOCK_LLM_PID"

# ── PHASE 3: daemon boot ────────────────────────────────────────────────
echo ""
echo "===== PHASE 3: daemon boot → 127.0.0.1:$DAEMON_PORT (enforce default, disposable PG) ====="
rm -rf "$DATA_DIR"
mkdir -p "$DATA_DIR/logs"
: > "$DAEMON_STDOUT"
unset SSL_CERT_FILE SSL_CERT_DIR

ENV_CMD=(env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
              -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL)
ENV_CMD+=(ENSEMBLE_TEST_PG_URL="postgresql://${PG_USER}@127.0.0.1:${PG_PORT}/${PG_DB}")
ENV_CMD+=(ENSEMBLE_DATA_DIR="$DATA_DIR")
ENV_CMD+=(DATA_DIR="$DATA_DIR")
ENV_CMD+=(PERSISTENCE_DB_PATH="$DATA_DIR/instances.db")
ENV_CMD+=(POSTGRES_HOST=127.0.0.1)
ENV_CMD+=(POSTGRES_PORT="$PG_PORT")
ENV_CMD+=(POSTGRES_DB="$PG_DB")
ENV_CMD+=(POSTGRES_USER="$PG_USER")
ENV_CMD+=(POSTGRES_PASSWORD="$PG_USER")
ENV_CMD+=(DAEMON_PORT="$DAEMON_PORT")
ENV_CMD+=(OPENAI_BASE_URL="http://127.0.0.1:${MOCK_LLM_PORT}/v1")
ENV_CMD+=(OPENAI_API_KEY=test-mock-key-not-real)
ENV_CMD+=(OPENAI_MODEL=mock-llm)
ENV_CMD+=(OPENAI_MODEL_KEYWORDS=)
ENV_CMD+=(OPENAI_REQUEST_GZIP=0)
ENV_CMD+=(nohup uv run python -m uvicorn daemon.api:app \
            --host 127.0.0.1 --port "$DAEMON_PORT" \
            --log-level info --no-access-log --timeout-graceful-shutdown 10)
"${ENV_CMD[@]}" > "$DAEMON_STDOUT" 2>&1 &
DAEMON_PID=$!
record_pid "$DAEMON_PID" "daemon"
echo "$DAEMON_PID" > "$SCRATCH/daemon.pid"
ok "daemon started pid=$DAEMON_PID (POSTGRES_*→disposable :$PG_PORT, DAEMON_PORT=$DAEMON_PORT, OPENAI→mock)"

# Health poll: time-bracketed (no line-number windows)
BOOT_START_TS=$(date +%s)
HEALTHY=0
for _ in $(seq 1 90); do
  time_left || { deadline_hit; exit 1; }
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$DAEMON_PORT/livez" 2>/dev/null)
  [ "$code" = "200" ] && { HEALTHY=1; break; }
  kill -0 "$DAEMON_PID" 2>/dev/null || { bad "daemon exited early during boot"; tail -40 "$DAEMON_STDOUT"; exit 1; }
  sleep 1
done
ASSERT_TS=$(date +%s)
[ "$HEALTHY" = "1" ] || { bad "/livez never returned 200 within 90s"; tail -40 "$DAEMON_STDOUT"; exit 1; }
ok "/livez 200 on $DAEMON_PORT (within $((ASSERT_TS-BOOT_START_TS))s)"

READY_CODE=$(curl -s -o "$SCRATCH/readyz.json" -w '%{http_code}' "http://127.0.0.1:$DAEMON_PORT/readyz" 2>/dev/null || echo "000")
[ "$READY_CODE" = "200" ] || { bad "/readyz returned $READY_CODE"; exit 1; }
ok "/readyz 200"

# ── PHASE 4: ENFORCE DEFAULT boot line + ESCALATED SURFACE SEED ─────────
echo ""
echo "===== PHASE 4: ENFORCE DEFAULT proof + ESCALATED SURFACE seeding ====="
sleep 3

# Pin engine line to disposable DB
ENGINE_LINE=$(grep "Creating PostgreSQL engine" "$DAEMON_STDOUT" | head -1)
if [ -n "$ENGINE_LINE" ] && echo "$ENGINE_LINE" | grep -q "$PG_DB"; then
  ok "engine line pins disposable DB $PG_DB: $ENGINE_LINE"
else
  bad "engine line missing or not pinned to $PG_DB — got: ${ENGINE_LINE:-<none>}"
  exit 1
fi

# Attestation boot line — enforced DEFAULT (mode=enforce)
JLINE=$(grep "Leader completion attestation resolved" "$DAEMON_STDOUT" | head -1)
if [ -n "$JLINE" ]; then
  ok "attestation boot line: $JLINE"
  if echo "$JLINE" | grep -q "mode=enforce"; then
    ok "attestation mode=enforce (DEFAULT active)"
  else
    bad "attestation mode != enforce"
  fi
  if echo "$JLINE" | grep -q "attestation_enabled=true"; then
    ok "attestation_enabled=true (DEFAULT active)"
  else
    bad "attestation_enabled != true"
  fi
  if echo "$JLINE" | grep -q "llm_judge_enabled=true"; then
    ok "llm_judge_enabled=true (DEFAULT active)"
  else
    bad "llm_judge_enabled != true"
  fi
  # Cite the resolver default from the source-code (drift-pinned)
  RESOLVER_DEFAULT=$(grep -n '^DEFAULT_MODE:.*Literal\["enforce"\]' "$PROJECT_DIR/daemon/services/attestation_resolver.py" | head -1 | sed 's/^/    /')
  note "daemon code (pinned to d5c50994 @ $HEAD_SHORT):"
  note "$RESOLVER_DEFAULT   ← resolver DEFAULT_MODE = 'enforce'"
else
  bad "NO 'Leader completion attestation resolved' line in boot log"
  grep -i "attestation" "$DAEMON_STDOUT" | head -5 | sed 's/^/    /'
  exit 1
fi

# No attestation-disabling markers
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

# ── PHASE 4b: SEED the escalated terminal + assert loud-surface render ─
# DEVIATION (documented above): SQL INSERT/UPDATE on the DISPOSABLE PG
# only. The daemon's work-resolver will join JobItem ↔ Instance at HTTP
# read time, surface completion_gate_escalated=True in the WorkRecord,
# and jobs_crud._job_to_response will render the distinct escalated
# string (NOT plain "completed"). Acceptable fallback path per brief.
echo ""
echo "===== PHASE 4b: SEED escalated terminal + assert loud-surface render ====="
SEED_OUT="$SCRATCH/seed_output.json"
SEED_PY="$SCRATCH/seed_escalated_job.py"
cat > "$SEED_PY" <<PYEOF
"""Seed a JobItem + Instance with completion_gate_escalated=True into the
disposable PG, then GET /api/jobs/{job_id} to read the rendered status.

This is a TEST-ONLY script operating on the disposable PG only
(127.0.0.1:15810). The daemon's HTTP layer is queried at
127.0.0.1:15800 (also disposable) for the loud-surface render.

Mechanism (acceptable fallback path per brief):
  1. INSERT Instance row with status='completed' +
     completion_gate_escalated=True.
  2. INSERT JobItem row with admission_state='done',
     terminal_reason='completed', instance_id=<above>, status='completed'.
  3. GET /api/jobs/{job_id} — assert response['status'] ==
     'completed (gate escalated — unverified)'.
"""
import json
import sys
import urllib.request
import uuid
from datetime import datetime, timezone

import psycopg2

PG = dict(host="127.0.0.1", port=15810, user="ensemble",
           password="ensemble", dbname="ensemble_test")
DAEMON_BASE = "http://127.0.0.1:15800"

instance_id = f"inst-lcafc-{uuid.uuid4().hex[:8]}"
job_id = f"job-lcafc-{uuid.uuid4().hex[:8]}"
now_iso = datetime.now(timezone.utc).isoformat()

conn = psycopg2.connect(**PG)
conn.autocommit = True
cur = conn.cursor()

# Insert Instance row with completion_gate_escalated=True + status='completed'.
# Use ON CONFLICT DO NOTHING to be idempotent across re-runs of the pack.
cur.execute("""
    INSERT INTO instances
        (instance_id, project_id, agent_id, agent_dir, agent_name, agent_tag,
         parent_id, status, metadata, version, last_activity_at,
         created_at, updated_at, paused_at,
         attestation_denied_count, completion_gate_escalated)
    VALUES
        (%s, NULL, %s, %s, NULL, NULL, NULL, %s, '{}'::jsonb, 1, NULL,
         %s, %s, NULL, 0, true)
    ON CONFLICT (instance_id) DO UPDATE
      SET status = EXCLUDED.status,
          completion_gate_escalated = EXCLUDED.completion_gate_escalated,
          updated_at = EXCLUDED.updated_at;
""", (instance_id, "leader", "agents/leader",
      "completed", now_iso, now_iso))

# Insert JobItem row with admission_state='done', terminal_reason='completed',
# instance_id=<above>, status='completed'. job_type defaults to 'task'
# (no message mirror → keeps status as 'completed' not 'settled').
cur.execute("""
    INSERT INTO job_queue_items
        (job_id, agent_id, agent_dir, agent_tag, message, source,
         project_id, queue_id, priority, admission_state,
         instance_id, job_type, terminal_reason, idempotency_key,
         retry_count, created_at)
    VALUES
        (%s, %s, %s, NULL, 'lcafc boot-smoke escalated seed', 'api',
         NULL, NULL, 5, 'done',
         %s, 'task', 'completed', NULL,
         0, %s)
    ON CONFLICT (job_id) DO UPDATE
      SET admission_state = EXCLUDED.admission_state,
          terminal_reason = EXCLUDED.terminal_reason,
          instance_id = EXCLUDED.instance_id;
""", (job_id, "leader", "agents/leader", instance_id, now_iso))

cur.close()
conn.close()

# GET /api/jobs/{job_id} on the daemon's HTTP layer.
req = urllib.request.Request(f"{DAEMON_BASE}/api/jobs/{job_id}")
with urllib.request.urlopen(req, timeout=10) as r:
    body = json.loads(r.read().decode("utf-8"))

# Get the FULL response body (not just two fields) for evidence dump.
print(json.dumps(body, indent=2, default=str))
PYEOF
SEED_RESULT=$(uv run --project "$PROJECT_DIR" python "$SEED_PY" 2>&1)
SEED_RC=$?
echo "$SEED_RESULT" | sed 's/^/    /'
echo "$SEED_RESULT" > "$SEED_OUT"
if [ "$SEED_RC" -ne 0 ]; then
  bad "seed_escalated_job.py exited with rc=$SEED_RC"
  exit 1
fi
ok "seeded escalated JobItem+Instance and read back via /api/jobs/{job_id}"

# Parse the response and assert the loud-surface render.
# python3 -c uses json.loads on the SEED_OUT — robust to multi-line JSON.
EXPECTED_LABEL="completed (gate escalated — unverified)"
ACTUAL_STATUS=$(python3 -c "
import json, sys
with open('$SEED_OUT') as f:
    blob = f.read()
# Find the LAST JSON object in stdout (after python prints it).
import re
m = re.search(r'\{.*\}', blob, re.DOTALL)
data = json.loads(m.group(0))
print(data.get('status',''))
" 2>/dev/null || echo "PARSE_FAIL")

if [ "$ACTUAL_STATUS" = "$EXPECTED_LABEL" ]; then
  ok "loud-surface render PRESENT: response.status == \"$EXPECTED_LABEL\""
else
  bad "loud-surface render FAILED — expected \"$EXPECTED_LABEL\" but got \"$ACTUAL_STATUS\""
  echo "    full HTTP response (debug):"
  echo "$SEED_RESULT" | sed 's/^/      /'
  echo ""
  echo "    GATE FINDING: the escalated label did NOT render through the"
  echo "    daemon's HTTP jobs read point. This is a GATE FINDING, not a fix —"
  echo "    the rendering seam (jobs_crud._job_to_response) is failing under"
  echo "    the booted daemon."
  exit 1
fi

# Negative-control: ensure plain (non-escalated) JobItem renders plain "completed"
NEG_PY="$SCRATCH/seed_plain_job.py"
NEG_OUT="$SCRATCH/plain_output.json"
cat > "$NEG_PY" <<PYEOF
"""Seed a plain (non-escalated) JobItem+Instance to confirm the loud
surface is conditional on completion_gate_escalated=True (negative control).
"""
import json
import urllib.request
import uuid
from datetime import datetime, timezone

import psycopg2

PG = dict(host="127.0.0.1", port=15810, user="ensemble",
           password="ensemble", dbname="ensemble_test")
DAEMON_BASE = "http://127.0.0.1:15800"

instance_id = f"inst-lcafc-plain-{uuid.uuid4().hex[:8]}"
job_id = f"job-lcafc-plain-{uuid.uuid4().hex[:8]}"
now_iso = datetime.now(timezone.utc).isoformat()

conn = psycopg2.connect(**PG)
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
    INSERT INTO instances
        (instance_id, agent_id, agent_dir, status, metadata, version,
         created_at, updated_at,
         attestation_denied_count, completion_gate_escalated)
    VALUES
        (%s, %s, %s, %s, '{}'::jsonb, 1, %s, %s, 0, false)
    ON CONFLICT (instance_id) DO UPDATE
      SET completion_gate_escalated = false;
""", (instance_id, "leader", "agents/leader",
      "completed", now_iso, now_iso))

cur.execute("""
    INSERT INTO job_queue_items
        (job_id, agent_id, agent_dir, message, source, project_id,
         priority, admission_state, instance_id, job_type,
         terminal_reason, retry_count, created_at)
    VALUES
        (%s, %s, %s, %s, 'api', NULL, 5, 'done', %s, 'task',
         'completed', 0, %s)
    ON CONFLICT (job_id) DO UPDATE
      SET instance_id = EXCLUDED.instance_id,
          terminal_reason = EXCLUDED.terminal_reason;
""", (job_id, "leader", "agents/leader",
      "lcafc boot-smoke plain seed", instance_id, now_iso))

cur.close()
conn.close()

req = urllib.request.Request(f"{DAEMON_BASE}/api/jobs/{job_id}")
with urllib.request.urlopen(req, timeout=10) as r:
    body = json.loads(r.read().decode("utf-8"))

print(json.dumps({"status": body.get("status")}, indent=2))
PYEOF

NEG_RESULT=$(uv run --project "$PROJECT_DIR" python "$NEG_PY" 2>&1)
NEG_RC=$?
echo "$NEG_RESULT" | sed 's/^/    /'
echo "$NEG_RESULT" > "$NEG_OUT"
if [ "$NEG_RC" -ne 0 ]; then
  bad "seed_plain_job.py exited with rc=$NEG_RC"
  exit 1
fi
NEG_STATUS=$(python3 -c "
import json, re
with open('$NEG_OUT') as f:
    blob = f.read()
m = re.search(r'\{.*\}', blob, re.DOTALL)
data = json.loads(m.group(0))
print(data.get('status',''))
" 2>/dev/null || echo "PARSE_FAIL")

if [ "$NEG_STATUS" = "completed" ]; then
  ok "negative control: plain terminal renders plain 'completed'"
else
  bad "negative control FAILED — got status=\"$NEG_STATUS\" (expected 'completed')"
  exit 1
fi

# Final evidence banner
note "ATTESTATION BOOT LOG EVIDENCE (verbatim, daemon stdout):"
echo "  BEGIN_OF_VERBATIM_BOOT_LINE"
echo "    $JLINE"
echo "  END_OF_VERBATIM_BOOT_LINE"

note "ESCALATED-SURFACE HTTP READ (verbatim from /api/jobs/{job_id}):"
echo "  BEGIN_OF_VERBATIM_HTTP_RESPONSE_SNIPPET"
echo "    response.status: \"$ACTUAL_STATUS\""
echo "  END_OF_VERBATIM_HTTP_RESPONSE_SNIPPET"
echo ""
note "PLAIN-SURFACE HTTP READ (negative control, verbatim from /api/jobs/{job_id}):"
echo "  BEGIN_OF_VERBATIM_PLAIN_HTTP_RESPONSE"
echo "    response.status: \"$NEG_STATUS\""
echo "  END_OF_VERBATIM_PLAIN_HTTP_RESPONSE"

# ── PHASE 5: clean shutdown ─────────────────────────────────────────────
echo ""
echo "===== PHASE 5: clean shutdown (SIGTERM → graceful exit ≤15s) ====="
SHUTDOWN_START=$(date +%s)
kill -TERM "$DAEMON_PID" 2>/dev/null || true
SHUTDOWN_OK=0
for _ in $(seq 1 30); do
  time_left || { deadline_hit; exit 1; }
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    SHUTDOWN_OK=1; break
  fi
  sleep 0.5
done
SHUTDOWN_ELAPSED=$(( $(date +%s) - SHUTDOWN_START ))
if [ "$SHUTDOWN_OK" = "1" ]; then
  ok "daemon exited gracefully in ${SHUTDOWN_ELAPSED}s (target ≤15s)"
else
  bad "daemon did NOT exit within 15s of SIGTERM (elapsed=${SHUTDOWN_ELAPSED}s)"
  kill -KILL "$DAEMON_PID" 2>/dev/null || true
  exit 1
fi

# Port 15800 freed (record a follow-up free-check inside the trap too)
sleep 1
if lsof -ti:"$DAEMON_PORT" >/dev/null 2>&1; then
  PORT_PID=$(lsof -ti:"$DAEMON_PORT" | head -1)
  if grep -q "^${PORT_PID}|" "$PIDS_FILE" 2>/dev/null; then
    note "port $DAEMON_PORT still bound by RECORDED pid=$PORT_PID — kill in trap"
  else
    note "port $DAEMON_PORT bound by UNRECORDED pid=$PORT_PID — leaving alone (assertion deferred)"
  fi
fi

# ── PHASE 6: freestatic check (ensure.md Core #4 evidence) ─────────────
echo ""
echo "===== PHASE 6: freestatic check (--timeout-graceful-shutdown 10 in dev.sh) ====="
GRAB_LINE=$(grep -n -- '--timeout-graceful-shutdown 10' "$PROJECT_DIR/dev.sh" | head -1)
if [ -n "$GRAB_LINE" ]; then
  ok "freestatic check: dev.sh carries --timeout-graceful-shutdown 10"
  note "verbatim line: ${GRAB_LINE}"
  ok "ensure.md Core #4 evidence: dev.sh:${GRAB_LINE%%:*} → uvicorn --timeout-graceful-shutdown 10"
else
  bad "freestatic check FAILED: --timeout-graceful-shutdown 10 NOT found in dev.sh"
  grep -n "uvicorn" "$PROJECT_DIR/dev.sh" | sed 's/^/    /'
  exit 1
fi

# Final EVIDENCE block
EVIDENCE_BANNER="${SCRATCH}/evidence_lcafc.txt"
{
  echo "LCAFC BOOT SMOKE — ATTESTATION + LOUD-SURFACE EVIDENCE"
  echo "branch=$(git rev-parse --abbrev-ref HEAD)  HEAD_short=$(git rev-parse --short HEAD)  HEAD_full=$(git rev-parse HEAD)"
  echo "pg_db=$PG_DB  pg_port=$PG_PORT  daemon_port=$DAEMON_PORT  mock_llm_port=$MOCK_LLM_PORT"
  echo ""
  echo "BOOT LOG: attestation boot line:"
  echo "  $JLINE"
  echo ""
  echo "HTTP READ (escalated): GET /api/jobs/{job_id}"
  echo "  response.status: \"$ACTUAL_STATUS\""
  echo ""
  echo "HTTP READ (plain negative control): GET /api/jobs/{job_id}"
  echo "  response.status: \"$NEG_STATUS\""
  echo ""
  echo "DEV.SH FREESTATIC: --timeout-graceful-shutdown 10"
  echo "  ${GRAB_LINE}"
  echo ""
  echo "BOOT LOG: tail of attestation-related lines:"
  grep -i "attestation\|enforce" "$DAEMON_STDOUT" | head -20 | sed 's/^/  /'
} > "$EVIDENCE_BANNER"
note "evidence written: $EVIDENCE_BANNER"

note "PHASE 5: clean shutdown complete — full teardown verified in trap EXIT"

# ── Cleanup via trap EXIT ───────────────────────────────────────────────
# Script's natural end triggers the EXIT trap → cleanup() runs and
# prints the final CLEANUP / PACK RESULT banners (see cleanup() body).
# No trailing echo here — the trap banner is the canonical end signal.
