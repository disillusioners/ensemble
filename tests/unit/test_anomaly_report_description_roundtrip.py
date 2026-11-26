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

from daemon.config import NestReportConfig
from daemon.services.anomaly_service import AnomalyService

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
