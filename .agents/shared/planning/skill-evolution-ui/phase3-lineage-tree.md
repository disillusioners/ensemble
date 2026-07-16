# Phase 3: Lineage Tree Visualization (Component Build)

## Objective
Build a Mermaid-based evolution tree component that visually shows parent→child relationships, generation depth, change summaries on edges, and skill status. Includes a reusable Mermaid wrapper component. **This phase builds standalone components only — integration into `skill-detail.component.html` happens in Phase 6.**

## Coupling
- **Depends on**: Phase 2 (tight — imports `SkillLineage`, `SkillLineageNode` interfaces)
- **Coupling type**: tight
- **Shared files with other phases**: New component files only. **Does NOT modify `skill-detail.component.html`** (deferred to Phase 6).
- **Shared APIs/interfaces**: Consumes `SkillLineage` from Phase 2; `skill.service.getLineage()` already exists
- **Why this coupling**: The `SkillLineageNode` interface (with `change_summary`, `content_diff`) must exist before the tree component can type its inputs.
- **Parallel safety**: This phase creates only NEW files. Phases 4 and 5 also create only NEW files. All three can run in parallel without file conflicts. Integration into shared files is centralized in Phase 6.

## Context
- Phase 1 enriched the `/lineage` endpoint to return `change_summary` + `content_diff` per edge (Task 5)
- Phase 2 delivered updated `SkillLineage` with `SkillLineageNode[]` (edge metadata)
- Mermaid v11.4.0 is globally configured with dark theme
- `MermaidActionsService` exists for copy SVG/PNG + fullscreen dialog

## Visualization Approach: Mermaid (Decision Rationale)

**Chosen: Mermaid `graph TD`** rendered via `<markdown [data]="mermaidSource" mermaid>`

**Why not ng-zorro NzTreeModule:**
- ng-zorro is 0% used in the project — adopting requires providers, icon setup, theme token configuration
- NzTreeModule renders hierarchical trees (expand/collapse), NOT relationship graphs
- Skill lineage is a DAG (parent→child), better suited to graph visualization
- Mermaid is already installed, configured, and has action services for copy/fullscreen

**Why not custom D3/SVG:**
- No charting library installed; building from scratch is high-effort
- Mermaid handles graph layout automatically (no manual positioning)

**Mermaid graph structure:**
```
graph TD
    origin["Skill Name (Gen 0)"]:::origin
    child1["Skill Name (Gen 1)"]:::evolved
    origin -->|"change_summary"| child1
```

## ⚠️ Mermaid Click Callback Limitation [S2]

**Mermaid's `click nodeId callback` directive requires a globally accessible function** — it does NOT work cleanly with Angular component methods. The callback is evaluated in `window` scope, not the component's `this` context.

**Workaround for interactivity:**
- Do NOT use Mermaid's `click` directive
- Instead, attach DOM event listeners to the rendered SVG after Mermaid finishes rendering
- Use `@ViewChild` + `ElementRef` to query `.node` elements by node ID (Mermaid assigns node IDs as DOM data attributes)
- Map clicked node → skill ID → emit Angular `output()` event
- Example:
  ```typescript
  ngAfterViewChecked() {
    if (!this.svgListenersAttached()) {
      const svg = this.host.nativeElement.querySelector('svg');
      if (svg) {
        svg.querySelectorAll('.node').forEach(node => {
          const nodeId = node.id; // e.g., "flowchart-origin-0"
          node.addEventListener('click', () => this.onNodeClick(nodeId));
        });
        this.svgListenersAttached.set(true);
      }
    }
  }
  ```
- Alternatively, use edge labels that show `change_summary` (static) and let the user click nodes in the tree to navigate — handled via `routerLink` or `output()` events

