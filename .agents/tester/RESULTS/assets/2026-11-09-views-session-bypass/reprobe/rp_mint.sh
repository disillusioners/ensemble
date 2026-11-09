#!/usr/bin/env bash
# rp mint runner — bash env setup, then mint the reprobe view.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rp_lib.sh"
load_env
set -a; source "$FENCE/state.env"; set +a
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON" SYSTEM_ENCRYPTION_KEY="$KEY"
export ROOT VIEW_R FENCE
timeout 120 .venv/bin/python "$HERE/rp_mint_views.py"
