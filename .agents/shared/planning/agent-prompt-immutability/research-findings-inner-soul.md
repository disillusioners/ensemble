# Research Findings: inner_soul + Memory Surface (rewrite target mapping)

Commission: agent-prompt-immutability planning — read-only investigation.
Worktree: `/home/ensemble-worktrees/agent-prompt-immutability` (branch `feature/agent-prompt-immutability`, base `latest` @ e9cdc25c6).
All `file:line` citations are against this worktree.

---

## Summary

`inner_soul` (`daemon/tools/inner_soul.py`, 1404 lines) is a self-modification tool writing SIX filesystem surfaces under the agent directory: `memories/*.md` (timestamped observation files), `memory.md` (prompt-loaded core knowledge), `soul.md` (prompt-loaded identity), `workflow.md` (prompt-loaded methodology), `user.md` (NOT prompt-loaded anywhere), and `history/*_soul_change.md` (audit trail). Knowledge-classified content is redirected to the RAG lane (`experience()`/`explore()`) when RAG is enabled, or rejected outright when disabled; a REJECT sentinel blocks project content from explicit-target laundering. A second, independent file-write path exists: `agent_mother.agent_modify` (category `mother`, held only by `_mother`) does full-file replacement of soul/workflow/rule/user/memory/tools .md — it must be in scope for any immutability regime. The prior KB signals about per-call archive sweeps and version-blind path resolution are both WRONG in detail: the archive sweep runs at tool-creation time (rate-limited 300s), and path resolution IS version-aware — the version-blind remnant is the five `prompt_cache.invalidate(agent_id)` calls that omit the supported `version_tag` argument.

---

## 1. inner_soul Full Contract

### 1.1 Factory and binding

- `create_inner_soul_tool(manager, agent_id, instance_id, version_tag=None)` — `daemon/tools/inner_soul.py:545-818`.
- Path resolution (VERSION-AWARE): `registry.get_version(agent_id, version_tag) or registry.get_resolved(agent_id)` → `agent_path` — `inner_soul.py:569-572`. C1-fix docstring at `:557-563`; call site threads `version_tag` at `daemon/tools/instance.py:5648`.
- **Archive sweep fires at TOOL CREATION, not per call**: `inner_soul.py:574-579` loads growth rules and calls `_archive_old_memories(agent_path, ttl_days)`. Since tool creation happens per instance build, and the sweep is rate-limited to once per 300s per agent path (module-level `_last_archive_sweep` dict, `:37`, check at `:520-525`), effective cadence is ≤1 sweep / 5 min per agent dir.

### 1.2 Tool signature

`inner_soul.py:583-588`:

```python
def inner_soul(
    content: str | None = None,        # legacy alias for request
    request: str | None = None,
    intent: Literal["remember", "learn", "change"] | None = None,
    target: Literal["memory", "workflow", "soul", "user", "memories"] | None = None
) -> str:
```

- `request or content` required; empty → `ERROR: Must provide 'request' or 'content' parameter` (`:592-594`).
- Hard cap: request > 2000 chars → `ERROR: Request exceeds 2000 character limit` (`:597-598`).
- Compound requests split on uppercase `AND` → semicolons → sentence boundaries (`_split_compound_request`, `:842-879`); each part classified/validated independently (`:670-741`).

### 1.3 Classification (`_classify_request`, `:882-1025`) — 3-stage flow

- **Stage 1 — persona-intent detection** (`:916-925`): request starting with a `_PERSONA_INTENT_PREFIXES` pattern (`:205-214`, e.g. `^i should...`, `^my approach...`, `^be more...`) sets a flag. This does NOT bypass Stage 2.
- **Stage 2 — project-content pre-check, ALWAYS runs** (`:927-985`): any `project_knowledge` pattern match (`:146-192` — git ops, deployments, code changes, infra terms, file names...) → REJECT unless a persona prefix AND a persona category (identity/personality/user_preference/user_identity/workflow, `:950-953`) both match (dual-match fall-through `:941-976`; else reject `:977-985` returning `{"targets": ["REJECT"]}`).
- **Stage 3 — normal semantic classification** (`:987-1025`): regex tables `CLASSIFICATION_RULES` (`:44-193`); matching categories' target lists are MERGED in order (`:1005-1016`). Fallback → `event`/`[memories]` (`:1019-1025`).

Classification → default targets:

