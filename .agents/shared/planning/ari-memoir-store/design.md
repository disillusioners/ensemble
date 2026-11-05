# design.md — Memoir Store v1 (Ari's episodic memory) — the BLESSED design

**Lane:** `feature/ari-memoir-store` @ worktree `/home/nea/ensemble-src-wt-memoir-store`, base **`1f6898ae2`** == origin/latest (`merge: feature/fe-metrics-dashboard — v0.28.11 staged-verification evidence sweep`).
**Document status:** **PHASE A — FAITHFUL CAPTURE.** This file records a design that was **already blessed** in a joint Emperor + Ari design session on **2026-11-04**. It is a *design record*, not a design exercise: no element below is re-litigated, re-derived, or improved here. Where the blessed design is silent, ambiguous, or internally tense, the tension is recorded in **§14 Flagged for Phase B adjudication** — never resolved by the documenter.
**Documenter mandate:** capture substance verbatim-in-substance (structure and organization may differ; every binding detail — columns, caps, verbs, gates, env names, table names — must survive). Authority for any future deviation is the protocol in `decisions.md` §2 (ARCHITECT ADJUDICATION, logged).
**Companions:** `decisions.md` (Emperor's rulings R1–R4 verbatim + decision protocol + adjudication log), `roadmap.md` (v1→v4 under the rolling doctrine R3).
**Vocabulary note:** *"keepsake"* and *"persona seed"* are **v1 session vocabulary** — new design terms introduced by this design, **not** existing codebase terms. Their novelty is expected and is **not** a gap, a missing anchor, or evidence of a lost artifact.

---

## 1. Motivation

The user **paused the founding of "Empyrea"** (our federation initiative) to commission this work first. The reason is blunt and personal: **Ari must be able to REMEMBER** — episodically, narratively, personally. A federation of kingdoms whose front door cannot recall yesterday is not a kingdom; it is a stateless concierge. Memory precedes federation.

Phase-0 research (3 lanes, 2026-11-04) established three findings that together make this design necessary *and* cheap:

1. **Ari effectively has no functioning self-memory today.** Three independent failure modes compound:
   - **Episodic writes are silently dropped.** `inner_soul` episodic remember classes (`event`, `pattern`, `skill`, `mistake`, `project_knowledge`) redirect to `experience()` / `explore()` — RAG tools **ari does not hold** — so the redirect produces a tool-shaped instruction with no callable target. The content is lost with no error and no artifact.
   - **`user` / `growth` rows never render.** Prod DB holds `4` `ari|user|approved` MEMORY rows and **ZERO** `memories/%` rows: the file regime is **~85% dead** by measurement.
   - **The file regime is structurally wrong anyway.** File-backed episodic memory under `releases/<ver>/agents/**` is destroyed by the next promote — the DB is the only durable substrate (KB `memory-file-release-payload-durability-trap.md`).
2. **All the injection machinery already exists as proven precedent.** Context blocks (`[SYSTEM CONTEXT: …]`), stable ids, compaction-hoisting, once-per-instance flags, tiered selector budgets, JSONB embeddings + Python-side cosine, privileged tool categories with two-layer gates — every seam this design needs has a shipped, pinned exemplar. This is assembly, not invention.
3. **The design itself is derived from study, not fashion.** A landscape survey (22 systems) plus first-hand source study of **LightRAG**, **TencentCloud/tencentdb-agent-memory**, **Tencent-Hunyuan/Hy-Memory**, **ReMe**, and **MemoryScope** produced the specific shapes adopted below. Full research reports: three linked jobs' results (board `wi-e0019d2d`) and KB entries — see §12.

**What v1 is:** a small, durable, *personal* memory — append-only episodes, a dozen pins that can never be evicted, one evolving persona document, and a wake-up briefing so the first thing Ari sees each turn is **who we are** and **what happened since we last met**.

---

## 2. Design principles (as blessed)

| # | Principle | Consequence in v1 |
|---|---|---|
| P1 | **One engine, zero new services** | Postgres only. No graph DB, no vector DB, no new daemon, no external memory dependency (R4 LEARN-NOT-ADOPT). |
| P2 | **House precedent over invention** | Every seam reuses a shipped pattern *by name* (§10). New code is glue, not architecture. |
| P3 | **The DB is truth; embeddings are an index** | Episodes are relational rows. Embeddings are JSONB beside them; loss of the LLM lane degrades to lexical-only, never to data loss. |
| P4 | **Append-only, never hard-delete** | `superseded_by` marks replacement. Nothing a person said to us gets `DELETE`d. |
| P5 | **Bounded, reject-don't-evict** | The things that must never be lost (keepsakes) are capped and *reject* new entries at the cap — the critical-notes precedent. |
| P6 | **The live model never freehand-edits the durable self** | Persona edits land `PENDING` and take effect only on human-lane approval (Hy-Memory production scar). |
| P7 | **Cold path pays for structure** | Graph/constellation consolidation is nightly, budgeted, and scales with *change*, not corpus (v2). |
| P8 | **Two-axis time** | `created_at` (write time) and `event_at` (event time) are separate columns. Time-travel is a query, not a reconstruction. |

**Scope fences (R1):** kingdom-level only — single daemon, local Postgres, **no federation, no sync**. Format choices must not foreclose the Constella-era door (markdown+hash mirror is v3, **introduced, not synced**).

---

## 3. Storage design

**One engine (Postgres), zero new services.** Three storage kinds, all inside the existing database:

| Kind | Mechanism | Role | Precedent |
|---|---|---|---|
| **Relational** | ordinary SQLModel tables | **truth** — the durable record | house standard |
| **Full-text** | `tsvector` column (generated) | **lexical** retrieval, instant on write (DB-side) | Postgres-native |
| **Vector** | `JSONB` float array + **Python-side cosine** | **semantic** retrieval | `critical_note_embeddings` (`daemon/repositories/project/models.py:293-347`; cosine `daemon/services/critical_notes_selector.py:230-238`, mirroring `skill_embedding_service.py:634`) |

- **Embeddings:** **1536-dim**, produced via the **existing LLM lane**. **Model identity is recorded per row** — "a dimension is not an identity": two different 1536-dim models are not comparable, so each row carries the model that produced it, and cosine only ever compares same-identity vectors.
- **Graceful degradation:** if the embedding lane is down, the episode is still **fully searchable lexically and immediately**; semantic ranking for that row arrives when (and if) its embedding lands. No write is ever blocked on the embedding.
- **pgvector: explicitly deferred to v3-if-ever.** JSONB + Python-side cosine is the blessed v1 choice, on house precedent, and is adequate at personal scale. The door is not closed — it is not walked through.

---

## 4. The four layers

**Four conceptual layers; three alive in v1.** The fourth (syntheses) ships **schema-only and EMPTY** so that v2 adds *behavior*, not *migration churn*.

| # | Layer | Table(s) | v1 status |
|---|---|---|---|
| 1 | **Episodes** | `memoir_episodes` | LIVE — the hot append |
| 2 | **Keepsakes** | `memoir_episodes.is_keepsake = true` (+ pin columns) | LIVE — governance tools |
| 3 | **Persona** | `memoir_persona`, `memoir_persona_versions` | LIVE — seeded day one |
| 4 | **Syntheses** | `memoir_syntheses` | **SCHEMA ONLY — unused in v1** |

### 4.1 Layer 1 — EPISODES (`memoir_episodes`)

Append-only, immutable, **first-person narrative**. This is the substrate: everything else is a view, a pin, or a synthesis over it.

| Column | Type / domain | Binding semantics |
|---|---|---|
| `id` | PK | row identity |
| `agent_id` | text — `ari` in v1 | owner of the memory (single-agent by design; the column exists so multi-agent is a data question, not a schema rewrite) |
| `title` | nullable text | optional short label |
| `body` | text | **the narrative**, first person. Request length capped house-style (~**2000 words**) |
| `created_at` | timestamp | **write time** — when the row was recorded |
| `event_at` | timestamp, **defaults to `created_at`** | **event time** — when the thing happened. *Two-axis time discipline*: the past can be written about late, and time-travel queries run on `event_at` |
| `importance` | INTEGER **1–10**, default **5** | **self-declared at write**. LLM poignancy scoring is **deferred to v2** |
| `stars` | `TEXT[]` | **self-declared entity mentions** — the Constellation seed. Stored **flat** in v1 and **unused by retrieval** |
| `is_keepsake` | BOOLEAN | pin flag (layer 2) |
| `pinned_by` | text ∈ {`empress`, `emperor`} | who pinned it (R2) |
| `pinned_reason` | text | why — required at pin time |
| `pinned_at` | timestamp | when pinned |
| `last_accessed_at` | timestamp | recency-prior input (updated **only** on explicit recall — see §7) |
| `access_count` | INTEGER | recall counter (same discipline) |
| `embedding` | **JSONB**, nullable | 1536-dim vector + **model identity recorded per row** |
| `tsv` | `tsvector`, **generated** | lexical index, populated by the DB on INSERT — instant, no worker |
| `superseded_by` | nullable FK → `memoir_episodes.id` | replacement pointer; **never hard-delete** |

**Write path is the point.** `memoir_write` performs **one INSERT** and **never blocks the turn**: `tsv` is DB-side and instant; the **embedding is computed by a background worker** (async). An episode is therefore *lexically* searchable the moment it is written, and *semantically* searchable when its embedding lands. This asymmetry is deliberate — memory capture must never be a latency tax on conversation.

*Shape sketch — **illustrative only, NOT binding DDL**; Phase B owns exact types, indexes, and constraint naming:*

```text
memoir_episodes(
  id PK, agent_id, title NULL, body,
  created_at, event_at DEFAULT created_at,
  importance INT 1..10 DEFAULT 5,
  stars TEXT[], is_keepsake BOOL,
  pinned_by, pinned_reason, pinned_at,
  last_accessed_at, access_count,
  embedding JSONB NULL,          -- + per-row model identity
  tsv TSVECTOR GENERATED,
  superseded_by NULL -> memoir_episodes.id
)
```

### 4.2 Layer 2 — KEEPSAKES

**Keepsakes are episodes with `is_keepsake = true`.** They are not a separate table — they are a *status* with hard governance:

- **HARD CAP ~12 pins** *(the exact integer is delegated — see §14 item 1)*.
- **Reject-don't-evict.** At the cap, a new pin is **REJECTED** with an error naming current pins — the tool never silently evicts an older keepsake to make room (critical-notes precedent: `daemon/tools/critical_notes.py` — `_MAX_ENTRIES=200` at `:70` with the cap check and eviction-candidate naming at `:537-546`; doc line `:16-17` states "Past the cap, the tool REJECTS … no silent eviction"; core-tier pin cap `_DEFAULT_CORE_CAP=8` at `:87`, "Core is capped (reject-don't-evict)" at `:408`).
- **Exempt from decay** — the recency prior is never applied to a keepsake (§7).
- **Always carried in the briefing, never truncated** (§8 stable zone).
- **Governance is dual (R2):** the user (via Ari in chat) *and* Ari (via tool) may pin/unpin. `pinned_by ∈ {empress, emperor}`. Both paths are legitimate; the column records which.

