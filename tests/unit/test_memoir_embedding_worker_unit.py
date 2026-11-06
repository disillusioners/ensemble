"""The memoir embedding worker — the unit suite (AC1/AC3/AC4/AC5/AC6).

Phase 3 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase3-plan.md``; pack
``test/packs/memoir_embedding_worker_unit_test.sh``).

What this suite pins:

* AC1 — the batch pipeline: N pending ⇒ at most ``batch_cap`` per tick,
  the four identity columns set together by ONE statement, ``embedded_at``
  ISO, ``embedding_error`` cleared, row-level independence (a failing row
  never aborts the rest of the batch).
* AC3 — the executor discipline: embeds run on a thread named
  ``memoir-embed`` (the prefix pin), embeds are SEQUENTIAL (at most one
  in flight), the module holds NO ``asyncio.to_thread`` call site, and
  the DEFAULT embed client carries the AM-28 request timeout.
* AC4 — the failure taxonomy: 401/403 terminal at once AND the latch
  engages that tick; other permanent 4xx terminal per row; transient
  statuses (429/408/5xx-family/connection) attempt-and-retry; a
  dimension mismatch is terminal with NOTHING stored; a write-back
  failure counts as an attempt; 5 attempts is terminal (the row is
  skipped and counted in M).
* AC5 — the tick line's exact bytes (``E`` = this tick's failures, ``M``
  = the TOTAL terminal rows) and the park/resume knob semantics.
* AC6 — the loop guard (a raising tick is logged, the next tick runs,
  ``CancelledError`` re-raises) and driver-agnostic SQL (no ``tsv``).

Everything runs on a throwaway SQLite database — the worker NEVER builds
an engine itself and never reads an ambient ``POSTGRES_*`` variable.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
import re
import threading
import time

import httpx
import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_embedding_worker import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_ATTEMPT_CAP,
    LATCH_THRESHOLD_TICKS,
    MEMOIR_RETRYABLE_STATUS_CODES,
    MemoirEmbeddingWorker,
    TERMINAL_ERROR_MARKER,
    TickSettings,
    _default_embed,
    _is_terminal_row,
    classify_embed_failure,
    latest_snapshot,
)
from daemon.llm_error_classifier import RETRYABLE_STATUS_CODES

WORKER_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "daemon"
    / "services"
    / "memoir_embedding_worker.py"
)

WORKER_LOGGER = "daemon.services.memoir_embedding_worker"


class _StatusError(Exception):
    """A stand-in for the SDK's ``APIStatusError`` carrying a status."""

    def __init__(self, status_code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.status_code = status_code


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/memoir-worker-test.db")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


def _ok_embedder(dimensions: int = 1536, name_sink: list | None = None):
    """A mock embedder: deterministic vector, optional thread capture."""

    def _embed(text, *, model, base_url, api_key, timeout_s):
        if name_sink is not None:
            name_sink.append(threading.current_thread().name)
        return [0.25] * dimensions

    return _embed


def _make_worker(repo, embedder, **kwargs) -> MemoirEmbeddingWorker:
    return MemoirEmbeddingWorker(repo, embedder=embedder, **kwargs)


# ── AC1: the batch pipeline ──────────────────────────────────────────────


async def test_batch_caps_at_the_configured_cap(repo):
    for index in range(12):
        repo.insert_episode(agent_id="ari", title=f"t{index}", body=f"body {index}")
    worker = _make_worker(repo, _ok_embedder())

    interval = await worker._tick()

    assert worker._count_pending() == 4
    assert worker.snapshot()["last_embedded"] == 8
    # The remaining rows go on the NEXT tick (boot catch-up is just the
    # queue's emptiness — no side table).
    await worker._tick()
    assert worker._count_pending() == 0


async def test_identity_columns_set_together_and_error_cleared(repo):
    episode_id = repo.insert_episode(agent_id="ari", title="t", body="b")
    # A pre-existing failure must be CLEARED by the successful write-back.
    repo.record_embedding_attempt(episode_id, "earlier transient failure")
    worker = _make_worker(repo, _ok_embedder())
    await worker._tick()

    pending = repo.get_pending_embeddings()
    assert pending == []  # the queue predicate (embedding IS NULL) is empty
    row = repo.fetch_latest("ari", limit=1)[0]
    assert row.embedding is not None and len(row.embedding) == 1536
    assert row.embedding_model == DEFAULT_EMBEDDING_MODEL
    assert row.embedding_dims == 1536
    assert row.embedded_at is not None
    # ISO — the house stamp shape
    assert re.match(r"^\d{4}-\d{2}-\d{2}T", row.embedded_at)
    assert row.embedding_error is None


async def test_row_level_independence(repo):
    first = repo.insert_episode(agent_id="ari", title="t1", body="b1")
    second = repo.insert_episode(agent_id="ari", title="t2", body="b2")
    third = repo.insert_episode(agent_id="ari", title="t3", body="b3")

    calls: list[str] = []

    def _embed(text, *, model, base_url, api_key, timeout_s):
        calls.append(text)
        if len(calls) == 2:
            raise _StatusError(500, "transient lane blip")
        return [0.5] * 1536

    worker = _make_worker(repo, _embed)
    await worker._tick()

    assert len(calls) == 3  # the batch was NOT aborted by row 2's failure
    # Row 2 failed transiently → it stays pending; the next tick retries it.
    assert [row.id for row in repo.get_pending_embeddings()] == [second]
    rows = {row.id: row for row in repo.fetch_latest("ari", limit=None)}
    assert rows[first].embedding is not None
    assert rows[third].embedding is not None
    assert rows[second].embedding is None
    assert rows[second].embedding_attempts == 1
    assert rows[second].embedding_error is not None


# ── AC3: the executor discipline ─────────────────────────────────────────


async def test_embeds_run_on_the_memoir_embed_thread_prefix(repo):
    names: list[str] = []
    repo.insert_episode(agent_id="ari", title="t", body="b")
    worker = _make_worker(repo, _ok_embedder(name_sink=names))
    await worker._tick()
    assert names, "the embed never ran"
    assert all(name.startswith("memoir-embed") for name in names)


def _code_only_source(path: pathlib.Path) -> str:
    """The module's CODE with docstrings stripped (ast round-trip).

    The static pins (``no to_thread``, ``no tsv``) must bite on CODE, not
    on the module's own prose — the docstrings legitimately discuss both
    (the forbidden thing is the call site / the SQL reference, not the
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
    source = _code_only_source(WORKER_PATH)
    assert "to_thread" not in source, (
        "AM-29: asyncio.to_thread is FORBIDDEN in the worker — a cancelled "
        "to_thread does not free a shared worker and the default pool is "
        "shared with LLM/compaction traffic"
    )


async def test_embeds_are_sequential(repo):
    for index in range(4):
        repo.insert_episode(agent_id="ari", title=f"t{index}", body=f"b{index}")

    in_flight = 0
    max_in_flight = 0

    def _embed(text, *, model, base_url, api_key, timeout_s):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.05)
        in_flight -= 1
        return [0.1] * 1536

    worker = _make_worker(repo, _embed)
    await worker._tick()
    assert max_in_flight == 1, (
        "D3.5: embeds are SEQUENTIAL — the batch cap is a count, not a "
        f"concurrency (observed {max_in_flight} in flight)"
    )


def test_the_default_embed_client_carries_the_request_timeout(monkeypatch):
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.embeddings = self

        def create(self, *, model, input):
            class _Data:
                embedding = [0.0] * 1536

            class _Response:
                data = [_Data()]

            return _Response()

    monkeypatch.setattr("daemon.services.memoir_embedding_worker.openai.OpenAI", _FakeClient)
    vector = _default_embed("hello", model="m", base_url="http://x", api_key="k")
    assert len(vector) == 1536
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    # AM-28: a 30 s per-call budget (NOT the SDK's 600 s default) with a
    # 5 s connect budget. ``Timeout(30.0, connect=5.0)`` sets the
    # read/write/pool trio to 30 and connect to 5.
    assert timeout.read == 30.0
    assert timeout.write == 30.0
    assert timeout.connect == 5.0


# ── AC4: the failure taxonomy ────────────────────────────────────────────


async def test_401_is_terminal_at_once_and_latches_that_tick(repo, caplog):
    episode_id = repo.insert_episode(agent_id="ari", title="t", body="b")

    def _embed(text, **kwargs):
        raise _StatusError(401, "invalid api key")

    worker = _make_worker(repo, _embed)
    with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
        await worker._tick()

    row = repo.fetch_latest("ari", limit=1)[0]
    # Terminal AT ONCE: ONE attempt recorded, nothing stored, no 5-burn.
    assert row.embedding_attempts == 1
    assert row.embedding is None
    assert row.embedding_error.startswith(TERMINAL_ERROR_MARKER)
    assert "embedding_auth_rejected" in row.embedding_error
    # The latch engages on the FIRST occurrence (D3.9).
    assert worker._latched is True
    tick_lines = [r.message for r in caplog.records if "[MemoirEmbed] pending=" in r.message]
    assert tick_lines and "slow_mode=True" in tick_lines[-1]

    # The next tick SKIPS the terminal row — the dead credential is not
    # hammered.
    calls: list[str] = []

    def _quiet(text, **kwargs):
        calls.append(text)
        return [0.1] * 1536

    worker._embedder = _quiet
    await worker._tick()
    assert calls == []


async def test_other_permanent_4xx_is_terminal_per_row(repo):
    episode_id = repo.insert_episode(agent_id="ari", title="t", body="b")

    def _embed(text, **kwargs):
        raise _StatusError(404, "model not found")

    worker = _make_worker(repo, _embed)
    await worker._tick()

    row = repo.fetch_latest("ari", limit=1)[0]
    assert row.embedding_attempts == 1
    assert row.embedding is None
    assert row.embedding_error.startswith(TERMINAL_ERROR_MARKER)
    # A 404 is a per-row terminal, NOT the dead-credential latch arm.
    assert worker._latched is False
    assert worker._all_fail_streak == 1


@pytest.mark.parametrize("status", sorted(MEMOIR_RETRYABLE_STATUS_CODES))
async def test_transient_statuses_attempt_and_retry(repo, status):
    episode_id = repo.insert_episode(agent_id="ari", title="t", body="b")

    def _embed(text, **kwargs):
        raise _StatusError(status, "transient")

    worker = _make_worker(repo, _embed)
    await worker._tick()
    row = repo.fetch_latest("ari", limit=1)[0]
    assert row.embedding_attempts == 1
    # NOT terminal — the row is retried on a later tick.
    assert not row.embedding_error.startswith(TERMINAL_ERROR_MARKER)
    assert worker._count_pending() == 1


async def test_connection_errors_are_transient(repo):
    episode_id = repo.insert_episode(agent_id="ari", title="t", body="b")

    def _embed(text, **kwargs):
        raise httpx.ConnectError("connection refused")

    worker = _make_worker(repo, _embed)
    await worker._tick()
    row = repo.fetch_latest("ari", limit=1)[0]
    assert row.embedding_attempts == 1
    assert not row.embedding_error.startswith(TERMINAL_ERROR_MARKER)


async def test_dims_mismatch_is_terminal_and_stores_nothing(repo):
    episode_id = repo.insert_episode(agent_id="ari", title="t", body="b")

    def _embed(text, **kwargs):
        return [0.1] * 3072  # the provider drifted to a wider model

    worker = _make_worker(repo, _embed)
    await worker._tick()

    row = repo.fetch_latest("ari", limit=1)[0]
    assert row.embedding is None
    assert row.embedding_model is None
    assert row.embedding_dims is None
    assert row.embedded_at is None
    assert row.embedding_error.startswith(TERMINAL_ERROR_MARKER)
    assert "embedding_dimensions_mismatch" in row.embedding_error
    assert "expected=1536" in row.embedding_error
    assert "got=3072" in row.embedding_error
    assert worker._count_terminal() == 1


async def test_write_back_failure_counts_as_an_attempt(repo, monkeypatch):
    episode_id = repo.insert_episode(agent_id="ari", title="t", body="b")

    def _explode(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repo, "store_embedding", _explode)
    worker = _make_worker(repo, _ok_embedder())
    await worker._tick()

    row = repo.fetch_latest("ari", limit=1)[0]
    # AM-30: the failed write-back IS an attempt — without it the row
    # would re-embed forever (embedding IS NULL + attempts=0).
    assert row.embedding_attempts == 1
    assert row.embedding is None
    assert "embedding_write_back_failed" in row.embedding_error


async def test_five_attempts_are_terminal_and_skipped(repo):
    episode_id = repo.insert_episode(agent_id="ari", title="t", body="b")
    # Drive the row to the cap minus one with real recorded attempts.
    for _ in range(EMBEDDING_ATTEMPT_CAP - 1):
        repo.record_embedding_attempt(episode_id, "transient")

    def _embed(text, **kwargs):
        raise _StatusError(503, "lane down")

    worker = _make_worker(repo, _embed)
    await worker._tick()  # the final attempt burns here
    row = repo.fetch_latest("ari", limit=1)[0]
    assert row.embedding_attempts == EMBEDDING_ATTEMPT_CAP
    assert worker._count_terminal() == 1

    calls: list[str] = []

    def _quiet(text, **kwargs):
        calls.append(text)
        return [0.1] * 1536

    worker._embedder = _quiet
    await worker._tick()
    assert calls == [], "a terminal row must never be re-embedded"
    assert worker.snapshot()["last_terminal"] == 1


# ── W-2: the terminal-aware window + the stall warning ──────────────────


async def test_pending_rows_behind_a_head_of_terminal_rows_are_still_found(repo):
    """W-2 (council): the oldest-N window stalled PERMANENTLY behind
    terminal rows — their embedding stays NULL forever, so they matched
    the pending query forever while the worker's Python filter emptied
    ``candidates`` every tick: pending>0, embedded=0, forever, silently.
    The terminal-aware scan advances the window; the pinned shape is the
    incident itself: >=200 terminal rows at the head, a fresh pending
    row behind them."""
    for index in range(200):
        burned = repo.insert_episode(
            agent_id="ari", title=f"old{index}", body=f"old body {index}"
        )
        for _ in range(EMBEDDING_ATTEMPT_CAP):
            repo.record_embedding_attempt(burned, "burned")
    survivor = repo.insert_episode(
        agent_id="ari", title="fresh", body="the fresh body"
    )

    # The repro shape depends on the window: the default scan limit is
    # exactly 200, so the terminal head FILLS the whole window and the
    # survivor sits behind it (the pre-W-2 shape starved forever here).
    assert worker_scan_limit_covers() == 200

    worker = _make_worker(repo, _ok_embedder())
    await worker._tick()

    row = repo.fetch_latest("ari", limit=1)[0]
    assert row.id == survivor
    assert row.embedding is not None, (
        "a pending row behind a head of terminal rows must still be "
        "found and embedded (W-2) — the window must advance"
    )
    assert worker.snapshot()["last_embedded"] >= 1


def worker_scan_limit_covers() -> int:
    """The worker's default scan limit (the window the incident
    clogged). Exposed for the pin's readability only."""
    from daemon.services.memoir_embedding_worker import SCAN_LIMIT

    return int(SCAN_LIMIT)


