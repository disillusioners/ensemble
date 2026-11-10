# POK — Project Operating Knowledge — Plan Overview (v3.4)

## ⛔ PENDING EMPEROR GO — not commissioned

**Status: DESIGN ONLY — implementation is NOT authorized.** Execution-ready phase plan for the FUTURE implementation lane, derived strictly from `architecture-recommendation.md` **v3.4** (rounds 1+2, C1+W5, F1+F3, leader disposition B1–B8, **Emperor-ruling fold ER-1** — `discharge-round-1.md` / `discharge-round-2.md` + appendices B/C/D). **D1's remaining open content is the implementation go — the split/audience model is DECIDED by ER-1** *(Emperor ruling, 2026-11-07, source: Discord)*; D2 DECIDED (allowlist); D3–D5 pending; P-1 + Phase 1.5 preconditions. Companion: `decisions.md` (v3.4, ER-1).

**Source:** architecture-recommendation.md v3.4; canon @ 7426e0d8a, amended on `feature/project-operating-knowledge-ruling` @ 5672f055c (fenced dev).

---

## 1. Objective

Make the §1 acceptance walkthrough TRUE: the next agent working the llm-supervisor-proxy project knows the deploy truth — the 7 operational facts — **at spawn (deterministic, ORDERED, FENCED, audience-scoped, wrapper-inclusively-bounded injection) or in ONE retrieval step (`pok_query` — universal read)**. **Ari holds the OPERATION slice at dispatch time (ER-1).** Phase 1 proves it in DEV; Phase 1.5 makes it true on LIVE — until then, dev-true only.

## 2. Scope

- **In scope (phase 1, dev):** storage **with the `audience`/`audience_agents` fields (ER-1)**; **3 tools** (+ audience params) + category + 40-agent grant sweep (B4 rules); injection block **with the audience filter + per-view admission + the ARI DISPATCH SEAM (ER-1)** + fence + family ordering + wrapper-inclusive bound + escape-aware budget + metadata ALLOWLIST companion (D2 decided); dogfood **with classifications**; gates.
- **In scope (Phase 1.5 — live seeding; owner leader/devops; gated on the next promote carrying the schema):** live project row; live 8-slug seeding (with classifications) via the tool lane; live fresh-leader-spawn AND **ari-dispatch** verification.
- **In scope (phase 2):** derived export; guidelines tier (D3); per-turn refresh evaluation; verify-window tuning; `pok_verify`; notes hygiene (D5); uniform metadata cap option.
- **Out of scope:** FE dashboards; watch/alert; cross-project search; KPI phases; bulk SQL backfill; genre migration; **any audience-based RETRIEVAL filtering (ER-1 invariant: reading is free)**.

## 3. Phases

| # | Name | Depends on |
|---|------|------------|
| 1 | Minimum slice — the llmproxy walkthrough TRUE **in dev** | Emperor implementation go (D1-remainder); P-1 |
| **1.5** | **LIVE SEEDING (B2 + ER-1)** — true on live, incl. the ari seam | Phase 1 green; next promote carries the schema; leader/devops |
| 2 | Extensions | Phase-1(+1.5) dogfood; D1 (export); D3; D5 |

---

## 4. Phase 1 — the minimum slice (dev)

### 4.1 Components

