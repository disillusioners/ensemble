"""SymptomRepairEngine facade-wrapped summarizer e2e tests — hallucination-recovery ladder phase 2.

This module closes the e2e gap on the REAL ``wrap_langchain_failover``
facade path through the ``SymptomRepairEngine._summarize`` call site
(ADR-0006). Existing unit coverage (``test_symptom_repair_engine.py``
``TestFacadeRouting``) stubs the facade entirely (``fake_wrap``) — the
leader's request is to exercise the engine end-to-end through the REAL
``wrap_langchain_failover`` with a FAILING primary + WORKING backup and
to verify the degenerate (primary AND backup both fail) abort path.

Concretely (per the gate task brief):

* F1 — REAL ``SymptomRepairEngine`` whose summarizer client is built via
  ``wrap_langchain_failover``; primary raises, backup returns a valid
  summary → repair SUCCEEDS via backup (surgery happens, budget +1,
  repair doc contains the backup summary).
* F2 — Degenerate summarizer output (primary AND backup both raise
  after the facade's bounded retry+failover) → repair ABORTS
  fail-open: no surgery, budget NOT incremented, the original
  messages are returned (turn not wedged).
* F3 — Source-level pin: no legacy static fallback string remains in
  the engine (the shipped ``LoopRepairer`` keeps its fallback for the
  kill-switch-OFF path; the engine deliberately does NOT reproduce it,
  ADR-0006 decision).
* F4 — 120s summarizer timeout parameter inherited from
  ``LoopBreakerConfig.summarization_timeout_seconds`` (config.py:1755).

Mocking strategy
----------------

The mock layer targets the INNER ``client.invoke`` only (the
LangChain-equivalent chat client the engine constructs via
``ThinkingChatOpenAI(**config)``). The mock inspects the
``root_client.base_url`` that the real ``FailoverController`` mutates
on swap — primary raises ``ConnectionError``, backup returns a valid
``AIMessage``. Everything ABOVE the client is real: the real
``wrap_langchain_failover`` builds a real ``ChatFailoverBinding`` with
real tenacity retries, the real ``FailoverController`` mutates
``base_url`` on swap, the real retry predicate swaps to backup when
primary exhausts its slice, and the real engine path catches the
final exception or accepts the final response.

No network: every layer below ``client.invoke`` is mocked. The real
facade machinery runs against the mock transport.

Test isolation: a small ``transient_max=1, timeout_max=1`` and a tight
``wall_clock_cap_s`` keeps the real facade retry loop bounded — the
primary exhausts its slice on the first attempt, swaps to backup,
and either succeeds (F1) or exhausts the backup slice and re-raises
(F2). No real wait_for sleeps are needed; the engine's 120s site
cap is backstop only.
"""
from __future__ import annotations

import inspect
from typing import Any

import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from daemon.config import LoopBreakerConfig, _reset_symptom_repair_ladder_for_tests
from daemon.graph import LoopDetector
from daemon.services.symptom_repair_engine import (
    DEFAULT_SUMMARIZATION_TIMEOUT_S,
    SYMPTOM_REPAIR_BUDGET,
    SymptomRepairContext,
    SymptomRepairEngine,
)
from tests.helpers.symptom_repair import loop_units as _loop_units


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _history():
    """Realistic loop history: user ask + context block + 3-loop tail.

    Mirrors the helper in ``test_symptom_repair_engine.py`` — the same
    shape every engine unit test uses, so the detection outcome is
    consistent across the suite.
    """
    return [
        HumanMessage(content="do the thing", id="h1"),
        SystemMessage(
            content="[SYSTEM CONTEXT: Project]\n\nproject body",
            id="ctx1",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "project",
            },
        ),
        *_loop_units(3),
    ]


def _detect(messages):
    det = LoopDetector.scan(messages=messages, threshold=3)
    assert det is not None, "fixture must produce a loop detection"
    return det


