# Phase 2: Enroll Ghost-Promise + Truncated + S1-Post-Ladder

**Date:** 2026-09-13
**Author:** planner[v2] via plan-creation worker
**Status:** Draft — gated by phase-1 soak + OQ1 (S1 placement) + OQ2 (ghost FP profile) + OQ3 (truncated partial preservation)

**Observed git state (verified before writing):**
- Branch: `plan/long-tool-call-nudge` (dispatcher-stated branch `plan/hallucination-recovery-ladder`; worktree authored here; see plan-overview.md)
- HEAD: `0acd3afae2d7f25e8c15d44f7802c4f23f5975b1` (short `0acd3afa`), clean tree at write time.
- Anchor drift note: SYMBOL NAMES authoritative, line numbers re-verified at implementation.
- **Phase-1 prerequisite:** phase-1 must ship + soak for at least one restart cycle with the new GraphState field (durability verification, SC-1); at least one `[SYMPTOM] class=loop phase=terminal reason=repair-budget-exhausted` event observed in soak.

---

## Objective

Enroll three new hallucination-class symptoms on the **proven phase-1 carrier** — ghost-promise, truncated, and empty (S1) post-ladder — converting real gaps (uncapped ghost burn, same-context truncated retries, post-ladder empty-poisoning) into `cap → repair → backstop` flows. Three per-class preset rows; no new machinery.

> *Single sentence: under per-class sub-flags (`ENSEMBLE_REPAIR_GHOST_PROMISE`, `ENSEMBLE_REPAIR_TRUNCATED`, `ENSEMBLE_REPAIR_EMPTY_POST_LADDER`) and the master `ENSEMBLE_SYMPTOM_REPAIR_LADDER`, each enrolled class routes through the phase-1 carrier with its own preset, on the same durable budget, with the same summarizer facade, the same telemetry shape, and OFF = byte-identical routing per class.*

---

## Scope

### In Scope (per-class enrollments on proven carrier)

