"""Read-cache substrate behavior tests — read-cache PR-1 (plan §4).

PR-1 ships the cache INERT (``CHECKPOINT_READ_CACHE_ENABLED`` default
OFF). These tests pin:

  * OFF (default) = zero observable behavior change: every read takes
    the miss path (the wrapped saver sees EVERY read), and the miss
    counter emits (the observable-off contract, plan §4.5);
  * ON = consult-before-fetch semantics: miss → put → hit with NO
    second saver call; per-hit shallow copies (no-mutation invariant);
    aget serves the checkpoint projection; ``cache_bypass=True`` skips
    ONLY the cache (and counts neither hit nor miss);
  * invalidation completeness: every intercepted write (async hot
    path, non-retry async writes, sync twins) invalidates BEFORE
    delegating and leaves ``cache.get(thread) is None`` afterwards;
    the four raw-SQL DELETE sites invalidate through the shared
    helper;
  * bounds: LRU cap 32 + 64 MB byte budget, oldest-first eviction;
  * the readiness sentinel writes to the dedicated sentinel thread and
    reads with ``cache_bypass=True`` (plan §4.3.D).

Mutation-proof red/green pairs for each invariant were executed
working-tree-only during development (never committed) — see the PR-1
report.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from tests.helpers.checkpoint_prune_pg import (
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)


@pytest.fixture(autouse=True)
def _real_langgraph():
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


@pytest.fixture(autouse=True)
def _cache_env_clean(monkeypatch):
    """Kill-switch hygiene: default OFF, explicit per test otherwise."""
    monkeypatch.delenv("CHECKPOINT_READ_CACHE_ENABLED", raising=False)
    yield
    monkeypatch.delenv("CHECKPOINT_READ_CACHE_ENABLED", raising=False)


@pytest.fixture(autouse=True)
def _reset_metrics():
    from daemon.checkpoint_metrics import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


def _read_counters() -> tuple[int, int]:
    from daemon.checkpoint_metrics import (
        ensemble_checkpoint_cache_hit_total as hit,
        ensemble_checkpoint_cache_miss_total as miss,
    )

    def _value(metric: Any) -> int:
        # No-label counters keep their count in ``_series[()]``.
        return int(metric._series.get((), {}).get("value", 0))

    return _value(hit), _value(miss)


def _make_tuple(config: dict, marker: Any = 1):
    from langgraph.checkpoint.base import CheckpointTuple

    return CheckpointTuple(
        config=config,
        checkpoint={
            "v": marker,
            "channel_values": {"messages": [f"m{marker}a", f"m{marker}b"]},
        },
        metadata={"step": marker},
        parent_config=None,
        pending_writes=[("task-1", "chan", marker)],
    )


class _CountingSaver:
    """A wrapped-saver double: counts every method the proxy forwards.

    When ``events`` is provided, writes append ``write:<op>`` to it —
    shared with the invalidation spy so tests can assert ORDER
    (invalidation before delegation).
    """

    def __init__(self, events: list | None = None) -> None:
        self.reads: list[tuple[str, dict]] = []
        self.writes: list[tuple[str, tuple, dict]] = []
        self._events = events

    def _log_write(self, op: str) -> None:
        if self._events is not None:
            self._events.append(f"write:{op}")

    async def aget(self, config, **kwargs):
        self.reads.append(("aget", dict(kwargs)))
        return _make_tuple(config).checkpoint

    async def aget_tuple(self, config, **kwargs):
        self.reads.append(("aget_tuple", dict(kwargs)))
        return _make_tuple(config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        self._log_write("aput")
        self.writes.append(("aput", (config, checkpoint, metadata, new_versions), {}))
        return config

    async def aput_writes(self, config, writes, task_id):
        self._log_write("aput_writes")
        self.writes.append(("aput_writes", (config, writes, task_id), {}))
        return None

    async def adelete_thread(self, thread_id):
        self._log_write("adelete_thread")
        self.writes.append(("adelete_thread", (thread_id,), {}))
        return None

    async def acopy_thread(self, *args, **kwargs):
        self._log_write("acopy_thread")
        self.writes.append(("acopy_thread", args, dict(kwargs)))
        return None

    async def adelete_for_runs(self, config, run_ids):
        self._log_write("adelete_for_runs")
        self.writes.append(("adelete_for_runs", (config, run_ids), {}))
        return None

    async def aprune(self, *args, **kwargs):
        self._log_write("aprune")
        self.writes.append(("aprune", args, dict(kwargs)))
        return None

    def put(self, config, checkpoint, metadata, new_versions):
        self._log_write("put")
        self.writes.append(("put", (config, checkpoint, metadata, new_versions), {}))
        return config

    def put_writes(self, config, writes, task_id):
        self._log_write("put_writes")
        self.writes.append(("put_writes", (config, writes, task_id), {}))
        return None

    def delete_thread(self, thread_id):
        self._log_write("delete_thread")
        self.writes.append(("delete_thread", (thread_id,), {}))
        return None

    def copy_thread(self, *args, **kwargs):
        self._log_write("copy_thread")
        self.writes.append(("copy_thread", args, dict(kwargs)))
        return None

    def delete_for_runs(self, config, run_ids):
        self._log_write("delete_for_runs")
        self.writes.append(("delete_for_runs", (config, run_ids), {}))
        return None

    def prune(self, *args, **kwargs):
        self._log_write("prune")
        self.writes.append(("prune", args, dict(kwargs)))
        return None

    def get_next_version(self, current=None, channel=None):
        return "0001.0001"


def _config(thread_id: str = "thread-1", ns: str = "") -> dict:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}


def _make_proxy(events: list | None = None):
    import daemon.checkpoint_adapter as ca

    saver = _CountingSaver(events)
    proxy = ca._wrap_saver_with_connection_retry(saver)
    return proxy, saver


# ── OFF (default): zero observable behavior change ──────────────────────────


class TestKillSwitchOff:
    def test_off_every_read_takes_the_miss_path(self):
        proxy, saver = _make_proxy()
        cfg = _config()
        r1 = asyncio.run(proxy.aget_tuple(cfg))
        r2 = asyncio.run(proxy.aget_tuple(dict(cfg)))
        assert r1 == r2
        # Both reads reached the wrapped saver — identical to today.
        assert [op for op, _kw in saver.reads] == ["aget_tuple", "aget_tuple"]
        hit, miss = _read_counters()
        assert hit == 0
        assert miss == 2  # observable-off: every read counts as a miss

    def test_off_aget_reads_forward_unchanged(self):
        proxy, saver = _make_proxy()
        result = asyncio.run(proxy.aget(_config()))
        assert result == {"v": 1, "channel_values": {"messages": ["m1a", "m1b"]}}
        assert [op for op, _kw in saver.reads] == ["aget"]
        _, miss = _read_counters()
        assert miss == 1

    def test_off_bypass_read_counts_neither(self):
        proxy, saver = _make_proxy()
        asyncio.run(proxy.aget_tuple(_config(), cache_bypass=True))
        hit, miss = _read_counters()
        assert hit == 0 and miss == 0  # bypass reads are non-participants
        assert len(saver.reads) == 1

    def test_bypass_kwarg_never_leaks_to_the_wrapped_saver_when_off(self):
        proxy, saver = _make_proxy()
        # If the kwarg leaked, _CountingSaver.aget_tuple(**kwargs) would
        # still record it — assert it did NOT receive it.
        asyncio.run(proxy.aget_tuple(_config(), cache_bypass=True))
        assert saver.reads[0][1] == {}

    def test_off_invalidation_hooks_still_fire_as_no_ops(self):
        proxy, saver = _make_proxy()
        cfg = _config()
        asyncio.run(proxy.aput(cfg, {"v": 2}, {}, {}))
        assert [w[0] for w in saver.writes] == ["aput"]
        assert len(proxy._read_cache) == 0  # nothing was ever cached


# ── ON: consult-before-fetch semantics ──────────────────────────────────────


class TestCacheOn:
    def test_miss_then_hit_no_second_saver_call(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        cfg = _config()
        first = asyncio.run(proxy.aget_tuple(cfg))
        second = asyncio.run(proxy.aget_tuple(dict(cfg)))  # fresh dict, same key
        assert [op for op, _kw in saver.reads] == ["aget_tuple"]
        assert second == first
        assert second is not first  # per-hit shallow copy
        hit, miss = _read_counters()
        assert (hit, miss) == (1, 1)

    def test_hit_object_is_a_shallow_copy_not_the_cached_artifact(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        cfg = _config()
        original = asyncio.run(proxy.aget_tuple(cfg))
        hit1 = asyncio.run(proxy.aget_tuple(dict(cfg)))
        hit2 = asyncio.run(proxy.aget_tuple(dict(cfg)))
        # Distinct container objects on every serve…
        assert hit1 is not hit2
        assert hit1 is not original
        # …but the SAME shared message objects (plan §4.2 copy policy).
        assert (
            hit1.checkpoint["channel_values"]["messages"][0]
            is hit2.checkpoint["channel_values"]["messages"][0]
            is original.checkpoint["channel_values"]["messages"][0]
        )

    def test_no_mutation_invariant_consumer_edits_do_not_poison_cache(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        cfg = _config()
        first = asyncio.run(proxy.aget_tuple(cfg))
        # A consumer that (against the audit) mutates the returned
        # CONTAINERS must not poison the cached artifact: containers
        # are per-serve copies…
        first.checkpoint["channel_values"]["messages"].append("ROGUE")
        first.checkpoint["v"] = 999
        second = asyncio.run(proxy.aget_tuple(dict(cfg)))
        assert "ROGUE" not in second.checkpoint["channel_values"]["messages"]
        assert second.checkpoint["v"] == 1
        # …only the shared MESSAGE OBJECTS are shared by design.
        assert (
            second.checkpoint["channel_values"]["messages"][0]
            is first.checkpoint["channel_values"]["messages"][0]
        )

    def test_aget_hit_serves_checkpoint_projection(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        cfg = _config()
        asyncio.run(proxy.aget_tuple(cfg))  # populate
        n_reads = len(saver.reads)
        projected = asyncio.run(proxy.aget(dict(cfg)))
        assert len(saver.reads) == n_reads  # served from cache, no saver call
        assert projected == {"v": 1, "channel_values": {"messages": ["m1a", "m1b"]}}
        hit, miss = _read_counters()
        assert hit == 1 and miss == 1

    def test_aget_miss_forwards_and_does_not_populate(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        cfg = _config()
        asyncio.run(proxy.aget(cfg))
        asyncio.run(proxy.aget(dict(cfg)))
        # aget keeps TODAY's exact delegation (K-guard); only aget_tuple
        # populates — both aget reads hit the saver.
        assert [op for op, _kw in saver.reads] == ["aget", "aget"]

    def test_bypass_skips_only_the_cache_layer(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        cfg = _config()
        asyncio.run(proxy.aget_tuple(cfg))  # populate (counted miss)
        n_reads = len(saver.reads)
        bypassed = asyncio.run(proxy.aget_tuple(dict(cfg), cache_bypass=True))
        assert len(saver.reads) == n_reads + 1  # exercised the REAL path
        assert bypassed.checkpoint["v"] == 1
        # The cache entry is untouched by the bypass read.
        assert proxy._read_cache.get("thread-1", "") is not None
        hit, miss = _read_counters()
        assert hit == 0 and miss == 1  # only the populate miss counted

    def test_unattributable_reads_bypass_the_cache(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        asyncio.run(proxy.aget_tuple({"configurable": {}}))  # no thread_id
        assert [op for op, _kw in saver.reads] == ["aget_tuple"]
        assert len(proxy._read_cache) == 0
        _, miss = _read_counters()
        assert miss == 1


# ── Version-scoped reads (fix pass F1) ──────────────────────────────────────


class _VersionedSaver:
    """Honors ``checkpoint_id`` pinning like the real saver's SELECT."""

    def __init__(self) -> None:
        self.versions = {
            "v1": _make_tuple(_config("thread-1"), marker=1),
            "v2": _make_tuple(_config("thread-1"), marker=2),
        }
        self.latest = "v1"
        self.reads: list[str | None] = []

    async def aget_tuple(self, config, **kwargs):
        cid = config.get("configurable", {}).get("checkpoint_id")
        self.reads.append(cid)
        return self.versions[cid] if cid else self.versions[self.latest]

    async def aput(self, config, checkpoint, metadata, new_versions):
        return config


