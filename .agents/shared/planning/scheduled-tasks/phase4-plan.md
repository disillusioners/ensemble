# Phase 4: Tool Registration & Documentation

## Objective

Land the `scheduling` tool category on the three target agents (Ari MANDATORY, Leader + Jober desirable) and update their per-agent `tools_note.md` plus the `docs/` user/operator reference so external systems and other agents can discover and reason about the new surface. Registration correctness is proven by tests (D9 — NO daemon restart in this task; correctness proven statically + at next registry exposure).

Specifically:

- `agents/ari/meta.json` — add `"scheduling"` to `tools.allow` (MANDATORY; D7)
- `agents/leader/meta.json` — add `"scheduling"` to `tools.allow` (desirable; D7)
- `agents/jober/meta.json` — add `"scheduling"` to `tools.allow` (desirable; D7)
- `agents/ari/tools_note.md` — add a `## Scheduling` section (one canonical home, agent POV, ZERO system internals per docs/agent-prompt-writing-guide.md §1)
- `agents/leader/tools_note.md` — add a `## Scheduling` section (canonical home — Leader's allow-list is narrow, keep the section small)
- `agents/jober/tools_note.md` — add a `## Scheduling` section (canonical home — Jober is a Job Orchestrator; scheduling is a peer capability to `schedule` a job)
- `docs/` — inventory current scheduling/sources/API docs, then update with a Scheduling user/operator reference + cross-link from existing API reference

> **Note (non-goal, mandatory):** NO changes to `daemon/tools/scheduling.py` (phase-2), NO changes to `daemon/tools/_tool_registry.py` (phase-2 wires the category), NO daemon restart. D9 requires registration correctness to be proven by tests; this phase authors those tests.

## Coupling

- **Depends on**: phase-2 (the `scheduling` tool category must be registered in `daemon/tools/_tool_registry.py:CATEGORY_MODULES` AND `KNOWN_TOOL_NAMES` must be regenerated, OR — if phase-2 uses the AST-discovery path `_tool_registry.py:299-396` — the four tool names `task_schedule` / `task_schedule_list` / `task_schedule_cancel` / `task_schedule_update` must resolve via `_discover_tool_names()`)
- **Coupling type**: tight (with phase-2; loose with phase-1)
- **Shared files with other phases**:
  - `agents/ari/meta.json` (this phase)
  - `agents/leader/meta.json` (this phase)
  - `agents/jober/meta.json` (this phase)
  - `agents/ari/tools_note.md` (this phase)
  - `agents/leader/tools_note.md` (this phase)
  - `agents/jober/tools_note.md` (this phase)
  - `docs/api-reference.md` and/or `docs/scheduler-fix-plan.md` (this phase — see Context "docs inventory")
- **Shared APIs / contracts**:
  - `CATEGORY_MODULES` (`daemon/tools/_tool_registry.py:522-596`) — phase-2 wires `"scheduling"` → `daemon.tools.scheduling`
  - `PRIVILEGED_TOOL_CATEGORIES` (`_tool_registry.py:167-171`) — triple-pinned by D18/A14; `scheduling` is NOT in this set (non-privileged by design, D7 grants via per-agent `meta.json`)
  - `KNOWN_TOOL_NAMES` — drift test `tests/unit/tools/test_frozen_tool_name_discovery.py:223-242` asserts this is in sync with `CATEGORY_MODULES`
  - Boot-validation behavior at `daemon/registry.py:1096-1118` — unknown allow-list entries produce NON-FATAL WARNING only; static per-agent tool-list tests at `tests/unit/test_plane_domain_access.py:515/:637/:672/:687` and `tests/unit/test_devops_agent.py:240` are the existing precedent this phase mirrors
- **Why this coupling**: The phase-2 worker lands `daemon/tools/scheduling.py` with four `@tool` functions, registers the category in `_tool_registry.py`, and regenerates `KNOWN_TOOL_NAMES`. The drift test (`test_frozen_tool_name_discovery.py:223-242`) will FAIL until phase-2 lands. **Per architecture §8 item 16: phase 2 + phase 4 are merged as ONE PR — non-negotiable.** A red CI merge is not mergeable; this phase CANNOT land independently. Phase 1 precedes (foundation: SchedulingConfig, tz, idempotency_key, CANCELLED enum). Phase 3 follows phase 2 (REST relies on the shared scheduling service). Phase 5 follows phase 3 (pack gates the merge).

## Context

### Current `meta.json` tool lists (verbatim, this phase edits these)

**`agents/ari/meta.json:10-32`** — `allow`: `[job, bash, filesystem, time, self, help, image, context, shared_meta_kv, project, job_messages, job_tree, job_progress, job_inject, pause_instance, resume_instance, system_upgrade, mission, service]`; `deny`: `[edit_file, write_file, watch_job, watch_jobs]`. Note: category keys and individual tool names freely mixed. This phase adds `"scheduling"` to `allow` (insertion order: append at end so reviewers see the diff cleanly).

**`agents/leader/meta.json:14-15`** — `allow`: `[instance, subtree_messages, subtree_status, self, attestation, project, help, image, knowledge, mcp, critical_notes, project_history, shared_meta_kv, question, midflight, job_pause, job_resume]`. **No `deny` list.** No `"job"` category currently. This phase adds `"scheduling"` to `allow`.

**`agents/jober/meta.json:11-12`** — `allow`: `[job, help, self, time, project, knowledge, mcp, context, shared_meta_kv, mission]`; `deny`: `[watch_job, watch_jobs]`. This phase adds `"scheduling"` to `allow`.

> **Note (verified):** NONE of the three agents has `skill-set.yaml` (irrelevant to tool registration — `skill-set.yaml` is the innate-skill manifest, not the tool manifest). Confirmed via glob `agents/<id>/skill-set.yaml` returning empty for all three.

### Phase 2 tool surface (binding across all phases — D5)
- Category: `create_scheduling_tools` factory at `daemon/tools/scheduling.py` (phase-2 worker lands this — **CANONICAL name**; earlier draft referenced `python_make_scheduling_tools` which is WRONG)
- Four tools (per D5):
  - **`task_schedule`** — args: `agent_id` (default = calling agent), `message`, `label`, `when` (local), `timezone` (optional), `recurrence` (once/daily/weekly/cron), `cron_expression` (optional, raw cron if cheap), `project_id`, `priority`. Returns: `{id, status, next_run_at_local, next_run_at_utc}`.
  - **`task_schedule_list`** — args: `project_id` (optional filter), `status` (optional filter). Returns: list of `{label, agent, next_run_local, next_run_utc, status}`.
  - **`task_schedule_cancel`** — args: `id` OR `label`. Returns: `{id, status: "cancelled", cancelled_at}`. Terminal.
  - **`task_schedule_update`** — args: `source_id: str` (primary, required) **OR** `label: str | None` (alternative resolver). The tool resolves `label` → `source_id` via `source_repo.get_source_config_by_name(label)` (passed via closure, exact match). **Collision behavior:** two schedules with the same label — first match returned; document in `tools_note.md`. Plus optional `message` / `when` / `timezone` / `paused`. Returns: updated detail with `last_execution_id` field if cancel was involved (new field, see architecture §5.3).
- Pause/resume reuse existing adapter stop/start (schedules.py:251-339 + :343-387) — see restart=True; cancel is terminal.

### Registration-substrate evidence (`daemon/registry.py` + `_tool_registry.py`)
- `KNOWN_TOOL_NAMES` is the frozen-tool-name set that drift tests assert; regenerated at phase-2 land. The four tool names above (`task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update`) MUST appear in this set before any allow-list entry `"scheduling"` will resolve at runtime.
- `PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py:167-171`) is `{"system_upgrade","system-log","ens-db"}`. **`scheduling` is NOT in this set** — no privileged behavior; every agent with `"scheduling"` in allow sees the full category.
- Boot validation at `daemon/registry.py:1096-1118` — unknown allow-list entries produce NON-FATAL WARNING only. **Therefore** a stale allow-list (e.g., `"scheduling"` before phase-2 lands) does NOT crash boot; the tool surface is just empty until phase-2 wires it. This phase's static-check test (`tests/unit/test_scheduling_registration.py::test_scheduling_in_meta_json_allowlist`, see Task 5) pins the allow-list to make the rollout explicit.

