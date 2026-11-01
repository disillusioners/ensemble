# Plan Overview: Project Skill Source Loader

Date: 2026-10-31
Author: planner[v2] via plan-creation worker
Status: Ready for Review
Worktree: `/home/nea/ensemble-src-wt-project-skill-loader` (branch `feature/project-skill-loader`, base `b56444244` = v0.28.1 tip)
Authoritative inputs: `requirements.md` (35 FRs across P1-P7, 18 NFRs, 5 contract specs, traceability matrix) + `technical-analysis.md` (Q1-Q8 decisive recommendations, sections A-H). Where they disagree, technical-analysis.md wins (it postdates and reconciled); every discrepancy is recorded in `decisions.md` Part 4.
**Precedence (reviewer S-e, 2026-10-31):** decisions.md Part 1 (the user-ruled P1–P7 binding principles) OVERRIDES `technical-analysis.md` on any conflict, regardless of the TA-postdates rule above — the principles are user-ruled and hard; the TA postdates rule applies only to requirements.md vs TA. Any apparent TA/principles conflict is a bug in the TA and must be escalated, not silently resolved toward TA.

---

## Objective

File-backed project skills (`.agents/*/skills/` under a project's tree) become a first-class SOURCE in the skill system — searchable, loadable, project-scoped — while git remains the distribution and evolution channel. Testable completion sentence: an agent working in a project with a canon can `skill_search` a topic, receive the file skill tagged `source: "project-file"`, and `skill_view`/`load_skill` it by its `pf:` address with the body read from disk — with zero body bytes ever persisted in the DB.

## Scope

### In Scope
- New `ProjectSkillSourceService` (`daemon/services/project_skill_source/`): canonical project resolver (Q1), lazy per-root index + stat/digest invalidation (Q2), per-skill fail-loud validation incl. the dual-leg legacy exclusion (Q7), address/pseudo-id minting (Q5), fire-and-forget embedding refresh into the DEDICATED `project_skill_embeddings` table (no FK, architect A1) under `pf-<sha16>` pseudo-ids (Q3) — reuse of the existing `skill_embeddings` table was structurally broken on PG (FK wall at `models.py:594-601`) and is REJECTED.
- New `project_skill_files` derived-metadata table: own `.sql` in `daemon/migrations/versions/` + `manager.py::_ensure_postgres_columns` mirror entry (house rule: two artifacts, one migration, zero exceptions).
- Search fusion at the corpus-assembly seam (`skill_search_service.py:709`): source tagging, shadow pass (P4), defensive post-stage-3 dedupe, dedicated file-corpus budget (50), combined-cap semantics.
- Load/inject path: STEP 0 file-first interception in `inject_explicit_skill` BEFORE `clone_on_miss_sync`; `pf:` address awareness in `skill_view`/`skill_list`; `skill_fix` refusal; `skill_feedback` metrics-only; `## Loads` metadata surfacing (Q6 v1 scope).
- Config/observability: `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED` (default ON) + `ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS` (dev-only); `detail.skill_sources` readiness advisory block that NEVER flips readiness; manager.py wiring (unconditional construction, optional injection, degraded-mode per-source tool guards).
- Gates per `.agents/tester/rules/ensure.md`: Tier 2 whole-tree dual-side (mechanical — `manager.py` core seam), deterministic recipe load, mutation-verified pins (≥4 legs per load-bearing claim), real-PG legs, net-new=0.
- Light Phase 0: four read-only measurements (tech §G) recorded into this planning dir.
- Rollout through STAGING (branch → gates → merge → bump → tag → `stage.sh`). NO promote by this lane.

### Out of Scope (with reasons)
- **Promote / live flip** — user's 3-factor nonce ceremony owns it (standing house rule).
- **Recursive `loads[]` auto-injection + `assets/` serving** — v2 (Q6: no consumer beyond the doctrine's mandated name-resolution; recursion adds cycle/budget classes).
- **Spawn-time `work_dir` metadata plumbing** — v2 (PA-A3: owner is the spawn path, mirroring `project_id` auto-inherit at `daemon/tools/instance.py:3256-3259`; needs spawn-surface changes with their own gate weight).
- **Physical deactivation of the 3 legacy DB skills** — out-of-lane operator action (PA-A2: shadow-only v1).
- **Hot-reload kill-switch** — boot-time env in v1; hot (DB-settings) switch deferred v2 (D-Q8 deviation note).
- **Explicit file-skill injection in evolution-off deployments** (un-gating `SkillInjectionService`) — v2 (core-seam blast radius, no demonstrated need; search + `skill_view` serve file skills in that mode).
- **FE work** — non-goal; additive JSON keys are FE-inert.
- **Any skill-evolution machinery change** — P7 hard boundary; the evolution lane is untouched.

## Requirements Summary

Full detail: `requirements.md` §3-§6 (35 FRs grouped P1-P7; 18 NFRs across performance/safety/compatibility/idempotency; 5 contract specs §4.1-§4.5: search-fusion shape, project-binding, load-path, validation, observability; traceability matrix §10). The seven binding principles (P1-P7, user-ruled 2026-10-31) and canonical Q1-Q8 (+Q8b) are resolved in `decisions.md`. Key contract shapes the implementation must honor:

- **Search-fusion contract (§4.1):** additive `source: "db" | "project-file"` on every injected/low_match entry; `max_results` = combined cap; `low_match` cap 3 combined; shadowed DB results HIDDEN; file skills without embeddings get stage-2 neutral 0.0 (BM25 order preserved).
- **Project-binding contract (§4.2):** resolution chain per D-Q1; no context ⇒ invisible + fail-closed load; cross-project leak = security-grade test criterion; non-repo ⇒ graceful DB-only.
- **Load-path contract (§4.3):** `skill_view`, `load_skill` chain, and auto-injection all read bodies from disk; intercept BEFORE `clone_on_miss_sync` (`skill_clone_service.py:143-223`); `skill_view` 8000-char cap + hint; injection full-body; missing file at load ⇒ fail closed with clear error.
- **Validation contract (§4.4):** per-skill fail-loud; `name == dir name`; unknown `schema_version` ⇒ skip; anti-conflation test against `.agents/project-skills/bump-version` is MANDATORY and standalone.
- **Observability contract (§4.5):** advisory detail only; a broken file source MUST NOT flip any readiness component; no new readiness component.

## Phases

| Phase | Name | Objective | Blast Radius | Gate Tier | Dependencies |
|-------|------|-----------|--------------|-----------|--------------|
| 0 | Evidence + measurements (light) | Bound the cost claims + pre-answer the P4 retirement census: canon census sweep, cold-scan/stat-gate cost, embedding-regen cost, live DB census of the shadow set, `.agents/` inventory | NONE (read-only; scratch scripts in `/tmp` only) | Tier 0 (no regression leg) | none |
| 1 | Index + validation core | Resolver, lazy index + invalidation, validation + legacy exclusion, addresses/pseudo-ids, embedding refresh dispatch, `project_skill_files` table + `.sql`, P1 sentinel invariant | NEW: `daemon/services/project_skill_source/{__init__,service,model,manifest}.py`, `daemon/repositories/project_skill_file/{__init__,models,repository}.py`, 1 migration `.sql`, unit/integration test packs. Core seam: **N** | Tier 1 (scoped dual-side; real-PG legs required — index is DB-backed) | Phase 0 |
| 2 | Search fusion + precedence + project binding | Fused corpus at `:709`, source tags, shadow pass, post-stage-3 dedupe, file budget, combined caps, leak + shadow + degraded-parity pins | TOUCHED: `daemon/services/skill_search_service.py`, test packs. Core seam: **N** | Tier 1 (scoped dual-side; real-PG) | Phase 1 |
| 3 | Load/inject path + tool surface | STEP 0 interception, `pf:` addressing across tools, P7 refusals/acceptances, `## Loads`, size/truncation behavior, clone-on-miss non-mutation pin | TOUCHED: `daemon/services/skill_injection_service.py`, `daemon/tools/skill_tools.py`, test packs. Core seam: **N** | Tier 1 (scoped dual-side) | Phase 1 (Phase 2 for fused-search interplay tests) |
| 4 | Config/observability + boot wiring | Knob + dev-roots, `detail.skill_sources` advisory, manager wiring (unconditional construction, optional injection, `_ensure_postgres_columns` mirror), degraded-mode per-source tool guards, knob-matrix + readiness-no-flip + soft-fail pins | TOUCHED: `daemon/manager.py` (**core seam — Y**), `daemon/config.py`, `daemon/services/readiness.py`, **`daemon/api.py` (A10 readiness snapshot param)**, `daemon/tools/skill_tools.py`, test packs | **Tier 2 (mechanical — manager.py)** | Phases 1-3 |
| 5 | Commission gates | Tier 2 whole-tree dual-side base-proven diff, mutation matrix, real-PG legs, migration-delta + rollback-safe adjudication inputs, staging | TOUCHED: test packs + `.agents/tester/` evidence only. Core seam: covered by the commission diff (manager.py) | **Tier 2 (whole-tree, ≤90 min advisory)** | Phases 0-4 |

Phasing-shape adjustments vs the caller's suggestion (with reasons):
1. The `_ensure_postgres_columns` mirror entry is grouped in **Phase 4** with the rest of the `manager.py` wiring cluster (not Phase 1), so exactly one phase trips the mechanical core-seam escalation mid-lane and Phase 1 stays Tier 1. Release-level invariant intact: both artifacts (`.sql` + mirror) land before staging — the single staged release ships them together. Intra-branch note: between Phase 1 and Phase 4 commits, a PRE-EXISTING PG DB lacks the table; nothing reads it in the boot path until Phase 4, and test DBs are fresh scratch PGs where the `.sql`/model applies — acceptable, recorded in phase1 risks.
2. Phase 0 is capped at the four named read-only measurements + inventory — kept light per tech §G verdict (architecture does not depend on the outcomes; they bound cost claims).
3. Phase 5 explicitly carries the mandatory deterministic recipe load (`whole-tree-base-proven-diff` ← loads `failed-digest-equality-gate`) — this lane is itself the first real consumer of the Q6 v1 name-resolution capability once Phase 2/3 land; until then the existing deterministic-path mandate covers it.

## Coupling Map

|  | P0 | P1 | P2 | P3 | P4 | P5 |
|---|---|---|---|---|---|---|
| **P0** | — | informs (cost budgets, census) | informs (leak-test fixtures) | independent | independent | consumes (evidence baselines) |
| **P1** | informs | — | **tight** (ProjectFileSkill + resolver contract) | **tight** (address/pseudo-id + lookup_explicit contract) | **tight** (service construction + table) | tight (subject of gates) |
| **P2** | informs | tight | — | loose (same fusion results feed injection tests) | tight (file_source param wiring) | tight |
| **P3** | independent | tight | loose | — | tight (injection service receives file_source) | tight |
| **P4** | independent | tight | tight | tight | — | tight |
| **P5** | consumes | tight | tight | tight | tight | — |

Tight couplings to police: the `ProjectFileSkill` duck-typed field set (P1) is consumed by P2 tokenization/stage-3 AND P3 formatter — any field addition must update all three consumers' pins in the same phase; the pseudo-id mint function (P1) is the identity spine for `skill_embeddings`, `skill_usage_records`, and index PK — single definition, imported everywhere.

## Top Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Cross-project leak (P3) — resolver picks wrong scope | High (security-grade: wrong project's skills served) | Low | Single resolver (D-Q1); fail-closed on unresolvable context; seeded-A/search-under-B pin (FR-P3-4) with ≥4 mutation legs; grep-fence test asserting no direct project_id reads outside the resolver. **CLOSED-CLASS AMENDMENTS (architect 2026-10-31): P3 was violated as planned (rung-1 widening + zero containment) — CLOSED by A5 (path containment at index AND every read) + A6 (rung-1 confirm-only; REST file-invisible v1) + A13 (single sanctioned scope reader, both sources, explicit allowlist)** |
| 2 | Stage-3 name-collision blindness (P4) | High (arbitrary twin selected by LLM pick) | Medium | Shadow pass upstream at corpus assembly (D-Q4 point 1) + defensive post-stage-3 dedupe + mutation pin: mock selector picking the shadowed name twice resolves to the file object. **CLOSED-CLASS AMENDMENT (architect A7, 2026-10-31): P4 was breakable as planned under budget overflow — CLOSED by the pre-budget shadow invariant (shadow set from the FULL file corpus BEFORE truncation; shadowing names budget-exempt in the preferred variant)** |
| 3 | Body bytes leak into DB (P1 violation) | High (canon authority lost; user ruling violated) | Low | Sentinel test across ALL skill tables after index+search+load (FR-P1-5); `manifest_json` metadata-only by construction; review gate on any new column |
| 4 | `clone_on_miss_sync` mutation leaks a file skill into DB | High (P1/P4 violation via DB write) | Medium | STEP 0 ordering before `:479` + mutation pin: plant same-name bank template, assert `skills` row count unchanged after explicit inject |
| 5 | Sync file I/O on the event loop | High (2026-10-26/30 incident class; loop stall) | Medium | ALL corpus/body reads via `asyncio.to_thread` (risk G-11); thread-identity pins in the concurrency pack |
| 6 | Embedding refresh adds search-path latency on git pull | Medium | Medium | Fire-and-forget async refresh; stale embeddings only perturb stage 2; `embedded_at`/`last_error` visible (D-Q3) |
| 7 | Legacy `.agents/project-skills/bump-version` ingested | Medium (wrong-schema rows poison index) | Low | Dual-leg exclusion (path shape + `schema_version==1`); mandatory standalone anti-conflation test (FR-VAL-1) |
| 8 | Knob matrix regression (OFF/OFF must be today byte-identical) | Medium (silent behavior change) | Low | Four-combination matrix pinned; OFF ⇒ `file_source=None` ⇒ unchanged code paths |
| 9 | Migration surface (new tables) blocks rollback | Medium | Low | All additive `CREATE TABLE IF NOT EXISTS`, NO FK (A1/A2), board/notify_audit adjudication class; explicit `rollback_safe` semantic adjudication at stage (standing rule — no reflex value); old binary has no models for the tables |
| 10 | Caps silently truncate one source (200/10/5/2) | Medium | Low | **REFRAMED by architect A7 (2026-10-31): the residual cap risk is P4-side, not availability-side — the shadow set is computed pre-budget so overflow can no longer surface a stale DB twin; preferred variant makes shadowing names budget-exempt (availability preserved); overflow surfaced in `detail.skill_sources` with the S5 shadowing/ordinary split** |
| 11 | Tier-2 gate budget breach (whole-tree on a large tree) | Medium (gate overrun) | Medium | Mandatory recipe load (measured-rate runners baked into recipe bodies); node-cap + measured-rate projection guard BEFORE legs; pre-exclusion registry with named compensating pins |
| 12 | FR-P7-3/Q3 embeddings tension misread as evolution coupling | Low (plan-level) | Low | **RESOLVED: architect F1 CONFIRMED the reconciliation sound (trigger-query generation imports zero evolution machinery — verified); the enforcement holes are closed by A14 (evolution tools + REST fix `pf:` refusals)** |
| 13 | `skill_embeddings` FK wall silently degrades file skills to BM25-only on PG (architect A1, MUST-CRITICAL, 2026-10-31) | High (had it shipped: silent permanent BM25-only + retry churn on fresh installs AND remote replicas) | **CLOSED** (found pre-implementation by the architect; storage vehicle superseded) | Dedicated `project_skill_embeddings` table (no FK) + the Phase-1 real-PG FK-wall regression pin documenting WHY (phase1 task 11) |

Known risks (from evidence): worktree canon drift — v1 indexes `main_directory`; worktree-only edits invisible until merged/checked out there (documented, accepted — same as every repo-committed resource). Assumed risks: v1 canon schema stability (requirements assumption 1); consumer tolerance of additive `source` key (assumption 2 — verified for both consumers, pinned in tests anyway).

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Fusion live | Search from an instance in the ensemble project for a gate-relevant query | ≥1 result tagged `source: "project-file"` in injected or low_match; shape matches §4.1 |
| 2 | P1 sentinel (FR-P1-5) | Sentinel string in a body; full index+search+load cycle; grep all skill tables' text columns (`skills`, `skill_bank`, `skill_embeddings`, `skill_triggers`, `skill_lineage`, `skill_usage_records`, `skill_ab_tests`, `project_skill_files`) | 0 rows contain the sentinel; test is mutation-verified |
| 3 | Cross-project leak (FR-P3-4) | Seed project A canon; search under project B context | 0 project-A names in results; conflicting caller-supplied path also yields 0 |
| 4 | Shadowing (FR-P4-6) | DB skill `X` + file skill `X`, same project; search | Only the file result (`source:"project-file"`, `shadows` populated); DB twin absent from injected AND low_match |
| 5 | Clone-on-miss non-mutation | Explicit `load_skill` of a file skill with a same-name bank template planted | `skills` row count unchanged; no clone/refresh DB writes (row-count + audit pin) |
| 6 | P7 boundary | `skill_fix` on `pf:` address; `skill_feedback` on `pf:` address; **A14 surfaces: `skill_analyze`/`skill_evolve`/`skill_get_metrics` + REST `POST /{skill_id}/fix`** | fix ⇒ fail-closed refusal with git-PR guidance, dispatcher never invoked (pin); feedback ⇒ usage record written per the D2 fork, NO lineage row, NO evolution dispatch (pin); **P7 pin scope = "tools + REST fix" explicitly (architect A14, 2026-10-31)** |
| 7 | Degraded parity (FR-P2-5) | Stage-2 embeddings unavailable; stage-3 LLM failure injected | File skills still surface via BM25 (0.0 neutral); `_degraded_select` includes file skills |
| 8 | Anti-conflation (FR-VAL-1) | Seed canon + `.agents/project-skills/`; scan | Only canon skills indexed; `bump-version` absent; no crash (standalone test) |
| 9 | No-context invisibility (FR-P3-3/FR-P2-4) | Search/load with no resolvable project | DB-only results; `pf:` load fails closed (explicit error, no fallback to another project) |
| 10 | Knob matrix | Construct with (ON,present)/(ON,absent)/(OFF,present)/(OFF,absent) | Full fusion / file-only search+view+list **per the A9 pinned semantics (BM25-only, top_k=max_results, no stage 2/3, `degraded:"file_only"` marker)** / byte-identical search/list-view RESULTS (A11 narrowing — `/readyz` detail differs) / byte-identical search/list/view RESULTS + **zero `pf-` rows in any skill table (A11 OFF-side pin)**; all four cells pinned against the M0-M11 mode table — phase4-plan.md Appendix A (M0–M11) |
| 11 | Readiness no-flip (NFR-SAFE-5) | Break the file source (unreadable root) under a live-shaped fixture | `/readyz` all components true; `detail.skill_sources.last_error` populated; no 500 |
| 12 | Soft-fail (NFR-SAFE-4) | Loader disabled + DB stack absent | No crash, no 500; DB-only or empty-DB-file-only behavior per matrix |
| 13 | Performance budgets | NFR-PERF-1..4 measured in tests (scan ≤2 s @ ≤50 skills; search delta ≤50 ms p95; load ≤50 ms p95 @ ≤64 KB; 0 re-scans per search after index) | All within target; `last_scan_ms` consistent with Phase-0 measurement |
| 14 | Idempotency (NFR-IDEM-1..3) | Re-index same project ×2; concurrent first-touch ×N | Identical index (no dupes/drift); concurrent builds coalesce |
| 15 | Migration + rollback posture | Stage-time delta census vs live | **Exactly +2 `.sql` (`create_project_skill_files.sql` + `create_project_skill_embeddings.sql`); +3 if the Phase-1 D2 census gate chooses the usage companion table (`create_project_skill_usage_records.sql`)** — all additive CREATE TABLE IF NOT EXISTS, no FK; explicit rollback_safe adjudication recorded (additive-CREATE-TABLE semantic reasoning); pre-`create_all` model registration verified + mirror entries present as belt |
| 16 | Commission gate | Tier 2 whole-tree dual-side, FAILED-digest diff | Net-new reds = 0 (or adjudicated); ≥4 mutation legs per load-bearing claim (shadow, leak, sentinel, clone-on-miss, refusals, knob matrix, readiness); recipe load deterministic |

## Rollout Posture (through STAGING)

1. Lane work happens on `feature/project-skill-loader` in the standing worktree; per-phase commits; no source edits outside the declared blast radii; planning writes confined to `.agents/shared/planning/project-skill-loader/`.
2. Phases 1-4 gate at their declared tiers (scoped legs), Phase 5 runs the commission gate: **Tier 2 whole-tree dual-side** (mechanical — `daemon/manager.py` in the diff), ≤90 min advisory, mandatory recipe load from `.agents/tester/skills/whole-tree-base-proven-diff/` (which loads `failed-digest-equality-gate`), node-cap + measured-rate projection before legs, pre-exclusion registry honored, FAILED-digest diff with net-new = 0.
3. Merge to latest (house merge discipline), bump version (next free slot adjudicated at stage time per the version-arbitration convention), tag, `stage.sh`.
4. **Stage-time standing rules:** migration delta is expected **exactly +2** (`2026MMDD_HHMMSS_create_project_skill_files.sql` + `2026MMDD_HHMMSS_create_project_skill_embeddings.sql` per architect A1; **+3 if the Phase-1 D2 census gate chooses the usage companion table** — `create_project_skill_usage_records.sql`) — all additive `CREATE TABLE IF NOT EXISTS`, **no FK, no ALTER/DROP** ⇒ explicit `rollback_safe` semantic adjudication REQUIRED (no reflex value): additive CREATE TABLE, zero references in the old tree (grep the old-tree for `project_skill_files`/`project_skill_embeddings` = 0 hits), old binary has no models ⇒ inert post-rollback — the board/notify_audit adjudication class holds. **`known_schema_gen` becomes the lexically-latest new file.** Prompt-artifact baseline asserted clean.
5. **Behavior-change disclosure for ceremony materials (required) — DUAL disclosure per architect A18.2 (2026-10-31):** (a) with the knob default ON, first boot of the new binary in THIS project shadows the 3 legacy DB gate-runner skills (`9e79ccad`/`fd1e01dc`/`76e3e1a7a`) — DESIRED per P4, must be stated, not hidden (Phase-0 census pre-answers the exact diff); **(b) THIS install runs evolution-OFF ⇒ first boot activates file-only search/list/view where tools today say "not yet available"** (the OQ-1 coupling, A9-pinned semantics). **PLUS the REST/FE consequence:** the FE Skills page keeps showing the 3 legacy DB skills as active with no shadow indication — REST list is DB-only per A6/S2 (shadow-only retirement is invisible to REST list).
   - **AMENDED (leader adjudication 2026-11-01 — decisions.md Part 10):** the Phase-5 live-DB census resolved the exact diff: the `regression-ab-partition-run` name is ONE lineage (gen-0 `978ae5cc` inactive → gen-1 `08a4332f` → gen-2 `fd1e01dc`, unresolved A/B pair, identical content) — correct P4 shadow-by-name covers it. The ENUMERATED active shadow set at promote is FOUR ids: `08a4332f` + `fd1e01dc` (regression-ab-partition-run lineage) + `9e79ccad` (whole-tree-base-proven-diff) + `76e3e1a7a` (failed-digest-equality-gate); `blast-radius-leg` has no DB copy. A18.4 census semantics are lineage-aware; staging STOP lifted. Runbook A18.2 carries the same enumeration (`docs/runbooks/project-skill-source.md`).
6. **Promote is the user's 3-factor nonce ceremony — NOT performed by this lane.** This plan covers through STAGING only. No live flips, no nonce consumption, no promote.sh runs.
7. Runtime opt-out without rollback: `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED=false` at the next boot restores **byte-identical search/list/view RESULTS** (architect A11 narrows the OFF claim — `/readyz` detail legitimately differs; the kill-switch is rollback-adjacent, not a substitute for rollback-safety adjudication).

## Non-Goals (v2.0 — verbatim from the commission)

- No DB body copies (P1).
- No `skill_fix`/evolution on file skills (P7).
- No injection default changes (P5).
- No public/global registry share (separate future).
- No FE work beyond trivially necessary.

Plus the deferred register (decisions.md Part 3 + technical-analysis §H): recursive loads injection, asset serving, spawn-time `work_dir` plumbing, evolution-off explicit injection, P4 physical deactivation, hot-reload kill-switch, cross-project/global registry share, FE tag surfacing, usage-driven git-PR suggestions. **NEW parked item — leader ruling D1 (2026-10-31, architect A-amendment A6): auth-principal→project binding for REST file-skill fusion (v2)** — a server-side project binding would let REST `/api/skills/search` fuse file skills; until then REST stays file-invisible in v1 (its own small v2 commission if ever wanted).

## Open Questions (routed to the architect — WITH recommendations)

| # | Question | Recommendation |
|---|----------|----------------|
| OQ-1 | Degraded-mode `skill_search` when the DB stack is absent (TA open question 1): serve file-only in evolution-off deployments, or restrict file skills to DB-stack-present deployments? | **Serve file-only** (per-source tool guards) — Skill-Bank precedent (`manager.py:3951-3956`: bank is standalone infrastructure, not evolution-gated) + the G2 lesson (evolution-off deployments must not be blind to the only authoritative gate recipes). Fallback position if the architect prefers zero new surface: independence claim shrinks to "construction + indexing un-gated"; wiring delta drops the tool-guard changes. |
| OQ-2 | P4 retirement persistence (TA open question 2): physical deactivation of the 3 legacy DB rows at enable-time? | **No — PA-A2: shadow-only v1.** Query-time shadowing is the retirement mechanism (knob-reversible, zero foreign-row writes). Physical deactivation = deliberate out-of-lane operator action, deferred with the v2 register. |
| OQ-3 | `work_dir` metadata convention owner (TA open question 3): who stamps `instance_metadata["work_dir"]`? | **PA-A3: defer to v2;** recommended owner = the spawn path, mirroring `project_id` auto-inherit (`daemon/tools/instance.py:3256-3259`); decision belongs to instance-lifecycle owners. |
| OQ-4 | FR-P7-3 ("no embeddings computed") vs Q3 derived-embedding rows in `skill_embeddings` under `pf-` ids — confirm the reconciliation (search-substrate derived metadata ≠ evolution machinery; trigger-QUERY generation is not trigger-ENGINE registration). | **RESOLVED by the architect (F1, 2026-10-31): reconciliation CONFIRMED sound — trigger-query generation imports zero evolution machinery (verified pure LLM call).** The STORAGE VEHICLE is superseded by A1 (dedicated `project_skill_embeddings` table, no FK — the reuse was structurally broken on PG: FK wall at `models.py:594-601`). Alternative (BM25-only) remains a one-flag fallback. |
| OQ-5 | Kill-switch semantics: requirements §6.3 wants disable "for the NEXT search" without restart; v1 ships a boot-time env knob (OFF ⇒ `file_source=None` from construction). | **Accept boot-time env for v1** (os.environ is process-inherited; a running daemon cannot see .env edits regardless — the requirements phrasing is only achievable via a DB-settings hot surface). Record hot-reload as v2. OFF-at-boot is byte-identical-DB behavior, which satisfies the operator's emergency-off intent. |
| OQ-6 | File-corpus budget value (50 at assembly, TA §C) — confirm before implementation. | **Confirm 50** (5× the current canon; bounded by the ≤64-namespace/≤128-dir scan caps; overflow surfaced in `detail.skill_sources`). Cheap to re-tune later — single constant. |
| OQ-7 | REST flat-row list endpoints (`/api/skills` list surface): TA verified `/search` passthrough (`routers/skills.py:1033-1056`) but flat-row list endpoints gain the `source` key with less evidence. | **RESOLVED by architect A6/S2 + leader ruling D1 (2026-10-31): REST `/api/skills/search` and the REST list stay DB-only in v1** — no file skills surface over REST (no server-side project binding exists; a caller-supplied `project_id` would be a cross-project body read). The real strip mechanism is the `_LIST_SKILL_FIELDS` allowlist (`skill_store_service.py:90`, projection `:516`), not pydantic; merging file rows would feed `pf:` ids into FE-built deactivate/share calls that 404. Server-side REST binding = separate v2 commission (leader ruling D1 parked item). FE keeps showing the 3 legacy DB skills as active with no shadow indication. |

## Research Insights

Consolidated evidence digest with all file:line anchors and the gotcha list lives in `research-findings.md` (this plan cites it throughout). Highest-value anchors: fusion seam `skill_search_service.py:709`; stage-3 collision blindness `:906-912`/`:1001-1012`; clone mutation contract `skill_clone_service.py:143` + call site `skill_injection_service.py:454/:479`; formatter duck-typing `:1019`/`:639`; wiring + bank-independence precedent `manager.py:2222-2276`/`:3951-3956`; readiness advisory precedent `readiness.py:512-531`; embeddings reuse `repositories/skill/models.py:563/:590`; Gate Tiers + mechanical core-seam list `.agents/tester/rules/ensure.md` (manager.py ⇒ Tier 2).

## Assumptions

1. v1 canon `skill.json` schema v1 is stable through this lane (requirements assumption 1; no formal schema doc — validation pins double as the schema record).
2. Search consumers tolerate additive `source` keys (requirements assumption 2; verified for tool JSON + REST passthrough; pinned regardless).
3. The shadow-target DB rows exist in THIS project's DB — **census-dependent (iteration 003 normalization): 3 legacy ids per the commission; a 4th predecessor id `08e67030` appears in the canon's supersedes fields — the Phase-0 census is authoritative** (replaces the assumption with evidence).
4. The embedding provider is configurable in dev; if unavailable, Phase-0 measurement 3 records N/A and the BM25-only fallback path is exercised in tests instead.

---

## FR → Phase/Task Traceability (approver P8, 2026-10-31)

Every FR/NFR ID in `requirements.md` §3–§5 maps to its owning phase/task; a reviewer verifies coverage without re-reading requirements.md. Grouped by principle/contract.

| FR/NFR group | IDs | Owning phase / task |
|---|---|---|
| P1 — files canon | FR-P1-1..3 | Phase 1 (tasks 1, 6, 9) — body read from disk; no DB body; fresh reads |
| P1 — files canon | FR-P1-4 | Phase 1 (tasks 3, 4 — INFRASTRUCTURE: the table/migration exist precisely so nothing else is written) + Phase 3 (task 1); the requirement's ACCEPTANCE is the sentinel/no-clone-write pins (Phase 1 task 10 + Phase 5 task 3d) |
| P1 — files canon | FR-P1-5 | Phase 1 (task 10) + Phase 5 (task 3a) — sentinel grep |
| P2 — search fusion | FR-P2-1..7 | Phase 2 (tasks 1-6, 9) + Phase 4 (task 5 matrix) |
| P3 — project binding | FR-P3-1..2 | Phase 1 (task 5 resolver) + Phase 2 (task 6) |
| P3 — project binding | FR-P3-3..4 | Phase 2 (tasks 6, 7) |
| P3 — project binding | FR-P3-5 | Phase 1 (task 5) + Phase 5 (task 6 fence) |
| P3 — project binding | FR-P3-6 | Phase 1 (task 5) + Phase 2 (task 6) |
| P4 — shadowing | FR-P4-1..4 | Phase 2 (tasks 2, 4, 5, 8) |
| P4 — shadowing | FR-P4-5..6 | Phase 2 (task 8) + Phase 0 (census) + Phase 5 (task 3c) |
| P5 — injection opt-in | FR-P5-1..4 | FR-P5-1 (gate unchanged): Phase 3 (task 12 — parity pin, gate untouched) + non-goals; FR-P5-2 (parity under caps): Phase 3 (task 12); FR-P5-3 (full-body from disk): Phase 3 (task 1 + task 8); FR-P5-4 (pf: load, no clone): Phase 3 (task 1 + task 2 clone-on-miss pin). (N3 tightened per approver iteration 002: the A14 guards own refusals, not P5 parity)

| P6 — cross-agent visibility | FR-P6-1..3 | Phase 1 (task 2 — all-namespace scan) + **Phase 5 (task 5 — P6 CROSS-AGENT VISIBILITY pin; approver B6, 2026-10-31: re-pointed from the erroneous "Phase 2 task 7" credit, which is the FR-P3-4 leak pin)** |
| P7 — git evolution | FR-P7-1 | Phase 3 (task 5) + Phase 4 (task 5) |
| P7 — git evolution | FR-P7-2 | Phase 3 (task 6) + D2 fork (Phase 1 Named Gate) |
| P7 — git evolution | FR-P7-3 | Phase 1 (task 8) + Phase 3 (task 10) |
| P7 — git evolution | FR-P7-4 | Phase 3 (task 5 + FR-P7-4 clause 2) |
| Load-path contract | FR-LP (§4.3) | Phase 3 (tasks 1, 3, 7, 8, 9, 12) |
| Validation contract | FR-VAL (§4.4) | Phase 1 (task 2) + Phase 5 (task 3i) |
| Observability contract | FR-OBS (§4.5) | Phase 4 (tasks 4, 8) |
| Performance NFRs | NFR-PERF-1..4 + NFR-PERF-5 | NFR-PERF-1..4: Phase 0 (tasks 2, 3) + Phase 1 (tasks 6, 7) + Phase 5 (task 7); **NFR-PERF-5 (concurrency): Phase 5 (task 8)** — pointer corrected per iteration 003 |
| Safety NFRs | NFR-SAFE-1..5 | Phase 1 (task 10) + Phase 2 (task 7) + Phase 4 (tasks 6, 7) + Phase 5 (tasks 3a/b/h) |
| Compatibility NFRs | NFR-COMPAT-1..5 | Phase 2 (task 1) + Phase 3 (tasks 4, 10) + Phase 4 (tasks 2, 3) + Phase 5 (task 4) |
| Idempotency NFRs | NFR-IDEM-1..3 | Phase 1 (tasks 6, 7, 11) + Phase 5 (task 5) |
| Config/kill-switch | §6.1–§6.3 | Phase 4 (tasks 1, 2, 5, 7) + Phase 0 (census) |
| Test strategy | §7 | Phase 5 (tasks 1-10) |
| Rollout/rollback | §8 | Phase 5 (task 9) + plan-overview Rollout Posture |

*Traceability added per approver P8 (2026-10-31). Coverage: every FR/NFR ID in requirements.md §3–§5 maps to at least one phase/task; the reviewer verifies coverage without re-reading requirements.md.*