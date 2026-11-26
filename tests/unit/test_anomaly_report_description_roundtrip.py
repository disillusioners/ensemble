"""Round-trip proof for the anomaly description cap on the REAL schema.

The defect: a 1000-char cap cut a filed wedge predicate mid-predicate,
and the STORED row carried no marker — the next reader trusted a halved
finding. The fix raises the config-owned cap and marks residual cuts
loudly. This pack proves BOTH halves through the real service + real
repository against a TEMP SQLITE DB with the ordered migration files
applied (no live DB, no HOME-derived paths, no daemon boot).

Fenced DB: ``tmp_path/anomaly_roundtrip.db`` (SQLite), created from
``daemon/migrations/versions/20261105_020500_create_anomaly_reports.sql``
and ``...020501_create_anomaly_clusters.sql`` (their UP sections).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, text

from cryptography.fernet import Fernet
from daemon.config import NestReportConfig
from daemon.services.anomaly_service import AnomalyService
from daemon.services.secret_persistence_scrub import (
    register_secret_service,
    reset_process_registry,
)
from daemon.services.secret_service import (
    SecretCipher,
    SecretService,
)
from sqlalchemy import create_engine as _ce
from sqlmodel import SQLModel as _SM

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "daemon" / "migrations" / "versions"


class _StubInstanceRepo:
    def get_ancestor_ids(self, instance_id: str) -> list[str]:
        return []

    def get_agent_ids_for(self, instance_ids: list[str]) -> dict[str, str | None]:
        return {iid: "" for iid in instance_ids}


class _StubTaskRepo:
    def find_running_by_instance(self, instance_id: str):
        return None


class _StubJobRepo:
    def get_active_by_instance(self, instance_id: str, job_id: str | None = None):
        return None


class _StubMissionRepo:
    def find_outstanding_commission_for_worker(self, instance_id: str, **kwargs):
        return None


class _StubWedgeRepo:
    def list_recent(self, limit: int = 50):
        return []


class _StubCheckpointer:
    class _Saver:
        async def aget(self, config: dict) -> dict:
            return {"channel_values": {"messages": []}}

    def __init__(self) -> None:
        self.raw_saver = self._Saver()


class _StubManager:
    def __init__(self) -> None:
        self.checkpointer = _StubCheckpointer()
        self.metrics_query_provider = None
        self._instance_repository = _StubInstanceRepo()
        self._task_repo = _StubTaskRepo()
        self._job_queue_service = type(
            "_Jqs", (), {"repository": _StubJobRepo()}
        )()
        self._mission_record_service = type(
            "_Mrs", (), {"commission_repository": lambda self=None: _StubMissionRepo()}
        )()
        self._wedge_capture_repository = _StubWedgeRepo()


async def _passthrough(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def _apply_up(engine, filename: str) -> None:
    """Apply ONLY the UP section of an ordered migration file."""
    sql = (MIGRATIONS / filename).read_text(encoding="utf-8")
    up = sql.split("-- UP", 1)[1].split("-- DOWN", 1)[0]
    statements = [part.strip() for part in up.split(";") if part.strip()]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def _harness(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'anomaly_roundtrip.db'}",
        connect_args={"check_same_thread": False},
    )
    _apply_up(engine, "20261105_020500_create_anomaly_reports.sql")
    _apply_up(engine, "20261105_020501_create_anomaly_clusters.sql")
    service = AnomalyService(
        engine,
        config=NestReportConfig(),
        manager=_StubManager(),
        offload=_passthrough,
    )
    return engine, service


def _read_back(engine, report_id: str):
    with Session(engine) as session:
        row = session.execute(
            text(
                "SELECT description, suspected_cause "
                "FROM anomaly_reports WHERE id = :id"
            ),
            {"id": report_id},
        ).one()
        return row[0], row[1]


@pytest.mark.asyncio
async def test_3000_char_description_round_trips_complete(tmp_path) -> None:
    engine, service = _harness(tmp_path)
    config = NestReportConfig()
    prefix = "wedge predicate: "
    tail = " AND last_activity_at IS NULL AND status='running'"
    description = prefix + "a" * (3000 - len(prefix) - len(tail)) + tail
    assert len(description) == 3000
    assert 3000 < config.description_max_chars  # the raised cap is load-bearing

    result = await service.accept_report(
        agent_id="worker",
        instance_id="inst-roundtrip",
        category="wedge-suspected",
        description=description,
        severity="major",
    )
    stored, cause = _read_back(engine, result.report_id)
    assert stored == description, "the 3000-char finding must survive byte-for-byte"
    assert len(stored) == len(description)
    assert "TRUNCATED" not in stored
    assert cause is None
    assert result.truncated is False

    with engine.connect() as conn:
        column_type = conn.exec_driver_sql(
            "SELECT type FROM pragma_table_info('anomaly_reports') "
            "WHERE name = 'description'"
        ).scalar_one()
    assert column_type == "TEXT"  # the DB owns no 1000-char cap


@pytest.mark.asyncio
async def test_over_cap_description_is_loud_in_the_stored_row(tmp_path) -> None:
    engine, service = _harness(tmp_path)
    cap = NestReportConfig().description_max_chars
    description = "p" * (cap + 500) + " THE-LOST-PREDICATE"

    result = await service.accept_report(
        agent_id="worker",
        instance_id="inst-overcap",
        category="wedge-suspected",
        description=description,
        severity="major",
    )
    stored, _ = _read_back(engine, result.report_id)
    assert result.truncated is True
    assert len(stored) == cap
    assert stored.endswith("stored prefix only]")
    assert f"[TRUNCATED: original {cap + 500 + len(' THE-LOST-PREDICATE')} chars" in stored
    assert "THE-LOST-PREDICATE" not in stored


@pytest.mark.asyncio
async def test_envelope_truncated_flag_agrees_with_stored_marker(tmp_path) -> None:
    """(iv) The ENVELOPE flag and the STORED marker always agree.

    Matrix:
      * fits            — input under cap, no secret growth -> truncated False,
                          no "[TRUNCATED" in stored description.
      * raw-too-long    — input over cap -> truncated True, marker present.
      * scrub-grew      — input under cap but a stored secret substitution
                          grows scrubbed past the cap -> truncated True, marker.
    For every row: envelope.truncated == ("[TRUNCATED" in stored_description
    or "[TRUNCATED" in stored_suspected_cause).
    """
    reset_process_registry()
    try:
        # Row 1: fits.
        (tmp_path / "fits").mkdir(exist_ok=True)
        engine, service = _harness(tmp_path / "fits")
        description = "wedge predicate: " + "a" * 100
        cap = NestReportConfig().description_max_chars
        assert len(description) < cap
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-fits",
            category="wedge-suspected",
            description=description,
            severity="major",
        )
        stored, cause = _read_back(engine, result.report_id)
        expected_truncated = False
        marker_in_stored = ("[TRUNCATED" in (stored or "")) or (
            "[TRUNCATED" in (cause or "")
        )
        assert result.truncated == expected_truncated, (
            f"fits: envelope={result.truncated} expected {expected_truncated}"
        )
        assert result.truncated == marker_in_stored, (
            f"fits: envelope={result.truncated} marker_in_stored={marker_in_stored}"
        )
        assert "TRUNCATED" not in (stored or "")
        # discriminating control: a genuinely over-cap input flips both.
        assert len(description) < cap

        # Row 2: raw-too-long.
        (tmp_path / "over").mkdir(exist_ok=True)
        engine2, service2 = _harness(tmp_path / "over")
        cap = NestReportConfig().description_max_chars
        description2 = "p" * (cap + 500) + " THE-LOST-PREDICATE"
        result2 = await service2.accept_report(
            agent_id="worker",
            instance_id="inst-over",
            category="wedge-suspected",
            description=description2,
            severity="major",
        )
        stored2, cause2 = _read_back(engine2, result2.report_id)
        marker_in_stored2 = ("[TRUNCATED" in (stored2 or "")) or (
            "[TRUNCATED" in (cause2 or "")
        )
        assert result2.truncated is True
        assert result2.truncated == marker_in_stored2, (
            f"over: envelope={result2.truncated} marker_in_stored={marker_in_stored2}"
        )
        assert "THE-LOST-PREDICATE" not in (stored2 or "")

        # Row 3: scrub-grew-past-cap (registered stored secret).
        reset_process_registry()
        fernet_key = Fernet.generate_key().decode()
        eng3 = _ce("sqlite:///:memory:")
        _SM.metadata.create_all(eng3)
        svc3_secret = SecretService(
            engine=eng3, cipher=SecretCipher(encryption_key=fernet_key)
        )
        svc3_secret.store(name="growth-secret", kind="generic", value="a", actor="tester")
        register_secret_service(svc3_secret)

        (tmp_path / "grew").mkdir(exist_ok=True)
        engine3, service3 = _harness(tmp_path / "grew")
        cap = NestReportConfig().description_max_chars
        # 300 copies of "a" (<= cap) whose substitution grows scrubbed past cap.
        description3 = "a" * 300
        assert len(description3) <= cap
        result3 = await service3.accept_report(
            agent_id="worker",
            instance_id="inst-grew",
            category="wedge-suspected",
            description=description3,
            severity="major",
        )
        stored3, cause3 = _read_back(engine3, result3.report_id)
        marker_in_stored3 = ("[TRUNCATED" in (stored3 or "")) or (
            "[TRUNCATED" in (cause3 or "")
        )
        assert result3.truncated is True, "scrub-grew must be truncated"
        assert result3.truncated == marker_in_stored3, (
            f"grew: envelope={result3.truncated} marker_in_stored={marker_in_stored3}"
        )
        assert "__ENS_REDACTED__" in (stored3 or "") or "TRUNCATED" in (stored3 or "")

        # suspected_cause path: None case already covered (cause is None).
        # Additionally verify a fits suspected_cause does not set flag.
        (tmp_path / "cause").mkdir(exist_ok=True)
        engine4, service4 = _harness(tmp_path / "cause")
        result4 = await service4.accept_report(
            agent_id="worker",
            instance_id="inst-cause",
            category="wedge-suspected",
            description="short desc",
            severity="major",
            suspected_cause="short cause",
        )
        stored4, cause4 = _read_back(engine4, result4.report_id)
        marker_in_stored4 = ("[TRUNCATED" in (stored4 or "")) or (
            "[TRUNCATED" in (cause4 or "")
        )
        assert result4.truncated == marker_in_stored4
        assert result4.truncated is False
    finally:
        reset_process_registry()