### Docs inventory (this phase updates or cross-links)
- `docs/api-reference.md` — top-level API reference; check whether it has a `/api/schedules` section. If yes, add the new endpoints; if no, create a brief subsection with `See Scheduling Reference` cross-link to a new `docs/scheduling.md`.
- `docs/scheduler-fix-plan.md` — exists (Plan document, not user-facing); this phase does NOT update (out of scope; design doc).
- `docs/sources/*` — likely has `scheduler.md` already; verify and update if so.
- `docs/pluggable-sources-architecture.md` — describes the scheduler adapter; this phase adds ONE paragraph cross-linking to the user reference.
- Recommended new doc: `docs/scheduling.md` (user/operator-facing reference mirroring the tool surface). Owner of this phase writes the doc.
- Recommended update: `docs/api-reference.md` — append the new `/api/schedules` endpoints with `See Scheduling Reference`.

### `docs/agent-prompt-writing-guide.md` checklist (binding — verify every edit)
- §1 :15-28 — first person, agent POV; FORBIDDEN tokens in prose: `meta.json`, `tools.allow`, `tools.deny`, `daemon/`, `skill-set.yaml`, `innate_skills`, `auto_load`, `agent_id=`, `default_agent_versions`, `_tool_registry`, `get_version`, test paths
- §1 :31-37 — Do/Don't table (NEVER write "validated against `_tool_registry.py`"; NEVER write "Auto-loads via `skill-set.yaml`'s `auto_load: true`")
- §2 :57-64 — file roles: `tools_note.md` owns tool-by-tool reference ONLY (what each tool does, when to use/avoid)
- §2 :66-78 — canonical-home rule: ONE place per artifact; cross-links use section names (see §3 convention v2)
- §4 :128-140 — operational boundaries, NOT grant mechanics ("I can schedule X; every schedule runs in my default timezone; cancel is permanent, pause is resumable"); NEVER describe the deny mechanism in prose
- §3 :91-108 — cross-reference form is `See <Section Name>` or `See <agent>'s <Section Name>`; ZERO filename/path tokens in prompt text. The closure grep pattern is `\.md|workflow\.md|rule\.md|soul\.md|tools_note\.md|memory\.md`
- §10 :244-258 — pre-commit checklist (internals grep, canonical-home, ≤7 cardinals if rule.md touched, cross-refs resolve, tone)

