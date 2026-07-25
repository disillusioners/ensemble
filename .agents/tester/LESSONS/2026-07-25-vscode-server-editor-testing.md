# LESSONS: VS Code Server Editor Integration Testing (2026-07-25)

## L1: pytest-timeout plugin missing — config keys warn but don't break tests

**Found:** 2026-07-25 during VS Code Server Editor testing
**Severity:** Low (hygiene)
**Status:** Not fixed (non-blocking)

### Problem
`pyproject.toml` contains `timeout = 30` and `timeout_method = "thread"` config keys (lines referencing the `pytest-timeout` plugin), but the plugin is NOT installed in the project venv. All 5 backend workers reported `PytestConfigWarning: Unknown config option: timeout` / `timeout_method`.

### Impact
- The script-internal timeout layer (Layer 2 of the dual-layer timeout) is INACTIVE for Python test packs.
- Only the command-level `timeout` wrapper (Layer 1) enforces the time cap.
- Tests still run correctly — the command wrapper catches hangs. But if a worker ran a pack WITHOUT the `timeout` wrapper (violating the contract), there would be no inner safety net.

### Root Cause
The plugin was likely installed at some point and removed, or the config was added aspirationally.

### Recommendation
- **Option A**: `pip install pytest-timeout` in the project venv to activate the config.
- **Option B**: Remove the config keys from `pyproject.toml` if the plugin isn't wanted.
- **Test pack mitigation**: Always use the command-level `timeout 300` wrapper (already enforced by test-pack skill).

---

## L2: Frontend test runner is Jest, not Karma

**Found:** 2026-07-25 during VS Code Server Editor testing
**Severity:** Informational

### Observation
The VS Code feature's frontend specs use **Jest + jest-preset-angular** (Angular 21), NOT Karma. The project `package.json` has `"test": "jest"` and no karma config/builder.

### Impact
- `npx ng test` (Karma) does NOT work for this project.
- Correct command: `npx jest <spec-files>` (or `npm test`).
- Jest 30 renamed `--testPathPattern` → `--testPathPatterns`; use positional file args for cleaner scoping.

### Lesson for future test packs
Always inspect `package.json` test script + dependencies BEFORE assuming the test runner. The test-pack-execution skill's Pre-Execution Self-Check should verify the actual runner.

---

## L3: SQLite :memory: + asyncio.to_thread writes vanish

**Found:** 2026-07-25 by worker vscode-new-lifecycle
**Severity:** Informational (test infrastructure)

### Problem
When using SQLite `:memory:` with `asyncio.to_thread()` for DB writes, the writes done in a different thread/connection vanish because `:memory:` SQLite creates a per-connection database.

### Fix Applied
The lifecycle integration worker switched to a file-backed SQLite DB (`tmp_path`) so writes round-trip across threads. This is a test-infrastructure fix, not a production change.

### Lesson
For integration tests that persist via `asyncio.to_thread`, use file-backed SQLite (`sqlite:///<tmp_path>/test.db`) or `StaticPool` with a single shared connection — never plain `:memory:`.

---

## L4: Settings component spec tests a copy, not the real component

**Found:** 2026-07-25 by worker vscode-new-frontend-mock
**Severity:** Medium (coverage gap, not a bug)
**Status:** Not fixed — recommendation logged

### Problem
`settings.component.spec.ts` (64 tests) uses a hand-rolled `TestableSettingsComponent` *copy* instead of the real `SettingsComponent`. The real component has `editorDirty = computed(() => this.selectedEditor() !== this.savedEditor())`; the copy uses `editorDirty = signal(false)` with manual `.set()` calls.

### Impact
A refactor that broke the real computed/Apply flow would be invisible to the 64 existing tests. The real component code IS correct (verified by the new integration spec), but the test coverage doesn't protect it.

### Recommendation
Migrate `settings.component.spec.ts` to test the real `SettingsComponent` via TestBed in a follow-up.

---

## L5: VS Code feature — all security boundaries hold

**Found:** 2026-07-25
**Severity:** Positive finding

### Summary
The VS Code Server Editor integration has comprehensive security hardening that was verified end-to-end:
- **C1** (path traversal): All attack vectors rejected with 403 before reaching code-server.
- **C4** (port/pid leak): Absent from all status endpoints (verified via raw response text scan).
- **W13** (transactionality): Preference NOT persisted when server fails to start (verified via spy).
- **C3** (mount isolation): `/vscode/*` reaches proxy, not SPA catch-all.

No production bugs were found. The feature is ready to merge.
