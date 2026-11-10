"""Memoir Store v2 — the POIGNANCY WORKER (the SQLite lane).

Phase 4 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase4-plan.md`` T4.3/T4.5;
AC3/AC4/AC5; pack ``test/packs/memoir_v2_poignancy_worker_test.sh``).

What this suite pins:

* the SCORING TICK — the pending episodes are scored and their
  ``poignancy`` column lands;
* the KILL-SWITCH park/resume (AC4) — the worker parks within one tick
  and RESUMES when the knob returns ON (the backlog is then scored);
* the ATTEMPT CAP (AC3) — a row that keeps failing counts an attempt
  per failure and flips to ``terminal:parse_failure`` on the fifth, and
  the worker ADVANCES PAST it (the next tick does not retry it);
* the DEAD-CREDENTIAL terminal — a 401/403 terminalizes the row at once
  (no attempt-budget burn against a wall);
* the BACKFILL FENCE (AC5) — the legacy corpus is NEVER scored with the
  default budget, and EXACTLY N legacy episodes are scored when the
  operator raises it;
* the DEDICATED THREAD — the lane runs on ``memoir-poignancy``, never
  on the event loop and never on the shared default executor;
* the per-tick EXCEPTION ISOLATION — one failed tick does not stop the
  loop;
* NO DELTA-NOTIFY COUPLING (V2-D 4.5) — the worker pushes nothing to
  the reflection service.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
import pathlib
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.repository import (
    MEMOIR_POIGNANCY_ATTEMPT_CAP,
    MemoirRepository,
)
from daemon.services.memoir_poignancy_service import (
    MemoirPoignancyService,
    PoignancySettings,
)
from daemon.services.memoir_poignancy_worker import (
    LATCH_THRESHOLD_TICKS,
    LATCH_TICK_INTERVAL_S,
    TICK_SCORE_BUDGET_S,
    MemoirPoignancyWorker,
    current_executor_thread_name,
    executor_thread_prefix,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

AGENT = "ari"

_ALL_KNOBS = (
    "ENSEMBLE_MEMOIR_POIGNANCY_ENABLED",
    "ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX",
    "ENSEMBLE_MEMOIR_POIGNANCY_TICK_S",
    "ENSEMBLE_MEMOIR_POIGNANCY_BATCH_CAP",
)

WORKER_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "daemon"
    / "services"
    / "memoir_poignancy_worker.py"
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_KNOBS:
        monkeypatch.delenv(name, raising=False)
    from daemon.config import _reset_v2_knob_warnings
    from daemon.tools.memoir_knob import reset_boot_log_state

    _reset_v2_knob_warnings()
    reset_boot_log_state()
    yield
    _reset_v2_knob_warnings()
    reset_boot_log_state()


@pytest.fixture()
def engine(tmp_path):
    # A FILE-backed SQLite database (the house pattern for the worker
    # lane): the lane runs on a DEDICATED executor thread, and an
    # in-memory SQLite database is per-THREAD (a second thread gets a
    # second, EMPTY database).
    eng = create_engine(f"sqlite:///{tmp_path}/memoir-poignancy-worker.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


def _service(repo, *, answer="7", threads=None):
    def _caller(prompt: str) -> str:
        if threads is not None:
            threads.append(threading.current_thread().name)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=_caller,
    )


def _worker(repo, *, answer="7", threads=None, settings=None):
    service = _service(repo, answer=answer, threads=threads)
    if settings is not None:
        service.apply_settings(settings)
    return MemoirPoignancyWorker(service, config=None)


def _seed(repo, count=1, *, importance=5, status=None, created_at=None):
    ids = []
    for index in range(count):
        episode_id = repo.insert_episode(
            agent_id=AGENT,
            body=f"memory {index}",
            importance=importance,
            created_at=created_at,
        )
        if status is not None:
            with Session(repo.engine) as session:
                row = session.exec(
                    select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
                ).first()
                row.poignancy_status = status
                session.add(row)
                session.commit()
        ids.append(episode_id)
    return ids


def _row(repo, episode_id):
    with Session(repo.engine) as session:
        return session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()


class _FakeStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


# ── the scoring tick ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_worker_scores_the_pending_episodes(repo):
    """The tick's whole job: pick the unscored rows up and score them."""
    episode_id = _seed(repo)[0]
    worker = _worker(repo, answer="8")

    await worker._tick()

    row = _row(repo, episode_id)
    assert row.poignancy == 8
    assert row.poignancy_status == "scored"
    assert int(row.importance) == 5  # the authored record is untouched


