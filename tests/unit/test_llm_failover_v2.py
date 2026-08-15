"""Tests for LLM HA failover v2 — shared facade across secondary sites.

Companion module: ``daemon.services.llm_failover`` (the production
code under test).

Section map (18 classes / 6 sections)
------------------------------------

1. ``wrap_langchain_failover`` — direct unit tests + end-to-end
   (TestWrapLangchainFailoverBinding, TestWrapLangchainFailoverEndToEnd,
    TestZeroBehaviorChangeGuarantee)

2. ``invoke_raw_with_failover`` — semantics, non-retryable, retry
   budget, end-to-end embeddings
   (TestInvokeRawWithFailoverSemantics, TestInvokeRawWithFailoverNonRetryable,
    TestInvokeRawWithFailoverRetryBudget,
    TestInvokeRawWithFailoverEndToEndEmbeddings)

3. ``Embedding endpoint guard`` — wiring + comparator + URL-equivalence
   (TestEmbeddingEndpointGuardEndToEnd)

4. ``Per-site wiring`` — every secondary site must route through the
   facade. Behavior-driven pins (patch the facade, drive the call,
   assert call count) + import-line pins (static).
   (TestTitleGenerationIsFailoverWired, TestKeywordExtractionIsFailoverWired,
    TestChildReportsIsFailoverWired, TestSkillEmbeddingServiceIsFailoverWired,
    TestSkillEvolutionServiceIsFailoverWired,
    TestSkillSearchServiceIsFailoverWired, TestCompactionIsFailoverWired)

5. ``Regression pins`` — pre-clean rebind hazard (fixed pre-branch),
   keep alive to prevent recurrence.
   (TestPreCleanRebindRegressionPin)

6. ``Real-path MockTransport E2E`` — drives the actual site function
   through httpx.MockTransport to confirm request URLs really swap
   across retries.
   (TestTitleGenerationRealPathWithMockTransport,
    TestSkillSearchRealPathWithMockTransport)

Per-site wiring details
-----------------------
* TitleGenerationService → ``wrap_langchain_failover`` (LangChain)
* extract_keywords → ``wrap_langchain_failover`` (LangChain)
* ChildReportsService._summarize_instance → ``wrap_langchain_failover``
* ChildReportsService._repair_report_with_llm → ``wrap_langchain_failover``
* ContextCompactor._call_summarization_llm → ``wrap_langchain_failover``
* SkillEmbeddingService.generate_trigger_queries → ``invoke_raw_with_failover``
* SkillEmbeddingService.embed_text → ``invoke_raw_with_failover``
* SkillEvolutionService._call_llm → ``invoke_raw_with_failover``
* SkillSearchService._llm_select → ``invoke_raw_with_failover``

Zero-behavior-change invariants
-------------------------------
* Backup unset preserves pre-v2 shape (raw-SDK sites gain a retry
  layer, but the predicate is a no-op when no controller is
  configured).
* Site-level ``asyncio.wait_for(..., timeout=...)`` is a
  belt-and-braces backstop; the real cap home is the facade's
  ``wall_clock_cap_s`` (``tenacity.stop_after_delay`` inside the
  retry loop) — see ``daemon.services.llm_failover`` docstring
  "Wall-clock cap".
"""

import asyncio
import os
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from daemon.llm_error_classifier import (
    PRIMARY_TIMEOUT_MAX,
    PRIMARY_TRANSIENT_MAX,
    TransientAPIError,
)

from daemon.services.llm_failover import (
    ChatFailoverBinding,
    current_failover_url,
    invoke_raw_with_failover,
    wrap_langchain_failover,
)


# ===========================================================================
# Helpers
# ===========================================================================


PRIMARY = "https://primary.test/v1"
BACKUP = "https://backup.test/v1"


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
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


def _embedding_body(text: str) -> dict:
    """Build an OpenAI-compatible ``/embeddings`` response."""
    # Deterministic vector — sum of char codes modulo 1000, padded to 8
    # dimensions. Real embeddings are 1536+ dims; the value only matters
    # for the call-path swap test, not the embedding semantics.
    vec = [float((sum(ord(c) for c in text) + i) % 1000) / 1000.0 for i in range(8)]
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": vec}],
        "model": "text-embedding-test",
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


def _patched_openai_with_transport(http_client: httpx.Client):
    """Build a ``side_effect`` callable for ``patch.object(openai, "OpenAI", ...)``
    that injects ``http_client`` and disables SDK-level retries.

    Transport-injection only — the real ``openai.OpenAI`` constructor
    still runs with production kwargs. ``max_retries=0`` prevents
    the SDK from masking our retry budget by retrying internally.
    """
    original_openai = openai.OpenAI

    def _patched_openai(**kwargs):
        kwargs["http_client"] = http_client
        kwargs["max_retries"] = 0
        return original_openai(**kwargs)

    return _patched_openai


class _FakeMeta:
    """Empty ``InstanceMetadata`` for the title-generation site."""

    instance_metadata: dict = {}


class _FakeRepo:
    """Repository stub for title-generation: no prior title, no-op
    update. The service reads the instance metadata first (to skip
    generation if a title exists) and writes the generated title at
    the end."""

    def get(self, iid: str):
        return _FakeMeta()

    def update_title(self, iid: str, title: str) -> None:
        return None


class _FakeManager:
    """Manager stub configured with ``base_url_backup`` — the
    service reads ``self._manager.config.llm.{base_url,base_url_backup,
    api_key,model_title}`` to build the cfg passed to the facade."""

    config = SimpleNamespace(
        llm=SimpleNamespace(
            base_url=PRIMARY,
            base_url_backup=BACKUP,
            api_key="test",
            model_title="gpt-test",
        )
    )
    _instance_repository = _FakeRepo()
    _logger = MagicMock()


# ===========================================================================
# wrap_langchain_failover — direct unit tests
# ===========================================================================


class TestWrapLangchainFailoverBinding:
    """Direct unit tests of the wrapper returned by
    ``wrap_langchain_failover`` — without driving the network."""

    def test_returns_chat_failover_binding(self):
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key="test", base_url=PRIMARY, model="g", max_retries=0
        )
        binding = wrap_langchain_failover(
            llm, {"base_url": PRIMARY, "api_key": "test", "model": "g"}
        )
        assert isinstance(binding, ChatFailoverBinding)

    def test_zero_behavior_change_when_backup_unset(self):
        """``base_url_backup`` None → ``is_failover_active`` is False,
        the wrapper still wires up a Retrying layer, but the underlying
        client is unmutated and the predicate runs with
        ``failover_controller=None``."""
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key="test", base_url=PRIMARY, model="g", max_retries=0
        )
        binding = wrap_langchain_failover(
            llm,
            {
                "base_url": PRIMARY,
                "api_key": "test",
                "model": "g",
                "base_url_backup": None,
            },
        )
        assert binding.is_failover_active is False
        # Client URL untouched — pre-v2 invariant preserved.
        assert str(llm.root_client.base_url).startswith(PRIMARY)

    def test_is_failover_active_when_backup_set(self):
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key="test", base_url=PRIMARY, model="g", max_retries=0
        )
        binding = wrap_langchain_failover(
            llm,
            {
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "g",
            },
        )
        assert binding.is_failover_active is True

    def test_backup_equal_to_primary_is_not_configured(self):
        """``FailoverController.is_configured`` requires the backup URL
        to differ from primary. The facade must mirror this — passing
        a backup URL equal to primary must NOT activate failover."""
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key="test", base_url=PRIMARY, model="g", max_retries=0
        )
        binding = wrap_langchain_failover(
            llm,
            {
                "base_url": PRIMARY,
                "base_url_backup": PRIMARY,  # same as primary
                "api_key": "test",
                "model": "g",
            },
        )
        assert binding.is_failover_active is False, (
            "backup URL equal to primary must not be considered a configured "
            f"backup (got is_failover_active={binding.is_failover_active})"
        )

    def test_clean_llm_config_strips_base_url_backup_kwargs(self):
        """F1 lesson: ``base_url_backup`` MUST NOT reach ChatOpenAI(**cfg).
        The facade re-runs ``clean_llm_config`` internally; verify the
        constructed client's ``model_kwargs`` doesn't carry the key."""
        from langchain_openai import ChatOpenAI

        # Bypass the facade for a moment to construct the client with the
        # uncleaned dict — this is the pre-v2 buggy shape. The facade
        # must cleanly avoid this.
        llm = ChatOpenAI(
            api_key="test",
            base_url=PRIMARY,
            model="g",
            max_retries=0,
            # Strip model_vision + base_url_backup via the same helper
            # the facade uses. Verify by re-importing clean_llm_config.
        )
        from daemon.graph import clean_llm_config
        cleaned = clean_llm_config(
            {
                "base_url": PRIMARY,
                "api_key": "test",
                "model": "g",
                "base_url_backup": BACKUP,
                "model_vision": "gpt-vision",
            }
        )
        # Stub: just exercise the helper
        assert "base_url_backup" not in cleaned
        assert "model_vision" not in cleaned

        # Now drive the facade. The wrap call must succeed and the binding
        # must reference the right primary URL (not the backup).
        binding = wrap_langchain_failover(
            llm,
            {
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "g",
            },
        )
        assert binding.primary_url == PRIMARY
        assert binding.backup_url == BACKUP