## Tasks

### Task 1: `agents/ari/meta.json` — add `"scheduling"` to `tools.allow`

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 1.1 | Append `"scheduling"` to `allow` array | Insertion order: append at end (after `"service"`) so the diff reads `+    "scheduling"`. Do NOT reorder. Do NOT touch `deny`. Do NOT touch `innate_skills`. | `agents/ari/meta.json:11-31` |
| 1.2 | Verify JSON parses | `python -c "import json; json.load(open('agents/ari/meta.json'))"` returns no error. | (verification step) |

**Diff (frozen for implementer):**

```diff
--- a/agents/ari/meta.json
+++ b/agents/ari/meta.json
@@ -27,7 +27,8 @@
       "pause_instance",
       "resume_instance",
       "system_upgrade",
       "mission",
-      "service"
+      "service",
+      "scheduling"
     ],
```

### Task 2: `agents/leader/meta.json` — add `"scheduling"` to `tools.allow`

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 2.1 | Append `"scheduling"` to `allow` array | Leader's `allow` is on a single line (`agents/leader/meta.json:14-15`); append `, "scheduling"` at the very end (after `"job_resume"`). Do NOT add a `deny` list (Leader currently has none — preserve that). | `agents/leader/meta.json:14-15` |
| 2.2 | Verify JSON parses | Same as 1.2. | (verification step) |

**Diff (frozen for implementer):**

```diff
--- a/agents/leader/meta.json
+++ b/agents/leader/meta.json
@@ -13,7 +13,7 @@
   "tools": {
     "allow": ["instance", "subtree_messages",
-    "subtree_status", "self", "attestation", "project", "help", "image", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question", "midflight", "job_pause", "job_resume"]
+    "subtree_status", "self", "attestation", "project", "help", "image", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question", "midflight", "job_pause", "job_resume", "scheduling"]
   },
```

### Task 3: `agents/jober/meta.json` — add `"scheduling"` to `tools.allow`

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 3.1 | Append `"scheduling"` to `allow` array | Insertion order: append at end (after `"mission"`). Do NOT reorder. Do NOT touch `deny`. Do NOT touch `innate_skills`. | `agents/jober/meta.json:11` |
| 3.2 | Verify JSON parses | Same as 1.2. | (verification step) |

**Diff (frozen for implementer):**

