# POK — Project Operating Knowledge — Architecture Recommendation (v3.3)

**Commission:** project-operating-knowledge (POK) — capture, storage, injection, retrieval, and currency of operational/procedural project knowledge.
**Tree:** v0.29.0 @ 7426e0d8a (worktree `feature/project-operating-knowledge`, fenced dev). All cited lines/constants RE-VERIFIED at HEAD.
**Method:** Architect-planned; single council (governor `74d0a09a`, skill `data-flow-design`). **Confidence: MEDIUM** — single-source council (proxy `coding`-alias defect). Corroboration post-D7 (§14).
**Status:** DESIGN ONLY — implementation is NOT authorized. The Emperor decides D1, D3–D5 and P-1; **D2 is now DECIDED (allowlist, §4.8)**. Markdown only; no commits.
**Revision note (v3.3 — LEADER DISPOSITION FOLD, approver cap reached):** the leader accepted all 8 iteration-003 findings and decided the two that needed decisions. Implemented here without re-litigation: **B1** block cap defined on the FULL rendered block incl. the wrapper (47c) → **2,900**; **B2** named **Phase 1.5 live-seeding** step (dev proof ≠ live truth until it runs); **B3** fact-5/6 contrast made ONE atomic family (`deploy-01/02`) + family regex tightened; **B4** grant-sweep integrity rules (watcher excluded, `nest-report` LAST, pin test cited); **B5** retrieval reuses the shipped critical-notes selector (invented fusion weights DELETED; pinned bypass the threshold); **B6+B8** metadata allowlist variant shipped in v1 (**D2 DECIDED**); **B7** PG registration surface named (`daemon/manager.py` pre-create_all imports; AC9 asserts materialization). Prior folds stand (C1/W5/F1/F3). Controller edits preserved.

---

## 0. Executive summary

Operational project knowledge ("deploy daoduc BACKUP first, verify, then ensem.dev PRIMARY") is learned in lanes and dies in transcripts compaction erases. **No existing surface can deliver it** — the bar: *no incumbent can deliver **guaranteed injection of ORDERED procedures** to every agent whose instance carries a non-default `project_id`* — mechanism-backed: ordered procedures are written as **slug families** (tightened regex `^([a-z][a-z0-9]*)-(\d{2})-(.+)$`), the block renders **families atomically in slug order**, and a **pinned member carries its whole family to the pinned position**; the fact-5/6 environment contrast is itself ONE family, so a one-sided render is structurally unreachable.

The recommendation: a **new `project_operating_knowledge` table pair in the ensemble PG, keyed by `project_id`, NO FK** — canon **A: DB-canon daemon-side**. Delivery: a **new deterministic injection block** — **`POK_BLOCK_MAX_CHARS = 2,900` measured on the FULL rendered block INCLUDING the `_make_context_message` wrapper** (`[SYSTEM CONTEXT: ` 17 + title 27 + `]\n\n` 3 = **47c**, `context_messages.py:75-76/:181-182`; in-file precedent `_SNAPSHOT_DIGEST_WRAPPER_TOKENS` `:205-230`); content worst 2,764 + 47 = **2,811 ≤ 2,900**; pinned tier 2,138 + 47 = **2,185 ≤ 2,900**. Retrieval: **reuse of the shipped critical-notes selector** (`critical_notes_selector.py` + `critical_notes_selection_orchestrator.py` — hybrid search with configurable fusion weights/threshold; **no POK-invented weights**), with **pinned rows bypassing the threshold**. Metadata companion: **v1 ships the ALLOWLIST variant** (D2 DECIDED) — a named policy/tracking key set renders verbatim; everything else capped 2000c/key with a 16384c block + omitted-count tail. **The walkthrough is TRUE in dev after Phase 1; it becomes true on the live install only when Phase 1.5 (live seeding) runs after the next promote carries the schema** — stated as the honest boundary.

---

## 1. The defect and the acceptance walkthrough

**The Emperor's problem (verbatim intent):** the system has critical notes, metadata, project history — loaded when a leader job starts with the right context. The problem: how can the system ACTIVELY inject context or let an agent PASSIVELY load project-related experience, so the next time an agent works the llm-supervisor-proxy project it already knows the operational truth learned in a prior lane — and that truth must be UPDATABLE over time and known by the leader AND every other agent on the project.

