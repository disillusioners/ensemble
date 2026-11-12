"""Pure-logic unit tests for port_ownership (component A, T4)."""

import os
from pathlib import Path

from daemon.services.port_ownership import (
    PortClaim,
    is_live_foreign,
    pid_alive_with_starttime,
    read_claim,
    resolve_data_dir,
    try_claim,
    write_refusal_marker,
    clear_refusal_marker,
    release_claim,
)


def test_pid_alive_with_starttime_self():
    st = __import__("daemon.services.port_ownership", fromlist=["_read_starttime"])._read_starttime(os.getpid())
    assert st is not None
    assert pid_alive_with_starttime(os.getpid(), st) is True


def test_pid_alive_with_starttime_wrong_starttime():
    st = __import__("daemon.services.port_ownership", fromlist=["_read_starttime"])._read_starttime(os.getpid())
    assert pid_alive_with_starttime(os.getpid(), (st or 0) + 1) is False


def test_pid_alive_with_starttime_dead_pid():
    # A very large pid that (almost certainly) does not exist.
    assert pid_alive_with_starttime(9999999, 12345) is False


def test_is_live_foreign_none_claim():
    assert is_live_foreign(None, Path("/tmp/x")) is False


def test_is_live_foreign_unresolved_self():
    claim = PortClaim(install_dir="/some/other", pid=os.getpid(), starttime=1, port=9, host="0.0.0.0", claimed_at="now")
    # self_install_dir None => non-foreign
    assert is_live_foreign(claim, None) is False


def test_is_live_foreign_same_install():
    st = __import__("daemon.services.port_ownership", fromlist=["_read_starttime"])._read_starttime(os.getpid())
    claim = PortClaim(install_dir="/tmp/self", pid=os.getpid(), starttime=st, port=9, host="0.0.0.0", claimed_at="now")
    assert is_live_foreign(claim, Path("/tmp/self")) is False


def test_is_live_foreign_foreign_live():
    # Use our own pid/starttime so liveness passes, but different install_dir.
    st = __import__("daemon.services.port_ownership", fromlist=["_read_starttime"])._read_starttime(os.getpid())
    claim = PortClaim(install_dir="/tmp/foreign-install", pid=os.getpid(), starttime=st, port=9, host="0.0.0.0", claimed_at="now")
    assert is_live_foreign(claim, Path("/tmp/self-install")) is True


def test_try_claim_take_when_absent(tmp_path):
    claims_dir = tmp_path / "claims"
    res = try_claim(claims_dir, "0.0.0.0", 9797, tmp_path / "self-install")
    assert res.kind == "taken"
    p = claims_dir / "0.0.0.0-9797.json"
    assert p.is_file()
    c = read_claim(p)
    assert c is not None and c.pid == os.getpid()


def test_try_claim_refuse_foreign(tmp_path):
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir(parents=True)
    # plant a foreign claim with a live pid/starttime
    import json
    st = __import__("daemon.services.port_ownership", fromlist=["_read_starttime"])._read_starttime(os.getpid())
    (claims_dir / "0.0.0.0-9797.json").write_text(json.dumps({
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
    import json
    st = __import__("daemon.services.port_ownership", fromlist=["_read_starttime"])._read_starttime(os.getpid())
    (claims_dir / "0.0.0.0-9797.json").write_text(json.dumps({
        "install_dir": str(tmp_path / "self-install"),
        "pid": os.getpid(),
        "starttime": st,
        "port": 9797,
        "host": "0.0.0.0",
        "claimed_at": "now",
    }))
    res = try_claim(claims_dir, "0.0.0.0", 9797, tmp_path / "self-install")
    assert res.kind == "taken"


def test_refusal_marker_write_and_clear(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    claim = PortClaim(install_dir="foreign", pid=os.getpid(), starttime=1, port=9797, host="0.0.0.0", claimed_at="now")
    assert write_refusal_marker(data_dir, claim, tmp_path / "self", 9797, "0.0.0.0") is True
    assert (data_dir / "port-ownership-refusal.json").is_file()
    assert clear_refusal_marker(data_dir) is True
    assert not (data_dir / "port-ownership-refusal.json").exists()


def test_release_claim_pid_gated(tmp_path):
    claims_dir = tmp_path / "claims"
    res = try_claim(claims_dir, "0.0.0.0", 9797, tmp_path / "self")
    assert res.kind == "taken"
    # wrong pid => not released
    assert release_claim(claims_dir, "0.0.0.0", 9797, 999999) is False
    assert (claims_dir / "0.0.0.0-9797.json").is_file()
    # correct pid => released
    assert release_claim(claims_dir, "0.0.0.0", 9797, os.getpid()) is True
    assert not (claims_dir / "0.0.0.0-9797.json").exists()
