"""Job-scoped answer-target resolution — the CHILD-ASKER defect class.

Commission (2026-10-19): the job-addressed answer surfaces routed the
answer at ``WorkRecord.instance_id`` — the work unit's OWNING instance.
For a MISSION-SCOPED ``work_id`` that is the mission ROOT, even when the
question was asked by a CHILD instance. The answer then landed on an
instance that never held the pack, and the shared helper raised a 410
``QUESTION_PACK_LOST`` whose message blamed a "daemon restart without a
durable shadow" — a claim that was false in the wrong-instance class (the
pack was alive and durable on the child).

The fix routes the answer at the ASKING instance, resolved from the
mission subtree by ONE shared resolver
(``daemon/services/answer_resolver.py``) used by BOTH job-addressed
surfaces:

* ``POST /api/jobs/{work_id}/answer`` (``daemon/routers/jobs_management.py``)
* the ``job_answer`` agent tool (``daemon/tools/job_queue.py``)

The instance-addressed surface (``POST /api/instances/{id}/answer``) is
deliberately NOT routed through the resolver — the caller names the
instance, so there is nothing to resolve. Its behaviour is pinned here
too (it must stay unchanged).

Harness: reuses the real-repo harness + seed helpers of
``tests/job_queue/test_job_answer_tool.py`` (the closest precedent —
same component set: real repos, real ``QuestionManager``, real
``EventBus``, real ``WorkResolverService``), plus the bare-app router
mount pattern of ``tests/job_queue/test_midflight_qa.py``.

All fixtures are in-memory SQLite — no live-shaped path is ever
resolved (the fenced-worktree rule: test scripts must NEVER resolve
HOME-derived install dirs).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.models.common import ErrorCodes
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.task.models import TaskStatus
from daemon.services.question_manager import pack_to_dict

# Reuse the harness + seed helpers of the closest sibling suite (the
# repo already imports across test modules — e.g. the e2e suites import
# from ``tests.e2e.test_e2e_workflows``). ``engine``/``harness`` are
# pytest fixtures; importing them into this module's namespace makes
# them resolvable here AND shadows the session-scoped ``engine`` in
# ``tests/job_queue/conftest.py`` with the function-scoped one (same
# shadowing the sibling module relies on).
from tests.job_queue.test_job_answer_tool import (  # noqa: F401
    _make_job_answer_tool,
    _seed_instance,
    _seed_task,
    engine,
    harness,
)

PROJECT_ID = "p"


# =============================================================================
# Seed helpers — mission tree + asking child
# =============================================================================


def _seed_mission_root(harness, *, project_id: str = PROJECT_ID) -> str:
    """Seed the mission ROOT (the work unit's owning instance)."""
    return _seed_instance(
        harness.engine,
        status=InstanceStatus.PAUSED.value,
        project_id=project_id,
        agent_id="leader",
    )


def _seed_asking_child(
    harness,
    parent_id: str,
    *,
    project_id: str = PROJECT_ID,
    with_pack: bool = True,
    with_handle: bool = True,
) -> tuple[str, object | None]:
    """Seed a CHILD instance that ASKED a question.

    The child owns the pending pack (RAM + the durable
    ``instance_metadata`` shadow) and carries the authoritative
    awaiting-answer handle (a ``paused`` task with
    ``suspension_reason='awaiting_answer'`` and a resume target) —
    exactly the state the ``question`` tool leaves behind.

    ``with_pack=False`` seeds the child with the handle but WITHOUT any
    pack (RAM + shadow) — the "genuinely lost pack" shape.

    ``with_handle=False`` seeds the child with the pack but WITHOUT the
    awaiting-answer handle — the PRE-PAUSE WINDOW: ``question_tools.py``
    stores the pack and stamps the durable shadow BEFORE the pause
    fires, so a child that asked a moment ago holds a live pending pack
    while no handle exists yet.

    Returns ``(child_id, pack_or_None)``.
    """
    child = _seed_instance(
        harness.engine,
        status=InstanceStatus.PAUSED.value,
        project_id=project_id,
        parent_id=parent_id,
        agent_id="devops",
    )
    if with_handle:
        # The authoritative awaiting-answer handle.
        _seed_task(harness.engine, child, status=TaskStatus.PAUSED.value)

    if not with_pack:
        return child, None

    pack = harness.qm.set_question_pack(
        child, [{"id": "q1", "text": "Approach A or B?"}]
    )
    harness.instance_repo.set_metadata(child, "question_pack_id", pack.id)
    harness.instance_repo.set_metadata(
        child, "question_pack_payload", pack_to_dict(pack)
    )
    return child, pack


def _seed_stale_pending_shadow(harness, instance_id: str) -> str:
    """Stamp a PENDING durable shadow on ``instance_id`` WITHOUT a live pack.

    The shadow's ``status`` is a FROZEN ask-time snapshot: the instance
    metadata can still say ``pending`` long after the turn moved on
    (the shadow is only cleared at answer consumption / dismiss). Such a
    stale shadow must never win the asker resolution — it would route
    the answer at an instance that is not waiting for one.

    Returns the shadowed pack id.
    """
    pack_id = str(uuid.uuid4())
    harness.instance_repo.set_metadata(instance_id, "question_pack_id", pack_id)
    harness.instance_repo.set_metadata(
        instance_id,
        "question_pack_payload",
        {
            "instance_id": instance_id,
            "id": pack_id,
            "status": "pending",
            "questions": [{"id": "q1", "text": "Stale question?"}],
            "answers": {},
        },
    )
    return pack_id


def _seed_mission_work(harness, root_id: str) -> str:
    """Seed the MISSION-SCOPED work unit (a task owned by the root).

    This is the geometry of the incident: the watcher holds a receipt
    of the mission (the root), while the question was asked by a child.
    """
    return _seed_task(
        harness.engine,
        root_id,
        status=TaskStatus.RUNNING.value,
    )


def _seed_stale_answered_pack(harness, root_id: str) -> object:
    """Leave a STALE ANSWERED RAM pack on the mission root.

    RAM packs SURVIVE answer consumption — only the durable
    ``instance_metadata`` shadow is cleared at consumption (the RAM
    store keeps the pack with ``status="answered"``). This is the
    geometry that broke the resolver's fast path: the root's stale pack
    short-circuited the resolution, the answer was routed at the root,
    the CAS lost, and the caller got a silent ``already_delivered``
    while the CHILD's live question was never answered.

    Returns the stale pack.
    """
    pack = harness.qm.set_question_pack(
        root_id, [{"id": "q1", "text": "Stale root question?"}]
    )
    # The durable shadow the create-site stamps.
    harness.instance_repo.set_metadata(root_id, "question_pack_id", pack.id)
    harness.instance_repo.set_metadata(
        root_id, "question_pack_payload", pack_to_dict(pack)
    )
    # The answer consumption: the pack flips to ``answered`` and the
    # durable shadow is cleared (midflight_qa.clear_question_pack_metadata).
    harness.qm.set_answers(root_id, {"q1": "stale answer"})
    harness.instance_repo.delete_metadata(root_id, "question_pack_id")
    harness.instance_repo.delete_metadata(root_id, "question_pack_payload")
    return pack


# =============================================================================
# Router mounts (bare-app pattern — tests/job_queue/test_midflight_qa.py)
# =============================================================================


@pytest.fixture
def jobs_client(harness):
    """TestClient over ONLY the jobs-management router."""
    from daemon.routers.jobs_management import router as management_router

    app = FastAPI()
    app.include_router(management_router)

    @app.middleware("http")
    async def _inject(request, call_next):
        request.app.state.manager = harness.manager
        request.app.state.live_hub = None
        return await call_next(request)

    return TestClient(app)


@pytest.fixture
def instances_client(harness):
    """TestClient over ONLY the instances router (instance-addressed lane)."""
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    from daemon.routers.instances import router as instances_router

    # The endpoints' existence check awaits ``manager.get_instance``.
    harness.manager.get_instance = AsyncMock(
        return_value=SimpleNamespace(instance_id="exists")
    )

    app = FastAPI()
    app.include_router(instances_router)

    @app.middleware("http")
    async def _inject(request, call_next):
        request.app.state.manager = harness.manager
        request.app.state.live_hub = None
        return await call_next(request)

    return TestClient(app)


# =============================================================================
# (a) Agent-tool lane — the answer lands on the CHILD asker
# =============================================================================


class TestJobAnswerLandsOnChildAsker:
    """(a) A child asked; the job-scoped work_id resolves to the mission
    root. The answer must reach the CHILD — not the root, and with NO
    false 410."""

    @pytest.mark.asyncio
    async def test_tool_answer_reaches_child_asker_not_mission_root(
        self, harness
    ):
        root = _seed_mission_root(harness)
        child, pack = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": pack.id,
            }
        )

        # The answer was delivered — no 410, no error string.
        assert "error" not in result, result.get("error")
        assert result["status"] == "answered"

        # The asker is the CHILD (the instance that actually holds the
        # pack), while the work unit still belongs to the mission root.
        assert result["instance_id"] == child
        assert result["answer_target"]["instance_id"] == child
        assert result["answer_target"]["work_instance_id"] == root
        assert result["answer_target"]["mission_root_id"] == root
        assert result["answer_target"]["route"] == "subtree_pack_id"

        # The child's pack flipped to ``answered``; the root holds none.
        assert harness.qm.get_question_pack(child).status == "answered"
        assert harness.qm.get_question_pack(root) is None

        # The resume targeted the CHILD (handle-first, then cascade).
        harness.manager.resume_processing_job.assert_awaited_once()
        assert harness.manager.resume_processing_job.await_args.args[0] == child
        harness.manager.resume_instance_cascade.assert_awaited_once_with(child)

    @pytest.mark.asyncio
    async def test_tool_answer_never_reports_question_pack_lost(
        self, harness
    ):
        """The old code answered the ROOT and reported the pack as LOST
        (410) even though it was alive and durable on the child. The
        error token must not appear on this geometry at all."""
        root = _seed_mission_root(harness)
        child, pack = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": pack.id,
            }
        )

        assert ErrorCodes.QUESTION_PACK_LOST.value not in str(result)
        assert ErrorCodes.NO_PENDING_QUESTION.value not in str(result)
        # The ROOT is named in the diagnostics (it is the work unit's
        # owner) but it is NOT the asker.
        assert result["instance_id"] != root
        assert result["instance_id"] == child

    @pytest.mark.asyncio
    async def test_tool_answer_resumes_child_from_paused(self, harness):
        """The asker is resumed (paused → running) as part of the answer
        delivery — the resume must be driven for the CHILD."""
        root = _seed_mission_root(harness)
        child, pack = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        resumed: list[str] = []

        async def _fake_cascade(instance_id: str):
            resumed.append(instance_id)
            return {
                "target_id": instance_id,
                "resumed_ids": [instance_id],
                "skipped_ids": [],
            }

        harness.manager.resume_instance_cascade = _fake_cascade

        tool = _make_job_answer_tool(harness)
        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": pack.id,
            }
        )

        assert result["status"] == "answered"
        assert resumed == [child]


