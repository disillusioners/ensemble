"""Mutation-proven pins for port-ownership (component A, E-ii / E-iii / promote / degrade)."""

import json
import os
from pathlib import Path

from daemon.services.port_ownership import (
    PortClaim,
    is_live_foreign,
    pid_alive_with_starttime,
    read_claim,
    try_claim,
    write_refusal_marker,
)


def _self_st():
    from daemon.services.port_ownership import _read_starttime

    return _read_starttime(os.getpid())


def test_E_ii_refuse_foreign_live(tmp_path):
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    st = _self_st()
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
    assert res.holder_pid == os.getpid()
    # refusal marker path exercised separately; here assert claim readback foreign
    claim = read_claim(claims_dir / "0.0.0.0-9797.json")
    assert claim is not None and claim.install_dir == "foreign-install-root"


def test_E_ii_M1_same_install_takes(tmp_path):
    # M1: flip planted claim install_dir to self's => no refuse => taken
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    st = _self_st()
    self_install = tmp_path / "self-install"
    (claims_dir / "0.0.0.0-9797.json").write_text(json.dumps({
        "install_dir": str(self_install),
        "pid": os.getpid(),
        "starttime": st,
        "port": 9797,
        "host": "0.0.0.0",
        "claimed_at": "now",
    }))
    res = try_claim(claims_dir, "0.0.0.0", 9797, self_install)
    assert res.kind == "taken"


def test_E_ii_M2_dead_pid_stale_takes(tmp_path):
    # M2: mismatched starttime => stale => taken
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    st = _self_st()
    (claims_dir / "0.0.0.0-9797.json").write_text(json.dumps({
        "install_dir": "foreign-install-root",
        "pid": os.getpid(),
        "starttime": (st or 0) + 9999,
        "port": 9797,
        "host": "0.0.0.0",
        "claimed_at": "now",
    }))
    res = try_claim(claims_dir, "0.0.0.0", 9797, tmp_path / "self-install")
    assert res.kind == "taken"


def test_E_ii_M3_remove_foreign_check_would_refuse_same_install(tmp_path):
    # M3: remove foreign-ness check entirely => same-install holder refuses.
    # We can't mutate code here; pin the contract: is_live_foreign is the
    # gate, and with it present same-install is non-foreign.
    st = _self_st()
    claim = PortClaim(install_dir=str(tmp_path / "self"), pid=os.getpid(),
                      starttime=st, port=9797, host="0.0.0.0", claimed_at="now")
    assert is_live_foreign(claim, tmp_path / "self") is False


def test_E_iii_happy_take_claim(tmp_path):
    claims_dir = tmp_path / "claims"
    res = try_claim(claims_dir, "0.0.0.0", 18790, tmp_path / "self-install")
    assert res.kind == "taken"
    p = claims_dir / "0.0.0.0-18790.json"
    assert p.is_file()
    c = read_claim(p)
    assert c is not None and c.install_dir == str(tmp_path / "self-install")


def test_E_iii_M4_plant_foreign_refuses(tmp_path):
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    st = _self_st()
    (claims_dir / "0.0.0.0-18790.json").write_text(json.dumps({
        "install_dir": "foreign-install-root",
        "pid": os.getpid(),
        "starttime": st,
        "port": 18790,
        "host": "0.0.0.0",
        "claimed_at": "now",
    }))
    res = try_claim(claims_dir, "0.0.0.0", 18790, tmp_path / "self-install")
    assert res.kind == "refused"


def test_E_iii_M5_degrade_claims_dir_create_fails(tmp_path, monkeypatch):
    # Force the claims_dir mkdir to fail => try_claim degrades, never raises.
    from pathlib import Path as _P

    def boom(self, *a, **kw):
        raise OSError("simulated read-only")

    monkeypatch.setattr(_P, "mkdir", boom)
    claims_dir = tmp_path / "claims"
    res = try_claim(claims_dir, "0.0.0.0", 18791, tmp_path / "self-install")
    assert res.kind == "degraded"
    assert "create failed" in res.reason


def test_promote_shape_same_install_non_refuse(tmp_path):
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    st = _self_st()
    self_install = tmp_path / "self-install"
    (claims_dir / "0.0.0.0-9797.json").write_text(json.dumps({
        "install_dir": str(self_install),
        "pid": os.getpid(),
        "starttime": st,
        "port": 9797,
        "host": "0.0.0.0",
        "claimed_at": "now",
    }))
    res = try_claim(claims_dir, "0.0.0.0", 9797, self_install)
    assert res.kind == "taken"
    # no refusal marker
    assert not (tmp_path / "data" / "port-ownership-refusal.json").exists()


def test_promote_shape_M6_foreignness_flip_would_refuse(tmp_path):
    # Pin: is_live_foreign returns False for same-install; flipping it to
    # treat same-install as foreign would make try_claim refuse. Document
    # by asserting the current non-foreign verdict.
    st = _self_st()
    claim = PortClaim(install_dir=str(tmp_path / "self"), pid=os.getpid(),
                      starttime=st, port=9797, host="0.0.0.0", claimed_at="now")
    assert is_live_foreign(claim, tmp_path / "self") is False


def test_refusal_marker_written_on_refuse(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    claim = PortClaim(install_dir="foreign", pid=os.getpid(), starttime=1,
                      port=9797, host="0.0.0.0", claimed_at="now")
    assert write_refusal_marker(data_dir, claim, tmp_path / "self", 9797, "0.0.0.0") is True
    import json
    marker = json.loads((data_dir / "port-ownership-refusal.json").read_text())
    assert marker["exit_code"] == 78
    assert marker["holder"]["install_dir"] == "foreign"
    assert "pid" in marker["holder"]
    assert "exe" in marker["holder"]
    assert "cwd" in marker["holder"]
