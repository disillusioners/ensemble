"""Dispatch execution classes + timeout + retry (Engine Arc P2 — T2.3).

Three concurrency classes (D2.2):

* ``MAIN_LOOP`` — awaited inline on the engine task (pure in-memory only).
* ``DB_OFFLOAD`` — ``await asyncio.to_thread(fn)``.
* ``DEDICATED_EXECUTOR`` — ``loop.run_in_executor`` on a dedicated
  ``ThreadPoolExecutor(max_workers=tick_workers)`` (never the shared
  db_offload executor).

Plus ``run_with_timeout`` (asyncio.wait_for; cancel vs extend policy) and
the retry loop (D2.8: ``min(cap, base * 2^(attempt-1))``, no jitter).
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from daemon.repositories.engine.models import (
    CONCURRENCY_CLASS_DB_OFFLOAD,
    CONCURRENCY_CLASS_DEDICATED_EXECUTOR,
    CONCURRENCY_CLASS_MAIN_LOOP,
)


async def _call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Invoke fn, awaiting it if it is a coroutine function."""
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return fn(*args, **kwargs)


async def run_main_loop(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """MAIN_LOOP class: awaited inline on the engine task (loop thread)."""
    return await _call(fn, *args, **kwargs)


async def run_db_offload(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """DB_OFFLOAD class: off-loop via asyncio.to_thread.

    ``fn`` must be a (sync) callable runnable in a worker thread. An async
    ``fn`` is awaited directly (best effort) but the off-loop thread
    assertion then cannot hold — callers wanting the thread assertion pass
    a sync callable.
    """
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return await asyncio.to_thread(fn, *args, **kwargs)


async def run_dedicated_executor(
    executor: ThreadPoolExecutor, fn: Callable, *args: Any, **kwargs: Any
) -> Any:
    """DEDICATED_EXECUTOR class: pooled thread via run_in_executor."""
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    loop = asyncio.get_event_loop()
    if args or kwargs:
        # run_in_executor only takes (fn, *args); bind kwargs via partial
        import functools

        bound = functools.partial(fn, *args, **kwargs)
        return await loop.run_in_executor(executor, bound)
    return await loop.run_in_executor(executor, fn)


async def run_with_timeout(
    coro_factory: Callable[[], Any],
    max_runtime_s: float,
    policy: str,
    on_extend_tick: Optional[Callable[[], Any]] = None,
) -> tuple[str, Optional[BaseException]]:
    """Run a dispatch coroutine with a timeout.

    Returns ``(outcome, exc)`` where outcome is one of
    ``"ok"`` / ``"TIMED_OUT"`` / ``"TIMEOUT_PENDING_EXTEND"``.

    * ``policy == "cancel"``: cancel on timeout → ``TIMED_OUT``.
    * ``policy == "extend"``: record ``TIMEOUT_PENDING_EXTEND`` (via
      ``on_extend_tick`` if provided) and DO NOT release the single-flight
      claim — await the actual completion. The caller keeps the lock held.
      ``on_extend_tick`` may be sync or async; an async callback is awaited
      at the fire instant so the fire-time audit row is durable before the
      extended dispatch continues.
    """
    # Build the awaitable once.
    awaitable = coro_factory()
    if not asyncio.iscoroutine(awaitable):
        # Allow a coroutine factory returning a plain value.
        awaitable = asyncio.coroutine(lambda: awaitable)()  # type: ignore

    if policy == "cancel":
        try:
            await asyncio.wait_for(awaitable, timeout=max_runtime_s)
            return "ok", None
        except asyncio.TimeoutError:
            return "TIMED_OUT", asyncio.TimeoutError()
        except BaseException as exc:  # noqa: BLE001
            return "ERROR", exc

    # extend policy
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=max_runtime_s)
        return "ok", None
    except asyncio.TimeoutError:
        # timeout reached — record pending-extend, keep running. Await an
        # async callback so the fire-time audit row lands before we continue.
        if on_extend_tick is not None:
            try:
                _fired = on_extend_tick()
                if asyncio.iscoroutine(_fired):
                    await _fired
            except Exception:  # noqa: BLE001 — fire-time write failure never kills the dispatch
                pass
        # await actual completion (lock stays held by caller)
        try:
            await task
            return "TIMEOUT_PENDING_EXTEND", None
        except BaseException as exc:  # noqa: BLE001
            return "TIMEOUT_PENDING_EXTEND", exc
    except BaseException as exc:  # noqa: BLE001
        if not task.done():
            task.cancel()
        return "ERROR", exc


async def run_with_retry(
    attempt_fn: Callable[[int], Any],
    retry_max: int,
    backoff_base_s: Optional[float],
    backoff_cap_s: Optional[float],
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> tuple[str, int, Optional[BaseException]]:
    """Retry loop (D2.8). Returns (outcome, attempts_used, last_exc).

    ``attempt_fn(attempt)`` is called for attempt 1..retry_max+1. It must
    raise on failure and return normally on success. Backoff between
    attempts: ``min(cap, base * 2^(attempt-1))`` seconds, no jitter.
    After ``retry_max`` retries (retry_max+1 total attempts) the outcome
    is ``FAILED_BACKOFF``.
    """
    base = backoff_base_s if backoff_base_s is not None else 1.0
    cap = backoff_cap_s if backoff_cap_s is not None else base
    attempts = 0
    last_exc: Optional[BaseException] = None
    total_attempts = max(1, int(retry_max) + 1)
    for attempt in range(1, total_attempts + 1):
        attempts = attempt
        try:
            await attempt_fn(attempt)
            return "SUCCEEDED", attempts, None
        except BaseException as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= total_attempts:
                break
            delay = min(cap, base * (2 ** (attempt - 1)))
            if delay and delay > 0:
                await sleep(delay)
    return "FAILED_BACKOFF", attempts, last_exc


__all__ = [
    "run_db_offload",
    "run_dedicated_executor",
    "run_main_loop",
    "run_with_retry",
    "run_with_timeout",
]