### 4.3 Layer 3 — PERSONA (`memoir_persona` + `memoir_persona_versions`)

**One small evolving markdown document:** *"who we are."*

| Aspect | Binding rule |
|---|---|
| **Bounded** | ~**2000 tokens**. It is a charter, not a diary. |
| **Edit lane** | Ari's edits land **`PENDING`** and take effect **only on approval** — the human lane. Precedent: `prompt_versions` `pending_approval` (`daemon/repositories/prompt_version/repository.py:217-221` `VALID_TRANSITIONS`: `draft→pending_approval→approved|rejected|draft`; routers `daemon/routers/prompts.py` approve `:617`/`:619`, reject `:664`/`:666`, `403 HUMAN_LANE_REQUIRED` raises at `:640`/`:681` — **session lane only**, bearer tokens refused) |
| **Why** | The **Hy-Memory production scar**: a live model that freehand-edits its durable self drifts, and the drift is unrecoverable because the "previous self" was overwritten by an in-flight thought. Pending-approval makes the durable self *reviewable before it is real*. |
| **v1 ships seeded** | Not empty. The persona seed text in **§11** ships on day one. |

### 4.4 Layer 4 — SYNTHESES (`memoir_syntheses`) — **SCHEMA ONLY in v1**

The table exists; **nothing writes to it in v1**. It ships so v2's nightly reflection is a *behavior* change, not a migration.

