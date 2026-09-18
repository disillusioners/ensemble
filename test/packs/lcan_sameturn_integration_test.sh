#!/usr/bin/env bash
# Test Pack: lcan_sameturn_integration_test — SAME-TURN WINDOW live
# verification for the LCA advisory-note-removal merge gate.
#
# Purpose: verify the drain→gate ordering property on the live
# ``agents-ensemble`` graph turn. The property: a child report drained
# at turn-T START IS visible to the completion gate evaluating at
# turn-T END (A-band fires: deny+nudge engages for the deny-band
# shape), and the SAME gate evaluating WITHOUT the in-window stamped
# row does NOT fire A-band. TWO stamp writers exist (the live drain
# at ``daemon/graph.py:~6960`` and the fallback enqueue at
# ``daemon/services/instance_messaging.py:~525``) — both produce a
# ``HumanMessage`` whose ``additional_kwargs["source"]`` starts with
# ``internal_report:`` so the A-scan's
# :func:`_is_child_report_message` detector recognizes them
# identically.
#
# Covering file (exactly 1):
#   1. tests/integration/test_lcan_sameturn_window.py  (4 tests =
#      S1 live-drain writer (full graph) + S2 fallback-enqueue
#      writer (gate-node direct) + S3 negative T+1 isolation
#      (gate-node direct; full-graph equivalent blocked by the
#      live drain slot's per-thread semantics — a second ainvoke
#      on the same thread_id does NOT re-trigger the drain) +
#      1× drift-pin)
#                                                       ----
#                                                       4 tests total
#
# Branch:        feature/lca-remove-advisory-note  @  0a4fccb1  (FROZEN —
#                verify, never rebase under a running gate)
# Engine focus:  daemon/graph.py (drain site + gate node),
#                daemon/services/instance_messaging.py (fallback
#                enqueue stamp), and daemon/services/
#                attestation_resolver_activation.py (A-scan from
#                the merged 6a695b8f — the evaluation-time scan
#                that replaced the deleted producer).
# Env vars:      none required (hermetic envelope — stub manager +
#                ledger; ``_invoke_judge_llm`` mocked to
#                ``not_complete`` per-test).
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): subprocess `timeout 240s pytest ...`
# Hard cap: 5 minutes per pack execution (skill rule). This pack is
# hermetic-stub and finishes in ~2s in steady state; the 240s
# inner cap is generous.
#
# No external services / ports / DBs needed beyond what the
# support fixtures already establish (file-backed SQLite via
# ``file_sqlite_engine`` + in-memory checkpointer). No real LLM
# call (the LangGraph ``build_instance_llms`` patch points to a
# ScriptedChatModel).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: lcan_sameturn_integration_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "Date:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# Belt and suspenders: scrub prod-related env vars so daemon-side
# modules that auto-connect on import (persistence.py env-detect)
# cannot bleed into the disposable PG or prod. SSL vars scrubbed
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
# The pack uses the `-m integration` filter (the integration test
# marker is registered in pyproject.toml; tests/integration/* are
# the documented location for these packs). The
# `--override-ini="addopts="` strips any default addopts that
# would otherwise inject -p no:randomly or other noisy options.
timeout 240s uv run python -m pytest \
    tests/integration/test_lcan_sameturn_window.py \
    --override-ini="addopts=" -m integration --tb=short -v \
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