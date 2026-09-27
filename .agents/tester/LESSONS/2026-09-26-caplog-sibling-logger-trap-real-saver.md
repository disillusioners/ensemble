# caplog sibling-logger trap — real-saver dry-run tests (pre-existing, quarantined 2026-09-26)

**Class**: silent-empty `caplog` capture — test asserts on log lines that ARE emitted but on a DIFFERENT logger than the one `caplog.at(...)` filters.

**Instance**: `tests/integration/checkpoint_prune_real_saver.py` — `TestRealSaverWritePruneResume::test_real_saver_write_retention_prune_blob_prune_resume` (:317) and `TestRealSaverDryRunReport::test_real_saver_dry_run_report_line_shape` (:948) both do `caplog` capture with `logger="daemon.checkpoint_perf"` (:304/:938), but the per-sweep dry-run summary line is emitted on `daemon.services.checkpoint_prune` (`daemon/services/checkpoint_prune.py:63` getLogger(__name__), emission :267-279). `daemon.checkpoint_perf` is a SIBLING logger, not a parent → caplog never sees the line → `summary_lines == []` → deterministic fail. Passing sibling at :563 uses the correct logger name — the in-file contrast is the tell.

**Why it survived**: both tests fail deterministically, so they'd never pass by luck; they were simply never green in recent cycles (discovered during the 2026-09-26 checkpoint-retention gate; A/B base-proof at `0f9bda12` = identical failures → pre-existing, not caused by the retention diff).

**Pattern to remember**:
1. When a log-assertion test fails with an EMPTY list, FIRST check the logger NAME against the emission site's `getLogger(...)` — before suspecting the emission logic.
2. caplog filters by logger ancestry: only the named logger or its DESCENDANTS are captured. Siblings with similar names (`daemon.checkpoint_perf` vs `daemon.services.checkpoint_prune`) are invisible to each other.
3. Attribution protocol that worked: identical invocation on a temp linked worktree at base + disposable PG on a fresh port → byte-identical per-test outcomes = PRE-EXISTING (quarantine, don't chase inside someone else's commission).

**Fix (2-char test edit, still pending 2026-09-26)**: change the two caplog targets to `daemon.services.checkpoint_prune`; un-quarantine requires 3× clean re-run per quarantine policy.
