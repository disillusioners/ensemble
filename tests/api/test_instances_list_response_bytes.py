"""Byte-identity pin for ``GET /api/instances`` after the D2 change.

D2 (``optd-d2-skip-revalidation``) makes the list route hand-build its
response body and return a ``JSONResponse`` so FastAPI skips the
``response_model`` validation + jsonable-encode pass on the event loop.
The wire bytes must stay byte-identical to the pre-change (D1 / fd61c0ba2)
output — including the decorator's ``response_model_exclude`` making
``mcp_tool_names`` key-ABSENT on every row.

Pins
----
(a) BYTE-IDENTITY: the full HTTP envelope (status + sorted raw wire
    headers + body repr) of ``GET /api/instances`` against a FIXED
    deterministic fixture equals the committed golden captured from the
    base tree (fd61c0ba2). Dies on ANY wire drift (status, header set,
    body bytes).
(b) The golden body contains NO ``mcp_tool_names`` substring.
(c) NO-REVALIDATION: with ``InstanceListResponse.model_validate`` patched
    to raise, the route still returns 200 with identical bytes — dies if
    someone goes back to returning the bare model (which would re-trigger
    FastAPI's response validation pass).
(d) The list path keeps ``mcp_tool_names`` key-absent (inherited from the
    mcp-tool-names wire-shape pack, re-asserted here on the golden).

Run in isolation::

    python -m pytest tests/api/test_instances_list_response_bytes.py -q --tb=short
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import httpx

from daemon.api import app
from daemon.models.instance import InstanceListResponse
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance_ui_prefs.models import InstanceUiPrefs
from daemon.repositories.instance_ui_prefs.repository import (
    InstanceUiPrefsRepository,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants (must match the capture that produced the golden)
# ─────────────────────────────────────────────────────────────────────────────

NOW = "2026-11-19T00:00:00+00:00"
GOLDEN_PATH = (
    Path(__file__).parent
    / "golden"
    / "instances_list_response_bytes.golden.txt"
)
IDS = [f"row-{i:02d}" for i in range(12)]


# ─────────────────────────────────────────────────────────────────────────────
# Auth posture neutralisation (house pattern)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _auth_gate_off():
    from daemon.middleware.auth import AuthMiddleware

    def _cls_of(entry: Any) -> Any:
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


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


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


def _seed(engine: Engine) -> None:
    with Session(engine) as s:
        for i, iid in enumerate(IDS):
            status = ["running", "idle", "completed", "failed", "paused"][i % 5]
            title = (
                f"caf\xc3\xa9 \u2603 {iid}" if i % 3 == 0 else f"row {iid}"
            )
            im = None if i % 4 == 0 else f"initiative {i}"
            meta: dict[str, Any] = {}
            if i % 2 == 0:
                meta["mcp_tool_names"] = [f"t{i}", "x"]
            if i % 3 == 0:
                meta["model_override"] = f"model-{i}"
            if i % 5 == 0:
                meta["watchover_enabled"] = True
                meta["watchover_context"] = f"ctx {i}"
            s.add(
                Instance(
                    instance_id=iid,
                    agent_id="developer" if i % 2 == 0 else "worker",
                    agent_dir="agents/developer",
                    agent_name="developer",
                    parent_id=None,
                    status=status,
                    version=1,
                    instance_metadata=meta if meta else None,
                    title=title,
                    initiative_message=im,
                    created_at=NOW,
                    updated_at=NOW,
                    project_id=f"proj-{i % 3}" if i % 2 == 0 else None,
                )
            )
        # prefs rows (deterministic pinned_at)
        s.add(
            InstanceUiPrefs(
                instance_id=IDS[0],
                pinned=True,
                pinned_at=NOW,
                color_tag="red",
                icon_tag="star",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        s.add(
            InstanceUiPrefs(
                instance_id=IDS[1],
                pinned=False,
                pinned_at=None,
                color_tag="blue",
                icon_tag=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        s.commit()


class _ManagerStandin:
    def __init__(self, engine: Engine):
        self._engine = engine
        self._instance_ui_prefs_repo = InstanceUiPrefsRepository(engine=engine)

    @property
    def is_write_paused(self) -> bool:
        return False

    @staticmethod
    def _row_dict(row: Instance) -> dict[str, Any]:
        return {
            "instance_id": row.instance_id,
            "agent_id": row.agent_id,
            "agent_dir": row.agent_dir,
            "status": row.status,
            "parent_id": row.parent_id,
            "title": row.title,
            "initiative_message": row.initiative_message,
            "children": [],
            "metadata": dict(row.instance_metadata or {}),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "project_id": row.project_id,
        }

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
    ) -> tuple[list[dict[str, Any]], int, bool]:
        with Session(self._engine) as s:
            rows = s.exec(select(Instance).order_by(Instance.created_at)).all()
        sliced = rows[offset : offset + limit]
        return [self._row_dict(r) for r in sliced], len(rows), False

    def get_instance_info(self, instance_id: str) -> dict[str, Any]:
        with Session(self._engine) as s:
            row = s.get(Instance, instance_id)
        if row is None:
            raise KeyError(instance_id)
        return self._row_dict(row)

    async def get_queue_stats(self, instance_id: str) -> dict[str, Any]:
        return {}


@pytest_asyncio.fixture
async def client(engine: Engine):
    _seed(engine)
    manager = _ManagerStandin(engine=engine)
    app.state.manager = manager
    app.state.start_time = 1000.0

    from daemon.services import db_offload as _db_offload

    _db_offload.configure(loop=asyncio.get_running_loop())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, manager


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _serialize_envelope(resp: httpx.Response) -> str:
    lines = [f"status: {resp.status_code}"]
    lines += [
        f"{k.decode('ascii')}: {v.decode('ascii')}"
        for k, v in sorted(resp.headers.raw)
    ]
    lines.append(f"body: {resp.content!r}")
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_instances_list_response_bytes_match_golden(engine, client):
    ac, manager = client
    resp = await ac.get("/api/instances")
    assert resp.status_code == 200, resp.text
    actual = _serialize_envelope(resp)
    golden = GOLDEN_PATH.read_text(encoding="utf-8")
    assert actual == golden, (
        "GET /api/instances envelope drifted from the committed golden "
        f"({GOLDEN_PATH.name}). If the drift is INTENTIONAL, regenerate "
        "the golden from the base tree and re-record provenance."
    )
    # (b) no mcp_tool_names anywhere in the body
    assert "mcp_tool_names" not in resp.text


@pytest.mark.asyncio
async def test_instances_list_no_revalidation_pass(engine, client):
    """With FastAPI's response-model handling patched to raise, the route
    still returns 200 with identical bytes (no revalidation pass).

    ``fastapi.routing.serialize_response`` is the function FastAPI calls
    ONLY on the non-Response (bare-model) path; returning a ``Response``
    bypasses it entirely. Patching it to raise proves the route no longer
    goes through that pass. (Patching ``InstanceListResponse.model_validate``
    alone is insufficient: FastAPI validates via pydantic's core validator,
    not the ``model_validate`` classmethod, so that patch is vacuous.)"""
    import fastapi.routing as _fr

    ac, manager = client
    golden = GOLDEN_PATH.read_text(encoding="utf-8")

    original = _fr.serialize_response

    async def _boom(*a, **k):
        raise RuntimeError("serialize_response must not be called on the response path")

    _fr.serialize_response = _boom
    try:
        resp = await ac.get("/api/instances")
    finally:
        _fr.serialize_response = original

    assert resp.status_code == 200, resp.text
    actual = _serialize_envelope(resp)
    assert actual == golden, (
        "no-revalidation path produced different bytes than the golden"
    )
