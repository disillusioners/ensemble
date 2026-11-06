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

## Tidier fix cycle — 2026-11-05 (49 findings: 8H/18M/23L)

### The referenced tidier report was ABSENT

The commission says "read the tidier report at `.agents/tidier/`". It is
**not there**: `.agents/tidier/notes.md` holds OTHER lanes' tidy passes
(newest 2026-11-03/04), and a whole-tree plus main-checkout search finds
no memoir tidier record. Every item was therefore executed from the
leader's enumerated rulings (item numbers + defect descriptions +
anchors), located in the tree by the described defect. Items the ruling
list does not enumerate could not be transcribed — they are recorded
BELOW by label only, with NO invented detail.

### Applied (batches A/B/C)

- A1 H1 `_gate` helper (memoir.py) — five bodies, ONE gate, ONE wording
  template; the three drifting kill-switch spellings are gone. The
  gate-order pin caught a real evaluation-order regression during the
  fix (an eagerly-read `manager.config` at the call site); the helper now
  takes the manager and reads the knob AFTER the auth gate.
- A2 H3 tool-side `k`/window re-derivation deleted; the service owns both
  bounds. Proven through the tool: k=0 / k=21 / k=-3 ⇒
  `memoir_k_out_of_range`; a non-integer reaches the body's typed
  envelope (`k must be an integer; got 'abc'`) via the service's own
  code; through `ainvoke` the int args_schema rejects it first (framework
  validation, pre-existing). NO type coercion kept, NO 20th code.
- A3 H5 `memoir_block_present` mints the stable id through
  `_stable_id_for("memoir", agent_id=…)` (lazy import).
- A4 H4 SCAN_LIMIT comment now states the W-2 truth (terminal-aware scan;
  the limit bounds the NON-TERMINAL working set; the Python filter is the
  belt).
- A5 H6 the interpreter fallback is gone — every lane pack FAILS LOUD on
  a missing interpreter (`RESULT: FAIL (MISSING-INTERPRETER — the
  worktree-local .venv/bin/python is absent)` + exit 1; batch A
  normalized 13 pack files, 17 lane packs fail-loud in total) and the
  L16 matrix PYTHON guard refuses once, by name, before any leg.
- A6 M12 exact `EXPECTED_PASSED_N` in the eight packs that carried
  `-lt 1` — measured, not copied (see the table in the lane report).
- A7 M15 RESERVED annotations on `memoir_not_a_keepsake` and
  `memoir_budget_exceeded` (no live emitter; no 20th code).
- A8 L21 `MEMOIR_CODE_QUERY_REQUIRED` added to the import-time membership
  guard loop.
- A9 M13 worker `__all__` + `_default_embed` declared (the lane's only
  SDK call site; imported by the retrieval service).
- A10 M11 size rationales for `daemon/tools/memoir.py` and
  `daemon/repositories/memoir/repository.py`, in the manager.py:235
  style.
- A11 M9 ONE provenance helper: `daemon/tools/provenance.py`
  `capture_provenance_class_name` (logs before any empty return); both
  memoir consumers delegate. **DEVIATION, flagged:** the brief's
  `daemon/services/provenance.py` path does not exist and was NOT
  created — the real existing module is `daemon/tools/provenance.py`.
- B1 H7 recall logging: module logger; a WARNING with a reason token on
  every semantic degrade (`no_embedder` / `timeout` / exception class
  name); one-shot construction INFO naming the missing arm
  (`no_config` / `no_api_key` / `no_base_url` / `no_model_or_dims`);
  `semantic_reason=` footer token (AM-33 extension) with the budget
  pack's footer assertions updated IN THE SAME COMMIT. Note: the footer
  pin is SUBSTRING-based (`tests/unit/test_memoir_budget_unit.py`), not a
  literal byte pin — the assertions were extended, not rewritten.
- B2 H8 query-vector dimension guard + pin (mutation-verified:
  RED without the guard, GREEN with it).
