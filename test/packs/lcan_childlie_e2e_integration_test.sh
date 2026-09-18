#!/usr/bin/env bash
# Test Pack: lcan_childlie_e2e_integration_test — CHILD-LIE E2E for the
# LCA advisory-note-removal merge gate (feature/lca-remove-advisory-note).
#
# Purpose: THE acceptance test that the A-band activation now comes from
# the evaluation-time transcript scan of internal_report-stamped child
# reports (NO delivery-time Child Report Check note is minted anywhere):
#   S1  child-lie deny→attest-allow core arc (real graph, 2 evals)
#   S2  revive variant → fused-judge rescue allow (real graph)
#   S3  D2 row: A-band fires ALONE during busy descendants (real graph)
# Global: zero child_report_check notes minted/delivered in any lane.
#
# Covering file (exactly 1):
#   tests/integration/test_lcan_childlie_e2e.py  (3 tests = S1 + S2 + S3)
#
# Drift-pin: HEAD must be feature/lca-remove-advisory-note @ 0a4fccb1.
# NEVER commit/checkout/reset from this pack — verify only.
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): `timeout 240` per invocation
# Per-test cap: --timeout=240 (pytest-timeout). Hard cap: 5 min/pack.
#
# Tests run via `uv run python -m pytest` ONLY (bare pytest PATH-resolves
# to a broken foreign Homebrew install). `--override-ini="addopts="`
# clears the default `-m 'not integration and not postgres'` exclusion;
# `-m integration` selects the marked tests.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Test Pack: lcan_childlie_e2e_integration_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "Date:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# === DRIFT-PIN (verify only — never mutate) ==============================
EXPECTED_SHA="0a4fccb1"
ACTUAL_SHA="$(git rev-parse --short HEAD)"
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
    echo "[drift] HEAD=$ACTUAL_SHA expected=$EXPECTED_SHA — DRIFT (hard blocker, continuing read-only)"
fi
if ! git diff --cached --quiet; then
    echo "[warn] staged index dirty — listing for forensics:"
    git diff --cached --stat
fi

# Belt and suspenders: scrub prod env so no module auto-connects to prod.
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL || true
unset SSL_CERT_FILE SSL_CERT_DIR || true
unset PYTEST_XDIST_WORKER || true

echo ""
echo "=== Pytest (Layer-2: 240s internal, per-test timeout 240) ==="
PACK_START=$(date +%s)

timeout 240 uv run python -m pytest \
    tests/integration/test_lcan_childlie_e2e.py \
    --override-ini="addopts=" -m integration --timeout=240 \
    --tb=short -q
PYTEST_RC=$?

PACK_END=$(date +%s)
echo "Pack inner runtime: $((PACK_END-PACK_START))s"

echo ""
echo "=== Report ==="
case "$PYTEST_RC" in
    0)   echo "RESULT: PASS" ;;
    124) echo "RESULT: TIMEOUT" ;;
    *)   echo "RESULT: FAIL (exit=$PYTEST_RC)" ;;
esac
exit $PYTEST_RC
