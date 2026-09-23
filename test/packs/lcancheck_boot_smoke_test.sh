#!/usr/bin/env bash
# Test Pack: lcancheck_boot_smoke — LCA Completion Check Note removal merge gate,
# SEGMENT 2 (boot smoke).
#
# Purpose: boot-smoke of the LCA Completion Check Note removal branch
# (feature/lca-remove-check-note @ ff9eb849, base 6bf7bed7): the deleted
# Completion Check Note mint in commit ff9eb849 (b2f4dae9 carried the
# (b)/(d)-pending routes to log-only, removing the live mint path). We
# prove the daemon boots at attestation enforce DEFAULT with NO live
# mint/inject of a Completion Check Note at boot, then exits cleanly.
#
# Architecture under test:
#   - daemon boots against DISPOSABLE PG14 on 127.0.0.1:15810 (NOT 5432).
#   - attestation at ENFORCE DEFAULT: NO ENSEMBLE_LEADER_ATTESTATION_* env set.
#   - Verification: the boot log contains the
#     "Leader completion attestation resolved" line with mode=enforce,
#     attestation_enabled=true, llm_judge_enabled=true, llm_judge_model=<...>,
#     AND zero occurrences of any Completion Check Note mint/inject
#     (the removal is compiled in this worktree, so the absence is the
#     expected shape).
#   - Clean shutdown: SIGTERM → uvicorn graceful exit ≤15s
#     (--timeout-graceful-shutdown 10) → port 15800 freed → no orphan PIDs.
#
# Drift pin (MUST match exactly or pack FAILS):
#   - branch == feature/lca-remove-check-note
#   - ff9eb849 is an ancestor of HEAD
#   - git diff ff9eb849 HEAD -- daemon/ scripts/ migrations/ is EMPTY
#     (test-only commits on top are expected; the production diff is zero).
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
# 15810/ensemble_test (the daemon reads POSTGRES_HOST+POSTGRES_DB for the
# postgres-vs-sqlite auto-detect at first boot; ENSEMBLE_TEST_PG_URL is
# NOT a daemon env var — it is only consumed by tests/unit/tools/test_ens_db_*,
# but we set it as well for forward-compat and visibility).
#
# Run:  timeout 300 bash test/packs/lcancheck_boot_smoke_test.sh
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
MOCK_LLM_PORT=15820      # lcan/lcancheck lane-disposable; OpenAI-compatible mock

PG_USER=ensemble
PG_DB="ensemble_test"

SCRATCH="/tmp/lcancheck-boot-smoke"
PGDATA="$SCRATCH/pgdata"
PG_LOG="$SCRATCH/pg.log"
DATA_DIR="$SCRATCH/data"
DAEMON_STDOUT="$SCRATCH/daemon.stdout"
MOCK_LLM_LOG="$SCRATCH/mock_llm.log"
MOCK_LLM_STDOUT="$SCRATCH/mock_llm.stdout"
PIDS_FILE="$SCRATCH/pids.txt"

PIN_HEAD="ff9eb8492a05aad485a17d94da12fe865bde0342"
EXPECTED_BRANCH="feature/lca-remove-check-note"

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
  # PG_PORT is handled by pg_ctl stop below — see lcan template pattern.
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

