# Decisions — Hallucination-Recovery Ladder

Date: 2026-09-13
Author: planner[v2] via technical-analysis worker
Status: Draft — ADRs proposed for architect enrichment; OPEN QUESTIONS section awaits rulings.

**Observed git state (verified before writing):**
- Branch: `plan/hallucination-recovery-ladder`
- HEAD: `0acd3afae2d7f25e8c15d44f7802c4f23f5975b1` (short `0acd3afa`), clean tree.
- Companion document: `technical-analysis.md` (same directory) — all anchors verified at this HEAD; the analysis records one correction to the research buffer (`wrap_langchain_failover` DOES exist at `daemon/services/llm_failover.py:617` with prod callers `compaction.py:3383/:3395`, contradicting the buffer's "DOES NOT EXIST" symbol correction).

---

## ADR-0001 — Unified repair engine with per-class surgical presets

**Context.** The user vision mandates a single cheap-first escalation ladder for ALL hallucination-class symptoms, generalized from the loop breaker (`LoopDetector` `daemon/graph.py:977` / `LoopRepairer.repair` `:1320`), explicitly forbidding a parallel duplicate. Candidate classes have materially different evidence shapes (toolcall units, empty messages, truncated partials, ghost-promise tails).

**Decision.** ONE generalized repair engine (evolution of `LoopRepairer`, same pre-LLM middleware slot `graph.py:5186` plus router-level entry points) with a per-class PRESET TABLE declaring: evidence-window selector, summary-prompt fragment, retention set, post-repair routing. One persistence carrier (return-carried sentinel-first prefix), one durable budget, one telemetry surface (`[SYMPTOM]`).

**Consequences.** New class enrollment = new preset row + detector, not new machinery. Risk concentrates in one engine (single point of failure — mitigated by fail-open abort). Per-class FP behavior is data-isolated.

**Alternatives rejected.**
- *Per-class parallel mechanisms*: duplicates sentinel/persistence/budget/telemetry N times; the exact duplication the vision forbids; N× maintenance.
- *Full `compact_state` for all classes*: engine gates refuse the target cases (60s dedup `compaction.py:2098`, min-messages `:2198-2208`; `force=True` bypasses ONLY threshold `:2222-2227`), cost disproportionate (batched-20 × concurrency-3 + serial merge `:2800-2861`), and destroys non-degenerate evidence in the window.

---

## ADR-0002 — Phase-1 cut: durable conversion of the loop-breaker rung (NOT transient-first)

**Context.** Loop repair today is TRANSIENT: in-memory filter only (`graph.py:1504-1556`); `RemoveMessage` sentinels never fed to state (`:1291-1310`/`:1413-1420`/`:1519-1525`); restart/revive replays original degenerate history; RAM budget resets. Leader ladder requirements: checkpoint-committed repair + durable budget counter are MANDATORY. Mid-turn durable persistence is return-carried ONLY (canary `test_compact_executor_revive_brick_e2e.py:1746`; sentinel recipe `compaction.py:427-507`).

**Decision.** Phase-1 = loop-breaker class ONLY, converted to durable: (1) removal-builder output (`graph.py:1477-1501`) feeds a return-carried sentinel-first prefix at node-return assembly; (2) durable per-task repair budget (GraphState field); (3) summarizer through the HA facade; (4) exhaustion → loud escalation. NO new class enrollment in phase-1.

**Consequences.** The risky part (the durable carrier) is proven on the one class with battle-tested detection before new classes ride it. A transient-first cut is explicitly rejected: it would ship the restart-replay defect by construction for any new class enrolled on it. Phase-2+ classes inherit the proven carrier (their marginal risk is detector-FP only).

**Alternatives rejected.**
- *Transient-first for everything, durable later*: reproduces the known defect on day one; a later durability conversion would re-touch every class.
- *Big-bang (all classes + durable carrier at once)*: conflates carrier risk with detector-FP risk; unsoakable.
- *Durability deferred to phase-2*: violates the leader requirement and leaves the flagship class broken across restarts.

---

## ADR-0003 — Where repair slots vs the shipped empty-guard ladder (per class)

**Context.** The shipped Option-4 ladder for empties is: nudge → 2nd-empty raise INSIDE retry scope (`daemon/llm_error_classifier.py:907` `_run_with_classification`, validate call `:916`) → transient retry (same poisoned context) → failover → loud ERROR (`graph.py:5516-5519`). The ladder thesis: repair belongs BETWEEN nudge-failure and retry exhaustion. Constraint: do NOT regress S1 raise-in-retry-scope, S5 derived caps, L1–L13, kill-switch byte-identity.

**Decision.** Conceptual slot honored; mechanical placement per class:
- **Router-detected classes** (ghost-promise; optionally S5-degenerate): repair AT DERIVED CAP, routing to a repair-flagged re-entry instead of the bare re-invoke (`graph.py:2601` for ghost) — genuinely between rung-1 and backstop, zero facade contact.
- **Raise-lane classes** (S1 empty; truncated via `LLMResponseValidationError` in retry scope): repair is **PRE-TERMINAL** — after the shipped ladder exhausts (raise → retries → failover), intercept ONCE, repair, re-enter; symptom-persist-after-repair → the shipped loud ERROR fires unchanged. Budget-gated; kill-switch-gated.
- **Facade-hook alternative** (repair on 2nd raise, replacing retries 2-3) is DEFERRED (OQ1): it saves ≤2 poisoned re-sends but touches the review-approved raise-in-retry-scope contract.

**Consequences.** Option-4 contracts structurally intact (audit in technical-analysis.md DQ1-c). Cost: ≤ `PRIMARY_TRANSIENT_MAX−1` wasted poisoned-context retries before repair fires — bounded and accepted for phase-2. With flag OFF or budget exhausted, terminal path is byte-identical to shipped.

**Alternatives rejected.**
- *Repair inside the facade retry loop now*: re-opens the approved contract; the raise's classification/lane semantics (`TRANSIENT_EXCEPTIONS` `:433-476`, `RetryByCategory` `:696-750`) were review-pinned.
- *Repair only AFTER the ERROR (post-terminal)*: a post-terminal repair cannot CONTINUE the task (terminal states route to error reporting, `manager.py:8469-8499` region); violates "continue the task" vision.

---

## ADR-0004 — Escalation-state home: derived-per-turn counts + ONE durable per-task budget

**Context.** Three in-tree precedents: S5 derived-from-tail zero-state counting (`graph.py:2651-2666`); loop-breaker RAM counter with clean-turn auto-reset (`:1848-1855`); language-check checkpoint-persisted fields (`:2456-2459`, explicit restart-survival rationale). Manager RAM streaks exist as non-gating telemetry (`[LLM-EMPTY]` `:5552-5577`).

**Decision.** Per-turn symptom detection: DERIVED from history wherever the symptom leaves tail evidence (self-clearing after repair — a feature, not a bug). Per-task repair budget: ONE durable GraphState field (language-check pattern). Per-class WARN streaks: RAM, telemetry-only, non-gating. No manager-side gating state.

**Consequences.** Restart/revive survival exactly where required (budget); zero migration surface for counts; the repair doc's presence in checkpointed history is the durable provenance record. Two-axis scheme is more concepts than a single counter — mitigated by documenting the axis rule ("evidence in tail → derive; budget → persist").

**Alternatives rejected.**
- *All-derived including budget*: budget resets on restart → deterministic re-trip (the defect being fixed).
- *All-durable per-class counters*: N new GraphState fields + migration surface for what history already documents; drift risk between counter and history.
- *Manager-RAM gating state*: restart-resets (ground truth #3), and Option-2's manager-scoped design was already rejected by the empty-guard council for cross-turn blindness (architecture-recommendation §5 Option 2).

---

## ADR-0005 — Budget-counter durability + exhaustion escalates (close the WARN+continue hole)

**Context.** `max_repairs=3` is RAM-only (`daemon/manager.py:754`, `:3898-3927`); exhaustion = WARN + continue with ORIGINAL messages (`graph.py:1857-1864`, verified). The WARN is log-only — the model never sees it; continuing on original degenerate history deterministically re-trips the detector. This is an escalation hole the design mandate requires closing.

**Decision.** (a) The authoritative repair budget is the durable GraphState counter (value 3 proposed, mirroring `max_repairs` semantics; reset policy → OQ5). (b) On exhaustion, the ladder ESCALATES to the class's loud terminal backstop with `[SYMPTOM] … phase=terminal reason=repair-budget-exhausted` telemetry, instead of continuing with original messages. (c) With the kill-switch OFF, shipped WARN+continue is preserved byte-identically (the hole closes only under the flag).

**Consequences.** No more silent infinite re-trip burn within a graph run; operators get a grep-able exhaustion signature. Behavior change is flag-gated; existing tests pin the OFF path.

**Alternatives rejected.**
- *Keep WARN+continue*: deterministic re-trip burn; the model cannot self-correct from a log line it never sees.
- *Exhaustion → inject a "you are looping" nudge*: an append-only nudge into degenerate history is rung-1-shaped; evidence shows appended nudges don't fix evidence-poisoned contexts (that is the ladder's premise).
- *Exhaustion → auto-compact full history*: ADR-0006/ADR-0001 analysis — wrong tool, gates and cost.

---

## ADR-0006 — Repair summarizer routed through the HA facade; fail-open abort

**Context.** `LoopRepairer`'s summarizer BYPASSES the facade — verified at `daemon/graph.py:1642-1677`: `clean_llm_config` `:1642` → bare `ThinkingChatOpenAI` `:1643` → `llm.invoke` via `asyncio.to_thread` `:1649-1651` → `wait_for` `:1662` → STATIC fallback string `:1632-1636` on timeout or ANY exception (`:1666-1677`). An empty/degenerate repair summary is silently injected into history — itself a hallucination-adjacent artifact. Compaction's summarizer IS facade-wrapped (`compaction.py:3383`/`:3395` via `wrap_langchain_failover`, `daemon/services/llm_failover.py:617` — symbol verified present at HEAD, contra the research buffer). This folds empty-guard Phase-2 deferred item "(3) raw-SDK mirrors: … LoopRepairer" into this program.

**Decision.** The generalized repair summarizer builds its client through `wrap_langchain_failover` (compaction's pattern). Empty/degenerate summarizer output raises inside the retry scope (S1 semantics) → bounded retry → failover → on ultimate failure the repair ABORTS fail-open: no surgery, budget NOT consumed, fall through to the next rung, loud `[SYMPTOM] … repair_abort` telemetry. Never wedge the turn. Persist-refusal (`persist_compaction_result` False on fail_open, `_compaction_persist_seam.py:84-90`) is treated as abort. Static truncation fallback: retained only as last-resort-with-loud-telemetry or removed — architect's call (sub-question of OQ1/OQ7).

**Consequences.** The repair rung cannot itself become an empty-loop; a degenerate summary can no longer silently enter history. Repair availability now depends on provider health — acceptable because abort falls through to the existing backstops.

**Alternatives rejected.**
- *Keep bare invoke + static fallback*: the verified silent-degrade defect; also the only summarizer in the codebase outside the facade (inconsistent by construction).
- *Fail-closed on summarizer failure (error the turn)*: a summarizer outage must not take down an otherwise-healthy task; compaction's truncation-fallback precedent (L11) argues for degrade-with-fallback or skip, never wedge.

---

## ADR-0007 — Class enrollment: now / later / never

**Context.** Four candidate undetected/under-handled classes plus the already-detected gaps. Detection maturity, FP risk, and partition exposure differ per class (technical-analysis.md symptom table + enrollment section).

**Decision.**
- **Phase-1 (now):** loop-breaker durability conversion (the generalization target itself; no new detection).
- **Phase-2 (now, after phase-1 soak):** ghost-promise (detection exists `graph.py:2596-2601`; converts an UNBOUNDED burn `:2538-2541` into cap→repair→backstop; router-level placement, cheapest real gap); truncated (detector exists `response_validation.py:452-457`; retry-on-same-context provably wasteful; partial-text preservation ruled by OQ3); S1 post-ladder pre-terminal repair (ADR-0003).
- **Phase-3 (later, detector-gated):** tool storms with drifting params (needs drift/no-progress signature = empty-guard Phase-2 item 5); stuck-without-progress (same prerequisite); schema/format violations (needs generic validator + exemption analysis); repeated identical FINAL answers non-tool (structurally invisible today — `LoopDetector` breaks on plain AIMessages `graph.py:1110-1113`, `should_continue` ENDs on truthy `:2609`; high FP risk, watchover-adjacent).
- **Never:** wrong-language (covered, fail-open `graph.py:2833-2837`); CLE (already a rung-2 shape `:5345-5394` — fix its superseded persist shape `:5396-5398` separately, do not duplicate); bare-JSON provider body (transient lane owns it, `llm_error_classifier.py:466`); watchover-denied classes (3-strike is watchover's — partition exclusivity); GII throttle storms (own escalating backoff).

**Consequences.** Highest-value/lowest-risk first; every phase-3 enrollment is explicitly gated on a detector design it does not yet have. The never-list documents partition non-negotiables so future "while we're here" scope creep is refusable by precedent.

**Alternatives rejected.** *Enroll all four candidates now*: conflates unproven carrier (none, post-phase-1) with unproven detectors; unsoakable FP surface. *Never enroll new classes*: leaves the unbounded ghost burn and truncated waste permanently open — contradicts the vision's "ANY hallucination-class symptom".

---

## ADR-0008 — Kill-switch convention: master + per-class sub-flags; OFF = byte-identical routing, telemetry stays

**Context.** Empty-guard shipped the convention: `ENSEMBLE_EMPTY_RESPONSE_GUARD` default ON, restart-pending, OFF = byte-identical ROUTING with `[LLM-EMPTY]` telemetry intentionally NOT gated (adr-0001 consequences, W1 leader decision KEEP — OFF-mode data is needed during an OFF soak). Resolver convention: explicit `_resolve_*` in `load_config`, invalid → `ValueError` at boot.

**Decision.** Master `ENSEMBLE_SYMPTOM_REPAIR_LADDER` (default ON, restart-pending; OFF = every router branch returns shipped values, middleware slot is a no-op pass-through, telemetry continues). Sub-flag per phase-1 loop enrollment: `ENSEMBLE_REPAIR_LOOP_DURABLE` (default ON). Same `_resolve_*` discipline. Telemetry stays ungated-by-design (W1 KEEP).

**Phase-2 amendment (ADR-0009).** Phase 2 enrolls three new classes (ghost / truncated / empty_post_ladder) WITHOUT per-class sub-flags. The master `ENSEMBLE_SYMPTOM_REPAIR_LADDER` governs ALL FOUR classes (loop + 3 new) — the P1 OFF contract extends: master OFF = byte-identical routing for every class. No `ENSEMBLE_REPAIR_GHOST_PROMISE` / `ENSEMBLE_REPAIR_TRUNCATED` / `ENSEMBLE_REPAIR_EMPTY_POST_LADDER` env vars exist. The sub-flag path documented here is preserved for phase-3 detector-gated classes (tool storms with drift, stuck-without-progress, schema/format violations) where surgical disable of one class without killing the whole ladder is needed.

**Consequences.** Surgical disable of one class without killing the ladder — available NOW for loop (`ENSEMBLE_REPAIR_LOOP_DURABLE`); reserved for phase-3 detector-gated classes. OFF-soak observability preserved; operator muscle memory from the empty-guard rollout transfers.

**Alternatives rejected.**
- *Single master only, no sub-flags ever*: a loop-only FP would force disabling the whole ladder (loop's primary value is the durable carrier; ghost / truncated / empty are detector-FP bounded by their respective cap / L1-L13 / excerpt invariants — phase 2 ships without sub-flags per USER AMENDMENT; phase 3 may revisit).
- *Default OFF soak-first*: contradicts the established default-ON restart-pending convention and the restart-pending activation runbook pattern already in use for this project's features.

---

## ADR-0009 — Phase 2 amendment: NO per-class sub-flags; master governs all classes (USER AMENDMENT 2026-09-13)

**Context.** Phase 2 enrollment of ghost-promise, truncated, and
empty_post_ladder classes (per `phase2-plan.md`) originally proposed
three per-class sub-flags (``ENSEMBLE_REPAIR_GHOST_PROMISE`` /
``ENSEMBLE_REPAIR_TRUNCATED`` / ``ENSEMBLE_REPAIR_EMPTY_POST_LADDER``)
to gate each enrollment independently (ADR-0008 default-ON-with-soak
semantics). On 2026-09-13 the user AMENDED the design: NO per-class
sub-flags. All three enrollments are governed ONLY by the existing
master ``ENSEMBLE_SYMPTOM_REPAIR_LADDER`` (default ON). Master OFF =
byte-identical routing for ALL FOUR classes (loop + 3 new). Telemetry
stays ungated-by-design (W1 KEEP).

**Decision.**
* **B-5 / C-5 / D-5 CANCELLED.** No ``ENSEMBLE_REPAIR_GHOST_PROMISE``
  / ``ENSEMBLE_REPAIR_TRUNCATED`` / ``ENSEMBLE_REPAIR_EMPTY_POST_LADDER``
  env vars exist.
* Master ``ENSEMBLE_SYMPTOM_REPAIR_LADDER`` (default ON, restart-pending)
  governs every class. The P1 OFF contract extends: master OFF =
  byte-identical routing for ALL FOUR classes (loop + ghost + truncated
  + empty_post_ladder).
* Telemetry stays ungated-by-design (W1 KEEP) — OFF soak data is
  needed during soak windows.
* Every new branch (ghost cap-routing in ``should_continue``,
  pre-terminal intercept for truncated/empty in ``agent_node``) is
  gated by the master switch FIRST; gate-OFF preserves the shipped
  byte-identical routing.

**Consequences.**
* One operator surface, one mental model. Muscle memory from the
  empty-guard and ladder phase-1 rollouts transfers directly.
* A FP in any one class requires disabling the WHOLE ladder — mitigated
  by phase-3 detector-gated enrollment (the cap-before-surgery FP
  mitigation on ghost; the L1-L13 exemption table on empty; the
  excerpt-round-trip verification on truncated).
* The ADR-0008 "kill-switch convention: master + per-class sub-flags"
  is SUPERSEDED in part for phase 2 — the sub-flag sub-bullet is
  re-scoped to phase-3 detector-gated classes (not phase 2's three).

**Alternatives rejected.**
* *Per-class sub-flags default ON after soak (phase-2 original)*:
  contradicted by the user's amendment.
* *Per-class sub-flags default OFF until soak*: contradicted the
  established default-ON restart-pending convention; also adds
  operator surface noise.
* *Single master only, no per-class granularity at all*: same as the
  decision; the ADR-0008 "per-class sub-flags" path was for FUTURE
  detector-gated classes (phase 3+) where a specific class might
  need surgical disable without disabling the whole ladder.

---

## OPEN QUESTIONS (for architect enrichment) — RESOLVED on 2026-09-13

**OQ1 — S1-class repair placement: pre-terminal (recommended) vs shallow facade hook.**
- *Blocking:* the facade hook (repair on 2nd `EmptyLLMResponseError`, replacing retries 2-3) saves ≤2 poisoned-context re-sends per incident but modifies the review-approved raise-in-retry-scope contract (`llm_error_classifier.py:907/:916`) and its lane semantics; pre-terminal wastes those retries but is contract-clean.
- *Options:* (a) pre-terminal only, permanently; (b) pre-terminal now, facade hook later behind its own sub-flag after telemetry shows poisoned-retry success rate ≈ 0; (c) facade hook immediately. Recommended: (b).
- **RESOLVED 2026-09-13 (leader ruling): option (b) PRE-TERMINAL placement now; the facade-hook alternative stays deferred — no scaffold for it.** Implemented in `daemon/graph.py::_maybe_pre_terminal_repair` for truncated and empty_post_ladder classes; consumed in `agent_node`'s retry-exhaustion except block. The intercept is gated by the master ladder switch (USER AMENDMENT — no sub-flag).

**OQ2 — Ghost-promise detector conservatism.**
- *Blocking:* shipped detection is bare `content.endswith(":")` (`graph.py:2596-2601`); legitimate colon-ending content (code blocks, list intros) would be capped/stripped once enrolled. No FP profile exists.
- *Options:* (a) enroll with bare detector + derived cap (FP bounded by cap-before-surgery ordering); (b) require a minimum-length/heuristic guard (e.g. only short trailing fragments count) before enrollment; (c) phase-3 until FP soak data exists. Recommended: (a) with sub-flag default ON only after a soak window; FP telemetry via `[SYMPTOM] class=ghost phase=detect`.
- **RESOLVED 2026-09-13 (leader ruling): option (a) — bare `endswith(':')` detector + derived trailing-ghost counter, cap 3, CAP-BEFORE-SURGERY ordering.** Implemented in `daemon/graph.py::_is_ghost_promise_message` + `_count_trailing_ghost_promise_ai_messages` + `GHOST_PROMISE_REINVOKE_CAP=3`. No sub-flag (USER AMENDMENT). The cap-routing branch in `should_continue` is master-gated; below cap the bare `"agent"` re-invoke is preserved byte-identically (OFF-mode).
- **C1 amendment (2026-09-13, post-implementation review):** the original `[SYMPTOM] class=ghost phase=detect` telemetry emission is **gated** by `ENSEMBLE_SYMPTOM_REPAIR_LADDER` (`graph.py:2312-2313` early-return in `_maybe_ghost_repair`, plus `graph.py:3708-3721` cap-routing branch in `should_continue`). The MASTER-OFF byte-identical contract wins; OFF mode produces ONLY the legacy `[Graph] Ghost promise detected, LLM text ends with ':'` WARN at `graph.py:3741` (the shipped pre-phase-2 line). The earlier "W1 KEEP — `[SYMPTOM]` telemetry stays OFF-mode" reading was INCORRECT: the empty-guard `[LLM-EMPTY]` precedent is intentionally ungated, but the `[SYMPTOM]` family for ladder classes (loop/ghost/truncated/empty_post_ladder) IS gated by the master switch (W1 distinction is `[LLM-EMPTY]`-specific). OFF stays truly byte-identical to pre-phase-2 routing; LEADER DECISION: no ungated emissions — operators wanting OFF-soak data use the legacy `[Graph] Ghost promise detected` line plus the master-ON telemetry after the soak window. Implementation change: NONE (the code was already gated); the DOC was wrong.

**OQ3 — Truncated-class partial-content preservation.**
- *Blocking:* repair drops the truncated AIMessage as degenerate evidence — but a `finish_reason=length` partial may contain real user-facing content; silent loss is a data-loss risk.
- *Options:* (a) preserve the partial VERBATIM inside the repair doc (excerpt section); (b) drop it (pure cleanup); (c) do not enroll truncated class at all. Recommended: (a).
- **RESOLVED 2026-09-13 (leader ruling): option (a) — verbatim `excerpt=` preservation in the repair doc.** Implemented in `daemon/services/symptom_repair_engine.py::SymptomRepairEngine._build_repair_doc` (truncated branch); excerpt round-trips through checkpoint serialize/deserialize byte-exact (T-11 / C-7 invariant pinned by `TestTruncatedExcerptRoundTrip`).

**OQ5 — Durable repair-budget reset policy.**
- *Blocking:* the loop breaker auto-resets its RAM counter after a clean detection turn (`graph.py:1848-1855`) — a deliberate freshness heuristic. A durable per-task budget must decide: never reset (long-lived tasks with many GENUINE repairs lock up), reset on clean-turn (re-admits slow-grinding loops), or reset on new user message (task-turn semantic).
- *Options:* (a) never reset within a task; (b) reset-on-clean-turn mirrored durably; (c) reset on new real (non-injected) HumanMessage. Recommended: (c) — aligns budget lifetime with user-visible task episodes; needs architect ruling because it defines "task" for budgeting purposes.
- *Phase-1 ruling:* option (c) was adopted (see phase-1 graph.py:5092-5102). Carried forward unchanged in phase 2.

**E — RAM streak decision (Workstream E).** Per phase-2 plan workstream E:
- The `[LLM-EMPTY]` / `[SYMPTOM]` / `[LOOP BREAKER]` streaks share the
  same RAM-vs-instance-row decision (recommendation: RAM, non-gating,
  telemetry-only — same as the existing `[LLM-EMPTY]` precedent).
- **RESOLVED 2026-09-13 (phase-2 E-1 ruling): RAM, non-gating,
  telemetry-only.** All three telemetry surfaces use the same gating
  helper; consolidation implementation deferred to phase 3 (per OQ7
  ruling — the dual emit during transition is acceptable). No code
  change for E in phase 2 — the existing `[LLM-EMPTY]` precedent and
  the `[SYMPTOM]` dual-emit (F-3) already satisfy the RAM,
  non-gating decision.

**OQ4 — Should S5-cap-exceeded route to repair instead of loud END (phase-3 option)?**
- *Blocking:* S5's loud END at cap is freshly shipped (merge f8ada495, restart-pending) and review-approved; changing its terminal semantics re-opens an approved contract for marginal value (degenerate messages carry nothing to summarize — repair would be a bare drop).
- *Options:* (a) keep loud END permanently; (b) bare-drop repair at cap behind a default-OFF sub-flag after soak; (c) escalate to repair only for the reasoning-only subclass. Recommended: (a) for now; revisit with soak data.
- *Unresolved — phase-3 backlog.*

**OQ6 — Bulk delegation to `compact_state` (hybrid escape hatch) — needed at all?**
- *Blocking:* if a degenerate evidence window can span most of a huge history, targeted-surgery retention may exceed practical sentinel size; delegation to `compact_state` force=True would handle bulk but re-imports the engine gates (dedup `:2098`, min-messages `:2198-2208`) and cost envelope.
- *Options:* (a) phase-3 behind telemetry (only if `[SYMPTOM] repair` events show large-window cases); (b) never (rely on L2/L3 compaction to have bounded history sizes before repair fires); (c) hard size cap on repair window — abort repair if exceeded (fall through to backstops). Recommended: (c) as an invariant, making (a) moot in most cases.
- *Unresolved — phase-3 backlog.*

**OQ7 — Telemetry consolidation + the FE SSE renderer gap.**
- *Blocking:* `[SYMPTOM]` is proposed alongside `[LOOP BREAKER]`/`[LLM-EMPTY]` during transition — when to consolidate is an operator-tooling call. Separately, the FE has NO SSE error-event renderer (pre-existing: all LLM-failure classes render a silent empty transcript — critical-notes flagged): if the ladder's loud terminals surface via SSE, users still see nothing.
- *Options:* (a) consolidate after one soak cycle; (b) keep dual lines permanently; FE renderer is a separate workstream either way. Recommended: (a) + file the FE renderer as its own fix (out of ladder scope; noted so the ladder's loudness is not assumed user-visible).
- *Unresolved — phase-3 backlog for the consolidation. FE renderer is a separate workstream entirely.*

---

## Cross-References

- Full analysis, anchors, matrices: `technical-analysis.md` (same directory).
- Shipped-guard contracts this design must not regress: `empty-response-guard/architecture-recommendation.md` §5 (Option 4), §6 (L1–L13), §8.1 (nudge allowance synthesis); `adr-0001-dropped-empty-content-check.md`.
- Mechanism inventory: `docs/hallucination-protection.md` §1/§6.3/§7/§8.1 (anchors pinned at `383fc24f` — re-pin symbols at implementation; drift table in technical-analysis.md References).
