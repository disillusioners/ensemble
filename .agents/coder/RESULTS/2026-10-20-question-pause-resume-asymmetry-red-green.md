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
39 passed, 31 warnings in 37.11s
RESULT: PASS
```

(The pack now carries ``--timeout=60`` — the C1 stratum is a DEADLOCK,
not an assertion failure; without a hard per-test bound a regression
deadlock would hang the pack instead of failing it.)

(cycle 3 added three discriminator cases — see the coverage map below;
the RED and GREEN repro outputs are UNCHANGED by cycle 3: the base run
still exits 1 with the identical captured text, and the HEAD run still
exits 0 with the identical captured text — re-verified after the
discriminator landed.)

The pack is hermetic: file-backed SQLite under pytest's `tmp_path`, no
network, no daemon, no ports, no DB, and it never resolves a
HOME-derived install path.

## COVERAGE MAP (39 cases)

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
| 26 | `test_deliberate_pause_with_paused_task_is_not_swept` | **T1** (R1 discriminator) |
| 27 | `test_idle_deliberate_pause_without_task_row_is_not_swept` | **T2** (R1 discriminator) |
| 28 | `test_deliberate_pause_clears_a_stale_question_marker` | R1 marker lifecycle |
| 29 | `test_n1_marker_work_happens_once_per_affected_node` | N1 (marker op count) |
| 30 | `test_n1_question_pause_stamps_the_marker_once_per_parked_node` | N1 (question half) |
| 31 | `test_n5_already_paused_node_loses_a_stale_question_marker` | N5 (already-paused branch) |
| 32 | `test_n3_later_deliberate_pause_wins_over_the_recorded_release` | N3 (precedence) |
| 33 | `test_n3_question_stratum_is_still_released` | N3 counter-pin |
| 34 | `test_c1_concurrent_record_merges_both_complete` | **C1** (no lock across await) |
| 35 | `test_council_i_deliberate_pause_over_parked_node_is_not_rearmed` | **council (i)** |
| 36 | `test_council_ii_terminated_asker_stale_shadow_does_not_block_sweep` | **council (ii)** |
| 37 | `test_w1_lost_status_flip_is_an_error_and_keeps_the_record` | **W1** |
| 38 | `test_w3_duplicate_answer_does_not_re_flip_or_re_wake` | **W3** |
| 39 | `test_dismiss_path_deliberate_pause_survives_the_release` | **rider** (dismiss exclusion) |

## HONEST PIN QUALITY (N6) — what the pre-fix reds really are

Measured, not asserted:

- On the pre-fix BASE (`1ebdf469f`) the suite reports **36 failed / 2
  passed**. The failures are dominated by
  `ModuleNotFoundError: No module named 'daemon.services.stale_pause_sweep'`
  (and the same for `question_pause_set`) — the FEATURE did not exist
  pre-fix, so those are **trivial reds**, NOT symptom reds.
- The only TRUE symptom-level pre-fix red is the standalone repro
  (`tests/manual/question_pause_strand_repro.py`, captured above): the
  base run exits **1** with the strand visible (the side-effect target
  never re-armed; the late pause parks the tree with no answering
  counterpart). That is the RED the fix flips.
- So: of the cases above, the pins that are *symptom* reds against the
  pre-fix source are the repro's two shapes plus the cases listed in
  the next section; the rest pin behaviour that did not exist before
  and therefore fail pre-fix with an import error.

## GATE CYCLE 1 — the new cases and their SEMANTIC pre-fix RED

Command (the pre-fix tip's `daemon/` checked out, the tests kept):

```bash
cd /home/nea/ensemble-src-wt-qprasym
git checkout 86fe0e03a -- daemon/
timeout 400 /home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/unit/test_question_pause_resume_asymmetry.py -q -p no:randomly \
  --timeout=30 -k "council_i or council_ii or w1_lost or w3_duplicate"
```

Captured output (4 failed, 34 deselected, 3 warnings in 6.23s):

```
E       AssertionError: a deliberate pause must survive the answer — the tree-scoped resume must not flip it back to running
E       assert 'running' == 'paused'
E         - paused
E         + running

E       AssertionError: a dead asker's stale pack shadow must not protect the tree forever — the stranded instance must be swept
E       assert 'stranded-2' in []

