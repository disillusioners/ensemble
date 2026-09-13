# Phase 2: Tool Surface + Validation

Date: 2026-09-14
Author: planner[v2] via plan-creation worker
Parent plan: `.agents/shared/planning/spawn-intelligence-override/plan-overview.md`
Decisions anchor: `decisions.md` (D1, D2, D3, D5, D7 in particular)
Companion: depends on Phase 1 (resolver). Phase 3 lands docstring concurrently. Phase 5 reads this signature.

## Objective

Add the `model_tier: Literal["high"] | None` parameter to `spawn_instance` (Pydantic `SpawnInstanceInput`, runtime tool signature, async dispatch). Thread the resolved value through to `manager.spawn_instance(model=...)` as the existing priority-1 override. Raise `ValueError` (loud validation) when the resolved model is not in `allowed_models`. The legacy `model=` silent-fallback path is UNTOUCHED.

## Shared Context

- **D1 (decisions.md L24-78):** new `model_tier: Literal["high"]` — global tier→model map. Vocabulary lock: param name = `model_tier`, literal value = `"high"` (Phase 3 docstring + Phase 4 nudge rec-4 lock onto this).
- **D2 (decisions.md L82-124):** LOUD `ValueError`, mirroring `spawn_councilor`'s strict-validation pattern at `daemon/tools/instance.py:2046-2068`. The error message is the ADOPTED §2.1 VERBATIM text (architecture-recommendation.md; folded into task 4b.i by A2, 2026-09-14): valid-models list + tier→model resolution result + env-var operator hint + a 3-remedy cluster incl. the legacy `model=` retry — with NO canonicalization claim in the message (W7 normalization is a code step, `instance.py:2075-2077`, not message content).
- **D3 (decisions.md L127-167):** `spawn_instance` ONLY. `spawn_councilor`, `terminate_instance`, and other instance-category tools MUST NOT have `model_tier` added in this phase. Negative tests pin the surface boundary.
- **D5 (decisions.md L262-318):** param docstring updated to mention `model_tier` and the high-tier default. Phase 3 documents the exact wording.
- **D7 (decisions.md L376-382):** no new InstanceManager kwarg. The resolver output threads via the existing `model=` kwarg at `daemon/services/instance_lifecycle.py:1325`. After resolution, the tool layer calls the EXACT SAME `manager.spawn_instance(...)` call site used today.
- **D6 (decisions.md L321-371):** no-`model_tier` path continues to use the weighted pool. The `model=` legacy path continues to silent-fallback. Both behaviors MUST have explicit regression assertions.
- **The W7 canonical-name normalization at `daemon/tools/instance.py:2070-2078`:** applies to the new tier path too — `model_tier="high"` resolving to `agentic` must match `allowed_models` case-insensitively, returning the canonical spelling (not the env-var case).

## Touched Files (with verified anchors at `a904374e`; re-locate by symbol before editing)

| File | Symbol / anchor | Reason |
|------|----------------|--------|
| `daemon/tools/instance.py` | `SpawnInstanceInput` definition at L1728-… (extends to roughly L1770 currently). Runtime signature at L1806. `spawn_councilor` validation pattern at L2046-2068 to mirror. | The new param + validation. Anchors may drift — locate by symbol first. |
| `daemon/tools/instance.py` | The `manager.spawn_instance(...)` call site for `spawn_instance`. After `@register_tool_category` at L1804, inside the new tool body (post-auth-gate, pre-DB-write). | Thread the resolved model into the existing call. |
| `tests/integration/test_spawn_intelligence_tier.py` | NEW file. Place under `tests/integration/services/` or `tests/integration/` — check `ls tests/integration/` first; use whichever matches the existing layout for spawn_instance integration tests. | Real-dispatch pin: this is the critical test that proves the loud-raise + legacy-silent both still work. |

