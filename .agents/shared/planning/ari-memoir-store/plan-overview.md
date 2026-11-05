# Memoir Store v1 — Plan Overview (Phase B implementation plan)

**Lane:** `feature/ari-memoir-store` @ worktree `/home/nea/ensemble-src-wt-memoir-store`. Plan written at `0d2f31dde` (Phase A design record; tree content == `1f6898ae2` == origin/latest at Phase A).
**Authority stack:** `decisions.md` §6 — Emperor rulings R1–R4 > blessed `design.md` > this plan > code. This plan stays WITHIN layers 1–2; every §14 flag disposition herein is **PROPOSED — architect adjudicates** (full analysis in `flag-dispositions.md`; draft §4 rows pre-staged there in §16).

---

## 1. Objective

Deliver **Memoir Store v1 — usable memory for Ari** end-to-end: the four-table schema (episodes / persona / persona-versions / empty syntheses), the `memoir` privileged tool category (ari-exactly, 5 tools, two-layer gate, kill-switch), hybrid retrieval (lexical ∥ semantic RRF k=60, importance tie-break, 180-day recency prior, keepsake-exempt), the background embedding worker (never blocking writes, graceful lexical-only degradation), the turn-1 `[SYSTEM CONTEXT: Memoir]` wake briefing (two zones, budget model, once-per-instance), and the ari-only `inner_soul` redirect repair — staged as a release, NO promote (user 3-factor nonce ceremony owns the flip).

## 2. Scope

