# Test Report: tool registry validation fix (frozen-binary tool-name discovery)

Date: 2026-08-20
Branch: `fix/tool-registry-validation-warnings` — commit under test `4f326f8d`; packs executed at HEAD `9a8da571` (ancestor verified: `4f326f8d` ⊂ `9a8da571`)
Instance IDs: toolreg-infra 7048798b, run-frozen-pack 6b3d32c0, run-tools-suite-pack 162484ae, run-registry-pack ff97c0e0, run-boot-pack ae66a8c4, tools-suite-requarantine 537336cc

## Summary
- Total: 4 packs | Passed: 4 | Failed: 0 | Timeout: 0 (tools_suite required a quarantine pass on first run — 5 pre-existing failures, not change-related)
- Unit tests: 819 executed (6 + 676 + 140 + 2 = 824 collected, 5 deselected post-quarantine), 0 change-related failures
- ensure.md (scoped): Core 2/2 validated — Critical #1 (no regressions in changed packs) PASS, Critical #4 (dev.sh graceful-shutdown flag, static) PRESENT; #2/#3 scoped out (zero concurrency/event-loop touchpoints)
- Quick fixes applied: 1 (quarantine deselect in tools_suite pack, commit `68823b49`)
- Quarantined: +5 this session (test_archive_lifecycle.py, pre-existing) — see QUARANTINE.md

