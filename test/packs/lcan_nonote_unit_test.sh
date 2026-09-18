#!/usr/bin/env bash
# Test Pack: lcan_nonote_unit_test
# Timeout: 2 minutes (120s) — unit pack budget; hard cap 5 min
#
# Job 4 NO-NOTE verification for the LCA advisory-note-removal merge gate
# (branch feature/lca-remove-advisory-note @ 0a4fccb1, base 858b1038).
#
# Runs the delta-touched contract files together with the new census file:
#   * tests/unit/test_attestation_stage3_census.py
#       Stage-3 retirement negative-pin census (companion — Stage-3 path)
#   * tests/unit/test_child_terminal_contradiction.py
#       Catalog membership pin (the deleted Stage-0 producer's tests
#       are removed; the remaining tests pin the 17-pattern catalog
#       contract)
#   * tests/unit/test_lcan_nonote_census.py
#       NEW — runtime NO-MINT census + byte-pin + legacy read-path
#
# The three files are disjoint surfaces that share only the marker
# catalog constant; running them together gives a single-pass gate
# that fails LOUDLY if any of the three regresses (catalog drift,
# legacy detector removal, or a note-mint resurrection).
#
# Per project convention (l): tests EXCLUSIVELY via
# `uv run python -m pytest` from worktree root (bare `pytest` PATH-
# resolves to a broken Homebrew install on this host).
#
# Dual-layer timeout:
#   Layer 1 (command-level): timeout 110s wrapping the pytest call
#   Layer 2 (script-internal): --timeout=100s per-test via pytest-timeout
#     so individual hangs die before the outer timer.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: lcan_nonote_unit_test ==="
echo "Project: $PROJECT_DIR"
echo "Branch: $(cd "$PROJECT_DIR" && git rev-parse --abbrev-ref HEAD)"
echo "HEAD:   $(cd "$PROJECT_DIR" && git rev-parse --short HEAD)"

cd "$PROJECT_DIR"

# Command-level (Layer 1) timeout: 110s — interrupts hung pytest process.
# Script-internal (Layer 2) timeout: per-test --timeout=100s so any
# individual hang dies before the outer timer.
timeout 110s uv run python -m pytest \
  tests/unit/test_attestation_stage3_census.py \
  tests/unit/test_child_terminal_contradiction.py \
  tests/unit/test_lcan_nonote_census.py \
  -v --override-ini="addopts=" --tb=short -q \
  --timeout=100 \
  2>&1

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