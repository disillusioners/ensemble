"""Tests for LLM provider HA auto-fallback (LLM-HA).

Covers:
  - ``FailoverController`` swap / reset mechanics (sticky-on-success,
    W1 adjudication — see class docstrings)
  - ``_make_llm_retry_strategy`` budget-split between primary and backup
  - Zero behavior change when no backup is configured
  - Sticky-on-success cross-invoke semantics (W1 adjudication)
  - IndexError retry-with-failover path
  - Auth / 400 / context-length non-retry paths unchanged
  - F1 regression: ``base_url_backup`` must never reach the ChatOpenAI
    constructor kwargs (real-SDK invoke test with MockTransport)

Companion module: ``daemon.llm_error_classifier`` (the production
code under test).
"""

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from tenacity import Retrying

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
# Sticky-on-success: reset fires on the first FAILED attempt of the next
# cycle (leader-adjudicated W1 semantic)
# ---------------------------------------------------------------------------


class TestStickyOnSuccessResetOnFailure:
    """Cross-invoke semantics (adjudicated in the 2026-08-14 review, W1):

    STICKY-ON-SUCCESS. After a cycle fails over and succeeds on backup,
    the client URL REMAINS on backup — the NEXT invoke's first request
    goes out on the lingering backup URL (it is sent before tenacity
    evaluates anything). The predicate's ``attempt_number == 1`` branch
    (counters + ``reset_to_primary``) runs AFTER that first attempt
    completes, so the client returns to primary from the second attempt
    onward.

    Why sticky is intentional: an eager reset before every invoke would
    tax EVERY invoke with dead-primary probe latency during an outage,
    while both endpoints serve the same backend — the one-request linger
    is harmless and self-heals on the next predicate evaluation. Do NOT
    "fix" this by adding a reset-at-invoke-start; the decision is final
    unless re-adjudicated.
    """

    def test_reset_to_primary_fires_at_attempt_one_of_next_cycle(self):
        """The predicate's ``attempt_number == 1`` branch calls
        ``reset_to_primary`` — it runs after the first attempt of a new
        cycle completes, so the RETRY after a failed first attempt goes
        to primary."""
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

        # Cycle 2's first attempt went out on backup (sticky inheritance)
        # and FAILED. The predicate evaluates attempt_number=1 → resets
        # counters AND the controller URL — the RETRY goes to primary.
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

    def test_success_on_backup_leaves_client_on_backup(self):
        """STICKY-ON-SUCCESS core invariant: the reset lives in the retry
        PREDICATE, which tenacity evaluates only AFTER an attempt. The
        request that just succeeded went out on backup and no reset has
        fired — the client lingers on backup until the next predicate
        evaluation (the next invoke's first attempt).

        (This is the adjudicated W1 semantic — see the class docstring.
        Codified here so a future "helpful" change cannot flip it
        silently. For the REQUEST-level proof that the next invoke's
        first request really goes to backup, see the MockTransport e2e
        test in TestEndToEndFailoverWithMockTransport.)
        """
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

        # Attempt 4 SUCCEEDS (no exception) — predicate returns False.
        # The URL is unchanged: success never triggers a swap-back.
        outcome_ok = MagicMock()
        outcome_ok.exception.return_value = None
        state_ok = MagicMock()
        state_ok.outcome = outcome_ok
        state_ok.attempt_number = 4
        assert strategy(state_ok) is False
        assert chat.root_client.base_url == "https://backup/v1/"


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

        from daemon.llm_error_classifier import classify_llm_errors

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

        # The openai client is left pointing at the backup — sticky-on-
        # success (W1 adjudication): the successful cycle does NOT swap
        # back; the next invoke's first request goes out on the backup
        # URL and only the predicate's attempt-1 evaluation afterwards
        # returns the client to primary.
        assert str(llm.root_client.base_url).startswith("https://backup.test")


# ---------------------------------------------------------------------------
# W1: request-level proof of sticky-on-success (two full invoke cycles)
# ---------------------------------------------------------------------------


