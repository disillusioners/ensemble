"""F11 pins — the daemon-minted DELEGATION CHAIN (per-seam, unit level).

Commission: nest-ops shakedown defect patch, Phase 3 (T3.1-T3.5).
Defect: F11 — a genuine user-origin turn delegated via ``job_create``
derives ``source = f"agent:{caller_agent_id}"`` (the AGENT NAME, not the
instance), and ``_inherit_provenance_window`` keyed ONLY on
``internal_agent:`` and POPped everything else ⇒ the child was windowless
⇒ every job-dispatched protected terminate refused ``no-user-provenance``.

The chain (Option B, D-7): the daemon mints a POINTER to the SUBMITTING
instance — ``{"caller_instance_id": str, "caller_agent_id": str | None}``
— and threads it job → message → claim → funnel, where the mint claims
THAT instance's live window through the SAME arithmetic as the legacy
``internal_agent:`` lane.

THE CHAIN IS A POINTER, NEVER A CREDENTIAL. Three forgery controls
(arch §7.4):

  (a) the service seam pops the reserved key from any caller-supplied
      metadata map, unconditionally;
  (b) the chain is honoured ONLY on the ``agent:`` lane (the message
      lane can carry the reserved key on a row without ever having
      passed the job seam);
  (c) the HTTP reserved-source gate rejects ``source="agent:…"`` with
      422.

CONSTRUCTION-NEVER-LITERAL (hard constraint 4): the RETIRED provenance
token must never appear as a LITERAL in a new file — it is reconstructed
from its two halves below. DF-4's tree-wide pin greps BOTH ``daemon/``
and ``tests/``, so a literal here would fail it (and DID, the first
time this file was written — the docstring spelled it out).

SAFETY FENCE: no HOME-derived or live-shaped path is ever resolved; the
audit-ledger scratch DB (where one is needed) is a ``tempfile`` SQLite
file. The real-PG e2e (real spawn order, real ledger) is the TESTER's
Phase-4 job — these pins are the per-seam unit layer.

PLANNED MUTATION (T4.4 — one behaviour per run): drop the chain at the
``job_create`` mint site (S2) → the e2e F11-a RED (child windowless);
remove the unconditional S3 pop in ``enqueue`` → the b4 injection pin
RED; mis-bind the factory's ``caller_instance_id`` → the
mis-bound-factory pin RED; remove the ``exp >= now_dt`` compare in the
shared claim body → the expired-window pin RED; raise/remove
``_INHERITED_PROVENANCE_MAX_DEPTH`` → the depth-cap pin RED. The
legacy-row pin is ANCHOR-NEUTRAL (declared — a regression pin, not
mutation coverage).
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.manager import InstanceManager
from daemon.repositories.job_queue import JobRepository, JobQueueRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import DISPATCH_CHAIN_KEY, JobQueueService
from daemon.services.message_processing_pipeline import ProcessingContext
from daemon.tools import upgrade_journal as _uj
from daemon.tools.job_queue import create_job_tools

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The reserved key's VALUE, reconstructed (never a literal) so DF-4's
#: tree-wide retired-token pin cannot be tripped by this file.
RESERVED_CHAIN_KEY = "__ens_" + "dispatch_chain__"

#: The retired provenance token, reconstructed from its two halves. It
#: must not survive anywhere in the tree — the pin below proves the
#: retired token is gone from the source.
RETIRED_TOKEN = "direct-" + "provenance-insufficient"


# ── helpers ────────────────────────────────────────────────────────────────


class _MintSelf:
    """The minimal ``self`` the provenance mint actually touches.

    ``InstanceManager._inherit_provenance_window`` reads and writes ONLY
    the two in-memory window stores — it never raises and never touches
    the database. Calling the REAL method with this stand-in keeps the
    pin on the production code path (a hand-rolled copy of the mint
    would prove nothing).
    """

    def __init__(self) -> None:
        self._user_origin_windows: dict = {}
        self._inherited_provenance_windows: dict = {}


def _live_direct_window(source: str = "api", *, ttl_s: int = 3600) -> dict:
    """A window in the PRODUCTION time format.

    ``upgrade_journal.parse_iso_utc`` only accepts
    ``"%Y-%m-%dT%H:%M:%SZ"`` — a plain ``datetime.isoformat()`` string
    parses to ``None`` and the mint would fail closed for the wrong
    reason. The pin must exercise LIVENESS, not a format mismatch.
    """
    now = _uj.now_iso()
    return {
        "source": source,
        "message_id": "root-msg-1",
        "stamped_at": now,
        "expires_at": _uj.iso_plus(now, ttl_s),
    }


def _mint(
    self_: _MintSelf,
    instance_id: str,
    source: str | None,
    dispatch_chain: dict | None = None,
) -> None:
    InstanceManager._inherit_provenance_window(
        self_, instance_id, source, dispatch_chain=dispatch_chain
    )


#: The scratch project id the tool-level pins normalize to. The house
#: pattern (``tests/test_job_queue_tools.py``) patches the constant
#: rather than the normalizer — the tool imports the normalizer
#: directly, so the constant is the single lever.
TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


@pytest.fixture(autouse=True)
def setup_system_default_project():
    """Give the normalizer a system default project id.

    ``normalize_project_id()`` raises when the default is uninitialized
    — without this the tool-level pins would never reach the enqueue
    call they are written to observe.
    """
    from daemon import constants

    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = TEST_SYSTEM_PROJECT_ID
    yield
    constants.SYSTEM_DEFAULT_PROJECT_ID = original


#: The scratch project the seam pins run against. The house pattern
#: (see ``tests/job_queue/test_retry_orphan_normalization.py``) patches
#: ``normalize_project_id`` in the service module and provisions the
#: system queue for the SAME scratch project.
SCRATCH_PROJECT = "__test_system_default__"


@dataclass
class Seam:
    """The REAL service plus the handles a test needs to seed state."""

    service: JobQueueService
    engine: object
    repository: object


@pytest.fixture
def seam(monkeypatch):
    """A REAL ``JobQueueService`` over an in-memory SQLite engine.

    The seam is the whole point of the F11 design, so the seam pins run
    the REAL service — a fake would let a per-site pop pass.
    """
    monkeypatch.setattr(
        "daemon.services.job_queue_service.normalize_project_id",
        lambda _pid: SCRATCH_PROJECT,
    )
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    repository = JobRepository(engine)
    lock_manager = JobLockManager(lock_repo=LockRepository(engine))
    queue_repo = JobQueueRepository(engine)
    queue_repo.create(
        project_id=SCRATCH_PROJECT,
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    return Seam(
        service=JobQueueService(repository, lock_manager, queue_repo),
        engine=engine,
        repository=repository,
    )


def _job_tools(*, current_instance_id: str = "inst-ari", agent_id: str = "ari"):
    """The REAL job tools over a capturing fake job service."""
    job_service = AsyncMock()
    job_service.use_virtual_job_resolver = False
    job_service.enqueue = AsyncMock(return_value=MagicMock(job_id="job-1"))
    # The queue repo must be a SYNC mock: the tool resolves the default
    # queue through ``asyncio.to_thread(queue_repo.get_by_name, ...)``,
    # and an async child mock would return a coroutine.
    job_service._queue_repo = MagicMock()
    job_service._queue_repo.get_by_name = MagicMock(
        return_value=MagicMock(queue_id="queue-1")
    )
    queue_mgmt_service = AsyncMock()
    dead_letter_service = MagicMock()
    tools = create_job_tools(
        job_service,
        queue_mgmt_service,
        dead_letter_service,
        current_instance_id=current_instance_id,
        agent_id=agent_id,
    )
    by_name = {getattr(t, "name", None): t for t in tools}
    return tools, by_name, job_service


# ── A. the service seam (S3) — the forgery dies here ───────────────────────


class TestSeamForgeryControlA:
    """Control (a) — the unconditional pop at the service seam."""

    @pytest.mark.asyncio
    async def test_caller_supplied_reserved_key_is_popped(self, seam):
        """A REST-style caller cannot smuggle a chain into the row."""
        svc = seam.service
        job = await svc.enqueue(
            agent_id="ari",
            message="m",
            source="api",
            metadata={RESERVED_CHAIN_KEY: {"caller_instance_id": "inst-forged"}},
        )
        assert RESERVED_CHAIN_KEY not in (job.job_metadata or {})

    @pytest.mark.asyncio
    async def test_daemon_value_wins_by_construction(self, seam):
        """The daemon value replaces whatever the caller sent."""
        svc = seam.service
        job = await svc.enqueue(
            agent_id="ari",
            message="m",
            source="agent:ari",
            metadata={RESERVED_CHAIN_KEY: {"caller_instance_id": "inst-forged"}},
            dispatch_chain={"caller_instance_id": "inst-real"},
        )
        assert job.job_metadata[RESERVED_CHAIN_KEY] == {
            "caller_instance_id": "inst-real"
        }

    @pytest.mark.asyncio
    async def test_absent_chain_is_byte_identical(self, seam):
        """Default-off: with no chain the stored metadata is unchanged."""
        svc = seam.service
        payload = {"custom": "value"}
        job = await svc.enqueue(
            agent_id="ari", message="m", source="api", metadata=payload
        )
        assert job.job_metadata == {"custom": "value"}
        assert RESERVED_CHAIN_KEY not in job.job_metadata

    @pytest.mark.asyncio
    async def test_caller_metadata_dict_is_not_mutated(self, seam):
        """The seam COPIES the caller's map — it never mutates it."""
        svc = seam.service
        payload = {RESERVED_CHAIN_KEY: {"caller_instance_id": "inst-forged"}}
        await svc.enqueue(
            agent_id="ari", message="m", source="api", metadata=payload
        )
        assert payload == {RESERVED_CHAIN_KEY: {"caller_instance_id": "inst-forged"}}

    @pytest.mark.asyncio
    async def test_idempotency_path_also_drops_the_reserved_key(self, seam):
        """BOTH create sites are covered — the pop runs at the TOP.

        A per-site pop leaves the idempotency branch open; this pin
        exercises that branch specifically.
        """
        svc = seam.service
        first = await svc.enqueue(
            agent_id="ari",
            message="m",
            source="api",
            idempotency_key="idem-1",
            metadata={RESERVED_CHAIN_KEY: {"caller_instance_id": "inst-forged"}},
        )
        assert RESERVED_CHAIN_KEY not in (first.job_metadata or {})
        second = await svc.enqueue(
            agent_id="ari",
            message="m",
            source="api",
            idempotency_key="idem-1",
            metadata={RESERVED_CHAIN_KEY: {"caller_instance_id": "inst-forged"}},
        )
        assert second.job_id == first.job_id
        assert RESERVED_CHAIN_KEY not in (second.job_metadata or {})

    @pytest.mark.asyncio
    async def test_retry_job_repasses_the_chain(self, seam):
        """A retry must not SILENTLY drop the chain.

        The seam's unconditional pop would strip the key out of
        ``job.job_metadata`` — so ``retry_job`` re-passes it as a
        KEYWORD.
        """
        svc = seam.service
        job = await svc.enqueue(
            agent_id="ari",
            message="m",
            source="agent:ari",
            dispatch_chain={"caller_instance_id": "inst-real"},
        )
        # Drive the row into the FAILED state ``retry_job`` accepts:
        # ``admission_state='done'`` with ``failed_at`` set (the
        # Phase-5 model dropped the legacy ``status`` mirror).
        with Session(seam.engine) as session:
            session.execute(
                sa_text(
                    "UPDATE job_queue_items "
                    "SET admission_state = 'done', failed_at = :failed_at "
                    "WHERE job_id = :jid"
                ),
                {"jid": job.job_id, "failed_at": datetime.utcnow().isoformat()},
            )
            session.commit()
        retried = await svc.retry_job(job.job_id)
        assert retried is not None
        assert (retried.job_metadata or {}).get(RESERVED_CHAIN_KEY) == {
            "caller_instance_id": "inst-real"
        }


