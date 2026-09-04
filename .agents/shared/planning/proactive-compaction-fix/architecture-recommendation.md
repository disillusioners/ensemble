# Architecture Recommendation: Proactive Context-Compaction Fix — Unification with /compact

Date: 2026-09-04
Mode: 🏛️ Council (3-of-4 triggers: cross-system + multiple viable approaches + high blast radius) followed by an evidence-verification pass
Instances: council governor `54dd24d2` (councilor model `agentic` — single-source; `coding` councilor timed out at 1h cap, unusable) + verification worker `2f8ab69b` (`data-flow-design`)
Verification tree: `feature/queue-status-missions-badge` @ `13782089` (council analyzed `latest`; line pins below are from the verification tree)
Constraint honored: NO code changes — analysis + recommendation only. No path split from /compact unless unavoidable (adjudicated below).

---

## 0. Executive Summary

Proactive compaction is dead for **three stacked reasons, not one**. Fixing only the terminal-shape gate repairs the trigger for plain conversing instances but leaves the reported incident class — long-orchestrating `waiting_children` leaders like the 810-message / 493k-token instance `809e2a59` — **untouched**, because (a) their turns dispatch on the `is_retry=True` resume lane which skips compaction entirely, and (b) their growth is injected `[SYSTEM CONTEXT]` child-report messages which the trigger numerator excludes and which the engine can never select.

**Recommended design — lower-layer unification (approach b):** one shared status frozenset, one engine invocation, one Variant-A persist recipe lifted into a shared seam consumed by BOTH the proactive trigger and `compact_executor`; the 9-step CommandDispatcher pipeline remains the interactive wrapper. Full pipeline reuse for the proactive case is **rejected as actively harmful** — it self-deadlocks (verified, §4 Q1). Ships behind `compaction.proactive_enabled` (env `ENSEMBLE_PROACTIVE_COMPACTION`), **default OFF**.

**Key correction to the leader's Option A framing:** the council's "gate fix without persist swap bricks every instance" hazard was **downgraded to conditional** on verification — the `as_node='agent'` collapse only reproduces with `interrupt_before=['agent']`, which **zero production agents carry**. The persist swap rides along as hardening, not as a prerequisite. The genuinely load-bearing scope additions are the `is_retry` exclusion and numerator/budget coherence.

---

## 1. Verified Root Cause (three stacked layers + one coverage gap)

| # | Layer | Mechanism | Evidence | Result |
|---|-------|-----------|----------|--------|
| L1 | **Terminal-shape gate is exactly inverted** | `_maybe_compact_context` gates on `_is_terminal_checkpoint()` which returns True when `state.next == ()` — the shape of EVERY quiescent between-turns checkpoint, not just completed ones. All dispatch lanes funnel through the per-instance ExecutionGate, so the trigger always sees a quiescent-shaped checkpoint. | `instance_messaging.py:1196-1200`; `_checkpoint_utils.py:80-84`; `execution_gate.py:118-144` | Skip fires BEFORE token math, at DEBUG. **Zero** "Compaction triggered" in 39,600-line `ensemble.log` scan; ~6 days prod |
| L2 | **`is_retry` blanket skip excludes the flagship victims** | `if not is_retry: await self._maybe_compact_context(...)` — and the cascade-resume lane (suspended-turn leaders waking on child reports) dispatches EVERY turn with `is_retry=True` | `instance_messaging.py:3750-3751`; `manager.py:9592-9594` (`_resume_processing_background` :9511 ← `resume_instance_cascade` :8680) | 810-msg `waiting_children` accumulators NEVER reach proactive compaction — even after a pure gate fix |
| L3 | **Threshold numerator divergence** | Gate numerator counts REGULAR messages only (injected partitioned out); FE badge counts ALL. Injected messages are re-attached verbatim in every engine exit path and "MUST survive compaction" | `compaction.py:1780-1782, 1807-1808`; `instance_messaging.py:1051`; `context_messages.py:109`; `compaction.py:77-79` + re-attach at :1893, :2002, :2086, :2148, :2177, :2193 | UI-80% ≠ gate-80%; gate undercounts real window occupancy; gap grows with injected blocks |
| G1 | **480k–600k coverage gap** | CLE reactive backstop fires only on `ContextLengthExceededError` (~600k+) | `graph.py:3512-3547` | Band above the 480k trigger and below CLE has NO coverage |

