"""Unit tests for the OPENAI_SELECTABLE_MODELS / OPENAI_ALLOWED_MODELS rename.

Precedence contract (mirrors the documented behavior in ``config.yaml``
and ``daemon/config.py``):

  1. ``OPENAI_SELECTABLE_MODELS`` (new, primary) — when set, wins
     outright, no warning.
  2. ``OPENAI_ALLOWED_MODELS`` (legacy) — when set AND the new name is
     unset, used as the effective source AND the warn-once
     ``warn_deprecated_allowed_models_env`` is invoked.
  3. Neither env var set → documented default
     ``("agentic", "coding", "coding2", "vision")`` AND the
     warn-once ``warn_default_allowed_models_applied`` is invoked.

Internal API (the pydantic field ``config.llm.allowed_models``) is
unchanged — only the env-var-level aliasing changed. These tests verify
the resolution chain in ``_resolve_allowed_models`` (pure function,
deterministic) and the warning-fires-exactly-once guard on
``warn_deprecated_allowed_models_env`` /
``warn_default_allowed_models_applied``.

The fail-safe default (``agentic, coding, coding2, vision``) was set
under the ``sel-default-20260927`` commission to align the constant in
``daemon/config.py`` with the YAML interpolation literal in
``config.yaml`` (which had already inlined ``vision`` per the
designer-agent P1-WP1 mission, but the constant drifted). The
``TestDefaultConsistencyPin`` class is the regression guard for future
drift.

Precedent: ``tests/unit/test_llm_reasoning_echo_config.py`` covers the
sibling deprecation pattern (``OPENAI_REASONING_ECHO_MODELS`` →
``OPENAI_REASONING_ECHO_DISABLED_MODELS``).
"""

from __future__ import annotations

import logging

import pytest


# ---------------------------------------------------------------------------
# Pure resolver tests — exercise every branch without touching os.environ
# or the module-level warning guard. Most other tests in this file import
# ``_resolve_allowed_models`` directly.
# ---------------------------------------------------------------------------


