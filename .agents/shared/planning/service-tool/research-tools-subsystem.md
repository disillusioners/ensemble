# Research: agents-ensemble Tools Subsystem (for planned `service` tool category)

- Repo: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Branch inspected: `feature/service-tool` (fresh from latest), 2026-09-14
- Method: READ-ONLY code inspection. Every claim carries `file:line` from THIS branch.
- Stale-anchor note: several KB anchors from earlier sessions are shifted on this branch
  (e.g. `create_instance_tools` extends now live at `daemon/tools/instance.py:4392-4649`,
  `_strip_privileged_category_tools` at `instance.py:4667`). Line numbers below are current.

---

## Q1. The 3-step registration seam — verified + worked example

### Seam anchors (verified on this branch)

| Step | Anchor | Current location |
|---|---|---|
| 1. Decorator | `register_tool_category(category)` — stamps `func._tool_category` (`:147`) and `func._tool_category_first_party = True` (`:154`) | `daemon/tools/_tool_registry.py:131-156` |
| 2. Registry entry | `CATEGORY_MODULES: dict[str, str \| list[str]]` mapping category key → module path(s) | `daemon/tools/_tool_registry.py:479-525` |
| 3. Construction | factory call + `tools.extend(...)` inside `create_instance_tools(...)` | `daemon/tools/instance.py:1796` (fn def); extends at `4392-4649` |
| Privileged set | `PRIVILEGED_TOOL_CATEGORIES = frozenset({"system_upgrade", "system-log", "ens-db"})` | `daemon/tools/_tool_registry.py:124-128` — VERIFIED exactly 3 entries |

