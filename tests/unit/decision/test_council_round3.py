"""Council round 3 — the final-fix pins.

One blocker + five fold-now advisories + the leader's (b) ruling, each
with the pin that would RED if the defect returned:

(c)  the shadow consult is DETACHED from the search path (a bounded
     background task with a strong reference, never-apply, contained
     TYPE-ONLY, kill/cost/trip inside the task);
(a)  the global-master poison is NOT inert (unreadable ⇒ global OFF);
(e)  a connect-phase fault NEVER opened a socket (no D3 floor);
(f)  the per-key raw-leaf accessor + the fail-soft settings read;
(g1) the pre-flight sits INSIDE a never-raises guard;
(g2) a NaN/garbage timeout budget is clamped, never a raise;
(A1) the invalidation narrowing — 0 for unrelated keys, exactly 1 for
     decision keys;
(b)  the empty-heuristic paths neither consult nor dispatch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from daemon.manager import InstanceManager
from daemon.models.app_settings import AppSetting
from daemon.services.decision import metrics
from daemon.services.decision.client import DecisionClient
from daemon.services.decision.config import (
    GLOBAL_ENABLED_KEY,
    DecisionConfig as CoreDecisionConfig,
)
from daemon.services.decision.cost_store import build_s1_seam_record
from daemon.services.decision.kill import (
    InMemoryKillStore,
    KillChannelResolver,
    seam_enabled_key,
    seam_stage_key,
)
from daemon.services.decision.overlay import DecisionOverlay
from daemon.services.decision.registry import (
    ReviewRecord,
    SeamRegistry,
)
from daemon.services.decision.shadow import ShadowWriter
from daemon.services.decision.types import (
    DecisionStage,
    FailureClass,
    OverlayOutcome,
)
from daemon.services.settings_service import (
    MANAGED_KEYS,
    ManagedKeySpec,
    SettingsService,
    _raw_yaml_leaf,
    resolve,
)
from daemon.services.snapshot_search_service import SnapshotSearchService
from tests.unit.decision.helpers import (
    RowCapture,
    choice_request,
    json_response,
    make_config,
    seam_record,
    smart_handler,
)

# ── small local fakes (the wiring tests' shapes, kept self-contained) ──


class _FakeSnapshot:
    def __init__(self, snapshot_id: str) -> None:
        self.id = snapshot_id
        self.project_id = "p1"
        self.title = snapshot_id
        self.task_summary = "find task in this snapshot"
        self.domain_tags = ["test"]
        self.status = "active"
        self.digest = {"task_summary_text": self.task_summary}
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.runtime_version = "0.0.0"
        self.supersedes_snapshot_id = None


class _FakeRepo:
    def __init__(self, rows: list[_FakeSnapshot]) -> None:
        self._rows = rows

    def list_active_by_project(self, project_id: str, limit: int = 50):
        return [r for r in self._rows if r.project_id == project_id][:limit]

    def filter_by_tags(self, project_id: str, tags: list[str], mode: str):
        return self._rows

    def get_embeddings(self, snapshot_id: str) -> list[Any]:
        return [MagicMock(embedding=[1.0, 0.0, 0.0])]


class _FakeEmb:
    async def embed_text(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        return 1.0


def _staleness_stub(_snap: Any) -> dict[str, Any]:
    return {"freshness": "fresh", "snapshot_age_days": 1.0}


def _on_seam() -> dict[str, str]:
    return {
        GLOBAL_ENABLED_KEY: "1",
        seam_enabled_key("snapshot.l3.selection"): "1",
    }


def _sqlite_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AppSetting.__table__.create(engine, checkfirst=True)
    return engine


# ── (c) the detached shadow consult ────────────────────────────────────


@pytest.mark.asyncio
async def test_c1_the_shadow_consult_is_detached_from_the_search_path() -> None:
    """(c) shape points 1+2 — the hook captures, dispatches and returns;
    ``search()`` does NOT wait for the consult, and the in-flight task
    is STRONGLY REFERENCED (never GC-eligible mid-flight)."""
    release = asyncio.Event()
    calls: list[httpx.Request] = []

    async def _parked(request: httpx.Request) -> httpx.Response:
        # The consult parks HERE once it reaches the socket phase —
        # long after search() has moved on.
        await release.wait()
        calls.append(request)
        return json_response(
            {
                "model": "decision",
                "answers": [
                    {
                        "type": "choice",
                        "name": "s1_candidate_selection",
                        "choice": "snap-2",
                        "probabilities": [
                            {"value": v, "probability": 0.5}
                            for v in ("snap-1", "snap-2", "snap-3")
                        ],
                        "confidence": 0.9,
                    }
                ],
            }
        )

    config = make_config()
    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(_parked))
    registry = SeamRegistry(client=client, config=config)
    registry.register(build_s1_seam_record())
    overlay = DecisionOverlay(
        registry=registry,
        kill=KillChannelResolver(_InMemStoreOn()),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
    )

    rows = [_FakeSnapshot("snap-1"), _FakeSnapshot("snap-2"), _FakeSnapshot("snap-3")]

    class _Chat:
        def create(self, *a, **kw):
            m = MagicMock()
            m.choices = [
                MagicMock(message=MagicMock(content='{"selected": ["snap-1", "snap-3"]}'))
            ]
            return m

    llm = MagicMock()
    llm.chat.completions = _Chat()

    service = SnapshotSearchService(
        snapshot_repo=_FakeRepo(rows),
        embedding_service=_FakeEmb(),  # type: ignore[arg-type]
        llm_config={"model": "test-chat"},
        staleness_fn=_staleness_stub,
        decision_overlay=overlay,
    )

    result = await service.search(
        query="find task", project_id="p1", llm_client=llm
    )

    # Point 1 — search() returned WITHOUT waiting for the consult:
    # the consult is parked at the socket, so no request has been
    # recorded yet, yet search() already produced its result.
    assert [r["snapshot_id"] for r in result["results"]] == ["snap-1", "snap-3"]
    assert calls == [], "search() must not wait for the shadow consult"

    # Point 2 — the in-flight task is STRONGLY REFERENCED.
    assert len(overlay._detached_tasks) == 1, (
        "a bare create_task is GC-eligible mid-flight; the strong-ref "
        "set must hold the task while it runs"
    )

    # Release the parked consult and drain it.
    release.set()
    drained = await overlay.wait_for_detached()
    assert drained == 1
    assert len(calls) == 1, "the consult completes inside the task"
    assert overlay._detached_tasks == set(), (
        "the strong reference is dropped in the payload's finally"
    )


@pytest.mark.asyncio
async def test_c1_a_detached_consult_never_applies_even_when_ratified_deciding() -> None:
    """(c) shape point 8 — the split is STRUCTURAL: the detached path
    forces ``force_shadow=True``, so even a RATIFIED DECIDING seam
    consulted through it cannot move the outcome, while the same seam
    through the INLINE consult applies."""
    config = make_config()

    def _handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "model": "decision",
                "answers": [
                    {
                        "type": "choice",
                        "name": "s1_candidate_selection",
                        "choice": "id-a",
                        "probabilities": [
                            {"value": v, "probability": 0.5}
                            for v in ("id-a", "id-b", "id-c")
                        ],
                        "confidence": 0.9,
                    }
                ],
            }
        )

    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(_handler))
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    registry.transition_stage(
        "snapshot.l3.selection",
        DecisionStage.DECIDING,
        review=ReviewRecord(reviewer="council", ratified_by="the Emperor"),
    )
    overlay = DecisionOverlay(
        registry=registry,
        kill=KillChannelResolver(_InMemStoreOn(stage="deciding")),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
    )

    inline = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert inline.applied is True, "the inline deciding path applies (P4 inherits it)"

    detached = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
        force_shadow=True,
    )
    assert detached.applied is False, "apply is NEVER detached"
    assert detached.state == OverlayOutcome.LOGGED.value
    assert detached.outcome == ["id-a"], "the heuristic stands under force_shadow"


@pytest.mark.asyncio
async def test_c1_detached_exceptions_are_contained_with_the_type_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """(c) shape point 7 + (d) — a fault inside the detached task never
    propagates, and the log carries the exception TYPE and NOTHING else
    (no message, no payload, no context)."""

    class _Boom:
        """A registry whose consult faults with a LOADED message."""

        async def execute(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("SECRET-PAYLOAD-MARKER the query text leaks here")

    config = make_config()
    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(smart_handler))
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    overlay = DecisionOverlay(
        registry=registry,
        kill=KillChannelResolver(_InMemStoreOn()),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
    )
    monkeypatch.setattr(overlay._registry, "execute", _Boom().execute)

    with caplog.at_level("WARNING", logger="daemon.services.decision.overlay"):
        # consult_detached itself must not raise, whatever the task does.
        overlay.consult_detached(
            "snapshot.l3.selection",
            heuristic_outcome=["id-a"],
            context={"input": "x"},
        )
        await overlay.wait_for_detached()

    assert "RuntimeError" in caplog.text, "the failure CLASS is visible"
    assert "SECRET-PAYLOAD-MARKER" not in caplog.text, (
        "the exception MESSAGE must never be logged"
    )
    assert "the query text" not in caplog.text


@pytest.mark.asyncio
async def test_c1_a_kill_written_between_dispatch_and_execution_is_read_inside_the_task() -> None:
    """(c) shape points 5+6 — the gate is read INSIDE the task: a kill
    flipped after dispatch but before the gate read executes IS
    honoured (suppressed ``switch_off``, no call). The disclosed
    detachment semantic is bounded at exactly this one in-flight
    consult."""

    class _GatePark:
        """A resolver wrapper that parks BEFORE the gate read."""

        def __init__(self, inner: KillChannelResolver, gate: asyncio.Event) -> None:
            self._inner = inner
            self._gate = gate

        async def read_state(self, seam_id: str):
            await self._gate.wait()
            return await self._inner.read_state(seam_id)

    gate = asyncio.Event()
    config = make_config()
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response(
            {
                "model": "decision",
                "answers": [
                    {
                        "type": "choice",
                        "name": "s1_candidate_selection",
                        "choice": "id-a",
                        "probabilities": [
                            {"value": v, "probability": 0.5}
                            for v in ("id-a", "id-b", "id-c")
                        ],
                        "confidence": 0.9,
                    }
                ],
            }
        )

    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(_handler))
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    store = _InMemStoreOn()
    overlay = DecisionOverlay(
        registry=registry,
        kill=_GatePark(KillChannelResolver(store), gate),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
    )

    overlay.consult_detached(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    # The kill is written AFTER dispatch, BEFORE the task's gate read.
    await store.set(GLOBAL_ENABLED_KEY, "0")
    gate.set()
    await overlay.wait_for_detached()

    assert calls == [], "the post-dispatch kill must be honoured — no call"


@pytest.mark.asyncio
async def test_c1_the_suppressed_detached_consult_writes_its_row_inside_the_task() -> None:
    """(c) shape point 4 — the A/B row lands INSIDE the task, including
    suppression and timeout rows; nothing is written at dispatch time."""
    capture = RowCapture()
    config = make_config()
    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(smart_handler))
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    # The store has NO switches set — the gate read inside the task
    # resolves OFF ⇒ a switch_off suppressed row, written in-task.
    overlay = DecisionOverlay(
        registry=registry,
        kill=KillChannelResolver(_InMemStoreOff()),
        shadow_writer=ShadowWriter(sink=capture),
        config=config,
        configured=True,
    )
    assert capture.rows == [], "dispatch writes nothing"

    overlay.consult_detached(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    await overlay.wait_for_detached()

    assert len(capture.rows) == 1, "the row is written inside the task"
    assert "switch_off" in capture.last


@pytest.mark.asyncio
async def test_c1_spend_and_usage_accrue_inside_the_task_not_at_dispatch() -> None:
    """(c) shape point 5 — cost accounting lives inside the task: spend
    accrues on consults, never on dispatches."""
    capture = RowCapture()
    config = make_config()
    calls: list[httpx.Request] = []
    release = asyncio.Event()

    async def _parked(request: httpx.Request) -> httpx.Response:
        await release.wait()
        calls.append(request)
        return json_response(
            {
                "model": "decision",
                "usage": {"cost_usd": 0.25, "total_tokens": 1000},
                "answers": [
                    {
                        "type": "choice",
                        "name": "s1_candidate_selection",
                        "choice": "id-a",
                        "probabilities": [
                            {"value": v, "probability": 0.5}
                            for v in ("id-a", "id-b", "id-c")
                        ],
                        "confidence": 0.9,
                    }
                ],
            }
        )

    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(_parked))
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    overlay = DecisionOverlay(
        registry=registry,
        kill=KillChannelResolver(_InMemStoreOn()),
        shadow_writer=ShadowWriter(sink=capture),
        config=config,
        configured=True,
    )

    seam = "snapshot.l3.selection"
    tokens_before = metrics.cost_tokens_total.get(seam_id=seam)

    overlay.consult_detached(seam, heuristic_outcome=["id-a"], context={"input": "x"})
    # Parked at the socket: NOTHING has accrued yet.
    assert metrics.cost_tokens_total.get(seam_id=seam) == tokens_before
    assert calls == []

    release.set()
    await overlay.wait_for_detached()

    assert metrics.cost_tokens_total.get(seam_id=seam) == tokens_before + 1000, (
        "usage is metered inside the task, once the consult actually happened"
    )
    assert len(calls) == 1


# ── (a) the global-master poison is NOT inert ──────────────────────────


@pytest.mark.asyncio
async def test_a1_global_poison_a_failed_global_write_resolves_off_not_on() -> None:
    """(a) — a failed GLOBAL-MASTER write poisons the GLOBAL tier:
    subsequent reads resolve UNREADABLE ⇒ OFF, and the consult makes NO
    call (bounded: one consult). Before the fix the poison was written
    but NO read path consulted it — the stale ON stood."""
    config = make_config()
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response(
            {
                "model": "decision",
                "answers": [
                    {
                        "type": "choice",
                        "name": "s1_candidate_selection",
                        "choice": "id-a",
                        "probabilities": [
                            {"value": v, "probability": 0.5}
                            for v in ("id-a", "id-b", "id-c")
                        ],
                        "confidence": 0.9,
                    }
                ],
            }
        )

    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(_handler))
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    store = _InMemStoreOn()
    kill = KillChannelResolver(store)
    capture = RowCapture()
    overlay = DecisionOverlay(
        registry=registry,
        kill=kill,
        shadow_writer=ShadowWriter(sink=capture),
        config=config,
        configured=True,
    )

    # The write-failure path poisons GLOBAL_ENABLED_KEY (what
    # ``AppSettingKillStore.set`` does on a read-back mismatch).
    kill._poisoned.update({GLOBAL_ENABLED_KEY, "__global_poison__"})

    state = await kill.read_state("snapshot.l3.selection")
    assert state.readable is False, "the poisoned global tier must resolve UNREADABLE"
    assert state.global_on is False, "global OFF — never the stale ON"

    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == [], "an unreadable global tier makes NO call"
    assert result.suppression == "switch_unreadable"
    assert "switch_unreadable" in capture.last

    # The operator retry clears it (invalidate(None) is the documented
    # retry; the poisoned set survives cache drops, only an explicit
    # invalidate clears it).
    kill.invalidate(None)
    state = await kill.read_state("snapshot.l3.selection")
    assert state.readable is True and state.global_on is True


# ── (e) a connect-phase fault never opened a socket ────────────────────


@pytest.mark.asyncio
async def test_e_connect_phase_faults_reach_no_socket_and_accrue_no_floor() -> None:
    """(e) — ``ConnectError``/``ConnectTimeout`` never established the
    TCP connection: ``reached_socket=False``, and the D3 floor is NOT
    applied (a mid-flight ``ReadTimeout`` keeps the conservative True,
    which is the D3-bills case already pinned)."""
    config = make_config(cost_floor_per_attempt_usd=0.5)

    def _connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    def _connect_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out during connect")

    def _read_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out mid-read")

    for handler, expected_label in (
        (_connect_error, FailureClass.CONNECT_ERROR.value),
        (_connect_timeout, FailureClass.TIMEOUT.value),
    ):
        client = DecisionClient(
            config, transport_factory=lambda h=handler: httpx.MockTransport(h)
        )
        outcome = await client.consult(choice_request())
        assert outcome.failure_class == expected_label
        assert outcome.reached_socket is False, (
            f"{expected_label}: the connection was never established — "
            "reached_socket must be False"
        )

    # The floor discriminator: a connect-error consult accrues NO floor.
    client = DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(_connect_error)
    )
    seam = "snapshot.l3.selection"
    cost_before = metrics.cost_total.get(seam_id=seam)
    outcome = await client.consult(choice_request())
    assert outcome.reached_socket is False
    floor_delta = metrics.cost_total.get(seam_id=seam) - cost_before
    assert floor_delta == 0.0, (
        "a connect-error outcome accrues NO floor — no socket, no spend"
    )

    # The contrast: a MID-FLIGHT timeout keeps reached_socket=True (the
    # socket WAS open — the D3 floor applies there, per the D3 pin).
    client = DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(_read_timeout)
    )
    outcome = await client.consult(choice_request())
    assert outcome.failure_class == FailureClass.TIMEOUT.value
    assert outcome.reached_socket is True


# ── (f) the per-key raw-leaf accessor + the fail-soft settings read ────


def test_f_the_per_key_accessor_reads_the_decision_section_and_fails_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(f) — the decision specs declare their own raw-yaml accessor (no
    name-based special-case); a BROKEN decision config path makes the
    yaml tier ABSENT — the settings read resolves on the lower tiers,
    never raises."""
    spec = MANAGED_KEYS["decision_global_enabled"]
    assert spec.raw_yaml_section is not None, "the decision spec carries its accessor"
    limits_spec = MANAGED_KEYS["limits.llm_concurrency"]
    assert limits_spec.raw_yaml_section is None, "pre-existing keys are untouched"

    # The working path: the accessor's section is walked exactly like
    # the limits dict.
    monkeypatch.setattr(
        "daemon.config.raw_yaml_decision",
        lambda: {"global_enabled": 1, "seams": {"snapshot_l3_selection_stage": "shadow"}},
    )
    assert _raw_yaml_leaf({"limits": {}}, spec) == 1

    stage_spec = MANAGED_KEYS["decision_seam_snapshot_l3_selection_stage"]
    assert (
        _raw_yaml_leaf({"limits": {}}, stage_spec) == "shadow"
    ), "the nested walk goes through the accessor too"

    # THE FAIL-SOFT PATH: a broken decision config path (the accessor
    # raises) means the yaml tier is ABSENT — the read resolves, never
    # raises.
    def _broken() -> Any:
        raise RuntimeError("the decision config module is broken")

    monkeypatch.setattr("daemon.config.raw_yaml_decision", _broken)
    assert _raw_yaml_leaf({"limits": {}}, spec) is None

    # The RESOLVE path: with the yaml tier absent, the registry default
    # wins — the settings read RESOLVES (fail-soft), never raises.
    snapshot = {"decision_global_enabled": {"yaml_value": _raw_yaml_leaf(None, spec)}}
    resolved = resolve("decision_global_enabled", snapshot)
    assert resolved["value"] == 0
    assert resolved["source"] == "default"

    # A non-dict section is ABSENT too (the walk guard).
    monkeypatch.setattr("daemon.config.raw_yaml_decision", lambda: "not-a-dict")
    assert _raw_yaml_leaf({"limits": {}}, spec) is None

    # A spec with NO accessor keeps reading the passed-in SECTION dict
    # (byte-for-byte behaviour for every pre-existing key).
    assert _raw_yaml_leaf({"llm_concurrency": 4}, limits_spec) == 4