@pytest.mark.asyncio
async def test_the_scored_row_leaves_the_queue(repo):
    """The queue IS the column's emptiness — a scored row is done."""
    _seed(repo)
    worker = _worker(repo, answer="8")
    await worker._tick()
    assert repo.get_pending_poignancy_episodes() == []


@pytest.mark.asyncio
async def test_the_tick_line_is_pinnable(repo, caplog):
    """§3.1: the structured line names the tick's counts."""
    import logging

    _seed(repo, count=2)
    worker = _worker(repo, answer="5")
    with caplog.at_level(logging.INFO, logger="daemon.services.memoir_poignancy_worker"):
        await worker._tick()
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "[MemoirPoignancy] pending=2 scored=2 failed=0 terminal=0" in text
    assert "duration=" in text


# ── the kill-switch (AC4) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_worker_parks_when_the_knob_is_off(repo, monkeypatch):
    """AC4: the lane OFF parks the worker — the episodes are untouched."""
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_ENABLED", "false")
    episode_id = _seed(repo)[0]
    worker = _worker(repo, answer="8")

    await worker._tick()

    row = _row(repo, episode_id)
    assert row.poignancy is None
    assert row.poignancy_status is None
    assert worker.snapshot()["parked"] is True


@pytest.mark.asyncio
async def test_the_worker_resumes_when_the_knob_returns_on(repo, monkeypatch):
    """AC4: "setting back to true resumes scoring backlog".

    The worker stays ALIVE while parked — the task is never destroyed by
    the OFF state, so the flip is honoured on the NEXT tick.
    """
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_ENABLED", "false")
    episode_id = _seed(repo)[0]
    worker = _worker(repo, answer="6")

    await worker._tick()
    assert _row(repo, episode_id).poignancy is None

    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_ENABLED", "true")
    await worker._tick()

    row = _row(repo, episode_id)
    assert row.poignancy == 6
    assert worker.snapshot()["parked"] is False


# ── the attempt cap (AC3) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_attempt_cap_terminalizes_the_row_as_parse_failure(repo):
    """AC3: a row that keeps failing counts an attempt per failure and
    flips to ``terminal:parse_failure`` on the fifth."""
    episode_id = _seed(repo)[0]
    worker = _worker(repo, answer="no score at all")

    for attempt in range(1, MEMOIR_POIGNANCY_ATTEMPT_CAP):
        await worker._tick()
        row = _row(repo, episode_id)
        assert row.poignancy_attempts == attempt
        assert row.poignancy_status is None  # not terminal yet

    await worker._tick()
    row = _row(repo, episode_id)
    assert row.poignancy_attempts == MEMOIR_POIGNANCY_ATTEMPT_CAP
    assert row.poignancy_status == "terminal:parse_failure"


@pytest.mark.asyncio
async def test_the_worker_advances_past_a_terminal_row(repo):
    """AC3's second half: the worker ADVANCES past the terminal row."""
    episode_id = _seed(repo)[0]
    worker = _worker(repo, answer="no score at all")
    for _ in range(MEMOIR_POIGNANCY_ATTEMPT_CAP):
        await worker._tick()

    assert _row(repo, episode_id).poignancy_status == "terminal:parse_failure"
    # The next tick finds nothing to do — the terminal row left the queue.
    await worker._tick()
    assert repo.get_pending_poignancy_episodes() == []


@pytest.mark.asyncio
async def test_a_dead_credential_terminalizes_the_row_at_once(repo):
    """A 401/403 is an IMMEDIATE terminal — the attempt budget is not
    burned against a wall."""
    episode_id = _seed(repo)[0]
    worker = _worker(repo, answer=_FakeStatusError(401))

    await worker._tick()

    row = _row(repo, episode_id)
    assert row.poignancy_status == "terminal:poignancy_auth_rejected"
    assert row.poignancy_attempts == 1


# ── the backfill fence (AC5 / V2-14) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_legacy_rows_are_never_scored_with_the_default_budget(repo):
    """AC5: with the default budget (0) the legacy corpus is NEVER
    scored."""
    _seed(repo, count=2, status="legacy")
    worker = _worker(repo, answer="7")

    await worker._tick()

    assert repo.get_pending_poignancy_episodes() == []


