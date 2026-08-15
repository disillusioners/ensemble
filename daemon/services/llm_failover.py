"""Shared LLM HA failover facade (v2).

Background
----------
v1 wired ``FailoverController`` + tenacity retry-with-failover into the
agent-chat hot path (``daemon.graph.build_instance_llms`` →
``_wire_retry_and_failover``). The rest of the daemon — title
generation, keyword extraction, child-report summarization / repair,
context compaction, skill embedding chat / embeddings, skill
evolution chat, skill search chat — all construct their LLM clients
freshly per call and lack ANY retry wiring. This module plugs that
gap by exposing ONE shared facade that both flavors (LangChain
``ThinkingChatOpenAI`` / ``ChatOpenAI`` and raw ``openai.OpenAI`` /
``openai.AsyncOpenAI``) consume.

Reuse, not duplicate. The v1 machinery (``FailoverController``,
``_make_llm_retry_strategy``, ``classify_llm_errors``, ``PRIMARY_*``
constants) lives in :mod:`daemon.llm_error_classifier`. The facade
imports and reuses it; nothing here duplicates HA logic. If reuse
forces awkward coupling, refactor the shared parts UP into
``llm_error_classifier.py`` rather than copy-pasting.

Two flavors of facade
--------------------
1. :func:`wrap_langchain_failover` — for LangChain sites. Mutates a
   long-lived ``ChatOpenAI`` client's underlying ``openai.OpenAI``
   ``base_url`` via ``FailoverController`` (same mechanism as the
   agent-chat hot path). Builds a ``tenacity.Retrying`` with the
   HA budget-split predicate and wraps ``invoke``.

2. :func:`invoke_raw_with_failover` — for raw-SDK sites. Takes a
   zero-arg factory that constructs an ``openai.OpenAI`` /
   ``openai.AsyncOpenAI`` client and performs one API call. On
   primary-exhausted swap, the facade rebuilds the factory's
   URL against the backup endpoint. The factory reads the current
   URL via :func:`current_failover_url` (a thread-local updated
   per attempt).

Stateless per-call semantic (raw SDK only)
------------------------------------------
Secondary sites construct fresh clients per call — there is no
"next invoke cycle" because each call is its own cycle. Therefore:

* Every raw-SDK call STARTS on primary (no sticky-on-success
  carryover across calls).
* The factory is re-entered for every retry attempt, each time
  reading the current URL via :func:`current_failover_url`.

This is INTENTIONAL. Stickiness only pays for long-lived clients
(agent-chat hot path); background-task clients prefer always-primary
for predictable latency on the common path. See the docstrings on
``wrap_langchain_failover`` and ``invoke_raw_with_failover`` for
the per-family invariant.

Embedding endpoint guard
------------------------
Embedding calls resolve ``config.embedding_base_url`` →
``llm_config.base_url`` → ``None``. When ``embedding_base_url`` is
EXPLICITLY set (different endpoint from the chat ``base_url``), a
failover to the CHAT backup URL would be WRONG — different
endpoints, different creds, possibly different model.
``daemon.services.skill_embedding_service.embed_text`` enforces
this: when the explicit ``embedding_base_url`` differs from the
chat ``base_url`` it drops the chat backup from the failover
config, so embedding calls retry on the embedding endpoint only
and never swap. See :func:`invoke_raw_with_failover` docstring
for the per-call wiring.

Budget defaults
---------------
The same slice math as v1:

* ``transient_max`` (default 3) — primary gets ``PRIMARY_TRANSIENT_MAX=3``
  attempts, then swap; backup gets the full ``transient_max``.
* ``timeout_max`` (default 2) — primary gets ``PRIMARY_TIMEOUT_MAX=2``
  attempts, then swap; backup gets the full ``timeout_max``.
* Same trigger set: transient + timeout + IndexError (the latter ONLY
  when a backup is configured — pre-HA behavior preserved otherwise).

Exception re-wrapping on retry exhaustion (raw SDK only)
--------------------------------------------------------
After the retry budget is exhausted on the raw-SDK path, callers
receive the facade's WRAPPED exceptions (e.g.
:class:`daemon.llm_error_classifier.TransientAPIError` for a
retryable HTTP status) rather than the raw
``openai.APIStatusError`` the pre-v2 code raised. All current
call sites catch ``except Exception`` and are unaffected, but any
future caller matching specific ``openai`` exception TYPES must
account for the wrapper (``TransientAPIError`` carries the
original as ``__cause__``).

Nested calls: ``invoke_raw_with_failover`` does NOT support
nesting — the thread-local URL slot is single-depth. A factory
that itself calls ``invoke_raw_with_failover`` (directly or via a
higher-level helper) will clobber the outer call's current-URL
state. Current call graphs are flat; keep them that way.

Retry-without-backup delta: sites that lacked retry pre-v2 get
bounded retry (≤3 transient / ≤2 timeout attempts on the same
endpoint) even when backup is unset — the one intentional behavior
delta of this facade. Everything else matches pre-v2 exactly.

IndexError retry (HA-only)
--------------------------
The v1 IndexError-gated-on-backup semantic carries through unchanged:
``_make_llm_retry_strategy`` only treats IndexError as transient when
``failover_controller.is_configured`` is True. With no backup, an
empty-choices response re-raises to the caller's normal except-block
graceful fallback (which is what every secondary site already has).

F1 lesson: kwarg hygiene
------------------------
Every ``ChatOpenAI(**cfg)`` construction site MUST strip
``base_url_backup`` (and ``model_vision`` if any) before passing
kwargs — otherwise they leak into ``model_kwargs`` and crash
``Completions.create()`` at request time. The facade's LangChain
flavor reuses :func:`daemon.graph.clean_llm_config` for this.
The raw SDK uses its own dynamic kwargs so the field is never an
issue there.

Constraints (carry-over from v1)
--------------------------------
* PostgreSQL primary test DB (no SQLite-only syntax).
* All log lines tagged ``[LLM-HA]`` for greppability.
* No new dependencies — uses only the tenacity + openai SDKs the rest
  of the daemon already imports.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

import openai

from tenacity import (
    Retrying,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ..llm_error_classifier import (
    PRIMARY_TIMEOUT_MAX,
    PRIMARY_TRANSIENT_MAX,
    RETRYABLE_STATUS_CODES,
    FailoverController,
    TransientAPIError,
    _make_llm_retry_strategy,
    classify_llm_errors,
)
from ..graph import clean_llm_config

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _classify_raw_sdk_exceptions(fn: Callable[[], T]) -> Callable[[], T]:
    """Wrap a raw-SDK callable so retryable openai errors are surfaced
    as :class:`TransientAPIError` for the tenacity predicate.

    Mirrors the relevant part of :func:`daemon.llm_error_classifier.classify_llm_errors`
    for raw ``openai.OpenAI`` / ``openai.AsyncOpenAI`` calls. Without
    this wrapper the openai SDK's ``openai.InternalServerError`` /
    other ``APIStatusError`` subclasses are not in
    ``TRANSIENT_EXCEPTIONS`` — the v1 predicate only matches
    ``TransientAPIError``. The LangChain hot path is unaffected
    because :func:`classify_llm_errors` already does this conversion
    inside the LangChain pipeline; only raw-SDK sites need this
    wrapper added on top of the factory.

    Non-retryable status codes (auth, 400, etc.) re-raise unmodified.

    Connection / timeout errors (``openai.APIConnectionError``,
    ``openai.APITimeoutError``) are already in ``TRANSIENT_EXCEPTIONS``
    / ``TIMEOUT_EXCEPTIONS`` and pass through unchanged. Context-length
    errors are not special-cased here — the caller decides whether
    they're catastrophic (rare in secondary sites) or absorbs them
    via the existing ``except`` graceful-fallback block.
    """
    def _wrapped() -> T:
        try:
            return fn()
        except openai.APIStatusError as e:
            # ``APIStatusError`` is the umbrella for any non-2xx HTTP
            # status. Must come AFTER ``BadRequestError`` branch (which
            # doesn't apply for raw SDK — see below).
            if e.status_code in RETRYABLE_STATUS_CODES:
                raise TransientAPIError(e) from e
            raise  # Non-retryable status — pass through.
        # openai.BadRequestError (400) is not in RETRYABLE_STATUS_CODES,
        # so it falls through the re-raise above. Same for AuthenticationError,
        # PermissionDeniedError, NotFoundError, UnprocessableEntityError,
        # RateLimitError on non-listed 429 (already in the set), etc.
        # Connection / timeout errors already match TRANSIENT_EXCEPTIONS
        # / TIMEOUT_EXCEPTIONS — pass through.
    return _wrapped


# ---------------------------------------------------------------------------
# Per-thread "current target URL" holder for raw-SDK call factories.
#
# tenacity is synchronous and retries run on the SAME thread as the
# initial attempt. The daemon wraps secondary calls in ``asyncio.to_thread``
# so the thread executing ``invoke_raw_with_failover`` (and its retries)
# is the daemon's worker thread, not the event loop's. Thread-locals are
# the simplest mechanism that survives tenacity's retry loop without any
# contextvars plumbing — retries in this codebase happen in the same
# Python thread that started the call.
#
# Every retry resets the per-thread URL to the current attempt's target
# (primary on attempt 1, backup after a swap). The factory reads it via
# ``current_failover_url()``. Sites that use ``asyncio.to_thread`` to
# enter this facade are fine: the thread stays alive across retries.
# ---------------------------------------------------------------------------


_thread_local = threading.local()


def _set_current_url(url: Optional[str]) -> None:
    _thread_local.llm_failover_url = url


def _clear_current_url() -> None:
    # Defensive: don't leak one call's URL into a later call on the
    # same worker thread. ``_thread_local`` attributes survive across
    # unrelated functions, so without this clear the next raw-SDK
    # call on the same thread would briefly see the prior call's
    # backup URL during its first attempt.
    if hasattr(_thread_local, "llm_failover_url"):
        delattr(_thread_local, "llm_failover_url")


def current_failover_url() -> Optional[str]:
    """Return the target URL the raw-SDK factory should construct its
    client against this attempt.

    Read by raw-SDK call factories wrapped in :func:`invoke_raw_with_failover`
    to know which endpoint (``base_url``) to use on the current retry
    attempt. Resolves to:

    * The primary URL on attempt 1 (always — every fresh
      ``invoke_raw_with_failover`` call begins on primary, by design).
    * The backup URL after :class:`FailoverController` swaps to backup
      because the primary-phase retry budget was exhausted.

    Returns ``None`` if the current thread is not inside an
    ``invoke_raw_with_failover`` call (e.g. the factory was invoked
    directly, without the facade). In that case, factories should
    fall back to ``llm_config["base_url"]`` (the primary) — the
    fallback is the same behavior as the pre-v2 system.
    """
    return getattr(_thread_local, "llm_failover_url", None)


# ---------------------------------------------------------------------------
# Raw-SDK FailoverController shim.
#
# The v1 ``FailoverController`` mutates ``client.base_url`` on a long-lived
# LangChain ``ChatOpenAI``. Raw-SDK sites construct a fresh client per
# call, so there's no client to mutate — instead we track the
# "current target URL" in a per-call state and the factory reads it via
# :func:`current_failover_url`.
#
# This shim implements the same surface the v1 retry predicate calls:
# ``is_configured``, ``swap_to_backup``, ``reset_to_primary``,
# ``failover_summary``. ZERO behavior change when backup unset (same as
# the v1 invariant).
# ---------------------------------------------------------------------------


class _RawFailoverShim:
    """Per-call failover tracker for raw OpenAI SDK sites.

    Implements the controller surface the v1 retry predicate uses
    (``is_configured`` / ``swap_to_backup`` / ``reset_to_primary`` /
    ``failover_summary``) but holds the target URL in a per-call
    mutable state instead of mutating a long-lived client. The
    facade's :func:`invoke_raw_with_failover` updates
    :func:`current_failover_url` to match on every attempt.

    Two intentional differences from ``FailoverController``:

    * ``reset_to_primary`` is a NO-OP. Each raw-SDK call is its own
      invoke cycle; the predicate's ``attempt_number == 1`` reset is
      handled by re-initializing state at the top of
      :func:`invoke_raw_with_failover`. There is no cross-call
      stickiness.
    * ``swap_to_backup`` flips the tracker's URL AND the per-thread
      :func:`current_failover_url` so the next attempt's factory
      reads the backup URL.

    See module docstring "Stateless per-call semantic" for rationale.
    """

    def __init__(self, primary_url: Optional[str], backup_url: Optional[str]) -> None:
        self._primary = primary_url
        self._backup = backup_url
        self._current: Optional[str] = primary_url
        self._swapped = False

    @property
    def is_configured(self) -> bool:
        """True iff a backup URL is configured AND differs from primary.

        Mirrors ``FailoverController.is_configured`` — same truthy check.
        """
        return bool(self._backup) and self._backup != self._primary

    @property
    def current_target_url(self) -> Optional[str]:
        """URL the current attempt's factory should construct against."""
        return self._current

    def swap_to_backup(self) -> None:
        """Flip to backup. No-op if already swapped (idempotent)."""
        if self._swapped:
            return
        self._current = self._backup
        self._swapped = True
        # Mirror the swap to thread-local so the next factory invocation
        # reads the backup URL.
        _set_current_url(self._backup)
        logger.warning(
            f"[LLM-HA] secondary raw-SDK swap: "
            f"primary={self._primary} -> backup={self._backup}"
        )

    def reset_to_primary(self) -> None:
        """Reset to primary URL.

        Called by the retry predicate on ``attempt_number == 1`` — for
        raw-SDK stateless per-call semantics this is a NO-OP (the state
        was fresh on entry). Kept on the interface so the same
        predicate can target both controller flavors.
        """
        # No-op: stateless per-call — state is fresh on every invoke.
        # If the predicate races an attempt on the wrong side (shouldn't
        # happen given tenacity's synchronous retry model) the next
        # ``_set_current_url`` call inside the attempt will correct it.
        pass

    def failover_summary(self) -> str:
        """Same shape as ``FailoverController.failover_summary`` for
        uniform log lines."""
        return f"primary={self._primary} -> backup={self._backup}"


