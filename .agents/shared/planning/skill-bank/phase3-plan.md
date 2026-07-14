# Phase 3: Frontend Skill Bank Page

## Objective

Build the full frontend for the Skill Bank: a model, a service, a route (`/skills/bank`), a standalone list page with inline create/edit, and a navigation link. The page lets users create, read, update, and delete skills in the bank.

## Coupling

- **Depends on:** Phase 2 (needs the `/api/skill-bank/*` REST contract)
- **Coupling type:** **loose** — the frontend depends only on the API request/response shapes, not Phase 2's Python code. Can be built against a mock backend or the live API.
- **Shared files with other phases:** None
- **Shared APIs/interfaces:** The REST contract defined in Phase 2 (see API Contract Summary below)
- **Why:** Frontend and backend are separate stacks; the only coupling is the HTTP contract.

## Context

- **Previous phase completed:** `/api/skill-bank` REST API is live (or at minimum the contract is agreed).
- **Stack:** Angular 21 standalone components, Angular Material (M3 dark theme), Angular Signals.
- **Pattern to follow:** The existing `SkillsComponent` + `SkillService` + `skill.model.ts` are the closest analog. The Skill Bank page is simpler: no A/B testing, no metrics, no lineage, no search — just CRUD.

### Pattern References (verified from source)

- **Model:** `frontend/src/app/models/skill.model.ts` — `export interface X { ... }` plain TS types.
- **Service:** `frontend/src/app/services/skill.service.ts` — `@Injectable({providedIn:'root'})`, `inject(HttpClient)`, signals (`readonly skills = signal([])`), `Observable` return + `tap`/`catchError`/`finalize`.
- **Component:** `frontend/src/app/pages/skills/skills.component.ts` — `@Component({standalone:true, imports:[...]})`, Angular Material modules, `signal`/`computed`/`inject`.
- **Route:** `frontend/src/app/app.routes.ts` — lazy-loaded standalone: `{ path: 'X', loadComponent: () => import('...').then(m => m.Y) }`.
- **Nav:** `frontend/src/app/app.html` — `<a routerLink="..." routerLinkActive="active" class="nav-link">`.

### API Contract (from Phase 2)

| Method | Path | Success Response | Error Statuses |
|--------|------|-----------------|----------------|
| GET | `/api/skill-bank?project_id=X&category=Y` | `{items: SkillBankItem[], total: number}` (200) | — |
| POST | `/api/skill-bank` | `SkillBankItem` (201) | 422 (empty name/content), 503 (write paused) |
| GET | `/api/skill-bank/{id}` | `SkillBankItem` (200) | 404 (not found) |
| PUT | `/api/skill-bank/{id}` | `SkillBankItem` (200) | 400 (no fields), 404, 503 |
| DELETE | `/api/skill-bank/{id}` | `{deleted: true}` (200) | 404, 503 (write paused) |

> **Frontend note:** The frontend service should handle 422 (validation error) and 503 (write paused) gracefully — display a user-friendly error via snackbar. These error codes come from Pydantic validation and the `is_write_paused` guard respectively.

```typescript
// SkillBankItem shape (matches backend SkillBankItemResponse)
{
  id: string;
  project_id: string | null;
  name: string;
  description: string;
  content: string;
  category: string;
  created_at: string;
  updated_at: string;
}
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `SkillBankItem` model | TS interfaces: `SkillBankItem`, `SkillBankItemCreate`, `SkillBankItemUpdate`, `SkillBankFilters`. Helper: `SKILL_BANK_CATEGORIES` const (can reuse existing `SKILL_CATEGORIES` from skill.model.ts). | `frontend/src/app/models/skill-bank.model.ts` |
| 2 | Create `SkillBankService` | `@Injectable({providedIn:'root'})`, `inject(HttpClient)`, `API_BASE = '/api/skill-bank'`. Signals: `items`, `loading`, `error`. Methods: `list(filters?)`, `create(data)`, `update(id, data)`, `delete(id)`. Follow `skill.service.ts` pattern (tap to update signals, catchError, finalize). | `frontend/src/app/services/skill-bank.service.ts` |
| 3 | Add route | Add `{ path: 'skills/bank', loadComponent: ... }` in `app.routes.ts`. **CRITICAL:** place this route BEFORE `{ path: 'skills/:id', ... }` so Angular doesn't match `bank` as an `:id` parameter. | `frontend/src/app/app.routes.ts` |
| 4 | Create page component | Standalone component with Angular Material. Layout: filter bar (category dropdown + search), card list, inline create/edit form (or dialog). Uses `SkillBankService` signals. Snackbar for feedback. | `frontend/src/app/pages/skill-bank/skill-bank.component.ts` + `.html` + `.scss` |
| 5 | Add nav link | Add "Bank" sub-link under Skills in the header nav. Use a mat-menu or a secondary link next to the Skills nav item. | `frontend/src/app/app.html` |

## Key Files

- `frontend/src/app/models/skill-bank.model.ts` — **NEW** model
- `frontend/src/app/services/skill-bank.service.ts` — **NEW** service
- `frontend/src/app/pages/skill-bank/skill-bank.component.ts` — **NEW** component
- `frontend/src/app/pages/skill-bank/skill-bank.component.html` — **NEW** template
- `frontend/src/app/pages/skill-bank/skill-bank.component.scss` — **NEW** styles
- `frontend/src/app/app.routes.ts` — Add route (before `/skills/:id`)
- `frontend/src/app/app.html` — Add nav link

## Detailed Specs

### Model (Task 1) — `skill-bank.model.ts`

```typescript
/** Skill Bank item — a user-managed skill template. */

