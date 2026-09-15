# Pre-Merge Verification — Critical-Notes Tiered Loading & Maintenance (FINAL GATE)

**Date:** 2026-09-15
**Branch:** `feature/critical-notes-retrieval` @ `0c48ff5d` (base `latest` `9db17e7d`; commits f5a1814f → 8ae029df → ecef5bd5 → eb0bd702 → da5c423e → 068d3105 → 0c48ff5d)
**Review chain:** APPROVED merge-ready (this gate = final verification)
**Verdict:** ✅ **PASS-WITH-QUARANTINE** (GO for merge) — every work item's closure criteria met; 5 sweep failures ALL adjudicated pre-existing (zero branch-caused); 4 non-blocking follow-up findings.

**Workers:** 10 instances (1 discovery + 1 live-PG probe + 4 gate packs + 2 sweep packs + 2 probes). 0 re-dispatches, 0 nodes incomplete, 0 repo writes, 0 commits, 0 production changes. Verify-only arc throughout; drift pin `0c48ff5d` confirmed by every worker independently.

---

## 0. Verdict per work item

| # | Work item | Verdict | Anchor evidence |
|---|---|---|---|
| 1 | 🔴 LIVE-PG PROBE (twice-deferred) | ✅ **CLOSED — GREEN** | §1: mirror DDL 6/6 columns + both partial indexes byte-identical + dead column dropped; embeddings `jsonb`; roundtrip via REAL pipeline; §1.4 |
| 2 | Original-symptom verification | ✅ **CLOSED — GREEN** | §2: live assembly path A1–A8 / B1–B4 all Y; zero-pin byte-identity pinned (`out is notes_dicts`) |
| 3 | Full critical-notes gate w/ verifiable counts | ✅ **PASS** — manifest PINNED | §3: 19 file-rows, 1,753 collected → 1,752 P / 0 F / 1 S (env-conditional); prior "1661" reconciled |
| 4 | Broader regression sweep | ✅ **PASS-WITH-QUARANTINE** | §4: 441 collected → 436 P / 5 F — all 5 pre-existing (3 expected + 2 new AsyncMock-rot, git-proven); 0 branch-caused |
| 5 | Degradation ladder + telemetry | ✅ **GREEN (T1–T4)** | §5: distinct rung tokens; clean-window 0 matches; routine line separate; boot probe once w/ real engine |
| 6 | Behavior spot-checks | ✅ **ALL PINNED + PASS** | §6: S1–S6, zero coverage gaps in scope |
| 7 | Leader prompt surface | ✅ **GREEN** | §7: composes via real loader; Convention v2 clean; integrity gate 1323/1323 |

---

## 1. Work item 1 — LIVE-PG PROBE (the twice-deferred merge blocker)

**Setup:** fresh disposable PG14 cluster (`initdb -A trust`, `/tmp/cnret-pgprobe/data`, port 15432, OS-user superuser); wholesale `POSTGRES_*`/`PG_TEST_*` env scrub; `ensemble_prod` never contacted; teardown via `pg_ctl stop -m fast` confirmed (`no server running`, port free).

### 1.1 Mirror DDL (source of truth, `daemon/manager.py:5884–5916`)

Six `ADD COLUMN IF NOT EXISTS` (pinned BOOLEAN NOT NULL DEFAULT FALSE · pinned_at TEXT · pinned_by TEXT · superseded_by_id TEXT · last_reviewed_at TEXT · detail_ref TEXT) + NULL-guarded backfill UPDATE + two partial-index `CREATE INDEX IF NOT EXISTS … WHERE …` + `ALTER TABLE projects DROP COLUMN IF EXISTS critical_notes`.

### 1.2 LINEAGE 1 — create_all + `_ensure_postgres_columns` (`cnret_probe`)

- **a1 six columns landed: Y** — `information_schema.columns` shows all six with expected types (pinned=boolean; others character varying).
- **a2 partial indexes byte-identical: Y** (verbatim `indexdef`):
  - `CREATE INDEX ix_critical_notes_pinned ON public.critical_notes USING btree (project_id, pinned) WHERE (pinned = true)`
  - `CREATE INDEX ix_critical_notes_superseded_by_id ON public.critical_notes USING btree (superseded_by_id) WHERE (superseded_by_id IS NOT NULL)`
