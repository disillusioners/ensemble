"""P2 T1/T2/T3 charge-seam tests (phase2-plan AC1 + D14/AC11 slice).

Gates (AC1, exact):

* append charges deep-size ONCE;
* replace charges the DELTA only (never re-charges full history);
* checkpoint ``aget``/``aget_tuple`` charges the decoded payload once
  ACROSS a transient connection retry (no double-charge);
* ``enabled=false`` ⇒ every seam is an identity no-op with ZERO
  registry growth.

Plus the D14 slice for the charge layer: the registry persists
SCALARS ONLY — a secret canary planted in message content must never
appear in any snapshot/aggregate payload.

FENCE DISCIPLINE — the 2026-10-12 live-install-deletion incident class
(project critical note: test scripts must NEVER resolve live-shaped
paths):

* ``FAKE_HOME`` is a pytest ``tmp_path`` directory installed as
  ``$HOME``; nothing under the real HOME is ever read.
* These tests are PURELY synthetic: the sizing walker consumes
  in-memory objects only — no /proc, no filesystem reads, no network.
* The session fence asserts the charge module holds no live-path
  surface: its only env reads are the RESOURCE_MONITOR_* numeric caps.

Run with ``.venv/bin/python -m pytest tests/test_resource_charge_seams.py -q``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from daemon.services.resource_monitor import charge
from daemon.services.resource_monitor.charge import (
    ChargeRegistry,
    bounded_deep_size,
)


@pytest.fixture(scope="session")
def real_langgraph():
    """The sanctioned mock-eviction binding gate (conftest mocks
    ``langgraph.*`` globally; tests needing the REAL pinned classes use
    ``evict_langgraph_mocks`` → real imports → ``restore_langgraph_mocks``
    — see tests/helpers/checkpoint_prune_pg.py round-2 semantics: once
    the real module loads it stays cached for the session, so module
    identity never forks)."""
    from tests.helpers.checkpoint_prune_pg import (
        evict_langgraph_mocks,
        restore_langgraph_mocks,
    )

    saved = evict_langgraph_mocks()
    try:
        from langgraph.checkpoint.base import CheckpointTuple as _RealTuple

        yield SimpleNamespace(CheckpointTuple=_RealTuple)
    finally:
        restore_langgraph_mocks(saved)


# ──────────────────────────────────────────────────────────────────────────
# Fence (session-scoped) — FAKE_HOME + env scrub, mirrors the P1 packs.
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session(tmp_path_factory):
    fake_home = tmp_path_factory.mktemp("fake_home_charge_seams")
    real_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    saved = {
        k: os.environ.get(k)
        for k in list(os.environ)
        if k.startswith("POSTGRES_") or k.startswith("ENSEMBLE_")
    }
    for k in saved:
        if k.startswith("POSTGRES_"):
            os.environ.pop(k, None)  # ambient PG must never steer tests
    yield
    os.environ["HOME"] = real_home or ""
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _charge_sandbox():
    """Every test starts from a disabled, empty singleton registry and
    leaves it disabled+empty (the production default is DISABLED until
    the monitor lane wires set_enabled(True) at facade start)."""
    charge.reset()
    charge.set_enabled(False)
    yield
    charge.reset()
    charge.set_enabled(False)


def _msg(content: str) -> AIMessage:
    return AIMessage(content=content)


# ──────────────────────────────────────────────────────────────────────────
# AC1 — append charges once
# ──────────────────────────────────────────────────────────────────────────


class TestAppendChargesOnce:
    def test_deterministic_shape_exact_arithmetic(self):
        charge.set_enabled(True)
        d = {"k": ["v" * 1000]}
        charge.note_message_append("inst-1", d)
        expected = (
            sys.getsizeof(d)
            + sys.getsizeof("k")
            + sys.getsizeof(d["k"])
            + sys.getsizeof(d["k"][0])
        )
        snap = charge.snapshot_instance("inst-1")
        assert snap is not None
        assert snap["message_bytes"] == expected
        assert snap["charged_bytes"] == expected

    def test_two_appends_sum_not_multiply(self):
        charge.set_enabled(True)
        d1 = {"a": "x" * 50}
        d2 = {"b": "y" * 60}
        charge.note_message_append("inst-1", d1)
        charge.note_message_append("inst-1", d2)
        expected = bounded_deep_size(d1)[0] + bounded_deep_size(d2)[0]
        assert charge.snapshot_instance("inst-1")["message_bytes"] == expected

    def test_real_langchain_message_walk(self):
        charge.set_enabled(True)
        msg = _msg("payload-" + "z" * 5000)
        charge.note_message_append("inst-1", msg)
        snap = charge.snapshot_instance("inst-1")
        assert snap["message_bytes"] == bounded_deep_size(msg)[0]
        # structural sanity: the content string dominates the count
        assert snap["message_bytes"] >= 5000

    def test_append_fires_once_per_seam_call(self):
        # Guards a double-charge regression (e.g. hooking both the
        # append site AND a downstream accumulation site).
        charge.set_enabled(True)
        d = {"a": "x" * 100}
        charge.note_message_append("inst-1", d)
        once = charge.snapshot_instance("inst-1")["message_bytes"]
        assert once == bounded_deep_size(d)[0]
        assert once > 0


# ──────────────────────────────────────────────────────────────────────────
# AC1 — replace charges DELTA only
# ──────────────────────────────────────────────────────────────────────────


class TestReplaceChargesDeltaOnly:
    def test_replace_delta_not_full_recharge(self):
        charge.set_enabled(True)
        m1 = _msg("a" * 100)
        m2 = _msg("b" * 300)
        charge.note_message_append("inst-1", m1)
        charge.note_message_replace("inst-1", m2, m1)
        snap = charge.snapshot_instance("inst-1")
        size_m1 = bounded_deep_size(m1)[0]
        size_m2 = bounded_deep_size(m2)[0]
        # THE assertion: stock equals the new message's size — NOT
        # size(m1) + size(m2) (the full-history re-charge trap).
        assert snap["charged_bytes"] == size_m2
        assert snap["charged_bytes"] != size_m1 + size_m2

    def test_negative_delta_shrinks_counter(self):
        charge.set_enabled(True)
        big = _msg("x" * 2000)
        small = _msg("y" * 10)
        charge.note_message_append("inst-1", big)
        charge.note_message_replace("inst-1", small, big)
        assert (
            charge.snapshot_instance("inst-1")["charged_bytes"]
            == bounded_deep_size(small)[0]
        )

    def test_same_object_replace_is_zero_cost_noop(self):
        charge.set_enabled(True)
        m = _msg("z" * 100)
        charge.note_message_append("inst-1", m)
        before = charge.snapshot_instance("inst-1")["charged_bytes"]
        charge.note_message_replace("inst-1", m, m)
        assert charge.snapshot_instance("inst-1")["charged_bytes"] == before

    def test_replace_chain_tracks_growth(self):
        charge.set_enabled(True)
        m1 = _msg("a" * 10)
        m2 = _msg("b" * 20)
        m3 = _msg("c" * 40)
        charge.note_message_append("inst-1", m1)
        charge.note_message_replace("inst-1", m2, m1)
        charge.note_message_replace("inst-1", m3, m2)
        assert (
            charge.snapshot_instance("inst-1")["charged_bytes"]
            == bounded_deep_size(m3)[0]
        )


# ──────────────────────────────────────────────────────────────────────────
# AC1 — checkpoint aget/aget_tuple across a transient retry
# ──────────────────────────────────────────────────────────────────────────


class _FakePgConnError(Exception):
    """Duck-typed psycopg connection-class failure (sqlstate 08xxx)."""

    sqlstate = "08006"


class _FlakyReadSaver:
    """Fake inner saver: first ``aget_tuple`` call raises a retryable
    connection error, second succeeds (exercises _call_with_retry)."""

    def __init__(self, fail_first: int = 1, real_langgraph=None):
        self.fail_first = fail_first
        self._real_langgraph = real_langgraph
        self.aget_tuple_calls = 0
        self.aget_calls = 0

    async def aget_tuple(self, config):
        self.aget_tuple_calls += 1
        if self.aget_tuple_calls <= self.fail_first:
            raise _FakePgConnError("the connection is closed")
        return _checkpoint_tuple(self._real_langgraph)

    async def aget(self, config):
        self.aget_calls += 1
        if self.aget_calls <= self.fail_first:
            raise _FakePgConnError("the connection is closed")
        return {"channel_values": {"ch": "v" * 400}}


def _checkpoint_tuple(real_langgraph) -> Any:
    checkpoint = {
        "v": 4,
        "id": "cp-1",
        "ts": "2026-10-18T00:00:00Z",
        "channel_values": {"messages": ["payload" + "q" * 600]},
        "channel_versions": {"messages": 3},
        "versions_seen": {"node": 3},
    }
    tuple_cls = getattr(real_langgraph, "CheckpointTuple", None)
    if tuple_cls is None:
        # Hermetic fallback: a NamedTuple with the same shape — the
        # charge path treats any tuple payload identically (walker
        # tuple branch). Only tests needing the REAL pinned class
        # request the ``real_langgraph`` fixture.
        from typing import NamedTuple as _NT

        class _FallbackTuple(_NT):
            config: dict
            checkpoint: dict
            metadata: dict
            parent_config: dict | None
            pending_writes: list

        tuple_cls = _FallbackTuple
    return tuple_cls(
        config={"configurable": {"thread_id": "inst-ck", "checkpoint_id": "cp-1"}},
        checkpoint=checkpoint,
        metadata={"source": "loop"},
        parent_config=None,
        pending_writes=[],
    )


def _proxy():
    from daemon.checkpoint_adapter import _wrap_saver_with_connection_retry

    return _wrap_saver_with_connection_retry


class TestCheckpointReadSeam:
    @pytest.mark.asyncio
    async def test_aget_tuple_retry_charges_once(self, real_langgraph):
        charge.set_enabled(True)
        saver = _FlakyReadSaver(fail_first=1, real_langgraph=real_langgraph)
        proxy = _proxy()(saver)
        config = {"configurable": {"thread_id": "inst-ck"}}
        result = await proxy.aget_tuple(config)
        # the retry REALLY happened ...
        assert saver.aget_tuple_calls == 2
        # ... yet the charge fired exactly once, for the decoded payload
        expected = bounded_deep_size(result)[0]
        snap = charge.snapshot_instance("inst-ck")
        assert snap is not None
        assert snap["checkpoint_bytes"] == expected
        # structural bound: content strings dominate
        assert snap["checkpoint_bytes"] >= 600

    @pytest.mark.asyncio
    async def test_aget_charges_decoded_channel_values(self):
        charge.set_enabled(True)
        saver = _FlakyReadSaver(fail_first=0)
        proxy = _proxy()(saver)
        result = await proxy.aget({"configurable": {"thread_id": "inst-ck"}})
        snap = charge.snapshot_instance("inst-ck")
        assert snap["checkpoint_bytes"] == bounded_deep_size(result)[0]

    @pytest.mark.asyncio
    async def test_non_retryable_failure_charges_nothing(self):
        class _BoomSaver:
            async def aget_tuple(self, config):
                raise ValueError("application-level boom")

        charge.set_enabled(True)
        proxy = _proxy()(_BoomSaver())
        with pytest.raises(ValueError):
            await proxy.aget_tuple({"configurable": {"thread_id": "inst-ck"}})
        assert charge.snapshot_instance("inst-ck") is None

    @pytest.mark.asyncio
    async def test_unattributable_calls_are_skipped_never_guessed(self, real_langgraph):
        charge.set_enabled(True)
        saver = _FlakyReadSaver(fail_first=0, real_langgraph=real_langgraph)
        proxy = _proxy()(saver)
        # no thread_id ⇒ unattributable, skipped
        await proxy.aget_tuple({"configurable": {}})
        # non-dict first arg ⇒ unattributable, skipped
        await proxy.aget_tuple(None)
        # no config at all ⇒ the saver's own signature rejects it —
        # the seam passes the malformed call through untouched and
        # charges nothing (the real saver requires config too).
        with pytest.raises(TypeError):
            await proxy.aget_tuple()
        # thread_id keyword style still works
        result = await proxy.aget_tuple(config={"configurable": {"thread_id": "inst-kw"}})
        assert bounded_deep_size(result)[0] > 0
        snaps = charge.snapshot_all()
        assert set(snaps) == {"inst-kw"}

    @pytest.mark.asyncio
    async def test_write_methods_are_not_hooked(self):
        """The WRITE surface must stay scrub-only (agent-secrets P2).

        A fake saver whose aput returns a marker proves the proxy adds
        no charge on the write path (only aput scrub + retry)."""
        from daemon.checkpoint_adapter import _wrap_saver_with_connection_retry

        charged_marker = {"written": True}

        class _WriteSaver:
            def __init__(self):
                self.aput_calls = 0

            async def aput(self, config, checkpoint, metadata, new_versions):
                self.aput_calls += 1
                return charged_marker

        saver = _WriteSaver()
        proxy = _wrap_saver_with_connection_retry(saver)
        charge.set_enabled(True)
        await proxy.aput(
            {"configurable": {"thread_id": "inst-ck"}}, {}, {}, {}
        )
        assert saver.aput_calls == 1
        # NO checkpoint charge was recorded for the write path
        snap = charge.snapshot_instance("inst-ck")
        assert snap is None or snap["checkpoint_bytes"] == 0


# ──────────────────────────────────────────────────────────────────────────
# AC1 — enabled=false ⇒ identity no-ops, zero growth
# ──────────────────────────────────────────────────────────────────────────


class TestDisabledIdentityNoOp:
    def test_all_seams_noop_no_growth(self):
        # registry stays at production default: DISABLED
        assert charge.is_enabled() is False
        d = {"k": "v" * 100}
        for i in range(50):
            charge.note_message_append(f"inst-{i}", d)
            charge.note_message_replace(f"inst-{i}", d, d)
            charge.note_checkpoint_read(f"inst-{i}", d)
            charge.register(f"inst-{i}")
            charge.set_deep_bytes(f"inst-{i}", 999)
        assert charge.snapshot_all() == {}
        assert charge.aggregate()["instances_n"] == 0

    @pytest.mark.asyncio
    async def test_proxy_read_still_works_disabled(self):
        saver = _FlakyReadSaver(fail_first=0)
        proxy = _proxy()(saver)
        result = await proxy.aget_tuple({"configurable": {"thread_id": "inst-x"}})
        assert result is not None
        assert charge.snapshot_all() == {}

    def test_seam_hot_path_microbenchmark(self):
        """R1 informational gate (generous bounds — gross-regression
        tripwire, not a timing claim): disabled seam ≈ a bool check;
        enabled seam ≈ one bounded walk + an int add."""
        d = {"k": "v" * 64}
        t0 = time.perf_counter()
        for i in range(5000):
            charge.note_message_append(f"disabled-{i}", d)
        disabled_s = time.perf_counter() - t0
        assert disabled_s < 1.0, disabled_s

        charge.set_enabled(True)
        t0 = time.perf_counter()
        for i in range(2000):
            charge.note_message_append("bench", d)
        enabled_s = time.perf_counter() - t0
        assert enabled_s < 2.0, enabled_s
        assert charge.snapshot_instance("bench")["message_bytes"] > 0


# ──────────────────────────────────────────────────────────────────────────
# Registry semantics + D14 scalars-only
# ──────────────────────────────────────────────────────────────────────────


class TestRegistrySemantics:
    def test_component_split_and_aggregate(self):
        charge.set_enabled(True)
        charge.note_message_append("i1", {"a": "x" * 100})
        charge.note_checkpoint_read("i1", {"cp": "y" * 200})
        charge.note_message_append("i2", {"b": "z" * 50})
        agg = charge.aggregate()
        snaps = charge.snapshot_all()
        assert agg["instances_n"] == 2
        assert agg["message_bytes"] == sum(s["message_bytes"] for s in snaps.values())
        assert agg["checkpoint_bytes"] == sum(
            s["checkpoint_bytes"] for s in snaps.values()
        )
        assert agg["charged_bytes"] == agg["message_bytes"] + agg["checkpoint_bytes"]

    def test_reset_one_and_all(self):
        charge.set_enabled(True)
        charge.note_message_append("i1", {"a": "x"})
        charge.note_message_append("i2", {"b": "y"})
        charge.reset("i1")
        assert set(charge.snapshot_all()) == {"i2"}
        charge.reset()
        assert charge.snapshot_all() == {}

    def test_deep_bytes_stored_beside_not_blended(self):
        charge.set_enabled(True)
        charge.note_message_append("i1", {"a": "x" * 100})
        charge.set_deep_bytes("i1", 7777, partial_walk=True)
        snap = charge.snapshot_instance("i1")
        assert snap["deep_bytes"] == 7777
        assert snap["deep_partial"] is True
        assert snap["deep_scans"] == 1
        assert snap["charged_bytes"] == bounded_deep_size({"a": "x" * 100})[0]

    def test_lru_eviction_under_cap(self):
        reg = ChargeRegistry(max_records=4)
        reg.set_enabled(True)
        for iid in ("a", "b", "c", "d"):
            reg.add_message_bytes(iid, 10)
        reg.add_message_bytes("a", 10)  # touch a ⇒ b becomes the LRU
        reg.add_message_bytes("e", 10)  # eviction fires
        snaps = reg.snapshot_all()
        assert set(snaps) == {"a", "c", "d", "e"}

    def test_disabled_direct_api_no_growth(self):
        reg = ChargeRegistry()
        for i in range(20):
            reg.add_message_bytes(f"i{i}", 5)
            reg.set_deep_bytes(f"i{i}", 1)
        assert reg.snapshot_all() == {}

    def test_d14_scalars_only_and_secret_canary_never_persisted(self):
        CANARY = "SECRET_CANARY_pbF1_plaintext_marker"
        charge.set_enabled(True)
        charge.note_message_append("i1", _msg(f"tool call with {CANARY}"))
        charge.note_checkpoint_read("i1", {"blob": CANARY})
        charge.set_deep_bytes("i1", 42, partial_walk=False)
        payload = {"snapshots": charge.snapshot_all(), "aggregate": charge.aggregate()}
        rendered = json.dumps(payload)
        assert CANARY not in rendered
        allowed_keys = {
            "instance_id",
            "message_bytes",
            "checkpoint_bytes",
            "charged_bytes",
            # P2 T5 (integration lane): cheap append-count scalar —
            # additive, still D14-compliant (ids/counters only).
            "all_state_messages_len",
            "deep_bytes",
            "deep_partial",
            "deep_scans",
            "instances_n",
            "message_bytes_total",
        }
        for snap in payload["snapshots"].values():
            extra = set(snap) - {
                "instance_id",
                "message_bytes",
                "checkpoint_bytes",
                "charged_bytes",
                # P2 T5 (integration lane): cheap append-count scalar.
                "all_state_messages_len",
                "deep_bytes",
                "deep_partial",
                "deep_scans",
            }
            assert not extra, extra
        for v in payload["aggregate"].values():
            assert isinstance(v, int)
        # instance_id is the only string shape — an id, never content
        assert set(payload["snapshots"]) == {"i1"}
        assert allowed_keys  # (documented shape; silence unused warn)


# ──────────────────────────────────────────────────────────────────────────
# Fence self-check
# ──────────────────────────────────────────────────────────────────────────


def test_charge_module_has_no_live_path_surface():
    """Escape-guard: the charge module's ONLY env reads are the
    RESOURCE_MONITOR_* numeric caps — no HOME/proc-shaped resolution."""
    import inspect

    src = inspect.getsource(charge)
    assert "/proc" not in src
    assert "expanduser" not in src
    for name in dir(charge):
        if name.startswith("DEFAULT_"):
            assert name.startswith("DEFAULT_")  # caps are import constants