1. **Storage:** two additive `.sql` (CHECKs incl. **`audience ∈ operation|implementation|both`** + `audience_agents` override; no FK) + repository + service (hybrid retrieval on the critical-notes embeddings infra). `UNIQUE PARTIAL INDEX uq_pok_active_slug`; `disputed`; `archived` REACHABLE; PG registration via `daemon/manager.py` pre-create_all (B7).
2. **Tools + registration:** `pok_upsert(…, audience='operation')` / `pok_query` / `pok_curate(pinned|disputed|archived|audience|audience_agents)` — **3 tools v1; `pok_verify` phase 2**. Fence + escaped-body budget + slug bound. Category `pok` non-privileged; house generator → 60→61, `VOCABULARY_VERSION` 6→7; no `TOOL_REQUIRED_AGENTS`; **B4 sweep rules** + `.prompt-artifacts.sha256` + pin ceremony.
3. **Injection block:** builder + orchestrator append (code-site) + scope gate + stable id + caps (full block 2,900 incl. 47c wrapper / 8 entries / pin 2 × family 3) + **AUDIENCE FILTER FIRST, then per-view admission (ER-1)** + **ARI DISPATCH SEAM (8th touch-point class; operation view; per-dispatch fresh read; same budget/fence)** + family-atomic admission (tightened regex) + pinned-drag + F3(+audience-invisible) exclusions + F4 counts-once + F5 rider + render fence + **metadata ALLOWLIST companion (UNAFFECTED by ER-1)**. Degradation per arch §4.9 (per-view).
4. **Dogfood:** §4.5 on the **dev P-1 llm-supervisor-proxy project row**.
5. **Gates:** packs; **Tier-2 whole-tree dual-side (mechanical — no Tier-1 leg)**; net-new deterministic reds = 0.

### 4.2 Files / modules the lane will touch

| Area | Files / modules |
|---|---|
| Migration | two additive `.sql` in `daemon/migrations/versions/` (audience CHECK included) |
| PG registration (B7) | `daemon/manager.py` pre-create_all import block |
| Data + service | SQLModel repository + service; **retrieval reuses `critical_notes_selector.py` + `critical_notes_selection_orchestrator.py` (B5)** |
| Injection builder | `context_messages.py` — builder, kind, `__all__`; code-site insert; fence via `escape_for_context_block` (`:699`); wrapper 47c (`:75-76`; precedent `:205-230`) |
| **Ari seam (ER-1)** | **Ari's dispatch/work context assembly site — emits the operation-audience view block for the target project (gates: non-default project; operation audience; same budget/fence; per-dispatch fresh read)** |
| Persistent head / degradation | `instance_messaging.py:4825-4830` landing; `:5083` swallow; once-flag on success only |
| Scope predicate | mirror `is_system_default` (`context_messages.py:2248/:2272`; `project_normalizer.py:6-45`; `constants.py:596-597`) |
| Metadata allowlist | `_format_project_json_section` (allowlist verbatim; non-allowlisted capped; count-only tail) |
| Registry + grants | `pok` + 3 tools; artifact regen; version bump; 40-agent sweep (B4); pin ceremony; B7 re-pins |
| Target project (dev) | `project_create` — dev llmproxy row (P-1) |

### 4.3 Gates

Pack registration; **Tier-2 whole-tree dual-side** — mechanical; no Tier-1 leg. Net-new deterministic reds = 0.

### 4.4 Risks carried by Phase 1

| Risk | Severity | Binding |
|---|---|---|
| Metadata companion slips / allowlist inflation | 🔴 | Same-lane item 3; AC6 + AC12 |
| Wrong-but-confident fact broadcast | 🔴 | Curator-only pinned-supersede; `disputed`; lineage; markers; leader pin review |
| **Audience misclassification (ER-1)** | 🟡 | **Universal read means nothing is hidden (dev can `pok_query` anything); curator-adjustable; dogfood classifications recorded (§4.5)** |
| **Ari-seam staleness** | 🟡 | **Per-dispatch fresh read; mid-lane `pok_query`** |
| Content forgery via body | 🟡 | Two-layer fence (AC17) |
| Terminal active-cap lockout | 🟡 | Curator archive (AC19) |
| Ordering regress | 🟡 | Family-atomic + adjacency + pinned-drag + tightened regex; contrast family atomic; **spine assertion is per-view (operation view holds fully)** |
| Pinned×family overflow | 🟡 | 6≤8 rows; 2,811≤2,900 chars incl. wrapper — per-view (AC11) |
| Escape-expansion truncation | 🟡 | Post-escape budget at write (AC20) |
| Grant-sweep breakage | 🟡 | B4 rules; pin test tripwire |
| Selector drift (reuse) | 🟡 | Pinned bypass + AC3 all-8 |
| Scope predicate wrong | 🟡 | AC10 both directions |
| Tool growth (3 × 40) | 🟡 | Minimal schemas |

### 4.5 DOGFOOD PROOF PROTOCOL (dev) — with audience classification (ER-1)

