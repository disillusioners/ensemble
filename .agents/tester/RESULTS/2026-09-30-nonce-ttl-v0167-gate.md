# Nonce TTL 15→60min (ADR-036) — v0.16.7 Pre-Finalize Independent Gate

- **Date:** 2026-09-30 (gate run 04:20–05:3x UTC)
- **Branch:** `feature/nonce-ttl-60m` @ `4622c669` (3 commits over base `7c24fee4`: `3356944b` TTL change + strings + boundary pair + ADR-036; `087a83f3` mirror-drift pin + symbolic upper-edge + positive UPGRADE-ARMED assert; `4622c669` mechanical fold) **+ disclosed tester fix commit `814c0ed3`** (docs-only, gate finding F1 — see §6)
- **Verdict: 🟢 GREEN — PROCEED TO FINALIZE** (with one disclosed docs commit `814c0ed3` folded on top; branch is LOCAL-ONLY — `origin/feature/nonce-ttl-60m` absent, leader must push)
- **Worker instances:** preflight `df0e0b56`, P1 `1c48c38a`, P2a `ebcba6ea`, P2b `3631a458`, P3 `187e5d73`, P4 `d116ef6f`, P5 `c5c42a18`, P6 `0b15f0cf`, P7 `be2f3237`, P8 `94407ade`, fix/mutation `b1fdf8fb`

## 0. Gate-set satisfaction (commission items 1–7)

| # | Commission gate item | Result | Evidence |
|---|---|---|---|
| 1 | Upgrade corpus baseline + boundary pair + mirror-drift pin + mock-reality | ✅ PASS | 278P/0F/0E in 14.7s; real expiry arithmetic verified by quotes AND empirical mutation (§4) |
| 2 | Full dir `tests/unit/tools/` (expect 9 known reds + 5 skips, A/B-isolated on base) | ✅ PASS | Branch 3156c/3142P/9F/5S/0E; base reproduces all 9F + 5S verbatim, zero divergence (§3) |
| 3 | Frozen tool-name drift test stays green | ✅ PASS | 7/7 in 5s (`test_frozen_tool_name_discovery.py`) |
| 4 | `test_maintenancer_upgrade_gate_refuses.py` FULL file | ✅ PASS | 2/2 in 5s; docstring 60-min claim confirmed (§5) |
| 5 | `-k nonce` micro-gate (rename verification) | ✅ PASS | **26/26** (not 11 — commission note stale, §7 D1); renamed `test_manager_fallback_nonce_literal_matches_canonical` green |
| 6 | Stale-string sweep: ZERO 15-min claims in daemon/ agents/ docs/runbooks/ | ✅ PASS after fix | 1 stale hit found (`agents/ari/tools_note.md:415`) → fixed `814c0ed3` → re-sweep CLEAN (§6) |
| 7 | Ceremony spot-check: spoof + 3-factor full-pass, meaningful at 60min | ✅ PASS | interlock pack 278P/13.7s; both targets TTL-INDEPENDENT (confirmed) with quoted evidence (§5) |

## 1. Scope decision

Leader-provided targeted gate set honored exactly — no expansion, no reduction. Full-dir `tests/unit/tools/` run justified by the full-dir-per-merge lesson (repo rule; `daemon/tools/` touched) and executed as two timeout-safe halves (HALF_A 48 files/1588c, HALF_B 4 files/1568c; single dir run projected ~226s = too little headroom under the 300s cap). ensure.md blast radius: upgrade nonce lane only — concurrency Core #2/#3 out of scope (rationale §8), Release Gate not warranted (targeted change, no architecture move).

## 2. Per-pack results

| Pack | Invocation (essentials) | Result | Runtime |
|---|---|---|---|
| P1 upgrade corpus | `timeout 300 bash scripts/run_tests_scrubbed.sh tests/unit/tools/test_upgrade_journal.py tests/unit/tools/test_upgrade_tools.py --tb=short -q -rA` | ✅ 278P/0F/0E | 14.7s |
| P2a tools HALF_A (branch) | `timeout 300 bash scripts/run_tests_scrubbed.sh tests/unit/tools/ --ignore=<4 HALF_B files> --tb=short -q` | ✅ 1588c/1576P/**7F known**/5S/0E | 214.4s |
| P2b tools HALF_B (branch) | `timeout 300 bash scripts/run_tests_scrubbed.sh <4 HALF_B files> --tb=short -q -rA` | ✅ 1568c/1566P/**2F known**/0S/0E | 16.1s |
| P3 frozen tool-name | wrapper + `test_frozen_tool_name_discovery.py` | ✅ 7/7 | 5s |
| P4 maintenancer integration | wrapper + `tests/integration/test_maintenancer_upgrade_gate_refuses.py --override-ini="addopts="` | ✅ 2/2 | 5s |
| P5 nonce micro | wrapper + `tests/unit/tools/ -k nonce` | ✅ 26c/26P (3130 deselected) | 2.9s |
| P6 stale-string sweep | static greps (daemon/ agents/ docs/runbooks/) | 🚩→✅ 1 stale → fixed `814c0ed3` → CLEAN | <1min |
| P7 ceremony pack | `timeout 120 bash test/packs/upgrade_tool_interlock_unit_test.sh` (inner 110s) | ✅ 278P | 13.7s |
| P8 A/B base leg | detached worktree `/tmp/ens-nonce-base` @ `7c24fee4`, cd-isolated, same halves | ✅ 1587c/7F/5S + 1566c/2F/0S — verbatim match | 211.3s + 18.1s |
| Fix/mutation | quick-fix commit + worktree mutation @ `4622c669` | ✅ `814c0ed3`; width-lock PROVEN (§4) | ~3min |

