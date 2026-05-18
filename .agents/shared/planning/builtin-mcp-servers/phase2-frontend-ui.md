# Phase 2: Frontend — Built-in Server UI

## Objective
Update the Angular frontend to display built-in MCP servers with distinct visual treatment, add delete/update protection, implement dynamic configuration schema-driven forms with `initialValues` pre-fill from the backend's reverse-mapped config, handle all API error states, and provide reset-to-defaults with proper state sync.

## Coupling
- **Depends on**: Phase 1 (Backend Framework)
- **Coupling type**: tight
- **Shared APIs/interfaces**: Consumes `GET /builtin-templates`, `POST /configure-builtin`, `POST /{id}/reset-builtin`; reads `is_builtin`, `config_schema`, `initial_values` from responses
- **Why this coupling**: Frontend needs the new endpoints, response fields, and `initial_values` that Phase 1 provides

---

## Tasks

### 1. Update TypeScript Models
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1.1 | Update `McpServer` interface | Add `is_builtin?: boolean`, `config_schema?: ConfigSchemaField[] | null`, `initial_values?: Record<string, unknown> | null` | `frontend/src/app/models/index.ts` |
| 1.2 | Add `ConfigSchemaField` interface | `{ key: string, label: string, type: 'text' | 'number' | 'boolean' | 'select', description?: string, default?: any, required?: boolean, options?: string[], min?: number, max?: number, section: 'args' | 'env' }` — note: `arg_format` omitted as it is server-side only; frontend does not need it to render forms or submit values | same |
| 1.3 | Add `BuiltinServerTemplate` interface | `{ name: string, description: string, config_schema: ConfigSchemaField[], default_config: Record<string, unknown> }` | same |
| 1.4 | Add `BuiltinTemplateListResponse` interface | `{ templates: BuiltinServerTemplate[] }` | same |
| 1.5 | Add `BuiltinServerConfigure` interface | `{ template_name: string, values: Record<string, unknown> }` | same |

### 2. Update API Service
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 2.1 | Add `templates` signal (public) | `readonly templates = signal<BuiltinServerTemplate[]>([])` — `readonly` (not `protected`) so components can access it, matching existing `servers`/`loading` pattern | `frontend/src/app/services/mcp-server.service.ts` |
| 2.2 | Add `templatesLoading` signal (public) | `readonly templatesLoading = signal<boolean>(false)` | same |
| 2.3 | Add `listTemplates()` method | `GET /api/mcp-servers/builtin-templates`. Sets `templatesLoading` to true before call, false on complete. Updates `templates` signal on success. Shows snackbar error on failure. Returns observable. | same |
| 2.4 | Add `configureBuiltin(request)` | `POST /api/mcp-servers/configure-builtin` → returns `McpServer`. Shows snackbar error on failure. | same |
| 2.5 | Add `resetBuiltin(serverId)` | `POST /api/mcp-servers/{serverId}/reset-builtin` → returns `McpServer`. Shows snackbar error on failure. | same |

### 3. Update Server List Component — Built-in Badge & Protection
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 3.1 | Add computed signals for separation | `builtInServers = computed(() => servers().filter(s => s.is_builtin))`, `userServers = computed(() => servers().filter(s => !s.is_builtin))` | `mcp-server-list.component.ts` |
| 3.2 | Load templates on init | Call `listTemplates()` alongside `listServers()` in `ngOnInit` | same |
| 3.3 | Add name conflict detection | `conflictingNames = computed(() => templates().filter(t => servers().some(s => s.name === t.name && !s.is_builtin)))` | same |
| 3.4 | Add built-in badge | "Built-in" badge with `$accent-violet` styling next to server name | `mcp-server-list.html` |
| 3.5 | Disable delete for built-in | `[disabled]="server.is_builtin"` + tooltip "Built-in servers cannot be deleted" | `mcp-server-list.html` |
| 3.6 | Change edit button for built-in | Label "Configure" instead of "Edit" for built-in servers | `mcp-server-list.html` |
| 3.7 | Add section headers | `── Built-in Servers ──` section above `── Your Servers ──` section | `mcp-server-list.html` |
| 3.8 | Add "Configure Built-in Server" button | Dropdown showing available templates (excluding already-configured ones). Opens configure dialog for selected template. | `mcp-server-list.html` |
| 3.9 | Add name conflict warning | For templates conflicting with user servers: `"Delete or rename your custom server to enable the built-in version."` — shown as a note under the template in the dropdown. | `mcp-server-list.html` |