**Target:** the dev P-1 `llm-supervisor-proxy` project row. **Source:** the prior session transcript, leader-curated — NOT SQL backfill. All slugs via `pok_upsert`, `provenance=observed`, fenced dev daemon.

| # | slug | domain | **audience** | note |
|---|------|--------|--------------|------|
| 1 | `artifact-is-container-image` | artifact | **both** | universal artifact truth (25c) |
| 2 | `pipeline-01-merge-master-triggers-image-ci` | deploy | **both** | devs merge knowingly; ops know nothing ships without CI (38c) |
| 3 | `pipeline-02-verify-image-tag-digest-feature` | deploy | **operation** | post-build verification ritual (39c) |
| 4 | `pipeline-03-deploy-order-daoduc-backup-first` | deploy | **operation** | pinned via `pok_curate`; 44c bound-setter |
| 5 | `deploy-01-daoduc-backup` | environment | **operation** | atomic contrast family step 1 (24c) |
| 6 | `deploy-02-ensem-dev-primary` | environment | **operation** | contrast family step 2 (27c) |
| 7 | `healthz-in-cluster-only` | verify | **both** | environment property — probing hazard for anyone (24c) |
| 8 | `model-catalogs-19-vs-22-not-drift` | catalog | **operation** | drift triage (33c) |

**Views:** ari/leader/devops → 8 entries; developer → 3 (artifact, CI-trigger, healthz) — honest: this payload is operational knowledge; no `implementation` facts yet. **Sizing (per-view):** entries ≤332c; content worst 2,764 + wrapper 47 = 2,811 ≤ 2,900. **Order (per-view):** the operation view renders the spine 01→02→03 pinned-ordered + the contrast atomic; the developer view renders the pipeline family's visible member(s) contiguously (audience-invisible = excluded-for-view). Slugs freeze AFTER dogfood.

---

## 4.5b PHASE 1.5 — LIVE SEEDING (B2 + ER-1; owner leader/devops; gate: next promote carries the schema)

1. **Live project row** (`project_create`; `main_directory=/home/nea/Code/opensource-projects/llm-supervisor-proxy`).
2. **Live seeding:** the 8 slugs WITH classifications via `pok_upsert` against prod through the tool lane.
3. **Live verification:** a fresh leader spawn on the live daemon (operation view: 8 entries, spine ordered, pinned top) **+ an ARI DISPATCH on the live daemon — Ari's dispatch context carries the operation slice (AC24 leg)**.
**Boundary: until step 3 passes (both legs), the walkthrough is TRUE IN DEV ONLY.**

---

## 5. Phase 2

| # | Item | Gate |
|---|------|------|
| 2.1 | Derived git export (private ensemble repo only) | D1 |
| 2.2 | Guidelines trust tier | D3 |
| 2.3 | Per-turn refresh evaluation | dogfood evidence |
| 2.4 | Per-domain verify-window tuning | evidence |
| 2.5 | `pok_verify` | — |
| 2.6 | Critical-notes hygiene | D5 |
| 2.7 | Uniform metadata cap option | D2 phase-2 review |

## 6. Coupling & dependency map

Phase 2 on phase-1(+1.5) dogfood; export on D1; guidelines on D3; notes lane on D5; metadata companion INSIDE phase 1 (allowlist); **the ari seam rides phase 1 (design + dev proof) and phase 1.5 (live)**; POK block requires a non-default project; refresh on the day-1 stable id.

## 7. Acceptance criteria (measurable)

