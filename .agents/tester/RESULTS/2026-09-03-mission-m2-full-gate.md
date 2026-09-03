# Mission M2 — FULL Gate Report
Date: 2026-09-03 | Tester gate | Branch: `feature/mission-class` @ `8eddeb3d` (feature code) → gate HEAD (test-infra commits only; ancestry verified throughout)
Base: `latest` @ `e676ddea` (ancestor check exit=0)

## FINAL VERDICT: ✅ PASS — 0 branch-caused failures across the full suite; all M2-specific contracts verified at runtime

---

## 1. Acceptance Sets (5/5 PASS — counts pasted)

| Set | Expected | Actual | Verdict | Evidence |
|---|---|---|---|---|
| tests/unit/routers/test_missions_api.py | 38 | **38 passed** (3.58s) | ✅ | standalone + in-suite (smaller-subdirs partition, 38/38) |
| tests/unit/services/test_mission_resolver.py | 48 | **48 passed** (1.68s) | ✅ | standalone |
| tests/unit/routers/test_jobs_streaming_resolver.py | 10 | **10 passed** (1.35s) | ✅ | standalone + in-suite |
| tests/integration/test_work_resolver_dead_letter_binding.py | 4 | **4 passed** (1.28s) | ✅ | standalone (in-suite hits documented httpx env class row 37 — "passes in isolation" holds) |
| Constitution drift pack | 10/10 | **10/10 passed** (5.27s) + companion linkage pack 14/14 = 24/24 | ✅ | `EXPECTED_BRANCH=feature/mission-class` |

**Census (verbatim introspection): `writers 23 | mints 6 | creators 1`** — task shorthand "23/6/1" reads writers/mints/creators; exact match. Branch guard fired correctly (BRANCH-CHECK line printed).

## 2. Full Regression Suite — baseline at HEAD vs base `e676ddea`

12 committed partition packs (new this gate, committed `3b0b98b6`), **16,022 collected**, all ≤5 min (fastest 13s, slowest 68s), dual-layer timeouts, rev-parse brackets before/after, no `-x`.

| Partition | Result | Failure inventory |
|---|---|---|
| unit_tools (1,049) | 1,042P/1F | upgrade_registration ×1 → **base-verified pre-existing** |
| unit_services (1,132) | 1,124P/8F | proxy_phase1 ×8 — exact baseline |
| unit_smaller_subdirs_routers (539) | 538P/1F | slash_commands ×1 known-defect; missions routers 38+10 green in-suite |
| unit_loose_a_d (1,050) | 1,017P/10F/21E | slash-fixture 21E (root-cause match, ~21 baseline) + misc-cluster 9 (coder_agent ×1, coder_developer_migration ×5, devops_agent ×3 — row-17 exact) + **TestApiModuleSize** |
| unit_loose_e_l (1,116) | 1,105P/11F | misc-cluster 9 + llm_allowed_models ×2 → **base-verified pre-existing** |
| unit_loose_m_r (1,890) | 1,843P/7F | models_split ×1 + phase4 ×1 (row-17 exact) + paused_auto_resume ×5 → **base-verified pre-existing** (mock-await class) |
| unit_loose_s_z (1,036) | 971P/52F/2E | watchover **47 exact** + row-15 webfetch ×2 + row-17 wanderer ×2/validate_agent_id ×1 + vision ×1, terminal_reason_mirror ×1 → **base-verified pre-existing** |
| top_level_a_h (1,072) | 1,001P/19F/2E | sqlite 9 + subdirs 4 + misc 3 (all ledger) + test_api ×2 (**base-verified**, mock-await) + jsonb ×3 (**context-flake**) |
| top_level_i_q (2,443) | 2,309P/61F | sqlite 29 + injection 27 (mock-await baseline class) + skill-evolution flake ×1 (row 12) + innate_skills/llm_load_balance (row-17 exact) + error_codes ×1, compaction_guard ×1 → **base-verified pre-existing** |
| top_level_r_z_misc (2,311) | 2,258P/14F | sqlite 9 + skill_evolution_config ×2 + terminal_orphan_matrix ×1 (ledger) + atomic_status ×1, worker_notification ×1 (**context-flake**) |
| job_queue (1,650) | **1,612P/0F/38S** | **ZERO failures — the mission/projection-adjacent suite is fully green** |
| integration_opencode_e2e (734 coll./262 desel.) | 710P/24F/18E | httpx ×19 (row 37) + e2e-stale ×3 (rows 18/20/21) + bucket5 ×6 + complete_cancel ×4 + vscode ×1 (row 36) + answer_dismiss/w7 (**base-verified**) + context_injection_hybrid (env class, no daemon) + **M2 runtime-contract probe passes IN-SUITE** |

**Raw F+E ≈ 251 nodes (≈243 unique after xdist error de-duplication) — inside the stable prior-gate band (~241–259).**

