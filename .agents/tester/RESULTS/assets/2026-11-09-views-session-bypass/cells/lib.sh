#!/usr/bin/env bash
# DEGRADATION CELLS lib — G2.7 fix 6398a29a3, cells (ii)(iii)(iv) (2026-11-09).
# PORT 8083 ONLY (another worker owns 8082; 8088 NEVER).
# FENCE: one mktemp root /tmp/lvcells.*; escape-guarded; FAKE_HOME inside
# the fence (never resolves live-shaped/HOME-derived paths — 2026-10-12
# incident class). DB: dev PG ensemble_dev (asserted). ONE drill Fernet
# key per fence (state.env), reused across the three cell boots.
# SWEEP: ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY on
# every daemon-executing shell (this shell IS a daemon child).
set -uo pipefail

export WT="/home/nea/ensemble-src-wt-views-session-bypass"
PYBIN="$WT/.venv/bin/python"
export PYBIN
EV="$WT/.agents/tester/RESULTS/assets/2026-11-09-views-session-bypass/cells"
MIRROR="/tmp/cells-evidence"
export EV MIRROR
mkdir -p "$EV" "$MIRROR"

# ── fence (escape-guarded) ───────────────────────────────────────────────
FENCE_FILE="$EV/.fence-path"
if [ -f "$FENCE_FILE" ]; then
  FENCE="$(cat "$FENCE_FILE")"
else
  FENCE="$(mktemp -d /tmp/lvcells.XXXXXX)"
  case "$FENCE" in /tmp/lvcells.*) ;; *) echo "FENCE FAIL: $FENCE"; exit 99;; esac
  echo "$FENCE" > "$FENCE_FILE"
fi
case "$FENCE" in /tmp/lvcells.*) ;; *) echo "FENCE GUARD FAIL: $FENCE"; exit 99;; esac
export FENCE

# ── FAKE_HOME + escape guard (mktemp-scoped HOME for every boot shell) ──
FAKE_HOME="$FENCE/home"
mkdir -p "$FAKE_HOME"
export HOME="$FAKE_HOME"
case "$HOME" in "$FENCE"/home) ;; *) echo "HOME GUARD FAIL: $HOME"; exit 99;; esac

export PORT="${CELLS_PORT:-8083}"
export DAEMON_PORT="$PORT"
export BASE_URL="http://127.0.0.1:${PORT}"
ROOT="cellroot"
export ROOT
VIEW_A="matrix-a.md"
export VIEW_A
PUB_A="/views/public/${ROOT}/${VIEW_A}"
export PUB_A

# ── per-variable env scrub (NEVER `env -u POSTGRES_*` — silent NO-OP) ───
scrub_ambient() {
  unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
  unset PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
  unset SSL_CERT_FILE SSL_CERT_DIR
  # Ambient shell inherits the LIVE daemon's auth env — scrub then
  # re-set deliberately, or live values (door.ensem.dev discovery URL,
  # client secret, EXTERNAL_REDIRECT_BASE) leak into the drill.
  unset ENSEMBLE_AUTH_DISCOVERY_URL ENSEMBLE_AUTH_ISSUER ENSEMBLE_AUTH_CLIENT_ID
  unset ENSEMBLE_AUTH_CLIENT_SECRET ENSEMBLE_AUTH_ALLOWED_ORIGINS ENSEMBLE_AUTH_ENABLED
  unset ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE ENSEMBLE_AUTH_REDIRECT_URI
  unset ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL ENSEMBLE_LIVE_VIEWS_ROOTS
  unset HOST DAEMON_HOST
}

load_env() {
  scrub_ambient
  cd "$WT"   # `-m daemon` module resolution + all relative paths resolve from the worktree
  set -a; source "$WT/.env"; set +a
  # .env carries the LIVE-dev daemon's DAEMON_PORT/HOST — RE-assert the
  # cell knobs AFTER the source (8083 drill port, never .env's 8079).
  export PORT="${CELLS_PORT:-8083}" DAEMON_PORT="${CELLS_PORT:-8083}"
  if [ "${POSTGRES_DB:-}" != "ensemble_dev" ]; then
    echo "ABORT: POSTGRES_DB='${POSTGRES_DB:-<unset>}' != ensemble_dev"; exit 91
  fi
  [ -f "$FENCE/state.env" ] && { set -a; source "$FENCE/state.env"; set +a; }
  # Guard pin — export EXPLICITLY on every shell that may exec the daemon.
  export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
  export ENSEMBLE_CONFIG="$WT/config.yaml"
  export ENSEMBLE_DATA_DIR="$FENCE/data"
}

ensure_fence_state() {
  mkdir -p "$FENCE/roots/$ROOT" "$FENCE/data"
  if [ ! -f "$FENCE/state.env" ]; then
    KEY="$($PYBIN -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    VIEWS_ROOT="$FENCE/roots/$ROOT"
    ROOTS_JSON="$(printf '{"%s": {"type": "filesystem", "path": "%s"}, "tmp-images": {"type": "tmp_images"}}' "$ROOT" "$VIEWS_ROOT")"
    cat > "$FENCE/state.env" <<EOF
FENCE='$FENCE'
KEY='$KEY'
ENSEMBLE_DATA_DIR='$FENCE/data'
ROOTS_JSON='$ROOTS_JSON'
EOF
  fi
}

ensure_fixtures() {
  cat > "$FENCE/roots/$ROOT/$VIEW_A" <<'EOF'
# Degradation Cell View A

SECRET-CONTENT-CELLS-marker.
EOF
}

# ── probe helper (g2g8 shape): label + cmd + status + headers + body ────
probe() {
  local gf="$1" label="$2"; shift 2
  local hdr body st
  hdr="$(mktemp)"; body="$(mktemp)"
  {
    echo ""
    echo "#### $label"
    echo "\$ curl $*"
  } >> "$gf"
  st=$(curl -sS -o "$body" -D "$hdr" -w "%{http_code}" "$@" 2>>"$gf")
  {
    echo "-- status: $st"
    grep -v '^$' "$hdr" | sed 's/^/   /'
    echo "-- body (first 400B):"
    head -c 400 "$body" | sed 's/^/   /' | head -12
    echo ""
  } >> "$gf"
  cp "$gf" "$MIRROR/$(basename "$gf")" 2>/dev/null || true
  echo "[$label] status=$st grant=$(grep -qi '^set-cookie: view_grant' "$hdr" && echo YES || echo no) loc=$(grep -i '^location' "$hdr" | tr -d '\r' | awk '{print $2}' | head -c 60)"
  rm -f "$hdr" "$body"
}

# ── paced POST: token bucket capacity 5 / refill 5/min → >=13s gaps ─────
PACE="${CELLS_PACE:-14}"
paced_post() {
  local gf="$1" label="$2"; shift 2
  sleep "$PACE"
  probe "$gf" "$label" -X POST "$@"
}

BROWSER_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
export BROWSER_ACCEPT
