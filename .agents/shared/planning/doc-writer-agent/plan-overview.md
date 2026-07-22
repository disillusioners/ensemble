# Plan Overview: `doc-writer` Agent

## Objective

Add a new `doc-writer` agent to the ensemble that produces polished, chart-enriched documentation (md primary, plus pdf/docx/excel/csv via format conversion) for critical or important sections. The agent writes **document-type files only** and rejects code-writing requests. It must be reachable from the Leader via `team_members`.

## Scope Assessment

**MEDIUM** — single-module change with a clear pattern (5 new files in `agents/doc-writer/` + 2 small edits elsewhere) but several design decisions to lock down: (1) whether to allow `bash` for format conversion, (2) whether to declare it a KB-type agent (no — requirement says visible in UI), (3) which tool categories are appropriate, (4) handling of binary formats (pdf/docx/excel/csv) since `write_file` is text-only, (5) how to keep one test in sync with the leader `team_members` list.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Convention source**: `agents/kb-writer/` (most recent precedent), `agents/tidier/`, `agents/planner/`
- **Critical constraint found**: `tests/test_spawn_team_members.py::TestTeamMembersRegistryParsing::test_leader_team_members_parsed` is an **exact-set equality** assertion on leader's `team_members`. Adding `doc-writer` requires updating that test in lockstep.

## Phase Index

| Phase | Name | Objective | Files | Coupling |
|-------|------|-----------|-------|----------|
| 1 | Create doc-writer agent + sync leader + sync test | Define the agent (meta.json, soul.md, workflow.md, rule.md), wire it into Leader's team_members, and update the leader-team-members exact-set test | 7 files | single phase (internally sequential: agent files → leader meta → test) |

This is a **single coherent module** (one new agent + 2 trivial syncs) — splitting into multiple phase files would over-fragment per the granularity rules. Per the Phase Granularity rule, one phase is appropriate here.

### Coupling

| Step inside Phase 1 | Coupling | Justification |
|---------------------|----------|---------------|
| Create `agents/doc-writer/{meta.json, soul.md, workflow.md, rule.md}` | — (foundation) | None — these are net-new files. |
| Update `agents/leader/meta.json` team_members | **loose** to agent creation | Only the string `"doc-writer"` must appear; no code in doc-writer references it. Could land in any order, but conventionally after the agent exists. |
| Update `tests/test_spawn_team_members.py::test_leader_team_members_parsed` | **tight** to leader meta edit | Exact-set test on `leader.team_members`. Must match the new value byte-for-byte. |
| Update `frontend/src/app/services/instance.service.ts` (notification.service.ts if applicable) | **N/A — NOT required** | `doc-writer` is **not** a KB-type agent (requirement: "NOT a KB-type agent — it should be visible in the UI and spawnable directly"). KB_AGENT_IDS frozenset stays at `{"experiencer", "kb-importer", "kb-writer"}`. |

## Key Design Decisions

### D1: Allow `bash` for format conversion? — **YES, with allowlist + clear soul/rule caveats**

| Option | Pro | Con |
|--------|-----|-----|
| A: Allow `bash` | Can convert md→pdf/docx/excel/csv via `pandoc`, `libreoffice --headless`, `csvkit`, etc. | Power + abuse risk; can run arbitrary shell |
| B: Forbid `bash` | Safer, more focused | Cannot satisfy the format conversion requirement at all |
| C: Custom tool with limited surface | Safest | Out of scope; requires new daemon tool |

**Decision: A** — `bash` is necessary to deliver pdf/docx/excel/csv. Mitigation:
- Narrow scope in `soul.md` / `rule.md`: bash is ONLY for `pandoc` / `libreoffice --headless --convert-to <fmt>` invocations on already-written md/csv files. No arbitrary scripting.
- Add an explicit MUST NOT in `rule.md`: no network calls, no `rm`, no `curl`, no shell metacharacters, no piping untrusted input.
- `bash` is also useful for verifying output sizes (`wc -l`, `file <output>`) before reporting.

### D2: Tool categories — minimal but sufficient

Allow list:
- `filesystem` (read/write/edit/list/glob/grep) — required
- `chart` (via `innate_skills: ["chart"]`) — auto-grants `generate_chart`
- `time`, `help` — utilities (convention)
- `bash` — for format conversion (D1)
- `context`, `shared_context` — to read `.agents/shared/planning/**` plans as input (consistent with planner/explorer/tidier)

Innate skills: `["chart"]`. (No need for `todo` — doc-writer is single-pass and short; no need for `question` — the soul.md instructs it to reject code requests or ask via plain text.)

### D3: KB-type or visible? — **VISIBLE** (per requirement)

`doc-writer` must be in the UI and directly spawnable by Leader. It is **not** added to `KB_AGENT_IDS`. No frontend sync needed.

### D4: How to write binary formats (pdf, docx, excel)?

`write_file` is UTF-8 text-only (see `daemon/tools/filesystem.py:447-450`). Binary formats must go through `bash`:

```
pandoc input.md -o output.pdf
pandoc input.md -o output.docx
libreoffice --headless --convert-to pdf input.md
```

