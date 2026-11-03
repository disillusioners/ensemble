# Research — house conventions for a NEW privileged read-only agent tool category

**Lane:** `metrics-agent-tools` (item #2 of the ratified metrics sequence)
**Worktree:** `/home/nea/ensemble-src-wt-metrics-tools` (branch `feature/metrics-agent-tools`)
**Date:** 2026-11-03
**Scope:** read-only investigation of the house pattern for minting a NEW PRIVILEGED,
READ-ONLY tool category (metrics families/snapshot/series) allow-listed to
`maintenancer`, `devops`, `wanderer`.

Every citation below is `file:line` in the worktree at the tip of
`feature/metrics-agent-tools` (base `origin/latest` = `03a2297c3`).

---

## 0. TL;DR — the shape of the change

A new tool category is NOT one edit. The house pattern is a **three-step registration
seam** (decorator → registry entries → construction) plus a **SAME-PR pin fan-out**
(16 assertion sites across 10 test files) plus **per-agent allow entries** plus a
**mandatory in-body `agent_id` gate**.

The single best template for THIS lane is **`wedge-capture`** (v0.27.x) — a READ-ONLY
privileged category with a THREE-holder allow-list (`ari` + `leader` + `maintenancer`).
The second-best is **`instance_read`** (nest-ops) — a READ-ONLY privileged category with
an explicit "a read writes NO audit row" contract.

---

## 1. RQ1 — Privileged category anatomy

### 1.1 Where categories live

| Constant / table | File:line | Purpose |
|---|---|---|
| `PRIVILEGED_TOOL_CATEGORIES: frozenset[str]` | `daemon/tools/_tool_registry.py:357-618` | The default-deny universe. 16 entries today. |
| `CATEGORY_MODULES: dict[str, str \| list[str]]` | `daemon/tools/_tool_registry.py:969-1260` | category key → module path. Drives the AST tool discovery **and** the boot-time allow-list validation. |
| `KNOWN_TOOL_NAMES: frozenset[str]` | `daemon/tools/_tool_registry.py:1262-…` | Static tool-name universe (inventory, not the gate). |
| `DYNAMIC_TOOL_NAMES: frozenset[str]` | `daemon/tools/_tool_registry.py:23-…` | Factory-built (not import-time registered) tool names. |
| `TOOL_REQUIRED_AGENTS: dict[str, list[str]]` | `daemon/tools/_auth.py:35-51` | category → agents a category's tools require. **Only for tier-B agent-backed categories.** |
| `INNATE_SKILL_TOOL_CATEGORIES` | `daemon/tools/instance.py:181-187` | innate-skill → category. A privileged category must NEVER be added here (the expander refuses it — `instance.py:228-229`). |

`daemon/constants.py` holds **NO** tool-category table (verified by grep: only a
prose mention at `daemon/constants.py:4`). Do not look there.

### 1.2 The category id string convention

Two shapes exist in the tree:

* snake_case: `instance_read`, `instance_ops`, `mcp_rpc`, `shared_meta_kv`,
  `doc_maintenance`, `critical_notes`, `project_history`
* **kebab-case (the newest house pattern)**: `self-alarm`, `view-write`, `board-link`,
  `wedge-capture`, `notify-user`, `view-views`, `image-gen`, `ens-env`

The registry states the rule explicitly for the two newest entries:

* `daemon/tools/_tool_registry.py:1060-1065` —
  *"The category key is KEBAB-case (`wedge-capture`) while the tool names are
  snake-case (`wedge_capture_*`) — the house pattern (`self-alarm` → `self_alarm`,
  `board-link` → `board_link`), and the MCP privilege stamping keys off the category
  prefix, so the two must agree."*
* `daemon/tools/_tool_registry.py:1072-1078` — same statement for `notify-user`.

**Recommendation for this lane:** kebab-case category key `metrics-read` (or
`metrics`), snake_case tool names `metrics_families` / `metrics_snapshot` /
`metrics_series`. The key must **byte-match** in all five places it appears:
`@register_tool_category(...)`, `PRIVILEGED_TOOL_CATEGORIES`, `CATEGORY_MODULES`,
the agent `meta.json` allow entry, and the pin tests' expected sets.

### 1.3 How a tool declares its category

Decorator order is **load-bearing and pinned**:

```python
@register_tool_category("wedge-capture")     # ABOVE
@tool(args_schema=WedgeCaptureListInput)     # BELOW
async def wedge_capture_list(...) -> ...:
```

* `register_tool_category` — `daemon/tools/_tool_registry.py:621-646`. It sets
  `func._tool_category = category` **and** `func._tool_category_first_party = True`
  (`:644`) — the provenance marker that stops an MCP server from spoofing a
  category onto a first-party tool (`:638-643`).
* If `@tool` ran first, the wrapper would carry the category but the strip reads the
  WRAPPED object → the privileged strip FAILS OPEN. Pinned at
  `tests/unit/test_notify_user_registration.py:90-115` and
  `tests/unit/test_wedge_capture_registration.py:23-27, 178-201`.
* The category attribute is what the strip reads —
  `daemon/tools/instance.py:7178-7181`:
  `if getattr(t, "_tool_category", None) not in PRIVILEGED_TOOL_CATEGORIES`.
  Without the decorator, the name-prefix fallback
  (`daemon/tools/_tool_registry.py:934-936`) would infer e.g. `"metrics"` from
  `metrics_families` — which may or may not be privileged → **fail-open risk**.
  The `_tool_category` attribute pin exists precisely for this
  (`tests/unit/test_wedge_capture_registration.py:185-201`).

### 1.4 The docs registry entry

The tool module must expose two module-level constants:

```python
CATEGORY_NAME = "Wedge Capture"     # human title
CATEGORY_DOC = """\ ... """         # markdown blurb
```

* `daemon/tools/wedge_capture.py:64-65`
* `daemon/tools/notify_user.py:149-169`
* `daemon/tools/instance_read.py:87-122`
* `daemon/tools/board_tools.py:57-…`

`get_category_doc(category_key)` — `daemon/tools/_tool_registry.py:1636-1661` — imports
the module named by `CATEGORY_MODULES[category_key]` and reads `CATEGORY_NAME` /
`CATEGORY_DOC` off it. It falls back to the category key if the module has no
constants.

Consumers:
* `daemon/loader.py:684-707` — builds the per-agent tools section of the system prompt:
  `## {category_name}\n{category_doc}\n\n**Available tools:** {tools_list}`.
* `daemon/tools/help.py:228` — the `tool_help(category=...)` path.

**No change is needed in `loader.py` or `help.py`** — they read the registry
generically.

### 1.5 How the tool factory is wired into `daemon/manager.py`

`daemon/manager.py:135` — `from .tools import create_instance_tools`. The manager does
NOT construct individual tool categories; the whole assembly lives in
`daemon/tools/instance.py::create_instance_tools`.

The construction seam (three steps):

1. **Import the factory** — `daemon/tools/instance.py:284-298`
   (e.g. `:290 from .wedge_capture import create_wedge_capture_tools`,
   `:291 from .notify_user import create_notify_user_tools`).
2. **Optional `*_if_available` wrapper** — used when the tool depends on a manager
   property that may be absent on a test double:
   `daemon/tools/instance.py:2667-2699` (board), `:2702-2739` (self-alarm),
   `:2742-2774` (notify_user). Returns `[]` instead of raising.
3. **`tools.extend(...)` inside `create_instance_tools`** — the CRITICAL list-append.
   Notify extend: `daemon/tools/instance.py:6617-6621`. Wedge extend: `:6589-6596`.
   Instance read/ops extends: `:6542-6557`.

**PLACEMENT CONSTRAINT (load-bearing):** the extend MUST precede
`create_help_tool(tools…)` (`daemon/tools/instance.py:7133`),
`scan_tools_for_full_docs(tools)` (`:7138`) and
`_apply_tool_filter(tools, agent_id…)` (`:7141`). A later extend is either invisible
to the help tool or **bypasses the privileged strip**. Pinned in every registration
pack (e.g. `tests/unit/test_notify_user_registration.py:146-164`).

### 1.6 How the category reaches the agent-visible surface

Three surfaces, all derived from the same registry:

* **Execution** — `daemon/tools/instance.py:7184-7273` `_apply_tool_filter` →
  `resolve_tool_filter` (`:424-580`).
* **Docs** — `daemon/loader.py:655-709` (system prompt tools section) and
  `daemon/tools/help.py:67-121` (`tool_help`).
* **Allow/deny resolution** — `daemon/registry.py:1355-1417` boot-time validation
  (warns on unknown entries).

### 1.7 The enforcement mechanics (READ THIS BEFORE DESIGNING)

**Two enforcement layers, both mandatory** — the house rule, repeated verbatim in
every privileged module:

1. **Category gate (visibility).** `PRIVILEGED_TOOL_CATEGORIES` membership +
   an explicit `tools.allow` entry naming the category.
2. **In-body `agent_id` gate (authority).** Because *a literal tool name in an allow
   list bypasses the category gate entirely*, the category alone cannot spell the
   audience. Every tool body must call a shared, fail-closed helper.
   * `daemon/tools/instance_read.py:51-60` (the doctrine paragraph)
   * `daemon/tools/instance_read.py:199-212` — `_is_instance_read_authorized`:
     fails closed on blank / non-`str`.
   * `daemon/tools/wedge_capture.py:9-22`, `daemon/tools/notify_user.py:19-30`,
     `daemon/tools/board_tools.py:19-29`.

**The default-deny strip.** Two paths remove privileged tools from the
default-grant (empty-allow) universe:
* `daemon/tools/instance.py:7146-7181` `_strip_privileged_category_tools` — used by
  the two "return-all" paths (`:7214`, `:7250`).
* `daemon/tools/instance.py:550-560` — the `resolve_tool_filter` empty-allow branch.

**The innate-skill second door.** `expand_allow_for_innate_skills`
(`daemon/tools/instance.py:190-235`) refuses to expand into a privileged category
(`:228-229`). A new metrics category must NOT be added to
`INNATE_SKILL_TOOL_CATEGORIES` (`daemon/tools/instance.py:181-187`).

**`_auth.py` row NOT needed** for this lane. `TOOL_REQUIRED_AGENTS`
(`daemon/tools/_auth.py:35-51`) is only for categories whose tools spawn an agent
(`knowledge`, `chart`, `image`, `council`, `design`). A metrics query tool spawns no
agent → no row. (Note: a missing row means the category grants NO implicit
`team_members` — correct for maint/devops/wanderer, which declare their own.)

### 1.8 MCP privilege stamping (relevant only if the tools are also MCP-exposed)

`daemon/mcp/tool_adapter.py:150-198` — a `tool_name_prefix` override is stamped with
its category ONLY if the bare prefix is in BOTH `DYNAMIC_TOOL_PREFIXES` and
`PRIVILEGED_TOOL_CATEGORIES` (`:195-197`). A static category that is NOT in
`DYNAMIC_TOOL_PREFIXES` gets no stamp. **Do not add a `metrics` entry to
`DYNAMIC_TOOL_PREFIXES`** — that namespace is for runtime-discovered toolsets
(`mcp_`, `plane_`) and adding a static category there would make
`resolve_tool_filter`'s dynamic sweep auto-populate it
(`daemon/tools/instance.py:515-548`).

---

## 2. RQ2 — Precedent A: nest-ops (`instance_read` / `instance_ops`, v0.27.1)

### 2.1 The two categories

* `instance_read` — PRIVILEGED entry #13 — `daemon/tools/_tool_registry.py:544-565`
  (literal at `:565`).
* `instance_ops` — PRIVILEGED entry #14 — same block (literal at `:566`).

Module map: `daemon/tools/_tool_registry.py:995-996`.

Why both, and why the split is structural (registry prose `:544-558`):

> * the ACT surface is DESTRUCTIVE (terminate takes a whole cascade tree) …
> * the READ surface is fleet-wide telemetry (every instance in the daemon,
>   cross-project by design) …
> * two categories keep read and act structurally separable: a future read-only
>   grant to another agent is a one-line allow change on a category that
>   structurally CANNOT carry acts.

**This is the house argument for splitting a read category from an act category.**
For the metrics lane there is no act half — a single `metrics-read` category is
structurally read-only by construction (the wedge-capture precedent).

### 2.2 Exact per-tool shape (the `instance_read` template)

`daemon/tools/instance_read.py`:

| Element | File:line |
|---|---|
| Module docstring + "Access posture — TWO layers, BOTH required" | `:1-65` |
| `CATEGORY_NAME = "Instance Read"` | `:87` |
| `CATEGORY_DOC = """…"""` (lists every tool + the measures caveat) | `:88-122` |
| `INSTANCE_READ_AUTHORIZED_AGENTS: frozenset[str] = frozenset({"maintenancer"})` | `:128` |
| Limits/caps as module constants (`MAX_LIST_LIMIT`, `FLEET_SCAN_LIMIT`, `FLEET_SCAN_TIME_BUDGET_SECONDS`, …) | `:131-177` |
| `_is_instance_read_authorized(agent_id)` — ONE shared helper, fail-closed on blank/non-`str` | `:199-212` |
| `_refusal(label, token, message)` — house refusal envelope `Error: {label} REFUSED — reason={token}: {message}` | `:215-221` |
| `_error(msg)` — house error envelope `ERROR: {msg}` | `:224-226` |
| `create_instance_read_tools(manager, *, current_instance_id="", agent_id="", agent_tag=None) -> list` | `:524-548` |
| Per-tool `class InstanceListInput(BaseModel)` args_schema with `Annotated[..., Field(description=…)]` | `:552-588` |
| `@register_tool_category("instance_read")` ABOVE `@tool(args_schema=…)` | `:590-591` |
| One-line docstring `"""Fleet census — … Use tool_help("instance_list") for details."""` | `:597` |
| **Body gate is the FIRST statement** | `:598-599` |
| `tool._full_doc_ = _build_full_doc()` (tool_help surface) | e.g. `daemon/tools/notify_user.py:538`, `daemon/tools/instance.py:6205` |

**Key contract — tools NEVER raise.** Every failure returns a typed refusal string.
`daemon/tools/instance_read.py:62-64`:

> Tools NEVER raise — every failure returns a typed refusal string. The audit ledger
> belongs to the ACT category only: a read has no side effect and writes NO audit row.

### 2.3 The allow-list mechanism ("maintenancer-only")

Three layers, all required:

1. `agents/maintenancer/meta.json:12-22` — `"tools": {"allow": [..., "instance_read",
   "instance_ops", …]}`.
2. `INSTANCE_READ_AUTHORIZED_AGENTS = frozenset({"maintenancer"})`
   (`daemon/tools/instance_read.py:128`) — the in-body gate.
3. The category is in `PRIVILEGED_TOOL_CATEGORIES` (default-deny).

**Per-tool allow is REJECTED for these categories.** `daemon/tools/_tool_registry.py:559-560`:

> Per-tool allow is REJECTED (D-13): a literal tool name in an allow list bypasses the
> category gate outright.

### 2.4 Audit-ledger coupling — DOES A READ-ONLY CATEGORY SKIP IT?

**YES — completely.**

* `daemon/tools/instance_read.py:62-64` — *"The audit ledger belongs to the ACT
  category only: a read has no side effect and writes NO audit row."*
* `daemon/tools/wedge_capture.py:30-32` — *"THE READS HAVE NO SIDE EFFECT — A read
  writes NO audit row, deletes NOTHING, and triggers NOTHING."*

The two-tier ledger (Tier-1 same-transaction vs Tier-2 write-ahead) lives **only in
the act category** (`instance_ops`). For a read-only metrics category: **no audit
table, no migration, no ledger.**

If the metrics lane later wants an audit trail (e.g. "who queried what"), that is a
**new** design decision — it would need a table + migration + the two-tier coupling.
Nothing in the house pattern obliges a read to write.

### 2.5 The tests/pins that hold `instance_read` / `instance_ops` in place

`tests/unit/test_instance_ops_registration.py` (691 lines) — the nest-ops pin pack.
Pins:

| Pin | Line |
|---|---|
| `EXPECTED_PRIVILEGED_SET` (exact set) | `:88-124` |
| AST discovery finds all 11 tools | `:131-136` |
| `CATEGORY_MODULES` entries point at `daemon.tools.*` | `:138-149` |
| `DYNAMIC_TOOL_NAMES` contains all 11 | `:151-153` |
| `KNOWN_TOOL_NAMES` contains all 11 | `:155-157` |
| construction extends greppable in `instance.py` | `:159-170` |
| extends precede `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` | `:172-191` |
| both categories privileged | `:195-196` |
| exact-set equality | `:199-200` |
| `len(PRIVILEGED_TOOL_CATEGORIES) == 16` | `:202-203` |
| maintenancer holds both; ari does NOT; leader does NOT | `:221-234` |
| **no other agent holds either** (`holders == ["maintenancer"]`) | `:236-246` |
| provenance-wiring pins (canonical helper, no local re-implementation) | `:252-303` |

---

## 3. RQ3 — Precedent B: the notify-user pin ceremony (v0.27.8)

### 3.1 What "16-site / 10-file pin ceremony" MECHANICALLY means

The house rule is the **SAME-PR RULE** — "same PR" = the category entry and the pin
bumps ship in the **same commit**. The set must never be red between two commits.

Registry prose: `daemon/tools/_tool_registry.py:314-356`. Key lines:

> `:314-321` — the set is pinned by exact-equality asserts at **TEN sites** — five
> inline `frozenset({...})` literals + five `EXPECTED_PRIVILEGED_SET` constants —
> plus a `len(PRIVILEGED_TOOL_CATEGORIES) == N` count assertion at **FIVE** phase-local
> pins — plus **ONE** allow-array TRAILING-ENTRY pin … **SIXTEEN assertion sites
> across TEN files**.
>
> `:347-351` — **DERIVE THE PIN SET BY GREPPING THE TREE — do NOT trust this prose:**
> ```
> grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ --include=*.py
> grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ --include=*.py
> ```
> Silent additions and silent removals (fail-open regressions) both trip the pins.

### 3.2 The exhaustive pin-site list (grep-verified at this tip)

**Exact-set pins — 5 files with an `EXPECTED_PRIVILEGED_SET` constant:**

| File | Constant | Assertion |
|---|---|---|
| `tests/unit/test_board_registration.py` | `:70` | `:201` |
| `tests/unit/test_instance_ops_registration.py` | `:88` | `:200` |
| `tests/unit/test_notify_user_registration.py` | `:54` | `:172` |
| `tests/unit/test_self_alarm_registration.py` | `:76` | `:209` |
| `tests/unit/test_wedge_capture_registration.py` | `:70` | `:169` |

**Exact-set pins — 5 files with an inline `frozenset({...})` literal:**

| File | Line |
|---|---|
| `tests/unit/tools/test_upgrade_registration.py` | `:149` |
| `tests/unit/tools/test_attestation_registration.py` | `:192` |
| `tests/unit/tools/test_secrets_registration.py` | `:226` |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py` | `:369` |
| `tests/integration/test_service_tool_flag_off_byte_identical.py` | `:832` |

**Count pins — `len(PRIVILEGED_TOOL_CATEGORIES) == 16` (must become 17):**

| File | Line |
|---|---|
| `tests/unit/test_board_registration.py` | `:215` |
| `tests/unit/test_instance_ops_registration.py` | `:203` |
| `tests/unit/test_notify_user_registration.py` | `:175` |
| `tests/unit/test_self_alarm_registration.py` | `:212` |
| `tests/unit/test_wedge_capture_registration.py` | `:172` |

**Allow-array trailing-entry pin (1 site, 1 file):**

`tests/unit/test_board_registration.py:249-287` — asserts that in `ari`'s allow array
only the categories appended **after** `board` may follow it. The pinned trailing set
(`:278`):

```python
"ari": ["self-alarm", "view-write", "board-link", "mcp_rpc", "wedge-capture", "notify-user"],
```

This pin only binds `ari`'s array. If the metrics category is NOT granted to `ari`,
this pin is untouched. (The lane's holders are maintenancer / devops / wanderer — so
this site likely needs **no** change; but re-read it: the pin asserts the trailing set
for `ari` only, and `BOARD_ALLOW_AGENTS` is ari-only. **No change needed.**)

**Total: 10 files, 16 assertion sites.** A new category must add its key to all
10 expected sets and bump the 5 count assertions 16 → 17 — **in the same commit**.

### 3.3 What an "exact-set pin" actually asserts

It asserts **the entire membership of `PRIVILEGED_TOOL_CATEGORIES`** — i.e. it is a
*silent-addition / silent-removal* guard, not a per-category guard. Adding or removing
ANY category trips all 10 files. That is the point: a category added without its
allow-entry / body-gate / construction wiring shows up as a **set diff**.

### 3.4 The per-category registration pack shape (the "check-list a NEW category must reproduce")

`tests/unit/test_notify_user_registration.py` (239 lines) is the canonical template.
Its docstring (`:1-32`) enumerates the ten legs:

| Leg | What it asserts | Notify file:line | Wedge file:line |
|---|---|---|---|
| (a) | AST discovery finds the tool in the category module | `:83-88` | `:99-108` |
| (b) | the category is in `PRIVILEGED_TOOL_CATEGORIES` | `:168-169` | `:165-166` |
| (c) | `len(PRIVILEGED_TOOL_CATEGORIES) == 16` | `:174-175` | `:171-172` |
| (d) | the set is EXACTLY the 16 entries | `:171-172` | `:168-169` |
| (e) | `CATEGORY_MODULES[key]` resolves to a `daemon.tools.*` path | `:117-125` | `:110-120` |
| (f) | `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` carry the tool names | `:127-133` | `:122-128` |
| (g) | the `create_instance_tools` extend is greppable in `instance.py` | `:135-144` | `:130-138` |
| (g') | the extend PRECEDES `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` | `:146-164` | `:140-158` |
| (h) | the holder census — the key is in the named agents' `meta.json` and NOWHERE ELSE | `:181-210` | `:252-275` |
| (i) | the in-body runtime gate constant exists | `:212-222` | `:226-246` |
| (j) | the decorator order is `("register_tool_category", "tool")` | `:90-115` | — |

Plus, in the wedge pack only (the **strongest** template):

| Leg | What it asserts | Wedge file:line |
|---|---|---|
| (g-A13) | **attribute pin** — `getattr(tool, "_tool_category", None) == "wedge-capture"` on the CONSTRUCTED tool objects (without it the name-prefix fallback infers `"wedge"` and the strip FAILS OPEN) | `:178-201` |
| (h-A13) | **default-deny behavioural leg** — an empty-allow synthetic agent resolves ZERO tools of the category (`_apply_tool_filter` → `[]`) | `:203-220` |
| (i-A13) | **fail-closed helper** — `_is_*_authorized("")`, `(None)`, `(123)` all `False` | `:226-232` |
| (j-A13) | the helper admits EXACTLY the holder set | `:234-246` |

The notify pack also pins the **gate ordering** (`:226-239`): the body gate must
precede the kill-switch, which must precede the size gate.

### 3.5 The other pack files for the same category

* `tests/unit/test_notify_user_tool.py` — behavioural unit surface.
* `tests/unit/test_notify_user_registration.py` — registration pack (above).
* `tests/integration/test_notify_user_boot_smoke.py` + `tests/support/notify_smoke.py`
  — opt-in boot smoke (`ENSEMBLE_NOTIFY_SMOKE=1`).
* `test/packs/notify_user_unit_test.sh`, `test/packs/notify_user_mutation_legs.sh`,
  `test/packs/notify_user_boot_smoke_test.sh` — shell packs registered in
  `.agents/tester/PACKS.md:2320-2322`.
* The **frozen tool-name drift pin**:
  `tests/unit/tools/test_frozen_tool_name_discovery.py` — bidirectional
  source-vs-`KNOWN_TOOL_NAMES` drift. Named at
  `daemon/tools/_tool_registry.py:1285-1288` (*"the alias — MUST land in the SAME
  commit as the closure, or the bidirectional drift pin fails in one direction or
  the other"*).

---

## 4. RQ4 — Allow-list wiring mechanics

### 4.1 The JSON key and the value form

`agents/<agent>/meta.json`:

```json
"tools": {
  "allow": ["<category-or-tool-name>", ...],
  "deny":  ["<category-or-tool-name>", ...]
}
```

The list mixes **category ids** and **literal tool names** freely. The resolver
(`daemon/tools/instance.py:561-568`) treats each entry as: *if the entry is a known
category → expand to its tool names; else → treat it as a literal tool name.*

### 4.2 `agents/maintenancer/meta.json` (26 lines)

```json
"innate_skills": ["dynamic-skill", "todo", "chart"],
"skill_injection": true,
"no_force_explore": true,
"tools": {
  "allow": ["system-log", "ens-db", "knowledge", "system_upgrade", "system_restart",
            "db", "instance_read", "instance_ops", "wedge-capture"],
  "deny": ["git_commit", "edit_file", "write_file"]
},
"team_members": ["explorer", "worker", "coder"]
```

* Holds 9 categories: `system-log`, `ens-db`, `knowledge`, `system_upgrade`,
  `db`, `instance_read`, `instance_ops`, `wedge-capture` — plus one **literal tool
  name**, `system_restart` (there is no `system_restart` category key; the
  `system_upgrade` category is a real key —
  `daemon/tools/_tool_registry.py:1151`).
* Deny: `git_commit`, `edit_file`, `write_file`.
* `team_members`: `explorer`, `worker`, `coder`. `skill_injection: true`.
* **No `llm_model` override; no vision/leaf flags.**
* Note the **array-order discipline**: the maintainer's array is NOT under the
  board trailing-entry pin (that pin is ari-only), but the house still appends new
  categories at the END.

### 4.3 `agents/devops/meta.json` (15 lines)

```json
"innate_skills": ["dynamic-skill", "chart", "todo"],
"skill_injection": false,
"capabilities": ["docker", "kubernetes", "terraform", "ci-cd", "shell-ops"],
"tools": {
  "allow": ["bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge",
            "mcp", "context", "shared_meta_kv", "db", "infra", "project",
            "dynamic-skill", "question", "midflight", "service"]
},
"team_members": ["explorer"]
```

* Holds 17 categories. **No `deny` key at all.**
* `skill_injection: false`; `no_force_explore` absent; no `llm_model` override.
* `team_members`: `explorer` only.
* Note `db` here is the **user-registered external DB** category — distinct from the
  maintainer's `ens-db` (the daemon's own DB).

### 4.4 `agents/wanderer/meta.json` (20 lines)

```json
"innate_skills": ["todo", "chart", "dynamic-skill"],
"skill_injection": false,
"team_members": ["explorer", "worker"],
"tools": {
  "allow": ["bash", "proc", "filesystem", "time", "self", "help", "explore", "mcp",
            "context", "shared_meta_kv", "rag", "instance", "blueprint", "db",
            "infra", "service"]
},
"context_injection": {"heuristic_match_shared_md_files": true},
"lifecycle_hooks": {"on_complete": ["add_to_shared_context_md_files"]}
```

* Holds 16 entries. **No `deny` key.**
* `skill_injection: false`; `team_members`: `explorer`, `worker`.
* `agents/wanderer/tools_note.md` (22 lines) has **no category table** — it is a
  prose note about delegating log forensics to the maintenancer. A metrics category
  would need a new section there (or no change — but the house pins agent-visible
  tool surfaces in `tools_note.md`).

### 4.5 The resolver — exactly how a category id matches

1. `daemon/registry.py:1355-1368` — boot-time: `available_categories =
   list_tools_by_category()`; `all_tool_names` = category tools + `DYNAMIC_TOOL_NAMES`
   + `discover_all_tool_names()` (AST); `known_categories = set(CATEGORY_MODULES.keys())`.
2. `daemon/registry.py:1385-1394` — an allow entry that is neither a known category,
   a known tool, nor a `DYNAMIC_TOOL_PREFIXES` prefix ⇒ boot **WARNING**
   *"allow entry '<x>' is neither a known category nor a known tool"*. Same for deny
   (`:1397-1406`). Same for a zero-tool result (`:1408-1417`).
3. `daemon/tools/instance.py:7237-7245` — `_apply_tool_filter`:
   `expand_allow_for_innate_skills(allow, innate_skills)` → `resolve_tool_filter(...)`.
4. `daemon/tools/instance.py:561-568` — expansion: `if item in tool_categories: expand;
   else: literal tool name`.
5. `daemon/tools/instance.py:570-578` — deny wins conflicts.

**The category must be in `CATEGORY_MODULES`** for the allow entry to resolve — that
is what makes it a "known category" at boot.

### 4.6 Category id or tool name — the answer

**Both forms are accepted by the resolver, but the house rule for a NEW privileged
category is: put the CATEGORY ID in the allow-set — and never the tool names.**

Why:

* A **literal tool name** in an allow list **bypasses the category gate entirely**
  (`daemon/tools/instance.py:565-568` — the else-branch adds the name directly; the
  privileged strip only applies on the empty-allow path). Stated in every privileged
  module: `daemon/tools/_tool_registry.py:457-459`, `476-479`, `500-503`,
  `519-521`, `537-539`, `559-560`, `580-582`, `608-610`;
  `daemon/tools/instance_read.py:55-58`.
* The nest-ops ruling explicitly **rejects** per-tool allow (D-13) —
  `daemon/tools/_tool_registry.py:559-560`.
* Literal tool names in allow-sets exist only for legacy/impersonal tools
  (e.g. `agents/ari/meta.json:24-30` — `job_messages`, `job_tree`, `job_progress`,
  `job_inject`, `pause_instance`, `resume_instance`).

⇒ **New category ⇒ one category id per agent's `tools.allow`; the in-body
`agent_id` gate carries the audience.**

---

## 5. RQ5 — Wanderer read-only posture

### 5.1 Does wanderer hold ANY mutating tools? — YES, MANY

`agents/wanderer/meta.json:12` allow list:

| Entry | Mutating? |
|---|---|
| `bash` | **YES** — arbitrary shell (write, network, processes). |
| `proc` | **YES** — `proc_run` spawns processes. |
| `filesystem` | **YES** — includes `write_file` / `edit_file`. |
| `db` | **YES** — `db_conn_add` / `db_conn_delete` / `db_postgres_dml_select` (DML). |
| `infra` | **YES** — KMS + `mcp_set_env`. |
| `service` | **YES** — `service_start` / `service_stop` spawn & kill persistent OS processes. |
| `shared_meta_kv` | **YES** — it upserts KV rows. |
| `self` | **YES** — inner_soul writes the agent's own soul file. |
| `rag` | **YES** — `rag_insert_text`, `rag_create_entity`, `rag_delete_docs`, … |
| `instance` | mixed — `pause_instance` / `resume_instance` are mutating. |
| `time`, `help`, `explore`, `mcp`, `context`, `blueprint` | read-mostly (`blueprint` can `blueprint_update`). |

**Verdict:** wanderer is *described* as a "Read-only investigation and research
specialist" (`agents/wanderer/meta.json:4`) but its **tool posture is NOT
read-only**. There is no `deny` key.

### 5.2 Is there a read-only allow-set precedent anywhere in the repo?

**No agent in the tree has an exclusively-read allow-set.** The three closest:

1. **`agents/critic/meta.json:12-17`** — the strongest precedent:
   ```json
   "allow": ["read_file", "image", "design"],
   "deny":  ["bash", "proc", "instance", "service", "midflight",
             "shared_meta_kv", "infra", "mcp", "image_save"]
   ```
   * `llm_model: "vision"`, `skill_injection: true`, `recursion_limit_multiplier: 7`,
     `team_members: []` (leaf).
   * This is the ONLY agent in the tree that achieves read-only posture by an
     explicit **deny-list** of the mutating categories. Note it still holds
     `read_file` as a **literal tool name**, not the `filesystem` category —
     precisely so it cannot reach `write_file`/`edit_file`.
   * Blueprint note: "Designer Agent & OD Plugin Integration" documents critic as
     *"leaf, read-only, resolved set `{read_file, image_get, image_list,
     explain_image, compare_images}`, image_save denied, vision lane"*.

2. **`agents/image-reader/meta.json:13`** —
   `"allow": ["time", "self", "help", "knowledge", "mcp", "context"]` — no `bash`,
   no `filesystem`, no `db`, no `infra`, no `service`. The closest thing to a
   read-only set, but `self` (inner_soul) and `shared_meta_kv`-adjacent writes
   remain possible; `mcp`/`context` are permissive surfaces.

3. **`agents/watcher/meta.json:22`** — `"allow": []` — the reference "empty-allow
   inherit universe". It is the agent every privileged-category comment cites as
   the population that must NEVER auto-grant. Its `tools.allow` is empty and the
   privileged strip guarantees it gets zero privileged tools
   (`daemon/tools/instance.py:7146-7181`).

(Adjacent, weaker: `agents/explorer/meta.json:11` and `agents/experiencer/meta.json:11`
— `rag`/`filesystem`/`blueprint`, so not read-only.)

### 5.3 Implication for this lane

* The **category** is the unit of grant, not the agent's whole allow-set. A single
  read-only `metrics-read` category can be granted identically to
  `maintenancer`, `devops`, `wanderer` **without** making any of them read-only —
  the metrics category itself carries no write authority.
* Because wanderer already holds `bash`/`proc`/`filesystem`/`db`/`infra`/`service`,
  granting it a read-only metrics category does not narrow anything; it also does
  not widen anything **if** the category is structurally read-only.
* If the lane wants wanderer to be *narrower* than maintenancer/devops, the
  mechanism is the **critic pattern**: a distinct category (or a per-agent
  `tools.deny` list), because a category's audience is not per-agent tunable
  beyond the allow-entry. The `wedge-capture` precedent shows a **single category
  with a 3-agent allow-list and a 3-agent body gate** — that is the pattern for
  "same category, several holders".
* **Recommendation:** ONE `metrics-read` category, allow-listed to the three agents,
  with a body gate of exactly `{maintenancer, devops, wanderer}` — the wedge-capture
  shape. No per-agent narrowing is needed unless the lane decides the metrics
  surface exposes daemon-internal telemetry that only the maintenancer should see.

---

## 6. RQ6 — Alert / wake / delivery surface census (design-only outline)

### 6.1 `self_alarm` — the ari-only alarm arm (`daemon/tools/self_alarm.py`)

**What it does.** Arms a reminder that fires **into Ari** at least 30 minutes out; the
fired instance then executes the message text **verbatim**
(`daemon/tools/self_alarm.py:1-53`, `:618-625`). One tool (`self_alarm`), one
category (`self-alarm`).

**Gating.** `self-alarm` is PRIVILEGED entry #9 (`daemon/tools/_tool_registry.py:465-486`,
literal at `:486`); the ari allow entry is `agents/ari/meta.json:39`; the body carries
an `agent_id == "ari"` runtime gate (`daemon/tools/instance.py:2715-2721`).

**Horizon floor.** `MIN_HORIZON_SECONDS = 1800`
(`daemon/services/self_alarm_service.py:77`), enforced by `enforce_min_horizon`
(`:550-590`) — a sub-30-minute alarm is refused, and a cron with a period ≤ 30 min
can never satisfy it (`:280-281`).

**Label namespace.** `LABEL_NAMESPACE = "self-alarm:"`
(`daemon/services/self_alarm_service.py:67`), auto-prefixed by
`validate_label_input` (`:694-708`); a caller-supplied `self-alarm:` prefix is
rejected. The namespace exists because the label lookup filters only on the NAME, so
an unprefixed label could collide with a user schedule
(`daemon/tools/self_alarm.py:618-625`).

**Wake-the-caller.** `SELF_ALARM_INSTANCE_MODE = "reuse_instance"`
(`daemon/tools/self_alarm.py:143`) — the schedule row is bound to the caller's
instance (the *"bind schedule to caller"* seam), and the wake lands back INTO that
session rather than spawning a fresh one. The mapper's E1 fire-time refusal
(`daemon/sources/mapper.py:422-460`) enforces the corollary: a mapping bound to a
**confirmed-gone** caller must NOT be re-targeted at a stranger instance — it raises
`BoundCallerRefusal` and keeps the mapping intact. Note the comment at
`daemon/sources/mapper.py:430-432`: `source_type` is unusable as the signal here
because **every self-alarm row is `source_type="scheduler"`**.

**Owner.** `ari` exclusively.

**Generic vs metrics-specific.** The *mechanism* (arm → bind → fire into the bound
caller → durable delivery if the caller is busy) is generic. The **category and the
target agent are hardcoded to ari** (`SELF_ALARM_TARGET_AGENT`), so a metrics watch
for `maintenancer` / `devops` / `wanderer` **cannot** reuse `self_alarm` as-is — it
needs either a new agent-parameterised lane or a different transport.

### 6.2 Scheduler deferred / coalesced delivery (v0.27.3)

**What it does.** Fixes a silent-drop defect: *"a scheduled fire at a BUSY caller was
SILENTLY DROPPED (no JobItem / Task / MessageQueue row / audit row) and a one-shot was
still CONSUMED — the alarm was permanently lost with zero trace"*
(`daemon/sources/adapters/scheduler.py:1115-1118`). The fix: the fire becomes a
**DURABLE PENDING delivery** to the pinned caller (a PENDING Task + a MessageQueue row
+ a JobItem), the fire is recorded in `schedule_executions` as `deferred`, and
repeated fires **COALESCE** into that ONE pending delivery — *at-most-once* is the
restored invariant (`daemon/sources/adapters/scheduler.py:1120-1124`).

**Mechanics / vocabulary.**
* `_DEFERRED_EXECUTION_ID_PREFIX = "deferred-"` (`daemon/sources/adapters/scheduler.py:120`).
* Coalesce outcome vocabulary: `_COALESCE_COALESCED` / `_COALESCE_ROLLOVER` /
  `_COALESCE_SKIPPED` / `_COALESCE_WRITE_UNKNOWN`
  (`daemon/sources/adapters/scheduler.py:126-136`). `write_unknown` must never be
  treated as `rollover` (which would authorise a fresh enqueue).
* Fire outcome `FIRE_OUTCOME_DEFERRED_DURABLE = "deferred_durable"`
  (`daemon/sources/adapters/scheduler.py:243`); `deferred_durable` /
  `refused_permanent` consume (`:238-254`).
* Bounds: `_defer_bounds()` reads `SchedulingConfig.defer_max_count` /
  `defer_max_age_seconds` (defaults **16 / 86400**), from pydantic-settings knobs,
  NOT DDL — the migration delta stays EMPTY
  (`daemon/sources/adapters/scheduler.py:1127-1145`).
* Episode carrier in the schedule row's `config` JSON: `_defer_gen`,
  `_defer_message_id`, `_defer_count`, `_defer_since`
  (`daemon/sources/adapters/scheduler.py:1147-1163`).
* Visibility surface: `daemon/services/scheduling_service.py:202-213` (the execution
  detail model carries `deferred_count`; the docstring notes `deferred` +
  `deferred_count == 0` means DIRECT dispatch), `:629-698`
  (`_last_execution_status` keeps reading `deferred` until the next completion;
  `_row_to_detail` reads `deferred_count` off the config JSON).
* Message-queue side: the coalescing write target is the message-queue repository
  (`daemon/sources/adapters/scheduler.py:1228-1230`).

**Owner.** No agent owner — daemon-level transport inside the scheduler source
adapter.

**Generic vs metrics-specific.** **Fully generic.** It is exactly the "the target is
busy → deliver later → coalesce the repeats" primitive a metrics watch needs, and it
is agent-agnostic (the pinned caller is a schedule row, not an agent).

### 6.3 `notify_user` — the ari-only deliberate chat voice (`daemon/tools/notify_user.py`)

**What it does.** ONE deliberate message from a **non-user-originated turn** (self-alarm
wake, scheduled fire) to the **SESSION'S OWN bound registered chat source**
(Discord/Slack/Telegram). There is **no destination parameter** — the destination is
resolved server-side from the session's `original_source`, so an arbitrary destination
is structurally impossible (`daemon/tools/notify_user.py:1-11`).

**Gating (three layers).** `notify-user` is PRIVILEGED entry #16
(`daemon/tools/_tool_registry.py:592-617`, literal at `:617`); the ari allow entry is
`agents/ari/meta.json:44`; the body gate is
`if agent_id != NOTIFY_AUTHORIZED_AGENT:` where
`NOTIFY_AUTHORIZED_AGENT = "ari"` (`daemon/tools/notify_user.py:180`), and it must be
the **first** check (pinned `tests/unit/test_notify_user_registration.py:226-239`).

**Bounds.** N = 1 per turn, enforced as a **≥60 s minimum interval per calling
instance**; M = **6 per rolling hour per RESOLVED DESTINATION**, where
`target_hash = sha256(source_id + ":" + external_user_id)`
(`daemon/tools/notify_user.py:74-97`). 2000-char cap
(`NOTIFY_SIZE_LIMIT`, `:240`). ONE **10 s** never-block bound
(`NOTIFY_SEND_TIMEOUT_SECONDS`, `:243`) — every path returns within it; on timeout
the delivery status is UNKNOWN and is never blindly re-sent (`:52-64`).

**Ledger.** `notify_audit` — body stored as **sha256 + length, never cleartext**
(`:66-72`, `:455-457`); commit-before-count under a per-destination `asyncio.Lock`
held across the whole gate (`:82-90`); **fail-closed** — a ledger-write failure
yields `notify_audit_unavailable` and sends nothing (`:95-97`); a 12-code frozen
error vocabulary, validated loudly at import (`:182-235`); a 120 s orphan-sweep floor
(`:259-266`).

**Doctrine.** *"The board is the ALWAYS surface; notify is the MAY voice"* —
`daemon/tools/notify_user.py:32-39`.

**Owner.** `ari` exclusively (both the category and the body gate).

**Generic vs metrics-specific.** The *mechanism* (ledgered, rate-bounded,
source-resolved, never-automatic chat voice) is generic. The **category and the body
gate are ari-EXACTLY** — a metrics lane for maintenancer/devops/wanderer cannot use
it without a new lane (or a per-agent re-gating decision).

### 6.4 Board tools — the ari-only always-surface

**What they do.** `daemon/tools/board_tools.py:1-38` — four tools
(`board_create` / `board_update` / `board_list` / `board_get`) over three tables
(`board_items` / `board_notes` / `board_item_tags`), a **thin closure layer** over
`daemon.services.board_service.BoardService` — the tools never write to the DB
directly (`:6`). The board is **ONE GLOBAL namespace** shared by every Ari instance
(`daemon/tools/_tool_registry.py:442-464`).

**Gating.** `board` is PRIVILEGED entry #8 (`:442-464`, literal `:464`); ari allow
entry `agents/ari/meta.json:38`; body gate `_is_board_authorized`
(`daemon/tools/board_tools.py:24-29`).
`board_link` (`daemon/tools/board_link.py`) is PRIVILEGED entry #11
(`daemon/tools/_tool_registry.py:509-528`) — it mints the passcode row for the
server-rendered board at `/views/public/board/index.html`; minting authorises reading
the user's whole work board, hence ari-only.

**Owner.** `ari` exclusively.

**Generic vs metrics-specific.** The board's *pattern* (durable, global, shared
evidence surface) is generic and is the house's designated fallback when a chat
voice is not warranted. But the namespace is ari-owned and global; a metrics lane for
other agents would need a distinct surface (or the board could be opened to them —
an explicit user decision, not a default).

### 6.5 Watchover service (`daemon/services/watchover_service.py`)

**What it does.** Activation / deactivation of the **watchover** lane: an LLM
evaluator (the `watcher` agent, `agents/watcher/meta.json:3-4`) that **intercepts
every tool call** from a watched instance *before execution* and returns Allow / Deny
based on verb classification and target sensitivity. `WatchoverService` is the class
(`daemon/services/watchover_service.py:236`); `activate_watchover` /
`deactivate_watchover` are the entry points (`:12-18`).

**Gating.** The pause-first-then-quiesce convention: `pause_instance_cascade` FIRST,
then quiescence confirmation, then the state mutation, then resume
(`daemon/services/watchover_service.py:12-17`, `:554-568`); the watchover context is
built from the instance's recent raw tail (`:396-403`, `_format_raw_tail` at `:81`);
the per-agent knobs live in the `watchover` block of the agent's `meta.json`
(`agents/watcher/meta.json:25-37`).

**Owner.** The `watcher` agent — a **tools-less** evaluator (`allow: []`).

**Generic vs metrics-specific.** **Not reusable for a metrics watch.** It is a
tool-call *interceptor*, not a scheduler/alarm/delivery channel. Its generic value
is only the pause→quiesce→resume choreography, which the metrics lane may need if
it ever has to mutate daemon state while an instance is running.

### 6.6 Census summary

| Surface | Category | Owner | Mutating? | Generic for a metrics watch? |
|---|---|---|---|---|
| `self_alarm` | `self-alarm` (privileged #9) | ari | arms a schedule row | Mechanism generic; **target hardcoded to ari** |
| scheduler deferred/coalesce | (no category — transport) | daemon | writes Task/MQ/JobItem rows | **YES — fully generic** |
| `notify_user` | `notify-user` (privileged #16) | ari | writes to the user's chat | Mechanism generic; **category + gate ari-EXACTLY** |
| `board_*` / `board_link` | `board` (#8), `board-link` (#11) | ari | writes board rows / mints passcode | Pattern generic; **namespace ari-owned + global** |
| watchover | (no category — service) | watcher | activates a watcher on an instance | **NO — it is an interceptor, not a delivery channel** |

**Design implication for the metrics lane:** the only *immediately* reusable
delivery primitive is the **scheduler deferred/coalesced delivery** path
(`daemon/sources/adapters/scheduler.py:1111-1125` + `daemon/sources/mapper.py:422-460`),
because it is agent-agnostic and already durable + at-most-once. The ari-owned
surfaces (`self_alarm`, `notify_user`, `board_*`) would each need a per-agent
re-gating lane.

---

## 7. MASTER PHASE CHECKLIST — every file site a new privileged read-only category touches

### Phase A — the tool module (NEW FILE, e.g. `daemon/tools/metrics_read.py`)

| # | Item | Template file:line |
|---|---|---|
| A1 | Module docstring with the "Access posture — TWO layers, BOTH required" doctrine + "tools never raise" | `daemon/tools/wedge_capture.py:1-40` |
| A2 | `CATEGORY_NAME = "…"` + `CATEGORY_DOC = """…"""` (list every tool) | `daemon/tools/wedge_capture.py:64-65`; `daemon/tools/instance_read.py:87-122` |
| A3 | `METRICS_CATEGORY_KEY = "metrics-read"` (kebab, byte-matched) | `daemon/tools/notify_user.py:171-173` |
| A4 | `METRICS_AUTHORIZED_AGENTS = frozenset({"maintenancer","devops","wanderer"})` | `daemon/tools/instance_read.py:128`; `daemon/tools/wedge_capture.py` `WEDGE_CAPTURE_AUTHORIZED_AGENTS` |
| A5 | `_is_metrics_authorized(agent_id)` — fail-closed on blank/non-`str` | `daemon/tools/instance_read.py:199-212` |
| A6 | `_refusal(...)` / `_error(...)` house envelopes | `daemon/tools/instance_read.py:215-226` |
| A7 | `create_metrics_read_tools(manager, *, current_instance_id="", agent_id="", agent_tag=None) -> list` | `daemon/tools/instance_read.py:524-548` |
| A8 | per-tool `class XInput(BaseModel)` args_schema | `daemon/tools/instance_read.py:552-588` |
| A9 | `@register_tool_category("metrics-read")` ABOVE `@tool(args_schema=…)` | `daemon/tools/instance_read.py:590-591` |
| A10 | one-line tool docstring + `tool._full_doc_ = _build_full_doc()` | `daemon/tools/instance_read.py:597`; `daemon/tools/notify_user.py:538` |
| A11 | **body gate is the FIRST statement** in every tool body | `daemon/tools/instance_read.py:598-599` |
| A12 | caps as MODULE constants (NOT `daemon/constants.py` — sealed) | `daemon/tools/instance_read.py:131-177`; precedent `daemon/tools/_tool_registry.py:1099-1100` |

### Phase B — `daemon/tools/_tool_registry.py` (4 edits)

| # | Item | Line |
|---|---|---|
| B1 | `PRIVILEGED_TOOL_CATEGORIES` — append `"metrics-read"` at the END of the literal + a doctrine comment block | set literal `:357-618`; append after `:617` |
| B2 | `CATEGORY_MODULES["metrics-read"] = "daemon.tools.metrics_read"` — MUST be a `daemon.tools.*` path | map `:969-1260`; model on `:1079` |
| B3 | `DYNAMIC_TOOL_NAMES` — add each factory-built tool name | `:23-…`; model on `:141-150` |
| B4 | `KNOWN_TOOL_NAMES` — add each tool name | `:1262-…`; model on `:1339-1347` |

**DO NOT** add the category to `DYNAMIC_TOOL_PREFIXES` (`daemon/tools/_tool_registry.py:279`),
to `INNATE_SKILL_TOOL_CATEGORIES` (`daemon/tools/instance.py:181-187`), or to
`TOOL_REQUIRED_AGENTS` (`daemon/tools/_auth.py:35-51`) — none of the three apply to a
non-agent-backed, non-innate read category.

### Phase C — `daemon/tools/instance.py` (2-3 edits)

| # | Item | Line |
|---|---|---|
| C1 | import the factory | imports block `:284-298` |
| C2 | optional `create_metrics_read_tools_if_available(...)` wrapper if the tool needs a manager property | model on `:2742-2774` |
| C3 | **`tools.extend(...)` INSIDE `create_instance_tools`** — MUST be before `:7133` (`create_help_tool`), `:7138` (`scan_tools_for_full_docs`), `:7141` (`_apply_tool_filter`). The notify extend sits at `:6617-6621`; place the metrics extend there or earlier. | `:6598-6621` |

### Phase D — agent metas (3 edits) + tools_note (3 edits)

| # | File | Edit |
|---|---|---|
| D1 | `agents/maintenancer/meta.json` | append `"metrics-read"` to `tools.allow` (`:12-22`) |
| D2 | `agents/devops/meta.json` | append `"metrics-read"` to `tools.allow` (`:12`) |
| D3 | `agents/wanderer/meta.json` | append `"metrics-read"` to `tools.allow` (`:12`) |
| D4 | `agents/maintenancer/tools_note.md` | add a row to the "I hold directly" table (`:7-16`) + a section |
| D5 | `agents/devops/tools_note.md` | add a section (394-line file, prose/shape per category) |
| D6 | `agents/wanderer/tools_note.md` | add a section (currently 22 lines, prose only — no category table) |

**Array-order discipline:** append the new category at the END of each allow array.
The **board trailing-entry pin** (`tests/unit/test_board_registration.py:249-287`)
binds only `ari`'s array — unaffected by this lane.

### Phase E — the pin fan-out (10 files, 16 assertion sites) — SAME COMMIT as Phase B

| # | File | Edit | Line |
|---|---|---|---|
| E1 | `tests/unit/test_board_registration.py` | `EXPECTED_PRIVILEGED_SET` + count 16→17 | `:70`, `:201`, `:215` |
| E2 | `tests/unit/test_instance_ops_registration.py` | same | `:88`, `:200`, `:203` |
| E3 | `tests/unit/test_notify_user_registration.py` | same | `:54`, `:172`, `:175` |
| E4 | `tests/unit/test_self_alarm_registration.py` | same | `:76`, `:209`, `:212` |
| E5 | `tests/unit/test_wedge_capture_registration.py` | same | `:70`, `:169`, `:172` |
| E6 | `tests/unit/tools/test_upgrade_registration.py` | inline `frozenset({...})` | `:149` |
| E7 | `tests/unit/tools/test_attestation_registration.py` | inline `frozenset({...})` | `:192` |
| E8 | `tests/unit/tools/test_secrets_registration.py` | inline `frozenset({...})` | `:226` |
| E9 | `tests/integration/test_maintenancer_spawn_resolves_tools.py` | inline `frozenset({...})` | `:369` |
| E10 | `tests/integration/test_service_tool_flag_off_byte_identical.py` | inline `frozenset({...})` | `:832` |

Re-derive the set by grepping, never from prose:
```
grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ --include=*.py
grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ --include=*.py
```

### Phase F — the NEW registration pack (1 new file)

`tests/unit/test_metrics_read_registration.py` — model on
`tests/unit/test_wedge_capture_registration.py` (303 lines) + the notify pack's
gate-ordering pin. Required legs:

1. AST discovery finds every tool in the category module.
2. `CATEGORY_MODULES["metrics-read"]` == `"daemon.tools.metrics_read"` and starts with
   `daemon.tools.`.
3. `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` carry every tool name.
4. The construction extend is greppable in `instance.py` AND precedes
   `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter`.
5. Decorator order is `("register_tool_category", "tool")`.
6. The category is in `PRIVILEGED_TOOL_CATEGORIES`; the set is exactly 17; the count
   is 17.
7. **Attribute pin** — `getattr(tool, "_tool_category", None) == "metrics-read"` on
   the CONSTRUCTED tool objects (`test_wedge_capture_registration.py:185-201`).
8. **Default-deny behavioural leg** — an empty-allow synthetic agent resolves ZERO
   metrics tools via `_apply_tool_filter` (`:203-220`).
9. **Holder census** — the category is in exactly
   `{maintenancer, devops, wanderer}`'s allow lists and NOWHERE ELSE
   (`test_instance_ops_registration.py:236-246`; `test_wedge_capture_registration.py:266-275`).
10. **Body-gate pin** — the helper admits exactly the three holders and is
    fail-closed on `""` / `None` / `123`
    (`test_wedge_capture_registration.py:226-246`).

### Phase G — shell packs + PACKS.md (optional but house-expected)

* `test/packs/metrics_read_unit_test.sh`
* `test/packs/metrics_read_mutation_legs.sh` — plant defects, assert RED, restore,
  assert GREEN (the house mutation-verification idiom; the notify pack's 8 legs over
  4 files is the model — `.agents/tester/PACKS.md:2321`).
* Register both in `.agents/tester/PACKS.md` (table rows at `:2320-2322` are the
  model).

### Phase H — optional: the frozen drift pin

`tests/unit/tools/test_frozen_tool_name_discovery.py` — the bidirectional
source-vs-`KNOWN_TOOL_NAMES` drift detector, named at
`daemon/tools/_tool_registry.py:1285-1288`. If the new tool names are added to
`KNOWN_TOOL_NAMES` but the source closure lands in a different commit, this pin
fires. Land A1-A12 and B3/B4 in the same commit.

---

## 8. Open design questions the planner must close (not answerable from the tree)

1. **How does the tool reach the metrics provider?** The central-metrics-v1 substrate
   serves the query door from `app.state.metrics_query`
   (`daemon/routers/metrics.py:220-231`; provider
   `daemon/services/metrics_query/provider.py:58` — `RegistryMetricsQueryProvider`
   with `families()` `:211`, `snapshot()` `:281`, `history()` `:318`). A per-instance
   tool factory receives the `manager`, not the `FastAPI` app. Options: (a) expose the
   provider on the manager (a `manager.metrics_query` property, like
   `manager.board_service` / `manager.scheduling_service` — `daemon/tools/instance.py:2691`,
   `:2728`); (b) call the service layer directly from the tool
   (`daemon/services/metrics_query/envelope.py` builders
   `build_families_envelope` / `build_snapshot_envelope` / `build_series_envelope`,
   `encode_cursor`). **(b) is the lower-risk path** — it needs no manager surface
   change, and the router already imports those builders directly.
2. **Auth parity.** The router's door is `maintenance:read`-gated and returns
   `503 AUTH_NOT_CONFIGURED` fail-closed when auth is not configured
   (`daemon/routers/metrics.py:171-220`). An agent tool has no HTTP auth; decide
   whether the tool mirrors the auth posture or relies on the category + body gate
   alone (the house precedent for privileged categories is: category + body gate).
3. **Multi-agent allow-list.** One category with a 3-agent allow-list (wedge-capture
   shape) vs three categories — the house precedent is ONE category with a 3-agent
   holder set (`daemon/tools/_tool_registry.py:529-540`).
4. **Audit.** A read writes no audit row (`daemon/tools/instance_read.py:62-64`). If the
   lane wants query auditing, that is a new table + migration + the two-tier ledger
   (Tier-1 same-transaction / Tier-2 write-ahead) — a separate decision, with a
   non-empty migration delta.
5. **Kill switch.** `notify_user` has `ENSEMBLE_NOTIFY_USER_ENABLED=false`
   (`daemon/tools/notify_user.py:106`, refusal detail at `:725`) → typed refusal
   `notify_disabled` (`:197`). A read-only metrics category probably needs no kill
   switch, but the house pattern for a privileged surface is to have one — decide
   explicitly.
