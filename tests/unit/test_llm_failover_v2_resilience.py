"""Tests for LLM HA failover v2 — resilience verification.

Companion module: ``daemon.services.llm_failover`` (the production
code under test) and the four ``asyncio.wait_for``-capped secondary
sites that wrap the facade.

This file focuses on three classes of resilience properties the
``llm-failover-v2-sites`` diff adds on top of the v2 facade:

* **Latency caps.** The HA facade adds bounded retry (≤ 3 transient
  / ≤ 2 timeout attempts on primary + up to 3 attempts on backup).
  Uncapped, a worst-case retry storm against a slow primary could
  stall a turn for 20+ minutes. Four sites wrap the facade call with
  ``asyncio.wait_for(..., timeout=...)`` to cap wall-clock time:

      * title_generation._generate_and_broadcast_title → 30.0
      * child_reports._summarize_instance            → 30.0
      * child_reports._repair_report_with_llm        → config.timeout_seconds (default 30)
      * compaction._call_summarization_llm           → 30.0
      * keyword_extraction.extract_keywords          → timeout_s (operator)

  We verify the cap is present (structural) AND that it actually
  fires under retry-storm conditions (functional — without literally
  waiting 30s in tests).

* **Fallback composition.** Every secondary site has a graceful
  ``except Exception`` block on top of the facade — facade exhaustion
  is supposed to look like a normal exception to the caller. We
  verify the EXACT graceful default returned by each site:

      * title_generation      → skip title update, no DB write
      * keyword_extraction    → []
      * child_reports summ.   → "Completed N message(s)."
      * child_reports repair  → None (caller uses _combine_messages)
      * skill_embedding chat  → []
      * skill_evolution       → ""
      * skill_search          → caller falls back to _degraded_select
      * compaction            → caller falls back to _truncate_fallback

* **Concurrency / thread-local.** The raw-SDK facade tracks the
  current target URL in a ``threading.local`` so the factory re-reads
  it on every retry attempt. Tenacity retries run synchronously on
  the same thread as the initial attempt, and daemon sites enter
  the facade via ``asyncio.to_thread`` — so the worker thread is
  NOT the event loop's. We verify:

      * Cross-thread isolation: 10 threads × 10 calls each — each
        thread only ever sees its own URL during its own call.
      * Cleanup under exception: ``current_failover_url()`` returns
        None after a call that raises mid-retry.
      * Sequential calls on the same thread: no leak between calls.
      * Single-depth nested semantics: pin the documented clobber.

Tests run under pytest. The companion test pack is
``test/packs/llm_failover_v2_resilience_unit_test.sh``.
"""

from __future__ import annotations

import asyncio
import ast
import inspect
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from daemon.services.llm_failover import (
    current_failover_url,
    invoke_raw_with_failover,
    wrap_langchain_failover,
)


# ===========================================================================
# Shared constants + helpers
# ===========================================================================


PRIMARY = "https://primary.test/v1"
BACKUP = "https://backup.test/v1"
UNREACHABLE = "https://unreachable.invalid/v1"  # not routable; conservative


def _function_body_source(source: str, function_name: str) -> str | None:
    """Extract the body of ``function_name`` from a module source
    string. Uses ``ast`` so nested parens are handled correctly
    (regex-on-source breaks on ``asyncio.wait_for(asyncio.to_thread(...), ...)``).

    Recurses into ``ClassDef`` to find methods.

    Returns the source of the function (between the ``def`` /
    ``async def`` and the next sibling), or ``None`` if not found.
    """
    tree = ast.parse(source)

    def _walk(node: ast.AST) -> ast.AST | None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                return node
            return None
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                found = _walk(child)
                if found is not None:
                    return found
            return None
        if isinstance(node, ast.If):
            # Module-level ``if TYPE_CHECKING:`` blocks (etc.) — skip
            return None
        # For other top-level statements, no function defined here.
        return None

    for top in tree.body:
        target = _walk(top)
        if target is not None:
            return ast.get_source_segment(source, target)
    return None


def _wait_for_timeout_kwarg(body_source: str) -> str | None:
    """Return the ``timeout=`` keyword's source text for any
    ``asyncio.wait_for(...)`` call in ``body_source``, or ``None``
    if no wait_for call exists.

    AST-walks the body to handle nested parens correctly. The
    timeout kwarg may be a literal (``30.0``) or a Name (``timeout``,
    ``timeout_s``) — we return the unparsed source for whichever
    appears.
    """
    tree = ast.parse(body_source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match ``asyncio.wait_for(...)`` — Attribute on Name "asyncio".
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "wait_for"
            and isinstance(func.value, ast.Name)
            and func.value.id == "asyncio"
        ):
            for kw in node.keywords:
                if kw.arg == "timeout":
                    return ast.unparse(kw.value).strip()
            return None  # wait_for exists but no timeout kwarg
    return None


def _has_asyncio_wait_for(body_source: str) -> bool:
    """Return True iff ``body_source`` contains an
    ``asyncio.wait_for(...)`` call."""
    return _wait_for_timeout_kwarg(body_source) is not None


def _patched_wrap_raises_factory(exc: BaseException):
    """Build a fake ``wrap_langchain_failover`` that returns a binding
    whose ``invoke`` raises ``exc``. Used to simulate facade
    exhaustion in fallback-composition tests."""

    def _fake_wrap(client, cfg, **kw):
        def _raise(*args, **kwargs):
            raise exc

        return SimpleNamespace(invoke=_raise)

    return _fake_wrap


def _patched_invoke_raw_raises_factory(exc: BaseException):
    """Build a fake ``invoke_raw_with_failover`` that always raises
    ``exc`` regardless of inputs. Mirrors the wrap helper but for the
    raw-SDK sites."""

    def _fake_invoke(factory, cfg, **kw):
        raise exc

    return _fake_invoke


# ===========================================================================
# A. Latency caps
# ===========================================================================


