# Architecture Recommendation: `generate_chart` Charter Reuse (P1–P9 Adjudication)

Date: 2026-09-10
Author: Architect (controller) — aggregation of 3 competitive fan-out worker reports (`structural-design`, one approach each)
Instances: `ada9f025` (A: revive-seam), `87cd8bbb` (B: persistent charter), `40ef3421` (C: stateless handoff)
Inputs: plan-overview.md, decisions.md (Part A M1–M14 / Part B P1–P9), technical-analysis.md, phase1-3-plan.md, research-*.md
Status: **ADJUDICATED** — 8 of 9 planner defaults CONFIRMED; 1 bounded deviation (P2 → pure query-discovery); 2 bounded additions (P9 test pins)

---

## Executive Summary

**Approach A (the planner's default — chart_tools-local register→enqueue→wait helper riding the service-side revive-on-send seam) is CONFIRMED as the reuse mechanism.** It is the only candidate that both (a) makes small-fix iterative refinement actually work — the revived charter reloads its full checkpointed history (prior mermaid, reasoning, NEEDS-MORE-INFO rounds, mmdc retry history) — and (b) requires zero new lifecycle machinery: it rides the terminal→RUNNING state machine, the terminal-only CompletionRegistry signal, permanent `instances.parent_id`, and the deliberately-reaper-free persistence of completed instances. B (persistent never-terminal charter) is rejected — it must invent a per-turn completion primitive that does not exist and would cascade-wedge ancestor chains, a designed-against bug class. C (stateless handoff) is rejected as primary — its failure mode is silent (refinement-quality regression on multi-round-clarification callers) and its cost is N× spawn with semaphore exhaustion under fan-out. **One deviation from the planner: P2 flips from SharedMetaKV-pointer-with-cross-check to pure query-discovery** — the KV pointer saves one indexed lookup, adds a staleness class, leaks unbounded orphan rows, and (decisively) becomes visible plumbing in the caller LLM's per-turn context via the ambient-KV render (merge 02cf770a, default ON).

---

## Mechanism Trade-Off Comparison

Full five-axis table + evidence: **`approach-comparison.md`** (this directory). Summary:

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|----------|-----------|-------------|-----------------|------|------|---------|
| **A: revive-seam local helper** | Low | Med | High | Med | Low | **WINNER** — rides existing grain; refinement works via checkpoint |
| B: persistent long-lived charter | High | Med-Low | Med | High | Med | **REJECT** — new per-turn completion machinery; cascade-wedge |
| C: stateless context handoff | Low | Med | High | Med | High | **REJECT as primary** — silent quality regression; N× cost |
| Hybrid A+C / B-park | — | — | — | — | — | None for v1 — A+C redundant under A (checkpoint carries prior mermaid); B-park collapses into A |

---

## P1–P9 Adjudication

### P1 — D-reuse-mechanism → **CONFIRM (a) chart_tools-local private helper**
The register→enqueue→wait→unregister block lives in `daemon/tools/chart_tools.py`, mirroring `utils.py:672-740` (including the buffered-completion re-register at `:684-686` and the `finally` unregister). Do NOT extend `invoke_agent_and_wait` (option b — pinned sister-tool surface for zero benefit to them) and do NOT pre-extract a shared helper (option c — premature abstraction; planner Q4 rec confirmed: extract only when a second consumer appears).
**Rationale:** A rides existing machinery with no invariant violations (worker A FA#1: revive-on-send `instance_messaging.py:1931-1966` with `is_terminal_revival` guard; permanent `parent_id` walk `repository.py:424-429`; no reaper required). B fails at the protocol layer — `CompletionRegistry.complete()` fires ONLY at terminal transitions (`child_reports.py:3733/3829/3946`, `error_reporting.py:718`), so a never-terminal charter can never resolve `wait_for` (worker B FA#1, the fatal flaw). C works for ~80% small-fix cases but silently degrades multi-round-clarification callers (worker C FA#2 🔴).
**Bounded edits:** none — planner default stands.

### P2 — D-tracking-store → **DEVIATE: flip (c) KV-pointer → (a) PURE QUERY-DISCOVERY**
Discovery = `get_children(caller_id)` filtered to `agent_id=="charter"` + `instance_metadata["invoked_as_tool"]`, ordered `last_activity_at` desc (tie-break `created_at`, then id). **No SharedMetaKV pointer row. No KV read, write, cross-check, or self-heal code.**
**Rationale (worker A FA#5, corroborated by worker C FA#1):**
1. **Ambient-KV pollution is decisive.** The kv-ambient-awareness-fix (merge 02cf770a, default ON) renders per-`context_key` SharedMetaKV rows as a per-turn `[Shared Meta KV]` block to the agent. A `charter_instance_id` row on the caller's context_key becomes visible internal plumbing in every caller turn — plus a cross-tool collision surface on that key.
2. **The KV saves exactly one indexed lookup** (N≈1 charter children per caller; `get_children` is an indexed permanent-parent_id walk) — negligible against the enqueue + graph + LLM turn that follows. The KV's DB-wins cross-check re-queries anyway, so the "cache" does not even save the query on the read path.
3. **The staleness class disappears entirely** — no pointer to disagree with the DB, no orphaned rows in `shared_context_metadata` (which has no TTL/reaper — worker C: `models.py:49-72`).
4. Restart-safe by construction (state re-derived from `instances` truth).
**Bounded edits (P2 flip was pre-authorized by decisions.md):**
- **Phase 1 T2: DELETE** (T1 discovery is the sole store).
- **Phase 1 Task 1 (fixture):** drop the `shared_meta_kv_repo` stubs (`get → None`, `set_kv → True`) — no longer exercised; KEEP `get_children → []` and `enqueue_message` AsyncMock.
- **Phase 1 T4:** "write-through after fresh spawn" → no-op (discovery finds the newest charter automatically via `last_activity_at`).
- **Phase 1 T8.7:** DELETE `test_pointer_stale_db_wins` and `test_kv_error_falls_back_to_discovery`.
- **Phase 3 T1:** "pointer pre-seeding vs discovery-only" gate → discovery-only.
- **plan-overview Success Criteria:** "fresh=True … updates the tracking pointer" → "…and the next reuse call discovers the new charter (latest by `last_activity_at`)".
- Note: `M5 caveat` (migration-runner NO-OP on PG) stays dormant — no schema interaction at all under (a).

### P3 — D-scoping-key → **CONFIRM (a) per-caller-instance (`context_key` concept drops away entirely under P2=(a))**
Fan-out is safe by construction: sibling children each own their charter; cross-talk impossible. Per-tree-root (b) would couple sibling refine loops to ONE charter's busy-guard surface — strictly worse under worker B's wedge analysis. Per-session (c) has no primitive.
**Bounded edits:** none — but under P2=(a) the reserved `_resolve_scope_key()` seam becomes trivial (the "key" IS `current_instance_id` as the `get_children` argument); keep or drop at implementer's discretion.

### P4 — D-api-shape + default semantics → **CONFIRM (a) `fresh: bool = False` (default = reuse)**
Default-reuse IS the feature ("successive calls reach the SAME charter"). Boolean kwarg matches existing style (`is_deferred`, `is_background`). The default-semantics change for ~15 carriers is deliberate and documented (Phase 2). Worker C's evidence sharpens the justification: today's always-fresh default makes the documented "Refine, don't hand-edit" contract (`chart/skill.md:62`) a blind re-derivation — the current default is the bug; default-reuse fixes documented behavior. Carriers needing per-call independence pass `fresh=True`.
**Bounded edits:** none.

### P5 — D-error-policy → **CONFIRM (b) one local revive attempt on ERROR/FAILED, then respawn**
Local per-charter counter (ERROR/FAILED consumes; COMPLETED/TERMINATED free; after 1 consumed → fresh spawn, `mode=reuse-respawn-after-failure`). M8 (reuse-path timeout does NOT terminate) and M14 (PAUSED → busy-reject) both stand.
**Rationale (worker A FA#2):** the counter's scope semantics exactly mirror the vetted v1-scope fix (`manager.py:2876-2886`). A refine-loop on a HEALTHY charter is not thrash — each iteration is a real, context-grounded refine turn; today's equivalent loop spawns blind fresh charters (strictly worse). Counter lost on restart: accepted (precedent `manager.py:2792-2793`).
**Bounded edits:** none.

### P6 — D-charter-prompt-delta → **CONFIRM (a) no prompt/message change in v1; A/B post-merge**
Worker A confirms refinement works via checkpoint history alone; worker C quantifies exactly what that history carries (NEEDS-MORE-INFO rounds, rejected intermediates, mmdc warnings, style memory) — the empirical A/B question (does the charter need an explicit refinement signal?) stays one-variable-at-a-time post-merge, per planner M9. Worker C's "bounded N-round mini-history" hybrid idea is redundant under A (checkpoint already carries it) — its only residual value is the refinement SIGNAL, i.e., exactly the gated P6=(b) follow-up. Phase 1 C1 stays dormant.
**Bounded edits:** none.

### P7 — D-carrier-doc-surface → **CONFIRM (a) `chart/skill.md` only**
The documented usage contract lives in the innate skill (auto-loaded by `innate_skills: ["chart"]` carriers); no other carrier prompt mentions `generate_chart`. Optional one-shot conventions.md note (Phase 2 T3) retained.
**Bounded edits:** none — but Phase 2 T1 wording should ALSO document the wedged-charter operator guidance added below (Residual Risks → doc duty).

### P8 — D-counter-location → **CONFIRM (a) `chart_tools.py` module-level dict**
Co-locating mechanism + policy in one file; manager centralization would push chart-specific semantics into the daemon core for a single consumer. The mandatory code comment cross-referencing `manager.py:773` ("SEPARATE mechanism from agent-tool ReviveGuard — programmatic paths never call `note_agent_tool_revive`, `manager.py:2832-2834`") stays (R5.3).
**Bounded edits:** none.

### P9 — D-reuse-test-pattern → **CONFIRM (M3 shape) + 2 bounded additions**
Pattern stays: reuse path patches manager methods + the `daemon.services.completion_registry` MODULE attribute; fresh path keeps patching `daemon.tools.chart_tools.invoke_agent_and_wait`; real-routing coverage in Phase 3 T1 (file-backed SQLite, governor-walk precedent).
**Bounded additions:**
1. **Busy-guard ordering pin (NEW unit test, Phase 1 T5/T8):** the in-flight check must fire BEFORE `CompletionRegistry.register()` — not merely before enqueue. Worker A FA#4: two waiters on the same `instance_id` share one event; the second waiter wakes on the FIRST completion (event coalescing, `completion_registry.py:73,158-161`) and would return a stale result for its own message. Test: two concurrent reuse calls → exactly one registers/enqueues; the rejected call observes NO register side-effect.
2. **ReviveGuard non-interaction pin (Phase 3 T2, confirm coverage):** `manager.get_agent_tool_revive_count(charter_id) == 0` after a full reuse cycle — assert it for BOTH a COMPLETED-revive and an ERROR-revive (cheap insurance against future drift; worker A FA#2 suggestion).
3. (From P2) fixture drops `shared_meta_kv_repo` stubs; pointer tests deleted.

---

## Focus-Question Rulings (dispatcher's key areas 5 & 7)

### Unguarded programmatic revive path — local counter SUFFICIENT; NO daemon-side guard
(i) The authority split is deliberate architecture: agent-tool policy lives at the tool layer (`instance.py:2978-2984` precedent); programmatic paths bypass `manager.py:2832-2834` by design. (ii) ERROR/FAILED thrash is bounded by the one-revive counter. (iii) Healthy-charter revive loops are not worse than today (each loop is a context-grounded refine turn vs today's blind fresh spawn). (iv) A daemon-side guard would centralize chart semantics into `manager.py` for one consumer — rejected. Residual: restart clears the counter (accepted, precedent).

### Wedged-busy charter under M8 — ACCEPTED residual with documented operator escape
M8 (no terminate on reuse-timeout) is correct: terminating races an in-flight turn and poisons the next refine with a TERMINATED revival. Self-heal path: a merely-slow charter completes → terminal → next call revives. A truly-hung charter stays RUNNING (orphaned, no reaper); the caller is never wedged (busy-reject is immediate and actionable). Operator escape: `fresh=True` for a new charter + manual `terminate_instance` via the daemon API for the orphan. **Do NOT add a charter-terminate tool surface** (blast radius for a rare event; worker A FA#3). Phase 2 T1 should carry one sentence of this guidance so carriers know the busy-reject → `fresh=True` ladder.

---

## Residual Risks (post-adjudication)

| # | Risk | Severity | Mitigation / Notes |
|---|------|----------|--------------------|
| 1 | Wedged charter (true LLM hang): orphaned RUNNING instance, no reaper; accumulates silently until operator terminates | 🟡 | Observable via persistent `busy-reject` mode log; escape documented in skill.md; manual `terminate_instance`. Bounded by LLM-stall rarity. |
| 2 | Compaction fidelity cliff at extreme refine counts (~50+ iterations; mermaid-bearing AIMessages ARE selectable, `compaction.py:160-219`; thresholds 560k/665k vs 10–150 KB realistic history) | 🟡 | Effectively unreachable at realistic loop sizes (2–10); skill.md guidance: switch to `fresh=True` if compaction observed mid-loop. |
| 3 | Two-waiter CompletionRegistry event coalescing if busy guard misordered | 🟢 → mitigated | Ordering requirement + unit pin (P9 addition 1). |
| 4 | Local revive counter lost on daemon restart | 🟢 | Precedent-accepted (`manager.py:2792-2793`). |
| 5 | Caller with multiple charter children (legacy pre-feature fresh spawns): discovery picks latest `last_activity_at` deterministically; older charters orphan silently | 🟢 | Same last-write-wins semantics the KV would have had; no pointer/DB disagreement possible under pure discovery. |
| 6 | Unverified: exact success-path terminal write site (both A and B workers flag; not load-bearing — revive flip verified independently) | 🟢 | Phase 3 T1 real-dispatch walk empirically confirms the COMPLETED→RUNNING→terminal cycle. |

## Decisions Pending (for leader/user)

None blocking implementation. Optional, non-blocking:
- Whether Phase 2 T3 (conventions.md migration note) is worth the surface — recommended YES (default-semantics change is Certain for ~15 carriers).

## Open Questions

1. Empirical refinement-signal question (P6 A/B): does the charter reliably infer refinement intent from checkpoint history alone? Decide post-merge with real traffic (gated Phase 1 C1 / P6=(b)).
2. Spawn-vs-revive latency delta unmeasured (worker C unverified item) — immaterial to the mechanism choice (A wins structurally), but worth a number in Phase 3 T1 notes.

## Confidence

**High.** The recommendation flips only if: (a) real traffic routinely produces 50+ iteration refine loops (re-opens C's bounded-context advantage), or (b) per-call `get_children` cost proves material at scale (re-opens the KV-pointer cache — currently refuted by N≈1 and the ambient-render hazard).