E       AssertionError: a lost status flip must be reported as an error (got 'wake_scheduled')
E       assert 'wake_scheduled' == 'error'
E         - error
E         + wake_scheduled

E       AssertionError: the record must carry the first pass's outcome for the parent (outcomes={})
E       assert None in ('wake_scheduled', 'wc_parked', 'unpaused')
E        +  where None = {}.get

FAILED tests/unit/test_question_pause_resume_asymmetry.py::test_council_i_deliberate_pause_over_parked_node_is_not_rearmed
FAILED tests/unit/test_question_pause_resume_asymmetry.py::test_council_ii_terminated_asker_stale_shadow_does_not_block_sweep
FAILED tests/unit/test_question_pause_resume_asymmetry.py::test_w1_lost_status_flip_is_an_error_and_keeps_the_record
FAILED tests/unit/test_question_pause_resume_asymmetry.py::test_w3_duplicate_answer_does_not_re_flip_or_re_wake
4 failed, 34 deselected, 3 warnings in 6.23s
```

The C1 pin is a DEADLOCK on the pre-fix source — the run does not
finish, it is killed by the per-test timeout. Captured traceback (the
second contender blocks the event loop inside the lock):

```
File ".../tests/unit/test_question_pause_resume_asymmetry.py", line 522, in _merge
    return await record_question_paused_set(
File ".../daemon/services/question_pause_set.py", line 322, in record_question_paused_set
    with _MERGE_LOCK:
+++++++++++++++++++++++++++++++++++ Timeout ++++++++++++++++++++++++++++++++++++
```

(the same command with `-k c1_concurrent` and `--timeout=30`; the
timeout is what makes the deadlock OBSERVABLE instead of hanging the
run forever).

The GREEN side of the same cases (at the gate-cycle-1 tip): the pack
reports **38 passed** and the standalone repro exits **0** with the
unchanged captured output.

### The rider's dismiss-path pin — its RED

Command (the pre-rider tip's `daemon/` checked out, the tests kept):

```bash
cd /home/nea/ensemble-src-wt-qprasym
git checkout 9f8618546 -- daemon/
timeout 300 /home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/unit/test_question_pause_resume_asymmetry.py -q -p no:randomly \
  --timeout=60 -k "dismiss_path_deliberate"
```

Captured output (1 failed, 38 deselected, 2 warnings in 3.51s):

```
E       AssertionError: the dismiss path flipped a deliberately parked instance back to running — the exclusion hole
E       assert 'running' == 'paused'
E         - paused
E         + running
FAILED tests/unit/test_question_pause_resume_asymmetry.py::test_dismiss_path_deliberate_pause_survives_the_release
1 failed, 38 deselected, 2 warnings in 3.51s
```

At the rider tip the pack reports **39 passed** (38 + this pin) and the
standalone repro exits **0** with the unchanged captured output.

## THE R1 PAUSE-CAUSE DISCRIMINATOR (review cycle 3)

The backstop may only revert a pause that was caused by a QUESTION.
Two independent, schema-free discriminators:

- **B (primary, both strata)** — a pause-cause MARKER on each parked
  instance's OWN row: `instances.metadata['question_paused'] =
  {pack_id, paused_at}`. The question pause cascade stamps it on every
  node it parks; every OTHER pause reason CLEARS it (a deliberate
  pause must never inherit a stale question marker — including on nodes
  that were already parked when the cascade ran).
- **A (independent second guard, task-bearing stratum)** — the
  candidate's PAUSED task must carry a question-cascade
  `suspension_reason` (`awaiting_answer` for the asker,
  `paused_by_parent` for the cascade artifacts). A deliberate pause's
  task carries `paused_external` / `watchover_setup`.

Why both: A alone cannot protect the task-less stratum (an idle
instance deliberately paused with no task row); B alone rests on a
metadata write. Together, a marker-write failure still cannot expose a
deliberate pause, and a stale marker still cannot expose a deliberate
pause with a non-question task reason.

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

Re-verified after cycle 3 (the bounded pause/resume/question subset):

```
122 passed, 17 skipped, 32 warnings in 13.05s
```

`tests/services/` + `tests/api/`: 13 failures — the PRE-EXISTING set
(`test_process_message_metrics` ×10, `test_instance_messaging_queue_routing` ×1,
`test_instance_ui_prefs_api` ×2), each reproduced on the base source.
Full-suite / regression coverage belongs to the tester lane.
