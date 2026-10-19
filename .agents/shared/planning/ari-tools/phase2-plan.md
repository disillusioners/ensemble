# Phase 2: `self_alarm` — alarm/timer wrapper for Ari

Date: 2026-10-19
Author: planner via plan-creation worker
Status: Ready for Review
Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base `latest@630cefebb`)
Predecessor: Phase 1 (board) — phase 2 touches **NO** phase-1 code; only the pinned `wi-<uuid8>` id format is consumed.

---

## Objective

Give Ari a single, policy-enforcing tool that arms a **reminder to itself** ≥30 minutes out, delivered as a self-contained message
into a fresh Ari instance — and (optionally) linked to a board item — without Ari having to know the scheduling engine's payload
grammar, label lifecycle, or timezone-resolution rules.

Testable goal: an Ari instance can call `self_alarm(label, when_or_every, message_to_self, link_board_item?)` and receive a durable
schedule that fires exactly once at the stated instant, into an instance that can act on the message alone.

## Scope

### In scope
- New tool `self_alarm` (category **`self-alarm`**, non-privileged), **ari-only** by allow-list *and* runtime gate.
- ≥30-minute minimum-horizon gate with a "use bash sleep in-turn" rejection hint.
- A **small** `when_or_every` grammar mapped onto the engine's four recurrence variants.
- Label-based reuse/dedupe semantics on top of the engine's terminal label namespace.
- One-shot post-fire lifecycle (engine-native "stopped") + honest statement of what the wrapper cannot observe.
- Self-contained reminder message (fresh-instance actionability) with board-link injection.
- Board `due_at` → alarm glue: **picked** (§D8) with a documented v2 deferral.
- Prompt surface in `agents/ari/tools_note.md` (new `## Self-Alarm` section) — **not** seal-pinned.

### Out of scope
- Any new table, migration, daemon-side scheduler, or engine change (the engine already exists and is unchanged).
- `self_alarm` for any agent other than `ari` (pre-ruling).
- A "list my alarms" tool (Ari already has `task_schedule_list`; phase 3 may add a board-scoped view).
- Natural-language time parsing ("in a bit", "next tuesday-ish") and an interval recurrence ("every 1h") — the
  grammar is deliberately small and `create_schedule` exposes no interval variant (§D3, OQ-3).
- Editing `agents/ari/rule.md` / `agents/ari/workflow.md` / `agents/leader/*` — `rule.md`+`workflow.md` are
  SHA-seal-pinned (`tests/test_chart_image_delivery_e2e.py:1350-1398`) and `agents/ari/workflow.md` is **already**
  drifting at plan time (7 stale pins measured) — a new edit would add an 8th and force a re-pin commission.
  `agents/ari/meta.json` + `agents/ari/tools_note.md` are **not** pinned (`research-db-agents.md` §5).

---

## Engine facts (anchored in THIS worktree — re-verify before coding)

