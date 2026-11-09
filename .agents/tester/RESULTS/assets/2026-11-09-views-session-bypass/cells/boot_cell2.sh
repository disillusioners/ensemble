#!/usr/bin/env bash
# CELL (ii) BOOT — auth-OFF + DECLARED base https://views.example, port 8083.
# Declared-base knob the fix reads (FIX-7 accessor
# LiveViewsService.declared_external_base_url -> LiveViewsConfig env
# ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL; the auth lane's
# live_view_external_base_url_fallback copy is auth-ON-only — api.py:2937
# writes it inside `if config.auth.enabled` — so it is INERT in this
# auth-OFF shape; this env is THE declaration source here).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
ensure_fence_state
set -a; source "$FENCE/state.env"; set +a
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON"
ensure_fixtures

# ── cell-(ii) boot shape ─────────────────────────────────────────────────
export ENSEMBLE_AUTH_ENABLED=false
export ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL="https://views.example"
export HOST=127.0.0.1 DAEMON_HOST=127.0.0.1

# ── shape disclosure (secrets redacted; POSTGRES_* never printed) ───────
{
  echo "# cell-(ii) boot shape — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "ENSEMBLE_AUTH_ENABLED=$ENSEMBLE_AUTH_ENABLED"
  echo "ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=$ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL"
  echo "ENSEMBLE_AUTH_DISCOVERY_URL=${ENSEMBLE_AUTH_DISCOVERY_URL:-<unset>}"
  echo "ENSEMBLE_AUTH_ISSUER=${ENSEMBLE_AUTH_ISSUER:-<unset>}"
  echo "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE=${ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE:-<unset>}"
  echo "HOST=$HOST DAEMON_HOST=$DAEMON_HOST PORT=$PORT DAEMON_PORT=$DAEMON_PORT"
  echo "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
  echo "SYSTEM_ENCRYPTION_KEY=<set redacted>"
  echo "ENSEMBLE_LIVE_VIEWS_ROOTS=$ENSEMBLE_LIVE_VIEWS_ROOTS"
} | tee "$EV/boot-cell2.env.txt"
cp "$EV/boot-cell2.env.txt" "$MIRROR/" 2>/dev/null || true

# ── port-free check ──────────────────────────────────────────────────────
if ss -ltn "sport = :${PORT}" | grep -q LISTEN; then
  echo "PORT ${PORT} NOT FREE — aborting (owner? cleanup.sh first)"; exit 98
fi
echo "== port ${PORT} free"

# ── bounded boot ─────────────────────────────────────────────────────────
BOOT_LOG="$EV/boot-cell2.log"
: > "$BOOT_LOG"
setsid "$WT/.venv/bin/python" -m daemon >> "$BOOT_LOG" 2>&1 &
BOOT_PID=$!
echo "$BOOT_PID" > "$FENCE/boot-pid"
echo "== booting daemon pid=$BOOT_PID port=$PORT host=$HOST (cell ii)"

UP=0
for i in $(seq 1 90); do
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/views/livez"; then UP=1; break; fi
  sleep 1
done
if [ "$UP" != "1" ]; then
  echo "BOOT FAILED — last 40 log lines:"; tail -40 "$BOOT_LOG"; exit 96
fi
echo "== daemon UP: $(curl -s http://127.0.0.1:${PORT}/views/livez | head -c 200)"

# ── bind disclosure (authoritative ss record) ────────────────────────────
ss -ltnp "sport = :${PORT}" | tee "$EV/bind-cell2.txt"
cp "$EV/bind-cell2.txt" "$MIRROR/" 2>/dev/null || true
