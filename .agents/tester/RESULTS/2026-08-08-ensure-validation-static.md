# ensure.md Core Static-Check Validation — 2026-08-08

**Feature:** Instance Lifecycle Hooks
**Blast radius:** `daemon/services/lifecycle_hooks.py` (new), `daemon/services/context_tools.py` (new), `daemon/services/context_injection.py` (new), `daemon/registry.py` (new field), `daemon/services/child_reports.py` (hook dispatch), `daemon/tools/context_tools.py` (new), `daemon/mcp/kb_server.py` (MCP surface), `agents/wanderer/meta.json` (opt-in)
**Scope:** Sub-set of ensure.md **Core** requirements (static analysis only — no pytest, per task).
**Coverage:** Critical (subset), Important, Nice-to-have (subset).

---

## Summary

| # | Priority | Requirement | Result |
|---|----------|-------------|--------|
| 1 | Important | All callers of converted async functions properly await | ✅ PASS |
| 2 | Nice-to-have | No dead code from the fix (deleted code was truly unused) | ✅ PASS |
| 3 | Critical | No sync DB calls on the asyncio event loop (re-confirm) | ✅ PASS |

**Critical Requirements:** 1/1 passed (sub-set re-confirmed)
**Important Requirements:** 1/1 passed
**Nice-to-have Requirements:** 1/1 passed