| Fact | Anchor |
|---|---|
| Engine = module functions; never dispatches; the adapter fires | `daemon/services/scheduling_service.py:3-7`, `:80-83` |
| `create_schedule` payload/recurrence set (`once\|daily\|weekly\|cron`), dup-label rejection | `scheduling_service.py:645`, `:668-670`, `:679-731` |
| `when` for `once` = ISO 8601 (naive → anchored in resolved tz); daily/weekly = `HH:MM`; cron = expr | `scheduling_service.py:696-731` |
| tz chain: explicit → user setting → `ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE` → host-local → UTC + loud warning | `scheduling_service.py:362-397`, `daemon/util/tz.py:283` |
| Label lookup does **not** filter status → a cancelled label stays burned; no hard delete for scheduler rows; recurrence is **immutable** via update; cancelled rows are 409 on update | `daemon/repositories/source/repository.py:212-216`, `:263`; `scheduling_service.py:923-926` |
| Fire path: `SchedulerAdapter._route_via_job_queue` → `InstanceMapper.get_or_create_instance(force_new=instance_mode==new_instance)` → `enqueue_message_job(..., idempotency_key=f"scheduler:{source_id}:{run_at.isoformat()}")` | `daemon/sources/adapters/scheduler.py:1025-1110`, `:1063-1069`, `:1090-1104` |
| **The job-queue route requires `project_id`** — without it the fire takes the legacy `_execute_immediate` path | `scheduler.py:995` (`if trigger_type == "scheduled" and self._project_id:`) |
| One-shot fires into a **brand-new instance** when `instance_mode=new_instance` (mapping deleted, new instance) | `daemon/sources/mapper.py:276-346` |
| After a one-shot fires: `status='stopped'`, `enabled=False` — the row **persists**, history preserved, NOT cancelled | `daemon/sources/registry.py:515-526` |
| The scheduler source is **one-way**: `SchedulerAdapter.send` only LOGS the reply | `scheduler.py:435-457` |
| **Lateness cap** default = `None` (unlimited) ⇒ a past-due one-shot fires immediately (late). With the cap set, a boot later than the cap ⇒ `schedule_executions` row `status='skipped'`, **no dispatch**, schedule **stays armed** (re-checks every 5 s, once-per-`run_at` guard) | `daemon/config.py:2617-2623`, `scheduler.py:497-560`, `:690-733`, `:737-796` |
| Idempotency dedup: a **terminal** prior job does NOT block a fresh submit (key gets a `#<hex>` suffix); only a **non-terminal** prior job within TTL (24 h) collapses | `daemon/services/job_queue_service.py:1424-1481` |
| Registration = **three** steps (decorator + registry entry + construction extend); decorator-only is silently invisible | `daemon/tools/instance.py:2286-2289`, `daemon/tools/_tool_registry.py:342-367`, `:690+` |
| `resolve_tool_filter`: empty allow **and** deny ⇒ everything allowed ⇒ an allow-list is not a hard boundary; runtime agent-gate precedent | `daemon/tools/instance.py:373-434`; `daemon/tools/blueprint.py:71-72` (`_is_writer_authorized`) |
| Public tz/cron helpers reusable without duplicating engine logic | `daemon/util/tz.py:283` `resolve_timezone`, `:353` `anchor_local_to_utc`, `:529` `compute_next_cron_fire`; `daemon/services/user_timezone_utils.py:31` |

---

## Tool contract (binding shape — `self_alarm(label, when_or_every, message_to_self, link_board_item?)`)

```python
self_alarm(
    label: str,                      # 1-128 chars; the dedupe key (engine label namespace)
    when_or_every: str,              # grammar below (3 families)
    message_to_self: str,            # "what to do" — appended verbatim as the Action line
    link_board_item: str | None,     # "wi-<uuid8>" (validated by regex, embedded verbatim)
) -> dict
```

Success return (mirrors the engine echo + wrapper extras — **never** a bare string on success):

```json
{ "ok": true, "source_id": "...", "label": "deploy-check", "status": "running", "recurrence": "once",
  "instance_mode": "new_instance", "timezone": "Asia/Ho_Chi_Minh", "armed_at_utc": "...",
  "first_fire_at_local": "2026-10-20T09:00:00+07:00", "first_fire_at_utc": "2026-10-20T02:00:00+00:00",
  "board_item": "wi-1a2b3c4d", "reused": false, "tz_warning": "", "message_preview": "[self-alarm] deploy-check …" }
```

Failure → `"ERROR: {msg}"` (house rule; the tool never raises — `daemon/tools/scheduling.py:57-59`).

### `when_or_every` grammar (exact)

| Family | Accepted form | Maps to engine | Notes |
|---|---|---|---|
| Absolute one-shot | `2026-10-20T09:00:00+07:00` / `…Z` / `2026-10-20T09:00:00` (naive) | `recurrence=once`, `when=<ISO>` | Offset-aware ⇒ trusted as-is; naive ⇒ anchored in the resolved tz (`anchor_local_to_utc`) |
| Relative one-shot | `45m`, `in 45m`, `2h`, `1d30m`, `90s` | `recurrence=once`, `when=<now+Δ as aware ISO>` | Regex `^(?:in\s+)?(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hr|d|day)s?$` (case-insensitive) |
| Recurring | `daily 06:30` | `recurrence=daily`, `when="06:30"` | Engine builds cron `M H * * *` |
| Recurring | `weekly 1 06:30` | `recurrence=weekly`, `weekday=1`, `when="06:30"` | DOW canonical: 0=Sunday..6=Saturday (engine convention) |
| Recurring | `cron */30 * * * *` | `recurrence=cron`, `cron_expression=…` | Raw 5-field expr |
| **Rejected** | `every 1h`, `every day at 6` | — | The engine has no interval recurrence in `create_schedule` ⇒ reject with the hint *"use `cron */60 * * * *`"* |
| **Rejected** | anything else | — | Typed error listing the accepted forms (keep the surface small) |

