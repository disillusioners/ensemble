#!/usr/bin/env bash
# Test Pack: watcher_rearm_integration_test — pause/resume watcher
# durability for the resume-cascade re-arm fix (Debug Phase 4 fix #2,
# commit e9aac370, branch feature/fix-report-delivery-ensure-deferred).
#
# Fix part 2 under test: resume_instance_cascade re-arms CANCELLED,
# never-delivered child-completion watchers (daemon/services/
# instance_lifecycle.py :: _rearm_cancelled_watchers_for_resumed):
#   * candidate = state='CANCELLED' AND enqueued_at IS NULL;
#   * child non-terminal guard reads durable state (task payload
#     child_id first, legacy source_task_id fallback);
#   * rowcount-guarded CAS CANCELLED→PENDING (concurrent transitions
#     between candidate SELECT and CAS are not overwritten);
#   * bus cache parity via DependencyBus.rearm_watch_cache;
#   * kill-switch ENSEMBLE_WATCHER_REARM_ON_RESUME (default ON,
#     restart-read; OFF = byte-identical legacy resume, test-pinned).
#
# Kill-switch pin map (grep-verified 2026-09-07):
#   OFF path (=0, legacy):  tests/integration/test_pause_resume_watcher_rearm.py::
#                           test_kill_switch_off_keeps_watcher_cancelled
#                           (monkeypatch.setenv "0" at line 348)
#   ON path (default):      test_pause_cascade_resume_rearms_watcher_and_wake_fires
#                           (delenv at line 86 → default ON) +
#                           test_terminal_child_watcher_stays_cancelled +
#                           test_legacy_row_without_payload_child_id_rearms_via_task
#
# NOTE ON PATHS: the dispatch paraphrased this file as
# tests/integration/test_watcher_rearm_resume.py — that path does NOT
# exist. The ACTUAL file in commit e9aac370 is:
#   tests/integration/test_pause_resume_watcher_rearm.py
# (verified via `git show e9aac370 --name-only`).
#
# Files covered:
#   1. tests/integration/test_pause_resume_watcher_rearm.py — 4 tests
#      (collect-verified 2026-09-07: pause→terminal→resume→re-arm→wake
#      with real bus + exactly-once double-emit; kill-switch OFF pins
#      legacy behavior; terminal-child guard; legacy fallback).
#
# Branch-under-test: feature/fix-report-delivery-ensure-deferred
# (fix commit e9aac370, parent bb052fce).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 280s` on the pytest process
#     (integration tests with real bus + event loops are slower than
#     unit packs; 280s matches the canonical PG-smoke internal cap).
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: watcher_rearm_integration_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(resume-cascade re-arm: CANCELLED→PENDING CAS + kill-switch pins)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 280s hard cap on the pytest process.
# set +e around the timed command so the RESULT: FAIL branch below is
# reachable under `set -e`.
set +e
timeout 280s .venv/bin/pytest \
  tests/integration/test_pause_resume_watcher_rearm.py \
  --tb=short -q 2>&1
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
