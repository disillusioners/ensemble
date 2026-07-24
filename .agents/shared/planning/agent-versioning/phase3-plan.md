# Phase 3: Frontend — Version Picker UI

## Objective

Add a version picker to the agent selector and switcher components so users can select a specific agent version when creating an instance. The chosen `version_tag` is sent to the backend during instance creation and displayed on existing instances.

## Coupling

- **Depends on**: Phase 2 (API contract: `AgentInfo.version_tag`, `AgentInfo.available_versions`, `InstanceCreate.version_tag`, `InstanceInfo.agent_tag`)
- **Coupling type**: loose
- **Shared files with other phases**: `frontend/src/app/models/index.ts` (reads Phase 2 API contract)
- **Shared APIs/interfaces**: `GET /api/agents` response shape, `POST /api/instances` request body
- **Why this coupling**: Phase 3 only needs the API contract (request/response shape). Frontend work can begin once Phase 2 models are defined and frozen, even before Phase 2 DB work is complete (mock the API).

## Context

- Previous phase delivered: API returns `version_tag` + `available_versions` per agent; `POST /api/instances` accepts `version_tag`
- Key decisions: D7 (flat API response, frontend deduplicates by id)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `Agent` TypeScript interface | Add `version_tag?: string \| null` and `available_versions?: (string \| null)[]` to the `Agent` interface. | `frontend/src/app/models/index.ts:110-119` |
| 2 | Update `ApiService.createInstance()` signature | Add optional `versionTag?: string` parameter (4th arg). Include `version_tag` in the POST body when provided. | `frontend/src/app/services/api.service.ts:58-69` |
| 3 | Create `VersionPickerComponent` | New standalone Angular component. Shows a dropdown/select of available versions when `available_versions.length > 1`. Emits the selected tag. Uses Angular signals. Dark theme styled. | `frontend/src/app/components/version-picker/` (new) |
| 4 | Integrate `VersionPickerComponent` into `AgentSelectorComponent` | When a user selects an agent, check `available_versions`. If > 1 version, show the version picker below the agent card. Track selected version tag in a signal. | `frontend/src/app/components/agent-selector/agent-selector.component.ts` + `.html` |
| 5 | Integrate `VersionPickerComponent` into `AgentSwitcherComponent` | When switching agents in the instance list, show version picker if the selected agent has multiple versions. | `frontend/src/app/components/agent-switcher/agent-switcher.component.ts` + `.html` |
| 6 | Deduplicate agents by `id` in components | Both selector and switcher currently display flat agent lists. Add a `computed()` that groups agents by `id`, picking the base version as the primary display entry while preserving `available_versions`. **W8**: Tiebreaker for no-base case is alphabetical by `version_tag`. | Both component `.ts` files |
| 7 | **Thread `version_tag` through all 5 call sites (C3 — CRITICAL)** | Enumerate and update every call site that invokes `createInstance()`. See "C3 Call-Site Enumeration" below. | 5 files (see below) |
| 8 | Display agent version badge on existing instances | Show a version badge on instance cards using `InstanceInfo.agent_tag` (from Phase 2 S9). Badge only shown when `agent_tag` is not null. | Instance list component |
| 9 | Write frontend unit tests | Tests for: VersionPickerComponent renders/emits correctly, agent deduplication logic, **all 5 call sites send version_tag** (C3), createInstance sends version_tag in body, picker hidden for single-version agents. | Component `.spec.ts` files |

## C3 Call-Site Enumeration (CRITICAL)

There are **5 call sites** that invoke `api.createInstance()`. Each currently constructs `agentPath = ./agents/${agent.id}` and calls `createInstance(agentPath)` WITHOUT a version tag. ALL must be updated.

> **Note**: `agentPath` is passed as `agent_id` to the backend (the backend resolves it via `resolve_to_id`). The `version_tag` is a separate parameter.

