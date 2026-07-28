"""Tests for the legacy ``context_injection: true`` deprecation warning.

When ``AgentRegistry.discover()`` loads an agent ``meta.json`` that still
carries the legacy boolean ``context_injection: true`` flag, it must emit a
``logger.warning`` so agent authors know to migrate to the newer
``context_injection_mode`` field (see ADR-8).

The warning must fire ONLY when the legacy boolean is present. Agents that
use only the newer ``context_injection_mode`` field — or neither flag at
all — must NOT produce the deprecation warning (they are not using the
deprecated flag).

These tests run via the standard ``AgentRegistry`` discovery flow against
freshly-created ``tmp_path`` agents directories, so no production
``agents/`` data is touched.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest


# Logger name emitted from ``daemon.registry`` (see registry.py module-level
# ``logger = logging.getLogger(__name__)``). Capturing at this level keeps
# the test focused on the deprecation warning rather than any other
# registry-level messages that may be emitted during discovery.
_REGISTRY_LOGGER = "daemon.registry"


def _write_meta(agent_dir: Path, meta: dict) -> Path:
    """Write ``meta`` to ``agent_dir/meta.json`` and return the agent dir.

    Creates ``agent_dir`` (and parents) if needed. The agent directory name
    is intentionally distinct from any real agent in ``agents/`` so the
    test never accidentally re-uses production state.
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    return agent_dir


def _make_agents_dir(tmp_path: Path, agents: dict[str, dict]) -> Path:
    """Build a tmp ``agents/`` directory containing the supplied agents.

    ``agents`` maps directory name -> meta.json dict. Each entry creates
    ``tmp_path/<dir>/meta.json`` with the supplied payload.
    """
    for dir_name, meta in agents.items():
        _write_meta(tmp_path / dir_name, meta)
    return tmp_path


def _discover_with_caplog(agents_dir: Path, caplog: pytest.LogCaptureFixture):
    """Run ``AgentRegistry.discover()`` while capturing registry logs."""
    from daemon.registry import AgentRegistry

    with caplog.at_level(logging.WARNING, logger=_REGISTRY_LOGGER):
        registry = AgentRegistry(agents_dir)
        registry.discover()
    return registry


def _has_deprecation_warning(caplog: pytest.LogCaptureFixture) -> bool:
    """True if any captured record is the legacy ``context_injection`` warning."""
    return any(
        "deprecated 'context_injection: true'" in rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.WARNING
    )


@pytest.fixture(autouse=True)
def _reset_deprecation_set():
    """Clear the module-level dedup set so every test starts fresh.

    ``daemon.registry._deprecation_warned`` persists across tests (module-level
    set). Without resetting, a test reusing an agent_id that an earlier test
    already warned for would see NO warning — a false negative.
    """
    from daemon.registry import _deprecation_warned

    _deprecation_warned.clear()
    yield
    _deprecation_warned.clear()


# =============================================================================
# 1. Legacy flag present → warning emitted
# =============================================================================