class TestResolveAllowedModelsPure:
    """The pure resolver (no os.environ access, no side effects beyond
    the ``on_legacy`` callback) — verify all three precedence branches.
    """

    def test_new_var_wins_when_set(self) -> None:
        from daemon.config import _resolve_allowed_models

        on_legacy_called = []

        def on_legacy() -> None:
            on_legacy_called.append(True)

        result = _resolve_allowed_models(
            "",
            new_var="gpt-5,gpt-5o",
            old_var=None,
            on_legacy=on_legacy,
        )
        assert result == "gpt-5,gpt-5o"
        assert on_legacy_called == []

    def test_new_var_wins_over_old_var(self) -> None:
        """When BOTH env vars are set, the new name wins outright — no
        warning, no consultation of the legacy value. This matches the
        contract that operators who have already migrated don't get
        nagged about the old name being on the same machine.
        """
        from daemon.config import _resolve_allowed_models

        on_legacy_called = []

        def on_legacy() -> None:
            on_legacy_called.append(True)

        result = _resolve_allowed_models(
            "",
            new_var="gpt-5",
            old_var="agentic,coding,coding2",
            on_legacy=on_legacy,
        )
        assert result == "gpt-5"
        assert on_legacy_called == []

    def test_old_var_honored_when_new_unset(self) -> None:
        """Legacy-only deployment: the legacy value is used AND the
        on_legacy callback fires exactly once.
        """
        from daemon.config import _resolve_allowed_models

        on_legacy_called = []

        def on_legacy() -> None:
            on_legacy_called.append(True)

        result = _resolve_allowed_models(
            "",
            new_var=None,
            old_var="agentic,coding,coding2",
            on_legacy=on_legacy,
        )
        assert result == "agentic,coding,coding2"
        assert on_legacy_called == [True]

    def test_old_var_honored_with_empty_yaml(self) -> None:
        """Real-world: the shipped config.yaml interpolates
        ``${OPENAI_SELECTABLE_MODELS:-agentic,coding}`` so the default
        is visible at the value line; an empty ``yaml_value`` here
        simulates what custom/programmatic yaml or a direct resolver
        call can still present. The resolver must NOT fall through to
        the default when the legacy var is set — the legacy value
        must win.
        """
        from daemon.config import _resolve_allowed_models

        result = _resolve_allowed_models(
            "",  # simulates a custom/programmatic yaml (shipped yaml now inlines the default)
            new_var=None,
            old_var="legacy-only",
            on_legacy=lambda: None,
        )
        assert result == "legacy-only"

    def test_neither_set_yields_documented_default(self) -> None:
        """When neither env var is exported and an empty yaml value
        arrives (custom yaml or programmatic callers), the resolver
        substitutes the documented default
        ``agentic,coding,coding2,vision`` so a no-env-var deployment
        matches the shipped YAML default (``sel-default-20260927``).
        The drift-detector pin test
        (``TestDefaultConsistencyPin``) guarantees the constant stays
        aligned with the YAML interpolation literal.
        """
        from daemon.config import _resolve_allowed_models

        result = _resolve_allowed_models(
            "",
            new_var=None,
            old_var=None,
            on_legacy=lambda: None,
        )
        assert result == "agentic,coding,coding2,vision"

    def test_yaml_passthrough_when_neither_set(self) -> None:
        """If the YAML layer somehow handed us a non-empty value
        (e.g. an operator hard-coded ``allowed_models: foo,bar`` in
        config.yaml bypassing the env vars), pass it through unchanged.
        The field validator downstream will handle CSV/JSON parsing.
        """
        from daemon.config import _resolve_allowed_models

        result = _resolve_allowed_models(
            "foo,bar",  # hard-coded YAML value
            new_var=None,
            old_var=None,
            on_legacy=lambda: None,
        )
        assert result == "foo,bar"

    def test_on_legacy_callback_not_invoked_when_new_wins(self) -> None:
        """The on_legacy callback must only fire when the legacy var
        is the EFFECTIVE source. New wins → callback never fires,
        even though the legacy var may also be set.
        """
        from daemon.config import _resolve_allowed_models

        calls = []

        def on_legacy() -> None:
            calls.append(1)

        _resolve_allowed_models(
            "", new_var="new-val", old_var="old-val", on_legacy=on_legacy
        )
        _resolve_allowed_models(
            "", new_var=None, old_var=None, on_legacy=on_legacy
        )
        assert calls == []


# ---------------------------------------------------------------------------
# Warn-once guard tests — verify the module-level guard short-circuits
# the second-and-subsequent calls. (Real load_config / __main__ / api.py
# call paths hit the same module-level guard.)
# ---------------------------------------------------------------------------


