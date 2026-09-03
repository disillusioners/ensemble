# 2026-09-03 — Mission M3 Deep-Review (feature/mission-class @ 798c1f4c)

**Verdict:** SHIP (unanimous 2/2 councilors, models agentic + coding). `rename-complete` = **N** narrowly (adjudicated). Follow-up ledger #1–#7 open.

## Lessons

1. **Token-rename sweeps undercount rendering surfaces.** The specced "4 read surfaces + SSE" was completed correctly, but a 5th surface and two FE companions were missed: event-text formatter (`work_notifier.py:311-327` — mirror terminals still print "completed"), FE SSE-patch handler (`jobs.component.ts:649` — `completed_at` stamped only on `'completed'|'failed'`, so 'settled' mirrors lose timestamps), FE filter dropdown (`jobs.component.ts:308-316` — no 'settled' option). For future renames, enumerate: API read surfaces → SSE payloads → event/notify TEXT formatters → FE model layer → FE filter affordances → FE timestamp/patch handlers.
2. **Pure-await pattern that verified sound:** `await_mission` = bounded asyncio poll loop re-resolving each cycle (`missions.py:675-762`) — no watcher/task/row registration → F7 epoch-safe by construction; timeout returns snapshot-as-data. Verification checklist that worked: deadline math (`loop.time() + timeout`, `sleep(min(poll_interval, remaining))`), error-retry sleeps BEFORE `continue`, poll-interval clamp (≤0 → 2.0s), mid-poll mission-disappearance handling.
3. **Council disagreement adjudication rule:** a coverage difference is not a factual contradiction. On "NO surface does X" invariants, the councilor that examined more surfaces wins the boolean — even when both councilors ship.


# Cycle 2 — verification @ 68202403 (same day)

**Verdict:** SHIP (2/2 after refinement). 7/9 items RESOLVED; V2 PARTIAL (hot path), V8 impl-resolved/pin-gap. New ledger: 0🔴/5🟡 (N1,N2,N3,N6,N8)/3🟢.

## Lessons (cycle 2)

1. **Vocabulary fixes must enumerate the STRING PRODUCERS, not the flagged site.** #1 was "fixed" on 3 sites (reconcile sweeps, watch-registration) yet the PRIMARY event path (`task_processor.py:870` + `job_feedback_observer.py:1829-1881`) still passes kind-blind `'completed'` literals — pins covered the synthetic invocation, not the hot path. Future rename/render reviews: grep for every producer of the rendered token; require pins that drive the production path.
2. **"Atomic claim" must be verified for ORDERING, not just existence.** `claim_watchers_for_job` is a true CAS (`DELETE…RETURNING`, watcher_repository.py:197-260) — but notify precedes claim (read :230/:240 → enqueue :337 → claim :354-390), so two concurrent evaluations can both deliver: at-least-once, bounded ≤2. Check call order around the CAS, and whether docstrings still describe the intended (claim-first) order.
3. **Datum-level pins ≠ execution-level pins.** The A6 OR-combine is a verified root-cause fix, but the per-kind SQL filter layer runs zero SQL-execution tests with mixed row kinds (N3). A correct implementation with an unpinned SQL layer is a 🟡, not a pass.
4. **Refinement rounds work:** round-0 SHIP/BLOCK split on V2 resolved by one targeted re-examination — both councilors withdrew their extreme labels (dead-code / fully-resolved) toward the converged middle. Persist disagreement; re-dispatch the specific fact, not the whole review.
