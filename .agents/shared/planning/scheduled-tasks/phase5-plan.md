# Phase 5: Tests & Test Packs

## Objective

Author the comprehensive test surface that proves the scheduling feature works end-to-end: unit tests for adapter semantics (TZ, DST, catch-up, idempotency), API tests for the three new REST endpoints (phase-3 contract), integration tests for the schedule→dispatch→job-created happy path, and a thin shell pack (`tests/packs/scheduled_tasks_acceptance.sh`) that runs the new unit + API suites and is registered in `.agents/tester/PACKS.md`. The existing test files stay green — this phase ADDS, never BREAKS.

Specifically:

- **Unit tests** for adapter semantics that phase-1/phase-2 introduce: TZ resolution (D2 default chain), DST (D8 spring-forward gap + fall-back ambiguity), catch-up (D3 past-due fire / beyond-cap no-dispatch), idempotency (D4 restart double-dispatch)
- **API tests** for the three new routes (`POST /schedules`, `DELETE /schedules/{id}`, `GET /schedules/{id}`) — phase-3 contract
- **Integration tests** for the schedule→dispatch→job-created happy path (D4 single-uuid contract) AND cancel-prevent-dispatch test (D5)
- **Pack** `tests/packs/scheduled_tasks_acceptance.sh` (NEW) — thin wrapper running `pytest -q tests/test_scheduler_adapter.py tests/test_scheduler_api.py tests/integration/test_scheduled_tasks_e2e.py`; registered in `.agents/tester/PACKS.md`

> **Note (verified):** `tests/conftest.py` `clean_env` autouse fixture (`tests/conftest.py:653-688`) tracks env keys with `_TRACKED_ENV_PREFIXES = ("OPENAI_", "ENSEMBLE_")` (`:644`). **`ENSEMBLE_SCHEDULING_*` keys are AUTO-COVERED** by the prefix match — NO `_TRACKED_ENV_EXACT` entry needed. This corrects the older recon advice and is reflected in Task 1 below.

## Coupling

- **Depends on**: phase-1 (adapter semantics — TZ, DST, catch-up, idempotency fix), phase-2 (shared scheduling service surface that phase-3 hands off to and phase-5 mocks), phase-3 (REST endpoints + Pydantic models being tested), phase-4 (meta.json allow-list edits feed the registration test)
- **Coupling type**: tight (with phase-2 service contract; loose with phase-1/3/4)
- **Shared files with other phases**:
  - `tests/test_scheduler_adapter.py` (this phase ADDS classes — does not modify existing)
  - `tests/test_scheduler_api.py` (this phase ADDS classes — does not modify existing)
  - `tests/integration/test_scheduled_tasks_e2e.py` (NEW)
  - `tests/packs/scheduled_tasks_acceptance.sh` (NEW)
  - `.agents/tester/PACKS.md` (this phase APPENDS a section)
- **Shared APIs / contracts**:
  - `SchedulerAdapter` (`daemon/sources/adapters/scheduler.py`) — unit tests exercise `_parse_schedule_config` and `_get_next_trigger_time`
  - `make_config(source_id, config)` fixture (`tests/conftest.py:764-774`) — wrap for new tests
  - `mock_on_message` fixture (`tests/conftest.py:743-746`) — required for `SchedulerAdapter(config, mock_on_message)`
  - `_TRACKED_ENV_PREFIXES` (`tests/conftest.py:644`) — covers `ENSEMBLE_SCHEDULING_*` automatically
  - `httpx + FastAPI` test pattern (`tests/test_scheduler_api.py:1-90`) — mirror for new API tests
  - `tests/integration/conftest.py` (`tests/integration/conftest.py:1-50`) — integration test base
  - Pack format: `tests/packs/*.sh` — thin wrappers (`set -euo pipefail`, `cd "$(dirname "$0")/../.."`, `exec .venv/bin/python <py>`, two-layer timeout documented at `:9-10` — exemplar `g7_unique_index_smoke_test.sh`, 13L)
- **Why this coupling**: This phase is the **verification tier — the acceptance PACK is the merge gate (architecture §8 item 16).** Tests MUST run against the real phase-1/2/3/4 changes. **Precise merge order:** phase 1 (foundation) → phase 2 (shared service + tools) + phase 4 (registration + docs) merge as ONE PR (drift test bidirectional) → phase 3 (REST surface) follows phase 2 (depends on the shared scheduling service) → phase 5 (pack) gates the final merge. **No xfail/skip parking.** All architecture-pinned semantics (croniter-default for cron, anchor_local_to_utc fold=0/gap-shift for one-shot, key from `self._run_at`, gate to `SCHEDULE_TYPE_ONE_TIME`) are asserted directly. The pack at `tests/packs/scheduled_tasks_acceptance.sh` is the merge-gate CI run.

## Context

### Existing test substrate (this phase adds to these — does NOT replace)

**`tests/test_scheduler_adapter.py` (1564L)** — adapter-direct unit tests, NO DB. Classes already present:
- `TestCronParsing` (`:18`)
- `TestIntervalScheduling` (`:85`)
- (other classes throughout — full inventory not enumerated; this phase ADDS new classes at the end of file)
- Fixtures: `mock_on_message` (`:743-746`), `make_config` (`:764-774`) — both reusable
- **TZ/DST test slots HERE** — adapter-direct, `ZoneInfo` cases against `_parse_schedule_config` and `_get_next_trigger_time` (`scheduler.py:204-216` for cron validation, `:210-211/:466-467` for aware datetimes into croniter)

