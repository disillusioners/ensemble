"""The memoir embedding worker — degradation + catch-up (AC7).

Phase 3 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase3-plan.md`` T3.7; pack
``test/packs/memoir_embedding_degradation_test.sh``).

What this suite pins:

* restart catch-up — N pending rows after a "restart" (a fresh worker on
  the same DB) ⇒ the FIRST tick embeds at most the batch cap and later
  ticks drain the rest (the queue IS the NULL; no side table, no
  outbox, no manual action);
* graceful lane-down — with the embedding lane failing, recall still
  returns rows LEXICALLY through the wave-2 service with the honest
  footer disclosure (``missing_embedding`` / ``semantic_enabled=False``);
  the write is never blocked and the worker never takes the surface
  down;
* the operator requeue — the runbook's documented procedure (clear
  ``embedding_attempts``/``embedding_error``) resets a terminal row so
  the worker re-embeds it on the next tick, and the runbook file
  actually carries the procedure (AC7's documentation leg).
"""

from __future__ import annotations

import pathlib
import re

import pytest
from sqlmodel import SQLModel, Session, create_engine, text

from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_embedding_worker import (
    EMBEDDING_ATTEMPT_CAP,
    MemoirEmbeddingWorker,
    TERMINAL_ERROR_MARKER,
    _default_embed,
)
from daemon.services.memoir_retrieval import MemoirRetrievalService

WORKER_LOGGER = "daemon.services.memoir_embedding_worker"
RUNBOOK_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs"
    / "runbooks"
    / "memoir-embedding.md"
)


class _StatusError(Exception):
    def __init__(self, status_code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/memoir-degradation.db")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


# ── AC7: restart catch-up ────────────────────────────────────────────────


async def test_restart_with_pending_rows_catches_up_without_manual_action(repo):
    for index in range(12):
        repo.insert_episode(agent_id="ari", title=f"t{index}", body=f"body {index}")

    # "Restart": a FRESH worker on the SAME database — the queue is the
    # NULL itself, so the first tick's rescan IS the catch-up (D3.2).
    fresh_worker = MemoirEmbeddingWorker(repo, embedder=lambda t, **k: [0.1] * 1536)
    await fresh_worker._tick()
    assert fresh_worker._count_pending() == 4, (
        "the first tick after a restart embeds AT MOST the batch cap (8)"
    )
    assert fresh_worker._count_terminal() == 0

    await fresh_worker._tick()
    assert fresh_worker._count_pending() == 0
    rows = repo.fetch_latest("ari", limit=None)
    assert len(rows) == 12
    assert all(row.embedding is not None for row in rows)


async def test_the_first_tick_scans_all_nulls_not_a_delta(repo):
    """Boot catch-up: rows written while the worker was DOWN are picked up.

    Insert rows, run NO tick (simulating a down worker), insert more
    rows, then run the first tick of a fresh worker — the older rows are
    still in the queue (nothing was lost by the restart).
    """
    repo.insert_episode(agent_id="ari", title="before", body="written while down")
    fresh_worker = MemoirEmbeddingWorker(repo, embedder=lambda t, **k: [0.1] * 1536)
    repo.insert_episode(agent_id="ari", title="after", body="written later")
    await fresh_worker._tick()
    assert fresh_worker._count_pending() == 0


# ── AC7: graceful lane-down ──────────────────────────────────────────────


async def test_lane_down_recall_still_returns_rows_with_the_footer(repo):
    for index in range(5):
        repo.insert_episode(
            agent_id="ari",
            title=f"deployment notes {index}",
            body=f"the kubernetes rollout for service {index} completed",
        )

    # The embedding lane is DOWN: every embed fails transiently.
    def _embed(text, **kwargs):
        raise _StatusError(503, "embedding lane down")

    worker = MemoirEmbeddingWorker(repo, embedder=_embed)
    await worker._tick()
    await worker._tick()

    # Nothing was embedded — but the ROWS were never blocked (DD-20):
    # the write path inserted them and lexical search sees them.
    assert worker._count_pending() == 5
    assert worker._count_terminal() == 0  # transient errors never burn rows

    service = MemoirRetrievalService(
        repo,
        embedder=None,  # the query embedder is down with the lane
        embedding_model="text-embedding-3-small",
        embedding_dims=1536,
    )
    result = service.recall("ari", "kubernetes rollout")

    assert len(result.episodes) == 5, "lexical-only recall still returns rows"
    assert result.semantic_enabled is False
    assert result.missing_embedding == 5
    assert result.active_model == "text-embedding-3-small"
    assert result.footer, "the footer carries the honest disclosure"


async def test_lane_down_never_blocks_the_write(repo):
    # DD-20: the worker is a background consumer — a dead lane cannot
    # block or fail a memoir_write. The write path is the repository's
    # ONE INSERT, which never touches the embedder (D3.16: the embedder
    # is reachable only from this module).
    def _embed(text, **kwargs):
        raise RuntimeError("lane catastrophically down")

    worker = MemoirEmbeddingWorker(repo, embedder=_embed)
    new_id = repo.insert_episode(agent_id="ari", title="t", body="b")
    await worker._tick()
    rows = repo.fetch_latest("ari", limit=None)
    assert [row.id for row in rows] == [new_id], "the write landed untouched"


# ── AC7: the operator requeue ────────────────────────────────────────────


async def test_the_runbook_requeue_procedure_revives_a_terminal_row(repo):
    episode_id = repo.insert_episode(agent_id="ari", title="t", body="b")

    def _embed(text, **kwargs):
        raise _StatusError(503, "lane down")

    worker = MemoirEmbeddingWorker(repo, embedder=_embed)
    # Burn the row's whole attempt budget.
    for _ in range(EMBEDDING_ATTEMPT_CAP):
        await worker._tick()
    row = repo.fetch_latest("ari", limit=1)[0]
    assert row.embedding_attempts == EMBEDDING_ATTEMPT_CAP
    assert worker._count_terminal() == 1

    # The worker now skips the terminal row — no more embeds.
    calls: list[str] = []

    def _counting(text, **kwargs):
        calls.append(text)
        return [0.1] * 1536

    worker._embedder = _counting
    await worker._tick()
    assert calls == []

    # ── THE RUNBOOK PROCEDURE (docs/runbooks/memoir-embedding.md) ──────
    # Clear the attempt counter AND the error for the row; the queue
    # predicate (embedding IS NULL) makes it pending again.
    with Session(repo.engine) as session:
        session.exec(
            text(
                "UPDATE memoir_episodes SET embedding_attempts = 0, "
                "embedding_error = NULL WHERE id = :id"
            ),
            params={"id": episode_id},
        )
        session.commit()

    assert worker._count_terminal() == 0
    await worker._tick()
    assert calls == ["t\nb"], "the requeued row embeds on the next tick"
    row = repo.fetch_latest("ari", limit=1)[0]
    assert row.embedding is not None
    assert row.embedding_error is None


def test_the_runbook_carries_the_requeue_procedure():
    """AC7's documentation leg: the runbook exists and documents the SQL."""
    assert RUNBOOK_PATH.exists(), f"missing runbook: {RUNBOOK_PATH}"
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    assert "UPDATE memoir_episodes" in runbook
    assert "embedding_attempts" in runbook
    assert "embedding_error" in runbook
    assert re.search(r"ENSEMBLE_MEMOIR_EMBEDDING_TICK_S", runbook)
    assert "[MemoirEmbed]" in runbook  # the tick line is documented
    assert TERMINAL_ERROR_MARKER in runbook  # the terminal marker is documented