class TestVersionScopedReads:
    """A pinned read must never consult or populate the latest-key cache."""

    def test_extract_cache_key_rejects_truthy_checkpoint_id(self):
        from daemon.checkpoint_read_cache import extract_cache_key

        base = {"configurable": {"thread_id": "t", "checkpoint_ns": ""}}
        assert extract_cache_key((dict(base),), {}) == ("t", "")
        falsy = {"configurable": {"thread_id": "t", "checkpoint_ns": "", "checkpoint_id": None}}
        assert extract_cache_key((falsy,), {}) == ("t", "")
        empty = {"configurable": {"thread_id": "t", "checkpoint_ns": "", "checkpoint_id": ""}}
        assert extract_cache_key((empty,), {}) == ("t", "")
        pinned = {"configurable": {"thread_id": "t", "checkpoint_ns": "", "checkpoint_id": "v1"}}
        assert extract_cache_key((pinned,), {}) is None

    def test_pinned_read_bypass_write_latest_read_correct(self, monkeypatch):
        """The F1 hole end-to-end: pinned read → (no populate) → write →
        latest read serves the LATEST version, never the pinned one."""
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        import daemon.checkpoint_adapter as ca

        saver = _VersionedSaver()
        proxy = ca._wrap_saver_with_connection_retry(saver)
        cfg = _config("thread-1")

        # 1) A latest-semantics read populates the cache with v1 (the
        #    current latest) — the only legal populate.
        first = asyncio.run(proxy.aget_tuple(dict(cfg)))
        assert first.checkpoint["v"] == 1
        assert len(proxy._read_cache) == 1

        # 2) A VERSION-SCOPED read (checkpoint_id pinned) must hit the
        #    REAL saver and return the pinned version — it must not be
        #    served the cached latest, and must not re-populate.
        pinned_cfg = {
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "",
                "checkpoint_id": "v1",
            }
        }
        pinned = asyncio.run(proxy.aget_tuple(dict(pinned_cfg)))
        assert pinned.checkpoint["v"] == 1
        assert len(saver.reads) == 2  # the pinned read did NOT hit the cache
        assert len(proxy._read_cache) == 1  # and did NOT populate a new entry

        # 3) Advance latest to v2 via a WRITE (invalidates thread-1).
        saver.latest = "v2"
        asyncio.run(proxy.aput(dict(cfg), {"v": 2}, {}, {}))
        assert len(proxy._read_cache) == 0

        # 4) Another pinned read (still v1). WITHOUT the F1 guard this
        #    would (wrongly) populate the thread key with the v1 tuple;
        #    the subsequent latest read would then HIT and serve v1.
        asyncio.run(proxy.aget_tuple(dict(pinned_cfg)))
        assert len(proxy._read_cache) == 0, (
            "a version-scoped read populated the latest-semantics cache key"
        )

        # 5) The latest read returns v2 — from the SAVER (a miss), or
        #    at never from a poisoned cache entry.
        latest = asyncio.run(proxy.aget_tuple(dict(cfg)))
        assert latest.checkpoint["v"] == 2
        hit, miss = _read_counters()
        # Reads: populate(miss) + pinned×2(misses — version-scoped reads
        # are never cache-servable) + latest(miss). Zero hits — the
        # pinned reads never polluted, the latest read re-fetched.
        assert hit == 0
        assert miss == 4

    def test_pinned_read_is_not_served_a_cached_latest(self, monkeypatch):
        """The consult half of F1: a pinned read must not be HANDED the
        cached latest-semantics tuple even when one is present."""
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        import daemon.checkpoint_adapter as ca

        saver = _VersionedSaver()
        saver.latest = "v2"
        proxy = ca._wrap_saver_with_connection_retry(saver)
        cfg = _config("thread-1")
        asyncio.run(proxy.aget_tuple(dict(cfg)))  # cache now holds v2
        pinned_cfg = {
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "",
                "checkpoint_id": "v1",
            }
        }
        pinned = asyncio.run(proxy.aget_tuple(dict(pinned_cfg)))
        assert pinned.checkpoint["v"] == 1  # the PINNED version, not cached v2
        assert len(saver.reads) == 2  # consulted the real saver


