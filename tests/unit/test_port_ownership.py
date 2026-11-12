"""Unit tests for port_ownership (component A).

Fix-round 2 additions: host-global per-uid claims dir (B1), normalized claim
keying (B1), port validation (N8), marker co-location (N2).
"""

import json
import os
import stat
from pathlib import Path

import pytest

from daemon.services import port_ownership as po
from daemon.services.port_ownership import (
    PortClaim,
    _claim_path,
    _claims_dir_for,
    _normalize_claim_host,
    clear_refusal_marker,
    ensure_claims_dir,
    is_live_foreign,
    pid_alive_with_starttime,
    read_claim,
    release_claim,
    resolve_claims_dir,
    resolve_refusal_marker_path,
    surface_refusal_marker,
    try_claim,
    validate_claim_port,
    write_refusal_marker,
)


def _self_st():
    return po._read_starttime(os.getpid())


# ---------------------------------------------------------------------------
# Liveness / foreign-ness
# ---------------------------------------------------------------------------


def test_pid_alive_with_starttime_self():
    st = _self_st()
    assert st is not None
    assert pid_alive_with_starttime(os.getpid(), st) is True


def test_pid_alive_with_starttime_wrong_starttime():
    st = _self_st()
    assert pid_alive_with_starttime(os.getpid(), (st or 0) + 1) is False


def test_pid_alive_with_starttime_dead_pid():
    assert pid_alive_with_starttime(9999999, 12345) is False


def test_is_live_foreign_none_claim():
    assert is_live_foreign(None, Path("/tmp/x")) is False


def test_is_live_foreign_unresolved_self():
    claim = PortClaim(install_dir="/some/other", pid=os.getpid(), starttime=1,
                      port=9, host="0.0.0.0", claimed_at="now")
    assert is_live_foreign(claim, None) is False


def test_is_live_foreign_same_install():
    st = _self_st()
    claim = PortClaim(install_dir="/tmp/self", pid=os.getpid(), starttime=st,
                      port=9, host="0.0.0.0", claimed_at="now")
    assert is_live_foreign(claim, Path("/tmp/self")) is False


def test_is_live_foreign_foreign_live():
    st = _self_st()
    claim = PortClaim(install_dir="/tmp/foreign-install", pid=os.getpid(),
                      starttime=st, port=9, host="0.0.0.0", claimed_at="now")
    assert is_live_foreign(claim, Path("/tmp/self-install")) is True


# ---------------------------------------------------------------------------
# B1 — host-global per-uid claims dir + normalized keying
# ---------------------------------------------------------------------------


def test_claims_dir_override_wins(tmp_path, monkeypatch):
    override = tmp_path / "override-claims"
    monkeypatch.setenv("ENSEMBLE_PORT_CLAIMS_DIR", str(override))
    assert resolve_claims_dir() == override
    # install identity is irrelevant to the LOCATION (B1)
    assert _claims_dir_for(Path("/tmp/any-install-root")) == override


