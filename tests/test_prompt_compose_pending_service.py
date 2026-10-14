"""F5 real-SERVICE lane — compose-on-pending through PromptVersionService.

Fix-cycle F5 (2026-10-14, dispatcher-mandated test shape): "submit
v1 (pending) → submit v2 (chained) → approve v2 → assert delta₁ AND
delta₂ both present in effective content. Real service+repo, no
mocks."

The repo-level coverage of this chain lives in
``tests/test_prompt_version_resolution.py`` (``TestCreateAndSubmit``,
parametrized sqlite+postgres, landed with the F5 fix in commit
``5bfe1b308``). THIS file runs the identical chain through the
``PromptVersionService`` async facade — the layer the dispatcher's
mandate names ("Real service+repo"). That lane matters beyond
duplication: the fix-cycle's call-form finding (F2) showed the
service facade is where async/sync shape bugs hide (the
``asyncio.to_thread``-around-async-facade bug class), so the
service-level chain pins the composite end-to-end at the layer
``inner_soul`` / ``agent_mother`` / the HTTP router actually call.

Fence: file-SQLite under ``tempfile.mktemp`` (no live-shaped
paths), WAL + busy timeout — mirrors the established
``_sqlite_repo_engine`` fixture pattern from
``tests/test_prompt_approval_service.py``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import SQLModel
from fastapi.testclient import TestClient  # noqa: F401 — pattern parity

from daemon.repositories.prompt_version.models import (
    PromptAuditEvent,
    PromptVersion,
)
from daemon.repositories.prompt_version.repository import (
    AUTH_METHOD_SESSION,
    ActorContext,
    PromptVersionRepository,
    actor_from_instance,
)
from daemon.services.prompt_version_service import PromptVersionService


@pytest.fixture
def service():
    """Real repo + real async service over a fresh file-SQLite DB."""
    _ = (PromptVersion, PromptAuditEvent)  # SQLModel.metadata registration
    db_path = tempfile.mktemp(suffix=".db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    try:
        yield PromptVersionService(PromptVersionRepository(engine))
    finally:
        engine.dispose()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + ext)
            except FileNotFoundError:
                pass


_SESSION_ACTOR = ActorContext(
    subject="operator-cli", auth_method=AUTH_METHOD_SESSION,
)


def _internal_actor():
    return actor_from_instance("inst-f5-service-lane", "developer", "")


class TestF5ServiceLaneComposeOnPending:
    """F5 mandate: both deltas survive the pending window, served
    through the SERVICE's get_effective after approval."""

    def test_submit_chain_approve_both_deltas_in_effective(
        self, service: PromptVersionService
    ):
        # ── submit v1 (mutator, lands pending_approval) ──
        v1 = asyncio.run(service.create_and_submit(
            "developer", "", "soul",
            lambda old: (old or "") + "\nDELTA1",
            _internal_actor(),
            file_fallback="TEMPLATE",
        ))
        assert v1.state == "pending_approval"
        assert v1.content == "TEMPLATE\nDELTA1"

        # ── submit v2 (chained mutator; archives v1) ──
        v2 = asyncio.run(service.create_and_submit(
            "developer", "", "soul",
            lambda old: (old or "") + "\nDELTA2",
            _internal_actor(),
            file_fallback="TEMPLATE",
        ))
        assert v2.state == "pending_approval"
        # THE F5 assertion: v2 composes on the superseded pending
        # v1's content — NOT on get_effective (file_fallback
        # "TEMPLATE" alone), which would lose DELTA1.
        assert v2.content == "TEMPLATE\nDELTA1\nDELTA2", (
            f"F5 service lane: v2 must chain on the prior pending "
            f"row's content; got {v2.content!r}"
        )

        # ── approve v2 through the SERVICE (session lane) ──
        approved = asyncio.run(service.approve(v2.version_id, _SESSION_ACTOR))
        assert approved.state == "approved"

        # ── effective content carries BOTH deltas ──
        effective = asyncio.run(service.get_effective("developer", "", "soul"))
        assert effective == "TEMPLATE\nDELTA1\nDELTA2", (
            f"F5 service lane: approval must retain delta1 — the "
            f"C4 approval-as-content-regression; got {effective!r}"
        )
        assert "DELTA1" in effective and "DELTA2" in effective

        # v1 stays archived (≤ 1 active pending per key preserved).
        v1_after = asyncio.run(service.get_version(v1.version_id))
        assert v1_after.state == "archived"
