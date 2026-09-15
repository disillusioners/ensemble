# Decisions — Critical-Notes Retrieval & Maintenance

Feature: `.agents/shared/planning/critical-notes-retrieval/` · Branch `feature/critical-notes-retrieval`
Status legend: **LOCKED** = user ruling, final · **LOCKED-L** = leader ruling, binding · **ADOPTED** = architect pick under delegated authority, accepted.
Companion: `architecture-recommendation.md` (v2.1) implements all of the below; `approach-comparison.md` is the archival v1 analysis.

---

## D1 — Retrieval direction: Option B (staged compute fusion) — LOCKED

- **Date:** 2026-09-15 · **Owner:** user
- **Decision:** Pinned core tier (≤8, leader-curated, always loads) + BM25/embedding-fusion tail selection: BM25 prefilter top-10 → cosine re-rank → fusion `0.4/0.6` @ threshold `0.30` → top-6 tail, top-2 priority floor, 12,000-char notes-section budget, count+fetch-hint line. First-turn selection, frozen for the session. C2 (quick-LLM stage) stays deferred to Phase 3 behind a **reserved config knob** (not an env flag — see D4), post-soak.
- **Rationale:** Matches the aggregator recommendation — dominant axes Risk (deterministic compute; worst case is bounded relevance noise, not a confident wrong pick hoisted forever) and dispatch latency under child fan-out (+150–300ms cached once per instance vs +1.5–3.5s for the LLM stage). C2 ⊃ B, so deferral is a one-seam addition, not a redesign.
- **Consequences:** Heuristic matcher class survives as the degradation floor. Phase-3 activation requires the guard set (id-intersection, floor outside LLM authority, cap, ~15s timeout, bounded-concurrency design) documented in recommendation §8.

## D2 — Core definition: pinned-only — LOCKED

