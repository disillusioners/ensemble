# Fix C — FULL Regression Gate — `feature/job-task-fix-c` @ `ab518e0b`

Date: 2026-09-02 (UTC) · Base: `e20d6e48` (latest, post-Fix-B merge) · Range: `e20d6e48..ab518e0b` (5 commits)
Dispatched: 19 worker instances (1 wave-0 env/discovery, 3 acceptance, 8 full-suite partitions, 1 base-worktree 2-phase, 2 read-only audits [additivity + mock], 3 runtime verification [d1d2/d3/d5], 1 ensure.md). Repo READ-ONLY throughout — zero commits by gate; base scratch worktree created + removed cleanly (`git worktree list` = main only, isolation proven via `daemon.__file__` under worktree). Evidence: `/tmp/fixc-gate/` (p1–p8.log, acc-*.log, base-p*.log, phase2/, d{1,3,5}-runtime.log + scripts, mock-audit.log, d4-additivity.log, ensure-concurrency.log, wave0.log).

## FINAL VERDICT: ✅ **PASS (merge-ready)** — 0 branch-caused regressions; 3/3 acceptance sets EXACT; full-suite census 242 pre-existing / 0 caused / 19 context-flakes, all base-evidenced with solo budgets.

Scope: FULL suite run — warranted: final merge gate (release-gate class), read-model split touching 4 daemon router/service files consumed by 4 read surfaces.

---

## 1. Acceptance sets (3/3 EXACT — independent runs)

| Set | Expected | Actual | Result |
|---|---|---|---|
| `bash test/packs/constitution_drift_test.sh` (EXPECTED_BRANCH=feature/job-task-fix-c) | 24P, 23 writers, delta 0, branch guard ENFORCED | **24 passed in 5.64s**, exit 0; guard line verbatim; NO SKIP notice; census introspection `writers 23` | ✅ |
| `tests/unit/services/test_fix_c_read_model_split.py` | 18 | **18 passed in 1.30s**, exit 0, 0 skipped | ✅ |
| Scoped: work_resolver + streaming + work_router (4 files pinned) | 117 (dev claim) | **117 passed in 3.35s** — work_resolver 76 + partial_collapse 11 + jobs_streaming_resolver 9 + work_router 21 | ✅ |

117-set resolution note: candidate discovery excluded `test_llm_streaming_*` (36, LLM-protocol domain) and `test_work_resolver_no_drift_warning.py` (5, edge-case file) — including them overshoots to 122. Constitution census via introspection (stdout-capture quirk carried from Fix B lesson).

## 2. FULL-suite baseline at HEAD (Fix-B 8-partition protocol)

| Partition | Scope | Collected | Passed | Failed | Errors | Skipped | Runtime | vs Fix-B baseline |
|---|---|---:|---:|---:|---:|---:|---:|---|
| P1 | tests/unit subdirs except tools (9 dirs) | 1,582 | 1,574 | 8 | 0 | 0 | 40.2s | +18 collected (= Fix-C file), F count identical |
| P2 | unit/tools + unit/test_[a-k]* | 2,539 | 2,492 | 24 | 21 | 2 | 54.6s | **EXACT parity** (Δ0 all counters) |
| P3 | unit/test_[l-m]* | 1,472 | 1,468 | 3 | 0 | 1 | 178.6s | **EXACT parity** |
| P4 | unit/test_[n-z]* | 2,128 | 2,017 | 59 | 2 | 50 | 34.7s | **EXACT parity** |
| P5 | {job_queue,services,message_queue_redesign,migration} | 2,749 | 2,681 | 2 | 0 | 66 | 71.3s | **EXACT parity** (documented pre-existing pair) |
| P6 | test_[a-j]* + {tools,api,manager,lint,performance,property,static} | 1,769 | 1,675 | 46 | 0 | 48 | 61.1s | **EXACT parity** |
| P7 | test_[k-z]* + {opencode,repositories} | 3,479 | 3,336 | 43 | 0 | 79 (+5xf, 16 desel.) | 111.4s | **EXACT parity** |
| P8 | tests/integration (override, not postgres) | 419 | 351 | 37 | 16 | 1 (14 desel.) | 47.7s | **+2F/−2P — resolved §3, all flake-family** |
| **TOTAL** | | **16,137** | **15,594** | **222** | **39** | **247** (+5xf) | ~10 min summed | Δ+18 collected = Fix-C's 18 new tests (all green); Δ+2F = flake manifestation |

Coverage anchoring: identical partition scopes to Fix-B protocol + per-partition count parity + wave-0 file enumerations (P2 a-k glob=63 files, P4 n-z=96, P6 top-level a-j=54, P7 k-z=80). 🟢 The P8 collect-only sentinel was INCONCLUSIVE (3,222 vs 16,137 sum — partial collection under default addopts; disclosed, not relied upon).

