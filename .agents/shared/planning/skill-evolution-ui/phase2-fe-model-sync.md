# Phase 2: Frontend Model Sync

## Objective
Update TypeScript interfaces in `skill.model.ts` to match backend reality, **fix pre-existing field name bugs**, add new interfaces for usage records / A/B test stats / triggers, and add corresponding service methods to `skill.service.ts`. This is the foundation phase — all subsequent FE visualization phases import from these models.

## Coupling
- **Depends on**: Phase 1 (loose — can code against documented contracts before Phase 1 ships)
- **Coupling type**: loose
- **Shared files with other phases**: `skill.model.ts` and `skill.service.ts` are imported by Phases 3, 4, 5
- **Shared APIs/interfaces**: Defines all TypeScript interfaces consumed by Phases 3-5
- **Why this coupling**: Phases 3-5 import these interfaces. Must be completed before visualization components can compile.

## Context
- Phase 1 defines the response shapes (documented in `phase1-be-api.md`)
- Backend already returns fields the FE doesn't type (verified from source)
- `skill.service.ts` is 625 lines with signals-based state management

## CRITICAL: Naming Convention

**[S1] No HTTP interceptor exists.** The frontend uses `provideHttpClient()` with zero arguments (verified in `app.config.ts`). All existing models use **snake_case** matching the backend directly.

**Decision: Use snake_case for ALL interfaces.** The app binds directly to backend field names — no automatic snake_case → camelCase transformation occurs.

## CRITICAL: Pre-Existing Field Name Bugs [C2]

The existing `SkillMetrics` interface has field names that **do not match** what the backend returns. The backend returns `selected`, `applied`, `completions`, `fallbacks` — but the FE interface declares `total_selections`, `total_applied`, `total_completions`, `total_fallbacks`. These are currently `undefined` at runtime against the live API.

**Backend returns** (from `GET /api/skills/{id}/metrics`):
```
total, selected, applied, completions, fallbacks,
avg_iterations, avg_duration,
completion_rate, applied_rate, fallback_rate,
consecutive_failures
```

| FE Field (WRONG) | BE Field (CORRECT) | Status |
|------------------|-------------------|--------|
| `total_selections` | `selected` | 🔴 Currently undefined at runtime |
| `total_applied` | `applied` | 🔴 Currently undefined at runtime |
| `total_completions` | `completions` | 🔴 Currently undefined at runtime |
| `total_fallbacks` | `fallbacks` | 🔴 Currently undefined at runtime |

**These renames must be reflected in `skill-detail.component.html`** where the metrics dashboard tiles bind to `m.total_selections` etc.

## Verified Gaps (from source exploration)

### skill.model.ts — Missing Fields
| Interface | Missing Fields | BE Source |
|-----------|---------------|-----------|
| `Skill` | `auto_load: boolean`, `source_skill_bank_id: string\|null` | `GET /api/skills/{id}` returns these |
| `SkillMetrics` | `total: number`, `avg_iterations: number`, `avg_duration: number` | `GET /api/skills/{id}/metrics` returns these |
| `SkillLineage` | parents/children need edge metadata: `change_summary`, `content_diff` (added in Phase 1 Task 5) | `skill_lineage` table has these fields |
| `SkillBankItem` | `template_version: number`, `agent_id: string`, `auto_load: boolean` | BE returns these |

### skill.model.ts — Missing Interfaces
| Interface | Purpose |
|-----------|---------|
| `SkillUsageRecord` | Individual usage record from `/usage-records` |
| `SkillUsageRecordsResponse` | Paginated response envelope |
| `SkillAbTestStats` | Full A/B comparison stats from `/ab-test/stats` (enriched per Phase 1) |
| `SkillAbTestStatsResponse` | Response envelope for A/B stats |
| `SkillTrigger` | Trigger definition (fields: `condition_type`, `condition_json`, NOT `trigger_type`/`trigger_config`) |
| `SkillLineageNode` | Skill node with edge metadata for lineage tree |

