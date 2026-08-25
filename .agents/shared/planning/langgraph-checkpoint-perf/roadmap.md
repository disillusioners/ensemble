# Roadmap: LangGraph Checkpoint / Message Persistence Performance

Date: 2026-08-25
Author: planner[v2] via roadmap-strategy worker (source analysis: `langgraph-checkpoint-performance-discussion.md`, 1777 lines)
Status: Ready for Review
Duration: 2026-08-26 → 2027-01-29 (Phase 1: ~3 weeks; Phases 2+ sketch-level)

## Initiative Summary

Remove checkpoint-history reconstruction from the normal message API (measured: ~206 MB transfer, ~42 s latency, 2.1 GB RSS for one GET /messages) and stop unbounded `checkpoint_blobs` growth, following the doc's central rule: *a LangGraph checkpoint is an execution recovery primitive, not the application's conversation database* (§39).

## Hard Constraints (apply to every phase)

1. **No naive `DELETE FROM checkpoint_blobs`** — blobs are versioned/shared across checkpoint reconstructions (§9); any prune must be reference-aware and §9-test-checklist-compliant.
2. **Do not touch pause/resume / turn-reconciler semantics** — checkpoint-at-node-boundary, `is_retry=True` resume-from-checkpoint, `resume_target_turn_id` handles, and the 8 mirror tables are off-limits.
3. **No frontend changes in Phase 1** — `/messages` response schema stays byte-compatible (Angular untouched).

---

## 1. Ranking Table (deliverable 1)

Score = **impact × ease ÷ blast-radius** (each 1–5; blast-radius lower = smaller). Sorted by composite. Phase 1 tier = C1–C4 (pre-decided by caller; ordering sanity-check below).

