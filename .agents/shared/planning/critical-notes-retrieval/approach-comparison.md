# Approach Comparison — Critical-Notes Retrieval & Maintenance Redesign

> **v1 → v2 note (2026-09-15):** this document is the archival record of the v1 competitive fan-out. **Decisions are now LOCKED — Option B adopted (user ruling D1); full ADRs in `decisions.md`; implementing spec in `architecture-recommendation.md` (v2).** One wording correction applied in this revision: the C2 "≤4-semaphore" reference now reads as a *proposed design-time mitigation*, which is what it was — no such concurrency cap exists as importable machinery today.

Date: 2026-09-15
Branch: `feature/critical-notes-retrieval` (base = latest @ 9db17e7d)
Analyst instances: `88aa0a13` (A, data-flow-design) · `942801ca` (B, data-flow-design) · `8ce1f94c` (C, data-flow-design) · `e895a936` (D-foundation, structural-design)
Aggregator: Architect controller.

---

## Question

Which retrieval mechanism replaces load-everything injection of `critical_notes` into the first-turn `[SYSTEM CONTEXT: Related Project]` block — and which tier/schema/lifecycle foundation does it sit on — without breaking the three-bucket compaction contract, the strict-equality write path (2026-09-10 hijack incident), or leader-only writes?

## Options analyzed (one worker each, same skill, different approaches)

| Option | Mechanism (one line) | Worker |
|---|---|---|
| **A — Heuristic only** | Pinned core + token-overlap selection (`context_injection.py` matcher shape), zero new runtime deps | A `88aa0a13` |
| **B — BM25/embedding fusion** | Pinned core + compute-only staged ranking: BM25 prefilter → cosine re-rank → fusion 0.4/0.6 @ 0.30; NO per-turn LLM | B `942801ca` |
| **C1 — Pure quick-LLM** | Cheap model (`model_keywords` tier) reads full note manifest + query → JSON id array (`keyword_extraction.py` recipe) | C `8ce1f94c` |
| **C2 — Staged hybrid** | C-family representative: BM25 → embed re-rank → quick-LLM selects among top-20 shortlist; trigger embeddings minted at write | C `8ce1f94c` |
| **D — Foundation** (orthogonal, not a competitor) | Tier model + additive schema + supersession/staleness lifecycle + bounded reference + budget split | D `e895a936` |

## Five-axis comparison

Risk axis is inverted for reading: **Low risk = good**. Ratings are the workers' self-assessments, cross-checked by the aggregator against their evidence sections.

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Aggregator verdict |
|---|---|---|---|---|---|---|
| A: heuristic | **Low** — one pure function + one column; reuses `_tokenize_query`/`_match_score` | **Med** — linear in note count, no corpus-side signal; degrades past ~100 notes | **High** — pure stdlib, no deps | **Med** — heuristic MISS is structural; own worked example scores 0 (query "merge 94513d55 worker pool" vs note vocab "restart-pending activation") | **Low** — <1ms, $0, 0 new DB reads | Adopted as the **degradation floor**, not the primary. Its own verdict concedes the live-store failure shape. |
| B: fusion | **Med** — 1 new side table + 1 selection service + backfill cmd; reuses `_bm25_score`, `_do_embed_call`, BlueprintMatcher weights | **High** — BM25 O(N) trivial ≤200 notes; cosine O(K=10); query embedding cached per instance | **High** — mirrors `skill_embeddings` pattern; clean 5-rung degradation ladder | **Low** — deterministic compute; no confident-wrong-LLM class; mis-pick ceiling = relevance noise bounded by char cap | **Low** — ~$0.00001/first-turn; one 150–300ms embed call per instance (cached); backfill ≈15s one-time | **RECOMMENDED — ADOPTED (user ruling D1, 2026-09-15).** Dominant axes: Risk (Low vs C2 Med) and latency (+0.15–0.3s vs +1.5–3.5s) in a system whose known pain is dispatch latency under child fan-out saturation. |
| C1: pure quick-LLM | **Low(-Med)** | **Med** — prompt grows linearly (~4.3k tok @50, ~15k @200 notes) | **Med** | **Med-High** — distractor-exposed manifest is exactly the generic-vocabulary class that caused the 2026-09-10 incident (as a ranking nuisance, not a write hazard) | **Low-Med** — $0.0007–0.0024/first-turn + 1–3s latency (15s cap) | **Not adopted as a standalone.** Survives as C2's embedded fallback organ (worker C's own framing). |
| C2: staged hybrid | **Med(-High)** — 3 stages + side table + mint path; 3 seams must track their originals | **High** — LLM prompt fixed ~2.2k tok at any store size | **Med** | **Med** — 🔴 confident WRONG selection hoisted forever (bounded by critical floor outside LLM authority + cap 12 + telemetry) | **Low** in $ ($0.0004/first-turn, $0.0005/note mint) but **latency-heavy**: +1.5–3.5s typical / +15s cap per instance first turn | **DEFERRED — Phase 3 plug-in (confirmed by D1).** C2 = B + stage 3; building B first with the selection-service seam makes C2 a one-seam addition behind a reserved config knob after soak. Under fan-out saturation, the bounded-concurrency cap *proposed* in worker C's design (a mitigation to be built, not existing machinery) would shed the LLM stage exactly when traffic peaks — reinforcing deferral. |
| D: foundation | **Med** — 2 migrations + 5 columns + 2 tool methods + derived-state fn | **High** — worst-case injected budget capped ~2.5k tokens regardless of store growth | **Med** | **Med** — all transitions leader-explicit; no silent mutation; 🔴-free at substrate level | **Low-Med** — one-time migration + leader-side backfill confirm | **ADOPTED (with the reconciled correction: pinned-only core per D2).** All three retrieval mechanisms plug into it unchanged. |