All three steps are REQUIRED: decorator-only = silently invisible (comment precedent at
`instance.py:4586-4592` and `4617-4620`: "the extend below is the third step of the
three-step registration seam (decorator + registry entry + construction — all three required)").

### Worked example: `system_upgrade` (privileged category, factory-created)

1. **Decorator**: tools in `daemon/tools/upgrade_tools.py` are built by
   `create_upgrade_tools()` with `@register_tool_category("system_upgrade")` +
   `@tool` inside the factory (evidence: `DYNAMIC_TOOL_NAMES` comment
   `_tool_registry.py:71-77` — "system_upgrade tools — created by create_upgrade_tools()
   factory (daemon/tools/upgrade_tools.py, P2.2 Dispatch A read pair + Dispatch B actor
   pair)"; registration-source pins in `tests/unit/tools/test_upgrade_registration.py:92-94`).
   The module also carries the registration checklist as a greppable comment block
   (`upgrade_tools.py:132` references PRIVILEGED_TOOL_CATEGORIES; the pin test
   `test_upgrade_registration.py:111-119` asserts the "P2.2 REGISTRATION CHECKLIST"
   comment block exists).
2. **CATEGORY_MODULES entry**: `"system_upgrade": "daemon.tools.upgrade_tools"` —
   `_tool_registry.py:518`. (Sibling: `"system-log": "daemon.tools.system_log_tools"`
   at `:514`.)
3. **Factory call + extend** in `create_instance_tools()`:
   - system-log: `create_system_log_tools(manager, current_instance_id, agent_id)` →
     `tools.extend(system_log_tool_list)` — `instance.py:4583-4584`.
   - system_upgrade: `create_upgrade_tools(manager, current_instance_id, agent_id,
     agent_tag=version_tag)` → `tools.extend(upgrade_tool_list)` — `instance.py:4593-4596`.
   - Ordering constraint: both happen BEFORE `scan_tools_for_full_docs(tools)`
     (`instance.py:4659`), BEFORE `create_help_tool(...)` (`:4654`), and BEFORE
     `_apply_tool_filter(...)` (`:4662`).
4. **DYNAMIC_TOOL_NAMES entries** (validation universe before first instance build):
   `release_info`, `upgrade_status`, `system_restart`, `system_upgrade` —
   `_tool_registry.py:74-77`; `ens_system_log_*` ×4 at `:50-53`.
5. **KNOWN_TOOL_NAMES** (frozen-binary fallback universe): `_tool_registry.py:559-740`
   (contains e.g. `system_upgrade` `:721`, `ens_system_log_*` `:595-598`). Regeneration
   command documented at `:544-548`; drift test named at `:553-554`
   (`tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift`).
6. **daemon/loader.py warm-list entry**: `_ensure_tool_metadata_populated()`
   (`daemon/loader.py:27-114`) imports the factory modules (`:58-62`, incl.
   `create_upgrade_tools` `:61`), builds **metadata-scan stubs with `None` manager**
   (`:79-90`; factories dereference manager only at CALL time — comment `:76-78`),
   flattens the lists into `all_tools` (`:107-112`) and runs
   `scan_tools_for_full_docs(all_tools)` (`:114`). Absence from this list = empty
   cold-boot docs (the W1 FIX-NOW incident, comment `:51-57`).
7. **Cold-boot doc test**: `tests/test_loader.py:966`
   (`class TestMaintenancerToolsDocColdBoot`) — fixture `cold_registry` (`:983-1018`)
   snapshots `_tool_metadata`/`_full_docs`, calls `clear_registry()` (`:1011`) to force
   the cold warm-list scan; `test_cold_boot_docs_cover_all_allowed_categories`
   (`:1020`) asserts every allowed category section appears, incl.
   `"System Upgrade": "system_upgrade"` and `"System Log": "ens_system_log_list"`
   (`:1036-1042`).

---

## Q2. bash tool (`daemon/tools/bash.py`, 433 lines)

**Spawn mechanics**
- `asyncio.create_subprocess_exec(*command, ...)` for argv form /
  `asyncio.create_subprocess_shell(command, ...)` for string form — `bash.py:289-305`.
- `start_new_session=True` on non-Windows (`subproc_kwargs`, `bash.py:256-258`) → the
  child becomes a new process group (PGID == PID); backgrounded grandchildren
  (`nohup ... &`) stay in the group and are killable with it (comment `:243-245`).
- stdout/stderr captured to **temp FILES, not pipes** (`tempfile.mkstemp`, `bash.py:268-275`)
  specifically to avoid the "backgrounded child keeps pipe write-end open →
  communicate() hangs forever" failure (comment `:247-255`). Optional stdin via temp
  file (`:279-284`).
- **Eager PGID capture at spawn, before any await** (`pgid = os.getpgid(proc.pid)`,
  fallback `pgid = proc.pid`; `bash.py:315-321`; rationale = PID-recycling TOCTOU,
  comments `:237-241` and `:147-152`).

**Timeout enforcement + kill**
- LLM-visible schema `BashInputSchema` (`bash.py:200-206`): `timeout` default 1800 s.
- Validation: `timeout < 0` or `> 1800` → error string, no spawn (`bash.py:222-226`).
  `timeout == 0` → no timeout (`actual_timeout = None`, `bash.py:324-325`).
- `await asyncio.wait_for(proc.wait(), timeout=actual_timeout)` (`bash.py:327`);
  on `asyncio.TimeoutError` → `await _kill_process(proc, pgid=pgid)` (`bash.py:329-336`).
- `_kill_process` (`bash.py:138-186`): `os.killpg(target_pgid, SIGTERM)` (`:168`) →
  wait 5 s (`:174`) → `os.killpg(target_pgid, SIGKILL)` (`:179`); Windows falls back to
  `proc.send_signal(SIGTERM)` / `proc.kill()` (`:172`, `:183`). PGID resolved ONCE at top
  (`:155-160`), never re-derived inside the kill branches.
- `asyncio.CancelledError` path: `task.uncancel()`, shielded `_kill_process` +
  shielded registry `unregister`, then ALWAYS re-raises (`bash.py:369-395`).

**Return shape**
- `"STDOUT:\n<out>\n\nSTDERR:\n<err>\n\nEXIT CODE: <n>"` (`bash.py:345-352`); on timeout the
  exit-code part is replaced by `ERROR: Command timed out after {timeout} seconds` and
  whatever was captured is still surfaced (`bash.py:350-355`); hard truncation at
  150,000 chars with a redirect-to-file hint (`bash.py:357-366`). Full-doc attr
  `bash._full_doc_` at `:415-433`.

**Per-instance state**
- `BashProcessRegistry` — in-memory, module-level **singleton**
  `get_bash_process_registry()` (`bash.py:111-119`). Storage: `Dict[instance_id,
  List[BashProcessEntry]]` where `BashProcessEntry = {pid, pgid}` (`bash.py:20-32`),
  guarded by `asyncio.Lock` (`:33`).
- `register` after spawn (`bash.py:321`), `unregister` after explicit kill/timeout
  (`:335`, `:390-392`), `cleanup_instance(iid)` SIGKILLs every tracked group (`:54-69`),
  `cleanup_all()` for daemon shutdown (`:71-95`) — documented limitations:
  `setsid`-detached orphans unreachable; registry NOT persisted, so processes from a
  hard crash cannot be enumerated after restart (`bash.py:74-79`).
- `instance_id` is runtime-injected, not LLM-controlled (comment `bash.py:210-211`);
  the injection happens via wrapper composition in `create_instance_tools()`:
  `_make_instance_id_aware(_make_workdir_aware(bash, get_current_workdir),
  get_current_instance_id)` — `instance.py:4351-4354` (wrappers at `instance.py:1533`
  and `:1603`). The wrapper rebuilds the StructuredTool and must re-apply
  `_tool_category` + first-party marker (mechanism documented at
  `_tool_registry.py:467-470`).

---

## Q3. proc tools (`daemon/tools/proc_tools.py`, 2196 lines)

**Surface**: `create_proc_tools(current_instance_id)` (`proc_tools.py:1872`) returns
`[proc_run, proc_logs, proc_status, proc_stop, proc_list]` (`:2196`); empty list when
`current_instance_id` falsy (`:1896-1900`). Definitions: `proc_run` `:1922`,
`proc_logs` `:2031`, `proc_stop` `:2135`, `proc_list` `:2176` (`proc_status` between
`:2031-2135`). Wired at `instance.py:4404-4405` — **without** workdir auto-injection
because `proc_run` takes an explicit `workdir` (`instance.py:4398-4403`).

**Registry location: manager-held module singleton, bucketed per instance**
- `_background_process_manager = BackgroundProcessManager()` at module level —
  `proc_tools.py:1842`; accessor `get_background_process_manager()` `:1845-1852`
  (function form so tests can patch).
- Per-instance bucketing inside the manager: `self._processes[instance_id][process_id]
  → ProcessInfo` (bucket pops at `:1119` and `:1546`; cap check at `:1090-1094`).
  Hard cap `MAX_PROCESSES_PER_INSTANCE = 10` (`:85`).
- `ProcessInfo` dataclass `:126-202`: `process_id` = `proc-{8 hex}` (`:205-207`),
  `instance_id`, `command`, `proc` handle, 4 MB memory ring buffer + spill
  `NamedTemporaryFile` (`:87-96`, `:170-177`), `status ∈ {running, exited, killed,
  error}` (`:141-142`), `exit_code`, `reader_task`/`exit_task`/`timeout_task`
  (`:145-150`), `timed_out`/`user_stopped`, `tracking_id` (`:189-196`).

**Spawn (`proc_run` → manager start, `proc_tools.py:1106-1197`)**
- `asyncio.create_subprocess_exec/shell` with `stdin=DEVNULL`,
  `stdout=PIPE`, `stderr=STDOUT` (merged for chronological output) — `:1144-1162`.
- `start_new_session=True` on Unix (`:1131-1133`) — same group-kill semantics as bash
  (design note `:29-35`).
- Child env gets `ENSEMBLE_PROC_TRACKING_ID=<process_id>` injected (`_TRACKING_ENV_VAR`
  `:114-118`; env build `:1140`) — PID-reuse defense.
- Reader + exit-watcher tasks `:1183-1190`; optional timeout killer task `:1193-1197`.
- Shell semantics ALWAYS (`proc_run` docstring `:1930-1934`).

**What `proc_stop` sends**
- `manager.stop_process(instance_id, process_id, force)` (`:2148-2152`).
- Strategy: `SIGTERM` → wait up to `_STOP_GRACE_SECONDS = 5.0` (`:106`) → `SIGKILL`;
  `force=True` skips straight to `SIGKILL` (`proc_stop` docstring `:2136-2141`, full
  doc `:2154-2169`).
- All kills route through `_attempt_kill_signal` (`:918-975+`), which runs three
  defense layers: **L1** status gate (`:961-962`), **L2** liveness via
  `proc.returncode` (`:968-974`), **L3** PID ownership via `_verify_pid_ownership`
  (`:228-242` — reads the tracking env var back from `/proc/{pid}/environ` on Linux
  or `ps eww -p` on macOS/BSD; aborts the kill on mismatch), then sends
  `os.killpg` (Unix) / `proc.send_signal` (Windows) (`:950-951`).
  Dispositions: `skipped_terminal` / `skipped_exited` / `aborted_recycled` / `sent`.
- Timeout kill also goes through the helper with `SIGKILL` (`:882-887`), with
  `timed_out` rollback if the PID was recycled (`:888-901`).

**What happens to children when the owning instance terminates**
- **They are killed.** `InstanceService.terminate_instance`
  (`daemon/services/instance_lifecycle.py:2113`) runs, in order: step 2.55 —
  `get_background_process_manager().cleanup_instance(instance_id)`
  (`instance_lifecycle.py:2333-2340`); step 2.56 —
  `get_bash_process_registry().cleanup_instance(instance_id)` (`:2347-2354`), both
  best-effort with the explicit rationale "orphaned background processes would
  survive instance termination otherwise" (`:2330-2332`) and TERMINATED instances
  "leak bash-spawned process groups until root finalizes or daemon shutdown"
  otherwise (`:2342-2346`). `BackgroundProcessManager.cleanup_instance`
  (`proc_tools.py:1537-1619`) cancels reader/exit/timeout tasks (bounded 2 s await,
  `:1561-1587`), force-kills via the defense-layered helper
  (`:1597-1608`), closes/unlinks the spill file.
- Daemon shutdown sweeps everything:
  `manager.py:10922-10935` (proc `cleanup_all`) and `manager.py:10937-10952`
  (bash `cleanup_all`), both best-effort BEFORE workers/sources teardown
  (`:10915-10921`).
- **Durability ceiling (relevant to the planned `service` category)**: both registries
  are in-memory only. Documented non-goals today: `setsid`-detached orphans
  unreachable (`proc_tools.py:1631-1635`, `bash.py:75-77`) and **crash-recovery leak —
  nothing survives a daemon restart** (`proc_tools.py:1636-1643`, `bash.py:77-79`).
  The module docstring's claim that manager lifecycle code calls `cleanup_instance`
  on termination (`proc_tools.py:25-27`) is realized at
  `instance_lifecycle.py:2333-2354`.
- Secondary consumer: `daemon/services/job_feedback_observer.py:2857-2883` also
  imports both accessors (purpose not inspected — UNVERIFIED beyond the import sites).

---

## Q4. Authorization surface for a NEW non-privileged category

**How an agent's tool set resolves**
- `create_instance_tools()` ends with `_apply_tool_filter(tools, agent_id,
  mcp_tool_names, version_tag=version_tag)` — `instance.py:4662`.
- `_apply_tool_filter` (`instance.py:4687-4776`): resolves meta via
  `registry.get_version(agent_id, version_tag)` falling back to
  `registry.get_resolved(agent_id)` (`:4708-4711`).
  - `agent_meta is None or agent_meta.tools is None` → **default-allow** all tools
    minus privileged (`_strip_privileged_category_tools`, `:4713-4717`).
  - Otherwise: `effective_allow = expand_allow_for_innate_skills(tools.allow,
    innate_skills)` (`:4740-4743`), then `resolve_tool_filter(allow=effective_allow,
    deny=tools.deny, all_tool_names=...)` (`:4744-4748`).
    `allowed_tools is None` (empty allow+deny) → default-allow minus privileged again
    (`:4752-4753`).
- `resolve_tool_filter` (`instance.py:273-353`):
  - both allow+deny empty → `None` (everything) — `:299-304`.
  - allow empty → universe = union of ALL category tool lists **skipping**
    `PRIVILEGED_TOOL_CATEGORIES` — `:323-333` (privileged skip at `:331-332`).
  - allow set → each entry is a **category key (exact match against
    `CATEGORY_MODULES`-derived `tool_categories` dict)** expanded to its tool names,
    or a literal tool name — `:335-341`.
  - deny wins (`:343-351`); MCP dynamic expansion (`mcp_` prefix + ≥1 more
    underscore) `:313-322`.
- **Strip sites on this branch** (defense-in-depth, 3 sites):
  1. `resolve_tool_filter` empty-allow skip — `instance.py:329-333`.
  2. `_apply_tool_filter` tools-config-None path — `instance.py:4717`.
  3. `_apply_tool_filter` allowed-None path — `instance.py:4753`.
  Helper: `_strip_privileged_category_tools` (`instance.py:4667-4684`).
  (Prior-KB refs `~282-290 / ~2186 / ~2224 / ~4497-4516` are shifted on this branch.)

**`expand_allow_for_innate_skills`** (`instance.py:167-197`)
- Appends categories from `INNATE_SKILL_TOOL_CATEGORIES` (`instance.py:157-164`,
  e.g. `"opencode": ["external_opencode"]`) to an explicit allow list, de-duplicated.
  No-op when `allow is None` or no innate skills (`:186-187`). Used at
  `instance.py:4740`, `daemon/loader.py:186`, `daemon/tools/help.py:103`.

**What default-OPEN means for a new category**
- A new category NOT added to `PRIVILEGED_TOOL_CATEGORIES` is automatically granted to:
  - agents with NO `tools` config at all (`instance.py:4713-4717`), and
  - agents with empty allow+deny (`:4752-4753`) or empty-allow-only (`:323-333`).
  Nothing else is required for them to receive it.
- Agents WITH a non-empty `tools.allow` must name the category key (or one of its tool
  names) explicitly — `instance.py:335-341`. So "default-open" applies only to the
  no/empty-config universes; explicit-allow agents are closed-world.
- Deny rules are never required for a non-privileged category (structural opt-in is
  only a privileged-category concept — `_tool_registry.py:105-123`).

**Exact-equality pin assertions (quoted)**

`tests/unit/tools/test_upgrade_registration.py:105-109`:
```python
        assert PRIVILEGED_TOOL_CATEGORIES == frozenset({
            "system_upgrade",
            "system-log",
            "ens-db",
        })
```
(context `:96-104`: "Adding a category here is a deliberate trust decision, and this
pin makes silent additions visible (D18 — same-PR pin updates)".) The same file also
pins the instance.py wiring greps at `:90-94`:
```python
        assert "from .upgrade_tools import create_upgrade_tools" in source
        assert "tools.extend(upgrade_tool_list)" in source
        assert "create_upgrade_tools(" in source
```

`tests/unit/tools/test_attestation_registration.py:158-162`:
```python
        assert PRIVILEGED_TOOL_CATEGORIES == frozenset({
            "system_upgrade",
            "system-log",
            "ens-db",
        })
```
(context `:142-157`: attestation deliberately NOT privileged (D7); the pin is updated
in the same PR as any promotion.) The same file pins decorator ORDER (register
outermost, tool innermost) at `:128-140` and live category-attr survival through the
langchain `@tool` wrap at `:174-177`.

**Startup validation universe** — `AgentRegistry.validate_tool_configs`
(`daemon/registry.py:1001`) validates agent `tools.allow/deny` names against the
known universe (registry metadata + `DYNAMIC_TOOL_NAMES` + `discover_all_tool_names()`
+ `CATEGORY_MODULES` keys — see `DYNAMIC_TOOL_NAMES` docblock,
`_tool_registry.py:20-22`). New factory-created tool names omitted from
`DYNAMIC_TOOL_NAMES` produce false "unknown tool" warnings before the first instance
build populates metadata.

---

## Q5. Tool docs / registry surfacing

- **Per-module display metadata**: each category module defines `CATEGORY_NAME` +
  `CATEGORY_DOC` — e.g. bash: `bash.py:121-135` ("Shell"); proc:
  `proc_tools.py:66-77` ("Background Processes"). `get_tool_categories()`
  (`_tool_registry.py:743-778`) keys sections by importing the FIRST module in the
  `CATEGORY_MODULES` entry and reading its `CATEGORY_NAME` (`:762-768`);
  `get_category_doc()` (`:781-814`) returns `(CATEGORY_NAME, CATEGORY_DOC)` and joins
  docs for list-valued modules (`:796-806`).
- **Metadata population**: `scan_tools_for_full_docs(tools)` (`_tool_registry.py:420-475`)
  reads `_full_doc_` attrs (`:432-434`), first line of description/docstring as
  short_doc (`:436-441`), and category from `_tool_category` or a name-prefix
  fallback (`:443-446`). Category updates on rescan are STICKY to first-party tools
  only — `:455-475` (spoofing defense; wrapper re-application noted `:467-470`).
  Called in `create_instance_tools()` at `instance.py:4659` and from the loader warm
  path at `daemon/loader.py:150`.
- **System-prompt docs**: `load_tools_doc_for_agent` (`daemon/loader.py:~120-234`)
  resolves the same allow/deny logic (docs side mirrors execution side —
  `loader.py:180-199`); the no-filter path uses `_default_documented_tools()`
  (`daemon/tools/help.py:38-61`) which excludes `PRIVILEGED_TOOL_CATEGORIES` (`:56-57`).
  The `tool_help` tool itself also filters privileged categories for non-opted agents
  (`help.py:41`, `:56`).
- **What a new category must add to appear correctly**:
  1. `CATEGORY_MODULES` key (`_tool_registry.py:479-525`) — REQUIRED (drives category
     expansion, doc lookup, AST discovery).
  2. `CATEGORY_NAME` + `CATEGORY_DOC` module attrs — REQUIRED for pretty sections
     (else raw key is used, `_tool_registry.py:768`, `:810-811`).
  3. `DYNAMIC_TOOL_NAMES` entries for factory-created names (`:23-94`) — REQUIRED for
     pre-build config validation.
  4. `KNOWN_TOOL_NAMES` regen (`:559-740`, command `:544-548`) — REQUIRED for the
     frozen-binary fallback + drift pin.
  5. loader warm list + cold-boot test — REQUIRED if system-prompt docs must work on
     a cold boot (see Q1 items 6-7).
  6. `_full_doc_` attrs on each tool — for `tool_help` depth (pattern:
     `bash.py:415-433`, `proc_tools.py:2154-2169`).
  Note: `PRIVILEGED_TOOL_CATEGORIES` is NOT touched for a normal category — only if
  the category must be opt-in-only.

---

## Q6. Per-instance factory closure pattern in `create_instance_tools()`

**Signature** (`instance.py:1796`):
`create_instance_tools(manager, current_instance_id, agent_id="", version_tag=None)`.

**Closure context built inside** (`instance.py:1814-1833`):
- `get_current_workdir()` → project workdir via `_get_project_workdir(manager,
  current_instance_id)` (`:1817-1818`).
- `get_current_instance_id()` (`:1820-1821`).
- `caller_agent_id` — pinned because `spawn_instance`'s own `agent_id` param shadows
  the outer name (`:1823-1827`).
- `caller_version_tag` — defensive snapshot for auth gates (`:1829-1833`).

**Factory-arg patterns in use** (what a new category's factory can ask for):
| Factory | Call site | Args |
|---|---|---|
| `create_proc_tools` | `instance.py:4404` | `current_instance_id` only; state in its own module singleton (`proc_tools.py:1842`, `:1911`) |
| `create_critical_notes_tools` | `instance.py:4408-4410` | `manager.project_store, current_instance_id, agent_id` |
| `create_system_log_tools` | `instance.py:4583` | `manager, current_instance_id, agent_id` |
| `create_upgrade_tools` | `instance.py:4593-4595` | `manager, current_instance_id, agent_id, agent_tag=version_tag` |
| `create_attestation_tools` | `instance.py:4606-4608` | `manager, current_instance_id, agent_id` |
| `create_ens_db_tools` | `instance.py:4621-4623` | `manager, current_instance_id, agent_id` |
| `create_db_tools` | (loader warm stub) `loader.py:87-90` | reads `manager.credential_manager` AT CONSTRUCTION (N1) — the one factory needing a real attribute at build time |

**What a `service` factory would need**
- `current_instance_id` — ownership bucketing / attribution (proc precedent:
  `proc_tools.py:1872-1915`).
- `manager` — only if the service registry is hosted on the InstanceManager facade
  (precedents: `manager._mcp_service` used in lifecycle cleanup,
  `instance_lifecycle.py:2324-2326`; `manager._job_queue_mgmt_service`,
  `instance.py:4334`). Alternative precedent: a module-level singleton like
  `_background_process_manager` (`proc_tools.py:1842`) — that is how proc/bash do it
  today, which also keeps factories callable with `None` manager for the loader's
  metadata-scan stubs (`loader.py:76-90`).
- `agent_id` / `version_tag` — only needed for in-tool authz gates
  (precedent: `upgrade_tools` auth gates use `caller_version_tag`-resolved allow
  lists; `instance.py:4593-4595`, `caller_version_tag` `:1829-1833`).
- Placement: construct + `tools.extend(...)` BEFORE `scan_tools_for_full_docs(tools)`
  (`instance.py:4659`) and BEFORE `create_help_tool` (`:4654`); authorization is then
  handled entirely by `_apply_tool_filter` (`:4662`) — no per-tool gate needed for a
  non-privileged category.

**Architecture facts the `service` design must reconcile (from code, not opinion)**
1. Today, ALL spawned processes die with the instance
   (`instance_lifecycle.py:2330-2354`) and at daemon shutdown
   (`manager.py:10915-10952`). A long-lived `service` must NOT be registered into
   those cleanup paths (or must be exempted).
2. Nothing survives a daemon restart today (in-memory registries; documented leak
   `proc_tools.py:1636-1643`, `bash.py:77-79`). Restart-survival requires a persisted
   registry (DB or state file) plus re-attach/verify logic at boot.
3. PID-reuse-safe kill machinery already exists and is reusable
   (`ENSEMBLE_PROC_TRACKING_ID` env-tag + 3-layer `_attempt_kill_signal`,
   `proc_tools.py:114-118`, `:918-975`).
4. If `service` should be opt-in-only (not default-granted), add it to
   `PRIVILEGED_TOOL_CATEGORIES` (`_tool_registry.py:124-128`) — which mandates BOTH
   pin tests in the same PR (Q4 quotes). If default-open, touch nothing there.

---

## New-category registration checklist (ordered, exact)

1. **Create `daemon/tools/service_tools.py`**:
   - module attrs `CATEGORY_NAME` + `CATEGORY_DOC` (pattern `proc_tools.py:66-77`);
   - each tool: `@register_tool_category("service")` OUTER decorator + `@tool`
     INNER (order pinned by `test_attestation_registration.py:128-140`);
   - `_full_doc_` attr per tool (pattern `proc_tools.py:2154-2169`);
   - factory `create_service_tools(<args>)` returning a list, `[]` when no instance
     context (pattern `proc_tools.py:1872-1900`); dereference manager only at CALL
     time so `None`-manager stubs work for the loader warm scan (`loader.py:76-90`).
2. **`daemon/tools/_tool_registry.py`**:
   - add `"service": "daemon.tools.service_tools"` to `CATEGORY_MODULES`
     (`:479-525`);
   - add every tool name to `DYNAMIC_TOOL_NAMES` (`:23-94`).
3. **Regenerate `KNOWN_TOOL_NAMES`**:
   `uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"`
   (command documented `_tool_registry.py:544-548`); paste sorted names into the
   frozenset (`:559-740`). Drift is pinned by
   `test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift`.
4. **Decide privilege**: default-open → do NOT touch `PRIVILEGED_TOOL_CATEGORIES`.
   Opt-in-only → add `"service"` at `_tool_registry.py:124-128` AND update BOTH pins
   in the SAME PR: `tests/unit/tools/test_upgrade_registration.py:105-109` and
   `tests/unit/tools/test_attestation_registration.py:158-162`.
5. **Wire into `daemon/tools/instance.py`**:
   - import next to the factory imports (`:236-241` block);
   - call + extend inside `create_instance_tools()` BEFORE `create_help_tool`
     (`:4654`) and `scan_tools_for_full_docs(tools)` (`:4659`) — pattern
     `:4593-4596` (upgrade) or `:4404-4405` (proc);
   - after the extend, `scan_tools_for_full_docs(tools)` + `_apply_tool_filter(...)`
     already handle docs + authorization (no extra code).
6. **daemon/loader.py warm list** (`_ensure_tool_metadata_populated`, `:27-114`):
   import the factory (`:58-62` block), build a `None`-manager stub
   (`:79-90` pattern), flatten into `all_tools` (`:107-112`) — REQUIRED for non-empty
   cold-boot system-prompt docs.
7. **Cold-boot doc regression test** in `tests/test_loader.py`, following
   `TestMaintenancerToolsDocColdBoot` (`:966-1042`): add the expected
   `CATEGORY_NAME → tool` section to `expected_sections` (`:1036-1042` pattern) with
   the `clear_registry()` cold fixture (`:983-1018`).
8. **Registration source pins** (convention): add an
   `assert "tools.extend(service_tool_list)" in source`-style pin mirroring
   `tests/unit/tools/test_upgrade_registration.py:90-94`, plus decorator-order +
   category-attr survival tests mirroring
   `tests/unit/tools/test_attestation_registration.py:128-140, :174-177`.
9. **Lifecycle decision (service-specific — no precedent for survival)**:
   - If services must die with their instance: add `cleanup_instance` calls to the
     terminate path following steps 2.55/2.56
     (`daemon/services/instance_lifecycle.py:2330-2354`) and to daemon shutdown
     (`daemon/manager.py:10922-10952`).
   - If services must survive instance termination and/or daemon restart (the stated
     goal): do NOT wire those cleanups for the service registry; add persistence
     (DB/state file) + boot-time re-adopt; reuse the PID-ownership kill machinery
     (`proc_tools.py:114-118`, `:228-242`, `:918-975`) rather than raw `killpg`.
10. **Validation walk**: `AgentRegistry.validate_tool_configs` (`daemon/registry.py:1001`)
    must accept every `tools.allow` entry naming `service` — covered by steps 2-3
    (`CATEGORY_MODULES` + `DYNAMIC_TOOL_NAMES` + regenerated `KNOWN_TOOL_NAMES`).

### UNVERIFIED items
- Purpose of `daemon/services/job_feedback_observer.py:2857-2883` consuming both
  process managers (import sites confirmed; behavior not read).
- Exact interior of `stop_process()` (`proc_tools.py`, between `:1206-1536`) — the
  SIGTERM→5s→SIGKILL contract is quoted from `proc_stop` full doc (`:2154-2169`) and
  constants (`:106`, `:109`), and the shared kill helper was read directly, but the
  stop orchestration body itself was not line-read.
