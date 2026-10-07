"""Tests for the slice-⑥ drift-event publisher + store (REC §1.2 comp 7).

Covers:

* :class:`~daemon.plugin_subsystem.drift_event_publisher.DriftEventRepository`
  — create-from-verbatim-payload, list-unresolved, resolve-deletes,
  latest-for-plugin (probe doc option (a) semantics: "resolution
  deletes the row"; "unresolved" == "row exists").
* :func:`~daemon.plugin_subsystem.sync_runner.emit_drift_event` —
  the ③ stub replaced by the publisher seam: log-only default,
  DB-backed when configured, signature stable, NEVER-RAISES.
* The module-level sink wiring (configure / get / reset).

Runs against an in-memory SQLite engine (the tests/services
conftest pattern, localized so the plugin_subsystem pack stays
self-contained).  Offline; no daemon boot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.plugin_subsystem import build_drift_event_payload, sync_runner as sr
from daemon.plugin_subsystem.drift_event_publisher import (
    DriftEvent,
    DriftEventPublisher,
    DriftEventRepository,
    LogOnlyDriftEventSink,
    configure_drift_event_publisher,
    emit_drift_event_safe,
    get_drift_event_publisher,
    reset_drift_event_publisher,
)


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def drift_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def drift_repo(drift_engine):
    return DriftEventRepository(drift_engine)


@pytest.fixture(autouse=True)
def _reset_sink():
    """Every test starts AND ends on the log-only default sink."""
    reset_drift_event_publisher()
    yield
    reset_drift_event_publisher()


def _payload(divergence_id: int = 5, plugin: str = "opendesign") -> Dict[str, Any]:
    entry = {
        "id": divergence_id,
        "files": ["prompts/contracts/od-next-intent-resolution.ts"],
        "delta": "+1 file in v0.24.1",
        "rationale": "Snapshot pulled at sync time; re-apply or drop per CON §2",
        "pinning_test": "tests/unit/plugin_subsystem/test_sync_runner.py::TestSyncSnapshotDrift",
    }
    return build_drift_event_payload(
        plugin,
        "snapshot_with_drift_alarm",
        entry,
        "open-design-v0.24.1",
        now=datetime(2026, 10, 6, 19, 45, tzinfo=timezone.utc),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Repository — probe option (a) semantics
# ══════════════════════════════════════════════════════════════════════════════


class TestDriftEventRepository:
    def test_create_from_payload_roundtrips_verbatim(self, drift_repo):
        payload = _payload(divergence_id=5)
        row = drift_repo.create_from_payload(payload)
        assert row.plugin == "opendesign"
        # the payload's `class` key maps to the target_class column
        assert row.target_class == "snapshot_with_drift_alarm"
        assert row.divergence_id == 5
        assert row.files == ["prompts/contracts/od-next-intent-resolution.ts"]
        assert row.observed_at == "2026-10-06T19:45:00+00:00"
        assert row.observed_tag == "open-design-v0.24.1"
        # to_payload() returns the CON §5 verbatim shape back
        assert row.to_payload() == payload

    def test_unresolved_means_row_exists(self, drift_repo):
        assert drift_repo.list_unresolved() == []
        drift_repo.create_from_payload(_payload(1))
        drift_repo.create_from_payload(_payload(2))
        unresolved = drift_repo.list_unresolved()
        assert len(unresolved) == 2
        # resolution DELETES the row (probe doc option (a))
        assert drift_repo.resolve("opendesign", 1) is True
        assert drift_repo.resolve("opendesign", 1) is False  # already gone
        remaining = drift_repo.list_unresolved()
        assert [r.divergence_id for r in remaining] == [2]

    def test_list_unresolved_plugin_filter(self, drift_repo):
        drift_repo.create_from_payload(_payload(1, plugin="opendesign"))
        drift_repo.create_from_payload(_payload(9, plugin="future-plugin"))
        assert [r.divergence_id for r in drift_repo.list_unresolved(plugin="opendesign")] == [1]
        # None walks ALL plugins (future cross-plugin sweep accommodation)
        assert len(drift_repo.list_unresolved(plugin=None)) == 2

    def test_latest_for_plugin(self, drift_repo):
        old = _payload(1)
        new = build_drift_event_payload(
            "opendesign",
            "snapshot_with_drift_alarm",
            {
                "id": 2,
                "files": ["b.ts"],
                "delta": "+1",
                "rationale": "r",
                "pinning_test": "t",
            },
            "open-design-v0.24.1",
            now=datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc),
        )
        drift_repo.create_from_payload(old)
        drift_repo.create_from_payload(new)
        latest = drift_repo.latest_for_plugin("opendesign")
        assert latest is not None
        assert latest.divergence_id == 2
        assert drift_repo.latest_for_plugin("no-such") is None


# ══════════════════════════════════════════════════════════════════════════════
# emit_drift_event — the stub replaced by the seam
# ══════════════════════════════════════════════════════════════════════════════


class TestEmitDriftEventSeam:
    def test_default_sink_is_log_only(self):
        assert isinstance(get_drift_event_publisher(), LogOnlyDriftEventSink)

    def test_emit_routes_to_configured_store(self, drift_repo):
        configure_drift_event_publisher(DriftEventPublisher(drift_repo))
        entry = {
            "id": 7,
            "files": ["a.ts"],
            "delta": "+1",
            "rationale": "r",
            "pinning_test": "t",
        }
        sr.emit_drift_event("opendesign", "snapshot_with_drift_alarm", entry, "open-design-v0.24.1")
        unresolved = drift_repo.list_unresolved()
        assert len(unresolved) == 1
        assert unresolved[0].divergence_id == 7

    def test_emit_signature_stable_from_slice3(self):
        # The ③ signature: (plugin, target_class, entry, observed_tag) -> None
        import inspect

        sig = inspect.signature(sr.emit_drift_event)
        assert list(sig.parameters) == ["plugin", "target_class", "entry", "observed_tag"]
        assert sig.return_annotation in (None, "None", inspect.Signature.empty)

    def test_emit_never_raises_on_sink_failure(self):
        class _ExplodingSink:
            def publish(self, payload):
                raise RuntimeError("DB is down")

        configure_drift_event_publisher(_ExplodingSink())
        entry = {"id": 3, "files": [], "delta": "d", "rationale": "r", "pinning_test": "p"}
        # must NOT raise — the sync result is already complete
        sr.emit_drift_event("opendesign", "snapshot_with_drift_alarm", entry, "tag")

    def test_emit_drift_event_safe_swallows_bad_payloads(self, drift_repo):
        configure_drift_event_publisher(DriftEventPublisher(drift_repo))
        # a payload missing every field still persists a row (defensive
        # defaults) rather than raising through the sync path
        emit_drift_event_safe({})
        assert len(drift_repo.list_unresolved()) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Table registration (boot-path create_all proof, no daemon boot)
# ══════════════════════════════════════════════════════════════════════════════


class TestDriftEventsTableRegistration:
    def test_drift_events_in_metadata(self):
        # The manager's create_all prelude imports DriftEvent so the
        # table joins create_all on BOTH drivers; the model must be on
        # the shared metadata for that to work.
        assert "drift_events" in SQLModel.metadata.tables

    def test_migration_sql_up_and_down_sections_exist(self):
        from pathlib import Path

        sql = Path("daemon/migrations/versions/20261007_000001_create_drift_events.sql")
        text = sql.read_text(encoding="utf-8")
        assert "-- UP" in text and "-- DOWN" in text
        assert "CREATE TABLE IF NOT EXISTS drift_events" in text
        assert "DROP TABLE IF EXISTS drift_events" in text
