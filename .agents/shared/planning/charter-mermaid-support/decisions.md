# Architecture Decisions: Charter + Mermaid Support

## Decision Record

> **Revision 3 (2026-07-02)**: Updated per approver feedback — B1 test file updates, B2 second agentColorMap, Notes 1-3 corrections.

### D1: Charter Agent — No OpenCode Skill

**Decision**: Charter does NOT get `innate_skills: ["opencode"]`.

**Rationale**: 
- Charter generates text (Mermaid syntax), not code changes
- OpenCode skill adds ~200 lines of prompt content about session management, code delegation, and tool workflows — entirely irrelevant to diagram generation
- Charter needs `bash` (for mmdc validation) and `filesystem` (for temp files) but NOT code-generation sessions
- Lean prompt = better diagram quality (less noise in context window)

**Alternatives Considered**:
- **Give charter opencode**: Overkill — charter would have opencode sessions available but never use them for diagram generation. Adds prompt bloat and unnecessary tool permissions.
- **Give charter a dedicated "chart" tool category with custom tools**: Over-engineered — the charter's job is LLM text generation + subprocess validation, not tool-based operations. Bash + filesystem is sufficient.

**Impact**: Charter's system prompt stays focused on Mermaid syntax and validation.

---

### D2: Mermaid Validation Strategy — mmdc (mermaid-cli) via subprocess

**Decision**: Charter validates all Mermaid output using `npx -y @mermaid-js/mermaid-cli` (mmdc).

**Rationale**:
- mmdc is the official Mermaid CLI tool that renders diagrams — if it renders, the syntax is valid
- Uses the exact same mermaid.js library the frontend uses, ensuring "validates = renders"
- `npx -y` auto-downloads on first use, no global install needed
- Charter writes Mermaid to a per-instance temp file (see C4 fix), runs `mmdc -i file.mmd -o /dev/null`, checks exit code

**S4 Fix — npx availability check**: Charter checks if `npx` is available at the start of validation (Step 0 in workflow). If not, it returns the diagram with a warning that validation was skipped, rather than failing silently.

**C4 Fix — per-instance temp files**: Validation uses `mktemp /tmp/charter_XXXXXX.mmd` instead of hardcoded paths. This prevents race conditions when multiple charter instances run concurrently.

**Alternatives Considered**:
| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **mmdc (mermaid-cli)** via subprocess | Official tool, exact same renderer as frontend, authoritative | Requires Node.js + Chromium (puppeteer) in environment | **CHOSEN** — most reliable validation |
| Python `mermaid-py` package | Pure Python, no Chromium dependency | Unofficial, may lag behind mermaid.js versions, validation may not match frontend renderer | Rejected — unreliable |
| Custom regex-based syntax validator | No external dependencies, fast | Cannot catch all syntax errors, high false-positive/negative rate, must maintain rules for every diagram type | Rejected — brittle |
| Headless browser + mermaid.js via Python | Same renderer as frontend | Heavy dependency, complex setup, fragile | Rejected — over-engineered |

**Risk**: Chromium dependency. Mitigation: Charter's workflow includes npx availability check (Step 0). If npx/mmdc unavailable, return diagram with warning.

---

### D3: Chart Skill — Requires Instance Tool Category Mapping (C1 FIX)

**Decision**: The "chart" innate skill REQUIRES an entry in `INNATE_SKILL_TOOL_CATEGORIES` mapping `"chart" → ["instance"]`.

**Rationale** (revised per C1):
- **Original assumption (WRONG)**: "All agents receiving the chart skill already have instance tools"
- **Reality**: The target agents (developer, planner, reviewer, tidier, approver, tester) have `tools.allow: ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"]` — **`"instance"` is NOT present**
- The chart skill tells agents to use `spawn_instance` and `send_message`, which are in the `"instance"` tool category
- Without the `"chart" → ["instance"]` mapping in `INNATE_SKILL_TOOL_CATEGORIES`, agents with the chart skill would get the instructional prompt but would be UNABLE to call `spawn_instance`
- The `expand_allow_for_innate_skills()` function (instance.py:57-87) merges these categories into the agent's allow list automatically

**Implementation**: Add to `daemon/tools/instance.py:52-54`:
```python
INNATE_SKILL_TOOL_CATEGORIES: dict[str, list[str]] = {
    "opencode": ["external_opencode"],
    "chart": ["instance"],
}
```