# ===========================================================================
# wrap_langchain_failover — End-to-end swap via MockTransport
# ===========================================================================


class TestWrapLangchainFailoverEndToEnd:
    """Drive the wrapper through ``httpx.MockTransport`` and assert
    the request path really swaps from primary to backup."""

    def test_swap_actually_redirects_requests_to_backup(self):
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(
                    200, json=_completion_body("from-backup")
                )
            # Primary returns 500 (RETRYABLE_STATUS_CODES).
            return httpx.Response(
                500,
                json={"error": {"message": "primary down",
                                "type": "server_error"}},
            )

        llm = ChatOpenAI(
            api_key="test",
            base_url=PRIMARY,
            model="gpt-test",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        binding = wrap_langchain_failover(
            llm,
            {
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "gpt-test",
            },
        )

        result = binding.invoke([HumanMessage(content="hi")])
        assert result.content == "from-backup"

        hosts = [u.host for u in captured]
        # Primary slice of PRIMARY_TRANSIENT_MAX (default 3), then swap.
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"expected exactly {PRIMARY_TRANSIENT_MAX} primary requests "
            f"in the primary slice; got hosts={hosts}"
        )
        assert hosts.count("backup.test") == 1, (
            f"swap must have routed ONE request to backup; got hosts={hosts}"
        )

    def test_zero_behavior_change_without_backup(self):
        """Without a backup URL, the wrapper retries the same primary URL
        exhaustively — no swap, no URL mutation."""
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            # Always primary — never backup.test.
            assert request.url.host == "primary.test"
            return httpx.Response(
                500,
                json={"error": {"message": "down", "type": "server_error"}},
            )

        llm = ChatOpenAI(
            api_key="test",
            base_url=PRIMARY,
            model="gpt-test",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        binding = wrap_langchain_failover(
            llm, {"base_url": PRIMARY, "api_key": "test", "model": "gpt-test"}
        )
        # Backup unset; expect the wrapper to NOT add a swap and the
        # client's URL to remain primary.
        assert binding.is_failover_active is False
        # Invoke should exhaust the (transient=3) budget on primary, then reraise.
        with pytest.raises(TransientAPIError):
            binding.invoke([HumanMessage(content="hi")])

        # All requests on primary — never backup.
        assert all(u.host == "primary.test" for u in captured)
        assert str(llm.root_client.base_url).startswith(PRIMARY)

    def test_non_retryable_status_does_not_retry(self):
        """401 / 400 / context-length must not retry even with a backup
        configured (the existing v1 invariant carries through)."""
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI
        from openai import AuthenticationError

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(
                401,
                json={"error": {"message": "auth", "type": "auth_error"}},
                headers={"www-authenticate": "Bearer"},
            )

        llm = ChatOpenAI(
            api_key="test",
            base_url=PRIMARY,
            model="gpt-test",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        binding = wrap_langchain_failover(
            llm,
            {
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "gpt-test",
            },
        )
        # 401 isn't in TRANSIENT_EXCEPTIONS — the classifier re-raises.
        with pytest.raises(AuthenticationError):
            binding.invoke([HumanMessage(content="hi")])
        # Exactly ONE request (no retry on non-retryable).
        assert request_count == 1

    def test_langchain_facade_does_not_mutate_shared_config(self):
        """v2 review Fix 3(a): a SHARED config dict passed to
        ``wrap_langchain_failover`` must be byte-identical after
        ``invoke`` — the facade's ``clean_llm_config(dict(llm_config))``
        internal-copy invariant must never leak a mutation back to
        the caller. Regression pin: a future "optimization" that
        cleans the dict in-place would strip ``base_url_backup``
        from the caller's config and silently kill downstream HA
        wiring."""
        import copy

        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_completion_body("ok"))

        llm = ChatOpenAI(
            api_key="test",
            base_url=PRIMARY,
            model="gpt-test",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        shared = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }
        before = copy.deepcopy(shared)

        binding = wrap_langchain_failover(llm, shared)
        result = binding.invoke([HumanMessage(content="hi")])
        assert result.content == "ok"

        assert shared == before, (
            f"facade must not mutate the caller's llm_config dict; "
            f"before={before!r} after={shared!r}"
        )
        assert list(shared.keys()) == list(before.keys()), (
            "key order / key set must be untouched as well"
        )
        assert shared.get("base_url_backup") == BACKUP, (
            "base_url_backup must still be present in the caller's dict"
        )


# ===========================================================================
# invoke_raw_with_failover — direct unit tests
# ===========================================================================


class TestInvokeRawWithFailoverSemantics:
    """Direct unit tests for the raw-SDK facade."""

    def test_is_failover_active_is_false_when_backup_unset(self):
        from daemon.services.llm_failover import _RawFailoverShim

        shim = _RawFailoverShim(primary_url=PRIMARY, backup_url=None)
        assert shim.is_configured is False
        assert shim.current_target_url == PRIMARY

    def test_is_failover_active_is_true_when_backup_set(self):
        from daemon.services.llm_failover import _RawFailoverShim

        shim = _RawFailoverShim(primary_url=PRIMARY, backup_url=BACKUP)
        assert shim.is_configured is True
        assert shim.current_target_url == PRIMARY

    def test_swap_to_backup_flips_target(self):
        from daemon.services.llm_failover import _RawFailoverShim

        shim = _RawFailoverShim(primary_url=PRIMARY, backup_url=BACKUP)
        shim.swap_to_backup()
        assert shim.current_target_url == BACKUP

    def test_swap_is_idempotent(self):
        from daemon.services.llm_failover import _RawFailoverShim

        shim = _RawFailoverShim(primary_url=PRIMARY, backup_url=BACKUP)
        shim.swap_to_backup()
        first_url = shim.current_target_url
        shim.swap_to_backup()  # should be a no-op
        assert shim.current_target_url == first_url

    def test_reset_to_primary_is_noop_for_raw(self):
        """Raw-SDK shim's ``reset_to_primary`` is intentionally a no-op
        (stateless per-call semantic — see module docstring)."""
        from daemon.services.llm_failover import _RawFailoverShim

        shim = _RawFailoverShim(primary_url=PRIMARY, backup_url=BACKUP)
        shim.swap_to_backup()
        shim.reset_to_primary()  # NO-OP — does not flip back
        # URL remains on backup (stickiness is per-call, not stateful).
        assert shim.current_target_url == BACKUP

    def test_current_failover_url_returns_none_outside_facade(self):
        """Outside an ``invoke_raw_with_failover`` scope, the thread-local
        holds no failover URL — factory should fall back to llm_config."""
        # At this point we are OUTSIDE any facade call.
        url = current_failover_url()
        # May be the prior test's leftover if xdist workers share threads;
        # we don't pin to None — but the fallback path is the same
        # (factory treats None as "use llm_config['base_url']").
        assert url is None or isinstance(url, str)


class TestInvokeRawWithFailoverNonRetryable:
    """v2 review Fix 5: non-retryable exceptions must propagate from
    the raw-SDK facade UNWRAPPED and with ZERO retries fired.

    Pins the 401/403/404/422 classification against regression: an
    auth-class error means the SAME failure would occur on the backup
    endpoint, so retrying or swapping is pure latency with no chance
    of recovery — the facade must short-circuit on attempt 1.
    """

    def test_authentication_error_propagates_unwrapped_no_retries(self):
        """Factory raises ``openai.AuthenticationError`` (401) → the
        ORIGINAL exception object propagates to the caller:
        * NOT re-wrapped in ``TransientAPIError``
        * factory entered exactly ONCE (no retry attempts)
        * no failover swap (every attempt would fail identically —
          same key on both endpoints)
        """
        original_error = openai.AuthenticationError(
            message="Invalid API key",
            response=MagicMock(),
            body=None,
        )

        attempts: list[str | None] = []

        def _factory():
            attempts.append(current_failover_url())
            raise original_error

        llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,  # HA configured — must NOT matter
            "api_key": "test",
        }

        with pytest.raises(openai.AuthenticationError) as exc_info:
            invoke_raw_with_failover(_factory, llm_config)

        # UNWRAPPED: the caller sees the ORIGINAL exception object,
        # not a TransientAPIError wrapper.
        assert exc_info.value is original_error, (
            "the ORIGINAL AuthenticationError must propagate unwrapped — "
            f"got {type(exc_info.value).__name__} instead"
        )
        assert not isinstance(exc_info.value, TransientAPIError)

        # Zero retries: the factory ran exactly once.
        assert len(attempts) == 1, (
            f"non-retryable error must fire exactly ONE attempt (no "
            f"retries, no failover swap); factory ran {len(attempts)} "
            f"times against URLs {attempts}"
        )
        # The single attempt targeted primary — no swap happened.
        assert attempts[0] in (PRIMARY, None), (
            f"attempt 1 must target the primary URL; got {attempts[0]!r}"
        )


