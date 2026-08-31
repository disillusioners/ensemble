#!/usr/bin/env bash
# Test Pack: ri_incident_repro_integration_test — P2 E2E incident-repro capstone (gate scope 8).
#
# Replays the 43070f6f-class silent-death shape on the dev-authored repro at
# tests/integration/test_report_integrity_repro.py:
#   * junk-report child (zero-tool-call no-work opener) + parent declaring wait
#   * OFF state (ship default) — guard SAW it (declared-waiting violation WARNING),
#     parent still completes, NO adjudication notice
#   * ON state (env per-test) — adjudication notice enqueued with
#     source=system:report-integrity-guard
#
# Hermetic harness: real ChildReportsService internals + real (b) predicate +
# real WritePauseGuard + real DependencyBus against in-memory SQLite (StaticPool);
# InstanceManager facade is MagicMock (mirrors test_child_reports.py /
# test_boot_report_recovery.py per the repro's own docstring). LLM seam:
# daemon.services.child_reports.get_instance_messages (patched to AsyncMock).
# Notice-injection verification seam: _manager.enqueue_message (AsyncMock; the
# OFF vs ON assertion is built from caplog + mock-call-args, NOT a real
# MessageQueue row — flagged in the report, not a defect).
#
# Timeout: 5 minutes (300s) — dual-layer (timeout wrapper + pytest's own
# per-test timeout is honoured by the upstream pytest config).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ri_incident_repro_integration_test ==="

cd "$PROJECT_DIR"

timeout 300s .venv/bin/pytest \
  tests/integration/test_report_integrity_repro.py \
  --tb=short -q \
  --override-ini="addopts=" \
  2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
elif [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
else
  echo "RESULT: FAIL"
  exit 1
fi