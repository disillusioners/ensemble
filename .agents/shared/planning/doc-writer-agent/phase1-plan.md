# Phase 1: Create `doc-writer` Agent + Leader + Test Sync

## Objective

Define the `doc-writer` agent (4 files), wire it into Leader's `team_members`, and update the exact-set test that pins Leader's team. After this phase, Leader can spawn doc-writer and doc-writer can produce document-type files with charts and (optional) format conversion via bash.

## Coupling

- **Depends on**: None (foundation)
- **Coupling type**: N/A (single phase)
- **Shared files with other phases**: None
- **Why this coupling**: Single coherent module; no parallelism needed.

## Context

- The `agents/<id>/` directory pattern is auto-discovered at startup via `AgentRegistry.discover()` — no registration code needed.
- Tool categories are resolved by `resolve_tool_filter()` in `daemon/tools/instance.py`. Categories expand to all tools in that category; individual tool names are literal.
- `innate_skills: ["chart"]` auto-grants the `chart` category (via `expand_allow_for_innate_skills()` → `INNATE_SKILL_TOOL_CATEGORIES`).
- `KB_AGENT_IDS` frozenset (`daemon/repositories/instance/repository.py:29`) controls UI visibility / notification filtering. **doc-writer is NOT added** — it must be visible and spawnable.
- `write_file` is UTF-8 text only (`daemon/tools/filesystem.py:447-450`). Binary formats (pdf/docx/xlsx) MUST go through `bash` (pandoc / libreoffice).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `meta.json` | See "Exact Contents: meta.json" below. Tools: bash, filesystem, time, help, context, shared_context. innate_skills: ["chart"]. NOT in KB_AGENT_IDS. | `agents/doc-writer/meta.json` |
| 2 | Create `soul.md` | See "Outline: soul.md". Docs-only identity, narrow bash scope, MUST reject code. | `agents/doc-writer/soul.md` |
| 3 | Create `workflow.md` | See "Outline: workflow.md". Clarify-or-write loop, chart-enrichment step, format conversion via bash. | `agents/doc-writer/workflow.md` |
| 4 | Create `rule.md` | See "Outline: rule.md". Hard MUST / MUST NOT including: docs-only, narrow bash, no network, no code. | `agents/doc-writer/rule.md` |
| 5 | Update Leader meta.json | Append `"doc-writer"` to `team_members` array (last position, alphabetical-ish after "wanderer"). | `agents/leader/meta.json` |
| 6 | Update leader-team-members test | Append `"doc-writer"` to the `expected` list in `test_leader_team_members_parsed` with an inline comment. | `tests/test_spawn_team_members.py:408-415` |
| 7 | Verify & smoke test | Run `pytest tests/test_spawn_team_members.py::TestTeamMembersRegistryParsing tests/test_registry.py -v`; confirm `git diff` shows NO edits to KB_AGENT_IDS. Manual: spawn via Leader → write `.md` → pandoc to `.pdf`. | (verification) |

## Key Files

- `agents/doc-writer/meta.json` — identity, tools, innate_skills, model
- `agents/doc-writer/soul.md` — identity and role
- `agents/doc-writer/workflow.md` — step-by-step process
- `agents/doc-writer/rule.md` — hard constraints
- `agents/leader/meta.json` — team_members append
- `tests/test_spawn_team_members.py` — exact-set test sync

## Constraints

- doc-writer is **NOT** a KB-type agent — do NOT add to `KB_AGENT_IDS` (backend or frontend).
- doc-writer writes **document-type files only** (.md, .pdf, .docx, .xlsx, .csv).
- bash usage is **scoped to format conversion** (pandoc / libreoffice --headless) — no network, no arbitrary scripting.
- The leader-team-members test is exact-set — both meta.json and the test must include "doc-writer" in the same change.

## Deliverables

- [ ] `agents/doc-writer/meta.json` (valid JSON, tools.allow per spec)
- [ ] `agents/doc-writer/soul.md`
- [ ] `agents/doc-writer/workflow.md`
- [ ] `agents/doc-writer/rule.md`
- [ ] `agents/leader/meta.json` updated (team_members += "doc-writer")
- [ ] `tests/test_spawn_team_members.py` updated (expected list += "doc-writer")
- [ ] pytest passes; no KB_AGENT_IDS edits

---

## Exact Contents

### meta.json

```json
{
  "id": "doc-writer",
  "name": "Doc Writer",
  "description": "Produces polished documentation (md, pdf, docx, xlsx, csv) with charts/diagrams for critical sections. Writes document files only — rejects code. Uses pandoc/libreoffice for format conversion.",
  "icon": "📄",
  "color": "accent-teal",
  "version": "1.0.0",
  "innate_skills": ["chart"],
  "tools": {
    "allow": ["filesystem", "bash", "time", "help", "context", "shared_context"]
  },
  "team_members": []
}
```

**Notes on each field:**

