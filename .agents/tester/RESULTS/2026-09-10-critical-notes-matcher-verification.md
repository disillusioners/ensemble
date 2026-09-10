# Verification Report — critical-notes matcher fix (pre-merge)

**Date**: 2026-09-10
**Branch / SHA**: `feature/fix-critical-notes-matcher` @ `53b63630` (3 commits over base `d84952dc`)
**Review-approved**: yes (2 rounds, all findings closed, shim-verified test honesty)
**Worktree**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble` (1 untracked file `docs/llm-stream-stall-hardening.md`, pre-existing, no tracked-file writes)
**Gate posture**: verify-only (0 repo writes, 0 commits, 0 pushes); 4 parallel workers, dual-layer timeouts, drift-pinned per pack

---

## Overall verdict: ✅ PASS FOR MERGE

PostgreSQL gap closed, original symptom closed on SQLite + PG, 0 branch-caused failures, 0 quarantines introduced. Adjacent sweep surfaced 2 pre-existing failures in `tests/unit/test_project_manager_agent.py` (project-manager prompt-convention drift, predates branch — see "Findings not blocking merge" §3). Isolation mechanism is sound; one unrelated sibling defect in `test/packs/wc_wake_off_bytecompat_probe_test.py` surfaced as follow-up (§4).

---

## Scope decision

Full test suite NOT requested. Blast radius = single tool (`daemon/tools/critical_notes.py`) + tests + docs, no architecture impact, no API endpoint schema change consumed by FE. Critical-notes tool's return-shape changes (loud REJECT, eviction-candidates error) are agent-tool-call surfaces only — no FE wiring touched. Scope reduced to: unit baseline + PG pin + default-run isolation + adjacent critical-notes-referencing sweep. Release-gate items in ensure.md scoped out (big/critical change gate — not warranted here).

---

## Pack totals

| Pack | Worker | Result | Counts | Runtime | Verdict |
|---|---|---|---|---|---|
| Unit baseline (3-file, expect 74P/0F/0S) | e531bf4c | PASS | **74 passed**, 0F, 0S, 0D (41+23+10 exact) | 1.55s | ✅ |
| PG pin — new file (`tests/postgres/test_critical_notes_repository_pg.py`) | ee20653c | PASS | **2 passed** | 0.33s | ✅ |
| PG pin — classification (`tests/postgres/test_smoke.py`) | ee20653c | PASS | **7 passed** | 0.59s | ✅ |
| Default-run isolation (`--collect-only` + dev.sh static) | bf153ad3 | PASS (mechanism) | 272 nodes deselected; dev.sh:99,102 | ~1s | ✅ |
| Adjacent sweep (critical_notes grep, 13 files) | fa0f8948 | PASS (with 2 pre-existing) | 387P / **2F** / 1S / 0D | 3.88s | ✅ (pre-existing) |

**Cumulative**: unit **74/74** · PG **9/9** · adjacent **387/389** (2 failures adjudicated pre-existing, not branch-caused).

---

## 1. Unit baseline re-run (`tests/unit/tools/test_critical_notes.py` + schema + api)

- **Drift pin**: `feature/fix-critical-notes-matcher` @ `53b63630` ✓
- **Command**: `timeout 120 uv run python -m pytest tests/unit/tools/test_critical_notes.py tests/unit/test_critical_notes_schema.py tests/unit/test_critical_notes_api.py -q --tb=short`
- **Verbatim summary**: `74 passed in 1.55s` (exit 0)
- **Per-file counts (via `--collect-only -q` per file)**:
  - `tests/unit/tools/test_critical_notes.py`: **41** ✓
  - `tests/unit/test_critical_notes_schema.py`: **23** ✓
  - `tests/unit/test_critical_notes_api.py`: **10** ✓
  - **Total: 74** ✓ — matches dev's pre-merge numbers (41+23+10) exactly
- 0 failures, 0 skips, 0 deselected. Matches expectation.

## 2. PG pin — THE MERGE GATE

### 2a. New PG file (`tests/postgres/test_critical_notes_repository_pg.py`) — **VERIFIED**

- **Drift pin**: ✓ @ `53b63630`
- **Setup**: Option A — fresh disposable PG cluster via `/opt/homebrew/opt/postgresql@14/bin/initdb -A trust -U $(whoami)` on `/tmp/critnotes-pgdata-23148`, port 15432, database `ensemble_test_critnotes`. OS user = cluster superuser → zero CREATE-on-public risk class.
- **Env**: wholesale scrub of `POSTGRES_*` + `ENSEMBLE_TEST_PG_URL` + `PG_TEST_*`; connection URL `postgresql+psycopg://nguyenminhkha:@localhost:15432/ensemble_test_critnotes` — `ensemble_prod` **never** appeared in any URL.
- **Conftest env var**: `tests/postgres/conftest.py` consumes **`PG_TEST_*`** (not `ENSEMBLE_TEST_PG_URL` as the task note suggested — minor doc drift).
- **Run**: `timeout 300 env PG_TEST_*=… uv run python -m pytest tests/postgres/test_critical_notes_repository_pg.py -q --tb=short`
- **Verbatim summary**: `2 passed in 0.33s` (exit 0)
- **Tests verified**:
  - `TestUpdateCriticalNoteReferenceNone::test_reference_none_preserves_stored_reference` — PASS
  - `TestUpdateCriticalNoteReferenceNone::test_reference_explicit_value_replaces_stored_reference` — PASS