- **a3 dead `projects.critical_notes` dropped: Y** — information_schema query returned ZERO rows.

### 1.3 LINEAGE 2 — migration-runner (`cnret_mig`)

Runner is an intentional **NO-OP on PG** (`applied_migrations=[]`; `daemon/migrations/runner.py:699–712` docstring: PostgreSQL is default dialect from v0.5.2+, runner intentionally no-ops for non-SQLite). Columns land via `create_all` (model-declared); **partial indexes land ONLY via the mirror** — by-design divergence matching the architecture contract (§5.2: partial indexes are not expressible via SQLModel create_all). Dead column never present in create_all lineage (no field) — drop statement mirror-exclusive.

### 1.4 (b) + (c) embeddings table & roundtrip

- **(b) `critical_note_embeddings` on PG: Y** — columns note_id / **embedding = `jsonb`** / model / dims / minted_at. JSONBType adapter materialises as real jsonb (not text/bytea).
- **(c) note write → embedding row roundtrip: Y** —
  - `tests/postgres/test_critical_notes_repository_pg.py` → **2 passed in 0.52s**
  - `tests/postgres/test_smoke.py` → **7 passed in 0.85s**
  - Scratch roundtrip through the REAL write-time pipeline (stub ONLY at embed transport, class-level `SkillEmbeddingService.embed_text`): note insert → `_fire_and_forget_embed` daemon thread → real `SkillEmbeddingService(engine=…)` → `set_critical_note_embedding` on same engine → row landed ≤50 ms with vector byte-identical, `model=text-embedding-3-small`, `dims=5`, `pg_typeof(embedding)=jsonb`, all elements `number`; **FK ON DELETE CASCADE verified** (delete note → embedding rows = 0).

**Merge blocker REMOVED.**

---

## 2. Work item 2 — Original-symptom close-out (REAL assembly path)

Probe driver: `assemble_context_messages` (`context_messages.py:1543`) → first-turn branch (`:1820-1839`) → `_maybe_tiered_critical_notes` (`critical_notes_selection_orchestrator.py:151`) → `build_project_context_message` (`context_messages.py:697`, `_stable_id_for('project', instance_id=…)`); embedder stubbed to `None` (BM25-only), mirroring the orchestrator test recipe.

**Probe A — pinned path (3 pinned + 12 active + 2 superseded, query "kubernetes operator pattern…"):**

| # | Assertion | Result |
|---|---|---|
| A1 | all pinned present | **Y** (3/3) |
| A2 | total ≤ 8+6, tail ⊆ active | **Y** (6 rendered: 3 pinned + 3 tail) |
| A3 | hint line w/ correct dropped-count + `project_cn_list` | **Y** — `…(9 additional notes not shown — use project_cn_list for the full view)` |
| A4 | notes section ≤ 12k chars | **Y** — measured **960 chars** |
| A5 | zero superseded content | **Y** (`SUPERSEDED-DEAD-NOTE-{1,2}` absent) |
| A6 | id = `project:{instance_id}` | **Y** — `project:i-probe-A` |
| A7 | revive: same id, no append | **Y** (same_id, same_content, project_msg_count=1; `add_messages` supersedes in place) |
| A8 | ref>500 truncated w/ documented suffix | **Y** — `… (truncated — project_cn_list for full text)`; raw 600-char block absent |

**Probe B — zero-pin render-all fallback:** B1 all 12 actives full text **Y** · B2 superseded absent **Y** · B3 legacy shape, no hint, priority resort **Y** · B4 stable id **Y** (`project:i-probe-B`).
Byte-identity vs pre-feature pinned by `test_critical_notes_phase2_orchestrator.py::TestGatingFallback::test_no_pins_falls_back_to_render_all` (`assert out is notes_dicts` — object identity; `pre_ordered=False`) — **PASS**.