**Timezone defaulting.** The wrapper resolves the tz **once**, server-side, via the *same public primitives* the engine uses —
`resolve_timezone(explicit, default=…)` + `get_user_timezone_preference(project_repo)` + `SchedulingConfig().default_timezone` —
then **hands the engine an absolute instant** (offset-aware ISO) for one-shots, or the resolved IANA name for recurring forms ⇒ the
stored `run_at` is exactly the instant the wrapper floored and the engine's tz chain is not re-derived. The engine's `tz_warning` is
echoed verbatim, never suppressed. A **parity test** pins the wrapper's resolution to
`scheduling_service._resolve_schedule_timezone` (§Tests) so chain drift is caught, not guessed.

**Recurring first fire.** Computed with the *same* helper the engine's `_compute_next_run` uses — `compute_next_cron_fire(cron_expr,
now, zone)` (`scheduling_service.py:474-478`) ⇒ DST fold/gap semantics are identical by construction (no duplicated DST logic).

---

## Design decisions made in this phase plan

**Refinements vs the pre-rulings (explicit, no pre-ruling is re-litigated):** (i) the ≥30-min floor is checked
**pre-create** on the wrapper-computed first fire, so a rejected alarm burns no label — the pre-ruling fixed the
threshold, not the check point; (ii) the ari-only rule is enforced by an **allow-list *and* a runtime gate**, because
an allow-list is not a hard boundary (D10) — a strengthening, not a change; (iii) the one-shot post-fire action is
**mark-done (engine-native)**, not auto-cancel — the pre-ruling allowed either, and cancel is terminal + label-burning
(D5); (iv) the board `due_at` glue picks the **agent-prompt convention** and defers creation-time arming to v2 (D8);
(v) the recurring "first fire <30 min away" case is **rejected**, not auto-skipped to the next occurrence (D9, OQ-2);
(vi) the module split adds a **pure service module** beside the tool module (D2) — an internal shape choice.

**D1 — The wrapper exists; it is not a re-implementation of `task_schedule`.** Verified value-add: (a) the ≥30-min horizon gate with
the bash-sleep hint — `task_schedule` validates *shape*, not horizon (`scheduling_service.py:645-731`); (b) fresh-instance message
composition — `task_schedule` stores the raw `message` and the adapter sends it verbatim (`scheduler.py:962-964`); (c) label-dedupe
UX — the engine's duplicate label `ValueError` is opaque to an agent and the burned-label case (D4) is invisible; (d) board-link
injection. All four are *wrapper* concerns; none need engine changes.

**D2 — New module pair, not a fold into `daemon/tools/scheduling.py`.** `CATEGORY_MODULES` is a category→module map
(`_tool_registry.py:690+`) ⇒ a second category needs its own module path. Split: `daemon/services/self_alarm_service.py` = **pure**
helpers (parse → fire plan → floor → message composition; no tool/LLM plumbing; unit-testable) and `daemon/tools/self_alarm.py` =
the LLM-facing tool + engine calls + dedupe. Rationale: the v2 board auto-arm path (D8) reuses the *pure* helpers without importing
a tool module — a 5-line call instead of a refactor. The `scheduling` module stays granted to leader/jober and holds no ari-only
policy.

**D3 — Small, prefix-disambiguated grammar (§tool contract).** No free-form natural language, no "tomorrow at 9" (the LLM computes
the absolute ISO with its `time` tool when it wants that). `every <duration>` is rejected (no engine interval variant) rather than
silently mistranslated.

**D4 — Dedupe/reuse by label: reuse-in-place, never cancel-to-free.** Engine facts: duplicate label ⇒ `ValueError("label already
exists: …")` (`scheduling_service.py:668-670`); the name lookup ignores status (`repository.py:212-216`); cancel is terminal and
`update` refuses cancelled rows (`:923-926`); no hard delete exists for scheduler rows ⇒ **a cancelled label is burned forever**.
Policy: (1) look the label up first (`asyncio.to_thread(source_repo.get_source_config_by_name, label)` — the seam
`task_schedule_cancel` uses, `daemon/tools/scheduling.py:206-216`); (2) no row ⇒ create; (3) row in `{running, stopped}` ⇒ **reuse
in place** via `update_schedule(source_id, {when, message})`; a differing recurrence *family* cannot be expressed by `update` ⇒
typed error ("recurrence cannot be changed; cancel and use a new label"); (4) row `cancelled` ⇒ label burned ⇒ the wrapper
**auto-retries once** with `"<label>-2"` (then `-3`, cap 5) and echoes the **effective** label — a hard error would deadlock the
agent, and re-naming is the only escape. *Engine caveat (documented, not a blocker):* re-arming at the **identical** instant within
24 h of a still **non-terminal** prior job collapses into that job (idempotency key `scheduler:{source_id}:{run_at}`), but a
**terminal** prior job (normal after a fire) does **not** block a fresh submit (`job_queue_service.py:1424-1481`) ⇒ the wrapper
rejects an unchanged instant on a `stopped` label.

**D5 — One-shot post-fire: "mark-done" is ENGINE-NATIVE; the wrapper does not cancel.** After a one-shot fires the adapter's
`on_complete_callback` sets `status='stopped'` + `enabled=False` (`registry.py:515-526`); the row persists (history preserved) and
is **not** cancelled. Cancelling would be worse on every axis: terminal, burns the label, hides the row from the default list,
destroys the "it fired" evidence. Clutter control = **status filter** (`running` = armed; `task_schedule_list` already supports it).
Honest limit: the wrapper **cannot observe the fire** — there is no agent-facing post-fire hook (the adapter's `execution_callback`
is internal) ⇒ "alarm fired" is readable only from the schedule row's status. No phase-2 auto-cleanup (a "board item → done on
alarm" hook would need that same internal seam).

**D6 — `instance_mode="new_instance"`, fixed.** Rationale: the alarm is a *fresh task*, not a continuation (a rehydrated checkpoint
would dilute it); a one-shot fires once, so `reuse_instance` buys no continuity; and `reuse_instance` wraps the message in
`_format_continuation_message` (run-number text), meaningless for a one-shot (`scheduler.py:801`, `:962`). **Which instance receives
it:** a scheduler-owned Ari instance (`source_id` = the schedule row id) — **not** any of Ari's chat instances — and the reply is
**not** delivered to a chat surface (the scheduler source is one-way; `SchedulerAdapter.send` only logs, `scheduler.py:435-457`).
The tool doc states this plainly: the alarm's value is that the reminder *lands in the agent's own workspace* and can act
autonomously (dispatch a job, update the board), **not** that it pings the human's chat (bridge-to-chat = OQ-1).

**D7 — Fresh-instance message contract (wrapper-composed, agent supplies only the action).** The wrapper stamps the prefix, injects
the context, appends the agent's text verbatim:

```
[self-alarm] <label>
Scheduled fire: <first_fire_local>  (<first_fire_utc>)
Armed at: <armed_at_utc>   Schedule: <recurrence> | tz <tz> | id <source_id>
Board item: wi-1a2b3c4d — call board_get with that id to load its title, body, status and notes before acting.
You are a fresh instance with no memory of this request; everything you need is in this message.
Action: <message_to_self>
```
The "fresh instance, no shared state" line is mandatory (Ari instances are separate trees). The scheduled instant is stated so a
late delivery is detectable ("if this is more than a few minutes past the scheduled time, verify current state before acting").

**D8 — Board `due_at` glue: PICK = agent-prompt convention (Option 2). Creation-time auto-arming DEFERRED to v2.** Auto-arming from
the board service is **not** clean against the actual API shape, for five concrete reasons: (1) `create_schedule` needs
`caller_instance_id`/`caller_agent_id` **and** a target `agent`, while the board tools are granted to **ari *and* leader** ⇒ the
board service would need a new caller-agent concept to honour the ari-only pre-ruling; (2) label synthesis + the burned-label rule
(D4) would leak into the board service; (3) a `due_at` change ⇒ reschedule via `update_schedule`, which **cannot change the
recurrence family** and has a documented <1 s missed-fire window (`scheduling_service.py:71-78`); (4) `due_at` removal ⇒ cancel ⇒
terminal + label burn + hidden history; (5) the board service is built **in parallel** in phase 1 and its API is not frozen —
coupling phase 2 to its internals creates a merge-order dependency. ⇒ Phase 2 arms from the **agent side**: the convention
("whenever you set or move a board item's due date, arm or re-arm a self_alarm for it and pass `link_board_item`") lives in
`agents/ari/tools_note.md` (new `## Self-Alarm` section — not seal-pinned) + the tool's `_full_doc_` + the tool's error hints. The
board's `due_at` stays the **source of truth for the deadline**; the alarm is the **reminder mechanism**; the board status
transition stays Ari-driven. The wrapper embeds the board id **verbatim** (validated `^wi-[0-9a-f]{8}$`) and instructs the fresh
instance to `board_get` it ⇒ phase 2 depends on phase 1 only through the pinned id format, never its internals.

**D9 — ≥30-minute floor lives in the wrapper, PRE-create.** `_enforce_floor` runs on the wrapper-computed first-fire instant
**before** any engine call ⇒ a rejected alarm creates **no row and burns no label** (create → validate → cancel would burn the
label, D4). The floor is stated in **minutes** (the user-facing policy) but compared in **seconds** against the absolute instant
(`first_fire - now >= 1800 s`), and applies to the **first-fire offset only**: a recurring alarm whose first fire is ≥30 min away is
accepted; later fires are unconstrained (≥ one recurrence period away). The recurring-arm-now-imminent case (`daily 06:30` armed at
06:15 ⇒ first fire in 15 min) is **rejected**, not auto-skipped to tomorrow — silently moving the user's stated schedule is worse
than an explicit error; the escape hatch is an absolute ISO for the next occurrence (OQ-2). Rejection text (exact, carries the
binding bash-sleep hint):

```
ERROR: self_alarm requires a minimum horizon of 30 minutes — the requested fire time is 12m away
(2026-10-20T01:07:00+07:00). For anything shorter, use bash with `sleep <seconds>` inside the current turn
instead of an alarm.
```

**D10 — ARI-only is enforced twice: allow-list *and* runtime gate.** `tools.allow` gets `"self-alarm"` in **`agents/ari/meta.json`
only** (never leader — the pre-ruling). But `resolve_tool_filter` returns `None` (everything allowed) when an agent's allow **and**
deny are both empty (`instance.py:373-434`) ⇒ the allow-list is a *visibility* boundary, not a security boundary. The tool body
therefore rejects any caller whose `agent_id != "ari"` with a typed error — the `blueprint` writer-gate precedent
(`daemon/tools/blueprint.py:71-72`) — and the registration test asserts that **no** other `agents/*/meta.json` lists `self-alarm`.

**D11 — `project_id` is auto-injected, never optional.** `project_id` decides the fire route: without it the fire takes the legacy
`_execute_immediate` path instead of the job-queue path (`scheduler.py:995`) ⇒ no JobItem, no idempotency key, no queue routing. The
wrapper resolves the caller's project from the instance row (`manager._instance_repository.get(current_instance_id).project_id` —
the `blueprint`/`chart_tools` auto-inject pattern, `daemon/tools/blueprint.py:61-66`) and passes it explicitly; if it cannot be
resolved it returns an error instead of arming a degraded alarm.

