"""P1 engine spec + registration pins (Engine Arc Phase 1 — T1.7/T1.8/T1.10).

FILE NAME per ``test-strategy.md`` §1.1 T1 (``tests/unit/services/
test_engine_jobspec_registration.py``) — the reconciliation rule in
phase1-plan §12.1 gives the test-strategy file names precedence over the
phase plan's indicative paths.

Covers acceptance gates:

* **AC4 (dormant default)** — ``EngineConfig`` resolves from
  ``ENSEMBLE_ENGINE_*``; an absent yaml section leaves pydantic defaults
  ``enabled=True`` / ``shadow_mode=True``; env wins on a present key.
* **AC5 (validation + registration contract)** — each ``EngineSpecError``
  code fires on its trigger input; a valid spec round-trips
  ``upsert_job`` → ``get_job`` with ``version == 1``; identical
  re-registration echoes ``version == 1``; changed content yields
  ``version == 2`` with ``updated_at`` moving.
* **AC6 (self-describing rows)** — every row carries NON-NULL
  ``service_tag`` and ``category``; ``module_info`` / ``description`` /
  ``metadata`` accept a 5-key dict without schema change.
* **AC7 (zero execution)** — no ``execute_fn`` / dispatch / tick-loop
  symbol exists anywhere in the P1 engine tree.
* **T1.10 (TriggerSpec seam)** — ``EventTrigger`` is a pure data spec:
  it names no callback and imports nothing from the event registry or the
  execution core.

Fence discipline: in-memory SQLite engines only (FILE-BACKED where a
second session must see the same data — ``StaticPool`` keeps one shared
connection so sequential ``Session`` blocks in one process see each
other); no daemon boot; no PostgreSQL connection.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.engine.repository import EngineRepository
from daemon.services.engine import estate, registry as registry_mod, spec as spec_mod
from daemon.services.engine.registry import EngineRegistry
from daemon.services.engine.spec import (
    CATEGORY_STARTER_VOCABULARY,
    CONCURRENCY_CLASSES,
    EngineSpecError,
    EventTrigger,
    IntervalTrigger,
    JobSpec,
    ServiceMeta,
    TIMEOUT_POLICIES,
    validate_spec,
)

ENGINE_DIR = Path(__file__).resolve().parents[3] / "daemon" / "services" / "engine"


# ── fixtures / helpers ─────────────────────────────────────────────────


def _engine():
    """A throwaway in-memory SQLite engine with both engine tables."""
    # StaticPool shares ONE connection so every ``Session`` block in this
    # process sees the same in-memory database.
    from sqlmodel.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _meta(**overrides):
    base = dict(
        service_tag="orphan-watcher-sweep",
        category="reconciler",
        service_name="Orphan Watcher Sweep",
        module_info="daemon.services.orphan_watcher_sweep",
        description="Reconcile orphaned watcher rows.",
        metadata={"owner": "a", "docs": "b", "slo": "c", "notes": "d", "x": "e"},
    )
    base.update(overrides)
    return ServiceMeta(**base)


def _spec(**overrides):
    base = dict(
        id="orphan-watcher",
        meta=_meta(),
        trigger=IntervalTrigger(seconds=90),
        max_runtime_s=60.0,
        timeout_policy="cancel",
        concurrency_class="DB_OFFLOAD",
        priority=5,
    )
    base.update(overrides)
    return JobSpec(**base)


# ── AC5: the 12 validation rules, one code per rule ────────────────────


class TestSpecValidation:
    def test_valid_spec_passes(self):
        validate_spec(_spec())  # no raise

    def test_spec_is_frozen(self):
        s = _spec()
        with pytest.raises(Exception):
            s.id = "other"  # type: ignore[misc]

    def test_service_meta_is_frozen(self):
        m = _meta()
        with pytest.raises(Exception):
            m.service_tag = "other"  # type: ignore[misc]

    def test_spec_id_rejects_empty(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(id=""))
        assert ei.value.code == "SPEC_ID"

    def test_spec_id_rejects_whitespace(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(id="orphan watcher"))
        assert ei.value.code == "SPEC_ID"

    def test_service_tag_required(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(meta=_meta(service_tag="")))
        assert ei.value.code == "SPEC_SERVICE_TAG"

    def test_category_required(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(meta=_meta(category="")))
        assert ei.value.code == "SPEC_CATEGORY"

    def test_interval_seconds_must_be_at_least_one(self):
        for bad in (0, 0.5, -3):
            with pytest.raises(EngineSpecError) as ei:
                validate_spec(_spec(trigger=IntervalTrigger(seconds=bad)))
            assert ei.value.code == "SPEC_INTERVAL"

    def test_interval_boundary_one_second_is_accepted(self):
        validate_spec(_spec(trigger=IntervalTrigger(seconds=1)))

    def test_max_runtime_must_be_positive(self):
        for bad in (0, -1.0):
            with pytest.raises(EngineSpecError) as ei:
                validate_spec(_spec(max_runtime_s=bad))
            assert ei.value.code == "SPEC_MAX_RUNTIME"

    def test_priority_bounds(self):
        for bad in (-1, 10, 99):
            with pytest.raises(EngineSpecError) as ei:
                validate_spec(_spec(priority=bad))
            assert ei.value.code == "SPEC_PRIORITY"
        for ok in (0, 5, 9):
            validate_spec(_spec(priority=ok))

    def test_priority_rejects_bool(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(priority=True))
        assert ei.value.code == "SPEC_PRIORITY"

    def test_jitter_must_be_non_negative(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(trigger=IntervalTrigger(seconds=90, jitter_seconds=-1)))
        assert ei.value.code == "SPEC_JITTER"

    def test_jitter_boundary_zero_is_accepted(self):
        validate_spec(_spec(trigger=IntervalTrigger(seconds=90, jitter_seconds=0)))

    # ── FOLD-1: non-finite numbers are rejected on EVERY numeric rule ──
    #
    # NaN is False against EVERY comparison, so a bare ``x < 1`` admits it;
    # +Inf passes any ``> 0``. These pins exist because the guards are
    # negated finite predicates, not because the ranges changed.

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_interval_seconds_rejects_non_finite(self, bad):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(trigger=IntervalTrigger(seconds=bad)))
        assert ei.value.code == "SPEC_INTERVAL"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_jitter_seconds_rejects_non_finite(self, bad):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(
                _spec(trigger=IntervalTrigger(seconds=90, jitter_seconds=bad))
            )
        assert ei.value.code == "SPEC_JITTER"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_first_tick_offset_seconds_rejects_non_finite(self, bad):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(
                _spec(
                    trigger=IntervalTrigger(
                        seconds=90, first_tick_offset_seconds=bad
                    )
                )
            )
        assert ei.value.code == "SPEC_INTERVAL"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_max_runtime_rejects_non_finite(self, bad):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(max_runtime_s=bad))
        assert ei.value.code == "SPEC_MAX_RUNTIME"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    @pytest.mark.parametrize(
        "field", ["retry_backoff_base_s", "retry_backoff_cap_s"]
    )
    def test_retry_backoff_rejects_non_finite(self, field, bad):
        """A non-finite backoff hangs ``run_with_retry``'s sleep forever."""
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(**{field: bad}))
        assert ei.value.code == "SPEC_RETRY"

    def test_finite_backoff_values_are_accepted(self):
        validate_spec(_spec(retry_backoff_base_s=0.0, retry_backoff_cap_s=5.0))
        validate_spec(_spec(retry_backoff_base_s=None, retry_backoff_cap_s=None))

    def test_negative_backoff_is_rejected(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(retry_backoff_base_s=-1.0))
        assert ei.value.code == "SPEC_RETRY"

    def test_first_tick_kind_restricted(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(
                _spec(trigger=IntervalTrigger(seconds=90, first_tick_kind="whenever"))
            )
        assert ei.value.code == "SPEC_INTERVAL"
        for ok in ("immediate", "delayed", None):
            validate_spec(_spec(trigger=IntervalTrigger(seconds=90, first_tick_kind=ok)))

    def test_event_trigger_kind_must_be_non_empty(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(trigger=EventTrigger(kind="")))
        assert ei.value.code == "SPEC_EVENT"

    def test_trigger_must_be_a_known_spec_type(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(trigger="interval"))  # type: ignore[arg-type]
        assert ei.value.code == "SPEC_TRIGGER_COUNT"

    def test_timeout_policy_closed_set(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(timeout_policy="ignore"))
        assert ei.value.code == "SPEC_TIMEOUT_POLICY"
        for ok in TIMEOUT_POLICIES:
            validate_spec(_spec(timeout_policy=ok))

    def test_concurrency_class_closed_set(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(concurrency_class="MAIN"))
        assert ei.value.code == "SPEC_CONCURRENCY"
        for ok in CONCURRENCY_CLASSES:
            validate_spec(_spec(concurrency_class=ok))

    def test_error_carries_job_id(self):
        with pytest.raises(EngineSpecError) as ei:
            validate_spec(_spec(max_runtime_s=0))
        assert ei.value.job_id == "orphan-watcher"
        assert ei.value.code in str(ei.value)

    def test_all_typed_codes_are_distinct(self):
        """The production error-code literals in spec.py are the documented set.

        This reads the REAL ``raise EngineSpecError("SPEC_...")`` literals out
        of spec.py so a forgotten/renamed/duplicated code REDs here — a
        literal set compared only against itself (the old shape) could never
        fail.

        The set grew by ONE code in the stage-review pass: ``SPEC_RETRY``
        (the finite-number guard for ``retry_backoff_base_s`` /
        ``retry_backoff_cap_s``). D1.12 carried no code for the backoff
        pair — the new code is reported, not smuggled in under an existing
        one (a non-finite backoff hangs ``run_with_retry``'s sleep, which is
        a different failure than a bad cadence or a bad runtime bound).
        """
        import re

        src = inspect.getsource(spec_mod)
        production = set(re.findall(r'"(SPEC_[A-Z_]+)"', src))
        assert production == {
            "SPEC_ID",
            "SPEC_SERVICE_TAG",
            "SPEC_CATEGORY",
            "SPEC_INTERVAL",
            "SPEC_MAX_RUNTIME",
            "SPEC_PRIORITY",
            "SPEC_JITTER",
            "SPEC_TRIGGER_COUNT",
            "SPEC_EVENT",
            "SPEC_TIMEOUT_POLICY",
            "SPEC_CONCURRENCY",
            "SPEC_RETRY",
        }


# ── AC6: self-describing metadata ──────────────────────────────────────


class TestSelfDescribingMeta:
    def test_metrics_labels_are_service_tag_plus_category(self):
        s = _spec()
        assert s.metrics_labels == {
            "service_tag": "orphan-watcher-sweep",
            "category": "reconciler",
        }

    def test_starter_vocabulary_is_documented_and_open(self):
        assert set(CATEGORY_STARTER_VOCABULARY) == {
            "reconciler",
            "recovery_sweep",
            "probe",
            "telemetry",
            "realtime_guard",
            "event_consumer",
            "engine_self",
            "housekeeping",
        }
        # OPEN vocabulary: an unlisted category is ACCEPTED (label
        # discipline, not a gate).
        validate_spec(_spec(meta=_meta(category="brand_new_class")))

    def test_row_projection_carries_every_addendum_field(self):
        row = _spec().to_row_dict()
        assert row["service_tag"] == "orphan-watcher-sweep"
        assert row["category"] == "reconciler"
        assert row["module_info"] == "daemon.services.orphan_watcher_sweep"
        assert row["description"]
        # The JSON blob key is the MODEL ATTRIBUTE (``job_metadata``); the
        # DDL column is ``metadata``.
        assert row["job_metadata"] == {
            "owner": "a", "docs": "b", "slo": "c", "notes": "d", "x": "e",
        }

    def test_five_key_metadata_dict_round_trips(self):
        engine = _engine()
        repo = EngineRepository(engine)
        reg = EngineRegistry(repo)
        reg.register(_spec())
        row = repo.get_job("orphan-watcher")
        assert row is not None
        assert row.service_tag == "orphan-watcher-sweep"
        assert row.category == "reconciler"
        assert row.job_metadata is not None
        assert len(row.job_metadata) == 5

    def test_trigger_projection_interval(self):
        row = _spec(
            trigger=IntervalTrigger(
                seconds=90, jitter_seconds=5, first_tick_kind="delayed",
                first_tick_offset_seconds=30,
            )
        ).to_row_dict()
        assert row["trigger_kind"] == "interval"
        assert row["interval_seconds"] == 90
        assert row["jitter_seconds"] == 5
        assert row["first_tick_kind"] == "delayed"
        assert row["first_tick_offset_seconds"] == 30
        assert "event_kind" not in row

    def test_trigger_projection_event(self):
        row = _spec(trigger=EventTrigger(kind="instance_terminal", filter={"k": 1})).to_row_dict()
        assert row["trigger_kind"] == "event"
        assert row["event_kind"] == "instance_terminal"
        assert row["event_filter"] == {"k": 1}
        assert "interval_seconds" not in row


# ── T1.10: the TriggerSpec seam is DATA ONLY ───────────────────────────


class TestTriggerSpecSeam:
    def test_event_trigger_carries_no_callback(self):
        fields = set(EventTrigger.__dataclass_fields__)
        assert fields == {"kind", "filter"}
        for forbidden in ("callback", "execute_fn", "on_event", "handler"):
            assert forbidden not in fields

    def test_event_trigger_trigger_kind_is_the_discriminator(self):
        t = EventTrigger(kind="instance_terminal")
        assert t.trigger_kind == "event"
        assert t.kind == "instance_terminal"  # the EVENT kind, not the trigger kind

    def test_interval_trigger_trigger_kind(self):
        assert IntervalTrigger(seconds=90).trigger_kind == "interval"

    def test_spec_module_imports_nothing_from_the_registry_or_execution_core(self):
        src = inspect.getsource(spec_mod)
        for forbidden in (
            "daemon.services.event_registry",
            "daemon.services.engine.execution",
            "daemon.services.engine.dispatch",
        ):
            assert forbidden not in src, f"spec.py must not import {forbidden}"


# ── AC5: the registration contract ─────────────────────────────────────


class TestRegistrationContract:
    def test_first_registration_stores_version_one(self):
        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)
        assert reg.register(_spec()) == 1
        row = repo.get_job("orphan-watcher")
        assert row is not None and row.version == 1

    def test_identical_reregistration_is_a_noop_echo(self):
        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)
        spec = _spec()
        assert reg.register(spec) == 1
        first = repo.get_job("orphan-watcher")
        assert reg.register(spec) == 1
        second = repo.get_job("orphan-watcher")
        assert second.version == 1
        assert second.updated_at == first.updated_at  # NO churn at all

    def test_db_grounded_echo_across_registry_instances(self):
        """D-2: a FRESH registry (empty RAM cache) must echo, not bump.

        Simulates a restart: register via one instance, then register the
        identical spec through a brand-new instance over the same DB. The
        echo must be grounded on the DB row (D1.5), so version stays 1 and
        updated_at does not move. A changed spec then still bumps to 2.
        """
        engine = _engine()
        repo = EngineRepository(engine)
        reg_a = EngineRegistry(repo)
        spec = _spec()
        assert reg_a.register(spec) == 1
        first = repo.get_job("orphan-watcher")
        first_updated = first.updated_at

        reg_b = EngineRegistry(repo)  # fresh instance — empty cache
        assert reg_b.cached_version("orphan-watcher") is None
        assert reg_b.register(spec) == 1  # DB-grounded echo
        second = repo.get_job("orphan-watcher")
        assert second.version == 1
        assert second.updated_at == first_updated  # no updated_at churn

        # A genuinely changed spec still bumps through the fresh registry.
        assert reg_b.register(_spec(trigger=IntervalTrigger(seconds=120))) == 2
        third = repo.get_job("orphan-watcher")
        assert third.version == 2

    def test_db_grounded_echo_normalizes_json_key_order(self):
        """D-2: JSON metadata/event_filter key order cannot fake a bump."""
        engine = _engine()
        repo = EngineRepository(engine)
        reg_a = EngineRegistry(repo)
        reg_a.register(
            _spec(meta=_meta(metadata={"owner": "a", "docs": "b", "slo": "c"}))
        )
        # Same content, different insertion order, fresh registry (DB path).
        reg_b = EngineRegistry(repo)
        reordered = _spec(
            meta=_meta(metadata={"slo": "c", "docs": "b", "owner": "a"})
        )
        assert reg_b.register(reordered) == 1
        assert repo.get_job("orphan-watcher").version == 1

    def test_changed_spec_bumps_version_and_moves_updated_at(self):
        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)
        reg.register(_spec())
        before = repo.get_job("orphan-watcher")
        changed = _spec(trigger=IntervalTrigger(seconds=120))
        assert reg.register(changed) == 2
        after = repo.get_job("orphan-watcher")
        assert after.version == 2
        assert after.updated_at >= before.updated_at
        assert after.registered_at == before.registered_at  # never moves

    def test_invalid_spec_writes_nothing(self):
        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)
        with pytest.raises(EngineSpecError):
            reg.register(_spec(max_runtime_s=0))
        assert repo.get_job("orphan-watcher") is None
        assert reg.cached_ids == ()

    def test_validation_precedes_any_db_write(self):
        """The registry must validate BEFORE constructing an EngineJob."""
        src = inspect.getsource(registry_mod.EngineRegistry._register_sync)
        assert "upsert_job" in src
        # Both entry points validate first (source-order proof).
        for entry in ("aregister", "register"):
            entry_src = inspect.getsource(getattr(EngineRegistry, entry))
            assert entry_src.index("validate_spec") < entry_src.index(
                "_register_sync"
            ), entry

    def test_cache_tracks_ids_and_versions(self):
        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)
        reg.register(_spec())
        reg.register(_spec(id="sources-gc", meta=_meta(service_tag="sources-gc")))
        assert reg.cached_ids == ("orphan-watcher", "sources-gc")
        assert reg.cached_version("orphan-watcher") == 1
        assert reg.cached_version("nope") is None
        assert reg.cached_spec("orphan-watcher") is not None

    def test_reregistration_preserves_run_history(self):
        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)
        reg.register(_spec())
        repo.stamp_job_outcome(
            "orphan-watcher",
            last_outcome="SKIPPED_SHADOW",
            last_skip_reason="shadow",
            consecutive_skips=3,
        )
        reg.register(_spec(trigger=IntervalTrigger(seconds=120)))
        row = repo.get_job("orphan-watcher")
        assert row.version == 2
        assert row.last_outcome == "SKIPPED_SHADOW"
        assert row.consecutive_skips == 3

    def test_event_trigger_spec_registers(self):
        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)
        spec = _spec(
            id="event-consumer",
            meta=_meta(service_tag="event-consumer", category="event_consumer"),
            trigger=EventTrigger(kind="instance_terminal", filter={"k": "v"}),
        )
        assert reg.register(spec) == 1
        row = repo.get_job("event-consumer")
        assert row.trigger_kind == "event"
        assert row.event_kind == "instance_terminal"

    def test_sync_register_refuses_on_a_running_loop(self):
        import asyncio

        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)

        async def call_sync():
            with pytest.raises(RuntimeError) as ei:
                reg.register(_spec())
            return str(ei.value)

        msg = asyncio.run(call_sync())
        assert "aregister" in msg

    def test_aregister_is_the_native_coroutine_path(self):
        import asyncio

        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)

        async def go():
            return await reg.aregister(_spec())

        assert asyncio.run(go()) == 1
        assert asyncio.run(go()) == 1  # echo on the async path too


