"""C1 — the init-path breaker host + the C4 journal suppression (soak-gate-mcp-warmup).

``daemon/mcp/init_guard.py`` is the ONE leaf home: the per-boot
quarantine registry, the init-path ``CircuitBreaker`` host, the DNS
pre-check and the journal-emit gate.

This file pins:

* **BLOCKING 4** — a daemon-side MCP state transition during a LIVE
  promote txn must NOT drop ``in_flight``: the journal write is
  suppressed and buffered (one entry per server+state, newest wins) and
  the buffer is flushed when the predicate flips false;
* the breaker host: after N=3 init failures the server is quarantined
  for the boot and NO further spawn is attempted, while the OTHER
  servers are unaffected (degrade alone);
* the half-open recovery probe brings a recovered server back WITHOUT a
  daemon restart;
* the class routing: ``dns_nxdomain`` is threshold-1, ``dns_servfail``
  is threshold-3, ``resolver_timeout`` is NEVER quarantine-eligible;
* ``CircuitBreaker.reset()`` — the only unsynchronised mutator — is
  NEVER called by the guard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daemon.mcp import init_guard
from daemon.mcp import warmup_pool as warmup_pool_module
from daemon.mcp.config import McpStdioConfig
from daemon.mcp.warmup_pool import McpWarmupPool
from daemon.services import promote_txn_hold
from daemon.sources.circuit_breaker import CircuitBreaker



def _write_state(install_dir: Path, in_flight: dict | None) -> Path:
    """Write a scratch ``releases/state.json``."""
    releases = install_dir / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    state = {
        "current": "v0.0.0",
        "previous": None,
        "in_flight": in_flight,
        "history": [],
    }
    path = releases / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def _live_promote_in_flight() -> dict:
    """An ``in_flight`` the §4.7 P7 predicate reads as LIVE.

    BOTH legs are made to apply so the fixture does not depend on how
    long the test session has been running:

    * the POST-BOOT leg needs a FRESH ``last_heartbeat`` (the shell's
      epoch-seconds stamp, within the 60s freshness window) — the
      daemon-boot-age leg would otherwise expire after
      ``BOOT_LIVENESS_S`` (120s) of module age, which a long test
      session reaches;
    * the BOOT-WINDOW leg needs ``started_at`` + a recent stamp.
    """
    now = time.time()
    return {
        "kind": "promote",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_heartbeat": int(now),
        "run_id": "r-test",
    }


@pytest.fixture
def scratch_install(tmp_path, monkeypatch):
    """A scratch install root wired into the promote-txn hold module."""
    install_dir = tmp_path / "install"
    monkeypatch.setattr(promote_txn_hold, "_install_dir_override", install_dir)
    return install_dir


# ── BLOCKING 4 — the journal write is suppressed during a live promote ──


async def test_state_transition_does_not_drop_in_flight_during_a_live_promote(
    scratch_install,
):
    """The daemon-side write must not take a stale snapshot of state.json.

    The write path is an UNLOCKED read -> mutate -> whole-document
    write: a daemon-side write during a live promote txn would write
    back a snapshot WITHOUT ``in_flight`` and release the A3 deferred
    resumes mid-promote.
    """
    path = _write_state(scratch_install, _live_promote_in_flight())
    before = path.read_bytes()

    assert promote_txn_hold.promote_txn_live() is True, (
        "the fixture must present a LIVE promote txn — otherwise the pin "
        "would prove nothing"
    )

    await init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED)

    after = path.read_bytes()
    assert after == before, (
        "state.json must be BYTE-IDENTICAL across the transition — a "
        "daemon-side write would drop in_flight and release the A3 "
        "deferred resumes mid-promote"
    )
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["in_flight"] is not None
    assert state["history"] == [], "the suppressed write must not be persisted"

    # The entry is BUFFERED (one per server+state).
    assert init_guard.buffered_transition_count() == 1


async def test_the_buffer_is_flushed_when_the_predicate_flips_false(scratch_install):
    """The buffered entry is flushed when the promote txn closes."""
    path = _write_state(scratch_install, _live_promote_in_flight())
    assert promote_txn_hold.promote_txn_live() is True

    await init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED)
    assert init_guard.buffered_transition_count() == 1
    assert json.loads(path.read_text(encoding="utf-8"))["history"] == []

    # The txn closes (in_flight disappears).
    _write_state(scratch_install, None)
    assert promote_txn_hold.promote_txn_live() is False

    # The explicit flush path.
    assert init_guard.flush_buffered_transitions() == 1
    assert init_guard.buffered_transition_count() == 0
    history = json.loads(path.read_text(encoding="utf-8"))["history"]
    assert len(history) == 1
    assert history[0]["event"] == init_guard.JOURNAL_EVENT
    assert "server=context7" in history[0]["detail"]


async def test_the_next_transition_flushes_the_buffer(scratch_install):
    """The flush-on-close rule: the next transition drains the buffer."""
    path = _write_state(scratch_install, _live_promote_in_flight())
    await init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED)
    assert init_guard.buffered_transition_count() == 1

    _write_state(scratch_install, None)
    init_guard.quarantine_server("context7", "manual", "init_failed")

    history = json.loads(path.read_text(encoding="utf-8"))["history"]
    assert len(history) == 2, (
        "the buffered entry must be flushed (never dropped) together with "
        "the new one"
    )
    assert init_guard.buffered_transition_count() == 0


async def test_the_buffer_keeps_one_entry_per_server_and_state(scratch_install):
    """Bounded buffer — newest wins per (server, state)."""
    _write_state(scratch_install, _live_promote_in_flight())
    await init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED)
    await init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED)
    await init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED)
    await init_guard.record_failure("webfetch", init_guard.FAILURE_CLASS_INIT_FAILED)
    # context7: degraded + quarantined (two distinct states -> two
    # entries); webfetch: degraded (one entry).
    assert init_guard.buffered_transition_count() == 3


async def test_the_transition_is_journaled_when_no_promote_is_live(scratch_install):
    """Outside a promote txn the transition IS journaled."""
    path = _write_state(scratch_install, None)
    assert promote_txn_hold.promote_txn_live() is False

    await init_guard.record_failure("context7", init_guard.FAILURE_CLASS_INIT_FAILED)
    history = json.loads(path.read_text(encoding="utf-8"))["history"]
    assert len(history) == 1
    assert history[0]["event"] == init_guard.JOURNAL_EVENT


# ── the breaker host ───────────────────────────────────────────────────


async def test_three_failures_quarantine_the_server_for_the_boot(caplog):
    """N=3 -> the breaker OPENs and the server is quarantined."""
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            await init_guard.record_failure(
                "context7", init_guard.FAILURE_CLASS_INIT_FAILED
            )

    assert init_guard.is_quarantined("context7") is True
    assert await init_guard.can_attempt("context7") is False

    entry = init_guard.server_state("context7")
    assert entry.breaker_state == "open"
    assert entry.attempts == 3

    # The quarantine is VISIBLE in the log (the WARN names the server,
    # the failure count and the reason).
    messages = [rec.getMessage() for rec in caplog.records]
    assert any(
        "context7" in m and "QUARANTINED" in m and "attempts=3" in m
        for m in messages
    ), f"the quarantine must be visible in the log: {messages}"

    # And in the observability surface.
    snapshot = init_guard.state_snapshot()
    assert snapshot["context7"]["state"] == "quarantined"
    assert snapshot["context7"]["breaker_state"] == "open"
    assert snapshot["context7"]["recoverable"] is True


async def test_degrade_alone_other_servers_are_unaffected():
    """One server's failure must not take the others down."""
    for _ in range(3):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_INIT_FAILED
        )
    assert init_guard.is_quarantined("context7") is True
    assert init_guard.is_quarantined("webfetch") is False
    assert await init_guard.can_attempt("webfetch") is True