```diff
--- a/agents/jober/meta.json
+++ b/agents/jober/meta.json
@@ -8,7 +8,7 @@
   "innate_skills": ["job-orchestration", "todo"],
   "no_force_explore": true,
   "tools": {
-    "allow": ["job", "help", "self", "time", "project", "knowledge", "mcp", "context", "shared_meta_kv", "mission"],
+    "allow": ["job", "help", "self", "time", "project", "knowledge", "mcp", "context", "shared_meta_kv", "mission", "scheduling"],
     "deny": ["watch_job", "watch_jobs"]
   },
```

### Task 4: `tools_note.md` updates — three agents (canonical homes, agent POV)

> **Convention v2 reminder:** No filename/path tokens in prompt prose. Cross-references use `See <Section Name>` (same-agent) or `See <agent>'s <Section Name>` (cross-agent). Operational boundaries, not grant mechanics. Operational ONLY.

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 4.1 | Add `## Scheduling` section to Ari's `tools_note.md` | **One canonical home** (no parallel copies in `soul.md` or `workflow.md`). Section shape: 1) `### task_schedule` — purpose, signature, when-to-use; 2) `### task_schedule_list` — purpose, signature, return shape including BOTH local+UTC; 3) `### task_schedule_cancel` — purpose, signature, **terminal** wording; 4) `### task_schedule_update` — purpose, signature, pause/resume semantic. Operational boundaries: "every schedule runs in my default timezone; cancel is permanent, pause is resumable". NO mentions of `_tool_registry`, `meta.json`, `tools.allow`, `daemon/`, or test paths. | `agents/ari/tools_note.md` |
| 4.2 | Add `## Scheduling` section to Leader's `tools_note.md` | Leader's posture is read-only + coordination — Leader should be a thin delegate for scheduling, NOT a power user. The section MUST be small: 1-2 sentences explaining Leader hands scheduling via dispatch (Leader owns delegation, not direct scheduling); cross-link to `See Ari's Scheduling` for the canonical reference (cross-agent form per convention v2). | `agents/leader/tools_note.md` |
| 4.3 | Add `## Scheduling` section to Jober's `tools_note.md` | Jober is a Job Orchestrator — scheduling is a peer capability to dispatching jobs. Section shape parallels Ari's but emphasizes the difference: scheduling is for WALL-CLOCK triggers (cron-like, one-shot, daily/weekly); `job_create` is for immediate dispatch. 1) `### task_schedule` (wall-clock), 2) cross-ref to `See Creating Jobs` for `job_create` (immediate), 3) `### task_schedule_list`, 4) `### task_schedule_cancel`, 5) `### task_schedule_update`. | `agents/jober/tools_note.md` |
| 4.4 | Run pre-commit checklist (guide §10 :244-258) | For EACH agent's `tools_note.md` diff: (a) grep `meta.json` / `tools.allow` / `tools.deny` / `daemon/` / `_tool_registry` / `skill-set.yaml` / `innate_skills` / `auto_load` / `agent_id=` / `default_agent_versions` / test-path tokens (`tests/unit/`, `tests/integration/`); ZERO hits expected in the new sections. (b) Cross-refs resolve — every `See <X>` points at a real heading in the owning agent's files OR a real heading in another agent's files (verify with `grep -rn` over `agents/<owner>/`). (c) No false "stated once" claims. (d) No "validated against registry" / "auto-load mechanism" prose. (e) No filename/path tokens in prose. | (verification step) |
| 4.5 | Closure grep (guide §3 :91-108) | After edits: `grep -rnE '\.md|workflow\.md|rule\.md|soul\.md|tools_note\.md|memory\.md' agents/ari/ agents/leader/ agents/jober/` returns ZERO hits in the NEW sections. (Existing sections untouched — closures on the FULL agent directory are not required by the guide; only new prose is in scope.) | (verification step) |

**Section templates (frozen for implementer — agent POV only):**

