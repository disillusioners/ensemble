# Agent Snapshot — Implementation-Feasibility & Effort Notes

**Status:** Assessment complete (read-only; no code touched). **SUPERSEDED POINTERS (Rev 5, 2026-09-25):** the **scope-shape** is superseded by [`design-exploration.md`](design-exploration.md) Rev 5 (per-instance pivot P1, grants P2-v1, R6-R16 fold-ins). The verified codebase anchors below (file paths, line ranges, mechanism facts) **remain the implementation ground truth** — PR-time development MUST re-verify them at current HEAD per verification rider (c) in §12 of the design doc. Specific supersedures recorded below at the relevant call sites; the architecture, register, and PR breakdown in §A-E remains valid as Rev 4 evidence, with Rev 5 deltas marked in-line.

**Rev 5 supersedures applied to this file:**
- §A.1 (Schema) — `snapshot_nodes` table is **DELETED in Rev 5**; only 2 tables ship (`snapshots`, `snapshot_embeddings`). §3.3 still describes the create_all + migration-file path that applies to both tables.
- §A.2 (SnapshotService creation pipeline) — **effort drops materially** in Rev 5: the tree walk (`get_tree_ids_permanent`), enumerate-first exclusion, per-node skip-and-record, per-node idempotency/refill, reduce-merge of child briefs, 32-node cap, `truncated` flag are all DELETED. Capture is single-instance (`manager.get_instance(target) + graph.aget_state(thread_id=target)`). Effort L → **M** in Rev 5.
- §A.5 (Flat-digest consumption) — survives; placement seam unchanged. Rev 5 renames `spawn_instance_from_snapshot` → `spawn_hot_instance` and adds the R14 auto-fallback result contract.
- §A.6 (3 tools + registration + prompt/meta lines) — Rev 5 changes the grants: `snapshot_create` + `snapshot_search` get per-tool `tools.allow` entries in **worker, coder, tester** only; `spawn_hot_instance` ships in the `instance` category (auto-grant, ari excluded). Rev 4 said leader + ari; that is SUPERSEDED.
- §B(i) — three caveats (memory pins, watchover cascade, KeyError) all survive; in Rev 5 the memory-pin caveat applies to ONE graph (was 32), the watchover cascade applies to the single target, and the KeyError try/except applies to the single target.
- §B(ii) — 6-file chain survives. **Add to the registration chain (Rev 5):** `agents/worker/meta.json`, `agents/coder/meta.json`, `agents/tester/meta.json` (was leader + ari). The warm-list hazard at `daemon/loader.py:42-63` and the DYNAMIC_TOOL_NAMES expansion at `_tool_registry.py:23-110` apply identically.
- §D3 — JAFP lane ruling survives; Rev 5 makes the row-ledger even simpler.
- §D4 — Checkpoint interactions survive; Rev 5 makes the survival check single-instance.
- §D5 — Token/cost ceilings survive with two Rev 5 deltas: **delete the 32-node cap** (Rev 5 is single-instance); **add R14 result-contract overhead** (the warm/cold hint is a few extra fields in the result, negligible cost).

**What this file remains authoritative for:** every line anchor verified end-to-end on the working tree (`daemon/compaction.py:3325-3440`, `daemon/services/compact_executor.py:732-738`, `daemon/services/instance_lifecycle.py:3861-3890`/`:3977-4370`, `daemon/repositories/instance/repository.py:527`/`:33`, `daemon/services/context_messages.py:83-107`/`:130-165`/`:1589`/`:618-640`/`:932-975`, `daemon/tools/instance.py:1853`/`:663`/`:686`/`:2224-2226`/`:4709-4711`/`:292-409`, `daemon/tools/_tool_registry.py:23-110`/`:513-570`/`:604`/`:370-372`, `daemon/loader.py:42-63`, `daemon/config.py:2835-2867`, `daemon/manager.py:6870`/`:3774`/`:520-525`/`:5099`, `daemon/migrations/runner.py:67-68`/`:719-727`). These anchors survive Rev 5 — the dev PR-time audit MUST re-pin them per verification rider (c).

