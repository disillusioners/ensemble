# nest-ops — Implementation Plan

**Commission:** nest-ops · `instance_ops` privileged tool category for the maintenancer
**Base:** `origin/latest` @ `8e6df58e445cd56b5e775dacebe0ef53f0add0b4`
**Branch:** `feature/nest-ops-instance-ops` (worktree `/home/nea/ensemble-src-wt-nest-ops`)
**Target version:** **resolved at stage time** — a separate commission owns the v0.27.0 line.
**Amended 2026-10-21** by the architect adjudication: D-13 (two categories), D-14 (provenance
neutral home), D-15 (two-tier audit), D-16 (budget + min-age), D-17 (`confirmed_target_id`),
D-18 (`pclass=="inherited"`), D-19 (three-dimension liveness + reporting predicates), D-20
(`instance_reclaim` redefined). **10 tools ship, not 8.**
**Promote:** **NO.** The 3-factor nonce ceremony is user-gated. Stage only.

---

## 0. Execution rules (read before touching anything)

| Rule | Detail |
|---|---|
| **ADDITIVE ONLY** | No modification of existing behaviour for existing callers. The only shared-code changes are (a) the registry/allow-list surface needed to add a category, (b) the `create_instance_tools` construction extend, (c) the additive dismiss-body extraction (DV-2). |
| **Daemon restart required** | `_registry` is an import-time singleton (`daemon/registry.py:1474-1496`); `discover()` runs **once per process**. The category is **not live** until the daemon restarts. This must be in the staging/runbook text and in the release notes. |
| **One-commit pin ceremony** | The 7 exact-set pins + the new registration pin + `ROLLOUT.md:51` + the inherited 3-red integration pin + the `agents/` meta.json edit land in **ONE commit** so the privileged set is never red between two commits. Precedent: `f585adea8`. |
| **Route/decorator binding trap (critical note `ed2dfe67`)** | Inserting a helper **between** a FastAPI decorator and its handler silently re-binds the route: the module imports, boot smoke stays green, and the endpoint 422s. **If any router is touched** (DV-2 touches `daemon/routers/instances.py`), a **route-table readback pin is MANDATORY** — assert the route path + methods for `/instances/{instance_id}/question/dismiss` and `/instances/{instance_id}/answer` are unchanged after the extraction. |
| **Boot-smoke isolation** | Smokes run in an **isolated `systemd-run` transient unit** with a scratch CWD and a scratch PG — **NEVER** inside the live daemon's cgroup. |
| **Never resolve live-shaped paths in test scripts** | Fence via `mktemp FAKE_HOME` + an escape guard. (Incident 2026-10-12: an unfenced fixture deleted the live install twice.) |
| **Every new SQLModel table needs its own `.sql` file** | The file is the persistent record; the runner is SQLite-only and cannot stand in. |
| **Doc surface must state CURRENT semantics** | The D-V2 lesson: a stale doc string shipped live in v0.26.2 and was only fixed in v0.26.3. A **test asserts the doc surface matches behaviour**. |
| **Forbidden layer** | `docs/agent-prompt-writing-guide.md` is BINDING for the `agents/maintenancer/` and `agents/ari/` edits. See §7. |

---

## 1. File-by-file change set

### 1.1 NEW — TWO tool modules (D-13)

| Path | Kind | Contents |
|---|---|---|
| `daemon/tools/instance_read.py` | **NEW** | The read category: `CATEGORY_NAME = "instance_read"`, its `CATEGORY_DOC`, `_is_instance_read_authorized()`, and `create_instance_read_tools(...)` holding the 4 read closures (`instance_list`, `instance_get`, `instance_health`, `fleet_capacity_summary`). **No kill-switch** — reads stay available when the act category is disabled. |
| `daemon/tools/instance_ops.py` | **NEW** | The act category: `CATEGORY_NAME = "instance_ops"`, its `CATEGORY_DOC`, `_is_instance_ops_authorized()` (a separate named constant, one agent: `maintenancer`), `KILL_SWITCH_ENV`, `_resolve_instance_ops_enabled()`, `_refusal()`, `_dry_verdict()`, `create_instance_ops_tools(...)` holding the 6 act closures, and the budget check (D-16). |
| `daemon/tools/provenance.py` | **NEW** (D-14) | The **neutral home** for the provenance implementation, **moved** out of `upgrade_tools`: the verdict dataclass, the canonical gate, and the capture helpers — all with **public** names. |

**Naming** — the category strings are **`instance_read`** and **`instance_ops`**
(underscore). Precedent: `system_upgrade` is already underscore-form. **Per-tool allow is
REJECTED** (D-13) — a literal tool name in an allow list bypasses the category gate.

**The 10 tool names** (flat snake_case, the repo convention established by `secret_store` and
`ens_env_read`): `instance_list`, `instance_get`, `instance_health`,
`fleet_capacity_summary` · `instance_terminate`, `instance_reclaim`, `instance_nudge`,
`instance_mark`, `instance_answer`, `instance_dismiss`.

**Layer-3 gate (mandatory, in BOTH modules).** Every closure calls its module's
`_is_*_authorized(agent_id)` **before** anything else. **One shared helper per module, N call
sites** — a copy-paste of the gate into ten bodies is the exact divergence the shared helper
exists to prevent. Fail-closed: a blank / non-`str` `agent_id` is **never** authorized.

**The doc-surface rule.** Each tool carries its documentation in **three** places, and **all
three must state current semantics**: the error envelope / short docstring, the
`args_schema` `Field(description=…)` strings, and the `_full_doc_` block. A **test asserts
the three agree** (AC-16). Surfaces at particular risk (R17): the `cascade` semantics (the
service has no cascade flag — the doc must not imply one exists); `instance_reclaim`'s
terminal-only + message-carrying semantics (D-20); the kill-switch's scope (**acts**
disabled, **reads** live); the provenance window's in-memory nature (R3).

#### 1.1a MODIFY — `daemon/tools/upgrade_tools.py` (D-14, the delegating aliases — FIVE names)

> **📌 ANNOTATION — stale-prose correction (approver note 2, applied on copy into the
> implementation worktree 2026-10-21).** The phrase "the 3-line delegating alias" was
> STALE: the alias block covers **FIVE** moved names (+ `_EMPTY_PROVENANCE`), not one.
> The following sites were corrected **in the worktree copy** of this doc set:
> * this file, §2.5 item 4 (line ~401) — now names the FIVE-name alias block;
> * this file, §5 build row **S-3** (line ~559) — same correction;
> * this file, §9 disclosure block (line ~750) — the alias paragraph now enumerates
>   all five moved names;
> * `decisions.md` — the **D-14** heading and the **DV-3** deviation-table row — same
>   correction.
>
> `architecture-recommendation.md` is the architect's **read-only INPUT** ("input, not
> amended by this doc set", `plan-overview.md` §Doc set) and still carries the phrase in
> two historical-recommendation rows — it is deliberately **NOT** amended. The binding
> spec for the alias shape is **§1.1a** of this file.
>

`_restart_provenance` becomes a **delegating alias** to `daemon.tools.provenance`:

```python
def _restart_provenance(manager, instance_id):
    """Delegating alias — the implementation moved to daemon/tools/provenance.py (nest-ops D-14).
    Name/signature/verdict shape preserved byte-compatibly for the live lane's pins."""
    from .provenance import restart_provenance
    return restart_provenance(manager, instance_id)
```

**⚠️ W1 (binding) — the alias block is NOT one name, it is FIVE.** The move relocates
`_restart_provenance`, `_lookup_provenance_field`, `_capture_user_source`,
`_capture_provenance_class`, and `_ProvenanceVerdict` (plus `_EMPTY_PROVENANCE`, which
travels with the dataclass). **The live lane still consumes the other four** — verified sites
in `upgrade_tools.py`: `:1734` and `:3043` (`_capture_provenance_class`), `:1744`
(`_capture_user_source`), `:2889` (**constructs `_ProvenanceVerdict` inside the
`system_restart` path**), `:1359` (`_lookup_provenance_field`), `_EMPTY_PROVENANCE` at
`:1275/:1338/:1348/:1736` (review delta: the earlier `:2896` cite was misattributed —
that line is a `_restart_provenance` **call site**, and the `_ProvenanceVerdict` construction
at `:2889` is already listed above). **Plus ONE EXTERNAL PIN the original list never named:**
`tests/unit/tools/test_post_restart_arm_notify_journal.py:833`, `:844`, `:852` **import
`_capture_user_source` from `upgrade_tools`** — add it to the affected-pin list.

