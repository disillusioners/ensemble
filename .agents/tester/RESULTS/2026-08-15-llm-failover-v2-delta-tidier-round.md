# Delta Verification — LLM Failover v2 Consolidated Tidier Round

Date: 2026-08-15
Branch: `feature/llm-failover-v2-sites` @ `dcbf5b87` (delta `7198bdd5..dcbf5b87`, 1 commit, 9 files, +994/−419)
Instance IDs: f5a94443 (recon), bc1a66d5, 68ab9690, 03b97245, 333e8ca2, 21d49355, 76d3c892, ec092227, 3ba7dc24, 3ff36aa7, c5b65f84 (packs), c5e9a1b8 (adversarial)
Verdict: **SHIP** ✅

## Summary
- Delta items verified: 4/4 (cap behavior, wiring pins, battery, scope confinement)
- Failover battery: 305/305 PASS (269 brief-specified + 36 bonus v1-adversarial) — every pack at exact expected count
- Regression spot-checks: 3/3 PASS, 0 new failures (compaction 206/206, skill_evolution 47/47, concurrency 66P/19S/0F — see Baseline Note)
- Adversarial mutation testing: 4/4 mutation classes caught by the new behavioral pins; 0 blind spots
- ensure.md (in-scope): Critical 2/2 PASS, Important 2/2 PASS (covered), static dev.sh flag PASS
- Quarantined: 1 skipped (pre-existing, unrelated)

## Scope Decision
> Full suite not warranted. This is a delta check on top of the SHIP campaign at 8b135da7 (17 packs, ~1,290 tests). Scope = diff `7198bdd5..dcbf5b87` (9 files: facade + 6 secondary sites + v2 test suite + tester catalog). Ran: 7 failover packs + 3 regression spot-checks + recon + adversarial. Skipped: remaining ~240 packs — zero overlap with LLM-invocation path. Reason: no job/task/queue files touched (recon-verified), no architecture change.

## Item 1 — Facade wall-clock cap: ✅ VERIFIED
- Static (recon): `stop=stop_after_attempt(max) | stop_after_delay(cap)` at BOTH entry points — LangChain binding `llm_failover.py:523`, raw-SDK facade `llm_failover.py:810`; default 45.0 at :483/:578/:661.
- Functional (adversarial, tiny caps): raw facade stopped at 0.151s / 97 calls against a 1000 budget; LangChain at 0.151s / 62 wire attempts — cap fires BETWEEN attempts, budget not exhausted.
- Site-level 30s caps intact: title_generation:135/140, child_reports summarize:611/617, repair:1212/1222 (`config.timeout_seconds`), compaction:1018, keyword_extraction:395 (`timeout_s`).
- Zero-drift backup-unset: exact budget stops 2/2, 5/5, 3/3 — cap never altered no-backup attempt semantics.

## Item 2 — Wiring pins: ✅ BITE (4/4 mutation classes caught)
Baseline 6/6 pin invocations pass; each mutation below made the intended pin(s) FAIL:
| Mutation | Pins that failed |
|---|---|
| B1 raw-facade `| stop_after_delay` removed | structural + retry-storm pins (attempts=100 unbounded) |
| B2 LangChain-binding cap removed | `test_wrap_langchain_facade_uses_wall_clock_cap` |
| B3 pre-F1 backup-strip at repair site re-introduced | `test_child_reports_repair_does_not_strip_backup_before_facade` |
| B4 embedding comparator reverted to raw `!=` | both `test_equivalent_urls_do_not_disable_failover` params |
- No `inspect.getsource` text-count pins remain (4 narrow import-line checks only).
- Method: git worktree mutations at `/tmp/adv_wt`, never main checkout; restored and verified 6/6 after each revert.

