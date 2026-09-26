#!/usr/bin/env bash
# Test Pack: lcafc_incident_replay_e2e_test — INDEPENDENT 7d4a3bd9
# incident-replay acceptance for the LCA-FALSE-COMPLETE merge gate
# (feature/lca-false-complete-fixes @ d5c50994).
#
# Purpose: independent acceptance evidence that the 2026-09-26
# ``correct-judge-override`` fix cycle wires the exhaustion composition
# gate correctly end-to-end through the REAL gate node. Three cells
# (a/b/c), each driven through the REAL compiled graph via
# ``ainvoke`` + scripted chat model + judge patched at the module-
# attribute seam:
#
#   (a) Ep-B arc — 1 substantive deny + 2 timeout denies ⇒ loud
#       ``terminal_after_bound`` written with
#       ``completion_gate_escalated=True`` + the canonical
#       ``completed (gate escalated — unverified)`` label (the
#       COMPLETION_GATE_ESCALATED_DISPLAY constant). Judge was RIGHT
#       on the substantive verdict (the 7d4a3bd9 incident pinned
#       that).
#   (b) All-timeout variant — 4+ deny episodes ALL judge-timeout,
#       ZERO substantive. The exhaustion composition gate
#       RE-REPLACES the terminal_after_bound decision back to DENIED
#       (the counter rises past the bound, which is now correct under
#       v3 ruling); the deny+nudge cycle CONTINUES — ZERO terminal
#       writes. Then leader exits via ``attest_completion`` (the
#       meta_bypass / attested-allow fallback).
#   (c) Directive nudge — 2 consecutive deny episodes with ZERO new
#       tool calls ⇒ directive nudge body fires on the 2nd deny ONLY
#       (byte-pinned to ``daemon.graph.ATTESTATION_DIRECTIVE_NUDGE_TEXT``).
#
# Covering file (exactly 1):
#   tests/integration/test_lcafc_incident_replay_e2e.py  (3 tests)
#
# Drift-pin (per coordinator update 2026-09-26):
#   HEAD is valid iff `git merge-base --is-ancestor d5c50994 HEAD`
#   (ancestor-check, NOT equality). Sibling commits on the branch
#   (e.g. 0620597a lcafc_pg_lane pack) are EXPECTED.
#   Real invariant: `git diff d5c50994..HEAD -- daemon/ frontend/
#   scripts/ migrations/` MUST stay EMPTY — verify that.
#
# Judge-timeout override:
#   ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=5.0 (the documented
#   MIN_JUDGE_TIMEOUT_S floor; default at d5c50994 is 180.0s). Each
#   timeout deny episode costs at most 5.0s × 2 retries (retry-once-
#   on-timeout, incident bc145c7e R1) = 10s wall-clock. Cell (b)
#   runs ~3 timeout evals ⇒ ~30s of judge time; cell (a) runs 2 ⇒
#   ~20s; cell (c) runs 0. The override keeps every timeout cell
#   bounded under the pack cap. Per the spec — NEVER extend the
#   5-min cap; override the env instead. Documented here for the
#   audit trail.
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer command-level): wrap with `timeout 300 <this-script>`
#   Layer 2 (script-internal per-pytest): `timeout 240` per invocation
# Per-test cap: --timeout=120 (pytest-timeout). Hard cap: 5 min/pack.
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

echo "=== Test Pack: lcafc_incident_replay_e2e_test ==="
echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "Date:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# === DRIFT-PIN (verify only — never mutate) ==============================
# Per coordinator update (2026-09-26): sibling commits (pack scripts,
# test files, PACKS.md sections) are EXPECTED. The real invariant:
# - d5c50994 MUST be an ancestor of HEAD
# - the diff vs d5c50994 across daemon/, frontend/, migrations/,
#   scripts/ MUST stay EMPTY
EXPECTED_FIX_SHA="d5c50994"
ACTUAL_SHA="$(git rev-parse --short HEAD)"
if [ "$ACTUAL_SHA" != "$EXPECTED_FIX_SHA" ]; then
    echo "[drift] HEAD=$ACTUAL_SHA expected-anchor=$EXPECTED_FIX_SHA (ancestor-check below)"
fi
if ! git merge-base --is-ancestor "$EXPECTED_FIX_SHA" HEAD; then
    echo "[drift] $EXPECTED_FIX_SHA is NOT an ancestor of HEAD — DRIFT (hard blocker)"
    exit 1
fi
# The relaxed guard: daemon/, frontend/, scripts/, migrations/ diff
# vs d5c50994 MUST be empty. Sibling commits on the branch are
# expected; production-code changes invalidate the merge gate.
PROD_DIFF="$(git diff "$EXPECTED_FIX_SHA"..HEAD -- daemon/ frontend/ scripts/ migrations/)"
if [ -n "$PROD_DIFF" ]; then
    echo "[drift] daemon/, frontend/, scripts/, or migrations/ changed vs $EXPECTED_FIX_SHA — DRIFT (hard blocker)"
    echo "$PROD_DIFF" | head -20
    exit 1
fi
# Belt and suspenders: scrub prod env so no module auto-connects to prod.
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL DATABASE_URL || true
unset SSL_CERT_FILE SSL_CERT_DIR || true
unset PYTEST_XDIST_WORKER || true

echo ""
echo "Drift-pin: d5c50994 ancestor of HEAD — OK; prod diff empty — OK"
echo "Judge timeout override: ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=5.0 (MIN_JUDGE_TIMEOUT_S floor)"
echo ""

PACK="lcafc_incident_replay_e2e_test"
FILES=(
tests/integration/test_lcafc_incident_replay_e2e.py
)
echo "--- Pack: ${PACK} ---"
START=$(date +%s)
timeout 240 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL \
  uv run python -m pytest "${FILES[@]}" --tb=short -q \
    --override-ini="addopts=" \
    -m integration \
    --override-ini="timeout=120"
RC=$?
END=$(date +%s)
echo "Pack inner runtime: $((END-START))s"
if [ $RC -eq 124 ]; then
  echo "RESULT: TIMEOUT"; exit 124
elif [ $RC -eq 0 ]; then
  echo "RESULT: PASS"; exit 0
else
  echo "RESULT: FAIL"; exit 1
fi