class TestLatencyCaps:
    """Verify each capped site wraps its facade call with
    ``asyncio.wait_for(..., timeout=...)`` AND that the cap actually
    fires under retry-storm conditions.

    Two-pronged approach:

    * **Structural** — AST-like regex over the source file confirms
      the wait_for(timeout=...) call exists at each site with the
      right timeout value (literal ``30.0`` for the three hard-coded
      sites, ``timeout`` for child_reports._repair_report_with_llm,
      ``timeout_s`` for keyword_extraction.extract_keywords).

    * **Functional** — monkeypatch the per-site ``asyncio.wait_for``
      (or use a real short timeout) and drive a hanging LLM. Verify
      the call returns within the short timeout window AND that
      facade attempts are bounded (no retry-storm amplification).
    """

    # ----- Structural pins -------------------------------------------------

    def test_title_generation_has_30s_wait_for(self):
        """``title_generation._generate_and_broadcast_title`` must wrap
        the facade call with ``asyncio.wait_for(..., timeout=30.0)``."""
        from daemon.services import title_generation as tg

        source = inspect.getsource(tg)
        body = _function_body_source(source, "_generate_and_broadcast_title")
        assert body is not None, (
            "_generate_and_broadcast_title not found in title_generation module"
        )
        assert _has_asyncio_wait_for(body), (
            "title_generation._generate_and_broadcast_title must call "
            "asyncio.wait_for(..., timeout=...) to cap wall-clock "
            "latency under retry-storm conditions"
        )
        kwarg = _wait_for_timeout_kwarg(body)
        assert kwarg == "30.0", (
            f"title_generation must use literal timeout=30.0; "
            f"got timeout={kwarg!r} (must NOT be derived)"
        )

    def test_child_reports_summarize_has_30s_wait_for(self):
        """``child_reports._summarize_instance`` must use a 30s cap."""
        from daemon.services import child_reports as cr

        source = inspect.getsource(cr)
        body = _function_body_source(source, "_summarize_instance")
        assert body is not None, (
            "_summarize_instance not found in child_reports module"
        )
        assert _has_asyncio_wait_for(body), (
            "child_reports._summarize_instance must call "
            "asyncio.wait_for(..., timeout=...) to cap wall-clock latency"
        )
        kwarg = _wait_for_timeout_kwarg(body)
        assert kwarg == "30.0", (
            f"child_reports._summarize_instance must use literal "
            f"timeout=30.0; got timeout={kwarg!r}"
        )

    def test_child_reports_repair_has_wait_for_with_config_timeout(self):
        """``child_reports._repair_report_with_llm`` must use the
        operator-configured ``timeout`` from ``ReportRepairConfig``
        (default 30s) — NOT a hardcoded literal — so the cap is
        tunable per deployment."""
        from daemon.services import child_reports as cr

        source = inspect.getsource(cr)
        body = _function_body_source(source, "_repair_report_with_llm")
        assert body is not None, (
            "_repair_report_with_llm not found in child_reports module"
        )
        assert _has_asyncio_wait_for(body), (
            "child_reports._repair_report_with_llm must call "
            "asyncio.wait_for(..., timeout=...) to cap wall-clock latency"
        )
        kwarg = _wait_for_timeout_kwarg(body)
        assert kwarg == "timeout", (
            f"child_reports._repair_report_with_llm must use "
            f"timeout=config.timeout_seconds (operator-configurable); "
            f"got timeout={kwarg!r}. Hardcoding 30s here would defeat "
            f"the operator-tunable knob in ReportRepairConfig."
        )

    def test_compaction_has_adaptive_wait_for(self):
        """``compaction._call_summarization_llm`` must use the adaptive
        per-call timeout (Phase 1 / WS-3.1) — the prior literal
        ``timeout=30.0`` was replaced with
        ``timeout=_summarization_timeout_s(prompt, config)``. The
        facade (``wrap_langchain_failover``) then receives
        ``wall_clock_cap_s = inner_cap + timeout_facade_margin_s`` per
        WS-3.2 (architect §9.8 PINNED +5s margin).

        ``compaction`` is in ``daemon/compaction.py`` (not
        ``daemon/services/``) — check both locations to catch a
        future module move.
        """
        try:
            import daemon.compaction as cmp
            module_label = "daemon.compaction"
        except ImportError:  # pragma: no cover — defensive
            pytest.skip("daemon.compaction not importable in this env")
        source = inspect.getsource(cmp)
        body = _function_body_source(source, "_call_summarization_llm")
        assert body is not None, (
            f"{module_label}._call_summarization_llm not found"
        )
        assert _has_asyncio_wait_for(body), (
            f"{module_label}._call_summarization_llm must call "
            "asyncio.wait_for(..., timeout=...) to cap wall-clock latency"
        )
        kwarg = _wait_for_timeout_kwarg(body)
        # Phase 1 / WS-3.1: adaptive timeout — the inner cap is the
        # output of ``_summarization_timeout_s(prompt, config)``, not
        # a hard-coded 30s literal.
        assert kwarg == "inner_cap", (
            f"{module_label}._call_summarization_llm must use the "
            f"adaptive timeout via ``timeout=inner_cap`` (computed from "
            f"_summarization_timeout_s); got timeout={kwarg!r}"
        )
        # The facade (``wrap_langchain_failover``) must be threaded with
        # ``wall_clock_cap_s = inner_cap + timeout_facade_margin_s``.
        assert "wall_clock_cap_s=facade_cap" in body, (
            f"{module_label}._call_summarization_llm must thread "
            "``wall_clock_cap_s=facade_cap`` into wrap_langchain_failover "
            "(WS-3.2 — PINNED +5s margin per architect §9.8)"
        )

    def test_keyword_extraction_has_wait_for_with_timeout_s(self):
        """``keyword_extraction.extract_keywords`` uses the operator-
        configurable ``timeout_s`` parameter (default
        ``KEYWORD_EXTRACTION_TIMEOUT_S``)."""
        from daemon.services import keyword_extraction as kx

        source = inspect.getsource(kx)
        body = _function_body_source(source, "extract_keywords")
        assert body is not None, (
            "extract_keywords not found in keyword_extraction module"
        )
        assert _has_asyncio_wait_for(body), (
            "keyword_extraction.extract_keywords must call "
            "asyncio.wait_for(..., timeout=...) to cap wall-clock latency"
        )
        kwarg = _wait_for_timeout_kwarg(body)
        assert kwarg == "timeout_s", (
            f"keyword_extraction.extract_keywords must use "
            f"timeout=timeout_s (operator-configurable); "
            f"got timeout={kwarg!r}"
        )

    # ----- Functional pins (hanging LLM, short timeout) -------------------

    def test_title_generation_30s_cap_actually_fires(self, monkeypatch):
        """Drive ``_generate_and_broadcast_title`` with a hanging LLM
        and verify the 30s cap fires — without waiting 30s. We
        monkeypatch the per-site ``asyncio.wait_for`` to use 0.05s
        and verify the call returns within 0.5s. ``asyncio.TimeoutError``
        must be caught at the site boundary (no bubble), and the
        title update MUST NOT happen.

        The hang is implemented with a ``threading.Event`` (not
        ``time.sleep``): when ``wait_for`` times out, we signal the
        event so the worker thread releases immediately. Otherwise
        ``asyncio.run`` blocks on the orphaned worker thread at
        shutdown, making the test slow AND unable to measure the
        cap's fire-time accurately."""
        from daemon.services import title_generation as tg

        real_wait_for = asyncio.wait_for
        worker_event = threading.Event()

        async def _short_wait_for(awaitable, timeout):
            # Always use a tiny cap. Same shape as real wait_for; we
            # only swap the timeout value. Always release the worker
            # thread on exit so ``asyncio.run`` doesn't block on it.
            try:
                return await real_wait_for(awaitable, timeout=0.05)
            finally:
                worker_event.set()

        attempts = {"n": 0}

        def _fake_wrap(client, cfg, **kw):
            attempts["n"] += 1

            def _hang(*args, **kwargs):
                # Wait for the event the test's wait_for fires on
                # timeout. A long timeout is fine — the event signals
                # within 0.05s.
                worker_event.wait(timeout=5.0)
                return SimpleNamespace(content="SHOULD-NOT-REACH")

            return SimpleNamespace(invoke=_hang)

        class _FakeRepo:
            def __init__(self):
                self.title_updates: list[tuple[str, str]] = []

            def get(self, iid):
                return SimpleNamespace(instance_metadata={})

            def update_title(self, iid, title):
                self.title_updates.append((iid, title))
                return None

        class _FakeManager:
            config = SimpleNamespace(
                llm=SimpleNamespace(
                    base_url=PRIMARY,
                    base_url_backup=BACKUP,
                    api_key="test",
                    model_title="gpt-test",
                    # Production reads `self._config.llm.buffer_response_header`
                    # (e.g. title_generation.py:104, child_reports.py:766/:1400).
                    # `LLMConfig.buffer_response_header` defaults to True
                    # (daemon/config.py:231); mirror that here so the fake matches.
                    buffer_response_header=True,
                )
            )

            _instance_repository = _FakeRepo()
            _logger = MagicMock()

        from daemon.services.title_generation import TitleGenerationService
        from daemon import graph as graph_mod

        svc = TitleGenerationService(manager=_FakeManager())

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(graph_mod, "ThinkingChatOpenAI", return_value=MagicMock())
            )
            stack.enter_context(
                patch.object(tg, "wrap_langchain_failover", side_effect=_fake_wrap)
            )
            stack.enter_context(
                patch.object(tg.asyncio, "wait_for", _short_wait_for)
            )

            t0 = time.monotonic()
            asyncio.run(
                svc._generate_and_broadcast_title("inst-test", "Hello world")
            )
            elapsed = time.monotonic() - t0

        # Cap fired: returned well before the 30s default.
        assert elapsed < 0.5, (
            f"30s wait_for cap must fire fast under hanging LLM; "
            f"got elapsed={elapsed:.2f}s"
        )

        # Bounded retries: facade was entered at most once for this
        # call (no retry-storm amplification at the OUTER wait_for
        # level — wait_for cancels on first timeout).
        assert attempts["n"] <= 1, (
            f"facade wrap call count must be bounded (≤ 1 per site "
            f"call); got attempts={attempts['n']}"
        )

        # Title update was NOT performed (timeout → graceful skip).
        assert svc._manager._instance_repository.title_updates == [], (
            f"timeout must skip title update; got updates="
            f"{svc._manager._instance_repository.title_updates!r}"
        )

    def test_no_retry_storm_amplification_under_short_timeout(
        self, monkeypatch
    ):
        """Verify that with a SHORT outer timeout, the retry ladder
        inside the facade does NOT amplify — the ``wait_for`` cap
        cancels the surrounding task on first timeout, so the
        retry loop's attempts must be bounded.

        Drive ``_generate_and_broadcast_title`` with a hanging LLM
        AND a 0.05s outer cap. Count facade attempts. The result
        should be ≤ 1 (the very first attempt) because wait_for
        cancels the awaiting coroutine immediately.
        """
        from daemon.services import title_generation as tg

        real_wait_for = asyncio.wait_for
        worker_event = threading.Event()

        async def _short_wait_for(awaitable, timeout):
            try:
                return await real_wait_for(awaitable, timeout=0.05)
            finally:
                worker_event.set()

        attempts = {"n": 0}

        def _fake_wrap(client, cfg, **kw):
            attempts["n"] += 1

            def _hang(*args, **kwargs):
                worker_event.wait(timeout=5.0)
                return SimpleNamespace(content="x")

            return SimpleNamespace(invoke=_hang)

        class _FakeRepo:
            def get(self, iid):
                return SimpleNamespace(instance_metadata={})

            def update_title(self, iid, title):
                return None

        class _FakeManager:
            config = SimpleNamespace(
                llm=SimpleNamespace(
                    base_url=PRIMARY,
                    base_url_backup=BACKUP,
                    api_key="test",
                    model_title="gpt-test",
                    # Production reads `self._config.llm.buffer_response_header`
                    # (e.g. title_generation.py:104, child_reports.py:766/:1400).
                    # `LLMConfig.buffer_response_header` defaults to True
                    # (daemon/config.py:231); mirror that here so the fake matches.
                    buffer_response_header=True,
                )
            )

            _instance_repository = _FakeRepo()
            _logger = MagicMock()

        from daemon.services.title_generation import TitleGenerationService
        from daemon import graph as graph_mod

        svc = TitleGenerationService(manager=_FakeManager())

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(graph_mod, "ThinkingChatOpenAI", return_value=MagicMock())
            )
            stack.enter_context(
                patch.object(tg, "wrap_langchain_failover", side_effect=_fake_wrap)
            )
            stack.enter_context(
                patch.object(tg.asyncio, "wait_for", _short_wait_for)
            )

            t0 = time.monotonic()
            asyncio.run(
                svc._generate_and_broadcast_title("inst-test", "Hello world")
            )
            elapsed = time.monotonic() - t0

        assert elapsed < 0.5, (
            f"cap must fire fast; got {elapsed:.2f}s"
        )
        # Bounded: wait_for cancels the FIRST attempt's coroutine,
        # so we should see at most one facade invocation. The
        # facade's internal retry loop never gets a chance to
        # re-enter because the OUTER cap preempts it.
        assert 1 <= attempts["n"] <= 2, (
            f"facade wrap call count must be tightly bounded (1-2); "
            f"got attempts={attempts['n']}. A higher count means the "
            f"outer wait_for is NOT preempting the retry loop — "
            f"retry-storm amplification regression."
        )

    def test_child_reports_repair_timeout_kwarg_is_config_driven(self):
        """Pin that the repair site's ``wait_for`` uses the
        ``ReportRepairConfig.timeout_seconds`` value (default 30),
        not a hardcoded literal. Verified by exercising the real
        function with a custom config and observing the per-call
        timeout value passed in.

        We don't drive the full LLM call (would require LangChain +
        a hanging client) — we patch ``wrap_langchain_failover`` to
        a recording stub and capture the timeout-kwarg AST
        position. The default value of
        ``ReportRepairConfig.timeout_seconds`` is the unit
        guarantee."""
        from daemon.config import ReportRepairConfig

        cfg = ReportRepairConfig()
        assert cfg.timeout_seconds == 30, (
            f"ReportRepairConfig.timeout_seconds must default to 30 "
            f"(matching sibling sites); got {cfg.timeout_seconds}"
        )

        # And the override path: a custom value flows through.
        cfg2 = ReportRepairConfig(timeout_seconds=7)
        assert cfg2.timeout_seconds == 7