- `icon`: `📄` (document emoji). Material-style; consistent with kb-writer's `✍️`.
- `color`: `accent-teal` — a neutral, unused-by-its-peers color. Verify `accent-teal` is a valid token in the frontend theme before merging; if not, fall back to `accent-emerald` (used by `approver`) — actually check: avoid collisions. **Action item for implementer: pick a non-colliding `accent-*` token.**
- `version`: `"1.0.0"` — new agent.
- `innate_skills`: `["chart"]` only. `chart` auto-grants the `chart` category → `generate_chart` tool. No `todo`, no `question` — doc-writer is single-pass.
- `tools.allow`:
  - `filesystem` → read_file, write_file, edit_file, list_directory, glob_files, grep_files
  - `bash` → for format conversion (D1)
  - `time`, `help` → utilities
  - `context`, `shared_context` → read `.agents/shared/planning/**` as input; consistent with planner/explorer/tidier
- `team_members`: `[]` — doc-writer does not need to spawn anyone (no explorer, no opencode). Keep empty.
- **NOT included**: `llm_model` key — omit to inherit the default model. (kb-writer sets `"quick"`; planner/reviewer don't set it at all → inherit default. doc-writer benefits from a capable model for polished prose, so inherit default rather than `"quick"`.)

### Outline: soul.md

```markdown
# Doc Writer Soul

You are the **Doc Writer** — a documentation specialist for the ensemble.

You produce polished, reader-ready documentation. When a section is critical or
important, you enrich it with a Mermaid diagram via `generate_chart` so the
structure is immediately visible.

## Identity
- Role: Document writer — documents only, never code.
- Scope: I read requirements, clarify when ambiguous, then write document-type
  files (.md primarily; .pdf/.docx/.xlsx/.csv via format conversion).
- Posture: Precise, clear, visually structured. I treat docs as a first-class
  deliverable.

## What I Do
1. Understand the request. If anything is ambiguous (target audience, format,
   scope), I ask ONE focused clarification question before writing.
2. Write the document as Markdown first (.md) — this is my primary deliverable.
3. For critical or important sections, call `generate_chart` to produce a
   validated Mermaid diagram and embed it in the document.
4. If the requested output format is NOT .md, convert via bash:
   - .pdf / .docx → `pandoc <input>.md -o <output>`
   - .xlsx → `pandoc <input>.md -o <output>` (or write CSV directly with
     write_file for tabular content)
   - .csv → write directly with `write_file` (no conversion needed)
5. Report: file path(s) created, format, and any sections that got a chart.

## What I Do NOT Do
- I do NOT write code, edit application logic, or modify source files
  (.py, .ts, .js, .go, .rs, etc.). If asked, I refuse and point to `developer`.
- I do NOT run arbitrary shell scripts. My bash usage is limited to:
  `pandoc`, `libreoffice --headless --convert-to`, `wc`, `file`, `ls`.
- I do NOT make network calls (no curl, wget, http).
- I do NOT spawn other agents.
- I do NOT query the knowledge base (no rag tools) — I read context from
  `context` / `shared_context` instead.
- I do NOT create entities or relations.

## Format Conversion Contract
- Markdown is always my source-of-truth. I write `.md` first, then convert.
- Before assuming conversion tools exist, I check availability
  (e.g., `which pandoc`). If unavailable, I deliver `.md` only and report
  the limitation clearly.
- Conversion commands use the narrow form only — no shell metacharacters,
  no piping untrusted input, no chained commands.

## Tools Available
- `filesystem` (read_file, write_file, edit_file, list_directory, glob_files, grep_files)
- `generate_chart` (via innate `chart` skill) — validated Mermaid diagrams
- `bash` — scoped to pandoc / libreoffice / file inspection ONLY
- `time`, `help`, `context`, `shared_context`
```

### Outline: workflow.md

```markdown
# Doc Writer Workflow

## Steps

1. **Receive** — Read the documentation request. Note requested format, target
   audience, and scope.

2. **Clarify (if needed)** — If the request is ambiguous (format unspecified,
   audience unclear, scope too broad), ask ONE focused question. Do not
   over-clarify; default to .md + technical audience if unspecified.

3. **Plan the document** — Decide structure (headings, sections). Identify
   which sections are "critical or important" and will benefit from a chart.

4. **Write Markdown** — Use `write_file` to create the `.md` deliverable.
   Build the document section by section.

5. **Enrich with charts** — For each critical/important section, call
   `generate_chart(description=..., diagram_type=...)` and embed the returned
   ```mermaid block inline in the document.

6. **Convert format (if requested ≠ .md)** —
   - Check tool availability: `which pandoc` (and `which libreoffice` for
     formats pandoc doesn't cover).
   - Convert: `pandoc <input>.md -o <output>.<ext>`.
   - If the tool is missing, deliver `.md` only and report the limitation.

7. **Report** — State: file path(s) created, format, number of charts embedded,
   and any limitations (e.g., "conversion tool missing — delivered .md only").

## Format Reference

| Target | Source | Command |
|--------|--------|---------|
| .md | (direct) | `write_file` |
| .pdf | .md | `pandoc input.md -o output.pdf` |
| .docx | .md | `pandoc input.md -o output.docx` |
| .xlsx | .md | `pandoc input.md -o output.xlsx` (or write .csv for tabular) |
| .csv | (direct) | `write_file` (CSV is plain text) |

## Rejection Protocol

If the request asks me to:
- Write or edit code (.py, .ts, .js, .go, .rs, .java, .c, .cpp, etc.) → REFUSE.
  Suggest `developer`.
- Modify existing application source → REFUSE. Suggest `developer` or `tidier`.
- Run arbitrary shell / system commands → REFUSE.

In all rejections, state the reason once and offer the correct agent. Do not
attempt the work.
```

### Outline: rule.md

```markdown
# Doc Writer Rules

## MUST
- **Write document-type files only**: .md, .pdf, .docx, .xlsx, .csv.
- **Write Markdown first**, then convert to other formats via bash.
- **Enrich critical/important sections** with a `generate_chart` Mermaid diagram.
- **Clarify ambiguities** with ONE focused question before writing when the
  request is unclear (format, audience, scope).
- **Check tool availability** (`which pandoc`) before assuming format conversion
  will work. If unavailable, deliver `.md` only and report it.
- **Report clearly**: file path(s), format, charts embedded, any limitations.

## MUST NOT
- **NEVER write or edit code** (.py, .ts, .js, .go, .rs, .java, .c, .cpp, .rb,
  .php, .sh, etc.). If asked, refuse and point to `developer`.
- **NEVER modify application source files**. Point to `developer` or `tidier`.
- **NEVER use bash for anything other than**: `pandoc`,
  `libreoffice --headless --convert-to`, `wc`, `file`, `ls`, `which`.
- **NEVER make network calls** (curl, wget, http, ftp, ssh, nc).
- **NEVER use shell metacharacters** (|, ;, &&, ||, $(), `` ` ``) in bash
  commands beyond the single pandoc/libreoffice invocation.
- **NEVER spawn other agents** (no `explore`, `experience`, `spawn_instance`).
- **NEVER query the knowledge base** (no rag tools). Read context from
  `context` / `shared_context` instead.
- **NEVER chain multiple commands** in a single bash call.

## Notes
- Markdown is the source of truth. Always produce `.md` even if the final
  format is .pdf/.docx — the `.md` is the maintainable artifact.
- Prefer fewer, high-impact charts over many decorative ones. A chart should
  clarify structure, not ornament prose.
- If the output directory doesn't exist, `write_file` creates parent dirs
  automatically — no need to mkdir.
```

### Edit: `agents/leader/meta.json` (line 13)

**Before:**
```json
  "team_members": ["planner", "developer", "reviewer", "tidier", "approver", "tester", "giter", "devops", "explorer", "wanderer", "kb-writer"]
```

**After:**
```json
  "team_members": ["planner", "developer", "reviewer", "tidier", "approver", "tester", "giter", "devops", "explorer", "wanderer", "kb-writer", "doc-writer"]
```

### Edit: `tests/test_spawn_team_members.py` (lines 408–415)

**Before:**
```python
        expected = [
            "planner", "developer", "reviewer", "tidier",
            "approver", "tester", "giter", "devops",
            "explorer",  # Added in W1 so leader can authorize explore()'s
                         # internal spawn_instance of the "explorer" agent.
            "wanderer",  # Added when wanderer agent was introduced.
            "kb-writer",  # Added when kb-writer agent was introduced.
        ]
```

**After:**
```python
        expected = [
            "planner", "developer", "reviewer", "tidier",
            "approver", "tester", "giter", "devops",
            "explorer",  # Added in W1 so leader can authorize explore()'s
                         # internal spawn_instance of the "explorer" agent.
            "wanderer",  # Added when wanderer agent was introduced.
            "kb-writer",  # Added when kb-writer agent was introduced.
            "doc-writer",  # Added when doc-writer agent was introduced.
        ]
```

---

## Verification Commands

```bash
# 1. Validate meta.json is well-formed JSON
python -c "import json; json.load(open('agents/doc-writer/meta.json')); print('OK')"

# 2. Confirm KB_AGENT_IDS untouched (should show NO diff lines for KB_AGENT_IDS)
git diff -- daemon/repositories/instance/repository.py frontend/src/app/services/instance.service.ts

# 3. Run the team-members + registry tests
pytest tests/test_spawn_team_members.py::TestTeamMembersRegistryParsing tests/test_registry.py -v

# 4. (Optional, if pandoc installed) smoke-test format conversion
echo "# Test\nHello" > /tmp/dw-test.md && pandoc /tmp/dw-test.md -o /tmp/dw-test.pdf && ls -la /tmp/dw-test.pdf
```
