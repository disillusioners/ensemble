"""Adversarial tests for LLM HA failover v2 — per-site zero-drift, the
embedding-endpoint guard matrix, and MockTransport E2E on both facade
families.

Companion production code: ``daemon/services/llm_failover.py`` and the
9 secondary call sites it wraps (title_generation, keyword_extraction,
child_reports summarization + repair, compaction, skill_embedding chat
+ embeddings, skill_evolution chat, skill_search chat).

Three adversarial classes:

* ``TestZeroBehaviorChangeAllSitesBackupUnset`` — for EVERY secondary
  call site (LangChain family driven through the real site function,
  raw-SDK family driven through the real site function), with the
  backup UNSET (``None`` or the empty string a YAML ``${VAR:-}``
  substitution produces), while the primary endpoint returns retryable
  500s forever:

      (a) total attempts against the wire stay within the no-backup
          retry bound (``max(transient_max, timeout_max) = 3``);
      (b) NOT ONE request ever lands on any host other than the
          primary (swap must never fire);
      (c) the site's own graceful fallback is reached — a default
          value is returned or a site-level error is raised, never a
          naked ``TransientAPIError``;
      (d) NO ``[LLM-HA]`` WARNING is emitted (a swap never fired).

* ``TestEmbeddingGuardMatrix`` — the v2 Fix 2 critical matrix. The
  embedding-endpoint guard in ``skill_embedding_service.embed_text``
  must drop the chat backup ONLY when the explicit
  ``embedding_base_url`` is a GENUINELY different endpoint. Equivalent
  spellings (trailing slash, host/scheme case) must keep failover
  armed. Verified ON THE WIRE: a genuinely different endpoint never
  routes a request to the backup host; an equivalent spelling swaps
  and succeeds on backup.

* ``TestMockTransportFailoverBothFamilies`` — E2E failover through
  real site functions with the backup UP: requests demonstrably land
  on the backup URL (wire log), a ``[LLM-HA]`` WARNING is emitted, and
  the site returns the LLM-derived value (not its fallback). Plus the
  both-legs-down case where the site's graceful fallback fires.

Non-goals (covered by ``tests/unit/test_llm_failover_v2.py`` — do not
duplicate here): facade direct-unit semantics, budget-split arithmetic,
wiring-pins (facade-called assertions), pre-clean rebind hazard,
shared-config immutability, thread-local hygiene.

Runtime discipline: the tenacity wait between retries is neutralized
via a module-level patch (``wait_exponential_jitter`` → ``wait_fixed(0)``)
so 500-storm retry ladders do not sleep the suite. This patches the
NAME the facade module imported, so only facade-built ``Retrying``
objects are affected; production code is untouched.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from tenacity import wait_fixed

from daemon.llm_error_classifier import (
    PRIMARY_TIMEOUT_MAX,
    PRIMARY_TRANSIENT_MAX,
)
from daemon.services import llm_failover as lf_module


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

PRIMARY = "https://primary.test/v1"
BACKUP = "https://backup.test/v1"

# No-backup retry bound: sites gained bounded retry in v2; without a
# backup the ceiling is max(transient_max, timeout_max) = max(3, 2) = 3.
NO_BACKUP_MAX_ATTEMPTS = max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)
assert NO_BACKUP_MAX_ATTEMPTS == 3

# Both-backup retry bound (facade arithmetic when failover is armed).
WITH_BACKUP_MAX_ATTEMPTS = max(3, 2) + max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Neutralize the exponential backoff the facade installs between
    retries (``wait_exponential_jitter`` → ``wait_fixed(0)``).

    Without this, a 500-storm retry ladder sleeps ~1-3s between
    attempts; the adversarial suites drive dozens of those ladders and
    would blow the 2-minute pack budget. The patch targets the NAME
    bound in the facade module so only facade-constructed ``Retrying``
    instances are affected — production code paths are untouched.
    """
    monkeypatch.setattr(lf_module, "wait_exponential_jitter", lambda **kw: wait_fixed(0))


def _completion_body(content: str) -> dict:
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


def _embedding_body(text: str) -> dict:
    vec = [float((sum(ord(c) for c in text) + i) % 1000) / 1000.0 for i in range(8)]
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": vec}],
        "model": "text-embedding-test",
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


