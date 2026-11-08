#!/usr/bin/env bash
# g2g8 BOOT — fenced auth-ON daemon on 8082, dev DB, sweep pinned.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env

# ── fence dirs + ONE drill key (created once, reused across reboots) ────
mkdir -p "$FENCE/roots/$ROOT" "$FENCE/data"
if [ ! -f "$FENCE/state.env" ]; then
  KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  VIEWS_ROOT="$FENCE/roots/$ROOT"
  ROOTS_JSON="$(printf '{"%s": {"type": "filesystem", "path": "%s"}, "tmp-images": {"type": "tmp_images"}}' "$ROOT" "$VIEWS_ROOT")"
  cat > "$FENCE/state.env" <<EOF
FENCE='$FENCE'
KEY='$KEY'
ENSEMBLE_DATA_DIR='$FENCE/data'
ROOTS_JSON='$ROOTS_JSON'
EOF
fi
set -a; source "$FENCE/state.env"; set +a

# ── fixtures ─────────────────────────────────────────────────────────────
cat > "$FENCE/roots/$ROOT/$VIEW_A" <<'EOF'
# Matrix View A

Origin/session/anonymous matrix target. SECRET-CONTENT-A-marker.
EOF
cat > "$FENCE/roots/$ROOT/$VIEW_B" <<'EOF'
# Matrix View B

Lockout-safety victim. SECRET-CONTENT-B-marker.
EOF

# ── auth-ON boot env (proven State-B shape) ─────────────────────────────
export SYSTEM_ENCRYPTION_KEY="$KEY"
export ENSEMBLE_AUTH_ENABLED=true
export ENSEMBLE_AUTH_CLIENT_ID=drill-client
export ENSEMBLE_AUTH_CLIENT_SECRET=drill-secret
export ENSEMBLE_AUTH_ISSUER=https://drill.invalid
export ENSEMBLE_AUTH_ALLOWED_ORIGINS="http://127.0.0.1:${PORT} http://localhost:${PORT}"
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON"
# NO proxy-front declaration (G2.8 negative leg needs legs b/c INERT):
# ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL and ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE unset.

# ── port-free check ──────────────────────────────────────────────────────
if ss -ltn "sport = :${PORT}" | grep -q LISTEN; then
  echo "PORT ${PORT} NOT FREE — if held by a non-ours process, rerun with G2G8_PORT=8083"; exit 98
fi
echo "== port ${PORT} free"

# ── bounded boot ─────────────────────────────────────────────────────────
BOOT_LOG="$EV/boot.log"
: > "$BOOT_LOG"
setsid .venv/bin/python -m daemon >> "$BOOT_LOG" 2>&1 &
BOOT_PID=$!
echo "$BOOT_PID" > "$FENCE/boot-pid"
echo "== booting daemon pid=$BOOT_PID port=$PORT"

UP=0
for i in $(seq 1 90); do
  if curl -s -o /dev/null --max-time 2 "${BASE_URL}/views/livez"; then UP=1; break; fi
  sleep 1
done
if [ "$UP" != "1" ]; then
  echo "BOOT FAILED — last 40 log lines:"; tail -40 "$BOOT_LOG"; exit 96
fi
echo "== daemon UP: $(curl -s ${BASE_URL}/views/livez | head -c 200)"
