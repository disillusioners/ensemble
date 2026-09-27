#!/usr/bin/env bash
# Section 1 (Checkpoint Cleanup) — Phase-1 endpoint smoke (DoD item 4).
#
# Boots a REAL daemon on a DISPOSABLE PostgreSQL database (never
# ensemble_prod), then curls the five frozen endpoints, including the
# Origin-guard refusal, the kill-switch behavior, and the boot sweep.
#
# Usage: bash tests/manual/maintenance_console_smoke.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT="${SMOKE_PORT:-8199}"
SMOKE_DIR="$(mktemp -d /tmp/mc_smoke.XXXXXX)"
DAEMON_PID=""
DB_NAME=""

log() { printf '\n[smoke] %s\n' "$*"; }
fail() { printf '\n[smoke][FAIL] %s\n' "$*"; EXIT_CODE=1; }

EXIT_CODE=0

cleanup() {
  [ -n "$DAEMON_PID" ] && kill "$DAEMON_PID" 2>/dev/null
  wait "$DAEMON_PID" 2>/dev/null
  [ -n "$DB_NAME" ] && cd "$REPO" && PGPASSWORD="${PGPASSWORD:-ensemble_dev}" \
    psql -h "${PG_TEST_HOST:-localhost}" -p "${PG_TEST_PORT:-5432}" \
    -U "${PG_TEST_USER:-ensemble}" -d "${PG_TEST_DB:-ensemble_test}" \
    -c "DROP DATABASE IF EXISTS \"$DB_NAME\" WITH (FORCE)" >/dev/null 2>&1
  rm -rf "$SMOKE_DIR"
}
trap cleanup EXIT

# ── 1. disposable DB ───────────────────────────────────────────────────────────
DB_NAME="ensemble_mc_smoke_$RANDOM$RANDOM"
log "creating disposable DB $DB_NAME"
PGPASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}" psql -h "${PG_TEST_HOST:-localhost}" \
  -p "${PG_TEST_PORT:-5432}" -U "${PG_TEST_USER:-ensemble}" \
  -d "${PG_TEST_DB:-ensemble_test}" -c "CREATE DATABASE \"$DB_NAME\"" || {
    echo "cannot create disposable DB (PG test stack down?)"; exit 2; }

DSN_HOST="${PG_TEST_HOST:-localhost}"; DSN_PORT="${PG_TEST_PORT:-5432}"
DSN_USER="${PG_TEST_USER:-ensemble}"; DSN_PASS="${PG_TEST_PASSWORD:-ensemble_dev}"

# ── 2. seed a phantom running row pre-boot (boot-sweep proof) ─────────────────
log "seeding phantom running row (boot-sweep witness)"
PGPASSWORD="$DSN_PASS" psql -h "$DSN_HOST" -p "$DSN_PORT" -U "$DSN_USER" -d "$DB_NAME" \
  -c "CREATE TABLE maintenance_runs (run_id TEXT PRIMARY KEY, section TEXT NOT NULL, kind TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL, triggered_by TEXT NOT NULL, requester_json JSON, dry_run_run_id TEXT, expected_bytes INTEGER, dry_run_summary_json JSON, confirm BOOLEAN, advisory TEXT, env_flags_json JSON, summary_json JSON, error_json JSON);
      CREATE UNIQUE INDEX uq_maintenance_runs_running_section ON maintenance_runs(section) WHERE status = 'running';
      INSERT INTO maintenance_runs (run_id, section, kind, started_at, status, triggered_by)
      VALUES ('ckpt-20260927_999999999999-phantom00', 'checkpoint-cleanup', 'manual_execute',
              '2026-09-27T00:00:00.000000+00:00', 'running', 'user');" >/dev/null

# ── 3. boot the daemon (env scrubbed → disposable PG only) ────────────────────
boot_daemon() {  # $1 = extra env (e.g. kill-switch)
  log "booting daemon on :$PORT against $DB_NAME $1"
  ( cd "$REPO" && exec env -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_USER \
      -u POSTGRES_PASSWORD -u POSTGRES_PORT -u ENSEMBLE_DB_DSN \
      -u CHECKPOINT_BLOB_PRUNE_DRY_RUN -u CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE \
      POSTGRES_HOST="$DSN_HOST" POSTGRES_PORT="$DSN_PORT" POSTGRES_USER="$DSN_USER" \
      POSTGRES_PASSWORD="$DSN_PASS" POSTGRES_DB="$DB_NAME" \
      PORT="$PORT" HOST=127.0.0.1 LOG_LEVEL=info \
      PERSISTENCE_DB_PATH="$SMOKE_DIR/instances.db" DATA_DIR="$SMOKE_DIR/data" \
      OPENAI_API_KEY="sk-smoke-dummy" OPENAI_BASE_URL="http://127.0.0.1:9/v1" \
      $1 \
      .venv/bin/python -m uvicorn daemon.api:app --host 127.0.0.1 --port "$PORT" \
      --no-access-log --timeout-graceful-shutdown 10 > "$SMOKE_DIR/daemon.log" 2>&1 ) &
  DAEMON_PID=$!
  for _ in $(seq 1 90); do
    sleep 1
    curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && return 0
    kill -0 "$DAEMON_PID" 2>/dev/null || { log "daemon died — tail of log:"; tail -20 "$SMOKE_DIR/daemon.log"; return 1; }
  done
  log "daemon did not become healthy in 90s — tail of log:"; tail -30 "$SMOKE_DIR/daemon.log"
  return 1
}