class WireLog:
    """MockTransport handler that records every request URL and lets the
    test script per-host responses.

    Default behavior: primary 500s forever (retryable storm), backup
    200s (chat completion). Tests override via ``respond``.
    """

    def __init__(self) -> None:
        self.urls: list[httpx.URL] = []
        # host -> callable(request) -> httpx.Response
        self.respond: dict[str, Any] = {}
        self.default = lambda req: httpx.Response(
            500, json={"error": {"message": "down", "type": "server_error"}}
        )

    @property
    def hosts(self) -> list[str]:
        return [u.host for u in self.urls]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(request.url)
        fn = self.respond.get(request.url.host)
        if fn is not None:
            return fn(request)
        return self.default(request)

    def http_client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _patch_langchain_constructor(module, wire: WireLog):
    """Patch the ``ThinkingChatOpenAI`` symbol a site resolves, so every
    constructed client carries our MockTransport + ``max_retries=0``
    (the openai SDK's OWN retry must not multiply wire counts).

    Sites that import the symbol at module top (title_generation,
    child_reports) are patched on the site module; sites that import
    it LAZILY inside the function body (keyword_extraction,
    compaction — ``from ..graph import ThinkingChatOpenAI``) resolve
    the symbol from ``daemon.graph`` at call time, so the patch lands
    there instead.

    Transport injection ONLY: all production kwargs still flow to the
    REAL constructor — same disclosure convention as the v2 suite.
    """
    from daemon import graph as graph_mod

    if hasattr(module, "ThinkingChatOpenAI"):
        target, real = module, module.ThinkingChatOpenAI
    else:
        target, real = graph_mod, graph_mod.ThinkingChatOpenAI

    def _injected(*args: Any, **kwargs: Any):
        kwargs["http_client"] = wire.http_client()
        kwargs["max_retries"] = 0
        # Opt out of streaming: this helper injects httpx.MockTransport
        # which returns raw JSON chat.completion bodies. The streaming
        # SDK path expects SSE-format chunks; without this opt-out the
        # mock is rejected with "No generations found in stream".
        # Production defaults streaming ON via clean_llm_config; that
        # wire-format path is exercised end-to-end in
        # test_llm_streaming_activation.py (request-side ``stream: true``
        # wire payload + the ``TestStreamingInvokeEndToEnd`` SSE round-
        # trip invoke test that aggregates content / reasoning /
        # tool_calls / usage_metadata from a real-shaped chunk stream).
        kwargs["streaming"] = False
        return real(*args, **kwargs)

    return patch.object(target, "ThinkingChatOpenAI", side_effect=_injected)


def _patch_raw_openai(module, wire: WireLog):
    """Patch a site module's ``openai`` symbol so every constructed raw
    client carries our MockTransport + ``max_retries=0``."""
    real_openai = module.openai
    real_ctor = real_openai.OpenAI

    def _injected(**kwargs: Any):
        kwargs["http_client"] = wire.http_client()
        kwargs["max_retries"] = 0
        return real_ctor(**kwargs)

    return patch.object(real_openai, "OpenAI", side_effect=_injected)


def _llm_ha_warnings(records: list[logging.LogRecord]) -> list[str]:
    return [
        r.getMessage()
        for r in records
        if r.levelno >= logging.WARNING and "[LLM-HA]" in r.getMessage()
    ]


def _manager_stub(*, backup: str | None, model: str = "gpt-test") -> SimpleNamespace:
    """Manager stub shaped for TitleGenerationService / ChildReportsService:
    ``config.llm.{base_url, base_url_backup, api_key, model*}`` plus the
    repository / checkpointer attributes the site functions touch."""

    class _FakeRepo:
        def get(self, iid: str) -> SimpleNamespace:
            return SimpleNamespace(instance_metadata={})

        def update_title(self, iid: str, title: str) -> None:
            return None

    class _FakeCheckpointerAdapter:
        raw_saver = MagicMock()

    return SimpleNamespace(
        config=SimpleNamespace(
            llm=SimpleNamespace(
                base_url=PRIMARY,
                base_url_backup=backup,
                api_key="test",
                model=model,
                model_title=model,
                model_keywords=model,
                # Production reads `self._config.llm.buffer_response_header`
                # (e.g. title_generation.py:104, child_reports.py:766/:1400).
                # `LLMConfig.buffer_response_header` defaults to True
                # (daemon/config.py:231); mirror that here so the fake matches.
                buffer_response_header=True,
            )
        ),
        _instance_repository=_FakeRepo(),
        _checkpointer=_FakeCheckpointerAdapter(),
        _logger=MagicMock(),
    )


def _embedding_service(*, embedding_base_url: str | None) -> Any:
    from daemon.services.skill_embedding_service import SkillEmbeddingService

    cfg = SimpleNamespace(
        embedding_model="text-embedding-test",
        embedding_base_url=embedding_base_url,
        embedding_api_key=None,
    )
    return SkillEmbeddingService(
        config=cfg,
        embedding_repo=SimpleNamespace(),
        llm_config={
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        },
    )


# ===========================================================================
# CLASS A — Zero-behavior-change at every secondary site, backup unset
# ===========================================================================