**Do NOT touch:** `daemon/tools/instance.py` `spawn_councilor` body (D3 — out of scope), the `register_tool_category("instance")` site list (only `spawn_instance` grows), `daemon/manager.py` (D7 — no facade change), `daemon/services/instance_lifecycle.py` (Phase 1's territory — Phase 1's function is imported, not re-defined here).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Read `decisions.md` D1/D2/D3/D7 + the existing `SpawnInstanceInput` body at `daemon/tools/instance.py:1728-…` and the `spawn_councilor` validation template at L2046-2068. Confirm 4 lines of understanding before touching code: (a) the new field is a Pydantic `Literal["high"] | None = None`; (b) the loud-raise sits BEFORE the spawn call; (c) the resolved model threads via the existing `model=` kwarg on `manager.spawn_instance`; (d) the legacy `model=` path is a separate code branch and is untouched. | none | Implementer can draw the new field's definition, the validation block, and the call-thread on paper before coding. |
| 2 | Open `daemon/tools/instance.py`. Locate `class SpawnInstanceInput(BaseModel)`. Add `model_tier` field AFTER `model` field (keep field order stable so docstring reordering is downstream-only): | Task 1 | Field exists; Pydantic accepts/rejects work; ratchet-style: missing tests fail first. |
| 2a | …add `model_tier: Annotated[Literal["high"] | None, Field(default=None, description=("Optional opt-in to spawn the child with the configured high-intelligence model. Resolves to the deployment's high-tier model (default 'agentic'; override via SPAWN_INTELLIGENCE_TIER_HIGH_MODEL). If the resolved model is not in config.llm.allowed_models, spawn_instance raises ValueError. If None, the spawn proceeds via today's weighted-pool default (or the model= legacy override). Use this when re-spawning a child that is busy-slow on a low-tier model."))] = None`. | 2 | Field defined; import of `Literal` is already present in this file (grep `from typing import` or `from typing_extensions`). If NOT present, add it — minimum-blast-radius import. |
| 2b | …confirm `model` field on `SpawnInstanceInput` is unchanged. | 2 | `git diff -U0 daemon/tools/instance.py` shows `model` field line unchanged. |
| 3 | Locate the runtime tool signature at L1806. Add `model_tier` as the LAST kwarg (order matters for the docstring legend Phase 3 will add). | Task 2 | Runtime signature accepts `model_tier="high"` without TypeError; rejects `model_tier="low"` via the LangChain `args_schema` validator at call time. |
| 3a | …update the L1806 signature: `..., model: Annotated[...] = None, model_tier: Annotated[Literal["high"] | None, Field(default=None, description=("Opt-in: spawn child with high-intelligence model (see SpawnInstanceInput.model_tier for full semantics). When set, raises ValueError if the resolved model is not in allowed_models. Use ONLY when you specifically want the configured high-tier model."))] = None) -> str:` | 3 | Compile + ruff clean. |
| 4 | Locate the body of `spawn_instance` (the function declared at L1804-…). The auth gate starts at L1826; the actual `manager.spawn_instance(...)` call happens AFTER the `_resolve_default_version_tag` await at L1882-1884 + the project_id inheritance block ending around L1870. Between those two (after the auth gate, after version resolution, BEFORE the `manager.spawn_instance` call), add the resolver block: | Tasks 1-3 | New block present; `git diff daemon/tools/instance.py` shows only the planned additions. |
| 4a | …import the resolver: at top of `daemon/tools/instance.py`, add `from .services.instance_lifecycle import _resolve_intelligence_tier` (matching the existing import style — check `from ..services.` prefix; mirror the existing relative-import path used for sibling functions). | 4 | Import resolves; no circular-import error. |
| 4b | …add the resolution block (skeleton — verbatim wording in 4c): `allowed_models = tuple(getattr(manager.config.llm, "allowed_models", None) or ())`. Then `if model_tier is not None:`. Two sub-branches: | 4a | Block compiles. |
| 4b.i | …branch "WARN from resolver": `resolved, err = _resolve_intelligence_tier(model_tier, allowed_models=allowed_models)`; if `err` starts with `"ERROR:"`, `raise ValueError(err[len("ERROR:"):].strip() or "unknown tier")`. If `err` starts with `"WARN:"`, raise `ValueError` with the ADOPTED VERBATIM message (A2, 2026-09-14 — architecture-recommendation.md §2.1, reproduced byte-exact in the block below the Tasks table; mirrors `spawn_councilor`'s raise at `daemon/tools/instance.py:2064-2067` "list valid models + No-fallback" style). | 4b | Loud raise works; message matches the §2.1 verbatim text byte-for-byte (modulo interpolation). |
| 4b.ii | …branch "W7 canonical-name normalization": `canonical_resolved = next((m for m in allowed_models if m.lower() == resolved.lower()), resolved)`; assign `effective_model = canonical_resolved`. | 4b | After this branch, `effective_model` is canonical-spelling. |
| 4b.iii | …branch "no tier requested": if `model_tier is None`, leave `effective_model = model` (the legacy path's value). | 4b | `effective_model` defaults to whatever the legacy `model` arg provides. |
| 4c | Update the `manager.spawn_instance(...)` call site: change `model=model` to `model=effective_model` (no other kwarg change). DO NOT change the kwarg list (D7 — no new manager kwarg). | 4b.iii | Existing call site unchanged in shape; only the value passed to `model=` differs. |
| 4d | …BOTH-PARAMS PRECEDENCE branch (A5; owner-ratified R-A5 2026-09-14 — tier-wins + visible `[NOTE]`, supersedes architecture-recommendation.md §9; explicitly NOT a strict-ValueError): inside the `if model_tier is not None:` block from 4b, add: if `model` is ALSO not None, `model_tier` WINS and `model=` is superseded — `effective_model` stays the tier-resolved canonical model (4b.ii), and the tool result MUST carry a visible supersede notice: `[NOTE] model='<model>' superseded by model_tier='high' (using <tier-mapped model>)`. House precedent: `caller_model_overrides` (`daemon/tools/knowledge_tools.py:722-788`) — explicit override wins, never silently, never rejected. | 4b.ii | Branch present; a both-params spawn proceeds on the tier-resolved model with the `[NOTE]` in the tool result; NO ValueError on mere conflict (D12). |
| 5 | In the auth-gate region BEFORE the resolution block, ADD a single-line pass-through comment noting that `model_tier` is a tier-capability expression, NOT a model-name expression, so the loud-raise is the right semantics (one line; this is for code reviewers and is the only documentation of the asymmetry besides the docstring). | Tasks 4a-4c | Comment present. |
| 6 | Run `uv run python -m pytest tests/unit/test_long_tool_nudge.py tests/unit/services/test_instance_lifecycle.py tests/unit/services/test_spawn_intelligence_tier.py -q` and confirm GREEN (Phase 1's tests + Feature #2 settled tests). | Tasks 4-5 | All green. |
| 7 | Create new test file `tests/integration/test_spawn_intelligence_tier.py` (or wherever the spawn_instance integration tests live — `ls tests/integration/`). Use the SAME fixtures the existing `test_spawn_instance_*` tests use; do NOT invent new DB fixtures. | none (parallel to 4c) | Test file exists; pytest discovery finds it. |
| 7a | Pin G: real-dispatch SUCCESS case — call `await manager.spawn_instance(agent_id="coder", parent_id=<seed>, model_tier="high")` (the manager method, NOT the LangChain tool, to validate the threading path). The second return value (`_returned_model`) MUST equal `"agentic"` (the configured high-tier model); the persisted `instance_metadata["model_override"]` MUST equal `"agentic"`. | 7 | Test passes; persisted read matches. |
| 7b | Pin H: real-dispatch LOUD raise case — monkeypatch `manager.config.llm.allowed_models` to `["coding"]` (excludes `agentic`); call the same path. Assert `ValueError` is raised and message contains `"agentic"` AND `"coding"` (the valid-models list). | 7 | Test passes; ValueError caught; assertion on message wording. |
| 7c | Pin I: real-dispatch legacy `model=` SILENT FALLBACK preserved — call `await manager.spawn_instance(agent_id="coder", parent_id=<seed>, model="gpt-4")` with `agentic` in allowed_models but `gpt-4` excluded. Assert NO `ValueError`, spawn succeeds with the default model (resolved via pool), AND the spawn returns a notice string indicating the override was rejected. (This is the legacy silent path — Phase 2 must NOT regress it.) | 7 | Test passes; confirms D2 asymmetry. |
| 7d | Pin J: real-dispatch DEFAULT-UNCHANGED (D6) — call `await manager.spawn_instance(agent_id="coder", parent_id=<seed>)` (no `model_tier`, no `model`). Assert returned model is one of the weighted-pool candidates. Also monkeypatch `_resolve_intelligence_tier` and assert it was NOT called. | 7 | Test passes; pool non-determinism handled by membership assertion. |
| 7e | Pin K: NEGATIVE surface boundary — assert `spawn_councilor` runtime signature does NOT accept `model_tier`. Inspect the function source via `inspect.signature(spawn_councilor.fn)` (or whatever the LangChain-wrapped accessor is — check Phase 1's note about `register_tool_category` patterns in this file) and assert `model_tier` is NOT a parameter. | 7 | Test passes; out-of-scope surface stays clean. |
| 7f | Pin L: NEGATIVE surface boundary — assert `terminate_instance` does NOT accept `model_tier` (different tool, but mirrors Pin K as a defensive sweep). | 7 | Test passes; defensive breadth check. |
| 7g | Pin X: real-dispatch BOTH-PARAMS precedence (A5 / owner-ratified R-A5, D12) — call `await manager.spawn_instance(agent_id="coder", parent_id=<seed>, model_tier="high", model="coding")` with BOTH passed. Assert ALL THREE: (1) spawn proceeds (NO ValueError) on the TIER-resolved model (`"agentic"`, not `"coding"`); (2) the tool result carries the visible `[NOTE]` supersede line (`model='coding' superseded by model_tier='high' (using agentic)`); (3) persisted `instance_metadata["model_override"]` equals the tier-mapped model (`"agentic"`). | 7 | Test passes; all three assertions green. |
| 8 | Non-regression sweep — `uv run python -m pytest tests/unit tests/integration -q -x --ignore=tests/postgres` and confirm the ONLY new failures (if any) come from Pin K / Pin L if their accessors differ. Update the pin implementation if needed; rerun. | All tasks | No new failures. |

### Task 4b.i — adopted verbatim ValueError message (A2, architecture-recommendation.md §2.1)

Reproduce BYTE-EXACT (modulo `{resolved_model}` / `{allowed}` interpolation):

```python
"spawn_instance(model_tier='high') resolved to model '{resolved_model}' (from env "
"SPAWN_INTELLIGENCE_TIER_HIGH_MODEL), but '{resolved_model}' is NOT in allowed_models: "
"{allowed}. No fallback — add '{resolved_model}' to allowed_models and restart, set "
"SPAWN_INTELLIGENCE_TIER_HIGH_MODEL to one of {allowed}, or retry with "
"model='<one-of-{allowed}>' for the legacy silent-fallback path."
```

Compliance (all 4 required components verified present in §2.1): (1) valid-models list `{allowed}` ✓ (2) tier→model resolution result `{resolved_model}` ✓ (3) env-var name (operator hint) ✓ (4) parent-actionable remedies — three discrete paths incl. the legacy `model=` escape hatch ✓. All remedies in ONE message = the parent self-corrects in one retry without a follow-up question. **Do NOT claim canonicalization in the message** — W7 normalization is a code step (`instance.py:2075-2077`), not message content.

### Pin X — what it catches (A5 / R-A5, D12)

Pin X proves the both-params surface contract end-to-end: `model_tier` wins, `model=` is superseded LOUDLY (visible `[NOTE]`, not silent-ignore, not a strict-ValueError), and the persisted `model_override` records the tier-mapped model — closing the "both-params behavior UNDEFINED" risk (§8 of the recommendation) before implementation.

## Test Plan

**New files:**
- `tests/integration/test_spawn_intelligence_tier.py` (Pins G-L + X)

**Run commands (exclusive):**
```bash
# Phase 2 unit slice
uv run python -m pytest tests/unit/services/test_spawn_intelligence_tier.py tests/unit/test_long_tool_nudge.py -v

# Phase 2 integration slice
uv run python -m pytest tests/integration/test_spawn_intelligence_tier.py -v

# Full sweep (Phase 2 acceptance)
uv run python -m pytest tests/unit tests/integration -q --ignore=tests/postgres
```

**Test isolation requirements:**
- The integration test MUST use a transactional fixture (per Phase 1's note about avoiding conftest edits). Either reuse an existing `session_manager_spawn` fixture (look in `tests/integration/conftest.py`) or build a local fixture in the new test file with a tear-down that drops the test instance.
- The Pin H monkeypatch on `manager.config.llm.allowed_models` must RESTORE the original value at test teardown (use `monkeypatch.setattr` — pytest's monkeypatch fixture, not raw setattr).

**What each pin catches:**

- Pin G (D1 + D2 success): the happy path proves the threading works end-to-end through the manager facade AND the persistence layer.
- Pin H (D2 loud-validation): the operator's loud-fail contract — proves parents DO see the failure.
- Pin I (D2 asymmetry): proves the legacy `model=` silent path was NOT regressed by Phase 2's loud-raise logic.
- Pin J (D6 regression): proves the no-`model_tier` path is undisturbed AND the resolver is NOT called (so the path is purely additive).
- Pin K / Pin L (D3 surface boundary): out-of-scope tools stay clean.
- Pin X (A5 / R-A5 both-params precedence, D12): proves `model_tier` wins with a visible `[NOTE]` supersede and the tier-mapped model persists — the conflict case is loud-but-successful, not silent, not rejected.

## Non-Regression Checks

- **Legacy `_resolve_model_override` (`daemon/services/instance_lifecycle.py:1241-1280`):** Phase 2 does NOT touch this method. Run Pin I's silent-fallback test as the regression.
- **`append_allowed_models` (`daemon/services/instance_lifecycle.py:877-944`):** unchanged.
- **`spawn_councilor` validation (`daemon/tools/instance.py:2046-2068`):** unchanged (we mirror its PATTERN, but its BODY is untouched). The new spawn_instance validation block sits in the spawn_instance body, NOT spawn_councilor's.
- **`spawn_instance` runtime signature should change ONLY by addition of `model_tier`.** `git diff -U0 daemon/tools/instance.py` should show: +1 field on `SpawnInstanceInput`, +1 kwarg on runtime signature, ~30 lines added inside the function body for the resolver block, +1 import line at top. Zero edits to spawn_councilor, terminate_instance, send_message, etc.
- **Existing legacy-fallback test (search for `test_spawn_instance_*` in `tests/`):** must be GREEN unchanged.
- **`_select_weighted_model` (`daemon/services/llm_load_balancer.py:21`):** unchanged.
- **Existing kill-switches (`ENSEMBLE_LONG_TOOL_NUDGE_*`):** unchanged (this phase adds no env var).
- **Facade-Forwarding:** `git diff daemon/manager.py` MUST be empty.

## Coupling

- **Tight with Phase 1:** depends on `_resolve_intelligence_tier` being importable. Phase 1 MUST land first.
- **Tight with Phase 3:** the docstring Phase 3 lands MUST name the literal `"high"` and the param `model_tier` — same vocabulary as Phase 2's field. Land in the same commit, OR Phase 3 must explicitly reference the Phase 2 PR branch + commit sha.
- **Tight with Phase 5:** the Pin G/J integration tests are themselves Phase 5's test gates. Phase 5 will add the activation checklist and the default-unchanged integration test, both of which reuse the same fixture lifecycle as Pin G. Phase 2 may colocate Pin G as a "phase 5-ready" test in the file; Phase 5 then adds the rest.
- **Loose with Phase 4 (nudge text):** only the param-name vocabulary must match. Phase 4 is string-template only and doesn't import from `daemon/tools/instance.py`.

## Risks (phase-specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R2.1 | Pydantic `Literal["high"]` not yet imported in `daemon/tools/instance.py` | Low | Medium | Task 2a checks the import before adding the field; falls back to a typed-string pattern with a runtime `if`-rejection if needed (degraded). |
| R2.2 | The resolution block sits AFTER the auth gate — if a misordered edit puts it BEFORE auth, the loud raise leaks past the membership check | Medium | Low | Task 4 forces re-read of the auth gate; test Pin H + Pin G confirm ordering. |
| R2.3 | Manager signature accidentally grows a new kwarg (Facade-Forwarding violation) | High | Low | `git diff daemon/manager.py` MUST be empty in this phase's diff. Code review explicitly checks this. |
| R2.4 | The W7 canonical-name normalization is forgotten on the tier path, leaving capitalisation drift in `instance_metadata["model_override"]` | Low | Low | Task 4b.ii is explicit; Pin G's assertion `model_override == "agentic"` (lowercase) catches drift. |
| R2.5 | The integration fixture collides with existing `test_spawn_instance_*` and DB state leaks between tests | Medium | Medium | Task 7 forces re-use of an existing fixture (no new conftest); transactional teardown via monkeypatch.setattr. |
| R2.6 | Phase 2 lands docstring changes that conflict with Phase 3's planned docstring update | Medium | Medium | Phase 2 TASK SCOPE = runtime signature + body only; DO NOT edit the docstring body (Lines 1807-1825). Phase 3 owns the docstring. Code review explicitly checks this. |

## Acceptance Gate

This phase is DONE when:

1. `git diff daemon/manager.py` is empty (D7 unviolated).
2. `git diff daemon/services/instance_lifecycle.py` is empty (Phase 1 owns that file).
3. `git diff daemon/services/long_tool_nudge.py` is empty (Phase 4's territory).
4. `git diff -U0 daemon/tools/instance.py` shows only: +1 `Literal` import (if needed), +1 field on `SpawnInstanceInput`, +1 kwarg on runtime signature, ~30 lines added inside `spawn_instance` body, zero edits to `spawn_councilor` or other tool bodies.
5. `uv run python -m pytest tests/integration/test_spawn_intelligence_tier.py -v` is 7/7 green (Pins G-L + X).
6. `uv run python -m pytest tests/unit/test_long_tool_nudge.py -q` is GREEN (Feature #2 settled).
7. `uv run python -m pytest tests/unit/services/test_instance_lifecycle.py -q` is GREEN (legacy silent-fallback regression).
8. `uv run ruff check daemon/tools/instance.py` is clean.
9. Existing legacy `spawn_instance` tests (grep `test_spawn_instance` in `tests/unit` and `tests/integration`) are UNCHANGED-GREEN.

## Exit Criterion

Phase 3 (docstring), Phase 4 (nudge rec-4), and Phase 5 (regression + activation) can begin in any order relative to one another, but Phase 5 depends on Phase 2's integration test file. Phase 3 and Phase 4 lock onto the param vocabulary `model_tier="high"` as the canonical surface.