# =============================================================================
# (b) HTTP route lane — the same geometry through POST /api/jobs/{id}/answer
# =============================================================================


class TestJobRouteLandsOnChildAsker:
    """(b) The HTTP job-addressed surface resolves the asker the same
    way — ONE shared resolver, BOTH surfaces."""

    def test_route_answer_reaches_child_asker(self, harness, jobs_client):
        root = _seed_mission_root(harness)
        child, pack = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={
                "answers": {"q1": "Approach A"},
                "question_pack_id": pack.id,
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "answered"
        assert body["instance_id"] == child
        assert body["answer_target"]["instance_id"] == child
        assert body["answer_target"]["work_instance_id"] == root
        assert body["answer_target"]["route"] == "subtree_pack_id"
        assert body["work_id"] == work_id

        assert harness.qm.get_question_pack(child).status == "answered"
        assert harness.qm.get_question_pack(root) is None

    def test_route_without_pack_id_resolves_via_answer_handle(
        self, harness, jobs_client
    ):
        """No ``question_pack_id`` supplied → the asker is found through
        the authoritative awaiting-answer handle (the only candidate in
        the subtree)."""
        root = _seed_mission_root(harness)
        child, pack = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == child
        assert body["answer_target"]["route"] == "subtree_answer_handle"


# =============================================================================
# (c) Two children with pending packs → the pack id disambiguates
# =============================================================================


class TestPackIdDisambiguatesMultipleAskers:
    """(c) Two children are waiting for an answer. The pack id selects
    the right one; without it the request is REFUSED (never guessed)."""

    @pytest.mark.asyncio
    async def test_pack_id_selects_the_right_child(self, harness):
        root = _seed_mission_root(harness)
        child_a, pack_a = _seed_asking_child(harness, root)
        child_b, pack_b = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": pack_b.id,
            }
        )

        assert "error" not in result, result.get("error")
        assert result["instance_id"] == child_b
        assert result["answer_target"]["route"] == "subtree_pack_id"

        # The OTHER child keeps its pending pack — the answer did not
        # land on the wrong asker.
        assert harness.qm.get_question_pack(child_b).status == "answered"
        assert harness.qm.get_question_pack(child_a).status == "pending"

    @pytest.mark.asyncio
    async def test_ambiguous_without_pack_id_is_refused(self, harness):
        """No pack id + two waiting askers → 400, naming the candidates.
        Picking one by recency would answer the WRONG question."""
        root = _seed_mission_root(harness)
        child_a, _pack_a = _seed_asking_child(harness, root)
        child_b, _pack_b = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        # The tool schema makes question_pack_id mandatory, so the
        # "no pack id" branch of the SHARED resolver is reached
        # directly — it is the same code both lanes call.
        from daemon.services.answer_resolver import resolve_answer_target

        target = resolve_answer_target(harness.manager, root, None)

        assert target.asker_instance_id is None
        assert target.error == "ambiguous"
        assert set(target.candidate_ids) == {child_a, child_b}
        assert target.subtree_scanned is True

        # A pack id that matches NEITHER child answers nobody — the
        # packs stay pending.
        tool = _make_job_answer_tool(harness)
        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": str(uuid.uuid4()),
            }
        )

        assert "error" in result
        assert harness.qm.get_question_pack(child_a).status == "pending"
        assert harness.qm.get_question_pack(child_b).status == "pending"

    def test_route_ambiguity_is_400_with_candidates(
        self, harness, jobs_client
    ):
        """The HTTP lane refuses the ambiguous request with a typed 400
        that names the candidate instances."""
        root = _seed_mission_root(harness)
        child_a, _pack_a = _seed_asking_child(harness, root)
        child_b, _pack_b = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == ErrorCodes.INVALID_REQUEST.value
        assert "question_pack_id" in detail["message"]
        assert child_a in detail["message"]
        assert child_b in detail["message"]
        assert set(detail["details"]["candidate_ids"]) == {child_a, child_b}
        assert detail["details"]["subtree_scanned"] is True

        # Nothing was answered.
        assert harness.qm.get_question_pack(child_a).status == "pending"
        assert harness.qm.get_question_pack(child_b).status == "pending"


