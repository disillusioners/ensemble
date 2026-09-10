# Research: Charter Agent, Callers, Prior Planning, Test Conventions

Researched: 2026-09-10 · Branch at research time: `feature/generate-chart-charter-reuse` (from `.git/HEAD`; read-only, no branch switch performed)

---

## 1. The Charter Agent (`agents/charter/`)

### meta.json (`agents/charter/meta.json`, 14 lines)

- `id: "charter"`, `name: "Charter"`, `version: "1.1.0"` (:2-7)
- `no_force_explore: true` (:8)
- `innate_skills: []` (:9)
- `tools.allow: ["bash", "proc", "filesystem", "context", "shared_meta_kv"]` (:11)
- `team_members: []` (:13) — charter cannot spawn other agents
- **No `llm_model` key** → uses system default model (unconfirmed whether a model override is applied at spawn; `invoke_agent_and_wait` forwards `model=None` by default, `daemon/utils.py:598`)

### Output contract (single fenced block + explanation)

- `agents/charter/rule.md:24` — "**Never return a diagram wrapped in anything other than a single ```mermaid fenced block** — the renderer depends on the exact fence tag"
- `agents/charter/soul.md:39` — "Return the validated diagram in a ```mermaid fenced code block with a brief explanation."
- `agents/charter/workflow.md:165-207` — exact return shapes: normal (block + optional 1-2 sentence explanation), `⚠️ Validation skipped` variant (:185-192), `⚠️ Validation failed after 3 attempts` variant with last mmdc error (:194-205)
- Charter is a **functional agent**: works only from caller-provided context, never investigates the codebase (`rule.md:11`, `workflow.md:26`)

### Mermaid validation mechanism — EXTERNAL CLI via bash, NOT a daemon validator

- Validation is a **prompt-driven bash subprocess**, not a daemon library/tool: `npx -y @mermaid-js/mermaid-cli` (`rule.md:5`, `soul.md:24`, `workflow.md:109-112`)
- Per-instance temp files via `mktemp /tmp/charter_XXXXXX.mmd` (`rule.md:6,20`, `workflow.md:97`) — explicitly motivated by concurrent charter instances (`rule.md:31`: "Concurrent charter instances must not collide")
- Retry budget: max 3 syntax-fix attempts, then return best-effort with warning (`rule.md:14`, `workflow.md:153-161`)
- If `npx`/`mmdc` absent: one install attempt, else skip validation + explicit `⚠️ Validation skipped` warning (`workflow.md:127-151`)
- Cleanup required: `rm -f $TMPFILE /tmp/charter_validate_output.svg` (`rule.md:13`)
- **No mermaid validation exists in `daemon/`** — `daemon/tools/chart_tools.py` performs no syntax checking; validation is entirely charter's responsibility (grep of daemon/tools for `generate_chart` shows only delegation wiring)

### Follow-up / refinement notion in prompts TODAY

Refinement exists only as a **caller-side re-invocation convention**, never as charter-side memory:

- `agents/charter/rule.md:12` — "Return a `NEEDS MORE INFO` result listing exactly what is missing ... so the caller can re-invoke `generate_chart` with sufficient detail in one round-trip"
- `agents/charter/workflow.md:43` — NEEDS MORE INFO template: "Re-invoke `generate_chart` supplying: ..."
- `agents/charter/soul.md:34` — "...so the caller can re-invoke me with sufficient detail. I never guess to fill gaps."
- `agents/charter/workflow.md:52` — "Every bullet must be concrete and actionable so the caller can fix the request in a single round-trip."
- **No session continuity, no "remember the prior chart", no conversational-refinement language anywhere in charter prompts.** Isolation is actively reinforced (`rule.md:6,31` per-instance temp files; `rule.md:11` work only from the request).

---

## 2. How Charter Receives the Task (`daemon/tools/chart_tools.py`)

### Message shape (`chart_tools.py:91-96`)

```
Create a {diagram_type} diagram.

Description: {description}
Project: {pid}          # line only if pid is non-None
```

- **No caller name, no caller context, no conversation history** in the message. The only identity-ish data is the spawned instance's *name*: `instance_name=f"chart-{description[:30]}"` (`chart_tools.py:107`).
- Invocation (`chart_tools.py:101-110`): `invoke_agent_and_wait(manager, agent_id="charter", message=chart_message, project_id=pid, parent_id=current_instance_id, instance_name=..., timeout=600.0, return_instance_id=True)`
- `project_id` auto-injection from the caller's instance meta (`chart_tools.py:45-55`, reads `manager._instance_repository.get(current_instance_id)`)

