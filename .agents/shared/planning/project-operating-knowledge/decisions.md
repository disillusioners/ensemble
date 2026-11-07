# POK — Project Operating Knowledge — Decision Record (decisions.md)

**Companion artifact to:** `architecture-recommendation.md` **v3.3** in this directory — this record derives from it and cites it.
**Tree:** v0.29.0 @ 7426e0d8a, worktree `feature/project-operating-knowledge` (fenced dev).
**Status: DESIGN ONLY — implementation is NOT authorized.** The Emperor decides D1, D3–D5 + P-1; **D2 is DECIDED** (allowlist, A-36). Markdown-only; the giter commits later.
**Revision note:** round-1 (v2); round-2 (v3); C1+W5 (v3.1); F1+F3 (v3.2); **leader disposition B1–B8 (v3.3)** — see `discharge-round-1.md` / `discharge-round-2.md` + appendices B/C. Forced choices: A-26…A-32, A-33 (C1/W5), A-34 (F1/F3), **A-35 (leader disposition), A-36 (D2 allowlist decided)**.

**ID conventions:** `A-n` = decided-so-far (cites architecture §s). `D1–D5` = decisions (D2 now DECIDED; D1/D3/D4/D5 PENDING EMPEROR). `P-1` = implementation precondition. `C-n` = boundaries. "D7" = the standing proxy `coding`-alias DEFECT id.

---

## (A) DECIDED-so-far

### Canon & storage

**A-1 — Canon A: DB-canon, daemon-side, new surface.** (§3; §9.) Reuse bar: *no incumbent can deliver GUARANTEED INJECTION OF ORDERED PROCEDURES to every agent on a non-default project* — mechanism-backed (A-26), bounded under pinning (A-33), proven on rows AND chars **including the `_make_context_message` wrapper** (A-35-B1: 2,811 ≤ 2,900).

**A-2 — Storage shape.** Two tables (§4.1): `project_operating_knowledge` (id; `project_id` NO FK; slug ≤44; CHECK `domain`/`provenance`; CHECK `status ∈ active|superseded|archived` — archived REACHABLE via `pok_curate(archived=True)`; `disputed`; `superseded_by_id` WIRED; `content_sha256`; factory-captured author; timestamps; counts; `pinned`) + embeddings companion. `UNIQUE PARTIAL INDEX uq_pok_active_slug (project_id, slug) WHERE status='active'`. Two additive `.sql`; CHECKs on the SQLModel table + mirrored in the audit files.

**A-3 — Genre split (definitive), no genre migrates.** (§4.1.)

### Write path

