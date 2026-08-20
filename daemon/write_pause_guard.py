"""Thread-safe write pause guard for database migrations.

Phase 3 of the SQLite→PostgreSQL migration introduces a hot-swap that
requires stopping all in-flight writes briefly. The codebase runs
synchronous SQLAlchemy writes inside `asyncio.to_thread()` workers and
inside sync `async def` functions, so we must use ``threading``
primitives — not ``asyncio.Lock``/``asyncio.Event`` (which raise
``RuntimeError`` when touched from non-event-loop threads).

This module exposes two cooperating classes:

* ``WritePauseGuard`` — the gate itself. Tracks an atomic
  ``_active_writes`` counter under a lock, signals drain via a
  ``threading.Event``, and serialises ``pause_writes`` /
  ``resume_writes`` via a second ``threading.Lock`` so concurrent
  pause/resume requests can't interleave.

* ``WriteGuardSession`` — a context-manager proxy around a SQLModel
  ``Session``. ``__enter__`` registers the write with the guard
  (raising if writes are paused) and ``__exit__`` releases it and
  closes the underlying session. All other ``Session`` methods
  (``add``, ``delete``, ``execute``, ``commit``, ``exec``, ``get``,
  ``flush``, ``close``, ``refresh``, ``rollback`` ...) are delegated
  transparently via ``__getattr__``.

Usage in a service / tool::

    with WriteGuardSession(Session(engine), manager.write_guard) as session:
        session.add(...)
        session.commit()

And from the migration entry point::

    manager.pause_writes()    # blocks until _active_writes == 0
    # ...perform migration...
    manager.resume_writes()
"""

from __future__ import annotations

import contextlib
import logging
import threading
from types import TracebackType
from typing import Any

from sqlmodel import Session

logger = logging.getLogger(__name__)


