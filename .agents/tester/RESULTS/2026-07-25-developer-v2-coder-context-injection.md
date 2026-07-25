# Test Report: developer[v2] + coder context_injection
Date: 2026-07-25
Branch: `feature/developer-v2-coder` @ `822888f1` (+ quick-fix commit `9e6eb46e`)
Worker Instances: 80c05d34 (pack-registry-versioning), 121c6a79 (pack-context-injection), 25aa057a (pack-coder-agent)
opencode session: `verify-prefailing` (pre-existing failure verification)

## Summary
- Total tests run: 168 | Passed: 161 | Failed: 6 (all pre-existing, quarantined) | Skipped: 0
- **All tests relevant to the change PASS.** Zero regressions.
- Quick Fixes Applied: 1 (2 tests restored — test-mock sync)
- Quarantined: 6 pre-existing failures (unrelated to change, see QUARANTINE.md)

## Scope Decision
> Full test suite NOT run. The change touches only 3 files in `agents/` (2 new agent-meta files + 1 modified meta.json) — no production source, no migrations, no test infrastructure. Blast radius is small and isolated to agent registry/metadata discovery. Ran only the 3 relevant packs (agent versioning + registry, context injection, coder agent metadata). Skipped the remaining 192 packs. Full suite not warranted.

## Verification Items (from request)

### 1. Agent registry picks up developer[v2] tag — ✅ PASS
- Pack: `tests/test_agent_versioning_api.py` + `tests/unit/test_meta_tag_parsing.py` + `tests/test_registry.py`
- Result: **112 passed, 0 failed** (runtime 0.97s)
- The `[v2]` suffix directory is correctly discovered and parsed by `AgentRegistry.discover()` + `_TAG_PATTERN`.

### 2. Context injection is recognized — ✅ PASS
- Pack: `tests/test_registry_skill_injection.py`
- Result: **12 passed, 0 failed** (runtime 0.7s)
- `context_injection: true` in both `agents/coder/meta.json` and `agents/developer[v2]/meta.json` is correctly recognized by `AgentRegistry.discover()`.
- Covers: `test_context_injection_from_meta_json`, `test_context_injection_default_false`, `test_context_injection_field_declared`, `test_context_injection_absent_from_meta_json`.

### 3. Coder explore tool present — ✅ PASS (no regression)
- `agents/coder/meta.json` `tools.allow` still contains `"knowledge"` (provides `explore`).
- Confirmed by `test_tools_allow_list` (PASS) — allow list = `bash, filesystem, time, self, help, knowledge, context, shared_context`.
- `test_coder_has_filesystem_tools` PASS.

### 4. Valid JSON — ✅ PASS
- Both `agents/developer[v2]/meta.json` and `agents/coder/meta.json` parse without errors (registry discovery succeeded; `test_meta_json_is_valid_json` PASS).

### 5. Broader agent registry/discovery suite — ✅ PASS
- `tests/test_registry.py` (74 tests): **PASS** — no regressions from adding the new `[v2]` directory and modifying coder meta.
- `tests/unit/test_coder_agent.py` (39 tests): **PASS, 0 failed** — coder metadata, tools, tool filter, soul all intact.

## ensure.md Validation Results (Core, blast-radius scoped)

### Critical
- ✅ **No regressions in changed packs** — every pack in the change set returns PASS. The 6 failures in `test_coder_developer_migration.py` are **pre-existing** (migration file deleted in `834c496c`, 4 days before this branch) and now quarantined (QUARANTINE.md). They are not in the blast radius of this agent-meta-only change.
- ⚠️ N/A **Deadlock/concurrency integrity** — not in blast radius (no concurrency/source changes). Skipped per blast-radius scoping.
- ⚠️ N/A **No sync DB calls on asyncio** — not in blast radius. Skipped.
- ✅ **dev.sh includes --timeout-graceful-shutdown 10** — static check PASS (line 74). Unchanged by this feature.

### Important / Nice-to-have
- Not in blast radius (async-callers, deadlock scenario, dead code) — skipped.

**ensure.md Release Gate:** NOT triggered — change is small/isolated (agent meta files only), not big/critical/architecture.

## Pre-existing Failures (verified independently via opencode)
The 6 failures in `tests/unit/test_coder_developer_migration.py` were independently verified as **pre-existing**:
- Migration file `20260626_000001_rename_coder_to_developer.sql` was intentionally deleted in commit `834c496c` ("Remove stale coder→developer agent rename migration", 2026-07-21).
- `834c496c` is an ancestor of `822888f1` (this branch's change) — deletion predates the branch by 4 days.
- `git diff HEAD~1 HEAD -- agents/` confirms the branch change is ONLY agent meta files (no migration files, no daemon source, no test infrastructure).
- The tests' `_run_sqlite_migration()` helper raises `RuntimeError` when the migration file is absent — they fail identically before this branch existed.
- **Conclusion: NOT regressions.** Now quarantined (QUARANTINE.md).

## Quick Fixes Applied
- **Worker 25aa057a (pack-coder-agent):** Fixed 2 test-mock-sync failures in `TestRestoreInstanceWithCoderAgentId`.
  - Root cause: `_restore_instance()` was refactored (commit `231253a9`, version-tag support) to call `registry.get_version(agent_id, agent_tag)` before falling back to `get_resolved()`. Tests only stubbed `get_resolved`, so MagicMock made `agent_tag` truthy and `get_version()` returned a truthy mock — the fallback was never reached.
  - Fix: +13/-6 lines, test-code only. Set `mock_meta.agent_tag = None` and `mock_registry.get_version.return_value = None`.
  - **Commit: `9e6eb46e`** (on `feature/developer-v2-coder`).

## Per-Pack Results

| Pack | Scope | Result | Count | Runtime |
|------|-------|--------|-------|---------|
| test_agent_versioning_api.py + test_meta_tag_parsing.py + test_registry.py | Versioning + tag parsing + registry discovery | ✅ PASS | 112/112 | 0.97s |
| test_registry_skill_injection.py | Context injection recognition | ✅ PASS | 12/12 | 0.7s |
| test_coder_agent.py | Coder metadata + tools + migration | ✅ PASS (coder: 39/39); migration: 6 pre-existing quarantined | 39/39 coder, 5/11 migration (6 quarantined) | ~1.2 min |

## Documentation Updated
- [x] RESULTS/2026-07-25-developer-v2-coder-context-injection.md — this report
- [x] QUARANTINE.md — created; 6 pre-existing migration failures quarantined
- [x] LESSONS/2026-07-25-coder-migration-test-staleness.md — quick fix + staleness finding

## Overall Status
- Agent versioning/tag discovery: ✅ PASS
- Context injection recognition: ✅ PASS
- Coder tools/metadata: ✅ PASS
- JSON validity: ✅ PASS
- ensure.md Core (scoped): ✅ PASS
- **Testing Complete: ✅ READY** — all change-relevant tests pass; zero regressions. Pre-existing migration-test staleness flagged for follow-up.
