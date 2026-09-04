# Phase 1 — T1.1 Diff Analysis: v1 PR1 (`0db1a768`) → v2

> Date: 2026-09-03 (UTC) | Working HEAD: `901d96e5` (branch `feature/langgraph-checkpoint-perf-v2`)
> Method: manual re-apply (cherry-pick FORBIDDEN). `git show 0db1a768 --stat` = ground truth for the 13-file surface (13 files, +2125/−32).
> Architect §1.2 verification: `git diff 58260f35 2f80d45b -- daemon/persistence.py daemon/services/maintenance.py` → **0 lines** (both hot files byte-identical between v1-base and v2-base). Confirmed again against the live worktree: `git diff 58260f35 HEAD -- <both files>` → 0 lines. Consequence: v1's PR1 hunks anchor on byte-identical text — manual re-apply is mechanical, no conflict resolution needed.

## 1. Per-file hunk shapes (from `git show 0db1a768`)

### 1.1 `daemon/persistence.py` (+100/−32 in v1's stat; 5 hunks)

| # | v1 hunk (@@ old) | Shape | v2 anchor (identical text) |
|---|------------------|-------|---------------------------|
| H1 | `@@ -19,6` | PURE ADD — `import time` after `import os` (stdlib block, alphabetical) | `import os` / `from pathlib import Path` at module imports |
| H2 | `@@ -27,6` | PURE ADD — `from daemon.checkpoint_perf import (checkpoint_perf_logs_enabled, log_messages_api, log_saver_op, time_saver_op)` after the `daemon.checkpoint_adapter` import | `from daemon.checkpoint_adapter import CheckpointerAdapter, SqliteCheckpointerAdapter` line |
| H3a | `@@ -308,8` | ADD comment block + `t0 = time.perf_counter()` after `config = {"configurable": ...}`; **1-line REPLACE**: `state = await saver.aget(config)` → `state = await time_saver_op("aget", instance_id, saver.aget(config))` — the only content modification in the whole file; the `await saver.aget(config)` call is preserved verbatim inside the wrapper arg |
| H3b | `@@ -317,20` | ADD early-return `log_messages_api(instance_id, …, 0, 0, 0)` inside `if not messages:`; **whitespace-only re-indent** of the alist `async for` loop wrapped in `try:`/`finally:` with `alist_count += 1` as first loop-body statement + `log_saver_op("alist", …)` in the finally (W2) |
| H4 | `@@ -375,6` | PURE ADD — `bytes_estimate` (gated behind `checkpoint_perf_logs_enabled()`, S4+W3) + final `log_messages_api(instance_id, …, len(result), bytes_estimate, alist_count)` between the `result.append(serialized)` loop and the `# ── Phase 4:` comment |

### 1.2 `daemon/services/maintenance.py` (+88/−32; 1 hunk `@@ -696,39`)

Docstring extension (PR1 paragraph) + inside `_prune_per_thread_checkpoints`:
- `import time` + `from daemon.checkpoint_perf import log_prune` (function-local, per v1) + `t0`/`observed_thread_count`/`observed_total_deleted` initialized **before** the try (W7).
- `log_prune("prune", 0, 0, 0, note="no excess threads")` on the no-excess early-return path.
- `log_prune("prune-entry", threads=…, deleted=0, duration_ms=0, max_per_thread=…)` after the excess-pairs check.
- In-loop `observed_total_deleted += …` accumulation (partial counts on mid-loop failure).
- Whole existing try/except **re-indented one level** under a new outer `try:` with `finally: log_prune("prune-exit", …)`.
- **Whitespace-only deltas on every pre-existing line** (re-indent); error semantics unchanged (inner `except Exception` still swallows + logs).

### 1.3 Clean-add / verbatim files

| File | v1 source | Notes |
|------|-----------|-------|
| `daemon/checkpoint_perf.py` | `fc908945` (task-mandated byte target) | 129 lines @ `0db1a768`; `fc908945` appends `log_message_tap` (PR2) + `log_blob_prune` (PR4) — **pure appends, no new imports, no behavior coupling** (dead code until Phases 2/4). Porting the `fc908945` version satisfies the task's explicit byte-equality instruction and zero-diffs this file in later phases. |
| `tools/lint/allowlist.txt` | `0db1a768` | 498 bytes / 10 lines — comment header, **zero entries** ("empty allowlist" state). v2 has no existing file → clean add, nothing clobbered. |
| `tests/integration/gate_suites/GATE_SUITES.txt` | structure from `fc908945`, table REGENERATED on v2 | Never copied — v1's counts are stale by construction. See §3. |
| `tests/integration/gate_suites/__init__.py` | `0db1a768` (≡ `fc908945`) | empty |
| `tests/integration/gate_suites/test_gate_suite_pause_resume.py` | `0db1a768` (≡ `fc908945`) | 136 lines, bounded-enumeration dry-run test |
| `tests/integration/test_messages_response_fixture_capture.py` | **`0db1a768`** | 657 lines, 4 variants. `fc908945` drifted +273/−48 (PR3-era: synthetic-layer + empty_history variants, `_armed_manager_stub`) — see §2. |
| `tests/integration/test_no_saver_imports_in_routers.py` | `0db1a768` (≡ `fc908945`) | 281 lines, 6 tests (AST import scan + receiver-agnostic `.alist` call scan) |
| `tests/unit/persistence/__init__.py` | `0db1a768` (≡ `fc908945`) | empty |
| `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json` | **`0db1a768`** | 128 lines, 4 variants — the pre-C1 byte-shape contract. `fc908945` drifted +59 lines (6 variants). See §2. |
| `tests/unit/persistence/test_checkpoint_perf_logging.py` | **`0db1a768`** | 510 lines, 19 tests, pre-C1 contract. `fc908945` drifted ±148 (post-C1 assertions). See §2. |
| `.agents/tester/QUARANTINE.md` | **SKIPPED** — see §4 |

