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
