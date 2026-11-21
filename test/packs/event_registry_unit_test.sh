#!/usr/bin/env bash
# Test Pack: event_registry_unit_test — Engine Arc P5 event registry v1 (T5.9)
#
# Purpose: Unit pack for Phase 5 (Event Registry v1) — kind catalog (T5.1),
# structured emission ring (T5.2), class-1/3/4 bridges (T5.3/T5.5/T5.6),
# observability projection + consumer liveness (T5.7), ingest sink (T5.8),
# plus the fences: F-1 zero-metrics-families (HARD leg), F-3/F-11 dim-name
# fence, F-17 catalog-first identity, F-18 deferred_v2 refusal, F-19
# no-second-fire static guards, AC1-AC7 acceptance legs, and the AC2
# bidirectional bridge census (house style:
# tests/job_queue/test_terminal_write_census.py).
#
# PACKS.md REGISTRATION IS P7's (phase5-plan.md T5.9) — this file ships
# unregistered by design.
#
# Scope: tests/unit/services/event_registry/ ONLY. No DB required (the
# registry is import-standalone; pytest still needs the dev env fence to
# pass — run with the worktree .env pinned, ENSEMBLE_SELF_ENV=dev).
#
# Expected test count: ~34 (4 files)
# Timeout: 2 minutes (120s, unit class)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: event_registry_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/services/event_registry/ \
  --override-ini="addopts=" --tb=short -q 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL (exit $EXIT_CODE)"
  exit $EXIT_CODE
fi