For `.csv` and `.md` use `write_file`. For `.xlsx` use `pandoc input.md -o output.xlsx`. For `.docx`/`pdf` use `pandoc` (preferred) or `libreoffice --headless`.

This means the **deliverable file format must be chosen up front** in the request — doc-writer asks for clarification if unspecified.

### D5: "Write files only or question back for clarification" — enforce in `rule.md`

If asked to write code, edit application logic, or modify non-document files, doc-writer must:
1. Politely reject with the reason ("I only write document-type files: .md, .pdf, .docx, .xlsx, .csv").
2. Suggest the right agent (`developer` for code, `tidier` for code review, `planner` for plans).

This is a hard MUST in `rule.md`.

## Files to Create / Modify

### Create (4 files)
| Path | Purpose |
|------|---------|
| `agents/doc-writer/meta.json` | Tool config, identity, version, model |
| `agents/doc-writer/soul.md` | Identity, scope, what I do / don't do |
| `agents/doc-writer/workflow.md` | Step-by-step doc-writing process |
| `agents/doc-writer/rule.md` | Hard MUST / MUST NOT constraints |

### Modify (2 files)
| Path | Change |
|------|--------|
| `agents/leader/meta.json` | Append `"doc-writer"` to `team_members` array |
| `tests/test_spawn_team_members.py` | Append `"doc-writer"` to the `expected` list in `test_leader_team_members_parsed` (lines 408–415) |

### NOT modified (verify)
- `daemon/repositories/instance/repository.py::KB_AGENT_IDS` — stays `{"experiencer", "kb-importer", "kb-writer"}`
- `frontend/src/app/services/instance.service.ts::KB_AGENT_IDS` — stays `{"experiencer", "kb-importer", "kb-writer"}`
- `frontend/src/app/services/notification.service.ts::KB_AGENT_IDS` — same
- No daemon code, no new tools, no new tests for chart/filesystem modules.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| doc-writer uses bash to escape its sandbox (e.g., `curl evil.com`) | high | `rule.md` MUST NOT: no network, no shell metacharacters beyond the pandoc/libreoffice command form; rule.md reminds "narrow scope" rationale copied from soul |
| doc-writer accepts code-writing requests and produces broken code | medium | Hard MUST in `rule.md`: "MUST reject code-writing requests." soul.md reinforces identity as docs-only. |
| `test_leader_team_members_parsed` exact-set test breaks if leader meta is updated without the test (or vice versa) | low (build break) | Single PR / single phase; both edits in the same commit. The test file is listed in this plan's modify list precisely to prevent forgetting. |
| `pandoc` / `libreoffice` not installed on the runtime host | medium | `rule.md` MUST: "If pandoc/libreoffice missing → call `tool_help('bash')` to confirm availability before assuming conversion works; otherwise report failure and write `.md` only." Surface as a clear error, not a silent half-deliverable. |
| doc-writer writes to wrong directory or escapes workdir | low | filesystem tools already enforce `_is_within_workdir` (see `daemon/tools/filesystem.py:144-176`). No new mitigation needed beyond the standard tool contract. |
| Adding `bash` to doc-writer widens its attack surface compared to kb-writer/explorer scope | low | Acceptable trade-off for format conversion. Mitigated by rule.md strict scoping. Compare to `tidier`/`reviewer`/`planner` which all already have `bash` — precedent is established. |

## Testing Considerations

| Test | Why | Type |
|------|-----|------|
| `tests/test_spawn_team_members.py::TestTeamMembersRegistryParsing::test_leader_team_members_parsed` | Must be updated to include `"doc-writer"` | Edit existing |
| `tests/test_registry.py::TestDiscoverAgents` family | Auto-discovers `agents/doc-writer/`; should pass without modification | Regression — run as-is |
| Manual smoke test (post-deploy) | Spawn `doc-writer` via Leader, ask for a small `.md` → `.pdf` conversion, verify file lands in workdir | Manual / e2e |

No new test files are required. The existing discovery tests cover auto-loading; the team-members test pins leader's list; manual smoke covers the unique format-conversion path.

## Success Criteria

- [ ] `agents/doc-writer/meta.json` exists, valid JSON, `tools.allow` contains exactly: `bash`, `filesystem`, `time`, `help`, `context`, `shared_context`.
- [ ] `agents/doc-writer/soul.md`, `workflow.md`, `rule.md` exist with the MUST-reject-code and narrow-bash contracts documented.
- [ ] `agents/leader/meta.json` `team_members` array contains `"doc-writer"`.
- [ ] `tests/test_spawn_team_members.py::test_leader_team_members_parsed` updated to include `"doc-writer"`.
- [ ] No edits to `KB_AGENT_IDS` (verified with `git diff`).
- [ ] `pytest tests/test_spawn_team_members.py::TestTeamMembersRegistryParsing -v` passes locally.
- [ ] Manual: Leader spawns doc-writer → doc-writer writes `out.md`, runs `pandoc out.md -o out.pdf`, both files exist in workdir.

## Tracking

- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: draft
- Phase file: `./phase1-plan.md`