| # | File | Line | Current Code | Required Change |
|---|------|------|-------------|----------------|
| 1 | `home.component.ts` | 114 | `this.api.createInstance(agentPath)` | Add `selectedVersionTag()` as 4th arg |
| 2 | `home.component.ts` | 170 | `this.api.createInstance(agentPath)` (Mother) | No change needed — Mother has no versions |
| 3 | `home.component.ts` | 187 | `this.api.createInstance(agentPath)` (quick spawn) | Add `agent.version_tag` or selected tag as 4th arg |
| 4 | `chat.component.ts` | 394 | `this.api.createInstance(agentPath, undefined, projectId)` | Add version tag as 4th arg |
| 5 | `instances.component.ts` | 100 | `this.api.createInstance(agentPath, undefined, actualProjectId)` | Add version tag as 4th arg |

### Output Contract Change

The `AgentSelectorComponent` currently emits `createInstance = output<void>()`. This needs to carry the version tag:

**Option A** (recommended): Change output to carry the tag:
```typescript
// agent-selector.component.ts
readonly createInstance = output<{ versionTag?: string }>();

// Emit:
this.createInstance.emit({ versionTag: this.selectedVersionTag() });
```

**Option B**: Add a separate output:
```typescript
readonly createInstance = output<void>();
readonly versionTagChange = output<string | null>();
```

**Recommended: Option A** — fewer outputs, tighter coupling between the create action and its version context.

### Parent Wiring (home.component.ts)

```typescript
// home.component.ts — BEFORE:
protected onCreateInstance(): void {
  const agent = this.selectedAgent();
  if (!agent) return;
  const agentPath = `./agents/${agent.id}`;
  this.api.createInstance(agentPath).subscribe({ ... });
}

// home.component.ts — AFTER:
protected onCreateInstance(payload?: { versionTag?: string }): void {
  const agent = this.selectedAgent();
  if (!agent) return;
  const agentPath = `./agents/${agent.id}`;
  this.api.createInstance(agentPath, undefined, undefined, payload?.versionTag).subscribe({ ... });
}
```

### Template Binding (home.component.html)

```html
<!-- BEFORE -->
<app-agent-selector (createInstance)="onCreateInstance()" />

<!-- AFTER -->
<app-agent-selector (createInstance)="onCreateInstance($event)" />
```

## Key Files

- `frontend/src/app/models/index.ts` — TypeScript interfaces
- `frontend/src/app/services/api.service.ts` — HTTP service
- `frontend/src/app/components/version-picker/version-picker.component.ts` — new component
- `frontend/src/app/components/agent-selector/agent-selector.component.ts` — main agent picker
- `frontend/src/app/components/agent-selector/agent-selector.html` — template
- `frontend/src/app/components/agent-switcher/agent-switcher.component.ts` — dropdown switcher
- `frontend/src/app/components/agent-switcher/agent-switcher.html` — template
- **`frontend/src/app/pages/home/home.component.ts:114,170,187`** — 3 call sites (C3)
- **`frontend/src/app/pages/chat/chat.component.ts:394`** — 1 call site (C3)
- **`frontend/src/app/pages/instances/instances.component.ts:100`** — 1 call site (C3)

## Detailed Implementation Notes

### Task 1: Agent Interface Update

```typescript
export interface Agent {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  version?: string;
  agent_id: string;
  system?: boolean;
  version_tag?: string | null;           // NEW
  available_versions?: (string | null)[]; // NEW
}
```

### Task 2: ApiService.createInstance() Update

```typescript
createInstance(
  agentId: string,
  instanceId?: string,
  projectId?: string,
  versionTag?: string,  // NEW
): Observable<InstanceInfo> {
  const body: Record<string, string> = { agent_id: agentId };
  if (instanceId) body['instance_id'] = instanceId;
  if (projectId) body['project_id'] = projectId;
  if (versionTag) body['version_tag'] = versionTag;  // NEW
  return this.http.post<InstanceInfo>(`${this.API_BASE}/instances`, body);
}
```

### Task 3: VersionPickerComponent (Standalone)