# ── N-1: the Python belt (the tick's second line of defence) ────────────


async def test_the_python_belt_skips_terminal_rows_the_sql_did_not_exclude(repo):
    """N-1 (council delta-2): the tick's Python filter (``_is_terminal_row``,
    the thin delegate to ``memoir_row_is_terminal``) is the BELT behind the
    SQL-side terminal exclusion (W-2). This pin calls
    ``get_pending_embeddings(exclude_terminal=False)`` — the repository flag
    that DISABLES the SQL-side exclusion (the diagnostic read) — and asserts
    the belt ALONE still refuses the terminal rows: a SQL scan that returns
    BOTH terminal forms (attempts at/over the cap AND the ``terminal:``
    marker) must still yield only the pending row as tick candidates."""
    burned = repo.insert_episode(agent_id="ari", title="burned", body="b")
    for _ in range(EMBEDDING_ATTEMPT_CAP):
        repo.record_embedding_attempt(burned, "burned")
    marked = repo.insert_episode(agent_id="ari", title="marked", body="b")
    repo.record_embedding_attempt(marked, f"{TERMINAL_ERROR_MARKER}gave up")
    fresh = repo.insert_episode(agent_id="ari", title="fresh", body="b")

    # The SQL exclusion is OFF: the raw emptiness-is-the-queue read must
    # return ALL THREE rows — otherwise this pin proves nothing.
    raw = repo.get_pending_embeddings(exclude_terminal=False)
    raw_ids = {row.id for row in raw}
    assert raw_ids == {burned, marked, fresh}, (
        "with exclude_terminal=False the SQL must return all three rows "
        "(both terminal forms plus the pending one)"
    )

    # Control leg: the SQL exclusion ON returns only the pending row —
    # proving the flag is what switched the SQL-side behaviour.
    excluded_ids = {row.id for row in repo.get_pending_embeddings()}
    assert excluded_ids == {fresh}, (
        "the default scan (exclude_terminal=True) must exclude both "
        "terminal rows SQL-side"
    )

    # The BELT — the tick's own filter, applied to the raw scan exactly
    # as the tick composes its candidates:
    candidates = [
        row
        for row in raw
        if not _is_terminal_row(row, EMBEDDING_ATTEMPT_CAP)
    ]
    assert [row.id for row in candidates] == [fresh], (
        "the worker-side Python belt must skip both terminal rows the "
        "SQL did not exclude"
    )
    for terminal_id in (burned, marked):
        row = next(r for r in raw if r.id == terminal_id)
        assert _is_terminal_row(row, EMBEDDING_ATTEMPT_CAP), (
            f"row {terminal_id} must classify terminal (the belt's skip)"
        )


