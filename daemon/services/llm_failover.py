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
``make_llm_retry_strategy``, ``classify_llm_errors``, ``PRIMARY_*``
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

Restricted ``ChatFailoverBinding`` surface
------------------------------------------
The LangChain binding carries HA for ``invoke`` ONLY. The following
``ChatOpenAI`` capabilities are NOT carried by the wrapper — calling
them must go through the UNDERLYING client (``binding.client``) and
therefore BYPASSES retry+failover entirely:

* ``bind_tools`` — not proxied; tool-binding is a construction-time
  concern, do it on the raw client BEFORE wrapping.
* ``batch`` — not proxied; no HA.
* ``stream`` — not proxied; no HA.
* ``ainvoke`` — deliberately REMOVED (v2 review Fix 1). A sync
  ``tenacity.Retrying`` around a coroutine function returns the
  un-awaited coroutine — mechanically inert HA. If a future site
  needs async, it must fail loudly on the missing attribute instead
  of silently skipping retry+failover, and a genuinely async retry
  (``AsyncRetrying``) must be wired first.

A future tool-using or async site MUST NOT assume ``wrapper.<method>``
exists. Anything beyond ``invoke`` (and the ``is_failover_active``
property) is outside the facade's contract.

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
``make_llm_retry_strategy`` only treats IndexError as transient when
``failover_controller.is_configured`` is True. With no backup, an
empty-choices response re-raises to the caller's normal except-block
graceful fallback (which is what every secondary site already has).

Wall-clock cap
--------------
Both facade entry points (``wrap_langchain_failover`` and
``invoke_raw_with_failover``) accept a ``wall_clock_cap_s`` parameter
(default 45.0s). The retry layer adds a second stop condition via
``stop_after_attempt(N) | stop_after_delay(wall_clock_cap_s)`` —
retries abort when EITHER the attempt budget is exhausted OR total
wall-clock time (since the first attempt) exceeds the cap.

Backoff interaction: under active failover the retry layer runs
``max_attempts = transient_max + timeout_max + 1`` (= 6 with the
canonical defaults ``transient_max=3`` / ``timeout_max=2``), and the
wait policy is ``wait_exponential_jitter(initial=1, exp_base=2,
jitter=1)``. That schedule accumulates 5 inter-attempt waits before
the 6th attempt: ``1 + 2 + 4 + 8 + 16 = 31`` seconds minimum, with
jitter this expands to roughly ``[31, 36)`` seconds. The wall-clock
cap therefore MUST exceed the minimum-backoff sum, or the cap
truncates the final attempt in a full-failover storm — converting a
would-succeed failover into ``RetryError``. The 45.0s default leaves
~9s of slack above the jittered minimum; callers anticipating storm
exposure (multiple failover swaps on a degraded primary) can pass a
larger ``wall_clock_cap_s`` to keep the budget wider than the
backoff.

Why centralize: pre-v2 raw-SDK sites had unbounded retry (bounded
attempts × request_timeout ≈ 20 min worst case) — the openai
SDK's default request timeout is the only ceiling, and a slow
primary can stall a turn for 20+ minutes. LangChain sites got
``asyncio.wait_for(..., timeout=30)`` at the call site, but every
new site had to remember to add it. The facade's ``wall_clock_cap_s``
is the single home for the cap; a future site that forgets an
outer ``asyncio.wait_for`` still gets bounded latency.

Sync-compatible: ``stop_after_delay`` runs inside tenacity's retry
loop — pure time check, no ``asyncio`` needed. The raw-SDK path
runs inside ``asyncio.to_thread`` worker threads where
``asyncio.wait_for`` is NOT usable, so the cap MUST live in sync
code. The LangChain path has it too for symmetry (and so a
future site that bypasses the ``asyncio.wait_for`` still gets
the cap).

Caveat: ``stop_after_delay`` fires BETWEEN attempts, not mid-call.
A single request that hangs longer than the cap will still hang
until the openai client's per-request timeout (or some other
external interrupt) fires. The cap bounds the retry-storm
amplification, not the individual request. For a true mid-call
cap, the factory should set a per-request ``timeout=`` on the
``openai.OpenAI`` / LangChain client — that's a per-site
concern, not the facade's contract. Site-level
``asyncio.wait_for(..., timeout=30)`` caps at the 5 secondary
sites are DELIBERATE and untouched by this facade default — they
are per-call latency caps, the facade cap is the retry-storm
amplification bound; both layers coexist.

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
    stop_after_delay,
    wait_exponential_jitter,
)