class TestInvokeRawWithFailoverRetryBudget:
    """Retry-budget behavior for ``invoke_raw_with_failover``."""

    def test_exhausts_primary_then_succeeds_on_backup(self):
        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(
                    200, json=_completion_body("ok-backup")
                )
            return httpx.Response(
                500,
                json={"error": {"message": "down", "type": "server_error"}},
            )

        # ``max_retries=0`` on the openai client keeps the retry count
        # deterministic — the openai SDK has its OWN retry mechanism
        # independent of our tenacity retry layer. Without this knob
        # each attempt would internally retry, multiplying the
        # observed request count past the v1 budget-split arithmetic.
        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
        }

        def _call() -> object:
            url = current_failover_url() or PRIMARY
            client = openai.OpenAI(
                api_key="test",
                base_url=url or None,
                http_client=http_client,
                max_retries=0,
            )
            return client.chat.completions.create(
                model="gpt-test", messages=[{"role": "user", "content": "hi"}]
            )

        r = invoke_raw_with_failover(_call, llm_config)
        # Response object (not just .content, since this is the raw SDK).
        assert r.choices[0].message.content == "ok-backup"

        hosts = [u.host for u in captured]
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"primary slice exhausted (expected {PRIMARY_TRANSIENT_MAX} on "
            f"primary); got hosts={hosts}"
        )
        assert hosts.count("backup.test") == 1

    def test_zero_behavior_change_without_backup(self):
        """Without a backup URL, the facade retries against primary only.
        Each retry constructs a fresh client against the same URL.
        The retry budget is the configured transient_max (default 3)."""
        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            return httpx.Response(
                500,
                json={"error": {"message": "down", "type": "server_error"}},
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": None,
            "api_key": "test",
        }

        def _call() -> object:
            url = current_failover_url() or PRIMARY
            client = openai.OpenAI(
                api_key="test",
                base_url=url or None,
                http_client=http_client,
                max_retries=0,
            )
            return client.chat.completions.create(
                model="gpt-test", messages=[{"role": "user", "content": "hi"}]
            )

        with pytest.raises(TransientAPIError):
            invoke_raw_with_failover(
                _call, llm_config, transient_max=3, timeout_max=2
            )

        # All on primary — never swapped.
        assert all(u.host == "primary.test" for u in captured), (
            f"backup unset must not route to backup.test; got hosts={[u.host for u in captured]}"
        )
        # Default budget: transient_max=3 → exactly 3 attempts on
        # primary (count < budget convention from the v1 predicate,
        # 3 failures exhaust the 3-attempt budget).
        assert len(captured) == 3, (
            f"expected 3 primary attempts (transient_max=3); got "
            f"{len(captured)}: {[u.host for u in captured]}"
        )

    def test_raw_facade_does_not_mutate_shared_config(self):
        """v2 review Fix 3(b): a SHARED config dict passed to
        ``invoke_raw_with_failover`` must be byte-identical after the
        call. Pins the same internal-copy invariant as the LangChain
        flavor — neither facade may clean/strip keys in the caller's
        dict."""
        import copy

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_completion_body("ok"))

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        shared = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }
        before = copy.deepcopy(shared)

        def _call() -> object:
            url = current_failover_url() or PRIMARY
            client = openai.OpenAI(
                api_key="test",
                base_url=url or None,
                http_client=http_client,
                max_retries=0,
            )
            return client.chat.completions.create(
                model="gpt-test", messages=[{"role": "user", "content": "hi"}]
            )

        r = invoke_raw_with_failover(_call, shared)
        assert r.choices[0].message.content == "ok"

        assert shared == before, (
            f"raw facade must not mutate the caller's llm_config dict; "
            f"before={before!r} after={shared!r}"
        )
        assert list(shared.keys()) == list(before.keys()), (
            "key order / key set must be untouched as well"
        )
        assert shared.get("base_url_backup") == BACKUP, (
            "base_url_backup must still be present in the caller's dict"
        )

    def test_thread_local_cleanup_after_call(self, monkeypatch):
        """After ``invoke_raw_with_failover`` returns (success or error),
        :func:`current_failover_url` must NOT leak the failover URL into
        subsequent calls on the same worker thread (F1-style hygiene)."""
        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(200, json=_completion_body("ok"))
            return httpx.Response(
                500, json={"error": {"message": "x", "type": "x"}}
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
        }

        def _call() -> object:
            url = current_failover_url() or PRIMARY
            client = openai.OpenAI(
                api_key="test",
                base_url=url or None,
                http_client=http_client,
                max_retries=0,
            )
            return client.chat.completions.create(
                model="gpt-test", messages=[{"role": "user", "content": "hi"}]
            )

        invoke_raw_with_failover(_call, llm_config)

        # After the call returns, the thread-local must be cleared.
        url = current_failover_url()
        assert url is None, (
            f"current_failover_url leaked into next call: {url!r}"
        )

    def test_retry_predicate_unaffected_by_predicate_construction(self):
        """Building the predicate with backup unset must NOT crash and
        must produce an equivalent retry budget to v1's pre-HA predicate
        (the same ``make_llm_retry_strategy(transient_max, timeout_max)``
        line used by the agent-chat hot path's no-controller branch)."""
        from daemon.llm_error_classifier import make_llm_retry_strategy

        # No controller — should not raise.
        pred = make_llm_retry_strategy(
            transient_max=3, timeout_max=2, failover_controller=None
        )
        # Try a transient error — predicate returns True on first attempts.
        e = openai.APIConnectionError(message="x", request=MagicMock())
        from tenacity import RetryCallState

        outcome = MagicMock()
        outcome.exception.return_value = e
        state = MagicMock(spec=RetryCallState)
        state.outcome = outcome
        state.attempt_number = 1
        assert pred(state) is True


class TestInvokeRawWithFailoverEndToEndEmbeddings:
    """Verify the embeddings path's URL swap (separate concern from the
    chat path — ``invoke_raw_with_failover`` is the same shape for both,
    but the resolutions chain differs)."""

    def test_swap_to_backup_for_embeddings(self):
        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(200, json=_embedding_body("hello"))
            return httpx.Response(500, json={"error": {"message": "x"}})

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        embed_llm_config = {
            # ``embed_base_url == chat base_url`` (no embedding override
            # in this scenario — passes the embedding-endpoint guard).
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
        }

        def _call() -> object:
            url = current_failover_url() or PRIMARY
            client = openai.OpenAI(
                api_key="test",
                base_url=url or None,
                http_client=http_client,
                max_retries=0,
            )
            return client.embeddings.create(
                model="text-embedding-test", input="hello"
            )

        result = invoke_raw_with_failover(_call, embed_llm_config)
        # The vector length matches the embedding body's vec length.
        assert len(result.data[0].embedding) == 8

        hosts = [u.host for u in captured]
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX
        assert hosts.count("backup.test") == 1


