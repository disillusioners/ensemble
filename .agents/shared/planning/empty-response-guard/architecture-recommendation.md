# Architecture Recommendation — Continuous-Empty-AI-Message Guard (`empty-response-guard`)

**Date:** 2026-09-12
**Branch observed:** `plan/empty-response-guard` @ `3a9357e8` (giter's switch landed before write)
**Provenance:** Architect council (2-model: `worker@agentic` + `worker@coding`, skill `resilience-design`, governor `fdb8a7f1`), 2 rounds, converged 2/2 after adjudicating 5 factual disputes. Load-bearing anchors independently spot-verified by the architect before writing (see §3.1).
**Status:** Recommended design approved for planning → implementation. Analysis only — no code changed.

---

## 1. Context & Incident

An OpenAI-compatible provider returns **continuous empty AI messages** (HTTP 200; content `None`/`""`/whitespace; including the reasoning-only variant where `reasoning_content` is set but `content` is empty, and the `<think>`-tag-only variant). Today the daemon:

- completes turns as **silent empty "successes"** — the empty AIMessage is checkpointed as the final answer, or
- for reasoning-only / `<think>`-tag-only empties, **re-invokes the LLM with no counter** until `GraphRecursionError` at `graph_recursion_limit=100` (~100 LLM calls burned per incident).

The protection lattice (22 mechanisms catalogued in `docs/hallucination-protection.md`) never inspects content emptiness on the success path: "the one hole in the lattice."

## 2. Verified Root-Cause Chain

1. `validate_llm_response` (`daemon/response_validation.py:24-60`) intentionally skips empty content (docstring :34-36: "Empty content is intentionally NOT validated here…").
2. Retry classification / failover are purely exception-driven (`daemon/llm_error_classifier.py:754-834`) — a 200-with-empty-content is a "success": tenacity predicate returns False at :774-776; no retry, no counter, no provider swap.
3. `agent_node` logs `[LLM] Response: empty` (info, consumed by nothing) and appends the empty AIMessage verbatim (`daemon/graph.py:5353-5416`).
4. Router `should_continue`: empty + no recent ToolMessage → END = silent success (`graph.py:2559`); reasoning-only (:2516-2522) and `<think>`-tag-only (:2539-2544) empties re-invoke unbounded. The only existing handler is a single nudge per tool boundary (:2553-2557; human-boundary reset :2584-2585) — sequence is empty → nudge → empty → END.
5. No consecutive-empty streak counter exists anywhere in `daemon/`; failover counters reset per retry-cycle (:767-772); no persistent provider-health state.
6. The empty-content raise WAS prescribed in `.agents/shared/planning/llm-retry-hardening/phase1-plan.md:90-98` but silently dropped — no ADR.

## 3. Facade Coverage Fact (the decisive discovery) + verification

`ChatFailoverBinding` (`daemon/services/llm_failover.py:490`) builds `classify_llm_errors`; every `wrap_langchain_failover` (`llm_failover.py:617`) site calls `invoke()` → `_run_with_classification` → **`validate_llm_response(result)` at `daemon/llm_error_classifier.py:911`, INSIDE the retry try-block** (docstring :893 states this explicitly).

**Consequence:** one edit at the validator covers the primary agent path AND 5 of the 8 secondary surface classes for free (5 wrapped + 3 uncovered = 8; corrected arithmetic, 2026-09-12 review W2 — the earlier "6 of 7" admits no valid partition):

| Surface | Wrap site (verified) | Post-exhaustion behavior (existing) |
|---|---|---|
| Compaction (×2 call sites) | `daemon/compaction.py:3383`, `:3395` | except-handler → truncation fallback daemon/compaction.py:2644-2659 (call site `:2650`; `_truncate_fallback` def `:3686`) |
| Title generation | `daemon/services/title_generation.py:114` | skips store |
| Keyword extraction | `daemon/services/keyword_extraction.py:387` | heuristic fallback |
| Child-report summarization (×2) | `daemon/services/child_reports.py:803`, `:1485` | existing except-path |
| Attestation judge | `daemon/services/attestation_report_judge.py:485` | `is_complete_report=False`, never-raises contract (:522-528) |

**NOT covered by construction:** skill embedding (raw `openai` SDK — `skill_embedding_service.py:62 import openai`; call sites ~:153/:366/:481, has its own fallback), `WatchoverEvaluator` (graph.py:6244/:6594-6597 — fail-closed deny semantics by design, should stay untouched), `LoopRepairer` (~graph.py:1632/:1640 — static-truncation fallback).

> ⚠️ **Doc correction required in the same change:** `docs/hallucination-protection.md` §7's claim that the secondary surfaces sit outside the classifier path is **wrong for 5 of the 8 secondary surface classes** (5 wrapped + 3 uncovered = 8). Correct it when closing the §7 gap.

### 3.1 Architect spot-verification (this doc's anchors)

- `daemon/llm_error_classifier.py:911` — `validate_llm_response(result)` inside retry scope; `:893` docstring confirms. ✔
- `wrap_langchain_failover` sites: compaction :3352/:3364, title :114, keyword :387, child_reports :803/:1485, attestation :485. ✔
- `skill_embedding_service.py:62` raw `import openai` (no facade). ✔
- `_is_empty_content` — def `graph.py:2562`, exactly ONE prod caller `graph.py:2555` (nudge gate). ✔
- `tests/unit/test_nudge_behavior.py:53` — `assert _is_empty_content([]) is False` (also `:54` `{}`, `:55` `123`). ✔

## 4. Design Contract — When Is Empty Acceptable?

**Reconciled empty contract (both councilors, verbatim-agreed):**

> *Empty-as-final-answer-to-user is NEVER acceptable when it is the turn's entire visible output; empty-as-final-message is acceptable iff the assistant already "spoke" earlier in the same turn.*

The dropped check's rationale ("empty = model done speaking") was correct for case 2, wrong as a blanket exemption for case 1.

**Retroactive ADR (record with the fix):** the prescribed-but-dropped check (phase1-plan.md:90-98) carried **three latent defects** that plausibly explain its silent drop (stated as hypothesis — chronology-inferred, not recorded):
1. raises on reasoning-only responses (pre-router, no `reasoning_content` awareness) → would burn retry budget + failover on *designed* reasoning sequences;
2. `.strip()` on list-block (multimodal) content → non-retryable `AttributeError`;
3. no `tool_calls` nuance → tool-only turns misjudged.

## 5. Design Options

### Option 1 — Validator-Chokepoint Guard (turn-aware S1)

| Dimension | Spec |
|---|---|
| **1. Detection** | S1 only: typed `EmptyLLMResponseError` (**subclass of `LLMResponseValidationError`** so the agent_node catch-tuple graph.py:5344, TRANSIENT membership :459, and error-lane mapping apply with zero seam edits) raised at `validate_llm_response` (called at llm_error_classifier.py:911). Gate = shared-predicate-empty ∧ no `tool_calls` ∧ no `reasoning_content` (available via `additional_kwargs`, graph.py:2064-2070) ∧ no prior-assistant-spoke since the last real human boundary (skip `injected_message=True`/`context_kind` humans). Input messages are in scope at :907 — **1-line signature change, no new state, no router change.** |
| **2. Failure semantics** | Empty-as-entire-answer → transient raise → existing tenacity budget (10) → failover swap (`PRIMARY_TRANSIENT_MAX=3`) → exhaustion → loud ERROR (SSE `stream_error` → job retry/dead-letter → `_send_error_report` manager.py:8469-8499 → `RECOVERY_GUIDANCE_HINT` :873; message lane → `validation_error` message_processing_errors.py:131-133 → instance ERROR). Legitimate empties never raise. |
| **3. New state** | **None.** Turn-position derived from in-scope input messages; retry/failover counters are existing per-cycle RAM. Nothing checkpointed → mid-superstep return-carried constraint untouched; SQLite/PG dual-compat trivial. |
| **4. False positives** | Reasoning-only / think-tag / ghost-promise (`:`) exempted by construction; tool-only turns exempt; done-speaking suppressed by spoke-rule; watchover/question-pause/language-check structurally unaffected. **Residual: the reasoning-only/think-tag re-invoke branches (router rows 2-4) remain unbounded** — this option alone does NOT add S5. |
| **5. Scope** | Agent path + 6 facade secondary surfaces in ONE edit (§3 table). Uncovered: 3 raw modules (by design). |
| **6. Knobs** | `ENSEMBLE_EMPTY_RESPONSE_GUARD` — default ON, kill-switch, restart-pending. |
| **7. Tests** | Predicate truth table; classifier transient-membership + swap-at-3; integration: continuous-empty → bounded calls → ERROR with full lineage; done-speaking → nudge checkpoint sequence unchanged; title-gen empty → retry-then-fallback. Run via `uv run python -m pytest` from worktree root. |

### Option 2 — Router/Streak-Centric (S2+S3+S5, graph-local)

| Dimension | Spec |
|---|---|
| **1. Detection** | Manager-scoped `_empty_response_streaks` (mirroring `_agent_tool_revive_counts` manager.py:768, same 3 cleanup sites) bumped in `agent_node` post-invoke (:5353-5363); S2 turns the router row-6 END (graph.py:2559) into streak-escalation; S5 caps rows 2-4 via derived trailing-degenerate count in `state.messages`. |
| **2. Failure semantics** | First empty still ENDs silently (streak=1); ERROR at K consecutive empty-terminated turns (K≈2-3); degenerate re-invokes capped at 3 → loud END. **No within-turn retry, no provider swap** — the LLM is never re-called for raw empties. |
| **3. New state** | Manager RAM only (restart-resets, like revive-counts). Never touches graph state → return-carried constraint moot. |
| **4. False positives** | Position-1-only counting preserves flows L1-L4; **hazard:** streak across user-abandoned/retried empty turns needs decay/window tuning (new tuning surface); nudge humans must be excluded from boundary detection. |
| **5. Scope** | **Primary path only — structurally blind to ALL secondary surfaces** (they never enter agent_node/should_continue). Both councilors independently judged this the option's fatal gap. |
| **6. Knobs** | `ENSEMBLE_EMPTY_STREAK_GUARD` default ON; `limits.empty_streak_threshold`; restart-read. |
| **7. Tests** | Counter unit + escalation integration + cleanup/restart-reset; router cap tests. RAM-only → no dual-DB surface. |

### Option 3 — Provider-Health Circuit (S4-centric)

| Dimension | Spec |
|---|---|
| **1. Detection** | Manager-owned registry keyed by `base_url`; every empty noted at the chokepoint (no raise); circuit opens at k empties within w seconds (e.g. 3/60s); while open, empties raise → immediate failover. |
| **2. Failure semantics** | One-off glitches pass silently; *sustained* provider failure fails over / fails loudly on every surface. Half-open trial on first healthy backup response. |
| **3. New state** | RAM registry (open/closed/half-open per provider), **cross-instance shared**; no checkpoint writes. |
| **4. False positives** | L1-L9 preserved while closed (common case). **Hole:** low-rate intermittent empties never open the circuit → single-turn silent successes persist; cross-instance coupling — one noisy instance opens the circuit for everyone (documented coupling). |
| **5. Scope** | Same 6+1 surfaces as Option 1 (noting at the same chokepoint); raw modules still out. |
| **6. Knobs** | `ENSEMBLE_EMPTY_PROVIDER_CIRCUIT` default ON + k/w envs. |
| **7. Tests** | Heaviest: circuit state machine, window math, cross-instance sharing, restart reset — plus Option 1's suite. |

### Option 4 — Layered Minimal (S1 chokepoint + S5 derived caps + telemetry-only streak) — **RECOMMENDED**

| Dimension | Spec |
|---|---|
| **1. Detection** | Option 1's turn-aware raise (3 exemptions + spoke-suppression) **+** S5 caps on router rows 2-4 — counting *trailing degenerate AIMessages already in `state.messages`* (derived; threshold 3, matching LoopDetector). **These caps do not exist today** (grep: zero re-invoke counters; only `GRAPH_RECURSION_LIMIT=100`, constants.py:95) — adopting this option *mandates creating them*. Manager streak demoted to WARN telemetry (`[LLM-EMPTY] provider=… streak=…`), never gating behavior. |
| **2. Failure semantics** | Position-1 empties → retry → failover → loud ERROR (Option 1 ladder). Degenerate reasoning-only/think-tag re-invokes → capped at 3 → loud END + WARN + manager note (kills the ~100-call burn; assert ≤ cap+2 LLM calls vs ~100 today). Done-speaking untouched. |
| **3. New state** | **Zero durable.** Spoke/cap/telemetry all derived from in-scope messages or manager RAM. |
| **4. False positives** | Union of the L1-L13 flow analysis (§6). Residual: the spoke-rule variant choice (§8.1). |
| **5. Scope** | Agent path + 5 wrapped secondary surface classes (one edit; 5 wrapped + 3 uncovered = 8). Known-uncovered by design: skill-embedding raw-SDK (own fallback), WatchoverEvaluator (**fail-closed deny on empties — independent semantics, leave untouched**), LoopRepairer (static-truncation fallback). |
| **6. Knobs** | `ENSEMBLE_EMPTY_RESPONSE_GUARD` (master, default ON, restart-pending); `ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP` (default OFF — preserves compaction's truncation fallback against retry-burn on continuous-empty summaries); `limits.empty_degenerate_reinvoke_cap=3`. Invalid values → `ValueError` at boot per the `_resolve_*` convention. |
| **7. Tests** | Option 1's suite + cap tests (3 trailing reasoning-only → END not re-invoke; think-tag/ghost variants) + burn-count assertion + **same-commit flip of `tests/unit/test_nudge_behavior.py:53`** (pins `_is_empty_content([]) is False`; under the shared predicate `[]` is vacuously empty — flip the pin in the same commit per grep-before-flip convention) + grep-pin test on the single-caller invariant (`_is_empty_content` exactly one prod caller, graph.py:2555). |

## 6. False-Positive Analysis — Legitimate-Empty Inventory (L1-L13)

Union of both councilors' code traces; each row is how the **recommended Option 4** preserves the flow:

| # | Flow | Anchor | Why it survives |
|---|---|---|---|
| L1 | Done-speaking trailing empty (nudge row 5) | graph.py:2555-2557; nudge_node :2590-2610 | Spoke-suppression → empty persists → row-5 nudge fires unchanged |
| L2 | Reasoning-only re-invoke (row 2) | :2516-2522 | `reasoning_content` exemption at S1; S5 cap bounds the loop |
| L3 | `<think>`-tag-only (row 3) | :2539-2544 | Tag chars are non-whitespace → non-empty at S1; row 3 + cap own it |
| L4 | Ghost-promise `:` | :2548-2551 | Truthy content → never empty at S1 |
| L5 | Tool-only turns | router exit :2501-2502 | `tool_calls` exemption |
| L6 | Nudge/context HumanMessages | :2593-2610; context_messages.py:85-110 | Skipped in boundary detection — never mistaken for user boundaries |
| L7 | Question pause / answer gate | child_reports.py:2968-2998 | No empty dependency |
| L8 | Watchover terminate/denial | fail-closed :6156-6158, :6592-6598 | Outside classifier path; own deny semantics |
| L9 | Language-check empty passthrough | graph.py:2648-2798 | Runs post-router; already handles empty |
| L10 | Completion-gate warn-and-proceed | child_reports.py:1811-1815 | ⚠ Open Question 1 — vestigial vs intentional |
| L11 | Compaction truncation fallback | daemon/compaction.py:2644-2659 (call site `:2650`; `_truncate_fallback` def `:3686`) | Preserved via retries→except-handler; `COMPACTION_SKIP` knob as belt-and-braces |
| L12 | Report-excluded / text-only agents | child_reports.py:1314, :1647-1670 | Empty finals become retries/errors — **improvement**, not regression (an empty report is a defect today) |
| L13 | Multimodal list-block | graph.py:2566-2568 | Shared predicate pins it (§11b) — kill-switch-gated behavior change |

## 7. 5-Axis Trade-Off Matrix

| Axis | 1 Chokepoint | 2 Router/Streak | 3 Circuit | 4 Layered Minimal |
|---|---|---|---|---|
| **Complexity** | Low — one seam, one predicate | Medium — manager state + router edits + decay tuning | High — new subsystem, window math, cross-instance coupling | Low-Med — Option 1 + derived count |
| **Scalability** | High — bounded budgets, no shared state | Med — per-instance RAM; no cross-instance signal | Highest provider-keyed coverage; shared-circuit contention | High — same as 1 |
| **Maintainability** | High — single predicate shared with router | Med — awareness duplicated at agent_node | Med-Low — second health system beside HA counters | High — one predicate, one derived count, no state machine |
| **Risk** | Low-Med — FP concentrated in predicate (truth-table-mitigated); rows 2-4 still unbounded | Med — first-empty silent; manual-retry streak FP; secondaries blind | Med — closed-circuit silent-success persists; open-circuit coupling surprises | **Low** — smallest behavioral delta per protected surface |
| **Cost** | Lowest (~1 call-site + tests) | Low code, recurring incident cost on secondaries | Highest (subsystem + tuning) | Low — marginally above Option 1 |

## 8. Recommendation — Option 4, "Layered Minimal"

Both councilors' final recommendations converged on this architecture (labels differ; identical design). Rationale:

1. **Coverage in one edit.** The facade chain (llm_failover.py:617 → llm_error_classifier.py:911) makes the chokepoint the only single-edit point covering the agent path + 5 wrapped secondary surface classes (of 8 total; 3 uncovered by design).
2. **Zero new durable state.** Return-carried constraint and dual-DB compatibility are neutralized by construction — nothing to checkpoint, nothing to migrate.
3. **The exemption set is load-bearing, not cosmetic.** Bare S1 is a *confirmed regression* (reasoning-only storm: validator fires pre-router with zero `reasoning_content` awareness → burns transient 10 + failover swap 3 on designed reasoning sequences → ERROR). The 3-exemption + turn-aware gate is what makes S1 safe. Predicate correctness, not placement, is the hard part — which is precisely why the original check was droppable-defective.
4. **The terminal path is already loud** (agent_node catch :5343-5348 → re-raise → SSE → worker_pool retry/dead-letter :630-640 → `_send_error_report` manager.py:8469-8499 → guidance hint :873). Phase 1 adds no failure machinery beyond the predicate and caps.
5. **S5 is required and currently nonexistent** — without it, rows 2-4 still burn ~100 calls; the derived trailing-count design adds it without new state.

### 8.1 Residual disagreement + synthesis resolution (implementer must verify)

The spoke-suppression rule variant:
- `coding`: prior assistant "spoke" = `tool_calls` ∨ `reasoning_content` ∨ non-whitespace content — preserves nudge in **all** row-5 shapes, but leaves `empty → nudge → empty → END` silent when the turn has no prior content.
- `agentic`: content-only — that sub-case shifts nudge→retry (louder), but changes nudge semantics for it.

**Adopted synthesis:** `coding`'s spoke-rule **PLUS a once-per-window nudge allowance — the second empty after a nudge raises.** Nudge presence is inspectable in the in-scope message list; the one-nudge-per-tool-boundary limit (:2553-2557) is verified. This preserves the nudge as cheap rung 1 and guarantees a loud terminal. *The implementer must verify nudge-message detectability at the validator window (this exact combination is a synthesis-level refinement).*

## 9. Phased Rollout

**Phase 1 (this branch):**
1. `EmptyLLMResponseError` ⊂ `LLMResponseValidationError`, raised at the validator (llm_error_classifier.py:911 call-site) — gate: shared-predicate-empty ∧ no tool_calls ∧ no reasoning_content ∧ spoke-suppression (+ §8.1 nudge allowance).
2. Shared multimodal-safe emptiness predicate, swapped into BOTH the validator and `_is_empty_content` — **with same-commit flip of `tests/unit/test_nudge_behavior.py:53`** + grep-pin single-caller test.
3. S5 derived caps (3) on router rows 2-4.
4. WARN-streak telemetry (non-gating): `[LLM-EMPTY] provider=… streak=…`.
5. Knobs: `ENSEMBLE_EMPTY_RESPONSE_GUARD` (ON) + `ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP` (OFF) + `limits.empty_degenerate_reinvoke_cap=3`.
6. Test suite (§10).
7. Docs: close hallucination-protection.md §7 gap; **correct its §7 secondary-surface claim** (wrong for 6/7); retroactive ADR recording the dropped check's three latent defects (as hypothesis).

**Deferred (Phase 2):**
- Exhaustion-signal promotion: `validation_error` → `max_retries_exceeded` in `CRITICAL_ERROR_TYPES` (today's severity=warning loses the "burned 10 retries" signal).
- Provider-health circuit (Option 3 core) — only if telemetry shows sustained storms.
- Raw-SDK mirrors: skill-embedding ×2; LoopRepairer; WatchoverEvaluator likely stays fail-closed by design.
- Manager streak → persistent observability metric (RAM-vs-instance-row decision).
- LoopDetector no-progress signature; `agents/*/meta.json` scan for designed-empty finals; `llm_load_balancer` empty-signal unification; compaction partial-summary preference hierarchy.

## 10. Test Strategy (all via `uv run python -m pytest` from worktree root)

- **Unit:** predicate truth table (None/""/ws str/think-tag/list-with-text-blocks/list-with-image-block/{}); classifier transient-membership + failover swap-at-3; cap counting on trailing degenerate messages.
- **Integration:** continuous-empty provider → bounded LLM calls → instance ERROR with full lineage (SSE + dead-letter + guidance hint); done-speaking → nudge checkpoint sequence **unchanged**; reasoning-only ×3 → END not re-invoke (burn assertion ≤ cap+2 calls vs ~100 today); title-gen empty → retry-then-skip-store; keyword empty → retry-then-heuristic; compaction empty → retry-then-truncation-fallback (and with `COMPACTION_SKIP=ON` → immediate fallback).
- **Pins:** same-commit flip of `test_nudge_behavior.py:53` (`[]` becomes vacuously empty under the shared predicate); grep-pin single-caller test for `_is_empty_content`; kill-switch OFF → byte-identical legacy behavior pinned.
- **State:** zero new durable state → no SQLite/PG migration surface; dual-DB trivially satisfied.

## 11. Edge Answers

**(a) Streaming vs non-streaming at S1 — no shape change.** Streaming defaults ON (config.yaml:46; `default_streaming` graph.py:1986; injected by `clean_llm_config` :2332-2368) and `invoke()` aggregates streamed chunks into one final AIMessage — "callers see identical final results"; `validate_llm_response` runs after `invoke()` returns. Pin **both `None` and `""` → empty** (non-streaming may yield `None` where streaming yields `""`). No streaming-specific code paths. *(Verified at in-repo-comment level; implementer should confirm installed `langchain-openai` aggregation behavior.)*

**(b) Multimodal list-block — yes, pin it; it's worse than "unpinned."** Two live defects today: the router's `_is_empty_content` returns False for ANY list (all-empty-text vision responses → silent success), and the legacy dropped check's `.strip()` would have crashed non-retryably on lists. **Shared predicate:** `None` → empty; `str` → whitespace-only ⇒ empty; `list` → empty iff NO non-text blocks (image/audio/file) present AND every text block whitespace-only; any other shape → fail-open non-empty; think-tag-only strings deliberately NON-empty (row 3 + S5 cap own that class). Kill-switch-gated (changes row-5 nudge eligibility for all-empty-text lists). *(2026-09-12 review S1 follow-up: a MALFORMED text block — `text` payload not a string, e.g. `[{"type": "text", "text": None}]` — also fails OPEN non-empty; `[None]` fails open via the non-dict rule. `{"type": "text", "text": ""}` stays a legitimately EMPTY well-formed block.)*

**(c) Streak telemetry vs the kill-switch — INTENTIONALLY exempt (W1, 2026-09-12 review; leader decision: KEEP).** The `[LLM-EMPTY]` streak telemetry (`agent_node` post-invoke → `InstanceManager.note_empty_response`, manager.py) is deliberately **NOT** gated by `ENSEMBLE_EMPTY_RESPONSE_GUARD`: with the master kill-switch OFF, passed empties still bump the streak and emit the `[LLM-EMPTY] provider=… streak=…` WARN. Observability is the point — Phase-2 needs the data even during an OFF soak, and an OFF-mode storm is exactly the signal worth seeing. Consequently the kill-switch OFF is **not** byte-identical on the telemetry leg (it remains byte-identical on both ROUTING legs: the S1 raise and the S5 caps). Pinned by `TestEmptyResponseStreakTelemetry.test_kill_switch_off_still_bumps_streak_and_warns`; code comment at the graph.py telemetry block.

## 12. Open Questions

1. **Completion-gate empty contract** — child_reports.py:1811 "warn and proceed": intentional or vestigial? (Determines whether S1 protecting it is a fix or a regression of a designed fallback.)
2. **`llm_load_balancer` empty signal** — unexplored; if present, unify with the shared predicate.
3. **Streaming internals** — confirm installed `langchain-openai` aggregation behavior during implementation.
4. **Designed-empty finals** — no code flow found that intends an empty entire answer; one-time `agents/*/meta.json` registry scan recommended.
5. **Dropped-check motive (F2)** — the three-defect explanation is chronology-inferred, not recorded; ADR states it as hypothesis.

## 13. Provisional Hypothesis Verdict (architect's pre-council hypothesis)

**Partially upheld, materially amended.** S1 confirmed as the core — but *only* exemption-gated and turn-aware (bare S1 = confirmed regression), and stronger than hypothesized (one edit covers 6/7 secondaries; the "secondary hole" is narrower than the evidence doc claims). S5 confirmed necessary — and must be **created**, not tuned. The manager-scoped streak is **demoted** from routing-critical to Phase-2 telemetry. Router-only (S2/S3/S5) confirmed structurally blind to secondaries.

## 14. Council Provenance & Dispute Resolution

2-model council (`worker@agentic`, `worker@coding`), 2 rounds. Round-1 resolutions (both councilors traced code):
- **Secondary coverage:** facade covers 6/7 (agentic correct; coding self-reversed).
- **Bare-S1 vs reasoning-only:** bare raise = confirmed regression (validator pre-router, no reasoning awareness).
- **Terminal path:** confirmed loud (SSE → dead-letter → guidance; message lane severity=warning — Phase-2 promotion flagged).
- **Nudge under turn-aware S1:** mechanics agreed; spoke-rule variant resolved by §8.1 synthesis.

## 15. Gaps

**None.** 2/2 councilors completed and converged; all load-bearing anchors spot-verified by the architect (§3.1). Known-uncovered surfaces (skill-embedding, WatchoverEvaluator, LoopRepairer) are design decisions, not analysis gaps (§5 Option 4, dimension 5).
