#!/usr/bin/env bash
# Test Pack: tool_pairing_original_symptom_mock_test — G3 gate, original-
# symptom closure for the 2013 brick class (incident 03d7657f).
#
# Background. fix/tool-pairing-full-history-heal commission gate G3. The
# incident's forensic shape: a conversation history poisoned with an
# UNANSWERED tool_call MID-LIST (~index 148 of ~600) reaching the LLM
# gateway, whose strict adjacency validation rejects with the canonical
# "tool call result does not follow tool call (2013)" BadRequestError.
# This pack drives the REAL daemon.graph.create_agent_node against an
# in-process strict-gateway fake LLM (real class, genuine call→result
# immediate-adjacency walk — NOT count-pairing) across the four G3
# scenarios:
#
#   * S1 — symptom reproduction with the W1 probe disabled
#     (daemon.graph.has_pairing_violations → lambda m: False): the
#     poisoned payload reaches the gateway, the 2013 rejection fires
#     terminally (pre-fix brick proven).
#   * S2 — W1 pre-heal delivery: healing enabled → the gateway receives
#     a clean payload on EXACTLY 1 invoke; synth partner adjacent.
#   * S3 — W2 heal-once + single retry ("late poison"): gateway
#     fail-once-then-succeed → EXACTLY 2 invokes, node returns OK.
#   * S4 — W2 bounded reraise: gateway always rejects → EXACTLY 2
#     invokes, terminal ToolPairingInvalidError, full __cause__ chain,
#     pairing-signature classification (NOT transient).
#
# Classification seam is production-faithful: the raw fake provider's
# 2013 BadRequestError is converted to ToolPairingInvalidError by the
# REAL classify_llm_errors wrapper (same unconditional wrap
# build_instance_llms applies), routing into the agent_node W2 catch.
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

echo "=== Test Pack: tool_pairing_original_symptom_mock_test ==="
echo "(G3 original-symptom closure: S1 repro / S2 W1 pre-heal / S3 W2 heal-once / S4 bounded reraise)"

cd "$PROJECT_DIR"

RC=0
# Layer 1 (script-internal): 240s hard cap on the pytest process.
# Layer 2 (per-test): --timeout=210 via pytest-timeout.
# -p no:cacheprovider avoids writing .pytest_cache into the worktree;
# --override-ini="addopts=" clears the repo default marker filter so
# the integration-dir file is not deselected.
timeout 240s .venv/bin/pytest \
  tests/integration/test_tool_pairing_original_symptom.py \
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
