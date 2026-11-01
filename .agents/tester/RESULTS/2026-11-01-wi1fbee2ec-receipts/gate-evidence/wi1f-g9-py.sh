#!/bin/bash
# wi-1fbee2ec G9 — single-invocation pytest runner (BASE/HEAD sides, identical flags).
# Args: WT  FILE [FILE...]
# Env hygiene: per-variable scrub of ambient LIVE pointers (POSTGRES_*/DATABASE_URL/PORT),
# then LANE-FENCE overrides (ENSEMBLE_SELF_ENV=dev PORT=8079 per KV wt.claim.obligation) —
# ambient shell carries ENSEMBLE_SELF_ENV=live + PORT=9797; fence prevents live resolution.
# PYTHONDONTWRITEBYTECODE + -p no:cacheprovider = no writes into the worktrees.
wt=$1; shift
cd "$wt" || exit 99
exec env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT \
  ENSEMBLE_SELF_ENV=dev PORT=8079 \
  PYTHONDONTWRITEBYTECODE=1 \
  /home/nea/ensemble-src/.venv/bin/python -m pytest "$@" -q --tb=no -rfE -p no:randomly --timeout=120 -p no:cacheprovider