**D12 — Board id validated, not parsed.** `link_board_item` must match `^wi-[0-9a-f]{8}$` (the pinned phase-1 format); a mismatch is
a typed error. The id is embedded **verbatim** (no title/status lookup) ⇒ zero runtime dependency on the board service.

**D13 — Prompt surface.** One new `## Self-Alarm` section in `agents/ari/tools_note.md` (agent-POV, first person, no daemon
internals — the forbidden-token list in `tests/unit/test_scheduling_registration.py:44-58` is the pattern to mirror). No edit to
`agents/ari/rule.md` / `workflow.md` (seal-pinned; `workflow.md` already drifting).

---

## Task checklist

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Re-verify the 3 engine invariants in THIS checkout and cite them: (a) one-shot completion ⇒ `status='stopped'` + `enabled=False` (`registry.py:515-526`); (b) cancelled label is permanently burned (`repository.py:212-216`, `scheduling_service.py:923-926`); (c) `project_id` gates the job-queue route (`scheduler.py:995`) | — | All three cited with file:line in the implementation PR description; any deviation re-planned before coding |
| 2 | Create `daemon/services/self_alarm_service.py` — pure helpers: `parse_when_or_every`, `resolve_fire_plan` (tz + cron/anchor), `enforce_min_horizon`, `compose_alarm_message`, `validate_board_id` | 1 | Pure module: no tool imports, no engine calls, no DB; 100% unit-testable |
| 3 | Create `daemon/tools/self_alarm.py` — closure factory `create_self_alarm_tools(service, *, source_repo, current_instance_id, agent_id, agent_tag)`, `SelfAlarmInput` BaseModel, `@register_tool_category("self-alarm")` **above** `@tool(args_schema=…)`, `self_alarm._full_doc_` | 2 | Tool returns a dict on success and `"ERROR: …"` on every failure; never raises |
| 4 | Register (all **three** steps): `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` ("self_alarm") and `CATEGORY_MODULES["self-alarm"] = "daemon.tools.self_alarm"` in `daemon/tools/_tool_registry.py`; `create_self_alarm_tools_if_available(manager, …)` + `tools.extend(…)` in `daemon/tools/instance.py` (next to the scheduling extend, `:5768-5775`) | 3 | The tool is visible in a real instance tool list; a decorator-only variant is proven insufficient (comment mirrors `:2286-2289`) |
| 5 | Dedupe/reuse implementation: label lookup → create / reuse-via-`update_schedule` / burned-label `-2` retry / recurrence-mismatch error / identical-instant re-arm guard | 3, 4 | Each branch covered by a unit test; the effective label is always echoed |
| 6 | ARI-only runtime gate (`agent_id != "ari"` ⇒ error) + `"self-alarm"` added to **`agents/ari/meta.json`** only | 3 | `grep -rl "self-alarm" agents/*/meta.json` → exactly one file |
| 7 | New `## Self-Alarm` section in `agents/ari/tools_note.md` (agent POV, zero forbidden tokens, cross-refs resolve) | 3 | Matches the guide's forbidden-token + cross-ref conventions; no seal pin touched (files edited: `tools_note.md`, `meta.json` only) |
| 8 | Tests: `tests/unit/test_self_alarm_service.py` (grammar, floor boundary, tz parity, message composition, board-id regex) | 2 | Boundary: 29 m ⇒ reject, 30 m ⇒ accept, 30 m + 1 s ⇒ accept; parity vs `_resolve_schedule_timezone` matrix |
| 9 | Tests: `tests/unit/tools/test_self_alarm_tools.py` (mocked service; `await tool.coroutine(...)`) | 3, 5 | Covers create / reuse / burned-label / recurrence-mismatch / non-ari gate / engine-error paths |
| 10 | Tests: `tests/unit/test_self_alarm_registration.py` (mirror `tests/unit/test_scheduling_registration.py`): allow ⊆ registry universe, category resolution, name in `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES`, **ari-only** allow, tools_note forbidden tokens + cross-refs | 4, 6, 7 | Green; asserts NO other agent grants the category |
| 11 | Regression run: the new tests + `tests/unit/test_scheduling_registration.py` + `tests/unit/tools/test_frozen_tool_name_discovery.py` + `tests/unit/tools/test_tool_config_validation_boot.py` + the seal test | 8-10 | Zero NEW failures; the stale-seal count stays at the measured baseline (7) — no `agents/ari/rule.md` / `workflow.md` edit |
| 12 | Record the deviations/notes into the phase-2 decisions feed (`decisions.md` in this planning dir) | 1-11 | Each of D1-D13 has an anchor; deviations from pre-rulings flagged explicitly |

