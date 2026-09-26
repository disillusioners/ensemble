#!/usr/bin/env bash
# snapshot_behavior_spot — agent-snapshot-v1 R14/R12/R15/D8 spot-check pack
# Gate 4 spot-check BEYOND the dev's 457-test bounded pack; asserts the
# observable TOOL-surface contracts pinned by design-exploration.md §4.3 /
# §6.1 / §6.3 (R-rules) / §10 D8.
#
# Scope: tests/test_snapshot_behavior_spot.py (14 tests across 5 classes +
# 1 parametrized sweep = 16 invocations).
# Tests = R14 explicit-warm contract; R14 explicit-cold (verify-failed,
# expired); R14 internal-search (no-hit, warm-top); R12 superseded never
# spawns (explicit, internal-search); R15 settings toggle (default OFF,
# clean disabled, ON proceeds, no-OFF-breaks-spawn); D8 project scoping
# (foreign snapshot id → cold); parametrized sweep of the 6-key contract.
#
# Est: < 30s (in-memory SQLite, no DB, no network); 14 tests.
# Env: scrubbed POSTGRES_*/ENSEMBLE_*/DATABASE_*/PG_TEST_*/SSL_CERT_*
# (live-DB fence); NEVER binds 9797/7979/8088.
#
# Hard fences (per task brief): no production-code changes; test-side
# quick-fix only (< 20 lines); no pushes.
set -u
SNAP_WT="${SNAP_WT:-$(cd "$(dirname "$0")/../.." && pwd)}"
SNAPAB_OUT="${SNAPAB_OUT:-/tmp/snapab/$(basename "$SNAP_WT")}"; mkdir -p "$SNAPAB_OUT"
# scrub env (live-DB fence: prior probe inheriting ambient POSTGRES_* cleared live backlog)
while IFS='=' read -r name _; do case "$name" in POSTGRES*|ENSEMBLE*|DATABASE*|PG_TEST*|SSL_CERT_FILE|SSL_CERT_DIR) unset "$name";; esac; done < <(printenv)
cd "$SNAP_WT" || { echo "RESULT: FAIL (no worktree)"; exit 1; }
test -x .venv/bin/python || { echo "RESULT: FAIL (no venv)"; exit 1; }
IMPORT_CHECK="$(.venv/bin/python -c "import daemon; print(daemon.__file__)" 2>/dev/null)"
case "$IMPORT_CHECK" in "$SNAP_WT"*) ;; *) echo "RESULT: FAIL (daemon import resolves outside worktree: $IMPORT_CHECK)"; exit 1;; esac
echo "=== Test Pack: snapshot_behavior_spot @ $(basename "$SNAP_WT") ==="
timeout 240 .venv/bin/python -m pytest tests/test_snapshot_behavior_spot.py -q --tb=short --junitxml="$SNAPAB_OUT/snapshot_behavior_spot.xml"
rc=$?
case $rc in 0) echo "RESULT: PASS"; exit 0;; 124) echo "RESULT: TIMEOUT"; exit 124;; *) echo "RESULT: FAIL (see junit $SNAPAB_OUT/snapshot_behavior_spot.xml)"; exit 1;; esac
