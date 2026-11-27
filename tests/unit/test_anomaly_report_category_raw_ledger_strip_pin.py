"""Permanent pin: refusal-ledger ``category_raw`` legacy-marker strip (H1-O1).

The ACCEPT site strips agent-supplied ``__ENS_SECRET__<uuid4>__`` markers before
storing ``category_raw``; the REFUSAL site (``_ledger_refusal``) must do the same.
Before this pin, deleting the ledger-site strip left all 152 anomaly unit tests
green. Leg 1 asserts the strip; leg 2 is a negative control proving the stored
text actually depends on it (the pin is non-vacuous).
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from daemon.config import NestReportConfig
from daemon.repositories.anomaly import models as anomaly_models
from daemon.repositories.anomaly.models import AnomalyCluster, AnomalyReport
from daemon.services import anomaly_service
from daemon.services.anomaly_service import AnomalyRefusalError, AnomalyService

_FIXED = "2026-11-27T06:00:00.000000+00:00"


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setattr(anomaly_service, "_utc_now_iso", lambda: _FIXED)
    monkeypatch.setattr(anomaly_models, "_utc_now_iso", lambda: _FIXED)


async def _passthrough(fn, *args, **kwargs):
    return fn(*args, **kwargs)


async def _refuse_with(tmp_path, agent_id, category):
    """Fill the 5-report window, then trip the gate with ``category``."""
    engine = create_engine(
        f"sqlite:///{tmp_path / (agent_id + '.db')}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
    )
    service = AnomalyService(
        engine, config=NestReportConfig(), manager=None, offload=_passthrough
    )
    for i in range(5):
        await service.accept_report(
            agent_id=agent_id, instance_id="inst-1", category="tool-failure",
            description=f"seed {i}", severity="minor",
        )
    with pytest.raises(AnomalyRefusalError) as caught:
        await service.accept_report(
            agent_id=agent_id, instance_id="inst-1", category=category,
            description="boom", severity="minor",
        )
    assert caught.value.token == "rate-limited"
    with Session(engine) as session:
        row = session.exec(
            select(AnomalyReport).where(
                AnomalyReport.outcome == "rate_limited",
                AnomalyReport.agent_id == agent_id,
            )
        ).first()
        assert row is not None
        session.expunge(row)
    return row


class TestRefusalLedgerCategoryRawStrip:
    async def test_refusal_ledger_category_raw_strips_supplied_marker(self, tmp_path):
        marker = "__ENS_SECRET__" + str(uuid.uuid4()) + "__"
        row = await _refuse_with(tmp_path, "worker-pin-a", "tool-failure " + marker)
        category_raw = row.category_raw or ""
        assert "__ENS_SECRET__" not in category_raw, category_raw
        assert "[marker-stripped]" in category_raw, category_raw
        assert len(category_raw) <= 64, category_raw
        # the folded category is the canonical (reserved) value, not marker text
        assert row.category == "other", row.category

    async def test_control_without_the_strip_the_marker_would_survive(
        self, tmp_path, monkeypatch
    ):
        marker = "__ENS_SECRET__" + str(uuid.uuid4()) + "__"
        uid = marker[len("__ENS_SECRET__") : -len("__")]
        monkeypatch.setattr(
            anomaly_service, "LEGACY_SECRET_MARKER_RE", re.compile(r"(?!x)x")
        )
        row = await _refuse_with(tmp_path, "worker-pin-b", "tool-failure " + marker)
        category_raw = row.category_raw or ""
        assert "__ENS_SECRET__" in category_raw, category_raw
        assert uid in category_raw, category_raw
        assert "[marker-stripped]" not in category_raw, category_raw
