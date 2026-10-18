#!/usr/bin/env bash
# ============================================================================
# S1 — auth-OFF real-wire byte-parity drill (live-view-passcode, item 5)
#
# Boots THIS worktree's daemon auth-OFF on fenced port 8085 with a
# drill-local views root `parityroot1`, captures REAL-WIRE envelopes for
# the legacy direct lane /views/{root}/{path} (GET 200 / GET 404 / HEAD 200)
# plus the unminted PUBLIC lane 404 posture, and compares each against the
# committed golden fixture
#   tests/unit/routers/golden/live_views_direct_lane_envelope_BASE-859e41193.golden.txt
# using the UNIT serializer format (test_live_views_public_lane.py:2149).
#
# FENCING (critical-note convention): all mutable state lives under ONE
# mktemp root /tmp/lv-parity-s1.*; DELETION IS ESCAPE-GUARDED. No
# HOME-derived / live-shaped install paths are ever resolved. Cleanup kills
# ONLY the process group this script started, and only after verifying the
# bound port is 8085 held by that group. Port 8088 is NEVER touched.
#
# DB: dev PG ensemble_dev @ 127.0.0.1:5432 (from the fenced .env; boot is
# ABORTED unless POSTGRES_DB == ensemble_dev — never ensemble_prod).
# KEY: fresh drill-only SYSTEM_ENCRYPTION_KEY minted per run.
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Authoritative worktree root via git (this script lives INSIDE the worktree)
WT="$(git -C "$HERE" rev-parse --show-toplevel)"
cd "$WT"
echo "== S1 worktree: $WT"
EVID="$HERE"
if [ ! -f "$WT/.env" ] || [ ! -d "$WT/daemon" ] || [ ! -x "$WT/.venv/bin/python" ]; then
  echo "ABORT: $WT does not look like the worktree root (.env/daemon/.venv missing)"; exit 97
fi

# ── 0. fence ────────────────────────────────────────────────────────────────
DRILL_TMP="$(mktemp -d /tmp/lv-parity-s1.XXXXXX)"
case "$DRILL_TMP" in /tmp/lv-parity-s1.*) ;; *) echo "FENCE FAIL: $DRILL_TMP"; exit 99;; esac
VIEWS_ROOT="$DRILL_TMP/views"
mkdir -p "$VIEWS_ROOT"
# Mandated fixture: hello.md, exactly `# parity hello` + newline (15 bytes)
printf '# parity hello\n' > "$VIEWS_ROOT/hello.md"
# Supplemental golden-clone fixture: page.html carries the golden's EXACT
# body bytes (b'<h1>Landing</h1>', 16 bytes) so the GET-200 envelope can be
# compared STRICTLY (content-length + body byte-equal) at the wire level.
printf '<h1>Landing</h1>' > "$VIEWS_ROOT/page.html"
trap 'rc=$?
  if [ -n "${BOOT_PID:-}" ]; then
    kill -TERM -- -"$BOOT_PID" 2>/dev/null
    sleep 2
    kill -KILL -- -"$BOOT_PID" 2>/dev/null
  fi
  case "$DRILL_TMP" in /tmp/lv-parity-s1.*) rm -rf "$DRILL_TMP";; esac
  exit $rc' EXIT

# ── 1. fenced env ───────────────────────────────────────────────────────────
set -a; source "$WT/.env"; set +a
# MANDATORY pre-boot assert (never boot against ensemble_prod / unset)
if [ "${POSTGRES_DB:-}" != "ensemble_dev" ]; then
  echo "ABORT: POSTGRES_DB='${POSTGRES_DB:-<unset>}' != ensemble_dev — refusing to boot"; exit 97
fi
echo "== POSTGRES_DB=ensemble_dev asserted"
export ENSEMBLE_SELF_ENV=dev
# config.yaml:111 substitutes port: ${PORT:-8088} — the YAML reads PORT.
# Ambient PORT (this shell descends from the live daemon: PORT=9797) MUST be
# overridden or the drill binds the live port. DAEMON_PORT kept in sync for
# any code reading it directly.
export PORT=8085
export DAEMON_PORT=8085
export ENSEMBLE_DATA_DIR="$DRILL_TMP/data"
mkdir -p "$ENSEMBLE_DATA_DIR"
FERNET_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export SYSTEM_ENCRYPTION_KEY="$FERNET_KEY"
export ENSEMBLE_LIVE_VIEWS_ROOTS="{\"parityroot1\": {\"type\": \"filesystem\", \"path\": \"$VIEWS_ROOT\"}}"
# auth-OFF: ENSEMBLE_AUTH_ENABLED deliberately NOT set (worktree .env carries
# no auth keys — default is off). Assert we did not inherit one:
if [ "${ENSEMBLE_AUTH_ENABLED:-false}" != "false" ]; then
  echo "ABORT: ENSEMBLE_AUTH_ENABLED='${ENSEMBLE_AUTH_ENABLED}' — S1 must boot auth-OFF"; exit 97
fi
echo "== fenced env: data_dir=$ENSEMBLE_DATA_DIR port=8085 views_root=$VIEWS_ROOT auth=OFF"

