#!/usr/bin/env bash
# g2g8 mint runner — bash env setup (state.env quoting), then mint views.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
set -a; source "$FENCE/state.env"; set +a
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON" SYSTEM_ENCRYPTION_KEY="$KEY"
export ROOT VIEW_A VIEW_B FENCE
timeout 120 .venv/bin/python "$HERE/mint_views.py"