**Impact**: This is a daemon code change (1 file). The chart skill is BOTH instructional (prompt via skill.md) AND a tool-permission expansion (instance tools auto-granted via the mapping).

---

### D4: Frontend Mermaid — ngx-markdown Built-in Support + angular.json Script (C2 FIX)

**Decision**: Use ngx-markdown's first-class mermaid support AND register mermaid as a global script in angular.json.

**Rationale**:
- ngx-markdown 21.2.0 has native mermaid support with:
  - `MarkdownComponent.mermaid` boolean input
  - `MarkdownComponent.mermaidOptions` input  
  - `provideMarkdown({ mermaidOptions: ... })` global config
  - Automatic detection of ` ```mermaid ` fenced blocks
  - Internal `renderMermaid()` method that handles the mermaid.js lifecycle
- **C2 Fix**: ngx-markdown expects `window.mermaid` to be available at runtime. The mermaid library MUST be registered in `angular.json` scripts array as `node_modules/mermaid/dist/mermaid.min.js`. Without this, enabling the `mermaid` attribute throws `errorMermaidNotLoaded`.
- This is the official, supported integration path — less code, fewer bugs, library-maintained
- Custom interceptors would need to: parse the DOM, find `code.language-mermaid` elements, extract text, call `mermaid.render()`, replace the element, handle re-renders on content changes — all of which ngx-markdown already does

**Alternatives Considered**:
| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **ngx-markdown built-in + angular.json script** | Zero custom code, library-maintained, automatic lifecycle, proper script loading | None significant | **CHOSEN** |
| ngx-markdown built-in WITHOUT angular.json script | Less config | Throws `errorMermaidNotLoaded` at runtime — mermaid not on window | Rejected (C2 bug) |
| Custom AfterViewChecked interceptor | Full control over rendering pipeline | Reimplements what ngx-markdown already does, fragile to library updates, must handle streaming re-renders | Rejected |
| Post-processing with marked extensions | Can customize at parse time | Complex, mermaid rendering still needs DOM access after parse | Rejected |

---

### D5: Which Agents Get "chart" Skill and "charter" in team_members

**Decision**: 
- **chart skill**: developer, planner, reviewer, tidier, approver, tester (all agents with opencode skill)
- **charter in team_members**: leader + developer, planner, reviewer, tidier, approver, tester

**Rationale**:
- Agents with opencode skill are the "worker" agents that produce deliverables where diagrams are useful (code reviews with architecture diagrams, plans with flowcharts, test results with state diagrams)
- Leader gets charter in team_members so it can directly spawn charter for high-level visualizations
- Leaf agents (explorer, experiencer, gaia, kb-importer) don't produce deliverables needing diagrams

**W3 Fix — Giter resolution (definitive)**: **Giter does NOT get charter or chart skill.** Giter lacks the opencode skill (`innate_skills: []`), focuses on git operations which rarely need diagrams. Leader can still spawn charter directly if needed for a git workflow visualization. This is a firm decision — no ambiguity.

**Agents NOT getting chart skill**: giter, devops, explorer, experiencer, gaia, kb-importer, jober

**Agents NOT getting charter in team_members**: same exclusions as above

---

### D6: Charter Agent Color — accent-blue (C3 FIX)

**Decision**: Charter uses `"color": "accent-blue"` (#3b82f6).

**Rationale** (revised per C3 + Note 2):
- **Original choice (WRONG)**: `accent-emerald` — COLLIDES with approver (approver already uses `accent-emerald`)
- **New choice**: `accent-blue` (#3b82f6) is defined in the frontend palette (`agent-switcher.component.ts:14`) and is not used by any existing agent
- **Note 2 correction**: `accent-blue` IS used as a SCSS color variable in several non-agent components (schedule-card, source-list, migration, etc.) and is available as a selectable color in add-agent-modal. However, NO existing agent uses `accent-blue` as its agent color — so there is no agent-color collision. The plan's original claim of "completely unused" was imprecise; the corrected claim is "not used by any existing agent."
- Blue is visually distinct from all existing agent colors (amber=leader, cyan=developer, violet=giter, indigo=planner, rose=reviewer, purple=tidier, emerald=approver, green=tester, orange=devops)
- Blue evokes "data/charts/analytics" semantics

**Existing agent color assignments** (verified):
| Agent | Color | Hex |
|-------|-------|-----|
| leader | accent-amber | #f59e0b |
| developer | accent-cyan | #10a7f7 |
| planner | accent-indigo | #6366f1 |
| reviewer | accent-rose | #f43f5e |
| tidier | accent-purple | #a855f7 |
| approver | accent-emerald | #10b981 |
| tester | accent-green | #22c55e |
| giter | accent-violet | #8b5cf6 |
| devops | accent-orange | — |
| **charter** | **accent-blue** | **#3b82f6** |

**S2 + B2 Fix**: Charter's color (#3b82f6) must be added to BOTH `agentColorMap` instances:
1. `chat-interface.component.ts` (controls chat message colors)
2. `message-input.component.ts` (controls message input pane colors)

Without updating both, charter messages would show correct blue in the chat area but wrong fallback cyan (#10a7f7) in the input area.

---

### D7: Mermaid Theme — Dark with Custom ThemeVariables (Note 1 FIX)

**Decision**: Configure mermaid with `theme: 'dark'` and custom `themeVariables` matching the app's dark palette. Do NOT rely on `darkMode` alone.

**Rationale**:
- The app is dark-only (`color-scheme: dark`, background `#0f172a`)
- Default mermaid renders light backgrounds — unreadable on dark UI
- Custom themeVariables ensure diagram colors match the app palette:
  - `background: #0f172a` (app background)
  - `primaryColor: #1e293b` (card background)
  - `primaryTextColor: #e2e8f0` (text color)
  - `lineColor: #64748b` (border/line color)

