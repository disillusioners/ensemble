# Plan: Phase 1 + Phase 2 — PM Agent Prompts & New Capabilities

**Date:** 2026-08-13
**Author:** planner[v2] via plan-creation worker
**Status:** Draft — Ready for review
**Parent effort:** `pm-system-improvement/` (Phase 3 architecture-dispatch plan already exists; Phase 4 Plane-MCP improvements are planned separately)
**Companion documents:**
- `architecture-dispatch.md` — Phase 3 (meta.json + instance reuse architecture). This plan depends on its findings but does NOT re-plan them.
- `docs/agent-prompt-writing-guide.md` — binding convention for all agent prompt prose (cited as **APWG** below).
- **`plan-overview.md`** — SINGLE SOURCE OF TRUTH for Cardinal/Guideline text, Flow numbering (C4), meta.json spec, KV schema, and unified task list. This document defers to it for canonical text.

---

## Pre-Execution Self-Check

| Check | Status |
|-------|--------|
| Feature/task identified | ✅ Phase 1 + Phase 2 of PM agent upgrade — prompt rewrites + new Plane-aware capability flows |
| Scope locked | ✅ Planning ONLY prompts + new flow definitions; NOT meta.json wiring (Phase 3), NOT Plane tool layer (Phase 4) |
| Research context loaded | ✅ `architecture-dispatch.md` reviewed; current PM files read end-to-end; APWG read end-to-end |
| Output location | ✅ `.agents/shared/planning/pm-system-improvement/phase1-2-prompts-capabilities.md` |
| Reference docs available | ✅ APWG, architecture-dispatch, current soul/rule/workflow/tools_note/meta.json |
| Standard template noted | ✅ Objective / Scope / Phases / Coupling / Risks / Success Criteria + per-phase plan |

---

## Objective

Upgrade the Project Manager (PM) agent from a stand-alone, read-only advisor into a **strategic brain that dispatches execution**: rewrite the prompt files to reflect its new dispatcher identity (PM→leader), document the new tool surface (Plane reads, instance control, shared KV), and add three new capability flows — **Roadmap**, **Milestones**, **Burndown** — that synthesize Plane data with internal project state.

**Single-sentence test:** *When complete, a user can ask PM "where are we on phase X and what's blocking it?" and receive a roadmap view, milestone-vs-exit-criterion delta, and burndown chart synthesized from Plane + `.agents/shared/planning/` + project history — and PM can dispatch the unblock to leader in the same turn.*

---

## Scope

### In Scope

- **`agents/project-manager/soul.md`** rewrite — identity shifts from "stand-alone, non-dispatching" to "strategic brain, dispatches to leader."
- **`agents/project-manager/rule.md`** Cardinal + Guideline reshuffle — replace Cardinal #2 (no-dispatch) with a delegation cardinal; add Plane-degradation guideline; drop the "hand-back" closing guideline.
- **`agents/project-manager/workflow.md`** rewrite — Plane-aware versions of Flow 1–4; add new Flow 6 (Roadmap), Flow 7 (Milestones), Flow 8 (Burndown) per C4 canonical numbering; add Flow 5 (Dispatch & Delegation) per Phase 3; replace closing hand-back with a Dispatch Protocol.
- **`agents/project-manager/tools_note.md`** rewrite — add `plane_*` rows, `spawn_instance`/`send_message`/`list_instances`/`get_instance_info`/`shared_meta_kv` rows; update "What I do NOT hold" section.
- **Cross-reference sweep** — fix stale `Guideline #8` (hand-back) and `Cardinal #2` (no-dispatch) pointers in `soul.md` and `workflow.md`.
- **Testing strategy** — prompt-consistency, tool-access, and flow-execution verification per APWG §10.

### Out of Scope (planned elsewhere)

- **`meta.json` schema changes** — Phase 3 `architecture-dispatch.md` already specifies the exact diff (tools.allow/deny, team_members, version bump). This plan **references** the diff but does not re-plan it.
- **Plane MCP server / tool-layer improvements** — Phase 4 (`plane-mcp-architecture.md` in `.agents/shared/planning/project-manager-agent/`).
- **Leader prompt changes** to recognize PM as a dispatcher — leader's existing pattern of treating any `send_message` as a task already handles this; no leader-prompt changes required.
- **Instance-tracking data structure** (`pm_leader_instances` JSON in `shared_meta_kv` — W5 canonical schema in `plan-overview.md`) — Phase 3 architecture plan specifies the operational contract; this plan documents what PM sees.
- **Architectural re-design of the PM↔leader lifecycle** — settled by Phase 3; this plan consumes the result.

### Why these boundaries

| Boundary | Reason |
|----------|--------|
| Plane tool-layer changes are out | Phase 4 already owns graceful-degradation at the tool layer (timeout + error wrapping). PM prompt only documents the **behavioral contract** PM relies on; it does not re-specify the tool. |
| meta.json is out | The diff is already specified in Phase 3 and verified against `daemon/tools/instance.py:226-258`. Re-planning here would duplicate and risk drift. |
| Leader prompt is out | Leader's existing contract (treat any incoming `send_message` as a fresh task; reuse its own planner/reviewer/giter across loop iterations) already covers PM-driven dispatch. No new contract needed. |

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | PM Prompt Rewrites | Rewrite soul/rule/workflow/tools_note to reflect dispatcher identity + Plane surface | 8 | tight with Phase 3 (consumes meta.json diff), tight with Phase 2 (flows live in workflow.md) | pending |
| 2 | New Capability Flows | Define + integrate Roadmap, Milestones, Burndown flows | 4 | tight with Phase 1 (workflow.md is the home), loose with Phase 4 (degradation contract is documented, not re-planned) | pending |

**Total tasks: 12** (within 3–10-per-phase guideline; Phase 1 has 8 because 5 file rewrites + cross-ref sweep + cardinal re-design naturally split into 8 tasks).

