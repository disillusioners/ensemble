#!/usr/bin/env bash
# Test Pack: lca2_budget_parity_integration_test — LCA Stage 2 budget parity matrix
#
# Purpose: LIVE judge-call counting parity matrix for the LCA resolver
# Stage 2 flip (``daemon/graph.py`` fused block, gated by
# ``_LCA_STAGE2_RESOLVER_FLIP=True``). Drives the REAL gate node
# (``create_attestation_gate_node``) through 9 scenarios × 2 flip
# states and pins the exact (fused_entries, fused_attempts, legacy_entries,
# legacy_attempts) per cell. Asserts the literal parity contract:
# ONLY scenario (3) A-band-alone adds a judge call vs old path.
#
# Covering file (exactly 1):
#   1. tests/integration/test_attestation_stage2_budget_parity.py
#
# Scenarios (parametrized):
#   1. deny-band quiet un-attested
#   2. marker-band (markers + tree not quiet)
#   3. A-band ALONE (child-suspicion, tree not quiet, no markers) — THE parity delta
#   4. attested meta-bypass
#   5. user-answer-pending
#   6. MODE=dry
#   7. judge kill-switch ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0
#   8. unparsable-first-attempt retry (entries==1 ∧ attempts==2)
#   9. bound-terminal 4th deny cycle (3 deny + 1 terminal arc)
#
# Spies:
#   * HTTP-attempt level: monkeypatch
#     daemon.services.attestation_report_judge._invoke_judge_llm
#   * Entry-point level: wrap judge_fused_bundle_async + judge_completion_report_async
#     with counting delegates to the REAL impls (preserves retry-on-unparsable)
#   * Flip: monkeypatch daemon.graph._LCA_STAGE2_RESOLVER_FLIP
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): subprocess `timeout 240s pytest ...`
# Hard cap: 5 minutes per pack execution (skill rule).
#
# Branch:        feature/lca-resolver-stage2
# Engine focus:  daemon/graph.py (fused-judge block + Stage-2 flip + 2 legacy sites)
#                 daemon/services/attestation_report_judge.py (both entry points + truncation)
#                 daemon/services/attestation_resolver_activation.py (band rows)
#                 daemon/services/attestation_judge_resolver.py (kill-switch)
#
# Runbook:
#   uv run python -m pytest \
#     tests/integration/test_attestation_stage2_budget_parity.py \
#     --tb=short -q
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: lca2_budget_parity_integration_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo ""

# Layer 2 — script-internal per-pytest timeout (innate test-pack invariant).
# 240s per pytest invocation — leaves 60s headroom under the 300s outer cap
# for the matrix (9 scenarios × 2 flips × 2 test classes ≈ 36 cells; the
# multi-cycle s9 cell drives 4 evaluations; cross-flip test runs all 18
# cells in one go). Each cell is fast (~50-200 ms) since there are no real
# LLM calls; the s9 cell with 4 cycles runs in <1s.
PYTEST_TIMEOUT=240

# Layer 1 — outer command-level timeout is the caller's responsibility
# (`timeout 300 ./test/packs/lca2_budget_parity_integration_test.sh`).
# This script assumes the outer wrap is in effect.

# xdist guard — keep single-worker for resolver cache determinism
# (killswitch fixture resets the cached-global resolvers per test;
# xdist would race the reset).
unset PYTEST_XDIST_WORKER || true

RESULT=0
trap 'rc=$?; echo "" >&2; echo "[teardown] pack exit code=$rc" >&2; exit $rc' EXIT INT TERM

# Single pytest invocation with --collect-only sanity check first to surface
# import errors fast (faster than letting them manifest mid-run).
echo "[sanity] --collect-only ..."
timeout $PYTEST_TIMEOUT uv run python -m pytest \
    tests/integration/test_attestation_stage2_budget_parity.py \
    --collect-only -q
COLLECT_RC=$?
if [ "$COLLECT_RC" -ne 0 ]; then
    echo ""
    echo "RESULT: FAIL (collect-only rc=$COLLECT_RC)"
    exit 1
fi
echo ""

echo "[run] pytest with $PYTEST_TIMEOUT s per-invocation timeout ..."
START=$(date +%s)
timeout $PYTEST_TIMEOUT uv run python -m pytest \
    tests/integration/test_attestation_stage2_budget_parity.py \
    --tb=short -q
PYTEST_RC=$?
END=$(date +%s)
ELAPSED=$((END - START))

echo ""
echo "elapsed: ${ELAPSED}s"
echo "pytest rc: $PYTEST_RC"

if [ "$PYTEST_RC" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
elif [ "$PYTEST_RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT (pytest rc=124)"
    exit 124
else
    echo "RESULT: FAIL (pytest rc=$PYTEST_RC)"
    exit 1
fi
