# POK — Project Operating Knowledge — Decision Record (decisions.md)

**Companion artifact to:** `architecture-recommendation.md` **v3.4** in this directory — this record derives from it and cites it.
**Tree:** canon authored @ 7426e0d8a; this amendment on `feature/project-operating-knowledge-ruling` @ 5672f055c. Fenced dev; markdown-only; the giter finalizes.
**Status: DESIGN ONLY — implementation is NOT authorized.** Pending: **D1 (implementation go — split/audience model DECIDED by ER-1)**, D3–D5 + P-1 + Phase 1.5; **D2 DECIDED** (allowlist, A-36).
**Revision note:** round-1 (v2); round-2 (v3); C1+W5 (v3.1); F1+F3 (v3.2); leader disposition B1–B8 (v3.3); **Emperor-ruling fold (v3.4 — ER-1)**. See `discharge-round-1.md` / `discharge-round-2.md` + appendices B/C/D.

**ID conventions:** `A-n` = decided-so-far. `ER-n` = EMPEROR RULING (decided by the Emperor; highest authority in this record). `D1–D5` = decision status. `P-1` = implementation precondition. `C-n` = boundaries. "D7" = the standing proxy `coding`-alias DEFECT id.

---

## (A) DECIDED-so-far

### Canon & storage

**A-1 — Canon A: DB-canon, daemon-side, new surface.** (§3; §9.) Reuse bar: *no incumbent can deliver GUARANTEED INJECTION OF ORDERED PROCEDURES to the agents that need them* — mechanism-backed (A-26), bounded under pinning (A-33), wrapper-inclusive (A-35-B1), **and audience-scoped per ER-1 (per-view bounds hold — filter precedes admission)**.

**A-2 — Storage shape.** Two tables (§4.1): `project_operating_knowledge` (id; `project_id` NO FK; slug ≤44; CHECK `domain`/`provenance`; CHECK `status ∈ active|superseded|archived`; **CHECK `audience ∈ operation|implementation|both` + `audience_agents` curator-override list — ER-1**; `disputed`; `superseded_by_id` WIRED; `content_sha256`; factory-captured author; timestamps; counts; `pinned`) + embeddings companion. `UNIQUE PARTIAL INDEX uq_pok_active_slug (project_id, slug) WHERE status='active'`. Two additive `.sql`; CHECKs on the SQLModel table + mirrored in the audit files.

**A-3 — Genre split (definitive), no genre migrates.** (§4.1.) **The split CONFIRMED by ER-1: POK (operating knowledge) vs the Related-Project record — two surfaces, deliberately.**

### Write path