# ── (g1)+(g2) the pre-flight guard + the budget clamp ──────────────────


@pytest.mark.asyncio
async def test_g_a_malformed_preflight_input_yields_the_failure_never_a_raise() -> None:
    """(g1) — the pre-flight sits INSIDE a never-raises guard: a
    malformed request shape yields the SEAM_FAULT outcome (the caller's
    heuristic stands), never an exception, and never a socket."""

    class _NotIterable:
        """``not q`` is False, ``for q in q`` raises TypeError."""

        def __bool__(self) -> bool:
            return True

        def __iter__(self) -> Any:
            raise TypeError("questions is not iterable — malformed input")

    config = make_config()
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response({})

    client = DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(_handler)
    )
    request = choice_request()
    object.__setattr__(request, "questions", _NotIterable())

    outcome = await client.consult(request)  # must NOT raise
    assert outcome.failure_class == FailureClass.SEAM_FAULT.value
    assert outcome.reached_socket is False, "a pre-flight fault never opened a socket"
    assert calls == [], "no socket for a malformed request"


@pytest.mark.asyncio
async def test_g2_a_nan_timeout_budget_is_clamped_never_a_fault() -> None:
    """(g2) — a NaN/garbage timeout budget is clamped to the configured
    budget (then to the §4.2 default): the consult proceeds normally
    instead of dying in ``wait_for`` with a ValueError."""

    config = make_config()
    calls: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        # Yield long enough that the loop must SELECT with the armed
        # (possibly NaN) timer — the crash mode of the unclamped shape.
        calls.append(request)
        await asyncio.sleep(0.05)
        return json_response(
            {
                "model": "decision",
                "answers": [
                    {
                        "type": "choice",
                        "name": "s1_candidate_selection",
                        "choice": "id-a",
                        "probabilities": [
                            {"value": v, "probability": 0.5}
                            for v in ("id-a", "id-b", "id-c")
                        ],
                        "confidence": 0.9,
                    }
                ],
            }
        )

    client = DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(_handler)
    )
    outcome = await client.consult(choice_request(), timeout_s=float("nan"))
    assert outcome.failure_class is None, (
        "a NaN budget must be clamped, not turned into a SEAM_FAULT"
    )
    assert outcome.suppression is None, "the consult proceeds — not suppressed"
    assert len(calls) == 1, "the clamped budget armed a real timer and completed"

    # A negative budget is clamped the same way.
    outcome = await client.consult(choice_request(), timeout_s=-5.0)
    assert outcome.failure_class is None