class TestWarnDeprecatedAllowedModelsGuard:
    """The module-level one-shot guard mirrors the precedent
    ``_reasoning_echo_deprecation_warned`` pattern. Tests reset the
    guard before each case so the test order doesn't matter.
    """

    def setup_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False

    def teardown_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False

    def _legacy_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "agentic,coding,coding2")
        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)

    def test_first_call_emits_warning(self, monkeypatch, caplog) -> None:
        """When the legacy var is set, the first call to
        ``warn_deprecated_allowed_models_env`` emits exactly one
        WARNING-level record naming the new env var.
        """
        from daemon.config import warn_deprecated_allowed_models_env

        self._legacy_present(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_deprecated_allowed_models_env()
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(records) == 1
        assert "OPENAI_ALLOWED_MODELS" in records[0].getMessage()
        assert "OPENAI_SELECTABLE_MODELS" in records[0].getMessage()

    def test_second_call_silent(self, monkeypatch, caplog) -> None:
        """The second call (e.g. from a second entry point — load_config
        AND api.py lifespan both invoke it) must emit nothing. The
        guard short-circuits before the os.environ check.
        """
        from daemon.config import warn_deprecated_allowed_models_env

        self._legacy_present(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_deprecated_allowed_models_env()  # first: fires
            warn_deprecated_allowed_models_env()  # second: silent
            warn_deprecated_allowed_models_env()  # third: still silent
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(records) == 1

    def test_no_warning_when_legacy_unset(
        self, monkeypatch, caplog
    ) -> None:
        """If the legacy var is NOT set (operator has already migrated,
        OR is using the new name), the function returns silently —
        no record emitted, guard stays at False so a later legacy-set
        scenario could still warn. Wait — in practice the guard is
        per-process and once the process has run a clean load_config,
        the operator shouldn't suddenly set the legacy var. But the
        guard semantics are: at most one warning per process, period.
        """
        from daemon.config import warn_deprecated_allowed_models_env

        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)
        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_deprecated_allowed_models_env()
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert records == []

    def test_guard_marks_true_after_firing(self, monkeypatch) -> None:
        """After the warning fires, the module-level guard is True
        so subsequent calls in the same process are silent.
        """
        import daemon.config as cfg
        from daemon.config import warn_deprecated_allowed_models_env

        self._legacy_present(monkeypatch)
        assert cfg._allowed_models_deprecation_warned is False
        warn_deprecated_allowed_models_env()
        assert cfg._allowed_models_deprecation_warned is True


# ---------------------------------------------------------------------------
# End-to-end integration via load_config — verifies the full path from
# os.environ through YAML interpolation through the resolver through
# LLMConfig.allowed_models. This is what the operator-facing behavior
# actually looks like in production.
# ---------------------------------------------------------------------------


class TestLoadConfigAllowedModelsIntegration:
    """End-to-end: ``load_config`` resolves the precedence chain and
    populates ``llm.allowed_models`` correctly for all three cases.
    Uses monkeypatch on os.environ to simulate launcher-exported .env
    values (matches the production deployment path — launcher.sh
    ``load_env_file`` exports the .env vars before running the binary).
    """

    def setup_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False

    def teardown_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False

    def test_new_var_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(a) New var wins — end-to-end via load_config."""
        monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "gpt-5,gpt-5o")
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)

        from daemon.config import load_config
        cfg = load_config()
        assert cfg.llm.allowed_models == ["gpt-5", "gpt-5o"]

    def test_old_var_honored_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """(b) Old var fallback — value honored AND warning fires
        exactly once (caplog proves only one WARNING record even though
        load_config + the resolver callback both invoke the warn fn).
        """
        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "agentic,coding,coding2")

        from daemon.config import load_config
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config()
        assert cfg.llm.allowed_models == [
            "agentic",
            "coding",
            "coding2",
        ]
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        legacy_records = [
            r for r in records if "OPENAI_ALLOWED_MODELS" in r.getMessage()
        ]
        assert len(legacy_records) == 1

    def test_neither_set_yields_documented_default(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """(c) Neither env var set → documented default
        ``[agentic, coding, coding2, vision]`` (sel-default-20260927).
        The shipped ``config.yaml`` inlines the same list at the
        interpolation site; the default-applied WARNING fires exactly
        once via the ``load_config`` wiring (caplog proof).
        """
        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)

        from daemon.config import load_config
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config()
        assert cfg.llm.allowed_models == [
            "agentic",
            "coding",
            "coding2",
            "vision",
        ]
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        # The legacy deprecation warning says "OPENAI_ALLOWED_MODELS is
        # set but renamed to OPENAI_SELECTABLE_MODELS ..." — the new
        # default-applied WARNING also mentions OPENAI_ALLOWED_MODELS
        # in its explanation but is distinguished by "default applied".
        # We assert NEITHER fires here: no legacy set → no deprecation
        # warning, AND the default-applied WARNING is exercised by the
        # dedicated TestDefaultAppliedWarningLoadConfig tests below.
        legacy_records = [
            r
            for r in records
            if "OPENAI_ALLOWED_MODELS" in r.getMessage()
            and "default applied" not in r.getMessage()
        ]
        assert legacy_records == []
        default_records = [
            r
            for r in records
            if "allowed_models default applied" in r.getMessage()
        ]
        assert len(default_records) == 1
        assert "vision" in default_records[0].getMessage()

    def test_both_set_new_wins_no_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Both vars set (operator partially migrated): new wins,
        no deprecation warning — operators who have set the new name
        should not be nagged about the old name being still on the
        machine.
        """
        monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "gpt-5")
        monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "agentic,coding,coding2")

        from daemon.config import load_config
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config()
        assert cfg.llm.allowed_models == ["gpt-5"]
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        # The legacy deprecation warning says "OPENAI_ALLOWED_MODELS is
        # set but renamed to OPENAI_SELECTABLE_MODELS ..." — the new
        # default-applied WARNING also mentions OPENAI_ALLOWED_MODELS
        # in its explanation but is distinguished by "default applied".
        # We assert NEITHER fires here: no legacy set → no deprecation
        # warning, AND the default-applied WARNING is exercised by the
        # dedicated TestDefaultAppliedWarningLoadConfig tests below.
        legacy_records = [
            r
            for r in records
            if "OPENAI_ALLOWED_MODELS" in r.getMessage()
            and "default applied" not in r.getMessage()
        ]
        assert legacy_records == []


