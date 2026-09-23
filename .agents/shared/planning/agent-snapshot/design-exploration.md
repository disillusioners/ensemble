# Agent Snapshot — Design Exploration

**Status:** Rev 4 — discussion-ready (design only — no code changes, no migrations executed). Architecture unchanged; Rev 2 folded in the developer feasibility gate + reviewer critique; Rev 3 recorded the **D1 ratification + hard requirement** and the goal check; Rev 4 records **D2 + D6 ratifications** (user, 2026-09-23, mid-analysis) and completes the goal check.
**Date:** 2026-09-23 (Rev 4)
**Branch:** `plan/agent-snapshot` @ 6ed47fca
**Method:** 6-worker competitive fan-out + dimension analysis, all reports spot-verified by the architect; two independent gates (developer feasibility — 38/38 anchors re-verified, zero substantive discrepancies; reviewer critique); user discussion round 1 (D1, D2, D6 ratified).

## Companion Artifacts

| Artifact | Role |
|---|---|
| **`design-exploration.md`** (this file) | Read first — the decisions, trade-off matrices, and recommendations |
| `machinery-inventory.md` | Verified machinery facts (independent read-only inventory, wanderer) |
| `feasibility-notes.md` | Per-component seams, effort, verdicts, hidden-risk investigations, PR breakdown (§A is the execution blueprint) |

---

## 0. Executive Summary

**The problem.** Every new instance/team starts cold: re-reading guidelines, re-discovering domain context, re-exploring the same material (version-pump rituals, domain test packs). Burning tokens on already-solved exploration.

**⭐ North star (user, verbatim):** *"newly spawned agent ready to work — less thinking, less exploration."* Every decision in this document is checked against it (§10.1).

**The proposal.** An Agent Snapshot system: capture an agent (sub)tree's accumulated working context into durable, searchable storage; future agents warm-start via `spawn_instance_from_snapshot`.

**[USER-FIXED v1] constraints:**
1. **TOOLS ONLY** — `snapshot_create` / `snapshot_search` / `spawn_instance_from_snapshot` surfaced to leader + ari; agent judgment drives when to snapshot/search/consume; no auto-injection, no heuristic detection.
2. **Tree semantics — RATIFIED 2026-09-23 (§10 D1):** a snapshot may spawn "as a TREE (restore structure) or as a single child instance (flat digest)"; the ratified reading is **flatten to ONE snapshot** — structure preserved as inline child briefs (data), no re-instantiated child instances. User's canonical example: a tester that spawned workers produces a **single "tester snapshot" containing the distilled whole**.
3. **The job/task/mission system must remain intact/unchanged** (hard requirement attached to the D1 ratification — audit in §10).

**Headline verdict on the user's hypothesis** ("reuse the context compaction feature as a summarizer with a modified prompt, plus a snapshoter applying it across a tree"):

> **Directionally correct, but the reuse surface is narrower than "the compaction feature."** All three independently-dispatched pipeline analyses converge: the genuinely reusable core is **one method plus its plumbing** — `_call_summarization_llm` (prompt-as-arg, `daemon/compaction.py:3325`; a `ContextCompactor` instance method — lightweight construction required, see §2.3) wrapping the model-override resolution, adaptive timeout, and HA-failover facade — plus the **dormant-instance read pattern** (`aget_state`, developer-verified including cold-load and daemon-restart cases) and the **permanent-lineage tree walk** (`get_tree_ids_permanent`, `daemon/repositories/instance/repository.py:527`). The compactor *proper* — `compact_state`, the persist seam, the token-pressure trigger machinery, the shrink-to-fit prompts, the per-instance ExecutionGate — is structurally unfit and must NOT be reused.

**Recommended architecture (v1):**

| Decision | Recommendation | Confidence |
|---|---|---|
| Creation pipeline | **Approach B+ (modified reuse with extraction discipline)** — new `SnapshotService`/`SnapshotExecutor` reusing `_call_summarization_llm` (persona parametrized, lightweight construction) + shared content-hardening module | High (developer-verified seams) |
| Trigger lane | **Row-ledger + asyncio + boot sweep** (developer ruling; the only lane that satisfies the D1 hard requirement — audit §10) | High |
| Storage | **Main ensemble PG, 3 new tables** (`snapshots` / `snapshot_nodes` / `snapshot_embeddings`), D3-compliant | High |
| Consumption | **FLAT spawn** — digest as persistent, compaction-surviving `context_kind`-stamped `HumanMessage`. Tree mode = child briefs inline, **flattened to one snapshot — RATIFIED 2026-09-23 (§10 D1)**; re-instantiated tree restore rejected (zombie structure) | High (survival developer-verified on all five mutation paths) |
| Staleness | **Metadata compare by default** (age + `daemon.__version__` + post-capture-advance check for live-tree captures), **git-anchor opt-in** (`verify=git`) | High |

---

## 1. Existing Machinery Inventory (verified)

### 1.1 Context compaction — how it works today

| Piece | What it does | Anchor | Reusable for snapshots? |
|---|---|---|---|
| `_call_summarization_llm(prompt, context)` | The LLM call: model-override resolution, `ThinkingChatOpenAI` + `clean_llm_config`, `wrap_langchain_failover`, adaptive timeout, never-silent construct fallback, empty-response guard | `daemon/compaction.py:3325-3440` | ✅ with two changes — it is a **`ContextCompactor` instance method** (lightweight construction or fold into the shared module) and the persona needs parametrizing |
| Hardcoded summarizer persona | `"You are a helpful assistant that summarizes conversations"` SystemMessage inside the reused call | `daemon/compaction.py:3429-3432` | ⚠️ **Must parametrize** (one optional argument — developer-verified feasible) |
| `resolve_compaction_model` | Cheap-model override chain (env `COMPACTION_MODEL` > yaml; pure function, `""` = no override) | `daemon/compaction.py:1295`; resolver `daemon/config.py:2835-2867` | ✅ pattern; **model chain `SNAPSHOT_MODEL > COMPACTION_MODEL > session`** (see §8) |
| Adaptive timeout | Per-prompt-size wall clock | `daemon/compaction.py:1176-1195` | ✅ AS-IS (inside the call) |
| Parallel pool + deadline budget | `Semaphore` + **ordered** `gather` + shared `_budget_remaining` deadline | `daemon/compaction.py:2811-2815, 2867-2935` | ✅ pattern (copy shape; ordered reassembly is the documented invariant) |
| Content hardening | `_is_injected_message` / `_has_context_kind` (user-intent preservation — "Summarizing it would erase user intent"), `_extract_text_from_content` multimodal flattener (silent-garbage fix) | `daemon/compaction.py:108-146`, `:81` | ✅ **MUST share** (extraction gate — §2.3) |
| Trigger machinery | Token-pressure thresholds, 80% arm, dedup, precall estimates | `daemon/compaction.py:1893-2058` | ❌ inapplicable (snapshots are tool-driven) |
| `compact_state` (compactor proper) | Single-instance, quiesce-demanding, **mutates** conversation | `daemon/compaction.py:2062` | ❌ unfit |
| Persist seam | Single sentinel channel write (`[RemoveMessage(REMOVE_ALL), …]`) + separate `compacted_at` stamp; writes INTO the target checkpoint | `daemon/services/_compaction_persist_seam.py:72`, stamp `:142-169` | ❌ **opposite** of capture; dedup-stamp contamination hazard |
| `/compact` command path | Sync single-instance CommandDispatcher op; pause→quiesce (30s) because it WRITES | `daemon/services/compact_executor.py:1755`, quiesce `:264/:875` | ❌ wrong shape (sync, single-node) |
| Input clamp constant | `TRUNCATION_GLOBAL_INPUT_CAP_CHARS = 40_000` | `daemon/compaction.py:285` | ✅ precedent for the per-call snapshot clamp |

### 1.2 Instance / spawning / checkpoint machinery

- **Tree walk:** `get_tree_ids_permanent(root_id)` — BFS over permanent `instances.parent_id` lineage ("survives completion, error, terminate, revive"), depth cap 256, NO status filter by design; docstring explicitly blesses it for whole-tree cascades — `daemon/repositories/instance/repository.py:527`, depth cap `:33`.
- **Dormant read:** `manager.get_instance(id)` + `graph_obj.aget_state(config)` — `daemon/services/compact_executor.py:732-738`. **Developer-verified end-to-end including the cold-load and daemon-restart case** (feasibility-notes §B(i)): `get_instance` has NO status filter (`instance_lifecycle.py:3861-3890`; `KeyError` only if the row is gone, `:3887-3888`); `_restore_instance` (`:3977-4370`) compiles the graph, registers it, and performs no status change / message emission / checkpoint write. Caveats in §9.
- **Revive hazard:** `send_message` to a terminal instance auto-revives it to RUNNING — `daemon/services/instance_messaging.py:1897-1931`. (The older blueprint anchor `:1486-1510` is stale; so is a docstring at `repository.py:485-486` — both superseded.) A snapshotter must NEVER message its targets.
- **Spawn facade:** `manager.spawn_instance(agent_id, parent_id, project_id, instance_name, model, version_tag, …) -> (instance_id, model)` — `daemon/manager.py:6870`.
- **Atomic metadata write:** `set_metadata_many` — "ONE SQL statement… prevents torn-state" — `daemon/manager.py:3774`.
- **Pause-first-then-quiesce** exists for checkpoint **writes**. Developer-verified from code (feasibility-notes §D1): compaction's write paths converge on a **single sentinel channel write**, so a concurrent `aget_state` observes the full pre- or post-compaction channel, **never a torn list** — "no pause needed; stamp staleness" is **confirmed**, with one cosmetic window (post-compaction messages + stale `compacted_at`) that snapshots don't gate on.
- **System prompt is NOT in checkpoints** (by design) — digest placement must go through the context-message seam.