# ── B. the mint sites (S2) ─────────────────────────────────────────────────


class TestMintSites:
    """The chain is minted ONLY where the daemon knows the caller."""

    @pytest.mark.asyncio
    async def test_job_create_mints_the_chain_by_keyword(self):
        """The chain rides a KEYWORD — never the metadata map."""
        _, by_name, job_service = _job_tools(current_instance_id="inst-ari")
        await by_name["job_create"].ainvoke(
            {"agent_id": "worker", "message": "do it"}
        )
        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["dispatch_chain"] == {
            "caller_instance_id": "inst-ari",
            "caller_agent_id": "ari",
        }
        # The chain is NOT in the metadata map.
        assert RESERVED_CHAIN_KEY not in (kwargs.get("metadata") or {})

    @pytest.mark.asyncio
    async def test_misbound_factory_names_the_acting_instance(self):
        """The chain names the SUBMITTING instance, never the target.

        A mis-bound factory would name the wrong instance — the mint
        would then claim somebody else's window.
        """
        _, by_name, job_service = _job_tools(current_instance_id="inst-ari")
        await by_name["job_create"].ainvoke(
            {"agent_id": "worker", "message": "do it"}
        )
        chain = job_service.enqueue.await_args.kwargs["dispatch_chain"]
        assert chain["caller_instance_id"] == "inst-ari"

    @pytest.mark.asyncio
    async def test_no_chain_without_a_caller_instance(self):
        """``current_instance_id`` empty ⇒ NO chain is minted."""
        _, by_name, job_service = _job_tools(current_instance_id="")
        await by_name["job_create"].ainvoke(
            {"agent_id": "worker", "message": "do it"}
        )
        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs.get("dispatch_chain") is None

    @pytest.mark.asyncio
    async def test_source_taxonomy_is_unchanged(self):
        """``JobItem.source`` still reads ``agent:<name>`` (B3.5 intact)."""
        _, by_name, job_service = _job_tools(current_instance_id="inst-ari")
        await by_name["job_create"].ainvoke(
            {"agent_id": "worker", "message": "do it"}
        )
        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["source"] == "agent:ari"

    @pytest.mark.asyncio
    async def test_job_continue_mints_the_chain_by_keyword(self):
        """``job_continue`` reaches the job seam via a KEYWORD.

        This is arch §7.3's hole: ``job_continue`` has no metadata
        channel at all, so a metadata-borne chain could never arrive.
        """
        _, by_name, _ = _job_tools(current_instance_id="inst-ari")
        manager = AsyncMock()
        # ``job_continue`` reads the instance through the SYNCHRONOUS
        # repository handle (``manager._instance_repository.get``), so the
        # repository stand-in must be a sync mock.
        manager._instance_repository = MagicMock()
        manager._instance_repository.get = MagicMock(
            return_value=MagicMock(
                status="completed",
                instance_id="inst-target",
                agent_id="worker",
                project_id="p1",
                metadata={},
            )
        )
        manager.get_instance = AsyncMock(
            return_value=MagicMock(
                status="completed",
                instance_id="inst-target",
                agent_id="worker",
                project_id="p1",
                metadata={},
            )
        )
        manager.get_agent_tool_revive_count = MagicMock(return_value=0)
        manager.note_agent_tool_revive = MagicMock()
        # The in-flight task guard reads ``manager._task_repo``; the
        # stand-in reports NO busy instance so the continue reaches the
        # enqueue call this pin observes.
        manager._task_repo = MagicMock()
        manager._task_repo.has_instance_busy = MagicMock(return_value=False)
        # `create_job_tools` was built with manager=None in the helper —
        # rebuild with a manager so job_continue can reach it.
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        job_service.get_work = AsyncMock(
            return_value=MagicMock(
                kind="job",
                job_id="job-old",
                instance_id="inst-target",
                agent_id="worker",
                project_id="p1",
                status="completed",
                job_type="task",
                deleted_at=None,
            )
        )
        job_service.get_job = AsyncMock(
            return_value=MagicMock(job_id="job-old", deleted_at=None)
        )
        tools = create_job_tools(
            job_service,
            AsyncMock(),
            MagicMock(),
            current_instance_id="inst-ari",
            agent_id="ari",
            manager=manager,
        )
        by_name = {getattr(t, "name", None): t for t in tools}
        result = await by_name["job_continue"].ainvoke(
            {"old_job_id": "job-old", "message": "continue"}
        )
        assert isinstance(result, dict)
        kwargs = manager.enqueue_message_job.await_args.kwargs
        assert kwargs["dispatch_chain"] == {
            "caller_instance_id": "inst-ari",
            "caller_agent_id": "ari",
        }


