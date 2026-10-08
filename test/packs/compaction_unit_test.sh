#!/usr/bin/env bash
# Test Pack: compaction_unit_test — Compaction-only (single-file) unit suite.
#
# Scope: tests/unit/test_compaction.py (130 tests). The pairing-heal branch
# pins compaction-related unit assertions on a single-file narrow surface:
# the canonical test_compaction.py module that drives the on-disk
# compaction engine behavior (truncate-floor, model config, layered
# summary, model_config surface, etc.).
#
# This is the PARTNER narrow pack for the broader 22-file `compaction`
# suite from the NEVER-BLOCKED commission (a separate gate on
# fix/compaction-never-blocked). Here we only prove the core
# test_compaction.py module is wired correctly on this branch.
#
# Branch pin: fix/tool-pairing-full-history-heal @ 86c1bc041.
# Base: latest @ 9be991d56.
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
# No external services.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 240`
#   - Layer 2 (script-internal): `timeout 210s` on the pytest process
#     (130 tests; <90s observed in calibration, 210s is a margin-rich
#     safety net).
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail
IFS=$'\n\t'
export PATH="/home/nea/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: compaction_unit_test (test_compaction.py, 130 tests) ==="

# Layer 2 (script-internal): 210s hard cap on the pytest process.
timeout 210s .venv/bin/pytest \
  tests/unit/test_compaction.py \
  --tb=short -q \
  --override-ini="addopts=" \
  --timeout=210 \
  -p no:cacheprovider 2>&1
RC=$?

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  echo "Exit: $RC"
  exit 1
fi
