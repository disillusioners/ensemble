"""O3 instance-graph cache bounding — TTL knob + LRU max-entries cap.

resource-cost-control commission, item O3 (2026-10-18). Companions the
pre-existing ``tests/unit/test_paused_instance_ttl.py`` (which pins the
TTL-sweep semantics) with coverage for:

  * the two managed knobs (``limits.instance_cache_ttl_hours`` default
    narrowed 24h → 6h, ``limits.instance_cache_max_entries`` default 64)
    and their 4-tier resolution ladder (DB > env > yaml > default),
  * the effective-value read off ``config.limits`` (with the module
    default as fallback),
  * the config-resolved TTL sweeping a non-active cached graph,
  * the LRU max-entries cap: least-recently-used-first eviction, recency
    = ACCESS not creation, active-instance protection, and the
    unsatisfiable-cap (active-alone-exceeds-cap) log path.

Fence discipline (house critical note — zero tolerance): the
boot-apply test uses a ``tmp_path`` SQLite file (never a HOME-derived or
install-dir path); the cap/TTL unit tests use in-memory fakes and touch
no filesystem at all.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.manager import (
    INSTANCE_CACHE_MAX_ENTRIES,
    INSTANCE_CACHE_TTL_HOURS,
    InstanceManager,
    _effective_instance_cache_max_entries,
    _effective_instance_cache_ttl_hours,
)

TTL_KEY = "limits.instance_cache_ttl_hours"
CAP_KEY = "limits.instance_cache_max_entries"


class _FakeManager:
    """Minimal manager exposing the REAL cache methods (bound as class
    attributes) so the methods' internal ``self._x()`` dispatch resolves
    to production code — a ``MagicMock`` would auto-stub the inner calls
    and mask the logic under test.
    """

    _touch_cached_instance = InstanceManager._touch_cached_instance
    _terminal_cached_instance_ids = InstanceManager._terminal_cached_instance_ids
    _enforce_instance_cache_cap = InstanceManager._enforce_instance_cache_cap
    _release_cached_instance = InstanceManager._release_cached_instance
    _cleanup_cached_instances = InstanceManager._cleanup_cached_instances

    def __init__(self, *, max_entries: int = 64, ttl_hours: int = 6,
                 cached=(), terminal=()) -> None:
        self.instances = {
            iid: (MagicMock(name=f"graph-{iid}"), "/agents/test") for iid in cached
        }
        self.config = SimpleNamespace(
            limits=SimpleNamespace(
                instance_cache_max_entries=max_entries,
                instance_cache_ttl_hours=ttl_hours,
            )
        )
        self._request_registry = MagicMock()
        self._request_registry.cancel_by_instance = MagicMock(return_value=0)
        self._instance_repository = MagicMock()
        # Faithful batched lookup: terminal ⊆ requested ids.
        self._instance_repository.list_paused_or_terminal_instance_ids = MagicMock(
            side_effect=lambda ids: set(terminal) & set(ids)
        )
        # Sweep TTL pass: no rows unless a test overrides it.
        self._instance_repository.list = MagicMock(return_value=([], 0, False))
        self._cleanup_instance_state = MagicMock(return_value={})
        self._cleanup_stale_injections = MagicMock(return_value=0)
        self._shutting_down = False


# ────────────────────────────────────────────────────────────────────────────
# Defaults + registry mirror
# ────────────────────────────────────────────────────────────────────────────


def test_default_values_pinned():
    """Leader-ruled defaults: TTL 6h (was 24h), cap 64 entries."""
    assert INSTANCE_CACHE_TTL_HOURS == 6
    assert INSTANCE_CACHE_MAX_ENTRIES == 64


def test_registry_defaults_mirror_module_constants():
    """The managed-key registry MUST mirror the module defaults — the
    registry is the resolver's truth (they move together)."""
    from daemon.services.settings_service import MANAGED_KEYS

    ttl = MANAGED_KEYS[TTL_KEY]
    cap = MANAGED_KEYS[CAP_KEY]
    assert ttl.default == INSTANCE_CACHE_TTL_HOURS == 6
    assert cap.default == INSTANCE_CACHE_MAX_ENTRIES == 64
    assert ttl.config_path == TTL_KEY
    assert cap.config_path == CAP_KEY
    assert ttl.env_var == "LIMITS_INSTANCE_CACHE_TTL_HOURS"
    assert cap.env_var == "LIMITS_INSTANCE_CACHE_MAX_ENTRIES"
    assert ttl.value_type == "int" and cap.value_type == "int"
    assert ttl.requires_restart is True and cap.requires_restart is True
    assert ttl.sensitive is False and cap.sensitive is False


