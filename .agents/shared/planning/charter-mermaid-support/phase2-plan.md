# Phase 2: Frontend Mermaid Rendering

## Objective

Install Mermaid.js as a dependency, register the mermaid script in angular.json, configure ngx-markdown's built-in mermaid support, enable the `mermaid` attribute on the chat markdown component, add charter's color to BOTH chat component color maps (chat-interface + message-input), and style diagrams for the dark theme so that ` ```mermaid ` code blocks in chat messages render as visual diagrams instead of raw text.

## Coupling

- **Depends on**: None (independent of Phase 1)
- **Coupling type**: independent
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: Consumes the convention that agents emit ` ```mermaid ` fenced code blocks (established by Phase 1's chart skill), but this is a text convention — any ` ```mermaid ` block renders regardless of source
- **Why this coupling**: Frontend rendering is decoupled from backend agent infrastructure. Mermaid blocks render whether they come from the charter agent, manual user input, or any other source.

## Context

### Current Frontend Stack
- **Angular**: 21.2.5 (standalone components, no NgModule, `bootstrapApplication`)
- **ngx-markdown**: 21.2.0 (has built-in mermaid support via optional peer dependency)
- **marked**: 17.0.4
- **Dark theme**: `styles.scss` uses `color-scheme: dark`, background `#0f172a`, text `#e2e8f0`
- **No mermaid installed**: `mermaid` is declared as an optional peer dep of ngx-markdown (`>= 10.6.0 < 12.0.0`) but NOT in `package.json` dependencies
- **angular.json**: Has a `scripts` field in the build options that is currently ABSENT/empty. Must add mermaid.min.js.

### ngx-markdown Mermaid API (from type definitions)
ngx-markdown 21.2.0 has **first-class mermaid support**:
- `MarkdownComponent` has a `mermaid` input (boolean): `<markdown [data]="..." mermaid>`
- `MarkdownComponent` has a `mermaidOptions` input (`MermaidAPI.MermaidConfig`)
- `provideMarkdown()` accepts `mermaidOptions?: TypedProvider<typeof MERMAID_OPTIONS>`
- The library auto-detects ` ```mermaid ` fenced blocks and renders them via the mermaid.js library
- Error if mermaid is enabled but the package/script isn't loaded: `errorMermaidNotLoaded`
- **CRITICAL (C2)**: The mermaid library must be loaded as a global script via `angular.json` scripts array — ngx-markdown expects `mermaid` to be available on `window.mermaid` at runtime

### Duplicate agentColorMap (B2 FIX)
There are TWO identical `agentColorMap` objects with only 4 entries each:
1. `chat-interface.component.ts:8-13` — controls chat message colors
2. `message-input.component.ts:69-74` — controls message input pane colors

Both must be updated with charter's color. Updating only one leaves charter with wrong fallback color in the other pane.

### Key Integration Points
1. **`frontend/package.json`** — Add `mermaid` to dependencies
2. **`frontend/angular.json`** — **[C2 FIX]** Add `node_modules/mermaid/dist/mermaid.min.js` to scripts array
3. **`frontend/src/app/app.config.ts`** — Configure `provideMarkdown()` with mermaid options
4. **`frontend/src/app/components/chat-interface/chat-interface.html`** — Add `mermaid` attribute to `<markdown>` tag
5. **`frontend/src/app/components/chat-interface/chat-interface.component.ts`** — **[S2]** Add charter to `agentColorMap`
6. **`frontend/src/app/components/message-input/message-input.component.ts`** — **[B2 FIX]** Add charter to second `agentColorMap`
7. **`frontend/src/app/components/chat-interface/chat-interface.scss`** — Dark theme overrides for mermaid

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Install mermaid.js | Add `mermaid` (^11.x) to `package.json` dependencies, run `npm install` | `frontend/package.json` |
| 2 | **[C2 FIX]** Register mermaid script in angular.json | Add `"node_modules/mermaid/dist/mermaid.min.js"` to the `architect.build.options.scripts` array. ngx-markdown requires mermaid as a global script — without this, it throws `errorMermaidNotLoaded`. | `frontend/angular.json` |
| 3 | Configure mermaid in provideMarkdown() | Extend `provideMarkdown()` with `mermaidOptions` provider for dark theme (do not use `darkMode` — use `theme: 'dark'` instead; see Note 1) | `frontend/src/app/app.config.ts` |
| 4 | Enable mermaid attribute on markdown component | Add `[mermaid]="true"` to the `<markdown>` tag in chat template | `frontend/src/app/components/chat-interface/chat-interface.html` |
| 5 | **[S2 + B2]** Add charter to BOTH agentColorMaps | Add `'charter': '#3b82f6'` to `agentColorMap` in BOTH `chat-interface.component.ts` AND `message-input.component.ts` so charter's messages display with correct color everywhere | `chat-interface.component.ts`, `message-input.component.ts` |
| 6 | Add mermaid dark theme CSS | Style mermaid containers, override background, handle SVG sizing | `frontend/src/app/components/chat-interface/chat-interface.scss` |
| 7 | Test rendering | Verify mermaid blocks render as diagrams, verify non-mermaid content unaffected, test with streaming messages | Manual + visual |

## Key Files

### Modified Files
- `frontend/package.json` — Add mermaid dependency
- `frontend/angular.json` — Add mermaid.min.js to scripts array (C2 fix)
- `frontend/src/app/app.config.ts` — Configure mermaidOptions in provideMarkdown()
- `frontend/src/app/components/chat-interface/chat-interface.html` — Add mermaid attribute (line ~97)
- `frontend/src/app/components/chat-interface/chat-interface.component.ts` — Add charter to agentColorMap (S2)
- **`frontend/src/app/components/message-input/message-input.component.ts`** — **Add charter to second agentColorMap (B2 fix)**
- `frontend/src/app/components/chat-interface/chat-interface.scss` — Mermaid styling

### Reference Files (No Change)
- `frontend/node_modules/ngx-markdown/types/ngx-markdown.d.ts` — Type definitions showing mermaid API

## Detailed Task Specs

### Task 1: Install Mermaid

Add to `frontend/package.json` dependencies:
```json
"mermaid": "^11.4.0"
```

Then run:
```bash
cd frontend && npm install
```

**Version note**: ngx-markdown peer dep range is `>= 10.6.0 < 12.0.0`. Mermaid 11.x is the latest major version in range. Use `^11.4.0` or latest 11.x.

### Task 2: [C2 FIX] Register Mermaid Script in angular.json

**File**: `frontend/angular.json`

The build options currently have `"styles": ["src/styles.scss"]` but NO `"scripts"` array. ngx-markdown requires mermaid to be loaded as a global script — it expects `window.mermaid` to be available at runtime. Without this, enabling the `mermaid` attribute throws:
> `errorMermaidNotLoaded`: "When using the `mermaid` attribute you *have to* include Mermaid files to `angular.json` or use imports."

**Current `angular.json` build options** (lines 45-57):
```json
"options": {
  "browser": "src/main.ts",
  "tsConfig": "tsconfig.app.json",
  "inlineStyleLanguage": "scss",
  "assets": [
    {
      "glob": "**/*",
      "input": "public"
    }
  ],
  "styles": [
    "src/styles.scss"
  ]
}
```

**Updated — add scripts array**:
```json
"options": {
  "browser": "src/main.ts",
  "tsConfig": "tsconfig.app.json",
  "inlineStyleLanguage": "scss",
  "assets": [
    {
      "glob": "**/*",
      "input": "public"
    }
  ],
  "styles": [
    "src/styles.scss"
  ],
  "scripts": [
    "node_modules/mermaid/dist/mermaid.min.js"
  ]
}
```

### Task 3: Configure provideMarkdown with Mermaid Options

**File**: `frontend/src/app/app.config.ts`

**Current**:
```typescript
import { provideMarkdown } from 'ngx-markdown';
// ...
  providers: [
    // ...
    provideMarkdown()
  ]
```

**Updated — add mermaid configuration with dark theme** (Note 1: `darkMode` is technically valid per `MermaidConfig` type, but `theme: 'dark'` + `themeVariables` is the correct approach for dark mode rendering — `darkMode` alone doesn't control the diagram color scheme):
```typescript
import { provideMarkdown, MERMAID_OPTIONS } from 'ngx-markdown';
// ...

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    provideHttpClient(),
    provideAnimations(),
    provideMarkdown({
      mermaidOptions: {
        provide: MERMAID_OPTIONS,
        useValue: {
          theme: 'dark',
          themeVariables: {
            // Match the app's dark theme palette
            background: '#0f172a',
            primaryColor: '#1e293b',
            primaryTextColor: '#e2e8f0',
            primaryBorderColor: '#475569',
            lineColor: '#64748b',
            secondaryColor: '#334155',
            tertiaryColor: '#1e293b',
            fontFamily: 'Roboto, "Helvetica Neue", sans-serif',
          },
          securityLevel: 'loose',  // Allow click events if needed
        },
      },
    })
  ]
};
```

**Note 1 (corrected)**: `darkMode` IS a valid field on `MermaidConfig` (`darkMode?: boolean`, confirmed in `ngx-markdown.d.ts:260`). However, we use `theme: 'dark'` with explicit `themeVariables` instead because `theme: 'dark'` provides full control over the diagram color scheme (background, text, borders), whereas `darkMode` alone is an insufficient setting for proper dark rendering. The chosen approach is correct.

**Why `theme: 'dark'`**: The app uses `color-scheme: dark` with `background-color: #0f172a`. Default mermaid renders light backgrounds, making text unreadable.