### Base adjudication (scratch worktree @ `e676ddea`, uv sync, isolation proven via `daemon.__file__` → worktree path)
- **12/15 candidates PRE-EXISTING** (reproduced verbatim at base): messages.py:258 mock-await class (vision ×1, paused_auto_resume ×5, test_api ×2), registry/sentinel drift (llm ×2, error_codes ×1, compaction_guard ×1, upgrade_registration ×1), contract literals (terminal_reason_mirror ×1), T0-mirror timing (answer_dismiss ×1), MagicMock queue_type (w7 ×1), env class (context_injection_hybrid).
- **3/15 PASS-AT-BOTH → context-flakes** (fail only under xdist/shared-PG partition context; 3× solo PASS at HEAD): jsonb_migration ×3-of-8, atomic_status_transitions ×1, worker_notification ×1.
- **Anything HEAD-fail/base-pass = 🔴 caused: NONE.**

### TestApiModuleSize — formal quarantine (task instruction, now evidence-backed)
`daemon/api.py`: **base 2005 / gate HEAD 2024** lines vs `< 1600` assertion → **fails at BOTH** → long-standing pre-existing; entry added to QUARANTINE.md Active (sweep-visible, no deselect). Note: HEAD is +19 vs base (M2 route registration), not smaller.

### QUARANTINE.md changes (this gate)
1. TestApiModuleSize formal row (the task's explicit ask).
2. Consolidated row: 12 base-verified pre-existing additions (6 root-cause groups).
3. Consolidated row: 3 partition-context flakes (dual-commit-evidenced, watch for second manifestation).

## 3. M2-Specific Results

### 3.1 OFF zero-query at runtime ✅ (probe `a9a00707`, 7/7)
- OFF (default) ⇒ **404** on list + detail (even with a real seeded instance id).
- **ZERO SQL queries** on OFF requests (engine listener, window-scoped): OFF list **0**, OFF detail **0**, framework-404 control **0**, **positive control ON-list = 2 SELECTs through the same listener** (proves the spy is live — the 0 is meaningful).
- **OpenAPI visibility — documented contract confirmed, dispatch expectation corrected:** routes stay **REGISTERED and documented in `/openapi.json` in BOTH states**; the kill-switch gates **in-handler** (missions.py:251-259, 404 pre-query). Sources agreeing: §8.4 ("Routes stay REGISTERED while OFF"), router docstring, unit pin `test_off_routes_stay_registered_in_openapi`. The "OFF-hidden" reading of the task's "OpenAPI visibility claim" is rejected on evidence.
- ON smoke: 200 with §8.4 envelope `{missions,total,limit,offset,has_more,degraded}`; unknown id ⇒ 404. Flip-back OFF⇒404 determinism verified (supports the restart-required operational note; flag is proc-cached, mission_resolver.py:113,131-148).

### 3.2 API contract matrix ✅ (probe `ad63a2d8`, 11/11)
- **Filters**: multi-liveness OR + agent_id AND-compose ✅; `dead_letter` rejected **alone AND inside a comma list** ⇒ 400 with accepted set enumerated (`cancelled,completed,failed,paused,pending,processing`; dead_letter is a terminal_reason, never a liveness — §8.2); whole-list rejected before SQL IN-clause.
- **Pagination**: clamps — `limit=0/-5 ⇒ 1` (**correction: clamp-to-minimum 1, NOT default 10**; matches unit pin `test_limit_clamped_to_minimum_one`), `limit>100 ⇒ 100`; beyond-end page ⇒ empty items + **total preserved**.
- **Ordering**: `last_activity_at DESC NULLS LAST` — explicit NULL positions asserted.
- **Detail discrimination**: unknown id ⇒ 404; degraded (dropped instances table) ⇒ **200** with `degraded`-shape (None-fields + empty linked_jobs) — never 500.

### 3.3 3-SELECT bound at route level ✅ (engine-counted)
| request | page | SELECTs (instances / job_queue_items) |
|---|---|---|
| list | 2 | 2 / 1 |
| list | 4 | 2 / 1 |
| list | 8 | 2 / 1 |
| detail (degraded) | — | 1 / 0 |

**Flat as page doubles; degraded path included** (batched IN short-circuits on empty page_ids). Matches the unit pin `TestEngineBoundQueryCount` — independently confirmed at runtime.

### 3.4 W4 five-surface consistency ✅
One seeded dead_letter row (ERROR instance + DEAD JobItem): `mission_terminal_reason = dead_letter` **identical across** jobs-list, jobs-detail, **SSE (first connected event consumed in-proc, 10s hard cap, arrives at T0)**, missions-list, missions-detail — 5/5 matrix.

### 3.5 Census + purity ✅
- Census 23/6/1 (writers/mints/creators) — drift pack 10/10.
- **Zero DML through the new routes** (integration level): 0 INSERT / 0 UPDATE / 0 DELETE / 0 DDL / 0 OTHER_MODIFYING across all ON-path missions list+detail requests (39 SELECTs total, informational).

### 3.6 FE ✅
- Diff claim verified: **0 production TS source files** in `e676ddea..HEAD -- frontend/` (4 files: chip .html/.scss token rename `mission-settled`→`mission-terminal`, 1 e2e spec regex line, 1 NEW guard spec). The literal "zero FE files" phrasing is inaccurate; the production-code claim holds.
- `tsc --noEmit -p tsconfig.app.json` exit 0; guard spec `mission-terminal-token-guard.spec.ts` 1/1 PASS (scanner: zero `mission-settled` occurrences remain).

## 4. ensure.md Status
- **Core #1** (no regressions in changed packs): ✅ acceptance + probes + job_queue partition all PASS.
- **Core #2/#3** (concurrency pack): see Addendum below (dispatched for formal pack-run closure; partition coverage already ran the canonical files green).
- **Core #4** (dev.sh `--timeout-graceful-shutdown 10`): ✅ static grep (discovery, line 102).
- **Important** (async-await discipline): ✅ all 8 call sites awaited (discovery grep).
- **Release Gate (E2E real-LLM)**: NOT RUN — scope decision (below).
- ensure.md contradictions: none (all validations pack-mapped this gate).

## 5. Scope Decision
- Full suite run — **warranted**: cross-module feature-branch gate (routers + services + config + constitution registration); leader mandated the baseline protocol.
- Release-Gate E2E (4 tests, real LLM, `./dev.sh` on :8079) **deferred to the program-final gate after M3** per task framing ("FE untouched this milestone; the program-final gate after M3 covers FE") — M2 adds read-only routes; no lifecycle behavior change. ensure.md's release-gate trigger (big/critical architecture change) not met by a read-only route milestone.
- FE: quick tsc + guard spec only (per task); full FE pack not warranted by a 0-production-TS diff.

## 6. Quick Fixes Applied (committed)
- `8132747f` missions_api pack: strict-bash RESULT-echo guard (pattern origin).
- Partition guard fixes (same 1-file mechanical class, per-pack): `f6e03bc4` unit_tools, `10a50c2a` unit_services, `c40c65e2` smaller_subdirs, `81b3f532` loose_a_d, `dfa81292` loose_e_l, `f617fde6` loose_s_z, `4cd25db0` top_a_h, `a799c5f8` integration, `7f925c6c` job_queue, `5548a92c` r_z_misc (+ m_r/i_q guard commits — see §8 commit-chain note).
- Probe wrapper fix inside `a9a00707` (`--override-ini="addopts="` etc.).
- **No product-code fixes; no test-code content changes** — all failures were adjudication material.

## 7. Quarantined / Gaps
- Quarantined skipped by packs: TestAccessMemoryArchive ×5 (tools partition deselects) + pack-carried deselects (dependency_bus ×1, turn_state_machine ×1, pause_after_spawn ×1).
- Coverage gap (documented, house shape): integration-marked tests excluded from the partition by pyproject `addopts` marker filter (~262) — prior-gate-comparable baseline; M2 integration coverage carried by the dedicated binding pack + both probes (one marked, one not — the unmarked probe also runs in-suite).
- Stale M1 worktree `/private/tmp/m1-gate-base` still present (pre-existing, not removed — not this gate's).
- `TESTER_CANT_OPTIMIZE_TEST_PACK`: **not needed** — no pack approached limits (max 68s vs 300s cap).

## 8. Commit-Chain Note (verification pending in doc-commit)
m-r and i-q runners both cited `a14f9678` as their guard-fix commit — same hash for different files is impossible; final commit sweep will paste `git log --oneline 3b0b98b6..HEAD` and RESULTS will be corrected if misattributed. Feature code is identical from `8eddeb3d` through gate HEAD (all subsequent commits touch `test/packs/*`, `tests/integration/test_m2_*`, `.agents/tester/*` only).

## Addendum (ensure.md Core #2/#3)
concurrency_atomic_unit_test pack: **✅ PASS — 98 passed / 74 skipped / 0 failed** (8.23s; wrapper + internal timers honored). Skips and failures are **baseline-exact** vs the recorded 91P/74S/0F (2026-08-24); the +7 passed delta is new test cases added to the canonical files since the baseline was recorded — recommend refreshing the recorded baseline to 98P/74S/0F. Both Core requirements (deadlock/concurrency integrity; no sync DB calls on the event loop via thread-identity tests) validated.

## Documentation Updated
- [x] PACKS.md — M2 section: 18 rows Last-Run/Status updated (0 PLANNED remaining)
- [x] QUARANTINE.md — +3 rows (TestApiModuleSize formal; 12 base-verified pre-existing; 3 context-flakes)
- [x] LESSONS/2026-09-03-m2-gate-lessons.md — 6 lessons (grep-on-.agents false negative; strict-bash guard class; census labeling; OpenAPI documented contract; limit-clamp semantics; integration marker-filter shape)
- [x] RESULTS/2026-09-03-mission-m2-full-gate.md — this report
- [ ] rules/ensure.md — unchanged (user-owned)
