#!/usr/bin/env bash
# Test Pack: regression_performance — tests/performance/ (perf matrix)
# Scope: tests/performance/ (test_context_api_latency.py + test_message_api_cost.py)
# Timeout: 5 minutes (300s) — SERIAL (no xdist) by design
#
# Split out of regression_top_level_a_h on 2026-10-20: the scope tail
# tests/performance/test_message_api_cost.py has ~30s/cell producer tests AND
# the [1x10k] parametrize cell, an UNINTERRUPTIBLE hang (QUARANTINE.md
# 2026-10-19 — 0 completions in 4 observed wedges; only an outer wall-kill
# stops it). Carrying that inside the P-8 280s cap was structurally impossible.
#
# SERIAL — DO NOT ADD -n auto. The suite is SINGLE-PROCESS-ONLY: the matrix
# producers (TestPerfMatrix::test_cell_runs_and_records_metrics) publish into
# MODULE-LEVEL globals (_CELL_RESULTS / _AGET_COMPONENT_RESULTS /
# _AGET_COMPONENT_MEAN_RESULTS) that the consumers in TestPerfMatrixAcceptance
# read. Under xdist a producer and its consumer land on DIFFERENT workers
# (separate processes, separate globals) and every consumer fails with the
# "producer-die consumer-fail" signature (missing cells / KeyError) — the
# exact shape the 2026-09-07 P-8 row mis-recorded as "load-sensitivity"
# (its "3/3 solo WHOLE-MODULE PASS" note is the serial-correctness tell).
#
# Quarantine-aware deselects (-k):
#   - "not 1x10k" — QUARANTINE.md 2026-10-19: the [1x10k] cell is an
#     UNINTERRUPTIBLE C-block hang; run via -k "not 1x10k" deselect until fixed.
#   - "not 100x10k-variance" — NOT quarantined, PASSES solo (observed 266.6 s in
#     this gate, LEG B) — single-test runtime structurally exceeds any pack
#     budget alongside pack-mates; dispatcher ruling 2026-10-20: run this cell
#     SOLO; do not un-exclude without splitting the suite (per-cell packs or a
#     slow-tier). Split-debt ledgered in PACKS.md by the tester lane.
#   - "not test_variance_across_history_depths" — SAME hard-asserts-cell-set
#     mechanism: reads the excluded 100x10k-variance producer's cell
#     (_CELL_RESULTS[(100, 10000)] at :986; VARIANCE_ANCHOR_DEPTHS includes
#     10000) — KeyErrors deterministically while the cell is pack-excluded.
#     Removal condition = together with "not 100x10k-variance" when the suite
#     is split or the cell re-enters packs.
#   - "not aget_component_decomposition_reported" — SAME hard-asserts-cell-set
#     mechanism: second assertion reads AGET_COMPONENT_MEAN_CELLS at :666-670,
#     which includes (100, 10000) from the excluded 100x10k-variance producer
#     — "missing component-mean cells" deterministically while excluded.
#     Removal condition = together with "not 100x10k-variance" when the suite
#     is split or the cell re-enters packs.
#   - "not all_six_cells_populated" / "not nfr_summary_print" — SAME quarantine
#     family: both hard-assert the presence of ALL SIX MATRIX cells
#     (test_message_api_cost.py:940 / :1213), so they cannot pass while the
#     mandated [1x10k] exclusion is in force (5 of 6 cells -> the autouse
#     _require_matrix skip does not fire and both red deterministically).
#     Remove these two terms together with "not 1x10k" when the cell is
#     un-quarantined.
#
# Pre-existing failure families MUST RUN and be adjudicated from failure
# inventory, NOT deselected: perf-matrix 2x-baseline anchor (load-sensitive),
# aget component decomposition, context-api latency budget.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_performance [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Regression partition pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
EXIT_CODE=0
# `.venv/bin/pytest` (house small-pack style, cf. api_unit_test.sh) — `uv run`
# needs uv on PATH, which a bare harness shell does not have (verified 2026-10-20:
# `timeout: failed to run command 'uv'` -> instant FAIL). Same venv, same plugins.
timeout 280s .venv/bin/pytest \
  tests/performance/ \
  -k "not 1x10k and not 100x10k-variance and not test_variance_across_history_depths and not aget_component_decomposition_reported and not all_six_cells_populated and not nfr_summary_print" \
  --tb=short -q -rf \
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