**FIX (all three, binding):**
1. A single **top-of-module rebinding block** aliasing **ALL FIVE moved names** (plus
   `_EMPTY_PROVENANCE`):
   ```python
   from .provenance import (  # D-14 — implementation moved; names preserved for the live lane
       provenance as _provenance_impl, ...  # or per-name def-style aliases, see below
   )
   ```
   (per-name `def`-style delegating aliases are equally acceptable — the requirement is that
   **every consumed name still resolves from `upgrade_tools`**).
2. **The alias-equivalence pin covers EACH consumed name** — `_restart_provenance`,
   `_capture_user_source`, `_capture_provenance_class`, `_lookup_provenance_field`,
   `_ProvenanceVerdict` — not just the first.
3. `tests/unit/tools/test_post_restart_arm_notify_journal.py` is named in the affected-pin
   list (its `_capture_user_source` imports must keep resolving).

**🔴 NAMED PLACEMENT CONSTRAINT (HARD — from the reviewer's T0a verification):**
the T0a pin at `tests/unit/tools/test_agent_restart.py:127-137` slices `upgrade_tools.py`
source from `src.index("async def system_upgrade")` to
`src.index("system_upgrade._full_doc_", si)` and asserts the slice contains
`windows.get(current_instance_id)` and **NONE of the moved names**. Therefore:

> **The aliases MUST live at MODULE level (near `:1384`, where `_restart_provenance` sits
> today) and MUST NEVER be nested inside `create_upgrade_tools` between the two markers
> `async def system_upgrade` and `system_upgrade._full_doc_`.** Violating this turns the T0a
> pin red (or, if the aliases were to shadow, silently changes the slice's meaning).

**⚠️ NAMED GATE (AC-26): the reviewer AND the tester must independently re-verify the T0a
claim** — that the slice scoping makes a module-level move invisible to the pin — **before
merge**, and record the verification in both reports.

### 1.2 MODIFY — the registry (`daemon/tools/_tool_registry.py`)

Four touch-points, all in the same file:

| Constant | Line | Change |
|---|---|---|
| `DYNAMIC_TOOL_NAMES` | `:23` | += the 10 tool names |
| `PRIVILEGED_TOOL_CATEGORIES` | `:290` | += `"instance_ops"` **and** `"instance_read"` — the set goes **12 → 14** (two entries; D-13). Add the system-POV comment block explaining why (single-owner categories; the act surface is destructive and the read surface is fleet-wide telemetry; the empty-allow universe must never auto-grant either) |
| `CATEGORY_MODULES` | `:828` | += **two** entries: `"instance_ops": "daemon.tools.instance_ops"` **and** `"instance_read": "daemon.tools.instance_read"` — each with the registry's own warning comment: the value **MUST** be a `daemon.tools.*` path, because the AST scanner resolves it as `daemon/tools/<parts[2:]>.py`; a `daemon.services.*` value is **never** discovered and the exact-equality drift pin fails at boot |
| `KNOWN_TOOL_NAMES` | `:1092` | += the 10 tool names (inventory, not the gate) |

Also update the registry's **SAME-PR RULE** comment (the one `f585adea8` corrected from
"FIVE pins" to "SEVEN pins") — it must now name **EIGHT** pin files and carry the grep
recipe, so the next commission does not inherit a stale count.

**D-14 addition:** `daemon/tools/provenance.py` needs **no** `CATEGORY_MODULES` entry — it
is a **library**, never a category target (the same rule the registry documents for
`daemon/services/self_alarm_service.py` and `daemon/tools/views_auth.py`).

### 1.3 MODIFY — the construction extend (`daemon/tools/instance.py`)

| Location | Change |
|---|---|
| imports (`:274-277` block) | `from .instance_ops import create_instance_ops_tools` **and** `from .instance_read import create_instance_read_tools` |
| `create_instance_tools` (`:5958-6028` region) | **Two** extend blocks: `tools.extend(create_instance_read_tools(manager, current_instance_id=current_instance_id, agent_id=agent_id, agent_tag=version_tag))` and `tools.extend(create_instance_ops_tools(manager, current_instance_id=current_instance_id, agent_id=agent_id, agent_tag=version_tag))` |
| **PLACEMENT (load-bearing)** | Both extends **must precede** `create_help_tool` (which snapshots the tool list into its index), `scan_tools_for_full_docs`, and `_apply_tool_filter` (which applies the per-agent allow list **and** the privileged-category default-deny strip). A later extend is either invisible to the help tool or **bypasses the privileged strip**. Copy the four-line placement comment from the board/self-alarm blocks verbatim in shape. |
| `agent_id` availability | `agent_id` is already a parameter of `create_instance_tools` — it is the layer-3 gate's input. A construction that omitted it would fall back to `""` ⇒ the body gate denies every call (fail-closed, but a dead tool). |

### 1.4 NEW — the audit table model

| Path | Kind |
|---|---|
| `daemon/repositories/instance_ops/__init__.py` | **NEW** (package) |
| `daemon/repositories/instance_ops/models.py` | **NEW** — `InstanceOpsAudit(SQLModel, table=True)`, `__tablename__ = "instance_ops_audit"` |
| `daemon/repositories/instance_ops/repository.py` | **NEW** — the writer, with the **same-transaction** contract |

**Model shape** — `repair_log`'s vocabulary, minus the 6 SQL-repair-specific columns
(`sql_text`, `sql_class`, `sql_hash`, `target_table`, `nonce`, `ttl_expires_at`), plus the
instance-ops-specific ones:

| Column | Type | Source | Notes |
|---|---|---|---|
| `id` | `str` (UUID4) | `repair_log` | PK |
| `created_at` | `str` (ISO-8601 UTC) | `repair_log` | |
| `created_by_instance_id` | `str \| None` | `repair_log` | the **actor** (the calling instance) |
| `created_by_agent_id` | `str \| None` | `repair_log` | the **actor** (the calling agent) |
| `action` | `str` | **new** | the tool name (`instance_terminate`, …) |
| `target` | `str` | **new** | the target instance id |
| `reason` | `str` | **new** | the required act reason, verbatim |
| `dry_run` | `bool` | `repair_log` | **discriminates a preview row from an applied row** |
| `before_snapshot` | `Any \| None` (JSONB) | `repair_log` | pre-act state (status, marker, children…) |
| `after_snapshot` | `Any \| None` (JSONB) | `repair_log` | post-act state |
| `outcome` | `str` | `repair_log` (+2) | `"previewed" \| "pending" \| "committed" \| "refused" \| "error"` — `"pending"` is the **Tier-2 write-ahead** state (D-15) |
| `confirmed_target_id` | `str \| None` | **new** (D-17) | the named-target confirmation, recorded verbatim |
| `provenance_class` | `str \| None` | **new** (D-18) | `"direct"` \| `"inherited"` \| `None` |
| `provenance_depth` | `int \| None` | **new** | the verdict's `depth` |
| `root_source` | `str \| None` | **new** | the window's `root_source` |
| `root_message_id` | `str \| None` | **new** | the user's `root_message_id` — what a reviewer compares `confirmed_target_id` against (D-17) |
| `error_message` | `str \| None` (≤512) | `repair_log` | the refusal text when `outcome != "committed"` |
| `preview_run_id` | `str \| None` | `maintenance_runs.dry_run_run_id` (`:132`) | soft link from a preview row to the later applied row |

**The audit boundary is a TWO-TIER property, stated per tool — not a uniform claim (D-15;**
the original uniform claim was partly unsound: `instance_ops` acts are daemon service calls
that **own their own transactions**, and terminate's DB half runs on a **worker thread** via
`asyncio.to_thread` (`daemon/services/instance_lifecycle.py:2827`) where a Session cannot be
shared). See `plan-overview.md` §7.5 for the full per-tool truth table.

* **Tier 1 (terminate, mark):** the act and its audit row commit **together or neither** —
  via an **additive `audit_sink` hook** inside the service (below) and an optional `session=`
  on `repo.set_metadata`. AC-9a.
* **Tier 2 (nudge, answer, dismiss, reclaim):** **write-ahead** — the audit row is INSERTed
  FIRST with `outcome="pending"` and the full intent, the act runs, then the row is UPDATEd
  to `"committed"` / `"error"`. **A crash mid-act leaves a durable `pending` row.** AC-9b.

**Schema registration.** The PG schema is created by `SQLModel.metadata.create_all` on boot
(`daemon/api.py:954-977` — the `secrets` + `secrets_audit` precedent) **plus** the
`.sql` migration file below. The `.sql` file is the **persistent record** (the runner is
SQLite-only).

### 1.4a MODIFY — the Tier-1 service hooks (D-15)

| Path | Change |
|---|---|
| `daemon/services/instance_lifecycle.py` | Add an **optional `audit_sink: Callable[[Session], None] \| None = None`** parameter threading into `_terminate_instance_db_sync`, invoked **inside its existing `WriteGuardSession`, before commit**. **Default `None` ⇒ byte-identical current behaviour for every existing caller** — the hook is strictly opt-in from the new tool layer. ~10 lines + one call site. **This touches the H10-hardened terminate path** — bounded by the additive-default-None shape and by a dedicated fault-injection test (AC-9a). |
| `daemon/repositories/instance/repository.py` | Add an **optional `session=`** parameter to `set_metadata` (`:2518`) — the same principle, for `instance_mark`. Default preserves current behaviour. |

**Rejected and why (recorded, do not re-litigate):** (a) threading a Session into the
service — Sessions are not thread-crossable, the DB half runs in `asyncio.to_thread`; (c)
moving the transaction boundary into the tool — the service owns the cascade snapshot, the
post-commit outbox ordering, and the H10 crash-safety contract.

### 1.5 NEW — the migration

| Path | Prefix check |
|---|---|
| `daemon/migrations/versions/20261021_120000_create_instance_ops_audit.sql` | **UNIQUE prefix verified free** — `ls daemon/migrations/versions/ | grep 20261021` returns nothing at HEAD (the newest is `20261020_060000_create_board_tables.sql`). 106 → **107** migration files. |

**Pre-existing blockers to note in the file header (they are NOT caused by this migration):**
* the duplicate-prefix `20261018_000001` was **fixed** at `811d3e21b` (both files renamed to
  `20261018_000002` / `20260629_000001`), and the runner now has a loud
  `DuplicateMigrationVersionError` guard;
* **fresh-SQLite boot is still blocked by a DIFFERENT pre-existing prefix**: `20260714_000001`
  (and `20240101_000001`) — those runners die before any new migration. A fresh migration is
  unaffected, but a fresh-SQLite boot test of this commission **will fail for that reason** —
  the test plan must run against **scratch PG**, not fresh SQLite.

### 1.6 The question tools — preference order, verified (leader instruction C + architect findings α/β)

**`instance_answer` — NO router edit at all (finding α).** `answer_questions_via_instance`
(`daemon/routers/answer_helper.py:704-712`) already takes **`live_hub` as a plain parameter**
(verified; the docstring notes "or `None` in tests"). The tool calls it with
`live_hub = getattr(manager, "_live_hub", None)` — the same object `daemon/api.py:2336`
binds to `app.state.live_hub` — and maps the raised `HTTPException`s to refusal tokens.
**`daemon/routers/instances.py` is NOT touched for this tool.**

**`instance_dismiss` — preference order, explicitly:**

1. **PREFERRED — import the router's helper directly, with NO router edit.** A router edit is
   the only thing that exposes the route/decorator binding trap (critical note `ed2dfe67`).
   **Verification result: NOT importable as-is.** `dismiss_question(instance_id, request)`
   (`daemon/routers/instances.py:1239`) takes a `request: Request` and reaches into
   `request.app.state.live_hub` at `:1330` — not self-contained.
2. **Therefore — the extraction happens**, scoped **honestly** per the architect's warning.
   The body at `:1239-1460` is ~200 lines that (i) reach into `request.app.state.live_hub`
   for an **F3-ordered SSE emit that must fire BEFORE the resume cascade** (`:1330-1335`),
   (ii) raise `HTTPException` with `ErrorResponse` envelopes at **four** sites, and (iii) run
   a `finally` block releasing the recorded side-effect set. **This is a real refactor of a
   live route, not a delegation edit.** The honest scope:
   * the extracted service takes **`live_hub` as a plain parameter** (mirroring
     `answer_questions_via_instance`'s shape): the router passes
     `getattr(request.app.state, "live_hub", None)`, the tool passes
     `getattr(manager, "_live_hub", None)`;
   * the four `HTTPException`s become **typed service errors** the router re-maps;
   * **a test asserts the SSE emit still precedes the cascade** (the F3 ordering contract);
   * **the mandatory route-table readback pin applies** (`ed2dfe67`) — assert
     `POST /instances/{instance_id}/question/dismiss` and `POST /instances/{instance_id}/answer`
     are still bound to the same handlers with the same paths/methods after the move.
     (Precision note: the decorator-rebinding trap fires when a helper is inserted *between*
     decorator and handler; a **body-move does not do that**. The pin is a cheap precaution
     guarding the route table — keep it, and keep the reasoning straight.)

**Files:** `daemon/services/instance_ops_service.py` (new, the extracted dismiss service) ·
`daemon/routers/instances.py` (the delegation edit).

### 1.7 MODIFY — `agents/maintenancer/meta.json`

```diff
   "tools": {
-    "allow": ["system-log", "ens-db", "knowledge", "system_upgrade", "system_restart", "db"],
+    "allow": ["system-log", "ens-db", "knowledge", "system_upgrade", "system_restart", "db", "instance_read", "instance_ops"],
     "deny": ["git_commit", "edit_file", "write_file"]
   },
```

**The position of the two new entries matters** — `agents/maintenancer/ROLLOUT.md:51` asserts
the **exact ORDERED joined string**. Placement: append at the end is simplest and matches
the "newest last" convention used by the ari allow-list growth; the ROLLOUT assert must be
updated to the identical string in the SAME commit.

**Note `ROLLOUT.md:52` too** — it carries a **second** exact-string assert (the deny list).
This commission does not change the deny list, so `:52` stays green — **but the ceremony
checklist names both** so a future deny edit does not rediscover it.

**Nothing else changes.** `agents/ari/meta.json` is **untouched** (D-1). `agents/leader/meta.json`
is **untouched** (D-2).

---

## 2. The pin ceremony — ONE commit

### 2.1 The 7 exact-set pin files (all updated)

| # | Path | Change |
|---|---|---|
| 1 | `tests/integration/test_service_tool_flag_off_byte_identical.py` (`:823`) | Add **BOTH `"instance_read"` and `"instance_ops"`** to the expected frozenset. **NOTE: `@pytest.mark.integration` — the repo default addopts DESELECTS it.** Must be run explicitly with `-m integration` or it is a silent no-op. **(W2: the original "add instance_ops" instruction was a stale single-category leftover — followed literally it left this exact-frozenset assert RED.)** |
| 2 | `tests/integration/test_maintenancer_spawn_resolves_tools.py` (`:344`) | Add **BOTH** entries to the expected frozenset **AND** fix the inherited 3 reds (see §2.3 — the literal final allow list is below). **(W2 correction, same stale-leftover cause.)** |
| 3 | `tests/unit/test_board_registration.py` (`:176` exact-set via `EXPECTED_PRIVILEGED_SET` at `:68-81`; `:187` `len == 12`) | Add **`"instance_ops"` and `"instance_read"`** to `EXPECTED_PRIVILEGED_SET`; bump `len(...) == 12` → **14**. |
| 4 | `tests/unit/test_self_alarm_registration.py` (`:184` exact-set via `EXPECTED_PRIVILEGED_SET` at `:74-87`; `:187` `len == 12`) | Same — both entries, `len == 14`. |
| 5 | `tests/unit/tools/test_secrets_registration.py` (`:226`) | Add **both** entries. |
| 6 | `tests/unit/tools/test_upgrade_registration.py` (`:152`) | Add **both** entries. |
| 7 | `tests/unit/tools/test_attestation_registration.py` (`:189`) | Add **both** entries. |

**Counts AND exact ordered lists are the norm** — a missed pin breaks SILENTLY. The
`EXPECTED_PRIVILEGED_SET` in pins 3 and 4 are **ordered literals**; the new entry's position
must be consistent across both files and the registry's literal order.

### 2.2 NEW — the registration pin

| Path | Asserts |
|---|---|
| `tests/unit/test_instance_ops_registration.py` | (a) `CATEGORY_MODULES.get("instance_ops") == "daemon.tools.instance_ops"` **AND** `CATEGORY_MODULES.get("instance_read") == "daemon.tools.instance_read"`; (b) exact `PRIVILEGED_TOOL_CATEGORIES` frozenset membership (**both** entries); (c) the frozenset **LENGTH** count (**14**); (d) **source-ordering** of both extends; (e) all 10 tool names ∈ `DYNAMIC_TOOL_NAMES` and ∈ `KNOWN_TOOL_NAMES`; (f) the AST scanner finds all 10 `@tool` functions at any nesting depth; (g) both `create_instance_tools` extends precede `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter`; (h) **the wiring pin** — the `instance_ops` gate resolves to the canonical helper in `daemon/tools/provenance.py`, no local re-implementation (AC-10a); (i) **the vocabulary pins** — `ok`/`pclass`/`depth`/`detail` tokens and the refusal-token mapping (AC-10b). |

**Model:** `tests/unit/test_board_registration.py` (the category→module + frozenset + count +
source-ordering pattern).

### 2.3 The inherited 3-red integration pin — **FIXED INLINE** (leader D-1)

`tests/integration/test_maintenancer_spawn_resolves_tools.py` is **ALREADY 3-RED on the live
tree**, for an unrelated routed reason: commit `ff27305c4` added `system_restart` to
`agents/maintenancer/meta.json`'s allow and updated only `tests/unit/test_maintenancer_agent.py`,
leaving this integration pin stale.

**Its 3 reds (verified at HEAD):**
| Line | Assertion | Live reality |
|---|---|---|
| `:125` | `MAINTAINER_META_ALLOW == ["system-log","ens-db","knowledge","system_upgrade","db"]` (5 items) | the live allow has **6** — `system_restart` was added at `:6` |
| `:137` | `MAINTAINER_META_DENY == ["git_commit","edit_file","write_file","system_restart"]` (4 items) | the live deny has **3** — `system_restart` is allowed, not denied |
| `:208` | `test_maintenancer_gets_system_upgrade_excluding_system_restart` asserts the deny-strip | the strip no longer applies — `system_restart` is **allowed** |

**Why FIX-INLINE and not scope-tight.** The commission edits **THE EXACT allow-list this pin
asserts**. If the file stays red, the new `instance_ops` assertion in it carries **no usable
green/red signal** — the file's exit status is dominated by 3 inherited reds, which destroys
the enforcement evidence the commission exists to produce. House precedent invited the fold
(*"the next commission touching tool pins should fold it"*).

**The commit MUST disclose:**
* pre-existing red count in that file: **3**
* post-commit red count in that file: **0**
* net-new reds introduced by nest-ops in that file: **0**

**The diff must be readable against the base** — the 3-red fix is a separate, clearly-labelled
hunk from the `instance_ops` hunk, so a reviewer can see the inherited fix and the new
assertion independently.

**W2 — the literal final allow list for `:125` (6 live + 2 new, APPENDED).** The inline
3-red fold must land at 0 in one shot, so the plan states the list literally, not as a
description:

```python
MAINTAINER_META_ALLOW == [
    "system-log",
    "ens-db",
    "knowledge",
    "system_upgrade",
    "system_restart",
    "db",
    "instance_read",
    "instance_ops",
]
```

(The appended position matches the `jq` assert string at `ROLLOUT.md:51` exactly:
`system-log,ens-db,knowledge,system_upgrade,system_restart,db,instance_read,instance_ops`.
`:137`'s deny assertion reverts to the 3-item live deny list; `:208`'s deny-strip assertion
is deleted — `system_restart` is now allowed, so the strip no longer applies.)

**Good news recorded (the reviewer's independent pin-ceremony audit): the ceremony is
otherwise COMPLETE** — 7 files / 9 assertion sites at the exact cited lines;
`ROLLOUT.md:51` allow / `:52` deny verbatim; the append-order matches the jq assert;
**NO missed pins exist** (no `len(DYNAMIC_TOOL_NAMES/KNOWN_TOOL_NAMES/CATEGORY_MODULES)`
asserts anywhere; membership-only pins are additive-safe; `test_maintenancer_end_to_end`
is intersection-based as claimed).

**Also verified and SAFE under an additive category:**
`tests/integration/test_maintenancer_end_to_end.py` uses **INTERSECTION** asserts — it
asserts that specific tools are *present* in the resolved set, not that the set is exactly
equal. Adding a category **cannot** break an intersection assert. **Do not touch it.** (This
claim is verified by reading the file; it is recorded here so a future reader knows it was
checked, not assumed.)

### 2.4 The non-test assertion site — `agents/maintenancer/ROLLOUT.md:51`

```
test "$(jq -r '.tools.allow | join(",")' agents/maintenancer/meta.json)" = "system-log,ens-db,knowledge,system_upgrade,system_restart,db"
```

An **exact ORDERED string** assert. Adding a 7th allow entry turns it RED **with no test
runner involved**. It MUST be updated in the SAME commit, with the new entry inserted at the
right position.

`agents/maintenancer/ROLLOUT.md` is **NOT a prompt artifact** (0 rows in
`agents/.prompt-artifacts.sha256`) — it is a runbook — but it still carries the assert, so it
still must be edited. **`:52` carries a second exact-string assert (the deny list)** —
unchanged by this commission (stays green), but named in the checklist so a future deny edit
does not rediscover it.

### 2.5 The ONE-commit requirement

**One commit** contains:
1. `daemon/tools/_tool_registry.py` (5 registry touch-points + the SAME-PR RULE comment).
2. `daemon/tools/instance.py` (two imports + two construction extends).
3. `daemon/tools/instance_read.py` + `daemon/tools/instance_ops.py` + `daemon/tools/provenance.py` (new; the last is D-14's neutral home).
4. `daemon/tools/upgrade_tools.py` (the **FIVE-name delegating alias block** —
   `_restart_provenance`, `_lookup_provenance_field`, `_capture_user_source`,
   `_capture_provenance_class`, `_ProvenanceVerdict`, plus `_EMPTY_PROVENANCE` —
   per §1.1a; D-14).
5. `daemon/repositories/instance_ops/**` (new) + `daemon/migrations/versions/20261021_120000_create_instance_ops_audit.sql` (new).
6. `daemon/services/instance_ops_service.py` (new, the dismiss extraction) + the `daemon/routers/instances.py` delegation edit (DV-2).
7. The Tier-1 service hooks: `daemon/services/instance_lifecycle.py` (`audit_sink`, default None) + `daemon/repositories/instance/repository.py` (`session=`, default None) (D-15).
8. `agents/maintenancer/meta.json` (the two allow entries).
9. `agents/maintenancer/tools_note.md` (agent-POV capability table).
10. `agents/ari/workflow.md` (the patrol convention).
11. The 7 exact-set pin files.
12. The new `tests/unit/test_instance_ops_registration.py`.
13. `agents/maintenancer/ROLLOUT.md` (the exact-string assert at `:51`).
14. **`agents/.prompt-artifacts.sha256` (the re-stamp)** — the architect found the original list omitted it; the baseline check is non-blocking at stage (`stage.sh:621-660`, allow-stale by design) so a stale baseline is **invisible rather than red**. Put it in the same commit so the stage journal does not record a drift warning for the release that introduces the categories.

**Rationale:** so the privileged set is **never red between two commits** (the
`f585adea8` precedent). A commit that adds the category without updating the pins leaves the
tree red for every other lane's CI run.

**If the implementation must be split into more than one commit for practical reasons**, the
split point is: **implementation commits first (category + tools + audit table), then ONE
final "pin ceremony" commit** that carries all 9 pin sites + the meta.json edit +
`ROLLOUT.md` **together**. The invariant to preserve is: *the privileged set and the allow
entry never disagree across a commit boundary.*

---

## 3. The `agents/` edits — the FORBIDDEN LAYER (highest-risk item)

`docs/agent-prompt-writing-guide.md` is **BINDING** for the `agents/maintenancer/` and
`agents/ari/` edits.

### 3.1 What agent-facing prompt prose must NEVER mention

`meta.json` itself · `tools.allow` / `tools.deny` as configuration objects · any `daemon/`
path · `_tool_registry.py` · `skill-set.yaml` · `innate_skills` · `agent_id=` plumbing ·
`seed_all` · `default_agent_versions` · test file paths.

### 3.2 THE TRAP

The privileged-category comment prose in the **registry source** (e.g. the `board`
category's own comment — *"the board category is PRIVILEGED … Explicit
`tools.allow: ["board"]` is the only access path"*) is **SYSTEM-POV**. It is **correct** in
the registry source and in a `CATEGORY_DOC` consumed by `tool_help` — and it is **FORBIDDEN**
if copy-pasted into an agent's `tools_note.md`.

The agent-facing section must be **AGENT-POV**: state what you **CAN** do, not which keys
configure it. **House style: a compact table of category → why I hold it, no registry
mention; never describe the deny mechanism in prose.**

### 3.3 `agents/maintenancer/tools_note.md` — the edit

The file already opens with a `Direct vs Delegated Access` section whose table is
category → what I use it for (verified at HEAD: the table rows are `system-log`, `ens-db`,
`knowledge`, `system_upgrade`, `system_restart`, `db`). The nest-ops edit adds **one row**:

```
| `instance_ops` | Fleet census + gated surgery — list/get/health/summary reads, and terminate/reclaim/nudge/mark/answer/dismiss acts |
```

and then a **short** section describing the **behaviour** the agent must know:
* the four reads are always available;
* the acts are **audited** — every act writes an audit row, and the act does not happen if
  the audit row cannot be written;
* the destructive act (`instance_terminate`) **previews first** (`dry_run` defaults to on) and
  the preview lists the exact instances that would be terminated;
* a **protected target** (a user-facing chat session, the leader, or one I have marked
  protected) needs an explicit confirmation **and** a genuine-user turn — if the turn is not
  user-originated the tool refuses;
* I never act on my own session;
* there is a kill switch: when it is off, the acts refuse and the reads still work.

**No** mention of `meta.json`, `tools.allow`, the registry, the deny mechanism, or any
`daemon/` path.

### 3.4 `agents/ari/workflow.md` — the patrol convention

Lands **next to the existing board/self-alarm conventions** (verified: `agents/ari/workflow.md:284-323`
carries the "Session open — read the FULL open board" and "Fired alarms surface on the board"
sections).

**Content, in agent-POV:**

> **Patrol loop — when an alarm wakes me.**
> When a self-alarm wakes me, I run my existing read lenses — `job_list`, `list_missions`,
> `queue_list`, `dlq_list`, `service_list`. If anything looks sick (a job stuck, a mission
> with no progress, a queue backing up, a service that should be up and is not), I **dispatch
> the maintenancer WITH SPECIFICS** — the instance id, what looks wrong, and what I already
> checked. I note it on the board. I report to the user only if it matters to them.

**It must NOT**: describe the category's configuration, the allow-list, or the registry; or
teach Ari to perform the surgery — **the surgery is the maintenancer's job.**

### 3.5 The pre-commit forbidden-layer gate (a NAMED gate, from the guide's checklist)

`docs/agent-prompt-writing-guide.md:250`:

> grep the agent dir for `meta.json`, `tools.allow`, `daemon/`, `_tool_registry`,
> `skill-set.yaml`, `agent_id=`, `seed_all`, `innate_skills`, `default_agent_versions`.
> If any appear in prompt prose, reword to agent POV.

**Required: 0 hits** in `agents/maintenancer/` **and** `agents/ari/` for the files this
commission **writes** (`tools_note.md`, `workflow.md`).

**Baseline measured at HEAD:**
* `agents/maintenancer/tools_note.md` — **CLEAN** on that grep today. **Keep it clean.**
* `agents/maintenancer/ROLLOUT.md` — hits (lines 50-53, 64-67, 73, 100, 117, 164, 229, 338,
  365-385). It is a **runbook, not prompt prose** — out of the gate's scope. Do **not** treat
  it as licence to add one.
* `agents/maintenancer/knowledge/**` — hits (`daemon/…` paths). These are the maintenancer's
  **knowledge base**, not prompt prose (they are not in the prompt-artifact baseline
  convention for prose rules). Out of scope for this commission.
* `agents/ari/soul.md` — **PRE-EXISTING DEVIATION**: a
  `daemon/services/mission_resolver.py::` path at `:103`, plus `agent_id=` at `:78`, `:136`.
  **Do NOT copy that pattern** into anything this commission writes, and **do not** treat it
  as licence to add one. `agents/ari/rule.md` and `agents/ari/workflow.md` also carry
  `agent_id="leader"` / `agent_id="worker"` at their existing lines — **pre-existing**, out of
  scope, and a precedent **not** to follow for new prose.

### 3.6 The prompt-artifact baseline re-stamp

`agents/.prompt-artifacts.sha256` (161 rows at HEAD) must be **re-stamped** when `agents/`
files change (this commission changes `agents/maintenancer/meta.json`,
`agents/maintenancer/tools_note.md`, `agents/ari/workflow.md`).

* Authority: `scripts/protection/generate_prompt_artifact_baseline.py --check`.
* **The check is CWD-sensitive — run it from the right directory** (the repo root). The
  known trap: `cd agents/ && sha256sum -c` is CWD-unsafe and produces false results.
* `agents/maintenancer/ROLLOUT.md` is **not** a prompt artifact (0 rows) — it still must be
  edited (§2.4) but it does **not** enter the baseline.

---

## 4. The audit writer — the two-tier contract (D-15)

The writer lives in `daemon/repositories/instance_ops/repository.py` and exposes **three**
methods, one per tier:

| Method | Tier | Semantics |
|---|---|---|
| `write_audit_and_act(action, target, reason, dry_run, before, after, outcome, error_message, actor_instance_id, actor_agent_id, act: Callable[[Session], None] \| None)` | **1** | `act` runs **inside the caller's existing session** (via the `audit_sink` hook for terminate, or the `session=` param for mark) and the `INSERT` joins it. **Any exception — including the `INSERT` failing — rolls back BOTH.** A destructive act that cannot be audited **never happens** (AC-9a). |
| `write_audit_ahead(action, target, reason, provenance_fields, actor_*) -> row_id` | **2** | **INSERT FIRST**, in its own transaction, with `outcome="pending"` and the full intent. Returns the `row_id` for the later update. |
| `settle_audit(row_id, outcome, error_message, after)` | **2** | **UPDATE** the pending row to `"committed"` / `"error"` after the act. **A crash between the two leaves the `pending` row durable** — the ledger always contains the intent (AC-9b). |

**The module docstring and the table's DDL comment must state the two-tier model** — an
honest declaration of which acts are transaction-coupled and which are write-ahead — so R11's
"never silently de-couple" rule is satisfied by *declaration*, not by silence.

**Careful (the ens-db lane's own bug class):** the ens-db code has a special autocommit path
for `VACUUM`/`ANALYZE` where the audit write is **separate** from the statement
(`daemon/tools/ens_db_tools.py:1034-1065`) — correct there, because `VACUUM` cannot run inside
a transaction. **No `instance_ops` act has that shape.** If a future act cannot be
transaction-coupled **and** cannot be honestly write-ahead, it must be **excluded from the
category** — the original R11 rule, restated so the generalization does not become licence.

## 5. Ordered build sequence

| # | Step | Files | Gate |
|---|---|---|---|
| S-1 | Create the branch/worktree from `origin/latest` @ `8e6df58e4`; confirm `base_ref_equal_local_latest` | — | `git rev-parse origin/latest` == `8e6df58e445cd56b5e775dacebe0ef53f0add0b4` |
| S-2 | Write the audit model + repository + `.sql` migration | `daemon/repositories/instance_ops/**`, `daemon/migrations/versions/20261021_120000_create_instance_ops_audit.sql` | Migration prefix unique (107 files); the `.sql` file is the persistent record |
| S-3 | Write `daemon/tools/provenance.py` (the neutral home) + the FIVE-name delegating alias block in `upgrade_tools` (§1.1a) | `daemon/tools/provenance.py`, `daemon/tools/upgrade_tools.py` | **AC-26 gate: reviewer AND tester independently re-verify the T0a claim before merge** |
| S-4 | Write the two tool-module skeletons (categories, gates, kill-switch, refusal, dry-verdict, budget check) | `daemon/tools/instance_read.py`, `daemon/tools/instance_ops.py` | imports clean; no daemon restart yet |
| S-5 | Implement the 4 READ tools (census/hydration split; SQL-side `status`) | `daemon/tools/instance_read.py` | GT-3: `status` threads through `repo.list(status=...)`; hydration fields ONLY in `instance_get` |
| S-6 | Implement the 6 ACT tools (Tier-1: terminate + mark-protect/suspect; Tier-2: nudge, answer, dismiss, reclaim, **mark-park (W3)**) | `daemon/tools/instance_ops.py` | reason required, dry_run default True, audit row, refusal tokens, budget + min-age (D-16), `confirmed_target_id` (D-17), `pclass=="inherited"` (D-18) |
| S-6b | The dismiss extraction (preference order §1.6 — direct import first, extraction only because the handler is not self-contained) | `daemon/services/instance_ops_service.py` (new), `daemon/routers/instances.py` | **route-table readback pin** + the **SSE-before-cascade ordering test** (DV-2 / `ed2dfe67`) |
| S-6c | The Tier-1 service hooks (additive, default None) | `daemon/services/instance_lifecycle.py`, `daemon/repositories/instance/repository.py` | byte-identical with default None; AC-9a fault-injection test. **W3: `mark_deliberate_pause_override` is NOT touched** — `park` rides Tier-2 write-ahead instead |
| S-7 | Registry touch-points (5) + the SAME-PR RULE comment | `daemon/tools/_tool_registry.py` | two `CATEGORY_MODULES` entries; the set 12 → 14; a `daemon.services.*` value is never discovered |
| S-8 | Construction extends (two) | `daemon/tools/instance.py` | both **before** `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` |
| S-9 | `agents/maintenancer/meta.json` (two allow entries) + `agents/maintenancer/tools_note.md` + `agents/ari/workflow.md` | the 3 agent files | **forbidden-layer grep = 0 hits** on the two prose files |
| S-10 | The 7 exact-set pins + the new registration pin + `ROLLOUT.md:51` + the inherited 3-red fix | the pin files | all in the ONE commit with S-7/S-8/S-9/S-12 |
| S-11 | Tests (§6) | `tests/unit/test_instance_ops_registration.py` + the behavioural tests | see §6 |
| S-12 | Re-stamp the prompt-artifact baseline — **in the SAME commit** (the architect's finding iii: the original list omitted it; a stale baseline is invisible, not red) | `agents/.prompt-artifacts.sha256` | `generate_prompt_artifact_baseline.py --check` from the **repo root** |
| S-13 | Full-suite run; **net-new reds == 0** | — | disclose pre-existing vs post-commit red counts |
| S-14 | Merge to `latest`; **stage only, NO promote** | `scripts/upgrade/stage.sh` | L1 tip-identity guard: `HEAD == refs/remotes/origin/latest` (non-tip ⇒ exit 78 `non-tip-tree`); provenance sidecars written; version **resolved at stage time** |
| S-15 | Isolated boot smoke on the staged binary | — | `systemd-run` transient unit, scratch CWD + scratch PG; banner shows the staged version; `/livez` + `/readyz` 200; the 10 tools appear in the maintenancer's resolved set (post-restart — the categories are inert until the daemon restarts) |

---

## 6. Test plan

### 6.1 Static / registration

| Test | File | Asserts |
|---|---|---|
| Registration pin | `tests/unit/test_instance_ops_registration.py` (new) | §2.2 (a)-(g) |
| Privileged set | the 7 existing pin files | §2.1 |
| Allow-lists | `tests/unit/test_ari_user_ruling_allow_sets.py` + a new maintenancer allow assertion | the maintenancer holds `instance_ops`; **Ari does NOT**; **leader does NOT**; no other agent's allow-list gains it |
| Deny semantics | `tests/integration/test_maintenancer_end_to_end.py` | **unchanged** — intersection asserts are safe under an additive category (verified, §2.3) |

### 6.2 The forbidden layer

| Test | Asserts |
|---|---|
| `tests/unit/test_instance_ops_prompt_layer.py` (new) | The grep from `docs/agent-prompt-writing-guide.md:250` returns **0 hits** for `meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, `skill-set.yaml`, `agent_id=`, `seed_all`, `innate_skills`, `default_agent_versions` in `agents/maintenancer/tools_note.md` and `agents/ari/workflow.md`. |
| Baseline | `generate_prompt_artifact_baseline.py --check` from the repo root ⇒ 0 mismatches |

### 6.3 Behavioural — the four READ tools

| Test | Asserts |
|---|---|
| `instance_list` — **SQL-side status** | **AC-21:** a fleet larger than `limit` matching one status returns a **correct** filtered result across pages — not a pinned-ordered subset (the GT-3 defect). `limit`/`offset` paginate correctly |
| `instance_list` — census shape | **No `current_task`, no `last_messages`** in the row (hydration belongs to `instance_get`); `children_count` = `len(children)`; `stale`/`idle_seconds` present as **separate fields**; `pause_marker` present; `pending_question_pack` a bool |
| `instance_list` — refusals | a non-`InstanceStatus` status value ⇒ `unknown-status-value` (typed, not an empty page); an out-of-range limit ⇒ `invalid-limit` |
| `instance_get` — hydration | ancestors + descendants; `current_task` and `last_messages` present; `instance_metadata` **redacted** (never the raw blob the existing `get_instance_info` returns); `pause_marker` distinguishes the deliberate tombstone from a question pack; `pending_question_pack` is `null` after a restart |
| `instance_health` — detectors | each of the 6 detectors returns its class; the **stale-running detector uses the lineage MAX aggregate** (R4) so a live `waiting_children` parent is **not** flagged |
| `instance_health` — **disagreement pins** (D-19, **graded honestly per A7**) | **Strong (genuinely public + callable counterparts):** detectors 2 and 5 — `WaitingChildrenWatchdog.wedge_episodes()` and `OrphanWatcherSweepService.counters()` — a labelled fixture yields the **same flagged set** from both code paths. **Weaker (docstring-labelled fixtures):** detectors 3 and 4 — `stale_pause_sweep` and `job_recovery_service` expose no callable classify-only entry, so the fixture is **labelled from the sweep's documented classification**, which is materially weaker than an executable counterpart; **the fixture-labelling table is an implementation-time artifact** and must be reviewed as such. Detectors with **no** counterpart at all (1, 6) are asserted to declare `disagreement_pin: null` — the honest gap is itself pinned |
| `fleet_capacity_summary` | counts by agent/status/liveness; queue depth; worker-pool saturation; DLQ depth |

### 6.3a Behavioural — the three-dimension liveness model (D-19) — **AC-22**

| Test | Asserts |
|---|---|
| The parked-vs-wedged distinction | A **deliberately parked** instance (`paused` + the `park` tombstone, frozen `last_activity_at`, past the staleness threshold) reports `pause_marker` and `stale=true` — **and its `liveness` value does not change with staleness**. A genuinely wedged instance differs by `pause_marker`, not by `liveness`. This is the test that would have failed under the rejected `alive\|stalled\|terminal` vocabulary |
| Canonical reuse | `liveness` is derived from `_STATUS_CANONICAL_MAP` (`daemon/services/work_status.py:67`) — no parallel vocabulary set exists in the new module (source grep) |
| Client-side composability | `status` + `stale` reproduces `alive`/`stalled` in one line for a caller that wants it — asserted as a documentation example, not a shipped vocabulary |

### 6.4 Behavioural — the six ACT tools

| Test | Asserts |
|---|---|
| `instance_terminate` — dry-run | the verdict block lists the target, the **full cascade tree**, the protected determination, the confirmation requirement, the **budget state**, and the orphan list; ends with the explicit `NO mutation happened (dry-run)` line; **no row was mutated** |
| `instance_terminate` — applied | the cascade happened; **exactly one** audit row with `dry_run=false`, `outcome="committed"`, the actor, the target, the reason, before/after snapshots, and all **five** provenance fields |
| `instance_terminate` — **AC-9a Tier-1 fail-closed** | **fault-inject the audit `INSERT` failure ⇒ the act did NOT happen.** The audit_sink hook is inside the service's WriteGuardSession, so the rollback covers the cascade |
| `instance_mark(protect/suspect)` — **AC-9a Tier-1** | via `session=` the marker write and the audit row commit together; fault-injection rolls back both. **`park` is Tier-2 (W3 / leader decision) — do NOT thread `session=` into `mark_deliberate_pause_override`** |
| `instance_terminate` — **AC-11 / D-17** | no `confirmed_target_id` ⇒ `protected-target-confirmation-required`; a **mismatched** name ⇒ `confirmed-target-mismatch`; exact name + no live window ⇒ `no-user-provenance`; exact name + **direct** window ⇒ `direct-provenance-insufficient` (D-18); exact name + **inherited** window ⇒ proceeds |
| `instance_terminate` — **AC-23a** | a **direct** window **on the maintenancer** (the trivial `source="api"` POST / raw direct dispatch — the F2 forgery class, critical note `fd3689a1`) ⇒ `direct-provenance-insufficient`. **The fixture also asserts the GENUINE operator-direct path is refused (INTENDED — L-9)** |
| `instance_terminate` — **AC-23b — THE DELEGATED-FORGE RESIDUAL, PINNED AS TRUTH** | a fixture with a **forged direct window on the SPAWNER** → an **inherited window on the maintenancer** ⇒ asserts the act is **ADMITTED** (the documented residual). **The test's assertion message names DF-4 / critical note `f4cf9c4f` as the root-fix lane.** This fixture exists precisely so the suite does not certify a property that does not hold |
| `instance_terminate` — **AC-24 / D-16** | the 4th committed terminate in 24h refuses `terminate-budget-exceeded`; a protected-target second terminate in 24h refuses (N=1); a target younger than 600s refuses `target-too-young` |
| `instance_terminate` — self-surgery | `id == current_instance_id` ⇒ `self-surgery-refused` |
| `instance_terminate` — cascade=False | a target with live descendants ⇒ `cascade-false-would-orphan-descendants`; with `allow_orphan_descendants=True` the preview **names** the orphans and the audit row names them too |
| `instance_mark` — **AC-9a Tier-1 protect/suspect ONLY — `park` is Tier-2 write-ahead (W3 amendment, decisions.md D-15), NO `session=`** | `protect` adds the dynamic layer; `suspect` annotates; via `session=` the marker write and the audit row commit together (fault-injection rolls back both); `marker-already-set` on a repeat `protect`; `invalid-marker` on a bad value. `park` writes the **deliberate tombstone** (`{"deliberate": True, "cleared_at": …}`) **and the release path defers to it** — via Tier-2 write-ahead audit, **no `session=` is ever threaded into `mark_deliberate_pause_override`** (a crash mid-act leaves a durable `pending` row — AC-9b, row below) |
| `instance_nudge` / `instance_answer` / `instance_dismiss` / `instance_reclaim` / **`instance_mark(park)`** — **AC-9b Tier-2** | the audit row is written **BEFORE** the act with `outcome="pending"`; after the act it is `"committed"`; **a simulated crash mid-act — implemented DETERMINISTICALLY as EXCEPTION INJECTION into the act callable (A5: a true process-kill is out of unit scope)** — leaves the `pending` row durable, and `fleet_capacity_summary.audit_pending` surfaces it (W4) |
| `instance_answer` / `instance_dismiss` — parity | **error parity with the route**: `NO_PENDING_QUESTION`, `QUESTION_PACK_LOST` (pack lost post-restart), `QUESTION_PACK_MISMATCH`, `ANSWER_TARGET_TERMINAL`, `already_delivered` (idempotent), `503` write-paused |
| `instance_dismiss` — **F3 ordering** | the SSE `question_pack` emit fires **BEFORE** the resume cascade (the extracted service preserves the route's contract) |
| `instance_reclaim` — **AC-25 / D-20** | a non-terminal target ⇒ `target-not-terminal` (distinguishing it from `instance_nudge`); a `COMPLETED`/`TERMINATED`/`ERROR`/`FAILED` target revives with the documented message and reuses its checkpoint; self ⇒ `self-surgery-refused` |

> **[DF-4 CORRECTION — 2026-10-22]** Applies to the **AC-11 / D-17**, **AC-23a** and
> **AC-23b** rows above.
>
> * **AC-11 / D-17** — the "exact name + **direct** window ⇒
>   `direct-provenance-insufficient` (D-18)" clause is **RETRACTED**: a live **direct**
>   window now **PROCEEDS** (DF-4 / P2). The `protected-target-confirmation-required`,
>   `confirmed-target-mismatch` and `no-user-provenance` clauses are unchanged.
> * **AC-23a** — **RE-POINTED**: the genuine direct window on the acting instance is
>   **ADMITTED** (test: `test_genuine_direct_window_admits_protected_act`).
> * **AC-23b** — **SPLIT**: the genuine delegated chain is ADMITTED (test:
>   `test_genuine_inherited_window_admits_protected_act`); the FORGED chain's refusal leg
>   moved to the REAL-chain e2e `tests/postgres/test_df4_provenance_closure.py`.
>
> See `.agents/shared/planning/df4-provenance-closure/plan-overview.md`.

### 6.5 The provenance pins (D-14) and the kill-switch

| Test | Asserts |
|---|---|
| **AC-10a — WIRING pin** | the `instance_ops` protected-target gate resolves to the **single canonical helper in `daemon/tools/provenance.py`** — an identity/source assertion (source-grepped, the `test_agent_restart.py:127-133` directional-rule style), **not** a behavioural comparison. **The original fixture-equality pin was VACUOUS** — it compared the helper to itself |
| **AC-10b — VOCABULARY pins** | the verdict tokens (`ok`, `pclass` ∈ {direct, inherited}, `depth`, `detail` strings) and the gate's refusal-token mapping (`ok=False` → `no-user-provenance`; `pclass="direct"` on a protected target → `direct-provenance-insufficient`) are pinned — **neither lane can silently change semantics** |
| Alias equivalence | `upgrade_tools._restart_provenance` and `provenance.restart_provenance` return the same verdict for the same fixture (the alias is a delegation, pinned so it cannot drift into a copy) |
| Kill-switch | `ENSEMBLE_INSTANCE_OPS_ENABLED=0` ⇒ every **ACT** tool returns the **byte-identical** no-op; every **`instance_read`** tool still works |

> **[DF-4 CORRECTION — 2026-10-22]** Applies to the **AC-10b** row above: the
> refusal-token mapping is **RE-POINTED** by DF-4 / P2 — there is now **exactly ONE**
> provenance refusal token (`no-user-provenance`); the class-keyed token is **RETIRED**
> and appears nowhere in `daemon/` or `tests/`. The verdict tokens (`ok` / `pclass` /
> `depth` / `detail`) are **unchanged** — `pclass` survives as OBSERVABILITY on the audit
> row. See `.agents/shared/planning/df4-provenance-closure/plan-overview.md`.

### 6.6 The doc-surface pin (D-V2 lesson)

| Test | Asserts |
|---|---|
| Doc surface | for each of the 10 tools: the error-envelope docstring, the `args_schema` field descriptions, and the `_full_doc_` all state **current** semantics — and the **`dry_run` default is stated as TRUE**, the **reason requirement is stated**, and the **kill-switch behaviour is stated**. Modelled on `TestDocSurfaceWakeTheCaller`. |

**Rationale (stated so it is not skipped):** a stale doc string shipped live in v0.26.2 and
was only fixed in v0.26.3 — the behavioural ruling was live while the doc told the agent the
old thing. This commission ships the doc surface and its pin **together**.

### 6.7 Route-table readback (DV-2)

| Test | Asserts |
|---|---|
| Route table | after the dismiss extraction, `POST /instances/{instance_id}/question/dismiss` **and** `POST /instances/{instance_id}/answer` are bound to their handlers with the same paths/methods/status codes. **Mandatory** (critical note `ed2dfe67`). **Precision:** the decorator-rebinding trap fires when a helper is inserted *between* decorator and handler; a body-move does not — the pin is a cheap precaution, retained. Note `instance_answer` needs **no** router edit at all (§1.6). |

### 6.8 Test hygiene

* **Boot smokes** in an isolated `systemd-run` transient unit with a scratch CWD + scratch PG.
  **Never** inside the live daemon's cgroup.
* **Never resolve live-shaped (HOME-derived) paths** in any test script — fence via
  `mktemp FAKE_HOME` + an escape guard.
* **SQLite**: the fresh-SQLite boot is blocked by a **pre-existing** migration-prefix blocker
  (`20260714_000001`, `20240101_000001`) — unrelated to this commission. The migration test
  runs against **scratch PG**.
* The integration pin #1 is `@pytest.mark.integration` — run it with `-m integration` or it
  is a silent no-op.

---

## 7. Deployment / staging / promote discipline

1. **A daemon restart is REQUIRED** for the category to become live — `_registry` is an
   import-time singleton and `discover()` runs once per process
   (`daemon/registry.py:1474-1496`; the same fact is recorded at
   `agents/maintenancer/ROLLOUT.md:229`). This must appear in the release notes and the
   operator runbook.
2. **Stage only. NO promote.** The 3-factor nonce ceremony is user-gated.
3. **Version resolved at stage time**, not assumed — a separate commission owns the v0.27.0
   line.
4. **L1 tip-identity guard**: `stage.sh` requires `HEAD == refs/remotes/origin/latest` (or
   `refs/heads/latest`); a non-tip tree ⇒ **exit 78 `non-tip-tree`**. **No override exists
   or is needed** — if `origin/latest` has advanced, merge first.
5. **L2 provenance sidecars** are written by `stage.sh` and the artifacts are **re-hashed**,
   never trusted from the sidecar.
6. **Rollback-safety adjudication.** The migration delta vs the live release is **+1 file**
   (107 vs 106), and the file is `CREATE TABLE` (additive). Per the standing rule (a
   non-empty delta gets **explicit semantic reasoning**, not a reflex `=0`): the new table is
   created by the new binary's boot `create_all` **and** recorded by the `.sql` file; the old
   binary never references `instance_ops_audit`, so the table is **inert** on rollback; no
   down-migration exists and none is needed. ⇒ `rollback_safe = true` with
   `source=explicit`, and the reasoning recorded verbatim in the manifest.
7. **The stage/promote runbook** must include the restart requirement and the isolated
   boot-smoke recipe.

---

## 8. The Ari-side patrol convention — exact placement

`agents/ari/workflow.md`, immediately after the existing self-alarm section (the block at
`:316-323` — *"30 minutes or more — arm `self_alarm`"* and *"Fired alarms surface on the
board"*). The new section is titled **"Patrol loop — when an alarm wakes me"** and its
content is the agent-POV text in §3.4.

**Subject to the SAME forbidden layer.** No category configuration, no allow-list, no
registry, no `daemon/` path, no `meta.json`. And it must **not** teach Ari to perform the
surgery.

---

## 9. Disclosure block for the commit (template)

The commit message for the ONE-commit ceremony must disclose, verbatim:

```
Pre-existing red count in tests/integration/test_maintenancer_spawn_resolves_tools.py: 3
Post-commit red count in that file: 0
Net-new reds introduced by nest-ops in that file: 0

Inherited 3-red cause (unrelated, routed): ff27305c4 added `system_restart` to
agents/maintenancer/meta.json and updated only tests/unit/test_maintenancer_agent.py,
leaving this integration pin stale. This commission edits THE EXACT allow-list the pin
asserts, so the pin is fixed INLINE (leader D-1) — otherwise the new assertions carry no
usable green/red signal.

TWO privileged categories (leader D-13): instance_read (4 reads) + instance_ops (6 acts),
both maintenancer-only. The privileged set goes 12 -> 14.

Pin ceremony (ONE commit, 9 assertion sites across 7 files + 1 new registration pin):
  1. tests/integration/test_service_tool_flag_off_byte_identical.py:823   (@pytest.mark.integration — run with -m integration)
  2. tests/integration/test_maintenancer_spawn_resolves_tools.py:344
  3. tests/unit/test_board_registration.py:176 + :187   (len 12 -> 14)
  4. tests/unit/test_self_alarm_registration.py:184 + :187   (len 12 -> 14)
  5. tests/unit/tools/test_secrets_registration.py:226
  6. tests/unit/tools/test_upgrade_registration.py:152
  7. tests/unit/tools/test_attestation_registration.py:189
  + tests/unit/test_instance_ops_registration.py (NEW — both categories, len 14, wiring pin, vocabulary pins)
  + tests/unit/tools/test_post_restart_arm_notify_journal.py (W1 — its `_capture_user_source`
    imports at :833/:844/:852 MUST keep resolving after the D-14 move; alias-equivalence pin
    extended to all five consumed names)
  + agents/maintenancer/ROLLOUT.md:51 (non-test exact-ORDERED allow assert — invisible to pytest)
  + agents/maintenancer/ROLLOUT.md:52 (the deny assert — unchanged, named so it is not rediscovered)
  + agents/.prompt-artifacts.sha256 (the re-stamp — in the SAME commit; a stale baseline is
    invisible, not red, at stage time)

D-14 (provenance neutral home): the implementation moved to daemon/tools/provenance.py;
upgrade_tools._restart_provenance is now a delegating alias to the neutral home
(name/signature/verdict shape preserved) — and so are the other FOUR moved
names (§1.1a): `_lookup_provenance_field`, `_capture_user_source`,
`_capture_provenance_class`, `_ProvenanceVerdict` (+ `_EMPTY_PROVENANCE`).
All five resolve from `upgrade_tools` via one top-of-module rebinding block. T0a re-verified independently by reviewer AND tester:
  reviewer: <name/instance> — verdict: <…>
  tester:   <name/instance> — verdict: <…>
The original fixture-equality drift pin was VACUOUS (compared the helper to itself);
replaced by AC-10a (wiring, identity/source) + AC-10b (vocabulary tokens).

D-15 (audit tiers): Tier-1 same-transaction = instance_terminate (via the additive
audit_sink hook, default None = byte-identical) + instance_mark (via session=, default None).
Tier-2 write-ahead (outcome="pending" -> "committed") = instance_nudge / instance_answer /
instance_dismiss / instance_reclaim. 3 of the 6 shipping acts are NOT transaction-couplable;
they are declared, not silently de-coupled.
```

## 10. Named gates (must appear in the review + test reports)

| Gate | Owner | Blocks merge |
|---|---|---|
| **AC-26 — T0a re-verification** | reviewer **and** tester, independently | YES |
| **AC-9a — Tier-1 fault injection** (audit failure rolls back terminate + mark) | tester | YES |
| **AC-9b — Tier-2 crash leaves `pending`** | tester | YES |
| **AC-21 — SQL-side status pagination** | tester | YES |
| **AC-22 — parked ≠ stale** | tester | YES |
| **AC-23 — the forge shape refuses** | tester | YES |
| **Route-table readback** (critical note `ed2dfe67`) | tester | YES |
| **Forbidden-layer grep = 0 hits** | author + reviewer | YES |
| **Baseline `--check` from the repo root** | author | YES |