**Why `securityLevel: 'loose'`**: Allows interactive diagrams. `loose` is the standard for development environments. For production, consider `strict` if security is a concern.

### Task 4: Enable Mermaid on Markdown Component

**File**: `frontend/src/app/components/chat-interface/chat-interface.html` (line ~97)

**Current**:
```html
<markdown [data]="message.content || ''"></markdown>
```

**Updated**:
```html
<markdown [data]="message.content || ''" mermaid></markdown>
```

**How it works**: ngx-markdown's `mermaid` input is a boolean. When enabled, the MarkdownService intercepts ` ```mermaid ` fenced code blocks during rendering and replaces them with mermaid-rendered SVG. Non-mermaid code blocks are unaffected.

**Alternative**: If the template-level `mermaid` attribute doesn't pick up the global `mermaidOptions`, bind explicitly:
```html
<markdown [data]="message.content || ''" [mermaid]="true" [mermaidOptions]="mermaidConfig"></markdown>
```
And add to the component:
```typescript
mermaidConfig = {
  theme: 'dark',
  themeVariables: { /* same as app.config.ts */ },
};
```

**Start with the simple `mermaid` attribute** — the global config from `provideMarkdown()` should apply automatically. Only fall back to explicit binding if the global config isn't picked up.

### Task 5: [S2 + B2] Add Charter to BOTH agentColorMaps

