"""Tests for the Snapshots read-only HTTP surface (snapshot-uiux v1).

26 unique cases per ``be-plan.md`` §8.1 — pins the HTTP wire contract
that the new ``/api/snapshots*`` endpoints expose:

* Route registration + envelope shape (cases 1, 14, 24).
* Pagination + sort (cases 2, 3, 4, 26).
* Filter pins: project_id, agent, status, tags, created window
  (cases 5-13).
* Detail + digest opt-in (cases 15, 16, 17, 18).
* Metrics surface + degraded paths (cases 19, 20, 25).
* Query-param validation (case 21).
* Deprecated proxy path (case 22).
* Settings toggle regression (case 23 = §8.6 pin — counted ONCE in the
  package-wide rollup, per sequencing §4.1 identity note).

Harness shape
-------------

* File-backed SQLite at ``tmp_path`` (NullPool + WAL + busy_timeout).
  NO ``PRAGMA foreign_keys=ON`` — matches the existing
  ``tests/unit/test_snapshot_repository.py:60-76`` (verified; the
  snapshots tables have no FK constraints that need enabling for v1;
  copying the missions-router pragma would force this test surface
  to satisfy FKs that other repo tests ignore).
* BOTH ``snapshots_router`` AND ``settings_router`` mounted under
  ``/api``. Cases 22-23 hit ``/api/settings/*`` paths; without that
  mount the fixture returns 404 for every settings path.
* A real ``SnapshotRepository`` + a real ``SnapshotMetricsService``
  wired to the engine; the FastAPI ``app.state.manager`` is a tiny
  stub exposing the two attributes the router reads
  (``_snapshot_repo``, ``_snapshot_metrics_service``).
* Case 23 requires a system-default project row (the
  ``GET /api/settings/snapshot-create`` handler calls
  ``get_project_repository()`` which raises 503 when the global
  ``_project_repo`` is ``None``). The ``project_repo`` fixture
  seeds one row and calls ``set_project_repository(repo)``.

Identity note (sequencing §4.1, amendment pass 3, fix #6):
case 23 IS the settings toggle regression pin — NOT a separate
case. Package-wide total: 26 router + 10 new-repo + 7 repo-ext + 1
search-ext = 44 unique BE cases.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

# Register the snapshot tables on import (so SQLModel.metadata.create_all
# builds them).
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.snapshot.models  # noqa: F401

from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.repositories.snapshot.models import (
    SNAPSHOT_STATUS_ACTIVE,
    SNAPSHOT_STATUS_FAILED,
    SNAPSHOT_STATUS_RUNNING,
    Snapshot,
)
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.routers.settings import router as settings_router
from daemon.routers.settings import set_project_repository
from daemon.routers.snapshots import router as snapshots_router
from daemon.services.snapshot_metrics_service import SnapshotMetricsService


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    NO ``PRAGMA foreign_keys=ON`` — matches the existing
    ``tests/unit/test_snapshot_repository.py:60-76`` (verified; the
    snapshots tables have no FK constraints that need enabling for
    v1).
    """
    db_path = tmp_path / "snapshots-api-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        # NOTE: NO foreign_keys pragma — matches the existing
        # test_snapshot_repository.py:60-76 engine fixture. Verified.
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def snapshot_repo(engine: Engine) -> SnapshotRepository:
    return SnapshotRepository(engine)


@pytest.fixture
def snapshot_metrics_service(
    engine: Engine,
) -> SnapshotMetricsService:
    return SnapshotMetricsService(engine=engine)


@pytest.fixture
def project_repo(engine: Engine) -> SQLModelProjectRepository:
    """Project repository wired to the engine + a seeded system-default project.

    Cases 22-23 hit ``/api/settings/*`` paths. The
    ``GET /api/settings/snapshot-create`` handler
    (``daemon/routers/settings.py:657-691``) calls
    ``get_project_repository()`` which raises 503 when
    ``_project_repo is None``. This fixture seeds the
    system-default project row AND calls
    ``set_project_repository(repo)`` so the dependency resolves.
    """
    repo = SQLModelProjectRepository(engine)
    repo.ensure_system_default_project()
    set_project_repository(repo)
    try:
        yield repo
    finally:
        set_project_repository(None)  # teardown — avoid global state leak