# ---------------------------------------------------------------------------
# Reviewer findings #1 / #2 / #3 — empty-string env-var semantics. These
# cases cover the launcher.sh ``load_env_file`` path: a bare ``KEY=``
# line in ``.env`` is exported verbatim into ``os.environ`` as the
# empty string. The pre-fix resolver / warn chain read that as "set"
# and (a) silently produced an empty / unrestricted allowlist, OR
# (b) emitted a spurious one-shot deprecation warning from the explicit
# __main__.py / api.py call sites. Post-fix: empty / whitespace-only
# values are treated as UNSET for BOTH names, restoring the shell-style
# ``:-`` default-on-empty semantics that existed before the rename.
# ---------------------------------------------------------------------------


class TestEmptyStringResolverSemantics:
    """Pure-resolver coverage — empty / whitespace values for both
    env vars. Mirrors ``TestResolveAllowedModelsPure`` (no os.environ
    access, no module-level mutation).
    """

    def test_new_var_empty_string_treated_as_unset(self) -> None:
        """F2 core: ``OPENAI_SELECTABLE_MODELS=""`` must fall through
        to the documented default (``agentic,coding,coding2,vision``,
        sel-default-20260927) — NOT unrestricted (empty list).
        Operator-facing: a bare ``KEY=`` line in ``.env`` must never
        silently produce unrestricted mode.
        """
        from daemon.config import _resolve_allowed_models

        result = _resolve_allowed_models(
            "",
            new_var="",
            old_var=None,
            on_legacy=lambda: None,
        )
        assert result == "agentic,coding,coding2,vision"

    def test_old_var_empty_string_treated_as_unset(self) -> None:
        """F3 core: ``OPENAI_ALLOWED_MODELS=""`` must NOT trigger the
        warning callback (the pre-fix code did via the ``is not None``
        check). Empty legacy == UNSET == falls through to default.
        """
        from daemon.config import _resolve_allowed_models

        calls = []

        def on_legacy() -> None:
            calls.append(1)

        result = _resolve_allowed_models(
            "",
            new_var=None,
            old_var="",
            on_legacy=on_legacy,
        )
        assert result == "agentic,coding,coding2,vision"
        assert calls == []

    def test_new_var_empty_old_var_set_old_honored_with_callback(
        self,
    ) -> None:
        """Edge: ``OPENAI_SELECTABLE_MODELS=""`` but legacy set —
        legacy IS the effective source, must win AND fire the
        ``on_legacy`` callback. (This is the post-fix happy path for
        legacy-only deployments and must not have regressed.)
        """
        from daemon.config import _resolve_allowed_models

        calls = []

        def on_legacy() -> None:
            calls.append(1)

        result = _resolve_allowed_models(
            "",
            new_var="",
            old_var="legacy-only",
            on_legacy=on_legacy,
        )
        assert result == "legacy-only"
        assert calls == [1]

    def test_both_env_vars_empty_yields_default(self) -> None:
        """Both vars present-but-empty → both UNSET → documented
        default, no callback fired. (Defensive: even an operator
        who exported *both* bare ``KEY=`` lines should land on the
        default rather than a surprise.)
        """
        from daemon.config import _resolve_allowed_models

        calls = []

        def on_legacy() -> None:
            calls.append(1)

        result = _resolve_allowed_models(
            "",
            new_var="",
            old_var="",
            on_legacy=on_legacy,
        )
        assert result == "agentic,coding,coding2,vision"
        assert calls == []

    def test_whitespace_only_treated_as_unset(self) -> None:
        """Belt-and-braces: ``OPENAI_SELECTABLE_MODELS="   "`` or
        ``"\\t"`` (operator typo, or stray whitespace from a paste)
        must not be 'set-but-blank'; it falls through to the default
        like a true empty.
        """
        from daemon.config import _resolve_allowed_models

        result = _resolve_allowed_models(
            "",
            new_var="   ",
            old_var="\t  ",
            on_legacy=lambda: None,
        )
        assert result == "agentic,coding,coding2,vision"


