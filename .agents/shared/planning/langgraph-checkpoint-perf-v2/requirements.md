# Requirements: langgraph-checkpoint-perf-v2 — Process Closure (PR5 + Re-Review + Deferred Items)

> Rev 2.2 — delta-review sweep (2026-09-04): stale-text alignment (5 warnings + 3 suggestions); APPROVE sign-off precondition

Date: 2026-09-03 (v1 Rev 4 → v2 closure pass)
Author: planner[v2] via requirements-analysis worker
Status: Draft (for planner ingestion; separate plan-creation worker owns phase plan)
Source Request (verbatim, dispatcher task):
> "Requirements decomposition — define the missing PR5 acceptance gate + process-closure + deferred-items disposition for the checkpoint-performance v2 initiative. You produce the WHAT-MUST-BE-PROVEN requirements; a separate plan-creation worker builds the phase plan from your output."

Companion docs (v1, READ-ONLY):
- `.agents/shared/planning/langgraph-checkpoint-perf/{plan-overview.md, phase1-plan.md, decisions.md, roadmap.md, research-findings.md}` (branch `feature/langgraph-checkpoint-perf` @ `c37c870c`)
- `.agents/reviewer/memories/2026-08-26-pr2-message-metadata-tap-deep-review.md`
- `.agents/reviewer/memories/2026-08-26-pr3-read-flip-deep-review.md`
- `.agents/reviewer/memories/2026-08-26-pr4-blob-prune-deep-review-needs-changes.md` (the NEEDS_CHANGES verdict — PR4 post-fix re-review exists ONLY in commit-message form at `7a7998fe`)
- `.agents/approver/langgraph-checkpoint-perf-tracking.md` (iteration 001 notes the deferred-by-design items)
- `docs/runbooks/checkpoint-blob-prune-restore.md` (destructive enablement gate)

Out-of-band reference: `~/Downloads/langgraph-checkpoint-performance-discussion.md` (1777 lines; PERF-1..9, Solutions A–U; §32 observability, §33 guardrail).

---

## Stakeholders

- **Requester:** planner[v2] (dispatcher) — orchestrating the v2 initiative on branch `feature/langgraph-checkpoint-perf-v2` (current tip `2f80d45b`).
- **Affected users (downstream):** developer worker (implements PR5 + process-closure artifacts), reviewer (consumes PR4 formal re-review record), tester (consumes re-baselined perf numbers + bound manifests), operator (consumes destructive-enable pre-flight evidence + runbook updates), user/program owner (decides go/no-go on the four deferred items).
- **Affected systems:**
  - `daemon/persistence.py`, `daemon/services/message_tap.py`, `daemon/services/checkpoint_prune.py`, `daemon/checkpoint_adapter.py`, `daemon/services/maintenance.py` (Operation E), `daemon/routers/instances.py` (GET `/instances/{id}/messages`).
  - `tests/integration/checkpoint_prune_real_saver.py` (binding gate), `tests/integration/test_message_metadata_hook_placement.py` (AST gate), `tests/integration/test_no_saver_imports_in_routers.py` (§33 guardrail), `tests/unit/persistence/test_get_instance_messages_no_alist.py` (alist absence), `tests/performance/` (bench harness).
  - `docs/runbooks/checkpoint-blob-prune-restore.md` (operator-facing).

---

