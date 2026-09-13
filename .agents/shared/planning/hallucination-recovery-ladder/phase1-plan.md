# Phase 1: Durable Loop-Breaker Rung Conversion

**Date:** 2026-09-13
**Author:** planner[v2] via plan-creation worker
**Status:** Draft — gated by ADR-0002 (durable-first rationale) + OQ5 (durable-budget reset policy)

**Observed git state (verified before writing):**
- Branch: `plan/long-tool-call-nudge` (dispatcher-stated branch `plan/hallucination-recovery-ladder`; worktree was authored here — recording actual observation; see plan-overview.md)
- HEAD: `0acd3afae2d7f25e8c15d44f7802c4f23f5975b1` (short `0acd3afa`), clean tree at write time.
- Anchor drift note (chronic, repo-wide): `docs/hallucination-protection.md` anchored at `383fc24f`; SYMBOL NAMES authoritative, line numbers re-verified at implementation. This plan pins symbols and cites lines observed at `0acd3afa`.

---

## Objective

Convert today's **TRANSIENT** loop repair into the **durable, facade-wrapped, budget-enforced** carrier that the ladder mandates, on the **loop class ONLY**. Close the WARN+continue exhaustion hole under the flag. Add the missing joint loop-breaker × empty-guard integration test. NO new class enrollment in this phase (proven carrier first, new detectors later — ADR-0002).

> *Single sentence: under `ENSEMBLE_SYMPTOM_REPAIR_LADDER=ON` and `ENSEMBLE_REPAIR_LOOP_DURABLE=ON`, a loop-class repair (a) survives restart/revive in the checkpoint, (b) consumes a single durable per-task budget, (c) routes its summarizer through the HA facade with fail-open abort, and (d) escalates loudly when the budget exhausts — with `=0` reverting each leg to shipped byte-identity.*

---

## Scope

### In Scope (workstreams)

1. **Carrier conversion** — return-carried sentinel-first prefix at the `agent_node` return assembly; mirrors `build_sentinel_replacement` (`daemon/compaction.py:427-507`).
2. **Durable budget** — new GraphState field (language-check pattern, `daemon/graph.py:2456-2459`); RAM `max_repairs` retained as per-turn expression.
3. **Summarizer facade routing** — `wrap_langchain_failover` pattern (`daemon/services/llm_failover.py:617`) replacing the bare `ThinkingChatOpenAI.invoke` of `LoopRepairer` (`daemon/graph.py:1642-1677`); fail-open abort on degenerate summary.
4. **Exhaustion escalation** — `WARN+continue` (`daemon/graph.py:1857-1864`) replaced with loud terminal + `[SYMPTOM] class=loop phase=terminal reason=repair-budget-exhausted` under flag.
5. **Joint integration test (T-1, MISSING today)** — loop-prone tool storm DURING continuous-empty provider episode; bounded repairs + bounded S5/S1 behavior; no cross-budget interference.
6. **Kill-switch + telemetry** — master `ENSEMBLE_SYMPTOM_REPAIR_LADDER` + sub `ENSEMBLE_REPAIR_LOOP_DURABLE`; `[SYMPTOM] class=loop phase=*` line alongside existing `[LOOP BREAKER]` during transition.
7. **Partition invariants P-1 … P-12 enforced as regression tests** (T-7).

### Out of Scope (deferred to phase-2 / phase-3)
- Ghost-promise, truncated, S1-post-ladder enrollment (phase-2; `[gated-by OQ1/OQ2/OQ3]`).
- Detector design for repeated identical FINAL answers, tool-storm drift, schema violations, stuck-without-progress (phase-3).
- Bulk delegation to `compact_state` (phase-3, `[gated-by OQ6]`).
- S5-cap-exceeded repair routing (phase-3 OPTION, `[gated-by OQ4]`).
- Telemetry consolidation (post-soak, `[gated-by OQ7]`).
- FE SSE error-event renderer (separate workstream; pre-existing gap).

### Adjacent (NOT this phase)
- CLE's superseded persist shape (`daemon/graph.py:5396-5398`) — separate defect, do NOT replicate.
- Manager streak → persistent observability metric — folded into phase-2 (one decision for all symptom streaks).
- Severity promotion `validation_error` → `max_retries_exceeded` in `CRITICAL_ERROR_TYPES` — separate workstream, sequence AFTER ladder phase-2.

