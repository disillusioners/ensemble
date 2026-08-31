#!/usr/bin/env bash
# ri_prompts_registry_unit_test.sh
#
# Gate scope 6 — P2 report-integrity prompt-edit gate (branch
# feature/wc-wake-report-integrity @ f8c5ce8f + test-only gate commits
# ddfc5fc6/f96a239f/22a6df4b/2134de9e; wave-1 prompt edits d4642381).
#
# Wraps ONE test file in a SINGLE pytest invocation:
#
#   * tests/unit/test_report_integrity_prompts.py (66 nodes: 7 test
#     functions, parametrized) — prompt-def pin suite:
#       (a) text-presence: every C2-D2.10 parent agent (12) carries the
#           [REPORT SANITY: scrutiny guidance ("interim, not completion"
#           + send_message verify) in its canonical home files;
#       (b) every C2-D2.11 work-turn agent (11) carries the opening
#           work-discipline cardinal ("before ending any turn" +
#           "zero tool calls") — canonical-home + registry scans;
#       (c) explorer exempt (text-only by design) — carries NEITHER
#           guidance (negative assertion);
#       (d) GRANDFATHERED_PARENTS {blueprinter, devops, giter, jober}
#           skipped-with-reason in the registry walk;
#       (e) guide-conformance: registry-completeness dynamically
#           enumerates agents/*/meta.json (v2 shadowing resolved), any
#           agent with non-empty team_members must carry (d) — rot
#           mitigation — and the dispatch mirror is checked across
#           skills-template/*.md (docs/agent-prompt-writing-guide.md).
#
# Expected: 49P / 0F / 17S (17 skips = 13 agents with empty
# team_members + 4 grandfathered parents). Exit 0=PASS, 1=FAIL,
# 124=TIMEOUT.
#
# Constraints honored (test-pack skill, MANDATORY):
#   * Single pack — this is the ONLY pack we run.
#   * Dual-layer timeout — script-internal 120s (Layer 2), caller wraps
#     with `timeout 300` (Layer 1, ≤ 5 min hard cap). Estimated runtime
#     ~10-20s; 120s leaves 6× headroom.
#   * agents/ and daemon/ read-only — the suite greps prompt text only.
#   * No -x (continue on first failure — we want the full count).
#   * Single-process collection (no -n auto).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: ri_prompts_registry_unit_test ==="
echo "Surface: 1 test file, 66 nodes (prompt-def pin suite:"
echo "         (a) parent scrutiny x12, (b) opening discipline x11,"
echo "         (c) explorer exempt, (d) grandfathered skips,"
echo "         (e) registry-completeness + dispatch mirrors)"
echo "File:"
echo "  - tests/unit/test_report_integrity_prompts.py (66)"
cd "$PROJECT_DIR"

# Script-internal timeout guard (Layer 2): 120s — interrupts hung tests.
# Command-level timeout (Layer 1): caller wraps with `timeout 300`.
# `|| EXIT_CODE=$?` keeps set -e from short-circuiting the RESULT line.
EXIT_CODE=0
timeout 120s .venv/bin/pytest \
  tests/unit/test_report_integrity_prompts.py \
  --override-ini="addopts=" --tb=short -q 2>&1 || EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$EXIT_CODE" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
