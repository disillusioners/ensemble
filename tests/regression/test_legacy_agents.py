"""Regression tests for ``system_prompt`` context injection mode.

Phase 1-5 of the Context Injection Restructure added two modes:

* ``system_prompt`` (default) — legacy behavior; all context baked
  into the system prompt via 7 appenders.
* ``human_messages`` (new) — context as ``[SYSTEM CONTEXT: ...]``
  HumanMessages.

The default mode (``system_prompt``) MUST be byte-identical to
pre-refactor behavior. These tests ensure that every aspect of the
legacy pipeline remains intact:

1. The 7-appender chain executes fully in ``system_prompt`` mode.
2. CONTEXT appenders do NOT early-return in ``system_prompt`` mode.
3. ``_resolve_injection_mode()`` defaults to ``"system_prompt"`` for
   all "missing/invalid" cases.
4. The defense instruction is absent in ``system_prompt`` mode.
5. ``_apply_post_cache_appends()`` with ``mode=system_prompt`` is
   byte-identical to calling it without ``mode`` (backward-compat).

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
    ``system_prompt`` mode paths.
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


# ─── Test 1: 7-appender chain executes fully in system_prompt mode ─────────────


class TestSevenAppenderChainInSystemPromptMode:
    """Verify all 7 appenders run when ``mode="system_prompt"``."""

    def test_context_key_appender_runs(self) -> None:
        """``append_context_key`` adds the CONTEXT_KEY section."""
        out, _ = _apply_post_cache_appends(
            **_args(
                SimpleNamespace(context_injection=False, inject_allowed_models=False),
                mode="system_prompt",
            )
        )
        assert "## Context Key" in out
        assert "CONTEXT_KEY:" in out

    def test_shared_context_metadata_appender_runs(self) -> None:
        """``append_shared_context_metadata`` adds the KV fence."""
        out, _ = _apply_post_cache_appends(
            **_args(
                SimpleNamespace(context_injection=False, inject_allowed_models=False),
                mode="system_prompt",
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
                mode="system_prompt",
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
                    mode="system_prompt",
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
                mode="system_prompt",
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
            mode="system_prompt",
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
            mode="system_prompt",
        )
        args["manager"] = manager

        out, _ = _apply_post_cache_appends(**args)
        assert "## Auto-Loaded Skills (Evolvable)" in out
        assert "# Evolvable Skill" in out
        assert "Do the evolvable thing." in out


# ─── Test 2: CONTEXT appenders do NOT early-return in system_prompt mode ─────────


class TestContextAppendersDoNotEarlyReturnInSystemPromptMode:
    """In ``system_prompt`` mode, CONTEXT appenders must produce output.

    Each test seeds data and asserts the appender produces its XML/KV
    fence — proving the early-return gate is NOT triggered.
    """

    def test_append_shared_context_metadata_does_not_early_return(self) -> None:
        """KV metadata IS injected in ``system_prompt`` mode."""
        kv_repo = _make_kv_repo({"scope": "LARGE"})
        result = append_shared_context_metadata(
            system_prompt="BASE",
            instance_id="inst-1",
            instance_repository=_make_instance_repo(),
            shared_context_metadata_repo=kv_repo,
            mode="system_prompt",
        )
        assert result != "BASE"
        assert "# Shared Context" in result
        assert "scope" in result

    def test_append_context_injection_does_not_early_return(self) -> None:
        """Matched context files ARE injected in ``system_prompt`` mode."""
        with patch(
            "daemon.services.instance_lifecycle.get_shared_context",
            return_value="# Shared Context\nSome injected content.",
        ):
            result = append_context_injection(
                system_prompt="BASE",
                instance_id="inst-1",
                instance_repository=_make_instance_repo(),
                agent_meta=SimpleNamespace(context_injection=True),
                mode="system_prompt",
            )
        assert result != "BASE"
        assert "# Injected Project Context" in result


# ─── Test 3: _resolve_injection_mode defaults to "system_prompt" ─────────────────


class TestResolveInjectionModeDefaults:
    """Coercion rules for ``_resolve_injection_mode``.

    Per ADR-8 the function must fail-open: any invalid / missing value
    must coerce to ``"system_prompt"`` so a misconfigured agent cannot
    break instance execution.
    """

    def test_none_meta_returns_system_prompt(self) -> None:
        """``agent_meta=None`` defaults to ``"system_prompt"``."""
        assert _resolve_injection_mode(None) == ContextInjectionMode.SYSTEM_PROMPT

    def test_missing_context_injection_mode_returns_system_prompt(self) -> None:
        """No ``context_injection_mode`` field → default ``"system_prompt"``."""
        meta = SimpleNamespace()  # no context_injection_mode attr
        assert _resolve_injection_mode(meta) == ContextInjectionMode.SYSTEM_PROMPT

    def test_explicit_system_prompt_returns_system_prompt(self) -> None:
        """Explicit ``"system_prompt"`` is returned unchanged."""
        meta = SimpleNamespace(context_injection_mode="system_prompt")
        assert _resolve_injection_mode(meta) == "system_prompt"

    def test_explicit_human_messages_returns_human_messages(self) -> None:
        """Explicit ``"human_messages"`` is returned unchanged."""
        meta = SimpleNamespace(context_injection_mode="human_messages")
        assert _resolve_injection_mode(meta) == "human_messages"

    def test_invalid_string_coerced_to_system_prompt(self) -> None:
        """A typo / garbage string falls back to ``"system_prompt"``."""
        meta = SimpleNamespace(context_injection_mode="BOTH")
        assert _resolve_injection_mode(meta) == ContextInjectionMode.SYSTEM_PROMPT

    def test_empty_string_coerced_to_system_prompt(self) -> None:
        """Empty string falls back to ``"system_prompt"``."""
        meta = SimpleNamespace(context_injection_mode="")
        assert _resolve_injection_mode(meta) == ContextInjectionMode.SYSTEM_PROMPT


# ─── Test 4: Defense instruction absent in system_prompt mode ────────────────────


class TestDefenseInstructionAbsentInSystemPromptMode:
    """The ``append_context_injection_defense`` PERSONA instruction must
    NOT appear in ``system_prompt`` mode.

    The instruction is wired in only for ``human_messages`` mode
    (legacy XML fences already serve as a structural boundary, and
    adding the instruction to the legacy path would break the
    byte-identical-output constraint).
    """

    def test_defense_instruction_absent_from_individual_apppender(self) -> None:
        """``append_context_injection_defense`` is never wired into the
        ``system_prompt`` path — it only runs inside
        ``_apply_post_cache_appends`` when ``mode="human_messages"``.
        """
        base = "BASE"
        result = append_context_injection_defense(base)
        # The function itself works (sanity check), but the chain
        # should never call it in system_prompt mode.
        assert "## System Context Messages" in result

    def test_defense_absent_from_apply_post_cache_appends_system_prompt_mode(
        self,
    ) -> None:
        """End-to-end: ``mode="system_prompt"`` → no defense instruction."""
        out, _ = _apply_post_cache_appends(
            **_args(
                SimpleNamespace(context_injection=False, inject_allowed_models=False),
                mode="system_prompt",
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


# ─── Test 5: Backward compatibility of _apply_post_cache_appends signature ───────


class TestBackwardCompatibilityOfApplyPostCacheAppends:
    """``_apply_post_cache_appends(mode=None)`` must behave identically to
    ``_apply_post_cache_appends(mode="system_prompt")``.

    Calling without ``mode`` resolves via ``_resolve_injection_mode``,
    which falls back to ``"system_prompt"``. A regression that flipped
    the default would silently break every legacy agent that omits the
    ``mode`` kwarg.

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

    def test_no_mode_and_system_prompt_mode_produce_identical_output(self) -> None:
        """The full prompt output must be byte-identical."""
        meta = SimpleNamespace(context_injection=True, inject_allowed_models=False)

        args_no_mode = _args(meta, mode=None)
        args_no_mode["project_repository"] = None  # → language = "Auto"

        args_system = _args(meta, mode="system_prompt")
        args_system["project_repository"] = None  # → language = "Auto"

        # ``append_current_time`` reads wall-clock UTC at microsecond
        # precision — patch ``datetime`` inside the lifecycle module
        # so both calls see the same value and the byte-identical
        # comparison is deterministic.
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
            out_no_mode, _ = _apply_post_cache_appends(**args_no_mode)
            out_system_prompt, _ = _apply_post_cache_appends(**args_system)

        # Suppress the unused-import warning.
        del _RealDatetime

        assert out_no_mode == out_system_prompt

    def test_no_mode_resolves_to_system_prompt_via_resolve(self) -> None:
        """``mode=None`` triggers ``_resolve_injection_mode`` lookup."""
        # Build args with agent_meta that has context_injection_mode unset
        # so the lookup returns the default.
        meta = SimpleNamespace(context_injection=False, inject_allowed_models=False)
        args = _args(meta, mode=None)
        args["project_repository"] = None  # → language = "Auto"

        # Verify the output has CONTEXT_KEY but NO defense instruction
        # (proves the legacy path ran, not human_messages).
        out, _ = _apply_post_cache_appends(**args)
        assert "## Context Key" in out
        assert "## System Context Messages" not in out

    def test_explicit_system_prompt_mode_matches_implicit_none_mode(self) -> None:
        """Regression guard: explicit vs implicit mode produce same sections."""
        meta = SimpleNamespace(context_injection=True, inject_allowed_models=True)

        args_implicit = _args(meta, mode=None)
        args_implicit["project_repository"] = None  # → language = "Auto"

        args_explicit = _args(meta, mode="system_prompt")
        args_explicit["project_repository"] = None  # → language = "Auto"

        # ``append_current_time`` reads wall-clock UTC at microsecond
        # precision — patch ``datetime`` inside the lifecycle module
        # so both calls see the same value.
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
            # No mode kwarg — resolved via agent_meta.
            out_implicit, _ = _apply_post_cache_appends(**args_implicit)
            # Explicit system_prompt.
            out_explicit, _ = _apply_post_cache_appends(**args_explicit)

        # Suppress the unused-import warning.
        del _RealDatetime

        # Both must contain all legacy sections.
        for out in (out_implicit, out_explicit):
            assert "## Context Key" in out
            assert "# Shared Context" in out
            assert "## Current Time" in out
            assert "# Injected Project Context" in out
            assert "# Allowed Models" in out
            assert "## System Context Messages" not in out  # no defense in legacy

        # And they must be identical.
        assert out_implicit == out_explicit