async def test_no_further_spawn_attempts_for_a_quarantined_server(monkeypatch):
    """The prevention: a quarantined server is NEVER spawned."""
    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
    )
    spawned: list[object] = []

    class _SentinelClient:
        def __init__(self, params):  # pragma: no cover — must never run
            spawned.append(params)

    monkeypatch.setattr(warmup_pool_module, "TaskScopedStdioClient", _SentinelClient)

    for _ in range(3):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_INIT_FAILED
        )

    with pytest.raises(init_guard.McpServerQuarantined):
        await pool._create_pooled_connection("context7")
    assert spawned == [], "no child may be spawned for a quarantined server"


async def test_the_replenish_skips_a_quarantined_server(monkeypatch):
    """S3 — the replenish loop stops re-spawning a quarantined server.

    The forever-retry shape: the health check evicts a dead connection
    every 60 s and the replenish re-ran the failing init chain FOREVER.
    The quarantine ends that loop.
    """
    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
    )
    pool._running = True
    spawned: list[object] = []

    class _SentinelClient:
        def __init__(self, params):  # pragma: no cover — must never run
            spawned.append(params)

    monkeypatch.setattr(warmup_pool_module, "TaskScopedStdioClient", _SentinelClient)

    for _ in range(3):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_INIT_FAILED
        )

    calls: list[str] = []

    async def _never(*args, **kwargs):
        calls.append("create")
        raise AssertionError(
            "the replenish must NOT even attempt a create for a "
            "quarantined server"
        )

    monkeypatch.setattr(pool, "_create_pooled_connection", _never)
    await pool._replenish("context7")
    assert calls == [], (
        "the replenish must not attempt a create for a quarantined server"
    )
    assert spawned == [], (
        "the replenish must not spawn a child for a quarantined server"
    )


