#!/usr/bin/env bash
# g2g8 lib — views-session-bypass HTTP behavior matrix (2026-11-09).
# FENCE: one mktemp root /tmp/lvg2g8.*; escape-guarded; never resolves
# live-shaped/HOME-derived paths. DB: dev PG ensemble_dev (asserted).
# KEY: ONE drill Fernet key per fence, reused across reboots.
# PORT: 8082 (fallback 8083). NEVER 8088. NEVER 8081/8443 (other worker).
# SWEEP: ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY
# (never env -u — this shell is a daemon child).
set -uo pipefail

export WT="/home/nea/ensemble-src-wt-views-session-bypass"
EV="$WT/.agents/tester/RESULTS/assets/2026-11-09-views-session-bypass/g2g8"
MIRROR="/tmp/g2g8-evidence"
export EV MIRROR
mkdir -p "$EV" "$MIRROR"

# ── fence ────────────────────────────────────────────────────────────────
FENCE_FILE="$EV/.fence-path"
if [ -f "$FENCE_FILE" ]; then
  FENCE="$(cat "$FENCE_FILE")"
else
  FENCE="$(mktemp -d /tmp/lvg2g8.XXXXXX)"
  case "$FENCE" in /tmp/lvg2g8.*) ;; *) echo "FENCE FAIL: $FENCE"; exit 99;; esac
  echo "$FENCE" > "$FENCE_FILE"
fi
case "$FENCE" in /tmp/lvg2g8.*) ;; *) echo "FENCE GUARD FAIL: $FENCE"; exit 99;; esac
export FENCE

export PORT="${G2G8_PORT:-8082}"
export BASE_URL="http://127.0.0.1:${PORT}"
ROOT="g2g8matrix"
export ROOT
VIEW_A="matrix-a.md"
VIEW_B="matrix-b.md"
export VIEW_A VIEW_B
PUB_A="/views/public/${ROOT}/${VIEW_A}"
PUB_B="/views/public/${ROOT}/${VIEW_B}"
export PUB_A PUB_B

# ── per-variable env scrub + fence state load ───────────────────────────
scrub_ambient() {
  # NEVER `env -u POSTGRES_*` (silent NO-OP). One-by-one.
  unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
  unset PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
  unset SSL_CERT_FILE SSL_CERT_DIR
  # Ambient shell inherits the LIVE daemon's auth env — every drill auth
  # value MUST be scrubbed then re-set, or live values (discovery URL,
  # client secret, EXTERNAL_REDIRECT_BASE proxy-front declaration) leak in.
  unset ENSEMBLE_AUTH_DISCOVERY_URL ENSEMBLE_AUTH_ISSUER ENSEMBLE_AUTH_CLIENT_ID
  unset ENSEMBLE_AUTH_CLIENT_SECRET ENSEMBLE_AUTH_ALLOWED_ORIGINS ENSEMBLE_AUTH_ENABLED
  unset ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE ENSEMBLE_AUTH_REDIRECT_URI
  unset ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL ENSEMBLE_LIVE_VIEWS_ROOTS
}

load_env() {
  scrub_ambient
  set -a; source "$WT/.env"; set +a
  if [ "${POSTGRES_DB:-}" != "ensemble_dev" ]; then
    echo "ABORT: POSTGRES_DB='${POSTGRES_DB:-<unset>}' != ensemble_dev"; exit 91
  fi
  [ -f "$FENCE/state.env" ] && { set -a; source "$FENCE/state.env"; set +a; }
  # Guard pin — export EXPLICITLY on every shell that may exec the daemon.
  export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
  export PORT DAEMON_PORT="$PORT" HOST=0.0.0.0
  export ENSEMBLE_CONFIG="$WT/config.yaml"
  export ENSEMBLE_DATA_DIR="$FENCE/data"
}

# ── probe helper: logs label + command + status + headers + body head ───
# usage: probe <gatefile> <label> <curl args...>
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
  echo "[$label] status=$st ct=$(grep -i '^content-type' "$hdr" | tr -d '\r' | awk '{print $2}')"
  rm -f "$hdr" "$body"
}

# body SHA helper: probe_sha <gatefile> <label> <curl args...>  → echoes sha256
probe_sha() {
  local gf="$1" label="$2"; shift 2
  local hdr body st sha
  hdr="$(mktemp)"; body="$(mktemp)"
  st=$(curl -sS -o "$body" -D "$hdr" -w "%{http_code}" "$@" 2>/dev/null)
  sha=$(sha256sum "$body" | awk '{print $1}')
  {
    echo ""
    echo "#### $label"
    echo "\$ curl $*"
    echo "-- status: $st   body-sha256: $sha"
    grep -i '^content-type' "$hdr" | sed 's/^/   /' | tr -d '\r'
  } >> "$gf"
  cp "$gf" "$MIRROR/$(basename "$gf")" 2>/dev/null || true
  echo "[$label] status=$st sha=$sha"
  rm -f "$hdr" "$body"
}

BROWSER_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
export BROWSER_ACCEPT
