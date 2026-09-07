# 2026-09-06 — Defer Self-Witness + Mission-Aware Cleanup Deep-Review (fix/defer-self-witness-and-cleanup @ 4e852775)

**Verdict:** SHIP (0🔴 / 3🟡 / 11🟢). Explicit lines: carve-out-dialect-safe **Y**, folding-agrees **Y**, destructive-guards-real **SPLIT→Y-with-mandatory-followups**, display==gate-survives **Y**, census **Y** (23/1/0 verified 3 ways). Both dev-internal RED→GREEN closures independently reproduced (logging pin RED at base via `git archive` /tmp extraction; Gate-B corner RED at 2f79417b).

**Open follow-up ledger:** W1+W2 one ticket — `force_complete_defer_holder` probe→terminate TOCTOU window + job-side-only probe (no `task.status` arm, no live-children anti-join; both exist in bulk scan `instance/repository.py:1549-1577`), holder-action live-children pin missing, "race-proof" docstring reword mandatory (`jobs_management.py` ~:819). W3 — PG-parity execution leg for carve-out bodies unexecuted. Docs batch S1/S2/S3/S7/S9; S10 witness-bodies annotation+pin; S11 abort-leg pin.

## Lessons
1. **Claim-wording adjudication:** "race-proof" parentheticals split verdicts — the literal adversarial test (display→confirm race) PASSES via execution-time re-derivation, but the probe→terminate window + probe scope gap make the wording false. Verdict lines must separate "guard exists and is pinned" from "claim as worded".
2. **Dispatch phrasing vs implementation:** my brief said a stalled-by-self candidate "never appears as its own blocker" — the implementation deliberately SHOWS stalled holders (`kind=stalled`, the actionable signal). Coherent and pinned, but different from the phrasing; surface phrasing drift explicitly instead of forcing a bare Y/N.
3. **Unexecuted PG-parity leg is a recurring incident-gap class** (2nd occurrence in this family — 693a4ffc shipped through the same gap). Static-safety-by-construction + extended static guard is acceptable closure ONLY when ledgered as a named residual.
4. **RED reproduction via `git archive` into /tmp** (councilor-level) is cheap, high-value independent closure evidence for "RED→GREEN" claims.
5. Councilor `skill_feedback` execution is unverifiable from the governor vantage (no tool transcripts relayed) — metrics gap, not a review gap.


# Round 2 — ship-condition fast-verify @ d870fff2 (2026-09-06)

**Mode:** Standard, 3 workers × `code-review`. **Verdict: BLOCK — new 🔴 only** (W1/W2/W3 ship conditions all genuinely closed; round-1 ledger items W3/S5/S10 also closed).

## Per-condition closure
- **W1 CLOSED** — re-check sits immediately pre-terminate (`job_queue_service.py:1589`, between initial probe `:1564` and terminate `:1607`); busy-path returns `{"terminated": False, "probe_busy": True}` with `terminate_instance` never awaited; docstring now reads "NOT race-proof end-to-end" (truthful residual window); pins 21a/21b RED at base 4e852775 with the exact symptoms (`terminated=True`; `call_count=0`).
- **W2 CLOSED** — `has_live_work` (`instance/repository.py:1669-1764`) composes from the SAME 3 zombie-scan CSV class constants (`:1538-1546`) — reuse by construction, no re-typed SQL; single-SELECT read-only, `SQLAlchemyError` propagated for fail-CLOSED; pins 21c/21d real-DB refusals (live-Task-no-JobItem; live-child) RED at base; 21e is a real-DB structural-inverse pin binding `has_live_work ↔ find_zombie_instances` on one fixture.
- **W3 CLOSED (better than ledger)** — 4-row carve-out matrix genuinely executed on real PG (`ensemble_test`, 0.25s, behavioral via real `JobRepository.has_active_non_deferred_work(..., requester_instance_id=...)`, engine-by-engine + parity + type assertions); skip is LOUD (two tagged paths, URL + remedy, engine disposed). `1,0` int-bind breakage in ORIGINAL helpers: PRE-EXISTING CONFIRMED by independent base worktree repro (same `DatatypeMismatch`, zero diff hunks on helper bodies in round).
- Round-1 🟢s closed in-round: S5 dead asyncio.coroutine removed (correct); S10 witness-body reserved-annotation + byte-derivation pin added.