- B3 item 14 `Config._classify_enabled` fails CLOSED for unvalidated
  types. VERIFIED BEFORE: a list / dict / float made Pydantic RAISE
  `ValidationError` (a config-file list would abort the BOOT); int 1/0
  were silently coerced. AFTER: 1/0 keep their YAML meaning; every other
  non-str, non-bool value resolves DISABLED and never raises.
- B4 item 15 `_record_failure` returns the CLASS token
  (`_reason_token`, marker-stripped) and the tick line carries a
  `fail_classes=token:count` census, so a dead credential and a dims
  mismatch are distinguishable.
- B5 M8 `_count_pins` ⇒ `len(self._pin_details(...))`.
- B6 L22 the four silent embedder-resolution fallbacks now log at
  WARNING with the offending value / reason: (1) env dims not an int,
  (2) config dims not an int, (3) the MemoirConfig default unresolvable
  (keeping 1536), (4) no embedding model resolvable.
- C1 M16 post-commit honesty in `memoir_write` / `memoir_pin` (a
  `committed` flag; the post-commit envelope says the write MAY HAVE BEEN
  APPLIED and names the verification read).
- C2 M17 per-read briefing guards (`_guarded_read`): a persona failure
  degrades ONLY the persona zone and the keepsakes still ship (D5.9),
  pinned.
- C3 M18 stats-write guard: a failed `mark_recalled` keeps the recall
  (`stats_updated=False`, `marked_ids=[]`,
  `WARNING stats_write_failed:<ExcType>`), pinned. RESIDUAL, disclosed:
  the shipped footer was measured BEFORE the write (it must be, to price
  the envelope), so on this path the footer still says
  `stats_updated=true` while the result fields say False. v2 shape
  (routed polish row): a conditional footer append on the failure path —
  append a correction line to the footer when `stats_updated` is False.
- NEW-4 FIXED (2026-11-06, pre-gate): the `memoir_recall._full_doc_`
  stats claim is qualified — the "episodes returned are exactly the
  episodes marked as accessed" claim now carries the failed-stats
  exception (result still returned, nothing marked,
  `stats_updated=false` / `marked_ids=[]`, `stats_write_failed`
  WARNING logged). Doc-only, zero behavior; `memoir_timeline._full_doc_`
  untouched.

### Deferred follow-ups (labels only — the report's per-item detail could not be transcribed)

| Label | What is known | Action now |
|---|---|---|
| **H2** BM25 consolidation | Production lexical path is PG `ts_rank_cd`; the SQLite path is the dev fallback. | NOT attempted: ranking equivalence was not provable under the lane's time budget. Divergence recorded; route as its own follow-up. |
| **NEW-3 v2** | Per-recall WARNING on a steadily degraded semantic lane (`daemon/services/memoir_retrieval.py:747/:779/:796`): one WARNING per recall. | Optional dampener v2: one-shot or rate-limited WARNING if volume proves noisy. |
| **M4** constant consolidation | The "readers" status is DISPUTED in the rulings. | NOT consolidated: no reader-verified constant identified. Record. |
| **M1 / M2 / M7** | Prose that describes PRE-FIX behaviour. | Not located by label (no report). Doc-only; needs the report or a reviewer pass to point at the exact sentences. Recorded, NOT guessed. |
| **M3** | Unknown detail (report absent). | Follow-up. |
| **M5** | Unknown detail (report absent). | Follow-up. |
| **M6** | Unknown detail (report absent). | Follow-up. |
| **M10** | Unknown detail (report absent). | Follow-up. |
| **M14** | Unknown detail (report absent). | Follow-up. |
| **Low polish set** | Not enumerated in the rulings. | Follow-up. |

A one-item note on M1/M2/M7: the A4 comment fix and the A2 docstring
correction DID remove two lying-prose instances that happened to be
identified by their own rulings; anything else needs the missing report.

### Test-fence correction (disclosed)

`test/packs/memoir_inner_soul_redirect_test.sh` carried a tree-fence
PREFIX allow-list admitting only `inner_soul.py` — it failed mid-lane for
legitimate edits to the memoir service/repository/config files. Extended
to the lane's real surface (disclosed in the lane report).