**Acceptance test:** the next agent on llm-supervisor-proxy must know, or retrieve in ONE step: (1) deployable artifact = container image, not a source build; (2) merge to master triggers the docker CI build — nothing ships without it; (3) after build verify the new image (tag + digest + feature actually present); (4) deploy order: daoduc (BACKUP) FIRST → verify → then ensem.dev (PRIMARY); (5) daoduc = Helm release v28 + kubectl patch/rollout vs ensem.dev = plain kubectl apply, no helm release; (6) /healthz NOT publicly exposed — verify in-cluster or false negative; (7) model catalogs differ 19 vs 22 and that is NOT deploy drift. *(The Emperor's enumerated examples are a subset of this 7-fact walkthrough — the walkthrough is the binding superset.)*

Today that list lives only in a transcript that compaction will erase. **That is the defect.**

---

## 2. Evidence layer map — load-bearing vs dead weight for THIS requirement

All cited line numbers re-verified at HEAD. All DB-stored data is NOT git-versioned except project-skill files.

| # | Layer | Mechanics (receipts at HEAD) | Verdict for POK |
|---|-------|------------------------------|------------------|
| 1 | **Critical notes** (`critical_notes` + `critical_note_embeddings`) | Injected first-turn for **all agents whose instance carries a NON-DEFAULT `project_id`** (the system-default project renders the scope guide instead — the v3.2 "ALL project-linked agents" quantifier was overstated; corrected per leader disposition); pinned + tail tiers, `section_char_cap` 12000, `context_messages.py:822-1013`; **references ARE rendered** — `*(ref: …)*` bounded at 500c (`context_messages.py:1030-1040`; the v2 claim "reference not injected" was FALSE and is retracted). Writers: leader + PM only. Fields ≤200/≤500. `_MAX_ENTRIES = 200` (`critical_notes.py:70`) — the v1 "cap 50/saturated" claim RETRACTED. | **Load-bearing for traps/conventions. Rejected as POK carrier** (§6.2) on field-caps + writer + genre grounds. |
| 2 | **Infra assets** (`infra_assets` + `infra_asset_types` + `infra_asset_history`) | `daemon/repositories/infra/models.py:107/:191/:197` (UNIQUE project·type·name) `/:205/:210` (GIN) `/:280` (version lock) `/:350` (append-only history); 9 tools (`_tool_registry.py:1589-1597`); **allow-granted to exactly 3 agents (devops/wanderer/worker); critic names `infra` under DENY**. | **Rejected as POK carrier** (§6.9). |
| 3 | **Project metadata** (`project_metadata_records` JSONB; `daemon/repositories/project/models.py:359-374`) | Uncapped, unfiltered ride into the Related-Project block. **Economics measurements, two bases stated once (reconciled per leader disposition):** the **metadata dict basis** = 69 keys / **279,810 B** of serialized metadata values; the **whole-project-JSON basis** = 408,809 B (project row + metadata + history rendered together) — so the metadata dict is **68.4 % of the whole project JSON** (279,810 / 408,809). The investigation's "82 %" figure was a share-of-injected-head reading with an unstated denominator; it is NOT independently reproducible from these two numbers and is withdrawn as a computed claim. **AC12 re-measures live and recomputes both bases.** | **Load-bearing keys in a dump.** Capped via the v1 ALLOWLIST (§4.8, D2 DECIDED). Not a carrier. |
| 4 | **Project history** | Append-only timeline; last 10 injected. | **Partial.** Timeline, not knowledge base. |
| 5 | **KB/RAG** (LightRAG, keyed by project NAME) | `explore()` = explorer child, 300s; never injected. | **Structurally mismatched.** |
| 6 | **Memoir** (v0.29.0, 4 tables) | AGENT-scoped — **4 quoted-`ari` occurrences across 3 files within `daemon/repositories/memoir/` at HEAD** (scoped census); no project column; zones; once-per-instance gate; write-time admission; `superseded_by` UNWIRED; decay/stats. | **NOT a carrier — the pattern source.** |
| 7 | **Board** | Human-facing, ari-only, no injection. | **Not a carrier.** |
| 8 | **Blueprint** (4 tables) | Async blueprinter-only writer; once-per-instance injection; first-user-message-only query gate; uncapped verbatim; **genre ban = PROMPT-DOC convention (build-blueprint.md:25/:33), not code-enforced** — code-verified grounds: async writer, turn-1 gate, no supersede. | **Seam-shape donor.** Rejected (§6.3). |
| 9 | **Project-skill-loader v2** (files) + **DB dynamic-skill tier** | Files canon; PR-only writes; DB tier: skill bank + BM25→embedding→LLM injection pipeline — gated on `skill_injection=true` (**11 of 40 registered agents**), max-2, relevance-gated. | **Best substrate; insufficient as carrier** (§6.4). |
| 10 | **Shared Meta KV** (`shared_meta_kv`) | Tree-scoped KV: `context_key` = **tree-root instance id** (`shared_meta_kv_tools.py:4/:37/:78-79/:113-122`); **row cap 4096 chars** (`repository.py:180/:210/:240-242`); block builder escapes content and skips >32k **with the cap AFTER the escape** (`context_messages.py:1338-1425`, escape `:1403`, rationale `:1395-1403`); **allow-granted to 28 of 40 registered agents** (29 mentions incl. critic's DENY — "ari-only" retracted). | **Rejected as POK carrier** (§6.8). **Pattern donor** — stable-id refresh, escape fence, cap-after-escape sizing. |
| 11 | **Parked guidelines** (wi-d2abe4f7) | Dev-curated immutable reference; subset questions. | **Fold** as trust tier (§8-iv). |
| 12 | **`.agents/shared/`** | 49,999,702 B / 2005 files; no daemon CODE injection reader (`routers/blueprints.py:583` seeds a job prompt only). | **Dead weight as a delivery channel.** |
| 13 | **Prompt-memory artifact family** | DB-versioned MEMORY-class rows; `load_recent_memories` (`daemon/loader.py:780-807/:1490`) surfaces a **FILENAME INDEX**; `access_memory` (`daemon/tools/access_memory.py:174`); `create_memory_version` (`daemon/services/prompt_version_service.py:1030`). | **Non-carrier:** agent-scoped persona memory. |
| 14 | **Snapshots** (`daemon/repositories/snapshot/models.py:105/:164/:255/:324`) | Checkpoint-archived instance warm-start copies. | **Non-carrier.** |
| 15 | **Source configs** (`daemon/sources/persistence.py:32-88`) | Message-source channel config + credentials. | **Non-carrier.** |
| 16 | **Injection seam economics** | Orchestrator `context_messages.py:2022`; landing `instance_messaging.py:4825-4830`; outer swallow **`:5083`** (`except Exception as _persist_exc` — a raising builder deletes ALL persistent blocks → builders never raise). Code-site anchors: turn-1 `:2230-2335`, refresh `:2179/:2287/:2328`. No global injection budget; hoisted blocks never compact and count toward the trigger. **Wrapper constants `CONTEXT_PREFIX`/`CONTEXT_SUFFIX` at `:75-76`; `_make_context_message` title `:191`; wrapper-cost-subtraction precedent `:205-230`.** | **The constraint surface.** |

---

## 3. The canon decision — **A: DB-canon daemon-side** (unchanged)

One authoritative surface: `project_operating_knowledge` in the ensemble PG, keyed by `project_id`. Derived git export optional phase 2 — never authoritative, never read. Defense (six points, unchanged). **The reuse bar is met separately in §6** on: *no incumbent can deliver guaranteed injection of ORDERED procedures to every agent on a non-default project* — mechanism-backed (§4.5), bounded on rows and chars, and measured on the FULL rendered block including the wrapper (B1).

---

## 4. The recommended design (concrete)

### 4.1 Storage layer

```
project_operating_knowledge
  id TEXT PK (uuid4) · project_id TEXT NOT NULL (NO FK) · slug TEXT NOT NULL  -- ≤ POK_SLUG_MAX_CHARS (44), validated at write
  domain TEXT NOT NULL DEFAULT 'other'   -- CHECK: artifact|deploy|verify|environment|catalog|other
  body TEXT NOT NULL                     -- escaped-body budget ≤ 240 (§4.2, W5)
  provenance TEXT NOT NULL DEFAULT 'observed'  -- CHECK: declared|observed
  status TEXT NOT NULL DEFAULT 'active'  -- CHECK: active|superseded|archived  (archived REACHABLE via pok_curate — §4.3)
  disputed BOOLEAN NOT NULL DEFAULT FALSE
  superseded_by_id TEXT (plain TEXT, WIRED) · content_sha256 TEXT NOT NULL
  created_by_agent TEXT NOT NULL · created/updated/last_verified TIMESTAMPTZ
  verify_count INTEGER · access_count INTEGER · last_accessed_at · pinned BOOLEAN DEFAULT FALSE

UNIQUE PARTIAL INDEX uq_pok_active_slug (project_id, slug) WHERE status = 'active'
project_operating_knowledge_embeddings (entry_id, embedding)  -- ranking-only companion
```

Two additive `.sql` files (house rule), CHECK-closed vocabularies, no FK → rollback-inert. **PG registration surface (B7):** the new models MUST be imported in the **pre-create_all registration block in `daemon/manager.py` (the `# register before create_all` import block, ~`:928-987` — app_settings, resource_monitor, board, project_skill_file, memoir all register there; `SQLModel.metadata.create_all` is the PG source-of-truth per `:129/:931/:942/:956/:980`)** — without the import, the tables never materialize on PG. Genre split unchanged.

### 4.2 Write path — the crux (≤2 calls) + scope predicate + fence + escape-aware budget

`pok_upsert(slug, body, domain='other', provenance='observed')`

- **Scope predicate:** `project_id != SYSTEM_DEFAULT_PROJECT_ID` (`project_normalizer.py:6-45` runs unconditionally at `instance_lifecycle.py:1895`; `constants.py:596-597`; `api.py:2656`). Default → block suppressed; `pok_upsert` → typed `pok_default_project_unsupported`; `pok_query` → typed `pok_no_project`.
- **Writers:** all agents on a non-default project. Attribution = factory-captured `agent_id`; **attribution ≠ authorization**.
- **Slug bound:** `[a-z0-9-]`, **≤ 44** (typed `pok_slug_oversize`).
- **Write-time fence:** typed `pok_body_structural_token` on newline / **leading `#` at body start** / literal `[SYSTEM CONTEXT:` / ``` fence.
- **Escape-aware body budget (W5):** `len(escape_for_context_block(body)) ≤ 240`; violation → typed **`pok_body_oversize`**.
- Transaction: exact-dup `content_sha256` → no-op echo (refreshes `last_verified_at`); normalized-hash dispute-laundering guard → `pok_disputed_content`; else supersede txn; upsert onto disputed flips it superseded; **upsert onto ARCHIVED flips it superseded (forward-only, never resurrects) + fresh guarded active row**.
- Admission: `POK_ACTIVE_CAP_PER_PROJECT = 64` refuse-never-evict (refusal names the oldest-least-verified slug AND the archive verb); each family member consumes one active slot. **Family admission bound:** >3 ACTIVE members → typed `pok_family_full`.
- **Grant model:** `pok` NOT privileged; default-granted, scope-gated — **sweep integrity rules in §7 (B4)**.

### 4.3 Update / supersede / curate / archive — authorization model

- Append-with-supersede ONLY (ONE ACID txn; unique partial index forbids dual-active).
- **Authorization matrix:**

| Operation | Non-pinned | Pinned |
|---|---|---|
| `pok_upsert` (supersede) | any project-linked agent | leader/PM only → `pok_pinned_requires_curator` |
| `pok_curate(pinned / disputed / archived)` | leader/PM only | leader/PM only |
| `pok_verify` | **phase 2** (deferred) | phase 2 |

- **`pok_curate(slug, pinned=None, disputed=None, archived=None)`** — pin admission `POK_PIN_CAP_PER_PROJECT = 2` (3rd pin → `pok_pin_cap`); dispute suppresses injection, stays readable; archive frees the active slot, row stays readable. Archive ships in v1 (B2' choice).
- Recording surface: typed echo + `ens_system_log` WARNING; no DB refusal ledger.
- `pok_verify` DEFERRED to phase 2; v1 = 3 tools.
- Suspected-wrong protocol: lineage → non-pinned correction / pinned escalate. `[stale]` = age; `disputed` = wrongness.
- Decay `exp(-idle/30d)` + `[stale Nd]`, never silent drop.

### 4.4 Trust / provenance (unchanged)

declared/observed inline; `[DISPUTED]`; ranking = decay × observed × access; single-active-version-per-slug; lineage always in `pok_query`.

### 4.5 Injection layer — deterministic, ordered, fenced, wrapper-inclusive bounded

- Block `## Project Operating Knowledge`; builder `build_pok_context_message`; ~7 touch-points; placement by CODE SITE.
- **Membership/admission:** **pinned tier first — ≤2 pinned members; a pinned member CARRIES ITS WHOLE FAMILY (C1-A), adjacent, in slug order, at the pinned position; a doubly-pinned family renders ONCE and is counted ONCE on rows AND chars (F4)** → ranked actives (disputed excluded) → **block-total rider (F5): pinned tier then actives, stopping where rendered chars would exceed `POK_BLOCK_MAX_CHARS` OR entries would exceed `POK_BLOCK_MAX_ENTRIES`; the tail counts the remainder.** Families atomic — **including the fact-5/6 contrast, which is ONE family (B3): a one-sided render of the contrast is structurally unreachable.**
- **EXCLUSION × DRAG (F3):** excluded rows NEVER render, in any tier — a pinned family drags its SURVIVING members, contiguous and in slug order; pinned-member exclusion cancels the drag; zero survivors → family renders nothing; the tail remainder count is the only trace.
- **ORDERING MECHANISM (regex tightened per leader disposition, B3):** `<family>-<NN>-<slug>`, zero-padded; **`^([a-z][a-z0-9]*)-(\d{2})-(.+)$` — family = a single hyphen-free token.** The v3.x `^(.*)-(\d{2})-(.+)$` mis-parses real slugs (e.g. `daoduc-helm-release-v28-kubectl-patch` would parse as family `daoduc-helm-release-v` step 28; `model-catalogs-19-vs-22-not-drift` as family `model-catalogs` step 19); the tightened regex leaves those UNORDERED. Members render adjacent in full-slug lexicographic order at the family's best-ranked (or dragged) position; never split.
- **BOUNDED ARITHMETIC — wrapper-inclusive (B1; DERIVED):**
  - Render overhead per entry, derived: `- ` 2 + `[environment]` 13 + ` ` 1 + slug ≤44 + ` ` 1 + `(`markers`)`: ` 4 + markers ≤27 (closed two-token vocabulary: `[observed]` 10 + ` ` + `[stale 999d]` 12 = 23 realized; the 4c slack absorbs ≤7-digit day counts) = **92c**. Entry ceiling = escaped body 240 + 92 = **332c**.
  - Content total = header ≤40 (derived 31) + 8 × (332 + join 1) + tail ≤60 = **2,764**.
  - **Wrapper (B1):** `CONTEXT_PREFIX` 17 (`[SYSTEM CONTEXT: `) + title 27 (`Project Operating Knowledge`) + `CONTEXT_SUFFIX` 3 (`]\n\n`) = **47c** (`context_messages.py:75-76/:181-182`; the same file's `_SNAPSHOT_DIGEST_WRAPPER_TOKENS` `:205-230` documents the class and subtracts wrapper overhead from the body budget).
  - **FULL rendered block worst = 2,764 + 47 = 2,811 ≤ `POK_BLOCK_MAX_CHARS = 2,900`** (89c slack). **Pinned tier = 2,138 + 47 = 2,185 ≤ 2,900.**
  - **Joint invariant:** `POK_PIN_CAP × POK_FAMILY_MAX = 6 ≤ 8` AND full-block worst 2,811 ≤ 2,900 — `pinned ⊆ block` PROVABLE on rows AND chars **including the `_make_context_message` wrapper** (no "proven" claim without it).
- **CONTENT FENCE (layer 2 — render-time):** fixed list-item template; body through `escape_for_context_block` (`:699`) + newline flattening. No structural token survives. Canary doctrine (staged v0.28.15). Fenced-verbatim.
- **Once-per-instance flag semantics:** flag writes ONLY on successful block render — store-down omission retries next turn.
- Reach/predicate/stable id/once-per-instance v1 unchanged.

### 4.6 Retrieval layer — one step, project-scoped, selector-reuse (B5, decided)

`pok_query(query='', domain=None)`:

- **REUSE the shipped critical-notes selector infrastructure — do NOT invent fusion weights.** The implementation reuses `daemon/services/critical_notes_selector.py` (the compute-fusion selection pipeline) + `daemon/services/critical_notes_selection_orchestrator.py` (hybrid search with **configurable** `fusion_bm25_weight` / `fusion_vector_weight` / `fusion_threshold` — NOT hardcoded). The v3.x "≥ 0.25" threshold claim is **DELETED** — it exists in no code. (Receipt-honest: the shipped defaults ARE `fusion_bm25_weight=0.4` / `fusion_vector_weight=0.6` / `fusion_threshold=0.30` — `critical_notes_selector.py:68-70`, configurable via `CriticalNotesConfig`; POK prescribes NO weights of its own and configures the shipped knobs.)
- **Pinned rows BYPASS the retrieval threshold** (pinned-always-returns, mirroring the blueprint core's forced score 1.0); the selector's threshold applies to **non-pinned rows only**.
- Family chains render in slug order (same adjacency + F3 exclusion semantics; excluded rows appear only on explicit status-inclusive requests). Empty query = inventory. Scope resolved server-side. Typed errors: `pok_no_project` / `pok_store_unavailable`; embedding lag → BM25-only + `embedding_lag: true`.

### 4.7 Sizing constants

| Constant | Value | Role |
|---|---|---|
| `POK_BODY_MAX_CHARS` | 240 | **counts the ESCAPED body (W5)** — typed `pok_body_oversize` |
| `POK_SLUG_MAX_CHARS` | 44 | slug bound (F1) |
| `POK_ENTRY_RENDER_MAX_CHARS` | 332 | escaped body 240 + derived overhead 92 (F1) |
| `POK_ACTIVE_CAP_PER_PROJECT` | 64 | refuse-never-evict (curable via curator archive) |
| `POK_PIN_CAP_PER_PROJECT` | 2 | pin admission; with FAMILY_MAX enforces rows ≤ 6 ≤ 8 |
| `POK_FAMILY_MAX` | 3 | family admission bound (`pok_family_full`) |
| `POK_BLOCK_MAX_CHARS` | **2,900** | **FULL rendered block INCLUDING the `_make_context_message` wrapper (47c) — B1**; worst 2,811 ≤ 2,900 |
| `POK_BLOCK_MAX_ENTRIES` | 8 | entry cap (families count per member; PIN×FAM = 6 ≤ 8) |
| `POK_RETRIEVAL_THRESHOLD` | **selector-configured (reused; non-pinned only)** | **B5: the shipped selector's `fusion_threshold`; no POK-invented value; pinned bypass** |
| `POK_VERIFY_WINDOW_DAYS` | 30 | decay window (tuning 🟢) |

### 4.8 REQUIRED companion — Related Project metadata: v1 ships the ALLOWLIST variant (D2 DECIDED, B6+B8)

- **Named ALLOWLIST of policy/tracking keys renders VERBATIM (no per-key cap):** start from this project's live key set — `ari_plane_tracking_policy`, `plane_workspace_discipline`, `deferred_key_rotation`, `deferred_key_rotation_f7_addition`, `upgrade_policy`, `upgrade_workflow_preference`, `blueprint_active`, `dependency_self_install_philosophy`, `self_provisioning_directive`, `opendesign_decisions`, the `plugin_*` policy keys (`plugin_direction_status`, `plugin_path_model`, `plugin_architecture_philosophy`, `plugin_staleness_policy` — exactly the four live `plugin_*` keys), `staged_release_v0290_ceremony_notes`. The list is a **leader-maintained artifact** (seeded from the live set above; refinable, but NAMED here).
- **All non-allowlisted keys:** capped at **2000c/key**, section block **16384c**, with an **omitted-count tail**.
- **Render order:** allowlisted keys first, verbatim; the capped section follows; the tail closes.
- **NO new read tool (do not add exposure surface).** The recovery path is the allowlist itself — load-bearing keys are never omitted; for forensic-archive keys, on-demand reach is **`project_get`** for the 5 privileged agents (ari/devops/governor/jober/leader) — stated honestly, not papered over.
- **Economics (two bases, stated once):** metadata dict 279,810 B / 69 keys; whole project JSON 408,809 B; 279,810/408,809 = **68.4 %** of the JSON; the investigation's "82 %" is withdrawn as a computed claim (unstated denominator) — **AC12 re-measures live and recomputes both bases**.
- Residual named: allowlisted verbatim values (e.g. `staged_release_*` ceremony notes) can be large; allowlist membership is reviewed, and the block's real size is measured at AC6/AC12.

### 4.9 Degradation contract (mirrors `build_shared_meta_kv_message`, `context_messages.py:1338-1425`)

| Failure | Behavior |
|---|---|
| Empty active set | `None` |
| Store unavailable / exception | `None` + WARNING naming reason; other blocks intact; once-flag NOT written (retry next turn) |
| Malformed row | row skipped + WARNING |
| Oversize at render | entry-boundary truncation + tail — **provably unreachable** given write-side bounds (escaped body ≤240, slug ≤44) + F5 stop rule + the wrapper-inclusive cap 2,900; retained as backstop |
| `disputed`/`superseded`/`archived` | excluded from membership; archived readable via query; **excluded rows never render, including inside a pinned family's drag (F3)** |
| Default project | block not built |
| `pok_query` store-down / lag | `pok_store_unavailable` / `embedding_lag: true` |
| Body content fence | write-time rejection + render-time escape/flatten |
| Escape-aware budget | enforced on the ESCAPED body at write |
| **Block cap (B1)** | measured on the FULL rendered block **including the 47c `_make_context_message` wrapper** — no unwrapped "proven" arithmetic anywhere |

---

## 5. Design questions A–H — where answered

| Q | § | Q | § |
|---|---|---|---|
| A Ownership | 3, 4.1, 6 | E Update/staleness | 4.3 |
| B Active injection | 4.5 | F Trust | 4.4 |
| C Passive retrieval | 4.6 | G Sizing | 4.7 |
| D Write path | 4.2 | H Migration | 11 + 10 (Phase 1.5) |

---

## 6. Rejected alternatives — the SPECIFIC structural reason each loses

**Bar:** *no incumbent can deliver GUARANTEED INJECTION OF ORDERED PROCEDURES to every agent on a non-default project* (mechanism §4.5).

1. **File-canon:** PR-only writes break ≤2 calls; relevance-gated opt-in (11/40) delivery; wrong anchor; infra topology in pushed trees.
2. **Critical-notes evolution:** summary ≤200 / reference ≤500 — references ARE injected (`context_messages.py:1030-1040`; contrary v2 claim retracted) — but 500c cannot hold an ordered runbook; alert-triage selection; leader/PM-only writers.
3. **Blueprint-as-carrier:** async writer; first-message-only gate; no supersede/verified-at (code-verified); genre ban = prompt-doc convention.
4. **Project-skill-loader (files) / DB dynamic-skill tier:** opt-in 11/40; max-2 relevance-gated; PR-only (files).
5. **Metadata-JSONB:** uncapped dump, no lineage.
6. **Memoir extension:** wrong scope/writer/genre.
7. **Board:** no injection path.
8. **Shared Meta KV:** tree-scoped `context_key`; 4096-row cap; per-tree friction; no retrieval; no lifecycle. Donates stable-id refresh + escape fence + cap-after-escape sizing.
9. **`infra_assets`:** no injection seam; 3 allow-grants + critic deny; asset genre; history = mutation audit; version lock = concurrency.
10. **Prompt-memory family:** agent-scoped persona; filename-index surface — not project knowledge.

**C1 option (C) — family-scoped pins — REJECTED:** contradicts member-pin semantics; adds indirection; still needs the same bound.

Every rejected carrier is a store (1–10) or a non-carrier row in §2 — the enumeration closes (sixteen rows).

---

## 7. Reuse verdict — carries, NEW, registration, grant-sweep integrity (B4)

**Carries over:** critical notes, blueprint (+patterns), project-skill loader, memoir, board, history, infra assets, shared_meta_kv, prompt-memory, snapshots, source configs.

**Borrowed:** critical-note embeddings infra; **the critical-notes selector + orchestrator (retrieval reuse, B5)**; memoir admission idioms; blueprint seam patterns; metrics-read two-layer gate; agent-secrets posture; KV stable-id idiom + escape fence + cap-after-escape; house generator.

**NEW pieces:** table pair (+`disputed`, reachable `archived`) · `pok` category + **3 tools v1** · `build_pok_context_message` + 7 touch-points · metadata allowlist renderer.

**Registration surfaces:** `TOOL_REQUIRED_AGENTS` → NO entry; `VOCABULARY_VERSION` (`checkpoint_metrics.py:244`, =6) → 7; `.agents/shared/metrics/tool-categories.json` regenerated 60→61 via the house generator; ordinal-free (privileged ordinal stays 19). **Same-commit re-pins named (B7):** `tests/unit/metrics/test_metrics_vocabulary_amendment.py` and `tests/unit/tools/test_tool_category_artifact.py` (the leader-named `tests/unit/test_*` paths resolve here). **PG registration (B7):** models imported in `daemon/manager.py`'s pre-create_all block (§4.1).

**Grant sweep — integrity rules (B4, normative):**
1. **`agents/watcher/meta.json` has an EMPTY allow = inherit-all (`_tool_registry.py:326-340`: "an absent/empty allow list = everything is potentially allowed … watcher is empty-allow today"). NEVER append to watcher's allow — appending ANY category to an empty allow STRIPS its inherited universe and silently deletes its whole toolset. Exclude watcher from the sweep; it inherits `pok` automatically (non-privileged).**
2. **`"nest-report"` carries the tail position in every non-empty allow that HAS it — insert `"pok"` BEFORE it** (pin test: `tests/unit/test_anomaly_report_grant_pins.py` — "every NON-EMPTY tools.allow carries `nest-report` as the LAST element"; HEAD exceptions the sweep handles: `ari` (memoir sits AFTER nest-report — insert `pok` before the `nest-report` entry, memoir stays last) and `canary` (no `nest-report` entry — append `pok` at the array end per the default-grant rule); the pin test's `EXPECTED_EXPLICIT`/`EXPECTED_TOTAL_META` constants are stale at HEAD (46 metas / 44 explicit) and are re-derived in the same commit; the establishing ceremony touched 40 meta.json files — the registered grants plus the `_baby_template`/`_mother`/`[v2]` specials).
3. **Sweep enumeration:** registered agents = `ls agents/*/meta.json | grep -Ev '\[v2\]|_baby_template'` = **40 at HEAD**; for each NON-EMPTY allow (39 files after the watcher exclusion), insert `"pok"` immediately before `"nest-report"` (exceptions per rule 2: `ari` → before its `nest-report` entry; `canary` → append at end); regenerate `.prompt-artifacts.sha256`; run the category pin ceremony. (The sweep rule + the pin test are the two guards; without both, an implementer silently strips the watcher and breaks the LAST rule.)

---

## 8. The four seam questions (unchanged)

(i) NEW block; (ii) per-block cap sufficient, metadata companion (now allowlist-shaped); (iii) once-per-instance v1 + `pok_query` freshness, stable id day 1 (flag on success only); (iv) guidelines un-park as phase-2 trust tier.

---

## 9. Leader leanings — adjudication

1–8 unchanged. v3: ordering mechanism, content fence, archive verb. v3.1: pinned-drag bound. v3.2: char keystone derived (Option A) + F3 exclusion ruling. **v3.3: leader disposition folded — wrapper-inclusive cap (B1), live-seeding boundary (B2), atomic contrast family (B3), sweep integrity (B4), selector reuse (B5), allowlist shipped (B6/B8, D2 DECIDED), PG registration surface (B7).**

---

## 10. Phased rollout — Phase 1 (dev), **Phase 1.5 (live seeding, B2)**, Phase 2

**Phase 1 (dev proof):** storage (2 `.sql`, CHECKs, `disputed`, reachable `archived`, **PG registration via the manager.py pre-create_all block**) · **3 tools** + category + 40-agent sweep (B4 rules) · injection block + fence + family ordering + pinned-drag + derived char bound (slug ≤44, entry ≤332, **full-block 2,900 incl. wrapper**) + escape-aware budget + **metadata allowlist companion** · dogfood §11 on the **dev** P-1 project row · gates (packs; Tier-2 mechanical; net-new 0).

**Phase 1.5 — LIVE SEEDING (new, B2; owner: leader/devops; gate: after the next promote ceremony carries the schema):**
1. Create the first-class **llm-supervisor-proxy project row on the LIVE install** (`project_create`; `main_directory=/home/nea/Code/opensource-projects/llm-supervisor-proxy`).
2. Seed the 8 slugs via `pok_upsert` **against prod through the tool lane** (leader-curated; `observed`; fenced write discipline).
3. Verify with a **fresh leader spawn on the live daemon** — the block renders the 8 entries, spine ordered, pinned at top.
**Honest boundary, stated plainly: until Phase 1.5 runs, the walkthrough is TRUE IN DEV ONLY — dev proof is not a claim of done; the live claim exists only after step 3 passes.**

**Phase 2:** derived export (private ensemble repo only); guidelines tier (D3); per-turn refresh evaluation; verify-window tuning; `pok_verify`; critical-notes hygiene (D5); **uniform metadata cap as a deferred phase-2 option (D2 decided allowlist for v1)**.
**NOT built:** FE dashboards; watch/alert; cross-project search; KPI phases.

---

## 11. Migration — the llm-supervisor-proxy operational knowledge (first payload)

**Target:** dev row now (Phase 1); **live row in Phase 1.5** (B2) — Option (a) first-class project row (consequences in `decisions.md` A-19).

**Payload (dogfood; B3 re-slug: the fact-5/6 contrast is ONE atomic family — `deploy-01/02`; slugs design-freeze only AFTER dogfood):**

| # | slug | domain | note |
|---|------|--------|------|
| 1 | `artifact-is-container-image` | artifact | unordered (27c) |
| 2 | `pipeline-01-merge-master-triggers-image-ci` | deploy | family `pipeline` step 1 (42c) |
| 3 | `pipeline-02-verify-image-tag-digest-feature` | deploy | family `pipeline` step 2 (43c) |
| 4 | `pipeline-03-deploy-order-daoduc-backup-first` | deploy | family `pipeline` step 3 — **pinned (curator)** (44c) |
| 5 | `deploy-01-daoduc-backup` | environment | **family `deploy` step 1 — the contrast family (B3)** (23c) |
| 6 | `deploy-02-ensem-dev-primary` | environment | **family `deploy` step 2 — contrast family** (27c) |
| 7 | `healthz-in-cluster-only` | verify | unordered (23c); critical-note POINTER optional |
| 8 | `model-catalogs-19-vs-22-not-drift` | catalog | unordered (33c) |

**Regex re-check (B3) against `^([a-z][a-z0-9]*)-(\d{2})-(.+)$`:** `pipeline-01/02/03` → family `pipeline` ✓; `deploy-01/02` → family `deploy` ✓ (atomic — **a one-sided render of the fact-5/6 contrast is structurally unreachable**); `artifact-is-container-image`, `healthz-in-cluster-only`, `model-catalogs-19-vs-22-not-drift` → no match → **unordered** ✓ (the old `^(.*)` regex mis-parsed `model-catalogs-19-vs-22-not-drift` as family `model-catalogs-19-vs` step `22` — greedy rightmost digit pair — when it must be unordered; the tightened regex no longer matches it). All 8 slugs ≤ 44c; bodies 54–93c raw, no escapable glyphs. Worst block: content 2,764 + wrapper 47 = **2,811 ≤ 2,900**; dogfood pinned tier ≈ 3×182 + 140 + 47 ≈ 733 ≤ 2,900.

---

## 12. Risks

- 🔴 Economics regression if the metadata companion slips; **allowlist inflation** (verbatim large policy values) is the new shape — allowlist review + AC6 fail-on-absent/truncated + AC12 live measure.
- 🔴 Wrong-but-confident fact injected to every agent — bounders: curator-only pinned-supersede; `disputed`; lineage; inline markers; fence. Leader pin review.
- 🔴 Content security — DB-canon; phase-2 export fence private-ensemble-only.
- 🟡 **Live-vs-dev boundary (B2):** dev proof is not live truth; the walkthrough claim on live exists only after Phase 1.5 step 3.
- 🟡 Terminality — cured (curator archive).
- 🟡 Pinned×family drag — bounded rows (6≤8) AND chars (2,811≤2,900 incl. wrapper).
- 🟡 Exclusion×drag (F3) — ruled; survivors drag contiguously.
- 🟡 Escape-aware sizing — post-escape at write (`pok_body_oversize`).
- 🟡 Retrieval reuse — selector config drift is a shared dependency; pinned-bypass + the all-8-retrievable AC3 bound the blast radius.
- 🟡 Grant sweep — B4 rules (watcher exclusion, nest-report LAST, pin test) are the guard set; violating any of them is silent breakage — the pin test is the tripwire.
- 🟡 Oblique-prose injection; dispute paraphrase laundering; near-dupes; wired supersede; once-per-instance staleness; tool growth. 🟢 verify-window tuning; export cadence.

---

## 13. Decisions pending (Emperor / leader)

- **D1** canon A + new surface (flip condition unchanged). **D2 — DECIDED (leader, v3.3): v1 ships the ALLOWLIST variant (§4.8); uniform per-key cap deferred as a phase-2 option.** **D3** guidelines un-park. **D4** second-model corroboration post-D7. **D5** critical-notes hygiene lane. **P-1** dev project row precondition; **Phase 1.5** = live row + live seeding (leader/devops-owned).

---

## 14. Confidence, dissent, corrections, unverified

**Confidence: MEDIUM** (single-source council). Receipt integrity: all anchors re-verified at HEAD this pass; two leader-named paths corrected to their real locations (`tests/unit/metrics/…`, `tests/unit/tools/…`).

**Corrections landed v3/v3.1/v3.2/v3.3:**
- v3: references-injected; KV true grounds; blueprint prompt-doc; memoir census; carrier phrasing; prompt-memory row; DB-skill tier; 54–93c; infra 3 grants + deny.
- v3.1: C1-A + PIN(2)×FAM(3); W5 escaped-body; archived-slug norm; fence wording; memoir scoped census; `:4957`→`:5083`.
- v3.2: F1 Option A (slug 44 / entry 332 / block 2,800) — superseded by v3.3's wrapper-inclusive **2,900**; F3 exclusion ruling; F4/F5/F6; F7 skipped (sealed history); F8 moot.
- **v3.3 (leader disposition):** **B1** — `POK_BLOCK_MAX_CHARS` defined on the FULL rendered block incl. the 47c `_make_context_message` wrapper (`CONTEXT_PREFIX` 17 + title 27 + `CONTEXT_SUFFIX` 3, `context_messages.py:75-76/:181-182`; in-file precedent `:205-230`); 2,811 ≤ **2,900**; pinned 2,185 ≤ 2,900. **B2** — Phase 1.5 live seeding named; dev-only boundary stated. **B3** — `deploy-01/02` atomic contrast family; regex tightened to `^([a-z][a-z0-9]*)-(\d{2})-(.+)$`; all 8 slugs re-checked. **B4** — watcher excluded (empty-allow inherit-all, `_tool_registry.py:326-340`), `nest-report` tail position with HEAD exceptions ari/canary (§7 rule 2), pin test `tests/unit/test_anomaly_report_grant_pins.py` cited. **B5** — invented fusion weights DELETED; retrieval reuses `critical_notes_selector.py`/`critical_notes_selection_orchestrator.py`; pinned bypass threshold; AC3 all-8-retrievable. **B6+B8** — allowlist variant shipped (D2 DECIDED); no new read tool; AC6 fail conditions; AC15 replaced. **B7** — `daemon/manager.py` pre-create_all registration block; AC9 asserts PG materialization; re-pin files named. **Notes:** row-1 quantifier corrected; economics reconciled on two bases (68.4 % of whole JSON; "82 %" withdrawn as computed claim).

**Dissent (unchanged):** C-with-mirror-in-phase-1 (DR); extend-critical-notes (cheapest); budget-first sequencing.

**Unverified (honest):** live metadata measurement (inherited; AC12 re-measures and recomputes both bases); RAG project-name-vs-id keying (not load-bearing); compaction interaction (code shape, not measured).