| ID | Criterion |
|----|-----------|
| AC1 | Fresh **leader** spawn on the **DEV llmproxy project** (operation view) renders the block containing the 8 entries with the spine ordered 01→02→03 at the pinned tier and the contrast atomic (live = AC21; **audience legs = AC22**) |
| AC2 | A **worker** on the SAME project receives its own view (worker ∈ no default audience set → sees `both` entries only — artifact, CI-trigger, healthz); **isolation: an agents-ensemble worker receives nothing** |
| AC3 | **ALL 8 slugs retrievable via `pok_query` by their own natural query from ANY audience position** (a developer's `pok_query('deploy order')` returns the order slug — universal read, AC23); order slug rank-1 for its query (pinned bypass); both legs |
| AC4 | Supersede txn; dual-active impossible |
| AC5 | `[stale Nd]` renders, ranks down, never dropped |
| AC6 | **Metadata allowlist:** a named allowlisted key (e.g. `ari_plane_tracking_policy`) renders verbatim untruncated (FAILS if absent/truncated); non-allowlisted capped 2000c/key / 16384c block + omitted-count tail |
| AC7 | Packs + Tier-2 whole-tree dual-side, net-new 0 |
| AC8 | Artifact regen (60→61; version 7) + B7 re-pins (`tests/unit/metrics/test_metrics_vocabulary_amendment.py`, `tests/unit/tools/test_tool_category_artifact.py`) |
| AC9 | **PG MATERIALIZATION:** scratch-PG boot shows BOTH tables |
| AC10 | Default project: no block; typed refusals both tools |
| AC11 | Leader pin renders the spine ordered at the pinned tier (operation view); typed admission refusals (`pok_pin_cap`, `pok_family_full`); **per-view char leg: worst admitted view renders ≤ 2,900 full-render (content 2,764 + wrapper 47)** |
| AC12 | Live metadata re-measurement, both bases recomputed |
| AC13 | Store-down: other blocks intact; once-flag unwritten; typed error |
| AC14 | Embedding lag: BM25-only + flag (verify assertions phase 2) |
| AC15 | Allowlist = the recovery path; non-allowlisted omitted by design; no dead pointers |
| AC16 | Disputed suppression + `[DISPUTED]` readable + laundering guard + archived forward-only |
| AC17 | Fence: forged-header body rejected at write / renders inert |
| AC18 | **Ordering per-view:** in the OPERATION view the spine renders 01→02→03 adjacent every render, contrast atomic, F3 exclusions hold, doubly-pinned family counted once, tightened-regex slugs unordered; **in a restricted view the family renders its VISIBLE members contiguously (audience-invisible = excluded-for-view)** |
| AC19 | Terminality cured (refuse → archive → succeed) |
| AC20 | Escape-aware sizing both legs (332 ceiling; `pok_body_oversize`) |
| AC21 | **LIVE-PATH (B2):** live row + live seeding + fresh live leader spawn (operation view) green — until then dev-true only |
| **AC22** | **AUDIENCE-FILTERED INJECTION (ER-1):** a **devops** instance sees operation∪both (8 entries here); a **developer** instance sees implementation∪both (3 entries — artifact, CI-trigger, healthz); a **worker** (no default audience) sees `both` only (3); the **leader** (member of both default sets) sees ALL — each view within the same 8/2,900 budget |
| **AC23** | **UNIVERSAL READ (ER-1):** `pok_query` from a developer instance returns entries of ALL audiences (the operation-only deploy facts are readable, never hidden); the audience field filters injection ONLY |
| **AC24** | **ARI SEAM (ER-1):** at an ari dispatch targeting a non-default project, Ari's dispatch context carries the operation-audience view block (fresh read at dispatch; same budget/fence); NO block when the target is the system-default project |

## 8. Assumptions

- Live metadata measurements inherited; AC12 re-measures (dev DB carries 0 rows).
- `pok` free at HEAD; registration via the generator. Slugs freeze AFTER dogfood.
- The 40-agent denominator re-derives mechanically; sweep excludes watcher.
- **Audience default sets are design-time constants (ari/leader/devops; leader/developer); adding an agent to a default set later is a curator/config concern, not a schema change.**

## 9. Open conditions before implementation starts

1. **D1 — remaining open content: the IMPLEMENTATION GO** (the split/audience model is DECIDED by ER-1 — Emperor ruling, 2026-11-07, source: Discord). 2. **D2 — DECIDED (allowlist v1).** 3. **D3** guidelines un-park. 4. **D4** corroboration post-D7. 5. **D5** notes hygiene lane. 6. **P-1** dev llmproxy row. 7. **Phase 1.5** live seeding incl. the ari-dispatch leg — AC21/AC24 gate the live claim.
