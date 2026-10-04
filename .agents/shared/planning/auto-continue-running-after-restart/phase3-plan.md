# Phase 3: Coexistence ordering + pinning (AC4)

## Objective

Prove — with an executable pinning test plus a negative lock-out test — the exact interleaving between the pending_wakes sweep and the new continue pass when BOTH target the same instance (an arming instance that was mid-turn RUNNING at daemon death). The invariant: continue-in-place keeps the orphan Task `status='running'` → the claim-guard (`repository.py:2230-2294`) blocks the wake's PENDING claim → the wake lands FIFO-behind the continued turn (arm-notify UX: outcome report AFTER the turn finishes). Terminalize-early (Interleaving Y) must be structurally impossible in shipped code.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.1 | Write the ordering-contract docstring into `auto_continue_boot_pass.py` (module level, concise): the three conditions that make double-fire structurally impossible — (1) continue-in-place, never force-cancel the orphan; (2) placement AFTER `sweep_wake_records` so the wake's PENDING row exists before the resume is scheduled; (3) per-instance isolation. Cross-reference `investigation.md` §C10 and the claim-guard anti-starvation comment (`repository.py:2284-2291`) | P2 | Docstring present; grep-verifiable; reviewed against Interleaving X steps in technical-analysis §C |
| 3.2 | Implement `tests/unit/services/test_auto_continue_interleaving.py::test_boot_continue_x_wake_sweep_interleaving_does_not_double_fire` (the analysis §C test, made executable): fixture instance `status='running'` + orphan `process_message` Task `status='running'` + a pending_wake journal record for the SAME instance → run the boot sequence slices in order: (a) `sweep_wake_records` (real service, temp install_dir journal) → assert wake Task row exists PENDING + orphan still RUNNING + instance status unchanged; (b) run `continue_running_instances_after_restart` → assert resume scheduled for the ORPHAN's work_id (not the wake's), CAS stamped on the orphan, wake Task STILL PENDING; (c) complete the orphan turn (deterministic LLM stub) → orphan terminal → (d) assert the wake Task is NOW claimable (`claim_pending_task` returns it — guard unblocked). Assert log contract: exactly one `[BOOT_CONTINUE]` for the instance, one wake delivery, zero `already_resuming` | P2 (placement), P1 (CAS) | Test green; the four ordering assertions each fail if the boot pass were moved before the wake sweep (mutation-check by temporarily reordering in a scratch run) |
| 3.3 | Negative lock-out test `::test_terminalize_early_opens_claim_window_is_rejected`: simulate Interleaving Y — orphan force-cancelled (CANCELLED) BEFORE the wake claims → assert the wake Task becomes claimable IMMEDIATELY (demonstrating the double-turn window) and assert that NO shipped code path does this: grep-guard structural test that `auto_continue_boot_pass.py` contains no `force_cancel`, no `find_stale_running_tasks`, no task-status writes (AST or source-scan assertion, mirroring the structural-pin style of `post_restart_arm_notify` structural tests) | 3.2 | Negative test documents the rejected alternative AND structurally pins its absence (fails if someone later adds a reaper to the pass) |
| 3.4 | Carve-out pinning tests (AC1 edge): `::test_paused_instance_never_selected` (PAUSED instance + RUNNING task → zero candidates; PAUSED task for RUNNING instance → not selected via instance-subquery); `::test_terminal_instance_never_touched` (TERMINATED/COMPLETED/ERROR/FAILED instance rows + orphan RUNNING task → zero candidates, zero repo writes, task row untouched); `::test_waiting_children_skipped_bus_owned` (WC instance → zero candidates; plus a comment-level assertion that `bus.start()` recovery covers both orderings — integration-level WC wake proof lives in P5 E2E) | P1 | Tests green; PAUSED test documents the pause-lane boundary ("PAUSED is the resume_instance_cascade lane — this feature must NOT touch them") |
| 3.5 | Boot-order placement pin (structural): source-scan test asserting `api.py` contains the `continue_running_instances_after_restart` call BETWEEN the `sweep_wake_records()` await and the `upgrade_journal_sweep.start()` line (line-order assertion on the source text — same technique as the wake-sweep sweep-method structural pin T6.6). This is the test that fails if a future edit reorders the boot sequence | 2.6 | Test green; deliberately fails on a scratch reorder (verify once manually, then revert) |

## Coupling

- **Tight with P2** — tests consume the real placement, real pass, real claim-guard. Any P2 ordering change re-runs this phase.
- **Tight with P1** — candidate-selection semantics are asserted (which rows qualify).
- **Feeds P4** — these tests are wrapped into `test/packs/auto_continue_interleaving_unit_test.sh` (SPEC in test-strategy.md).
- **Independent of P5** — the E2E re-proves the happy path on a real daemon but does not replace the executable pins.

## Risks

- **Test determinism**: the interleaving test spans two async subsystems (wake sweep journal I/O + the pass). Mitigation: temp-dir journal + deterministic LLM stub + `asyncio` race-free sequencing (await each boot slice to completion before assertions); no sleeps.
- **Claim-guard surface drift**: `claim_pending_task` internals are load-bearing for the test; anchors drift (repo trap #3 — `claim_pending_task` lives at `task/repository.py:1733+` with the guard at `:2230-2294` at cf8efbef). Mitigation: import + call the public method, never copy SQL into tests; grep-verify any cited line at use time.
- **Structural-pin brittleness**: source-scan assertions break on formatting changes. Mitigation: assert on stable tokens (function names, `await` targets), not line numbers, except the 3.5 ordering pin which is deliberately order-sensitive.

## Verification Steps

1. `.venv/bin/pytest tests/unit/services/test_auto_continue_interleaving.py -q` → all green.
2. Mutation checks (run once each in a scratch worktree, then revert): (a) move the pass call before the wake sweep → 3.2 and/or 3.5 fails; (b) add a `force_cancel` call to the pass → 3.3 structural test fails.
3. Re-read `investigation.md` §C10 + `technical-analysis.md` §C and confirm every Interleaving-X step (1-7) has a corresponding assertion; document the mapping in the test module docstring.
4. Read-back verification of the new test file (repo trap #2 — single big file: re-read after write).

## AC Mapping

- **AC4** — this entire phase; 3.2 is THE pinning test for the exact interleaving.
- **AC1** — 3.4 carve-outs (PAUSED parked, terminal untouched, WC bus-owned).
- **AC5** — 3.2's no-double-delivery assertions complement P2's reboot-loop tests.
- **AC7** — tests land in repo conventions (pytest, `tests/unit/services/`), packed in P4.

## Exit Criterion

All 3.2-3.5 tests green + mutation checks performed once + the ordering contract documented in the module docstring. The AC4 property is now executable-pinned; only the real-environment proof (P5) remains.

## Rollback Note

Delete the test file; no production code depends on it. The ordering contract docstring (3.1) is documentation-only. If a future refactor legitimately reorders boot steps, 3.5 must be updated IN THE SAME COMMIT with a new interleaving analysis (note this in the test's docstring).