# ── 2. port-free check ──────────────────────────────────────────────────────
if ss -ltn "sport = :8085" | grep -q LISTEN; then
  echo "PORT 8085 NOT FREE — aborting (never killing a port we did not bind)"; exit 98
fi
echo "== port 8085 free"

# ── 3. bounded boot ─────────────────────────────────────────────────────────
BOOT_LOG="$EVID/s1-boot.log"
: > "$BOOT_LOG"
setsid .venv/bin/python -m daemon >> "$BOOT_LOG" 2>&1 &
BOOT_PID=$!
echo "== booting daemon pgid=$BOOT_PID (log: $BOOT_LOG)"
UP=0
for i in $(seq 1 90); do
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:8085/views/livez"; then UP=1; break; fi
  if ! kill -0 "$BOOT_PID" 2>/dev/null; then echo "== BOOT DIED after ${i}s"; break; fi
  sleep 1
done
if [ "$UP" != "1" ]; then
  echo "== daemon did not come up in 90s — boot log tail:"
  tail -30 "$BOOT_LOG"
  exit 96
fi
echo "== daemon UP (waited ${i}s)"
# Remember the actual listener pid on 8085 for the kill-safety check
LISTENER_PID="$(ss -ltnp "sport = :8085" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)"
echo "== 8085 listener pid: ${LISTENER_PID:-unknown}"
if [ -n "$LISTENER_PID" ] && [ "$LISTENER_PID" != "$BOOT_PID" ]; then
  PGID_OF_LISTENER="$(ps -o pgid= -p "$LISTENER_PID" 2>/dev/null | tr -d ' ' || true)"
  if [ "$PGID_OF_LISTENER" != "$BOOT_PID" ]; then
    echo "ABORT: 8085 held by pid $LISTENER_PID outside our process group — refusing"; exit 95
  fi
fi

BASE="http://127.0.0.1:8085"

# ── 4. real-wire captures ───────────────────────────────────────────────────
capture() { # capture <label-slug> <url> [HEAD]
  local slug="$1" url="$2" mode="${3:-GET}"
  local hf="$EVID/s1-${slug}.headers" bf="$EVID/s1-${slug}.body"
  if [ "$mode" = "HEAD" ]; then
    curl -sI --max-time 10 -D "$hf" -o /dev/null "$url" || return 1
    : > "$bf"
  else
    curl -s --max-time 10 -D "$hf" -o "$bf" "$url" || return 1
  fi
  # full raw transcript (headers + body) for evidence
  { cat "$hf"; echo "--- BODY (${mode}, $(wc -c < "$bf") bytes) ---"; cat "$bf"; } > "$EVID/s1-${slug}.transcript.txt"
  return 0
}

S1_URL_HELLO="$BASE/views/parityroot1/hello.md"
S1_URL_ABSENT="$BASE/views/parityroot1/no-such-file.html"
S1_URL_PAGE="$BASE/views/parityroot1/page.html"

echo "== capturing direct-lane envelopes"
capture get200-hello  "$S1_URL_HELLO"  GET   || { echo "CAPTURE FAIL get200-hello"; exit 94; }
capture get404-absent "$S1_URL_ABSENT" GET   || { echo "CAPTURE FAIL get404-absent"; exit 94; }
capture head200-hello "$S1_URL_HELLO"  HEAD  || { echo "CAPTURE FAIL head200-hello"; exit 94; }
# supplemental strict-parity fixture (golden body clone)
capture page200       "$S1_URL_PAGE"   GET   || { echo "CAPTURE FAIL page200"; exit 94; }
# S1d — unminted PUBLIC lane (row absent for both paths at this point)
capture s1d-public-hello-unminted "$BASE/views/public/parityroot1/hello.md" GET || { echo "CAPTURE FAIL s1d-hello"; exit 94; }
capture s1d-public-absent-unminted "$BASE/views/public/parityroot1/no-such-file.html" GET || { echo "CAPTURE FAIL s1d-absent"; exit 94; }
echo "== captures complete"

# ── 5. clean shutdown (kill ONLY our group, port-verified) ─────────────────
BOUND_NOW="$(ss -ltnp "sport = :8085" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)"
if [ -n "$BOUND_NOW" ] && [ -n "$LISTENER_PID" ] && [ "$BOUND_NOW" != "$LISTENER_PID" ]; then
  echo "REFUSING SHUTDOWN: 8085 now held by pid $BOUND_NOW, expected $LISTENER_PID"; exit 95
fi
echo "== shutting down our process group (pgid=$BOOT_PID)"
kill -TERM -- -"$BOOT_PID" 2>/dev/null
for t in $(seq 1 30); do kill -0 "$BOOT_PID" 2>/dev/null || break; sleep 1; done
if kill -0 "$BOOT_PID" 2>/dev/null; then echo "== TERM timeout — KILL"; kill -KILL -- -"$BOOT_PID" 2>/dev/null; fi
sleep 1
if ss -ltn "sport = :8085" | grep -q LISTEN; then echo "WARN: 8085 still listening after shutdown"; else echo "== 8085 released"; fi

echo "== S1 captures done; fence cleaned on exit"
exit 0