### Scope Decision
> Full suite NOT requested; change set = `daemon/tools/_tool_registry.py` (+235: KNOWN_TOOL_NAMES frozenset + frozen-detection fallback) + 1 new test file. `daemon/registry.py` untouched. No job/task/queue/DB touchpoints → e2e convention (job/task/queue changes) NOT triggered; no postgres packs; no concurrency packs; real PyInstaller frozen build skipped per task note ("don't over-invest" — simulated-frozen coverage is the 4-test suite's explicit design). Smallest scope that covers the change: 4 packs (1 new ad-hoc per new test file + regression sweep + boot-path validation).

## Fix Mechanism (verified by infra worker)
- `KNOWN_TOOL_NAMES`: 166-entry frozenset of all `@tool`-decorated function names, adjacent to `CATEGORY_MODULES`, regenerable via documented one-liner.
- Frozen detection: `any_source_read` flag — True only when a category-module `.py` is actually read from disk. PyInstaller ships `daemon/` as bytecode → every `file_path.exists()` is False → flag stays False.
- Fallback seam: zero readable sources → return `set(KNOWN_TOOL_NAMES)` verbatim; partial sources → `tool_names |= KNOWN_TOOL_NAMES` merge (source canonical).
- Consumption seam: `daemon/registry.py::validate_tool_configs` (982-1124) builds the known universe from `list_tools_by_category()` + `DYNAMIC_TOOL_NAMES` + `discover_all_tool_names()` + `CATEGORY_MODULES.keys()`; `get_registry()` (1142-1161) logs each warning via `logger.warning("Tool config validation: {w}")` on `daemon.registry` — pre-fix frozen boot: discovery → ∅ → 30-32 false positives for project-manager.

## Test-Item Results

| # | Requested item | Verdict | Evidence |
|---|---|---|---|
| 1 | New test file (4 tests) passes | ✅ PASS | frozen_tool_name_discovery pack: 6/6 in 1.05s (4 from 4f326f8d: superset-of-30-flagged-names, no-source fallback == static set, partial-source merge, e2e frozen-sim zero PM warnings; +2 drift-guard tests from concurrent follow-up 8ef609ee: KNOWN_TOOL_NAMES ↔ source exact-match, source-only-raises-in-frozen) |
| 2 | Regression sweep (tools + registry) | ✅ PASS | tools_suite: 671P/5-deselected/0F in 13.07s (first run 5/676 FAIL, all pre-existing → quarantined + re-run PASS); registry_validation: 140/140 in 6.07s |
| 3 | Source-mode boot → zero warnings | ✅ PASS | tool_config_validation_boot pack: real `AgentRegistry(agents/).discover().validate_tool_configs()` + `get_registry()` boot wrapper with caplog@WARNING on `daemon.registry` — **0** occurrences of "is neither a known category nor a known tool" for project-manager; grep of `--log-cli-level=WARNING -s` output: 0 symptom records |
| 4 | Frozen-mode simulation | ✅ PASS | Covered by item 1 (frozen sim = `_tool_registry.__file__` → empty tmp dir; discovery == KNOWN_TOOL_NAMES; e2e frozen-mode validate_tool_configs → zero PM warnings). Real frozen build: SKIPPED (not cheap; task explicitly de-prioritized) |
| 5 | No false-negative regression | ✅ PASS | bogus allow entry `totally_bogus_tool_xyz` in tmp_path-staged PM config → exactly **1** WARNING record: `daemon.registry:registry.py:1160 Tool config validation: Agent 'pm-bogus-probe': allow entry 'totally_bogus_tool_xyz' is neither a known category nor a known tool` — validator still catches genuinely-unknown names |

**Original symptom (30-32 boot warnings for project-manager): RESOLVED** — zero-warning pinned in source mode (item 3) and in frozen-mode simulation (item 1, test 4); fallback proven to carry all 30 incident names (item 1, test 1).

## Pack Results

| Pack | Result | Tests | Runtime | Worker |
|---|---|---|---|---|
| frozen_tool_name_discovery_unit_test | ✅ PASS | 6/6 | 1.05s | 6b3d32c0 |
| tools_suite_unit_test | ✅ PASS (post-quarantine) | 671P/5-deselected | 13.07s | 162484ae → 537336cc |
| registry_validation_unit_test | ✅ PASS | 140/140 | 6.07s | ff97c0e0 |
| tool_config_validation_boot_unit_test | ✅ PASS | 2/2 | 2s | ae66a8c4 |

## ensure.md (Core, scoped)
- ✅ Critical #1 — no regressions in changed packs: all 4 packs PASS (pre-existing failures quarantined per policy)
- ✅ Critical #4 — `dev.sh` includes `--timeout-graceful-shutdown 10`: PRESENT (dev.sh:102)
- Scoped out: Critical #2/#3 (concurrency_atomic_unit_test) — zero deadlock/concurrency/event-loop touchpoints in the change set; Important #1/#2, Release Gate — no converted-async callers touched, not a big/critical change
- Improvement notices: none (no contradictions found)

## Pre-Existing Failures Quarantined (5)
All in `tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive` — `daemon/tools/access_memory.py` returns 'Access denied' instead of expected archive content. Triple-attributed pre-branch:
1. Symbol grep on failing file: zero refs to KNOWN_TOOL_NAMES / discover_all_tool_names / validate_tool_configs / _tool_registry
2. File unmodified on branch (last-touch `1da0d84f`, predates branch)
3. Ancestor re-run at 4f326f8d state → identical 5 failures (same lines, same 'Access denied' text)

**Routing recommendation (NOT fixed — out of scope):** 🟠 `daemon/tools/access_memory.py` archive access path appears broken (all 5 tests expect content/not-found, get 'Access denied'). Likely an auth/permission guard regression in access_memory tool. Needs a separate fix task — the 5 quarantined tests are the acceptance suite.

## Quick Fixes Applied
- Worker 537336cc: quarantine-aware deselect in `test/packs/tools_suite_unit_test.sh` (5 × `--deselect .../TestAccessMemoryArchive::*`)
  - Root cause of FAIL: pre-existing failures (not the change)
  - Fix: deselect flags + comment placed BEFORE the `timeout 110s .venv/bin/pytest \` line (⚠️ bash gotcha: a `#` comment line inside a `\`-continuation chain terminates the chain and silently drops subsequent flags — first placement dropped the deselects)
  - Verification: re-run PASS 671/5-deselected/0F, 13.07s
  - Commit: `68823b49` (pack script + QUARANTINE.md)

## Unexpected Findings
1. 🟡 **Concurrent commit `8ef609ee`** landed mid-test on the same branch (drift-detection follow-up to `_tool_registry.py` + 2 tests). Packs ran at HEAD 9a8da571 which includes it; commit-under-test 4f326f8d verified as ancestor. The +2 tests (exact-match drift guard, source-only-raises-in-frozen) also pass — strengthens the fix.
2. 🟡 **archive-lifecycle pre-existing failures** (routed above).
3. 🟢 Benign host noise: `pytest-timeout` declared in pyproject but not installed in `.venv` → 2 `PytestConfigWarning: Unknown config option: timeout` lines on every pytest run (pre-existing, all packs).

## Documentation Updated
- [x] PACKS.md — 4 pack rows: registered (9a8da571) → final results stamped
- [x] QUARANTINE.md — 5 new Active rows (attribution + ancestor evidence)
- [x] LESSONS/2026-08-20-bash-comment-continuation-chain.md — (see below, included in docs commit)
- [x] RESULTS/2026-08-20-tool-registry-validation-fix.md — this report
- [ ] rules/ensure.md — no changes (user-maintained)

## Code Changes Summary (this session)
- `test/packs/{frozen_tool_name_discovery,tools_suite,registry_validation,tool_config_validation_boot}_unit_test.sh` + `tests/unit/tools/test_tool_config_validation_boot.py` + PACKS.md rows — commit `9a8da571`
- `test/packs/tools_suite_unit_test.sh` quarantine deselect + `.agents/tester/QUARANTINE.md` — commit `68823b49`
- PACKS.md result stamps + RESULTS/ + LESSONS — docs commit (pending, dispatched after this write)
- No production code modified by testing.

### Overall Status
- Unit Tests: ✅ PASS (0 change-related failures across 824 collected)
- Mock/E2E: N/A (scoped out — no job/task/queue/DB touchpoints; simulated frozen coverage per design)
- ensure.md (scoped): ✅ PASS (2/2 in-scope Core items)
- **Testing Complete: ✅ READY — verdict on 4f326f8d: SHIP**