# ── C. the messaging keyword (arch §7.3) ───────────────────────────────────


class TestMessagingKeywordChannel:
    """The chain travels by KEYWORD through the messaging facade."""

    def test_manager_facade_forwards_the_chain(self):
        """``InstanceManager.enqueue_message_job`` forwards the kwarg."""
        import inspect

        sig = inspect.signature(InstanceManager.enqueue_message_job)
        assert "dispatch_chain" in sig.parameters
        assert sig.parameters["dispatch_chain"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_messaging_service_forwards_the_chain(self):
        from daemon.services.instance_messaging import InstanceMessagingService

        sig = inspect.signature(InstanceMessagingService.enqueue_message_job)
        assert "dispatch_chain" in sig.parameters

    def test_the_chain_never_travels_via_metadata(self):
        """The facade must NOT merge the chain into ``metadata``.

        The seam pops the reserved key out of any caller-supplied
        metadata map — a metadata-borne chain would be erased before it
        reached the row.
        """
        from daemon.services.instance_messaging import InstanceMessagingService

        src = inspect.getsource(InstanceMessagingService.enqueue_message_job)
        assert "dispatch_chain=dispatch_chain" in src
        assert f"{RESERVED_CHAIN_KEY}: dispatch_chain" not in src


# ── D. the dispatch seam (S4) ──────────────────────────────────────────────


class TestDispatchSeam:
    """``job_processor`` forwards the RESERVED key only."""

    def test_the_shared_chain_reader_reads_the_reserved_key(self):
        """ONE reader for the chain (the tidier fold).

        The three forward sites used to carry their own copy of
        ``(job.job_metadata or {}).get(DISPATCH_CHAIN_KEY)`` — and the
        copies had drifted (``proc_job`` vs ``job``) even though all
        three objects are the same ``JobItem``. This pins the helper's
        contract:

          * the chain rides under the RESERVED key;
          * an ABSENT chain reads as ``None`` (never an empty dict —
            the forward is byte-identical to the pre-chain shape);
          * a job row with NO metadata at all reads as ``None``.
        """
        from daemon.services import job_processor

        class _Job:
            def __init__(self, metadata):
                self.job_metadata = metadata

        assert (
            job_processor._chain_metadata(_Job({RESERVED_CHAIN_KEY: {"c": 1}}))
            == {"c": 1}
        )
        assert job_processor._chain_metadata(_Job({})) is None
        assert job_processor._chain_metadata(_Job(None)) is None
        # the RESERVED key is the ONLY key that is read.
        assert job_processor._chain_metadata(_Job({"other": "x"})) is None

    def test_three_dispatch_sites_forward_the_reserved_key(self):
        from daemon.services import job_processor

        src = Path(job_processor.__file__).read_text()
        import ast

        tree = ast.parse(src)
        helper_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_chain_metadata"
        ]
        # THREE dispatch sites read the chain — through the ONE shared
        # helper. (The tidier fold replaced three near-verbatim copies
        # that had already drifted: two read ``proc_job.job_metadata``
        # and one read ``job.job_metadata``, although all three objects
        # are the same ``JobItem`` model.)
        assert len(helper_calls) == 3, (
            "the three dispatch sites must read the chain through the "
            f"shared helper (found {len(helper_calls)})"
        )
        # ... and each forwards it under the RESERVED key, CONDITIONALLY.
        assert src.count("{DISPATCH_CHAIN_KEY: chain} if chain else None") == 3
        # the reserved key is READ in exactly ONE place — the helper.
        assert src.count(".get(DISPATCH_CHAIN_KEY)") == 1, (
            "the reserved key must be read in ONE place (the shared "
            f"helper); found {src.count('.get(DISPATCH_CHAIN_KEY)')} reads"
        )

    def test_absent_chain_forwards_no_metadata(self):
        """Absent ⇒ ``metadata=None`` ⇒ byte-identical to today."""
        from daemon.services import job_processor

        src = Path(job_processor.__file__).read_text()
        # The forwarding expression is a conditional that yields None
        # when the chain is absent — never a bare dict.
        assert "metadata={DISPATCH_CHAIN_KEY: chain}" not in src


