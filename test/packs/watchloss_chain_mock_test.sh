#!/usr/bin/env bash
# Test Pack: watchloss_chain_mock_test — usage-limit retry-supersede
# double-silence chain regression (RCA job 043d64a8, e8a61da3).
#
# Drives the FULL chain end-to-end through REAL implementations
# (worker_pool deferral → schedule_retry → RetryTurn F6 re-key →
# notify_work_watchers settle + mission-terminal delivery), with a
# fidelity-guard assertion after every stage so a PASS proves the
# chain executed rather than mocks short-circuiting:
#
#   1. Worker._handle_usage_limit → deferral fires
#   2. schedule_retry → RetryTurn.run → F6 re-key moves the watcher
#      row from parent work_id to child work_id
#   3. notify_work_watchers settle (retry-receipt, mission live):
#      matching_readonly bucket at HEAD (mission_terminal armed);
#      matching_claimable bucket at BASE (row CAS-consumed before
#      mission_terminal could fire)
#   4. notify_work_watchers mission-terminal settle (mission dead):
#      mission_terminal firing honored at HEAD (count == 1);
#      zero watchers at BASE (row already CAS-consumed at step 3)
#
# A/B differential proof (the commission asks for the BASE pre-fix
# output as evidence; the reviewer flagged that in-tree "fails on
# pre-fix tree" claims were docstring-asserted but never re-run):
#
#   HEAD @ e3fea4556 (post-fix Option A+E): expect PASS — arming
#     in turn_transitions.RetryTurn._rearm_watchers_in_txn keeps
#     mission_terminal in the row's watch_events across the F6
#     re-key; the work_notifier multi-kind retire rule honors the
#     event at the mission-terminal settle.
#
#   BASE @ 5cb964187 (pre-fix): expect FAIL — no arming fires; the
#     row's events stay transport-only; the row is CAS-consumed at
#     the first settle (matching_claimable on transport kind alone);
#     the mission-terminal settle finds zero rows → mission_terminal
#     delivery count == 0 (the 043d64a8 watch-loss class).
#
# MANDATORY env scrub — ambient shell env carries LIVE-PROD DB
# coordinates from the developer's working session; never inherit
# (the SQLite-default test would otherwise try to migrate the
# live DB on collection):
#
#   env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER
#       -u POSTGRES_PASSWORD -u POSTGRES_DB -u POSTGRES_URL
#       -u DATABASE_URL -u ENSEMBLE_DB_DSN
#       -u SSL_CERT_FILE -u SSL_CERT_DIR
#
# Dual-layer timeout (per the test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 280s` on the pytest
#     process + pytest's `--override-ini="timeout=280"` + blank
#     addopts (the project default `-m "not integration and not
#     postgres and not slow"` would still match our test which
#     carries no such marker — the override is belt-and-braces
#     against future pyproject.ini_options tweaks)
#
# Serial (NO xdist, NO randomly): the test mutates a fresh
# tmp_path SQLite; parallel workers would each spawn their own
# DB so parallel is safe BUT the per-test cost is sub-second;
# keeping serial makes the diff between runs easier to read.
#
# Exit codes (per the test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: watchloss_chain_mock_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '<detached>') @ $(git rev-parse --short HEAD)"
echo "(usage-limit retry-supersede double-silence chain: deferral → F6"
echo " re-key → settle CAS → mission-terminal delivery)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 280s hard cap on the pytest process.
# set +e around the timed command so the RESULT: FAIL branch below
# is reachable under `set -e`.
set +e
timeout 280s env \
    -u POSTGRES_HOST \
    -u POSTGRES_PORT \
    -u POSTGRES_USER \
    -u POSTGRES_PASSWORD \
    -u POSTGRES_DB \
    -u POSTGRES_URL \
    -u DATABASE_URL \
    -u ENSEMBLE_DB_DSN \
    -u SSL_CERT_FILE \
    -u SSL_CERT_DIR \
    .venv/bin/python -m pytest \
    tests/vfy/test_watchloss_chain_regression.py \
    --override-ini="addopts=" \
    --override-ini="timeout=280" \
    -p no:randomly \
    -p no:xdist \
    --tb=short \
    -q 2>&1
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
elif [ $EXIT_CODE -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL"
    exit 1
fi