**A-4 — ≤2-call capture + guards.** `pok_upsert(slug, body, domain, provenance)`: slug `[a-z0-9-]` ≤44 (`pok_slug_oversize`); exact-dup `content_sha256` → no-op echo; normalized-hash dispute-laundering guard (`pok_disputed_content`); else supersede txn; upsert onto disputed flips superseded; **upsert onto ARCHIVED flips superseded (forward-only, never resurrects) + fresh guarded active row**. Write-time fence: `pok_body_structural_token` (newline / leading `#` at body start / `[SYSTEM CONTEXT:` / ```). Escape-aware budget: `len(escape_for_context_block(body)) ≤ 240` (`pok_body_oversize`). (§4.2.)

**A-5 — Writers, scope predicate, attribution.** Non-default project only (`project_id != SYSTEM_DEFAULT_PROJECT_ID`; `project_normalizer.py:6-45`; `instance_lifecycle.py:1895`; constants `:596-597`; `api.py:2656`; mirrors `context_messages.py:2248/:2272`). Default → block suppressed; typed `pok_default_project_unsupported` / `pok_no_project`. Attribution = factory-captured `agent_id` — **attribution ≠ authorization**.

**A-6 — Admission + sizing.** Active cap 64 refuse-never-evict (refusal names oldest-least-verified + archive cure); each family member consumes one slot; family admission >3 active → `pok_family_full`. `POK_BODY_MAX_CHARS=240` **escaped**; `POK_SLUG_MAX_CHARS=44`; `POK_PIN_CAP_PER_PROJECT=2`; `POK_FAMILY_MAX=3` (2×3 = 6 ≤ 8).

### Update / curate / currency

**A-7 — Append-with-supersede ONLY; the txn IS the design.** (§4.3.)

**A-8 — Currency.** `last_verified_at` at write; same-content re-upsert refreshes. `pok_verify` DEFERRED to phase 2; v1 = 3 tools. Decay `exp(-idle/30d)` + `[stale Nd]`. Embedding-lag: BM25-only + `embedding_lag: true`.

**A-9 — Authorization matrix + archive + recording surface.**

| Operation | Non-pinned | Pinned |
|---|---|---|
| `pok_upsert` (supersede) | any project-linked agent | leader/PM only → `pok_pinned_requires_curator` |
| `pok_curate(pinned / disputed / archived)` | leader/PM only | leader/PM only |
| `pok_verify` | phase 2 | phase 2 |

`pok_curate(slug, pinned=None, disputed=None, archived=None)` — pin cap 2 (`pok_pin_cap`); dispute suppresses injection, stays readable; archive frees the active slot, row readable. Recording: typed echo + `ens_system_log` WARNING; no DB refusal ledger. Suspected-wrong protocol unchanged.

### Trust / provenance

**A-10 — declared|observed inline; `[DISPUTED]`; ranking = decay × observed × access; lineage always rendered; pinned rows BYPASS the retrieval threshold in `pok_query` (A-35-B5).** (§4.4, §4.6.)

### Injection layer

**A-11 — Block shape + ordering + fence + pinned-drag + wrapper-inclusive cap.** Block `## Project Operating Knowledge`; builder + ~7 touch-points; placement by code site. Membership: pinned tier first (≤2 pins; a pinned member CARRIES ITS WHOLE FAMILY — adjacent, slug order, at the pinned position; doubly-pinned family counted ONCE on rows AND chars), then ranked actives, **F5 rider: stop where rendered chars would exceed `POK_BLOCK_MAX_CHARS` OR entries would exceed 8; tail counts remainder**. **B3: the fact-5/6 contrast is ONE family (`deploy-01/02`) — one-sided render structurally unreachable.** **F3: excluded rows never render — survivors drag contiguously; pinned-member exclusion cancels the drag; zero survivors → nothing.** **B1 arithmetic (FULL rendered block incl. wrapper):** entry ≤332 (escaped body 240 + derived overhead 92); content worst 2,764; **wrapper 47 (CONTEXT_PREFIX 17 + title 27 + CONTEXT_SUFFIX 3, `context_messages.py:75-76/:181-182`; precedent `:205-230`) → 2,811 ≤ `POK_BLOCK_MAX_CHARS = 2,900`**; pinned 2,138+47 = 2,185 ≤ 2,900. Stable id `pok:{project_id}` day 1; v1 once-per-instance; opt-out `pok_inactive`; once-flag writes ONLY on successful render.

### Retrieval layer

**A-12 — `pok_query` — SELECTOR REUSE (B5, decided).** No invented fusion weights (the v3.x "0.4·BM25+0.6·cos ≥ 0.25" is DELETED — it exists in no code; weights are configuration). Reuse `daemon/services/critical_notes_selector.py` + `critical_notes_selection_orchestrator.py` (configurable `fusion_bm25_weight`/`fusion_vector_weight`/`fusion_threshold`). **Pinned rows BYPASS the threshold (pinned-always-returns, mirroring blueprint core forced 1.0); the threshold applies to non-pinned rows only.** Family chains in slug order; exclusions per F3; archived on explicit request; server-side scope; typed errors; lag surfaced. **AC3: ALL 8 dogfood slugs retrievable by their own natural query.**

### Sizing constants

**A-13 —** body 240 (**escaped**) · slug ≤44 · entry ≤332 · active 64 (curable via archive) · pin 2 · family-max 3 · **block 2,900 (FULL rendered block incl. wrapper 47c — B1)** / 8 · retrieval = selector-configured threshold, non-pinned only (B5) · window 30d. (§4.7.)

### REQUIRED same-lane companion

**A-14 — Metadata: v1 ships the ALLOWLIST variant (D2 DECIDED — A-36).** Named ALLOWLIST renders VERBATIM: `ari_plane_tracking_policy`, `plane_workspace_discipline`, `deferred_key_rotation`, `deferred_key_rotation_f7_addition`, `upgrade_policy`, `upgrade_workflow_preference`, `blueprint_active`, `dependency_self_install_philosophy`, `self_provisioning_directive`, `opendesign_decisions`, the `plugin_*` policy keys, `staged_release_v0290_ceremony_notes` (leader-maintained; refinable, NAMED here). All non-allowlisted: 2000c/key, 16384c block, omitted-count tail. **No new read tool** — the allowlist IS the recovery path; forensic keys via `project_get` (5 privileged agents), stated honestly. **Economics, two bases (reconciled):** metadata dict 279,810B/69 keys; whole project JSON 408,809B; 279,810/408,809 = **68.4 %**; the "82 %" share-of-head figure is withdrawn as a computed claim (unstated denominator). **AC12** re-measures live and recomputes both bases. (§4.8.)

### Rejected carriers

**A-15 —** (§6; ten stores + non-carrier rows; 16-row map)
1. File-canon — friction/probability/anchor/security.
2. Critical-notes — references ARE injected (`:1030-1040`); rejection on field-caps + writer + selection.
3. Blueprint — async writer + turn-1 gate + no supersede; genre ban = prompt-doc.
4. Project-skill files / DB tier — opt-in 11/40, max-2.
5. Metadata-JSONB — uncapped, no lineage.
6. Memoir extension — scope/writer/genre.
7. Board — no injection.
8. Shared Meta KV — tree-scoped `context_key`; 4096 cap; per-tree friction; no retrieval/lifecycle; 28/40 allow-grants (29 mentions incl. critic deny). Donates stable-id refresh + escape fence + cap-after-escape.
9. `infra_assets` — no injection seam; 3 grants + deny; asset genre; history≠currency.
10. Prompt-memory — agent-scoped persona; filename index.

### Reuse verdict

**A-16 —** Carries/borrowed per §7 (incl. **the critical-notes selector reuse — B5**; KV cap-after-escape). NEW: table pair; `pok` category + 3 tools v1; builder + touch-points; metadata allowlist renderer. Registration: `TOOL_REQUIRED_AGENTS` none; `VOCABULARY_VERSION` 6→7; artifact 60→61; ordinal-free (privileged ordinal 19). **B7: PG registration = models imported in `daemon/manager.py`'s pre-create_all block (~:928-987, `# register before create_all`; create_all is the PG source-of-truth `:129/:931/:942/:956/:980`); same-commit re-pins: `tests/unit/metrics/test_metrics_vocabulary_amendment.py` + `tests/unit/tools/test_tool_category_artifact.py`.** **B4 grant-sweep integrity (normative):** watcher excluded (empty allow = inherit-all, `_tool_registry.py:326-340` — NEVER append); `"nest-report"` stays LAST — insert `"pok"` before it; pin test `tests/unit/test_anomaly_report_grant_pins.py`; sweep = 40 registered, 39 non-empty allows.

### Seam rulings

**A-17 —** (i) NEW block; (ii) per-block cap sufficient, metadata companion (allowlist-shaped); (iii) once-per-instance v1 + query freshness, flag on success only; (iv) guidelines phase-2 tier.

### Leader adjudications

**A-18 —** v2 stand; v3 ordering/fence/archive; v3.1 drag bound; v3.2 char keystone + F3; **v3.3 leader disposition (A-35).**

### Migration

**A-19 —** W-3 Option (a) first-class llmproxy project row — **dev now; LIVE in Phase 1.5 (B2)**. Payload re-slugged (B3): `pipeline-01/02/03` + **`deploy-01-daoduc-backup` / `deploy-02-ensem-dev-primary` (atomic contrast family)** + 3 unordered; pin = `pipeline-03-deploy-order-daoduc-backup-first` (44c, the bound-setting slug). All 8 slugs re-checked against `^([a-z][a-z0-9]*)-(\d{2})-(.+)$` — pipeline ✓, deploy ✓, others unordered (the old `^(.*)` regex would have mis-parsed `model-catalogs-19-…`; fixed). Bodies 54–93c. **Dev proof ≠ live truth: the walkthrough is true in dev after Phase 1; live only after Phase 1.5 step 3.**

### Deliberately NOT built

**A-20 —** FE dashboards; watch/alert; cross-project search; KPI phases.

**A-21–A-25 (retired in v3):** round-1 ruling IDs — content folded into A-5 (writers/scope), A-9 (authorization/archive), A-16 (carries/registration), and arch §4.9 (degradation); originals preserved in `discharge-round-1.md`. Gap kept for citation stability — round-1 register references (A-21/A-22/A-23/A-24/A-25) resolve here.

### Round-2 forced choices

**A-26 — Ordering: slug families + atomic adjacent rendering.** `<family>-<NN>-<slug>`; **regex `^([a-z][a-z0-9]*)-(\d{2})-(.+)$` (tightened per B3 — family = one hyphen-free token; the old `^(.*)` form mis-parsed hyphenated slugs)**; atomic admission, slug-order adjacency, never split; query chains in order.

**A-27 — Archive verb in v1.** `pok_curate(archived=True)` frees the slot; row readable.

**A-28 — `pok_query` pinned-BYPASS (revised per B5).** Pinned rows always return (threshold bypass, mirroring blueprint core 1.0); threshold applies to non-pinned only. (The v3 "pinned score bonus" is superseded by the bypass.)

**A-29 — Content fence: two layers.** Write-time typed rejection + render-time escape/flatten. Fenced-verbatim.

**A-30 — KV rejection on true grounds + layer rows.** shared_meta_kv (§2.10), prompt-memory (§2.13), DB-skill tier (§2.9).

**A-31 — Once-flag on success only.**

**A-32 — Precision corrections.** References-injected; blueprint prompt-doc; memoir census; carrier phrasing; 54–93c; infra 3+deny; 7-facts superset.

### FINAL FOLD (council round 3) — C1 + W5 + advisories

**A-33 — C1 option (A) family-carries-to-pin** (member-pin semantics preserved; (C) rejected — contradicts the dogfood's own member pin, adds indirection, same bound needed). Rows bound PIN(2)×FAM(3)=6≤8. **W5:** escaped-body budget (`len(escape_for_context_block(body)) ≤ 240`). Advisories (i)–(iii) adopted (archived re-upsert forward-only; "leading `#` at body start"; memoir census 4 occurrences / 3 files in `daemon/repositories/memoir/`).

### MECHANICAL FOLD (controller re-check) — F1 + F3 + advisories

**A-34 — F1 Option A** (slug 44 / entry 332 / block 2,800 — **block value superseded by A-35-B1's 2,900 wrapper-inclusive**). **F3 exclusion×drag ruled** (excluded rows never render; survivors drag contiguously; pinned-member exclusion cancels the drag; zero survivors → nothing). F4/F5/F6 folded; F7 skipped (sealed history); F8 moot.

### LEADER DISPOSITION (approver cap reached) — B1–B8

**A-35 — leader decisions, implemented without re-litigation:**
- **B1 — DEFINE THE LHS.** `POK_BLOCK_MAX_CHARS` measures the **FULL rendered block INCLUDING the `_make_context_message` wrapper**: `CONTEXT_PREFIX` 17 (`[SYSTEM CONTEXT: `) + title 27 + `CONTEXT_SUFFIX` 3 (`]\n\n`) = **47c** (`context_messages.py:75-76/:181-182`; in-file wrapper-cost precedent `_SNAPSHOT_DIGEST_WRAPPER_TOKENS` `:205-230`). Content 2,764 + 47 = **2,811 → `POK_BLOCK_MAX_CHARS = 2,900`** (89c slack); pinned 2,138 + 47 = **2,185 ≤ 2,900**. No "proven" claim without the wrapper.
- **B2 — LIVE SEEDING step named.** Phase 1 = dev dogfood proof. **Phase 1.5 — live seeding** (owner leader/devops; gate: after the next promote carries the schema): (a) create the llmproxy project row on the LIVE install; (b) seed the 8 slugs via `pok_upsert` against prod through the tool lane; (c) verify with a fresh leader spawn on the live daemon. **Stated plainly: until Phase 1.5 runs, the walkthrough is TRUE IN DEV ONLY — the honest boundary, not a claim of done.**
- **B3 — atomic contrast family.** `deploy-01-daoduc-backup` + `deploy-02-ensem-dev-primary` = ONE family → half-delivery structurally impossible. All 8 slugs re-checked against the tightened family regex; `model-catalogs-19-…` correctly unordered (old regex mis-parse fixed).
- **B4 — grant-sweep integrity (normative).** (1) `agents/watcher/meta.json` empty allow = inherit-all (`_tool_registry.py:326-340`) — NEVER append; exclude watcher (it inherits `pok`); (2) `"nest-report"` remains LAST — insert `"pok"` BEFORE it; (3) sweep = 40 registered / 39 non-empty allows; pin test `tests/unit/test_anomaly_report_grant_pins.py` is the tripwire.
- **B5 — retrieval concrete.** (a) pinned rows BYPASS the threshold; (b) the invented "0.4·BM25+0.6·cos ≥ 0.25" is DELETED (exists in no code) — reuse `critical_notes_selector.py` + `critical_notes_selection_orchestrator.py` (configurable fusion weights/threshold); (c) AC3 asserts ALL 8 slugs retrievable by their own natural query.
- **B6+B8 — metadata allowlist in v1 (D2 DECIDED).** Named allowlist renders verbatim; non-allowlisted capped 2000c/key + 16384c block + omitted-count tail; NO new read tool (allowlist is the recovery path; `project_get` for forensic keys, 5 privileged agents, stated honestly); AC6 fails if any allowlisted key is absent/truncated; AC15's dead-pointer requirement replaced. **D2: DECIDED — allowlist for v1; uniform deferred as a phase-2 option.** (A-36.)
- **B7 — PG registration surface named.** Models imported in `daemon/manager.py`'s pre-create_all block (~:928-987; `SQLModel.metadata.create_all` is the PG source-of-truth `:129/:931/:942/:956/:980`). **AC9 asserts PG MATERIALIZATION** (scratch-PG boot shows both tables), not merely two `.sql` files with no FK. Same-commit re-pins: `tests/unit/metrics/test_metrics_vocabulary_amendment.py`, `tests/unit/tools/test_tool_category_artifact.py` (leader-named bare paths resolve here).
- **Notes folded:** §2 row 1 quantifier corrected ("all agents whose instance carries a non-default `project_id`"); economics reconciled on two bases (68.4 % of whole JSON; "82 %" withdrawn as a computed claim; AC12 re-measures); recurrence acknowledged — B5/B6 were iteration-002 notes carried rather than folded and returned as blockers; B7 recurs the round-001 registration class — all three are now folded as decisions above, not restated as residuals.

**A-36 — D2 DECIDED: v1 ships the ALLOWLIST variant** (A-14); the uniform per-key-cap shape is a deferred phase-2 option. A residual presented as open must be genuinely open — this one is closed.

---

## (B) DECISIONS — status

**D1 — PENDING EMPEROR.** Ratify canon A + the new surface. Flip condition unchanged.
**D2 — DECIDED (leader, v3.3): v1 = ALLOWLIST variant** (A-14/A-36); uniform cap = phase-2 option.
**D3 — PENDING (user un-park go; phase 2; wi-d2abe4f7).**
**D4 — PENDING (second-model corroboration post-D7).**
**D5 — PENDING (critical-notes hygiene lane; cap already 200).**
**P-1 — implementation precondition:** dev llmproxy project row (Phase 1); **Phase 1.5** = live row + live seeding (leader/devops-owned, gated on the next promote carrying the schema).

---

## (C) Boundaries

- **C-1** Markdown only; no code/migrations/commits. **C-2** Fenced dev only; prod not probed (AC12 gates the live measurement; live seeding is Phase 1.5, leader/devops-owned). **C-3** No genre migration.