class TestJobQueueChainBuilder:
    """ONE builder for the two mint sites (the tidier fold).

    The two copies — the ``job_create`` mint and the ``job_continue``
    forward — were near-verbatim and their COMMENTS had already
    drifted, while the value they produced is identical.

    PLANNED MUTATION:
      * inline the dict at one site again → the "both sites use the
        builder" pin RED;
      * drop the ``caller_agent_id`` field → the shape pin RED.
    """

    def test_the_builder_returns_none_without_a_submitting_instance(self):
        from daemon.tools import job_queue

        assert job_queue._build_dispatch_chain("", "agent") is None
        assert job_queue._build_dispatch_chain(None, "agent") is None

    def test_the_builder_carries_both_fields(self):
        from daemon.tools import job_queue

        assert job_queue._build_dispatch_chain("inst-1", "agent") == {
            "caller_instance_id": "inst-1",
            "caller_agent_id": "agent",
        }

    def test_both_mint_sites_use_the_shared_builder(self):
        import ast

        from daemon.tools import job_queue

        src = Path(job_queue.__file__).read_text()
        tree = ast.parse(src)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_build_dispatch_chain"
        ]
        # EXACTLY TWO mint sites — the definition is a FunctionDef, not
        # a Call, so it is not counted here.
        assert len(calls) == 2, (
            "both mint sites must use the shared builder; found "
            f"{len(calls)} call site(s)"
        )
        # the chain is spelled ONCE — in the builder, never inline at a
        # mint site.
        assert src.count('"caller_instance_id": current_instance_id') == 1