### skill.service.ts — Missing Methods
| Method | Endpoint |
|--------|----------|
| `getUsageRecords(id, limit?, offset?)` | `GET /api/skills/{id}/usage-records` |
| `getAbTestStats(id)` | `GET /api/skills/{id}/ab-test/stats` |
| `listTriggers()` | `GET /api/skills/triggers` |
| `createTrigger(data)` | `POST /api/skills/triggers` |
| `updateTrigger(id, data)` | `PUT /api/skills/triggers/{id}` |
| `deleteTrigger(id)` | `DELETE /api/skills/triggers/{id}` |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `Skill` interface | Add `auto_load: boolean` and `source_skill_bank_id: string\|null` | `frontend/src/app/models/skill.model.ts` |
| 2 | **[C2] Fix `SkillMetrics` field names + add missing fields** | Rename: `total_selections` → `selected`, `total_applied` → `applied`, `total_completions` → `completions`, `total_fallbacks` → `fallbacks`. Add: `total: number`, `avg_iterations: number`, `avg_duration: number`. | `frontend/src/app/models/skill.model.ts` |
| 3 | **[C2] Fix metrics tile bindings in skill-detail template** | Update `skill-detail.component.html` metric tile bindings from `m.total_selections` → `m.selected`, `m.total_applied` → `m.applied`, `m.total_completions` → `m.completions`, `m.total_fallbacks` → `m.fallbacks`. | `frontend/src/app/pages/skills/skill-detail/skill-detail.component.html` |
| 4 | Update `SkillLineage` interface | Change `parents`/`children` from `Skill[]` to `SkillLineageNode[]`. Define `SkillLineageNode` extending `Skill` with `change_summary: string` and `content_diff: string` (edge metadata from parent — added by Phase 1 Task 5). | `frontend/src/app/models/skill.model.ts` |
| 5 | Update `SkillBankItem` interface | Add `template_version: number`, `agent_id: string`, `auto_load: boolean` | `frontend/src/app/models/skill-bank.model.ts` |
| 6 | Add `SkillUsageRecord` interface | Fields: `id, skill_id, project_id, instance_id, agent_id, task_message, selected, applied, task_succeeded, iterations, duration_seconds, fallback, feedback_applied, feedback_note, ab_test_group, superseded, created_at` | `frontend/src/app/models/skill.model.ts` |
| 7 | Add `SkillUsageRecordsResponse` interface | Fields: `skill_id, records: SkillUsageRecord[], total, limit, offset` | `frontend/src/app/models/skill.model.ts` |
| 8 | Add `SkillAbTestStats` interface (enriched) | Fields (from Phase 1 enriched response): `skill_id_a, skill_id_b, completion_rate_a/b, composite_score_a/b, difference, comparisons, sample_size, extension_count, ready_to_resolve, needs_more_data, applied_rate_a/b, fallback_rate_a/b, avg_iterations_a/b, avg_duration_a/b` | `frontend/src/app/models/skill.model.ts` |
| 9 | Add `SkillAbTestStatsResponse` interface | Fields: `skill_id, ab_test_group, stats: SkillAbTestStats\|null` | `frontend/src/app/models/skill.model.ts` |
| 10 | Add `SkillTrigger` interface | **Important**: Backend fields are `condition_type` and `condition_json` — NOT `trigger_type`/`trigger_config`. Fields: `id, project_id, name, condition_type, condition_json: Record<string, unknown>, action, is_enabled, created_at`. Verified from `daemon/repositories/skill/models.py:447-512`. | `frontend/src/app/models/skill.model.ts` |
| 11 | Add service methods | Add `getUsageRecords()`, `getAbTestStats()`, and trigger CRUD methods to `skill.service.ts`. Follow existing patterns (return `Observable<T>`, use HttpClient). | `frontend/src/app/services/skill.service.ts` |
| 12 | **[W4] Write unit tests for new service methods** | For each of the 6 new service methods: mock `HttpClient`, verify correct URL construction + query params, verify response type mapping. Use Angular `HttpTestingController`. | `frontend/src/app/services/skill.service.spec.ts` |
| 13 | **[W4] Write interface contract test for SkillMetrics** | Test that verifies `SkillMetrics` field names match actual backend response keys. Can be a type-level test (compile-time) or a runtime test that deserializes a sample backend response and checks all expected keys are present. | `frontend/src/app/models/skill.model.spec.ts` |

## Key Files
- `frontend/src/app/models/skill.model.ts` — Primary work (interface updates + fixes + new interfaces)
- `frontend/src/app/models/skill-bank.model.ts` — Update `SkillBankItem`
- `frontend/src/app/services/skill.service.ts` — Add 6 new methods
- `frontend/src/app/pages/skills/skill-detail/skill-detail.component.html` — Fix metric tile bindings
- `frontend/src/app/services/skill.service.spec.ts` — **NEW** service method tests
- `frontend/src/app/models/skill.model.spec.ts` — **NEW** interface contract test

## Interface Definitions (ready to implement)

