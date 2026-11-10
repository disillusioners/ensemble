"""F-10 — the REST dual-column path, END-TO-END through the REAL store.

WHAT THIS FILE PINS (and what the sibling file does NOT).

``tests/unit/routers/test_skill_patch_dual_column.py`` drives the REAL
router with a RECORDING STORE DOUBLE.  That proves the ROUTER forwards
both columns — it does NOT prove that a real database ends up with both
columns, because the double never touches a database.  A router that
forwarded both keys to a store that silently persisted only one of them
would still pass the sibling file.

This file closes that gap: the REAL ``SkillStoreService`` over the REAL
``SkillRepository`` over a REAL (file-backed) SQLite database, driven
through the REAL ``PUT /api/skills/{skill_id}`` route over a REAL
``TestClient``.  The assertions read the pair back through a BRAND-NEW
engine and session on the same file, so what is asserted is the
PERSISTED row — not the response body and not the ORM identity map of
the session that wrote it.

THE INVARIANT.

``skills.is_active`` and ``skills.status`` are TWO DESYNCABLE COLUMNS
(a real production row carries ``(False, 'ab_testing')``).  The
sanctioned REST path must therefore never move one of the pair without
the other:

* when the payload carries ``is_active``, BOTH columns move;
* when the payload does NOT carry ``is_active``, NEITHER moves — and
  ``status`` is not an accepted field at all, so a caller cannot move
  it alone even by trying.

The second half is the part the sibling file cannot express, and the
part that would be a FINDING if it were violated: a one-column write of
the pair IS expressible via ``SkillRepository.update`` (it takes
arbitrary ``**fields``), so the REST route's guarantee is what keeps the
public path safe.  Node 2 asserts the route's ACTUAL guarantee.

MUTATION LEG (pack ``skill_dual_column_deactivation_unit_test``):
``M-I5-a`` restores the old pop-and-translate shape in
``daemon/routers/skills.py`` — popping ``is_active`` and writing only
``status`` — and node 1 REDs (the ``is_active`` column never moves) and
node 3 REDs (the repository call carries one column, not the pair).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from daemon.repositories.skill.models import (  # noqa: F401 — create_all needs them
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)
from daemon.repositories.skill.repository import (
    SkillLineageRepository,
    SkillRepository,
)
from daemon.services.skill_store_service import SkillStoreService


class _NoEmbeddingService:
    """A tripwire for the embedding refresh — NOT a behavioural double.

    ``SkillStoreService.update_skill`` refreshes the embedding cache only
    when the ``content`` field changes.  This file never sends ``content``,
    so the refresh must never fire; if a future change put it on the
    single-column path, the ``AssertionError`` makes that loud instead of
    silently reaching a network provider from a unit test.
    """

    async def update_skill_embeddings(self, skill: object) -> int:
        raise AssertionError(
            "the single-column PATCH path must not refresh embeddings"
        )


class _CallSpy:
    """A PASS-THROUGH wrapper over the REAL repository.

    Every attribute is the REAL repository's (``__getattr__`` delegates);
    only ``update`` is intercepted, and it delegates to the real
    ``SkillRepository.update`` so the SQLModel write is the real one.  The
    spy exists to observe the repository BOUNDARY — how many calls, and
    which columns each carried.
    """

    def __init__(self, inner: SkillRepository) -> None:
        self._inner = inner
        self.calls: list[dict] = []

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def update(self, skill_id: str, **fields):
        self.calls.append(dict(fields))
        return self._inner.update(skill_id, **fields)


def _app_with(store: SkillStoreService) -> TestClient:
    """The house app shape (mirrors the sibling doubles file)."""
    from daemon.routers import skills as skills_module

    app = FastAPI()
    app.state.manager = MagicMock(is_write_paused=False)
    app.include_router(skills_module.router, prefix="/api")
    app.dependency_overrides[skills_module.get_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


def _read_pair_fresh(db_path: Path, skill_id: str) -> tuple[bool, str]:
    """Read the pair through a BRAND-NEW engine + session on the same file.

    A fresh engine opens a new connection to the on-disk SQLite file, so
    the values observed here are PERSISTED values — not the ORM identity
    map of the session that performed the write, and not the response
    body.
    """
    fresh_engine = create_engine(f"sqlite:///{db_path}")
    try:
        with Session(fresh_engine) as session:
            row = session.get(Skill, skill_id)
            assert row is not None, "the row must exist in the database"
            return bool(row.is_active), str(row.status)
    finally:
        fresh_engine.dispose()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """A FILE-backed SQLite path (a fresh engine can reopen it)."""
    return tmp_path / "skills.db"


@pytest.fixture()
def engine(db_path: Path):
    """The real engine over the file-backed database."""
    eng = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _fence_data_dir(tmp_path: Path, monkeypatch) -> None:
    """Keep any incidental data-dir write inside the test's tmp_path.

    The E4 receipt mechanism resolves its data dir through
    ``ENSEMBLE_DATA_DIR > DATA_DIR > ./data``; a stray write would land
    in the checkout's real ``data/`` tree.  The route under test does not
    touch it, and this fence makes that a guarantee rather than a hope.
    """
    monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture()
def skill_repo(engine) -> SkillRepository:
    """The REAL repository over the real engine."""
    return SkillRepository(engine)


@pytest.fixture()
def store(engine, skill_repo) -> SkillStoreService:
    """The REAL store service over the REAL repository."""
    return SkillStoreService(
        skill_repo=skill_repo,
        lineage_repo=SkillLineageRepository(engine),
        embedding_service=_NoEmbeddingService(),
    )


@pytest.fixture()
def client(store) -> TestClient:
    """A REAL client over the REAL store."""
    return _app_with(store)


@pytest.fixture()
def skill(skill_repo):
    """One ACTIVE row — the row the route is asked to deactivate."""
    return skill_repo.create(
        name="f10-dual-column",
        description="the description",
        content="the content",
        project_id="f10-project",
    )


class TestTheRestPathLandsBothColumns:
    """F-10 — the pair moves together, and it is PERSISTED that way."""

    def test_a_single_column_patch_lands_both_columns_in_the_database(
        self, client, db_path, skill
    ) -> None:
        """PUT ``{"is_active": false}`` moves BOTH columns — provably persisted.

        The read-back goes through a NEW engine + session on the same file,
        so the assertion is about the DATABASE, not the response body.
        """
        response = client.put(f"/api/skills/{skill.id}", json={"is_active": False})
        assert response.status_code == 200, response.text

        is_active, status = _read_pair_fresh(db_path, skill.id)
        assert is_active is False, (
            "the is_active COLUMN never moved — the row is left in the "
            "(False, 'active') mirror desync"
        )
        assert status == "inactive", (
            "the status column did not follow the flag — the row is left "
            "desynced"
        )

    def test_the_pair_reaches_the_repository_in_one_call(
        self, engine, db_path, skill
    ) -> None:
        """ONE repository call, carrying BOTH columns of the pair.

        The old pop-and-translate shape forwarded ``status`` alone, so the
        ``is_active`` column never moved.  This node pins the call shape
        at the repository boundary.
        """
        spy = _CallSpy(SkillRepository(engine))
        spied_store = SkillStoreService(
            skill_repo=spy,
            lineage_repo=SkillLineageRepository(engine),
            embedding_service=_NoEmbeddingService(),
        )
        spied_client = _app_with(spied_store)

        response = spied_client.put(
            f"/api/skills/{skill.id}", json={"is_active": False}
        )
        assert response.status_code == 200, response.text

        assert len(spy.calls) == 1, (
            f"the pair must land in ONE repository call, got {len(spy.calls)}"
        )
        forwarded = spy.calls[0]
        assert "is_active" in forwarded, (
            "the repository call must carry the is_active column — popping "
            "it writes only status and never moves the column"
        )
        assert forwarded.get("status") == "inactive", (
            "the repository call must carry the paired status"
        )

    def test_the_sanctioned_path_cannot_move_one_column_alone(
        self, client, db_path, skill
    ) -> None:
        """NO payload can move one column of the pair without the other.

        Three attempts, each read back from the database:

        1. ``{"status": "inactive"}`` — ``status`` is NOT an accepted field
           on ``SkillUpdateRequest``, so the attempt moves NEITHER column.
        2. ``{"is_active": false, "status": "active"}`` — the caller's
           ``status`` is not an accepted field either; the flag decides and
           the row lands canonical.
        3. ``{"description": ...}`` — a payload without ``is_active`` leaves
           the pair untouched.
        """
        # 1 — a status-only write is not expressible.
        response = client.put(
            f"/api/skills/{skill.id}", json={"status": "inactive"}
        )
        assert response.status_code == 200, response.text
        assert _read_pair_fresh(db_path, skill.id) == (True, "active"), (
            "a payload carrying only 'status' moved the pair — the route "
            "must not accept a one-column write of the pair"
        )

        # 2 — a conflicting status alongside the flag is ignored; the flag
        #     decides and BOTH columns land canonical.
        response = client.put(
            f"/api/skills/{skill.id}",
            json={"is_active": False, "status": "active"},
        )
        assert response.status_code == 200, response.text
        assert _read_pair_fresh(db_path, skill.id) == (False, "inactive"), (
            "a caller-supplied status must not decouple the pair from the "
            "flag"
        )

        # 3 — a payload without the flag leaves the pair untouched.
        response = client.put(
            f"/api/skills/{skill.id}", json={"description": "renamed"}
        )
        assert response.status_code == 200, response.text
        assert _read_pair_fresh(db_path, skill.id) == (False, "inactive"), (
            "a payload without is_active moved the pair"
        )