# ── E. the claim seam (S5) ─────────────────────────────────────────────────


class TestClaimSeam:
    """The chain is read off the persisted row and carried to the funnel."""

    def test_processing_context_carries_the_chain(self):
        ctx = ProcessingContext(
            instance_id="i",
            message_id="m",
            message="x",
            dispatch_chain={"caller_instance_id": "inst-ari"},
        )
        assert ctx.dispatch_chain == {"caller_instance_id": "inst-ari"}

    def test_processing_context_defaults_to_none(self):
        ctx = ProcessingContext(instance_id="i", message_id="m", message="x")
        assert ctx.dispatch_chain is None

    def test_pipeline_forwards_the_chain_to_the_funnel(self):
        import inspect

        from daemon.services import message_processing_pipeline as mpp

        src = inspect.getsource(mpp)
        assert "dispatch_chain=context.dispatch_chain" in src

    def test_task_processor_reads_the_reserved_key(self):
        from daemon.services import task_processor

        src = Path(task_processor.__file__).read_text()
        assert "message_metadata.get(DISPATCH_CHAIN_KEY)" in src


# ── F. the mint logic (S6) ─────────────────────────────────────────────────


class TestMintLogic:
    """The lane selection + the shared claim arithmetic."""

    def test_internal_agent_lane_is_byte_identical(self):
        """The legacy lane behaves exactly as before."""
        self_ = _MintSelf()
        self_._user_origin_windows["inst-caller"] = _live_direct_window("api")
        _mint(self_, "inst-child", "internal_agent:inst-caller")
        entry = self_._inherited_provenance_windows["inst-child"]
        assert entry["depth"] == 1
        assert entry["root_source"] == "api"
        assert entry["root_message_id"] == "root-msg-1"

    def test_agent_lane_with_a_valid_chain_claims(self):
        """The NEW lane: ``agent:`` + a chain naming a live caller."""
        self_ = _MintSelf()
        self_._user_origin_windows["inst-caller"] = _live_direct_window("api")
        _mint(
            self_,
            "inst-child",
            "agent:ari",
            dispatch_chain={
                "caller_instance_id": "inst-caller",
                "caller_agent_id": "ari",
            },
        )
        entry = self_._inherited_provenance_windows["inst-child"]
        assert entry["depth"] == 1
        assert entry["root_source"] == "api"

    def test_agent_lane_without_a_chain_pops(self):
        """No chain ⇒ fail-closed (today's behaviour, unchanged)."""
        self_ = _MintSelf()
        self_._user_origin_windows["inst-caller"] = _live_direct_window("api")
        _mint(self_, "inst-child", "agent:ari")
        assert "inst-child" not in self_._inherited_provenance_windows

    def test_agent_lane_naming_a_windowless_caller_pops(self):
        """The chain is a POINTER — no live window ⇒ POP."""
        self_ = _MintSelf()
        _mint(
            self_,
            "inst-child",
            "agent:ari",
            dispatch_chain={"caller_instance_id": "inst-nobody"},
        )
        assert "inst-child" not in self_._inherited_provenance_windows

    def test_non_agent_lane_with_a_chain_is_ignored(self):
        """Control (b) — the LANE check.

        A chain on a NON-``agent:`` source is IGNORED: the message lane
        can carry the reserved key on a row without ever having passed
        the job seam.
        """
        self_ = _MintSelf()
        self_._user_origin_windows["inst-caller"] = _live_direct_window("api")
        for source in ("api", "system:sweep_no_user_origin_wake", "internal_report:x"):
            _mint(
                self_,
                "inst-child",
                source,
                dispatch_chain={"caller_instance_id": "inst-caller"},
            )
            assert "inst-child" not in self_._inherited_provenance_windows

    def test_expired_caller_window_pops(self):
        """Liveness is re-checked AT CLAIM TIME."""
        self_ = _MintSelf()
        self_._user_origin_windows["inst-caller"] = _live_direct_window(
            "api", ttl_s=-60
        )
        _mint(
            self_,
            "inst-child",
            "agent:ari",
            dispatch_chain={"caller_instance_id": "inst-caller"},
        )
        assert "inst-child" not in self_._inherited_provenance_windows

    def test_depth_cap_pops(self):
        """The depth cap is shared by both lanes."""
        self_ = _MintSelf()
        now = _uj.now_iso()
        self_._inherited_provenance_windows["inst-caller"] = {
            "root_source": "api",
            "root_message_id": "root-msg-1",
            "root_stamped_at": now,
            "depth": 4,
            "expires_at": _uj.iso_plus(now, 1800),
        }
        _mint(
            self_,
            "inst-child",
            "agent:ari",
            dispatch_chain={"caller_instance_id": "inst-caller"},
        )
        assert "inst-child" not in self_._inherited_provenance_windows

    def test_window_entry_shape_is_unchanged(self):
        """The minted entry keeps the canonical shape."""
        self_ = _MintSelf()
        self_._user_origin_windows["inst-caller"] = _live_direct_window("api")
        _mint(
            self_,
            "inst-child",
            "agent:ari",
            dispatch_chain={"caller_instance_id": "inst-caller"},
        )
        entry = self_._inherited_provenance_windows["inst-child"]
        assert set(entry) == {
            "root_source",
            "root_message_id",
            "root_stamped_at",
            "depth",
            "expires_at",
        }

    def test_monotonic_decay_never_outlives_the_parent(self):
        """``expires_at = min(parent_expiry, now + TTL)``."""
        self_ = _MintSelf()
        now = _uj.now_iso()
        parent_expiry = _uj.iso_plus(now, 30)
        self_._user_origin_windows["inst-caller"] = {
            "source": "api",
            "message_id": "root-msg-1",
            "stamped_at": now,
            "expires_at": parent_expiry,
        }
        _mint(
            self_,
            "inst-child",
            "agent:ari",
            dispatch_chain={"caller_instance_id": "inst-caller"},
        )
        entry = self_._inherited_provenance_windows["inst-child"]
        minted = _uj.parse_iso_utc(entry["expires_at"])
        assert minted is not None
        assert minted <= _uj.parse_iso_utc(parent_expiry)

    def test_malformed_chain_is_fail_closed(self):
        """A malformed chain value never mints."""
        for bad in (
            {},
            {"caller_instance_id": ""},
            {"caller_instance_id": None},
            {"caller_instance_id": 12345},
            "not-a-dict",
        ):
            self_ = _MintSelf()
            self_._user_origin_windows["inst-caller"] = _live_direct_window("api")
            _mint(self_, "inst-child", "agent:ari", dispatch_chain=bad)
            assert "inst-child" not in self_._inherited_provenance_windows

    def test_silent_resume_never_mints_an_inherited_window(self):
        """The silent-resume edge — TODAY's semantics, pinned.

        A checkpoint resume (``silent=True``) never reaches the mint at
        all: the funnel skips the whole stamp/inherit block. The child
        therefore has NO inherited window and its protected act refuses
        with ``no-user-provenance``.
        """
        src = Path(inspect.getsourcefile(InstanceManager)).read_text()
        assert "if not silent:" in src
        # The inherit call is INSIDE the `if not silent:` block.
        inherit_idx = src.index("self._inherit_provenance_window(")
        silent_idx = src.index("if not silent:", inherit_idx - 4000)
        assert silent_idx < inherit_idx

    def test_retired_token_is_absent_from_the_tree(self):
        """DF-4's closure, re-proven in this file's own tree.

        BOTH trees are scanned (the retired token must not survive
        anywhere under ``daemon/`` OR ``tests/``) — the same rule DF-4's
        own registration pin enforces.
        """
        hits = []
        for scope in ("daemon", "tests"):
            for path in (REPO_ROOT / scope).rglob("*.py"):
                if RETIRED_TOKEN in path.read_text():
                    hits.append(str(path.relative_to(REPO_ROOT)))
        assert hits == []