class TestEmptyStringWarnSemantics:
    """Warn-function coverage for empty / whitespace values, plus
    the full-startup both-set simulation that the explicit
    ``__main__.py`` / ``api.py`` call sites exercise.
    """

    def _reset_guard(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False

    def setup_method(self) -> None:
        self._reset_guard()

    def teardown_method(self) -> None:
        self._reset_guard()

    def test_full_startup_path_no_warning_when_new_wins(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """F1 core: full-startup simulation — ``load_config`` (which
        internally invokes the resolver callback on the legacy
        branch) THEN explicit ``warn_deprecated_allowed_models_env``
        (the path taken by ``daemon/__main__.py`` /
        ``daemon/api.py``). When NEW wins, neither call must log —
        the warn function itself short-circuits on a winning new var
        (this is independent of the one-shot guard, which only fires
        after the env-check).
        """
        from daemon.config import (
            load_config,
            warn_deprecated_allowed_models_env,
        )

        monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "gpt-5")
        monkeypatch.setenv(
            "OPENAI_ALLOWED_MODELS", "agentic,coding,coding2"
        )

        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config()
            # Explicit second call — the ``__main__/api`` path. Even
            # with the guard still at False (load_config never fired
            # because new wins), this must NOT log.
            warn_deprecated_allowed_models_env()

        assert cfg.llm.allowed_models == ["gpt-5"]
        legacy_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "OPENAI_ALLOWED_MODELS" in r.getMessage()
        ]
        assert legacy_records == []

    def test_warning_fires_when_legacy_effective_with_empty_new(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Regression guard: when new is empty and legacy is non-empty,
        legacy IS the effective source → warning MUST fire. (This is
        the post-fix happy-path legacy deployment.)
        """
        from daemon.config import warn_deprecated_allowed_models_env

        monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "")
        monkeypatch.setenv(
            "OPENAI_ALLOWED_MODELS", "agentic,coding,coding2"
        )
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_deprecated_allowed_models_env()
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(records) == 1
        assert "OPENAI_ALLOWED_MODELS" in records[0].getMessage()

    def test_no_warning_when_legacy_empty_string(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """F1 core (single-call variant): ``OPENAI_ALLOWED_MODELS=""``
        (bare ``KEY=`` in .env) is treated as UNSET → no warning.
        Reproduces the spurious-warning case from the bug report.
        """
        from daemon.config import warn_deprecated_allowed_models_env

        monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "")
        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_deprecated_allowed_models_env()
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert records == []

    def test_no_warning_when_legacy_whitespace_only(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Whitespace-only legacy is treated as UNSET → no warning."""
        from daemon.config import warn_deprecated_allowed_models_env

        monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "   \t  ")
        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_deprecated_allowed_models_env()
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert records == []


