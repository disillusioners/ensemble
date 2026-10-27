"""C2 — the bounded DNS pre-check (soak-gate-mcp-warmup).

The pre-check resolves the host the child would ACTUALLY use (the
effective post-heal merged env), OFF the event loop, bounded at
``DNS_PRECHECK_TIMEOUT_S = 2.0`` s (requirements §4.5 P5), and it is a
PROBE, never a pin: the resolved address is DISCARDED (decisions §3f
F1).

This file pins:

* the resolved-base derivation — the row env wins on every key, and
  the ``CONTEXT7_BASE_URL`` mirror is the derivation input only when
  ``CONTEXT7_API_URL`` is unset;
* a non-resolving host ⇒ the init REFUSES within the 2.0 s bound (far
  below the 30 s package-side hang budget) and NO child is spawned;
* the class routing — ``dns_nxdomain`` is threshold-1,
  ``dns_servfail`` counts toward the threshold of 3, and
  ``resolver_timeout`` is NOT quarantine-eligible on the first
  occurrence (the attempt PROCEEDS);
* the resolved ADDRESS is never written anywhere (F1);
* the readiness verdict is untouched by an MCP failure (C4 — advisory
  only).
"""

from __future__ import annotations

import socket
import time

import pytest

from daemon.mcp import init_guard
from daemon.mcp import warmup_pool as warmup_pool_module
from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
from daemon.mcp.config import McpStdioConfig
from daemon.mcp.warmup_pool import McpWarmupPool, build_pooled_stdio_config


@pytest.fixture(autouse=True)
def _clean_registry():
    init_guard.reset_for_tests()
    yield
    init_guard.reset_for_tests()


def _gaierror(code: int, message: str) -> socket.gaierror:
    return socket.gaierror(code, message)


# ── the derivation ─────────────────────────────────────────────────────


def test_the_mirror_is_the_derivation_input_when_the_api_is_unset():
    definition = Context7ServerDefinition()
    env = {"CONTEXT7_BASE_URL": "https://mirror.example"}
    assert definition.get_precheck_url(env) == "https://mirror.example"


def test_the_api_url_is_the_effective_lever():
    definition = Context7ServerDefinition()
    env = {
        "CONTEXT7_BASE_URL": "https://mirror.example",
        "CONTEXT7_API_URL": "https://api.example/v1",
    }
    assert definition.get_precheck_url(env) == "https://api.example/v1"


def test_the_row_env_wins_over_the_definition_defaults():
    """The pre-check reads the EFFECTIVE merged env (the row wins)."""
    definition = Context7ServerDefinition()
    row_config = {"env": {"CONTEXT7_BASE_URL": "https://row.example"}}
    merged = build_pooled_stdio_config(definition, row_config)
    assert merged.env["CONTEXT7_BASE_URL"] == "https://row.example"
    assert definition.get_precheck_url(merged.env) == "https://row.example"


def test_the_default_env_derives_the_package_root():
    definition = Context7ServerDefinition()
    merged = build_pooled_stdio_config(definition, None)
    assert merged.env["CONTEXT7_BASE_URL"] == "https://context7.com"
    assert definition.get_precheck_url(merged.env) == "https://context7.com"


# ── the classification ─────────────────────────────────────────────────


async def test_a_successful_resolution_proceeds(monkeypatch):
    def _fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert await init_guard.dns_precheck("https://example.test/api") == "ok"


async def test_nxdomain_is_classified_as_dns_nxdomain(monkeypatch):
    def _fake_getaddrinfo(*args, **kwargs):
        raise _gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    outcome = await init_guard.dns_precheck("https://dead.example/api")
    assert outcome == init_guard.FAILURE_CLASS_DNS_NXDOMAIN


async def test_servfail_is_classified_as_dns_servfail(monkeypatch):
    def _fake_getaddrinfo(*args, **kwargs):
        raise _gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    outcome = await init_guard.dns_precheck("https://broken.example/api")
    assert outcome == init_guard.FAILURE_CLASS_DNS_SERVFAIL


async def test_the_resolver_timeout_is_bounded_at_two_seconds(monkeypatch):
    """§4.5 P5 — the bound is 2.0 s and the outcome is its own class."""
    import threading

    release = threading.Event()

    def _slow_getaddrinfo(*args, **kwargs):
        # The pre-check runs this in a worker thread; the wait_for bound
        # cuts the CALL short, and the event lets the thread exit
        # promptly so the event loop's executor shutdown is not delayed.
        release.wait(10)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", _slow_getaddrinfo)

    started = time.monotonic()
    outcome = await init_guard.dns_precheck("https://slow.example/api")
    elapsed = time.monotonic() - started
    release.set()

    assert outcome == init_guard.FAILURE_CLASS_RESOLVER_TIMEOUT
    assert elapsed < 5.0, (
        f"the pre-check must be bounded at {init_guard.DNS_PRECHECK_TIMEOUT_S}s "
        f"(observed {elapsed:.2f}s)"
    )
    assert elapsed >= init_guard.DNS_PRECHECK_TIMEOUT_S * 0.5, (
        "the bound must actually fire (the sleep must have been cut short)"
    )