**Note 1 correction**: The previous revision claimed `darkMode` is "NOT a valid mermaid.js runtime config option." This was **factually incorrect**. `darkMode` IS a valid field on `MermaidConfig` (`darkMode?: boolean`, confirmed in `ngx-markdown.d.ts:260`). However, we still choose `theme: 'dark'` + `themeVariables` over `darkMode` because `theme: 'dark'` provides full control over the diagram color scheme (background, text, borders), whereas `darkMode` alone is insufficient for proper dark rendering. The chosen approach is correct; the factual claim has been corrected.

**Alternatives Considered**:
- Mermaid built-in `dark` theme without custom variables: Works but colors don't match app palette. Custom variables ensure visual consistency.
- CSS-only overrides (override mermaid SVG styles): Fragile, breaks on mermaid updates, doesn't affect SVG-internal colors. Theme variables are the proper API.

---

### D8: Streaming Behavior — Acceptable Degradation

**Decision**: During message streaming, partial ` ```mermaid ` blocks render as raw code text until the closing ` ``` ` arrives. This is acceptable.

**Rationale**:
- Mermaid syntax is only complete and valid when the closing fence arrives
- ngx-markdown re-renders on `data` input changes via `ngOnChanges`
- Once the closing fence arrives, ngx-markdown's mermaid handler kicks in and renders the diagram
- The brief raw-text period during streaming is not a bug — it's the correct behavior for incomplete syntax
- Alternative (buffer until complete fence): Would require intercepting ngx-markdown's rendering pipeline — over-engineering for a cosmetic streaming artifact

---

### D9: Phase Execution — Parallel Capable

**Decision**: Phase 1 (backend) and Phase 2 (frontend) can execute in parallel by separate developer instances.

