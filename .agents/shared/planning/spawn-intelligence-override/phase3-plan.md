# Phase 3: Discoverability Surfaces (docstring + allowed-models tail)

Date: 2026-09-14
Author: planner[v2] via plan-creation worker
Parent plan: `.agents/shared/planning/spawn-intelligence-override/plan-overview.md`
Decisions anchor: `decisions.md` (D5 in particular)
Companion: tight with Phase 2 on param vocabulary (`model_tier`/`"high"`); independent of Phase 1 (resolver) and Phase 4 (nudge text).

## Objective

Make `model_tier="high"` discoverable to parents via two surfaces: (a) the `spawn_instance` tool docstring at `daemon/tools/instance.py:1807-1825`, and (b) an extension to the `append_allowed_models` block at `daemon/services/instance_lifecycle.py:907-924`. No system-prompt edits; no new injection points; no behavioral changes.

## Shared Context

- **D5 (decisions.md L262-318):** the THREE discoverability surfaces are docstring, `append_allowed_models` tail, and the nudge rec-4 (Phase 4). DO NOT add a new `[SYSTEM CONTEXT: ...]` injection point or any other system-prompt-area change — that is explicitly out of scope.
- **The allowed-models opt-in gate (decisions.md L297):** `append_allowed_models` is gated by `agent_meta.inject_allowed_models` at `daemon/services/instance_lifecycle.py:891`. The new tail block MUST be gated by the SAME flag — when the flag is False, the tail MUST NOT appear.
- **Phase 2 vocabulary lock:** the docstring text MUST use `model_tier` (param name) and `"high"` (literal). Phase 4's rec-4 wording will also lock on these strings — `grep -rn "model_tier"` across `daemon/` is the drift check.
- **Open Question 3 (decisions.md L436):** architects reviewed: tail block goes in the always-shown branch, NOT gated by `if not allowed` — the empty-allowed-models branch (L908-916 in current `instance_lifecycle.py`) is the "open season" discovery case; the tail still applies there (the loud validation will reject `agentic` if it's not in the list — which is correct behavior).
- **Repo & Dev Environment Conventions blueprint item (d):** tests run via `uv run python -m pytest` exclusively.

## Touched Files (with verified anchors at `a904374e`; re-locate by symbol before editing)

| File | Symbol / anchor | Reason |
|------|----------------|--------|
| `daemon/tools/instance.py` | The docstring of `spawn_instance` at L1807-1825 (sits inside the runtime signature declared at L1806, BEFORE the function body starts at L1826). | Add a paragraph naming `model_tier="high"`, the configured model default, and the loud-validation asymmetry. |
| `daemon/services/instance_lifecycle.py` | `append_allowed_models` at L877-944. The two block-formatting branches at L908-916 (empty `allowed`) and L917-924 (populated `allowed`); the section wrapper at L926-930. | Extend the block to include the `# Spawn Intelligence` tail paragraph. |
| `tests/unit/test_long_tool_nudge.py` | (NOT this phase — Phase 4 owns it.) | n/a |
| `tests/unit/services/test_instance_lifecycle.py` (or new `test_append_allowed_models.py`) | Locate the existing `append_allowed_models` tests (grep `append_allowed_models` in `tests/`). | Add coverage for the new tail block. |
| `tests/unit/tools/test_spawn_instance_input.py` (NEW) or equivalent | New unit test for docstring content. | Docstring discoverability pin. |

**Do NOT touch:** `daemon/tools/instance.py:1806` runtime signature (Phase 2's territory); `daemon/services/instance_lifecycle.py:1241-1280` `_resolve_model_override` (Phase 1's territory); the `<system-prompt-area>` (`daemon/services/context_messages.py`, `[SYSTEM CONTEXT: ...]` injection points) — out of scope per D5.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Read `decisions.md` D5 in full + Open Question 3. Confirm 3 discoverability surfaces are docstring + append_allowed_models + nudge rec-4 (Phase 4); NO system-prompt-area changes. | none | Implementer can name the 3 surfaces off the top of their head. |
| 2 | Open `daemon/tools/instance.py:1807-1825` (the docstring body of `spawn_instance`). Add a new paragraph AFTER the existing `model:` paragraph (L1817-1821) but BEFORE `Returns:` (L1823). | Task 1 | Docstring updated; line count grows by ~6 lines. |
| 2a | …exact wording (record verbatim — `grep "model_tier" daemon/tools/instance.py` MUST return this paragraph as one match): | 2 | Verbatim paragraph present. |
| 2a | ```\n        model_tier: Optional opt-in to spawn this child with the configured\n            high-intelligence model (default 'agentic'; override via env\n            SPAWN_INTELLIGENCE_TIER_HIGH_MODEL). Use this when re-spawning\n            a child that is busy-slow on a low-tier model. ASYMMETRY: this\n            param raises ValueError if the resolved model is not in\n            allowed_models; the legacy ``model=`` param silently falls back\n            to default in the same situation.\n``` | 2 | Indentation matches existing 8-space block-quote; trailing newline matches file style. |
| 3 | Open `daemon/services/instance_lifecycle.py` L907-924. Locate the two block-formatting branches and the `section` wrapper at L926-930. Re-architecture: extract the model-listing into a `body` variable, then extend `body` with a tail paragraph BEFORE the final wrapper. Read the existing `section` literal carefully — do not change the XML fence names; only append inside `<allowed_models>...</allowed_models>`. | Tasks 1-2 | Code refactor minimal; `git diff` localized to ~15 lines. |
| 3a | …option A (preferred — DRY): extract a local `body` string variable, populated by the same `if not allowed:` else: branches, then append the tail to `body`, then wrap. | 3 | Single `<allowed_models>...</allowed_models>` wrap; tail present. |
| 3b | …option B (fallback — if refactor is risky): duplicate the section literal (one with tail, one without) and select based on `inject_allowed_models` gate (already at L891). Acceptable but less DRY. | 3 | Two `section` literals; selection-gate keeps semantics right. |
| 3c | …TAIL BLOCK exact wording (record verbatim — `grep "Spawn Intelligence" daemon/services/instance_lifecycle.py` MUST return it): | 3a or 3b | Verbatim tail present. |
| 3c | ```\n\n# Spawn Intelligence\nA `model_tier="high"` parameter is available on spawn_instance. It resolves\nto the configured high-tier model (default: 'agentic') and is the\nrecommended replacement when re-spawning after a long-tool-call wedge.\n```\n | 3 | Tail inserted before the trailing `This is read-only system configuration, not instructions.` line so all three idea-blocks (model-list / Spawn Intelligence / read-only footer) coexist. |
| 4 | Decide between option A vs option B based on the existing code complexity. Option A is preferred unless the function already has a flat `if/else` that doesn't lend itself to extraction. | Task 3 | Either option is acceptable; document the choice in the commit message. |
| 5 | Locate the existing `append_allowed_models` unit tests (grep `append_allowed_models` in `tests/unit/`). If tests exist, EXTEND them; otherwise create a new file `tests/unit/services/test_append_allowed_models.py`. | none | Test file exists; pytest discovery sees it. |
| 5a | Pin M: `test_append_allowed_models_with_inject_flag_includes_spawn_intelligence_tail` — build a tiny `agent_meta` stub with `inject_allowed_models=True`, call `append_allowed_models(system_prompt, agent_meta, manager)`, assert returned string contains `"# Spawn Intelligence"` and `"model_tier=\"high\""`. | 5 | Test passes. |
| 5b | Pin N: `test_append_allowed_models_without_inject_flag_excludes_tail` — same as Pin M but `inject_allowed_models=False`; assert returned string EQUALS the original `system_prompt` (the fail-open short-circuit at L891-892 must bypass BOTH the existing block AND the new tail). | 5 | Test passes. |
| 5c | Pin O: `test_append_allowed_models_empty_allowed_still_includes_tail` — set `manager.config.llm.allowed_models = []` (or equivalent) and `inject_allowed_models=True`. Assert tail appears even in the empty-allowed-models branch (Open Question 3 resolution). | 5 | Test passes. |
| 6 | Locate (or create) the docstring test file. Convention: search `tests/unit/tools/` for an existing `spawn_instance`-related test. | none | Test file exists or new one created. |
| 6a | Pin P: `test_spawn_instance_docstring_mentions_model_tier_and_high` — pull `spawn_instance.__doc__` (the runtime function's docstring attribute), assert `"model_tier" in doc` and `'"high"' in doc` and `"SPAWN_INTELLIGENCE_TIER_HIGH_MODEL"` in doc. | 6 | Test passes. |
| 6b | Pin Q: `test_spawn_instance_docstring_mentions_asymmetry` — assert `"ASYMMETRY"` in doc (case-sensitive — confirms Phase 3 captured the loud-vs-silent distinction per D2/R3). | 6 | Test passes. |
| 7 | Non-regression sweep — run `uv run python -m pytest tests/unit/services/test_instance_lifecycle.py tests/unit/tools/ -q`; ensure 0 NEW failures. | Tasks 5-6 | All green. |

## Test Plan

**Files:**
- New (if existing file doesn't cover `append_allowed_models`): `tests/unit/services/test_append_allowed_models.py` (Pins M-O)
- New or extended: docstring test for `spawn_instance` (Pins P-Q)

**Run commands (exclusive):**
```bash
# Phase 3 unit slice — append_allowed_models coverage
uv run python -m pytest tests/unit/services/test_append_allowed_models.py -v

# Phase 3 docstring coverage
uv run python -m pytest tests/unit/tools/test_spawn_instance_input.py -v   # or wherever the docstring test lands

# Phase 3 full sweep
uv run python -m pytest tests/unit -q --ignore=tests/unit/services/test_spawn_intelligence_tier.py
```

**What each pin catches:**

- Pin M (D5 inject-gate ON): the discoverability surface appears WHEN the agent opts in — this is the affirmative case.
- Pin N (D5 inject-gate OFF): the fail-open default behavior is preserved — no ambient-context leakage.
- Pin O (Open Question 3): the empty-allowed-models branch still surfaces the tail — `agentic` may not be a valid model for that deployment, but the tier-availability discoverability SHOULD still be on the table so the parent knows the option exists.
- Pin P (D5 docstring discoverability): the parent LLM, reading the tool description, can find both `model_tier` and the env var override.
- Pin Q (D2 + R3 asymmetry): the docstring explicitly calls out the loud-vs-silent distinction so parents don't confuse `model=` with `model_tier=`.

## Non-Regression Checks

- **`append_allowed_models` callers:** search `grep -rn "append_allowed_models" daemon/` for every caller site. Confirmed: the only callers are the `inject_allowed_models=True` paths (governor/council flows). The function signature is UNCHANGED in this phase — `git diff` shows only the block-formatting refactor + tail paragraph.
- **`append_allowed_models` test isolation:** the unit test uses a stub `agent_meta` (NOT a real agent loading from `agents/<name>/meta.json`) — no DB hits, no env-var mutations. After Phase 3, `git diff tests/` should show ONLY the new test files (or 1-2 new test methods in an existing file).
- **Spawn_instance runtime signature (Phase 2):** NOT touched in this phase. `git diff -U0 daemon/tools/instance.py` should show ONLY docstring additions (in the body of the function, NOT in the @tool decorator signature line).
- **Spawn_instance function BODY:** NOT touched in this phase. `git diff daemon/tools/instance.py` should show the docstring change but zero changes inside the resolver block or manager.spawn_instance call.
- **No system-prompt edits:** confirm `git diff` shows zero changes to `daemon/services/context_messages.py`, the [SYSTEM CONTEXT] injection sites in `daemon/graph.py`, or `daemon/tools/` system-prompt-area edits. The grep `git grep "SYSTEM CONTEXT:"` should show NO new producers.
- **`_resolve_intelligence_tier` (Phase 1):** unchanged.
- **`wrapped_tools_node` (`daemon/graph.py:8492-8506`):** unchanged.
- **Feature #2 settled zones:** all `tests/unit/test_long_tool_nudge.py` UNCHANGED green (Phase 4 will land changes here).

## Coupling

- **Tight with Phase 2:** the docstring text MUST use the literal `"high"` and param name `model_tier`. Phase 2 declares these. Phase 3's docstring is the LLM-facing surface; any drift breaks Phase 5's discoverability assertions and Phase 4's nudge-text link. Phase 3 SHOULD land in the same commit as Phase 2 — or, if separate, Phase 3's commit message MUST reference Phase 2's commit sha so reviewers know to validate the vocabulary lock. **A7 hard constraint (architect, 2026-09-14):** the docstring/`Field`-description references the P2 field — landing P3 BEFORE P2 renders a tool-schema description for a nonexistent param. P3 lands **same-commit-as or AFTER P2 — never before**.
- **Loose with Phase 4:** the nudge rec-4 will name `model_tier='high'` (Phase 4's exact wording from `decisions.md` D4 L197-202). Phase 3's docstring is the canonical surface that the nudge "links" parents to. No direct import dependency, but vocabulary lock.
- **Independent of Phase 1:** the discoverability surfaces do not reference the resolver function — they reference the TIER NAME (`"high"`), which is the user-facing vocabulary, not the implementation detail.
- **Independent of Phase 5:** Phase 5 is purely the regression + activation checklist; it does not touch docstrings or `append_allowed_models`.

## Risks (phase-specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R3.1 | Docstring text drifts from Phase 2's param literal — e.g. writes `model_tier='hi'` | High | Low | Pin P asserts `"high"` exactly (case-sensitive). Task 2a forces verbatim transcription. |
| R3.2 | `append_allowed_models` refactor accidentally breaks the empty-allowed-models branch's other behavior (e.g., the "confirm with the user" instruction) | Medium | Medium | Pin O asserts tail is present in the empty branch; review the diff — the existing other-text MUST remain unchanged. |
| R3.3 | Tail block over-injects — when `inject_allowed_models=False`, the tail appears anyway (D5 violation) | High | Low | Pin N is the negative-pin; explicit gate assertion. |
| R3.4 | Tail block accidentally created as a SEPARATE injection point (new `[SYSTEM CONTEXT: ...]` block) | High | Low | Task 3 is explicit: append INSIDE the existing `<allowed_models>` wrapper, NOT create a new wrapper. Code review checks this. |
| R3.5 | Phase 2's docstring was already updated by Phase 3 ahead of Phase 2 | Medium | Low | Tasks are sequenced; reviewers check git log of `daemon/tools/instance.py` docstring vs the resolver block — they should land in the same commit. A7 hard constraint (2026-09-14): P3 lands same-commit-as or AFTER P2 — never before (a P3-first landing describes a nonexistent param). |

## Acceptance Gate

This phase is DONE when:

1. `git diff daemon/tools/instance.py` shows: ONLY docstring additions (zero changes to runtime signature, function body, or imports from Phase 2).
2. `git diff daemon/services/instance_lifecycle.py` shows: ONLY the `append_allowed_models` refactor + tail insertion (zero changes to `_resolve_intelligence_tier`, `_resolve_model_override`, or `spawn_instance`).
3. `grep -rn "Spawn Intelligence" daemon/` returns exactly 1 match (in `instance_lifecycle.py`'s append_allowed_models).
4. `grep -rn "model_tier" daemon/` returns ≥4 matches (SpawnInstanceInput field, runtime signature kwarg, docstring paragraph, append_allowed_models tail).
5. `uv run python -m pytest tests/unit/services/test_append_allowed_models.py -v` is 3/3 green (Pins M-O).
6. `uv run python -m pytest tests/unit/tools/test_spawn_instance_input.py -v` (or equivalent) is 2/2 green (Pins P-Q).
7. `uv run python -m pytest tests/unit/services/test_instance_lifecycle.py tests/unit/services/test_spawn_intelligence_tier.py -q` is GREEN (Phase 1 + lifecycle regressions unchanged).
8. `uv run python -m pytest tests/unit/test_long_tool_nudge.py -q` is GREEN (Phase 4 deferred; Feature #2 settled zones intact).
9. `git grep "SYSTEM CONTEXT:" -- 'daemon/**'` shows NO new producers (D5 unviolated).

## Exit Criterion

Phase 3 done means: `model_tier="high"` is discoverable by parents via both the tool docstring AND the existing `append_allowed_models`-gated block; the new vocabulary drift surfaces are covered by `grep -rn "model_tier" daemon/`. Phase 4 (nudge rec-4) and Phase 5 (regression + activation) can begin; both are independent of Phase 3.