## 3. Per-failure attribution — all 261 F+E classified (scratch worktree @ `e20d6e48`, isolation-proven)

| Verdict | Count | Detail |
|---|---:|---|
| **PRE-EXISTING at base** | **242** | fail/error at base in batch (P1–P7: 208/208; P8: 30 batch + 2 solo-resolved incl. `test_agent_bootstrap_and_hello` whose base-batch pass was a context false-positive — solo 0P/3F at base) |
| **🔴 CAUSED** | **0** | none (no node met HEAD-solo-3/3-fail ∧ base-solo-3/3-pass ∧ base-batch-pass) |
| 🟠 Context-flake (pre-existing, order-sensitive) | **19** | 3 multi_turn_resume + 2 skill_cross_phase_flow_b + 1 workspace_sse + 12 vscode (5 routing + 7 security) + 1 debug_llm_invocation_count — ALL pass solo 3/3 at BOTH commits; fail only in HEAD-batch context (QUARANTINE.md rows 37–38 class, re-verified; membership wobble disclosed) |
| Unexplained / other | **0** | full reconciliation; 261 = 242 + 19 |

**P8 +2 delta (35F→37F) resolved:** of the 37 HEAD failures — 30 base-batch-fail + 1 base-solo-fail (bootstrap) = 31 pre-existing; 6 are flaky-family FAILED-this-batch (3 multi_turn_resume + 2 cross_phase_b + 1 workspace_sse). The +2 vs Fix-B is drawn from the 6-node unstable set — **none caused** (all 6 solo-clean at both commits). 30+1+6=37 ✓.

**Priority-node solo verdicts (P8 suspects):** `test_cold_resume_ttl` ×2 (`'pending'=='cancelled'` resume-cascade), `test_message_queue_e2e` ×3 (LLM connection-refused), `test_pause_race_w7` ×1 — ALL fail solo 3/3 at base AND HEAD → **definitively pre-existing**.

**Reviewer-flagged candidates re-confirmed byte-identical pre-existing:** `test_api_module_is_small` (api.py 2005 ≥ 1600; api.py diff vs base EMPTY) and `test_total_route_count` (10 routes; the 10th = `GET /api/jobs/cleanup/preflight` at jobs_management.py:564, present at HEAD AND base) — both fail at base batch AND base solo. Fix C added ZERO routes (decorator inventory identical HEAD↔base).

## 4. Fix-C-specific verification — ALL PASS

### 4a. 28c6421b read, end-to-end — ✅ PASS (runtime, real code)
Seeded `Instance(status='waiting_children')` + `JobItem(job_type='message', admission_state=done, terminal_reason=completed)` on file-backed SQLite (NullPool/WAL/busy_timeout — blueprint recipe):
- **Resolver**: real `resolve_work`/`list_work`, zero mocks → `status='completed', job_type='message', mission_liveness='processing'` ✓
- **Router**: real FastAPI TestClient → real `jobs_crud.get_job` route → HTTP **200** with the exact 3-field shape ✓
- **SSE**: BOTH the internal `_resolve`+`to_payload`/`to_completed_payload` AND the real SSE route via ASGITransport — `connected`+`completed` events both carry `job_type='message'`, `mission_liveness='processing'` ✓
- Additivity at runtime: all 25 pre-Fix-C JobResponse keys + 4/5 SSE pre-keys preserved on every surface ✓

### 4b. Degradation both paths — ✅ PASS
- **Single-row** (runtime): `SQLModelInstanceRepository.get` → SQLAlchemyError through the HTTP route → **HTTP 200 (explicit no-500)**, `mission_liveness=None`, `status` still `completed`, 1 warning logged ✓
- **Batch**: W-1 test (in the 18/18 file) + mock-audit VACUOUS-PROOF (deleting the catch at work_resolver.py:1737-1754 propagates → test fails; asserts records-populated + None + status-preserved + exactly-one-warn) ✓

### 4c. No N+1 — ✅ PASS (engine-proven)
Mixed pages 8-row (3 mission/3 mirror/2 no-instance) and 16-row (6/6/4), all linked rows on DISTINCT instances: **3 queries total at both sizes** (bound ≤3 per unit test :720); Instance-SELECTs flat at **2** while rows doubled (O(1) batch). **Counter engine-equivalence proven**: Session.exec-patch counter == `before_cursor_execute` engine listener EXACTLY at both sizes — the mock-audit's MOCK-COUNTED label is upgraded to ENGINE-EQUIVALENT (a true runtime fact, not a mock artifact).

