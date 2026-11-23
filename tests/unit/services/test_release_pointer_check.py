"""Unit tests for daemon/services/release_pointer_check.py (relcur-pointer M2).

Pins the boot-time release-pointer invariant check contract:
  check_release_pointer(install_dir) -> dict with
    ok / reasons / alias_present / alias_dangling / alias_matches_journal
  plus journal_current, top_target, alias_target, top_matches_journal.

Fixtures:
  - matched: pointers agree with journal -> ok, no drift reasons
  - mismatched alias -> drift detected
  - dangling alias -> drift detected
  - absent alias with journal current -> drift detected
  - journal null -> nothing-to-check, NOT a warning (ok=True)
Mutation guard: gutting the comparison must turn these RED.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.services.release_pointer_check import (
    DRIFT_TOKEN,
    check_release_pointer,
    run_boot_check,
    last_boot_check,
)


def _make_install(root: Path, *, journal_current: str | None, alias_target: str | None,
                  top_target: str | None, make_alias_dir: bool = True,
                  make_top_dir: bool = True,
                  in_flight_target: str | None = None,
                  pending_op_target: str | None = None) -> Path:
    """Build a fake install dir. alias_target/top_target are raw readlink values
    (e.g. 'v0.31.42' / 'releases/v0.31.42') or None for absent."""
    install = root / "install"
    install.mkdir()
    (install / "releases").mkdir()
    # journal
    import json
    if journal_current is None:
        (install / "releases" / "state.json").write_text("{}", encoding="utf-8")
    else:
        payload = {"current": journal_current, "previous": None}
        if in_flight_target is not None:
            payload["in_flight"] = {"target": in_flight_target}
        if pending_op_target is not None:
            payload["pending_op"] = {"target": pending_op_target}
        (install / "releases" / "state.json").write_text(
            json.dumps(payload), encoding="utf-8",
        )
    # release dirs for targets that should resolve
    names = {"v0.31.42", "v0.31.37", "vOLD"}
    for cand in (journal_current, alias_target, top_target, in_flight_target, pending_op_target):
        if cand:
            nm = str(cand).rsplit("/", 1)[-1]
            if "DOES_NOT_EXIST" not in nm and "MISSING" not in nm:
                names.add(nm)
    for name in names:
        d = install / "releases" / name
        d.mkdir(exist_ok=True)
    # alias symlink
    alias_path = install / "releases" / "current"
    if alias_target is not None:
        alias_path.symlink_to(alias_target)
    # top-level current
    top_path = install / "current"
    if top_target is not None:
        top_path.symlink_to(top_target)
    return install


@pytest.fixture
def install_factory(tmp_path):
    def factory(**kw):
        return _make_install(tmp_path, **kw)
    return factory


def test_matched_ok(install_factory):
    install = install_factory(
        journal_current="v0.31.42",
        alias_target="v0.31.42",
        top_target="releases/v0.31.42",
    )
    r = check_release_pointer(install)
    assert r["ok"] is True
    assert r["alias_present"] is True
    assert r["alias_dangling"] is False
    assert r["alias_matches_journal"] is True
    assert r["top_matches_journal"] is True
    # no drift (warning) reasons
    assert not any(not x.startswith("nothing-to-check") for x in r["reasons"])


def test_mismatched_alias_drift(install_factory):
    install = install_factory(
        journal_current="v0.31.42",
        alias_target="v0.31.37",
        top_target="releases/v0.31.42",
    )
    r = check_release_pointer(install)
    assert r["ok"] is False
    assert r["alias_present"] is True
    assert r["alias_matches_journal"] is False
    assert any("alias-mismatch" in x for x in r["reasons"])


def test_dangling_alias_drift(install_factory):
    install = install_factory(
        journal_current="v0.31.42",
        alias_target="vDOES_NOT_EXIST",
        top_target="releases/v0.31.42",
    )
    r = check_release_pointer(install)
    assert r["ok"] is False
    assert r["alias_present"] is True
    assert r["alias_dangling"] is True
    assert any("alias-dangling" in x for x in r["reasons"])


def test_absent_alias_with_journal_current_drift(install_factory):
    install = install_factory(
        journal_current="v0.31.42",
        alias_target=None,
        top_target="releases/v0.31.42",
    )
    r = check_release_pointer(install)
    assert r["ok"] is False
    assert r["alias_present"] is False
    assert any("alias-absent" in x for x in r["reasons"])


def test_journal_null_no_warning(install_factory):
    install = install_factory(
        journal_current=None,
        alias_target="v0.31.42",
        top_target="releases/v0.31.42",
    )
    r = check_release_pointer(install)
    # nothing-to-check -> not a warning -> ok True
    assert r["ok"] is True
    assert any(x.startswith("nothing-to-check") for x in r["reasons"])
    assert not any(not x.startswith("nothing-to-check") for x in r["reasons"])


def test_top_mismatch_drift(install_factory):
    install = install_factory(
        journal_current="v0.31.42",
        alias_target="v0.31.42",
        top_target="releases/v0.31.37",
    )
    r = check_release_pointer(install)
    assert r["ok"] is False
    assert r["top_matches_journal"] is False
    assert any("top-mismatch" in x for x in r["reasons"])


def test_none_install_dir_no_crash():
    r = check_release_pointer(None)
    # no install dir -> nothing-to-check
    assert r["ok"] is True
    assert r["alias_present"] is False


def test_ceremony_window_info(install_factory, caplog):
    import logging
    # Mid-ceremony: alias already at the new version (vB) while journal/top
    # still at the old (vA); in_flight target == vB. The alias mismatch's
    # "new side" (vB) equals the ceremony target -> renders ceremony-window
    # INFO, NO release-pointer-drift token, ceremony_in_progress=True.
    install = install_factory(
        journal_current="vA",
        alias_target="vB",
        top_target="releases/vA",
        in_flight_target="vB",
    )
    r = check_release_pointer(install)
    assert r["ceremony_in_progress"] is True
    assert r["ok"] is True
    assert any(str(x).startswith("ceremony-window:") for x in r["reasons"])
    with caplog.at_level(logging.WARNING):
        run_boot_check(install)
    assert not any("release-pointer-drift" in rec.message for rec in caplog.records)


def test_third_version_mismatch_warning(install_factory, caplog):
    import logging
    # Ceremony in progress (in_flight target vB, alias at vB) but the top
    # pointer names a THIRD version (vC) -> real drift: WARNING + token,
    # ceremony_in_progress still True.
    install = install_factory(
        journal_current="vA",
        alias_target="vB",
        top_target="releases/v0.31.37",
        in_flight_target="vB",
    )
    r = check_release_pointer(install)
    assert r["ceremony_in_progress"] is True
    assert r["ok"] is False
    with caplog.at_level(logging.WARNING):
        run_boot_check(install)
    assert any("release-pointer-drift" in rec.message for rec in caplog.records)


def test_run_boot_check_logs_drift_token(install_factory, caplog):
    import logging
    install = install_factory(
        journal_current="v0.31.42",
        alias_target="v0.31.37",
        top_target="releases/v0.31.42",
    )
    with caplog.at_level(logging.WARNING):
        run_boot_check(install)
    assert any(DRIFT_TOKEN in rec.message for rec in caplog.records)
    # last_boot_check recorded
    assert last_boot_check() is not None