async def test_the_health_check_does_not_respawn_a_quarantined_server(monkeypatch):
    """S3 — the health-check loop stops re-spawning a quarantined server."""
    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
    )
    pool._running = True
    spawned: list[object] = []

    class _SentinelClient:
        def __init__(self, params):  # pragma: no cover — must never run
            spawned.append(params)

    monkeypatch.setattr(warmup_pool_module, "TaskScopedStdioClient", _SentinelClient)

    for _ in range(3):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_INIT_FAILED
        )

    # A dead connection in the pool -> the health check evicts it and
    # would normally START a replenish. The S3 consult is what stops
    # the loop (the pool would otherwise re-run the failing init chain
    # every 60 s, forever).
    await pool._pools["context7"].put(_dead_connection())
    await pool.health_check()
    assert not pool._replenish_tasks, (
        "the health check must not start a replenish for a quarantined "
        "server — that loop is the forever-retry shape"
    )
    assert spawned == [], (
        "the health check must not re-spawn a quarantined server"
    )


def _dead_connection():
    """A pooled connection whose ping fails (the health check evicts it)."""

    class _FailingSession:
        async def send_ping(self):
            raise RuntimeError("dead")

    class _StreamCM:
        async def __aexit__(self, *args):
            return False

    from daemon.mcp.warmup_pool import PooledConnection

    return PooledConnection(
        session=_FailingSession(),
        stream_cm=_StreamCM(),
        tools=[],
        server_name="context7",
        created_at=0.0,
    )


async def test_the_recovery_probe_fires_through_the_production_gates(monkeypatch):
    """C-1 fix: the recovery probe must fire through the REAL gates.

    The bare ``is_quarantined()`` consults in ``_replenish`` and
    ``health_check`` short-circuited BEFORE the only production
    re-probe caller (``_create_pooled_connection`` → ``claim_attempt``)
    — so the "recovers without a daemon restart" promise was
    unreachable in PRODUCTION. This pin drives the production gates
    themselves (not the init_guard API directly) with a shrunk recovery
    window.

    WHAT THIS PIN DOES NOT COVER: the create path is FAKED here (the
    real SDK handshake is unreachable inside pytest — the root conftest
    mocks the ``mcp`` package), so ``claim_attempt`` itself is NOT
    exercised by this pin. It is covered by the pre-existing
    claim-attempt pin and by the integration leg, which drives the real
    spawn.
    """
    monkeypatch.setattr(init_guard, "INIT_RECOVERY_TIMEOUT_S", 0.05)

    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
    )
    pool._running = True

    attempts: list[str] = []

    async def _fake_create(server_name: str):
        attempts.append(server_name)
        raise RuntimeError("the probe failed again")

    monkeypatch.setattr(pool, "_create_pooled_connection", _fake_create)

    for _ in range(3):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_INIT_FAILED
        )
    assert init_guard.is_quarantined("context7") is True

    # BEFORE the recovery window: the gates must NOT let a probe through.
    await pool._replenish("context7")
    assert attempts == [], (
        "no recovery probe may run before the recovery window elapses"
    )
    await pool.health_check()
    assert not pool._replenish_tasks, (
        "the health check must not start a replenish before the recovery "
        "window elapses"
    )
    await asyncio.sleep(0)
    assert attempts == [], "no probe may have run"

    # AFTER the window: the gates MUST let the probe through.
    await asyncio.sleep(0.1)
    await pool._replenish("context7")
    assert attempts == ["context7"], (
        "the recovery window elapsed — the replenish gate must let the "
        "probe through to the create path"
    )

    # The health check is the OTHER production gate — it must start the
    # replenish once a probe is due.
    pool._replenish_tasks.clear()
    await pool.health_check()
    assert pool._replenish_tasks, (
        "the health check must start the recovery replenish once the "
        "recovery window elapsed"
    )
    for task in list(pool._replenish_tasks):
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    pool._replenish_tasks.clear()