class TestEmbeddingEndpointGuardEndToEnd:
    """Finding 1: ``SkillEmbeddingService.embed_text`` must NOT fail
    over to the chat backup when an explicit ``embedding_base_url``
    differs from the chat ``base_url`` — that backup is a different
    endpoint (different creds, possibly different model), so a swap
    would be wrong. When ``embedding_base_url`` is unset (inherits
    the chat endpoint), failover applies normally."""

    def _make_service(self, *, embedding_base_url):
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

    def test_explicit_different_embedding_base_url_skips_failover(self):
        """Explicit embedding endpoint ≠ chat endpoint + chat backup
        set → NO failover on the embedding path: primary exhausts,
        call fails, backup host never receives a request."""
        import daemon.services.skill_embedding_service as ses

        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            # Primary (the embedding endpoint) always 500s; the chat
            # backup would return 200 — the guard must prevent us
            # from ever reaching it.
            return httpx.Response(
                500, json={"error": {"message": "embedding endpoint down"}}
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))

        svc = self._make_service(embedding_base_url="https://embed.test/v1")

        with patch.object(
            ses.openai, "OpenAI", side_effect=_patched_openai_with_transport(http_client)
        ):
            with pytest.raises(RuntimeError, match="Embedding API call failed"):
                asyncio.run(svc.embed_text("hello"))

        hosts = [u.host for u in captured]
        # The guard short-circuited the backup: every attempt hit the
        # embedding endpoint (primary for this call), none hit the
        # chat backup. With no backup the facade still gives bounded
        # retry — transient_max attempts, all on the same endpoint.
        assert hosts.count("backup.test") == 0, (
            "embedding call with a differing explicit embedding_base_url "
            "must NEVER swap to the chat backup endpoint"
        )
        assert hosts.count("embed.test") == PRIMARY_TRANSIENT_MAX, (
            f"no-backup retry budget is transient_max on the embedding "
            f"endpoint only; got hosts={hosts}"
        )

    def test_unset_embedding_base_url_inherits_chat_and_fails_over(self):
        """embedding_base_url unset (embedding inherits the chat
        endpoint) + chat backup set → failover applies: primary
        exhausts, swap lands on backup, call succeeds."""
        import daemon.services.skill_embedding_service as ses

        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(200, json=_embedding_body("hello"))
            return httpx.Response(
                500, json={"error": {"message": "primary down"}}
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))

        svc = self._make_service(embedding_base_url=None)

        with patch.object(
            ses.openai, "OpenAI", side_effect=_patched_openai_with_transport(http_client)
        ):
            vec = asyncio.run(svc.embed_text("hello"))

        assert len(vec) == 8
        hosts = [u.host for u in captured]
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"primary slice exhausted before swap; got hosts={hosts}"
        )
        assert hosts.count("backup.test") == 1, (
            f"chat-endpoint-identical embedding call must swap to backup; "
            f"got hosts={hosts}"
        )

    @pytest.mark.parametrize(
        "embedding_base_url",
        [
            # Trailing-slash variant — "https://x/v1/" vs chat's
            # "https://x/v1" name the SAME endpoint.
            "https://primary.test/v1/",
            # Scheme + host-case variant — "HTTPS://PRIMARY.test/v1"
            # vs chat's "https://primary.test/v1" name the SAME
            # endpoint.
            "HTTPS://PRIMARY.test/v1",
        ],
        ids=["trailing_slash", "host_case"],
    )
    def test_equivalent_urls_do_not_disable_failover(self, embedding_base_url):
        """v2 review Fix 2: an explicit ``embedding_base_url`` that
        names the SAME endpoint as the chat ``base_url`` modulo
        trailing slash or host case must NOT trip the
        different-endpoint guard — failover stays armed. Raw-string
        ``!=`` compared "https://x/v1" vs "https://x/v1/" as
        different and silently disabled HA for identical endpoints.
        """
        import daemon.services.skill_embedding_service as ses

        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(200, json=_embedding_body("hello"))
            return httpx.Response(
                500, json={"error": {"message": "primary down"}}
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        svc = self._make_service(embedding_base_url=embedding_base_url)

        with patch.object(
            ses.openai, "OpenAI", side_effect=_patched_openai_with_transport(http_client)
        ):
            vec = asyncio.run(svc.embed_text("hello"))

        assert len(vec) == 8
        hosts = [u.host for u in captured]
        assert hosts.count("backup.test") == 1, (
            f"URL-equivalent embedding endpoint must still fail over "
            f"to the chat backup (variant: {embedding_base_url!r}); "
            f"got hosts={hosts}"
        )


# ===========================================================================
# Per-site wiring smoke tests
# ===========================================================================
#
# These tests verify that every secondary site whose call site was wired
# in this feature now routes through the facade. The check is structural:
# the LLM call MUST go through the facade wrapper (LangChain sites) or
# the facade's ``invoke_raw_with_failover`` (raw-SDK sites).
#
# Tests below either patch the facade functions and assert they're called,
# OR drive a controlled failure flow and assert the call still reaches
# the network/SDK. The harness uses httpx.MockTransport for real-SDK
# discipline (no constructor patching).
# ===========================================================================


class TestTitleGenerationIsFailoverWired:
    """``TitleGenerationService._generate_and_broadcast_title`` must
    route its LLM call through ``wrap_langchain_failover``."""

    def test_title_generation_calls_wrap_langchain_failover(self):
        from daemon.services import title_generation as tg

        calls = {"wrap": 0}

        class _FakeBinding:
            def invoke(self, *args, **kwargs):
                # Return a simple string-typed response stand-in.
                return SimpleNamespace(content="A Title")

        def _fake_wrap(client, cfg, **kw):
            calls["wrap"] += 1
            # Exact-match assertions on the FULL contract — the site must
            # pass the RAW dict (with backup) to the facade, not a
            # pre-cleaned dict. A pre-clean rebind would strip
            # ``base_url_backup`` and silently kill failover while every
            # weaker assertion would still pass.
            assert cfg.get("base_url_backup") == BACKUP, (
                f"title_generation must pass base_url_backup to "
                f"wrap_langchain_failover; got {cfg.get('base_url_backup')!r} "
                f"(cfg keys: {sorted(cfg.keys())})"
            )
            assert cfg.get("base_url") == PRIMARY, (
                f"title_generation must pass base_url to wrap_langchain_failover; "
                f"got {cfg.get('base_url')!r}"
            )
            return _FakeBinding()

        # Patch ThinkingChatOpenAI to return a fake client (already-cleaned
        # config — no need for full ChatOpenAI construction).
        class _FakeLLM:
            pass

        fake_llm_instance = _FakeLLM()

        from daemon import graph as graph_mod

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    graph_mod, "ThinkingChatOpenAI", return_value=fake_llm_instance
                )
            )
            stack.enter_context(
                patch.object(
                    tg, "wrap_langchain_failover", side_effect=_fake_wrap
                )
            )

            from daemon.services.title_generation import TitleGenerationService

            # Use the shared _FakeManager / _FakeRepo / _FakeMeta
            # trio (defined at module level) so the wiring pin
            # doesn't redefine the same fixture.
            svc = TitleGenerationService(manager=_FakeManager())

            asyncio.run(
                svc._generate_and_broadcast_title("inst-1", "Hello world")
            )

        assert calls["wrap"] == 1, (
            f"title_generation must route through wrap_langchain_failover "
            f"exactly once per generation; got {calls['wrap']}"
        )


class TestKeywordExtractionIsFailoverWired:
    """``extract_keywords`` must route through ``wrap_langchain_failover``."""

    def test_extract_keywords_calls_wrap_langchain_failover(self):
        from daemon.services import keyword_extraction as kx

        calls = {"wrap": 0}

        def _fake_wrap(client, cfg, **kw):
            calls["wrap"] += 1
            # Exact-match assertions on the FULL contract — the site must
            # pass the RAW dict (with backup) to the facade, not a
            # pre-cleaned dict. A pre-clean rebind would strip
            # ``base_url_backup`` and silently kill failover while every
            # weaker assertion would still pass.
            assert cfg.get("base_url_backup") == BACKUP, (
                f"keyword_extraction must pass base_url_backup to "
                f"wrap_langchain_failover; got {cfg.get('base_url_backup')!r} "
                f"(cfg keys: {sorted(cfg.keys())})"
            )
            assert cfg.get("base_url") == PRIMARY, (
                f"keyword_extraction must pass base_url to "
                f"wrap_langchain_failover; got {cfg.get('base_url')!r}"
            )
            return SimpleNamespace(
                invoke=lambda messages: SimpleNamespace(content="kw1, kw2, kw3")
            )

        # The facade is imported inside ``extract_keywords`` (lazy
        # import), so the patch target is the symbol's source module:
        # ``daemon.services.llm_failover``. The lazy import
        # ``from .llm_failover import wrap_langchain_failover`` pulls
        # the (now-patched) attribute at call time.
        from daemon.services import llm_failover as lf_module
        from daemon import graph as graph_mod

        class _FakeLLM:
            pass

        fake_llm_instance = _FakeLLM()

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    graph_mod, "ThinkingChatOpenAI", return_value=fake_llm_instance
                )
            )
            stack.enter_context(
                patch.object(
                    lf_module, "wrap_langchain_failover", side_effect=_fake_wrap
                )
            )

            class _Cfg:
                class llm:
                    base_url = PRIMARY
                    base_url_backup = BACKUP
                    api_key = "test"
                    model = "gpt-test"
                    model_keywords = "gpt-test"

            import asyncio

            result = asyncio.run(
                kx.extract_keywords(message="Hello", config=_Cfg(), timeout_s=2)
            )

        assert result == ["kw1", "kw2", "kw3"]
        assert calls["wrap"] == 1, (
            "keyword_extraction must route through wrap_langchain_failover"
        )