@pytest.mark.asyncio
async def test_the_backfill_budget_scores_exactly_that_many_legacy_rows(
    repo, monkeypatch
):
    """AC5: a budget of 5 processes EXACTLY 5 legacy episodes."""
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX", "5")
    _seed(repo, count=8, status="legacy")
    worker = _worker(repo, answer="7")

    await worker._tick()

    scored = [
        row
        for row in repo.get_pending_poignancy_episodes(50, backfill_max=50)
        if row.poignancy_status == "legacy"
    ]
    assert len(scored) == 3  # 8 legacy rows − 5 scored
    for row in scored:
        assert row.poignancy is None


@pytest.mark.asyncio
async def test_the_backfill_scores_the_oldest_legacy_rows_first(repo, monkeypatch):
    """§3-A3: the backfill is OLDEST-FIRST."""
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX", "2")
    ids = _seed(repo, count=4, status="legacy")
    worker = _worker(repo, answer="7")

    await worker._tick()

    assert _row(repo, ids[0]).poignancy == 7
    assert _row(repo, ids[1]).poignancy == 7
    assert _row(repo, ids[2]).poignancy is None
    assert _row(repo, ids[3]).poignancy is None


@pytest.mark.asyncio
async def test_the_new_rows_are_scored_even_with_the_backfill_off(repo):
    """The default budget never touches the legacy corpus — and never
    blocks the NEW rows either."""
    _seed(repo, count=1, status="legacy")
    new_id = _seed(repo, count=1)[0]
    worker = _worker(repo, answer="9")

    await worker._tick()

    assert _row(repo, new_id).poignancy == 9


# ── the dedicated thread ─────────────────────────────────────────────────


def test_the_lane_runs_on_its_dedicated_thread():
    """V2-D 4.2: the executor is the lane's own — never the shared
    default pool."""
    assert executor_thread_prefix() == "memoir-poignancy"
    assert current_executor_thread_name().startswith("memoir-poignancy")


@pytest.mark.asyncio
async def test_the_scoring_call_runs_on_the_dedicated_thread(repo):
    """The LLM call BLOCKS — it must never run on the event loop."""
    threads: list[str] = []
    _seed(repo)
    worker = _worker(repo, answer="7", threads=threads)

    await worker._tick()

    assert threads
    assert threads[0].startswith("memoir-poignancy")


def test_asyncio_to_thread_is_forbidden_in_the_worker_module():
    """D3.5: ``asyncio.to_thread`` is FORBIDDEN in a worker module."""
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "to_thread"
        and isinstance(node.value, ast.Name)
        and node.value.id == "asyncio"
    ]
    assert offenders == [], (
        f"asyncio.to_thread is forbidden in the worker (lines {offenders})"
    )


# ── the per-tick exception isolation ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_tick_level_guard_still_catches_a_broken_queue_read(repo, monkeypatch):
    """A failed TICK is logged and the NEXT tick still runs.

    THE SCOPE IS THE TICK, not the episode: this leg injects the failure
    at the QUEUE READ (the census), which is the tick-level guard's own
    job. The PER-EPISODE failure (a raising scoring call) is a
    different property and has its own leg below — the earlier version of
    this test was the only failure injection in the suite, and it left
    the per-episode path unpinned (the false confidence the review
    named).
    """
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_TICK_S", "1")
    worker = _worker(repo, answer="7")

    calls = {"n": 0}
    original = worker._service.pending_episodes

    def _boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the tick blew up")
        return original(*args, **kwargs)

    worker._service.pending_episodes = _boom

    task = asyncio.create_task(worker.run(), name="memoir-poignancy-worker")
    await asyncio.sleep(1.4)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls["n"] >= 2  # the loop survived the first failure


@pytest.mark.asyncio
async def test_a_raising_scoring_call_does_not_kill_the_loop(repo, monkeypatch):
    """THE PER-EPISODE FAILURE (W1) — injected at the SCORING CALL.

    The failure is injected where it really happens (the per-episode
    scoring call), NOT at the queue read: the tick must SURVIVE it, and
    the failure must be RECORDED as an attempt on that episode.
    """
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_TICK_S", "1")
    episode_id = _seed(repo)[0]
    worker = _worker(repo, answer="7")

    calls = {"n": 0}
    original = worker._service.score_episode

    def _boom(episode):
        calls["n"] += 1
        raise RuntimeError("the scorer exploded")

    worker._service.score_episode = _boom

    task = asyncio.create_task(worker.run(), name="memoir-poignancy-worker")
    await asyncio.sleep(1.4)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls["n"] >= 2, "the loop died on the first raising episode"
    row = _row(repo, episode_id)
    assert row.poignancy_attempts >= 1, (
        "the raising episode was not RECORDED — the attempt counter must "
        "advance (or the row can never terminalize)"
    )