**R21 external surfaces (superseded absence, shared predicate `critical_note_gate.py:32`):**
1. `routers/projects.py::_get_critical_notes_safe` (:117-120) — pinned (`TestGetCriticalNotesSafeFilter` ×2 incl. empty-string-pointer defense) PASS
2. `context_injection.py::_mcp_rag_hint` (:548-551) — pinned (`TestMcpRagHintFilter`) PASS
3. `tools/external_opencode.py` preload (:501-505) — **NO dedicated surface pin** (shared-predicate unit test only) → follow-up F4 (§9)

**Original symptom: CLOSED on live-path evidence.**

---

## 3. Work item 3 — Full critical-notes gate manifest (COUNTS PINNED — this run is the record)

Collect-only headcount recorded per file before every run (`--collect-only -q`), then per-file run. All on `0c48ff5d`.

| # | File | Collected | Passed | Failed | Skipped |
|--:|---|--:|--:|--:|--:|
| 1 | tests/unit/tools/test_critical_notes.py | 41 | 41 | 0 | 0 |
| 2 | tests/unit/tools/test_critical_notes_lifecycle.py | 34 | 34 | 0 | 0 |
| 3 | tests/unit/test_critical_notes_api.py | 14 | 14 | 0 | 0 |
| 4 | tests/unit/test_critical_notes_config.py | 12 | 12 | 0 | 0 |
| 5 | tests/unit/test_critical_notes_migrations.py | 15 | 15 | 0 | 0 |
| 6 | tests/unit/test_critical_notes_schema.py | 23 | 23 | 0 | 0 |
| 7 | tests/unit/services/test_critical_notes_render_phase1.py | 17 | 17 | 0 | 0 |
| 8 | tests/unit/services/test_critical_notes_phase2_selection.py | 18 | 18 | 0 | 0 |
| 9 | tests/unit/services/test_critical_notes_phase2_orchestrator.py | 19 | 19 | 0 | 0 |
| 10 | tests/unit/services/test_critical_notes_phase2_bm25_safety.py | 13 | 13 | 0 | 0 |
| 11 | tests/unit/services/test_critical_notes_phase2_filters.py | 10 | 10 | 0 | 0 |
| 12 | tests/unit/services/test_critical_notes_phase2_r25_id.py | 6 | 6 | 0 | 0 |
| 13 | tests/unit/repositories/test_critical_note_embeddings.py | 18 | 18 | 0 | 0 |
| 14 | tests/unit/services/test_context_injection.py | 90 | 90 | 0 | 0 |
| 15 | tests/unit/test_context_messages.py | 83 | 82 | 0 | 1 |
| 16 | tests/unit/tools/test_prompt_section_reference_integrity.py | 1323 | 1323 | 0 | 0 |
| 17 | tests/integration/test_critical_notes_embed_pipeline.py | 8 | 8 | 0 | 0 |
| 18 | tests/postgres/test_critical_notes_repository_pg.py *(live PG)* | 2 | 2 | 0 | 0 |
| 19 | tests/postgres/test_smoke.py *(live PG)* | 7 | 7 | 0 | 0 |
| | **TOTAL** | **1753** | **1752** | **0** | **1** |

- The single skip: `test_context_messages.py:2252` — env-conditional self-skip (`langgraph.graph.message unavailable in this env`), documented in the test docstring; unrelated to critical-notes.
- **Prior "14-file gate 1661" claim reconciled:** the 1,661 COUNT is exact (D1 union: 15 cn-files incl. integration = 248 + context_injection 90 + integrity 1323), but the file count is **16 active files** (17 unique incl. the PG-marker file), and this gate additionally pinned `test_context_messages.py` (83) + both live-PG files (9) → 19 rows / 1,753.
- Integrity gate (1,323) detail: leader `tools_note.md` "Critical Notes Management" section + ALL agent prompt fences green — no bare file refs, no `file.md → Section` arrows.
- Integration pack is UN-MOCKED by design: `TestRealEmbedderConstruction` (real service bound to caller engine) + `TestRealMintPipeline` (write-time thread persists row on same engine); zero skips/mocks in output.

---