class TestEmptyStringEndToEnd:
    """load_config coverage — full operator path with bare ``KEY=``
    in ``.env``, matching the launcher.sh ``load_env_file`` export
    semantics in production.
    """

    def setup_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False

    def teardown_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False

    def test_new_var_empty_string_yields_default_e2e(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """E2e (F2): ``OPENAI_SELECTABLE_MODELS=""`` falls through to
        the documented default (``agentic,coding,coding2,vision``,
        sel-default-20260927) — NOT unrestricted. The CSV/JSON
        validator downstream would otherwise turn ``""`` into ``[]``
        = unrestricted.
        """
        monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "")
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)

        from daemon.config import load_config
        cfg = load_config()
        assert cfg.llm.allowed_models == [
            "agentic",
            "coding",
            "coding2",
            "vision",
        ]

    def test_old_var_empty_string_yields_default_no_warning_e2e(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """E2e (F3 / F1): ``OPENAI_ALLOWED_MODELS=""`` falls through to
        the default AND does NOT emit the spurious warning the
        pre-fix ``is not None`` / ``not in os.environ`` check fired.
        """
        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "")

        from daemon.config import load_config
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config()
        assert cfg.llm.allowed_models == [
            "agentic",
            "coding",
            "coding2",
            "vision",
        ]
        legacy_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "OPENAI_ALLOWED_MODELS" in r.getMessage()
        ]
        assert legacy_records == []

    def test_new_empty_old_set_old_honored_with_warning_e2e(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """E2e (F1 happy-path regression): new empty but legacy set →
        legacy wins AND the deprecation warning fires exactly once.
        """
        monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "")
        monkeypatch.setenv(
            "OPENAI_ALLOWED_MODELS", "agentic,coding,coding2"
        )

        from daemon.config import load_config
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config()
        assert cfg.llm.allowed_models == [
            "agentic",
            "coding",
            "coding2",
        ]
        legacy_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "OPENAI_ALLOWED_MODELS" in r.getMessage()
        ]
        assert len(legacy_records) == 1


# ---------------------------------------------------------------------------
# sel-default-20260927 — Default-applied WARNING (the new emit-once surface
# that names the shipped default list when neither env var is set).
#
# Rationale: the v0.16.0 deploy blocked instance creation when a deployed
# env file had ``OPENAI_SELECTABLE_MODELS`` set WITHOUT ``vision``. The
# strict-when-set contract is ratified correct (exit-proof, do not weaken),
# but deployments with the var UNSET must ALSO include vision — that is the
# YAML interpolation default, and it is now the Python constant too. The
# default-applied WARNING tells operators (at startup, once per process)
# that the default fired and names the list, so a misconfigured deployment
# surfaces the issue before the first spawn fails downstream.
# ---------------------------------------------------------------------------