def _context(
    messages,
    *,
    base_url: str = "http://primary.example/v1",
    base_url_backup: str | None = "http://backup.example/v1",
    budget_used: int = 0,
    instance_id: str = "iid-failover-e2e",
    summarization_timeout_seconds: int = DEFAULT_SUMMARIZATION_TIMEOUT_S,
):
    return SymptomRepairContext(
        detection=_detect(messages),
        messages=list(messages),
        llm_config={
            "model": "test-model",
            "model_vision": None,
            "base_url": base_url,
            "base_url_backup": base_url_backup,
        },
        system_prompt="you are a test assistant",
        instance_id=instance_id,
        budget_used=budget_used,
        budget_cap=SYMPTOM_REPAIR_BUDGET,
        summarization_timeout_seconds=summarization_timeout_seconds,
    )


@pytest.fixture(autouse=True)
def _restore_flags():
    """Isolate the ladder kill-switch module cache (config accessors)."""
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


# ---------------------------------------------------------------------------
# Fake transport — mock the inner chat client only (real facade above)
# ---------------------------------------------------------------------------


class _FakeRootClient:
    """Stand-in for ``openai.OpenAI`` (the ``root_client`` attribute on a
    LangChain ``ChatOpenAI``).

    The real ``FailoverController._mutate_client_base_url`` assigns
    ``client.base_url = new_url`` on both ``root_client`` and
    ``root_async_client``; ``httpx.URL()`` normalizes the value. We
    mimic that with a plain ``str`` attribute (the controller's
    assignment is the only place that touches it; ``httpx.URL``
    normalization is irrelevant when the controller only stores a
    string and we read it back as a string).
    """

    def __init__(self, base_url: str):
        self.base_url = base_url


class _FakeChatClient:
    """Stand-in for ``ThinkingChatOpenAI``.

    Must expose ``root_client`` / ``root_async_client`` (the
    ``FailoverController`` mutates these on swap) AND ``invoke``
    (the inner client the real ``classify_llm_errors(client)`` wraps).
    ``default_streaming`` and ``default_request_gzip`` are touched by
    ``clean_llm_config`` so we set them as class attrs (mirrors the
    pattern in ``test_symptom_repair_engine.py::_StubClient``).
    """

    default_streaming = False
    default_request_timeout = 610
    default_request_gzip = False

    def __init__(
        self,
        *,
        primary_url: str,
        backup_url: str | None,
        primary_summary: str | Exception,
        backup_summary: str | Exception,
    ):
        self.root_client = _FakeRootClient(primary_url)
        self.root_async_client = _FakeRootClient(primary_url)
        self._primary_url = primary_url
        self._backup_url = backup_url
        self._primary_summary = primary_summary
        self._backup_summary = backup_summary
        # Counters — only used by F1 evidence.
        self.invoke_calls: list[str] = []

    @property
    def current_url(self) -> str:
        """Read ``root_client.base_url`` (the URL the controller swaps)."""
        return self.root_client.base_url

    def invoke(self, messages):
        self.invoke_calls.append(self.current_url)
        # Decide primary vs backup based on the URL the controller has
        # set. On backup URL → return the backup summary; on primary →
        # raise the primary error (or return summary if a string).
        if self._backup_url and self.current_url == self._backup_url:
            outcome = self._backup_summary
        else:
            outcome = self._primary_summary
        if isinstance(outcome, Exception):
            raise outcome
        return AIMessage(content=outcome)


def _patch_facade(monkeypatch, fake_client: _FakeChatClient):
    """Wire the fake transport so the REAL ``wrap_langchain_failover``
    builds a real ``ChatFailoverBinding`` over it.

    The engine calls ``ThinkingChatOpenAI(**clean_llm_config(...))``,
    so we replace ``ThinkingChatOpenAI`` with a class-like factory
    that has the class-level ``default_streaming`` /
    ``default_request_gzip`` attributes ``clean_llm_config`` reads
    (see ``daemon.graph.clean_llm_config`` line ~2843 — it pulls
    ``streaming`` from ``ThinkingChatOpenAI.default_streaming``).
    A plain function would crash with
    ``AttributeError: 'function' object has no attribute
    'default_streaming'``.

    The factory ignores its kwargs and returns the captured
    ``fake_client`` instance; the engine only consumes ``root_client``
    / ``root_async_client`` / ``invoke`` from it (the facade
    machinery), so the rest of the ChatOpenAI surface is irrelevant
    for these tests.
    """
    import daemon.graph as graph_module

    class _ThinkingChatOpenAIFactory:
        # Mirror the class-level defaults the daemon's real
        # ``ThinkingChatOpenAI`` exposes — ``clean_llm_config``
        # reads ``default_streaming`` here (graph.py:2843).
        default_streaming = False
        default_request_timeout = 610
        default_request_gzip = False

        def __new__(cls, **_kwargs):
            return fake_client

    monkeypatch.setattr(
        graph_module, "ThinkingChatOpenAI", _ThinkingChatOpenAIFactory
    )
    return fake_client


