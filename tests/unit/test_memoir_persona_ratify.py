"""memoir_persona_ratify verb — persona approval queue surface (scope addendum)."""

from __future__ import annotations

import re

import pytest
from sqlmodel import SQLModel, create_engine
from sqlmodel.pool import StaticPool

from daemon.repositories.prompt_version.repository import actor_from_instance
from daemon.repositories.memoir.repository import MemoirPersonaRepository
from daemon.services.prompt_version_service import HumanLaneRequiredError
from daemon.tools.memoir import (
    MEMOIR_AUTHORIZED_AGENT,
    MEMOIR_CODE_INTERNAL_ERROR,
    MEMOIR_CODE_NOT_AUTHORIZED,
    MEMOIR_CODE_NOT_FOUND,
    MEMOIR_TOOL_NAMES,
    create_memoir_tools,
)

ENVELOPE_RE = r"^Error: (?P<label>memoir_\w+) REFUSED — reason=(?P<code>memoir_\w+): "


class ProbeManager:
    def __init__(self, engine=None, knob_on=True):
        from types import SimpleNamespace

        self._engine_handle = engine
        self._knob_on = knob_on
        self.knob_reads = 0
        self.engine_touches = 0

    @property
    def config(self):
        from types import SimpleNamespace

        self.knob_reads += 1
        return SimpleNamespace(memoir=SimpleNamespace(enabled=self._knob_on))

    @property
    def _engine(self):
        self.engine_touches += 1
        return self._engine_handle


def _assert_envelope(result, label, code):
    text = result if isinstance(result, str) else str(result)
    m = re.match(ENVELOPE_RE, text)
    assert m is not None, text
    assert m.group("label") == label, text
    assert m.group("code") == code, text


@pytest.fixture()
def engine():
    # A REAL in-memory engine: on the co-merged tree (fix/persona-self-ratify-ari)
    # approve() calls get_version() BEFORE the human-lane refusal, so a string
    # engine would raise AttributeError and the generic handler would turn the
    # refusal into memoir_internal_error. A real engine keeps the verbatim leg
    # honest on both trees.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def mgr(engine):
    return ProbeManager(engine=engine, knob_on=True)


@pytest.fixture()
def tools(mgr):
    return {t.name: t for t in create_memoir_tools(mgr, current_instance_id="inst-1", agent_id=MEMOIR_AUTHORIZED_AGENT)}


@pytest.mark.asyncio
async def test_verbatim_refusal_relay(tools):
    # NON-EXISTENT id on purpose: on the co-merged exemption tree a lookup that
    # finds no version leaves the self-ratify exemption inactive (the exemption
    # never turns a refusal into a not-found error), so the SAME
    # HumanLaneRequiredError is relayed on both trees. An EXISTING row + an
    # "ari" actor would trip the exemption there and the call would SUCCEED.
    result = await tools["memoir_persona_ratify"].ainvoke({"version_id": "abc"})
    text = result if isinstance(result, str) else str(result)
    # The HumanLaneRequiredError text must be relayed unchanged.
    assert "approve/reject on prompt_version abc requires auth_method='session'" in text
    assert "got 'internal'" in text
    _assert_envelope(result, "memoir_persona_ratify", MEMOIR_CODE_NOT_AUTHORIZED)


def test_schema_exposes_only_version_id(tools):
    schema = tools["memoir_persona_ratify"].args_schema
    fields = list(schema.model_fields.keys())
    assert fields == ["version_id"], fields


@pytest.mark.asyncio
async def test_missing_version_mapping(monkeypatch, tools):
    def fake(self, version_id, actor):
        raise ValueError(f"persona version {version_id} not found")

    monkeypatch.setattr(MemoirPersonaRepository, "approve", fake, raising=False)
    result = await tools["memoir_persona_ratify"].ainvoke({"version_id": "nope"})
    _assert_envelope(result, "memoir_persona_ratify", MEMOIR_CODE_NOT_FOUND)


@pytest.mark.asyncio
async def test_tool_registered():
    mgr = ProbeManager(engine=None, knob_on=True)
    built = create_memoir_tools(mgr, current_instance_id="i", agent_id=MEMOIR_AUTHORIZED_AGENT)
    names = {t.name for t in built}
    assert "memoir_persona_ratify" in names
    assert "memoir_persona_ratify" in MEMOIR_TOOL_NAMES