class TestLegacyFlagEmitsWarning:
    """``context_injection: true`` in meta.json triggers a deprecation warning."""

    def test_context_injection_true_emits_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """An agent with ``context_injection: true`` must emit the deprecation warning."""
        _make_agents_dir(
            tmp_path,
            {
                "legacy_agent": {
                    "id": "legacy_agent",
                    "name": "Legacy Agent",
                    "description": "Agent still using the legacy boolean flag.",
                    "context_injection": True,
                },
            },
        )

        _discover_with_caplog(tmp_path, caplog)

        assert _has_deprecation_warning(caplog), (
            "Expected the 'context_injection: true' deprecation warning when "
            "an agent's meta.json carries the legacy boolean flag. "
            f"Captured records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_warning_includes_agent_id(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """The warning message must identify the offending agent by id."""
        _make_agents_dir(
            tmp_path,
            {
                "my_legacy_agent": {
                    "id": "my_legacy_agent",
                    "name": "My Legacy Agent",
                    "description": "Test agent with legacy flag.",
                    "context_injection": True,
                },
            },
        )

        _discover_with_caplog(tmp_path, caplog)

        deprecation_records = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "deprecated 'context_injection: true'" in rec.getMessage()
        ]
        assert deprecation_records, "Expected at least one deprecation warning record"
        # The agent id must appear in the formatted message so operators can
        # locate the offending meta.json.
        assert any(
            "my_legacy_agent" in rec.getMessage() for rec in deprecation_records
        ), (
            "Deprecation warning must include the agent id so operators can "
            "locate the offending meta.json. "
            f"Got: {[rec.getMessage() for rec in deprecation_records]}"
        )

    def test_warning_mentions_new_flag(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """The warning must guide operators toward the new ``context_injection_mode`` flag."""
        _make_agents_dir(
            tmp_path,
            {
                "legacy_agent": {
                    "id": "legacy_agent",
                    "name": "Legacy Agent",
                    "description": "Agent still using the legacy boolean flag.",
                    "context_injection": True,
                },
            },
        )

        _discover_with_caplog(tmp_path, caplog)

        deprecation_records = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "deprecated 'context_injection: true'" in rec.getMessage()
        ]
        assert deprecation_records, "Expected at least one deprecation warning record"
        # Guide operators toward the replacement field.
        assert any(
            "context_injection_mode" in rec.getMessage()
            for rec in deprecation_records
        ), (
            "Deprecation warning must mention the replacement "
            "'context_injection_mode' field. "
            f"Got: {[rec.getMessage() for rec in deprecation_records]}"
        )


# =============================================================================
# 2. Legacy flag absent → NO warning
# =============================================================================


class TestNoLegacyFlagNoWarning:
    """Agents without the legacy boolean must NOT emit the deprecation warning."""

    def test_only_context_injection_mode_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Using only ``context_injection_mode`` (the replacement field) must not warn.

        These agents have already migrated — they explicitly opt into
        ``human_messages`` via the new flag and never used the legacy
        boolean. The deprecation warning is for the legacy flag only.
        """
        _make_agents_dir(
            tmp_path,
            {
                "modern_agent": {
                    "id": "modern_agent",
                    "name": "Modern Agent",
                    "description": "Agent using the new mode field.",
                    "context_injection_mode": "human_messages",
                },
            },
        )

        _discover_with_caplog(tmp_path, caplog)

        assert not _has_deprecation_warning(caplog), (
            "Did NOT expect the legacy 'context_injection: true' deprecation "
            "warning when the agent only uses the new 'context_injection_mode' field. "
            f"Captured records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_no_flags_at_all_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An agent with neither flag must not emit the deprecation warning."""
        _make_agents_dir(
            tmp_path,
            {
                "vanilla_agent": {
                    "id": "vanilla_agent",
                    "name": "Vanilla Agent",
                    "description": "Plain agent, no injection flags set.",
                },
            },
        )

        _discover_with_caplog(tmp_path, caplog)

        assert not _has_deprecation_warning(caplog), (
            "Did NOT expect the legacy 'context_injection: true' deprecation "
            "warning when the agent's meta.json has neither flag set. "
            f"Captured records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_context_injection_false_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Explicit ``context_injection: false`` must not emit the deprecation warning.

        The legacy flag is still recognised by AgentMetadata, but the
        deprecation warning is reserved for agents actually USING the flag
        (i.e. opting in via ``true``). A boolean ``false`` is functionally
        equivalent to omitting the flag — neither case needs migration.
        """
        _make_agents_dir(
            tmp_path,
            {
                "explicit_false_agent": {
                    "id": "explicit_false_agent",
                    "name": "Explicit False Agent",
                    "description": "Agent explicitly opting out via legacy boolean.",
                    "context_injection": False,
                },
            },
        )

        _discover_with_caplog(tmp_path, caplog)

        assert not _has_deprecation_warning(caplog), (
            "Did NOT expect the deprecation warning when 'context_injection' "
            "is explicitly set to false (functionally identical to absent). "
            f"Captured records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_warn_only_for_offending_agent_in_mixed_dir(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """In a mixed directory, the warning fires only for the legacy-flag agent.

        When several agents are discovered together, the warning must be
        scoped to the agent that actually carries the legacy flag. Modern
        agents (using only ``context_injection_mode``) must not trigger
        spurious warnings.
        """
        _make_agents_dir(
            tmp_path,
            {
                "legacy_a": {
                    "id": "legacy_a",
                    "name": "Legacy A",
                    "description": "Legacy agent A.",
                    "context_injection": True,
                },
                "modern_b": {
                    "id": "modern_b",
                    "name": "Modern B",
                    "description": "Modern agent B.",
                    "context_injection_mode": "human_messages",
                },
                "vanilla_c": {
                    "id": "vanilla_c",
                    "name": "Vanilla C",
                    "description": "Vanilla agent C.",
                },
            },
        )

        _discover_with_caplog(tmp_path, caplog)

        deprecation_records = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "deprecated 'context_injection: true'" in rec.getMessage()
        ]
        # Exactly one warning, scoped to legacy_a.
        assert len(deprecation_records) == 1, (
            "Expected exactly one deprecation warning (for legacy_a). "
            f"Got {len(deprecation_records)}: "
            f"{[rec.getMessage() for rec in deprecation_records]}"
        )
        assert "legacy_a" in deprecation_records[0].getMessage(), (
            "The single deprecation warning must reference 'legacy_a'. "
            f"Got: {deprecation_records[0].getMessage()}"
        )
        assert "modern_b" not in deprecation_records[0].getMessage()
        assert "vanilla_c" not in deprecation_records[0].getMessage()

    def test_context_injection_object_form_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The new object form must not trigger the legacy boolean deprecation warning."""
        _make_agents_dir(
            tmp_path,
            {
                "object_form_agent": {
                    "id": "object_form_agent",
                    "name": "Object Form Agent",
                    "description": "Agent using the new context_injection object form.",
                    "context_injection": {"heuristic_match_shared_md_files": True},
                },
            },
        )

        _discover_with_caplog(tmp_path, caplog)

        assert not _has_deprecation_warning(caplog), (
            "Did NOT expect the legacy 'context_injection: true' deprecation warning "
            "when the agent uses the new object form "
            "'context_injection: {\"heuristic_match_shared_md_files\": true}'. "
            f"Captured records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )
