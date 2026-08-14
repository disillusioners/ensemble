# Architecture Recommendation: PM-Plane Project Sync & Execution Bridge

**Date:** 2026-08-14
**Architect Instance:** architect (controller)
**Worker Instances:** architect-worker-tradeoff-sync (27802e9a), architect-worker-dataflow-sync (5a685310), architect-worker-decomposition-sync (f43eff35)
**Status:** Complete — 3/3 worker reports aggregated
**Confidence:** High on structural sync; Medium on semantic sync path (requires user decision on read_only_tools approach)
**Purpose:** Architecture investigation for DISCUSSION — no implementation

---

## Executive Summary

The PM-Plane sync problem has two layers with different natural homes:

1. **Structural sync** (project existence: name, description, status) → **daemon service**. Unanimous across all three workers. Deterministic, testable, no LLM dependency, no Cardinal #1 violation. The daemon calls the Plane REST API directly, bypassing the MCP tool layer entirely.

2. **Semantic sync** (issues, cycles, milestones) → **user decision required**. Two viable paths exist, each with a different solution to the `read_only_tools=True` global constraint. This is the design pivot that needs human input before implementation.

The central blocker: `read_only_tools=True` is a **class-level property** on `PlaneServerDefinition`, enforced during MCP tool discovery (`McpService._get_read_only_tools`). It strips ALL write tools (create/update/delete/add/remove/set/edit/assign patterns) from ALL agents before any per-agent `meta.json` filtering applies. No agent in the system — PM, leader, worker, or any other — has Plane write tools today.

---

## The Two Gaps

### Gap 1: No Sync Mechanism
Ensemble has projects (`Project` model with UUID, name, status, type, metadata). Plane has projects (with issues, cycles, modules). There is no bridge. PM cannot do meaningful project management without a Plane counterpart for each ensemble project.

