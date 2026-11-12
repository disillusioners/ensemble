"""Mutation-proven pins for port-ownership (component A).

Fix-round 2 additions:
  * B1 — cross-install claim blindness: TWO-PROCESS end-to-end pin.
  * B3 — the daemon never releases the claim while alive.
  * B5 — the refusal marker has a real reader (surfaced once, rotated).
  * B7(ii) — the gate WIRING is pinned (live foreign claim => exit 78;
    the refusal never enters the bind-retry loop).
  * N3 — dev boots never resolve (or write) the canonical LIVE root.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from daemon.services import port_ownership as po
from daemon.services.port_ownership import (
    PortClaim,
    is_live_foreign,
    read_claim,
    try_claim,
    write_refusal_marker,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _self_st():
    return po._read_starttime(os.getpid())


def _plant_claim(claims_dir: Path, port: int, install_dir, pid=None, starttime=None,
                 host="0.0.0.0"):
    claims_dir.mkdir(parents=True, exist_ok=True)
    path = po._claim_path(claims_dir, host, port)
    path.write_text(json.dumps({
        "install_dir": install_dir,
        "pid": os.getpid() if pid is None else pid,
        "starttime": _self_st() if starttime is None else starttime,
        "port": port,
        "host": host,
        "claimed_at": "now",
    }))
    return path


# ---------------------------------------------------------------------------
# E-ii / E-iii / promote-shape (logic level)
# ---------------------------------------------------------------------------


def test_E_ii_refuse_foreign_live(tmp_path):
    claims_dir = tmp_path / "claims"
    _plant_claim(claims_dir, 9797, "foreign-install-root")
    res = try_claim(claims_dir, "0.0.0.0", 9797, tmp_path / "self-install")
    assert res.kind == "refused"
    assert res.holder_pid == os.getpid()
    claim = read_claim(po._claim_path(claims_dir, "0.0.0.0", 9797))
    assert claim is not None and claim.install_dir == "foreign-install-root"


def test_E_ii_M1_same_install_takes(tmp_path):
    claims_dir = tmp_path / "claims"
    self_install = tmp_path / "self-install"
    _plant_claim(claims_dir, 9797, str(self_install))
    res = try_claim(claims_dir, "0.0.0.0", 9797, self_install)
    assert res.kind == "taken"


def test_E_ii_M2_dead_pid_stale_takes(tmp_path):
    claims_dir = tmp_path / "claims"
    _plant_claim(claims_dir, 9797, "foreign-install-root",
                 starttime=(_self_st() or 0) + 9999)
    res = try_claim(claims_dir, "0.0.0.0", 9797, tmp_path / "self-install")
    assert res.kind == "taken"


def test_E_ii_M3_remove_foreign_check_would_refuse_same_install(tmp_path):
    st = _self_st()
    claim = PortClaim(install_dir=str(tmp_path / "self"), pid=os.getpid(),
                      starttime=st, port=9797, host="0.0.0.0", claimed_at="now")
    assert is_live_foreign(claim, tmp_path / "self") is False


def test_E_iii_happy_take_claim(tmp_path):
    claims_dir = tmp_path / "claims"
    res = try_claim(claims_dir, "0.0.0.0", 18790, tmp_path / "self-install")
    assert res.kind == "taken"
    p = po._claim_path(claims_dir, "0.0.0.0", 18790)
    assert p.is_file()
    c = read_claim(p)
    assert c is not None and c.install_dir == str(tmp_path / "self-install")


def test_E_iii_M4_plant_foreign_refuses(tmp_path):
    claims_dir = tmp_path / "claims"
    _plant_claim(claims_dir, 18790, "foreign-install-root")
    res = try_claim(claims_dir, "0.0.0.0", 18790, tmp_path / "self-install")
    assert res.kind == "refused"


def test_E_iii_M5_degrade_claims_dir_create_fails(tmp_path, monkeypatch):
    def boom(self, *a, **kw):
        raise OSError("simulated read-only")

    monkeypatch.setattr(Path, "mkdir", boom)
    res = try_claim(tmp_path / "claims", "0.0.0.0", 18791, tmp_path / "self-install")
    assert res.kind == "degraded"
    assert "create failed" in res.reason


def test_promote_shape_same_install_non_refuse(tmp_path):
    claims_dir = tmp_path / "claims"
    self_install = tmp_path / "self-install"
    _plant_claim(claims_dir, 9797, str(self_install))
    res = try_claim(claims_dir, "0.0.0.0", 9797, self_install)
    assert res.kind == "taken"
    assert not (claims_dir / "port-ownership-refusal.json").exists()


def test_promote_shape_M6_foreignness_flip_would_refuse(tmp_path):
    st = _self_st()
    claim = PortClaim(install_dir=str(tmp_path / "self"), pid=os.getpid(),
                      starttime=st, port=9797, host="0.0.0.0", claimed_at="now")
    assert is_live_foreign(claim, tmp_path / "self") is False


def test_refusal_marker_written_on_refuse(tmp_path):
    data_dir = tmp_path / "claims"
    data_dir.mkdir()
    claim = PortClaim(install_dir="foreign", pid=os.getpid(), starttime=1,
                      port=9797, host="0.0.0.0", claimed_at="now")
    assert write_refusal_marker(data_dir, claim, tmp_path / "self", 9797, "0.0.0.0") is True
    marker = json.loads((data_dir / "port-ownership-refusal.json").read_text())
    assert marker["exit_code"] == 78
    assert marker["holder"]["install_dir"] == "foreign"
    assert "pid" in marker["holder"]
    assert "exe" in marker["holder"]
    assert "cwd" in marker["holder"]


# ---------------------------------------------------------------------------
# Gate behaviour
# ---------------------------------------------------------------------------


def test_gate_refuses_live_foreign_claim_and_writes_marker(tmp_path, monkeypatch, caplog):
    claims_dir = tmp_path / "claims"
    _plant_claim(claims_dir, 18793, "foreign-install-root")
    monkeypatch.setenv("ENSEMBLE_PORT_CLAIMS_DIR", str(claims_dir))
    monkeypatch.setattr(po, "resolve_self_install_dir", lambda: tmp_path / "self-install")
    with caplog.at_level("WARNING", logger="daemon.services.port_ownership"):
        with pytest.raises(SystemExit) as exc:
            po.run_port_ownership_gate("127.0.0.1", 18793)
    assert exc.value.code == 78
    assert "PORT OWNERSHIP REFUSED" in caplog.text
    marker = json.loads((claims_dir / "port-ownership-refusal.json").read_text())
    assert marker["exit_code"] == 78
    assert marker["holder"]["install_dir"] == "foreign-install-root"


def test_gate_refusal_never_enters_bind_retry(monkeypatch):
    """B7(ii): the gate runs BEFORE the bind, and its 78 never reaches the
    bind-retry loop."""
    from types import SimpleNamespace

    import daemon.__main__ as dm

    called = {"gate": False, "bind": False}

    def fake_gate(host, port):
        called["gate"] = True
        raise SystemExit(78)

    def fake_bind(host, port, max_wait_s=None):
        called["bind"] = True
        raise AssertionError("bind must NOT be reached after an ownership refusal")

    monkeypatch.setattr("daemon.services.port_ownership.run_port_ownership_gate", fake_gate)
    monkeypatch.setattr("daemon.services.bind_retry.bind_socket_with_retry", fake_bind)
    monkeypatch.setattr("daemon.logging_setup.install_root_logging", lambda: None)

    cfg = SimpleNamespace(host="127.0.0.1", port=18790, uds=None,
                          should_reload=False, workers=1)
    with pytest.raises(SystemExit) as exc:
        dm._serve_with_bind_retry(cfg, max_wait_s=1)
    assert exc.value.code == 78
    assert called == {"gate": True, "bind": False}


def test_gate_port_zero_refuses_claim_loudly(tmp_path, monkeypatch, caplog):
    """N8: PORT=0 never writes a claim file; the gate says so loudly."""
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    monkeypatch.setenv("ENSEMBLE_PORT_CLAIMS_DIR", str(claims_dir))
    with caplog.at_level("ERROR", logger="daemon.services.port_ownership"):
        po.run_port_ownership_gate("127.0.0.1", 0)  # must return, not exit
    assert "REFUSED TO CLAIM" in caplog.text
    assert list(claims_dir.iterdir()) == []


def test_gate_surfaces_prior_refusal_once(tmp_path, monkeypatch, caplog):
    """B5: a marker left by a prior boot is reported by the NEXT boot."""
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    (claims_dir / "port-ownership-refusal.json").write_text(json.dumps({
        "refused_at": "2026-11-12T06:00:00Z",
        "port": 9797, "host": "0.0.0.0",
        "holder": {"pid": 4242, "install_dir": "/tmp/rogue"},
        "exit_code": 78,
    }))
    monkeypatch.setenv("ENSEMBLE_PORT_CLAIMS_DIR", str(claims_dir))
    monkeypatch.setattr(po, "resolve_self_install_dir", lambda: tmp_path / "self")
    with caplog.at_level("ERROR", logger="daemon.services.port_ownership"):
        po.run_port_ownership_gate("127.0.0.1", 18794)
    assert "PORT-OWNERSHIP REFUSAL FROM A PRIOR BOOT" in caplog.text
    assert list(claims_dir.glob("port-ownership-refusal.json.seen-*"))


# ---------------------------------------------------------------------------
# B1 — TWO-PROCESS cross-install pin (the reviewer's demonstrated blindness)
# ---------------------------------------------------------------------------

_CHILD = textwrap.dedent(
    """
    import pathlib, sys
    from daemon.services.port_ownership import try_claim
    claims_dir = pathlib.Path(sys.argv[1])
    port = int(sys.argv[2])
    res = try_claim(claims_dir, "127.0.0.1", port, pathlib.Path("/tmp/foreign-install"))
    print(res.kind, flush=True)
    sys.stdin.readline()
    """
)


def test_B1_two_process_foreign_claim_is_visible_and_refused(tmp_path, monkeypatch):
    """A claim written by a SECOND PROCESS (different install) is visible to
    the first process and refuses it with 78. This is the exact blindness
    the reviewer demonstrated: with an install-scoped claims dir, process A
    saw nothing."""
    # Resolve the shared dir through XDG_RUNTIME_DIR (NOT the override),
    # so the pin is causal for the host-global resolution itself: an
    # install-scoped claims dir makes the second process's claim invisible.
    monkeypatch.delenv("ENSEMBLE_PORT_CLAIMS_DIR", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    claims_dir = tmp_path / "ensemble-port-claims"
    claims_dir.mkdir()
    port = 18795

    env = dict(os.environ)
    env.pop("ENSEMBLE_PORT_CLAIMS_DIR", None)
    env["XDG_RUNTIME_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(REPO_ROOT)
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(claims_dir), str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        line = child.stdout.readline().strip()
        assert line == "taken", (line, child.stderr.read())

        monkeypatch.setattr(po, "resolve_self_install_dir", lambda: tmp_path / "self-install")

        # Sanity: the second process's claim is READ by this process.
        claim = read_claim(po._claim_path(claims_dir, "127.0.0.1", port))
        assert claim is not None
        assert claim.install_dir == "/tmp/foreign-install"
        assert claim.pid == child.pid

        with pytest.raises(SystemExit) as exc:
            po.run_port_ownership_gate("127.0.0.1", port)
        assert exc.value.code == 78
    finally:
        try:
            child.stdin.write("\n")
            child.stdin.flush()
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            child.kill()
            child.wait(timeout=5)


# ---------------------------------------------------------------------------
# B3 — no eager release while the process is alive
# ---------------------------------------------------------------------------


def test_B3_daemon_never_releases_claim_eagerly():
    src = (REPO_ROOT / "daemon" / "api.py").read_text(encoding="utf-8")
    assert "release_claim(" not in src, (
        "api.py must not release the claim — releasing during lifespan "
        "shutdown runs while the process still owns the listener (B3)"
    )


# ---------------------------------------------------------------------------
# N3 — dev boots never resolve/write the canonical LIVE root
# ---------------------------------------------------------------------------


def test_N3_self_install_dir_never_falls_back_to_live_root(monkeypatch):
    import daemon.tools.upgrade_tools as ut

    monkeypatch.setattr(ut, "_resolve_install_dir", lambda env: None)
    monkeypatch.setattr(ut, "_self_env_marker", lambda: None)
    resolved = po.resolve_self_install_dir()
    assert resolved is not None
    assert resolved == REPO_ROOT
    assert resolved != Path.home() / "agents-ensemble"


def test_N3_gate_never_writes_under_the_install_root(tmp_path, monkeypatch):
    claims_dir = tmp_path / "global-claims"
    monkeypatch.setenv("ENSEMBLE_PORT_CLAIMS_DIR", str(claims_dir))
    fake_install = tmp_path / "fake-install"
    fake_install.mkdir()
    monkeypatch.setattr(po, "resolve_self_install_dir", lambda: fake_install)
    po.run_port_ownership_gate("127.0.0.1", 18796)
    assert (claims_dir / "any-18796.json").is_file()
    assert not (fake_install / "data").exists()
