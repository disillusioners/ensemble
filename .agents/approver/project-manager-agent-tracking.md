# Tracking: project-manager-agent

## Iteration 001 — 2026-08-12T20:18:16Z
**Verdict: REJECTED**
**Worker: approve-worker-pm-agent (20b99a32-8b14-4274-8ff5-3baa5102a0df)**
**Skill: plan-approval**

### Blocking Issues
1. **Cardinal count contradiction** — Plan designs 8 Cardinal Rules (Phase 2, lines 322–330) but Phase 4 §10 checklist (line 527) enforces ≤7 Cardinals (verbatim from convention guide). Plan's Research Insights (line 104) misreads guide cap as ≤8; guide actually says ≤7. Multiple internal references (success criterion #2 line 86, task 2.4 line 248, task 4.2 line 514, rule.md spec header line 319) all propagate the wrong cap. Deliverable cannot pass its own gate.
   - Fix options: (a) collapse Cardinal #8 (No secrets) into a Guideline, (b) reduce design to 7 Cardinals, or (c) raise Phase 4 threshold with explicit documented exception.

### Notes (non-blocking)
- Cardinal #1 collapses read-only + no-write-to-state; success criterion #3 expects 3 distinct semantics (satisfied via grep, but clarify).
- Filesystem category allow + per-tool deny is correct but fragile; prefer individual read-only tool allows.
- Cardinal #7 references non-existent `.agents/shared/active.md`; for stand-alone non-dispatching agent the rule has no operational effect — consider dropping to free a Cardinal slot (helps resolve Blocking #1).
- Task 4.6 dependency is correct but placement is confusing (Phase 1 artifact under Phase 4 section).
- Future Integration Contract (line 227) says "Cardinal #1 stays" but omits that Cardinal #2 must change for v2 dispatch.
- PM description (line 151) lacks explicit "non-dispatching"/"stand-alone" callout.

### Status
IN_PROGRESS — awaiting revision for iteration 002.

## Iteration 002 — 2026-08-13T14:31:03Z
**Verdict: APPROVED**
**Worker: approve-worker-plan (9457e910-b1ab-49ab-a3e1-1d14b7e22121)**
**Skill: plan-approval**

### Blocking Issues
*(none)*

### Iteration 001 Resolution
The prior blocking issue (Cardinal count contradiction — plan designed 8 Cardinals but convention cap is ≤7) is RESOLVED. The revised plan-overview.md locks Cardinals at 7 (one replaced, none added) and grows Guidelines from 8 to 10. Worker verified against both the convention guide and current rule.md.

### Notes (non-blocking)
1. **Tree-root KV partition not isolated to PM** (KV Schema § "Write-Ordering Discipline", L270–275) — `pm_leader_instances` key is visible to tree-root ancestors. Not a security risk (UUIDs aren't secrets) but plan assumes namespace exclusivity the runtime doesn't guarantee. Recommend a one-line acknowledgment in Cleanup Rules.
2. **"Same task area" undefined for instance reuse** (Guideline #9, L230 + Cardinal #2, L194) — `task_area` matching predicate has no canonical taxonomy, fuzzy-match strategy, or fallback rule. Acceptable as LLM-judged, but plan should define the predicate or state it's left to LLM discretion in workflow.md Flow 5.
3. **5-min staleness threshold unjustified** (Phase 4, L357) — `is_available()` returns False if `last_success_timestamp` >5min old. Threshold introduced without rationale or configurability note. One-line justification would make it defensible.
4. **Phase 4 on-demand probe is synchronous** (Phase 4 § "Health check lifecycle", L354–356) — HALF_OPEN probe runs inline in `_lazy_coroutine`, blocking PM turn until complete. No worst-case latency quantified; recommend probe-specific timeout (e.g., 5s) in U-MCP-7 acceptance criteria.
5. **Cardinal #2 enforcement relies partly on prompt adherence** (L194) — `team_members=["leader"]` (meta.json gate) + Cardinal text both enforce the constraint; both present and aligned. No action needed.
6. **Open Question #1 ownership** (L494) — "Exact Plane write tool names" owned by "Phase 3" (a phase label, not an actor). Recommend more specific owner (e.g., "Phase 3 implementer at PR-1 merge time").

### Worker Strengths
- Verified meta.json diff against actual `agents/project-manager/meta.json` (4 deny removed, 3 added, team_members flipped)
- Confirmed `_check_team_membership` exists at `daemon/tools/instance.py:407`
- Confirmed `shared_meta_kv` partitioned by `get_tree_root_id` (matches plan assumption)
- Checked all common traps: halo effect, missing negative cases, implicit assumptions, complexity hiding, dependency blindness

### Status
APPROVED — iteration 002 final.