# ---------------------------------------------------------------------------
# LangChain facade: ``wrap_langchain_failover``
# ---------------------------------------------------------------------------


@dataclass
class ChatFailoverBinding:
    """Result of :func:`wrap_langchain_failover`.

    Holds the retry strategy + classification wrapper for a single
    LangChain ChatOpenAI client. The caller invokes ``invoke`` /
    ``ainvoke`` instead of the raw client's. Failure modes:

    * Transient / timeout / ``IndexError`` (when backup configured) →
      tenacity predicate retries with HA budget-split.
    * Auth / 400 / context-length / non-retryable status → classify
      re-raises, tenacity surfaces to the caller's ``except``.

    Sticky-on-success: same as v1. After a cycle that swapped and
    succeeded on backup, the client remains on backup until the
    next cycle's first attempt completes (and the predicate's
    ``attempt_number == 1`` reset flips it back). The retry
    predicate implementation is reused unchanged — see
    :func:`daemon.llm_error_classifier._make_llm_retry_strategy`.
    """

    client: Any
    primary_url: str
    backup_url: Optional[str]
    transient_max: int = 3
    timeout_max: int = 2
    _controller: FailoverController = field(init=False, repr=False)
    _retrying: Retrying = field(init=False, repr=False)
    _classified: Any = field(init=False, repr=False)
    _is_failover_active: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._controller = FailoverController(
            chat_client=self.client,
            primary_url=self.primary_url or "",
            backup_url=self.backup_url,
        )
        self._is_failover_active = self._controller.is_configured

        if self._is_failover_active:
            # Worst case: primary exhausts its slice for one category,
            # then backup runs the FULL ``max(transient, timeout)``.
            max_attempts = max(self.transient_max, self.timeout_max) + max(
                PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX
            )
        else:
            max_attempts = max(self.transient_max, self.timeout_max)

        predicate = _make_llm_retry_strategy(
            transient_max=self.transient_max,
            timeout_max=self.timeout_max,
            failover_controller=(
                self._controller if self._is_failover_active else None
            ),
        )
        self._retrying = Retrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(),
            retry=predicate,
            reraise=True,
        )
        # classify_llm_errors() converts openai.APIStatusError → TransientAPIError
        # and openai.BadRequestError (context-length) → ContextLengthExceededError
        # BEFORE tenacity sees them. Without this wrapper, the predicate's
        # TRANSIENT_EXCEPTIONS tuple (which references TransientAPIError,
        # not the bare APIStatusError) wouldn't fire on the raw SDK's
        # exception — the retry layer would silently let the error escape.
        self._classified = classify_llm_errors(self.client)

    @property
    def is_failover_active(self) -> bool:
        """True iff a backup URL was configured (HA wired up).

        False means ``base_url_backup`` was None or equal to primary —
        every call retries on the same endpoint (pre-v2 behavior).
        """
        return self._is_failover_active

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        """Invoke the underlying chat client with HA retry+failover.

        Builds the same retry pipeline as the agent-chat hot path
        (v1 ``build_instance_llms``): ``Retrying(classify(invoke))``.
        Sites that today call ``llm.invoke(messages)`` swap to
        ``wrapper.invoke(messages)``.
        """
        return self._retrying(self._classified.invoke, *args, **kwargs)

    def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        """Async variant of :meth:`invoke`.

        Uses the SAME classified wrapper and retry strategy as
        ``invoke`` — the predicate's URL swap mutates the same
        underlying ``openai.AsyncOpenAI`` client (via
        ``root_async_client``), so an async failover is consistent
        with a sync one in the same process.

        Note: secondary sites in this daemon all use sync ``invoke``
        behind ``asyncio.to_thread``. This is here for future-proofing
        and symmetry — see the comment block on
        ``classify_llm_errors`` for the upstream limitation that
        currently makes this path untested in production.
        """
        return self._retrying(self._classified.ainvoke, *args, **kwargs)