class WritePauseGuard:
    """Thread-safe gate that drains in-flight writes before allowing a pause.

    Attributes
    ----------
    _write_paused:
        ``True`` once ``pause_writes()`` has been called and not yet
        released by ``resume_writes()``. Read via the public
        ``is_write_paused`` property.
    _active_writes:
        Atomic counter of sessions currently between ``write_enter``
        and ``write_exit``. Mutated only under ``_lock``.
    _lock:
        Protects ``_active_writes`` and the ``_drain_event`` transitions.
    _drain_event:
        ``threading.Event`` that is *set* when ``_active_writes == 0``
        and *cleared* whenever the counter is non-zero. ``pause_writes``
        waits on this event, so it returns only once every in-flight
        write has finished.
    _gate_lock:
        Serialises ``pause_writes`` / ``resume_writes`` against each
        other so two callers cannot race the ``_write_paused`` flag.
    """

    def __init__(self) -> None:
        """Initialise the guard in the "writes allowed, none in flight" state."""
        self._write_paused: bool = False
        self._active_writes: int = 0
        self._lock: threading.Lock = threading.Lock()
        # Set when no writes are active (initial state). Cleared as soon
        # as a writer enters; set again when the last writer exits.
        self._drain_event: threading.Event = threading.Event()
        self._drain_event.set()
        # Guards transitions of ``_write_paused`` so concurrent
        # pause/resume callers don't interleave and corrupt the flag.
        self._gate_lock: threading.Lock = threading.Lock()

    # ── public read-only state ──────────────────────────────────────────────

    @property
    def is_write_paused(self) -> bool:
        """Return ``True`` if ``pause_writes()`` is currently in effect."""
        return self._write_paused

    @property
    def active_writes(self) -> int:
        """Return the current count of in-flight writes.

        Exposed for diagnostics / tests; reads the counter atomically
        by briefly acquiring ``_lock``.
        """
        with self._lock:
            return self._active_writes

    # ── gate control (caller side, e.g. migration entry point) ──────────────

    def pause_writes(self) -> None:
        """Block new writes and wait for in-flight writes to finish.

        Acquires ``_gate_lock`` so concurrent pause/resume requests
        can't race. Sets ``_write_paused = True`` *before* waiting on
        ``_drain_event`` so any writer that races the wait still sees
        the flag on its next ``write_enter`` call.

        Returns once ``_active_writes`` has reached zero. Safe to call
        multiple times — subsequent calls just re-block on the event
        (which is already set), so they're effectively no-ops.
        """
        with self._gate_lock:
            self._write_paused = True
            active = self._active_writes
            logger.info("WritePauseGuard.pause_writes: pausing (active_writes=%d)", active)
            # Block until all in-flight writers release the guard.
            # If no writers are active, the event is already set and
            # ``wait()`` returns immediately.
            self._drain_event.wait()
            logger.info("WritePauseGuard.pause_writes: drained, writes paused")

    def resume_writes(self) -> None:
        """Re-allow new writes.

        Acquires ``_gate_lock`` so we never race a concurrent
        ``pause_writes``. The drain event stays set in the steady
        state — it's toggled by ``write_enter`` / ``write_exit``
        based on the counter.
        """
        with self._gate_lock:
            self._write_paused = False
            logger.info("WritePauseGuard.resume_writes: writes resumed")

    # ── writer registration (called by WriteGuardSession) ───────────────────

    def write_enter(self) -> None:
        """Register a new in-flight write or raise if writes are paused.

        Order matters: the paused check happens *before* we take the
        counter lock, so a paused guard never inflates the counter.
        A writer that slips through before ``pause_writes`` set the
        flag will increment the counter and clear the drain event,
        so the pausing thread still waits for it.
        """
        # Fast path: paused flag is read without the counter lock.
        if self._write_paused:
            raise RuntimeError("Writes are paused for database migration")
        with self._lock:
            # Re-check under the lock to close the TOCTOU window: a
            # pause could have raced the fast-path read between the
            # check above and the lock acquisition below.
            if self._write_paused:
                raise RuntimeError("Writes are paused for database migration")
            self._active_writes += 1
            # Mark "drain pending" so pause_writes blocks until we exit.
            self._drain_event.clear()

    def write_exit(self) -> None:
        """Release a previously-acquired write slot.

        When the counter hits zero, set ``_drain_event`` to release
        any thread blocked inside ``pause_writes``.
        """
        with self._lock:
            if self._active_writes <= 0:
                # Defensive: write_exit should always be paired with
                # write_enter, but log rather than silently corrupt
                # the counter if it's somehow called extra times.
                logger.warning(
                    "WritePauseGuard.write_exit called with no active writes"
                )
                self._active_writes = 0
            else:
                self._active_writes -= 1
            if self._active_writes == 0:
                # Last writer released — wake up any pausing thread.
                self._drain_event.set()

    # ── context-manager protocol ────────────────────────────────────────────
    #
    # Production usage at ``daemon/manager.py`` (sites
    # ``_revive_terminal_instance``,
    # ``_reconcile_deferred_report``, and
    # ``_reconcile_deferred_report_async``) does e.g.
    # ``with self._write_guard:`` / ``async with self._write_guard:``
    # inside outer methods that bracket the session-scope lifetime.
    # Without these dunders the first real exercise would raise
    # ``TypeError: 'WritePauseGuard' object does not support the
    # context manager protocol`` and the Site-1 / ORPHAN recovery
    # would silently never run.
    #
    # The primitives are ``threading.Lock`` / ``threading.Event``,
    # so the async dunders simply delegate to the sync ones — there
    # is no native coroutine to await. (*Don't* wrap sync enter /
    # exit in a coroutine that yields to the loop: that would break
    # callers running on a non-loop ``threading.Thread``.)
    #
    # Exception semantics match :class:`WriteGuardSession`: an
    # exception inside the ``with`` block must still release the
    # slot (otherwise an unexpected error would leave
    # ``_active_writes`` inflated and deadlock ``pause_writes``).
    # We do NOT swallow the exception — ``__exit__`` returns
    # ``None``/falsy so the interpreter propagates normally.

    def __enter__(self) -> "WritePauseGuard":
        """Enter the write region and return ``self``.

        Delegates to :meth:`write_enter`.
        """
        self.write_enter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the write region, releasing the slot.

        Delegates to :meth:`write_exit`. Always releases (even on
        exception) so a failure inside the region cannot pin
        ``_active_writes`` above zero and deadlock ``pause_writes``.
        """
        self.write_exit()

    async def __aenter__(self) -> "WritePauseGuard":
        """Async equivalent of :meth:`__enter__`.

        The guard primitives are ``threading``-based (non-loop-safe
        sync primitives), so the async delegate calls the sync
        ``__enter__`` directly. There is no coroutine to await.
        """
        self.write_enter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async equivalent of :meth:`__exit__``.

        Same caveat as ``__aenter__``: delegates directly to the
        sync ``write_exit`` (no ``await`` involved).
        """
        self.write_exit()