**Date:** 2026-09-23 (Rev 4 verification); Rev 5 supersedures recorded 2026-09-25.
**Branch:** `plan/agent-snapshot` @ 720a2b39 (verified at Rev 5 fold-in)
**Inputs:** design-exploration.md (Rev 4, 640 ln) + design-exploration.md Rev 5 (945 ln, 2026-09-25, post-Rev-5.1 textual touch-ups) + machinery-inventory.md (63 ln), all read fully from disk.
**Method:** every load-bearing anchor below was re-verified by direct read on the working tree (grep/sed/read_file only — no builds, no tests). Where architect, wanderer, and my read disagree, my read wins and the delta is recorded in Section C. **Rev 5 does not re-verify the codebase anchors** (Rev 4's verification stands); the supersedures above are scope-shape changes, not anchor changes.

---

## SECTION A — Per-Component Feasibility / Effort

### Summary table

| # | Component | Effort | Genuinely new | Reused (anchor verified) |
|---|---|---|---|---|
| 1 | Snapshot schema + migrations [**Rev 5: 2 tables**] | **M** | 2 SQLModel tables (was 3 in Rev 4; `snapshot_nodes` deleted) + 1 SQL migration | `JSONBType` (infra/types.py:35), dual-driver convention (20260710_000001:1-4), `create_all` registration pattern (manager.py:520-525) |
| 2 | SnapshotService creation pipeline [**Rev 5: M (was L)** — walk/delete] | **M** (Rev 5) | Single-instance read + harden + summarize + write; tree walk + per-node machinery DELETED in Rev 5 | `get_tree_ids_permanent` (repository.py:527) — **Rev 5: lineage-tag utility only, NOT a capture walk**; dormant `aget_state` read (compact_executor.py:732-734), parallel-pool shape (compaction.py:2867-2935) — Rev 5: single-call deadline-budget only |
| 3 | Summarizer invocation + cost control | **M** | `SNAPSHOT_MODEL` resolver, input clamp, per-tree cap | `_call_summarization_llm` (compaction.py:3325-3440), `_resolve_compaction_model` chain (config.py:2835-2867), `_summarization_timeout_s` (compaction.py:1176) |
| 4 | snapshot_search retrieval [**Rev 5: tag-overlap added**] | **M** | Snapshot corpus + freshness post-filter + R8 typed-tag filter + R10 tag-overlap signal | BM25/cosine/LLM-select stack (skill_search_service.py:45-60 region), `SkillEmbeddingService` (skill_embedding_service.py:537) |
| 5 | Consumption injection [**Rev 5: rename + auto-fallback**] | **M** | `CONTEXT_KIND_SNAPSHOT_DIGEST` + assemble hook + staleness report + R14 auto-fallback result contract | `_make_context_message` stable-id supersede (context_messages.py:130-165), `assemble_context_messages` (:1589), critical-notes truncate pattern (:618-640), `set_metadata_many` (manager.py:3774) |
| 6 | 3 tools + registration + prompt/meta lines [**Rev 5: grants P2-v1**] | **M** | snapshot_tools.py (3 tools); `spawn_hot_instance` (R13 rename) | Full registration chain precedented (skill_tools.py:140-141; service-tools DYNAMIC_TOOL_NAMES precedent, _tool_registry.py:94-109); **Rev 5: per-tool `tools.allow` entries in worker/coder/tester; `spawn_hot_instance` ships in `instance` category** |

**Total: 1×L → 1×M (Rev 5) + 5×M ≈ 4-5 focused days of implementation** across **6 PRs** (below), plus the compaction regression pack for PR2. **Rev 5 reduces total effort materially** by deleting the tree-walk + per-node machinery; the L is gone, and PR4 collapses from L to M. The remaining M is the single-instance orchestration (read → harden → summarize → write → record failure); the persona parametrization in PR2 remains the only invasive touch to battle-tested compaction (one optional argument at `:3429`).

### PR-level breakdown (ordering matters)

| PR | Contents | Depends on |
|---|---|---|
| **PR1** | Extract content-hardening corpus (`_extract_text_from_content` :81, `_is_injected_message` :108, `_has_context_kind` :129, partition/hoist predicates :191-249) into a neutral shared module; swap graph.py's existing lazy import (:1866-1867) to the new home; compaction regression pack green | — (GATE: must land with-or-before PR4) |
| **PR2** | Persona parametrization: optional `system_message`/persona arg at compaction.py:3429-3434; `SNAPSHOT_MODEL` env+yaml chain in config.py mirroring `_resolve_compaction_model` (:2835-2867); config.yaml interpolation | PR1 (optional but cheap to sequence) |
| **PR3** | Storage: `daemon/repositories/snapshot/` (models.py — **2 tables** in Rev 5; `snapshot_nodes` deleted), `daemon/migrations/versions/{ts}_create_snapshot_tables.sql`, import registration before `create_all` (manager.py:525) | — (parallelizable with PR1/2) |
| **PR4** | `SnapshotService` + `SnapshotExecutor` + `snapshot_prompts.py` (R11 8-tuple steering block) + cost caps + trigger lane per Section D ruling + **Rev 5: single-instance capture (P1) + R6/R7/R8/R9/R10/R12/R14 protocol; R13 rename; R15 settings toggle; R16 monitoring metrics** | PR1, PR2, PR3 |
| **PR5** | `snapshot_search`: hybrid retrieval over snapshot corpus + R8 typed-tag filter + R10 tag-overlap signal + pin-with-tests vs skill-search helpers | PR3 |
| **PR6** | Consumption + tools + meta.json grants (**Rev 5: worker/coder/tester per-tool entries; `instance` category auto-grant for `spawn_hot_instance`, ari excluded**) + prompt lines (≈10-17 across 6-7 agents; Sections A5+A6 + R14 auto-fallback; one PR keeps the tool surface atomic) | PR4 |

### Component detail

**A1 — Snapshot schema + migrations (M) — Rev 5: 2 tables.**
Seams: `JSONBType` at daemon/repositories/infra/types.py:35 (dialect-aware JSONB/JSON — exactly the design's assumption); migration convention stated verbatim in the skill-tables header (20260710_000001_create_skill_tables.sql:1-4) with an explicit `-- DOWN` section (:101); SHA-256 content checksums at runner.py:67-68; runner is **SQLite-only** (runner.py:719-727 skips non-SQLite engines). Fresh PG DBs get tables from `SQLModel.metadata.create_all` (manager.py:525) — which runs **every boot**, so new tables appear on existing PG DBs too without touching `_ensure_postgres_columns` (:5099). Requirement: import the snapshot models package before :525 executes (precedent: the `SchemaMigration` import at :520-522). Naming must timestamp-sort after `20260915_212810_create_service_tracking.sql` (latest today). Soft-TEXT `instance_id`/`target_instance_id` (no FK; was `root_instance_id` in Rev 4, renamed in Rev 5 to reflect per-instance capture) matches the documented terminate/revive lifecycle (repository.py:531-546 docstring; instances.parent_id permanent per models.py:61). **Rev 5 ships 2 tables** (`snapshots`, `snapshot_embeddings`); the Rev 4 `snapshot_nodes` table is **deleted** — per-instance capture has no ordered 1:N tree to persist.
Verdict: fully precedented; the design's step-2 ("mirror DDL in `_ensure_postgres_columns`") is **unnecessary for brand-new tables** (create_all covers both fresh and existing PG) — harmless if kept, but the real requirements are the create_all import ordering and the SQLite migration file.

**A2 — SnapshotService creation pipeline (M) — Rev 5: dropped from L.**
Seams verified: **Rev 5 deletes the tree walk entirely.** Capture is `manager.get_instance(target_instance_id) + graph.aget_state({"configurable":{"thread_id": target_instance_id}})` on the target instance only. The dormant read pattern at compact_executor.py:732-734 (verified for both in-memory and cold instances, see B(i)) is reused as-is. Per-instance capture means a single try/except around the read+LLM+write (was per-node in Rev 4). The shared hardening corpus (PR1) doubles as the R6a negative-space exclusion (`context_kind=snapshot_digest` skip).
The M-ness is the orchestration breadth (read → harden-with-R6a → summarize → write → record failure), not risk: worst case is a bad snapshot row, never a corrupted conversation. **Rev 5 reduction rationale:** the walk (`get_tree_ids_permanent`), enumerate-first exclusion, per-node skip-and-record, per-node idempotency/refill, reduce-merge of child briefs, 32-node cap, `truncated` flag are all DELETED. Effort L → M.

**A3 — Summarizer invocation + cost control (M).**
The reuse seam `_call_summarization_llm(prompt, context)` at compaction.py:3325-3440 verified in full: prompt-as-arg ✓, model override via `resolve_compaction_model` (:3349-3356), adaptive per-prompt timeout `_summarization_timeout_s` (:3365, def :1176-1195), HA facade `wrap_langchain_failover` (:3378-3383), never-silent construct fallback (:3384-3395), empty-response guard (:3413-3423), hardcoded persona SystemMessage at **:3429-3432** (the design's :3430 anchor is exact), multimodal-safe text extraction on return (:3440).
Two changes the design understates (see E verdict): (a) it is an **instance method of `ContextCompactor`** (:3325 `self`) requiring `self.llm_config_with_headers` and a `CompactionContext` — reuse means constructing a lightweight compactor with the target instance's LLM config + a synthetic `CompactionContext` (only `.config` is read on this path), or folding the call body into the PR1 shared module; (b) the persona parametrization is genuinely one optional argument (:3429-3434 — the SystemMessage is inlined at the single call site of `_invoke_summarizer_llm`). Cost control: `SNAPSHOT_MODEL` mirrors `_resolve_compaction_model` (config.py:2835-2867: env > yaml > "", pure function, empty string = falsy no-override); **recommend the fallback chain `SNAPSHOT_MODEL > COMPACTION_MODEL > session model`** so an operator who already pinned the cheap tier gets cheap snapshot calls by default — with `""` falling straight to the session model, a 32-node tree is 32 main-model calls.

**A4 — snapshot_search retrieval (M).**
Shared stack verified: skill search is 3-stage pure-Python BM25 → cached-embedding cosine → LLM select with graceful degrade (skill_search_service.py:45-60 design-notes region; "No numpy, no external BM25 library. Per `ensemble.spec`" verbatim :52-53); trigger-query embeddings minted at create AND update (skill_embedding_service.py:11-12, update path :537). `SkillEmbedding` model at skill/models.py:563 (JSONB floats; next class starts :623, so the design's :563-621 range is exact). Snapshot-specific work: corpus assembly (`task_summary` + node digests + `domain_tags`), project+status candidate filter, freshness post-filter. The design's drift hazard (direct helper imports coupling the two rankers) is real — pin-with-tests in the same PR is the right v1 call.

**A5 — Flat-digest consumption injection (M).**
Seams verified: `set_metadata_many` at manager.py:3774 ("ONE SQL statement... prevents torn-state" — the atomic multi-key write the design calls :3779, ±5 drift); `assemble_context_messages` at context_messages.py:1589; `_make_context_message(kind, title, content, id_)` at :130-165 stamps `{"injected_message": True, "context_kind": kind}` and honors stable ids for `add_messages` in-place supersede; the enum is **plain string constants** (:83-107 — `CONTEXT_KIND_SYMPTOM_REPAIR` at :104 with a docstring that says verbatim that this kind places a doc "in the permanently non-selectable / hoisted bucket... survives every later compaction verbatim"); `_make_context_message` does NOT escape — caller must run `escape_for_context_block` (:147-152 docstring; fn at :342). Truncate-with-hint precedent: critical-notes reference truncation appends "… (truncated — project_cn_list for full text)" (:631-636) — the exact shape the design proposes for the ~12k digest cap. Survival of the stamp is verified exhaustively in D-checkpoint: the digest survives **every** compaction path.
One nuance: the digest lands only if the metadata key is written BEFORE the instance's first `assemble_context_messages` pass (turn 1) — spawn-then-write-then-message ordering, which `spawn_instance_from_snapshot` [renamed spawn_hot_instance, R13] controls end-to-end. Feasible.

**A6 — 3 tools + registration + prompt/meta lines (M).**
The registration chain is fully traced (see B(ii) for the exact mechanism). Auth precedents verified: `_check_team_membership` at instance.py:663; completion-watcher zombie mechanism `_register_child_completion_watcher` at instance.py:686 (design's :686-734 exact — supports the tree-restore rejection); `SpawnInstanceInput` BaseModel convention at instance.py:1853; never-raise error strings at :2224-2226 (`return f"ERROR: {error_msg}"`). Prompt-edit surface (4-5 lines × ari/leader per docs/agent-prompt-writing-guide.md) is ordinary prompt work; leader fire-and-forget spawn block exists at agents/leader/workflow.md:660 (heading verbatim "Spawn Instance is Fire-and-Forget").

---

## SECTION B — The Two Open Feasibility Items

### B(i) `aget_state` cold-load for long-terminal instances — **RESOLVED: WORKS, with three named caveats**

Real path, verified end-to-end:

1. `manager.get_instance(id)` → `InstanceLifecycleService.get_instance` (instance_lifecycle.py:3861-3890): in-memory cache fast path (:3877-3879) → cold-load: `ensure_mcp_preloaded` (:3882) → DB row fetch (:3885-3886) → **`KeyError` only if the row is GONE** (:3887-3888) → `_restore_instance` (:3890). **There is NO status filter anywhere on this path** — a COMPLETED/TERMINATED/ERROR/FAILED instance with a live row cold-loads exactly like a live one. This is the same machinery that makes revive-on-message work (revive reuses the same thread; instance_messaging.py:1897-1909 comment).
2. `_restore_instance` (instance_lifecycle.py:3977-4370) rebuilds and **eagerly compiles** the graph, then registers `manager.instances[instance_id] = (graph, meta.agent_dir)` (:4366-4367). It performs **no status change, no message emission, no checkpoint write**.
3. The compiled graph carries the shared checkpointer: `checkpointer=self._checkpointer` (manager.py:2519), where `_checkpointer` is the process-wide `CheckpointerAdapter` from `get_checkpointer` (persistence.py:268-306: PG → PostgresCheckpointerAdapter, else SQLite). Since `thread_id == instance_id` universally (15+ sites; orphan detection depends on it), the restored graph's `aget_state({"configurable": {"thread_id": instance_id}})` reads the SAME checkpoint tables — no rehydration of conversation needed, the saver fetches by thread_id.
4. The compact_executor precedent (compact_executor.py:732-738) does exactly this read and tolerates failure (`except Exception: checkpoint_state = None`, then `(checkpoint_state.values or {})`).

Answers to the specific sub-questions:
- **Does the read require the graph object in memory?** No — it requires *a* compiled graph bound to the shared checkpointer, which cold-load builds on demand. The original in-memory object is irrelevant.
- **Synthetic system prompt reconstruction?** Not involved. That path (persistence.py:634-647 `synthetic-system-{iid}`, rebuild at :976-1105) serves the messages READ API for humans; `aget_state` returns raw `state.values` — the messages channel and last-value channels — with no system prompt (system prompt is not checkpointed by design). For snapshot capture that is precisely what you want: per-node message history, nothing else.
- **Empty/partial state?** A spawned-but-never-dispatched instance has NO checkpoint row (checkpoint lazily created on first `graph.astream`, instance_messaging.py:4195). LangGraph `aget_state` on a missing thread returns an empty snapshot (`values={}`) — the executor precedent's `(checkpoint_state.values or {})` pattern shows the shape. The design's "empty reads → skip-and-record" handles it.
- **After daemon restart?** Same path — restore re-compiles from the CURRENT agent files on disk; irrelevant for reading message state, but see caveat (c).

Caveats (all confirmed in code):
- **(a) Memory pinning:** restore registers the graph in `manager.instances` permanently (:4366-4367). A 32-node tree walk cold-loads and pins up to 32 graphs. The design's own risk note is confirmed; a post-walk eviction pass (pop entries it inserted) is cheap insurance.
- **(b) A real side effect — watchover crash recovery:** `_restore_instance` ends by calling `_recover_watchover_pending_termination` (:4369-4370; def :3892-3975), which for an instance carrying a stale `watchover_pending_termination` marker triggers a **real `terminate_instance` cascade** (:3918-3921). A "read-only" snapshot walk can therefore terminate a corrupted-watchover instance it touches. Narrow, but it means "capture never mutates targets" needs a one-line asterisk.
- **(c) Hard-deleted rows:** tree hard-delete removes instance rows (repository.py:2706-2710 region); those yield `KeyError` at :3887-3888. Per-node try/except with skip-and-record is mandatory (the design already specifies per-node isolation — this is the concrete exception to tolerate).

**Verdict: the dormant-read premise holds, including the cold/restart case the design left unverified.** The compact_executor precedent is even stronger than claimed — it is the same call shape the snapshot executor will use.

### B(ii) DYNAMIC_TOOL_NAMES / `discover_all_tool_names()` pickup — **RESOLVED: 6 files, mechanism fully traced**

How a factory-created tool actually becomes callable, end-to-end (every step verified):

1. **Construction** — `create_instance_tools` (instance.py:1913) builds the full per-instance tool list; factory-created tools are appended inside it (precedent: `skill_tool_list = create_skill_tools(manager, current_instance_id)` :4709-4711).
2. **Registration** — after all tools are built, `scan_tools_for_full_docs(tools)` (called :4877; def _tool_registry.py:454) writes each tool into `_tool_metadata` (name → category), reading the `_tool_category` attribute that the `@register_tool_category("snapshot")` decorator sets (registry :165-186, with the `_tool_category_first_party` anti-spoof marker).
3. **Category resolution** — `list_tools_by_category()` (:274) groups `_tool_metadata`; `resolve_tool_filter` (instance.py:291-408) expands a `tools.allow` entry that names a category into its tool names (:390-396). Both leader and ari use explicit allow-lists (agents/leader/meta.json:13-31 — 15 entries incl. categories `instance`, `critical_notes`; agents/ari/meta.json:10-30 — incl. `job`, `mission`, `service`), so a `"snapshot"` allow entry is required in **both**.
4. **Filtering** — `_apply_tool_filter` (instance.py:4915, invoked :4881) resolves the agent's (version-aware) meta and drops non-allowed tools. `snapshot` is NOT a privileged category, so it needs no `PRIVILEGED_TOOL_CATEGORIES` change (:158-162; note the triple-pin SAME-PR rule :151-157 applies only to privileged-category edits — not triggered here).
5. **Prompt doc block** — `load_tools_doc_for_agent` (loader.py:131-245) resolves the same filter and emits the per-category `## Snapshot` section into the system prompt; display name/doc come from module attrs `CATEGORY_NAME`/`CATEGORY_DOC` on snapshot_tools.py (lookup at registry :818-825, :839-866; precedent skill_tools.py:49-50).
6. **Startup validation** — `DYNAMIC_TOOL_NAMES` (registry :23-110) exists precisely so allow/deny entries for factory-created tools validate before an instance is built; the five `service_*` tools (:105-109) are the freshest precedent for adding factory-tool names.
7. **Frozen-binary safety** — `discover_all_tool_names()` (:403) AST-scans `CATEGORY_MODULES` source files; in frozen builds with zero readable source it falls back to the static `KNOWN_TOOL_NAMES` universe (the 2026-08-20 prod-incident mitigation, :562-584). Adding `"snapshot": "daemon.tools.snapshot_tools"` to `CATEGORY_MODULES` (~:577) makes the AST scan pick the new tools up automatically (`_scan_category_module_sources` descends into factory bodies, :288+).

**EXACT file list for the 3 tools (Rev 5 P2-v1 grants — supersedes Rev 4 leader+ari list):**

| File | Change |
|---|---|
| `daemon/tools/snapshot_tools.py` | NEW: `CATEGORY_NAME`/`CATEGORY_DOC` module attrs; `create_snapshot_tools(manager, current_instance_id, agent_id, version_tag)`; 3 × `@register_tool_category("snapshot")` + `@tool` closures (`snapshot_create`, `snapshot_search`, `spawn_hot_instance` — Rev 5 rename R13) |
| `daemon/tools/_tool_registry.py` | `CATEGORY_MODULES` += `"snapshot": "daemon.tools.snapshot_tools"` (~:577); `DYNAMIC_TOOL_NAMES` += **3 names** (`snapshot_create`, `snapshot_search`, `spawn_hot_instance`) at `:23-110` — **Rev 5 verification rider (d): all three MUST be present** |
| `daemon/tools/instance.py` | import `create_snapshot_tools` (near :241) + call/extend inside `create_instance_tools` (precedent :4709-4711) |
| `daemon/loader.py` | `_ensure_tool_metadata_populated` (:27) += warm import + placeholder construction of `create_snapshot_tools` (maintenancer W1-fix precedent :47-60 — **skipping this pins an EMPTY category into the no-TTL prompt cache on cold boot**) |
| `agents/worker/meta.json` | `tools.allow` += `"snapshot_create"`, `"snapshot_search"` (**Rev 5 — creator**) |
| `agents/coder/meta.json` | same (**Rev 5 — creator**) |
| `agents/tester/meta.json` | same (**Rev 5 — creator**) |
| **NOT touched in v1** | `agents/leader/meta.json`, `agents/ari/meta.json`, `agents/developer[v2]/meta.json` — `spawn_hot_instance` ships in the `instance` category (auto-grant via existing `tools.allow` entry); **ari excluded permanently per user decision** (R13 + P2-v1). **Rev 4's "leader + ari" entries in `tools.allow` are SUPERSEDED.** |

Non-blocking follow-up: regenerate `KNOWN_TOOL_NAMES` via `discover_source_only_tool_names()` (registry :370-372 is the declared regen source of truth) so frozen builds don't false-positive "unknown tool" — pin with a source-discovery test (precedent: tests/job_queue/test_job_answer_tool.py:696-700).

---

## SECTION C — Discrepancy Reconciliation (38 appendix anchors + inline disagreements)

Legend: **A** = design-exploration anchor, **W** = machinery-inventory, **measured** = my read on 6ed47fca. "line-drift" = same symbol, shifted lines.

| # | Symbol/claim | A says | W says | Measured | Verdict |
|---|---|---|---|---|---|
| 1 | `_call_summarization_llm` | :3326 | :3340-3359 | def :3325-3329; model-override body :3349-3356 | **agree-with-drift** — A cites the signature, W the resolution body; same method. A's "prompt-as-arg" claim correct |
| 2 | hardcoded persona | :3430 | :3429-3434 | SystemMessage content :3430-3431 | **agree** (A exact) |
| 3 | `resolve_compaction_model` | :1295 | :1295-1317 | def :1295, body ends :1317 | **agree exact** |
| 4 | adaptive timeout | :1176 | — | :1176-1195 | **architect-right** |
| 5 | parallel pool + budget | :2860-2930 | — | `_budget_remaining` :2811-2815; pool :2867-2935 | **agree-with-drift** (±6) |
| 6 | content-hardening / hoist | :108-152, :81 | partition+sentinel :427-620 | predicates :108-146; `_is_hoisted_injected` :191; `_partition_injected_for_compaction` :217-249; `build_sentinel_replacement` :427-613 | **both-right, different layers** — A cites the predicates, W the seam builder; A's §1.3 phrasing ("compaction preserves... :108-152") conflates predicate-definition lines with the preservation mechanism, but both symbols exist where cited |
| 7 | `_extract_text_from_content` | :81 | — | :81 | **architect-right exact** |
| 8 | trigger machinery (not reused) | :1895-2058 | class :1832; :2096 dedup | support methods :1893-2058; dedup :2096-2098 | **agree** |
| 9 | `compact_state` | :2062 | :2062 | :2062 | **agree exact** |
| 10 | persist seam | :72 | :80+ | `persist_compaction_result` def :72; stamp-only :142-169 | **architect-right** (def exact; W's :80+ also inside) |
| 11 | dormant `aget_state` read | :733-737 | — | get_instance :732, aget_state :734, except :735-738 | **agree ±1** |
| 12 | `/compact` shape | :1754 | :520; reg :1282-1284 | `register_compact_command` :1755; `execute_compact` :520; reg :1281-1284 | **agree ±1** (A's "sync, wrong shape" characterization correct) |
| 13 | per-instance ExecutionGate | :589 | — | docstring step :559; acquire :1007 | **architect-right-with-drift** (±30; claim itself correct — per-instance `asyncio.Lock`, no tree-level serialization) |
| 14 | `get_tree_ids_permanent` | :528 | :527+ | def :527; no-status-filter + depth-256 docstring :531-546 | **agree ±1** |
| 15 | revive-on-send hazard | :1904-1930 | :1897-1924 | comment :1897-1909; terminal-detect :1914-1919; status flip :1924 | **both-right; wanderer tighter** — A self-flagged its drift from the blueprint's older :1486-1510; hazard is real and A's "never message targets" rule stands |
| 16 | `spawn_instance` facade | :6871 | :6870/:6920 | def :6870; `spawn_instance_with_mcp` :7005 | **agree ±1** |
| 17 | atomic metadata write | :3779 | — | `set_metadata_many` :3774 ("ONE SQL statement... prevents torn-state") | **architect-right ±5** |
| 18 | `_ensure_postgres_columns` | :5099 | — | :5099 | **architect-right exact** (but see A1: not needed for new tables) |
| 19 | `CONTEXT_KIND_*` enum | :83-119 | — | constants :83-107 (`SYMPTOM_REPAIR` :104) | **agree** (A's range includes trailing comment) |
| 20 | `_make_context_message` | :130-165 | — | def :130, return :164-165 | **architect-right exact** |
| 21 | critical-notes block precedent | :526-647 | — | truncate-with-hint :618-640 (inside range) | **agree** |
| 22 | `build_shared_context_message` | :1006-1059 | — | def :1006 | **architect-right exact** |
| 23 | `assemble_context_messages` | :1589 | :1589-2090 | def :1589 | **agree exact** |
| 24 | `SpawnInstanceInput` | :1853 | tool :1952 | class :1853; spawn tool :1952 | **agree** (different symbols, both right) |
| 25 | `_check_team_membership` | :663 | deny-by-default :2008 | def :663 | **agree** |
| 26 | completion watcher | :686-734 | — | `_register_child_completion_watcher` :686 | **architect-right exact** (zombie-tree mechanism confirmed) |
| 27 | critical-notes command-shape factory | :292 | :299+ | `create_critical_notes_tools` def :292 | **agree** (A exact; W inside the factory) |
| 28 | skill category-allow precedent | skill_tools.py:141 | — | `@register_tool_category("dynamic-skill")` :140-141 over `skill_search` | **architect-right exact** |
| 29 | `skill_embeddings` JSONB | models.py:563-621 | :563 | class :563; next class :623 | **agree exact** |
| 30 | BM25 no-numpy hybrid | :45-60 | :1-73 | design-notes region :45-60 ("No numpy... per `ensemble.spec`" :52-53) | **agree** |
| 31 | embedding svc | :11-12, :537-549 | :163-201, :537-549 | :11-12 ✓; `update_skill_embeddings` :537 | **agree** (W adds the generate range) |
| 32 | critical-notes lifecycle/soft refs | project/models.py:210-231 | — | `pinned` :215, `superseded_by_id` :218, `detail_ref` :222 | **agree** |
| 33 | `JSONBType` | infra/types.py:35 | — | class :35 | **architect-right exact** |
| 34 | dual-driver migration | 20260710_000001:1-4 | — | verbatim :1-4; `-- DOWN` :101 | **architect-right exact** |
| 35 | `__version__` | __init__.py:3 | — | `__version__ = "0.14.0"` :3 | **agree exact** |
| 36 | only-git-usage claim | doc_commit_service.py:243, :456-462 | — | module is git-bound (validate→stage→commit docstring :1-23); no other daemon git usage surfaced in grep | **architect-right** (spot-checked at module level; the substantive claim — git use is confined, no repo-HEAD cache — holds) |
| 37 | fire-and-forget spawn contract | leader/workflow.md:660-681 | — | heading "Spawn Instance is Fire-and-Forget" :660 | **architect-right exact** |
| 38 | prompt-writing guide | docs/agent-prompt-writing-guide.md | — | exists; referenced project metadata | **agree** |

**Inline / cross-doc discrepancies beyond the appendix:**

39. **"JAFP names 4 public entry points"** (design §10 Q1 and §9): the JAFP invariant doc counts **SIX** public entry points routed through `enqueue_message_job` (docs/architecture/job-as-front-primitive-invariants.md:14-24: POST /messages, external sources, scheduler, send_message tool, job_continue tool, PAUSED cascade-resume). The "4" the architect likely recalls is `JobQueueService.enqueue`'s four canonical *source tags* (`"api"`, `"telegram"`, `"scheduler"`, `"webhook"` — skill_job_dispatcher.py:126-133). **verdict: design-stale on the count**; does not change the ruling in D-JAFP.
40. **W's docstring-drift note** (repository.py:485-486 citing revive at instance_messaging.py:1510-1530): confirmed stale-on-its-face — the live revive block is :1897-1931 (row 15). The docstring is wrong, both A and W right about the live site.
41. **W's Task Context range** (instance.py:92-143): the cap constant `_TASK_CONTEXT_MAX_CHARS = 4000` is at **:89**, formatter :92+. Line-drift only; the 4000-char cap is real and verified.
42. **compaction.py line count "3,902"**: exact on both sides (file is 3,902 lines) — **agree**.

**Net: zero substantive disagreements.** Every architect anchor resolves to the cited symbol within ±30 lines (worst: ExecutionGate :589 vs :559/:1007); every wanderer anchor likewise. The one factual correction is the JAFP entry-point count (row 39), which is doc-level, plus the two line-precision notes (rows 40-41).

---

## SECTION D — Hidden Implementation Risks

### D1. Concurrency: snapshot read vs compaction write — "no pause needed" **CONFIRMED, one cosmetic window**

- All compaction write paths converge on `persist_compaction_result` (seam :72) → `build_sentinel_replacement` (seam :222; graph.py:7504 for the CLE path) → a **single** messages-channel write (`[RemoveMessage(REMOVE_ALL), *injected, *doc_and_tail]`, compaction.py:609-613), followed by a separate `compacted_at` write. The sentinel recipe replaces the ENTIRE channel value in one update — a concurrent `aget_state` therefore observes either the full pre-compaction channel or the full post-compaction channel, **never a torn message list**. The only interleaving window is between the messages write and the compacted_at write, where a snapshotter would read post-compaction messages with a stale `compacted_at` — cosmetic for capture (snapshots don't gate on `compacted_at`).
- `aget_state` is passive (checkpointer read); it takes no lock and mutates nothing. The pause-first-then-quiesce convention exists for checkpoint **writes** (its proven consumer flips state mid-mutation); a read-only capture does not meet the convention's trigger condition. The design's "no pause needed; stamp staleness" claim is **confirmed from code**.
- The design's "two concurrent snapshots of the same tree are safe" also holds: reads don't collide; storage dedup via `root_id+hash` idempotency is the design's own mechanism.
- Residual: the aupdate_state(as_node='agent') mid-turn variant (Variant B) writes from INSIDE a live turn (precall-95 path, seam docstring :105-115) — same coherence argument applies (sentinel first, stamp second), but a snapshotter racing a mid-turn compaction reads the last committed boundary either way. Stale-by-one-turn at worst; the per-node `captured_at` stamp covers it.

### D2. Migration ordering — conventions verified, one design over-statement

- Runner is **SQLite-only** (runner.py:719-727, explicit "Do not fix this to apply .sql files on PostgreSQL"); fresh PG schema comes from `SQLModel.metadata.create_all` (manager.py:525, runs every boot); existing-PG column evolution via `_ensure_postgres_columns` (manager.py:5099; docstring :5099-5110 spells out the division of labor).
- Consequences for the 3 new tables: (a) the SQL migration file serves **SQLite** and the audit trail; (b) PG gets the tables from create_all **on both fresh and existing DBs** — the design's step 2 (mirror the DDL in `_ensure_postgres_columns`) is **unnecessary for new tables** (that helper exists for columns on pre-existing tables; its own docstring says so). Adding CREATE TABLE IF NOT EXISTS there is harmless but redundant; the load-bearing requirement is importing the snapshot models package before manager.py:525 runs (precedent: `SchemaMigration` import :520-522).
- Naming/checksum: `{YYYYMMDD}_{HHMMSS}_create_snapshot_tables.sql` must sort after 20260915_212810 (latest); SHA-256 content checksum recorded at apply (runner.py:67-68, schema_migrations :217); UP **and** DOWN sections per the skill-migration shape (:1-4, :101); never edit an applied migration (checksum).
- SQLite compat: `JSONBType` degrades to JSON (types.py:35-58); the design's 🟢 "no GIN on SQLite" note stands; `domain_tags` containment stays PG-only.

### D3. JAFP lane ruling — **v1: NO JobItem. Row-ledger + in-process asyncio + boot sweep. (The JobItem lane is feasible but not at the price the design's table shows.)**

Precedents compared, all verified:
- **`/compact`** runs as a CommandDispatcher command (registered manager.py:1281-1284) — sync, single-instance, pause→quiesce (:264, :875) — wrong shape for a minutes-long tree walk (design already rejects).
- **`experience()`** dispatches kb-writer work as a JobItem on `system_kb_fifo_queue` via `job_service.enqueue(agent_id="kb-writer", source="experience:{iid}", idempotency_key=…)` (knowledge_tools.py:342-410, queue resolve :359-367, idempotency :386) — a tool-initiated **internal** job, source-tagged, JAFP-compatible.
- **`skill_job_dispatcher`** routes to `system_parallel_queue` with `source="skill_evolution"` (skill_job_dispatcher.py:126-145).
- **JAFP itself** governs *instance-execution* work: the invariant collapses PUBLIC entry points onto `enqueue_message_job` with job_type ∈ {task, message} (invariants doc :14-44). A snapshot job is internal subsystem work — like the two precedents above, it does NOT violate JAFP. **The design's "developer check: lane-compatible" resolves to YES at the JAFP level.**

But the design's chosen lane has a hidden cost the table hides: **`job_processor` understands exactly two job shapes.** `job_type == "message"` jobs are skipped at dispatch (the Task row already exists — job_processor.py:883, :1249); everything else falls into the task branch that **spawns an agent instance** for the job. Both working precedents (kb-writer, skill-keeper) work *because* the job carries an agent. A `job_type='snapshot'` job with no agent fits NEITHER branch: it needs (a) a new dispatch branch in job_processor, (b) a new execution processor, (c) attention to the terminal-token contract (JobFeedbackObserver accepts only completed/error/failed at :316; a wedge means locks held), and (d) per the e2e execution-lane convention (`.agents/tester/rules/ensure.md` gates; judge by lane intersection, not file diff), the full job/task/queue e2e suite.

Ruling:
- **v1 (recommended): Option-C shape** — `snapshot_create` tool → `SnapshotService.capture_async(...)` asyncio task; the `snapshots` row is the durability ledger (`status: running → completed | failed | interrupted`); a **boot sweep** marks orphaned `running` rows `interrupted` (one idempotent query at startup — same *shape* as `JobRecoveryService.recover_on_startup`, scoped to one table). Cost ≈ S. Loses automatic retry of a mid-walk crash; per-node idempotency makes a manual re-run a cheap refill — and snapshots are agent-initiated, so the agent sees the failure and can re-invoke. No e2e lane gates triggered.
- **If the JobItem lane is chosen anyway** (crash-recovery maximalism): budget it as an **L** — new job_type branch + processor + observer tokens + e2e gates — and do NOT ship it in the same PR as the rest of v1.
- Middle option if the team wants queue visibility without the processor: enqueue a *kb-writer-style agent job is not available* (no snapshotter agent in v1 by design) — so there is no cheap version of the JobItem lane. This strengthens the row-ledger ruling.

### D4. Checkpoint interactions — FLAT consumption **HOLDS on every path** (verified exhaustively)

The question: would a `CONTEXT_KIND_SNAPSHOT_DIGEST`-stamped HumanMessage survive every later compaction verbatim?

- **Hoist is keyed on ANY truthy `context_kind`, not a closed enum.** `_has_context_kind` returns `bool(additional_kwargs.get("context_kind"))` (compaction.py:129-146). The write side (`_make_context_message(kind: str, …)`, context_messages.py:130-165) accepts any string; the "enum" is plain module constants (:83-107). A new kind requires **zero compaction changes** — add the constant, pass it. (The design's 🔴 "wrong context_kind value" risk is real only as a *typo/constant-drift* risk — mitigate with the constant import + a preservation unit test, as proposed.)
- **Summarization path:** `_partition_injected_for_compaction` (:217-249, called :2113) splits the channel up-front; context_kind messages land in `preserved_injected` and are hoisted by `build_sentinel_replacement` (:551-556 via `_is_hoisted_injected` :191 — hoists any context_kind message) above the doc, ids intact. The pre-write guard (:558-607) raises rather than silently dropping ids — an extra safety net.
- **Emergency truncation fallback:** `emergency_truncate` (:1688-1767) itself does NOT check context_kind (its Pass-2 truncates human messages to 4000 chars; C1 `pop(0)` drops from the head — where hoisted messages sit). **But it never sees them**: it is invoked on `preserved_msgs` only (:2328-2332 — the selectable pool), and the hoisted set is re-attached VERBATIM afterward (:2345-2351, C3 comment "so they survive emergency truncation"). Digest survives.
- **Precall-95% path:** `_maybe_precall_compact_95` (graph.py:5961, called :7400, ratio :5925) runs the same engine and persists via the same seam (`mid_turn=True` → same `build_sentinel_replacement`). Digest survives.
- **Stamp-only anti-refire:** writes ONLY `compacted_at` (seam :142-169); no message write at all. Digest untouched.
- **CLE-retry path:** `compact_state` at graph.py:7457 → same seam (:7473, :7504). Digest survives.
- **Loop breaker (checked as a non-compaction mutator):** `LoopRepairer.repair` (graph.py:1485+) removes only `detection.loop_messages` — repeated tool-call evidence ids — and re-appends injected messages (:1610-1614). A digest message is not a loop candidate; theoretical exposure only.
- **One genuine accounting caveat:** hoisted tokens sit in the compaction GATE NUMERATOR (`injected_tokens`, :2146-2150 region) and are never compaction-relievable. A ~12k-char digest ≈ ~3k tokens is negligible against the default 700k window (config.py:815+), but N digests (supersede sweeps aside) would permanently inflate the numerator and push the instance toward repeated skip/truncate cycles. The stable-id supersede (`snapshot_digest:{iid}`) plus one-digest-per-instance keeps this bounded — worth an explicit assertion in the injection hook.
- **Escape discipline:** `_make_context_message` does NOT escape (:147-152); the design's "always escape" answer (Q5) is mandatory, and the cap should be applied **after** `escape_for_context_block` (the KV block's W10 lesson: escape expands up to 6×, cap-after-escape with skip-not-truncate semantics — context_messages.py:932-975).

### D5. Token/cost ceilings — verified precedents + concrete recommendations

Measured precedent caps:
- Task Context block: `_TASK_CONTEXT_MAX_CHARS = 4000` (instance.py:89), truncate-with-suffix (:135-141 region).
- Shared Meta KV block: 32k cap **post-escape**, on overflow → WARNING + SKIP the block, never truncate (context_messages.py:932-975).
- Critical-notes reference: bounded truncation with a "...for full text" hint (:618-640).
- Compaction GLOBAL overview: 600-token cap per call (compaction.py:280), whole-doc ceiling 15% of window (:289-291 region constants).

Recommendations:
1. **Consume cap: 12k chars, measured post-escape, tail-truncate with the `snapshot_search` hint** (critical-notes pattern). 12k chars ≈ 3k tokens: ~0.4% of a 700k window, ~2% of a 128k window — negligible per turn, and the hoisted bucket makes it permanent, which is the point. Do NOT skip-on-overflow (KV semantics) — a digest that silently doesn't land defeats warm-start; truncate instead. (D6-ratified in Rev 4 raised this to **~25k tokens, strictly counted** — see design §4.3.)
2. **Per-tree node cap 32 [DELETED in Rev 5]:** the per-instance pivot eliminates the tree-walk; there is no per-node cap because capture is single-instance. The Rev 4 "32 nodes × ~500-token digests ≈ ~16k tokens of storage" estimate is moot. Input cost is bounded by the per-call clamp + single-call deadline budget. **Rev 5 is single-node by construction.**
3. **SNAPSHOT_MODEL chain: `SNAPSHOT_MODEL > COMPACTION_MODEL > session model`.** Mirror `_resolve_compaction_model` (config.py:2835-2867: env > yaml > "", pure, blank-normalizing). Rationale: compaction's "" → session-model fallback is correct *for compaction* (quality matters mid-conversation); for snapshots "" → session model means every capture is a main-model call. Reusing the operator's existing cheap-tier pin (COMPACTION_MODEL) as the intermediate default makes the design's "cheap-tier default" true out of the box. Also stamp the effective model into the snapshot row for later cost forensics.
4. **Per-call input clamp:** cap each capture's extracted tail (e.g. 40k chars ≈ 10k tokens — matches `TRUNCATION_GLOBAL_INPUT_CAP_CHARS = 40_000` at compaction.py:285) before the summarizer; the adaptive timeout (:1176) then sizes correctly.

---

## SECTION E — Verdicts

| Component | Verdict |
|---|---|
| 1. Schema + migrations | **FEASIBLE-AS-DESIGNED** (Rev 5: 2 tables, not 3 — `snapshot_nodes` deleted; drop or mark-optional the `_ensure_postgres_columns` mirror for new tables; the real requirement is the pre-`create_all` model import) |
| 2. SnapshotService creation pipeline | **FEASIBLE-WITH-CHANGES** — the `job_type='snapshot'` JobItem lane as tabled has no processor in the two-shape job pipeline; v1 uses the row-ledger + asyncio + boot-sweep (D3). **Rev 5: scope shrinks further — single-instance capture deletes the tree walk + per-node machinery; effort drops L → M.** Change is containment, not architecture |
| 3. Summarizer + cost control | **FEASIBLE-WITH-CHANGES** — `_call_summarization_llm` is a `ContextCompactor` instance method needing an llm_config + synthetic `CompactionContext` (construct-lightweight or fold into the PR1 shared module); define the `SNAPSHOT_MODEL > COMPACTION_MODEL > session` fallback chain explicitly |
| 4. snapshot_search | **FEASIBLE-AS-DESIGNED** (Rev 5: ADD R8 typed-tag filter + R10 tag-overlap signal; pin-with-tests vs skill-search helpers in the same PR) |
| 5. Flat-digest consumption | **FEASIBLE-AS-DESIGNED** — survival verified on all five compaction/mutation paths; hoist is truthy-keyed so no compaction change is needed; apply the cap post-escape. **Rev 5: tool renamed `spawn_instance_from_snapshot` → `spawn_hot_instance` (R13); R14 auto-fallback adds the warm/cold result contract.** |
| 6. Tools + prompts | **FEASIBLE-AS-DESIGNED** — exact 6-file registration list in B(ii); no privileged-category implications. **Rev 5: grants tighten to worker/coder/tester creators (per-tool `tools.allow`); `spawn_hot_instance` ships in `instance` category (auto-grant, ari excluded permanently). Prompt totals revised to ≈10-17 across 6-7 agents.** |

**Overall verdict.** The design is implementable as scoped: every load-bearing anchor it cites is real on this tree (Section C found zero substantive discrepancies — only line precision and one doc-level count), the two open feasibility questions both resolve in the design's favor (cold-load reads work with named caveats; tool pickup is a fully-precedented 6-file chain), and the flat-consumption lynchpin — a stamped digest surviving all compaction — is verified path-by-path rather than assumed. **Rev 5 reduces the surface further**: per-instance capture deletes the tree walk + per-node machinery; effort drops to ≈4-5 focused implementation days (was 6-8); PR4 effort collapses from L to M. The two-table schema (`snapshots` + `snapshot_embeddings`) is the verified shape; `snapshot_nodes` is deleted. **Highest-risk item (Rev 5 unchanged):** the silent-wrong-digest class — if the content-hardening extraction (PR1) slips behind the first summarizer, failures are confidently-wrong digests, not crashes; my read of the hardening corpus (three-bucket partition, answered-note lifecycle, multimodal flattening) confirms the design's own 🔴 is the correct top risk. **Runner-up risk:** the JobItem-lane assumption in the creation pipeline — cheap to avoid now (row-ledger ruling) and expensive to discover mid-build (new processor + observer tokens + e2e gates). **Rev 5 new risks:** R8 tag-drift across agents (free-form `subsystem:`/`feature:`/`topic:` tags); R14 fail-soft must be regression-pinned; R15 settings-toggle must not gate `spawn_hot_instance`; \bhot word-boundary grep trap for e2e tests.

---

### Anchor spot-check coverage (for the record)

38/38 appendix anchors re-verified, plus: persist seam (:72, :142-169, :222), graph.py precall/CRE sites (:5925, :5961, :7400, :7457, :7473, :7504), loop-repair injected re-append (:1610-1614), compact_executor dormant read + quiesce + gate (:264, :520, :559, :732-738, :860-866, :1007, :1755), instance_lifecycle get_instance/_restore_instance/watchover (:3861-3890, :3977-4370, :3892-3975, :4366-4370), instance_messaging revive (:1897-1931) + compact trigger (:1133), manager create_all/migrations/checkpointer/spawn/metadata (:519-534, :1281-1284, :2459-2461, :2519, :3774, :5099, :6870, :7005), runner SQLite-only (:693-727), checkpoint backend (persistence.py:268-306), repository tree-walk + depth cap (:33, :527-560), context_messages enum/factory/caps/KV (:83-165, :342, :618-640, :932-987, :1589), tools instance.py filter chain (:89, :291-408, :663, :686, :1853, :1913, :4709-4711, :4877-4915, :2224-2226), registry (:23-110, :158-162, :165-186, :274, :288+, :370-424, :454, :525-584, :798-866), loader warm-list + doc block (:27+, :131-245), config model resolver (:2835-2867), knowledge/skill lanes (knowledge_tools.py:342-410, skill_job_dispatcher.py:120-145), JAFP invariants doc (:14-44), e2e rule file (.agents/tester/rules/ensure.md).

---

### Independent Verification Addendum (developer orchestrator, 2026-09-23)

A separate read-only verification instance spot-checked 12 load-bearing claims from these notes against the working tree @ 6ed47fca. Results: **8 PASS, 3 PASS-with-drift, 1 citation defect — no verdict flips.** All Section E verdicts stand as written.

Corrections (supersede the corresponding anchors above where they differ):
- §B(ii) table row `daemon/loader.py`: the warm-import precedent should read — warm list at loader.py:42-63; live entries at :58-63 are `system_log_tools`/`ens_db_tools`/`knowledge_tools` (plus `upgrade_tools`/`db_tools`/`service_tools`); NO `skill_tools` entry exists in the warm list. (The `skill_tools.py:49-50` precedent cited at §B(ii) step 5 is for the CATEGORY_NAME/CATEGORY_DOC module attrs — that citation stands.) The mechanism claim itself is confirmed: def at :27; skipping the warm entry pins an empty category into the no-TTL prompt cache on cold boot.
- §B(ii) step 7 anchors: `CATEGORY_MODULES` dict is at :513-570 (entries :514-569, not ~:577); `KNOWN_TOOL_NAMES` static fallback at :604 (comment block :573-587, not :562-584).
- §B(i) caveat (a): the graph-register line is :4369 (comment block :4366-4367).
- Discrepancy row 39 / §D3: the JAFP six-entry-point table sits at docs/architecture/job-as-front-primitive-invariants.md:22-29 (not :14-24).
- §D3 precedent precision: knowledge_tools.py `experience()` — `_enqueue_experience_job` def :342, `system_kb_fifo_queue` resolve :359-367, `enqueue` call starts :385, idempotency key :392 — confirmed.

Claims confirmed verbatim (highest-leverage): `get_instance` fetch has no status filter, KeyError at :3888; `_restore_instance` pins the restored graph in `manager.instances`; watchover crash-recovery can trigger a real terminate cascade (:3918-3921); job_processor dispatch knows exactly two shapes (`job_type == "message"` skip at :883/:1249; the other branch spawns an agent — no third shape exists); `_has_context_kind` is truthy-keyed on any `context_kind` (:146, no enum membership check); `emergency_truncate` runs only on the preserved/selectable pool (:2328-2332) with hoisted messages re-attached verbatim (:2351); `_call_summarization_llm` is a `ContextCompactor` instance method (:3325) with the hardcoded persona at :3429-3432; both `agents/leader/meta.json` and `agents/ari/meta.json` use explicit `tools.allow` lists containing no "snapshot" entry today. **Rev 5 grants supersedure:** neither `leader` nor `ari` gets `"snapshot"` in `tools.allow` — `snapshot_create` + `snapshot_search` go to **worker, coder, tester** (per-tool entries), and `spawn_hot_instance` is consumed via the existing `instance` category (auto-grant). **`ari` is permanently excluded from `spawn_hot_instance`** per the user-decision recorded in design §10 (verbatim-in-spirit: *"ari manages jobs via job interface; this tool is for agents working directly like leader."*). Rev 5 verification rider (d) on `DYNAMIC_TOOL_NAMES` and verification rider (c) on line-refs re-pinning apply to PR-time development.
