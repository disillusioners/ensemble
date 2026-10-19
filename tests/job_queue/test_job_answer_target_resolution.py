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
) -> tuple[str, object | None]:
    """Seed a CHILD instance that ASKED a question.

    The child owns the pending pack (RAM + the durable
    ``instance_metadata`` shadow) and carries the authoritative
    awaiting-answer handle (a ``paused`` task with
    ``suspension_reason='awaiting_answer'`` and a resume target) —
    exactly the state the ``question`` tool leaves behind.

    ``with_pack=False`` seeds the child with the handle but WITHOUT any
    pack (RAM + shadow) — the "genuinely lost pack" shape.

    Returns ``(child_id, pack_or_None)``.
    """
    child = _seed_instance(
        harness.engine,
        status=InstanceStatus.PAUSED.value,
        project_id=project_id,
        parent_id=parent_id,
        agent_id="devops",
    )
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