class TestStickyOnSuccessEndToEnd:
    """Drives TWO full invoke cycles through a real ChatOpenAI +
    MockTransport to pin the request-level sticky-on-success behavior:

      Cycle 1: primary 500s → slice → swap → backup answers → client
               stays on backup (sticky).
      Cycle 2: first request goes to BACKUP (proving the linger), then
               the predicate's attempt-1 reset returns the client to
               primary; if primary is healthy again the cycle completes
               there.

    This codifies the leader-adjudicated W1 semantic (2026-08-14 review):
    do NOT add an eager reset-at-invoke-start — see the adjudication
    note in TestStickyOnSuccessResetOnFailure's docstring.
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

    def test_next_invoke_first_request_hits_backup_then_returns_to_primary(self):
        import httpx
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI
        from tenacity import Retrying, stop_after_attempt, wait_fixed

        from daemon.llm_error_classifier import classify_llm_errors

        captured: list[httpx.URL] = []
        primary_down = {"v": True}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(
                    200, json=self._completion_body("from-backup")
                )
            if primary_down["v"]:
                return httpx.Response(
                    500,
                    json={"error": {"message": "primary down",
                                    "type": "server_error"}},
                )
            return httpx.Response(
                200, json=self._completion_body("from-primary")
            )

        llm = ChatOpenAI(
            api_key="test",
            base_url=self.PRIMARY,
            model="gpt-test",
            max_retries=0,
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

        # Cycle 1: primary down → swap → backup answers.
        r1 = retrying(classified.invoke, [HumanMessage(content="cycle-1")])
        assert r1.content == "from-backup"
        # Client lingers on backup (sticky-on-success).
        assert str(llm.root_client.base_url).startswith("https://backup.test")

        # Cycle 2: primary RECOVERED. The first request of this cycle
        # still goes to BACKUP (proof of the linger)...
        primary_down["v"] = False
        r2 = retrying(classified.invoke, [HumanMessage(content="cycle-2")])
        assert r2.content == "from-backup", (
            "cycle 2's first (successful) request must be served by the "
            "lingering backup URL — sticky-on-success (W1 adjudication)"
        )
        # ...but the predicate's attempt-1 evaluation afterwards has
        # returned the client to primary for subsequent requests.
        assert str(llm.root_client.base_url).startswith("https://primary.test")

        # Request-level accounting: cycle 1 = 3 primary (slice) + 1
        # backup; cycle 2 = 1 backup (sticky linger) and 0 primary —
        # the single successful backup request ends the cycle before
        # any primary retry is needed.
        hosts = [u.host for u in captured]
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"cycle 1 primary slice; captured={hosts}"
        )
        assert hosts.count("backup.test") == 2, (
            f"one backup request per cycle; captured={hosts}"
        )
        assert hosts[-1] == "backup.test", (
            f"cycle 2's only request must be the lingering backup hit; "
            f"captured={hosts}"
        )


# ---------------------------------------------------------------------------
# F1 (CRITICAL, merge-blocker): base_url_backup must never reach the
# ChatOpenAI constructor kwargs
# ---------------------------------------------------------------------------


class TestF1BackupKwargNeverReachesConstructor:
    """Regression tests for the round-2 critical finding.

    F1: ``graph.py:3083-3085`` passed the threaded ``base_url_backup``
    key into ``ThinkingChatOpenAI(**cfg)``. On openai 2.24.0 /
    langchain-openai 1.1.10, ``BaseChatOpenAI`` transfers unknown kwargs
    into ``model_kwargs``, and ``model_kwargs`` entries are forwarded
    verbatim to ``Completions.create()`` — so every invoke crashed with
    ``TypeError: Completions.create() got an unexpected keyword argument
    'base_url_backup'`` (with a backup set; with the key present but
    None the same transfer applies).

    The existing wiring tests MISSED this because they all patched the
    ``ThinkingChatOpenAI`` constructor. These tests construct the REAL
    class via ``build_instance_llms`` with the production
    ``_build_llm_config`` dict shape and drive a REAL invoke through
    ``httpx.MockTransport`` — no constructor patching anywhere.
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

    def _production_llm_config(self, http_client):
        """Mirror ``InstanceLifecycle._build_llm_config``'s dict shape
        (daemon/services/instance_lifecycle.py) + the header merge from
        ``build_instance_graph`` — the exact production pipeline that
        feeds ``build_instance_llms``."""
        from daemon.graph import build_instance_graph  # noqa: F401  (shape doc)
        return {
            "base_url": self.PRIMARY,
            "base_url_backup": self.BACKUP,
            "api_key": "test",
            "model": "gpt-test",
            "model_vision": None,
            "temperature": 0.7,
            "request_timeout": 30,
            # merge performed by build_instance_graph before
            # build_instance_llms:
            "default_headers": {"x-proxy-app": "ensemble"},
            # MockTransport injection (test-only keys; ChatOpenAI accepts
            # both as first-class constructor kwargs):
            "http_client": http_client,
            "max_retries": 0,
        }

    def _invoke(self, handler, backup_set, **build_kwargs):
        import httpx
        from daemon.graph import build_instance_llms

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = self._production_llm_config(client)
        if not backup_set:
            cfg["base_url_backup"] = None
        cfg.update(build_kwargs)

        llm_tools, llm_std = build_instance_llms(
            llm_config_with_headers=cfg,
            model_standard="gpt-test",
            model_vision=cfg.get("model_vision"),
            tools=[],
            retry_config={"transient_attempts": 8, "timeout_attempts": 3},
        )
        return llm_std

    def test_real_invoke_succeeds_with_backup_configured(self):
        """THE F1 regression test: real ThinkingChatOpenAI built through
        build_instance_llms with the production config shape and a SET
        backup URL; the invoke must succeed (pre-fix it raised TypeError
        on every call)."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=self._completion_body("f1-fixed")
            )

        llm_std = self._invoke(handler, backup_set=True)
        from langchain_core.messages import HumanMessage
        result = llm_std.invoke([HumanMessage(content="hi")])
        assert result.content == "f1-fixed"

    def test_real_invoke_succeeds_with_backup_unset_key_present(self):
        """F1 also fired with the key present but None (the unset case)
        — the kwargs transfer happens regardless of the value."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=self._completion_body("unset-ok")
            )

        llm_std = self._invoke(handler, backup_set=False)
        from langchain_core.messages import HumanMessage
        result = llm_std.invoke([HumanMessage(content="hi")])
        assert result.content == "unset-ok"

    def test_real_invoke_with_vision_model_configured(self):
        """The vision construction path (graph.py ~3098) must strip the
        kwarg too — drive the dual-LLM shape end to end."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=self._completion_body("vision-ok")
            )

        llm_std = self._invoke(
            handler, backup_set=True, model_vision="gpt-vision-test"
        )
        from langchain_core.messages import HumanMessage
        result = llm_std.invoke([HumanMessage(content="hi")])
        assert result.content == "vision-ok"

    def test_clean_llm_config_strips_backup_key(self):
        """Unit-level pin on the choke point itself."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config({
            "base_url": "https://p/v1",
            "base_url_backup": "https://b/v1",
            "api_key": "k",
            "model": "m",
            "model_vision": "mv",
        })
        assert "base_url_backup" not in cleaned
        assert "model_vision" not in cleaned
        assert cleaned["base_url"] == "https://p/v1"
        assert cleaned["model"] == "m"

    def test_secondary_sites_clean_config_before_construction(self):
        """The secondary LLM sites (compaction / title_generation /
        keyword_extraction / child_reports) route their config through
        ``clean_llm_config`` before ``ThinkingChatOpenAI(**cfg)`` — so
        stripping the key at the choke point covers them too. Verify by
        constructing through the same shape they use."""
        import httpx
        from langchain_core.messages import HumanMessage
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=self._completion_body("sec-ok"))

        # title_generation.py / child_reports.py / keyword_extraction.py shape
        llm_config = {
            "base_url": self.PRIMARY,
            "base_url_backup": self.BACKUP,
            "api_key": "test",
            "model": "gpt-test",
            "temperature": 0.3,
            "default_headers": {"x-proxy-app": "ensemble"},
        }
        llm_config = clean_llm_config(llm_config)
        llm = ThinkingChatOpenAI(
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            **llm_config,
        )
        result = llm.invoke([HumanMessage(content="hi")])
        assert result.content == "sec-ok"