---

## Tasks

> Workstream → Task → Touch-site (symbol + file) → Acceptance test.
> Acceptance tests are described; full code is out of scope per the plan-creation worker contract.

### Workstream A — Carrier conversion (return-carried sentinel)

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| A-1 | Introduce `SymptomRepairEngine` class skeleton (evolves `LoopRepairer`); preserves the same public surface for the loop class; per-class preset table is a dict on the class, not separate engines | none | New module `daemon/services/symptom_repair_engine.py` (home TBD by Developer; candidate) | Unit test: instantiation with empty preset table; loop-class preset loaded by symbol-name reference (`"loop"`) |
| A-2 | Re-route `LoopRepairer.repair` removal-builder output (`daemon/graph.py:1477-1501`) into the engine's surgery builder; drop the in-memory filter path (`:1504-1556`) | A-1 | `daemon/graph.py:1477-1501` + `:1504-1556` (the in-mem filter, to be removed) | Unit test: removal builder input → engine surgery output equivalence (same set of `MessageID`s + same retention set) |
| A-3 | Emit the return-carried sentinel-first prefix at the `agent_node` final return assembly (`daemon/graph.py:5612-5638`, superseded-persist caution `:5618`); sentinel element-0 invariant (P-6: `RemoveMessage(REMOVE_ALL_MESSAGES)` MUST be element 0; cf. `daemon/compaction.py:451-457`) | A-2 | `daemon/graph.py:5612-5638` | Integration test: repaired history upserts in the same task commit; canary mirror of `TestMidSuperstepPersistCanary` (T-3) |
| A-4 | Construction-time stable `id=` on the repair-doc message (message-id invariant, blueprint warning); distinct id namespace `repair-{instance_id}-{seq}` vs `compaction-global-{instance}-{seq}` (`daemon/graph.py:5320-5322`) | A-3 | `daemon/services/symptom_repair_engine.py` (new); `daemon/graph.py:5320-5322` (pattern reference) | Unit test: every repair-doc message carries construction-time `id=`; round-trip preserves through checkpoint serialise/deserialize |
| A-5 | Retain ORIGINAL ids on the retained tail (P-7: `daemon/compaction.py:438-443` docstring + `:448` upsert-in-place) | A-3 | `daemon/services/symptom_repair_engine.py` | Unit test: retained messages keep ORIGINAL ids (upsert-in-place, not new objects) |
| A-6 | Folded tool-call unit preservation (P-8): stripped AIMessage takes its paired ToolMessages via `tool_call_id` matching (`daemon/graph.py:1105-1113` region) | A-3 | `daemon/graph.py:1105-1113` (pattern reference); new engine method | Unit test: post-surgery every retained ToolMessage still pairs with its issuing AIMessage; T-4 tool-pairing invariant |

### Workstream B — Durable budget

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| B-1 | Add GraphState field `repair_budget_used` (or equivalent name, final per Developer); default 0; declared alongside language-check fields `language_check_retry` / `language_check_count` (`daemon/graph.py:2456-2459`, explicit "Persisted in checkpoints" rationale) | none | `daemon/graph.py:2456-2459` (region — language-check field declaration) | Unit test: state declaration round-trip; GraphState schema unchanged for SQLite + PG |
| B-2 | Initialise value at first turn (default 0); increment atomically on successful repair (NOT on repair_abort — budget not consumed on abort); per-turn reset of the per-TURN expression (the existing RAM `max_repairs=3` counter at `daemon/manager.py:754`) remains unchanged | A-3, B-1 | `daemon/graph.py` (post-repair region) | Unit test: successful repair → durable counter +1; repair_abort → durable counter unchanged; P-9 counter-theft invariant (no S5/S1/transient-budget cross-consumption) |
| B-3 | Reset policy `[gated-by OQ5]` — default per ADR-0004: reset on new real (non-injected) HumanMessage; inject-detection via `injected_message=True` and `context_kind` patterns from `daemon/services/context_messages.py:85-110` | B-1 | `daemon/graph.py` (post-HumanMessage region) | Unit test: real HumanMessage → durable counter reset to 0; injected/context HumanMessage → counter unchanged |
| B-4 | Wire the engine to consult the durable counter before each repair attempt; if `repair_budget_used >= REPAIR_BUDGET=3` (proposed, mirrors `max_repairs=3` semantics), refuse + escalate (Workstream D) | A-3, B-1, B-2 | `daemon/services/symptom_repair_engine.py` | Unit test: counter at cap → repair refused; no surgery; budget NOT incremented; routed to escalation path |

