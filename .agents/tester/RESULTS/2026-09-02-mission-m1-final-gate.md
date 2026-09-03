# Mission M1 — FINAL gate — `feature/mission-class` @ `0e74ca1e`

Date: 2026-09-02 (UTC) · Base: `e676ddea` (latest, program-complete) · Range: `e676ddea..0e74ca1e` (12 M1 commits: 7 M1 + 5 fix-round) + 2 gate-owned test-infra commits landed during the gate (`12ed8f86` pytest-mock dev dep; `b488fabc` FE pack EXPECTED_BRANCH re-point) → final HEAD `b488fabc`.
Dispatched: 22 workers (1 wave-0 discovery, 4 acceptance + 1 quick-fix rerun, 1 FE acceptance/mutation, 3 M1-runtime, 8 partitions, 1 FE-full + 1 pack quick-fix, 2 base-attribution cohorts, 1 N+1 base micro-check, 1 ensure.md). Repo READ-ONLY except the 2 committed test-infra fixes; base scratch worktree `/tmp/m1-gate-base` @ `e676ddea` isolation-proven (daemon.__file__ resolves under worktree). Evidence: `/tmp/m1-gate/` (p1–p8.log, base-p*.log, acc-*.log, offpath/onpath/purity scripts+logs, fe-mutation/, evidence-p8/).

## FINAL VERDICT: ⚠️ **CONDITIONAL PASS — SHIP with flag OFF; one ON-path defect must be fixed before the flag flips**

- **0 branch-caused regressions** in the full 16,194-test suite (259 F+E nodes fully attributed: 257 pre-existing + 1 context-flake + 1 isolation-inverse).
- **All 6 acceptance sets green** (one after a gate-fixed manifest gap).
- **OFF-path byte-identical to base** (runtime-proven, 6/6 surfaces). Kill-switch default OFF holds: merging is safe today.
- **ONE M1 semantic defect (🟠)**: ON-path dead-letter scenario surfaces `mission_terminal_reason='failed'` where doc §8.3:1096 (W4 hazard) specifies `'dead_letter'`. Details §3. Production impact zero while `ENSEMBLE_MISSION_PROJECTION_ENABLED` stays OFF; must be fixed + integration-pinned BEFORE the flag is ever flipped ON.

---

## 1. Acceptance sets — 6/6 PASS (independent runs, counts pasted)

| Set | Expected | Actual | Result |
|---|---|---|---|
| `tests/unit/services/test_mission_resolver.py` | 48 | **48 passed in 1.66s** (after gate fix `12ed8f86`; pre-fix 46P/2E `mocker` fixture missing) | ✅ |
| `tests/unit/routers/test_jobs_streaming_resolver.py` | 10 | **10 passed in 1.33s**, exit 0 | ✅ |
| Constitution drift pack (`EXPECTED_BRANCH=feature/mission-class`) | 10/10, census 23, mint 6 | Pack **24 passed** (drift 10/10 + linkage 14); introspection **writers 23 / mints 6** (creators 1, informational); branch guard honored (BRANCH-CHECK, not SKIP) | ✅ |
| `tests/unit/services/test_fix_c_read_model_split.py` | 20 | **20 passed in 1.31s**; EXISTS_AT_BASE, M1 diff `+91/−0` (commit 3df44658); key-parity tripwire = `test_job_response_serializer_key_parity_with_model_fields`, ON-emission = `test_job_response_on_state_emits_mission_keys` — both present & green | ✅ |
| FE token-guard spec (mutation-proofed) | guard green + mutation caught | Spec 1/1 green; **mutation DETECTED** (injected `mission-settled` token in scratch copy → guard fails with file:line report; clean control passes); guard = in-spec scanner banning literal `mission-settled` (built via `['mission-','settled'].join('')`), scan surface `frontend/src`+`frontend/e2e` ts/html/scss/css | ✅ |
| FE: tsc + targeted jest | green | tsc exit 0; guard spec 1 suite + chip consumers (job-card, job-detail-drawer) 2 suites / **59 tests** green | ✅ |

## 2. FULL regression suite — 8 partitions (Fix-B/Fix-C protocol) + FE

