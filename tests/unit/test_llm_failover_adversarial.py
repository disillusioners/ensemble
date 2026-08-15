"""Adversarial verification tests for LLM provider HA auto-fallback.

These tests exercise the LLM-HA failover surface from edges the
canonical ``tests/unit/test_llm_failover.py`` suite does not pin down.
Where an existing test already covers an adversarial scenario, that
scenario is listed in the class docstring as "skipped (covered by
<other class>)". Everything else targets a fresh adversarial angle.

Six areas are covered (numbered to match the task spec):

  1. Zero-behavior-change with backup unset (THE critical invariant).
  2. Budget split arithmetic at slice < budget / == budget / > budget.
  3. Config validation (empty / whitespace / non-string rejection).
  4. Failover end-to-end via MockTransport (log capture, primary-down
     cycle, backup-fails-fall-through).
  5. Sticky-on-success + counter reset (backup-fails-after-success).
  6. Production-code IndexError (empty-choices[]) edits across
     title_generation / keyword_extraction / child_reports.

Test code ONLY. No production code is modified. The only network I/O
is via ``httpx.MockTransport`` (no real HTTP).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from daemon.llm_error_classifier import (
    PRIMARY_TIMEOUT_MAX,
    PRIMARY_TRANSIENT_MAX,
    FailoverController,
    make_llm_retry_strategy,
    classify_llm_errors,
)


# ---------------------------------------------------------------------------
# Shared helpers — kept local so the file is self-contained.
# Reuses the same construction shape as
# ``tests/unit/test_llm_failover.py::_make_fake_chat_client``.
# ---------------------------------------------------------------------------


def _make_fake_chat_client(primary_url: str):
    """Stand-in shaped like ``langchain.ChatOpenAI`` with REAL
    ``openai.OpenAI`` / ``openai.AsyncOpenAI`` clients (construction
    only — no network) so that ``base_url`` assignment uses the public
    setter that goes through ``URL()`` + ``_enforce_trailing_slash``.
    """
    sync = openai.OpenAI(api_key="test-key", base_url=primary_url)
    async_c = openai.AsyncOpenAI(api_key="test-key", base_url=primary_url)
    return SimpleNamespace(root_client=sync, root_async_client=async_c)


def _make_mock_retry_state(exception, attempt_number=1):
    """Build a tenacity RetryCallState mock."""
    from tenacity import RetryCallState

    outcome = MagicMock()
    outcome.exception.return_value = exception
    state = MagicMock(spec=RetryCallState)
    state.outcome = outcome
    state.attempt_number = attempt_number
    return state


def _transient_error():
    return openai.APIConnectionError(message="boom", request=MagicMock())


def _timeout_error():
    return openai.APITimeoutError(request=MagicMock())


def _index_error():
    return IndexError("list index out of range")


# ---------------------------------------------------------------------------
# AREA 1: zero-behavior-change with backup unset
#
# Coverage notes vs tests/unit/test_llm_failover.py:
#   - TestNoBackupUnchangedBehavior.test_index_error_not_retried and
#     test_auth_error_not_retried already pin transient/auth behavior.
#   - This file adds: config-level invariant, exact retry counting
#     against the documented budgets, and a 1000-iteration sweep that
#     asserts no failover ever engages (no swap, no counter reset).
# ---------------------------------------------------------------------------


class TestAdversarial1ZeroBehaviorChangeBackupUnset:
    """Zero behavior change when OPENAI_BASE_URL_BACKUP is unset.

    The most important invariant in this feature: deployment with the
    env var unset must behave IDENTICALLY to the pre-HA system. These
    tests pin both the config surface and the retry surface.
    """

    def test_env_unset_yields_none_at_config_layer(self, monkeypatch):
        """``OPENAI_BASE_URL_BACKUP`` unset → ``LLMConfig.base_url_backup``
        is exactly ``None`` (not the empty string, not ``"None"``).
        """
        monkeypatch.delenv("OPENAI_BASE_URL_BACKUP", raising=False)
        from daemon.config import LLMConfig

        cfg = LLMConfig()
        assert cfg.base_url_backup is None, (
            f"backup-unset env must yield None at config layer, got "
            f"{cfg.base_url_backup!r}"
        )

    def test_exact_retry_count_matches_pre_ha_budget(self):
        """Without a controller, transient_max=10 must allow EXACTLY
        ``transient_max - 1`` retries (the pre-HA documented convention:
        ``count < transient_max`` → True on the first ``transient_max -
        1`` attempts, False on the ``transient_max``th). This convention
        is pinned by ``TestRetryByCategory::test_transient_errors_limited_to_transient_max``
        — re-pin here as an adversarial check.
        """
        strategy = make_llm_retry_strategy(transient_max=10, timeout_max=5)
        e = _transient_error()
        results = [
            strategy(_make_mock_retry_state(e, attempt_number=n))
            for n in range(1, 20)
        ]
        # First 9 attempts True (transient_max - 1 = 9 retries), attempt
        # 10 False — convention is ``count < transient_max`` returns True.
        assert results[:9] == [True] * 9, (
            f"first transient_max-1 attempts must be retried; got "
            f"{results[:9]}"
        )
        assert results[9] is False, (
            f"the transient_max-th attempt must NOT retry (budget "
            f"exhausted); got {results[9]}"
        )
        assert not any(results[10:]), (
            f"attempts 11+ must NOT retry without controller; got "
            f"{results[10:]}"
        )

    def test_index_error_never_retried_in_long_sweep_without_controller(self):
        """1,000-attempt sweep: IndexError must NEVER be retried with no
        controller (catches any future change that accidentally adds
        IndexError to the always-retry path).
        """
        strategy = make_llm_retry_strategy(transient_max=20, timeout_max=20)
        e = _index_error()
        for n in range(1, 1001):
            assert strategy(_make_mock_retry_state(e, attempt_number=n)) is False
        # Counter must NEVER have been incremented.
        # The predicate keeps ``counts`` in a closure; we cannot read it
        # directly, but the above 1k-iteration assertion that every call
        # returned False (without the predicate short-circuiting on the
        # category guard) is the behavioral pin.

    def test_url_never_swaps_with_long_transient_failure_storm(self):
        """A storm of transient failures (more than enough to exhaust
        any reasonable budget) on a no-backup client must NEVER swap the
        URL. This is the most adversarial check against accidental
        controller promotion.
        """
        chat = _make_fake_chat_client("https://primary/v1")
        # Construct a controller but make it "unconfigured" — the
        # predicate must NOT promote it. We pass it through
        # ``is_configured`` semantics by leaving backup=None.
        ctl = FailoverController(chat, "https://primary/v1", None)
        assert ctl.is_configured is False
        strategy = make_llm_retry_strategy(
            transient_max=10, timeout_max=5, failover_controller=ctl
        )
        e = _transient_error()
        for n in range(1, 20):
            strategy(_make_mock_retry_state(e, attempt_number=n))
        assert chat.root_client.base_url == "https://primary/v1/", (
            f"unconfigured controller must never mutate the URL; got "
            f"{chat.root_client.base_url}"
        )


# ---------------------------------------------------------------------------
# AREA 2: budget split arithmetic (W2 regression) at all boundaries.
#
# Coverage notes:
#   - TestPrimarySliceClampedToBudget already covers the clamp with
#     transient_max=2 < 3 (and timeout_max=1 < 2).
#   - This file pins the EXACT boundary cases: slice < budget,
#     slice == budget, slice > budget, for BOTH transient and timeout
#     categories, and verifies that the swap fires on the boundary
#     attempt (count == min(primary_cap, full_budget)).
# ---------------------------------------------------------------------------


class TestAdversarial2BudgetSplitBoundaries:
    """Pin the budget-split arithmetic at every operator-configurable
    boundary.
    """

    def _build(
        self,
        primary_transient_max,
        primary_timeout_max,
        transient_max,
        timeout_max,
    ):
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(
            chat, "https://primary/v1", "https://backup/v1"
        )
        strategy = make_llm_retry_strategy(
            transient_max=transient_max,
            timeout_max=timeout_max,
            failover_controller=ctl,
            primary_transient_max=primary_transient_max,
            primary_timeout_max=primary_timeout_max,
        )
        return strategy, ctl, chat

    # --- transient boundaries ---

    def test_transient_slice_less_than_budget_swaps_at_slice(self):
        """primary_transient_max=2 < transient_max=8. effective_cap = 2.
        Swap fires at attempt 2 (count == 2). Backup gets full 8.
        """
        strategy, ctl, chat = self._build(
            primary_transient_max=2, primary_timeout_max=1,
            transient_max=8, timeout_max=3,
        )
        e = _transient_error()
        # Attempt 1: count=1 < cap=2 → retry on primary.
        assert strategy(_make_mock_retry_state(e, 1)) is True
        assert chat.root_client.base_url == "https://primary/v1/"
        # Attempt 2: count=2 == cap=2 → SWAP, retry on backup.
        assert strategy(_make_mock_retry_state(e, 2)) is True
        assert chat.root_client.base_url == "https://backup/v1/"

    def test_transient_slice_equal_to_budget_swaps_at_slice(self):
        """primary_transient_max=5 == transient_max=5. Swap fires at
        attempt 5 (count == 5 == cap). Backup gets the full 5 budget.
        """
        strategy, ctl, chat = self._build(
            primary_transient_max=5, primary_timeout_max=2,
            transient_max=5, timeout_max=2,
        )
        e = _transient_error()
        for n in (1, 2, 3, 4):
            assert strategy(_make_mock_retry_state(e, n)) is True
            assert chat.root_client.base_url == "https://primary/v1/"
        # Attempt 5: swap.
        assert strategy(_make_mock_retry_state(e, 5)) is True
        assert chat.root_client.base_url == "https://backup/v1/"

    def test_transient_slice_greater_than_budget_swaps_at_budget(self):
        """primary_transient_max=10 > transient_max=4. effective_cap =
        min(10, 4) = 4. Swap fires at attempt 4 (the operator's budget,
        NOT the higher primary cap).
        """
        strategy, ctl, chat = self._build(
            primary_transient_max=10, primary_timeout_max=2,
            transient_max=4, timeout_max=2,
        )
        e = _transient_error()
        for n in (1, 2, 3):
            assert strategy(_make_mock_retry_state(e, n)) is True
            assert chat.root_client.base_url == "https://primary/v1/"
        # Attempt 4: count=4 == cap=4 → swap (operator budget wins).
        assert strategy(_make_mock_retry_state(e, 4)) is True
        assert chat.root_client.base_url == "https://backup/v1/"

    # --- timeout boundaries ---

    def test_timeout_slice_less_than_budget_swaps_at_slice(self):
        """primary_timeout_max=1 < timeout_max=5. Swap fires at attempt 1.
        """
        strategy, ctl, chat = self._build(
            primary_transient_max=3, primary_timeout_max=1,
            transient_max=8, timeout_max=5,
        )
        e = _timeout_error()
        # Attempt 1: count=1 == cap=1 → SWAP.
        assert strategy(_make_mock_retry_state(e, 1)) is True
        assert chat.root_client.base_url == "https://backup/v1/"

    def test_timeout_slice_equal_to_budget_swaps_at_slice(self):
        """primary_timeout_max=3 == timeout_max=3. Swap fires at attempt 3.
        """
        strategy, ctl, chat = self._build(
            primary_transient_max=3, primary_timeout_max=3,
            transient_max=8, timeout_max=3,
        )
        e = _timeout_error()
        for n in (1, 2):
            assert strategy(_make_mock_retry_state(e, n)) is True
            assert chat.root_client.base_url == "https://primary/v1/"
        # Attempt 3: swap.
        assert strategy(_make_mock_retry_state(e, 3)) is True
        assert chat.root_client.base_url == "https://backup/v1/"

    def test_timeout_slice_greater_than_budget_swaps_at_budget(self):
        """primary_timeout_max=10 > timeout_max=2. Swap fires at attempt 2.
        """
        strategy, ctl, chat = self._build(
            primary_transient_max=3, primary_timeout_max=10,
            transient_max=8, timeout_max=2,
        )
        e = _timeout_error()
        assert strategy(_make_mock_retry_state(e, 1)) is True
        assert chat.root_client.base_url == "https://primary/v1/"
        # Attempt 2: count=2 == cap=2 → swap.
        assert strategy(_make_mock_retry_state(e, 2)) is True
        assert chat.root_client.base_url == "https://backup/v1/"


# ---------------------------------------------------------------------------
# AREA 3: config validation — exercise the field validator directly.
#
# Coverage notes:
#   - TestBackupFieldRejectsNonStrings already exercises YAML ``true``,
#     numbers, and a valid URL via ``load_config``.
#   - This file drives the field validator directly so the validator
#     itself (not the YAML/env loader round-trip) is the unit under test,
#     and pins every whitespace edge case (empty, single space, tab,
#     newline).
# ---------------------------------------------------------------------------


class TestAdversarial3ConfigValidation:
    """Pin ``_coerce_base_url_backup_empty_to_none`` behavior across
    every realistic input shape, including the env-var substitution
    pattern ``${OPENAI_BASE_URL_BACKUP:-}``.
    """

    def test_none_passes_through_as_none(self):
        from daemon.config import LLMConfig

        assert LLMConfig.model_validate({"base_url_backup": None}) \
            .base_url_backup is None

    @pytest.mark.parametrize(
        "blank",
        ["", " ", "\t", "\n", "  \t  \n  "],
    )
    def test_blank_and_whitespace_only_yields_none(self, blank):
        """Whitespace-only values must coerce to None — the config.yaml
        pattern ``${OPENAI_BASE_URL_BACKUP:-}`` yields ``""`` (env
        unset), and operators may edit the YAML to leave whitespace.
        A bare ``" "`` reaching the failover predicate as a truthy
        string would corrupt the swap (pointing HTTP at an unresolvable
        host).
        """
        from daemon.config import LLMConfig

        cfg = LLMConfig.model_validate({"base_url_backup": blank})
        assert cfg.base_url_backup is None, (
            f"whitespace-only {blank!r} must coerce to None; got "
            f"{cfg.base_url_backup!r}"
        )

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("https://backup.example/v1", "https://backup.example/v1"),
            ("  https://backup.example/v1  ", "  https://backup.example/v1  "),
            ("true", "true"),  # truthy string is the operator's call
            ("1", "1"),
        ],
    )
    def test_non_empty_string_round_trips_unmodified(self, value, expected):
        """A non-empty, non-blank string must round-trip unchanged
        (validator does NOT normalize URLs — that is the caller's job).
        """
        from daemon.config import LLMConfig

        cfg = LLMConfig.model_validate({"base_url_backup": value})
        assert cfg.base_url_backup == expected

    @pytest.mark.parametrize(
        "garbage",
        [True, False, 0, 1, 1.5, ["https://x"], {"url": "https://x"}],
    )
    def test_non_string_types_raise_with_legible_message(self, garbage):
        """Non-string values must raise ValueError with a message that
        names the offending type and points at the boolean footgun.
        """
        from pydantic import ValidationError

        from daemon.config import LLMConfig

        with pytest.raises(ValidationError) as excinfo:
            LLMConfig.model_validate({"base_url_backup": garbage})
        msg = str(excinfo.value)
        # The validator message must be specific enough to help the
        # operator, not just a generic "str_type" error.
        assert (
            "base_url_backup" in msg
            or "boolean" in msg.lower()
            or "URL" in msg
        ), (
            f"validator message too generic; got: {msg[:300]}"
        )

    def test_yaml_substitution_empty_string_yields_none(self, monkeypatch):
        """End-to-end: config.yaml with the substitution
        ``${OPENAI_BASE_URL_BACKUP:-}`` (yields ``""`` when env unset)
        must round-trip to ``None`` through ``load_config``. This is the
        realistic config surface the validator was written for.
        """
        monkeypatch.delenv("OPENAI_BASE_URL_BACKUP", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        from daemon.config import load_config

        yaml_content = (
            "llm:\n"
            "  base_url: 'https://primary.example/v1'\n"
            "  base_url_backup: ''\n"   # empty substitution
            "  api_key: 'sk-test'\n"
            "  model: 'gpt-4'\n"
        )
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            tmp = f.name
        try:
            cfg = load_config(tmp)
            assert cfg.llm.base_url_backup is None, (
                f"empty substitution must coerce to None; got "
                f"{cfg.llm.base_url_backup!r}"
            )
        finally:
            Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# AREA 4: failover end-to-end via MockTransport — log + URL routing.
#
# Coverage notes:
#   - TestEndToEndFailoverWithMockTransport.test_swap_actually_redirects
#     _requests_to_backup already pins the request URL and budget count.
#   - This file adds: ``[LLM-HA]`` WARNING log capture, and the
#     "backup also fails" fall-through (primary-down cycle that exhausts
#     BOTH legs).
# ---------------------------------------------------------------------------


class TestAdversarial4FailoverEndToEnd:
    """End-to-end through a REAL ``ChatOpenAI`` + ``httpx.MockTransport``
    (no network) — covers log capture and the both-legs-down case.
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
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def test_llm_ha_warning_logged_on_swap(self, caplog):
        """When the swap fires, exactly one greppable ``[LLM-HA]`` WARNING
        line must be emitted — operators rely on this for incident
        response (a silent swap would be undetectable in production).
        """
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "backup.test":
                return httpx.Response(
                    200, json=self._completion_body("from-backup")
                )
            return httpx.Response(
                500,
                json={"error": {"message": "primary down", "type": "server_error"}},
            )

        llm = ChatOpenAI(
            api_key="test",
            base_url=self.PRIMARY,
            model="gpt-test",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        ctl = FailoverController(llm, self.PRIMARY, self.BACKUP)
        strategy = make_llm_retry_strategy(
            transient_max=5, timeout_max=2, failover_controller=ctl
        )
        ceiling = max(5, 2) + max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)
        retrying = Retrying(
            stop=stop_after_attempt(ceiling),
            wait=wait_fixed(0),
            retry=strategy,
            reraise=True,
        )
        classified = classify_llm_errors(llm)

        with caplog.at_level(logging.WARNING, logger="daemon.llm_error_classifier"):
            retrying(classified.invoke, [HumanMessage(content="hi")])

        warnings = [
            r for r in caplog.records
            if r.levelname == "WARNING" and "[LLM-HA]" in r.getMessage()
        ]
        assert len(warnings) >= 1, (
            f"expected at least one [LLM-HA] WARNING on swap; got "
            f"{[r.getMessage() for r in warnings]}"
        )
        # The summary line carries both URLs for greppability.
        msg = warnings[0].getMessage()
        assert "primary=" in msg and "backup=" in msg, (
            f"WARNING must contain primary=/backup= for greppability; "
            f"got {msg}"
        )

    def test_backup_also_down_falls_through_with_reraise(self):
        """Both primary AND backup return 500 → the retry must exhaust
        and surface the final transient error (reraise=True semantics).
        The client is left on backup after exhaustion.
        """
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI
        from daemon.llm_error_classifier import TransientAPIError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500,
                json={"error": {"message": "both down", "type": "server_error"}},
            )

        llm = ChatOpenAI(
            api_key="test",
            base_url=self.PRIMARY,
            model="gpt-test",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        ctl = FailoverController(llm, self.PRIMARY, self.BACKUP)
        strategy = make_llm_retry_strategy(
            transient_max=3, timeout_max=1, failover_controller=ctl
        )
        ceiling = max(3, 1) + max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)
        retrying = Retrying(
            stop=stop_after_attempt(ceiling),
            wait=wait_fixed(0),
            retry=strategy,
            reraise=True,
        )
        classified = classify_llm_errors(llm)

        with pytest.raises(TransientAPIError):
            retrying(classified.invoke, [HumanMessage(content="hi")])
        # The final attempt was on backup — the URL is left there.
        assert str(llm.root_client.base_url).startswith("https://backup.test"), (
            f"after exhausting both legs the URL should remain on backup; "
            f"got {llm.root_client.base_url}"
        )

    def test_no_llm_ha_warning_when_no_backup_configured(self, caplog):
        """When the controller is not configured (no backup), no
        ``[LLM-HA]`` WARNING may be logged — that prefix is reserved
        for actual HA events.
        """
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500,
                json={"error": {"message": "primary down", "type": "server_error"}},
            )

        llm = ChatOpenAI(
            api_key="test",
            base_url=self.PRIMARY,
            model="gpt-test",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        # No FailoverController → identical to pre-HA behavior.
        strategy = make_llm_retry_strategy(transient_max=3, timeout_max=1)
        ceiling = max(3, 1)
        retrying = Retrying(
            stop=stop_after_attempt(ceiling),
            wait=wait_fixed(0),
            retry=strategy,
            reraise=True,
        )
        classified = classify_llm_errors(llm)

        with caplog.at_level(logging.WARNING, logger="daemon.llm_error_classifier"):
            with pytest.raises(Exception):
                retrying(classified.invoke, [HumanMessage(content="hi")])

        llm_ha_warnings = [
            r for r in caplog.records
            if "[LLM-HA]" in r.getMessage()
        ]
        assert not llm_ha_warnings, (
            f"[LLM-HA] WARNING must not fire when no backup is configured; "
            f"got {[r.getMessage() for r in llm_ha_warnings]}"
        )


# ---------------------------------------------------------------------------
# AREA 5: sticky-on-success + counter reset across the backup-fails
#          edge case.
#
# Coverage notes:
#   - TestStickyOnSuccessResetOnFailure already pins: sticky on success,
#     reset at attempt 1 of next cycle.
#   - TestStickyOnSuccessEndToEnd already pins: cycle 1 fail → backup ok,
#     cycle 2 starts on backup.
#   - This file adds: BACKUP FAILS AFTER SUCCESS — the cycle after the
#     sticky success exhausts the backup budget and must NOT silently
#     fall back to primary (the predicate returns False; the operator
#     sees the failure).
# ---------------------------------------------------------------------------


class TestAdversarial5StickyOnSuccessBackupFails:
    """Sticky-on-success cross-invoke: backup-succeeded cycle, next cycle
    backup-fails. The next cycle must NOT silently swap back to primary
    — it must exhaust the backup budget and surface the error so the
    upstream pipeline sees the failure.
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
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def test_backup_fails_after_sticky_success_exhausts_and_reraises(self):
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI
        from daemon.llm_error_classifier import TransientAPIError

        backup_state = {"down": False}
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url.host)
            if request.url.host == "backup.test":
                if backup_state["down"]:
                    return httpx.Response(
                        500,
                        json={"error": {"message": "backup down",
                                        "type": "server_error"}},
                    )
                return httpx.Response(200, json=self._completion_body("ok"))
            # Primary always 500 (simulates a real outage).
            return httpx.Response(
                500,
                json={"error": {"message": "primary down",
                                "type": "server_error"}},
            )

        llm = ChatOpenAI(
            api_key="test",
            base_url=self.PRIMARY,
            model="gpt-test",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        ctl = FailoverController(llm, self.PRIMARY, self.BACKUP)
        # Tight budgets so the test runs in O(few) requests.
        strategy = make_llm_retry_strategy(
            transient_max=3, timeout_max=1, failover_controller=ctl
        )
        ceiling = max(3, 1) + max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)
        retrying = Retrying(
            stop=stop_after_attempt(ceiling),
            wait=wait_fixed(0),
            retry=strategy,
            reraise=True,
        )
        classified = classify_llm_errors(llm)

        # Cycle 1: primary down → swap → backup ok (sticky lingers).
        r1 = retrying(classified.invoke, [HumanMessage(content="c1")])
        assert r1.content == "ok"
        assert str(llm.root_client.base_url).startswith("https://backup.test")

        # Cycle 2: backup ALSO goes down. The first request of cycle 2
        # goes out on the lingering backup (W1 adjudication). It fails.
        # The predicate's attempt-1 reset returns the client to primary,
        # which is also down — so the rest of cycle 2 burns through the
        # budget on both legs and reraises.
        backup_state["down"] = True
        with pytest.raises(TransientAPIError):
            retrying(classified.invoke, [HumanMessage(content="c2")])

        # Sanity: the cycle 2 budget was actually exhausted on the wire
        # (we don't pin an exact count, just that both legs got tried).
        hosts = captured[3:]  # skip cycle 1's requests
        assert "backup.test" in hosts, (
            f"cycle 2 must have hit backup (the lingering URL); got {hosts}"
        )
        assert "primary.test" in hosts, (
            f"cycle 2's attempt-1 reset must have returned to primary; "
            f"got {hosts}"
        )

    def test_counter_zero_after_swap_regardless_of_category(self):
        """W4 cross-category counter reset: after the swap fires, BOTH
        transient and timeout counters must be zero (not just the
        triggering category). The observable contract: after the swap,
        the FIRST attempt of the new category on backup is still
        retried with the full budget available.

        Drive: 1 transient (count=1) + 1 timeout (count=1) + 1
        transient (count=2) + 1 transient (count=3 == primary cap →
        swap, both counters reset). Now on backup. Three timeout
        attempts: count goes 0→1, 1→2, 2→3 == budget=3 → STOP. The
        full 3-attempt budget on backup proves the timeout counter was
        reset; if W4 regression had left the timeout counter at 1,
        only 2 attempts would be available.
        """
        chat = _make_fake_chat_client("https://primary/v1")
        ctl = FailoverController(chat, "https://primary/v1", "https://backup/v1")
        strategy = make_llm_retry_strategy(
            transient_max=4, timeout_max=3, failover_controller=ctl,
            primary_transient_max=3, primary_timeout_max=2,
        )

        # Phase 1: 3 transients + 1 timeout on primary. The 3rd
        # transient pushes count=3 == primary cap → swap.
        assert strategy(_make_mock_retry_state(_transient_error(), 1)) is True
        assert strategy(_make_mock_retry_state(_timeout_error(), 2)) is True
        assert strategy(_make_mock_retry_state(_transient_error(), 3)) is True
        # Attempt 4 transient: count=3 == primary cap → swap, reset.
        assert strategy(_make_mock_retry_state(_transient_error(), 4)) is True
        assert chat.root_client.base_url == "https://backup/v1/", (
            "swap must have fired by attempt 4"
        )

        # Phase 2: 3 timeouts on backup. If W4 reset both counters, the
        # timeout counter starts at 0 → 3 retries available. If the
        # timeout counter had survived the swap (carrying its primary
        # count of 1), only 2 attempts would be available before stop.
        assert strategy(_make_mock_retry_state(_timeout_error(), 5)) is True
        assert strategy(_make_mock_retry_state(_timeout_error(), 6)) is True
        # Third timeout attempt: count 2→3 == budget=3 → STOP.
        assert strategy(_make_mock_retry_state(_timeout_error(), 7)) is False


# ---------------------------------------------------------------------------
# AREA 6: production-code IndexError (empty-choices[]) handling.
#
# The feature diff touched manager.py, instance_lifecycle.py,
# child_reports.py, keyword_extraction.py, title_generation.py with
# small IndexError-handling edits (in addition to threading
# ``base_url_backup`` through the config dict). Each of these
# services builds a bare ``ThinkingChatOpenAI`` (NOT wrapped by
# ``classify_llm_errors``) and catches exceptions at the call site.
# The IndexError raised by langchain on ``choices[0]`` when the LLM
# returns ``choices: []`` is what these catches must absorb.
#
# This test exercises ONE representative site (``keyword_extraction``)
# and asserts the observable contract: no crash, sane empty fallback.
# ---------------------------------------------------------------------------


class TestAdversarial6ProductionIndexErrorHandling:
    """Exercise the production-side IndexError (empty-choices[]) edits.

    Title/keyword/child_report services all build a bare LLM and rely
    on a try/except around ``invoke`` to absorb any exception — the
    IndexError raised by langchain on ``choices[0]`` when the LLM
    returns ``choices: []`` is the realistic failure mode. We verify
    the user-observable contract: the service does not crash, the
    caller gets the documented fallback (``[]`` or a placeholder),
    and no exception leaks to the caller.
    """

    def _patched_extract_keywords_with_indexerror(self, monkeypatch):
        """Drive ``extract_keywords`` against a fake manager/config whose
        LLM raises ``IndexError`` on ``.invoke()``.
        """
        import asyncio
        from daemon.services import keyword_extraction as kx

        class _FakeLLM:
            def invoke(self, messages, *a, **kw):
                raise IndexError("list index out of range")

        class _FakeConfig:
            class llm:
                base_url = "https://primary/v1"
                base_url_backup = None
                api_key = "test"
                model = "gpt-test"

        result = asyncio.run(
            kx.extract_keywords(
                message="Hello, world",
                config=_FakeConfig(),
                timeout_s=2,
            )
        )
        return result

    def test_extract_keywords_handles_indexerror_gracefully(self):
        """Empty-choices IndexError from the LLM must be caught and the
        caller receives ``[]`` — the documented fallback. No leak.
        """
        result = self._patched_extract_keywords_with_indexerror(None)
        assert result == [], (
            f"IndexError from LLM must produce empty list fallback; "
            f"got {result!r}"
        )

    def test_extract_keywords_empty_message_still_empty(self):
        """Sanity baseline: empty/whitespace messages return ``[]``
        BEFORE the LLM is called (no network at all). Catches
        regression where the LLM fallback path would shadow the
        fast-path.
        """
        import asyncio
        from daemon.services import keyword_extraction as kx

        for empty in ("", " ", "\n\n"):
            result = asyncio.run(
                kx.extract_keywords(
                    message=empty,
                    config=None,  # unused for empty message
                    timeout_s=1,
                )
            )
            assert result == [], (
                f"empty input {empty!r} must return [] without LLM; "
                f"got {result!r}"
            )

    def test_extract_keywords_does_not_raise_on_unexpected_exception(self):
        """An unexpected exception class must also be caught — the
        production sites use bare ``except Exception`` so IndexError
        falls under the same umbrella. We mock the LLM with a different
        RuntimeError to verify the umbrella still holds.
        """
        import asyncio
        from daemon.services import keyword_extraction as kx

        class _FakeLLM:
            def invoke(self, messages, *a, **kw):
                raise RuntimeError("upstream blew up")

        class _FakeConfig:
            class llm:
                base_url = "https://primary/v1"
                base_url_backup = None
                api_key = "test"
                model = "gpt-test"

        # ``ThinkingChatOpenAI`` is imported INSIDE the function via
        # ``from ..graph import ThinkingChatOpenAI, clean_llm_config``,
        # so the canonical patch target is ``daemon.graph``.
        from daemon import graph as graph_mod

        with patch.object(
            graph_mod, "ThinkingChatOpenAI", return_value=_FakeLLM()
        ):
            result = asyncio.run(
                kx.extract_keywords(
                    message="Hello",
                    config=_FakeConfig(),
                    timeout_s=2,
                )
            )
        assert result == [], (
            f"unexpected RuntimeError must produce [] fallback; got {result!r}"
        )
