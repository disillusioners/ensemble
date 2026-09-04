# Phase 1: PR1 Port — Instrumentation (Manual Re-Apply)

> Rev 2.1 — adversarial-review fold (2026-09-04): 3 blockers + 12 warnings + suggestions applied; design foundation verified

## Objective

Land v1's PR1 (instrumentation: timing brackets + `log_prune` entry/exit + `checkpoint_perf.py` clean add + `GATE_SUITES.txt` fresh-on-v2 regen + `tools/lint/allowlist.txt` empty clean add) onto v2 via manual re-apply (per technical-analysis.md §"Per-PR landing method"). Zero behavior change to GET /messages; pure additive observability. Establishes the timing/logging foundation that Phases 2..4 will rely on.

## Port Method

**Manual re-apply from `git diff v1-base..0db1a768`** (per technical-analysis.md table row PR1). v1 only modifies `daemon/persistence.py` + `daemon/services/maintenance.py`; both files have been rewritten in v2's middle sections (persistence.py has message-display-latency + identity-field fixes; maintenance.py has defer-gate widen predicates). The hunks are too small + too interwoven with v2's adjacent churn to cherry-pick cleanly. Re-apply by hand: copy v1's timing bracket + `time.perf_counter()` + `log_prune` call from v1's diff, place at v2's current anchors.

## Files Touched

