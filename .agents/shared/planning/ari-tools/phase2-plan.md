# Phase 2: `self_alarm` — alarm/timer wrapper for Ari

Date: 2026-10-19
Author: planner via plan-creation worker
Status: **AMENDED** — architect amendments A-18…A-29 folded against `2b297db7d`; **iteration-002** amendments (approver blocking
#2 + notes B1/B2/B8 + the cron-example note) folded in on 2026-10-20 — ready for reviewer
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
- New tool `self_alarm` (category **`self-alarm`**, **PRIVILEGED** — entry #10 of `PRIVILEGED_TOOL_CATEGORIES`),
  **ari-only** by three independent layers: the privileged category (default-deny strip + `resolve_tool_filter`
  empty-allow branch), the `agents/ari/meta.json` allow entry, and the in-body `agent_id == "ari"` runtime gate.
  Architect ruling **R1b** (§1.4): a non-privileged category would be resolved by `watcher` (the one agent with an
  empty allow list), which could then arm an alarm whose message text a fresh Ari instance executes **verbatim** —
  a privilege-escalation chain, not a visibility leak.
- ≥30-minute minimum-horizon gate with a **family-aware** rejection hint (bash-sleep in-turn for one-shots; a later time or
  an absolute next occurrence for recurring) that always prints the computed next fire.
- A **small** `when_or_every` grammar mapped onto the engine's four recurrence variants.
- Label-based reuse/dedupe semantics on top of the engine's terminal label namespace.
- One-shot post-fire lifecycle (engine-native "stopped") + honest statement of what the wrapper cannot observe.
- Self-contained reminder message (fresh-instance actionability) with board-link injection.
- Board `due_at` → alarm glue: **picked** (§D8) with a documented v2 deferral.
- The **tool** + its `_full_doc_` + the error hints (the discovery surface for phase 2).
- **Prompt surface — DEFERRED to the conventions step** ([plan-overview.md §8](plan-overview.md), step 4 of this commission): the new `## Self-Alarm` section of `agents/ari/tools_note.md` is authored **there**, not in phase 2. `tools_note.md` **IS** manifest-covered (161-entry committed manifest `agents/.prompt-artifacts.sha256`, row `:28`) ⇒ editing it in phase 2 would create a **second** re-stamp pass, which the **C1① consolidation ruling** forbids. Phase 2 therefore ships **no prompt-artefact edit** and **no re-stamp task**; the section's content spec lives in [plan-overview.md §8.4](plan-overview.md) and in D13 below.

### Out of scope
- Any new table, migration, daemon-side scheduler, or engine change (the engine already exists and is unchanged).
- `self_alarm` for any agent other than `ari` (pre-ruling).
- A "list my alarms" tool (Ari already has `task_schedule_list`; phase 3 may add a board-scoped view).
- Natural-language time parsing ("in a bit", "next tuesday-ish") and an interval recurrence ("every 1h") — the
  grammar is deliberately small and `create_schedule` exposes no interval variant (§D3; OQ-3 **DEFERRED-to-v2**).
- Editing **any** prompt artefact in this phase — `agents/ari/rule.md` / `soul.md` / `tools_note.md` / `workflow.md`
  and the leader equivalents (`growth.md` / `rule.md` / `soul.md` / `tools_note.md` / `workflow.md`). **Two DIFFERENT
  baselines cover them** and both are live: the **committed 161-entry manifest** `agents/.prompt-artifacts.sha256`
  (ari rows `:26`–`:29`, leader rows `:102`–`:106`) and the **35-entry `SEALED_SHA_BASELINES` test tripwire**
  (`tests/test_chart_image_delivery_e2e.py:1350-1398`, which covers `agents/ari/{rule,workflow}.md` +
  `agents/leader/rule.md` among them). The tripwire is **already red** — `agents/ari/workflow.md` is one of the **7
  stale pins measured at this HEAD** — so a new edit would move that baseline, and every prompt-artefact edit is
  **deferred to the conventions step** ([plan-overview.md §8](plan-overview.md), step 4), which owns the ONE
  in-commit re-stamp pass. **Only** `agents/ari/meta.json` is untouched by both baselines (zero `meta.json` rows in
  the manifest; absent from the tripwire) ⇒ **this phase's allow-list edit carries NO re-stamp obligation**.
  See [research-db-agents.md §5](research-db-agents.md) for the two-baseline distinction and the erratum.

---

## Engine facts (anchored in THIS worktree — re-verify before coding)

| Fact | Anchor |
|---|---|
| Engine = module functions; never dispatches; the adapter fires | `daemon/services/scheduling_service.py:3-7`, `:80-83` |
| `create_schedule` payload/recurrence set (`once\|daily\|weekly\|cron`), dup-label rejection | `scheduling_service.py:645`, `:668-670`, `:679-731` |
| **`create_schedule` REQUIRES `payload.agent`** — the *invoked* agent comes from the **payload**, not from the caller (`caller_agent_id` is audit-only, `scheduling_service.py:645-651`); the field is **required** (`ScheduleCreatePayload.agent`, `scheduling_service.py:132`) and the create path **fail-fasts on an unknown agent** (`get_registry().exists(payload.agent)` ⇒ `agent_id {…} does not exist`, `:673-676`) ⇒ the wrapper **MUST** set `agent="ari"` on **every** create: the alarm fires **into ari** (the receiving instance is a scheduler-owned ari instance), never under the caller's identity | `scheduling_service.py:132`, `:673-676` |
| **`ScheduleUpdatePayload` carries only `label`/`message`/`when`/`timezone`/`paused`/`priority`** — **no `weekday`, no `recurrence`, no `cron_expression`** ⇒ an update can move `when` but can **never** change the recurrence family or the stored DOW | `scheduling_service.py:208-214`, `:941-986` |
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
| Registration = **six** surfaces (decorator **above** `@tool`; `CATEGORY_MODULES`; `KNOWN_TOOL_NAMES`; `DYNAMIC_TOOL_NAMES`; `PRIVILEGED_TOOL_CATEGORIES` + its five exact-set pins; `tools.extend(...)` **before** `create_help_tool`/`scan_tools_for_full_docs`/`_apply_tool_filter`) — "three steps" is a lower bound; decorator-only is silently invisible | `daemon/tools/instance.py:2286-2289`, `:5698-6240`, `:6230-6238`; `daemon/tools/_tool_registry.py:342-367`, `:690+`, `:239-338` |
| `resolve_tool_filter`: empty allow **and** deny ⇒ everything allowed ⇒ an allow-list is not a hard boundary; runtime agent-gate precedent | `daemon/tools/instance.py:373-434`; `daemon/tools/blueprint.py:71-72` (`_is_writer_authorized`) |
| **`update_schedule` preserves the stored `enabled` flag when `paused` is omitted** ⇒ reusing a fired row (`enabled=False`) without `paused=False` leaves it disarmed and the rebuild is skipped | `scheduling_service.py:1005`, `:1019-1023`, `:1046-1047`; `sources/registry.py:515-526` |
| Default list is **not** status-filtered and the repository page caps at **100** rows (`created_at DESC`) | `scheduling_service.py:858-874`; `daemon/repositories/source/repository.py:217-224` |
| Duplicate-label races are **accepted** (no unique constraint on `name`) — two same-label rows can both fire | `scheduling_service.py:34-39` |
| `update_schedule` branches on the **stored** recurrence: `once` re-anchors, `daily`/`weekly` edit the cron in place, **`cron` raises** | `scheduling_service.py:941-986` |
| The **weekly** update branch rewrites **only the minute/hour fields** of the stored cron (`parts[0]`/`parts[1]`) ⇒ the **stored DOW (5th field) is preserved** — an update can never retarget the weekday | `scheduling_service.py:973-982` (`:980-981`) |
| `update_schedule` refreshes the stored zone **only when `payload.timezone` is set** (and differs from the stored value) ⇒ an update that omits `timezone` leaves a **stale** stored zone (the `when` re-anchor and every later cron evaluation then use the old zone) | `scheduling_service.py:931-936` |
| The engine stores **no** `weekday` key — the stored cron's DOW field is authoritative (`get_schedule` echoes `weekday=None`, "weekday echo is create-time only") ⇒ a wrapper that needs the DOW must **derive it from the stored cron expression** | `scheduling_service.py:627` |
| `update_schedule` **mutates before it answers**: it drains the adapter (`:1011`), writes the config (`:1026-1034`) and **rebuilds/starts** it (`:1046-1047`) ⇒ a failure of the post-call assertion means the stored row is **already mutated and armed** — the assertion is a *detector*, not a *guard* | `scheduling_service.py:1011`, `:1026-1034`, `:1046-1047` |
| One-shot idempotency key = `scheduler:{source_id}:{run_at}` (one-time only); a non-terminal prior job within 24 h collapses the submit | `scheduler.py:1090-1094`; `job_queue_service.py:1424-1481` |
| Lateness cap is **daemon-global** — never a payload field; the adapter reads it at fire time and the wrapper cannot override it | `daemon/config.py:2617-2623`; `scheduler.py:705-728` |
| Public tz/cron helpers reusable without duplicating engine logic | `daemon/util/tz.py:283` `resolve_timezone`, `:353` `anchor_local_to_utc`, `:529` `compute_next_cron_fire`; `daemon/services/user_timezone_utils.py:31` |

---

## Tool contract (binding shape — `self_alarm(label, when_or_every, message_to_self, link_board_item?)`)

```python
self_alarm(
    label: str,                      # 1-115 chars; stored as "self-alarm:<label>" (the effective label, D4)
    when_or_every: str,              # grammar below (3 families)
    message_to_self: str,            # "what to do" — appended verbatim as the Action line
    link_board_item: str | None,     # "wi-<uuid8>" (validated by regex, embedded verbatim)
) -> dict
```

Success return (mirrors the engine echo + wrapper extras — **never** a bare string on success):

```json
{ "ok": true, "source_id": "...", "label": "self-alarm:deploy-check", "status": "running", "recurrence": "once",
  "instance_mode": "new_instance", "timezone": "Asia/Ho_Chi_Minh", "armed_at_utc": "...",
  "first_fire_at_local": "2026-10-20T09:00:00+07:00", "first_fire_at_utc": "2026-10-20T02:00:00+00:00",
  "board_item": "wi-1a2b3c4d", "reused": false, "tz_warning": "",
  "lateness_cap_seconds": null, "message_preview": "[self-alarm] self-alarm:deploy-check …" }
```

- `label` echoes the **effective** full label (the `self-alarm:`-prefixed name actually stored; see D4).
- `status` is the **actual** row status after create/update. The create-response status races
  `'stopped'` → `'starting'` → `'running'` (`repository.py:82` / `registry.py:611`, `:785`); `'starting'`
  **counts as armed** and is reported as such.
- `lateness_cap_seconds` echoes the daemon-global `one_shot_max_lateness_seconds`: `null` = unlimited lateness
  (a missed alarm fires late), an integer = a missed alarm beyond the cap is skipped permanently (see
  "Tool-doc obligations"). The wrapper cannot set it, so it must disclose it.
- `tz_warning` is the **wrapper's own** warning, not the engine's: passing an explicit `timezone` short-circuits
  the engine's tz chain (`daemon/util/tz.py:323-327`) ⇒ the engine's UTC-fallback warning can never fire on this path.

Failure → `"ERROR: {msg}"` (house rule; the tool never raises — `daemon/tools/scheduling.py:57-59`).

### `when_or_every` grammar (exact)

| Family | Accepted form | Maps to engine | Notes |
|---|---|---|---|
| Absolute one-shot | `2026-10-20T09:00:00+07:00` / `…Z` / `2026-10-20T09:00:00` (naive) | `recurrence=once`, `when=<ISO>` | Offset-aware ⇒ trusted as-is; naive ⇒ anchored in the resolved tz (`anchor_local_to_utc`) |
| Relative one-shot | `45m`, `in 45m`, `2h`, `1d30m`, `90s` | `recurrence=once`, `when=<now+Δ as aware ISO>` | Regex `^(?:in\s+)?(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hr|d|day)s?$` (case-insensitive) |
| Recurring | `daily 06:30` | `recurrence=daily`, `when="06:30"` | Engine builds cron `M H * * *` |
| Recurring | `weekly 1 06:30` | `recurrence=weekly`, `weekday=1`, `when="06:30"` | DOW canonical: 0=Sunday..6=Saturday (engine convention) |
| Recurring | `cron 0 9 * * *` | `recurrence=cron`, `cron_expression="0 9 * * *"` | Raw 5-field expr — the documented **armable** cron example (daily-at-09:00, 24-h period). **Period rule (the D9 floor applies to the FIRST fire):** a raw cron whose period is **≤ 30 min** (e.g. `*/30 * * * *`) is **structurally unarmable** — the longest possible wait to its first fire is one period, so the first fire is always `< 30 min` away and the floor rejects it *every* time. A 60-min period (`0 * * * *`) is rejected whenever the arm lands inside the 30 min before the top of the hour. The documented example clears the floor except in the 30 min before 09:00 |
| **Rejected** | `every 1h`, `every day at 6` | — | The engine has no interval recurrence in `create_schedule` ⇒ reject with the hint *"use a raw cron expression instead — e.g. `cron 0 */2 * * *` (every two hours)"*. The hint must **not** point at a ≤ 30-min period (`*/30 * * * *`): such a cron can never satisfy the floor on its first fire |
| **Rejected** | anything else | — | Typed error listing the accepted forms (keep the surface small) |

**Timezone defaulting.** The wrapper resolves the tz **once**, server-side, via the *same public primitives* the engine uses —
`resolve_timezone(explicit, default=…)` + `get_user_timezone_preference(project_repo)` + `SchedulingConfig().default_timezone` —
then **hands the engine an absolute instant** (offset-aware ISO) for one-shots, or the resolved IANA name for recurring forms ⇒ the
stored `run_at` is exactly the instant the wrapper floored and the engine's tz chain is not re-derived. Because the engine receives an
already-resolved zone, its own fallback warning can never fire ⇒ the wrapper echoes **its own** `tz_warning` (from the identical
resolution chain) verbatim, never suppressed. A **parity test** pins the wrapper's resolution to
`scheduling_service._resolve_schedule_timezone` (§Tests) so chain drift is caught, not guessed.

**Recurring first fire.** Computed with the *same* helper the engine's `_compute_next_run` uses — `compute_next_cron_fire(cron_expr,
now, zone)` (`scheduling_service.py:474-478`) ⇒ DST fold/gap semantics are identical by construction (no duplicated DST logic).

### Tool-doc obligations (binding — architect §4.1, §4.2, §4.4)

`self_alarm._full_doc_` — shipped in **phase 2** — **and** the `agents/ari/tools_note.md` `## Self-Alarm` section —
authored in the **conventions step** ([plan-overview.md §8](plan-overview.md), step 4) — must state all five items below.
A doc-pin test asserts each one is present in `_full_doc_` (same pattern as the forbidden-token/cross-ref closure in
`tests/unit/test_scheduling_registration.py:44-58`); the prompt-side assertions move to the conventions step's
checklist (§8.6), because the section does not exist during phase 2.

1. **Lateness — both cap states, named explicitly, plus the skip signature.** `one_shot_max_lateness_seconds` is
   daemon-global config (`daemon/config.py:2617-2623`) read at fire time; the wrapper cannot set or override it.
   - Default `None` ⇒ unlimited: an alarm whose instant passed while the daemon was down **fires late at the next boot**.
   - Cap set to N ⇒ a boot later than N writes a `schedule_executions` row `status='skipped'`, **no dispatch**, and the
     schedule **stays armed** — it re-evaluates the same past instant every 5 s (`scheduler.py:559`,
     `SCHEDULER_ERROR_RETRY_S = 5.0`) with **no self-heal** and **no user-visible signal**. The only remedy is a manual
     `task_schedule_update`.
   - Every response echoes `lateness_cap_seconds` so the agent knows its exposure.
2. **The board is the user-visible surface — promise the board, never a ping.** Promise: the alarm is delivered into a
   fresh Ari instance at the scheduled instant; that instance performs the action and leaves evidence **on the board**
   (status/notes) and in any job it dispatches; the phase-3 live board view shows the change. Must **not** promise: a
   ping in chat/Discord/Slack, a reminder in the conversation, or an interruption. `SchedulerAdapter.send` only logs and
   returns `True` (`scheduler.py:435-457`), and the receiving instance is scheduler-owned (`force_new=True`,
   `external_user_id = source_id`, `:1063-1069`) ⇒ no chat identity exists to notify.
3. **What the wrapper cannot observe.** There is no agent-facing post-fire hook; "it fired" is readable only from the row
   (`status='stopped'`, `enabled=False`). No auto-cleanup, no auto-done on the board item.
4. **List semantics.** `task_schedule_list` is **not** status-filtered by default and is capped at **100** rows
   (`created_at DESC`) ⇒ armed rows eventually hide behind stopped ones; use `status='running'` to see armed alarms.
   `stopped` means **dispatched**, not **acted upon** — the completion callback fires right after the enqueue
   (`scheduler.py:599-609`).
5. **Label namespace.** Every wrapper-minted label carries the `self-alarm:` prefix (D4) — the wrapper's namespace is
   disjoint from MCP-server / Slack source names, because the label lookup has no `source_type` filter.

---

## Design decisions made in this phase plan

**Refinements vs the pre-rulings (explicit, no pre-ruling is re-litigated):** (i) the ≥30-min floor is checked
**pre-create *and* pre-update** — always on the wrapper-computed first fire and on **both** branches (create and reuse), so a
rejected alarm burns no label **and mutates no stored row** — the pre-ruling fixed the threshold, not the check point; (ii) the ari-only rule is enforced **three times** — a **privileged category**,
the ari allow entry, and a runtime gate — because an allow-list is not a hard boundary and a literal tool-name
entry bypasses the category gate (D10) — a strengthening, not a change; (iii) the one-shot post-fire action is
**mark-done (engine-native)**, not auto-cancel — the pre-ruling allowed either, and cancel is terminal + label-burning
(D5); (iv) the board `due_at` glue picks the **agent-prompt convention** and defers creation-time arming to v2 (D8);
(v) the recurring "first fire <30 min away" case is **rejected**, not auto-skipped to the next occurrence (D9, OQ-2);
(vi) the module split adds a **pure service module** beside the tool module (D2) — an internal shape choice.

**D1 — The wrapper exists; it is not a re-implementation of `task_schedule`.** Verified value-add: (a) the ≥30-min horizon gate with
the family-aware hint — `task_schedule` validates *shape*, not horizon (`scheduling_service.py:645-731`); (b) fresh-instance message
composition — `task_schedule` stores the raw `message` and the adapter sends it verbatim (`scheduler.py:962-964`); (c) label-dedupe
UX — the engine's duplicate label `ValueError` is opaque to an agent and the burned-label case (D4) is invisible; (d) board-link
injection. All four are *wrapper* concerns; none need engine changes.

**D2 — New module pair, not a fold into `daemon/tools/scheduling.py`.** `CATEGORY_MODULES` is a category→module map
(`_tool_registry.py:690+`) ⇒ a second category needs its own module path. Split: `daemon/services/self_alarm_service.py` = **pure**
helpers (parse → fire plan → floor → message composition; no tool/LLM plumbing; unit-testable) and `daemon/tools/self_alarm.py` =
the LLM-facing tool + engine calls + dedupe. Rationale: the v2 board auto-arm path (D8) reuses the *pure* helpers without importing
a tool module — a 5-line call instead of a refactor. The `scheduling` module stays granted to leader/jober and holds no ari-only
policy.

**Registry contract (binding).** The `CATEGORY_MODULES["self-alarm"]` **value MUST be a `daemon.tools.*` path**
(`"daemon.tools.self_alarm"`). The AST scanner resolves the value as `daemon/tools/<parts[2:]>.py` (`_tool_registry.py:504-506`);
a `daemon.services.*` value is never discovered and the exact-equality drift pin fails at boot. The split above is legal
**only** because the category points at the tool module while the pure helpers live in the service module. The
`tools.extend(...)` call must sit **before** `create_help_tool` (`instance.py:6230`), `scan_tools_for_full_docs` (`:6235`) and
`_apply_tool_filter` (`:6238`) — a later extend is either silently invisible to the allow list or bypasses the privileged strip.

**D3 — Small, prefix-disambiguated grammar (§tool contract).** No free-form natural language, no "tomorrow at 9" (the LLM computes
the absolute ISO with its `time` tool when it wants that). `every <duration>` is rejected (no engine interval variant) rather than
silently mistranslated. **The cron family is documented with an armable example only.** The engine's cron support is real — the
create path validates the expression with `croniter` (`scheduling_service.py:684-694`) and stores it raw — so a raw 5-field
expression is *accepted*; but the **≥30-min floor applies to the first fire** (D9) and a raw cron's longest possible wait to its
first fire is **one period** ⇒ a cron with a **≤ 30-min period** (`*/30 * * * *`) is **structurally unarmable** — its first fire
is always < 30 min away and the floor rejects it every time. The grammar therefore documents **`cron 0 9 * * *`** (24-h period)
as the cron example, and the `every 1h` rejection hint points at a raw-cron form with a period comfortably above the floor
(`cron 0 */2 * * *`) rather than at `*/60` — `*/60` is a non-portable step (60 is outside the 0-59 minute field range) and a
60-minute period lands inside the floor window about half the time.

**D4 — Dedupe/reuse by label: namespaced, reuse-in-place with an explicit resume, never cancel-to-free — and never leave a
duplicate armed.**

*Namespace rule (A-27).* The human-supplied `label` is always stored as **`self-alarm:<label>`** (the **effective** label). Input is
validated at **1-115 chars**: the engine caps `label` at 128 (`ScheduleCreatePayload.label`, `scheduling_service.py:131`) and the
stored name is the 11-char prefix + input + up to a 2-char `-N` retry suffix (`-5` is the cap) ⇒ 115 reserves the worst case. Reason:
`get_source_config_by_name` filters **only**
on `name` — there is **no `source_type` filter** (`daemon/repositories/source/repository.py:212-216`) — while `list_schedules`
**does** filter by source type (`scheduling_service.py:877-878`) ⇒ an unprefixed label can collide with an MCP-server or Slack
source name and the collision is invisible in the schedule list. Every response, hint and error text echoes the effective label;
the wrapper never accepts a user string that already starts with `self-alarm:` as-is (it rejects it with a "the prefix is added
automatically" hint).

Engine facts: duplicate label ⇒ `ValueError("label already exists: …")` (`scheduling_service.py:668-670`); the name lookup ignores
status; cancel is terminal and `update` refuses cancelled rows (`:923-926`); no hard delete exists for scheduler rows ⇒ **a
cancelled label is burned forever**. The table has **no unique constraint on `name`**: two racing creates with the same label both
survive and **both fire** (`scheduling_service.py:34-39`).

**Update-surface limits (load-bearing for the steps below).** `ScheduleUpdatePayload` carries **no `weekday` / `recurrence` /
`cron_expression`** (`:208-214`) ⇒ an update can move the *time* but never the *shape*; the **weekly** branch rewrites only the
minute/hour of the stored cron and **preserves the stored DOW** (`:973-982`); the **`cron`** branch **raises** on a `when`
(`:983-986`); the stored zone is refreshed **only** when `payload.timezone` is set (`:931-936`); and the service **mutates before
it answers** (adapter drained `:1011`, config written `:1026-1034`, adapter rebuilt/started `:1046-1047`) ⇒ a failed
post-call assertion means the row is **already mutated and armed**.

Policy, in order (after the D11 `project_id` resolution — which must succeed before anything below runs). The sequence is
**strictly ordered — verify-compatibility → floor → update + assert → fallback + neutralization**: **steps 1-3 never mutate the
stored row**, step 4 is the only mutation, and step 6 is what makes the fallback path safe.

1. **Step 1 — Resolve the label (read-only probe).** Look the effective label up
   (`asyncio.to_thread(source_repo.get_source_config_by_name, effective_label)` — the seam `task_schedule_cancel` uses,
   `daemon/tools/scheduling.py:206-216`). Read the stored row's `recurrence`, `schedule` (the raw cron), `timezone`, `run_at`,
   `enabled` and `status` from the row / `row.config`. **This read is the compatibility probe — no engine write has happened yet,
   and none may happen before step 4.** No row ⇒ the create path (step 4a).
2. **Step 2 — Compatibility gate (BEFORE any mutation).** Compare the **requested** recurrence family with the **stored** one
   and — for a weekly row — the **requested weekday** with the **stored DOW** (derived from the stored cron's 5th field; the
   engine stores no `weekday` key — `scheduling_service.py:627`). Five outcomes:

   | Stored row | Requested | Verdict | Action |
   |---|---|---|---|
   | `cancelled` | anything | **burned label** | ⇒ **step 4a** (fresh-label candidate; `update` refuses cancelled rows, `:923-926`) |
   | `running` / `starting` / `stopped` | **same family**, same DOW (weekly), same expression (cron) | **compatible** | ⇒ **step 3** (floor) → **step 4b** (update) |
   | `running` / `starting` / `stopped` | **different family** | **mismatch** | ⇒ **step 4a** (fresh-label candidate) — **`update_schedule` is never called** |
   | `running` / `starting` / `stopped` | same family `weekly`, **different DOW** | **mismatch** | ⇒ **step 4a** — **`update_schedule` is never called** |
   | `running` / `starting` / `stopped` | same family `cron`, **different expression** | **mismatch** | ⇒ **step 4a** — **`update_schedule` is never called** |

   *Why the gate is mandatory (it is not an optimisation).* `ScheduleUpdatePayload` has **no `weekday` / `recurrence` /
   `cron_expression` field** (`scheduling_service.py:208-214`); the weekly branch rewrites **only the minute/hour** of the stored
   cron and **preserves the stored DOW** (`:973-982`); the `cron` branch **raises** on `when` (`:983-986`). An update can
   therefore only ever move the *time*, never the *shape*: a weekly alarm asked to move to another weekday would **keep firing
   on the old weekday**, and a `once` row handed a recurring request would be silently downgraded to a **one-shot** at the next
   occurrence. Mismatches consequently **never reach the engine**: the original row is **never mutated**, so a duplicate-armed
   state is impossible by construction. The per-family diagnostic text survives as the **fallback reason** in the response
   (`fallback_reason: "recurrence_family_mismatch" | "weekly_dow_mismatch" | "cron_expression_mismatch"`, carrying the stored vs
   the requested family/DOW/expression) — never one generic "recurrence cannot be changed" string, and never a bare failure that
   leaves the agent unable to arm its alarm.
3. **Step 3 — Floor on the wrapper-computed first fire (BOTH branches).** `_enforce_floor` runs on the **wrapper-computed first
   fire** — **on the reuse path exactly as on the create path** (D9; worker note B2). The check is `first_fire - now >= 1800 s`
   against the absolute instant and it runs **before any engine call** ⇒ a below-floor request **never mutates the stored row**
   and never burns a label. The first-fire instant is computed per the **target row's own semantics**: `once` ⇒ the requested
   absolute instant; `daily`/`weekly`/`cron` ⇒ `compute_next_cron_fire(<the cron the row will carry>, now, resolved_tz)` — for a
   weekly row that cron is `(requested HH:MM, **stored DOW**)`, because the update preserves the stored DOW
   (`daemon/util/tz.py:529` — the same helper the engine's `_compute_next_run` uses, `scheduling_service.py:469-478`). The
   rejection text is family-aware and prints the computed next fire (D9).
4. **Step 4 — Mutate exactly one row.**
   - **4a — No row (or a burned/fresh candidate) ⇒ CREATE.** `create_schedule` with the **full** payload: `label` = the
     effective label (or the candidate), **`agent="ari"`** — **required** by `ScheduleCreatePayload.agent`
     (`scheduling_service.py:132`; the create path fail-fasts on an unknown agent, `:673-676`) and semantically correct: the
     alarm fires **into ari** (worker note B1) — `message`, the family-shaped `when`/`weekday`/`cron_expression`,
     `timezone=<resolved IANA name>`, `project_id` (D11), `instance_mode="new_instance"` (D6). **Duplicate-label handling:**
     duplicate label ⇒ `ValueError("label already exists: …")` (`scheduling_service.py:668-670`); the name lookup ignores status
     and the table has **no unique constraint on `name`** ⇒ two racing creates with the same label both survive and **both
     fire** (`scheduling_service.py:34-39`). Therefore: **collision-check every candidate** with the same name lookup before
     creating (the lookup cannot be trusted alone — the create itself races), and after a successful create **re-read by name
     and report duplicates** if more than one row matches (the lookup returns only the first match). **Never cancel the
     duplicate loser** — cancel burns the name and hides evidence; report the duplicate id(s) in the response instead. Always
     echo the effective label actually created. **Burned-label naming:** a `cancelled` row ⇒ candidate `"<effective>-2"` (then
     `-3`, cap 5).
   - **4b — Compatible row ⇒ UPDATE in place, then ASSERT.** `update_schedule(source_id, {...})` with a **family-shaped
     payload**:

     | Stored family | Reuse payload | Notes |
     |---|---|---|
     | `once` | `{when: <requested ISO instant>, message: …, timezone: <resolved IANA>, paused: False}` | the `once` branch re-anchors the ISO to the **stored** zone (`scheduling_service.py:941-972`) |
     | `daily` | `{when: "HH:MM", message: …, timezone: <resolved IANA>, paused: False}` | the branch rewrites the cron's minute/hour in place |
     | `weekly` | `{when: "HH:MM", message: …, timezone: <resolved IANA>, paused: False}` | same — and the stored DOW is preserved (step 2 guarantees it is the requested one) |
     | `cron` | `{message: …, timezone: <resolved IANA>, paused: False}` — **no `when` key at all** | the `cron` branch **raises** on a `when` (`:983-986`); the stored expression stays authoritative and unchanged |

     - **`timezone` is MANDATORY in the reuse payload** — not cosmetic (amendment (a)). `update_schedule` refreshes the stored
       zone **only when `payload.timezone` is set** and differs (`scheduling_service.py:931-936`) ⇒ omitting it leaves the stored
       zone **stale**, and the re-anchored `when` plus every later cron evaluation then use the **old** zone ⇒ the alarm fires at
       the wrong instant. The wrapper therefore always sends its **resolved IANA name** — the same value it hands the create path
       — so the stored zone **cannot drift stale on reuse**. `timezone=None` is never sent on the update path.
     - **`paused=False` is mandatory** — it is the *resume* branch (`scheduling_service.py:1005-1007`, `:1015-1017`) and it forces
       `enabled=True` before the rebuild. Without it the update takes the "keep the previous enabled flag" branch
       (`:1018-1023`), a fired row is `enabled=False` (`daemon/sources/registry.py:514-526`), the rebuild is skipped
       (`:1046-1047`), the row stays `status='stopped'` and the boot filter skips it (`registry.py:291-310`) ⇒ **a silently
       disarmed alarm that never fires**.
     - **Assert the response**: `paused is False`, `status != "stopped"` (accept `running`/`starting`), and `next_run_at_utc`
       equal to the **wrapper-computed first fire for the target row's semantics** (±1 s) — the requested absolute instant for
       `once`, the computed next occurrence for `daily`/`weekly`/`cron`. A recurring row's response echo is a **next occurrence**,
       never the requested wall-clock time ⇒ comparing it to the raw request would fail every recurring reuse.
     - **A `stopped` row (fired one-shot, or paused) must be re-armed with a strictly later instant** (for a recurring row: the
       requested time/expression must yield a next occurrence later than the stored one). Under the ≥30-min floor an
       identical-instant re-arm of a **fired** row is already impossible (the stored instant is past), and a message-only edit
       keeps the stored `run_at` ⇒ the rebuilt adapter re-enqueues under the same `scheduler:{source_id}:{run_at}` key
       (`scheduler.py:1090-1094`) ⇒ a still non-terminal prior job within 24 h collapses the submit
       (`job_queue_service.py:1424-1481`) ⇒ **silently never delivered**. The typed error names the fix ("a fired alarm must be
       re-armed with a new, later time").
5. **Step 5 — Fallback: fresh-label CREATE.** If the step-4b assertion fails, the tool **does not report success**. The wrapper
   mints a **fresh label** (the step-4a candidate naming: `"<effective>-2"`, then `-3`, cap 5), **collision-checks** it with the
   same name lookup, and creates it through the **same create path as step 4a** (full payload, `agent="ari"`, the
   collision-check + post-create duplicate report). **But the step-4b update has already mutated and re-armed the original row**
   (`update_schedule` writes the config at `:1026-1034` and rebuilds/starts the adapter at `:1046-1047` **before** it returns)
   ⇒ the original is now a **second armed row** for the same request ⇒ **step 6 is mandatory**.
6. **Step 6 — Neutralize the mutated original (mandatory on the fallback path).** The mutated original MUST be disarmed by
   explicit design. **The plan mandates a PAUSE: `update_schedule(source_id, {paused: True})`** — the engine's own
   "disarm-without-destroying" primitive (`paused=True` ⇒ `config_enabled=False` + `status='stopped'`, `:1013-1014`; the adapter
   is drained at `:1011` and **not** rebuilt at `:1046-1047` ⇒ the row cannot fire, while the row, its history **and its label
   survive**).
   - *Why pause — and not the two alternatives.* (a) **Not "push its next fire beyond the new alarm's"**: a moved `when` leaves the
     original **armed**, so it still fires (later) ⇒ a *delayed second alarm* — the same "fires twice" defect with a different
     trigger; and `update` can move `when` but **not the recurrence family** (`scheduling_service.py:208-214`, `:941-986`), so for
     a recurring original there is no `when` that expresses "skip the next occurrence" at all. (b) **Not "cancel with a note"**:
     cancel is **terminal** and **burns the label forever** (`:923-926`; no hard delete exists for scheduler rows) ⇒ every
     assertion failure would burn the agent's own `self-alarm:<label>` name and push it down the `-2` chain for good.
     (c) **Pause** is reversible, does **not** burn the label (the next request finds the row `stopped` and re-arms it under
     step 4b), needs **no** `when` — so the "cannot change the recurrence family" gap does not bite — and it **preserves the
     evidence** of the failed reuse. The "never cancel the duplicate loser" rule (step 4a) is therefore **extended, not
     contradicted**: the create-race loser is **never cancelled** (it is reported), and the mutated original is **paused**
     (also never cancelled, and not destroyed).
   - *Mandatory shape.* The pause is issued **unconditionally** on the fallback path (idempotent for an already-disarmed row) and
     carries **no `when`** — the row's `run_at` stays as the failed update left it (the row is disarmed, not re-timed). The
     wrapper **asserts** the pause response (`paused is True`, `status == 'stopped'`).
   - *If the pause itself fails* ⇒ the tool returns a typed `ERROR` naming **both** rows (the mutated original and the fresh
     label) and their ids, and **never** claims success — a still-armed original beside a fresh label is exactly the
     duplicate-armed state this step exists to prevent.
7. **Step 7 — Report.** The response echoes the **effective label actually created/armed** and, on the fallback path, the
   **neutralized original** (its `source_id` + `paused` state) so the agent can see that the old label is disarmed and which
   label is live. A mismatch fallback (step 2) additionally discloses the **untouched pre-existing row** (label + `source_id` +
   state): it is still armed with its **own** schedule — the honest consequence of not mutating it.

**D5 — One-shot post-fire: "mark-done" is ENGINE-NATIVE; the wrapper does not cancel.** After a one-shot fires the adapter's
`on_complete_callback` sets `status='stopped'` + `enabled=False` (`registry.py:514-526`); the row persists (history preserved) and
is **not** cancelled. Cancelling would be worse on every axis: terminal, burns the label, hides the row from the default list,
destroys the "it fired" evidence. Clutter control = **status filter** (`running` = armed; `task_schedule_list` already supports it).
**Two list facts the tool doc must carry (A-21):** the default `list_schedules(status=None)` is **not** status-filtered — it returns
running **and** stopped rows — and the repository page caps at **100** rows ordered `created_at DESC`
(`scheduling_service.py:858-874`; `daemon/repositories/source/repository.py:217-224`) ⇒ armed alarms eventually hide behind stopped
ones; `status='running'` is the armed filter. And **`stopped` means *dispatched*, not *acted upon*** — the completion callback
fires right after the enqueue (`scheduler.py:599-609`), so the action itself can still be lost or doubled.
Honest limit: the wrapper **cannot observe the fire** — there is no agent-facing post-fire hook (the adapter's `execution_callback`
is internal) ⇒ "alarm fired" is readable only from the schedule row's status. No phase-2 auto-cleanup (a "board item → done on
alarm" hook would need that same internal seam).

**D6 — `instance_mode="new_instance"`, fixed.** Rationale: the alarm is a *fresh task*, not a continuation (a rehydrated checkpoint
would dilute it); a one-shot fires once, so `reuse_instance` buys no continuity; and `reuse_instance` wraps the message in
`_format_continuation_message` (run-number text), meaningless for a one-shot (`scheduler.py:801`, `:962`). **Which instance receives
it:** a scheduler-owned Ari instance (`source_id` = the schedule row id) — **not** any of Ari's chat instances — and the reply is
**not** delivered to a chat surface (the scheduler source is one-way; `SchedulerAdapter.send` only logs, `scheduler.py:435-457`).
The tool doc states the promise contract plainly (see "Tool-doc obligations" #2): **the board is the user-visible surface** — the
reminder lands in the agent's own workspace, the instance acts autonomously (dispatch a job, update the board), and the phase-3
live board view shows the result. It **never** promises a ping, a chat/Discord/Slack message, or an interruption: the scheduler
source is one-way and the receiving instance has no chat identity (`scheduler.py:435-457`, `:1063-1069`). Chat-bridge = OQ-1
(**RESOLVED**: no new infrastructure in v1 — the board is the only guaranteed surface).

**D7 — Fresh-instance message contract (wrapper-composed, agent supplies only the action).** The wrapper stamps the prefix, injects
the context, appends the agent's text verbatim:

```
[self-alarm] <effective label>
Scheduled fire: <first_fire_local>  (<first_fire_utc>)
Armed at: <armed_at_utc>   Schedule: <recurrence> | tz <tz> | id <source_id>
Board item: wi-1a2b3c4d — call board_get with that id to load its title, body, status and notes before acting.
You are a fresh instance with no memory of this request; everything you need is in this message.
Action: <message_to_self>
```
The "fresh instance, no shared state" line is mandatory (Ari instances are separate trees). The scheduled instant is stated so a
late delivery is detectable ("if this is more than a few minutes past the scheduled time, verify current state before acting").
The header line carries the **effective** label (`self-alarm:<label>`) so the fresh instance can correlate it with
`task_schedule_list` without guessing the prefix.

**D8 — Board `due_at` glue: PICK = agent-prompt convention (Option 2). Creation-time auto-arming DEFERRED to v2.** Auto-arming from
the board service is **not** clean against the actual API shape, for five concrete reasons: (1) `create_schedule` needs
`caller_instance_id`/`caller_agent_id` **and** a target `agent`, while the board tools are granted to **ari *and* leader** ⇒ the
board service would need a new caller-agent concept to honour the ari-only pre-ruling; (2) label synthesis + the burned-label rule
(D4) would leak into the board service; (3) a `due_at` change ⇒ reschedule via `update_schedule`, which **cannot change the
recurrence family** and has a documented <1 s missed-fire window (`scheduling_service.py:71-78`); (4) `due_at` removal ⇒ cancel ⇒
terminal + label burn + hidden history; (5) the board service is built **in parallel** in phase 1 and its API is not frozen —
coupling phase 2 to its internals creates a merge-order dependency. ⇒ Phase 2 arms from the **agent side**: the convention
("whenever you set or move a board item's due date, arm or re-arm a self_alarm for it and pass `link_board_item`") lives in
the tool's `_full_doc_` + the tool's error hints **in phase 2**, and in the `## Self-Alarm` section of
`agents/ari/tools_note.md` — which is authored in the **conventions step** ([plan-overview.md §8](plan-overview.md), step
4 of this commission), because `tools_note.md` **IS** manifest-covered (committed manifest `agents/.prompt-artifacts.sha256`
row `:28`) and the **C1① consolidation ruling** defers every prompt-artefact edit to that single step ⇒ **one** in-commit
re-stamp pass for the whole commission. The
board's `due_at` stays the **source of truth for the deadline**; the alarm is the **reminder mechanism**; the board status
transition stays Ari-driven. The wrapper embeds the board id **verbatim** (validated `^wi-[0-9a-f]{8}$`) and instructs the fresh
instance to `board_get` it ⇒ phase 2 depends on phase 1 only through the pinned id format, never its internals.

**D9 — ≥30-minute floor lives in the wrapper, PRE-create *and* PRE-update (both branches).** `_enforce_floor` runs on the
wrapper-computed first-fire instant **before any engine call** ⇒ a rejected alarm creates **no row and burns no label**
(create → validate → cancel would burn the label, D4). **The same floor is re-enforced on the REUSE path** (worker note B2):
the check runs on the wrapper-computed first fire for **both** the create branch **and** the reuse/re-arm branch (D4 step 3),
**before** `update_schedule` is called ⇒ a below-floor reuse **never mutates the stored row** (the update mutates before it
answers — `scheduling_service.py:1011`, `:1026-1034`, `:1046-1047` — so the floor must run *before* it, not after it).
The floor is stated in **minutes** (the user-facing policy) but compared in **seconds** against the absolute instant
(`first_fire - now >= 1800 s`), and applies to the **first-fire offset only**: a recurring alarm whose first fire is ≥30 min away is
accepted; later fires are unconstrained (≥ one recurrence period away). **A recurring arm whose first fire is < 30 min away is
rejected, not auto-skipped** (`daily 06:30` armed at 06:15 ⇒ first fire in 15 min ⇒ reject) — silently moving the user's stated
schedule is worse than an explicit error; the escape hatch is an absolute ISO for the next occurrence (OQ-2). **The same rule
makes a short-period raw cron unarmable**: a cron whose period is ≤ 30 min (e.g. `*/30 * * * *`) has a first fire **always**
< 30 min away (the longest possible wait is one period) ⇒ it can never satisfy the floor ⇒ the grammar table documents
`cron 0 9 * * *` (24-h period) as the armable cron example. **The rejection text is
family-aware and always prints the computed next fire (A-22)** — bash-sleep is *wrong advice* for a recurring schedule:

```
# one-shot family (absolute / relative):
ERROR: self_alarm requires a minimum horizon of 30 minutes — the requested fire time is 12m away
(2026-10-20T01:07:00+07:00). For anything shorter, use bash with `sleep <seconds>` inside the current turn
instead of an alarm.

# recurring family (daily / weekly / cron):
ERROR: self_alarm requires a minimum horizon of 30 minutes — the next `daily 06:30` fire is 12m away
(2026-10-20T06:30:00+07:00). To start recurring later, arm this schedule after the first fire
(2026-10-21T06:30:00+07:00) or choose a later time.
```

Both texts carry the **computed next fire** (local, with offset) and are selected from the parsed family — never guessed. An
absolute one-shot for the next occurrence remains a valid escape hatch for the recurring case.

**D10 — `self-alarm` is PRIVILEGED, and ARI-only is enforced a second and third time.** Architect ruling **R1b** (§1.4) is
binding: the category joins `PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py:239-338`) — **entry #10**. The three
layers, each independent:

1. **Privileged category.** Both default-allow paths skip it: the empty-allow branch of `resolve_tool_filter` (`instance.py:464-470`)
   and the `_strip_privileged_category_tools` strip (`instance.py:6243-6271`). The inheritance universe is **`agents/watcher/meta.json`**
   (the only empty-allow agent, and the attacker-influenced one) plus every `tools`-less agent (`agent_create` writes no `tools` key,
   `daemon/tools/agent_mother.py:282-295`; empty allow/deny normalises to `None`,
   `daemon/repositories/custom_agent_configs/repository.py:193-198`). Why it matters: `watcher` arming an alarm means a **fresh Ari
   instance executing caller-supplied text verbatim** with Ari's full allow list (`bash`, `system_upgrade`, `secrets`, …).
2. **Allow entry.** `tools.allow` gets `"self-alarm"` in **`agents/ari/meta.json` only** (never leader — the pre-ruling).
3. **Runtime gate.** The tool body rejects any caller whose `agent_id != "ari"` with a typed error — the `blueprint` writer-gate
   precedent (`daemon/tools/blueprint.py:71-72`). This layer is **required even with** the privileged category, because a **literal
   tool-name** allow entry bypasses the category gate (`instance.py:471-478`); the category alone cannot spell "ari only".

**The five exact-set pins move in the same commit** (one inserted literal line each — there is no `len()` assertion anywhere):

| Pin | Line |
|---|---|
| `tests/unit/tools/test_upgrade_registration.py` | `:142-166` |
| `tests/unit/tools/test_attestation_registration.py` | `:186-208` |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py` | `:324-341` |
| `tests/integration/test_service_tool_flag_off_byte_identical.py` | `:823-858` |
| `tests/unit/tools/test_secrets_registration.py` | `:218-232` |

The prose claims "exactly eight entries" in the surrounding test docstrings are **not** assertions but go stale on this pass — update
them to **ten** in the same commit.

**Registering test must prove the property directly:** `resolve_tool_filter` yields **no** `self_alarm` for a non-ari agent with an
**EMPTY allow list** — not merely that only one `meta.json` names the category. The ari allow-list assertion stays as a second,
cheaper check.

**Privileged-set trajectory (cross-phase coupling):** **8** → **9** (P1: `board`) → **10** (P2: `self-alarm`) → **12** (P3:
`view-write`, `board-link`) ⇒ **three pin passes over the same five files**, not one. Each pass is one line per file and each commit
is self-consistent, so there is no red window — but no phase may claim the pins were "already paid".

**D11 — `project_id` is auto-injected, never optional, and resolved BEFORE anything else.** `project_id` is the **only** selector
for the fire route: without it the fire takes `_execute_immediate` instead of the job-queue path (`scheduler.py:995-998`) ⇒
delivered, but with **no JobItem, no idempotency key, no retry, no visibility**. The wrapper resolves the caller's project from the
instance row (`manager._instance_repository.get(current_instance_id).project_id` — the `blueprint`/`chart_tools` auto-inject pattern,
`daemon/tools/blueprint.py:61-66`) and passes it explicitly.

**Ordering is binding (A-25):** resolve `project_id` → error on failure → **only then** look up the label / create / reuse. A
missing project must leave **no row and no burned name**. Do **not** swallow the exception the way `blueprint.py:67-68` does
(`except Exception: pass`): catch it, log it, and return a typed `ERROR: cannot resolve project_id for instance …` — a degraded
alarm must be refused, not armed.

**D12 — Board id validated, not parsed.** `link_board_item` must match `^wi-[0-9a-f]{8}$` (the pinned phase-1 format); a mismatch is
a typed error. The id is embedded **verbatim** (no title/status lookup) ⇒ zero runtime dependency on the board service.

**D13 — Prompt surface, DEFERRED to the conventions step.** The new `## Self-Alarm` section in `agents/ari/tools_note.md`
(agent-POV, first person, no daemon internals — the forbidden-token list in
`tests/unit/test_scheduling_registration.py:44-58` is the pattern to mirror) is authored in the **conventions step**
([plan-overview.md §8](plan-overview.md) — step 4 of this commission, before merge + stage), **not** in phase 2. Reason:
`tools_note.md` **IS** manifest-covered (161-entry committed manifest `agents/.prompt-artifacts.sha256`, row `:28`) — it
is **not** in the `SEALED_SHA_BASELINES` tripwire, but it **is** in the committed manifest, and the manifest is what the
fail-closed file-tool write guards protect. Editing it in phase 2 would create a **second** re-stamp pass; the **C1①
consolidation ruling** defers every prompt-artefact edit to one place so the commission produces exactly **one**
in-commit re-stamp pass. ⇒ **Phase 2 ships no prompt-artefact edit and carries no re-stamp task.** Content spec of that section
(the five **Tool-doc obligations** above — lateness both-ways, board-is-the-surface / never-a-ping, unobservable fire, list
semantics, `self-alarm:` namespace — plus the D8 `due_at` convention, the ≥30 min vs <30 min decision, and the board's
role as the user-visible surface) is written out in [plan-overview.md §8.4](plan-overview.md). The section is a second
home for the grammar only by reference — the tool doc stays canonical. **Write lane (binding):** the file tools
**HARD-REFUSE** writes to a versioned prompt artefact (`VERSIONED_PROMPT_ARTIFACTS`,
`daemon/tools/prompt_artifacts.py:119-127`; fail-closed guards in `daemon/tools/filesystem.py` — `write_file` **and**
`edit_file`) — the sanctioned lane is the **bash/git escape hatch** with a **same-commit manifest re-stamp**; the
**prompt-versions API is a runtime DB lane**, not the repo-commit path, and does not satisfy the manifest seal.

---

## Task checklist

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Re-verify the 3 engine invariants in THIS checkout and cite them: (a) one-shot completion ⇒ `status='stopped'` + `enabled=False` (`registry.py:515-526`); (b) cancelled label is permanently burned (`repository.py:212-216`, `scheduling_service.py:923-926`); (c) `project_id` gates the job-queue route (`scheduler.py:995`) | — | All three cited with file:line in the implementation PR description; any deviation re-planned before coding |
| 2 | Create `daemon/services/self_alarm_service.py` — pure helpers: `parse_when_or_every`, `resolve_fire_plan` (tz + cron/anchor), `enforce_min_horizon`, `compose_alarm_message`, `validate_board_id` | 1 | Pure module: no tool imports, no engine calls, no DB; 100% unit-testable |
| 3 | Create `daemon/tools/self_alarm.py` — closure factory `create_self_alarm_tools(service, *, source_repo, current_instance_id, agent_id, agent_tag)`, `SelfAlarmInput` BaseModel, `@register_tool_category("self-alarm")` **above** `@tool(args_schema=…)`, `self_alarm._full_doc_` carrying the five **Tool-doc obligations** | 2 | Tool returns a dict on success and `"ERROR: …"` on every failure; never raises; `_full_doc_` doc-pin test green |
| 4 | Register **six surfaces**: `PRIVILEGED_TOOL_CATEGORIES` + `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` + `CATEGORY_MODULES["self-alarm"] = "daemon.tools.self_alarm"` in `daemon/tools/_tool_registry.py`; the **five exact-set pins** (one line each); `create_self_alarm_tools_if_available(manager, …)` + `tools.extend(…)` in `daemon/tools/instance.py` **before** `create_help_tool`/`scan_tools_for_full_docs`/`_apply_tool_filter` (next to the scheduling extend, `:5773-5775`) | 3 | Tool visible in a real instance tool list; all five pins exact-set green; a decorator-only variant is proven insufficient (comment mirrors `:2286-2289`); `CATEGORY_MODULES` value is a `daemon.tools.*` path |
| 5 | Dedupe/reuse implementation (D4) — the **strictly ordered** sequence **verify-compatibility → floor → update+assert → fallback+neutralization**: `self-alarm:` namespace → `project_id` resolve-first (D11) → **step 1** label lookup (read-only) → **step 2** compatibility gate (family + weekly-DOW + cron-expression; a mismatch ⇒ fresh-label create with **no `update_schedule` call**) → **step 3** floor on the wrapper-computed first fire (**both** branches, before any engine call) → **step 4a** create (`agent="ari"`, `timezone=<resolved IANA name>`, candidate collision-check + post-create duplicate report, never cancel the loser) / **step 4b** reuse-via-`update_schedule({when?, message, timezone: <resolved IANA name>, paused=False})` + the response assertion (`paused is False`, `status != 'stopped'`, `next_run_at_utc` == the **wrapper-computed first fire** ±1 s) + `stopped`-label strictly-later re-arm (message-only REFUSED) → **step 5** fallback create → **step 6** **neutralize the mutated original by pausing it** (`update_schedule(source_id, {paused: True})`, asserted) → **step 7** report (echo the armed label; disclose the neutralized original / the untouched pre-existing row) | 3, 4 | Each branch covered by a unit test; **a forced assertion failure leaves exactly ONE armed row** (the fresh label) and the mutated original **paused**; a compatibility mismatch and a below-floor reuse issue **zero** `update_schedule` calls; the reuse payload always carries `timezone=<resolved IANA name>`; the effective label is always echoed |
| 6 | **Three-layer** ARI-only: privileged category + runtime gate (`agent_id != "ari"` ⇒ error) + `"self-alarm"` added to **`agents/ari/meta.json`** only | 3 | `grep -rl "self-alarm" agents/*/meta.json` → exactly one file; an **empty-allow** non-ari agent resolves **no** `self_alarm` (`resolve_tool_filter`) |
| 7 | **Prompt surface — DEFERRED to the conventions step** ([plan-overview.md §8](plan-overview.md), step 4). Phase 2 ships **only** the tool + `_full_doc_` + the error hints; the `## Self-Alarm` section of `agents/ari/tools_note.md` is authored in step 4, where its manifest row is re-stamped **in the same commit**. | 3 | Phase 2 edits **no** prompt artefact and carries **no** re-stamp task (`tools_note.md` **IS** manifest-covered — the 161-entry committed manifest `agents/.prompt-artifacts.sha256`, row `:28`; the C1① consolidation ruling forbids a second re-stamp pass). The **prompt-content assertions** that used to live here move to the conventions step's checklist (guide forbidden-token + cross-ref closure). |
| 8 | Tests: `tests/unit/test_self_alarm_service.py` (grammar, floor boundary, tz parity, message composition, board-id regex) | 2 | Boundary: 29 m ⇒ reject, 30 m ⇒ accept, 30 m + 1 s ⇒ accept — **on both branches** (the floor is branch-independent: create **and** reuse); **family-aware rejection text** with the computed next fire (one-shot vs recurring); the ≤ 30-min-period cron is rejected by the floor (`*/30 * * * *` ⇒ its first fire is always < 30 min away) while the documented example `cron 0 9 * * *` is accepted; parity vs `_resolve_schedule_timezone` matrix |
| 9 | Tests: `tests/unit/tools/test_self_alarm_tools.py` (mocked service; `await tool.coroutine(...)`) | 3, 5 | Covers create / reuse (`paused=False` + response assertion) / **reuse payload shape: `timezone` == the resolved IANA name; no `when` key for a stored `cron` row** / **step-2 compatibility gate: a family mismatch and a weekly-DOW mismatch ⇒ zero `update_schedule` calls + a fresh-label create; the stored row is untouched** / **step-3 reuse-path floor: 29 m ⇒ `ERROR` + zero `update_schedule` calls; 30 m and 30 m + 1 s ⇒ accepted** / **steps 5-6 fallback: a forced assertion failure ⇒ a fresh-label create AND the mutated original paused (`{paused: True}` issued, `paused is True` asserted) ⇒ exactly one armed row** / **a failed pause ⇒ typed `ERROR` naming both rows, never a success claim** / stopped-label strictly-later re-arm / **message-only re-arm refusal** / burned-label `-2` collision-check + duplicate report / the mismatch fallback's `fallback_reason` + the disclosure of the untouched pre-existing row / non-ari gate / non-ari **empty-allow** gate / `project_id`-resolve-first (no row, no label burn) / family-aware floor text with computed next fire / `lateness_cap_seconds` + `tz_warning` + actual `status` echo / engine-error paths |
| 10 | Tests: `tests/unit/test_self_alarm_registration.py` (mirror `tests/unit/test_scheduling_registration.py`): allow ⊆ registry universe, category resolution, name in `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES`, **`"self-alarm"` in `PRIVILEGED_TOOL_CATEGORIES`**, **ari-only** allow, the five tool-doc obligation pins | 4, 6, 7 | Green; asserts NO other agent grants the category; `resolve_tool_filter` for an empty-allow non-ari agent yields no `self_alarm`. The **tools_note forbidden-token + cross-ref assertions** are NOT in this phase — the section is authored in the conventions step, so they move to that step's checklist ([plan-overview.md §8.6](plan-overview.md)) |
| 11 | Regression run: the new tests + `tests/unit/test_scheduling_registration.py` + `tests/unit/tools/test_frozen_tool_name_discovery.py` + `tests/unit/tools/test_tool_config_validation_boot.py` + **the five privileged-set pin files** + the seal test | 8-10 | Zero NEW failures; all five exact-set pins green with **ten** entries; the seal-test mismatch count **before == after** (the measured baseline at this HEAD is **7** — the tripwire is **already red** on 7 pre-existing stale pins, which are **NOT ours to fix**; the acceptance is "no NEW stale pins beyond the baseline", NOT "green") — phase 2 edits **no** prompt artefact |
| 12 | Record the deviations/notes into the phase-2 decisions feed (`decisions.md` in this planning dir) | 1-11 | Each of D1-D13 has an anchor; deviations from pre-rulings flagged explicitly |

---

## File map

| File | Action | Notes |
|---|---|---|
| `daemon/services/self_alarm_service.py` | **new** | Pure logic: grammar, fire plan, floor, message, board-id regex |
| `daemon/tools/self_alarm.py` | **new** | Factory + `self_alarm` tool + `_full_doc_` + `CATEGORY_NAME`/`CATEGORY_DOC` |
| `daemon/tools/_tool_registry.py` | modify | **4 edits**: `PRIVILEGED_TOOL_CATEGORIES` (+`"self-alarm"` → entry #10), `DYNAMIC_TOOL_NAMES`, `KNOWN_TOOL_NAMES`, `CATEGORY_MODULES["self-alarm"] = "daemon.tools.self_alarm"` (a `daemon.tools.*` path is mandatory) |
| `daemon/tools/instance.py` | modify | `create_self_alarm_tools_if_available` + one `tools.extend(...)` **before** `create_help_tool`/`scan_tools_for_full_docs`/`_apply_tool_filter` |
| `tests/unit/tools/test_upgrade_registration.py` · `tests/unit/tools/test_attestation_registration.py` · `tests/integration/test_maintenancer_spawn_resolves_tools.py` · `tests/integration/test_service_tool_flag_off_byte_identical.py` · `tests/unit/tools/test_secrets_registration.py` | modify | **the five exact-set pins** — one inserted literal line each (`"self-alarm"`, **ten** entries); update the stale "exactly eight entries" prose in the test docstrings |
| `agents/ari/meta.json` | modify | add `"self-alarm"` to `tools.allow` — **only** allow-list touched |
| `agents/ari/tools_note.md` | **unchanged in phase 2** | the `## Self-Alarm` section is authored in the **conventions step** ([plan-overview.md §8](plan-overview.md), step 4) — `tools_note.md` **IS** manifest-covered (committed manifest row `:28`), so phase 2 carries **no** re-stamp task |
| `tests/unit/test_self_alarm_service.py` | **new** | Pure-logic unit tests |
| `tests/unit/tools/test_self_alarm_tools.py` | **new** | Tool-level unit tests (mocked service) |
| `tests/unit/test_self_alarm_registration.py` | **new** | Registration + allow-list + prompt-closure pins |
| `daemon/services/scheduling_service.py`, `daemon/sources/adapters/scheduler.py` | **unchanged** | The engine is reused verbatim |
| `agents/ari/rule.md`, `agents/ari/soul.md`, `agents/ari/workflow.md`, `agents/leader/*` | **unchanged** | All are **manifest-covered** (committed manifest `agents/.prompt-artifacts.sha256`); `agents/ari/{rule,workflow}.md` + `agents/leader/rule.md` are **also** in the `SEALED_SHA_BASELINES` tripwire. Every prompt-artefact edit is deferred to the conventions step |

---

## Tests (mirroring the existing scheduling / shared_meta_kv files)

- Pattern sources: `tests/unit/tools/test_scheduling_tools.py` (mocked service, `await tool.coroutine(...)`,
  `_full_doc_` pins), `tests/unit/test_scheduling_registration.py` (allow ⊆ registry, category resolution,
  forbidden-token + cross-ref prompt closure), `tests/unit/tools/test_frozen_tool_name_discovery.py`
  (frozen-universe membership).
- **Floor**: 29 min ⇒ `ERROR` + the **family-appropriate** hint + **no** engine call; 30 min ⇒ accept; 30 min + 1 s ⇒ accept;
  past-due absolute ⇒ reject; 90 s relative ⇒ reject; a recurring `daily 06:30` armed 15 min before the fire ⇒ the recurring-family
  text naming the **computed next fire** (never the bash-sleep hint). **The same boundary on the REUSE path** (D4 step 3 — the
  floor is **branch-independent**): 29 min ⇒ `ERROR` + **zero `update_schedule` calls** (the stored row is **not** mutated);
  30 min ⇒ accepted; 30 min + 1 s ⇒ accepted. A raw cron with a ≤ 30-min period (`*/30 * * * *`) ⇒ rejected (its first fire is
  always < 30 min away); the documented example `cron 0 9 * * *` ⇒ accepted.
- **Grammar**: relative (`45m`, `in 2h`, `1d30m`), absolute aware/naive, `daily 06:30`, `weekly 1 06:30`,
  `cron 0 9 * * *`, `every 1h` ⇒ rejected with the raw-cron hint, garbage ⇒ typed error listing the forms; a label already
  prefixed `self-alarm:` ⇒ typed "prefix is added automatically" error; a **116-char** label ⇒ typed length error (115 is the max).
  The accepted cron example is the **armable** `cron 0 9 * * *` (24-h period) — `*/30 * * * *` is **not** offered as an example,
  because under the floor its first fire is always < 30 min away.
- **Recurring parity**: the wrapper's first fire == the engine's `next_run_at_utc` echo (≤1 s) for `daily`,
  `weekly`, `cron` — a tz/first-fire parity test pins the wrapper against the engine.
- **Tz parity**: the wrapper's resolved zone == `scheduling_service._resolve_schedule_timezone` for the matrix
  {explicit tz, user setting set/unset, env default set/unset} — guards against chain drift; the echoed `tz_warning`
  is the **wrapper's own** (the engine's chain is short-circuited by an explicit zone).
- **Dedupe / re-arm**: no row ⇒ create; `running`/`starting` ⇒ `update_schedule(..., paused=False)` (no create) with the response
  asserted (`paused is False`, status not `stopped`, `next_run_at_utc` == the **wrapper-computed first fire** ±1 s — the requested
  instant for `once`, the computed **next occurrence** for `daily`/`weekly`/`cron`); `stopped` ⇒ re-arm via update
  **with `paused=False` + the same assertion**, and a **message-only re-arm is REFUSED** (typed error: a fired alarm needs a
  new, later time); `cancelled` ⇒ create with `"<effective>-2"` (**candidate collision-checked**) and the effective label echoed;
  two rows sharing a name ⇒ the duplicate is **reported**, never cancelled.
- **Compatibility gate (D4 step 2 — before any mutation)**: a stored `once` row + a `daily` request, a stored `daily` row + a
  `once` request, a stored `weekly` row + a **different weekday**, and a stored `cron` row + a **different expression** ⇒
  **zero `update_schedule` calls** (the stored row is **not** mutated) + a fresh-label create carrying `fallback_reason`; the
  stored row's **DOW is derived from the stored cron's 5th field** (the engine stores no `weekday` key —
  `scheduling_service.py:627`), not from a `weekday` field.
- **Fallback + neutralization (D4 steps 5-6)**: a forced assertion failure (the mocked update returns a wrong `next_run_at_utc`)
  ⇒ a fresh-label create proceeds **and** the mutated original is **paused** (`update_schedule(source_id, {paused: True})` issued
  and asserted: `paused is True`, `status == 'stopped'`) ⇒ **exactly one armed row** survives (the fresh label) ⇒ "fires exactly
  once" holds; the response discloses the neutralized original's `source_id`. A **failed pause** ⇒ typed `ERROR` naming **both**
  rows — never a success claim.
- **Gate**: a non-`ari` caller ⇒ `ERROR` and no engine call; an **empty-allow** non-ari agent (watcher-shaped) ⇒
  `resolve_tool_filter` yields no `self_alarm` (privileged-category proof).
- **Ordering / echo**: unresolvable `project_id` ⇒ `ERROR` **before** any label lookup (assert zero repository writes);
  `lateness_cap_seconds` echoed as `None` and as an integer; `status` echoed as the actual row status with `'starting'`
  treated as armed.
- **Doc obligations**: `_full_doc_` and the tools_note section contain all five Tool-doc obligations (both cap states +
  the skip signature, board-promise/never-a-ping, unobservable fire, list semantics, `self-alarm:` namespace).
- **Message**: contains `"[self-alarm]"`, the effective label, the scheduled instant (local **and** UTC), the board id + the
  `board_get` instruction, the fresh-instance note, and the agent's action text verbatim.
- **Engine errors**: `create_schedule`/`update_schedule` raising ⇒ `"ERROR: …"` (never raises).
- **Safety**: hermetic — mocked service + mocked repo only; no DB, no daemon boot, no `$HOME`-derived paths
  (critical note 2026-10-12: test scripts must never resolve live-shaped paths).

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **Alarm never fires after a long outage.** Default `one_shot_max_lateness_seconds=None` ⇒ unlimited lateness ⇒ a ≥30-min alarm whose instant passed while the daemon was down **fires LATE at the next boot** (adapter rebuilt from config; the past-due one-shot returns `now` as its next trigger → immediate dispatch). If the operator sets `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS=N`, a boot later than N ⇒ a `skipped` history row, **no dispatch**, and the schedule **stays armed** (re-checks every 5 s; once-per-`run_at` guard ⇒ it never fires until someone updates `run_at`). | High (the reminder is silently lost / arrives very late) | Medium | Document **both cap states and the skip signature** in the tool's `_full_doc_` + the tools_note section; echo `lateness_cap_seconds` in every response so the agent sees its exposure; the composed message states the **scheduled** instant so a late delivery is detectable; recommend a post-outage board re-check. **Phase 2 does not change engine semantics** (out of scope; OQ-4 RESOLVED — no cap in v1) |
| 2 | Burned label after cancel ⇒ the agent cannot re-use its own name | Medium | Medium | Auto-retry with `"<effective>-2"` (D4) + the typed error explaining the terminal namespace |
| 3 | Alarm armed with the wrong target instance (the human expects a chat ping) | Medium | Medium | Stated explicitly in the tool doc + tools_note (the scheduler source is one-way; `scheduler.py:435-457`); OQ-1 RESOLVED — no chat bridge in v1, the board is the sole promised surface |
| 4 | The floor is checked on the **scheduled** instant, not the **delivery** instant ⇒ a very-late delivery can look "on time" | Low | Medium | The message states the scheduled instant; the fresh-instance note tells the actor to verify state if late |
| 5 | Wrapper tz resolution drifts from the engine chain ⇒ the floor is computed against the wrong instant | Medium | Low | Parity test vs `_resolve_schedule_timezone` (D3/tests); the wrapper reuses the *public* helpers only |
| 6 | `self-alarm` leaks to another agent because an allow/deny-empty agent gets everything | High (policy) | Low | **Three layers**: privileged category (default-deny strip + empty-allow branch) + runtime ari-only gate (D10) + the ari-only allow entry; the registration test proves an empty-allow non-ari agent resolves **no** `self_alarm` |
| 7 | Phase-1 board id format changes ⇒ the link instruction breaks | Low | Low | The wrapper embeds the id verbatim + validates the shape; a phase-3 conformance check covers the drift |
| 8 | Re-arm under the one-shot idempotency key collapses into a still-non-terminal prior job ⇒ no second delivery. Under the ≥30-min floor an **identical-instant** re-arm is impossible (the stored instant is past); the reachable hole is the **message-only** edit, which keeps the stored `run_at` | Low | Medium | A `stopped` label is re-armed **only with a strictly later instant**; a message-only re-arm is REFUSED with a typed error (D4; `scheduler.py:1090-1094`, `job_queue_service.py:1424-1481`) |
| 9 | Update-path rebuild gap (<1 s) loses a re-armed fire | Low | Low | Every armed instant is ≥30 min out (D9) ⇒ the window is irrelevant |
| 10 | New category breaks the frozen-binary tool universe (the tool vanishes in prod) | High | Low | `self_alarm` added to **both** `DYNAMIC_TOOL_NAMES` and `KNOWN_TOOL_NAMES` (the `secrets` precedent, `_tool_registry.py:1110-1120`); frozen-discovery test pinned |
| 11 | Prompt-surface drift (the tools_note section is a second home for the grammar) | Low | Medium | One canonical home (the tools_note section) + the tool doc references it; the forbidden-token/cross-ref tests enforce the closure |
| 12 (= **R33**) | **`stopped`-label reuse without `paused=False` silently disarms the alarm** — the update keeps `enabled=False`, the rebuild is skipped, the row never fires (architect §4.3 D-1) | 🔴 High | Medium | Pass `paused=False`; **assert** `paused is False` / `status != 'stopped'` / `next_run_at_utc` in the response; fall back to a fresh label if the resume did not take |
| 13 (= **R34**) | **A message-only re-arm of a fired label is swallowed** by `scheduler:{source_id}:{run_at}` (non-terminal prior job within 24 h), so the alarm silently never delivers (architect §4.3 D-2) | 🔴 High | Medium | Refuse a re-arm that is not strictly later; state it in the tool doc |
| 14 (= **R35**) | **A non-privileged `self-alarm` lets `watcher` (and every `tools`-less agent) arm alarms** ⇒ a fresh Ari instance executes attacker-influenced text verbatim (architect §1.4) | 🔴 High | Low | Privileged category (entry #10) + runtime ari gate + the five exact-set pins |
| 15 (= **R43**) | **The default schedule list hides armed alarms** — not status-filtered, repository page capped at 100 rows `created_at DESC`; `stopped` means *dispatched*, not *acted upon* (architect §4.4 P2-2/P2-3) | 🟡 Medium | High | Tool doc: use `status='running'` to list armed alarms; state that stopped rows push armed ones out of the default page |
| 16 (= **R44**) | **Label namespace is shared with non-scheduler sources** — `get_source_config_by_name` has no `source_type` filter, so an unprefixed label can collide with an MCP/Slack source name invisible in the list (architect §3.2 d) | 🟡 Medium | Medium | `self-alarm:` prefix on every wrapper-minted label (D4) |
| 17 (= **R46**) | **`tz_warning` erasure** — passing an explicit zone short-circuits the engine's tz chain, so the engine's UTC-fallback warning is suppressed (architect §4.4 P2-4) | 🟡 Low | Medium | Echo the **wrapper's own** warning in `tz_warning` |
| 18 (= **R47**) | **Duplicate-armed alarm on the reuse path** — `update_schedule` **mutates the stored row** (`when`/`message`/`timezone`/`enabled=True`) and **drains + rebuilds/starts the adapter before it returns** (`scheduling_service.py:1011`, `:1026-1034`, `:1046-1047`); if the ±1 s assertion *then* fails and the fallback mints a fresh label, the ALREADY-MUTATED original stays **armed** (wrong weekday / stale tz) ⇒ **two** alarms fire for one request ⇒ violates the plan's own "fires exactly once" goal (Objective, `phase2-plan.md:18`) | 🔴 High | Low | **CLOSED by this amendment** — D4 is now a strictly ordered sequence (**verify-compatibility → floor → update+assert → fallback+neutralization**): (b) the step-2 compatibility gate rejects a family / weekly-DOW / cron-expression mismatch **before any mutation** (fresh label, **zero** engine writes) ⇒ a mutated-original state cannot arise there; (a) the reuse payload always carries `timezone=<resolved IANA name>` ⇒ the stored zone cannot go stale on reuse; (c) the fallback path **MUST** neutralize the mutated original by **pausing** it (`update_schedule(source_id, {paused: True})` — reversible, does **not** burn the label, needs no `when`, so the "cannot change the recurrence family" gap does not bite) ⇒ exactly **one** armed row survives; the pause is asserted and a failed pause is reported as a typed error naming both rows |
| 19 (= **R48**) | **A cron example that is structurally unarmable** — a raw cron with a ≤ 30-min period (e.g. `*/30 * * * *`) has a first fire **always** < 30 min away (the longest possible wait is one period) ⇒ the D9 floor rejects it every time ⇒ the documented example would have been unusable; the `every 1h` hint also pointed at the non-portable `*/60` step (60 is outside the 0-59 minute field range) | 🟡 Medium | — | **CLOSED** — the grammar table now documents `cron 0 9 * * *` (24-h period) as the **armable** cron example and states the period rule explicitly; `*/30 * * * *` is removed from the grammar table, the test list and the D3 rejection hint (now `cron 0 */2 * * *`) |
| 20 (= **R49**) | **A family/DOW mismatch now creates a second row instead of erroring** — the approver-mandated fresh-label route (D4 step 2) leaves the pre-existing row **untouched and still armed with its own schedule** ⇒ two reminders can coexist under one agent label | 🟡 Medium | Low | The response **discloses** the untouched pre-existing row (label + `source_id` + state) beside the newly created one and carries `fallback_reason`; no schedule is destroyed (the original is never mutated, never cancelled). Recorded for approver adjudication |

---

## Success criteria

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 1 | `self_alarm` is registered and reachable | The registration test + a real instance tool list | Category resolves; name in `DYNAMIC_TOOL_NAMES` **and** `KNOWN_TOOL_NAMES`; 1 tool in the category |
| 2 | ARI-only | `grep -rl "self-alarm" agents/*/meta.json` + a non-ari call + the empty-allow resolution test | Exactly **1** meta.json; a non-ari call returns an `ERROR` and creates no row; an empty-allow non-ari agent resolves **no** `self_alarm` |
| 3 | The 30-minute floor holds | Unit tests at 29 m / 30 m / 30 m+1 s | 29 m ⇒ `ERROR` (bash-sleep hint present) + **no** engine call; 30 m ⇒ accepted |
| 4 | The armed instant is exactly the floored instant | Wrapper first-fire vs the engine's `next_run_at_utc` echo | ≤1 s drift for once/daily/weekly/cron |
| 5 | A fired one-shot leaves a `stopped` row (not cancelled) and the label is not silently reusable | Engine-status check (task 1) + the dedupe tests | `status='stopped'`, `enabled=False`; a later same-label request **re-arms the same row** (no `-2`); only a `cancelled` label takes the `-2` path |
| 6 | The reminder is actionable by a fresh instance | The message-composition test + a manual read of a real composed message | Contains prefix, label, scheduled local+UTC instant, board id + `board_get` instruction, fresh-instance note, action text |
| 7 | No collateral damage | The phase-2 regression run | Zero new failures; the stale-seal count unchanged (7); no `agents/ari/rule.md` / `workflow.md` diff |
| 8 | The phase-1 board contract is respected | The board-id regex test | `wi-<8 hex>` accepted; anything else ⇒ typed error; no phase-1 module imported |
| 9 | `self-alarm` is **privileged** | `PRIVILEGED_TOOL_CATEGORIES` membership + all five exact-set pins | Present in all five pins (**ten** entries); an empty-allow non-ari agent resolves no `self_alarm` |
| 10 | A fired label re-arms **armed** | re-arm unit tests | `paused=False` passed and asserted; a message-only re-arm is refused |
| 11 | Lateness exposure is visible | response field + doc pins | `lateness_cap_seconds` echoed; both cap states + the skip signature documented |
| 12 | The user-visible surface is named | doc pins | The board is promised; a chat ping is explicitly **not** promised |
| 13 | **The reuse path cannot leave a duplicate armed** | the step-2 / step-3 / fallback tests + engine-call counters | A compatibility mismatch and a below-floor reuse each issue **zero** `update_schedule` calls (the stored row is untouched); a forced assertion failure leaves **exactly one armed row** (the fresh label) with the mutated original **paused** (`paused is True` in the pause response); the reuse payload always carries `timezone=<resolved IANA name>` |

---

## Resolved questions (architect adjudication §6 — all binding)

1. **Chat bridge for the alarm's reply.** → **RESOLVED: no new infrastructure in v1.** The scheduler source is one-way
   (`scheduler.py:435-457`) and the receiving instance is scheduler-owned with no chat identity; **the board is the
   user-visible surface** and the contract promises the board, never a ping (D6, tool-doc obligation #2). Phase 3 does
   not bridge.
2. **Recurring arm-now-imminent.** → **RESOLVED: confirm reject**, with a **family-aware** error text that always prints
   the computed next fire (D9).
3. **Interval recurrence.** → **DEFERRED-to-v2.** Keep rejecting `every 1h` with the raw-cron hint (`cron 0 */2 * * *`);
   a real engine change is a separate commission. (The hint was re-pointed off `cron */60 * * * *`: `*/60` is a
   non-portable step — 60 is outside the 0-59 minute field range — and a 60-minute period lands inside the ≥30-min
   first-fire floor window about half the time. The documented cron example is `cron 0 9 * * *` — a 24-h period,
   armable.)
4. **Lateness-cap policy.** → **RESOLVED: default `None`, no cap in v1.** Document both cap states **and** the skip-loop
   signature, and echo `lateness_cap_seconds` in the response (Tool-doc obligation #1). The deployment question is an
   operator decision, recorded as Risk 1.
5. **Auto-arm v2 (the deferred board glue).** → **RESOLVED: deferral confirmed** (the five blockers stand); v1 is the
   prompt convention (D8).
6. **Alarm scope semantics.** → **RESOLVED: ari-for-ari only.** "Arm alarms for another agent" is a different tool, out
   of scope.

---

## Amendment log (post-architect `2b297db7d`)

Architect adjudication: `.agents/shared/planning/ari-tools/architecture-recommendation.md` (§1.4, §3.1, §3.2, §4.1–§4.5, §9).
Leader accepted all verdicts as binding; the amendments below are folded into the body above so the plan reads as-if-designed-so.

| ID | Pri | Folded into |
|---|---|---|
| **A-18** | P0 | Scope line 23, D10 (privileged category + five exact-set pins + empty-allow test + 8→9→10→12 trajectory), file map, tests, risks, success criteria |
| **A-19** | P0 | D4 steps **4b/5** (numbered 3/4 before the iteration-002 renumber) — `paused=False` on reuse, response assertion, fresh-label fallback, message-only re-arm REFUSED |
| **A-20** | P0 | D4 steps **4a/5** (numbered 5 before the iteration-002 renumber) — `-2` candidate collision-check, post-create duplicate report, never cancel the loser |
| **A-21** | P0 | D5 — default list not status-filtered, 100-row cap, `status='running'` = armed, `stopped` = dispatched |
| **A-22** | P0 | D9 — family-aware rejection text with the computed next fire |
| **A-23** | P0 | Tool-doc obligation #1 + success return (`lateness_cap_seconds`), engine facts, risks 1/17 |
| **A-24** | P0 | Tool-doc obligation #2, D6 board-surface contract, D13 content spec |
| **A-25** | P0 | D11 — `project_id` resolve-first, error before label lookup, do not swallow |
| **A-26** | P1 | Success return notes — wrapper's own `tz_warning`, actual `status` (`'starting'` = armed) |
| **A-27** | P1 | D4 namespace rule — `self-alarm:` prefix on all wrapper labels, collisions invisible in the list |
| **A-28** | P1 | D2 registry contract + task 4 + file map — `CATEGORY_MODULES` value MUST be a `daemon.tools.*` path; six surfaces |
| **A-29** | P1 | Risks 12–17 (= R33, R34, R35, R43, R44, R46) |

---

## Amendment log (iteration 002 — approver blocking fixes, 2026-10-20)

Approver tracking: `/home/nea/ensemble-src/.agents/approver/ari-tools-tracking.md` — Iteration 001 (**REJECTED**, 2 blocking / 10 notes).
The body above already reads as-if-designed-so; this table records where each iteration-002 amendment was folded in.

| ID | Source | Folded into |
|---|---|---|
| **I2-BLOCK2** | Approver blocking #2 — D4 reuse path double-arm | D4 rewritten as a **strictly ordered sequence — verify-compatibility → floor → update+assert → fallback+neutralization** (steps 1-7): step-2 compatibility gate (family / weekly-DOW / cron-expression ⇒ fresh label, **the original row is never mutated**), step-3 floor on **both** branches, step-4b reuse payload with the **mandatory `timezone=<resolved IANA name>`**, step-5 fallback create, step-6 **neutralization of the mutated original by pause** (with its own justification + test), step-7 report/disclosure; the engine-facts rows (update-payload shape, tz-refresh-only-if-set, weekly-DOW preservation, mutate-before-answer); D9; task 5 / 8 / 9; Tests bullets; Risks 18-20; Success criterion 13 |
| **I2-B1** | Approver note B1 — engine facts / payload shape | Engine-facts table: `create_schedule` **requires** `payload.agent` (`ScheduleCreatePayload.agent`, `scheduling_service.py:132`; fail-fast on an unknown agent, `:673-676`) ⇒ the wrapper sets **`agent="ari"`** on every create (the alarm fires *into* ari); D4 step 4a (the create payload) |
| **I2-B2** | Approver note B2 — floor re-enforcement on the reuse path | D4 step 3 + D9 (the floor runs on the wrapper-computed first fire for **both** the create and the reuse branch, **before** any engine call, so a below-floor reuse never mutates the stored row); task 8 boundary test; Tests bullet (reuse path 29 m / 30 m / 30 m + 1 s) |
| **I2-B8** | Approver note B8 (merged into blocking #2(a)) | Engine-facts row (`update_schedule` refreshes the stored zone **only** when `payload.timezone` is set, `:931-936`) + D4 step 4b (the reuse payload **must** carry `timezone=<resolved IANA name>` ⇒ the stored zone cannot drift stale) |
| **I2-BLOCK2b** | Approver blocking #2 (b) — the family/DOW mismatch rule | D4 step 2 — a family / weekly-DOW / cron-expression mismatch routes **directly to a fresh-label create without calling `update_schedule` at all**; the per-family diagnostic survives as `fallback_reason` (superseding the former "typed per-family error" rule, which would have left the agent unable to arm); D4 step 7 (the untouched pre-existing row is disclosed); Risk 20 |
| **I2-CR** | Approver note — the cron example is unarmable | Grammar table (the cron family documents the **armable** `cron 0 9 * * *` + the period rule: a ≤ 30-min period is structurally unarmable); D3 `every 1h` hint (`cron 0 */2 * * *` — the non-portable `*/60` step is gone); D9; Tests bullet; Resolved question 3; Risk 19 |