> Ari (`agents/ari/tools_note.md` — new section appended at end, after the existing `## Conversation Visibility` chain):
>
> ```markdown
> ## Scheduling
>
> I schedule wall-clock tasks — recurring or one-shot triggers that fire a message
> to an agent at a specific local time. There are four tools; they share one
> service and one set of records (a cancelled schedule never fires again, but its
> history stays).
>
> ### task_schedule
>
> **Purpose:** Create a new scheduled task. The trigger time is always interpreted
> in the supplied `timezone`; if you omit it, the configured default chain runs
> (explicit → config → host-local auto-detect → UTC with a loud warning).
>
> Signature:
>
> ```raw
> task_schedule(
>     agent_id="ari",        # default = calling agent (me)
>     message="Give me a morning briefing",
>     label="morning-briefing",
>     when="2026-10-02T06:00:00",  # local; tz-aware ISO is also accepted
>     timezone="America/New_York",  # optional
>     recurrence="daily",    # once | daily | weekly | cron
>     cron_expression=None,  # required iff recurrence="cron"
>     project_id="default",
>     priority=5,
> )
> ```
>
> **Returns:** `{id, status, next_run_at_local, next_run_at_utc}` — both timezones
> are always present (both null if no upcoming run).
>
> **Use for:** anything wall-clock that needs to land at a human-local time of day.
>
> **Don't use for:** immediate dispatch — that's job_create.
>
> ### task_schedule_list
>
> **Purpose:** List my scheduled tasks. Returns `label`, `agent`, `next_run_local`,
> `next_run_utc`, `status`. Cancelled and paused tasks are hidden by default;
> pass a `status` filter to see them.
>
> ### task_schedule_cancel
>
> **Purpose:** Cancel a scheduled task. **Terminal** — the task never fires again,
> not after a daemon restart, not after a re-list. History (what it DID fire
> before cancel) is preserved.
>
> Pass either `id` (the schedule row id) or `label` (the human-readable name).
> Cancellation is permanent; to pause-and-resume instead, use `task_schedule_update`.
>
> ### task_schedule_update
>
> **Purpose:** Reschedule, change the message, or pause/resume an existing
> scheduled task. Pause is **resumable** (the row stays, the adapter stops); cancel
> is **terminal** (different tool — see task_schedule_cancel). A pause followed by
> a daemon restart stays paused; resume picks it back up.
> ```

> Leader (`agents/leader/tools_note.md` — appended, single short paragraph):
>
> ```markdown
> ## Scheduling — Delegation Only
>
> I do not schedule directly. When a scheduling request lands on me, I dispatch
> to the right peer (Ari handles direct scheduling; Jober handles scheduling
> embedded in a job-orchestration flow). See Ari's Scheduling and Jober's
> Scheduling for the canonical reference.
> ```

> Jober (`agents/jober/tools_note.md` — new section appended at end):
>
> ```markdown
> ## Scheduling
>
> Wall-clock triggers that fire a message to an agent at a specific local time.
> This is a peer capability to Creating Jobs (`job_create` is for immediate
> dispatch; `task_schedule` is for triggers that should fire at a specific
> time-of-day). One service, four tools, terminal cancel.
>
> ### task_schedule — wall-clock trigger
>
> **Purpose:** Schedule a one-shot or recurring task. The trigger time is
> interpreted in the supplied `timezone` (or the configured default chain).
>
> Signature:
>
> ```raw
> task_schedule(
>     agent_id="jober",  # default = calling agent (me)
>     message="Re-run nightly rollup",
>     label="nightly-rollup",
>     when="02:30",       # HH:MM for daily/weekly
>     timezone="UTC",     # optional
>     recurrence="daily", # once | daily | weekly | cron
>     project_id="default",
>     priority=5,
> )
> ```
>
> **Returns:** `{id, status, next_run_at_local, next_run_at_utc}`.
>
> **Don't use for:** immediate dispatch — see Creating Jobs for `job_create`.
>
> ### task_schedule_list
>
> **Purpose:** List scheduled tasks. Returns `label`, `agent`, `next_run_local`,
> `next_run_utc`, `status`. Cancelled and paused are hidden by default; pass a
> `status` filter to see them.
>
> ### task_schedule_cancel
>
> **Purpose:** Cancel a scheduled task. Terminal — the task never fires again.
> History is preserved.
>
> ### task_schedule_update
>
> **Purpose:** Reschedule, change the message, or pause/resume. Pause is
> resumable; cancel is terminal (different tool).
> ```

