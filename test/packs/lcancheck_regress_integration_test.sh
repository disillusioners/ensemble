#!/usr/bin/env bash
# Test Pack: lcancheck_regress_integration_test — INDEPENDENT b2f4dae9
# regression construction for the LCA Completion Check Note removal
# merge gate (feature/lca-remove-check-note).
#
# Purpose: independent acceptance evidence that the 2026-09-23 LCA
# Completion Check Note removal is wired correctly end-to-end through
# the REAL gate node. Three cells (S1/S2/S3), each driven through the
# REAL compiled graph via ``ainvoke`` + scripted chat model + judge
# patched at the module-attribute seam:
#
#   S1  b2f4dae9 healthy-busy regression (RUNNING developer child,
#       busy=1, live=1, pending=1) — the canonical incident shape;
#       ALLOW log-only, ZERO Completion Check Note messages, full
#       surviving log-row schema.
#   S2  suspect-pending PAUSED child (busy=0, live=1, pending=1) —
#       PAUSED is unconditional-live (R2 ALLOW via live_descendants);
#       ALLOW log-only; deny path INTACT.
#   S3  suspect-pending en-route-only (terminal child, busy=0, live=0,
#       pending=1) — PENDING dependency-watcher wakeup drives R2 via
#       pending_children arm; ALLOW log-only; deny path INTACT.
#
# Covering file (exactly 1):
#   tests/integration/test_lcancheck_b2f4dae9_regression.py  (3 tests)
#
# Drift-pin: HEAD must be feature/lca-remove-check-note @ ff9eb849,
# the diff vs base 6bf7bed7 is the LCA note removal commit
# (single-commit branch); test-only commits on top are expected
# (other gate workers commit concurrently) but the daemon/scripts/
# migrations diff vs ff9eb849 MUST stay EMPTY (the relaxed guard).
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): `timeout 240` per invocation
# Per-test cap: --timeout=240 (pytest-timeout). Hard cap: 5 min/pack.
#
# Tests run via `uv run python -m pytest` ONLY (bare pytest PATH-resolves
# to a broken foreign Homebrew install). `--override-ini="addopts="`
# clears the default `-m 'not integration and not postgres'` exclusion;
# `-m integration` selects the marked tests. Every invocation is
# prefixed with `env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB
# -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL`
# so no module auto-connects to the prod database (the file-backed
# SQLite is the only DB the integration tests use).
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Test Pack: lcancheck_regress_integration_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "Date:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# === DRIFT-PIN (verify only — never mutate) ==============================
EXPECTED_SHA="ff9eb849"
ACTUAL_SHA="$(git rev-parse --short HEAD)"
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
    echo "[drift] HEAD=$ACTUAL_SHA expected=$EXPECTED_SHA — DRIFT (hard blocker)"
fi
# Ancestor check: the LCA note removal commit MUST be in HEAD's
# ancestry (the test-only commits on top are expected; the fix is
# already on the branch).
if ! git merge-base --is-ancestor "$EXPECTED_SHA" HEAD; then
    echo "[drift] $EXPECTED_SHA is NOT an ancestor of HEAD — DRIFT (hard blocker)"
fi
# Relaxed guard: daemon/ + scripts/ + migrations/ diff vs ff9eb849
# MUST be empty. Test-only commits on top are expected; production-
# code changes would invalidate the merge gate.
DAEMON_DIFF="$(git diff "$EXPECTED_SHA" HEAD -- daemon/ scripts/ migrations/)"
if [ -n "$DAEMON_DIFF" ]; then
    echo "[drift] daemon/, scripts/, or migrations/ changed vs $EXPECTED_SHA — DRIFT (hard blocker)"
    echo "$DAEMON_DIFF" | head -20
fi
# Belt and suspenders: scrub prod env so no module auto-connects to prod.
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL DATABASE_URL || true
unset SSL_CERT_FILE SSL_CERT_DIR || true
unset PYTEST_XDIST_WORKER || true

echo ""
echo "=== Pytest (Layer-2: 240s internal, per-test timeout 240) ==="
PACK_START=$(date +%s)

timeout 240 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB \
    -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL \
    -u DATABASE_URL \
    uv run python -m pytest \
    tests/integration/test_lcancheck_b2f4dae9_regression.py \
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