# ── (A1) the invalidation narrowing spy ────────────────────────────────


class _SpyResolver:
    """Counts invalidate() calls — the spy half of the A1 pin."""

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def invalidate(self, seam_id: str | None = None) -> None:
        self.calls.append(seam_id)


def test_a1_the_invalidation_narrowing_zero_unrelated_exactly_one_decision() -> None:
    """(A1) — an UNRELATED settings write produces ZERO invalidations;
    a ``decision_*`` write produces EXACTLY ONE (``invalidate(None)`` —
    the full cache drop). The tidier proved this manually; this pin
    makes it permanent."""
    spy = _SpyResolver()

    class _ManagerStub:
        def __init__(self, resolver: Any) -> None:
            self._decision_kill_resolver = resolver

    # The REAL wiring method, bound onto the stub — the narrowing
    # lambda under test is the shipped code, not a copy.
    _ManagerStub.set_settings_service = InstanceManager.set_settings_service  # type: ignore[attr-defined]

    engine = _sqlite_engine()
    service = SettingsService(engine)
    manager = _ManagerStub(spy)
    manager.set_settings_service(service)

    # An UNRELATED settings write — ZERO invalidations.
    service.set_override("limits.llm_concurrency", 4, updated_by="tester")
    assert spy.calls == [], "an unrelated settings write must not invalidate"

    # A decision_* write — EXACTLY ONE invalidation (the full drop).
    service.set_override("decision_global_enabled", 1, updated_by="tester")
    assert spy.calls == [None], (
        "a decision_* write invalidates exactly once, with seam_id=None"
    )

    # And a second decision key write — one MORE (exactly one per write).
    service.set_override("decision_seam_snapshot_l3_selection_stage", "deciding", updated_by="tester")
    assert spy.calls == [None, None]


