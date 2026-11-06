# G2 — Authoritative Real-PG Corpus — Memoir Store v1 Phase C

**Gate worker:** 2 of 5 · **Date:** 2026-11-06 (session started 2026-11-06T01:05Z)
**Tree:** `/home/nea/ensemble-src-wt-memoir-store` @ `feature/ari-memoir-store`, HEAD `2a9a6f1b92cc16b20db5464dc81154138d45112d` (G1's evidence commit — verified at arrival, `git status --porcelain` clean)
**Interpreter:** worktree `.venv/bin/python` (3.14.7, pytest 9.0.2); `import daemon` → `/home/nea/ensemble-src-wt-memoir-store/daemon/__init__.py` (re-proven once)
**Binding spec:** `phase7-plan.md` **D7.6** + `decisions.md` DR-1 / DR-3 + `architecture-recommendation.md` (RRF/recency/keepsake/access-stats/two-axis/zone sections)

## Verdict

**PASS.** Every corpus item 1–10 is proven on real PostgreSQL 16.15, and BOTH dispatch-mandated mutation legs went RED with sha-proven byte-identical restores. No production fix was applied; no plant was ever committed.

## Scratch cluster + fences

| Fence | Evidence |
|---|---|
| Scratch PG | `/usr/lib/postgresql/16/bin/initdb -A trust -U postgres` in `/tmp/memoir-g2-cluster/data`; `SHOW server_version_num` = **160015** (major-version parity with live 16.15 asserted) |
| Port | **15433** — free-port hunt in 10000–19999 via `ss`, never 9797/8079/8088 |
| TCP-only | server started with `unix_socket_directories=''`; all connections over TCP 127.0.0.1 |
| Target assertion | `SELECT current_database()` == `memoir_g2_scratch` asserted before any write; EVERY pack asserts its own throwaway DB the same way (`memoir_schema_pg`: `ensemble_memoir_pg_1395364`; `memoir_retrieval_pg`: `ensemble_memoir_retrieval_pg_1395556`; corpus: `ensemble_memoir_pg_corpus_*`; mutation runs: dedicated `ensemble_memoir_mut*` DBs, all dropped at teardown) |
| Env discipline | inline explicit `POSTGRES_DB/USER/HOST/PORT` + `ENSEMBLE_SELF_ENV=dev` on every DB-touching command; the per-variable `env -u …` scrub inside the packs (never the glob form) |
| Live fence | ZERO writes to `/home/nea/agents-ensemble/**` / `/home/nea/ensemble-src/**`; no daemon boots; no InstanceManager construction; no stage/promote scripts |
| Serial | no xdist anywhere (`PYTEST_XDIST_WORKER` absent; packs run one at a time) |

## Coverage map — D7.6 item → proof

| # | Item | Proof | Where |
|---|---|---|---|
| 1 | **Corrected `ts_rank_cd(tsv, plainto_tsquery('english', :q))` order + mutation leg** | AST source pin + runtime ordering + repo-path rank + EXPLAIN: `test_the_shipped_lexical_sql_uses_the_tsvector_first`, `test_the_shipped_lexical_sql_returns_the_ts_rank_cd_ordering`, `test_the_repository_lexical_rank_uses_the_shipped_sql`, `test_the_gin_index_serves_the_lexical_query` — **MUTATION LEG EXECUTED**: planted the swap in `_MEMOIR_PG_LEXICAL_SQL` → **4 FAILED** → restore byte-identical (sha `293aa605…d3c5d48` == HEAD) → **12 passed** | `tests/postgres/test_memoir_retrieval_pg.py` (existing) + executed plant below |
| 2 | **Two-arg IMMUTABLE `to_tsvector` + AM-8 RED leg** | DB-level rejection: `test_the_one_argument_tsv_form_is_rejected_by_pg` (asserts "not immutable"); shipped-ensure application: `test_the_tsv_column_is_created_by_the_ensure_and_populates` — **MUTATION LEG EXECUTED**: planted the ONE-ARG form in `daemon/manager.py`'s ensure statement → **3 FAILED** with PG's exact error `psycopg.errors.InvalidObjectDefinition: generation expression is not immutable` (the plan's named error) → restore byte-identical (sha `627ddcbb…4a9d38d` == HEAD) → **12 passed** | `tests/postgres/test_memoir_schema_pg.py` (existing) + executed plant below |
| 3 | **GIN usage — EXPLAIN on a populated corpus** | `test_the_gin_index_serves_the_lexical_query` (300-row corpus + `ANALYZE`, asserts the index in the plan) + live EXPLAIN capture: `Bitmap Index Scan on idx_memoir_episodes_tsv (cost=0.00..12.81 rows=1) → Bitmap Heap Scan` — no Seq Scan on the retrieval path | existing pack + live capture (plan text below) |
| 4 | **JSONB embedding round-trip + Python cosine on real stored rows** | `test_the_embedding_round_trips_as_jsonb_and_the_cosine_works`: `store_embedding` → raw-column read (`embedding`, `embedding_model`, `embedding_dims`, `embedded_at`) → `list(stored) == vector`; `_cosine_similarity(vector, stored) == 1.0`; orthogonal-vector cosine ≈ 0.26726 | existing pack |
| 5 | **Keepsake admission + DR-1 zone arithmetic (24k / 48k)** | NEW corpus legs: `test_keepsake_pin_admission_respects_the_zone_cap_on_pg` (two ~1,520-token pins admitted, zone `3044 < 4000`; third refused `MemoirAdmissionError('keepsake_zone_tokens')` naming "the 4000-token zone"; zone UNCHANGED after the refusal; the refused row is NOT a keepsake — nothing evicted) · `test_dr1_overage_disclosure_drops_the_dynamic_zone_on_pg` (25,398-char stable zone: ships IN FULL — body tail verbatim; `"## Recent episodes"` ABSENT; keepsake line ships in the stable zone; disclosure `[…stable zone is 25398 chars — 1398 over the 24,000-char ceiling; pin admission exceeded its budget]` byte-asserted; affordance line closes the block) · `test_dr1_withhold_above_the_absolute_bound_on_pg` (~56k-char persona ⇒ `render_briefing_body` returns **None** + withhold WARNING logged; CONTROL: small persona ships a block — the withhold is size-caused) · `test_dr1_nominal_block_keepsakes_never_double_rendered_on_pg` (all three zones; keepsake rendered ONCE in stable, its body never rendered, dynamic lines carry the plain episodes) | `tests/postgres/test_memoir_pg_corpus.py` (NEW) |
| 6 | **Two-axis time discipline (DD-7)** | NEW: `test_two_axis_time_travel_window_filters_event_at_not_created_at_on_pg` (row written NOW about 2026-03-01: `timeline_window` around the HISTORICAL event returns it — the window filters `event_at`; `fetch_latest` still surfaces it as latest — the write axis; stored `created_at` ≈ now while `event_at` == historical) · `test_two_axis_recency_prior_uses_the_write_axis_on_pg` (identical bodies ⇒ identical `ts_rank_cd`; ids invert the ASCII tie-break: `a-stale-written-year-ago` vs `z-fresh-write-old-event` — the fresh write about the past ships ABOVE the year-old write, which holds ONLY if the prior runs on `created_at`/`last_accessed_at`; under the wrong hypothesis the id tie-break orders the other way) · inclusivity already pinned by existing `test_the_timeline_window_is_inclusive_on_pg` | corpus module (NEW) + existing |
| 7 | **Persona seed + human-lane guard** | NEW: `test_persona_seed_lands_via_the_seed_path_and_is_idempotent_on_pg` (`seed_persona_if_absent` on real PG: `version=1`, `status=approved`, `source="seed"`, body BYTE-EQUAL `MEMOIR_PERSONA_SEED`, `approved_at` set; pointer row `current_version_id == version.id`; second call returns the SAME id and the version count stays 1 — a re-boot duplicates NOTHING) · `test_human_lane_guard_refuses_non_human_actors_on_pg` (`create_draft → submit` allowed for a machine; `approve`/`reject` with `auth_method` ∈ {`api_key`, `mcp`, None} and `actor=None` ALL raise the house `HumanLaneRequiredError`; row still `pending_approval` after the refusals — nothing written; `auth_method="session"` approves) | corpus module (NEW) |
| 8 | **RRF fusion + recency prior + keepsake exemption** | NEW: `test_rrf_fusion_combines_the_bm25_and_cosine_lanes_on_pg` (real PG rows + stub sync embedder + `store_embedding`; expected order recomputed from the SAME primitives — `repo.lexical_rank` + Python cosine → `fuse_rankings` → assert `rendered_ids == expected`; `rank_basis == "rrf"`, `semantic_enabled`; a SEMANTIC-ONLY row (no lexical term) ships; a both-lane row outranks any single-lane row) · `test_recency_prior_deprioritizes_stale_and_exempts_keepsakes_on_pg` (asserted order `[m-fresh-now, z-keepsake-old, a-stale-old]`: the fresh row leads via `event_at` tie-break; the KEEPSAKE leads the stale row — impossible unless the multiplier exemption holds, and the stale row trails — impossible unless the prior is applied; both wrong hypotheses (prior absent / exemption broken) produce `[m-fresh, a-stale, z-keepsake]` and RED) · arithmetic on real idle: `recency_multiplier(is_keepsake=False, idle_days=365) < 0.2`; keepsake == 1.0 at any idle | corpus module (NEW) |
| 9 | **Access stats on EXPLICIT recall only** | NEW both directions: `test_explicit_recall_bumps_access_stats_for_exactly_the_shipped_ids_on_pg` (recall k=2 of 4 rows: shipped `access_count == 1` + `last_accessed_at` set; non-shipped `0`/`None`; second recall → `== 2`; `stats_updated`) · `test_implicit_briefing_reads_do_not_bump_access_stats_on_pg` (seeds persona + 2 episodes; snapshot all-zero; runs the REAL briefing path `asyncio.run(_build_memoir_briefing_message("ari", …, stub_manager(_engine)))` → block built ("## Persona" + seed text) and stats BYTE-UNCHANGED; CONTRAST leg: one explicit recall immediately bumps the shipped ids) | corpus module (NEW) |
| 10 | **ARRAY `stars` / CHECK constraints (W2) / partial indexes** | `test_the_stars_round_trip_on_pg` (list / None / empty), `test_the_model_bound_check_constraints_exist_on_the_pg_tables` + `test_the_importance_check_is_enforced_by_pg` + `test_the_pinned_by_check_is_enforced_by_pg`, `test_the_partial_indexes_exist_on_pg` (both packs: keepsakes + pending-embedding predicates carry `WHERE`; agent_created + agent_event present) — re-run green at registered counts | existing packs (both) |
| — | ensure idempotence · tsv populate-on-INSERT · W8 one-transaction blast radius · tsv ALTER loud-failure | **G1-covered** (cited, not redone): `.agents/tester/RESULTS/2026-11-05-memoir-g1-w8-boot-abort.md` | G1 |

## Pack runs (serial, all on the scratch cluster)

| Pack | Result | Runtime |
|---|---|---|
| `memoir_schema_pg_test` | **PASS — 12/12** at the registered pin | 12.5 s |
| `memoir_retrieval_pg_test` | **PASS — 12/12** at the registered pin | 12.4 s |
| `memoir_pg_corpus_test` (NEW) | **PASS — 13/13** at the new pin (`EXPECTED_PASSED_N=13`) | 15.6 s |
| retrieval pack under mutation-leg-1 plant | **RED — 4 failed / 8 passed** | 11.9 s |
| retrieval pack after sha-verified restore | **GREEN — 12/12** | 12.2 s |
| schema pack under mutation-leg-2 plant | **RED — 3 failed / 9 passed** (`InvalidObjectDefinition: generation expression is not immutable` ×3) | 13.3 s |
| schema pack after sha-verified restore | **GREEN — 12/12** | 12.3 s |

## Mutation legs — RED/GREEN proofs

### Leg 1 — the shipped `ts_rank_cd` argument order (D7.6 / AM-1)

- **Plant:** `daemon/repositories/memoir/repository.py`, `_MEMOIR_PG_LEXICAL_SQL` ONLY (the module docstring occurrence is prose and untouched):
  `ts_rank_cd(tsv, plainto_tsquery('english', :q))` → `ts_rank_cd(plainto_tsquery('english', :q), tsv)`
- **RED (4 FAILED):**
  - `test_the_shipped_lexical_sql_uses_the_tsvector_first` — the AST-extracted source pin
  - `test_the_shipped_lexical_sql_returns_the_ts_rank_cd_ordering` — runtime ordering
  - `test_the_repository_lexical_rank_uses_the_shipped_sql` — runtime through the repo (PG raises `UndefinedFunction` — the R2 defect class)
  - `test_the_gin_index_serves_the_lexical_query` — the EXPLAIN leg
- **Restore:** `git checkout -- daemon/repositories/memoir/repository.py`
- **Sha proof:** `git show HEAD:…` `293aa605496ac003f90696514ef450cd237fabe45f050ab0e29d41519d3c5d48` == working tree `293aa605…d3c5d48` (pre-plant working-tree sha was the same — recorded before planting)
- **GREEN:** retrieval pack 12/12.

### Leg 2 — the two-arg IMMUTABLE `to_tsvector` (D7.6 / AM-8)

- **Plant:** `daemon/manager.py`, the memoir ensure statement ONLY: `to_tsvector('english', …)` → `to_tsvector(…)` (the ONE-ARG form — STABLE, illegal in a `GENERATED … STORED` expression)
- **RED (3 FAILED), each with PG's exact plan-named error:**
  `psycopg.errors.InvalidObjectDefinition: generation expression is not immutable`
  - `test_the_tsv_column_is_created_by_the_ensure_and_populates` — applies the shipped ensure statements extracted BY AST from `manager.py` (the discriminator: the plant changes the SHIPPED statement, the test proves the shipped tree)
  - `test_the_gin_index_exists_and_serves_the_lexical_query`
  - `test_the_ensure_is_idempotent`
- **Restore:** `git checkout -- daemon/manager.py`
- **Sha proof:** `git show HEAD:…` `627ddcbb00329ea44bb4b68aa1c66c07a3338936278aa380002cec4f84a9d38d` == working tree `627ddcbb…4a9d38d` (pre-plant sha identical)
- **GREEN:** schema pack 12/12.
- **Final tree state:** `git status --porcelain` = exactly the two NEW untracked files (`tests/postgres/test_memoir_pg_corpus.py`, `test/packs/memoir_pg_corpus_test.sh`) — never a plant.

## Item 3 — live EXPLAIN capture (300-row corpus, `ANALYZE`)

```text
Limit  (cost=16.84..16.84 rows=1 width=14)
  ->  Sort  (cost=16.84..16.84 rows=1 width=14)
        Sort Key: (ts_rank_cd(tsv, '''zebra7'''::tsquery)) DESC, id
        ->  Bitmap Heap Scan on memoir_episodes  (cost=12.81..16.83 rows=1 width=14)
              Recheck Cond: (tsv @@ '''zebra7'''::tsquery)
              Filter: (agent_id = 'ari'::text)
              ->  Bitmap Index Scan on idx_memoir_episodes_tsv  (cost=0.00..12.81 rows=1 width=0)
                    Index Cond: (tsv @@ '''zebra7'''::tsquery)
```

## Notes and disclosures

1. **GIN plan shape is corpus-dependent (planner cost, not a defect):** on a 300-row corpus of ONE-LINE bodies the planner legitimately chooses a Seq Scan (table cost ≈ 9.5 under the GIN path); the pack's corpus shape (40-filler rows ⇒ ≈84 KB table) tips the cost model to the GIN Bitmap Index Scan. The pack asserts the index NAME in the plan, which holds on its corpus. Recorded so a future gate doesn't misread a tiny-corpus seq scan as a regression.
2. **Quick-fixes applied (test-side only, each re-run to green; production code never permanently modified):**
   - the corpus module's `_WORD` length assert (10 vs 11 chars) → `_WORD = "quiescence "` (one line);
   - pin-actor vocabulary: `PIN_ACTORS` is LOWERCASE (`emperor`/`empress`) — the first drafts used `"Emperor"` and were refused `MemoirWriteError('invalid_actor')`; the corpus now uses the shipped vocabulary (this refusal is itself the shipped validation behaving correctly);
   - the admission refusal formats the budget WITHOUT a thousands separator (`"4000-token zone"`);
   - the DR-1 disclosure assert: the renderer measures `len(escaped_stable)`; naive `len(rendered.split(prefix)[0])` counts the 2-char `"\n\n"` join separator — fixed with `.rstrip("\n")` (the assert now byte-matches the renderer's own number);
   - **lazy model-registration hazard:** the briefing path (`_build_memoir_briefing_message` → `context_messages` → skill-metrics/critical-notes chains; `daemon.tools.provenance`) registers ADDITIONAL SQLModel families mid-run (`snapshot_embeddings`, `board_notes`, `secrets_audit`…), and the per-test `TRUNCATE` fixture names every registered table — a mid-run registration names a table the session `create_all` never built. Fixed by pre-importing the chains at MODULE level (collection time ⇒ before `create_all`). Diagnostic path (one probe per hypothesis, no retry loops): which import registers what, proven with a metadata-table census before each fix.
3. **First mutation RED run initially came back `12 skipped` (GREEN-AT-ZERO trap):** the conftest connects to `PG_TEST_DB` but never CREATES it — the packs own the CREATE DATABASE step. The corpus/mutation runs create their DB explicitly and assert `current_database()` before pytest. Recorded as the execution-shape lesson for future PG legs: **create-then-assert-then-run**.
4. The pack wrappers' DSN contracts differ (`PG_TEST_*` vs `MEMOIR_PG_*`) — both honored as shipped; the corpus wrapper consumes `PG_TEST_*` to match the schema pack.
5. Runtime: ~60 min wall total (setup ~2 min, registered packs ~1 min, EXPLAIN capture ~5 min, corpus authoring+fix cycles ~30 min, mutation legs + restores ~10 min, docs ~10 min). Every pack ≤ 5 min (dual-layer `timeout 300` outer / 280 s inner).

## Commits

Path-scoped only: `tests/postgres/test_memoir_pg_corpus.py`, `test/packs/memoir_pg_corpus_test.sh`, `.agents/tester/PACKS.md`, this RESULTS doc — message `test(memoir): G2 real-PG corpus legs + mutation pins`. The two mutated production files were RESTORED byte-identical and are NOT in the commit (sha-proven above).