## Item 3 — Battery: ✅ 269/269 exact + 36 bonus
| Pack | Expected | Actual | Result |
|---|---|---|---|
| llm_failover_unit_test | 64 | 64 passed (10.74s) | PASS |
| llm_error_classifier_unit_test | 74 | 74 passed (0.48s) | PASS |
| graph_retry_unit_test | 18 | 18 passed (0.74s) | PASS |
| llm_failover_v2_unit_test | 45 | 45 passed (88.89s) | PASS |
| llm_failover_v2_adversarial_unit_test | 48 | 48 passed (1.06s) | PASS |
| llm_failover_v2_resilience_unit_test | 20 | 20 passed (1.19s) | PASS |
| llm_failover_adversarial_unit_test (bonus) | 36 | 36 passed (1.52s) | PASS |
| compaction_unit_test (regression) | 206 | 206 passed (0.98s) | PASS |
| skill_evolution_unit_test (regression) | 47 | 47 passed (1.35s) | PASS |
| concurrency_atomic_unit_test (regression) | 66P/19S | 66P/19S/0F (5.63s) | PASS |

### Baseline Note — concurrency 66P/19S vs brief's 91P/74S
The brief's 91P/74S was calibrated against a PACKS.md row whose Last-Run figure came from a 13-file invocation (7 registered + 6 accrued files). The 7 registered files cap at 85 tests — 91+74 arithmetically impossible; 66P/19S matches the canonical baseline (ensure.md mapping rows, 2026-08-02 row) exactly and 0 failures occurred. Root-caused by worker c5b65f84; PACKS.md row reconciled by tester (2026-08-15). NOT a regression.

## Item 4 — Scope confinement: ✅ CONFINED
- Changed files: facade (`llm_failover.py`), 6 documented secondary sites (compaction.py, child_reports.py, keyword_extraction.py, title_generation.py, skill_evolution_service.py, skill_search_service.py — last two = raw-SDK skill services per LLM-HA-v2 note), v2 test suite, PACKS.md catalog. Nothing else.
- Zero changes to job/task/queue files (claim_pending_task, turn_transitions, reconcile_turn_mirror, job_processor, job_locks) → ensure.md mandatory full e2e gate NOT triggered.
- HEAD `dcbf5b87` confirmed by two workers independently; tree clean (4 untracked agent-notes .md files pre-existing).

## ensure.md Validation (blast-radius scoped)
- Critical: ✅ No regressions in changed packs (all PASS); ✅ Deadlock/concurrency integrity (concurrency pack 66P/0F)
- Critical: ✅ No sync DB calls on asyncio loop (thread-identity tests in pack, passed)
- Critical: ✅ `dev.sh` includes `--timeout-graceful-shutdown 10` (grep, dev.sh:102)
- Important: ✅ async-await callers + original deadlock scenario (in-pack)
- Release Gate: not triggered (no job/task/queue change, not architecture)

## Findings (non-blocking, for the record)
1. 🟢 keyword_extraction facade cap tightened 45→40s default (`wall_clock_cap_s=timeout_s` @ keyword_extraction.py:377) — semantic narrowing beyond pure "hygiene" in a commit labeled tidier-round; behavior is strictly safer (cap ≤ site cap), zero-drift preserved. Noted for commit-message accuracy only.
2. 🟢 compaction lost its dedicated wiring pin (recon: old getsource block removed, replacement pin covers child_reports.repair instead). Residual coverage: adversarial wire tests + `_calls_wrap_langchain_failover` siblings — acceptable, defensive pin add is a nice-to-have.
3. 🟢 Title-generation/summarize/compaction sites rely on facade default 45s with 30s site caps as primary defense — intentional belt-and-braces ordering; firing-pins exist only at raw-SDK facade (pin #4). Fine as designed.
4. 🟢 PACKS.md concurrency row was internally inconsistent (7 files ↔ 13-file baseline) — reconciled by tester this session; future campaigns inherit a correct 66P/19S baseline.

## Quick Fixes Applied
- None needed by workers (all green). Tester applied 1 documentation fix: PACKS.md concurrency row reconciliation (this file documents it).

## Overall Status
- Cap behavior: ✅ | Pins: ✅ | Battery: ✅ 305/305 | Scope: ✅ | ensure.md: ✅ 3/3 Critical + 2/2 Important
- **Verdict: SHIP**
