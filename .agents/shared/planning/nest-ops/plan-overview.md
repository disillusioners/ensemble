# nest-ops — `instance_ops` privileged tool category for the maintenancer

**Commission:** nest-ops
**Base:** `origin/latest` @ `8e6df58e445cd56b5e775dacebe0ef53f0add0b4`
**Branch:** `feature/nest-ops-instance-ops`
**Status:** DESIGN COMPLETE — no product code written. **Amended 2026-10-21 by the architect
adjudication (`architecture-recommendation.md`, 279 lines) — leader verdicts Q1–Q6 ACCEPT, plus
THE CUT overruled-with-substance. New decisions D-13…D-20; superseded decisions annotated, not
deleted.**
**Doc set:** `plan-overview.md` (this file) · `decisions.md` · `implementation-plan.md` ·
`risk-register.md` · `architecture-recommendation.md` (input, not amended by this doc set)

---

## 0. Ground-truth corrections (the architect's GT-1…GT-5 — adjudicated ACCEPT)

Five facts verified in the tree by the adversarial pass materially alter the analysis. They
are stated up front because they recur through the contracts.

| # | Correction | Evidence | Effect on this doc |
|---|---|---|---|
| **GT-1** | **The maintenancer does NOT hold the `instance` category.** Its allow is exactly `["system-log","ens-db","knowledge","system_upgrade","system_restart","db"]`. The `instance` tools (`list_instances`, `get_instance_info`, `subtree_status`, `subtree_messages`, `send_message`, `terminate_instance`, `pause_instance`, `resume_instance`) are registered under `"instance"` (`daemon/tools/instance.py:5493-5800`). **The commission brief's premise — "the maintenancer ALREADY holds the `instance` category" — is FALSE.** | `agents/maintenancer/meta.json`; `daemon/tools/instance.py:5493-5800` | §2.11 reframed: the redundancy is **daemon-level** (two surfaces offering overlapping reads), not per-agent. The maintenancer has **no census at all today** — the reads are a genuine gap for the holder |
| **GT-2** | **The LEADER holds the entire `instance` category, including an UNGOVERNED `terminate_instance`.** `agents/leader/meta.json` allow includes `"instance"` (verified: `["instance","subtree_messages","subtree_status","self","attestation","project","help","image","image-gen","knowledge","mcp","critical_notes","project_history","shared_meta_kv","question","midflight","job_pause","job_resume","scheduling","view-views","cu","secrets","settings"]`). The tool at `daemon/tools/instance.py:5493-5503` is nine lines: fuzzy-resolve, `manager.terminate_instance(id)`, return `{"terminated": result}`. **No dry-run, no reason, no audit, no protected-target gate, no kill-switch.** `agents/watcher/meta.json` has `"allow": []` ⇒ the empty-allow universe ⇒ every non-privileged tool, including the ungoverned terminate | `agents/leader/meta.json`; `daemon/tools/instance.py:5493-5503` | **Fleet-wide ungoverned terminate already exists in this daemon and is reachable by at least the leader and every empty-allow agent.** `instance_ops` does not create the blast radius; it adds one *governed* path while leaving the ungoverned ones untouched. → new risk **R21** (leader's terminate remains ungoverned; retrofit is a named follow-up, out of scope) |
| **GT-3** | **The repository ALREADY has a SQL-side `status` filter.** `SQLModelInstanceRepository.list(status=...)` (`daemon/repositories/instance/repository.py:1008-1014`, applied in SQL at `:1135-1137`) accepts a status filter on the flat path. The route has no `status` param, but the repository does — it threads up with **one additive parameter** on `_lifecycle_service.list_instances` / `manager.list_instances` | `daemon/repositories/instance/repository.py:1008-1014`, `:1135-1137` | The original L-2 ("filters applied in Python over a fetched page") was a **CORRECTNESS DEFECT, not a disclosed limitation** — past one page, a status filter silently returns an arbitrary pinned-ordered subset, violating the very house rule the doc cited ("filters apply in SQL, never as post-page Python filtering", `daemon/services/mission_resolver.py:157-180`). → `status` is now **SQL-side** (D-13-amended / Q1) |
| **GT-4** | **The wedge sweeps expose NO reusable read-only predicates.** `job_recovery_service._is_instance_alive` / `_fail_orphaned_job` (private; the one public method `reconcile_drift_states:925` is a *reconciler*, i.e. mutating); `stale_pause_sweep._owner_is_alive` / `_has_live_descendant` (private module functions); `waiting_children_watchdog` exposes counters and `wedge_episodes()` (the already-notified set) plus a destructive `run_once()`; `orphan_watcher_sweep` delegates to `bus._sweep_orphan_watchers` — a **private, destructive** UPDATE; `report_delivery_ledger` exposes one public function; `report_delivery_recovery`'s lanes are all private and all *recover* | § GT-4 of the architecture pass | The original §2.10 "Composes? YES" column **was not supported by the tree. There is nothing to compose.** → D-19 replaces composition with **reporting predicates + disagreement pins** |
| **GT-5** | **`_schedule_explicit_handle_resume` takes a PRE-COMPUTED suspension handle; the selection semantics live in the boot pass, not in the primitive.** `daemon/manager.py:12433-12444` — the signature requires `target_work_id`, `handle_work_id`, `selected_suspension_reason`, `route_outcome`. These are outputs of the boot pass's *selector* (`daemon/services/auto_continue_boot_pass.py`). The primitive has **no selection semantics at all** | `daemon/manager.py:12433-12444` | The original D-8 ("thin wrapper over `_schedule_explicit_handle_resume(silent=True)`") was **not implementable without re-implementing the boot pass's selector** — which D-3 forbids. → **D-20 redefines `instance_reclaim`** entirely |

Two smaller corrections also folded:
* **(a)** the existing `list_instances` tool returns `DEFAULT_PAGE_LIMIT = 10` rows
  (`daemon/constants.py:16`), not 50, fleet-wide across all projects (`project_id=None`) — it is
  a **10-row pinned-first window**, not a census.
* **(b)** the existing `get_instance_info` tool returns the repository `Instance.to_dict()`
  (`daemon/repositories/instance/models.py:128-154`) — which includes the **raw `metadata`
  blob** and does *not* include the API `InstanceInfo` fields (`pending_count`, `pinned`,
  `mcp_tool_names`, `watchover_*` — those live on `daemon/models/instance.py:36`, the HTTP
  response model). The original §2.11 conflated the API model with the tool's real return
  shape. The `instance_get` contract below is written against the tool's **real** shape, and
  the metadata redaction is a genuine improvement the HTTP model does not make.

---

## 1. Objective

Give the **maintenancer** agent the missing surfaces over the instance fleet: (a) the
**read surfaces** it has no census for today (GT-1), and (b) **tiered act surfaces**
(terminate / reclaim / nudge / mark / answer / dismiss) — each act audited, each destructive
act gated by a protected-target confirmation rule.

**No new daemon primitives** (as a *subsystem*). Every act tool wraps an existing daemon
primitive. Where a needed primitive does not exist, the tool is **redefined** or marked
**DEFERRED** rather than a new subsystem being designed (D-3).

**Two categories, one owner (D-13).** `instance_read` (the 4 read tools) and `instance_ops`
(the 6 act tools) — **both** privileged, **both** maintenancer-only. The read/act risk split
becomes **structural** rather than prose. Per-tool allow is **REJECTED** (it opens the
documented literal-name bypass — §2.2).

**Non-goals (explicitly out):** no Ari tools (D-1), no leader access (D-2), no new detection
*engine* (the health report writes **reporting predicates** and pins agreement with the
sweeps — D-19; the original "compose existing predicates" premise was false, GT-4), no second
nonce regime (D-4), no UI/FE surface, no change to any existing caller's behaviour. **And
critically: this commission does NOT retrofit the leader's existing ungoverned
`terminate_instance` (GT-2) — that is a named follow-up (R21), out of scope.**


---

## 2. Verified ground truth

Everything below was read in the tree at `8e6df58e4`. `NOT FOUND` markers are load-bearing.

### 2.1 The registration spine (V1)

| Fact | Evidence |
|---|---|
| `DYNAMIC_TOOL_NAMES` — frozenset of factory-created tool names | `daemon/tools/_tool_registry.py:23` |
| `PRIVILEGED_TOOL_CATEGORIES` — frozenset of privileged category strings | `daemon/tools/_tool_registry.py:290` |
| `CATEGORY_MODULES` — category string → `daemon.tools.*` module path | `daemon/tools/_tool_registry.py:828` |
| `KNOWN_TOOL_NAMES` — static inventory (separate from the gate) | `daemon/tools/_tool_registry.py:1092` |
| `@register_tool_category(cat)` sets `func._tool_category` + `func._tool_category_first_party` | `daemon/tools/_tool_registry.py` (`register_tool_category`) |
| AST scanner resolves a `CATEGORY_MODULES` value as `daemon/tools/<parts[2:]>.py`; a `daemon.services.*` value is **never** discovered | `daemon/tools/_tool_registry.py:626-681` + the registry's own comment block above `"self-alarm"` |
| The three-leg seam: (1) decorator `@register_tool_category` + registry entries, (2) `CATEGORY_MODULES` entry, (3) **construction extend** inside `create_instance_tools` | `daemon/tools/instance.py:5958-6028` ("this extend is the third step of the three-step registration seam") |

**The "3-leg registration" is confirmed** — and it is 3 *legs* with **4** registry touch-points:
`DYNAMIC_TOOL_NAMES` + `CATEGORY_MODULES` + `PRIVILEGED_TOOL_CATEGORIES` + the
`create_instance_tools` extend (the `KNOWN_TOOL_NAMES` inventory is a 5th, regenerated
separately). The ari-tools phase-3 commit `f585adea8` is the canonical worked example:
it added `view_write` + `board_link` to `DYNAMIC_TOOL_NAMES` **and** `KNOWN_TOOL_NAMES`,
added two `CATEGORY_MODULES` entries, added two `PRIVILEGED_TOOL_CATEGORIES` entries, and
extended `create_instance_tools` — all in one commit.

**Placement rule (load-bearing).** The extend in `create_instance_tools` **must precede**
`create_help_tool` (which snapshots the tool list into its index), `scan_tools_for_full_docs`,
and `_apply_tool_filter` (which applies the per-agent allow list **and** the privileged-category
default-deny strip). A later extend is either invisible to the help tool or **bypasses the
privileged strip** (`daemon/tools/instance.py:5950-5957` and the three identical comments
above the job/scheduling/board/self-alarm extends).

### 2.2 What makes a category privileged; the fail-closed gate (V2)

* Privileged categories live in the `PRIVILEGED_TOOL_CATEGORIES` frozenset
  (`daemon/tools/_tool_registry.py:290`). A privileged category **never joins the
  empty-allow default-grant universe**.
* The strip sites are `_strip_privileged_category_tools` (default-deny strip) and the
  `resolve_tool_filter` empty-allow branch (`daemon/tools/instance.py`, cited in the registry
  comments at `:6169` and `:373`).
* **`TOOL_REQUIRED_AGENTS` (`daemon/tools/_auth.py:35`) is NOT the privilege mechanism.** It is
  the *spawn* map: "category → backing agents that tools in that category must spawn". It has
  5 entries today (`knowledge`, `chart`, `image`, `council`, `design`). `instance_ops` spawns
  nothing ⇒ **no `TOOL_REQUIRED_AGENTS` row** (leader D3).
* **The category gate alone is NOT sufficient.** A *literal tool name* in an allow list is
  added verbatim and bypasses the category gate outright. Hence the **second, in-body agent
  check** — the ari-tools precedent (`daemon/tools/views_auth.py:40-70`, the "layer 3" doctrine)
  and the board precedent (`daemon/tools/board_tools.py:105-140`). `instance_ops` follows the
  same two-layer pattern: privileged category **+** in-body `agent_id` gate.

### 2.3 The allow-list format and current holders (V3)

`agents/maintenancer/meta.json` (verbatim):

```json
"tools": {
  "allow": ["system-log", "ens-db", "knowledge", "system_upgrade", "system_restart", "db"],
  "deny":  ["git_commit", "edit_file", "write_file"]
}
```

`agents/ari/meta.json` holds **32** allow entries today (including `board`, `self-alarm`,
`view-write`, `board-link` — the ari-tools set) and denies
`["edit_file", "write_file", "watch_job", "watch_jobs"]`.

The maintenancer already holds `system_upgrade` — which is what makes the D4 provenance
reuse free (no privilege surface widens).

### 2.4 The audit situation (V4) — **there is NO generic audit table**

| Precedent | What it actually is |
|---|---|
| `event` table | **NOT an audit table.** An SSE fan-out store; `JobFeedbackObserver` filters it on `event_type != "instance_lifecycle"`. Not queryable as a before/after ledger. |
| `board_*` (ari-tools P1) | **NO audit table at all.** Its trail is `board_notes` (append-only work notes) — a workflow trail, not an actor/target/before/after ledger. |
| `self_alarm` (ari-tools P2) | **NO audit table.** |
| `view_write` / `board_link` (ari-tools P3) | **NO audit table.** |
| `secrets_audit` (`daemon/repositories/secrets/models.py:310`) | A **domain-specific** append-only log for secret *resolution* — right shape, wrong domain. |
| `repair_log` (`daemon/repositories/ens_db/models.py:44`, `__tablename__` at `:94`) | The ens-db lane's audit table. **The house shape precedent** — see D-2. |
| `restart_audits` | **File-backed**, a JSON dict inside `releases/state.json` (`daemon/tools/upgrade_journal.py:454-460`) — the upgrade lane's audit, not a DB table. |
| `maintenance_runs` (`daemon/repositories/maintenance_runs/models.py:132`) | Has `dry_run_run_id` — a **soft link** from a preview run to its commit run. Useful *link pattern*. |

⇒ **A new `instance_ops_audit` table is required.** Rationale in `decisions.md` D-2.
**No dry-run row is currently distinguishable from a real row in any existing precedent
except `repair_log`'s `dry_run` bool + `sql_class`/`outcome` enums** — the new table copies
that discrimination.

### 2.5 The privileged pin files (V5) — **7 exact-set files, 9 assertion sites**

Derived by the canonical grep recipe (from the `f585adea8` commit message), re-run at HEAD:

```
grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ --include=*.py
grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ --include=*.py
```

| # | File | Sites | Marker |
|---|---|---|---|
| 1 | `tests/integration/test_service_tool_flag_off_byte_identical.py` | `:823` exact-set | `@pytest.mark.integration` — **deselected by the repo default addopts**; must be run with `-m integration` or it is a silent no-op |
| 2 | `tests/integration/test_maintenancer_spawn_resolves_tools.py` | `:344` exact-set | no marker — default selection |
| 3 | `tests/unit/test_board_registration.py` | `:176` exact-set (via `EXPECTED_PRIVILEGED_SET` at `:68-81`) + `:187` `len == 12` | default |
| 4 | `tests/unit/test_self_alarm_registration.py` | `:184` exact-set (via `EXPECTED_PRIVILEGED_SET` at `:74-87`) + `:187` `len == 12` | default |
| 5 | `tests/unit/tools/test_secrets_registration.py` | `:226` exact-set | default |
| 6 | `tests/unit/tools/test_upgrade_registration.py` | `:152` exact-set | default |
| 7 | `tests/unit/tools/test_attestation_registration.py` | `:189` exact-set | default |

Plus **one non-test assertion site** that pytest cannot see:

* `agents/maintenancer/ROLLOUT.md:51` —
  `test "$(jq -r '.tools.allow | join(",")' agents/maintenancer/meta.json)" = "system-log,ens-db,knowledge,system_upgrade,system_restart,db"`
  — an **exact ORDERED string** assert. Adding a 7th allow entry turns it RED with no test
  runner involved. It is a **runbook**, not a prompt artifact (0 rows in
  `agents/.prompt-artifacts.sha256`), but it still must be edited.

**Counts and exact ordered lists are the norm — a missed pin breaks SILENTLY** (a frozenset
equality that no longer matches simply fails; but a *count* pin that is merely updated without
the *set* pin updated produces a set/count divergence that passes one and fails the other).

### 2.6 The deliberate-pause tombstone (V6) — **VERIFIED, and it is in `instances.metadata`**

`daemon/services/question_pause_set.py`:

| Function | Line | What it does |
|---|---|---|
| `mark_deliberate_pause_override(manager, instance_id)` | `:608` | Writes the **C2 tombstone** into `instances.metadata["question_paused"] = {"deliberate": True, "cleared_at": "<iso>"}` via `repo.set_metadata(instance_id, QUESTION_PAUSED_MARKER_METADATA_KEY, payload)`. **The tombstone (not the absence of the marker) is what the release path defers on.** |
| `read_pause_override(manager, instance_id)` | `:658` | Reads the marker payload. `{"pack_id":…, "paused_at":…}` for a question pause; `{"deliberate": True, …}` for a deliberate one. `None` when there is no marker (a *failed write*). |
| `is_deliberate_pause_override(marker)` | `:687` | `bool(marker) and marker.get("deliberate") is True`. |
| `deliberate_pause_exclusions(manager, asker_id, pack_id)` | `:692` | The recorded instances carrying a deliberate tombstone → **exclusions** passed to the tree-scoped resume cascade, so the cascade does not flip them to `running` and does not re-claim their suspended turns. |
| `resume_instance_cascade_with_exclusions(...)` | `:728` | Cascade-resume **excluding** the deliberate parks. |
| `clear_question_pause_marker(manager, instance_id)` | `:747` | Clears the question-pause marker on a clean release of the QUESTION stratum — **a DELIBERATE tombstone survives.** |

**The brief's belief is CONFIRMED:** `instances.metadata` (JSONB column `metadata` on the
`instances` table — `daemon/repositories/instance/models.py:64-67`) holds the pause-cause
marker. The metadata setter/getter primitives are `repo.set_metadata`
(`daemon/repositories/instance/repository.py:2518`) and `repo.get_metadata_value` (`:2270`).

