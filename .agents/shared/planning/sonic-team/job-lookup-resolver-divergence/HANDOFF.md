# HANDOFF — job-lookup-resolver-divergence (sonic lane) — v0.31.31

Commission: Ari (anomaly ea3dcdc7) — Diagnose + fix the JOB-LOOKUP RESOLVER DIVERGENCE (list-path vs direct-lookup-path) in agents-ensemble @ /home/nea/ensemble-src.
Re-dispatch: sonic-leader (REVIEW: NEEDS-REVISION, re-dispatch cycle 1) — closed all 3 review blockings (post-resolution canonical ID re-querying, 61-node full decoration matrix, deliverables 2c+4 separation disclosures).

**Release vehicle: v0.31.31** (STAGED RELEASE ONLY — NO promote; Emperor ceremony pending).
Base commit: `faff9c1de` (`origin/latest` @ v0.31.30).
Integration branch: `sonic/job-resolver-int`.

## Files touched across modules

- **Module 1 (`mod-fix-resolver` @ `bd364876f`):**
  - `daemon/services/work_resolver.py`
  - `daemon/tools/job_queue.py`
- **Module 2 (`mod-pins-and-registry` @ `ec9a44486`):**
  - `tests/unit/test_job_lookup_resolver_divergence.py`
  - `.agents/tester/PACKS.md`
- **Integration & Release Vehicle:**
  - `CHANGELOG.md`
  - `daemon/__init__.py`
  - `pyproject.toml`
  - `tests/test_api.py`
  - `uv.lock`
  - `.agents/shared/planning/sonic-team/job-lookup-resolver-divergence/HANDOFF.md`

## What changed