| File | Change Type | Source |
|------|-------------|--------|
| `daemon/persistence.py` | Manual re-apply — add `import time` + `from daemon.checkpoint_perf import (time_saver_op, log_messages_api)` + `t0 = time.perf_counter()` + `state = await time_saver_op("aget", ...)` + `log_messages_api(...)` on early-return at `get_instance_messages` | v1 `0db1a768` diff (persistence.py portion). **Architect §1.2 correction:** `git diff 58260f35..2f80d45b` returns ZERO lines for `daemon/persistence.py` — the file is byte-identical, so this is actually a clean cherry-pick target, NOT manual re-apply. The TA's "HIGH conflict" claim was wrong; v2's compaction + identity work churned adjacent files but NOT persistence.py itself. The manual re-apply column in the TA's port-strategy table was based on a false premise (see plan-overview.md port-strategy paragraph for the corrected rationale). For Phase 1, KEEP manual re-apply as a defensive fallback (the v1 hunks are small + manual review confirms no v2 churn overlaps) but expect cherry-pick to succeed byte-clean. |
| `daemon/services/maintenance.py` | Manual re-apply — add `t0 = time.perf_counter()` + `log_prune("prune-entry", ...)` + `log_prune("prune-exit", ...)` in `_prune_per_thread_checkpoints` (Operation D) | v1 `0db1a768` diff (maintenance.py portion). **Architect §1.2 correction:** `git diff 58260f35..2f80d45b` returns ZERO lines for `daemon/services/maintenance.py` — byte-identical; defer-gate fix landed in `job_queue_service.py`, NOT maintenance.py. Operation E anchor `:448→:450` (the eventual PR4 site) intact. Same defensive-fallback rationale as persistence.py: KEEP manual re-apply as the safe path, expect cherry-pick clean. |
| `daemon/checkpoint_perf.py` | CLEAN ADD — create from v1 (`0db1a768` content), no edits | v1 `0db1a768` |
| `tools/lint/allowlist.txt` | CLEAN ADD — empty file (matches v1's empty state) | v1 `0db1a768` |
| `tests/integration/gate_suites/GATE_SUITES.txt` | CLEAN ADD then REGENERATE — copy v1's structure from `fc908945`, then REGENERATE the header + table at v2-base HEAD per the file's own header method (per-file `uv run pytest <file> -o addopts= --collect-only -q -p no:cacheprovider --no-header` in a clean worktree). DO NOT copy v1's 37-row/411-test manifest verbatim — regenerate fresh for v2 | Per v1 file header + Phase 0 T0.4 pre-counts |
| *all remaining `0db1a768` files ported verbatim* | CLEAN ADD / port (catch-all per adversarial-review W1) | v1 `0db1a768` per `git show --stat 0db1a768` = 13 files changed (full per-commit surface incl. 8 test files): `.agents/tester/QUARANTINE.md` (4 lines, registered 3 pre-existing-failure files for exclusion), `daemon/checkpoint_perf.py` (129 lines clean add), `daemon/persistence.py` (100 lines, listed in this table), `daemon/services/maintenance.py` (88 lines, listed in this table), `tests/integration/gate_suites/GATE_SUITES.txt` (114 lines, listed in this table), `tests/integration/gate_suites/__init__.py` (0 lines), `tests/integration/gate_suites/test_gate_suite_pause_resume.py` (136 lines), `tests/integration/test_messages_response_fixture_capture.py` (657 lines), `tests/integration/test_no_saver_imports_in_routers.py` (281 lines, §33 import scan + receiver-agnostic .alist scan, 6 tests), `tests/unit/persistence/__init__.py` (0 lines), `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json` (128 lines frozen fixture), `tests/unit/persistence/test_checkpoint_perf_logging.py` (510 lines, 19 unit tests for env-suppression + walk-exception), `tools/lint/allowlist.txt` (10 lines, empty); the 8 previously-unlisted test files (`__init__.py` ×2, `test_gate_suite_pause_resume.py`, `test_messages_response_fixture_capture.py`, `test_no_saver_imports_in_routers.py`, `fixtures/get_instance_messages_pre_phase1.json`, `test_checkpoint_perf_logging.py`, `.agents/tester/QUARANTINE.md`) port verbatim from `git show fc908945:<path>`. Verify byte-equality (or doc-only diffs) on port. | v1 `0db1a768` — see `git show --stat 0db1a768` for full surface |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| T1.1 | Read v1's PR1 diff end-to-end: `git show 0db1a768` (full commit) + `git show fc908945 -- daemon/checkpoint_perf.py daemon/persistence.py daemon/services/maintenance.py` (the final shape) + `git show 0db1a768 -- tools/lint/allowlist.txt tests/integration/gate_suites/GATE_SUITES.txt` (the clean adds). Document the exact hunk shapes in `phase1-diff-analysis.md`. | Phase 0 DONE | Diff-analysis file exists; hunk boundaries + v2 insertion anchors documented |
| T1.2 | Create `daemon/checkpoint_perf.py` clean — copy v1 content verbatim from `git show fc908945:daemon/checkpoint_perf.py`. Verify file imports + module-level docstring + helper functions match v1 byte-equality. | T1.1 | File created; byte-identical to v1 `fc908945` |
| T1.3 | Create `tools/lint/allowlist.txt` empty — matches v1's empty state. Verify the file has a header comment matching v1's style. | T1.1 | File created; empty (matches v1) |
| T1.4 | Manual re-apply v1's PR1 timing bracket into `daemon/persistence.py::get_instance_messages` — add `import time` + `from daemon.checkpoint_perf import (...)` at module-level imports (preserve v2's existing imports); insert `t0 = time.perf_counter()` AFTER v2's added imports/docstrings at the head of `get_instance_messages`; wrap `await saver.aget(...)` with `state = await time_saver_op("aget", ...)`; add `log_messages_api(...)` call on early-return paths. Resolve conflicts per technical-analysis.md §"`daemon/persistence.py`" resolution rule (insert AFTER v2's added imports/docstrings; preserve any v2 docstrings by moving them to the post-insertion location). | T1.2 | `get_instance_messages` has timing bracket + `time_saver_op` wrapper + `log_messages_api` calls; imports inserted at correct position; `git diff` confirms v1's hunks + v2's prior changes both intact |
| T1.5 | Manual re-apply v1's PR1 timing bracket into `daemon/services/maintenance.py::_prune_per_thread_checkpoints` (Operation D) — insert `t0 = time.perf_counter()` + `log_prune("prune-entry", ...)` BEFORE the `find_excess_checkpoint_groups` call; add `log_prune("prune-exit", ...)` in a finally block. v1's timing calls go OUTSIDE v2's additional logging (per technical-analysis.md §"`daemon/services/maintenance.py`" resolution rule). | T1.2 | `_prune_per_thread_checkpoints` has timing bracket; v2's existing logging preserved; `git diff` confirms both v1's + v2's prior hunks intact |
| T1.6 | Create `tests/integration/gate_suites/GATE_SUITES.txt` — copy v1's structure from `fc908945`, then REGENERATE the header + table at v2-base HEAD. Use the v2 `addopts` (per Phase 0 T0.4). Cross-check with aggregate collect-only over all paths. Record regen provenance in the file's header (commit SHA, regeneration date). | T1.1, Phase 0 T0.4 | File created with v2-specific counts; per-file + aggregate counts match; regen provenance recorded |
| T1.7 | Run port verification: `pytest tests/unit/persistence/test_checkpoint_perf_logging.py -v` (the v1 instrumentation test) on v2 tip. Verify GREEN. If FAIL, diff v1's `tests/unit/persistence/test_checkpoint_perf_logging.py` against v2's tree (may have moved/refactored). | T1.4, T1.5, T1.6 | All tests GREEN; result recorded in `phase1-results.md` |
| T1.8 | Run drift-regression checks: `tests/unit/persistence/test_checkpoint_perf_logging.py` + 6 vocabulary grep guards (per Phase 0 T0.7) + facade-forwarding guards (`tests/unit/test_manager_enqueue_message_work_id_required.py` + `tests/integration/test_job_driven_enqueue_work_id_facade.py`) + mission stale-fixture 7-node family. All must stay GREEN / unchanged. | T1.7 | All checks PASS; v2-baseline counts unchanged from Phase 0; `phase1-results.md` records deltas (expected: 0 deltas) |

## Coupling