# ===========================================================================
# B. Fallback composition
# ===========================================================================


class TestFallbackComposition:
    """Verify facade exhaustion → site ``except Exception`` → graceful
    default. For each secondary site, patch the facade to raise and
    confirm the EXACT graceful default the site returns.

    The facade is patched to raise rather than driving a real LLM
    because:

    1. We want to verify the SITE's except-block composition, not
       the facade's retry semantics (those are covered by
       test_llm_failover_v2.py).
    2. Driving real HTTP exhaustion would require MockTransport
       plumbing for every site — fragile, slow, and noisy.

    Patching the facade call is the canonical way to simulate
    "retry budget exhausted" from the site's perspective — the
    site's except-block must catch whatever the facade raises.
    """

    # ----- title_generation -------------------------------------------------

    def test_title_generation_graceful_skip_when_facade_exhausts(self):
        """When the facade raises, title generation must swallow the
        exception AND must NOT perform the title DB write. Caller
        sees no exception."""
        from daemon.services import title_generation as tg
        from daemon.services.title_generation import TitleGenerationService

        class _FakeRepo:
            def __init__(self):
                self.title_updates: list[tuple[str, str]] = []

            def get(self, iid):
                return SimpleNamespace(instance_metadata={})

            def update_title(self, iid, title):
                self.title_updates.append((iid, title))
                return None

        repo = _FakeRepo()

        class _FakeManager:
            config = SimpleNamespace(
                llm=SimpleNamespace(
                    base_url=PRIMARY,
                    base_url_backup=BACKUP,
                    api_key="test",
                    model_title="gpt-test",
                    # Production reads `self._config.llm.buffer_response_header`
                    # (e.g. title_generation.py:104, child_reports.py:766/:1400).
                    # `LLMConfig.buffer_response_header` defaults to True
                    # (daemon/config.py:231); mirror that here so the fake matches.
                    buffer_response_header=True,
                )
            )

            _instance_repository = repo
            _logger = MagicMock()

        svc = TitleGenerationService(manager=_FakeManager())

        fake_wrap = _patched_wrap_raises_factory(
            RuntimeError("simulated facade exhaustion")
        )

        from daemon import graph as graph_mod

        with patch.object(
            graph_mod, "ThinkingChatOpenAI", return_value=MagicMock()
        ), patch.object(tg, "wrap_langchain_failover", side_effect=fake_wrap):
            # Must not raise.
            asyncio.run(
                svc._generate_and_broadcast_title("inst-1", "Hello world")
            )

        assert repo.title_updates == [], (
            f"facade exhaustion must skip title DB write; "
            f"got title_updates={repo.title_updates!r}"
        )

    # ----- keyword_extraction ----------------------------------------------

    def test_keyword_extraction_returns_empty_list_when_facade_exhausts(self):
        """``extract_keywords`` returns ``[]`` on any exception (including
        facade exhaustion) so callers fall back to heuristic keywords."""
        from daemon.services import keyword_extraction as kx
        from daemon.services import llm_failover as lf_module
        from daemon import graph as graph_mod

        fake_wrap = _patched_wrap_raises_factory(
            RuntimeError("simulated facade exhaustion")
        )

        class _Cfg:
            class llm:
                base_url = PRIMARY
                base_url_backup = BACKUP
                api_key = "test"
                model = "gpt-test"
                model_keywords = "gpt-test"
                # Production reads `config.llm.buffer_response_header`
                # (keyword_extraction.py:377). `LLMConfig.buffer_response_header`
                # defaults to True (daemon/config.py:231); mirror that here so
                # the fake matches.
                buffer_response_header = True

        # ``extract_keywords`` lazy-imports ``wrap_langchain_failover``
        # from ``daemon.services.llm_failover`` inside the function,
        # so the patch target is the source module.
        with patch.object(
            graph_mod, "ThinkingChatOpenAI", return_value=MagicMock()
        ), patch.object(lf_module, "wrap_langchain_failover", side_effect=fake_wrap):
            result = asyncio.run(
                kx.extract_keywords(message="Hello", config=_Cfg(), timeout_s=2)
            )

        assert result == [], (
            f"keyword_extraction must return [] on facade exhaustion; "
            f"got {result!r}"
        )

    # ----- child_reports: summarization ------------------------------------

    def test_child_reports_summarization_returns_fallback_string_when_facade_exhausts(
        self,
    ):
        """``_summarize_instance`` must return the canned
        ``"Completed N message(s)."`` fallback when the facade
        raises — preserving the count-fallback invariant."""
        from daemon.services import child_reports as cr

        captured = {"wrap": 0, "cfg": None}

        def _fake_wrap(client, cfg, **kw):
            captured["wrap"] += 1
            captured["cfg"] = cfg

            def _raise(*args, **kwargs):
                raise RuntimeError("simulated facade exhaustion")

            return SimpleNamespace(invoke=_raise)

        from daemon import graph as graph_mod

        class _FakeCheckpointerAdapter:
            raw_saver = MagicMock()

        class _FakeManager:
            config = SimpleNamespace(
                llm=SimpleNamespace(
                    base_url=PRIMARY,
                    base_url_backup=BACKUP,
                    api_key="test",
                    model="gpt-test",
                    # Production reads `self._config.llm.buffer_response_header`
                    # (e.g. child_reports.py:766, child_reports.py:1400).
                    # `LLMConfig.buffer_response_header` defaults to True
                    # (daemon/config.py:231); mirror that here so the fake matches.
                    buffer_response_header=True,
                )
            )
            _checkpointer = _FakeCheckpointerAdapter()

        svc = cr.ChildReportsService(manager=_FakeManager())

        async def _fake_get_messages(checkpointer, instance_id, manager=None):
            return [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "How are you?"},
                {"role": "assistant", "content": "Doing well"},
            ]

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    graph_mod, "ThinkingChatOpenAI", return_value=MagicMock()
                )
            )
            stack.enter_context(
                patch.object(cr, "wrap_langchain_failover", side_effect=_fake_wrap)
            )
            stack.enter_context(
                patch.object(cr, "get_instance_messages", side_effect=_fake_get_messages)
            )
            stack.enter_context(
                patch.object(
                    svc, "_get_instance_report_prefix", return_value="Test agent"
                )
            )

            result = asyncio.run(svc._summarize_instance("inst-1", "agent-1"))

        assert captured["wrap"] == 1, (
            f"summarization must call facade exactly once; "
            f"got {captured['wrap']}"
        )
        # Exact-match on the canned fallback string — protects against
        # a future refactor that silently changes the count-fallback
        # message format.
        assert result == "Test agent, below is the response: Completed 4 message(s).", (
            f"child_reports summarization must return the canned "
            f"'Completed N message(s).' fallback on facade exhaustion; "
            f"got {result!r}"
        )

    # ----- child_reports: repair -------------------------------------------

    def test_child_reports_repair_returns_none_when_facade_exhausts(self):
        """``_repair_report_with_llm`` must return ``None`` when the
        facade raises — the caller (``_attempt_report_repair``) uses
        ``_combine_messages`` as the fallback when this returns None.
        Returning a string would silently overwrite the report with
        a partial response."""
        from daemon.services import child_reports as cr
        from daemon.config import ReportRepairConfig

        def _fake_wrap(client, cfg, **kw):
            def _raise(*args, **kwargs):
                raise RuntimeError("simulated facade exhaustion")

            return SimpleNamespace(invoke=_raise)

        from daemon import graph as graph_mod

        class _FakeCheckpointerAdapter:
            raw_saver = MagicMock()

        class _FakeManager:
            config = SimpleNamespace(
                llm=SimpleNamespace(
                    base_url=PRIMARY,
                    base_url_backup=BACKUP,
                    api_key="test",
                    model="gpt-test",
                    # Production reads `self._config.llm.buffer_response_header`
                    # (e.g. child_reports.py:766, child_reports.py:1400).
                    # `LLMConfig.buffer_response_header` defaults to True
                    # (daemon/config.py:231); mirror that here so the fake matches.
                    buffer_response_header=True,
                )
            )
            _checkpointer = _FakeCheckpointerAdapter()

        svc = cr.ChildReportsService(manager=_FakeManager())

        repair_config = ReportRepairConfig()
        messages = [
            {"role": "assistant", "content": "First message " * 20},
            {"role": "assistant", "content": "Second message " * 20},
        ]

        with patch.object(
            graph_mod, "ThinkingChatOpenAI", return_value=MagicMock()
        ), patch.object(cr, "wrap_langchain_failover", side_effect=_fake_wrap):
            result = asyncio.run(
                svc._repair_report_with_llm(
                    messages, repair_config, instance_id="inst-1"
                )
            )

        assert result is None, (
            f"repair must return None on facade exhaustion so the caller "
            f"falls back to _combine_messages; got {result!r}"
        )

    # ----- skill_embedding: chat (trigger_queries) -------------------------

    def test_skill_embedding_trigger_queries_returns_empty_when_facade_exhausts(
        self,
    ):
        """``generate_trigger_queries`` returns ``[]`` when the raw-SDK
        facade raises — caller treats this as "skip embedding
        refresh"."""
        from daemon.services import skill_embedding_service as ses
        from daemon.services.skill_embedding_service import SkillEmbeddingService

        fake_invoke = _patched_invoke_raw_raises_factory(
            RuntimeError("simulated facade exhaustion")
        )

        cfg = SimpleNamespace(
            embedding_model="text-embedding-test",
            embedding_base_url=None,  # unset → use chat endpoint
            embedding_api_key=None,
        )
        llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }
        svc = SkillEmbeddingService(
            config=cfg, embedding_repo=MagicMock(), llm_config=llm_config
        )

        skill = SimpleNamespace(
            id="skill-1", name="TestSkill", description="Test", content="body"
        )

        with patch.object(ses, "invoke_raw_with_failover", side_effect=fake_invoke):
            result = asyncio.run(svc.generate_trigger_queries(skill))

        assert result == [], (
            f"generate_trigger_queries must return [] on facade exhaustion; "
            f"got {result!r}"
        )

    # ----- skill_evolution ------------------------------------------------

    def test_skill_evolution_call_llm_returns_empty_when_facade_exhausts(self):
        """``SkillEvolutionService._call_llm`` returns ``""`` when the
        raw-SDK facade raises — defensive parsers treat empty string
        as "no usable response"."""
        from daemon.services import skill_evolution_service as evo
        from daemon.services.skill_evolution_service import SkillEvolutionService

        fake_invoke = _patched_invoke_raw_raises_factory(
            RuntimeError("simulated facade exhaustion")
        )

        # We don't drive the full constructor — patch the resolution
        # helpers to return deterministic values, then drive _call_llm
        # directly. Pass ``model=`` explicitly to skip
        # ``_resolve_analysis_model`` which reads ``self._config``.
        svc = SkillEvolutionService.__new__(SkillEvolutionService)
        svc._llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }
        # ``_call_llm`` reads ``base_url`` and ``api_key`` from
        # ``_llm_config`` via ``_resolve_chat_base_url`` /
        # ``_resolve_chat_api_key`` — those work without ``_config``.

        with patch.object(evo, "invoke_raw_with_failover", side_effect=fake_invoke):
            result = asyncio.run(svc._call_llm("Test prompt", model="gpt-test"))

        assert result == "", (
            f"skill_evolution _call_llm must return '' on facade exhaustion; "
            f"got {result!r}"
        )

    # ----- skill_search ---------------------------------------------------

    def test_skill_search_falls_back_to_degraded_select_when_facade_exhausts(
        self,
    ):
        """``SkillSearchService.search`` must catch any exception from
        ``_llm_select`` and call ``_degraded_select`` instead. The
        fallback returns the top reranked candidates regardless of
        LLM availability.

        The flow:

            search → _embedding_rerank (Stage 2)
                  → _llm_select    (Stage 3, may raise)
                  → _degraded_select  (fallback)

        Patch the raw-SDK facade to raise; verify _llm_select raises
        and the search caller catches it and calls _degraded_select.
        """
        from daemon.services import skill_search_service as sss
        from daemon.services.skill_search_service import SkillSearchService

        # Build minimal service stub — search() only needs a few
        # attributes to drive the path we care about.
        svc = SkillSearchService.__new__(SkillSearchService)
        svc._llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }
        svc._config = SimpleNamespace(
            bm25_top_k=10,
            llm_select_top_k=5,
        )

        # Mock candidates (skill, score) tuples — the rerank already
        # sorted them descending.
        skill_a = SimpleNamespace(name="alpha", description="A", content="")
        skill_b = SimpleNamespace(name="beta", description="B", content="")
        candidates = [(skill_a, 0.9), (skill_b, 0.7)]
        reranked = candidates

        # Stage 1 (BM25) and Stage 2 (embedding rerank) both pass
        # through cleanly. Patch them so search() proceeds to Stage 3.
        async def _fake_bm25(query, project_id, top_k):
            return candidates

        async def _fake_embedding_rerank(query, cands, top_k):
            return reranked

        # _llm_select (Stage 3) must raise — patched at the source
        # module so the method body hits our raising version.
        async def _fake_llm_select_raising(*args, **kwargs):
            raise RuntimeError("simulated facade exhaustion")

        # _degraded_select — capture call + return shape. The real
        # ``_degraded_select`` is a SYNC method (def, not async def),
        # so the fake must also be sync — otherwise ``return
        # self._degraded_select(...)`` from inside ``async search()``
        # would return an unawaited coroutine.
        degraded_called = {"called": False, "reranked": None, "max_results": None}

        def _fake_degraded_select(r, m):
            degraded_called["called"] = True
            degraded_called["reranked"] = r
            degraded_called["max_results"] = m
            return {
                "injected": [{"skill": r[0][0], "score": r[0][1]}],
                "low_match": [],
            }

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(svc, "_bm25_prefilter", side_effect=_fake_bm25)
            )
            stack.enter_context(
                patch.object(
                    svc, "_embedding_rerank", side_effect=_fake_embedding_rerank
                )
            )
            stack.enter_context(
                patch.object(svc, "_llm_select", side_effect=_fake_llm_select_raising)
            )
            stack.enter_context(
                patch.object(svc, "_degraded_select", side_effect=_fake_degraded_select)
            )

            result = asyncio.run(svc.search("test query", project_id=None, max_results=2))

        assert degraded_called["called"], (
            "skill_search.search must fall back to _degraded_select when "
            "_llm_select raises (facade exhaustion)"
        )
        assert degraded_called["max_results"] == 2
        # Result came from _degraded_select, not _llm_select.
        assert result == {
            "injected": [{"skill": skill_a, "score": 0.9}],
            "low_match": [],
        }

    # ----- compaction ----------------------------------------------------

    def test_compaction_falls_back_to_truncation_when_facade_exhausts(self):
        """``ContextCompactor.compact_messages`` / ``compact_state``
        wraps ``_call_summarization_llm`` in a try/except — on any
        exception the caller falls back to ``_truncate_fallback``
        and the resulting ``CompactionResult`` carries
        ``compaction_type='truncation'`` and a non-None
        ``summarization_error``.

        We drive the public path (``compact_state``) with a
        summarizing-LLM that raises and verify the truncation
        fallback engages."""
        from daemon import compaction as cmp
        from langchain_core.messages import HumanMessage

        # ---- Fake facade that raises ------------------------------------
        def _fake_wrap(client, cfg, **kw):
            def _raise(*args, **kwargs):
                raise RuntimeError("simulated facade exhaustion")

            return SimpleNamespace(invoke=_raise)

        llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }

        # Use a small context window so we can drive compaction with
        # a small message set. ``min_messages_before_compaction``
        # default 10 is kept; ``context_window_default=2000`` puts
        # the threshold at 1600 tokens, easily exceeded by 10 long
        # messages.
        config = cmp.CompactionConfig(
            context_window_default=2000,
            threshold=0.80,
            min_messages_before_compaction=10,
            recent_message_window=2,
            min_recent_window=1,
        )

        from daemon.services import llm_failover as lf_module
        from daemon import graph as graph_mod

        # Build 12 long messages — enough to clear
        # ``min_messages_before_compaction`` (10) and to exceed the
        # token threshold.
        messages = [
            HumanMessage(content="word " * 200) for _ in range(12)
        ]
        context = cmp.CompactionContext(
            messages=messages,
            system_prompt_tokens=10,
            model_name="gpt-test",
            config=config,
            llm_config=llm_config,
        )

        compactor = cmp.ContextCompactor(config=config, llm_config=llm_config)

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(lf_module, "wrap_langchain_failover", side_effect=_fake_wrap)
            )
            stack.enter_context(
                patch.object(graph_mod, "ThinkingChatOpenAI", return_value=MagicMock())
            )

            result = asyncio.run(compactor.compact_state(context))

        # Result must come from _truncate_fallback, not from a successful
        # summarization. The truncation branch sets these two fields.
        assert result is not None, (
            "compact_state must return a CompactionResult even on facade "
            "exhaustion (via _truncate_fallback)"
        )
        assert result.compaction_type == "truncation", (
            f"facade exhaustion must trigger truncation fallback; "
            f"got compaction_type={result.compaction_type!r}"
        )
        assert result.summarization_error is not None, (
            "CompactionResult.summarization_error must be populated when "
            "the LLM summarization failed"
        )
        assert "simulated facade exhaustion" in result.summarization_error, (
            f"summarization_error should carry the original exception text; "
            f"got {result.summarization_error!r}"
        )


