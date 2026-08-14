# PM Domain-Access Architecture — Direct ensemble + Plane Management

**Date:** 2026-08-14
**Status:** Recommended — implementation-ready
**Mode:** Standard Design, competitive fan-out (3 workers, `security-design`, approaches A/B/C)
**Instances:** 238bfced (A), 948c26fa (B), 57fdeb5a (C)
**Scope:** targeted update — NOT a redesign

---

## 1. Decision: Approach B — read-only server default + per-agent `mcp_full_access` opt-out

**Signal shape: b1 — top-level meta.json field `mcp_full_access: ["plane"]`.**

`PlaneServerDefinition.read_only_tools = True` stays (global fail-closed default). PM opts out per-server via one explicit, validated field. The daemon skips the read-only strip only for that (agent, server) pair.

### Why B (and why not A / C)

| Axis | A: remove flag | B: per-agent opt-out ✅ | C: inverted default |
|------|----------------|--------------------------|---------------------|
| Complexity | Low (no code) — but compensating controls push it back up | **Med** (~5 surgical changes, single site) | High (gate + call-site threading) |
| Scalability | Low — new agents inherit writes via category sweep; deny lists lag schema | **High** — opt-in per (agent, server), fail-closed, validator warns on typos | Med — prefix gate drifts from filter |
| Maintainability | Med — safety story moves entirely to per-agent configs | **High** — one field, one validator, CR-3 site preserved | Low — two authorization brains |
| Risk | 🔴 High — see elimination evidence below | **Low** — only (PM, plane) weakened, by design, validated | Med — violates fail-closed convention |
| Cost | Lowest code, highest latent cost | Small one-time (~1 day incl. tests) | Highest |
| **Verdict** | **Rejected** | **RECOMMENDED** | **Rejected** |

**Elimination evidence for A:** the daemon's no-config default is **allow-all** (`daemon/tools/instance.py:2145-2147`, `resolve_tool_filter` returns `None` → all tools pass, `instance.py:252-257`). Under A, any agent without a tools block (verified: `agents/watcher`) receives plane WRITE tools at discovery. Combined with deny-list drift (future Plane verbs sweep in via the `plane` category), A trades a tested global control for per-agent vigilance. A's own analysis recommended the per-agent override instead.

**Elimination evidence for C:** the gate must replicate `expand_allow_for_innate_skills` (`instance.py:131-163`) + `get_version` fallback (`instance.py:2140-2143`) + category expansion, or silently disagree with `_apply_tool_filter`. C also inverts the documented fail-closed posture (CR-3 rationale, `plane.py:123-135`) and requires `preload_mcp_tools` signature changes threaded through `manager.py:5216/5244` + `instance_lifecycle.py:2445`. All three workers independently converged on the B-family outcome.

**Why b1 over b2 (`plane:full`) / b3 (categories dict):** b2 introduces a colon convention that exists nowhere in `tools.allow` today and typos vanish silently (no operator signal); b3 collides with the native `plane` category namespace. b1 is a new isolated field — explicit, discoverable, fails closed with a validator warning (`["pane"]` typo → filter stays ON → PM keeps read-only).

**Confidence: High.** Flip condition: if the product later wants plane writes for ALL trusted agents by default, A's model becomes the right default — not the case today.

---

## 2. Daemon changes (Approach B, with one refinement from cross-checking)

All changes are at the existing CR-3 enforcement site — **no schema-cache restructuring required** (the filter operates on the per-preload local copy of `schema_dicts` at `mcp_service.py:564-592`; `_schema_cache` is per-server and untouched; `_tools_cache` is already per-instance at line 610).

| # | File | Change |
|---|------|--------|
| 1 | `daemon/registry.py:140` | Add `mcp_full_access: list[str] \| None = None` to `AgentMetadata` |
| 2 | `daemon/registry.py:990-1060` | Extend `validate_tool_config`: cross-check entries against the builtin-server registry (`daemon.mcp.builtin_servers.get_registry()`); WARN on unknown name (fail closed, consistent with existing unknown-category warnings). **Mandatory** — without it a typo on a future agent fails open silently |
| 3 | `daemon/services/mcp_service.py:735-767` | `_get_read_only_tools(server_name, agent_meta=None)` — return `False` when `agent_meta.mcp_full_access` contains `server_name` |
| 4 | `daemon/services/mcp_service.py:392` (`preload_mcp_tools`) | Resolve agent meta and pass to the filter call at line 566 |
| 5 | `daemon/mcp/builtin_servers/plane.py:123` | **NO CHANGE** to the property. Update docstring: opt-out is meta-side |