1. **Ghost-promise (row 4 in symptom matrix)** — derived counter + cap + repair-at-cap at the router (`daemon/graph.py:2596-2601` detector → preset-driven surgery). Converts an UNBOUNDED burn (`:2538-2541`) into cap→repair→backstop. `[gated-by OQ2 — detector conservatism]`.
2. **Truncated (`finish_reason=length`, row 5 in symptom matrix)** — pre-terminal placement (ADR-0003); preserves partial text inside the repair doc per architect's call. `[gated-by OQ3 — partial-content preservation]`.
3. **Empty (S1) post-ladder (row 2 in symptom matrix)** — pre-terminal once, budget-gated; fires after shipped ladder exhausts (raise → retries → failover) and BEFORE loud ERROR (`graph.py:5516-5519`). `[gated-by OQ1 — placement / facade-hook alternative]`.
4. **Telemetry unification decision** (empty-guard Phase-2 item (4) folded in per `technical-analysis.md` §DQ4-a): one RAM-vs-instance-row decision for all symptom streaks.
5. **Sub-flag kill-switches** with default-ON-soak semantics: sub-flag default ON only after a soak window (recommended per OQ2); sub-flag default OFF until soak for the others (architect's call).

### Out of Scope (deferred to phase-3 / other workstreams)
- Detector design for repeated identical FINAL answers, tool-storm drift, schema violations, stuck-without-progress (phase-3).
- Bulk delegation to `compact_state` (phase-3, `[gated-by OQ6]`).
- S5-cap-exceeded repair routing (phase-3 OPTION, `[gated-by OQ4]`).
- Telemetry consolidation (post-soak, `[gated-by OQ7]`).
- Facade-hook alternative for S1 (deferred per ADR-0003; revisit post-soak if telemetry shows poisoned-retry success rate ≈ 0).
- Severity promotion `validation_error` → `max_retries_exceeded` in `CRITICAL_ERROR_TYPES` — sequence AFTER this phase.
- Provider-health circuit (Option-3 core) — telemetry-gated, separate workstream; the `[SYMPTOM]` surface is the evidence collector.
- FE SSE error-event renderer — separate workstream; pre-existing gap.

### Adjacent (NOT this phase)
- Phase-1 carrier + budget + summarizer + telemetry surface — INHERITED AS-IS. Phase-2 adds preset rows only; no engine internals change.
- Manager streak → persistent observability metric (folded in here per `technical-analysis.md` §DQ4-a: one decision for all symptom streaks, not two).

---

## Tasks

> Workstream → Task → Touch-site (symbol + file) → Acceptance test.
> Acceptance tests are described; full code is out of scope per the plan-creation worker contract.

### Workstream A — Phase-1 carrier assumption verification (gate)

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| A-1 | **Confirm phase-1 has shipped and soaked**: durable carrier, durable budget, summarizer facade, fail-open abort, kill-switch OFF byte-identity all green; at least one `[SYMPTOM] class=loop phase=terminal reason=repair-budget-exhausted` observed in soak | Phase-1 exit criteria | n/a (review gate) | A formal sign-off in the merge PR description + soak log excerpt; gated task that blocks A-2 onward |
| A-2 | **Confirm OQ1/OQ2/OQ3 architect rulings received**; if any are deferred, halt the corresponding workstream (B/C/D) and proceed only with the others | Phase-1 soak, A-1 | `decisions.md` §OPEN QUESTIONS | A-2 splits into B/C/D gates individually; partial phase-2 is acceptable (architect's call) |
| A-3 | **Re-verify partition-by-shape at this HEAD** (chronic anchor drift): S5 excludes tool_calls (`graph.py:2634-2635`); S1 first-empty-after-tool passes (`response_validation.py:371-372`/`:386-389`); `LoopDetector` breaks on plain AIMessages (`graph.py:1110-1113`); watchover-denied batches excluded (`:1066-1102`) | A-1 | Per-class anchors (above) | Drift audit pinned; any drift triggers plan re-verification before implementation |

### Workstream B — Ghost-promise enrollment `[gated-by OQ2]`

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| B-1 | Add `ghost` preset row to `SymptomRepairEngine` (the engine introduced in phase-1 A-1): evidence-window selector = trailing ghost-promise AIMessages from the last non-ghost boundary; summary-prompt fragment = "what was attempted, with the trailing colon-promise removed"; retention set = all preceding messages with ORIGINAL ids (P-7); post-repair routing = continue on next invoke | A-1 | `daemon/services/symptom_repair_engine.py` (preset table, new row) | Unit test: preset row loads; detector input → surgery output equivalence |
| B-2 | Add derived trailing-ghost-promise counter, mirroring `_count_trailing_degenerate_ai_messages` (`graph.py:2651-2666`); cap `GHOST_PROMISE_REINVOKE_CAP=3` (proposed, mirrors `EMPTY_DEGENERATE_REINVOKE_CAP=3` at `:2620`); helper co-located with the S5 helpers in `graph.py` | B-1 | `daemon/graph.py` (new helper, near `:2651-2666`) | Unit test: trailing count truth table (single ghost / N ghosts / ghosts separated by other content); cap-before-surgery ordering asserted |
| B-3 | Router-level mechanical placement: at the derived cap, `should_continue` (`graph.py:2596-2601` detection row) routes to a **repair-flagged re-entry** instead of the bare `"agent"` re-invoke at `:2601`; cap-before-surgery ordering means surgery only fires after `>=GHOST_PROMISE_REINVOKE_CAP` ghost-promise re-invokes (mitigates R6 FP on legitimate colon-ending content) | B-2 | `daemon/graph.py:2596-2601` + `:2601` re-invoke | Unit test: cap(3) bare re-invokes → repair-flagged re-entry; off-by-one pinned; cross-partition negative: tool-call AIMessage never counted (P-1) |
| B-4 | Repair-once per turn (superstep-scoped RAM latch) — `<=1` repair per turn regardless of class; reset at turn boundary (technical-analysis §DQ3-a) | B-3 | `daemon/services/symptom_repair_engine.py` (shared with phase-1 B-2) | Unit test: two symptoms same turn → only first fires repair; second waits next turn |
| B-5 | Sub-flag `ENSEMBLE_REPAIR_GHOST_PROMISE` (default ON only after soak per OQ2 recommended (a)): `_resolve_repair_ghost_promise` in `daemon/config.py`; empty-string safe; invalid → `ValueError` at boot | B-3 | `daemon/config.py` (new `_resolve_*` helper) | Unit test: env var resolution + default + ValueError on invalid |
| B-6 | Telemetry: `[SYMPTOM] class=ghost phase=<detect|rung1|repair|repair_abort|terminal> action=<fired|skipped|abort|escalate> budget=<used>/<cap> …`; OFF-mode still emits telemetry (W1 KEEP); upgrade the per-occurrence WARN at `graph.py:2600` to counted telemetry | B-3 | `daemon/graph.py:2596-2601` + `:2600` | Unit test: every transition emits the correct `[SYMPTOM] class=ghost` line; OFF-mode telemetry preserved |
| B-7 | FP soak test (per OQ2 recommendation): drive a workload where legitimate colon-ending content appears (code blocks, list intros); assert no false repair fires unless cap reached AND FP-suspect pattern dominates | B-3 | `tests/integration/test_ladder_ghost_promise_soak.py` | Test passes; FP telemetry line `[SYMPTOM] class=ghost phase=detect` (NOT `repair`) for legitimate colons |

### Workstream C — Truncated enrollment `[gated-by OQ3]`

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| C-1 | Add `truncated` preset row: evidence-window selector = the single truncated AIMessage (`finish_reason=length`); summary-prompt fragment = "partial completion lost to token truncation; excerpt preserved below"; retention set = all preceding messages with ORIGINAL ids (P-7); post-repair routing = continue on next invoke; **partial preservation** (per OQ3 architect's call): repair-doc carries `excerpt=<original truncated text verbatim>` field | A-1, OQ3 ruling | `daemon/services/symptom_repair_engine.py` (preset table, new row); repair-doc schema (new `excerpt=` field) | Unit test: preset row loads; truncated AIMessage input → surgery output; repair-doc contains `excerpt=` with ORIGINAL partial verbatim |
| C-2 | Pre-terminal placement (ADR-0003): intercept AFTER the shipped ladder exhausts (raise → retries → failover) and BEFORE loud ERROR (`graph.py:5516-5519`); budget-gated; symptom-persist-after-repair → loud ERROR fires UNCHANGED | C-1 | `daemon/graph.py:5516-5519` (pre-terminal region, NEW intercept; existing loud ERROR untouched) | Unit test: truncated-after-retries-exhaustion → repair intercept → re-entry; symptom-persist-after-repair → loud ERROR unchanged |
| C-3 | Honor raise-in-retry-scope contract (T-10 placement pin): `LLMResponseValidationError` raise for truncation (`response_validation.py:452-457`) is INSIDE the retry scope at `llm_error_classifier.py:916`; repair is a node/router-level state transformation on a LATER superstep — NEVER inside the retry try-block | C-2 | `daemon/llm_error_classifier.py:907/:916` (NOT touched); `daemon/graph.py` (pre-terminal intercept) | Source-level pin: `llm_error_classifier.py:907/:916` UNCHANGED; repair intercept lives in `graph.py`, NOT in `llm_error_classifier.py` |
| C-4 | Lane name-match (P-12): any new `LLMResponseValidationError` subclass for truncated-repair-routing must be added to `daemon/services/message_processing_errors.py:112-165` (existing `EmptyLLMResponseError` listed at `:139`); if NO new subclass is needed (the existing raise + a router-level intercept is sufficient), document explicitly | C-2 | `daemon/services/message_processing_errors.py:112-165` (only if new subclass added) | Source-level pin: if new subclass, listed at `:112-165`; otherwise documented in code comment |
| C-5 | Sub-flag `ENSEMBLE_REPAIR_TRUNCATED` (default OFF until soak per OQ3 conservatism): `_resolve_repair_truncated` in `daemon/config.py`; same `_resolve_*` discipline | C-2 | `daemon/config.py` | Unit test: env var resolution + default OFF + ValueError on invalid |
| C-6 | Telemetry: `[SYMPTOM] class=truncated phase=repair action=fired budget=<used>/<cap> … detail=excerpt-preserved=Nchars` (N = length of preserved excerpt) | C-2 | `daemon/graph.py` (pre-terminal intercept region) | Unit test: every transition emits the correct `[SYMPTOM] class=truncated` line; OFF-mode telemetry preserved |
| C-7 | Partial-preservation invariant test (T-11 repair-doc branch for `excerpt=`): assert repair doc carries verbatim original partial content; no summarizer hallucination of the partial text | C-1 | `tests/unit/test_symptom_repair_doc_excerpt.py` | Test passes; repair-doc `excerpt=` field round-trips through checkpoint serialise/deserialize |

### Workstream D — Empty (S1) post-ladder enrollment `[gated-by OQ1]`

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| D-1 | Add `empty_post_ladder` preset row: evidence-window selector = empty AIMessages in the turn window (post-nudge; per §8.1 nudge-allowance synthesis, second empty after a nudge is the trigger); summary-prompt fragment = "empty responses received after the nudge; tool results retained"; retention set = all preceding messages + tool results with ORIGINAL ids (P-7); post-repair routing = continue on next invoke | A-1, OQ1 ruling | `daemon/services/symptom_repair_engine.py` (preset table, new row) | Unit test: preset row loads; post-nudge empty AIMessage input → surgery output; tool results retained |
| D-2 | Pre-terminal placement (ADR-0003): intercept ONCE after the shipped ladder exhausts (raise → retries → failover via `LLMResponseValidationError` in retry scope at `llm_error_classifier.py:916`) and BEFORE loud ERROR (`graph.py:5516-5519`); budget-gated; symptom-persist-after-repair → loud ERROR fires UNCHANGED. **Wasted retries ≤ `PRIMARY_TRANSIENT_MAX−1` = ≤2 calls** are accepted as the cost of NOT touching the facade (OQ1 quantifies the alternative) | D-1 | `daemon/graph.py:5516-5519` (NEW pre-terminal intercept; existing loud ERROR untouched) | Unit test: continuous-empty × `PRIMARY_TRANSIENT_MAX` retries → pre-terminal repair → re-entry; symptom-persist-after-repair → loud ERROR unchanged; retry count pinned ≤ `PRIMARY_TRANSIENT_MAX` |
| D-3 | Honor raise-in-retry-scope contract (T-10): `EmptyLLMResponseError` raise (which is a `LLMResponseValidationError` subclass — `daemon/response_validation.py:26` ⊂ `:466`) is INSIDE the retry scope at `llm_error_classifier.py:916`; repair is a node/router-level state transformation on a LATER superstep — NEVER inside the retry try-block | D-2 | `daemon/llm_error_classifier.py:907/:916` (NOT touched); `daemon/graph.py` (pre-terminal intercept) | Source-level pin: `llm_error_classifier.py:907/:916` UNCHANGED; repair intercept lives in `graph.py`, NOT in `llm_error_classifier.py` |
| D-4 | L1-L13 preservation (L6 especially): surgery re-emits hoisted `context_kind` blocks + unanswered bare-flag notes in the sentinel prefix (`build_sentinel_replacement` `compaction.py:444-450` shape); nudge/context HumanMessages stay invisible to boundary detection (P-5) | D-1 | `daemon/services/symptom_repair_engine.py` (surgery builder); `daemon/services/context_messages.py:85-110` (pattern reference) | Unit test: surgery with `context_kind` blocks present → re-emitted in prefix; nudge HumanMessages invisible |
| D-5 | Sub-flag `ENSEMBLE_REPAIR_EMPTY_POST_LADDER` (default OFF until soak per OQ1 conservatism): `_resolve_repair_empty_post_ladder` in `daemon/config.py`; same `_resolve_*` discipline | D-2 | `daemon/config.py` | Unit test: env var resolution + default OFF + ValueError on invalid |
| D-6 | Telemetry: `[SYMPTOM] class=empty phase=repair action=fired budget=<used>/<cap> … detail=post-ladder pre-terminal` | D-2 | `daemon/graph.py` (pre-terminal intercept region) | Unit test: every transition emits the correct `[SYMPTOM] class=empty` line; OFF-mode telemetry preserved |
| D-7 | Wasted-retry counter test: assert continuous-empty scenario still makes ≤ `PRIMARY_TRANSIENT_MAX + repair(1) + 1` LLM calls (≤5 vs today's ~100 for reasoning-only sibling — R3-burn asymmetry benchmark, not direct equivalence) | D-2 | `tests/integration/test_ladder_empty_post_ladder.py` | Burn-count assertion per T-9; OFF-mode preserved byte-identically |

### Workstream E — Telemetry unification (empty-guard Phase-2 item (4))

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| E-1 | **One decision for all symptom streaks** (folded empty-guard item (4) per `technical-analysis.md` §DQ4-a): `[LLM-EMPTY]`/`[SYMPTOM]`/`[LOOP BREAKER]` streaks share the same RAM-vs-instance-row decision (recommendation: RAM, non-gating, telemetry-only — same as the existing `[LLM-EMPTY]` precedent) | A-1 | `daemon/graph.py:5552-5577` (the `[LLM-EMPTY]` block — pattern reference) | Unit test: all three telemetry surfaces use the same gating helper; no duplication |
| E-2 | Document the deferred consolidation: dual emit `[SYMPTOM]` + existing lines during transition; consolidation is a phase-3 cleanup (per OQ7 ruling) | E-1 | Doc note in `plan-overview.md` | Doc updated |

### Workstream F — Joint integration tests + burn-count assertions

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| F-1 | Author `tests/integration/test_ladder_ghost_promise.py`: ghost storm scenario; assert ≤ cap(3) + repair(1) + 1 continued = ≤5 LLM calls (vs ~300 today); correct class attribution in `[SYMPTOM]`; loud terminal on cap exhaustion | B-3 | New test file (`tests/integration/`) | Test passes on SQLite + PG |
| F-2 | Author `tests/integration/test_ladder_truncated.py`: truncated-after-retries scenario; partial preservation asserted (`excerpt=` field round-trip); pre-terminal placement; symptom-persist-after-repair → loud ERROR | C-2 | New test file | Test passes; OFF-mode byte-identical preserved |
| F-3 | Author `tests/integration/test_ladder_empty_post_ladder.py`: continuous-empty after nudge; pre-terminal repair intercept; L1-L13 (especially L6 nudge HumanMessages) preserved; OFF-mode byte-identical | D-2 | New test file | Test passes; OFF-mode byte-identical preserved |
| F-4 | Author `tests/integration/test_ladder_class_isolation.py`: assert each class's repair consumes ONLY its own budget (P-9); no cross-class budget theft; dual-budget scenarios (loop + ghost same task) coexist correctly | A-1, B-3, C-2, D-2 | New test file | Test passes; SQLite + PG both green |

### Workstream G — Rollout

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| G-1 | Pause-first restart runbook per phase-1's runbook (mirror, don't diverge); each per-class sub-flag has independent OFF path; activation gated on per-class soak window | F-1, F-2, F-3, F-4 | New section in `plan-overview.md`; activation note in PR description | Runbook pinned; reviewer confirms per-class sub-flag independence |
| G-2 | Merge to `latest` branch with `--no-ff` per repo convention (h); atomic per-class sub-flag commits where applicable | G-1 | n/a | PR merged; reviewer checklist: per-class sub-flag OFF = byte-identical |

---

## Coupling

- **Tight with:** **phase-1 carrier + budget + summarizer + telemetry surface** — phase-2 inherits ALL of these as-is. No engine internals change in phase-2.
- **Tight with:** shipped empty-guard contracts (S1 raise-in-retry-scope; S5 caps; L1-L13) — phase-2 placement rules preserve them; D-3, C-3 source-level pins are the contract gates.
- **Loose with:** empty-guard Phase-2 deferred items (1) exhaustion-signal promotion, (2) provider-health circuit — sequence AFTER phase-2; (4) manager streak → persistent metric — folded INTO phase-2 via Workstream E.
- **Independent of:** phase-3 detector-gated classes — per-class preset is data, not control flow; no engine duplication.
- **Independent of:** watchover 3-strike termination (P-4) — partition non-negotiable.

---

## Risks (phase-2-specific; cross-referenced to plan-overview R1-R7)

| # | Risk (source) | Impact | Likelihood | Mitigation |
|---|---------------|--------|------------|------------|
| P2-R1 | **R6 — Ghost-promise FP** (legitimate colon-ending content) | Medium | Medium | Conservative detector (shipped `graph.py:2596-2601` shape unchanged); cap-before-surgery ordering (B-3); FP soak before default-ON enrollment (B-7); `[gated-by OQ2]` |
| P2-R2 | **R5 — Truncated data loss** | High | Medium | Partial text preserved inside repair-doc `excerpt=` field (C-1, C-7); `[gated-by OQ3]` |
| P2-R3 | **R7 — Contract regression pressure** (S1 facade-hook temptation) | High | Medium | ADR-0003 defers it behind OQ1 with explicit preconditions; D-3 source-level pin: `llm_error_classifier.py:907/:916` UNCHANGED; OFF-mode byte-identical |
| P2-R4 | **Burn-count under pre-terminal placement** (≤ `PRIMARY_TRANSIENT_MAX−1` poisoned re-sends before repair fires) | Medium | Medium | Bounded and accepted (OQ1 quantifies alternative); T-9 burn-count assertion (D-7); no facade contact; OFF-mode preserves shipped behavior |
| P2-R5 | **Lane name-match debt** (P-12) | Low | Low | C-4 explicit decision: add to `message_processing_errors.py:112-165` IF new subclass; otherwise document explicitly in code comment |
| P2-R6 | **Telemetry duplication noise** (dual `[SYMPTOM]` + existing lines during transition) | Low | Medium | E-1 unification decision (one gating helper); consolidation deferred to phase-3 per OQ7 |

**Phase-2-specific risk (NOT in R1-R7):**
- **P2-R7 — Per-class soak gating discipline:** if per-class sub-flags default ON before soak, a ghost FP could over-repair legitimate content. Mitigation: sub-flags default OFF until per-class soak window completes; E-1 telemetry unification makes the soak data readable.

---

## Test Plan (phase-2 additions to phase-1's T-1 … T-13)

| T-n (phase-1) | Phase-2 extension | Tests authored in workstream |
|---------------|-------------------|-------------------------------|
| **T-1** | (No new joint test; phase-2 tests are per-class below) | — |
| **T-2** | Ghost-promise durability across restart; truncated repair-doc survives checkpoint | F-1 (ghost restart branch); C-7 (truncated `excerpt=` round-trip) |
| **T-3** | Per-class sentinel element-0; per-class original-id tail | B-1, C-1, D-1 (preset unit tests); F-4 (class isolation) |
| **T-4** | Tool-pairing (ghost doesn't orphan ToolMessages; truncated is single-message so N/A) | B-1 (unit test for ghost tool-pairing) |
| **T-5** | Per-class summarizer degeneracy — same fail-open abort path | B-1, C-1, D-1 (preset unit tests share phase-1 fail-open machinery) |
| **T-6** | Per-class exhaustion — ghost budget exhausts, truncated budget exhausts, empty-post-ladder budget exhausts | B-3, C-2, D-2 (cap-exhaustion unit tests) |
| **T-7** | Per-class partition pins — ghost cross-partition negative (tool-call AIMessage never counted); truncated partition (single-message, no walk); empty-post-ladder L1-L13 (especially L6 nudge HumanMessages) | B-3, C-2, D-4 (partition unit tests) |
| **T-8** | Per-class kill-switch golden routing — each sub-flag OFF = byte-identical | B-5, C-5, D-5 (config unit tests) + F-1, F-2, F-3 (integration OFF-mode branches) |
| **T-9** | Burn-count assertions: ghost ≤ cap(3) + repair(1) + 1 = ≤5 calls vs ~300 today; empty pre-terminal ≤ `PRIMARY_TRANSIENT_MAX + repair(1) + 1` ≤5 vs ~100 for sibling reasoning-only | F-1, D-7 |
| **T-10** | Per-class placement pins — `llm_error_classifier.py:907/:916` source-level UNCHANGED for truncated and empty-post-ladder | C-3, D-3 (source-level pins in code comments + reviewer checklist) |
| **T-11** | Per-class repair-doc invariants — ghost summary prompt; truncated `excerpt=` field; empty-post-ladder tool-results retention | B-1, C-7, D-1 (preset unit tests); C-7 (excerpt round-trip) |
| **T-12** | Per-class persist-refusal handling (inherits from phase-1) | A-1 inherited |
| **T-13** | Test runner discipline — all via `uv run python -m pytest` from worktree root; SQLite + PG dual coverage | All new tests |

**Phase-2 critical invariants NOT covered by phase-1:**
- **P2-T14 — Per-class isolation (P-9):** F-4 cross-class budget isolation test (loop + ghost same task; loop + empty same task; etc.).
- **P2-T15 — OFF-mode byte-identity per class:** F-1, F-2, F-3 each have an OFF-mode branch asserting byte-identical routing.
- **P2-T16 — FP soak telemetry:** B-7 ghost FP soak test (legitimate colon-ending content does NOT fire repair).

---

## Exit Criterion

Phase-2 is DONE when:

1. **Workstreams A-G all complete** with their acceptance tests green on SQLite AND disposable PostgreSQL (T-13, SC-12).
2. **All three enrollments (ghost, truncated, empty-post-ladder)** have per-class preset tests passing (B-1, C-1, D-1).
3. **Per-class sub-flags OFF = byte-identical routing** (T-8, SC-5): `ENSEMBLE_REPAIR_GHOST_PROMISE=0`, `ENSEMBLE_REPAIR_TRUNCATED=0`, `ENSEMBLE_REPAIR_EMPTY_POST_LADDER=0` each preserves shipped behavior.
4. **Joint integration tests** pass on SQLite + PG: ghost storm, truncated-after-retries, empty-post-ladder, class isolation (F-1, F-2, F-3, F-4).
5. **Source-level placement pins hold**: `llm_error_classifier.py:907/:916` UNCHANGED (C-3, D-3); S5 cap semantics unchanged (T-10); L1-L13 exemption set preserved (L6 tested in D-4).
6. **Burn-count assertions** hold: ghost ≤5 calls (vs ~300 today); empty post-ladder ≤5 calls (vs ~100 for sibling); OFF-mode preserved (T-9).
7. **No regression in shipped empty-guard test suite** (SC-3): all `tests/unit/test_empty_response_guard*` + `tests/integration/test_empty_response_guard*` still green.
8. **No regression in phase-1 ladder tests** (T-1, T-2, T-6 etc.): all phase-1 integration tests still green.
9. **Telemetry unification decision** documented (E-1, E-2); dual emit during transition is acceptable per OQ7.
10. **Activation runbook** authored (G-1); per-class sub-flag independent OFF paths verified.

**Phase-3 cannot start until:**
- Phase-2 ships + soaks for at least **one restart cycle** with all three new sub-flags enabled.
- `[SYMPTOM]` lines for each class observed in soak with both `phase=detect` (true positives + FP probes) AND `phase=repair` (repair fires) events.
- Per-class OFF = byte-identical pins verified live.
- OQ4 (S5-at-cap repair routing), OQ6 (bulk delegation) architect rulings received (phase-3 specific).

---

## Cross-References

- Design basis (anchors, matrices, risks): `technical-analysis.md` (same directory).
- ADRs: `decisions.md` ADR-0003 (mechanical placement per class), ADR-0007 (enrollment now/later/never), ADR-0008 (kill-switch convention).
- OPEN QUESTIONS (gating): `decisions.md` OQ1 (S1 placement), OQ2 (ghost FP), OQ3 (truncated partial).
- Phase overview (symptom-class matrix, phase table, coupling map, risks, success criteria): `plan-overview.md` (same directory).
- Phase-1 carrier assumption + workstream gates: `phase1-plan.md` (same directory).
- Shipped-guard contracts this phase must not regress: `empty-response-guard/architecture-recommendation.md` §5 (Option 4), §6 (L1-L13), §8.1 (nudge allowance synthesis).
- Empty-guard Phase-2 deferred items reconciliation: `technical-analysis.md` §DQ5; items (3) folded into phase-1 (ADR-0006); item (4) folded into phase-2 (E-1); items (1)+(2)+(5) defer per disposition table.