async def test_a_starved_window_warns_after_n_stalled_ticks(repo, caplog):
    """W-2: ``pending>0`` with ``embedded==0`` for N consecutive ticks
    fires a LOUD WARNING naming the pending count, the scan window and
    the terminal-row count — silent-forever is unacceptable. The
    warning fires AT the Nth stalled tick (not before), does NOT repeat
    every stalled tick after that, and a healthy tick clears the stall
    (no warning, re-armed)."""
    from daemon.services.memoir_embedding_worker import (
        MEMOIR_EMBED_STALL_WARN_TICKS,
    )

    repo.insert_episode(agent_id="ari", title="s", body="stalled body")

    def _transient(text, **kwargs):
        # 503 is transient: the attempt is recorded, the row stays
        # NON-terminal and pending — exactly a starved-but-alive queue.
        raise _StatusError(503, "lane down")

    worker = _make_worker(repo, _transient)
    with caplog.at_level(logging.WARNING, logger=WORKER_LOGGER):
        for _ in range(MEMOIR_EMBED_STALL_WARN_TICKS - 1):
            await worker._tick()
        early = [r for r in caplog.records if "STALL" in r.message]
        assert early == [], "no stall warning before the threshold"

        await worker._tick()  # the threshold tick
        warnings = [r for r in caplog.records if "STALL" in r.message]
        assert len(warnings) == 1, "the stall WARNING fires AT the threshold"
        message = warnings[0].message
        assert "pending=1" in message
        assert f"limit={worker._scan_limit}" in message
        assert "terminal=0" in message

        # A stalled tick AFTER the warning does not repeat it (one loud
        # line per stall; the operator alerting owns repetition).
        await worker._tick()
        assert [r for r in caplog.records if "STALL" in r.message] == warnings

        # THE HEALTHY TICK — a successful embed clears the stall and
        # fires nothing.
        worker._embedder = _ok_embedder()
        repo.insert_episode(agent_id="ari", title="ok", body="fine now")
        caplog.clear()
        await worker._tick()
        assert [r for r in caplog.records if "STALL" in r.message] == []


