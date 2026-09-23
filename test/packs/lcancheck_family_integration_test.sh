#!/usr/bin/env bash
# Test Pack: lcancheck_family_integration_test — LCA Completion Check
# Note family SEPARATION merge gate (Job 3, ff9eb849, base 6bf7bed7).
#
# Purpose: live verification that the LCA Completion Check Note family
# removal (incident b2f4dae9, 2026-09-23) is FAMILY-ISOLATED from the
# attest-first Final Report Reminder family. The removal retired ONLY
# the Completion Check Note family; the attest-first Final Report
# Reminder family MUST be UNTOUCHED. The two families share the
# ``attestation_route="agent"`` + ``HumanMessage`` injection shape but
# NEVER the title, body, stable-id kind, or counter channel.
#
# Architecture under test:
#   - S1 — HOLD → Final Report Reminder STILL injects (live):
#       Two sub-scenarios drive the REAL gate node (production
#       ``create_attestation_gate_node`` factory closure) with the
#       CLEAN-call shape and the c5d9a38a BUNDLED shape. Both routes
#       resolve to ``Decision.HOLD`` and inject exactly one
#       ``HumanMessage`` carrying the canonical Final Report Reminder
#       — the ``[SYSTEM CONTEXT: Final Report Reminder]`` title +
#       ``attestation_final_report_reminder:{instance_id}`` stable id
#       + ``attestation_reminder_count == 1`` + ``attestation_route
#       == "agent"``. The injected body MUST NOT carry any
#       Completion Check Note needle — that is the family-separation
#       evidence.
#   - S2 — runtime zero-note + kind retirement contract:
#       Drive a (b)/(d)-with-pending-shaped turn (live RUNNING child
#       + LCA busy trigger suppression) → ZERO injected messages.
#       Direct retirement negative: ``_stable_id_for(
#       "completion_check_note", instance_id=...)`` raises
#       ``ValueError`` (supported kinds are now exactly four).
#   - S3 — whole-tree census (independent of dev's pins):
#       Real ``grep -rn`` across the worktree for each retired
#       needle. Classify every hit. LIVE ``daemon/`` or ``frontend/``
#       hits = FAIL; everything else (tests/, docs/, planning,
#       tester artifacts) = OK.
#
# - Hermetic test envelope (stub manager + stub ledger + monkey-
#   patched resolvers + ``llm_judge_enabled=False``) — NO LLM HTTP,
#   NO DB, NO daemon. Real ``create_attestation_gate_node`` factory
#   closure + real ``_stable_id_for`` + real ``attest_completion``
#   tool body.
#
# Drift pin (FIRST, before every commit): branch =
# ``feature/lca-remove-check-note`` AND ``ff9eb849`` is an ancestor
# of HEAD AND ``git diff ff9eb849 HEAD -- daemon/ scripts/
# migrations/`` is EMPTY (test-only commits on top expected; other
# workers commit concurrently). STOP + report on mismatch.
#
# PORT SAFETY: 5432 (prod PG), 8079 (dev API), 8088 (self-system)
# NEVER touched.
#
# Run:  timeout 300 bash test/packs/lcancheck_family_integration_test.sh
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer): `timeout 300` wrapper by the caller
#   Layer 2 (inner): subprocess `timeout 240s pytest ...`
# Hard cap: 5 minutes per pack execution.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: lcancheck_family_integration_test ==="
echo "Project: $PROJECT_DIR"
echo "Branch: $(git rev-parse --abbrev-ref HEAD)"
echo "HEAD:   $(git rev-parse --short HEAD)"
echo "Date:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# === DRIFT-PIN ===========================================================
echo ""
echo "[drift] head: $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
echo "[drift] ff9eb849 ancestor: $(git merge-base --is-ancestor ff9eb849 HEAD && echo YES || echo NO)"
echo "[drift] daemon/scripts/migrations diff (must be empty):"
if git diff ff9eb849 HEAD -- daemon/ scripts/ migrations/ | grep -q '^'; then
    echo "[FAIL] DRIFT: daemon/scripts/migrations differ from ff9eb849"
    git diff ff9eb849 HEAD -- daemon/ scripts/ migrations/ --stat
    echo ""
    echo "RESULT: FAIL (drift-pin violation)"
    exit 1
else
    echo "[OK] daemon/scripts/migrations are byte-identical to ff9eb849"
fi

# === ENV-SCRUB ===========================================================
# Belt and suspenders: scrub prod-related env vars so daemon-side
# modules that auto-connect on import (persistence.py env-detect)
# cannot bleed into the disposable PG or into prod. SSL vars scrubbed
# per house recipe. The test is hermetic (no PG, no LLM), but the
# scrub stays consistent with the rest of the packs.
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL DATABASE_URL || true
unset SSL_CERT_FILE SSL_CERT_DIR || true

# xdist guard — keep the dev-shared PG/SQLite seam clean. (The
# lcancheck_family_separation tests use stub manager + stub ledger;
# xdist doesn't break them, but we want a deterministic single-
# process run for forensics.)
unset PYTEST_XDIST_WORKER || true

# === PYTEST (single invocation, 240s inner timeout) =====================
echo ""
echo "=== Pytest (Layer-2: 240s internal) ==="

PYTEST_RC=0
# Per-test --timeout=240 caps damage if any individual test hangs.
# --tb=short + -q for compact output. NO -x (test contract requires
# collecting every assertion failure, not stopping at the first).
timeout 240s uv run python -m pytest \
    tests/integration/test_lcancheck_family_separation.py \
    --override-ini="addopts=" -m "" --tb=short -q \
    --timeout=240 \
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