| Class | Default targets | Lines |
|---|---|---|
| identity | `[soul]` | `:44-55` |
| personality | `[soul, user]` | `:56-67` |
| user_preference | `[user]` | `:68-78` |
| user_identity | `[user]` | `:79-88` |
| pattern | `[memories]` | `:89-99` |
| workflow | `[workflow]` | `:100-113` |
| event | `[memories]` | `:114-124` |
| skill | `[memories]` | `:125-134` |
| mistake | `[memories]` | `:135-145` |
| project_knowledge | `[REJECT]` | `:146-192` |

### 1.4 Target resolution (`_resolve_targets`, `:821-839`)

- Explicit `target` → `[target]` (`:832-833`)
- `intent="remember"` → `[memories]` (`:834-835`)
- `intent="learn"` → `[memories, memory]` (`:836-837`)
- `intent="change"`/None → classification's (merged) targets (`:838-839`)

### 1.5 Redirect layer (`_should_redirect_to_rag`, `:230-276`)

Runs BEFORE the REJECT check (`:626-631` single; `:681-689` compound):
- If RAG disabled (`is_rag_enabled()`, imported from `daemon/rag/config` at `:24`) → never redirect (`:255-257`).
- `project_knowledge` classification → ALWAYS redirect when RAG enabled (`:265-266`).
- Else redirect iff ALL resolved targets ∈ `_RAG_TARGETS = {"memories", "memory"}` (`:217`) AND classification ∈ `_KNOWLEDGE_CLASSIFICATIONS = {pattern, event, skill, mistake, project_knowledge}` (`:225-227`) (`:268-274`).
- Any soul/user/workflow target → never redirect (`:269-270`).
- Redirect response (`_format_rag_redirect`, `:279-301`): `📋 Redirected to Knowledge System: "..."` + literal `experience(text="...")` / `explore()` guidance.

### 1.6 Project-content rejection (actual code)

- Primary check on CLASSIFICATION targets — G2 fix, prevents laundering via explicit target/intent: `inner_soul(target="memory", content="database migration done")` is caught because `"REJECT" in classification.get("targets", [])` — `:642-650` (single) and `:697-711` (compound); secondary safety net on resolved targets `:649-650, :706-711`.
- `_format_project_rejection` (`:304-329`): `⛔ Rejected: "..."` + guidance `project_history_add(project_id=..., entry_type='feature', summary='...')` for events and `experience(text='...')` for knowledge (`:318-322`).

### 1.7 Target × intent execution matrix (exact file effects)

Dispatcher `_execute_update` `:1028-1051`. All paths below resolve `agent_path` = `agents/{name}/` (versioned subtree when `version_tag` set).

| target | Handler | FILES READ | FILES WRITTEN (exact paths) | Guard rails |
|---|---|---|---|---|
| `memories` | `_update_memories` `:1054-1095` | existence check only | `agents/{name}/memories/YYYYMMDD_HHMM-<slug>[-N].md` via plain `write_text` `:1084`; `mkdir` `:1057`; collision counter `:1069-1073` | none (no lock; content = Created/Type/Description header + request `:1076-1083`) |
| `memory` | `_update_memory_md` `:1107-1177` | `agents/{name}/memory.md` read inside lock `:1114` | `agents/{name}/memory.md` atomic write `:1158` (+ transient `.tmp`/`.bak` `:363-398`, `.lock` sibling `:332-360`) | `max_memory_words` (default 2000, `:1116`); compaction at ≥80% `:1119-1131`; exact-dup skip `:1144-1152`; insert before `*For events`/`<!--` marker `:1134-1139` |
| `soul` | `_update_soul` `:1180-1276` | `agents/{name}/soul.md` read inside lock `:1191-1194` | ① `agents/{name}/soul.md` atomic write `:1237` ② `agents/{name}/history/YYYYMMDD_HHMMSS_soul_change.md` audit file `:1251, :1267` (`history/` mkdir `:1184`; previous-state snippet ≤500 chars `:1261-1265`) | `max_soul_chars` (default 2000) `:1197-1203`; name-change replace-in-place `:1210-1225`; else append bullet |
| `user` | `_update_user` `:1279-1310` | `agents/{name}/user.md` read inside lock `:1285` | `agents/{name}/user.md` atomic write `:1295` | placeholder `(To be filled` removal `:1288-1289`; append bullet |
| `workflow` | `_update_workflow` `:1313-1342` | `agents/{name}/workflow.md` read inside lock `:1319` | `agents/{name}/workflow.md` atomic write `:1327` | `**Learned:**` section ensured `:1321-1322`; append bullet |