```typescript
@Component({
  selector: 'app-version-picker',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './version-picker.html',
  styleUrl: './version-picker.scss',
})
export class VersionPickerComponent {
  readonly availableVersions = input<(string | null)[]>([]);
  readonly selectedTag = input<string | null>(null);
  readonly tagChange = output<string | null>();

  readonly hasMultipleVersions = computed(() => this.availableVersions().length > 1);

  readonly sortedVersions = computed(() => {
    const versions = [...this.availableVersions()];
    // Sort: null (base) first, then alphabetical
    return versions.sort((a, b) => {
      if (a === null) return -1;
      if (b === null) return 1;
      return a.localeCompare(b);
    });
  });

  onSelect(tag: string | null): void {
    this.tagChange.emit(tag);
  }

  getDisplayLabel(tag: string | null): string {
    return tag === null ? 'Base' : tag;
  }
}
```

### Task 6: Agent Deduplication (computed signal)

> **W8 Fix**: When no base version exists (only tagged dirs), the tiebreaker is alphabetical by `version_tag`.

Add to both `AgentSelectorComponent` and `AgentSwitcherComponent`:

```typescript
/** Deduplicate agents by id, keeping base version as primary, preserving available_versions.
 *  W8: When no base exists, use first alphabetical tagged version. */
readonly deduplicatedAgents = computed(() => {
  const agents = this.selectableAgents();
  const byId = new Map<string, Agent>();
  
  for (const agent of agents) {
    const existing = byId.get(agent.id);
    if (!existing) {
      byId.set(agent.id, agent);
    } else {
      // Prefer base version (version_tag === null or undefined) as primary entry
      const existingIsBase = existing.version_tag === null || existing.version_tag === undefined;
      const agentIsBase = agent.version_tag === null || agent.version_tag === undefined;
      
      if (!existingIsBase && agentIsBase) {
        // Replace tagged with base
        byId.set(agent.id, agent);
      } else if (!existingIsBase && !agentIsBase) {
        // W8: Neither is base — keep the one with alphabetically smaller tag
        if ((agent.version_tag ?? '') < (existing.version_tag ?? '')) {
          byId.set(agent.id, agent);
        }
      }
    }
  }
  
  return Array.from(byId.values()).sort((a, b) => 
    a.name.localeCompare(b.name)
  );
});
```

Then update `filteredAgents` to use `deduplicatedAgents` instead of `selectableAgents`:

```typescript
readonly filteredAgents = computed(() => {
  const query = this.searchQuery().trim().toLowerCase();
  const base = this.deduplicatedAgents();  // CHANGED from selectableAgents()
  if (!query) return base;
  return base.filter(agent => {
    const name = (agent.name ?? '').toLowerCase();
    const desc = (agent.description ?? '').toLowerCase();
    return name.includes(query) || desc.includes(query);
  });
});
```

## Constraints

- Angular 21 with signals (input/computed/effect) — no NgModel two-way binding; use signal outputs.
- Dark theme UI — match existing component styling (see `agent-selector.scss`).
- Version picker must NOT appear for agents with only one version (`available_versions.length <= 1`).
- Must handle `available_versions` containing `null` (base version) gracefully.
- Existing keyboard navigation in selector/switcher must continue to work.
- **C3**: ALL 5 call sites must be updated — no call site can be missed.
- **W8**: Dedup tiebreaker is alphabetical by `version_tag` when no base exists.

## Deliverables

- [ ] `Agent` interface updated with `version_tag` + `available_versions`
- [ ] `ApiService.createInstance()` accepts and sends `versionTag` (4th arg)
- [ ] `VersionPickerComponent` created with dropdown UI
- [ ] Agent selector shows version picker for multi-version agents
- [ ] Agent switcher shows version picker for multi-version agents
- [ ] Agents deduplicated by id in both components (W8 tiebreaker)
- [ ] **ALL 5 call sites thread `version_tag` (C3)**:
  - [ ] `home.component.ts:114` — main create
  - [ ] `home.component.ts:170` — Mother (no change, no versions)
  - [ ] `home.component.ts:187` — quick spawn
  - [ ] `chat.component.ts:394` — chat-based spawn
  - [ ] `instances.component.ts:100` — instance list spawn
- [ ] Version badge on instance cards (using `agent_tag` from InstanceInfo)
- [ ] Frontend unit tests pass (including call-site verification)
