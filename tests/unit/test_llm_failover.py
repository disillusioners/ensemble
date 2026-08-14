"""Tests for LLM provider HA auto-fallback (LLM-HA).

Covers:
  - ``FailoverController`` swap / reset semantics
  - ``_make_llm_retry_strategy`` budget-split between primary and backup
  - Zero behavior change when no backup is configured
  - Idempotent / non-sticky behavior across invoke cycles
  - IndexError retry-with-failover path
  - Auth / 400 / context-length non-retry paths unchanged

Companion module: ``daemon.llm_error_classifier`` (the production
code under test).
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import openai
import pytest

from daemon.llm_error_classifier import (
    FailoverController,
    PRIMARY_TIMEOUT_MAX,
    PRIMARY_TRANSIENT_MAX,
    _make_llm_retry_strategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_retry_state(exception, attempt_number=1):
    """Build a tenacity RetryCallState mock with the given exception/attempt."""
    from tenacity import RetryCallState

    outcome = MagicMock()
    outcome.exception.return_value = exception

    state = MagicMock(spec=RetryCallState)
    state.outcome = outcome
    state.attempt_number = attempt_number
    return state


def _make_fake_chat_client(primary_url: str):
    """Build a stand-in shaped like ``langchain.ChatOpenAI``.

    Uses REAL ``openai.OpenAI`` / ``openai.AsyncOpenAI`` clients (no
    network is involved — construction only). This models the actual
    SDK invariant the production code depends on: ``base_url`` is a
    property backed by ``_base_url: httpx.URL``, and the public setter
    normalises through ``URL()`` + ``_enforce_trailing_slash``.

    The earlier version of this helper mocked ``_base_url`` as a plain
    ``str`` — the exact wrong invariant that hid the raw-assignment
    bug (``'str' object has no attribute 'raw_path'`` on the next
    request build). Real clients make that class of bug impossible to
    miss here again.

    Assertions on the resulting URL must be trailing-slash aware
    (``URL('https://backup/v1/')``).
    """
    sync = openai.OpenAI(api_key="test-key", base_url=primary_url)
    async_c = openai.AsyncOpenAI(api_key="test-key", base_url=primary_url)

    return SimpleNamespace(root_client=sync, root_async_client=async_c)


def _transient_error():
    """Return an openai.APIConnectionError (in TRANSIENT_EXCEPTIONS)."""
    return openai.APIConnectionError(message="boom", request=MagicMock())


def _timeout_error():
    """Return an openai.APITimeoutError (in TIMEOUT_EXCEPTIONS)."""
    return openai.APITimeoutError(request=MagicMock())


def _index_error():
    """Return the empty-choices IndexError the daemon sees in prod."""
    return IndexError("list index out of range")


# ---------------------------------------------------------------------------
# FailoverController — URL swap mechanics
# ---------------------------------------------------------------------------


class TestFailoverControllerBasics:
    """Direct unit tests of the controller (no retry strategy)."""

    def test_swap_to_backup_mutates_root_client_base_url(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        assert chat.root_client.base_url == "https://primary/v1/"
        ctl.swap_to_backup()
        assert chat.root_client.base_url == "https://backup/v1/"

    def test_swap_to_backup_mutates_async_client_base_url(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        ctl.swap_to_backup()
        assert chat.root_async_client.base_url == "https://backup/v1/"

    def test_reset_to_primary_restores_url(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        ctl.swap_to_backup()
        ctl.reset_to_primary()
        assert chat.root_client.base_url == "https://primary/v1/"

    def test_swap_is_idempotent(self):
        """Calling swap_to_backup twice doesn't re-mutate or double-log.

        The retry predicate may fire multiple times in a cycle. The
        controller must not "swap back to backup" repeatedly — that
        would generate a log line per predicate call (which would be
        spam).
        """
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        ctl.swap_to_backup()
        url_after_first = chat.root_client.base_url
        ctl.swap_to_backup()  # idempotent
        assert chat.root_client.base_url == url_after_first

    def test_reset_when_on_primary_is_noop(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        # Already on primary; reset must be a no-op.
        ctl.reset_to_primary()
        assert chat.root_client.base_url == "https://primary/v1/"

    def test_is_configured_true_when_backup_differs(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        assert ctl.is_configured is True

    def test_is_configured_false_when_backup_missing(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", None)
        assert ctl.is_configured is False

    def test_is_configured_false_when_backup_same_as_primary(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://primary/v1")
        assert ctl.is_configured is False

    def test_swap_handles_missing_root_client_gracefully(self):
        """If the langchain client has no ``root_client`` (older SDK or
        stripped-down test double), swap must NOT raise — log at DEBUG
        and continue."""
        chat = MagicMock(spec=[])  # no root_client / root_async_client attrs
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        # Must not raise.
        ctl.swap_to_backup()
        ctl.reset_to_primary()

    def test_failover_summary_contains_both_urls(self, caplog):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        summary = ctl.failover_summary()
        assert "primary=https://primary/v1" in summary
        assert "backup=https://backup/v1" in summary


# ---------------------------------------------------------------------------
# _make_llm_retry_strategy — without controller (zero behavior change)
# ---------------------------------------------------------------------------


class TestNoBackupUnchangedBehavior:
    """With ``failover_controller=None`` the predicate must behave
    identically to the pre-HA system.

    These tests guard the "zero behavior change when OPENAI_BASE_URL_BACKUP
    unset" invariant — if any of them break, a regression has been
    introduced.
    """

    def test_transient_counted_against_full_budget(self):
        """Transient errors: count up to ``transient_max`` (full budget)."""
        strategy = _make_llm_retry_strategy(transient_max=3, timeout_max=2)
        e = _transient_error()

        # attempts 1,2 → True; attempt 3 → False
        for attempt, expected in [(1, True), (2, True), (3, False), (4, False)]:
            state = _make_mock_retry_state(e, attempt_number=attempt)
            assert strategy(state) is expected, (
                f"transient attempt {attempt} → expected {expected}"
            )

    def test_timeout_counted_against_full_budget(self):
        """Timeout errors: count up to ``timeout_max`` (full budget)."""
        strategy = _make_llm_retry_strategy(transient_max=10, timeout_max=2)
        e = _timeout_error()

        for attempt, expected in [(1, True), (2, False), (3, False)]:
            state = _make_mock_retry_state(e, attempt_number=attempt)
            assert strategy(state) is expected, (
                f"timeout attempt {attempt} → expected {expected}"
            )

    def test_index_error_not_retried(self):
        """IndexError is non-retryable in the pre-HA path.

        Spec: empty-choices IndexError is treated as retryable-with-failover
        ONLY when a backup is configured. With no backup the original
        behavior (short-circuit to upstream error pipeline) is preserved.
        """
        strategy = _make_llm_retry_strategy(transient_max=5, timeout_max=5)
        state = _make_mock_retry_state(_index_error(), attempt_number=1)
        assert strategy(state) is False

    def test_auth_error_not_retried(self):
        """AuthenticationError (401) must NOT retry — same key on both
        endpoints means it would fail identically."""
        strategy = _make_llm_retry_strategy(transient_max=10, timeout_max=10)
        e = openai.AuthenticationError(
            message="Invalid API key",
            response=MagicMock(),
            body=None,
        )
        state = _make_mock_retry_state(e, attempt_number=1)
        assert strategy(state) is False

    def test_non_retryable_status_not_retried(self):
        """A 400 BadRequestError (non-context-length) is non-retryable."""
        strategy = _make_llm_retry_strategy(transient_max=10, timeout_max=10)
        e = openai.BadRequestError(
            message="Bad request",
            response=MagicMock(),
            body=None,
        )
        state = _make_mock_retry_state(e, attempt_number=1)
        assert strategy(state) is False

    def test_counters_reset_between_cycles(self):
        """The pre-HA reset semantics must survive — attempt_number=1
        clears both counters."""
        strategy = _make_llm_retry_strategy(transient_max=3, timeout_max=2)

        # Exhaust transient retries
        e = _transient_error()
        for n in range(1, 4):
            state = _make_mock_retry_state(e, attempt_number=n)
            strategy(state)

        # Next cycle: attempt_number=1 → counters reset → retry again
        state = _make_mock_retry_state(e, attempt_number=1)
        assert strategy(state) is True


# ---------------------------------------------------------------------------
# _make_llm_retry_strategy — WITH controller (HA budget-split)
# ---------------------------------------------------------------------------


class TestFailoverBudgetSplit:
    """With ``failover_controller`` set, primary gets a small slice and
    swap-to-backup grants the FULL original budget."""

    def _build(self, primary_transient_max=3, primary_timeout_max=2,
               transient_max=8, timeout_max=3):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(
            chat, "https://primary/v1", "https://backup/v1"
        )
        strategy = _make_llm_retry_strategy(
            transient_max=transient_max,
            timeout_max=timeout_max,
            failover_controller=ctl,
            primary_transient_max=primary_transient_max,
            primary_timeout_max=primary_timeout_max,
        )
        return strategy, ctl, chat

    def test_primary_slice_transient(self):
        """Transient on primary: 2 retries on primary (slice), then swap,
        backup gets full 8 retries.

        ``primary_transient_max=3`` means: predicate returns True while
        count < 3 (i.e. on attempts 1 and 2); on attempt 3, swap fires
        and counter resets so backup gets the FULL ``transient_max=8``.
        """
        strategy, ctl, chat = self._build()
        e = _transient_error()

        # Primary attempts 1, 2 → True (still on primary, count < 3)
        for n in (1, 2):
            state = _make_mock_retry_state(e, attempt_number=n)
            assert strategy(state) is True, f"attempt {n} should be retryable on primary"
            assert chat.root_client.base_url == "https://primary/v1/"

        # Attempt 3: count == primary_transient_max → swap to backup, reset.
        state = _make_mock_retry_state(e, attempt_number=3)
        assert strategy(state) is True
        assert chat.root_client.base_url == "https://backup/v1/"

        # Now on backup — full 8 budget. Counter starts at 1 (post-reset)
        # and counts up to 8. Predicate True for counts 1..7 (attempts
        # 4..10). False at count 8 (attempt 11).
        for n in range(4, 11):
            state = _make_mock_retry_state(e, attempt_number=n)
            assert strategy(state) is True, (
                f"backup attempt {n} should be retryable "
                f"(count={n-3}, < full_budget 8)"
            )

        # Backup exhausted (count == 8).
        state = _make_mock_retry_state(e, attempt_number=11)
        assert strategy(state) is False

    def test_primary_slice_timeout(self):
        """Timeout on primary: 1 retry on primary (slice), then swap.

        ``primary_timeout_max=2`` means: predicate returns True on
        attempt 1 (count < 2). Attempt 2 swaps to backup.
        """
        strategy, ctl, chat = self._build()
        e = _timeout_error()

        # Primary attempt 1 → True (still on primary)
        state = _make_mock_retry_state(e, attempt_number=1)
        assert strategy(state) is True
        assert chat.root_client.base_url == "https://primary/v1/"

        # Attempt 2: count == primary_timeout_max → swap, reset, retry.
        state = _make_mock_retry_state(e, attempt_number=2)
        assert strategy(state) is True
        assert chat.root_client.base_url == "https://backup/v1/"

        # Now on backup — full 3 timeout budget (counts 1, 2 → True; 3 → False).
        for n in range(3, 5):
            state = _make_mock_retry_state(e, attempt_number=n)
            assert strategy(state) is True, f"backup timeout attempt {n} should retry"

        # Backup exhausted.
        state = _make_mock_retry_state(e, attempt_number=5)
        assert strategy(state) is False

    def test_index_error_triggers_failover_on_primary(self):
        """IndexError (empty choices[]) is treated as transient under
        failover — primary slice of 2 retries, then swap to backup."""
        strategy, ctl, chat = self._build()
        e = _index_error()

        # Primary attempts 1, 2 → retry on primary.
        for n in (1, 2):
            state = _make_mock_retry_state(e, attempt_number=n)
            assert strategy(state) is True, f"IndexError attempt {n} should retry"
            assert chat.root_client.base_url == "https://primary/v1/"

        # Attempt 3: swap to backup.
        state = _make_mock_retry_state(e, attempt_number=3)
        assert strategy(state) is True
        assert chat.root_client.base_url == "https://backup/v1/"

    def test_index_error_does_not_retry_without_controller(self):
        """No backup configured → IndexError still non-retryable.

        This is the invariant for "no behavior change when backup unset".
        Already covered in TestNoBackupUnchangedBehavior but reaffirmed
        here in the failover-aware section.
        """
        strategy = _make_llm_retry_strategy(transient_max=5, timeout_max=5)
        state = _make_mock_retry_state(_index_error(), attempt_number=1)
        assert strategy(state) is False

    def test_swap_log_emits_warning_once_per_failover(self, caplog):
        """One greppable ``[LLM-HA]`` WARNING per failover event — not
        per predicate call (would spam)."""
        strategy, ctl, chat = self._build()
        e = _transient_error()

        with caplog.at_level(logging.WARNING, logger="daemon.llm_error_classifier"):
            # Attempts 1, 2 on primary, attempt 3 swaps
            state = _make_mock_retry_state(e, attempt_number=1)
            strategy(state)
            state = _make_mock_retry_state(e, attempt_number=2)
            strategy(state)
            state = _make_mock_retry_state(e, attempt_number=3)
            strategy(state)  # <-- swap fires here, exactly one WARNING
            # Subsequent calls on backup: no further warnings.
            state = _make_mock_retry_state(e, attempt_number=4)
            strategy(state)
            state = _make_mock_retry_state(e, attempt_number=5)
            strategy(state)

        ha_lines = [r for r in caplog.records
                    if r.levelname == "WARNING" and "[LLM-HA]" in r.getMessage()]
        assert len(ha_lines) == 1, (
            f"Expected exactly one [LLM-HA] WARNING per failover, got "
            f"{len(ha_lines)}: {[r.getMessage() for r in ha_lines]}"
        )
        assert "primary=https://primary/v1" in ha_lines[0].getMessage()
        assert "backup=https://backup/v1" in ha_lines[0].getMessage()

    def test_swap_summary_visible_in_log(self, caplog):
        strategy, ctl, chat = self._build()
        e = _transient_error()

        with caplog.at_level(logging.WARNING, logger="daemon.llm_error_classifier"):
            for n in (1, 2, 3):
                strategy(_make_mock_retry_state(e, attempt_number=n))

        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "primary=" in msg and "backup=" in msg

    def test_auth_error_skips_failover(self):
        """AuthError (401) must NOT trigger a swap — same key on both
        endpoints means it would fail identically and waste the backup."""
        strategy, ctl, chat = self._build()
        e = openai.AuthenticationError(
            message="Invalid API key",
            response=MagicMock(),
            body=None,
        )
        state = _make_mock_retry_state(e, attempt_number=1)
        assert strategy(state) is False
        # URL unchanged.
        assert chat.root_client.base_url == "https://primary/v1/"

    def test_non_retryable_status_skips_failover(self):
        """BadRequestError (400) must NOT trigger a swap."""
        strategy, ctl, chat = self._build()
        e = openai.BadRequestError(
            message="Bad request",
            response=MagicMock(),
            body=None,
        )
        state = _make_mock_retry_state(e, attempt_number=1)
        assert strategy(state) is False
        assert chat.root_client.base_url == "https://primary/v1/"


# ---------------------------------------------------------------------------
# Non-sticky: each invoke starts on primary
# ---------------------------------------------------------------------------


class TestNonStickyBehavior:
    """The retry predicate's ``attempt_number=1`` reset must also call
    ``reset_to_primary`` on the controller so the next invoke cycle
    starts on primary regardless of where the previous one ended.

    Spec: "Each invoke starts on primary. Self-healing — when primary
    DC recovers, next invoke uses it again. No cross-invoke persistence."
    """

    def test_reset_to_primary_called_at_cycle_start(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        strategy = _make_llm_retry_strategy(
            transient_max=8, timeout_max=3, failover_controller=ctl,
            primary_transient_max=3, primary_timeout_max=2,
        )

        # Simulate cycle 1: swap to backup (at attempt 3 with primary slice=2)
        e = _transient_error()
        for n in (1, 2, 3):
            strategy(_make_mock_retry_state(e, attempt_number=n))
        assert chat.root_client.base_url == "https://backup/v1/"

        # Cycle 2 begins: attempt_number resets to 1 → URL must reset too
        strategy(_make_mock_retry_state(e, attempt_number=1))
        assert chat.root_client.base_url == "https://primary/v1/"

    def test_reset_to_primary_not_called_when_no_failover(self):
        """When no controller is supplied, the reset path is a no-op and
        the predicate must not AttributeError."""
        strategy = _make_llm_retry_strategy(transient_max=3, timeout_max=2)
        # Should not raise.
        e = _transient_error()
        for n in (1, 2, 3, 4):
            strategy(_make_mock_retry_state(e, attempt_number=n))

    def test_independent_invoke_cycles_each_start_on_primary(self):
        """Two consecutive cycles that hit transient errors: each cycle
        starts on primary; if it fails, swaps; next cycle resets."""
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        # primary_transient_max=3 → 2 retries on primary; full budget 4
        strategy = _make_llm_retry_strategy(
            transient_max=4, timeout_max=2, failover_controller=ctl,
            primary_transient_max=3, primary_timeout_max=2,
        )

        e = _transient_error()

        # Cycle 1: primary fails → swap → backup (at attempt 3)
        for n in (1, 2, 3):
            strategy(_make_mock_retry_state(e, attempt_number=n))
        assert chat.root_client.base_url == "https://backup/v1/"

        # Cycle 2 begins: attempt_number=1 → URL must reset
        strategy(_make_mock_retry_state(e, attempt_number=1))
        assert chat.root_client.base_url == "https://primary/v1/"


# ---------------------------------------------------------------------------
# Counters reset after URL swap
# ---------------------------------------------------------------------------


class TestCountersResetAfterSwap:
    """Spec: "Counters reset after URL swap."

    After the swap fires, the predicate must allow the FULL budget on
    the backup — i.e. counters must be zeroed. This is verified by
    driving enough attempts on the backup to confirm the FULL budget
    is in effect (not a "remaining" budget).
    """

    def test_backup_gets_full_transient_budget_after_swap(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        # Full budget = 5 transient; primary slice = primary_transient_max=3.
        strategy = _make_llm_retry_strategy(
            transient_max=5, timeout_max=3, failover_controller=ctl,
            primary_transient_max=3, primary_timeout_max=2,
        )

        e = _transient_error()

        # Cycle through: attempts 1,2 on primary, attempt 3 swaps to backup
        for n in (1, 2, 3):
            strategy(_make_mock_retry_state(e, attempt_number=n))

        # We are on backup now. Backup should get the FULL 5 transient
        # budget (not 5-3 = 2 "remaining"). Counter starts at 1 after
        # swap and counts up.
        for n in range(4, 8):  # attempts 4-7 → counts 1-4 on backup, all <5
            assert strategy(_make_mock_retry_state(e, attempt_number=n)) is True

        # Attempt 8: count == 5, 5 < 5 = False → exhausted on backup.
        assert strategy(_make_mock_retry_state(e, attempt_number=8)) is False

    def test_no_double_swap(self):
        """After the swap fires once, subsequent transient errors on
        backup must NOT trigger another swap. (Swapped flag is sticky.)"""
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        strategy = _make_llm_retry_strategy(
            transient_max=8, timeout_max=3, failover_controller=ctl,
            primary_transient_max=3, primary_timeout_max=2,
        )

        e = _transient_error()
        # First swap (at attempt 3)
        for n in (1, 2, 3):
            strategy(_make_mock_retry_state(e, attempt_number=n))
        assert chat.root_client.base_url == "https://backup/v1/"

        # Many more transient errors on backup — should not swap back
        for n in range(4, 10):
            strategy(_make_mock_retry_state(e, attempt_number=n))
        # Still on backup.
        assert chat.root_client.base_url == "https://backup/v1/"


# ---------------------------------------------------------------------------
# LLMConfig base_url_backup field
# ---------------------------------------------------------------------------


class TestLLMConfigBackupField:
    """The new ``base_url_backup`` field on ``LLMConfig`` must default to
    ``None`` and respect env-var wiring via ``OPENAI_BASE_URL_BACKUP``."""

    def test_default_is_none(self, monkeypatch):
        monkeypatch.delenv("OPENAI_BASE_URL_BACKUP", raising=False)
        from daemon.config import LLMConfig
        cfg = LLMConfig()
        assert cfg.base_url_backup is None

    def test_env_var_picked_up(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL_BACKUP", "https://backup.example/v1")
        from daemon.config import LLMConfig
        cfg = LLMConfig()
        assert cfg.base_url_backup == "https://backup.example/v1"

    def test_empty_string_env_coerced_to_none(self, monkeypatch):
        """When ``config.yaml`` substitutes ``${OPENAI_BASE_URL_BACKUP:-}``
        to an empty string, the field validator must coerce to ``None``
        so the failover logic correctly takes the "no backup" branch."""
        monkeypatch.setenv("OPENAI_BASE_URL_BACKUP", "")
        from daemon.config import LLMConfig
        cfg = LLMConfig()
        assert cfg.base_url_backup is None

    def test_whitespace_only_env_coerced_to_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL_BACKUP", "   ")
        from daemon.config import LLMConfig
        cfg = LLMConfig()
        assert cfg.base_url_backup is None

    def test_loaded_from_yaml_with_explicit_value(self, tmp_path, monkeypatch):
        """When config.yaml sets base_url_backup to a real URL, the
        loaded Config has it populated."""
        from daemon.config import load_config

        # Need to unset env so the YAML's value isn't shadowed
        monkeypatch.delenv("OPENAI_BASE_URL_BACKUP", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(
            "llm:\n"
            "  base_url: 'https://primary.example/v1'\n"
            "  base_url_backup: 'https://backup.example/v1'\n"
            "  api_key: 'sk-test'\n"
            "  model: 'gpt-4'\n"
        )
        cfg = load_config(str(yaml_path))
        assert cfg.llm.base_url == "https://primary.example/v1"
        assert cfg.llm.base_url_backup == "https://backup.example/v1"

    def test_yaml_empty_string_coerced_to_none(self, tmp_path, monkeypatch):
        """When config.yaml sets base_url_backup to '' (the env-var
        substitution default), the field validator coerces it to None."""
        from daemon.config import load_config

        monkeypatch.delenv("OPENAI_BASE_URL_BACKUP", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(
            "llm:\n"
            "  base_url: 'https://primary.example/v1'\n"
            "  base_url_backup: ''\n"
            "  api_key: 'sk-test'\n"
            "  model: 'gpt-4'\n"
        )
        cfg = load_config(str(yaml_path))
        assert cfg.llm.base_url_backup is None

class TestBuildInstanceLLMSFailoverWiring:
    """Integration-level: verify ``build_instance_llms`` correctly wires
    the FailoverController when ``base_url_backup`` is configured, and
    does NOT wire it when ``base_url_backup`` is absent."""

    def _make_mock_chat_openai(self, root_url):
        """Return a stand-in that mimics a ThinkingChatOpenAI instance.

        Uses REAL ``openai.OpenAI`` / ``openai.AsyncOpenAI`` clients for
        ``root_client`` / ``root_async_client`` (construction only — no
        network) so the ``FailoverController`` operates on the genuine
        SDK ``base_url`` property invariant rather than a ``str``
        attribute, mirroring what langchain_openai builds at runtime.
        """
        sync = openai.OpenAI(api_key="test-key", base_url=root_url)
        async_c = openai.AsyncOpenAI(api_key="test-key", base_url=root_url)

        chat = MagicMock()
        chat.root_client = sync
        chat.root_async_client = async_c
        return chat

    def test_no_backup_does_not_invoke_failover_controller(self):
        """When ``base_url_backup`` is None, ``build_instance_llms`` must
        pass ``failover_controller=None`` to ``_make_llm_retry_strategy``.

        We assert by patching ``_make_llm_retry_strategy`` (imported
        locally inside ``build_instance_llms`` from
        ``daemon.llm_error_classifier``) and checking the
        ``failover_controller`` kwarg.
        """
        from unittest.mock import MagicMock, patch
        from daemon.graph import build_instance_llms

        with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class:
            # Each call to ThinkingChatOpenAI returns a fresh mock with a
            # bind_tools that yields a separate bound runnable.
            mock_chat = self._make_mock_chat_openai("https://primary/v1")
            bound_a = MagicMock()
            bound_b = MagicMock()
            mock_chat.bind_tools.side_effect = [bound_a, bound_b]
            mock_llm_class.return_value = mock_chat

            with patch("daemon.graph.classify_llm_errors") as mock_classify:
                # classify_llm_errors is identity in this test (we do not
                # exercise the runtime exception path here).
                mock_classify.side_effect = lambda x: x
                # _make_llm_retry_strategy is imported INSIDE
                # build_instance_llms (local import to avoid the graph ↔
                # services cycle), so patch it on its source module.
                with patch("daemon.llm_error_classifier._make_llm_retry_strategy") as mock_strategy, \
                     patch("daemon.graph.Retrying"):
                    mock_strategy.return_value = MagicMock()

                    build_instance_llms(
                        llm_config_with_headers={
                            "base_url": "https://primary/v1",
                            "base_url_backup": None,
                            "api_key": "test",
                            "model": "gpt-4",
                            "model_vision": None,
                            "default_headers": {},
                        },
                        model_standard="gpt-4",
                        model_vision=None,
                        tools=[],
                        retry_config={"transient_attempts": 8, "timeout_attempts": 3},
                    )

                    # failover_controller must be None (no backup).
                    kwargs = mock_strategy.call_args.kwargs
                    assert kwargs["failover_controller"] is None

    def test_with_backup_invokes_failover_controller(self):
        """When ``base_url_backup`` is set, ``build_instance_llms`` must
        pass a real FailoverController to ``_make_llm_retry_strategy``."""
        from unittest.mock import MagicMock, patch
        from daemon.llm_error_classifier import FailoverController
        from daemon.graph import build_instance_llms

        with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class:
            mock_chat = self._make_mock_chat_openai("https://primary/v1")
            bound_a = MagicMock()
            bound_b = MagicMock()
            mock_chat.bind_tools.side_effect = [bound_a, bound_b]
            mock_llm_class.return_value = mock_chat

            with patch("daemon.graph.classify_llm_errors") as mock_classify:
                mock_classify.side_effect = lambda x: x
                with patch("daemon.llm_error_classifier._make_llm_retry_strategy") as mock_strategy, \
                     patch("daemon.graph.Retrying"):
                    mock_strategy.return_value = MagicMock()

                    build_instance_llms(
                        llm_config_with_headers={
                            "base_url": "https://primary/v1",
                            "base_url_backup": "https://backup/v1",
                            "api_key": "test",
                            "model": "gpt-4",
                            "model_vision": None,
                            "default_headers": {},
                        },
                        model_standard="gpt-4",
                        model_vision=None,
                        tools=[],
                        retry_config={"transient_attempts": 8, "timeout_attempts": 3},
                    )

                    # failover_controller must be a FailoverController.
                    kwargs = mock_strategy.call_args.kwargs
                    ctl = kwargs["failover_controller"]
                    assert ctl is not None
                    assert isinstance(ctl, FailoverController)

    def test_with_backup_extends_max_attempts(self):
        """When ``base_url_backup`` is set, ``build_instance_llms`` must
        pass a higher ``stop=stop_after_attempt(N)`` so tenacity does not
        terminate mid-cycle after the swap.

        Exact value, not a slack bound: with defaults
        ``transient=8, timeout=3`` and primary caps 3/2, the ceiling is
        ``max(8, 3) + max(3, 2) = 11``.
        """
        from unittest.mock import MagicMock, patch
        from daemon.llm_error_classifier import (
            PRIMARY_TIMEOUT_MAX,
            PRIMARY_TRANSIENT_MAX,
        )
        from daemon.graph import build_instance_llms

        with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class:
            mock_chat = self._make_mock_chat_openai("https://primary/v1")
            bound_a = MagicMock()
            bound_b = MagicMock()
            mock_chat.bind_tools.side_effect = [bound_a, bound_b]
            mock_llm_class.return_value = mock_chat

            with patch("daemon.graph.classify_llm_errors") as mock_classify:
                mock_classify.side_effect = lambda x: x
                with patch("daemon.llm_error_classifier._make_llm_retry_strategy") as mock_strategy, \
                     patch("daemon.graph.Retrying") as mock_retrying, \
                     patch("daemon.graph.stop_after_attempt") as mock_stop:
                    mock_stop.return_value = MagicMock()
                    mock_strategy.return_value = MagicMock()
                    mock_retrying.return_value = MagicMock()

                    build_instance_llms(
                        llm_config_with_headers={
                            "base_url": "https://primary/v1",
                            "base_url_backup": "https://backup/v1",
                            "api_key": "test",
                            "model": "gpt-4",
                            "model_vision": None,
                            "default_headers": {},
                        },
                        model_standard="gpt-4",
                        model_vision=None,
                        tools=[],
                        retry_config={"transient_attempts": 8, "timeout_attempts": 3},
                    )

                    # stop_after_attempt(N): N must be exactly
                    # max(transient, timeout) + max(primary caps) — large
                    # enough to cover primary slice + full backup budget,
                    # small enough to not over-retry.
                    stop_call = mock_stop.call_args
                    n = stop_call.args[0]
                    expected = max(8, 3) + max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)
                    assert n == expected, (
                        f"max_attempts must be exactly {expected}; got {n}"
                    )

    def test_custom_retry_config_ceiling_is_derived_not_hardcoded(self):
        """The ceiling must be DERIVED from the slice caps, not a magic
        ``+3``. The derivation ``max(t, o) + max(PRIMARY_*)`` is what
        keeps graph.py in lock-step with the strategy's slice constants.

        With the current defaults (3/2) ``max(PRIMARY_*) == 3``, so a
        hardcoded ``+3`` behaves identically — the drift only bites when
        the constants change. This test pins the RELATIONSHIP: patch the
        exported constants (as any future tuning would) and require the
        ceiling to follow. A hardcoded ``+3`` fails here.
        """
        from unittest.mock import MagicMock, patch
        from daemon.graph import build_instance_llms

        with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class:
            mock_chat = self._make_mock_chat_openai("https://primary/v1")
            bound_a = MagicMock()
            bound_b = MagicMock()
            mock_chat.bind_tools.side_effect = [bound_a, bound_b]
            mock_llm_class.return_value = mock_chat

            with patch("daemon.graph.classify_llm_errors") as mock_classify:
                mock_classify.side_effect = lambda x: x
                with patch("daemon.llm_error_classifier._make_llm_retry_strategy") as mock_strategy, \
                     patch("daemon.graph.Retrying") as mock_retrying, \
                     patch("daemon.graph.stop_after_attempt") as mock_stop, \
                     patch("daemon.llm_error_classifier.PRIMARY_TRANSIENT_MAX", 5), \
                     patch("daemon.llm_error_classifier.PRIMARY_TIMEOUT_MAX", 3):
                    mock_stop.return_value = MagicMock()
                    mock_strategy.return_value = MagicMock()
                    mock_retrying.return_value = MagicMock()

                    build_instance_llms(
                        llm_config_with_headers={
                            "base_url": "https://primary/v1",
                            "base_url_backup": "https://backup/v1",
                            "api_key": "test",
                            "model": "gpt-4",
                            "model_vision": None,
                            "default_headers": {},
                        },
                        model_standard="gpt-4",
                        model_vision=None,
                        tools=[],
                        retry_config={"transient_attempts": 3, "timeout_attempts": 2},
                    )

                    stop_call = mock_stop.call_args
                    n = stop_call.args[0]
                    # max(3, 2) + max(5, 3) = 3 + 5 = 8. The patched
                    # constants must flow into the ceiling; a hardcoded
                    # ``+ 3`` would yield 6 and truncate the backup budget.
                    assert n == 8, (
                        f"max_attempts must follow the PRIMARY_* constants "
                        f"(expected 8 = max(3,2) + max(5,3)); got {n} — "
                        f"a hardcoded +3 would truncate the backup budget"
                    )

    def test_no_backup_keeps_max_attempts_at_pre_ha_value(self):
        """Without ``base_url_backup``, the ceiling must stay at the
        pre-HA value ``max(transient, timeout)`` — no HA extension."""
        from unittest.mock import MagicMock, patch
        from daemon.graph import build_instance_llms

        with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class:
            mock_chat = self._make_mock_chat_openai("https://primary/v1")
            bound_a = MagicMock()
            bound_b = MagicMock()
            mock_chat.bind_tools.side_effect = [bound_a, bound_b]
            mock_llm_class.return_value = mock_chat

            with patch("daemon.graph.classify_llm_errors") as mock_classify:
                mock_classify.side_effect = lambda x: x
                with patch("daemon.llm_error_classifier._make_llm_retry_strategy") as mock_strategy, \
                     patch("daemon.graph.Retrying") as mock_retrying, \
                     patch("daemon.graph.stop_after_attempt") as mock_stop:
                    mock_stop.return_value = MagicMock()
                    mock_strategy.return_value = MagicMock()
                    mock_retrying.return_value = MagicMock()

                    build_instance_llms(
                        llm_config_with_headers={
                            "base_url": "https://primary/v1",
                            "base_url_backup": None,
                            "api_key": "test",
                            "model": "gpt-4",
                            "model_vision": None,
                            "default_headers": {},
                        },
                        model_standard="gpt-4",
                        model_vision=None,
                        tools=[],
                        retry_config={"transient_attempts": 8, "timeout_attempts": 3},
                    )

                    stop_call = mock_stop.call_args
                    n = stop_call.args[0]
                    assert n == 8, (
                        f"max_attempts without backup must be exactly 8 "
                        f"(max(8, 3)); got {n}"
                    )



# ---------------------------------------------------------------------------
# End-to-end: real langchain_openai + httpx.MockTransport
# ---------------------------------------------------------------------------


class TestEndToEndFailoverWithMockTransport:
    """Full request-path verification of the swap.

    Uses a REAL ``langchain_openai.ChatOpenAI`` backed by
    ``httpx.MockTransport`` (no network) so the swap is exercised through
    the genuine openai SDK request builder — the layer where the
    raw-``_base_url``-assignment bug crashed with
    ``AttributeError: 'str' object has no attribute 'raw_path'``.

    Flow: primary always returns 500 (transient) → primary slice of 2
    transient retries exhausts → swap fires → the request actually hits
    the backup URL and succeeds.
    """

    PRIMARY = "https://primary.test/v1"
    BACKUP = "https://backup.test/v1"

    @staticmethod
    def _completion_body(content):
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }

    def test_swap_actually_redirects_requests_to_backup(self):
        import httpx
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI
        from tenacity import Retrying, stop_after_attempt, wait_fixed

        from daemon.llm_error_classifier import (
            PRIMARY_TIMEOUT_MAX,
            PRIMARY_TRANSIENT_MAX,
            classify_llm_errors,
        )

        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(
                    200, json=self._completion_body("from-backup")
                )
            # Primary always fails transient (500 is in RETRYABLE_STATUS_CODES).
            return httpx.Response(
                500,
                json={"error": {"message": "primary down",
                                "type": "server_error"}},
            )

        llm = ChatOpenAI(
            api_key="test",
            base_url=self.PRIMARY,
            model="gpt-test",
            max_retries=0,  # keep HTTP request count deterministic
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        transient_max, timeout_max = 5, 2
        ctl = FailoverController(llm, self.PRIMARY, self.BACKUP)
        strategy = _make_llm_retry_strategy(
            transient_max=transient_max,
            timeout_max=timeout_max,
            failover_controller=ctl,
        )
        ceiling = max(transient_max, timeout_max) + max(
            PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX
        )
        retrying = Retrying(
            stop=stop_after_attempt(ceiling),
            wait=wait_fixed(0),
            retry=strategy,
            reraise=True,
        )
        classified = classify_llm_errors(llm)

        # Baseline sanity: the primary URL is live in the openai client.
        assert str(llm.root_client.base_url).startswith("https://primary.test")

        # Drive the full pipeline: primary 500s → slice → swap → backup 200.
        result = retrying(classified.invoke, [HumanMessage(content="hi")])

        # The backup answered.
        assert result.content == "from-backup"

        # The request really hit the backup URL (host + chat/completions path).
        hosts = [u.host for u in captured]
        assert hosts[0] == "primary.test", (
            f"first request must go to primary; captured={hosts}"
        )
        assert "backup.test" in hosts, (
            f"post-swap request must reach the backup; captured={hosts}"
        )
        backup_urls = [u for u in captured if u.host == "backup.test"]
        assert all(
            str(u).endswith("/chat/completions") for u in backup_urls
        ), f"backup requests must target the completions path; got {backup_urls}"

        # Budget accounting: primary slice (PRIMARY_TRANSIENT_MAX=3 counts
        # → attempts 1,2 retried on primary, 3rd triggers swap) then the
        # first backup request succeeds.
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"expected exactly {PRIMARY_TRANSIENT_MAX} primary requests "
            f"(primary slice); captured={hosts}"
        )
        assert hosts.count("backup.test") == 1

        # The openai client is left pointing at the backup (non-sticky
        # reset happens at the START of the next cycle, not on success).
        assert str(llm.root_client.base_url).startswith("https://backup.test")
