# Phase 3: Detector-Gated Class Enrollment

**Date:** 2026-09-13
**Author:** planner[v2] via plan-creation worker
**Status:** Draft — explicitly gated on phase-1 + phase-2 soak telemetry + detector designs (none exist today)

**Observed git state (verified before writing):**
- Branch: `plan/long-tool-call-nudge` (dispatcher-stated branch `plan/hallucination-recovery-ladder`; worktree authored here; see plan-overview.md)
- HEAD: `0acd3afae2d7f25e8c15d44f7802c4f23f5975b1` (short `0acd3afa`), clean tree at write time.
- Anchor drift note: SYMBOL NAMES authoritative, line numbers re-verified at implementation.
- **Phase-1 prerequisite:** shipped + soaked; durable carrier, budget, summarizer facade, kill-switch all green.
- **Phase-2 prerequisite:** shipped + soaked; ghost/truncated/empty-post-ladder enrollments all green; `[SYMPTOM]` telemetry lines observed in soak for each class.

---

## Objective

Enroll the four candidate hallucination-class symptoms whose **detection is currently missing** — repeated identical FINAL answers (non-tool), tool storms with drifting params, schema/format violations, stuck-without-progress — **only AFTER the corresponding detectors are designed and proven** on telemetry from phase-1/2 soak. Each enrollment is a new preset row + detector; no engine internals change.

> *Single sentence: under per-class sub-flags (default OFF until soaked) and the master `ENSEMBLE_SYMPTOM_REPAIR_LADDER`, each detector-gated class routes through the phase-1 carrier with its own preset and detector, with the same durable budget, summarizer facade, telemetry shape, and OFF = byte-identical routing — and only IF the corresponding detector's FP profile justifies enrollment after phase-1/2 soak.*

---

## Scope

### In Scope (detector-gated enrollments on proven carrier)