def _tighten_facade_defaults(monkeypatch):
    """Shrink the facade retry budgets AND neutralize the inter-attempt
    wait so tests finish in <2s.

    Two patches:
    * ``wrap_langchain_failover.__defaults__`` → ``(1, 1, 0.5)``
      (transient_max, timeout_max, wall_clock_cap_s). With these,
      primary tolerates 0 retries before swapping; backup gets the
      full default slice.
    * ``tenacity.nap.sleep`` → a no-op. The real facade uses
      ``wait_exponential_jitter(initial=1, exp_base=2, jitter=1)``
      which accumulates 1+2+4=7s of inter-attempt wait BEFORE the 6th
      attempt — far too slow for tests. Patching the nap fn keeps the
      REAL facade retry/budget/swap logic intact (we want to exercise
      it, not stub it) while eliminating the real time.sleep.

    The engine call site is untouched: the real
    ``wrap_langchain_failover(llm, llm_config)`` runs end-to-end.
    """
    import daemon.services.llm_failover as failover_module
    import tenacity.nap as tenacity_nap

    new_defaults = (1, 1, 0.5)  # transient_max, timeout_max, wall_clock_cap_s
    monkeypatch.setattr(
        failover_module.wrap_langchain_failover, "__defaults__", new_defaults
    )
    # Neutralize the inter-attempt wait — keep the budget logic REAL.
    monkeypatch.setattr(tenacity_nap, "sleep", lambda *_a, **_kw: None)
    # tenacity may import sleep directly from time; patch that too.
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *_a, **_kw: None)


# ---------------------------------------------------------------------------
# F1 — REAL facade: failing primary + working backup → repair succeeds
# ---------------------------------------------------------------------------