class WriteGuardSession:
    """Context-manager proxy that registers a ``Session`` with a guard.

    ``__enter__`` calls ``guard.write_enter()`` (which raises if writes
    are paused) and ``__exit__`` calls ``guard.write_exit()`` and
    closes the underlying session. All other ``Session`` methods /
    attributes are delegated to the wrapped session via
    ``__getattr__``.

    The proxy is intentionally permissive: read methods (``get``,
    ``exec``, ``query`` ...) and write methods (``add``,
    ``commit`` ...) are *all* delegated unchanged — the gate is
    enforced once at ``__enter__`` time. This matches the call-site
    pattern of "open a session, do a bounded unit of work, close"
    where the entire block is a single logical write.

    Methods that *do* live on the wrapper:
        ``__enter__``, ``__exit__``, ``__getattr__``, ``close``,
        ``__init__`` and dunder protocols required for the proxy.
    """

    # Attributes accessed on the wrapper itself (not delegated).
    _RESERVED_ATTRS = frozenset({
        "_session",
        "_guard",
        "_closed",
        "__enter__",
        "__exit__",
        "__getattr__",
    })

    def __init__(self, session: Session, guard: WritePauseGuard) -> None:
        """Wrap a ``Session`` and register it with a ``WritePauseGuard``.

        Accepts either a raw ``Session`` *or* a context manager that
        yields one. The latter arises at test sites and at any caller
        that has already done ``with Session(engine) as s:`` and now
        wants to gate ``s``. We unwrap the CM by calling ``__enter__``
        here and store the underlying session, so the proxy exposes
        ``add`` / ``commit`` / ``close`` / ``exec`` ... directly
        instead of forwarding to a ``_GeneratorContextManager``.

        We *only* unwrap ``contextlib._GeneratorContextManager``
        instances (the type produced by ``@contextmanager``). A raw
        SQLModel ``Session`` is itself a context manager — calling
        its ``__enter__`` would change semantics and ownership — so
        it must be passed through unchanged. ``MagicMock`` instances
        used in tests happen to expose ``__enter__``/``__exit__`` but
        are not context managers, so they also pass through.

        Args:
            session: The underlying ``Session`` (or a context manager
                yielding one) whose work we want to gate. Ownership
                transfers to this proxy — ``__exit__`` will close it.
            guard: The ``WritePauseGuard`` that will be notified of
                the session's entry and exit.
        """
        # Only unwrap the specific CM type produced by
        # ``@contextmanager`` (and tests that fake it). Real
        # ``Session`` objects and ``MagicMock`` instances are not
        # ``_GeneratorContextManager`` subclasses, so they pass
        # through unchanged and ``close()`` still hits the right
        # object on ``__exit__``.
        if isinstance(session, contextlib._GeneratorContextManager):
            session = session.__enter__()
        # Use object.__setattr__ to bypass our own __setattr__ (if
        # we ever add one) and avoid triggering __getattr__ for
        # ``_session`` during init.
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_guard", guard)
        object.__setattr__(self, "_closed", False)

    # ── context manager protocol ────────────────────────────────────────────

    def __enter__(self) -> "WriteGuardSession":
        """Register the write with the guard and return ``self``.

        Raises:
            RuntimeError: If ``guard.write_enter`` rejects the
                session because writes are currently paused.
        """
        self._guard.write_enter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the session and release the guard slot.

        Delegates to :meth:`close`, which is idempotent — so
        ``close()`` called explicitly inside the ``with`` block
        is safe and ``__exit__`` becomes a no-op for the guard
        side (no double ``write_exit``).
        """
        self.close()

    # ── explicit close (mirrors Session.close) ──────────────────────────────

    def close(self) -> None:
        """Close the underlying session and release the guard slot.

        Idempotent: subsequent calls are no-ops. Either invoke via
        ``with`` (preferred) or call directly if the proxy is used
        outside a context manager.
        """
        if self._closed:
            return
        try:
            self._session.close()
        finally:
            # Set ``_closed`` and release the guard even if
            # ``self._session.close()`` raised, so a broken
            # session close can never leave ``_active_writes``
            # inflated and deadlock ``pause_writes()``.
            object.__setattr__(self, "_closed", True)
            self._guard.write_exit()

    # ── proxy everything else to the wrapped Session ────────────────────────

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute / method access to the wrapped ``Session``.

        ``__getattr__`` is only called when normal attribute lookup
        fails, so dunders and explicit attributes (``_session``,
        ``_guard``, ``close``, ``__enter__`` ...) are *not* delegated.

        ``_session`` is accessed via ``object.__getattribute__`` to
        avoid recursing back through ``__getattr__`` in the (rare)
        case where the wrapped session lacks the requested attribute
        and raises ``AttributeError``.
        """
        # Belt-and-braces: avoid recursion if Python somehow asks
        # __getattr__ for the wrapper's own private attributes.
        if name in WriteGuardSession._RESERVED_ATTRS:
            raise AttributeError(name)
        session = object.__getattribute__(self, "_session")
        return getattr(session, name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        """Return a debug-friendly repr that identifies the wrapper."""
        try:
            inner = object.__getattribute__(self, "_session")
        except AttributeError:
            inner = "<uninitialised>"
        return f"WriteGuardSession(session={inner!r}, closed={self._closed})"


__all__ = ["WritePauseGuard", "WriteGuardSession"]