**Fallback approach**: If DOM event listeners prove fragile (Mermaid may re-render), use a simpler approach — display the graph as read-only visual, and provide a separate clickable legend/list below the graph for navigation.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `MermaidGraphComponent` | Reusable wrapper for non-chat Mermaid rendering. Inputs: `graphSource: input.required<string>()`, `title: input<string>('')`. Uses `<markdown [data]="graphSource()" mermaid>` internally. Integrates `MermaidActionsService` for copy SVG/PNG + fullscreen buttons. Shows toolbar with actions. | `frontend/src/app/components/mermaid-graph/mermaid-graph.component.ts/html/scss` — **NEW** |
| 2 | Create `SkillLineageTreeComponent` | Standalone component. Input: `lineage: input.required<SkillLineage>()`, `currentSkillId: input.required<string>()`. Uses `computed()` signal to transform lineage data → Mermaid graph string. | `frontend/src/app/components/skill-lineage-tree/skill-lineage-tree.component.ts/html/scss` — **NEW** |
| 3 | Implement Mermaid graph string builder | Pure function: `buildLineageGraph(lineage: SkillLineage, currentSkillId: string): string`. Generates `graph TD` Mermaid syntax. | Inside `SkillLineageTreeComponent` or utility file |
| 4 | Implement edge metadata detail popup | When user clicks a node/edge, show `change_summary` + `content_diff` in a popup. Use CDK Overlay pattern from `TodoGraphPopupComponent` OR a Material dialog. | Inside `SkillLineageTreeComponent` |
| 5 | Style Mermaid nodes by skill status | Define Mermaid CSS classes: `.origin`, `.evolved`, `.current`, `.active`, `.inactive`, `.deprecated`. Inject via Mermaid `classDef` statements in the graph string. | Inside `SkillLineageTreeComponent` |
| 6 | Handle large trees | If generation depth > 5 or total nodes > 20, show warning + "View Full Tree" button that opens `MermaidFullscreenDialogComponent`. Default view limits to nearest 3 generations. | Inside `SkillLineageTreeComponent` |
| 7 | **[S2] Implement node click via DOM listeners** | Attach DOM event listeners to rendered SVG nodes (not Mermaid's `click` directive). Emit Angular `output<string>()` with skill ID on node click. Include fallback legend/list for navigation. | Inside `SkillLineageTreeComponent` |

## Key Files
- `frontend/src/app/components/mermaid-graph/mermaid-graph.component.ts/html/scss` — **NEW** reusable wrapper
- `frontend/src/app/components/skill-lineage-tree/skill-lineage-tree.component.ts/html/scss` — **NEW** lineage tree
- `frontend/src/app/services/mermaid-actions.service.ts` — **REFERENCE** for copy/fullscreen actions

> **Note**: `skill-detail.component.html` and `skill-detail.component.ts` are NOT modified in this phase. Integration happens in Phase 6.

## Component Design

### MermaidGraphComponent
```typescript
@Component({
  selector: 'app-mermaid-graph',
  standalone: true,
  imports: [MarkdownModule, MermaidActionsMenuComponent],
  template: `
    <div class="mermaid-graph-container">
      <div class="mermaid-toolbar">
        @if (title()) {
          <span class="title">{{ title() }}</span>
        }
        <app-mermaid-actions-menu [graphSource]="graphSource()" />
      </div>
      <markdown [data]="graphSource()" mermaid></markdown>
    </div>
  `,
})
export class MermaidGraphComponent {
  graphSource = input.required<string>();
  title = input<string>('');
}
```

### SkillLineageTreeComponent
```typescript
@Component({
  selector: 'app-skill-lineage-tree',
  standalone: true,
  imports: [MermaidGraphComponent, MatDialogModule],
  template: `
    @if (graphSource()) {
      <app-mermaid-graph [graphSource]="graphSource()" title="Evolution Lineage" />
      <!-- Fallback navigation list for nodes (S2) -->
      <div class="lineage-legend">
        @for (node of allNodes(); track node.id) {
          <button mat-button (click)="navigateTo.emit(node.id)">
            {{ node.name }} (Gen {{ node.generation }})
          </button>
        }
      </div>
    } @else {
      <p class="no-lineage">This skill has no evolution history.</p>
    }
  `,
})
export class SkillLineageTreeComponent {
  lineage = input.required<SkillLineage>();
  currentSkillId = input.required<string>();

  navigateTo = output<string>();  // emits skill ID when a node is clicked

  graphSource = computed(() => {
    const lin = this.lineage();
    if (!lin || (lin.parents.length === 0 && lin.children.length === 0)) {
      return '';
    }
    return buildLineageGraph(lin, this.currentSkillId());
  });

  allNodes = computed(() => {
    const lin = this.lineage();
    if (!lin) return [];
    return [...lin.parents, ...lin.children];
  });
}
```

### buildLineageGraph function
```typescript
function buildLineageGraph(lineage: SkillLineage, currentSkillId: string): string {
  // 1. Collect all unique nodes (origin, parents, children, current)
  // 2. Assign Mermaid node IDs (sanitized — no spaces/special chars: use `node{index}`)
  // 3. Generate classDef statements for styling (origin, evolved, current, active, etc.)
  // 4. Generate node statements with labels (name + gen badge)
  // 5. Generate edge statements with change_summary labels:
  //    - Truncate to 40 chars
  //    - Quote labels: origin -->|"summary text"| child
  //    - Empty change_summary → label "Auto-evolved"
  // 6. Return complete `graph TD\n  ...` string
}
```

## Constraints
- Mermaid node IDs must be sanitized (no spaces, special chars) — use `node{index}` pattern
- Edge labels with special characters must be quoted: `-->|"summary text"|`
- Large trees (> 20 nodes) must be truncated with "view full" option
- Must work with dark theme (Mermaid configured with `theme: 'dark'`)
- Empty `change_summary` → show "Auto-evolved" as edge label
- Component must handle null/empty lineage gracefully (show empty state message)
- **[S2]** Do NOT use Mermaid's `click` directive — use DOM event listeners on rendered SVG or a fallback legend

## Testing Strategy
- Unit test `buildLineageGraph()`: verify correct Mermaid syntax for various lineage shapes (no parents, multiple children, deep chain, empty change_summary)
- Unit test edge label truncation
- Component test: verify `graphSource` computed signal updates when lineage input changes
- Component test: verify `navigateTo` output emits correct skill ID

## Deliverables
- [ ] `MermaidGraphComponent` created (reusable for future diagrams)
- [ ] `SkillLineageTreeComponent` created with `buildLineageGraph` logic
- [ ] Edge metadata popup/dialog implemented
- [ ] Mermaid nodes styled by status/generation
- [ ] Large tree handling (truncation + fullscreen)
- [ ] **[S2]** Node click via DOM listeners + fallback legend
- [ ] Unit tests for graph builder
- [ ] `ng build` compiles

> **Integration into skill-detail page is deferred to Phase 6.**