### 4. Create Dynamic Config Schema Form Component
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 4.1 | Create `config-schema-form` component | New standalone component | `frontend/src/app/components/config-schema-form/` (new) |
| 4.2 | Inputs | `schema: InputSignal<ConfigSchemaField[]>`, `initialValues: InputSignal<Record<string, unknown>>` (pre-fills form with current/existing config values) | `config-schema-form.component.ts` |
| 4.3 | Output | `valuesChange: EventEmitter<Record<string, unknown>>` | same |
| 4.4 | Field rendering by type | `text` → text input, `number` → number input with min/max attributes, `boolean` → toggle switch, `select` → `<select>` dropdown with options | `config-schema-form.component.html` |
| 4.5 | Pre-fill from `initialValues` | On init: for each schema field, check `initialValues[key]` first (current stored value), fall back to `field.default`, fall back to empty. Emits initial values via `valuesChange`. | `config-schema-form.component.ts` |
| 4.6 | Field descriptions | Each field shows `label`, `description` hint, validation hints (min/max, required) | `config-schema-form.component.html` |
| 4.7 | Form validation | Required fields, min/max for numbers, options check for select. Emit validity via `isValid: OutputEmitterRef<boolean>` | `config-schema-form.component.ts` |
| 4.8 | Styling | Match dark theme: `$bg-primary` inputs, `$border-color` borders, `$accent-cyan` focus, `$text-secondary` labels | `config-schema-form.component.scss` |

### 5. Update Server Dialog — Triple Mode
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 5.1 | Extend `DialogData` interface | `interface DialogData { server?: McpServer; template?: BuiltinServerTemplate }` | `mcp-server-dialog.component.ts` |
| 5.2 | Add computed modes | `isEditMode = computed(() => !!data?.server && !data?.server.is_builtin)`, `isBuiltinConfigureMode = computed(() => !!data?.server?.is_builtin)`, `isTemplateMode = computed(() => !!data?.template)` | same |
| 5.3 | Template mode rendering | When `data.template`: show template name/description (read-only), render `config-schema-form` with `schema=template.config_schema` and `initialValues={}` (defaults), hide name/description fields | `mcp-server-dialog.html` |
| 5.4 | Builtin configure mode rendering | When `data.server.is_builtin`: show server name/description (read-only), render `config-schema-form` with `schema=server.config_schema` and `initialValues=server.initial_values`, hide raw JSON textarea | `mcp-server-dialog.html` |
| 5.5 | Add schema form reference | `@ViewChild(ConfigSchemaFormComponent) schemaForm!: ConfigSchemaFormComponent` | `mcp-server-dialog.component.ts` |
| 5.6 | Triple submit handler | (a) `isEditMode` → existing `updateServer` flow, (b) `isBuiltinConfigureMode` → call `configureBuiltin({ template_name: server.name, values })`, (c) `isTemplateMode` → call `configureBuiltin({ template_name: template.name, values })`. All paths: on error → show snackbar with error message. | same |
| 5.7 | Conditional template | `@if (isBuiltinConfigureMode() || isTemplateMode()) { schema form } @else { raw JSON textarea }` | `mcp-server-dialog.html` |

### 6. Reset to Defaults (with State Sync)
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 6.1 | Add reset button in configure mode | In dialog, when `isBuiltinConfigureMode()`, show "Reset to Defaults" button | `mcp-server-dialog.html` |
| 6.2 | Confirm dialog | Before resetting: `confirm("Reset configuration to defaults? Your custom settings will be lost.")` | `mcp-server-dialog.component.ts` |
| 6.3 | Implement reset with state sync | Call `resetBuiltin(server.id)`. On success: (1) update server in local `servers` list signal with returned server data, (2) re-initialize schema form with new `initial_values` from the returned server, (3) show success snackbar "Configuration reset to defaults". On error: show error snackbar. | same |

### 7. Error Handling
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 7.1 | Templates API error | In `listTemplates()`: catch → show snackbar "Failed to load built-in server templates" | `mcp-server.service.ts` or `mcp-server-list.component.ts` |
| 7.2 | Configure API error | In configure flow: catch → show snackbar with error detail from API response | `mcp-server-dialog.component.ts` |
| 7.3 | Reset API error | In reset flow: catch → show snackbar "Failed to reset configuration" | `mcp-server-dialog.component.ts` |
| 7.4 | Templates loading state | Show spinner in templates dropdown while `templatesLoading()` is true; disable dropdown when loading | `mcp-server-list.html` |

