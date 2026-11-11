# Phase 6: Bounded Test Suite — rev 4

> **rev 4 — supersedes rev 3 (commit e25804554) per the approver-iter-002 amendment
> pass.** Binding source of truth: `architecture-recommendation.md` §5 (failure-path
> rulings — **Phase 6 must test every row**), §4.1a (the bounded re-arm), §4.2 (the
> 16-code vocabulary + P0–P7 + **P5a**), §4.3.1 (the verdict-publication ordering),
> §4.4 (the verdict record), §4.5 (the event drop path), §4.6 (the config wiring
> and the now-assertable boot-abort rung), §8 (the five mandated Phase-6 additions
> + the Success-Criterion-3 re-aim), §14 (the amendment ledger), §15 (the strings to
> pin char-for-char — **B18**, **B19**), §16 (the iteration-002 ledger). Where this
> file and that document disagree, **that document wins**. Rev-3 content that survives
> is carried forward with task numbers continuous (T1…T24 preserved; new work appended
> from T25).

## Objective

Prove the feature with **real process trees that genuinely balloon**, without ever
OOM-ing the test host. Demonstrate: a breach kills the whole tree and returns an
honest typed error whose **deciding value** clears the ceiling; an under-limit
command is unaffected; the kill-switch OFF is byte-identical; the config ladder
behaves per house rules (including the **now-assertable** boot-abort rung); and
**every row of the arch doc's §5 failure-path table is tested**.