@pytest.fixture
def client(
    snapshot_repo: SnapshotRepository,
    snapshot_metrics_service: SnapshotMetricsService,
    project_repo: SQLModelProjectRepository,
) -> TestClient:
    """TestClient with BOTH routers mounted under /api.

    Mounts ``snapshots_router`` (for the new endpoints) AND
    ``settings_router`` (for cases 22-23, which hit
    ``/api/settings/*``). Mirrors the api.py registration
    (``/api`` parent prefix) and the manager wiring
    (``app.state.manager._snapshot_repo``,
    ``app.state.manager._snapshot_metrics_service``).

    The ``project_repo`` fixture (above) MUST run before this fixture
    so that ``_project_repo`` is wired globally before any
    ``get_project_repository()`` call inside the settings router
    fires — pytest resolves fixtures in dependency order.
    """
    app = FastAPI()
    # Stub manager — exposes the two attributes the router depends on
    # (per be-plan §7: ``manager._snapshot_repo`` for read paths and
    # ``manager._snapshot_metrics_service`` for the metrics surface).
    class _StubManager:
        _snapshot_repo = snapshot_repo
        _snapshot_metrics_service = snapshot_metrics_service
    app.state.manager = _StubManager()
    app.include_router(snapshots_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")  # cases 22-23
    return TestClient(app)


# ─── Seed helpers ─────────────────────────────────────────────────────────


def _seed_snapshot(
    snapshot_repo: SnapshotRepository,
    *,
    project_id: str | None = None,
    agent: str = "coder",
    title: str = "snap-1",
    status: str = SNAPSHOT_STATUS_ACTIVE,
    tags: list[str] | None = None,
    created_at: str | None = None,
    task_summary: str = "did the thing",
) -> Snapshot:
    """Build + persist a minimal valid Snapshot row for tests."""
    row = Snapshot(
        project_id=project_id or str(uuid.uuid4()),
        created_by_agent_id=agent,
        target_instance_id=str(uuid.uuid4()),
        title=title,
        task_summary=task_summary,
        domain_tags=tags or [],
        status=status,
        repo_path="/repo",
        vcs_type="git",
        git_sha="abc1234",
        git_branch="latest",
        git_dirty=False,
        runtime_version="0.14.2",
        effective_model="cheap-model",
        digest={"task_summary_text": task_summary, "refs": {"commits": ["abc1234"]}},
    )
    if created_at is not None:
        row.created_at = created_at
    return snapshot_repo.create_with_embeddings(row)


# ─── Tests ────────────────────────────────────────────────────────────────


# 1 ────────────────────────────────────────────────────────────────────────


def test_list_envelope_default(client: TestClient):
    """Empty repo → ``{"items": [], "total": 0}`` (200)."""
    resp = client.get("/api/snapshots")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "total": 0}


# 2 ────────────────────────────────────────────────────────────────────────


