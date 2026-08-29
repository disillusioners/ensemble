# 2026-08-29 — Reconciler Wedge-Fix Deep Review (APPROVED, 0🔴 / 2🟡 / 10🟢)

Branch `feature/reconciler-wedge-fix`, range `29898ee2..79d73eb8` (23fc5e2d + 79d73eb8).
Council: governor `4c370535-67f9-417b-a6d7-b5d70c6ee89b`, 2 councilors (models agentic + coding), `code-review` skill. Both APPROVED; both 🟡 came from agentic (coding had a coverage gap, not a factual dispute — D1).

## Verdict: APPROVED — pre-merge condition: Y1+Y2 land on this branch before the backstop sees production ticks

## The two 🟡 (should-fix, on-branch pre-merge)
- **Y1 — wedge predicate missing the children gate.** `waiting_children_watchdog.py:847-871` (docstring `:829-831` promises 3-part predicate; code checks only WC + not-paused + no-live-carrier). PROCESS_REPORT carriers are created ONLY at child completion (`child_reports.py:2844-2852`) → a *healthy* WC parent waiting on still-running children has NO carrier → spurious wedge notice fires (~1/episode, cooldown-bounded, but systematic whenever first-child-completion > tick interval). Notice content is factually wrong ("zero non-terminal children") and its playbook recommends terminate-and-respawn; delivery flips WC→RUNNING (`instance_messaging.py:1535-1539`). No test covers WC + live children + no carrier. Fix: children-gate EXISTS (reuse zombie pattern `instance/repository.py:980`) + pinning test.
- **Y2 — phantom test invariant.** `constants.py:278-281` docstring cites `tests/unit/test_reconciler_wedge_fix.py::T2b` as the ALIVE membership invariant; T2b actually lives at `tests/job_queue/test_seam_invariants.py:3413` and ZERO tests reference `ALIVE_INSTANCE_STATUSES`. Fix: membership assert + corrected pointer.

## Facts corrected (supersede prior anchors)
- `claim_pending_task` does NOT exclude WAITING_CHILDREN — exclusion is paused/terminated only; the WC exclusion lives in the job-coordination guard (`_active_jobitem_with_inflight_task_sql`). A JobItem-less PROCESS_REPORT carrier IS claimable → sub-shape (c) revival correctly does NOT flip WC: commit(carrier) → notify_work → worker claims → report processing resumes the parent. Supersedes the 2026-08-27-era anchor "claim_pending_task excludes WC at task/repository.py:1605".
- Scoped-suite reality: **155 tests** (not ~145); `test_job_recovery_service.py` lives in `tests/job_queue/`, not `tests/unit/`.

## Verified clean (reuse as anchors)
- Pattern (d) linkage `get(task.work_id)`; JAFP early-continue `job is None → continue` (`job_recovery_service.py:1085-1094`); guard order evidence→guard→cancel (`:1108-1120`); T2 safety net green both sides (TERMINATED + matching work_id → CANCELLED).
- `ALIVE_INSTANCE_STATUSES` single def (`constants.py:282-288`), set {idle, running, paused, queued, waiting_children}, both consumers import the same object.
- Sync/async revival byte-identical modulo `asyncio.to_thread` (manager.py ~:7270 / ~:7898); carrier predicate pending/running ∧ PROCESS_REPORT consistent between manager and repo helpers; `session.commit()` BEFORE `notify_work()` (`:7307→:7313` / `:7932→:7937`); fresh work_id per revival; **no `set_injection` anywhere in the watchdog**; `WEDGE_SOURCE="system:watchdog:wedge"` passes the `system:*` guard (`instance_messaging.py:2308`); wedge cooldown disjoint from hang cooldown; ≤1 carrier query per WC parent per tick; api.py wiring triple-defended.
- Stale-`running` hazard resolved: skipped orphan remains claimable → bounded at-least-once duplicate execution → zombie-reaper/restart recovery; no permanent leak, no wedged parent.
- A/B method that works: `git worktree add /tmp/X 29898ee2` + copy NEW test files in + `cd /tmp/X && <main>/.venv/bin/python -m pytest` (cwd-first sys.path resolves `daemon/` from the worktree; validated by T3/T4 ImportError showing the worktree path).

## Lessons
- Memory-derived dispatch anchors can be STALE — agentic traced the live SQL and falsified my "claim_pending_task excludes WC" premise (D2). Keep supplying anchors (they accelerate) but always framed "verify against live tree"; the falsification itself explained WHY the fix works.
- Docstring-vs-code predicate drift (Y1): when a docstring enumerates an N-part predicate, diff the parts against code — the missing part was exactly the one no test covered.
- Council requests should carry the skill_feedback contract line (mirroring worker dispatch) — I omitted it this time.