---

## File map

| File | Action | Notes |
|---|---|---|
| `daemon/services/self_alarm_service.py` | **new** | Pure logic: grammar, fire plan, floor, message, board-id regex |
| `daemon/tools/self_alarm.py` | **new** | Factory + `self_alarm` tool + `_full_doc_` + `CATEGORY_NAME`/`CATEGORY_DOC` |
| `daemon/tools/_tool_registry.py` | modify | `DYNAMIC_TOOL_NAMES`, `KNOWN_TOOL_NAMES`, `CATEGORY_MODULES` (3 entries) |
| `daemon/tools/instance.py` | modify | `create_self_alarm_tools_if_available` + one `tools.extend(...)` |
| `agents/ari/meta.json` | modify | add `"self-alarm"` to `tools.allow` — **only** allow-list touched |
| `agents/ari/tools_note.md` | modify | new `## Self-Alarm` section (not seal-pinned) |
| `tests/unit/test_self_alarm_service.py` | **new** | Pure-logic unit tests |
| `tests/unit/tools/test_self_alarm_tools.py` | **new** | Tool-level unit tests (mocked service) |
| `tests/unit/test_self_alarm_registration.py` | **new** | Registration + allow-list + prompt-closure pins |
| `daemon/services/scheduling_service.py`, `daemon/sources/adapters/scheduler.py` | **unchanged** | The engine is reused verbatim |
| `agents/ari/rule.md`, `agents/ari/workflow.md`, `agents/leader/*` | **unchanged** | Seal-pinned / pre-ruled out |