**Precision notes (verification):** L2's "ALL cascade-resume turns" is slightly overstated — the watchover graph-restart *fallback* lane enqueues with no `resume_mode` metadata, so `is_retry` computes False there (`watchover_service.py:676, 722`; `task_processor.py:354`). The **primary** resume lane — the one that matters for orchestrators — is fully confirmed.

### 1.1 Correction to the council's brick-hazard claim (changes Phase-1 framing, not direction)

The council asserted Option A alone (gate fix, persist unchanged) **bricks** every auto-compacted instance. Verification: **PARTIAL — conditional brick**. The proactive path's own persist does use `aupdate_state(..., as_node='agent')` on both writes (`instance_messaging.py:1308-1311, 1316-1319`), and the collapse is documented at `:1177-1189` — but the canary test's own header is decisive: the brick reproduces **with** `interrupt_before=['agent']`; **without** it, langgraph 1.0.x re-primes the graph on new input and the agent runs anyway (`test_compact_executor_revive_brick_e2e.py:14-19`; echoed `compact_executor.py:58-62, 1564-1571`). Grep confirms zero `interrupt_before` in `agents/` + `daemon/`.

**Consequence:** the persist swap to Variant A is **defense-in-depth and version-fragility insurance** (it "closes the window regardless"), not the thing that makes the gate fix safe. It should still land in the same PR — it also kills a verbatim-duplicated derivation block (§3.3) — but Phase-1 scope is driven by L2/L3, not by a live brick.

---

## 2. Approach Comparison

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|----------|-----------|-------------|-----------------|------|------|---------|
| **(i) Minimal status-gate patch** (gate polarity + status gate only) | Low | **Low** — fixes plain conversing instances; L2/L3 leave leaders and injected growth uncovered | Low — duplicate gates/persist recipes persist; drift risk the user explicitly wants gone | Medium — NOT brick-driven (downgraded) but keeps version-fragile `as_node='agent'` persist and leaves the incident class broken on paper | Low | ❌ Insufficient — repairs the trigger, not the disease |
| **(ii) Unified lower-layer core** (shared status frozenset + engine seam + Variant-A persist helper; both paths consume) — **RECOMMENDED** | Medium | High — covers plain instances, leaders (with P2), and injected growth (with L3 fix) | High — one gate, one recipe, one engine; abort policy parameterized; duplication eliminated | Medium — touches the proven executor; mitigated by existing canary e2e + new AST pins (T2/T3) + default-OFF flag | Medium | ✅ **Recommended** |
| **(iii) Turn-end evaluation** (post-agent_node hook instead of pre-dispatch) | Med-High — new hook site, bus_pending interplay | Medium | Medium — third evaluation site unless it replaces pre-dispatch | High — child reports arrive immediately after turn end (bus_pending=1), racing the compaction; checkpoint shape at that instant is less uniform | Medium | ⏸ Deferred — Phase-3 option only if soak shows dispatch-hold pain |
| (c) Internal `/compact` via CommandRegistry (full pipeline reuse) | — | — | — | **🔴 Self-deadlock, verified** | — | ❌ Rejected — see Q1 |

**Dominant axis:** Maintainability. (ii) wins because the no-path-split constraint is satisfied at the layer where the paths genuinely share semantics (gate, engine, persist), while the layers that differ by nature (orchestration, progress reporting, pause semantics) stay separate deliberately — with the divergence reasons documented (§3.4) instead of implicit.

---

## 3. Recommended Design

**"Inverted-shape gate + shared status gate + shared Variant-A persist seam + honest numerator — behind a default-OFF flag."**