**In (v1 end-to-end):** everything in `design.md` §1 "What v1 is" + `roadmap.md` v1 table — write/pin/unpin/recall/timeline, keepsake governance (R2, reject-don't-evict), seeded persona (§11 verbatim), wake briefing, leak repair, stars seed, syntheses schema, migration + PG materialization + tsv ensure, embedding worker, tests/gates/boot-smoke, staged release.

**Out (guardrails Phase C cannot smuggle in — from the commission §10 and design §10.3/§13):**
- NO other agents' tool sets; ari-exactly means exactly that.
- NO RAG/knowledge-system changes EXCEPT the ari `inner_soul` redirect branch (§6.2).
- NO loader/system-prompt changes (L1/DD-10 — context block only).
- NO new external deps, NO new services, NO graph DB, NO pgvector (R4/DD-1/DD-17).
- NO federation/sync (R1).
- NO syntheses behavior — schema only (DD-2); NO `memoir_about` (DD-4); NO LLM importance scoring (DD-5); NO star normalization build (DD-19, flag #5 = awareness only); NO episode-body mutability (DD-8, flag #6 partition); NO per-turn reinjection (DD-11); NO v2/v3/v4 work (roadmap: no build authority).
- NO scheduler bare-HUMAN stamp change (L4) — explicitly forbidden even for the briefing.

## 3. Binding-input summary (what this plan treats as specification)

`design.md` (413 lines, blessed 2026-11-04): §3 storage kinds (relational + tsvector full-text + JSONB/Python-cosine vectors); §4 four layers and the `memoir_episodes` column contract; §5 tool surface + three-step registration + two-layer gate + `ENSEMBLE_MEMOIR_ENABLED`; §6 inner_soul repair; §7 retrieval algorithm (RRF k=60, tie-break, recency prior τ=180d, keepsake exemption, recall-only stats); §8 briefing seam + two zones; §10 seams/landmines; §11 persona seed VERBATIM; §14 the 14 flags. `decisions.md`: R1–R4 verbatim; P-1..P-7 protocol; DD-1..DD-20; §4 empty (Phase B fills via architect). `roadmap.md`: v1 builds now; doctrine pressure already folded (syntheses-empty, stars-seed). N1–N5 citations are `[citation needed]` board-only artifacts — planned around, never fabricated, never blocking. **All code anchors this plan cites were re-grep-verified on 2026-11-05** (values recorded in phase files; drift notes where they occur — notably flag #4's inner_soul lead-vs-truth).

## 4. Phases

| # | Name | File | Objective | Depends on | Flags consumed |
|---|------|------|-----------|------------|----------------|
| 0 | Anchor re-verification + scaffolding | phase0-plan.md | Final grep sweep at implementation HEAD; repository/service/tool module skeletons; config block + kill-switch knob module; migration timestamp reservation | — | #13 (knob shape) |
| 1 | Schema + PG materialization + persona seed | phase1-plan.md | One `.sql` (SQLite-valid, additive, IF NOT EXISTS); 4 SQLModel tables + manager registration before create_all; PG tsv+GIN ensure; boot-time seed ensure (§11 verbatim, pin-tested) | P0 | #14, #8 (mirror schema), #6 (column families) |
| 2 | Repository + hybrid retrieval service | phase2-plan.md | Named-writer repository (no general update); RRF k=60 hybrid retrieval (ts_rank_cd ∥ Python cosine, identity-matched); importance tie-break; 180d recency prior, keepsake-exempt; recall-only stats; timeline filter-then-rank; recall budget | P1 | #6, #10, #11, #5 (bounds), #1 (admission) |
| 3 | Embedding worker | phase3-plan.md | Lifespan asyncio task; NULL-embedding queue; attempts/backoff/terminal; lane-down latch; boot catch-up; per-tick counter; kill-switch-gated | P1 | #7, #13 |
| 4 | Tool category + 5 tools + gates + pin ceremony | phase4-plan.md | `daemon/tools/memoir.py` (category #18, 5 tools, two-layer gate, kill-switch typed refusals, budgets, dual pin admission); three-step registration; ari allow entry (implemented in Phase C per commission); ari tools_note doctrine; ONE-commit pin ceremony + re-stamp | P2, P3 | #12, #13, #2, #1, #5, #11 |
| 5 | Wake briefing | phase5-plan.md | `CONTEXT_KIND_MEMOIR` block at the assembly seam; stable id `memoir:{agent_id}`; compaction-hoisted; once-per-instance flag; two-zone renderer + normalized budget model; kill-switch OFF → no block | P2 | #3, #9, #1, #13 |
| 6 | inner_soul ari-only redirect | phase6-plan.md | Branch at the two `_should_redirect_to_rag` call sites (`:716`/`:777`) with factory-captured `agent_id`; memoir redirect text; kill-switch loud notice; non-ari byte-identical | P4 | #4, #13, (DD-13/DD-16) |
| 7 | Tests + gates + boot smoke | phase7-plan.md | Unit packs + mutation matrix; Tier-1 per-phase + Tier-2 lane-final (mechanical: manager.py+api.py are core seams); real-PG legs; degradation + catch-up legs; byte-identity leg; boot smoke per standing conventions | P1–P6 | all (pins) |
| 8 | Staged release | phase8-plan.md | Version slot derived at stage time (W9); rollback-safe adjudication (standing rule); NO promote; staging worktree + provenance sidecars kept | P7 | — |

Sequencing note: P3 and P5 can proceed in parallel after P2 (they share no files); P4 depends on both (tools need the worker's store_embedding seam contract and the service). P6 after P4 (redirect text references the live tools). The plan's critical path is P0→P1→P2→P4→P7→P8.

## 5. Coupling map

| | P1 | P2 | P3 | P4 | P5 | P6 | P7 | P8 |
|---|---|---|---|---|---|---|---|---|
| P1 schema | — | models consumed | NULL-embedding columns | — | — | — | real-PG legs | migration delta |
| P2 repo/service | | — | `store_embedding`/pending scan contract | tools call service | briefing calls renderer | — | packs | — |
| P3 worker | | | — | kill-switch shared | — | — | degradation legs | — |
| P4 tools+category | | | | — | affordance line | redirect target exists | registration pack | release content |
| P5 briefing | | | | | — | — | budget pins | — |
| P6 inner_soul | | | | | | — | byte-identity leg | — |
| `daemon/manager.py` | registration+ensure (P1) + worker wiring is api.py | | | | | | | Tier-2 trigger |
| `daemon/api.py` | — | | worker lifespan task (P3) | | | | | Tier-2 trigger |
| `daemon/services/context_messages.py` | | | | | CONTEXT_KIND + builder (P5) | | | — |
| `daemon/tools/inner_soul.py` | | | | | | two call sites (P6) | | — |
| `_tool_registry.py` + `instance.py` | | | | category entries + extend (P4) | | | | |
| `agents/ari/*` | | | | allow entry + tools_note (P4, Phase C) | | | re-stamp | payload trees |

Hard-coupling hazard called out: P1's manager.py edit and P3's api.py edit are the two Tier-2 core-seam touches — both land early and are pinned in P7's whole-tree leg.

## 6. Risk register (top risks)

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| R1 | **SQLite/PG DDL divergence** — tsvector + ARRAY are PG-only; a naive model breaks SQLite boots (the runner applies the .sql to SQLite; create_all must stay dual-driver) | high | tsv lives in NO model (PG ALTER-ensure, flag #14); `stars` via a TypeDecorator (PG ARRAY(Text) / SQLite JSON — the `JSONBType` pattern at `daemon/repositories/infra/types.py:35`); .sql is SQLite-valid by construction; boot smoke on BOTH drivers (P7) |
| R2 | **Registration invisibility** — decorator-only category addition is silently invisible (C5: "no error, no tool, no clue") | high | three-step seam checklist in P4 + `test_memoir_registration.py` (a)–(j) legs + extends-order pin; ceremony in ONE commit |
| R3 | **Budget arithmetic unsound** → stable-zone truncation (the one invariant that must NEVER break) | high | dual pin admission makes overflow impossible by construction (flag #1); 24k absolute ceiling derived from the token ceilings (flag #3); mutation pins on both |
| R4 | **inner_soul blast radius** — non-ari behavior drifts | high | branch condition is `agent_id == "ari"` only; byte-identity pack leg (pre/post responses identical for a non-ari fixture); DD-16 |
| R5 | **Embedding lane down at runtime** (the known live-proxy defect class) | medium | P3 degradation is the design (lexical-immediate / semantic-soon); lane-down latch + terminal-at-5; writes never block (DD-20) |
| R6 | **Env-poison / boot-smoke incidents** (standing critical notes: sweep kills live procs; `env -u POSTGRES_*` is a no-op) | high | P7 boot smoke pins `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly, per-variable env scrub, `ENSEMBLE_SELF_ENV=sandbox`, binary COPY to scratch, scratch DB, state.json fence PRE==MID==POST |
| R7 | **Event-loop discipline** — DB reads on the loop (the routed incident class) | medium | all memoir DB access behind `asyncio.to_thread` (the snapshot-digest builder precedent at `context_messages.py:358-392`); Tier-2 whole-tree gate covers the class |
| R8 | **Pin-ceremony drift** — a count/exact-set pin missed → false-red gates later | medium | P4 enumerates all 11 pin sites (6 count + 5 exact-set) + the new one, grepped fresh at implementation time; one commit |
| R9 | **Phase C re-derives design** (scope creep into v2) | medium | §2 Out-list is contractual; each phase names its flags; non-goal guardrails repeated in phase files |

## 7. Test strategy

- **Unit packs** per module (repo, retrieval, worker, briefing, tools, knob, redirect) — registered in `.agents/tester/PACKS.md` per house convention.
- **Mutation matrix**: cap rejection (count-bound + budget-bound legs), keepsake exemption, once-flag, agent gate (blank/non-str/scoped), decorator order, extends order, budget clamps (admission + render + recall envelope on the SHIPPED projection), pin-admission arithmetic, seed byte-identity, kill-switch tristate, byte-identity (non-ari inner_soul).
- **Gate tiers** (`.agents/tester/rules/ensure.md` doctrine): Tier-1 blast-radius per phase merge; **Tier-2 whole-tree at lane end — MECHANICAL trigger: the lane touches `daemon/manager.py` (registration + ensure) and `daemon/api.py` (worker lifespan), both on the core-seam list.**
- **Real-PG legs** where behavior is PG-specific: tsvector generated column + GIN + `ts_rank_cd`/`plainto_tsquery`, JSONB embedding round-trip, ARRAY `stars`, CHECK constraints, partial indexes; SQLite legs for the Python-BM25 fallback + migration-runner path.
- **Embedding legs**: worker with mock endpoint (down→attempts+error+latch; up→catch-up; terminal-at-5); boot catch-up (restart with pending NULLs).
- **Boot smoke**: frozen-binary copy to scratch, `ENSEMBLE_SELF_ENV=sandbox`, per-var PG scrub, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` PINNED, throwaway DB, live fence (state.json byte-identical; `current->` symlink untouched), SIGTERM-only teardown.
- **Registration pin pack**: `tests/unit/test_memoir_registration.py` — the notify-shape (a)–(j), 18-flavored.

## 8. Release plan

Stage only — **NO live promote** (user 3-factor nonce ceremony owns the flip). Version slot derived at stage time per house W9 (highest tag + releases/ census; never hardcoded). Rollback-safe adjudication under the standing rule: additive `CREATE TABLE IF NOT EXISTS` ×4; PG tsv ensure is additive `ALTER ADD COLUMN`; zero references in the pre-lane tree ⇒ old binary never touches the tables (the board-tables/notify_audit inert-on-rollback class) ⇒ `ENSEMBLE_ROLLBACK_SAFE=1`, reasoning recorded in stage journal + tag annotation. Staging worktree + BOTH provenance sidecars kept (v0.25.2 gap avoided). Payload: agents tree changes (ari `meta.json` + `tools_note.md`) + daemon binary; FE/plugins untouched.

## 9. Flag-disposition summary (full analysis: flag-dispositions.md)

| # | Short name | Status | One-line disposition |
|---|---|---|---|
| 1 | Pin-cap integer | PROPOSED | 12 count + dual admission (count AND 4,000-token zone budget); reject names the binding constraint |
| 2 | Pin attribution | PROPOSED | Required `actor` Literal(empress/emperor) param + visible `pinned_by` render; turn-provenance + separate-tool rejected |
| 3 | Budget units | PROPOSED | Tokens plan (house estimator); stable zone admission-bounded (2,000+4,000); dynamic 3,000 oldest-first; absolute section cap 24,000 chars; ~12k = nominal |
| 4 | inner_soul anchors | PROPOSED | Re-grep confirms predicate `:316-362` + call sites `:716`/`:777` are the target; P-7 note, no §4 row |
| 5 | Stars normalization | PROPOSED | Store verbatim; v2 rule documented now; input bounds (16×120 chars) = hygiene not normalization |
| 6 | Mutability partition | PROPOSED | Record columns INSERT-once; each operational family exactly ONE named repository writer; no general update method |
| 7 | Embedding worker | PROPOSED | Lifespan asyncio task; NULL=is-queue; 5-attempt terminal; lane-down latch; catch-up by construction; per-tick counter |
| 8 | Persona approval lane | PROPOSED | MIRROR the transition vocabulary in `memoir_persona_versions`; seed born approved `source='seed'`; NO v1 edit path |
| 9 | Once-per-instance | PROPOSED | Stands (DD-11); affordance closing line; fresh per-turn flag read; v2 alarm-refresh inherits L4 constraint |
| 10 | Timeline q? | PROPOSED | Filter-then-rank; lexical-only ranker; no cosine/RRF/recency/keepsake priority; no stats writes |
| 11 | Recall budget | PROPOSED | k≤20 (default 5); 1,200-token per-episode clamp; 6,000-token envelope, ranked-drop + hint footer |
| 12 | Census numbers | PROPOSED | `memoir` = privileged #18 (17→18); 5 tools/4 families; 11 pin sites + new pack in ONE commit + re-stamp |
| 13 | Kill-switch semantics | PROPOSED | OFF = whole personality (tools/briefing/worker off, loud inner_soul notice, data untouched); tristate polarity, unset→config default ON, garbage→fail-closed OFF+WARNING |
| 14 | PG materialization | PROPOSED | .sql = SQLite+audit (no tsvector/ARRAY); PG via manager registration→create_all; tsv+GIN via ALTER-ensure; seed via boot get-or-create |

**14 PROPOSED / 0 OPEN-FOR-ARCHITECT.** 13 earn §4 rows once adjudicated (#4 exempt, P-7); draft row text pre-staged in `flag-dispositions.md` §16.

## 10. Assumptions & open questions

**Assumptions (flagged, not buried):**
- The 5-tool surface is exhaustive for v1 (§5.4); no persona-edit tool (flag #8 disposition) — if the architect rules an in-v1 edit path, P4 gains a task and §5.4 text is superseded surgically.
- `agents/ari/meta.json` allow entry + `tools_note.md` doctrine edit land in Phase C (implementation) as the commission states — planned in P4, executed there.
- The house estimator (`estimate_tokens`, tiktoken cl100k) is THE budget instrument (flag #3); no new tokenizer dependency (R4).
- Embedding model/endpoint come from the shared `EMBEDDING_*` config family (config.py:4374-4376); dimension 1536 is the design contract (§3) — the per-row `embedding_dims` column records reality if the lane ever differs, and identity-mismatched vectors never compare (DD-6).
- N1–N5 board citations stay `[citation needed]`; nothing in this plan blocks on them.

**Open questions for the architect** (all carried WITH proposed dispositions — none blocks planning):
1. Flag #3: accept the 24,000-char absolute ceiling (Option B) or shrink the token ceilings to honor 12k chars (Option A)? Recommended: B.
2. Flag #8 conditional: any in-v1 persona edit path? Default: none.
3. Flag #13: confirm default-ON (unset → config default true) against a strict "unset must not enable" reading. Recommended: default-ON with fail-closed-on-garbage.

## 11. Non-goals guardrails (restated for Phase C)

See §2 Out-list. Phase C acceptance includes a grep-assertion sweep that NONE of the forbidden surfaces were touched: loader.py, RAG tools beyond the inner_soul ari branch, other agents' meta.json, external dependency manifests (pyproject/uv.lock beyond version bump), plugin trees. The guardrails are load-bearing, not decorative.
