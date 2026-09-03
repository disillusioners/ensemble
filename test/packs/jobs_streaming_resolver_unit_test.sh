#!/usr/bin/env bash
# Test Pack: jobs_streaming_resolver_unit_test — M2 Mission Class acceptance
# Tests: tests/unit/routers/test_jobs_streaming_resolver.py (10 tests)
# Timeout: 2 minutes (120s)
#
# M2 Gate acceptance pack — jobs streaming resolver (routers) surface.
# Mission-class feature pack on `feature/mission-class` @ 8eddeb3d.
# Worktree-bound; relies on rev-parse bracket echo for drift guard.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: jobs_streaming_resolver_unit_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 110s — interrupts hung tests
# Layer 1 (command-level): 120s via `timeout` wrapper below
timeout 110s .venv/bin/pytest \
  tests/unit/routers/test_jobs_streaming_resolver.py \
  --tb=short -q -rf 2>&1
EXIT_CODE=$?
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