class TestZeroBehaviorChangeAllSitesBackupUnset:
    """Backup unset (None or "") + primary 500-storm → for each of the
    9 secondary sites: attempts bounded by the no-backup budget, all on
    primary; site's graceful fallback reached; no swap warning."""

    # ------------------------------------------------------------------
    # LangChain family (5 sites) — driven through the real site functions
    # ------------------------------------------------------------------

    def _drive_title_generation(self, caplog, backup: str | None):
        from daemon.services import title_generation as tg
        from daemon.services.title_generation import TitleGenerationService

        wire = WireLog()
        mgr = _manager_stub(backup=backup)
        titles: list[str] = []
        mgr._instance_repository.update_title = lambda iid, t: titles.append(t)
        svc = TitleGenerationService(manager=mgr)

        with caplog.at_level(logging.WARNING):
            with _patch_langchain_constructor(tg, wire):
                asyncio.run(svc._generate_and_broadcast_title("inst-1", "Hello world"))

        # (c) graceful fallback: title generation is best-effort — the
        # LLM failure must NOT raise; no title is stored.
        assert titles == [], "primary 500-storm with no backup must not store a title"
        return wire, caplog.records

    def _drive_keyword_extraction(self, caplog, backup: str | None):
        from daemon.services import keyword_extraction as kx

        wire = WireLog()

        cfg = SimpleNamespace(
            llm=SimpleNamespace(
                base_url=PRIMARY,
                base_url_backup=backup,
                api_key="test",
                model="gpt-test",
                model_keywords="gpt-test",
                # Production reads `config.llm.buffer_response_header`
                # (keyword_extraction.py:377). `LLMConfig.buffer_response_header`
                # defaults to True (daemon/config.py:231); mirror that here so
                # the fake matches.
                buffer_response_header=True,
            )
        )

        with caplog.at_level(logging.WARNING):
            with _patch_langchain_constructor(kx, wire):
                result = asyncio.run(
                    kx.extract_keywords(message="Hello world", config=cfg, timeout_s=30)
                )

        # (c) graceful fallback: [] on any failure (documented contract).
        assert result == [], (
            f"keyword_extraction must return [] on LLM failure; got {result!r}"
        )
        return wire, caplog.records

    def _drive_child_reports_summarization(self, caplog, backup: str | None):
        from daemon.services import child_reports as cr

        wire = WireLog()
        mgr = _manager_stub(backup=backup)
        svc = cr.ChildReportsService(manager=mgr)

        async def _fake_get_messages(checkpointer, instance_id, manager=None):
            return [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]

        with caplog.at_level(logging.WARNING):
            with _patch_langchain_constructor(cr, wire):
                with patch.object(cr, "get_instance_messages", side_effect=_fake_get_messages):
                    with patch.object(svc, "_get_instance_report_prefix", return_value="Agent"):
                        out = asyncio.run(svc._summarize_instance("inst-1", "agent-1"))

        # (c) graceful fallback: count-based summary, no raise.
        assert "Completed 2 message(s)" in out, (
            f"summarization fallback must be the count-based summary; got {out!r}"
        )
        return wire, caplog.records

    def _drive_child_reports_repair(self, caplog, backup: str | None):
        from daemon.config import ReportRepairConfig
        from daemon.services import child_reports as cr

        wire = WireLog()
        mgr = _manager_stub(backup=backup)
        svc = cr.ChildReportsService(manager=mgr)
        messages = [
            {"role": "assistant", "content": "First message " * 20},
            {"role": "assistant", "content": "Second message " * 20},
        ]

        with caplog.at_level(logging.WARNING):
            with _patch_langchain_constructor(cr, wire):
                out = asyncio.run(
                    svc._repair_report_with_llm(
                        messages, ReportRepairConfig(), instance_id="inst-1"
                    )
                )

        # (c) graceful fallback: None on LLM failure (caller combines).
        assert out is None, f"repair must return None on LLM failure; got {out!r}"
        return wire, caplog.records

    def _drive_compaction(self, caplog, backup: str | None):
        from langchain_core.messages import HumanMessage

        from daemon import compaction as cmp

        wire = WireLog()
        llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": backup,
            "api_key": "test",
            "model": "gpt-test",
        }
        context = cmp.CompactionContext(
            messages=[HumanMessage(content="Hello")],
            system_prompt_tokens=100,
            model_name="gpt-test",
            config=cmp.CompactionConfig(),
            llm_config=llm_config,
        )
        compactor = cmp.ContextCompactor(config=cmp.CompactionConfig(), llm_config=llm_config)

        # Compaction's LLM failure propagates to the CALLER's graceful
        # truncation fallback — here we assert the site layer surfaces
        # the exhausted-retry exception (the caller catches Exception).
        caught: BaseException | None = None
        logger_records: list[logging.LogRecord] = []

        class _Cap:
            def add(self, r):
                logger_records.append(r)

        import logging as _logging

        handler = _logging.Handler()
        handler.emit = lambda r: logger_records.append(r)  # type: ignore[method-assign]
        root = _logging.getLogger()
        root.addHandler(handler)
        try:
            with _patch_langchain_constructor(cmp, wire):
                try:
                    asyncio.run(compactor._call_summarization_llm("Test prompt", context))
                except Exception as e:  # noqa: BLE001 — site contract under test
                    caught = e
        finally:
            root.removeHandler(handler)

        # (c) site layer raises (caller's truncation fallback fires) —
        # the exception must be the exhausted retry, NOT a swap artifact.
        assert caught is not None, "compaction must surface the LLM failure to its caller"
        return wire, logger_records

    # ------------------------------------------------------------------
    # Raw-SDK family (4 sites) — driven through the real site functions
    # ------------------------------------------------------------------

    def _drive_skill_embedding_chat(self, caplog, backup: str | None):
        from daemon.services import skill_embedding_service as ses
        from daemon.services.skill_embedding_service import SkillEmbeddingService

        wire = WireLog()
        svc = SkillEmbeddingService(
            config=SimpleNamespace(
                embedding_model="text-embedding-test",
                embedding_base_url=None,
                embedding_api_key=None,
            ),
            embedding_repo=SimpleNamespace(),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": backup,
                "api_key": "test",
                "model": "gpt-test",
            },
        )
        skill = SimpleNamespace(id="sk-1", name="s", description="d", content="c")

        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(ses, wire):
                result = asyncio.run(svc.generate_trigger_queries(skill))

        # (c) graceful fallback: [] on any failure (documented contract).
        assert result == [], (
            f"generate_trigger_queries must return [] on failure; got {result!r}"
        )
        return wire, caplog.records

    def _drive_skill_embedding_embeddings(self, caplog, backup: str | None):
        from daemon.services import skill_embedding_service as ses

        wire = WireLog()
        # embedding_base_url unset → embed endpoint = chat base_url →
        # guard does NOT drop the backup … which is None here anyway.
        svc = _embedding_service(embedding_base_url=None)
        svc.llm_config["base_url_backup"] = backup

        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(ses, wire):
                with pytest.raises(RuntimeError, match="Embedding API call failed"):
                    asyncio.run(svc.embed_text("hello"))

        return wire, caplog.records

    def _drive_skill_evolution_chat(self, caplog, backup: str | None):
        from daemon.services import skill_evolution_service as sev

        wire = WireLog()
        svc = sev.SkillEvolutionService(
            skill_repo=SimpleNamespace(),
            lineage_repo=SimpleNamespace(),
            usage_repo=SimpleNamespace(),
            embedding_service=SimpleNamespace(),
            metrics_service=SimpleNamespace(),
            ab_test_repo=SimpleNamespace(),
            config=SimpleNamespace(analysis_model=None, evolution_model=None),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": backup,
                "api_key": "test",
                "model": "gpt-test",
            },
        )

        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(sev, wire):
                out = asyncio.run(svc._call_llm("analyze this skill"))

        # (c) graceful fallback: "" on any failure (documented contract).
        assert out == "", f"_call_llm must return '' on failure; got {out!r}"
        return wire, caplog.records

    @staticmethod
    def _drive_skill_search_chat(caplog, backup: str | None):
        from daemon.services import skill_search_service as sss

        wire = WireLog()
        svc = sss.SkillSearchService(
            skill_repo=SimpleNamespace(),
            embedding_repo=SimpleNamespace(),
            embedding_service=SimpleNamespace(),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": backup,
                "api_key": "test",
                "model": "gpt-test",
            },
            config=SimpleNamespace(bm25_top_k=10, llm_select_top_k=5),
        )
        candidate = SimpleNamespace(
            name="test_skill", description="test desc", content="test content"
        )

        raised: BaseException | None = None
        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(sss, wire):
                try:
                    asyncio.run(
                        svc._llm_select(
                            query="hi", candidates=[(candidate, 0.9)], max_results=2
                        )
                    )
                except Exception as e:  # noqa: BLE001 — the site's contract
                    raised = e

        # (c) site contract on LLM failure: ``_llm_select`` RAISES so
        # the public ``search`` catches it and falls to
        # ``_degraded_select``. The raise IS the graceful hand-off.
        assert raised is not None, (
            "_llm_select must surface the LLM failure so search() can "
            "degrade to the ranked-candidates fallback"
        )
        return wire, caplog.records, raised

    # ------------------------------------------------------------------
    # The parametrized zero-drift matrix
    # ------------------------------------------------------------------

    LANGCHAIN_DRIVERS = {
        "title_generation": _drive_title_generation,
        "keyword_extraction": _drive_keyword_extraction,
        "child_reports_summarization": _drive_child_reports_summarization,
        "child_reports_repair": _drive_child_reports_repair,
        "compaction": _drive_compaction,
    }
    RAW_DRIVERS = {
        "skill_embedding_chat": _drive_skill_embedding_chat,
        "skill_embedding_embeddings": _drive_skill_embedding_embeddings,
        "skill_evolution_chat": _drive_skill_evolution_chat,
    }

    def _assert_zero_drift(self, site: str, backup: str | None, wire: WireLog, records):
        hosts = wire.hosts
        # (a) attempts bounded by the no-backup budget …
        assert 1 <= len(hosts) <= NO_BACKUP_MAX_ATTEMPTS, (
            f"[{site}] backup={backup!r}: primary 500-storm must stay within the "
            f"no-backup bound of {NO_BACKUP_MAX_ATTEMPTS} wire attempts; "
            f"got {len(hosts)}: {hosts}"
        )
        # … and every one of them hit the primary host (b) — no swap.
        non_primary = [h for h in hosts if h != "primary.test"]
        assert non_primary == [], (
            f"[{site}] backup={backup!r}: no request may leave the primary host "
            f"when the backup is unset; saw {non_primary}"
        )
        # (d) no swap warning was emitted.
        ha_warnings = _llm_ha_warnings(records)
        assert ha_warnings == [], (
            f"[{site}] backup={backup!r}: no [LLM-HA] WARNING may fire with the "
            f"backup unset; saw {ha_warnings}"
        )

    @pytest.mark.parametrize("backup_variant", [None, ""], ids=["backup-none", "backup-empty-str"])
    @pytest.mark.parametrize(
        "site",
        [
            "title_generation",
            "keyword_extraction",
            "child_reports_summarization",
            "child_reports_repair",
            "compaction",
        ],
    )
    def test_langchain_site_zero_drift(self, site, backup_variant, caplog):
        """LangChain family: 5 sites × {None, ""} backup variants."""
        driver = self.LANGCHAIN_DRIVERS[site]
        wire, records = driver(self, caplog, backup_variant)
        self._assert_zero_drift(site, backup_variant, wire, records)

    @pytest.mark.parametrize("backup_variant", [None, ""], ids=["backup-none", "backup-empty-str"])
    @pytest.mark.parametrize(
        "site",
        [
            "skill_embedding_chat",
            "skill_embedding_embeddings",
            "skill_evolution_chat",
        ],
    )
    def test_raw_site_zero_drift(self, site, backup_variant, caplog):
        """Raw-SDK family: 3 sites × {None, ""} backup variants."""
        driver = self.RAW_DRIVERS[site]
        wire, records = driver(self, caplog, backup_variant)
        self._assert_zero_drift(site, backup_variant, wire, records)

    def test_skill_search_zero_drift_backup_none(self, caplog):
        """skill_search._llm_select (raw-SDK site #4) — backup None."""
        wire, records, _ = self._drive_skill_search_chat(caplog, None)
        self._assert_zero_drift("skill_search_chat", None, wire, records)

    def test_skill_search_zero_drift_backup_empty_string(self, caplog):
        """skill_search._llm_select — backup "" (the YAML ``${VAR:-}`` shape)."""
        wire, records, _ = self._drive_skill_search_chat(caplog, "")
        self._assert_zero_drift("skill_search_chat", "", wire, records)

    def test_skill_search_llm_failure_routes_to_degraded_select(self, caplog):
        """The site contract on LLM failure: ``search`` catches the raise
        from ``_llm_select`` and degrades to the ranked-candidates
        fallback — pin the degraded payload keeps the skill object and
        the LLM leg really exhausted (bounded attempts, primary only)."""
        from daemon.services import skill_search_service as sss

        wire = WireLog()
        svc = sss.SkillSearchService(
            skill_repo=SimpleNamespace(),
            embedding_repo=SimpleNamespace(),
            embedding_service=SimpleNamespace(),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": None,
                "api_key": "test",
                "model": "gpt-test",
            },
            config=SimpleNamespace(bm25_top_k=10, llm_select_top_k=5),
        )
        candidates = [
            (SimpleNamespace(name="s1", description="d1", content="c1"), 0.9),
            (SimpleNamespace(name="s2", description="d2", content="c2"), 0.5),
        ]

        # First prove the LLM leg really raises after its bounded storm
        # (this is the raise ``search`` catches to route into the
        # degraded fallback).
        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(sss, wire):
                with pytest.raises(Exception):  # noqa: BLE001 — site raises to caller
                    asyncio.run(
                        svc._llm_select(
                            query="hi", candidates=[(candidates[0][0], 0.9)], max_results=2
                        )
                    )
        hosts = wire.hosts
        assert 1 <= len(hosts) <= NO_BACKUP_MAX_ATTEMPTS
        assert all(h == "primary.test" for h in hosts)

        # Then pin the degraded fallback contract ``search`` lands on.
        with _patch_raw_openai(sss, wire):
            degraded = svc._degraded_select(candidates, max_results=1)
        assert degraded["injected"][0]["skill"].name == "s1"
        assert degraded["low_match"][0]["name"] == "s2"


