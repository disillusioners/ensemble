# Plan Overview: Charter Agent + Mermaid Chart Support

## Objective

Add a new "charter" agent that generates and validates Mermaid diagrams, create a "chart" innate skill that teaches other agents how to request diagrams, wire up team_members so charter is spawnable (including auto-granting instance tools via skill→tool-category mapping), and integrate Mermaid rendering into the Angular frontend so ` ```mermaid ` code blocks in chat messages render as actual diagrams.

## Scope Assessment

**LARGE** — Two independent components spanning backend (new agent + innate skill + 7 meta.json edits + daemon tool-category mapping + 2 test files) and frontend (npm dependency + angular.json scripts + Angular config + component changes + styling + 2 color maps). Each component is a coherent, self-contained module. Backend and frontend are **independent** (can run in parallel or sequentially).

### Justification
- **Backend**: New agent directory (5 files), new innate skill (1 file), 7 meta.json modifications, 1 Python daemon change (`INNATE_SKILL_TOOL_CATEGORIES`), 2 test file updates (hardcoded assertions on `innate_skills` and `team_members`)
- **Frontend**: 1 npm dependency, 1 angular.json script registration, 1 app config, 3 component files (2 templates + 1 TS color map + 1 message-input color map), 1 SCSS addition
- **Total**: 22 files touched, 2 distinct subsystems, no cross-dependency between phases

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Stack**: Python daemon (LangGraph) + Angular 21.2.5 frontend (ngx-markdown 21.2.0, mermaid optional peer dep)
- **Key files explored**: All agent meta.json files, innate-skills directory, daemon/tools/instance.py, daemon/tools/_tool_registry.py, chat-interface component, message-input component, app.config.ts, package.json, angular.json, ngx-markdown type definitions, test_innate_skills_refactoring.py, test_spawn_team_members.py

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Charter Agent + Chart Skill + Backend Wiring | Create charter agent, chart innate skill, update team_members, add instance tool-category mapping, update tests | None | — (root) | 4-5h |
| 2 | Frontend Mermaid Rendering | Install mermaid, register script in angular.json, configure ngx-markdown, enable in chat component, dark theme, update both color maps | None | independent | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 ↔ Phase 2 | **independent** | Backend defines WHAT mermaid syntax agents produce (convention in skill.md); frontend defines HOW it renders. No code dependency. Phase 2 can proceed without Phase 1 complete — any ` ```mermaid ` block renders regardless of source. |

**Scheduling**: Both phases can run **in parallel** by separate developer instances. No blocking dependency.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Mermaid.js bundle size (~600KB minified) degrades frontend load time | medium | Mermaid is an optional peer dep loaded as a global script. Accept initial load cost since diagram rendering is a core feature. |
| LLM generates invalid Mermaid syntax that fails to render | high | Charter agent validates syntax via `npx mmdc` (mermaid-cli) subprocess before returning. Chart skill instructs agents to use charter for guaranteed-valid output. |
| `mmdc` requires Chromium/puppeteer — may not be available in all environments | medium | Charter uses `npx -y @mermaid-js/mermaid-cli` which auto-downloads. Charter checks if `npx`/mmdc is available at startup and returns diagrams with a warning if validation is skipped. |
| Streaming messages re-render mermaid blocks mid-flight causing flicker | low | ngx-markdown handles re-render via its internal change detection. Mermaid blocks are only complete when the closing ` ``` ` arrives. Partial blocks render as raw code (acceptable degradation). |
| Dark theme makes light-background mermaid diagrams unreadable | medium | Configure `mermaidOptions` with `theme: 'dark'` and custom `themeVariables` in `provideMarkdown()`. |
| Adding "charter" to 7+ agents' team_members is error-prone (miss one) | medium | Enumerate exact agent list in Phase 1 plan. All agents with `innate_skills: ["opencode"]` get charter in team_members. Use checklist. |
| Chart skill agents lack `instance` tool access (they have bash/filesystem/etc but NOT instance) | **high** | **C1 FIX**: Add `"chart": ["instance"]` to `INNATE_SKILL_TOOL_CATEGORIES` in daemon — auto-grants instance tools when chart skill is present. |
| **Test files with hardcoded assertions break after meta.json changes** | **high** | **B1 FIX**: Update `test_innate_skills_refactoring.py` and `test_spawn_team_members.py` with new expected values for `innate_skills` and `team_members`. |

## Success Criteria

- [ ] `charter` agent appears in registry after daemon restart
- [ ] Leader can spawn charter: `spawn_instance(agent_id="charter")` succeeds
- [ ] Any agent with chart skill can spawn charter as child (instance tools auto-granted)
- [ ] Charter generates valid Mermaid syntax (verified via mmdc)
- [ ] Charter uses per-instance temp files for validation (no collision risk)
- [ ] Agents with `chart` skill know to delegate diagram requests to charter
- [ ] ` ```mermaid ` code blocks render as diagrams in the chat UI
- [ ] Mermaid diagrams use dark theme matching the app
- [ ] Charter color displays correctly in chat UI AND message input (accent-blue in both color maps)
- [ ] Existing markdown rendering (non-mermaid) is unaffected
- [ ] Frontend build succeeds with `npm run build`
- [ ] All existing tests pass after meta.json + test updates

## Tracking

- Created: 2026-07-02
- Last Updated: 2026-07-02 (rev 3: B1 test files, B2 second agentColorMap, Note 1-3 corrections)
- Status: draft
