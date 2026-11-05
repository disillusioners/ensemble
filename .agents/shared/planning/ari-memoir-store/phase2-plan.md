# Phase 2 — Repository + hybrid retrieval service

**Depends on:** P1. **Flags consumed:** #6 (named writers), #10 (timeline semantics), #11 (recall budget), #1 (pin admission), #5 (write bounds).

## 1. Objective

The data-access layer with NO general-purpose update (mutability partition enforced structurally), and the hybrid retrieval service implementing design §7 exactly: RRF k=60 over two rankers, importance tie-break, 180-day recency prior (keepsake-exempt, recall-only stats), plus the timeline filter-then-rank semantics and the recall output budget.

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D2.1 | Repository public surface is a CLOSED set: `insert_episode`, `mark_recalled`, `set_pin`, `clear_pin`, `store_embedding`, `record_embedding_attempt`, `mark_superseded` (defined, uncalled in v1), `get_pending_embeddings`, `fetch_keepsakes`, `fetch_latest`, `get_approved_persona`, `seed_persona_if_absent`, `lexical_rank`, `timeline_window`, `count_pins`/`keepsake_zone_tokens` | flag #6 partition: one named writer per mutable family; NO body/identity UPDATE exists anywhere | a generic `update_episode(**cols)` (the exact hazard the flag names) |
| D2.2 | Lexical ranker is driver-aware behind ONE interface: PG → `text()` SQL `ts_rank_cd(plainto_tsquery('english', :q), tsv) … WHERE tsv @@ plainto_tsquery('english', :q) ORDER BY score DESC LIMIT :cand`; SQLite → Python BM25 (`skill_search_service._bm25_score`, the `critical_notes_selector` precedent :12-20) | stock PG has no BM25; **ts_rank_cd** named as the closest stock approximation (coverage-density ≈ TF+proximity); RRF consumes only ORDINAL ranks so the score family is immaterial to fusion (flag #10 vocabulary resolution, applies to recall AND timeline) | literal "BM25" claims (dishonest on stock PG); pgvector/paradedb extensions (R4) |
| D2.3 | Semantic ranker: embed the QUERY once per call (worker-grade client, P3 contract), then cosine in Python over candidates `WHERE embedding IS NOT NULL AND embedding_model = :model` (identity match — DD-6 "a dimension is not an identity") | `_cosine_similarity` shape at `daemon/services/critical_notes_selector.py:230-238` (mirrors `SkillEmbeddingService.cosine_similarity` :634 — both verified) | SQL-side cosine (none exists for JSONB without extensions) |
| D2.4 | Fusion pipeline (design §7 verbatim order): (1) candidate sets from both rankers (a row missing an embedding contributes to lexical only — degradation, never disqualification); (2) `score = Σ_r 1/(60 + rank_r)`; (3) sort (score DESC, importance DESC); (4) multiply NON-keepsake scores by `exp(−idle_days/180)` where `idle = now − coalesce(last_accessed_at, created_at)`; keepsakes multiplier = 1.0 (NEVER decayed); (5) final sort (score DESC, importance DESC, event_at DESC) | §7 binding text; keepsake exemption DD-12 | applying the prior pre-fusion (changes ranker semantics, not blessed) |
| D2.5 | Stats discipline: `mark_recalled(ids, now)` — ONE column-targeted UPDATE `SET last_accessed_at=now, access_count=access_count+1 WHERE id IN (…)`, called ONLY by the recall service for RETURNED episodes; briefing and timeline NEVER write stats | design §7 "the only path that updates"; DD-12 "a briefing is not an act of attention" | counting timeline reads (read-path write amplification) |
| D2.6 | Timeline = filter-then-rank (flag #10): inclusive `event_at` window; `q` present → lexical-only rank within the window (D2.2 ranker); `q` absent → `event_at DESC`; NO cosine/RRF/recency/keepsake priority; NO stats writes | roadmap: "time-travel is a query"; the recency prior would demote exactly the rows a historical window asks about | timeline-as-second-recall (two tools, two expectations — the flag's warning) |
| D2.7 | Recall budget (flag #11): k default 5 max 20 (out-of-range → typed refusal); per-episode render clamp 1,200 tokens with `[…body truncated, N chars total]`; total envelope ≤6,000 tokens with RANKED-DROP (lowest rank dropped first, head stays whole) + footer `[k episodes; M dropped for budget — narrow the query or use memoir_timeline]`; envelope enforced on the SHIPPED projection with a final re-check before return | harmonized with the briefing estimator (`estimate_tokens`, `daemon/loader.py:1027` — verified); metrics-read M-1 doctrine ("shipped projection, not raw rows" — the C1 regression class named and pinned) | uniform squeeze across episodes (destroys the head results) |
| D2.8 | Pin admission (flag #1): `set_pin` REJECTS when `count_pins(agent) >= 12` OR `keepsake_zone_tokens(existing) + est(candidate_render) > 4000`; error names the binding constraint + current pins (id/title/pinned_by/tokens) — the critical-notes reject-don't-evict shape (`_MAX_ENTRIES` :70, check+candidates :537-546 — verified) | the stable zone's "never truncated" made true by construction | count-only cap (a 2,800-token body × 12 blows the zone) |
| D2.9 | Write bounds (flag #5 hygiene): body ≤ ~2,000 words (house request-length family; hard char cap ~16,000 chars with typed refusal), title ≤200 chars, stars ≤16 entries × ≤120 chars each (post-trim), importance validated 1–10, `event_at` ISO-parse else refusal | request-shape bounds, not normalization; content untouched | silent clamping (refusals teach; clamps corrupt) |

## 3. Tasks

| ID | Task | Outcome | Touchpoints | Depends | AC |
|----|------|---------|-------------|---------|----|
| T2.1 | Repository with the D2.1 closed surface + column-targeted UPDATEs | method census test passes; grep pin: no UPDATE touches body/title/importance/stars | daemon/repositories/memoir/repository.py | P1 | AC1 |
| T2.2 | Driver-aware lexical ranker (PG text() / SQLite Python BM25) | identical interface; PG leg returns ts_rank_cd ordering; SQLite leg BM25 ordering | repository.py (`lexical_rank`) | T2.1 | AC2 |
| T2.3 | Retrieval service: fusion pipeline D2.4 + stats D2.5 + budget D2.7 | ranked results with metadata footer (rank basis, missing-embedding count, stats-updated flag) | daemon/services/memoir_retrieval.py | T2.2 | AC3 |
| T2.4 | Timeline window query D2.6 | window+q results rank-ordered; no stats delta (assert) | repository.py (`timeline_window`) | T2.2 | AC4 |
| T2.5 | Pin admission D2.8 (+ keepsake-at-write path calls the same admission) | cap rejections name constraint + pins; unpin frees budget | repository.py (`set_pin`/`clear_pin`/counters) | T2.1 | AC5 |
| T2.6 | Persona getters + transition guard (mirror `VALID_TRANSITIONS` vocabulary) | approve/reject/submit helpers enforce transitions; no direct-to-approved path outside the seam | repository.py | T2.1 | AC6 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Repository surface test: exact method set; no general update; UPDATE statements column-targeted (mutation: add a body-touching UPDATE → pin RED) |
| AC2 | Ranker equivalence-of-interface: same query on PG (real) and SQLite (Python BM25) both return ordinal-ranked candidate lists; PG path uses the GIN index (EXPLAIN leg) |
| AC3 | Fusion: synthetic corpus pin — RRF k=60 math exact (hand-computed fixture); importance breaks ties; prior multiplies non-keepsakes only; never-recalled row uses created_at as idle base |
| AC4 | Timeline: window filter inclusive both ends; q narrows lexically; keepsake and stale rows NOT prioritized/deprioritized; `mark_recalled` NOT called (byte-assert on stats) |
| AC5 | Admission: 13th pin rejected (count); oversized pin rejected (budget) with names; 12 small pins accepted; unpin→re-pin works; keepsake-at-write reuses admission (fixture) |
| AC6 | Persona transitions: draft→pending_approval→approved ✓; draft→approved ✗ (refused); seed path bypasses via `source='seed'` constructor only |

## 5. Test packs

- `test/packs/memoir_retrieval_unit_test.sh` — AC2/AC3/AC4 fixtures (pure-python, mock embedder).
- `test/packs/memoir_retrieval_pg_test.sh` — real-PG ts_rank_cd + GIN EXPLAIN + JSONB cosine + ARRAY stars.
- `test/packs/memoir_admission_unit_test.sh` — AC5 + AC1 mutation legs.
- `test/packs/memoir_budget_unit_test.sh` — envelope ranked-drop, per-episode clamp, footer, final-re-check (mutation: re-check removed → RED; trim measuring raw rows → RED).

## 6. Flag consumption

#6 (D2.1), #10 (D2.2/D2.6 + the ts_rank_cd vocabulary resolution), #11 (D2.7), #1 (D2.8), #5 (D2.9), plus DD-6/D2.3 (identity) and DD-12/D2.5 (stats discipline).
