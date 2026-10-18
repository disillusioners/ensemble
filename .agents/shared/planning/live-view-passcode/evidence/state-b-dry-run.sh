#!/usr/bin/env bash
# T3.3(iii) State B dry-run drill — live-view-passcode P3.
#
# Boots the daemon LOCALLY from THIS worktree with auth ON and
# auth.anonymous_view_paths LEFT EMPTY (the whole point: the public
# lane must pass AuthMiddleware via the code-seeded
# tag_routes_by_path(app, path_prefix="/views/public") prefix tag).
#
# FENCING (critical-note convention): all mutable drill state lives
# under a single mktemp root. DELETION IS ESCAPE-GUARDED — only
# paths that literally start with the mktemp root are ever removed.
# The script NEVER resolves live-shaped paths (no $HOME-derived
# install dirs), never touches the live install, never binds a port
# other than DAEMON_PORT from the fenced .env (8079).
#
# DB: dev PG ensemble_dev @ 127.0.0.1:5432 (from the fenced .env).
# KEY: a FRESH drill-only SYSTEM_ENCRYPTION_KEY is minted per run —
# NEVER the live key.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"          # .../.agents/shared/planning/live-view-passcode/evidence
WT="$(cd "$HERE/../../../../.." && pwd)"                      # worktree root
echo "== drill worktree: $WT"
cd "$WT"

# ── 0. fence ────────────────────────────────────────────────────────────────
DRILL_TMP="$(mktemp -d /tmp/liveview-drill.XXXXXX)"
case "$DRILL_TMP" in /tmp/liveview-drill.*) ;; *) echo "FENCE FAIL: $DRILL_TMP"; exit 99;; esac
VIEWS_ROOT="$DRILL_TMP/views"
mkdir -p "$VIEWS_ROOT"
printf '# Drill view\n\nHello from the State B dry-run drill.\n' > "$VIEWS_ROOT/hello.md"
trap 'rc=$?; case "$DRILL_TMP" in /tmp/liveview-drill.*) rm -rf "$DRILL_TMP";; esac; exit $rc' EXIT

# ── 1. fenced env (source the worktree .env: POSTGRES_*, DAEMON_PORT) ──────
set -a; source "$WT/.env"; set +a
export ENSEMBLE_DATA_DIR="$DRILL_TMP/data"
mkdir -p "$ENSEMBLE_DATA_DIR"

# Fresh drill-only encryption key (NOT the live key)
FERNET_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export SYSTEM_ENCRYPTION_KEY="$FERNET_KEY"

# Auth ON, anonymous_view_paths UNSET (ships/stays empty — the drill proves
# the code-seeded prefix tag alone carries the exemption)
export ENSEMBLE_AUTH_ENABLED=true
export ENSEMBLE_AUTH_CLIENT_ID=drill-client
export ENSEMBLE_AUTH_CLIENT_SECRET=drill-secret
export ENSEMBLE_AUTH_ISSUER=https://drill.invalid
export ENSEMBLE_AUTH_ALLOWED_ORIGINS="http://127.0.0.1:${DAEMON_PORT:-8079}"

# Drill-local filesystem root (project_scoped defaults would resolve against
# a project workdir we do not have in a drill)
export ENSEMBLE_LIVE_VIEWS_ROOTS="{\"drillroot\": {\"type\": \"filesystem\", \"path\": \"$VIEWS_ROOT\"}}"

PORT="${DAEMON_PORT:-8079}"
BASE="http://127.0.0.1:${PORT}"

echo "== fenced env: data_dir=$ENSEMBLE_DATA_DIR port=$PORT views_root=$VIEWS_ROOT"
echo "== auth: ENSEMBLE_AUTH_ENABLED=$ENSEMBLE_AUTH_ENABLED anonymous_view_paths=<unset>"

# ── 2. port-free check ──────────────────────────────────────────────────────
if ss -ltn "sport = :${PORT}" | grep -q LISTEN; then
  echo "PORT ${PORT} NOT FREE — aborting"; exit 98
fi
echo "== port ${PORT} free"

# ── 3. bounded boot ─────────────────────────────────────────────────────────
BOOT_LOG="$HERE/state-b-boot.log"
: > "$BOOT_LOG"
setsid .venv/bin/python -m daemon >> "$BOOT_LOG" 2>&1 &
BOOT_PID=$!
echo "== booting daemon pid=$BOOT_PID (log: state-b-boot.log)"

UP=0
for i in $(seq 1 90); do
  if curl -s -o /dev/null --max-time 2 "$BASE/views/livez"; then UP=1; break; fi
  if ! kill -0 "$BOOT_PID" 2>/dev/null; then echo "== BOOT DIED after ${i}s"; break; fi
  sleep 1
done
if [ "$UP" != "1" ]; then
  echo "== daemon did not come up in 90s — tail of boot log:"
  tail -30 "$BOOT_LOG"
  kill -TERM -- -"$BOOT_PID" 2>/dev/null; sleep 3; kill -KILL -- -"$BOOT_PID" 2>/dev/null
  exit 97
fi
echo "== daemon UP (waited ${i}s)"

# ── 5. the curl sequence ────────────────────────────────────────────────────
probe() { # probe <label> <curl-args...>
  local label="$1"; shift
  echo ""
  echo "--- PROBE: $label"
  curl -sS -o /tmp/lv-drill-body -D /tmp/lv-drill-headers --max-time 10 "$@" | true
  sed -n '1p;/^[Cc]ontent-[Tt]ype\|^[Ll]ocation\|^[Ss]et-[Cc]ookie/p' /tmp/lv-drill-headers
  echo "    body-bytes: $(wc -c < /tmp/lv-drill-body) first-line: $(head -c 120 /tmp/lv-drill-body | tr '\n' ' ')"
}