---

## Tests (mirroring the existing scheduling / shared_meta_kv files)

- Pattern sources: `tests/unit/tools/test_scheduling_tools.py` (mocked service, `await tool.coroutine(...)`,
  `_full_doc_` pins), `tests/unit/test_scheduling_registration.py` (allow ⊆ registry, category resolution,
  forbidden-token + cross-ref prompt closure), `tests/unit/tools/test_frozen_tool_name_discovery.py`
  (frozen-universe membership).
- **Floor**: 29 min ⇒ `ERROR` + bash-sleep hint + **no** engine call; 30 min ⇒ accept; 30 min + 1 s ⇒ accept;
  past-due absolute ⇒ reject; 90 s relative ⇒ reject.
- **Grammar**: relative (`45m`, `in 2h`, `1d30m`), absolute aware/naive, `daily 06:30`, `weekly 1 06:30`,
  `cron */30 * * * *`, `every 1h` ⇒ rejected with the cron hint, garbage ⇒ typed error listing the forms.
- **Recurring parity**: the wrapper's first fire == the engine's `next_run_at_utc` echo (≤1 s) for `daily`,
  `weekly`, `cron` — a tz/first-fire parity test pins the wrapper against the engine.
- **Tz parity**: the wrapper's resolved zone == `scheduling_service._resolve_schedule_timezone` for the matrix
  {explicit tz, user setting set/unset, env default set/unset} — guards against chain drift.