# ── AC5: the tick line + the park semantics ──────────────────────────────


async def test_tick_line_bytes_are_exact(repo, caplog):
    for index in range(3):
        repo.insert_episode(agent_id="ari", title=f"t{index}", body=f"b{index}")
    worker = _make_worker(repo, _ok_embedder())
    with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
        await worker._tick()

    lines = [r.message for r in caplog.records if r.message.startswith("[MemoirEmbed] pending=")]
    assert lines, "the tick line is the operator's primary surface"
    pattern = (
        r"^\[MemoirEmbed\] pending=(\d+) embedded=(\d+) errors=(\d+) "
        r"terminal=(\d+) tick_ms=(\d+) slow_mode=(True|False)$"
    )
    match = re.match(pattern, lines[-1])
    assert match, f"tick-line bytes drifted: {lines[-1]!r}"
    pending, embedded, errors, terminal, tick_ms, slow_mode = match.groups()
    assert (pending, embedded, errors, terminal) == ("3", "3", "0", "0")
    assert slow_mode == "False"
    assert int(tick_ms) >= 0


async def test_tick_line_m_is_the_total_terminal_not_this_tick(repo, caplog):
    # Two rows already terminal (burned budget), one NEW row that fails
    # permanently this tick.
    for _ in range(2):
        burned = repo.insert_episode(agent_id="ari", title="old", body="old")
        for _ in range(EMBEDDING_ATTEMPT_CAP):
            repo.record_embedding_attempt(burned, "burned")
    fresh = repo.insert_episode(agent_id="ari", title="new", body="new")

    def _embed(text, **kwargs):
        raise _StatusError(401, "invalid api key")

    worker = _make_worker(repo, _embed)
    with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
        await worker._tick()

    lines = [r.message for r in caplog.records if r.message.startswith("[MemoirEmbed] pending=")]
    # The optional `` fail_classes=...`` suffix is the item-15 census
    # (present only when this tick recorded failures); the stable prefix
    # is unchanged.
    match = re.match(
        r"^\[MemoirEmbed\] pending=(\d+) embedded=(\d+) errors=(\d+) "
        r"terminal=(\d+) tick_ms=(\d+) slow_mode=(True|False)"
        r"(?: fail_classes=[a-z_0-9:,]+)?$",
        lines[-1],
    )
    pending, embedded, errors, terminal, tick_ms, slow_mode = match.groups()
    # E = rows that failed THIS tick (1); M = the TOTAL terminal rows in
    # the table (the two burned + this tick's 401).
    assert errors == "1"
    assert terminal == "3"
    assert slow_mode == "True"