Locking/atomicity helpers: `_lock_memory_file` (flock on `.lock` sibling, 5s timeout, lock-file cleanup) `:332-360`; `_atomic_write_memory` (tmp → rename-current-to-`.bak` → rename-tmp-in → delete bak; rollback on failure) `:363-398`.

Growth-rule knobs (`_load_growth_rules`, `:1369-1396`): parses `agents/{name}/growth.md` for `max_memory_words` (default 2000), `max_soul_chars` (default 2000), `memory_archive_ttl_days` (default 90). NOTE: defaults dict also carries `max_soul_statements`, `soul_requires_approval`, `workflow_changes_per_tasks`, `soul_changes_per_tasks` (`:1374-1382`) but NO regex parses them from growth.md — they are dead defaults today.

### 1.8 Archive lifecycle

- `_archive_memory_file` `:469-501`: moves `memories/<file>.md` → `memories/archive/YYYY/MM/<file>.md` (collision counter `-1`, `-2`...).
- `_archive_old_memories` `:504-542`: TTL scan by `st_mtime` (default 90d), `.md` files only, skips symlinks; 300s rate limit.
- Trigger point: tool creation `:574-579` (see 1.1) — **not** inside `_update_memory_md`.

### 1.9 Prompt-cache invalidation

Five call sites, all `manager.prompt_cache.invalidate(agent_id)` WITHOUT `version_tag`: `:1088` (memories), `:1162` (memory), `:1247` (soul), `:1304` (user), `:1336` (workflow). `PromptCache.invalidate(agent_id, mcp_tool_names=None, version_tag=None)` supports version scoping — `daemon/loader.py:809-819`. Because inner_soul resolves a versioned `agent_path` but invalidates only the base key, a v2 agent's write leaves stale `developer[v2]` cache entries. Mitigating net: `load_and_cache_prompt` compares tracked file mtimes on every get (`loader.py:908-917`, memories mtimes tracked at `:897-905`), so correctness is preserved by mtime compare; the invalidate calls are a latency optimization only. `agent_mother.agent_modify` has the same base-only invalidation (`agent_mother.py:380`).

### 1.10 Response formats

- Single success: `_format_response` `:1345-1366` — `✓ Processed: "<req≤80>"` + `Classification: <type> (<desc>)` + per-target `  ✓ <target>: <file> [(N chars)]` or `  ⚠ <target>: <error>`.
- Compound: `✓ Compound request split into N parts:` + per-part lines incl. `→ <type> (targets)` / `(redirected to RAG)` / `(REJECTED)` — `:671-741`.
- RAG redirect: `📋 Redirected to Knowledge System: ...` + experience/explore invocation string — `:279-301`.
- Rejection: `⛔ Rejected: ...` + redirect guidance — `:304-329`.
- Errors: empty `:594/:611/:617`; >2000 chars `:598`; lock timeout per-handler (`:1171-1177`, `:1238-1243`, `:1296-1301`, `:1328-1333`); word-limit `_format_rejection` `:1098-1104`; unknown target `:1051`; catch-all `ERROR: {e}` `:743-744`.

---

## 2. Memory Landscape Map (definitive current state)

### 2(a) `agents/{name}/memories/` — per-AGENT-NAME (not instance id)

- **Keying**: `{name}` is the agent NAME resolved via registry (`get_version`/`get_resolved`); versioned agents get their own subtree (`agents/developer[v2]/memories/`). There is NO instance-id-keyed memory path in current code. The old `.agents/{agent-id}/memories/` layout has ZERO references in `daemon/` in this worktree — retired.
- **Writers**: `inner_soul` target=memories (`inner_soul.py:1054-1095`) and the archive mover (`:469-501`). Nothing else.
- **Readers**:
  - `access_memory` tool — `daemon/tools/access_memory.py:32-96` (version-aware `:48`; archive path `archive/YYYY/MM/file.md` support `:58-67`; traversal/symlink defense `:76-82`; on miss lists ≤10 available names `:84-91`). Tool created for every instance at `daemon/tools/instance.py:5653`.
  - Prompt injection: `load_recent_memories` (`daemon/loader.py:314-360`) — FILENAMES ONLY, 5 most-recent active + optional archived; composed as `## Recent Memories` section (`compose_system_prompt` `loader.py:518-520`); memories mtimes tracked for cache invalidation (`loader.py:897-905`). Called at `loader.py:924`.