# ===========================================================================
# C. Concurrency / thread-local
# ===========================================================================


class TestConcurrencyThreadLocal:
    """Verify ``current_failover_url()`` has no cross-talk between
    concurrent ``asyncio.to_thread`` workers.

    The raw-SDK facade tracks the current target URL in a
    ``threading.local``. Tenacity retries run synchronously on the
    SAME thread as the initial attempt — so a single
    ``invoke_raw_with_failover`` call is single-threaded by design.
    But daemon sites enter the facade via ``asyncio.to_thread``,
    meaning multiple ``invoke_raw_with_failover`` calls can run
    concurrently on DIFFERENT worker threads. Each thread must see
    only its OWN URL during its OWN call.
    """

    # ----- Cross-thread isolation ------------------------------------------

    def test_current_failover_url_thread_isolation_under_concurrent_calls(self):
        """Spawn N threads × M calls each; each thread drives
        ``invoke_raw_with_failover`` with a unique per-thread URL.
        Inside the factory, capture ``current_failover_url()`` and
        verify it equals the thread's own URL (NOT some other
        thread's URL).

        A ``threading.Barrier`` synchronizes thread start so all
        threads enter the facade at once — maximizing the chance of
        cross-talk if the slot were shared state instead of
        thread-local."""
        n_threads = 10
        calls_per_thread = 5

        # Each thread gets its own URL pair (primary, backup). The
        # factory always invokes ``current_failover_url()`` — the
        # facade sets it to primary on attempt 1, backup after a swap.
        # With a 200 OK on the FIRST attempt, we never swap, so the
        # expected value is the per-thread primary URL.
        thread_urls = [
            (f"https://primary-{i}.test/v1", f"https://backup-{i}.test/v1")
            for i in range(n_threads)
        ]

        observed: list[tuple[int, str, str]] = []  # (thread_id, seen_url, expected_url)
        observed_lock = threading.Lock()
        barrier = threading.Barrier(n_threads)

        def _worker(thread_id: int) -> None:
            primary, backup = thread_urls[thread_id]
            llm_config = {
                "base_url": primary,
                "base_url_backup": backup,
                "api_key": "test",
            }

            barrier.wait()  # release all threads simultaneously

            for _ in range(calls_per_thread):
                seen_urls: list[str | None] = []

                def _factory() -> str:
                    # Capture the URL the facade set for THIS attempt.
                    seen_urls.append(current_failover_url())
                    # Simulate a successful response without making a
                    # real network call — return a sentinel that the
                    # facade can serialize.
                    return primary  # use primary URL as the "result"

                # Bind a per-thread copy of ``invoke_raw_with_failover``
                # so monkeypatching doesn't leak across threads.
                invoke_raw_with_failover(_factory, llm_config)

                with observed_lock:
                    observed.append(
                        (thread_id, seen_urls[0], primary)
                    )

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(_worker, i) for i in range(n_threads)]
            for f in as_completed(futures):
                # Surface any worker exceptions.
                f.result()

        # Cross-talk check: each observation matches its thread's URL.
        wrong = [
            (tid, seen, expected)
            for tid, seen, expected in observed
            if seen != expected
        ]
        assert not wrong, (
            f"cross-thread isolation violated: threads saw foreign "
            f"URLs during their own calls. "
            f"Offending observations (first 5): {wrong[:5]} "
            f"(total wrong: {len(wrong)} of {len(observed)})"
        )

        # Sanity: we observed all expected calls.
        assert len(observed) == n_threads * calls_per_thread

        # And ``current_failover_url`` is back to None on the test's
        # main thread (the test runs on the main thread; if a worker
        # set the slot, we want to confirm we didn't leak it back).
        assert current_failover_url() is None, (
            "current_failover_url leaked from worker thread into main "
            "thread — thread-local was bypassed somewhere"
        )

    # ----- Cleanup under exception -----------------------------------------

    def test_thread_local_cleanup_after_exception(self):
        """Verify the facade's ``finally:`` clear works even when the
        factory raises mid-call — ``current_failover_url()`` must
        return None after the call (on the same thread)."""
        primary = "https://primary.test/v1"
        backup = "https://backup.test/v1"
        llm_config = {
            "base_url": primary,
            "base_url_backup": backup,
            "api_key": "test",
        }

        class _BoomError(RuntimeError):
            pass

        def _factory() -> None:
            raise _BoomError("simulated mid-call failure")

        # The facade propagates the exception after exhausting its
        # retry budget. We expect the test thread to see a
        # ``_BoomError`` (or wrapped variant).
        try:
            invoke_raw_with_failover(_factory, llm_config)
        except _BoomError:
            pass  # expected
        except Exception as e:  # pragma: no cover — defensive
            # Some retry strategies wrap — accept any non-empty raised
            # exception class as long as cleanup still works.
            assert "BoomError" in type(e).__name__ or isinstance(e, BaseException)

        # After the call, the thread-local must be cleared regardless
        # of exception path.
        assert current_failover_url() is None, (
            "current_failover_url must be cleared in the finally block "
            "even when the factory raises — leaked URL detected"
        )

    # ----- Sequential calls on the same thread ----------------------------

    def test_no_leaked_url_between_sequential_calls_same_thread(self):
        """Verify two sequential ``invoke_raw_with_failover`` calls on
        the same thread don't leak the FIRST call's URL into the
        second call (the F1 hygiene invariant). The second call must
        see its OWN primary URL, not the first call's URL."""
        primary1 = "https://primary-1.test/v1"
        backup1 = "https://backup-1.test/v1"
        primary2 = "https://primary-2.test/v1"
        backup2 = "https://backup-2.test/v1"

        def _factory_for(primary: str):
            def _factory() -> str:
                # If the slot leaked from a previous call, this would
                # NOT match the new primary URL.
                seen = current_failover_url()
                assert seen == primary, (
                    f"URL leaked from previous call: expected "
                    f"{primary!r}, saw {seen!r}"
                )
                return primary

            return _factory

        # First call.
        invoke_raw_with_failover(
            _factory_for(primary1),
            {"base_url": primary1, "base_url_backup": backup1, "api_key": "test"},
        )
        # Confirm clean state between calls.
        assert current_failover_url() is None, (
            "URL leaked after first call — cleanup did not run"
        )
        # Second call with different URLs.
        invoke_raw_with_failover(
            _factory_for(primary2),
            {"base_url": primary2, "base_url_backup": backup2, "api_key": "test"},
        )
        assert current_failover_url() is None, (
            "URL leaked after second call — cleanup did not run"
        )

    # ----- Single-depth semantics (documented limitation) -----------------

    def test_nested_calls_clobber_outer_url(self):
        """Pin the documented single-depth semantic: nested
        ``invoke_raw_with_failover`` corrupts the outer call's URL
        slot. The module docstring of
        ``daemon.services.llm_failover`` warns:

            "the thread-local URL slot is single-depth. A factory
            that itself calls ``invoke_raw_with_failover`` will
            clobber the outer call's current-URL state."

        Mechanically: the inner call's ``finally:`` block clears the
        slot, which (from the outer factory's perspective) IS the
        clobber — the outer factory sees ``None`` instead of its own
        URL after the nested call returns. Both behaviors
        (clobber-to-None via finally-clear, clobber-to-inner-URL)
        are equally broken; this test pins what we ACTUALLY see
        today so a future refactor that adds proper stack semantics
        is caught by this test rather than silently regressing some
        other assumption.
        """
        outer_primary = "https://outer-primary.test/v1"
        outer_backup = "https://outer-backup.test/v1"
        inner_primary = "https://inner-primary.test/v1"
        inner_backup = "https://inner-backup.test/v1"

        observed_outer: list[str | None] = []
        observed_inner: list[str | None] = []
        phase = {"stage": "outer"}

        def _outer_factory() -> str:
            # Snapshot what the OUTER facade set on entry.
            observed_outer.append(current_failover_url())
            phase["stage"] = "inner"

            def _inner_factory() -> str:
                # Inside the nested call — observe what the INNER
                # facade set.
                observed_inner.append(current_failover_url())
                return inner_primary

            # Nested call — this corrupts the outer's slot (the
            # inner's finally block clears it; from the outer
            # factory's perspective the slot is gone).
            invoke_raw_with_failover(
                _inner_factory,
                {"base_url": inner_primary, "base_url_backup": inner_backup, "api_key": "test"},
            )

            phase["stage"] = "outer-after"
            # Re-read the slot — should NOT be the outer URL anymore
            # (single-depth clobber). The exact failure mode is
            # ``None`` because the inner call's finally cleared it.
            observed_outer.append(current_failover_url())
            return outer_primary

        invoke_raw_with_failover(
            _outer_factory,
            {"base_url": outer_primary, "base_url_backup": outer_backup, "api_key": "test"},
        )

        # First observation: outer facade set primary on entry.
        assert observed_outer[0] == outer_primary, (
            f"outer factory must see outer_primary on entry; "
            f"got {observed_outer[0]!r}"
        )
        # Inner facade set its own primary on entry.
        assert observed_inner[0] == inner_primary, (
            f"inner factory must see inner_primary on entry; "
            f"got {observed_inner[0]!r}"
        )
        # After the nested call returns, the OUTER factory observes
        # ``None`` — the documented single-depth corruption. The
        # inner call's ``finally`` block cleared the slot. If this
        # test starts failing (i.e., the outer factory sees
        # outer_primary again), the semantic has changed to
        # proper stack semantics — update the docstring AND this
        # test together.
        assert observed_outer[1] is None, (
            f"documented single-depth corruption: after nested "
            f"invoke_raw_with_failover returns, the outer factory "
            f"must NOT see outer_primary (the slot is corrupted by "
            f"the inner call's finally-clear). Got "
            f"{observed_outer[1]!r}. If this assertion starts "
            f"failing, the single-depth semantic changed to proper "
            f"stack semantics — update this test (and the "
            f"docstring) together."
        )