# =============================================================================
# (d) Genuinely-missing pack → 410 with the ACCURATE cause
# =============================================================================


class TestGenuinelyMissingPackIsAccurate410:
    """(d) The asker WAS identified (unique awaiting-answer handle) but
    the pack is gone from both the memory store and the durable shadow
    → 410 ``QUESTION_PACK_LOST`` — with a message that names the
    instance + the pack id and makes NO false restart claim."""

    @pytest.mark.asyncio
    async def test_missing_pack_is_410_without_restart_claim(self, harness):
        root = _seed_mission_root(harness)
        child, _ = _seed_asking_child(harness, root, with_pack=False)
        work_id = _seed_mission_work(harness, root)
        missing_pack_id = str(uuid.uuid4())

        tool = _make_job_answer_tool(harness)

        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": missing_pack_id,
            }
        )

        assert "error" in result
        assert ErrorCodes.QUESTION_PACK_LOST.value in result["error"]
        assert "410" in result["error"]

        # The OLD wording asserted a daemon restart that never
        # happened — the message must not claim it any more.
        assert "daemon restart" not in result["error"]

        # The message names the resolved asker AND the pack id that was
        # searched.
        assert child in result["error"]
        assert missing_pack_id in result["error"]

    @pytest.mark.asyncio
    async def test_missing_pack_details_carry_resolution_fields(self, harness):
        """The additive DETAILS fields let consumers/tests assert on the
        resolution instead of pattern-matching prose."""
        root = _seed_mission_root(harness)
        child, _ = _seed_asking_child(harness, root, with_pack=False)
        work_id = _seed_mission_work(harness, root)
        missing_pack_id = str(uuid.uuid4())

        from daemon.routers.answer_helper import answer_questions_via_instance
        from daemon.services.answer_resolver import resolve_answer_target

        target = resolve_answer_target(
            harness.manager, root, missing_pack_id
        )
        assert target.asker_instance_id == child
        assert target.route == "subtree_answer_handle"

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await answer_questions_via_instance(
                manager=harness.manager,
                instance_id=child,
                answers={"q1": "Approach A"},
                question_pack_id=missing_pack_id,
                live_hub=None,
                resolution_details=target.detail_fields(),
            )

        exc = exc_info.value
        assert exc.status_code == 410
        assert exc.detail["code"] == ErrorCodes.QUESTION_PACK_LOST.value
        details = exc.detail["details"]
        assert details["resolved_instance_id"] == child
        assert details["question_pack_id"] == missing_pack_id
        assert details["subtree_scanned"] is True
        assert details["mission_root_id"] == root
        assert "daemon restart" not in exc.detail["message"]

    def test_route_missing_pack_is_410(self, harness, jobs_client):
        root = _seed_mission_root(harness)
        child, _ = _seed_asking_child(harness, root, with_pack=False)
        work_id = _seed_mission_work(harness, root)
        missing_pack_id = str(uuid.uuid4())

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={
                "answers": {"q1": "Approach A"},
                "question_pack_id": missing_pack_id,
            },
        )

        assert resp.status_code == 410, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == ErrorCodes.QUESTION_PACK_LOST.value
        assert "daemon restart" not in detail["message"]
        assert child in detail["message"]
        assert missing_pack_id in detail["message"]