**Refinement (synthesis, from C's call-site evidence):** B's original spec resolved agent meta via `_instance_repository.get(instance_id)` — but preloading can run **before the instance row exists** on the spawn path. Resolve identity explicitly instead:

- Thread `agent_id` + `version_tag` as optional params into `preload_mcp_tools` from the two call sites where identity is known pre-insert: `manager.py:5244` (`spawn_instance_with_mcp`) and `instance_lifecycle.py:2445` (`ensure_mcp_preloaded` restore path).
- Meta lookup must follow the versioned-agent convention: `agent_registry.get_version(agent_id, version_tag)` with fallback to `get_resolved(agent_id)`.
- **Fail closed on any lookup failure** → filter stays ON (PM boots read-only rather than write-open). A silent spawn-time failure degrades capability, never safety — acceptable and testable.

**No other daemon code changes.** `plane_sync_project` is a native tool (`daemon/tools/plane_sync.py`, category `plane_sync`) — untouched by this design.

---

## 3. PM meta.json — exact diff

⚠️ **The allow-driven filter subtlety (C's finding):** PM's `tools.allow` is non-empty, so the filter is **allow-list-driven** — only tools in `allow` pass, then `deny` subtracts. **Removing a tool from `deny` alone grants NOTHING.** The project write tools must be added to `allow` AND removed from `deny`. Plane tools need only deny removal (`"plane"` category is already in allow, line 29).

```diff
 {
   "id": "project-manager",
   "name": "Project Manager",
-  "description": "Strategic project oversight with execution delegation. Read-only on code and external systems (v2).",
+  "description": "Strategic project oversight with execution delegation. Directly manages the project domain (ensemble records + Plane work); read-only on code (v2.1).",
+  "mcp_full_access": ["plane"],
   ...
   "tools": {
     "allow": [
       "explore",
       "project_get",
       "project_list",
       "project_search",
       "project_get_by_instance",
       "project_get_by_directory",
       "project_history_list",
       "project_history_search",
       "project_cn_list",
+      "project_create",
+      "project_update",
+      "project_set_status",
+      "project_history_add",
+      "project_cn_add",
+      "project_cn_remove",
+      "project_set_tags",
+      "project_add_tag",
+      "project_remove_tag",
+      "project_set_shortnames",
+      "project_add_shortname",
+      "project_remove_shortname",
+      "project_set_metadata",
+      "project_delete_metadata",
+      "project_link",
+      "project_unlink",
+      "project_add_directory",
+      "project_remove_directory",
       "filesystem",
       ...
     ],
     "deny": [
       "experience",
-      "project_cn_add",
-      "project_cn_remove",
-      "project_history_add",
       "project_history_delete",
-      "project_set_status",
-      "project_update",
-      "project_create",
       "project_delete",
-      "project_set_tags",
-      "project_add_tag",
-      "project_remove_tag",
-      "project_set_shortnames",
-      "project_add_shortname",
-      "project_remove_shortname",
-      "project_set_metadata",
-      "project_delete_metadata",
-      "project_link",
-      "project_unlink",
-      "project_add_directory",
-      "project_remove_directory",
       "edit_file",
       "write_file",
       "bash",
       ...
       "mcp",
-      "plane_create_issue",
-      "plane_update_issue",
-      "plane_delete_issue",
-      "plane_add_comment",
-      "plane_remove_comment",
-      "plane_create_cycle",
-      "plane_update_cycle",
-      "plane_assign_issue"
     ],
```

**Net: +18 allow entries, −26 deny entries, +1 top-level field. Deny list keeps 11 entries.**

**Rationale for what stays denied:**
- `project_delete` — irreversible destruction of ensemble records. **Unanimous** across all three workers: PM surfaces delete decisions, does not execute. (User's requirement did not list it.)
- `project_history_delete` — audit-trail erase; PM can append but not rewrite history. Conservative default; one-line flip later if wrong.
- `experience` — KB writes, not project records. `edit_file`/`write_file`/`bash`/`terminate_instance`/`council`/`self`/`question` — the no-code-contact spine. `mcp` — blocks non-plane `mcp_*` tools (plane_* already bypass it via the prefix override; keeping it is harmless and intentional).

**Granted per explicit user requirement:** `plane_delete_issue` IS granted (user listed it verbatim). Workers' conservative default (keep denied) is overridden by the stated requirement. Flagged as an accepted risk — destructive, but scoped to PM's own domain and user-mandated.

---

## 4. Cardinal #1 — new wording (7 Cardinals total, unchanged count)

> **Cardinal #1 — Direct Domain Management, Zero Code Contact.**
> I directly manage my domain: project records in Ensemble (create/update projects, set status/tags/metadata/shortnames, add critical notes and history events, link directories) and project work in Plane (create/update/delete issues, cycles, comments, assignments — enabled by `mcp_full_access: ["plane"]`, which only I hold). I NEVER edit source code or files outside my project-management domain (no `edit_file`/`write_file`/`bash`), NEVER delete Ensemble projects (`project_delete` is surfaced as a decision, not executed), NEVER run lifecycle operations on other agents, and NEVER touch systems beyond Ensemble project records and Plane. My writes are surgical record operations — never bulk, exploratory, or speculative.

Keeps the boundary that matters (code/files), grants the domain authority (records + work items), names the enforcement (`mcp_full_access` exclusivity), and fences the two destructive edges (source code, project_delete). Cardinal #2 (dispatch) is unchanged — governing WHO PM dispatches to, orthogonal to WHAT tools PM uses directly.

---

## 5. Prompt-file touch list (minimal)

| File | Change |
|------|--------|
| `agents/project-manager/rule.md` | Cardinal #1 replacement (above). Guideline on dispatch-vs-direct: simple record/plane updates are now DIRECT; multi-step sync orchestration still dispatches to worker+skill (Flow 5 unchanged) |
| `agents/project-manager/soul.md` | "Analyzes, doesn't mutate" → "Manages project records and project work; never touches code". Add note: `mcp_full_access` is exclusive to PM; `project_delete` is delegated |
| `agents/project-manager/tools_note.md` | Table: plane_* rows read→read/write; project_* write rows move from "denied" to "direct" |
| `agents/project-manager/workflow.md` | Flow 5 (Dispatch) stays. Add short direct-write guidance: for single-step record/plane updates, act directly and cite the resulting ID (project/issue); escalate to dispatch only for multi-step orchestration |

**Leader: ZERO changes — verified.** Leader's allow (`agents/leader/meta.json:14`) = `["instance", "self", "project", "help", "image", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question"]` — no `plane` category, no `plane_*` names, no wildcard token exists in the filter vocabulary (`*` is not recognized). The `"mcp"` allow expands only to names with the literal `mcp_` prefix (`instance.py:267-275`); plane tools are named `plane_*` via `tool_name_prefix` (`plane.py:104-120`, pinned by `test_plane_tools_not_caught_by_mcp_deny`). Leader has no `mcp_full_access` → CR-3 default strip applies → **zero new tools before AND after**. Worker: `"plane_sync"` is a native category, not MCP — unaffected. Fleet-wide sweep (verified): only project-manager and worker reference "plane" anywhere in `agents/*/meta.json`; **no agent has `mcp_full_access` today**.

---

## 6. Tests

**Update:**
- `tests/unit/test_plane_mcp.py` Class 12 `TestPlaneReadOnlyFilter` (703-780) — assertions stay valid (property unchanged); ADD assertion that `mcp_full_access` does not alter the property itself (separate gate).

**Add:**
1. `mcp_full_access` bypasses the filter for the named server — write tools present in the per-instance tool list.
2. Typo (`["pane"]`) → filter still applies → fail-closed, no crash.
3. Absent flag → default filter (regression: leader/worker/throwaway-allow-["plane"] agent all stay read-only).
4. Two instances (PM + leader) share `_schema_cache` entry but produce different `_tools_cache` lists.
5. `validate_tool_config` warns on unknown `mcp_full_access` entry (registry).
6. Identity-resolution failure / pre-insert spawn → fail closed (read-only), no exception leak.
7. Effective-surface inventory: PM's resolved plane tool set matches an expected enumerated list — **drift alarm** for future Plane verbs sweeping in via the `plane` category allow (deny-list drift mitigation, A's compensating control adapted to B).
8. Optional e2e (mock Plane MCP server): PM calls `plane_create_issue` end-to-end; leader instance sees schemas filtered.

---

## 7. Risks

- 🟡 **Deny-list drift via category sweep.** PM allows the whole `plane` category; a future Plane MCP release adding write verbs (e.g. `plane_archive_*`, `plane_bulk_*`) reaches PM automatically. Mitigation: test #7 (surface inventory) + periodic deny audit. Not blocking.
- 🟡 **Shared API key attribution.** All plane writes run under one `PLANE_MCP_API_KEY` — no per-agent attribution on Plane's side. Mitigation: tool-call trace in checkpoint logs agent_id+instance_id (already logged at the adapter). Acceptable single-tenant.
- 🟢 **`plane_delete_issue` granted by explicit user requirement** against workers' conservative default — accepted; scoped to PM's domain.
- 🟢 **Phase 4 resilience layer** (retry + circuit breaker + write-invalidated cache) already covers PM's new write traffic — no additional cooldown needed for v1.
- 🟢 **Minor protocol note:** Worker A omitted the `Skill loaded:` first-line confirmation; its report followed the skill's mandatory format and cites the skill's lens throughout, so treated as loaded. No degraded-run flag.

## 8. Decisions Pending (leader/user)

1. `project_history_delete` — kept denied (conservative). Flip to granted if PM should curate its own history entries. One-line diff either way.
2. `plane_remove_comment` — granted (comment hygiene, low blast). Confirm acceptable.
3. `plane_delete_issue` — granted per explicit requirement. Confirm the Plane workspace has acceptable undo/audit posture.

## 9. Open Questions

- Exact current Plane MCP verb inventory was not verified against a live Plane instance (workers worked from the classifier at `plane.py:197-208`). Test #7 will pin it.
- Cold-start ordering: builtin-server registry availability inside `validate_tool_config` at registry-construction time — needs test #5 to confirm no import-cycle issue.
- Third-party (non-builtin) MCP servers registering `plane_*`-prefixed tools — `DYNAMIC_TOOL_PREFIXES` reserves the namespace but enforcement against user-registered servers is unverified. Low likelihood; flag only.