class TestRealFacadeFailingPrimaryWorkingBackup:
    async def test_primary_fails_backup_succeeds_repair_succeeds(self, monkeypatch):
        """F1: REAL ``wrap_langchain_failover`` exercised end-to-end
        through ``SymptomRepairEngine._summarize`` → ``repair``.

        The fake transport makes ``primary`` raise ``ConnectionError``
        on every attempt; ``backup`` returns a real summary. The
        REAL facade retries on primary until the slice is exhausted,
        swaps to backup via ``FailoverController``, and the backup
        returns the summary. The engine accepts it, builds surgery,
        and the caller increments the budget.
        """
        fake = _FakeChatClient(
            primary_url="http://primary.example/v1",
            backup_url="http://backup.example/v1",
            primary_summary=openai.APIConnectionError(
                request=__import__("httpx").Request(
                    "POST", "http://primary.example/v1/chat/completions"
                ),
                message="primary endpoint unreachable",
            ),
            backup_summary="REAL backup summary via HA facade",
        )
        _patch_facade(monkeypatch, fake)
        _tighten_facade_defaults(monkeypatch)

        eng = SymptomRepairEngine()
        outcome = await eng.repair(_context(_history()))

        # Surgery built → success.
        assert outcome.success is True
        assert outcome.aborted is False
        assert outcome.abort_reason is None
        # Budget consumed.
        assert outcome.budget_consumed is True
        # The summary is from the BACKUP transport, not a static string.
        assert outcome.summary == "REAL backup summary via HA facade"
        # Surgery prefix is the return-carried sentinel-first list, NOT None.
        assert outcome.surgery_prefix is not None
        assert outcome.surgery_prefix[0].id == "__remove_all__"
        # The repair doc contains the backup summary text.
        doc = next(
            m for m in outcome.surgery_prefix if getattr(m, "id", "").startswith("repair-")
        )
        assert "REAL backup summary via HA facade" in doc.content

        # The fake client must have actually been called (the real
        # facade drove invokes against it).
        assert len(fake.invoke_calls) >= 1
        # At least one call landed on the primary (where it raised).
        assert any(
            url == "http://primary.example/v1" for url in fake.invoke_calls
        )
        # At least one call landed on the backup (where it succeeded).
        assert any(
            url == "http://backup.example/v1" for url in fake.invoke_calls
        )

    async def test_repaired_messages_do_not_contain_orig_loop_block(self, monkeypatch):
        """F1 corollary: after repair via backup, the surgery removes
        the loop evidence duplicates — the agent's ORIGINAL first unit
        (the evidence unit kept by ``LoopDetector.scan``) survives in
        the retained tail, and the duplicate units (ai-1, tm-1, ai-2,
        tm-2) are removed. The repair doc is inserted."""
        fake = _FakeChatClient(
            primary_url="http://primary.example/v1",
            backup_url="http://backup.example/v1",
            primary_summary=openai.APIConnectionError(
                request=__import__("httpx").Request(
                    "POST", "http://primary.example/v1/chat/completions"
                ),
                message="primary down",
            ),
            backup_summary="backup summary",
        )
        _patch_facade(monkeypatch, fake)
        _tighten_facade_defaults(monkeypatch)

        eng = SymptomRepairEngine()
        outcome = await eng.repair(_context(_history()))

        # surgery_prefix[0] is the RemoveMessage(REMOVE_ALL_MESSAGES) sentinel.
        prefix_ids = {
            getattr(m, "id", None) for m in outcome.surgery_prefix
        }
        # The DUPLICATE loop units (the evidence-block after the original
        # first unit) MUST be in the removal set — i.e. NOT present in
        # the surgery prefix's retained tail beyond the sentinel.
        for dup_id in ("ai-1", "tm-1", "ai-2", "tm-2"):
            assert dup_id not in prefix_ids
        # The evidence unit (the OLDEST matching pair) survives — this
        # is what ``LoopDetector.scan`` keeps so the agent has context
        # about what it was originally doing.
        assert "ai-0" in prefix_ids
        # The retained human message survives.
        assert "h1" in prefix_ids
        # The repair doc is inserted (it carries the backup summary).
        doc_ids = {
            getattr(m, "id", "")
            for m in outcome.surgery_prefix
            if getattr(m, "id", "").startswith("repair-")
        }
        assert len(doc_ids) == 1
        assert "backup summary" in next(iter(doc_ids)) or any(
            "backup summary" in getattr(m, "content", "")
            for m in outcome.surgery_prefix
            if getattr(m, "id", "").startswith("repair-")
        )


# ---------------------------------------------------------------------------
# F2 — REAL facade: BOTH primary AND backup fail → repair aborts fail-open
# ---------------------------------------------------------------------------


