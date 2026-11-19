"""memoir_review verb — keepsake-proposal review decision (m3 lane)."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daemon.repositories.memoir.models import PIN_ACTORS
from daemon.services.memoir_keepsake_proposal_service import (
    MemoirKeepsakeProposalService,
)
from daemon.tools.memoir import (
    MEMOIR_AUTHORIZED_AGENT,
    MEMOIR_CODE_ACTOR_REQUIRED,
    MEMOIR_CODE_DISABLED,
    MEMOIR_CODE_INVALID_ACTOR,
    MEMOIR_CODE_INTERNAL_ERROR,
    MEMOIR_CODE_NOT_AUTHORIZED,
    MEMOIR_CODE_NOT_FOUND,
    MEMOIR_TOOL_NAMES,
    create_memoir_tools,
)

ENVELOPE_RE = r"^Error: (?P<label>memoir_\w+) REFUSED — reason=(?P<code>memoir_\w+): "


class ProbeManager:
    def __init__(self, engine=None, knob_on=True):
        self._engine_handle = engine
        self._knob_on = knob_on
        self.knob_reads = 0
        self.engine_touches = 0

    @property
    def config(self):
        self.knob_reads += 1
        return SimpleNamespace(memoir=SimpleNamespace(enabled=self._knob_on))

    @property
    def _engine(self):
        self.engine_touches += 1
        return self._engine_handle


@pytest.fixture()
def mgr():
    return ProbeManager(engine=None, knob_on=True)


@pytest.fixture()
def tools(mgr):
    return {
        t.name: t
        for t in create_memoir_tools(mgr, current_instance_id="inst-1", agent_id=MEMOIR_AUTHORIZED_AGENT)
    }


def _assert_envelope(result, label, code):
    text = result if isinstance(result, str) else str(result)
    m = re.match(ENVELOPE_RE, text)
    assert m is not None, text
    assert m.group("label") == label, text
    assert m.group("code") == code, text


@pytest.mark.asyncio
async def test_tool_is_registered_in_factory_list(mgr):
    built = create_memoir_tools(mgr, current_instance_id="i", agent_id=MEMOIR_AUTHORIZED_AGENT)
    names = {t.name for t in built}
    assert "memoir_review" in names
    assert "memoir_review" in MEMOIR_TOOL_NAMES


@pytest.mark.asyncio
async def test_unauthorized_caller_refused_before_db(monkeypatch, tools):
    # rebuild with non-ari agent
    mgr2 = ProbeManager(engine=None, knob_on=True)
    t2 = {t.name: t for t in create_memoir_tools(mgr2, current_instance_id="i", agent_id="worker")}
    result = await t2["memoir_review"].ainvoke(
        {"title": "T", "decision": "accepted", "actor": next(iter(PIN_ACTORS))}
    )
    _assert_envelope(result, "memoir_review", MEMOIR_CODE_NOT_AUTHORIZED)


@pytest.mark.asyncio
async def test_passes_title_decision_actor_through(monkeypatch, mgr):
    captured = {}

    def fake(self, *, title, decision, actor):
        captured["title"] = title
        captured["decision"] = decision
        captured["actor"] = actor
        return {"ledger_id": "L1", "episode_id": "E1", "decision": decision, "title": title, "reason": "r"}

    monkeypatch.setattr(MemoirKeepsakeProposalService, "record_review", fake, raising=False)

    # give manager an engine handle
    class M(ProbeManager):
        pass

    mgr2 = ProbeManager(engine="ENGINE", knob_on=True)
    t = {x.name: x for x in create_memoir_tools(mgr2, current_instance_id="i", agent_id=MEMOIR_AUTHORIZED_AGENT)}
    actor = next(iter(PIN_ACTORS))
    result = await t["memoir_review"].ainvoke(
        {"title": "My Title", "decision": "accepted", "actor": actor}
    )
    assert captured == {"title": "My Title", "decision": "accepted", "actor": actor}
    # success line names memoir_pin for accepted
    assert "memoir_pin" in str(result)


@pytest.mark.asyncio
async def test_maps_valueerror_by_code(monkeypatch):
    class Boom(ValueError):
        def __init__(self, code):
            super().__init__("boom msg")
            self.code = code

    def fake(self, *, title, decision, actor):
        raise Boom("not_found")

    monkeypatch.setattr(MemoirKeepsakeProposalService, "record_review", fake, raising=False)

    mgr2 = ProbeManager(engine="ENGINE", knob_on=True)
    t = {x.name: x for x in create_memoir_tools(mgr2, current_instance_id="i", agent_id=MEMOIR_AUTHORIZED_AGENT)}
    actor = next(iter(PIN_ACTORS))
    result = await t["memoir_review"].ainvoke(
        {"title": "T", "decision": "accepted", "actor": actor}
    )
    _assert_envelope(result, "memoir_review", MEMOIR_CODE_NOT_FOUND)


@pytest.mark.asyncio
async def test_refuses_unknown_decision(monkeypatch):
    called = {"n": 0}

    def fake(self, *, title, decision, actor):
        called["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(MemoirKeepsakeProposalService, "record_review", fake, raising=False)

    mgr2 = ProbeManager(engine="ENGINE", knob_on=True)
    t = {x.name: x for x in create_memoir_tools(mgr2, current_instance_id="i", agent_id=MEMOIR_AUTHORIZED_AGENT)}
    actor = next(iter(PIN_ACTORS))
    result = await t["memoir_review"].ainvoke(
        {"title": "T", "decision": "maybe", "actor": actor}
    )
    _assert_envelope(result, "memoir_review", MEMOIR_CODE_INTERNAL_ERROR)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_refuses_actor_outside_pin_actors(monkeypatch):
    called = {"n": 0}

    def fake(self, *, title, decision, actor):
        called["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(MemoirKeepsakeProposalService, "record_review", fake, raising=False)

    mgr2 = ProbeManager(engine="ENGINE", knob_on=True)
    t = {x.name: x for x in create_memoir_tools(mgr2, current_instance_id="i", agent_id=MEMOIR_AUTHORIZED_AGENT)}
    result = await t["memoir_review"].ainvoke(
        {"title": "T", "decision": "accepted", "actor": "not-an-actor"}
    )
    _assert_envelope(result, "memoir_review", MEMOIR_CODE_INVALID_ACTOR)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_kill_switch_refusal(monkeypatch):
    mgr2 = ProbeManager(engine="ENGINE", knob_on=False)
    t = {x.name: x for x in create_memoir_tools(mgr2, current_instance_id="i", agent_id=MEMOIR_AUTHORIZED_AGENT)}
    actor = next(iter(PIN_ACTORS))
    result = await t["memoir_review"].ainvoke(
        {"title": "T", "decision": "accepted", "actor": actor}
    )
    _assert_envelope(result, "memoir_review", MEMOIR_CODE_DISABLED)


@pytest.mark.asyncio
async def test_degraded_boot_keeps_tool_present():
    mgr2 = ProbeManager(engine=None, knob_on=True)
    t = {x.name: x for x in create_memoir_tools(mgr2, current_instance_id="i", agent_id=MEMOIR_AUTHORIZED_AGENT)}
    assert "memoir_review" in t
    actor = next(iter(PIN_ACTORS))
    result = await t["memoir_review"].ainvoke(
        {"title": "T", "decision": "accepted", "actor": actor}
    )
    _assert_envelope(result, "memoir_review", MEMOIR_CODE_INTERNAL_ERROR)
