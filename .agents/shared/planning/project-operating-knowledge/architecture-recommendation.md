# POK — Project Operating Knowledge — Architecture Recommendation (v3.4)

**Commission:** project-operating-knowledge (POK) — capture, storage, injection, retrieval, and currency of operational/procedural project knowledge.
**Tree:** canon authored @ v0.29.0 7426e0d8a; this amendment on `feature/project-operating-knowledge-ruling` @ 5672f055c (committed canon rode latest; anchors re-verified at this tree). Markdown only; no commits — the giter finalizes.
**Method:** Architect-planned; single council (governor `74d0a09a`, skill `data-flow-design`). **Confidence: MEDIUM** — single-source council (proxy `coding`-alias defect). Corroboration post-D7 (§14).
**Status:** DESIGN ONLY — implementation is NOT authorized. Pending: D1 (implementation go — the split/audience model is DECIDED by the Emperor ruling below), D3–D5, P-1, Phase 1.5. **D2 DECIDED (allowlist, §4.8).**
**Revision note (v3.4 — EMPEROR-RULING FOLD, docs-only, bounded):** the Emperor ruled on the injection model (Discord 2026-11-07): *split confirmed* (POK vs the Related-Project record; a unified-block alternative was offered and DECLINED — Emperor as decider); a NEW **audience dimension** on entries (`operation` | `implementation` | `both`) feeding per-view ACTIVE injection; a NEW **ari-lane delivery seam** (Ari holds the OPERATION slice at dispatch time); and **passive retrieval stays universal** (injection is curated; reading is free). All amendments carry the provenance stamp **"Emperor ruling, 2026-11-07, source: Discord"**. Every closed-form bound holds per-view; nothing else reopens. Prior folds stand (C1/W5, F1/F3, leader disposition B1–B8).
**Revision note (v3.3 — leader disposition):** B1 wrapper-inclusive cap (2,900); B2 Phase 1.5 live seeding; B3 atomic contrast family + tightened regex; B4 sweep integrity; B5 selector reuse; B6+B8 allowlist (D2 DECIDED); B7 PG registration. (Appendix C.)

---

## 0. Executive summary

Operational project knowledge ("deploy daoduc BACKUP first, verify, ensem.dev PRIMARY") is learned in lanes and dies in transcripts compaction erases. **No existing surface can deliver it** — the bar: *no incumbent can deliver **guaranteed injection of ORDERED procedures** to the agents that need it* — mechanism-backed: slug families (tightened regex `^([a-z][a-z0-9]*)-(\d{2})-(.+)$`) render atomically in slug order; a pinned member carries its family to the pinned position; the fact-5/6 contrast is ONE family. **Since v3.4 (Emperor ruling, 2026-11-07, source: Discord): injection is AUDIENCE-SCOPED** — each entry classifies `operation` (default audience {ari, leader, devops}) | `implementation` (default {leader, developer}) | `both` (every agent on the project); each agent's block is selected from ITS visible set — **audience filter FIRST, then the existing admission semantics operate within the visible set; every bound holds per-view** — and **Ari receives the OPERATION slice at dispatch time via a new ari-lane seam** (today the Related-Project block injects only into project-linked working agents — Ari receives no project injection; the ruling changes that). **Reading stays universal: `pok_query` returns ALL entries to ANY agent on the project.** The two-surface SPLIT (POK vs the Related-Project record) stands — a unified-block alternative was considered and DECLINED (decider: the Emperor).

The recommendation: a **new `project_operating_knowledge` table pair in the ensemble PG, keyed by `project_id`, NO FK** — canon **A: DB-canon daemon-side**. Delivery: a per-view deterministic injection block — **`POK_BLOCK_MAX_CHARS = 2,900` on the FULL rendered block INCLUDING the `_make_context_message` wrapper** (47c; content worst 2,764 + 47 = 2,811 ≤ 2,900; pinned tier 2,185) — reaching every agent on a non-default project **for the entries that agent may see**, plus the ari dispatch seam for the operation slice. Retrieval: reuse of the shipped critical-notes selector (pinned rows bypass the threshold); **universal read**. Metadata companion: v1 ALLOWLIST variant (D2 DECIDED) — **unaffected by this ruling**. **The walkthrough is TRUE in dev after Phase 1; live only after Phase 1.5.**