# ── repository closed surface ──────────────────────────────────────────


class TestRepositorySurface:
    def test_closed_surface_only(self):
        public = {
            name
            for name, value in inspect.getmembers(
                EngineRepository, predicate=inspect.isfunction
            )
            if not name.startswith("_")
        }
        assert public == {
            "upsert_job",
            "get_job",
            "list_jobs",
            "append_run",
            "stamp_job_outcome",
        }

    def test_every_method_is_sync(self):
        for name in ("upsert_job", "get_job", "list_jobs", "append_run",
                     "stamp_job_outcome"):
            assert not inspect.iscoroutinefunction(
                getattr(EngineRepository, name)
            ), f"{name} must be sync (callers off-load)"

    def test_list_jobs_filters(self):
        repo = EngineRepository(_engine())
        reg = EngineRegistry(repo)
        reg.register(_spec())
        reg.register(
            _spec(id="tmp-image", meta=_meta(service_tag="tmp-image-cleanup",
                                            category="housekeeping"))
        )
        assert {j.id for j in repo.list_jobs()} == {"orphan-watcher", "tmp-image"}
        assert {j.id for j in repo.list_jobs(category="housekeeping")} == {"tmp-image"}
        assert {j.id for j in repo.list_jobs(service_tag="orphan-watcher-sweep")} == {
            "orphan-watcher"
        }

    def test_stamp_job_outcome_returns_none_for_unknown(self):
        repo = EngineRepository(_engine())
        assert (
            repo.stamp_job_outcome("nope", last_outcome="SUCCEEDED") is None
        )


