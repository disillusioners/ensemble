"""End-to-end family semantics for the ``anchor`` param on
``GET /api/instances``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, Session, select

import httpx

from daemon.api import app
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance_ui_prefs.repository import InstanceUiPrefsRepository
from daemon.services.instance_lifecycle import (
    InstanceLifecycleService,
    INSTANCE_LIST_ANCHOR_BFS_CAP,
)


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


def _seed(engine, ids, parent_map=None, project_map=None):
    repo = SQLModelInstanceRepository(engine)
    parent_map = parent_map or {}
    project_map = project_map or {}
    for iid in ids:
        repo.create(
            instance_id=iid,
            agent_id="developer",
            agent_dir="agents/developer",
            parent_id=parent_map.get(iid),
            status="running",
            project_id=project_map.get(iid, "proj-1"),
        )
    return repo


class _ManagerStandin:
    def __init__(self, engine):
        self._engine = engine
        self._instance_repository = SQLModelInstanceRepository(engine)
        self._instance_ui_prefs_repo = InstanceUiPrefsRepository(engine=engine)
        # lifecycle wired to real repo via fake manager
        self._fake = self
        self._lifecycle = InstanceLifecycleService(self, None)
        # monkey: lifecycle uses self._manager._instance_repository

    @property
    def is_write_paused(self):
        return False

    def list_instances(self, limit=10, offset=0, project_id=None, exclude_kb=True,
                       include_descendants=False, search=None, order="pinned",
                       source=None):
        repo = self._instance_repository
        with __import__("sqlmodel").Session(self._engine) as s:
            q = select(Instance)
            if project_id is not None:
                q = q.where(Instance.project_id == project_id)
            rows = s.exec(q.order_by(Instance.created_at.desc())).all()
        # apply offset/limit
        sliced = list(rows)[offset:offset + limit]
        children_map = repo.list_child_ids_for_parents([r.instance_id for r in sliced])
        # need children for all rows? mirror: shape each
        result = []
        for r in sliced:
            info = r.to_dict() if hasattr(r, "to_dict") else None
            if info is None:
                # fallback build
                info = {
                    "instance_id": r.instance_id,
                    "agent_id": r.agent_id,
                    "agent_dir": r.agent_dir,
                    "status": r.status,
                    "parent_id": r.parent_id,
                    "title": r.title,
                    "initiative_message": r.initiative_message,
                    "children": [],
                    "metadata": dict(r.instance_metadata or {}),
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                    "project_id": r.project_id,
                }
            info["children"] = children_map.get(r.instance_id, [])
            result.append(info)
        return result, len(rows), False

    def get_instance_anchor_family(self, anchor_id):
        return self._lifecycle.get_instance_anchor_family(anchor_id)


@pytest_asyncio.fixture
async def client(engine):
    manager = _ManagerStandin(engine)
    app.state.manager = manager
    app.state.start_time = 1000.0
    from daemon.services import db_offload as _db_offload
    _db_offload.configure(loop=asyncio.get_running_loop())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, manager, engine


def _get(ac, **params):
    return ac.get("/api/instances", params=params or None)


@pytest.mark.asyncio
async def test_anchor_older_than_page_present(client):
    ac, manager, engine = client
    repo = manager._instance_repository
    # anchor old
    repo.create(instance_id="old-anchor", agent_id="developer", agent_dir="agents/developer",
                status="running", project_id="proj-1")
    repo.create(instance_id="old-child", agent_id="developer", agent_dir="agents/developer",
                parent_id="old-anchor", status="running", project_id="proj-1")
    # recent page rows
    for i in range(3):
        repo.create(instance_id=f"recent-{i}", agent_id="developer", agent_dir="agents/developer",
                    status="running", project_id="proj-1")
    # page limit 1 => only most recent? our standin sorts created_at desc and slices; okay
    resp = await _get(ac, limit=1, anchor="old-anchor")
    assert resp.status_code == 200
    ids = [i["instance_id"] for i in resp.json()["instances"]]
    assert "old-anchor" in ids
    assert "old-child" in ids


@pytest.mark.asyncio
async def test_family_present_include_descendants_false(client):
    ac, manager, engine = client
    repo = manager._instance_repository
    repo.create(instance_id="anc", agent_id="developer", agent_dir="agents/developer", status="running")
    repo.create(instance_id="anc-child", agent_id="developer", agent_dir="agents/developer", parent_id="anc", status="running")
    resp = await _get(ac, include_descendants="false", anchor="anc")
    assert resp.status_code == 200
    ids = [i["instance_id"] for i in resp.json()["instances"]]
    assert "anc" in ids and "anc-child" in ids


@pytest.mark.asyncio
async def test_unknown_anchor_unchanged(client):
    ac, manager, engine = client
    repo = manager._instance_repository
    repo.create(instance_id="r1", agent_id="developer", agent_dir="agents/developer", status="running")
    resp0 = await _get(ac)
    ids0 = [i["instance_id"] for i in resp0.json()["instances"]]
    resp1 = await _get(ac, anchor="nope")
    ids1 = [i["instance_id"] for i in resp1.json()["instances"]]
    assert ids0 == ids1


@pytest.mark.asyncio
async def test_no_duplicate_when_already_in_page(client):
    ac, manager, engine = client
    repo = manager._instance_repository
    repo.create(instance_id="anc", agent_id="developer", agent_dir="agents/developer", status="running")
    resp = await _get(ac, anchor="anc")
    ids = [i["instance_id"] for i in resp.json()["instances"]]
    assert ids.count("anc") == 1


@pytest.mark.asyncio
async def test_children_populated_on_family_rows(client):
    ac, manager, engine = client
    repo = manager._instance_repository
    repo.create(instance_id="anc", agent_id="developer", agent_dir="agents/developer", status="running")
    repo.create(instance_id="anc-child", agent_id="developer", agent_dir="agents/developer", parent_id="anc", status="running")
    resp = await _get(ac, anchor="anc")
    by_id = {i["instance_id"]: i for i in resp.json()["instances"]}
    assert "anc-child" in by_id["anc"].get("children", [])
    assert by_id["anc-child"].get("children", []) == [] or isinstance(by_id["anc-child"].get("children"), list)


@pytest.mark.asyncio
async def test_anchor_bypasses_project_filter(client):
    ac, manager, engine = client
    repo = manager._instance_repository
    repo.create(instance_id="anc", agent_id="developer", agent_dir="agents/developer", status="running", project_id="other-proj")
    repo.create(instance_id="anc-child", agent_id="developer", agent_dir="agents/developer", parent_id="anc", status="running", project_id="other-proj")
    repo.create(instance_id="r1", agent_id="developer", agent_dir="agents/developer", status="running", project_id="proj-1")
    resp = await _get(ac, project_id="proj-1", anchor="anc")
    assert resp.status_code == 200
    ids = [i["instance_id"] for i in resp.json()["instances"]]
    assert "anc" in ids and "anc-child" in ids


@pytest.mark.asyncio
async def test_cap_respected(client, caplog):
    ac, manager, engine = client
    repo = manager._instance_repository
    repo.create(instance_id="cap-anchor", agent_id="developer", agent_dir="agents/developer", status="running")
    # create 505 children (cap 500 total => 499 descendants max)
    for i in range(505):
        repo.create(instance_id=f"cap-child-{i}", agent_id="developer", agent_dir="agents/developer",
                    parent_id="cap-anchor", status="running")
    with caplog.at_level(logging.WARNING):
        resp = await _get(ac, anchor="cap-anchor")
    assert resp.status_code == 200
    ids = [i["instance_id"] for i in resp.json()["instances"]]
    family_ids = [x for x in ids if x == "cap-anchor" or x.startswith("cap-child-")]
    assert len(family_ids) <= INSTANCE_LIST_ANCHOR_BFS_CAP
    assert any("limit" in r.message.lower() or "anchor" in r.message.lower() for r in caplog.records)