- **Disk state in worktree**: `glob agents/**/memories/*.md` → ZERO files. Runtime artifacts only (never committed).

### 2(b) RAG knowledge base (explore/experience)

- **Config**: `daemon/rag/config.py` (`is_rag_enabled`, consumed at `inner_soul.py:24`, `loader.py:306`, `knowledge_tools.py:890`).
- **Agent-facing write path**: `experience()` tool — `daemon/tools/knowledge_tools.py:874-919+` (category `knowledge`): requires RAG enabled + project context; extracts entities/relations via the **kb-writer agent** (docstring `:878`) in background; ALSO persists the raw text to the shared-context directory (fire-and-forget `_save_experience_result` via `asyncio.to_thread`, `:913-919`; context_key = tree-root instance id `:898-904`).
- **Raw insert path**: `rag_insert_text` (`daemon/tools/rag_tools.py:178`), `rag_insert_texts` (`:243`), plus query/graph/entity tools `rag_query` `:289`, `rag_query_data` `:378`, `rag_search_labels` `:543`, `rag_get_graph` `:587`, `rag_create_entity` `:664`, `rag_create_relation` `:778` (category `rag`). Held by `experiencer`, `explorer`, `kb-importer` (category `rag`), `kb-writer` (explicit `rag_insert_text`, `agents/kb-writer/meta.json:12`).
- **Read/injection path**: `explore()` — `knowledge_tools.py:679-870`; queries RAG then `_save_explorer_result` → shared context dir (`:860-868`). Injection: context slot (`daemon/graph.py:660-754`) `assemble_context_messages` returns `(persistent_msgs, ephemeral_msgs)`; the persistent half (project + shared context + skills) is prepended to `graph_input` on first turn as `[SYSTEM CONTEXT: ...]` HumanMessage blocks; per-agent opt-in flag `context_injection` (`daemon/registry.py:342`, parse `:613-618`).
- **Static doc**: `agents/_prompt_system/knowledge.md` teaches explore/experience usage; loaded `loader.py:300-311`, composed as `## Knowledge Base` section `:522-527`.

### 2(c) Relationship & overlap — the three stores

| Store | Prompt-visible? | Written by | Read by | Semantics |
|---|---|---|---|---|
| `memory.md` | YES — boot prompt section 8 (`loader.py:418` prompt_files; `:512-516`) | inner_soul target=memory; agent_mother agent_modify | prompt build; nothing else | persona insights, dedup-compacted, 2000-word cap |
| `memories/` | filenames only (`## Recent Memories`) | inner_soul target=memories | access_memory tool; loader index | timestamped observations; TTL archive to `memories/archive/` |
| RAG KB | via shared-context injection (per-turn `[SYSTEM CONTEXT]`, once persistent) | experience() → kb-writer → rag_insert_text; shared-context file mirror | explore(); rag_query* tools | cross-project queryable knowledge graph |
| `user.md` | **NO — never loaded into any prompt** (not in `prompt_files` `loader.py:418`; no compose section) | inner_soul target=user (`:1279-1310`); agent_mother agent_modify (`:359, :377`) | agent_mother agent_read ONLY (`agent_mother.py:301, :341`) | write-mostly artifact; agents record user info they can never re-read in-prompt |
| `growth.md` | NO (config file) | humans/agent_mother read | `_load_growth_rules` `:1369-1396`; agent_mother agent_read | inner_soul tuning knobs |

Redirect wiring: when RAG enabled, knowledge-classified content NEVER lands in memory.md/memories/ — it goes to RAG (redirect) — so memory.md/memories/ hold only persona-reflection residue; when RAG disabled, knowledge is rejected outright. Tool doc marks memory/memories targets "deprecated for new content" (`inner_soul.py:775-779`).

Migration-relevant special readers of prompt .md files (outside loader):
- Watcher soul: module-level read-ONCE cache of `agents/watcher/soul.md`, frozen-aware base path — `daemon/graph.py:10660-10674` (`_WATCHER_AGENTS_BASE`; PyInstaller `sys.executable` parent vs `__file__`-relative). A DB-override model must give this path a freshness/override story.
- `skill_job_dispatcher.py:114` and `watcher_context_builder.py:10` reference soul/workflow docs as design documentation only.

### 2(d) Second prompt-write path: agent_mother (must be in immutability scope)