**`tests/test_scheduler_api.py` (1068L)** — FastAPI + mock_manager fixture (`:22-40`) + temp SQLite file DB with HAND-WRITTEN `CREATE TABLE` for `source_configs` / `instance_mappings` / `schedule_executions` (`:42-90`), driven via httpx. New REST endpoint tests slot HERE.

**`tests/test_scheduler_instance_mode.py` (1288L)** — instance-mode parsing, one_time forcing (`:26-100`), error-recovery callbacks. **Not in scope** for this phase (instance-mode behavior is owned by phase-1/2; phase-5 reads its existing coverage and does NOT add to it).

**`tests/job_queue/conftest.py`** — `:84-102` in-memory SQLite + `SQLModel.metadata.create_all` + autouse `_truncate_tables` (`:50-81`). Not directly used by this phase's new tests; relevant context only.

**`tests/conftest.py`** — `clean_env` autouse (`:653-688`); `_TRACKED_ENV_PREFIXES = ("OPENAI_", "ENSEMBLE_")` (`:644`). **`ENSEMBLE_SCHEDULING_*` keys are auto-covered** — NO `_TRACKED_ENV_EXACT` entry needed for new scheduling tests. (Older recon advice overstated; reflected correctly in Task 1.4.)

### Integration test conventions
- `tests/integration/` flat `test_*.py` files; `__init__.py` + `conftest.py` present
- EXCLUDED by default via `pyproject.toml:80` `addopts = "-m 'not integration and not postgres'"`
- Run via `pytest -m integration` (manual or pack-driven)
- Real `InstanceManager` construction pattern: see `tests/integration/chat_source_harness.py` + `tests/integration/test_chat_source_enqueue_wake_e2e.py`
- This phase's integration test uses the same harness pattern: build a real manager, mint a schedule via the shared service (phase-2), fast-forward or trigger immediately, assert a `JobItem` lands in `job_queue_items` with `job_type='message'`, `source='scheduler'`, and **`JobItem.job_id == Task.work_id` (single-uuid contract at `daemon/services/instance_messaging.py:2504/:2554`, `work_id_required=True :2555`)**. The work_id == job_id contract is the load-bearing invariant of the entire job system — phase-5 pins it in the integration tests.

### D8 DST semantics (MANDATORY test pair, D8)
- Spring-forward gap: e.g., `America/New_York` 2026-03-08 02:30 — does NOT exist. Daily cron `30 2 * * *` in `America/New_York` — pinned semantics MUST be tested.
- Fall-back ambiguity: e.g., `America/New_York` 2026-11-01 01:30 — exists TWICE. Daily cron `30 1 * * *` — pinned semantics MUST be tested.
- The exact semantics (skip, fire-once at first occurrence, fire-twice, etc.) come from phase-1's documentation. Phase-5 PARAMETERIZES on whatever phase-1 chose — it does NOT re-specify. The Coupling section below marks this dependency explicitly.
- Adapter does NO manual DST arithmetic (phase-1 design): aware datetimes flow into croniter at `scheduler.py:210-211/:466-467`; `croniter>=3.0.0` per `pyproject.toml:25`.

### Pack format
- `tests/packs/*.sh` — thin wrappers (13 lines max); format documented in `.agents/tester/PACKS.md`
- Two-layer timeout: outer `timeout 300` (caller); inner `signal.alarm(120)` in any Python script
- `set -euo pipefail`; `cd "$(dirname "$0")/../.."`; `exec .venv/bin/python <py>`
- Pack is registered in `.agents/tester/PACKS.md` — human-maintained ledger, per-commission section + verdict paragraph + `| Pack | Invocation | Scope | Result |` table
- NO machine-parsed manifest; maintainer writes both the pack script and the ledger entry
- Exemplar: `tests/packs/g7_unique_index_smoke_test.sh` (13L) — single-file Python verifier

## Tasks

### Task 1: Adapter-direct unit tests (TZ, DST, catch-up, idempotency)