No quick fixes applied. No ensure.md Improvement Notices (no contradictions found — task used grep/static-check methods that match ensure.md's own prescription: "Validation: grep / static check").

---

## Requirement 1 — All callers of converted async functions properly await

ensure.md: "All callers of converted async functions properly await (`_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats`) — Validation: grep / static check"

Method: AST walk over `daemon/**/*.py` to enumerate every `Call` whose function name matches the targets, then check whether the immediate parent node is `ast.Await`.

### `_get_system_prompt_tokens`

Defined in `daemon/services/instance_messaging.py:532` (AsyncFunctionDef).

| Call site | File:Line | Awaited? |
|-----------|-----------|----------|
| `self._get_system_prompt_tokens(instance_id)` | `daemon/services/instance_messaging.py:581` | ✅ `await` |
| `self._get_system_prompt_tokens(instance_id)` | `daemon/services/instance_messaging.py:727` | ✅ `await` |

**2/2 call sites awaited** — PASS.

### `_compute_context_usage`

Defined in `daemon/services/instance_messaging.py:556` (AsyncFunctionDef).

| Call site | File:Line | Awaited? |
|-----------|-----------|----------|
| `self._compute_context_usage(instance_id, messages)` | `daemon/services/instance_messaging.py:611` | ✅ `await` |

**1/1 call site awaited** — PASS.

### `get_queue_stats`

Two definitions (delegate pair): `daemon/manager.py:5569` and `daemon/services/instance_messaging.py:3789` (both AsyncFunctionDef).

| Call site | File:Line | Awaited? |
|-----------|-----------|----------|
| `self._messaging_service.get_queue_stats(instance_id)` | `daemon/manager.py:5575` | ✅ `await` |
| `manager.get_queue_stats(instance_id)` | `daemon/routers/instances.py:377` | ✅ `await` |
| `manager.get_queue_stats(instance_id)` | `daemon/routers/instances.py:527` | ✅ `await` |
| `manager.get_queue_stats(instance_id)` | `daemon/routers/messages.py:591` | ✅ `await` |
| `manager.get_queue_stats(instance_id)` | `daemon/tools/instance.py:1671` | ✅ `await` |

**5/5 call sites awaited** — PASS.

### Aggregate: Requirement 1 — PASS

Total: **8/8** call sites across the three functions are properly `await`-ed. No orphan sync invocations found anywhere in `daemon/`.

---

## Requirement 2 — No dead code from the fix

ensure.md: "No dead code from the fix (deleted code was truly unused) — Validation: import check / grep"

This feature is **additive** (no deletion was the goal of "no dead code"), so the test is "every newly-introduced module / symbol is actually used."

### Module usage (imports across `daemon/`)

| Module | Importer(s) | Used as |
|--------|-------------|---------|
| `daemon.services.lifecycle_hooks` | `daemon/services/child_reports.py:25` | `LifecycleHookContext`, `dispatch_lifecycle_hooks` |
| `daemon.services.context_tools` | `daemon/mcp/kb_server.py:17`, `daemon/services/context_injection.py:12`, `daemon/services/lifecycle_hooks.py:10`, `daemon/tools/context_tools.py:20` | `list_context_files`, `read_context_file`, `resolve_context_dir`, `write_context_file` |
| `daemon.services.context_injection` | `daemon/services/context_messages.py:1193`, `daemon/tools/external_opencode.py:23`, `daemon/tools/knowledge_tools.py:18` | `get_shared_context` |
| `daemon.tools.context_tools` | `daemon/tools/instance.py:194` (via `create_context_tools`) | tool category |
| `daemon.services.child_reports` | `daemon/manager.py:80`, `daemon/services/__init__.py:14`, `daemon/services/instance_lifecycle.py:3662`, `daemon/services/instance_messaging.py:43` | `ChildReportsService` |

All five new/modified modules are wired into the daemon — none is orphan.

### `register_lifecycle_hook` — does it actually run?

Two **call sites** found by AST:

1. `daemon/services/lifecycle_hooks.py:34` — the **definition** (FunctionDef).
2. `daemon/services/lifecycle_hooks.py:117` — the **module-level** call that registers `_add_to_shared_context_md_files` for the `on_complete` event.

Because `daemon.services.lifecycle_hooks` is imported by `daemon/services/child_reports.py:25`, and `daemon.services.child_reports` is imported by `daemon/manager.py:80` at daemon startup, the module-level `register_lifecycle_hook(...)` call **executes at daemon init**. So the hook is registered before any child report fires. ✅

### `dispatch_lifecycle_hooks` — actually called?

- Call site: `daemon/services/child_reports.py:2939` (inside `_process_child_completion_and_notify_parent`'s `regular_child_completed` branch, wrapped in `asyncio.wait_for(..., timeout=5.0)`).
- The dispatch is gated by `lifecycle_hooks.get("on_complete", [])` read from the agent's `meta.json` via `registry.get_version(...)` → falls back to `registry.get_resolved(...)`.
- `agents/wanderer/meta.json` is configured with `{"on_complete": ["add_to_shared_context_md_files"]}`, so the wanderer is the first enabled agent.

✅ Called from the only completion path that fires for regular child reports.

### `LifecycleHookContext`

- Used at `daemon/services/child_reports.py:2928` (constructor) and passed as the third arg to `dispatch_lifecycle_hooks`.

### Other new symbols

- `daemon.registry.AgentMetadata.lifecycle_hooks: dict[str, list[str]]` (`daemon/registry.py:312`) — read at `daemon/services/child_reports.py:2901` via `getattr(agent_meta, "lifecycle_hooks", {})`.

### Aggregate: Requirement 2 — PASS

No dead code found. Every new module, function, and field is imported, registered, or invoked from a real call path.

---

## Requirement 3 — No sync DB calls on the asyncio event loop (re-confirm)

ensure.md: "No sync DB calls on the asyncio event loop — covered by `concurrency_atomic_unit_test` (thread-identity tests verify `asyncio.to_thread` wrapping for all DB helpers) — Validation: pack PASS"

Task scope: re-confirm via static check on the two new feature modules.

### `daemon/services/lifecycle_hooks.py`

AST walk flagged two method-`.get` calls:

- `:54` `_HOOK_REGISTRY.get(event, {})` — **in-memory dict** (module-level `_HOOK_REGISTRY: dict[str, dict[str, Callable]]`). Not a DB call.
- `:56` `registry.get(hook_name)` — **in-memory dict** (`_HOOK_REGISTRY[event]`). Not a DB call.

No `session.execute`, `session.commit`, `session.rollback`, `repository.get`, `engine.connect`, `Session()`, `inspect(...)`, or other sync DB idiom is present.

The only blocking-ish call is the **filesystem** write at lines 108-110, which **is** wrapped:

```python
await asyncio.to_thread(
    write_context_file, ctx.context_key, body, slug, ".md", ctx.instance_id
)
```

### `daemon/services/context_tools.py`

AST walk over this file flagged **zero** DB-like method calls. The module is pure filesystem helpers (atomic write via `tmp_path.write_text(...)` + `os.replace(...)`); all consumers in `daemon/tools/context_tools.py` and `daemon/mcp/kb_server.py` already wrap these via `asyncio.to_thread`.

### Cross-check: `child_reports.py:2912` (the only sync DB-ish helper in the new code path)

```python
context_key = await asyncio.to_thread(
    _resolve_tree_root_id, instance_id, parent_id, instance_repo,
)
```

Wrapped ✅.

### Aggregate: Requirement 3 — PASS

Re-confirmed: no sync DB calls in the new feature modules reach the event loop unwrapped.

---

## Notes

- **Quarantine awareness:** no test in this sub-set is governed by `QUARANTINE.md` (all checks are grep/AST). No impact.
- **Contradictions:** none. ensure.md's listed methods for these three requirements are "grep / static check" and "import check / grep", which the task followed exactly.
- **Blast-radius scope:** Instance Lifecycle Hooks is a single-feature additive change touching 6 files (and one MCP surface). It is **not** big/critical/architecture — therefore Release Gate items in `ensure.md` (full non-integration suite, E2E journeys) are **out of scope** for this static-only validation pass.
- **No quick fixes applied.** No code changes were made by this validation.
- **No commit hash** (validation only).
