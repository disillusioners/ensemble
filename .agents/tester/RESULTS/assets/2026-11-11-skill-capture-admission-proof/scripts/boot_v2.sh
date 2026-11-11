#!/bin/bash
# boot_v2.sh — boot the STAGED ARTIFACT COPY in isolation (fresh TMPROOT, port 18912).
# umask 077 + ambient scrub BEFORE any capture (F7 harness rule).
umask 077
set -uo pipefail
EV=/tmp/ens-e2e-evidence/v2
TMPROOT=$(cat "$EV/tmp_root.txt")
FAKE_HOME=$(cat "$EV/fake_home.txt")
REL="$TMPROOT/releases/v0.29.17"
LOG="$EV/daemon-boot-v2.log"
PGPORT=$(cat "$EV/pg_port.txt")

cd "$TMPROOT" || exit 78

# pre-flight: refuse to boot over a stale listener on OUR port
if ss -ltn 2>/dev/null | grep -q ":18790 "; then
  echo "PRE-FLIGHT FAIL: port 18790 already bound (stale daemon?)" >&2
  exit 79
fi

# per-variable ambient scrub
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DATABASE_URL PGHOST PGPORT PGUSER PGPASSWORD
unset ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES
for v in $(env | sed -n 's/^\(OPENAI_[A-Z_]*\)=.*/\1/p'); do unset "$v"; done
for v in $(env | sed -n 's/^\(ENSEMBLE_AUTH_[A-Z_]*\)=.*/\1/p'); do unset "$v"; done
# Re-read PGPORT after the ambient scrub above (which unsets it).
PGPORT=$(cat "$EV/pg_port.txt")

export HOME="$FAKE_HOME"
export XDG_CONFIG_HOME="$FAKE_HOME/.config"
export XDG_CACHE_HOME="$FAKE_HOME/.cache"
export XDG_DATA_HOME="$FAKE_HOME/.local/share"
export TMPDIR="$FAKE_HOME/tmp"; mkdir -p "$TMPDIR"

export ENSEMBLE_DATA_DIR="$TMPROOT/data"
mkdir -p "$ENSEMBLE_DATA_DIR"
export ENSEMBLE_CONFIG="$REL/config.yaml"

export PORT=18790 DAEMON_PORT=18790 HOST=127.0.0.1

export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT="$PGPORT"
export POSTGRES_DB=ens_e2e_v02917
export POSTGRES_USER=pguser
export POSTGRES_PASSWORD=""

export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
export ENSEMBLE_ORPHAN_F1_ENABLED=0

# THE FLAGS UNDER TEST (explicit, both ON)
export ENSEMBLE_SKILL_CAPTURE_ENABLED=1
export ENSEMBLE_SKILL_EVOLUTION_ENABLED=1

export ENSEMBLE_SELF_ENV=sandbox

export OPENAI_BASE_URL="http://127.0.0.1:18791/v1"
export OPENAI_API_KEY="sk-local-e2e-mock-not-a-real-key"
export OPENAI_MODEL=agentic
export OPENAI_MODEL_VISION=vision
export OPENAI_MODEL_TITLE=title
export OPENAI_MODEL_KEYWORDS=quick
export OPENAI_STREAMING=false

export ENSEMBLE_AUTH_ENABLED=true
export ENSEMBLE_AUTH_ALLOW_INSECURE_COOKIE=true
export ENSEMBLE_AUTH_COOKIE_SECURE=false
export ENSEMBLE_AUTH_CLIENT_ID="e2e-local-client"
export ENSEMBLE_AUTH_CLIENT_SECRET="e2e-local-secret-not-real"
export ENSEMBLE_AUTH_ISSUER="http://127.0.0.1:18792/issuer"
export ENSEMBLE_AUTH_DISCOVERY_URL="http://127.0.0.1:18792/.well-known/openid-configuration"
export ENSEMBLE_AUTH_ALLOWED_ORIGINS="http://127.0.0.1:18790"
# one-shot bootstrap token for the auth CLI (consumed once; value never captured to evidence)
export ENSEMBLE_AUTH_BOOTSTRAP_TOKEN="e2e-bootstrap-local-not-a-real-secret"

: > "$LOG"
exec timeout --signal=TERM --kill-after=30 1500 "$REL/ensemble-prod" >>"$LOG" 2>&1