### Task 5: `docs/` updates

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 5.1 | Inventory current scheduling docs | Glob `docs/sources/` and `docs/*.md` for existing scheduler/scheduling/API references. Identify which doc, if any, covers the operator-facing surface today. | `docs/` tree (Context "docs inventory" above) |
| 5.2 | Author `docs/scheduling.md` | New user/operator reference. Mirrors the four-tool surface (`task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update`) AND the three REST endpoints added in phase-3 (`POST /api/schedules`, `DELETE /api/schedules/{id}`, `GET /api/schedules/{id}`). Cover the D2 TZ rule explicitly (BOTH local+UTC surfaces; default chain). Cover D3 catch-up (one_shot_max_lateness_seconds). Cover D8 DST (spring-forward gap + fall-back ambiguity semantics for daily cron in DST zone). Cover D5 cancel-terminal / pause-resumable semantics. Cover the tz-environment default (`ENSEMBLE_SCHEDULING_DEFAULT_TZ`). | `docs/scheduling.md` (NEW) |
| 5.3 | Update `docs/api-reference.md` | Append a "Scheduling Endpoints" section listing the three new routes with brief descriptions + `See Scheduling Reference` cross-link. Mirror the existing `/api/sources` reference style if any. | `docs/api-reference.md` |
| 5.4 | Update `docs/pluggable-sources-architecture.md` (ONE paragraph) | Add a paragraph near the scheduler-adapter description: "Scheduler adapters also expose a higher-level scheduling surface (`task_schedule` / `task_schedule_list` / `task_schedule_cancel` / `task_schedule_update`) backed by the same adapter. See Scheduling Reference for the operator-facing reference." | `docs/pluggable-sources-architecture.md` |
| 5.5 | Cross-link from `docs/scheduler-fix-plan.md` | If that doc is referenced from the new `docs/scheduling.md`, add a `See Scheduling Reference` link at the top. Omit if `scheduler-fix-plan.md` is internal-only (do not edit internal design docs from a feature commission). | `docs/scheduler-fix-plan.md` |

### Task 6: Registration-proof tests (D9 — static + dynamic)