def wrap_langchain_failover(
    chat_client: Any,
    llm_config: dict,
    *,
    transient_max: int = 3,
    timeout_max: int = 2,
) -> ChatFailoverBinding:
    """Wrap a LangChain ``ChatOpenAI`` with HA failover for ``invoke`` / ``ainvoke``.

    Reuses :class:`daemon.llm_error_classifier.FailoverController` to
    mutate the client's underlying ``openai.OpenAI`` ``base_url`` on
    swap, and the v1 retry predicate to share the budget across
    primary and backup. The wrapper's ``invoke`` /
    ``ainvoke`` return what the underlying ``chat_client.invoke`` /
    ``chat_client.ainvoke`` returns.

    Args:
        chat_client: An already-constructed LangChain
            ``ChatOpenAI`` (or subclass like
            ``daemon.graph.ThinkingChatOpenAI``). Must have
            ``root_client`` and ``root_async_client`` attributes
            (LangChain's ``BaseChatOpenAI`` does — the daemon's
            subclasses inherit this).
        llm_config: LLM config dict. ``llm_config["base_url"]`` is
            the primary URL; ``llm_config.get("base_url_backup")``
            is the optional backup. Strips ``base_url_backup`` and
            ``model_vision`` via :func:`daemon.graph.clean_llm_config`
            BEFORE any client construction (F1 lesson: unknown
            kwargs reaching ``ChatOpenAI(**cfg)`` leak into
            ``model_kwargs`` and crash on the next request).
        transient_max: Operator-configured transient-retry budget
            (default 3). The same default is used by the agent-chat
            hot path — secondary sites match it for uniformity.
        timeout_max: Operator-configured timeout-retry budget
            (default 2). Same default as the agent-chat hot path.

    Returns:
        :class:`ChatFailoverBinding` whose ``invoke`` /
        ``ainvoke`` carry HA retry+failover.

    Retry is added even when ``base_url_backup`` is None or equal to
    primary: sites that had NO retry pre-v2 now get bounded retry
    (≤ ``transient_max`` transient / ``timeout_max`` timeout attempts
    against the same endpoint) — the only behavior delta this
    facade introduces. With a backup configured the full v1
    budget-split retry+failover applies; everything else is
    identical to pre-v2.
    """
    # F1: strip unknown kwargs that would corrupt model_kwargs.
    # The langchain client's ``base_url`` is the primary; we read
    # ``base_url_backup`` directly from the original dict.
    cleaned = clean_llm_config(dict(llm_config))
    base_url = cleaned.get("base_url") or llm_config.get("base_url") or ""
    base_url_backup = llm_config.get("base_url_backup")

    return ChatFailoverBinding(
        client=chat_client,
        primary_url=base_url,
        backup_url=base_url_backup,
        transient_max=transient_max,
        timeout_max=timeout_max,
    )