# ---------------------------------------------------------------------------
# W2: primary slice clamps to the operator-configured budget
# ---------------------------------------------------------------------------


class TestPrimarySliceClampedToBudget:
    """W2: when a custom budget is SMALLER than the default primary cap
    (e.g. ``transient_max=2 < PRIMARY_TRANSIENT_MAX=3``), the swap must
    trigger at the budget boundary — not never fire (which would strand
    the configured backup unused).
    """

    def _build(self, transient_max, timeout_max, primary_transient_max=3,
               primary_timeout_max=2):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        strategy = _make_llm_retry_strategy(
            transient_max=transient_max,
            timeout_max=timeout_max,
            failover_controller=ctl,
            primary_transient_max=primary_transient_max,
            primary_timeout_max=primary_timeout_max,
        )
        return strategy, ctl, chat

    def test_small_transient_budget_still_swaps(self):
        """``transient_max=2 < primary_cap=3``: without the clamp the
        predicate would return False at count 2 (budget exhausted on
        primary) and the swap would NEVER fire. With the clamp
        (``effective_cap = min(3, 2) = 2``) the swap fires at attempt 2.
        """
        strategy, ctl, chat = self._build(transient_max=2, timeout_max=2)
        e = _transient_error()

        # Attempt 1: count=1 < effective_cap=2 → stay on primary.
        assert strategy(_make_mock_retry_state(e, attempt_number=1)) is True
        assert chat.root_client.base_url == "https://primary/v1/"

        # Attempt 2: count=2 >= effective_cap=2 → SWAP (not a silent
        # budget-exhausted False).
        assert strategy(_make_mock_retry_state(e, attempt_number=2)) is True
        assert chat.root_client.base_url == "https://backup/v1/"

    def test_small_timeout_budget_still_swaps(self):
        """``timeout_max=1 < primary_timeout_cap=2``: same clamp on the
        timeout side."""
        strategy, ctl, chat = self._build(transient_max=8, timeout_max=1)
        e = _timeout_error()

        assert strategy(_make_mock_retry_state(e, attempt_number=1)) is True
        assert chat.root_client.base_url == "https://backup/v1/"

    def test_backup_budget_never_exceeds_operator_ceiling(self):
        """After the swap, the backup leg is bounded by the operator's
        configured budget — the failover never grants MORE retries than
        the pre-HA system would have."""
        strategy, ctl, chat = self._build(transient_max=2, timeout_max=2)
        e = _transient_error()

        # Swap fires at attempt 2 (clamped cap).
        strategy(_make_mock_retry_state(e, attempt_number=1))
        strategy(_make_mock_retry_state(e, attempt_number=2))
        assert chat.root_client.base_url == "https://backup/v1/"

        # Backup gets the full budget of 2: attempts 3 (count 1) and 4
        # (count 2) are retried... wait — count < full_budget means
        # attempt 3 (count=1 < 2) → True; attempt 4 (count=2 < 2) →
        # False. So exactly one more retry on backup, then stop.
        assert strategy(_make_mock_retry_state(e, attempt_number=3)) is True
        assert strategy(_make_mock_retry_state(e, attempt_number=4)) is False

    def test_default_budgets_unchanged_by_clamp(self):
        """With operator budgets >= the primary caps (the default case),
        the clamp is a no-op: swap still fires at the primary cap."""
        strategy, ctl, chat = self._build(transient_max=8, timeout_max=3)
        e = _transient_error()

        for n in (1, 2):
            assert strategy(_make_mock_retry_state(e, attempt_number=n)) is True
            assert chat.root_client.base_url == "https://primary/v1/"
        assert strategy(_make_mock_retry_state(e, attempt_number=3)) is True
        assert chat.root_client.base_url == "https://backup/v1/"