- **Layer pinned**: the `SQLModelProjectRepository.update_critical_note` None-skip guard is now verified at the real-PG layer. Original-symptom coverage (silent reference overwrite) closed out on PG.

### 2b. Dev's smoke classification — **ENVIRONMENTAL** (not a defect)

- Same setup, same env.
- **Run**: `timeout 300 env PG_TEST_*=… uv run python -m pytest tests/postgres/test_smoke.py -q --tb=short`
- **Verbatim summary**: `7 passed in 0.59s` (exit 0)
- **Verdict**: dev's `InvalidSchemaName: role lacks CREATE on schema public` is a **pre-existing local cluster permission state**, NOT a defect in `test_smoke.py` / conftest / the new file. Identical fresh-disposable-cluster setup runs smoke green. Classified environmental; no action.

### 2c. Isolation hazard check

- Conftest uses autouse `TRUNCATE … RESTART IDENTITY CASCADE` per-test (not connection-scoped `SET search_path`). LESSONS §1 dangerous pattern **absent** in tests/postgres/.
- 2 new tests use distinct project names (`cn_reference_none_pg_test` / `cn_reference_replace_pg_test`) — no cross-test UniqueViolation observed.

### 2d. Teardown (mandatory, verified)

- `pg_ctl -D /tmp/critnotes-pgdata-23148 stop -m fast` → "server stopped"
- `pg_ctl status` → "no server running"
- `rm -rf /tmp/critnotes-pgdata-23148` → cleaned
- `lsof -i :15432` → empty (port freed)
- `ls /tmp/critnotes-pgdata-*` → "No such file or directory"

## 3. Default-run isolation + static check

### 3a. Mechanism (CONIRMED)

- **Config rule (pinned)**: `pyproject.toml:67 addopts = "-m 'not integration and not postgres'"` + marker registration at `pyproject.toml:64`.
- **Coverage of both files**:
  - `tests/postgres/test_critical_notes_repository_pg.py` → module-level `pytestmark = pytest.mark.postgres` (file line 30)
  - `tests/postgres/test_smoke.py` → auto-applied via `tests/postgres/conftest.py:82-95` `pytest_collection_modifyitems` (path-prefix match `tests/postgres/`)
- **Deselect count**: 272 nodes from `tests/postgres/` are excluded from default `pytest` runs.
- **Collectability proof**: `uv run python -m pytest tests/postgres --collect-only -q -o addopts=""` → `272 tests collected in 0.24s` (no broken-import errors). The `-m`-addopts filter applies even with an explicit path; -o override restores it.

### 3b. Part-1 anomaly — unrelated sibling defect