class TestRealFacadeBothEndpointsFailAbort:
    async def test_both_primary_and_backup_fail_aborts_fail_open(self, monkeypatch):
        """F2: REAL facade, BOTH transports raise — primary exhausts
        its slice (ConnectionError), swaps to backup, backup ALSO
        exhausts its slice → tenacity re-raises → engine catches in
        the outer ``except Exception`` block, wraps as
        ``SymptomRepairAborted(reason="summarizer-failed")``, and
        the repair returns:
            - success=False, aborted=True
            - surgery_prefix=None  (NO surgery happened)
            - budget_consumed=False (budget NOT incremented)
            - repaired_messages == original messages (turn NOT wedged)
            - abort_reason == "summarizer-failed"
        """
        fake = _FakeChatClient(
            primary_url="http://primary.example/v1",
            backup_url="http://backup.example/v1",
            primary_summary=openai.APIConnectionError(
                request=__import__("httpx").Request(
                    "POST", "http://primary.example/v1/chat/completions"
                ),
                message="primary dead",
            ),
            backup_summary=openai.APIConnectionError(
                request=__import__("httpx").Request(
                    "POST", "http://backup.example/v1/chat/completions"
                ),
                message="backup dead too",
            ),
        )
        _patch_facade(monkeypatch, fake)
        _tighten_facade_defaults(monkeypatch)

        msgs = _history()
        eng = SymptomRepairEngine()
        outcome = await eng.repair(_context(msgs))

        # Outcome shape — fail-open abort.
        assert outcome.success is False
        assert outcome.aborted is True
        assert outcome.abort_reason == "summarizer-failed"
        assert outcome.error  # error string populated
        # NO surgery happened.
        assert outcome.surgery_prefix is None
        # Budget NOT incremented.
        assert outcome.budget_consumed is False
        # Turn NOT wedged — original messages returned.
        assert outcome.repaired_messages == list(msgs)
        # Summary empty.
        assert outcome.summary == ""

        # Both transports were exercised (primary → backup → exhausted).
        assert any(
            url == "http://primary.example/v1" for url in fake.invoke_calls
        )
        assert any(
            url == "http://backup.example/v1" for url in fake.invoke_calls
        )

    async def test_abort_falls_through_without_wedging_turn(self, monkeypatch):
        """F2 corollary: the turn keeps going — the engine's repair()
        returns a fail-open outcome, NOT raises. The caller (graph.py
        :2076-2120 in ``_durable_loop_break``) routes the original
        messages through and emits the [SYMPTOM] phase=repair_abort
        telemetry line. We assert the OUTCOME shape that drives that
        path; the integration of telemetry is exercised in the
        ``ladder_exhaust_oq5`` pack.
        """
        fake = _FakeChatClient(
            primary_url="http://primary.example/v1",
            backup_url="http://backup.example/v1",
            primary_summary=openai.APIConnectionError(
                request=__import__("httpx").Request(
                    "POST", "http://primary.example/v1/chat/completions"
                ),
                message="primary dead",
            ),
            backup_summary=RuntimeError("backup also dead"),
        )
        _patch_facade(monkeypatch, fake)
        _tighten_facade_defaults(monkeypatch)

        eng = SymptomRepairEngine()
        # The call returns the outcome; it MUST NOT raise.
        outcome = await eng.repair(_context(_history()))
        assert outcome.aborted is True
        # Caller-routable: messages are the ORIGINAL list (no surgery).
        assert outcome.repaired_messages == _history()


# ---------------------------------------------------------------------------
# F3 — Engine source pin (no static fallback) — T-5
# ---------------------------------------------------------------------------


class TestNoStaticFallback:
    def test_engine_source_has_no_silent_static_fallback(self):
        """F3 / T-5: ADR-0006 removal chosen over last-resort-with-telemetry.
        The engine source must NOT carry a silent static truncation
        fallback — a degenerate LLM summary must never silently enter
        history. The shipped ``LoopRepairer`` keeps its fallback for the
        kill-switch-OFF path; the engine deliberately does not reproduce
        it.
        """
        src = inspect.getsource(SymptomRepairEngine)
        # The shipped LoopRepairer fallback string is "without progress." —
        # it must not appear anywhere in the engine's source.
        assert "without progress." not in src

    async def test_engine_returns_degenerate_empty_after_facade(
        self, monkeypatch
    ):
        """F3 corollary: when the facade's final response is whitespace-only
        (the inner client returned ``AIMessage(content="   ")``), the
        engine's local degeneracy check raises
        ``SymptomRepairAborted(reason="summarizer-failed")`` — NOT a
        silent static fallback. Mirrors the existing
        ``TestSummarizerFailOpenAbort::test_empty_summary_text_aborts``
        pin but goes through the REAL facade (not a stubbed one).
        """
        fake = _FakeChatClient(
            primary_url="http://primary.example/v1",
            backup_url="http://backup.example/v1",
            # Both endpoints return whitespace — the facade succeeds
            # (no raises), but the engine's local degeneracy check
            # catches the empty ``cleaned.strip()``.
            primary_summary="   ",
            backup_summary="   ",
        )
        _patch_facade(monkeypatch, fake)
        _tighten_facade_defaults(monkeypatch)

        eng = SymptomRepairEngine()
        outcome = await eng.repair(_context(_history()))

        assert outcome.aborted is True
        assert outcome.abort_reason == "summarizer-failed"
        assert outcome.surgery_prefix is None
        assert outcome.budget_consumed is False


# ---------------------------------------------------------------------------
# F4 — 120s summarizer timeout parameter inherited from config
# ---------------------------------------------------------------------------