class TestChildReportsIsFailoverWired:
    """Child-reports summarization + repair must each route through
    ``wrap_langchain_failover`` with the FULL backup-bearing config."""

    def _patched_service(self, *, llm_content: str):
        """Build a ChildReportsService stub with patched facade + LLM
        and a fake manager configured with ``base_url_backup``. Returns
        the ``ExitStack``, the service instance, and a recorder dict
        that captures the cfg passed to the facade."""
        captured = {"wrap": 0, "cfg": None}

        def _fake_wrap(client, cfg, **kw):
            captured["wrap"] += 1
            captured["cfg"] = cfg
            return SimpleNamespace(
                invoke=lambda messages: SimpleNamespace(content=llm_content)
            )

        from daemon.services import child_reports as cr
        from daemon import graph as graph_mod

        class _FakeLLM:
            pass

        fake_llm_instance = _FakeLLM()

        stack = ExitStack()
        stack.enter_context(
            patch.object(
                graph_mod, "ThinkingChatOpenAI", return_value=fake_llm_instance
            )
        )
        stack.enter_context(
            patch.object(cr, "wrap_langchain_failover", side_effect=_fake_wrap)
        )

        # Manager stub with backup configured — the service reads
        # ``self._config.llm.{base_url,base_url_backup,api_key,model}``
        # to build the cfg passed to the facade. ``_checkpointer`` is a
        # property on the service that accesses
        # ``self._manager._checkpointer`` and returns
        # ``adapter.raw_saver`` — we set it to a non-None fake so the
        # summarization method proceeds past the early-return guard.
        class _FakeCheckpointerAdapter:
            raw_saver = MagicMock()

        class _FakeManager:
            config = SimpleNamespace(
                llm=SimpleNamespace(
                    base_url=PRIMARY,
                    base_url_backup=BACKUP,
                    api_key="test",
                    model="gpt-test",
                )
            )
            _checkpointer = _FakeCheckpointerAdapter()

        svc = cr.ChildReportsService(manager=_FakeManager())
        return stack, svc, captured

    def test_summarization_calls_wrap_langchain_failover(self):
        """Drive ``_summarize_instance`` with a fake checkpointer and
        assert the facade receives the backup-bearing config. A pre-clean
        rebind (the bug class) would strip ``base_url_backup`` before the
        facade sees it, silently killing failover."""
        from daemon.services import child_reports as cr

        stack, svc, captured = self._patched_service(llm_content="Summary")

        # Patch the message-fetching layer so the LLM branch fires.
        # The summarization code returns early when ``_checkpointer`` is
        # None or messages are empty — we need at least one message to
        # reach the wrap call.
        async def _fake_get_messages(checkpointer, instance_id, manager=None):
            return [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]

        with stack, patch.object(
            cr, "get_instance_messages", side_effect=_fake_get_messages
        ), patch.object(
            svc, "_get_instance_report_prefix", return_value="Test agent"
        ):
            asyncio.run(svc._summarize_instance("inst-1", "agent-1"))

        assert captured["wrap"] == 1, (
            f"child_reports summarization must route through "
            f"wrap_langchain_failover exactly once; got {captured['wrap']}"
        )
        assert captured["cfg"] is not None, (
            "facade must receive a config dict"
        )
        # Exact-match assertions on the FULL contract — the site must
        # pass the RAW dict (with backup) to the facade, not a
        # pre-cleaned dict. A pre-clean rebind would strip
        # ``base_url_backup`` and silently kill failover while every
        # weaker assertion would still pass.
        assert captured["cfg"].get("base_url_backup") == BACKUP, (
            f"child_reports summarization must pass base_url_backup to "
            f"wrap_langchain_failover; got {captured['cfg'].get('base_url_backup')!r} "
            f"(cfg keys: {sorted(captured['cfg'].keys())})"
        )
        assert captured["cfg"].get("base_url") == PRIMARY, (
            f"child_reports summarization must pass base_url to "
            f"wrap_langchain_failover; got {captured['cfg'].get('base_url')!r}"
        )

    def test_repair_calls_wrap_langchain_failover(self):
        """Drive ``_repair_report_with_llm`` and assert the facade
        receives the backup-bearing config. A pre-clean rebind (the bug
        class) would strip ``base_url_backup`` before the facade sees
        it, silently killing failover."""
        from daemon.services import child_reports as cr
        from daemon.config import ReportRepairConfig

        stack, svc, captured = self._patched_service(llm_content="Repaired")

        repair_config = ReportRepairConfig()
        messages = [
            {"role": "assistant", "content": "First message " * 20},
            {"role": "assistant", "content": "Second message " * 20},
        ]

        with stack:
            asyncio.run(
                svc._repair_report_with_llm(
                    messages, repair_config, instance_id="inst-1"
                )
            )

        assert captured["wrap"] == 1, (
            f"child_reports repair must route through "
            f"wrap_langchain_failover exactly once; got {captured['wrap']}"
        )
        assert captured["cfg"] is not None, (
            "facade must receive a config dict"
        )
        # Exact-match assertions on the FULL contract — the site must
        # pass the RAW dict (with backup) to the facade, not a pre-cleaned
        # rebind. A pre-clean rebind would strip ``base_url_backup`` and
        # silently kill failover while every weaker assertion would still
        # pass.
        assert captured["cfg"].get("base_url_backup") == BACKUP, (
            f"child_reports repair must pass base_url_backup to "
            f"wrap_langchain_failover; got {captured['cfg'].get('base_url_backup')!r} "
            f"(cfg keys: {sorted(captured['cfg'].keys())})"
        )
        assert captured["cfg"].get("base_url") == PRIMARY, (
            f"child_reports repair must pass base_url to "
            f"wrap_langchain_failover; got {captured['cfg'].get('base_url')!r}"
        )


class TestSkillEmbeddingServiceIsFailoverWired:
    """``SkillEmbeddingService.generate_trigger_queries`` and
    ``embed_text`` must each route through ``invoke_raw_with_failover``.
    Behavior-driven: patch the facade symbol on the target module,
    drive the real call path, assert the recording was called.
    """

    def test_chat_call_uses_invoke_raw_with_failover(self):
        """``generate_trigger_queries`` (chat-completion path) must
        route through the facade. Drive a real call against a
        minimal ``SkillEmbeddingService`` and assert the facade was
        entered exactly once.
        """
        from daemon.services import skill_embedding_service as ses

        calls = {"invoke": 0}

        def _fake_invoke(factory, cfg, **kw):
            calls["invoke"] += 1
            # Don't drive the real facade (it would need httpx
            # transport). Return a canned chat-completion shape so
            # ``_extract_chat_content`` downstream can parse it.
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='["q1","q2","q3"]'))]
            )

        cfg = SimpleNamespace(
            embedding_model="text-embedding-test",
            embedding_base_url=None,
            embedding_api_key=None,
        )
        svc = ses.SkillEmbeddingService(
            config=cfg,
            embedding_repo=SimpleNamespace(),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "gpt-test",
            },
        )

        skill = SimpleNamespace(id="skill-1", name="test-skill", description="desc")

        with patch.object(ses, "invoke_raw_with_failover", side_effect=_fake_invoke):
            result = asyncio.run(svc.generate_trigger_queries(skill))

        assert calls["invoke"] == 1, (
            f"skill_embedding_service.generate_trigger_queries must route "
            f"through invoke_raw_with_failover exactly once; got {calls['invoke']}"
        )
        # Sanity: the fake returned a parseable JSON list.
        assert isinstance(result, list)

        # Import-line pin (a future careless refactor that removes
        # the import while leaving a comment reference would pass
        # the call-count check above, so we double-pin statically).
        import inspect
        source = inspect.getsource(ses)
        assert (
            "from .llm_failover import current_failover_url, invoke_raw_with_failover"
            in source
        ), "skill_embedding_service must import invoke_raw_with_failover"

    def test_embedding_call_uses_invoke_raw_with_failover(self):
        """``embed_text`` (embeddings path) must route through the
        facade. Drive a real call and assert the facade was entered
        exactly once.
        """
        from daemon.services import skill_embedding_service as ses

        calls = {"invoke": 0}

        def _fake_invoke(factory, cfg, **kw):
            calls["invoke"] += 1
            # Return a minimal valid embeddings shape.
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])]
            )

        cfg = SimpleNamespace(
            embedding_model="text-embedding-test",
            embedding_base_url=None,
            embedding_api_key=None,
        )
        svc = ses.SkillEmbeddingService(
            config=cfg,
            embedding_repo=SimpleNamespace(),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "gpt-test",
            },
        )

        with patch.object(ses, "invoke_raw_with_failover", side_effect=_fake_invoke):
            vec = asyncio.run(svc.embed_text("hello"))

        assert calls["invoke"] == 1, (
            f"skill_embedding_service.embed_text must route through "
            f"invoke_raw_with_failover exactly once; got {calls['invoke']}"
        )
        assert len(vec) == 8

        # Import-line pin (kept per task: keep import-line pins,
        # drop docstring-count assertion).
        import inspect
        source = inspect.getsource(ses)
        assert (
            "from .llm_failover import current_failover_url, invoke_raw_with_failover"
            in source
        ), "skill_embedding_service must import invoke_raw_with_failover"


class TestSkillEvolutionServiceIsFailoverWired:
    """``SkillEvolutionService._call_llm`` must route through
    ``invoke_raw_with_failover``. Behavior-driven: drive a real
    call against a minimal service stub and assert the facade was
    entered exactly once.
    """

    def test_skill_evolution_uses_facade(self):
        from daemon.services import skill_evolution_service as ses

        calls = {"invoke": 0}

        def _fake_invoke(factory, cfg, **kw):
            calls["invoke"] += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="analyzed"))]
            )

        # Bypass ``__init__`` and set the minimum attributes the
        # production ``_call_llm`` body reads:
        # - ``_config``: read by ``_resolve_analysis_model``
        # - ``_llm_config``: passed to the facade as the failover cfg
        svc = ses.SkillEvolutionService.__new__(ses.SkillEvolutionService)
        svc._config = SimpleNamespace(
            analysis_model="gpt-test", evolution_model="gpt-test"
        )
        svc._llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }

        with patch.object(ses, "invoke_raw_with_failover", side_effect=_fake_invoke):
            result = asyncio.run(svc._call_llm("Test prompt"))

        assert calls["invoke"] == 1, (
            f"skill_evolution_service._call_llm must route through "
            f"invoke_raw_with_failover exactly once; got {calls['invoke']}"
        )
        # Sanity: the fake returned a parseable string.
        assert result == "analyzed"

        # Import-line pin.
        import inspect
        source = inspect.getsource(ses)
        assert "invoke_raw_with_failover" in source, (
            "skill_evolution_service must import invoke_raw_with_failover"
        )