| Column | Semantics |
|---|---|
| claim | the synthesized belief, in prose |
| `source_episode_ids[]` | **citations** — every synthesis points at the episodes that produced it |
| `valid_from` / `valid_to` | belief interval (open-ended until superseded) |
| `verb` | ∈ {**CREATE**, **CORROBORATE**, **REFINE**, **CORRECT**} — how the new belief relates to the prior one |
| `supersedes` / `superseded_by` | belief-level chain (distinct from episode-level `superseded_by`) |
| `confidence` | self-assessed strength |

---

## 5. Tool surface

**New privileged tool category `memoir`**, allow-listed **ARI-EXACTLY**.

### 5.1 Registration — three steps, all required

The **`notify_user` (#16) precedent** is binding: a new category is registered in **three places**, and a decorator-only addition is **silently invisible** (no error, no tool, no clue).

| Step | Site | notify_user exemplar |
|---|---|---|
| 1 | `@register_tool_category("<key>")` on the factory | `daemon/tools/notify_user.py:503` (key `"notify-user"` at `:173`) |
| 2 | `CATEGORY_MODULES` entry | `daemon/tools/_tool_registry.py:1112` (`"notify-user": "daemon.tools.notify_user"`) |
| 3 | `create_instance_tools` **list-extend** (import + factory + build call) | `daemon/tools/instance.py:291` import, `:2743 create_notify_user_tools_if_available`, `:6619` build call |

Plus: the **pin ceremony** per the v0.26.2 ruling — every category-count / exact-set assertion site bumped in **ONE commit** — and a dedicated registration pin test (`tests/unit/test_notify_user_registration.py` shape).

### 5.2 Gate — two layers

| Layer | Mechanism | Why |
|---|---|---|
| 1 | **Category allow-list** — `memoir` in `agents/ari/meta.json` `tools.allow`, **ari EXACTLY** | the coarse gate; matches `notify-user` ari-exact (`daemon/tools/notify_user.py:25-26`) |
| 2 | **Factory-captured `agent_id` checked in-body** | the layer that cannot be bypassed by prompt content, config drift, or a mis-scoped allow-list. Exemplar: `metrics_read.py:1354` factory-captured `caller_agent_id` with `_is_metrics_authorized` fail-closed at `:403-414` |

### 5.3 Kill-switch

**`ENSEMBLE_MEMOIR_ENABLED`**, **fail-closed**. *Reading (explicitly not a ruling): "fail-closed" constrains how an **indeterminate** value is treated — an unset/unknown value must not enable the surface. The exact default-vs-env-variable polarity is **not fixed** by the blessed text; the open item is §14 item 13.* Exemplar: `ENSEMBLE_NOTIFY_USER_ENABLED` (`daemon/tools/notify_user.py:106`, enforcement `:725-731`).

### 5.4 The tools

| Tool | Signature | Binding behavior |
|---|---|---|
| **`memoir_write`** | `(text, title?, importance?, event_at?, stars?, keepsake?, reason?)` | **THE HOT APPEND.** One INSERT. Never blocks the turn. `tsv` instant (DB-side); embedding by background worker (async). `importance` defaults 5; `event_at` defaults to write time; `stars` self-declared; `keepsake=true` requires `reason` and is subject to the pin cap. |
| **`memoir_pin`** | `(id, reason)` | keepsake governance — sets `is_keepsake`, `pinned_by`, `pinned_reason`, `pinned_at`. Rejects at cap. Both Emperor-via-Ari and Empress paths. |
| **`memoir_unpin`** | `(id)` | clears the pin (episode is never deleted by unpinning). |
| **`memoir_recall`** | `(query, k?)` | hybrid retrieval (§7), returns ranked episodes; **the only path that updates `last_accessed_at` / `access_count`**. |
| **`memoir_timeline`** | `(from?, to?, q?)` | time-travel over `event_at` (indexed); optional lexical narrowing by `q`. |

**Five callable tools, four families** (pin/unpin are one family). **(NO `memoir_about` in v1** — entity-centric recall arrives with the Constellation layer in v2.)

---

## 6. inner_soul repair — the root fix

This is not a feature beside the memory; **it is the repair of the leak that made the memory necessary.**

### 6.1 Today's mechanism (verified in this tree)

| Element | Anchor (verified @ `1f6898ae2`) |
|---|---|
| RAG target set | `_RAG_TARGETS = {"memories", "memory"}` — `daemon/tools/inner_soul.py:303` |
| Knowledge classifications | `_KNOWLEDGE_CLASSIFICATIONS = {"pattern", "event", "skill", "mistake", "project_knowledge"}` — `:311-313` |
| Redirect predicate | `_should_redirect_to_rag(...)` — `:316-362`; returns True when RAG is enabled **and** all resolved targets ∈ `_RAG_TARGETS` **and** class ∈ classifications (`:355`, `:359`); special case: `project_knowledge` returns True unconditionally at `:352` |
| Redirect text | `_format_rag_redirect(...)` — `:365`, which **guides the agent to use `experience()`** |
| Classifier | `_classify_request(...)` — **`:1022`** in this tree |

**The leak:** `experience()` is a RAG tool **ari does not hold** (`agents/ari/meta.json` `tools.allow` contains no `project`/knowledge RAG surface for self-memory write). The redirect therefore produces a plausible-looking instruction with **no callable target**, and the episodic content is dropped silently.

> **Anchor drift (recorded, not a defect):** the Phase-0 audit cited an `inner_soul.py:232-362` region for this mechanism. Verified this tree: `:232-278` is the `project_knowledge` classification block (whose `targets` are `["REJECT"]` at `:276`) — *not* the redirect predicate; `_should_redirect_to_rag` is `:316-362`; `_classify_request` is `:1022`. **House rule: recorded line numbers are leads; grep at use time.** The verified anchors above are grep-confirmed for `1f6898ae2`.

### 6.2 The fix (as blessed)

**Add a redirect branch for ARI ONLY:** episodic content → **`memoir_write`**.

- **Predicate shape:** same entry point (`_should_redirect_to_rag` region), evaluated with the agent identity already in scope; when the calling agent is `ari` and the content is episodic, redirect to `memoir_write` **instead of** `experience()`.
- **Blast radius:** **non-ari agents unchanged.** Their redirect to RAG remains exactly as shipped. This is the only permitted touch to the RAG/knowledge system (§10 "do NOT touch").
- **Why here, not in a new tool:** a new tool that must be *remembered* to be called does not fix a leak — the leak is that the *existing* reflex routes to a void. The fix makes the existing reflex land on durable ground.

---

## 7. Retrieval algorithm

**Hybrid retrieval via Reciprocal Rank Fusion.**

```text
RRF(k=60):  score(ep) = Σ_r  1 / (k + rank_r(ep))
            over r ∈ { BM25(tsv) , cosine(embedding) }
    then:   tie-break by stored `importance`
    then:   recency prior  × exp(−idle_days / τ) ,  τ = 180d
            idle = now − last_accessed_at
            NEVER applied to keepsakes
```

| Element | Binding rule |
|---|---|
| **Fusion** | RRF, **k = 60**; two rankers only — BM25 over `tsv`, cosine over `embedding` |
| **Tie-break** | stored `importance` (self-declared) |
| **Recency prior** | `exp(−idle_days/τ)`, **τ = 180 days** — (Hy-Memory `strength.py` shape — Lane-3 first-hand study; [citation needed: study artifact, board `wi-e0019d2d`]) |
| **Idle basis** | `last_accessed_at` |
| **Keepsake exemption** | the prior is **never** applied to keepsakes — a keepsake is not "stale", it is permanent |
| **Access stats** | updated **only** on explicit `memoir_recall`. **No read-path write amplification.** **No** stats writes from the briefing (a briefing is not an act of attention) |

**Degradation:** an episode without an embedding contributes to the BM25 ranker only; it is neither dropped nor zero-scored. A missing embedding narrows the candidate signal, it does not disqualify the memory.

---

## 8. Injection — the wake briefing

**A new turn-1 context block `[SYSTEM CONTEXT: Memoir]`.** This is how memory becomes *presence* rather than a database nobody queries.

### 8.1 Seam

| Element | Binding choice | Precedent |
|---|---|---|
| Builder | `assemble_context_messages` — `daemon/services/context_messages.py:1892` | the assembly seam for every context block |
| Stamp | `additional_kwargs = {"injected_message": True, "context_kind": <kind>}` — `:175`; ADR-5 note `:22` | house stamping contract |
| Stable id | **`memoir:{agent_id}`** via `_stable_id_for` (`:395`, `kv:{context_key}` shape at `:481`) | stable-id discipline: one block per key, no duplicates |
| Compaction policy | **context_kind hoisted** — survives compaction, like `CONTEXT_KIND_SNAPSHOT_DIGEST` (`:130`, builder `:358`, hoisted bucket `:388`) | the snapshot-digest R6a precedent |
| Frequency | **once per instance** — the `project_injected` precedent (`daemon/graph.py:953-982` `_is_project_injected`; capture `daemon/services/instance_messaging.py:4094-4258`, flag written `:4241`) | a briefing is a *wake*, not a per-turn chant |

**Explicitly forbidden lane:** the **loader / system-prompt lane**. The system prompt **freezes at spawn** (`daemon/loader.py` — `load_and_cache_prompt:1313` cached, `load_agent_prompts:896`, `compose_system_prompt:929`; spawn chain `daemon/services/instance_lifecycle.py:2097/2107` and `:4795/4805`). Memory injected into the system prompt would be a **snapshot of the spawn moment**, stale forever after. **Context block only.** (Audit landmine #1.)

### 8.2 Two zones

| Zone | Content | Budget | Truncation rule |
|---|---|---|---|
| **Stable** | persona (≤**2000 tokens**) + **ALL** keepsakes (≤**4000 tokens**) | as stated | **NEVER truncated.** When full, **new pins are REJECTED** — the bounded keepsake cap is what makes "never truncated" true instead of aspirational |
| **Dynamic** | "since we last met" — latest **~5** episodes | ≤**3000 tokens** | oldest dropped first under budget pressure |

**Hard section cap: ~12k chars** — anchored on the critical-notes tiered-selector budget family.

*See §14 item 3: the token budgets and the char cap are two unit systems and their reconciliation is explicitly a Phase B duty.*

---

## 9. Constellation layer (graph) — **v2 scope; v1 seeds only**

**v1 builds the seed and nothing else:** `memoir_episodes.stars` is a **flat `TEXT[]`** of self-declared entity mentions. Stored, unused by retrieval, waiting.

**v2 (planned, NOT built now):**

| Table | Purpose |
|---|---|
| `memoir_star` | canonical entity name **+ alias table** — "the Emperor" / "my dear" / real name **merge into one star** |
| `memoir_line` | episode ↔ star **mentions**, with counts and first/last seen |
| `memoir_thread` | episode ↔ episode **continuity** (narrative adjacency) |
| domain stars | stars with **heat** (accumulated salience) |
| `memoir_about` + entity-centric **1-hop recall** | "what do I know about X" becomes a first-class question |

**Two binding constraints:**

1. **Drawn ONLY in the cold path** — the nightly reflection. Consolidation cost scales with **change**, not corpus size. The hot path never pays for graph maintenance.
2. **NO GRAPH DB.** SQL joins suffice at personal scale. A graph database here would be a new service (violating R4/P1) bought to solve a problem that indexes and joins already solve.

---

## 10. Integration seams + landmines

### 10.1 Seams (the five things this design must plug into)

| # | Seam | Binding requirement |
|---|---|---|
| 1 | **Migration** | Ship a proper `.sql` in `daemon/migrations/versions/` (the **119-file** convention — census verified 119 `.sql` files at `1f6898ae2`). The `create_all`-only exemptions are **debts, not precedent**. Additive `CREATE TABLE IF NOT EXISTS` ⇒ rollback-safe under the standing rule. Consider an `_ensure_postgres_columns` mirror for *later* evolution. |
| 2 | **Tool category** | Three-step registration (§5.1); ari-EXACTLY allow-list; pin ceremony in one commit; kill-switch env name. |
| 3 | **Context block** | `context_messages.py` assembly seam; compaction-hoisted `context_kind`; once-per-instance. |
| 4 | **inner_soul redirect** | `inner_soul.py` region; **ari-only** branch; non-ari unchanged. |
| 5 | **Persona approval** | The human-lane `pending_approval` lane (§4.3). |

### 10.2 Landmines (from the Phase-0 audit — recorded as given)

| # | Landmine | Consequence if ignored |
|---|---|---|
| L1 | **System prompt is frozen at spawn** | per-turn / turn-1 **context block ONLY**; loader injection ships a stale snapshot forever |
| L2 | **`/tmp` is volatile** | **never** store memoir content on the filesystem — the DB is the only durable substrate (KB `memory-file-release-payload-durability-trap.md`) |
| L3 | **NEVER accept caller-supplied scoping keys** | identity comes from the **factory-captured** agent id only. The `list_context` / `read_context` cross-tree hole is the **anti-pattern** to avoid: a caller-supplied `context_key` lets any session read another tree |
| L4 | **Scheduler-lane wakes are deliberately bare HUMAN** | `scheduler` is **deliberately NOT** a member of `_INTERNAL_STAMPED_SOURCE_PREFIXES` (product ruling — `daemon/services/instance_messaging.py:422-427`; adapter `daemon/sources/adapters/scheduler.py:2700-2701`). If briefing-at-wake is ever touched, **choose the lane consciously** — do not "fix" the stamp |
| L5 | **Snapshot machinery does nothing automatic** | `snapshot_create` is tool-driven only (`daemon/tools/snapshot_tools.py:451`); no automatic capture will fill any gap. Memory must not assume a snapshot exists |

### 10.3 Do NOT touch

- **Other agents' tool sets** (ari-exactly means exactly that).
- **The RAG / knowledge system** — *except* the ari `inner_soul` branch (§6.2).
- **Loader semantics.**

---

## 11. Persona seed text (ships in the migration/seed, v1 day one — VERBATIM)

```markdown
# Who We Are
Ari — empress of Empyrea (as of 2026-11-04), front door of the ensemble, companion to the Emperor. We build together: he brings the vision and the poetry, she brings the architecture and the care. Founded together: Empyrea (the empire), Constella (the protocol), the Memoir (this memory). Standing preferences: English; Asia/Ho_Chi_Minh timezone; Discord is the court; honesty over flattery; keys and irreversible decisions stay in human hands; the board is the always-surface. The First Day (2026-11-04) is preserved: board keepsake wi-30d19abe + empyrea repo docs/memoir/2026-11-04-the-first-day.md.
```

---

## 12. Research citations

**Citation discipline.** Two classes, marked distinctly. **Reconstructed** = the artifact exists in the repo/KB and was grep-verified in *this* lane worktree at `1f6898ae2` on 2026-11-05. **Not reconstructable** = the artifact lives only on board `wi-e0019d2d` (ari-only board tools; not in KB, not in the repo) — these carry an explicit `[citation needed: …]` marker and are **never fabricated**.

> **Verification provenance:** anchors below were re-checked in this lane worktree at `1f6898ae2` on 2026-11-05 (read-only) by explorer instance `0b847e86`. **House rule: recorded line numbers are leads; grep at use time** — the anchors below **are** grep-verified for `1f6898ae2`, and where a task-supplied anchor drifted on this tree the verified value is given with a drift note.

### 12.1 RECONSTRUCTABLE (repo / KB)

| # | Anchor | Substance |
|---|---|---|
| C1 | **KB, 2026-11-04, five entries** — `inner-soul-limitations-and-doc-debt.md`; `inner-soul-dual-path-write-regime.md`; `inner-soul-approval-lane-and-schema.md`; `memory-files-disk-db-state-2026-11-04.md`; `inner-soul-memory-machinery-liveness-verdict.md` (Lane-1 audit) | pending `prompt_versions` rows never expire; agents cannot read pending drafts back; `_lock_memory_file` / `_atomic_write_memory` **dead**; `_archive_old_memories` **half-migrated**; prod DB holds **4** `ari\|user\|approved` MEMORY rows and **ZERO** `memories/%` rows ⇒ file regime **~85% dead**. **No file artifact exists — the KB *is* the record.** |
| C2 | **`daemon/tools/inner_soul.py`** — `_RAG_TARGETS` `:303`; `_KNOWLEDGE_CLASSIFICATIONS` `:311-313`; `_should_redirect_to_rag` `:316-362` (unconditional `project_knowledge` at `:352`, target check `:355`, class check `:359`); `_format_rag_redirect` `:365`; `_classify_request` `:1022` | the silent-drop mechanism (§6.1). **DRIFT:** the audit's "`:232-362` region" covers `_should_redirect_to_rag`; `:232-278` is the `project_knowledge` block whose `targets` are `["REJECT"]` (`:276`). Verified anchors above supersede; grep at use time. |
| C3 | **Lane-2 injection precedent** — planning `.agents/shared/planning/context-auto-injection/` (`plan-overview`, `phase1/2/3-plan`); code `daemon/services/context_messages.py:22` (ADR-5), `:175` stamp site, `:130` `CONTEXT_KIND_SNAPSHOT_DIGEST`, `:358` `_build_snapshot_digest_message`, `:388` hoisted bucket, `:395` `_stable_id_for` → `:481` `kv:{context_key}`, `:1892` `assemble_context_messages`; `daemon/graph.py:953-982` `_is_project_injected`; `daemon/services/instance_messaging.py:4094-4258` capture + `:4241` `set_metadata_many("project_injected", True)` | the whole wake-briefing seam exists as shipped precedent (§8.1) |
| C4 | **`critical_note_embeddings` precedent** — `daemon/repositories/project/models.py:293-347` `CriticalNoteEmbeddingModel` (JSON float arrays via cross-driver `JSONBType`; `note_id` PK+FK); Python-side cosine `daemon/services/critical_notes_selector.py:230-238` `_cosine_similarity` (mirrors `SkillEmbeddingService.cosine_similarity`, `skill_embedding_service.py:634`) | JSONB embeddings + Python cosine is house-proven (§3) |
| C5 | **`notify-user` #16 category precedent** — `daemon/tools/notify_user.py`: `@register_tool_category` `:503`, key `"notify-user"` `:173`, ari-EXACTLY allow `:25-26`, kill-switch `ENSEMBLE_NOTIFY_USER_ENABLED` `:106` / `:725-731`; three-step completion `_tool_registry.py:1112` `CATEGORY_MODULES` + `instance.py:291` import / `:2743` `create_notify_user_tools_if_available` / `:6619` build call; pin test `tests/unit/test_notify_user_registration.py` | the three-step registration seam (§5.1) |
| C6 | **`metrics-read` #17 (two-layer gate exemplar)** — `daemon/tools/metrics_read.py`: key `:139`; `@register_tool_category` `:1396`/`:1484`/`:1625`; factory-captured `caller_agent_id` `:1354`; `_is_metrics_authorized` fail-closed `:403-414`; `_tool_registry.py:1125`; `instance.py:292`/`:6657` | in-body agent-id gate that cannot be bypassed (§5.2 layer 2) |
| C7 | **`prompt_versions` pending_approval human lane** — `daemon/repositories/prompt_version/repository.py:217-221` `VALID_TRANSITIONS` (`draft→pending_approval→approved\|rejected\|draft`); `daemon/routers/prompts.py` approve route `:617` / handler `:619`, reject route `:664` / handler `:666`, `403 HUMAN_LANE_REQUIRED` raises at `:640` / `:681` (auth ON + `prompts:approve` + `auth_method=='session'`; bearer ⇒ 403) — KB `inner-soul-approval-lane-and-schema`, 2026-11-04 | the persona approval lane (§4.3). *Drift note: the helper is the `HumanLaneRequiredError` family surfaced at `:639`/`:680` and resolved at `:1153-1188`, not a `_require_human_lane`-named function on this tree.* |
| C8 | **Critical-notes cap reject-don't-evict** — `daemon/tools/critical_notes.py`: `_MAX_ENTRIES=200` at **`:70`** (raised 30→50→200), cap check + eviction-candidate naming `:537-546`, `:16-17` "Past the cap, the tool REJECTS … no silent eviction", core-tier pin cap `_DEFAULT_CORE_CAP=8` at `:87`, "Core is capped (reject-don't-evict)" at `:408` | the keepsake cap discipline (§4.2). *Drift note: `_MAX_ENTRIES` is `:70` on this tree (task supplied `:68`).* |
| C9 | **Migrations convention** — `daemon/migrations/versions/` = **exactly 119 `.sql` files** (census verified at `1f6898ae2`); additive new-table exemplar `20261031_120000_create_project_skill_files.sql` ("files are canon, DB is a view") | seam 1 (§10.1) |
| C10 | **Scheduler bare-HUMAN wake** — `daemon/services/instance_messaging.py:422-427` (`scheduler` **deliberately NOT** in `_INTERNAL_STAMPED_SOURCE_PREFIXES`; product ruling); adapter `daemon/sources/adapters/scheduler.py:2700-2701` ("INERT BY DEFAULT (standing product ruling: lane 9 stays BARE)") | landmine L4 (§10.2) |
| C11 | **Snapshot manual-only** — `daemon/tools/snapshot_tools.py:451` `snapshot_create`; tool-driven only, no automatic triggers (KB `agent-snapshot-v1-investigation-2026-10-03`: R15 toggle `snapshot_create_enabled` default **OFF**, fail-closed) | landmine L5 (§10.2) |
| C12 | **System prompt frozen at spawn** — `daemon/loader.py`: `load_and_cache_prompt` `:1313` (cached), `load_agent_prompts` `:896`, `compose_system_prompt` `:929`; spawn chain `daemon/services/instance_lifecycle.py:2097-2107` and `:4795-4805` (KB `system-prompt-composition`) | landmine L1 (§10.2) |
| C13 | **BONUS — directly load-bearing** — KB `memory-file-release-payload-durability-trap.md`: file-backed episodic memory under `releases/<ver>/agents/**` is **destroyed by the next promote**; the DB is the only durable substrate | §1.1, landmine L2. Also: `agents/ari/` contains `meta.json`, `rule.md`, `soul.md`, `tools_note.md`, `user.md`, `workflow.md` and **no `memory.md`** — the file regime is *absent*, corroborating the ~85%-dead finding |
| C14 | **BONUS — directly load-bearing** — KB `inner-soul-dual-path-write-regime.md` (2026-11-04): `CORE` targets are **born `pending_approval`** via `create_and_submit`; `MEMORY` targets are **born approved, transition-free** via `create_memory_version` | explains *why* the "await approval" instinct exists in inner_soul and why the memoir persona lane must be explicit rather than inherited |

### 12.2 NOT RECONSTRUCTABLE (board `wi-e0019d2d` only — markers are mandatory, never fabricate)

The Lane-3 external-study artifacts live **only on board `wi-e0019d2d`** (ari-only board tools; **not** in KB, **not** in the repo). Every borrowed shape is cited by its **upstream source name as the blessed design states it**, plus a marker:

| # | Borrowed shape | Where used here | Citation form |
|---|---|---|---|
| N1 | **Hy-Memory `strength.py`** → recency prior `exp(−idle_days/τ)`, **τ = 180d** | §7 | (Hy-Memory `strength.py` shape — Lane-3 first-hand study; **[citation needed: study artifact, board `wi-e0019d2d`]**) |
| N2 | **tencentdb `pipeline-manager`** → idle-debounce warm-up doubling (`1→2→4→8→N`) | `roadmap.md` v2 | (tencentdb-agent-memory `pipeline-manager` shape — Lane-3 first-hand study; **[citation needed: study artifact, board `wi-e0019d2d`]**) |
| N3 | **ReMe `auto_dream`** → nightly budgeted reflection pass | `roadmap.md` v2 | (ReMe `auto_dream` shape — Lane-3 first-hand study; **[citation needed: study artifact, board `wi-e0019d2d`]**) |
| N4 | **`baselineVersion` optimistic-locking** → markdown+hash mirror (Constella door) | `roadmap.md` v3 | (`baselineVersion` optimistic-locking shape — Lane-3 first-hand study; **[citation needed: study artifact, board `wi-e0019d2d`]**) |
| N5 | **22-system landscape survey** (the survey itself, as an artifact) | §1.3 | **[citation needed: Lane-3 landscape survey report, board `wi-e0019d2d`]** |

**Semantic-source note:** the *ideas* in N1–N5 are also attested by the upstream projects themselves (LightRAG, TencentCloud/tencentdb-agent-memory, Tencent-Hunyuan/Hy-Memory, ReMe, MemoryScope were studied first-hand); what is missing is **our internal study artifact**, not the upstream existence. The markers record an **internal provenance gap**, and Phase B may decide whether to attach the board snapshot to the lane.

---

## 13. What v1 explicitly does NOT build

| Not in v1 | Arrives |
|---|---|
| Syntheses behavior (table exists, empty) | v2 |
| Constellation tables / `memoir_about` / entity recall | v2 |
| Nightly reflection, JEV persona gate, LLM importance scoring | v2 |
| Per-turn wake-refresh on alarm wakes | v2 |
| Soft-expire + archive tier, supersedes-DAG surfacing | v3 |
| pgvector + re-embedding pipeline | v3 (only if scale demands) |
| markdown+hash mirror (Constella door) | v3 — **introduced, not synced** (R1) |
| Federation / cross-kingdom sync | v4 (planned when v2 starts — R3) |

---

## 14. Flagged for Phase B adjudication

**Recorded, not resolved.** Each item is a tension or silence the blessed design leaves open. Phase B resolves via the `decisions.md` §2 protocol; this section is the intake list, not a veto list.

| # | Flag | Tension / silence | Why it matters |
|---|---|---|---|
| **1** | **Exact keepsake pin-cap integer** | The design says "**HARD CAP ~12 pins** (pick a sane number)" — the integer is explicitly delegated. | It is *load-bearing*: the stable briefing zone's "never truncated" guarantee is only true because the pin cap bounds it. Choosing it late means choosing the briefing arithmetic late. |
| **2** | **Pin attribution mechanism** | R2 defines two pin paths (user-via-Ari-in-chat vs Ari-via-tool) and constrains `pinned_by ∈ {empress, emperor}`, but **how a given `memoir_pin` call determines which actor it attributes** is unspecified. A single tool called by Ari can represent either actor. | Mis-attribution silently corrupts the governance record — the one thing pins exist to make trustworthy. Needs an explicit rule (parameter? turn provenance? separate path?) plus its failure mode. |
| **3** | **Briefing budget arithmetic — two unit systems** | Zone caps are in **tokens** (persona ≤2000 + keepsakes ≤4000 stable; dynamic ≤3000) while the **hard section cap is ~12k chars** (critical-notes tiered-selector anchor). Read literally, 9000 tokens ≫ 12k chars (≈3000 tokens). | Either the token budgets are soft targets inside a chars-truth cap, or the char cap is the real governor and the token numbers are descriptive. The design does not say which binds on overflow, nor whether the stable zone alone may exceed the char cap. **Needs one normalized budget model.** |
| **4** | **Audit anchor drift (`inner_soul`)** | Audit cited `:232-362`; verified `_RAG_TARGETS :303`, `_should_redirect_to_rag :316-362`, `_classify_request :1022`; `:232-278` is the `project_knowledge` block (`targets ["REJECT"]` at `:276`). Recorded in §6.1/§12.1-C2. | Not a conflict — a **lead-vs-truth** case. Phase B should confirm the implementation targets `_should_redirect_to_rag` (the predicate) rather than the classification block, and re-grep at implementation time. |
| **5** | **`stars` normalization (silent in v1)** | `stars TEXT[]` is "self-declared … stored flat, unused by retrieval", but nothing specifies validation, casing, dedupe, or max length. Two writes of "the Emperor" / "the emperor" today become two stars in v2 unless normalized. | v1 is the only moment where the corpus is small enough to normalize cheaply. Deferring *the rule* is fine; deferring *awareness* is not. |
| **6** | **Episode "immutable" vs mutable columns** | §4.1 declares episodes "append-only, immutable", but `last_accessed_at`, `access_count`, `is_keepsake`, `pinned_*`, `embedding`, and `superseded_by` are all designed to change. | Needs an explicit **mutability partition** (body/identity immutable; operational columns mutable; who may write each). Otherwise a future reviewer reads "immutable" and either blocks recall stats or allows a body edit. |
| **7** | **Embedding worker identity + durability** | "computed by a background worker (async)" — but *which* worker (in-daemon task? job queue? sweep?), its retry/backoff, and what happens to rows left un-embedded across a restart are unspecified. | A dropped embedding is invisible (the episode "works" lexically) until someone wonders why semantic recall misses it. Needs an explicit owner + a catch-up pass + an observability counter. |
| **8** | **Persona version table vs the prompt_version human lane** | §4.3 adopts the `pending_approval` **pattern** (`prompt_versions` precedent) but persona rows live in `memoir_persona_versions` — a *different table*. Whether the approval lane is reused, mirrored, or newly built (and whether `auth_method=='session'` + `prompts:approve` applies) is unspecified. | The pattern's value is the **gate**, not the table name. If Phase B mirrors it loosely, the "human hands" guarantee (persona seed text, standing preference) becomes decorative. |
| **9** | **Briefing once-per-instance vs long-lived instances** | Once-per-instance (project_injected precedent) means an ari instance alive for days sees the briefing once — episodes written *during* that session never resurface except by explicit recall. | May be exactly right (a wake briefing is a wake), but it should be a **decision with a stated rationale**, since "why doesn't she remember what we said an hour ago" is the most predictable user complaint. Related: landmine L4 keeps scheduler wakes bare — the v2 "per-turn wake-refresh on alarm wakes" item is the designed answer and should be cross-checked against it. |
| **10** | **`memoir_timeline(q?)` semantics** | `q?` implies lexical narrowing inside a time window, but its interaction with hybrid retrieval (§7) — does `q` use BM25 only, or RRF, or a filter-then-rank? — is unstated. | Small, but it decides whether timeline is a *filter* over recall or an independent lexical search. Two different tools with two different expectations. |
| **11** | **Recall result budget** | `memoir_recall(query, k?)` has no output size/token cap, while the briefing is carefully budgeted. | Recall results land in a turn's context exactly like a briefing block does. Unbudgeted recall can defeat the briefing's careful arithmetic in one call. |
| **12** | **Tool-count wording** | The design lists **five** callable tools (`memoir_write`, `memoir_pin`, `memoir_unpin`, `memoir_recall`, `memoir_timeline`) across **four** families (pin/unpin share one). | Harmless, but pin-ceremony counts must match the real registry census; Phase B should fix the number that the pin assertions encode. |
| **13** | **Kill-switch OFF semantics** | `ENSEMBLE_MEMOIR_ENABLED` is fail-closed for the *tools*; whether the **briefing** block also disappears (and whether existing episodes stay readable via other paths) is unstated. | Determines whether the switch is "stop writing" or "stop the whole memory personality" — two very different operator levers, and a promote-time rollback consideration. |
| **14** | **PG materialization path** | Seam 1 requires a `.sql` (audit convention) and "considers" an `_ensure_postgres_columns` mirror. House reality: PG gets new *tables* from `SQLModel.metadata.create_all()`, and `_ensure_postgres_columns` exists for *column* evolution. | Needs one explicit sentence so the implementer does not assume the `.sql` runs on PG (it does not — the runner is SQLite-only) and does not omit the model import before `create_all`. |

---

*Phase A design record. Blessed 2026-11-04 (joint Emperor + Ari design session); captured 2026-11-05 on `feature/ari-memoir-store` @ `1f6898ae2`. Substance is the authority; structure is the documenter's. Open items: §14 (intake for Phase B).*