# ── AC4: EngineConfig resolution ───────────────────────────────────────


class TestEngineConfig:
    def test_defaults_are_the_dormant_posture(self, monkeypatch):
        from daemon.config import EngineConfig

        for name in list(__import__("os").environ):
            if name.startswith("ENSEMBLE_ENGINE_"):
                monkeypatch.delenv(name, raising=False)
        cfg = EngineConfig()
        assert cfg.enabled is True
        assert cfg.shadow_mode is True

    def test_every_field_resolves_from_the_engine_env_prefix(self, monkeypatch):
        from daemon.config import EngineConfig

        monkeypatch.setenv("ENSEMBLE_ENGINE_ENABLED", "false")
        monkeypatch.setenv("ENSEMBLE_ENGINE_SHADOW_MODE", "false")
        monkeypatch.setenv("ENSEMBLE_ENGINE_TICK_RESOLUTION_SECONDS", "2.5")
        monkeypatch.setenv("ENSEMBLE_ENGINE_TICK_WORKERS", "4")
        monkeypatch.setenv("ENSEMBLE_ENGINE_WATCHDOG_INTERVAL_SECONDS", "7.5")
        monkeypatch.setenv("ENSEMBLE_ENGINE_BOOT_MAX_WINDOW_SECONDS", "90")
        monkeypatch.setenv("ENSEMBLE_ENGINE_BOOT_SWEEP_DEADLINE_SECONDS", "11")
        monkeypatch.setenv("ENSEMBLE_ENGINE_BOOT_PASS_DEADLINE_SECONDS", "22")
        monkeypatch.setenv("ENSEMBLE_ENGINE_RUN_RETENTION_DAYS", "7")
        cfg = EngineConfig()
        assert cfg.enabled is False
        assert cfg.shadow_mode is False
        assert cfg.tick_resolution_seconds == 2.5
        assert cfg.tick_workers == 4
        assert cfg.watchdog_interval_seconds == 7.5
        assert cfg.boot_max_window_seconds == 90
        assert cfg.boot_sweep_deadline_seconds == 11
        assert cfg.boot_pass_deadline_seconds == 22
        assert cfg.run_retention_days == 7

    def test_config_mounts_engine_peer_of_notify_user(self):
        from daemon.config import Config, EngineConfig, NotifyUserConfig

        assert "engine" in Config.model_fields
        assert Config.model_fields["engine"].default_factory is EngineConfig
        assert "notify_user" in Config.model_fields
        assert Config.model_fields["notify_user"].default_factory is NotifyUserConfig

    def test_yaml_section_is_honored_and_env_still_wins(self, tmp_path, monkeypatch):
        from daemon.config import load_config

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            """
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "test-key"
  model: "gpt-4"
daemon:
  host: "127.0.0.1"
  port: 8079
agents:
  directory: "./agents"
engine:
  tick_workers: 9
  run_retention_days: 14
"""
        )
        monkeypatch.delenv("ENSEMBLE_ENGINE_TICK_WORKERS", raising=False)
        monkeypatch.setenv("ENSEMBLE_ENGINE_RUN_RETENTION_DAYS", "3")
        cfg = load_config(str(cfg_file))
        assert cfg.engine.tick_workers == 9  # yaml honored
        assert cfg.engine.run_retention_days == 3  # env wins on a present key

    def test_absent_yaml_section_leaves_pydantic_defaults(self, tmp_path, monkeypatch):
        from daemon.config import load_config

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            """
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "test-key"
  model: "gpt-4"
daemon:
  host: "127.0.0.1"
  port: 8079
agents:
  directory: "./agents"
"""
        )
        for name in list(__import__("os").environ):
            if name.startswith("ENSEMBLE_ENGINE_"):
                monkeypatch.delenv(name, raising=False)
        cfg = load_config(str(cfg_file))
        assert cfg.engine.enabled is True
        assert cfg.engine.shadow_mode is True

    def test_shipped_config_yaml_carries_the_engine_section(self):
        repo_root = Path(__file__).resolve().parents[3]
        text = (repo_root / "config.yaml").read_text()
        assert "\nengine:\n" in text
        assert "shadow_mode:" in text
        assert "enabled:" in text


