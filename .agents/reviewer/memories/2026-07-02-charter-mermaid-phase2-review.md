# Charter Mermaid Frontend — Phase 2 Review

**Date:** 2026-07-02
**Commit:** 3ab80032 (feature/charter-mermaid-support)
**Verdict:** APPROVED with 1 warning + 2 suggestions

## Key Findings

### 🟡 Warnings
1. **securityLevel: 'loose'** — Mermaid 'loose' allows HTML in diagram labels, bypassing Angular sanitizer.
   Mermaid renders AFTER Angular sanitizes the markdown HTML, so injected HTML from LLM-generated
   diagrams could execute. Low severity (diagrams are agent-generated, not arbitrary user input).
   Default 'strict' would be safer if HTML-in-labels isn't needed.

2. **Perpetual budget warning** — maximumWarning is 1MB but actual bundle is 4.92MB (mermaid is 3.4MB).
   Build always emits "bundle initial exceeded maximum budget" warning. Consider raising to 5-6MB.

### 🟢 Suggestions
1. **[data-mermaid-error] CSS may be dead** — ngx-markdown v21.2.5 doesn't set this attribute on render errors.
   Mermaid.js v11 handles errors differently. Harmless but likely never triggers.

2. **mermaidOptions could use per-component override** — Not needed now but noting that the `mermaidOptions`
   input on `<markdown>` can override global options per-instance if dark/light themes are needed per-message.

## Verified Correct
- provideMarkdown({ mermaidOptions: { provide: MERMAID_OPTIONS, useValue: {...} } }) — correct Angular DI syntax
- `<markdown ... mermaid>` — bare boolean attribute is correct for boolean @Input
- scripts: ["node_modules/mermaid/dist/mermaid.min.js"] — sets global `window.mermaid`, required by library
- Both agentColorMap instances updated with charter: '#3b82f6' (matches agent.json color: "accent-blue")
- Dark theme palette (background #0f172a, text #e2e8f0) matches existing SCSS variables
- Both dev + production builds succeed, no errors

## Build Metrics
- mermaid.min.js: 3.4MB, bundled into scripts-*.js chunk
- Production initial bundle: 4.92MB (was ~1.5MB before mermaid)
- Budget: 1MB warning / 6MB error — build passes, warning fires

## API Verification
Checked ngx-markdown fesm2022 source directly:
- provideMarkdown() spreads mermaidOptions into providers array ✅
- MERMAID_OPTIONS is an InjectionToken ✅
- renderMermaid() checks for global `mermaid` object ✅
- Component has boolean `mermaid` input ✅
