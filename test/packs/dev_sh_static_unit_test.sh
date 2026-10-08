#!/usr/bin/env bash
# Test Pack: dev_sh_static_unit_test — dev.sh freestatic invariants.
#
# Scope: pure bash (no pytest). Verifies that dev.sh carries the
# `--timeout-graceful-shutdown 10` flag at the uvicorn launch invocation
# (ensure.md Core #4 invariant — graceful-shutdown bounded to 10s so a
# long-tail boot/sleep/long-LLM-call cannot consume the entire
# route's wait_for budget).
#
# This gate isolates the "dev.sh carries the graceful-shutdown bound"
# invariant from the LCA-FALSE-COMPLETION fix cycle merge gate (which
# checked the same line at d5c50994 / lcafc_boot_smoke_mock_test.sh
# PHASE 6) — here we re-prove it on the pairing-heal branch to chart
# the invariant survives the patch.
#
# Branch pin: fix/tool-pairing-full-history-heal @ 86c1bc041.
# Base: latest @ 9be991d56.
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
# No pytest invocation. Pure static grep.
#
# Single-layer timeout (no pytest, no LLM, no daemon):
#   - Layer 1 (command-level): caller wraps with `timeout 60`
#   - Layer 2 is implicit (sub-second exit).
#
# Exit codes (per test-pack skill):
#   0   PASS  (line found)
#   1   FAIL  (line missing)
set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

# Static check: the uvicorn invocation in dev.sh MUST carry the bounded
# --timeout-graceful-shutdown 10 flag. Drift off this line is a Core #4
# violation per ensure.md (graceful shutdown unbounded = 504 on long LLM
# tail).
if ! grep -Fq -- '--timeout-graceful-shutdown 10' dev.sh; then
  echo "RESULT: FAIL: --timeout-graceful-shutdown 10 missing from dev.sh" >&2
  exit 1
fi
echo "=== Test Pack: dev_sh_static_unit_test (dev.sh: --timeout-graceful-shutdown 10) ==="
echo "RESULT: PASS"
exit 0