---

## Coupling Map

| | Phase 1 (Prompts) | Phase 2 (Flows) | Phase 3 (Architecture, sibling) | Phase 4 (Plane, sibling) |
|---|---|---|---|---|
| **Phase 1** | — | tight (Phase 2's flows live inside Phase 1's `workflow.md` rewrite) | loose (Phase 1 documents tools PM holds; Phase 3 wires those tools in meta.json) | independent |
| **Phase 2** | tight | — | loose (Phase 2's flows assume dispatch capability PM gains in Phase 3) | loose (Phase 2 documents degradation behavior Phase 4 implements at tool layer) |
| **Phase 3** | loose | loose | — | independent |
| **Phase 4** | independent | loose | independent | — |

**Key coupling risks:**
- **Phase 1 ↔ Phase 2 ordering:** Phase 2's flow definitions MUST land in the same `workflow.md` rewrite that Phase 1 produces. If split into two commits, cardinal renumbering in Phase 1 may invalidate Phase 2's cross-refs. → **Mitigation:** single PR for both phases.
- **Phase 1 ↔ Phase 3:** Phase 1's `tools_note.md` lists `spawn_instance`, `send_message`, `shared_meta_kv`, `list_instances`, `get_instance_info`. Phase 3 wires these in meta.json. If Phase 3 lands first, prompt claims match reality. If Phase 1 lands first, the prompt is aspirational. → **Mitigation:** merge Phase 1 + Phase 3 in the same release; never ship Phase 1 alone.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Cardinal count drifts past 7 after PM-dispatch additions | High (APWG §3 mandates ≤7; agents stop obeying long flat lists) | Medium | Lock at exactly 7 in the rewrite; new behaviors land in **Guidelines**, not Cardinals. New Cardinal #2 absorbs both "read-only on code" and "delegate execution to leader" as one rule. |
| 2 | Stale `Guideline #8` (hand-back) cross-references survive the rewrite | High (workflow.md and soul.md both link to it; hand-back is being removed) | High | Sweep all `Guideline #8` pointers in the same commit; replace with Dispatch Protocol reference. Sweep list is in §"Cross-Reference Sweep" below. |
| 3 | Plane tools fail silently — PM hallucinates issue lists | High (false evidence violates Cardinal #4 — Evidence-cite) | Medium | Document the degradation contract in `tools_note.md` (Plane tool failure → PM uses `project_history` + planning docs only, marks gap explicitly); add Cardinal-grade "never fabricate Plane data" guideline. |
| 4 | PM dispatches leader before the user actually wants action | Medium (false execution cost; user expected advice, not work) | Medium | Cardinal #2 restricts dispatch to **strategic execution that requires a leader**; tactical questions and ambiguous asks hand back. Workflow's Dispatch Protocol requires the dispatch message to begin with the explicit phrase `DISPATCH:` so the user/leader can audit intent. |
| 5 | PM forgets `shared_meta_kv` write ordering — writes registry before spawn returns, gets phantom instance_id | Medium | Medium | Document the write-after-spawn invariant in `workflow.md` Dispatch Protocol step 4 (after the architecture plan's Write-Ordering Discipline rule). |
| 6 | Instance reuse confusion — PM spawns a new leader when one already exists for the task | Medium (wasted instance slot; possible context fragmentation) | Medium | PM must read `"pm_leader_instances"` registry BEFORE deciding spawn-vs-reuse. Document in `workflow.md` Dispatch Protocol step 1. |
| 7 | Prompt prose leaks system internals (`meta.json`, `tools.allow`, `daemon/` paths) — fails APWG §1 | High | Medium | Run APWG §10 grep against the new files before commit. The forbidden-token list is in §"Testing Strategy." |
| 8 | Cardinal #5 ("Frame decisions, do not make them") collides with new dispatch authority | Medium | Low | Cardinal #5 governs **decisions** (recommendations to the user); Cardinal #2 governs **execution** (delegation to leader). They are orthogonal. Document the distinction in `soul.md` Role-vs-Leader table. |
| 9 | Roadmap/Milestones/Burndown duplicate information already in `.agents/shared/planning/` | Low | Medium | Each new flow explicitly cross-references the planning doc by path; Plane data adds external view, planning doc is the internal ground truth. |
| 10 | Burndown chart confuses "Plane cycle" with "internal milestone" — different windows | Medium | High | Burndown Flow explicitly distinguishes: Plane cycle window is calendar-based; internal milestone window is `phaseN-plan.md` exit-criterion based. PM must label which one each chart uses. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Cardinal count in `rule.md` is exactly ≤7 | `grep -cE '^[0-9]+\.\s+\*\*' agents/project-manager/rule.md` (count Cardinal section rules only) | ≤7 |
| 2 | No APWG-forbidden tokens in prompt prose | `grep -rE 'meta\.json|tools\.allow|tools\.deny|daemon/|_tool_registry|skill-set\.yaml|innate_skills|seed_all|default_agent_versions|agent_id=' agents/project-manager/*.md` | 0 matches |
| 3 | All `Guideline #8` and `Hand-back` references resolved or removed | `grep -nE 'Guideline #8|Hand-back' agents/project-manager/*.md` | 0 matches except in the new Dispatch Protocol where the term "hand-back" is explicitly **redefined** |
| 4 | `tools_note.md` lists every tool the agent holds operationally (allow + deny-by-policy) | Cross-reference against Phase 3 meta.json diff | 100% of allow-listed tools documented; every deny-listed tool either documented as "I do not hold" or implicitly out of scope |
| 5 | Each of Flow 1–4 has at least one Plane-aware step | `grep -E 'plane_|\*Plane\*|Plane data' agents/project-manager/workflow.md` | ≥4 (one per existing flow) |
| 6 | Flow 6 (Roadmap), Flow 7 (Milestones), Flow 8 (Burndown) each have: data sources, step-by-step process, output template, flow-chaining rule, Plane-degradation clause | Manual review against `APWG §2` one-concern-per-file rule | All three flows pass |
| 7 | New Cardinal set replaces the "no dispatch" stance with a delegation stance | Manual review of Cardinal #2 text | Cardinal #2 explicitly references leader delegation |
| 8 | `shared_meta_kv` write-after-spawn invariant is documented in Dispatch Protocol | `grep -E 'AFTER.*spawn|write.*AFTER' agents/project-manager/workflow.md` | ≥1 explicit rule |
| 9 | PM can spawn leader + send_message + reuse same instance via conversation history | Integration test (PM → spawn leader "implement-x" → PM → send_message same leader "follow-up") | Both spawn and reuse succeed; reuse reuses (not re-spawns) |
| 10 | Plane unavailable → PM degrades to planning-doc-only without fabrication | Failure-injection test (set `PLANE_MCP_URL=` empty, ask PM for roadmap) | PM response explicitly states "Plane unavailable; using planning docs only" and never lists fabricated issues |
| 11 | `meta.json` version bumped to `2.0.0` | `cat agents/project-manager/meta.json \| jq .version` | `"2.0.0"` (Phase 3 owns this; this plan only verifies the bump happens alongside the prompt merge) |

---

## Research Insights

From `architecture-dispatch.md` (Phase 3, already approved):
- **Instance reuse is a first-class capability** — `_prepare_enqueued_message` revives `COMPLETED → RUNNING` (`daemon/services/instance_messaging.py:1486-1510`); `parent_id` is permanent (`daemon/tools/instance.py:484`); PM only needs to remember instance_id. → Drives Cardinal #2 ("delegate execution to leader") and the reuse step in Dispatch Protocol.
- **`instance` tool category expands to 5 tools** (`spawn_instance`, `send_message`, `terminate_instance`, `list_instances`, `get_instance_info`); deny-list wins over allow at `daemon/tools/instance.py:226-258`. → Drives `tools_note.md` rows.
- **`shared_meta_kv` is partitioned by `context_key`** (tree-root instance_id); PM and all its leaders share the KV namespace. → Drives the registry design (single key `"pm_leader_instances"` — W5 canonical schema).
- **Report-back is automatic** — `ReportInjection` + `PROCESS_REPORT` task + DependencyBus emit deliver leader reports as HumanMessages. → PM does not poll; it just waits.

From `docs/agent-prompt-writing-guide.md`:
- **≤7 Cardinal rules** at top of `rule.md` (APWG §3). → Drives Cardinal re-design.
- **No system internals in prose** (APWG §1) — forbidden tokens: `meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, `skill-set.yaml`, `innate_skills`, `seed_all`, `default_agent_versions`, `agent_id=`. → Drives the testing-strategy grep.
- **One canonical home per artifact** (APWG §2). → Cardinal set lives ONLY in `rule.md`; tone directive lives ONLY in `soul.md`; tool reference lives ONLY in `tools_note.md`; flows live ONLY in `workflow.md`.
- **Cross-reference hygiene** (APWG §3): prefer stable labels (`Cardinal #N`, `Guideline #N`, section-name) over positional refs. → Drives the cross-ref sweep using semantic labels.
- **END TURN contract** (APWG §7): state once in `workflow.md`, reference elsewhere. → Drives the "End turn after dispatch" line to live only in `workflow.md`'s Dispatch Protocol.
- **Fan-in escape valve** (APWG §7): max 1 re-dispatch cap. → Drives Dispatch Protocol's "stuck leader" ladder.

From the current `agents/project-manager/` files (read end-to-end):
- Existing Terse + Full output templates in `soul.md` (lines 34–62) are well-shaped; **keep them** and add new templates (Roadmap, Milestones, Burndown) rather than replacing.
- Existing Severity guideline (`rule.md` Guideline #3, lines 19) is well-shaped; **keep it**.
- Existing cross-refs in `soul.md` (lines 10, 20, 28, 32) and `workflow.md` (lines 11, 20, 40, 50, 58, 59, 70, 76) all use stable labels — except `Guideline #8` (hand-back) which is being removed.

From the recent-history note (system context):
- Tester was upgraded in-place to v2 before versioning existed (project critical-note). Treat PM v2 the same way: in-place rewrite of `agents/project-manager/`, bump to `2.0.0`, no `agents/project-manager[v2]/` directory needed unless a rollback is anticipated. → Drives a **single in-place rewrite**, no parallel directory.
- Prompt-writing-guide applies (project critical-note). → Drives the APWG compliance throughout this plan.

---

## Open Questions

| # | Question | Why it matters | Proposed default |
|---|----------|----------------|------------------|
| 1 | Should `plane_*` write tools (`plane_create_issue`, `plane_update_issue`) be in PM's allow-list? | Currently `meta.json` line 29 has `"plane"` (the category). If write tools are included, PM can mutate external state — violates Cardinal #1. | **Default:** deny `plane_write_*` by exact name (mirroring `terminate_instance` deny-by-name in Phase 3). PM is read-only on Plane. If a future need arises, add a separate Cardinal + tool row. |
| 2 | Should `plane_*` tools be in `deny` (forcing category-allow) or in `deny` by exact name (overriding category-allow)? | Category-allow includes all `plane_*` tools (read + write); exact-name-deny can block writes. | **Default:** mirror `terminate_instance` pattern — `"plane"` in allow, then exact-name-deny any `plane_write_*` discovered. Phase 3 to verify against the actual registered tool names (discovered dynamically from MCP). |
| 3 | What is the canonical name for the dispatch-tracking KV key? | W5 resolved: key is `"pm_leader_instances"` (JSON array) per `plan-overview.md` canonical schema. | **Resolved:** `"pm_leader_instances"`. |
| 4 | Does PM need a `memory.md` for cross-session continuity (e.g., recurring project baselines)? | v1 has no `memory.md`. Roadmap + Milestones flows would benefit from a "previous roadmap" memory. | **Default:** out of scope for this plan. If recurring roadmaps prove valuable, add `memory.md` in a follow-up. The roadmap flow output is the artifact for now. |
| 5 | Should the dispatch-protocol closing line replace the "hand-back" line, or be appended? | The "hand-back" line currently lives in `workflow.md` line 76 and `soul.md` Guideline #8. Removing it changes PM's external contract. | **Default:** replace. The new closing is conditional: if PM dispatched → END TURN; if PM only analyzed → `If you want this acted on, ask PM to dispatch (PM has authority).` |

---

## New Cardinal Set (proposed, ≤7)

> **W2: Canonical text is in `plan-overview.md` → "Canonical Cardinal Set".** The table below is a summary referencing that text. No "v1 verbatim" labels — all text is the final v2 canonical wording.

| # | Cardinal | Change | Rationale |
|---|----------|--------|-----------|
| 1 | **Read-only on code, plans, configs, project state, and external systems.** I never edit, write, commit, or mutate source code, plans, configurations, project state, or external systems (Plane). My output is messages and dispatch instructions only. | **Extended (W2 + C2)** | Adds "and external systems (Plane)" — enforces read-only on Plane at prompt level. Pairs with meta.json deny for Plane write tools. "v1 verbatim" label removed. |
| 2 | **Dispatch execution to `leader` only.** I may spawn `leader` instances to execute work. I spawn exactly the agents in my `team_members` — currently `leader` only. I always END MY TURN after `send_message`. | **Replaces v1 #2** | Reflects new dispatcher identity. |
| 3 | **Answer in proportion to the question.** Default Terse; Full or named-flow template when user asks. | Unchanged | |
| 4 | **Evidence-cite every claim.** Bullets carry evidence refs. When Plane unavailable, cite planning docs only and note gap — never fabricate Plane data. | **Extended** | Adds Plane-unavailability clause. |
| 5 | **Frame decisions, do not make them.** Surface trade-offs + recommendation; final call is human. For tactical execution, dispatch to `leader` per Cardinal #2. | **Modified parenthetical** | Cross-refs Cardinal #2. |
| 6 | **Scope discipline.** I do not expand the user's stated question. | Unchanged | |
| 7 | **No secrets in output.** I never reproduce secrets, API keys, or credentials. | Unchanged | |

**Removed:** v1 Guideline #8 — "Hand-back" (replaced by Guideline #8 "Dispatch vs advisory mode" — canonical text in `plan-overview.md`).

**Net change:** Cardinal count stays 7. Guideline #8 is redefined (not removed). Guidelines #9–#10 added. Total Guidelines = 10 (canonical list in `plan-overview.md`).

---

## Cross-Reference Sweep

| File | Line | Current Reference | New Reference | Reason |
|------|------|-------------------|---------------|--------|
| `soul.md` | 20 | `see \`rule.md\` → Guideline #8` | `see \`rule.md\` → Cardinal #2` (delegation), `see \`workflow.md\` → "Dispatch Protocol"` | The Handoff row in the Role-vs-Leader table changes meaning — PM no longer "hands back"; it dispatches or hands back conditionally. |
| `workflow.md` | 11 | `see Guideline #8 — Hand-back` | `see \`workflow.md\` → "Dispatch Protocol" (tactical / ambiguous hand back)` | Workflow intro references the closing hand-back rule, which is moving into Dispatch Protocol. |
| `workflow.md` | 70 | `the closing hand-back (Guideline #8)` | `the closing contract — see \`workflow.md\` → "Closing"` | Flow Chaining section references the closing line. |
| `workflow.md` | 76 | bold inline `If you want this acted on, hand to \`leader\`.` | conditional close (see `workflow.md` rewrite below) | The closing line is being replaced. |

**Stable cross-references that DO NOT need changes** (using semantic labels, APWG §3 best practice):
- `soul.md:10` → `Cardinal #3` (Answer in proportion) — STABLE
- `soul.md:28` → `Guideline #3` (Severity) — STABLE
- `soul.md:32` → `Cardinal #3` — STABLE
- `workflow.md:20` → `Cardinal Rules (plural)` — STABLE
- `workflow.md:40` → `Cardinal #4 — Evidence-cite` — STABLE
- `workflow.md:50` → `Cardinal #6 — Scope discipline` — STABLE
- `workflow.md:58` → `Cardinal #4 — Evidence-cite` — STABLE
- `workflow.md:59` → `Cardinal #5 — Frame decisions` — STABLE

**Post-sweep verification command:** `grep -nE 'Guideline #8|Hand-back' agents/project-manager/*.md` — must return 0 matches except in `workflow.md` "Closing" where "hand back" appears in its new conditional form.

---

## Phase 1 Plan — PM Prompt Rewrites

### Objective

Rewrite `agents/project-manager/soul.md`, `rule.md`, `workflow.md`, and `tools_note.md` to reflect the new dispatcher identity, Plane surface, and Dispatch Protocol. Stay within APWG conventions and keep Cardinal count at exactly 7.

### Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1.1 | Rewrite `soul.md` — identity header + Nature bullets + Role-vs-Leader table + Tone section + Output Templates | None | Header status changes from "stand-alone, non-dispatching" to "strategic brain, dispatches to leader." Nature bullet "Non-dispatching" replaced with "Dispatches to leader — see Cardinal #2." Role-vs-Leader table Handoff row updated (see Cross-Reference Sweep). Output Templates add **Roadmap** (see Phase 2 Flow 6 spec) and **Milestones** (see Phase 2 Flow 7 spec). Burndown chart is rendered via `chart`, not a text template — referenced from Burndown flow only. |
| 1.2 | Rewrite `rule.md` Cardinal section — replace Cardinal #2 with delegation cardinal | 1.1 | New Cardinal set as specified in "New Cardinal Set" above. Count is exactly 7. No duplication with Guidelines. |
| 1.3 | Rewrite `rule.md` Guidelines section — add Plane-degradation guideline + dispatch-closing guideline; remove Guideline #8 (Hand-back) | 1.2 | New Guidelines cover: Severity (kept), Risk math (kept), Decision framing (kept), Stuck-on-data (kept), Skill versioning (kept), **Plane degradation (new)**, **Dispatch closing contract (new)**. Guideline #8 removed. Total Guidelines = 9. |
| 1.4 | Rewrite `workflow.md` Flows 1–4 to be Plane-aware; update Closing section | 1.1, 1.3 | Each of Flow 1 (Risk), Flow 2 (Progress), Flow 3 (Scope), Flow 4 (Decision) gains a step that pulls Plane data when relevant (e.g., Risk Flow step "Check Plane for any � blockers on the affected cycles"). Closing section replaced with conditional close (see Task 1.6). |
| 1.5 | Add `workflow.md` Dispatch Protocol section (Flow 5 — see Phase 3) | 1.2, 1.4 | New section between Flow 4 and Flow 6. Contains: spawn-vs-reuse decision (read `"pm_leader_instances"` registry first — W5 schema), spawn-and-track sequence (write KV AFTER spawn), send_message format (`DISPATCH:` prefix, scoped to leader, self-contained per APWG §7), END TURN contract (APWG §7), fan-in escape valve (max 1 re-dispatch, then escalate to user), write-after-spawn invariant. |
| 1.6 | Rewrite `workflow.md` Closing — replace hand-back with conditional close | 1.5 | New Closing: "If I dispatched in this reply → `END TURN` (the system resumes me when leader reports). If I only analyzed → `If you want this acted on, ask PM to dispatch — I have authority for strategic execution.`" |
| 1.7 | Rewrite `tools_note.md` — add new tool rows; update "What I do NOT hold" | 1.2 | New rows for `spawn_instance`, `send_message`, `list_instances`, `get_instance_info`, `shared_meta_kv`. Plane section: state that `plane_*` tools are read-only (write tools denied by name — see Open Question #1). Document the degradation contract (Plane unavailable → PM uses planning docs only, marks gap). "What I do NOT hold" updated: remove `instance` (now held), `shared_meta_kv` (now held), `send_message` (now held), `spawn_instance` (now held); add `plane_write_*` (denied by name), `terminate_instance` (denied), `bash`/`edit_file`/`write_file` unchanged. |
| 1.8 | Cross-reference sweep + APWG grep | 1.1–1.7 | `grep -nE 'Guideline #8|Hand-back' agents/project-manager/*.md` → 0 matches except the conditional new "hand back" wording in `workflow.md` Closing. `grep -rE 'meta\.json\|tools\.allow\|tools\.deny\|daemon/\|_tool_registry\|skill-set\.yaml\|innate_skills\|seed_all\|default_agent_versions\|agent_id=' agents/project-manager/*.md` → 0 matches. |

### Coupling

- **Tight with Phase 2 (same files):** Flow 6/7/8 definitions land in `workflow.md` rewrite (Task 1.4 area); new Output Templates (Roadmap, Milestones) land in `soul.md` rewrite (Task 1.1). Both must commit together.
- **Tight with Phase 3 (architecture-dispatch.md):** Tasks 1.7's `tools_note.md` row for `spawn_instance` corresponds exactly to Phase 3's `tools.allow` diff. If Phase 3 changes which tools are allowed, Task 1.7 must update.
- **Loose with Phase 4 (Plane tool layer):** Task 1.7's Plane-degradation documentation mirrors Phase 4's tool-layer timeout behavior. If Phase 4 changes the failure mode (e.g., returns empty list instead of raising), Task 1.7 must update the "I treat empty list as data gap" clause.

### Risks (Phase-1-specific)

- **Task 1.1 risk:** Adding too many output templates bloats `soul.md` past ~2k chars (APWG §10 quick-ref). → Mitigation: keep Roadmap/Milestones templates short; refer to Flow 6/7 for the full step-by-step; only the rendered shape lives in `soul.md`.
- **Task 1.5 risk:** Dispatch Protocol becomes a wall of prose, violating APWG §2 (one concern per file). → Mitigation: extract any reusable dispatch snippets into a `pm-dispatch-strategy.md` skill (auto-loaded) if the section exceeds ~60 lines. Default is inlining; escalate to skill only if length demands.
- **Task 1.7 risk:** Listing tools PM holds leaks system internals (APWG §1). → Mitigation: list tools in operational rows ("why I hold it, how I use it"), not in a meta-grant-narrows-it phrasing. The forbidden-token grep in Task 1.8 enforces this.

### Exit Criterion

All 8 tasks complete; Cardinal count is exactly 7; cross-ref sweep returns 0 unexpected matches; APWG §10 forbidden-token grep returns 0 matches. `git diff agents/project-manager/` shows only the 4 prompt files changed (no `meta.json` edits — Phase 3 owns those).

---

## Phase 2 Plan — New Capability Flows

### Objective

Define three new flows — **Roadmap**, **Milestones**, **Burndown** — that synthesize Plane data (cycles, issues, milestones) with internal project state (`.agents/shared/planning/`, `project_history`). Each flow must integrate into the existing Flow Chaining ladder and degrade gracefully when Plane is unavailable.

### Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.1 | Define Flow 6 — Roadmap Generation (full spec below) (C4 numbering) | Phase 1 (Task 1.4 — Flow Chaining section needs the new flow IDs) | Flow lives in `workflow.md` between Flow 5 and Flow 7. Step-by-step process, data sources, output template (Roadmap template in `soul.md`), Plane-degradation clause, chaining rules. |
| 2.2 | Define Flow 7 — Milestone Tracking (full spec below) (C4 numbering) | Phase 1 | Same structure as Flow 6. Output template in `soul.md` is **Milestones** table. |
| 2.3 | Define Flow 8 — Burndown / Status Reporting (full spec below) (C4 numbering) | Phase 1 | Same structure. Uses `chart` for the burndown visualization; chart code lives in the flow spec, not in `soul.md`. |
| 2.4 | Add Flow Chaining rules for the new flows | 2.1, 2.2, 2.3 | Flow Chaining section extended with: Roadmap → Milestones (auto-run when Roadmap reveals a milestone discrepancy); Milestones → Decision Framing (when a milestone is blocked); Burndown → Risk Assessment (when burndown slope is negative-trending). Existing 2 chaining rules preserved. |

### Coupling

- **Tight with Phase 1 (same `workflow.md`):** Flows must land in the same rewrite.
- **Tight with Phase 1 (same `soul.md`):** Output templates for Roadmap + Milestones live there. Burndown chart code lives in `workflow.md` (charts are workflow artifacts, not identity templates — APWG §2).
- **Loose with Phase 4 (Plane MCP):** Flow Plane-degradation clauses assume Phase 4's tool-layer failure mode (timeout + empty/exception). If Phase 4 changes behavior, update the clauses.

### Risks (Phase-2-specific)

- **Task 2.3 risk:** Burndown chart Mermaid syntax is fragile — long labels overflow. → Mitigation: cap labels to 20 chars in the flow spec; cite the chart tool's own length limits.
- **Task 2.1 risk:** Roadmap pulls from too many sources and exceeds context budget. → Mitigation: scope Roadmap to ONE feature (user-provided); do not auto-synthesize cross-project roadmaps in a single reply.
- **Task 2.2 risk:** Milestones cross-reference Plane cycles vs internal phase-exit-criteria — labels can collide. → Mitigation: use distinct column headers (`Plane milestone` vs `Phase exit criterion`) in the Milestones table.

### Exit Criterion

All 4 tasks complete; Flow 6/7/8 exist in `workflow.md` with full step-by-step, data sources, output template reference, chaining rule, Plane-degradation clause. Flow Chaining section has 5 total rules (2 existing + 3 new). No duplicated output templates across `workflow.md` and `soul.md`.

---

## New Flow Definitions

### Flow 6 — Roadmap Generation

**Purpose:** Synthesize a timeline view of a single feature from planning docs + Plane cycles + project history.

**Step-by-step process:**
1. **Scope:** user names the feature. If no feature is named, hand back ("Which feature's roadmap?").
2. **Read internal planning:** `.agents/shared/planning/<feature>/plan-overview.md` and each `phaseN-plan.md`. Extract phase objectives + exit criteria. If absent, hand back with `### Gaps` — there is nothing to roadmap.
3. **Read Plane data:** call `plane_list_cycles` (or current dynamic discovery) for cycles touching the feature; `plane_list_issues` for issues tagged with the feature label or in the matching cycle. Extract cycle windows + issue status counts.
4. **Read project history:** `project_history_list` for the feature's last 30 events; classify each as `phase-done`, `phase-blocked`, `scope-change`, `decision-made`.
5. **Synthesize timeline:** for each phase in the plan, list: planned window (from `phaseN-plan.md`), Plane cycle window (if any), observed progress (from history), and current status (on-track / slipped / blocked).
6. **Render chart:** use `chart` with a Mermaid `gantt` for the timeline (one row per phase, one section per Plane cycle if present).
7. **Output:** the **Roadmap** template from `soul.md` → "Output Templates."

**Roadmap template (lands in `soul.md`):**
```
## Roadmap: <feature>

As of <time>:
- Phases: <N planned, M in progress, K done>
- Cycles touched: <Plane cycle names or "n/a — no Plane data">
- Slippage: <none / <phase>: +N days / unknown — <reason>>

### Timeline

| Phase | Planned Window | Plane Cycle | Observed Progress | Status |
|-------|----------------|-------------|-------------------|--------|
| Phase 1 — <name> | <window> | <cycle or "—"> | <evidence> | � on-track / 🟡 slipped / 🔴 blocked |

### Chart
<mermaid gantt — see Flow 6 step 6>

### Adjacent Work
- 🔴 <adjacent scope flagged in Cardinal #6>

### Decisions Pending
<0–3 framed questions>
```

**Plane degradation clause:** if any `plane_*` call raises (timeout, auth, network) or returns empty, Flow 6 proceeds with planning-doc + project-history only. The output's `Plane Cycle` column is filled with `— (Plane unavailable: <reason>)` per row, and a single `### Data Gap` section appears above `Decisions Pending`: `Plane MCP unavailable; roadmap synthesized from `.agents/shared/planning/<feature>/` and project history only. Cycle windows in this report are NOT calendar-anchored.`

**Chaining rules:**
- If Flow 6 reveals a milestone discrepancy → auto-run Flow 7 in the same reply.
- If Flow 6 reveals a 🔴 blocked phase → auto-run Flow 1 (Risk) in the same reply.

---

### Flow 7 — Milestone Tracking

**Purpose:** Cross-reference Plane milestones (cycles, targets) with internal `phaseN-plan.md` exit criteria; flag discrepancies.

**Step-by-step process:**
1. **Scope:** user names the feature; PM also accepts implicit scope ("milestones for current plan") if the user has only one active feature.
2. **Read internal exit criteria:** for each phase in `.agents/shared/planning/<feature>/`, extract the exit criterion text.
3. **Read Plane milestones:** call `plane_list_cycles` (current name); extract each cycle's name, start, end, and progress.
4. **Cross-reference:** for each internal exit criterion, find the closest Plane cycle or milestone by name + window. Classify as: **aligned** (Plane milestone matches exit criterion), **plane-ahead** (Plane milestone completes before internal criterion is met — risk of premature sign-off), **plane-behind** (Plane milestone scheduled after internal criterion is meant to be done), **no-plane-match** (internal criterion has no Plane representation).
5. **Confirm via history:** for each row, pull the most recent `project_history` event for that phase. If the internal exit criterion is textually met but no history event confirms it, flag `evidence gap`.
6. **Output:** the **Milestones** template from `soul.md`.

**Milestones template (lands in `soul.md`):**
```
## Milestones: <feature>

As of <time>:

| Internal Phase / Exit Criterion | Plane Milestone | Alignment | Last History Event | Status |
|---------------------------------|-----------------|-----------|--------------------|--------|
| Phase 2 — "Auth flow tests pass" | Cycle "Auth Sprint 14" | aligned | ✅ tests-green commit abc123 | 🟢 |
| Phase 3 — "Migration deployed" | (no match) | no-plane-match | � blocker noted 2026-08-10 | 🔴 |

### Discrepancies
- <list each row where Alignment != "aligned">

### Evidence Gaps
- <list each row where Last History Event is missing for a textual exit criterion>

### Decisions Pending
<0–3 framed questions>
```

**Plane degradation clause:** if Plane is unavailable, Flow 7 reduces to **internal-only milestone tracking**. The `Plane Milestone` column is filled with `— (Plane unavailable)` for every row. No `Discrepancies` section is emitted (there is no Plane side to compare against). An `### Data Gap` section appears: `Plane MCP unavailable; milestones reflect internal `phaseN-plan.md` exit criteria only.`

**Chaining rules:**
- If Flow 7 reveals a 🔴 evidence gap → auto-run Flow 1 (Risk) in the same reply.
- If Flow 7 reveals a 🟡 plane-ahead discrepancy → auto-run Flow 4 (Decision Framing) on whether to sign off early.

---

### Flow 8 — Burndown / Status Reporting

**Purpose:** Produce a burndown chart combining Plane cycle progress with internal event velocity.

**Step-by-step process:**
1. **Scope:** user names the feature OR Plane cycle. If both are given, use the feature scope and the cycle only as a window hint.
2. **Define the window:** explicit user window (e.g., "last 14 days") OR the Plane cycle's `[start, end]` OR the planning-doc's first phase's planned window. State the window in the reply.
3. **Pull Plane data:** `plane_list_issues` filtered by the cycle (or feature label). For each day in the window, count `issues closed on that day` (or `issues remaining` as of that day — choose one and stick with it).
4. **Pull history data:** `project_history_list` filtered by feature + window. For each day, count `events completed` (commit, merge, deploy, test-green, blocker-cleared — events that move work forward).
5. **Render chart:** use `chart` with a Mermaid line chart. Two lines: `Plane issues remaining` (right-axis if dual-axis supported, else normalized) and `internal events completed`. X-axis = days in window.
6. **Synthesize:** describe the slope (`accelerating / steady / decelerating`), call out any day-over-day delta >2σ, and compare Plane trend vs internal trend (e.g., Plane flat but internal spiking = hidden work; Plane dropping but internal flat = external signal without internal confirmation).
7. **Output:** **Terse** template from `soul.md` plus the chart and a 3-line interpretation paragraph.

**Burndown template (NOT in `soul.md` — output is text + chart):**
```
## Burndown: <feature or cycle>

Window: <start> → <end> (<N> days). Source: <Plane + internal / Plane only / internal only>.

<mermaid line chart — see Flow 8 step 5>

**Slope:** <accelerating / steady / decelerating>. **Largest day-over-day delta:** <day> (<+N / -N>).

**Interpretation:**
- <1 sentence on Plane trend>
- <1 sentence on internal trend>
- <1 sentence on alignment or gap>

### Data Gap
<only if Plane or internal data was unavailable>

### Decisions Pending
<0–3 framed questions>
```

**Plane degradation clause:** if Plane is unavailable, Burndown reduces to **internal-only velocity chart**. The chart has one line (`internal events completed`) and the `Data Gap` section states: `Plane MCP unavailable; burndown reflects internal project history only — external issue progress not visible.`

**Chaining rules:**
- If Flow 8 shows decelerating trend → auto-run Flow 1 (Risk) in the same reply.
- If Flow 8 shows plane-ahead / internal-behind divergence → auto-run Flow 7 (Milestones) in the same reply.

---

## Testing Strategy

> Bind to APWG §10 pre-commit checklist + project conventions (PostgreSQL primary DB, tester agent owns `.agents/tester/rules/ensure.md`).

### Pre-commit (developer runs before push)

- [ ] **Cardinal count:** `awk '/^## Cardinal Rules/,/^---/' agents/project-manager/rule.md | grep -cE '^[0-9]+\.'` → exactly **7**.
- [ ] **No stale cross-refs:** `grep -nE 'Guideline #8|Hand-back' agents/project-manager/*.md` → 0 matches except the conditional "hand back" in `workflow.md` Closing.
- [ ] **APWG §1 forbidden tokens:** `grep -rE 'meta\.json|tools\.allow|tools\.deny|daemon/|_tool_registry|skill-set\.yaml|innate_skills|seed_all|default_agent_versions|agent_id=' agents/project-manager/*.md` → **0 matches**.
- [ ] **No verbatim duplication** of Terse/Full templates between `soul.md` and `workflow.md`. (APWG §2 canonical-home rule.) `diff <(grep -A20 'Terse (default):' agents/project-manager/soul.md) <(grep -A20 'Terse template:' agents/project-manager/workflow.md)` → meaningful diff is OK; verbatim copy is not.
- [ ] **Tone directive present in `soul.md`** (APWG §5): the Tone & Voice section covers voice-to-caller, voice-in-dispatch, per-severity framing.
- [ ] **END TURN contract stated once in `workflow.md`** (APWG §7): `grep -cE 'END (YOUR|MY) TURN' agents/project-manager/workflow.md` → exactly **1**.
- [ ] **Fan-in escape valve defined** (APWG §7): `grep -cE 'max.*re-dispatch|max 1 re-dispatch' agents/project-manager/workflow.md` → ≥1.

### Unit tests (PostgreSQL)

> Project critical note: PostgreSQL is the primary dev/test DB. Run tests against PostgreSQL, not SQLite.

- **`tests/unit/test_pm_v2_prompts.py` (new)** — prompt-consistency checks:
  - `test_cardinal_count_is_seven` — parse `rule.md`, assert ≤7 Cardinals.
  - `test_no_apwg_forbidden_tokens` — load all 4 prompt files, assert none contain APWG §1 forbidden tokens.
  - `test_cross_references_resolve` — every `Cardinal #N` and `Guideline #N` reference points at an actual rule.
  - `test_dispatch_protocol_present` — `workflow.md` contains the keywords `DISPATCH:`, `END MY TURN`, `pm_leader_instances`.
  - `test_three_new_flows_present` — `workflow.md` contains `Flow 6`, `Flow 7`, `Flow 8` headers.

- **`tests/unit/test_pm_v2_tools.py` (new)** — tool surface checks (defers to Phase 3 for actual config; this plan only verifies the prompt side):
  - `test_tools_note_documents_all_allowed_tools` — every tool in Phase 3's allow-list appears as a row in `tools_note.md`.
  - `test_tools_note_does_not_document_denied_tools_as_held` — no row in `tools_note.md` claims to hold `bash`, `edit_file`, `write_file`, or any `plane_write_*` tool.
  - `test_no_secrets_in_prompts` — none of the 4 prompt files contain a string matching a key/token regex.

### Integration tests (PostgreSQL, run by tester agent)

- **`tests/integration/test_pm_dispatch.py` (new)**:
  - `test_pm_spawns_leader_for_strategic_question` — PM, asked "implement feature X", spawns a leader and dispatches via `send_message`. Asserts a `ReportInjection` row exists within 60s.
  - `test_pm_reuses_leader_instance` — PM spawns leader, receives report, then dispatches follow-up to the SAME instance_id. Asserts no second `spawn_instance` call was made (`shared_meta_kv` key `"pm_leader_instances"` shows one entry with two reports).
  - `test_pm_hands_back_tactical_question` — PM, asked "how do I run `pytest`?", does NOT spawn. Asserts no `spawn_instance` call.
  - `test_pm_degrades_when_plane_unavailable` — unset `PLANE_MCP_URL`, ask PM for roadmap. Asserts PM response contains "Plane unavailable" and does NOT list any `CY-###` issue IDs.

### Manual / reviewer checks (approver agent owns, per project convention)

- [ ] **APWG §10 checklist** — reviewer runs the full 12-item checklist (already enumerated above) on the PR.
- [ ] **Cross-reference sweep** — reviewer runs the grep commands and confirms 0 stale matches.
- [ ] **Prompt tone sample** — reviewer spawns PM with 3 representative questions and confirms the Terse/Full/Roadmap/Milestones/Burndown templates render as written.

### Tester agent gate (per project convention)

- 🟡 Per the project critical note: full e2e test (`.agents/tester/rules/ensure.md`) is MANDATORY when changes touch job/task/queue system. **This plan does NOT touch the job/task/queue system** — it is a pure prompt rewrite. Tester e2e is therefore **not required** for this plan's merge. (If Phase 3 architecture changes are merged in the same PR, the tester gate applies to Phase 3, not Phase 1+2.)

---

## Risks & Mitigations Summary

> Full table is in §"Risks" above. This section restates only the most critical ones for the implementer.

1. **Cardinal count drift (Risk #1, High):** Lock at 7. New behaviors land in Guidelines. If a new requirement cannot fit as a Guideline, do not add an 8th Cardinal — collapse two existing ones.
2. **Stale cross-refs (Risk #2, High):** Sweep `Guideline #8` and `Hand-back` in the same commit. The grep at the top of the testing strategy is the gate.
3. **Plane fabrication (Risk #3, High):** Cardinal #4's "Plane unavailability" clause + per-flow degradation clause + integration test `test_pm_degrades_when_plane_unavailable` form three layers of defense.
4. **Phase 1 ships without Phase 3 (Coupling risk):** Phase 1 alone makes PM aspirational (prompt claims tools PM doesn't yet hold). Mitigation: gate the merge — Phase 1 PR is blocked from merge until Phase 3 PR is also ready.

---

## Deliverable Checklist

- [ ] `agents/project-manager/soul.md` rewritten (Task 1.1)
- [ ] `agents/project-manager/rule.md` Cardinal + Guidelines rewritten (Tasks 1.2, 1.3)
- [ ] `agents/project-manager/workflow.md` Flows 1–4 Plane-aware (Task 1.4); Dispatch Protocol / Flow 5 added (Task 1.5 + Phase 3); Closing replaced (Task 1.6); Flows 6/7/8 added (Phase 2 Tasks 2.1, 2.2, 2.3); Flow Chaining extended (Task 2.4)
- [ ] `agents/project-manager/tools_note.md` rewritten (Task 1.7)
- [ ] Cross-reference sweep passes (Task 1.8)
- [ ] APWG §10 pre-commit checklist passes
- [ ] Unit tests pass against PostgreSQL
- [ ] Integration tests pass (PM spawns leader, reuses leader, hands back tactical, degrades on Plane unavailable)
- [ ] `agents/project-manager/meta.json` version bumped to `2.0.0` (Phase 3 owns; verify the bump is in the merged PR)
- [ ] PR description cites this plan + `architecture-dispatch.md` + APWG compliance

---

**End of plan. No follow-up summary.**