## NEW findings (round-introduced)
- **🔴 (convergent 2/3 workers, different entry points)** — preflight reads `manager._defer_block_resolver` (`jobs_management.py:675`) but NO production code ever assigns it; `api.py:977-978` wires only the `queues.py:563` module-global → `defer_blocked_count` silently always 0; masked by MagicMock attribute-set (`test_jobs_cleanup_endpoint.py:1063-1069`). Pre-r2 the path reached `manager._job_queue_service._repository.engine` (wired) — a regression, not a pre-existing gap.
- **🟡** cycle docstring documents WRONG edges (real cycle traced: jobs_management → defer_block_resolver → routers.schemas → routers/__init__ → queues.py:32 → recursion); **🟡** `defer_resolver._job_repo.engine` private reach-through; **🟡** no FE/docs cross-surface pin for the canonical sentence (BE-pinned only; docs has lowercase "every" mid-sentence); **🟡** boolean-bind follow-up unledgered (test-docstring-only mention).
- **#7 adjudication:** cycle REAL (traceback-verified); deferred import is the canonical break; implementation REJECTED as-shipped pending the 🔴 re-wire.

## Lessons (round 2)
1. **MagicMock attribute-set launders dead wiring** — a unit test that manually sets `manager._attr` on a MagicMock hides that production never wires the attribute. Any router read of a manager attribute needs (a) lifespan wiring AND (b) an integration test through real startup wiring. Facade-forwarding discipline extends to attribute READS, not just method kwargs.
2. **Severity adjudication on convergence:** same finding graded 🟡 and 🔴 by different workers — dedup to highest severity when it's a production regression in a destructive-flow preflight + test-masked; keep the lower-severity worker's contribution (regression framing: pre-round path worked).
3. **A real cycle can be documented with wrong edges** — verify the docstring's claimed import cycle against an actual traceback; "circular import" must never stand as unfalsifiable justification.
4. T-H2-style external ledger ids (caller's own tracker) are unverifiable in-repo — resolve the underlying artifact (here: `cleanupDeferNote` present + truthful) and flag the id as phantom rather than failing the item.


# Round 3 — unblock spot-verify @ 9c48e750 (2026-09-06)

**Mode:** Standard, 1 worker × `code-review` (spot-verify per round-2 prescription). **Verdict: CLEARED FOR TESTER GATE** — 🔴 closed end-to-end, item-11 sound; 4 🟡 doc-truth items carried as pre-merge batch (non-behavioral).

## Closures verified
- **🔴 CLOSED** — preflight consumes canonical singleton: `api.py:977-978` (lifespan `set_defer_block_resolver`) → `queues.py:563-596` module-global → `jobs_management.py:794-803` deferred-import `get_defer_block_resolver()` → `resolver.defer_pending_count()` (public instance method, zero private reach). Dead `manager._defer_block_resolver` read gone (zero runtime refs). test_27 real-wiring integration pin: RED at base d870fff2 (`0 == 2`, the exact dead-wiring symptom) → GREEN at HEAD (`2 == 2`); MagicMock hand-set eliminated; 27b inverse pin = regression-shape documentation (not a differential — vacuous at base, correctly graded).
- **Item 11 SOUND** — `has_real_active_or_queued_work` (`instance/repository.py:1766-1825`): narrow JobItem-only arm, reuses `_LIVE_JOBITEM_STATES_FOR_ZOMBIE_SCAN` (no drift), mirror protection via `job_type != 'message'`, fail-CLOSED. Truth table verified: excludes cancellable (ACTIVE/queued non-mirror), includes protected mirrors + Task-only + child-only. The predicate's narrowness IS its correctness.
- **AST caller guard non-tautological** — proven by violator injection (pin fails with exact file:line). Cycle docstring now matches the traced chain. Round-2 residuals closed: boolean-bind ledger (`CHANGELOG.md:69-80`), FE canonical-sentence pin (`cleanup-preflight.model.spec.ts:42-46`).

## Carried 🟡 (doc-truth batch, pre-merge — behavioral QC is the tester's; doc-truth is this batch's)
1. `CLEANUP_TRUTH_SURVIVOR_NOTE` exported + spec'd but NEVER rendered in the dialog (docstring at `cleanup-preflight.model.ts:51-66` promises the dialog surfaces it) — render it or downgrade the claim.
2-4. Stale `has_live_work` references where code calls `has_real_active_or_queued_work`: `jobs_management.py:658-660` (load-bearing ITEM-11 docstring), `test_jobs_cleanup_endpoint.py:1180-1185`, `test_nuclear_cleanup_bucket5.py:1776` (+misleading "W2 companion" provenance). 🟢 #15: contradictory survivor prose `jobs_management.py:708-718`; 🟢 #16: child-only shape unpinned in test_28.

## Lessons (round 3)
1. **Predicate-swap string-producer sweep** (M3 lesson recurring): when a folded item swaps the predicate (`has_live_work` → `has_real_active_or_queued_work`), grep every docstring/test-banner/section-header naming the OLD predicate — 3 stale refs shipped in one round.
2. **Spec-asserts-shape-not-consumption blindness**: a const can be exported, substring-spec'd, and still never rendered — specs must assert the RENDERING (template consumption), not just the symbol's existence.
3. **Inverse pins can be vacuous at base** — when the base bug produces the inverse condition via a different mechanism (dead path returns 0), the inverse pin passes at base too; grade it as documentation, not differential proof.