### 1.3 Context injection (the consumption seam)

- `CONTEXT_KIND_*` constants — `daemon/services/context_messages.py:83-107`. `CONTEXT_KIND_SYMPTOM_REPAIR` (`:104`) exists precisely to place a doc in "the permanently non-selectable / hoisted bucket… survives every later compaction verbatim". The hoist is **truthy-keyed on any `context_kind` string** (no closed enum — `daemon/compaction.py:129-146`): a new kind requires **zero compaction changes**.
- `_make_context_message(kind, title, content, id_=None)` — stamps `additional_kwargs={"injected_message": True, "context_kind": kind}`; stable ids make the `add_messages` reducer SUPERSEDE in place — `daemon/services/context_messages.py:130-165`. Does NOT escape — caller must run `escape_for_context_block` (`:147-152`).
- `assemble_context_messages` orchestrator (turn-1 assembly, persistent-vs-ephemeral split) — `daemon/services/context_messages.py:1589`.
- Critical-notes and shared-context blocks travel this seam — `:526-647` (reference-field truncation-with-hint at `:618-640`), `:1006-1059`.
- Compaction hoists `context_kind`-stamped messages verbatim — predicates `daemon/compaction.py:108-146`, partition `:217-249`, hoist `:551-556` via `_is_hoisted_injected` `:191`.

### 1.4 Knowledge systems (coexistence surface — §7)

- **Skills:** `skill_embeddings` (JSONB float arrays, no numpy/BYTEA, 1536-dim typical) — `daemon/repositories/skill/models.py:563-621`. Hybrid search: pure-Python BM25 (k1=1.5, b=0.75, "No numpy, no external BM25 library. Per `ensemble.spec`") + cached-embedding cosine rerank + LLM selection with BM25-only degrade — `daemon/services/skill_search_service.py:45-60`. Trigger queries 3-10, computed at creation AND update — `daemon/services/skill_embedding_service.py:11-12`, `:537`.
- **Critical notes:** leader-gated, lifecycle columns (pinned/superseded), `superseded_by_id` soft self-ref, `detail_ref` pointer pattern — `daemon/repositories/project/models.py:210-231`.
- **RAG `experience()`/`explore()`:** `experience()` dispatches kb-writer work as a JobItem on `system_kb_fifo_queue` (`daemon/tools/knowledge_tools.py:342-410`) — the internal-job precedent. Backend lives in the MCP/kb-writer subsystem (contract-level boundary holds — §7).
- **`.agents/shared/`:** planning dirs + context.md — file-surface convention.
- **Migrations:** runner is **SQLite-only** (`daemon/migrations/runner.py:719-727` — "Do not fix this to apply .sql files on PostgreSQL"); fresh **and existing** PG schema comes from `SQLModel.metadata.create_all`, which runs **every boot** (`daemon/manager.py:525`); `_ensure_postgres_columns` (`:5099`) exists only for columns on pre-existing tables. Checksummed, UP/DOWN convention per `daemon/migrations/versions/20260710_000001_create_skill_tables.sql:1-4, :101`.

### 1.5 Version / git anchors

- `daemon.__version__ = "0.14.0"` — `daemon/__init__.py:3`.
- Git usage in the daemon is confined to `daemon/services/doc_commit_service.py` — no repo-HEAD cache exists (§5).

### 1.6 Job pipeline shape (governs the trigger lane — §2.4)

`job_processor` understands **exactly two job shapes**: `job_type == "message"` jobs are skipped at dispatch (the Task row already exists — `daemon/services/job_processor.py:883, :1249`); everything else falls into the task branch that **spawns an agent instance**. Both working internal-job precedents (`experience()` → kb-writer; `skill_job_dispatcher.py:126-145` → skill-keeper) work *because the job carries an agent*. JAFP itself governs instance-execution work and counts **six** public entry points (`docs/architecture/job-as-front-primitive-invariants.md:22-29`) — a snapshot job is internal subsystem work and does NOT violate JAFP, but a `job_type='snapshot'` JobItem with no agent fits **neither** processor branch.

---

## 2. Decision Point 1 — Creation Pipeline (the headline question)

**Options explored competitively** (same skill, different approaches, one worker each):

