# Phase 6 — inner_soul ari-only redirect (the leak repair)

**Depends on:** P4 (the redirect target must exist as a real, registered tool). **Flags consumed:** #4 (lead-vs-truth, resolved by re-grep), #13 (loud-notice branch), plus DD-13/DD-16 (factory-captured identity; ari-only blast radius).

## 1. Objective

Repair the root leak: ari's episodic `inner_soul` writes currently redirect to `experience()` — a RAG tool ari does not hold — so the content is silently dropped. Add the ari-only branch at the two redirect call sites so the existing reflex lands on durable ground (`memoir_write`), with non-ari agents byte-identical.

## 2. Verified anchors (re-grepped this session; re-grep again at implementation — P-7)

| Element | Anchor @ `0d2f31dde` (tree == `1f6898ae2`) |
|---|---|
| `_RAG_TARGETS = {"memories","memory"}` | `daemon/tools/inner_soul.py:303` |
| `_KNOWLEDGE_CLASSIFICATIONS` (event/pattern/skill/mistake/project_knowledge) | `:311-313` |
| `_should_redirect_to_rag(...)` (predicate) | `:316-362` — unconditional `project_knowledge` `:352`; all-targets-∈-RAG check `:355`; class check `:359` |
| `_format_rag_redirect(...)` | `:365` |
| `_classify_request(...)` | `:1022` |
| **Call sites (the implementation target)** | `:716` (single-request flow) and `:777` (compound-request flow) — both INSIDE the `create_inner_soul_tool` closure (factory signature `:631-637`: `manager, agent_id, instance_id, version_tag`) where **factory-captured `agent_id` is in scope** |
| NOT the target | `:232-278` is the `project_knowledge` CLASSIFICATION block (targets `["REJECT"]` at `:276`) — flag #4 drift note confirmed; classification is upstream of the predicate and stays untouched |

## 3. Decisions

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|----------------------|
| D6.1 | Branch AT THE CALL SITES (`:716`, `:777`), not inside the predicate: `if _should_redirect_to_rag(...)` and `agent_id == "ari"` → return `_format_memoir_redirect(request, classification, targets)` instead of `_format_rag_redirect(...)`; one shared module-level helper `_format_memoir_redirect(...)`, both call sites edited identically | design §6.2: "same entry point (`_should_redirect_to_rag` region), evaluated with the agent identity already in scope"; the closure's `agent_id` IS the factory-captured identity (DD-13 — never caller-supplied); keeping the predicate pure preserves its unit contract | changing the predicate signature (ripples into its unit tests and muddies "RAG-ness" with agent identity); a new standalone tool (DD-16: "a new tool that must be remembered to be called does not fix a leak") |
| D6.2 | `_format_memoir_redirect` text: guides to `memoir_write` with the content shape (first-person narrative, title/importance/event_at/stars/keepsake options), names the classification, and states the record is durable — the `_format_rag_redirect` counterpart in register | the redirect's job is to convert an existing reflex into the right call with the right shape | silently calling memoir_write from inside inner_soul (the agent should SEE the write happen — transparency + one write path) |
| D6.3 | Kill-switch interaction (flag #13): with the knob OFF the ari branch is STILL TAKEN (never fall back to the RAG void) but returns the loud notice: `memory recording is disabled (ENSEMBLE_MEMOIR_ENABLED) — this content was NOT recorded` | a disabled memory must be VISIBLE, not a return to the silent drop the lane exists to cure | falling through to the RAG redirect when OFF (re-creates the void) |
| D6.4 | Blast radius: non-ari agents byte-identical — the branch condition is `agent_id == "ari"` EXACTLY (case-sensitive, factory value); no other edit to inner_soul.py beyond the two call sites + helper + import | DD-16 ("non-ari agents unchanged. Their redirect to RAG remains exactly as shipped"); this is the ONLY permitted RAG/knowledge-system touch (design §10.3) | redirecting all agents (blast radius past the mandate) |
| D6.5 | Compound flow symmetry: the `:777` arm carries the same branch, with its compound-line formatting (`Part {idx}: … (redirected to memoir)`) | the compound path is the G2-fixed second flow — asymmetry would re-introduce the leak for compound requests | single-flow-only fix (half a repair) |

## 4. Tasks

| ID | Task | Outcome | Touchpoints | Depends | AC |
|----|------|---------|-------------|---------|----|
| T6.1 | Helper + two call-site branches + knob interaction | source diff = exactly: 1 helper, 1 import, 2 branch edits; no other line in inner_soul.py changes | daemon/tools/inner_soul.py | P4 | AC1 |
| T6.2 | Byte-identity leg: for a non-ari fixture agent (developer-class), response bytes for RAG-redirect-triggering requests are IDENTICAL pre/post change | pack proves identity across the classification corpus incl. compound requests | tests/unit/memoir/ | T6.1 | AC2 |
| T6.3 | Ari-path legs: each of the five classifications redirects to memoir guidance; knob OFF → loud notice (exact bytes); knob garbage-value → same OFF behavior (knob module single authority) | fixtures per classification × knob state | tests/unit/memoir/ | T6.1 | AC3 |
| T6.4 | Anchor re-grep record: implementation commit message carries the verified line numbers AT IMPLEMENTATION TIME (they may drift from this plan — P-7) | commit message + phase notes carry values | — | T6.1 | AC4 |

## 5. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Diff census: inner_soul.py delta touches ONLY the helper + import + the two call sites (a source-diff assertion in the pack) |
| AC2 | Non-ari byte-identity across the classification corpus (single + compound) — ZERO diffs |
| AC3 | Ari: 5/5 classifications → memoir guidance; OFF/garbage knob → the loud notice verbatim; no path returns the old RAG redirect for ari |
| AC4 | Implementation-time re-grep values recorded (commit message); any drift is a P-7 note, not a deviation |

## 6. Test packs

- `test/packs/memoir_inner_soul_redirect_test.sh` — AC1–AC3 (byte-identity harness: capture pre-change responses from the base commit fixture, compare post-change).

## 7. Flag consumption

#4 (disposition executed: predicate/call-sites confirmed the target; classification block untouched), #13 (D6.3), DD-13 (D6.1 factory-captured identity), DD-16 (D6.4 blast radius). This phase is the ONLY sanctioned RAG/knowledge-system touch (non-goal guardrail).
