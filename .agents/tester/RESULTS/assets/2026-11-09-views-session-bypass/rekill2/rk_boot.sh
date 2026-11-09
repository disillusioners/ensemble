#!/usr/bin/env bash
# rk_boot.sh <boot-tag> <shape>
#   shape=declared → BOOT 1: auth-OFF, ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL
#                    =https://views.example (the FIX-7 knob), NO audience.
#   shape=audience → BOOT 2: auth-ON, audience https://op.example,
#                    NO declared base.
# FENCE: mktemp FAKE_HOME, dev DB assert, ONE drill Fernet key, sweep=0 pinned.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env

TAG="${1:?boot tag required}"
SHAPE="${2:?shape required}"

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
cat > "$FENCE/roots/$ROOT/$VIEW_R" <<EOF
# RE-KILL 2 drill view

Post-fix re-probe target (RE-KILL-2 marker). SECRET-CONTENT-REKILL2-marker.
EOF

export SYSTEM_ENCRYPTION_KEY="$KEY"
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON"

# ── shape-specific env ───────────────────────────────────────────────────
case "$SHAPE" in
  declared)
    export ENSEMBLE_AUTH_ENABLED=false
    unset ENSEMBLE_AUTH_ALLOWED_ORIGINS ENSEMBLE_AUTH_ISSUER ENSEMBLE_AUTH_CLIENT_ID
    unset ENSEMBLE_AUTH_CLIENT_SECRET ENSEMBLE_AUTH_DISCOVERY_URL
    unset ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
    export ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL="https://views.example"
    ;;
  audience)
    export ENSEMBLE_AUTH_ENABLED=true
    export ENSEMBLE_AUTH_CLIENT_ID=drill-client
    export ENSEMBLE_AUTH_CLIENT_SECRET=drill-secret
    export ENSEMBLE_AUTH_ISSUER=https://drill.invalid
    export ENSEMBLE_AUTH_ALLOWED_ORIGINS="https://op.example"
    unset ENSEMBLE_AUTH_DISCOVERY_URL ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
    unset ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL
    ;;
  *) echo "ABORT: unknown shape $SHAPE"; exit 93;;
esac

# ── boot config record ───────────────────────────────────────────────────
{
  echo "# RE-KILL 2 BOOT CONFIG — $TAG — $(date -u +%FT%TZ)"
  echo "tip=$(cd "$WT" && git rev-parse HEAD)"
  echo "shape=$SHAPE"
  echo "PORT=$PORT (owned exclusively by this lane; 8088 NEVER)"
  echo "FAKE_HOME=$HOME"
  echo "ENSEMBLE_DATA_DIR=$ENSEMBLE_DATA_DIR"
  echo "ENSEMBLE_AUTH_ENABLED=$ENSEMBLE_AUTH_ENABLED"
  echo "ENSEMBLE_AUTH_ALLOWED_ORIGINS=${ENSEMBLE_AUTH_ALLOWED_ORIGINS:-<unset>}"
  echo "ENSEMBLE_AUTH_ISSUER=${ENSEMBLE_AUTH_ISSUER:-<unset>} (drill-fake; live issuer scrubbed)"
  echo "ENSEMBLE_AUTH_DISCOVERY_URL=${ENSEMBLE_AUTH_DISCOVERY_URL:-<unset>}"
  echo "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE=${ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE:-<unset>}"
  echo "ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=${ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL:-<unset>}"
  echo "ENSEMBLE_LIVE_VIEWS_ROOTS=$ENSEMBLE_LIVE_VIEWS_ROOTS"
  echo "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
  echo "POSTGRES_DB=$POSTGRES_DB (dev assert passed)"
  echo "SYSTEM_ENCRYPTION_KEY=<drill-key sha256:$(printf %s "$KEY" | sha256sum | cut -c1-12)...>"
} > "$EV/BOOT-CONFIG-$TAG.txt"
cp "$EV/BOOT-CONFIG-$TAG.txt" "$MIRROR/" 2>/dev/null || true

# ── port-free check (8082 mine; never kill anything unverified) ─────────
if ss -ltn "sport = :${PORT}" 2>/dev/null | grep -q LISTEN; then
  echo "PORT ${PORT} NOT FREE — aborting (no kill performed)"; exit 98
fi
echo "== port ${PORT} free"

# ── bounded boot ─────────────────────────────────────────────────────────
BOOT_LOG="$EV/boot-$TAG.log"
: > "$BOOT_LOG"
setsid .venv/bin/python -m daemon >> "$BOOT_LOG" 2>&1 &
BOOT_PID=$!
echo "$BOOT_PID" > "$FENCE/boot-pid-$TAG"
echo "== booting daemon pid=$BOOT_PID port=$PORT tag=$TAG"

UP=0
for i in $(seq 1 90); do
  if curl -s -o /dev/null --max-time 2 "${BASE_URL}/views/livez"; then UP=1; break; fi
  sleep 1
done
if [ "$UP" != "1" ]; then
  echo "BOOT FAILED — last 40 log lines:"; tail -40 "$BOOT_LOG"; exit 96
fi
echo "== daemon UP: $(curl -s ${BASE_URL}/views/livez | head -c 200)"
echo "== boot config: $EV/BOOT-CONFIG-$TAG.txt"
