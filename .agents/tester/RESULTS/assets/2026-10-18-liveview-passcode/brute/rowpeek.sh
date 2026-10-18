#!/usr/bin/env bash
# rowpeek helper — read-only PG row introspection (post-phase-A fix)
set -euo pipefail
WT=/home/nea/ensemble-src-wt-liveview-passcode
FENCE=/tmp/liveview-brute-fence
set -a; source "$WT/.env"; set +a
FERNET_KEY="$(grep ^FERNET_KEY "$FENCE/state.env" | cut -d= -f2- | tr -d '"')"
export SYSTEM_ENCRYPTION_KEY="$FERNET_KEY"
export POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
test "$POSTGRES_DB" = "ensemble_dev" || { echo "ABORT: not dev DB"; exit 98; }
exec "$WT/.venv/bin/python" /home/nea/ensemble-src-wt-liveview-passcode/.agents/tester/RESULTS/assets/2026-10-18-liveview-passcode/brute/rowpeek.py "${1:-both}"