# ── G. forgery control (c) — the HTTP reserved-source gate ────────────────


class TestHttpReservedSourceGate:
    """Control (c) — REST cannot forge the ``agent:`` lane."""

    def test_agent_prefix_is_reserved(self):
        from daemon.constants import RESERVED_SOURCE_PREFIXES, is_reserved_source

        assert "agent:" in RESERVED_SOURCE_PREFIXES
        assert is_reserved_source("agent:ari") is True

    def test_the_router_gate_returns_422(self):
        """The router rejects a reserved source with 422."""
        from daemon.routers import jobs_crud

        src = Path(jobs_crud.__file__).read_text()
        assert "is_reserved_source(body.source)" in src
        assert "status_code=422" in src


# ── the asyncio marker ─────────────────────────────────────────────────────

#: Set when the house pytest-asyncio plugin is NOT importable. Without
#: it, every ``async def`` test in this file is collected and SILENTLY
#: SKIPPED — the file would report green while asserting nothing.
_ASYNCIO_MARKER_MISSING = False

try:  # pragma: no cover — the marker is installed by the house plugin
    import pytest_asyncio  # noqa: F401
except ImportError:  # pragma: no cover
    _ASYNCIO_MARKER_MISSING = True


def test_asyncio_marker_is_available():
    """Guard: the async pins above must actually RUN, not silently skip.

    The pre-fix guard asserted only that an asyncio event-loop policy
    existed — which is ALWAYS true, on any interpreter. It never
    checked the thing that actually matters: whether the plugin that
    runs the async pins is importable. The absence is now recorded in
    a flag and asserted here.
    """
    assert not _ASYNCIO_MARKER_MISSING, (
        "pytest_asyncio is not importable — every async pin in this file "
        "would be SILENTLY SKIPPED, so the file would report green "
        "while asserting nothing"
    )
    assert asyncio.get_event_loop_policy() is not None
