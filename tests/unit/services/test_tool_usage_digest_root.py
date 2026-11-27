"""Unit tests for the digest ROOT resolution — spec v4 §3.9.1 (9 nodes).

This file REWRITES the v1 "council C5" pins.  v1 asserted that the
frozen branch anchored the digest root at ``sys.executable``'s parent —
the *convention candidate* the house resolver returns when it finds
nothing — and pointed ``sys.executable`` at a NON-EXISTENT
``/tmp/fake-install/bin/ensemble``.  The verification gate
(``_verified_install_root``) then raised ``DigestRootUnresolved``, so the
assertion was UNREACHABLE (§3.9.1 test 3: "v1's shape could not pass").
The rewrite pins the RESOLVED, VERIFIED roots instead:

* ``_code_root()``   — the READ anchor: the RELEASE dir when frozen, the
  checkout root in source mode (§3.4.1).  It is NEVER a write
  destination.
* ``digest_root()``  — the WRITE anchor: the install dir that OWNS
  ``releases/`` (never a path under ``releases/<v>/`` — a promote MOVES
  that subtree), reached through the house resolver's bounded
  four-ancestor walk.  ``DigestRootUnresolved`` when no install dir
  verifies — and NOTHING is written.
* ``_artifact_dir()`` — the release payload dir
  (``releases/<v>/tool-usage-artifacts``), never the source-mode
  planning path.

HERMETIC (binding): every layout is built under ``tmp_path``; no
live-shaped HOME-derived path is resolved, no file is written outside
``tmp_path``, and no database is contacted.  The ``ENSEMBLE_TOOL_USAGE_*``
env seams are cleared in every node so an ambient value can never decide
an assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from daemon.services import tool_usage_digest as tud


# ── layout builders (every path lives under tmp_path) ──────────────────────


def _make_binary(path: Path) -> Path:
    """Create a REAL (empty) file at ``path``.

    The house resolver stats and resolves the executable, so the fixture
    is a real file on disk rather than a synthetic string.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _install_layout(tmp_path: Path, *rel: str) -> tuple[Path, Path]:
    """Build ``<tmp_path>/install/<rel...>/ensemble-prod``.

    Returns ``(install_dir, binary)`` — ``install_dir`` is the ancestor
    that owns ``releases/``, i.e. the promote-stable install root.
    """
    install = tmp_path / "install"
    binary = _make_binary(install.joinpath(*rel) / "ensemble-prod")
    return install, binary


def _frozen_at(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    """Pin the process into the frozen shape the release binary runs in."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(binary), raising=False)


# ── 1. the READ anchor (frozen) ────────────────────────────────────────────


def test_frozen_code_root_anchors_at_the_release_dir(monkeypatch, tmp_path):
    """Spec §3.9.1 test 1 — frozen → the RELEASE dir (the binary's parent).

    The corrected contract (§3.4.1): the frozen branch anchors at the
    RELEASE dir (``releases/<v>/``), NOT the install dir that owns
    ``releases/``.  A promote MOVES the release dir; the install dir is
    the promote-stable anchor.
    """
    release = tmp_path / "install" / "releases" / "v0.0.0"
    binary = _make_binary(release / "ensemble-prod")
    _frozen_at(monkeypatch, binary)

    code_root = tud._code_root()

    assert code_root == release
    # NOT the install dir one level up, and NOT the payload dir.
    assert code_root != release.parent
    assert code_root.name == "v0.0.0"


# ── 2. the READ anchor (source mode) ───────────────────────────────────────


def test_non_frozen_code_root_is_the_checkout_root(monkeypatch):
    """Spec §3.9.1 test 2 — source mode → ``__file__`` parents[2]."""
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert tud._code_root() == Path(tud.__file__).resolve().parents[2]


# ── 3. the WRITE anchor — the install dir that owns releases/ ─────────────


def test_digest_root_resolves_to_the_install_dir_owning_releases(monkeypatch, tmp_path):
    """Spec §3.9.1 test 3 (T-B) — the resolved digest root.

    Real layout ``<tmp>/install/releases/v0.0.0/ensemble-prod`` with
    ``sys.frozen`` set and ``sys.executable`` pointed at it: the root is
    the install dir that owns ``releases/`` — one level ABOVE the release
    dir — and ``releases`` is not part of the root path.
    """
    install, binary = _install_layout(tmp_path, "releases", "v0.0.0")
    _frozen_at(monkeypatch, binary)
    monkeypatch.delenv(tud.DIGEST_ROOT_ENV, raising=False)

    root = tud.digest_root()

    expected_install = Path(str(binary)).resolve().parents[2]
    assert expected_install == install
    assert root == expected_install / tud.DIGEST_RELATIVE_DIR
    assert "releases" not in root.parts
    assert root.parts[-5:] == tuple(tud.DIGEST_RELATIVE_DIR.parts)


# ── 4. the WRITE anchor — the bounded four-ancestor walk ───────────────────


def test_digest_root_finds_the_install_dir_through_the_bounded_walk(monkeypatch, tmp_path):
    """Spec §3.9.1 test 4 (T-B2) — the nested release layout.

    ``<tmp>/install/releases/v9/nested/ensemble-prod`` resolves to the
    SAME install dir.  This exercises the resolver's four-ancestor bound
    (``daemon/repositories/source/maintenance_seed.py:219-238``): the
    install dir is the FOURTH ancestor of the binary, i.e. the last one
    the bounded walk inspects.
    """
    install, binary = _install_layout(tmp_path, "releases", "v9", "nested")
    _frozen_at(monkeypatch, binary)
    monkeypatch.delenv(tud.DIGEST_ROOT_ENV, raising=False)

    root = tud.digest_root()

    expected_install = Path(str(binary)).resolve().parents[3]
    assert expected_install == install
    assert root == expected_install / tud.DIGEST_RELATIVE_DIR
    assert "releases" not in root.parts
    assert "nested" not in root.parts


# ── 5. the WRITE anchor — unresolvable ⇒ typed error, NOTHING written ──────


def test_digest_root_raises_when_no_install_dir_is_verifiable(monkeypatch, tmp_path):
    """Spec §3.9.1 test 5 (T-B3) — beyond the four-ancestor bound.

    ``<tmp>/install/releases/v9/nested/deeper/ensemble-prod``: no ancestor
    within the bound owns ``releases/``, and the house convention
    candidate does not verify either — so the digest destination is
    UNRESOLVED.  The typed error is raised, NO directory is created and
    NO file is written (no guessed path is ever used).
    """
    install, binary = _install_layout(tmp_path, "releases", "v9", "nested", "deeper")
    _frozen_at(monkeypatch, binary)
    monkeypatch.delenv(tud.DIGEST_ROOT_ENV, raising=False)

    def _tree() -> list[str]:
        return sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    before = _tree()

    with pytest.raises(tud.DigestRootUnresolved):
        tud.digest_root()

    assert _tree() == before, "the resolver must not create or write anything"
    assert not (install / ".agents").exists()
    assert not (install / ".agents" / "shared" / "planning").exists()


# ── 6/7. the env override wins over BOTH resolution paths ──────────────────


def test_env_override_precedence_over_frozen(monkeypatch, tmp_path):
    """Spec §3.9.1 test 6 — the env override wins; the resolver is NOT consulted."""
    _frozen_at(monkeypatch, tmp_path / "install" / "releases" / "v0.0.0" / "ensemble-prod")

    def _boom() -> Path:
        raise AssertionError("the install-root resolver must NOT be consulted")

    monkeypatch.setattr(tud, "_install_root", _boom)

    override = tmp_path / "custom-digests"
    monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(override))

    assert tud.digest_root() == override


def test_env_override_precedence_over_non_frozen(monkeypatch, tmp_path):
    """Spec §3.9.1 test 7 — same in source mode."""
    monkeypatch.delattr(sys, "frozen", raising=False)

    def _boom() -> Path:
        raise AssertionError("the install-root resolver must NOT be consulted")

    monkeypatch.setattr(tud, "_install_root", _boom)

    override = tmp_path / "another-digests"
    monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(override))

    assert tud.digest_root() == override


# ── 8. an EMPTY override falls through to the default resolution ───────────


def test_env_override_empty_string_falls_through(monkeypatch, tmp_path):
    """Spec §3.9.1 test 8 — a whitespace-only override does not win.

    The value is stripped before it is honoured, so a blank override
    falls through to the DEFAULT resolution — the install dir that owns
    ``releases/`` — not to the blank path.
    """
    install, binary = _install_layout(tmp_path, "releases", "v0.0.0")
    _frozen_at(monkeypatch, binary)
    monkeypatch.setenv(tud.DIGEST_ROOT_ENV, "   ")

    root = tud.digest_root()

    expected_install = Path(str(binary)).resolve().parents[2]
    assert expected_install == install
    assert root == expected_install / tud.DIGEST_RELATIVE_DIR
    assert root != Path("   ")


# ── 9. the READ payload anchor (frozen) ────────────────────────────────────


def test_artifact_dir_reads_the_release_payload_when_frozen(monkeypatch, tmp_path):
    """Spec §3.9.1 test 9 — frozen → ``<release>/tool-usage-artifacts``.

    The release payload ships NEXT TO the binary
    (``releases/<v>/tool-usage-artifacts/``); the source-mode planning
    path is NOT read in a frozen install.
    """
    release = tmp_path / "install" / "releases" / "v0.0.0"
    binary = _make_binary(release / "ensemble-prod")
    _frozen_at(monkeypatch, binary)
    monkeypatch.delenv(tud.ARTIFACT_DIR_ENV, raising=False)

    artifact_dir = tud._artifact_dir()

    assert artifact_dir == release / tud.ARTIFACT_DIRNAME
    assert artifact_dir == release / "tool-usage-artifacts"
    # NOT the source-mode planning path.
    repo_planning = (
        Path(tud.__file__).resolve().parents[2]
        / ".agents"
        / "shared"
        / "planning"
        / "tool-usage-metrics-p1"
    )
    assert artifact_dir != repo_planning
    assert "releases" in artifact_dir.parts
