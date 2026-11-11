#!/usr/bin/env bash
# prompt_assent_matrix_test.sh
#
# The prompt-assent LANE PACK — the FULL test matrix as a first-class
# artefact (phase 4, task 4.4; branch feature/prompt-assent, base
# 0efdf106f). Wraps the lane's ENTIRE surface in ONE pytest invocation:
#
# The mandatory cells (architect §4), each individually addressable by
# its pytest node id and reporting its own name:
#   * the happy path with a real token echo + the five mandated
#     refusals          -> test_prompt_assent_lane.py
#   * the SIX named counter-mutations (each demonstrated to redden)
#                         -> test_prompt_assent_counter_mutations.py
#   * the seven amendment-pass cells (mint-without-window, group-DM
#     mint, wrong-user-of-channel, budget exhaustion, pending-side
#     mutation, effective-side mutation, column-source pin)
#                         -> test_prompt_assent_mint_gate.py /
#                            test_prompt_assent_budget.py /
#                            test_prompt_assent_user_binding.py /
#                            test_prompt_assent_render_mutation.py /
#                            test_prompt_assent_column_source.py
#   * the counter-separate-transaction regression cell
#                         -> test_prompt_assent_counter_transaction.py
#   * the additional refusal cells
#                         -> test_prompt_assent_tool_refusals.py
#   * the token-store cells
#                         -> test_prompt_assent_token_store.py
#   * the registration cells (exact-set + count + the ari-ONLY census
#     + the shared-default single-home pin)
#                         -> tests/unit/tools/test_prompt_assent_registration.py
#   * the freshness cells (D-6/J-2, mint AND approve)
#                         -> test_prompt_assent_freshness.py
#   * the FE-unchanged cells
#                         -> test_prompt_assent_fe_unchanged.py
#
# Phase-4 additions (the kill-switch matrix):
#   * default OFF => lane-disabled OUT OF THE BOX (config tier AND env
#     tier), the ON-path test ARMS THE ENV EXPLICITLY, mint refused
#     when off, invalid => fail-closed, the refusal names
#     ENSEMBLE_PROMPT_ASSENT_ENABLED, not-ari wins over the switch,
#     the budget KEY rides the class
#                         -> test_prompt_assent_kill_switch.py
#   * the Phase-4 structural pins (the shared-constant BOTH-consumers
#     pin + the retired-spelling tree grep, each with an in-file
#     counter-mutation demonstration)
#                         -> test_prompt_assent_pins.py
#
# SP-1 (rewritten to the rendered canonical bytes, K-7) and SP-2 (no
# UPDATE/DELETE against prompt_audit_events) live in
# test_prompt_assent_column_source.py /
# test_prompt_assent_counter_mutations.py /
# test_prompt_assent_audit_payload.py — collected here (the plan's
# "pack-embedded" shape).
#
# The end-to-end provenance flow (the ledger reconstruction + the raw
# token's absence) is the integration file, run EXPLICITLY (the
# integration dir is deselected by default addopts):
#                         -> tests/integration/test_prompt_assent_flow.py
#
# The PG durable legs (the counter wiring + the token store against a
# native disposable cluster) are the postgres shard — run separately
# per their own headers (PG_TEST_* DSN), never inside this pack.
#
# Expected: 0 failed. Exit 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# DEFAULT-OFF NOTE (D-2): the lane's default state is lane-disabled out
# of the box — the pack's ON-path cells arm the env EXPLICITLY
# (setenv ENSEMBLE_PROMPT_ASSENT_ENABLED=1); an unarmed run is NOT a
# pass, it is a vacuous run.
#
# Constraints honored (test-pack skill, MANDATORY):
#   * Single pack — this is the ONLY whole-lane pack (the phase-3 pack
#     remains as the narrower phase snapshot).
#   * Dual-layer timeout — script-internal 300s (Layer 2), caller
#     wraps with `timeout 600` (Layer 1). Estimated runtime ~60-90s.
#   * agents/ and daemon/ read-only — the suite greps pins only.
#   * No -x (continue on first failure — we want the full count).
#   * Single-process collection (no -n auto).
#   * Env hygiene (H-7): the env scrub is PER-VARIABLE (a bare
#     `env -u POSTGRES_*` is a silent NO-OP) — set the scrub env when
#     invoking this pack:
#       env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
#           -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
#           -u ENSEMBLE_SELF_ENV bash test/packs/prompt_assent_matrix_test.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: prompt_assent_matrix_test ==="
echo "Surface: the FULL prompt-assent matrix (phases 1-4)"
cd "$PROJECT_DIR"

EXIT_CODE=0
timeout 300s .venv/bin/pytest \
  tests/unit/test_prompt_assent_lane.py \
  tests/unit/test_prompt_assent_kill_switch.py \
  tests/unit/test_prompt_assent_pins.py \
  tests/unit/test_prompt_assent_freshness.py \
  tests/unit/test_prompt_assent_mint_gate.py \
  tests/unit/test_prompt_assent_user_binding.py \
  tests/unit/test_prompt_assent_budget.py \
  tests/unit/test_prompt_assent_render_binding.py \
  tests/unit/test_prompt_assent_render_mutation.py \
  tests/unit/test_prompt_assent_counter_transaction.py \
  tests/unit/test_prompt_assent_counter_mutations.py \
  tests/unit/test_prompt_assent_column_source.py \
  tests/unit/test_prompt_assent_consume_hook.py \
  tests/unit/test_prompt_assent_actor_lane.py \
  tests/unit/test_prompt_assent_audit_payload.py \
  tests/unit/test_prompt_assent_group_dm_discriminator.py \
  tests/unit/test_prompt_assent_tool_refusals.py \
  tests/unit/test_prompt_assent_token_store.py \
  tests/unit/test_prompt_assent_list_surface.py \
  tests/unit/test_prompt_assent_list_pending.py \
  tests/unit/test_prompt_assent_fe_unchanged.py \
  tests/unit/tools/test_prompt_assent_registration.py \
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
