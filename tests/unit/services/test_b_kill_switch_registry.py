"""B.S.8 — Versioned-constant REGISTRY tests for the (b) kill-switch.

Wave 2 stage iii (wc-wake-report-integrity, phase2-plan §4.2 B.S.8,
ruling S8). Pins the exact kill-switch env names as SEPARATELY
VERSIONED constants and their wiring surface, mirroring the
``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED`` registry shape
(``tests/unit/test_governor_recursion_guard.py::TestKillSwitchResolution``
pins the resolver truthiness; this module pins the NAME REGISTRY):

  * BOTH constants exist in ``daemon/constants.py`` with their exact
    reserved strings — renaming or deleting either fails here.
  * The **B** name (``WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED``)
    is WIRED: a ``daemon/config.py`` Pydantic field exists with a
    TRUTHY default (True — enforcement ship state since the D2.5-FLIP
    default promotion landed 2026-10-31), and the section's
    ``env_prefix`` + field name resolve to EXACTLY the constant's
    string (no literal env-name fork in either direction). Setting the
    env is honored by a fresh settings instance in BOTH directions
    (``=0`` → False — the kill switch).
  * The **A** name (``WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED``)
    is RESERVED-UNUSED (C2-D2.2/D2.3 LOCKED): it is wired to NOTHING —
    no config field consumes it and the only ``daemon/`` occurrence of
    the string is its constants.py declaration.
  * **Split-versioning independence** (ruling S8 + D2.9, refined by
    S1 council follow-up, 2026-08-30): suppressing the Wave-1 (c)
    marker via ``SANITY_FLAG_VERSION != 1`` does NOT affect the (d)
    prompt-side guidance consumption — the prompt guidance is static
    agent-file text, independent of the marker constant; and the
    (b) PREDICATE does NOT read ``SANITY_FLAG_VERSION`` (D2.18
    content-blind — the predicate's emission / decision is decoupled
    from the marker's emission state). The (b) guard/notice module
    IS allowed to read ``SANITY_FLAG_VERSION`` in ONE bounded
    location — the ``constants_marker_text()`` citation helper —
    because the (b) notice's marker citation must be gated on the
    (c) marker emission state (S1: a citation pointing at a marker
    that never appears would be a lying instruction). This is a
    deliberate, narrow coupling; the source-scan assert below is
    updated to enforce that the read is BOUNDED to the citation
    helper and does not leak into the predicate / enforcement path.

Resolver truthiness semantics themselves (env values → bool, cache,
restart-required) are pinned beside the resolver in
``tests/unit/services/test_report_integrity_guard.py``; the
D2.5-FLIP default promotion (unset/blank → ON, ``=0`` → OFF) is
pinned at the resolver level in ``TestBResolverDefaultPromotion``
below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import daemon.constants as constants
from daemon.config import Config, ReportIntegrityConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
DAEMON_DIR = REPO_ROOT / "daemon"


class TestKillSwitchNameRegistry:
    """The exact env-name strings, pinned as constants (B.S.8)."""

    def test_b_constant_exists_with_exact_name(self) -> None:
        assert hasattr(constants, "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED")
        assert (
            constants.WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED
            == "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED"
        )

    def test_a_constant_exists_with_exact_name(self) -> None:
        assert hasattr(constants, "WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED")
        assert (
            constants.WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED
            == "WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED"
        )


class TestBKillSwitchConfigWiring:
    """The B name is wired to a config field with a TRUTHY default
    (D2.5-FLIP default promotion, landed 2026-10-31)."""

    def test_config_section_exists_on_config(self) -> None:
        cfg = Config()
        assert hasattr(cfg, "report_integrity"), (
            "Config must expose the report_integrity section (B.S.2 wiring)"
        )
        assert isinstance(cfg.report_integrity, ReportIntegrityConfig)

    def test_b_field_defaults_truthy(self) -> None:
        """Default True — ENFORCEMENT ship state (D2.5-FLIP landed
        2026-10-31); the ``=0`` env is the retained kill switch."""
        cfg = Config()
        assert cfg.report_integrity.b_terminal_waiting_guard_enabled is True

    def test_env_name_derivation_matches_constant(self) -> None:
        """No literal env-name fork: prefix + field must resolve to the
        exact constant string (both directions pinned).
        """
        prefix = ReportIntegrityConfig.model_config.get("env_prefix", "")
        derived = (prefix + "b_terminal_waiting_guard_enabled").upper()
        assert (
            derived
            == constants.WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED
        ), (
            "the Pydantic env binding (env_prefix + field) must resolve to "
            "the daemon/constants.py NAME — the registry test fails on any "
            "fork between config.py and constants.py"
        )

    def test_env_flip_reaches_fresh_settings_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting the env var flips a FRESH settings instance in BOTH
        directions — the binding is live, not decorative (with the
        default now True, the ``=0`` leg is the discriminating one).
        (The runtime gate additionally caches at boot: restart required
        to flip — pinned by the resolver tests in
        test_report_integrity_guard.py and the default-promotion pins
        in TestBResolverDefaultPromotion below.)
        """
        name = constants.WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED
        monkeypatch.setenv(name, "1")
        assert ReportIntegrityConfig().b_terminal_waiting_guard_enabled is True
        monkeypatch.setenv(name, "0")
        assert ReportIntegrityConfig().b_terminal_waiting_guard_enabled is False

    def test_resolver_reads_the_constant_not_a_literal(self) -> None:
        """The guard module's resolver consumes the NAME constant from
        daemon/constants.py — no literal env-name string fork.
        """
        import daemon.services.report_integrity_guard as rig

        assert (
            rig._B_GUARD_ENV
            == constants.WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED
        )