| # | Item | Impact | Ease | Blast | Score | Phase | Rationale (one line) |
|---|------|--------|------|-------|-------|-------|----------------------|
| 1 | **C4** — Phase 0 lite instrumentation (§27/§32): structured logs for /messages + saver op timing + `message_api_checkpoint_list_total==0` invariant | 3 | 5 | 1 | **15.0** | **P1** | Pure-additive logging; zero blast; captures the before/after baseline that proves C1's win and guards the regression forever. |
| 2 | **C1 / PERF-1** — kill `alist()` in /messages (§4): `aget` only + `latest["channel_values"]["messages"]`, timestamps from side table (absent row → fallback to latest-checkpoint `state.ts`, null only if both missing), schema unchanged | 5 | 4 | 2 | **10.0** | **P1** | Directly kills the measured incident (~206 MB → ~762 KB; 42 s → sub-second); one read path, response schema frozen. |
| 3 | **C2 / Solution M** — `message_metadata(thread_id, message_id, created_at)` side table + repository-layer write at message creation (§16/§17) | 4 | 4 | 2 | **8.0** | **P1** | Additive table + one idempotent insert; supplies C1's timestamps and is the seed of the future `agent_messages` store (designed to evolve, §17). |
| 4 | §33 guardrail — forbid saver/history access from request handlers (import-boundary hard-fail test now; full routers→MessageRepository / runtime→CheckpointRepository layering later) | 3 | 4 | 2 | **6.0** | P2 ⚠ | Cheap structural prevention of "someone re-adds `alist()` because it's convenient"; score argues for promotion — see Flag A. |
| 5 | **PERF-4 / Solution N** — one-time backfill of old threads' timestamps into `message_metadata` (§18) | 3 | 3 | 2 | **4.5** | P2 wave 1 ⚠ | Additive, idempotent, re-runnable offline batch; converts a recurring O(history) tax into one migration; score exceeds the P1 floor — see Flag B. |
| 6 | **C3** — reference-aware `checkpoint_blobs` prune, extending the retention job (§9-compliant: only blobs referenced by no remaining checkpoint of the thread) | 4 | 3 | 3 | **4.0** | **P1** | Only item that stops the verified unbounded-growth defect (retention prunes to 50/thread at constants.py:68 / maintenance.py:679-721 but never deletes blobs); ease/blast penalties encode the §9 care it requires, not low value. |
| 7 | Solution Q — LZ4/TOAST compression on `checkpoint_blobs` (§21) | 2 | 4 | 2 | **4.0** | P5 ⚠ | Ease-inflated score; §36 explicitly forbids treating compression as the fix, and C3 shrinks the very table it would compress — see Flag C. |
| 8 | PERF-3 / Solution A(+B) — durable `agent_messages` / `agent_events` store (schema, indexes, idempotent writes, stable IDs, run correlation) | 5 | 2 | 3 | **3.3** | P2 | The doc's "strongest general-purpose solution" and target-architecture cornerstone (§5); expensive because it becomes the system of record (consistency model §30 must be chosen). |
| 9 | Solution R — network path fix, 100 Mbps cross-host (§22) | 2 | 3 | 2 | **3.0** | P5 (parallel ops track) | Valuable independently, but "bad architecture × fast network = still bad"; zero coupling to app phases. |
| 10 | PERF-5 / Solution D — evaluate `ShallowPostgresSaver` (§8): feature/interrupt/pending-write/subgraph tests + storage benchmark | 4 | 3 | 5 | **2.4** | P2 | Biggest storage lever *if* it fits; blast 5 because adoption would replace the saver under pause/resume semantics we've promised not to disturb without proof. |
| 11 | PERF-2 / Solution C — cursor pagination for /messages (§7) | 4 | 2 | 4 | **2.0** | P2 | Bounds response size independently of history; needs API + Angular changes (the frontend blast is why it's not Phase 1). |
| 12 | Saver connection concurrency — pool / per-instance conns (single long-lived psycopg conn serializes ALL instances, persistence.py:163-172) | 4 | 2 | 4 | **2.0** | P2 eval / P3 impl | Throughput ceiling independent of `alist()`; C1 removes the acute pain, so measure contention first (C4), then size the pool. |
| 13 | PERF-6 / Solution F(+G) — bounded active message window in graph state + summary; context-reconstruction variant | 4 | 2 | 4 | **2.0** | P3 | Fixes the runtime side of 2.1 GB RSS (state size per put); changes what the LLM sees → quality-drift risk gates it behind measurement. |
| 14 | PERF-7 / Solution J — artifact/reference storage for large tool outputs (§14) | 3 | 2 | 3 | **2.0** | P3 | Huge win for coding-agent payloads but a cross-cutting reference-vs-inline type change touching every message consumer. |
| 15 | PERF-8 remainder / Solution E(+T) — checkpoint lifecycle policy choice (3A shallow / 3B last-N / 3C per-run / 3D hybrid) + cleanup migration + monitoring | 4 | 2 | 4 | **2.0** | P3 | The *policy* layer atop C3's mechanism; blocked on the PERF-5 evaluation outcome and the §34 Q1 time-travel answer. |
| 16 | Solution K — audit graph state: no ephemeral/UI-only channels in durable state (§15) | 2 | 3 | 3 | **2.0** | P4 | Cheap hygiene pass; savings bounded by how much scratch data the 10-node graph actually persists. |
| 17 | Solution U — completed-run compaction (§25) | 3 | 2 | 4 | **1.5** | P4 ⚠ | Conflicts with instance-revive semantics (COMPLETED → RUNNING reuses the existing checkpoint) — compaction must exempt revivable instances. |
| 18 | Solution P — read replica for history/analytics load (§20) | 1 | 3 | 2 | **1.5** | P5 | Moves load, fixes nothing (206 MB still transferred); admin/analytics convenience only. |
| 19 | Solution O — cache /messages (§19) | 2 | 2 | 3 | **1.3** | P5 / deprioritized | Doc's own verdict: hides bad query architecture, adds invalidation; only after persistence is correct — and then probably unneeded. |
| 20 | Solution I — thread rotation (§13) | 3 | 1 | 5 | **0.6** | P4 / deferred | Cuts across `thread_id == instance_id`, a load-bearing invariant (instance manager, resume handles, mirror tables). Most invasive item on the board. |
| 21 | Solution H — separate conversation_id from execution run/thread (§12) | 3 | 1 | 5 | **0.6** | Deferred | Same invariant damage as rotation, larger conceptual change; only attractive if per-run lifecycles become the product model. |
| 22 | Solution S — custom `BaseCheckpointSaver` (§23) | 3 | 1 | 5 | **0.6** | Only-if | Permanent maintenance burden of tracking LangGraph persistence semantics; doc gates it behind "shallow + retention proven insufficient". |

### Sanity-check: does the arithmetic support the pre-decided Phase 1 tier? — **Yes, with three flags**

- **Ranks 1–3 are Phase 1 outright** (C4 = 15.0, C1 = 10.0, C2 = 8.0). The tier's core is exactly what the directive ("easy, small blast radius, high impact first") selects for.
- **C3 holds only rank 6 by composite (4.0)** but stays Phase 1 on impact-urgency: it is the only item addressing the *verified unbounded-growth defect* (finding #4). Every week of deferral grows the primary DB; its ease/blast penalties are the §9 test checklist, not low value. Deferring a controlled, well-tested prune to "save composite points" trades it for continued unbounded growth during Phase 2 — a bad trade.
- **Recommended landing order within Phase 1: C4 → C2 → C1 → C3.** Composite order (C4 > C1 > C2) and dependency order agree on C4 first (baseline must be captured *before* the read-path flip or the before/after delta is unprovable) and C3 last (longest test runway). C1↔C2 are deliberately order-flexible: C1's `state.ts` fallback is safe without C2, but landing C2's write path first (or same-PR) minimizes the window where old messages show the fallback (latest-checkpoint) timestamp instead of their true per-message time.
- **Flag A (§33 guardrail, 6.0 — promotion argument):** resolve by pulling only the *import-boundary guard* ("no `langgraph.checkpoint.*` import under daemon/routers/**", enforced as a hard-fail test) into Phase 1 — it is literally PERF-1's own third bullet ("regression metric ensuring no checkpoint-list call occurs") and costs ~a day. The full layering refactor stays Phase 2+.
- **Flag B (PERF-4 backfill, 4.5 — promotion argument):** tops C3's composite but is *not needed for correctness* (C1 falls back to latest-checkpoint `state.ts` for messages without side-table rows; only old threads' timestamp precision is affected). Resolution: schedule as the **first item of Phase 2 wave 1** rather than expanding Phase 1 scope.
- **Flag C (Solution Q LZ4, 4.0 — ties C3):** score is ease-inflated. §36 explicitly warns against declaring the issue fixed via compression; rollout requires the §21 benchmark; and C3 reclaims the exact bytes it would compress. Held at Phase 5 by policy.

---

## 2. Phase 1 — Top Tier (pre-decided; details live in `phase1-plan.md`)

- **C1 (PERF-1):** Replace the `aget`+`alist(1000)` walk in `get_instance_messages` (persistence.py:254) with `aget`-only + `latest["channel_values"]["messages"]`; timestamps looked up from the side table, falling back to the latest-checkpoint `state.ts` when a row is absent (persistence.py:368-370; null only when even that is missing); response schema unchanged. Expected: ~206 MB → ~762 KB read.
- **C2 (Solution M):** New `message_metadata(thread_id, message_id, created_at)` table + repository-layer idempotent write (ON CONFLICT DO NOTHING) at message creation; designed to evolve into `agent_messages` later.
- **C3:** Extend the retention job (maintenance.py:679-721) with a conservative, reference-aware `checkpoint_blobs` prune — delete only blobs not referenced by any remaining checkpoint of the thread; dry-run mode + §9 test checklist + rehearsed restore.
- **C4 (Phase 0 lite):** Structured logs on /messages (latency, bytes, message count) + saver op timing (`checkpoint_get/list/put_seconds`) + `message_api_checkpoint_list_total` counter expected 0, alerted on any nonzero.

Acceptance (Phase 1): GET /messages never enumerates checkpoint history (import-guard + observed-count tests, under the existing test gates); invariant green in prod; blob growth curve flattens; pause/resume + resume-from-checkpoint integration suites unregressed.

---

## 3. Milestones

| # | Milestone | Date | Definition of Done | Owner | Status |
|---|-----------|------|--------------------|-------|--------|
| M1 | Baseline + side table (C4 + C2) | 2026-09-02 | Instrumentation merged & baseline P50/P95/RSS captured pre-flip; `message_metadata` writes flowing for all new messages | developer + tester | pending |
| M2 | Read-path flip (C1 + §33 import guard) | 2026-09-09 | `alist()` gone from /messages; schema byte-identical; import-level hard-fail test (no `langgraph.checkpoint.*` import under daemon/routers/**) + observed-count invariant test, both under the existing test gates | developer + tester | pending |
| M3 | Reference-aware prune (C3) | 2026-09-16 | Prune merged behind dry-run→enabled ladder; §9 checklist green (latest-state load, interrupt resume, pending writes, subgraphs, no referenced-blob deletion, concurrency, rollback rehearsed) | developer + tester + reviewer | pending |
| M4 | **Phase 1 gate** (see §6) + Phase 2 kickoff decisions | 2026-09-23 | Gate criteria all true; §34 blockers answered (consistency model, schema A/B, SLA, time-travel-in-prod) | planner + user | pending |
| M5 | Phase 2 wave 1: backfill (PERF-4) + message store (PERF-3) + shallow-saver evaluation (PERF-5) | 2026-10-21 | Old threads backfilled & validated; `agent_messages`/`agent_events` schema + idempotent dual-write live; ShallowPostgresSaver fit report vs pause/resume semantics | developer + tester | pending |
| M6 | Phase 2 wave 2: cursor pagination (PERF-2) + PERF-5 decision | 2026-11-18 | `?before=&limit=` shipped API+frontend (lazy scroll-up); saver policy decided (3A–3D) with test evidence | developer + tester + reviewer | pending |
| M7 | Phase 3: bounded active state (PERF-6), conn concurrency if C4 data justifies, artifacts (PERF-7) | 2026-12-30 | MAX_ACTIVE_MESSAGES/MAX_INLINE_BYTES thresholds enforced via compaction; pool sized or explicitly declined with measurements; artifact references for >threshold payloads | developer + tester | pending |
| M8 | Phase 4/5 (sketch): rotation/compaction re-evaluation, storage & network tuning | 2027-01-29 | LZ4 benchmark post-C3; Solutions I/U/H/S consciously adopted or parked with rationale | deferred | pending |

## 4. Timeline

| Phase | Start | End | Duration | Effort (PW) | Confidence | Buffer |
|-------|-------|-----|----------|-------------|------------|--------|
| P1 (C4→C2→C1→C3 + gate) | 2026-08-26 | 2026-09-23 | 4 wk | 3 | high | 25% (C3 test matrix is the unknown) |
| P2 wave 1 (PERF-4/3/5-eval) | 2026-09-23 | 2026-10-21 | 4 wk | 3 | medium | 20% |
| P2 wave 2 (PERF-2 + PERF-5 decision) | 2026-10-21 | 2026-11-18 | 4 wk | 3 | medium | 20% (frontend) |
| P3 (PERF-6/7, conn pool) | 2026-11-18 | 2026-12-30 | 6 wk | 4 | low | 30% |
| P4/P5 (parking lot) | 2027-01-04 | 2027-01-29 | 4 wk | 1 | low | n/a (decision-driven) |

PW = person-weeks (single developer + agent test/review cycles, per project convention).

## 5. Dependencies

| # | From | To | Type | Owner | Unblock Action |
|---|------|-----|------|-------|----------------|
| 1 | C4 (baseline capture) | C1 | hard | developer | Merge instrumentation before the read-path flip so before/after is provable |
| 2 | C2 (side-table writes) | C1 | soft | developer | Land C2 first/same-PR to minimize fallback-timestamp window (C1 safe without it via `state.ts` fallback) |
| 3 | C1 + C2 | PERF-4 backfill | hard | developer | Backfill targets C2's table; C1's `state.ts` fallback makes timing flexible |
| 4 | C3 (prune mechanism) | PERF-8 policy (3A–3D) | hard | team | Policy needs the prune as its 3B enforcement arm |
| 5 | PERF-5 evaluation | PERF-8 decision / Solution T | hard | team | §8 test matrix outcome selects the policy |
| 6 | PERF-3 (message store w/ sequence) | PERF-2 pagination | hard | developer | Cursor pagination needs a stable sequence column |
| 7 | §34 Q1 (time travel in prod?) | PERF-5 adoption | hard | user | Believed "no" — must be confirmed before any saver switch |
| 8 | PERF-3 + PERF-5 decision | Solution I rotation | hard | team | Rotation is only safe when UI history and execution state are fully decoupled |
| 9 | C3 | Solution Q (LZ4) | soft | ops | Compress after the table stops growing; benchmark on post-prune distribution |
| 10 | C4 (saver op timing) | Conn-concurrency sizing | hard | developer | Pool size must come from measured contention, not vibes |

## Critical Path

```
C4 ──► C2 ──► C1 ──► [Phase 1 gate] ──► PERF-3 (store) ──► PERF-2 (pagination) ──► end-state UX
                │                          │
                └──► C3 ──► PERF-8 policy └──► PERF-5 eval ──► PERF-8 decision ──► end-state storage
```

- **Critical path duration:** ~17 weeks to the full §26 target architecture (P1 4 wk + P2 8 wk + P3 6 wk − overlap).
- **Float:** C3 has ~1 week float inside Phase 1 (parallel track, longest test runway); Solution R (network) and §33 layering have full float (parallel tracks).
- **Acceleration candidates:** (1) C1 alone delivers ~99% of the measured incident's relief — if only one thing ships, ship C1+C4; (2) pulling PERF-4 into Phase 1 tail if C3's test matrix slips (Flag B); (3) pre-building PERF-3's sequence column into C2's table now (cheap option value, avoids a later ALTER).

## 6. Phase Gating — what must be TRUE before Phase 2 starts

1. **Merged & deployed:** C1–C4 all merged to `latest` and pushed through the standard deploy ladder into prod, past a ≥1-week soak.
2. **Invariant green:** `message_api_checkpoint_list_total == 0` in production for ≥7 consecutive days (no alert firings); enforcement is an import-level hard-fail test in the standard test suite (asserts no `langgraph.checkpoint.*` import under daemon/routers/**) plus the observed-count invariant test — both run under the existing test gates (the repo has no CI).
3. **Measured win recorded:** /messages P50/P95/P99 + response bytes + daemon RSS delta captured (C4 baseline vs post-C1), filed next to this roadmap as evidence.
4. **Storage curve bent:** post-C3 `checkpoint_blobs` size-per-thread chart flat or declining over ≥2 retention cycles; dry-run reports reviewed; restore procedure rehearsed once.
5. **No regressions in protected semantics:** pause/resume, interrupt/human-approval resume, `is_retry` resume-from-checkpoint, turn-reconciler mirror suites all green (Phase 1 must not have disturbed them — gate proves it).
6. **Phase 2 design blockers answered (§34):** consistency model chosen (§30 — recommend A: idempotent eventual projection); schema decision message-only vs event store (§5 vs §6); /messages SLA target (§28 proposes P95 < 500 ms same-DC); time-travel-in-prod question (Q1) answered — this one gates PERF-5 specifically.
7. **No open SEV** related to /messages latency or DB growth.

---

## 7. Phase 2+ Sketches (NOT implemented now — one paragraph each)

### PERF-2 — Cursor pagination (§7 / Solution C)
**What/why:** After C1, /messages still serializes the *entire* lifetime conversation from the latest checkpoint's `messages` channel — fine at 786 KB, unacceptable at 10 MB+. Cursor pagination (`GET /instances/{id}/messages?before=<cursor>&limit=50`, SQL `WHERE thread_id=$1 AND sequence<$2 ORDER BY sequence DESC LIMIT $3`) makes response cost proportional to page size, per §39's principle. **Dependency on Phase 1:** hard on PERF-3's sequence column (or a cursor derived from message order + C2's table); C1 keeps the no-cursor response shape working meanwhile. **Top risk:** API contract change plus Angular lazy-load (scroll-up fetching) — frontend regression surface is exactly why this is not Phase 1; cursor stability across message mutations needs a defined rule. **Rollback:** ship cursor as an opt-in param — default (no param) returns the legacy full payload; rollback = frontend stops passing the cursor. **Frontend impact: YES.**

### PERF-4 / Solution N — One-time timestamp backfill (§18)
**What/why:** Old threads' messages predate C2's side table, so post-C1 they show the latest-checkpoint `state.ts` fallback timestamp rather than their true per-message time (null only when even `state.ts` is absent) — and will until this backfill lands; a single offline, batched scan of checkpoint history (the same expensive `alist` path — run deliberately, throttled, off-peak, resumable) extracts message_id → first-observed timestamp into `message_metadata`, converting a recurring O(history) tax into one migration. **Dependency on Phase 1:** needs C2's table as target and C1's fallback-tolerant read path as the safety net (idempotent, re-runnable). **Top risk:** production DB load during the scan (206 MB/thread-class reads) — batch size + throttle + off-peak window mandatory; extraction logic must exactly replicate the current persistence.py timestamp-derivation semantics or old threads get subtly wrong times. **Rollback:** rows are additive to a side table — a bad backfill is corrected by deleting the affected threads' `message_metadata` rows and re-running (note: the §9 no-naive-DELETE warning applies to `checkpoint_blobs`, not this table; deleting side-table rows is safe). **Frontend impact: NO** (timestamps simply appear).

### PERF-5 / Solution D — ShallowPostgresSaver evaluation (§8)
**What/why:** The shallow saver retains only the latest checkpoint per thread, eliminating history accumulation *by construction* instead of by pruning — the largest single storage lever available. **Fit check against our protected semantics (the crux):** pause/resume freezes the turn at the last committed node boundary and resumes via `is_retry=True` from checkpoint — that boundary *is* the latest checkpoint, so shallow should fit; but the §8/§37 test matrix must prove it against: interrupt/human-approval resume, `checkpoint_writes` (pending writes) retention under shallow mode, subgraph checkpointing, turn-reconciler mirror assumptions (8 mirror tables keyed by work_id — confirm none read history), and mixed-fleet operation (existing threads on full saver, new threads on shallow). §34 Q1 ("do we use time travel in prod?") must be answered NO first. **Dependency on Phase 1:** C3 keeps full-saver threads bounded in the meantime; C4's storage metrics provide the benchmark baseline. **Top risk:** an upstream semantic we silently depend on is absent in shallow mode → resume breakage discovered late; also rotation of saver per workflow type (Solution T) multiplies test combinations. **Rollback:** saver selection is flag/config-scoped per thread class — revert the flag and new checkpoints are full again; **caveat:** history already discarded by shallow mode is unrecoverable, which is precisely why the evaluation gate is test-matrix-heavy. **Frontend impact: NO.**

### Saver connection concurrency (finding #6; persistence.py:163-172)
**What/why:** `AsyncPostgresSaver` holds one long-lived psycopg connection, so every `aget`/`aput` across *all* instances serializes on a single socket — a system-wide throughput ceiling and latency-tail source independent of the `alist()` bug. Options: bounded connection pool (multiple saver instances), per-instance connections, or upstream pool support; the SQLite compat path (persistence.py:56-58) must keep working. **Dependency on Phase 1:** C1 removes the giant scans that made the serialization acute; C4's saver-op timing is the instrument that decides *whether* residual contention justifies the work (evaluate in Phase 2, implement in Phase 3 only if data says so); C3 shrinks per-op blob I/O. **Top risk:** connection lifecycle vs LangGraph's transactional expectations — checkpoint put + pending writes must stay atomic; pool exhaustion or leaked connections under instance churn would degrade the exact crash-recovery path we've promised not to destabilize. **Rollback:** make pool size a flag where size=1 ≈ current single-connection behavior. **Frontend impact: NO.**

### PERF-7 / Solution J — Artifact/reference storage for large tool outputs (§14)
**What/why:** Coding-agent messages embed multi-hundred-KB tool outputs (logs, code, retrieved chunks) that get re-checkpointed in every subsequent checkpoint's `messages` channel; storing payloads out-of-band (artifact table / object store) and keeping `{artifact_id, type, preview, size}` references in state stops that multiplication. Threshold from measurement (~64–128 KB starting point, §14). **Dependency on Phase 1:** mechanically independent, but sequenced after C3 (stop growth first) and C4 (measure what is actually big before choosing thresholds). **Top risk:** cross-cutting type change — every consumer of message content (Angular rendering, child_reports, compaction, RAG ingestion) must handle reference-vs-inline duality; plus lifecycle/GC rules so orphaned artifacts don't recreate the unbounded-growth problem one level up. **Rollback:** threshold-gated — setting the threshold to ∞ disables reference extraction; artifacts table is additive. **Frontend impact: PARTIAL** (preview renders inline; fetching the full payload becomes a separate call if the UI needs it).

### Thread rotation (§13 / Solution I)
**What/why:** Cap worst-case per-thread persistence by rotating to a fresh LangGraph thread after N checkpoints / time / serialized-size threshold, compacting state at the rotation boundary and keeping a `conversation_id → current_thread_id` mapping; old threads archive under retention policy. **Dependency on Phase 1:** needs PERF-3 (UI history spanning threads transparently) and the PERF-5/PERF-8 policy decision first — rotation is the 3C option, only attractive if per-run lifecycles win. **Top risk (why it scores 0.6 and is deferred):** `thread_id == instance_id` is load-bearing across the instance manager, checkpoint resume handles (`resume_target_turn_id`), and the turn-reconciler's 8 mirror tables — rotation cuts across exactly the pause/resume/reconciler semantics this initiative is forbidden to disturb, so it requires its own dedicated design cycle. **Rollback:** rotation is forward-only per conversation (once rotated, the old thread is archived); mitigate by flag-gating the trigger threshold so rotation can be disabled, and by archiving (not deleting) old threads under §9-safe rules. **Frontend impact: NO** (the mapping layer hides thread switching).

---

## Resource Allocation

| Milestone | Team/Role | FTE | Skills Needed | Conflicts |
|-----------|-----------|-----|---------------|-----------|
| M1–M3 (Phase 1) | developer + tester agent + reviewer | 1.0 + agent cycles | LangGraph saver internals, psycopg, PG DDL, retention job | None — Phase 1 is scoped to be conflict-free |
| M4 (gate) | planner + user | 0.2 | Decision-making on §34 blockers | User availability for Q1/Q11 answers |
| M5 (P2 w1) | developer + tester | 1.0 | Batch migration scripting, schema design, LangGraph shallow-saver internals | Tester cycle shared with any parallel initiative |
| M6 (P2 w2) | developer + frontend | 1.0 + FE | Angular lazy-load, API contract versioning | First frontend touch in this initiative |
| M7 (P3) | developer + tester | 1.0 | Compaction middleware, pooling, artifact lifecycle | Compaction work overlaps context-compaction subsystem ownership |
| M8 | decision-only | 0.1 | — | None |

## Resource Gaps

- **LangGraph-saver-internals depth** for PERF-5 (shallow-saver semantics under our pause/resume contract) — the §8 test matrix is the mitigation; budget reviewer time for it specifically.
- **Frontend capacity** first needed only at M6 — flag early so it isn't the critical-path surprise.
- No dedicated DBA/ops role exists for C3's restore rehearsal and the §21 LZ4 benchmark — developer doubles as ops per project convention.

## Calendar Constraints

- Phase 1 window (Aug 26 – Sep 23, 2026) has no known freeze; keep C3's dry-run→enabled ladder clear of any deploy-freeze windows per the project's standard deploy ladder.
- M7 (Phase 3) spans year-end holidays (late Dec) — the 30% buffer and the low confidence rating already account for this.

## Open Questions (blockers are marked in §6 gate; full list is §34 of the source doc)

1. Do we use LangGraph time travel in production? (gates PERF-5 — believed no, needs confirmation)
2. Consistency guarantee between message store and graph checkpointing — §30 A/B/C? (recommend A: idempotent eventual projection)
3. Message-only table vs event store (§5 vs §6) for PERF-3?
4. /messages SLA — adopt §28's P95 < 500 ms same-DC or set our own?
5. Max active message count and max inline tool-result size (§34 Q8/Q9 — Phase 3 thresholds, measurement-driven)?
6. Where do large artifacts live — DB artifact table vs object store (Phase 3)?
 store (Phase 3)?
