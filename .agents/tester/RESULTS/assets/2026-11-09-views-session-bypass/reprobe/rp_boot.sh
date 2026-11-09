#!/usr/bin/env bash
# rp BOOT — fenced auth-ON daemon on 8082, dev DB, sweep pinned,
# FAKE_HOME. Boot shape: ENSEMBLE_AUTH_ALLOWED_ORIGINS=https://operator.example
# ONLY (deliberately NOT the drill host → 127.0.0.1:8082 is NOT
# audience-vouched; any grant must come from the loopback carve or a
# defect). NO declared external base → legs (b)/(c)/(e) INERT.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rp_lib.sh"
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

# ── fixture ──────────────────────────────────────────────────────────────
cat > "$FENCE/roots/$ROOT/$VIEW_R" <<'EOF'
# G2.7 Reprobe View

Post-fix re-probe target. SECRET-CONTENT-REPROBE-marker.
EOF

# ── auth-ON boot env (REPROBE SHAPE — operator audience, NOT drill host) ─
export SYSTEM_ENCRYPTION_KEY="$KEY"
export ENSEMBLE_AUTH_ENABLED=true
export ENSEMBLE_AUTH_CLIENT_ID=drill-client
export ENSEMBLE_AUTH_CLIENT_SECRET=drill-secret
export ENSEMBLE_AUTH_ISSUER=https://drill.invalid
export ENSEMBLE_AUTH_ALLOWED_ORIGINS="https://operator.example"
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON"
# NO proxy-front declaration: ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL and
# ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE remain UNSET (scrubbed in load_env).

# ── boot config record ──────────────────────────────────────────────────
{
  echo "# REPROBE BOOT CONFIG — $(date -u +%FT%TZ)"
  echo "tip=$(cd "$WT" && git rev-parse HEAD)"
  echo "PORT=$PORT (owned exclusively by this lane)"
  echo "FAKE_HOME=$HOME"
  echo "ENSEMBLE_DATA_DIR=$ENSEMBLE_DATA_DIR"
  echo "ENSEMBLE_AUTH_ENABLED=$ENSEMBLE_AUTH_ENABLED"
  echo "ENSEMBLE_AUTH_ALLOWED_ORIGINS=$ENSEMBLE_AUTH_ALLOWED_ORIGINS"
  echo "ENSEMBLE_AUTH_ISSUER=$ENSEMBLE_AUTH_ISSUER (drill-fake; live issuer scrubbed)"
  echo "ENSEMBLE_AUTH_DISCOVERY_URL=${ENSEMBLE_AUTH_DISCOVERY_URL:-<unset>}"
  echo "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE=${ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE:-<unset>}"
  echo "ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=${ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL:-<unset>}"
  echo "ENSEMBLE_LIVE_VIEWS_ROOTS=$ENSEMBLE_LIVE_VIEWS_ROOTS"
  echo "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
  echo "POSTGRES_DB=$POSTGRES_DB (dev assert passed)"
  echo "SYSTEM_ENCRYPTION_KEY=<drill-key sha256:$(printf %s "$KEY" | sha256sum | cut -c1-12)...>"
} > "$EV/BOOT-CONFIG.txt"
cp "$EV/BOOT-CONFIG.txt" "$MIRROR/" 2>/dev/null || true

# ── port-free check (8082 mine; never kill anything unverified) ─────────
if ss -ltn "sport = :${PORT}" | grep -q LISTEN; then
  echo "PORT ${PORT} NOT FREE — aborting (no kill performed)"; exit 98
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
echo "== boot config: $(cat "$EV/BOOT-CONFIG.txt" | head -3 | tail -2)"
