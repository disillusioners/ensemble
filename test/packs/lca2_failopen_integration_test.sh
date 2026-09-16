#!/usr/bin/env bash
# Test Pack: lca2_failopen_integration_test — LCA Stage 2 RESOLVER-FAULT FAIL-OPEN
#
# Purpose: Integration-level fault-injection matrix for the LCA resolver Stage 2
# flip (``daemon/graph.py`` fused block, gated by
# ``_LCA_STAGE2_RESOLVER_FLIP=True``). Drives the REAL gate node
# (``create_attestation_gate_node``) through four seam fault classes (i) /
# (ii) / (iii) / (iv) crossed with three bands (deny-band quiet-un-attested /
# marker-band / A-band) plus the F2 wakeup re-fire class (2 scenarios).
#
# Covering file (exactly 1):
#   1. tests/integration/test_attestation_stage2_failopen.py
#
# Fault seams (Job 5 spec):
#   * (i)   activation-predicate computation raises
#   * (ii)  fused-payload build raises (child-evidence assembly)
#   * (iii) judge invocation itself raises (transport/LLM error)
#   * (iv)  verdict parse / outcome mapping raises
#
# Per-band conservative outcome expectations are documented IN the test
# bodies — the assertions only fail on the explicit contract pins (gate
# survives; loud-row contract holds; F2 re-fire verified).
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): subprocess `timeout 240s pytest ...`
# Hard cap: 5 minutes per pack execution (skill rule).
#
# Branch:        feature/lca-resolver-stage2  @  <expected HEAD at authoring>
# Engine focus:  daemon/graph.py (fused-judge block + Stage-2 flip)
#                 daemon/services/attestation_resolver_activation.py
#                   (activation_predicate, assemble_fused_bundle,
#                   emit_resolver_eval_row)
#                 daemon/services/attestation_report_judge.py
#                   (judge_fused_bundle_async)
#                 daemon/services/attestation_gate.py (§(vi) inner try/except)
#
# Runbook:
#   uv run python -m pytest \
#     tests/integration/test_attestation_stage2_failopen.py \
#     --tb=short -q
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: lca2_failopen_integration_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo ""

# Layer 2 — script-internal per-pytest timeout (innate test-pack invariant).
# 240s per pytest invocation — leaves 60s headroom under the 300s outer
# cap for the 12-cell matrix + F2 re-fire class (each cell ~20-30s).
PYTEST_TIMEOUT=240

# Layer 1 — outer command-level timeout is the caller's responsibility
# (`timeout 300 ./test/packs/lca2_failopen_integration_test.sh`).
# This script assumes the outer wrap is in effect.

# xdist guard — keep single-worker for resolver cache determinism.
unset PYTEST_XDIST_WORKER || true

RESULT=0
trap 'rc=$?; echo "" >&2; echo "[teardown] pack exit code=$rc" >&2; exit $rc' EXIT INT TERM

# Single pytest invocation with --collect-only sanity check first to surface
# import errors fast (faster than letting them manifest mid-run).
echo "[sanity] --collect-only ..."
timeout $PYTEST_TIMEOUT uv run python -m pytest \
    tests/integration/test_attestation_stage2_failopen.py \
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
    tests/integration/test_attestation_stage2_failopen.py \
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