async def test_knob_off_parks_without_work_and_resumes_on_on(repo, caplog, monkeypatch):
    repo.insert_episode(agent_id="ari", title="t", body="b")
    monkeypatch.setenv("ENSEMBLE_MEMOIR_ENABLED", "false")
    calls: list[str] = []

    def _embed(text, **kwargs):
        calls.append(text)
        return [0.1] * 1536

    worker = _make_worker(repo, _embed)
    with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
        interval = await worker._tick()
        assert calls == [], "a parked worker does no embedding work"
        assert worker._parked is True
        assert worker.snapshot()["phase"] == "parked"
        park_lines = [r.message for r in caplog.records if "parked" in r.message]
        assert park_lines, "the park transition emits its own line"

        # The task is alive and the interval is the configured tick.
        assert interval == 60

        # The knob returns ON — no restart, the next tick resumes.
        monkeypatch.setenv("ENSEMBLE_MEMOIR_ENABLED", "true")
        interval = await worker._tick()
        assert calls == ["t\nb"], "the parked worker resumed on ON"
        assert worker._parked is False
        assert worker.snapshot()["phase"] == "running"


async def test_numeric_knobs_are_re_read_every_tick(repo, monkeypatch):
    worker = _make_worker(repo, _ok_embedder())
    settings = worker._resolve_settings()
    assert settings.tick_interval_s == 60  # boot tier
    assert settings.batch_cap == 8
    assert settings.dimensions == 1536

    # The operator slows the worker during an incident — no restart.
    monkeypatch.setenv("ENSEMBLE_MEMOIR_EMBEDDING_TICK_S", "300")
    settings = worker._resolve_settings()
    assert settings.tick_interval_s == 300

    # A garbage value warns and keeps the boot tier (never a crash).
    monkeypatch.setenv("ENSEMBLE_MEMOIR_EMBEDDING_BATCH_CAP", "not-a-number")
    settings = worker._resolve_settings()
    assert settings.batch_cap == 8

    monkeypatch.setenv("ENSEMBLE_MEMOIR_EMBEDDING_DIMENSIONS", "3072")
    settings = worker._resolve_settings()
    assert settings.dimensions == 3072