# ── AC7: ZERO EXECUTION in the P1 SUBSTRATE ────────────────────────────


#: The four P1 substrate modules. Phase 2 legitimately added execution
#: modules to the same package, so an engine-DIRECTORY-wide scan would be
#: structurally RED now — the invariant is re-scoped to the substrate that
#: must stay execution-free FOREVER, not to the directory.
SUBSTRATE_MODULES = ("__init__.py", "spec.py", "registry.py", "estate.py")

#: Machine-readable execution tokens (prose that NAMES the forbidden thing
#: while documenting the dormancy gate is not a call path — docstrings are
#: stripped before the scan so the fence pin does not forbid the word while
#: prohibiting the mechanism).
FORBIDDEN_EXECUTION_TOKENS = (
    "execute_fn",          # the executable slot does not exist in P1
    "run_with_timeout",    # P2's timeout wrapper
    "asyncio.sleep",       # a tick loop's cadence sleep
    "create_task",         # a background loop
    "run_forever",
    "dispatch_due",
    "_stopping",           # the loop sentinel (P2's Templates)
)


def _tracked_substrate_paths() -> list[Path]:
    """The substrate four, resolved through TRACKED files only.

    Enumerated via ``git ls-files`` so an UNTRACKED file dropped into the
    engine directory (another lane's WIP, a scratch module) can never
    affect this scan and can never make the pin unreproducible outside
    this particular working tree. Sorted for determinism.
    """
    import subprocess

    repo_root = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        ["git", "ls-files", "--", "daemon/services/engine"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"git ls-files failed ({proc.returncode}) — the tracked-scoped "
        f"substrate scan cannot be reproduced: {proc.stderr.strip()}"
    )
    tracked_names = {
        line.rsplit("/", 1)[-1]
        for line in proc.stdout.splitlines()
        if line.strip().endswith(".py")
    }
    missing = [name for name in SUBSTRATE_MODULES if name not in tracked_names]
    assert not missing, (
        f"the P1 substrate modules are not all TRACKED in git: {missing} "
        f"(tracked .py under daemon/services/engine: "
        f"{sorted(tracked_names)})"
    )
    return [ENGINE_DIR / name for name in SUBSTRATE_MODULES]