## 4. Work item 4 — Broader regression sweep + quarantine adjudication

| Pack | File | Collected | P | F | S |
|---|---|--:|--:|--:|--:|
| A | tests/unit/test_wanderer_agent.py | 37 | 35 | 2 | 0 |
| A | tests/unit/test_coder_agent.py | 39 | 38 | 1 | 0 |
| A | tests/unit/test_ensemble_config.py | 17 | 17 | 0 | 0 |
| A | tests/test_migration_system_comprehensive.py | 22 | 22 | 0 | 0 |
| A | tests/unit/test_migration_worker.py | 41 | 41 | 0 | 0 |
| B | tests/test_project_repository_atomic.py | 32 | 32 | 0 | 0 |
| B | tests/api/test_projects.py | 9 | 9 | 0 | 0 |
| B | tests/unit/tools/test_instance_tools.py | 197 | 197 | 0 | 0 |
| B | tests/test_api.py | 47 | 45 | 2 | 0 |
| | **TOTAL** | **441** | **436** | **5** | **0** |

All 5 failures adjudicated **PRE-EXISTING, zero branch-caused**:

1. `…test_wanderer_agent.py::TestWandererMetaJsonValidation::test_tools_allow_has_all_declared_categories` — allow grew 13→15 (db+infra, df4bdf99); stale assertion. EXPECTED ✓ (exact node-id match)
2. `…test_wanderer_agent.py::TestWandererMetaJsonValidation::test_tools_allow_does_not_contain_db` — 'db' now allowed (df4bdf99). EXPECTED ✓
3. `…test_coder_agent.py::TestCoderPromptComposition::test_load_coder_prompts` — 'workflow' key since 642b8526. EXPECTED ✓
   (All three already members of the 2026-09-14 consolidated QUARANTINE row; re-confirmed here at `0c48ff5d`.)
4. `tests/test_api.py::test_send_message_success` — `TypeError: object Mock can't be used in 'await' expression` at `daemon/routers/messages.py:249` (`await manager.command_dispatcher.dispatch(...)`).
5. `tests/test_api.py::test_global_exception_handler` — same root cause.

**Adjudication evidence for #4/#5 (NEW pre-existing, git-proven):** zero branch commits on `tests/test_api.py` OR `daemon/routers/messages.py` in `latest..HEAD`; the awaited line was introduced by ancestor merge `fb74611e` (slash-command dispatch seam); `mock_manager` fixture (`tests/test_api.py:16-200`) never AsyncMock-ifies `command_dispatcher.dispatch`; failure class matches the blueprint convention warning verbatim and the known deferred `messages.py:249 AsyncMock rot` family. → QUARANTINE row added; fixture fix = one-line `AsyncMock` addition (recommended follow-up, out of gate scope).

**Sweep bottom line: 436/441 pass, 5/5 pre-existing, branch-caused = 0.**

---

## 5. Work item 5 — Degradation ladder + telemetry

| Check | Verdict | Evidence |
|---|---|---|
| T1 distinct per-rung tokens | **Y** | `stage=vector_rank reason=fuse_failure` · `stage=query_embed reason=api_failure` · `stage=floor reason=under_selection` — mutually distinct |
| T2 routine vs degraded separation | **Y** | routine `[CriticalNotes] selected=N total=M floor_applied=bool instance=X` (no `:Degraded` suffix); ladder under `[CriticalNotes:Degraded]` |
| T3 clean-window absence | **Y** | instrumented healthy run (`phase2_selection`, 18 tests, `--log-cli-level=INFO`): **0** Degraded matches. Orchestrator file's 16 matches ALL expected (5 deliberately-degraded tests + 11 fixture-noise from opaque `FakeProjectRepository.engine` stub) — no production noise |
| T4 boot probe fires once, real engine (B2) | **Y** | pinned by `test_critical_notes_embed_pipeline.py::TestBootProbeNonDeferred` (2 passed); emitted from `daemon/api.py:352` FastAPI lifespan (structural once-only); scratch render confirmed real config values + deferred `?/?` fallback on exception |

