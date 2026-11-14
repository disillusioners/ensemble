"""ENSEMBLE-32: platform gate tests (boot + registration belt-and-braces)."""

import logging
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from daemon.api import _boot_ram_guard
from daemon.config import RamGuardConfig
from daemon.services.ram_guard import get_ram_guard, set_ram_guard
from daemon.services.ram_guard.guard import RamGuard
from daemon.services.ram_guard.verdict import STATE_REFUSED
from daemon.services.ram_guard.ladder import platform_supported


@pytest.fixture
def _linux(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")


@pytest.fixture
def _darwin(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")


def test_platform_supported_linux():
    assert platform_supported("linux") is True
    assert platform_supported("linux2") is True


def test_platform_supported_darwin():
    assert platform_supported("darwin") is False
    assert platform_supported("win32") is False


def test_boot_inert_on_darwin(_darwin, caplog):
    """_boot_ram_guard under darwin => None guard, None app.state, exactly one OFF line."""
    app = FastAPI()
    app.state.ram_guard = "sentinel"
    cfg = SimpleNamespace(
        ram_guard_derived_switch=True,
        ram_guard=RamGuardConfig(ceiling_mib=3072),
    )
    with caplog.at_level(logging.INFO):
        guard = _boot_ram_guard(app, manager=SimpleNamespace(), config=cfg)
    assert guard is None
    assert app.state.ram_guard is None
    off_lines = [r for r in caplog.records if "[RamGuard] OFF" in r.getMessage()]
    assert len(off_lines) == 1
    # ensure facade cleared
    assert get_ram_guard() is None
    set_ram_guard(None)


def test_boot_registers_on_linux(_linux):
    app = FastAPI()
    cfg = SimpleNamespace(
        ram_guard_derived_switch=True,
        ram_guard=RamGuardConfig(ceiling_mib=3072),
    )
    guard = _boot_ram_guard(app, manager=SimpleNamespace(), config=cfg)
    try:
        assert guard is not None
        assert app.state.ram_guard is guard
        assert get_ram_guard() is guard
    finally:
        if app.state.ram_guard is not None:
            app.state.ram_guard.stop()
            app.state.ram_guard.join(timeout=3.0)
        set_ram_guard(None)


def test_register_refuses_on_darwin_platform():
    """Belt-and-braces: a guard constructed on darwin refuses registration."""
    import tempfile, os
    root = None
    g = RamGuard(
        enabled=True,
        resolved_ceiling=(1024 * 1024, "test"),
        platform="darwin",
    )
    rec = g.register(
        tracking_id="t" * 32,
        instance_id="i-1",
        lane="bash",
        pid=1234,
        pgid=1234,
        start_time_jiffies=999,
    )
    assert rec is not None
    assert rec.state == STATE_REFUSED
    assert rec.reason == "refused:platform-unsupported"
    assert g.counters_snapshot()["ram_kill_refusals_p0"] == 1


def test_register_registers_on_linux_platform():
    import tempfile
    g = RamGuard(
        enabled=True,
        resolved_ceiling=(1024 * 1024, "test"),
        platform="linux",
    )
    rec = g.register(
        tracking_id="t" * 32,
        instance_id="i-1",
        lane="bash",
        pid=1234,
        pgid=1234,
        start_time_jiffies=999,
    )
    assert rec is not None
    assert not (rec.state == STATE_REFUSED and rec.reason == "refused:platform-unsupported")
