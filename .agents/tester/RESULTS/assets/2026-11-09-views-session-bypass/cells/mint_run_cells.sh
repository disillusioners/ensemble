#!/usr/bin/env bash
# mint wrapper — sources fence env then runs the in-process mint.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
set -a; source "$FENCE/state.env"; set +a
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON"
"$WT/.venv/bin/python" "$HERE/mint_cells.py"
