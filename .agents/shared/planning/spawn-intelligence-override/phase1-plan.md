# Phase 1: Config Surface + Resolver

Date: 2026-09-14
Author: planner[v2] via plan-creation worker
Parent plan: `.agents/shared/planning/spawn-intelligence-override/plan-overview.md`
Decisions anchor: `decisions.md` (read ENTIRELY first — D1, D2, D7, D8, D11 in particular)
Companion: Phase 2 consumes this resolver's output; Phase 4 is loosely coupled on the param name only.

## Objective

Add the operator-side configuration surface (`SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` env var, default `"agentic"`) and a free-function resolver (`_resolve_intelligence_tier`) that returns the canonical `(resolved_model, error)` tuple. This phase is purely additive: no tool signature change, no InstanceManager kwarg, no DB schema change.

## Shared Context (what the implementer MUST know)

- **D1 (decisions.md L24-78):** the resolver is GLOBAL — one canonical tier→model map; per-agent overrides are deferred (D11). The env var is `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL`, default `"agentic"` (mirrors `_ALLOWED_MODELS_DEFAULT` first element at `daemon/config.py:2307`).
- **D2 (decisions.md L82-124):** resolver return contract is `tuple[str | None, str | None]` — `(model, error)`:
  - `(str, None)` — resolved model OK (in allowed_models)
  - `(str, "WARN: ...")` — resolved but the target model is NOT in allowed_models (caller surfaces loud-raise)
  - `(None, None)` — tier is None (no override path)
  - `(None, "ERROR: ...")` — unknown tier literal (mirrors `model_validator` rejection shape; not raised here — returned as the error tuple element so the tool surface can `raise ValueError(error)` atomically)
- **D7 (decisions.md L376-382):** NO new InstanceManager kwarg. The resolver output threads via the existing `model=` kwarg at `daemon/services/instance_lifecycle.py:1325`. The Phase 1 deliverable is a pure free-function — no facade mutation.
- **D8 (decisions.md L384-388):** process-lifetime config (restart to activate). Documented in Phase 5 rollout checklist.
- **D11 (decisions.md L405-411):** only `"high"` ships in v1. The function MUST reject unknown tier literals by name (defensive — even though Pydantic Literal gates callers, future internal callers could pass an unvalidated string).
- **Repo & Dev Environment Conventions blueprint item (d):** tests run exclusively via `uv run python -m pytest` from worktree root. Bare pytest is forbidden.
- **Drift correction:** the spec's task brief originally cited `daemon/config.py:2214+` for `_ALLOWED_MODELS_DEFAULT` — corrected to line 2307 (verified at SHA `a904374e`).

## Touched Files (with verified anchors at `a904374e`; re-locate by symbol before editing)

| File | Symbol / anchor | Reason |
|------|----------------|--------|
| `daemon/config.py` | New section immediately after `_ALLOWED_MODELS_DEFAULT` at L2307 (currently ends ~L2313 before `_resolve_allowed_models`). | Home for the env var resolver + tier→model default. Anchors may drift — locate by symbol first. |
| `daemon/services/instance_lifecycle.py` | New free function, place immediately above `class InstanceLifecycleService` (search for that class definition; ~L230-260). Anchor listed: L260 first public name in the class area. | Free function (NOT a method) so it can be imported from the tool layer without instantiating the service. |
| `tests/unit/test_spawn_intelligence_tier.py` | NEW file. Place under `tests/unit/services/test_spawn_intelligence_tier.py` to mirror existing `test_instance_lifecycle.py` layout. | Independent test gate for this phase. |
| `tests/conftest.py` (if needed) | Read first; only touch if a shared fixture is required. | Prefer NOT to modify conftest; build a local fixture in the new test file instead. |

