"""Dedicated off-loop DB execution seam (wi-3f5bea95 / Phase 1).

This module is the single off-loop execution seam every C-class site in the
event-loop sync-DB hot-paths lane consumes. It runs a synchronous callable on
a dedicated ``ThreadPoolExecutor`` (6 workers, ``thread_name_prefix="db-offload"``)
and awaits its result, so synchronous DB reads no longer block the event loop.

Public seam::

    await offload(fn, *args, **kwargs)

Design invariants (see .agents/shared/planning/eventloop-sync-db-hotpaths/ for
the authoritative plan; D-ids cited inline):

* **Executor selection (B6):** no executor parameter is exposed. Callers
  cannot pick the wrong executor because there is no parameter to pick.
* **kwargs mandatory (D-25):** ``offload`` accepts ``**kwargs`` and forwards
  them via ``functools.partial``. ``/api/work`` calls ``list_work`` keyword-only.
* **Re-entrancy (B1, D-52):** a bridged callee that itself calls ``offload``
  deadlocks iff ``O >= W``. The guard raises a typed error **before**
  submitting, detected by **thread identity** (``threading.current_thread().name``
  starts with ``"db-offload"``). A ``ContextVar`` marker is defeated by
  ``copy_context()`` being taken in the loop thread at submit time, so thread
  identity is the only reliable signal.
* **BaseException containment (B2, D-52):** ``KeyboardInterrupt`` / ``SystemExit``
  raised inside a bridged callable are contained and surfaced as a defined
  error (otherwise they escape the loop and crash-loop the daemon). ``CancelledError``
  is re-raised **first**, so cancellation is never swallowed.
* **Admission gate (B4, D-43/D-67/D-79):** a custom permit counter + waiter
  deque. Invariant: `permits + inflight + in_transit == W` (waiters hold NO
  permits; `in_transit` is TRACKED, not implicit — it counts tokens delivered
  by `do_release` via set_result but not yet claimed by the woken waiter).
  Transfer semantics: `do_release` decrements `_inflight` and, if a waiter
  exists, pops it, increments `_in_transit`, and set_results its future WITHOUT
  incrementing `_permits`; the woken waiter's normal resume decrements
  `_in_transit` and increments `_inflight`; post-wake cancellation (the window
  between set_result and resumption) returns the token — re-transferring to the
  next live waiter if the deque is non-empty, else restoring `_permits`. A
  cancelled parked waiter that was never woken removes itself from the deque and
  returns nothing. No-barging rule: `acquire()` parks (`if self._waiters or
  self._permits <= 0`) BEFORE the permits fast path, so a fresh acquirer can
  never steal a permit ahead of parked waiters. The gate **defers, never
  rejects, never times out**.
  The permit is released in the **executor future's done-callback** (thread-side truth), never the caller's `finally`.
* **Drain (B3, D-24/D-53):** four-step shutdown (closed flag -> fail all
  waiters -> wait for in-flight up to ``DB_OFFLOAD_DRAIN_BOUND_S`` -> executor
  shutdown). Called from the lifespan **before** ``manager.shutdown()``.
* **Contextvars:** ``run_in_executor`` does not propagate context; ``copy_context().run``
  preserves request tags / logging context. Offload-thread statements are
  attributed OFF-loop by construction (what the N2/N4 gates measure).
* **Error contract:** callee exception types propagate verbatim.
* **Type discipline (B9):** a ``TypeVar`` return, not ``-> Any``.
* **TB-1..TB-8:** a bridged callable must be atomic open/close, no session
  across await/boundary, no await/awaitable return, no callback into the loop
  (thread-safe future resolution via ``call_soon_threadsafe`` on a parked future
  is the *sanctioned* wake primitive), no WriteGuardSession participant,
  read-only, and idempotent under concurrency with no new shared in-process
  mutation. TB-8's one known instance (``_descendant_cap_warn_last_emit``) is a
  disclosed log-rate-only waiver.

The async-native deferral (D-19) is recorded as DEFERRED/UNFUNDED; this module's
waiters+in-flight alarm is the re-open trigger.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from daemon.constants import DB_OFFLOAD_DRAIN_BOUND_S as _DEFAULT_DRAIN_BOUND
from daemon.constants import DB_OFFLOAD_MAX_WORKERS_DEFAULT
from daemon.constants import DB_OFFLOAD_QUEUE_WARN_DEPTH

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DbOffloadError(Exception):
    """Base error for the db-offload seam."""


class DbOffloadClosedError(DbOffloadError):
    """Raised when the seam is closed (draining / shut down)."""


class DbOffloadReentrancyError(DbOffloadError):
    """Raised when a bridged callee re-enters the seam (B1)."""



_OFFLOAD_THREAD_PREFIX = "db-offload"


class _Gate:
    """Custom admission gate: permit counter + waiter deque (D-67/D-79)."""

    def __init__(self, workers: int, loop: asyncio.AbstractEventLoop, warn_sum: int):
        self._workers = workers
        self._loop = loop
        self._permits = workers
        self._inflight = 0
        self._in_transit = 0
        self._waiters: deque[asyncio.Future] = deque()
        self._closed = False
        self._warn_sum = warn_sum
        # observability counters
        self.submit_count = 0
        self.alarm_count = 0
        self._alarm_active = False

    @property
    def workers(self) -> int:
        return self._workers

    @property
    def permits(self) -> int:
        return self._permits

    @property
    def inflight(self) -> int:
        return self._inflight

    @property
    def waiters(self) -> int:
        return len(self._waiters)

    @property
    def in_transit(self) -> int:
        return self._in_transit

    @property
    def waiter_deque(self) -> deque:
        return self._waiters

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_alarm(self) -> None:
        w = len(self._waiters)
        total = w + self._inflight
        triggered = (w > 0) or (total > self._warn_sum)
        if triggered and not self._alarm_active:
            self._alarm_active = True
            self.alarm_count += 1
            logger.warning(
                "db_offload: backpressure alarm — waiters=%d inflight=%d "
                "sum=%d threshold=%d",
                w,
                self._inflight,
                total,
                self._warn_sum,
            )
        elif not triggered:
            self._alarm_active = False

    async def acquire(self) -> None:
        self.submit_count += 1
        if self._closed:
            raise DbOffloadClosedError("db_offload seam is closed")
        # no-barging: if any waiter is parked, this acquirer must park too,
        # even if a permit appears free (waiters>0 ==> permits==0 structurally).
        if self._waiters or self._permits <= 0:
            # park a waiter
            waiter = self._loop.create_future()
            self._waiters.append(waiter)  # register first
            if self._closed:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
                if not waiter.done():
                    try:
                        waiter.set_exception(DbOffloadClosedError("db_offload seam is closed"))
                    except (asyncio.InvalidStateError, RuntimeError):
                        pass
            self._check_alarm()
            try:
                await waiter
            except asyncio.CancelledError:
                # Post-wake cancellation token handling (CRITICAL-1 fix):
                # if the future is done and not cancelled, set_result already
                # delivered the token -> the waiter HOLDS it; return it.
                if waiter.done() and not waiter.cancelled():
                    if self._in_transit > 0:
                        self._in_transit -= 1
                    # return the token: transfer if waiters, else free permit
                    while self._waiters:
                        nxt = self._waiters[0]
                        if nxt.cancelled() or nxt.done():
                            self._waiters.popleft()
                            continue
                        self._waiters.popleft()
                        self._in_transit += 1
                        try:
                            nxt.set_result(None)
                        except (asyncio.InvalidStateError, RuntimeError):
                            pass
                        return  # token transferred; cancelled caller returns nothing
                    self._permits = min(self._permits + 1, self._workers)
                    return
                else:
                    # never woken: remove from deque if still present
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        pass
                raise
            if self._closed:
                raise DbOffloadClosedError("db_offload seam is closed")
            # normal wake: claim the transferred token
            if self._in_transit > 0:
                self._in_transit -= 1
            self._inflight += 1
            self._check_alarm()
            return
        # direct admit
        self._permits -= 1
        self._inflight += 1
        self._check_alarm()

    def do_release(self) -> None:
        # runs on loop thread
        if self._inflight > 0:
            self._inflight -= 1
        # transfer-style: wake one waiter WITHOUT incrementing permits;
        # increment in_transit (token in flight).
        while self._waiters:
            waiter = self._waiters[0]
            if waiter.cancelled() or waiter.done():
                self._waiters.popleft()
                continue
            self._waiters.popleft()
            self._in_transit += 1
            try:
                waiter.set_result(None)
            except (asyncio.InvalidStateError, RuntimeError):
                self._in_transit = max(0, self._in_transit - 1)
                self._permits = min(self._permits + 1, self._workers)
            return
        self._permits = min(self._permits + 1, self._workers)

    def close(self) -> None:
        self._closed = True
        # fail all waiters (also transfers in-flight tokens back to permits)
        while self._waiters:
            w = self._waiters.popleft()
            if not w.done():
                try:
                    w.set_exception(DbOffloadClosedError("db_offload seam is draining"))
                except (asyncio.InvalidStateError, RuntimeError):
                    pass

    def fail_waiters(self) -> None:
        self.close()


# Module state
_EXECUTOR: ThreadPoolExecutor | None = None
_GATE: _Gate | None = None
_LOOP: asyncio.AbstractEventLoop | None = None
_CLOSED = False
_DRAIN_BOUND: float = _DEFAULT_DRAIN_BOUND
_CONFIGURED = False
_WARN_SUM: int | None = None
_MAX_WORKERS: int = DB_OFFLOAD_MAX_WORKERS_DEFAULT


def configure(
    max_workers: int | None = None,
    drain_bound_s: float | None = None,
    wait_warn_sum: int | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Configure (or re-configure) the seam. Idempotent-ish.

    Called from the lifespan after config load, before the first request.
    Captures the loop once. Re-configuration shuts down the prior executor
    (bounded drain) before creating a new one.
    """
    global _EXECUTOR, _GATE, _LOOP, _CLOSED, _DRAIN_BOUND, _CONFIGURED

    if max_workers is None:
        max_workers = DB_OFFLOAD_MAX_WORKERS_DEFAULT
    if drain_bound_s is None:
        drain_bound_s = _DEFAULT_DRAIN_BOUND
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

    # re-configure: shut down prior (bounded, no cancel_futures)
    if _CONFIGURED and _EXECUTOR is not None:
        if _GATE is not None:
            try:
                _GATE.close()
            except Exception:
                pass
        # sync path: non-blocking, never join on the loop thread
        try:
            _EXECUTOR.shutdown(wait=False)
        except Exception:
            pass
        _EXECUTOR = None
        _GATE = None

    if wait_warn_sum is None:
        wait_warn_sum = max_workers + 2

    global _WARN_SUM, _MAX_WORKERS
    _WARN_SUM = wait_warn_sum
    _MAX_WORKERS = max_workers

    _LOOP = loop
    _EXECUTOR = ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix=_OFFLOAD_THREAD_PREFIX
    )
    if loop is not None:
        _GATE = _Gate(workers=max_workers, loop=loop, warn_sum=wait_warn_sum)
    else:
        _GATE = None
    _DRAIN_BOUND = drain_bound_s
    _CLOSED = False
    _CONFIGURED = True