# ── (b) the empty-heuristic paths never consult nor dispatch ──────────


@pytest.mark.asyncio
async def test_b_the_malformed_llm_output_path_never_consults_nor_dispatches() -> None:
    """(b) branch 1 — a malformed LLM selection parses to ``[]`` and
    ``_llm_select`` returns BEFORE the hook: no consult, no dispatch,
    no call."""
    config = make_config()
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response({})

    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(_handler))
    registry = SeamRegistry(client=client, config=config)
    registry.register(build_s1_seam_record())
    overlay = DecisionOverlay(
        registry=registry,
        kill=KillChannelResolver(_InMemStoreOn()),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
    )

    class _GarbageChat:
        def create(self, *a, **kw):
            m = MagicMock()
            m.choices = [MagicMock(message=MagicMock(content="I cannot answer that"))]
            return m

    llm = MagicMock()
    llm.chat.completions = _GarbageChat()

    rows = [_FakeSnapshot("snap-1"), _FakeSnapshot("snap-2")]
    service = SnapshotSearchService(
        snapshot_repo=_FakeRepo(rows),
        embedding_service=_FakeEmb(),  # type: ignore[arg-type]
        llm_config={"model": "test-chat"},
        staleness_fn=_staleness_stub,
        decision_overlay=overlay,
    )
    result = await service.search(query="find task", project_id="p1", llm_client=llm)
    await overlay.wait_for_detached()

    assert overlay._consults == {}, "the empty-heuristic path never consults"
    assert overlay._detached_tasks == set(), "…and never dispatches"
    assert calls == [], "…and never reaches the decision proxy"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_b_the_no_chat_model_path_never_consults_nor_dispatches() -> None:
    """(b) branch 2 — the graceful-degrade path (no chat model) bypasses
    ``_llm_select`` ENTIRELY: no consult, no dispatch, no call. A
    consult here would turn every failed LLM selection into a billable
    call the design never budgeted (the leader's (b) ruling)."""
    config = make_config()
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response({})

    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(_handler))
    registry = SeamRegistry(client=client, config=config)
    registry.register(build_s1_seam_record())
    overlay = DecisionOverlay(
        registry=registry,
        kill=KillChannelResolver(_InMemStoreOn()),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
    )

    llm = MagicMock()  # never called — no chat model configured
    rows = [_FakeSnapshot("snap-1"), _FakeSnapshot("snap-2")]
    service = SnapshotSearchService(
        snapshot_repo=_FakeRepo(rows),
        embedding_service=_FakeEmb(),  # type: ignore[arg-type]
        llm_config={},  # ← the no-chat-model branch
        staleness_fn=_staleness_stub,
        decision_overlay=overlay,
    )
    result = await service.search(query="find task", project_id="p1", llm_client=llm)
    await overlay.wait_for_detached()

    assert overlay._consults == {}
    assert overlay._detached_tasks == set()
    assert calls == []
    assert result["error"] is None
    assert len(result["results"]) == 2, "the BM25/cosine order still lands"


# ── the tiny in-memory kill stores (shadow/deciding/off) ───────────────


def _InMemStoreOn(stage: str = "shadow"):
    values = _on_seam()
    values[seam_stage_key("snapshot.l3.selection")] = stage
    return InMemoryKillStore(values)


def _InMemStoreOff():
    return InMemoryKillStore(
        {
            GLOBAL_ENABLED_KEY: "0",
            seam_enabled_key("snapshot.l3.selection"): "0",
            seam_stage_key("snapshot.l3.selection"): "shadow",
        }
    )
