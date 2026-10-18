#!/usr/bin/env bash
# ============================================================================
# S2 — auth-ON State-B drill (live-view-passcode, commission item 5)
#
# Boots THIS worktree's daemon with ENSEMBLE_AUTH_ENABLED=true + placeholder
# OIDC (drill.invalid — discovery failure is WARN-not-fatal) and
# anonymous_view_paths UNSET (the code-seeded /views/public prefix tag is the
# ONLY exemption carrier — ADR D3). Proves the State-B shape:
#   S2a  direct legacy lane unauthenticated   -> 302 /api/auth/login
#   S2b  PUBLIC lane unauthenticated, MINTED  -> 200 interstitial (NOT 302)
#   S2c  mint -> correct code POST (XFP https)-> 303 + view_grant cookie
#            -> GET with cookie               -> 200 view bytes
#   S2d  unminted public path                 -> uniform daemon 404 (no leak)
#   S2e  /api/instances unauthenticated       -> 302 login (control)
#
# FENCING: identical to s1-parity-drill.sh — single mktemp root, escape-guarded
# deletion, no HOME-derived/live-shaped paths, port 8085 owned + released by
# this script, 8088 never touched. POSTGRES_DB must be ensemble_dev (asserted).
# KEY: fresh drill-only SYSTEM_ENCRYPTION_KEY.
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(git -C "$HERE" rev-parse --show-toplevel)"
cd "$WT"
echo "== S2 worktree: $WT"
if [ ! -f "$WT/.env" ] || [ ! -d "$WT/daemon" ] || [ ! -x "$WT/.venv/bin/python" ]; then
  echo "ABORT: $WT does not look like the worktree root"; exit 97
fi
EVID="$HERE"

# ── 0. fence ────────────────────────────────────────────────────────────────
DRILL_TMP="$(mktemp -d /tmp/lv-parity-s2.XXXXXX)"
case "$DRILL_TMP" in /tmp/lv-parity-s2.*) ;; *) echo "FENCE FAIL: $DRILL_TMP"; exit 99;; esac
VIEWS_ROOT="$DRILL_TMP/views"
mkdir -p "$VIEWS_ROOT"
printf '# parity hello\n' > "$VIEWS_ROOT/hello.md"
trap 'rc=$?
  if [ -n "${BOOT_PID:-}" ]; then
    kill -TERM -- -"$BOOT_PID" 2>/dev/null
    sleep 2
    kill -KILL -- -"$BOOT_PID" 2>/dev/null
  fi
  case "$DRILL_TMP" in /tmp/lv-parity-s2.*) rm -rf "$DRILL_TMP";; esac
  exit $rc' EXIT

# ── 1. fenced env (auth-ON State-B) ─────────────────────────────────────────
set -a; source "$WT/.env"; set +a
if [ "${POSTGRES_DB:-}" != "ensemble_dev" ]; then
  echo "ABORT: POSTGRES_DB='${POSTGRES_DB:-<unset>}' != ensemble_dev — refusing to boot"; exit 97
fi
echo "== POSTGRES_DB=ensemble_dev asserted"
export ENSEMBLE_SELF_ENV=dev
export PORT=8085            # config.yaml:111 substitutes ${PORT:-8088} — ambient PORT=9797 MUST be overridden
export DAEMON_PORT=8085
export ENSEMBLE_DATA_DIR="$DRILL_TMP/data"
mkdir -p "$ENSEMBLE_DATA_DIR"
FERNET_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export SYSTEM_ENCRYPTION_KEY="$FERNET_KEY"
export ENSEMBLE_LIVE_VIEWS_ROOTS="{\"parityroot1\": {\"type\": \"filesystem\", \"path\": \"$VIEWS_ROOT\"}}"
export ENSEMBLE_AUTH_ENABLED=true
export ENSEMBLE_AUTH_CLIENT_ID=drill-client
export ENSEMBLE_AUTH_CLIENT_SECRET=drill-secret
export ENSEMBLE_AUTH_ISSUER=https://drill.invalid
export ENSEMBLE_AUTH_ALLOWED_ORIGINS="http://127.0.0.1:8085"
# anonymous_view_paths deliberately UNSET (ADR D3 — code-seeded prefix tag only)
unset ENSEMBLE_AUTH_ANONYMOUS_VIEW_PATHS 2>/dev/null || true
echo "== fenced env: port=8085 views_root=$VIEWS_ROOT auth=ON issuer=$ENSEMBLE_AUTH_ISSUER anonymous_view_paths=<unset>"