### 3.1 Gate rework (`instance_messaging.py:1196-1200`)
- Skip at **INFO** if instance status ∈ `COMPACT_REJECT_STATUSES` (`{"terminated","error","failed"}` — import from `command_dispatcher.py:123-125`; do NOT duplicate a third frozenset).
- Skip at **INFO** if checkpoint is **NOT** terminal-shaped — polarity **inverted**: at pre-dispatch, quiescent shape (`state.next == ()`) is the REQUIRED precondition, not a rejection.
- Else invoke the engine (`force=False` — proactive respects dedup/recency floors, unlike the command's `force=True`).

### 3.2 `is_retry` skip removal (`instance_messaging.py:3750-3751`) — Phase 2, load-bearing
Replace the blanket `if not is_retry` with the same status+shape gate. The original WARN-5 rationale (don't compact a mid-flight resume) is moot at pre-dispatch: by definition the checkpoint is quiescent-shaped there. **Without this, the headline 810-msg orchestrator scenario is fixed on paper only** (L2). Carve-out note: the watchover fallback lane already arrives with `is_retry=False` and is unaffected.

### 3.3 Shared persist seam (lift from `compact_executor._persist_compaction_result`)
- Lift `_persist_compaction_result` (`compact_executor.py:1537`, the no-`as_node` pair at `:1665-1681`, order-pinned "nothing between them" `:1544-1546`) into a shared module consumed by both sites. Sentinel ordering (REMOVE_ALL_MESSAGES as element 0, `build_sentinel_replacement` `compaction.py:249, 296-298`) and the `CompactionAborted` pre-write guard (checkpoint untouched on abort) come along verbatim.
- This also eliminates the verbatim-duplicated B1+B2 `compacted_ids` derivation block (`instance_messaging.py:1283-1307` ≡ `compact_executor.py:1614-1641`).
- **Parameterize** (verified asymmetries the seam MUST carry): (a) abort policy — executor **re-raises** `CompactionAborted` (`:1647-1662`), proactive site currently **returns silently** (`instance_messaging.py:1299-1306`); (b) `force` flag (True/False); (c) the compaction message tap (`MessageTapSlot` / `SOURCE_COMPACTION_MESSAGING`, `instance_messaging.py:1330-1345`) exists ONLY on the proactive path — keep it at the proactive call site, not in the seam.
- The proactive path's current `as_node='agent'` persist (`:1308-1319`) is **retired** — hardening per §1.1.

### 3.4 What is shared vs separate (the no-path-split adjudication)

| Shared (one implementation, both consumers) | Proactive-only (deliberate) | Command-only (deliberate) |
|---|---|---|
| `COMPACT_REJECT_STATUSES` status frozenset | Message tap on persist | CommandStateRegistry slot mgmt (one-active-slot would BLOCK the user's manual /compact if shared) |
| Engine invocation (`compact_state`) | `force=False` | SSE `phase_seq` progress cards (FE 7-state machine) |
| Variant-A persist recipe + sentinel + guard + B1/B2 derivation | Fail-open abort policy | pause→quiesce→resume (self-deadlocks if invoked from gate-holding proactive path — V7) |
| Gate-skip observability (INFO/WARN) | Pre-dispatch placement | Dispatcher rate-limit, terminalize step |

**Why full pipeline reuse is infeasible (documented, per the hard constraint):** the proactive path runs INSIDE the per-instance ExecutionGate-held dispatch section (`message_processing_pipeline.py:432-437` → `_do_process` → `_process_message_with_tracking`). If it then invoked the command pipeline: (1) pause→quiesce burns the 30s quiescence timeout against its own in-flight claim (`compact_executor.py:231, 841-848`); (2) the executor re-acquires the SAME non-reentrant per-instance lock (`compact_executor.py:1206`; `execution_gate.py:127-134` — "blocks until the holder releases", no owner tracking) → **self-deadlock**; (3) resume-in-finally re-acquires again (`manager.py:9600`). Additionally the dispatch task registers in `_graph_tasks` only AFTER compaction (`instance_messaging.py:3987-3991`), so pause's cancel would not even find it. The 9-step pipeline is the interactive wrapper; the shared core is the layer beneath it.

### 3.5 Numerator/budget coherence (L3 fix — both sides or neither)
- Include injected-message tokens in the **gate numerator** (they occupy real window space — honest trigger, matches what the LLM sees).
- Include them in the **relief-budget math** (target computation), while injected messages remain **non-selectable** (they must survive; re-attach paths unchanged).
- **Anti-refire policy (critical):** when injections dominate and compacting all regular groups cannot durably reach target, abort early + rate-limited **WARN** + **stamp `compacted_at`** (engages the 60s dedup at `compaction.py:1771-1774`). Verification confirmed the livelock mechanism: a numerator-only change makes the gate re-fire **per dispatch** (engine returns None un-stamped → dedup never engages; the `min_messages` gate at `:1798-1805` is a second un-stamped re-fire path).
- **FE badge unchanged** (`_compute_context_usage` keeps counting all messages — it becomes CONSISTENT with the gate instead of divergent). `tokens_saved` F3 asymmetry (documented deliberate) may stay as-is; only the threshold numerator moves.

### 3.6 Observability
- Terminal-status skip, shape skip, threshold skip → **INFO** (currently DEBUG, invisible in prod).
- Rate-limited **WARN** at ≥90% of threshold and on injections-dominate skips.

### 3.7 Kill-switch
`compaction.proactive_enabled` (env `ENSEMBLE_PROACTIVE_COMPACTION`), **default OFF**; OFF = byte-identical behavior to today. Manual `/compact` remains the working fallback throughout.

---

## 4. Answers Q1–Q7

**Q1 Unification shape → (b) shared lower-layer seam.** (a) direct executor-core invocation is rejected because the executor's orchestration wrapper (pause/quiesce/terminalize) is wrong for a gate-holding caller — only its persist+gate core is reusable; (c) internal command dispatch self-deadlocks (§3.4). Correct machinery for proactive: status gate + engine + Variant-A persist + tap. Harmful machinery: slots, SSE `phase_seq`, pause→quiesce→resume, rate-limit. The quiescent fast-path (V8: `compact_executor.py:817-818, 972, 1261-1263` — `needs_pause_resume = run_status == "running"`; idle/paused fall through) confirms the executor ALREADY treats quiescent instances correctly — which is exactly why the seam lives beneath it, not above it.

**Q2 Placement → keep pre-dispatch primary; replace `is_retry` skip with the shape guard (P2); turn-end deferred (P3).** Pre-dispatch checkpoint is quiescent-shaped by construction (the gate guarantees it post-fix). Turn-end races bus_pending child reports (leader → WAITING_CHILDREN with bus_pending=1; reports arrive immediately after turn end) and adds a second evaluation site with less-uniform checkpoint shape. `is_retry` blanket skip is superseded — it was deliberate (WARN-5) but its premise (mid-flight resume) does not hold at pre-dispatch; without its removal the flagship scenario stays broken.

**Q3 Persist → Variant A (two `aupdate_state` WITHOUT `as_node`), via the shared seam.** Verified safe for quiescent checkpoints by the C1 canary (`test_compact_executor_revive_brick_e2e.py:580-655`); `next` untouched → resume handles (`resume_target_turn_id`) and revive-on-send unaffected; sentinel-element-0 ordering preserves channel order; MessageTapSlot idempotent and stays at the proactive call site. `as_node='agent'` on a quiescent checkpoint is the documented brick **only under `interrupt_before=['agent']`** (§1.1) — no production agent carries it today, so the swap is hardening, not repair. Correctness of the current code is an accident of config + version behavior; Variant A removes the dependence.

**Q4 'completed' → reuse `COMPACT_REJECT_STATUSES`, no special handling.** Verified ordering: revive happens at enqueue inside `_prepare_enqueued_message` (`instance_messaging.py:1820-1830`, commit `:1865`) — strictly BEFORE dispatch — so the proactive gate observes `running`, never `completed`. The narrow window (message enqueued mid-turn, dispatches after turn ends `completed`) lands on a compact-ELIGIBLE completed checkpoint under C1 Variant A (`compact_executor.py:46-53`) — benign. Terminate-race TOCTOU is already doctrine (`:40-44` defense-in-depth): a mid-compaction terminate leaves a compacted checkpoint nothing runs against.

**Q5 Numerator → include injected in gate numerator AND relief budget; do NOT align FE badge down.** Leader's Option B as posed is incomplete → per-dispatch refire loop (§3.5). Injected messages remain non-selectable; injections-dominate → skip+WARN+stamp. Blast radius: F3 `tokens_saved` asymmetry untouched (deliberate, may stay); `TestWindowGatedAtSessionWindow` / `TestWindowMathFollowsCompactionModel` / `tests/unit/test_compaction_model_config.py` need updating for the numerator+budget change (expected, not regression); FE badge contract unchanged (T8) — and it becomes consistent with the gate.

**Q6 → §2 matrix + §5 phasing + §6 anchors.**

**Q7 Concurrency → inline-at-dispatch acceptable for Phase 1.** Verified: compaction runs inside the dispatch's own gate-held section (`message_processing_pipeline.py:432-437`), so it cannot deadlock its own trigger; the engine takes no gate; cross-instance dispatches unaffected (per-instance lock, `execution_gate.py:107-112`). **Caveat (restated from verification):** the ≤300s adaptive cap (`compaction.py:990-1000`; `timeout_cap_s=300.0` `config.py:787-791`) bounds each compaction LLM call, NOT a sibling dispatch's wait — a same-instance sibling blocks for the ENTIRE turn (compaction + full `astream`), and the gate has no timeout (`:131-134`). Bounded in aggregate by the 3600s watchdog and by rarity (trigger fires at 480k tokens), but this is the honest number for the UX trade-off. Background deferral remains a Phase-3 option if soak shows pain.

---

## 5. Phased Implementation Sketch

| Phase | Contents | Gate to next |
|-------|----------|--------------|
| **P1 Core** (one PR) | Inverted gate + shared status frozenset import + shared Variant-A persist seam (retire `as_node='agent'`, dedup B1/B2 block) + numerator/budget coherence + anti-refire policy + INFO/WARN logs + `proactive_enabled` flag **default OFF** | All anchors green; user flips flag |
| **P2 Leader coverage** | Remove `is_retry` blanket skip (shape guard replaces WARN-5) | Soak: `waiting_children` leaders auto-compact on report turns (log-verified) |
| **P3 UX option** (conditional) | Turn-end / background pre-compaction | Only if soak shows dispatch-hold pain |
| **P4 Engine capacity** (independent) | Scale chunk budget for 800+ msgs; document partial-summary degrade ("budget exhausted after 12/26 batches", ebf542de) | Independent of P1–P3 |

---

## 6. Test Anchors

- **T1 Gate polarity:** quiescent-shaped + running/idle/waiting_children → proceeds; status ∈ reject-set → INFO skip; non-quiescent shape → INFO skip.
- **T2 Revive canary (proactive entry):** extend `test_compact_executor_revive_brick_e2e.py` harness to the proactive path on a real graph — pin no-`as_node` immunity INCLUDING a `interrupt_before=['agent']` config (the brick repro), so the hardening is pinned, not assumed.
- **T3 AST persist-identity pin:** shared seam issues NO `as_node=`; sentinel is element 0; two ordered writes, nothing between.
- **T4 Numerator/budget:** injected-in-both; injections-dominate → skip + single rate-limited WARN + `compacted_at` stamped (no per-dispatch refire; assert dedup engages).
- **T5 `is_retry` both shapes:** primary resume lane compacts under the new gate; watchover fallback lane (`is_retry=False`) unchanged.
- **T6 Anti-drift pins:** ONE frozenset, THREE importers (command_dispatcher gate, proactive gate, tests); canonical `TERMINAL_INSTANCE_STATUSES` tripwire `tests/unit/tools/test_instance_tools.py:199-201` stays green.
- **T7 Observability:** INFO skip logs + WARN ≥90% anchors.
- **T8 FE badge contract:** `_compute_context_usage` output unchanged.

---

## 7. Risks

- 🔴 **Refire-loop regression if L3 lands half-done** — numerator without budget/anti-refire policy creates a per-dispatch engine storm (V4-verified mechanism). T4 is the acceptance gate.
- 🔴 **P2 skipped = incident class unfixed** — gate+P1 alone leaves the 810-msg leader scenario intact (L2). Phase-2 is not optional polish; it is the fix for the reported victims.
- 🟡 **Executor regression surface** — the shared seam touches the proven `/compact` executor; mitigated by canary e2e, AST pins, default-OFF flag, manual `/compact` unaffected while OFF.
- 🟡 **Whole-turn sibling-dispatch hold** — same-instance dispatches behind a compacting turn wait compaction + full `astream` (no gate timeout). Acceptable pre-soak; P3 is the relief valve.
- 🟢 **Engine capacity at 800+ msgs** — partial-summary degrade once auto-compaction actually fires; P4, independent.
- 🟢 **Single-source council synthesis** — mitigated by the verification pass (all 8 claims adjudicated: 7 CONFIRMED, 1 PARTIAL-with-correction). Residual model risk low; claims are code-pinned.

---

## 8. Open Decisions (user)

1. **Flag default** — OFF + manual flip after anchor-green (**recommended**) vs ON + kill-switch.
2. **P2 scope** — `is_retry` removal in-scope now (**recommended** — without it the flagship scenario stays broken) vs deferred.
3. **Injection-dominated policy** — skip+WARN+stamp (**recommended**) vs best-effort compact-regular-anyway.
4. **Dispatch-hold UX** — accept whole-turn sibling hold for Phase 1 (**recommended**) vs pre-commit to P3 turn-end.
5. **P4 appetite** — raise engine budget now vs ship P1/P2 and observe.

---

## 9. Evidence Index (verification pass, tree @ 13782089)

`instance_messaging.py`: :1051 (FE badge numerator) · :1177-1189 (collapse doc) · :1196-1200 (shape gate) · :1264/:1283-1319 (proactive persist + B1/B2 dup + `as_node='agent'`) · :1299-1306 (silent abort) · :1330-1345 (message tap) · :1820-1830/:1865 (revive at enqueue) · :3750-3751 (`is_retry` skip) · :3987-3991 (`_graph_tasks` registration)
`compact_executor.py`: :46-53, :58-62 (C1 doctrine) · :231, :817-818, :841-848, :972, :983-1000 (quiescent fast-path, quiescence) · :1206 (gate re-acquire) · :1537, :1544-1546, :1564-1571, :1614-1641, :1647-1681 (`_persist_compaction_result`, Variant A, B1/B2, abort)
`compaction.py`: :77-79 (injected must survive) · :249, :296-298 (sentinel) · :990-1000 (adaptive timeout) · :1696-1741 (`_trigger_window`) · :1771-1774 (60s dedup) · :1780-1782, :1798-1805, :1807-1808, :1824-1828, :1838-1849 (numerator/selection/min_messages)
`command_dispatcher.py`: :123-125 (`COMPACT_REJECT_STATUSES`)
`_checkpoint_utils.py`: :45-105 · `execution_gate.py`: :107-112, :118-144 (per-instance lock, no timeout)
`message_processing_pipeline.py`: :400-437 (gate-held dispatch) · `manager.py`: :9592-9600 (resume lane `is_retry=True`, re-acquire) · `task_processor.py`: :354 · `watchover_service.py`: :676, :722
`context_messages.py`: :106-110 (injected stamp) · `graph.py`: :3512-3547 (CLE backstop) · `config.py`: :787-791 (`timeout_cap_s`)
Tests: `test_compact_executor_revive_brick_e2e.py` (:14-19 header, :192-311 brick, :580-655 canary) · `tests/unit/tools/test_instance_tools.py:199-201` (canonical tripwire)
Prior art: `.agents/shared/planning/compact-on-completed/architecture-recommendation.md` (2026-08-31) — revive-location drift corrected to `:1820-1838` by verification.


---

## ADDENDUM (2026-09-04): Mid-Turn 95% Pre-Call Reactive Trigger

Added post-main-recommendation per user requirement. Verified on the current tree by worker `08d6dfe8` (`data-flow-design`); all line pins below are from that pass (tree had moved — CLE persist re-pinned `graph.py:3583` → `:3606-3608`). Main recommendation body above is NOT rewritten; this section amends it where noted.

### A.1 Requirement
A SECOND reactive trigger at **0.95 × `_trigger_window(...)`** (0.95 × min(session, override) = 570k on a 600k window), evaluated **before each LLM call, mid-turn**, in addition to (not replacing) the CLE trigger. Purpose: catch mid-turn context explosions (huge tool results, injected child reports) that the pre-dispatch proactive 80% check structurally cannot see — BEFORE the provider call fails.

### A.2 Decision-register updates (supersede main-doc §8 where stated)
- **Open Decision #1 RESOLVED (user, 2026-09-04):** `ENSEMBLE_PROACTIVE_COMPACTION` ships **default ON**, single kill-switch semantics preserved (OFF = one-flag revert path; see A.8).
- Open Decision #2 (P2 `is_retry` scope) and #3–#5 remain open.

### A.3 Hook site (Q1) — pinned: `daemon/graph.py:3504-3511`
Insert immediately inside the `try:`, **after `_maybe_repair_loop`** (`:3424-3437`) and **before the invoke** (`run_in_executor(..., current_llm.invoke(full_messages))`, `:3508-3511`). NOT a middleware slot, NOT inside the CLE handler. Rationale: the check must observe the post-repair LLM-bound payload (the loop breaker rebuilds/drops items, `graph.py:1771-1778`); the site is a sibling of the CLE handler so `compactor`/`graph_ref`/`thread_config` are already in scope (closure locals of `create_agent_node`, `:2716-2734`); middleware sees the channel dict, not `full_messages` with system prompt + injections prepended (`:2830`) — wrong abstraction layer.

**`usage_metadata` verdict:** reachable (captured `:2282-2283`, survives the reducer into the checkpoint `:2110-2111`) but a **stale/undercounting proxy** — first call in a turn has no same-turn AIMessage; mid-loop the last AIMessage undercounts by the new tool result; non-compliant backends return None (`:2347-2381`). The drift is exactly the explosion the hook exists to catch. **Use only as a secondary cross-check; primary signal = estimator.**

### A.4 Estimator cost (Q2)
Facts (`daemon/loader.py:450-520`): no cache, up to 4 sub-encodes/message, pure-Python loop → **~150–200 ms at 800 msgs / ~500k tokens** (encode ~3–10 ms + ~160 ms loop overhead). Non-trivial on every call of a multi-call tool-loop turn.

**Adopted mitigation:** (c) **O(1) pre-filter** — per-instance `last_estimate: (msg_count, total_tokens)` on the compactor (sibling of `last_compacted_at`); skip the estimator unless the message count grew OR the last estimate was ≥0.80×window; plus (b) `usage_metadata` as cross-check only. Common case O(1); the ~200 ms worst case is confined to the 80–95% band, where the call is already at risk. **Rejected:** (a) incremental id-hash cache — fragile across sentinel re-ids (`compaction.py:1880-1882` re-mints ids) and cache-lookup cost approaches encoding cost.

### A.5 Persist-recipe resolution (CRITICAL — amends §3.3)
**KEEP `as_node='agent'` for mid-turn sites; do NOT adopt Variant A mid-superstep (unproven in this codebase). The shared seam gains a `mid_turn: bool` parameter:**
- `mid_turn=True` → `as_node='agent'` (CLE handler `:3606-3608` + the new 95% hook)
- `mid_turn=False` → no `as_node` (Variant A: executor; proactive path post-§3.3 retirement)

Evidence — three persist sites, two frames: proactive (in-frame, quiescent) `instance_messaging.py:1308-1320`; executor (out-of-frame, quiescent) `compact_executor.py:1668-1680`; CLE (in-frame, **mid-superstep**) `graph.py:3606-3608`. The C1 docstring (`compact_executor.py:1551-1571`) explicitly distinguishes the frames; §1.1's `interrupt_before` finding covers **quiescent** checkpoints only — mid-superstep `aupdate_state` without `as_node` is untested here. **AMENDMENT to §3.3:** the seam's parameter list is now (abort policy, `force`, tap, **`mid_turn`**). Gate any future recipe change at the CLE handler on the mid-superstep canary (A.9 T2-ext).

### A.6 Composition + refire safety (Q3) — VERIFIED
- **Abort → no stamp:** `build_sentinel_replacement` raises `CompactionAborted` (`compaction.py:412`) before any `aupdate_state`; the stamp happens inside `compact_state` on success (`:1851`, `:3339`). So a proactive 80% abort does NOT engage the 60s dedup (`:3391-3407`) — the 95% hook can fire later in the same turn. Compose confirmed.
- **Success stops refire:** `compacted_at` written mid-turn (`:3607-3608`) → subsequent same-turn calls read it (`:3524`) → `_is_recently_compacted` (`:1771-1773`) returns None → stop re-triggering. Emergency truncation always lands at `target_ratio` (≤ ~0.5×window) below 95% (`:1860-1922`) → durable relief even for the 810-msg class.
- **Injection-dominated no-op paths do NOT stamp** (all-injected early-return `:1793`; `min_messages` `:1798-1804`) → the per-call refire risk exists here too; the §3.5 anti-refire policy (skip + WARN + **stamp**) applies to the 95% hook identically (covered by T4-ext).
- **Same-call conflict impossible** (95% is pre-call, CLE is post-failure); same-turn sequences safe per Q3c analysis.

### A.7 CLE isolation (Q7) — VERIFIED
CLE is not transient (`llm_error_classifier.py:387, 433-479`); tenacity does not retry it; the "single retry" is purely the in-handler re-invoke (`graph.py:3717-3720`). The 95% hook is a disjoint path before `:3508`; the CLE persist (`:3606-3608`) and retry are untouched. Turn resumability: same in-invocation pattern (persist → `aget_state` → rebuild → re-invoke → return; `:3606-3608`, `:3635`, `:3704-3712`, `:3717-3720`) — no re-dispatch, no graph restart.

### A.8 Flag + phase (Q4, Q5)
- **Q4 — single flag governs both triggers** (`ENSEMBLE_PROACTIVE_COMPACTION`, default ON): both are one feature (the auto-compaction safety net) consuming one seam; independent flags create a meaningless mixed state (proactive ON + 95% OFF re-opens the exact mid-turn hole this requirement closes) and double the revert matrix — against the user's one-kill-switch preference and house kill-switch doctrine. The **CLE trigger stays ungated** (pre-existing last-resort behavior; gating it would change today's behavior — out of scope). 🟢 Naming note: the flag name says "proactive" but now governs a reactive trigger; accept the name (avoid churn), document the widened semantics at the config site.
- **Q5 — own small phase P1b, immediately after P1** (it consumes P1's seam + numerator fix; independent of P2). P1b scope: hook + `mid_turn` seam param + tap label + pre-filter state + anchors. Resulting coverage ladder: **80% pre-dispatch → 95% pre-call → CLE ~600k**.

### A.9 Test anchors (Q6) — extends §6
- **T2-ext (mid-superstep canary):** extend `test_compact_executor_revive_brick_e2e.py` with a turn-in-flight graph exercising `as_node='agent'` AND no-`as_node` mid-superstep; gates any future recipe change (A.5).
- **T4-ext (multi-call refire loop):** a turn with N LLM calls crossing 95% compacts ONCE (dedup stamped); injection-dominated no-op stamps + single WARN (no per-call refire).
- **T-estimator (perf):** below-80% pre-filter path invokes the estimator ZERO times across a multi-call turn (assert call count); estimator runs only when count grew or last estimate ≥0.80×window.
- **T-boundary:** 0.9499×window no-fire / 0.9501×window fire (≈569.9k / 570.1k on 600k).
- **T-isolation (CLE):** 95% fire does not consume/reset the CLE single-retry; CLE persist site byte-unchanged; hook-then-CLE in one turn behaves per A.6/A.7.
- **T-tap (gate — missed by the leader's sketch):** `compaction_tap_slot`'s AST gate (`test_hook_placement`) requires EXACTLY 4 distinct source labels; adding the hook without a label decision fails the gate. Add `SOURCE_COMPACTION_PRECALL_95` (or document reuse of the reactive label) — decide in P1b.

### A.10 Risks added
- 🟡 Seam must carry the `mid_turn` parameter (A.5) — §3.3 as written covers only quiescent sites; P1b amends it before a third consumer exists.
- 🟡 Tap-label AST gate (exactly 4 labels) — a P1b-blocking detail absent from the leader's sketch.
- 🟢 `usage_metadata` staleness — documented; never the primary signal.
- 🟢 Estimator latency ~150–200 ms worst case — confined to the 80–95% band by the O(1) pre-filter.
- 🟢 Rare stuck case (even emergency truncation CLEs) — pre-existing CLE behavior; the 95% hook does not worsen it (A.6).

### A.11 Corrections to the leader's sketch
1. "Wherever the full message list is available" → pinned to the post-loop-repair pre-invoke site (`graph.py:3504-3511`); middleware and CLE-neighborhood placements rejected with reasons (A.3).
2. "Mid-turn-proven persist approach" → confirmed as `as_node='agent'`, NOT the lifted Variant-A recipe; the seam is parameterized per-site instead (A.5).
3. Sketch omissions surfaced by verification: tap-label AST gate, estimator cost mitigation, injection-dominated refire stamping — all specified above (A.4, A.6, A.9).