Pinned rung tokens: `floor/under_selection`, `query_embed/unavailable`, `query_embed/api_failure`, `pinned_count/repo_failure`. Findings → §9 (F1 token-coverage gap, F2 spec drift, F3 cache_clear format).

---

## 6. Work item 6 — Behavior spot-checks (node-level)

| # | Behavior | Pinning node(s) | Outcome |
|---|---|---|---|
| S1 | lazy-mint ≤10/read hard cap | `test_critical_notes_config.py::…test_section_absent_yields_documented_defaults` (`mint_cap_per_read == 10`) + `test_critical_note_embeddings.py::TestBackfillCandidates::test_respects_limit` | PASS / PASS |
| S2 | M1 mixed superseded/pinned — hint count + sentinel survival | `phase2_orchestrator.py::TestR21EntryPreFilter::test_superseded_rows_never_compete_and_sentinel_lands_active` (`sentinel == 8`; sentinel lands ACTIVE row) | PASS |
| S3 | R25 stable `project:{instance_id}` + revive no-append | `phase2_r25_id.py::TestStableId::test_same_instance_id_same_id_across_calls` | PASS |
| S4 | supersede 2-cycle + old==new refusals | `test_critical_notes_lifecycle.py::TestProjectCnSupersede::test_rejects_old_equals_new` + `…test_2_cycle_supersede_refused_new_is_already_superseded` (A→B legal, B→A refused, lineage intact) | PASS / PASS |
| S5 | ref≤500 write-REJECT + injection truncation precedence | `…lifecycle.py::TestReferenceBound::test_write_reject_over_500_is_authoritative` (reject-first) + `render_phase1.py::TestReferenceBounding::test_over_bound_truncated_with_suffix` (backstop, documented suffix) | PASS / PASS |
| S6 | `_days_since` naive-ISO no-crash | `…lifecycle.py::TestReviewConditions::test_days_since_accepts_naive_iso_string` (tz-less ISO via raw SQL; no raise; 25≤days≤35) | PASS |

Zero NO-PINNING-TEST gaps within item-6 scope.

---

## 7. Work item 7 — Leader prompt surface

Composed via the REAL loader (`daemon/loader.py:load_agent_prompts` :357 → `compose_system_prompt` :390, tools_note as system-prompt section 6). Composed output (60,947 chars) contains `## Critical Notes Management` at line 374 with all five subheadings (Pin discipline / Supersede, don't re-add / Keep `reference` ≤ 500 chars / Re-affirm, don't let notes rot / Removal respects lineage). Section is self-contained: zero `.md` refs, zero arrow-form refs (Convention v2 clean) — corroborated by the 1323/1323 integrity gate.

---

## 8. ensure.md scoped validation

| Requirement | Status | Evidence |
|---|---|---|
| Critical #1 — no regressions in changed packs | ✅ PASS | all gate + sweep packs in change set: 0 branch-caused failures |
| Critical #2/#3 — concurrency pack / no sync-DB-on-loop | N/A-scope | branch touches no locking/async-loop DB surfaces; the one new thread (`_fire_and_forget_embed`) is exercised by the integration pack (same-engine persistence) |
| Critical #4 — dev.sh graceful-shutdown flag | not re-run | `dev.sh` untouched by branch; last verified green 2026-09-10 gate (dev.sh:99/102) |
| Important #1/#2 | N/A-scope | named functions untouched |
| Release Gate | NOT TRIGGERED | verify-only gate on review-approved branch; original-symptom closure proven at integration + live-assembly level; no e2e workflow surface changed |

No ensure.md contradictions this gate. No `pytest -x` anywhere.

---

## 9. Findings & follow-ups (non-blocking; routed for backlog)