1. **Repeated identical FINAL answers (non-tool)** — row 10 in symptom matrix. **Detector design first.** New detector walks plain AIMessages (current `LoopDetector` breaks at `daemon/graph.py:1110-1113`); high FP risk (legitimate repeated answers exist: confirmations, status echoes); watchover-adjacent — partition discipline required (must not steal watchover's 3-strike termination).
2. **Tool storms with drifting params** — row 11 in symptom matrix. **Detector design gated on the no-progress signature** (empty-guard Phase-2 item (5) "LoopDetector no-progress signature" — same program, same prerequisite). Exact-signature chain breaks on any param drift (`daemon/graph.py:1002-1022`); only `recursion_limit=300` bounds the burn today.
3. **Schema/format violations** — row 12 in symptom matrix. **Detector design gated on a generic validator + exemption analysis** (a NEW detection surface with its own L1-L13-style exemption analysis). The malformed-tool-call lane (`daemon/response_validation.py:459-464`) already retries today.
4. **Stuck-without-progress** — row 13 in symptom matrix. **Detector design gated on a progress metric** ("what is progress?" needs its own mini-analysis). Highest design risk.
5. **Bulk delegation to `compact_state`** (hybrid escape hatch) — only IF `[SYMPTOM] repair` events show large-window cases that exceed the hard size cap. `[gated-by OQ6]`.
6. **S5-cap-exceeded repair routing** — OPTION only, only if soak shows value. `[gated-by OQ4]`.
7. **Telemetry consolidation** (single line for all symptoms) — only after one soak cycle. `[gated-by OQ7]`.

### Out of Scope (NEVER enrolled — partition non-negotiables)

- **Wrong-language** — fail-open at `LANGUAGE_CHECK_MAX_RETRIES=2` (`daemon/graph.py:2833-2837`); covered at rung-1.
- **CLE (ContextLengthExceeded) handler** — already a rung-2 shape (`daemon/graph.py:5345-5394`); its superseded persist defect (`:5396-5398`) is a **separate** defect — do not duplicate, do not fix here.
- **Bare-JSON provider body** — `MalformedLLMResponseError` is `TRANSIENT_EXCEPTIONS` (`llm_error_classifier.py:466`); provider-failure framing owns it.
- **Watchover-denied classes** — 3-strike is watchover's (P-4); partition non-negotiable.
- **GII tool-throttle storms** — own escalating backoff (mechanism #10); partition respect.

### Adjacent (NOT this phase)
- Phase-1 carrier + phase-2 presets — INHERITED AS-IS. Phase-3 adds new preset rows AND new detectors.
- Detector design itself — gated on its own work (no detector exists for any phase-3 candidate today).
- Severity promotion `validation_error` → `max_retries_exceeded` in `CRITICAL_ERROR_TYPES` — separate workstream.
- Provider-health circuit (Option-3 core) — separate workstream; telemetry-gated.

---

## Tasks

> Workstream → Task → Touch-site (symbol + file) → Acceptance test.
> Acceptance tests are described; full code is out of scope per the plan-creation worker contract.
> **Phase-3 work is gated by detector designs** — the design tasks (A-1, B-1, C-1, D-1 below) are prerequisites to any enrollment tasks. Enrollment without a proven detector is explicitly prohibited by ADR-0007.

### Workstream A — Repeated identical FINAL answers (non-tool)

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| A-1 | **Detector design** (NEW — gated): design a non-tool signature that distinguishes hallucination-repetition from legitimate repetition (confirmations, status echoes). Candidates: (a) N consecutive identical final AIMessages (no tool calls, no reasoning_content) beyond a context-dependent threshold; (b) "same-content-after-non-tool-action" pattern; (c) LLM-judge-assisted confirmation. Watchover-adjacent — must not steal watchover's 3-strike | OQ-architect ruling on detector approach (no ruling today) | NEW module `daemon/services/detectors/repeated_final.py` (TBD by Developer) | Detector design doc + truth-table analysis; FP profile on a labeled corpus; L1-L13-style exemption analysis (confirmations, status echoes, intent repeats) |
| A-2 | Author detector unit tests: truth table (identical finals, legitimate repetitions, mixed-with-tool-calls, mixed-with-reasoning_content); cross-partition negative (tool-call message never counted — P-1) | A-1 | `tests/unit/test_detector_repeated_final.py` (NEW) | Tests pass; FP profile documented |
| A-3 | Add `repeated_final` preset row to `SymptomRepairEngine`: evidence-window selector = the trailing repeated-final AIMessages; summary-prompt fragment = "model is repeating identical non-tool final answers"; retention set = all preceding messages with ORIGINAL ids (P-7); post-repair routing = continue on next invoke | A-1, A-2 | `daemon/services/symptom_repair_engine.py` (preset table, new row) | Unit test: preset row loads; detector input → surgery output |
| A-4 | Wire detector + preset at the pre-LLM middleware slot (same slot as the loop breaker: `daemon/graph.py:5186`); watchover-deny path respects P-4 exclusion (`:1066-1102`) | A-3 | `daemon/graph.py:5186` (region); NEW detector module | Integration test: detector fires → repair → re-entry; watchover-deny path skips detector |
| A-5 | Sub-flag `ENSEMBLE_REPAIR_REPEATED_FINAL` (default OFF until soaked): `_resolve_repair_repeated_final` in `daemon/config.py` | A-3 | `daemon/config.py` | Unit test: env var resolution + default OFF + ValueError on invalid |
| A-6 | Telemetry: `[SYMPTOM] class=repeated_final phase=* …`; OFF-mode still emits telemetry (W1 KEEP) | A-3 | `daemon/graph.py` | Unit test: every transition emits the correct `[SYMPTOM]` line; OFF-mode telemetry preserved |

### Workstream B — Tool storms with drifting params

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| B-1 | **Detector design** (NEW — gated, SAME PROGRAM as empty-guard Phase-2 item (5)): design a drift / no-progress signature. Candidates: (a) "no-progress" = trailing N consecutive tool calls with zero state advancement (e.g. no new files created, no API responses that change downstream reasoning); (b) "drift" = trailing N consecutive tool calls where args show monotonic divergence; (c) combined no-progress + drift. **Empty-guard Phase-2 item (5) is the same work** — same program, same prerequisite (technical-analysis.md §DQ5 reconciliation) | OQ-architect ruling on detector approach (no ruling today) | NEW module `daemon/services/detectors/no_progress.py` (TBD by Developer) | Detector design doc + truth-table analysis; FP profile on a labeled corpus; L1-L13-style exemption analysis |
| B-2 | Author detector unit tests: truth table (drift, no-progress, mixed-with-clean-tools, mixed-with-progress-markers); cross-partition negative (clean-progress tool calls never counted) | B-1 | `tests/unit/test_detector_no_progress.py` (NEW) | Tests pass; FP profile documented |
| B-3 | Add `drifting_tools` preset row: evidence-window selector = the trailing N drifting tool-call AIMessages (with their paired ToolMessages folded per P-8); summary-prompt fragment = "tool calls are not making progress"; retention set = preceding messages; post-repair routing = continue on next invoke | B-1, B-2 | `daemon/services/symptom_repair_engine.py` (preset table, new row) | Unit test: preset row loads; detector input → surgery output; tool-pairing invariant (P-8) |
| B-4 | Wire detector + preset at the pre-LLM middleware slot | B-3 | `daemon/graph.py:5186` (region) | Integration test: detector fires → repair → re-entry |
| B-5 | Sub-flag `ENSEMBLE_REPAIR_DRIFTING_TOOLS` (default OFF until soaked): `_resolve_repair_drifting_tools` in `daemon/config.py` | B-3 | `daemon/config.py` | Unit test: env var resolution + default OFF + ValueError on invalid |
| B-6 | Telemetry: `[SYMPTOM] class=drifting_tools phase=* …`; OFF-mode still emits telemetry (W1 KEEP) | B-3 | `daemon/graph.py` | Unit test: every transition emits the correct `[SYMPTOM]` line; OFF-mode telemetry preserved |
| B-7 | Cross-class isolation test (P-9): assert loop-class detection (phase-1) and drifting-tools detection (phase-3) coexist without consuming each other's budget; if the same tool-call signature satisfies BOTH detectors, the loop-class takes precedence (lower latency, proven) | B-3, phase-1 | `tests/integration/test_ladder_drift_x_loop.py` | Test passes; budget isolation pinned |

### Workstream C — Schema/format violations

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| C-1 | **Detector design** (NEW — gated): design a generic validator with exemption analysis. The malformed-tool-call lane (`daemon/response_validation.py:459-464`) already retries today — phase-3 generic validator must COEXIST, not duplicate. Candidates: (a) JSON-schema validation against agent-declared output schemas (declared in `meta.json` or prompt-contract); (b) structural validator (e.g. required keys, type checks); (c) per-tool declared output schemas. **A generic validator is a NEW detection surface with its own L1-L13-style exemption analysis** — design doc MUST precede implementation | OQ-architect ruling on validator approach + exemption analysis (no ruling today) | NEW module `daemon/services/detectors/schema_validator.py` (TBD) | Detector design doc + truth-table analysis; L1-L13-style exemption analysis; coexistence with malformed-tool-call lane |
| C-2 | Author detector unit tests: truth table (declared schema, missing required field, wrong type, empty struct, list vs object); exemption set (designed-empty finals, intentional partial output) | C-1 | `tests/unit/test_detector_schema.py` (NEW) | Tests pass; FP profile documented |
| C-3 | Add `schema` preset row: evidence-window selector = the schema-violating AIMessage(s); summary-prompt fragment = "schema validation failed; see validator output"; retention set = preceding messages; post-repair routing = continue on next invoke | C-1, C-2 | `daemon/services/symptom_repair_engine.py` (preset table, new row) | Unit test: preset row loads; detector input → surgery output |
| C-4 | Wire detector + preset at the pre-LLM middleware slot | C-3 | `daemon/graph.py:5186` (region) | Integration test: detector fires → repair → re-entry |
| C-5 | Sub-flag `ENSEMBLE_REPAIR_SCHEMA` (default OFF until soaked): `_resolve_repair_schema` in `daemon/config.py` | C-3 | `daemon/config.py` | Unit test: env var resolution + default OFF + ValueError on invalid |
| C-6 | Telemetry: `[SYMPTOM] class=schema phase=* … detail=<schema-violation-type>`; OFF-mode still emits telemetry (W1 KEEP) | C-3 | `daemon/graph.py` | Unit test: every transition emits the correct `[SYMPTOM]` line; OFF-mode telemetry preserved |

### Workstream D — Stuck-without-progress

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| D-1 | **Detector design** (NEW — gated): define "progress" as a measurable signal. **Highest design risk** — what counts as progress? Candidates: (a) N consecutive supersteps with zero message-channel change AND zero state-channel change; (b) N consecutive invocations with monotonically similar output entropy; (c) watchover-style marker scan. **Needs its own mini-analysis** before enrollment | OQ-architect ruling on progress metric (no ruling today) | NEW module `daemon/services/detectors/stuck.py` (TBD) | Detector design doc + mini-analysis report; truth-table analysis; FP profile |
| D-2 | Author detector unit tests: truth table (stuck-clean, stuck-with-noise, stuck-but-progressing); cross-partition negative (long-running tasks with intentional silence never counted) | D-1 | `tests/unit/test_detector_stuck.py` (NEW) | Tests pass; FP profile documented; long-running task scenario included |
| D-3 | Add `stuck` preset row: evidence-window selector = the trailing N "stuck" supersteps' AIMessages; summary-prompt fragment = "task is stuck without progress; consider re-orienting"; retention set = preceding messages; post-repair routing = continue on next invoke | D-1, D-2 | `daemon/services/symptom_repair_engine.py` (preset table, new row) | Unit test: preset row loads; detector input → surgery output |
| D-4 | Wire detector + preset at the pre-LLM middleware slot | D-3 | `daemon/graph.py:5186` (region) | Integration test: detector fires → repair → re-entry |
| D-5 | Sub-flag `ENSEMBLE_REPAIR_STUCK` (default OFF until soaked): `_resolve_repair_stuck` in `daemon/config.py` | D-3 | `daemon/config.py` | Unit test: env var resolution + default OFF + ValueError on invalid |
| D-6 | Telemetry: `[SYMPTOM] class=stuck phase=* … detail=<stuck-window-length>`; OFF-mode still emits telemetry (W1 KEEP) | D-3 | `daemon/graph.py` | Unit test: every transition emits the correct `[SYMPTOM]` line; OFF-mode telemetry preserved |

### Workstream E — Hybrid escape hatch (bulk delegation) `[gated-by OQ6]`

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| E-1 | **Hard size cap on repair window** (invariant per OQ6 recommended (c)): abort repair if evidence window exceeds a threshold; fall through to existing backstops; this is an engine-level invariant, NOT a per-class setting | A-3, B-3, C-3, D-3 (or whichever detectors shipped) | `daemon/services/symptom_repair_engine.py` (window-size guard) | Unit test: window over threshold → abort; budget NOT consumed; loud telemetry |
| E-2 | **Bulk delegation** (conditional, default NOT wired): IF `[SYMPTOM] repair` events show large-window cases (per OQ6 recommended (a)), wire `compact_state(force=True)` as a fallback behind an OFF-default sub-flag `ENSEMBLE_REPAIR_BULK_DELEGATION`. Re-imports the engine gates (`compaction.py:2098` dedup, `:2198-2208` min-messages); cost envelope (batched 20 × concurrency 3 + serial merge `:2800-2861`) | E-1, OQ6 ruling | `daemon/services/symptom_repair_engine.py`; `daemon/compaction.py` (call site) | Unit test: bulk path invoked only when window over threshold AND sub-flag ON; else hard cap abort |
| E-3 | Documented acceptance criterion: large-window cases observed in soak telemetry BEFORE bulk path is wired (this is a "revisit if telemetry shows X" task, not a "wire now" task) | E-1 | Doc note | Telemetry evidence cited in PR description |

### Workstream F — S5-cap-exceeded repair routing `[gated-by OQ4]`

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| F-1 | **Sub-flag `ENSEMBLE_REPAIR_S5_AT_CAP`** (default OFF until soaked per OQ4 recommended (a)): `_resolve_repair_s5_at_cap` in `daemon/config.py`; same `_resolve_*` discipline | OQ4 ruling | `daemon/config.py` | Unit test: env var resolution + default OFF + ValueError on invalid |
| F-2 | **Routing change** (gated, off by default): at S5 cap (`graph.py:2620`; `:2651-2666`), OPTIONALLY route to bare-drop repair instead of loud END; co-located with the S5 helpers; coexists with shipped loud END via the sub-flag | F-1 | `daemon/graph.py:2620` (region); `daemon/graph.py:2651-2666` | Unit test: sub-flag OFF → shipped loud END preserved; sub-flag ON → bare-drop repair path |
| F-3 | Telemetry: `[SYMPTOM] class=degenerate phase=* …`; OFF-mode still emits telemetry (W1 KEEP); retained S5 lines `[LLM-EMPTY]` during transition | F-2 | `daemon/graph.py` | Unit test: every transition emits the correct `[SYMPTOM]` line; OFF-mode telemetry preserved |

### Workstream G — Telemetry consolidation `[gated-by OQ7]`

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| G-1 | **Consolidate `[SYMPTOM]` into existing lines** (per OQ7 recommended (a)): single gating helper for all symptom streaks; remove dual emit (per E-1 from phase-2); single `[SYMPTOM]` line becomes canonical | Phase-2 E-1, OQ7 ruling | `daemon/graph.py:5552-5577` (the `[LLM-EMPTY]` block — pattern reference); `daemon/services/symptom_repair_engine.py` (gating helper) | Unit test: single line emitted; existing lines retired; OFF-mode preserved |
| G-2 | **FE SSE renderer** (NOT this phase, but flagged for parallel workstream): if the ladder's loud terminals surface via SSE, users see nothing in FE today (pre-existing gap). **Out of scope here**; flagged in plan-overview.md §Open Questions; separate workstream. | OQ7 ruling + FE team | n/a | Doc note; flag filed |

### Workstream H — Rollout (per-class sequential soak)

| # | Task | Depends On | Touch-site (symbol + file) | Acceptance |
|---|------|------------|----------------------------|------------|
| H-1 | **Per-class sequential soak**: each phase-3 class enrolls ONE AT A TIME, with its own sub-flag OFF→ON transition after its dedicated detector's soak window | A-3, B-3, C-3, D-3 (each independent) | n/a (process) | Per-class activation PR; per-class soak log |
| H-2 | Pause-first restart runbook per phase-1's runbook; per-class sub-flag independent OFF paths; activation gated on per-class detector soak window | H-1 | New section in `plan-overview.md` | Runbook pinned |
| H-3 | Merge to `latest` branch with `--no-ff` per repo convention (h); atomic per-class sub-flag commits | H-1, H-2 | n/a | PR merged |

---

## Coupling

- **Tight with:** **phase-1 carrier + budget + summarizer + telemetry surface** — phase-3 inherits ALL of these as-is. New preset rows + new detectors; no engine internals change.
- **Loose with:** phase-2 enrollments (ghost/truncated/empty-post-ladder) — per-class isolation via P-9 partition; cross-class budget isolation tested in B-7.
- **Independent of:** watchover 3-strike termination (P-4) — partition non-negotiable; A-1, A-4 explicitly respect it.
- **Independent of:** CLE, wrong-language, bare-JSON, GII throttle — NEVER enrolled; partition respect.

---

## Risks (phase-3-specific; cross-referenced to plan-overview R1-R7)

| # | Risk (source) | Impact | Likelihood | Mitigation |
|---|---------------|--------|------------|------------|
| P3-R1 | **Detector-FP risk** (all four classes) — every new detector adds FP surface that can over-repair legitimate content; legacy `LoopDetector` (`graph.py:1002-1022`) breaks on plain AIMessages — repeated-finals detector is structurally new | High | High | **Each detector's design doc is a gating prerequisite** (A-1, B-1, C-1, D-1); per-class sub-flags default OFF until soaked; per-class sequential soak (H-1); L1-L13-style exemption analysis for each |
| P3-R2 | **Watchover partition violation** (repeated-finals) — the watchover-adjacent class risks stealing watchover's 3-strike termination role | High | Medium | A-1 explicitly forbids stealing watchover's role; P-4 partition invariant + A-4 watchover-deny exclusion (`:1066-1102`); partition unit tests |
| P3-R3 | **No-progress signature ambiguity** ("what is progress?") — highest design risk per technical-analysis §Enrollment Recommendations | High | High | D-1 mini-analysis is a gating prerequisite; long-running task scenario in D-2 test; D-5 sub-flag default OFF |
| P3-R4 | **Schema validator coexistence** with malformed-tool-call lane (`response_validation.py:459-464`) — must NOT duplicate; coexistence design is a gating prerequisite | Medium | Medium | C-1 exemption analysis + coexistence design doc; C-3 preset coexists with existing retry lane |
| P3-R5 | **Bulk delegation cost envelope** (`compaction.py:2800-2861`) — disproportionate if invoked too aggressively | Medium | Low | E-1 hard size cap (invariant, not setting); E-2 sub-flag default OFF; E-3 telemetry-evidence-before-wiring discipline |
| P3-R6 | **S5-at-cap routing change** (`graph.py:2620`/`:2651-2666`) — re-opens shipped loud END contract (Option 4 review-approved) | Medium | Low | F-1 sub-flag default OFF; F-2 coexists with shipped loud END via sub-flag; OFF-mode byte-identical preserved; OQ4 ruling required |
| P3-R7 | **Telemetry consolidation** (G-1) loses signal if rushed | Low | Low | Gated on one full soak cycle (OQ7 recommended (a)); dual emit during transition (phase-2 E-1 pattern) |

**Phase-3-specific risk (NOT in R1-R7):**
- **P3-R8 — Detector design without FP profile (the meta-risk):** if any detector ships without its own labeled-corpus FP profile and exemption analysis, the entire ladder's credibility degrades. Mitigation: **every detector task in this phase is gated on its design doc + truth-table analysis as a hard prerequisite**; sub-flags default OFF until proven.

---

## Test Plan (phase-3 additions to phase-1's T-1 … T-13)

| T-n (phase-1/2) | Phase-3 extension | Tests authored in workstream |
|-----------------|-------------------|-------------------------------|
| **T-1** | (Per-class joint tests below; no new joint test for phase-3) | — |
| **T-2** | Per-class durability across restart (each detector's repair-doc persists) | Per-class integration tests |
| **T-3** | Per-class sentinel element-0; per-class original-id tail | Per-class preset unit tests |
| **T-4** | Tool-pairing (drifting-tools folds via P-8; stuck may include tool-call AIMessages) | B-3, D-3 (preset unit tests) |
| **T-5** | Per-class summarizer degeneracy — same fail-open abort path | Per-class preset unit tests |
| **T-6** | Per-class exhaustion (each detector's budget exhaust → loud terminal) | Per-class cap-exhaustion tests |
| **T-7** | Per-class partition pins — watchover-deny exclusion; tool-call message cross-partition negative; long-running task stuck-detector negative | A-4, B-2, C-2, D-2 (partition unit tests) |
| **T-8** | Per-class kill-switch golden routing — each sub-flag OFF = byte-identical | Per-class config unit tests + per-class integration OFF-mode branches |
| **T-9** | Burn-count assertions: each detector's worst-case LLM-call burn vs today | Per-class integration tests |
| **T-10** | Per-class placement pins — `llm_error_classifier.py:907/:916` UNCHANGED for any class that raises in retry scope (none of the phase-3 candidates do — all raise in pre-LLM middleware slot) | Source-level pin in code comments |
| **T-11** | Per-class repair-doc invariants | Per-class preset unit tests |
| **T-12** | Per-class persist-refusal handling (inherits from phase-1) | A-1 inherited |
| **T-13** | Test runner discipline — all via `uv run python -m pytest` from worktree root; SQLite + PG dual coverage | All new tests |

**Phase-3 critical invariants:**
- **P3-T14 — Detector FP profile (the meta-test):** each phase-3 detector's truth-table + FP profile documented in its test docstring; reviewer checklist per PR.
- **P3-T15 — Watchover partition (P-4):** repeated-finals detector MUST NOT fire on watchover-denied batches; co-located partition unit tests.
- **P3-T16 — Cross-class isolation (P-9):** drift × loop coexistence test (B-7); long-running task × stuck coexistence test (D-2 long-running scenario).
- **P3-T17 — Per-class sequential soak discipline:** each per-class activation PR cites its detector's soak log; reviewer checklist.

---

## Exit Criterion

Phase-3 is DONE when:

1. **Workstreams A-G all complete** with their acceptance tests green on SQLite AND disposable PostgreSQL (T-13, SC-12).
2. **All four detector-gated enrollments** (or the subset the architect approves) have per-class preset + detector tests passing (A-3, B-3, C-3, D-3).
3. **Per-class sub-flags OFF = byte-identical routing** (T-8, SC-5): `ENSEMBLE_REPAIR_REPEATED_FINAL=0`, `ENSEMBLE_REPAIR_DRIFTING_TOOLS=0`, `ENSEMBLE_REPAIR_SCHEMA=0`, `ENSEMBLE_REPAIR_STUCK=0`, `ENSEMBLE_REPAIR_S5_AT_CAP=0`, `ENSEMBLE_REPAIR_BULK_DELEGATION=0` each preserves shipped behavior.
4. **Detector FP profiles documented** for each enrolled class (P3-T14); labeled-corpus data cited in PR.
5. **Watchover partition** (P-4) holds for repeated-finals detector (A-4, P3-T15); verified live.
6. **Cross-class isolation** (P-9) holds for drift × loop, long-running × stuck (B-7, P3-T16).
7. **Bulk delegation** (Workstream E) gated on `[SYMPTOM] repair` telemetry evidence (E-3); no proactive wiring.
8. **Telemetry consolidation** (G-1) ships only after one full soak cycle (OQ7); dual emit during transition is acceptable.
9. **No regression in shipped empty-guard test suite** (SC-3); no regression in phase-1/2 ladder tests; no regression in proactive compaction L2 durability (SC-11).
10. **Activation runbook** authored (H-1, H-2); per-class sub-flag independent OFF paths verified.

**The ladder program is COMPLETE** when all three phases' exit criteria are met, the consolidated telemetry surface is in place (G-1), and the FE SSE renderer is filed as a separate workstream (out of scope but documented).

---

## Cross-References

- Design basis (anchors, matrices, risks): `technical-analysis.md` (same directory).
- ADRs: `decisions.md` ADR-0001 (unified engine), ADR-0007 (enrollment now/later/never — defines phase-3 candidates + never list), ADR-0008 (kill-switch convention).
- OPEN QUESTIONS (gating): `decisions.md` OQ4 (S5-at-cap), OQ6 (bulk delegation), OQ7 (telemetry consolidation + FE renderer).
- Phase overview (symptom-class matrix, phase table, coupling map, risks, success criteria): `plan-overview.md` (same directory).
- Phase-1 carrier + budget + summarizer + telemetry: `phase1-plan.md` (same directory).
- Phase-2 enrollments (ghost, truncated, empty-post-ladder): `phase2-plan.md` (same directory).
- Empty-guard Phase-2 deferred item (5) "LoopDetector no-progress signature" = SAME PROGRAM = B-1 prerequisite.
- Shipped-guard contracts this phase must not regress: `empty-response-guard/architecture-recommendation.md` §5 (Option 4), §6 (L1-L13), §8.1 (nudge allowance synthesis).
- Partition non-negotiables (NEVER enrolled): P-4 watchover, wrong-language, CLE, bare-JSON, GII throttle.