- `daemon/tools/agent_mother.py` — category `mother`, granted ONLY to `_mother` (`agents/_mother/meta.json:11`).
- `agent_create` writes new agents' `soul.md`/`workflow.md` directly (`:188-218`).
- `agent_read` valid files: soul.md, workflow.md, rule.md, user.md, memory.md, tools.md, growth.md, meta.json (`:301`).
- `agent_modify` (`:349-393`): FULL-file replacement (`write_text` `:377`) of `modifiable_files = ["soul.md", "workflow.md", "rule.md", "user.md", "memory.md", "tools.md"]` (`:359`); protects `_`-agents except `_mother` (`:352`); base-key cache invalidation `:380`. No approval, no lock, no atomic rename.
- This is the path by which rule.md (a prompt part with NO inner_soul target) is agent-modifiable today.

---

## 3. Blast Radius (agents holding inner_soul / access_memory)

Grant mechanics (`daemon/tools/instance.py`):
- `self` category = inner_soul + access_memory (`daemon/tools/_tool_registry.py:578`); NOT in `PRIVILEGED_TOOL_CATEGORIES` (`:194-199` set = system_upgrade, system-log, ens-db, ens-env).
- No tools config or empty allow+deny → ALL tools minus privileged (includes `self`) — `_apply_tool_filter` `instance.py:6211-6215, :6248-6251`; `resolve_tool_filter` empty-empty → None `instance.py:400-405`.
- Explicit allow → only listed categories (expanded) `instance.py:6238-6246`.

**Agents granting `self` (28 of 41 meta-bearing agent dirs) = hold BOTH inner_soul AND access_memory:**

Explicit `"self"` in tools.allow (27): `_mother, approver, approver[v2], architect, ari, blueprinter, coder, designer, developer, developer[v2], devops, giter, governor, image-reader, jober, leader, planner, planner[v2], project-manager, reviewer, reviewer[v2], skill-keeper, tester, tidier, tidier[v2], wanderer, worker`

Default-granted via empty allow (1): `watcher` (`agents/watcher/meta.json:22` `"allow": []`)

**Without `self` (13):** `_baby_template, charter, critic, doc-maintainer, doc-writer, experiencer, explorer, gaia, image-comparator, kb-importer, kb-writer, maintenancer, sketcher`

Non-agent dirs: `agents/_inner_soul/` = documentation-only (no meta.json, never loaded — README `agents/_inner_soul/README.md:1-36`); `agents/_prompt_system/` = shared innate-skills/knowledge assets.

RAG lane holders for reference: category `rag` → experiencer, explorer, kb-importer; `rag_insert_text` explicit → kb-writer; `knowledge` (explore/experience) is broader (grep `"knowledge"` in meta.json if needed).

---

## 4. Write/Read Site Inventory (rewrite scoping)

### inner_soul filesystem WRITES (all sites)

| # | What | Where (file:line) | Classification |
|---|---|---|---|
| W1 | `memories/YYYYMMDD_HHMM-slug[-N].md` create | `inner_soul.py:1084` (`write_text`), mkdir `:1057` | pure memory state |
| W2 | `memory.md` atomic rewrite | `:1158` (+tmp/bak/lock `:363-398, :332-360`) | prompt part §8 AND mutable knowledge state — **dual nature** |
| W3 | `soul.md` atomic rewrite | `:1237` | PROMPT ARTIFACT (§1 of system prompt) |
| W4 | `history/*_soul_change.md` audit write | `:1267`, mkdir `:1184` | audit trail (side effect of W3) |
| W5 | `user.md` atomic rewrite | `:1295` | NOT prompt-loaded; persona data |
| W6 | `workflow.md` atomic rewrite | `:1327` | PROMPT ARTIFACT (§7) |
| W7 | `memories/…` → `memories/archive/YYYY/MM/…` rename | `:498`; triggered at creation `:574-579` | memory state lifecycle |

### inner_soul filesystem READS

| # | What | Where |
|---|---|---|
| R1 | `growth.md` rules | `:1387`; invoked `:576` (creation) + `:601` (per call) |
| R2 | `memories/` dir scan (TTL) | `:534-540` |
| R3 | `memory.md` | `:1114` |
| R4 | `soul.md` | `:1191-1194` |
| R5 | `user.md` | `:1285` |
| R6 | `workflow.md` | `:1319` |

### Classification for the immutability regime (what the code does TODAY)

