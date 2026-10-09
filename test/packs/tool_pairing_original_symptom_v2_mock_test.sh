#!/usr/bin/env bash
# Test Pack: tool_pairing_original_symptom_v2_mock_test — round-2 gate,
# original-symptom closure for the invalid_tool_calls shape (incident
# 03d7657f round 2, task 10816).
#
# Background. Round-2 closure criterion on branch
# fix/tool-pairing-invalid-tool-calls. The live incident shape: the
# poisoned AIMessage carries ``tool_calls=[], invalid_tool_calls=[X]`` —
# the id was emitted on the wire but marked INVALID, so round-1 code
# (which read only ``tool_calls``) could not see it, stripped the DB-
# repair TM answering X, and re-shipped the unanswered X → 2013 loop.
# This pack drives the REAL daemon.graph.create_agent_node against an
# in-process UNION strict-gateway fake LLM (v1 harness subclassed; the
# needed-set is the production union of tool_calls + invalid_tool_calls
# ids via daemon.tool_pairing_history._extract_tool_call_ids) across
# the four round-2 arcs:
#
#   * (a) — round-1 failure mechanism without production edits: the
#     union gateway rejects the raw never-answered invalid poison AND
#     the round-1-strip simulation (answering TM removed → unanswered X
#     resurfaces); v1-view negative control ACCEPTS the same payload
#     (the blind spot); UNREACHABLE: through the real W1 path no
#     captured payload carries an unanswered invalid id.
#   * (b) — W1 pre-heal synthesizes FOR the invalid call: synth TM id
#     partner-synth-{X} with PARTNER_SYNTH_INVALID_TEXT (invalid
#     flavor, NOT PARTNER_SYNTH_TEXT); EXACTLY 1 invoke; node OK.
#   * (c) — W2 heal-once + single retry with identity survival: fail-
#     once-then-succeed → EXACTLY 2 invokes; the synth TM survives into
#     the retry payload by identity (same id string, same object,
#     adjacent block); the DB-repair-style uuid-id TM is NOT stripped;
#     retry accepted; node OK.
#   * (d) — verbatim live tuple (call_8ed9e1771dca42348dfa7ca0 answered
#     by uuid TM 1c2a9d4f-3b71-4f0e-9a23-deadbeef0001) mid-list in
#     ~600 msgs: probe CLEAN; TM survives BY IDENTITY (is check); zero
#     removal; gateway accepts; EXACTLY 1 invoke.
#
# The round-1 v1 file (tests/integration/
# test_tool_pairing_original_symptom.py) is imported, NEVER modified,
# and must stay green as the round-1 regression (its own pack).
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no real
# LLM calls, no network, no port binding (pure in-process; port 8088
# never touched).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#     (script-internal `timeout 240s` on the pytest process is the
#     in-script outer guard).
#   - Layer 2 (per-test): pytest-timeout `--timeout=210`.
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: tool_pairing_original_symptom_v2_mock_test ==="
echo "(round-2 closure: (a) round-1 mechanism + UNREACHABLE / (b) W1 invalid-flavor synth / (c) W2 identity survival / (d) verbatim live tuple)"

cd "$PROJECT_DIR"

RC=0
# Layer 1 (script-internal): 240s hard cap on the pytest process.
# Layer 2 (per-test): --timeout=210 via pytest-timeout.
# -p no:cacheprovider avoids writing .pytest_cache into the worktree;
# --override-ini="addopts=" clears the repo default marker filter so
# the integration-dir file is not deselected.
timeout 240s .venv/bin/pytest \
  tests/integration/test_tool_pairing_original_symptom_v2.py \
  -p no:cacheprovider --override-ini="addopts=" --tb=short -q --timeout=210 \
  2>&1 || RC=$?

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
