"""Behavioural unit surface for the ``notify_user`` tool (ari-voice, 2026-10-25).

WHAT THIS FILE PROVES (the frozen plan's §4 test strategy)
---------------------------------------------------------
* the D1 resolution chain — the 13-edge census from
  ``architecture-recommendation.md`` §4 (API origin, scheduler-only,
  child-inherited binding, disabled adapter, circuit open, unregistered
  source, multi-source structurally-impossible, terminal-revived,
  oversize body, rate refusals, timeout, definite failure, internal
  error);
* the rate gates — the C3 commit-before-count under the B2
  per-destination-key lock, the N3 budget predicate (ONLY rows in the
  ACCEPTED CLASS — ``accepted`` plus the settled terminal forms —
  consume budget; EVERY refusal class is ledgered and NONE consumes),
  the N4 crash-orphan sweep, and the restart-durability sim (the ledger
  is the only budget state — a fresh "process" sees the old rows);
* the failure paths — the ``notify_dispatch_timeout`` delivery-UNKNOWN
  envelope (never retried), ``notify_dispatch_failed`` after the single
  retry, the N2 fail-closed pin (ledger failure ⇒ NO send), the N9
  kill-switch, and the ari-only literal-name bypass control;
* the W10 security properties — gate concurrency, cross-instance
  shared-cap, the budget predicate, and ledger completeness +
  body-never-cleartext. Each carries a mutation leg (the planted
  defects live in ``test/packs/notify_user_mutation_legs.sh``).

THE LEDGER IS REAL
------------------
The rate tests run against a REAL ``NotifyAuditRepository`` over a
temp-file SQLite engine (the house pattern from
``tests/unit/test_instance_ops_behaviour.py``) — the budget semantics
under test are SQL semantics, not mock semantics. The adapters, the
instance repository and the source repository are fakes (the seam under
test is the TOOL's policy, not the adapters').

ENV FENCE
---------
The bounds resolve from the environment at CALL time, so every test
scrubs ``ENSEMBLE_NOTIFY_USER_*`` first (the house per-variable scrub
rule — ``env -u POSTGRES_*`` style globbing is a silent NO-OP).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.config import NotifyUserConfig
from daemon.repositories.notify_audit.models import NotifyAudit
from daemon.repositories.notify_audit.repository import NotifyAuditRepository
from daemon.sources.base import OutgoingMessage, SourceStatus
from daemon.tools.notify_user import (
    NOTIFY_CODE_AUDIT_UNAVAILABLE,
    NOTIFY_CODE_DISABLED,
    NOTIFY_CODE_DISPATCH_FAILED,
    NOTIFY_CODE_DISPATCH_TIMEOUT,
    NOTIFY_CODE_INTERNAL_ERROR,
    NOTIFY_CODE_NO_BOUND_CHAT_SOURCE,
    NOTIFY_CODE_RATE_LIMITED,
    NOTIFY_CODE_SIZE_EXCEEDED,
    NOTIFY_CODE_SOURCE_CIRCUIT_OPEN,
    NOTIFY_CODE_SOURCE_DISABLED,
    NOTIFY_CODE_SOURCE_NOT_CHAT_TYPE,
    NOTIFY_CODE_SOURCE_NOT_REGISTERED,
    NOTIFY_TARGET_HASH,
    create_notify_user_tools,
)

CHAT_SOURCE = "discord-main"
CHAT_USER = "4242"


# ── fakes ────────────────────────────────────────────────────────────────


class FakeInstanceRow:
    def __init__(
        self,
        instance_id: str,
        *,
        parent_id: str | None = None,
        original_source: str | None = None,
        status: str = "running",
    ) -> None:
        self.instance_id = instance_id
        self.parent_id = parent_id
        self.status = status
        self.instance_metadata = (
            {"original_source": original_source} if original_source else {}
        )


class FakeInstanceRepository:
    def __init__(self, rows: dict[str, FakeInstanceRow]) -> None:
        self.rows = rows
        self.get_calls: list[str] = []

    def get(self, instance_id: str) -> FakeInstanceRow | None:
        self.get_calls.append(instance_id)
        return self.rows.get(instance_id)


class FakeSourceRow:
    def __init__(
        self, source_id: str, source_type: str = "discord", enabled: bool = True
    ) -> None:
        self.source_id = source_id
        self.source_type = source_type
        self.enabled = enabled


class FakeSourceRepository:
    def __init__(self, rows: dict[str, FakeSourceRow]) -> None:
        self.rows = rows

    def get_source_config(self, source_id: str) -> FakeSourceRow | None:
        return self.rows.get(source_id)


class FakeBreaker:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    async def can_execute(self) -> bool:
        return self.ok


class FakeAdapter:
    def __init__(
        self,
        *,
        source_id: str = CHAT_SOURCE,
        status: SourceStatus = SourceStatus.RUNNING,
        send_result: bool = True,
        send_delay: float = 0.0,
        breaker_ok: bool = True,
        raises: bool = False,
    ) -> None:
        self.source_id = source_id
        self.source_type = "discord"
        self._status = status
        self._send_result = send_result
        self._send_delay = send_delay
        self._raises = raises
        self._circuit_breaker = FakeBreaker(breaker_ok)
        self.sent: list[OutgoingMessage] = []

    @property
    def status(self) -> SourceStatus:
        return self._status

    async def send(self, message: OutgoingMessage) -> bool:
        self.sent.append(message)
        if self._send_delay:
            await asyncio.sleep(self._send_delay)
        if self._raises:
            raise RuntimeError("transport exploded")
        return self._send_result


class FakeRegistry:
    def __init__(self, adapters: dict[str, FakeAdapter]) -> None:
        self._adapters = adapters

    def get(self, source_id: str) -> FakeAdapter | None:
        return self._adapters.get(source_id)


class FakeManager:
    def __init__(
        self,
        *,
        engine,
        instances: dict[str, FakeInstanceRow],
        sources: dict[str, FakeSourceRow],
        adapters: dict[str, FakeAdapter],
        config: NotifyUserConfig | None = None,
    ) -> None:
        self.engine = engine
        self._instance_repository = FakeInstanceRepository(instances)
        self._source_repository = FakeSourceRepository(sources)
        self.source_registry = FakeRegistry(adapters)
        self.config = config or NotifyUserConfig()


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _scrub_notify_env(monkeypatch) -> None:
    """Scrub the notify knobs from the environment (per-variable, never a
    glob) so the ambient shell cannot steer a test."""
    for key in list(os.environ):
        if key.startswith("ENSEMBLE_NOTIFY_USER_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def engine():
    tmp = tempfile.mkdtemp(prefix="notify-audit-")
    eng = create_engine(f"sqlite:///{tmp}/notify.db")
    SQLModel.metadata.create_all(eng, tables=[NotifyAudit.__table__])
    return eng


@pytest.fixture()
def bound_manager(engine):
    """A manager whose session is bound to a working Discord source."""
    adapter = FakeAdapter()
    manager = FakeManager(
        engine=engine,
        instances={
            "i-ari": FakeInstanceRow(
                "i-ari", original_source=f"{CHAT_SOURCE}:{CHAT_USER}"
            )
        },
        sources={CHAT_SOURCE: FakeSourceRow(CHAT_SOURCE)},
        adapters={CHAT_SOURCE: adapter},
    )
    manager.adapter = adapter
    return manager


def _tool(manager, *, instance_id: str = "i-ari", agent_id: str = "ari"):
    tools = create_notify_user_tools(
        manager,
        current_instance_id=instance_id,
        agent_id=agent_id,
    )
    assert len(tools) == 1
    return tools[0]


def _rows(engine) -> list[NotifyAudit]:
    from sqlmodel import Session, select

    with Session(engine) as session:
        return list(session.exec(select(NotifyAudit)).all())


# ── the resolution chain (edge census 1-13) ─────────────────────────────


class TestResolutionChain:
    async def test_edge1_api_origin_is_unbound(self, bound_manager) -> None:
        """Edge 1 — the HTTP-API origin (``original_source="api"``) is
        colon-less → unbound."""
        bound_manager._instance_repository.rows["i-ari"] = FakeInstanceRow(
            "i-ari", original_source="api"
        )
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_NO_BOUND_CHAT_SOURCE in result
        assert bound_manager.adapter.sent == []

    async def test_edge2_scheduler_only_session_is_unbound(
        self, bound_manager
    ) -> None:
        """Edge 2 — a scheduler-stamped session has no chat target."""
        bound_manager._instance_repository.rows["i-ari"] = FakeInstanceRow(
            "i-ari", original_source="scheduler"
        )
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_NO_BOUND_CHAT_SOURCE in result
        assert bound_manager.adapter.sent == []

    async def test_edge3_child_inherits_the_root_binding(
        self, bound_manager
    ) -> None:
        """Edge 3 — a child mid-turn resolves through the parent walk to
        the ROOT's chat source."""
        bound_manager._instance_repository.rows["i-child"] = FakeInstanceRow(
            "i-child", parent_id="i-ari", original_source=None
        )
        tool = _tool(bound_manager, instance_id="i-child")
        result = await tool.ainvoke({"message": "from the child"})
        assert result["ok"] is True, result
        assert bound_manager.adapter.sent[0].external_user_id == CHAT_USER

    async def test_edge3b_child_with_unbound_root_refuses(
        self, bound_manager
    ) -> None:
        """Edge 3 (negative) — an unbound root means no legal target."""
        bound_manager._instance_repository.rows["i-root"] = FakeInstanceRow(
            "i-root", original_source="api"
        )
        bound_manager._instance_repository.rows["i-child"] = FakeInstanceRow(
            "i-child", parent_id="i-root"
        )
        tool = _tool(bound_manager, instance_id="i-child")
        result = await tool.ainvoke({"message": "from the child"})
        assert NOTIFY_CODE_NO_BOUND_CHAT_SOURCE in result
        assert bound_manager.adapter.sent == []

    async def test_edge4_disabled_source_refuses(self, bound_manager) -> None:
        """Edge 4 — ``enabled=False`` on the source row."""
        bound_manager._source_repository.rows[CHAT_SOURCE] = FakeSourceRow(
            CHAT_SOURCE, enabled=False
        )
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_SOURCE_DISABLED in result
        assert bound_manager.adapter.sent == []

    async def test_edge4b_adapter_not_running_refuses(
        self, bound_manager
    ) -> None:
        """Edge 4 (variant) — the adapter status is not RUNNING."""
        bound_manager.adapter._status = SourceStatus.STOPPED
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_SOURCE_DISABLED in result
        assert bound_manager.adapter.sent == []

    async def test_edge5_circuit_open_refuses(self, bound_manager) -> None:
        """Edge 5 — the adapter's circuit breaker is OPEN."""
        bound_manager.adapter._circuit_breaker = FakeBreaker(ok=False)
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_SOURCE_CIRCUIT_OPEN in result
        assert bound_manager.adapter.sent == []

    async def test_edge6_unregistered_source_refuses(self, bound_manager) -> None:
        """Edge 6 — the source_configs row is gone."""
        bound_manager._source_repository.rows.pop(CHAT_SOURCE)
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_SOURCE_NOT_REGISTERED in result
        assert bound_manager.adapter.sent == []

    async def test_edge6b_no_adapter_refuses(self, bound_manager) -> None:
        """Edge 6 (variant) — the row exists but no adapter is built
        (the whatsapp class: semantically a chat type, not buildable)."""
        bound_manager.source_registry = FakeRegistry({})
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_SOURCE_NOT_REGISTERED in result
        assert bound_manager.adapter.sent == []

    async def test_edge6c_non_chat_source_type_refuses(
        self, bound_manager
    ) -> None:
        """Edge 6 (variant) — the source type is not a human chat channel."""
        bound_manager._source_repository.rows[CHAT_SOURCE] = FakeSourceRow(
            CHAT_SOURCE, source_type="webhook"
        )
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_SOURCE_NOT_CHAT_TYPE in result
        assert bound_manager.adapter.sent == []

    async def test_edge7_multi_source_is_structurally_impossible(
        self, bound_manager
    ) -> None:
        """Edge 7 — the anchor is the SINGLE write-once string; the
        mapping-table reverse lookup is never used, so two registered
        chat sources cannot make the destination ambiguous."""
        bound_manager._instance_repository.rows["i-ari"] = FakeInstanceRow(
            "i-ari", original_source=f"{CHAT_SOURCE}:{CHAT_USER}"
        )
        bound_manager._source_repository.rows["second-chat"] = FakeSourceRow(
            "second-chat"
        )
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert result["ok"] is True
        assert result["source_id"] == CHAT_SOURCE
        assert bound_manager.adapter.sent[0].source_id == CHAT_SOURCE

    async def test_edge8_terminal_then_revived_still_resolves(
        self, bound_manager
    ) -> None:
        """Edge 8 — instance_metadata is permanent; the calling session's
        status is irrelevant."""
        bound_manager._instance_repository.rows["i-ari"] = FakeInstanceRow(
            "i-ari",
            original_source=f"{CHAT_SOURCE}:{CHAT_USER}",
            status="completed",
        )
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert result["ok"] is True, result

    async def test_edge9_oversize_body_refuses_before_resolution(
        self, bound_manager
    ) -> None:
        """Edge 9 + fold-able 6 — the size gate is PURE and runs BEFORE
        any resolution work (the instance repository is never touched),
        and the refusal is ledgered with a NULL source_id."""
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "x" * 2001})
        assert NOTIFY_CODE_SIZE_EXCEEDED in result
        assert bound_manager._instance_repository.get_calls == [], (
            "the size gate must reject BEFORE any binding resolution"
        )
        rows = _rows(bound_manager.engine)
        assert len(rows) == 1
        assert rows[0].outcome == "refused"
        assert rows[0].refusal_reason == NOTIFY_CODE_SIZE_EXCEEDED
        assert rows[0].source_id is None
        assert rows[0].target_hash is None

    async def test_empty_body_refuses_under_the_size_code(
        self, bound_manager
    ) -> None:
        """The frozen vocabulary has no empty-body code — it folds into
        the size class."""
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": ""})
        assert NOTIFY_CODE_SIZE_EXCEEDED in result
        assert bound_manager.adapter.sent == []

    async def test_edge13_internal_error_is_typed_and_never_raises(
        self, bound_manager, monkeypatch
    ) -> None:
        """Edge 13 — an unexpected exception is logged server-side and
        answered with the typed envelope (the PB-F1 leak class: no
        exception text in the payload)."""
        import daemon.tools.notify_user as module

        def _boom(*_args, **_kwargs):
            raise RuntimeError("secret internal detail")

        monkeypatch.setattr(module, "_resolve_bounds", _boom)
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_INTERNAL_ERROR in result
        assert "secret internal detail" not in result