> All tests use the existing `mock_on_message` and `make_config` fixtures (`tests/conftest.py:743-774`). ZoneInfo cases against `SchedulerAdapter._parse_schedule_config` and `_get_next_trigger_time` (`daemon/sources/adapters/scheduler.py:99/:117/:204-216/:210-211/:466-467`).

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 1.1 | New `TestTzResolution` class | Test the D2 default chain (explicit param → `ENSEMBLE_SCHEDULING_DEFAULT_TZ` env → host-local auto-detect → **terminal fallback = `datetime.timezone.utc`** per architecture §4.3). Test cases: explicit tz wins, env default wins when param absent, host-local fallback when both absent, UTC fallback when host-local also unavailable (monkeypatch `time.tzname` to `("UTC", "UTC")` plus clear env), loud warning emitted in the UTC fallback case (caplog). | `tests/test_scheduler_adapter.py` (NEW class appended at end) |
| 1.2 | New `TestDstSemantics` class (D8) — parameterized over BOTH paths (architecture §4.2) | Test phase-1's pinned DST semantics on **BOTH code paths**: (a) **cron path** via `croniter` (already pinned to skip-the-gap / first-occurrence-on-fall-back per ADR-008); (b) **one-shot path** via `daemon.utils.tz.anchor_local_to_utc` (pinned to fold=0 / gap-shift+warning per architecture §4.2). Four named test cases: `test_dst_spring_forward_gap_cron` (e.g. `America/New_York` 2026-03-08 02:30 — assert croniter skips to 03:30 EDT); `test_dst_fall_back_ambiguity_cron` (2026-11-01 01:30 — assert croniter picks first occurrence EDT, UTC-4); `test_dst_spring_forward_gap_one_shot_anchor` (2026-03-08 02:30 — assert `anchor_local_to_utc` shifts to 03:30 EDT + emits warning containing `"shifted-forward"`); `test_dst_fall_back_ambiguity_one_shot_anchor` (2026-11-01 01:30 — assert `anchor_local_to_utc` returns first occurrence + no warning). All 4 tests assert against the documented semantic — no `pytest.skip`. | `tests/test_scheduler_adapter.py` (NEW class appended at end) |
| 1.3 | New `TestCatchUpSemantics` class (D3) | Test past-due fire logic. Test cases: `test_within_cap_fires_late` (one-shot past-due by N seconds, `one_shot_max_lateness_seconds=N+1` → fires); `test_beyond_cap_no_dispatch_with_reason_marker` (one-shot past-due by N seconds, cap `N-1` → does NOT fire, `schedule_executions` row recorded with `status='skipped'` + `error_message` reason); `test_cron_skips_missed` (cron past-due by hours → does NOT fire-catch-up; next occurrence only). | `tests/test_scheduler_adapter.py` (NEW class appended at end) |
| 1.4 | New `TestIdempotencyRestart` class (D4) — 3 tests, no `pytest.skip` | Three test cases: (a) `test_no_double_dispatch_after_restart` — simulate "crash between enqueue and disable-write" per phase-1's mechanism; assert at-most-one `JobItem` lands in `job_queue_items` after a simulated restart; assert **key value** equals `f"scheduler:{source_id}:{self._run_at.isoformat()}"` byte-for-byte (NOT `now.isoformat()`); (b) `test_cron_not_affected_by_idempotency_key` (architecture §1.3) — recurring schedule, two distinct `next_trigger` values, assert **two** distinct JobItems (cron fires intentionally keep minting fresh JobItems); (c) `test_5s_retry_collapse` — within a single daemon lifetime, repeat `_route_via_job_queue` for a failing one-shot and assert the second attempt returns the existing JobItem (no second row). All 3 tests pin the phase-1 mechanism. No `pytest.skip`. | `tests/test_scheduler_adapter.py` (NEW class appended at end) |
| 1.5 | `clean_env` already covers `ENSEMBLE_SCHEDULING_*` | Verified: `_TRACKED_ENV_PREFIXES = ("OPENAI_", "ENSEMBLE_")` at `tests/conftest.py:644` matches the `ENSEMBLE_SCHEDULING_*` prefix; no `_TRACKED_ENV_EXACT` entry needed. **Action: this phase makes ZERO edits to `tests/conftest.py`.** (Older recon advice that suggested adding `_TRACKED_ENV_EXACT` is OBSOLETE; the prefix-match covers it.) | (no edit — verification step only) |

**Test skeleton (frozen for implementer — DST parameterized on phase-1):**