**Rationale**:
- No file overlap (backend: agents/ + daemon/ + tests/; frontend: frontend/)
- No code dependency (frontend renders any ` ```mermaid ` block regardless of which agent produced it)
- No API dependency (the ` ```mermaid ` format is a markdown convention, not an API contract)
- Both phases can be tested independently:
  - Phase 1: Test by spawning charter via API, checking its output + running pytest
  - Phase 2: Test by manually sending a ` ```mermaid ` block in chat + npm run build

---

### D10: Charter no_force_explore (W5 FIX)

**Decision**: Charter has `"no_force_explore": true` in meta.json.

**Rationale**:
- Charter's workflow includes optional context-gathering (Step 2: explore when diagramming existing code)
- Forced explore adds unnecessary latency when charter is just generating a diagram from a description
- Charter can explore manually when needed — it has `knowledge` and `context` tools
- The `no_force_explore` flag prevents the daemon from injecting forced-explore instructions that add latency for cases where charter doesn't need codebase context

---

### D11: Test File Updates Required (B1 FIX)

**Decision**: Both `test_innate_skills_refactoring.py` and `test_spawn_team_members.py` must be updated in Phase 1.

**Rationale**:
- `test_innate_skills_refactoring.py` (lines 57-67, 97): Uses exact `==` assertions on `agent_meta.innate_skills` arrays. After adding "chart" to 6 agents, these assertions hard-fail.
- `test_spawn_team_members.py` (lines 158-161, 224-247, 253-269): Uses exact assertions on `team_members` arrays AND exact string matching on error messages that display `Allowed team members: [...]`. After adding "charter" to leader and 6 agents, these assertions hard-fail.
- These tests MUST be updated in the same phase as the meta.json changes — otherwise the test suite is broken.
- **Sorted display format note**: The `_check_team_membership()` function (line 302) uses `sorted(allowed_canonical)` for error message display. With both "charter" and "explorer" in a team_members list, the sorted output will be `['charter', 'explorer']` (alphabetical). Assertions must match this exact format.

---

### D12: Charter Includes "time" Tool (Note 3 FIX)

**Decision**: Charter includes `"time"` in `tools.allow`.

**Rationale**:
- All 6 reference agents (developer, planner, reviewer, tidier, approver, tester) include `"time"` in their `tools.allow`
- Charter should follow the same convention for consistency
- The `time` tool is harmless and may be useful for timestamped error messages or logging during validation

---

## Summary of All File Changes

### Phase 1 — Backend (15 files)
| Action | File | Change |
|--------|------|--------|
| CREATE | `agents/charter/meta.json` | New agent (color=accent-blue, no_force_explore=true, time in tools) |
| CREATE | `agents/charter/soul.md` | Charter identity |
| CREATE | `agents/charter/rule.md` | Validation rules + per-instance temp file rule (C4) |
| CREATE | `agents/charter/workflow.md` | Workflow with npx check (S4) + mktemp (C4) |
| CREATE | `agents/_prompt_system/innate-skills/chart/skill.md` | Chart innate skill |
| MODIFY | `daemon/tools/instance.py` | [C1] Add `"chart": ["instance"]` to INNATE_SKILL_TOOL_CATEGORIES |
| MODIFY | `agents/leader/meta.json` | Add "charter" to team_members |
| MODIFY | `agents/developer/meta.json` | Add "charter" to team_members, "chart" to innate_skills |
| MODIFY | `agents/planner/meta.json` | Add "charter" to team_members, "chart" to innate_skills |
| MODIFY | `agents/reviewer/meta.json` | Add "charter" to team_members, "chart" to innate_skills |
| MODIFY | `agents/tidier/meta.json` | Add "charter" to team_members, "chart" to innate_skills |
| MODIFY | `agents/approver/meta.json` | Add "charter" to team_members, "chart" to innate_skills |
| MODIFY | `agents/tester/meta.json` | Add "charter" to team_members, "chart" to innate_skills |
| **MODIFY** | **`tests/test_innate_skills_refactoring.py`** | **[B1] Update innate_skills assertions for 6 agents** |
| **MODIFY** | **`tests/test_spawn_team_members.py`** | **[B1] Update team_members assertions for leader + agents** |

### Phase 2 — Frontend (7 files)
| Action | File | Change |
|--------|------|--------|
| MODIFY | `frontend/package.json` | Add mermaid dependency |
| MODIFY | `frontend/angular.json` | [C2] Add mermaid.min.js to scripts array |
| MODIFY | `frontend/src/app/app.config.ts` | Configure mermaidOptions (theme: 'dark' + themeVariables) |
| MODIFY | `frontend/src/app/components/chat-interface/chat-interface.html` | Add `mermaid` attribute |
| MODIFY | `frontend/src/app/components/chat-interface/chat-interface.component.ts` | [S2] Add charter to agentColorMap |
| **MODIFY** | **`frontend/src/app/components/message-input/message-input.component.ts`** | **[B2] Add charter to second agentColorMap** |
| MODIFY | `frontend/src/app/components/chat-interface/chat-interface.scss` | Mermaid dark theme styling |

**Total: 22 files (5 new, 17 modified)**