def _code_only(src: str) -> str:
    """Strip docstrings so prose naming a forbidden token stays legal."""
    import ast

    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                first.value.value = ""
    return ast.unparse(tree)


class TestZeroExecution:
    """AC7 — the P1 substrate is execution-free, FOREVER.

    Structural ruling (stage review, FOLD-2): the two P1 pins removed by
    P2 (``test_no_execution_symbols_in_the_engine_tree`` /
    ``test_no_tick_loop_or_dispatch_module_exists``) are RESTORED,
    re-scoped — the universe is exactly the four substrate modules,
    resolved through TRACKED files only. P2's execution modules are
    sanctioned and neither test says anything about them.
    """

    def test_no_execution_symbols_in_the_substrate_modules(self):
        """No ``execute_fn`` / dispatch / tick-loop token in the substrate.

        Scanned set = :data:`SUBSTRATE_MODULES` only, tracked-file
        enumerated (:func:`_tracked_substrate_paths`). Docstrings are
        stripped first, so the module prose that DOCUMENTS the dormancy
        gate does not trip the pin.
        """
        for path in _tracked_substrate_paths():
            code = _code_only(path.read_text())
            for token in FORBIDDEN_EXECUTION_TOKENS:
                assert token not in code, (
                    f"{path.name} carries execution token {token}"
                )

    def test_substrate_modules_are_data_and_contract_modules_only(self):
        """The substrate four exist and none is a tick/dispatch module.

        Deliberately asserts NOTHING about any other file in the engine
        directory — P2's execution core lives there by design. What it
        pins is that the substrate itself never grows a second job: the
        four tracked modules are present and none of them carries an
        execution token.
        """
        paths = _tracked_substrate_paths()
        assert {p.name for p in paths} == set(SUBSTRATE_MODULES)
        for path in paths:
            assert path.is_file(), f"substrate module missing: {path}"
            code = _code_only(path.read_text())
            carried = [t for t in FORBIDDEN_EXECUTION_TOKENS if t in code]
            assert not carried, (
                f"{path.name} is a substrate module yet carries {carried} — "
                f"the substrate stays execution-free"
            )

    def test_api_py_is_untouched_by_this_phase(self):
        """AC7: ``daemon/api.py`` is UNCHANGED by Phase 1 (P4 owns the wiring).

        Proven by source: no engine symbol is imported anywhere in api.py.
        """
        api_py = Path(__file__).resolve().parents[3] / "daemon" / "api.py"
        src = api_py.read_text()
        for token in (
            "services.engine",
            "EngineRegistry",
            "engine_jobs",
            "engine_runs",
        ):
            assert token not in src, f"daemon/api.py must not reference {token}"