## Functional Requirements

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-1 | PR5 acceptance gate MUST run on REAL PostgreSQL (binding) and prove the Phase-1 perf goal end-to-end | v1's 969-line `tests/integration/checkpoint_prune_real_saver.py` reached 9/9 GREEN on PG 14.22 (commit `7a7998fe`) — v2 must re-prove on the v2 tip with current PG. Mocked-saver gates are insufficient for data-destruction features | Must | PR5 Acceptance Gate |
| FR-2 | PR5 MUST assert the invariant `message_api_checkpoint_list_total == 0` across every message-API path (GET `/instances/{id}/messages` and any message-listing endpoint) — NOT a hardcoded literal; observed count must disappear post-C1 | §32 binding intent; v1's gate was a log-line emitting the OBSERVED count + its post-C1 collapse to 0 (Critical 7 — vacuous-`=0` constant rejected). A vacuous assertion would let a regression slip through | Must | PR5 Acceptance Gate |
| FR-3 | PR5 MUST assert "message API cost ∝ page size, NOT checkpoint history" via a re-baselined performance harness (executed matrix: 6 targeted cells drawn from page sizes {1,10,100,400,1000} × history depths {100,150,400,1000,10000}, incl. baseline anchors 150/400 at page_size=100 — per AC-3.2/AC-3.3, NFR-4, plan-overview SC-3, phase5-plan T5.5) | The pathology measured 206 MB / 42 s / 2.1 GB RSS at 1000-checkpoint history (v1 discussion doc §32). The fix's value is the disappearance of the history-multiplier — a single page-size benchmark is insufficient | Must | PR5 Acceptance Gate |
| FR-4 | PR5 MUST prove `saver.alist(...)` is invoked ONLY inside `daemon/migrations/checkpoint_migrator.py` (the offline migrator) and ZERO times on any live API code path, via armed-absence tests (`AsyncMock` set up to fail if invoked, not just count) | A simple call-count assertion can pass under "the call exists but was bypassed by mocking" — armed absence (would-succeed-if-invoked) is the genuine dead-path proof (PR3 review doc, §1.1.2) | Must | PR5 Acceptance Gate |
| FR-5 | §32 observability: every saver operation MUST emit a structured log line `op=<name> latency_ms=<int> bytes=<int>` gated by `CHECKPOINT_PERF_LOGS`; metrics surface MUST expose `message_api_checkpoint_list_total` and the per-op latency histogram | §32 source-doc imperative; v1's `checkpoint_perf.py` log line already exists but does NOT cover the full saver-op surface (only the `alist_count` was emitted). The v2 imperative is "all saver ops, not just the forbidden one" | Must | Observability (per §32) |
| FR-6 | §32 observability: every `message_metadata` lookup that fails (repo exception, DB down, schema drift) MUST log a WARNING with the degradation reason AND emit `state.ts` fallback timestamps; response shape MUST stay byte-identical | PR3 review doc §1 (🟡): `getattr(manager, "message_metadata_repo", None)` short-circuit is SILENT when repo is None — no log/metric. Today's prod has the repo, but the gate must catch any future misconfiguration. Catch must be `except Exception` (never `BaseException` — CancelledError propagates by design) | Must | Observability (per §32) |
| FR-7 | §33 guardrail: an AST-based import-scan test MUST assert NO `langgraph.checkpoint.*` import AND NO `.alist(` call site appears under `daemon/routers/**` (Phase 1 scope; allowlist `tools/lint/allowlist.txt` empty for Phase 1) — test is a standard-suite gate (NO new CI infra) | v1 has `tests/integration/test_no_saver_imports_in_routers.py` (LD-OQ2). v2 MUST re-prove it on the v2 tip AND extend scope per §33 ("no raw saver anywhere in routers"). Test mechanism = AST import detection + AST call-func scan (multi-line/aliased-import robust) | Must | Guardrail (per §33) |
| FR-8 | PR4 formal re-review record MUST exist as a STANDALONE ARTIFACT (markdown file in `.agents/reviewer/memories/`), NOT a commit message; it MUST re-verify the four race folds landed at `7a7998fe` AND the two pre-existing reviewer 🟡 follow-ups carried from the original NEEDS_CHANGES verdict | v1's `7a7998fe` carries a "Targeted re-review verdict: SATISFIED, 0 findings" line in its commit message — that's an INSUFFICIENT record: no separate artifact means no audit trail, no second-pair-of-eyes evidence outside the implementer. The process gap you are closing | Must | Process Closure |
| FR-9 | Gate-manifest regeneration MUST be performed per closure cycle and stored as an artifact (file path + commit SHA pair); manifests MUST NEVER be copy-pasted from prior phases | v1 manifests are STALE on v2 tip (v2 added commits since the last manifest regen at `fc908945` post-`7a7998fe`). Copy-pasting the v1 manifest would silently understate the v2 test count and weaken the binding | Must | Process Closure |
| FR-10 | Deferred-items checklist MUST exist with per-item verify-now vs defer-with-signoff classification, owner-facing verification steps, and explicit "NEVER write to ensemble_prod" exclusion | v1's approver tracking (iteration 001 notes) lists three deferred-by-design items: (a) prod `channel_versions` JSONB shape verification; (b) `seq` index at prod volume; (c) `is_retry` re-tap drift. None have been verified | Must | Deferred Items |
| FR-11 | For deferred item (a) prod channel_versions verification, the verification path MUST be a READ-ONLY query against `ensemble_prod` checkpoints OR be skipped entirely with signoff; the runbook MUST document the exact query (one representative thread's `jsonb_pretty(checkpoint->'channel_versions')` + the round-trip blob-row count) | v1's destructive-enable runbook §2 already enumerates this query verbatim — v2 MUST reuse the query and either execute it (read-only) or carry it as an explicit sign-off checklist item with a documented reason for deferral | Must | Deferred Items |
| FR-12 | For deferred item (b) seq-index decision at prod volume, v2 MUST capture the v2-tip INSERT rate at the binding-gate PG instance, derive the estimated 7-day row-count, and decide add-index-now vs add-on-Phase-2 with explicit cost numbers — NOT a "we'll see" deferral | v1 decisions D5/D9 chose nullable-with-no-default (ADD cheap on both backends) + NO index in Phase 1. At prod write volumes an unindexed seq column could become a Phase-2 wire-up hazard. The decision must be evidence-based | Should | Deferred Items |
| FR-13 | For deferred item (c) `is_retry` re-tap drift on pause→resume→revive, v2 MUST add a test that pauses mid-turn (between `tap_node_return` await and node return) and revives via the COMPLETED→RUNNING path (per `instance_messaging.py` reuse-revive semantics) and asserts NO `message_metadata` row is MISSING (never-under-record invariant) | PR2 review doc §3: "over-record property: pause between tap-await and node return → side-table rows for un-checkpointed messages (never under-records; benign once PR3 joins metadata to checkpoint walk)". The never-under-record property needs an explicit test pinning it across the revive path | Must | Deferred Items |
| FR-14 | Out-of-scope items MUST be enumerated as an explicit list with rationale (cursor pagination, agent_messages/agent_events durable store, shallow saver, LZ4/TOAST, thread rotation, artifact OOB storage, backfill) — and backfill (Solution N) MUST have explicit "coverage sufficient" criteria so it can be DROPPED if PR3's `state.ts` fallback already covers pre-side-table history | The owner has flagged backfill as OPTIONAL final phase ONLY IF cheap. The criteria for "coverage sufficient" are NOT in v1 — v2 MUST define them so a future reviewer cannot invent a backfill as a "completeness" excuse | Must | Out of Scope |
| FR-15 | `message_metadata` side-table prune MUST be wired into the instance-cleanup path BEFORE MERGE (MERGE PRECONDITION per plan-overview.md "MERGE PRECONDITION" bullet + architect §3 / §8.1): add `MessageMetadataRepository.delete_for_thread(thread_id) -> int` (PG + SQLite) and call it from `maintenance.py::_cleanup_instance` AFTER `adelete_thread` and BEFORE the in-memory on-instance-deleted callback. The deliberate non-action on Operation D checkpoint-prune orphans MUST be documented (PR2 review §3 property). A real-PG acceptance test MUST prove rows drop to 0 after `_cleanup_instance` (populate → tap → assert N rows → `_cleanup_instance` → assert 0 rows + checkpoints gone) | Architect §3 finding: v1 ships `message_metadata` unbounded growth (2–4 rows/turn × turns × instances, forever; Operations A–D never touch the side table; no FK to `instances` on either backend; pinned/revivable terminals make rows permanent); this v2-side work packages the §3 fix as a v2-new Phase 5 task (T5.19) and elevates it to a **MERGE PRECONDITION** | Must | MERGE PRECONDITION (per plan-overview.md) — delivers the prune + test + non-action doc; complements FR-10..13 deferred-item disposition |

### Theme: PR5 Acceptance Gate (FR-1 through FR-4)

**FR-1 (PR5 binding gate on real PG):** PR5 is the Phase-1 closure gate. It MUST re-run the v1 binding-gate harness on the v2 tip. The harness is `tests/integration/checkpoint_prune_real_saver.py` (v1: 969 lines, 9/9 GREEN on PG 14.22, implementer ×4 consecutive runs + independent targeted re-review run per `7a7998fe`'s commit-message). v2 MUST additionally prove the PR2/PR3/PR4 suites on the v2 tip. SKIP-LOUDLY on PostgreSQL-unreachable — a skip MUST NEVER count as GREEN for the binding gate (v1 honesty-contract line 1 of the test file).

- **Rationale:** The data-destruction feature (Operation E destructive DELETE on `checkpoint_blobs`) requires real-saver proof that no mock can supply.
- **Priority:** Must.
- **Notes:** Reuse v1's `tests/helpers/checkpoint_prune_pg.py::ADMIN_DSN` and the `evict_langgraph_mocks`/`restore_langgraph_mocks` fixture pair (v1 proven pattern). Use the file-backed SQLite recipe (tmp_path + NullPool + PRAGMA journal_mode=WAL + PRAGMA busy_timeout=10000) ONLY for non-PG integration paths. **FORBIDDEN:** `StaticPool + WriteGuardSession` (trips the QUARANTINE.md write-corruption pattern — see TESTER conventions).

**FR-2 (invariant `message_api_checkpoint_list_total == 0`):** The metric MUST be exposed on the v2 metrics surface (Prometheus-style endpoint OR the `daemon` internal metrics collector per project convention). The assertion in PR5 MUST scan every message-API endpoint (`GET /instances/{id}/messages`, any future list endpoint) and assert the observed count is 0. The implementation is NOT a hardcoded `== 0` literal (Critical 7 — vacuously true). The implementation is: capture the count via the `log_saver_op` observer; assert the captured count equals 0 across N=10 random thread ids with non-empty history.

- **Rationale:** The §32 source-doc metric captures the operator's primary invariant ("alist walks = data corruption risk"). A vacuous assertion would defeat the metric.
- **Priority:** Must.
- **Notes:** The test-pinned mechanism is `tests/unit/persistence/test_checkpoint_perf_logging.py` (v1 exists — verify it still pins the OBSERVED count, not a literal).

**FR-3 (perf assertion — cost ∝ page size, NOT history):** v2 MUST produce a `tests/performance/test_message_api_cost.py` harness. Bench matrix: 6 executed cells (page_size, history_depth): `(1,10000)`, `(10,1000)`, `(100,150)`, `(100,400)`, `(100,10000)`, `(1000,100)`, drawn from axes page sizes {1, 10, 100, 400, 1000} × history depths {100, 150, 400, 1000, 10000} (per AC-3.2). For each (page_size, history_depth) cell, measure wall-clock latency + peak RSS delta + transfer bytes. The acceptance criterion is: variance across history_depth (holding page_size fixed) is ≤ noise floor (e.g. < 10% relative). v1's bench (150 msgs 63.9→1.9 ms; 400 msgs 510→4.5 ms) is the baseline — v2 MUST demonstrate the same property at the larger scale.

- **Rationale:** A single page-size test cannot distinguish "history-coupling fixed" from "everything got faster". The variance test is what proves the property.
- **Priority:** Must.
- **Notes:** Reuse v1's bench harness shape if one exists (none found in v1 tip's `tests/performance/`); otherwise build from scratch using `time.perf_counter()` + `tracemalloc` for RSS. The 10000-history-depth cells MUST run on real PG (file-backed SQLite is too slow to be representative).

**FR-4 (armed-absence test for `saver.alist`):** Every test in v2's PR5 suite that exercises a message-API path MUST patch the saver's `alist` with an ARMED mock — a mock whose invocation triggers a hard test failure (e.g. `side_effect=AssertionError("alist called on live path")`), NOT a counter. The test fails LOUDLY if alist fires, regardless of whether the test's other assertions pass. Three layers (per PR3 review doc §1.1.2): (1) the test fixture monkey-patches `AsyncPostgresSaver.alist`; (2) every test in the PR5 file uses the fixture; (3) the assertion is `with pytest.raises(AssertionError): ...` is REJECTED — the test simply fails via `assert_called` semantics.

- **Rationale:** Counter-based "alist count == 0" passes when the call site is mocked away. Armed absence passes ONLY when alist is provably unreachable on the live path.
- **Priority:** Must.
- **Notes:** The repo-wide grep `daemon/**/*.py` excluding `daemon/migrations/checkpoint_migrator.py` MUST contain zero `.alist(` call sites — this is the AST guardrail FR-7 layered with FR-4.

### Theme: Observability per §32 (FR-5, FR-6)

**FR-5 (structured saver-op logging + metric surface):** Every saver op on the live path (currently `aget`, `aput`, `adelete`, `alist` [migration-only]) MUST emit one log line: `op=<name> latency_ms=<int> bytes=<int>`. Gated by `CHECKPOINT_PERF_LOGS=1` (matches v1's gating pattern). Metrics surface MUST expose `message_api_checkpoint_list_total` (counter) and `message_api_saver_op_latency_seconds` (histogram, labels: `op`).

- **Rationale:** §32 source-doc imperative: "expected normal value: 0" for `message_api_checkpoint_list_total`; per-op latency is the basis for any future SLO.
- **Priority:** Must.
- **Notes:** v1's `daemon/services/checkpoint_perf.py` (verified in v1 tip tree) emits the observed alist count but not the per-op surface. v2 extends to the full surface. **NEVER** wrap the tap emit in `except BaseException:` (CancelledError must propagate on Python 3.13; same rule as `daemon/services/message_tap.py:146-220` containment).

**FR-6 (degradation-path warning):** Every code path that falls back to `state.ts` due to a `message_metadata` lookup failure MUST log a WARNING (not info, not debug) including: thread_id, reason category (the literal tokens `{manager_missing, repo_missing, row_absent}` are emitted at `daemon/persistence.py:467-469, 491-496`; the exception path at `daemon/persistence.py:481` emits `reason={type(exc).__name__}`, i.e. the exception's CLASS NAME, e.g. `reason=OperationalError`; the literal token `repo_exception` does NOT appear in any emitted log line). The warning is emitted ONCE PER THREAD (F7 per-thread LRU gate; pre-tap threads are the normal `row_absent` case on the polled `GET /messages` path — see `daemon/persistence.py:458-462` for the rationale and the bounded 1024-entry LRU). Response shape MUST be byte-identical to the non-degraded path (PR3 review doc §1: response shape unchanged invariant).

> **Amendment (2026-09-04 — Fix 2, cpv2 final-gate review 🟡2):** Original FR-6 text mandated a `message_id` field and implied per-`message_id` emission. F7's per-thread LRU gate supersedes the per-`message_id` mandate BY DESIGN — the implemented contract emits ONCE per thread_id (bounded 1024-entry LRU), not per `message_id`. The original "per-message line" wording in the AC-6.1 "Then" clause below is similarly superseded (see F7 reference at `daemon/persistence.py:458-462`). The `repo_exception` token in the original FR-6 enum was settled-then-refined at implementation: the category exists as a LABEL but is emitted as the exception's class name (e.g. `reason=OperationalError`), not as the literal string `repo_exception`. Tests at `tests/unit/persistence/test_checkpoint_perf_logging.py::TestDegradationWarning` (5/5 GREEN) assert this class-name emission contract.

- **Rationale:** PR3 review doc §1 finding: `getattr(manager, "message_metadata_repo", None)` short-circuit is silent today — a future misconfiguration could silently degrade every timestamp with no signal. The warning is the operator's diagnostic.
- **Priority:** Must.
- **Notes:** Catch MUST be `except Exception` (never `BaseException`) — CancelledError propagates. PR3 review doc §1 already cites this rule; FR-6 codifies it.

### Theme: Guardrail per §33 (FR-7)

**FR-7 (import-boundary test, no new CI infra):** AST-based import scan over `daemon/routers/**/*.py` asserting: (1) NO `from langgraph.checkpoint...` import; (2) NO `import langgraph.checkpoint` statement; (3) NO `.alist(` call (receiver-agnostic AST call-func scan). Allowlist path `tools/lint/allowlist.txt` MUST exist and be EMPTY in Phase 1. The test MUST run under standard pytest gates (no new CI infra, per LD-OQ2). Scope: Phase 1 = `daemon/routers/**` only.

- **Rationale:** v1's `tests/integration/test_no_saver_imports_in_routers.py` already exists (verified at v1 tip); v2 MUST (a) re-prove on v2 tip AND (b) extend the call-func scan to cover `.alist(` call sites (v1 covers imports; v2 adds the runtime-call guard per §33 full intent).
- **Priority:** Must.
- **Notes:** Docstring/comment mentions of "LangGraph checkpoint" in prose are NOT violations — only AST-detected `ast.Import`/`ast.ImportFrom` and `ast.Call` with `func.attr == 'alist'`. Mechanism MUST be AST (multi-line/aliased-import robust) — NOT a regex grep.

### Theme: Process Closure (FR-8, FR-9)

**FR-8 (PR4 formal re-review record as artifact):** A new file MUST be added to `.agents/reviewer/memories/` named `2026-09-XX-pr4-blob-prune-race-fold-re-review.md`. The file MUST be a re-review against the `7a7998fe` fold set (commit-message evidence is INSUFFICIENT — the artifact is the audit trail). The re-review MUST verify:

1. **SERIALIZABLE wrap** at `daemon/checkpoint_adapter.py` for the destructive DELETE — confirm pool-acquire-per-attempt, `sqlstate 40001/40P01` retry with `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3` and `50ms·2ⁿ` backoff, exhaustion returns `(0,0)` and skips without raising. Predicate used verbatim.
2. **Honest scope acknowledgment** — confirm the docstring states the wrap converts SSI-detectable conflict classes (deadlocks, two-serializable-participant pivots) to abort-retry, and that the lone-READ-COMMITTED-racer µs-window is NOT eliminated (rw-out-edge is not a dangerous structure).
3. **Atomicity-claim retraction** — confirm module-level RETRACTION note + 3 docstring sites cite `aio.py:82, 280-304` (default pipeline path = two separate implicit transactions) and `aio.py:393-399` (non-pipeline fallback IS atomic).
4. **Runbook §7 disclosure** — confirm intra-process µs-window race sentence + single-process rule scoped to cross-process variant + backup-covers-recovery note.
5. **Bidirectional race coverage** — confirm `TestRealSaverRaceWindow` asserts pre-existing referenced blobs survive interleaved multi-turn aputs + destructive prune byte-equal.
6. **Real-40001 retry test** — confirm two serializable participants, PG itself aborts, retry completes.
7. **Separate-pools harness** — confirm fixture mirrors `create_postgres_checkpointer` (prod topology).
8. **Carry-over 🟡 from original NEEDS_CHANGES verdict** — confirm both follow-ups addressed: (a) bidirectional concurrency coverage, (b) harness topology.
9. **Verdict stamp** — `APPROVED` / `NEEDS_CHANGES` / `BLOCKED` with finding counts.

The artifact MUST be authored by a reviewer (NOT the implementer) and MUST be referenced in the PR4 PR description as a required sign-off attachment.

- **Rationale:** v1 has `7a7998fe` with a "Targeted re-review verdict: SATISFIED, 0 findings" line in the commit message but NO separate artifact. Process gap: no audit trail outside the implementer; no second-pair-of-eyes evidence; no carry-over finding closure check.
- **Priority:** Must.
- **Notes:** FR-8 is the v2 initiative's PRIMARY process-closure deliverable — without it, the destructive-enable gate has no second-pair-of-eyes evidence. The artifact is a HARD prerequisite for any future `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` flip (operator's runbook §1 references the re-review).

**FR-9 (gate-manifest regeneration):** The gate manifest (v1: `tests/integration/gate_suites/GATE_SUITES.txt` referenced — the canonical v1 path; verified by `git ls-tree fc908945 -- tests/integration/gate_suites/GATE_SUITES.txt` returning the blob SHA; manifest was regenerated via `chore(gate)` commits like `fc908945`) MUST be regenerated on the v2 tip and stored as a NEW artifact. The artifact is a (file path, commit SHA) pair: path = wherever the manifest lives in v2; SHA = the v2 tip commit at regeneration time. The manifest MUST list every test file under the four PR-suites (PR2/PR3/PR4/PR5) with: collection count, marker, gate scope (binding/secondary), real-DB requirement (yes/no), skip-on-unreachable behavior.

- **Rationale:** v1's last manifest regen at `fc908945` lists 9/9 GREEN on PG 14.22 (post-`7a7998fe`). v2 has added commits since (e.g. the `wc-wake`/`mission` work that landed on latest per the recent history). Copy-pasting the v1 manifest would silently understate v2's test surface.
- **Priority:** Must.
- **Notes:** The regen MUST be a separate commit (`chore(gate): regen manifest at <sha>` convention). The manifest MUST be regenerated per closure cycle (PR5 closure, any subsequent PR4-fix closure) — NOT once at the start of v2.

### Theme: Deferred Items (FR-10 through FR-13)

**FR-10 (deferred-items checklist with classification):** The requirements MUST include a deferred-items checklist (separate file or section in this doc) with per-item: verify-now vs defer-with-signoff classification, owner-facing verification steps, "NEVER write to ensemble_prod" exclusion note.

- **Rationale:** v1's approver tracking iteration 001 lists three deferred-by-design items; none have been verified. v2 cannot ship without a documented disposition for each.
- **Priority:** Must.
- **Notes:** See the Deferred-Items Checklist section below.

**FR-11 (prod `channel_versions` JSONB shape verification, READ-ONLY):** Per v1 runbook §2: query `SELECT jsonb_pretty(checkpoint->'channel_versions') FROM checkpoints WHERE thread_id = '<representative>' ORDER BY checkpoint_id DESC LIMIT 5;` AND the round-trip blob-row count. Verification path options: (A) execute the query read-only on `ensemble_prod`; (B) defer with sign-off and document the reason. The query MUST NEVER be executed against any non-`ensemble_prod` DB (no test DB writes masquerading as prod verification). The result MUST be filed as evidence (or the deferral reason documented).

- **Rationale:** The §9 source-doc warning is explicit: "do NOT assume the JSONB shape". v1's destructive-enable runbook §2 already enumerates this query verbatim — v2 MUST reuse it.
- **Priority:** Must (verify-now strongly preferred; defer-with-signoff only with documented reason).
- **Notes:** Query is read-only SELECT — no `INSERT`/`UPDATE`/`DELETE` against `ensemble_prod`. Result is operator-facing evidence, not a test result.

**FR-12 (seq-index decision at prod volume):** v2 MUST capture INSERT rate at the binding-gate PG instance (estimated 7-day row count) and decide: (a) add `ix_message_metadata_seq` index NOW; (b) defer to Phase 2 wire-up with a documented expected row-count threshold for triggering. The decision MUST be evidence-based with explicit cost numbers (index size, INSERT overhead %, SELECT speedup at Phase 2 query patterns).

- **Rationale:** v1 D5/D9 chose nullable-with-no-default + NO index in Phase 1. At prod write volumes the unindexed seq could become a Phase-2 wire-up hazard.
- **Priority:** Should.
- **Notes:** v1's `seq` column is currently nullable with no default; ADD COLUMN with no default is cheap (no PG row rewrite). ADD INDEX is the cost driver — measure it.

**FR-13 (`is_retry` re-tap drift test, pause→resume→revive):** v2 MUST add a test that:
1. Starts a turn; tap fires at `_build_graph_input` (entry site, `user_message_entry`).
2. Pauses mid-turn between `tap_node_return` await and node return (per PR2 review doc §3 "over-record property").
3. Resumes via `is_retry=True` (per `daemon/services/instance_messaging.py` reuse-revive semantics: COMPLETED→RUNNING auto-transition with checkpoint reuse).
4. Asserts NO `message_metadata` row is MISSING (the never-under-record invariant) — i.e., every persisted message after revive has a corresponding `message_metadata` row.
5. The test MUST run on real PG (file-backed SQLite insufficient — revive path touches the saver's aget/aput surface).

- **Rationale:** PR2 review doc §3 documents the over-record property (benign) and the never-under-record invariant (load-bearing for PR3's join). The invariant must be pinned across the revive path — a regression here would silently degrade timestamps for resumed instances.
- **Priority:** Must.
- **Notes:** This test is the missing piece from v1's approver iteration 001 deferred items. The "never under-records" claim is currently documented in `daemon/services/message_tap.py` but not test-pinned across revive.

### Theme: Out of Scope (FR-14)

**FR-14 (out-of-scope enumeration with explicit backfill criteria):** The v2 initiative MUST NOT include: cursor pagination (frontend blast radius); agent_messages/agent_events durable store; shallow saver (upstream `ShallowPostgresSaver` DEPRECATED + no SQLite equivalent — hard owner exclusion); LZ4/TOAST compression; thread rotation; artifact OOB storage; backfill (Solution N — the owner flagged as OPTIONAL final phase ONLY IF cheap).

For backfill (Solution N), v2 MUST define explicit "coverage sufficient" criteria:
- Criterion A: `state.ts` fallback on PR3 already covers pre-side-table messages (any checkpoint created BEFORE the C2 migration landed). Result: those messages display with their state.ts timestamp, not their first-appearance timestamp.
- Criterion B: A pre-side-table message's `state.ts` is "close enough" to first-appearance IF the next checkpoint was created within `Δt` of the message.
- Criterion C: Define `Δt` empirically — measure the gap on real PG at the binding gate (one representative thread, 10 messages, observe the delta).
- If corrected Criteria A′ AND B′ AND C′ (architect §2.4) are ALL true → backfill is DROPPED. Otherwise → backfill is the bounded, operator-initiated offline-only shape with explicit cost/blast analysis (per T5.14).

- **Rationale:** Without explicit criteria, a future reviewer can invent "we need backfill for completeness" as an excuse. With criteria, the decision is forced.
- **Priority:** Must.
- **Notes:** The criteria are pre-emptive — they prevent the backfill from sneaking into v2 scope.

---

## Non-Functional Requirements

| ID | Category | Requirement | Metric | Target | Measurement |
|----|----------|-------------|--------|--------|-------------|
| NFR-1 | Performance | `GET /instances/{id}/messages` wall-clock latency at 1000-checkpoint history, page size 100 | ms | < 50 ms (v1 baseline: 4.5 ms at 400 msgs — v2 scales linearly with page size) | `tests/performance/test_message_api_cost.py` matrix |
| NFR-2 | Performance | Peak RSS delta during `GET /messages` at 1000-checkpoint history | MB | < 50 MB (v1 baseline: 762 KB transfer) | `tracemalloc` peak in the perf harness |
| NFR-3 | Performance | Transfer size at 1000-checkpoint history, page size 100 | bytes | < 1 MB (v1 baseline: 206 MB pre-fix; 762 KB post-fix at 100 msgs) | response body byte count |
| NFR-4 | Correctness | The perf improvement holds across history depths {100, 150, 400, 1000, 10000} (the executed matrix's depth axis) | variance | < 10% relative across depths {150,400,10000} at page_size=100 (the variance-anchor column) — **dispatcher adjudication 2026-09-04, Option a**: gated metric is the aget/DB-exec component (`_measure_aget_component` over N_TIMED iterations) AFTER `ANALYZE checkpoints / checkpoint_blobs / checkpoint_writes` precondition; threshold is OR-logic `rel_var < 0.10 OR abs_delta < 1.0 ms`; wall-clock end-to-end stays reported (not gated). Honest-red `98d0df49` (variance-cell realism + N_TIMED=10) superseded by the new-commit green — see `phase5-perf-depth-diagnosis.md` §Executive Root-Cause + `phase5-perf-results.md` §AC-3.2 RESOLUTION. | bench matrix (FR-3) |
| NFR-5 | Safety | Destructive DELETE on `checkpoint_blobs` MUST NOT delete a blob referenced by a remaining checkpoint | byte-equality | 100% survival across all real-saver binding-gate tests | `tests/integration/checkpoint_prune_real_saver.py::TestRealSaverRaceWindow` (v1) |
| NFR-6 | Safety | Destructive DELETE MUST abort-and-retry on SSI conflict | sqlstate | 40001/40P01 detected, retry succeeds, exhaustion skips | `tests/integration/checkpoint_prune_real_saver.py::TestSerializableRetryPath` (v1) |
| NFR-7 | Safety | The lone-READ-COMMITTED-racer µs-window MUST be acknowledged in code comments and runbook | docs presence | `aio.py:82, 280-304, 393-399` cited verbatim; runbook §7 disclosure present | grep on `daemon/services/checkpoint_prune.py` + `docs/runbooks/checkpoint-blob-prune-restore.md` |
| NFR-8 | Observability | Every saver op emits one structured log line | log-line count | ≥ 1 per op per call | grep `[CheckpointPerf] op=` log lines + `CHECKPOINT_PERF_LOGS=1` |
| NFR-9 | Observability | Every `state.ts` fallback emits a WARNING with reason category | warning count | ≥ 1 per fallback event | caplog assert in tests/integration |
| NFR-10 | Maintainability | Gate manifest regenerated per closure cycle | artifact count | ≥ 1 per closure phase | git log `chore(gate)` commits |
| NFR-11 | Auditability | PR4 re-review exists as standalone artifact with carry-over 🟡 finding closure check | file presence + content | file at `.agents/reviewer/memories/2026-09-XX-pr4-blob-prune-race-fold-re-review.md` exists with all 9 verification items + verdict stamp | file read |
| NFR-12 | Reliability | `message_metadata` re-tap under `is_retry` revive MUST NEVER produce a missing row | invariant | 0 missing rows across all test turns | `tests/integration/test_message_metadata_retry_recovery.py` (new in v2 per FR-13) |
| NFR-13 | Compatibility | File-backed SQLite recipe: tmp_path + NullPool + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=10000`; NEVER `StaticPool + WriteGuardSession` (QUARANTINE.md write-corruption pattern) | recipe match | 100% match | grep test fixtures |
| NFR-14 | Compatibility | Dev deps installed via plain `uv sync` (PEP 735 `[dependency-groups].dev`); `uv sync --extra dev` is OBSOLETE | install command | `uv sync` works; no `--extra dev` flag in any doc | grep docs/scripts |

---

## Constraints

| ID | Type | Description | Source | Impact |
|----|------|-------------|--------|--------|
| C-1 | Technical | Branch is `feature/langgraph-checkpoint-perf-v2` @ `2f80d45b`. NO merge to `latest` (user reviews first) | dispatcher task | restricts v2 work to branch; no fast-forward to latest |
| C-2 | Technical | v1 branch `feature/langgraph-checkpoint-perf` @ `c37c870c` is READ-ONLY | dispatcher task | inspections only via `git show c37c870c:<path>`; no edits, no new commits |
| C-3 | Operational | NO writes to `ensemble_prod` — read-only query for FR-11 verification only | v1 runbook + dispatcher constraint | restricts deferred-item verification to SELECT queries |
| C-4 | Operational | NEVER `git add -A`, `git add .`, `git commit -a` — explicit paths only | dispatcher task | forces per-file staging; prevents accidental user-work staging |
| C-5 | Operational | NEVER stage `.agents/shared/planning/job-task-retrospective/` or `.agents/shared/planning/defer-gate-fix/` (user's live uncommitted work per `git status`) | dispatcher task | protects user's in-flight work |
| C-6 | Testing | 5 pre-existing quarantined `TestAccessMemoryArchive` failures on `latest` are known and NOT blockers (per QUARANTINE.md) | v1 approver iteration 001 notes + QUARANTINE.md | excludes those 5 from v2 success criteria |
| C-7 | Testing | Dev deps installed via bare `uv sync` (PEP 735 `[dependency-groups].dev`); `uv sync --extra dev` is OBSOLETE | critical note (commit `c983637a` 2026-08-24) | forces plain `uv sync` in any v2 setup doc |
| C-8 | Architectural | No custom ToolNode wrapper (Critical 4); ToolNode registered raw | v1 D10/D18 | rules out any tap under `daemon/graph.py:5546` ToolNode block |
| C-9 | Architectural | No new CI infra; standard pytest gates only (LD-OQ2) | v1 D7/D13/LD-OQ2 | rules out new CI hooks; gates run under existing `addopts` (`-m 'not integration and not postgres'`) for unit, real-PG for binding |
| C-10 | Architectural | Repository pattern mandatory in routers; no raw SQL | v1 conventions + blueprint | rules out direct `langgraph.checkpoint` access in `daemon/routers/**` |
| C-11 | Architectural | 4-tap-site inventory is binding (D1/D19/D20); no 5th tap | v1 decisions | rules out adding a tap for the `ainvoke` direct path (B1 accepted-degradation OOS) |
| C-12 | Architectural | `message_metadata` idempotency via `ON CONFLICT DO NOTHING` (first-appearance wins) | v1 D3 | rules out any UPSERT that overwrites `created_at` |
| C-13 | Architectural | `RemoveMessage` markers MUST be filtered before INSERT (D17) | v1 D17 | rules out inserting rows for `type=='remove'` |
| C-14 | Architectural | Tap call sites are bare awaits BY DESIGN (CancelledError propagates on Python 3.13) | v1 PR2 review doc §1 | rules out wrapping tap sites in `except BaseException:` — must be `except Exception:` only |
| C-15 | Architectural | `create_all` dual-driver convention is INTENTIONAL (one canonical SQL file + PG ensure path on boot) | v1 D2 leader-wording | rules out divergent migrations on SQLite vs PG |
| C-16 | Source | v1 source docs: `~/Downloads/langgraph-checkpoint-performance-discussion.md` §32 (observability), §33 (guardrail) | dispatcher task | v2 §32/§33 requirements MUST trace back to source-doc imperatives |
| C-17 | Source | v1 review docs (`.agents/reviewer/memories/2026-08-26-pr{2,3,4}-*.md`) — carry-over 🟡 findings MUST be closed | v1 approver iteration 001 | FR-8 verification items 8 covers this |
| C-18 | Source | v1 approver tracking (`.agents/approver/langgraph-checkpoint-perf-tracking.md`) — three deferred-by-design items | v1 approver iteration 001 | FR-10/11/12/13 derive from this |
| C-19 | Source | v1 destructive-enable runbook (`docs/runbooks/checkpoint-blob-prune-restore.md`) — destructive flip gated by runbook §1-§7 checklist | v1 runbook | FR-8 + NFR-7 derive from this; operator's flip path is unchanged |
| C-20 | Process | v2 process closure exists BECAUSE v1 stopped at `c37c870c` ("saving on going work, we back later, need focus another hot fix") | v1 c37c870c commit message | the process gap is the v2 initiative's primary purpose |

---

## Acceptance Criteria

### FR-1: PR5 binding gate on real PG

**AC-1.1** (binding gate green)
- **Given:** v2 tip HEAD on `feature/langgraph-checkpoint-perf-v2`, PG 14.22 reachable at `tests/helpers/checkpoint_prune_pg.py::ADMIN_DSN`, `CHECKPOINT_BLOB_PRUNE_DRY_RUN=0` + `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` armed via monkeypatch
- **When:** `pytest tests/integration/checkpoint_prune_real_saver.py -v` runs
- **Then:** 9/9 GREEN (matching v1's v1-`7a7998fe` baseline); ZERO skips; SKIP-LOUDLY only when PG unreachable (skip does NOT count as GREEN)
- **Test type:** integration (real PG, binding gate)

**AC-1.2** (PR2/PR3/PR4 suites green)
- **Given:** v2 tip HEAD, same PG setup
- **When:** `pytest tests/integration/test_message_metadata_hook_placement.py tests/unit/persistence/test_get_instance_messages_no_alist.py tests/integration/checkpoint_prune_real_saver.py -v` runs
- **Then:** AST gate (4-site/4-label/no-ToolNode contract) GREEN; alist-absence test (armed-absence semantics) GREEN; binding gate GREEN
- **Test type:** integration + unit mix

**AC-1.3** (skip is not green)
- **Given:** PG unreachable (e.g. ADMIN_DSN points to a closed port)
- **When:** the binding gate runs
- **Then:** `pytest.skip(...)` is raised with the v1 honesty-contract message; CI exits with skip-status (not pass); operator must NOT proceed to merge or enable
- **Test type:** integration

### FR-2: invariant `message_api_checkpoint_list_total == 0`

**AC-2.1** (observed count disappears)
- **Given:** N=10 random thread ids with non-empty checkpoint history (≥ 100 checkpoints each)
- **When:** `GET /instances/{id}/messages` runs against each thread; the test captures the metric value post-call
- **Then:** the captured value is 0 for all N threads; v1 baseline (≥1 per thread) collapsed
- **Test type:** integration (real PG; mock-saver insufficient)

**AC-2.2** (vacuous-literal regression guard)
- **Given:** the metric implementation in v2
- **When:** code review (FR-8 re-review) inspects the assertion
- **Then:** the assertion is `assert captured_count == 0`, NOT `assert True` or `assert CAPTURED_VALUE == 0` with a hardcoded literal — verified by AST scan for `assert ... == 0` patterns paired with the metric capture site
- **Test type:** manual (re-review artifact per FR-8)

**AC-2.3** (every message-API endpoint covered)
- **Given:** every endpoint under `daemon/routers/instances.py` and any other message-listing endpoint
- **When:** PR5 enumerates the endpoints
- **Then:** each endpoint is in the PR5 test table; the metric is captured at each
- **Test type:** integration

### FR-3: perf assertion — cost ∝ page size, NOT history

**AC-3.1** (bench matrix runs)
- **Given:** `tests/performance/test_message_api_cost.py` executed matrix = 6 cells (page_size, history_depth): `(1,10000)`, `(10,1000)`, `(100,150)`, `(100,400)`, `(100,10000)`, `(1000,100)` (drawn from axes {1,10,100,400,1000} × {100,150,400,1000,10000} — per AC-3.2)
- **When:** the harness runs against real PG (file-backed SQLite is too slow for 10000-history cells)
- **Then:** each cell produces (latency_ms, peak_rss_bytes, transfer_bytes)
- **Test type:** performance (real PG)

**AC-3.2** (history-coupling variance bound)
- **Given:** AC-3.1 results; the executed matrix is 6 cells drawn from axes page_size × history_depth = `{1,10,100,400,1000} × {100,150,400,1000,10000}`: `(page_size=1, history_depth=10000)`, `(10,1000)`, `(100,150)`, `(100,400)`, `(100,10000)`, `(1000,100)` (per adversarial-review blocker-2 + dispatcher resolution option A — see phase5-plan.md T5.5 + plan-overview.md SC-3)
- **When:** variance across history_depth is computed at page_size=100 across depths {150,400,10000} (the variance-across-depth anchor column at fixed page_size)
- **Then:** relative variance < 10% across depths {150,400,10000} at page_size=100 — proves the property
- **Test type:** performance

> **Measurement basis (dispatcher adjudication 2026-09-04, Option a) —
> additive, 2026-09-04; honest-red history at `98d0df49` (variance-cell
> realism + N_TIMED=10) stays untouched.** The load-bearing metric is
> the **aget/DB-exec component** (`_measure_aget_component` over
> N_TIMED iterations) across depths {150, 400, 10000} at page_size=100,
> NOT wall-clock end-to-end. The harness issues `ANALYZE checkpoints /
> checkpoint_blobs / checkpoint_writes` AFTER `_populate_thread` and
> BEFORE every measurement (`_analyze_after_populate` in
> `tests/performance/test_message_api_cost.py`); the depth-spread
> carrier was identified as a planner-cache artifact on the saver's
> prepared statement (generic-plan seq-scan over `checkpoint_blobs`
> under stale/absent stats) plus a ±2–6 ms process-noise floor —
> see `phase5-perf-depth-diagnosis.md` §Executive Root-Cause for the
> 8.557 ms → 0.064 ms collapse at depth 10000 post-ANALYZE.
>
> The threshold rule is `pass if EITHER (rel_var < 0.10) OR
> (abs_delta < 1.0 ms)` — relative is plan-faithful, absolute is the
> sub-ms fallback when estimator noise dominates the relative CoV even
> though the depth-spread is bounded (1.0 ms ≫ 6× the observed
> pkey-probe floor). Wall-clock per cell stays printed + recorded in
> `phase5-perf-results.md` but is NOT the load-bearing metric.

**AC-3.3** (baseline re-confirmation)
- **Given:** v1 baseline: 150 msgs 63.9→1.9 ms (33×); 400 msgs 510→4.5 ms (114×)
- **When:** v2 bench runs at the same data points (150 msgs, 400 msgs); cells `(page_size=100, history_depth=150)` and `(page_size=100, history_depth=400)` are the anchor cells (per the restored 6-cell matrix)
- **Then:** v2 numbers at cells `(100,150)` and `(100,400)` are within 2× of v1's post-fix numbers (the "post-fix" side, NOT the pre-fix pathology) — regression guard against the fix rotting
- **Test type:** performance

### FR-4: armed-absence test for `saver.alist`

**AC-4.1** (armed mock setup)
- **Given:** every test in v2's PR5 suite
- **When:** the test fixture applies
- **Then:** the saver's `alist` is monkey-patched with `AsyncMock(side_effect=AssertionError("alist called on live path"))`
- **Test type:** unit (fixture)

**AC-4.2** (zero call sites on live path)
- **Given:** `daemon/**/*.py` excluding `daemon/migrations/checkpoint_migrator.py`
- **When:** AST call-func scan runs (`func.attr == 'alist'`)
- **Then:** ZERO matches; the migration-only path is excluded by an explicit allowlist entry or by file-pattern skip
- **Test type:** unit (AST scan)

**AC-4.3** (migration-only path still works)
- **Given:** `daemon/migrations/checkpoint_migrator.py` is in scope for alist calls
- **When:** the offline migrator runs
- **Then:** alist IS called (NOT armed) — the test for the migrator uses a normal mock, not the armed one
- **Test type:** integration (offline migrator)

### FR-5: structured saver-op logging + metric surface

**AC-5.1** (every op emits one log line)
- **Given:** `CHECKPOINT_PERF_LOGS=1`, ops `aget`/`aput`/`adelete`/`alist`(migration-only)
- **When:** each op runs
- **Then:** exactly one log line `[CheckpointPerf] op=<name> latency_ms=<int> bytes=<int>` is emitted
- **Test type:** integration (caplog assert)

**AC-5.2** (metric surface exposed)
- **Given:** the v2 metrics surface (Prometheus-style endpoint OR daemon internal collector per project convention)
- **When:** `GET <metrics>` is called
- **Then:** `message_api_checkpoint_list_total` (counter, expected value 0) AND `message_api_saver_op_latency_seconds` (histogram, labels: `op`) are exposed
- **Test type:** integration

**AC-5.3** (no `except BaseException:` wrap)
- **Given:** the emit code path
- **When:** code review (FR-8 re-review or v2 PR review) inspects
- **Then:** NO `except BaseException:` wraps the emit; CancelledError propagates by design (matches `daemon/services/message_tap.py:146-220` containment rule)
- **Test type:** manual (re-review artifact)

### FR-6: degradation-path warning

**AC-6.1** (warning emitted per fallback)
- **Given:** the `message_metadata` lookup raises an exception (or repo is None)
- **When:** `GET /instances/{id}/messages` runs
- **Then:** one WARNING log line per affected thread (F7 per-thread LRU gate — see `daemon/persistence.py:458-462`; F7 supersedes the original per-message mandate BY DESIGN) with: thread_id, reason category where the emitted literal-token set is `manager_missing` | `repo_missing` | `row_absent` and the exception path emits the exception's class name (`reason={type(exc).__name__}`, e.g. `reason=OperationalError`; the literal token `repo_exception` is NOT emitted — it exists only as the test-case label for the class-name emission contract at `daemon/persistence.py:481`); response shape byte-identical to non-degraded path
- **Test type:** integration (caplog assert)

**AC-6.2** (`except Exception:` only)
- **Given:** the catch site
- **When:** code review inspects
- **Then:** catch is `except Exception:` — NEVER `except BaseException:` (CancelledError must propagate); NEVER bare `except:` (C-14)
- **Test type:** manual

### FR-7: import-boundary test, no new CI infra

**AC-7.1** (no imports / no alist calls under `daemon/routers/`)
- **Given:** v2 tip HEAD
- **When:** `pytest tests/integration/test_no_saver_imports_in_routers.py -v` runs
- **Then:** 0 violations; AST scan returns empty list; allowlist `tools/lint/allowlist.txt` is empty (Phase 1 scope)
- **Test type:** integration (LD-OQ2 standard gate)

**AC-7.2** (extends to runtime call sites)
- **Given:** v2 tip HEAD
- **When:** the AST call-func scan in the same test file runs
- **Then:** ZERO `.alist(` calls in `daemon/routers/**` (v1 covered imports; v2 adds runtime-call guard per §33 full intent)
- **Test type:** integration

**AC-7.3** (no new CI infra)
- **Given:** the test file's `pytest.mark.*` decorators
- **When:** the standard test gates run (`-m 'not integration and not postgres'`)
- **Then:** the test executes under existing infra (NO new CI hook, NO new runner)
- **Test type:** integration (LD-OQ2 compliance)

### FR-8: PR4 formal re-review record as artifact

**AC-8.1** (artifact exists at expected path)
- **Given:** v2 closure phase
- **When:** filesystem is inspected
- **Then:** `.agents/reviewer/memories/2026-09-XX-pr4-blob-prune-race-fold-re-review.md` exists (replace `XX` with actual day)
- **Test type:** manual (file presence)

**AC-8.2** (artifact covers all 9 verification items)
- **Given:** the artifact file
- **When:** content is reviewed
- **Then:** items 1-9 of FR-8 (SERIALIZABLE wrap, honest scope, atomicity retraction, runbook disclosure, bidirectional race, real-40001 retry, separate-pools harness, carry-over 🟡, verdict stamp) are each present with explicit verification status (PASS/FAIL/N/A)
- **Test type:** manual

**AC-8.3** (verdict stamp present)
- **Given:** the artifact
- **When:** content is reviewed
- **Then:** the verdict stamp (`APPROVED` / `NEEDS_CHANGES` / `BLOCKED`) appears with finding counts
- **Test type:** manual

**AC-8.4** (author is NOT the implementer)
- **Given:** the artifact
- **When:** authorship is checked
- **Then:** the author is a reviewer (NOT the implementer of `7a7998fe`); git author + reviewer-instance-id verified
- **Test type:** manual

### FR-9: gate-manifest regeneration

**AC-9.1** (manifest regenerated per closure cycle)
- **Given:** v2 closure phase
- **When:** git log is inspected
- **Then:** at least one `chore(gate): regen manifest at <sha>` commit exists in v2 (post-v1-tip)
- **Test type:** manual

**AC-9.2** (manifest contents enumerate v2 test files)
- **Given:** the regenerated manifest
- **When:** content is reviewed
- **Then:** every test file under PR2/PR3/PR4/PR5 suites is listed with: collection count, marker, gate scope (binding/secondary), real-DB requirement, skip-on-unreachable behavior
- **Test type:** manual

**AC-9.3** (no copy-paste from v1)
- **Given:** the regenerated manifest
- **When:** content is reviewed
- **Then:** the test counts and file paths match v2's tip (NOT v1's `fc908945` baseline); mismatch is a process regression
- **Test type:** manual

### FR-10: deferred-items checklist

**AC-10.1** (checklist artifact exists)
- **Given:** v2 closure
- **When:** the deferred-items checklist is produced
- **Then:** a per-item table with verify-now vs defer-with-signoff, owner-facing verification steps, "NEVER write to ensemble_prod" exclusion is present (see Deferred-Items Checklist section in this doc)
- **Test type:** manual (artifact presence)

### FR-11: prod `channel_versions` JSONB shape verification

**AC-11.1** (read-only query runs OR defer-with-signoff documented)
- **Given:** operator has access to `ensemble_prod` read replica
- **When:** v2 closure runs the runbook §2 query (read-only SELECT only)
- **Then:** evidence is filed (jsonb_pretty output + blob-row round-trip count) OR an explicit defer-with-signoff entry exists with documented reason
- **Test type:** operational (operator-facing; not a pytest)

**AC-11.2** (query never writes)
- **Given:** the runbook §2 query
- **When:** query is executed
- **Then:** ZERO write operations touch `ensemble_prod`; SELECT only; verifiable by audit log
- **Test type:** operational

### FR-12: seq-index decision

**AC-12.1** (evidence-based decision filed)
- **Given:** v2 closure
- **When:** the seq-index decision is made
- **Then:** the decision (add-now / defer-to-Phase-2) is filed with: estimated 7-day row count from the binding-gate PG instance, index size, INSERT overhead %, Phase-2 SELECT speedup
- **Test type:** manual

### FR-13: `is_retry` re-tap drift test

**AC-13.1** (test pinned across revive)
- **Given:** a turn that pauses mid-tap, resumes via `is_retry=True` (COMPLETED→RUNNING revive with checkpoint reuse)
- **When:** the new test (`tests/integration/test_message_metadata_retry_recovery.py`) runs against real PG
- **Then:** every persisted message has a corresponding `message_metadata` row (never-under-record invariant); 0 missing rows across the turn + pause + resume + revive
- **Test type:** integration (real PG)

**AC-13.2** (over-record tolerance preserved)
- **Given:** a turn with a pause-between-tap-and-node-return (PR2 review doc §3 property)
- **When:** the test runs
- **Then:** ghost rows (for un-checkpointed messages) are tolerated (the existing over-record tolerance test passes); the never-under-record invariant is the new constraint
- **Test type:** integration

**AC-13.3** (read→revive→read on COMPLETED instance — message_metadata survives revive)
- **Given:** a thread has been populated and tapped; `message_metadata` rows exist; the instance has reached COMPLETED status (revive-eligible per `instance_messaging.py` reuse-revive semantics, cardinal #2 scoping discipline)
- **When:** a first read is captured (pre-revive snapshot), then a `send_message` is issued to revive (COMPLETED→RUNNING transition via the reuse-revive path), then a second read is taken (post-revive snapshot)
- **Then:**
  - pre-revive snapshot is byte-identical to the post-revive snapshot's message list (same message ids, same content order, same byte-equality)
  - `message_metadata` rows survive revive (the second read sees the same `message_id → (created_at, seq)` map; no row is missing as a side-effect of the revive transition)
  - both reads show `alist_count == 0` (FR-2 invariant preserved across the revive; the second read does NOT regress to the alist walk)
  - synthetic-system message id `synthetic-system-{iid}` is identical in both reads (deterministic id contract)
- **Test type:** integration (real PG; revive-via-send_message with checkpoint reuse)
- **Architect guardrail:** row 2 of architect §5 Guardrail Checklist — "COMPLETED revive-on-send survives read flip" — ensures the never-under-record invariant holds across the COMPLETED→RUNNING revive path

### FR-14: out-of-scope enumeration with backfill criteria

**AC-14.1** (criteria defined and decision forced)
- **Given:** v2 closure
- **When:** backfill disposition is decided
- **Then:** corrected Criteria A′/B′/C′ from FR-14 (architect §2.4) are evaluated against real-PG measurement; backfill is DROPPED if all three are true, otherwise documented as the offline-only shape with cost/blast analysis
- **Test type:** manual

**AC-14.2** (out-of-scope list explicit)
- **Given:** v2 docs
- **When:** scope is reviewed
- **Then:** cursor pagination, agent_messages/agent_events durable store, shallow saver (with DEPRECATION rationale), LZ4/TOAST, thread rotation, artifact OOB storage are EACH enumerated with "out-of-scope + rationale" — not just a one-line "future work"
- **Test type:** manual

---

## Deferred-Items Checklist

| # | Item | Classification | Owner-facing Verification Steps | "NEVER write to ensemble_prod" Exclusion | Source |
|---|------|---------------|--------------------------------|----------------------------------------|--------|
| **D-1** | Prod `channel_versions` JSONB shape verification | **Verify-now (preferred)** OR defer-with-signoff with documented reason | Run `docs/runbooks/checkpoint-blob-prune-restore.md` §2 query verbatim against `ensemble_prod`: (1) `SELECT jsonb_pretty(checkpoint->'channel_versions') FROM checkpoints WHERE thread_id = '<representative>' ORDER BY checkpoint_id DESC LIMIT 5;` (2) round-trip blob-row count via `jsonb_each_text` join. File both outputs as operator-facing evidence. | READ-ONLY SELECT only; ZERO writes. Audit-log verifiable. | v1 approver iteration 001; runbook §2 |
| **D-2** | Seq-index decision at prod volume | **Verify-now** (cheap to measure) | (1) Capture INSERT rate at the binding-gate PG instance over a 1-hour window (`pg_stat_user_tables` on `message_metadata`). (2) Project 7-day row count. (3) Measure `CREATE INDEX CONCURRENTLY ix_message_metadata_seq` cost: index size (pg_relation_size), INSERT overhead % (pg_stat_statements or simple A/B), Phase-2 SELECT speedup estimate. (4) Decide add-now vs defer-to-Phase-2 with explicit numbers. | No write to `ensemble_prod`; the measurement can be on a fresh DB or the binding-gate PG. | v1 D5/D9 |
| **D-3** | `is_retry` re-tap drift (pause→resume→revive) | **Verify-now** (test-pinned via FR-13) | Add `tests/integration/test_message_metadata_retry_recovery.py` per FR-13 AC-13.1/13.2; run against binding-gate PG; assert never-under-record invariant across revive path. | Test uses disposable PG (per binding-gate harness); no write to `ensemble_prod`. | v1 approver iteration 001; PR2 review doc §3 |
| **D-4** | PR4 formal re-review artifact | **Verify-now** (process closure) | Produce `.agents/reviewer/memories/2026-09-XX-pr4-blob-prune-race-fold-re-review.md` per FR-8 AC-8.1-8.4; reviewer NOT implementer; all 9 verification items + verdict stamp. | N/A (process artifact; no DB access). | v1 `7a7998fe` commit-message-only re-review (INSUFFICIENT) |
| **D-5** | Gate-manifest regeneration | **Verify-now** (process closure) | `chore(gate): regen manifest at <v2-sha>` commit; manifest enumerates v2 test files with collection count, marker, gate scope. | N/A (process artifact). | v1 `fc908945` regen (STALE for v2) |

---

## Gaps & Ambiguities

| # | Gap / Ambiguity | Question for Caller | Severity |
|---|-----------------|---------------------|----------|
| 1 | v1 binding-gate PG version: v1 commit-message says "PG 14.22"; v2 has no guarantee the test environment matches. | Is PG 14.22 the canonical version for v2 binding gate, or has it moved? (Critical for NFR-5/NFR-6 binding — different PG versions may have different SSI semantics.) | High |
| 2 | v2 tip's actual test surface is unknown — `git diff feature/langgraph-checkpoint-perf 2f80d45b` shows many v2 commits but the perf/PR suites may have moved. | Does the v2 implementer own a re-discovery pass to confirm which v1 test files exist on v2 tip (and which need porting)? | High |
| 3 | The "page size" semantics: `GET /instances/{id}/messages` accepts `limit` and `before` query params (per v1 blueprint). v1 bench used page sizes 150 and 400 — v2 must define the page size range explicitly. | What is the canonical page size for the v2 perf assertion (default `limit`)? | Medium |
| 4 | The PR4 re-review artifact's author identity: "reviewer NOT implementer" — who is the reviewer? v1's review artifacts are in `.agents/reviewer/memories/`. | Does the planner dispatch a reviewer worker explicitly for FR-8, or is there a default reviewer role? | Medium |
| 5 | The metrics surface: project convention for exposing metrics — Prometheus-style endpoint, daemon internal collector, or both? v1 blueprint doesn't pin this. | What is the canonical metrics surface for v2 to extend (FR-5 AC-5.2)? | Medium |
| 6 | The `state.ts` fallback reason categories (`repo_missing` | `repo_exception` | `row_absent`) are derived from PR3 review doc §1 wording; v1 may have other categories not yet enumerated. | Are these three exhaustive, or should the test cover more (e.g. `conn_timeout`, `schema_drift`)? | Low | **[Settled-then-refined 2026-09-04, Fix 2]:** Implemented literal-token set at `daemon/persistence.py:467-469, 481, 491-496` is `manager_missing` | `repo_missing` | `row_absent`; the exception category is EMITTED as the exception's class name (`reason=<ExceptionClass>`, e.g. `reason=OperationalError`) — the literal token `repo_exception` does NOT appear in any emitted log line. FR-6 amended to match (cpv2 final-gate review 🟡2). |
| 7 | The "5 pre-existing quarantined TestAccessMemoryArchive failures" are explicitly NOT blockers (C-6) — but the v2 test command must exclude them. | Is the QUARANTINE.md exclusion mechanism (`pytest --deselect`) standardized in v2, or is it per-file? | Low |
| 8 | The `seq` index column exists in v1 schema (`D5`) but Phase-2 wire-up is deferred. v2 must decide add-index-now vs defer-with-cost-numbers (D-2). | Is the planner's preference add-now or defer-with-threshold, in absence of prod data? | Medium |
| 9 | The "operator-facing evidence" for FR-11/D-1: file format is undefined — JSON, markdown, raw query output? | What is the canonical evidence format for the runbook §2 query? | Low |
| 10 | The "armed absence" test (FR-4): does the v2 implementer own the monkey-patch fixture, or is there a v1 helper? v1 has `evict_langgraph_mocks` / `restore_langgraph_mocks` (in `tests/helpers/checkpoint_prune_pg.py`) but no "armed alist" helper. | Confirm: v2 implementer owns a new helper, or v1's mock-eviction pattern extends? | Low |
| 11 | The v2 initiative's relationship to the `wc-wake`/`mission` work that landed on `latest` per the recent history (post-v1-tip). Some v1 source files may have moved or been refactored. | Does v2 cherry-pick from latest or branch from `2f80d45b` (current v2 base)? | High |
| 12 | The §32 observability scope: is it just the message-API saver ops, or ALL saver ops (including `aput`)? | What is the full saver-op surface v2 must instrument? | Medium |

---

## Assumptions

| # | Assumption | Reason | Risk if Wrong |
|---|------------|--------|---------------|
| A-1 | v2 branch is `feature/langgraph-checkpoint-perf-v2` @ `2f80d45b` (current tip) — confirmed via `git branch --show-current` + `git log --oneline -1` in reconnaissance. | Dispatcher task + recon output. | If branch moved between recon and execution, all v2 commits may land on a stale tip — would need rebase. |
| A-2 | v1 binding-gate harness (`tests/integration/checkpoint_prune_real_saver.py`, 969 lines, 9/9 GREEN on PG 14.22) is the canonical reference for v2's PR5 — v2 re-runs it as-is on the v2 tip. | v1 commit message `7a7998fe` lines 23-25: "binding gate 7→9 (411)... 9/9 GREEN on real PG 14.22 (implementer ×4 consecutive runs + independent targeted re-review run)". | If v2 tip doesn't include `tests/integration/checkpoint_prune_real_saver.py` (e.g. refactored), v2 must re-derive. |
| A-3 | v1 review docs (`.agents/reviewer/memories/2026-08-26-pr{2,3,4}-*.md`) are the canonical reference for v2 PR4 re-review (FR-8 verification items 1-9). | v1 c37c870c tree contains all three; v1 approver iteration 001 references them. | If v1 review docs were edited post-`c37c870c` (e.g. on a newer commit), the verification items may have shifted. |
| A-4 | The `wc-wake`/`mission` work on `latest` is orthogonal to the perf initiative (per recent history showing them as separate features). | Recent history in critical notes + project history; no perf-related commits in those histories. | If those features touched `daemon/persistence.py` / `daemon/services/message_tap.py` / `daemon/services/checkpoint_prune.py`, v2 must reconcile. |
| A-5 | The PG version for v2 binding gate remains 14.22 (same as v1). | v1 `7a7998fe` commit message + no v2 env-info found. | Different PG versions may have different SSI semantics — could affect NFR-5/NFR-6 binding. (Gap #1.) |
| A-6 | The "page size" range for the perf assertion is {1, 10, 100, 400, 1000} — derived from v1 bench numbers (150/400) + standard API pagination defaults. | v1 bench range; no explicit v2 spec found. | If `GET /messages` has a different default `limit`, the page-size axis may need adjustment. (Gap #3.) |
| A-7 | The executed matrix's history-depth values {100, 150, 400, 1000, 10000} cover the v2 perf assertion (6 cells per AC-3.2) — 10000 is above v1's bench (1000) to prove the property holds at scale. | v1 bench (150/400) at <1000 history; v2 needs to demonstrate scaling. | If 10000-history cells are too slow on the binding-gate PG, the matrix may need to shrink. |
| A-8 | The metrics surface is the existing daemon internal collector (no Prometheus endpoint in v1). | Project blueprint doesn't mention Prometheus; no v1 endpoint found. | If the project has a Prometheus endpoint convention v1 doesn't expose, FR-5 AC-5.2 may need extension. (Gap #5.) |
| A-9 | The `state.ts` fallback reason categories are exactly the three enumerated in Gap #6. | PR3 review doc §1 + v1 message_tap.py docstring; no other categories enumerated. | If v2 discovers additional categories (e.g. `conn_timeout`), the enum may need extension. |
| A-10 | The 4-tap-site inventory is unchanged on v2 tip (no new tap added). | v1 D1/D11/D19/D20 + no v2 tap-modification commit found in reconnaissance. | If v2 added a tap (e.g. for `ainvoke` direct path), the AST gate test count changes (FR-2/FR-7). |

---

## Out of Scope (Deferred)

| # | Item | Reason for Deferral | Decision Criteria (if any) |
|---|------|----------------------|----------------------------|
| **OOS-1** | **Cursor pagination (frontend blast radius)** | v2 is daemon-side only; cursor pagination requires FE changes (project_blueprint notes FE blast radius). | Phase 2+ — when v2 is stable, evaluate cursor pagination as the next perf lever. |
| **OOS-2** | **agent_messages / agent_events durable store** | v2 is read-path perf only; durable store is a schema change beyond v2 scope. | Phase 3+ — when the message API has a separate read store. |
| **OOS-3** | **Shallow saver** (upstream `ShallowPostgresSaver`) | Upstream DEPRECATED; no SQLite equivalent — owner hard exclusion. | HARD EXCLUSION — never re-evaluate unless upstream restores the saver AND ships a SQLite equivalent. |
| **OOS-4** | **LZ4/TOAST compression** | Adds dependency; perf gain unproven at v2's scale (v2 already at <1 MB transfer for 100 msgs). | Phase 3+ — only if a future perf regression shows transfer-size is the bottleneck. |
| **OOS-5** | **Thread rotation** | Schema change; affects all instances; blast radius high. | Phase 3+ — when retention policy evolves. |
| **OOS-6** | **Artifact out-of-band storage** | Storage infra change; orthogonal to perf. | Phase 3+ — when artifact volume justifies separate infra. |
| **OOS-7** | **Backfill (Solution N)** | Owner flagged as OPTIONAL final phase ONLY IF cheap. FR-14 corrected Criteria A′/B′/C′ (architect §2.4) evaluated against real-PG measurement. | DROP if all three true: (A′) PR3's `state.ts` fallback timestamps suffice for UI display of pre-side-table messages (accepted degradation, non-breaking); (B′) no scheduled/batch consumer requires accurate first-appearance timestamps (`created_at` is the only consumer); (C′) the row-growth defect is addressed by the `delete_for_thread` prune, NOT backfill. (Original A/B/C superseded — Criterion B was false on the merits per architect §2.4.) Otherwise → the bounded, operator-initiated offline-only shape with explicit cost/blast analysis. **Offline-shape hardening (adversarial-review W11):** if any A/B/C fails and the offline path via `daemon/migrations/checkpoint_migrator.py` is taken, it is operator-initiated ONLY — requires explicit operator sign-off (documented in `phase5-backfill-disposition.md` §sign-off with operator name + timestamp), bounded batch size (e.g. ≤1000 rows/transaction), explicit time-window or row-count cap, and a hard upper bound (e.g. `MAX_BACKFILL_ROWS=10000` env or operator-set ceiling); every batch MUST log start/end + row count; no operator-initiated path runs in the binding-gate disposable PG. |

---

## Cross-Reference Index (for plan-creation worker)

| Requirement | Source Doc / Section | Constraint Anchors |
|-------------|----------------------|--------------------|
| FR-1, FR-2, FR-3, FR-4 | v1 plan-overview.md §"Phase 1" PR5 row; v1 approver iteration 001 (binding gate note 9) | C-1, C-9, C-13 |
| FR-5, FR-6 | source-doc §32 (observability) | C-14 |
| FR-7 | source-doc §33 (guardrail) + v1 LD-OQ2 + v1 D7/D13 | C-9, C-10 |
| FR-8 | v1 PR4 review doc (NEEDS_CHANGES) + v1 `7a7998fe` (SATISFIED in commit msg ONLY) | C-2, C-19 |
| FR-9 | v1 manifest regen history (`fc908945`, `e3c69b48`, `80c84219`) | C-1 |
| FR-10, FR-11, FR-12, FR-13 | v1 approver iteration 001 "Unverified (deferred by design)" | C-3, C-18 |
| FR-14 | dispatcher task (out-of-scope enumeration + backfill criteria) | C-1 |
| NFR-1..4 | v1 discussion doc §32 (perf baseline 206 MB / 42 s / 2.1 GB RSS) | C-9 |
| NFR-5, NFR-6, NFR-7 | v1 PR4 review doc (race folds) | C-19 |
| NFR-8, NFR-9 | source-doc §32 + v1 PR3 review doc §1 | C-14 |
| NFR-10, NFR-11 | process-closure gap (v1 stopped at `c37c870c`) | C-1, C-20 |
| NFR-12 | v1 PR2 review doc §3 (never-under-record) | C-11, C-12, C-13 |
| NFR-13, NFR-14 | tester conventions (file-backed SQLite recipe, PEP 735) | C-6, C-7 |
