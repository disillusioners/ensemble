# Plan Overview: Hallucination-Recovery Ladder (Symptom-Triggered Context Repair)

**Date:** 2026-09-13
**Author:** planner[v2] via plan-creation worker
**Status:** Draft — for architect approval; 7 OPEN QUESTIONS still pending (see §Open Questions & gating)

**Observed git state (verified before writing):**
- Branch: `plan/long-tool-call-nudge` (NOTE: dispatch brief stated `plan/hallucination-recovery-ladder`; the worktree was already checked out at `plan/long-tool-call-nudge` and the planning directory was authored there — recording actual observation per the honest-record rule; feature branch naming is the architect's call)
- HEAD: `0acd3afae2d7f25e8c15d44f7802c4f23f5975b1` (short `0acd3afa`), clean tree at write time.
- Companion documents (already authored at this HEAD):
  - `technical-analysis.md` (394 lines, this directory) — design basis, anchors, 12 partition invariants, 13 test-critical invariants, risks R1-R7.
  - `decisions.md` (168 lines, this directory) — ADRs 0001-0008 + 7 OPEN QUESTIONS (OQ1-OQ7).
- Reference (already merged): empty-response-guard `architecture-recommendation.md` (Option 4 contracts this program must not regress).
- Anchor drift note: `docs/hallucination-protection.md` anchors are pinned at `383fc24f` and have drifted; SYMBOL NAMES are authoritative, not line numbers.

---

## Objective

**One generalized, durable, symptom-triggered context-repair rung** that slots between cheap countermeasures (nudge / capped re-invoke) and the existing retry/failover/terminal ladder for **every hallucination-class symptom**, evolved from today's transient loop breaker rather than duplicated beside it. On restart/revive, repair persists; on budget exhaustion, repair escalates loudly; with the master kill-switch OFF, shipped behavior is preserved byte-identically.

> *A single sentence that, when true, marks the feature complete: across all enrolled hallucination-class symptoms, restart/revive no longer replays degenerate history (carrier durable), budget exhaustion no longer silently re-trips (escalation loud), and the shipped empty-guard contracts (S1 raise-in-retry-scope, S5 caps, L1-L13, kill-switch byte-identity) hold unchanged.*

---

## Scope

### In Scope
- One **unified repair engine** (`SymptomRepairEngine`, evolution of `LoopRepairer`) at the existing pre-LLM middleware slot (`daemon/graph.py:5186`) plus router-level entry points (`should_continue`).
- **Per-class preset table** (evidence-window selector / summary prompt fragment / retention set / post-repair routing) for: loop (toolcalls, phase-1), ghost-promise (phase-2), empty (S1) post-ladder (phase-2), truncated (phase-2).
- **One durable carrier** — return-carried sentinel-first prefix mirroring `build_sentinel_replacement` (`daemon/compaction.py:427-507`); NO new `aupdate_state`-mid-superstep shape.
- **One durable per-task repair budget** — GraphState field, language-check pattern (`daemon/graph.py:2456-2459`); reset policy `[gated-by OQ5]`.
- **Facade-wrapped summarizer** — `wrap_langchain_failover` pattern (`daemon/services/llm_failover.py:617`); fail-open abort on degenerate summary; folds empty-guard Phase-2 deferred item "(3) raw-SDK mirrors: LoopRepairer".
- **Exhaustion escalation** — close the `WARN+continue` hole (`daemon/graph.py:1857-1864`) under the flag; OFF-mode = byte-identical to shipped.
- **Kill-switch convention** — master + per-class sub-flags; `_resolve_*` discipline; OFF = byte-identical routing, telemetry stays (W1 KEEP precedent).
- **Joint loop-breaker × empty-guard integration test** (T-1, verified absent at this HEAD).
- **Reconciliation with empty-response-guard Phase-2 deferred list** — fold-ins: item (3) LoopRepairer (phase-1, ADR-0006), item (4) manager streak → persistent metric (phase-2), item (5) no-progress signature (phase-3 prerequisite).

### Out of Scope (deliberate)
- **Watchover 3-strike termination** — partition non-negotiable (`daemon/graph.py:7888-7896` region); ladder never terminates for watchover-adjacent classes.
- **CLE (ContextLengthExceeded) handler** — already a rung-2 shape (`daemon/graph.py:5345-5394`); its superseded persist defect (`:5396-5398`) is a **separate** defect — do not duplicate, do not fix here.
- **Bare-JSON provider body** — `MalformedLLMResponseError` is `TRANSIENT_EXCEPTIONS` (`llm_error_classifier.py:466`); provider-failure framing owns it.
- **Wrong-language** — fail-open at `LANGUAGE_CHECK_MAX_RETRIES=2` (`daemon/graph.py:2833-2837`); already covered at rung-1.
- **GII tool-throttle storms** — own escalating backoff (mechanism #10); partition respect.
- **Phase-3 detector design** (drift / no-progress / schema / repeated-finals) — explicitly gated on phase-1/2 soak telemetry; only enrollment becomes in scope after detectors exist.
- **FE SSE error-event renderer** — pre-existing gap (critical notes: ALL LLM-failure classes render silent empty transcript); adjacent, separate workstream. The ladder's loud terminals are not assumed user-visible.
- **Editing `technical-analysis.md` or `decisions.md`** — they are the tech worker's artifacts; this plan cites them, never modifies them.

### Adjacent Features (deliberately excluded)
- "Severity promotion `validation_error` → `max_retries_exceeded` in `CRITICAL_ERROR_TYPES`" (empty-guard Phase-2 item 1) — **separate workstream**, sequence AFTER ladder phase-2; the pre-terminal repair rung changes when `validation_error` terminals occur (fewer, later), so promoting severity first would need re-measurement.
- "Provider-health circuit (Option-3 core)" (empty-guard Phase-2 item 2) — **separate**, telemetry-gated; the ladder's `[SYMPTOM]` surface is exactly the evidence collector the circuit decision awaits.
- `agents/*/meta.json` scan for designed-empty finals; `llm_load_balancer` empty-signal unification; compaction partial-summary hierarchy — **separate** cleanups (empty-guard Phase-2 "while-we're-here" pile).

---

## Symptom-Class Matrix (centerpiece)

> Columns mirror `technical-analysis.md` §Symptom handler table + decisions.md ADR-0007 enrollment; phase column reflects the locked-in phasing. Rung assignment, durability, and mechanism are all sourced from those documents and cited inline.

| # | Class | Current handler site (symbol + file) | Today's recovery | Ladder rung assignment | Repair mechanism | Durability | Phase enrolled | Kill-switch / telemetry |
|---|---|---|---|---|---|---|---|---|
| 1 | **Repeated identical toolcalls** | `LoopDetector` `daemon/graph.py:977`; signature `:1002-1022`; scan `:1024-1180`; `LoopRepairer.repair` `:1320`; in-mem filter `:1504-1556`; static-fallback summarizer `:1632-1677`; WARN+continue exhaustion `:1857-1864` | In-memory filter + LLM summary + fresh-UUID SystemMessage + re-append; `max_repairs=3` RAM | **Rung 2 (the engine target)** | Removal-builder output (`:1477-1501`) feeds return-carried sentinel-first prefix; facade-wrapped summarizer; budget consumed; exhaust → loud terminal | **DURABLE** (return-carried) | **Phase-1** | Master `ENSEMBLE_SYMPTOM_REPAIR_LADDER`; sub `ENSEMBLE_REPAIR_LOOP_DURABLE` (default ON, restart-pending); telemetry `[SYMPTOM] class=loop phase=*` + retained `[LOOP BREAKER]` line during transition |
| 2 | **Empty-as-entire-answer (S1)** | `validate_llm_response` `daemon/response_validation.py:396`; raise `:466-475`, `EmptyLLMResponseError ⊂ LLMResponseValidationError` `:26`; call at `llm_error_classifier.py:916` (docs cite `:911` — stale) | nudge (`graph.py:2555-2557`) → 2nd-empty raise INSIDE retry scope → transient retry → failover → loud ERROR (`graph.py:5516-5519`) | **Pre-terminal once** (ADR-0003) `[gated-by OQ1 — facade-hook alternative deferred]` | Strip empty AIMessages in turn window + summary; keep tool results | **DURABLE** (same carrier) | **Phase-2** | Sub `ENSEMBLE_REPAIR_EMPTY_POST_LADDER` (default ON after soak); telemetry `[SYMPTOM] class=empty phase=repair` |
| 3 | **Degenerate / reasoning-only / think-tag-only (S5)** | `_is_degenerate_ai_message` `daemon/graph.py:2623-2648`; `_count_trailing_degenerate_ai_messages` `:2651-2666`; re-invoke at `:2566-2572`/`:2587-2594`; loud END at cap | Derived count, zero-state; `EMPTY_DEGENERATE_REINVOKE_CAP=3` (`:2620`) | **Covered** — S5 cap is the rung-1+backstop; **repair-at-cap = phase-3 OPTION** `[gated-by OQ4]` | (Phase-3) Bare-drop at cap, no summarizer needed | n/a in phase 1-2 | **Phase-3 OPTION only, default OFF** | Sub `ENSEMBLE_REPAIR_S5_AT_CAP` (default OFF until soaked) |
| 4 | **Ghost promise (content ends with `:`)** | Detect `daemon/graph.py:2596-2601`; re-invoke `:2601`; per-occurrence WARN `:2600`, NO counter | BARE re-invoke, **UNCAPPED** (`:2538-2541`); only backstop = `recursion_limit=300` | **Rung 2 (router-level at-cap)** | Strip trailing ghost-promise AIMessages at derived cap + summary of attempted work; tail window from last non-ghost boundary | **DURABLE** (same carrier) | **Phase-2** `[gated-by OQ2 — detector conservatism / FP profile]` | Sub `ENSEMBLE_REPAIR_GHOST_PROMISE` (default ON only after soak); telemetry `[SYMPTOM] class=ghost phase=detect` |
| 5 | **Truncated (`finish_reason=length`)** | `response_validation.py:452-457`; `_is_truncated_response` `:478-495` (missing metadata fails OPEN); raises in retry scope | RETRY-ONLY, same poisoned context | **Pre-terminal once** (ADR-0003) | Drop the truncated AIMessage + summary; **PRESERVE the partial text as an excerpt inside the repair doc** `[gated-by OQ3]` | **DURABLE** (same carrier) | **Phase-2** | Sub `ENSEMBLE_REPAIR_TRUNCATED` (default ON after soak); telemetry `[SYMPTOM] class=truncated phase=repair` |
| 6 | **Malformed tool calls** | `response_validation.py:459-464` | Retry lane only; transient budget | (Rung 2 if enrolled; minor gap) | Drop malformed AIMessage + immediate re-invoke | DURABLE if enrolled | **DEFERRED** (not in phase 1-3 cuts) | n/a |
| 7 | **Bare-JSON provider body** | `ThinkingChatOpenAI._create_chat_result` `daemon/graph.py:2044-2065` → `MalformedLLMResponseError` (TRANSIENT member `llm_error_classifier.py:402-418`/`:466`) | Retry/failover | **Never** — provider-failure framing owns it | n/a | n/a | **Never** | n/a |
| 8 | **Wrong language** | `create_language_check_node` `daemon/graph.py:2783`/`:2799`; checkpoint fields `:2456-2459` | Reminder ×2 → fail-open `:2833-2837` | **Never** — covered at rung-1 | n/a | n/a | **Never** | n/a |
| 9 | **CLE (context-length exceeded)** | `daemon/graph.py:5302`; handler `:5345-5394`; supersede persist `:5396-5398` | Reactive compaction (already rung-2 shape) | **Never** — separate defect; do NOT duplicate | n/a | n/a | **Never** | n/a |
| 10 | (candidate) **Repeated identical FINAL answers, non-tool** | **Undetected** — `LoopDetector` breaks on plain AIMessages `:1110-1113`; `should_continue` ENDs on truthy `:2609`; S5 counts only degenerate empties | none (recursion_limit only) | **Phase-3** (detector design first) | TBD with detector | TBD | **Phase-3, gated on detector** | Sub `ENSEMBLE_REPAIR_REPEATED_FINAL` (default OFF) |
| 11 | (candidate) **Tool storms with drifting params** | **Undetected** — exact-signature chain breaks `:1002-1022` | none (recursion_limit only) | **Phase-3** (gated on no-progress signature = empty-guard item 5) | TBD with detector | TBD | **Phase-3, gated on detector** | Sub `ENSEMBLE_REPAIR_DRIFTING_TOOLS` (default OFF) |
| 12 | (candidate) **Schema/format violations** | **Undetected** (no generic validator) | malformed-tool-call retry lane only | **Phase-3** (gated on validator design + exemption analysis) | TBD | TBD | **Phase-3, gated on detector** | Sub `ENSEMBLE_REPAIR_SCHEMA` (default OFF) |
| 13 | (candidate) **Stuck-without-progress** | **Undetected** | none (recursion_limit only) | **Phase-3** (gated on progress metric design) | TBD | TBD | **Phase-3, gated on detector** | Sub `ENSEMBLE_REPAIR_STUCK` (default OFF) |

**Out-of-ladder classes (enrolled NEVER)** — wrong-language (covered), CLE (already rung-2 shape), bare-JSON provider body (transient lane owns it), watchover-denied classes (3-strike is watchover's), GII throttle storms (own escalating backoff).

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | **Durable loop-breaker rung conversion** | Convert today's TRANSIENT loop repair into the durable, facade-wrapped, budget-enforced carrier; close the WARN+continue exhaustion hole; add the missing joint loop-breaker × empty-guard integration test. No new class enrollment. | 8 (phase1-plan.md) | Tight with empty-guard shipped contracts (S1 raise-in-retry-scope; S5 caps; L1-L13); independent of phase-2 classes | Pending (gated by ADR-0002 phase-1 = durable-first rationale) |
| 2 | **Enroll ghost-promise + truncated + S1-post-ladder** | Convert the cheapest real gaps (ghost-promise uncapped, truncated same-context waste, S1 post-ladder repair) onto the proven phase-1 carrier. | 8 (phase2-plan.md) | Tight with phase-1 (inherits proven carrier + budget + summarizer); loose with empty-guard shipped contracts (pre-terminal placement; gated by OQ1/OQ2/OQ3) | Pending; depends on phase-1 soak + 3 OPEN QUESTION rulings (OQ1, OQ2, OQ3) |
| 3 | **Detector-gated classes** | Enroll the four candidates whose detection is currently missing: repeated identical FINAL answers (non-tool), tool storms with drifting params, schema/format violations, stuck-without-progress. | 6 (phase3-plan.md) | Independent of phase-1/2 internals (each new class = new preset row + detector); tight with OQ6 (bulk delegation) | Pending; explicitly gated on phase-1/2 soak telemetry + detector designs (no detector exists today) |

> **Phasing rationale (cited from `decisions.md` ADR-0002):** phase-1 proves the durable carrier on the one class with battle-tested detection before new classes ride it. Phase-2+ inherit the proven carrier; their marginal risk is detector-FP only.

---

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| **Phase 1** | — | Tight (shared carrier + budget + summarizer + telemetry surface) | Independent (no detector coupling) |
| **Phase 2** | Tight | — | Independent (per-class preset is data, not control flow) |
| **Phase 3** | Independent | Independent | — |

**Tight couplings (cited, with the contract they share):**
- Phase-1 ↔ Phase-2: one carrier, one budget, one summarizer-facade wrap, one `[SYMPTOM]` telemetry line. Phase-2 presets add per-class rows; no engine duplication.
- Phase-1 ↔ Empty-guard shipped contracts (S1 raise-in-retry-scope; S5 caps; L1-L13): do not regress. Kill-switch OFF = byte-identical routing preserved.

**Loose couplings:**
- Phase-2 ↔ Empty-guard shipped contracts: pre-terminal placement wastes ≤`PRIMARY_TRANSIENT_MAX−1` poisoned-context retries before repair fires (bounded, accepted); does NOT touch facade.
- Phase-2 ↔ Telemetry consolidation (OQ7): new `[SYMPTOM]` line emitted alongside `[LOOP BREAKER]`/`[LLM-EMPTY]` during transition; consolidation deferred.

**Independent:** Phase-3 detector design does not couple to phase-1/2 internals; each new class = new preset row + detector (ADR-0001).

---

## Risks (top, deduped from `technical-analysis.md` §Risks R1-R7)

| # | Risk (source) | Impact | Likelihood | Mitigation | Owner-lane |
|---|---------------|--------|------------|------------|------------|
| R1 | **Hottest-path surgery** — converting loop repair to return-carried touches `agent_node` return assembly (`graph.py:5612-5638`, caution `:5618`) shared with L2 compaction durability (technical-analysis R1) | High | Medium | Canary mirrors (T-3); golden-routing pins (T-8); phase-1 confined to loop class; symbol-pinned not line-pinned | Developer (coder lane) |
| R2 | **`checkpoint_ns` empty-snapshot trap** — any `aget_state` in repair path with node-stamped config reads EMPTY snapshot (`graph.py:4382`/`:5312`/`:5345`); CLE's superseded persist shape (`:5396-5398`) is the precedent of what NOT to do (technical-analysis R2, TD#4) | High | Low | Thread-id-only reads (`_compaction_persist_seam.py:139`); partition-invariant P-10 enforces no mid-flight `aupdate_state`; canary `TestMidSuperstepPersistCanary` mirrored | Developer (coder lane) |
| R3 | **Repair-storm interplay** — repair docs and compaction docs interleaving; convergent re-detection (technical-analysis R3) | Medium | Medium | Distinct id namespaces (`repair-{instance_id}-{seq}` vs `compaction-global-{instance}-{seq}`); derived detectors self-clear on repaired tails (T-2); budget cap | Developer (coder lane) |
| R4 | **Summarizer-as-hallucination-vector** — the repair doc is LLM-written context injected into history (technical-analysis R4) | High | Medium | Verbatim-excerpt pinning (DQ2-e); facade coverage (fail-open abort on degenerate, never silent static fallback); T-11 invariants; ADR-0006 fail-open abort | Developer (coder lane) |
| R5 | **Truncated-class data loss** — dropping the partial AIMessage can lose real user-facing content (technical-analysis R5) | High | Medium | **Preserve partial text inside the repair doc** (architect's call per OQ3); `[gated-by OQ3]` | Architect (decision) → Developer (implementation) |
| R6 | **Ghost-promise FP** — legitimate colon-ending content (code blocks, lists) could be capped/stripped (technical-analysis R6) | Medium | Medium | Conservative detector (shipped `graph.py:2596-2601` shape); cap-before-surgery ordering; FP soak before default-ON enrollment; `[gated-by OQ2]` | Architect (decision) → Developer (implementation) → Maintenancer (soak telemetry) |
| R7 | **Contract regression pressure** — the S1 facade-hook alternative (repair before retries) is tempting post-incident; re-opens the review-approved raise-in-retry-scope contract (technical-analysis R7) | High | Medium (incident-driven) | ADR-0003 defers it behind OQ1 with explicit preconditions (telemetry must show poisoned-retry success rate ≈ 0); OFF = byte-identical preserved | Architect (decision) |

**Cross-phase risks (cited, not new):**
- **Doc anchor drift** (technical-analysis §Anchors + TD#6): `docs/hallucination-protection.md` pinned at `383fc24f`, drifted vs `0acd3afa`; SYMBOL NAMES authoritative, line numbers re-verified at implementation. Owner-lane: Developer (re-verify at PR time).
- **FE SSE renderer gap** (TD#5): pre-existing, all LLM-failure classes render silent empty transcript in FE. Out of scope here; the ladder's loudness is operator-visible (logs / `[SYMPTOM]` line) but NOT user-visible. Owner-lane: separate FE workstream (filed in critical notes).
- **Lane name-match debt** (TD#3 + P-12): any new `LLMResponseValidationError` subclass MUST be registered in `daemon/services/message_processing_errors.py:112-165` or it silently misroutes to `execution_error`. Owner-lane: Developer (P-12 partition invariant).

---

## Success Criteria

> Every criterion is **measurable**, with a measurement method and pass threshold. "Works well" is not a criterion.

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| SC-1 | **Phase-1 restart/revive no longer replays degenerate loop history** | Restart a task after a successful loop repair; observe history in next checkpoint; assertion in joint integration test (T-2 durability) | Degenerate window gone from checkpoint; derived detectors self-clear; repair budget persists across restart |
| SC-2 | **Exhaustion no longer WARN+continue under flag** | Drive loop class to budget exhaustion; observe terminal; OFF-mode comparison (T-6) | Loud terminal with `[SYMPTOM] class=loop phase=terminal reason=repair-budget-exhausted` (flag ON); shipped WARN+continue preserved (flag OFF) |
| SC-3 | **No regression in shipped empty-guard test suite** | `uv run python -m pytest tests/unit/test_empty_response_guard* tests/integration/test_empty_response_guard*` from worktree root | All shipped empty-guard tests still green; `test_nudge_behavior.py:53` pin still holds |
| SC-4 | **Joint loop-breaker × empty-guard integration test exists and passes** | T-1 (verified absent today); `tests/integration/test_ladder_loop_x_empty_guard.py` | Continuous-empty provider + loop-prone tool storm → bounded S5/S1 behavior + bounded repairs + correct class attribution + loud terminal; no budget cross-consumption (P-9) |
| SC-5 | **Kill-switch OFF = byte-identical routing** | T-8 golden routing pins; `ENSEMBLE_SYMPTOM_REPAIR_LADDER=0` replay of every shipped scenario | Every shipped behavior pinned; OFF-mode telemetry (W1 KEEP) still emits `[LLM-EMPTY]`/`[SYMPTOM]` |
| SC-6 | **Ghost-promise burn bounded** (phase-2 success, pending OQ2 ruling) | T-9 burn-count assertion; ghost storm scenario | ≤ cap(3) + repair(1) + 1 continued = ≤5 LLM calls (vs ~300 today); OFF-mode preserved byte-identically |
| SC-7 | **Truncated repair preserves partial text** (phase-2 success, pending OQ3 ruling) | T-11 repair-doc invariant assertion; truncated scenario | Repair doc contains `excerpt=` field with original partial verbatim; no data loss |
| SC-8 | **Summarizer fail-open abort never silently degrades** | T-5 degenerate summary scenario | Continuous-empty summarizer → facade retry → failover → `repair_abort` telemetry; budget NOT consumed; no static fallback injected; never wedge the turn |
| SC-9 | **Partition invariants P-1 … P-12 all hold** | Per-invariant test pins (T-7) | Every invariant has a regression test; watchover-denied batches never trigger repair; tool-call messages never counted by ghost/degenerate detectors |
| SC-10 | **Test runner discipline** (T-13) | All tests invoked via `uv run python -m pytest` from worktree root | No bare `pytest` invocations in CI / review pipelines; SQLite + PostgreSQL coverage (disposable-PG14 recipe) |
| SC-11 | **No regression in proactive compaction L2 durability** | L2 95% pre-call compaction hook (`graph.py::_maybe_precall_compact_95`) regression suite | Return-carried prefix change does NOT regress the L2 hook (R1 mitigation) |
| SC-12 | **Dual-DB parity** (SQLite + PG) | Disposable-PG14 recipe (per repo conventions) + SQLite suite | Identical pass on both stores; one durable per-task budget field is the only new durable state (TRIGGER: any other durable state must justify migration surface) |

---

## Research Insights (from `technical-analysis.md` + `decisions.md`)

- **Ground-truth anchors verified at `0acd3afa`** (technical-analysis lines 30-42): the loop breaker is TRANSIENT (RemoveMessage sentinels never fed to state at `daemon/graph.py:1291-1310`/`:1413-1420`/`:1519-1525`); durable mid-turn rewrite is return-carried ONLY (canary `test_compact_executor_revive_brick_e2e.py:1746`; `build_sentinel_replacement` `compaction.py:427-507`); `max_repairs=3` RAM-only (`manager.py:754`); partition-by-shape is clean (S5/S1/L7-L9 mutually exclusive at detector walks); the ONE correction to the research buffer (`wrap_langchain_failover` DOES exist at `daemon/services/llm_failover.py:617` — symbol verified, an earlier "DOES NOT EXIST" claim was a grep false-negative).
- **Folding empty-response-guard Phase-2 deferred items** (technical-analysis §DQ5 reconciliation): item (3) LoopRepairer (phase-1 via ADR-0006); item (4) manager streak → persistent metric (phase-2, one decision for all symptom streaks); item (5) no-progress signature (phase-3 prerequisite, same program); items (1)+(2) are separate workstreams.
- **The shipped empty-guard contracts this design must not regress** (architecture-recommendation §5 Option 4, §6 L1-L13, §8.1 nudge-allowance synthesis): S1 raise-in-retry-scope (`llm_error_classifier.py:907/:916`); S5 derived caps (`graph.py:2620`/`:2651-2666`); L1-L13 exemption set; kill-switch OFF = byte-identical routing (W1 KEEP — telemetry stays).
- **Partition discipline** (technical-analysis §Partition-Invariant Compliance List P-1 … P-12, technical-analysis §Ground Truth #5): watchover 3-strike is watchover's; S5/S1 partition-by-shape is clean; CLE's superseded persist shape must NOT be replicated; `checkpoint_ns` empty-snapshot trap must be avoided (thread-id-only reads).

---

## Relation to Empty-Response-Guard Phase-2 Deferred List

| Deferred item (architecture-recommendation.md §9) | Same program or separate | Disposition | Reference |
|---|---|---|---|
| (1) Exhaustion-signal promotion `validation_error` → `max_retries_exceeded` in `CRITICAL_ERROR_TYPES` | **Separate workstream, same umbrella**, sequence AFTER ladder phase-2 | The pre-terminal repair rung changes when `validation_error` terminals occur (fewer, later); promoting severity first would need re-measurement after the ladder lands | architecture-recommendation.md §9 |
| (2) Provider-health circuit (Option-3 core), only-if-telemetry-shows-storms | **Separate**, telemetry-gated | The ladder's `[SYMPTOM]` surface is exactly the evidence collector the circuit decision awaits | architecture-recommendation.md §9 |
| (3) Raw-SDK mirrors: skill-embedding ×2; **LoopRepairer**; WatchoverEvaluator stays fail-closed | **LoopRepairer = SAME program (phase-1, ADR-0006).** skill-embedding = separate (different subsystem, own fallback). WatchoverEvaluator = out of scope (fail-closed by design). | Fold-in (LoopRepairer only) | technical-analysis.md §Reconciliation; ADR-0006 |
| (4) Manager streak → persistent observability metric | **Same program (phase-2, DQ4-a)** | One RAM-vs-instance-row decision for all symptom streaks | technical-analysis.md §DQ4-a; ADR-0004 |
| (5) LoopDetector no-progress signature (+ meta.json scan, load-balancer empty-signal, compaction partial-summary hierarchy) | **Same program for the no-progress signature (phase-3 prerequisite).** meta.json scan / load-balancer / partial-summary = separate cleanups. | Fold-in for item 5 proper | technical-analysis.md §Reconciliation; ADR-0007 |

---

## Open Questions (consolidated; full text in `decisions.md` §OPEN QUESTIONS)

The plan is **conditional on architect rulings** for seven open questions. Phases with `[gated-by OQ<n>]` annotations MUST NOT ship without the corresponding ruling.

| OQ | Topic | Phases gated | Default if unresolved |
|---|---|---|---|
| **OQ1** | S1-class repair placement: pre-terminal (recommended) vs shallow facade hook | Phase-2 | Pre-terminal (ADR-0003); saves ≤2 poisoned re-sends but touches approved raise-in-retry-scope contract |
| **OQ2** | Ghost-promise detector conservatism (bare `endswith(":")` FP risk) | Phase-2 | Enroll with bare detector + derived cap; FP soak before default-ON; sub-flag default ON only after soak window |
| **OQ3** | Truncated-class partial-content preservation (silent data-loss risk) | Phase-2 | Preserve partial VERBATIM inside repair doc (excerpt section); recommended (a) |
| **OQ4** | S5-cap-exceeded route to repair instead of loud END (phase-3 option) | Phase-3 only | Keep loud END permanently (phase-3 candidate behind default-OFF sub-flag after soak) |
| **OQ5** | Durable repair-budget reset policy | Phase-1 + Phase-2 | Reset on new real (non-injected) HumanMessage (recommended (c)) — defines "task" for budgeting purposes |
| **OQ6** | Bulk delegation to `compact_state` (hybrid escape hatch) | Phase-3 only | Hard size cap on repair window → abort repair if exceeded; fall through to backstops; makes (a) moot in most cases |
| **OQ7** | Telemetry consolidation + FE SSE renderer gap | Phases 1-3 (post-merge) | Consolidate `[SYMPTOM]` into existing lines after one soak cycle; FE renderer is separate workstream either way |

**FE SSE renderer gap (adjacent, not gated):** the FE has NO SSE error-event renderer (pre-existing; all LLM-failure classes render silent empty transcript). If the ladder's loud terminals surface via SSE, users see nothing. This is operator-visible (logs) but NOT user-visible. Filed in critical notes; not a ladder regression.

---

## Cross-References

- Design basis (anchors, matrices, risks): `technical-analysis.md` (same directory, 394 lines, verified at `0acd3afa`).
- ADRs + OPEN QUESTIONS (architect rulings pending): `decisions.md` (same directory, 168 lines).
- Shipped-guard contracts this plan must not regress: `empty-response-guard/architecture-recommendation.md` §5 (Option 4), §6 (L1-L13), §8.1 (nudge allowance synthesis); `adr-0001-dropped-empty-content-check.md`.
- Mechanism inventory (22 protections): `docs/hallucination-protection.md` §1/§6.3 (decision matrix)/§7 (seams)/§8.1 (middleware pipeline) — anchors pinned at `383fc24f`, drifted; SYMBOL NAMES authoritative.
- Ground-truth buffer (verification wanderer, HEAD `0acd3afa`) + Research Buffers A/B (explorer, HIGH confidence): symptom table, lane facts, compaction engine facts; the one buffer correction (item 10 of technical-analysis §Context Summary) is the `wrap_langchain_failover` symbol verification.
- Pause-first restart runbook: `.agents/shared/planning/kv-ambient-awareness-fix/plan-overview.md` Rollout section (same activation pattern).
- Repo conventions for test invocation: `.agents/shared/planning/.../plan-overview.md` T-13 (all via `uv run python -m pytest` from worktree root); disposable-PG14 recipe per repo blueprint.
