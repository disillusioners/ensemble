# POK — Project Operating Knowledge — Plan Overview (v3.3)

## ⛔ PENDING EMPEROR GO — not commissioned

**Status: DESIGN ONLY — implementation is NOT authorized.** Execution-ready phase plan for the FUTURE implementation lane, derived strictly from `architecture-recommendation.md` **v3.3** (§10 rollout; rounds 1+2, C1+W5, F1+F3, **leader disposition B1–B8** — `discharge-round-1.md` / `discharge-round-2.md` + appendices). The Emperor decides D1, D3–D5; **D2 DECIDED (allowlist)**; **P-1 (dev row) precondition; Phase 1.5 (live seeding) is leader/devops-owned.** Companion: `decisions.md` (v3.3).

**Source:** architecture-recommendation.md v3.3 @ tree 7426e0d8a (worktree `feature/project-operating-knowledge`, fenced dev).

---

## 1. Objective

Make the §1 acceptance walkthrough TRUE: the next agent working the llm-supervisor-proxy project knows the deploy truth — the 7 operational facts (a superset of the Emperor's enumerated examples) — **at spawn (deterministic, ORDERED, FENCED, wrapper-inclusively-bounded injection block) or in ONE retrieval step (`pok_query`)**. **Phase 1 proves it in DEV; Phase 1.5 makes it true on LIVE** — until Phase 1.5 runs, the walkthrough is true in dev only (the honest boundary, not a claim of done).

## 2. Scope

- **In scope (phase 1, dev):** storage; **3 tools** + category + 40-agent grant sweep (B4 rules); injection block + fence + family ordering + pinned-drag + **wrapper-inclusive char bound (slug ≤44, entry ≤332, full block 2,900)** + escape-aware budget + **metadata ALLOWLIST companion (D2 decided)**; dogfood; gates.
- **In scope (Phase 1.5 — live seeding, after the next promote carries the schema; owner leader/devops):** live project row; live 8-slug seeding via the tool lane; live fresh-leader-spawn verification.
- **In scope (phase 2):** derived git export; guidelines trust tier (D3); per-turn refresh evaluation; verify-window tuning; **`pok_verify`**; critical-notes hygiene (D5); **uniform metadata cap (deferred option)**.
- **Out of scope:** FE dashboards; watch/alert; cross-project search; KPI phases; bulk SQL backfill; genre migration.

## 3. Phases

| # | Name | Depends on |
|---|------|------------|
| 1 | Minimum slice — the llmproxy walkthrough TRUE **in dev** | Emperor go (D1); P-1 |
| **1.5** | **LIVE SEEDING (B2)** — make it true on live | Phase 1 green; next promote carries the schema; owner leader/devops |
| 2 | Extensions after the first real lane dogfoods it | Phase-1 (+1.5) dogfood; D1 (export); D3 (guidelines); D5 (notes lane) |

---

## 4. Phase 1 — the minimum slice (dev)

### 4.1 Components

1. **Storage:** two additive `.sql` (CHECKs; no FK) + repository + service (hybrid retrieval on the critical-notes embeddings infra). `UNIQUE PARTIAL INDEX uq_pok_active_slug`; `disputed`; `archived` REACHABLE via curator archive; **PG registration: models imported in `daemon/manager.py`'s pre-create_all block (~:928-987) — B7**.
2. **Tools + registration:** `pok_upsert` / `pok_query` / `pok_curate(pinned|disputed|archived)` — **3 tools v1; `pok_verify` phase 2**. Write-time fence + escaped-body budget (`pok_body_oversize`) + slug bound (`pok_slug_oversize`). Category `pok` non-privileged; house generator → artifact 60→61, `VOCABULARY_VERSION` 6→7; no `TOOL_REQUIRED_AGENTS` entry; **grant sweep per B4: 40 registered, watcher EXCLUDED (inherits), `"pok"` inserted BEFORE `"nest-report"` (kept LAST) in 39 non-empty allows + `.prompt-artifacts.sha256` + pin ceremony + the pin test `tests/unit/test_anomaly_report_grant_pins.py` green**.
3. **Injection block:** builder + orchestrator append (code-site) + scope gate (`project_id != SYSTEM_DEFAULT_PROJECT_ID`) + stable id + **caps: full rendered block 2,900 (incl. 47c wrapper — B1) / 8 entries / pin 2 × family 3 (drag 6 ≤ 8; content 2,764 + wrapper 47 = 2,811 ≤ 2,900)** + **family-atomic admission (tightened regex `^([a-z][a-z0-9]*)-(\d{2})-(.+)$` — B3) + adjacent ordered rendering + pinned-drag + F3 exclusions + F4 counts-once + F5 block-total rider** + **render fence** + **metadata ALLOWLIST companion (A-14)**. Degradation per arch §4.9.
4. **Dogfood:** §4.5 on the **dev P-1 llm-supervisor-proxy project row**.
5. **Gates:** packs; **Tier-2 whole-tree dual-side (mechanical — no Tier-1 leg)**; net-new deterministic reds = 0.

### 4.2 Files / modules the lane will touch

| Area | Files / modules |
|---|---|
| Migration | two additive `.sql` in `daemon/migrations/versions/` |
| **PG registration (B7)** | **`daemon/manager.py` pre-create_all import block (~:928-987, `# register before create_all`); `SQLModel.metadata.create_all` = PG source-of-truth (:129/:931/:942/:956/:980)** |
| Data + service | SQLModel repository + service (embeddings infra pattern); **retrieval reuses `daemon/services/critical_notes_selector.py` + `critical_notes_selection_orchestrator.py` (B5 — no invented weights)** |
| Injection builder | `context_messages.py` — builder, kind, `__all__`; insert by code site (`:2230-2335` turn-1; `:2179/:2287/:2328` refresh); **fence via `escape_for_context_block` (`:699`; KV precedent `:1403`; cap-after-escape `:1395-1403`); wrapper 47c per `:75-76/:181-182`, precedent `:205-230`** |
| Persistent head / degradation | `instance_messaging.py:4825-4830` landing; `:5083` swallow (builder never raises); once-flag **on success only** |
| Scope predicate | mirror `is_system_default` (`context_messages.py:2248/:2272`; `project_normalizer.py:6-45`; `constants.py:596-597`) |
| Metadata allowlist | `_format_project_json_section` — allowlist verbatim; non-allowlisted 2000c/key, 16384c block, omitted-count tail |
| Registry + grants | `pok` + 3 tools; artifact regen; version bump; 40-agent sweep (B4 rules); pin ceremony; **same-commit re-pins `tests/unit/metrics/test_metrics_vocabulary_amendment.py` + `tests/unit/tools/test_tool_category_artifact.py` (B7)** |
| Target project (dev) | `project_create` — dev llmproxy row (P-1) |

### 4.3 Gates

Pack registration; **Tier-2 whole-tree dual-side** — mechanical (seam list includes `daemon/services/instance_messaging.py`); no Tier-1 leg. Net-new deterministic reds = 0.

### 4.4 Risks carried by Phase 1

| Risk | Severity | Binding |
|---|---|---|
| Metadata companion slips | 🔴 | Same-lane item 3; **allowlist inflation is the new shape** — allowlist review + AC6 + AC12 |
| Wrong-but-confident fact broadcast | 🔴 | Curator-only pinned-supersede; `disputed`; lineage; inline markers; leader pin review |
| Content forgery via body | 🟡 | Two-layer fence (A-29; AC17) |
| Terminal 64-cap lockout | 🟡 | Cured in v1 — curator archive (AC19) |
| Dispute laundering (paraphrase) | 🟡 | Normalized-hash guard (A-4); curator review residual |
| Ordering regress | 🟡 | Family-atomic + adjacency + pinned-drag + **tightened regex (B3; AC18)**; contrast family atomic — one-sided render unreachable |
| Pinned×family overflow (rows) | 🟡 | PIN(2)×FAM(3)=6≤8; `pok_pin_cap` / `pok_family_full` (AC11) |
| **Char-side / wrapper overflow** | 🟡 | **DERIVED incl. 47c wrapper (B1): content 2,764 + 47 = 2,811 ≤ 2,900; pinned 2,185 ≤ 2,900 (AC11)** |
| Escape-expansion truncation | 🟡 | Post-escape budget at write (`pok_body_oversize`; AC20) |
| **Grant-sweep breakage (B4)** | 🟡 | **Watcher excluded (empty allow = inherit-all); `nest-report` LAST; pin test = tripwire; violating either silently strips toolsets** |
| Retrieval config drift (B5 selector reuse) | 🟡 | Shared dependency; pinned bypass + AC3 all-8 bound the blast radius |
| Scope predicate wrong | 🟡 | AC10 both directions; dogfood on a real project row |
| Tool growth (3 × 40) | 🟡 | Minimal schemas |

### 4.5 DOGFOOD PROOF PROTOCOL (dev)

**Target:** the dev P-1 `llm-supervisor-proxy` project row (main_directory = `/home/nea/Code/opensource-projects/llm-supervisor-proxy`). **Source:** the prior session transcript (only surviving copy), leader-curated — NOT SQL backfill. All slugs via `pok_upsert`, `provenance=observed`, fenced dev daemon.

| # | slug | domain | note |
|---|------|--------|------|
| 1 | `artifact-is-container-image` | artifact | unordered (27c) |
| 2 | `pipeline-01-merge-master-triggers-image-ci` | deploy | family `pipeline` step 1 (42c) |
| 3 | `pipeline-02-verify-image-tag-digest-feature` | deploy | family `pipeline` step 2 (43c) |
| 4 | `pipeline-03-deploy-order-daoduc-backup-first` | deploy | family `pipeline` step 3 — pinned via `pok_curate` (leader); 44c = the bound-setting slug |
| 5 | `deploy-01-daoduc-backup` | environment | **family `deploy` step 1 — atomic contrast family (B3)** (23c) |
| 6 | `deploy-02-ensem-dev-primary` | environment | **family `deploy` step 2 — contrast family** (27c) |
| 7 | `healthz-in-cluster-only` | verify | unordered (23c); optional critical-note POINTER |
| 8 | `model-catalogs-19-vs-22-not-drift` | catalog | unordered (33c) |

**Sizing consequence (derived):** all slugs ≤44c; bodies 54–93c raw (escaped = raw); entry ≤332c; content worst 2,764 + wrapper 47 = **2,811 ≤ 2,900**; dogfood pinned tier ≈ 3×182 + 140 + 47 ≈ 733 ≤ 2,900. **Order consequence:** the `pipeline` spine renders 01→02→03 adjacently every render (pinned tier under C1-A); the `deploy` contrast renders as ONE atomic unit — **a one-sided render of fact 5/6 is structurally unreachable (B3)**. Slugs freeze AFTER dogfood.

---

## 4.5b PHASE 1.5 — LIVE SEEDING (B2; owner leader/devops; gate: next promote carries the schema)

1. **Live project row:** `project_create` on the LIVE install — `llm-supervisor-proxy`, `main_directory=/home/nea/Code/opensource-projects/llm-supervisor-proxy`.
2. **Live seeding:** the same 8 slugs via `pok_upsert` against **prod through the tool lane** (leader-curated, `observed`; fenced write discipline; per-slug confirmation).
3. **Live verification:** a **fresh leader spawn on the live daemon** → block renders 8 entries, `pipeline` spine ordered 01→02→03 at the pinned tier, `deploy` contrast atomic; `pok_query('deploy order')` returns the order slug.
**Boundary, stated plainly: until Phase 1.5 step 3 passes, the walkthrough is TRUE IN DEV ONLY — dev proof is not a claim of done.**

---

## 5. Phase 2

| # | Item | Gate |
|---|------|------|
| 2.1 | Derived git export (private ensemble repo only; never read) | D1 |
| 2.2 | Guidelines trust tier (`project_id NULL`; dev/maintenancer; immutable) | D3 |
| 2.3 | Per-turn refresh evaluation (day-1 stable id) | dogfood evidence |
| 2.4 | Per-domain verify-window tuning | evidence |
| 2.5 | **`pok_verify`** (deferred from v1) | — |
| 2.6 | Critical-notes hygiene (compaction/archive) | D5 |
| 2.7 | **Uniform metadata cap** (deferred option; v1 ships the allowlist — D2 decided) | D2 phase-2 review |

## 6. Coupling & dependency map

Phase 2 on phase-1 (+1.5) dogfood; export on D1; guidelines on D3; notes lane on D5; metadata companion INSIDE phase 1 (allowlist); POK block requires a non-default project (dev P-1 for Phase 1; live row for Phase 1.5; AC10 pins default behavior); refresh on the day-1 stable id.

## 7. Acceptance criteria (measurable)

| ID | Criterion |
|----|-----------|
| AC1 | Fresh leader spawn on the **DEV llmproxy project** renders the block containing the 8 entries (the 7 dogfood facts; one fact spans two slugs) **with the pipeline spine in order 01→02→03 at the pinned tier and the deploy contrast atomic** (live-path truth is AC21) |
| AC2 | A worker on the SAME project receives the same block; **isolation: an agents-ensemble worker does NOT receive llmproxy facts** |
| AC3 | **ALL 8 dogfood slugs are retrievable via `pok_query` by their own natural query** (e.g. 'deploy order' → the order slug; 'healthz' → the healthz slug; 'catalog' → the catalog slug) — **a totally broken ranker must fail**; the order slug is rank 1 for its query (pinned bypass, B5); both legs (warm embeddings; BM25-only lag) |
| AC4 | Supersede of a NON-pinned slug flips old→superseded + link in ONE txn; dual-active impossible |
| AC5 | Forced old `last_verified_at` → `[stale Nd]` renders, ranks down, never dropped |
| AC6 | **Metadata allowlist (B6/B8):** (a) at least one named allowlisted key (e.g. `ari_plane_tracking_policy`) renders **verbatim, untruncated** — the AC FAILS if any allowlisted key is absent or truncated; (b) non-allowlisted keys are capped 2000c/key within a 16384c block with the omitted-count tail |
| AC7 | Packs + Tier-2 whole-tree dual-side, net-new deterministic reds = 0 (no Tier-1 leg) |
| AC8 | Artifact regenerated via house generator (60→61; `VOCABULARY_VERSION`=7 in `checkpoint_metrics.py`); **same-commit re-pins: `tests/unit/metrics/test_metrics_vocabulary_amendment.py` + `tests/unit/tools/test_tool_category_artifact.py`** (B7) |
| AC9 | **PG MATERIALIZATION (B7):** a scratch-PG boot materializes BOTH tables (the models are imported in `daemon/manager.py`'s pre-create_all block) — not merely "two .sql files exist, no FK" |
| AC10 | Default project: block renders NOTHING; `pok_upsert` → `pok_default_project_unsupported`; `pok_query` → `pok_no_project` |
| AC11 | Leader `pok_curate(pinned=True)` on `pipeline-03` renders the WHOLE spine verbatim and ordered at the pinned tier ahead of actives; non-leader `pok_curate` and non-leader pinned-supersede → typed refusals (echo + `ens_system_log` WARNING = the recording surface); **rows leg: 3rd pin → `pok_pin_cap`, 4th family member → `pok_family_full`, drag 2×3=6≤8 enforced; chars leg: a worst-case admitted block renders at FULL rendered size (content 2,764 + wrapper 47 = 2,811) ≤ `POK_BLOCK_MAX_CHARS` 2,900 — pinned tier 2,185 ≤ 2,900; no truncation (B1)** |
| AC12 | **Live-daemon metadata re-measurement** captured BEFORE the cap is claimed: 69 keys / 279,810 B (metadata-dict basis) and the whole project JSON (408,809 B basis); recompute both shares (279,810/408,809 = 68.4 %); the "82 %" head-share figure is NOT asserted unless re-derived with its denominator |
| AC13 | Store-down: injection completes with ALL other persistent blocks intact, POK block omitted, WARNING; the once-flag is NOT written and the next turn retries; `pok_query` → typed `pok_store_unavailable` |
| AC14 | Embedding lag: affected entry returns BM25-only + `embedding_lag: true` (verify-verb assertions are PHASE 2) |
| AC15 | **(Replaced per B6/B8)** (a) **allowlisted keys ARE the recovery path** — load-bearing metadata is never omitted; (b) **non-allowlisted keys are omitted by design** (capped section + omitted count); (c) no rendered tail names a tool the receiving audience lacks (no dead pointers; `project_get` documented in the runbook, not dangled) |
| AC16 | Curator `disputed=True`: entry leaves injection, readable via `pok_query` with `[DISPUTED]`; agents cannot reactivate; normalized-hash re-upsert of the disputed body under a NEW slug → `pok_disputed_content`; upsert onto an ARCHIVED slug flips it superseded (never resurrects) and inserts a fresh guarded active row |
| AC17 | **Fence:** a body containing `## Related Project`, `[SYSTEM CONTEXT: …`, or a ``` fence is REJECTED at write (`pok_body_structural_token`); a body smuggled past validation renders INERT |
| AC18 | **Ordering incl. pinned-drag + exclusions + atomic contrast:** the pipeline family renders adjacently in slug order 01→02→03 in EVERY block render and never splits — ranked or pinned; **B3 leg: the `deploy-01/02` contrast renders as ONE atomic unit — a one-sided render is unreachable**; **F3 leg:** a middle member disputed/superseded/archived → survivors drag contiguously, the excluded row never renders, pinned-but-excluded cancels the drag, fully-excluded renders nothing; **F4 leg:** a doubly-pinned family renders and is counted ONCE on rows AND chars; **regex leg:** `model-catalogs-19-vs-22-not-drift` and `daoduc-helm-release-v28-…`-shaped slugs are UNORDERED (tightened regex) |
| AC19 | **Terminality cured:** at cap 64 the 65th upsert refuses naming the oldest-least-verified slug AND the archive cure; curator archive frees the slot (row readable); the 65th upsert then succeeds |
| AC20 | **Escape-aware sizing (W5/F1/F6), both legs:** (positive) a legal &<>-dense body (escaped length ≤ 240) renders within its 332c entry ceiling and the block stays ≤2,900; (negative) a body whose escaped length exceeds 240 is refused at write with the **dedicated typed `pok_body_oversize`** — no render-time silent truncation |
| **AC21** | **LIVE-PATH CHECK (B2, Phase 1.5):** on the LIVE install — the llmproxy project row exists; the 8 slugs are seeded via the tool lane; a **fresh leader spawn on the live daemon** renders the block with the 8 entries, spine ordered, pinned at top; `pok_query('deploy order')` returns the order slug. **Until AC21 passes, the walkthrough is dev-true only.** |

## 8. Assumptions

- Live metadata measurements inherited; AC12 re-measures and recomputes both bases (dev DB carries 0 rows — verified).
- `pok` free at HEAD (verified absent from registry + artifact); derive registration via the generator.
- Slugs freeze AFTER dogfood (B3's re-slug is design-time; all 8 slugs ≤44c — the flagship is the bound-setter).
- The 40-agent denominator re-derives mechanically if the tree moves; the sweep excludes watcher (empty allow = inherit-all).

## 9. Open conditions before implementation starts

1. **D1** canon ratification. 2. **D2 — DECIDED (allowlist; v1)**; uniform cap = phase-2 option. 3. **D3** guidelines un-park. 4. **D4** corroboration post-D7. 5. **D5** notes hygiene lane. 6. **P-1** dev llmproxy project row — without it AC1/AC2 cannot be exercised. 7. **Phase 1.5** (live seeding) runs after the next promote carries the schema — AC21 gates the live claim.