# ── 2. port-free + bounded boot ─────────────────────────────────────────────
if ss -ltn "sport = :8085" | grep -q LISTEN; then
  echo "PORT 8085 NOT FREE — aborting"; exit 98
fi
BOOT_LOG="$EVID/s2-boot.log"
: > "$BOOT_LOG"
setsid .venv/bin/python -m daemon >> "$BOOT_LOG" 2>&1 &
BOOT_PID=$!
echo "== booting daemon pgid=$BOOT_PID"
UP=0
for i in $(seq 1 90); do
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:8085/views/livez"; then UP=1; break; fi
  if ! kill -0 "$BOOT_PID" 2>/dev/null; then echo "== BOOT DIED after ${i}s"; break; fi
  sleep 1
done
if [ "$UP" != "1" ]; then
  echo "== daemon did not come up — tail:"; tail -20 "$BOOT_LOG"; exit 96
fi
echo "== daemon UP (waited ${i}s)"
LISTENER_PID="$(ss -ltnp "sport = :8085" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)"
if [ -n "$LISTENER_PID" ] && [ "$LISTENER_PID" != "$BOOT_PID" ]; then
  PGID_OF_LISTENER="$(ps -o pgid= -p "$LISTENER_PID" 2>/dev/null | tr -d ' ' || true)"
  if [ "$PGID_OF_LISTENER" != "$BOOT_PID" ]; then
    echo "ABORT: 8085 held outside our process group — refusing"; exit 95
  fi
fi

BASE="http://127.0.0.1:8085"

capture() { # capture <slug> <curl-args...>
  local slug="$1"; shift
  local hf="$EVID/s2-${slug}.headers" bf="$EVID/s2-${slug}.body"
  curl -s --max-time 15 -D "$hf" -o "$bf" "$@" || return 1
  { cat "$hf"; echo "--- BODY ($(wc -c < "$bf") bytes) ---"; cat "$bf"; } > "$EVID/s2-${slug}.transcript.txt"
}

# ── 3. pre-mint posture (row absent — first-ever parityroot1 run) ───────────
echo "== S2a: direct legacy lane unauthenticated"
capture s2a-direct-hello "$BASE/views/parityroot1/hello.md"
echo "== S2b-pre: PUBLIC lane pre-mint (row absent — row-gating posture)"
capture s2b-pre-public-hello-unminted "$BASE/views/public/parityroot1/hello.md"
capture s2d-public-absent-unminted "$BASE/views/public/parityroot1/no-such-file.html"

# ── 4. MINT (ViewPasscodeService — the same service call view_link makes;
#       view_link itself is instance-bound and cannot be invoked without a
#       live instance; seam noted per task) ─────────────────────────────────
echo "== minting passcode for (parityroot1, hello.md)"
MINT_OUT="$(
SYSTEM_ENCRYPTION_KEY="$FERNET_KEY" POSTGRES_HOST="$POSTGRES_HOST" POSTGRES_PORT="$POSTGRES_PORT" \
POSTGRES_DB="$POSTGRES_DB" POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  .venv/bin/python - <<'PYEOF'
import os
from sqlalchemy import create_engine
from daemon.services.view_passcode_service import ViewPasscodeCipher, ViewPasscodeService
url = f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
svc = ViewPasscodeService(engine=create_engine(url), cipher=ViewPasscodeCipher())
try:
    r = svc.mint("parityroot1", "hello.md", actor="s2-state-b-drill")