⇒ **`instance_mark` writes through `repo.set_metadata` under a namespaced key.** The
`park` marker **reuses** `mark_deliberate_pause_override` (an existing primitive). The
`protect` / `suspect` markers are new *metadata values*, not new primitives.

### 2.7 Question answer / dismiss; pause / resume cascade; stale-pause sweep (V7)

| Surface | Location | Reusable by a tool? |
|---|---|---|
| `POST /instances/{id}/answer` | `daemon/routers/instances.py:1088` → delegates to `answer_questions_via_instance` in `daemon/routers/answer_helper.py` | **YES — shared helper.** But it lives under `daemon/routers/`, so a tool module importing it is a **routers→tools dependency inversion** (must be handled — see implementation-plan step S-6). |
| `POST /instances/{id}/question/dismiss` | `daemon/routers/instances.py:1239` — **the body is INLINE in the router** | **NO reusable service function.** `dismiss_question` is not extracted into `daemon/services/`. Reuse requires **extracting the router body into a service function** (an additive extraction, not a behaviour change) — see implementation-plan S-6b. **This is the single largest reuse gap in the commission.** |
| `GET /instances/{id}/question` | `daemon/routers/instances.py:1146` | read of the in-memory pack |
| `POST /instances/{id}/pause` / `/resume` | `daemon/routers/instances.py:677` / `:708` | already exposed as **tools** in the `instance` category (`pause_instance`, `resume_instance`) — see `daemon/tools/instance.py` |
| `stale_pause_sweep` | `daemon/services/stale_pause_sweep.py:326` (`sweep_stale_pauses`), `:464` (`StalePauseSweepService`) | **Composes into `instance_health`** (§6) |

**Question packs are IN-MEMORY.** `daemon/services/question_manager.py:223-226`:
`self._packs: dict[str, QuestionPack] = {}` — an in-process dict behind a `threading.Lock`.
There is **no `questions` table** (the DB has no questions table — the `SQLModel` table list
under `daemon/models/` is `app_settings`, `board_items`, `board_notes`, `board_item_tags`,
`resource_daemon_bucket`, `resource_instance_sample`, `resource_pid_map`,
`resource_limit_policy`). The pack therefore **does not survive a daemon restart** — the
`410 QUESTION_PACK_LOST` error in the answer route's contract is the honest rendering of
that. `instance_answer` / `instance_dismiss` must return `410 QUESTION_PACK_LOST` when the
pack is absent post-restart, and must not fabricate.

### 2.8 Revive-from-checkpoint and arbitrary-instance message injection (V8)

| Primitive | Location | Verdict |
|---|---|---|
| Revive-on-send (terminal → RUNNING, reuse checkpoint) | `daemon/services/instance_messaging.py` — `_prepare_enqueued_message` (`:2001`) auto-resumes `IDLE`/`WAITING_CHILDREN`/`COMPLETED`; the terminal-state revive path is the shared `_prepare_enqueued_message` route documented at `:1522-1540` | **EXISTS** — but it is **message-driven** (a revive is the side effect of delivering a message) |
| Silent checkpoint continuation (no HumanMessage) | `daemon/services/auto_continue_boot_pass.py` → `_schedule_explicit_handle_resume(silent=True)` (`daemon/manager.py:12433`) | **EXISTS** — used by the boot auto-continue pass |
| `resume_instance` tool | `daemon/tools/instance.py` (the `instance` category) | Resumes **WORK** (a paused/stranded turn). It is **not** a reclaim of a dead instance, and it explicitly refuses when a question pack is pending. |
| Message injection into an arbitrary instance id | `send_message` tool (`daemon/tools/instance.py:3727`) → `manager.set_injection` (live target) or `manager.enqueue_message` (durable) | **EXISTS** — `instance_nudge` wraps this. |

⇒ **`instance_reclaim` as originally specified was NOT IMPLEMENTABLE (GT-5).**
`_schedule_explicit_handle_resume` requires a **pre-computed suspension handle**
(`target_work_id`, `handle_work_id`, `selected_suspension_reason`, `route_outcome` —
`daemon/manager.py:12433-12444`); those are outputs of the **boot pass's selector**
(`daemon/services/auto_continue_boot_pass.py`), not of the primitive. A tool taking a bare
`instance_id` would have to re-implement that selector — a new primitive by D-3's own
definition, in the lane where a wrong selection silently resumes the wrong turn.