# ────────────────────────────────────────────────────────────────────────────
# Effective value read (off config.limits, module-default fallback)
# ────────────────────────────────────────────────────────────────────────────


class TestEffectiveResolution:
    def test_reads_configured_values(self):
        m = _FakeManager(ttl_hours=3, max_entries=7, cached=[])
        assert _effective_instance_cache_ttl_hours(m) == 3
        assert _effective_instance_cache_max_entries(m) == 7

    def test_falls_back_to_module_default_on_stub_config(self):
        # spec'd MagicMock: no real ``.config`` attribute → default tier.
        m = MagicMock(spec=InstanceManager)
        assert _effective_instance_cache_ttl_hours(m) == INSTANCE_CACHE_TTL_HOURS
        assert _effective_instance_cache_max_entries(m) == INSTANCE_CACHE_MAX_ENTRIES


# ────────────────────────────────────────────────────────────────────────────
# 4-tier resolution ladder for the two knobs
# ────────────────────────────────────────────────────────────────────────────


class TestResolverLadder:
    def test_default_tier(self, monkeypatch):
        from daemon.services.settings_service import resolve

        monkeypatch.delenv("LIMITS_INSTANCE_CACHE_TTL_HOURS", raising=False)
        r = resolve(TTL_KEY, {})
        assert r["value"] == 6 and r["source"] == "default"

    def test_env_beats_default(self, monkeypatch):
        from daemon.services.settings_service import resolve

        monkeypatch.setenv("LIMITS_INSTANCE_CACHE_MAX_ENTRIES", "8")
        r = resolve(CAP_KEY, {})
        assert r["value"] == 8 and r["source"] == "env"

    def test_yaml_literal_beats_default(self, monkeypatch):
        from daemon.services.settings_service import resolve

        monkeypatch.delenv("LIMITS_INSTANCE_CACHE_TTL_HOURS", raising=False)
        snap = {TTL_KEY: {"default": 6, "env_value": None, "yaml_value": 3}}
        r = resolve(TTL_KEY, snap)
        assert r["value"] == 3 and r["source"] == "yaml"

    def test_db_beats_env(self, monkeypatch):
        from daemon.services.settings_service import resolve

        monkeypatch.setenv("LIMITS_INSTANCE_CACHE_TTL_HOURS", "5")
        r = resolve(TTL_KEY, {}, db_overrides={TTL_KEY: 1})
        assert r["value"] == 1 and r["source"] == "db"


def test_boot_apply_mutates_config_and_publishes_resolution(tmp_path, monkeypatch):
    """The boot apply (DB > env > yaml > default) mutates the Config
    fields the manager reads AND publishes the per-key resolution record
    consumed by the manager's startup log line."""
    from daemon.config import Config
    from daemon.services import settings_service

    monkeypatch.setenv("LIMITS_INSTANCE_CACHE_TTL_HOURS", "2")
    monkeypatch.delenv("LIMITS_INSTANCE_CACHE_MAX_ENTRIES", raising=False)
    monkeypatch.setattr(settings_service, "_last_applied_resolution", None)

    cfg = Config(limits={}, persistence={"db_path": str(tmp_path / "boot.db")})
    returned, _snapshot = settings_service.apply_db_settings_overrides(
        cfg, SimpleNamespace(is_postgres=False), raw_yaml_limits=None
    )

    # env tier (2) wins the TTL; the cap stays on the default (64).
    assert returned.limits.instance_cache_ttl_hours == 2
    assert returned.limits.instance_cache_max_entries == 64
    # Effective helpers read the SAME mutated config (via a manager-like
    # object exposing ``.config``).
    _mgr = SimpleNamespace(config=returned)
    assert _effective_instance_cache_ttl_hours(_mgr) == 2
    assert _effective_instance_cache_max_entries(_mgr) == 64

    rec = settings_service.get_last_applied_resolution()
    assert rec is not None
    assert rec[TTL_KEY]["source"] == "env"
    assert rec[CAP_KEY]["source"] == "default"


# ────────────────────────────────────────────────────────────────────────────
# Config-resolved TTL sweep
# ────────────────────────────────────────────────────────────────────────────


def _iso_ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )


class TestTtlSweep:
    @pytest.mark.asyncio
    async def test_configured_short_ttl_evicts_non_active_entry(self):
        """TTL=0 (configured) → a non-active cached graph is released."""
        m = _FakeManager(ttl_hours=0, cached=["a"], terminal=["a"])
        inst = MagicMock(instance_id="a", updated_at=_iso_ago(seconds=30), paused_at=None)
        m._instance_repository.list = MagicMock(return_value=([inst], 1, False))

        async def _break(*_a, **_k):
            m._shutting_down = True

        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=_break):
            await m._cleanup_cached_instances()

        assert "a" not in m.instances

    @pytest.mark.asyncio
    async def test_configured_ttl_keeps_recent_entry(self):
        """TTL=1h → the same 30s-old entry is KEPT (proves the configured
        value drives eviction, not a hard-coded constant)."""
        m = _FakeManager(ttl_hours=1, cached=["b"], terminal=["b"])
        inst = MagicMock(instance_id="b", updated_at=_iso_ago(seconds=30), paused_at=None)
        m._instance_repository.list = MagicMock(return_value=([inst], 1, False))

        async def _break(*_a, **_k):
            m._shutting_down = True

        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=_break):
            await m._cleanup_cached_instances()

        assert "b" in m.instances


# ────────────────────────────────────────────────────────────────────────────
# LRU max-entries cap
# ────────────────────────────────────────────────────────────────────────────


class TestLruCap:
    def test_evicts_least_recently_used_first(self):
        m = _FakeManager(max_entries=2, cached=["a", "b", "c"], terminal=["a", "b", "c"])
        evicted = m._enforce_instance_cache_cap()
        assert evicted == 1
        assert set(m.instances) == {"b", "c"}  # "a" (oldest) went first

    def test_recency_is_access_not_creation(self):
        m = _FakeManager(max_entries=2, cached=["a", "b", "c"], terminal=["a", "b", "c"])
        # Reuse "a" → most-recently-used → "b" becomes the LRU victim.
        m._touch_cached_instance("a")
        assert list(m.instances) == ["b", "c", "a"]
        evicted = m._enforce_instance_cache_cap()
        assert evicted == 1
        assert set(m.instances) == {"c", "a"}  # "b" evicted, "a" saved by access

    def test_active_entry_survives_cap_pressure(self):
        # "live" is active (absent from the terminal set) → never evicted.
        m = _FakeManager(max_entries=2, cached=["t1", "live", "t2"], terminal=["t1", "t2"])
        evicted = m._enforce_instance_cache_cap()
        assert evicted == 1
        assert "live" in m.instances
        assert set(m.instances) == {"live", "t2"}  # LRU terminal "t1" evicted

    def test_active_alone_exceeds_cap_evicts_nothing(self, caplog):
        m = _FakeManager(max_entries=1, cached=["live1", "live2"], terminal=[])
        with caplog.at_level(logging.WARNING, logger="daemon.manager"):
            evicted = m._enforce_instance_cache_cap()
        assert evicted == 0
        assert set(m.instances) == {"live1", "live2"}
        assert any("unsatisfiable" in r.getMessage() for r in caplog.records)

    def test_no_eviction_at_or_below_cap(self):
        m = _FakeManager(max_entries=3, cached=["a", "b"], terminal=["a", "b"])
        assert m._enforce_instance_cache_cap() == 0
        assert set(m.instances) == {"a", "b"}

    def test_touch_uncached_id_is_noop(self):
        m = _FakeManager(max_entries=2, cached=["a"], terminal=["a"])
        m._touch_cached_instance("not-cached")  # must not raise / mutate
        assert set(m.instances) == {"a"}

    def test_status_lookup_failure_is_fail_safe(self, caplog):
        """A repo failure treats every cached entry as ACTIVE → evict
        nothing (never drop a live entry on incomplete information)."""
        m = _FakeManager(max_entries=1, cached=["a", "b"], terminal=["a", "b"])
        m._instance_repository.list_paused_or_terminal_instance_ids = MagicMock(
            side_effect=RuntimeError("db down")
        )
        with caplog.at_level(logging.WARNING, logger="daemon.manager"):
            evicted = m._enforce_instance_cache_cap()
        assert evicted == 0
        assert set(m.instances) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_sweep_enforces_cap_after_ttl_pass(self):
        m = _FakeManager(max_entries=2, cached=["a", "b", "c"], terminal=["a", "b", "c"])

        async def _break(*_a, **_k):
            m._shutting_down = True

        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=_break):
            await m._cleanup_cached_instances()

        assert set(m.instances) == {"b", "c"}