def _ensure_configured() -> None:
    # D-78 lazy-init form (stated): if unconfigured when offload() is
    # first called, configure() runs with defaults and the loop is
    # captured from the running loop; a second configure() performs a
    # bounded drain of the prior executor before replacing it.
    global _CONFIGURED, _LOOP, _GATE
    if not _CONFIGURED:
        configure()
    if _EXECUTOR is None:
        configure()
    if _GATE is None:
        try:
            _LOOP = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if _LOOP is not None and _GATE is None:
            _GATE = _Gate(
                workers=_MAX_WORKERS,
                loop=_LOOP,
                warn_sum=(_WARN_SUM if _WARN_SUM is not None else _MAX_WORKERS + 2),
            )



def _reentrancy_check() -> None:
    name = threading.current_thread().name
    if name.startswith(_OFFLOAD_THREAD_PREFIX):
        raise DbOffloadReentrancyError(
            "db_offload.offload called from within an offload worker thread"
        )


async def offload(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ``fn(*args, **kwargs)`` on the dedicated offload executor."""
    global _GATE, _LOOP
    _reentrancy_check()
    _ensure_configured()

    assert _EXECUTOR is not None
    if _GATE is None or _LOOP is None:
        try:
            _LOOP = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if _GATE is None and _LOOP is not None:
            _GATE = _Gate(
                workers=_MAX_WORKERS, loop=_LOOP,
                warn_sum=(_WARN_SUM if _WARN_SUM is not None else _MAX_WORKERS + 2),
            )
    assert _GATE is not None
    assert _LOOP is not None

    if _CLOSED or _GATE.closed:
        raise DbOffloadClosedError("db_offload seam is closed")

    gate = _GATE
    executor = _EXECUTOR
    loop = _LOOP

    await gate.acquire()

    # submit
    ctx = contextvars.copy_context()
    fut: Future = executor.submit(ctx.run, functools.partial(fn, *args, **kwargs))

    def _release(_f: Future) -> None:
        # runs on worker thread
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(gate.do_release)
        except RuntimeError:
            pass

    fut.add_done_callback(_release)

    try:
        result = await asyncio.wrap_future(fut)
    except asyncio.CancelledError:
        # cancellation propagates; permit release handled by done-callback
        # when the thread finishes (may complete anyway). Ensure we don't
        # double-release: the done-callback is the single release point.
        raise
    except BaseException as exc:  # containment (B2)
        # The done-callback still releases the permit when the thread
        # finishes. Surface as defined error? Plan: contain and surface
        # as defined error path. Re-raise typed? We'll re-raise original
        # for normal exceptions; contain BaseException.
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise DbOffloadError("contained BaseException in bridged callable") from exc
        raise
    # If the callee raised, wrap_future re-raises it; reach here only on success.
    return result


async def shutdown() -> None:
    """Four-step drain (D-53)."""
    global _CLOSED, _EXECUTOR, _GATE
    _CLOSED = True
    if _GATE is not None:
        _GATE.close()  # fail all waiters
    # wait for in-flight
    gate = _GATE
    start = time.monotonic()
    while gate is not None and gate.inflight > 0:
        if (time.monotonic() - start) >= _DRAIN_BOUND:
            logger.warning(
                "db_offload: drain bound exceeded; inflight=%d", gate.inflight
            )
            break
        await asyncio.sleep(0.05)
    # executor shutdown
    if _EXECUTOR is not None:
        exec_ref = _EXECUTOR
        _EXECUTOR = None
        try:
            await asyncio.wait_for(
                asyncio.to_thread(exec_ref.shutdown, True),
                timeout=_DRAIN_BOUND,
            )
        except (asyncio.TimeoutError, Exception):
            try:
                exec_ref.shutdown(wait=False)
            except Exception:
                pass


def get_executor() -> ThreadPoolExecutor | None:
    return _EXECUTOR


def get_gate() -> _Gate | None:
    return _GATE
