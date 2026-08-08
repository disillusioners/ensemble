# Phase 3: Config Field + Wanderer meta.json

## Objective

Add the `lifecycle_hooks` configuration field to `AgentMetadata` (Pydantic model in `daemon/registry.py`) and configure Wanderer's `meta.json` to opt into the `on_complete` hook. The field is typed `dict[str, list[str]]` (event_name → list of hook function names) so an agent can register multiple hook functions per event. This makes the feature per-agent configurable with zero behavior change for agents that don't configure it.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add `lifecycle_hooks: dict[str, list[str]] = Field(default_factory=dict)` field to `AgentMetadata` class in `daemon/registry.py`, positioned after the existing `context_injection` field (after line 311, before `inject_allowed_models` at line 312). Include a descriptive docstring explaining: maps lifecycle event names (e.g. `on_complete`) to a list of hook function names registered in the dispatcher (e.g. `["add_to_shared_context_md_files"]`). Empty dict (default) = no hooks. Value is a list so multiple hook functions can be registered per event (W1). | none | `AgentMetadata` model accepts `lifecycle_hooks={"on_complete": ["x"]}` in constructor; defaults to `{}` when omitted; Pydantic validation passes for existing agents (backward-compatible). Empty list `{"on_complete": []}` also parses (treated as "no hooks for this event"). |
| 2 | Add `"lifecycle_hooks": {"on_complete": ["add_to_shared_context_md_files"]}` to `agents/wanderer/meta.json`, positioned after the existing `"context_injection"` key. The value is a list (W1) — even though only one hook is configured today, the schema reserves room for future hook functions. | task 1 | Wanderer's resolved metadata has `lifecycle_hooks == {"on_complete": ["add_to_shared_context_md_files"]}`; all other agents have `lifecycle_hooks == {}`. |
| 3 | Verify `extra="ignore"` compatibility — confirm that the new field does NOT break any existing meta.json parsing. Run the existing test suite that touches registry/meta parsing. | tasks 1-2 | All existing tests pass; no agent's metadata resolution changes behavior. |

## Coupling

- **Tight with Phase 4:** The integration code in Phase 4 reads `agent_meta.lifecycle_hooks` to decide whether to dispatch hooks. The field MUST exist before Phase 4 can function.
- **Independent of:** Phase 1, Phase 2 (no code dependency — only the config schema)

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Pydantic model change breaks JSON schema generation or API serialization | Medium | `lifecycle_hooks` is a simple `dict[str, list[str]]` with a default. It's additive — no existing field is modified or removed. Run any API/schema tests to confirm. |
| `meta.json` for Wanderer fails to parse (malformed JSON) | Low | The addition is a simple key-value pair. Validate by loading the file with `json.load()` after editing. |
| Type change from hypothetical `dict[str, str]` to `dict[str, list[str]]` breaks any existing meta.json that used the old shape | Low | This is a NEW field — no existing meta.json contains a `lifecycle_hooks` key yet (verified — only Wanderer will add it, in task 2 of this phase). The `extra="ignore"` policy on `AgentMetadata` means unrecognized fields are silently dropped, so older meta.json files in the wild are unaffected. |
| Single-element list vs. single string confusion in tests | Low | Document the canonical form: `{"on_complete": ["hook_fn_name"]}` (list, even with one element). Code review: any test that writes a string instead of a list should be caught by Pydantic's `list[str]` validator. |

## Exit Criterion

`AgentMetadata.lifecycle_hooks` exists as a typed `dict[str, list[str]]` field (default `{}`). Wanderer's `meta.json` configures `"lifecycle_hooks": {"on_complete": ["add_to_shared_context_md_files"]}`. All existing tests pass. Phase 4 can read the config (via `.get("on_complete", [])`) to decide which hook names to dispatch.