### 4d. Additivity — ✅ CONFIRMED (static)
Schema: only 2 added fields, both `str | None = Field(default=None)` (schemas.py:114,122); zero renames/removals/type-changes. Zero new routes; zero status-code changes; identical service call-surface counts. SSE payloads 4→6 / 5→7 keys, existing keys bit-for-bit. **FE: zero references to `job_type`/`mission_liveness` across all of frontend/** (net-new contract; strictly-keyed consumers unaffected). 🟢 NUANCE: resolver fabricates `job_type='task'` for legacy JobItem rows missing the column (documented defensive default, models.py parity); `_job_to_response` dual-source fallback leaves `mission_liveness=None` when the resolver filtered the row — semantics "split-unavailable", not "instance dead".

### 4e. Read-model purity at runtime — ✅ PASS
4/4 §8.2 read surfaces driven (resolver ×28 calls, jobs_crud real route handlers ×12, jobs_management read-delegation helper ×9 [DISCLOSED substitution — endpoint bodies are write paths per §8.2 "delegates response construction"], jobs_streaming full event-generator drains). **Zero INSERT/UPDATE/DELETE/CREATE/ALTER/DROP during reads** (210/210 statements SELECT; `job_locks` untouched). BEFORE==AFTER row dumps byte-equal (12,135 bytes, 5 tables). Census 23 before AND after. Static: constitution pack green, delta 0.

## 5. Mock-quality audit — ✅ PASS-WITH-NOTES
- W-1 batch-degradation: surgical double-patch (Session.exec filtered to Instance-SELECT + repo.get) — **VACUOUS-PROOF**
- Single-row degradation test: PRESENT, non-vacuous (deleting the guard propagates)
- N+1 counter: engine-equivalence runtime-proven (§4c)
- Zero kwargs-sourced assertions; zero repo-READ mocks on asserted happy paths; positive-value asserts fail on silent no-op (negative-contract `is None` tests pass on no-op BY DESIGN)
- 🟠 Gap (follow-up, non-blocking): 28c6421b full-shape covered at RESOLVER only in committed tests; router/SSE tests assert key-presence on task-type seeds. Runtime gate covered it this run (§4a); recommend committing a router+SSE-shape test.

## 6. ensure.md Core — ✅ 4/4 Critical + Important

| Req | Evidence | Status |
|---|---|---|
| #1 No regressions in changed packs | 3/3 acceptance sets PASS (logs re-verified by ensure worker) | ✅ |
| #2/#3 Concurrency + no-sync-DB | `concurrency_atomic_unit_test` **98P/74S/0F** — baseline-exact | ✅ |
| #4 dev.sh graceful flag | `--timeout-graceful-shutdown 10` at dev.sh:102 | ✅ |
| Important: await discipline | 0 un-awaited call sites (7 grep residuals all comments/docstrings; positive controls awaited) | ✅ |

No ensure.md contradictions. Release Gate (e2e) not run — no e2e-scope change (read-model only), territory split per protocol.

## 7. Gaps / follow-ups (non-blocking)

1. 🟠 Commit a router+SSE-shape test for the 28c6421b scenario (extend `_seed_job` with `job_type='message'` + live linked Instance; assert `mission_liveness='processing'` round-trips through both wire surfaces) — runtime gate proved it; make it permanent.
2. 🟢 Flake-family membership wobble: 19 this gate vs 18 at Fix-B (cross_phase_b ×2 + debug_llm ×1 surfaced; one vscode node fewer). Polluter pair-bisection follow-up carried forward (QUARANTINE row 37).
3. 🟢 `job_type='task'` fabrication for legacy rows + `mission_liveness=None` dual-source fallback semantics — document in §8.2 consumer guidance.
4. 🟢 §8.2 says "5 observed values"; canonical vocabulary has 7 — wording update.
5. 🟢 Collect-only sentinel inconclusive (3,222 vs 16,137) — investigate default-addopts collection quirk or drop the sentinel from the protocol.
6. 🟢 Carried from Fix B: constitution pack stdout census echo; base-batch false-positives in both directions justify always-solo-verifying pass-at-base nodes (this gate did).

## 8. Documentation updated

- PACKS.md: Fix-C gate entry. QUARANTINE.md: re-verification stamp rows 37/38 (membership note). LESSONS/2026-09-02-fixc-gate-notes.md. This file.

## 9. Verdict

- Acceptance **3/3 EXACT** · full suite **15,594 passed / 261 F+E, 100% adjudicated: 0 caused, 242 pre-existing, 19 context-flakes** · Fix-C-specific 5/5 PASS (28c6421b e2e, degradation both paths, N+1 engine-proven, additivity, purity) · mock-audit PASS-WITH-NOTES · ensure Core 4/4 · census 23/delta 0 · FE untouched (0 files) · repo READ-ONLY, worktree clean teardown, port 8088 untouched.
- **FINAL: ✅ PASS — merge-ready @ `ab518e0b`.** Follow-ups in §7 (one 🟠 test-commit gap; rest 🟢).