- Default `pytest --collect-only -q` produced `59 tests collected, 1 error in 0.44s` (not the expected `2 deselected` summary).
- **Root cause**: `test/packs/wc_wake_off_bytecompat_probe_test.py:79` calls `sys.exit(1)` when `DAEMON_BYTECOMPAT_ROOT` env var is unset. The probe is auto-collected from `test/packs/` but is meant to run ONLY via its sibling `wc_wake_off_bytecompat_probe_test.sh` wrapper (which exports the env var). Bare pytest aborts before the summary prints.
- **NOT caused by this branch** — `test/packs/wc_wake_*` is not in the branch file list (branch = `daemon/tools/critical_notes.py`, `tests/unit/tools/test_critical_notes.py`, `tests/postgres/test_critical_notes_repository_pg.py`, `docs/usage.md`). Defect lives on `latest` independently.
- **NOT a merge blocker for critical-notes**, but a real pre-existing ergonomic defect for any bare `pytest` invocation in the repo.

### 3c. ensure.md Core static — PASS

- `grep -n "timeout-graceful-shutdown 10" dev.sh` → 2 hits:
  - `dev.sh:99` — comment
  - `dev.sh:102` — actual `--timeout-graceful-shutdown 10` flag in the uvicorn command

### 3d. ensure.md Core 2/3 (concurrency_atomic pack) — scoped out

- `concurrency_atomic_unit_test` (deadlock / sync-DB-on-event-loop integrity) is scoped OUT for this gate. Reason: change touches a single tool file (`daemon/tools/critical_notes.py`); no DB-helper / event-loop / concurrency code changed; the tool's repo functions go through the existing repository layer (verified by the new PG test in pack 2). No blast-radius justification to run the concurrency pack for this gate.

## 4. Adjacent sweep (13 critical_notes-referencing files, excl. baseline + postgres)

### 4a. Pack composition

Deterministic selection: `grep -rl "critical_notes" tests/unit tests/integration --include="*.py"` minus the 3 baseline files and `tests/postgres/`. Result: **13 files, 390 tests collected**, well under 5-min cap.

```
tests/integration/test_context_freshness.py
tests/integration/test_context_hierarchy.py
tests/integration/test_context_in_graph.py
tests/integration/test_context_injection_integration.py
tests/integration/test_explorer_shared_context_paths.py
tests/unit/services/test_context_injection.py
tests/unit/services/test_kv_ambient_fresh_c3.py
tests/unit/test_blueprint_injection.py
tests/unit/test_context_messages.py
tests/unit/test_governor_recursion_guard.py
tests/unit/test_project_manager_agent.py
tests/unit/tools/test_spawn_councilor_default_version.py
tests/unit/tools/test_version_tag_tool_resolution.py
```

### 4b. Result

- **Verbatim summary**: `2 failed, 387 passed, 1 skipped, 5 warnings in 3.88s` (exit 1)
- **Failures**:

| Test | File:line | Asserted | Found |
|---|---|---|---|
| `TestPromptComposition::test_soul_cites_rule_for_severity_framing` | `tests/unit/test_project_manager_agent.py:1004` | `'rule.md' in soul.md body` | 0 occurrences |
| `TestPromptComposition::test_workflow_cites_soul_and_rule` | `tests/unit/test_project_manager_agent.py:1011` | `'soul.md' in workflow.md body` | 0 occurrences |

### 4c. Adjudication — **PRE-EXISTING, NOT branch-caused**

