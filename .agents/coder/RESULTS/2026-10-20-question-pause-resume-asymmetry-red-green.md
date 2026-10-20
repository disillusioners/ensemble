# Question pause/resume asymmetry — RED → GREEN evidence

Feature: `fix/question-pause-resume-asymmetry` (side-effect-paused
instances stranded after a sibling's `ask_questions`).

- BASE (pre-fix): `1ebdf469f` — "chore(release): bump version to 0.25.7"
- HEAD (post-fix): `02a6385c5` — "fix(question-pause): address review findings …"
- Worktree: `/home/nea/ensemble-src-wt-qprasym` (branch
  `fix/question-pause-resume-asymmetry`)
- Live cases the fix addresses: `f86c4015` / `756d37ba` (2026-10-19) —
  both needed a MANUAL resume.

## THE REPRO ARTIFACT

`tests/manual/question_pause_strand_repro.py` — a STANDALONE symptom
repro. It deliberately imports NOTHING from the new modules
(`daemon.services.question_pause_set` /
`daemon.services.stale_pause_sweep`), so it runs against the pre-fix
source as well (no trivial `ImportError` "red"). It drives the REAL
production paths: the real `QuestionManager`, the real
`InstanceLifecycleService` pause cascade and the real shared answer
helper (`daemon/routers/answer_helper.py`).

Exit codes: `0` = no strand (GREEN), `1` = strand present (RED),
`2` = harness error.

The pytest suite `tests/unit/test_question_pause_resume_asymmetry.py`
carries the same two symptom repros as assertions (plus the 23
scenario/branch pins).

## COMMAND 1 — RED (base source)

```bash
cd /home/nea/ensemble-src-wt-qprasym
# base source in place (the new modules removed, the pre-fix daemon/ files checked out)
git checkout 1ebdf469f -- daemon/
rm -f daemon/services/question_pause_set.py daemon/services/stale_pause_sweep.py
/home/nea/ensemble-src/.venv/bin/python tests/manual/question_pause_strand_repro.py
```

Captured output (exit code **1** = STRAND PRESENT):

```
pause cascade parked: ['asker-1', 'root-1', 'side-1']
side-1 parked at   : paused
------------------------------------------------------------------------
answer status      : answered
------------------------------------------------------------------------
========================================================================
QUESTION PAUSE STRAND — symptom repro
========================================================================
side-1 (side-effect target) status : running
side-1 paused_at                   : None
root-1 (parent) status             : running
re-armed (woken) instances         : ['asker-1']
------------------------------------------------------------------------
RESULT: STRAND PRESENT — the side-effect target's interrupted turn was never re-armed; it can never report terminal, so the parent stays in waiting_children forever.
========================================================================

========================================================================
SHAPE B — late-pause race (the pause lands AFTER the answer)
========================================================================
answer status      : answer_fallback_enqueued
late pause refused : False
side-1 status      : paused
root-1 status      : paused
------------------------------------------------------------------------
RESULT: STRAND PRESENT — the late pause parked the tree after the question was consumed; the child REMAINS paused with no answering counterpart.
```

The same two shapes as pytest assertions on the base source (the
failures are ASSERTION failures — the symptom — not import errors):

```bash
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  "tests/unit/test_question_pause_resume_asymmetry.py::test_symptom_repro_side_effect_child_strands_the_parent" \
  "tests/unit/test_question_pause_resume_asymmetry.py::test_symptom_repro_late_pause_leaves_the_child_parked" \
  -q -p no:randomly
```

```
>       assert "side-1" in _woken_ids(manager), (
E       AssertionError: the side-effect target was never re-armed — its turn can never complete, so the parent stays in waiting_children forever
E       assert 'side-1' in ['asker-1']

>       assert pause_result.get("question_pause_refused") is True, (
E       AssertionError: a question pause fired after the question was consumed — the tree is parked with no answering counterpart
E       assert None is True
E        +  where None = {}.get('question_pause_refused')
E        +    where {}.get = {'paused_ids': ['root-1', 'asker-1', 'side-1'], 'skipped_ids': []}.get

2 failed, 3 warnings in 3.50s
```

Shape A is the primary strand: the tree-scoped resume flips the
side-effect target's STATUS (`running`) but never re-arms its
interrupted turn (it owns no task row, hence no suspension handle) →
it can never report terminal → the parent never leaves
`waiting_children`. The base ALSO loses the parent's parked state
(`root-1` shows `running`, not `waiting_children`) because the
tree-scoped resume cascade flattens every parked node to `running`.

Shape B is the late-pause race: the deferred question pause runs
OUTSIDE the graph task, so it can land AFTER the answer was consumed →
the whole tree is parked (`side-1` REMAINS `paused`, `root-1` REMAINS
`paused`) with no question left to answer it.

## COMMAND 2 — GREEN (HEAD source)

```bash
cd /home/nea/ensemble-src-wt-qprasym
git checkout HEAD -- daemon/
PYTEST_BIN=/home/nea/ensemble-src/.venv/bin/pytest \
  ./test/packs/question_pause_resume_asymmetry_unit_test.sh
/home/nea/ensemble-src/.venv/bin/python tests/manual/question_pause_strand_repro.py
```

Captured output (exit code **0** = NO STRAND):

```
pause cascade parked: ['asker-1', 'root-1', 'side-1']
side-1 parked at   : paused
------------------------------------------------------------------------
answer status      : answered
------------------------------------------------------------------------
========================================================================
QUESTION PAUSE STRAND — symptom repro
========================================================================
side-1 (side-effect target) status : running
side-1 paused_at                   : None
root-1 (parent) status             : waiting_children
re-armed (woken) instances         : ['asker-1', 'root-1', 'side-1']
------------------------------------------------------------------------
RESULT: NO STRAND — the side-effect target was re-armed; its turn can complete and the parent can leave waiting_children.
========================================================================

========================================================================
SHAPE B — late-pause race (the pause lands AFTER the answer)
========================================================================
answer status      : answer_fallback_enqueued
late pause refused : True
side-1 status      : running
root-1 status      : waiting_children
------------------------------------------------------------------------
RESULT: NO STRAND — the late pause was refused; the tree was not parked with no answering counterpart.
```

Deltas vs the RED run:

| observable | RED (base) | GREEN (HEAD) |
|---|---|---|
| `side-1` re-armed | NO (`['asker-1']`) | YES (`['asker-1', 'root-1', 'side-1']`) |
| `root-1` after the answer | `running` (parked state lost) | `waiting_children` (restored + WC-woken) |
| late pause | parks the tree | refused |

## COMMAND 3 — the bounded suite (GREEN side)

```bash
cd /home/nea/ensemble-src-wt-qprasym
PYTEST_BIN=/home/nea/ensemble-src/.venv/bin/pytest \
  ./test/packs/question_pause_resume_asymmetry_unit_test.sh
```

```
25 passed, 19 warnings in 23.53s
RESULT: PASS
```

The pack is hermetic: file-backed SQLite under pytest's `tmp_path`, no
network, no daemon, no ports, no DB, and it never resolves a
HOME-derived install path.

## COVERAGE MAP (25 cases)

| # | case | finding |
|---|---|---|
| 1 | `test_symptom_repro_side_effect_child_strands_the_parent` | the RED symptom (shape A) |
| 2 | `test_symptom_repro_late_pause_leaves_the_child_parked` | the RED symptom (shape B) |
| 3 | `test_scenario_a_answer_releases_side_effect_paused_child` | (a) |
| 4 | `test_recorded_paused_set_is_durable_and_scoped` | R1 durability |
| 5 | `test_recorded_set_is_deleted_after_a_clean_release` | #10 |
| 6 | `test_durable_shadow_pack_id_is_resolved` | CRITICAL #1 |
| 7 | `test_record_drop_is_loud` | CRITICAL #1 |
| 8 | `test_release_runs_even_when_the_cascade_raises` | CRITICAL #2 |
| 9 | `test_terminal_asker_releases_the_recorded_set` | #9 |
| 10 | `test_pack_id_mismatch_does_not_release_the_outstanding_set` | #9 (adjudicated) |
| 11 | `test_waiting_children_parent_is_restored_and_woken` | the WC question |
| 12 | `test_cas_loser_branch_also_releases_recorded_set` | A5 (ii) |
| 13 | `test_late_pause_after_answer_is_refused` | race pin |
| 14 | `test_recorded_target_with_paused_turn_is_resumed_by_handle` | paused-turn routing |
| 15 | `test_scenario_b_recorded_set_survives_restart` | (b1) |
| 16 | `test_scenario_b_boot_sweep_recovers_stranded_instance` | (b2) |
| 17 | `test_scenario_c_sweep_respects_the_age_bound` | (c) |
| 18 | `test_sweep_never_finalizes_an_instance` | A6 pin |
| 19 | `test_scenario_d_pending_question_protects_the_asker` | (d) |
| 20 | `test_d1_tree_scoped_protection_pins_the_predicate` | D1 pin |
| 21 | `test_d1_resolved_question_releases_the_tree` | D1 counter-pin |
| 22 | `test_sweeper_admits_instances_with_a_paused_task` | #4 |
| 23 | `test_sweeper_skips_instances_with_a_live_task` | #4 |
| 24 | `test_sweep_service_refuses_to_bridge_onto_its_own_loop` | A1 pin |
| 25 | `test_sweep_service_respects_the_kill_switch` | kill-switch |

## REGRESSION (bounded, touched areas)

```bash
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/unit/test_pause_instance_cascade.py tests/unit/test_cascade_pause_resume.py \
  tests/unit/test_pause_flow_redesign.py tests/unit/test_pause_never_dispatched_ghosts.py \
  tests/unit/test_pause_resume_root.py tests/unit/test_pause_resume_terminate_tree_fix_p1.py \
  tests/unit/test_pause_cascade_message_queue_orphan.py tests/unit/test_pause_tool_result_race.py \
  tests/unit/test_pause_tool_result_race_enqueue.py tests/unit/test_resume_flow_redesign.py \
  tests/unit/test_resume_waiting_children.py tests/unit/test_resume_convention.py \
  tests/unit/test_resume_child_notification.py tests/unit/test_child_resume.py \
  tests/unit/test_tree_aware_pause_resume.py tests/unit/test_paused_auto_resume_fallback.py \
  tests/unit/test_paused_instance_ttl.py tests/unit/test_question_graph.py \
  tests/unit/test_question_deferred_pause_callback.py \
  tests/unit/test_question_deferred_pause_edge_cases.py \
  tests/unit/test_answer_gate_resume_chain.py \
  tests/unit/test_explicit_handle_resume_report_guard.py \
  tests/unit/test_resume_message_append.py tests/unit/test_resume_router_deferred_recovery.py \
  tests/unit/test_resume_router_report_dup.py tests/unit/test_write_pause_guard.py \
  tests/unit/test_recovery_wiring_failure.py tests/unit/config -q -p no:randomly
```

```
355 passed, 28 skipped, 115 warnings in 36.21s
```

`tests/services/` + `tests/api/`: 13 failures — the PRE-EXISTING set
(`test_process_message_metrics` ×10, `test_instance_messaging_queue_routing` ×1,
`test_instance_ui_prefs_api` ×2), each reproduced on the base source.
Full-suite / regression coverage belongs to the tester lane.