# =============================================================================
# (e) No pack-holding asker in the subtree → 404, not a false 410
# =============================================================================


class TestNoAskerInSubtreeIs404:
    """Nobody in the mission subtree holds a pack → 404
    ``NO_PENDING_QUESTION`` (the subtree was scanned). The old code
    answered the root and claimed the pack was LOST (410) — a claim
    that was false: nobody ever had a pack for this work unit."""

    def test_route_reports_404_when_nobody_holds_a_pack(
        self, harness, jobs_client
    ):
        root = _seed_mission_root(harness)
        work_id = _seed_mission_work(harness, root)

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 404, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == ErrorCodes.NO_PENDING_QUESTION.value
        assert detail["details"]["subtree_scanned"] is True
        assert detail["details"]["resolved_instance_id"] == root
        assert "daemon restart" not in detail["message"]


# =============================================================================
# (e) Instance-addressed surface — behaviour UNCHANGED
# =============================================================================


class TestInstanceScopedRouteUnchanged:
    """(e) ``POST /api/instances/{id}/answer`` names the instance
    explicitly — no subtree resolution, today's semantics preserved."""

    def test_instance_scoped_answer_still_works(self, harness, instances_client):
        root = _seed_mission_root(harness)
        child, pack = _seed_asking_child(harness, root)

        resp = instances_client.post(
            f"/instances/{child}/answer",
            json={
                "answers": {"q1": "Approach A"},
                "question_pack_id": pack.id,
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "answered"
        assert body["instance_id"] == child
        assert harness.qm.get_question_pack(child).status == "answered"

    def test_instance_scoped_missing_pack_still_410(self, harness, instances_client):
        """The instance-addressed surface keeps its 410 code — only the
        message changed (the false restart claim is gone)."""
        root = _seed_mission_root(harness)
        child, _ = _seed_asking_child(harness, root, with_pack=False)

        resp = instances_client.post(
            f"/instances/{child}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 410, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == ErrorCodes.QUESTION_PACK_LOST.value
        assert "daemon restart" not in detail["message"]
        # The instance-scoped surface passes no resolution details —
        # its details stay the instance-scoped shape.
        assert detail["details"]["instance_id"] == child

    def test_instance_scoped_unknown_instance_still_404(
        self, harness, instances_client
    ):
        resp = instances_client.post(
            f"/instances/{uuid.uuid4()}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 404, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == ErrorCodes.INSTANCE_NOT_FOUND.value


# =============================================================================
# (f) Stale ANSWERED RAM pack on the mission root — the fast path must
#     NOT short-circuit on a non-pending pack
# =============================================================================


class TestStaleAnsweredRootPackDoesNotShortCircuit:
    """(f) A RAM pack SURVIVES answer consumption (only the durable
    shadow is cleared), so the mission root can hold a STALE ANSWERED
    pack while a CHILD holds the live pending question. The resolver's
    fast path must accept a PENDING pack only: short-circuiting on the
    stale pack routes the answer at the root, the CAS loses, and the
    caller gets a silent 200 ``already_delivered`` while the child's
    question is never answered and the child stays paused."""

    def test_route_without_pack_id_answers_the_child_not_the_stale_root(
        self, harness, jobs_client
    ):
        root = _seed_mission_root(harness)
        _seed_stale_answered_pack(harness, root)
        child, pack = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        # The HTTP route permits omitting ``question_pack_id``.
        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        # NOT the silent CAS-loss no-op on the root's stale pack.
        assert body["status"] == "answered"
        assert body["resume_route"] != "already_delivered"

        # The answer landed on the CHILD — the live asker.
        assert body["instance_id"] == child
        assert body["answer_target"]["instance_id"] == child
        assert body["answer_target"]["work_instance_id"] == root
        assert body["answer_target"]["route"] == "subtree_answer_handle"

        # The child's pending pack was answered …
        assert harness.qm.get_question_pack(child).status == "answered"
        # … and the root's stale pack was NOT re-answered (its stored
        # answers are the original ones).
        root_pack = harness.qm.get_question_pack(root)
        assert root_pack.status == "answered"
        assert root_pack.answers == {"q1": "stale answer"}

        # The CHILD was resumed (its pause cleared) — the resume seams
        # were driven for the child, not for the root.
        assert harness.manager.resume_processing_job.await_args.args[0] == child
        harness.manager.resume_instance_cascade.assert_awaited_once_with(child)


# =============================================================================
# W-1 — SINGLE candidate-collection pass (remediation cycle 1)
# =============================================================================


class TestPrePauseWindowChildIsACandidate:
    """W-1a — the PRE-PAUSE WINDOW.

    ``question_tools.py`` stores the pack and stamps the durable shadow
    BEFORE the ``awaiting_answer`` pause fires. A child that asked a
    moment ago therefore holds a LIVE PENDING PACK while NO
    awaiting-answer handle exists yet. The resolver used to scan for
    paused handles only, so this geometry produced a false 404 — the
    answer was never stored and the child stayed paused."""

    def test_route_with_pack_id_reaches_the_pre_pause_child(
        self, harness, jobs_client
    ):
        root = _seed_mission_root(harness)
        child, pack = _seed_asking_child(harness, root, with_handle=False)
        work_id = _seed_mission_work(harness, root)

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={
                "answers": {"q1": "Approach A"},
                "question_pack_id": pack.id,
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == child
        assert body["answer_target"]["route"] == "subtree_pack_id"
        assert harness.qm.get_question_pack(child).status == "answered"

    def test_route_without_pack_id_reaches_the_pre_pause_child(
        self, harness, jobs_client
    ):
        """Without a pack id the LIVE PENDING PACK is the only candidate
        signal available in the pre-pause window — it must count."""
        root = _seed_mission_root(harness)
        child, pack = _seed_asking_child(harness, root, with_handle=False)
        work_id = _seed_mission_work(harness, root)

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == child
        # No handle → the live pending pack is what identified it.
        assert body["answer_target"]["route"] == "subtree_pending_pack"
        assert harness.qm.get_question_pack(child).status == "answered"


class TestAmbiguousCandidatesAreRefused:
    """W-1b — the ambiguity refusal must NOT be pre-empted.

    Root pending + child pending and NO pack id: the root used to win
    unconditionally (the direct lane short-circuited the scan), so a
    second asker was never even seen. Both surfaces now collect ALL
    candidates first and refuse to guess."""

    def test_route_refuses_root_plus_child_without_pack_id(
        self, harness, jobs_client
    ):
        root = _seed_mission_root(harness)
        root_pack = harness.qm.set_question_pack(
            root, [{"id": "qr", "text": "Root question?"}]
        )
        harness.instance_repo.set_metadata(root, "question_pack_id", root_pack.id)
        harness.instance_repo.set_metadata(
            root, "question_pack_payload", pack_to_dict(root_pack)
        )
        _seed_task(harness.engine, root, status=TaskStatus.PAUSED.value)

        child, child_pack = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == ErrorCodes.INVALID_REQUEST.value
        # BOTH candidates are named — the caller must disambiguate.
        assert root in detail["message"]
        assert child in detail["message"]
        assert set(detail["details"]["candidate_ids"]) == {root, child}

        # NEITHER pack was answered — refusing is the contract.
        assert harness.qm.get_question_pack(root).status == "pending"
        assert harness.qm.get_question_pack(child).status == "pending"

    def test_resolver_reports_both_candidates(self, harness):
        """The shared resolver (both lanes) collects every candidate
        before it returns."""
        from daemon.services.answer_resolver import resolve_answer_target

        root = _seed_mission_root(harness)
        root_pack = harness.qm.set_question_pack(
            root, [{"id": "qr", "text": "Root question?"}]
        )
        harness.instance_repo.set_metadata(root, "question_pack_id", root_pack.id)
        _seed_task(harness.engine, root, status=TaskStatus.PAUSED.value)
        child, child_pack = _seed_asking_child(harness, root)

        target = resolve_answer_target(harness.manager, root, None)

        assert target.asker_instance_id is None
        assert target.error == "ambiguous"
        assert set(target.candidate_ids) == {root, child}


class TestStalePendingShadowIsNotAuthoritative:
    """W-1c — the durable shadow is a FROZEN ask-time snapshot.

    A stale PENDING shadow on the mission root (the pack is gone; the
    turn moved on) must never short-circuit the resolution — it would
    route the answer at an instance that is not waiting for one and
    hide the LIVE question of the real asker."""

    def test_route_ignores_stale_root_shadow_and_answers_the_child(
        self, harness, jobs_client
    ):
        root = _seed_mission_root(harness)
        _seed_stale_pending_shadow(harness, root)
        # The root has NO live RAM pack and NO awaiting-answer handle.
        assert harness.qm.get_question_pack(root) is None

        child, pack = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == child
        assert body["answer_target"]["instance_id"] == child
        assert harness.qm.get_question_pack(child).status == "answered"


class TestMissionRootIsReachableWhenTheWorkResolvesToAChild:
    """W-1d — the direct lanes were keyed on the WORK instance while the
    scan ran on the MISSION ROOT: the root's own pending pack was
    unreachable when the work resolved to a child. The mission root is
    now folded into the reachable set."""

    def test_route_answers_the_root_when_the_work_is_a_child(
        self, harness, jobs_client
    ):
        root = _seed_mission_root(harness)
        root_pack = harness.qm.set_question_pack(
            root, [{"id": "qr", "text": "Root question?"}]
        )
        harness.instance_repo.set_metadata(root, "question_pack_id", root_pack.id)
        harness.instance_repo.set_metadata(
            root, "question_pack_payload", pack_to_dict(root_pack)
        )
        _seed_task(harness.engine, root, status=TaskStatus.PAUSED.value)

        # A plain child — the work unit resolves to IT.
        work_child = _seed_instance(
            harness.engine,
            status=InstanceStatus.RUNNING.value,
            project_id=PROJECT_ID,
            parent_id=root,
            agent_id="worker",
        )
        work_id = _seed_task(
            harness.engine, work_child, status=TaskStatus.RUNNING.value
        )

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={
                "answers": {"qr": "Root answer"},
                "question_pack_id": root_pack.id,
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == root
        assert body["answer_target"]["instance_id"] == root
        assert body["answer_target"]["work_instance_id"] == work_child
        assert body["answer_target"]["mission_root_id"] == root
        assert harness.qm.get_question_pack(root).status == "answered"

    def test_route_answers_the_root_without_a_pack_id(
        self, harness, jobs_client
    ):
        """The root's LIVE pending pack must be reachable even when the
        work resolves to a CHILD and no pack id was supplied.

        The direct lanes were keyed on the work instance (the child), so
        the root's own pack was invisible to them; and the old
        handle-only subtree scan missed it while the root is still in
        the PRE-PAUSE window (live pack, no handle yet)."""
        root = _seed_mission_root(harness)
        root_pack = harness.qm.set_question_pack(
            root, [{"id": "qr", "text": "Root question?"}]
        )
        harness.instance_repo.set_metadata(root, "question_pack_id", root_pack.id)
        harness.instance_repo.set_metadata(
            root, "question_pack_payload", pack_to_dict(root_pack)
        )
        # NOTE: no paused task on the root → no awaiting-answer handle
        # yet (the pre-pause window).

        work_child = _seed_instance(
            harness.engine,
            status=InstanceStatus.RUNNING.value,
            project_id=PROJECT_ID,
            parent_id=root,
            agent_id="worker",
        )
        work_id = _seed_task(
            harness.engine, work_child, status=TaskStatus.RUNNING.value
        )

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={"answers": {"qr": "Root answer"}},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == root
        assert body["answer_target"]["work_instance_id"] == work_child
        assert harness.qm.get_question_pack(root).status == "answered"


# =============================================================================
# W-2 — the candidate scan is NOT truncated when a pack id is supplied
# =============================================================================


class TestLargeSubtreeWithSuppliedPackId:
    """W-2 — ``MAX_SUBTREE_SCAN`` truncated the candidate pass BEFORE the
    pack-id scan, so an asker beyond the cap produced a false 404 even
    though the caller named the pack exactly. A supplied pack id lifts
    the cap — and the lifted scan is REPORTED (``scan_uncapped`` /
    ``reachable_count``), because the enumeration is bounded in DEPTH
    only, not in node count."""

    def test_asker_beyond_the_cap_is_reached_with_a_pack_id(
        self, harness, jobs_client
    ):
        from daemon.services.answer_resolver import (
            MAX_SUBTREE_SCAN,
            resolve_answer_target,
        )

        root = _seed_mission_root(harness)
        work_id = _seed_mission_work(harness, root)

        # A mission tree wider than the cap; the asker is the LAST child
        # (well beyond MAX_SUBTREE_SCAN).
        asker = None
        asker_pack = None
        for index in range(MAX_SUBTREE_SCAN + 17):
            child, pack = _seed_asking_child(harness, root)
            if index == MAX_SUBTREE_SCAN + 16:
                asker, asker_pack = child, pack

        assert asker is not None and asker_pack is not None

        # The lifted cap is REPORTED, not silent: the whole reachable
        # set was scanned (nothing was missed — ``subtree_truncated``
        # stays False) and that set exceeded the cap.
        #
        # Probed BEFORE the answer is delivered: the RAM pack survives
        # consumption (it flips to 'answered'), so a probe after the
        # POST would see a different candidate set.
        target = resolve_answer_target(harness.manager, root, asker_pack.id)
        details = target.detail_fields()
        assert target.asker_instance_id == asker
        assert details["scan_uncapped"] is True
        assert details["subtree_truncated"] is False
        assert details["reachable_count"] == MAX_SUBTREE_SCAN + 18

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={
                "answers": {"q1": "Approach A"},
                "question_pack_id": asker_pack.id,
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == asker
        assert harness.qm.get_question_pack(asker).status == "answered"

    def test_cap_still_applies_without_a_pack_id(self, harness):
        """Without a pack id the cap holds (it bounds the per-instance
        reads) — and the truncation is reported in the details."""
        from daemon.services.answer_resolver import (
            MAX_SUBTREE_SCAN,
            resolve_answer_target,
        )

        root = _seed_mission_root(harness)
        for _ in range(MAX_SUBTREE_SCAN + 5):
            _seed_instance(
                harness.engine,
                status=InstanceStatus.RUNNING.value,
                project_id=PROJECT_ID,
                parent_id=root,
                agent_id="worker",
            )

        target = resolve_answer_target(harness.manager, root, None)

        assert target.error == "not_found"
        assert target.subtree_truncated is True
        assert target.detail_fields()["subtree_truncated"] is True


# =============================================================================
# W-4 — the TERMINAL signal survives the resolution
# =============================================================================


class TestTerminalWorkInstanceKeepsItsSignal:
    """W-4 — before the asker resolution existed, the job-scoped route
    answered the WORK instance directly and a terminal work instance
    raised 410 ANSWER_TARGET_TERMINAL from the helper's pre-check. The
    resolution must not swallow that signal behind a 404."""

    @pytest.mark.parametrize(
        "status",
        [InstanceStatus.COMPLETED.value, InstanceStatus.TERMINATED.value],
    )
    def test_route_reports_answer_target_terminal(
        self, harness, jobs_client, status
    ):
        root = _seed_instance(
            harness.engine,
            status=status,
            project_id=PROJECT_ID,
            agent_id="leader",
        )
        work_id = _seed_task(
            harness.engine, root, status=TaskStatus.RUNNING.value
        )

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={"answers": {"q1": "Approach A"}},
        )

        assert resp.status_code == 410, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == ErrorCodes.ANSWER_TARGET_TERMINAL.value
        assert detail["details"]["work_instance_status"] == status
        assert root in detail["message"]

    @pytest.mark.asyncio
    async def test_tool_lane_reports_answer_target_terminal(self, harness):
        """The agent tool carries the same typed code."""
        root = _seed_instance(
            harness.engine,
            status=InstanceStatus.COMPLETED.value,
            project_id=PROJECT_ID,
            agent_id="leader",
        )
        work_id = _seed_task(
            harness.engine, root, status=TaskStatus.RUNNING.value
        )

        tool = _make_job_answer_tool(harness)
        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": str(uuid.uuid4()),
            }
        )

        assert "error" in result
        assert ErrorCodes.ANSWER_TARGET_TERMINAL.value in result["error"]
        assert "410" in result["error"]


# =============================================================================
# W-8 — test pins
# =============================================================================


class TestAskerIsRootFastPath:
    """W-8(i) — the fast path (the work instance holds the requested
    pack) must be PINNED: it is a distinct code path, not just another
    spelling of the general scan. The pin asserts both the route label
    AND the absence of any per-instance repository read — if the fast
    path were deleted, the candidate pass would run and the read
    counters would move."""

    def test_fast_path_resolves_without_any_candidate_pass_read(
        self, harness, monkeypatch
    ):
        from daemon.services.answer_resolver import resolve_answer_target

        root = _seed_mission_root(harness)
        pack = harness.qm.set_question_pack(
            root, [{"id": "qr", "text": "Root question?"}]
        )
        harness.instance_repo.set_metadata(root, "question_pack_id", pack.id)
        harness.instance_repo.set_metadata(
            root, "question_pack_payload", pack_to_dict(pack)
        )
        _seed_task(harness.engine, root, status=TaskStatus.PAUSED.value)

        reads = {"metadata": 0, "handle": 0, "tree": 0}
        orig_meta = harness.instance_repo.get_metadata_value
        orig_handle = harness.task_repo.find_suspended_turn_for_answer
        orig_tree = harness.instance_repo.get_tree_ids_permanent

        def _counted_meta(*args, **kwargs):
            reads["metadata"] += 1
            return orig_meta(*args, **kwargs)

        def _counted_handle(*args, **kwargs):
            reads["handle"] += 1
            return orig_handle(*args, **kwargs)

        def _counted_tree(*args, **kwargs):
            reads["tree"] += 1
            return orig_tree(*args, **kwargs)

        monkeypatch.setattr(
            harness.instance_repo, "get_metadata_value", _counted_meta
        )
        monkeypatch.setattr(
            harness.task_repo, "find_suspended_turn_for_answer", _counted_handle
        )
        monkeypatch.setattr(
            harness.instance_repo, "get_tree_ids_permanent", _counted_tree
        )

        target = resolve_answer_target(harness.manager, root, pack.id)

        # The asker IS the root.
        assert target.asker_instance_id == root
        assert target.route == "direct_ram_pack"
        assert target.subtree_scanned is False

        # The fast path performed NO candidate-pass read at all.
        assert reads == {"metadata": 0, "handle": 0, "tree": 0}

    def test_route_answers_the_root_asker(self, harness, jobs_client):
        """The behavioural pin: the asker IS the mission root (the
        classic geometry) — the answer lands on the root."""
        root = _seed_mission_root(harness)
        pack = harness.qm.set_question_pack(
            root, [{"id": "qr", "text": "Root question?"}]
        )
        harness.instance_repo.set_metadata(root, "question_pack_id", pack.id)
        harness.instance_repo.set_metadata(
            root, "question_pack_payload", pack_to_dict(pack)
        )
        _seed_task(harness.engine, root, status=TaskStatus.PAUSED.value)
        work_id = _seed_mission_work(harness, root)

        resp = jobs_client.post(
            f"/jobs/{work_id}/answer",
            json={
                "answers": {"qr": "Root answer"},
                "question_pack_id": pack.id,
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == root
        assert body["answer_target"]["route"] == "direct_ram_pack"
        assert harness.qm.get_question_pack(root).status == "answered"


class TestToolLaneErrorStringSeams:
    """W-8(ii) — the tool lane formats its failures through
    ``_format_answer_http_error`` (a DIFFERENT code path from the HTTP
    lane). The typed codes must survive that shaping."""

    @pytest.mark.asyncio
    async def test_tool_404_no_pending_question_string(self, harness):
        """No pack-holding asker anywhere in the mission → the tool
        surfaces the typed 404 token (and the ``job_continue`` hint)."""
        root = _seed_mission_root(harness)
        work_id = _seed_mission_work(harness, root)

        tool = _make_job_answer_tool(harness)
        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": str(uuid.uuid4()),
            }
        )

        assert "error" in result
        assert ErrorCodes.NO_PENDING_QUESTION.value in result["error"]
        assert "404" in result["error"]
        assert "job_continue" in result["error"]
        assert work_id in result["error"]

    @pytest.mark.asyncio
    async def test_tool_400_invalid_request_string(self, harness):
        """The tool CAN produce the typed 400: the supplied pack id
        matches nobody while SEVERAL instances hold an awaiting-answer
        handle."""
        root = _seed_mission_root(harness)
        child_a, _ = _seed_asking_child(harness, root)
        child_b, _ = _seed_asking_child(harness, root)
        work_id = _seed_mission_work(harness, root)

        tool = _make_job_answer_tool(harness)
        result = await tool.ainvoke(
            {
                "work_id": work_id,
                "answers": {"q1": "Approach A"},
                "question_pack_id": str(uuid.uuid4()),
            }
        )

        assert "error" in result
        assert ErrorCodes.INVALID_REQUEST.value in result["error"]
        assert "400" in result["error"]
        # Both candidate instances are named in the error string.
        assert child_a in result["error"]
        assert child_b in result["error"]
        # Neither pack was answered.
        assert harness.qm.get_question_pack(child_a).status == "pending"
        assert harness.qm.get_question_pack(child_b).status == "pending"
