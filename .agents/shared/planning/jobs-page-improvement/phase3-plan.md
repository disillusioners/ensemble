# Phase 3: Mission-Context Grouping, Honest Titles & Vocabulary Unification

Date: 2026-09-10 · Author: planner[v2] via plan-creation worker · Status: Draft
Arc: jobs-page-improvement · Direction: A as C-shaped groundwork

## Objective

Give the flat list conversation structure without becoming a second glanceable panel (positioning split, `current-state-analysis.md` §0): rows group under mission/conversation headers via the panel-proven coalesced key `mission_id ?? instance_id`, headers carry honest fallback titles (optional lazy `/api/missions` enrichment for visible groups only), and the whole page speaks ONE status vocabulary (`settled` = teal + `receipt_long` receipt vs `completed` = green + `check_circle` work-done, ADR-MISSION-01).

## Shared Context (true at phase start)

- Phases 1–2 landed: one store + one filter pipeline; virtual list consumes a flattened `WindowItem[]`; all-work rows now carry real timestamps (Phase 1 task 7).
- Proven panel patterns to port (alignment research): coalesced key `route()` `instance-node.model.ts:382-394` (when both populated, `mission_id == instance_id`, fallback can never re-key); NEVER-hide principle (`:177-183`, orphans degrade, never drop); honest title chain `job_metadata.instance_name → agent_id → first-8-chars` (`job-queue-panel.component.ts:508-517`); user-touched-wins expansion G1 (`:145-154`); clamped tree sort (live-first → recency → id tiebreak, `:283-305`).
- `/api/missions` is paged (limit 10/100, offset, `total/has_more/degraded`, `title`, `initiative_preview`, `parent_mission_id`) — `daemon/routers/missions.py:172-269`. **No `mission.service.ts` exists**; the only consumer is the indicator's badge leg. `JobService.listJobsByMission` (`job.service.ts:210-217`) is deliberately unwired — NOT needed for grouping (client-side grouping is the sanctioned pattern, api findings §4).
- Child-bound rows ship `mission_id: null` from `/api/jobs` (implicit `root_only=True` enrichment, `jobs_crud.py:820-824`) — the coalesced-key fallback exists precisely for this.
- M3 prose rule: mission-side prose must NOT use the word "settled" (`job.model.ts:289-297`).

## Components / Services / Models Touched

| Path | Action |
|---|---|
| `frontend/src/app/models/jobs-grouping.model.ts` (+ `.spec.ts`) | **NEW** — pure grouping model |
| `frontend/src/app/services/mission.service.ts` (+ `.spec.ts`) | **SPLIT + NEW** — move `listMissions` (`job.service.ts:167`) + `listJobsByMission` (`:210`) OUT of `job.service.ts`; NEW `getMission(id)` wrapper for `GET /api/missions/{id}` |
| `frontend/src/app/pages/jobs/jobs.component.ts` / `.html` / `.scss` | Modify — group headers, chevrons, expansion, vocabulary sweep |
| `frontend/src/app/components/job-card/` | Modify — glyph/teal sweep if any drift vs panel |
| `frontend/src/app/models/job.model.ts` | Extend — reuse existing helpers (`missionLivenessChip`, `isReceiptRow`); no BE contract change |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Grouping model: `groupJobs(jobs) → GroupedWindowItem[]` (header\|row flattened list feeding the Phase-2 virtual source); coalesced key `mission_id ?? instance_id`; rows without any mission context land in an explicit "(no mission context)" group — **never-hide**, every row renders exactly once | P2 item list shape | Property spec: every input row appears exactly once across groups — fixtures MUST include MULTI-GROUP cases (≥3 groups mixing live, terminal, and no-context rows), never single-group-only; null-mission rows land in the fallback group |
| 2 | Expansion: `Set<string>` keyed by group id; chevron-only toggle; FIRST 2 live groups auto-expand; user-touched ids permanently override auto-expand (G1 semantics port); collapsed groups OMIT rows from the flattened list (keyboard order == DOM order) | Task 1 | Model spec: touched-wins merge, terminal groups start collapsed, collapsed ⇒ rows absent from items |
| 3 | Header meta line: `agent · N jobs · timeAgo(last activity)` — zero-count segments dropped (panel `instanceMetaLine` parity, `instance-node.model.ts:532-567`); live-first sort port | Task 1 | Spec pins sort order + segment dropping |
| 4 | Header titles via the `instanceDisplayTitle` chain (`title → agent_id · timeAgo(created_at)`, `instance-node.model.ts:509-530`) — NOT the job-row `resolveTitle` chain (`job_metadata.instance_name → agent_id → first-8-chars`, panel `:508-517`), which stays on cards; `mission.service.ts` is a **SPLIT**: `listMissions` + `listJobsByMission` move OUT of `job.service.ts` (the indicator `job-queue-indicator` is the LIVE `listMissions` consumer — parallel-creation trap: one home per call, migrate its imports, do not duplicate), plus NEW `getMission(id)` wrapper; **lazy title enrichment for VISIBLE groups only**, capped at named constant `MAX_TITLE_ENRICHMENT_FETCHES = 3` (spec-pinned, not a magic number); degraded/failed enrichment ⇒ keep fallback title (retain-last-data) | Tasks 1-3 | `mission.service.spec.ts` pins the migrated + NEW URLs (URL pins MOVE with the split; `job.service.spec.ts` drops them); indicator spec stays green (consumer migrated, not duplicated); enrichment failure leaves fallback title rendered |
| 5 | Vocabulary unification sweep: `settled` → teal `#14B8A6` + `receipt_long`, `completed` → green `check_circle` on page/cards; UPPERCASE status text on live rows; kind chips (`task` hidden priority note per card spec); M3 prose rule respected in all new copy | Task 1 | Grep specs pinning glyph/color mapping (panel spec `:611-630` as reference); zero "settled" in mission-prose copy |
| 6 | Cancel/Delete semantics unchanged (both `DELETE /api/jobs/{id}` — noted constraint P8.4, no FE change) | — | No-op guard: existing cancel/delete specs stay green |