- **F1 🟠 Telemetry token coverage gap:** only 4 of 20 distinct `[CriticalNotes:Degraded]` tokens are test-pinned; notably the vector-rung token `stage=vector_rank reason=fuse_failure` (`critical_notes_selector.py:382`) has NO pin (rung BEHAVIOR is covered by bm25_safety, the token string is not asserted).
- **F2 🟡 Spec/docstring drift:** architecture doc `:109` and `repositories/project/repository.py:1993` docstring say `reason=embed_unavailable`; code emits `reason=fuse_failure`. Ops alerting docs would grep the wrong value. One-line doc sync.
- **F3 🟡 `cache_clear` format deviation:** `repository.py:1809` emits `stage=cache_clear note_id=%s reason=clear_failed` (reason positional, not `stage=X reason=Y` adjacent) — a grep alert on `stage=cache_clear reason=` misses it.
- **F4 🟠 R21 surface-3 pin gap:** `tools/external_opencode.py:501-505` preload path has no dedicated superseded-exclusion test (shared-predicate unit test covers logic, not wiring). Recommended: small `test_external_opencode_critical_notes_filter.py`.
- **F5 🟢 Fixture fix (test-side):** AsyncMock-ify `manager.command_dispatcher.dispatch` in `tests/test_api.py` `mock_manager` (closes sweep reds #4/#5; known `messages.py:249` rot family).

## 10. Soak-only items (explicitly NOT testable in-repo)

- First-token latency delta under a ≥10-child fan-out (§4.8 bound ~one embed RTT/child, parallel) — needs live fan-out.
- Priority-distribution floor sizing (critical-count → floor_applied rate) — live telemetry review.
- Post-restart prod boot line `projects_with_pins=N/M total_pinned=K` on the target project (activation checklist §7) — runtime soak after daemon restart.
- `[CriticalNotes:Degraded]` occurrences on real embed-endpoint incidents (token behavior is pinned; real-incident firing is soak).

## 11. Scope decision

Full suite NOT run. Branch = 5 prod files + 3 test files (+ docs) around critical-notes tiered loading — a bounded feature with review approval. Ran: LIVE-PG probe (both lineages) + 19-file gate manifest (1,753) + 9-file touched-module sweep (441) + live assembly probes + telemetry census. Skipped: FE (zero frontend change), e2e release gate (not triggered), full non-integration suite (covered perimeter suffices; consolidated quarantine row documents the wider known-red population). Grand totals executed: **2,194 collected → 2,188 P / 5 F (all pre-existing) / 1 S**.

## 12. Repository state

- Working tree: dirty ONLY on `.agents/` planning/tracking artifacts (approver active.md, tidier notes.md, untracked planning docs + prior-gate RESULTS) — pre-existing, no source dirt; `git diff --stat HEAD -- daemon/ tests/` empty at probe time.
- Zero repo writes, zero commits, zero production changes by any gate worker. Scratch confined to `/tmp/cnret-*/`.

## 13. Worker roster

| Instance | Role | Result |
|---|---|---|
| cnret-discovery (a363cb8b) | inventory + calibration | 17 files / 1,661 union + sweep inventory; calibration 17/17 |
| cnret-pgprobe (1476cebf) | LIVE-PG probe | all green, teardown clean |
| cnret-gate-a (242417df) | gate pack A | 139/139 |
| cnret-gate-b (922337d8) | gate pack B | 273/274 (1 env skip) |
| cnret-gate-c (12316215) | integrity gate | 1323/1323 |
| cnret-gate-d (70b22351) | integration pack | 8/8 |
| cnret-sweep-a (060a03f1) | sweep A | 153/156 (3 expected reds) |
| cnret-sweep-b (1d62cca0) | sweep B | 283/285 (2 pre-existing rot) |
| cnret-symptom (2228e708) | symptom + spot-checks + leader compose | A1-A8/B1-B4/S1-S6 all Y |
| cnret-telemetry (6e47357f) | telemetry probe | T1-T4 all Y + findings F1-F3 |

---

## Overall Status

- Gate manifest: ✅ 1,752/1,753 (1 env-conditional skip) · Sweep: ✅ 436/441 (5/5 pre-existing, quarantined) · LIVE-PG: ✅ · Original symptom: ✅ CLOSED · Telemetry: ✅ · ensure.md scoped: ✅
- **PASS-WITH-QUARANTINE — GO for merge.** Quarantine row added (§4); follow-ups F1–F5 routed; soak items listed (§10).
