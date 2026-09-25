"""Unit tests for the SnapshotExecutor + SnapshotService (PR4).

Covers:

* The per-instance capture pipeline (P1): read on the TARGET ONLY,
  R6a snapshot_digest exclusion, 40k input clamp, R11 digest parse,
  provenance block (§9d — LIVE banner + snapshot-born banner),
  effective-model stamping (SNAPSHOT_MODEL chain), terminal row
  writes, and the memory-pin eviction pass.
* Hazard tolerance: KeyError on hard-deleted rows and empty
  checkpoints both land as ``failed`` rows — never raised.
* The D3 row-ledger lane: ``capture_async`` inserts the ``running``
  row and the background task writes ``active``; the service-level
  boot sweep; the 600s committed wall clock fails loud.
* The §5.2 staleness compute: fresh/stale/expired thresholds,
  runtime-version drift, post-capture-advance warning.

The LLM call is monkeypatched at ``daemon.compaction`` — the executor
imports it lazily at call time, so the patch intercepts the real
seam (``call_summarization_llm_for_snapshot``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.snapshot.models import (
    SNAPSHOT_STATUS_ACTIVE,
    SNAPSHOT_STATUS_FAILED,
    SNAPSHOT_STATUS_RUNNING,
    Snapshot,
)
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.services.snapshot_executor import (
    SNAPSHOT_EXPIRED_AGE_DAYS,
    SNAPSHOT_FRESH_MAX_AGE_DAYS,
    SnapshotExecutor,
    SnapshotService,
    compute_staleness_report,
)


# ============================================================================
# Fakes
# ============================================================================


class FakeGraph:
    """Compiled-graph stand-in whose ``aget_state`` returns canned state."""

    def __init__(self, messages: list[Any] | None = None, error: Exception | None = None):
        self._messages = messages or []
        self._error = error

    async def aget_state(self, config: dict) -> Any:
        if self._error is not None:
            raise self._error
        return SimpleNamespace(values={"messages": list(self._messages)})


class SimpleNamespace:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class FakeInstanceRepo:
    """Dict-backed stand-in for the instance repository ``get``."""

    def __init__(self, rows: dict[str, Instance]) -> None:
        self._rows = rows

    def get(self, instance_id: str) -> Instance | None:
        return self._rows.get(instance_id)


class FakeManager:
    """Manager stand-in: dormant read + eviction + LLM config seams.

    ``send_message`` RAISES if called — the executor must never
    message its target (revive hazard, design §9).
    """

    def __init__(
        self,
        instance_repo: FakeInstanceRepo,
        graph: FakeGraph,
        compactor: Any = None,
    ) -> None:
        self._instance_repository = instance_repo
        self._graph = graph
        self.instances: dict[str, tuple[Any, str]] = {}
        self._compactor = compactor

    async def get_instance(self, instance_id: str) -> FakeGraph:
        if instance_id not in self._instance_repository._rows:
            raise KeyError(instance_id)  # instance_lifecycle.py:3887-3888 shape
        # Mirror the real cold-load: registers permanently.
        self.instances[instance_id] = (self._graph, "/agents/x")
        return self._graph

    async def send_message(self, *a: Any, **kw: Any) -> None:  # pragma: no cover
        raise AssertionError("SnapshotExecutor must NEVER message its target")


def _instance(
    instance_id: str,
    *,
    status: str = "completed",
    parent_id: str | None = None,
    agent_id: str = "coder",
    metadata: dict | None = None,
) -> Instance:
    return Instance(
        instance_id=instance_id,
        project_id="p1",
        agent_id=agent_id,
        agent_dir="/agents/coder",
        status=status,
        parent_id=parent_id,
        instance_metadata=metadata or {},
    )


def _compactor() -> Any:
    from daemon.compaction import ContextCompactor
    from daemon.config import CompactionConfig

    return ContextCompactor(
        config=CompactionConfig(),
        llm_config={"base_url": "http://x", "api_key": "k", "model": "session-model"},
    )


def _digest_llm_response() -> str:
    return (
        "Working state summary.\n"
        "## Decisions\n- chose the probe-first path\n"
        "## Gotchas\n- silent edit failures exist\n"
        "## Artifact refs\n- commit abc1234\n"
    )


@pytest.fixture
def engine():
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


@pytest.fixture
def repo(engine):
    return SnapshotRepository(engine)


def _install_llm_patch(monkeypatch, response: str | None = None, delay: float = 0.0, calls: list | None = None):
    """Patch the snapshot-side LLM seam at daemon.compaction."""
    from daemon import compaction as compaction_mod

    async def fake_call(compactor, prompt, context, *, system_message):
        if calls is not None:
            calls.append({"prompt": prompt, "system_message": system_message})
        if delay:
            await asyncio.sleep(delay)
        if response is None:
            raise RuntimeError("llm blew up")
        return response

    monkeypatch.setattr(
        compaction_mod, "call_summarization_llm_for_snapshot", fake_call
    )


def _running_row(target: str = "inst-1") -> Snapshot:
    return Snapshot(
        project_id="p1",
        created_by_agent_id="coder",
        target_instance_id=target,
        title="snap",
        status=SNAPSHOT_STATUS_RUNNING,
        runtime_version="0.14.2",
    )


# ============================================================================
# Capture pipeline
# ============================================================================


class TestCapturePipeline:
    def _scenario(self, monkeypatch, repo, *, target_status="completed", target_metadata=None):
        rows = {
            "inst-1": _instance("inst-1", status=target_status, metadata=target_metadata),
            "root-9": _instance("root-9", status="terminated"),
        }
        irepo = FakeInstanceRepo(rows)
        messages = [
            SimpleNamespace(**{"content": "explored the upgrade pipeline", "id": "m1"}),
            SimpleNamespace(**{"content": "found the defect", "id": "m2"}),
            # R6a block: a previously-injected warm-start digest.
            SimpleNamespace(
                **{
                    "content": "[SYSTEM CONTEXT: Agent Snapshot Digest]\nold digest body",
                    "id": "digest-block",
                    "additional_kwargs": {
                        "injected_message": True,
                        "context_kind": "snapshot_digest",
                    },
                }
            ),
        ]
        mgr = FakeManager(irepo, FakeGraph(messages=messages), compactor=_compactor())
        executor = SnapshotExecutor(mgr, repo)
        return mgr, executor

    def test_successful_capture_active_row_and_r6a_exclusion(
        self, engine, repo, monkeypatch
    ):
        calls: list[dict] = []
        _install_llm_patch(monkeypatch, _digest_llm_response(), calls=calls)
        mgr, executor = self._scenario(monkeypatch, repo)
        row = repo.create_with_embeddings(_running_row())

        out = asyncio.run(executor.capture(row))

        assert out.status == SNAPSHOT_STATUS_ACTIVE
        assert out.effective_model == "session-model"
        assert out.digest["decisions"] == ["chose the probe-first path"]
        assert out.digest["gotchas"] == ["silent edit failures exist"]
        assert out.digest["artifact_refs"] == ["commit abc1234"]
        # R6a: the digest block NEVER reaches the summarizer prompt.
        prompt = calls[0]["prompt"]
        assert "old digest body" not in prompt
        assert "found the defect" in prompt
        # R11 persona passed as the REQUIRED system_message.
        assert "EXPERIENCE, not its transcript" in calls[0]["system_message"]
        # §9d provenance block atop the digest.
        prov = out.digest["provenance"]
        assert prov["source_instance_id"] == "inst-1"
        assert prov["prompt_version"]
        assert prov["captured_at"]
        assert prov["effective_model"] == "session-model"
        # Memory-pin eviction: the entry the read inserted is popped.
        assert "inst-1" not in mgr.instances

    def test_provenance_live_banner_and_snapshot_banner(
        self, engine, repo, monkeypatch
    ):
        _install_llm_patch(monkeypatch, _digest_llm_response())
        # Live target (non-terminal) + R6b snapshot-born stamp.
        mgr, executor = self._scenario(
            monkeypatch,
            repo,
            target_status="running",
            target_metadata={"spawned_from_snapshot_id": "snap-parent"},
        )
        row = repo.create_with_embeddings(_running_row())
        out = asyncio.run(executor.capture(row))
        banners = "\n".join(out.digest["provenance"]["banner"])
        assert "LIVE-CAPTURE" in banners
        assert "SNAPSHOT-BORN" in banners and "snap-parent" in banners
        assert out.digest["provenance"]["captured_live"] is True
        assert out.digest["provenance"]["spawned_from_snapshot_id"] == "snap-parent"

    def test_pre_pinned_target_not_evicted(self, engine, repo, monkeypatch):
        _install_llm_patch(monkeypatch, _digest_llm_response())
        mgr, executor = self._scenario(monkeypatch, repo)
        # The target was ALREADY in manager.instances before the read.
        mgr.instances["inst-1"] = (mgr._graph, "/agents/other")
        row = repo.create_with_embeddings(_running_row())
        asyncio.run(executor.capture(row))
        # Eviction pops only entries the READ inserted — the
        # pre-existing pin survives.
        assert "inst-1" in mgr.instances

    def test_keyerror_hard_deleted_row_fails_soft(self, engine, repo, monkeypatch):
        calls: list[dict] = []
        _install_llm_patch(monkeypatch, _digest_llm_response(), calls=calls)
        irepo = FakeInstanceRepo({})  # target row GONE
        mgr = FakeManager(irepo, FakeGraph(), compactor=_compactor())
        executor = SnapshotExecutor(mgr, repo)
        row = repo.create_with_embeddings(_running_row())
        out = asyncio.run(executor.capture(row))
        assert out.status == SNAPSHOT_STATUS_FAILED
        assert "not found" in out.digest["error"]
        assert calls == []  # LLM never invoked

    def test_empty_checkpoint_fails(self, engine, repo, monkeypatch):
        _install_llm_patch(monkeypatch, _digest_llm_response())
        irepo = FakeInstanceRepo({"inst-1": _instance("inst-1")})
        mgr = FakeManager(irepo, FakeGraph(messages=[]), compactor=_compactor())
        executor = SnapshotExecutor(mgr, repo)
        row = repo.create_with_embeddings(_running_row())
        out = asyncio.run(executor.capture(row))
        assert out.status == SNAPSHOT_STATUS_FAILED
        assert "empty checkpoint" in out.digest["error"]

    def test_llm_failure_records_failed_row(self, engine, repo, monkeypatch):
        _install_llm_patch(monkeypatch, response=None)  # fake raises
        mgr, executor = self._scenario(monkeypatch, repo)
        row = repo.create_with_embeddings(_running_row())
        out = asyncio.run(executor.capture(row))
        assert out.status == SNAPSHOT_STATUS_FAILED
        assert "RuntimeError" in out.digest["error"]
        # §9 watchover asterisk is named in the failure digest.
        assert "watchover" in out.digest["watchover_note"]

    def test_input_clamp_40k_engages(self, engine, repo, monkeypatch):
        calls: list[dict] = []
        _install_llm_patch(monkeypatch, _digest_llm_response(), calls=calls)
        from daemon.compaction import TRUNCATION_GLOBAL_INPUT_CAP_CHARS

        big = "x" * (TRUNCATION_GLOBAL_INPUT_CAP_CHARS + 5000)
        irepo = FakeInstanceRepo({"inst-1": _instance("inst-1")})
        messages = [SimpleNamespace(**{"content": big, "id": "m1"})]
        mgr = FakeManager(irepo, FakeGraph(messages=messages), compactor=_compactor())
        executor = SnapshotExecutor(mgr, repo)
        row = repo.create_with_embeddings(_running_row())
        out = asyncio.run(executor.capture(row))
        assert out.status == SNAPSHOT_STATUS_ACTIVE
        assert out.digest["provenance"]["input_clamped"] is True
        assert len(calls[0]["prompt"]) < TRUNCATION_GLOBAL_INPUT_CAP_CHARS + 500


# ============================================================================
# D3 row-ledger lane (SnapshotService)
# ============================================================================


class TestSnapshotServiceLane:
    def _service(self, monkeypatch, repo, response=_digest_llm_response(), delay=0.0, target_status="completed"):
        rows = {"inst-1": _instance("inst-1", status=target_status, parent_id="root-9"),
                "root-9": _instance("root-9", status="terminated")}
        irepo = FakeInstanceRepo(rows)
        messages = [SimpleNamespace(**{"content": "did the work", "id": "m1"})]
        mgr = FakeManager(irepo, FakeGraph(messages=messages), compactor=_compactor())
        _install_llm_patch(monkeypatch, response, delay=delay)
        return SnapshotService(mgr, repo), mgr

    def test_capture_async_running_row_then_active_terminal(
        self, engine, repo, monkeypatch
    ):
        service, mgr = self._service(monkeypatch, repo)

        async def _driver():
            result = await service.capture_async(
                target_instance_id="inst-1",
                project_id="p1",
                created_by_agent_id="coder",
                title="snap",
                judgment_tags=["kind:implementation", "subsystem:upgrade-pipeline"],
                task_summary="did the work",
            )
            assert result["status"] == SNAPSHOT_STATUS_RUNNING
            # D3 ledger row: running + tags stamped pre-capture.
            row = repo.get(result["snapshot_id"])
            assert row.status == SNAPSHOT_STATUS_RUNNING
            assert "project:p1" in row.domain_tags
            assert "agent:coder" in row.domain_tags
            assert "role:coder" in row.domain_tags
            assert "lineage:root-9" in row.domain_tags  # parent_id chain
            assert "kind:implementation" in row.domain_tags
            # Drain the background task ON THE SAME LOOP (production
            # runs it on the daemon's long-lived loop).
            await asyncio.gather(*list(service._tasks))
            return result

        result = asyncio.run(_driver())
        snap_id = result["snapshot_id"]
        # Terminal write landed post-drain.
        final = repo.get(snap_id)
        assert final.status == SNAPSHOT_STATUS_ACTIVE
        assert final.digest["decisions"]

    def test_capture_async_invalid_tags_fail_loud_before_insert(
        self, engine, repo, monkeypatch
    ):
        service, _mgr = self._service(monkeypatch, repo)
        with pytest.raises(ValueError, match="fixed enum"):
            asyncio.run(
                service.capture_async(
                    target_instance_id="inst-1",
                    project_id="p1",
                    created_by_agent_id="coder",
                    title="snap",
                    judgment_tags=["kind:bogus", "subsystem:x"],
                )
            )
        assert repo.count_by_project("p1") == 0  # no ledger row written

    def test_wall_clock_cap_fails_loud(self, engine, repo, monkeypatch):
        service, _mgr = self._service(monkeypatch, repo, delay=0.5)
        service._wall_clock_s = 0.05  # drill-scale committed cap

        async def _driver():
            result = await service.capture_async(
                target_instance_id="inst-1",
                project_id="p1",
                created_by_agent_id="coder",
                title="snap",
                judgment_tags=["kind:review", "topic:x"],
            )
            await asyncio.gather(*list(service._tasks))
            return result

        result = asyncio.run(_driver())
        final = repo.get(result["snapshot_id"])
        assert final.status == SNAPSHOT_STATUS_FAILED
        assert "wall clock" in final.digest["error"]

    def test_service_boot_sweep(self, engine, repo, monkeypatch):
        service, _mgr = self._service(monkeypatch, repo)
        repo.create_with_embeddings(_running_row())
        flipped = asyncio.run(service.sweep_orphaned_running())
        assert flipped == 1
        assert repo.find_by_status("interrupted")[0].target_instance_id == "inst-1"


# ============================================================================
# Embeddings-at-creation wiring — e2e (Wave 2a review fold-in #1)
# ============================================================================


class RecordingEmbeddingService:
    """Mocked SnapshotEmbeddingService: records the rows it receives."""

    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def update_snapshot_embeddings(self, row: Any) -> None:
        self.rows.append(row)


class TestEmbeddingWiringE2E:
    def test_active_finish_row_fires_embedding_task_once_with_post_write_row(
        self, engine, repo, monkeypatch
    ):
        """E2E wiring: a full ``capture()`` that lands an ACTIVE write
        kicks off the fire-and-forget embedding task exactly ONCE,
        with the POST-WRITE row (the re-fetched terminal view, not
        the pre-write in-memory row) — and the task is registered in
        the executor's tracked set so shutdown can drain it."""
        _install_llm_patch(monkeypatch, _digest_llm_response())
        rows = {
            "inst-1": _instance("inst-1", status="completed"),
        }
        irepo = FakeInstanceRepo(rows)
        messages = [
            SimpleNamespace(**{"content": "explored the upgrade pipeline", "id": "m1"}),
            SimpleNamespace(**{"content": "found the defect", "id": "m2"}),
        ]
        mgr = FakeManager(irepo, FakeGraph(messages=messages), compactor=_compactor())
        emb_service = RecordingEmbeddingService()
        executor = SnapshotExecutor(mgr, repo, snapshot_embedding_service=emb_service)
        row = repo.create_with_embeddings(_running_row())

        async def _drive() -> Any:
            out = await executor.capture(row)
            # Registered exactly once, mirroring the ``_run_capture``
            # lane's registration shape. No loop yield has happened
            # since create_task, so the task is still tracked here.
            (emb_task,) = list(executor._tasks)
            # Let the fire-and-forget run to completion in-loop.
            await emb_task
            return out

        out = asyncio.run(_drive())

        assert out.status == SNAPSHOT_STATUS_ACTIVE
        # Exactly ONE fire-and-forget fired, once.
        assert len(emb_service.rows) == 1
        # The service saw the POST-WRITE row: terminal status, the
        # parsed digest, and the effective model stamped.
        seen = emb_service.rows[0]
        assert seen.id == out.id
        assert seen.status == SNAPSHOT_STATUS_ACTIVE
        assert seen.effective_model == "session-model"
        assert seen.digest["decisions"] == ["chose the probe-first path"]
        # The task completed cleanly and de-registered itself
        # (add_done_callback(self._tasks.discard) — same lifecycle
        # as the _run_capture lane).
        assert executor._tasks == set()