class TestDefaultAppliedWarning:
    """``warn_default_allowed_models_applied`` — emit-once per process
    when neither ``OPENAI_SELECTABLE_MODELS`` nor the legacy
    ``OPENAI_ALLOWED_MODELS`` is exported. Mirrors the
    ``warn_deprecated_allowed_models_env`` module-guard pattern.
    """

    def setup_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False
        cfg._default_allowed_models_warned = False

    def teardown_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False
        cfg._default_allowed_models_warned = False

    def test_first_call_emits_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """(a) Both env vars unset → the default-applied WARNING fires
        exactly once, names the default list, and notes ``vision``.
        """
        from daemon.config import warn_default_allowed_models_applied

        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_default_allowed_models_applied()
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        default_records = [
            r
            for r in records
            if "allowed_models default applied" in r.getMessage()
        ]
        assert len(default_records) == 1
        msg = default_records[0].getMessage()
        # Names the default list.
        assert "agentic" in msg
        assert "coding" in msg
        assert "coding2" in msg
        # Notes vision inclusion (operator-facing: vision-guard compat).
        assert "vision" in msg
        # Points to the override escape hatch.
        assert "OPENAI_SELECTABLE_MODELS" in msg

    def test_second_call_silent(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The module-level guard makes the second-and-subsequent
        calls silent — at most one record per process, regardless of
        how many entry points invoke the function.
        """
        from daemon.config import warn_default_allowed_models_applied

        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_default_allowed_models_applied()  # first: fires
            warn_default_allowed_models_applied()  # second: silent
            warn_default_allowed_models_applied()  # third: still silent
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        default_records = [
            r
            for r in records
            if "allowed_models default applied" in r.getMessage()
        ]
        assert len(default_records) == 1

    def test_no_warning_when_new_set(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """(b) ``OPENAI_SELECTABLE_MODELS`` set (even WITHOUT vision) →
        no default-applied WARNING. Strict-when-set is the ratified
        contract; the WARNING is a deployment-config hint, not a
        per-spawn behavior. Operators who set the var explicitly
        opt out of the hint.
        """
        from daemon.config import warn_default_allowed_models_applied

        # Explicit list WITHOUT vision — this would fail the vision-guard
        # downstream, but the WARNING here is for the unresolved default
        # case only. The set-var path bypasses it.
        monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "agentic,coding")
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_default_allowed_models_applied()
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        default_records = [
            r
            for r in records
            if "allowed_models default applied" in r.getMessage()
        ]
        assert default_records == []

    def test_no_warning_when_only_legacy_set(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When ONLY the legacy alias is set, the legacy value is the
        effective source — the default does NOT apply, so no
        default-applied WARNING. The legacy deprecation WARNING still
        fires on its own emit-once path.
        """
        from daemon.config import warn_default_allowed_models_applied

        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "agentic,coding,coding2")
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_default_allowed_models_applied()
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        default_records = [
            r
            for r in records
            if "allowed_models default applied" in r.getMessage()
        ]
        assert default_records == []

    def test_no_warning_when_both_env_empty(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Bare ``KEY=`` lines (empty / whitespace-only) for BOTH vars
        are treated as UNSET — the default applies AND the WARNING
        fires (this is the v0.16.0 incident shape: operator typo'd
        the env var, the default saved them, but they should know).
        """
        from daemon.config import warn_default_allowed_models_applied

        monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "")
        monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "")
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            warn_default_allowed_models_applied()
        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        default_records = [
            r
            for r in records
            if "allowed_models default applied" in r.getMessage()
        ]
        assert len(default_records) == 1

    def test_guard_marks_true_after_firing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After the warning fires, the module-level guard is True so
        subsequent calls in the same process are silent — preventing
        the duplicate-record scenario where ``load_config`` AND the
        ``__main__/api`` startup wiring both invoke the function.
        """
        import daemon.config as cfg
        from daemon.config import warn_default_allowed_models_applied

        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)
        assert cfg._default_allowed_models_warned is False
        warn_default_allowed_models_applied()
        assert cfg._default_allowed_models_warned is True


class TestDefaultAppliedWarningLoadConfig:
    """(a) End-to-end via ``load_config`` — the deployment-facing path.
    Verifies that the default-applied WARNING is fired from the
    ``load_config`` call site (NOT only via the explicit startup
    wiring) so the operator sees it even on processes that go
    straight through ``load_config`` without entering
    ``__main__`` / ``api``.
    """

    def setup_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False
        cfg._default_allowed_models_warned = False

    def teardown_method(self) -> None:
        import daemon.config as cfg
        cfg._allowed_models_deprecation_warned = False
        cfg._default_allowed_models_warned = False

    def test_load_config_emits_warning_when_both_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """(a) end-to-end: ``load_config`` with both env vars unset →
        allowed_models == shipped default AND exactly one
        default-applied WARNING in the caplog.
        """
        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)

        from daemon.config import load_config
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config()

        # The shipped default (YAML interpolation literal) — vision
        # is present so designer-agent vision-guard resolves.
        assert cfg.llm.allowed_models == [
            "agentic",
            "coding",
            "coding2",
            "vision",
        ]
        default_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "allowed_models default applied" in r.getMessage()
        ]
        assert len(default_records) == 1
        # No spurious legacy deprecation warning.
        legacy_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "OPENAI_ALLOWED_MODELS" in r.getMessage()
            and "default applied" not in r.getMessage()
        ]
        assert legacy_records == []

    def test_load_config_emits_warning_exactly_once_across_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """(a) module-guard: ``load_config()`` called twice on the
        same process still emits exactly ONE default-applied
        WARNING. Pin test for the cross-call guard.
        """
        monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)

        from daemon.config import load_config
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            load_config()
            load_config()  # second call: guard short-circuits

        default_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "allowed_models default applied" in r.getMessage()
        ]
        assert len(default_records) == 1

    def test_load_config_no_warning_when_explicit_with_vision(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """(c) Explicit ``OPENAI_SELECTABLE_MODELS`` including
        ``vision`` → strict-when-set, default does NOT apply, NO
        default-applied WARNING. ``vision`` resolves cleanly through
        the spawn-time validation chain.
        """
        monkeypatch.setenv(
            "OPENAI_SELECTABLE_MODELS", "agentic,coding,coding2,vision"
        )
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)

        from daemon.config import load_config
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config()

        assert cfg.llm.allowed_models == [
            "agentic",
            "coding",
            "coding2",
            "vision",
        ]
        # The default-applied WARNING must NOT fire when the env var
        # is set (regardless of whether vision is in the list).
        default_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "allowed_models default applied" in r.getMessage()
        ]
        assert default_records == []

    def test_load_config_no_warning_when_explicit_without_vision(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """(b) Explicit ``OPENAI_SELECTABLE_MODELS`` WITHOUT vision →
        strict-when-set preserved (the v0.16.0 incident-shape path).
        No default-applied WARNING; the strict behavior raises
        downstream at the vision-guard. The exit-proof contract is
        ratified — do not weaken it.
        """
        monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "agentic,coding")
        monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)

        from daemon.config import load_config
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config()

        assert cfg.llm.allowed_models == ["agentic", "coding"]
        # No default-applied WARNING (the explicit set opts out).
        default_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "allowed_models default applied" in r.getMessage()
        ]
        assert default_records == []


