"""END-TO-END proof of the memoir review seam (m5 lane).

This module proves, against a REAL in-memory SQLite engine (the house
memoir shape), that the ari-only ``memoir_review`` tool verb records a
DURABLE decision on a real ``memoir_proposal_ledger`` row — and that the
decided row then vanishes from the briefing's proposal zone and from the
notify selection, and refuses a second decision.

NO source changes. NO persona-lore title tokens. The actor claim is
derived at runtime from ``PIN_ACTORS`` (never spelled literally).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from daemon.repositories.memoir.models import (
    MemoirProposalLedger,
    PIN_ACTORS,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_keepsake_proposal_service import (
    MemoirKeepsakeProposalService,
)
from daemon.tools.memoir import (
    MEMOIR_AUTHORIZED_AGENT,
    create_memoir_tools,
)

AGENT = "ari"
LONG_BODY = (
    "We argued about the font for three hours and settled on the serif; "
    "the decision became canon for every later page we shipped together."
)
TITLE = "A memory"

_ACTOR = next(iter(PIN_ACTORS))


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def manager(engine):
    return __import__("types").SimpleNamespace(
        _engine=engine,
        config=__import__("types").SimpleNamespace(
            memoir=__import__("types").SimpleNamespace(enabled=True)
        ),
    )


def _stage_one(engine, repo, title=TITLE):
    repo.insert_episode(
        agent_id=AGENT, body=LONG_BODY, title=title, importance=9
    )
    service = MemoirKeepsakeProposalService(engine)
    outcome = service.process_pass(notify=False)
    assert outcome.staged == 1, outcome


def _ledger_rows(engine):
    with Session(engine) as session:
        return list(session.exec(MemoirProposalLedger.__table__.select()).all())


@pytest.mark.asyncio
async def test_verb_present(engine, manager):
    """Leg 1 — the verb is present and named ``memoir_review``."""
    tools = {
        t.name: t
        for t in create_memoir_tools(manager, agent_id=MEMOIR_AUTHORIZED_AGENT)
    }
    assert "memoir_review" in tools, "memoir_review must be registered"
    assert tools["memoir_review"].name == "memoir_review"


@pytest.mark.asyncio
async def test_review_records_decision(engine, repo, manager):
    """Leg 2 — calling it returns success and stamps the ledger row."""
    _stage_one(engine, repo, title=TITLE)
    tools = {
        t.name: t
        for t in create_memoir_tools(manager, agent_id=MEMOIR_AUTHORIZED_AGENT)
    }
    result = await tools["memoir_review"].ainvoke(
        {"title": TITLE, "decision": "accepted", "actor": _ACTOR}
    )
    assert not (isinstance(result, str) and result.startswith("Error:")), result

    rows = _ledger_rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row.decision_status == "accepted", row
    assert row.decided_by == _ACTOR, row
    assert row.decided_at is not None, row


@pytest.mark.asyncio
async def test_decided_row_excluded_from_zone(engine, repo, manager):
    """Leg 3 — the decided row leaves the zone selection and zone_lines."""
    _stage_one(engine, repo, title=TITLE)
    tools = {
        t.name: t
        for t in create_memoir_tools(manager, agent_id=MEMOIR_AUTHORIZED_AGENT)
    }
    await tools["memoir_review"].ainvoke(
        {"title": TITLE, "decision": "accepted", "actor": _ACTOR}
    )

    service = MemoirKeepsakeProposalService(engine)
    assert service.select_zone_rows() == [], "decided row must leave the zone"
    assert service.zone_lines() == [], "decided row must leave zone_lines"


@pytest.mark.asyncio
async def test_decided_row_not_renotifiable(engine, repo, manager):
    """Leg 4 — the decided row is not re-notifiable."""
    _stage_one(engine, repo, title=TITLE)
    tools = {
        t.name: t
        for t in create_memoir_tools(manager, agent_id=MEMOIR_AUTHORIZED_AGENT)
    }
    await tools["memoir_review"].ainvoke(
        {"title": TITLE, "decision": "accepted", "actor": _ACTOR}
    )

    with Session(engine) as session:
        before = _ledger_rows(engine)[0].notification_status

    service = MemoirKeepsakeProposalService(engine)
    enqueued, reason = service.notify_once()
    assert enqueued is False, (enqueued, reason)

    with Session(engine) as session:
        after = _ledger_rows(engine)[0].notification_status
    assert after == before, (before, after)


@pytest.mark.asyncio
async def test_second_review_does_not_overwrite(engine, repo, manager):
    """Leg 5 — a second review is a compare-and-set (no overwrite, no success)."""
    _stage_one(engine, repo, title=TITLE)
    tools = {
        t.name: t
        for t in create_memoir_tools(manager, agent_id=MEMOIR_AUTHORIZED_AGENT)
    }
    first = await tools["memoir_review"].ainvoke(
        {"title": TITLE, "decision": "accepted", "actor": _ACTOR}
    )
    assert not (isinstance(first, str) and first.startswith("Error:")), first

    rows = _ledger_rows(engine)
    first_status = rows[0].decision_status
    first_by = rows[0].decided_by
    first_at = rows[0].decided_at

    second = await tools["memoir_review"].ainvoke(
        {"title": TITLE, "decision": "declined", "actor": _ACTOR}
    )
    assert isinstance(second, str) and second.startswith("Error:"), second

    rows2 = _ledger_rows(engine)
    assert rows2[0].decision_status == first_status
    assert rows2[0].decided_by == first_by
    assert rows2[0].decided_at == first_at


@pytest.mark.asyncio
async def test_unknown_title_refused(engine, repo, manager):
    """Leg 6 — an unknown title refuses (typed refusal) and leaves the row."""
    _stage_one(engine, repo, title=TITLE)
    tools = {
        t.name: t
        for t in create_memoir_tools(manager, agent_id=MEMOIR_AUTHORIZED_AGENT)
    }

    before_rows = _ledger_rows(engine)
    before_status = before_rows[0].decision_status
    before_by = before_rows[0].decided_by
    before_at = before_rows[0].decided_at

    result = await tools["memoir_review"].ainvoke(
        {"title": "No such proposal title", "decision": "accepted", "actor": _ACTOR}
    )
    assert isinstance(result, str) and result.startswith("Error:"), result
    assert "memoir_not_found" in result, result

    after_rows = _ledger_rows(engine)
    assert after_rows[0].decision_status == before_status
    assert after_rows[0].decided_by == before_by
    assert after_rows[0].decided_at == before_at