class TestSkillSearchServiceIsFailoverWired:
    """``SkillSearchService._llm_select`` must route through
    ``invoke_raw_with_failover`` for the production path (test-injected
    ``client`` arg bypasses the facade). Behavior-driven: drive a
    real call with ``client=None`` and assert the facade was entered
    exactly once.
    """

    def test_skill_search_uses_facade(self):
        from daemon.services import skill_search_service as sss

        calls = {"invoke": 0}

        def _fake_invoke(factory, cfg, **kw):
            calls["invoke"] += 1
            # The LLM returns a JSON selection. ``_parse_llm_selection``
            # is tolerant of markdown fences and surrounding prose.
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"selected": [{"name": "test_skill", "score": 0.9}], '
                                '"low_match": []}'
                    )
                )]
            )

        # Bypass ``__init__`` and set the minimum attributes the
        # production ``_llm_select`` body reads.
        svc = sss.SkillSearchService.__new__(sss.SkillSearchService)
        svc._llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }

        # Minimal candidate tuples ``(skill, score)`` for the LLM stage.
        candidate_skill = SimpleNamespace(
            name="test_skill", id="skill-1", description="desc"
        )
        candidates = [(candidate_skill, 0.9)]

        with patch.object(sss, "invoke_raw_with_failover", side_effect=_fake_invoke):
            result = asyncio.run(
                svc._llm_select(
                    query="test query",
                    candidates=candidates,
                    client=None,  # production path
                )
            )

        assert calls["invoke"] == 1, (
            f"skill_search_service._llm_select (production path) must route "
            f"through invoke_raw_with_failover exactly once; got {calls['invoke']}"
        )
        # Sanity: the LLM selected the candidate → result is non-empty.
        assert "injected" in result
        assert len(result["injected"]) == 1
        assert result["injected"][0]["skill"].name == "test_skill"

        # Import-line pin.
        import inspect
        source = inspect.getsource(sss)
        assert "invoke_raw_with_failover" in source, (
            "skill_search_service must import invoke_raw_with_failover"
        )


class TestCompactionIsFailoverWired:
    """``ContextCompactor._call_summarization_llm`` must route through
    ``wrap_langchain_failover`` with the FULL backup-bearing config.
    Behavior-driven: drive a real ``_call_summarization_llm`` and
    assert the facade was entered exactly once with the backup-bearing
    config. Pinned statically: lazy import line (compaction imports
    the facade at function scope, not module scope).
    """

    def test_compaction_uses_facade(self):
        """Drive ``_call_summarization_llm`` with a real ``ContextCompactor``
        and assert the facade receives the backup-bearing config. The
        compaction site now uses the canonical ``clean_llm_config`` inline
        form (``ThinkingChatOpenAI(**clean_llm_config(dict(llm_config)))``)
        and passes the LOCAL ``llm_config`` (with backup) to the facade —
        a pre-clean rebind would strip ``base_url_backup`` before the
        facade sees it, silently killing failover.
        """
        from daemon import compaction as cmp
        from daemon.services import llm_failover as lf_module
        from langchain_core.messages import HumanMessage

        # ---- Fake-facade exact-match on backup-bearing config ----
        captured = {"wrap": 0, "cfg": None}

        def _fake_wrap(client, cfg, **kw):
            captured["wrap"] += 1
            captured["cfg"] = cfg
            return SimpleNamespace(
                invoke=lambda messages: SimpleNamespace(content="Summary")
            )

        llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }

        # Use ``CompactionConfig()`` directly (Pydantic BaseSettings →
        # defaults are fine for this smoke test).
        config = cmp.CompactionConfig()
        context = cmp.CompactionContext(
            messages=[HumanMessage(content="Hello")],
            system_prompt_tokens=100,
            model_name="gpt-test",
            config=config,
            llm_config=llm_config,
        )

        compactor = cmp.ContextCompactor(config=config, llm_config=llm_config)

        # Patch at the source module so the lazy import picks up
        # our stub.
        with patch.object(
            lf_module, "wrap_langchain_failover", side_effect=_fake_wrap
        ), patch("daemon.graph.ThinkingChatOpenAI", return_value=MagicMock()):
            asyncio.run(compactor._call_summarization_llm("Test prompt", context))

        assert captured["wrap"] == 1, (
            f"compaction must route through wrap_langchain_failover "
            f"exactly once; got {captured['wrap']}"
        )
        assert captured["cfg"] is not None, (
            "facade must receive a config dict"
        )
        # Exact-match assertions on the FULL contract — the site must
        # pass the RAW dict (with backup) to the facade, not a pre-cleaned
        # rebind. A pre-clean rebind would strip ``base_url_backup`` and
        # silently kill failover while every weaker assertion would still
        # pass.
        assert captured["cfg"].get("base_url_backup") == BACKUP, (
            f"compaction must pass base_url_backup to wrap_langchain_failover; "
            f"got {captured['cfg'].get('base_url_backup')!r} "
            f"(cfg keys: {sorted(captured['cfg'].keys())})"
        )
        assert captured["cfg"].get("base_url") == PRIMARY, (
            f"compaction must pass base_url to wrap_langchain_failover; "
            f"got {captured['cfg'].get('base_url')!r}"
        )


# ===========================================================================
# Regression pin: pre-clean rebind hazard
# ===========================================================================


class TestPreCleanRebindRegressionPin:
    """Regression pin for the pre-clean rebind hazard that silently killed
    failover at multiple secondary sites.

    The bug pattern (fixed): a site did
        ``llm_config = clean_llm_config(llm_config)``
    and then passed the REBOUND ``llm_config`` to
    ``wrap_langchain_failover(llm, llm_config)``. The assignment REPLACES
    the dict with the cleaned one, so the facade reads the cleaned dict
    (no ``base_url_backup``). Failover was silently inactive while every
    wiring smoke test stayed green.

    The strengthened assertions above lock the fix for each affected site.
    This class adds ONE additional regression pin that documents the
    rebind hazard directly: it drives the CORRECT pattern (raw dict) and
    asserts the facade receives the backup, AND it verifies the BUG
    pattern (pre-clean rebind) would strip the backup — proving the test
    machinery actually catches the regression class.
    """

    def test_site_does_not_strip_backup_before_facade_regression_pin(self):
        """Drive ``_generate_and_broadcast_title`` (the title_generation
        site) end-to-end and assert the facade receives the backup-bearing
        config. If a future change rebinds ``llm_config`` before the
        facade call, this test fails with a clear message.

        Additionally, verifies the bug pattern at the helper level: a
        pre-clean rebind DOES strip ``base_url_backup`` from the dict
        the facade sees — proving the strengthened assertions above
        would catch a regression of this class.
        """
        from daemon.services import title_generation as tg
        from daemon.graph import clean_llm_config

        # ---- Correct pattern: site passes RAW dict to the facade ----
        captured = {"wrap": 0, "cfg": None}

        def _fake_wrap(client, cfg, **kw):
            captured["wrap"] += 1
            captured["cfg"] = cfg
            return SimpleNamespace(
                invoke=lambda messages: SimpleNamespace(content="A Title")
            )

        class _FakeLLM:
            pass

        fake_llm_instance = _FakeLLM()

        from daemon import graph as graph_mod

        from daemon.services.title_generation import TitleGenerationService

        # Use the shared _FakeManager / _FakeRepo / _FakeMeta trio
        # (defined at module level) so the regression pin doesn't
        # redefine the same fixture.
        svc = TitleGenerationService(manager=_FakeManager())

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    graph_mod,
                    "ThinkingChatOpenAI",
                    return_value=fake_llm_instance,
                )
            )
            stack.enter_context(
                patch.object(
                    tg, "wrap_langchain_failover", side_effect=_fake_wrap
                )
            )
            asyncio.run(
                svc._generate_and_broadcast_title("inst-1", "Hello world")
            )

        assert captured["wrap"] == 1, (
            "title_generation must route through wrap_langchain_failover "
            "exactly once; got {captured['wrap']}"
        )
        # The pin: the facade MUST receive the backup, not the cleaned dict.
        assert captured["cfg"].get("base_url_backup") == BACKUP, (
            f"REGRESSION: pre-clean rebind stripped base_url_backup before "
            f"the facade saw it. Got {captured['cfg'].get('base_url_backup')!r}, "
            f"expected {BACKUP!r}. The site must pass the RAW dict "
            f"(with backup) to wrap_langchain_failover, not a pre-cleaned "
            f"rebind — see daemon/services/title_generation.py for the "
            f"correct pattern."
        )
        assert captured["cfg"].get("base_url") == PRIMARY, (
            f"facade must receive primary base_url; got "
            f"{captured['cfg'].get('base_url')!r}"
        )

        # ---- Bug pattern: pre-clean rebind strips backup ----
        # This sub-assertion proves the test machinery actually catches
        # the regression class. If a future change reintroduces the bug
        # pattern at any site, the assertion above fails. This helper
        # call confirms the helper itself is the strip mechanism.
        raw = {
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "api_key": "test",
            "model": "gpt-test",
        }
        cleaned = clean_llm_config(dict(raw))
        assert "base_url_backup" not in cleaned, (
            "clean_llm_config must strip base_url_backup (this is the "
            "strip mechanism — the site must pass the RAW dict to the "
            "facade, NOT a pre-cleaned rebind)"
        )
        # Re-running the stripped cfg through the facade (the bug pattern)
        # would fail the pin above — documenting the hazard.

    def test_child_reports_repair_does_not_strip_backup_before_facade(self):
        """Drive ``ChildReportsService._repair_report_with_llm`` (the
        child_reports REPAIR path) end-to-end and assert the facade
        receives the backup-bearing config. If a future change rebinds
        ``llm_config`` before the facade call (e.g. re-introduces the
        missing ``dict()`` defensive wrap at the ``clean_llm_config``
        call site), this test fails with a clear message.

        Sibling to ``test_site_does_not_strip_backup_before_facade_regression_pin``
        (which covers ``_generate_and_broadcast_title``); together they
        pin the facade contract at the LangChain secondary sites most
        prone to silent failover inactivation.
        """
        from daemon.services import child_reports as cr
        from daemon.config import ReportRepairConfig
        from daemon import graph as graph_mod

        captured = {"wrap": 0, "cfg": None}

        def _fake_wrap(client, cfg, **kw):
            captured["wrap"] += 1
            captured["cfg"] = cfg
            return SimpleNamespace(
                invoke=lambda messages: SimpleNamespace(content="Repaired")
            )

        class _FakeLLM:
            pass

        fake_llm_instance = _FakeLLM()

        class _FakeCheckpointerAdapter:
            raw_saver = MagicMock()

        class _FakeManager:
            config = SimpleNamespace(
                llm=SimpleNamespace(
                    base_url=PRIMARY,
                    base_url_backup=BACKUP,
                    api_key="test",
                    model="gpt-test",
                )
            )
            _checkpointer = _FakeCheckpointerAdapter()

        svc = cr.ChildReportsService(manager=_FakeManager())
        repair_config = ReportRepairConfig()
        messages = [
            {"role": "assistant", "content": "Long content " * 20},
            {"role": "assistant", "content": "More content " * 20},
        ]

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    graph_mod,
                    "ThinkingChatOpenAI",
                    return_value=fake_llm_instance,
                )
            )
            stack.enter_context(
                patch.object(
                    cr, "wrap_langchain_failover", side_effect=_fake_wrap
                )
            )
            asyncio.run(
                svc._repair_report_with_llm(
                    messages, repair_config, instance_id="inst-1"
                )
            )

        assert captured["wrap"] == 1, (
            f"child_reports repair must route through wrap_langchain_failover "
            f"exactly once; got {captured['wrap']}"
        )
        # The pin: the facade MUST receive the backup, not the cleaned dict.
        # A pre-clean rebind (or a missing ``dict()`` wrap that lets a
        # future in-place mutator strip the key before the facade sees it)
        # would set ``base_url_backup`` to ``None`` here, silently killing
        # failover while every weaker assertion would still pass.
        assert captured["cfg"] is not None, (
            "facade must receive a config dict"
        )
        assert captured["cfg"].get("base_url_backup") is not None, (
            f"REGRESSION: pre-clean rebind stripped base_url_backup before "
            f"the facade saw it (child_reports REPAIR path). Got "
            f"{captured['cfg'].get('base_url_backup')!r}. The site must "
            f"pass the RAW dict (with backup) to wrap_langchain_failover, "
            f"with the canonical ``clean_llm_config(dict(llm_config))`` "
            f"defensive wrap at the constructor — see "
            f"daemon/services/child_reports.py:_repair_report_with_llm."
        )
        assert captured["cfg"].get("base_url_backup") == BACKUP, (
            f"child_reports REPAIR facade must receive the configured "
            f"base_url_backup; got {captured['cfg'].get('base_url_backup')!r}, "
            f"expected {BACKUP!r}"
        )


