# ADR-0001 — The dropped empty-content check: retroactive record (hypothesis)

**Status:** Accepted (retroactive)
**Date:** 2026-09-12
**Branch:** `feature/fix-empty-response-guard`
**Supersedes the silent drop of:** `.agents/shared/planning/llm-retry-hardening/phase1-plan.md:90-98`

## Context

The LLM-retry-hardening Phase 1 plan prescribed an empty-content check in
`validate_llm_response`:

> *"Check for completely empty response (no content AND no tool_calls)"* →
> `raise LLMResponseValidationError("Response is empty (no content and no tool_calls)")`

with acceptance criteria requiring it ("Empty response detection raises validation
error", "empty responses … trigger retry"). The shipped `response_validation.py`
omitted it, its docstring explicitly exempted empty content ("Empty content is
intentionally NOT validated here … it means the model is done speaking"), and no
decision record explains the drop. The exemption's rationale was correct for one
case (done-speaking) and wrong as a blanket rule: a provider returning continuous
empty AI messages completed every turn as a silent empty "success" (the
documented incident in `docs/hallucination-protection.md` §6).

## Decision (what shipped instead — empty-response-guard Phase 1)

The check ships as a typed `EmptyLLMResponseError` (⊂ `LLMResponseValidationError`)
raised at `validate_llm_response` — but **turn-aware and exemption-gated**, not
bare. The gate: shared multimodal-safe emptiness predicate ∧ no `tool_calls` ∧ no
`reasoning_content` ∧ turn-window analysis over the in-scope input messages
(prior assistant spoke since the real human boundary → pass; tool result nearest →
pass, the router nudge owns the first empty; nudge nearest → raise, the §8.1
second-empty allowance). Legitimate empties (L1–L13 table in
`architecture-recommendation.md` §6) never raise.

## The three latent defects of the originally prescribed (dropped) check

Stated as **hypothesis** — chronology-inferred from the shape of the prescribed
check vs. what shipped; no record states why the check was dropped. Each defect
is real *as a property of the prescribed check text*, independent of whether it
was the actual motive:

1. **Reasoning-only false positive.** The prescribed check raises on any response
   with empty content and no tool_calls — including the DESIGNED reasoning-only
   shape (`reasoning_content` set, content empty) that the router deliberately
   re-invokes. Firing pre-router at the validator would have burned the transient
   retry budget (10) and the failover swap budget (3) on designed reasoning
   sequences and then errored the turn — a regression the council confirmed as
   the "bare S1" failure mode. The shipped guard exempts `reasoning_content`
   responses at the validator and bounds that class with the S5 router cap
   instead.
2. **`.strip()` on list-block (multimodal) content.** The prescribed check's
   emptiness test would crash with a non-retryable `AttributeError` on vision
   list-block content (`[{type: image_url, ...}]`), converting a provider quirk
   into a hard turn failure. The shipped shared predicate treats list content
   structurally (empty iff no non-text blocks and every text block
   whitespace-only; unknown shapes fail open).
3. **No tool-calls nuance.** The prescribed check's "no tool_calls" clause as
   written (bare raise, no turn awareness) misjudged tool-only turns and the
   post-tool nudge sequence: the first empty AFTER a tool result is the router
   nudge's designed input, and empty-after-nudge must escalate rather than END
   silently. The shipped guard encodes both (tool-nearest passes; nudge-nearest
   raises).

## Consequences

- Empty-as-entire-answer is now a transient-class validation failure: retry →
  failover → loud terminal ERROR, on the agent path and on all six facade-wrapped
  secondary surfaces (see the corrected §7 of `docs/hallucination-protection.md`).
- The kill-switch `ENSEMBLE_EMPTY_RESPONSE_GUARD` (default ON,
  restart-pending) restores the pre-guard pass-through byte-identically; the
  compaction-only opt-out is `ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP` (default OFF).
- The unbounded re-invoke burn (~100 calls) is separately closed by the S5
  derived caps (`EMPTY_DEGENERATE_REINVOKE_CAP`, default 3).

## References

- Spec: `.agents/shared/planning/empty-response-guard/architecture-recommendation.md`
  (Option 4, §5/§6/§8.1/§9/§11)
- Gap analysis: `docs/hallucination-protection.md` §6 (incident), §7 (corrected
  seam coverage)
- Implementation anchors: `daemon/response_validation.py` (predicate, error,
  guard), `daemon/llm_error_classifier.py` (`input_messages` threading),
  `daemon/graph.py` (S5 caps, nudge marker, streak telemetry),
  `daemon/manager.py` (RAM-only streak), `daemon/config.py` (knob resolvers).