```python
# tests/test_scheduler_adapter.py (NEW classes appended at end of file)

class TestTzResolution:
    """D2 default tz chain: explicit → ENSEMBLE_SCHEDULING_DEFAULT_TZ → host-local → UTC+loud."""

    def test_explicit_tz_wins(self, mock_on_message, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TZ", "UTC")
        config = make_config("t1", {"schedule": "0 6 * * *", "timezone": "Asia/Tokyo", "agent": "./agents/a", "message": "x"})
        adapter = SchedulerAdapter(config, mock_on_message)
        assert adapter._resolved_tz.key == "Asia/Tokyo"

    def test_env_default_used_when_param_absent(self, mock_on_message, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TZ", "Europe/Berlin")
        config = make_config("t2", {"schedule": "0 6 * * *", "agent": "./agents/a", "message": "x"})
        adapter = SchedulerAdapter(config, mock_on_message)
        assert adapter._resolved_tz.key == "Europe/Berlin"

    def test_utc_fallback_warns_loudly(self, mock_on_message, monkeypatch, caplog):
        monkeypatch.delenv("ENSEMBLE_SCHEDULING_DEFAULT_TZ", raising=False)
        with caplog.at_level("WARNING"):
            config = make_config("t3", {"schedule": "0 6 * * *", "agent": "./agents/a", "message": "x"})
            SchedulerAdapter(config, mock_on_message)
        assert any("UTC" in r.message for r in caplog.records)


class TestDstSemantics:
    """D8 DST semantics — parameterized over BOTH paths (architecture §4.2):
    cron path (croniter-default) AND one-shot path (anchor_local_to_utc fold=0 / gap-shift).
    These tests assert architecture-pinned behavior — no pytest.skip.
    """

    def test_dst_spring_forward_gap_cron(self, mock_on_message):
        # 2026-03-08 02:30 America/New_York does NOT exist.
        # Croniter-default: skip-the-gap → fires at 03:30 EDT.
        # See phase1-plan.md §Task 8.1 + ADR-008.
        # Test asserts next_trigger is 2026-03-08T03:30:00-04:00.
        ...

    def test_dst_fall_back_ambiguity_cron(self, mock_on_message):
        # 2026-11-01 01:30 America/New_York exists TWICE.
        # Croniter-default: first occurrence (EDT, UTC-4).
        # Test asserts next_trigger is 2026-11-01T01:30:00-04:00.
        ...

    def test_dst_spring_forward_gap_one_shot_anchor(self):
        # 2026-03-08 02:30 America/New_York does NOT exist.
        # anchor_local_to_utc (architecture §4.2): shift forward + warning.
        # Test asserts shifted forward to 03:30 EDT + warning containing "shifted-forward".
        # See phase1-plan.md §Task 9 + ADR-008.
        ...

    def test_dst_fall_back_ambiguity_one_shot_anchor(self):
        # 2026-11-01 01:30 America/New_York exists TWICE.
        # anchor_local_to_utc: fold=0, first occurrence (EDT, UTC-4).
        # Test asserts first occurrence + no warning.
        ...


class TestCatchUpSemantics:
    """D3 catch-up rules."""

    def test_one_shot_within_cap_fires_late(self, mock_on_message, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS", "3600")
        # construct config with run_at 60s in the past; expect adapter to fire on next wake
        # (no DB in this test — assert via mock_on_message callback invocation)
        ...

    def test_one_shot_beyond_cap_no_dispatch_with_reason(self, mock_on_message, monkeypatch):
        # construct config with run_at 2h in the past, cap=60s
        # assert mock_on_message NOT called; schedule_executions row recorded skipped+reason
        # (this test requires the phase-2 schedule_executions repository — covered by Task 3.3 integration test)
        ...


class TestIdempotencyRestart:
    """D4 no-double-dispatch on restart — architecture §1.2/§1.3 pinned.
    Key value source = self._run_at.isoformat() (NOT next_trigger).
    Emission gated to SCHEDULE_TYPE_ONE_TIME (cron fires keep minting fresh JobItems).
    No pytest.skip — pack is the merge gate (architecture §8 item 16).
    """

    def test_no_double_dispatch_after_restart(self):
        # Simulate crash-between-enqueue-and-disable-write.
        # Key sourced from self._run_at.isoformat() — NOT next_trigger.
        # This test asserts at-most-one JobItem lands after a simulated restart.
        # Companion assertion: key value equals f"scheduler:{source_id}:{self._run_at.isoformat()}"
        # byte-for-byte (NOT now.isoformat()).
        ...

    def test_cron_not_affected_by_idempotency_key(self):
        # Architecture §1.3: cron path must NOT emit idempotency_key.
        # Two distinct cron fires → two distinct JobItems (no collapse).
        ...

    def test_5s_retry_collapse(self):
        # Within a single daemon lifetime, repeat _route_via_job_queue for a failing one-shot.
        # Assert the second attempt returns the existing JobItem (no second row).
        ...
```

### Task 2: API tests (REST surface, phase-3 contract)

> Slot in at `tests/test_scheduler_api.py` (1068L). Mirror the existing fixture pattern (mock_manager :22-40, temp SQLite :42-90, httpx client).

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 2.1 | New `TestCreateSchedule` class | Test `POST /schedules`. Cases: (a) 201 on valid body; (b) 503 when `is_write_paused=True`; (c) 409 on duplicate `source_id`; (d) 422 on invalid `instance_mode`; (e) 422 on missing required field (e.g. `cron_expression` when `recurrence="cron"`); (f) response body conforms to `ScheduleCreateResponse` schema (verify `next_run_at_local` AND `next_run_at_utc` both present). Mock `manager.scheduling_service.create_schedule` via AsyncMock. | `tests/test_scheduler_api.py` (NEW class appended at end) |
| 2.2 | New `TestGetScheduleById` class | Test `GET /schedules/{id}`. Cases: (a) 200 on existing; (b) 404 on missing; (c) 400 on non-scheduler source_type; (d) response carries BOTH `next_run_at_local` AND `next_run_at_utc` (never only one populated); (e) `cancelled_at` is `null` for non-cancelled, ISO string for cancelled. | `tests/test_scheduler_api.py` (NEW class appended at end) |
| 2.3 | New `TestCancelSchedule` class | Test `DELETE /schedules/{id}`. Cases: (a) 200 on existing; (b) 503 when `is_write_paused=True`; (c) 404 on missing; (d) 400 on non-scheduler source_type; (e) response is `ScheduleCancelResponse` shape with `status="cancelled"` AND `last_execution_id` echoed (architecture §5.3); (f) **history preservation assertion** — assert that `delete_source_config` was NEVER called (mock `manager._source_repository.delete_source_config` and assert `.assert_not_called()`); (g) cancel-by-label resolution — if `schedule_id` is a label not a UUID, the shared service resolves via `get_source_config_by_name`; verify by mocking that path. **NEW (architecture §3.4):** assert `DELETE` calls `cancel_source_config` (the NEW atomic method, NOT the two-call sequence). | `tests/test_scheduler_api.py` (NEW class appended at end) |
| 2.4 | New `TestScheduleListFilter` class (architecture §3.4) | Test `GET /schedules` filter. Cases: (a) default call excludes `SourceStatus.CANCELLED` rows from the response; (b) `?include_cancelled=true` returns cancelled rows; (c) `?include_cancelled=false` excludes them (mirror of default). Assert the SQL filter is applied server-side, not client-side. **NEW (architecture §3.4):** `GET /schedules/{id}` DOES return cancelled rows (the "did I actually cancel X?" path) — pin via separate case in `TestGetScheduleById` (Task 2.2). | `tests/test_scheduler_api.py` (NEW class appended at end) |

