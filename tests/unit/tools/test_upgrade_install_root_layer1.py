"""Layer-1 ownership-separation HOME-shape tests (protection plan 2026-10-12).

Context (protection-plan-install-dir-20261012.md §Layer 1): the live
service migrates to a dedicated service user whose systemd unit carries
``User=<service user>`` + ``Environment=HOME=<install dir>`` (e.g.
``HOME=/home/nea/agents-ensemble``), while agents stay on the operator
user. Every resolution that previously spelled the install root as
``Path.home() / "agents-ensemble"`` would WRONGLY NEST under that shape
(``/home/nea/agents-ensemble/agents-ensemble``) — this pack proves BOTH
shapes resolve correctly:

* (a) legacy shape — operator user, ``HOME=<tmp>/fake-home`` → roots are
  ``<fake-home>/agents-ensemble`` / ``<fake-home>/agents-ensemble-demo``
  (byte-identical to the historical resolution);
* (b) post-Layer-1 shape — service user, ``HOME=<tmp>/agents-ensemble``
  (or the -demo variant) → the root is ``HOME`` itself.

Sandbox discipline: ALL fixtures live under pytest's ``tmp_path`` via
fake-HOME monkeypatching — no fixture is ever constructed at a
real live-resolved path (2026-10-12 commission constraint).
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest

import daemon.tools.upgrade_journal as uj
import daemon.tools.upgrade_tools as ut


# ── shared fixture shims ────────────────────────────────────────────────────


def _legacy_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Shape (a): operator user home with NO canonical basename."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
    return fake_home


def _layer1_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str = "agents-ensemble"
) -> Path:
    """Shape (b): post-Layer-1 service user — HOME IS the install dir."""
    home = tmp_path / name
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
    return home


def _silence_env_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)


# ── _install_root_candidates / _canonical_install_root ─────────────────────


class TestInstallRootCandidates:
    def test_legacy_shape_candidates_are_the_nested_roots(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _legacy_home(monkeypatch, tmp_path)
        assert ut._install_root_candidates("agents-ensemble") == [
            home / "agents-ensemble"
        ]
        assert ut._install_root_candidates("agents-ensemble-demo") == [
            home / "agents-ensemble-demo"
        ]

    def test_layer1_shape_home_self_first_then_nested(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        # HOME-self FIRST (the correct post-flip root), then the legacy
        # nested spelling (kept for the stale-nested-dir edge case).
        assert ut._install_root_candidates("agents-ensemble") == [
            home,
            home / "agents-ensemble",
        ]

    def test_layer1_demo_shape(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble-demo")
        assert ut._install_root_candidates("agents-ensemble-demo") == [
            home,
            home / "agents-ensemble-demo",
        ]

    def test_layer1_live_home_resolves_demo_sibling_next_door(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        # The flipped live daemon resolving the demo install that still
        # lives next door: candidates = nested spelling, then the sibling.
        assert ut._install_root_candidates("agents-ensemble-demo") == [
            home / "agents-ensemble-demo",
            home.parent / "agents-ensemble-demo",
        ]

    def test_canonical_root_legacy_verified(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _legacy_home(monkeypatch, tmp_path)
        live = home / "agents-ensemble"
        live.mkdir()
        (live / ".env").write_text("POSTGRES_DB=ensemble_prod\n", encoding="utf-8")
        assert ut._canonical_install_root("agents-ensemble") == live

    def test_canonical_root_legacy_fallback_when_nothing_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _legacy_home(monkeypatch, tmp_path)
        # Nothing exists → legacy spelling fallback (historical behavior).
        assert ut._canonical_install_root("agents-ensemble") == home / "agents-ensemble"

    def test_canonical_root_layer1_verified_via_home_self(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        (home / ".env").write_text("POSTGRES_DB=ensemble_prod\n", encoding="utf-8")
        root = ut._canonical_install_root("agents-ensemble")
        assert root == home
        # The wrong-nesting class is dead: the root is never HOME/<name>.
        assert root == Path.home()
        assert root != Path.home() / "agents-ensemble"

    def test_canonical_root_layer1_demo_sibling_verified(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        (home / ".env").write_text("POSTGRES_DB=ensemble_prod\n", encoding="utf-8")
        demo = home.parent / "agents-ensemble-demo"
        demo.mkdir()
        (demo / ".env").write_text("POSTGRES_DB=ensemble_demo\n", encoding="utf-8")
        assert ut._canonical_install_root("agents-ensemble-demo") == demo


# ── _auto_derive_env under both shapes ──────────────────────────────────────


class TestAutoDeriveLayer1:
    def test_live_via_legacy_topology(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _silence_env_markers(monkeypatch)
        home = _legacy_home(monkeypatch, tmp_path)
        live_env = home / "agents-ensemble" / ".env"
        live_env.parent.mkdir()
        live_env.write_text("POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8")
        assert ut._auto_derive_env() == "live"

    def test_live_via_layer1_home_topology(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The core post-flip proof: HOME=/…/agents-ensemble with .env at
        HOME/.env (NOT nested) auto-derives live. Pre-fix this returned
        None (fail-closed refusal — the upgrade lane would be dead)."""
        _silence_env_markers(monkeypatch)
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        (home / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        assert ut._auto_derive_env() == "live"

    def test_demo_via_layer1_home_topology(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _silence_env_markers(monkeypatch)
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble-demo")
        (home / ".env").write_text(
            "POSTGRES_DB=ensemble_demo\nPORT=7979\n", encoding="utf-8"
        )
        assert ut._auto_derive_env() == "demo"

    def test_layer1_live_shape_db_hint_cross_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """db-hint branch under the flipped shape: the daemon's own
        $POSTGRES_DB=ensemble_prod + HOME/.env present → live."""
        _silence_env_markers(monkeypatch)
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        (home / ".env").write_text("PORT=9797\n", encoding="utf-8")
        monkeypatch.setenv("POSTGRES_DB", "ensemble_prod")
        assert ut._auto_derive_env() == "live"

    def test_dev_shape_still_wins_when_no_install_topology(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _silence_env_markers(monkeypatch)
        _legacy_home(monkeypatch, tmp_path)
        monkeypatch.setenv("POSTGRES_DB", "ensemble_dev")
        assert ut._auto_derive_env() == "dev"

    def test_frozen_binary_layer1_live_not_sandbox(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The dangerous misclassification, pinned: a frozen binary inside
        the flipped install tree must NOT resolve sandbox (sandbox env
        skips the live-only gates). Pre-fix, install_dir == HOME failed
        the `!= HOME/agents-ensemble` check and returned sandbox."""
        _silence_env_markers(monkeypatch)
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        (home / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        exe = home / "releases" / "v9.9.9" / "ensemble-prod"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(ut.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ut.sys, "executable", str(exe))
        assert ut._auto_derive_env() == "live"

    def test_frozen_binary_legacy_live_not_sandbox(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Legacy shape preserved: frozen binary under the operator-user
        live install is live, not sandbox."""
        _silence_env_markers(monkeypatch)
        home = _legacy_home(monkeypatch, tmp_path)
        live = home / "agents-ensemble"
        live.mkdir()
        (live / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        exe = live / "releases" / "v9.9.9" / "ensemble-prod"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(ut.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ut.sys, "executable", str(exe))
        assert ut._auto_derive_env() == "live"

    def test_frozen_binary_sandbox_still_sandbox(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Sandbox detection preserved under BOTH shapes: a frozen binary
        in a throwaway tree is sandbox even when a canonical-looking HOME
        exists."""
        _silence_env_markers(monkeypatch)
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        (home / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        sbx = tmp_path / "ens-sbx"
        exe = sbx / "releases" / "v0.0.1" / "ensemble-prod"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(ut.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ut.sys, "executable", str(exe))
        assert ut._auto_derive_env() == "sandbox"

    def test_frozen_binary_sandbox_under_legacy_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Historical shape preserved verbatim: legacy HOME + throwaway
        frozen install → sandbox (the drill-lane contract)."""
        _silence_env_markers(monkeypatch)
        _legacy_home(monkeypatch, tmp_path)
        sbx = tmp_path / "ens-sbx"
        exe = sbx / "releases" / "v0.0.1" / "ensemble-prod"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(ut.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ut.sys, "executable", str(exe))
        assert ut._auto_derive_env() == "sandbox"


# ── _resolve_install_dir under both shapes ──────────────────────────────────


class TestResolveInstallDirLayer1:
    def test_live_legacy(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        home = _legacy_home(monkeypatch, tmp_path)
        assert ut._resolve_install_dir("live") == home / "agents-ensemble"

    def test_demo_legacy(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        home = _legacy_home(monkeypatch, tmp_path)
        assert ut._resolve_install_dir("demo") == home / "agents-ensemble-demo"

    def test_live_layer1_home_self(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """journal_path(install_dir) feeds the whole upgrade lane; under
        the flipped shape it must resolve to HOME/releases/state.json,
        never the nested HOME/agents-ensemble/releases/state.json."""
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        (home / ".env").write_text("POSTGRES_DB=ensemble_prod\n", encoding="utf-8")
        resolved = ut._resolve_install_dir("live")
        assert resolved == home
        assert resolved != home / "agents-ensemble"

    def test_demo_layer1_home_self(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble-demo")
        (home / ".env").write_text("POSTGRES_DB=ensemble_demo\n", encoding="utf-8")
        assert ut._resolve_install_dir("demo") == home

    def test_sandbox_branch_untouched(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _legacy_home(monkeypatch, tmp_path)
        assert ut._resolve_install_dir("sandbox") is None  # not frozen
        assert ut._resolve_install_dir("dev") is None
        assert ut._resolve_install_dir(None) is None


# ── executor-user consistency (upgrade_journal.build_service_argv) ─────────


class TestExecutorUserConsistency:
    """The Layer-1 executor mechanism: system-bus transient units default
    to running as ROOT; when the daemon itself is the post-Layer-1
    unprivileged service user, the unit is pinned to the daemon's OWN
    runtime uid/gid. Runtime-context derived (HOME topology + geteuid) —
    never a hardcoded username, and byte-identical pre-flip."""

    ENV = {"PATH": "/usr/bin:/bin", "HOME": "/home/whoever"}
    ARGV = ["bash", "scripts/upgrade/promote.sh", "live"]

    def test_layer1_system_bus_pins_uid_gid(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "agents-ensemble"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(uj.os, "geteuid", lambda: 6499)
        monkeypatch.setattr(uj.os, "getegid", lambda: 6499)
        argv = uj.build_service_argv(
            list(self.ARGV), "run-1", "system", dict(self.ENV), tmp_path / "up.log"
        )
        assert "--uid=6499" in argv
        assert "--gid=6499" in argv

    def test_legacy_home_system_bus_no_pin(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Pre-flip operator lane: HOME basename is not an install name →
        NO uid/gid flags → argv byte-identical to the historical lane."""
        home = tmp_path / "fake-home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(uj.os, "geteuid", lambda: 6499)
        monkeypatch.setattr(uj.os, "getegid", lambda: 6499)
        argv = uj.build_service_argv(
            list(self.ARGV), "run-1", "system", dict(self.ENV), tmp_path / "up.log"
        )
        assert not any(a.startswith("--uid=") or a.startswith("--gid=") for a in argv)

    def test_layer1_user_bus_never_pins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """User-bus units already run as the invoking user (and reject
        User= overrides) — never pin there, under any HOME shape."""
        home = tmp_path / "agents-ensemble"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(uj.os, "geteuid", lambda: 6499)
        argv = uj.build_service_argv(
            list(self.ARGV), "run-1", "user", dict(self.ENV), tmp_path / "up.log"
        )
        assert not any(a.startswith("--uid=") or a.startswith("--gid=") for a in argv)

    def test_root_daemon_never_pins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Root daemon (today's live lane): euid 0 → NO flags, even if
        HOME looks Layer-1. The pre-flip root lane stays byte-identical
        under every condition combination."""
        home = tmp_path / "agents-ensemble"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(uj.os, "geteuid", lambda: 0)
        monkeypatch.setattr(uj.os, "getegid", lambda: 0)
        argv = uj.build_service_argv(
            list(self.ARGV), "run-1", "system", dict(self.ENV), tmp_path / "up.log"
        )
        assert not any(a.startswith("--uid=") or a.startswith("--gid=") for a in argv)

    def test_layer1_service_shape_probe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "agents-ensemble"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        assert uj._layer1_service_shape() is True
        other = tmp_path / "fake-home"
        other.mkdir()
        monkeypatch.setattr(Path, "home", lambda: other)
        assert uj._layer1_service_shape() is False
        demo = tmp_path / "agents-ensemble-demo"
        demo.mkdir()
        monkeypatch.setattr(Path, "home", lambda: demo)
        assert uj._layer1_service_shape() is True


# ── W1 (review round 2): own-db-hint precedence in the flipped shape ────────


class TestW1FlippedDemoPrecedence:
    """W1: a flipped-DEMO daemon (HOME=…agents-ensemble-demo, own
    POSTGRES_DB=ensemble_demo) with a co-located live sibling must derive
    ``demo`` — the daemon's own db_hint is evaluated against BOTH expected
    DBs BEFORE any .env cross-check file read, so the sibling's
    ensemble_prod .env can never relabel it (base behavior for this
    topology was fail-closed None; round-1 regressed it to ``live``)."""

    def test_flipped_demo_with_live_sibling_derives_demo(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """THE reviewer repro: flipped-demo HOME + own POSTGRES_DB + live
        sibling .env present → "demo" (not live, not None)."""
        _silence_env_markers(monkeypatch)
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble-demo")
        (home / ".env").write_text(
            "POSTGRES_DB=ensemble_demo\nPORT=7979\n", encoding="utf-8"
        )
        live_sibling = home.parent / "agents-ensemble"
        live_sibling.mkdir()
        (live_sibling / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        monkeypatch.setenv("POSTGRES_DB", "ensemble_demo")
        assert ut._auto_derive_env() == "demo"

    def test_flipped_demo_own_env_only_no_db_hint_still_demo(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Same-defect variant: db_hint NOT exported (daemon env lacks
        POSTGRES_DB) → the HOME-anchored (own) install .env must win over
        the sibling's before any sibling read (own-signals-first ordering
        of the .env cross-check fall-through)."""
        _silence_env_markers(monkeypatch)
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble-demo")
        (home / ".env").write_text(
            "POSTGRES_DB=ensemble_demo\nPORT=7979\n", encoding="utf-8"
        )
        live_sibling = home.parent / "agents-ensemble"
        live_sibling.mkdir()
        (live_sibling / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        assert ut._auto_derive_env() == "demo"

    def test_flipped_live_with_demo_sibling_still_live(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Symmetry: the flipped-LIVE daemon keeps deriving live with a
        demo sibling next door (db_hint unset)."""
        _silence_env_markers(monkeypatch)
        home = _layer1_home(monkeypatch, tmp_path, "agents-ensemble")
        (home / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        demo_sibling = home.parent / "agents-ensemble-demo"
        demo_sibling.mkdir()
        (demo_sibling / ".env").write_text(
            "POSTGRES_DB=ensemble_demo\nPORT=7979\n", encoding="utf-8"
        )
        assert ut._auto_derive_env() == "live"

    def test_preflip_demo_daemon_live_precedence_preserved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """BYTE-EQUIVALENCE PIN 1 (pre-flip, HOME=/home/<user> lane): the
        pre-flip demo daemon on a two-install host (HOME=<fake-home>, own
        POSTGRES_DB=ensemble_demo, BOTH installs present) keeps the
        legacy live→demo .env precedence → ``live``. The W1 gate must be
        scoped to the flipped HOME shape only — an ungated db_hint-first
        check would flip this to demo and change pre-flip behavior."""
        _silence_env_markers(monkeypatch)
        home = _legacy_home(monkeypatch, tmp_path)
        live = home / "agents-ensemble"
        live.mkdir()
        (live / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        demo = home / "agents-ensemble-demo"
        demo.mkdir()
        (demo / ".env").write_text(
            "POSTGRES_DB=ensemble_demo\nPORT=7979\n", encoding="utf-8"
        )
        monkeypatch.setenv("POSTGRES_DB", "ensemble_demo")
        assert ut._auto_derive_env() == "live"

    def test_preflip_ambient_prod_db_hint_alone_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """BYTE-EQUIVALENCE PIN 2 (pre-flip, no-topology lane): a daemon
        with ambient POSTGRES_DB=ensemble_prod but NO install-dir topology
        anywhere still fails closed to None — db_hint alone never resolves
        live/demo in the legacy hierarchy (the env-marker-absent refusal
        path is preserved verbatim)."""
        _silence_env_markers(monkeypatch)
        _legacy_home(monkeypatch, tmp_path)
        monkeypatch.setenv("POSTGRES_DB", "ensemble_prod")
        assert ut._auto_derive_env() is None


# ── M1: HOME edge shapes — unset / root / trailing slash / symlink ──────────


class TestHomeEdgeShapes:
    """M1: pin the traced-but-unpinned HOME edges against the CURRENT
    resolver contract. All fixtures live under tmp_path (fake-HOME
    discipline); the unset-HOME case patches the pwd fallback so no real
    home is ever consulted."""

    def test_unset_home_pwd_fallback_anchored_and_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """HOME unset → Path.home() falls back to pwd.getpwuid(); pinned
        to a fake pw_dir under tmp_path. Candidates anchor there and, with
        no install topology, the resolver fails closed to None."""
        pw_home = tmp_path / "pw-home"
        pw_home.mkdir()
        monkeypatch.delenv("HOME", raising=False)

        class _FakePw:  # noqa: D401 — plain record
            pw_dir = str(pw_home)

        monkeypatch.setattr(pwd, "getpwuid", lambda uid: _FakePw)
        _silence_env_markers(monkeypatch)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._install_root_candidates("agents-ensemble") == [
            pw_home / "agents-ensemble"
        ]
        assert ut._auto_derive_env() is None

    def test_root_home_candidates_join_at_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """HOME=/ → Path("/") (name ""); candidates join at the filesystem
        root (/agents-ensemble — mirroring lib.sh's ${HOME%/} empty-join)
        and the resolver fails closed (no install topology at /)."""
        monkeypatch.setattr(Path, "home", lambda: Path("/"))
        _silence_env_markers(monkeypatch)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._install_root_candidates("agents-ensemble") == [
            Path("/agents-ensemble")
        ]
        assert ut._auto_derive_env() is None

    def test_trailing_slash_home_byte_identical(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """HOME with a trailing slash resolves byte-identically to the
        canonical spelling (pathlib collapses at construction; S1's
        _normalized_home makes the strip explicit) — verified both at the
        candidate level and end-to-end through _auto_derive_env."""
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home) + "/")
        _silence_env_markers(monkeypatch)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._install_root_candidates("agents-ensemble") == [
            fake_home / "agents-ensemble"
        ]
        live = fake_home / "agents-ensemble"
        live.mkdir()
        (live / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        assert ut._auto_derive_env() == "live"

    def test_symlinked_home_resolves_through_link_unresolved_spelling(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """HOME is a symlink → verification follows the link (is_file/
        is_dir resolve), but the resolved root keeps the LINK spelling
        (Path.home() never canonicalizes) — current behavior, pinned."""
        real_home = tmp_path / "real-home"
        live = real_home / "agents-ensemble"
        live.mkdir(parents=True)
        (live / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        link_home = tmp_path / "link-home"
        link_home.symlink_to(real_home)
        monkeypatch.setenv("HOME", str(link_home))
        _silence_env_markers(monkeypatch)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._canonical_install_root("agents-ensemble") == (
            link_home / "agents-ensemble"
        )
        assert ut._auto_derive_env() == "live"


# ── M2: _layer1_service_shape exception path + bounded pin-own-uid ──────────


class TestLayer1ServiceShapeFailClosed:
    """M2: the topology probe fails closed (False) when Path.home itself
    raises, and the executor uid pin is BOUNDED to the process's OWN
    geteuid/getegid — spoofing HOME can gate WHETHER the pin fires but can
    never make the code pin any OTHER uid."""

    def test_path_home_raising_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> Path:
            raise RuntimeError("HOME unreadable")

        monkeypatch.setattr(Path, "home", _boom)
        assert uj._layer1_service_shape() is False

    def test_spoofed_home_pins_exactly_own_uid(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Spoofed-HOME documentation pin: with HOME spoofed to the
        install-dir basename and geteuid/getegid deliberately NOT
        spoofed, the pin (when it fires) equals the REAL process euid/egid
        exactly — no other uid value can ever appear in the argv."""
        home = tmp_path / "agents-ensemble"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        argv = uj.build_service_argv(
            ["bash", "scripts/upgrade/promote.sh", "live"],
            "run-m2-own-uid",
            "system",
            {"PATH": "/usr/bin:/bin", "HOME": "/home/whoever"},
            tmp_path / "up.log",
        )
        euid, egid = os.geteuid(), os.getegid()
        uid_flags = [
            a for a in argv if a.startswith("--uid=") or a.startswith("--gid=")
        ]
        if euid == 0:
            # Root process → the pin never fires (root lane stays unpinned).
            assert uid_flags == []
        else:
            assert uid_flags == [f"--uid={euid}", f"--gid={egid}"]
            for flag in uid_flags:
                assert flag in (f"--uid={euid}", f"--gid={egid}")

    def test_spoofed_home_value_comes_from_geteuid_not_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The pin VALUE derives from the process's own geteuid — for
        EITHER spoofed install-named HOME, the flags carry the (spoofed)
        process euid, never anything encoded in the HOME path."""
        for name in ("agents-ensemble", "agents-ensemble-demo"):
            home = tmp_path / name
            home.mkdir(exist_ok=True)
            monkeypatch.setattr(Path, "home", lambda h=home: h)
            monkeypatch.setattr(uj.os, "geteuid", lambda: 6499)
            monkeypatch.setattr(uj.os, "getegid", lambda: 6499)
            argv = uj.build_service_argv(
                ["bash", "scripts/upgrade/promote.sh", "live"],
                "run-m2-spoof",
                "system",
                {"PATH": "/usr/bin:/bin", "HOME": "/home/whoever"},
                tmp_path / "up.log",
            )
            assert [a for a in argv if a.startswith("--uid=")] == ["--uid=6499"]
            assert [a for a in argv if a.startswith("--gid=")] == ["--gid=6499"]


# ── S3: full-argv byte-identity snapshot vs the historical construction ─────


class TestExecutorArgvHistoricalByteIdentity:
    """S3: for EACH current lane, the COMPLETE systemd-run argv equals the
    historical pre-Layer-1 construction (base c8a3fbf6a build_service_argv)
    — not just --uid/--gid absence. The historical builder below is
    transcribed verbatim from the base code: ["systemd-run", ("--user")?,
    "--unit=ensemble-upgrade-<run_id>", "--wait", "--collect",
    "--property=Restart=no", "--property=StandardOutput=append:<log>",
    "--property=StandardError=append:<log>", "--setenv=K=V" (sorted),
    "--", <inner_argv...>]."""

    ENV = {"PATH": "/usr/bin:/bin", "HOME": "/home/whoever"}
    ARGV = ["bash", "scripts/upgrade/promote.sh", "live"]

    @staticmethod
    def _historical(
        bus_kind: str,
        run_id: str,
        env: dict[str, str],
        log_path: Path,
        inner_argv: list[str],
    ) -> list[str]:
        head: list[str] = ["systemd-run"]
        if bus_kind == "user":
            head.append("--user")
        head.append(f"--unit=ensemble-upgrade-{run_id}")
        head.append("--wait")
        head.append("--collect")
        head.append("--property=Restart=no")
        log_str = str(Path(log_path).absolute())
        head.append(f"--property=StandardOutput=append:{log_str}")
        head.append(f"--property=StandardError=append:{log_str}")
        for key in sorted(env):
            head.append(f"--setenv={key}={env[key]}")
        head.append("--")
        return head + list(inner_argv)

    def _check_lane(
        self,
        monkeypatch: pytest.MonkeyPatch,
        home: Path,
        euid: int,
        egid: int,
        bus_kind: str,
        run_id: str,
        tmp_path: Path,
    ) -> None:
        # NOTE: home basenames only — build_service_argv never stats HOME,
        # so /root and /home/nea spellings need no fixture dirs (and no
        # real tree is ever touched).
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(uj.os, "geteuid", lambda: euid)
        monkeypatch.setattr(uj.os, "getegid", lambda: egid)
        log_path = tmp_path / f"{run_id}.log"
        argv = uj.build_service_argv(
            list(self.ARGV), run_id, bus_kind, dict(self.ENV), log_path
        )
        assert argv == self._historical(
            bus_kind, run_id, dict(self.ENV), log_path, list(self.ARGV)
        )

    def test_lane_root_daemon_euid0_home_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Root daemon (today's live lane): euid 0, HOME=/root, system
        bus → full argv byte-identical to the historical construction."""
        self._check_lane(monkeypatch, Path("/root"), 0, 0, "system", "s3-root", tmp_path)

    def test_lane_operator_user_home_nea(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Operator lane: euid!=0, HOME=/home/nea (basename not an
        install name) → no pin → historical argv verbatim."""
        self._check_lane(
            monkeypatch, Path("/home/nea"), 1000, 1000, "system", "s3-op", tmp_path
        )

    def test_lane_user_bus_kind_even_flipped_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """User-bus lane: user units already run as the invoking user —
        no pin EVEN under the flipped HOME shape → historical argv."""
        home = tmp_path / "agents-ensemble"
        home.mkdir()
        self._check_lane(monkeypatch, home, 1000, 1000, "user", "s3-user-bus", tmp_path)

    def test_lane_nonroot_euid_wrong_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """euid!=0 but HOME=/root (root-shaped HOME, non-root process):
        _layer1_service_shape() is False → no pin → historical argv."""
        self._check_lane(
            monkeypatch, Path("/root"), 1000, 1000, "system", "s3-wrong-home", tmp_path
        )
