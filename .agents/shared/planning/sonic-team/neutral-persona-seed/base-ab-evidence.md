# Base A/B evidence — pre-existing red set (integration gate)

Gate: pytest over the 33 touched unit test files on the MERGED tree.
MERGED tree result: 6 failed, 748 passed (61.30s).
BASE leg: detached worktree at 3c64d47f5 (uv sync --frozen, own .venv), same test selection
          -> 6 failed, 14 passed (3.72s) — SAME 6 failure identities.

Pre-existing failures (A/B-proven identical at base — NOT branch-caused):
1. tests/unit/test_anomaly_report_grant_pins.py::TestTheGrantCompletenessWalk::test_the_grant_is_the_last_element
2. tests/unit/test_anomaly_report_grant_pins.py::TestTheGrantCompletenessWalk::test_the_three_phase_3_touched_agents_carry_both_entries
3. tests/unit/test_anomaly_report_grant_pins.py::TestTheSealMapAfterTheGrant::test_the_seal_map_is_zero_stale
   -> stale SEALED_SHA_BASELINES rows (35-row map in tests/test_chart_image_delivery_e2e.py:1425):
      daemon/constants.py (baseline 5ce5a419... / current 6c1628b8...) and
      agents/leader/rule.md (baseline 6a225b9f... / current 9ee75e1f...).
      NEITHER file is touched by this sweep.
4. tests/unit/test_memoir_schema_contract.py::test_the_git_derived_lane_delta_is_exactly_one_migration_file
   -> environmental: refs/heads/feature/ari-memoir-store is absent in this repo, so the leg
      degrades to LANE_BASE..HEAD and counts 14 foreign migration files.
5. tests/unit/test_prompt_assent_lane.py::TestTheStructuralPins::test_the_gate_has_exactly_one_implementation_site
6. tests/unit/test_prompt_assent_lane.py::TestTheStructuralPins::test_the_render_hash_recheck_lives_only_in_the_hook
   -> environmental path artifact: grep prints 'daemon//repositories/prompt_assent/consume_hook.py'
      (double slash) while the assertion expects 'daemon/repositories/...' — identical at base.

Env parity: daemon.__file__ resolves inside the task worktree (editable .pth pins the worktree path).