## 2. Drift adjudication: `0db1a768` vs `fc908945` per file

`git diff 0db1a768 fc908945 -- <file>` line counts: checkpoint_perf 95 · GATE_SUITES 198 · fixture-capture test 469 · fixture JSON 59 · perf-logging test 148 · **all others 0**.

**Decision — the 3 drifted test/fixture files port from `0db1a768` (PR1-era), NOT `fc908945`:**

1. The task names `git show 0db1a768 --stat` as **ground truth for the per-commit surface**, and its own descriptors (657 / 128 / 510 lines; 19 tests) match the `0db1a768` versions only.
2. The `fc908945` versions encode the **post-C1 (PR3) contract**: the perf-logging tests assert `saver.alist` is NEVER called and `alist_count=0`; the fixture carries 6 variants (adds `empty_history` + `synthetic_system`); the capture test re-harnesses for the synthetic layer. At Phase 1 the alist walk is INTACT by design (C1 is Phase 3) — the `fc908945` versions would fail T1.7 by construction and would falsify the fixture's documented purpose ("pre-C1 byte-shape contract for PR3").
3. The later growth of these files is exactly the later phases' port surface (PR2/PR3 diffs carry it). Porting PR1-era files keeps every phase's diff honest.

`checkpoint_perf.py` is the opposite case: the drift is pure self-contained append (gated emit helpers, no imports of PR2+ modules), the task EXPLICITLY mandates the `fc908945` byte target, and it is behaviorally dead code at Phase 1 → port `fc908945` version. GATE_SUITES drift is the per-PR regen cadence — regenerated fresh on v2, never copied.

## 3. GATE_SUITES.txt regen plan (T1.6)

- Copy v1's STRUCTURE (header block + `# File / N` comment table + non-manifest-companions note + spec/format notes + gate-concept comment scaffolding + excluded-trailer).
- Regenerate the table at working HEAD `901d96e5`: per-file `uv run pytest <file> -o addopts= --collect-only -q -p no:cacheprovider --no-header` with the DSN-pinning prefix (BOTH `POSTGRES_URL` + `POSTGRES_DB` → `ensemble_cpv2_test`).
- Rows: the 22 v2-existing files (phase0-pre-counts; 337 tests @ base) + `tests/unit/persistence/test_checkpoint_perf_logging.py` (the only PR1 file that is a manifest row, per v1's own PR1-era 23-row manifest). Fixture-capture / no-saver / gate-dry-run tests are non-manifest companions (v1's header agrees).
- Cross-check: one aggregate collect-only over all 23 paths; totals must match the per-file sum.
- dur(s) column deliberately omitted (collect-only regen; execution timings belong to the tester phase — none fabricated).

## 4. `.agents/tester/QUARANTINE.md` — SKIP decision (pre-declared per special handling)

v1's hunk appends 4 rows (3 files: `test_cold_resume_ttl.py` ×2, `test_question_deferred_pause_edge_cases.py` ×1, `test_pause_race_w7_jobitem_skip.py` ×1) after the `test_project_delete_clears_injection` row. **SKIP**, because:

1. **Interaction with an existing v2 row**: v2's ledger already carries `test_pause_race_w7_jobitem_skip ×1` inside the "M2-gate base-verified pre-existing additions (12 nodes)" family row (2026-09-03, `MagicMock queue_type` attribution) — different root-cause attribution than v1's row for the same file. Appending v1's row would create a conflicting duplicate registration.
2. **Ledger restructure**: v2's QUARANTINE.md (50 lines) is a rewritten tester-owned document; v1's rows were written against the v1-branch ledger shape and cite v1-branch evidence (clean-tree stash @ `7a94162b`, 2026-08-25) that does not transfer to v2.
3. **Evidence transferability**: whether those failures still reproduce at v2-base is unverified here; registering them would assert v1-branch evidence in a v2 ledger. That is a tester disposition (ideally re-verified against v2-base per the tester's own base-evidence protocol), not a port-mechanical act.
4. Instruction's own tiebreak: "If v2 already carries equivalent rows, or the append would interact with existing rows in any way → SKIP … When in doubt, SKIP + document."

Consequence for the ported gate dry-run test: `tests/integration/gate_suites/test_gate_suite_pause_resume.py` is ported verbatim (structure port); its eventual EXECUTION result on v2 (and any exclusion rows it needs) is tester-phase surface — flagged in phase1-results.md.

## 5. Zero-behavior-change proof plan

- `git diff -- daemon/persistence.py daemon/services/maintenance.py` must show: **0 substantive deletions**. Allowed `-` lines: (a) whitespace-only re-indentation (maintenance outer-try; persistence alist try/finally) — "whitespace excepted" clause; (b) exactly ONE content change — the mandated `aget` wrap (H3a), whose `await saver.aget(config)` call survives verbatim inside `time_saver_op(...)`.
- Evidence: `git diff --ignore-all-space` on maintenance.py must show **zero deletions** (pure additions only); on persistence.py exactly **one** deleted line (the aget wrap).
- v2's prior hunks in both files are untouched (files were byte-identical to v1-base; the v1 diff contains no other `-` context).