# ---------------------------------------------------------------------------
# W4: swap resets BOTH category counters
# ---------------------------------------------------------------------------


class TestCrossCategoryCounterReset:
    """W4: after ``swap_to_backup()``, both ``counts["transient"]`` and
    ``counts["timeout"]`` must be zeroed. Real failures interleave
    (transient, transient, timeout, ...); resetting only the triggering
    category would carry the other category's primary-phase count into
    the backup phase and shortchange the backup budget.
    """

    def _build(self):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        strategy = _make_llm_retry_strategy(
            transient_max=8, timeout_max=3, failover_controller=ctl,
            primary_transient_max=3, primary_timeout_max=2,
        )
        return strategy, ctl, chat

    def test_interleaved_failures_grant_full_backup_budget(self):
        """2 transients + 1 timeout on primary (transient count=2,
        timeout count=1), then a 3rd transient triggers the swap at
        count=3. The timeout counter (1) must ALSO reset — otherwise the
        backup's timeout leg would have only 2 attempts left instead of
        the full 3.
        """
        strategy, ctl, chat = self._build()

        # Primary phase: interleave transient and timeout failures.
        # attempt 1 transient (t-count 1), attempt 2 timeout (o-count 1),
        # attempt 3 transient (t-count 2), attempt 4 transient (t-count 3
        # = primary_transient_max) → SWAP. Timeout count is 1 at swap time.
        strategy(_make_mock_retry_state(_transient_error(), attempt_number=1))
        strategy(_make_mock_retry_state(_timeout_error(), attempt_number=2))
        strategy(_make_mock_retry_state(_transient_error(), attempt_number=3))
        assert chat.root_client.base_url == "https://primary/v1/"
        strategy(_make_mock_retry_state(_transient_error(), attempt_number=4))
        assert chat.root_client.base_url == "https://backup/v1/"

        # Backup phase: the timeout budget must be the FULL 3, not 3-1=2.
        # Drive timeouts on backup: counts 1, 2 → True; count 3 → False.
        for n, expected in [(5, True), (6, True), (7, False)]:
            got = strategy(_make_mock_retry_state(_timeout_error(), attempt_number=n))
            assert got is expected, (
                f"backup timeout attempt {n}: expected {expected} (full "
                f"budget must survive the cross-category reset), got {got}"
            )

    def test_swap_via_timeout_resets_transient_count(self):
        """Mirror: a timeout-triggered swap must zero the transient
        counter so the backup's transient leg is unaffected by the
        primary-phase transient failures."""
        strategy, ctl, chat = self._build()

        # Primary phase: transient count=2 (near its cap), then the 2nd
        # timeout hits timeout cap=2 first and triggers the swap.
        # attempt 1 transient (t=1), attempt 2 transient (t=2),
        # attempt 3 timeout (o=1), attempt 4 timeout (o=2 = cap) → SWAP.
        # Transient count is 2 at swap time.
        strategy(_make_mock_retry_state(_transient_error(), attempt_number=1))
        strategy(_make_mock_retry_state(_transient_error(), attempt_number=2))
        strategy(_make_mock_retry_state(_timeout_error(), attempt_number=3))
        assert chat.root_client.base_url == "https://primary/v1/"
        strategy(_make_mock_retry_state(_timeout_error(), attempt_number=4))
        assert chat.root_client.base_url == "https://backup/v1/"

        # Backup phase: full transient budget of 8 available — counts
        # 1..7 retried, 8 stops. Pre-fix, the transient count carried
        # over as 2 and the budget would end 2 attempts early.
        for n in range(5, 12):
            assert strategy(
                _make_mock_retry_state(_transient_error(), attempt_number=n)
            ) is True, f"backup transient attempt {n} must retry (full budget)"
        assert strategy(
            _make_mock_retry_state(_transient_error(), attempt_number=12)
        ) is False