**Test skeleton (frozen for implementer):**

```python
# tests/test_scheduler_api.py (NEW classes appended at end of file)


class TestCreateSchedule:
    """Tests for POST /api/schedules endpoint (phase-3 contract)."""

    @pytest.mark.asyncio
    async def test_create_returns_201_with_both_timezones(self, client, mock_manager):
        mock_manager.scheduling_service.create_schedule = AsyncMock(return_value=_mock_schedule_detail(
            id="morning-briefing",
            status="running",
            next_run_at_local="2026-10-02T06:00:00-04:00",
            next_run_at_utc="2026-10-02T10:00:00+00:00",
        ))
        body = {
            "source_id": "morning-briefing",
            "name": "Morning Briefing",
            "agent_id": "ari",
            "message": "Give me a morning briefing",
            "project_id": "default",
            "recurrence": "daily",
            "local_time": "06:00",
            "timezone": "America/New_York",
        }
        response = await client.post("/schedules", json=body)
        assert response.status_code == 201
        data = response.json()
        assert data["detail"]["next_run_at_local"] == "2026-10-02T06:00:00-04:00"
        assert data["detail"]["next_run_at_utc"] == "2026-10-02T10:00:00+00:00"

    @pytest.mark.asyncio
    async def test_create_503_when_write_paused(self, client, mock_manager):
        mock_manager.is_write_paused = True
        response = await client.post("/schedules", json={...})
        assert response.status_code == 503
        assert "Writes are paused" in response.json()["detail"]


class TestCancelSchedule:
    """Tests for DELETE /api/schedules/{id} — terminal cancel, history preserved."""

    @pytest.mark.asyncio
    async def test_cancel_calls_cancel_schedule_NOT_delete_source_config(self, client, mock_manager):
        mock_manager.scheduling_service.cancel_schedule = AsyncMock(return_value=_mock_cancelled(
            id="morning-briefing",
            status="cancelled",
            cancelled_at="2026-10-01T20:00:00+00:00",
        ))
        mock_manager._source_repository.delete_source_config = Mock()
        response = await client.delete("/schedules/morning-briefing")
        assert response.status_code == 200
        assert mock_manager.scheduling_service.cancel_schedule.await_count == 1
        mock_manager._source_repository.delete_source_config.assert_not_called()
```

