# Latest-baseline hygiene @ e202e277 — 17 pre-existing failures (mission-tree gate 2026-09-07)

**Context**: During the `feature/job-queue-mission-tree` @ `708ee7a5` verification gate, two partitions (P-8, P-10) surfaced 17 "NEW-suspect" failures. Both groups were adjudicated via disposable merge-base worktree (`git merge-base HEAD latest` = `e202e277`) + 3× solo determinism at HEAD: **ALL 17 fail at BASE with byte-identical signatures → pre-existing on `latest`, branch-exonerated**. Recorded here so future gates classify them on sight instead of re-adjudicating.

## The 17 nodes

**Fixture/stub drift (instances/agents surfaces — untouched by mission-tree branch):**
- `tests/test_agents_api.py::test_list_agents_success` — `assert 34 == 1` (real `agents/` dir, 34 entries, leaks past expected-empty tempdir fixture)
- `tests/test_agents_api.py::test_list_agents_empty_directory` — `assert [...] == []` (same leak)
- `tests/api/test_instance_ui_prefs_api.py::test_get_instances_list_includes_prefs_fields` — `TypeError: _ManagerStandin.list_instances() got an unexpected keyword argument 'search'` (standin predates `search=` kwarg at `daemon/routers/instances.py:424`, instance-search feature)
- `tests/api/test_instance_ui_prefs_api.py::test_get_instances_list_includes_icon_tag` — same standin kwarg drift

**Title generation:**
- `tests/test_enqueue_shared.py::TestTitleGeneration::test_triggers_title_on_idle_to_running` — `run_async_no_wait` call_count 2≠1 (double-fire on idle→running); `coroutine ... never awaited` warning on BOTH base and HEAD

**Fresh-SQLite migration trap class-wide (masks assertions, NOT logic breakage):**
- `tests/test_spawn_limit_edge_cases.py::TestSpawnLimitEdgeCases` — ALL 9 methods: `MigrationError: 20260714_000001 ... DROP CONSTRAINT ... near "CONSTRAINT": syntax error` (PG-only syntax on SQLite; every test instantiates `InstanceManager` with `db_path=":memory:"`). This is the known Critical-Notes trap (see also LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only.md) now manifesting inside a partition.

**Config-defaults drift:**
- `tests/test_skill_evolution_config.py::TestConfigIntegration::test_skill_evolution_defaults_via_config` — `assert 20 == 10` (`ab_sample_size`)
- `tests/test_skill_evolution_config.py::TestDefaults::test_defaults` — `assert 'https://api.openai.com/v1' is None` (`embedding_base_url` now has non-None default)

**Reconciler invariant:**
- `tests/test_terminal_orphan_matrix.py::test_jobitem_task_status_matrix[pending-True-active]` — `Reconciler invariant violation ... admission_state=active, has_lock=False`

## Why P-10 showed a "FAIL-signature shift"

Baseline (2026-09-03) 14F healed; these 12 appeared. The rotation is fixture-mask churn (e.g. sqlite-migration error class surfacing differently as fixtures/config defaults evolved on latest), not branch behavior. Adjudication protocol: disposable worktree at merge-base + `daemon.__file__` isolation proof + 3× solo at HEAD; worktrees under `/tmp/ens-mt-base*` and removed after.

## Disposition

- Not quarantined by the gate (report-only arc; quarantine requires its own evidence discipline).
- Candidates for the next baseline sweep on `latest`: fix the 4 fixture/stub drifts + title-gen double-fire; decide quarantine vs fix for the sqlite-trap class + config-defaults pair + reconciler invariant node.
- Any future partition gate on a branch cut from `latest` ≥ `e202e277` should classify these 17 as PRE-EXISTING on sight (cite this file).