- `test_project_manager_agent.py` mentions `critical_notes.py` only in a module docstring (factory-chain listing) and one inline comment (`:116`). The tests assert pure cross-references between `agents/project-manager/soul.md` and `rule.md`/`workflow.md`; they do NOT touch critical-notes semantics.
- Branch diff vs `origin/latest` is EMPTY for `agents/project-manager/`. Branch file list is `daemon/tools/critical_notes.py` + 3 test/doc files only.
- Last touches to `agents/project-manager/soul.md` / `workflow.md` are commits `03eb1398`, `656989a8`, `f970e7ac`, `fabf08be`, `71822f4f` (the prompt reword/restoration arc) — none on this branch.
- QUARANTINE.md does NOT list these tests as known flakes (not quarantinable-flaky; they're deterministic stale asserts on prompt files).
- **Verdict**: pre-existing prompt-convention drift in `project-manager` agent, lives on `latest`, days old. Not attributable to this merge. Surfaced as a separate follow-up item (§5.3).

---

## 5. Findings

### 5.1 Pre-existing failures (NOT branch-caused, not blocking)

- **2 × `TestPromptComposition`** in `tests/unit/test_project_manager_agent.py` (asserting `rule.md`/`soul.md` cross-references in `agents/project-manager/`). Branch doesn't touch that directory; the static asserts are deterministic against prompt files last edited by an unrelated arc. **Owner**: project-manager-prompt-convention follow-up.

### 5.2 Pre-existing ergonomic defect (NOT branch-caused, surfacing only)

- **`test/packs/wc_wake_off_bytecompat_probe_test.py:77-79`** — unconditional `sys.exit(1)` when `DAEMON_BYTECOMPAT_ROOT` unset. Auto-collected by bare `pytest` from `test/packs/` (default discovery); the env var is only set by the sibling `.sh` runner. Effect: any bare `pytest` invocation (including `--collect-only`) aborts with `INTERNALERROR> SystemExit: 1` before the summary line prints. Doesn't affect this branch, doesn't affect any pack execution under `uv run python -m pytest ... <paths>`. **Owner**: WC-wake-runner / probe ergonomics follow-up (separate ticket).

### 5.3 Knowledge nuggets

- `tests/postgres/conftest.py` consumes **`PG_TEST_*`** env vars, NOT `ENSEMBLE_TEST_PG_URL` (the gate's task note and the bootstrap convention's `ENSEMBLE_TEST_PG_URL` claim refer to a different path — daemon boot, not test conftest).
- LESSONS §1 (connection-scoped `SET search_path` masking) is **NOT** a hazard in `tests/postgres/` — the conftest uses `TRUNCATE … RESTART IDENTITY CASCADE` autouse, no libpq connect-options needed.

### 5.4 ensure.md status (scoped to this branch)

| Requirement | Status | Evidence |
|---|---|---|
| Core: No regressions in changed packs | ✅ | Unit baseline 74/74, PG 9/9, adjacent 387/389 (2 pre-existing, adjudicated) |
| Core: Deadlock / concurrency integrity (`concurrency_atomic_unit_test`) | SCOPED OUT | Single-tool change; no DB-helper/event-loop/concurrency code touched (§3d) |
| Core: No sync DB calls on the asyncio event loop | SCOPED OUT | Same scope as above; covered by `concurrency_atomic_unit_test` |
| Core: `dev.sh` includes `--timeout-graceful-shutdown 10` | ✅ | `dev.sh:99` (comment) + `dev.sh:102` (flag) |
| Release Gate | NOT RUN | Blast radius does not warrant it (single tool, no architecture) |

---

## 6. Verdict

**`feature/fix-critical-notes-matcher` @ `53b63630` is MERGE-READY.**

- Unit: **74/74** PASS (matches dev exactly, 41+23+10)
- PG (the merge gate): **9/9** PASS on disposable PG14 cluster — new file verified, dev's smoke failure classified environmental
- Adjacent: **387/389** with 2 pre-existing failures adjudicated non-branch-caused and not gating
- Isolation: mechanism CONFIRMED (`pyproject.toml:67` `addopts = "-m 'not integration and not postgres'"`, 272 nodes deselected, both files covered)
- Two pre-existing follow-ups surfaced (project-manager prompt convention + WC-wake probe ergonomics) — neither attributable to this branch

**Zero new quarantines introduced.** No repo writes; no commits; no prod contact.

**Follow-ups (separate tickets, not for this gate)**:
1. `tests/unit/test_project_manager_agent.py::TestPromptComposition` — `agents/project-manager/soul.md` must cross-reference `rule.md`; `workflow.md` must cross-reference `soul.md`. Static convention gate; pre-existing on `latest`.
2. `test/packs/wc_wake_off_bytecompat_probe_test.py:77-79` — replace `sys.exit(1)` with `pytest.skip(...)` when `DAEMON_BYTECOMPAT_ROOT` unset, so bare `pytest` invocations don't abort the collection phase.