# ===========================================================================
# CLASS B — Embedding-endpoint guard matrix (v2 Fix 2 critical test)
# ===========================================================================


class TestEmbeddingGuardMatrix:
    """The chat backup is WRONG for an embedding call whose explicit
    ``embedding_base_url`` is a genuinely different endpoint (different
    creds / model). The guard must drop the backup in exactly that case
    and ONLY that case — equivalent spellings of the SAME endpoint keep
    failover armed (v2 Fix 2: raw ``!=`` used to disable HA for
    ``"https://x/v1"`` vs ``"https://x/v1/"`` and host-case variants).

    Every case is verified ON THE WIRE: the primary (embedding endpoint)
    500s forever; the backup host would 200. Failover-armed cases must
    swap and SUCCEED on backup; guarded cases must NEVER route a single
    request to the backup host.
    """

    def _embed_with_transport(self, *, embedding_base_url, caplog):
        from daemon.services import skill_embedding_service as ses

        wire = WireLog()
        wire.respond["backup.test"] = lambda req: httpx.Response(
            200, json=_embedding_body("hello")
        )
        svc = _embedding_service(embedding_base_url=embedding_base_url)

        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(ses, wire):
                try:
                    vec = asyncio.run(svc.embed_text("hello"))
                except RuntimeError:
                    vec = None
        return svc, wire, vec, caplog.records

    # ---- Failover must be ACTIVE (backup inherited, guard passes) ----

    @pytest.mark.parametrize(
        "embedding_base_url",
        [
            None,                        # unset → inherits chat endpoint
            "https://primary.test/v1",   # byte-identical
            "https://primary.test/v1/",  # same endpoint, trailing slash (Fix 2)
            "https://PRIMARY.test/v1",   # host case differs (Fix 2)
            "HTTPS://primary.test/v1",   # scheme case differs
            "https://primary.test/v1?x=1#frag",  # query/fragment dropped
        ],
        ids=[
            "unset-inherits",
            "byte-identical",
            "trailing-slash",
            "host-case",
            "scheme-case",
            "query-fragment",
        ],
    )
    def test_equivalent_endpoint_failover_active(self, embedding_base_url, caplog):
        """Equivalent endpoint → embedding call swaps to the chat backup
        and SUCCEEDS there."""
        svc, wire, vec, records = self._embed_with_transport(
            embedding_base_url=embedding_base_url, caplog=caplog
        )

        assert vec is not None and len(vec) == 8, (
            f"embedding_base_url={embedding_base_url!r} names the SAME endpoint as "
            f"the chat base_url — failover must stay armed and succeed on backup"
        )
        hosts = wire.hosts
        assert hosts.count("backup.test") >= 1, (
            f"equivalent endpoint spelling must still fail over to the chat "
            f"backup; wire hosts={hosts}"
        )
        # The primary slice exhausted before the swap (default budget).
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"primary slice must exhaust ({PRIMARY_TRANSIENT_MAX}) before swap; "
            f"wire hosts={hosts}"
        )
        assert any("[LLM-HA]" in m for m in _llm_ha_warnings(records)), (
            "the swap must emit its greppable [LLM-HA] WARNING"
        )

    # ---- Failover must be DISABLED (genuinely different endpoint) ----

    @pytest.mark.parametrize(
        "embedding_base_url",
        [
            "https://different-endpoint.test/v1",  # different host
            "https://primary.test:8443/v1",        # different port
            "https://primary.test/embed",          # different path
            "http://primary.test/v1",              # different scheme
        ],
        ids=["different-host", "different-port", "different-path", "different-scheme"],
    )
    def test_genuinely_different_endpoint_failover_disabled(self, embedding_base_url, caplog):
        """Genuinely different endpoint → embed_backup dropped: bounded
        retry on the embedding endpoint ONLY, zero requests to any
        backup host, RuntimeError raised at the site layer."""
        svc, wire, vec, records = self._embed_with_transport(
            embedding_base_url=embedding_base_url, caplog=caplog
        )

        # Site-level graceful failure reached.
        assert vec is None, (
            f"embedding_base_url={embedding_base_url!r} is a different endpoint — "
            f"the 500-storm must fail the call, not succeed via some other route"
        )
        hosts = wire.hosts
        # Zero backup hits — the guard short-circuited the chat backup.
        assert hosts.count("backup.test") == 0, (
            f"embedding call with a differing explicit embedding_base_url must "
            f"NEVER route to the chat backup host; wire hosts={hosts}"
        )
        # Bounded no-backup retry against the embedding endpoint itself.
        expected_host = wire.urls[0].host
        assert 1 <= len(hosts) <= NO_BACKUP_MAX_ATTEMPTS, (
            f"guarded embedding call must stay within the no-backup bound "
            f"({NO_BACKUP_MAX_ATTEMPTS}); wire hosts={hosts}"
        )
        assert all(h == expected_host for h in hosts), (
            f"all attempts must stay on the embedding endpoint {expected_host!r}; "
            f"wire hosts={hosts}"
        )
        # No swap warning — failover never fired.
        assert _llm_ha_warnings(records) == [], (
            f"guarded embedding call must not emit [LLM-HA] swap warnings; "
            f"saw {_llm_ha_warnings(records)}"
        )

    # ---- Guard normalization unit pins (comparison layer) ----

    @pytest.mark.parametrize(
        "other, equivalent",
        [
            ("https://primary.test/v1", True),
            ("https://primary.test/v1/", True),
            ("https://PRIMARY.test/v1", True),
            ("HTTPS://primary.test/v1", True),
            ("https://user:pass@primary.test/v1", True),   # userinfo dropped
            ("https://different.test/v1", False),
            # Port is PRESERVED (documented) — an explicit :443 is NOT
            # normalized away, so it compares as a different endpoint.
            # Conservative direction: guard keeps retrying the exact
            # embedding endpoint rather than ever swapping.
            ("https://primary.test:443/v1", False),
            ("https://primary.test:8443/v1", False),
            ("https://primary.test/embed", False),
            ("http://primary.test/v1", False),
        ],
        ids=[
            "identical",
            "trailing-slash",
            "host-case",
            "scheme-case",
            "userinfo",
            "diff-host",
            "explicit-default-port-preserved",
            "diff-port",
            "diff-path",
            "diff-scheme",
        ],
    )
    def test_normalize_endpoint_url_equivalence(self, other, equivalent):
        """Pin the comparator: scheme/host case-insensitive, port
        preserved, trailing slash stripped, userinfo/query/fragment
        dropped — path compared case-SENSITIVELY."""
        from daemon.services.skill_embedding_service import _normalize_endpoint_url

        base = "https://primary.test/v1"
        same = _normalize_endpoint_url(base) == _normalize_endpoint_url(other)
        assert same is equivalent, (
            f"_normalize_endpoint_url({other!r}) vs {base!r}: expected "
            f"equivalent={equivalent}, got {same}"
        )

    def test_normalize_endpoint_url_path_is_case_sensitive(self):
        """Paths CAN be case-sensitive on real servers — ``/V1`` and
        ``/v1`` are NOT treated as equivalent (recon confirmed the
        comparator keeps path case)."""
        from daemon.services.skill_embedding_service import _normalize_endpoint_url

        assert _normalize_endpoint_url("https://x.test/V1") != _normalize_endpoint_url(
            "https://x.test/v1"
        )

    def test_normalize_endpoint_url_empty_and_malformed(self):
        """Empty/None → ""; malformed → conservative raw compare."""
        from daemon.services.skill_embedding_service import _normalize_endpoint_url

        assert _normalize_endpoint_url(None) == ""
        assert _normalize_endpoint_url("") == ""
        # A malformed URL falls back to the raw trimmed string — never raises.
        out = _normalize_endpoint_url("http://[::1")
        assert isinstance(out, str) and out