### Response flow back — VERBATIM

- `chart_tools.py:117-119` — `None` content → `"Error: Charter agent timed out or failed..."`; otherwise **returns the agent's text string unchanged** (no extraction, no re-wrapping)
- `chart_tools.py:135-137` (`_full_doc_`) — "returns the agent's text — a ```mermaid fenced block plus explanation — **directly to the caller. Paste it into your response without re-wrapping or stripping the fence**."
- Under the hood (`daemon/utils.py:588-740`): `invoke_agent_and_wait` spawns via `manager.spawn_instance_with_mcp(invoked_as_tool=True, ...)` (:660-670), registers with `completion_registry` (:674), enqueues message with `source=f"internal_invoke_and_wait:{parent_id or 'system'}"` (:680), waits on `registry.wait_for(instance_id, timeout)` (:689); timeout → best-effort orphan terminate + `"Error: Agent timed out after {timeout}s..."` (:691-697); error → `"Error: Agent failed. ..."` (:699-701). Deadlock prevention: semaphore capped at `WORKER_POOL_SIZE - 1` (:605-606). **Each call spawns a FRESH instance** — there is no reuse seam in this path today.

---

## 3. Caller Surface (backward-compat constraints)

### The documented usage contract lives in the chart INNATE SKILL, not in per-agent prompts

- `agents/_prompt_system/innate-skills/chart/skill.md` is the canonical caller documentation (auto-loaded into agents with `innate_skills: ["chart"]`)
- Grep of `agents/**/*.md` for `generate_chart|charter` returns only: charter's own files, `chart/skill.md`, and `agents/project-manager/tools_note.md:59` ("**No spawning other agents:** `charter`, `image-reader` — denied by name")

### Agents carrying the chart skill (15, via meta.json `innate_skills`)

`reviewer`, `developer`, `planner[v2]`, `tidier[v2]`, `approver`, `devops`, `doc-writer` (`["chart"]` only), `governor`, `developer[v2]`, `maintenancer`, `planner`, `ari`, `leader`, `reviewer[v2]`, `approver[v2]` — each meta.json line 7-11.

### Tool-permission wiring

- `daemon/tools/instance.py:133-140` — `INNATE_SKILL_TOOL_CATEGORIES` maps `"chart": ["chart"]` (note: current code maps to the **chart** category itself, not `["instance"]` as the original decision D3 planned — the mapping evolved; the comment at `instance.py:4315` confirms "agents with innate_skills:[\"chart\"] via INNATE_SKILL_TOOL_CATEGORIES")
- `daemon/tools/instance.py:143-173` — `expand_allow_for_innate_skills()` appends implied categories to `tools.allow`
- `daemon/tools/_tool_registry.py:610` — `"generate_chart"` registered in the tool universe
- `chart_tools.py:57` — `@register_tool_category("chart")`; security pin: chart tool must NOT be tagged `"instance"` (`tests/test_chart_tools.py:99-111`)

### Key usage contracts to preserve (exact quotes)