# ---------------------------------------------------------------------------
# W5: dead swap path emits one WARNING per controller
# ---------------------------------------------------------------------------


class TestDeadSwapPathWarning:
    """W5: if BOTH base_url mutation attempts fail (root_client AND
    root_async_client), the failover silently did nothing while a backup
    is configured. One WARNING must be emitted per swap attempt so the
    operator can see it at normal log levels (per-attempt detail stays
    at DEBUG with traceback).
    """

    def _broken_chat_client(self):
        """A chat client whose root_client/root_async_client raise on
        base_url assignment (simulates a stripped-down or future-SDK
        client shape)."""
        class BrokenBaseUrl:
            def __init__(self):
                # real OpenAI clients type base_url as httpx.URL; a
                # property that raises simulates an incompatible SDK.
                pass

            @property
            def base_url(self):
                return None

            @base_url.setter
            def base_url(self, value):
                raise RuntimeError("incompatible client shape")

        from types import SimpleNamespace
        return SimpleNamespace(
            root_client=BrokenBaseUrl(),
            root_async_client=BrokenBaseUrl(),
        )

    def test_both_attempts_failing_emits_warning(self, caplog):
        chat = self._broken_chat_client()
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        with caplog.at_level(logging.DEBUG, logger="daemon.llm_error_classifier"):
            ctl.swap_to_backup()

        warnings = [
            r for r in caplog.records
            if r.levelname == "WARNING" and "[LLM-HA]" in r.getMessage()
            and "NO-OP" in r.getMessage()
        ]
        assert len(warnings) == 1, (
            f"expected exactly one WARNING for the dead swap path; got "
            f"{[r.getMessage() for r in warnings]}"
        )

    def test_single_failure_stays_at_debug(self, caplog):
        """Only when BOTH attempts fail does the WARNING fire. A single
        failed mutation (e.g. missing async client) stays at DEBUG — the
        sync path (the one the daemon actually uses) still works."""
        from types import SimpleNamespace

        class BrokenBaseUrl:
            @property
            def base_url(self):
                return None

            @base_url.setter
            def base_url(self, value):
                raise RuntimeError("broken")

        chat = SimpleNamespace(
            root_client=openai.OpenAI(api_key="k", base_url="https://primary/v1"),
            root_async_client=BrokenBaseUrl(),
        )
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        with caplog.at_level(logging.DEBUG, logger="daemon.llm_error_classifier"):
            ctl.swap_to_backup()

        # Sync mutation succeeded — the swap is NOT dead, no WARNING.
        dead_warnings = [
            r for r in caplog.records
            if r.levelname == "WARNING" and "NO-OP" in r.getMessage()
        ]
        assert not dead_warnings
        # The async failure detail is at DEBUG.
        assert any(
            r.levelname == "DEBUG" and "root_async_client" in r.getMessage()
            for r in caplog.records
        )
        # And the sync client really moved.
        assert str(chat.root_client.base_url).startswith("https://backup")

    def test_swap_warning_count_bounded_across_predicate_calls(self, caplog):
        """Sug6(c): the WARNING count stays bounded under repeated
        predicate calls — swap_to_backup is idempotent, so a dead swap
        logs once, not once per predicate evaluation."""
        chat = self._broken_chat_client()
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        strategy = _make_llm_retry_strategy(
            transient_max=8, timeout_max=3, failover_controller=ctl,
            primary_transient_max=3, primary_timeout_max=2,
        )
        e = _transient_error()

        with caplog.at_level(logging.WARNING, logger="daemon.llm_error_classifier"):
            for n in range(1, 8):
                strategy(_make_mock_retry_state(e, attempt_number=n))

        dead_warnings = [
            r for r in caplog.records if "NO-OP" in r.getMessage()
        ]
        assert len(dead_warnings) == 1, (
            f"dead-swap WARNING must fire exactly once (swap is "
            f"idempotent); got {len(dead_warnings)}"
        )