| P | Scope | Collected | Passed | Failed | Errors | Skipped | Runtime | vs Fix-C baseline |
|---|---|---:|---:|---:|---:|---:|---:|---|
| P1 | unit subdirs except tools (9) | 1,633 | 1,625 | 8 | 0 | 0 | 42s | +51 collected (= M1 tests, all green); 8F identical |
| P2 | unit/tools + test_[a-k]* | 2,539 | 2,491 | 25 | 21 | 2 | 57s | +1F/−1P → context-flake (§2a) |
| P3 | unit/test_[l-m]* | 1,472 | 1,468 | 3 | 0 | 1 | 177s | **EXACT parity** |
| P4 | unit/test_[n-z]* | 2,130 | 2,020 | 58 | 2 | 50 | 36s | +2 collected (pre-M1 file); **−1F** (improvement) |
| P5 | {job_queue,services,message_queue_redesign,migration} | 2,749 | 2,682 | 2 | 0 | 65 | 82s | known pair only; +1P/−1S benign |
| P6 | test_[a-j]* + 7 dirs | 1,769 | 1,675 | 46 | 0 | 48 | 61s | **EXACT parity** |
| P7 | test_[k-z]* + {opencode,repositories} | 3,479 | 3,336 | 43 | 0 | 79 (+5xf, 16 des.) | 111s | **EXACT parity** |
| P8 | integration (override, not postgres) | 423 | 357 | 35 | 16 | 1 (14 des.) | 57s | −2F/+6P/+4 executed → 1 confirmed improvement |
| **Σ** | | **16,194** | **15,654** | **220** | **39** | **246** (+5xf, +30 des.) | ~10.4 min | Δ+57 collected ≈ M1 +51 unit + integration shape |

**FE:** full jest **67 suites / 2,398 tests / 0 failures** (11.1s, `--no-cache`, rev-parse bracketed; baseline 66/2,396 + guard spec +1 suite/+2 tests) · tsc exit 0 · build exit 0 · SCSS warnings 10 vs baseline 7 (+3 = mission-liveness-chip SCSS; adjudication data, non-gating).

### 2a. Per-failure attribution — 259/259 F+E classified (scratch worktree @ `e676ddea`)

| Verdict | Count | Detail |
|---|---:|---|
| **PRE-EXISTING at base** | **257** | P1–P7: 207/208 (batch fail at base, deterministic); P8: 50/51. Family confirmations: proxy_phase1 ×8, mock cluster `slash_commands` ×21E, archive_lifecycle ×5 (quarantined), P5 documented pair ×2, injection_api ×26, watchover family, vscode cluster ×16E |
| **🔴 CAUSED** | **0** | none met HEAD-3/3F ∧ base-3/3P ∧ base-batch-P |
| 🟠 Context-flake (order-sensitive) | **1** | `tests/unit/test_infra_tools.py::TestInfraAssetListTool::test_list_filter_by_type` — `dc1` fixture pollution from prior test; 6/6 solo PASS at BOTH commits; passes base-batch, fails HEAD-batch. QUARANTINE.md row added |
| 🟡 Isolation-inverse | **1** | `test_agent_bootstrap_and_hello` — fails solo 3/3 at both, passes base-batch (re-confirmed Fix-C class) |
| Improvements (F→P vs baseline) | 2 | P8 `skill_cross_phase_flow_b::test_resolution_force_resolves_when_max_extensions_exceeded` (base FAIL → HEAD 3/3 PASS solo-confirmed); P4 −1F |
| Base-only orthogonal (fail at base, pass at HEAD) | 2 | `test_filesystem_workdir::test_dotdot_traversal_blocked`; `test_ab_resolution_threshold_met` (quarantined flake family manifesting at base only) — environment-shape drift, not regressions |

**Ledger notes:** (a) `test_message_queue_e2e::test_debug_llm_invocation_count` — Fix-C classed flake; at base `e676ddea` it is DETERMINISTIC 3/3F (class shifted between ab518e0b and e676ddea; pre-existing either way, not M1). (b) P1's 51-collection delta = M1's 3 files (48+2+10 via parametrize); base P1 also drops 2 empty placeholder dirs (checkpoint_adapter/, persistence/ — no tests either side).

## 3. Mission-M1-specific — runtime verification

