# Agent Contract Draft — Mission Tool Surface & Anti-Trap Guardrails

**Date:** 2026-09-02 · Source: agent-contract worker (structural-design) with identity remapped per adjudication (`mission_id == instance_id` — see `approach-comparison.md` §3.1). READ-ONLY design; no implementation.

---

## 1. The reframe (one sentence per layer, everywhere)

> **Jobs = your submission's journey** (transport — "was my item handled?"; delivery failure → resend).
> **Missions = the work** (outcome — "is the work done?"; mission failure → handle).

## 2. Tool surface (3 new tools, additive)

### `get_mission(mission_id: str) -> dict` — snapshot, never blocks
```json
{
  "mission_id": "<instance_id>",          // identity == instance_id (adjudicated)
  "agent_id": "leader",
  "parent_mission_id": null | "<parent instance_id>",
  "liveness": "pending|processing|paused|completed|failed|cancelled",
  "terminal_reason": null | "completed|failed|cancelled|dead_letter|...",
  "epoch": 3,                              // current epoch number; null when terminal
  "epochs": [                              // read-only nested history (best-effort for past epochs)
    {"seq": 1, "started_at": "...", "ended_at": "...", "kind": "initial|revive", "terminal_reason": "..."}
  ],
  "epoch_count": 3, "last_epoch_at": "...",
  "linked_jobs": ["job_..."],              // JobItems whose instance_id/mission linkage resolves here
  "started_at": "...", "last_activity_at": "...",
  "outcome": null | "completed|failed|cancelled"   // ALWAYS set when terminal; null when live
}
```
W4-hazard rule: DEAD-job missions report `terminal_reason: "dead_letter"` regardless of a revived instance (renderer must not let liveness override DEAD — docs/job-task-system.md:800-813).

### `await_mission(mission_id: str, timeout: float = 600, poll_interval: float = 2) -> dict`
Blocks within the tool call until terminal (`liveness` terminal AND `terminal_reason` set) or timeout → returns current snapshot (no error). Reuses the `watch_job` watch primitive (job_queue.py:1320-1360) as a blocking poll. Not-found → `{"error": "mission_not_found", "mission_id": "..."}`. Fail-closed handle: resolution via existing work/job service resolvers; NO minting.

### `list_missions(filters) -> list[dict]`
`filters: {agent_id?, liveness?, parent_mission_id?, since?, limit? (1-200, default 50)}` → mission summaries (`epoch_count` + `last_epoch_at` instead of the full `epochs` array).

## 3. Structural guardrails — making the wrong-predicate trap hard

The trap: agent awaits outcome, acts on transport-done. Five guardrails (identity-agnostic):

1. **Naming asymmetry** — `await_mission` (outcome) vs `watch_job` (transport). No shared verb; tool-selection-time disambiguation.
2. **`outcome` token asymmetry** — transport payloads carry `"outcome": null` ALWAYS; mission payloads carry the outcome value ALWAYS when terminal. A literal key the model can branch on; null-on-transport = "NOT done" by construction.
3. **Mandatory `mission_ref` cross-reference** on every terminal job payload (job_get/job_list — job payloads only, NOT watch events):
```json
"job_type": "task|message",
"mission_ref": {"mission_id": "<instance_id>|null", "agent_id": "...|null", "liveness": "processing|completed|null"},
"outcome": null
```
An agent cannot read a terminal job state without seeing the linked mission's liveness in the same payload. Reword reality: watch events (`watch_job`, incl. `events='mission_terminal'`) carry only the `work_id` + a terminal hint — never `mission_ref`; resolution happens via `job_get`, which is where `mission_ref` lives.
4. **Bidirectional doc one-liners** — every job tool: "*`status` answers transport questions only (was my submission handled?). For outcome, use `get_mission`/`await_mission`.*" Every mission tool: the inverse.
5. **Opt-in outcome events** — `watch_job(events='mission_terminal')` fires only when admission AND mission liveness are both terminal; default stays transport semantics (back-compat).

Plus: `job_continue` gate — accepts `job_type='task'` only (mirrors continue via `send_message`, the canonical mirror path).

## 4. Job-tool payload changes (additive)

- Every terminal job payload: `mission_ref` + `outcome: null` (§3.3 — job payloads only; watch events carry the `work_id` + terminal hint, resolution via `job_get`).
- `job_list`: add `job_types` filter (`["task"|"message"]`, default both); legacy `statuses` filter retained through M3 window.

## 5. ari/jober prompt edits (sketch — for the implementing agent)

- **ari/soul.md (Mode 1 tools list):** add "Mission outcome checks — `get_mission`, `await_mission` (when I need 'is the work done?' vs 'was the job handled?')."
- **ari/soul.md (Mode 2 dispatch loop):** after `job_create(watch=True)`: "On `[JOB_EVENT]`: if `job_type='task'`, the event means the work is done. If `job_type='message'`, the event means the receipt settled — the mission may still be running; check `mission_ref.liveness` and `outcome` (null on transport = NOT done) before reporting completion."
- **jober/soul.md (workflow line):** "create (mission `job_type='task'`) → `await_mission` or `watch_job(events='mission_terminal')` → decide → report."
- **jober/tools_note.md:** job_get gets the §3.4 one-liner; job_list statuses-filter note: "'completed' is the legacy derived string; for mission outcome use `get_mission` (canonical liveness)."
- **meta.json (both):** `tools.allow` += `get_mission`, `await_mission`, `list_missions`; version minor bump (additive).

## 6. Registry, tests, versioning

- Tool names registered in the frozen-name discovery surface (zero-source-readable scanner, test_frozen_tool_name_discovery.py precedent); census untouched (readers, not writers).
- New test file: mission tools read paths + await blocking semantics + W4-hazard pin + `mission_ref` presence on terminal payloads.
- Wire additive only; no existing tool removed or renamed.

## 7. What is deliberately NOT in this contract

- No HTTP `/missions` now (gated M4-i; operators covered by FE mission chips).
- No epoch ids as params/filters (epochs are read-only nested history — variant d2 rejected on complexity).
- No mission writes of any kind (projection only; storage = M4-ii, append-only, gated).