from ..llm_error_classifier import (
    FailoverController,
    TransientAPIError,
    TransientLLMError,
    UsageLimitError,
    _matches_transient_valueerror,
    _matches_usage_limit,
    classify_llm_errors,
    classify_transient_apierror_body,
    derive_ha_attempt_ceiling,
    is_retryable_status_code,
    make_llm_retry_strategy,
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

    Non-status transient-channel parity (plan work unit 5,
    ``docs/plans/transient-channel-retry-widening.md``): bare
    ``openai.APIError`` bodies and ``ValueError`` body shapes are
    pattern-matched with the SAME helpers/lists as the hot-path
    classifier (imported — never duplicated), wrapping hits in
    ``TransientLLMError``. The facade's 45s wall-clock cap still
    bounds every secondary site regardless of retryability.
    """
    def _wrapped() -> T:
        # All non-``APIStatusError`` exceptions propagate untouched:
        # 400 BadRequestError / 401 AuthenticationError /
        # 403 PermissionDeniedError / 404 NotFoundError / 422
        # UnprocessableEntityError ARE ``APIStatusError`` subclasses —
        # they take the non-retryable re-raise above (none of those
        # statuses are in RETRYABLE_STATUS_CODES). Connection /
        # timeout errors (``APIConnectionError`` / ``APITimeoutError``)
        # are NOT APIStatusError — the explicit branches below pass
        # them straight through to the predicate's TRANSIENT_EXCEPTIONS
        # / TIMEOUT_EXCEPTIONS sets, BEFORE the bare-APIError pattern
        # branch (both are APIError subclasses — ordering preserved
        # from the hot-path classifier).
        try:
            return fn()
        except openai.APIStatusError as e:
            # ``APIStatusError`` is the umbrella for ANY non-2xx HTTP
            # status the SDK raises as an exception. This wrapper has a
            # SINGLE branch: retryable-status → TransientAPIError,
            # everything else re-raises. (No BadRequestError-first
            # ordering here — that ordering rule belongs to
            # ``classify_llm_errors``, which has a dedicated
            # context-length branch; this raw-SDK wrapper does not.)
            if is_retryable_status_code(e.status_code):
                raise TransientAPIError(e) from e
            raise  # Non-retryable status — pass through.
        except openai.APITimeoutError:
            raise  # ⊂ APIConnectionError ⊂ APIError — timeout budget, unchanged.
        except openai.APIConnectionError:
            raise  # ⊂ APIError — transient budget, unchanged.
        except openai.APIError as e:
            # Bare APIError — no status code channel. The wrap decision
            # is the SAME shared helper the hot path uses (one
            # kind-routing implementation, never duplicated); misses
            # re-raise unmodified.
            #
            # Quota-window typing FIRST (usage-limit-deferral-path W1
            # facade parity): a secondary-site quota hit surfaces as
            # UsageLimitError. The facade does NOT retry it — the
            # secondary sites' graceful-fallback ``except Exception``
            # blocks (audited: skill_search / skill_embedding /
            # skill_evolution all catch generically) still match, so
            # only the surfaced TYPE changes.
            if _matches_usage_limit(str(e)):
                raise UsageLimitError(e) from e
            wrapper = classify_transient_apierror_body(e)
            if wrapper is not None:
                raise wrapper from e
            raise
        except ValueError as e:
            # 200-body proxy error dicts / zero-chunk SSE streams.
            # Same pattern list as the hot path; genuine data bugs
            # re-raise unmodified. Quota-window typing FIRST, same as
            # the bare-APIError branch above.
            if _matches_usage_limit(str(e)):
                raise UsageLimitError(e) from e
            if _matches_transient_valueerror(str(e)):
                raise TransientLLMError("value_error_body", e) from e
            raise
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
    LangChain ChatOpenAI client. The caller invokes ``invoke``
    (sync only — see module docstring "Restricted surface") instead
    of the raw client's. Failure modes:

    * Transient / timeout / ``IndexError`` (when backup configured) →
      tenacity predicate retries with HA budget-split.
    * Auth / 400 / context-length / non-retryable status → classify
      re-raises, tenacity surfaces to the caller's ``except``.

    Sticky-on-success: same as v1. After a cycle that swapped and
    succeeded on backup, the client remains on backup until the
    next cycle's first attempt completes (and the predicate's
    ``attempt_number == 1`` reset flips it back). The retry
    predicate implementation is reused unchanged — see
    :func:`daemon.llm_error_classifier.make_llm_retry_strategy`.

    Wall-clock cap
    --------------
    The binding applies a SECOND stop condition via
    ``stop_after_attempt | stop_after_delay(wall_clock_cap_s)`` —
    retries are aborted when EITHER the attempt count is exhausted
    OR total wall-clock time (since the first attempt) exceeds the
    cap. This is the central policy for retry-storm protection; a
    future site that forgets an outer ``asyncio.wait_for`` still
    gets bounded latency. The cap is sync-compatible (tenacity's
    ``stop_after_delay`` runs inside the retry loop, no
    ``asyncio`` needed). See :func:`wrap_langchain_failover` and
    the module docstring "Wall-clock cap" for the full design.
    """

    client: Any
    primary_url: str
    backup_url: Optional[str]
    transient_max: int = 3
    timeout_max: int = 2
    wall_clock_cap_s: float = 45.0
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

        # Worst case: primary exhausts its slice for one category,
        # then backup runs the FULL ``max(transient, timeout)``.
        max_attempts = derive_ha_attempt_ceiling(
            self.transient_max,
            self.timeout_max,
            failover_active=self._is_failover_active,
        )

        predicate = make_llm_retry_strategy(
            transient_max=self.transient_max,
            timeout_max=self.timeout_max,
            failover_controller=(
                self._controller if self._is_failover_active else None
            ),
        )
        # Wall-clock cap: retries stop when EITHER ``max_attempts``
        # attempts have fired OR ``wall_clock_cap_s`` seconds have
        # elapsed since the first attempt. This is the central
        # policy for retry-storm protection; sites that forget an
        # outer ``asyncio.wait_for`` still get bounded latency. The
        # cap is sync-compatible (``stop_after_delay`` lives inside
        # tenacity's retry loop — no asyncio needed). See module
        # docstring "Wall-clock cap" for the design rationale and
        # the per-request-timeout caveat.
        self._retrying = Retrying(
            stop=stop_after_attempt(max_attempts) | stop_after_delay(self.wall_clock_cap_s),
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

        Note: there is deliberately NO ``ainvoke`` on this binding —
        the tenacity ``Retrying`` is synchronous and would return the
        un-awaited coroutine, silently bypassing retry+failover (see
        the module docstring "Restricted surface"). Async callers
        must fail loudly at ``AttributeError`` rather than silently
        skip HA.
        """
        return self._retrying(self._classified.invoke, *args, **kwargs)

    # v2 review Fix 1: the former ``ainvoke`` method was REMOVED. It
    # wrapped ``self._classified.ainvoke`` (an async callable) inside a
    # SYNCHRONOUS ``tenacity.Retrying``: ``Retrying.__call__`` returns
    # whatever the wrapped callable returns, which for a coroutine
    # function is the un-awaited coroutine object — zero retries, zero
    # failover, zero logging during exactly the outage the method
    # existed for. A future async caller must wire a genuinely async
    # retry (e.g. ``AsyncRetrying``) rather than resurrect this trap.


def wrap_langchain_failover(
    chat_client: Any,
    llm_config: dict[str, Any],
    *,
    transient_max: int = 3,
    timeout_max: int = 2,
    wall_clock_cap_s: float = 45.0,
) -> ChatFailoverBinding:
    """Wrap a LangChain ``ChatOpenAI`` with HA failover for ``invoke``.

    Reuses :class:`daemon.llm_error_classifier.FailoverController` to
    mutate the client's underlying ``openai.OpenAI`` ``base_url`` on
    swap, and the v1 retry predicate to share the budget across
    primary and backup. The wrapper's ``invoke`` returns what the
    underlying ``chat_client.invoke`` returns. ``ainvoke`` is NOT
    wrapped (see module docstring "Restricted surface").

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
        wall_clock_cap_s: Total wall-clock cap for the entire
            ``invoke`` cycle, in seconds. The retry loop stops
            when EITHER ``max_attempts`` is exhausted OR this many
            seconds have elapsed since the first attempt. Default
            45.0s — the canonical cap every secondary site
            inherits (calibrated above the
            ``wait_exponential_jitter`` minimum-backoff sum of
            ~31s for 6 attempts; see module docstring "Wall-clock
            cap" "Backoff interaction"). Pass
            ``config.timeout_seconds`` from ``ReportRepairConfig``
            (etc.) for per-site tunability. See module docstring
            "Wall-clock cap" for the design.

    Returns:
        :class:`ChatFailoverBinding` whose ``invoke`` carries
        HA retry+failover.

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
        wall_clock_cap_s=wall_clock_cap_s,
    )


# ---------------------------------------------------------------------------
# Raw-SDK facade: ``invoke_raw_with_failover``
# ---------------------------------------------------------------------------


def invoke_raw_with_failover(
    factory: Callable[[], T],
    llm_config: dict[str, Any],
    *,
    transient_max: int = 3,
    timeout_max: int = 2,
    wall_clock_cap_s: float = 45.0,
) -> T:
    """Run a raw OpenAI-SDK call factory with HA failover retry+swap.

    The ``factory`` constructs and runs ONE ``openai.OpenAI`` (or
    ``openai.AsyncOpenAI``) call, and is re-entered on every retry
    attempt. Each entry reads the current target URL via
    :func:`current_failover_url` and constructs the client against
    that URL.

    Reuses :func:`daemon.llm_error_classifier.make_llm_retry_strategy`
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
        wall_clock_cap_s: Total wall-clock cap for the entire
            factory cycle, in seconds. The retry loop stops when
            EITHER the attempt budget is exhausted OR this many
            seconds have elapsed since the first attempt. Default
            45.0s — the canonical cap every raw-SDK site
            inherits (calibrated above the
            ``wait_exponential_jitter`` minimum-backoff sum of
            ~31s for 6 attempts; see module docstring "Wall-clock
            cap" "Backoff interaction"). This is the central
            policy for retry-storm protection; a future site that
            forgets an outer ``asyncio.wait_for`` still gets
            bounded latency. The cap is sync-compatible —
            ``stop_after_delay`` lives inside tenacity's retry
            loop, no ``asyncio`` needed (raw-SDK sites run inside
            ``asyncio.to_thread`` worker threads where
            ``asyncio.wait_for`` is not usable). See module
            docstring "Wall-clock cap" for the design.

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
        predicate = make_llm_retry_strategy(
            transient_max=transient_max,
            timeout_max=timeout_max,
            failover_controller=(shim if shim.is_configured else None),
        )
        max_attempts = derive_ha_attempt_ceiling(
            transient_max,
            timeout_max,
            failover_active=shim.is_configured,
        )

        def _attempt() -> T:
            # Defensive: re-affirm the current URL in case the predicate
            # swapped mid-retry (sets _thread_local + shim state
            # together, but tenacity can race). Cheap, idempotent.
            _set_current_url(shim.current_target_url)
            return _classify_raw_sdk_exceptions(factory)()

        # Wall-clock cap: retries stop when EITHER ``max_attempts``
        # is exhausted OR ``wall_clock_cap_s`` seconds have elapsed
        # since the first attempt. This is the central policy for
        # retry-storm protection; raw-SDK sites run inside
        # ``asyncio.to_thread`` worker threads where
        # ``asyncio.wait_for`` is not usable, so the cap MUST live
        # in sync code. ``stop_after_delay`` lives inside
        # tenacity's retry loop — pure time check, no asyncio.
        # See module docstring "Wall-clock cap" for the design and
        # the per-request-timeout caveat (cap fires between
        # attempts; a single hanging request still needs a
        # per-request timeout on the openai client to interrupt
        # mid-call — that's the factory's responsibility, not
        # the facade's).
        retrying = Retrying(
            stop=stop_after_attempt(max_attempts) | stop_after_delay(wall_clock_cap_s),
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