## Why B over C2 (the contested call — upheld by user ruling D1)

1. **Risk axis:** B's selection is deterministic compute — worst case is a *less relevant* tail, bounded by the 12k char cap and recoverable via `project_cn_list`. C2's worst case is a **confident wrong pick frozen into a hoisted-forever, non-compactable message** for the instance's entire life (worker C's own 🔴). Mitigations bound it but cannot eliminate it.
2. **Latency axis (the live-system evidence):** this codebase just bumped `WORKER_POOL_SIZE 4→5` over pool-saturation incidents (31-descendant mission fan-out) and has open head-of-line-blocking work. Adding 1.5–3.5s *typical* to every instance's first turn — children dominate instance counts — taxes exactly the known pain point. B's 150–300ms one-time cached embed is two orders cheaper.
3. **C2's advantage is real but deferrable:** the LLM genuinely reads task-shaped dispatch prompts better than cosine (worker C, honest point). But C2 ⊃ B: BM25→embed is C2's stages 1–2 verbatim. Building B with a strategy seam means Phase-3 C2 is additive, flag-gated, and reversible — not a redesign.
4. **B's failure floor is A:** if embeddings are chronically down, B degrades to BM25-only, then to priority floor — which is A's mechanism class with better vocabulary handling. The ladder never blocks dispatch (coverage at the floor is narrower than render-all; see recommendation §4.6 for the corrected ladder-relative statement).

## Cross-worker convergence (adopted as invariants — 4/4 or 3/4 independent agreement)

1. **First-turn-only selection, frozen for session lifetime** (A, B, C, D all) — preserves `project_injected` one-shot semantics; revive reuses the checkpointed block.
2. **Explicit leader-curated core tier always loads** (all) — pinning is a human/leader-agent judgment call; auto-curation on the write path is the 2026-09-10 hijack class and is rejected.
3. **Priority floor beneath whatever ranking** (A top-3, B top-2, C critical-floor) — reconciled: critical-priority ranking boost + top-2 floor (recommendation §4.2).
4. **Count + fetch-hint line pointing at `project_cn_list`** whenever notes are dropped (all).
5. **Single combined `context_kind=project` block** (D recommends; A/B/C all render into it) — no second context-kind enum value; three-bucket contract untouched.
6. **Always-on activation with config-layer tuning** (updated per D4 — the v1 kill-switch convention item was superseded by the user's flag-policy ruling; boot state line remains as INFO visibility).
7. **Retrieval-side similarity NEVER writes/merges/supersedes** — every write, pin, supersede, archive remains leader-explicit through the tool layer with strict-equality collision detection (all four, explicit).

## Divergences reconciled by the aggregator (v1; all now LOCKED — see `decisions.md`)

| Divergence | Workers | Resolved decision |
|---|---|---|
| Kill-switch names (3 different) | A / B / C / D | Superseded by D4: always-on, config knobs only, no env flag |
| Budgets: A K=5/12k · B N=3+floor2/8k · D core≤8+tail≤6 | A / B / D | D1: core ≤8 · tail ≤6 · section cap 12,000 chars · reference ≤500 |
| Floor count: A top-3 · B top-2 · C critical-cap-8 | A / B / C | Critical-priority ranking boost + top-2 priority floor |
| Tier derivation: D's prose "suggest-only" vs D's formula auto-loading criticals | D (intra-report) | D2: pinned-only core; criticals = suggestions + boost |
| Selection latency appetite | B (0.3s) vs C (1.5–3.5s) | D1: B ships; C2 deferred behind reserved config knob (Phase 3) |

## Worker-flagged unverified items carried into implementation verification

- B: tail-block merge timing into the persistent first-turn message (`_make_context_message` seam, `context_messages.py:85-110`); child instances' embedding-cache independence; `_bm25_score` signature/IDF state before import-reuse.
- C: live priority distribution (critical count — affects floor sizing); p95 latency of the quick model under fleet load; quick-model pricing (mini-tier assumed); leader-only write exclusivity re-grep across all metas.
- D: SQLite `DROP COLUMN` ≥3.35 feature-detect on legacy dev machines; live critical-row count across projects.
- A: exact `_STOP_WORDS` size (74 claimed, not enumerated).
