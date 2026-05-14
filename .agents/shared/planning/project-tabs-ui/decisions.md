# Architecture Decisions: Project-Based Tabs

## Decision 1: Real Column vs JSON Query for project_id

**Decision**: Add a real `project_id` column to the `instances` table.

**Alternatives Considered**:
| Option | Pros | Cons |
|--------|------|------|
| **Real column** (chosen) | Indexable, type-safe, simple queries, foreign key support | Requires migration |
| JSON query on `metadata` | No schema change | Not indexable in SQLite, complex queries, fragile |
| Use project `relationships["instances"]` | Already exists | Wrong direction (project→instance, not instance→project); requires join for every query |

**Rationale**: Filtering instances by project is a core operation that will happen every 10 seconds (polling). JSON extraction on every query is inefficient and fragile. A real column with an index is the correct approach.

**Note**: The actual SQLite column name is `metadata` (mapped via `sa_column=Column("metadata", JSON)` at `daemon/repositories/instance/models.py:58`). NOT `instance_metadata`.

---

## Decision 2: Tab State in Service + localStorage (Not URL)

**Decision**: Store tab state in an Angular Signals service, persisted to localStorage with key `ensemble-project-tabs` (matching existing `ensemble-*` prefix pattern).

**Alternatives Considered**:
| Option | Pros | Cons |
|--------|------|------|
| **Signals + localStorage** (chosen) | Simple, aligns with existing patterns, survives refresh | Not deep-linkable |
| URL query params (`?tab=project-1`) | Deep-linkable, shareable URLs | Complex routing, conflicts with existing routes |
| Session storage | Cleared on tab close | Doesn't persist across browser restarts |
| NgRx store | Scalable for large apps | Overkill for this feature; project doesn't use NgRx |

**Rationale**: Tabs are UI chrome, not content identity. Users navigate to instances via URL (`/instances/:id`), not via tab selection. localStorage with `ensemble-project-tabs` key is sufficient and matches project naming conventions.

---

## Decision 3: Custom Tab Bar vs MatTabGroup

**Decision**: Build a custom `ProjectTabBarComponent`.

**Alternatives Considered**:
| Option | Pros | Cons |
|--------|------|------|
| **Custom component** (chosen) | Full control over styling, "+" button, close behavior, dropdown | More code to write |
| Angular Material MatTabGroup | Well-tested, accessible | Doesn't support "+" button pattern, close buttons, or dynamic tab management easily |
| ng-zorro-antd Tabs | Rich features | Library barely used in project; adds dependency weight |

**Rationale**: The tab bar has specific UX requirements (add button, close button, context menu, fixed "All" tab) that don't map cleanly to standard tab components. A custom component gives full control with minimal overhead.

---

## Decision 4: Playwright for E2E

**Decision**: Use Playwright for e2e testing.

**Alternatives Considered**:
| Option | Pros | Cons |
|--------|------|------|
| **Playwright** (chosen) | Modern, Angular-recommended, auto-wait, great TypeScript support | New dependency |
| Cypress | Visual test runner, time-travel debugging | Heavier, Angular integration less native |
| Protractor | Deprecated | Dead project |

**Rationale**: Playwright is the modern standard for e2e testing with Angular. It has first-class TypeScript support, excellent auto-wait mechanics, and can test against both Chromium and Firefox.

**Infrastructure Notes**:
- Backend port: **8088** (not 8000) — see `config.yaml:18`
- Backend entry: **`python -m daemon`** (not `python -m uvicorn main:app`) — see `daemon/__main__.py`
- No `DELETE /api/projects/{id}` — tests must use alternative cleanup (timestamp-prefixed names or direct DB)

---

## Decision 5: Extract InstanceService from Components

**Decision**: Create a dedicated `InstanceService` to centralize instance loading, pagination, and polling lifecycle.

**Alternatives Considered**:
| Option | Pros | Cons |
|--------|------|------|
| **InstanceService** (chosen) | Single source of truth, reusable, clean polling lifecycle, owns pagination | New file to create |
| Keep logic in components | No refactoring needed | Duplicated logic, hard to coordinate polling |
| Extend ApiService | Centralized | Violates SRP; API service should only handle HTTP |

**Rationale**: Instance loading with project filtering, pagination (`append`, `hasMoreInstances`), and polling lifecycle needs to be centralized. The service is a singleton (`providedIn: 'root'`) but components call `startPolling(projectId)` / `stopPolling()` in their own lifecycle hooks, making the polling lifecycle explicit and component-managed.

---

## Decision 6: Tabs on ChatComponent Only (Not HomeComponent)

**Decision**: Project tabs appear only in the ChatComponent sidebar.

**Rationale**: HomeComponent is for agent selection and instance creation — it doesn't display a persistent instance list that needs project filtering. The instance list sidebar lives in ChatComponent, making it the sole tab integration point. This avoids unnecessary complexity and keeps the Home page focused on its primary purpose.