- **Dedupe**: no row ⇒ create; `running` ⇒ `update_schedule` (no create); `stopped` ⇒ re-arm via update;
  `cancelled` ⇒ create with `"<label>-2"` and the effective label echoed; recurrence-family mismatch ⇒ typed error;
  identical-instant re-arm ⇒ typed error.
- **Gate**: a non-`ari` caller ⇒ `ERROR` and no engine call.
- **Message**: contains `"[self-alarm]"`, the label, the scheduled instant (local **and** UTC), the board id + the
  `board_get` instruction, the fresh-instance note, and the agent's action text verbatim.
- **Engine errors**: `create_schedule`/`update_schedule` raising ⇒ `"ERROR: …"` (never raises).
- **Safety**: hermetic — mocked service + mocked repo only; no DB, no daemon boot, no `$HOME`-derived paths
  (critical note 2026-10-12: test scripts must never resolve live-shaped paths).

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **Alarm never fires after a long outage.** Default `one_shot_max_lateness_seconds=None` ⇒ unlimited lateness ⇒ a ≥30-min alarm whose instant passed while the daemon was down **fires LATE at the next boot** (adapter rebuilt from config; the past-due one-shot returns `now` as its next trigger → immediate dispatch). If the operator sets `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS=N`, a boot later than N ⇒ a `skipped` history row, **no dispatch**, and the schedule **stays armed** (re-checks every 5 s; once-per-`run_at` guard ⇒ it never fires until someone updates `run_at`). | High (the reminder is silently lost / arrives very late) | Medium | Document the exact behaviour in the tool's `_full_doc_` + the tools_note section; the composed message states the **scheduled** instant so a late delivery is detectable; recommend a post-outage board re-check. **Phase 2 does not change engine semantics** (out of scope, OQ-4) |
| 2 | Burned label after cancel ⇒ the agent cannot re-use its own name | Medium | Medium | Auto-retry with `"<label>-2"` (D4) + the typed error explaining the terminal namespace |
| 3 | Alarm armed with the wrong target instance (the human expects a chat ping) | Medium | Medium | Stated explicitly in the tool doc + tools_note (the scheduler source is one-way; `scheduler.py:435-457`); OQ-1 |
| 4 | The floor is checked on the **scheduled** instant, not the **delivery** instant ⇒ a very-late delivery can look "on time" | Low | Medium | The message states the scheduled instant; the fresh-instance note tells the actor to verify state if late |
| 5 | Wrapper tz resolution drifts from the engine chain ⇒ the floor is computed against the wrong instant | Medium | Low | Parity test vs `_resolve_schedule_timezone` (D3/tests); the wrapper reuses the *public* helpers only |
| 6 | `self-alarm` leaks to another agent because an allow/deny-empty agent gets everything | High (policy) | Low | Runtime ari-only gate (D10) — the real boundary; the registration test asserts ari-only allow-lists |
| 7 | Phase-1 board id format changes ⇒ the link instruction breaks | Low | Low | The wrapper embeds the id verbatim + validates the shape; a phase-3 conformance check covers the drift |
| 8 | Re-arm at an identical instant collapses into a still-non-terminal prior job (idempotency key) ⇒ no second delivery | Low | Low | The wrapper rejects an identical instant on a `stopped` label; a terminal prior job does not block (`job_queue_service.py:1424-1481`) |
| 9 | Update-path rebuild gap (<1 s) loses a re-armed fire | Low | Low | Every armed instant is ≥30 min out (D9) ⇒ the window is irrelevant |
| 10 | New category breaks the frozen-binary tool universe (the tool vanishes in prod) | High | Low | `self_alarm` added to **both** `DYNAMIC_TOOL_NAMES` and `KNOWN_TOOL_NAMES` (the `secrets` precedent, `_tool_registry.py:1110-1120`); frozen-discovery test pinned |
| 11 | Prompt-surface drift (the tools_note section is a second home for the grammar) | Low | Medium | One canonical home (the tools_note section) + the tool doc references it; the forbidden-token/cross-ref tests enforce the closure |