- **Independent of:** Phase 2 (PR2 side table), Phase 3 (PR3 read flip), Phase 4 (PR4 prune), Phase 5 (PR5 closure)
- **Loose with:** Phase 4 — both touch `daemon/services/maintenance.py`, but PR1's timing lives in Operation D and PR4's Operation E is AFTER Operation D (disjoint hunks; can land in either order)
- **Phase 1 → Phase 5:** Phase 5 FR-5 (full saver-op structured logs) extends PR1's `log_saver_op` / `log_messages_api` / `log_prune` infrastructure. Phase 1 must land first.

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | `daemon/persistence.py` import collision — v2 may have added imports that overlap v1's `import time` or `from daemon.checkpoint_perf import ...` | Low (deduplication) | T1.4 uses `git diff` to verify no duplicates; if v2 already imports `time`, skip v1's line; if v2 imports `daemon.checkpoint_perf` partially, add only the missing names |
| 2 | `daemon/services/maintenance.py` Operation D hunks shifted in v2 | Low | T1.5 searches for `find_excess_checkpoint_groups` text + inserts timing bracket around it; if the function moved, the search hits v2's current location |
| 3 | `GATE_SUITES.txt` regeneration drifts from v1's 37-row baseline | Low (the v2 count will differ; that is the WHOLE POINT) | T1.6 notes the v2-specific count in the file's header provenance block; the diff vs Phase 0 T0.4 pre-counts documents the delta |
| 4 | `tools/lint/allowlist.txt` not empty on v2-tip (v2 has its own allowlist entries) | Low (port preserves v1's empty intent; v2 entries stay separate) | T1.3 reads v2-tip for an existing `tools/lint/allowlist.txt`; if present with v2 content, APPEND v1's intent (or skip if v2 already serves the purpose) |

## Drift-Regression Checks (from technical-analysis.md §"Drift-Regression Verification Protocol")

Run AFTER T1.8 commits:
- `tests/unit/persistence/test_checkpoint_perf_logging.py` — PR1 instrumentation test
- 6 vocabulary grep guards (per Phase 0 T0.7) — must show 0 NEW diffs vs `phase0-grep-baseline.md`
- `tests/unit/test_manager_enqueue_message_work_id_required.py` + `tests/integration/test_job_driven_enqueue_work_id_facade.py` — facade-forwarding guards stay GREEN
- `tests/job_queue/` (regression_job_queue partition) — mission stale-fixture 7-node family stays at v2-base state
- `tests/services/test_instance_messaging_queue_routing.py` — WC-wake kill-switch state preserved

## Tests Ported vs Regenerated

| Item | Treatment | Rationale |
|------|-----------|-----------|
| `tests/unit/persistence/test_checkpoint_perf_logging.py` | **PORT** (copy from v1 if missing on v2; KEEP if v2 has equivalent) | v1 instrumentation test; PR1's load-bearing regression boundary |
| `tests/integration/gate_suites/GATE_SUITES.txt` | **REGENERATE** (copy v1 structure, fresh v2 counts) | NEVER copy v1's 411-test manifest; the file's own header mandates regeneration |
| `tools/lint/allowlist.txt` | **PORT** empty (v1 ships empty) | PR1 ships the allowlist concept; empty is the Phase 1 scope |

## Rollback Note

`git revert <commit>` per PR1 commit. The commit set is: `<manual-reapply-persistence-and-maintenance> <checkpoint_perf-clean-add> <allowlist-clean-add> <gate_suites-regen>`. Revert order: regen last, clean adds second, manual re-apply first (preserves the invariant that manual re-apply is the cleanest revert target). Phase 1 has NO behavioral effect on GET /messages; reverting only removes the observability instrumentation.

## Acceptance / Effort / Impact / Blast

| Field | Value |
|-------|-------|
| Acceptance | PR1 instrumentation lands on v2 with zero behavior change to GET /messages; timing brackets in persistence.py + maintenance.py; `checkpoint_perf.py` exists; `GATE_SUITES.txt` regenerated; allowlist.txt empty; v1's `test_checkpoint_perf_logging.py` GREEN on v2; all drift-regression checks pass |
| Effort | **S** (1-2 hours; manual re-apply is small + clean adds are zero-touch) |
| Impact | **M** (establishes observability foundation; required for Phase 5 FR-5) |
| Blast radius | **L** (additive only; zero behavior change; easily reverted) |

## Requirements Traceability

- **FR-5** (full saver-op structured logs) — Phase 1 lays the infrastructure; Phase 5 extends to full surface
- **NFR-8** (every saver op emits one structured log line) — Phase 1 establishes `log_saver_op`; Phase 5 emits per-op lines
- **AC-1.2** (PR2/PR3/PR4 suites green) — Phase 1's regen of GATE_SUITES.txt sets up the post-PR gate
- **NFR-13, NFR-14** (file-backed SQLite recipe; bare `uv sync`) — Phase 1 respects these in T1.7 verification
- **C-7** (bare `uv sync`) — Phase 1 setup uses bare `uv sync` (no `--extra dev`)
- **C-14** (no `except BaseException:`) — Phase 1's timing bracket is purely additive; no exception handling change