All packs dual-layer timeout (outer `timeout 300`/`120` + per-test pytest-timeout / script-internal 110s). Env hygiene on every run: `unset ENSEMBLE_UPGRADE_LIVE ENSEMBLE_UPGRADE_SCRIPTS_DIR` + `export ENSEMBLE_SELF_ENV=dev` + wrapper POSTGRES/PG scrub (ambient `ENSEMBLE_SELF_ENV=live` on this host — LESSONS 2026-09-30 countermeasure applied; zero journal writes, zero live-port touches).

## 3. A/B isolation table (branch @ 4622c669 vs base @ 7c24fee4)

| Half | Collected (branch / base) | Failures branch | Failures base | Divergent |
|---|---|---|---|---|
| HALF_A | 1588 / 1587 (−1 mirror pin, branch-added) | 7 = archive ×5 + explore ×2 | **same 7 IDs verbatim** | NONE |
| HALF_B | 1568 / 1566 (−2 boundary pair, branch-added) | 2 = mission-terminal ×2 | **same 2 IDs verbatim** | NONE |
| Skips | 5 (PG-only ×4 + Windows-only ×1, all HALF_A) | — | same 5 IDs | NONE |

**All 9 pre-existing reds + 5 skips are base-identical ⇒ zero branch-caused failures. Base leg method note:** detached worktree, **cd-into-worktree isolation** (`PYTHONPATH` pin is defeated by the venv's `_editable_impl_ensemble.pth` — see LESSONS 2026-09-30-worktree-isolation).

Known pre-existing reds (unchanged debt, previously attributed; NOT this branch's): `TestAccessMemoryArchive` ×5 (access-memory denies archive — own ticket), `TestExploreCallerModelOverrides` ×2 (spawn-seam contract vs 1a40bc56, deferred), mission-terminal ×2 (`:179` AsyncMock comparison, `:268` assert_not_called).

## 4. Mock-reality verification (gate item 1) — quotes + EMPIRICAL proof

**Real arithmetic (quoted, main checkout @ 4622c669):**
- `daemon/tools/upgrade_journal.py:116` — `NONCE_TTL_S = 60 * 60` (single canonical constant, seconds)
- `daemon/tools/upgrade_journal.py:838` — `ttl_expires_at = iso_plus(now_iso(), NONCE_TTL_S)` → **absolute expiry stamped at mint**
- `daemon/tools/upgrade_tools.py:2598` — strict `datetime.now(tz=utc) > ttl` → refusal `nonce-expired: … TTL 60min elapsed` (**strict `>`**, not `>=`)
- Boundary construction `test_upgrade_tools.py:2645-2648/2682-2684` — offsets **symbolic from the imported constant** (`NONCE_TTL_S − 1` valid / `−(NONCE_TTL_S + 1)` expired); **zero fake-clock machinery** (grep-proven: no freezegun/monkeypatch-datetime)
- Positive assert `:2663-2670` — asserts real success token `"UPGRADE ARMED"` (emitted at `upgrade_tools.py:2869`) AND refusal-reason is None
- Mirror pin `test_upgrade_journal.py:737-764` — dual-mode: (a) `assert NONCE_TTL_S == 60*60`; (b) source-pins the `daemon/manager.py:4078` mirror literal

**Empirical mutation proof (throwaway worktree @ 4622c669, `NONCE_TTL_S` → `900`):**
- `test_manager_fallback_nonce_literal_matches_canonical` **FAILS** (`AssertionError: canonical NONCE_TTL_S drifted from ADR-036 (60min): 900`) → width-lock #1
- `test_nonce_ttl_boundary_59m59s_still_valid` **FAILS its top-of-body canonical fixture-lock** (`assert NONCE_TTL_S == 60*60`, `:2643`) → width-lock #2 (bonus — see D3)
- `test_nonce_ttl_boundary_60m01s_expired` **PASSES** → confirms the semantic layer is genuinely TTL-agnostic (symbolic offsets)
- **Conclusion: the corpus CANNOT pass under any TTL other than 60 minutes (two independent locks), while exercising the real clock + absolute-timestamp + strict-comparison arithmetic. No fake-clock exposure.**

## 5. Ceremony spot-check (gate item 7) + maintenancer file (gate item 4)

- `test_fabricated_nonce_with_no_window_refuses` (:2561-2602): empty windows + forged nonce string → refuses at factor 2 (`user-confirmation-missing`) **before any TTL evaluation**; asserts nonce absent from `pending_actions` + `markers == []`. **TTL-INDEPENDENT (confirmed)** — no ttl/iso_plus/now_iso references.
- `test_full_pass_consumes_nonce_and_arms` (:2709-2740): all three factors constructed (user_confirmed=True + `_stamp_window(source="api")` + fresh `_mint_nonce`); positive evidence: `"UPGRADE ARMED"` + `"nonce consumed (confirmed_source=api)"` + `pending_op.nonce_consumed=True` + `nonce_consumed` history event + promote marker. Fresh nonce ⇒ TTL never breached. **TTL-INDEPENDENT (confirmed)**.
- `tests/integration/test_maintenancer_upgrade_gate_refuses.py` FULL file: 2/2 green; docstring claims `single-use, 60-min TTL` (matches ADR-036); both pinned behaviors hold (refusal without nonce / arm with valid nonce).

## 6. Stale-string sweep (gate item 6) — finding F1 + fix

- 30+ `15min/900` hits classified across daemon/ agents/ docs/runbooks/ — **1 RELEVANT-STALE**: `agents/ari/tools_note.md:415` told Ari the nonce is "single-use, 15min TTL" (user-facing relay surface; file NOT in branch diff — branch missed it).
- All 5 touched runtime surfaces correctly state 60min (`upgrade_journal.py:116`, `:838`, `upgrade_tools.py:2925`, `manager.py:4078`, maintenancer runbook `:21`). All 4 planning docs handled (2 SUPERSEDED-NOTE blocks @ 087a83f3, ADR-036 entry @ 3356944b, 1 in-place correction @ 4622c669). dev.sh static: `--timeout-graceful-shutdown 10` present (:102), dev.sh NOT in branch diff.
- **Fix (disclosed tester quick-fix, precedent v0.16.6 `3de1621c`): commit `814c0ed3`** — 1 file / 1 line: `15min TTL` → `60min TTL — widened from 15min per ADR-036, 2026-09-30`. Pre-check: no test asserts tools_note content. Post-fix scoped + broad re-sweep: **CLEAN** (only permitted historical phrasings). Test-pack evidence validity unaffected (doc-only; zero test-behavior delta).

## 7. Findings & discrepancies (none blocking)

- **D1 (doc accuracy):** commission/commit-body claim `-k nonce → 11 collected` is wrong — actual **26** (rename purpose itself sound: `noncel`→`nonce` restored discovery). Claim absent from repo content (only in 4622c669 commit body). No action required; noted for commit-message hygiene.
- **D2 (arithmetic):** commission said "baseline 278P/0F **+** boundary pair + mirror pin"; on-disk truth: **278 TOTAL includes** the 3 new (pre-branch corpus was 275 = 107+168). Baseline intent (zero regressions + 3 new green) fully holds.
- **D3 (informational, strengthens gate):** the 59m59s boundary test carries a top-of-body canonical fixture-lock (`assert NONCE_TTL_S == 60*60`, `:2643`) — deliberate guard so the "still valid" semantics only ever assert against the canonical constant. Discovered via mutation; turns the width-lock from single into dual. No action.
- **F1 (fixed):** ari/tools_note stale TTL (§6).

## 8. ensure.md status (blast-radius scoped)

| Requirement | Status | Basis |
|---|---|---|
| Core #1 no regressions in changed packs | ✅ PASS | all scoped packs green; 9 reds A/B-proven pre-existing |
| Core #2/#3 concurrency/deadlock + sync-DB-on-loop | N/A — out of blast radius | change is upgrade nonce lane (journal/tools/manager-mirror doc); no lock/async/thread surface touched |
| Core #4 dev.sh graceful-shutdown flag | ✅ PASS | static grep :102; dev.sh not in diff |
| Important #1 await-callers | N/A | no async conversions in the 11-file diff |
| Release Gate | NOT RUN — not warranted | targeted TTL widening, no architecture/cross-module refactor |
| Contradiction notices | NONE | gate set already pack-shaped |

## 9. Overall status

- Unit (tools dir, both halves + corpus + frozen + micro): ✅ (9F/5S = base-identical known debt)
- Integration (maintenancer): ✅
- Ceremony/interlock pack: ✅
- Static sweep: ✅ (after `814c0ed3`)
- ensure.md (scoped): ✅
- **TESTING COMPLETE: 🟢 READY — proceed to v0.16.7 finalize on tip `814c0ed3`** (evidence gathered @ `4622c669`; fix is doc-only, verified test-inert). Reminder: branch local-only — push before finalize/promote lanes.

*Quarantined/known-debt note: the 9 pre-existing reds remain documented test debt (archive ×5 own-ticket; explore ×2 deferred vs 1a40bc56; mission-terminal ×2). Rising-count risk: none this gate — identical set for ≥3 consecutive gates.*