async def test_an_early_raise_in_the_claim_span_is_recorded(monkeypatch):
    """WARNING 2: a raise between the claim and the spawn MUST count.

    The span (the DNS pre-check, the secret-marker resolution, the
    ``StdioServerParameters`` construction) used to escape WITHOUT
    ``record_failure``: an env/marker failure never incremented the
    breaker, so the server never reached the threshold, never
    quarantined, and the 60s health-check retry loop continued
    invisible in ``detail.mcp``.
    """
    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
    )
    pool._running = True

    def _boom(*args, **kwargs):
        raise RuntimeError("the secret-marker envelope failed")

    monkeypatch.setattr(warmup_pool_module, "resolve_secret_markers", _boom)

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await pool._create_pooled_connection("context7")

    entry = init_guard.server_state("context7")
    assert entry is not None
    assert entry.attempts == 3, (
        "an early raise MUST increment the breaker — otherwise this "
        "failure class can never quarantine"
    )
    assert init_guard.is_quarantined("context7") is True, (
        "the marker-failure class MUST be able to reach the threshold"
    )


async def test_an_early_raise_releases_the_recovery_probe_claim(monkeypatch):
    """A failure during a recovery probe must not wedge the recovery.

    The claim is taken BEFORE the spawn; a raise in the span left
    ``probe_claimed`` set, so the breaker's single-probe slot stayed
    taken and the server's recovery was wedged for the REST OF THE
    BOOT — one marker failure during a recovery probe was enough.
    """
    monkeypatch.setattr(init_guard, "INIT_RECOVERY_TIMEOUT_S", 0.05)

    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
    )
    pool._running = True

    def _boom(*args, **kwargs):
        raise RuntimeError("the secret-marker envelope failed")

    monkeypatch.setattr(warmup_pool_module, "resolve_secret_markers", _boom)

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await pool._create_pooled_connection("context7")
    assert init_guard.is_quarantined("context7") is True

    await asyncio.sleep(0.1)
    # The probe is due: the create path claims it, the marker raises.
    with pytest.raises(RuntimeError):
        await pool._create_pooled_connection("context7")

    entry = init_guard.server_state("context7")
    assert entry.probe_claimed is False, (
        "the early raise MUST release the claimed probe — a stuck claim "
        "wedges the server's recovery for the whole boot"
    )


async def test_a_cancellation_in_the_spawn_span_releases_the_claim(monkeypatch):
    """N1: a cancellation inside the SPAWN span must release the claim.

    The spawn span (after the pre-try region) can be cancelled — the
    daemon shutdown cancels the replenish tasks, ``drain()`` runs, the
    watchdog cancels. The spawn's own ``except`` skips
    ``record_failure`` for a cancellation, so the CLAIMED recovery probe
    would stay claimed (``probe_claimed`` set and the breaker
    HALF_OPEN) → ``can_attempt`` reads False for the rest of the boot
    → the server's recovery is wedged until a daemon restart (the exact
    false promise C-1 was fixing, under a narrow path).
    """
    monkeypatch.setattr(init_guard, "INIT_RECOVERY_TIMEOUT_S", 0.05)

    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
    )
    pool._running = True

    class _CancellingClient:
        """A spawn whose ``__aenter__`` is cancelled."""

        def __init__(self, params):
            self.params = params

        async def __aenter__(self):
            raise asyncio.CancelledError()

        async def __aexit__(self, *args):
            return False

        def request_close(self):
            pass

    monkeypatch.setattr(warmup_pool_module, "TaskScopedStdioClient", _CancellingClient)

    for _ in range(3):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_INIT_FAILED
        )
    assert init_guard.is_quarantined("context7") is True

    await asyncio.sleep(0.1)  # the recovery window elapses
    assert await init_guard.can_attempt("context7") is True

    with pytest.raises(asyncio.CancelledError):
        await pool._create_pooled_connection("context7")

    entry = init_guard.server_state("context7")
    assert entry.probe_claimed is False, (
        "the cancellation MUST release the claimed probe — a stuck claim "
        "wedges the server's recovery for the rest of the boot"
    )
    # The recovery is genuinely re-claimable (the breaker's probe slot
    # was released too — the entry flag alone would not be enough).
    assert await init_guard.claim_attempt("context7") is True, (
        "the recovery probe must be claimable again after a cancelled "
        "spawn — otherwise the server stays wedged until a restart"
    )