**File 1**: `frontend/src/app/components/chat-interface/chat-interface.component.ts` (lines 8-13)

**File 2**: `frontend/src/app/components/message-input/message-input.component.ts` (lines 69-74)

Both files have an identical `agentColorMap` with only 4 entries:
```typescript
agentColorMap: Record<string, string> = {
  'leader': '#f59e0b',
  'developer': '#10a7f7',
  'coder': '#10a7f7',  // backward compat for cached responses
  'reviewer': '#8b5cf6',
};
```

**Updated — add charter to BOTH** (uses accent-blue value #3b82f6):
```typescript
agentColorMap: Record<string, string> = {
  'leader': '#f59e0b',
  'developer': '#10a7f7',
  'coder': '#10a7f7',  // backward compat for cached responses
  'reviewer': '#8b5cf6',
  'charter': '#3b82f6',
};
```

**Why both (B2 fix)**: The chat-interface component controls the color of rendered chat messages. The message-input component controls the color of the input pane (avatar, border). Without updating both, charter messages would show with the correct blue in the chat area but the default cyan (#10a7f7) in the input area — an inconsistent visual experience.

### Task 6: Mermaid Dark Theme CSS

Add to `frontend/src/app/components/chat-interface/chat-interface.scss`:

```scss
// Mermaid diagram styling for dark theme
.mermaid {
  // Ensure SVG diagrams scale properly within message bubbles
  display: flex;
  justify-content: center;
  overflow-x: auto;
  max-width: 100%;
  
  svg {
    max-width: 100%;
    height: auto;
  }
}

// Remove default code block styling from mermaid blocks
// (ngx-markdown would otherwise apply <pre><code> styles)
.message-bubble {
  .mermaid {
    background: transparent;
    border: none;
    padding: 8px 0;
    
    // Error state — mermaid renders an error message when syntax is invalid
    &[data-mermaid-error] {
      color: #f43f5e;
      font-family: 'Courier New', monospace;
      font-size: 0.85em;
      white-space: pre-wrap;
    }
  }
}
```

**Why overflow-x: auto**: Large diagrams (e.g., complex flowcharts) may exceed the message bubble width. Horizontal scroll prevents layout breakage.

### Task 7: Testing Checklist

Run through these verification steps after implementation:

1. **Build check**: `cd frontend && npm run build` — no TypeScript errors
2. **Dev server**: `npm start` — app loads without console errors (especially no `errorMermaidNotLoaded`)
3. **Simple mermaid block**: Send a message with:
   ```
   ```mermaid
   flowchart TD
       A[Start] --> B[End]
   ```
   ```
   Verify it renders as a diagram, not raw code.
4. **Complex diagram**: Test a sequence diagram with multiple participants
5. **Non-mermaid code blocks**: Verify regular ` ```python ` or ` ```bash ` blocks still render with syntax highlighting
6. **Dark theme**: Verify diagram background is dark, text is light/readable
7. **Streaming**: Send a message that streams in a mermaid block — verify it renders after the closing ` ``` ` arrives (partial blocks may show raw text temporarily — acceptable)
8. **Invalid syntax**: Send an intentionally broken mermaid block — verify it shows an error message or raw code, not a broken layout
9. **Charter color (chat area)**: Select charter agent and send a message — verify chat avatar and message bubble use blue (#3b82f6)
10. **Charter color (input area)**: With charter selected, verify the message input pane (avatar, border) also uses blue (#3b82f6) — NOT the default cyan

## Constraints

- **Must register mermaid script in angular.json** (C2 fix) — ngx-markdown requires `window.mermaid` to be available. The script must be in `angular.json` scripts array, not just in package.json.
- **Must use ngx-markdown's built-in mermaid support** — do NOT build a custom code-block interceptor. The library has first-class mermaid support that handles parsing, rendering, and lifecycle.
- **Use `theme: 'dark'` + `themeVariables` for dark mode** (not `darkMode` alone) — `theme: 'dark'` provides full control over the diagram color scheme. `darkMode` is technically valid per the type definition but insufficient on its own.
- **Must update BOTH agentColorMap instances** (B2 fix) — chat-interface.component.ts AND message-input.component.ts.
- **Dark theme is mandatory** — the app is dark-only (`color-scheme: dark`). Light-background diagrams would be unreadable.
- **Bundle size**: Mermaid.js is ~600KB minified. This is an accepted cost for diagram rendering capability. It loads as a global script.
- **Mermaid version**: Must be `>= 10.6.0 < 12.0.0` per ngx-markdown's peer dependency constraint.
- **No backend changes**: This phase is frontend-only. No daemon, Python, or agent changes.

## Deliverables

- [ ] `mermaid` added to `frontend/package.json` dependencies
- [ ] `npm install` completed successfully
- [ ] **`frontend/angular.json` updated** — mermaid.min.js added to scripts array (C2 fix)
- [ ] `provideMarkdown()` configured with dark-theme mermaidOptions in `app.config.ts`
- [ ] `mermaid` attribute added to `<markdown>` in `chat-interface.html`
- [ ] **charter added to `agentColorMap` in `chat-interface.component.ts`** (S2)
- [ ] **charter added to `agentColorMap` in `message-input.component.ts`** (B2 fix)
- [ ] Mermaid dark theme CSS added to `chat-interface.scss`
- [ ] `npm run build` succeeds with no errors
- [ ] ` ```mermaid ` code blocks render as visual diagrams in chat UI
- [ ] Non-mermaid markdown rendering is unaffected
- [ ] Diagrams use dark theme matching app palette
- [ ] No `errorMermaidNotLoaded` console errors
- [ ] Charter color correct in BOTH chat area and input area