**D-20 (leader overrule of the architect's cut) REDEFINES the tool instead:**
`instance_reclaim(id, reason)` is a **TERMINAL-ONLY, REASON-REQUIRED, REVIVE-BY-DOCUMENTED-
MESSAGE** wrapper over the existing revive-on-send primitive
(`daemon/services/instance_messaging.py:2365-2387`: a message to a `COMPLETED` / `TERMINATED` /
`ERROR` / `FAILED` instance flips it to `RUNNING` and reuses the checkpoint). It refuses on
non-terminal targets, refuses self-surgery, and writes its audit row. The **silent
checkpoint-restore** semantics stay **DEFERRED** (DF-1 unchanged) — the architect is right
that the silent primitive is not wrappable.


### 2.9 The LIVENESS model (V9) — **there is no instance-level `liveness` field**

* **Instance-level:** the `instances` table has `status` (10-value enum,
  `daemon/repositories/instance/models.py:20-31`: `idle`, `running`, `waiting`, `paused`,
  `completed`, `error`, `terminated`, `queued`, `waiting_children`, `failed`) and
  `last_activity_at` (`:72`). There is **no `liveness` column and no `liveness` property**.
  The closest canonical sets are `ALIVE_INSTANCE_STATUSES` (5 members: `idle`, `running`,
  `paused`, `queued`, `waiting_children` — `daemon/constants.py:720` doc + the frozenset
  below it) and `TERMINAL_INSTANCE_STATUSES` (4: `completed`, `terminated`, `error`, `failed`).
* **Mission-level:** `liveness` **is** a first-class field — but it belongs to the **mission
  projection**, not to instances. Its canonical value space is
  `MISSION_LIVENESS_FILTER_VALUES` (`daemon/services/mission_resolver.py:141-153`) =
  `{pending, processing, paused, completed, failed, cancelled}` (the `_STATUS_CANONICAL_MAP`
  targets minus `dead_letter`, which is a `terminal_reason`, never a liveness). The rendered
  wire value may be the distinct string
  `"completed (gate escalated — unverified)"` (`daemon/tools/missions.py:249-269`).

⇒ ~~**`instance_list(liveness=?)` must define a DERIVED, instance-domain vocabulary.**~~
**SUPERSEDED by D-19 (Q6 verdict: REJECT the third vocabulary).** The original
`alive | stalled | terminal` derivation **conflated the liveness axis with the time axis and
erased exactly the park/wedge distinction the commission exists to preserve**: under the
original derivation, a deliberately parked instance (`paused` + the D-11 `park` tombstone)
has a frozen `last_activity_at`, so past the staleness threshold it reported `stalled`,
byte-identical to a genuinely wedged one. The `park` marker exists precisely to make that
distinction; the derived vocabulary deleted it.

**The D-19 replacement is an orthogonal field model — no new vocabulary is minted (the
original "three orthogonal dimensions" phrasing under-counted; the list below has four
items because `pause_marker` is reported alongside):**
1. **Filter on the raw `status`** (the instance's own 10-value domain) — **SQL-side via the
   existing `repo.list(status=...)`** (GT-3). The instance domain's native vocabulary; no
   translation, no drift, no third enum.
2. **Report canonical `liveness` as a derived FIELD, never a filter** — computed from
   `_STATUS_CANONICAL_MAP` (`daemon/services/work_status.py:67`, documented as *"the single
   source of truth — no parallel vocabulary set is maintained"*), which already covers the
   full `InstanceStatus` domain and already has an instance-domain-aware inversion helper
   (`mission_resolver._instance_statuses_for_liveness`, `daemon/services/mission_resolver.py:157`).
   **Reuse, do not restate.**
3. **Report staleness as a separate, explicitly-named field** — `idle_seconds: int | null`
   plus `stale: bool` against the caller-supplied threshold, using the **lineage-MAX**
   aggregate (R4). **Never fold it into the liveness enum.**
4. **Report `pause_marker` alongside** — so a `paused` row reads as *"deliberately parked,
   4h idle"*, not *"stalled"*.

A caller wanting the old `alive`/`stalled` can compute it client-side from `status` + `stale`
in one line. The typed `unknown-liveness-value` refusal **survives unchanged** for anyone
passing a non-canonical liveness value into a `liveness` parameter (which is now a
**returned field's** value space, not a filter) — see §5.1.


### 2.10 The wedge detectors that ALREADY EXIST (V10)

| # | Detector class | Existing implementation | Composes? |
|---|---|---|---|
| 1 | **stale-running** | `daemon/services/stale_task_recovery.py` (`StaleTaskRecovery` — finds stale tasks, sets `cancel_requested`, waits grace, force-cancels, reaps — per `agents/maintenancer/knowledge/05-repair-runbooks.md:29`); `daemon/services/timeout_monitor.py` | **YES** |
| 2 | **waiting-on-a-dead-parent** | `daemon/services/waiting_children_watchdog.py` (enumerates `WAITING_CHILDREN` instances, finds non-terminal children with stale `last_activity_at`, skips `paused` children/parents, delivers a hang notice); `daemon/services/mission_strand_healer.py:110` (`MissionStrandHealer` — periodic heal of stranded mission trees) | **YES** |
| 3 | **question-paused-without-task-row** | `daemon/services/stale_pause_sweep.py:326` — the selector admits a paused instance with no live turn; a `pending`/`running` task means the pool owns the turn, a `paused` task is the question-pause side-effect shape | **YES** |
| 4 | **zombie pending** | `daemon/services/job_recovery_service.py:387` (`JobRecoveryService`) — `recover_on_startup` (`:522`), `reconcile_drift_states` (`:925`), `_is_instance_alive` (`:496`), `_fail_orphaned_job` (`:697`); `daemon/services/eligible_pending_sweep.py`; `daemon/services/job_lock_sweep.py` | **YES** |
| 5 | **orphans** | `daemon/services/orphan_watcher_sweep.py:85` (`OrphanWatcherSweepService`); `daemon/services/auto_continue_boot_pass.py` (boot-time orphan handling); `daemon/services/boot_subprocess_sweep.py` | **YES** |
| 6 | **failed-with-unreported-result** | `daemon/services/report_delivery_recovery.py`; `daemon/services/report_delivery_ledger.py`; `daemon/services/report_integrity_guard.py`; `daemon/services/child_reports.py`; `daemon/services/error_reporting.py` | **YES** |

**All six classes already exist as sweeps — but (GT-4) they expose NO reusable read-only
predicates.** The original §2.10 "Composes? YES" column **was not supported by the tree**:
`job_recovery_service._is_instance_alive` / `_fail_orphaned_job` are private (the one public
method, `reconcile_drift_states:925`, is a *reconciler*, i.e. **mutating**);
`stale_pause_sweep._owner_is_alive` / `_has_live_descendant` are private module functions;
`waiting_children_watchdog` exposes counters and `wedge_episodes()` (the already-notified
set) plus a **destructive** `run_once()`; `orphan_watcher_sweep` delegates to
`bus._sweep_orphan_watchers` — a private, destructive UPDATE; `report_delivery_ledger`
exposes one public function; `report_delivery_recovery`'s four lanes are all private and all
*recover*. **There is nothing to compose.**

**D-19 replaces composition with reporting predicates + disagreement pins:**
* **Extract nothing.** Do not refactor six live sweep modules to expose predicates — a far
  larger behaviour-surface change than this commission should make, and it would risk the
  sweeps' own carefully-tuned epistemology (their per-sweep fail-open vs fail-closed choices).
* **Write the detectors in the new module as REPORTING predicates** — cheap, read-only, and
  explicitly labelled as a projection.
* **Add a DISAGREEMENT PIN, not a verdict-equality pin.** For each detector whose sweep
  counterpart is observable, a test constructs a fixture the sweep itself classifies (via its
  own public entry point where one exists — e.g. `OrphanWatcherSweepService.counters()`,
  `WaitingChildrenWatchdog.wedge_episodes()` — or via the sweep's documented classification)
  and asserts **the health report flags the same set**. Two independent code paths must agree
  on a labelled corpus. Where **no observable counterpart exists**, the detector's row in the
  table says so and the pin is **skipped** — an honest gap, not a pretend one.
* **Keep the R4 lineage-MAX rule hard and tested** (`get_max_last_activity_in_instances`,
  `daemon/repositories/instance/repository.py:670`) — the one place where the new detector's
  semantics are already settled by the tree and must not be re-derived.

### 2.11 Existing fleet / health surfaces (V11)

* **NOT FOUND — no fleet / health / wedge summary HTTP route.** `daemon/routers/` contains 42
  route modules; grepping the `GET` decorators for `health|overview|summary|stats|fleet|census`
  yields exactly one hit, and it is unrelated
  (`daemon/routers/skills.py:1423` — `GET /{skill_id}/ab-test/stats`). There is no
  `/health/fleet`, no `/instances/health`, no `/instances/summary`. ⇒ `fleet_capacity_summary`
  is genuinely new surface.
* `GET /instances` (`daemon/routers/instances.py:362`) supports
  `limit, offset, project_id, exclude_kb, include_descendants, search, order, source`.
  The **route** has no `status` filter and no `agent` filter — **but the REPOSITORY does have
  a SQL-side `status` filter** (GT-3): `SQLModelInstanceRepository.list(status=...)`
  (`daemon/repositories/instance/repository.py:1008-1014`, applied in SQL at `:1135-1137`),
  which threads up with **one additive parameter** on the manager/lifecycle facade. ⇒
  **`status` is filtered SQL-side** (D-13-amended); the remaining filters (`agent`,
  `parent_id`, `stale_minutes`) are **client-side over the returned bounded page** — which is
  now correct, because the status predicate no longer lies about what it returned.
* **GT-1 correction — the maintenancer does NOT hold the `instance` category.** Its allow is
  exactly the six entries in §2.3. The existing `list_instances` tool is a **10-row
  pinned-first window** (`DEFAULT_PAGE_LIMIT = 10`, `daemon/constants.py:16`), fleet-wide
  (`project_id=None`), with no filters and no count — not a census. **The reads are a genuine
  gap for the holder**, not per-agent duplication.
* **GT-2 correction — the LEADER holds the entire `instance` category, including an
  UNGOVERNED `terminate_instance`** (`daemon/tools/instance.py:5493-5503`, nine lines: no
  dry-run, no reason, no audit, no protected-target gate, no kill-switch). Every empty-allow
  agent (e.g. `agents/watcher/meta.json` → `"allow": []`) also reaches it through the
  default-grant universe. **Fleet-wide ungoverned terminate already exists; this commission
  adds one governed path and does not remove the ungoverned one** → **R21**.
* `InstanceInfo` (`daemon/models/instance.py:36`) carries `instance_id`, `agent_id`,
  `project_id`, `agent_dir`, `status`, `title`, `initiative_message`, `parent_id`,
  **`children: list[str] | None`** (populated from `list_child_ids_permanent()` — a **list of
  child ids**, *not* a count), `mcp_tool_names`, `created_at`, `updated_at`,
  **`pending_count`**, `pinned`, `color_tag`, `icon_tag`, `pinned_at`, `agent_tag`, `model`,
  `watchover_*`.
* **NOT FOUND — no `children_count`, no `current_task`, no `waiting_on` field** on the
  `instances` row or on `InstanceInfo`. The task row lives in the separate `task` table
  (`daemon/repositories/task/models.py:169`); the job row in `job_queue_items`.
  "Waiting-on" for a parent is **implicit** in `status == "waiting_children"` — there is no
  denormalised "waiting_on_child_id" column. ⇒ `instance_list`'s "children count" is
  `len(children)`; **`current_task` and `last_messages` are HYDRATION fields and belong to
  `instance_get`, not `instance_list`** (Q1).
* **§0(b) correction — the existing `get_instance_info` TOOL returns the repository
  `Instance.to_dict()`** (`daemon/repositories/instance/models.py:128-154`), which includes
  the **raw `metadata` blob** and **lacks** `pending_count` / `pinned` / `mcp_tool_names` /
  `watchover_*` (those are HTTP-model fields). The `instance_get` contract below is written
  against the tool's **real** shape; the **metadata redaction** the contract adds is a
  genuine improvement the HTTP model does not make.


---

## 3. Scope

### In scope

1. **Two new privileged tool categories (D-13):** `instance_read`
   (`daemon/tools/instance_read.py`) and `instance_ops` (`daemon/tools/instance_ops.py`),
   together holding **10 tools**.
2. Four **read** tools (`instance_list`, `instance_get`, `instance_health`,
   `fleet_capacity_summary` — renamed from `instance_fleet_summary` per Q1).
3. Six **act** tools (`instance_terminate`, `instance_reclaim`, `instance_nudge`,
   `instance_mark`, `instance_answer`, `instance_dismiss`).
4. One new `instance_ops_audit` SQLModel table + its own `.sql` migration.
5. Shared-code touch points, all additive: the registry allow-list surface (to add two
   categories), the `create_instance_tools` construction extends, **the D-14 provenance
   neutral home + delegating alias**, **the D-15 Tier-1 service hooks** (`audit_sink`, default
   `None`; `session=`, default `None`), and the D-15b dismiss extraction (only because the
   router handler is not importable as-is — §1.6 preference order).
6. `agents/maintenancer/meta.json` — add **`"instance_read"` and `"instance_ops"`** to
   `tools.allow`.
7. `agents/maintenancer/tools_note.md` — agent-POV capability table.
8. `agents/ari/workflow.md` — the patrol convention (agent-POV).
9. The pin ceremony: 7 exact-set pin files + 1 new registration pin +
   `agents/maintenancer/ROLLOUT.md:51` + the inherited 3-red integration pin — **all in ONE
   commit**.
10. `agents/.prompt-artifacts.sha256` re-stamp.

### Out of scope

* Any Ari tool (D1). Any leader access (D2).
* Any new detection algorithm, sweep, or recovery primitive.
* A second nonce regime (D4).
* A generic cross-category audit framework. This commission establishes an audit table **for
  `instance_ops` only** (R5).
* UI/FE surface, HTTP routes for the new tools, or API-key scoping changes.
* Fixing the inherited user-origin forge path (R1) — recorded, not fixed.
* Any change to existing callers' behaviour. **The only shared-code change is the
  registry/allow-list surface needed to add a category** (+ the additive extraction of the
  dismiss body into a service, which preserves the router's behaviour byte-for-byte).

---

## 4. The ten tool contracts

Every tool returns a **string** (house rule: tools never raise — `daemon/tools/board_tools.py`
module docstring, "Tools NEVER raise — every failure returns an `"ERROR: {msg}"` string").
The refusal shape for act tools is the upgrade lane's
`f"Error: {label} REFUSED — reason={token}: {message}"`
(`daemon/tools/upgrade_tools.py:1844-1852` `_refusal`).

**Common argument conventions.** `id` = instance id (accepts the short form; the existing
`_resolve_instance_id(manager, instance_id)` helper at `daemon/tools/instance.py` resolves
typos — reuse it). `dry_run` defaults **TRUE** on every destructive act. `reason` is
**required** on every act (D5).

**Common refusal tokens** (house vocabulary, drawn from
`daemon/tools/upgrade_tools.py` and the ens-db lane):

`confirmed-target-mismatch` · `no-user-provenance` · `direct-provenance-insufficient` ·
`protected-target-confirmation-required` · `reason-required` · `self-surgery-refused` ·
`cascade-false-would-orphan-descendants` · `instance-not-found` · `not-permitted` ·
`unknown-status-value` · `terminate-budget-exceeded` · `target-too-young` ·
`instance-ops-disabled` · `question-pack-lost` · `no-pending-question` ·
`stale-question-pack` · `audit-write-failed` · `marker-already-set` · `invalid-marker` ·

> **[DF-4 CORRECTION — 2026-10-22]** `direct-provenance-insufficient` is **RETIRED**
> (DF-4 / P2). The class-keyed refusal is gone: the protected-target gate now keys on
> the **LIVENESS** of the user-origin window, so `no-user-provenance` is the **only**
> provenance refusal token. The token list above is kept verbatim as history — the
> authoritative history is `decisions.md` D-18 and its 5-step retraction trace
> (`decisions.md:666-684`). See
> `.agents/shared/planning/df4-provenance-closure/plan-overview.md`.
`target-not-terminal`.

*(Amended by review A1/A8: superseded token `user-confirmation-missing` REMOVED — D-17
replaced it. `target-not-protected` REMOVED — it had no firing site: no tool refuses because
a target is NOT protected; protection only ADDS conditions. `unknown-liveness-value` renamed
`unknown-status-value` per D-19 — `liveness` is a returned field, `status` is the filter.)*

---

### 4.1 `instance_list` — fleet census (READ, category `instance_read`)

**Shape per Q1/D-19 (amended): CENSUS ONLY — the hydration fields
(`current_task`, `last_messages`) are CUT from the row shape and belong to `instance_get`.**
Cheap census, expensive detail.

| Arg | Type | Default | Semantics |
|---|---|---|---|
| `status` | `str \| None` | `None` | Exact `InstanceStatus` value (10-value enum). **FILTERED SQL-SIDE** via the existing `repo.list(status=...)` (`daemon/repositories/instance/repository.py:1008-1014`, `:1135-1137`) threaded up with one additive parameter. **This was the original L-2 defect** — a Python-side status filter past one page silently returns an arbitrary pinned-ordered subset. |
| `limit` | `int` | `50` | Max rows returned (clamped to `[1, 200]`). |
| `offset` | `int` | `0` | SQL-side offset. |

**Server-side filter: `status` only.** The remaining dimensions are **returned fields** the
agent filters client-side on the bounded JSON — correct now, because the status predicate no
longer lies about what it returned:

| Filter | Mechanism |
|---|---|
| `agent` | client-side predicate over the returned rows (exact `agent_id`) |
| `parent_id` | client-side (`None` = no filter; `""` matches roots) |
| staleness | client-side over the returned `stale` / `idle_seconds` fields |

**Returns** — one JSON string, `{"rows": [...], "count": N, "total_matching": M, "truncated": bool, "filters_applied": {...}}`.
Each row (census shape — **no `current_task`, no `last_messages`**):
`instance_id`, `agent_id`, `agent_name`, `status` (raw 10-value), `liveness` (canonical
mission vocabulary via `_STATUS_CANONICAL_MAP` — **a returned field, never a filter**),
`parent_id`, `children_count`, `age_seconds`, `idle_seconds`, `stale` (bool, against the
caller's threshold), `pause_marker` (`null` \| `{"deliberate": bool, "pack_id":…,
"paused_at":…}`), `pending_question_pack` (bool — cheap RAM-dict lookup), `pending_count`,
`project_id`, `title`, `waiting_on` (`null` | `{"kind": "children"}`).

**Side effects:** NONE. Read-only, no audit row (audit rows are for acts only).

**Refusals:** `invalid-limit`, `unknown-status-value` (a non-`InstanceStatus` value is a
typed refusal, not a silent empty page).

**Rationale for the split (Q1):** a census over 200 rows must not hydrate `current_task`
(a task-table join) or `last_messages` (a checkpoint read) per row. Those are paid for on
**one** instance in `instance_get`. The two tools become **complementary**, not nested.


---

### 4.2 `instance_get` — deep detail (READ, category `instance_read`)

**The HYDRATION tool (Q1)** — it earns its slot by paying for exactly what a census must not.

**Args:** `id: str`.

**Returns** — one JSON string with: `instance_id`, `agent_id`, `agent_name`, `agent_tag`,
`status` (raw), `liveness` (canonical), `instance_metadata` (**REDACTED safe subset** — never
the raw blob; the existing `get_instance_info` tool returns the raw
`Instance.to_dict()` including the full metadata, so this is a genuine improvement),
`project_id`, `parent_id`, `children` (ids), `children_count`, `ancestors` (root→parent chain),
`descendants` (ids), `created_at`, `updated_at`, `last_activity_at`, `age_seconds`,
`idle_seconds`, `stale`, `paused_at`, `pause_marker`, `pending_question_pack` (**the pack
object**, not just a bool), **`current_task`** (the task-table row, hydrated here — NOT in the
census), **`last_messages`** (last N, bounded — hydrated here), `spawned_by` (parent id + the
spawning agent), `mission_ref` (liveness + id when a mission projection exists).

**Side effects:** NONE.

**Refusals:** `instance-not-found`.


---

### 4.3 `instance_health` — wedge report (READ, category `instance_read`)

**Args:** `stale_minutes: int = 30`.

**Returns** — one JSON string:
`{"stale_minutes": N, "detectors": [{"detector": "<name>", "instances": [{"instance_id", "agent_id", "status", "idle_seconds", "pause_marker", "detail"}], "count": N, "disagreement_pin": "<sweep-counterpart | null>"}], "total_flagged": N}`.

**D-19 amendment — REPORTING predicates, not composition.** GT-4 established there is nothing
to compose. Each detector is written in the new module as a cheap, read-only, explicitly
labelled **projection**, and its agreement with the corresponding sweep is **pinned** by a
disagreement test (two independent code paths must agree on a labelled corpus). Where no
observable sweep counterpart exists, the detector's row says `"disagreement_pin": null` — an
honest gap, not a pretend one.

**Critical caveat (R4):** `last_activity_at` **freezes** on a `waiting_children` parent while
a descendant streams mid-LLM (`daemon/repositories/instance/repository.py:670-700`). A naive
stale-running detector therefore **false-positives on every live parent**. The stale-running
detector must aggregate `MAX(last_activity_at)` across the **permanent lineage**
(`get_max_last_activity_in_instances` — the existing primitive,
`daemon/repositories/instance/repository.py:670`) — the one place where the new detector's
semantics are already settled by the tree and must not be re-derived. **A per-row staleness
check is forbidden.**

**Side effects:** NONE. **A health report never triggers a repair.**

**Refusals:** `invalid-stale-minutes`.


---

### 4.4 `fleet_capacity_summary` — "how's the nest?" (READ, category `instance_read`)

**Renamed from `instance_fleet_summary` (Q1 verdict — cosmetic but cheap while the category
is unbuilt).** The architect's finding: the tool's content is **~⅔ system capacity**
(`queue_depth`, `worker_pool`, `dlq_depth`), not instances — the original name mislabels it.
The maintenancer holds no queue/pool/DLQ lens today (GT-1), so this is a genuine gap for the
holder.

**Args:** none.

**Returns** — one JSON string:
`{"counts_by_agent": {...}, "counts_by_status": {...}, "counts_by_liveness": {...},
"queue_depth": [{"queue_name", "pending", "active", "dead"}],
"worker_pool": {"size", "busy", "idle", "saturated": bool},
"dlq_depth": N,
"audit_pending": {"count": N, "oldest_age_seconds": N},
"generated_at": "<iso>"}`.

**W4 amendment — `audit_pending` (Tier-2 reconciliation surface).** "a sweeper can
reconcile" was under-specified: the six detectors cover no audit ledger, and a sweeper would
be a **new primitive** (D-3 forbids). The cheap fix is this summary field: a count of
`instance_ops_audit` rows stuck at `outcome="pending"` plus the age of the oldest. **When a
row is stuck `pending`, the act's TRUE outcome must be read from INSTANCE STATE, not from
the ledger** — the row records the *intent*, the instance records the *fact*. Operator SQL:

```sql
-- What is stuck, and for how long?
SELECT action, target, reason, created_at,
       EXTRACT(EPOCH FROM (now() - created_at)) AS age_seconds
FROM instance_ops_audit
WHERE outcome = 'pending'
ORDER BY created_at ASC;

-- Did the act actually happen? (terminate example — compare the ledger against the row)
SELECT i.instance_id, i.status, i.updated_at, a.outcome AS audit_outcome, a.created_at AS audit_at
FROM instance_ops_audit a
LEFT JOIN instances i ON i.instance_id = a.target
WHERE a.outcome = 'pending';
```

Sources: instance rows (status/agent); `job_queue_items` grouped by queue + admission state
(the 4-value `AdmissionState`: `QUEUED`/`ACTIVE`/`DONE`/`DEAD`); `WorkerPool.get_stats()`
(`daemon/services/worker_pool.py:1518`); the dead-letter queue depth
(`daemon/services/dead_letter_service.py`, `daemon/routers/dlq.py`).

**Side effects:** NONE.


---

### 4.5 `instance_terminate` — ACT (destructive, category `instance_ops`)

**Args:** `id: str`, `cascade: bool = True`, `reason: str` (**required**),
`dry_run: bool = True`, **`confirmed_target_id: str | None = None`** (D-17 — replaces
`user_confirmed: bool`), `allow_orphan_descendants: bool = False`.

**D-17 — the confirmation NAMES the target it authorizes.** The gate requires
`confirmed_target_id == id` **verbatim**. This is not a semantic binding (the agent can
still assert falsely) but it converts an unfalsifiable boolean into an **attributable,
audited assertion**: the agent must *name the target it believes the user consented to*, the
refusal path teaches the correct shape, and a post-hoc reviewer comparing
`confirmed_target_id`, `reason`, and the user's `root_message_id` can detect a false claim.
The audit row records `root_message_id`, `root_source`, `pclass`, `depth`, and
`confirmed_target_id` — all five.

**Semantics.** The underlying service cascade is **implicit and total**:
`InstanceLifecycleService.terminate_instance` (`daemon/services/instance_lifecycle.py:2539`)
snapshots `repo.get_cascade_tree_ids(instance_id)` and terminates every node in the snapshot;
children always get `terminal_reason = "aborted"`. **There is no cascade flag and no
single-instance-only path in the service.** ⇒ `cascade=False` must be implemented in the
**tool layer** as "terminate the target only, leave descendants running", and it **creates
the exact wedge class `instance_health` must detect**. ⇒ `cascade=False` on a target with
live descendants **REFUSES** with `cascade-false-would-orphan-descendants` unless
`allow_orphan_descendants=True` is passed, in which case the dry-run preview **and** the audit
row both **name the descendants that will be orphaned** (D-5).

**Protected-target gate — THREE conditions, ALL required.** A target is protected if
**either**:
1. it is a **user-facing chat root** — `_is_user_facing_root(parent_id=…, source_type=…)`
   (`daemon/tools/instance.py:2561-2592`), fail-closed on both legs; **or**
2. its `agent_id == "leader"` (the static role rule); **or**
3. it carries a dynamic `protect` marker written by `instance_mark`.

Terminating a protected target requires **ALL THREE**:
* **(a)** `confirmed_target_id == id` (D-17);
* **(b)** a live user-origin verdict from the shared provenance helper
  (`daemon/tools/provenance.py` after D-14);
* **(c)** **`pclass == "inherited"`** (D-18).

> **[DF-4 CORRECTION — 2026-10-22]** Clause **(c) is RETIRED** (DF-4 / P2). The
> protected-target gate now requires **TWO** conditions — (a) the named confirmation
> and (b) a **live** user-origin window — and keys on **LIVENESS, not on the window's
> class**. `pclass` is a read-time derivation that carried no trust signal; it survives
> as **observability only** (the audit rows answer "which window shape authorised this
> act?"). Superseded by `.agents/shared/planning/df4-provenance-closure/plan-overview.md`.
> The original three-clause rule is kept verbatim above as history.

**D-18 — what requiring `pclass == "inherited"` ACTUALLY closes (bounds corrected by review
C1; the original "CLOSES R1" claim is RETRACTED — see `decisions.md` D-18 and
`risk-register.md` R1).** The gate is retained as **defense-in-depth with honestly-stated
bounds**, not as a proof of user intent:

* **What it DOES close — the DIRECT-window-on-the-acting-instance class:**
  * the trivial `localhost` POST with `source="api"` straight at a maintenancer instance (the
    **F2 forgery class**, critical note `fd3689a1`), and
  * a raw direct dispatch to the maintenancer instance.
  Both mint `pclass="direct"` on the acting instance ⇒ refused.
* **What it does NOT close — the DELEGATED forge (R1's real shape).** The verified trace: the
  sweep delivers the promote-kind wake to **`ari_instance_id`**
  (`upgrade_journal_sweep.py:1205`) or the arming instance (`:1724`), **not to the
  maintenancer** — *except when the arming instance itself is a maintenancer (it holds
  `system_upgrade`); that shape mints direct-on-the-acting-instance and falls in the CLOSED
  (refused) class* — ; the funnel stamps a **DIRECT** window on that woken target
  (`daemon/manager.py:9615` → `stamp_user_origin_window:4850`;
  `classify_user_origin("api")` → `exact:api`, `upgrade_journal.py:3391-3392`). Ari then
  delegates to the maintenancer with `source=f"internal_agent:{ari_iid}"`
  (`daemon/tools/instance.py:964-969`, `:3493`, `:4152`, `:4259`) ⇒
  `_inherit_provenance_window` mints `pclass="inherited"`, `depth=1` from the caller's live
  direct window **regardless of whether that root was genuine** (`daemon/manager.py:4914-4990`
  — the mint checks prefix-match, caller-liveness, TTL and the depth cap **ONLY; it never
  inspects the root's provenance**). `root_source="api"` is additionally **ambiguous**: a
  genuine operator POST is also `exact:api`.
  **Consequence: requiring `pclass=="inherited"` ADMITS the delegated forge and REFUSES the
  genuine operator-direct path. The gate's polarity is inverted relative to its original
  justification.** The ROOT fix is **DF-4 / critical note `f4cf9c4f`** (the sweep's
  `or "api"` fallback) — a separate lane, **NOT fixed here**.

> **[DF-4 CORRECTION — 2026-10-22]** The ROOT fix **IS DF-4** and it **HAS LANDED**
> (commission DF-4, release line v0.27.2). The sweep's no-user-origin fallback now rides
> the reserved internal literal `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE`
> (`"system:sweep_no_user_origin_wake"`) instead of the user-origin literal `"api"`, so
> it can no longer mint a forged DIRECT root; and the class-keyed gate clause is retired
> (the gate keys on liveness). The critical note `f4cf9c4f` is **superseded** by
> `.agents/shared/planning/df4-provenance-closure/plan-overview.md`. See also
> `decisions.md:666-684` (the authoritative 5-step retraction trace).
* **Why the gate is still worth keeping (defense-in-depth, four layers):**
  (a) **D-17 attributable confirmation** — the act names its target and the audit records it;
  (b) **D-16 the N=1/24h protected-target budget**; (c) **the audit row records `root_source` +
  `depth` for POST-HOC detection**; (d) the protected static set + the self-surgery refusal +
  the dry-run default + the kill-switch.
  **Stated plainly: this is a SPEED BUMP PLUS AUDIT, NOT A PROOF OF USER INTENT.**

**Disclosed consequence of D-18 (an accepted design, not an accident):** a user message sent
**directly** to a maintenancer instance (an operator POSTing to its instance id via the API)
carries a **direct** window and therefore **cannot** authorize a protected terminate. **That
is intended** — the maintenancer is a **delegated leaf, not user-facing**; a fleet-destructive
act against a protected target should require a delegation chain from a user-facing turn. The
refusal message names the workaround ("route via the leader"). Recorded as **L-9**.

> **[DF-4 CORRECTION — 2026-10-22]** **L-9 is RETIRED** (DF-4 / P2). The
> "direct window cannot authorize a protected terminate" property is **retired BY DESIGN**
> — it refused the GENUINE operator-direct path (defect B) while never blocking the
> delegated forge. A live user-origin window of **either** shape now admits the act; the
> window's shape is recorded in the audit ledger but is NOT a gate input. The compensating
> controls for the newly-admitted DIRECT shape are: scopes · acting-instance allow-list ·
> named-target confirmation (`confirmed_target_id`) · the D-16 budget (protected 1/24h,
> fleet 3/24h) · the two-tier audit ledger · the `ENSEMBLE_INSTANCE_OPS_ENABLED` kill
> switch. The REST-ingress class stays OPEN (critical note `257488dc`). See
> `.agents/shared/planning/df4-provenance-closure/plan-overview.md` §Honesty Clause.

**Refusal doc-surface requirement (C1 point 4):** the refusal text for
`direct-provenance-insufficient` must also state that **any non-`internal_agent:` dispatch
clears the inherited window** (a delegated turn arriving on another lane — including
`internal_invoke_and_wait:*`, which deliberately NEVER inherits — therefore REFUSES,
fail-closed; `daemon/manager.py:4914+` docstring), so the agent understands which delegation
lanes can and cannot authorize the act.

> **[DF-4 CORRECTION — 2026-10-22]** The `direct-provenance-insufficient` refusal text is
> **RETIRED** (DF-4 / P2) — the branch that carried it no longer exists, so this
> doc-surface requirement has no surface left to apply to. The surviving refusal text
> (`no-user-provenance`) describes the LIVENESS requirement only. The inherited-window
> clearing semantics it referenced are unchanged and still documented at
> `daemon/manager.py:4914+`.

**Refusals:** `reason-required`, `self-surgery-refused`, `instance-not-found`,
`cascade-false-would-orphan-descendants`, `protected-target-confirmation-required`,
`confirmed-target-mismatch` (D-17), `no-user-provenance`, `direct-provenance-insufficient`
(D-18), `terminate-budget-exceeded` (D-16), `target-too-young` (D-16),
`instance-ops-disabled`, `audit-write-failed`, `not-permitted`.

> **[DF-4 CORRECTION — 2026-10-22]** `direct-provenance-insufficient` (D-18) is
> **RETIRED** — it no longer exists in the product code (DF-4 / P2). The live refusal
> vocabulary for the protected-target gate is: `protected-target-confirmation-required`,
> `confirmed-target-mismatch`, `no-user-provenance`. The list above is kept verbatim as
> history.


---

### 4.6 `instance_reclaim` — ACT (recovery, category `instance_ops`)

**REDEFINED by D-20 (leader overrule of the architect's cut — the architect's reasoning is
recorded in `decisions.md` D-20; the substance is ACCEPTED, the cut is OVERRULED).**

**Args:** `id: str`, `message: str` (**required** — the revival message), `reason: str`
(**required**), `dry_run: bool = True`.

**Semantics.** A **TERMINAL-ONLY, REASON-REQUIRED, REVIVE-BY-DOCUMENTED-MESSAGE** wrapper
over the existing revive-on-send primitive: a message to a `COMPLETED` / `TERMINATED` /
`ERROR` / `FAILED` instance flips it to `RUNNING` and reuses its checkpoint
(`daemon/services/instance_messaging.py:2365-2387`).

* **Refuses on non-terminal targets** — `target-not-terminal`. This is what distinguishes it
  from `instance_nudge` (which delivers to any target): reclaim is *only* the "a dead
  instance is back and usable" operation.
* **Refuses self-surgery** — `self-surgery-refused`.
* **Writes its audit row** — Tier-2 write-ahead (§7.5).
* **The message itself is the revival record** — which answers the architect's "least
  auditable act" criticism: the HumanMessage landing in the revived instance's checkpoint IS
  the durable evidence of what was revived and why, independent of the audit row.

**The SILENT checkpoint-restore semantics stay DEFERRED (DF-1, unchanged).** The architect is
right that `_schedule_explicit_handle_resume` takes a pre-computed suspension handle (GT-5)
and is not wrappable without re-implementing the boot pass's selector. A silent reclaim needs
a new primitive — D-3 forbids it. **10 tools ship, not 8.**

**Side effects:** a revived instance + a delivered message + one audit row (Tier-2).

**Refusals:** `reason-required`, `message-required`, `self-surgery-refused`,
`instance-not-found`, `target-not-terminal`, `instance-ops-disabled`, `not-permitted`,
`audit-write-failed`.

---

### 4.7 `instance_nudge` — ACT (message injection)

**Args:** `id: str`, `message: str` (**required**), `reason: str` (**required**),
`dry_run: bool = True`.

**Semantics.** Inject a message into **any** instance (not just a job root). Wraps the
existing `send_message` tool path (`daemon/tools/instance.py:3727`): live target →
`manager.set_injection`; otherwise → `manager.enqueue_message` (durable). A send to a
terminal instance **revives** it (revive-on-send) — the tool's doc surface must say so.

**Side effects:** a delivered message (possibly reviving the target) + one audit row.

**Refusals:** `reason-required`, `message-required`, `instance-not-found`,
`instance-ops-disabled`, `not-permitted`, `audit-write-failed`.

---

### 4.8 `instance_mark` — ACT (flag write)

**Args:** `id: str`, `marker: str` (one of `park` | `protect` | `suspect`),
`note: str | None = None`, `reason: str` (**required**), `dry_run: bool = True`.

**Semantics.**

* `marker="park"` → writes the **deliberate-pause tombstone** via the existing
  `mark_deliberate_pause_override(manager, instance_id)`
  (`daemon/services/question_pause_set.py:608`) — i.e. `instances.metadata["question_paused"]
  = {"deliberate": True, "cleared_at": "<iso>"}`. This is the **deliberate-pause tombstone
  semantics** the commission named: the answer/dismiss release path defers to it and does
  **not** re-claim the instance's suspended turn.
* `marker="protect"` → writes a namespaced metadata key (e.g.
  `instance_ops.protect`) via `repo.set_metadata`. **Dynamic protection layer** (D-4).
* `marker="suspect"` → writes a namespaced metadata key (e.g. `instance_ops.suspect`) — an
  operator/agent annotation. Non-destructive, non-blocking.
* `marker` values outside the three → refusal `invalid-marker`.
* `marker="protect"` on an already-protected target → refusal `marker-already-set`
  (idempotent refusal, not a silent no-op).

**Un-protect.** There is **no `unprotect` marker in this contract.** A `protect` marker can
only be cleared by an explicit, separately-specified act — **DEFERRED** (the commission's
marker set is `park` / `protect` / `suspect`; adding `unprotect` would let a marker tool
un-protect a statically protected target, which D-4 forbids without a distinct gate).

**Side effects:** one metadata write + one audit row.

**Refusals:** `reason-required`, `invalid-marker`, `marker-already-set`, `instance-not-found`,
`instance-ops-disabled`, `not-permitted`, `audit-write-failed`.

---

### 4.9 `instance_answer` — ACT (question pack)

**Args:** `id: str`, **`answers: dict`** (**corrected by review W5**: the route schema is
`answers: dict` — `AnswerRequest` at `daemon/routers/answer_helper.py:85-109`, helper
signature `:704-712`; **NOT `list[dict]`**), `question_pack_id: str | None = None`,
`resume_message: str | None = None`, `reason: str` (**required**), `dry_run: bool = True`.

**Semantics.** Submit answers to a pending question pack and resume the cascade. Calls
`answer_questions_via_instance` (`daemon/routers/answer_helper.py:704-712`) — the **same
implementation** the HTTP route uses, so the two surfaces cannot diverge.

**NO ROUTER EXTRACTION IS NEEDED (architect finding (α), folded):**
`answer_questions_via_instance(manager, instance_id, answers, question_pack_id, live_hub, …)`
already takes **`live_hub` as a plain parameter** (verified at `:704-712`; the docstring
notes "or `None` in tests"). The tool calls it with `live_hub = getattr(manager, "_live_hub",
None)` — the same object `daemon/api.py:2336` binds to `app.state.live_hub` — and maps the
raised `HTTPException`s to refusal tokens. **No router file is edited for this tool.**

**Error parity is mandatory** — the route's documented contract
(`daemon/routers/instances.py:1088-1130`) must be reproduced by the tool:
`404` unknown instance; `404 NO_PENDING_QUESTION`; `410 QUESTION_PACK_LOST` (pack lost
post-restart — the in-memory store is empty after a restart, `daemon/services/question_manager.py:223-226`);
`400 QUESTION_PACK_MISMATCH` (stale pack id); `410 ANSWER_TARGET_TERMINAL`;
`200 {resume_route: "already_delivered"}` (idempotent duplicate); `503` write-paused.

**Side effects:** the answer, the pack clear, the cascade resume + one audit row.

**Refusals:** `reason-required`, `instance-not-found`, `no-pending-question`,
`question-pack-lost`, `stale-question-pack`, `instance-ops-disabled`, `not-permitted`,
`audit-write-failed`.

---

### 4.10 `instance_dismiss` — ACT (question pack, without answering)

**Args:** `id: str`, `reason: str` (**required**), `dry_run: bool = True`.

**Semantics.** Dismiss a pending question pack without answering; resume the cascade.

**The reuse gap is real, and the preference order is explicit (leader instruction C +
architect finding (β)):**
1. **PREFERRED — import the router's helper directly, no router edit.** A router edit is the
   only thing that exposes the route/decorator binding trap (critical note `ed2dfe67`).
   **Verification result: NOT importable as-is.** `dismiss_question(instance_id, request)`
   (`daemon/routers/instances.py:1239`) takes a `request: Request` and reaches into
   `request.app.state.live_hub` at `:1330` — not self-contained.
2. **Therefore — the extraction happens**, scoped **honestly** per the architect's warning:
   the body at `:1239-1460` is ~200 lines that (i) reach into `request.app.state.live_hub`
   for an F3-ordered SSE emit that **must fire BEFORE the resume cascade** (`:1330-1335`),
   (ii) raise `HTTPException` with `ErrorResponse` envelopes at **four** sites, and (iii) run
   a `finally` block releasing the recorded side-effect set. The extraction is therefore a
   **real refactor of a live route**, not a delegation edit:
   * the extracted service takes **`live_hub` as a plain parameter** (mirroring
     `answer_questions_via_instance`'s shape), so the router passes
     `getattr(request.app.state, "live_hub", None)` and the tool passes
     `getattr(manager, "_live_hub", None)` (`daemon/api.py:2336`);
   * the four `HTTPException`s become **typed service errors** the router re-maps;
   * **a test asserts the SSE emit still precedes the cascade** (the F3 ordering contract);
   * **the mandatory route-table readback pin applies** (`ed2dfe67`) — assert
     `POST /instances/{instance_id}/question/dismiss` and `/answer` are still bound to the
     same handlers with the same paths/methods after the move. (Note: the decorator-rebinding
     trap fires when a helper is inserted *between* decorator and handler; a body-move does
     not do that — the pin is a precaution, and it guards the *route table*, which is the
     cheap thing to verify.)

**Side effects:** pack clear + cascade resume + one audit row.

**Refusals:** same shape as `instance_answer`.

---

## 5. The read → act flow

```
                    ┌─────────────────────────────────────────┐
                    │ instance_list / instance_get /           │
                    │ instance_health / fleet_capacity_summary │  READ (no audit row)
                    └───────────────┬─────────────────────────┘
                                    │  agent triages a wedge
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │ instance_terminate(id, cascade, reason,  │
                    │   dry_run=True)          ── DRY RUN ──▶  │   verdict block:
                    │                                          │   target, cascade tree,
                    │  gates: reason-required                  │   protected?, confirmation
                    │         self-surgery-refused             │   required, orphan list
                    │         cascade-false-would-orphan-...   │   NO mutation happened
                    │         protected-target-confirmation-.. │
                    │         confirmed-target-mismatch        │
                    │         no-user-provenance               │
                    └───────────────┬─────────────────────────┘
                                    │  verdict = pass
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │ instance_terminate(..., dry_run=False)   │
                    │   SAME TRANSACTION:  act + audit row    │   ← fail-closed:
                    │   audit write fails ⇒ act rolls back    │     no act without
                    └─────────────────────────────────────────┘
```

`instance_health` output is the **evidence** that feeds an act's `reason` string. The
`reason` is free text but must be non-empty; the audit row stores it verbatim.

---

## 6. The `instance_health` detector table (D-19 — reporting predicates + disagreement pins)

**The original "Composes? YES" column was not supported by the tree (GT-4) — there are no
public read-only predicates to call. The table below states, per detector, whether an
observable sweep counterpart exists for pinning.**

| # | Detector class | Reporting basis (written in `instance_ops`, labelled a projection) | Observable sweep counterpart? | Disagreement pin |
|---|---|---|---|---|
| 1 | `stale-running` | `stale_task_recovery.py`'s stale-task shape + the **lineage-MAX aggregate** (`get_max_last_activity_in_instances`, `repository.py:670`) — R4 hard | partial — `StaleTaskRecovery` has no public classify-only entry | **skipped, honest gap** (documented in the detector row) |
| 2 | `waiting-on-a-dead-parent` | non-terminal children with stale `last_activity_at`; parent alive-check via `ALIVE_INSTANCE_STATUSES` | **YES** — `WaitingChildrenWatchdog.wedge_episodes()` (the already-notified set) + `MissionStrandHealer` | **PIN** — the health report flags a superset of a fresh wedge episode |
| 3 | `question-paused-without-task-row` | paused + aged + no live turn + no pending question in the permanent-lineage tree (`stale_pause_sweep.py:326`'s documented predicate) | partial — the sweep's predicate is documented in its docstring but its selectors are private | **PIN via documented classification** (fixture labelled per the docstring) |
| 4 | `zombie-pending` | `admission_state='active'`, no Task row, instance not alive (`job_recovery_service.py:925` `reconcile_drift_states`'s documented predicate) | partial — the one public method is a **reconciler** (mutating); the predicate is documented | **PIN via documented classification** |
| 5 | `orphans` | the orphan selectors' shape (`orphan_watcher_sweep.py:85`, `auto_continue_boot_pass.py`) | **YES** — `OrphanWatcherSweepService.counters()` | **PIN** |
| 6 | `failed-with-unreported-result` | `report_delivery_recovery.py` / `report_integrity_guard.py`'s documented shape | partial — `report_delivery_ledger.parent_history_has_internal_report` is the one public function | **PIN where observable; skipped where not** |

**Rules (D-19):** extract nothing; import no privates; write the predicates in the new
module as cheap read-only projections; **pin agreement where a counterpart is observable and
say so where it is not**. **A health report never triggers a repair.** The R4 lineage-MAX
rule stays hard and tested.


---

## 7. Security & governance model

### 7.1 Three-layer privilege posture, across TWO categories (D-13)

1. **Layer 1 — privileged categories.** `instance_read` ∈ `PRIVILEGED_TOOL_CATEGORIES` **and**
   `instance_ops` ∈ `PRIVILEGED_TOOL_CATEGORIES` ⇒ neither joins the empty-allow
   default-grant universe. The frozenset goes **12 → 14**.
2. **Layer 2 — explicit allow entries.** `"instance_read"` and `"instance_ops"` both in
   `agents/maintenancer/meta.json` `tools.allow`. **No other agent's allow-list gains either**
   (D1: Ari excluded by design; D2: leader excluded). **Per-tool allow is REJECTED outright**
   — a literal tool name in an allow list is added verbatim and **bypasses the category gate**
   (§2.2), so a per-tool allow mechanism on a privileged category deliberately opens the
   documented bypass and collapses the two-layer gate into one.
3. **Layer 3 — in-body `agent_id` gate.** Every tool body (in both categories) checks
   `agent_id == "maintenancer"` via a shared helper. Fail-closed: a blank / non-`str`
   `agent_id` is **never** authorized.

**Why two categories and not one (D-13, superseding the original single-category reading).**
The read/act risk split becomes **structural**: a future read-only grant to another agent
becomes a one-line allow change on a read category that structurally cannot carry acts. The
cost is **~8 lines across the same pin files plus count bumps** — not a ceremony
multiplication (the architect verified the cost model; the original single-category framing
was based on a false ceremony-multiplication premise). **Single-OWNER is preserved** — both
categories name the same agent. The kill-switch gates only the act category.

### 7.2 The protected-target confirmation gate (D-4, amended by D-17/D-18)

* **Static protected set** = `_is_user_facing_root(parent_id=…, source_type=…)`
  (`daemon/tools/instance.py:2561`) **OR** `agent_id == "leader"`.
* **Dynamic protected set** = an `instance_ops.protect` marker in `instances.metadata`.
* A protected target requires **ALL THREE**: (a) `confirmed_target_id == id` (D-17);
  (b) a live user-origin verdict from the shared provenance helper; (c) `pclass ==
  "inherited"` (D-18 — closes the R1 forge completely without touching the upgrade lane).

> **[DF-4 CORRECTION — 2026-10-22]** Clause (c) — the `pclass == "inherited"`
> requirement — is **RETRACTED** and superseded by DF-4's **liveness-only** gate: a
> protected target requires (a) the named confirmation and (b) a **live** user-origin
> window, whatever its shape. The class-keyed clause never closed the delegated forge
> (the inherited mint copies the root without inspecting the root's own provenance) and
> it DID refuse the genuine operator-direct path. DF-4 closed the delegated forge at its
> ROOT (the sweep's no-user-origin fallback now rides a reserved lane). The clause above
> is kept verbatim as history; see
> `.agents/shared/planning/df4-provenance-closure/plan-overview.md`.
* The refusal message discloses what counts as user-origin via
  `user_origin_sources_display()` (`daemon/tools/upgrade_journal.py:3363-3373`).
* **No nonce.** The upgrade lane's 3-factor nonce regime is untouched, byte-for-byte.
* **The shared provenance helper lives in a NEUTRAL HOME (D-14, superseding D-3's
  "import, don't move"): `daemon/tools/provenance.py`.** The implementation is **moved**
  there; `_restart_provenance` survives in `daemon/tools/upgrade_tools.py` as a **3-line
  delegating alias** with identical signature and verdict shape, so the live lane's name,
  signature, and pins are preserved. The architect verified the T0a source-pin
  (`test_agent_restart.py:127-133`) scopes its assertion to the `system_upgrade` **tool
  body**, which a module-level move does not enter — **and the reviewer and tester must
  independently re-verify that claim before merge (a named gate)**.


### 7.3 The kill-switch (D-7)

`ENSEMBLE_INSTANCE_OPS_ENABLED` — default **ON**. Resolution order mirrors
`_resolve_repair_enabled` (`daemon/tools/ens_db_tools.py:137`): env → (legacy aliases, if
any) → default ON.

**When OFF, the ACT tools return a byte-identical no-op.** Exact string (fixed at
implementation time; the shape below is the contract):

```
Error: INSTANCE_OPS DISABLED — reason=instance-ops-disabled: the instance_ops
act tools are disabled by configuration (ENSEMBLE_INSTANCE_OPS_ENABLED=0).
No mutation happened.
```

**The READ tools STAY AVAILABLE when the kill-switch is OFF.** Reasoning: read is not
destructive; the read surfaces are the category's principal value and are needed to
*diagnose* while acts are disabled (the ens-db lane's kill-switch likewise leaves the
SELECT-only readers — `ens_db_postgres_select`, `ens_db_inspect`, `ens_db_pool_status` —
available and gates only the writer, `ens_db_repair_execute`). This mirrors the house
precedent exactly.

### 7.4 The audit contract (D-2)

* **One table**: `instance_ops_audit`.
* **Vocabulary copied from `repair_log`** (`daemon/repositories/ens_db/models.py:44`):
  `created_by_instance_id`, `created_by_agent_id`, `dry_run: bool`,
  `before_snapshot` (JSONB), `after_snapshot` (JSONB), `outcome`, `error_message`,
  `created_at`, plus `action`, `target`, `reason`, and — per D-17/D-18 — `confirmed_target_id`,
  `provenance_class`, `provenance_depth`, `root_source`, `root_message_id`.
* **Outcome enum:** `"previewed" | "pending" | "committed" | "refused" | "error"`. The
  `"pending"` value is the Tier-2 write-ahead state (§7.5).
* **Dry-run rows are distinguishable**: `dry_run = true` + `outcome = "previewed"`.
  Refusals are also recorded (`outcome = "refused"`, `error_message` = the refusal text) so
  the ledger shows attempts, not just successes.
* **The `maintenance_runs.dry_run_run_id` link pattern** (`daemon/repositories/maintenance_runs/models.py:132`)
  is adopted as `preview_run_id`.

### 7.5 The per-tool AUDIT TRUTH TABLE (D-15) — the two-tier model, stated per tool

**The uniform same-transaction claim of the original design was PARTLY UNSOUND** (architect
Q4, ACCEPT). The ens-db pattern works there because **the ens-db act IS a SQL statement in
the tool's own session**; `instance_ops` acts are **daemon service calls that own their own
transactions**. For `instance_terminate` there is an additional structural blocker: the
service's DB half runs on a **worker thread** via `asyncio.to_thread`
(`daemon/services/instance_lifecycle.py:2827`) — **a SQLAlchemy Session cannot be shared
across that boundary.**

| Act | Underlying primitive | Transaction shape | Couplable? | **Tier** |
|---|---|---|---|---|
| `instance_terminate` | `InstanceLifecycleService.terminate_instance` → `_terminate_instance_db_sync(engine, write_guard, …)` via `asyncio.to_thread` (`instance_lifecycle.py:2827`) | Own `WriteGuardSession`, committed inside the helper, on a **worker thread**; post-commit outbox (SSE / cleanup / lock release) fires after | **YES — via an ADDITIVE `audit_sink` hook** (below) | **Tier 1** |
| `instance_mark` (`protect`/`suspect`) | `repo.set_metadata` (`repository.py:2518`) — dialect-aware single-statement UPDATE | Own session, commits | **YES — via an optional `session=` parameter** | **Tier 1** |
| `instance_mark` (`park`) | `mark_deliberate_pause_override` (`question_pause_set.py:608-655`) | Own `asyncio.to_thread`, **NO session param**, and **fail-open** (swallows exceptions, returns `False`) | **NO — and by LEADER DECISION (review W3), `session=` is NOT threaded into it**: that is the pause/resume machinery, a live subsystem fixed in v0.26.1, and changing its fail-open behaviour is not this commission's business | **Tier 2** |
| `instance_nudge` | `manager.enqueue_message` / `set_injection` | RAM injection or its own message-queue/task session | **NO** without touching a large path | **Tier 2** |
| `instance_answer` | `answer_questions_via_instance` (`routers/answer_helper.py:704`) | Pack is **in-memory**; durable shadow is a metadata write; resume is a scheduler | **NO** — no single transaction to join | **Tier 2** |
| `instance_dismiss` | the (extracted) dismiss service | Same shape as answer | **NO** | **Tier 2** |
| `instance_reclaim` | the revive-on-send path (`instance_messaging.py:2365-2387`) | A scheduler + enqueue, not a transaction | **NO** | **Tier 2** |

**Tier 1 — TRUE same-transaction** (act and audit commit together or neither):
* `instance_terminate` via an **additive `audit_sink: Callable[[Session], None] | None = None`**
  hook invoked inside `_terminate_instance_db_sync`'s **existing** `WriteGuardSession`,
  **before commit**. **Default `None` ⇒ byte-identical current behaviour for every existing
  caller** — the hook is strictly opt-in from the new tool layer. (~10 lines in the service +
  one call site.)
* `instance_mark(marker="protect"|"suspect")` via an optional `session=` parameter on
  `repo.set_metadata`, same principle. **`marker="park"` is Tier-2, NOT Tier-1** (review W3 /
  leader decision): `park` writes through `mark_deliberate_pause_override`
  (`question_pause_set.py:608-655`), which calls `repo.set_metadata` inside its **own**
  `asyncio.to_thread`, has **no session parameter**, and **swallows exceptions** (fail-open,
  returns `False`). **Threading `session=` through it is explicitly REJECTED** — that is the
  pause/resume machinery, a live subsystem fixed in v0.26.1, and changing its fail-open
  behaviour is not this commission's business.

**Tier 2 — WRITE-AHEAD audit** for the acts that have no single transaction to join:
the tool **INSERTs the audit row FIRST** in its own transaction with `outcome="pending"`
(carrying the full intent: action, target, reason, provenance fields), performs the act, then
**UPDATEs** the row to `"committed"` / `"error"`. **A crash mid-act leaves a durable
`pending` row** — the ledger always contains the intent, and `instance_health` (or a sweeper)
can reconcile. This is **strictly stronger than audit-after**, and costs one extra UPDATE.

**The original R11 rule — "a future act that cannot be transaction-coupled must be EXCLUDED
from the category, not silently de-coupled" — applied to three of the six acts shipping now,
not to a hypothetical future act.** Either apply the rule (cut nudge/answer/dismiss) or
**generalize the write-ahead tier and state it in the module docstring and the table's DDL
comment**. D-15 takes the latter: cutting three tools to preserve a uniform property trades
real capability for a phrasing win.

**AC-9 (fault-injection rolls back the act) applies to TIER-1 TOOLS ONLY.** A second AC
covers "a crash mid-act leaves a `pending` row" for Tier-2.

**Rejected options (recorded so they are not re-litigated):** (a) threading a session into
the service — sessions are not thread-crossable, the DB half runs in `asyncio.to_thread`;
(c) moving the transaction boundary into the tool — the service owns the cascade snapshot,
the post-commit outbox ordering, and the H10 crash-safety contract; the tool cannot wrap it.

---

## 8. Acceptance criteria

**Authoritative count (A8): 26 numbered criteria across 29 rows (AC-9/AC-10/AC-23 each split
into (a)/(b)).**

| # | Criterion | Verification |
|---|---|---|
| AC-1 | `CATEGORY_MODULES.get("instance_ops") == "daemon.tools.instance_ops"` **AND** `CATEGORY_MODULES.get("instance_read") == "daemon.tools.instance_read"` (D-13) | new pin `tests/unit/test_instance_ops_registration.py` (two categories) |
| AC-2 | Both categories ∈ `PRIVILEGED_TOOL_CATEGORIES`; frozenset length 12 → **14** | the 7 exact-set pins, all updated in ONE commit |
| AC-3 | All 10 tool names ∈ `DYNAMIC_TOOL_NAMES` and ∈ `KNOWN_TOOL_NAMES` | registration pin |
| AC-4 | The AST scanner finds all 10 `@tool` functions (nested-in-factory) | registration pin, modelled on `tests/unit/test_board_registration.py` |
| AC-5 | The `create_instance_tools` extends are **before** `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` | source-ordering pin (the `f585adea8` source-ordering pattern) |
| AC-6 | `tools.allow` of the maintenancer contains **both** `"instance_read"` and `"instance_ops"`; **Ari, leader and every other agent's allow-list do NOT** | allow-list pins + `tests/unit/test_ari_user_ruling_allow_sets.py` |
| AC-7 | Every act tool refuses with a typed token; the refusal string matches `f"Error: {label} REFUSED — reason={token}: {msg}"` | unit tests per tool |
| AC-8 | Every act writes exactly one audit row; **dry-run rows have `dry_run=true` + `outcome="previewed"`** | audit tests |
| **AC-9a** | **TIER-1 only** — an audit-write failure **rolls back the act** (terminate, mark) | fault-injection test |
| **AC-9b** | **TIER-2** — a crash mid-act leaves a durable `outcome="pending"` row (nudge / answer / dismiss / reclaim) | crash-simulation test |
| **AC-10a** | **WIRING pin** — the instance_ops gate resolves to the single canonical helper in `daemon/tools/provenance.py` (an identity/source assertion, NOT a behavioural comparison; the original fixture-equality pin was **vacuous** — it compared the helper to itself) | source-grepped, like `test_agent_restart.py:127-133` |
| **AC-10b** | **VOCABULARY pins** — the verdict tokens (`ok`, `pclass` ∈ {direct, inherited}, `depth`, `detail`) and the gate's refusal-token mapping (`ok=False` → `no-user-provenance`; `pclass="direct"` on a protected target → `direct-provenance-insufficient`) are pinned so neither lane can silently change semantics | vocabulary test |
| AC-11 | Terminating a user-facing chat root without `confirmed_target_id == id` **refuses** (`confirmed-target-mismatch`); with it but no live provenance ⇒ `no-user-provenance`; with a **direct** window ⇒ `direct-provenance-insufficient` (D-18); with an **inherited** live window ⇒ proceeds | gate tests |
| AC-12 | `instance_terminate(id=self)` refuses with `self-surgery-refused` | self-surgery test |
| AC-13 | `cascade=False` on a target with live descendants refuses with `cascade-false-would-orphan-descendants`; with `allow_orphan_descendants=True` the preview **names** the orphans | cascade test |
| AC-14 | `ENSEMBLE_INSTANCE_OPS_ENABLED=0` ⇒ every act tool returns the byte-identical no-op; the `instance_read` tools still work | kill-switch test |
| AC-15 | `instance_answer` / `instance_dismiss` reproduce the route's error parity (`NO_PENDING_QUESTION`, `QUESTION_PACK_LOST`, `QUESTION_PACK_MISMATCH`, `ANSWER_TARGET_TERMINAL`, `already_delivered`) | parity tests |
| AC-16 | The **doc surface** (error envelope + `args_schema` + `_full_doc_`) states **current** semantics, and a test asserts it (the D-V2 lesson — v0.26.2 shipped stale doc strings, fixed only in v0.26.3) | doc-surface pin, modelled on `TestDocSurfaceWakeTheCaller` |
| AC-17 | The forbidden-layer grep over `agents/maintenancer/` and `agents/ari/` returns **0 hits** for the guide's token list (`meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, `skill-set.yaml`, `agent_id=`, `seed_all`, `innate_skills`, `default_agent_versions`) | pre-commit gate (`docs/agent-prompt-writing-guide.md:250`) |
| AC-18 | `agents/.prompt-artifacts.sha256` re-stamped; `generate_prompt_artifact_baseline.py --check` passes **from the repo root** | baseline check |
| AC-19 | The 7 exact-set pins + the new registration pin + `ROLLOUT.md:51` **and `:52`** + the inherited 3-red integration pin + **the baseline re-stamp** all land in **ONE commit** | commit-shape disclosure |
| AC-20 | A **daemon restart** is required for the categories to be live (`_registry` is an import-time singleton, `daemon/registry.py:1474-1496`; `discover()` at `:1491`) | documented in the deployment section; a boot smoke proves the tools appear |
| **AC-21** | **`instance_list`'s `status` filter is SQL-side** — a fleet larger than one page returns a **correct** filtered result, not a pinned-ordered subset (the GT-3 / former-L-2 defect) | pagination test: > `limit` rows matching a status; assert page 2 continues correctly |
| **AC-22** | **The three-dimension liveness model** (D-19): `status` filterable; `liveness` a returned field from `_STATUS_CANONICAL_MAP`; `stale`/`idle_seconds` separate fields; `pause_marker` reported — and a **deliberately parked** instance does **not** read as wedged | model test: a parked instance reports `pause_marker` + `idle_seconds` and its `liveness` value does not change with staleness |
| **AC-23a** | **D-18's real bounds — the class it GENUINELY closes:** a **direct** window **on the acting (maintenancer) instance** against a protected target ⇒ `direct-provenance-insufficient`. **The fixture includes the GENUINE operator-direct path and asserts it is ALSO refused (INTENDED — L-9)** | direct-window test |
| **AC-23b** | **THE DELEGATED-FORGE RESIDUAL, PINNED AS TRUTH:** a fixture with a **forged direct window on the SPAWNER** → an **inherited window on the maintenancer** ⇒ asserts the **DOCUMENTED RESIDUAL behaviour** — the act is **ADMITTED** by the provenance gate (and must therefore be caught by the D-16 budget + post-hoc audit, not by the gate). **Without this fixture the suite would certify a property that does not hold.** The test's assertion message names DF-4 / critical note `f4cf9c4f` as the root-fix lane | delegated-forge residual test |
| **AC-24** | **D-16**: the 4th committed terminate in 24h refuses `terminate-budget-exceeded`; a target younger than 600s refuses `target-too-young` | budget tests |
| **AC-25** | **D-20**: `instance_reclaim` refuses a non-terminal target (`target-not-terminal`) and a terminal target revives with a documented message | reclaim tests |
| **AC-26** | **The provenance T0a claim is independently re-verified by the reviewer AND the tester before merge** (D-14's named gate) | review gate, recorded in both reports |

> **[DF-4 CORRECTION — 2026-10-22]** Applies to the **AC-10b** and **AC-11** rows above.
>
> * **AC-10b** — the refusal-token mapping is **RE-POINTED** by DF-4 / P2: there is now
>   **exactly ONE** provenance refusal token (`no-user-provenance`); the class-keyed token
>   is **RETIRED** and no longer appears anywhere in `daemon/` or `tests/`. The verdict
>   tokens (`ok` / `pclass` / `depth` / `detail`) are **unchanged** — `pclass` survives as
>   OBSERVABILITY.
> * **AC-11** — the "with a **direct** window ⇒ `direct-provenance-insufficient`" clause is
>   **RETRACTED**: a live **direct** window on the acting instance now **PROCEEDS** (the
>   genuine operator path is admitted). The `confirmed-target-mismatch` and
>   `no-user-provenance` clauses are unchanged.
>
> **[DF-4 CORRECTION — 2026-10-22]** Applies to the **AC-23a** and **AC-23b** rows above.
>
> * **AC-23a** — **RE-POINTED** (DF-4 / P2): the genuine direct window on the acting
>   instance is **ADMITTED**, not refused. The test is now
>   `test_genuine_direct_window_admits_protected_act`.
> * **AC-23b** — **SPLIT** (DF-4 / P3): the **genuine** delegated chain is ADMITTED (the
>   test is now `test_genuine_inherited_window_admits_protected_act`), and the **forged**
>   chain's refusal leg moved to the REAL-chain e2e
>   (`tests/postgres/test_df4_provenance_closure.py` — the sweep's no-user-origin wake can
>   no longer mint a forged DIRECT root, so the delegated child inherits nothing and the
>   gate refuses `no-user-provenance`). The row above is kept verbatim as history; the
>   authoritative retraction trace is `decisions.md:666-684`. See
>   `.agents/shared/planning/df4-provenance-closure/plan-overview.md`.

---

## 9. Cross-references

* `decisions.md` — D-1 … D-20 (the binding leader decisions, the architect-adjudicated
  amendments D-13…D-20, the deviations, the deferrals).
* `implementation-plan.md` — the ordered, file-by-file build; the ONE-commit pin ceremony;
  the migration; the test plan.
* `risk-register.md` — R1 … R21 + the accepted limitations L-1 … L-9.
* `architecture-recommendation.md` — the adversarial input (GT-1…GT-5, Q1–Q6, the CUT, the
  missed items); read-only input to this amendment, not itself amended.