# ── AC9: the continuity table is DATA and matches the live knobs ───────


class TestContinuityTable:
    def test_twelve_rows(self):
        assert len(estate.CONTINUITY_JOBS) == 12

    def test_job_ids_are_unique(self):
        ids = [r.job_id for r in estate.CONTINUITY_JOBS]
        assert len(ids) == len(set(ids)) == 12

    def test_service_tags_are_unique(self):
        tags = [r.service_tag for r in estate.CONTINUITY_JOBS]
        assert len(tags) == len(set(tags)) == 12

    def test_every_services_attr_exists_with_the_recorded_default(self):
        """The drift pin (AC9): a knob default moving under the table REDs."""
        from daemon.config import ServicesConfig

        fields = ServicesConfig.model_fields
        for row in estate.CONTINUITY_JOBS:
            assert row.services_attr in fields, (
                f"{row.job_id}: services_attr {row.services_attr!r} is not a "
                f"ServicesConfig field"
            )
            live = fields[row.services_attr].default
            assert live == row.default_seconds, (
                f"{row.job_id}: {row.services_attr} default drifted — table "
                f"records {row.default_seconds}, live config says {live}"
            )

    def test_categories_come_from_the_starter_vocabulary(self):
        vocab = set(CATEGORY_STARTER_VOCABULARY)
        for row in estate.CONTINUITY_JOBS:
            assert row.category in vocab, (
                f"{row.job_id}: category {row.category!r} not in the "
                f"documented starter vocabulary"
            )

    def test_expected_knob_set(self):
        assert {r.services_attr for r in estate.CONTINUITY_JOBS} == {
            "watch_reconcile_sweep_interval_seconds",
            "mission_strand_heal_interval_seconds",
            "report_delivery_recovery_interval_seconds",
            "sources_gc_interval_seconds",
            "eligible_pending_sweep_interval_seconds",
            "orphan_watcher_sweep_interval_seconds",
            "job_lock_sweep_interval_seconds",
            "tmp_image_cleanup_interval_seconds",
            "plane_sync_watchdog_interval_seconds",
            "stale_pause_sweep_interval_seconds",
            "waiting_children_watchdog_interval_seconds",
            "drift_reconcile_interval_seconds",
        }

    def test_expected_defaults(self):
        got = {r.services_attr: r.default_seconds for r in estate.CONTINUITY_JOBS}
        assert got == {
            "watch_reconcile_sweep_interval_seconds": 300,
            "mission_strand_heal_interval_seconds": 300,
            "report_delivery_recovery_interval_seconds": 300,
            "sources_gc_interval_seconds": 3600,
            "eligible_pending_sweep_interval_seconds": 90,
            "orphan_watcher_sweep_interval_seconds": 90,
            "job_lock_sweep_interval_seconds": 90,
            "tmp_image_cleanup_interval_seconds": 3600,
            "plane_sync_watchdog_interval_seconds": 300,
            "stale_pause_sweep_interval_seconds": 300,
            "waiting_children_watchdog_interval_seconds": 3600,
            "drift_reconcile_interval_seconds": 300,
        }

    def test_estate_module_is_data_only(self):
        src = inspect.getsource(estate)
        for forbidden in ("register(", "EngineRegistry", "asyncio", "create_task"):
            assert forbidden not in src
