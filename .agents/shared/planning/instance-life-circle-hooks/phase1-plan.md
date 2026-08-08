# Phase 1: Lifecycle Hook Registry & Dispatcher

## Objective

Create the extensible hook infrastructure module (`daemon/services/lifecycle_hooks.py`) that any future lifecycle event can plug into. Only the `on_complete` event slot is wired; no hook functions are registered in this phase. The dispatcher filters by a `hook_names` list (C1) so an agent configured for hook A does NOT run hook B even if both are registered for the same event.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create `daemon/services/lifecycle_hooks.py` with `_HOOK_REGISTRY: dict[str, dict[str, Callable]]` (module-level dict-of-dicts) | none | Module imports without error; registry is an empty `defaultdict(lambda: {})` |
| 2 | Define `LifecycleHookContext` NamedTuple with fields: `instance_id: str`, `agent_id: str \| None`, `parent_id: str \| None`, `last_content: str`, `outcome: str`, `context_key: str \| None`, `manager: Any` | task 1 | NamedTuple constructs with all fields; has docstring explaining each field |
| 3 | Implement `register_lifecycle_hook(event: str, hook_name: str, fn: Callable)` — adds `fn` to `_HOOK_REGISTRY[event][hook_name]`. Idempotent (re-registering overwrites). The `fn` MUST be an async coroutine function; the docstring mandates this. | task 1 | Calling `register_lifecycle_hook("on_complete", "test_hook", some_async_fn)` makes `_HOOK_REGISTRY["on_complete"]["test_hook"] == some_async_fn` |
| 4 | Implement `async dispatch_lifecycle_hooks(event: str, hook_names: list[str], context: LifecycleHookContext) -> None` (C1 signature) — looks up `_HOOK_REGISTRY.get(event, {})`, then for each `hook_name` in `hook_names` (in the order given) resolves `fn = registry.get(hook_name)`. If a name is not registered, skip it (DEBUG-log "hook not registered, skipping"). For each resolved `fn`, `await fn(context)`, wrapped in: `except asyncio.CancelledError: raise` first (W3 — preserve cancellation propagation); then `except Exception as e: log(severity=WARNING, msg=f"hook {hook_name} failed: {e}")` (NOT `except BaseException`, which would swallow `CancelledError` on Python 3.13+ and break pause-cancel). If `hook_names` is empty, returns immediately (no-op). If a registered hook function is sync (not async), the `await` raises `TypeError` which is caught by the `except Exception` — this is acceptable defensive behavior, but in practice all registered hooks MUST be async. | tasks 1-3 | (a) Dispatching with empty `hook_names` returns without error. (b) Dispatching with 2 hooks where the first raises still calls the second (the second is in a fresh try/except iteration). (c) All non-cancellation exceptions logged not raised. (d) `CancelledError` propagates. (e) **Configured-name filtering (C1):** 2 hooks registered for `on_complete`, `hook_names=["hook_a"]` → only `hook_a` runs, `hook_b` is not invoked. Verified in Phase 5 unit test. |

## Coupling

- **Independent of:** Phase 2 (hook function), Phase 3 (config field)
- **Tight with:** Phase 4 (integration calls `dispatch_lifecycle_hooks`)

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Hook function signature mismatch (some hooks async, some sync) | Medium | Mandate async hook functions (`async def`). Document in `register_lifecycle_hook` docstring that `fn` must be async. The `await` in the dispatcher will surface sync-function misuse as a `TypeError` which is caught by the broad `except Exception` and logged. |
| Registry grows unbounded if hooks are registered per-instance | Low | Registry is keyed by event+hook_name (string), not per-instance. Re-registering same name overwrites. Document this is a code-level (module-import-time) registry. |
| `except BaseException` swallows `CancelledError`, breaking pause-cancel (W3) | High | Mandate `except Exception:` everywhere in the dispatcher and downstream hook code. Place `except asyncio.CancelledError: raise` BEFORE the broad `except Exception` whenever cancellation must propagate. The codebase has a known bug class on this — see the C2 DB Torn State Fix critical note. |

## Exit Criterion

`daemon/services/lifecycle_hooks.py` exists, imports cleanly, `dispatch_lifecycle_hooks("on_complete", [], ctx)` with no registered hooks is a no-op, `dispatch_lifecycle_hooks("on_complete", ["only_a"], ctx)` runs only `only_a` (skipping other registered hooks for the event — C1), and dispatching with multiple registered hooks calls each in the order of `hook_names` (failures swallowed except `CancelledError` which propagates — W3). Phase 2 can register the first hook function into this registry.