- **Prompt .md artifacts** (loaded into boot prompt via `loader.py:418/:844` + compose `:460-516`): `soul.md` (W3), `workflow.md` (W6), `memory.md` (W2 — also knowledge state), plus `rule.md`/`skill.md`/`tools_note.md` which inner_soul does NOT touch (but agent_mother can modify rule.md/memory.md/tools.md).
- **Pure memory/recall state**: `memories/` (W1, W7 — file-based, access_memory-read), RAG KB (experience/explore — DB-backed already), `user.md` (W5 — file-based persona state, prompt-invisible).
- **Audit**: `history/` (W4).

Prompt-cache coupling: any DB-override model loses the mtime-compare invalidation net (`loader.py:843-917` tracks mtimes of exactly these files) — the five base-key-only invalidation sites (`:1088, :1162, :1247, :1304, :1336`) and agent_mother's (`agent_mother.py:380`) would need re-engineering, not just version-tag threading.

---

## 5. Semantics to Preserve (must-keep behaviors for the DB-submission rewrite)

1. **Self-reflection-only gate**: 3-stage classification with the project pre-check ALWAYS running (`:927-985`); persona prefixes never bypass it (`:888-920`).
2. **Anti-laundering G2 check**: REJECT decided on CLASSIFICATION targets, not resolved targets (`:642-650`) — explicit `target=`/`intent=` must not admit project content.
3. **Redirect layer ordering**: RAG redirect BEFORE REJECT (`:626-631`); soul/user/workflow never redirected (`:269-270`); project_knowledge always redirected when RAG on (`:265-266`), rejected with tool guidance when RAG off (`:304-329`).
4. **Compound split** with independent per-part classify/redirect/reject (`:670-741`) and split-marker-only guards (`:613-617`).
5. **Limits**: 2000-char request cap (`:597`); memory.md word cap + 80% proactive compaction + dedup (`:1116-1131, :1144-1152`); soul.md char cap (`:1197`); growth.md override knobs (`:1369-1396`).
6. **Concurrency discipline**: flock + atomic tmp/bak/rename with rollback for memory/soul/user/workflow (`:332-398`) — DB submissions replace this with transactional semantics but must stay multi-instance safe.
7. **Version-aware targeting**: writes/reads must keep landing in the versioned subtree (`:571`; `access_memory.py:48`) — DB model needs version-scoped rows equivalent.
8. **Response-shape compatibility**: agents parse the `✓/⚠/📋/⛔` response strings and the `experience()`/`project_history_add()` redirect guidance (`:279-329, :1345-1366`) — keep the grammar or bump tool docs atomically.
9. **Soul audit trail** (`history/` W4) — the DB revision log is the natural replacement; keep parity (who/when/previous-state).
10. **Recent-memories filename index** + access_memory on-demand reads (`loader.py:314-360, :518-520`; `access_memory.py`) — any memories-lane migration must preserve the index-then-fetch pattern.
11. **`user.md` asymmetry**: written by inner_soul target=user (+personality class) but never prompt-readable — decide whether the DB model fixes or preserves this.
12. **agent_mother second write path**: `_mother` can full-replace soul/workflow/rule/user/memory/tools .md today (`agent_mother.py:349-393`) — an immutability regime that only rewires inner_soul leaves a bypass.

---

## 6. Open Questions

1. **memory.md dual nature**: it is BOTH a prompt part (section 8) and inner_soul-mutable knowledge state. Under "prompt .md = immutable template + DB versioned override", does memory.md become a template (DB overrides) or stay mutable memory state? Today's redirect layer keeps knowledge out of it when RAG is on, so its live content is persona-reflection residue.
2. **user.md prompt-invisibility**: preserve (file stays write-mostly) or promote user.md into the prompt/DB persona block?
3. **Watcher soul module-cache** (`graph.py:10660-10674`): read-once-at-import, frozen-aware path — DB override freshness story needed.
4. **agent_mother scope**: convert agent_modify to DB submissions too, or strip file targets? Also `agent_create` seeds new agents' soul.md/workflow.md.
5. **Cache invalidation redesign**: with DB-backed prompts, the mtime net disappears; invalidation must become event-driven and version-scoped (today's 5 sites are base-key-only).
6. **`memories/` lane end-state**: keep file-based (index + access_memory) or migrate to RAG/DB? Code currently treats it as deprecated-but-alive (`inner_soul.py:775-779`); archive TTL lifecycle (`:469-542`) has no DB analogue yet.
7. **Dead growth knobs**: `soul_requires_approval` default (`:1378`) exists but is unparsed/unenforced — the approval workflow is the natural place to make it real.