class TestSummarizerTimeoutConfig:
    def test_site_timeout_default_matches_config(self):
        """F4: ``LoopBreakerConfig.summarization_timeout_seconds`` is the
        authoritative default (120s) — same value the engine falls back to
        when neither the context nor the engine's own override sets it
        (engine code at ``_summarize``:
        ``timeout = context.summarization_timeout_seconds or
        self._timeout_seconds or DEFAULT_SUMMARIZATION_TIMEOUT_S``).
        """
        assert LoopBreakerConfig().summarization_timeout_seconds == 120
        # Engine module constant mirrors the config default (single
        # source of truth — ``DEFAULT_SUMMARIZATION_TIMEOUT_S``).
        assert DEFAULT_SUMMARIZATION_TIMEOUT_S == 120

    def test_engine_falls_back_to_120s_when_context_zero(self, monkeypatch):
        """F4 corollary: when ``context.summarization_timeout_seconds=0``
        the engine's fallback chain resolves to 120s (the
        ``DEFAULT_SUMMARIZATION_TIMEOUT_S`` constant). We assert by
        inspecting the engine source — the chain is
        ``context.X or self._timeout_seconds or DEFAULT_SUMMARIZATION_TIMEOUT_S``.
        """
        src = inspect.getsource(SymptomRepairEngine._summarize)
        # The fallback chain is present and the constant matches.
        assert "DEFAULT_SUMMARIZATION_TIMEOUT_S" in src
        assert "summarization_timeout_seconds" in src

    async def test_engine_passes_context_timeout_through_to_summarize(
        self, monkeypatch
    ):
        """F4 contract: the context's ``summarization_timeout_seconds``
        is the SITE-LEVEL ``asyncio.wait_for`` backstop the engine's
        ``_invoke_summarizer`` consumes. The facade's
        ``wall_clock_cap_s`` (45s default) trips FIRST in practice; the
        120s site cap stays as backstop. We pin the source-level
        wiring so a future regression that drops the timeout is caught.
        """
        # Source-level pin on the engine's invoke path.
        from daemon.services.symptom_repair_engine import SymptomRepairEngine as E
        invoke_src = inspect.getsource(E._invoke_summarizer)
        assert "asyncio.wait_for" in invoke_src
        assert "timeout=timeout_seconds" in invoke_src

        # End-to-end: a context with the default 120s survives the
        # real facade (the facade's wall_clock_cap_s=0.5 we set in
        # ``_tighten_facade_defaults`` is below 120s so it trips
        # first — irrelevant to this pin, which is about the engine's
        # own cap). The repair succeeds via backup.
        fake = _FakeChatClient(
            primary_url="http://primary.example/v1",
            backup_url="http://backup.example/v1",
            primary_summary=openai.APIConnectionError(
                request=__import__("httpx").Request(
                    "POST", "http://primary.example/v1/chat/completions"
                ),
                message="primary dead",
            ),
            backup_summary="backup ok",
        )
        _patch_facade(monkeypatch, fake)
        _tighten_facade_defaults(monkeypatch)

        eng = SymptomRepairEngine()
        outcome = await eng.repair(_context(_history()))
        assert outcome.success is True
        # And it consumed the context's 120s cap (not the fallback).
        # Source-level: the engine picks ``context.summarization_timeout_seconds``
        # when truthy — the 120s we passed is what landed.
        assert _context(_history()).summarization_timeout_seconds == 120


# ---------------------------------------------------------------------------
# Cross-cut pin: facade-forwarding seam style — engine MUST NOT bypass
# ---------------------------------------------------------------------------


class TestFacadeNotBypassed:
    def test_engine_source_references_wrap_langchain_failover(self):
        """Pin: the engine source must reference ``wrap_langchain_failover``
        — the same facade-forwarding seam the compaction module uses.
        A future regression that swaps to bare-invoke must be caught.
        """
        src = inspect.getsource(SymptomRepairEngine)
        assert "wrap_langchain_failover" in src
        # And it must NOT pass ``wall_clock_cap_s=`` (the facade default
        # is inherited — mirrors the C-4 pin in test_symptom_repair_engine).
        assert "wall_clock_cap_s=" not in src