### Workstream C — Summarizer facade routing + fail-open abort

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| C-1 | Replace the bare `ThinkingChatOpenAI.invoke` summarizer (`daemon/graph.py:1642-1677`) with a `wrap_langchain_failover`-built client (compaction's pattern: `daemon/compaction.py:3383`, `:3395` calling `daemon/services/llm_failover.py:617`) | A-3 | `daemon/graph.py:1642-1677` (replace); new method on `daemon/services/symptom_repair_engine.py` | Unit test: summarizer client constructed via `wrap_langchain_failover`; live prod callers reference match `compaction.py:3383`/`:3395` |
| C-2 | Wire empty/degenerate summarizer output → facade retry → failover → on ultimate failure the repair ABORTS fail-open: budget NOT consumed, no surgery, fall through to next rung (retry/failover/terminal), loud `[SYMPTOM] class=loop phase=repair action=abort reason=summarizer-failed` telemetry | C-1 | `daemon/services/symptom_repair_engine.py` | Unit test (T-5): summarizer continuous-empty → bounded retries → failover → repair_abort telemetry; budget NOT incremented; never the static fallback of today (`:1632-1636`) |
| C-3 | Persist-refusal handling (T-12): if a persist seam call refuses (e.g. `persist_compaction_result` returns False on fail_open refusal, `daemon/_compaction_persist_seam.py:84-90`), treat as abort; same discipline as compaction seam contract | C-2 | `daemon/services/symptom_repair_engine.py` | Unit test: persist seam False return → repair_abort; budget NOT consumed; loud telemetry |
| C-4 | Summarizer timeout 120s preserved (`daemon/config.py:1755`); wall-clock-cap param `wall_clock_cap_s` (`daemon/services/llm_failover.py:620-624`) inherited from facade | C-1 | `daemon/config.py:1755` (no change; documented); `daemon/services/llm_failover.py:620-624` (no change; referenced) | Unit test: 120s timeout enforced; abort path documented |

### Workstream D — Exhaustion escalation

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| D-1 | Replace `WARN+continue` (`daemon/graph.py:1857-1864`) under the flag with loud terminal: route to the loop class's loud terminal backstop with `[SYMPTOM] class=loop phase=terminal reason=repair-budget-exhausted` telemetry | B-4 | `daemon/graph.py:1857-1864` (gated replacement) | Unit test (T-6): budget exhausted → loud terminal + class=loop attribution; OFF-mode → shipped WARN+continue preserved |
| D-2 | Ensure kill-switch OFF preserves byte-identical routing (P-11): every new branch gated before behavior; OFF path keeps the shipped return value from `graph.py:1857-1864` | D-1 | `daemon/graph.py:1857-1864`; new gating helper | Test (T-8): golden-routing pin: `ENSEMBLE_SYMPTOM_REPAIR_LADDER=0` + `ENSEMBLE_REPAIR_LOOP_DURABLE=0` → byte-identical to pre-phase-1 behavior |

### Workstream E — Joint integration test (T-1, MISSING today)

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| E-1 | Author `tests/integration/test_ladder_loop_x_empty_guard.py`: continuous-empty provider + loop-prone tool storm | A-3, B-1, C-1, D-1 | New test file (path per repo conventions — `tests/integration/`) | T-1 acceptance: bounded repairs (≤3); bounded S5/S1 behavior (≤3 each); correct class attribution in `[SYMPTOM]` telemetry; loud terminal on either exhaustion; no cross-budget interference (P-9); SQLite + PG both pass (T-13) |
| E-2 | Author SQLite fixture mirroring the disposable-PG14 recipe per repo blueprint (both DBs in same test) | E-1 | `tests/postgres/conftest.py` (existing pattern) | Test suite runs against both SQLite and disposable PG; identical pass |

### Workstream F — Kill-switch + telemetry

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| F-1 | Add `_resolve_symptom_repair_ladder` and `_resolve_repair_loop_durable` to `daemon/config.py` (mirroring `_resolve_proactive_enabled` `config.py:2409-` and `_resolve_compaction_model`); empty-string safe; invalid → `ValueError` at boot | none | `daemon/config.py` (new `_resolve_*` helpers) | Unit test: invalid env value → `ValueError` at boot; empty-string safe; defaults ON |
| F-2 | Wire master `ENSEMBLE_SYMPTOM_REPAIR_LADDER` (default ON, restart-pending) and sub `ENSEMBLE_REPAIR_LOOP_DURABLE` (default ON) into the gating helpers | F-1 | `daemon/config.py` | Unit test: env var resolution + default; gating helper returns False when OFF |
| F-3 | Emit `[SYMPTOM] class=loop phase=<detect|rung1|repair|repair_abort|terminal> action=<fired|skipped|abort|escalate> budget=<used>/<cap> instance=<short> turn=<task_id> detail=<one-liner>` alongside the existing `[LOOP BREAKER]` line (DQ4-b schema); telemetry stays on OFF (W1 KEEP precedent) | D-1, F-1 | `daemon/graph.py` (post-repair region) | Unit test: every transition emits the correct `[SYMPTOM]` shape; OFF-mode still emits telemetry (W1 KEEP) |

### Workstream G — Partition invariants + regression tests

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| G-1 | Author regression tests for each partition invariant P-1 … P-12 (per `technical-analysis.md` §Partition-Invariant Compliance List): tool-call messages never counted by ghost/degenerate detectors (P-1); first-empty-after-tool passes (P-2); LoopDetector walk purity (P-3); watchover-denied batches excluded (P-4); L6 injection preservation (P-5); sentinel element-0 (P-6); original-id tail (P-7); tool-pairing (P-8); no counter theft (P-9); no mid-flight `aupdate_state` (P-10); kill-switch byte-identity (P-11); lane name-match (P-12) | A-3, B-1, C-1, D-1 | New tests under `tests/unit/test_symptom_repair_partition.py` + wherever else each invariant's home is | Each P-n has at least one regression test (T-7); all green |
| G-2 | Author canary mirror of `TestMidSuperstepPersistCanary` for the repair carrier (T-3): assert NO `aupdate_state`-only mid-turn persist anywhere in the new path | A-3 | `tests/unit/test_symptom_repair_mid_superstep_canary.py` | Test passes; mirrors the compaction canary's contract |
| G-3 | Author repair-doc invariant tests (T-11): construction-time id present; non-selectable/hoisted partition treatment; verbatim-excerpt pinning (no free-form narrative acceptance) | A-4 | `tests/unit/test_symptom_repair_doc.py` | All three invariants tested; `[SYMPTOM]` telemetry asserted |

### Workstream H — Rollout (activation, restart-pending convention)

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| H-1 | Document the pause-first restart activation runbook for this phase (mirroring `.agents/shared/planning/kv-ambient-awareness-fix/plan-overview.md` Rollout section); merge to `latest` branch with `--no-ff` per repo convention (h) | F-2 | New section in `plan-overview.md`; activation note in PR description | Runbook pinned; reviewer confirms pause-first ordering |
| H-2 | Close the §7 gap in `docs/hallucination-protection.md` and correct any further drift discovered at implementation time (deferred to merge commit per the doc-correction precedent in architecture-recommendation §3) | F-3 | `docs/hallucination-protection.md` | Doc updated in same merge; symbol-pinned |

---

## Coupling

- **Tight with:** shipped empty-guard contracts (S1 raise-in-retry-scope, S5 caps, L1-L13, kill-switch byte-identity) — phase-1 is structurally additive; OFF-mode preserves byte-identity (P-11); golden-routing pins (T-8) are the contract gate.
- **Tight with:** proactive compaction L2 durability (`daemon/graph.py::_maybe_precall_compact_95`, return-carried persist `:5409-5414` per blueprint) — phase-1 shares the same `agent_node` return assembly; regression suite must include L2.
- **Loose with:** existing `[LOOP BREAKER]` / `[LLM-EMPTY]` / `[LLM-HA]` telemetry lines — dual emit during transition; consolidation deferred to phase-3 (`[gated-by OQ7]`).
- **Independent of:** phase-2 enrollments (ghost-promise / truncated / S1-post-ladder) — they ride the proven carrier but their workstreams are separate. Phase-1 must NOT pre-implement any phase-2 surface.
- **Independent of:** watchover 3-strike termination (P-4) — partition non-negotiable.

---

## Risks (phase-1-specific; cross-referenced to plan-overview R1-R7)

| # | Risk (source) | Impact | Likelihood | Mitigation |
|---|---------------|--------|------------|------------|
| P1-R1 | **Hottest-path surgery** — touches `agent_node` return assembly (`:5612-5638`) shared with L2 compaction | High | Medium | Workstream A tests (T-3 canary); T-8 golden routing pins; SC-11 L2 regression suite; symbol-pinned not line-pinned |
| P1-R2 | **`checkpoint_ns` empty-snapshot trap** — `aget_state` with node-stamped config reads EMPTY snapshot | High | Low | P-10 enforces no mid-flight `aupdate_state`; thread-id-only reads (`_compaction_persist_seam.py:139`); G-2 canary mirror |
| P1-R3 | **Repair-storm interplay** with compaction docs | Medium | Medium | Distinct id namespaces (A-4); derived detectors self-clear on repaired tails (T-2); budget cap (B-4) |
| P1-R4 | **Summarizer-as-hallucination-vector** | High | Medium | C-1 facade routing; C-2 fail-open abort; T-5 degenerate summary test; T-11 repair-doc invariants |
| P1-R5 | **Contract regression pressure** (S1 facade-hook temptation post-incident) | High | Medium | ADR-0003 defers it behind OQ1; OFF = byte-identical preserved (D-2); reviewer checklist: "did not touch `llm_error_classifier.py:907/:916`" |

**Phase-1-specific risk (NOT in R1-R7):**
- **P1-R6 — Budget reset policy ambiguity (`[gated-by OQ5]`):** without OQ5 ruling, the default (reset-on-real-HumanMessage) is implemented and pinned; if the architect rules differently post-merge, Workstream B-3 needs re-implementation + T-2 budget-reset test re-run. Mitigation: pin B-3 default explicitly in code comment + test docstring.

---

## Test Plan (mapped to `technical-analysis.md` §Test-Critical Invariants T-1 … T-13)

| T-n | Title | Tests authored in workstream | Coverage |
|-----|-------|-------------------------------|----------|
| **T-1** | Joint loop-breaker × empty-guard integration test | E-1, E-2 | Continuous-empty provider + loop-prone tool storm; bounded repairs + S5/S1; correct class attribution; no budget cross-consumption; SQLite + PG both pass |
| **T-2** | Durability across restart/revive | E-1 (restart branch), G-1 (B-3) | Restart after repair → no re-trip; budget persists across restart; budget NOT reset by intervening clean turn unless OQ5 rules so (B-3 default = reset-on-real-HumanMessage) |
| **T-3** | Return-carried correctness | G-2 canary | Sentinel element-0 (P-6); repaired history upserts with ORIGINAL ids (P-7); NO `aupdate_state`-only mid-turn persist (P-10); mirrors `TestMidSuperstepPersistCanary` |
| **T-4** | Tool-pairing | A-6, G-1 (P-8) | Post-surgery every retained ToolMessage still pairs with its issuing AIMessage; repair-doc idempotent under re-compaction |
| **T-5** | Summarizer degeneracy / fail-open abort | C-2, C-3 | Empty/degenerate summarizer output → facade retry → failover → fail-open abort → next rung; NEVER the silent static fallback of today (`:1632-1636`); `[SYMPTOM] … repair_abort` asserted |
| **T-6** | Exhaustion escalation | D-1 | Budget exhausted → loud terminal; OFF-flag → shipped WARN+continue preserved |
| **T-7** | Partition pins | G-1 | Per-class detector truth tables including cross-partition negatives (tool-call message never counted by ghost/degenerate detectors; watchover-denied batch never triggers repair) |
| **T-8** | Kill-switch golden routing | F-1, F-2, G-1 (P-11) | OFF → byte-identical (the empty-guard test pattern: pin shipped behavior BEFORE flipping anything; grep-pin the single-caller invariants where they exist, e.g. `_is_empty_content` one-prod-caller pin precedent) |
| **T-9** | Burn-count assertions | E-1 (loop branch) | Loop storm ≤ budget(3) + detections; offline counting pins |
| **T-10** | Placement pins | F-2, G-1 | S1 raise still inside retry scope (`llm_error_classifier.py:907/:916` untouched — source-level pin; AsyncMock + `inspect.getsource` substring assertion per the facade-forwarding discipline); S5 cap semantics unchanged |
| **T-11** | Repair-doc invariants | G-3 | Construction-time id present; non-selectable/hoisted partition treatment; verbatim-excerpt pinning |
| **T-12** | Persist-refusal handling | C-3 | `persist_compaction_result`-style False return → abort; budget not consumed; loud telemetry |
| **T-13** | Test runner discipline | All | All tests via `uv run python -m pytest` from worktree root (no bare `pytest`); SQLite + PG dual coverage (disposable-PG14 recipe) |

**Test-critical invariants NOT covered by phase-1 (deferred to phase-2/3):**
- Burn-count for ghost storm (phase-2; T-9 ghost branch).
- Truncated partial preservation (phase-2; T-11 repair-doc invariants for excerpt section).
- S1 post-ladder pre-terminal repair placement test (phase-2).

---

## Exit Criterion

Phase-1 is DONE when:

1. **Workstreams A-G all complete** with their acceptance tests green on SQLite AND disposable PostgreSQL (T-13, T-2, SC-12).
2. **All 12 partition invariants (P-1 … P-12)** have regression tests passing (G-1, T-7).
3. **T-1 joint loop-breaker × empty-guard integration test** passes (E-1, E-2) — this is the test that verifies no shipped empty-guard contract regresses AND the ladder adds value on the loop class.
4. **Kill-switch OFF golden routing** preserved byte-identically (T-8, SC-5, D-2).
5. **Exhaustion escalation** works under flag ON (loud terminal + class attribution) and is preserved under flag OFF (shipped WARN+continue) (T-6, SC-2, D-1).
6. **Summarizer fail-open abort** never silently degrades (T-5, SC-8, C-2).
7. **No regression in shipped empty-guard test suite** (SC-3): all `tests/unit/test_empty_response_guard*` + `tests/integration/test_empty_response_guard*` still green.
8. **No regression in proactive compaction L2 durability** (SC-11): regression suite for `graph.py::_maybe_precall_compact_95` still green.
9. **Activation runbook** authored (H-1) — pause-first restart per repo convention.
10. **Doc correction** committed in same merge (H-2): `docs/hallucination-protection.md` §7 secondary-surface claim updated, drift corrected.

**Phase-2 cannot start until**:
- Phase-1 ships + soaks for at least **one restart cycle** with the new GraphState field (durability verification, SC-1).
- At least one `[SYMPTOM] class=loop phase=terminal reason=repair-budget-exhausted` event is observed in soak (proves exhaustion escalation fires).
- OQ1, OQ2, OQ3 architect rulings are received (gating phase-2 tasks).

---

## Cross-References

- Design basis (anchors, matrices, risks): `technical-analysis.md` (same directory).
- ADRs: `decisions.md` ADR-0001 (unified engine), ADR-0002 (phase-1 = durable loop conversion), ADR-0003 (placement), ADR-0004 (escalation-state home), ADR-0005 (budget durability + exhaustion escalates), ADR-0006 (summarizer through HA facade + fail-open abort), ADR-0008 (kill-switch convention).
- OPEN QUESTIONS (gating): `decisions.md` OQ5 (durable-budget reset policy — directly gates B-3).
- Phase overview (symptom-class matrix, phase table, coupling map, risks, success criteria): `plan-overview.md` (same directory).
- Shipped-guard contracts this phase must not regress: `empty-response-guard/architecture-recommendation.md` §5 (Option 4), §6 (L1-L13), §8.1 (nudge allowance synthesis).
- Pause-first restart runbook pattern: `.agents/shared/planning/kv-ambient-awareness-fix/plan-overview.md` Rollout section.
- Activation convention (kill-switch default ON, restart-pending): repo conventions blueprint + `adr-0001-dropped-empty-content-check.md` consequences block.
