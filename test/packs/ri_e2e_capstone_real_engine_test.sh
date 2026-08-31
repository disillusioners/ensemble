#!/usr/bin/env bash
# Test Pack: ri_e2e_capstone_real_engine_test — P2 E2E capstone (gate scope 8).
#
# Real-engine bar for the report-integrity (b) terminal-waiting guard.
# Replays the 43070f6f-class silent-death shape on the real
# ``InstanceManager`` (file-backed WAL SQLite), real ``WorkerPool(1)`` +
# ``JobProcessor`` + ``JobQueueService``, scripted LLM stub at one seam.
#
# Bar achieved: real-manager + live completion entry. The pack boots
# the real engine, creates real instances, drives the live completion
# entry (``service._process_child_completion_db_sync`` +
# ``_dispatch_post_commit_side_effects``) on real durable rows. The
# (b) enforcement action — when the kill-switch is ON — writes a real
# ``MessageQueue`` + ``Task`` row pair via ``manager.enqueue_message``.
#
# Two tests, ONE per flag state (the task spec says split into 2 packs
# if needed; one pack with two tests is fine since they share the
# harness and run sequentially):
#
#   * OFF (ship default, flag unset) — log-only byte-parity:
#     guard SAW it ([ReportIntegrityGuard] WARNING), parent COMPLETED,
#     ZERO MessageQueue rows with source system:report-integrity-guard,
#     _B_NOTICE_LEDGER empty.
#   * ON (flag = 1) — adjudication notice enqueued through real path:
#     parent COMPLETED, real MessageQueue row with source
#     system:report-integrity-guard + metadata report_integrity_notice
#     true + body cites child id + (c) marker + NOT in [SYSTEM NOTE]
#     frame.
#
# Reuses the wake_harness pattern from test/packs/wc_wake_e2e_capstone_test.py
# — no new harness was invented. Mirrors the bootstrap recipe from
# tests/integration/test_wc_wake_pure_hang.py.
#
# No ports below 10000: the harness never opens a socket —
# DaemonConfig declares port=8079 but the listener is NEVER started.
# WorkerPool(1) is started but the assertion queries MessageQueue
# immediately after the live completion path returns, so the worker
# cannot have consumed the notice before we observe the row.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 280s` cap on the pytest
#     process. Two tests, target ≤90s combined; 280s is margin-rich.
#
# Exit codes (per test-pack skill):
#   0   PASS (both OFF and ON assertions hold)
#   1   FAIL (any assertion failed)
#   124 TIMEOUT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ri_e2e_capstone_real_engine_test ==="
echo "(OFF log-only + ON real MessageQueue notice row — real-engine capstone)"

cd "$PROJECT_DIR"

# Layer 2: 280s hard cap on the pytest process. Two tests, target
# ≤90s combined at HEAD; 280s is margin-rich.
EXIT_CODE=0
timeout 280s .venv/bin/pytest \
  test/packs/ri_e2e_capstone_real_engine_test.py \
  --tb=short -q -ra -p no:cacheprovider 2>&1 || EXIT_CODE=$?

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