> **Note (verified):** NO daemon restart. Registration correctness is proven by these tests; runtime exposure requires the next daemon restart after phase-2 lands.

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 6.1 | Author static meta.json allow-list test | New test file `tests/unit/test_scheduling_registration.py`. Three test functions asserting `"scheduling" ∈ json.load(open(f"agents/{agent}/meta.json"))["tools"]["allow"]` for each of `ari`, `leader`, `jober`. Mirror the precedent at `tests/unit/test_plane_domain_access.py:515/:637/:672/:687` and `tests/unit/test_devops_agent.py:240`. | `tests/unit/test_scheduling_registration.py` (NEW) |
| 6.2 | Author registry-exposure test | Same file. Asserts the `scheduling` category resolves via `daemon.tools._tool_registry.discover_category_tool_names("scheduling")` (or equivalent). Asserts the four tool names appear in `KNOWN_TOOL_NAMES` (drift-test fixture `tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`). This test PASSES only after phase-2 lands — gate the implementer on phase-2 status before merging. | `tests/unit/test_scheduling_registration.py` (NEW) |
| 6.3 | Author per-agent tool-list static test (mirror plane_domain_access precedent) | Three test functions asserting each agent's `meta.json` allow-list is a SUBSET of the union of `KNOWN_TOOL_NAMES` and known category keys. Surfaces any drift between meta.json and registry. | `tests/unit/test_scheduling_registration.py` (NEW) |
| 6.4 | Author tools_note.md pre-commit-grep test | Same file. Three test functions (one per agent) parsing the new `## Scheduling` section and asserting ZERO matches for the forbidden tokens (per Task 4.4 — `meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, etc.) inside that section. The guide's pre-commit checklist is the spec. | `tests/unit/test_scheduling_registration.py` (NEW) |
| 6.5 | Author tools_note.md cross-ref resolution test | Same file. Three test functions asserting every `See <X>` in the new section resolves to a real heading in either the owning agent's files (same-agent form) or another agent's files (cross-agent form). | `tests/unit/test_scheduling_registration.py` (NEW) |

**Test skeleton (frozen for implementer):**

```python
# tests/unit/test_scheduling_registration.py (NEW)
"""Registration proof for the scheduling tool category (D9)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"

FORBIDDEN_TOKENS = (
    "meta.json", "tools.allow", "tools.deny", "daemon/", "_tool_registry",
    "skill-set.yaml", "innate_skills", "auto_load", "agent_id=",
    "default_agent_versions", "tests/unit/", "tests/integration/",
)


@pytest.mark.parametrize("agent_id", ["ari", "leader", "jober"])
def test_scheduling_in_meta_json_allowlist(agent_id: str):
    """D7 + D9: each target agent's meta.json allow-list contains 'scheduling'."""
    meta = json.loads((AGENTS_DIR / agent_id / "meta.json").read_text())
    assert "scheduling" in meta["tools"]["allow"], (
        f"agents/{agent_id}/meta.json tools.allow missing 'scheduling'"
    )


@pytest.mark.parametrize("agent_id", ["ari", "leader", "jober"])
def test_meta_json_allowlist_subset_of_registry(agent_id: str):
    """Each allow-list entry is either a known category or in KNOWN_TOOL_NAMES."""
    from daemon.tools._tool_registry import KNOWN_TOOL_NAMES, CATEGORY_MODULES
    meta = json.loads((AGENTS_DIR / agent_id / "meta.json").read_text())
    known = set(KNOWN_TOOL_NAMES) | set(CATEGORY_MODULES.keys())
    for entry in meta["tools"]["allow"]:
        assert entry in known, (
            f"agents/{agent_id}/meta.json tools.allow contains unknown entry: {entry!r}"
        )


@pytest.mark.parametrize("agent_id", ["ari", "leader", "jober"])
def test_scheduling_section_no_system_internals(agent_id: str):
    """D9 + docs/agent-prompt-writing-guide §1: the new ## Scheduling section contains no system internals."""
    md = (AGENTS_DIR / agent_id / "tools_note.md").read_text()
    section_match = re.search(r"^## Scheduling.*?(?=^## |\Z)", md, re.MULTILINE | re.DOTALL)
    assert section_match, f"agents/{agent_id}/tools_note.md missing '## Scheduling' section"
    section = section_match.group(0)
    for token in FORBIDDEN_TOKENS:
        assert token not in section, (
            f"agents/{agent_id}/tools_note.md ## Scheduling mentions forbidden token {token!r}"
        )


@pytest.mark.parametrize("agent_id", ["ari", "leader", "jober"])
def test_scheduling_section_cross_refs_resolve(agent_id: str):
    """D9 + docs/agent-prompt-writing-guide §3: every 'See <X>' points at a real heading."""
    md = (AGENTS_DIR / agent_id / "tools_note.md").read_text()
    section_match = re.search(r"^## Scheduling.*?(?=^## |\Z)", md, re.MULTILINE | re.DOTALL)
    section = section_match.group(0) if section_match else ""
    refs = re.findall(r"See\s+([^.`]+?)(?:`|\.|$)", section, re.MULTILINE)
    # resolve each ref against owning agent's files
    owning_md = md
    for ref in refs:
        # ref is like "Ari's Scheduling" (cross-agent) or "Creating Jobs" (same-agent)
        heading = ref.split("'s ")[-1].strip()
        assert heading in owning_md or any(
            heading in (AGENTS_DIR / other / "tools_note.md").read_text()
            for other in ["ari", "leader", "jober", "developer", "jober"]
        ), f"Unresolved cross-ref {ref!r} in agents/{agent_id}/tools_note.md"
```

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | `meta.json` allow-list contains `"scheduling"` before phase-2 wires the category — agent boot logs a NON-FATAL WARNING at `daemon/registry.py:1096-1118` for every affected agent on every boot until phase-2 lands | Low | High | Acceptance: register the allow-list edits in this phase; the registry-exposure test (Task 6.2) FAILS until phase-2 lands. Phase-4 is merge-coupled to phase-2 as ONE PR (architecture §8 item 16). Implementer MUST run both before claiming complete; warn-log is acceptable intermediate state. |
| 2 | `tools_note.md` sections may inadvertently name a forbidden token (`meta.json`, `tools.allow`, `_tool_registry`, etc.) — guide §10 :244-258 closure grep is the test, but a missed hit slips into production | Medium | Medium | Task 4.4 closure grep + Task 6.4 static test (frozen above) — both run in CI. Section templates in Task 4 are agent-POV only, no internals; copy them. |
| 3 | Cross-reference `See Ari's Scheduling` may not resolve if Ari's `tools_note.md` heading is named differently | Medium | Low | Convention: every agent's scheduling section heading MUST be exactly `## Scheduling`. Task 6.5 enforces this (the regex extracts the heading from `^## Scheduling`). |
| 4 | `docs/scheduling.md` may duplicate content from `docs/scheduler-fix-plan.md` or `docs/pluggable-sources-architecture.md` — drift risk | Medium | Medium | Author `docs/scheduling.md` as the ONE canonical user/operator reference; the other two docs cross-link via "See Scheduling Reference" and do NOT copy content. Task 5.4 + 5.5 enforce one-shot copy. |
| 5 | The static test `test_meta_json_allowlist_subset_of_registry` (Task 6.3) requires `KNOWN_TOOL_NAMES` to be in sync — phase-2 wires the category and regenerates the set, but if phase-2 lands AFTER phase-4 the test FAILS | High | High | **CLOSED (architecture §8 item 16): Phases 2 and 4 are merged as ONE PR — non-negotiable.** The drift test (`tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) is bidirectional and runs in CI; a red CI merge is not mergeable. If leader/jober registration is deferred past the merge, parameterize their registration tests over only the agents actually registered — do NOT xfail. Single green CI gate proves ADR-009's static posture. |
| 6 | Leader's narrow posture (Task 4.2.2) means Leader has `"scheduling"` in allow but never uses it — confusing for future maintainers | Low | Low | The Leader section explicitly says "I do not schedule directly. When a scheduling request lands on me, I dispatch." Allow-list entry exists for forward-compat (downstream Leader-spawned workers may inherit); document in PR description. |
| 7 | The forbidden-tokens list (`FORBIDDEN_TOKENS` in test skeleton, Task 6.4) is HARDCODED — if a future agent-prompt-writing-guide amendment adds new forbidden tokens the test silently misses them | Low | Low | Add a CI comment in the test pointing at `docs/agent-prompt-writing-guide.md §1` so future authors update both. |

## Acceptance Criteria

- [ ] `agents/ari/meta.json` parses as valid JSON; `tools.allow` contains `"scheduling"`; `tools.deny`, `innate_skills`, `default_queue` unchanged
- [ ] `agents/leader/meta.json` parses as valid JSON; `tools.allow` contains `"scheduling"`; no `deny` list added
- [ ] `agents/jober/meta.json` parses as valid JSON; `tools.allow` contains `"scheduling"`; `tools.deny`, `innate_skills`, `team_members`, `default_queue` unchanged
- [ ] `agents/ari/tools_note.md` has a `## Scheduling` section; section contains exactly four `### task_schedule*` subsections; ZERO forbidden-token hits per closure grep
- [ ] `agents/leader/tools_note.md` has a `## Scheduling — Delegation Only` section (or `## Scheduling` heading with the delegation-only paragraph); ZERO forbidden-token hits
- [ ] `agents/jober/tools_note.md` has a `## Scheduling` section; section references `task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update`; ZERO forbidden-token hits
- [ ] `docs/scheduling.md` exists and covers: tool surface (4 tools), REST surface (3 endpoints), TZ rule (D2 — BOTH local+UTC), catch-up (D3), DST (D8), cancel-terminal / pause-resumable semantics, environment default (`ENSEMBLE_SCHEDULING_DEFAULT_TZ`)
- [ ] `docs/api-reference.md` lists the three new REST endpoints with brief descriptions + cross-link to `docs/scheduling.md`
- [ ] `docs/pluggable-sources-architecture.md` has ONE new paragraph cross-linking to `docs/scheduling.md`
- [ ] `tests/unit/test_scheduling_registration.py` exists with the five test functions (Tasks 6.1-6.5) parametrized over `["ari", "leader", "jober"]`
- [ ] `test_scheduling_in_meta_json_allowlist` PASSES for all three agents at this phase's merge time
- [ ] `test_scheduling_section_no_system_internals` PASSES for all three agents at this phase's merge time
- [ ] `test_scheduling_section_cross_refs_resolve` PASSES for all three agents at this phase's merge time
- [ ] `test_meta_json_allowlist_subset_of_registry` PASSES at this phase's merge time (requires `KNOWN_TOOL_NAMES` to include `"scheduling"` OR `"scheduling"` to be a known category key — phases 2 + 4 land together as ONE PR per architecture §8 item 16; no xfail)
- [ ] Pre-commit checklist (guide §10) verified manually for each `tools_note.md` diff: no forbidden tokens, canonical-home rule obeyed, cross-refs resolve, tone, fan-in escape valve (only for dispatchers — Leader/Jober/Ari), skill versions consistent (no skill changes in this phase), no adapted-from provenance, no fallback-spawn-of-peer-not-in-team-members
- [ ] Closure grep per guide §3 returns zero hits in the NEW `## Scheduling` sections
- [ ] No changes to `daemon/tools/scheduling.py`, `daemon/tools/_tool_registry.py`, or any other daemon source — this phase is pure agent-prompt + docs + tests
- [ ] No daemon restart required; runtime exposure gated on next daemon restart after phase-2 lands