stop_daemon() {
  [ -n "$DAEMON_PID" ] && kill "$DAEMON_PID" 2>/dev/null
  wait "$DAEMON_PID" 2>/dev/null; DAEMON_PID=""
}

API="http://127.0.0.1:$PORT/api/maintenance/checkpoint-cleanup"
J() { curl -s -o "$SMOKE_DIR/body.json" -w '%{http_code}' "$@"; }
check() { # $1=expected $2=got $3=label
  if [ "$1" = "$2" ]; then log "PASS $3 (HTTP $2)"; else
    fail "$3: expected $1 got $2 — body: $(cat "$SMOKE_DIR/body.json" 2>/dev/null | head -c 300)"; fi
}
jq_has() { grep -q "$1" "$SMOKE_DIR/body.json" 2>/dev/null; }

boot_daemon "" || exit 2

# ── 4. the five endpoints ──────────────────────────────────────────────────────
log "== 1. GET /availability (no Origin — curl) =="
check 200 "$(J "$API/availability")" "availability"
jq_has '"state": "ready"' || jq_has '"state":"ready"' || fail "availability not ready: $(cat "$SMOKE_DIR/body.json")"

log "== 2. GET /status (Origin guard: no header → allow) =="
check 200 "$(J "$API/status")" "status"
jq_has '"config"' || fail "status missing config block"

log "== 3. GET /status with untrusted Origin → 403 origin_not_trusted =="
check 403 "$(J -H "Origin: http://evil.example" "$API/status")" "origin refusal"
jq_has 'origin_not_trusted' || fail "403 body lacks origin_not_trusted"

log "== 4. GET /availability with untrusted Origin → 200 (EXEMPT) =="
check 200 "$(J -H "Origin: http://evil.example" "$API/availability")" "availability exempt"

log "== 5. GET /status with localhost-family Origin → 200 =="
check 200 "$(J -H "Origin: http://localhost:4199" "$API/status")" "localhost allowed"

log "== 6. POST /dry-run → 200 (contract §3 shape) =="
check 200 "$(J -X POST "$API/dry-run")" "dry-run"
jq_has 'would_delete_count' || fail "dry-run missing would_delete_count"
jq_has 'fresh_until' || fail "dry-run missing fresh_until"
RUN_ID=$(python3 -c "import json;print(json.load(open('$SMOKE_DIR/body.json'))['run_id'])" 2>/dev/null)
log "dry-run run_id: $RUN_ID"

log "== 7. POST /execute without confirm → 400 confirm_required =="
check 400 "$(J -X POST -H 'Content-Type: application/json' -d '{}' "$API/execute")" "confirm gate"
jq_has 'confirm_required' || fail "400 body lacks confirm_required"

log "== 8. GET /runs/{unknown} → 404 not_found =="
check 404 "$(J "$API/runs/ckpt-smoke-00000000")" "runs 404"
jq_has 'not_found' || fail "404 body lacks not_found"

log "== 9. GET /runs/{dry-run id} → 200 (dry-run queryable by id) =="
check 200 "$(J "$API/runs/$RUN_ID")" "runs by id"

log "== 10. boot sweep: phantom row interrupted, no phantom in_flight =="
PHANTOM=$(PGPASSWORD="$DSN_PASS" psql -h "$DSN_HOST" -p "$DSN_PORT" -U "$DSN_USER" \
  -d "$DB_NAME" -tAc "SELECT status || '/' || (error_json->>'code') FROM maintenance_runs WHERE run_id='ckpt-20260927_999999999999-phantom00'")
if [ "$PHANTOM" = "interrupted/run_interrupted" ]; then log "PASS boot sweep ($PHANTOM)";
else fail "boot sweep: phantom row is '$PHANTOM'"; fi
curl -s "$API/status" | grep -q '"in_flight": *null' || fail "status shows phantom in_flight"

# ── 5. kill-switch pass (restart with MAINTENANCE_ENDPOINTS_ENABLED=0) ────────
stop_daemon
boot_daemon "MAINTENANCE_ENDPOINTS_ENABLED=0" || exit 2

log "== 11. kill-switch OFF: /status /dry-run /execute /runs → 503 maintenance_disabled =="
for EP in "status" "dry-run" "execute" "runs/ckpt-smoke-00000000"; do
  M="GET"; [ "$EP" = "dry-run" ] && M="POST"; [ "$EP" = "execute" ] && M="POST"
  CODE=$(J -X "$M" "$API/$EP")
  if [ "$CODE" = "503" ] && jq_has 'maintenance_disabled'; then log "PASS $EP 503 maintenance_disabled";
  else fail "$EP: expected 503 maintenance_disabled got $CODE"; fi
done

log "== 12. kill-switch OFF: /availability → 200 kill_switched =="
check 200 "$(J "$API/availability")" "availability kill_switched"
jq_has 'kill_switched' || fail "availability lacks kill_switched"

log "boot INFO line for kill-switch:"
grep -c "Maintenance endpoints DISABLED" "$SMOKE_DIR/daemon.log" >/dev/null || fail "kill-switch boot INFO line missing"
grep "Maintenance endpoints DISABLED" "$SMOKE_DIR/daemon.log" | head -1

log "boot sweep summary line:"
grep "maintenance boot sweep" "$SMOKE_DIR/daemon.log" | head -1 || true

if [ "$EXIT_CODE" = "0" ]; then log "SMOKE: ALL CHECKS PASSED"; else log "SMOKE COMPLETE WITH FAILURES"; fi
exit $EXIT_CODE