# ── Invalidation completeness (plan §4.3) ────────────────────────────────────


class TestWriteInvalidation:
    @pytest.mark.parametrize(
        "drive",
        [
            pytest.param(
                lambda proxy, cfg: asyncio.run(
                    proxy.aput(cfg, {"v": 2}, {}, {})
                ),
                id="aput",
            ),
            pytest.param(
                lambda proxy, cfg: asyncio.run(
                    proxy.aput_writes(cfg, [("t", "c", 1)], "task-1")
                ),
                id="aput_writes",
            ),
            pytest.param(
                lambda proxy, cfg: asyncio.run(proxy.adelete_thread("thread-1")),
                id="adelete_thread",
            ),
            pytest.param(
                lambda proxy, cfg: asyncio.run(
                    proxy.acopy_thread(cfg, {"configurable": {"thread_id": "thread-2"}})
                ),
                id="acopy_thread",
            ),
            pytest.param(
                lambda proxy, cfg: asyncio.run(
                    proxy.adelete_for_runs(cfg, ("run-1",))
                ),
                id="adelete_for_runs",
            ),
            pytest.param(
                lambda proxy, cfg: asyncio.run(proxy.aprune()),
                id="aprune",
            ),
            pytest.param(
                lambda proxy, cfg: proxy.put(cfg, {"v": 2}, {}, {}),
                id="put",
            ),
            pytest.param(
                lambda proxy, cfg: proxy.put_writes(cfg, [("t", "c", 1)], "task-1"),
                id="put_writes",
            ),
            pytest.param(
                lambda proxy, cfg: proxy.delete_thread("thread-1"),
                id="delete_thread",
            ),
            pytest.param(
                lambda proxy, cfg: proxy.copy_thread(cfg, "thread-2"),
                id="copy_thread",
            ),
            pytest.param(
                lambda proxy, cfg: proxy.delete_for_runs(cfg, ("run-1",)),
                id="delete_for_runs",
            ),
            pytest.param(
                lambda proxy, cfg: proxy.prune(),
                id="prune",
            ),
        ],
    )
    def test_every_write_forwarder_invalidates_before_delegating(
        self, monkeypatch, drive
    ):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        from daemon.checkpoint_read_cache import CheckpointReadCache

        events: list[str] = []
        proxy, saver = _make_proxy(events)
        cfg = _config()
        # Populate the target thread AND a bystander.
        asyncio.run(proxy.aget_tuple(cfg))
        asyncio.run(proxy.aget_tuple(_config("bystander")))

        # Class-level spies (the cache uses __slots__, so instance
        # shadowing is impossible — monkeypatch the CLASS and delegate
        # to the real implementation).
        real_invalidate = CheckpointReadCache.invalidate
        real_clear = CheckpointReadCache.clear

        def _spy_invalidate(self, thread_id: str) -> None:
            events.append(f"invalidate:{thread_id}")
            real_invalidate(self, thread_id)

        def _spy_clear(self) -> None:
            events.append("clear")
            real_clear(self)

        monkeypatch.setattr(CheckpointReadCache, "invalidate", _spy_invalidate)
        monkeypatch.setattr(CheckpointReadCache, "clear", _spy_clear)

        first_event_before = len(events)
        drive(proxy, cfg)
        new_events = events[first_event_before:]

        # The invalidation fired BEFORE the write was delegated…
        assert new_events, "no invalidation hook fired for the write"
        first_inv = next(
            (i for i, e in enumerate(new_events) if e.startswith(("invalidate:", "clear"))),
            None,
        )
        first_write = next(
            (i for i, e in enumerate(new_events) if e.startswith("write:")), None
        )
        assert first_inv is not None, "no invalidation event"
        assert first_write is not None, "write never reached the wrapped saver"
        assert first_inv < first_write, (
            f"invalidation must precede delegation; events={new_events}"
        )
        # …and the written thread left the cache (runtime self-check).
        assert not proxy._read_cache.thread_cached("thread-1")

    def test_aput_invalidates_only_the_written_thread(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        asyncio.run(proxy.aget_tuple(_config()))
        asyncio.run(proxy.aget_tuple(_config("bystander")))
        asyncio.run(proxy.aput(_config(), {"v": 2}, {}, {}))
        assert not proxy._read_cache.thread_cached("thread-1")
        assert proxy._read_cache.thread_cached("bystander")  # untouched

    def test_unattributable_write_clears_everything(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        asyncio.run(proxy.aget_tuple(_config()))
        asyncio.run(proxy.aget_tuple(_config("bystander")))
        # aprune() carries no thread attribution at all.
        asyncio.run(proxy.aprune())
        assert len(proxy._read_cache) == 0

    def test_namespace_scoped_invalidation_drops_all_namespaces(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        asyncio.run(proxy.aget_tuple(_config("thread-1", ns="")))
        asyncio.run(proxy.aget_tuple(_config("thread-1", ns="sub")))
        assert len(proxy._read_cache) == 2
        asyncio.run(proxy.aput(_config("thread-1"), {"v": 2}, {}, {}))
        assert not proxy._read_cache.thread_cached("thread-1")


# ── The four raw-SQL DELETE sites (plan §4.3.B) ─────────────────────────────


class _FakeConn:
    # CLASS-level fetch state: each chunk acquires a FRESH connection
    # from the pool, so per-instance counters would never observe the
    # second (terminating, empty) chunk — the pair loop would spin.
    _fetch_calls = 0

    def __init__(self) -> None:
        pass

    async def execute(self, sql: str, *args: Any) -> str:
        return "DELETE 3"

    async def fetchrow(self, sql: str, *args: Any):
        return {"cnt": 1, "bytes": 10}

    async def fetch(self, sql: str, *args: Any):
        _FakeConn._fetch_calls += 1
        # First chunk returns one row, then the natural empty chunk ends
        # the pair loop.
        if _FakeConn._fetch_calls == 1:
            return [{"n": 128}]
        return []

    def transaction(self, isolation: str = "serializable"):
        import contextlib

        @contextlib.asynccontextmanager
        async def _tx():
            yield

        return _tx()


class _FakePool:
    def acquire(self):
        import contextlib

        @contextlib.asynccontextmanager
        async def _acq():
            yield _FakeConn()

        return _acq()


def _make_pg_adapter():
    import daemon.checkpoint_adapter as ca

    saver = _CountingSaver()
    adapter = ca.PostgresCheckpointerAdapter(saver, _FakePool())
    return adapter, adapter.raw_saver  # raw_saver IS the proxy


class TestRawSqlDeleteSiteInvalidation:
    def _populate(self, proxy, thread_id: str = "thread-1"):
        asyncio.run(proxy.aget_tuple(_config(thread_id)))

    def test_delete_checkpoints_excluding_invalidates(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        adapter, proxy = _make_pg_adapter()
        self._populate(proxy)
        assert proxy._read_cache.thread_cached("thread-1")
        deleted = asyncio.run(
            adapter.delete_checkpoints_excluding("thread-1", "", {"keep-me"})
        )
        assert deleted == 3
        assert not proxy._read_cache.thread_cached("thread-1")

    def test_delete_writes_excluding_invalidates(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        adapter, proxy = _make_pg_adapter()
        self._populate(proxy)
        deleted = asyncio.run(
            adapter.delete_writes_excluding("thread-1", "", {"keep-me"})
        )
        assert deleted == 3
        assert not proxy._read_cache.thread_cached("thread-1")

    def test_adapter_adelete_thread_invalidates(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        adapter, proxy = _make_pg_adapter()
        self._populate(proxy)
        asyncio.run(adapter.adelete_thread("thread-1"))
        assert not proxy._read_cache.thread_cached("thread-1")

    def test_delete_blobs_anti_join_invalidates(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        adapter, proxy = _make_pg_adapter()
        self._populate(proxy)
        deleted, freed = asyncio.run(adapter.delete_blobs_anti_join("thread-1", ""))
        assert (deleted, freed) == (1, 128)
        assert not proxy._read_cache.thread_cached("thread-1")

    def test_hooks_are_no_ops_while_the_kill_switch_is_off(self, monkeypatch):
        adapter, proxy = _make_pg_adapter()
        # OFF: nothing is ever cached; the raw-SQL hooks fire as no-ops.
        deleted = asyncio.run(
            adapter.delete_checkpoints_excluding("thread-1", "", {"keep-me"})
        )
        assert deleted == 3
        assert len(proxy._read_cache) == 0


# ── Bounds (plan §4.7) ───────────────────────────────────────────────────────


class TestBounds:
    def test_lru_cap_32_entries(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        for i in range(40):
            asyncio.run(proxy.aget_tuple(_config(f"thread-{i}")))
        assert len(proxy._read_cache) == 32
        # Oldest evicted, newest retained.
        assert not proxy._read_cache.thread_cached("thread-0")
        assert proxy._read_cache.thread_cached("thread-39")

    def test_lru_recency_refresh_moves_entry_to_newest(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        asyncio.run(proxy.aget_tuple(_config("t-old")))
        for i in range(35):
            asyncio.run(proxy.aget_tuple(_config(f"t-{i}")))
        assert len(proxy._read_cache) == 32
        assert not proxy._read_cache.thread_cached("t-old")
        # Repopulate t-old then bump recency with a hit; after enough
        # NEW puts, the refreshed entry survives past non-refreshed peers.
        asyncio.run(proxy.aget_tuple(_config("t-old")))
        asyncio.run(proxy.aget_tuple(dict(_config("t-old"))))  # hit → recency bump
        for i in range(31):
            asyncio.run(proxy.aget_tuple(_config(f"t-new-{i}")))
        assert proxy._read_cache.thread_cached("t-old")

    def test_byte_budget_evicts_oldest_first(self):
        from daemon import checkpoint_read_cache as crc

        cache = crc.CheckpointReadCache()
        # Entry budget: 30 MB each against a 64 MB budget — the third
        # put pushes 90 MB > 64 MB ⇒ the OLDEST (t0) is evicted first
        # (cap 32 never reached).
        cache.put("t0", "", object(), size=30 * 1024 * 1024)
        cache.put("t1", "", object(), size=30 * 1024 * 1024)
        assert cache.total_bytes == 60 * 1024 * 1024
        cache.put("t2", "", object(), size=30 * 1024 * 1024)
        assert cache.total_bytes == 60 * 1024 * 1024  # t0 evicted
        assert len(cache) == 2
        assert cache.get("t0", "") is None
        assert cache.get("t1", "") is not None
        assert cache.get("t2", "") is not None
        cache.put("t3", "", object(), size=30 * 1024 * 1024)
        assert cache.get("t1", "") is None  # next-oldest evicted
        assert cache.get("t3", "") is not None  # newest survives

    def test_oversized_single_entry_stays_cached(self):
        from daemon import checkpoint_read_cache as crc

        cache = crc.CheckpointReadCache()
        cache.put("huge", "", object(), size=200 * 1024 * 1024)
        assert cache.get("huge", "") is not None  # never evict the just-put entry

    def test_eviction_accounting_never_drifts_negative(self):
        from daemon import checkpoint_read_cache as crc

        cache = crc.CheckpointReadCache()
        for i in range(50):
            cache.put(f"t{i}", "", object(), size=1024)
        cache.invalidate("t10")
        cache.invalidate("t10")  # double invalidate is a no-op
        assert cache.total_bytes >= 0
        assert len(cache) == 32


# ── Counters + charge semantics (plan §4.4) ─────────────────────────────────


class TestCountersAndCharge:
    def test_counters_render_canonical_and_alias(self, monkeypatch):
        from daemon.checkpoint_metrics import (
            ensemble_checkpoint_cache_hit_total as hit,
            render_metrics,
        )

        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        proxy, saver = _make_proxy()
        asyncio.run(proxy.aget_tuple(_config()))
        asyncio.run(proxy.aget_tuple(dict(_config())))
        rendered = render_metrics()
        assert "ensemble_checkpoint_cache_hit_total 1" in rendered
        assert "ensemble_checkpoint_cache_miss_total 1" in rendered
        assert "checkpoint_cache_hit_total 1" in rendered  # alias, same series
        assert "checkpoint_cache_miss_total 1" in rendered

    def test_reset_for_tests_covers_the_cache_counters(self):
        from daemon.checkpoint_metrics import (
            ensemble_checkpoint_cache_hit_total as hit,
            ensemble_checkpoint_cache_miss_total as miss,
            render_metrics,
            reset_for_tests,
        )

        hit.inc()
        miss.inc()
        reset_for_tests()
        assert "ensemble_checkpoint_cache_hit_total 0" in render_metrics()
        assert "ensemble_checkpoint_cache_miss_total 0" in render_metrics()

    def test_hit_is_still_charged_never_silently_uncharged(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_READ_CACHE_ENABLED", "1")
        import daemon.checkpoint_adapter as ca

        charged: list[tuple[str, int]] = []
        monkeypatch.setattr(
            ca, "note_checkpoint_read",
            lambda instance_id, result, size=None: charged.append((instance_id, size)),
        )
        monkeypatch.setattr(ca, "_charge_enabled", lambda: True)
        proxy, saver = _make_proxy()
        cfg = _config()
        miss_result = asyncio.run(proxy.aget_tuple(cfg))
        from daemon.services.resource_monitor.charge import bounded_deep_size

        miss_size = bounded_deep_size(miss_result)[0]
        hit_result = asyncio.run(proxy.aget_tuple(dict(cfg)))
        # BOTH reads charged — the hit with the stored size (no second
        # walk; same byte count a real read would have charged).
        assert [i for i, _s in charged] == ["thread-1", "thread-1"]
        assert charged[0][1] == miss_size
        assert charged[1][1] == miss_size  # stored size == walked size


# ── Readiness sentinel (plan §4.3.D + §4.1 bypass) ───────────────────────────


class TestReadinessSentinel:
    def _run_probe(self):
        """Drive one full sentinel cycle; returns the fake saver."""
        from daemon.services.readiness import make_checkpoint_saver_probe

        class _PoolishConn:
            get_stats = staticmethod(lambda: {})
            min_size = 2

        class _ProbeSaver:
            def __init__(self) -> None:
                self.conn = _PoolishConn()
                self.aput_writes_calls: list[tuple] = []
                self.aget_tuple_calls: list[tuple] = []

            async def aput_writes(self, config, writes, task_id, *a, **kw):
                self.aput_writes_calls.append((config, writes, task_id))

            async def aget_tuple(self, config, **kwargs):
                self.aget_tuple_calls.append((config, dict(kwargs)))
                return None  # healthy no-row answer

        class _Adapter:
            def __init__(self, saver) -> None:
                self.raw_saver = saver

        saver = _ProbeSaver()

        async def _drive():
            # The probe factory captures the RUNNING loop — construct
            # it inside the loop, then run the sync probe from a worker
            # thread (exactly the orchestrator's to_thread shape).
            probe = make_checkpoint_saver_probe(_Adapter(saver))
            return await asyncio.to_thread(probe)

        outcome = asyncio.run(_drive())
        assert outcome == (True, None)
        return saver

    def test_sentinel_write_targets_the_dedicated_sentinel_thread(self):
        from daemon.constants import CHECKPOINT_SENTINEL_THREAD_ID

        saver = self._run_probe()
        assert saver.aput_writes_calls, "sentinel never wrote"
        config = saver.aput_writes_calls[0][0]
        assert (
            config["configurable"]["thread_id"] == CHECKPOINT_SENTINEL_THREAD_ID
        ), "sentinel write must never churn a real-instance cache key"

    def test_sentinel_read_passes_cache_bypass(self):
        saver = self._run_probe()
        assert saver.aget_tuple_calls, "sentinel never read"
        for _config_recorded, kwargs in saver.aget_tuple_calls:
            assert kwargs.get("cache_bypass") is True, (
                "the readiness sentinel must exercise the REAL path "
                "(retry + lock + charge), skipping only the cache layer"
            )
