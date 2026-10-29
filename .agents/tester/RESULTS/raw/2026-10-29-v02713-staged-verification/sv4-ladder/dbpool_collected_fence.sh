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
# T-L9 (D-29 rationale, mirroring the ladder header): the interpreter
# path below is the certified /home/nea/ensemble-src/.venv/bin/python
# and the hard-coded cd target is the WORKTREE — the cd is LOAD-BEARING
# (from any other cwd the certified interpreter resolves daemon to the
# SHARED CHECKOUT); never run this fence from another checkout.
#
# Exit codes: 0 = COLLECTED-OK; 1 = FATAL (bad args, non-integer
# EXPECTED, a MISSING expected-collected.md artifact, a non-zero
# pytest exit (collection errors) or an errors-segment in the summary,
# count mismatch, or a vacuous 0-collected run).
set -euo pipefail
# T-M3: argument-count guard — under 2 args previously aborted on
# bash's raw 'unbound variable' instead of a usage line.
if [ "$#" -lt 2 ]; then
  echo "usage: $0 <declared expected count> <marker (or "" for default)> [paths...]" >&2
  exit 1
fi
# T-M3: the temp HOME is trap-removed (previously every invocation
# leaked one /tmp/tmp.* dir).
_TMP_HOME=$(mktemp -d)
trap '_rc=$?; rm -rf "$_TMP_HOME"; exit $_rc' EXIT
# $1 = declared expected count (from expected-collected.md), $2 = marker (or "" for default), $3.. = paths
EXPECTED="$1"; MARKER="$2"; shift 2
case "$EXPECTED" in ''|*[!0-9]*)
  echo "FATAL: EXPECTED is not an integer (TBD cell?) for: $*" >&2; exit 1;; esac
WORKTREE=/home/nea/ensemble-src-wt-stage-v02713
# T-L1: a MISSING expected-collected.md is the artifact-level defect —
# stat before anything else so the FATAL names it (this fence receives
# the count as an argument, but the artifact is the count's authority).
EXPECTED_ARTIFACT="$WORKTREE/.agents/shared/planning/db-pool-idle-txn/expected-collected.md"
if [ ! -f "$EXPECTED_ARTIFACT" ]; then
  echo "FATAL: expected-collected.md is MISSING at $EXPECTED_ARTIFACT (the declared counts' authority — R-1 lane artifact)" >&2
  exit 1
fi
cd "$WORKTREE"
ARGS=()
[ -n "$MARKER" ] && ARGS+=(--override-ini="addopts=" -m "$MARKER")
# T-M1: capture pytest's exit status AND its stderr — '2>/dev/null' +
# '|| true' + a count-only grep previously threw away all three, so
# 'collected N' could not be distinguished from 'collected N with
# errors' (count-preserving collection errors passed the fence).
_TMP_OUT=$(mktemp)
set +e
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
  HOME="$_TMP_HOME" \
  /home/nea/ensemble-src/.venv/bin/python -m pytest "$@" "${ARGS[@]}" --collect-only -q >"$_TMP_OUT" 2>&1
_PYTEST_RC=$?
set -e
if [ "$_PYTEST_RC" -ne 0 ]; then
  echo "FATAL: pytest --collect-only exited $_PYTEST_RC (collection errors?) for: $*" >&2
  echo "---- last 5 lines of retained output ----" >&2
  tail -5 "$_TMP_OUT" >&2
  echo "-----------------------------------------" >&2
  rm -f "$_TMP_OUT"
  exit 1
fi
if grep -qE '[1-9][0-9]* error' "$_TMP_OUT"; then
  echo "FATAL: the collection summary reports an errors segment for: $*" >&2
  grep -E '[1-9][0-9]* error' "$_TMP_OUT" | tail -2 >&2
  rm -f "$_TMP_OUT"
  exit 1
fi
COLLECTED=$(grep -E 'tests? collected' "$_TMP_OUT" | grep -oE '^[0-9]+' || true)
rm -f "$_TMP_OUT"
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
