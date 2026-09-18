#!/usr/bin/env bash
# Test Pack: lcan_legacy_integration_test — LEGACY-CHECKPOINT DEFENSE
# for the LCA advisory-note-removal merge gate.
#
# Purpose: verify that a pre-removal leader checkpoint carrying an OLD
# Child Report Check note (minted by the now-deleted producer at
# ``daemon/services/child_reports.py`` prior to commit ``6a695b8f``)
# still A-band-activates through the kept legacy-note read branch in
# ``daemon/services/attestation_resolver_activation.py``
# (::``_is_child_report_check_note`` and the Pass-1 of
# ``collect_source_a_signals``). The kept path is the
# defense-in-depth seam against a silent regression for in-flight
# leaders across the upgrade — the producer is DELETED, but the
# detector is RETAINED so old checkpoints do not lose A-band coverage.
#
# Covering file (exactly 1):
#   1. tests/integration/test_lcan_legacy_checkpoint.py  (6 tests =
#      S1 legacy activates + S2 no re-mint + S3 mixed-shape single
#      trigger + 3× parametrize drift-pin)
#                                                       ----
#                                                       6 tests total
#
# Branch:        feature/lca-remove-advisory-note  @  0a4fccb1  (FROZEN —
#                verify, never rebase under a running gate)
# Engine focus:  daemon/services/attestation_resolver_activation.py
#                (Pass-1 legacy note detector + dual-surface kwargs
#                recognition) and
#                daemon/graph.py (gate-node post-resolve attested-allow
#                transition).
# Env vars:      none required (test envelope is hermetic — stub manager
#                + ledger; ``_invoke_judge_llm`` mocked to
#                ``not_complete`` per-test).
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): subprocess `timeout 240s pytest ...`
# Hard cap: 5 minutes per pack execution (skill rule). Unit-pack
# 2-min cap is the target; this is a fast hermetic-stub pack so it
# should fit comfortably inside.
#
# No external services / ports / DBs needed — the test uses stub
# manager + stub ledger + monkey-patched judge, with no real LLM call.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: lcan_legacy_integration_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "Date:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# Belt and suspenders: scrub prod-related env vars so daemon-side
# modules that auto-connect on import (persistence.py env-detect)
# cannot bleed into the disposable PG or into prod. SSL vars scrubbed
# per house recipe. No PG needed for this pack but the scrub stays
# consistent with the rest of the packs.
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL || true
unset SSL_CERT_FILE SSL_CERT_DIR || true

# xdist guard — keep the dev-shared PG/SQLite seam clean. (The
# lcan_* tests use stub manager + ledger; xdist doesn't break them,
# but we want a deterministic single-process run for forensics.)
unset PYTEST_XDIST_WORKER || true

# === DRIFT-PIN ===========================================================
echo ""
echo "[drift] head: $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
if ! git diff --cached --quiet; then
    echo "[warn] staged index dirty — listing diff for forensics:"
    git diff --cached --stat
fi

# === PYTEST (single invocation, 240s inner timeout) =====================
echo ""
echo "=== Pytest (Layer-2: 240s internal) ==="

PYTEST_RC=0
# The pack envelope is hermetic-stub (no LLM, no DB, no network);
# 240s is a generous cap — a typical run finishes in <2s.
timeout 240s uv run python -m pytest \
    tests/integration/test_lcan_legacy_checkpoint.py \
    --override-ini="addopts=" -m "" --tb=short -v \
    || PYTEST_RC=$?

# === REPORT ==============================================================
echo ""
echo "=== Report ==="
case "$PYTEST_RC" in
    0)   echo "RESULT: PASS" ;;
    124) echo "RESULT: TIMEOUT" ;;
    *)   echo "RESULT: FAIL (exit=$PYTEST_RC)" ;;
esac

exit $PYTEST_RC
