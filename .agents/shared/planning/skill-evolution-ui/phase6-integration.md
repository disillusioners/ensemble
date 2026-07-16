# Phase 6: Skill-Detail Integration

## Objective
Wire all components built in Phases 3, 4, and 5 into the `skill-detail` page, add new routes for the trigger management page, and perform final QA. This is the single integration point that consolidates all parallel component work into the user-facing UI.

## Coupling
- **Depends on**: Phases 3, 4, 5 (tight — imports all built components)
- **Coupling type**: tight
- **Shared files with other phases**: Modifies `skill-detail.component.html`, `skill-detail.component.ts`, `app.routes.ts` — the only phase that touches these shared files
- **Shared APIs/interfaces**: Imports `SkillLineageTreeComponent`, `AbTestDashboardComponent`, `SkillUsageTableComponent`, `SkillTriggerListComponent`
- **Why this coupling**: This phase exists specifically to resolve the [W1] parallel-write conflict. Phases 3-5 build components in isolation; Phase 6 integrates them sequentially into shared files.

## Context
- Phase 3 built `SkillLineageTreeComponent` + `MermaidGraphComponent` (standalone, with `input()` and `output()` APIs)
- Phase 4 built `AbTestDashboardComponent` (standalone, fetches its own data)
- Phase 5 built `SkillUsageTableComponent` + `SkillTriggerListComponent` + `SkillTriggerFormComponent` (standalone)
- Phase 2 fixed `SkillMetrics` field names and updated `skill-detail.component.html` metric tile bindings (already done)
- Current `skill-detail.component.html` has 9 sections (header, loading, error, meta card, metrics dashboard, A/B panel, lineage flat lists, content card, feedback card)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Import all new components into `SkillDetailComponent` | Add imports to `skill-detail.component.ts`: `SkillLineageTreeComponent`, `AbTestDashboardComponent`, `SkillUsageTableComponent`. Add to component `imports` array. | `frontend/src/app/pages/skills/skill-detail/skill-detail.component.ts` |
| 2 | Replace lineage flat lists with `SkillLineageTreeComponent` | Replace `<div class="lineage-section">` with `<app-skill-lineage-tree [lineage]="lineage()" [currentSkillId]="skillId()" (navigateTo)="onNavigateTo($event)" />`. Add `onNavigateTo(id)` method that routes to `/skills/${id}`. | `frontend/src/app/pages/skills/skill-detail/skill-detail.component.html` |
| 3 | Replace A/B test panel with `AbTestDashboardComponent` | Replace `<div class="ab-test-section">` with `<app-ab-test-dashboard [skillId]="skillId()" [abTestStatus]="abTest()" />`. Keep the existing resolve buttons below the dashboard. | `frontend/src/app/pages/skills/skill-detail/skill-detail.component.html` |
| 4 | Add usage history section | Add new `<mat-expansion-panel>` section after metrics dashboard: "Usage History" containing `<app-skill-usage-table [skillId]="skillId()" />`. Collapsed by default. | `frontend/src/app/pages/skills/skill-detail/skill-detail.component.html` |
| 5 | Add `SkillTriggerListComponent` to triggers page | Create a new standalone page component OR add a tab in the skills list page. Wire route. | `frontend/src/app/pages/skills/skills.component.html`, `frontend/src/app/app.routes.ts` |
| 6 | Add route for trigger management | Add route `skills/triggers` → lazy-loaded `SkillTriggerListComponent`. **Must precede `skills/:id`** in route order (same as `skills/bank` pattern). | `frontend/src/app/app.routes.ts` |
| 7 | Wire lineage navigation | When `SkillLineageTreeComponent` emits `navigateTo(skillId)`, route to `/skills/${skillId}`. This lets users click nodes in the tree to navigate to ancestor/descendant skills. | `skill-detail.component.ts` |
| 8 | Add composite score tile to metrics dashboard | Add a 7th tile "Composite Score" to the metrics dashboard, visible only when the skill is in an A/B test (`ab_test_group` is set). Uses stats from `AbTestDashboardComponent`. | `skill-detail.component.html` |
| 9 | Final QA pass | Verify all sections render correctly: lineage tree with Mermaid graph, A/B dashboard with scores, usage table with pagination, trigger page accessible. Test with skills that have: no lineage, deep lineage, active A/B test, no A/B test, usage records, no usage records. | Manual testing |

