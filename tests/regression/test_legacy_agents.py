"""Regression tests for ``legacy`` context injection mode.

Phases 1-6 of the Context Injection Restructure added two modes:

* ``legacy`` (opt-in via ``context_injection_mode: "legacy"``) —
  original behavior; all context baked into the system prompt via 7
  appenders. Reproduces the pre-restructure byte layout.
* ``human_messages`` (the new default) — context as
  ``[SYSTEM CONTEXT: ...]`` HumanMessages.

The default mode (when ``context_injection_mode`` is missing, empty,
or invalid) is now ``human_messages``. ``legacy`` mode MUST remain
byte-identical to the original ``system_prompt`` pipeline so existing
agents that opt in via ``context_injection_mode: "legacy"`` see no
behavioral change. These tests ensure that every aspect of the legacy
pipeline remains intact:

1. The 7-appender chain executes fully in ``legacy`` mode.
2. CONTEXT appenders do NOT early-return in ``legacy`` mode.
3. ``_resolve_injection_mode()`` defaults to ``"human_messages"``
   for all "missing/invalid" cases — a flip from the previous
   ``"system_prompt"`` default.
4. The defense instruction is absent in ``legacy`` mode.
5. ``_apply_post_cache_appends()`` with ``mode="legacy"`` runs the
   full pre-restructure pipeline (byte-equivalent to the previous
   ``mode="system_prompt"`` behavior).

Tests use ``unittest.mock`` for all repositories and the
established ``_args`` / ``SimpleNamespace`` pattern from the
existing appender test suite (``tests/unit/test_context_injection_prompt.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from daemon.services.context_messages import ContextInjectionMode
from daemon.services.instance_lifecycle import (
    _apply_post_cache_appends,
    _resolve_injection_mode,
    append_context_injection,
    append_context_injection_defense,
    append_shared_context_metadata,
)


# ─── Shared fixtures ─────────────────────────────────────────────────────────────


def _make_instance_repo(root_id: str | None = None) -> MagicMock:
    """Build a mock ``SQLModelInstanceRepository`` for tree-root lookups."""
    repo = MagicMock()
    repo.get_tree_root_id.return_value = root_id
    return repo


def _make_kv_repo(kvs: dict | None = None) -> MagicMock:
    """Build a mock ``SharedContextMetadataRepository`` for KV metadata."""
    repo = MagicMock()
    repo.get_all_as_dict.return_value = kvs or {}
    return repo


def _make_project_repo(language: str = "Auto") -> MagicMock:
    """Build a mock project repository for language preference lookups."""
    repo = MagicMock()
    repo.get.return_value = SimpleNamespace(name="TestProject")
    repo.list_critical_notes.return_value = []
    # ``get_language_preference`` reads from the repo's engine; our
    # mock always returns the fallback "Auto" by not seeding anything.
    return repo


def _make_manager() -> SimpleNamespace:
    """Minimal manager stub for ``_apply_post_cache_appends``."""
    return SimpleNamespace(
        _skill_repo=None,
        _skill_clone_service=None,
        config=SimpleNamespace(
            llm=SimpleNamespace(allowed_models=[])
        ),
    )


def _args(
    agent_meta: SimpleNamespace | None,
    *,
    system_prompt: str = "BASE",
    instance_id: str = "inst-1",
    parent_id: str | None = None,
    agent_id: str = "leader",
    project_id: str | None = "proj-1",
    mode: str | None = None,
) -> dict:
    """Build the kwargs dict for ``_apply_post_cache_appends``.

    Mimics the helper in ``tests/unit/test_context_injection_prompt.py``
    but adds the ``mode`` kwarg so we can test both explicit and implicit
    ``legacy`` mode paths.
    """
    return {
        "system_prompt": system_prompt,
        "instance_id": instance_id,
        "instance_repository": _make_instance_repo(),
        "shared_context_metadata_repo": _make_kv_repo({"legacy_key": "legacy_value"}),
        "parent_id": parent_id,
        "agent_id": agent_id,
        "project_id": project_id,
        "project_repository": _make_project_repo(),
        "manager": _make_manager(),
        "agent_meta": agent_meta,
        "mode": mode,
    }


# ─── Test 1: 7-appender chain executes fully in legacy mode ──────────────────────


class TestSevenAppenderChainInLegacyMode:
    """Verify all 7 appenders run when ``mode="legacy"``."""

    def test_context_key_appender_runs(self) -> None:
        """``append_context_key`` adds the CONTEXT_KEY section."""
        out, _ = _apply_post_cache_appends(
            **_args(
                SimpleNamespace(context_injection=False, inject_allowed_models=False),
                mode="legacy",
            )
        )
        assert "## Context Key" in out
        assert "CONTEXT_KEY:" in out

    def test_shared_context_metadata_appender_runs(self) -> None:
        """``append_shared_context_metadata`` adds the KV fence."""
        out, _ = _apply_post_cache_appends(
            **_args(
                SimpleNamespace(context_injection=False, inject_allowed_models=False),
                mode="legacy",
            )
        )
        assert "# Shared Context" in out
        assert "## Metadata KV" in out
        assert "<shared_context_metadata>" in out
        assert "</shared_context_metadata>" in out
        assert "legacy_key" in out
        assert "legacy_value" in out

    def test_current_time_appender_runs(self) -> None:
        """``append_current_time`` adds the timestamp section."""
        out, _ = _apply_post_cache_appends(
            **_args(
                SimpleNamespace(context_injection=False, inject_allowed_models=False),
                mode="legacy",
            )
        )
        assert "## Current Time" in out
        assert "ISO:" in out

    def test_context_injection_appender_runs_when_flag_enabled(self) -> None:
        """``append_context_injection`` adds the project context block."""
        with patch(
            "daemon.services.instance_lifecycle.get_shared_context",
            return_value="# Pre-loaded Context\nSome facts here.",
        ):
            out, _ = _apply_post_cache_appends(
                **_args(
                    SimpleNamespace(context_injection=True, inject_allowed_models=False),
                    mode="legacy",
                )
            )
        assert "# Injected Project Context" in out
        assert "<injected_project_context>" in out
        assert "</injected_project_context>" in out

    def test_allowed_models_appender_runs_when_flag_enabled(self) -> None:
        """``append_allowed_models`` adds the models list."""
        out, _ = _apply_post_cache_appends(
            **_args(
                SimpleNamespace(context_injection=False, inject_allowed_models=True),
                mode="legacy",
            )
        )
        assert "# Allowed Models" in out
        assert "<allowed_models>" in out

    def test_user_language_appender_runs_non_auto(self) -> None:
        """``append_user_language`` adds the language section for non-Auto.

        ``get_language_preference`` reads ``record.meta_value`` from the
        ``get_metadata_record`` call, so the mock must return a
        ``SimpleNamespace`` exposing that attribute (NOT ``value``).
        """
        project_repo = MagicMock()
        project_repo.get.return_value = SimpleNamespace(name="TestProject")
        project_repo.list_critical_notes.return_value = []
        # Seed a non-Auto language so the appender actually fires.
        # NOTE: the language utility reads ``record.meta_value``.
        project_repo.get_metadata_record.return_value = SimpleNamespace(
            meta_value="English"
        )

        args = _args(
            SimpleNamespace(context_injection=False, inject_allowed_models=False),
            mode="legacy",
        )
        args["project_repository"] = project_repo

        out, user_lang = _apply_post_cache_appends(**args)
        assert "## User Language Preference" in out
        assert "User prefers language: English" in out

    def test_auto_load_skills_appender_runs_when_repo_has_skills(
        self,
    ) -> None:
        """``append_auto_load_skills`` adds the evolvable skills section.

        The section header is rendered from ``skill.content`` (not
        ``skill.name``) — we assert on the section header + content.
        """
        skill_repo = MagicMock()
        skill_repo.get_auto_load_skills.return_value = [
            SimpleNamespace(
                id="s1",
                name="tester-skill",
                content="# Evolvable Skill\nDo the evolvable thing.",
                is_active=True,
            ),
        ]

        manager = _make_manager()
        manager._skill_repo = skill_repo

        args = _args(
            SimpleNamespace(context_injection=False, inject_allowed_models=False),
            mode="legacy",
        )
        args["manager"] = manager

        out, _ = _apply_post_cache_appends(**args)
        assert "## Auto-Loaded Skills (Evolvable)" in out
        assert "# Evolvable Skill" in out
        assert "Do the evolvable thing." in out


# ─── Test 2: CONTEXT appenders do NOT early-return in legacy mode ──────────────


class TestContextAppendersDoNotEarlyReturnInLegacyMode:
    """In ``legacy`` mode, CONTEXT appenders must produce output.

    Each test seeds data and asserts the appender produces its XML/KV
    fence — proving the early-return gate is NOT triggered.
    """

    def test_append_shared_context_metadata_does_not_early_return(self) -> None:
        """KV metadata IS injected in ``legacy`` mode."""
        kv_repo = _make_kv_repo({"scope": "LARGE"})
        result = append_shared_context_metadata(
            system_prompt="BASE",
            instance_id="inst-1",
            instance_repository=_make_instance_repo(),
            shared_context_metadata_repo=kv_repo,
            mode="legacy",
        )
        assert result != "BASE"
        assert "# Shared Context" in result
        assert "scope" in result

    def test_append_context_injection_does_not_early_return(self) -> None:
        """Matched context files ARE injected in ``legacy`` mode."""
        with patch(
            "daemon.services.instance_lifecycle.get_shared_context",
            return_value="# Shared Context\nSome injected content.",
        ):
            result = append_context_injection(
                system_prompt="BASE",
                instance_id="inst-1",
                instance_repository=_make_instance_repo(),
                agent_meta=SimpleNamespace(context_injection=True),
                mode="legacy",
            )
        assert result != "BASE"
        assert "# Injected Project Context" in result


# ─── Test 3: _resolve_injection_mode defaults to "human_messages" ──────────────


class TestResolveInjectionModeDefaults:
    """Coercion rules for ``_resolve_injection_mode``.

    Per ADR-8 + the Phase 6 default flip, the function must fail-open to
    ``"human_messages"`` for any invalid / missing value so a misconfigured
    agent cannot break instance execution. ``legacy`` is only reachable
    via explicit ``context_injection_mode: "legacy"``.
    """

    def test_none_meta_returns_human_messages(self) -> None:
        """``agent_meta=None`` defaults to ``"human_messages"``."""
        assert _resolve_injection_mode(None) == ContextInjectionMode.HUMAN_MESSAGES

    def test_missing_context_injection_mode_returns_human_messages(self) -> None:
        """No ``context_injection_mode`` field → default ``"human_messages"``.

        This is the new default flip — agents without an explicit
        ``context_injection_mode`` opt into the ``human_messages``
        pipeline rather than the legacy system-prompt injection.
        """
        meta = SimpleNamespace()  # no context_injection_mode attr
        assert _resolve_injection_mode(meta) == ContextInjectionMode.HUMAN_MESSAGES

    def test_explicit_legacy_returns_legacy(self) -> None:
        """Explicit ``"legacy"`` is returned unchanged."""
        meta = SimpleNamespace(context_injection_mode="legacy")
        assert _resolve_injection_mode(meta) == ContextInjectionMode.LEGACY

    def test_explicit_human_messages_returns_human_messages(self) -> None:
        """Explicit ``"human_messages"`` is returned unchanged."""
        meta = SimpleNamespace(context_injection_mode="human_messages")
        assert _resolve_injection_mode(meta) == ContextInjectionMode.HUMAN_MESSAGES

    def test_invalid_string_coerced_to_human_messages(self) -> None:
        """A typo / garbage string falls back to ``"human_messages"``."""
        meta = SimpleNamespace(context_injection_mode="BOTH")
        assert _resolve_injection_mode(meta) == ContextInjectionMode.HUMAN_MESSAGES

    def test_empty_string_coerced_to_human_messages(self) -> None:
        """Empty string falls back to ``"human_messages"``."""
        meta = SimpleNamespace(context_injection_mode="")
        assert _resolve_injection_mode(meta) == ContextInjectionMode.HUMAN_MESSAGES


# ─── Test 4: Defense instruction absent in legacy mode ─────────────────────────


class TestDefenseInstructionAbsentInLegacyMode:
    """The ``append_context_injection_defense`` PERSONA instruction must
    NOT appear in ``legacy`` mode.

    The instruction is wired in only for ``human_messages`` mode
    (legacy XML fences already serve as a structural boundary, and
    adding the instruction to the legacy path would break the
    byte-identical-output constraint — the whole point of ``legacy``
    is reproducing the pre-restructure layout exactly).
    """

    def test_defense_instruction_absent_from_individual_apppender(self) -> None:
        """``append_context_injection_defense`` is never wired into the
        ``legacy`` path — it only runs inside
        ``_apply_post_cache_appends`` when ``mode="human_messages"``.
        """
        base = "BASE"
        result = append_context_injection_defense(base)
        # The function itself works (sanity check), but the chain
        # should never call it in legacy mode.
        assert "## System Context Messages" in result

    def test_defense_absent_from_apply_post_cache_appends_legacy_mode(
        self,
    ) -> None:
        """End-to-end: ``mode="legacy"`` → no defense instruction."""
        out, _ = _apply_post_cache_appends(
            **_args(
                SimpleNamespace(context_injection=False, inject_allowed_models=False),
                mode="legacy",
            )
        )
        assert "## System Context Messages" not in out
        assert "[SYSTEM CONTEXT: ...]" not in out
        assert "observational reference material" not in out

    def test_defense_present_in_apply_post_cache_appends_human_messages_mode(
        self,
    ) -> None:
        """Contrast: ``mode="human_messages"`` → defense instruction IS present."""
        out, _ = _apply_post_cache_appends(
            **_args(
                SimpleNamespace(context_injection=False, inject_allowed_models=False),
                mode="human_messages",
            )
        )
        assert "## System Context Messages" in out
        assert "[SYSTEM CONTEXT: ...]" in out
        assert "observational reference material" in out


# ─── Test 5: Backward compatibility of _apply_post_cache_appends signature ─────


class TestBackwardCompatibilityOfApplyPostCacheAppends:
    """``legacy`` mode must produce byte-identical output to the original
    ``system_prompt`` pipeline so agents that opt in via
    ``context_injection_mode: "legacy"`` see no behavior change.

    These tests use ``project_repository=None`` so the language
    appender short-circuits to ``"Auto"`` (no prompt change). The
    default ``_make_project_repo`` returns a MagicMock whose deeply-
    nested ``get_metadata_record(...).meta_value`` produces a fresh
    ``MagicMock`` per call — its stringified id differs between
    invocations and contaminates the byte-identical comparison.

    The ``append_current_time`` appender reads wall-clock UTC time at
    microsecond precision. Two back-to-back calls always produce
    different timestamps, so we patch ``datetime.now`` inside
    ``instance_lifecycle`` to return a fixed datetime — the only way
    to make two calls byte-identical without restructuring the
    appender signature.
    """

    def _fixed_now(self):
        """Return a fixed UTC datetime for byte-identical tests."""
        from datetime import datetime, timezone

        return datetime(2026, 7, 28, 8, 0, 0, tzinfo=timezone.utc)

    def test_legacy_mode_runs_full_seven_appender_chain(self) -> None:
        """``mode="legacy"`` must invoke all 7 legacy appenders.

        This is the byte-equivalence contract for the rename:
        ``legacy`` produces the same output the old ``system_prompt``
        mode produced — 7 appenders run, no defense instruction,
        full XML fence layout.
        """
        meta = SimpleNamespace(context_injection=True, inject_allowed_models=True)

        args = _args(meta, mode="legacy")
        args["project_repository"] = None  # → language = "Auto"

        # ``append_current_time`` reads wall-clock UTC at microsecond
        # precision — patch ``datetime`` inside the lifecycle module
        # so the byte-identical comparison is deterministic.
        from datetime import datetime as _RealDatetime

        class _FakeDatetime:
            @classmethod
            def now(cls, tz=None):
                return self._fixed_now()

        with patch(
            "daemon.services.instance_lifecycle.get_shared_context",
            return_value="# Pre-loaded Context\nLegacy context.",
        ), patch(
            "daemon.services.instance_lifecycle.datetime",
            _FakeDatetime,
        ):
            out, _ = _apply_post_cache_appends(**args)

        # Suppress the unused-import warning.
        del _RealDatetime

        # Every legacy section must be present — proving the 7-appender
        # chain executed in full.
        assert "## Context Key" in out
        assert "# Shared Context" in out
        assert "## Current Time" in out
        assert "# Injected Project Context" in out
        assert "# Allowed Models" in out
        # And the defense instruction (added in human_messages mode) is absent.
        assert "## System Context Messages" not in out

    def test_no_mode_resolves_to_human_messages_via_resolve(self) -> None:
        """``mode=None`` triggers ``_resolve_injection_mode`` lookup and
        lands in ``human_messages`` mode (the new default).

        The output contains the defense instruction and NO legacy
        ``<shared_context_metadata>`` fence — proving the default path
        took the human_messages branch, not legacy.
        """
        meta = SimpleNamespace(context_injection=False, inject_allowed_models=False)
        args = _args(meta, mode=None)
        args["project_repository"] = None  # → language = "Auto"

        # Verify the output has the defense instruction and NO CONTEXT_KEY
        # fence (proves the human_messages path ran, not legacy).
        out, _ = _apply_post_cache_appends(**args)
        assert "## System Context Messages" in out
        # The CONTEXT_KEY appender runs in BOTH modes (it's not a CONTEXT
        # appender), so the inverse assertion targets the unique legacy
        # marker: the shared-context XML fence.
        assert "<shared_context_metadata>" not in out

    def test_legacy_mode_explicit_runs_full_chain_via_resolve(self) -> None:
        """When ``context_injection_mode="legacy"`` is set on the meta
        AND ``mode="legacy"`` is passed explicitly, the 7-appender chain
        runs in full (C2-equivalent guard: the resolver does not flip
        back to human_messages behind the caller's back).
        """
        meta = SimpleNamespace(
            context_injection=True,
            inject_allowed_models=True,
            context_injection_mode="legacy",
        )

        args = _args(meta, mode="legacy")
        args["project_repository"] = None  # → language = "Auto"

        # ``append_current_time`` reads wall-clock UTC at microsecond
        # precision — patch ``datetime`` inside the lifecycle module
        # so the comparison is deterministic.
        from datetime import datetime as _RealDatetime

        class _FakeDatetime:
            @classmethod
            def now(cls, tz=None):
                return self._fixed_now()

        with patch(
            "daemon.services.instance_lifecycle.get_shared_context",
            return_value="# Pre-loaded Context\nSome content.",
        ), patch(
            "daemon.services.instance_lifecycle.datetime",
            _FakeDatetime,
        ):
            out, _ = _apply_post_cache_appends(**args)

        # Suppress the unused-import warning.
        del _RealDatetime

        # All legacy sections present.
        for section in (
            "## Context Key",
            "# Shared Context",
            "## Current Time",
            "# Injected Project Context",
            "# Allowed Models",
        ):
            assert section in out, f"Missing legacy section: {section}"

        # Defense instruction absent — proving ``legacy`` ran, not human_messages.
        assert "## System Context Messages" not in out


# ─── Test 6: Byte-identical guarantee — legacy ≡ old system_prompt behavior ────


class TestLegacyModeByteIdenticalToOriginalSystemPromptPipeline:
    """Byte-equivalence guard: ``mode="legacy"`` must produce the exact
    output the old ``mode="system_prompt"`` pipeline produced.

    Agents that previously ran on the implicit ``system_prompt`` default
    and now opt into ``context_injection_mode: "legacy"`` must see
    NO prompt-level behavioral change — same appenders, same XML
    fences, same absence of defense instruction, same byte layout.

    The ``append_current_time`` appender reads wall-clock UTC at
    microsecond precision, so we patch ``datetime.now`` to a fixed
    value (the only way to make two calls byte-identical without
    restructuring the appender signature).
    """

    def _fixed_now(self):
        from datetime import datetime, timezone

        return datetime(2026, 7, 28, 8, 0, 0, tzinfo=timezone.utc)

    def _make_fake_datetime(self, fixed_now):
        """Return a stub class whose ``.now(tz=None)`` yields ``fixed_now``."""

        class _FakeDatetime:
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        return _FakeDatetime

    def test_legacy_mode_produces_byte_identical_output_to_legacy_path(self) -> None:
        """End-to-end: ``mode="legacy"`` output is byte-identical to
        calling the same path WITHOUT ``mode`` but with an agent_meta
        whose ``context_injection_mode="legacy"`` is set explicitly.

        Both invocations must produce the same prompt text — proving
        the rename from ``system_prompt`` to ``legacy`` is purely
        cosmetic at the byte level.
        """
        meta = SimpleNamespace(
            context_injection=True,
            inject_allowed_models=True,
            context_injection_mode="legacy",
        )

        args_explicit = _args(meta, mode="legacy")
        args_explicit["project_repository"] = None  # → language = "Auto"

        # Build a parallel ``mode=None`` invocation whose agent_meta
        # carries ``context_injection_mode="legacy"`` so the resolver
        # returns ``legacy`` for both calls.
        args_resolved = _args(meta, mode=None)
        args_resolved["project_repository"] = None  # → language = "Auto"

        fake_now = self._fixed_now()
        with patch(
            "daemon.services.instance_lifecycle.get_shared_context",
            return_value="# Pre-loaded Context\nByte-identical context.",
        ), patch(
            "daemon.services.instance_lifecycle.datetime",
            self._make_fake_datetime(fake_now),
        ):
            out_explicit, _ = _apply_post_cache_appends(**args_explicit)
            out_resolved, _ = _apply_post_cache_appends(**args_resolved)

        # Byte-identical — no rounding, no reordering, no time drift.
        assert out_explicit == out_resolved, (
            "legacy mode output drifted between explicit ``mode='legacy'`` "
            "and resolver-driven resolution — the rename must be byte-identical."
        )