### Gap 2: PM Can't Execute Plane Writes
PM is read-only by design (Cardinal #1). Its Plane MCP access is read-only (`read_only_tools=True` + meta.json deny list). But sync requires writes. The user suggests PM → worker (with skill) → plane tools. The problem: **worker also has no Plane write tools** — the constraint is global, not per-agent.

---

## Architecture: Structural Sync (Daemon Service)

> **Verdict: 🟢 Recommended — all three workers agree. No open questions.**

### Design

A new daemon service handles automatic project mirroring when ensemble projects are created or updated. It does NOT go through the MCP tool layer — it calls the Plane REST API directly via HTTP.

### Components

| Component | Path | Responsibility |
|-----------|------|----------------|
| `PlaneSyncService` (NEW) | `daemon/services/plane_sync_service.py` | Orchestrate sync: read ensemble project → map fields → call Plane API → store mapping → handle errors |
| `PlaneHttpClient` (NEW) | `daemon/clients/plane_http_client.py` | Async HTTP client calling Plane REST API directly; reuses `PLANE_MCP_API_KEY`; circuit breaker via `daemon/sources/circuit_breaker.py` |
| `PlaneSyncRouter` (NEW) | `daemon/routers/plane_sync.py` | HTTP endpoints: `POST /api/plane/sync/{project_id}` (manual trigger), `GET /api/plane/mapping/{project_id}` (read mapping) |
| Project create hook (MODIFIED) | `daemon/tools/project.py` or `daemon/services/instance_lifecycle.py` | Fire-and-forget sync trigger after `project_create` commits |

### Entity Mapping (Ensemble → Plane)

| Ensemble field | Plane field | Direction | Notes |
|---|---|---|---|
| `name` | `name` | E→P (master) | Dedup anchor |
| `description` | `description` | E→P | Prefixed with `[type=software]` if project_type has no Plane equivalent |
| `status` | `state` | E→P (master) | Map: `active→backlog/planned`, `paused→paused`, `completed→completed`, `archived→cancelled` |
| `project_type` | — | E-only | No Plane field; encoded as description prefix |
| `main_directory` | — | E-only | No Plane equivalent |
| `tags` | — | E-only (v1) | Future: map to Plane labels |
| — | `identifier` (slug) | P→E (read) | Stored for diagnostics, not mapped to ensemble name |
| — | `members`, `cover_image` | P-only | PM queries directly via `plane_list_*` tools |

### Sync Metadata Storage

Stored in the **`ProjectMetadataEntry`** table (not the JSONB `metadata` column) with 8 reserved `plane_*` keys:

| Key | Purpose |
|---|---|
| `plane_project_id` | Plane internal UUID — primary mapping handle |
| `plane_identifier` | Plane slug (diagnostic) |
| `plane_sync_state` | State machine: `unlinked → syncing → linked → drift_detected → error` |
| `plane_last_pushed_at` | Last successful E→P push timestamp |
| `plane_last_pulled_at` | Last successful P→E pull timestamp |
| `plane_content_hash` | SHA-256 of normalized Plane body (drift detection) |
| `plane_etag` | HTTP ETag from last Plane response |
| `plane_last_error` | Error message (truncated, set only when state=error) |

> **Why not the JSONB `metadata` column?** The JSONB column mixes user data with sync internals. `ProjectMetadataEntry` is a dedicated key-value table with UNIQUE(project_id, meta_key) constraint — cleaner separation, indexable, survives schema changes without migration.

### Sync Direction

**One-way push (E→P) for ensemble-owned fields** + **read-only pull (P→E) for drift detection**.

This is NOT bidirectional sync. Ensemble is the master for project identity (name, description, status). Plane edits to these fields surface as `drift_detected` for an operator to reconcile — they do NOT auto-propagate back to ensemble.

### Trigger Model

| Trigger | When | Direction | Mechanism |
|---|---|---|---|
| **Event-driven push** | Ensemble project created/updated | E→P | Post-commit hook → sync queue (system_parallel_queue) → PlaneSyncService |
| **Scheduled pull** | Every N minutes (default 15) | P→E (read) | PlaneProjectReconciler polls linked projects, compares content hash |
| **Manual reconcile** | PM or operator requests | E→P + diagnostic | `POST /api/plane/sync/{project_id}` → force fresh fetch + push |

### Idempotency

- **Dedup key:** `plane_project_id` in metadata. If absent, the service does a name-based pre-create lookup (`GET /projects/?search=<slug>`) before creating.
- **Crash safety:** Every state transition is in its own DB transaction. A crash mid-POST leaves `plane_sync_state='syncing'`; the next pull tick retries.
- **Conflict:** If two ensemble projects map to the same Plane project, refuse the second link and surface `drift_detected`.

### How PM Interacts

PM stays **read-only**. After sync:
- PM reads Plane project data via existing `plane_list_projects`, `plane_list_issues`, `plane_list_cycles` (already in tools.allow, already read-only).
- PM reads sync status via `project_get` (metadata includes `plane_sync_state`).
- PM triggers manual reconcile by dispatching to leader (existing Flow 5 dispatch chain), which calls the daemon HTTP endpoint.
- PM **never** calls Plane write tools. Cardinal #1 is fully preserved.

---

## Architecture: Semantic Sync (Issues, Cycles, Milestones)

> **Verdict: 🟡 User decision required — two viable paths.**

Structural sync handles project existence. But PM also needs to push issues, cycles, and milestones to Plane. This is "semantic sync" — potentially requiring LLM judgment (e.g., "which planning phases should become Plane cycles?").

Two paths exist, each solving the `read_only_tools=True` constraint differently:

### Path 1: Daemon HTTP Bypass (Extend Structural Service)

**Concept:** Extend `PlaneSyncService` to also handle issue/cycle/milestone creation. The daemon calls Plane REST API directly for everything. No agent ever touches Plane writes.

**How read_only_tools is solved:** Bypassed entirely. The daemon service uses `PlaneHttpClient` (direct HTTP), not MCP tools. The global `read_only_tools=True` on the MCP server definition is irrelevant because writes never go through MCP.

**Trigger:** PM dispatches to leader → leader calls `POST /api/plane/sync/issues/{project_id}` with issue data. Or: planning docs are parsed by the daemon and auto-pushed.

| Axis | Assessment |
|---|---|
| Complexity | 🟡 Medium — daemon must parse planning docs or accept structured issue data |
| Scalability | 🟢 High — deterministic Python, no LLM in the loop |
| Maintainability | 🟢 High — single code path, testable, no MCP changes |
| Risk | 🟢 Low — no security boundary modified, no agent gets new tools |
| Cost | 🟡 Medium — daemon work for issue/cycle/milestone CRUD via HTTP |

**Pros:** No security boundary touched. No MCP changes. All writes in one auditable service.
**Cons:** No LLM judgment in the sync (e.g., can't auto-decide "this planning phase should be a Plane cycle"). Sync logic is rigid Python rules.

### Path 2: Per-Agent read_only_tools Override (Agent Skill Path)

**Concept:** Modify `_get_read_only_tools()` in `McpService` to accept agent context. When a worker's `meta.json` explicitly allows Plane write tools (e.g., `"plane"` in `tools.allow` with no matching deny entries), the read-only filter is bypassed for that agent only. A `project-sync` skill encapsulates the multi-step sync procedure.

**How read_only_tools is solved:** Add agent context to the resolution:

```python
# Current (global):
def _get_read_only_tools(self, server_name: str) -> bool:
    definition = get_registry().get_by_name(server_name)
    return bool(getattr(definition, "read_only_tools", False))

# Proposed (per-agent):
def _get_read_only_tools(self, server_name: str, agent_id: str = None, agent_meta: dict = None) -> bool:
    definition = get_registry().get_by_name(server_name)
    if not definition or not getattr(definition, "read_only_tools", False):
        return False
    # Server declares read-only by default; check if THIS agent opts in
    if agent_meta:
        allowed = agent_meta.get("tools", {}).get("allow") or []
        opted_in = any(t.startswith(f"{server_name}_") for t in allowed)
        if opted_in:
            return False  # This agent gets write tools
    return True  # Default: read-only for everyone else
```

Worker `meta.json` would add `"plane"` to `tools.allow` (opting into write tools). PM `meta.json` keeps `plane_create_*` in `tools.deny` (stays read-only regardless).

**The `project-sync` skill** (loaded into worker via `load_skill="project-sync"`):
```markdown
---
name: project-sync
description: Sync ensemble project issues, cycles, and milestones to Plane
trigger: "sync project to Plane", "push issues to Plane", "create Plane milestones"
---

## Preconditions
1. Verify plane_project_id mapping exists (via project_get metadata)
2. If no mapping → structural sync must run first

## Procedure
1. Read ensemble project planning docs + project history
2. Read current Plane state (plane_list_issues, plane_list_cycles)
3. Diff: identify new/stale items
4. Push: plane_create_issue, plane_create_cycle as needed
5. Update: plane_update_issue for status changes
6. Verify and report summary
```

**Dispatch chain:** PM → leader (Flow 5) → leader spawns worker with `load_skill="project-sync"` → worker executes using Plane MCP write tools → reports back through chain.

| Axis | Assessment |
|---|---|
| Complexity | 🟡 Medium — MCP service modification + worker meta.json + skill creation |
| Scalability | 🟢 High — proven dispatch chain, LLM can handle fuzzy mapping |
| Maintainability | 🟡 Medium — two code paths (daemon structural + agent semantic); skill needs version management |
| Risk | 🔴 Higher — modifies the MCP security boundary; must be carefully scoped to prevent accidental unlock |
| Cost | 🟢 Low — less daemon code; skill is markdown, meta.json is config |

**Pros:** LLM judgment available (auto-decide cycle mapping, issue prioritization). Uses proven dispatch chain. Skill is flexible and evolvable.
**Cons:** Modifies the `read_only_tools` security boundary. The `base.py` docstring explicitly calls the current approach "the strongest possible enforcement" — this weakens it for opted-in agents.

### Path Comparison

| Criterion | Path 1 (Daemon HTTP) | Path 2 (Per-Agent Override) |
|---|---|---|
| MCP security boundary | ✅ Untouched | 🔴 Modified |
| LLM judgment in sync | ❌ No | ✅ Yes |
| Daemon code volume | 🟡 More | 🟢 Less |
| Agent config changes | ✅ None | 🟡 Worker meta.json |
| Skill needed | ❌ No | ✅ Yes |
| PM Cardinal #1 | ✅ Preserved | ✅ Preserved (PM deny list stays) |
| Auditability | ✅ Single service | 🟡 Agent actions in checkpoint |
| Flexibility | 🟡 Rigid rules | ✅ Evolvable via skill |
| Plane auth duplication | 🟡 Two HTTP paths | ✅ Single MCP path |

---

## How PM Triggers Sync (Both Paths)

PM uses the **existing Flow 5 — Dispatch & Delegation** pattern (already implemented, proven in architecture-dispatch.md):

```
User: "Sync project X to Plane"
  │
  ▼
PM (Flow 5): spawn_instance("leader")
  send_message(leader, "Sync project X to Plane. 
    Use POST /api/plane/sync/{project_id} for structural sync,
    then push open issues as Plane issues.")
  END TURN
  │
  ▼
Leader: spawns worker (or calls daemon API directly)
  │
  ├── Path 1: Leader calls POST /api/plane/sync/{project_id} (daemon HTTP)
  │           → daemon creates Plane project + issues
  │           → daemon returns mapping + summary
  │
  └── Path 2: Leader spawns worker with load_skill="project-sync"
              → worker calls plane_create_issue, plane_create_cycle (MCP writes)
              → worker stores mapping, reports summary
  │
  ▼
PM receives report → reports to user
```

**No changes to PM's dispatch pattern.** PM already has `team_members: ["leader"]`, `instance` tools, and `shared_meta_kv` for tracking. The sync is just another task PM dispatches.

### PM meta.json Changes

**For structural sync (Path 1):** NO changes needed. PM dispatches to leader, leader calls daemon API. PM stays exactly as-is.

**For semantic sync (Path 2):** NO changes to PM's meta.json. PM still dispatches to leader only. The worker meta.json changes (adds `"plane"` to allow), but PM's config is untouched. PM's `plane_create_*` deny entries remain as belt-and-suspenders.

---

## Recommended Approach (For Discussion)

### Phase 1: Structural Sync (Daemon Service) — BUILD NOW

**Unanimous recommendation from all three workers.** Low risk, high value, no security boundary changes.

**Changes needed:**
- NEW `daemon/services/plane_sync_service.py` — sync orchestration
- NEW `daemon/clients/plane_http_client.py` — Plane REST client with circuit breaker
- NEW `daemon/routers/plane_sync.py` — HTTP endpoints for manual trigger + mapping read
- MODIFY project create path — fire-and-forget sync trigger post-commit
- NEW migration — ensure `plane_*` metadata keys work with `ProjectMetadataEntry` (likely no migration needed — existing table supports arbitrary keys)
- NEW tests — sync service unit tests, HTTP client tests, E2E round-trip

**No agent changes.** No meta.json edits. No skill files. No MCP modifications.

### Phase 2: Semantic Sync — DECIDE BEFORE BUILDING

**The user must choose between Path 1 (daemon HTTP) and Path 2 (per-agent override).**

Key decision factor: **Do you want LLM judgment in the sync process?**

- **If YES** (auto-decide "planning phase 1 → Plane cycle", prioritize issues, fuzzy matching) → **Path 2** (per-agent override + skill). Accept the security boundary modification with careful scoping.
- **If NO** (sync is deterministic: "create a Plane issue for each open task in planning docs") → **Path 1** (daemon HTTP). Keep the security boundary untouched.

**My recommendation if forced to choose:** Start with **Path 1** for Phase 2 as well. The daemon can parse planning docs and create issues/cycles deterministically. Reserve Path 2 (per-agent override) for when a concrete use case emerges that genuinely requires LLM judgment in the sync. This keeps the security boundary intact for as long as possible.

---

## Risks

### Structural Sync (Phase 1)

- 🟡 **Plane API contract unverified** — The exact Plane REST endpoints (`POST /api/v1/workspaces/{slug}/projects/`) are inferred from the MCP tool surface, not verified against Plane's API docs. A contract test is needed before implementation.
- 🟡 **Name uniqueness divergence** — Ensemble enforces UNIQUE(name); Plane may not. Pre-create name lookup mitigates, but race conditions exist if someone creates a Plane project with the same name manually.
- 🟡 **Status mapping lossiness** — Ensemble has 4 statuses; Plane has 5+. `archived → cancelled` may surprise operators. Document the mapping explicitly.
- 🟢 **Plane API key in daemon env** — Standard secret handling. Same key already used by MCP. Document rotation path.
- 🟢 **Plane unavailability** — If Plane is down during project_create, sync fails gracefully (state=error). Manual reconcile endpoint recovers when Plane returns.

### Semantic Sync (Phase 2 — Path 2 only)

- 🔴 **CR-3 hardening weakened** — Per-agent `read_only_tools` override modifies the security boundary described as "strongest possible enforcement" in `base.py:80-101`. Mitigation: default stays True; only agents with explicit `plane` in `tools.allow` AND no matching deny entries get writes. Security test required.
- 🟡 **Two Plane auth surfaces** — MCP path (agent writes) and HTTP path (daemon writes) both use `PLANE_MCP_API_KEY`. Key rotation must update both. Mitigation: single `PlaneCredentials` config source.
- 🟡 **Skill version drift** — If Plane MCP schema changes, the `project-sync` skill must be updated. Mitigation: skill references capability, not raw tool signatures.

---

## Decisions Pending (For User)

1. **Structural sync approach:** Daemon service is recommended. Confirm or reject?
2. **Semantic sync path:** Path 1 (daemon HTTP, deterministic) or Path 2 (per-agent override, LLM-guided)? Or defer Phase 2 entirely until a concrete use case emerges?
3. **Sync direction:** One-way (E→P push + P→E read for drift) is recommended. Is bidirectional sync ever needed?
4. **Sync trigger:** Event-driven (auto on project_create) + scheduled pull + manual reconcile. Any additions?
5. **Status mapping:** `active → backlog` vs `active → planned`? (Plane distinction is about scheduling intent; ensemble "active" doesn't distinguish.)

---

## Open Questions

- **Plane REST API contract** — exact endpoints, field names, and response shapes need verification against Plane's current API docs before implementation.
- **Plane rate limits** — what's the quota for project/issue creation? The resilience layer's defaults (3 retries, 5-failure circuit breaker) are best-effort, not a documented Plane contract.
- **Lifecycle hook location** — `project_create` in `daemon/tools/project.py` already has a fire-and-forget pattern (queue provisioning). The sync hook can mirror this. Confirm the hook point is correct.
- **Migration path** — `ProjectMetadataEntry` table already exists and supports arbitrary keys. Confirm no schema migration is needed for `plane_*` keys.
- **PM access to sync state** — PM needs to read `plane_sync_state` from project metadata. PM currently has `project_get` (reads metadata). Confirm this surfaces the metadata entries.

---

## Appendix: Rejected Options

| Option | Why Rejected |
|---|---|
| **B: PM → Worker directly** | Violates PM Cardinal #2 (dispatch to leader only). No architectural gain over PM→leader→worker. Worker still hits the same `read_only_tools` wall. |
| **D: shared_meta_kv hand-off** | Race-prone, timing-dependent, debugging-hostile. Same `read_only_tools` constraint applies when the watcher picks up the work. Trades simplicity for fragility. |
| **E: Full hybrid (all at once)** | Most complex. The structural half (daemon) is uncontroversial, but building the semantic half before a concrete use case exists is premature. Build Phase 1 now; decide Phase 2 when needed. |
| **A1: Second Plane server definition** | DRY violation: two connection pools, two schema lists, two caches. A forgotten tool name change in one definition doesn't propagate. Rejected by Worker 3. |

---

## Appendix: Worker Reports Summary

| Worker | Skill | Key Finding |
|---|---|---|
| tradeoff-sync | trade-off-analysis | Option C (daemon service) scores highest (3.50) on 5-axis comparison. Agent options (A/B/D) all hit the `read_only_tools` wall. |
| dataflow-sync | data-flow-design | One-way E→P sync with 8 reserved `plane_*` metadata keys in `ProjectMetadataEntry`. Daemon writer bypasses MCP. Drift detection via content hash. |
| decomposition-sync | system-decomposition | Hybrid recommended: daemon for structural + per-agent override (A2) for semantic. New components: PlaneSyncService, PlaneHttpClient, project-sync skill. |