except Exception as rot:
    print(f"mint -> {type(rot).__name__}; regenerating via refresh (documented recovery)")
    r = svc.refresh("parityroot1", "hello.md", actor="s2-state-b-drill")
print(f"MINT passcode={r.passcode} generation={r.generation} created={r.created} locked={r.locked_status}")
PYEOF
)" || { echo "MINT FAILED: $MINT_OUT"; exit 94; }
echo "$MINT_OUT" | tee "$EVID/s2-mint.txt"
PASSCODE="$(echo "$MINT_OUT" | grep -oE 'passcode=[A-Za-z0-9]{6}' | cut -d= -f2)"
if [ -z "$PASSCODE" ]; then echo "ABORT: no passcode extracted"; exit 94; fi

# ── 5. S2b-post: PUBLIC lane unauthenticated, MINTED -> interstitial ────────
echo "== S2b-post: PUBLIC lane (minted) unauthenticated"
capture s2b-post-public-hello "$BASE/views/public/parityroot1/hello.md"

# ── 6. S2c: verify flow ─────────────────────────────────────────────────────
echo "== S2c-wrong: POST wrong code (same-host Origin)"
capture s2c-wrong-code -X POST --data-urlencode "code=wrong0" \
  -H "Origin: http://127.0.0.1:8085" "$BASE/views/public/parityroot1/hello.md"
echo "== S2c-plain: POST correct code over plain HTTP (secure-context page, D6)"
capture s2c-correct-plain -X POST --data-urlencode "code=$PASSCODE" \
  -H "Origin: http://127.0.0.1:8085" "$BASE/views/public/parityroot1/hello.md"
echo "== S2c-https: POST correct code + X-Forwarded-Proto: https (expect 303 + Set-Cookie)"
capture s2c-correct-https -X POST --data-urlencode "code=$PASSCODE" \
  -H "Origin: http://127.0.0.1:8085" \
  -H "X-Forwarded-Proto: https" "$BASE/views/public/parityroot1/hello.md"
COOKIE="$(grep -oiE 'set-cookie: view_grant=[^;[:space:]]+' "$EVID/s2-s2c-correct-https.headers" | head -1 | sed 's/^set-cookie: //I')"
echo "    captured cookie: ${COOKIE:0:32}... (len ${#COOKIE})"
if [ -z "$COOKIE" ]; then echo "ABORT: no view_grant cookie captured"; exit 94; fi
echo "== S2c-get: GET with grant cookie (expect view bytes)"
capture s2c-get-with-cookie -H "Cookie: $COOKIE" -H "X-Forwarded-Proto: https" \
  "$BASE/views/public/parityroot1/hello.md"

# ── 7. S2d/S2e + livez sanity ───────────────────────────────────────────────
echo "== S2d done above (pre-mint captures; post-mint unminted path also captured)"
capture s2d-postmint-public-absent "$BASE/views/public/parityroot1/no-such-file.html"
echo "== S2e: control /api/instances unauthenticated"
capture s2e-api-instances "$BASE/api/instances"
echo "== sanity: /views/livez (public tag unaffected)"
capture sanity-livez "$BASE/views/livez"

# ── 8. verified shutdown ────────────────────────────────────────────────────
BOUND_NOW="$(ss -ltnp "sport = :8085" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)"
if [ -n "$BOUND_NOW" ] && [ -n "$LISTENER_PID" ] && [ "$BOUND_NOW" != "$LISTENER_PID" ]; then
  echo "REFUSING SHUTDOWN: 8085 held by pid $BOUND_NOW, expected $LISTENER_PID"; exit 95
fi
kill -TERM -- -"$BOOT_PID" 2>/dev/null
for t in $(seq 1 30); do kill -0 "$BOOT_PID" 2>/dev/null || break; sleep 1; done
kill -0 "$BOOT_PID" 2>/dev/null && kill -KILL -- -"$BOOT_PID" 2>/dev/null
sleep 1
ss -ltn "sport = :8085" | grep -q LISTEN && echo "WARN: 8085 still listening" || echo "== 8085 released"
echo "== S2 captures done"
exit 0