## Dependencies

**Internal:** Phase 1 (single pipeline — grouping is a presentation layer over `store.filteredJobs()`), Phase 2 (flattened virtual item list). Phases 4–6 build on the grouped structure (banner placement, ARIA tree roles).

**External:**
- **GATE-COMBO-FIX:** NOT implicated. Displaying `settled` rows never required the combo fix — only settled-involving multi-status FILTER combos do (Phase 1 gate). Stated explicitly to prevent scope creep.
- **needs-BE (noted, out of scope):** gap-d row-titles (BE-efficient titles) — the lazy missions join is the sanctioned client workaround; gap-b real group counts beyond the window (group count is bounded by the fetched window — honest limitation, banner already covers it).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Grouped virtual scroll (headers inside virtual viewport) | Medium — the arc's known fiddly bit | Headers are items in the SAME flattened list (no nested scrollers); header height uniform ⇒ fixed itemSize holds |
| Title join cost on wide windows | Low | Visible-group-only + page-fetch cap + degraded retention; enrichment is an enhancement, never a blocker |
| Child-bound rows mis-keyed | Low | Proven fallback (`mission_id == instance_id` when both present — re-keying impossible, `instance-node.model.ts:367-378`); spec with both-shapes fixtures |
| Positioning drift toward panel-clone | Medium | Groups are headers over a flat deep-inspection list — no top-10 slice, no auto-hide, no `recentFlat` cap; §0 positioning table is the review gate |

## Test Strategy

Grouping model: property test (each row exactly once — never-hide), coalesced-key fixtures (null mission_id, both populated equal, no-context rows), expansion touched-wins, collapsed-omission. Empty-state integration: the Phase-2 `jobs-empty-state` model distinguishes `filterEmpty` (filters emptied ALL groups — filter-empty copy + clear-filters affordance) from `dataEmpty`; empty group shells are never rendered. Mission service: URL pins + degraded `total:null/has_more/degraded:true` envelope never clobbers (indicator F-5 pattern). Cross-seam invariant: group count bounded by window size; filtering regroups without orphaning rows. Vocabulary: glyph/color grep specs. **Template-extraction audit:** chevrons are new real `<button type="button">` with `aria-expanded` + `stopPropagation` (chevron tap never toggles card/navigates) — diff-audit every new binding hunk. Merge-order rule: grouping is a projection, never a re-sort of the store array (sort applies to group ORDER, row order within groups preserves server `created_at DESC`).

## Verification Commands

```bash
cd frontend
npx tsc --noEmit -p tsconfig.app.json
npx jest jobs-grouping mission.service jobs.component job-card
npm run build
```

## Sizing

**M (2–4 days).** Pure model (M) + small service (S) + template surgery with the arc's one genuinely intricate rendering constraint (grouped virtualization) — mitigated by the Phase-2 item-list contract.

## Exit Criterion

Every row renders under exactly one visible-or-collapsed group; headers show honest titles (or fallbacks); the page's status vocabulary matches the panel's (`settled` teal receipt vs `completed` green); grouping provably never drops or duplicates a row.
