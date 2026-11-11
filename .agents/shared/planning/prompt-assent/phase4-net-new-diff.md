# Phase 4 — Net-new verification evidence (H-6, task 4.5)

Lane: `prompt-assent` Phase 4 (guardrails, docs) · branch
`feature/prompt-assent` · HEAD `3b62707ef` (+ docs commits) · TRUE base
`0efdf106f` (origin/latest, the lane's base commit).

Interpretation note (disclosed): the commission orders **no
whole-suite run**. The net-new protocol below therefore combines (a) a
REAL same-command base-vs-HEAD diff on the touched neighborhood, (b)
head-only spot verification of every pre-existing red named in the
commission's context (the provided base record), and (c) the lane
surface itself (net-new by construction — every prompt-assent test
file is new on this branch and green at HEAD). A full `tests/unit`
base capture was additionally started and is reported at the bottom if
it completed in time.

## (a) The touched neighborhood — SAME COMMAND, both trees

Command (identical at base and HEAD; per-variable env scrub; worktree
venv interpreter; `-p no:timeout`; explicit paths):

```
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u ENSEMBLE_SELF_ENV \
  pytest tests/test_config.py \
         tests/unit/persistence/test_checkpoint_metrics_vocabulary.py \
         tests/unit/test_notify_user_registration.py \
         tests/test_skill_evolution_config.py \
         tests/unit/tools/test_prompt_section_reference_integrity.py \
         tests/unit/test_board_registration.py \
         tests/unit/test_memoir_registration.py \
         tests/unit/tools/test_prompt_reload_pins.py \
         [-e tests/unit/tools/test_prompt_assent_registration.py  # HEAD only — file is net-new]
         -q -p no:timeout
```

| Tree | Result |
|---|---|
| base `0efdf106f` (detached worktree `/tmp/pa4-base`) | **8 failed, 1765 passed** |
| HEAD | **8 failed, 1786 passed** (+21 = the net-new prompt-assent registration file, all green) |

FAILED-list diff: **IDENTICAL** (the same 8 node ids on both trees):

```
FAILED tests/test_skill_evolution_config.py::TestConfigIntegration::test_skill_evolution_defaults_via_config
FAILED tests/test_skill_evolution_config.py::TestDefaults::test_defaults
FAILED tests/unit/persistence/test_checkpoint_metrics_vocabulary.py::TestSweepLaneVocabulary::test_rdrs_lanes_come_from_the_config_kill_switchs
FAILED tests/unit/tools/test_prompt_section_reference_integrity.py::test_no_bare_md_filename_tokens_in_prompts[install-mermaid-cli.md]
FAILED tests/unit/tools/test_prompt_section_reference_integrity.py::test_no_bare_md_filename_tokens_in_prompts[workflow.md13]
FAILED tests/unit/tools/test_prompt_section_reference_integrity.py::test_no_bare_md_filename_tokens_in_prompts[workflow.md20]
FAILED tests/unit/tools/test_prompt_section_reference_integrity.py::test_odd_fence_count_fails[rule.md5]
FAILED tests/unit/tools/test_prompt_section_reference_integrity.py::test_odd_fence_count_fails[workflow.md5]
```

Net-new failed-list on the touched neighborhood: **EMPTY**.

## (b) The commission's pre-existing reds — head-only spot verification

Each red named in the commission context was reproduced at HEAD with
the SAME identity (none attributed to this lane; none newly introduced):

| Provided base record | HEAD spot run | Match |
|---|---|---|
| `test_migration_files_present == 96` | `tests/test_prompt_version_resolution_core.py::TestMigrationParity::test_migration_files_present` — `assert 128 == 96` (1 failed; the stale 96-pin vs the tree's 128 migrations — 127 at base, already red there) | ✓ |
| 75 `test_prompts_router` AuthConfig errors | `tests/test_prompts_router.py`: **75 ERRORs** | ✓ exact count |
| metrics borrow-census drift from other lanes | `tests/unit/metrics/`: 7 failed + 1 error (family adjudication / W3 / auth-matrix ×3 / scope-cage census `reads_117`) | ✓ |
| `test_instance_tools` planner-grant | `tests/unit/tools/test_instance_tools.py`: 2 planner/tester registration failures | ✓ |
| `test_prompt_section_reference_integrity` | in (a)'s diff — red at base AND head, identical nodes | ✓ |
| `test_snapshot_tools` | `tests/unit/tools/test_snapshot_tools.py`: 1 failed | ✓ |
| `test_checkpoint_pool_config` | `tests/unit/test_checkpoint_pool_knob_threading.py` green at HEAD (the named red is either a differently-named node inside the full-unit base list or already repaired by another lane — the full-unit diff below is the authority) | see (c) |
| `test_checkpoint_metrics_vocabulary::test_rdrs_lanes` | in (a)'s diff — red at base AND head, identical node | ✓ |

## (c) The lane surface — net-new by construction, green

```
bash test/packs/prompt_assent_matrix_test.sh   # × 3 isolated: 249 passed each
+ reversed-order single invocation:            # 249 passed
```

At base these files do not exist (the lane is new on this branch), so
every one of the 249 nodes is net-new and green. The Phase-4 additions:
22 kill-switch nodes + 10 pin nodes.

## (d) Mutation evidence (live, with byte-identical restores)

Protocol: byte-identical backups in `/tmp/pa4-mutation-backups/`,
mutate the working tree ONLY, run ONE test file under `timeout 300`,
restore, prove `sha256 == PRE`.

| # | Counter-mutation (named) | Pin that reddened | Verdict |
|---|---|---|---|
| M1 | flip the shared constant (`daemon/config.py` `PROMPT_ASSENT_DEFAULT_ENABLED` False→True) | `test_prompt_assent_pins.py` → **2 failed** (the shared-constant pin + the fail-closed value leg) | RED → restore proven → 10 passed |
| M1b | the same flip | `test_prompt_assent_kill_switch.py` → **4 failed** (the default-OFF-out-of-the-box cells) | RED → restore proven → 22 passed |
| M2 | re-declare a second literal in `daemon/tools/prompt_assent.py` | `test_prompt_assent_registration.py` → **2 failed** (the single-home pin) | RED → restore proven → 21 passed |
| M3 | plant the retired `PromptAssist`-stem spelling in a code file | `test_prompt_assent_pins.py` → **1 failed** (the tree grep pin) | RED → restore proven → 10 passed |
| M4 | fail-open coercion (`_coerce_switch` indeterminate → `True`) | `test_prompt_assent_kill_switch.py` → **2 failed** (the invalid-fail-closed cells) | RED → restore proven → 22 passed |
| M5 | plant `UPDATE prompt_audit_events` (SP-2) | `test_prompt_assent_audit_payload.py::test_sp2_no_update_delete_against_audit_events` → **1 failed** | RED → restore proven → 6 passed |

## (e) Full-unit base capture (supplementary)

A full `tests/unit -q` run at the detached base worktree was started
(same command as the HEAD full-unit run) to produce the exhaustive
diff; result recorded in the lane report if it completed within the
lane's wall-clock budget.