# ---------------------------------------------------------------------------
# Suggestion 6(d): missing-root_client no-op assertions
# ---------------------------------------------------------------------------


class TestMissingRootClientNoOp:
    """Sug6(d): when the langchain client exposes neither
    ``root_client`` nor ``root_async_client``, the controller must be a
    silent no-op (no raise, no WARNING — nothing is broken, there is
    just nothing to mutate).
    """

    def test_swap_and_reset_are_silent_noops(self, caplog):
        chat = MagicMock(spec=[])  # no root_client / root_async_client
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")

        with caplog.at_level(logging.WARNING, logger="daemon.llm_error_classifier"):
            ctl.swap_to_backup()  # must not raise
            ctl.reset_to_primary()  # must not raise

        # No warnings — a missing client is a no-op, not a dead swap.
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_strategy_tolerates_clientless_controller(self):
        """The full predicate path with a clientless controller: retry
        decisions must still be made correctly (the swap is a no-op but
        the budget-split still applies)."""
        chat = MagicMock(spec=[])
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        assert ctl.is_configured is True  # backup URL set → configured

        strategy = _make_llm_retry_strategy(
            transient_max=8, timeout_max=3, failover_controller=ctl,
            primary_transient_max=3, primary_timeout_max=2,
        )
        e = _transient_error()
        # Budget split still works: 2 primary retries, swap (no-op), then
        # backup budget.
        assert strategy(_make_mock_retry_state(e, attempt_number=1)) is True
        assert strategy(_make_mock_retry_state(e, attempt_number=2)) is True
        assert strategy(_make_mock_retry_state(e, attempt_number=3)) is True
        for n in range(4, 11):
            assert strategy(_make_mock_retry_state(e, attempt_number=n)) is True
        assert strategy(_make_mock_retry_state(e, attempt_number=11)) is False