def test_list_pagination(client: TestClient, snapshot_repo: SnapshotRepository):
    """5 rows seeded; ``?limit=2&offset=2`` → 2 items, ``total=5``."""
    for i in range(5):
        _seed_snapshot(snapshot_repo, title=f"snap-{i}")
    resp = client.get("/api/snapshots?limit=2&offset=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


# 3 ────────────────────────────────────────────────────────────────────────


def test_list_default_sort_created_at_desc(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """3 rows inserted in known order → reverse-order response."""
    _seed_snapshot(snapshot_repo, title="a", created_at="2026-10-05T01:00:00+00:00")
    _seed_snapshot(snapshot_repo, title="b", created_at="2026-10-05T02:00:00+00:00")
    _seed_snapshot(snapshot_repo, title="c", created_at="2026-10-05T03:00:00+00:00")
    body = client.get("/api/snapshots").json()
    assert [it["title"] for it in body["items"]] == ["c", "b", "a"]


# 4 ────────────────────────────────────────────────────────────────────────


def test_list_sort_allowlist_validation(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """``?sort=garbage`` → 422. All 8 allowed values accepted."""
    # Garbage rejects with 422.
    resp = client.get("/api/snapshots?sort=garbage")
    assert resp.status_code == 422

    # All 8 allowed values produce a 200.
    for sort_value in (
        "created_at_desc",
        "created_at_asc",
        "title_asc",
        "title_desc",
        "status_asc",
        "status_desc",
        "project_id_asc",
        "project_id_desc",
    ):
        resp = client.get(f"/api/snapshots?sort={sort_value}")
        assert resp.status_code == 200, f"sort={sort_value} should be 200"


# 5 ────────────────────────────────────────────────────────────────────────


def test_list_filter_project_id(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """Two projects seeded → ``?project_id=p1`` returns only p1 rows."""
    p1 = str(uuid.uuid4())
    p2 = str(uuid.uuid4())
    _seed_snapshot(snapshot_repo, project_id=p1, title="p1-a")
    _seed_snapshot(snapshot_repo, project_id=p1, title="p1-b")
    _seed_snapshot(snapshot_repo, project_id=p2, title="p2-a")

    body = client.get(f"/api/snapshots?project_id={p1}").json()
    assert body["total"] == 2
    assert {it["title"] for it in body["items"]} == {"p1-a", "p1-b"}


# 6 ────────────────────────────────────────────────────────────────────────


def test_list_filter_invalid_project_id(client: TestClient):
    """``?project_id=not-a-uuid`` → 400, body ``{"detail": "project_id must be a valid UUID"}``."""
    resp = client.get("/api/snapshots?project_id=not-a-uuid")
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"] == "project_id must be a valid UUID"


# 7 ────────────────────────────────────────────────────────────────────────


def test_list_filter_agent_exact_match(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """``?agent=coder`` returns only coder rows. ``?agent=code`` returns 0 (D4 — exact match)."""
    _seed_snapshot(snapshot_repo, agent="coder", title="by-coder")
    _seed_snapshot(snapshot_repo, agent="tester", title="by-tester")

    body_coder = client.get("/api/snapshots?agent=coder").json()
    assert body_coder["total"] == 1
    assert body_coder["items"][0]["title"] == "by-coder"

    # Exact match: ``code`` (a prefix of ``coder``) is NOT a match.
    body_code = client.get("/api/snapshots?agent=code").json()
    assert body_code["total"] == 0


# 8 ────────────────────────────────────────────────────────────────────────


def test_list_filter_status_multi(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """``?status=active&status=running`` returns both. ``?status=garbage`` → 422."""
    _seed_snapshot(snapshot_repo, status=SNAPSHOT_STATUS_ACTIVE, title="active-row")
    _seed_snapshot(
        snapshot_repo, status=SNAPSHOT_STATUS_RUNNING, title="running-row"
    )
    _seed_snapshot(snapshot_repo, status=SNAPSHOT_STATUS_FAILED, title="failed-row")

    body = client.get("/api/snapshots?status=active&status=running").json()
    assert body["total"] == 2
    assert {it["title"] for it in body["items"]} == {"active-row", "running-row"}

    # Garbage status → 422.
    resp = client.get("/api/snapshots?status=garbage")
    assert resp.status_code == 422


# 9 ────────────────────────────────────────────────────────────────────────


def test_list_filter_status_default_all(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """Omit ``status`` → all 5 statuses surface (D5)."""
    _seed_snapshot(snapshot_repo, status=SNAPSHOT_STATUS_ACTIVE, title="a")
    _seed_snapshot(snapshot_repo, status=SNAPSHOT_STATUS_RUNNING, title="r")
    _seed_snapshot(snapshot_repo, status=SNAPSHOT_STATUS_FAILED, title="f")
    body = client.get("/api/snapshots").json()
    assert body["total"] == 3
    assert {it["status"] for it in body["items"]} == {"active", "running", "failed"}


# 10 ───────────────────────────────────────────────────────────────────────


def test_list_filter_tags_all_mode(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """``?tags=kind:impl&tag_mode=all`` → row has BOTH tags."""
    _seed_snapshot(
        snapshot_repo,
        title="both",
        tags=["kind:implementation", "subsystem:upgrade-pipeline"],
    )
    _seed_snapshot(
        snapshot_repo,
        title="only-kind",
        tags=["kind:implementation"],
    )
    _seed_snapshot(
        snapshot_repo,
        title="neither",
        tags=["unrelated"],
    )
    body = client.get(
        "/api/snapshots?tags=kind:implementation&tags=subsystem:upgrade-pipeline&tag_mode=all"
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "both"


# 11 ───────────────────────────────────────────────────────────────────────


def test_list_filter_tags_any_mode(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """``?tags=a&tags=b&tag_mode=any`` → row has at least one."""
    _seed_snapshot(snapshot_repo, title="a-row", tags=["a", "x"])
    _seed_snapshot(snapshot_repo, title="b-row", tags=["b", "y"])
    _seed_snapshot(snapshot_repo, title="c-row", tags=["c"])
    body = client.get(
        "/api/snapshots?tags=a&tags=b&tag_mode=any"
    ).json()
    assert body["total"] == 2
    assert {it["title"] for it in body["items"]} == {"a-row", "b-row"}


# 12 ───────────────────────────────────────────────────────────────────────


def test_list_filter_tags_repeat_param(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """FastAPI's ``?tags=a&tags=b`` encoding accepted; both passed through."""
    _seed_snapshot(snapshot_repo, title="both", tags=["a", "b"])
    _seed_snapshot(snapshot_repo, title="only-a", tags=["a"])
    _seed_snapshot(snapshot_repo, title="only-b", tags=["b"])
    # Repeat-param form (the canonical encoding per D3).
    body = client.get(
        "/api/snapshots?tags=a&tags=b&tag_mode=all"
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "both"


# 13 ───────────────────────────────────────────────────────────────────────


def test_list_filter_created_window(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """``?created_after=...&created_before=...`` narrows; invalid ISO → 400."""
    _seed_snapshot(snapshot_repo, title="early", created_at="2026-10-01T00:00:00+00:00")
    _seed_snapshot(snapshot_repo, title="mid", created_at="2026-10-05T00:00:00+00:00")
    _seed_snapshot(snapshot_repo, title="late", created_at="2026-10-10T00:00:00+00:00")

    body = client.get(
        "/api/snapshots"
        "?created_after=2026-10-02T00:00:00%2B00:00"
        "&created_before=2026-10-08T00:00:00%2B00:00"
    ).json()
    assert {it["title"] for it in body["items"]} == {"mid"}

    # Invalid ISO → 400.
    resp = client.get("/api/snapshots?created_after=banana")
    assert resp.status_code == 400


# 14 ───────────────────────────────────────────────────────────────────────


def test_list_digest_excluded(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """List payload has NO ``digest`` key (D7 — list payloads stay lean)."""
    _seed_snapshot(snapshot_repo, title="lean", task_summary="X")
    body = client.get("/api/snapshots").json()
    item = body["items"][0]
    # D7: list strips digest entirely (not even the empty shape).
    assert "digest" not in item


# 15 ───────────────────────────────────────────────────────────────────────


def test_detail_default_no_digest(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """``GET /api/snapshots/{id}`` returns ``digest={}`` (D7)."""
    row = _seed_snapshot(snapshot_repo, title="detail-row", task_summary="text")
    resp = client.get(f"/api/snapshots/{row.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == row.id
    assert body["digest"] == {}


# 16 ───────────────────────────────────────────────────────────────────────


def test_detail_with_include_digest(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """``GET /api/snapshots/{id}?include=digest`` returns the full digest."""
    row = _seed_snapshot(snapshot_repo, title="with-digest", task_summary="text")
    resp = client.get(f"/api/snapshots/{row.id}?include=digest")
    assert resp.status_code == 200
    body = resp.json()
    # The full digest is returned (not the empty shape).
    assert body["digest"] != {}
    assert body["digest"]["task_summary_text"] == "text"


# 17 ───────────────────────────────────────────────────────────────────────


def test_detail_404_shape(client: TestClient):
    """Unknown id → 404 with ``{"detail": {"error": "Snapshot not found", "snapshot_id": "..."}}``."""
    missing_id = str(uuid.uuid4())
    resp = client.get(f"/api/snapshots/{missing_id}")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error"] == "Snapshot not found"
    assert body["detail"]["snapshot_id"] == missing_id


# 18 ───────────────────────────────────────────────────────────────────────


def test_detail_invalid_uuid(client: TestClient):
    """Blank / non-UUID path → 400."""
    # Non-UUID: rejected by the _validate_uuid helper.
    resp = client.get("/api/snapshots/not-a-uuid")
    assert resp.status_code == 400
    assert "snapshot_id must be a valid UUID" in resp.json()["detail"]


# 19 ───────────────────────────────────────────────────────────────────────


def test_metrics_endpoint(
    client: TestClient,
    snapshot_metrics_service: SnapshotMetricsService,
):
    """Seed 1 capture counter + 1 spawn counter; ``GET /metrics`` returns both."""
    snapshot_metrics_service.inc_capture("coder")
    snapshot_metrics_service.inc_capture("coder")
    snapshot_metrics_service.inc_spawn("snap-001")

    body = client.get("/api/snapshots/metrics").json()
    assert body["capture_counts"]["coder"]["created"] == 2
    spawns = body["spawn_counts_per_snapshot"]
    assert any(s["snapshot_id"] == "snap-001" and s["count"] == 1 for s in spawns)


# 20 ───────────────────────────────────────────────────────────────────────


def test_metrics_degraded_engine_none(
    client: TestClient,
    snapshot_metrics_service: SnapshotMetricsService,
):
    """``manager._snapshot_metrics_service = None`` → 200 empty shape, NOT 503."""
    # Monkey-patch the stub manager to simulate a not-yet-wired state.
    app = client.app
    app.state.manager._snapshot_metrics_service = None  # type: ignore[attr-defined]

    resp = client.get("/api/snapshots/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"capture_counts": {}, "spawn_counts_per_snapshot": []}


# 21 ───────────────────────────────────────────────────────────────────────


def test_limit_offset_422_validation(client: TestClient):
    """``?limit=10000`` and ``?offset=-5`` both reject with 422 (D-6 + D-6).

    FastAPI's ``Query(ge=…, le=…)`` fires FIRST (422); the runtime
    double-clamp idiom is a defense-in-depth belt for direct repo
    callers, NOT a 200-success path on the HTTP surface.
    """
    # limit > le=200 → 422.
    resp = client.get("/api/snapshots?limit=10000")
    assert resp.status_code == 422

    # offset < ge=0 → 422.
    resp = client.get("/api/snapshots?offset=-5")
    assert resp.status_code == 422


# 22 ───────────────────────────────────────────────────────────────────────


def test_deprecated_old_metrics_path(client: TestClient):
    """``GET /api/settings/snapshot-usage-metrics`` → 200 + manual deprecation headers.

    The handler emits the three headers manually (FastAPI's
    ``deprecated=True`` alone emits nothing — verified by read of
    ``daemon/routers/blueprints.py:551-557``): ``Deprecation``,
    ``Sunset``, and ``Link rel="successor-version"`` pointing at
    ``/api/snapshots/metrics``.
    """
    resp = client.get("/api/settings/snapshot-usage-metrics")
    assert resp.status_code == 200
    # The deprecation signal headers are present.
    assert resp.headers.get("Deprecation") == "true"
    assert "Sunset" in resp.headers
    link_header = resp.headers.get("Link", "")
    assert 'rel="successor-version"' in link_header
    assert "/api/snapshots/metrics" in link_header


# 23 ───────────────────────────────────────────────────────────────────────


def test_settings_toggle_regression(client: TestClient):
    """``GET /api/settings/snapshot-create`` + ``PUT /api/settings/snapshot-create`` still work.

    Regression pin — the toggle endpoints stay in
    ``daemon/routers/settings.py:657-691`` (FE scope moves the UI;
    BE scope leaves the endpoints unchanged). The plan's case 23
    identity note: this IS the §8.6 regression pin, NOT a separate
    case.
    """
    # GET — read the current toggle (default OFF).
    resp_get = client.get("/api/settings/snapshot-create")
    assert resp_get.status_code == 200
    body_get = resp_get.json()
    assert "enabled" in body_get
    assert body_get["enabled"] is False  # fail-closed default

    # PUT — flip it on.
    resp_put = client.put(
        "/api/settings/snapshot-create", json={"enabled": True}
    )
    assert resp_put.status_code == 200
    assert resp_put.json()["enabled"] is True

    # GET again — confirm persistence.
    resp_get2 = client.get("/api/settings/snapshot-create")
    assert resp_get2.status_code == 200
    assert resp_get2.json()["enabled"] is True

    # PUT — flip it back off (test hygiene).
    resp_put_off = client.put(
        "/api/settings/snapshot-create", json={"enabled": False}
    )
    assert resp_put_off.status_code == 200
    assert resp_put_off.json()["enabled"] is False


# 24 ───────────────────────────────────────────────────────────────────────


def test_list_excludes_task_summary(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """List payload has NO ``task_summary`` key (sequencing §1 addendum A-2).

    The detail endpoint still carries the field.
    """
    row = _seed_snapshot(snapshot_repo, title="row", task_summary="bm25-corpus-text")
    list_body = client.get("/api/snapshots").json()
    assert "task_summary" not in list_body["items"][0]

    # Detail carries it.
    detail_body = client.get(f"/api/snapshots/{row.id}").json()
    assert detail_body["task_summary"] == "bm25-corpus-text"


# 25 ───────────────────────────────────────────────────────────────────────


def test_metrics_degraded_surface_raises(
    client: TestClient,
):
    """A service whose ``.surface()`` raises → 200 empty shape, NOT 500.

    Mirrors the ``daemon/routers/settings.py:737`` fail-soft
    precedent; complements case 20's None-service branch.
    """
    class _RaisingService:
        async def surface(self) -> dict[str, Any]:
            raise RuntimeError("metrics store down")

    app = client.app
    app.state.manager._snapshot_metrics_service = _RaisingService()  # type: ignore[attr-defined]

    resp = client.get("/api/snapshots/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"capture_counts": {}, "spawn_counts_per_snapshot": []}


# 26 ───────────────────────────────────────────────────────────────────────


def test_list_sort_created_at_desc_1s_apart(
    client: TestClient, snapshot_repo: SnapshotRepository
):
    """Two rows seeded 1 second apart → ``created_at_desc`` is chronological.

    Pins the lexicographic-ISO-8601 = chronological claim (R4 / R10):
    the schema is fixed and only ``_now_iso`` writes the column, so
    zero-padded ISO-8601 strings with the same timezone offset
    order chronologically.
    """
    _seed_snapshot(
        snapshot_repo, title="older", created_at="2026-10-05T01:00:00.000000+00:00"
    )
    _seed_snapshot(
        snapshot_repo, title="newer", created_at="2026-10-05T01:00:01.000000+00:00"
    )
    body = client.get("/api/snapshots?sort=created_at_desc").json()
    assert [it["title"] for it in body["items"]] == ["newer", "older"]