### Task 3: Integration test (happy path + cancel-prevent-dispatch)

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 3.1 | New `tests/integration/test_scheduled_tasks_e2e.py` | One-shot schedule → trigger → job created. Build a real `InstanceManager` via the integration harness (mirror `tests/integration/test_chat_source_enqueue_wake_e2e.py`'s pattern). Create a schedule via `manager.scheduling_service.create_schedule(...)`. Force-fire the schedule (manual trigger). Assert a `JobItem` lands in `job_queue_items` with: `job_type='message'`, `source='scheduler'`, `JobItem.job_id == Task.work_id` (single-uuid contract at `instance_messaging.py:2504/:2554`, `work_id_required=True :2555`). Assert one `JobItem` — never two (D4 guard). | `tests/integration/test_scheduled_tasks_e2e.py` (NEW) |
| 3.2 | Cancel-prevent-dispatch test | Same file; new test. Create a schedule. Cancel it via `manager.scheduling_service.cancel_schedule(...)`. Attempt to manual-trigger the cancelled schedule. Assert NO `JobItem` lands in `job_queue_items`. Assert `get_source_config(id).status == "cancelled"`. Assert `schedule_executions` history PRESERVED (the cancelled schedule may have history from a prior fire; verify rows are still there). **NEW (architecture §3.3):** assert `last_execution_id` is echoed in the cancel response. | `tests/integration/test_scheduled_tasks_e2e.py` (NEW) |
| 3.2b | Stop-doesn't-clobber-cancelled test (architecture §3.3) | New test `test_stop_adapter_does_not_clobber_cancelled`: cancel a schedule, call `stop_adapter` again, assert `status == "cancelled"` (NOT `stopped`). The clobber guard at phase-1 §Task 7.4 must prevent the unconditional `update_source_status(STOPPED)` write. | `tests/integration/test_scheduled_tasks_e2e.py` (extend) |
| 3.2c | Cancelled-never-boot-starts test (architecture §3.4) | New test `test_cancelled_never_boot_starts`: cancel a schedule, restart the adapter (simulate boot), assert no adapter is created for the cancelled row. Boot filter at `registry.py:292` must skip rows where `status in {STOPPED, CANCELLED}`. | `tests/integration/test_scheduled_tasks_e2e.py` (extend) |
| 3.3 | Catch-up integration test (D3) | Same file; new test. Create a one-shot schedule with `run_at` 2 hours in the past; `one_shot_max_lateness_seconds` = 60. Force a wake (don't manual_trigger — let the cron loop fire on its next tick). Assert NO `JobItem` is dispatched. Assert a `schedule_executions` row is recorded with `status='skipped'` and `error_message` indicating the lateness cap was hit. Assert the schedule's `last_run_at` is the marker timestamp, NOT a dispatch. (This is the integration version of Test 1.3's `test_one_shot_beyond_cap_no_dispatch_with_reason`.) | `tests/integration/test_scheduled_tasks_e2e.py` (NEW) |
| 3.4 | Mark all integration tests `@pytest.mark.integration` | `pytestmark = pytest.mark.integration` at module level so `pyproject.toml:80` default-addopts `'-m "not integration and not postgres"'` excludes them from the default suite. They run via `pytest -m integration` or via the pack. | `tests/integration/test_scheduled_tasks_e2e.py` |

**Integration test skeleton (frozen for implementer):**

```python
# tests/integration/test_scheduled_tasks_e2e.py (NEW)
"""Scheduled tasks end-to-end integration tests.

Excluded by default; run via `pytest -m integration` or the
`scheduled_tasks_acceptance` pack (tests/packs/scheduled_tasks_acceptance.sh).
"""
from __future__ import annotations

import asyncio
import pytest

from tests.integration.chat_source_harness import (
    build_live_pool_manager,
    wait_until,
)


pytestmark = pytest.mark.integration


async def test_one_shot_schedule_creates_job_item():
    """D4 single-uuid contract: schedule → dispatch → JobItem.job_id == Task.work_id."""
    manager = await build_live_pool_manager()
    try:
        schedule = await manager.scheduling_service.create_schedule({
            "source_id": "int-test-1",
            "name": "Integration Test",
            "agent_id": "ari",
            "message": "fire",
            "project_id": "default",
            "recurrence": "once",
            "run_at": "...",  # tz-qualified ISO near-now
            "timezone": "UTC",
        })
        # Manual trigger the schedule.
        execution_id = await manager.source_registry.get(schedule.id).manual_trigger()
        # Wait for the JobItem to land in job_queue_items.
        await wait_until(
            lambda: manager.job_queue_service.repository.get_by_source("scheduler"),
            timeout=10,
        )
        items = manager.job_queue_service.repository.get_by_source("scheduler")
        assert len(items) == 1
        item = items[0]
        assert item.job_type == "message"
        assert item.source == "scheduler"
        # Single-uuid contract: JobItem.job_id == Task.work_id
        task = manager.task_repository.get_by_work_id(item.job_id)
        assert task is not None
        assert task.work_id == item.job_id
    finally:
        await manager.shutdown()


async def test_cancelled_schedule_never_dispatches():
    """D5 cancel = terminal; cancelled schedules do not dispatch even on manual trigger."""
    manager = await build_live_pool_manager()
    try:
        schedule = await manager.scheduling_service.create_schedule({...})
        await manager.scheduling_service.cancel_schedule(schedule.id)
        # Attempt to manual-trigger — should be rejected (adapter evicted or guard returns None).
        with pytest.raises((RuntimeError, AttributeError)):
            await manager.source_registry.get(schedule.id).manual_trigger()
        items = manager.job_queue_service.repository.get_by_source("scheduler")
        assert items == []
        # History preserved: schedule_executions rows still queryable.
        history = manager._source_repository.list_schedule_executions(schedule.id, limit=100)
        assert isinstance(history, list)  # may be empty for fresh schedule, but the query must succeed
    finally:
        await manager.shutdown()


async def test_one_shot_beyond_cap_no_dispatch_with_reason():
    """D3 catch-up: past-due beyond cap → no dispatch + reason marker."""
    manager = await build_live_pool_manager()
    try:
        schedule = await manager.scheduling_service.create_schedule({
            ...,
            "recurrence": "once",
            "run_at": "...",  # 2h in the past
            "one_shot_max_lateness_seconds": 60,  # cap = 60s
        })
        # Wait for the catch-up logic to fire (no manual_trigger; let the loop run).
        await asyncio.sleep(2.0)
        items = manager.job_queue_service.repository.get_by_source("scheduler")
        assert items == []
        history = manager._source_repository.list_schedule_executions(schedule.id, limit=10)
        assert len(history) >= 1
        assert history[0].status == "skipped"
        assert "lateness" in (history[0].error_message or "").lower()
    finally:
        await manager.shutdown()
```

### Task 4: Pack — `tests/packs/scheduled_tasks_acceptance.sh`

> Mirror the `g7_unique_index_smoke_test.sh` (13L) format. Thin wrapper. Two-layer timeout.

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 4.1 | Author pack shell script | Three-section pytest invocation: (1) adapter unit tests `tests/test_scheduler_adapter.py`; (2) API tests `tests/test_scheduler_api.py`; (3) integration tests `tests/integration/test_scheduled_tasks_e2e.py` via `-m integration`. Document the two-layer timeout in a header comment (mirror `:9-10` of `g7_unique_index_smoke_test.sh`). | `tests/packs/scheduled_tasks_acceptance.sh` (NEW) |
| 4.2 | Register pack in `.agents/tester/PACKS.md` | Append a `## Scheduled Tasks Acceptance` section with a verdict paragraph (placeholder: "TBD pending phase-1/2/3/4 merge") and a `| Pack | Invocation | Scope | Result |` table with one row pointing at the new pack. Maintainer writes the row entry; this plan provides the placeholder structure. | `.agents/tester/PACKS.md` (APPEND a section) |

**Pack shell script (frozen for implementer):**

```bash
#!/usr/bin/env bash
# Scheduled Tasks Acceptance Pack
#
# Runs the full test surface that proves the scheduled-tasks feature works:
#   (1) adapter-direct unit tests (TZ, DST, catch-up, idempotency)
#   (2) REST API tests (phase-3 contract: POST/DELETE/GET-by-id)
#   (3) integration tests (happy path + cancel-prevent-dispatch + catch-up)
#
# Layer 1: outer `timeout 600` (caller responsibility, not enforced here —
#   invocation in PACKS.md must wrap with `timeout 600 bash …`)
# Layer 2: pytest's per-test timeout (none configured; tests self-bound)
set -euo pipefail
cd "$(dirname "$0")/../.."
exec .venv/bin/python -m pytest \
  tests/test_scheduler_adapter.py \
  tests/test_scheduler_api.py \
  tests/integration/test_scheduled_tasks_e2e.py \
  -m "integration or not integration" \
  -q -rA
```

**PACKS.md append (frozen for implementer):**

```markdown
## In-flight commission — SCHEDULED TASKS (phases 3/4/5 acceptance, report-only)

Branch `feature/scheduled-tasks` @ **`<pending>`** (base `<pending>`). **VERDICT: TBD pending phase-1/2/3/4 merge.** Pack skeleton authored; merge gate is the green run after phase-1 (adapter semantics), phase-2 (service surface), phase-3 (REST endpoints), and phase-4 (registration) all land.

| Pack | Invocation | Scope | Result |
|---|---|---|---|
| `scheduled_tasks_acceptance` | `timeout 600 bash tests/packs/scheduled_tasks_acceptance.sh` | adapter unit + REST API + integration (TZ, DST, catch-up, idempotency, happy path, cancel-prevent-dispatch) | TBD |
```

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | DST test cases (`test_dst_spring_forward_gap_cron`, `test_dst_fall_back_ambiguity_cron`, `test_dst_spring_forward_gap_one_shot_anchor`, `test_dst_fall_back_ambiguity_one_shot_anchor`) — 4 cases, NO `pytest.skip`. Each test's docstring cites `phase1-plan.md` DST section + `daemon.utils.tz.anchor_local_to_utc` (architecture §4.2). Two paths (cron + one-shot anchor) are parameterized; tests assert both paths can't drift. | Low | High | Architecture-pinned semantics (croniter-default for cron, anchor_local_to_utc fold=0/gap-shift for one-shot). Tests assert documented behavior; deviation requires an ADR amendment. |
| 2 | Idempotency tests (`test_no_double_dispatch_after_restart`, `test_cron_not_affected_by_idempotency_key`, `test_5s_retry_collapse`) require phase-1's idempotency fix to be implemented. | High | Low | Architecture-pinned: key value source = `self._run_at.isoformat()`, gate = `SCHEDULE_TYPE_ONE_TIME`. Tests assert pinned behavior. NO `pytest.skip` — pack is the merge gate. |
| 3 | Integration tests require phase-2's `scheduling_service` to be importable and the harness to support it. | High | Low | Pack is the merge gate (architecture §8 item 16). Phase-2 service lands BEFORE phase-5 pack merges. NO `@pytest.mark.xfail` parking; tests assert pinned behavior. |
| 4 | `clean_env` autouse may interfere with `ENSEMBLE_SCHEDULING_DEFAULT_TZ` test (Task 1.1) if the env var is set in the parent shell and survives into the test | Low | Low | Use `monkeypatch.setenv` + `monkeypatch.delenv` per test; the `clean_env` autouse is per-test, so monkeypatch restoration is handled. |
| 5 | Pack may exceed 600s if integration tests block — the outer `timeout 600` is caller responsibility | Low | Low | Document in the PACKS.md row + the pack header comment that the invocation MUST wrap with `timeout 600`. The pack itself does NOT enforce (house posture: two-layer timeout, outer is caller). |
| 6 | `tests/test_scheduler_api.py` `mock_manager` fixture (`:22-40`) doesn't yet mock `manager.scheduling_service` — adding tests for the new endpoints requires extending the fixture | Medium | High | Update the existing `mock_manager` fixture to add `manager.scheduling_service` as a MagicMock (or AsyncMock) with `create_schedule`/`cancel_schedule`/`get_schedule` as AsyncMocks defaulting to a `NotImplementedError` (so existing tests still pass). The new test classes override per-test. |
| 7 | `tests/integration/conftest.py` may not expose `manager.job_queue_service.repository.get_by_source` or `manager.task_repository.get_by_work_id` — methods used by the integration test skeleton | Medium | Medium | The implementer verifies these helpers exist before merging; if absent, the harness is extended in this phase (NOT in a separate harness PR). Document the extension in the test docstring. |
| 8 | DST tests may behave differently in CI vs local (different host-local timezone auto-detect) | Medium | Low | Tests in Task 1.1 always set `monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TZ", ...)` to remove host-local dependency; tests in Task 1.2 explicitly pass `timezone=` to the adapter config. CI is timezone-agnostic. |

## Acceptance Criteria

### Unit tests
- [ ] `TestTzResolution` (Task 1.1) — 4 test cases (explicit, env-default, host-local, **terminal `datetime.timezone.utc` fallback**) all PASS in CI
- [ ] `TestDstSemantics` (Task 1.2) — 4 test cases (cron spring-forward, cron fall-back, one-shot anchor spring-forward, one-shot anchor fall-back) all PASS; **no `pytest.skip`**
- [ ] `TestCatchUpSemantics` (Task 1.3) — `test_one_shot_within_cap_fires_late` PASSES; `test_one_shot_beyond_cap_no_dispatch_with_reason` is covered by Task 3.3 integration (not skipped)
- [ ] `TestIdempotencyRestart` (Task 1.4) — 3 test cases PASS: `test_no_double_dispatch_after_restart`, `test_cron_not_affected_by_idempotency_key`, `test_5s_retry_collapse`. **No `pytest.skip`**
- [ ] NO edits to `tests/conftest.py` (`ENSEMBLE_SCHEDULING_*` is auto-covered by `_TRACKED_ENV_PREFIXES`)
- [ ] All existing classes in `tests/test_scheduler_adapter.py` remain GREEN (1564L baseline)
- [ ] `tests/unit/test_tz_resolver.py::TestAnchorLocalToUtc` (NEW in phase-1 §Task 9) covers 5 cases (aware-trust, fold=0, gap-shift+warning, UTC, non-DST-date) — passes in CI

### API tests
- [ ] `TestCreateSchedule` (Task 2.1) — 6 test cases PASS: 201-valid, 503-paused, 409-duplicate, 422-instance_mode, 422-missing-field, response-both-timezones
- [ ] `TestGetScheduleById` (Task 2.2) — 5 test cases PASS: 200-existing, 404-missing, 400-non-scheduler, both-local+utc, cancelled_at-null-vs-string
- [ ] `TestCancelSchedule` (Task 2.3) — 7 test cases PASS: 200-existing, 503-paused, 404-missing, 400-non-scheduler, response-shape, **`delete_source_config.assert_not_called()` PASSES** (history preservation), cancel-by-label
- [ ] `TestScheduleListFilter` (phase-3 §Task 4.1, `tests/test_scheduler_api.py`) — 3 test cases PASS: `default_excludes_cancelled`, `?include_cancelled=true_returns_cancelled`, `?include_cancelled=false_excludes_cancelled`. Server-side SQL filter; `GET /schedules/{id}` (Task 2.2) DOES return cancelled rows — pinned via `TestGetScheduleById` cancelled-row case.
- [ ] `TestScheduleEdgeCases` (Task 2.4) — 3 test cases PASS: past-due-kickoff, idempotent-cancel, cancelled-get-shows-cancelled_at. No skip.
- [ ] `mock_manager` fixture extended to provide `manager.scheduling_service` MagicMock (Task Risk #6)
- [ ] All existing classes in `tests/test_scheduler_api.py` remain GREEN (1068L baseline)

### Integration tests
- [ ] `test_one_shot_schedule_creates_job_item` (Task 3.1) — PASSES, asserts single-uuid contract `JobItem.job_id == Task.work_id`
- [ ] `test_cancelled_schedule_never_dispatches` (Task 3.2) — PASSES, asserts NO `JobItem` after cancel; `schedule_executions` history queryable
- [ ] `test_one_shot_beyond_cap_no_dispatch_with_reason` (Task 3.3) — PASSES, asserts `schedule_executions` row with `status='skipped'` + reason
- [ ] All integration tests carry `@pytest.mark.integration` (Task 3.4); excluded by default addopts

### Pack
- [ ] `tests/packs/scheduled_tasks_acceptance.sh` exists, is executable (`chmod +x`), 15 lines or fewer, follows the `g7_unique_index_smoke_test.sh` format
- [ ] Pack invocation in PACKS.md wraps with `timeout 600`
- [ ] `.agents/tester/PACKS.md` has the new `## Scheduled Tasks Acceptance` section with the verdict paragraph + `| Pack | Invocation | Scope | Result |` table row
- [ ] Pack exits 0 when all suites are green; exits non-zero on any failure

### Dependency gates (architecture §8 item 16 — pack is the merge gate, NO xfail/skip parking)
- [ ] DST tests assert architecture-pinned semantics (croniter-default for cron, anchor_local_to_utc fold=0/gap-shift for one-shot) — NO `pytest.skip`
- [ ] Idempotency tests assert architecture-pinned mechanism (key from `self._run_at`, gated to `SCHEDULE_TYPE_ONE_TIME`) — NO `pytest.skip`
- [ ] Integration tests: phase-2 service lands BEFORE phase-5 pack; pack is the merge gate; no `@pytest.mark.xfail`
- [ ] API tests: extend `mock_manager` fixture; the extension is INERT (no behavior change) for existing tests
- [ ] Phases 2 and 4 land as ONE PR (architecture §8 item 16); phase-5 pack is the CI gate for the merged PR

### Existing-suite regression
- [ ] `tests/test_scheduler_adapter.py` — all 1564L baseline tests remain GREEN
- [ ] `tests/test_scheduler_api.py` — all 1068L baseline tests remain GREEN
- [ ] `tests/test_scheduler_instance_mode.py` — all 1288L baseline tests remain GREEN (NOT modified by this phase)
- [ ] `tests/conftest.py` — UNCHANGED (verified: `_TRACKED_ENV_PREFIXES` covers `ENSEMBLE_SCHEDULING_*`)