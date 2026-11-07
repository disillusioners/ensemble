# POK — Trust-Tier Mechanism Addendum (D3 un-park)

**Companion to:** `architecture-recommendation.md` v3.4 + `decisions.md` (A-2…A-19, ER-1) in this directory; closes planner gap **G-1** (`implementation-readiness.md:58`). **D3 status: DECIDED-mechanism-attached** (decision ratified by the Emperor 2026-11-07; this addendum supplies the mechanism; one non-blocking role question carried in §T-8).
**Method:** architect-planned; single worker analysis (`structural-design`, instance 750e5833); DESIGN ONLY — no code.
**Parked-material census (honest):** NOT in-repo — no guidelines planning dir exists; `wi-d2abe4f7` appears only inside this design set; the preserved "Guidelines system" draft text lives in project-history event `8ffd3082-a1bb-4273-a5ea-e9ac87f5ceca` (dev daemon, unreachable at authoring time; documented not-in-repo/not-in-RAG by `plane-pm-issue-tools/phase5-plan.md:37-41`); the board is ari-owned. Designed from the POK design's own vocabulary: parked guidelines = **"Dev-curated immutable reference"** (arch §2 row 11).

## T-1 Tier model — one additive column

- `source_tier TEXT NOT NULL DEFAULT 'agent'` — CHECK `source_tier IN ('agent','curated')`, mirrored in the additive `.sql` AND the SQLModel table (house rule, readiness §2.1). Named `source_tier`, NOT `tier`: the admission vocabulary already owns "tier" (pinned tier / ranked actives, §4.5:107).
- Default `'agent'` ⇒ every Phase-1 call signature and every post-dogfood row is byte-unchanged; zero behavior change without opt-in.
- **Orthogonality (adjudicated — no existing field carries it):** `provenance` = epistemic status (A-10) ⊥ `pinned` = delivery placement (§4.7) ⊥ `audience` = view membership (ER-1) ⊥ `status` = lifecycle ⊥ `source_tier` = write/mutation AUTHORITY class — five independent axes on one row. Deriving tier from `created_by_agent` is FORBIDDEN (A-5: attribution ≠ authorization, decisions:26).
- **Set at write:** curator (leader/PM — the ONLY curator concept in v3.4, A-9) may pass `source_tier='curated'` on `pok_upsert`; any writer may omit (→ `'agent'`); non-curator passing `'curated'` → typed refusal `pok_tier_requires_curator`. **Changed later:** `pok_curate(source_tier=…)` curator-only, both directions — extends the EXISTING A-9 pok_curate row; no new matrix row.

## T-2 Injection semantics — precedence band, SHARED budget

- Per-view admission ladder becomes: **audience filter FIRST** (§4.5.1) → pinned (≤2/project, family drag) → **CURATED band (cap 2 per view, no drag)** → ranked actives → F5 rider — ALL inside the SHARED 8-entry / 2,900c per-view budget. **No separate slice.**
- **Bounds hold WITHOUT re-derivation:** the §4.5:110 bound is a function of admitted count × entry ceiling, not of which band supplied the entries. Named worst case: pins 2 × family 3 = 6 rows + curated 2 = 8 → header 40 + 8×333 + tail 60 = 2,764 + wrapper 47 = **2,811 ≤ 2,900** — identical to the standing derivation.
- Audience-invisible curated row = excluded row FOR THAT VIEW (F3 extension, §4.5:108); no curated drag, no cross-view leakage.
- **Rejected — ranking boost (Option A):** a tier multiplier is a POK-invented weight (B5/A-12 prohibit; the invented 0.4/0.6/0.25 was DELETED), and decay×access can crowd an immutable reference OUT of a competitive view — defeating §2 row 11. Band placement mirrors the existing pins mechanism shape (precedence + cap) and is the minimal addition.

## T-3 Mutation semantics — lattice extension

- **Supersede-of-curated → curator-only**, typed refusal `pok_curated_requires_curator` (naming mirrors `pok_pinned_requires_curator`); guard predicate becomes `target.pinned OR target.source_tier='curated'`.
- **Tier-flip is a `pok_curate` parameter, never a pok_upsert verb** — authority reclassification is curation, the established in-place pattern (pinned/disputed/archived/audience flips, A-9).
- **Append-with-supersede-only intact (A-7):** content supersede mints a NEW row in the same txn; **omitted `source_tier` on supersede INHERITS the target's tier** (explicit curator param overrides) — prevents accidental demotion/promotion on content refresh.
- ≤2-call capture preserved: curator captures a guideline in ONE call (`pok_upsert(…, source_tier='curated')`); optional second `pok_curate` to pin (§11 slug-4 pattern).

## T-4 pok_query — universal read untouched

