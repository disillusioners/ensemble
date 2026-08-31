#!/usr/bin/env bash
# ri_repair_instruments_unit_test.sh
#
# Scope 3 — ALWAYS-ON Wave-1 report-integrity instruments safety gate.
#
# Wraps ONLY tests/unit/test_report_repair.py (61 tests):
#   * TestIsLikelyTruncatedReport — _is_likely_truncated_report heuristic
#   * TestCombineMessages — _combine_messages fallback combiner
#   * TestRepairReportWithLlm — _repair_report_with_llm (30s timeout)
#   * TestGetLastAssistantMessageRaw — end-to-end raw fetch + (c)-marker
#     envelope test (single-message junk shape) + skip_repair + excluded
#     agents (wanderer/explorer + watcher via NR-2 lift) + custom exclusion
#     + skip_repair through wrapped path
#   * TestN2IndexingRegression — N-2 prompt regression
#   * TestCombineMessagesTruncation — 10K cap
#   * TestEndToEndPersistence — parent_report_string delivery
#   * TestConfigDefaults — NR-2 default derivation + S2 validators
#
# P2 surgical modifications to this surface (commit f8c5ce8f):
#   * Wave-1 (c) marker envelope test — test_single_message_returns_it
#     (lines 585-608): single-message junk-shape terminal report carries
#     REPORT_SANITY_MARKER as additive suffix; original content preserved
#     as prefix. Mirror of test_child_reports.py::TestReportSanityMarker.
#   * NR-2 default derivation — test_repair_excluded_agents_default
#     (lines 1267-1278): asserts ReportRepairConfig.repair_excluded_agents
#     derives from daemon.constants.REPORT_REPAIR_EXCLUDED_AGENTS
#     (= {"wanderer", "explorer", "watcher"} post-watcher-lift).
#
# Scope-3 (read-and-verify, NOT executed here) lives in
# tests/unit/services/test_child_reports.py — already fully covered by
# the existing child_reports_unit_test pack (47P/0F).
#
# Constraints honored:
#   * Single pack — this is the ONLY pack we run.
#   * Dual-layer timeout — script-internal 120s (Layer 2), caller wraps
#     with `timeout 300` (Layer 1, ≤ 5 min hard cap).
#   * Test code only — no production touchpoints.
#   * daemon/ is read-only — never executes the daemon.
#
# Expected: 61 passed / 0 failed in <30s (pure mock manager, no DB).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: ri_repair_instruments_unit_test ==="
echo "Surface: tests/unit/test_report_repair.py (61 tests, scope-3 gate surface)"
cd "$PROJECT_DIR"

# Script-internal timeout guard (Layer 2): 120s — interrupts hung tests.
# Command-level timeout (Layer 1): caller wraps with `timeout 300`.
timeout 120s .venv/bin/pytest \
  tests/unit/test_report_repair.py \
  -v --override-ini="addopts=" --tb=short -q 2>&1
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
