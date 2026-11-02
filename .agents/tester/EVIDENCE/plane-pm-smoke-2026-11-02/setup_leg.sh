#!/usr/bin/env bash
# GATE E — phase-6 staged-smoke REHEARSAL — per-leg scratch setup.
# Creates: scratch root, FAKE_HOME, throwaway PG role + scratch DB (LOCAL
# 127.0.0.1:5432 only), a free non-live port, and the per-leg env file.
#
# NEVER touches /home/nea/agents-ensemble (live), :9797, :7979, :8079, :8088,
# ensemble_prod, or any .env file. The armed worktree .env is NOT read here
# except for a READ-ONLY cross-check of the public Plane URL (hash compare,
# never echoed).
set -euo pipefail

RUN=/tmp/plane-pm-smoke-2026-11-02
WT=/home/nea/ensemble-src-wt-plane-pm
LEG="$1"          # l1 | l2 | l3 | l4
SELF_ENV="$2"     # dev | live
SLUG_MODE="$3"    # e2e | nea | unset

case "$LEG" in l1|l2|l3|l4) : ;; *) echo "FATAL: bad leg $LEG" >&2; exit 93 ;; esac
case "$SELF_ENV" in dev|live) : ;; *) echo "FATAL: bad self env" >&2; exit 93 ;; esac
case "$SLUG_MODE" in e2e|nea|unset) : ;; *) echo "FATAL: bad slug mode" >&2; exit 93 ;; esac

# ── F1 scratch root + FAKE_HOME ────────────────────────────────────────────
SCRATCH=$(mktemp -d "/tmp/plane-pm-smoke.${LEG}.scratch.XXXXXX")
FAKE_HOME=$(mktemp -d "/tmp/plane-pm-smoke.${LEG}.home.XXXXXX")

# ── F6 escape guard: neither path may sit under a real install shape ───────
for p in "$SCRATCH" "$FAKE_HOME" "$FAKE_HOME/agents-ensemble"; do
    case "$p" in
        /home/nea/agents-ensemble*|/home/nea/ensemble-src*)
            echo "FATAL: escape guard tripped: $p" >&2; exit 94 ;;
    esac
done
[ "$SCRATCH" != "$FAKE_HOME" ] || { echo "FATAL: scratch==fake_home" >&2; exit 94; }
mkdir -p "$SCRATCH/data" "$SCRATCH/tmp" "$FAKE_HOME/agents-ensemble/releases"

# ── F7 scratch PG — LOCAL cluster only, throwaway role ────────────────────
ROLE="plane_pm_smoke_${LEG}_$(date +%s)_$RANDOM"
DB="ens_smoke_${LEG}_$(date +%s)_$RANDOM"
ROLE_PW=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 28)
psql -U nea -d postgres -tAc "CREATE ROLE \"$ROLE\" LOGIN PASSWORD '$ROLE_PW'" >/dev/null
psql -U nea -d postgres -tAc "CREATE DATABASE \"$DB\" OWNER \"$ROLE\"" >/dev/null

# ── F8 free port — never a live/live-demo/dev/8088 port ───────────────────
PORT=""
for cand in 8951 8953 8955 8957 8959 8961 8963 8965; do
    case "$cand" in 9797|7979|8079|8088|8971|8972) continue ;; esac
    if ! ss -ltn 2>/dev/null | grep -qE ":${cand}\b"; then PORT="$cand"; break; fi
done
[ -n "$PORT" ] || { echo "FATAL: no free port" >&2; exit 94; }

# ── the PUBLIC Plane URL (public in .env.example; NOT a secret) ───────────
PLANE_URL=$(awk -F= '/^#?PLANE_MCP_URL=/ {gsub(/^[ \t]*#?[ \t]*PLANE_MCP_URL=/,""); gsub(/^[ \t]+|[ \t]+$/,""); print; exit}' "$WT/.env.example")
[ -n "$PLANE_URL" ] || { echo "FATAL: no public PLANE_MCP_URL in .env.example" >&2; exit 95; }
# read-only cross-check against the armed worktree .env — HASH COMPARE ONLY
# (the value is never echoed; the armed .env is never modified)
WT_URL_HASH=$(python3 - "$WT/.env" <<'PYEOF'
import sys
raw = ""
for line in open(sys.argv[1]):
    if line.startswith("PLANE_MCP_URL="):
        raw = line.split("=", 1)[1].strip()
        break
if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
    raw = raw[1:-1]
print(raw)
PYEOF
)
URL_MATCH="MISMATCH"
if [ -n "$WT_URL_HASH" ]; then
    a=$(printf '%s' "$PLANE_URL" | sha256sum | awk '{print $1}')
    b=$(printf '%s' "$WT_URL_HASH" | sha256sum | awk '{print $1}')
    [ "$a" = "$b" ] && URL_MATCH="MATCH"
fi

# ── the DUMMY key (presence-only semantics — never a real bearer) ──────────
DUMMY_KEY="plane_api_staged_smoke_dummy_${LEG}"

# ── write the per-leg env file (chmod 600) ────────────────────────────────
ENVF="$RUN/leg-${LEG}.env"
umask 077
cat > "$ENVF" <<EOF
SELF_ENV="$SELF_ENV"
SLUG_MODE="$SLUG_MODE"
SLUG_VALUE="$([ "$SLUG_MODE" = unset ] && echo '' || echo "$SLUG_MODE")"
PORT="$PORT"
ROLE="$ROLE"
DB="$DB"
ROLE_PW="$ROLE_PW"
SCRATCH="$SCRATCH"
FAKE_HOME="$FAKE_HOME"
DUMMY_KEY="$DUMMY_KEY"
PLANE_URL="$PLANE_URL"
URL_MATCH="$URL_MATCH"
EOF
chmod 600 "$ENVF"

echo "SETUP OK leg=$LEG self_env=$SELF_ENV slug=$SLUG_MODE port=$PORT role=$ROLE db=$DB"
echo "  scratch=$SCRATCH"
echo "  fake_home=$FAKE_HOME (live install dir resolves to $FAKE_HOME/agents-ensemble — INSIDE the scratch)"
echo "  public plane url vs armed worktree .env: $URL_MATCH"
