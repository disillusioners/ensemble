# Memoir Store v1 — Implementation Notes (Phase 7 close-out)

Consolidated implementation record for the `feature/ari-memoir-store` lane.
Consolidates `phase-notes.md`, `phase-notes-p3.md`, `phase-notes-p4p6.md`,
`phase-notes-p5.md`. Per-wave files are retained.

## Phase map (commits, waves 1–4 landed at HEAD 830060619)

| Wave | Commit(s) | Scope |
|---|---|---|
| W1 | schema + seed + persona lane | 4 tables, seed persona, persona state machine |
| W2 | repository + hybrid retrieval | RRF, budget, write purity |
| W3 | embedding worker + api lifespan + readyz advisory | worker, degradation |
| W4 | wake briefing + tools + THE CEREMONY + inner_soul redirect | 5 tools, 18-code vocab, ari redirect |
| W5 (this) | phase 7 close-out | packs, matrix driver, guardrail sweep, semantic recall wiring |

## In-mandate adjudications (phase 7)

- ITEM 1 (semantic recall gap): the service now resolves a default query
  embedder internally (shared EMBEDDING_* family / memoir config), so
  production recall runs hybrid (BM25 ∥ cosine, RRF k=60) instead of
  lexical-only. The tool module stays PURE (zero embedder symbols; the
  wave-4 purity pin stays GREEN). The embed call is bounded
  (MEMOIR_QUERY_EMBED_TIMEOUT_S = 5.0) and degradable (lexical-only on
  any exception/timeout/config-missing). RRF arithmetic, comparator,
  budget and footer bytes are unchanged.
- ITEM 2 (queue predicate literal): `get_pending_embeddings` now predicates
  on `embedding IS NULL AND embedded_at IS NULL` (plan D3.2), one line;
  behaviour unchanged (columns written atomically).

## Phase 7 deliverables

- PACKS.md registration block appended (all lane packs).
- Consolidated mutation-matrix driver: `test/packs/memoir_mutation_matrix_test.py`
  (orchestrates the 10 lane packs; digest recorded in
  `mutation-matrix-digest.json`).
- Guardrail sweep (D7.9): no touched `daemon/loader.py`, no agents meta.json
  except ari, no pyproject/uv.lock change, no plugins/frontend, no new
  external dependency, no sync/federation surface (Constella door intact).
- FC9 record (T7.8): baseline 48,170 → 50,266; growth 2,096 = 4.35 %;
  both bounds PASS. Caveats: scratch HOME lacked project-experience.md
  (baseline slightly understated); no MCP names bound. Recorded per
  wave 4's measurement.

## Tester disclosure list (ITEM 5 — tester owns these)

- W8 frozen-binary boot-abort induction (phase 1's mechanical proof exists:
  `test_a_failing_memoir_statement_rolls_back_the_whole_transaction`).
- Authoritative real-PG gate corpus.
- Boot smoke: frozen-binary COPY, ENSEMBLE_SELF_ENV=sandbox, per-variable PG
  scrub plus EMBEDDING_BASE_URL/API_KEY/MODEL scrub,
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned, scratch PG major-version
  parity with live, worker task census in BOTH knob states (ON: tasks present;
  OFF with ENSEMBLE_MEMOIR_ENABLED=false: ZERO tasks pinned explicitly),
  state.json fence PRE==MID==POST, current-> untouched, SIGTERM-only teardown.
- Tier-2 whole-tree: mechanical trigger daemon/manager.py + daemon/api.py are
  core seams; base 1f6898ae2.
- inner_soul family: 16 pre-existing reds (base-proven, byte-identical FAILED
  sets both legs).
- project_knowledge now redirects ari to the memoir guidance (recorded
  semantic consequence of D6.1's branch shape + AC3's 5/5).

## Leader decision — KEEP the four memoir tables + seeded persona row in ensemble_prod

Appendix (phase-notes.md §4.5, dated 2026-11-05, leader-attributed):

The prod-DB incident (occ#8 env-poison family) created four memoir tables
plus a seeded persona row in ensemble_prod. Decision: KEEP them; do NOT drop.
Rationale: they are inert on the live binary (board-tables/notify_audit
inert-on-rollback class) and the promote creates the same tables idempotently
via create_all, so prod already matches the promoted end-state; a DROP is a
destructive write for zero gain that would diverge prod from that end-state.

Root fix (boot-epoch/heartbeat-gated refusal of discard_on_startup) is
escalated and routed OUT of this lane — do not touch boot-recovery code.