- **Date:** 2026-09-15 · **Owner:** user
- **Decision:** Core tier = `pinned=TRUE` rows only. `priority=critical` yields a ranking boost + a write-time pin *suggestion* surfaced to the leader — never auto-load into the always-injected set.
- **Rationale:** Bounds the always-load budget by explicit curation; critical-count growth cannot silently re-inflate the hoisted block. Resolves the intra-report tension in the v1 foundation analysis (suggest-only prose vs auto-load formula) in favor of suggest-only.
- **Consequences:** Core cap 8 enforced by REJECT-naming-candidates on the pin action (reject-don't-evict philosophy). The leader prompt guide (D3) teaches the discipline.

## D3 — Curation ownership: agent-managed + concise prompt-guide workstream — LOCKED

- **Date:** 2026-09-15 · **Owner:** user *(amended 2026-09-15 v2.1: reference-form clause corrected to the guide's Convention v2 after approver iteration-001 rejection)*
- **Decision:** Agents manage critical notes; there is **no user-in-chat curation round**. The leader (sole `critical_notes` tool-holder today) curates pins/supersessions during normal operation. NEW workstream in the plan (ships in Phase 1 so curation can begin as soon as pinning exists): a **concise** critical-notes management section titled **Critical Notes Management** at canonical home `agents/leader/tools_note.md` (the leader's tool-guidance surface), authored per `docs/agent-prompt-writing-guide.md` (first-person agent POV, no system internals, one canonical home). Cross-references inside the section follow the guide's **Convention v2**: same-agent `See <Section Name>` · cross-agent `See <agent>'s <Section Name>` — bare file refs and the `file.md → Section` arrow form are both **forbidden** (repo-wide closure-grep gate, `tests/unit/tools/test_prompt_section_reference_integrity.py`; guide lines 91–108, read directly).
- **Content the section must teach (tight — tool-layer guidance bloat is part of the problem):** pin discipline (core ≤8; core = always-relevant contracts/traps, NOT incident narration); supersede-don't-re-add; keep `reference` ≤500; refresh `last_reviewed_at` when re-affirming; propose archive for activated restart-pending/stale notes.
- **Rationale:** The user demanded agent-owned maintenance with minimal prompt surface; the leader's tool-guidance surface owns tool-by-tool usage guidance per the guide's file-roles table (precedent: System Maintenance Delegation section).
- **Consequences:** Initial ~40-note tiering happens via the LLM-assisted proposal surfaced to the leader in-band, confirmed through the normal tools during ordinary operation. Former open decision "backfill-session ownership" is RESOLVED by this ruling.

## D4 — Flag policy: always-on, config knobs only, no new env flag — LOCKED

- **Date:** 2026-09-15 · **Owner:** user (resolves reviewer #14)
- **Decision:** The tiered loading + lifecycle feature is **always-on** once shipped. No new `ENSEMBLE_*` env kill-switch. Tuning surface = `config.yaml` fields + pydantic booleans/ints with explicit `_resolve_*` helpers in `load_config` (the pydantic-settings init-kwarg > env inversion trap still applies — mirror the `_resolve_compaction_model` pattern). Boot state line remains as an **INFO log** (state visibility, not a kill-switch). **Rollback story = redeploy the previous build** — stated explicitly in the rollout section.
- **Rationale:** User ruling; consistent with the config-layer (not env-layer) tuning convention for this feature class.
- **Consequences:** The v1 `ENSEMBLE_CRITICAL_NOTES_TIERED` kill-switch is withdrawn; knob enumeration in recommendation §4.7 (reviewer #18). Phase-3 C2 knob is a reserved `critical_notes.llm_select` config field, default false until Phase 3.

## R19 — Render re-sorting scoped to the injected block only — LOCKED-L

- **Date:** 2026-09-15 · **Owner:** leader
- **Decision:** Sorting changes apply to the **injected block only**: pinned entries priority-sorted (critical→high→medium, recency within tier), then tail entries score-ordered (fusion desc). `project_cn_list` output order stays `created_at` DESC (tool surface unchanged). Superseded strike-through and staleness marks are in scope **as maintenance features** — they render in the list/housekeeping surfaces, keeping the injected block clean.
- **Rationale:** Leader ruling; avoids changing an existing tool contract while the injection block is the actual budget surface.

## R21 — Phase-2 entry gate: superseded-filter at external list_critical_notes surfaces — LOCKED-L

- **Date:** 2026-09-15 · **Owner:** leader (review-driven, reviewer MAJOR finding #4)
- **Decision:** Phase 2 STARTS by adding a superseded-row filter at the 3 external surfaces that pass `list_critical_notes` through unfiltered (`daemon/services/context_injection.py:536-545`, `daemon/tools/external_opencode.py:492-493`, `daemon/routers/projects.py:103-104`); single `include_superseded=False`-style filter helper applied at all three sites. Implemented at Phase-2 start, not retroactively in Phase 1.

## R20 — Collision scope excludes superseded rows — LOCKED-L

- **Date:** 2026-09-15 · **Owner:** leader
- **Decision:** `_find_near_duplicate_entry` scans **active rows only** (`superseded_by_id IS NULL`). Superseded rows never appear in a duplicate-reject message.
- **Rationale:** Naming invisible rows in a reject is a real defect — a leader re-adding knowledge that exists only as a superseded row would be told "duplicate" by a row it cannot see in normal flows.
- **Consequences:** A verbatim re-add after supersede inserts fresh (acceptable: supersede was an explicit leader act; re-adding is an implicit un-supersede, visible in the list). Strict normalized-equality semantics otherwise untouched.

## R25 — Message-id scheme: option (a), stable `project:{instance_id}` — ADOPTED

- **Date:** 2026-09-15 · **Owner:** architect (delegated pick; reviewer #25)
- **Decision:** The injected project-context HumanMessage carries a **stable construction-time id `project:{instance_id}`**. Implementation surface: plumbing `instance_id` into `build_project_context_message` (`daemon/services/context_messages.py:146-211`; first-turn call site `:1599`) so the builder mints the id at construction time.
- **Rationale:** The codebase's Message-id invariant is foundational — id-less persisted HumanMessages silently break FE merge ordering (`persistence.py:527-528` falls back to the moving checkpoint-commit timestamp) and cause `MessageTapSlot` metadata drops. A per-instance deterministic id is revive-safe (same instance → same id → stable merge), unique per instance (no cross-turn supersede collisions under first-turn-frozen semantics), and costs one plumbing parameter at a call site that already holds the instance id. Option (b) — documenting the accumulate-on-revive baseline — would ratify a known wart instead of closing it.
- **Consequences:** Whether `_stable_id_for("project", …)` already stamps an id is a verification question (recommendation §9), not the implementation surface; if present, formalize the mint to the explicit per-instance scheme.
