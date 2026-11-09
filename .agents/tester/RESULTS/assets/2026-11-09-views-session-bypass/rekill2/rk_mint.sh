#!/usr/bin/env bash
# rk_mint.sh — mint the drill rows (view target + board row) in-process.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
set -a; source "$FENCE/state.env"; set +a
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON" SYSTEM_ENCRYPTION_KEY="$KEY"
export ROOT VIEW_R FENCE
timeout 180 .venv/bin/python "$HERE/rk_mint.py"