# ============================================================================
# §5.2 staleness compute
# ============================================================================


class TestStalenessCompute:
    def _snap(self, age_days: float, runtime_version: str = "0.14.2", captured_live: bool = False) -> Snapshot:
        snap = _running_row()
        snap.status = SNAPSHOT_STATUS_ACTIVE
        snap.runtime_version = runtime_version
        snap.created_at = (
            datetime.now(timezone.utc) - timedelta(days=age_days)
        ).isoformat()
        snap.digest = {"provenance": {"captured_live": captured_live}}
        return snap

    def test_fresh_stale_expired_thresholds(self):
        assert compute_staleness_report(self._snap(1))["freshness"] == "fresh"
        assert compute_staleness_report(self._snap(SNAPSHOT_FRESH_MAX_AGE_DAYS + 1))[
            "freshness"
        ] == "stale"
        assert compute_staleness_report(self._snap(SNAPSHOT_EXPIRED_AGE_DAYS + 1))[
            "freshness"
        ] == "expired"

    def test_version_drift_warning(self):
        report = compute_staleness_report(
            self._snap(1, runtime_version="0.13.9"),
            current_runtime_version="0.14.2",
        )
        assert any("runtime version drift" in w for w in report["warnings"])
        assert report["repo_state"] is None  # verify=git is Wave 2 opt-in

    def test_post_capture_advance_warning(self):
        # Captured LIVE, target has since gone terminal → advanced.
        report = compute_staleness_report(
            self._snap(1, captured_live=True),
            target_instance_status="completed",
        )
        assert any("tree advanced post-capture" in w for w in report["warnings"])
        # Captured at terminal — no advance possible, no warning.
        report2 = compute_staleness_report(
            self._snap(1, captured_live=False),
            target_instance_status="completed",
        )
        assert not any("tree advanced" in w for w in report2["warnings"])

    def test_shape(self):
        report = compute_staleness_report(self._snap(2))
        assert set(report) == {"snapshot_age_days", "freshness", "warnings", "repo_state"}
        assert isinstance(report["snapshot_age_days"], float)