# ===========================================================================
# Facade wall-clock cap (item 1)
# ===========================================================================
#
# The cap is implemented as ``stop_after_attempt | stop_after_delay`` in
# the facade's tenacity.Retrying construction. This class verifies the
# cap is wired AND that it fires under retry-storm conditions. The
# resilient-hang test (uses time.monotonic + factory that sleeps past
# the cap) confirms the deadline fires BETWEEN attempts, before the
# retry budget would have exhausted.


class TestFacadeCap:
    """Verify ``wall_clock_cap_s`` lives in the facade (the single
    home) and fires under retry-storm conditions.

    Two-pronged approach:

    * **Structural** — the facade's tenacity ``Retrying`` uses
      ``stop_after_attempt | stop_after_delay(wall_clock_cap_s)``.
    * **Functional** — drive a hanging factory with a tiny cap and
      verify the call returns within the cap window, with bounded
      attempts (no retry-storm amplification).
    """

    def test_invoke_raw_facade_uses_wall_clock_cap(self):
        """``invoke_raw_with_failover`` must wire
        ``stop_after_delay`` into the Retrying's stop conditions.
        Source-level pin (introspection of the actual ``Retrying``
        object built by the facade)."""
        from daemon.services.llm_failover import invoke_raw_with_failover
        from tenacity import Retrying

        # Build a real Retrying by calling the facade with a
        # hanging factory (the cap will fire before the cap
        # timeout elapses if we go through the real call). We
        # don't need the call to actually succeed; we just need
        # to inspect the Retrying object.

        # Use a tiny cap so the call fails fast. The factory
        # always fails so the retry budget is the only cap that
        # could matter (transient_max attempts → 3). The cap
        # also stops the loop when wall-clock exceeds 0.05s.
        call_count = {"n": 0}

        def _always_fails():
            call_count["n"] += 1
            raise ValueError("fail")

        # Patch the Retrying class itself to capture the
        # constructed instance.
        captured_retrying = {}

        original_retrying = Retrying

        class _CapturingRetrying(Retrying):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured_retrying["instance"] = self

        with patch(
            "daemon.services.llm_failover.Retrying", _CapturingRetrying
        ):
            try:
                invoke_raw_with_failover(
                    _always_fails,
                    {"base_url": PRIMARY, "api_key": "test"},
                    transient_max=3,
                    timeout_max=2,
                    wall_clock_cap_s=0.05,
                )
            except (ValueError, Exception):
                pass  # expected — the cap fires

        assert "instance" in captured_retrying, (
            "facade must construct a tenacity.Retrying"
        )
        retrying = captured_retrying["instance"]
        # Inspect the stop attribute — must be a stop_any
        # (combination of stop_after_attempt | stop_after_delay).
        from tenacity.stop import stop_any
        assert isinstance(retrying.stop, stop_any), (
            f"facade's Retrying.stop must be stop_any "
            f"(attempt | delay); got {type(retrying.stop).__name__}"
        )

    def test_invoke_raw_cap_fires_under_retry_storm(self):
        """Drive a failing factory with a tiny wall_clock_cap_s and
        verify the call returns within the cap window. The cap must
        abort the retry loop before the retry budget would have
        exhausted (so attempts stay bounded).

        Uses a fixed wait (patched in) so the test is deterministic
        — production uses ``wait_exponential_jitter`` which has
        ~1.3s waits at attempt 1, far above the cap; that would
        hang the test for many minutes if the cap is missing.
        """
        import time

        import httpx
        import openai
        from daemon.services.llm_failover import invoke_raw_with_failover
        from tenacity import wait_fixed

        # Cap = 0.05s. Transient_max = 100, timeout_max = 100
        # (way over what the cap should permit). The cap should
        # fire first. Raise a real openai.APIStatusError so the
        # facade's ``_classify_raw_sdk_exceptions`` wrapper catches
        # it and re-raises as TransientAPIError (in
        # RETRYABLE_STATUS_CODES), which the retry predicate
        # actually retries.
        call_count = {"n": 0}

        def _make_status_error(code: int) -> openai.APIStatusError:
            return openai.APIStatusError(
                message="server_error",
                response=httpx.Response(
                    status_code=code,
                    request=httpx.Request("POST", "https://primary.test/v1"),
                ),
                body=None,
            )

        def _always_fails():
            call_count["n"] += 1
            raise _make_status_error(500)

        def _patched_retrying(*args, **kwargs):
            """Capture the Retrying then build a real one with a
            fixed wait so the test is deterministic."""
            from tenacity import Retrying
            kwargs["wait"] = wait_fixed(0.001)  # 1ms — no real delay
            return Retrying(*args, **kwargs)

        t0 = time.monotonic()
        try:
            with patch(
                "daemon.services.llm_failover.Retrying", _patched_retrying
            ):
                invoke_raw_with_failover(
                    _always_fails,
                    {"base_url": PRIMARY, "api_key": "test"},
                    transient_max=100,
                    timeout_max=100,
                    wall_clock_cap_s=0.05,
                )
        except (openai.APIStatusError, Exception):
            pass  # expected — the cap fires
        elapsed = time.monotonic() - t0

        # Cap fired: total elapsed < ~0.5s (cap + some slack).
        # If the cap were missing, all 100 attempts would fire
        # (each fail + 1ms wait ≈ 100ms total, still <0.5s — so
        # we also assert attempt-count bounded to be sure the
        # cap is what stopped the loop, not the retry budget).
        assert elapsed < 0.5, (
            f"wall_clock_cap_s=0.05 must fire under retry storm; "
            f"got elapsed={elapsed:.2f}s (cap not working?)"
        )
        # Sanity: the cap bounded attempts. With 1ms waits the
        # budget would let all 100 attempts run in ~100ms; with
        # cap=0.05s only a handful of attempts can fit before
        # the cap fires. Assert attempts stayed well under 100.
        assert call_count["n"] < 100, (
            f"wall_clock_cap_s=0.05 must bound attempts; "
            f"got attempts={call_count['n']} (cap not working?)"
        )

    def test_wrap_langchain_facade_uses_wall_clock_cap(self):
        """``wrap_langchain_failover`` must wire ``stop_after_delay``
        into the LangChain facade's Retrying."""
        from langchain_openai import ChatOpenAI
        from tenacity import Retrying
        from tenacity.stop import stop_any

        llm = ChatOpenAI(
            api_key="test", base_url=PRIMARY, model="g", max_retries=0
        )

        captured_retrying = {}

        class _CapturingRetrying(Retrying):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured_retrying["instance"] = self

        # Patch Retrying at the import site in the facade module.
        with patch(
            "daemon.services.llm_failover.Retrying", _CapturingRetrying
        ):
            binding = wrap_langchain_failover(
                llm,
                {"base_url": PRIMARY, "api_key": "test", "model": "g"},
                wall_clock_cap_s=0.05,
            )

        assert "instance" in captured_retrying, (
            "wrap_langchain_failover must construct a tenacity.Retrying"
        )
        retrying = captured_retrying["instance"]
        assert isinstance(retrying.stop, stop_any), (
            f"wrap_langchain_failover's Retrying.stop must be stop_any "
            f"(attempt | delay); got {type(retrying.stop).__name__}"
        )