def test_claims_dir_xdg_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("ENSEMBLE_PORT_CLAIMS_DIR", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert resolve_claims_dir() == tmp_path / "ensemble-port-claims"


def test_claims_dir_fallback_is_per_uid_tmp(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_PORT_CLAIMS_DIR", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    d = resolve_claims_dir()
    assert d == Path(f"/tmp/ensemble-port-claims-{os.getuid()}")


def test_claims_dir_is_host_global_not_install_scoped(tmp_path, monkeypatch):
    """B1: two different install roots resolve the SAME claims dir."""
    monkeypatch.setenv("ENSEMBLE_PORT_CLAIMS_DIR", str(tmp_path / "shared"))
    a = _claims_dir_for(Path("/tmp/install-a"))
    b = _claims_dir_for(Path("/tmp/install-b"))
    assert a == b


def test_ensure_claims_dir_mode_0700(tmp_path):
    d = tmp_path / "claims"
    assert ensure_claims_dir(d) is None
    mode = stat.S_IMODE(d.stat().st_mode)
    assert mode == 0o700, oct(mode)


def test_normalize_claim_host_collapses_wildcard_and_loopback():
    for h in ("0.0.0.0", "::", "[::]", "", "*", "127.0.0.1", "localhost", "::1"):
        assert _normalize_claim_host(h) == "any", h
    assert _normalize_claim_host("10.1.2.3") == "10.1.2.3"
    assert _normalize_claim_host("Daemon.Example.COM") == "daemon.example.com"


def test_claim_key_normalizes_host(tmp_path):
    # A wildcard claimant and a loopback claimant must contend for one key.
    assert _claim_path(tmp_path, "0.0.0.0", 9797) == _claim_path(tmp_path, "127.0.0.1", 9797)
    assert _claim_path(tmp_path, "0.0.0.0", 9797).name == "any-9797.json"


# ---------------------------------------------------------------------------
# N8 — port validation
# ---------------------------------------------------------------------------


def test_validate_claim_port_edges():
    assert validate_claim_port(8079) is None
    assert validate_claim_port(1) is None
    assert validate_claim_port(65535) is None
    assert "port 0" in (validate_claim_port(0) or "")
    assert "range" in (validate_claim_port(65536) or "")
    assert "range" in (validate_claim_port(-1) or "")
    assert validate_claim_port("nope") is not None


def test_try_claim_port_zero_writes_nothing(tmp_path):
    claims_dir = tmp_path / "claims"
    res = try_claim(claims_dir, "127.0.0.1", 0, tmp_path / "self")
    assert res.kind == "invalid"
    assert not claims_dir.exists() or not any(claims_dir.iterdir())


def test_try_claim_port_65535_ok(tmp_path):
    res = try_claim(tmp_path / "claims", "0.0.0.0", 65535, tmp_path / "self")
    assert res.kind == "taken"


# ---------------------------------------------------------------------------
# Claim lifecycle
# ---------------------------------------------------------------------------


def test_try_claim_take_when_absent(tmp_path):
    claims_dir = tmp_path / "claims"
    res = try_claim(claims_dir, "0.0.0.0", 9797, tmp_path / "self-install")
    assert res.kind == "taken"
    p = claims_dir / "any-9797.json"
    assert p.is_file()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    c = read_claim(p)
    assert c is not None and c.pid == os.getpid()
    assert c.uid == os.getuid()


def test_try_claim_refuse_foreign(tmp_path):
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir(parents=True)
    st = _self_st()
    (claims_dir / "any-9797.json").write_text(json.dumps({
        "install_dir": "foreign-install-root",
        "pid": os.getpid(),
        "starttime": st,
        "port": 9797,
        "host": "0.0.0.0",
        "claimed_at": "now",
    }))
    res = try_claim(claims_dir, "0.0.0.0", 9797, tmp_path / "self-install")
    assert res.kind == "refused"


def test_try_claim_same_install_takes(tmp_path):
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir(parents=True)
    st = _self_st()
    self_install = tmp_path / "self-install"
    (claims_dir / "any-9797.json").write_text(json.dumps({
        "install_dir": str(self_install),
        "pid": os.getpid(),
        "starttime": st,
        "port": 9797,
        "host": "0.0.0.0",
        "claimed_at": "now",
    }))
    res = try_claim(claims_dir, "0.0.0.0", 9797, self_install)
    assert res.kind == "taken"


def test_refusal_marker_write_and_clear(tmp_path):
    data_dir = tmp_path / "claims"
    data_dir.mkdir()
    claim = PortClaim(install_dir="foreign", pid=os.getpid(), starttime=1,
                      port=9797, host="0.0.0.0", claimed_at="now")
    assert write_refusal_marker(data_dir, claim, tmp_path / "self", 9797, "0.0.0.0") is True
    assert (data_dir / "port-ownership-refusal.json").is_file()
    assert clear_refusal_marker(data_dir) is True
    assert not (data_dir / "port-ownership-refusal.json").exists()


def test_marker_is_co_located_with_claims_dir(tmp_path, monkeypatch):
    """N2: the marker path is derived from the claims dir, never CWD."""
    monkeypatch.setenv("ENSEMBLE_PORT_CLAIMS_DIR", str(tmp_path / "claims"))
    assert resolve_refusal_marker_path() == tmp_path / "claims" / "port-ownership-refusal.json"


# ---------------------------------------------------------------------------
# B5 — refusal-marker reader (surface once, rotate)
# ---------------------------------------------------------------------------


def test_surface_refusal_marker_reports_once_and_rotates(tmp_path, caplog):
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    marker = claims_dir / "port-ownership-refusal.json"
    marker.write_text(json.dumps({
        "refused_at": "2026-11-12T06:00:00Z",
        "port": 9797,
        "host": "0.0.0.0",
        "holder": {"pid": 4242, "exe": "/tmp/rogue", "cwd": "/tmp", "install_dir": "/tmp/rogue"},
        "self_install_dir": "/home/nea/agents-ensemble",
        "exit_code": 78,
    }))
    with caplog.at_level("ERROR", logger="daemon.services.port_ownership"):
        assert surface_refusal_marker(claims_dir) is True
    assert "PORT-OWNERSHIP REFUSAL FROM A PRIOR BOOT" in caplog.text
    assert "4242" in caplog.text
    assert not marker.exists()
    assert list(claims_dir.glob("port-ownership-refusal.json.seen-*"))
    # exactly once
    assert surface_refusal_marker(claims_dir) is False


def test_surface_refusal_marker_unparseable_is_loud(tmp_path, caplog):
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    (claims_dir / "port-ownership-refusal.json").write_text("<not json>")
    with caplog.at_level("ERROR", logger="daemon.services.port_ownership"):
        assert surface_refusal_marker(claims_dir) is True
    assert "unparseable" in caplog.text


# ---------------------------------------------------------------------------
# release_claim (tests / manual only — B3 removed the daemon call site)
# ---------------------------------------------------------------------------


def test_release_claim_pid_gated(tmp_path):
    claims_dir = tmp_path / "claims"
    res = try_claim(claims_dir, "0.0.0.0", 9797, tmp_path / "self")
    assert res.kind == "taken"
    assert release_claim(claims_dir, "0.0.0.0", 9797, 999999) is False
    assert (claims_dir / "any-9797.json").is_file()
    assert release_claim(claims_dir, "0.0.0.0", 9797, os.getpid()) is True
    assert not (claims_dir / "any-9797.json").exists()


def test_claims_dir_override_lands_in_override_dir(tmp_path, monkeypatch):
    override = tmp_path / "override-claims"
    override.mkdir()
    fake_install = tmp_path / "fake-install-root"
    fake_install.mkdir()
    monkeypatch.setenv("ENSEMBLE_PORT_CLAIMS_DIR", str(override))
    cd = _claims_dir_for(fake_install)
    assert cd == override
    res = try_claim(cd, "0.0.0.0", 12345, fake_install)
    assert res.kind == "taken"
    assert (override / "any-12345.json").is_file()
    assert not (fake_install / "data" / "port-claims").exists()
