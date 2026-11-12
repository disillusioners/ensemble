# Tool-Lane Research: project_future modeled on project_history

Worktree: `/home/nea/ensemble-src-wt-project-future` (feature/project-future, base ac9849bf7 == origin/latest).
All file:line references below were read directly from THIS worktree. Read-only — nothing committed.

---

## Q1. daemon/tools/project_history.py — full anatomy (305 lines total)

### Module constants (claimed :19-22 — ✅ CONFIRMED EXACT)
- `CATEGORY_NAME = "project_history"` — **:19**
- `CATEGORY_DOC = """Manage project history entries — ..."""` — **:20-22** (3-line docstring, ends :22)
- Length caps: `_MAX_SUMMARY_LEN = 300` — **:24**; `_MAX_DETAILS_LEN = 5000` — **:25**
- Imports: `HistoryEntryType` from `..repositories.project.models` (:13); `BLUEPRINT_ACTIVE_METADATA_KEY, SYSTEM_DEFAULT_PROJECT_NAME` from `daemon.constants` (:14); `register_tool_category` from `._tool_registry` (:15).

### _FULL_DOCS dict (claimed :30-90 — ⚠️ MINOR CORRECTION: actual dict is :29-91)
- `_FULL_DOCS = {` opens at **:29**, closes at **:91**. Keys (claimed :30-90 points at the first key and last key's doc — correct content, bounds off by one on each side):
  - `"project_history_add"` — **:30-52**
  - `"project_history_list"` — **:54-65**
  - `"project_history_search"` — **:67-78**
  - `"project_history_delete"` — **:80-90**
- Each full doc is attached to its tool as `_full_doc_` AFTER the def: **:219, :253, :282, :303**.

### Factory signature (claimed :106-109 — ✅ CONFIRMED EXACT)
```python
def create_project_history_tools(          # :106
    store, current_instance_id: str = "", agent_id: str = "",   # :107
    manager=None,                           # :108
) -> list:                                  # :109
```
- `manager=None` enables the Blueprint pending-queue side hook; `None` = legacy behavior (docstring :116-120).

### Decorators per tool
Every tool carries the SAME two-decorator stack, `register_tool_category` OUTERMOST, `tool` INNERMOST:
- add: `@register_tool_category(CATEGORY_NAME)` :123, `@tool` :124
- list: :221 / :222
- search: :255 / :256
- delete: :284 / :285

`register_tool_category` (daemon/tools/_tool_registry.py:826-851) just sets `func._tool_category = category` (:842) plus first-party provenance flag `func._tool_category_first_party = True` (:849). It does NOT insert the tool into any runtime list.

### The four tools — args + EXACT response shapes

**project_history_add** (:125-218) — args: `project_id: str, entry_type: str, summary: str, details: str | None = None, entry_metadata: dict | None = None` (:125-131).
Validation pipeline:
1. entry_type must be in `HistoryEntryType._value2member_map_` (:134-136; helper `_is_valid_entry_type` :94-96) → error `{"error": f"Invalid entry_type '{entry_type}'. Valid: {valid_types}"}`
2. project must exist via `store.get(project_id)` (:139-141) → `{"error": f"Project '{project_id}' not found"}`
3. summary must be non-empty after strip (:144-145) → `{"error": "Summary cannot be empty"}`
4. **TRUNCATE-not-reject**: `summary = summary[:300]` (:148); `details = details[:5000]` (:149-150)
5. `store.add_history_entry(...)` (:153-161) — returns the entry dict directly (:218 `return entry`).

SUCCESS response shape = `ProjectHistoryEntry.to_dict()` (daemon/repositories/project/models.py:498-510):
```python
{
  "id": str,                    # uuid4
  "project_id": str,
  "entry_type": str,
  "summary": str,               # ≤300
  "details": str | None,        # ≤5000
  "source_agent": str | None,
  "source_instance_id": str | None,
  "entry_metadata": dict | None,
  "created_at": "<ISO-8601 string>",
}
```

HIDDEN COUPLING inside add (:163-216): Blueprint pending-queue side hook.
- Fires only when `manager._blueprint_pending_repo` is set (:169-173) AND `entry_type in _PENDING_QUEUE_ENTRY_TYPES` = `frozenset({"feature", "milestone"})` (:103, :174-176) AND project is not `SYSTEM_DEFAULT_PROJECT_NAME` (:179).
- Per-project opt-in: `manager._project_repository.get_metadata(project_id, BLUEPRINT_ACTIVE_METADATA_KEY)` (:190-199); failure ⇒ treated as inactive.
- Fire-and-forget `pending_repo.enqueue(project_id=..., source_type="history", source_payload={...})` (:200-210); enqueue failure = WARNING log, history write still succeeds (:211-216).
- For project_future: decide explicitly whether to replicate or omit this hook.

**project_history_list** (:223-252) — args: `project_id: str, limit: int = 20, offset: int = 0, entry_type: str | None = None` (:223-228).
- Project-exists check (:231-233). **`limit = max(1, min(limit, 100))`** (:236); `offset = max(0, offset)` (:237). entry_type validated if provided (:240-242).
- `store.list_history_entries(...)` (:245-250) → returns the store dict verbatim (:252).

SUCCESS response shape (daemon/repositories/project/repository.py:1395-1400):
```python
{
  "entries": [ <ProjectHistoryEntry.to_dict()>, ... ],   # newest first (created_at.desc, repository.py:1392)
  "total": int,      # count AFTER entry_type filter, BEFORE pagination (:1388-1389)
  "limit": int,
  "offset": int,
}
```

**project_history_search** (:257-281) — args: `project_id: str, query: str, limit: int = 20, offset: int = 0` (:257-262). Same project check (:265-267), same limit/offset clamp (:270-271), NO entry_type filter. `store.search_history_entries(...)` (:274-279) → dict with `entries`, `total`, `limit`, `offset`, **plus `"query"`** (repository.py:1402-1408 docstring :1418; LIKE-escaped term, escape="\\" for SQLite/PG parity :1420-1427).

**project_history_delete** (:286-302) — args: `project_id: str, entry_id: str` (:286-289).
- Project check (:292-294). `store.delete_history_entry(entry_id, project_id=project_id)` (:297) → False on missing/ownership-mismatch (repository.py:1350-1356).
- Failure: `{"error": f"Entry '{entry_id}' not found in project '{project_id}'"}` (:300).
- SUCCESS: `{"success": True, "deleted_entry_id": entry_id}` (**:302**).

Factory return: `[project_history_add, project_history_list, project_history_search, project_history_delete]` — **:305**.

### Input validation summary
- Length caps are TRUNCATION (300/5000), never refusal (:148-150; second truncation layer in the repository :1309-1310).
- limit capped at 100 (:236/:270); offset floor 0; entry_type whitelist; empty-summary refusal; project-existence check on every tool.
- NO cap on total entries (unbounded growth) — project_history has NO max-entries ceiling. Critical notes is the capped contrast (Q3).

### What `store` IS
`store` = **`InstanceManager.project_store` = `self._project_repository`** (daemon/manager.py:2151) — i.e. the **ProjectRepository** instance from `daemon/repositories/project/repository.py`.
- `store.get(project_id)` → Project row (has `.name`, used at :179)
- `store.add_history_entry` — repository.py:1285-1325
- `store.list_history_entries` — repository.py:1362-1400
- `store.search_history_entries` — repository.py:1402+
- `store.delete_history_entry` — repository.py:1340-1360
- Storage model: `ProjectHistoryEntry` SQLModel, `__tablename__ = "project_history"` (models.py:479-481), FK→projects.project_id ON DELETE CASCADE (:485).

---

## Q2. daemon/tools/_tool_registry.py (2026 lines) — registry + construction seam

### CATEGORY_MODULES (claimed ~:1174 — ✅ dict starts EXACTLY :1174; project_history ENTRY is :1189)
- `CATEGORY_MODULES: dict[str, str | list[str]] = {` — **:1174**
- `"project_history": "daemon.tools.project_history"` — **:1189** (single-module string form; list form exists e.g. `"instance": ["daemon.tools.instance", "daemon.tools.tunables"]` :1192)
- Constraint comments: value MUST be a `daemon.tools.*` path — the AST scanner resolves it as `daemon/tools/<parts[2:]>.py` (:1207-1211, restated :1221-1224, :1236-1239). A `daemon.services.*` value is NEVER discovered and the exact-equality drift pin fails at boot.
- `"critical_notes"` precedent: :1176. `"pok"` precedent (most recent non-privileged category, 2026-11-07): :1188 with design comments :1177-1187.

### KNOWN_TOOL_NAMES (claimed ~:1565 — ✅ frozenset starts EXACTLY :1565; project_history NAMES are :1772-1775)
- `KNOWN_TOOL_NAMES: frozenset[str] = frozenset({` — **:1565**
- `"project_history_add"` — **:1772**; `"project_history_delete"` — **:1773**; `"project_history_list"` — **:1774**; `"project_history_search"` — **:1775**
- Maintenance contract documented in-place: regenerate via `uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"` (:1550-1553); bidirectional drift is caught by `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` (:1559-1560; file EXISTS at tests/unit/tools/test_frozen_tool_name_discovery.py).
- Rationale: frozen-binary fallback — in PyInstaller builds source files are absent, so `discover_all_tool_names()` returns `set(KNOWN_TOOL_NAMES)` when ZERO source files readable (:1088-1100); otherwise source-scan ∪ KNOWN (:1102-1105). Historical incident documented at :1536-1543 (2026-08-20 false "unknown tool" warnings).
- `DYNAMIC_TOOL_NAMES` (a separate frozenset, :23+) is for factory tools NOT in CATEGORY_MODULES-scanned import-time registration; project_history tools are decorator-registered inside a factory → they live in KNOWN_TOOL_NAMES via source scan, not DYNAMIC_TOOL_NAMES.

### CONSTRUCTION SEAM — in daemon/tools/instance.py, NOT _tool_registry.py
(⚠️ The commission brief located the seam in `_tool_registry.py`; it is actually in `instance.py`'s tool-assembly function. `_tool_registry.py` has no `create_project_history_tools` call and no `tools.extend`.)

- Import at module top: `from .project_history import create_project_history_tools` — daemon/tools/instance.py:281
- Construction + extend: **instance.py:6651-6655**
```python
history_tools = create_project_history_tools(     # :6651
    manager.project_store, current_instance_id, agent_id,   # :6652
    manager=manager,                                # :6653
)                                                   # :6654
tools.extend(history_tools)                         # :6655
```
- Immediate neighbors: critical-notes extend at :6641-6644 (`manager.project_store` passed the same way), job tools :6660-6661, scheduling :6670-6673, board :6691-6694. The board/scheduling/self-alarm comment blocks (:6675-6686, :6696-6705) explicitly document the PLACEMENT rule: extends MUST precede create_help_tool / scan_tools_for_full_docs / _apply_tool_filter — "A later extend is either invisible to the help tool or BYPASSES the privileged strip."

### Ordering of the three post-assembly steps (end of the same function)
- `help_tool = create_help_tool(tools, agent_id, mcp_tool_names, version_tag=version_tag)` — **instance.py:7620** (appended :7621). create_help_tool (daemon/tools/help.py:124-162) SNAPSHOT-builds its `tool_index` (:150-162) from the list it is given, and itself calls `scan_tools_for_full_docs(all_tools)` (:147) — a tool added after this point is invisible to tool_help.
- `scan_tools_for_full_docs(tools)` — **instance.py:7625** (populates `_tool_metadata` + `_full_docs`; def _tool_registry.py:1115-1129: harvests `_full_doc_` attrs, `hasattr(tool_func, '_full_doc_')` → `register_full_doc(tool_name, ...)` :1128-1129)
- `tools = _apply_tool_filter(tools, agent_id, mcp_tool_names, version_tag=version_tag)` — **instance.py:7628**
- So the project_history extend at :6655 precedes all three. ✅

### Enforcement (or absence) — decorator-registered but missing registry entries
There is NO single boot-time assertion that every `@register_tool_category`-tagged function is in CATEGORY_MODULES. The enforcement is DISTRIBUTED, and each miss fails DIFFERENTLY:

1. **Missing CATEGORY_MODULES entry** → module never AST-scanned (`discover_source_only_tool_names` iterates `CATEGORY_MODULES.items()`, _tool_registry.py:972) → names absent from the validator universe → (a) agent `tools.allow: ["project_future"]` produces startup WARNING "allow entry '...' is neither a known category nor a known tool" (daemon/registry.py:1429-1439 — warn, not refuse); (b) the drift pin `test_known_tool_names_matches_source_exactly_no_drift` FAILS if the names were pasted into KNOWN_TOOL_NAMES while the module is missing from CATEGORY_MODULES (KNOWN side would carry names source-scan can't see — bidirectional test).
2. **Missing KNOWN_TOOL_NAMES entry** (module IS in CATEGORY_MODULES) → source checkouts are fine (scan finds the names); FROZEN BINARY loses them from the validator universe → false "unknown tool" warnings at agent boot (the 2026-08-20 incident class, :1536-1543). The drift pin FAILS in CI regardless (source has it, KNOWN lacks it).
3. **Missing construction + tools.extend** → the tool exists nowhere at runtime: the decorator only sets attributes on the function object (:842, :849); if the factory is never called inside the assembly function, no instance ever receives the tool. This is documented IN-REPO as "SILENTLY INVISIBLE" (instance.py:6665-6668, :6677-6680, :6698-6701). No test catches it directly — it is the seam comment's load-bearing warning.
4. Startup validation universe = `list_tools_by_category()` ∪ `DYNAMIC_TOOL_NAMES` ∪ `discover_all_tool_names()` (registry.py:1401-1409); unknown allow/deny entries are WARNINGS only (:1437-1439), then the runtime membership filter simply drops what wasn't constructed.

---

## Q3. critical_notes.py — REFUSE-on-full cap (the deliberate contrast for FIFO eviction)

- `_MAX_ENTRIES = 200` — **daemon/tools/critical_notes.py:70** (claimed 200 ✅); `_MAX_EVICTION_CANDIDATES_NAMED = 3` — **:77**.
- Cap check (claimed :538-551 — ✅ essentially exact; check line :538, return dict :544-552):
```python
# Step 6: CAP CHECK (new-add path)                        # :537
if len(entries) >= _MAX_ENTRIES:                           # :538
    candidates = _eviction_candidates(entries)[:_MAX_EVICTION_CANDIDATES_NAMED]  # :539
    candidate_lines = [                                    # :540-543
        f"  - id={c.id} priority={c.priority} created_at={c.created_at} summary={c.summary!r}"
        for c in candidates
    ]
    return {
        "error": (
            f"Project '{project_id}' is at the cap of {_MAX_ENTRIES} "
            f"(currently {len(entries)}) critical notes. "
            f"Remove one of the following eviction candidates and re-add, or use "
            f"project_cn_remove + project_cn_add.\n"
            + "\n".join(candidate_lines)
        )
    }                                                      # :544-552
```
- Response shape: single `{"error": "<multi-line human guidance + up-to-3 candidate lines>"}` dict. NO silent eviction — the caller must remove explicitly. The tool's `_full_doc_` states the contract verbatim: "If the list is full (200 entries), the call REJECTS with an error naming eviction candidates. No silent eviction." (:578-579). Sibling strictness: near-duplicate REJECT (:525-535) — same philosophy: refuse and instruct, never auto-evict.
- project_future's FIFO auto-eviction is therefore a DELIBERATE deviation from the critical_notes doctrine; worth stating that contrast in the new tool's docs.

---

## Q4. Tool gating: how meta.json `tools.allow` filters availability at runtime

**The availability gate is `_apply_tool_filter` (daemon/tools/instance.py:7671-7760) + `resolve_tool_filter` (instance.py:431-587). `daemon/tools/_auth.py` is a DIFFERENT gate (spawn/team-membership), not tool availability.**

Runtime flow:
1. Agent meta resolution: `registry.get_version(agent_id, version_tag)` falling back to `registry.get_resolved(agent_id)` — instance.py:7692-7695 (versioned meta preferred; reviewerv2-style fix documented :7689-7691).
2. NO tools config at all → everything allowed EXCEPT privileged categories: `return _strip_privileged_category_tools(tools)` (:7697-7701; helper def :7633-7668 filters on `getattr(t, "_tool_category", None) not in PRIVILEGED_TOOL_CATEGORIES`, registry constant `_tool_registry.py` — set ends :823). Same for allow+deny both empty / resolve returns None (:7736-7737). → a NON-privileged category like project_history (and project_future if modeled the same) is default-granted to empty-allow agents.
3. Innate-skill expansion: `effective_allow = expand_allow_for_innate_skills(agent_meta.tools.allow, agent_meta.innate_skills)` (:7724-7727; def :190).
4. `resolve_tool_filter(allow=..., deny=..., all_tool_names=...)` (:7728-7732; def :431):
   - allow entries are CATEGORY names or literal tool names: category → expand from `tool_categories` (built via `list_tools_by_category()` from `_tool_metadata`, :468 — populated by scan_tools_for_full_docs at :7625 BEFORE the filter runs; the ordering dependency is documented at :492-493); non-category entries pass through as literal names (:569-575).
   - deny wins over allow (:577-585).
   - Dynamic prefix categories (`mcp`, `plane`, ... `DYNAMIC_TOOL_PREFIXES`) expand from live `all_tool_names` with an ownership guard excluding tools owned by other categories (:474-501, :522-555).
5. Final membership filter: a tool survives iff its `name` ∈ allowed set (:7739-7754; name from `.name` or func `__name__` fallback :7742-7747).
6. Startup-time validation (registry.py:1429-1439): every allow/deny entry across base + versioned agents checked against known categories ∪ discovered tool names ∪ dynamic prefixes; unknown → WARNING (non-fatal).

**_auth.py's role (separate):** `_check_team_membership` (_auth.py:90+) governs SPAWN authorization (spawn_instance/council) against `team_members`; `TOOL_REQUIRED_AGENTS` (:35-51) implicitly grants required teammates when an agent allows an agent-backed category (e.g. "design" → image-comparator). project_history has no entry there — a non-agent-backed category needs none. project_future likewise needs NO _auth.py entry unless its tools spawn agents.

---

## Q5. The FIVE registration steps — checklist with project_history exemplar file:line

1. **Module + decorator stack** — new `daemon/tools/project_future.py`; every tool wrapped `@register_tool_category(CATEGORY_NAME)` (outermost) over `@tool` (innermost).
   Exemplar: project_history.py:123-124 (add), :221-222 (list), :255-256 (search), :284-285 (delete). Category constants :19-22. Decorator impl: _tool_registry.py:826-851 (sets `_tool_category` + `_tool_category_first_party`).
2. **`_full_doc_` per tool** — dict of long docs + assignment after each def: `_full_doc_ = _FULL_DOCS["<name>"]`.
   Exemplar: _FULL_DOCS dict project_history.py:29-91; attachments :219 (add), :253 (list), :282 (search), :303 (delete). Harvested by scan_tools_for_full_docs (_tool_registry.py:1128-1129). Short docstring stays one-liner "… Use tool_help() for details." (e.g. :132).
3. **CATEGORY_MODULES entry** — `"project_future": "daemon.tools.project_future"`.
   Exemplar: _tool_registry.py:1189 ("project_history"); dict opens :1174; value MUST be `daemon.tools.*` (scanner resolution rule :1207-1211).
4. **KNOWN_TOOL_NAMES entries** — all four/five names, sorted position, then regenerate the set.
   Exemplar: _tool_registry.py:1772-1775. Regen command :1550-1553; drift pin tests/unit/tools/test_frozen_tool_name_discovery.py (:1559-1560). Skipping this = frozen-binary "unknown tool" false warnings + CI drift-pin failure.
5. **Construct + `tools.extend` BEFORE help/scan/filter** — inside the instance tool-assembly function in daemon/tools/instance.py.
   Exemplar: import instance.py:281; construct+extend :6651-6655 (pass `manager.project_store, current_instance_id, agent_id, manager=manager`). The three ordering anchors: create_help_tool :7620, scan_tools_for_full_docs :7625, _apply_tool_filter :7628. Placement rationale comments :6675-6686 (later extend = invisible to help or bypasses privileged strip).

Optional/conditional extras (project_history exemplars):
- Blueprint pending-queue side hook: project_history.py:163-216 (needs `manager=` threading at the construction site, :6653).
- Per-agent `tools.allow` grants in agents' meta.json (category key must match CATEGORY_NAME) — pok precedent comment _tool_registry.py:1177-1187 (B4 sweep landed `tools.allow: ["pok"]` across 40 agents; watcher excluded as empty-allow inherits-all).

---

## Surprises / plan-relevant findings

1. **The construction seam is in instance.py, not _tool_registry.py** — the brief's "CONSTRUCTION SEAM ... in _tool_registry.py ~:1174" conflates the registry dict with the assembly function. There is exactly ONE construction site for create_project_history_tools (instance.py:6651) plus one import (:281); no second registration site found.
2. **Enforcement is distributed and warn-only at boot**: unknown allow entries WARN (registry.py:1437-1439); the drift pin is the only hard failure, and it does not catch a missing extend (silently invisible). plan-body-ledger-agreement style trap: a green suite does not prove the tool is reachable — a live-instance smoke must list the tool.
3. **Name-collision scan**: no occurrence of `project_future` or `project-future` anywhere in `agents/` (grep, 0 hits) and none in `daemon/` except possibly one oversized skipped file (>1.4MB — the grep tool skipped it; given the name is brand-new, collision risk is negligible but not byte-proven). Tool names `project_future_*` would sit alphabetically adjacent to the `project_*` family in KNOWN_TOOL_NAMES (:1757-1786) — mind sort order when regenerating.
4. **PRIVILEGED_TOOL_CATEGORIES decision**: project_history is NOT privileged → empty-allow agents (e.g. watcher) get it by default. If project_future should be default-on too, do nothing; if opt-in, the category must join PRIVILEGED_TOOL_CATEGORIES (registry :822 shows the set's tail; instance.py:7633-7668 strip + resolve :557-567 both consult it) — that touches the same-PR five-pin exact-set assertions mentioned at instance.py:7646-7653.
5. **add() has a hidden write-amplification hook** (Blueprint pending-queue, :163-216) — copying the add tool verbatim would drag blueprint coupling along; decide consciously.
6. **Response-shape convention**: errors are ALWAYS `{"error": "<string>"}` (no codes, no structured fields); list/search envelope is `{entries, total, limit, offset[, query]}`; delete is `{success, deleted_entry_id}`. FIFO eviction in add() would need a documented extra field (e.g. `evicted: [...]`) — no precedent in this lane.
7. **Every new SQLModel table needs its own checksummed migration .sql in daemon/migrations/versions/** (Core Architecture blueprint; the runner is SQLite-only and PG is ensured at boot) — applies to a project_future table; project_history's table (`project_history`, models.py:481) followed this.
8. **grep tooling caveat**: file-scoped grep silently returns "No matches" on larger files in this environment (verified false-negative on _tool_registry.py:826 for `register_tool_category`); directory-scoped grep is reliable. Anyone re-verifying these anchors should grep directories, not files.