- **A — Full reuse:** snapshot capture lives INSIDE the compaction subsystem (new trigger + prompt variant on the existing middleware/executor). No new service.
- **B — Modified reuse (user's hypothesis):** compaction untouched; new snapshot pipeline reusing its LLM-call plumbing with a new prompt + tree walk + own persistence.
- **C — Build new:** clean-room `snapshot_service.py` sharing only the generic `llm_failover` facade + repository patterns.

### 2.1 What each approach found (evidence highlights)

**A (full reuse) — collapses under inspection.** The reusable surface is "~2 of ~15 compaction subsystems" (`_call_summarization_llm` + model override). The compactor proper is single-instance, quiesce-demanding, checkpoint-mutating, wrong-prompt-shaped. Additional hazards unique to A: the `compacted_at` dedup stamp would silently suppress the next legitimate `/compact` on a snapshotted instance; `ExecutionGate` is per-instance with no tree-level serialization; tree TOCTOU without quiescence; prompt-fork drift inside the same file.

**B (modified reuse) — the reuse boundary is REAL but narrow.** The expensive, battle-tested part (failover facade, adaptive timeouts, never-silent fallback, parallel pool with deadline budget) is reachable via `_call_summarization_llm`, which takes an arbitrary prompt (`compaction.py:3325-3440`). Precise classification: reuse as-is = the call + model-override + timeout; reuse pattern = parallel pool; parametrize = the hardcoded persona (`:3429-3432`); NEW = prompts, tree walk, artifact, executor; NOT reused = persist seam, trigger machinery. B's weakest point: the persona parametrization + instance-method construction touch a 3,902-line battle-tested file.

**C (build new) — cleanest shape, but re-learns hard-won behavior.** The persist-seam problem disappears entirely (additive writes only). But compaction's content-hardening corpus (~150-250 lines of subtle, bug-derived behavior) would need re-learning; skipping it yields **confidently-wrong digests** (silent garbage). C's mitigation is precedented: `graph.py:1866-1867` already lazily imports `_extract_text_from_content` — extracting predicates + flattener into a neutral shared module converts "re-learn" into "import". C's gate: **shared-module extraction lands BEFORE the first snapshot summarizer.**

**Convergence (all three):** walk = `get_tree_ids_permanent`; read = `aget_state` (no revive, no pause — now developer-confirmed, §1.2); never `send_message` to targets; facade-based LLM invocation; per-node failure isolation; cheap-tier model default.

### 2.2 Five-axis comparison (normalized: **higher = better on every axis; Risk and Cost are inverted so higher = safer/cheaper**)

Weights: Complexity 20% · Scalability 20% · Maintainability 25% · Risk 20% · Cost 15%.

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Weighted |
|---|---|---|---|---|---|---|
| A: Full reuse | 2 (fork + sink + tree lock + prompt plumbing despite "reuse" label) | 2 (serialized per-node engine, no batching, O(N) LLM calls) | 3 (parallel codepath; `/compact` regression surface) | 2 (dedup contamination, tree TOCTOU, gate mismatch, prompt drift) | 3 (per-node LLM cost, no budget enforcement) | **2.40** |
| **B: Modified reuse** | 2 (plumbing reused wholesale; ~500-700 LOC new) | 4 (bounded by chunk_concurrency + lane caps) | 4 (follows compact_executor precedent; second consumer of compaction internals) | 2 (read-only on targets; two contained touches) | 3 (N conversations as LLM input; cheap-tier chain + budget) | **3.05** |
| C: Build new | 3 (new service+repo+tool wiring; no trigger/sentinel inheritance) | 4 (additive writes, semaphore-capped) | 3 (single-purpose service; second summarizer codepath until extraction) | 2 (read-only; worst case = bad snapshot row, never corrupted conversation) | 3 (token cost approach-invariant; one-time re-derivation) | **3.00** |

B and C land within noise (3.05 vs 3.00). Applying the tie-break sequence (equal weighted totals → best Risk score → best Complexity score): both tie at Risk 2, and **B's Complexity score (2) beats C's (3) under the higher-is-better convention — B's raw complexity is genuinely lower** (the plumbing is imported rather than re-derived). B edges C. The deeper reading: **each report's weakest point is cured by the other's mitigation**, which is exactly what the recommendation below adopts.

### 2.3 Recommendation — **Approach B+ (modified reuse with extraction discipline)** [PROPOSED]

A new `SnapshotService` + `SnapshotExecutor` (C's clean service shape — sibling of `compact_executor.py`) that:

1. **Reuses `_call_summarization_llm`** (B's real reuse boundary) with two developer-verified changes: (a) it is a `ContextCompactor` **instance method** — construct a lightweight compactor carrying the target instance's LLM config + a synthetic `CompactionContext` (only `.config` is read on this path), or fold the call body into the PR1 shared module; (b) the persona parametrized as **one optional argument** at `compaction.py:3429-3434` (the SystemMessage is inlined at a single call site). A snapshot-side wrapper pins the call shape so later compaction signature drift breaks loudly.
2. **Extracts the content-hardening corpus** (`_is_injected_message`, `_has_context_kind`, `_extract_text_from_content`, partition/hoist predicates) into a neutral shared module (C's gate — converts silent-wrong-digest risk into an import; `graph.py:1866-1867` is the existing precedent), swapping graph.py's lazy import to the new home.
3. **Owns its prompts** (new `snapshot_prompts.py`): durable-knowledge extraction (decisions, gotchas, conventions, open threads, artifact refs) — NOT compaction's next-turn-continuity contract. **Quality steering (D6):** the prompt steers toward GOOD, well-chosen context — the ~25k-token ceiling is a ceiling, not a target; padding toward it is a prompt-level failure.
4. **Model chain `SNAPSHOT_MODEL > COMPACTION_MODEL > session model`** (mirroring `_resolve_compaction_model`, `daemon/config.py:2835-2867`): an operator who already pinned the cheap compaction tier gets cheap snapshot calls by default; a bare `""` must NOT fall straight to the session model (that would make a 32-node tree 32 main-model calls). Stamp the effective model into the snapshot row for cost forensics.
5. **Never touches** the persist seam, trigger machinery, or `compact_state`.

**Assumption that would flip this:** if compaction enters a heavy-refactor cycle (making any shared-module/persona touch expensive), C's clean-room with same-PR extraction becomes the winner — the delta is one extraction PR either way.

### 2.4 Pipeline semantics (agreed by all three approaches + developer rulings)

- **Trigger [PROPOSED, user ratifies — §10 D3]:** `snapshot_create` tool → `SnapshotService.capture_async(...)` asyncio background task; the `snapshots` row **is** the durability ledger (`status: running → completed | failed | interrupted`). **Developer ruling (feasibility-notes §D3):** the JobItem lane has no home — `job_processor` knows exactly two shapes (§1.6) and a no-agent `job_type='snapshot'` job fits neither; retrofitting it means a new dispatch branch + execution processor + terminal-token contract + full e2e lane gates ≈ an L-sized PR. **v1 = row-ledger; JobItem lane deferred** (and now additionally constrained-blocked by the D1 hard requirement — audit, §10).
- **Restart recovery:** a **boot sweep** marks orphaned `running` rows `interrupted` (one idempotent startup query — same shape as `JobRecoveryService.recover_on_startup`, scoped to one table). Lost: automatic retry. Kept: per-node idempotency makes a manual re-invoke a cheap refill, and snapshots are agent-initiated — the agent sees the failure and can re-invoke.
- **Walk:** BFS via `get_tree_ids_permanent` (depth-256 traversal cap; the binding constraint is the 32-node capture cap, §8); enumerate-first (children spawned after enumeration are excluded); terminated-but-checkpointless children (spawned, never dispatched — no checkpoint row exists; `aget_state` returns empty `values={}`) → skip-and-record.
- **Live vs terminal trees:** terminal = exact capture; live = last-committed-boundary read, stamped per-node `captured_at`. No-pause is **developer-confirmed from code** (§1.2): the sentinel-write design means a racing read never sees a torn channel.
- **Concurrency:** reads don't mutate → two concurrent snapshots of the same tree are safe (idempotency key `root_id+hash` handed to storage for dedup). LLM parallelism bounded by semaphore + shared deadline budget.
- **Failure:** per-node isolation (one dead node never kills the tree); failed nodes recorded with error clauses; re-run refills missing nodes. `KeyError` on hard-deleted instance rows (`instance_lifecycle.py:3887-3888`) is the concrete exception the per-node try/except must tolerate.
- **Cost caps — COMMITTED (§8):** 32 nodes/tree (truncate-with-flag, not silent), 40k chars/call input clamp (matches `TRUNCATION_GLOBAL_INPUT_CAP_CHARS`, `compaction.py:285`), 600s per-tree wall clock, fail loud on breach; cheap-tier model chain default.

```mermaid
flowchart TD
    %% Entry — daemon/tools/snapshot_tools.py
    T1["snapshot_create tool call (leader or ari agent)"] --> A1{"Caller-owns-target check (transitive descendant via parent_id)"}

    %% Trigger — row-ledger lane (developer ruling D3: no JobItem in v1)
    A1 -->|"pass"| Q1["SnapshotService: insert snapshots row status=running + asyncio background task"]
    A1 -->|"reject"| X1["Auth failure"]
    Q1 -.->|"daemon restart mid-walk"| R1["Boot sweep: orphaned running rows -> interrupted"]
    R1 -->|"agent re-invokes; per-node idempotent refill"| T2

    %% Executor — BFS via get_tree_ids_permanent (repositories/instance/repository.py:527)
    T2["Tree walk over instances.parent_id (BFS, depth-capped)"] --> S1

    subgraph SAFE["READ-ONLY SAFETY"]
        S1["Per node: manager.get_instance + graph.aget_state"]
        H1["REVIVE HAZARD avoided: never send_message to targets"]
        S1 -.- H1
    end

    %% Hardening helpers: _is_injected_message, _has_context_kind, _extract_text_from_content
    S1 --> F1["Filter + flatten (shared hardening module, PR1 extraction)"]
    %% _call_summarization_llm (compaction.py:3325) — prompt as arg, persona parametrized, SNAPSHOT_MODEL chain
    F1 --> L1["Per-node summarization LLM call"]
    L1 -->|"node fails"| E1["Failed node: status=failed + error clause, walk continues"]
    L1 --> M1{"Walk complete?"}
    E1 --> M1
    M1 -->|"no, next node"| S1
    M1 -->|"yes"| G1["Reduce merge: root digest + ordered child briefs"]
    G1 --> W1["Write snapshot rows (snapshots, snapshot_nodes, snapshot_embeddings)"]
    W1 --> D1["Row status=completed; report to caller with snapshot_id"]

    classDef hazard fill:#fff3f3,stroke:#c0392b,color:#000000
    class H1 hazard
```

---

## 3. Decision Point 2 — Storage & Data Model

### 3.1 Options (normalized: **higher = better; Risk/Cost inverted — higher = safer/cheaper**)

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Weighted |
|---|---|---|---|---|---|---|
| **Main PG, 3 tables (D3)** | 4 | 4 | 5 | 4 | 4 | **4.25** |
| Separate SQLite store | 3 | 2 | 2 | 2 | 3 | 2.40 |
| File/blob store + index table | 2 | 4 | 2 | 3 | 3 | 2.75 |
| Main PG + pgvector | 2 | 5 | 3 | 2 | 3 | 3.00 |

**Recommendation: main ensemble PG (PostgreSQL primary, SQLite-compatible), 3 new tables — D3 followed, no deviation warranted.** The skill subsystem proves the shape in production: 1536-dim float arrays live happily as JSONB; snapshot digests are text, orders of magnitude smaller. pgvector adds an extension dependency for a problem JSONB+cosine already solves at v1 volumes.

### 3.2 Schema

- **`snapshots`** (header + root digest + filterable search metadata): `id`, `project_id` (FK), `created_by_agent_id`, `root_instance_id` (soft TEXT — no FK: the terminate/revive lifecycle makes hard FKs wrong, per the `superseded_by_id` precedent at `project/models.py:215-222`), `title`, `task_summary` (BM25 corpus), `domain_tags` (JSONB), `status` (**`active` | `superseded` only — see below**), `supersedes_snapshot_id` (soft self-ref), `repo_path`, `vcs_type`, `git_sha`, `git_branch`, `git_dirty`, `runtime_version`, `effective_model` (cost forensics), `truncated` (bool — set when the 32-node cap engaged), `node_count`, `digest` (JSONB — **stored knowledge, unbounded by the consume-side ~25k-token ceiling**, with a `refs`/`artifacts` section, see §10.1 D6), `created_at`.
- **`snapshot_nodes`** (tree members): `id`, `snapshot_id` (FK CASCADE), `parent_node_id` (soft self-ref), `instance_id` (soft TEXT), `agent_id`, `role`, `depth`, `position`, `digest` (JSONB), `status` (`ok | failed | skipped`), `captured_at`.
- **`snapshot_embeddings`** (mirrors `skill_embeddings` exactly): `id`, `snapshot_id` (FK CASCADE), `trigger_query` (≤512), `embedding` (JSONB floats).

**Status vs freshness (dead-state resolution):** the stored `status` enum is a **lifecycle** property with a real writer (`active` on create; `superseded` when a newer snapshot of the same root explicitly supersedes). `fresh | stale | expired` are **computed at query time** from `age_days` / `runtime_version` thresholds (§5) — never stored, so there is no dead `expired` column state needing a flip mechanism.

**Why 3 tables:** the ordered 1:N tree and the 1:N vectors don't fit one row; header stays narrow + indexed while digests stay JSONB. Flattening the tree into one header blob kills per-node queries (anti-pattern flagged).

```mermaid
erDiagram
    projects ||--o{ snapshots : "project_id"
    snapshots ||--o{ snapshot_nodes : "snapshot_id (CASCADE)"
    snapshots ||--o{ snapshot_embeddings : "snapshot_id (CASCADE)"
    snapshots |o--o| snapshots : "supersedes_snapshot_id (soft)"
    snapshot_nodes |o--o{ snapshot_nodes : "parent_node_id (soft)"
    instances |o..o{ snapshot_nodes : "instance_id (soft TEXT, no FK)"

    snapshots {
        string id PK
        string project_id FK
        string created_by_agent_id
        string root_instance_id
        string title
        text task_summary
        jsonb domain_tags
        string status
        string supersedes_snapshot_id
        string repo_path
        string vcs_type
        string git_sha
        string git_branch
        bool git_dirty
        string runtime_version
        string effective_model
        bool truncated
        int node_count
        jsonb digest
        string created_at
    }
    snapshot_nodes {
        string id PK
        string snapshot_id FK
        string parent_node_id
        string instance_id
        string agent_id
        string role
        int depth
        int position
        jsonb digest
        string status
        string captured_at
    }
    snapshot_embeddings {
        string id PK
        string snapshot_id FK
        string trigger_query
        jsonb embedding
    }
```

### 3.3 Migration path (simplified per developer ruling — feasibility-notes §D2)

1. New `daemon/migrations/versions/{YYYYMMDD}_{HHMMSS}_create_snapshot_tables.sql` — serves **SQLite + the audit trail**; timestamp must sort after `20260915_212810_…` (latest today); UP **and** DOWN sections; SHA-256 content checksum recorded at apply (`runner.py:67-68`); runner is SQLite-only by design (`runner.py:719-727`).
2. **PG needs no migration-mirror step for brand-new tables:** `SQLModel.metadata.create_all` runs every boot (`manager.py:525`) and creates missing tables on both fresh AND existing PG databases. ~~Mirror DDL in `_ensure_postgres_columns`~~ — **unnecessary** (that helper exists for columns on pre-existing tables). The load-bearing requirement is **importing the snapshot models package before `manager.py:525` executes** (precedent: the `SchemaMigration` import at `:520-522`).
3. SQLModel package `daemon/repositories/snapshot/` (`models.py`, `repository.py`) with `JSONBType` (`daemon/repositories/infra/types.py:35` — degrades to JSON on SQLite).
4. No backfill — brand-new tables.

### 3.4 Search (D4 parallel, not duplicate)

`snapshot_search` parallels the skill hybrid: **shared** — pure-Python BM25 + cached-embedding cosine rerank + LLM selection with BM25-only degrade; **snapshot-specific** — corpus = `task_summary` + node digests + `domain_tags`; candidate filter by project + `status='active'`; freshness post-filter after ranking; no usage-metrics/A-B in v1. Trigger queries generated from `task_summary` + 2-3 node digests (grounds them if summaries are thin).

- **Embedding lifecycle reconciled:** v1 snapshots are **immutable** (supersede, never update) — embeddings are computed **at creation only**; D4's "computed at creation AND update" maps to supersession minting a fresh row with fresh embeddings.
- **Search-side LLM cost is bounded by candidate count [COMMITTED]:** the LLM-selection stage runs only over the **top-20** hybrid-ranked candidates (hard cap); beyond K the ranking degrades to the BM25+cosine order — mirroring skill_search's graceful-degrade shape.
- **Drift hazard:** direct import of skill-search helpers couples the two ranking behaviors — pin with tests in the same PR (or extract `text_search_common.py` when a third consumer appears).

---

## 4. Decision Point 3 — Consumption (tree restore vs flat digest)

### 4.1 Options (normalized: **higher = better; Risk/Cost inverted — higher = safer/cheaper**)

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Weighted |
|---|---|---|---|---|---|---|
| **FLAT** — one instance, digest as persistent stamped HumanMessage | 4 | 3 | 4 | 4 | 4 | **3.80** |
| TREE RESTORE — re-spawn parent + children | 1 | 2 | 1 | 1 | 2 | 1.35 |
| HYBRID — flat + deferred subtree expansion | 3 | 3 | 3 | 3 | 3 | 3.00 |

### 4.2 Why tree restore is rejected (structural, not preferential)

The snapshot's children **already finished their work**. Re-spawning them produces a **zombie tree**: no pending task, no LLM wake. The completion watcher registers only when a child task/message id exists (`daemon/tools/instance.py:686` — `_register_child_completion_watcher`); the parent's `waiting_children` gate never flips; the parent either idles forever or someone must hand-assign new tasks — which contradicts the feature's purpose.

**RATIFIED reading (user decision, 2026-09-23 — §10 D1):** structure **preserved as data** — root digest + ordered per-child briefs flattened into **ONE snapshot and ONE spawned instance's context** — never re-instantiated as live child instances. User's canonical example: a tester that spawned workers produces a **single "tester snapshot" containing the distilled whole**. The ratification carries a hard requirement: **the job/task/mission system must remain intact/unchanged** (audit in §10 — satisfied).

### 4.3 Recommendation — FLAT for v1; "tree" = flatten to one [RATIFIED 2026-09-23 — §10 D1]

- `mode="flat"`: root digest block. `mode="tree"`: root digest + ordered per-child briefs, inline — same spawn mechanics, only payload shape differs; either way **one snapshot, one spawned instance**.
- **Digest placement (developer-verified on every path):** during spawn, atomically write `instance_metadata["snapshot_digest"]` (`set_metadata_many`, `manager.py:3774`) **before** returning the instance_id (turn-1 ordering is fully under the tool's control); on TURN 1, `assemble_context_messages` (`context_messages.py:1589`) reads it and emits the block via `_make_context_message(kind=CONTEXT_KIND_SNAPSHOT_DIGEST, id_=f"snapshot_digest:{instance_id}")`. **Feasibility-notes §D4 verified the stamp survives all five compaction/mutation paths** — summarization partition+sentinel, emergency truncation (which never sees hoisted messages and re-attaches them verbatim, `compaction.py:2328-2351`), precall-95%, stamp-only, and CLE-retry — plus the loop breaker. The hoist is truthy-keyed, so **zero compaction changes** are needed.
- **Escape-then-cap ordering [developer-verified discipline; D6-ratified ceiling]:** `_make_context_message` does NOT escape (`:147-152`); run `escape_for_context_block` FIRST (escaping can expand content up to ~6× — the KV-block lesson, `context_messages.py:932-975`), then enforce the **hard ceiling of ~25k tokens, strictly counted** (D6, 2026-09-23), tail-truncating with a `snapshot_search`-for-full-body hint (critical-notes reference-truncation shape, `:618-640`). Truncate, never skip — a digest that silently doesn't land defeats warm-start. **The cap bounds INJECTION, not knowledge** — the full digest (with refs/artifacts) persists in the DB and is fetchable (§10.1 D6).
- **Hoisted-token accounting (updated for the 25k ceiling):** hoisted tokens sit in the compaction gate numerator and are never compaction-relievable. A full 25k-token digest ≈ ~3.6% of a 700k window (fine) but ≈ ~20% of a 128k-class window — permanently. Hence D6's **ceiling-not-target semantics**: the prompt steers toward well-chosen context; the stable-id supersede + one-digest-per-instance keeps N-digest inflation bounded; assert the ceiling in the injection hook.
- **System prompt is forbidden placement** (not checkpointed — lost across pause/resume, invisible to the agent's reasoning).
- **`snapshot_search` returns metadata + digest preview only** — the full body is read at spawn time. Keep the surfaces distinct.
- **Hybrid deferred:** a future `spawn_subtree_from_snapshot` is cleanly boxed as v2 (would need re-approval against the D1 hard requirement).
- **Cross-project isolation:** `snapshot_create` stamps `project_id` from the **target tree's root instance**, not the caller's; `snapshot_search` scopes to the caller's project by default; `spawn_instance_from_snapshot` validates `snapshot.project_id` matches the spawn's project unless explicitly overridden — preventing cross-project digest leakage through a shared leader.
- **Poisoning posture (see 🔴 §9):** the digest is permanently hoisted and trusted by warm-started agents — mitigations are mandatory, not optional.

```mermaid
flowchart TD
    %% Entry — spawn_instance_from_snapshot(snapshot_id, mode, agent_id, task)
    C1["spawn_instance_from_snapshot tool call"]
    %% tools/instance.py:663
    A1{"Auth: _check_team_membership"}
    X1["Auth failure"]
    R1["Read snapshot row (digest, child briefs, staleness stamps)"]
    %% age_days + runtime version + post-capture-advance (default) / git HEAD compare (opt-in verify=git) — no LLM
    S1["Staleness compute - sync, no LLM"]
    M1{"Mode dispatch"}
    F1["flat: root digest block"]
    %% children flattened to briefs — NEVER re-spawned (D1 ratified 2026-09-23)
    TR1["tree: root digest + child briefs inline - flatten to ONE instance"]
    %% manager.py:6870 — existing spawn lane, unchanged (D1 hard requirement)
    SP1["manager.spawn_instance"]
    %% manager.py:3774 set_metadata_many
    AW1["Atomic write: instance_metadata snapshot_digest"]
    RET1["Return instance_id + staleness_report"]
    %% workflow.md:660-681
    SM1["Caller: send_message(instance_id, task) - fire-and-forget"]
    %% context_messages.py:1589
    AC["assemble_context_messages reads snapshot_digest"]
    %% stable id snapshot_digest:{iid} — escape FIRST, then ~25k-token hard ceiling (D6)
    MC["_make_context_message kind=snapshot_digest"]
    %% compaction.py:108-152 hoisted bucket — verified all five mutation paths
    SC["Stamped message survives all compaction"]
    W1["New instance starts WARM (digest in turn 1, task follows)"]
    %% tools/instance.py:686 — no task, watcher never registers, parent gate never flips
    RJ["TREE RESTORE - rejected; D1 RATIFIED 2026-09-23: flatten"]

    subgraph TURN1["TURN 1 — CONTEXT ASSEMBLY"]
        AC
        MC
        SC
    end

    C1 --> A1
    A1 -->|"pass"| R1
    A1 -->|"reject"| X1
    R1 --> S1
    S1 --> M1
    M1 -->|"flat"| F1
    M1 -->|"tree"| TR1
    F1 --> SP1
    TR1 --> SP1
    SP1 --> AW1
    AW1 --> RET1
    RET1 --> SM1
    SM1 --> AC
    AC --> MC
    MC --> SC
    SC --> W1
    M1 -.-> RJ

    linkStyle 16 stroke:#c0392b
    classDef rejected fill:#fff3f3,stroke:#c0392b,color:#000000
    class RJ rejected
```

---

## 5. Decision Point 4 — Staleness Verification

### 5.1 Options (normalized: **higher = better; Risk/Cost inverted — higher = safer/cheaper**)

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Weighted |
|---|---|---|---|---|---|---|
| **(b) Freshness metadata** — age_days + `daemon.__version__` vs snapshot stamp | 4 | 5 | 5 | 4 | 5 | **4.60** |
| (a) Git-state anchor — `git rev-parse HEAD` vs snapshot SHA | 3 | 4 | 3 | 3 | 3 | 3.20 |
| (c) Content-hash spot-check — hash files the digest references | 1 | 2 | 1 | 3 | 1 | 1.60 |

### 5.2 Recommendation — (b) default + (a) opt-in; skip (c)

- **Default (every consume call, sync, no LLM, no subprocess):** compare snapshot `runtime_version` stamp vs `daemon.__version__` (`daemon/__init__.py:3`) + `age_days` vs a freshness ceiling. **Plus (live-tree captures, §10.1 D7 adjustment):** a **post-capture-advance check** — one cheap DB read over the captured nodes' soft `instance_id` refs; if any captured node has since transitioned to a terminal state, emit `"tree advanced post-capture"` in warnings. This catches the specific live-tree hazard (the work moved on after the digest froze) that age/version cannot see.
- **Opt-in (`verify=git`):** `git rev-list --count <snapshot_sha>..HEAD` + diverged-file count. Git usage in the daemon today is confined to `doc_commit_service.py` — a daemon-wide repo-HEAD anchor is a new coupling; keep opt-in.
- **Skip (c):** digests are working narratives, not file manifests.

**`staleness_report` shape** (returned by both `snapshot_search` and `spawn_instance_from_snapshot`):

```python
{
  "snapshot_age_days": float,
  "freshness": "fresh" | "stale" | "expired",   # computed, never stored
  "warnings": ["runtime version drift: 0.13.10 → 0.14.0",
               "tree advanced post-capture (live-tree snapshot)", ...],
  "repo_state": {            # populated only when verify=git
    "snapshot_head": "<sha>", "current_head": "<sha>", "diverged_files": int
  }
}
```

Verification runs inline in the tool body. The report is surfaced to the spawning agent — **the agent decides whether to trust** (no silent override), and the leader prompt line (§6.2) mandates a drift note in the dispatch when stale.

---

## 6. Tool Surface + Prompt Updates

### 6.1 Signatures and registration [PROPOSED]

Convention: Pydantic `BaseModel` + `Annotated[..., Field]`, matching `SpawnInstanceInput` (`daemon/tools/instance.py:1853`); dict-with-`error` returns matching the command-shape factory (`daemon/tools/critical_notes.py:292`); error strings, never raises (`instance.py:2224-2226`).

```python
class SnapshotCreateInput(BaseModel):
    target_instance_id: str          # root of the subtree; tool validates caller-owns (self or transitive descendant via parent_id)
    scope: Literal["self", "tree"] = "tree"
    name: str                        # e.g. "version-pump-taskpack-after-v0.13.9"
    tags: list[str] = []
    include_message_history: bool = True   # per-node tail, capped (§10 D5)

class SnapshotSearchInput(BaseModel):
    query: str
    project_id: str | None = None    # None = current instance's project
    tags: list[str] = []             # all-of filter
    freshness_max_age_days: int | None = None
    limit: int = 10                  # 1..50

class SpawnFromSnapshotInput(BaseModel):
    snapshot_id: str
    mode: Literal["flat", "tree"]    # tree = flatten child briefs into ONE instance (ratified §10 D1)
    agent_id: str
    task: str                        # self-contained; digest lands BEFORE this in context
    instance_name: str | None = None
    model: str | None = None         # mirrors spawn_instance fallback semantics
    verify: Literal["metadata", "git"] = "metadata"   # staleness check depth
```

Returns: `{"snapshot_id", "status", "node_count", "truncated", "error"}` / `{"results": [{snapshot_id, name, tags, freshness, age_days, summary}], "error"}` / `{"instance_id", "snapshot_id", "staleness": {...}, "error"}`.

- **Home:** `daemon/tools/snapshot_tools.py` — `CATEGORY_NAME`/`CATEGORY_DOC` module attrs + `create_snapshot_tools(manager, current_instance_id, agent_id, version_tag)` factory + 3 × `@register_tool_category("snapshot")` + `@tool` closures.
- **Registration — the exact 6-file chain (developer-traced, feasibility-notes §B(ii)):**
  1. `daemon/tools/snapshot_tools.py` — NEW (above).
  2. `daemon/tools/_tool_registry.py` — `CATEGORY_MODULES` += `"snapshot": "daemon.tools.snapshot_tools"` (dict at `:513-570`); `DYNAMIC_TOOL_NAMES` += the 3 names (`:23-110`).
  3. `daemon/tools/instance.py` — import + call inside `create_instance_tools` (precedent: skill tools at `:4709-4711`).
  4. `daemon/loader.py` — **warm-list entry** (`:42-63`) — ⚠️ **skipping this pins an EMPTY category into the no-TTL prompt cache on cold boot**.
  5. `agents/leader/meta.json` — `tools.allow` += `"snapshot"` (15 explicit entries today).
  6. `agents/ari/meta.json` — `tools.allow` += `"snapshot"`.
  Non-blocking follow-up: regenerate `KNOWN_TOOL_NAMES` via `discover_source_only_tool_names()` (registry `:370-372`) so frozen builds don't false-positive — pin with a source-discovery test.
- **Auth:** create = caller-owns-target (transitive descendant check); search = project-scoped open; spawn-from = `_check_team_membership` (`daemon/tools/instance.py:663`), same TOCTOU caveat as `spawn_instance`.

### 6.2 Prompt updates — line budget [original user-fixed "4-5 lines each per agent" RELAXED by D2 ratification, 2026-09-23: **8-12 lines per agent allowed** ("no need to be too strict")]

The original constraint produced a real tension (the repo's 3-file prompt convention needs ~8-12 lines per agent to cover when-to-create / when-to-search / when-to-spawn-from). **The user resolved it on 2026-09-23: the standard minimal 3-file pattern (~8-12 lines/agent) is authorized.**

- **Adopted pattern (Option 2 from earlier revisions):** per agent — a `tools_note.md` `## Snapshot Tools` section (the canonical tool-usage home, ~5-6 lines: the three tools + never-snapshot-mid-flight + stale-digest drift note), plus one guideline line in `rule.md` and one trigger line in `soul.md`/`workflow.md` (leader's warm-start variant sits inside the fire-and-forget block, `agents/leader/workflow.md:660-681`). Example ari guideline: *"Before delegating recurring-shape work I run `snapshot_search`; if a fresh snapshot fits I spawn via `spawn_instance_from_snapshot` and say so in the dispatch; after a finished run worth keeping I call `snapshot_create` on the tree root. I never snapshot mid-flight."*
- **Goal discipline (§10.1 D2):** HOW-to-call documentation still rides `CATEGORY_NAME`/`CATEGORY_DOC` module attrs, which `loader.py` emits into the system prompt's `## Snapshot` section at zero per-agent line cost (feasibility-notes §B(ii) step 5) — the agent lines concentrate on WHEN judgment, with the first line always the proactive trigger ("before recurring-shape work, search first").

---

## 7. Coexistence — Snapshots vs the Existing Knowledge Stores

The boundary is drawn by the question each store answers:

| Store | Answers | Lifetime | Rule |
|---|---|---|---|
| **Snapshot** (new) | "What was the working state of this task + tree at time T?" | Time-boxed, consumable | Point-in-time working state: task digest, tree shape, where work stopped, codebase position, freshly distilled task-scoped guidelines |
| `experience()` RAG | "How does this project/system work?" | Indefinite | Timeless, task-independent knowledge — snapshots should **promote** such findings to kb-writer, not hoard them |
| Critical notes | "Which operational facts must survive?" | Reviewable lifecycle | Leader-manual curation only; snapshots never auto-write |
| `.agents/shared/planning/` | "What is the living design for this in-flight feature?" | Feature lifetime | Snapshot references the path, never mirrors content |
| Dynamic skills | "How do I DO this?" | Evolvable (A/B) | Reusable procedures; general snapshot distillations graduate via `skill_create` |

**Anti-corruption rules:** snapshot OWNS its tables only; REFERENCES everything else by pointer (`detail_ref` pattern, `project/models.py:222`). No double-storage: reference-by-pointer + promotion replaces duplication. No cross-context auto-writes (critical notes and skills have human/keeper gates by design).

---

## 8. Recommended V1 Scope + Deferred Items

### In scope (minimal-but-useful) — with COMMITTED numeric caps

1. `daemon/tools/snapshot_tools.py` — 3 tools, `snapshot` category, 6-file registration chain (§6.1), meta.json allows (ari + leader), prompt lines per the D2-ratified 8-12-line/agent budget (§6.2).
2. `SnapshotService` + `SnapshotExecutor` (Approach B+): **row-ledger + asyncio + boot-sweep trigger lane** (developer ruling; satisfies the D1 hard requirement — audit §10); `get_tree_ids_permanent` walk; read-only `aget_state`; shared-hardening extraction (PR1, hard gate); `_call_summarization_llm` reuse via lightweight construction + persona param; `snapshot_prompts.py` — must emit a `refs`/`artifacts` section **and carry D6 quality-steering guidance** (well-chosen context over padding; the ~25k-token ceiling is a ceiling, not a target); model chain `SNAPSHOT_MODEL > COMPACTION_MODEL > session` with the effective model stamped on the row.
3. **Committed caps (fail loud on breach, not advisory):** **32 nodes/tree** — if enumeration exceeds 32, capture the first 32 in BFS order, set `truncated=true` on the row and surface a warning recommending a deeper root or `scope='self'` (never silent exclusion); **40k chars (~10k tokens) per-call input clamp** (matches `TRUNCATION_GLOBAL_INPUT_CAP_CHARS`, `compaction.py:285`; the reviewer-suggested 8k-token figure is a one-line tunable if tighter cost control is wanted); **600s per-tree wall clock**; cheap-tier model chain default.
4. 3 tables + SQLite migration file + pre-`create_all` model import (§3.3; no `_ensure_postgres_columns` mirror).
5. `snapshot_search`: BM25 + cached-embedding hybrid paralleling skill search; helpers imported directly, pinned by tests; **LLM-selection stage bounded to top-20 candidates**.
6. Consumption: FLAT (+ inline-briefs "tree" mode per the ratified D1 semantics), `instance_metadata["snapshot_digest"]` → stamped persistent HumanMessage with stable id; **escape-then-cap with the D6-ratified hard ceiling of ~25k tokens strictly counted** (tail-truncate with search hint, never skip); `staleness_report` (metadata + post-capture-advance default, `verify=git` opt-in).
7. **Observability floor:** one structured log line per snapshot capture (node counts, per-node status histogram, token usage, wall clock, effective model), mirrored into the snapshot row and the completion report.

### Phased plan (developer-estimated: 6 PRs ≈ 6-8 focused days; feasibility-notes §A is the execution blueprint)

| PR | Contents | Gate |
|---|---|---|
| **PR1** | Extract content-hardening corpus into a neutral shared module; swap graph.py's lazy import; compaction regression pack green | **HARD ordering gate — must land with-or-before PR4** |
| **PR2** | Persona parametrization (one optional arg, `compaction.py:3429-3434`) + `SNAPSHOT_MODEL > COMPACTION_MODEL > session` chain in config | after PR1 (cheap to sequence) |
| **PR3** | Storage: `daemon/repositories/snapshot/` + migration file + pre-`create_all` import | parallelizable with PR1/2 |
| **PR4** | `SnapshotService` + `SnapshotExecutor` + `snapshot_prompts.py` + committed caps + row-ledger lane | requires PR1, PR2, PR3 |
| **PR5** | `snapshot_search` hybrid + pin-with-tests | requires PR3 |
| **PR6** | Consumption + 3 tools + 6-file registration + meta.json + prompt lines (atomic tool surface) | requires PR4 |

### Deferred (with rationale)

| Item | Rationale |
|---|---|
| Tree restore (re-instantiated structure) | **Rejected + D1 RATIFIED against it (2026-09-23):** zombie structure, and re-instantiation would violate the flatten-to-one semantics |
| Hybrid deferred subtree expansion (`spawn_subtree_from_snapshot`) | No concrete consumer; second routing path = race risk; would need re-approval against the D1 hard requirement |
| JobItem lane for snapshot capture | Developer ruling: no processor home (two-shape pipeline, §1.6); retrofit ≈ L-sized PR (new branch + processor + terminal tokens + e2e lane gates). **Now additionally constrained-blocked by the D1 hard requirement** — it is the one deferred item that would touch the job/task/mission system |
| Auto-injection / heuristic when-to-snapshot | [USER-FIXED v1 constraint] tools only, agent judgment |
| Git anchor as DEFAULT staleness check | New daemon-wide repo coupling; opt-in first |
| `text_search_common.py` full extraction | Pin-with-tests suffices; extract when a third consumer appears |
| Retention / auto-supersession automation **incl. deletion semantics (GDPR-style purge of captured content)** | `status` + `supersedes_snapshot_id` ship now; automation + purge semantics later |
| Pause-first live-tree capture | No-pause read developer-confirmed safe (§1.2); stamps + post-capture-advance check cover staleness |
| Per-snapshot usage metrics / A-B | Skill-machinery analog; v2 |
| Live auto-digest on completion (Observer) | Violates tools-only v1; breaks tree walks needing explicit intent |

---

## 9. Risks

- 🔴 **Snapshot poisoning / indirect prompt injection.** Captured content (tool output, web/MCP results, user messages) can carry adversarial text; the summarizer absorbs it; the digest lands in the **permanently-hoisted, compaction-surviving bucket trusted by every warm-started agent**. `escape_for_context_block` is structural escaping, not semantic defense. *Mitigations (all four, mandatory):* (a) summarize only **finalized turns** (never mid-flight content); (b) treat the digest as **low-authority** in the consuming prompt — "a lead, not ground truth" — with warm-started reports held to the same evidence rule as cold ones; (c) sanitization/confidence cap on summarizer output; (d) **digest provenance block** (source tree id, effective model, prompt version, captured_at; live-tree captures carry a **LIVE-TREE banner**) rendered atop every digest for audit.
- 🔴 **Stale-digest trust — concrete failure mode:** a v0.13.9-era digest instructs "gate the pack on ensure.md dev.sh boot" — but ensure.md was later corrected to a 4-line boot-probe-only rule (a real drift this repo has hit). An agent trusting the stale digest runs the wrong gate and false-gates a merge. *Mitigation:* staleness_report + the mandated drift note (§6.2) + runtime-version warning + post-capture-advance check for live-tree captures (§5.2).
- 🔴 **Silent-wrong digests** if the content-hardening corpus isn't shared before the first summarizer ships — confidently-wrong output, worse than a crash. *Gate: PR1 is a hard ordering gate (§8).*
- 🔴 **Persona parametrization + instance-method construction touch battle-tested compaction** (`compaction.py:3425-3440`). *Mitigation: one optional argument + lightweight-construction wrapper + compaction regression pack in PR2.*
- 🟡 **Cost overrun.** Caps are COMMITTED (§8): 32 nodes/tree, 40k chars/call, 600s wall clock, fail-loud on breach; digest injection hard-capped at ~25k tokens (D6); search LLM-stage bounded to top-20; cheap-tier model chain with `""`-never-means-session-model semantics. Residual: repeated snapshots of the same tree multiply cost until dedup lands (idempotency key handed to storage).
- 🟡 **Memory pinning on cold-load walks:** `_restore_instance` registers each restored graph in `manager.instances` permanently — a 32-node walk pins up to 32 graphs. *Mitigation: post-walk eviction pass (pop only the entries the walk inserted) — cheap insurance.*
- 🟡 **"Capture never mutates" needs one asterisk:** `_restore_instance` ends with `_recover_watchover_pending_termination` (`instance_lifecycle.py:4369-4370`), which on an instance carrying a **stale watchover marker** triggers a REAL `terminate_instance` cascade (`:3918-3921`). Narrow, but the capture path can terminate a corrupted-watchover instance it touches. Per-node skip-and-record absorbs the fallout.
- 🟡 **Hard-deleted instance rows** raise `KeyError` (`instance_lifecycle.py:3887-3888`) → per-node try/except skip-and-record is mandatory (specified).
- 🟡 **Hoisted-token accounting (25k ceiling):** digest tokens permanently count against the compaction gate numerator — a full 25k-token digest ≈ ~3.6% of a 700k window but ≈ ~20% of a 128k-class window, permanently. Bounded by D6's ceiling-not-target discipline + quality steering + stable-id supersede + one-digest-per-instance (assert the ceiling in the injection hook).
- 🟡 **Sibling-drift:** skill-search helper edits silently change snapshot ranking (same failure class as the reasoning-echo test-contract drift lesson). *Mitigation: pin with tests in PR5.*
- 🟡 **Digest drift across runtime versions** — mitigated by `runtime_version` stamp + warnings surfaced to the agent.
- 🟡 **Tree TOCTOU on live trees** — stamped per-node `captured_at`; developer-confirmed the read never tears (§1.2); stale-by-one-turn at worst under mid-turn compaction races; the post-capture-advance check (§5.2) surfaces work that finished after capture.
- 🟢 **Storage bloat** — retention v2; JSONB size assumptions to validate at pilot.
- 🟢 **SQLite fallback scan** for `domain_tags` containment (GIN is PG-only) — acceptable at v1 volumes.

---

## 10. Decisions

**⭐ North star (user, verbatim):** *"newly spawned agent ready to work — less thinking, less exploration."* Checked against every decision — see §10.1.

### (i) User decisions

| # | Decision | Status / Recommendation |
|---|---|---|
| **D1** | **Tree semantics — RATIFIED by user, 2026-09-23.** Tree mode = **inline child briefs as data, NOT re-instantiated child instances; flatten to ONE snapshot.** User's canonical example: a tester that spawned workers produces a **single "tester snapshot" containing the distilled whole**. **Hard requirement attached (verbatim): "the job/task/mission system must remain intact/unchanged."** | ✅ **RATIFIED.** Constraint audit below: the recommended v1 SATISFIES the hard requirement; the deferred JobItem lane is the one component that would violate it — now constrained-blocked unless the user lifts the requirement. |
| **D2** | **Prompt-line budget — RATIFIED by user, 2026-09-23.** **8-12 lines per agent allowed** — the strict 4-5-line constraint is dropped ("no need to be too strict"). | ✅ **RATIFIED.** The standard minimal 3-file pattern (~8-12 lines/agent) is authorized and adopted (§6.2). Goal interplay (§10.1): the relaxed budget resolves the tool-underuse risk that threatened the goal. |
| **D3** | **Trigger lane ratification:** row-ledger + asyncio + boot sweep (developer-grounded ruling) — JobItem lane deferred as a separate L-sized PR. | *Pending.* **Ratify row-ledger** — it is also the only lane satisfying the D1 hard requirement (audit below). |
| **D4** | **Persona parametrization now vs v1.1:** touch `compaction.py:3429-3434` now, or accept the generic "summarizes conversations" persona in v1. | *Pending.* **Now (PR2)** — one optional argument, regression-packed; the persona mismatch is semantically real for durable-knowledge extraction. Fallback: accept generic in v1 only if the compaction-regression budget is tight. |
| **D5** | **`include_message_history` default:** `True` with a 20-turn per-node tail cap, or `False` (cheapest). | *Pending.* **True + cap** — thin snapshots undermine warm-start; the cheap-tier chain bounds cost; the tail cap is the cost knob. |
| **D6** | **Digest cap — RATIFIED by user, 2026-09-23.** **Hard ceiling of ~25k tokens, strictly counted — a ceiling, not a target.** The snapshoter prompt steers toward GOOD, well-chosen context (quality > size). The artifact-reference pattern is unchanged: the digest can still REFERENCE stored full artifacts fetched on demand — **the cap bounds injection, not knowledge.** | ✅ **RATIFIED.** Recorded in §2.3 (prompt steering), §4.3 (injection ceiling + accounting), §8 (committed cap). Goal interplay (§10.1): 25k ceiling + quality steering serves domain-heavy warm-starts without padding rot. |
| **D7** | **Live-tree snapshots:** allow (stamped, best-effort) vs terminal-only. Trade: live trees risk stale-by-one-turn captures but capture in-flight value; terminal-only is exact but loses mid-flight use. | *Pending.* **Allow with stamps + the §5.2 post-capture-advance check + LIVE-TREE banner**; prompt norm stays "snapshot when done" (live = escape hatch). |
| **D8** | **Search default scope:** project-scoped by default (`project_id=None` → caller's project). | *Pending.* **Yes** — cross-project snapshot reuse is a foot-gun in v1; §4.3's isolation paragraph enforces it structurally. |

**D1 constraint audit — "job/task/mission system must remain intact/unchanged":** the recommended v1 touches that system **nowhere**. The row-ledger lane writes only `snapshots` rows and runs an in-process asyncio task (§2.4); the boot sweep is one idempotent query on the snapshots table — same *shape* as job recovery but touching zero job/task tables; `spawn_instance_from_snapshot` rides the **existing** `manager.spawn_instance` + `send_message` fire-and-forget lanes unchanged (§4.3); the 6-file registration chain is tools/category/meta only (§6.1); the capture walk reads checkpoints, never task rows (§2.4). **The working hypothesis holds: the row-ledger ruling SATISFIES the hard requirement** — chosen originally for processor-shape reasons (§1.6), it coincidentally eliminates the design's only job-system touchpoint. The **deferred JobItem lane WOULD violate the requirement** (new dispatch branch + execution processor + terminal-token contract + e2e lane gates — §8): it moves from "deferred" to **constrained-blocked** pending an explicit user lift. No v1 component requires redesign.

### 10.1 Goal check (2026-09-23)

North star: *"newly spawned agent ready to work — less thinking, less exploration."*

| # | Decision | Verdict | Grounding + Adjustment |
|---|---|---|---|
| D1 | flatten-to-one tree semantics | **supports** (resolved-by-user) | §4.2 — one digest, one warm instance; zero job-system coupling by construction |
| D2 | prompt-line budget | **resolved-by-user** | RATIFIED 8-12 lines/agent: the relaxed budget **resolves the tool-underuse risk that threatened the goal** — agents get room for WHEN + HOW-lite judgment across the 3-file pattern, while CATEGORY_DOC still carries HOW-to-call at zero line cost (§6.2) |
| D3 | row-ledger lane | **supports** | §2.4/§10 audit — zero job-system coupling, agent-visible failures, and the only lane legal under the D1 hard requirement |
| D4 | persona parametrization now | **supports** | §2.3 — persona mismatch degrades digest quality, which IS warm-start quality |
| D5 | history True + 20-turn cap | **supports** | §8 — richer digests; cost bounded by cap + cheap-tier chain |
| D6 | digest cap → 25k-token ceiling | **resolved-by-user** | RATIFIED: hard ~25k-token ceiling (strictly counted) + quality-steering prompt — domain-heavy warm-starts (version-pump guidelines, test-pack runs) get ~8× the original 12k-char injection headroom while the refs/artifacts pattern keeps knowledge unbounded in storage; steering prevents goal-rot via padded low-value context (§2.3, §4.3) |
| D7 | live trees allowed | **risks-the-goal → adjusted** | §5.2/§9 — a stale live-tree digest can actively mislead (digest says "X unresolved" while X finished) — worse than cold. Adjustments: (1) prompt norm stays "snapshot when done" (live = explicit escape hatch); (2) default staleness gains the cheap post-capture-advance check (instance-status read on captured node ids — O(N) DB read, no LLM); (3) LIVE-TREE banner in the provenance block |
| D8 | project-scoped search | **supports** | §4.3/§5 — higher result relevance, less cross-project noise → faster warm-start |

### (ii) Resolved during exploration (recorded for transparency — no user action needed)

| # | Item | Resolution |
|---|---|---|
| R1 | Escape digest at consume time? | **Yes — mandatory.** `_make_context_message` does NOT escape (`:147-152`); cap applies **post-escape** (expansion up to ~6×). |
| R2 | `context_kind` enum + stable id? | **Confirmed:** `CONTEXT_KIND_SNAPSHOT_DIGEST = "snapshot_digest"`, stable id `snapshot_digest:{instance_id}`. Hoist is truthy-keyed — zero compaction changes; survival developer-verified on all five mutation paths. |
| R3 | `aget_state` cold-load for long-terminal instances? | **Works**, including daemon-restart; three caveats folded into §9 (memory pins, watchover cascade asterisk, KeyError skip-and-record). |
| R4 | JAFP entry-point count | **Six**, not four (Rev 1 said "4" — recalled the four source tags). Doc-level correction; the lane ruling is unaffected. |
| R5 | `_ensure_postgres_columns` mirror | **Unnecessary for new tables** — `create_all` covers existing PG on every boot; migration story simplified (§3.3). |
| R6 | Tool registration pickup | **Fully traced — 6-file chain** (§6.1), including the loader warm-entry that prevents an empty-category cache pin. |
| R7 | "No pause needed" concurrency claim | **Confirmed from code** (single sentinel channel write; reads never tear). |

---

## Appendix — Code Anchor Index (all verified on `plan/agent-snapshot`; ⚙ = developer re-verified in feasibility-notes)

| Anchor | What |
|---|---|
| `daemon/compaction.py:3325-3440` ⚙ | `_call_summarization_llm` — prompt-as-arg reuse seam (ContextCompactor instance method) |
| `daemon/compaction.py:3429-3432` ⚙ | hardcoded summarizer persona (parametrize — one optional arg) |
| `daemon/compaction.py:1295`, `daemon/config.py:2835-2867` ⚙ | model-override resolution (mirror for SNAPSHOT_MODEL chain) |
| `daemon/compaction.py:1176-1195` ⚙ | adaptive summarization timeout |
| `daemon/compaction.py:2811-2815, 2867-2935` ⚙ | parallel pool + deadline budget (ordered gather) |
| `daemon/compaction.py:108-146`, `:217-249`, `:191`, `:551-556` ⚙ | injected/context_kind predicates, partition, hoist (truthy-keyed) |
| `daemon/compaction.py:81` | `_extract_text_from_content` multimodal flattener |
| `daemon/compaction.py:1893-2058` | token-pressure trigger machinery (NOT reused) |
| `daemon/compaction.py:2062` | `compact_state` compactor proper (NOT reused) |
| `daemon/compaction.py:285` ⚙ | `TRUNCATION_GLOBAL_INPUT_CAP_CHARS = 40_000` (clamp precedent) |
| `daemon/services/_compaction_persist_seam.py:72`, `:142-169` ⚙ | persist seam — writes INTO checkpoint (NOT reused) |
| `daemon/services/compact_executor.py:732-738` ⚙ | dormant-instance `aget_state` read |
| `daemon/services/compact_executor.py:1755` | `/compact` command (sync — wrong shape) |
| `daemon/services/job_processor.py:883, :1249` ⚙ | the two job shapes (message-skip / agent-spawn) — lane ruling basis |
| `daemon/tools/knowledge_tools.py:342-410` ⚙ | `experience()` internal-JobItem precedent |
| `daemon/services/skill_job_dispatcher.py:126-145` | skill internal-job precedent |
| `docs/architecture/job-as-front-primitive-invariants.md:22-29` ⚙ | JAFP six public entry points |
| `daemon/repositories/instance/repository.py:527` (depth cap `:33`) ⚙ | `get_tree_ids_permanent` tree walk |
| `daemon/services/instance_lifecycle.py:3861-3890` ⚙ | `get_instance` — no status filter; KeyError `:3887-3888` |
| `daemon/services/instance_lifecycle.py:3977-4370` ⚙ | `_restore_instance` — compiles + pins, no mutation; watchover recovery `:3892-3975`, cascade trigger `:3918-3921` |
| `daemon/services/instance_messaging.py:1897-1931` ⚙ | terminal auto-revive on send (hazard) |
| `daemon/manager.py:6870` ⚙ | `spawn_instance` facade |
| `daemon/manager.py:3774` ⚙ | `set_metadata_many` atomic metadata write |
| `daemon/manager.py:520-525` ⚙ | pre-`create_all` import precedent + every-boot create_all |
| `daemon/manager.py:5099` | `_ensure_postgres_columns` (columns on pre-existing tables only) |
| `daemon/migrations/runner.py:67-68, :719-727` ⚙ | checksums; SQLite-only runner |
| `daemon/migrations/versions/20260710_000001_create_skill_tables.sql:1-4, :101` | dual-driver migration convention |
| `daemon/services/context_messages.py:83-107` ⚙ | `CONTEXT_KIND_*` constants |
| `daemon/services/context_messages.py:130-165` ⚙ | `_make_context_message` (stable ids, stamps, no-escape contract) |
| `daemon/services/context_messages.py:526-647` (truncation `:618-640`) ⚙ | critical-notes block + reference-truncation precedent |
| `daemon/services/context_messages.py:932-975` ⚙ | KV cap-post-escape / skip-not-truncate lesson |
| `daemon/services/context_messages.py:1006-1059` | `build_shared_context_message` precedent |
| `daemon/services/context_messages.py:1589` | `assemble_context_messages` orchestrator |
| `daemon/tools/instance.py:1853` | `SpawnInstanceInput` signature convention |
| `daemon/tools/instance.py:663` / `:686` / `:2224-2226` ⚙ | auth gate / completion watcher (zombie mechanism) / error-string convention |
| `daemon/tools/instance.py:4709-4711` | factory-call precedent inside `create_instance_tools` |
| `daemon/tools/critical_notes.py:292` | command-shape tool factory |
| `daemon/tools/skill_tools.py:140-141` | category-allow precedent |
| `daemon/tools/_tool_registry.py:23-110`, `:513-570`, `:604`, `:370-372` ⚙ | DYNAMIC_TOOL_NAMES / CATEGORY_MODULES / KNOWN_TOOL_NAMES fallback / regen source |
| `daemon/loader.py:42-63` ⚙ | warm-list (empty-category cache-pin hazard) |
| `daemon/repositories/skill/models.py:563-621` ⚙ | `skill_embeddings` JSONB precedent |
| `daemon/services/skill_search_service.py:45-60` | BM25 no-numpy hybrid design |
| `daemon/services/skill_embedding_service.py:11-12`, `:537` | trigger queries, create+update embeddings |
| `daemon/repositories/project/models.py:210-231` | critical-notes lifecycle + soft refs |
| `daemon/repositories/infra/types.py:35` | `JSONBType` |
| `daemon/__init__.py:3` | `__version__ = "0.14.0"` |
| `daemon/services/doc_commit_service.py` | only existing git usage in daemon |
| `agents/leader/workflow.md:660-681` | fire-and-forget spawn contract |
| `docs/agent-prompt-writing-guide.md` | prompt-authoring conventions |

---

*Rev 1: 6 design workers (pipeline-reuse `9e4a5760`, pipeline-modified `12c24ca1`, pipeline-new `8712bbb1`, storage-model `af1184cb`, tool-surface `bb8b50f9`, consumption `5a7279dc`) + charter `1ed28228`; all reports architect-spot-verified. Rev 2: developer feasibility gate (38/38 anchors, zero substantive discrepancies) + reviewer critique (C1-C4 + quick wins + backlog). Rev 3: user discussion round 1 — **D1 RATIFIED 2026-09-23** with the job/task/mission-intact hard requirement (audit: satisfied; JobItem lane constrained-blocked) + north-star goal check. Rev 4: **D2 + D6 RATIFIED 2026-09-23** (8-12 lines/agent; ~25k-token digest ceiling with quality steering) folded into the goal check. Architecture unchanged.*