### 8. Component Testing
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 8.1 | Test `config-schema-form` rendering | Verify text, number, boolean, select fields render from schema | `config-schema-form.component.spec.ts` |
| 8.2 | Test `config-schema-form` with `initialValues` | Verify pre-fill: `initialValues` takes precedence over defaults | same |
| 8.3 | Test dialog triple mode | Verify correct rendering for edit / builtin-configure / template modes | `mcp-server-dialog.component.spec.ts` |
| 8.4 | Test built-in delete protection | Verify delete button is disabled for built-in servers | `mcp-server-list.component.spec.ts` |

---

## Key Files
- `frontend/src/app/models/index.ts` — Updated interfaces (no `arg_format` in frontend — server-side only)
- `frontend/src/app/services/mcp-server.service.ts` — New API methods + public signals + error handling
- `frontend/src/app/components/mcp-server-list/` — Separation + protection + templates + conflict warnings
- `frontend/src/app/components/mcp-server-dialog/` — Triple mode + reset + error handling
- `frontend/src/app/components/config-schema-form/` — NEW dynamic form with `initialValues` pre-fill

## Data Flow: Edit Existing Built-in Server

```
1. User clicks "Configure" on built-in server card
2. Dialog opens with data: { server: McpServer }
   - server.config_schema = [ { key: "user_agent", ... }, ... ]  (from DB, synced by bootstrap)
   - server.initial_values = { user_agent: "MyBot", ignore_robots_txt: true }  (reverse-mapped by backend)
   - server.config = { transport: "stdio", command: "uvx", args: [...] }  (actual MCP config)
3. Schema form renders fields from config_schema
4. Schema form pre-fills from initial_values (NOT from config — config is lossy)
5. User modifies values → clicks "Save Configuration"
6. Frontend calls POST /configure-builtin { template_name: "webfetch", values: { user_agent: "NewBot" } }
7. Backend: validate → build_config(values) → save to DB
8. Response includes updated server with new initial_values
9. Frontend updates local signal, closes dialog
```

## Visual Design Notes

### Built-in Section Layout
```
MCP Servers              [⟳] [Add Built-in ▾] [+ Add Server]

── Built-in Servers ──────────────────────────────────
┌────────────────────┐
│ WebFetch           │
│ [Built-in] [Active]│
│ [Configure]        │
└────────────────────┘

── Your Servers ───────────────────────────────────────
┌────────────┐  ┌────────────┐
│ My Server  │  │ webfetch   │  ← user-created, name conflict
│ [Active]   │  │ [Active]   │     with built-in template
│ Edit | Del │  │ Edit | Del │
└────────────┘  └────────────┘
```

### Conflict Warning (in dropdown)
```
┌── Add Built-in Server ──────┐
│ ○ WebFetch                  │
│   ⚠ Delete or rename your   │
│     custom server to enable │
│     the built-in version.   │
└─────────────────────────────┘
```

### Configure Dialog (with Reset)
```
┌── Configure: WebFetch ──────────────────┐
│                                          │
│  User Agent                              │
│  ┌─────────────────────────────────────┐ │
│  │ MyBot/2.0  (from initial_values)    │ │
│  └─────────────────────────────────────┘ │
│  Custom User-Agent string for requests   │
│                                          │
│  Ignore robots.txt                       │
│  [● toggle switch] (True from i.v.)      │
│  Bypass robots.txt restrictions          │
│                                          │
│  Proxy URL                               │
│  ┌─────────────────────────────────────┐ │
│  │ (empty)                             │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  [Reset to Defaults]                     │
│         [Cancel]  [Save Configuration]   │
└──────────────────────────────────────────┘
```

## Constraints
- **Match existing dark theme**: Same color variables
- **No breaking changes**: User server flow works identically
- **No `arg_format` in frontend**: This is server-side config generation detail; frontend only deals with values and schemas
- **`initial_values` drives pre-fill**: Frontend never parses stored MCP config directly

## Deliverables
- [ ] Updated TypeScript models (no `arg_format`, no `config_schema_version` in frontend — server-side only)
- [ ] API service with public `readonly` signals, error handling, loading state
- [ ] Server list: separate sections, badges, conflict warnings with actionable copy
- [ ] Delete protection for built-in servers
- [ ] New `config-schema-form` component with `initialValues` input
- [ ] Dialog with triple mode (edit / builtin-configure / template)
- [ ] Reset to defaults with confirmation + state sync (update signal + re-init form)
- [ ] Error handling for all new API calls
- [ ] 4 component tests
