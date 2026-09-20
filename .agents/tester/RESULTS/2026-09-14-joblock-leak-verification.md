# Test Report: joblock-leak fixes (commits `9b562e14` + `6622d85c`)

Date: 2026-09-14/15
Branch: `feature/fix-joblock-leak` @ `6622d85c` (base `56391477`, = latest after question-resume-stuck merge)
Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-question-resume-stuck`
Worker instances: J1 `c14bdc1d` (infra) · J2 `cdb2a89f` (analysis) · J3 `2270a694` (prime exact-failure) · J4 `1be57099` (acceptance) · J5 `59305c78` (PG) · J6a-d `d73c8324`/`a8e42c18`/`dbd4c400`/`b34a0cdf` (branch census) · J7a-d `2c9c9b84`/`23e92161`/`d0236157`/`f14db657` (base census) · J8 `b621ccc2` (gates) · lifecycle `4e217b99` · adjudication `d1fa8728` · cleanup `b8ce713a` · concurrency `2670630f`
Mode: verification-only — **zero commits** (per mission constraint); 4 disclosed untracked test additions left in the worktree for leader adoption decision.

## VERDICT: ✅ SHIP — prime directive satisfied (3/3 repros independently CONFIRMED-FAIL-AT-BASE), no regressions (census byte-identical), PG gap closed green, one methodology finding against the dev's proof runbook

### Summary
- **Exact-failure proofs (prime directive): 3/3 CONFIRMED** at base `56391477` — F1-inline, F1-paused (the 8h45m prod wedge), F5 all fail with the exact leak shape (`assert 1 == 0` on `_lock_count`). Independent re-execution succeeded via a disclosed rescue variant because **the raw file-copy method is broken** (see F1 finding below).
- **Acceptance: 26/26 green at branch** (dev claim exact); `tests/unit/job_queue` family 80/80; prod-wedge repro green.
- **PG (was SQLite-only): gap CLOSED** — 8 new disclosed PG tests green under **PROD-shape** constraint triggers (F1-inline ×2, F1-backstop ×3, F3-sweep ×3 incl. negatives); pre-existing PG suites 17/17; no trigger-interplay defect.
- **Census: branch ↔ base byte-identical** on all 4 slices (57F+23E both sides after adjudication) — zero branch-induced regressions; the single observed delta (filesystem_workdir) adjudicated ENVIRONMENTAL with same-commit-under-/tmp proof.
- **Gates**: constitution drift 24/24 PASS (census pins 10/10); config knob CONFIRMED (default 90, `ge=1` fail-fast, lifespan wiring); concurrency pack 98/0/74 PASS; flag-policy PASS (cadence-only knob, no disable value, dead DISABLED branch removed).
- Dev claims audit: 26/26 ✓ · constitution 10/10 ✓ · prod-wedge ✓ · exact-failure-at-base ✓ (outcome) with methodology caveat · "targeted 716/716" composition unverifiable (job_queue dir = 80; census covers the tree) · "SQLite-only" accurate → closed.

### Scope Decision
One fix family (2 commits, 6 files, +1961/−34) in a dedicated worktree. Packs: acceptance+family, base exact-failure (detached worktree), full unit-tree census ×4 slices ×2 sides (explicitly required branch-vs-base comparison), PG disposable cluster, constitution+concurrency+knob gates, disclosed gap-closing additions. NOT run: tests/e2e (Release Gate — no release), tests/postgres beyond the joblock-relevant files.

---

### 1. Prime directive — independently re-executed exact-failure-at-base (J3)
Base worktree `/tmp/ens-base-56391477` (detached @ 56391477), test file copied, NO production code copied.

**🔴 METHODOLOGY FINDING (F1):** the acceptance file imports `daemon.services.job_lock_sweep` at module level (line 90) — a module that does NOT exist at base → whole-file collection error. The "self-contained per commit census" claim is false for base-provability; the coder's original proofs must have used a modified copy. J3 built a disclosed rescue variant (sweep plumbing stripped, loud skips; preserved at `/tmp/j3-artifacts/`) — 17/26 tests provable.

**Repro verdicts (via rescue):**
| Repro | Verdict | Evidence |
|---|---|---|
| `TestFixBInlineWriterReleasesLock::test_active_message_job_lock_released_same_commit` (F1-inline) | **CONFIRMED-FAILS-AT-BASE** | `assert 1 == 0` — lock leaked |
| `TestFixBInlineWriterReleasesLock::test_paused_instance_lock_released_same_commit` (F1-paused, prod 8h45m wedge) | **CONFIRMED-FAILS-AT-BASE** | `assert 1 == 0` |
| `TestForceFinalizeOrphanReleasesLock::test_orphan_reap_releases_lock_same_transaction` (F5) | **CONFIRMED-FAILS-AT-BASE** | `assert 1 == 0` |
| `test_sweep_releases_terminal_job_lock` (the file's runbook lists as 4th repro) | **UNPROVABLE at base** — sweep wrapper absent; primitive exists | runbook misleading (J2 audit concurs) |

Full 26 at base (rescue): **8F / 9P / 9S** — 6 leak-class failures (adds queued-message + 2 backstop variants) + 2 hardening-marker absences (R6/R7 `(R7 hardening)` log markers) + 9 pre-existing-behavior passes (healthy-path/no-op/race-loss pins) + 9 sweep-dependent skips.

### 2. Acceptance suite + fidelity (J4, J2)
- 26/26 green (3.12s family run: 80/80 incl. 54 sibling Fix-B tests). Class census: F1-inline 6, F1-backstop 3, F5 3, F3-sweep 7, R6 2, R7 2, R8 3.
- Fidelity: real file-backed SQLite + QueuePool; real PAUSED instances in DB; PRE-EXISTING stale rows seeded before `sweep_once()` (≈ first loop tick); F2 hardening tests mock only the release seam (justified). Deviations: deferral-branch test models the symptom (terminal-task mirror), not the full WAITING_CHILDREN causal chain (R3); "stays gone over time" pinned by idempotency only, no delayed re-check (R4); R8 pins are primitive-level (R5).
- Coverage gaps found → closed by disclosed additions (below): g (start/stop lifecycle + lifespan wiring), k (knob bounds).

### 3. PG backend (J5) — gap closed
Disposable PG14 @15432 (initdb -A trust, role ensemble SUPERUSER, db ensemble_test; torn down + port verified free). Existing: `test_orphan_reaper_pg.py` + `test_jq_proxy_phase2_constraints.py` = **17/17** (F5-vs-triggers + phase-2 suite). New disclosed files install **PROD-shape** trigger DDL (narrow body w/ `job_type != 'message'` conjunct — deliberately NOT the wider `PHASE2_INSTALL_STATEMENTS` test-side body; see R6):
- `tests/postgres/test_f1_inline_writer_pg.py` (430 ln) — active-message + PAUSED-owner same-commit release under deferred constraint triggers
- `tests/postgres/test_f1_backstop_writer_pg.py` (433 ln) — terminal-mirror, PAUSED-owner, live-task-negative
- `tests/postgres/test_f3_sweep_pg.py` (466 ln) — PAUSED stale reclaim, active-survives negative, dead-job reclaim
All **8/8 PASS**; combined 25/25 no-regression. Trigger interplay analysis: JobItem UPDATE runs before lock DELETE in-transaction; guard trigger's outer IF is FALSE on `done`; lock-guard fires only on INSERT/UPDATE — DELETE unaffected. **No defect.**

### 4. Branch-vs-base census (J6a-d vs J7a-d)
| Slice | Branch | Base | Verdict |
|---|---|---|---|
| A services | 8F / 1671P / 41s | 8F / 1671P / 43s | **IDENTICAL** (7× job_queue_proxy_phase1 + 1× wc-cwd env hazard) |
| B subdirs | 5F / **3352P** / 58s | 5F / 3326P / 55s | **IDENTICAL failures**; +26 = acceptance suite (green) |
| C root½1 | 30F+21E / 2963P / 149s | 31F+21E / 2962P / 151s | 30/31 identical; 1 delta adjudicated (below) |
| D root½2 | 14F+2E / 3095P / 101s | 14F+2E / 3095P / 105s | **IDENTICAL** |
| **Total** | **57F+23E / 11,081P / 57S** | **58F+23E / 11,054P / 57S** | arithmetic-consistent: +26 acceptance + 1 environmental flip |

**Delta adjudication (`d1fa8728`):** `test_filesystem_workdir.py::TestIsWithinWorkdir::test_dotdot_traversal_blocked` fails at base, passes at branch → **ENVIRONMENTAL, proven**: same commit `6622d85c` PASSes at `/Users/...` (×2) and FAILs under `/tmp` (×2, incl. fresh same-commit worktree); `_is_within_workdir`'s `_is_in_temp_dir` allow-list (workspace_guard.py:134-139) absorbs the fixture's `data` parent when the tree lives under the `/tmp → /private/tmp` symlink. Pre-existing test-bug masked at normal dev locations; zero file overlap with the fix. Not attributable; direction precludes regression.
All failure families match the documented pre-existing backlog (job_queue_proxy_phase1 ×7, archive_lifecycle ×5 quarantined, messages.py:249 await-rot ×6+1, find_near ×13, builtin_mcp/context7/webfetch mock AttributeErrors ×23, agent-drift, release-tag pin, api-size, etc.).

### 5. Gates
- Constitution drift (`EXPECTED_BRANCH=feature/fix-joblock-leak`): **RESULT: PASS**, 24/24 (census 23/1/0 anchors hold; JobLockSweepService correctly NOT registered — writes no admission_state).
- Config knob: default **90** ✓ · `0` → **ValidationError** (fail-fast) ✓ · lifespan wiring api.py:728 + boot log ✓ · cadence-only, no disable value (owner hard policy honored; dead `interval < 1 → DISABLED` branch dropped by 6622d85c) ✓.
- Concurrency pack: **PASS** 98/0/74 in 7.05s (lock/finalize paths are concurrency-adjacent → in blast radius).
- Flag census: zero new `os.environ`/`getenv` reads; one tuning-only pydantic knob. PASS.
- ensure.md Core: 4/4 critical satisfied (scoped packs PASS; concurrency pack PASS; sync-DB-thread gate via pack; dev.sh untouched by commits + standing check verified 2026-09-14 earlier session). Release Gate: not warranted.
- Leftover-file census: 13 files across 2 commits, 100% accounted (F1/F2/F3/F5 + tests). Note: 6622d85c touches 2 daemon files under a `test(...)` prefix (comment doc-truth + dead-branch removal — verified consistent with `ge=1`).

### 6. Disclosed test additions (UNCOMMITTED, left for leader decision)
1. `tests/postgres/test_f1_inline_writer_pg.py` (430 ln)
2. `tests/postgres/test_f1_backstop_writer_pg.py` (433 ln)
3. `tests/postgres/test_f3_sweep_pg.py` (466 ln)
4. `tests/unit/job_queue/test_joblock_sweep_lifecycle.py` (359 ln — closes gaps g/k: start→first-tick-reclaim→stop, idempotent-restart, default=90, ge=1 rejection, lifespan doc-truth; 6/6 green, acceptance re-verified 26/26)
Plus transient proof artifacts preserved OUTSIDE the repo at `/tmp/j3-artifacts/` (raw + rescue copies). Final worktree state: HEAD `6622d85c`, tracked diff empty, staged empty, exactly the 4 untracked files above.

### 7. Residuals / risks
- **R1 (🟠 methodology):** base-proof file-copy broken by module-level `job_lock_sweep` import; test-file runbook lists an unprovable 4th repro. Recommend: (a) local imports or `importorskip` guard for sweep-dependent tests, (b) a dedicated `cleanup_terminal_job_locks` primitive test that runs at BOTH heads, (c) fix the runbook text.
- **R2 (🟢):** 9 sweep-dependent acceptance tests unprovable at base (acceptable — F3 is new surface; behavior verified at branch + PG).
- **R3 (🟢):** deferral-branch test models symptom (terminal mirror), not the full WAITING_CHILDREN→instance-layer causal chain of the original specimen.
- **R4 (🟢):** "lock stays gone" pinned by instant + idempotency only (no delayed re-check). Mitigated by F3 sweep + idempotency test.
- **R5 (🟢):** R8 healthy-path pins exercise LockRepository primitives, not the observer's own SQL.
- **R6 (🟢 divergence):** prod trigger body (manager.py:5537, narrower) vs `PHASE2_INSTALL_STATEMENTS` (wider) — my PG tests pin the PROD shape; recommend reconciling the shared constant to one source of truth.
- **R7 (🟢 pre-existing test-bug):** `test_filesystem_workdir` dotdot test is location-dependent (temp-dir escape hatch) — fails for any checkout under /tmp on macOS; harden fixture.
- **R8 (🟢):** F4 disjointness proven by doc block + 2 functional tests only; no dedicated named class.
- Pre-existing census families (58 base failures) unchanged — backlog, not this branch.
- Concurrency-pack script quirk (informational): under `set -euo pipefail` a failing pytest would abort before printing `RESULT: FAIL` — PASS lines trustworthy; flag to pack owner.
- The 4 disclosed additions are UNCOMMITTED per mission constraint — leader/dev must review and commit or discard.

### 8. Worktree end-state (J9)
`/tmp/ens-base-56391477` removed (git worktree list clean of it); feature worktree @ `6622d85c`, `git status --porcelain` = exactly the 4 disclosed untracked files; ports 15432/8088 verified untouched; `/tmp/j3-artifacts` preserved.

### Documentation Updated
- [x] RESULTS/2026-09-14-joblock-leak-verification.md — this report
- [ ] PACKS.md / QUARANTINE.md / MOCK_TESTS.md — no changes (no new pack scripts registered; no new flaky candidates; disposable PG documented here)

### Overall Status
- Exact-failure proofs (prime directive): ✅ 3/3 CONFIRMED (independent)
- Acceptance + family: ✅ 26/26 + 80/80 · PG: ✅ 25/25 (8 new gap-closing) · Gates: ✅ all (constitution, knob, concurrency, flags)
- Census: ✅ byte-identical, zero branch-induced regressions, 1 delta adjudicated environmental with proof
- **Testing Complete: ✅ READY — branch cleared for merge workflow; decide on adopting the 4 disclosed test additions**
