#!/usr/bin/env bash
# Test Pack: regression_integration_test (non-chat-source slice)
# Scope: tests/integration/ EXCLUDING tests/integration/test_chat_source_*.py
# (962 tests at 2026-09-19, **post-filter** count). The chat-source family
# is hoisted into regression_chat_source_integration_test.sh — see that
# pack for the addopts-defect fix.
#
# === SAME addopts-defect class as the sibling chat-source pack ===
# pyproject.toml:80 ``addopts = "-m 'not integration and not postgres'"``
# silently deselects every ``pytest.mark.integration``-marked test in
# this slice, the same defect class fix-landed in
# ``regression_chat_source_integration_test.sh`` earlier in this branch.
# Empirically confirmed 2026-09-19:
#   * DEFAULT addopts            : 708 tests collected, 254 deselected
#                                  (the BUG state — runs 70% of the slice)
#   * --override-ini="addopts="  : 962 tests collected   (the FIX state)
# This pack uses the SAME flag shape that the chat-source sibling uses
# to override the silent-deselect, WITHOUT re-applying a `-m integration`
# filter (which would re-deselect the SAME 254 tests the override restores
# — see the "Why no -m integration here" block below the result-echo).
#
# Timeout: was 5 min (300s) — 283s solo with the old (BUGGY) 708-test
# selection under default addopts. With 254 more integration-marked
# tests now actually executing (~0.1-0.3s/test per the dispatcher's
# family estimate on this slice), the upper bound adds ~+76s → ~359s;
# mid estimate 0.2s/test adds ~+51s → ~334s. Budget raised MINIMALLY
# to cover the upper estimate + a 10s margin:
#   * Inner guard 350s (was 290s, +60s)
#   * Outer guard 360s (was 300s, +60s)
#
# Split from regression_integration_opencode_e2e_test.sh (P-12 in
# PACKS.md) — that combined pack breached 300s solo at the merge
# gate. The split shape is:
#
#   * regression_chat_source_integration_test.sh — chat-source-only,
#     --override-ini="addopts=" -m integration (fixes the addopts
#     defect; 50 tests, ~22s).
#   * regression_integration_test.sh (this pack) — non-chat-source
#     integration slice (962 tests now actually executed, ≤350s).
#   * regression_opencode_e2e_test.sh — tests/opencode/ + tests/e2e/
#     (~520 tests, ~17s).
#
# All three of the new packs fit under their respective dual-layer guards.
#
# Invocation contract:
#   timeout 360 bash test/packs/regression_integration_test.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_integration_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Integration-slice regression pack. Dual-layer timeout.
# Layer 2 (script-internal): 350s — interrupts hung tests
# Layer 1 (command-level): 360s via `timeout` wrapper below
# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
#
# Why `--override-ini="addopts="` alone (NO `-m integration` here):
# the chat-source sibling needs `-m integration` because every chat-source
# test carries the marker (50/50). This slice has mixed markers: 708
# non-integration tests + 254 integration-marked tests. Adding `-m
# integration` here would re-deselect the SAME 254 tests the override
# restores (collect-only proof: `-m integration` alone → 254/962; just
# `--override-ini="addopts="` → 962 collected — the desired outcome).
EXIT_CODE=0
timeout 350s .venv/bin/python -m pytest \
  tests/integration/ \
  --override-ini="addopts=" \
  -n auto --tb=short -q -rf \
  --override-ini="timeout=240" \
  --ignore-glob='**/test_chat_source_*.py' \
  2>&1 || EXIT_CODE=$?
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