async def test_latch_slows_the_tick_interval(repo):
    repo.insert_episode(agent_id="ari", title="t", body="b")

    def _embed(text, **kwargs):
        raise _StatusError(503, "lane down")

    worker = _make_worker(repo, _embed)
    first = await worker._tick()
    second = await worker._tick()
    assert first == 60 and second == 60  # below the threshold: normal cadence
    assert worker._latched is False
    third = await worker._tick()
    assert worker._latched is True
    assert third == 300  # the latch interval (D3.10)
    # A success releases the latch back to the configured interval.
    worker._embedder = _ok_embedder()
    fourth = await worker._tick()
    assert worker._latched is False
    assert fourth == 60


# ── AC6: the loop guard + driver-agnostic SQL ────────────────────────────


async def test_a_raising_tick_is_logged_and_the_next_tick_runs(repo, caplog, monkeypatch):
    repo.insert_episode(agent_id="ari", title="t", body="b")
    worker = _make_worker(repo, _ok_embedder(), tick_embed_budget_s=45)
    # D3.12: the operator can shorten the cadence via env — the test
    # uses it so the SECOND tick actually runs inside the test window.
    monkeypatch.setenv("ENSEMBLE_MEMOIR_EMBEDDING_TICK_S", "1")

    original_scan = worker._repository.get_pending_embeddings
    state = {"failed_once": False}

    def _scan_once_flaky(*args, **kwargs):
        if not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("simulated scan failure")
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(worker._repository, "get_pending_embeddings", _scan_once_flaky)

    with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
        task = asyncio.get_running_loop().create_task(worker.run())
        # The FIRST tick fails; poll until the NEXT tick has run.
        for _ in range(100):
            await asyncio.sleep(0.05)
            if worker.snapshot()["last_embedded"] == 1:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # cancellation may land in the tick OR the sleep — both clean

    failed_lines = [r for r in caplog.records if "tick failed" in r.message]
    assert failed_lines, "a failed tick must be logged, never silent"
    assert worker.snapshot()["last_embedded"] == 1, (
        "the NEXT tick ran after the failed one (a bad tick never kills "
        "the task)"
    )