- `chart/skill.md:62` — "**Refine, don't hand-edit.** To modify a diagram, call `generate_chart()` again with a refined description rather than patching the previous output by hand." *(Note: this phrasing lives in the chart skill, not in any planner-specific prompt file — the task's premise attributed it to the planner; it applies to ALL 15 chart-skill carriers.)*
- `chart/skill.md:56` — "`generate_chart()` returns a single ```mermaid block, already validated. Paste it directly into your response — don't re-wrap, re-tag, or strip the fence."
- `chart/skill.md:61` — "**One diagram per call.** Each visual artifact is a separate `generate_chart()` invocation."
- `chart/skill.md:19` — "**Never iterate on a broken self-generated diagram.** If a chart you wrote doesn't render, don't patch it by hand — call `generate_chart()` instead."
- Signature contract (`chart/skill.md:41-47`): `description` (str, required), `diagram_type` (str, default `"flowchart"`, one of flowchart/sequence/class/er/state/gantt), `project_id` (str, optional) — matches `chart_tools.py:59-63`
- `chart/skill.md:14` — "User says your self-generated chart is broken/wrong → Use `generate_chart()`"

---

## 4. Prior Planning (`.agents/shared/planning/charter-mermaid-support/`)

Files: `plan-overview.md` (70 lines), `decisions.md` (266 lines), `phase1-plan.md`, `phase2-plan.md`. Status: **draft** (plan-overview.md:70), rev 3 dated 2026-07-02.

### What it built

- **Phase 1** (plan-overview.md:27): charter agent (5 files), `chart` innate skill, team_members wiring, `INNATE_SKILL_TOOL_CATEGORIES` daemon mapping, meta.json test updates
- **Phase 2** (plan-overview.md:28): frontend mermaid rendering — mermaid npm dep, angular.json script, ngx-markdown `mermaidOptions` dark theme, both agentColorMaps
- Phases independent, schedulable in parallel (plan-overview.md:30-36)
- Key decisions: D1 charter gets NO opencode skill (lean prompt, `decisions.md:7-21`); D2 mmdc-via-subprocess validation chosen over python libs/regex (alternatives table `decisions.md:39-46`); D3 chart skill requires tool-category mapping (original shape `"chart": ["instance"]`, `decisions.md:51-70` — since revised in code to `"chart": ["chart"]`, `daemon/tools/instance.py:135`); D4 ngx-markdown native mermaid + angular.json script registration (`decisions.md:74-85`)

### Deferred/future work re: instance reuse, sessions, refinement

**NONE FOUND.** Grep of all four planning files for `reuse|session|refine|iterat|follow-up|re-invok|defer|future|backlog` yields only incidental hits (opencode-skill rationale mentioning "session management" as irrelevant to charter, `decisions.md:13-14`; `phase1-plan.md:389` team-member test edit). The one-shot-fresh-instance-per-call model was simply the design; iterative refinement / per-caller instance reuse was never scoped, deferred, or rejected in these docs.

### Test files it planned to touch (B1 fix)

`plan-overview.md:49` + `phase1-plan.md:389`: update `test_innate_skills_refactoring.py` and `test_spawn_team_members.py` hardcoded assertions. **UNCONFIRMED/note:** current `tests/test_spawn_team_members.py` contains NO `charter` reference — the planned edit either landed differently or the test was restructured since.

---

## 5. Test Conventions

### Primary: `tests/test_chart_tools.py` (221 lines, flat under `tests/`, not `tests/unit/tools/`)

Three lanes (docstring :3-15):
1. **Factory** — `create_chart_tools(manager, "test-instance-id")` returns exactly one tool named `generate_chart`; independent closures per call (:47-81)
2. **Registration** — `_tool_category == "chart"` and SECURITY: != `"instance"` (:84-111)
3. **Invocation** — delegates to `invoke_agent_and_wait` with pinned kwargs: `agent_id="charter"`, `return_instance_id=True`, `timeout=600.0`, `parent_id` from closure (:117-153); default diagram_type flows into message (:155-167); response returned verbatim (:169-181); `None` → `"Error:"` string (:183-201); explicit `project_id` propagates into kwargs AND message (:203-221)

**Fixture/fake pattern** (:30-41, docstring :17-20): `MagicMock` manager with `manager._instance_repository.get = MagicMock(return_value=None)` (kills project auto-injection deterministically) + `patch("daemon.tools.chart_tools.invoke_agent_and_wait", AsyncMock(return_value=(content, "child-id")))` patched **at the chart_tools module level**; async invocation via `await tools[0].coroutine(...)`.

### Pattern descendants (explicitly chart-derived)

- `tests/test_todo_tools.py:3,14` — "Mirrors the structure of `tests/test_chart_tools.py`... The mocking pattern is identical to chart_tools"
- `tests/test_system_log_tools.py:46` — "Build a mock manager (pattern parity with test_chart_tools.py)"
- `tests/test_image_tools.py` (958 lines) — 5-lane extension of the chart pattern (:26-28: "Pattern reference: tests/test_chart_tools.py for the create_image_tools-factory + invoke_agent_and_wait patching style"), adding: agent-definition validation (meta.json security posture + markdown must NOT reference shell primitives `curl`/`mktemp`/`rm`, lanes :8-12), per-agent `tools.allow` whitelist pins for all carrier agents (:13-17, list at :44-56), mock-heavy security scenarios, `invoke_agent_and_wait` backward-compat lane for new optional kwargs (`images` defaults None, :22-24)

### Async wait-path testing seams

- **Module-level patch of `invoke_agent_and_wait`** is the standard for tool tests (chart/todo/system-log/image)
- **Real-routing test**: `tests/test_governor_recursion_acceptance_walk.py:570-603` — V5 imports REAL `invoke_agent_and_wait` from `daemon.utils` and awaits it end-to-end to assert guard refusals surface readably (integration-style seam: real spawn→register→enqueue→wait path)
- **completion_registry patching** for finalize-path tests: `tests/test_finalize_instance.py:116-125` and `tests/test_finalize_job_h15.py:320-325` patch `daemon.services.completion_registry.get_completion_registry` — note the lazy-import gotcha: `_finalize_instance` imports it lazily inside the function, so the patch must target the `daemon.services.completion_registry` **module attribute**, not the local name
- Semaphore/deadlock behavior (`daemon/utils.py:605-606`, `_get_invoke_semaphore()`) has no dedicated test file found (unconfirmed negative — searched `tests/` for `invoke_agent_and_wait` hits only in the files above)

### Naming conventions

- Tool tests: flat `tests/test_<x>_tools.py` with class-per-lane (`TestCreateXToolsFactory`, `TestXToolRegistration`, `TestXInvocation`), `_make_manager()` module-level helper
- Test-execution gate (repo convention): run ONLY via `uv run python -m pytest` from worktree root
- **QUARANTINE**: `.agents/tester/QUARANTINE.md` (not `tests/QUARANTINE.md`) — **no chart/charter/invoke_agent entries** (grep: zero matches)

---

## 6. Prompt-Change Constraints (if `agents/charter/*.md` changes)

### Integrity gate: `tests/unit/tools/test_prompt_section_reference_integrity.py` (820 lines)

- **Scope** (`_is_in_scope`, :80-117): ALL `agents/_prompt_system/innate-skills/*` files are in scope (:87-88 — branch was dead-scope-fixed 2026-09-08, guarded by `test_innate_skill_files_in_scope`), plus `agents/<agent>/{soul,rule,workflow,tools_note,memory}.md` and `skills-template/*`. **NOT in scope**: `meta.json`, `skill-set.yaml`, `growth.md`, `builder-prompt.md`, underscore scaffolding agents. → Both `agents/charter/*.md` prompt edits AND `agents/_prompt_system/innate-skills/chart/skill.md` edits fall under this gate.
- **Detectors** (docstring :25-31): empty captures `(See )`, glued words `whichload_skill`, bare `agents/` prefix used as cross-reference, lost backticks/quotes/parens. Convention v2: sibling references are `file.md → Section Name` with REAL markdown headings; path tokens are eliminated repo-wide (~290 refs, 97 files, per project blueprint).
- Charter's current prompts contain only **in-file** pointers — e.g. `soul.md:34` "(see workflow Step 2)", `rule.md:11` "(see next rule)", `workflow.md:20` "(Step 2)" — no cross-file section references. Cross-file: `chart/skill.md:80` "**Charter agent** (specialist behind `generate_chart()`): See charter's My Expertise" (natural-language section ref to charter soul.md's `## My Expertise` heading — keep that heading stable if editing soul.md).
- Tests run against the live checkout (`REPO_ROOT` via `__file__`, :46-47) — they double as regression guards on every prompt edit.

### Other test pins touching charter prompts/definition

- `tests/test_image_tools.py`-style agent-definition lanes (meta.json security posture, prompt-vs-shell-primitive greps) are the established pattern — a charter-prompt change that adds/renames sections could be mirrored in such lanes (no charter-specific lane found; unconfirmed negative)
- `tests/test_chart_tools.py:150-153` pins that the message "carries the description and diagram_type" — message-shape changes in `chart_tools.py` must update these assertions
- Category security pins: `tests/test_chart_tools.py:99-111` (`_tool_category != "instance"`) and the `PRIVILEGED_TOOL_CATEGORIES` exact-equality frozenset pins elsewhere (`{system_upgrade, system-log, ens-db}` — chart is not privileged; any change here would violate the blueprint privilege pins)

---

## Unconfirmed / Gaps

1. **Charter model**: no `llm_model` in `agents/charter/meta.json` — whether any caller passes `model=` to `invoke_agent_and_wait` for charter (chart_tools does not) → charter runs on system default. Caller-model-override machinery exists (`daemon/utils.py:625-632`) but chart_tools never uses it.
2. `phase1-plan.md:389` planned adding charter to `test_spawn_team_members.py`'s `expected_team` — current file has zero `charter` matches; the current location of any spawn-roster pin for charter is **unconfirmed**.
3. Planning docs' phase1/phase2-plan.md were only spot-grepped (not fully read); deferred-work conclusion rests on the grep + plan-overview + decisions.md full reads.
4. I cannot execute bash; branch identity comes from reading `.git/HEAD` (`ref: refs/heads/feature/generate-chart-charter-reuse`) — no `git rev-parse` was run.