# ── the failure paths ───────────────────────────────────────────────────


class TestFailurePaths:
    async def test_edge11_timeout_is_delivery_unknown_and_never_retried(
        self, bound_manager, monkeypatch
    ) -> None:
        """Edge 11 — the 10s bound fires: the envelope says UNKNOWN and
        the adapter is called EXACTLY ONCE (D4/D10 — never a blind
        retry)."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_TIMEOUT_SECONDS", "1")
        bound_manager.adapter._send_delay = 5.0
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_DISPATCH_TIMEOUT in result
        assert "UNKNOWN" in result
        assert len(bound_manager.adapter.sent) == 1

    async def test_the_timeout_settles_the_row_as_delivery_unknown(
        self, bound_manager, monkeypatch
    ) -> None:
        """The write-ahead row must not read as a false delivery."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_TIMEOUT_SECONDS", "1")
        bound_manager.adapter._send_delay = 5.0
        tool = _tool(bound_manager)
        await tool.ainvoke({"message": "hello"})
        rows = _rows(bound_manager.engine)
        assert len(rows) == 1
        assert rows[0].outcome == "delivery_unknown"

    async def test_edge12_definite_failure_after_the_single_retry(
        self, bound_manager, monkeypatch
    ) -> None:
        """Edge 12 — the adapter returns False; D10 allows exactly ONE
        transient retry, then the definite-failure envelope."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_TIMEOUT_SECONDS", "10")
        bound_manager.adapter._send_result = False
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_DISPATCH_FAILED in result
        assert len(bound_manager.adapter.sent) == 2, (
            "D10 — exactly one transient retry (2 attempts total)"
        )

    async def test_n2_ledger_failure_is_fail_closed(self, bound_manager) -> None:
        """N2 — a ledger failure on the send path means NO send and the
        typed refusal."""
        repo = NotifyAuditRepository(bound_manager.engine)

        def _boom(**_kwargs):
            raise RuntimeError("ledger down")

        repo.insert_accepted = _boom  # type: ignore[method-assign]
        import daemon.tools.notify_user as module

        original = module._audit_repo_for
        module._audit_repo_for = lambda manager: repo  # type: ignore[assignment]
        try:
            tool = _tool(bound_manager)
            result = await tool.ainvoke({"message": "hello"})
        finally:
            module._audit_repo_for = original  # type: ignore[assignment]
        assert NOTIFY_CODE_AUDIT_UNAVAILABLE in result
        assert bound_manager.adapter.sent == [], (
            "N2 — no ledger row, no send"
        )

    async def test_n9_kill_switch_refuses_and_sends_nothing(
        self, bound_manager, monkeypatch
    ) -> None:
        """N9 — ``ENSEMBLE_NOTIFY_USER_ENABLED=false`` disables the
        tool with the typed refusal."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_ENABLED", "false")
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_DISABLED in result
        assert bound_manager.adapter.sent == []
        rows = _rows(bound_manager.engine)
        assert len(rows) == 1
        assert rows[0].outcome == "refused"
        assert rows[0].refusal_reason == NOTIFY_CODE_DISABLED

    async def test_the_ari_only_gate_refuses_a_non_ari_caller(
        self, bound_manager
    ) -> None:
        """Layer 3 — the literal-name bypass control: a non-ari caller
        that somehow holds the tool gets a refusal, NO ledger row and
        NO send."""
        tool = _tool(bound_manager, agent_id="leader")
        result = await tool.ainvoke({"message": "hello"})
        assert "ari agent only" in result
        assert bound_manager.adapter.sent == []
        assert _rows(bound_manager.engine) == [], (
            "the layer-3 gate is PRE-AUDIT — a caller that never had the "
            "tool legitimately leaves no ledger noise"
        )