class TestDefaultConsistencyPin:
    """(d) Drift-detector pin: ``_ALLOWED_MODELS_DEFAULT`` in
    ``daemon/config.py`` MUST match the ``config.yaml`` interpolation
    literal byte-for-byte. This test fails loudly if either side is
    edited without the other — the v0.16.0 drift (YAML had vision,
    constant did not) is the regression we are guarding against.

    Parsing strategy: read ``config.yaml`` raw text, regex-match the
    ``allowed_models:`` line, extract the
    ``${OPENAI_SELECTABLE_MODELS:-<value>}`` fallback. The fallback
    is the canonical YAML-layer default. We assert the comma-joined
    ``_ALLOWED_MODELS_DEFAULT`` tuple matches the fallback.
    """

    def test_yaml_literal_matches_constant(self) -> None:
        import re
        from pathlib import Path

        from daemon.config import _ALLOWED_MODELS_DEFAULT

        config_yaml = (
            Path(__file__).resolve().parents[2] / "config.yaml"
        )
        assert config_yaml.is_file(), (
            f"config.yaml not found at {config_yaml} — drift test "
            "needs the repo-root YAML to extract the literal default"
        )
        text = config_yaml.read_text(encoding="utf-8")
        # Match the allowed_models interpolation line. The value is
        # anchored to ``${OPENAI_SELECTABLE_MODELS:-<FALLBACK>}``.
        match = re.search(
            r"^\s*allowed_models:\s*\$\{OPENAI_SELECTABLE_MODELS:-([^}]+)\}\s*$",
            text,
            re.MULTILINE,
        )
        assert match is not None, (
            "Could not find the allowed_models interpolation line in "
            "config.yaml — drift test needs the standard "
            "${OPENAI_SELECTABLE_MODELS:-...} form to extract the literal"
        )
        yaml_default_csv = match.group(1).strip()
        yaml_default_tuple = tuple(
            m.strip() for m in yaml_default_csv.split(",") if m.strip()
        )
        assert _ALLOWED_MODELS_DEFAULT == yaml_default_tuple, (
            f"DRIFT: _ALLOWED_MODELS_DEFAULT {_ALLOWED_MODELS_DEFAULT} "
            f"!= config.yaml interpolation default {yaml_default_tuple}. "
            "Both homes of the shipped default must stay aligned — "
            "update both daemon/config.py:_ALLOWED_MODELS_DEFAULT AND "
            "config.yaml:87 in the same change. See the "
            "sel-default-20260927 commission."
        )