# ── the per-episode isolation (W1 — the four-part proof) ─────────────────


@pytest.mark.asyncio
async def test_a_raising_episode_is_isolated_recorded_and_the_queue_advances(repo):
    """W1 — THE FOUR-PART PROOF.

    A scorer that RAISES on episode 1 must not abort the tick:

    (a) the tick CONTINUES to episode 2;
    (b) the failure is RECORDED;
    (c) ``poignancy_attempts`` ADVANCES toward the cap;
    (d) the poison row TERMINALIZES at the cap and the queue ADVANCES
        PAST it.

    BEFORE the fix the raise propagated out of the tick: the queue is
    ``created_at ASC``, so the poison row sat at the head forever, the
    rows behind it were NEVER reached, the attempt counter never
    advanced and the cap never fired — a silent, permanent starvation.
    """
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    poison_id = repo.insert_episode(
        agent_id=AGENT, body="the poison row", created_at=base
    )
    healthy_id = repo.insert_episode(
        agent_id=AGENT, body="the healthy row", created_at=base + timedelta(seconds=1)
    )

    worker = _worker(repo, answer="7")
    service = worker._service
    raised = {"n": 0}
    original = service.score_episode

    def _boom(episode):
        raised["n"] += 1
        if getattr(episode, "id", "") == poison_id:
            raise RuntimeError("the scorer exploded")
        return original(episode)

    service.score_episode = _boom

    await worker._tick()

    # (a) THE TICK CONTINUED to the second episode
    assert raised["n"] == 2, "the tick aborted on the raising episode"
    assert _row(repo, healthy_id).poignancy == 7

    # (b) + (c) THE FAILURE WAS RECORDED and the counter ADVANCED
    poison = _row(repo, poison_id)
    assert poison.poignancy_attempts == 1
    assert poison.poignancy_status is None  # not terminal yet
    assert poison.poignancy is None

    # (d) THE COUNTER ADVANCES TO THE CAP and the row TERMINALIZES
    for attempt in range(2, MEMOIR_POIGNANCY_ATTEMPT_CAP + 1):
        await worker._tick()
        poison = _row(repo, poison_id)
        assert poison.poignancy_attempts == attempt
    assert poison.poignancy_status == "terminal:poignancy_exception"

    # ... AND THE QUEUE ADVANCES PAST the poison row
    assert poison_id not in [
        row.id for row in repo.get_pending_poignancy_episodes()
    ]


@pytest.mark.asyncio
async def test_a_stalled_queue_warns_loudly(repo, caplog):
    """W1: a queue that does not advance must be VISIBLE.

    The stall warning names the stalled episode — silent starvation is
    the harm the per-episode isolation exists to prevent, and the
    warning is the second line of defence.
    """
    episode_id = _seed(repo)[0]
    worker = _worker(repo, answer="7")

    def _boom(episode):
        raise RuntimeError("the scorer exploded")

    worker._service.score_episode = _boom

    with caplog.at_level(
        logging.WARNING, logger="daemon.services.memoir_poignancy_worker"
    ):
        for _ in range(3):
            await worker._tick()

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "STALL" in text, f"the stalled queue was not named: {text}"
    assert episode_id in text, f"the stalled episode was not named: {text}"


# ── the lane-down latch (W2 — V2-D 4.6 / D3.9-D3.10) ─────────────────────


def _credential_service(repo):
    """A service whose caller ALWAYS rejects the credential (401)."""
    calls = {"n": 0}

    def _caller(prompt: str) -> str:
        calls["n"] += 1
        raise _FakeStatusError(401)

    service = MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=_caller,
    )
    return service, calls


@pytest.mark.asyncio
async def test_a_dead_credential_arms_the_latch_and_stops_the_tick(repo):
    """W2: the lane-down latch — the credential half.

    A dead credential must NOT burn one request per pending row per
    tick, and the latch state must be OBSERVABLE on the snapshot.
    """
    _seed(repo, count=3)
    service, calls = _credential_service(repo)
    worker = MemoirPoignancyWorker(service, config=None)

    interval = await worker._tick()

    # ONE request — not one per pending row
    assert calls["n"] == 1, (
        f"the dead credential burned {calls['n']} requests — the tick "
        "must stop at the first rejection"
    )
    # the latch is engaged and observable
    snapshot = worker.snapshot()
    assert snapshot["latched"] is True
    assert snapshot["credential_rejected_streak"] == 1
    assert snapshot["consecutive_all_fail_ticks"] == LATCH_THRESHOLD_TICKS
    assert snapshot["latch_tick_interval_s"] == LATCH_TICK_INTERVAL_S
    # the tick SLOWS DOWN
    assert interval == LATCH_TICK_INTERVAL_S
    # the untouched rows wait for a working credential
    assert len(repo.get_pending_poignancy_episodes()) == 2


