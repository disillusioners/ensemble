# HANDOFF — job-lookup-resolver-divergence (sonic lane) — v0.31.29

Commission: Ari (anomaly ea3dcdc7) — Diagnose + fix the JOB-LOOKUP RESOLVER DIVERGENCE (list-path vs direct-lookup-path) in agents-ensemble @ /home/nea/ensemble-src.

**Release vehicle: v0.31.29** (STAGED RELEASE ONLY — NO promote; Emperor ceremony pending).
Base commit: `8894f86a8` (`origin/latest` at lane open); drift merge `e0bfeaa74` folds `origin/latest` @ `0da8aa619` (`v0.31.28`).
Integration branch: `sonic/job-resolver-int`.

## Files touched across modules

- **Module 1 (`mod-fix-resolver` @ `96f21f1c5`):**
  - `daemon/services/work_resolver.py`
  - `daemon/tools/job_queue.py`
- **Module 2 (`mod-pins-and-registry` @ `0dd0a9927`):**
  - `tests/unit/test_job_lookup_resolver_divergence.py`
  - `.agents/tester/PACKS.md`
- **Drift Merge & Integration:**
  - `e0bfeaa74`: folded `origin/latest` (`0da8aa619`, `v0.31.28` project_get ladder) cleanly with zero conflicts.
- **Release Vehicle (v0.31.29):**
  - `CHANGELOG.md`
  - `daemon/__init__.py`
  - `pyproject.toml`
  - `tests/test_api.py`
  - `uv.lock`
  - `.agents/shared/planning/sonic-team/job-lookup-resolver-divergence/HANDOFF.md`

## What changed

- **Root Cause Established:**
  - Forensic analysis of live database and leader thread (`77ddcb9f`) proved that the underlying stored job row `a785828f-9e28-45a0-9e4f-2caa21de281e` (`status=done`, `terminal_reason=completed`, `job_type=task`) was a **JobItem-only** row (no corresponding `Task` table row).
  - The `job_list` output correctly emitted `...-9e4f-...`, while downstream invocations suffered from two human/agent copying hazards:
    1. Downstream typo drift across multiple transcript attempts (`9f4f`, `9f2f`, `9f2c`).
    2. Format/whitespace artifacts attached to copied IDs (trailing newlines, whitespace, quotes, or markdown backticks) which caused direct lookup tools (`job_get`, `job_continue`, `job_inject`) to fail with "Job ... not found" because `resolve_work` performed an exact literal string equality lookup.
  - Symmetrical JobItem-only handling: `resolve_work` and `job_continue` previously contained code paths biased toward dual-backed (Task + JobItem) rows, which could fail or misattribute terminal state for pure JobItems.

- **Resolver Hardening (`daemon/services/work_resolver.py`):**
  - Added `_normalize_work_id` helper that defensively strips leading/trailing whitespace and matched quotes (`"`, `'`), brackets (`[`, `]`, `(`, `)`), and backticks (`` ` ``) from input work IDs, failing soft on empty/non-string inputs.
  - Applied normalization in `resolve_work`, `per_kind_status_for`, and record conversion boundaries so that lookups against Task unique index and JobItem primary key are uniform and tolerant of formatting artifacts.
  - Explicitly guaranteed that JobItem-only rows speak the canonical status vocabulary and set `record.kind == "job"`.

- **Tool Layer Sanitization (`daemon/tools/job_queue.py`):**
  - Added `_strip_id` boundary helper to all 10 job tool entrypoints (`job_get`, `job_inject`, `job_continue`, `job_cancel`, `job_pause`, `job_resume`, `job_messages`, `job_tree`, `job_progress`, `job_answer`).
  - Hardened `job_continue` for JobItem-only rows so completed task-type jobs without Task shadows can be cleanly continued, preserving the M2 gate and soft-delete protections while clarifying terminal-state error messages.

- **Comprehensive Regression Pack (`tests/unit/test_job_lookup_resolver_divergence.py`):**
  - Added 31 unit tests covering:
    1. Row shape matrix: JobItem-only (seeded with live incident ID `a785828f-9e28-45a0-9e4f-2caa21de281e`), JobItem-only message mirror, Task-only report lane, and dual-backed.
    2. Input normalization matrix: trailing space, trailing newline, leading space, surrounding whitespace, quotes, backticks.
    3. Tool surfaces: `job_get`, `job_continue`, `job_inject` over real repos and factory.
    4. Anti-vacuity controls: verifying unknown IDs return None / "Job not found".
  - Registered in `.agents/tester/PACKS.md` as `## JOB-LOOKUP-RESOLVER-DIVERGENCE — Unit Pack Registration` without disturbing recent blocks.

## What is UNTESTED

- Live daemon E2E with real PostgreSQL cluster (tests run against in-memory SQLite and isolated test harnesses per house unit test conventions).
- Waiting-children watchdog child census: verified that the watchdog uses instance repo queries (`Instance.parent_id`) rather than the job resolver, so it was not directly affected by work ID normalization.

## Known risks

- Normalization only strips matched wrapper pairs and whitespace; an ID containing internal syntax corruption will still return "Job not found" (intended fail-closed behavior).
- Pre-existing mock expectation issues in `tests/test_job_queue_tools.py` (`TestResolverRoutedTools::test_watch_job_*` 4 failures due to tool index shift in older mock tests) remain untouched as they predate this commission on `origin/latest`.

## How to verify

1. Run the regression test pack in an isolated environment:
   ```bash
   .venv/bin/python -m pytest tests/unit/test_job_lookup_resolver_divergence.py -v
   ```
   Assert: 31 passed in <10s.
2. Run adjacent work resolver unit tests:
   ```bash
   .venv/bin/python -m pytest tests/unit/services/test_work_resolver.py -q
   ```
   Assert: 79 passed.
3. Check version bump:
   ```bash
   .venv/bin/python -m pytest tests/test_api.py -k test_health_check -q
   ```
   Assert: passed with version `0.31.29`.

## Bash-raises used

none

## Complexity self-assessment

low — clean diagnosis and modular fix touching only resolver normalization and tool boundary sanitization; zero database schema migrations or daemon concurrency changes.
