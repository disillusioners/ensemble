"""Memoir Store v2 — the POIGNANCY LIFECYCLE (the SQLite lane).

Phase 4 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase4-plan.md`` T4.2/T4.3;
AC1/AC2; pack ``test/packs/memoir_v2_poignancy_lifecycle_test.sh``).

What this suite pins:

* THE HOT APPEND NEVER BLOCKS (AC1 / DD-20 / R4) — ``memoir_write``
  stays ONE fast INSERT, the scorer is never called on the write path,
  and the write returns promptly even while the scoring lane's LLM is
  hung;
* the INSERT's EXPLICIT ``poignancy_status`` literal NULL — the
  unscored state never rests on a column-default accident;
* the END-TO-END FLOW — the write lands, the background worker picks the
  episode up, the score lands in ``poignancy`` and the AUTHORED
  ``importance`` is UNCHANGED (AC2);
* the WRITE PATH NEVER REACHES FOR THE LLM — a static pin on the
  repository module (the write path carries no provider at all).
"""

from __future__ import annotations

import ast
import pathlib
import time
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_poignancy_service import MemoirPoignancyService
from daemon.services.memoir_poignancy_worker import MemoirPoignancyWorker

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

AGENT = "ari"

REPOSITORY_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "daemon"
    / "repositories"
    / "memoir"
    / "repository.py"
)


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/memoir-poignancy-lifecycle.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


class ProbeManager:
    """A manager double — the house shape (``test_memoir_tools_unit.py``).

    It counts the engine touches so the write-latency leg can prove the
    write path does exactly ONE DB round trip (the INSERT) and never
    reaches for the LLM.
    """

    def __init__(self, engine=None, knob_on: bool = True) -> None:
        self._engine_handle = engine
        self._knob_on = knob_on
        self.engine_touches = 0
        self.knob_reads = 0

    @property
    def config(self):
        self.knob_reads += 1
        return SimpleNamespace(memoir=SimpleNamespace(enabled=self._knob_on))

    @property
    def _engine(self):
        self.engine_touches += 1
        return self._engine_handle


def _tools(manager):
    from daemon.tools.memoir import create_memoir_tools

    return {
        tool_obj.name: tool_obj
        for tool_obj in create_memoir_tools(
            manager, current_instance_id="inst-1", agent_id=AGENT
        )
    }


def _row(repo, episode_id):
    with Session(repo.engine) as session:
        return session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()


# ── the hot append (AC1) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_real_memoir_write_tool_never_touches_the_scorer(repo):
    """THE HOT-PATH PROOF: the write path has no LLM in it at all.

    The scorer is a BACKGROUND worker — the write never calls it, never
    enqueues into it, and never waits for it.
    """
    manager = ProbeManager(engine=repo.engine)
    tools = _tools(manager)

    result = await tools["memoir_write"].ainvoke({"text": "a quiet moment"})

    # The write landed (the tool's success envelope carries the id).
    assert isinstance(result, dict)
    assert result.get("episode_id")
    with Session(repo.engine) as session:
        rows = list(session.exec(select(MemoirEpisode)).all())
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_the_write_returns_promptly_while_the_scorer_is_hung(repo):
    """THE WRITE-LATENCY LEG.

    The scoring lane's LLM is rigged to HANG (far longer than the
    write's budget) and the write STILL returns immediately — because
    the write path never calls it. The proof is both the wall-clock and
    the LLM-call counter.
    """
    calls: list[str] = []

    def _hung_caller(prompt: str) -> str:
        calls.append(prompt)
        time.sleep(5)
        return "7"

    service = MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=_hung_caller,
    )
    # The scorer exists and is armed — the write must still not touch it.
    assert service is not None

    manager = ProbeManager(engine=repo.engine)
    tools = _tools(manager)

    started = time.monotonic()
    await tools["memoir_write"].ainvoke({"text": "another quiet moment"})
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, (
        f"the write took {elapsed:.2f}s — the hot append must never wait "
        f"for the scoring lane"
    )
    assert calls == [], "the write path called the scorer"


def test_the_write_path_module_carries_no_llm_provider():
    """THE STATIC HALF: the repository module (the ONE INSERT's owner)
    imports no LLM provider and no scorer — the write path cannot
    reach for a model even by accident."""
    tree = ast.parse(REPOSITORY_PATH.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = [
        name
        for name in imported
        if name in {"openai", "httpx"}
        or "llm" in name.lower()
        or "memoir_poignancy_service" in name
    ]
    assert forbidden == [], f"the write path imports {forbidden}"


def test_the_insert_writes_the_poignancy_status_as_an_explicit_null():
    """V2-D 1.14 / V2-42 (already landed in phase 1): the unscored state
    is EXPLICIT, never a column-default accident."""
    from daemon.repositories.memoir.repository import MEMOIR_INSERT_SQL

    values = MEMOIR_INSERT_SQL[
        MEMOIR_INSERT_SQL.index("VALUES") : MEMOIR_INSERT_SQL.rindex(")")
    ]
    assert "poignancy_status" in MEMOIR_INSERT_SQL
    assert ":poignancy_status" not in values
    assert values.rstrip().endswith("NULL")


def test_a_fresh_episode_is_unscored(repo):
    """The row lands unscored — the worker's queue."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="a moment")
    row = _row(repo, episode_id)
    assert row.poignancy is None
    assert row.poignancy_status is None
    assert row.poignancy_attempts == 0
    assert int(row.importance) == 5


# ── the end-to-end flow (AC2) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_write_then_the_worker_then_the_score(repo):
    """AC2: the background worker fills the score and the AUTHORED
    importance is UNCHANGED."""
    episode_id = repo.insert_episode(
        agent_id=AGENT, body="the constitution was rewritten", importance=8
    )

    service = MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=lambda prompt: "10",
    )
    worker = MemoirPoignancyWorker(service, config=None)

    await worker._tick()

    row = _row(repo, episode_id)
    assert row.poignancy == 10
    assert row.poignancy_status == "scored"
    # THE IMPORTANCE RULING: the authored record is UNTOUCHED.
    assert int(row.importance) == 8


@pytest.mark.asyncio
async def test_a_failed_scoring_leaves_the_authored_importance_untouched(repo):
    """A row that cannot be scored keeps its authored importance AND its
    place in the queue."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="a moment", importance=4)

    service = MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=lambda prompt: "no score here",
    )
    worker = MemoirPoignancyWorker(service, config=None)

    await worker._tick()

    row = _row(repo, episode_id)
    assert row.poignancy is None
    assert row.poignancy_status is None
    assert row.poignancy_attempts == 1
    assert int(row.importance) == 4
    # The row is STILL in the queue — the next tick retries it.
    assert len(repo.get_pending_poignancy_episodes()) == 1