# ===========================================================================
# CLASS C — MockTransport failover E2E, both families + both-legs-down
# ===========================================================================


class TestMockTransportFailoverBothFamilies:
    """Backup UP + primary DOWN → the failover must be observable on the
    WIRE (requests land on the backup host), audible in the LOGS (one
    ``[LLM-HA]`` WARNING), and correct at the SITE layer (LLM-derived
    value, not the graceful fallback). Both-legs-down → graceful
    fallback at the site layer."""

    # ---- LangChain family: title_generation E2E ----

    def test_langchain_title_generation_swaps_on_wire_and_succeeds(self, caplog):
        """Drive the REAL ``_generate_and_broadcast_title`` with a real
        ``ThinkingChatOpenAI`` + MockTransport. Primary 500s → swap →
        backup 200s → the generated title is STORED (fallback not
        taken)."""
        from daemon.services import title_generation as tg
        from daemon.services.title_generation import TitleGenerationService

        wire = WireLog()
        wire.respond["backup.test"] = lambda req: httpx.Response(
            200, json=_completion_body("A Great Title")
        )

        mgr = _manager_stub(backup=BACKUP)
        titles: list[str] = []
        mgr._instance_repository.update_title = lambda iid, t: titles.append(t)
        svc = TitleGenerationService(manager=mgr)

        with caplog.at_level(logging.WARNING):
            with _patch_langchain_constructor(tg, wire):
                asyncio.run(svc._generate_and_broadcast_title("inst-1", "Hello world"))

        assert titles == ["A Great Title"], (
            f"failover must deliver the LLM-derived title, not the fallback; "
            f"stored={titles!r}"
        )
        hosts = wire.hosts
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"primary slice must exhaust before swap; wire hosts={hosts}"
        )
        assert hosts.count("backup.test") == 1, (
            f"exactly one backup request must hit the wire after the swap; "
            f"wire hosts={hosts}"
        )
        ha = _llm_ha_warnings(caplog.records)
        assert ha, "the swap must emit a [LLM-HA] WARNING"
        assert "primary=" in ha[0] and "backup=" in ha[0], (
            f"[LLM-HA] WARNING must carry both URLs for greppability; got {ha[0]!r}"
        )

    # ---- Raw-SDK family: skill_search E2E ----

    def test_raw_skill_search_swaps_on_wire_and_succeeds(self, caplog):
        """Drive the REAL ``SkillSearchService._llm_select`` with a real
        ``openai.OpenAI`` + MockTransport. Primary 500s → facade
        rebuilds the client against the backup → selection parsed from
        the backup response."""
        from daemon.services import skill_search_service as sss

        wire = WireLog()
        wire.respond["backup.test"] = lambda req: httpx.Response(
            200,
            json=_completion_body(
                '{"selected": [{"name": "test_skill", "score": 0.9}], '
                '"low_match": []}'
            ),
        )

        svc = sss.SkillSearchService(
            skill_repo=SimpleNamespace(),
            embedding_repo=SimpleNamespace(),
            embedding_service=SimpleNamespace(),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "gpt-test",
            },
            config=SimpleNamespace(bm25_top_k=10, llm_select_top_k=5),
        )
        candidate = SimpleNamespace(
            name="test_skill", description="test desc", content="test content"
        )

        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(sss, wire):
                result = asyncio.run(
                    svc._llm_select(query="hi", candidates=[(candidate, 0.9)], max_results=2)
                )

        # LLM-derived selection (not the degraded fallback shape).
        assert len(result["injected"]) == 1
        assert result["injected"][0]["skill"].name == "test_skill"
        hosts = wire.hosts
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"raw-SDK primary slice must exhaust before swap; wire hosts={hosts}"
        )
        assert hosts.count("backup.test") >= 1, (
            f"raw-SDK swap must reach the wire; wire hosts={hosts}"
        )
        assert _llm_ha_warnings(caplog.records), (
            "the raw-SDK swap must emit its [LLM-HA] WARNING "
            "(\"secondary raw-SDK swap\")"
        )

    def test_raw_skill_evolution_swaps_and_returns_llm_text(self, caplog):
        """Second raw-SDK site: ``SkillEvolutionService._call_llm``
        fails over and returns the backup's LLM text (not the ""-on-
        failure fallback)."""
        from daemon.services import skill_evolution_service as sev

        wire = WireLog()
        wire.respond["backup.test"] = lambda req: httpx.Response(
            200, json=_completion_body("VERDICT: NONE")
        )

        svc = sev.SkillEvolutionService(
            skill_repo=SimpleNamespace(),
            lineage_repo=SimpleNamespace(),
            usage_repo=SimpleNamespace(),
            embedding_service=SimpleNamespace(),
            metrics_service=SimpleNamespace(),
            ab_test_repo=SimpleNamespace(),
            config=SimpleNamespace(analysis_model=None, evolution_model=None),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "gpt-test",
            },
        )

        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(sev, wire):
                out = asyncio.run(svc._call_llm("analyze this skill"))

        assert out == "VERDICT: NONE", (
            f"failover must deliver the LLM text, not the ''-on-failure "
            f"fallback; got {out!r}"
        )
        hosts = wire.hosts
        assert hosts.count("backup.test") >= 1, f"swap must reach the wire; {hosts}"
        assert _llm_ha_warnings(caplog.records), "swap WARNING must fire"

    def test_langchain_keyword_extraction_swaps_and_returns_keywords(self, caplog):
        """Second LangChain site: ``extract_keywords`` fails over and
        returns the parsed keywords (not the []-on-failure fallback)."""
        from daemon.services import keyword_extraction as kx

        wire = WireLog()
        wire.respond["backup.test"] = lambda req: httpx.Response(
            200, json=_completion_body("deploy, rollback, kubernetes")
        )

        cfg = SimpleNamespace(
            llm=SimpleNamespace(
                base_url=PRIMARY,
                base_url_backup=BACKUP,
                api_key="test",
                model="gpt-test",
                model_keywords="gpt-test",
                # Production reads `config.llm.buffer_response_header`
                # (keyword_extraction.py:377). `LLMConfig.buffer_response_header`
                # defaults to True (daemon/config.py:231); mirror that here so
                # the fake matches.
                buffer_response_header=True,
            )
        )

        with caplog.at_level(logging.WARNING):
            with _patch_langchain_constructor(kx, wire):
                result = asyncio.run(
                    kx.extract_keywords(message="Hello world", config=cfg, timeout_s=30)
                )

        assert result != [], (
            f"failover must deliver parsed keywords, not the [] fallback; got {result!r}"
        )
        hosts = wire.hosts
        assert hosts.count("backup.test") >= 1, f"swap must reach the wire; {hosts}"
        assert _llm_ha_warnings(caplog.records), "swap WARNING must fire"

    # ---- Both legs down → site graceful fallback ----

    def test_both_legs_down_langchain_site_falls_back_gracefully(self, caplog):
        """Primary AND backup both 500 → title_generation exhausts both
        legs and takes its graceful fallback (no title stored, no
        raise out of the fire-and-forget task)."""
        from daemon.services import title_generation as tg
        from daemon.services.title_generation import TitleGenerationService

        wire = WireLog()  # default: 500 on every host

        mgr = _manager_stub(backup=BACKUP)
        titles: list[str] = []
        mgr._instance_repository.update_title = lambda iid, t: titles.append(t)
        svc = TitleGenerationService(manager=mgr)

        with caplog.at_level(logging.WARNING):
            with _patch_langchain_constructor(tg, wire):
                asyncio.run(svc._generate_and_broadcast_title("inst-1", "Hello world"))

        assert titles == [], (
            f"both legs down → no title stored (graceful fallback); got {titles!r}"
        )
        hosts = wire.hosts
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"primary slice must exhaust; wire hosts={hosts}"
        )
        assert hosts.count("backup.test") >= 1, (
            f"backup leg must be exercised before fallback; wire hosts={hosts}"
        )
        # Full HA budget respected: 3 primary + up to 3 backup = 6 max.
        assert len(hosts) <= WITH_BACKUP_MAX_ATTEMPTS, (
            f"both-legs ladder must respect the HA budget "
            f"({WITH_BACKUP_MAX_ATTEMPTS}); wire hosts={hosts}"
        )

    def test_both_legs_down_raw_site_falls_back_gracefully(self, caplog):
        """Both legs 500 → skill_embedding chat path exhausts and
        returns [] (its documented best-effort fallback)."""
        from daemon.services import skill_embedding_service as ses
        from daemon.services.skill_embedding_service import SkillEmbeddingService

        wire = WireLog()  # default: 500 on every host

        svc = SkillEmbeddingService(
            config=SimpleNamespace(
                embedding_model="text-embedding-test",
                embedding_base_url=None,
                embedding_api_key=None,
            ),
            embedding_repo=SimpleNamespace(),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "gpt-test",
            },
        )
        skill = SimpleNamespace(id="sk-1", name="s", description="d", content="c")

        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(ses, wire):
                result = asyncio.run(svc.generate_trigger_queries(skill))

        assert result == [], (
            f"both legs down → generate_trigger_queries must return [] ; got {result!r}"
        )
        hosts = wire.hosts
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX
        assert hosts.count("backup.test") >= 1
        assert len(hosts) <= WITH_BACKUP_MAX_ATTEMPTS

    def test_both_legs_down_embed_text_raises_runtime_error(self, caplog):
        """Both legs 500 → ``embed_text`` (which has no soft fallback)
        raises its site-level RuntimeError after exhausting both legs."""
        from daemon.services import skill_embedding_service as ses

        wire = WireLog()  # default: 500 on every host
        svc = _embedding_service(embedding_base_url=None)

        with caplog.at_level(logging.WARNING):
            with _patch_raw_openai(ses, wire):
                with pytest.raises(RuntimeError, match="Embedding API call failed"):
                    asyncio.run(svc.embed_text("hello"))

        hosts = wire.hosts
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX
        assert hosts.count("backup.test") >= 1
        assert len(hosts) <= WITH_BACKUP_MAX_ATTEMPTS