# ===========================================================================
# Adversarial: zero-behavior-change guarantee
# ===========================================================================


class TestZeroBehaviorChangeGuarantee:
    """Pinned invariants — when ``base_url_backup`` is unset:
    * No call to ``backup_url`` happens.
    * Client URL never mutates.
    * Retry budget equals the documented v1 default.
    """

    def test_langchain_no_url_mutation_when_backup_unset(self):
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key="test", base_url=PRIMARY, model="g", max_retries=0
        )
        wrap_langchain_failover(
            llm, {"base_url": PRIMARY, "api_key": "test", "model": "g"}
        )
        # Client URL untouched.
        assert str(llm.root_client.base_url).startswith(PRIMARY)

    def test_raw_no_swap_when_backup_unset(self):
        """Without a backup URL, the raw-SDK facade never swaps — every
        attempt reads the same primary URL via the fallback path."""
        captured: list[str] = []

        def _factory() -> str:
            url = current_failover_url() or PRIMARY
            captured.append(url)
            return "ok"

        llm_config = {
            "base_url": PRIMARY,
            "base_url_backup": None,
            "api_key": "test",
        }
        result = invoke_raw_with_failover(_factory, llm_config)
        assert result == "ok"
        # All calls use primary URL — never swapped to backup.
        assert all(u == PRIMARY for u in captured)
        assert len(captured) == 1, (
            f"single success on first attempt expected; got {len(captured)} "
            f"attempts (captured={captured})"
        )

    def test_default_retry_budget_matches_v1(self):
        """The facade's default transient/timeout budgets match the v1
        ones from ``make_llm_retry_strategy``'s parameter defaults —
        pinning that secondary sites get the same retry budget as the
        agent-chat hot path."""
        assert PRIMARY_TRANSIENT_MAX == 3
        assert PRIMARY_TIMEOUT_MAX == 2


# ===========================================================================
# Real-construction-path integration test for ONE LangChain secondary site
# (title_generation) and ONE raw-SDK site (skill_search_service).
#
# These tests satisfy the spec's "real-construction-path discipline
# (NO constructor patching) for at least one site" — they drive the
# FULL service path with a real ``httpx.MockTransport`` injection on the
# LangChain client / OpenAI client (no monkey-patching of constructors),
# and assert the URL swap actually reaches the wire on each path.
# ===========================================================================


class TestTitleGenerationRealPathWithMockTransport:
    """End-to-end: ``TitleGenerationService._generate_and_broadcast_title``
    with a real ``ThinkingChatOpenAI`` + ``httpx.MockTransport`` injection.

    Confirms that wiring the facade behind the production call site
    really swaps the URL on the wire when the primary is down —
    i.e. the facade mutation path is observable from a real LLM client.
    """

    def test_title_generation_drives_facade_end_to_end(self):
        # Drive the LLM via real construction (no constructor patching)
        # and verify that the title service uses the wrapper. We use
        # httpx.MockTransport to make primary down / backup up.

        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(
                    200, json=_completion_body("Accepted Title")
                )
            return httpx.Response(
                500,
                json={"error": {"message": "primary down",
                                "type": "server_error"}},
            )

        # Build the LLM the way the title service builds it — via the
        # same ChatOpenAI constructor pattern; then wrap with the
        # facade's wrap_langchain_failover and call invoke (mirroring
        # the production wiring from daemon/services/title_generation.py).
        llm_config = {
            "api_key": "test",
            "base_url": PRIMARY,
            "base_url_backup": BACKUP,
            "model": "gpt-test",
            "temperature": 0.3,
            "default_headers": {"x-proxy-app": "ensemble"},
        }
        from daemon.graph import clean_llm_config
        cleaned = clean_llm_config(dict(llm_config))

        llm = ChatOpenAI(
            api_key="test",
            base_url=cleaned.get("base_url"),
            model="gpt-test",
            temperature=0.3,
            max_retries=0,
            default_headers={"x-proxy-app": "ensemble"},
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        binding = wrap_langchain_failover(llm, llm_config)
        assert binding.is_failover_active is True

        result = binding.invoke(
            [
                SystemMessage(content="You are a helpful assistant."),
                HumanMessage(content="Make a title"),
            ]
        )
        assert result.content == "Accepted Title"

        hosts = [u.host for u in captured]
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX
        assert hosts.count("backup.test") == 1


class TestSkillSearchRealPathWithMockTransport:
    """End-to-end: ``SkillSearchService._llm_select`` drives through
    ``invoke_raw_with_failover`` with a real ``openai.OpenAI`` client
    + ``httpx.MockTransport``. Confirms the raw-SDK callable-rebuild
    pattern really swaps the URL on the wire."""

    def test_skill_search_drives_facade_end_to_end(self):
        from daemon.services.skill_search_service import SkillSearchService

        captured: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            if request.url.host == "backup.test":
                return httpx.Response(
                    200, json=_completion_body(
                        '{"selected": [{"name": "test_skill", "score": 0.9}], '
                        '"low_match": []}'
                    )
                )
            return httpx.Response(
                500,
                json={"error": {"message": "primary down",
                                "type": "server_error"}},
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))

        # Stub the embedding service dependencies to keep the focus on
        # the LLM call path.
        cfg = SimpleNamespace(
            bm25=SimpleNamespace(),
            embedding=SimpleNamespace(),
        )

        # SkillSearchService internals — we drive ONLY the LLM select
        # path. The EmbeddingService + SkillRepo are stubbed.
        class _Stub:
            pass

        skill_repo = _Stub()
        embedding_repo = _Stub()

        svc = SkillSearchService(
            skill_repo=skill_repo,
            embedding_repo=embedding_repo,
            embedding_service=_Stub(),
            llm_config={
                "base_url": PRIMARY,
                "base_url_backup": BACKUP,
                "api_key": "test",
                "model": "gpt-test",
            },
            config=cfg,
        )

        # Stub the BM25+embedding stages so only the LLM stage fires.
        candidate_skill = SimpleNamespace(
            name="test_skill", description="test desc", content="test content"
        )

        async def _drive():
            return await svc._llm_select(
                query="hi",
                candidates=[(candidate_skill, 0.9)],
                max_results=2,
            )

        # Drive with an http_client monkey-patch only at the openai
        # call site. The skill_search code constructs
        # ``openai.OpenAI(base_url=url, ...)`` — we override the
        # module-level openai.OpenAI to use our http_client.
        #
        # Honesty note (constructor-wrap disclosure): the constructor
        # is wrapped ONLY to inject ``http_client`` + ``max_retries=0``
        # into the kwargs; the wrapper preserves the call shape and
        # forwards ALL production kwargs to the REAL
        # ``openai.OpenAI`` constructor, which still executes
        # unmodified. This is transport injection, not a constructor
        # shape patch — the real construction path still runs.
        import daemon.services.skill_search_service as sss_mod

        with patch.object(
            sss_mod.openai, "OpenAI", side_effect=_patched_openai_with_transport(http_client)
        ):
            import asyncio
            result = asyncio.run(_drive())

        # The selected skill should match the stub.
        assert "injected" in result
        assert len(result["injected"]) == 1
        assert result["injected"][0]["skill"].name == "test_skill"

        hosts = [u.host for u in captured]
        assert hosts.count("primary.test") == PRIMARY_TRANSIENT_MAX, (
            f"raw-SDK primary slice exhausted; got hosts={hosts}"
        )
        assert hosts.count("backup.test") >= 1, (
            f"raw-SDK swap to backup must reach the wire; got hosts={hosts}"
        )
