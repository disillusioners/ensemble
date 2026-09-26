#!/usr/bin/env bash
# A/B slice: top_message_queue_redesign — agent-snapshot-v1 acceptance gate
# Scope: tests/message_queue_redesign
# Est: ~~2 min (tests=470; xdist -n auto on 8 cores, repo pack calibration 3.9-9.5 tests/s)
# Env: scrubbed POSTGRES_*/ENSEMBLE_*/DATABASE_* (live-DB fence); NEVER binds 9797/7979/8088.
# NOTE: -n auto added per repo convention (test/packs/regression_unit_services_test.sh);
#       per-test timeout=30 + addopts marker filter from pyproject apply unchanged.
set -u
SNAP_WT="${SNAP_WT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
SNAPAB_OUT="${SNAPAB_OUT:-/tmp/snapab/$(basename "$SNAP_WT")}"; mkdir -p "$SNAPAB_OUT"
# scrub env (live-DB fence: prior probe inheriting ambient POSTGRES_* cleared live backlog)
while IFS='=' read -r name _; do case "$name" in POSTGRES*|ENSEMBLE*|DATABASE*) unset "$name";; esac; done < <(printenv)
cd "$SNAP_WT" || { echo "RESULT: FAIL (no worktree)"; exit 1; }
test -x .venv/bin/python || { echo "RESULT: FAIL (no venv)"; exit 1; }
IMPORT_CHECK="$(.venv/bin/python -c "import daemon; print(daemon.__file__)" 2>/dev/null)"
case "$IMPORT_CHECK" in "$SNAP_WT"*) ;; *) echo "RESULT: FAIL (daemon import resolves outside worktree: $IMPORT_CHECK)"; exit 1;; esac
echo "=== Test Pack: ab_top_message_queue_redesign @ $(basename "$SNAP_WT") ==="
timeout 240 .venv/bin/python -m pytest tests/message_queue_redesign -q --tb=short --junitxml="$SNAPAB_OUT/top_message_queue_redesign.xml" -n auto
rc=$?
case $rc in 0) echo "RESULT: PASS"; exit 0;; 124) echo "RESULT: TIMEOUT"; exit 124;; *) echo "RESULT: FAIL (see junit $SNAPAB_OUT/top_message_queue_redesign.xml)"; exit 1;; esac