# Drift pin: branch + ff9eb849 ancestor + daemon/scripts/migrations diff EMPTY.
cd "$PROJECT_DIR" || { bad "cannot cd $PROJECT_DIR"; exit 1; }
HEAD_HASH=$(git rev-parse HEAD)
HEAD_SHORT=$(git rev-parse --short HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
note "branch=$BRANCH HEAD=$HEAD_SHORT (=$HEAD_HASH)"
[ "$BRANCH" = "$EXPECTED_BRANCH" ] || { bad "branch=$BRANCH != expected $EXPECTED_BRANCH"; exit 1; }
git merge-base --is-ancestor "$PIN_HEAD" HEAD || { bad "$PIN_HEAD not an ancestor of HEAD"; exit 1; }
ok "drift pin: $PIN_HEAD is an ancestor of HEAD"
# daemon/+scripts/+migrations/ diff must be EMPTY (production diff = 0)
PROD_DIFF=$(git diff "$PIN_HEAD" HEAD -- daemon/ scripts/ migrations/ | wc -l | tr -d ' ')
if [ "$PROD_DIFF" = "0" ]; then
  ok "drift pin: daemon/ scripts/ migrations/ diff EMPTY (production diff = 0)"
else
  bad "drift pin FAILED: daemon/ scripts/ migrations/ diff is $PROD_DIFF lines"
  git diff "$PIN_HEAD" HEAD --stat -- daemon/ scripts/ migrations/ | sed 's/^/    /'
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
# Embedded minimal mock — daemon boot only needs OpenAI-style /v1/models +
# /v1/chat/completions for any LLM-touching path to survive. We don't drive
# the LCA flow in this pack (no chat completion required at boot); the mock
# just has to be OPENAI_BASE_URL-reachable + return valid JSON shapes.
echo ""
echo "===== PHASE 2: minimal mock LLM on 127.0.0.1:$MOCK_LLM_PORT ====="
: > "$MOCK_LLM_LOG"
: > "$MOCK_LLM_STDOUT"

# Minimal mock LLM: returns a canned chat completion with mock-llm model + a minimal /v1/models response.
# Stays running until killed; never opens a chat at boot (daemon boot does not require one).
nohup "$PROJECT_DIR/.venv/bin/python" -c "
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PORT = int(os.environ.get('MOCK_LLM_PORT', '15820'))
LOG  = os.environ.get('MOCK_LLM_LOG', '$MOCK_LLM_LOG')

def log(msg):
    with open(LOG, 'a') as f:
        f.write(f'[{time.strftime(\"%H:%M:%S\")}] {msg}\n')

class H(BaseHTTPRequestHandler):
    def log_message(self, *a, **k): pass  # suppress default access log
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
  # Verify by HTTP status 200 + body contains "status" with "ok"
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

# env -u POSTGRES_* prevents the inheriting shell POSTGRES_DB=ensemble_prod (5432)
# from leaking into the daemon subprocess. Then set them explicitly to disposable.
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

# ── PHASE 4: boot-log assertions ────────────────────────────────────────
echo ""
echo "===== PHASE 4: boot-log assertions ====="
# Time-bracket (NO line-number windows): use --since / --until epoch-second windows
# the daemon's stdout is a single-file time-ordered stream — grep by a
# time-bracketed slice is reliable. ensemble.log is interleaved append regions
# (NOT chronological by line number); daemon stdout is a clean FIFO pipe and
# the append order matches real emission order.
sleep 3
FRESH_TS=$(date +%s)

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
  note "daemon code (pinned to ff9eb849 @ $HEAD_SHORT):"
  note "$RESOLVER_DEFAULT   ← resolver DEFAULT_MODE = 'enforce'"
else
  bad "NO 'Leader completion attestation resolved' line in boot log"
  grep -i "attestation" "$DAEMON_STDOUT" | head -5 | sed 's/^/    /'
  exit 1
fi

# NO-MINT WITNESS: zero mint/inject of any Completion Check Note in the boot log.
# The removal is compiled in this worktree (commit ff9eb849 carried the
# (b)/(d)-pending routes to log-only), so the absence IS the expected shape.
# grep uses BARE tokens matching the actual mint/scan strings, evaluated as
# fixed patterns — escape away regex meaning to avoid false positives.
MINT_HITS=0
while IFS= read -r pattern; do
  [ -n "$pattern" ] || continue
  if grep -F -q "$pattern" "$DAEMON_STDOUT"; then
    bad "Completion Check Note surface present in boot log (mint): $pattern"
    grep -F "$pattern" "$DAEMON_STDOUT" | head -3 | sed 's/^/    /'
    MINT_HITS=$((MINT_HITS+1))
  fi
done <<'EOF'
[SYSTEM CONTEXT: Completion Check]
[SYSTEM CONTEXT: Completion Check Nudge]
[SYSTEM CONTEXT: Child Report Check]
child_report_check
CONTEXT_KIND_CHILD_REPORT_CHECK
EOF
[ "$MINT_HITS" = "0" ] && ok "zero Completion Check Note surface in boot log (removal compiled at ff9eb849)" \
                     || true

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

# Echo the attestation line(s) verbatim — deliverable copy (verbatim into report)
note "ATTESTATION BOOT LOG EVIDENCE (verbatim, daemon stdout):"
echo "  BEGIN_OF_VERBATIM_BOOT_LINE"
echo "    $JLINE"
echo "  END_OF_VERBATIM_BOOT_LINE"

# Echo the resolver default constant — cite from source code (NOT boot log)
RESOLVER_LINE=$(grep -n '^DEFAULT_MODE:' "$PROJECT_DIR/daemon/services/attestation_resolver.py" | head -1)
note "ATTESTATION RESOLVER DEFAULT CONSTANT (verbatim, daemon/services/attestation_resolver.py):"
echo "    ${RESOLVER_LINE}"
echo "    (=>  DEFAULT_MODE = 'enforce'; ship default per operator override 2026-09-06)"

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

# Final EVIDENCE block (verbatim, what the runtime printed into the boot log)
EVIDENCE_BANNER="${SCRATCH}/evidence_attestation.txt"
{
  echo "LCANCHECK BOOT SMOKE — ATTESTATION EVIDENCE"
  echo "branch=$(git rev-parse --abbrev-ref HEAD)  HEAD_short=$(git rev-parse --short HEAD)  HEAD_full=$(git rev-parse HEAD)"
  echo "pg_db=$PG_DB  pg_port=$PG_PORT  daemon_port=$DAEMON_PORT"
  echo ""
  echo "BOOT LOG: attestation boot line:"
  echo "  $JLINE"
  echo ""
  echo "SOURCE: daemon/services/attestation_resolver.py DEFAULT_MODE constant:"
  echo "  ${RESOLVER_LINE}"
  echo ""
  echo "BOOT LOG: tail of attestation-related lines:"
  grep -i "attestation\|enforce" "$DAEMON_STDOUT" | head -20 | sed 's/^/  /'
  echo ""
  echo "BOOT LOG: zero mint/inject of Completion Check Note: MINT_HITS=$MINT_HITS"
} > "$EVIDENCE_BANNER"
note "evidence written: $EVIDENCE_BANNER"

note "PHASE 5: clean shutdown complete — full teardown verified in trap EXIT"

# ── Cleanup via trap EXIT ───────────────────────────────────────────────
echo ""
echo "===== PHASE 6: teardown (trap EXIT) ====="
note "all teardown handled in trap EXIT — see cleanup() above"
