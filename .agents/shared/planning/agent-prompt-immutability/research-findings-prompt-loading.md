# Research Findings: Prompt Loading & Runtime Writers (agent-prompt-immutability)

Worktree: `/home/ensemble-worktrees/agent-prompt-immutability` (branch `feature/agent-prompt-immutability`, base `latest @ e9cdc25c6`). Read-only survey; all file:line refs below are worktree-verified.

---

## Summary

Prompt artifacts are read from the resolved agent directory by `daemon/loader.py:load_agent_prompts` (loader.py:405) and assembled by `compose_system_prompt` (loader.py:438-533, 11 fixed sections). The single choke point is `load_and_cache_prompt` (loader.py:822-932): an mtime-checked in-memory cache (`PromptCache`, loader.py:737-819, key `{agent_id}[{version_tag}]::{sorted_mcp_csv}`) consulted at exactly three call sites — spawn (instance_lifecycle.py:2098), checkpoint restore (instance_lifecycle.py:4561), and message reconstruction (persistence.py:1141). A DB-versioned resolution layer hooks cleanly at loader.py:920 (the cache-miss reload) and/or by wrapping `load_and_cache_prompt` itself.

Runtime writers of prompt .md are: `agent_mother.agent_modify` (agent_mother.py:377 — the primary offender), `agent_mother.agent_create/delete` (template copy / _trash move), `write_file`/`edit_file` (filesystem.py:899/:1270 — absolute paths bypass the workdir boundary), `inner_soul` (soul.md :1237, workflow.md :1327, user.md :1295, memory.md :1158, memories/*.md :1084), plus the `bash` escape hatch. A fail-loud guard follows two existing precedents: doc_write's allowlist+denylist+realpath discipline and agent_mother's fixed-file allowlist refusals.

**Key contradiction of the commission's assumptions:** the "immutable template" regime cannot blanket-cover all of agents/**.md — `memory.md` IS a boot-prompt section (section 8) yet is runtime-written by inner_soul with compaction logic; `user.md`/`growth.md` are inner_soul-managed state (growth.md parsed for behavioral rules); the assembly-order assumption that these are "inner_soul-only, not boot prompt" holds for user.md/growth.md but NOT for memory.md.

---

## 1. Artifact Inventory

### Directory census (agents/, worktree)

43 directories. Registry skip-list `SKIP_DIRS` (registry.py:19-24): `_trash`, `_baby_template`, `_prompt_system`, `_inner_soul`. Versioned dirs parsed via `_parse_agent_dir_name` (registry.py:31-43): `approver[v2]`, `developer[v2]`, `planner[v2]`, `reviewer[v2]`, `tidier[v2]`. Plain agents: ari, architect, blueprinter, charter, coder, critic, designer, developer, devops, doc-maintainer, doc-writer, experiencer, explorer, gaia, giter, governor, image-comparator, image-reader, jober, kb-importer, kb-writer, leader, maintenancer, planner, project-manager, reviewer, sketcher, skill-keeper, tester, tidier, wanderer, watcher, worker. Special: `_baby_template` (agent_create template source, agent_mother.py:141), `_mother` (self-managing system agent), `_prompt_system` (shared scaffolding).

### File union set (sampled: leader, ari, coder, developer[v2], explorer, _mother)

| File | Present in | Loaded into boot prompt? | Loaded by | Notes |
|---|---|---|---|---|
| soul.md | ALL sampled | YES — section 1 | loader.py:418,424 | identity/persona |
| rule.md | all except coder | YES — section 2 | loader.py:418 | constraints |
| workflow.md | all sampled | YES — section 7 | loader.py:418 | methodology |
| tools_note.md | all except coder | YES — section 6 (key `"tools"`) | loader.py:427-431 | fallback to tools.md (:428-433); no legacy tools.md observed in worktree |
| skill.md | none observed | YES — section 3 (legacy single-skill) | loader.py:418 | legacy path; multi-skill via innate_skills |
| memory.md | leader, _mother, developer[v2] | YES — section 8 | loader.py:418 | ⚠️ runtime-written by inner_soul with compaction (inner_soul.py:1107-1162) — NOT template-safe |
| user.md | leader, ari, _mother | NO | — | inner_soul state (written inner_soul.py:1295) |
| growth.md | leader, _mother | NO | — | inner_soul rules parse (inner_soul.py:1370-1373); NOT in agent_mother modifiable list (:359) |
| memories/*.md | agents with memories/ | filenames only — section 9 | loader.py:314-360 | recent-memories listing |

### Other contents

- **meta.json** — every agent. Config only (id, name, description, icon, color, version, `innate_skills` NAME list, `no_force_explore`, `context_injection`, `tools.allow`, `team_members`, `llm_model(s)`, `skill_injection`, flags). **No file-path fields**; `innate_skills` names resolve to shared `agents/_prompt_system/innate-skills/{name}/skill.md` (loader.py:363-369, registry.py:1033).
- **skill-set.yaml + skills-template/** — only dynamic-skill agents (coder, developer[v2]).
- **_prompt_system/** — shared: `critical-notes.md`, `knowledge.md`, `knowledge_no_force_explore.md`, `project-experience.md`, `innate-skills/`. knowledge.md / knowledge_no_force_explore.md become boot section 10 (loader.py:296-311, keyed off meta `no_force_explore`); project-experience.md section 11 (loader.py:285-293).

---

## 2. Prompt Assembly Path (trace)

### Read sites & choke points

1. **`load_agent_prompts(agent_dir)` — loader.py:405-435.** Reads the 5 base files + tools_note/tools via `filepath.read_text`. Skips missing files.
2. **`compose_system_prompt(prompts, skills, dynamic_tools, project_experience, recent_memories, shared_knowledge)` — loader.py:438-533.** Section order: 1 soul (:476) → 2 rule (:482) → 3 skill (:488) → 4 skills/ (:494) → 5 dynamic-tools-doc (:502) → 6 tools_note (:506) → 7 workflow, 8 memory (:512) → 9 recent memories (:519) → 10 shared knowledge (:523) → 11 project experience (:530). Joined `"\n\n---\n\n"` (:533).
3. **⭐ THE CHOKE POINT: `load_and_cache_prompt(agent_id, agent_dir, cache, mcp_tool_names, version_tag)` — loader.py:822-932.**
   - Builds mtime snapshot: meta.json (:852-860), prompt files (:868-871), tools_note/tools (:874-879), innate skills (:884-886), legacy skills/ (:890-895), memories/ (:897-905), project-experience.md (:848-849), knowledge variant (:863-866).
   - Cache hit iff stored mtimes == current mtimes (:908-917).
   - **Cache miss → reload + recompose at :920-930.** This is where a DB-override resolution layer hooks: wrap the `prompts` dict between :920 and :926, or decorate `load_and_cache_prompt` itself (all three callers route through `daemon.manager.load_and_cache_prompt` re-export, manager.py:26 — tests patch there).
4. **Callers (exactly three prompt-building sites):**
   - **Spawn:** instance_lifecycle.py:2096-2104 → then `_apply_post_cache_appends` (:2108-2120) appends context-injection/metadata/time/language/auto-skills POST-cache (uncached, per-instance).
   - **Checkpoint restore:** instance_lifecycle.py:4559-4567 — `agent_path = Path(agent_meta.path)`, `version_tag=resolved_tag`. **Resume DOES re-read files** (mtime-checked) — a resumed instance can silently see newer template content than the one it started with (drift window; DB versioning closes this only if the restore path also resolves versions).
   - **Message reconstruction:** persistence.py:1053-1157 `_reconstruct_full_system_prompt` — mirrors spawn/restore using the **persisted `instance.agent_dir`** (DB row, :1121-1129) + `instance.agent_tag` (:1134).
5. **Token accounting:** instance_messaging.py:1047-1069 `_get_system_prompt_tokens` reads `self._prompt_cache.get(meta.agent_id, mcp_tool_names)` — ⚠️ **without version_tag** (latent key-shape inconsistency; returns base entry or 0 for tagged agents). Feeds `_compute_context_usage` (:1071-1100) = history tokens + cached system tokens vs context window → compaction. Token estimation: `estimate_tokens` loader.py:536 (tiktoken cl100k_base; warm-up at manager.py:3308-3324).

### Timing semantics

- Read **per instance spawn** and **per checkpoint restore**; NOT per turn. The composed prompt is baked into graph state as the SystemMessage for the instance's life.
- Mid-session file edits have NO effect on running instances unless something calls `prompt_cache.invalidate` AND a later spawn/restore re-runs `load_and_cache_prompt` — the mtime check only executes when the function is invoked.
- `prompt_cache` is in-memory on the manager (manager.py:480), process-local, lost on restart. Key: `{agent_id}[{version_tag}]::{sorted_mcp_csv}` (loader.py:748-774; D15). `invalidate(agent_id, mcp_tool_names, version_tag)` at :809-819. **No version/token column anywhere — DB versioning is a new cache-key dimension** (resolution hook must extend `_make_key` or cache the RESOLVED prompt under the resolved-version id).
- graph.py has two ADDITIONAL global read-once caches for watcher internals: `_WATCHER_SOUL_PROMPT_CACHE` (graph.py:10665-10706) and `_WATCHER_BUILDER_PROMPT_CACHE` (:10713-10747) — loaded once per process, never invalidated.

### agent_dir resolution (install-tree hazard confirmed)

- `AgentRegistry(agents_dir)` (registry.py:535-541); default singleton `AgentRegistry(base_dir / "agents")` (registry.py:1220); `base_dir` = `sys.executable.parent` when frozen else `Path(__file__).parent.parent` (registry.py:1217-1219; same pattern loader.py:19-21, agent_mother.py:27-30, routers/agents.py:20-22). Comment at manager.py:3270-3282 confirms frozen binaries resolve agents/ **into the install/release tree** (`make install` copies agents/ per release; retention 3 → old release trees get evicted).
- `AgentMetadata.path` = "Resolved absolute path to agent directory" (registry.py:267), persisted as `instances.agent_dir` at spawn (instance_lifecycle.py:1921 `resolved_agent_dir = str(metadata.path)`, :2429).
- ⚠️ **Divergent path bases:** loader/registry use the registry-resolved path, but `agent_mother` and `routers/agents.py` use their own `BASE_DIR / "agents"` (agent_mother.py:48,140,424; routers/agents.py:67,154) — in frozen installs both should agree, but the guard must cover BOTH resolution styles.

---

## 3. Runtime Writers & Guard Points

### (a) Agent-facing file tools

| Writer | Site | Path handling | Natural guard point |
|---|---|---|---|
| `write_file` | filesystem.py:882-905 (write :899) | `_resolve_within_workdir` (:889); **absolute paths bypass boundary check** (:205-209); creates parent dirs | Immediately after resolve (:891, pre-open) — check target against `**/agents/{name}/{artifact}.md` (registry-path OR BASE_DIR form) → loud refusal. Alternatively inside `_resolve_within_workdir` to cover edit_file too |
| `edit_file` | filesystem.py:1235-1270 (write :1270) | same resolver | same |
| `bash` | bash.py (shell escape hatch) | arbitrary shell | NOT coverable by code-level path check — needs OS-level fence (read-only release tree / chmod) or bash policy lane; disclose this gap in design |
| `agent_mother.agent_modify` | agent_mother.py:349-393 (write :377) | `BASE_DIR/agents/{name}/{file}`; modifiable_files **:359 = [soul, workflow, rule, user, memory, tools].md**; system-agent refusal :352-356; invalidates cache :380 (**base key only — version_tag/mcp variants stay stale**) | :359-364 — shrink `modifiable_files` / branch to the new DB versioning API; fix :380 to invalidate all key variants |
| `agent_mother.agent_create` | agent_mother.py:~130-260 (writes :200 soul, :219 workflow, :234 rule, :251 tools from `_baby_template`) | creates new agent dir | Author-time exception: creation writes a NEW dir — legal under immutability; still route through the new versioned-write API for the audit trail |
| `agent_mother.agent_delete` | agent_mother.py:~420-440 (move to `_trash` :426) | rename | Not a content edit; keep, but consider guard against deleting versioned dirs mid-approval |
| routers/agents.py POST | routers/agents.py:101-148 (template copy :106-110, meta.json write :122) | `BASE_DIR/agents` | Same creation exception as agent_create |
| routers/agents.py DELETE | routers/agents.py:151-160 (→ `_trash` :156) | rename | As above |

### (b) Internal writers

- **inner_soul** (agent_path = `agent_meta.path`, inner_soul.py:572): `memories/*.md` create :1084 (+invalidate :1088); `memory.md` lock+atomic write :1107-1162 (`_atomic_write_memory` :363-376; compaction at 80%/:1119); **soul.md** :1237 (+invalidate :1247); history :1267; **user.md** :1295 (+invalidate :1304); **workflow.md** :1327 (+invalidate :1336); growth.md READ for rules :1370-1373. These are the persona-evolution writes the immutability regime must redirect to DB-versioned rows (or carve out).
- **skill_seed_service** — constructed with `agents_base` (manager.py:3283-3286) to seed the Skill Bank from agents/ tree; seeding reads skill content INTO DB (not verified to write back — verify in design phase).
- No other daemon writers target `agents/{name}/*.md`: knowledge_tools writes go to `<tempdir>/ensemble/context/` (knowledge_tools.py:537-558); doc_write is docs/-allowlisted; comment_edit targets source comments; upgrade_journal writes only its own state.

### (c) inner_soul write enumeration (for the sibling explorer)

:363 `_atomic_write_memory`, :1084 memories file, :1158 memory.md, :1237 soul.md, :1267 history, :1295 user.md, :1327 workflow.md. Cache invalidations at :1088, :1162, :1247, :1304, :1336 (all base-key only).

---

## 4. Precedents for the Guard Design

1. **doc_write.py (BEST precedent)** — daemon/tools/doc_write.py:1-60: path **allowlist** (`docs/`, `doc/`, top-level `*.md`), **denylist** (`.agents/`, `daemon/`, `frontend/`, `node_modules/` — :57-60), **realpath containment** (anti-`../`), atomic write + flock, AND **mechanical tool-absence** (doc-maintainer's tools.allow omits write_file/edit_file entirely — layering: even if the guard has a hole, the tool isn't reachable).
2. **agent_mother refusals** — system-agent protection :295-299 / :352-356 (`startswith("_")` → structured `{success: False, error}`), fixed file allowlists :301 (read) / :359 (modify). This IS the existing partial-immutability implementation: growth.md/meta.json are already read-only there.
3. **WorkspaceGuard + walk-tool hardening** — filesystem.py:192-244 (relative paths must stay in workdir; temp-dir allowance) and `_resolve_search_root` :247-266, which deliberately CLOSED the absolute-path bypass for walk tools after the 2026-09-23 16GB-walk incident — direct precedent for closing write_file's absolute-path bypass for the agents/ subtree.
4. **Registry defensives** — symlink skip (registry.py:563-566), SKIP_DIRS (:19-24), tag-name sanitization (:26-43 `dev[../etc]` treated as plain id).
5. **Fail-loud refusal convention** — upgrade lane's fail-closed gates (env allowlist stripping, argv-only operator flags) and RAG tool closed-fail authorization: refuse with a reason token + journal/log line, never silently degrade.

---

## Open Questions

1. **memory.md fate** — it is boot-prompt section 8 AND runtime-written with compaction. Under immutability: does memory.md content migrate to a DB table (per agent), or is it carved out of the immutable artifact set? (Same question, lower stakes, for user.md/growth.md.)
2. **Cache key extension** — DB-versioned prompts need either a new `PromptCache` key dimension (resolved-version id) or resolution upstream of the cache. `_make_key` (loader.py:748) is the single extension site; `invalidate` callers (agent_mother.py:380; inner_soul.py:1088/:1162/:1247/:1304/:1336) currently only clear the base key — tagged/MCP variants go stale (pre-existing bug the migration must fix or inherit).
3. **`_get_system_prompt_tokens` drops version_tag** (instance_messaging.py:1063) — token accounting for tagged agents is already subtly wrong; fix opportunistically during the resolution-layer work.
4. **skills-template/ & skill-keeper** — do skill-evolution flows write skill-set.yaml / skills-template/ at runtime? (skill_seed_service seeding verified as read-into-DB; skill-keeper write path not traced in this pass.)
5. **Frozen-install topology** — in releases, agents/ is already de-facto immutable (release artifacts, retention-evicted); the guard's real target is dev-checkout + demo installs. Should the guard key off `ENSEMBLE_SELF_ENV` / frozen detection, or enforce uniformly?
6. **bash escape hatch** — code-level guards cannot stop `bash("echo x > agents/ari/soul.md")` (or `cd` + heredoc). Design needs either an OS-level fence for the template tree or an explicit accepted-risk note, following the test-fencing precedent from the 2026-10-12 live-deletion incident (critical notes).