## Key Files
- `frontend/src/app/pages/skills/skill-detail/skill-detail.component.ts` — **MODIFY** add imports + navigation handler
- `frontend/src/app/pages/skills/skill-detail/skill-detail.component.html` — **MODIFY** replace sections + add new ones
- `frontend/src/app/app.routes.ts` — **MODIFY** add `/skills/triggers` route
- `frontend/src/app/pages/skills/skills.component.html` — **MODIFY** add triggers tab (if tab approach)

## Integration Layout (skill-detail page after Phase 6)

```
┌─────────────────────────────────────────────┐
│ Detail Header (back button + actions)       │  ← existing
├─────────────────────────────────────────────┤
│ Skill Meta Card (name, desc, status, etc.)  │  ← existing
├─────────────────────────────────────────────┤
│ Metrics Dashboard (7 tiles)                  │  ← existing + 1 new tile (composite score)
│  [Success] [Selected] [Applied] [Completions]│
│  [Fallbacks] [Consec. Fail] [Composite]      │
├─────────────────────────────────────────────┤
│ ▼ Evolution Lineage (Mermaid tree)           │  ← REPLACES flat lists
│   [graph TD with nodes + edges]              │
│   [fallback legend with clickable nodes]     │
├─────────────────────────────────────────────┤
│ A/B Test Dashboard                           │  ← REPLACES basic panel
│   [Variant A card] [Variant B card]          │
│   [Per-metric comparison table]              │
│   [Ready-to-resolve / needs-data banner]     │
│   [Resolve buttons]                          │  ← existing, kept
├─────────────────────────────────────────────┤
│ ▼ Usage History (collapsible)                │  ← NEW section
│   [Paginated table with expandable rows]     │
├─────────────────────────────────────────────┤
│ Content Card (markdown source)               │  ← existing
├─────────────────────────────────────────────┤
│ Feedback Card                                │  ← existing
└─────────────────────────────────────────────┘
```

## Navigation Structure

```
/skills                    ← Skills list (existing)
/skills/bank               ← Skill bank CRUD (existing)
/skills/triggers           ← Trigger management (NEW — Phase 6)
/skills/:id                ← Skill detail (existing, now enriched)
```

## Constraints
- Route ordering: `skills/triggers` and `skills/bank` MUST precede `skills/:id` in `app.routes.ts`
- All component insertions must handle null/empty data gracefully (no crash when lineage is empty, no A/B test active, etc.)
- Usage history section should be collapsed by default (expansion panel) to avoid overwhelming the page
- Lineage tree navigation (`navigateTo`) must use Angular Router, not direct URL manipulation

## Testing Strategy
- **Integration test**: skill-detail page renders all new components when data is available
- **Integration test**: skill-detail page renders gracefully when data is missing (no lineage, no A/B test, no usage)
- **Navigation test**: clicking a lineage tree node navigates to the correct skill detail page
- **Route test**: `/skills/triggers` loads the trigger list component
- **Visual QA**: test with real skill data (various states: deep lineage, active A/B, usage records, triggers)

## Deliverables
- [ ] All components imported and rendered in skill-detail page
- [ ] Lineage flat lists replaced with Mermaid tree component
- [ ] A/B test panel replaced with analytics dashboard
- [ ] Usage history section added (collapsible)
- [ ] Trigger management route added (`/skills/triggers`)
- [ ] Lineage navigation (click node → navigate) working
- [ ] Composite score tile in metrics dashboard (conditional on A/B test)
- [ ] Integration tests passing
- [ ] `ng build` compiles
- [ ] Full visual QA with real data
