# Delta Review — slash-commands defect-fix commits (2026-08-31)

**Target:** `feature/slash-commands` @ f9d377b9 — BE 85ba2250 (#1), 04139030 (#3), 795bac07 (#4), f9d377b9 (#2+#7); FE 34c08746 (#5)
**Mode:** 🔴 Deep-Review council (governor 47e7de14, 2 councilors: worker/agentic + worker/coding, skill `code-review`)
**Verdict:** ✅ APPROVED — 0 🔴 / 2 🟡 / 4 🟢. BE 257/257 (254+3 lifecycle), FE 110/110 — dual-model independent runs, identical counts.

## Confirmed
- #1 resume-in-finally wraps `execution_gate.run` only; all exit paths covered; no double-resume (idempotent `resume_instance_cascade` skips non-PAUSED; pause-fail paths resume-and-return before gate). `execution_gate.py` byte-untouched.
- #3 `list(ring.values())` before `reversed()`; eviction semantics unchanged; deterministic repro (back-dated `last_event_at`, no sleeps).
- #4 "BE structurally single-write" CONFIRMED at router seam (one write sink per branch); 6 forward-guard tests are real pins. Client-side double-POST hypothesis still unrefuted → netlog at next e2e gate.
- #2+#7 terminal gate before `record_start` (no command_id minted); §7 `state:"rejected"` envelope (with `command_id:null` vs stale §7 `string` type — F5); executor guard retained; `waiting` SSE hoisted (test-pinned `waiting_idx < pause_idx && < checkpoint_idx`); ack non-blocking.
- #5 escape-form match, retry_of_message_id gating, queue_id stash/forward, merge/evict preserve failed entries, a11y `role=alert aria-live=assertive`; 13 behavioral specs.

## Findings carried forward
- **F1 🟡 FE escape-retry hazard** (`chat.component.ts:1757`): retry of a fail-marked escape bubble re-POSTs the echo-**stripped** content → BE parses real `/compact`. Fix: stash raw sent content at fail-mark (`retry_content`). Highest-value follow-up.
- **F2 🟡 dropped-jobs pin is simulation-coupled** (lifecycle test mirrors `claim_pending_task`, not real repo SQL at `task/repository.py:844-914`); tester's live "1 of 8 processed, pending_count 0" not reproduced — mechanism inferred. Fix: netlog/status-diff on recurrence.
- F3-F6 🟢: cancel-safety docstring + injection test; stale line-number comments; §7 type `command_id: string | null`; FIFO assertion/fixture hygiene.

## Lessons
- Councilor disagreement handled by adopting the *stricter* label when one analysis is strictly deeper (coding didn't examine retry content for stripped bubbles; agentic did) — label divergence ≠ factual contradiction.
- "Structurally pinned" claims need a residual scope caveat: structural BE pins cannot rule out client-side duplicates (same-µs datum class).
- RED→GREEN claims verified by asking: *would this assert fail if the fix were reverted?* — await-count == 1 on the previously-broken path is a strong pin form.


## Addendum (2026-08-31, later): N1 mapping fix micro-delta 5c4fa98a — APPROVED
Standard review (no council — single mapping-function fix, no trigger). 0🔴/0🟡/4🟢. Confirmed: mapping total-by-construction (failed reachable only via `fk=="error"` branch, executor.py:1347; default → success + `unknown_compaction_type=True` diagnostic); engine file zero-diff; N1 literal pinned via real `CompactionResult` (would fail under old pinned-"summary" mapping); 63/63 executor + 76/76 dispatcher green, +22 verified by collect-only. Engine `compaction_type` vocabulary = 5 live values (`summarization`/`partial_summary`/`truncation`/`emergency_truncation` at compaction.py:875/900/920/933/953/966; `chunked_summarization` doc-only) — all converge at `CompactionResult` assembly :995-1003. Note: FE commit 9eb1b67e (F1 retry_content stash from F1 finding above) landed at 9eb1b67e — not yet reviewed.