- `pok_query` returns ALL entries (agent + curated) to ANY project-linked agent. The invariant generalizes verbatim from ER-1.4/C-4: **the audience field AND the source-tier field affect ACTIVE injection only; they never filter retrieval.**
- `[CURATED]` renders in the QUERY lineage surface (lineage always rendered, A-10; query output unbounded ⇒ zero arithmetic impact). **No in-block marker in v1** (conservative default): the band position signals status in-block and the 332c entry ceiling stays exact; an in-block marker may be added ONLY if the 92c-overhead slack audit passes at implementation time.

## T-5 Seeding + migration + board contract

- **Seeding = tool lane (confirmed house pattern** — readiness §1.3 "via `pok_upsert` (NOT SQL backfill)"; plan §4.5; Phase 1.5 precedent). Once the draft text is retrievable (`project_history_search` after the dev daemon returns, or operator-hands-verbatim — phase5-plan.md:41 forms a/b), a CURATOR seeds per slug with `source_tier='curated'`; bodies obey the 240-escaped budget ⇒ leader-led segmentation into slugs (§11 bodies run 54–93c); segmentation is curation judgment, NOT paraphrase (the phase5 verbatim rule carries).
- **Schema delta (Phase 2 lands post-Phase-1-promote ⇒ live PG already has the table):** SQLModel field + `_ensure_postgres_columns` entry + ONE additive `.sql` with a unique prefix (v0.25.4 precedent; checksummed Phase-1 migrations are never amended). Prove `ALTER TABLE … ADD COLUMN … CHECK` on a throwaway SQLite before landing (fresh-SQLite breaker lesson).
- **Board contract — the lane hands ari this note CONTENT for `wi-d2abe4f7`** (ari/leader dual-gate; the lane never writes the board): disposition `D3-folded-into-POK`; mechanism one-liner "POK entries carry `source_tier ∈ agent|curated`; curated = the parked guidelines (dev-curated immutable reference); write/supersede/flip curator-only"; the finalization commit sha; seeding status "draft preserved verbatim in history event `8ffd3082…`; tool-lane seeding pending retrieval — no SQL backfill"; **annotate-and-close** with a pointer to this addendum (board = pointer, never a duplicate).

## T-6 Acceptance criteria (Phase 2 gate legs)

| AC | Gate |
|---|---|
| AC-T1 | CHECK `source_tier ∈ agent|curated` mirrored in `.sql` AND SQLModel; default `'agent'`; Phase-1 dogfood rows read byte-unchanged |
| AC-T2 | Typed refusals fire: non-curator upsert-curated → `pok_tier_requires_curator`; non-curator supersede-of-curated → `pok_curated_requires_curator`; non-curator `pok_curate(source_tier=…)` refused |
| AC-T3 | Lattice legs (≥4): leader CAN upsert/supersede/flip curated; PM same; developer CANNOT, both directions |
| AC-T4 | Per-view bounds with curated present: named worst 6 pinned-family + 2 curated = 8 rows → 2,811 ≤ 2,900; curated cap 2/view; audience-invisible curated = excluded-for-view |
| AC-T5 | Universal read across tiers: developer `pok_query` returns curated entries; `[CURATED]` in lineage |
| AC-T6 | Seeding legs: curator `pok_upsert(source_tier='curated')` per slug, verbatim-derived, 240-budget boundary leg; ZERO guideline content in any `.sql` (no-backfill grep leg) |
| AC-T7 | Board evidence: ari's `wi-d2abe4f7` update records disposition/sha/mechanism/seeding-status (read-back = ari's typed echo) |

## T-7 Registration facts — parameters-only (hard constraint honored)

Zero new tools, zero new categories, `pok_query` untouched. `pok_upsert` +1 optional param, `pok_curate` +1 optional param ⇒ **no `VOCABULARY_VERSION` bump, no tool-categories artifact regen, no `TOOL_REQUIRED_AGENTS` entry, B4 40-agent sweep untouched** (A-16 tracks categories/tools, not params). Only registration-adjacent delta: the one additive `.sql` + `_ensure_postgres_columns` entry.

## T-8 Risks + the one Emperor question

- 🔴 Curated-band starvation (unbounded corpus per view) → capped 2/view; universal read keeps everything reachable.
- 🟡 In-block `[CURATED]` marker vs the 332c ceiling → query-only default (§T-4).
- 🟡 SQLite `ADD COLUMN … CHECK` authoring → prove on throwaway SQLite before landing.
- 🟡 **"Dev-curated" role reading (the one Emperor question):** v3.4's ONLY curator concept is leader/PM (A-9); this addendum maps dev-curated → leader/PM. Confirm-or-amend: if "dev-curated" intended a developer-agent curator set, that is a one-row A-9 matrix change — additive, zero schema impact; flagged at the Phase-2 gate, non-blocking.
- 🟢 Tier-flip history: covered by the A-9 recording surface (typed echo + `ens_system_log`).