| Check | Result | Evidence |
|---|---|---|
| **OFF-path byte-identical** | ✅ PASS | 6/6 surfaces byte-equal HEAD↔base (sha256: jobs-list default+filtered, SSE raw stream/connected/completed events, job detail); fully pinned seed (fixed UUIDs/timestamps/project). Load-bearing: SSE `to_payload` splats empty `mission_projection_to_dict()` at OFF; HTTP `JobResponse` custom `@model_serializer` (27 keys) omits nulls at OFF — pydantic default WOULD have emitted nulls |
| **ON-path matrix** | ⚠️ 12/14 | S1 mirror-live (epoch 1, reason None) ✅ · S2 dead-link (None/None/None) ✅ · S3 task row ✅ · **S4 dead-letter ❌** (see below) · S5 revived epoch STILL 1 (constant-1 contract) ✅ · S6 degraded: HTTP 200 both surfaces, mission_* None, exactly 1 batched warning ✅ · SSE S1 mission keys ✅ · OFF-contrast: keys absent at OFF ✅ |
| **🟠 S4 divergence** | FAIL (code vs doc) | Doc §8.3:1096 (W4 hazard): DEAD admission overrides instance liveness → expect `'dead_letter'`. Actual: `'failed'`. Root cause: `daemon/services/work_resolver.py:1702` binds via `resolver.project(instance)` (defaults `dead_linked=False`), bypassing the `dead_linked` pre-fetch that `resolve()/resolve_many()` do; `MissionResolver._project` W4 path (mission_resolver.py:519-525) never fires on the read surface. Resolver logic itself CORRECT (direct `resolve()` returns `'dead_letter'`). Fix = production change (route work_resolver through the resolve path or fetch dead_linked) — NOT gate-fixable |
| **Purity (zero DML)** | ✅ PASS | Engine listener (`before_cursor_execute`) across all 4 read surfaces: 28 statements, **all SELECT**, 0 DML, `job_locks` untouched, census 23 before AND after, row-dumps byte-equal 7/7 tables |
| **N+1 bound** | ✅ UPHELD (pinned bound) | `_batch_jobitem_lookup` = **exactly 1** JobItem SELECT per page (N=8 and N=16), engine-counted; Instance SELECTs FLAT at 2 while N doubles. Committed test `test_resolve_issues_exactly_one_jobitem_select` is **ENGINE-BOUND** (mission_resolver tests :1078-1133) ✅. Route-level total JobItem SELECTs = `3 + N_queued` (count+list pagination + per-QUEUED `_get_queue_position` at jobs_crud) — **identical at base** (base run 4/2 and 6/2, statement-for-statement) → pre-existing fe-liveness-era pattern, NOT M1. 🟢 M1 added zero route-level SELECTs beyond its single batch |
| **Mock-quality audit** | ⚠️ 1 finding | `test_list_work_no_n_plus_one_for_mission_liveness` (fix_c_read_model_split.py:655-730) counts via `patch.object(Session,'exec')` → **MOCK-COUNTED** (would not catch raw-SQL bypass); intent correct. Recommend engine-listener migration (follow-up) |
| **FE guard** | ✅ mutation-proof | Scratch-injected `mission-settled` token → guard FAILS with file:line; clean control passes; zero occurrences in frontend/ (rename to `mission-terminal` complete, incl. CSS — no `.mission-settled` residue) |

## 4. ensure.md — Core 4/4 PASS

| Req | Evidence | Status |
|---|---|---|
| #1 No regressions in changed packs | 6/6 acceptance sets + scoped packs green (logs re-verified) | ✅ |
| #2/#3 Concurrency + no-sync-DB | `concurrency_atomic_unit_test` **98P/74S/0F** in 8.49s — baseline-exact | ✅ |
| #4 dev.sh graceful flag | `--timeout-graceful-shutdown 10` at dev.sh:102 | ✅ |
| Important: await discipline | 8/8 real call sites awaited (11 residuals all defs/comments) | ✅ |

Release Gate (e2e) NOT run — read-model+FE additive change, territory split per protocol (same call as Fix-B/Fix-C gates). No contradictions.

## 5. Quick fixes applied (2, committed)

| Commit | What | Root cause | Verification |
|---|---|---|---|
| `12ed8f86` | pytest-mock 3.15.1 → `[dependency-groups].dev` (pyproject +1, uv.lock +14) | Branch commit `0e74ca1e` itself added `mocker`-based degradation tests without declaring the plugin — fresh `uv sync`/CI would error them | File re-run 48/48; both formerly-errored tests pass; diff scope verified 2 files |
| `b488fabc` | FE packs `EXPECTED_BRANCH` re-point → `${EXPECTED_BRANCH:-feature/mission-class}` (2 files, 2 lines) | Packs hardcoded `feature/job-queue-fe-liveness` from the prior gate → Stage-0 DRIFT short-circuit | Both packs re-run green (67/2,398 jest; tsc 0; build 0); env-overridable form prevents recurrence |

## 6. Gaps / follow-ups

1. 🟠 **S4 dead-letter divergence** — fix `work_resolver` binding (dead_linked fetch / resolve-path) + commit an integration-level S4 pin (list+detail+SSE) before ANY `ENSEMBLE_MISSION_PROJECTION_ENABLED=ON` flip. The runtime matrix proved the unit suite misses this (resolver-level tests green; binding-level bug).
2. 🟢 Migrate `test_list_work_no_n_plus_one_for_mission_liveness` counter to engine listener (MOCK-COUNTED today).
3. 🟢 Route-level `3 + N_queued` pre-existing N+1 (`_get_queue_position` per QUEUED row, jobs_crud) — efficiency follow-up, base-identical, not M1.
4. 🟢 QUARANTINE row added for `test_list_filter_by_type` (context-flake); monitor — a second manifestation would justify family entry.
5. 🟢 `test_debug_llm_invocation_count` class shift (flake→deterministic) happened between `ab518e0b` and `e676ddea` — outside M1; ledger note.
6. 🟢 purity_verify.py is argv-less and imports M1-only modules — base reruns need the stubbed copy at `/tmp/m1-gate/purity_verify_base.py` (pattern recorded to KB).

