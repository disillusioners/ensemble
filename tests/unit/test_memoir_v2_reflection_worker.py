"""Memoir Store v2 — the REFLECTION WORKER (the SQLite lane).

Phase 2 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase2-plan.md`` AC3;
pack ``test/packs/memoir_v2_reflection_worker_test.sh``).

What this suite pins:

* **THE NIGHTLY-BEFORE-IN-DAY ORDER** (AC7(a) / V2-24 / Register #19) —
  the nightly gate is evaluated FIRST, and the order is OBSERVABLE
  through :attr:`MemoirReflectionWorker.pass_order`. This is the leg
  the order-reversal mutation flips RED;
* the nightly gate's quiet-hours + date-watermark conditions;
* the 15-minute CLAIM STALE TAKEOVER (SF5 / V2-35) through the worker;
* the DEDICATED THREAD — the lane runs on ``memoir-reflect``, never on
  the event loop and never on the shared default executor;
* the KILL-SWITCH park/resume — the worker parks within one tick and
  resumes when the knob returns;
* the per-tick EXCEPTION ISOLATION — one failed tick does not stop the
  loop;
* the shutdown cancels and awaits the worker task.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import os
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories.memoir.reflection_repository import (
    MEMOIR_REFLECTION_CLAIM_STALE_SECONDS,
    MemoirReflectionRepository,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.repositories.memoir.syntheses_repository import (
    MemoirSynthesesRepository,
)
from daemon.services.memoir_reflection_service import (
    MemoirReflectionService,
    ReflectionSettings,
)
from daemon.services.memoir_reflection_worker import (
    MemoirReflectionWorker,
    executor_thread_name,
    executor_thread_prefix,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

AGENT = "ari"

WORKER_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "daemon"
    / "services"
    / "memoir_reflection_worker.py"
)


@pytest.fixture()
def engine(tmp_path):
    # A FILE-backed SQLite database (the house pattern for the worker
    # lane — ``test_memoir_embedding_worker_unit.py``): the reflection
    # pass runs on the DEDICATED executor thread, and an in-memory
    # SQLite database is per-THREAD (a second thread gets a second,
    # EMPTY database). A file is shared by every connection.
    eng = create_engine(f"sqlite:///{tmp_path}/memoir-reflection-worker.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def reflection_repo(engine):
    return MemoirReflectionRepository(engine)


@pytest.fixture()
def syntheses_repo(engine):
    return MemoirSynthesesRepository(engine)


def _service(reflection_repo, syntheses_repo, **kwargs):
    settings = {"agent_id": AGENT}
    settings.update(kwargs)
    return MemoirReflectionService(
        reflection_repository=reflection_repo,
        syntheses_repository=syntheses_repo,
        settings=ReflectionSettings(**settings),
        llm_caller=lambda prompt: '{"episodes": [], "syntheses": []}',
    )


def _worker(reflection_repo, syntheses_repo, *, clock=None, **kwargs):
    service = _service(reflection_repo, syntheses_repo, **kwargs)
    return MemoirReflectionWorker(service, config=None, clock=clock)


def _seed(repo, count=3, importance=9, created_at=None):
    return [
        repo.insert_episode(
            agent_id=AGENT,
            body=f"memory {index}",
            importance=importance,
            created_at=created_at,
        )
        for index in range(count)
    ]


# ── AC7(a): the nightly-before-in-day order ──────────────────────────────


@pytest.mark.asyncio
async def test_the_nightly_gate_runs_before_the_in_day_branch(
    repo, reflection_repo, syntheses_repo
):
    """BOTH branches are due — the nightly pass wins, and it is FIRST.

    This is the AC7(a) mutation leg: reversing the order in the tick
    body makes ``pass_order`` ``["in_day", "nightly"]`` and this
    assertion flips RED.
    """
    base = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    # The episodes are OLD ENOUGH to be idle (the in-day branch is due)
    # and they are inside the quiet hours (the nightly branch is due).
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    await worker._tick()
    assert worker.pass_order == ["nightly"]


@pytest.mark.asyncio
async def test_the_in_day_branch_runs_outside_the_quiet_hours(
    repo, reflection_repo, syntheses_repo
):
    base = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    await worker._tick()
    assert worker.pass_order == ["in_day"]


@pytest.mark.asyncio
async def test_no_pass_runs_when_neither_branch_is_due(
    repo, reflection_repo, syntheses_repo
):
    base = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    await worker._tick()
    assert worker.pass_order == []


# ── the quiet hours + the date watermark ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_restart_at_0230_runs_the_nightly_pass_on_the_first_tick(
    repo, reflection_repo, syntheses_repo
):
    """AC3: a restart inside the quiet hours runs nightly immediately."""
    base = datetime(2026, 11, 9, 2, 30, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    await worker._tick()
    assert worker.pass_order == ["nightly"]
    assert reflection_repo.last_nightly_date() == "2026-11-09"


@pytest.mark.asyncio
async def test_the_nightly_pass_does_not_run_twice_on_the_same_day(
    repo, reflection_repo, syntheses_repo
):
    base = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    await worker._tick()
    assert worker.pass_order == ["nightly"]
    await worker._tick()
    # The second tick finds the date watermark and falls through to the
    # in-day branch — which is idle (the episodes were reflected).
    assert worker.pass_order == []


# ── the claim stale takeover (SF5 / V2-35) ───────────────────────────────


@pytest.mark.asyncio
async def test_a_stale_claim_is_taken_over_by_the_next_pass(
    repo, reflection_repo, syntheses_repo
):
    """A crashed pass leaves its claim behind — the next pass takes it."""
    base = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    # A crashed claimant: the claim is in the past.
    stale = base - timedelta(seconds=MEMOIR_REFLECTION_CLAIM_STALE_SECONDS + 60)
    assert reflection_repo.claim(now=stale) is True

    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    await worker._tick()
    assert worker.pass_order == ["nightly"]


@pytest.mark.asyncio
async def test_a_fresh_claim_blocks_the_pass(
    repo, reflection_repo, syntheses_repo
):
    """A live claim is never stolen."""
    base = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    assert reflection_repo.claim(now=base) is True

    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    await worker._tick()
    # The nightly gate is OPEN (the branch was evaluated), but the claim
    # is held by somebody else — so the pass did NOT run.
    assert worker.pass_order == ["nightly"]
    assert worker.last_outcome is not None
    assert worker.last_outcome.ran is False
    assert worker.last_outcome.reason == "claim_held"


# ── the dedicated thread ─────────────────────────────────────────────────


def test_the_lane_runs_on_the_dedicated_reflect_thread():
    """The executor thread carries the ``memoir-reflect`` prefix (D3.5)."""
    assert executor_thread_name().startswith(executor_thread_prefix())
    assert executor_thread_prefix() == "memoir-reflect"


@pytest.mark.asyncio
async def test_the_pass_runs_on_the_reflect_thread(
    repo, reflection_repo, syntheses_repo
):
    """The blocking work runs on ``memoir-reflect``, NOT the event loop."""
    import threading

    base = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    seen: list[str] = []

    class _Spy:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def run_nightly_pass(self, now=None):
            seen.append(threading.current_thread().name)
            return self._inner.run_nightly_pass(now)

    service = _service(reflection_repo, syntheses_repo)
    worker = MemoirReflectionWorker(_Spy(service), config=None, clock=lambda: base)
    await worker._tick()
    assert seen and all(name.startswith("memoir-reflect") for name in seen)


# ── the kill-switch park / resume ────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_worker_parks_within_one_tick_when_the_knob_is_off(
    repo, reflection_repo, syntheses_repo, monkeypatch
):
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "false")
    base = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    await worker._tick()
    assert worker.pass_order == []
    assert worker.snapshot()["parked"] is True
    assert worker.snapshot()["phase"] == "parked"


@pytest.mark.asyncio
async def test_the_worker_resumes_when_the_knob_returns(
    repo, reflection_repo, syntheses_repo, monkeypatch
):
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "false")
    base = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    await worker._tick()
    assert worker.snapshot()["parked"] is True
    # The knob returns ON — the NEXT tick resumes (a call-time read).
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "true")
    await worker._tick()
    assert worker.snapshot()["parked"] is False
    assert worker.pass_order == ["nightly"]


def test_the_worker_reads_the_knob_per_tick(
    reflection_repo, syntheses_repo, monkeypatch
):
    """The knob is re-read EVERY tick (D3.12) — never cached."""
    from daemon.services import memoir_reflection_worker as worker_module

    reads: list[int] = []
    original = worker_module.resolve_reflection_settings

    def _spy(config=None):
        reads.append(1)
        return original(config)

    monkeypatch.setattr(worker_module, "resolve_reflection_settings", _spy)
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "false")
    worker = _worker(reflection_repo, syntheses_repo)
    worker._resolve_settings()
    worker._resolve_settings()
    assert len(reads) == 2


# ── the exception isolation + the shutdown ──────────────────────────────


@pytest.mark.asyncio
async def test_one_failed_tick_does_not_stop_the_loop(
    repo, reflection_repo, syntheses_repo, monkeypatch
):
    """The per-tick EXCEPTION ISOLATION: a bad tick is logged, the next
    tick still runs (the house contract)."""
    base = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base - timedelta(hours=2))
    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    calls = {"n": 0}
    original = worker._tick

    async def _boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the tick exploded")
        return 1

    monkeypatch.setattr(worker, "_tick", _boom)
    # A ONE-SECOND tick interval so the second tick lands inside the test.
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_TICK_INTERVAL_S", "1")
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(1.3)
    task.cancel()
    await task
    assert calls["n"] >= 2, (
        "the loop must keep ticking after a failed tick"
    )


@pytest.mark.asyncio
async def test_the_shutdown_cancels_and_awaits_the_worker_task(
    repo, reflection_repo, syntheses_repo
):
    """AC3: the shutdown cancels and awaits the worker task."""
    base = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    worker = _worker(
        reflection_repo,
        syntheses_repo,
        clock=lambda: base,
    )
    task = asyncio.create_task(worker.run(), name="memoir-reflection-worker")
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.done(), "the shutdown must cancel AND await the worker task"


def test_the_knob_off_env_pauses_the_lane(monkeypatch):
    """The env knob reaches the worker (the per-tick re-read)."""
    from daemon.services.memoir_reflection_service import (
        resolve_reflection_settings,
    )

    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "false")
    assert resolve_reflection_settings(None).enabled is False
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "true")
    assert resolve_reflection_settings(None).enabled is True
    monkeypatch.delenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", raising=False)
    assert resolve_reflection_settings(None).enabled is True


# ── the static pins (the house shape) ────────────────────────────────────


def _code_only_source(path: pathlib.Path) -> str:
    """The module's CODE with docstrings stripped (an ast round-trip).

    The static pin (``no to_thread``) must bite on CODE, not on the
    module's own prose — the docstring legitimately DISCUSSES the
    forbidden thing (the forbidden thing is the CALL SITE, not the
    English word).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            node.body = _drop_docstring(node.body)
    return ast.unparse(tree)


def _drop_docstring(body):
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def test_the_worker_module_holds_no_to_thread_call_site():
    """D3.5: ``asyncio.to_thread`` is FORBIDDEN in a worker module.

    A cancelled ``to_thread`` does not free a shared worker, and the
    DEFAULT pool is shared with the LLM/compaction traffic. The lane
    runs on its OWN executor (``memoir-reflect``).
    """
    source = _code_only_source(WORKER_PATH)
    assert "to_thread" not in source, (
        "D3.5: asyncio.to_thread is FORBIDDEN in the worker — the lane "
        "must use its dedicated single-thread executor"
    )


def test_the_worker_uses_the_dedicated_executor():
    """The lane's blocking work goes through ``run_in_executor``."""
    source = _code_only_source(WORKER_PATH)
    assert "run_in_executor" in source
    assert "memoir-reflect" in source
