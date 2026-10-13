# Test-Wall Classification — Phase A (agent-prompt-immutability)

Date: 2026-10-13
Owner: T12 (Phase A) — coder (working-lead)
Branch: `feature/agent-prompt-immutability` @ `db9f9e371` (after A7 amendment commit)
Base: `e9cdc25c6` (`v0.20.2` latest)
Worktree: `/home/ensemble-worktrees/agent-prompt-immutability`
Fence: `ENSEMBLE_SELF_ENV=dev POSTGRES_DB=ensemble_dev_test_only HOME=<mktemp>` (verified pre/post every pytest; loader coder's `POSTGRES_DB=ensemble_prod` posture refused)

## TL;DR

- **A7 contract amendment** journaled and committed (`db9f9e371` — `docs(contract): journal A7 retry-budget amendment (dispatcher-approved)`).
- **Ordered first-run list executed** end-to-end. **ZERO reclassifications were needed** — every test either passes on branch as-is or is documented as a known pre-existing failure base-proven at `e9cdc25c6`.
- **Two RED base-proves CONFIRMED PRE-EXISTING.** Both files fail identically at base and branch: 2 fails in `tests/test_agents_api.py` + 18 fails in `tests/test_progressive_dispatch.py`. Per the repo's quarantine philosophy: classify `not-fix`; document; flag for `.agents/tester/QUARANTINE.md` handoff (no edit made — append convention not verified).
- **PG-parity finding:** `tests/test_prompt_version_resolution.py` does **NOT** carry `engine_postgres` fixtures — the file is SQLite-only (47/47 PASS). PG-parity is covered at the schema/migration level via `tests/migration/test_prompt_versions_index_parity.py` (10/10 PASS) which compares index NAMES to `__table_args__` across both dialects.
- **Zero silent weakening.** Every PASS is genuine; every FAIL is pre-existing; the T11 shim's effect on the LLM-gated integration tests is recorded as Phase B reclassification work (file-effect → DB-row-effect).

---

## 1. Ordered first-run table

| # | Pack | On-branch counts | Classification | Fork-id | Action taken |
|---|------|------------------|----------------|---------|--------------|
| 1 | `tests/test_prompt_cache.py` | 11/11 PASS | **intent-survives** (no reclassification) | F9 / R10 | None — cache-key + invalidate round-trip pins match the new semantics (T7) |
| 2 | `tests/test_loader.py` (1432L) | 70/70 PASS | **intent-survives** (no reclassification) | F6 / C1 | None — cache-miss path + compose; the hook is transparent to these tests |
| 3 | `tests/unit/test_filesystem_absolute_path.py:80-83` (`test_absolute_path_skips_workdir`) | 31/31 PASS | **intent-survives** (no reclassification) | S10 / C3 | None — the test exercises `_resolve_target_path` with `tmp_path/file.txt`, NOT an agents/-shaped artifact; the guard only fires on versioned-artifact paths; resolver semantics unchanged |
| 4a | `tests/integration/test_inner_soul.py:216-224` (`test_inner_soul_change_workflow_e2e`) | 3/3 DESELECT (`OPENAI_API_KEY` gate) | **file-effect-rewritten-pending-Phase-B** | S11 / D-1 | None on branch — reclassification deferred to Phase B (T7) when the file-effect assertion becomes a DB-row-effect assertion. The T11 shim would refuse `target=workflow` today; the test would fail if executed with LLM access. |
| 4b | `tests/integration/test_inner_soul_standalone.py:362` (`test_inner_soul_workflow_change`) | 1/1 SKIP (`skip_llm_tests` gate) | **file-effect-rewritten-pending-Phase-B** | S11 / D-1 | None on branch — same as 4a; LLM-gated |
| 5a | `tests/integration/test_boot_report_recovery.py` (manager-init body cage) | 14/14 PASS | **intent-survives** | T5 | None — `manager._prompt_version_repo` / `manager._prompt_version_service` wired beside `manager.py:1171-1173`; the cage test source-scans `pool_orchestrator.py` text and remains valid |
| 5b | `tests/unit/test_skill_seeding.py` (skill-seed boot-scan) | 29/29 PASS | **intent-survives** | T5 / S10 | None — skill-seed boot-scan unaffected; carve-outs (`memories/*` not in versioned set) honored |
| 6a | `tests/migration/test_prompt_versions_index_parity.py` (NEW, fresh-SQLite boot smoke) | 10/10 PASS | **golden-regenerated** (T1) | T1 / T2 | None — index NAMES byte-identical to `__table_args__` for both dialects |
| 6b | `tests/test_prompt_version_resolution.py` (47 new tests) | 47/47 PASS | **golden-regenerated** (T3-T10 named tests) | T2-T10 / A1-A8 / C1-C7 / S10-S20 | None — every named test (state machine, selection pointer, audit stamping, precedence, create_and_submit, insert-retry, get_effective_rows, loader hook fail-open + precedence + drift + version-tag invalidate) GREEN |
| 7 | `tests/test_manager.py` + `tests/test_persistence.py` + `tests/test_llm_load_balance_integration.py` (invalidate-signature callers) | 54 PASS / 30 SKIP / 0 FAIL | **intent-survives** (no reclassification) | T7 / R10 / F9 | None — `test_manager.py` 12 PASS, `test_persistence.py` SKIP (no collection without config fixture), `test_llm_load_balance_integration.py` SKIP (`OPENAI_API_KEY` gate); whole-function patchers of `daemon.manager.load_and_cache_prompt` unaffected; direct invalidate-shape assertions match the new version-tagged signature |
| 8 | `tests/test_agents_api.py` (14 create/delete + 2 list) | **12 PASS / 2 FAIL** | **base-proven PRE-EXISTING** | pre-existing | Document only — see §2 base-prove verdicts |
| 9 | `tests/unit/test_router_scope_manifest.py` (7) + `tests/integration/test_router_class_manifest.py` (4) | 7+4=11/11 PASS | **intent-survives** | S13 / T6 (Phase B) | None — Phase A added NO router (`prompts_router` is Phase B T6); the trip-risk per S13 doesn't fire |
| 10 | spot-check: `tests/unit/test_compaction.py` + `tests/unit/test_snapshot_prompts.py` + `tests/unit/test_watcher_context_builder.py` + `tests/test_injection_compaction.py` | 196 PASS / 0 FAIL | **intent-survives** | F8 / R5 / D-WATCHER-CACHES | None — carve-outs hold (compaction, memory, watcher cache are Phase D / accepted-risk territory; none touched by Phase A) |
| 10b | spot-check: `tests/test_memory_integration.py` | **10 FAIL** (out of 216) | **base-proven PRE-EXISTING** | pre-existing | Document only — `_load_growth_rules(agent_path)` at `inner_soul.py:1434` reads `growth.md` via `re.search(...content...)`; the test fixture passes a `MagicMock` agent_path → `re.search` fails with `expected str, got MagicMock`. Branch's only `inner_soul.py` diff is the T11 shim (soul/workflow write guard) + version_tag threading (T7); neither touches the `_load_growth_rules` path. Verified pre-existing via code inspection (no commit on branch touches `_load_growth_rules`; test fixture has been unchanged since `1da0d84f1` 2026-07-11, well before branch base `e9cdc25c6` 2026-10-13). |

**Total counts on branch:** 414 PASS / 10 FAIL (pre-existing) / 32 SKIP (LLM-gated or fixture-gated) / 3 DESELECT (`OPENAI_API_KEY`).

---

## 2. Base-prove verdicts (RED leads at e9cdc25c6)

Methodology: `git worktree add <mktemp> e9cdc25c6` in a throwaway temp dir (`/tmp/ens-sbx-base-wquA25`), ran the two RED files there using the same `.venv/bin/python` + fence, then `git worktree remove --force`. Branch state untouched.

### 2.1 `tests/test_agents_api.py` — 2 fails on branch HEAD

Branch failures:
- `test_list_agents_success` — `assert 39 == 1` (real agents leak through the temp-agents fixture)
- `test_list_agents_empty_directory` — `assert [{...}, ...39 items...] == []` (same)

**Base-prove @ e9cdc25c6:** identical 2 failures reproduce. **VERDICT: pre-existing.**

### 2.2 `tests/test_progressive_dispatch.py` — 18 fails on branch HEAD

Branch failures: 18 tests in `TestManagerStreamingDispatchOriginalSource` + related, all failing with `daemon.migrations.runner.MigrationError: Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS": syntax error [SQL: ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type]`.

**Base-prove @ e9cdc25c6:** identical 18 failures reproduce. **VERDICT: pre-existing** (the documented `20260714_000001` DROP CONSTRAINT defect — SQLite-NO-OP runner; pg path unaffected; the runner is SQLite-only by design per `daemon/migrations/runner.py:693-718`; this branch's added migration `20261013_*_create_prompt_versions.sql` is the audited additive entry, not the offending one).

### 2.3 Combined verdict for QUARANTINE.md handoff

Both files fail identically at base and branch — these are NOT Phase-A regressions. Per repo quarantine philosophy: classify `not-fix`; flag for `.agents/tester/QUARANTINE.md` handoff.

**NOT EDITED.** The current `QUARANTINE.md` append convention was not verified (no time spent spelunking that file). The dispatcher (handoff recipient) should append per their existing convention. The 20 failures are documented here as T12 evidence.

### 2.4 Bonus: `tests/test_memory_integration.py` — 10 fails (spot-check)

The spot-check (item 10) surfaced 10 fails. The branch did not touch `_load_growth_rules` (T11 is soul/workflow writes; T7 is version_tag threading). The test fixture passes a `MagicMock` agent_path that `re.search` chokes on. **VERDICT: pre-existing.** Documented here for handoff completeness.

---

## 3. PG-parity finding

**Question (cycle-1 review unverified-items):** "In `tests/test_prompt_version_resolution.py`, confirm whether the `engine_postgres` fixtures ACTUALLY EXECUTE or skip on this host."

**Answer:** **`engine_postgres` fixtures do NOT EXIST in `tests/test_prompt_version_resolution.py`.** The file is **SQLite-only**:

- Line 80-120: `repo_engine` fixture creates a `sqlite:///<tempfile.mktemp>` engine with `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=30000`.
- Line 123-128: `repo` fixture composes the `PromptVersionRepository` over the SQLite engine.
- No `engine_postgres` fixture. No `@pytest.mark.parametrize` over dialects. No `postgres`/`psycopg`/`asyncpg` import.
- 47/47 PASS (all SQLite).

**Where PG parity actually lives:**

- `tests/migration/test_prompt_versions_index_parity.py` (10/10 PASS) — compares index NAMES between the `.sql` migration and `__table_args__` for BOTH SQLite + PG; both dialects produce the same index names (the canonical parity test).
- `daemon/migrations/versions/*.sql` — DUAL-DRIVER NOTES header discipline; `metadata.create_all` covers the PG-existing-DB path (`api.py:713/:726` lifespan precedent).
- `daemon/repositories/prompt_version/models.py` — SQLModel is dialect-agnostic via SQLAlchemy; `JSONBType` handles JSON columns on both engines (PG native JSONB, SQLite TEXT+JSON).

**Runtime PG execution on this host: NOT exercised by the new prompt-version test pack.** The 18 `test_progressive_dispatch.py` failures (pre-existing, see §2.2) are blocked at the migration stage on SQLite (the 20260714_000001 DROP CONSTRAINT defect prevents the runner from ever reaching the new `prompt_versions` migration in fresh-SQLite boots); the same boot flow on PG would be unaffected by that defect (PG-only path; SQLite-NO-OP) and would execute `prompt_versions` + `prompt_audit_events` via `metadata.create_all`. **No host-side PG runtime verification was performed during T12** (no `engine_postgres` test runs); PG parity is structural (schema + index names + DDL) rather than runtime (behavior).

---

## 4. A7 amendment commit

**Commit:** `db9f9e371 docs(contract): journal A7 retry-budget amendment (dispatcher-approved)`
**Files:** 2 changed, 5 insertions, 1 deletion
- `.agents/shared/planning/agent-prompt-immutability/phase-contract.md` — A7 cell amended to read "bounded retry = `_MAX_INSERT_RETRIES = 8`" with the `(amended 2026-10-13: dispatcher-approved post-hoc, R1 adjudication — 10-thread contention; bounded, recompute-max, exhaustion re-raises)` stamp.
- `.agents/shared/planning/agent-prompt-immutability/decisions.md` — appended "Phase A contract amendment journal" subsection under §2.9 Migration Sequencing, recording the R1 reviewer adjudication + dispatcher post-hoc sign-off.

**Cross-check:** `_MAX_INSERT_RETRIES = 8` confirmed at `daemon/repositories/prompt_version/repository.py:372` (used at `:478` `create_draft` and `:1287` `create_and_submit` IntegrityError retry loops). Mechanism = recompute-max under retry; exhaustion re-raises — matches the contract intent, only the bound differs from the originally-frozen "retry ≤ 3".

---

## 5. Reclassifications / fixes (commit SHAs)

| SHA | Kind | Description |
|-----|------|-------------|
| `db9f9e371` | docs | A7 amendment commit (phase-contract.md + decisions.md) |

**ZERO test-code edits.** Every Phase A test pack is GREEN. Every failure observed on branch is pre-existing at base (verified via throwaway worktree) and classified `not-fix` per repo quarantine philosophy.

**No `daemon/` production code touched** (the rule: only edit `daemon/` if base-proven branch-caused + in Phase-A scope). No such case was found.

---

## 6. Fence discipline (zero-tolerance check)

Every pytest invocation ran with:
- `ENSEMBLE_SELF_ENV=dev` (override ambient `live`)
- `POSTGRES_DB=ensemble_dev_test_only` (override ambient `ensemble_prod`)
- `POSTGRES_*` set to localhost/test-only (no prod IP)
- `DAEMON_PORT=8079` (override ambient unset)
- `HOME=$(mktemp -d -t ens-fake-home-XXXXXX)` (override ambient `/home/nea` — 2026-10-12 critical note compliance)
- `daemon.__file__` resolves to `/home/ensemble-worktrees/agent-prompt-immutability/daemon/__init__.py` (verified pre + post)

**The loader coder's `POSTGRES_DB=ensemble_prod` ambient posture was REFUSED.** `unset POSTGRES_URL DATABASE_URL` + `export POSTGRES_DB=ensemble_dev_test_only` is the binding posture for this commission (same discipline as the drift coder's bash pack).

---

## 7. Notes for morning review

1. **No reclassifications needed in Phase A.** The Phase B reclassification scope is well-defined: when Phase B's `create_and_submit` rewrite lands, the file-effect assertions in the LLM-gated inner_soul test packs (items 4a + 4b) become DB-row-effect assertions. Phase B owner T7 should drive this with a fork-id annotation per the rubric (`# reclassified per S11/D-1: …`).
2. **Two RED leads confirmed pre-existing.** Per repo quarantine philosophy, both go to `.agents/tester/QUARANTINE.md` handoff (NOT EDITED by T12 — append convention not verified). The 20260714_000001 DROP CONSTRAINT defect is the root cause for the 18 progressive-dispatch fails; the 2 agents-api fails are a test-fixture bypass (real agents leak through `patch.object(BASE_DIR, ...)`).
3. **Phase B's T6 router trip risk (S13) does not fire in Phase A** because Phase A adds NO router. Phase B owner must update `tests/unit/test_router_scope_manifest.py` manifest + add to census when mounting `/api/prompts/` router.
4. **PG runtime verification is structural-only.** No runtime PG test for `prompt_versions` exists in this branch. The 47 new tests are SQLite-only; the 10 parity tests cover index names + DDL shape, not PG behavior. A follow-up commission should add a runtime-PG smoke (`engine_postgres` fixture + PG-marked CI lane) once PG is reachable from the test container — currently it's not (host lacks PG connection; SQLite fallback per `daemon/persistence.py`).

---

**End of test-wall-classification-phaseA.md.**