#!/usr/bin/env bash
# prompt_assent_phase3_unit_test.sh
#
# The prompt-assent Phase-3 lane pack — the acceptance path and the
# provenance flow (branch feature/prompt-assent, base 0efdf106f).
#
# Wraps the Phase-3 test surface in ONE pytest invocation:
#
#   * tests/unit/test_prompt_assent_lane.py — the lane matrix: the
#     Phase-2 gate cells + the Phase-3 acceptance cells (the approve
#     happy path with a real token echo, the five mandated refusals,
#     the shared approver predicate, the structural pins);
#   * tests/unit/test_prompt_assent_freshness.py — D-6/J-2 (stale /
#     unparseable stamps; mint AND approve alike);
#   * tests/unit/test_prompt_assent_mint_gate.py — K-2 / K-3 (no
#     window ⇒ no row, prior live token untouched; group-DM /
#     absent-discriminator ⇒ mint-requires-private-channel);
#   * tests/unit/test_prompt_assent_user_binding.py — AR-1 legs
#     (channel mismatch, approver mismatch, the R3 analogue: no
#     identity tool args, the durable counter + lockout);
#   * tests/unit/test_prompt_assent_budget.py — K-9/D-3b (the 11th
#     mismatch, per-instance, rollover, in-RAM only);
#   * tests/unit/test_prompt_assent_render_mutation.py — J-3/K-7
#     (pending- AND effective-side mutation ⇒ token-content-mismatch
#     with rollback);
#   * tests/unit/test_prompt_assent_counter_transaction.py — the D-4
#     blocking rule (the counter survives a guarded rollback; the
#     lockout fires);
#   * tests/unit/test_prompt_assent_counter_mutations.py — the SIX
#     named counter-mutations + the K-7 column-source pin, each
#     demonstrated to REDDEN its cell;
#   * tests/unit/test_prompt_assent_designated_channel.py — the
#     Phase-1.5 designated-channel cell matrix (20 mint + 12 approve
#     cells; the runtime resolver-raises pin; the map-unset
#     equivalence; the digest provenance; the P-2b/P-2c/P-3/P-7/P-8
#     counter-mutations) — Phase 1.5, T3.10;
#   * tests/unit/test_prompt_assent_designation_parser.py — the
#     Phase-1 designation-map parser + typed outcome + the arena pins
#     (P-1, P-6) — Phase 1.5, T3.10;
#   * tests/unit/test_prompt_assent_fe_unchanged.py — the FE proof
#     (routes / service / repo methods byte-identical to base;
#     F4 + the bearer clamp intact);
#   * tests/integration/test_prompt_assent_flow.py — the end-to-end
#     provenance flow (the ledger reconstruction + the raw token's
#     absence). NOTE: the integration dir is deselected by default;
#     this pack runs the file EXPLICITLY.
#
# Expected: 0 failed. Exit 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Constraints honored (test-pack skill, MANDATORY):
#   * Single pack — this is the ONLY pack for the lane.
#   * Dual-layer timeout — script-internal 300s (Layer 2), caller
#     wraps with `timeout 600` (Layer 1). Estimated runtime ~40-60s.
#   * agents/ and daemon/ read-only — the suite greps pins only.
#   * No -x (continue on first failure — we want the full count).
#   * Single-process collection (no -n auto).
#   * Env hygiene (H-7): the env scrub is PER-VARIABLE (a bare
#     `env -u POSTGRES_*` is a silent NO-OP) — set the scrub env when
#     invoking this pack; the designation map var is scrubbed too, so
#     an ambient operator map cannot leak into the house gate (T3.10):
#       env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
#           -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
#           -u ENSEMBLE_SELF_ENV \
#           -u ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS \
#           bash test/packs/prompt_assent_phase3_unit_test.sh

set -euo pipefail

# N2 (2026-11-12 fix round): the per-variable `-u` flags in the
# invocation note above are ADVISORY (they document the caller's duty);
# this body-level unset makes the scrub REAL — an ambient operator map
# cannot leak into the house gate even when the pack is invoked without
# the `-u` flags (T3.10; the one ambient-flippable pin is the
# guild-default ids at tests/unit/test_prompt_assent_mint_gate.py:102).
unset ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: prompt_assent_phase3_unit_test ==="
echo "Surface: the prompt-assent Phase-3 acceptance path + provenance flow"
cd "$PROJECT_DIR"

EXIT_CODE=0
timeout 300s .venv/bin/pytest \
  tests/unit/test_prompt_assent_lane.py \
  tests/unit/test_prompt_assent_freshness.py \
  tests/unit/test_prompt_assent_mint_gate.py \
  tests/unit/test_prompt_assent_user_binding.py \
  tests/unit/test_prompt_assent_budget.py \
  tests/unit/test_prompt_assent_render_mutation.py \
  tests/unit/test_prompt_assent_counter_transaction.py \
  tests/unit/test_prompt_assent_counter_mutations.py \
  tests/unit/test_prompt_assent_designated_channel.py \
  tests/unit/test_prompt_assent_designation_parser.py \
  tests/unit/test_prompt_assent_fe_unchanged.py \
  tests/integration/test_prompt_assent_flow.py \
  --override-ini="addopts=" --tb=short -q -p no:timeout 2>&1 || EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$EXIT_CODE" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