- **Root Cause & Forensic Truth (Anomaly ea3dcdc7):**
  - Forensic analysis of live database and leader thread (`77ddcb9f`) proved that the underlying stored job row `a785828f-9e28-45a0-9e4f-2caa21de281e` (`status=done`, `terminal_reason=completed`, `job_type=task`) was a **JobItem-only** row (no corresponding `Task` table row).
  - The `job_list` output correctly emitted `...-9e4f-...`. The transcript refusals on `9f4f`, `9f2f`, and `9f2c` were **CORRECT fail-closed behavior** on genuinely typo'd IDs (anti-vacuity controls in the test pack pin that non-existent IDs must fail closed).
  - The true defects were two-fold:
    1. Decoration artifacts (whitespace, trailing newlines, surrounding quotes `"`/`'`, and backticks `` ` ``) attached to copied IDs caused direct lookups (`job_get`, `job_inject`, `job_continue`) to miss the exact indexed column in SQL.
    2. Post-resolution re-queries: in `job_continue` (line 2114), after `resolve_work` normalized the decorated ID and returned the JobItem record, the W3 soft-delete race-guard re-queried `get_job(old_job_id)` with the raw caller ID instead of the canonical normalized ID, producing a false "Job ... has been deleted and cannot be continued". Similar seams existed in `job_cancel`, `job_retry`, `job_delete`, `job_restore`, `watch_job`/`watch_jobs`, and `watch_mission`.

- **Resolver Hardening (`daemon/services/work_resolver.py`):**
  - Added `_normalize_work_id` helper that defensively strips leading/trailing whitespace and matched quotes (`"`, `'`), brackets (`[`, `]`, `(`, `)`), and backticks (`` ` ``) from input work IDs, failing soft on empty/non-string inputs.
  - Applied normalization in `resolve_work`, `per_kind_status_for`, and record conversion boundaries so that lookups against Task unique index and JobItem primary key are uniform and tolerant of formatting artifacts.
  - Explicitly guaranteed that JobItem-only rows speak the canonical status vocabulary and set `record.kind == "job"`.

- **Tool Layer Sanitization & Canonical Keying (`daemon/tools/job_queue.py`):**
  - Added `_strip_id` boundary helper to all 17 work-id-taking tools (`job_get`, `job_inject`, `job_continue`, `job_cancel`, `job_retry`, `job_delete`, `job_restore`, `job_pause`, `job_resume`, `job_messages`, `job_tree`, `job_progress`, `job_answer`, `watch_job`, `unwatch_job`, `watch_jobs`, `watch_mission`).
  - Keyed every post-resolution follow-up lookup/mutation on `record.work_id` instead of the raw caller ID:
    - `job_continue`: `old_job = await job_service.get_job(record.work_id)` (W3 race-guard re-query uses canonical ID).
    - `job_cancel`: `get_by_work_id` and `cancel_job` use `record.work_id`.
    - `job_retry`, `job_delete`, `job_restore`: `retry_job`, `soft_delete_job`, `restore_job` use `record.work_id` (fallback to raw ID on unwired resolver).
    - `watch_job`, `watch_jobs`: `add_watch` and `notify_watchers` use `record.work_id`.
    - `watch_mission`: `pre_dispatch_receipt = record.work_id` ensures the watch row is armed on the clean canonical ID so event queries match.

- **Expanded Regression Pack (`tests/unit/test_job_lookup_resolver_divergence.py`):**
  - Expanded from 31 to **61 nodes**:
    1. Row shape matrix: JobItem-only (seeded with live incident ID `a785828f-9e28-45a0-9e4f-2caa21de281e`), JobItem-only message mirror, Task-only report lane, and dual-backed.
    2. Full 7-decoration matrix across all tool surfaces: `job_get`, `job_continue`, `job_inject` pinned across trailing space, trailing newline, leading space, surrounding whitespace, double quotes, single quotes, and backticks.
    3. `watch_mission` receipt resolution (`TestWatchMissionReceiptResolution`, 15 nodes): dispatched receipt and pre-dispatch receipt resolution across the full 7-decoration matrix with real `create_mission_watch_tools` + real `MissionResolver` + real `JobWatcherRepository`.
    4. Anti-vacuity controls: verifying unknown decorated IDs return None / "Job not found".
  - Registered in `.agents/tester/PACKS.md` as `## JOB-LOOKUP-RESOLVER-DIVERGENCE — Unit Pack Registration` (61 nodes, RC=0).

## What is UNTESTED

- Live daemon E2E with real PostgreSQL cluster (unit pack runs against in-memory SQLite with StaticPool per house unit test conventions; no PostgreSQL cluster exercised).
- Non-job queue tools (`queue_*`, `job_create`, `job_list`): these take no work ID parameter and remain untouched by design.

## Known risks

- Deliverables 2c & 4 separation (blast radius audit):
  - **Mission completion gates & watchers:** Reviewer code-level audit confirmed that callers of the changed functions in mission-completion gates (`job_queue_service.py:561/:880`, `child_reports.py:6269`, `task_processor.py:1137`, `job_feedback_observer.py:1518/:2192`) pass clean database-stored IDs, so normalization is a widening no-op on those internal paths.
  - **Watchdog child census:** Disjoint — `waiting_children_watchdog.py:839` performs dependency-bus queries keyed directly on `Instance.parent_id`, with zero `resolve_work` usage.
  - **`unwatch_job` residual:** `unwatch_job` has no resolver lookup step (it deletes from the watcher repo directly); it strips whitespace at the boundary, but an unwatch handle bearing quotes or backticks will miss the stored clean key.
- Pre-existing mock expectation issues in `tests/test_job_queue_tools.py` (`TestResolverRoutedTools::test_watch_job_*` 4 failures due to tool index shift in older mock tests) remain untouched as they predate this commission on `origin/latest`.

## How to verify

1. Run the widened regression test pack:
   ```bash
   .venv/bin/python -m pytest tests/unit/test_job_lookup_resolver_divergence.py -q
   ```
   Assert: 61 passed in <10s.
2. Run adjacent work resolver unit tests:
   ```bash
   .venv/bin/python -m pytest tests/unit/services/test_work_resolver.py -q
   ```
   Assert: 79 passed.
3. Check version bump:
   ```bash
   .venv/bin/python -m pytest tests/test_api.py -k test_health_check -q
   ```
   Assert: passed with version `0.31.31`.

## Bash-raises used

none

## Complexity self-assessment

low — focused bug fix in ID normalization and follow-up query keying; zero schema migrations; zero background threads added.