# ---------------------------------------------------------------------------
# Raw-SDK facade: ``invoke_raw_with_failover``
# ---------------------------------------------------------------------------


def invoke_raw_with_failover(
    factory: Callable[[], T],
    llm_config: dict,
    *,
    transient_max: int = 3,
    timeout_max: int = 2,
) -> T:
    """Run a raw OpenAI-SDK call factory with HA failover retry+swap.

    The ``factory`` constructs and runs ONE ``openai.OpenAI`` (or
    ``openai.AsyncOpenAI``) call, and is re-entered on every retry
    attempt. Each entry reads the current target URL via
    :func:`current_failover_url` and constructs the client against
    that URL.

    Reuses :func:`daemon.llm_error_classifier._make_llm_retry_strategy`
    with a :class:`_RawFailoverShim` adapter, so the predicate
    behavior (budget split, IndexError-on-backup gate, transient /
    timeout classification) is identical to the LangChain facade.

    Args:
        factory: Zero-arg callable returning the result. Typical
            shape::

                def _call():
                    url = current_failover_url() or llm_config["base_url"]
                    client = openai.OpenAI(
                        api_key=llm_config["api_key"] or "",
                        base_url=url or None,
                    )
                    return client.chat.completions.create(
                        model=...,
                        messages=[...],
                        ...,
                    )

            The factory is expected to be re-entrant (called multiple
            times, once per retry attempt). It SHOULD read
            :func:`current_failover_url` each call — passing a
            constant URL via closure breaks failover.
        llm_config: LLM config dict. ``llm_config["base_url"]`` is
            primary; ``llm_config.get("base_url_backup")`` is
            optional backup (None = zero behavior change).
        transient_max: Per-call transient retry budget (default 3,
            matches the agent-chat hot path's ``retry_config`` default
            ``transient_attempts``).
        timeout_max: Per-call timeout retry budget (default 2).

    Returns:
        The factory's return value (e.g. ``openai.types.chat.ChatCompletion``
        for chat calls, or ``openai.types.embeddings.CreateEmbeddingResponse``
        for embeddings calls).

    Raises:
        Whatever the factory raises after the retry budget is exhausted.
        Sites with graceful fallbacks (``except Exception``) keep them
        on top — the facade's retry exhausts BEFORE the caller's
        ``except`` fires.

    Embedding endpoint guard
        ~~~~~~~~~~~~~~~~~~~~~~
        If the call's true endpoint differs from the chat endpoint
        (i.e. ``config.embedding_base_url`` is explicitly set),
        the caller MUST build a separate ``llm_config`` with the
        embedding endpoint's base_url+backup. Failing over an
        embedding call to a CHAT backup URL is wrong (different
        endpoint, possibly different creds). Sites that the
        embedding path hits use the chat base_url only when no
        ``embedding_base_url`` override is set — see
        ``daemon.services.skill_embedding_service`` for the existing
        resolution chain.

    When ``base_url_backup`` is None or equal to primary, the
    predicate is built with ``failover_controller=None``, every
    attempt uses primary, and no swap ever occurs —
    ``current_failover_url()`` inside the factory returns the
    primary URL, same as the factory passing the primary URL
    explicitly. Note the one intentional delta: the call still
    gains bounded retry (≤ ``transient_max`` / ``timeout_max``
    attempts on primary) that raw-SDK sites lacked pre-v2.

    Note on exception classification: the openai SDK raises
    ``openai.InternalServerError`` etc. directly; this facade wraps
    the factory in :func:`_classify_raw_sdk_exceptions` so retryable
    statuses (``openai.APIStatusError`` with status in
    ``RETRYABLE_STATUS_CODES``) are re-raised as
    :class:`TransientAPIError` for the predicate. Without it the
    retry layer would not fire on HTTP-level errors. Connection /
    timeout errors (``APIConnectionError`` / ``APITimeoutError``) are
    already in the predicate's match sets and pass through
    unmodified. Non-retryable statuses (auth, 400, etc.) re-raise to
    the caller unchanged so the existing graceful-fallback blocks
    still trigger.
    """
    primary_url = llm_config.get("base_url")
    backup_url = llm_config.get("base_url_backup")

    shim = _RawFailoverShim(primary_url, backup_url)
    # Always initialize the thread-local to primary at start of each
    # call — replaces any prior call's lingering URL on the same worker
    # thread (see the F1-style hygiene note in the module docstring).
    _set_current_url(primary_url)
    try:
        predicate = _make_llm_retry_strategy(
            transient_max=transient_max,
            timeout_max=timeout_max,
            failover_controller=(shim if shim.is_configured else None),
        )
        if shim.is_configured:
            max_attempts = max(transient_max, timeout_max) + max(
                PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX
            )
        else:
            max_attempts = max(transient_max, timeout_max)

        def _attempt() -> T:
            # Defensive: re-affirm the current URL in case the predicate
            # swapped mid-retry (sets _thread_local + shim state
            # together, but tenacity can race). Cheap, idempotent.
            _set_current_url(shim.current_target_url)
            return _classify_raw_sdk_exceptions(factory)()

        retrying = Retrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(),
            retry=predicate,
            reraise=True,
        )
        return retrying(_attempt)
    finally:
        _clear_current_url()


__all__ = [
    "ChatFailoverBinding",
    "current_failover_url",
    "invoke_raw_with_failover",
    "wrap_langchain_failover",
]
