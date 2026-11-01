# Phase 2: Search Fusion + Precedence + Project Binding

## Objective

Fuse the file corpus into `SkillSearchService.search` at the corpus-assembly seam (`skill_search_service.py:709`, post-ownership-filter, pre-tokenization): source tagging on every result, the P4 shadow pass upstream of stage 1 AND stage 3, the defensive post-stage-3 dedupe, the dedicated file-corpus budget, combined-cap semantics, and the full P3 project-binding invariant set (leak, no-context invisibility, degraded parity).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | `SkillSearchService` gains OPTIONAL `file_source=None` constructor param (absent ⇒ byte-identical current behavior); no other signature change | P1-9 | Existing service tests green unchanged with file_source=None |
| 2 | Corpus assembly inside `_bm25_prefilter` after the existing overlay + ownership filter, before tokenize: `file_skills = await file_source.corpus_for(project_id, root)` (index-backed + stat-gate per S4 — NOT "fresh reads per search"; SC 13's 0-re-scans-per-search invariant holds); **ORDER (approver P2, 2026-10-31; aligns with A7 and the acceptance): append → FULL-corpus shadow (pre-budget) → budget (50; overflow by tier/name-ordinal, overflow counted) → tokenize the merged list (zero scoring-code changes).** **P3 clarification (approver P3, 2026-10-31):** "no DIRECTORY re-walk per search (index-backed, stat-gated); the body read at search time is REQUIRED and never cached" — consistent with phase1 task 9 ("corpus objects carry FRESH disk reads") and the A16 metadata-only cache (the cache holds metadata, never body bytes); the apparent tension resolves under this wording. The shadow set is computed from the FULL file corpus BEFORE budget truncation; the budget is applied only to non-shadowing file skills (preferred variant) — see task 5 / A7. **A7 ORDER AMENDMENT (architect A7, MUST, CRITICAL, 2026-10-31): the shadow set is computed from the FULL file corpus BEFORE budget truncation** — preferred variant is MANDATORY: shadowing names are EXEMPT from the budget (cap applies to non-shadowing file skills; availability preserved, P4 holds). **The "acceptable variant" (full-corpus shadow ⇒ both hidden under overflow) is EXPLICITLY FORBIDDEN — planner ruling N7 (2026-10-31, approver N7): it hides the file skill itself and breaks P4 availability; do not implement it.** (S5 overflow advisory applies to NON-shadowing overflow only.) **A8 GUARD AMENDMENT (architect A8, MUST, CRITICAL, 2026-10-31): the fusion-seam call is wrapped in a LOCAL try/except** — an unguarded escape propagates out of `_bm25_prefilter` ⇒ `search` raises ⇒ the tool returns the user-visible `"ERROR: skill_search failed"` string (`skill_tools.py:167`), violating the plan's own never-fails-a-search principle. Guard: `except` ⇒ WARNING + `[]` + `last_error` — search proceeds DB-only. **S3 (advisory, folded): dedicated bounded executor for file reads (mirror the v0.27.14 `db_offload` idiom) or a pinned concurrency bound — PROMOTED TO MUST if the Phase-0 cold-scan measurement exceeds 50 ms** (default-executor contention with DB `to_thread` calls is the routed R-3 class) | P1-6, P1-9 | Fusion functional test: canon query returns file results in the existing `{"injected", "low_match"}` shape; BM25 normalization inherited (one corpus, one scorer — no parallel scorer exists); A7 order pinned (append → FULL-corpus shadow → budget → tokenize); A8 guard pinned for modes M3/M7/M8 (root unreadable mid-search, file-only mode broken, corpus raises) — phase4-plan.md Appendix A (M0–M11): search returns DB results, never the ERROR string, never 500 |
| 3 | Source tagging: every injected entry gains `source` (+ `address`, `shadows` where applicable); every low_match entry gains `source` (+ name/score/description parity, FR-P2-6); additive keys only | 2 | Result-shape test asserts tags on BOTH lists; consumers-that-ignore-unknown-keys tolerance pinned |
| 4 | Defensive post-stage-3 dedupe: if a DB twin somehow reaches injected/low_match alongside its file shadow, drop the DB twin, keep the file one | 2 | Synthetic drift test (bypass the corpus shadow pass) proves the belt-and-suspenders fires |
| 5 | Combined caps: `max_results` = combined cap across sources; `low_match` cap 3 on the combined set; bm25 top_k 10 / llm_select 5 / `max_inject_skills` 2 untouched; file budget overflow surfaces in counters. **A7 pin (architect A7, MUST, 2026-10-31): >50 file skills including one DB twin ⇒ the twin is absent from injected AND low_match — under the MANDATED budget-exempt variant** (shadowing names exempt from the budget — the N7-forbidden full-corpus-shadow variant is NOT testable: building it is forbidden; see decisions.md N7); budget order proven: shadow set computed from the FULL file corpus BEFORE truncation | 2,3 | Cap tests: combined caps hold; single-source behavior = that source only, no padding (FR contract §4.1); the A7 >50-skills pin green under the mandated budget-exempt variant (no forbidden variant is built or tested); P4 holds at the budget boundary |
| 6 | No-context invisibility (FR-P2-4/FR-P3-3): no resolvable project/root ⇒ file corpus contributes nothing (DB-only); load-side fail-closed behavior tested in Phase 3 | 2,5 | Search with project_id=None returns DB-only; DEBUG log, no error |
| 7 | Cross-project leak pin (FR-P3-4, security-grade): seed project A canon + project B context (AND a caller-supplied conflicting path/project_id attempt) | 2 | Zero project-A names under B's context in ALL cases; mutation-verified in Phase 5 |
| 8 | Shadowing e2e pin (FR-P4-1..6): DB skill `X` + file skill `X` same project; assert file-only result + `shadows` populated + DB twin absent from injected AND low_match; stage-3 mock selecting the shadowed name resolves to the file object | 2,4 | Pin green; the 3 legacy DB copies (`9e79ccad`/`fd1e01dc`/`76e3e1a7a`) shadow-retired against the Phase-0 census (FR-P4-5) |
| 9 | Degraded parity pins (FR-P2-5): embeddings unavailable ⇒ file skills surface via BM25 with stage-2 neutral 0.0 (byte-parity with `:829-835`); LLM stage failure ⇒ `_degraded_select` (`:1040-1075`) includes file skills. **S4 wording (architect advisory, 2026-10-31): the pin asserts a file skill SURFACES in top-5 and documents rank-6-10 exclusion as BY-DESIGN uniform competition** (BM25/LLM caps apply uniformly to both sources) | 2,3 | Both degraded paths green with file skills present; ordering = BM25 order preserved; file-in-top-5 assertion green with the rank-exclusion documented |
| 10 | **REST-INVISIBILITY pin (architect A6 + leader ruling D1, MUST, 2026-10-31 — SUPERSEDES the planned REST-parity test; requirements NFR-COMPAT-3 annotated superseded in v1):** `POST /api/skills/search` (no instance context) contributes **ZERO file skills** in v1 — caller-supplied `project_id` is passed straight into search (`routers/skills.py:1032-1056`, consumed `:1054` — corrected at fold, iteration 003: `body.project_id` is the :1054 argument) with no server-side project binding, so file visibility there would be a cross-project body read (P3 violation). REST list stays DB-only via the `_LIST_SKILL_FIELDS` allowlist strip (`skill_store_service.py:90`, projection `:516`) — S2. A server-side REST binding (auth principal → project) is the v2 path (leader ruling D1 parked item) | 3 | REST test: arbitrary caller `project_id` ⇒ zero file entries in the response (asserted invisibility pin); REST list contains no `pf:` rows; agent-lane fusion unaffected (total fusion preserved on the agent lane — P2 holds) |

## Coupling

- **Tight with:** Phase 1 (the `corpus_for` contract + `ProjectFileSkill` field set are frozen inputs — any drift updates Phase-1 pins in the same commit), Phase 4 (the `file_source` param is what manager wiring injects).
- **Loose with:** Phase 3 (fusion results feed the injection-path tests; the load seam itself is independent).
- **Independent of:** Phase 0.

## Blast Radius

- **TOUCHED:** `daemon/services/skill_search_service.py` (constructor param + corpus-assembly block at the `:709` seam + shadow pass + tagging + post-stage-3 dedupe); test packs (new fusion/leak/shadow/degraded/REST pins).
- **NOT touched:** scoring/tokenization code (`:720-723` onward — zero changes), stage-2 code (`:763-850`), stage-3 selection code (`:856-1034`), `skill_injection_service.py`, `skill_tools.py`, `manager.py`, `config.py`, `readiness.py`.
- **Core seam hit:** **N** (`skill_search_service.py` is not on the mechanical core-seam list; the commission-level Tier 2 comes from manager.py in Phase 4).

## Gate Tier

**Tier 1** — blast-radius scoped dual-side (≤30 min HARD; recipe `.agents/tester/skills/blast-radius-leg/`), with real-PG legs for the fusion pins (embeddings table involved).

## Dependencies

Phase 1 (hard — corpus contract + service). Phase 3 interplay tests run after Phase 3 lands (ordered sequencing keeps it simple).

## Exit / Acceptance Criteria

- Fusion live and pinned: tags on every entry; shadow e2e green; leak pin green (including conflicting-supply attempts); degraded parity green; combined caps green; **A7 pre-budget shadow pin green (mandated budget-exempt variant — the N7-forbidden variant is neither built nor tested)**; **A8 seam guards green (search never surfaces the ERROR string)**; **REST-invisibility pin green (A6/leader D1 — supersedes the original REST-parity criterion; NFR-COMPAT-3 annotated superseded in v1)**.
- `file_source=None` regression: ALL pre-existing service/REST skill tests green unchanged (NFR-COMPAT-1 — same results, same scores, DB-only behavior preserved).
- Blast radius respected: only the declared file + test packs changed.


## Rollback note (approver P7, 2026-10-31)
Until the enable knob lands in Phase 4, staging rollback for this phase is **GIT-REVERT-ONLY** — there is no runtime disable for the file-source surface in Phases 2-3; reverting the phase commit restores DB-only behavior.
## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Score-scale mixing (BM25 vs cosine across sources) | High (incomparable rankings) | ELIMINATED by seam choice (tech §C): one corpus, one scorer; asserted by a no-parallel-scorer review check |
| Shadow pass placed after tokenization (wrong order) | High (stage-3 collision window reopens) | Order pinned: append → FULL-corpus shadow (pre-budget) → budget → tokenize; mutation leg plants a same-name pair and asserts single stage-3 candidate |
| File corpus crowds DB skills out of fetch (or vice versa) | Medium | Dedicated budget 50; caps untouched; overflow counter test |
| `asyncio.to_thread` omission on corpus read | High (event-loop stall class) | Thread-identity pin in the concurrency pack (house rule) |
| Additive-key intolerance in some consumer | Medium | Shape pins on tool JSON + REST; consumers verified tolerant (research findings §B) |
| Shadow pass drops a DB skill whose name differs only by case | Low (intended — case-insensitive match per FR-P4-1) | Pinned explicitly as the spec behavior |
