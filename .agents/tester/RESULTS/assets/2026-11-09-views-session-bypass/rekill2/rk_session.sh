#!/usr/bin/env bash
# rk_session.sh — mint a REAL ensemble_sid session row (dev DB, in-process).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
export FENCE
timeout 120 .venv/bin/python "$HERE/rk_session.py"