```typescript
// === CRITICAL FIXES TO EXISTING ===

export interface Skill {
  // ... existing fields ...
  auto_load: boolean;
  source_skill_bank_id: string | null;
}

// [C2] Fixed field names — match backend exactly
export interface SkillMetrics {
  total: number;              // NEW
  selected: number;           // RENAMED from total_selections
  applied: number;            // RENAMED from total_applied
  completions: number;        // RENAMED from total_completions
  fallbacks: number;          // RENAMED from total_fallbacks
  avg_iterations: number;     // NEW
  avg_duration: number;       // NEW
  completion_rate: number;    // 0-1
  applied_rate: number;       // 0-1
  fallback_rate: number;      // 0-1
  consecutive_failures: number;
}

export interface SkillLineageNode extends Skill {
  change_summary: string;   // edge metadata: what changed from parent
  content_diff: string;     // edge metadata: diff content
}

export interface SkillLineage {
  parents: SkillLineageNode[];
  children: SkillLineageNode[];
  generation: number;
  origin: string;
}

// === NEW INTERFACES ===

export interface SkillUsageRecord {
  id: string;
  skill_id: string;
  project_id: string | null;
  instance_id: string;
  agent_id: string;
  task_message: string;
  selected: boolean;
  applied: boolean;
  task_succeeded: boolean;
  iterations: number;
  duration_seconds: number;
  fallback: boolean;
  feedback_applied: boolean | null;
  feedback_note: string | null;
  ab_test_group: string | null;
  superseded: boolean;
  created_at: string;
}

export interface SkillUsageRecordsResponse {
  skill_id: string;
  records: SkillUsageRecord[];
  total: number;
  limit: number;
  offset: number;
}

// [W2+W3] Enriched with per-variant metrics + sample_size
export interface SkillAbTestStats {
  skill_id_a: string | null;
  skill_id_b: string | null;
  completion_rate_a: number;
  completion_rate_b: number;
  composite_score_a: number;
  composite_score_b: number;
  difference: number;
  comparisons: number;
  sample_size: number;          // [W3] configurable, NOT hardcoded
  extension_count: number;
  ready_to_resolve: boolean;
  needs_more_data: boolean;
  applied_rate_a: number;       // [W2] per-variant
  applied_rate_b: number;
  fallback_rate_a: number;
  fallback_rate_b: number;
  avg_iterations_a: number;
  avg_iterations_b: number;
  avg_duration_a: number;
  avg_duration_b: number;
}

export interface SkillAbTestStatsResponse {
  skill_id: string;
  ab_test_group: string;
  stats: SkillAbTestStats | null;
}

// [S3] Trigger interface — uses condition_type/condition_json, NOT trigger_type/trigger_config
export interface SkillTrigger {
  id: string;
  project_id: string | null;   // null = global trigger
  name: string;
  condition_type: string;      // e.g. 'low_completion_rate', 'periodic_scan'
  condition_json: Record<string, unknown>;  // free-form dict, schema varies by condition_type
  action: string;              // e.g. 'analyze', 'evolve_fix'
  is_enabled: boolean;
  created_at: string;
}
```

## Constraints
- All interfaces use **snake_case** (no interceptor, direct BE binding — [S1])
- Service methods must return `Observable<T>` (RxJS pattern, consistent with existing methods)
- `SkillMetrics` field renames are **breaking changes** for existing template bindings — must update `skill-detail.component.html` in the same PR
- Do not break existing components that import from these models (additive changes except for the C2 metric rename)

## Testing Strategy [W4]

### Service Method Unit Tests (`skill.service.spec.ts`)
For each of the 6 new methods:
1. Mock `HttpTestingController` to intercept the request
2. Verify correct URL and HTTP method
3. Verify query parameters (for `getUsageRecords`: `limit`, `offset`)
4. Verify response type mapping (return type matches interface)
5. Verify error handling (404, 500)

### Interface Contract Test (`skill.model.spec.ts`)
1. Define a sample raw backend response for `SkillMetrics` (matching the real field names: `selected`, `applied`, `completions`, `fallbacks`, `total`, `avg_iterations`, `avg_duration`)
2. Deserialize it as `SkillMetrics`
3. Assert all expected keys exist and have correct types
4. **This test prevents the C2 bug from recurring** — if someone renames a field in the interface, this test fails

### Compilation Check
- `ng build` compiles without errors
- Existing skills pages still render correctly (no runtime errors from undefined metric fields)

## Deliverables
- [ ] `Skill` interface updated with `auto_load`, `source_skill_bank_id`
- [ ] **[C2]** `SkillMetrics` field names fixed (`selected`, `applied`, `completions`, `fallbacks`) + `total`, `avg_iterations`, `avg_duration` added
- [ ] **[C2]** `skill-detail.component.html` metric tile bindings updated
- [ ] `SkillLineage` updated to use `SkillLineageNode[]` with edge metadata
- [ ] `SkillBankItem` updated with missing fields
- [ ] `SkillUsageRecord` + `SkillUsageRecordsResponse` interfaces added
- [ ] `SkillAbTestStats` + `SkillAbTestStatsResponse` interfaces added (with enriched fields)
- [ ] `SkillTrigger` interface added (with correct `condition_type`/`condition_json` field names)
- [ ] 6 new service methods added to `skill.service.ts`
- [ ] **[W4]** Unit tests for all 6 new service methods
- [ ] **[W4]** Interface contract test for `SkillMetrics`
- [ ] `ng build` compiles without errors
- [ ] Existing skills pages still render correctly (metrics show real values now, not undefined)
