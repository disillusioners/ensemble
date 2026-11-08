#!/usr/bin/env bash
# g2g8 session mint runner — bash env setup, then insert AuthSession rows.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
export FENCE
timeout 60 .venv/bin/python "$HERE/mint_sessions.py" 2>&1 | tee "$EV/session-mint.txt"