---

## Success criteria

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 1 | `self_alarm` is registered and reachable | The registration test + a real instance tool list | Category resolves; name in `DYNAMIC_TOOL_NAMES` **and** `KNOWN_TOOL_NAMES`; 1 tool in the category |
| 2 | ARI-only | `grep -rl "self-alarm" agents/*/meta.json` + a non-ari call | Exactly **1** meta.json; a non-ari call returns an `ERROR` and creates no row |
| 3 | The 30-minute floor holds | Unit tests at 29 m / 30 m / 30 m+1 s | 29 m ⇒ `ERROR` (bash-sleep hint present) + **no** engine call; 30 m ⇒ accepted |
| 4 | The armed instant is exactly the floored instant | Wrapper first-fire vs the engine's `next_run_at_utc` echo | ≤1 s drift for once/daily/weekly/cron |
| 5 | A fired one-shot leaves a `stopped` row (not cancelled) and the label is not silently reusable | Engine-status check (task 1) + the dedupe tests | `status='stopped'`, `enabled=False`; a later same-label create takes the `-2` path |
| 6 | The reminder is actionable by a fresh instance | The message-composition test + a manual read of a real composed message | Contains prefix, label, scheduled local+UTC instant, board id + `board_get` instruction, fresh-instance note, action text |
| 7 | No collateral damage | The phase-2 regression run | Zero new failures; the stale-seal count unchanged (7); no `agents/ari/rule.md` / `workflow.md` diff |
| 8 | The phase-1 board contract is respected | The board-id regex test | `wi-<8 hex>` accepted; anything else ⇒ typed error; no phase-1 module imported |

---

## Open questions for the architect

1. **Chat bridge for the alarm's reply.** The scheduler source is one-way (`scheduler.py:435-457`) ⇒ the human sees
   nothing. Should phase 3 bridge a fired alarm's summary back to the originating chat surface (and if so, which
   surface — the instance is scheduler-owned, with no chat context)? A product call, not a code call.
2. **Recurring arm-now-imminent.** `daily 06:30` armed at 06:15 ⇒ first fire in 15 min ⇒ currently **rejected**
   (D9). Alternative: auto-skip to the next occurrence (tomorrow 06:30). Rejection is honest; auto-skip is friendlier
   but silently moves the user's stated schedule. Confirm **reject**.
3. **Interval recurrence.** `every 1h` is rejected because `create_schedule` exposes no interval variant (the adapter
   has an INTERVAL type, the create path does not). Worth an engine change (out of phase-2 scope)?
4. **Lateness-cap policy.** Should the deployment set `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS`? With it
   set, a missed alarm becomes a permanently-armed skip-loop (never fires without an operator update); without it, a
   missed alarm fires very late. Currently: no phase-2 change, documented only (Risk 1).
5. **Auto-arm v2 (the deferred board glue).** If the board service is to own `due_at → alarm`, it needs a
   caller-agent concept + a label/recurrence-change policy (D8). Confirm the deferral, or commission a v2 slice.
6. **Alarm scope semantics.** "Ari-only" means *the alarm is for ari itself* (today). Confirm it does not also mean
   "ari may arm alarms **for** other agents" (a different tool, out of scope).