## 7. Documentation updated

PACKS.md (gate entry) · QUARANTINE.md (new context-flake row + re-verification stamps) · LESSONS/2026-09-02-m1-gate-notes.md · this file.

## 8. Verdict

**CONDITIONAL PASS — merge-safe with kill-switch OFF (byte-identical OFF path, 0 caused regressions, all gates green). One 🟠 ON-path defect (S4 dead-letter `failed` vs doc `dead_letter`) must be fixed and pinned before the projection flag is enabled in any environment.**

---

# AMENDMENT (2026-09-02, same day) — S4 fix verified @ `7852aeab` → verdict upgraded

Fix under test: `7852aeab` — dead-link pre-fetch at the binding seam (`work_resolver.py:1729-1737`, mirroring `resolve()`'s single-row shape; resolver semantics untouched; kill-switch gate stays first) + new `tests/integration/test_work_resolver_dead_letter_binding.py` (593 lines: LIST/DETAIL/SSE pins + control, ported from this gate's `/tmp/m1-gate/onpath_verify.py` template). Lineage: `0e74ca1e → 12ed8f86 → b488fabc → 7852aeab`.

## Amendment verification (5/5 PASS — 2 workers, /tmp/m1-flip/)

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | 4 pins at `7852aeab` green + non-vacuous | ✅ **4/4 in BOTH addopts modes** (1.38s/1.13s); vacuity audit **FAITHFUL**: real ASGI routes on all pins; W4 case seeded with `InstanceStatus.ERROR` (liveness `'failed'`) so `dead_letter` is reachable ONLY via the admission branch — no wrong-reason pass; control asserts `'failed'` stands for no-dead-link (guards blanket-return AND silent W4 drop); value-equality (`== "dead_letter"`), not key-presence; file-backed SQLite + NullPool + WAL + busy_timeout; zero mocks on asserted paths (autouse kill-switch ON fixture only) |
| 2 | Differential at pre-fix `b488fabc` (scratch worktree, file scratch-copied) | ✅ **3F / 1P reproduced exactly** — LIST/DETAIL/SSE pins fail with `got 'failed', expected 'dead_letter'` + defect-location assertion naming `work_resolver.py:1702`; control passes. Matches dev's differential verbatim; worktree removed cleanly |
| 3 | ON-matrix S4 re-run at HEAD (gate's own harness) | ✅ **14/14 PASS** — S4 rows: `list … dead_letter PASS`, `detail … dead_letter PASS`; all other scenarios unchanged-green |
| 4 | OFF-path spot (jobs list A1) | ✅ sha256 `b92bb75a97ab73d7…`, 2829 B — **byte-identical to base** `e676ddea`; fix invisible at OFF (properly scoped) |
| 5 | Quick sanity | ✅ resolver **48/48** · streaming **10/10** · drift **10/10** + census **23 / 6** — zero collateral from the fix |

## AMENDED FINAL VERDICT: ✅ **PASS — M1 merge-ready (kill-switch default OFF); the S4 conditional is RESOLVED**

The gate's sole blocker (§3 S4 divergence) is fixed, correctly scoped (ON-path only), integration-pinned on all three surfaces with a differential-proven control, and collateral-free. All §1–§5 gate results stand unchanged.

## Pre-flip ledger (before ANY `ENSEMBLE_MISSION_PROJECTION_ENABLED=ON` in a real environment)

1. 🟠 **Per-row SELECT batching at flip** — the pre-existing `3 + N_queued` route signature (per-QUEUED `_get_queue_position`, `jobs_crud.py`; base-identical, fe-liveness-era): batch the position lookup when the flag flips or accept a documented per-queued-job SELECT cost.
2. 🟢 **Docstring straggler → M2** (dev-carried item from the fix round).
3. 🟢 MOCK-COUNTED `test_list_work_no_n_plus_one_for_mission_liveness` counter → migrate to engine listener (gate §3 finding).
4. 🟢 Flip discipline: flag is env-only + restart-read; flip via the pause-first-then-quiesce convention on a soaked instance; revert = env unset + restart.

Evidence: `/tmp/m1-flip/` (pins_override/default.log, onpath.log, offpath-head.log, prefix-pytest.log).