# ---------------------------------------------------------------------------
# Suggestion 2: non-string base_url_backup values are rejected
# ---------------------------------------------------------------------------


class TestBackupFieldRejectsNonStrings:
    """Sug2: YAML ``true`` / numbers must not silently corrupt
    ``is_configured``. A YAML boolean can only reach the validator via
    the YAML source (pydantic-settings env source always yields
    strings), so the YAML path is the one pinned here.
    """

    def test_yaml_true_rejected(self, tmp_path, monkeypatch):
        from daemon.config import load_config

        monkeypatch.delenv("OPENAI_BASE_URL_BACKUP", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text(
            "llm:\n"
            "  base_url: 'https://primary.example/v1'\n"
            "  base_url_backup: true\n"
            "  api_key: 'sk-test'\n"
            "  model: 'gpt-4'\n"
        )
        with pytest.raises(Exception) as excinfo:
            load_config(str(yaml_path))
        # Targeted message beats pydantic's generic string_type error.
        assert "no boolean form" in str(excinfo.value) or (
            "boolean" in str(excinfo.value)
        )

    def test_yaml_number_rejected(self, tmp_path, monkeypatch):
        from daemon.config import load_config

        monkeypatch.delenv("OPENAI_BASE_URL_BACKUP", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text(
            "llm:\n"
            "  base_url: 'https://primary.example/v1'\n"
            "  base_url_backup: 123\n"
            "  api_key: 'sk-test'\n"
            "  model: 'gpt-4'\n"
        )
        with pytest.raises(Exception):
            load_config(str(yaml_path))

    def test_valid_url_still_accepted(self, tmp_path, monkeypatch):
        from daemon.config import load_config

        monkeypatch.delenv("OPENAI_BASE_URL_BACKUP", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        yaml_path = tmp_path / "good.yaml"
        yaml_path.write_text(
            "llm:\n"
            "  base_url: 'https://primary.example/v1'\n"
            "  base_url_backup: 'https://backup.example/v1'\n"
            "  api_key: 'sk-test'\n"
            "  model: 'gpt-4'\n"
        )
        cfg = load_config(str(yaml_path))
        assert cfg.llm.base_url_backup == "https://backup.example/v1"


# ---------------------------------------------------------------------------
# W3: vision client gets its own failover controller
# ---------------------------------------------------------------------------


class TestVisionControllerSeparation:
    """W3 (preferred fix applied): when a vision model is configured,
    ``llm_with_tools`` and ``llm_standard`` are SEPARATE underlying
    ChatOpenAI clients. Each must get its OWN FailoverController and its
    OWN retry strategy so a vision failure swaps the vision client's URL
    (and a standard failure swaps the standard client's URL) — no
    asymmetric cross-wiring.

    When vision is NOT configured both wrappers share one client and one
    strategy (pre-HA behavior).
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

    def test_vision_failure_swaps_only_vision_client(self):
        """End-to-end with REAL ThinkingChatOpenAI + MockTransport: the
        vision MODEL's requests fail on the primary; the failover swaps
        the VISION client to backup while the STANDARD client stays on
        primary (its requests succeed there). The handler discriminates
        by request body ``model`` — both clients share the same
        ``base_url`` but are separate SDK clients with separate
        controllers."""
        import httpx
        from langchain_core.messages import HumanMessage
        from daemon.graph import build_instance_llms

        requests: list[tuple[str, str]] = []  # (host, model)
        down_models = {"gpt-vision"}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            model = body.get("model", "?")
            requests.append((request.url.host, model))
            if (
                model in down_models
                and request.url.host == "standard-primary.test"
            ):
                return httpx.Response(
                    500,
                    json={"error": {"message": "vision model down on primary",
                                    "type": "server_error"}},
                )
            return httpx.Response(200, json=self._completion_body(f"ok-{model}"))

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = {
            "base_url": "https://standard-primary.test/v1",
            "base_url_backup": "https://backup.test/v1",
            "api_key": "test",
            "model": "gpt-standard",
            "model_vision": "gpt-vision",
            "temperature": 0.7,
            "request_timeout": 30,
            "default_headers": {"x-proxy-app": "ensemble"},
            "http_client": client,
            "max_retries": 0,
        }
        llm_tools, llm_std = build_instance_llms(
            llm_config_with_headers=cfg,
            model_standard="gpt-standard",
            model_vision="gpt-vision",
            tools=[],
            retry_config={"transient_attempts": 8, "timeout_attempts": 3},
        )

        # Invoke the vision-backed tools LLM: vision fails on the primary
        # → its OWN controller swaps the vision client to backup →
        # succeeds on backup.
        r = llm_tools.invoke([HumanMessage(content="describe image")])
        assert r.content == "ok-gpt-vision"
        vision_hosts = [h for h, m in requests if m == "gpt-vision"]
        assert "standard-primary.test" in vision_hosts, "vision starts on primary"
        assert "backup.test" in vision_hosts, "vision fails over to backup"

        # Now invoke the standard LLM: it must still be on the PRIMARY
        # (the vision failover must not have touched it).
        requests.clear()
        r2 = llm_std.invoke([HumanMessage(content="hello")])
        assert r2.content == "ok-gpt-standard"
        hosts_used_by_standard = [h for h, m in requests]
        assert hosts_used_by_standard == ["standard-primary.test"], (
            f"standard client must remain on primary after a vision-only "
            f"failover; hosts={hosts_used_by_standard}"
        )

    def test_no_vision_shares_one_retrying(self):
        """No vision model → both wrappers drive the same underlying
        client and share ONE strategy (pre-HA shape preserved)."""
        import httpx
        from daemon.graph import build_instance_llms

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=self._completion_body("ok"))

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = {
            "base_url": self.PRIMARY,
            "base_url_backup": self.BACKUP,
            "api_key": "test",
            "model": "gpt-test",
            "model_vision": None,
            "temperature": 0.7,
            "request_timeout": 30,
            "default_headers": {"x-proxy-app": "ensemble"},
            "http_client": client,
            "max_retries": 0,
        }
        with patch("daemon.graph.Retrying", wraps=Retrying) as mock_retrying:
            build_instance_llms(
                llm_config_with_headers=cfg,
                model_standard="gpt-test",
                model_vision=None,
                tools=[],
                retry_config={"transient_attempts": 8, "timeout_attempts": 3},
            )
            assert mock_retrying.call_count == 1, (
                "no-vision case must build exactly one Retrying (shared "
                "strategy for both wrappers)"
            )

    def test_vision_builds_two_retryings(self):
        """Vision model → two independent strategies (one per client)."""
        import httpx
        from daemon.graph import build_instance_llms

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=self._completion_body("ok"))

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = {
            "base_url": self.PRIMARY,
            "base_url_backup": self.BACKUP,
            "api_key": "test",
            "model": "gpt-test",
            "model_vision": "gpt-vision-test",
            "temperature": 0.7,
            "request_timeout": 30,
            "default_headers": {"x-proxy-app": "ensemble"},
            "http_client": client,
            "max_retries": 0,
        }
        with patch("daemon.graph.Retrying", wraps=Retrying) as mock_retrying:
            build_instance_llms(
                llm_config_with_headers=cfg,
                model_standard="gpt-test",
                model_vision="gpt-vision-test",
                tools=[],
                retry_config={"transient_attempts": 8, "timeout_attempts": 3},
            )
            assert mock_retrying.call_count == 2, (
                "vision case must build two Retrying instances (one per "
                "underlying client)"
            )