class TestBResolverDefaultPromotion:
    """D2.5-FLIP default promotion (landed 2026-10-31) pinned at the
    RESOLVER level: unset and set-but-blank resolve ON, explicit
    ``=0`` resolves OFF, unknown non-blank WARNs once and falls back
    to ON — a future accidental flip-back of either default fails
    here.

    Cache discipline (W1-pollution lesson): every test resets the
    module-global resolver cache via ``monkeypatch.setattr(rig,
    ``_B_GUARD_ENABLED``, None)`` — monkeypatch restores the original
    value on teardown, so no resolved state leaks between tests.
    """

    def test_unset_env_resolves_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unset env + cold cache → ON (the new default)."""
        import daemon.services.report_integrity_guard as rig

        monkeypatch.delenv(
            constants.WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED,
            raising=False,
        )
        monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
        assert rig.resolve_report_integrity_b_guard_enabled() is True

    def test_set_blank_resolves_on_silently(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Set-but-blank (``""`` / whitespace) ≡ unset ≡ ON — resolved
        silently, NO unknown-value warning.
        """
        import logging

        import daemon.services.report_integrity_guard as rig

        monkeypatch.setenv(
            constants.WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED,
            "   ",
        )
        monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            assert rig.resolve_report_integrity_b_guard_enabled() is True
        assert not any(
            "not a recognized" in r.getMessage() for r in caplog.records
        ), "blank ≡ unset ≡ default — must not WARN"

    def test_explicit_zero_resolves_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The kill switch: explicit ``=0`` → OFF (the revert path —
        blanking/unsetting the env is NO LONGER the revert path)."""
        import daemon.services.report_integrity_guard as rig

        monkeypatch.setenv(
            constants.WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED,
            "0",
        )
        monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
        assert rig.resolve_report_integrity_b_guard_enabled() is False

    def test_unknown_value_warns_once_and_falls_back_on(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unknown non-blank value → WARN once + fall back to the NEW
        default (ON); the fallback caches so the warning fires once.
        """
        import logging

        import daemon.services.report_integrity_guard as rig

        monkeypatch.setenv(
            constants.WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED,
            "banana",
        )
        monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            assert rig.resolve_report_integrity_b_guard_enabled() is True
            # Cached: a second call returns without re-warning.
            assert rig.resolve_report_integrity_b_guard_enabled() is True
        warn_records = [
            r
            for r in caplog.records
            if "not a recognized" in r.getMessage()
            and r.levelno == logging.WARNING
        ]
        assert len(warn_records) == 1, (
            "unknown value must WARN exactly once (cache) and fall back "
            f"to the ON default; got {len(warn_records)} warnings"
        )
        assert "falling back" in warn_records[0].getMessage()
        assert "ON (default" in warn_records[0].getMessage()