The full outline (cases, harness, fencing) is in **`test-plan.md`**; this phase
carries the **execution order and the exit gates**.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Build the bounded harness** — a fixture that spawns a real ballooning process tree with an **explicitly bounded** allocator (sized in MiB, computed from the injected ceiling with a safety factor), asserts the host's available memory before spawning, and always tears the tree down. Reuse the house idioms: the autouse `_reap_spawned_subprocesses` fixture (`tests/tools/conftest.py:196-207`), the `_EnvCapture` spy (`tests/tools/test_bash_spawn_stamp.py:69-87`), the `_spawn_teardown` CM (`tests/tools/test_auto_kill_integration.py`), and the `_wait_for_pidfile` pattern | Phase 2 Task 22 | The harness refuses to spawn if the host's free memory is below the planned allocation; every spawned pid is registered for teardown; the fixture's **stat-line format** is documented and pinned |
| 2 | **Write the fence helper** applying the house hazards on EVERY spawning test — see the fencing table in `test-plan.md` | Task 1 | Every spawning test runs under the fence helper; the helper is **asserted to have applied** (not merely invoked) |
| 3 | **Breach tests (bash lane)** — a real tree that allocates past the injected ceiling ⇒ the direct child AND every grandchild are dead; the return is the envelope and the **DECIDING VALUE** (`confirmed_pss_bytes`, or `observed_peak_bytes` on a fail-open trigger) is **`>= limit_bytes`** — **not** `observed_peak_bytes >= limit_bytes` (under the ruled hybrid the RSS peak and the deciding PSS value are deliberately **distinct** quantities) | Phase 3 | All tree members dead within grace + poll window; the envelope parses; the **deciding value** clears the ceiling |
| 4 | **Breach tests (proc lane)** — the same via `proc_run`; the tree is dead and `proc_status` carries the `ram_breach:` line + flat `ram_killed: true` | Phase 4 | Same guarantees; the RAM status is distinguishable from a user stop |
| 5 | **Under-limit tests (both lanes)** — a command allocating a small amount under the ceiling completes normally: output byte-identical to the base commit, exit code 0, `exit_kind="success"`, no event, no counter increment | Phases 3, 4 | Byte-identical output against a base-commit reference; no observability side effects |
| 6 | **Kill-switch OFF tests (both lanes)** — mirroring `test_bash_spawn_stamp.py::explicit_off_byte_identical_env_no_row`: with the switch OFF, the child env carries no stamp, no record is written, no measurement occurs, and the output is byte-identical to the ON path. **Plus the no-thread-at-all case:** OFF ⇒ the guard **starts no thread** (`thread_alive` False, no thread object) and `app.state.ram_guard is None` | Phases 2, 3, 4 | Byte-identical output; zero writes; **zero threads**; zero guard reads |
| 7 | **AMENDED (approver blocker 5) — config ladder tests.** Default (**3 072 MiB / 3 GiB**), env override applies, out-of-range env **aborts boot**, the yaml block does not beat the env tier, and the per-call override wins over the config default. **The boot-abort rung is ASSERTABLE — the "untestable disclosure" escape is DELETED:** `load_config()` itself raises, so no daemon-boot harness is needed (§4.6). The assertion shapes are §15 B9, verbatim | Phase 5 Tasks 3–6, 16, 17 | Each rung behaves as specified; the boot-abort rung is asserted via `pytest.raises(ValidationError)` around `load_config`; the §15 B9 shapes are asserted as written |
| 8 | **Negative-path tests** — non-Linux platform ⇒ inert; unreadable `/proc` ⇒ the reader's fail-mode; enumeration failure ⇒ the sample is **dropped** and **no breach is evaluated** (never kill on no-evidence); a command that exits normally between samples ⇒ no false breach; a tree that shrinks ⇒ the peak stays monotonic | Phases 2, 3, 4 | Every negative path has a defined, asserted outcome; **no** false breach is ever attributed |
| 9 | **Regression sweep** — run the full `tests/` suite against the base commit and confirm **net-new reds = 0**. Specifically re-run: `tests/tools/test_bash.py`, `tests/tools/test_bash_spawn_stamp.py`, `tests/tools/test_proc_tools.py`, `tests/tools/test_auto_kill_integration.py`, `tests/unit/services/test_bash_spawn_reaper.py`, `tests/test_resource_control_seam.py` | Tasks 3–8 | Net-new reds = 0; the listed packs pass **unmodified** |
| 10 | **Mutation-prove the key assertions** — the house rule is that *"a test green under its own leg's revert is not vacuous if a COUNTER-mutation reddens it"*. Re-aim at the **positive shape**. Report the mutation counts (a count of 0 is a signal, not a flake) | Task 9 | Each key assertion has **at least one** counter-mutation that reddens it |
| 11 | **ADDITION 1 (arch §8) — dead-guard and blocked-tick cases.** Rev 1's S1–S10 covered the substrate but had **no "the thread died" case**. Test: (a) the tick body raises ⇒ the **outer restart loop** resumes it and the heartbeat advances, `missed_samples` increments, and **no** log line is emitted from the tick path; (b) the **four death modes** of §5 are exercised — **tick dies** (restart loop revives it) · **tick WEDGED** (a never-returning `/proc` read: **not recoverable, no supervisor exists**; measurement stops and `last_tick_at` frozen is the only signal — `missed_samples` does **not** climb) · **lane dies** (restart loop revives it; pending breaches resume) · **lane WEDGED** (**kills stop, measurement continues**; `lane_alive` stays True, so the signal is the **progress heartbeat `last_lane_progress_at` frozen** plus `lane_pending` climbing — **NOT `Thread.is_alive()`**); (c) the guard's death is **visible** on the read surface (`thread_alive` False, `missed_samples` climbing) | Phase 2 Task 14 | The thread **restarts**; the heartbeat is observable; the tick path stays silent; the dead-guard state is visible on `GET /api/resources/ram-guard` |
| 12 | **ADDITION 2 (arch §8) — the BOUNDED OVERCOUNTED re-arm path.** A fork-without-exec tree whose summed RSS crosses the ceiling but whose summed PSS does **not** ⇒ the confirm marks it **OVERCOUNTED**, the tree is **NOT killed**, and it re-arms at the **bounded** threshold; a further confirm requires the growth governor to be satisfied; a later breach at the re-armed threshold **does** kill | Phase 2 Task 4 | The overcounted tree survives the first trigger; the re-armed threshold is **bounded** by the 32 MiB cap and **ratchets** (never decreases); the confirm does **not** run on every tick (the 128 MiB growth governor); the second trigger kills |
| 13 | **ADDITION 3 (arch §8) — the one-shot `mark_exit` ordering AND the verdict-publication ordering.** A RAM kill where the success arm's `mark_exit` could run first ⇒ the test asserts the RAM verdict **survives** (the live row is deleted at `pid_registry.py:423`; a wrong ordering **permanently erases** the verdict). The publication ordering is asserted by a **recording fake** (not by inspection): the verdict publication is observed **before** `mark_exit` is called; the success arm's `mark_exit(exit_kind=EXIT_KIND_SUCCESS)` did **not** run; the guard-registry release happened **after** the publication | Phase 3 Task 5 | The verdict is present after the arm ordering; a deliberately inverted ordering is shown to erase it (the counter-mutation) |
| 14 | **ADDITION 4 (arch §8) — the PSS-confirm fail-open path.** The confirm **times out** (or fails) ⇒ **fail-open ⇒ kill** (the deadline is `RAM_GUARD_PSS_CONFIRM_DEADLINE_MS` = 250 ms); the trigger is recorded as `pss_confirm_unavailable`; the envelope's deciding value falls back to the RSS peak | Phase 2 Task 3 | A timeout kills the tree; the trigger value is `pss_confirm_unavailable`; the fallback deciding value is asserted |
| 15 | **ADDITION 5 (arch §8) — mutation-proof per the existing house rule** — the matrix in `test-plan.md` §4 is executed, **including the fork-tree fairness counter-mutation** | Task 10 | Every key assertion has ≥1 counter-mutation that reddens it; the counts are reported |
| 16 | **Fork-tree FAIRNESS test + counter-mutation for the 8.9× over-count claim.** Build a real fork-without-exec tree (9 members) whose summed RSS exceeds the ceiling while its summed PSS does not. Assert: the tree is **NOT killed** (the fair path is taken, not the RSS shortcut). **Counter-mutation:** break the confirm (make it always fail-open) ⇒ the same tree **IS** killed ⇒ the test reddens. This is the single most important fairness proof in the suite | Task 12 | The 9-member fork tree survives; the broken-confirm counter-mutation kills it; the measured over-count factor is recorded (expected ≈8.9×) |
| 17 | **Coincidence + three-kill-path interaction tests.** (a) A timeout races the guard ⇒ exactly one claimant owns the verdict; the loser is `coincident`; the flags (`ram_breach_observed` / `coincident_timeout`) are correct in both directions. (b) **Three-kill-path interaction:** the timeout arm, the guard arm, and the reaper must **never both signal** — a test drives all three against one tree and asserts a single delivered signal set | Phases 3, 4 | The verdict has exactly one owner; the loser is recorded `coincident`; only one kill path signals |
| 18 | **Liveness-sweep tests.** The guard's bounded non-lethal sweep: a dead pid's entry is dropped; a **live-but-unreleased** entry is **kept watching** (the safe direction); the sweep is **bounded** (a cap on entries examined) and **never signals** | Phase 2 Task 15 | The sweep drops dead entries, retains live ones, respects its bound, and emits no signal |
| 19 | **Per-tick cost measured on the SCAN-POPULATION axis.** The shared scan is O(host pids) per tick (9.49 ms median / 13.89 ms p95 at 250 pids; ≈40 ms at 1000). Measure at ≥2 populations and record; size the 200 ms interval against the **worst** host pid count | Phase 2 Task 18 | Median + p95 recorded at ≥2 populations; the interval is confirmed or amended |
| 20 | smaps_rollup stalled-read → head-of-line blocking. Test the confirm's deadline path (T14) with a stalled confirm on the execution-lane thread and assert the tick path is never blocked: last_tick_at keeps advancing at the poll cadence, the shared scan keeps running, the liveness sweep still fires, and a second breach detected during the stall is detected on schedule. Record the residual in the phase report as disclosed | Task 14 | A simulated stalled confirm does not delay a tick; the deadline fires; the tick heartbeat advances during the stall; the residual (a wedged read holds the kill path until restart) is disclosed |
| 21 | **Write the reports** — the measured per-tick cost, the measured allocation rate **R** and the overshoot law, the **re-published §3.4 table** (Phase 2 T19), the fork-tree over-count factor, the mutation counts | Tasks 9, 15–20 | All measurements are recorded with their numbers; no claim is asserted without a number behind it |
| 22 | **NEW (approver blocker 1) — the S8-MATCHING Q2 predicate test.** The test-plan's S8 case must pin the **Q2 semantics**, not the stale P5. Per predicate (the **canonical** Q2 strings, §15 B6): `the watch stays alive until the group member count reaches 0` · `identity = captured pgid ∧ surviving-member group membership` · `starttime gate applies to the leader only while alive` · `the watch is released only at group member count 0` · `group_member_count == 0`. **The specific legs, each one:** (a) leader **alive** + starttime **matches** ⇒ TRUE, the kill proceeds; (b) leader **alive** + starttime **mismatch** ⇒ `refused:pgid-recycled`; (c) leader **absent (ENOENT)** ⇒ **NOT APPLICABLE** — the identity re-anchors on the surviving group members and the kill **may proceed** on group membership (and the **watch stays alive**); (d) leader stat **unreadable** (read error / unparseable — *not* ENOENT) ⇒ `refused:leader-unreadable` (**fail-closed**), and the **watch stays alive** while only the kill is refused; (e) **P5a** — a live process holding the captured pgid with a **different** starttime ⇒ `refused:pgid-recycled` (fail-closed); (f) `group_member_count == 0` ⇒ `already-dead:group-empty` — **release, no signal** — and **only** at member count 0 (it is **not** produced merely because the leader is gone); (g) the **leaderless case produces NO reason code** | Phase 2 Tasks 10, 11 | Each leg has its own test, named per §15 B12 as `test_p<n>_<case>_<expected_status>`; the absent-vs-unreadable split is distinguished (absent ⇒ kill may proceed; unreadable ⇒ refused); a recycled pgid is refused; the leaderless case yields no reason code. **This task and test-plan S8/B13 must agree** — they are the same semantics, and a divergence is a defect |
| 23 | **NEW (approver blocker 7) — the FIXTURE SPLIT and the TEST-NAME SHAPE.** §15 B12, verbatim: `kill_fence` (the P0–P7 REFUSAL legs only) · `kill_census` (the ladder and the P8 legs) · `test_p<n>_<case>_<expected_status>`. `kill_fence` hard-raises on ANY non-zero signal (`tests/unit/services/test_bash_spawn_reaper.py:64-81`) ⇒ it can only host legs where **no signal may fire**. `kill_census` **records** `(pgid, sig)` without delivering (`:84-108`) ⇒ it hosts the **ladder + P8** legs. **P8's probe additionally needs an injectable fake `proc_root`** whose member stat lines transition alive→gone at the recorded KILL — the census alone cannot make the probe observe a dead tree | Tasks 22 | No P0–P7 refusal leg runs under a fixture that would tolerate a signal; no ladder/P8 leg runs under `kill_fence`; every new predicate test follows the name shape; the P8 probe's fake `proc_root` transition is exercised |
| 24 | **AMENDED (approver iteration 002, blocker B4) — every absence-asserting acceptance criterion carries a counter-mutation row.** The contract's rule: *every acceptance criterion that asserts an absence must carry a counter-mutation row in the §4 mutation table* — absence-only and raw-text pins are prose-satisfiable. This applies in particular to phase6 T11(b) and to test-plan S17 / O11 / K5 | Tasks 10, 15 | The mutation table in `test-plan.md` §4 carries a row for **every** absence-asserting acceptance criterion in the plan set; the audit is enumerated in the phase report (criterion → mutation row); the four rows added at iteration 002 cover **S17** (the substrate's no-`/proc`-reads rule), **O11** (the tick-path silence), **K5** (zero DB queries) and **phase6 T11(b)** (the blocked-tick case) |