export interface SkillBankItem {
  id: string;
  project_id: string | null;
  name: string;
  description: string;
  content: string;
  category: string;
  created_at: string;
  updated_at: string;
}

export interface SkillBankItemCreate {
  name: string;
  content: string;
  project_id?: string | null;
  description?: string;
  category?: string;
}

export interface SkillBankItemUpdate {
  name?: string;
  content?: string;
  description?: string;
  category?: string;
  project_id?: string | null;
}

export interface SkillBankFilters {
  project_id?: string;
  category?: string;
}

export interface SkillBankListResponse {
  items: SkillBankItem[];
  total: number;
}

// Reuse categories from the existing skill model
export { SKILL_CATEGORIES } from './skill.model';
```

### Service (Task 2) — `skill-bank.service.ts`

```typescript
@Injectable({ providedIn: 'root' })
export class SkillBankService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/skill-bank';

  readonly items = signal<SkillBankItem[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  list(filters?: SkillBankFilters): Observable<SkillBankItem[]> {
    // Build HttpParams from filters, GET API_BASE,
    // map {items, total} → items[], tap into signal.
    // On error: set error signal, return of([]).
  }

  create(data: SkillBankItemCreate): Observable<SkillBankItem> {
    // POST API_BASE, tap → prepend to items signal.
  }

  update(id: string, data: SkillBankItemUpdate): Observable<SkillBankItem> {
    // PUT API_BASE/id, tap → replace in items signal.
  }

  delete(id: string): Observable<{ deleted: boolean }> {
    // DELETE API_BASE/id, tap → remove from items signal.
  }

  refresh(filters?: SkillBankFilters): void { /* list().subscribe() */ }
  clearError(): void { this.error.set(null); }
}
```

### Route (Task 3) — `app.routes.ts`

**CRITICAL ORDERING:** The `/skills/bank` route MUST appear before `/skills/:id`:

```typescript
export const routes: Routes = [
  // ... existing routes ...
  { path: 'skills', loadComponent: () => import('./pages/skills/skills.component').then(m => m.SkillsComponent) },
  // ⬇️ Bank route BEFORE the :id route
  { path: 'skills/bank', loadComponent: () => import('./pages/skill-bank/skill-bank.component').then(m => m.SkillBankComponent) },
  // ⬆️ :id route AFTER the static /bank route
  { path: 'skills/:id', loadComponent: () => import('./pages/skills/skill-detail/skill-detail.component').then(m => m.SkillDetailComponent) },
  // ... rest of routes ...
];
```

### Component (Task 4) — `skill-bank.component.ts`

The component mirrors `skills.component.ts` but simplified (no evolution, no metrics, no A/B testing, no lineage):

**Features:**
- Card list of skill bank items (reuse styling conventions from skills page)
- Category filter dropdown (reuse `SKILL_CATEGORIES`)
- Inline create form: name, content (textarea), description, category
- Inline edit (click card → edit mode, or a small edit button)
- Delete with confirmation
- Loading spinner + error snackbar
- Empty state when no items

**Imports (Angular Material):**
```typescript
imports: [
  CommonModule, FormsModule,
  MatButtonModule, MatIconModule, MatProgressSpinnerModule,
  MatChipsModule, MatSelectModule, MatFormFieldModule,
  MatInputModule, MatTooltipModule, MatSnackBarModule,
  MatCardModule, MatDividerModule,
],
```

**Signals:**
```typescript
private readonly service = inject(SkillBankService);
readonly items = this.service.items;
readonly loading = this.service.loading;
// Local signals for form state, selected category, edit mode
```

### Nav Link (Task 5) — `app.html`

Add a "Bank" link. Two options depending on the existing nav structure:

**Option A (simple — sibling link):**
```html
<a routerLink="/skills" routerLinkActive="active" class="nav-link">Skills</a>
<a routerLink="/skills/bank" routerLinkActive="active" class="nav-link">Bank</a>
```

**Option B (mat-menu dropdown under Skills):**
If the nav already uses a mat-menu pattern, add a "Skill Bank" menu item.

> **Recommendation:** Check the current `app.html` structure. The existing Skills link is a flat `<a>`. Option A (sibling link labeled "Bank") is the simplest and matches the existing pattern. Optionally, relabel "Skills" to "Skill Bank" if the evolution-gated Skills page is not primary.

## Constraints

- **Route order** — `/skills/bank` MUST be before `/skills/:id` in `app.routes.ts`.
- **Standalone component** — Angular 21 standalone (no NgModule).
- **Signals** — use Angular Signals for state (not BehaviorSubject/RxJS subjects).
- **Dark theme** — follow existing M3 dark theme CSS custom properties.
- **Per-component SCSS** — styles in the component's `.scss` file, not global.

## Deliverables

- [ ] `SkillBankItem` model in `frontend/src/app/models/skill-bank.model.ts`
- [ ] `SkillBankService` in `frontend/src/app/services/skill-bank.service.ts`
- [ ] `/skills/bank` route added (before `/skills/:id`) in `app.routes.ts`
- [ ] `SkillBankComponent` (ts + html + scss) with list/create/edit/delete
- [ ] Navigation link added in `app.html`
- [ ] Page renders against a live backend (manual smoke test)

---

## Revision History

- Rev 1 (2026-07-13): Initial draft.
- Rev 2 (2026-07-13): Added error status codes (422, 503) to API contract table — frontend should handle these gracefully. No structural changes to Phase 3 (frontend is unaffected by backend service-layer removal).