async def test_the_claim_is_released_before_the_failure_recording(monkeypatch):
    """N2: the load-bearing ORDER (release BEFORE record) is pinned.

    The release must happen BEFORE the recording: a recorder that is
    itself cancelled (or that raises) would otherwise skip the release
    and leave the claim stuck — the server's recovery wedged for the
    boot — with EVERY other pin still green. This pin drives the real
    escape path with a recorder that raises ``CancelledError``.
    """
    monkeypatch.setattr(init_guard, "INIT_RECOVERY_TIMEOUT_S", 0.05)

    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
    )
    pool._running = True

    def _boom(*args, **kwargs):
        raise RuntimeError("the secret-marker envelope failed")

    monkeypatch.setattr(warmup_pool_module, "resolve_secret_markers", _boom)

    async def _cancelled_recorder(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(warmup_pool_module, "record_failure", _cancelled_recorder)

    for _ in range(3):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_INIT_FAILED
        )
    assert init_guard.is_quarantined("context7") is True

    await asyncio.sleep(0.1)  # the recovery window elapses
    assert await init_guard.can_attempt("context7") is True

    with pytest.raises(RuntimeError):
        await pool._create_pooled_connection("context7")

    entry = init_guard.server_state("context7")
    assert entry.probe_claimed is False, (
        "the claim MUST be released BEFORE the recording — a cancelled "
        "recorder must not be able to skip the release"
    )


async def test_the_recovery_probe_brings_the_server_back_without_a_restart(
    monkeypatch,
):
    """The half-open probe after the recovery window."""
    monkeypatch.setattr(init_guard, "INIT_RECOVERY_TIMEOUT_S", 0.05)

    for _ in range(3):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_INIT_FAILED
        )
    assert init_guard.is_quarantined("context7") is True
    assert await init_guard.can_attempt("context7") is False

    import asyncio

    await asyncio.sleep(0.1)
    assert await init_guard.can_attempt("context7") is True, (
        "after the recovery window the half-open probe must be allowed"
    )
    # Exactly ONE probe (the CR-4 single-probe discipline) — the CLAIM
    # is what enforces it (``can_attempt`` is a pure query).
    assert await init_guard.claim_attempt("context7") is True
    assert await init_guard.claim_attempt("context7") is False

    await init_guard.record_success("context7")
    assert init_guard.is_quarantined("context7") is False
    assert init_guard.state_snapshot()["context7"]["state"] == "ok"


async def test_the_reset_method_is_never_called(monkeypatch):
    """S11 — ``CircuitBreaker.reset()`` is the only unsynchronised
    mutator; the guard must never call it."""
    calls: list[int] = []

    def _explode(self):  # pragma: no cover — must never run
        calls.append(1)
        raise AssertionError("CircuitBreaker.reset() must never be called")

    monkeypatch.setattr(CircuitBreaker, "reset", _explode)

    for _ in range(3):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_INIT_FAILED
        )
    init_guard.clear_quarantine("context7")
    await init_guard.record_success("context7")
    assert calls == []


# ── the class routing ──────────────────────────────────────────────────


async def test_dns_nxdomain_is_threshold_one():
    """A definitive answer kills the server immediately."""
    await init_guard.record_failure(
        "context7", init_guard.FAILURE_CLASS_DNS_NXDOMAIN
    )
    assert init_guard.is_quarantined("context7") is True
    entry = init_guard.server_state("context7")
    assert entry.attempts == 1
    assert entry.reason == init_guard.FAILURE_CLASS_DNS_NXDOMAIN


async def test_dns_servfail_counts_toward_the_threshold_of_three():
    """SERVFAIL is a resolution failure — it counts toward the threshold."""
    await init_guard.record_failure(
        "context7", init_guard.FAILURE_CLASS_DNS_SERVFAIL
    )
    assert init_guard.is_quarantined("context7") is False
    await init_guard.record_failure(
        "context7", init_guard.FAILURE_CLASS_DNS_SERVFAIL
    )
    assert init_guard.is_quarantined("context7") is False
    await init_guard.record_failure(
        "context7", init_guard.FAILURE_CLASS_DNS_SERVFAIL
    )
    assert init_guard.is_quarantined("context7") is True


async def test_resolver_timeout_is_never_quarantine_eligible():
    """A resolver hiccup must never permanently disable a server."""
    for _ in range(5):
        await init_guard.record_failure(
            "context7", init_guard.FAILURE_CLASS_RESOLVER_TIMEOUT
        )
    assert init_guard.is_quarantined("context7") is False
    entry = init_guard.server_state("context7")
    assert entry.state == init_guard.STATE_DEGRADED
    assert entry.last_error_class == init_guard.FAILURE_CLASS_RESOLVER_TIMEOUT
    assert entry.breaker_state == "closed", (
        "the resolver timeout must NOT count towards the init-failure "
        "threshold"
    )
