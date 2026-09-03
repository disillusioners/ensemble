#!/usr/bin/env bash
# Test Pack: m2_missions_off_zeroquery_integration_test — M2 Mission Class probe
# Tests: tests/integration/test_m2_missions_off_zeroquery.py (M2 probe, real create_app)
# Timeout: 5 minutes (300s)
#
# M2 Gate probe-pack wrapper. The probe file exercises the REAL app
# assembly (daemon.api.create_app(), lifespan bypassed per the
# vscode-integration precedent) with the mission resolver wired exactly
# as production wiring does — proving the route-level OFF semantics:
# 404 on both routes with ZERO SQL statements in the request window
# (before_cursor_execute engine-spy), OpenAPI path visibility in both
# flag states, the §8.4 ON smoke, and flip-back determinism.
#
# Wrapper notes (M2 implementer, 2026-09-03):
# * --override-ini="addopts=" — REQUIRED: the repo's default addopts is
#   `-m 'not integration and not postgres'` (pyproject.toml), which
#   deselects every integration test. This mirrors the house
#   integration-pack pattern (api_messages_integration_test.sh).
# * --timeout=240 — per-test cap ≤240s per the gate task spec (CLI
#   overrides the ini default of 30s, leaving headroom for the heavy
#   create_app() import path).
# * -rP — prints captured stdout (the PROBE-CENSUS / PROBE-OPENAPI
#   evidence lines) for PASSED tests; the gate needs pasted evidence,
#   not just a green dot.
#
# Mission-class feature pack on `feature/mission-class`.
# Worktree-bound; relies on rev-parse bracket echo for drift guard.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: m2_missions_off_zeroquery_integration_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Integration pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
timeout 280s .venv/bin/pytest \
  tests/integration/test_m2_missions_off_zeroquery.py \
  --override-ini="addopts=" \
  --timeout=240 \
  --tb=short -q -rf -rP 2>&1
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