# ── the rate gates ──────────────────────────────────────────────────────


class TestRateGates:
    async def test_the_interval_gate_refuses_the_second_send(
        self, bound_manager
    ) -> None:
        """N=1/turn as the >=60s interval proxy — the second send inside
        the window is refused and the refusal states when the next send
        becomes legal."""
        tool = _tool(bound_manager)
        first = await tool.ainvoke({"message": "one"})
        assert first["ok"] is True, first
        second = await tool.ainvoke({"message": "two"})
        assert NOTIFY_CODE_RATE_LIMITED in second
        assert "legal in" in second
        assert len(bound_manager.adapter.sent) == 1

    async def test_an_unparseable_interval_baseline_refuses_fail_closed(
        self, bound_manager
    ) -> None:
        """A4 — an UNPARSEABLE baseline stamp fails CLOSED (the safe
        direction for outbound-to-user traffic): the minimum interval
        cannot be verified, so the interval is NOT satisfiable. The
        send is REFUSED (typed, ledgered) and NOTHING reaches the
        adapter.

        The old branch fell back to ``elapsed = min_interval_seconds``
        (=> wait_s = 0 => the send went through on a garbage stamp) —
        the fail-OPEN direction this test pins shut.
        """
        from sqlmodel import Session

        # Plant a baseline row whose created_at cannot be parsed. It
        # sorts AFTER any ISO stamp ("n" > "2"), so the MAX() baseline
        # read returns IT — the gate sees an unparseable baseline.
        with Session(bound_manager.engine) as session:
            session.add(
                NotifyAudit(
                    id="garbage-baseline",
                    created_at="not-a-timestamp",
                    agent_id="ari",
                    instance_id="i-ari",
                    source_id=CHAT_SOURCE,
                    target_hash=NOTIFY_TARGET_HASH(CHAT_SOURCE, CHAT_USER),
                    outcome="accepted",
                    body_length=3,
                    body_sha256="deadbeef",
                )
            )
            session.commit()

        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "one"})
        assert NOTIFY_CODE_RATE_LIMITED in result, result
        assert bound_manager.adapter.sent == [], (
            "an unparseable baseline must NEVER let a send through"
        )
        # The refusal is LEDGERED (N3 discipline — every refusal is a
        # row, and it names the unparseable baseline).
        refusals = [
            row
            for row in _rows(bound_manager.engine)
            if row.outcome == "refused"
        ]
        assert len(refusals) == 1, refusals
        assert refusals[0].refusal_reason == NOTIFY_CODE_RATE_LIMITED
        assert "unparseable" in (refusals[0].detail or "")

    async def test_the_orphan_sweep_threshold_scales_with_the_bound(
        self, bound_manager, monkeypatch
    ) -> None:
        """A2 — the N4 sweep threshold is DERIVED from the (tunable)
        dispatch bound — ``max(120s, 12 x timeout_seconds)`` — so
        raising the bound raises the threshold with it and a live
        dispatch can never be swept. The 120s FLOOR still holds below
        the default bound."""
        seen: list[int] = []

        def _capture(self, *, older_than_seconds: int) -> int:
            seen.append(older_than_seconds)
            return 0

        monkeypatch.setattr(
            NotifyAuditRepository, "sweep_stale_accepted", _capture
        )
        tool = _tool(bound_manager)

        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_TIMEOUT_SECONDS", "30")
        await tool.ainvoke({"message": "one"})
        assert seen == [360], seen  # 12 x the 30s bound

        # The FLOOR: a SMALLER bound keeps the 120s threshold.
        seen.clear()
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_TIMEOUT_SECONDS", "5")
        await tool.ainvoke({"message": "two"})
        assert seen == [120], seen  # max(120, 12 x 5) == 120

    async def test_the_hourly_cap_is_per_resolved_destination(
        self, bound_manager, monkeypatch
    ) -> None:
        """M=6 per rolling hour per DESTINATION (W1) — the cap is keyed
        on the source-qualified target hash, so a second session for the
        same user shares it."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS", "0")
        bound_manager._instance_repository.rows["i-ari2"] = FakeInstanceRow(
            "i-ari2", original_source=f"{CHAT_SOURCE}:{CHAT_USER}"
        )
        tool_a = _tool(bound_manager, instance_id="i-ari")
        tool_b = _tool(bound_manager, instance_id="i-ari2")
        for i in range(6):
            result = await tool_a.ainvoke({"message": f"m{i}"})
            assert result["ok"] is True, (i, result)
        # The SEVENTH send — from a DIFFERENT session, same destination.
        result = await tool_b.ainvoke({"message": "seventh"})
        assert NOTIFY_CODE_RATE_LIMITED in result
        assert len(bound_manager.adapter.sent) == 6

    async def test_the_budget_predicate_counts_only_accepted_rows(
        self, bound_manager, monkeypatch
    ) -> None:
        """N3 — refusal rows are ledgered but NEVER consume budget; only
        rows in the ACCEPTED CLASS count (``accepted`` plus the settled
        terminal forms ``dispatched`` / ``delivery_failed`` /
        ``delivery_unknown``)."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS", "0")
        repo = NotifyAuditRepository(bound_manager.engine)
        target = NOTIFY_TARGET_HASH(CHAT_SOURCE, CHAT_USER)
        for i in range(6):
            repo.insert_accepted(
                agent_id="ari",
                instance_id="i-ari",
                source_id=CHAT_SOURCE,
                target_hash=target,
                body_length=3,
                body_sha256="deadbeef",
            )
            repo.write_refusal(
                agent_id="ari",
                instance_id="i-ari",
                source_id=CHAT_SOURCE,
                target_hash=target,
                refusal_reason=NOTIFY_CODE_RATE_LIMITED,
                body_length=3,
                body_sha256="deadbeef",
            )
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert NOTIFY_CODE_RATE_LIMITED in result, (
            "six accepted rows already consumed the cap"
        )

    async def test_refusal_rows_alone_never_trigger_the_cap(
        self, bound_manager, monkeypatch
    ) -> None:
        """C3 (allow-direction probe) — refused rows WITH a resolved
        destination (e.g. past rate_limited / circuit-open refusals)
        never consume budget: six of them leave the hour fully
        available. This is the direction the drop-the-filter mutation
        flips (the refused rows would consume and the send would
        refuse)."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS", "0")
        repo = NotifyAuditRepository(bound_manager.engine)
        target = NOTIFY_TARGET_HASH(CHAT_SOURCE, CHAT_USER)
        for _ in range(6):
            repo.write_refusal(
                agent_id="ari",
                instance_id="i-ari",
                source_id=CHAT_SOURCE,
                target_hash=target,
                refusal_reason=NOTIFY_CODE_RATE_LIMITED,
                body_length=3,
                body_sha256="deadbeef",
            )
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert result["ok"] is True, (
            "six REFUSED rows must not consume the budget — the send is "
            f"legal (got: {result})"
        )

    async def test_a_refusal_burst_does_not_self_dose_the_hour(
        self, bound_manager, monkeypatch
    ) -> None:
        """C3 — the refusal rows themselves never consume budget, so a
        burst of refusals leaves the hour intact."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS", "0")
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MAX_PER_HOUR", "2")
        tool = _tool(bound_manager)
        # Two accepted sends consume the cap.
        for i in range(2):
            result = await tool.ainvoke({"message": f"m{i}"})
            assert result["ok"] is True, result
        # A refusal storm (oversize bodies) — ledgered, never counted.
        for _ in range(5):
            result = await tool.ainvoke({"message": "x" * 2001})
            assert NOTIFY_CODE_SIZE_EXCEEDED in result
        # The cap is still exactly the two accepted sends — the refusal
        # storm never accrued (C3), and the settled sends still count.
        repo = NotifyAuditRepository(bound_manager.engine)
        target = NOTIFY_TARGET_HASH(CHAT_SOURCE, CHAT_USER)
        count = repo.count_accepted_for_target(
            target_hash=target,
            since_iso=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        assert count == 2
        rows = _rows(bound_manager.engine)
        assert len([r for r in rows if r.outcome == "refused"]) == 5

    async def test_the_restart_durability_sim_has_no_burst_window(
        self, bound_manager, monkeypatch
    ) -> None:
        """Restart durability — the ledger IS the budget state: a fresh
        process (a fresh repo instance over the same engine) sees the
        old rows and refuses."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS", "0")
        tool = _tool(bound_manager)
        first = await tool.ainvoke({"message": "one"})
        assert first["ok"] is True
        # A "restart": a brand-new repository object over the same engine.
        restarted_repo = NotifyAuditRepository(bound_manager.engine)
        target = NOTIFY_TARGET_HASH(CHAT_SOURCE, CHAT_USER)
        count = restarted_repo.count_accepted_for_target(
            target_hash=target, since_iso="1970-01-01T00:00:00+00:00"
        )
        assert count == 1, "the budget survives the restart (ledger-backed)"

    async def test_the_n4_sweep_marks_stale_accepted_rows_unknown(
        self, bound_manager
    ) -> None:
        """N4 — a crash-orphaned accepted row is swept to a terminal
        delivery_unknown (it reads as unknown, not as a false
        delivery)."""
        repo = NotifyAuditRepository(bound_manager.engine)
        target = NOTIFY_TARGET_HASH(CHAT_SOURCE, CHAT_USER)
        row_id = repo.insert_accepted(
            agent_id="ari",
            instance_id="i-ari",
            source_id=CHAT_SOURCE,
            target_hash=target,
            body_length=3,
            body_sha256="deadbeef",
        )
        assert repo.sweep_stale_accepted(older_than_seconds=0) == 1
        rows = _rows(bound_manager.engine)
        assert rows[0].id == row_id
        assert rows[0].outcome == "delivery_unknown"

    async def test_the_sweep_leaves_fresh_rows_alone(
        self, bound_manager
    ) -> None:
        """A live dispatch must never be swept."""
        repo = NotifyAuditRepository(bound_manager.engine)
        repo.insert_accepted(
            agent_id="ari",
            instance_id="i-ari",
            source_id=CHAT_SOURCE,
            target_hash=NOTIFY_TARGET_HASH(CHAT_SOURCE, CHAT_USER),
            body_length=3,
            body_sha256="deadbeef",
        )
        assert repo.sweep_stale_accepted(older_than_seconds=3600) == 0
        rows = _rows(bound_manager.engine)
        assert rows[0].outcome == "accepted"


# ── the W10 security properties ─────────────────────────────────────────


class TestSecurityProperties:
    async def test_w10_1_gate_concurrency_admits_exactly_the_cap(
        self, bound_manager, monkeypatch
    ) -> None:
        """W10-1 — the B2 per-destination lock serializes the gate: N
        concurrent calls over one destination admit EXACTLY the cap.

        The count carries a STRUCTURAL barrier (not a timing sleep):
        each call's COUNT waits until either all 12 inserts have
        committed or a bounded 250ms has elapsed. WITHOUT the lock all
        12 inserts land inside the first count's wait, so every count
        reads over the cap and the gate OVER-REFUSES (0 delivered);
        WITH the lock the gate is serialized and each count reads its
        own prefix (exactly 6 delivered). Either wrong outcome breaks
        the assertion — the leg is deterministic in both directions.
        """
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS", "0")
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MAX_PER_HOUR", "6")
        repo = NotifyAuditRepository(bound_manager.engine)

        import threading

        state = {"inserts": 0}
        lock = threading.Lock()
        original_insert = repo.insert_accepted
        original_count = repo.count_accepted_for_target

        def _counting_insert(**kwargs):
            row_id = original_insert(**kwargs)
            with lock:
                state["inserts"] += 1
            return row_id

        def _barrier_count(**kwargs):
            import time as _time

            deadline = _time.monotonic() + 0.25
            while _time.monotonic() < deadline:
                with lock:
                    if state["inserts"] >= 12:
                        break
                _time.sleep(0.005)
            return original_count(**kwargs)

        repo.insert_accepted = _counting_insert  # type: ignore[method-assign]
        repo.count_accepted_for_target = _barrier_count  # type: ignore[method-assign]
        import daemon.tools.notify_user as module

        original_repo_for = module._audit_repo_for
        module._audit_repo_for = lambda manager: repo  # type: ignore[assignment]
        try:
            tool = _tool(bound_manager)
            results = await asyncio.gather(
                *[tool.ainvoke({"message": f"m{i}"}) for i in range(12)]
            )
        finally:
            module._audit_repo_for = original_repo_for  # type: ignore[assignment]
        delivered = [
            r for r in results if isinstance(r, dict) and r.get("ok")
        ]
        refused = [r for r in results if isinstance(r, str)]
        assert len(delivered) == 6, (
            f"expected exactly 6 deliveries under the lock, got "
            f"{len(delivered)}"
        )
        assert len(refused) == 6
        assert len(bound_manager.adapter.sent) == 6

    async def test_w10_2_cross_instance_shared_cap(self, bound_manager, monkeypatch) -> None:
        """W10-2 — parallel sessions to the SAME destination share the
        cap (the destination is the spam surface)."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS", "0")
        bound_manager._instance_repository.rows["i-ari2"] = FakeInstanceRow(
            "i-ari2", original_source=f"{CHAT_SOURCE}:{CHAT_USER}"
        )
        tool_a = _tool(bound_manager, instance_id="i-ari")
        tool_b = _tool(bound_manager, instance_id="i-ari2")
        results = await asyncio.gather(
            *[tool_a.ainvoke({"message": f"a{i}"}) for i in range(4)],
            *[tool_b.ainvoke({"message": f"b{i}"}) for i in range(4)],
        )
        delivered = [r for r in results if isinstance(r, dict) and r.get("ok")]
        assert len(delivered) == 6, (
            "the two sessions must share ONE cap of six per destination"
        )

    async def test_w10_3_every_refusal_class_is_ledgered(
        self, bound_manager, monkeypatch
    ) -> None:
        """W10-3 — the ledger is complete: EVERY refusal class lands a
        row (the refusal IS the security signal) and NONE of them
        consumes budget."""
        monkeypatch.setenv("ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS", "0")
        tool = _tool(bound_manager)

        # size_exceeded (pre-resolution, source_id NULL)
        await tool.ainvoke({"message": "x" * 2001})
        # no_bound_chat_source (pre-resolution)
        bound_manager._instance_repository.rows["i-unbound"] = FakeInstanceRow(
            "i-unbound", original_source="api"
        )
        unbound_tool = _tool(bound_manager, instance_id="i-unbound")
        await unbound_tool.ainvoke({"message": "hello"})
        # source_not_registered
        bound_manager._source_repository.rows.pop(CHAT_SOURCE)
        await tool.ainvoke({"message": "hello"})
        # source_disabled
        bound_manager._source_repository.rows[CHAT_SOURCE] = FakeSourceRow(
            CHAT_SOURCE, enabled=False
        )
        await tool.ainvoke({"message": "hello"})

        rows = _rows(bound_manager.engine)
        reasons = {r.refusal_reason for r in rows}
        assert {
            NOTIFY_CODE_SIZE_EXCEEDED,
            NOTIFY_CODE_NO_BOUND_CHAT_SOURCE,
            NOTIFY_CODE_SOURCE_NOT_REGISTERED,
            NOTIFY_CODE_SOURCE_DISABLED,
        } <= reasons, reasons
        assert all(r.outcome == "refused" for r in rows)
        # None of them consumed budget.
        assert not [r for r in rows if r.outcome == "accepted"]

    async def test_w10_4_the_body_is_never_stored_as_cleartext(
        self, bound_manager
    ) -> None:
        """W10-4 — the message body is stored as sha256 + length, never
        as text."""
        secret = "CANARY-TEXT-MUST-NOT-BE-STORED-4711"
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": secret})
        assert result["ok"] is True
        rows = _rows(bound_manager.engine)
        assert len(rows) == 1
        row = rows[0]
        assert row.body_length == len(secret)
        assert row.body_sha256 == hashlib.sha256(secret.encode()).hexdigest()
        blob = " ".join(
            str(value)
            for value in (
                row.id,
                row.agent_id,
                row.instance_id,
                row.action,
                row.source_id,
                row.target_hash,
                row.outcome,
                row.refusal_reason,
                row.error_code,
                row.body_sha256,
                row.detail,
            )
        )
        assert secret not in blob, "the body leaked into the ledger"

    async def test_the_success_envelope_shape(self, bound_manager) -> None:
        """D11 — the success shape."""
        tool = _tool(bound_manager)
        result = await tool.ainvoke({"message": "hello"})
        assert result["ok"] is True
        assert result["delivered"] is True
        assert result["source_id"] == CHAT_SOURCE
        assert isinstance(result["latency_ms"], int)