**A-4 — ≤2-call capture + guards.** `pok_upsert(slug, body, domain, provenance, audience='operation')` — **the writer states the audience at capture; default `operation`** (ER-1). Slug ≤44 (`pok_slug_oversize`); exact-dup no-op echo; normalized-hash dispute guard (`pok_disputed_content`); supersede txn; disputed flip; archived flip (forward-only). Fence: `pok_body_structural_token` (newline / leading `#` at body start / `[SYSTEM CONTEXT:` / ```). Escape budget: `len(escape_for_context_block(body)) ≤ 240` (`pok_body_oversize`). (§4.2.)

**A-5 — Writers, scope predicate, attribution.** Non-default project only (`project_id != SYSTEM_DEFAULT_PROJECT_ID`; `project_normalizer.py:6-45`; `instance_lifecycle.py:1895`; `constants.py:596-597`; `api.py:2656`; mirrors `context_messages.py:2248/:2272`). Default → block suppressed; typed refusals. Attribution = factory-captured `agent_id` — **attribution ≠ authorization**; **audience mapping ALSO by factory-captured `agent_id` (ER-1 — the proven in-body pattern).**

**A-6 — Admission + sizing.** Active cap 64 refuse-never-evict (curable via archive; **per PROJECT, not per audience**); family admission >3 → `pok_family_full`. Body 240 **escaped**; slug ≤44; pin 2; family-max 3 (2×3 = 6 ≤ 8). **Block budget 8/2,900 is PER-VIEW (ER-1).**

### Update / curate / currency

**A-7 — Append-with-supersede ONLY; the txn IS the design.**

**A-8 — Currency.** `last_verified_at` at write; same-content re-upsert refreshes. `pok_verify` DEFERRED to phase 2; v1 = 3 tools. Decay `exp(-idle/30d)` + `[stale Nd]`. Embedding-lag: BM25-only + `embedding_lag: true`.

**A-9 — Authorization matrix + archive + recording surface.**

| Operation | Non-pinned | Pinned |
|---|---|---|
| `pok_upsert` (supersede) | any project-linked agent | leader/PM only → `pok_pinned_requires_curator` |
| `pok_curate(pinned / disputed / archived / **audience / audience_agents**)` | leader/PM only | leader/PM only |
| `pok_verify` | phase 2 | phase 2 |

`pok_curate` — pin cap 2 **PER PROJECT (not per audience; a pinned entry renders in every audience view that can see it — ER-1 consistency note)**; dispute suppresses injection, stays readable; archive frees the slot, row readable; **audience reclassification/narrowing curator-only (ER-1)**. Recording: typed echo + `ens_system_log` WARNING. Suspected-wrong protocol unchanged.

### Trust / provenance

**A-10 — declared|observed inline; `[DISPUTED]`; ranking = decay × observed × access; lineage always rendered; pinned rows BYPASS the retrieval threshold.**

### Injection layer

**A-11 — Block shape + ordering + fence + pinned-drag + wrapper-inclusive cap — **PER-VIEW** (ER-1).** Block `## Project Operating Knowledge`; builder + the working-agent touch-points **+ the ari dispatch-context seam (the 8th touch-point class)**. Membership: **audience filter FIRST** (visible set per the agent's `agent_id`), then pinned tier (≤2 pins, family drag, doubly-pinned counted once on rows AND chars) → ranked actives → F5 rider (stop at chars OR entries; tail counts). **B3 contrast family atomic; F3: excluded rows never render — audience-INVISIBLE = excluded-for-that-view (survivors contiguous; pinned-member exclusion cancels the drag; zero visible survivors → no render in that view).** B1 arithmetic holds per-view: entry ≤332; content worst 2,764; wrapper 47 → **2,811 ≤ 2,900**; pinned 2,185 ≤ 2,900 (the visible set is a subset — the project-wide worst bounds every view). Once-flag on success only. Stable id `pok:{project_id}` (instance-scoped namespace); opt-out `pok_inactive`.

### Retrieval layer

**A-12 — `pok_query` — selector reuse + pinned bypass + the UNIVERSAL-READ INVARIANT (ER-1).** Reuse `critical_notes_selector.py` + `critical_notes_selection_orchestrator.py` (configurable weights/threshold; the invented 0.4/0.6/0.25 is DELETED). **UNIVERSAL READ: `pok_query` returns ALL entries of the project to ANY agent on it, regardless of audience tags — injection is curated; reading is free.** Family chains in slug order; status-based exclusions per F3 (audience NEVER filters retrieval); archived on explicit request; server-side scope; typed errors; lag surfaced. AC3: ALL 8 slugs retrievable by their own natural query.

### Sizing constants

**A-13 —** body 240 (escaped) · slug ≤44 · entry ≤332 · active 64 (per project) · pin 2 (per project) · family-max 3 · **block 2,900 full-render PER-VIEW** / 8 · threshold = selector-configured, non-pinned only · window 30d.

### REQUIRED same-lane companion

**A-14 — Metadata ALLOWLIST (D2 DECIDED) — UNAFFECTED by ER-1.** Named allowlist verbatim (`ari_plane_tracking_policy` … `staged_release_v0290_ceremony_notes`); non-allowlisted 2000c/key + 16384c block + omitted-count tail; no new read tool. Economics two-bases (279,810 B / 408,809 B / 68.4 %); AC12 re-measures. **ER-1 touches POK only.**

### Rejected carriers

**A-15 —** (§6; ten stores + non-carriers; sixteen map rows) — file-canon; critical-notes; blueprint; skill-loader tiers; metadata-JSONB; memoir; board; shared_meta_kv (true grounds); infra_assets; prompt-memory. Plus two DECISION-declined injection-model alternatives: C1-(C) family-scoped pins; **unified single-audience block (declined by the Emperor — ER-1)**.

### Reuse verdict

**A-16 —** Carries/borrowed per §7 (selector reuse included). NEW: table pair (+audience fields); `pok` category + 3 tools v1 (+ audience params); builder + working-agent touch-points **+ ari seam**; allowlist renderer. Registration: `TOOL_REQUIRED_AGENTS` none; `VOCABULARY_VERSION` 6→7; artifact 60→61; ordinal-free. B7 PG registration (manager.py pre-create_all) + re-pins. **B4 sweep rules** (watcher excluded; `nest-report` LAST; pin test tripwire; 40 registered / 39 non-empty allows).

### Seam rulings

**A-17 —** (i) NEW block; (ii) per-block cap sufficient, allowlist companion; (iii) once-per-instance v1 + query freshness — **the ari seam assembles per dispatch (fresh read)**; (iv) guidelines phase-2 tier.

### Leader adjudications

**A-18 —** v2 stand; v3 ordering/fence/archive; v3.1 drag bound; v3.2 char keystone + F3; v3.3 leader disposition; **v3.4 Emperor ruling folded (ER-1) — delivery audience-scoped; ari seam; universal read.**

### Migration

**A-19 —** W-3 Option (a) project row (dev now; live in Phase 1.5). Payload classified per ER-1: **both** = `artifact-is-container-image`, `pipeline-01-merge-master-triggers-image-ci`, `healthz-in-cluster-only`; **operation** = `pipeline-02`, `pipeline-03` (pinned), `deploy-01`, `deploy-02`, `model-catalogs-19-vs-22-not-drift`; **implementation** = none in this payload (honest — it is operational knowledge). Views: ari/leader/devops see 8; developer sees 3. Bodies 54–93c. Dev proof ≠ live truth (Phase 1.5).

### Deliberately NOT built

**A-20 —** FE dashboards; watch/alert; cross-project search; KPI phases.

**A-21–A-25 (retired in v3):** round-1 ruling IDs — content folded into A-5 (writers/scope), A-9 (authorization/archive), A-16 (carries/registration), and arch §4.9 (degradation); originals preserved in `discharge-round-1.md`. Gap kept for citation stability — round-1 register references (A-21/A-22/A-23/A-24/A-25) resolve here.

### Round-2 forced choices

**A-26 — Ordering: slug families + atomic adjacent rendering** (regex `^([a-z][a-z0-9]*)-(\d{2})-(.+)$`, tightened per B3). **A-27 — Archive verb in v1.** **A-28 — `pok_query` pinned-BYPASS.** **A-29 — Content fence: two layers.** **A-30 — KV true grounds + layer rows.** **A-31 — Once-flag on success only.** **A-32 — Precision corrections.**

### FINAL FOLD (council round 3) — C1 + W5

**A-33 — C1 option (A) family-carries-to-pin; W5 escaped-body budget; advisories (i)–(iii) adopted.**

### MECHANICAL FOLD — F1 + F3

**A-34 — F1 Option A (slug 44 / entry 332; block value superseded by A-35-B1's wrapper-inclusive 2,900); F3 exclusion×drag ruled; F4–F8 dispositioned.**

### LEADER DISPOSITION — B1–B8

**A-35 —** B1 wrapper-inclusive 2,900; B2 Phase 1.5 live seeding; B3 atomic contrast family + tightened regex; B4 sweep integrity; B5 selector reuse + pinned bypass; B6+B8 allowlist (D2); B7 PG registration + AC9 materialization; notes (row-1 quantifier; economics two-bases; recurrence closed).

**A-36 — D2 DECIDED: v1 = ALLOWLIST variant** (A-14); uniform cap = phase-2 option.

---

## (ER) EMPEROR RULINGS

**ER-1 — Injection model: SPLIT + AUDIENCE + ARI SEAM + UNIVERSAL READ.** *(Emperor ruling, 2026-11-07, source: Discord — verbatim intent: "we should split [operating knowledge vs project record]. Ari you need know operation one (to control the leader, aegis,… teams), also agent like devops need know. The dev team actually need know the implementation major, and operation is also need (not all the case). It's for active injection — if agent want read them they can read any.")*
1. **SPLIT CONFIRMED** — the two-surface structure (POK vs the Related-Project record) stands. **A unified-block alternative was offered and DECLINED — considered-and-declined, decider: the Emperor.**
2. **Audience dimension** — entry field `operation` | `implementation` | `both`; default ACTIVE-injection audiences: operation → {ari, leader, devops} (**leader included — it runs the ops lanes**); implementation → {leader, developer}; both → every agent on the project. Curator narrowing via `audience_agents`. **Per-agent block budget (8 / 2,900) is a PER-AUDIENCE effective slice; admission = audience filter FIRST, then the existing semantics within the visible set; every bound holds per-view.** Audience mapping by factory-captured agent_id.
3. **Ari delivery seam** — Ari holds the OPERATION slice at dispatch time (today Ari receives no project injection); the seam sits where Ari's dispatch/work context is assembled for a target project; gates: non-default project, operation audience, same budget/fence/family semantics; per-dispatch fresh read.
4. **Universal read (invariant)** — `pok_query` returns ALL entries to ANY agent on the project, regardless of audience tags. Injection is curated; reading is free.
Landed: arch §4.1/§4.2/§4.3/§4.5/§4.5.1/§4.6/§11/§13/§14; decisions A-2/A-4/A-5/A-6/A-9/A-11/A-12/A-19 + this row; plan §2/§4.1/§4.5/AC22–AC24/§9; Appendix D.

---

## (B) DECISIONS — status

**D1 — AMENDED: the split/audience model is DECIDED by ER-1; D1's remaining open content is the IMPLEMENTATION GO.** *(Emperor ruling, 2026-11-07, source: Discord.)*
**D2 — DECIDED (leader, v3.3): v1 = ALLOWLIST variant** (A-14/A-36).
**D3 — PENDING** (guidelines un-park; wi-d2abe4f7). **D4 — PENDING** (corroboration post-D7). **D5 — PENDING** (notes hygiene lane).
**P-1 — precondition:** dev llmproxy project row (Phase 1). **Phase 1.5** = live row + live seeding incl. the ari-dispatch verification (leader/devops-owned, gated on the next promote carrying the schema).

---

## (C) Boundaries

- **C-1** Markdown only; no code/migrations/commits (the giter finalizes). **C-2** Fenced dev only; prod not probed (AC12 gates the live measurement; live seeding is Phase 1.5). **C-3** No genre migration. **C-4** *(ER-1 discipline)* the audience dimension gates ACTIVE injection ONLY — never retrieval.