**Do NOT modify:** `daemon/manager.py` (D7 — no facade change), `daemon/tools/instance.py` (Phase 2's territory), `daemon/services/llm_load_balancer.py` (D6 — pool untouched), `agents/*/meta.json` (D11 — no per-agent tier in v1).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Read this file + `decisions.md` lines 1-250 end-to-end. Confirm the resolver contract is `(model: str | None, error: str | None)` and the error type is one of `None`, `"WARN: ..."`, `"ERROR: ..."` (prefix-conventional — Phase 2 grep-checks for these to decide `raise` vs `pass-through`). | none | Implementer can articulate the 4-element return-tuple matrix without referencing notes. |
| 2 | Open `daemon/config.py`, locate `_ALLOWED_MODELS_DEFAULT` (grep `^_ALLOWED_MODELS_DEFAULT`), then locate the existing `OPENAI_*` env family region (`grep -n "OPENAI_ALLOWED_MODELS\\|OPENAI_SELECTABLE_MODELS"` near L385-413). Add a new `spawn_intelligence` mini-section immediately after `_ALLOWED_MODELS_DEFAULT`'s trailing comment block: | Task 1 | New section exists; runs under `uv run python -c "from daemon.config import _SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT; print(_SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT)"` returning `"agentic"`. |
| 2a | …define `_SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT: str = "agentic"` as a module-level constant (mirrors `_ALLOWED_MODELS_DEFAULT` style). | 2 | Constant importable; value == `"agentic"`. |
| 2b | …define `_resolve_intelligence_tier_high_model(env_value: str | None) -> str` (or fold into Pydantic settings via `pydantic_settings` if the project uses it — check `Settings` shape at top of `daemon/config.py`; most envs in this file are simple `_resolve_*` helpers). | 2a | Returns `"agentic"` when `env_value` is `None`, empty, or whitespace; otherwise returns the stripped value. |
| 3 | Open `daemon/services/instance_lifecycle.py`, locate `class InstanceLifecycleService` (grep `^class InstanceLifecycleService`). Add a new FREE function `_resolve_intelligence_tier(tier, allowed_models)` immediately before that class: | Task 1 | Function importable as `from daemon.services.instance_lifecycle import _resolve_intelligence_tier`. |
| 3a | …signature: `def _resolve_intelligence_tier(tier: str | None, *, allowed_models: tuple[str, ...] | list[str] | None) -> tuple[str | None, str | None]`. | 3 | Sig matches Decision D2 matrix verbatim. |
| 3b | …body: returns `(None, None)` when tier is None/empty; returns `(resolved, None)` when tier == `"high"` and resolved is in allowed_models (case-insensitive); returns `(resolved, "WARN: ...")` when tier == `"high"` and resolved is NOT in allowed_models (resolved is still returned so the tool layer can surface the loud-validation message); returns `(None, "ERROR: ...")` for any other string. The exact error-message wording is recorded in test pins in task 5 — match exactly. | 3a | Implementer writes docstring with each branch's intent + a 4-line "Returns" matrix. |
| 3c | …DI drift check: confirm `decisions.md` does NOT require any DB read inside the resolver. The function is pure: caller passes `allowed_models`. The tool layer (Phase 2) is responsible for fetching `manager.config.llm.allowed_models` and threading it in. | 3b | No `self.`, no `self._config.`, no `await` in the function body. |
| 4 | Read `daemon/services/instance_lifecycle.py` imports top-of-file. If `_resolve_intelligence_tier` uses `logger.warning`, the module-level logger is already imported — no import change needed. If you decide to omit logging (clean function), skip the import check. | Task 3 | No untracked import added; ruff clean (`uv run ruff check daemon/services/instance_lifecycle.py`). |
| 5 | Create new test file `tests/unit/services/test_spawn_intelligence_tier.py`. | Tasks 3-4 | File exists; pytest discovery sees it via `uv run python -m pytest --collect-only tests/unit/services/test_spawn_intelligence_tier.py`. |
| 5a | Pin A: `test_resolve_intelligence_tier_high_default_in_allowed` — assert `_resolve_intelligence_tier("high", allowed_models=("agentic", "coding"))` returns `("agentic", None)`. | 5 | Test passes. |
| 5b | Pin B: `test_resolve_intelligence_tier_high_not_in_allowed` — assert `_resolve_intelligence_tier("high", allowed_models=("coding", "coding2"))` returns `("agentic", "WARN: ...agentic is not in allowed_models...")`. | 5 | Test passes; error string begins with `"WARN:"`. |
| 5c | Pin C: `test_resolve_intelligence_tier_none_returns_no_override` — assert `_resolve_intelligence_tier(None, allowed_models=("agentic",))` returns `(None, None)`. | 5 | Test passes. |
| 5d | Pin D: `test_resolve_intelligence_tier_empty_string_treated_as_none` — assert `_resolve_intelligence_tier("", allowed_models=("agentic",))` returns `(None, None)`. (Defensive — Pydantic Literal won't allow empty string in production, but free function must be robust for internal callers.) | 5 | Test passes. |
| 5e | Pin E: `test_resolve_intelligence_tier_unknown_literal_returns_error` — assert `_resolve_intelligence_tier("bogus", allowed_models=("agentic",))` returns `(None, "ERROR: ...unknown tier 'bogus'...")` and that no `ValueError` is raised inside the function itself. | 5 | Test passes; error string begins with `"ERROR:"`. |
| 5f | Pin F: `test_resolve_intelligence_tier_high_case_insensitive_allowed_match` — assert `_resolve_intelligence_tier("HIGH", allowed_models=("Agentic", "coding"))` returns `("agentic", None)` (after case-insensitive resolved-side match — the env default is lowercase; doc this clearly). | 5 | Test passes; implementer documents case-handling in docstring. |
| 6 | Non-regression check — run the entire `tests/unit/` suite via `uv run python -m pytest tests/unit -x -q` and confirm 0 NEW failures. Existing `tests/unit/services/test_instance_lifecycle.py` and `tests/unit/test_long_tool_nudge.py` must be GREEN. | Tasks 2-5 | `git diff` shows ONLY the 3 new file additions + the planned config/lifecycle inserts; `pytest -q` reports no new failures. |

## Test Plan

**File:** `tests/unit/services/test_spawn_intelligence_tier.py` (NEW)

**Run command (exclusive):**
```bash
uv run python -m pytest tests/unit/services/test_spawn_intelligence_tier.py -v
```

**What each pin catches:**

- Pin A (D2 success path): the common case — `model_tier="high"` resolves to `"agentic"` cleanly.
- Pin B (D2 loud-validation contract): the resolver returns the WARN signal so the tool layer can `raise ValueError` later — the loud behavior is decided downstream, not here.
- Pin C (D6 default-unchanged contract): tier absent → no override → existing code paths fire unchanged.
- Pin D (defensive): empty string treated as None; cheap line of defensive code to keep future internal callers safe.
- Pin E (D11 future-proof): unknown tier literal returns an error string, not silently None — Phase 2 will raise `ValueError(error)` on it.
- Pin F (W7 parity): case-insensitive match mirrors `_resolve_model_override` at `daemon/services/instance_lifecycle.py:1267-1272` (canonical-name normalization).

**Additional invariants pinned:**
- The function is pure (no `self.`, no IO) — verified by reading the body during task 3c.
- Module logger is unchanged — verified by `git diff` after implementation (no logger.* lines added in this phase).

## Non-Regression Checks

- **Feature #2 settled zones (long-tool-call-nudge):** no daemon-side change in this phase; nudge notice (`daemon/services/long_tool_nudge.py:796-855`) is Phase 4's territory. Run `uv run python -m pytest tests/unit/test_long_tool_nudge.py -v` — must be GREEN, unchanged.
- **Existing `_resolve_model_override` (`daemon/services/instance_lifecycle.py:1241-1280`):** untouched. Run `uv run python -m pytest tests/unit/services/test_instance_lifecycle.py -v -k "test_resolve_model_override"` — must remain GREEN.
- **`_ALLOWED_MODELS_DEFAULT` (`daemon/config.py:2307`):** value unchanged; only a new section is added BELOW it.
- **`spawn_instance` runtime signature (`daemon/tools/instance.py:1806`):** NOT touched this phase.
- **`InstanceManager.spawn_instance` facade signature:** NOT touched this phase. Confirm with `git diff daemon/manager.py` — empty diff.
- **`_select_weighted_model` (`daemon/services/llm_load_balancer.py:21`):** NOT touched. The pool is unchanged.
- **`wrapped_tools_node` (`daemon/graph.py:8492-8506`):** NOT touched (Feature #2 settled zone).
- **Existing kill-switch family `ENSEMBLE_LONG_TOOL_NUDGE_*`:** NOT touched. No env var is named in that family in this phase.

## Coupling

- **Tight with Phase 2:** the `(model, error)` return shape is the exact contract Phase 2 imports and branches on. Phase 2 MUST follow Phase 1; do not parallelize. If Phase 2's tool-side change lands first, `_resolve_intelligence_tier` will be undefined at import time and `ruff` + the unit suite will fail.
- **Loose with Phase 4:** the resolver does NOT need to exist for the nudge text to compile — Phase 4 is string-template only and tests its own `# FUTURE` replacement independently. But the rec-4 wording (`spawn_instance(..., model_tier="high")`) names the param literal that Phase 1's env var + resolver ultimately resolve. The wording MUST say `model_tier` (not `intelligence_tier` or `high_model`) — Phase 4 word-locks onto Phase 1's chosen vocabulary.
- **Independent of Phase 3** (docstring / tail block — different surfaces).
- **Independent of Phase 5** (regression test — runs against Phase 2's wired `model_tier` param).

## Risks (phase-specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R1.1 | Resolver return-shape drift across Phase 1/2 | High | Low | Phase 1 task 1 forces read of the 4-element matrix; Phase 2's import asserts the same tuple destructuring shape; tests pin each branch. |
| R1.2 | `_SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT` collides with later import via `OPENAI_*` family | Low | Low | `grep -n "SPAWN_INTELLIGENCE_TIER_HIGH"` must show only the new declaration sites; Phase 2 will add its own grep gate. |
| R1.3 | Case-handling divergence with `_resolve_model_override` (W7 parity) | Medium | Low | Pin F explicitly tests `"HIGH"` → `"agentic"`; docstring documents the policy. |
| R1.4 | Drift to module-level import order | Low | Low | `_resolve_intelligence_tier` lives in `instance_lifecycle.py` (the file Phase 2's validator already imports) — no import-order change needed. |
| R1.5 | Tests added to wrong directory (flat `tests/unit/` vs `tests/unit/services/`) | Low | Medium | Folder targeted explicitly: `tests/unit/services/test_spawn_intelligence_tier.py`. Existing sibling files use the same layout. |

## Acceptance Gate

This phase is DONE when ALL of the following are true:

1. `git diff daemon/manager.py` is empty (Facade-Forwarding unviolated, D7).
2. `git diff daemon/tools/instance.py` is empty (Phase 2's territory).
3. `git diff daemon/services/long_tool_nudge.py` is empty (Phase 4's territory).
4. `uv run python -m pytest tests/unit/services/test_spawn_intelligence_tier.py -v` is 6/6 green (Pins A-F).
5. `uv run python -m pytest tests/unit/test_long_tool_nudge.py -q` is unchanged-green (Feature #2 settled).
6. `uv run python -m pytest tests/unit/services/test_instance_lifecycle.py -q` is unchanged-green (legacy `_resolve_model_override` regressions = 0).
7. `uv run ruff check daemon/config.py daemon/services/instance_lifecycle.py` is clean.
8. The resolver is a PURE free function (no `self.`, no IO) — verified by reading the body in code review.

## Exit Criterion

Phase 2 can begin: the function `_resolve_intelligence_tier(tier, *, allowed_models)` is importable from `daemon.services.instance_lifecycle`; its 4-element return tuple is locked by Pin A-F; the operator-side env-var resolver in `daemon/config.py` is ready for the tool layer's `manager.config.llm.allowed_models` lookup.
