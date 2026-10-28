#!/usr/bin/env bash
# dbpool_collected_fence.sh — the §2.3 collected-count fence (db-pool-idle-txn).
#
# Materialised VERBATIM from test-strategy.md §2.3 (the fence script is
# authored there; this file is its executable form at the house packs
# location). Contract: final-implementation-spec.md §4.3 / §6.2 (DR-16 —
# the fence itself carries the FULL Class-1 fence; DR-23 — the EXPECTED
# count comes from the INDEPENDENT artifact expected-collected.md; Δ1 —
# a TBD cell at gate time is FATAL: the script integer-validates EXPECTED
# BEFORE comparing, so a non-integer can never slip through as a vacuous
# pass).
#
# Usage: dbpool_collected_fence.sh <declared expected count> <marker (or ""
#        for default)> [paths...]
#
# Exit codes: 0 = COLLECTED-OK; 1 = FATAL (non-integer EXPECTED, count
# mismatch, or vacuous 0-collected run).
set -euo pipefail
# $1 = declared expected count (from expected-collected.md), $2 = marker (or "" for default), $3.. = paths
EXPECTED="$1"; MARKER="$2"; shift 2
case "$EXPECTED" in ''|*[!0-9]*)
  echo "FATAL: EXPECTED is not an integer (TBD cell?) for: $*" >&2; exit 1;; esac
cd /home/nea/ensemble-src-wt-dbpool-idletxn
ARGS=()
[ -n "$MARKER" ] && ARGS+=(--override-ini="addopts=" -m "$MARKER")
COLLECTED=$(env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
  HOME="$(mktemp -d)" \
  /home/nea/ensemble-src/.venv/bin/python -m pytest "$@" "${ARGS[@]}" --collect-only -q 2>/dev/null \
  | grep -E 'tests? collected' | grep -oE '^[0-9]+' || true)
COLLECTED="${COLLECTED:-0}"
if [ "$COLLECTED" -ne "$EXPECTED" ]; then
  echo "FATAL: collected=${COLLECTED} expected=${EXPECTED} for: $*" >&2
  exit 1
fi
if [ "$COLLECTED" -le 0 ]; then
  echo "FATAL: vacuous run — 0 collected for: $*" >&2
  exit 1
fi
echo "COLLECTED-OK count=${COLLECTED} expected=${EXPECTED}"