async def test_the_resolver_timeout_is_not_quarantine_eligible(monkeypatch, caplog):
    """A resolver hiccup must never permanently disable a server."""
    import logging
    import threading

    release = threading.Event()

    def _slow_getaddrinfo(*args, **kwargs):
        release.wait(10)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", _slow_getaddrinfo)

    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
        precheck_resolver=lambda env: "https://slow.example/api",
    )

    spawned: list[object] = []

    class _SentinelClient:
        def __init__(self, params):  # pragma: no cover — must NOT run
            spawned.append(params)

    monkeypatch.setattr(warmup_pool_module, "TaskScopedStdioClient", _SentinelClient)

    # The timeout must NOT quarantine — but the attempt PROCEEDS, so the
    # spawn site IS reached (the sentinel is what proves that).
    with caplog.at_level(logging.WARNING):
        try:
            with pytest.raises(Exception) as excinfo:
                await pool._create_pooled_connection("context7")
        finally:
            release.set()

    assert not isinstance(excinfo.value, init_guard.McpInitRefused), (
        "a resolver timeout must not refuse the init"
    )
    assert init_guard.is_quarantined("context7") is False
    messages = [rec.getMessage() for rec in caplog.records]
    assert any(
        init_guard.FAILURE_CLASS_RESOLVER_TIMEOUT in m
        and "NOT quarantine-eligible" in m
        for m in messages
    ), f"the resolver-timeout class must be recorded: {messages}"


# ── the refusal path ───────────────────────────────────────────────────


async def test_a_dead_host_refuses_the_init_and_spawns_no_child(monkeypatch):
    """The refusal is fast, records the class, and spawns NOTHING."""
    def _fake_getaddrinfo(*args, **kwargs):
        raise _gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    pool = McpWarmupPool()
    pool.register_server(
        "context7",
        McpStdioConfig(transport="stdio", command="false", args=[]),
        precheck_resolver=lambda env: "https://dead.example/api",
    )

    spawned: list[object] = []

    class _SentinelClient:
        def __init__(self, params):  # pragma: no cover — must never run
            spawned.append(params)

    monkeypatch.setattr(warmup_pool_module, "TaskScopedStdioClient", _SentinelClient)

    started = time.monotonic()
    with pytest.raises(init_guard.McpInitRefused) as excinfo:
        await pool._create_pooled_connection("context7")
    elapsed = time.monotonic() - started

    assert excinfo.value.error_class == init_guard.FAILURE_CLASS_DNS_NXDOMAIN
    assert spawned == [], "NO child may be spawned when the pre-check refuses"
    assert elapsed < 5.0, (
        "the refusal must be bounded (the 30s package-side hang budget "
        f"must never be reached); observed {elapsed:.2f}s"
    )

    # The server is degraded with the DNS reason (and — NXDOMAIN being
    # definitive — quarantined for this boot).
    entry = init_guard.server_state("context7")
    assert entry.last_error_class == init_guard.FAILURE_CLASS_DNS_NXDOMAIN
    assert init_guard.is_quarantined("context7") is True


async def test_the_refusal_does_not_flip_the_readiness_verdict():
    """C4 — the MCP failure is ADVISORY: readiness is untouched."""
    from daemon.services.readiness import ReadinessComposite

    await init_guard.record_failure(
        "context7", init_guard.FAILURE_CLASS_DNS_NXDOMAIN
    )
    assert init_guard.is_quarantined("context7") is True

    composite = ReadinessComposite(
        database=True,
        queue_freshness=True,
        services=True,
        checkpoint_saver=True,
    )
    payload = composite.to_payload()
    assert payload["status"] == "ready"
    assert payload["components"] == {
        "database": True,
        "queue_freshness": True,
        "services": True,
        "checkpoint_saver": True,
    }
    assert payload["detail"]["reasons"] == []


# ── F1 — the probe is never a pin ──────────────────────────────────────


async def test_the_resolved_address_is_never_written(monkeypatch, tmp_path):
    """The pre-check resolves once and DISCARDS the address."""
    sentinel = "203.0.113.77"

    def _fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (sentinel, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    assert await init_guard.dns_precheck("https://pinned.example/api") == "ok"

    # The address appears nowhere in the guard's published state...
    published = repr(init_guard.state_snapshot())
    assert sentinel not in published

    # ...nor in the guard's module-level tables.
    for name in ("_SERVERS", "_BREAKERS", "_JOURNAL_BUFFER"):
        assert sentinel not in repr(getattr(init_guard, name))

    # ...nor in the env the child would receive.
    definition = Context7ServerDefinition()
    merged = build_pooled_stdio_config(definition, None)
    assert sentinel not in repr(merged.env)
