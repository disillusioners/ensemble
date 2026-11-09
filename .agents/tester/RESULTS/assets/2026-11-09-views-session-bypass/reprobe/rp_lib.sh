#!/usr/bin/env bash
# reprobe lib — G2.7 FIX RE-PROBE (2026-11-09, tip 6398a29a3).
# FENCE: one fresh mktemp root /tmp/lvreprobe.*; escape-guarded; FAKE_HOME
# inside the fence. DB: dev PG ensemble_dev (asserted). KEY: ONE drill
# Fernet key per fence (generated once in state.env, reused on reboots).
# PORT: 8082 ONLY (8083 = other worker; 8088 NEVER; 8081/8443 NEVER).
# SWEEP: ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 exported EXPLICITLY.
# AUTH: ambient live-door vars scrubbed PER-VARIABLE, re-scrubbed AFTER
# .env source (belt+braces; POK auth-lane-scrub-ensemble-auth-env).
set -uo pipefail

export WT="/home/nea/ensemble-src-wt-views-session-bypass"
EV="$WT/.agents/tester/RESULTS/assets/2026-11-09-views-session-bypass/reprobe"
MIRROR="/tmp/lvreprobe-evidence"
export EV MIRROR
mkdir -p "$EV" "$MIRROR"

# ── fence (fresh; escape-guarded) ────────────────────────────────────────
FENCE_FILE="$EV/.fence-path"
if [ -f "$FENCE_FILE" ]; then
  FENCE="$(cat "$FENCE_FILE")"
else
  FENCE="$(mktemp -d /tmp/lvreprobe.XXXXXX)"
  case "$FENCE" in /tmp/lvreprobe.*) ;; *) echo "FENCE FAIL: $FENCE"; exit 99;; esac
  echo "$FENCE" > "$FENCE_FILE"
fi
case "$FENCE" in /tmp/lvreprobe.*) ;; *) echo "FENCE GUARD FAIL: $FENCE"; exit 99;; esac
case "$FENCE" in "$WT"*) echo "FENCE ESCAPE: inside worktree"; exit 99;; esac
export FENCE

export PORT="${G27RP_PORT:-8082}"
export BASE_URL="http://127.0.0.1:${PORT}"
ROOT="g27fix"
VIEW_R="reprobe.md"
export ROOT VIEW_R
PUB_R="/views/public/${ROOT}/${VIEW_R}"
export PUB_R

# ── per-variable env scrub (NEVER `env -u X_*` — silent NO-OP) ──────────
scrub_ambient() {
  unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
  unset PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
  unset SSL_CERT_FILE SSL_CERT_DIR
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
  # POK belt+braces: re-scrub the live-door auth vars AFTER .env source,
  # then PROVE they are gone (ambient shell inherited live door.ensem.dev).
  unset ENSEMBLE_AUTH_DISCOVERY_URL ENSEMBLE_AUTH_ISSUER ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  unset ENSEMBLE_AUTH_CLIENT_ID ENSEMBLE_AUTH_CLIENT_SECRET
  unset ENSEMBLE_AUTH_ALLOWED_ORIGINS ENSEMBLE_AUTH_ENABLED
  for v in ENSEMBLE_AUTH_ISSUER ENSEMBLE_AUTH_DISCOVERY_URL ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE; do
    if [ -n "${!v:-}" ]; then echo "ABORT: ambient $v survived scrub"; exit 92; fi
  done
  [ -f "$FENCE/state.env" ] && { set -a; source "$FENCE/state.env"; set +a; }
  export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
  export PORT DAEMON_PORT="$PORT" HOST=0.0.0.0
  export ENSEMBLE_CONFIG="$WT/config.yaml"
  export ENSEMBLE_DATA_DIR="$FENCE/data"
  # FAKE_HOME — never resolve HOME-derived/live-shaped paths (2026-10-12 rule).
  mkdir -p "$FENCE/home"
  export HOME="$FENCE/home"
}

# ── probe helper: label + command + status + headers + body head ────────
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
    echo "-- set-cookie present: $(grep -ci '^set-cookie' "$hdr")"
    echo "-- body (first 400B):"
    head -c 400 "$body" | sed 's/^/   /' | head -12
    echo ""
  } >> "$gf"
  cp "$gf" "$MIRROR/$(basename "$gf")" 2>/dev/null || true
  echo "[$label] status=$st ct=$(grep -i '^content-type' "$hdr" | tr -d '\r' | awk '{print $2}') sc=$(grep -ci '^set-cookie' "$hdr")"
  rm -f "$hdr" "$body"
}

BROWSER_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
export BROWSER_ACCEPT

# ── rowpeek (in-DB failed_attempts ledger for the drill row) ────────────
rowpeek() {
  local label="$1" lf="$EV/ROWPEEK-LEDGER.txt"
  local out
  out="$(timeout 60 .venv/bin/python "$EV/rp_rowpeek.py" 2>/dev/null)"
  { echo "$(date -u +%FT%TZ) [$label] $out"; } >> "$lf"
  cp "$lf" "$MIRROR/$(basename "$lf")" 2>/dev/null || true
  echo "ROWPEEK [$label] $out"
}