## Coupling

- **Tight with:** Phases 2, 3, 4, 5 (the tests pin the envelope, the deciding-value
  semantics, the exit-kind vocabulary, the knob names, the counter set and the
  event/counter names).
- **Independent of:** Phase 7 (which can run in parallel — arch §8 item 5).

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **The test OOMs the host** (the suite's own failure mode is the incident it tests) | **High** | Medium | The ceiling is **injected and small** (MiB-scale, never the shipped 3 GiB default); the allocator size is **explicitly bounded in the test source**; the harness checks free memory before spawning; the teardown always runs |
| A test leaks a live ballooner into the next test | High | Medium | The autouse reaper fixture plus the harness's own registration; the `_spawn_teardown` idiom |
| The suite touches the **live** daemon or its `/proc` | High | Low | The fence helper; the reader's injectable `proc_root` for every unit-level read |
| The suite connects to a **production database** | High | Low | Per-variable `POSTGRES_*` scrub — `env -u POSTGRES_*` is a **silent NO-OP**; plus the `data/ensemble.json` postgres pin (see the fence table in `test-plan.md`) |
| The boot sweep kills the test's own processes | Medium | Medium | Pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **explicitly** — `env -u` on it strips the ambient guard |
| The auth env leaks and produces phantom reds | Medium | Medium | Scrub the **full** `ENSEMBLE_AUTH_*` family **or none** — a partial scrub also breaks collection |
| A vacuous test passes while the feature is broken | **High** | Medium | Task 10's counter-mutation proof + T15's matrix + T16's fairness counter-mutation + **T24's absence-assertion audit**; assert the **positive** shape |
| The boot-abort rung is skipped because it "needs a daemon harness" | Medium | Low | Task 7 — the rung is assertable today via `pytest.raises(ValidationError)` around `load_config`; the untestable-disclosure escape is **deleted** |
| **A refusal leg runs under `kill_fence` and the ladder runs under `kill_census` (or worse, the ladder under `kill_fence`)** | Medium | Medium | Task 23 — the split is explicit and asserted; `kill_fence` hard-raises on the ladder's own TERM, so the split is not a style rule but a fixture constraint |
| The S8 case and the phase-6 Q2 task drift apart | Medium | Medium | Task 22 states that they are the same semantics and that a divergence is a defect; both are pinned by the same seven legs |

## Exit Criterion

- Real ballooning trees are killed **tree-wide** in both lanes, with the **deciding
  value** clearing the ceiling.
- Under-limit commands are **byte-identical** to the base commit.
- Kill-switch OFF is **byte-identical**, side-effect-free, and starts **no thread**.
- The config ladder behaves per house rules, including the **boot-abort** rung
  (asserted via `pytest.raises(ValidationError)` — no untestable disclosure).
- **All five §8 additions** are implemented and tested: dead-guard/blocked-tick ·
  **bounded** OVERCOUNTED re-arm · one-shot `mark_exit` + verdict-publication
  ordering · PSS-confirm fail-open · mutation-proof.
- The **S8-matching Q2 predicate test** exists with all seven legs, and agrees with
  test-plan S8/B13.
- The **fork-tree fairness** test exists **with** its counter-mutation.
- The **coincidence** and **three-kill-path** interaction tests exist.
- The **liveness-sweep** tests exist.
- The **event-drop** path is tested (no live loop ⇒ dropped + counted + logged).
- The **fixture split** and the **test-name shape** are enforced.
- Every negative path has an asserted outcome; **no** false breach is attributed.
- **Net-new reds = 0** against the base commit; the named existing packs pass
  **unmodified**.
- The key assertions are **mutation-proven** — each has ≥1 counter-mutation that
  reddens it — and every **absence-asserting** acceptance criterion has a row.
- **The host survived** the suite with its memory intact — an explicit check.