probe "a) GET /views/livez (PUBLIC exact tag — unaffected)"                       "$BASE/views/livez"
probe "b) GET /views/public/assets/mermaid/mermaid.min.js (gate-free alias)"     "$BASE/views/public/assets/mermaid/mermaid.min.js"
probe "c) GET /views/public/drillroot/hello.md UNMINTED (expect uniform 404 FROM DAEMON, NOT a login redirect)" "$BASE/views/public/drillroot/hello.md"
probe "c2) GET /views/public/drillroot/absent-file.md GUARANTEED-UNMINTED (expect uniform 404 from daemon — the row-gating posture)" "$BASE/views/public/drillroot/absent-file.md"

# ── 4b. mint AFTER the unminted probe (same code path as view_link) ─────────
MINT_OUT="$(SYSTEM_ENCRYPTION_KEY="$FERNET_KEY" POSTGRES_HOST="$POSTGRES_HOST" POSTGRES_PORT="$POSTGRES_PORT" POSTGRES_DB="$POSTGRES_DB" POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  .venv/bin/python - <<'PYEOF'
import os
from sqlalchemy import create_engine
from daemon.services.view_passcode_service import ViewPasscodeCipher, ViewPasscodeService
url = f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
svc = ViewPasscodeService(engine=create_engine(url), cipher=ViewPasscodeCipher())
try:
    r = svc.mint("drillroot", "hello.md", actor="p3-drill")
except Exception as rot:
    # A row minted by a PREVIOUS drill run under a different drill key
    # surfaces ViewPasscodeRotationRequired (the R4 coupling, fail-closed
    # by design). The documented recovery is refresh (view_link(refresh=True)).
    print(f"mint -> {type(rot).__name__}; regenerating via refresh (documented recovery)")
    r = svc.refresh("drillroot", "hello.md", actor="p3-drill")
print(f"MINT passcode={r.passcode} generation={r.generation} created={r.created} locked={r.locked_status}")
PYEOF
)" || { echo "MINT FAILED: $MINT_OUT"; kill -TERM -- -"$BOOT_PID"; exit 96; }
echo "== $MINT_OUT"
PASSCODE="$(echo "$MINT_OUT" | grep -oE 'passcode=[A-Za-z0-9]{6}' | cut -d= -f2)"

probe "e) GET /views/public/drillroot/hello.md (interstitial after mint)"        "$BASE/views/public/drillroot/hello.md"
probe "f) POST verify wrong code + SAME-HOST Origin (must NOT 403 — proceeds to compare/deny)" \
      "-X" "POST" "--data-urlencode" "code=wrong0" \
      "-H" "Origin: http://127.0.0.1:${PORT}" "$BASE/views/public/drillroot/hello.md"
probe "g) POST verify + CROSS-HOST Origin (MUST 403 — Host-preservation assertion counterpart)" \
      "-X" "POST" "--data-urlencode" "code=wrong0" \
      "-H" "Origin: http://evil.example" "$BASE/views/public/drillroot/hello.md"
probe "h-plain) POST verify CORRECT code over PLAIN HTTP (expect the secure-context page — D6:79/S11 — NO cookie, NO redirect loop)" \
      "-X" "POST" "--data-urlencode" "code=$PASSCODE" \
      "-H" "Origin: http://127.0.0.1:${PORT}" "$BASE/views/public/drillroot/hello.md"
echo "    secure-context page copy present? $(grep -c -i 'Secure connection required' /tmp/lv-drill-body)"
probe "h-https) POST verify CORRECT code + X-Forwarded-Proto: https (edge-emulated; expect 303 PRG + Set-Cookie view_grant)" \
      "-X" "POST" "--data-urlencode" "code=$PASSCODE" \
      "-H" "Origin: http://127.0.0.1:${PORT}" \
      "-H" "X-Forwarded-Proto: https" "$BASE/views/public/drillroot/hello.md"
COOKIE="$(grep -oE 'view_grant=[^;]+' /tmp/lv-drill-headers | head -1)"
echo "    captured cookie: ${COOKIE:0:24}...(len ${#COOKIE})"
probe "i2) GET with captured grant cookie (expect view bytes through the gate)" \
      "-H" "Cookie: $COOKIE" \
      "-H" "X-Forwarded-Proto: https" "$BASE/views/public/drillroot/hello.md"
probe "j) GET /views/drillroot/hello.md DIRECT LANE unauthenticated (expect 302 to /api/auth/login)" "$BASE/views/drillroot/hello.md"
probe "k) GET /api/instances (expect 302/401 — still guarded)"                   "$BASE/api/instances"

# ── 6. clean shutdown ────────────────────────────────────────────────────────
echo ""
echo "== shutting down (TERM → wait → KILL fallback)"
kill -TERM -- -"$BOOT_PID" 2>/dev/null
for t in $(seq 1 30); do kill -0 "$BOOT_PID" 2>/dev/null || break; sleep 1; done
kill -0 "$BOOT_PID" 2>/dev/null && { echo "== TERM timeout — KILL"; kill -KILL -- -"$BOOT_PID" 2>/dev/null; }
echo "== daemon stopped; drill complete"
