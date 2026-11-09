#!/usr/bin/env bash
# rk_lib.sh — RE-KILL 2 + third-class sweep lib (2026-11-09, tip 4be947eee)
# FENCE: one fresh mktemp root /tmp/lvrekill2.*; escape-guarded; FAKE_HOME
#        inside the fence. DB: dev PG ensemble_dev (asserted, never prod).
# KEY:   ONE drill Fernet key per fence (state.env, reused across reboots).
# PORT:  8082 ONLY (8088 NEVER; 8079/8443/9797 NEVER).
# SWEEP: ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 exported EXPLICITLY.
# AUTH:  ambient live-door vars scrubbed PER-VARIABLE (never `env -u X_*`),
#        re-scrubbed AFTER .env source (POK auth-lane-scrub-ensemble-auth-env).
set -uo pipefail

export WT="/home/nea/ensemble-src-wt-views-session-bypass"
export EV="$WT/.agents/tester/RESULTS/assets/2026-11-09-views-session-bypass/rekill2"
export MIRROR="/tmp/lvrekill2-mirror"
mkdir -p "$EV" "$MIRROR"

# ── fence (fresh; escape-guarded) ────────────────────────────────────────
FENCE_FILE="$EV/.fence-path"
if [ -f "$FENCE_FILE" ]; then
  FENCE="$(cat "$FENCE_FILE")"
else
  FENCE="$(mktemp -d /tmp/lvrekill2.XXXXXX)"
  case "$FENCE" in /tmp/lvrekill2.*) ;; *) echo "FENCE FAIL: $FENCE"; exit 99;; esac
  echo "$FENCE" > "$FENCE_FILE"
fi
case "$FENCE" in /tmp/lvrekill2.*) ;; *) echo "FENCE GUARD FAIL: $FENCE"; exit 99;; esac
case "$FENCE" in "$WT"*) echo "FENCE ESCAPE: inside worktree"; exit 99;; esac
export FENCE

export PORT="${RK_PORT:-8082}"
export BASE_URL="http://127.0.0.1:${PORT}"
export ROOT="g27fix"
export VIEW_R="rekill2.md"
export PUB_R="/views/public/${ROOT}/${VIEW_R}"
export BOARD_PUB_R="/views/public/board/index.html"

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
  # POK belt+braces: re-scrub the live-door auth vars AFTER .env source.
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
  mkdir -p "$FENCE/home"
  export HOME="$FENCE/home"
}

# ── probe helper: label + expectation + status + headers + body head ────
# probe <gf> <label> <expect|RECORD> <curl args...>
probe() {
  local gf="$1" label="$2" expect="$3"; shift 3
  local hdr body st ct sc verdict
  hdr="$(mktemp)"; body="$(mktemp)"
  {
    echo ""
    echo "#### $label"
    echo "EXPECT: $expect"
    echo "\$ curl $*"
  } >> "$gf"
  st=$(curl -sS -o "$body" -D "$hdr" -w "%{http_code}" "$@" 2>>"$gf")
  ct=$(grep -i '^content-type' "$hdr" | tr -d '\r' | head -1 | awk '{print $2}')
  sc=$(grep -ci '^set-cookie' "$hdr")
  if [ "$expect" = "RECORD" ]; then verdict="RECORD"
  elif [ "$st" = "$expect" ]; then verdict="MATCH"
  else verdict="*** MISMATCH ***"; fi
  {
    echo "-- status: $st   (expect $expect)  => $verdict"
    echo "-- content-type: ${ct:-<none>}   set-cookie headers: $sc"
    grep -v '^$' "$hdr" | sed 's/^/   /'
    echo "-- body (first 400B):"
    head -c 400 "$body" | sed 's/^/   /' | head -12
    echo ""
  } >> "$gf"
  cp "$gf" "$MIRROR/$(basename "$gf")" 2>/dev/null || true
  echo "[$label] status=$st expect=$expect => $verdict ct=${ct:-none} sc=$sc"
  rm -f "$hdr" "$body"
}

BROWSER_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
export BROWSER_ACCEPT

# ── rowpeek (in-DB failed_attempts ledger for the drill rows) ───────────
rowpeek() {
  local label="$1" lf="$EV/ROWPEEK-LEDGER.txt"
  local out
  out="$(timeout 60 .venv/bin/python "$EV/rk_rowpeek.py" 2>/dev/null)"
  { echo "$(date -u +%FT%TZ) [$label] $out"; } >> "$lf"
  cp "$lf" "$MIRROR/$(basename "$lf")" 2>/dev/null || true
  echo "ROWPEEK [$label] $out"
}

# ── pace (verify rate limiter: capacity 5, refill 5/min ⇒ 12s/token) ───
pace() { sleep "${1:-13}"; }