class TestAKillSwitchReservedUnused:
    """The A name is RESERVED-UNUSED (C2-D2.2/D2.3) — wired to NOTHING."""

    def test_no_config_field_consumes_a(self) -> None:
        cfg = Config()
        assert not hasattr(
            cfg.report_integrity, "a_premature_turn_guard_enabled"
        ), (
            "the reserved (a) env name must NOT gain a config field at "
            "this stage (C2-D2.2 LOCKED — (a) does not land initially)"
        )
        assert not any(
            "a_premature_turn" in name
            for name in type(cfg.report_integrity).model_fields
        )

    def test_a_name_appears_only_in_constants_within_daemon(self) -> None:
        """grep-assert: the ONLY ``daemon/`` file containing the reserved
        (a) env-name string is its constants.py declaration.
        """
        needle = "WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED"
        hits: list[Path] = []
        for path in sorted(DAEMON_DIR.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:  # pragma: no cover — unreadable file
                continue
            if needle in text:
                hits.append(path)
        assert hits == [DAEMON_DIR / "constants.py"], (
            f"the reserved (a) name must be declared ONLY in "
            f"daemon/constants.py; found {hits}"
        )


class TestSplitVersioningIndependence:
    """SANITY_FLAG_VERSION (the (c) marker's versioned rollback seam)
    is INDEPENDENT of the (d) prompt-side consumption and of the (b)
    guard/notice code (ruling S8 share; D2.9 corrected marker text).
    """

    def test_marker_suppressed_still_prompt_guidance_intact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Documentation-grade: with SANITY_FLAG_VERSION suppressed (=0),
        the (d) parent-scrutiny guidance text is STILL present in the
        agent prompt surface — the prompts are static text consumed
        directly, never gated on the marker version constant.
        """
        monkeypatch.setattr(constants, "SANITY_FLAG_VERSION", 0)

        # The Wave-1 prompt tests are text-presence assertions on static
        # agent files — re-run their canonical-home check inline (leader
        # + the (d) parent set carry the marker-conditioned scrutiny
        # guidance in their operative prompt files).
        from tests.unit.test_report_integrity_prompts import (  # noqa: PLC0415
            D_HOME_FILES,
            _operative_dir,
        )

        for agent_id, homes in D_HOME_FILES.items():
            operative = _operative_dir(agent_id)
            texts = [
                (operative / home).read_text(encoding="utf-8")
                for home in homes
                if (operative / home).is_file()
            ]
            assert texts, (
                f"{operative.name}: no canonical home files found for (d)"
            )
            joined = "\n".join(texts)
            assert "[REPORT SANITY:" in joined, (
                f"(d) scrutiny guidance must remain present for "
                f"{agent_id} even with SANITY_FLAG_VERSION suppressed — "
                f"the prompt channel is independent of the marker version "
                f"(split-versioning independence)"
            )

    def test_guard_predicate_does_not_read_sanity_flag_version(self) -> None:
        """Source-scan: the (b) PREDICATE / gate code (not the citation
        helper) does NOT read ``SANITY_FLAG_VERSION``. The predicate is
        content-blind (D2.18) and its emission / decision is decoupled
        from the (c) marker version.

        The citation helper ``constants_marker_text()`` IS allowed to
        read the constant (S1 council follow-up, 2026-08-30): the (b)
        notice's marker citation is gated on the (c) marker emission
        state so the citation cannot point at a marker that never
        appears. This test asserts that read is BOUNDED to that helper
        and does NOT leak into the predicate / enforcement path —
        preserving the spirit of the S8 split-versioning ruling.
        """
        guard_path = DAEMON_DIR / "services" / "report_integrity_guard.py"
        guard_src = guard_path.read_text(encoding="utf-8")
        lines = guard_src.splitlines()

        # Find the helper's body by line number. We use a line-based
        # scanner: from the ``def constants_marker_text(`` line forward,
        # up to the next top-level ``def`` / ``class`` / ``@decorator`` at
        # column 0 — or end of file (the helper is currently the last
        # function in the module).
        helper_start = next(
            (
                i
                for i, ln in enumerate(lines)
                if ln.startswith("def constants_marker_text(")
            ),
            None,
        )
        assert helper_start is not None, (
            "the constants_marker_text() citation helper must exist in "
            "daemon/services/report_integrity_guard.py"
        )
        helper_end = next(
            (
                i
                for i in range(helper_start + 1, len(lines))
                if (
                    lines[i].startswith("def ")
                    or lines[i].startswith("class ")
                    or (
                        lines[i].startswith("@")
                        and not lines[i].startswith("    ")
                    )
                )
            ),
            len(lines),
        )
        helper_text = "\n".join(lines[helper_start:helper_end])

        # Sanity pin 1: the bounded read IS present in the citation helper.
        # If this assertion fires the helper was refactored away from its
        # S1 contract — re-anchor the gate or update S1.
        assert "SANITY_FLAG_VERSION" in helper_text, (
            "S1 pinning: the constants_marker_text() citation helper MUST "
            "read SANITY_FLAG_VERSION so the notice citation is gated on "
            "the (c) marker emission state (council S1, 2026-08-30). If "
            "this assertion fires the helper was refactored away from "
            "its S1 contract — re-anchor the gate or update S1."
        )

        # Sanity pin 2: the (b) predicate / gate / enforcement code
        # does NOT read the constant. We excise the helper body and
        # assert the remainder is free of the constant. We strip
        # comment lines first so a S1 explanation comment inside
        # ``_build_adjudication_notice`` does not register as a leak —
        # only actual code reads (non-comment, non-docstring) count.
        def _strip_comments(src: str) -> str:
            lines = src.splitlines()
            kept = []
            for ln in lines:
                stripped = ln.lstrip()
                if stripped.startswith("#"):
                    continue
                kept.append(ln)
            return "\n".join(kept)

        carved = "\n".join(lines[:helper_start] + lines[helper_end:])
        carved_no_comments = _strip_comments(carved)
        assert "SANITY_FLAG_VERSION" not in carved_no_comments, (
            "S8 split-versioning independence: the (b) predicate / gate / "
            "enforcement code MUST NOT read SANITY_FLAG_VERSION — the read "
            "is bounded to the constants_marker_text() citation helper "
            "(S1 council follow-up, 2026-08-30). Found a code-level read "
            "(non-comment) in the carved-out source."
        )