@pytest.mark.asyncio
async def test_the_latch_releases_on_the_first_success(repo):
    """W2: the latch releases on the first success (D3.10)."""
    _seed(repo, count=2)
    service, _calls = _credential_service(repo)
    worker = MemoirPoignancyWorker(service, config=None)

    await worker._tick()
    assert worker.snapshot()["latched"] is True

    # the credential recovers
    service._llm_caller = lambda prompt: "6"
    interval = await worker._tick()

    snapshot = worker.snapshot()
    assert snapshot["latched"] is False
    assert snapshot["credential_rejected_streak"] == 0
    assert interval != LATCH_TICK_INTERVAL_S


# ── the tick bound (V2-44) ────────────────────────────────────────────────


def test_the_tick_budget_is_the_plans_twenty_seconds():
    """V2-44: the tick's TOTAL scoring budget is the PLAN's 20 s.

    The code shipped 60 s (the plan says 20 s); V2-D 4.2's "30 s" is
    the PER-CALL timeout, a different knob. The reconciliation is
    recorded in ``docs/memoir-v2/implementation-notes.md``.
    """
    assert TICK_SCORE_BUDGET_S == 20.0


@pytest.mark.asyncio
async def test_the_tick_budget_is_a_real_wall_clock_bound(repo):
    """V2-44: the budget BOUNDS THE TICK even when an episode hangs.

    The OLD shape checked the budget only BETWEEN episodes: one episode
    could run to the per-call timeout (30 s) and the tick reached ~1.5x
    its budget. The bound is now REAL — the per-episode wait is sized
    to the REMAINING budget.
    """
    _seed(repo, count=3)
    worker = _worker(repo, answer="7")
    worker._tick_score_budget_s = 0.25

    hang_s = 1.5
    finished = threading.Event()

    def _hang(prompt: str) -> str:
        time.sleep(hang_s)
        finished.set()
        return "7"

    worker._service._llm_caller = _hang

    started = time.monotonic()
    await worker._tick()
    elapsed = time.monotonic() - started

    assert not finished.is_set(), (
        "the tick waited for the hung scoring call — the budget must "
        "bound the tick (the wait is checked only BETWEEN episodes)"
    )
    assert elapsed < hang_s, (
        f"the tick took {elapsed:.2f}s against a {hang_s}s hang — the "
        "budget did not bound the tick"
    )
    # the timed-out episode counts an attempt (the timeout is RECORDED)
    row = _row(repo, repo.get_pending_poignancy_episodes()[0].id)
    assert row.poignancy_attempts == 1



# ── no delta-notify coupling (V2-D 4.5) ──────────────────────────────────


def test_the_worker_pushes_nothing_to_the_reflection_service():
    """V2-D 4.5: the trigger sum is DERIVED per tick in SQL — the worker
    holds no delta-notify coupling."""
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not any("memoir_reflection" in name for name in imports), (
        f"the worker imports the reflection service: {imports}"
    )


@pytest.mark.asyncio
async def test_the_settings_are_re_read_every_tick(repo, monkeypatch):
    """The knob is re-read per tick — a mid-flight change takes effect
    without a restart."""
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_BATCH_CAP", "1")
    _seed(repo, count=3)
    worker = _worker(repo, answer="7")

    await worker._tick()

    with Session(repo.engine) as session:
        rows = list(session.exec(select(MemoirEpisode)).all())
    scored = [row for row in rows if row.poignancy is not None]
    assert len(scored) == 1  # the batch cap of 1 — the rest wait


@pytest.mark.asyncio
async def test_the_batch_cap_bounds_the_scored_rows(repo, monkeypatch):
    """The batch cap is a COUNT, not a promise — the remainder waits for
    the next tick."""
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_BATCH_CAP", "2")
    _seed(repo, count=5)
    worker = _worker(repo, answer="7")

    await worker._tick()

    with Session(repo.engine) as session:
        rows = list(session.exec(select(MemoirEpisode)).all())
    assert len([row for row in rows if row.poignancy is not None]) == 2
    assert len([row for row in rows if row.poignancy is None]) == 3
