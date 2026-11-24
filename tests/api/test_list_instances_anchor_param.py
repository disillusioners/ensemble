"""API integration tests for the new ``anchor`` query parameter on
``GET /api/instances``.

Route-boundary pack: accepts param, forwards verbatim to the new facade
``manager.get_instance_anchor_family``, absent anchor => facade not called,
envelope shape unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import httpx

from daemon.api import app


@pytest.fixture(autouse=True)
def _auth_gate_off():
    from daemon.middleware.auth import AuthMiddleware

    def _cls_of(entry):
        return getattr(entry, "cls", entry)

    removed = [e for e in list(app.user_middleware) if _cls_of(e) is AuthMiddleware]
    for entry in removed:
        app.user_middleware.remove(entry)
    original_cfg = getattr(app.state, "auth_config", None)
    if original_cfg is not None:
        app.state.auth_config = None
    if removed:
        app.middleware_stack = None
    try:
        yield
    finally:
        if removed:
            for entry in removed:
                app.user_middleware.insert(0, entry)
            app.middleware_stack = None
        if original_cfg is not None:
            app.state.auth_config = original_cfg


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _instance_dict() -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "instance_id": "anchor-test-001",
        "agent_id": "developer",
        "agent_dir": "agents/developer",
        "status": "running",
        "parent_id": None,
        "title": None,
        "children": [],
        "metadata": {},
        "created_at": now,
        "updated_at": now,
        "project_id": None,
    }


class _UiPrefsRepoStub:
    def get_all(self, instance_ids: list[str]) -> dict[str, Any]:
        return {}


class _SpyManager:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, Any]] = []
        self.anchor_calls: list[Any] = []

    @property
    def is_write_paused(self) -> bool:
        return False

    @property
    def _instance_ui_prefs_repo(self) -> Any:
        return _UiPrefsRepoStub()

    def list_instances(
        self,
        limit: int = 10,
        offset: int = 0,
        project_id: str | None = None,
        exclude_kb: bool = True,
        include_descendants: bool = False,
        search: str | None = None,
        order: str = "pinned",
        source: str | None = None,
    ) -> tuple[list[dict], int, bool]:
        self.list_calls.append({
            "limit": limit, "offset": offset, "project_id": project_id,
            "exclude_kb": exclude_kb, "include_descendants": include_descendants,
            "search": search, "order": order, "source": source,
        })
        return [_instance_dict()], 1, False

    def get_instance_anchor_family(self, anchor_id: Any) -> list[dict]:
        self.anchor_calls.append(anchor_id)
        # Return an extra row distinct from the page row to exercise merge.
        extra = dict(_instance_dict())
        extra["instance_id"] = "anchor-extra-002"
        return [extra]


@pytest_asyncio.fixture
async def client(engine: Engine):
    spy = _SpyManager()
    app.state.manager = spy
    app.state.start_time = 1000.0

    from daemon.services import db_offload as _db_offload

    _db_offload.configure(loop=asyncio.get_running_loop())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, spy


def _get_list(client, **params):
    return client.get("/api/instances", params=params or None)


@pytest.mark.asyncio
async def test_anchor_accepted(client):
    ac, spy = client
    response = await _get_list(ac, anchor="abc-123")
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_anchor_forwarded_verbatim(client):
    ac, spy = client
    await _get_list(ac, anchor="abc-123")
    assert len(spy.anchor_calls) == 1
    assert spy.anchor_calls[0] == "abc-123"


@pytest.mark.asyncio
async def test_anchor_absent_not_called(client):
    ac, spy = client
    await _get_list(ac)
    assert spy.anchor_calls == []


@pytest.mark.asyncio
async def test_anchor_empty_string_forwarded(client):
    ac, spy = client
    response = await _get_list(ac, anchor="")
    assert response.status_code == 200, response.text
    # empty string is a present value => forwarded (degrades inside facade)
    assert spy.anchor_calls == [""]


@pytest.mark.asyncio
async def test_envelope_unchanged(client):
    ac, spy = client
    response = await _get_list(ac, anchor="abc-123")
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data.keys()) >= {"instances", "total"}
    assert isinstance(data["instances"], list)
    assert isinstance(data["total"], int)
    # total stays normal-page semantics (1), not including anchor extra
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_anchor_merge_appends_row(client):
    ac, spy = client
    response = await _get_list(ac, anchor="abc-123")
    data = response.json()
    ids = [i["instance_id"] for i in data["instances"]]
    assert "anchor-test-001" in ids
    assert "anchor-extra-002" in ids