async def test_cancellation_inside_a_tick_propagates(repo):
    worker = _make_worker(repo, _ok_embedder())

    async def _cancel_from_within():
        raise asyncio.CancelledError

    worker._tick = _cancel_from_within  # type: ignore[method-assign]

    task = asyncio.get_running_loop().create_task(worker.run())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


def test_the_worker_sql_has_no_tsv_reference():
    source = _code_only_source(WORKER_PATH)
    assert "tsv" not in source, (
        "D3.14/AM-36: the worker SQL is driver-agnostic — no tsv reference"
    )


def test_the_lane_local_transient_set_widens_the_house_set():
    # AM-32: the memoir set is the house set PLUS 408; the house
    # constant itself is untouched.
    assert MEMOIR_RETRYABLE_STATUS_CODES == frozenset(RETRYABLE_STATUS_CODES) | {408}
    assert 408 not in RETRYABLE_STATUS_CODES


def test_classification_contract():
    token, terminal = classify_embed_failure(_StatusError(401))
    assert (token, terminal) == ("embedding_auth_rejected", True)
    token, terminal = classify_embed_failure(_StatusError(403))
    assert (token, terminal) == ("embedding_auth_rejected", True)
    token, terminal = classify_embed_failure(_StatusError(429))
    assert (token, terminal) == ("embedding_status_transient", False)
    token, terminal = classify_embed_failure(_StatusError(408))
    # THE WIDENING: 408 is transient lane-locally even though the house
    # helper alone would call it permanent.
    assert (token, terminal) == ("embedding_status_transient", False)
    token, terminal = classify_embed_failure(_StatusError(500))
    assert (token, terminal) == ("embedding_status_transient", False)
    token, terminal = classify_embed_failure(_StatusError(404))
    assert (token, terminal) == ("embedding_status_permanent", True)
    token, terminal = classify_embed_failure(httpx.ConnectError("x"))
    assert (token, terminal) == ("embedding_connection_error", False)