---

## 1. The defect and the acceptance walkthrough

**The Emperor's problem (verbatim intent):** the system has critical notes, metadata, project history — loaded when a leader job starts with the right context. The problem: how can the system ACTIVELY inject context or let an agent PASSIVELY load project-related experience, so the next time an agent works the llm-supervisor-proxy project it already knows the operational truth learned in a prior lane — and that truth must be UPDATABLE over time and known by the leader AND every other agent on the project. **(v3.4 refinement, Emperor ruling 2026-11-07, source: Discord: "we should split [operating knowledge vs project record]. Ari you need know operation one (to control the leader, aegis,… teams), also agent like devops need know. The dev team actually need know the implementation major, and operation is also need (not all the case). It's for active injection — if agent want read them they can read any.")**

**Acceptance test:** the next agent on llm-supervisor-proxy must know, or retrieve in ONE step: (1) deployable artifact = container image, not a source build; (2) merge to master triggers the docker CI build — nothing ships without it; (3) after build verify the new image (tag + digest + feature actually present); (4) deploy order: daoduc (BACKUP) FIRST → verify → then ensem.dev (PRIMARY); (5) daoduc = Helm release v28 + kubectl patch/rollout vs ensem.dev = plain kubectl apply, no helm release; (6) /healthz NOT publicly exposed — verify in-cluster or false negative; (7) model catalogs differ 19 vs 22 and that is NOT deploy drift. *(The Emperor's enumerated examples are a subset of this 7-fact walkthrough — the walkthrough is the binding superset.)*

Today that list lives only in a transcript that compaction will erase. **That is the defect.**

---

## 2. Evidence layer map — load-bearing vs dead weight for THIS requirement

Anchors re-verified at 5672f055c (wrapper `:75-76`, escape `:699`, swallow `:5083`, selector files, pre-create_all block — all hold). All DB-stored data is NOT git-versioned except project-skill files.

| # | Layer | Mechanics (receipts) | Verdict for POK |
|---|-------|------------------------------|------------------|
| 1 | **Critical notes** | Injected first-turn for **all agents whose instance carries a NON-DEFAULT `project_id`** (system default renders the scope guide); pinned + tail tiers, `section_char_cap` 12000 (`context_messages.py:822-1013`); references ARE rendered bounded 500c (`:1030-1040`; v2's contrary claim retracted); writers leader + PM; fields ≤200/≤500; `_MAX_ENTRIES = 200` (`critical_notes.py:70`). | **Load-bearing for traps/conventions. Rejected as carrier** (§6.2). |
| 2 | **Infra assets** | `daemon/repositories/infra/models.py:107/:191/:197/:205/:210/:280/:350`; 9 tools (`_tool_registry.py:1589-1597`); allow-granted to exactly 3 agents (devops/wanderer/worker); critic denies. | **Rejected** (§6.9). |
| 3 | **Project metadata** | Uncapped ride into Related-Project. **Two bases (reconciled):** metadata dict 69 keys / 279,810 B; whole project JSON 408,809 B → **68.4 %**; the "82 %" head-share withdrawn as a computed claim; AC12 re-measures. | **Load-bearing keys in a dump.** ALLOWLIST companion (§4.8). Not a carrier. |
| 4 | **Project history** | Append-only timeline; last 10 injected. | **Partial.** |
| 5 | **KB/RAG** | `explore()` = explorer child, 300s; never injected. | **Mismatched.** |
| 6 | **Memoir** | AGENT-scoped (4 quoted-`ari` occurrences / 3 files in `daemon/repositories/memoir/`); no project column; zones; unwired `superseded_by`. | **NOT a carrier — the pattern source.** |
| 7 | **Board** | Human-facing, ari-only, no injection. | **Not a carrier.** |
| 8 | **Blueprint** | Async blueprinter-only writer; turn-1-only query gate; uncapped verbatim; genre ban = prompt-doc convention (build-blueprint.md:25/:33). | **Seam-shape donor.** Rejected (§6.3). |
| 9 | **Project-skill-loader v2 + DB tier** | Files canon; PR-only; both tiers gated `skill_injection=true` (11/40), max-2 relevance-gated. | **Best substrate; insufficient.** |
| 10 | **Shared Meta KV** | Tree-scoped `context_key` (`shared_meta_kv_tools.py:4/:37/:78-79/:113-122`); 4096-char row cap (`repository.py:180/:210/:240-242`); cap AFTER escape (`context_messages.py:1338-1425`, `:1403`, `:1395-1403`); 28/40 allow-grants (29 mentions incl. critic deny). | **Rejected.** Donates stable-id + escape fence + cap-after-escape. |
| 11 | **Parked guidelines** (wi-d2abe4f7) | Dev-curated immutable reference. | **Fold** as trust tier (§8-iv). |
| 12 | **`.agents/shared/`** | 49,999,702 B / 2005 files; no daemon CODE injection reader (`routers/blueprints.py:583` prompt seed only). | **Dead weight as a channel.** |
| 13 | **Prompt-memory family** | MEMORY-class rows; filename index (`loader.py:780-807/:1490`); `access_memory` (`daemon/tools/access_memory.py:174`); `create_memory_version` (`prompt_version_service.py:1030`). | **Non-carrier.** |
| 14 | **Snapshots** | Checkpoint warm-start copies. | **Non-carrier.** |
| 15 | **Source configs** | Channel config + credentials. | **Non-carrier.** |
| 16 | **Injection seam economics** | Orchestrator `context_messages.py:2022`; landing `instance_messaging.py:4825-4830`; outer swallow `:5083` (builders never raise); turn-1 `:2230-2335`, refresh `:2179/:2287/:2328`; wrapper `:75-76`, title `:191` (committed canon cites `:181-182` — same `_make_context_message` site family), precedent `:205-230`; no global budget; hoisted blocks never compact. | **The constraint surface.** |

---

## 3. The canon decision — **A: DB-canon daemon-side** (unchanged)

One authoritative surface: `project_operating_knowledge` in the ensemble PG, keyed by `project_id`. Derived export optional phase 2. **The reuse bar (§6):** *no incumbent can deliver guaranteed injection of ORDERED PROCEDURES to the agents that need them* — mechanism-backed (§4.5), bounded per-view (§4.5.1), wrapper-inclusive.

---

## 4. The recommended design (concrete)

### 4.1 Storage layer

```
project_operating_knowledge
  id TEXT PK (uuid4) · project_id TEXT NOT NULL (NO FK) · slug TEXT NOT NULL  -- ≤ POK_SLUG_MAX_CHARS (44)
  domain TEXT NOT NULL DEFAULT 'other'   -- CHECK: artifact|deploy|verify|environment|catalog|other
  audience TEXT NOT NULL DEFAULT 'operation'  -- CHECK: operation|implementation|both  [Emperor ruling, 2026-11-07, source: Discord]
  audience_agents TEXT NULL              -- curator-set narrow override; REPLACES the default audience set when present [same ruling]
  body TEXT NOT NULL                     -- escaped-body budget ≤ 240 (§4.2, W5)
  provenance TEXT NOT NULL DEFAULT 'observed'  -- CHECK: declared|observed
  status TEXT NOT NULL DEFAULT 'active'  -- CHECK: active|superseded|archived  (archived REACHABLE via pok_curate)
  disputed BOOLEAN NOT NULL DEFAULT FALSE
  superseded_by_id TEXT (plain TEXT, WIRED) · content_sha256 TEXT NOT NULL
  created_by_agent TEXT NOT NULL · created/updated/last_verified TIMESTAMPTZ
  verify_count INTEGER · access_count INTEGER · last_accessed_at · pinned BOOLEAN DEFAULT FALSE

UNIQUE PARTIAL INDEX uq_pok_active_slug (project_id, slug) WHERE status = 'active'
project_operating_knowledge_embeddings (entry_id, embedding)  -- ranking-only companion
```

Two additive `.sql` files (house rule), CHECK-closed vocabularies (incl. the new `audience` CHECK), no FK → rollback-inert. **PG registration:** models imported in `daemon/manager.py`'s pre-create_all block (`# register before create_all` imports; create_all = PG source-of-truth). Genre split unchanged.

### 4.2 Write path — the crux (≤2 calls) + scope predicate + fence + escape-aware budget

`pok_upsert(slug, body, domain='other', provenance='observed', audience='operation')` — **the writer states the audience classification at capture time** [Emperor ruling, 2026-11-07, source: Discord]; default `operation` (the channel's genre).

- **Scope predicate:** `project_id != SYSTEM_DEFAULT_PROJECT_ID` (`project_normalizer.py:6-45`; `instance_lifecycle.py:1895`; `constants.py:596-597`; `api.py:2656`). Default → block suppressed; typed refusals.
- **Writers:** all agents on a non-default project. Attribution = factory-captured `agent_id`; **attribution ≠ authorization**.
- **Slug bound:** `[a-z0-9-]` ≤44 (`pok_slug_oversize`). **Write-time fence:** `pok_body_structural_token` (newline / leading `#` at body start / `[SYSTEM CONTEXT:` / ```). **Escape-aware budget:** `len(escape_for_context_block(body)) ≤ 240` (`pok_body_oversize`).
- Transaction: exact-dup no-op echo; normalized-hash dispute-laundering guard; supersede txn; disputed flip; **archived flip (forward-only)**.
- Admission: refuse-never-evict at POK_ACTIVE_CAP_PER_PROJECT (256) active entries (refusal names oldest-least-verified + archive cure); family admission >3 active → `pok_family_full`.
- **Grant model:** `pok` NOT privileged; 40-agent sweep per §7 (B4 rules — watcher excluded, `nest-report` LAST).

### 4.3 Update / supersede / curate / archive — authorization model

Unchanged from v3.3 (authorization matrix; `pok_curate(slug, pinned=None, disputed=None, archived=None)`; pin cap 2 **PER PROJECT — not per audience** [Emperor-ruling consistency note: pins are project-level facts; a pinned entry renders in every audience view that can SEE the entry]; family max 3; recording surface = typed echo + `ens_system_log`; `pok_verify` phase 2; suspected-wrong protocol; decay). **`pok_curate` gains `audience=None` / `audience_agents=None` parameters** (curator-only reclassification/narrowing) [Emperor ruling, 2026-11-07, source: Discord].

### 4.4 Trust / provenance (unchanged)

declared/observed inline; `[DISPUTED]`; ranking = decay × observed × access; single-active-version-per-slug; lineage always in `pok_query`.

### 4.5 Injection layer — deterministic, ordered, fenced, wrapper-inclusive bounded — **per-view** (Emperor ruling, 2026-11-07, source: Discord)

- Block `## Project Operating Knowledge`; builder `build_pok_context_message`; ~7 touch-points; placement by CODE SITE.
- **Membership/admission (per-view):** pinned tier first (≤2 pins; pinned member carries its whole family, adjacent, slug order, at the pinned position; doubly-pinned family counted once on rows AND chars) → ranked actives (disputed excluded) → **F5 rider: stop where rendered chars would exceed `POK_BLOCK_MAX_CHARS` OR entries would exceed 8; tail counts remainder.** Families atomic.
- **EXCLUSION × DRAG (F3, extended):** excluded rows NEVER render, in any tier — **and an audience-INVISIBLE member is an excluded row FOR THAT VIEW**: survivors render contiguously in slug order; pinned-member exclusion cancels the drag; zero visible survivors → the family does not render in that view.
- **ORDERING MECHANISM:** `<family>-<NN>-<slug>`; `^([a-z][a-z0-9]*)-(\d{2})-(.+)$`; adjacency in slug order; never split (within a view).
- **BOUNDED ARITHMETIC (wrapper-inclusive, DERIVED — holds PER-VIEW):** entry ≤332 (escaped body 240 + overhead 92); content worst = header 40 + 8×333 + tail 60 = 2,764; wrapper 47 (`CONTEXT_PREFIX` 17 + title 27 + `CONTEXT_SUFFIX` 3, `:75-76`, precedent `:205-230`); **full-block worst 2,811 ≤ 2,900; pinned tier 2,185 ≤ 2,900.** **The audience filter PRECEDES admission — each agent's view is a subset of the project set, so the worst case per view cannot exceed the project-wide worst case; every closed-form bound holds per-view without re-derivation.**
- **CONTENT FENCE:** fixed list-item template + `escape_for_context_block` (`:699`) + newline flattening; no structural token survives. Fenced-verbatim.
- **Once-per-instance flag:** writes only on successful render (store-down retries next turn) — per agent instance, per view.

#### 4.5.1 Audience dimension — per-view ACTIVE injection (Emperor ruling, 2026-11-07, source: Discord)

- **Classification field:** `audience ∈ {operation, implementation, both}` on every entry (schema §4.1), stated at write, curator-adjustable.
- **Default audiences for ACTIVE injection:**
  - `operation` → **{ari, leader, devops}** — *leader INCLUDED (it runs the ops lanes; today's deploy ran through it)*.
  - `implementation` → **{leader, developer}**.
  - `both` (universal) → **every agent on the project** (preserves the original guarantee for genuinely universal facts).
  - A curator-set `audience_agents` list REPLACES the default set where a narrower delivery is wanted.
- **Per-view budget:** the per-agent injected block budget (8 entries / 2,900c full-render) is a **PER-AUDIENCE effective slice** — each agent's block is selected from ITS visible entry set; **the budget never sums across audiences.** Admission rule, precisely: **audience filter FIRST** (visible set = entries whose effective audience includes the agent's `agent_id`, minus status/dispute exclusions), **then the existing admission semantics (pinned ∪ family-ordered, atomic families, pinned drag, F5 rider) operate WITHIN the visible set.** Agents map to audiences by **factory-captured `agent_id`** (the proven in-body pattern).
- **Considered and DECLINED — unified block (one undifferentiated injection to everyone):** offered during the ruling discussion; the Emperor declined it — the split (POK vs the Related-Project record) AND the audience dimension both stand. Decider: the Emperor (2026-11-07, Discord).
- **ARI-LANE DELIVERY SEAM (new touch-point):** structural fact — today the Related-Project block injects only into a project-linked working agent's context; **Ari receives NO project injection.** The ruling requires Ari to hold the OPERATION slice at dispatch time. Seam (design level): **where Ari's dispatch/work context is assembled for a target project, the builder emits the operation-audience view block for that project** — gates: target `project_id != SYSTEM_DEFAULT_PROJECT_ID`; audience = operation (ari ∈ the operation default set by the ruling); the SAME block budget (8 / 2,900 full-render) and content fence and family semantics; assembled **per dispatch** (fresh read at dispatch time — Ari is long-lived/reused, so per-dispatch assembly is the freshness mechanism; `pok_query` remains the mid-lane refresh); stable id `pok:{project_id}` within Ari's own instance context namespace. This is additive to the ~7 working-agent touch-points (an eighth touch-point class: the ari dispatch-context assembly site).

### 4.6 Retrieval layer — one step, project-scoped, selector-reuse — **and UNIVERSAL READ (invariant)**

`pok_query(query='', domain=None)` — reuse of `daemon/services/critical_notes_selector.py` + `critical_notes_selection_orchestrator.py` (configurable fusion weights/threshold; no POK-invented weights); **pinned rows bypass the threshold**; family chains in slug order; exclusions per F3 (status-based); typed errors; lag surfaced.

- **UNIVERSAL-READ INVARIANT (Emperor ruling, 2026-11-07, source: Discord):** `pok_query` returns **ALL entries of the project to ANY agent on it, regardless of audience tags** — *injection is curated; reading is free.* The audience field affects ACTIVE injection ONLY; it never filters retrieval. ("It's for active injection — if agent want read them they can read any.")

### 4.7 Sizing constants (unchanged by the ER-1 audience ruling; active cap raised to 256 by Emperor directive 2026-11-10 — per-view)

| Constant | Value | Role |
|---|---|---|
| `POK_BODY_MAX_CHARS` | 240 | escaped body (`pok_body_oversize`) |
| `POK_SLUG_MAX_CHARS` | 44 | slug bound |
| `POK_ENTRY_RENDER_MAX_CHARS` | 332 | derived entry ceiling |
| `POK_ACTIVE_CAP_PER_PROJECT` | 256 | refuse-never-evict (curable) — **per PROJECT (not per audience)** |
| `POK_PIN_CAP_PER_PROJECT` | 2 | **pin admission per PROJECT — pins render in every audience view that can see the entry** |
| `POK_FAMILY_MAX` | 3 | family admission bound |
| `POK_BLOCK_MAX_CHARS` | 2,900 | FULL rendered block incl. wrapper — **PER-VIEW** (audience filter precedes admission) |
| `POK_BLOCK_MAX_ENTRIES` | 8 | per-view entry cap |
| retrieval threshold | selector-configured | non-pinned only |
| `POK_VERIFY_WINDOW_DAYS` | 30 | decay window |

### 4.8 REQUIRED companion — Related Project metadata ALLOWLIST (D2 DECIDED) — **UNAFFECTED by the ruling**

Named allowlist renders verbatim (`ari_plane_tracking_policy`, `plane_workspace_discipline`, `deferred_key_rotation{,_f7_addition}`, `upgrade_policy`, `upgrade_workflow_preference`, `blueprint_active`, `dependency_self_install_philosophy`, `self_provisioning_directive`, `opendesign_decisions`, `plugin_*` policy keys, `staged_release_v0290_ceremony_notes`); non-allowlisted 2000c/key + 16384c block + omitted-count tail; no new read tool. **The audience ruling touches POK only — the Related-Project record block, its allowlist, and its caps are unchanged.** Economics bases (279,810 B / 408,809 B / 68.4 %) and AC12 stand.

### 4.9 Degradation contract (unchanged; per-view)

Never raise (the `:5083` swallow); None+WARNING on store-down (once-flag unwritten → retry); malformed-row skip; oversize backstop (unreachable per arithmetic, per-view); status/dispute/audience-invisible rows excluded from that view's membership; default project → no block; typed query errors; lag surfaced; fence + escape-aware budget at write; block cap measured on the FULL rendered block incl. wrapper.

---

## 5. Design questions A–H — where answered

| Q | § | Q | § |
|---|---|---|---|
| A Ownership | 3, 4.1, 6 | E Update/staleness | 4.3 |
| B Active injection | 4.5 + **4.5.1 (audience; ari seam)** | F Trust | 4.4 |
| C Passive retrieval | 4.6 (**universal read**) | G Sizing | 4.7 |
| D Write path | 4.2 | H Migration | 11 + 10 |

---

## 6. Rejected alternatives — the SPECIFIC structural reason each loses

**Bar:** *no incumbent can deliver guaranteed injection of ORDERED PROCEDURES to the agents that need them* (mechanism §4.5; audience scoping §4.5.1).

1. **File-canon** — friction/probability/anchor/security. 2. **Critical-notes evolution** — field caps + writer + selection. 3. **Blueprint** — async writer/turn-1 gate/no supersede. 4. **Project-skill files / DB tier** — opt-in probability. 5. **Metadata-JSONB** — uncapped, no lineage. 6. **Memoir extension** — wrong scope. 7. **Board** — no injection. 8. **Shared Meta KV** — tree-scope/4096/friction (true grounds; donor of three patterns). 9. **`infra_assets`** — no seam/3 grants/genre. 10. **Prompt-memory** — persona scope. **C1-(C) family-scoped pins** — contradicts member-pin semantics. **Unified single-audience block — considered and DECLINED by the Emperor (2026-11-07, Discord; §4.5.1).**

The enumeration closes (sixteen map rows + the two injection-model alternatives declined by decision).

---

## 7. Reuse verdict — carries, NEW, registration, grant-sweep integrity

Carries/borrowed unchanged (embeddings infra; **selector reuse**; memoir idioms; blueprint patterns; two-layer gate; agent-secrets posture; KV idiom+fence+sizing; house generator). NEW pieces: table pair (+`disputed`, reachable `archived`, **+`audience`/`audience_agents`**); `pok` category + 3 tools v1 (+ audience params on upsert/curate); `build_pok_context_message` + the working-agent touch-points **+ the ari dispatch-context seam (the 8th touch-point class)**; metadata allowlist renderer. Registration: `TOOL_REQUIRED_AGENTS` none; `VOCABULARY_VERSION` 6→7; artifact 60→61; ordinal-free. **B7 re-pins:** `tests/unit/metrics/test_metrics_vocabulary_amendment.py`, `tests/unit/tools/test_tool_category_artifact.py`. **B4 sweep rules:** watcher excluded (empty allow = inherit-all, `_tool_registry.py:326-340`); `"pok"` before `"nest-report"` (LAST) in the 39 non-empty allows; pin test `tests/unit/test_anomaly_report_grant_pins.py`.

---

## 8. The four seam questions (unchanged)

(i) NEW block; (ii) per-block cap sufficient, allowlist companion; (iii) once-per-instance v1 + `pok_query` freshness (per agent instance — **the ari seam assembles per dispatch**); (iv) guidelines phase-2 tier.

---

## 9. Leader leanings — adjudication

1–8 stand (v2–v3.3). **v3.4: the Emperor's ruling supersedes the "every project-linked agent receives every entry" reading — delivery is now audience-scoped active injection + universal passive read + the ari seam.** The original guarantee survives for `both`-classified entries.

---

## 10. Phased rollout — Phase 1 (dev), Phase 1.5 (live seeding), Phase 2

**Phase 1 (dev proof):** storage (2 `.sql` with the `audience` CHECK; PG registration) · 3 tools (+ audience params) + category + 40-agent sweep (B4) · injection block **with the audience filter + per-view admission + the ari dispatch seam** + fence + family ordering + wrapper-inclusive bound + escape-aware budget + allowlist companion · dogfood §11 on the dev P-1 row · gates (packs; Tier-2 mechanical; net-new 0).
**Phase 1.5 — LIVE SEEDING (owner leader/devops; gate: next promote carries the schema):** live project row; live 8-slug seeding via the tool lane (with audience classifications); fresh live leader-spawn AND **ari-dispatch** verification. **Until Phase 1.5 step 3 passes, the walkthrough is TRUE IN DEV ONLY.**
**Phase 2:** derived export; guidelines tier (D3); per-turn refresh evaluation; verify-window tuning; `pok_verify`; notes hygiene (D5); uniform metadata cap option.
**NOT built:** FE dashboards; watch/alert; cross-project search; KPI phases.

---

## 11. Migration — the llm-supervisor-proxy payload, WITH audience classification (Emperor ruling, 2026-11-07, source: Discord)

**Target:** dev row now; live row in Phase 1.5. **Payload (8 slugs, classified honestly — deploy facts are operation; the CI-trigger and the healthz property are genuinely both):**

| # | slug | domain | **audience** | note |
|---|------|--------|--------------|------|
| 1 | `artifact-is-container-image` | artifact | **both** | artifact truth — universal for anyone touching the project |
| 2 | `pipeline-01-merge-master-triggers-image-ci` | deploy | **both** | devs must merge-to-master knowingly; ops must know nothing ships without CI |
| 3 | `pipeline-02-verify-image-tag-digest-feature` | deploy | **operation** | post-build verification ritual |
| 4 | `pipeline-03-deploy-order-daoduc-backup-first` | deploy | **operation** | deploy order — pinned (curator) |
| 5 | `deploy-01-daoduc-backup` | environment | **operation** | Helm v28 mechanism |
| 6 | `deploy-02-ensem-dev-primary` | environment | **operation** | plain-kubectl mechanism |
| 7 | `healthz-in-cluster-only` | verify | **both** | environment property — false-negative hazard for anyone probing, devs included |
| 8 | `model-catalogs-19-vs-22-not-drift` | catalog | **operation** | deploy-drift triage |

**Resulting views:** ari/leader/devops (operation ∪ both) see all 8; **developer (implementation ∪ both) sees 3** — artifact, CI-trigger, healthz — an honest match to the ruling ("the dev team need know the implementation major, and operation is also need — not all the case"); this payload carries no implementation-classified facts yet (future implementation facts slot into the same field). Regex re-check stands (pipeline/deploy families parse; the rest unordered). All slugs ≤44c; bodies 54–93c; per-view worst ≤2,811 ≤2,900.

---

## 12. Risks

- 🔴 Metadata companion slip; allowlist inflation (unchanged).
- 🔴 Wrong-but-confident fact injected — bounders unchanged (curator-only pinned-supersede; `disputed`; lineage; inline markers; fence).
- 🔴 Content security — DB-canon; export fence.
- 🟡 **Audience misclassification** (an `operation` fact marked `both` over-delivers; marked `operation` under-delivers to devs) — mitigated: universal read means nothing is hidden (a dev can `pok_query` anything); classification is curator-adjustable; the dogfood classifications are recorded above.
- 🟡 **Ari-seam staleness** — per-dispatch assembly reads the current store (fresh by construction); mid-lane `pok_query`.
- 🟡 Live-vs-dev boundary (Phase 1.5); terminality (cured); drag bounds (per-view, hold); F3+audience exclusions (ruled); escape-aware sizing; selector reuse drift; sweep integrity (B4); oblique prose; paraphrase laundering; near-dupes; wired supersede; tool growth. 🟢 verify-window; export cadence.

---

## 13. Decisions — status

- **D1 — AMENDED (Emperor ruling, 2026-11-07, source: Discord): the split and the audience model are DECIDED** (this fold); **D1's remaining open content is the implementation go.** **D2 — DECIDED (allowlist).** **D3** guidelines un-park (pending). **D4** corroboration post-D7 (pending). **D5** notes hygiene (pending). **P-1** dev row; **Phase 1.5** live seeding (leader/devops).

---

## 14. Confidence, dissent, corrections, unverified

**Confidence: MEDIUM** (single-source council); anchors re-verified at 5672f055c.

**Corrections landed v3–v3.4:** v3 set (references-injected; KV true grounds; blueprint prompt-doc; memoir census; carrier phrasing; prompt-memory row; DB-skill tier; 54–93c; infra grants); v3.1 (C1-A; W5; archived norm; fence wording; memoir scoped census; `:5083`); v3.2 (F1 Option A; F3; F4–F8); v3.3 (B1 wrapper-inclusive 2,900; B2 Phase 1.5; B3 atomic contrast + tightened regex; B4 sweep rules; B5 selector reuse; B6/B8 allowlist, D2 decided; B7 PG registration; row-1 quantifier; economics two-bases). **v3.4 (Emperor ruling, 2026-11-07, source: Discord): split CONFIRMED (unified block considered-and-declined, Emperor as decider); audience field + default audiences + per-view admission (filter precedes admission; bounds hold per-view; pin cap stays per project); ari dispatch seam (8th touch-point class; per-dispatch freshness; same budget/fence); universal-read invariant; dogfood classification (3 `both`, 5 `operation`, 0 `implementation` — honest for this payload).**

**Dissent (unchanged):** C-with-mirror-in-phase-1 (DR); extend-critical-notes; budget-first sequencing.

**Unverified (honest):** live metadata measurement (AC12); RAG keying (not load-bearing); compaction interaction (code shape).

**Consistency notes (v3.4):** (1) the "spine 01→02→03" AC18 assertion is now **per-view** — it holds fully in the operation view (ari/leader/devops); in a developer's view the pipeline family renders only its visible member(s) (audience-invisible = excluded-for-view, F3 semantics); no invariant breaks. (2) Pin cap remains per project; a pinned `operation` entry never consumes developer-view budget (invisible there). (3) The metadata companion and the Related-Project record are untouched by the ruling.