def test_the_worker_publishes_a_not_started_block_before_any_worker():
    # The module snapshot is honest before the lifespan runs (probe apps).
    snap = latest_snapshot()
    assert snap["phase"] in {"not_started", "running", "parked"}


# ── Tidier item 15 (B4): distinguishable failure classes in the tick ─────


async def test_the_tick_line_keeps_distinct_failure_classes(repo, caplog):
    """A dims mismatch must NOT read like a credential rejection.

    Both are terminal failures, but they call for opposite operator
    actions (fix the model identity vs re-mint the key). The tick line
    carries the per-class census and the token is the CLASS, never the
    bare ``terminal`` marker word.
    """
    repo.insert_episode(agent_id="ari", title="t", body="b")

    def _wrong_dims(text, **kwargs):
        return [0.0] * 3  # != the default 1536

    worker = _make_worker(repo, _wrong_dims)
    with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
        await worker._tick()

    tick_lines = [
        r.message for r in caplog.records if "[MemoirEmbed] pending=" in r.message
    ]
    assert tick_lines, "the tick line must be emitted"
    line = tick_lines[-1]
    assert "fail_classes=embedding_dimensions_mismatch:1" in line, line
    assert "terminal:" not in line


async def test_record_failure_returns_the_class_token_not_the_marker(repo):
    """The returned token drives the census — it must be the CLASS."""
    repo.insert_episode(agent_id="ari", title="t", body="b")
    row = repo.fetch_latest("ari", limit=1)[0]
    worker = _make_worker(repo, _ok_embedder())
    token = await worker._record_failure(
        row,
        "terminal:embedding_dimensions_mismatch expected=1536 got=3",
        immediate_terminal=True,
        already_marked=True,
    )
    assert token == "embedding_dimensions_mismatch", token
    # And the PERSISTED error keeps the terminal marker + true class.
    stored = repo.fetch_latest("ari", limit=1)[0]
    assert stored.embedding_error.startswith(TERMINAL_ERROR_MARKER)
    assert "embedding_dimensions_mismatch" in stored.embedding_error
