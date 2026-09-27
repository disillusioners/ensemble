"""KMS logging redaction filter (P3-WP10 — designer-agent mission).

Day-1 runtime mitigation for arch §8 R4 ("no logging redaction").
The filter scrubs KMS plaintext values that have been registered with
:mod:`daemon.services.kms_lite`'s redaction-use registry so they never
appear in any log line, regardless of which handler emits the record.

Semantics (per plan §P3-WP10):

* **Marker format pass-through.** Markers ``__KMS_REF__<handle>__`` carry
  no secret (they are a one-to-one handle reference). The filter MUST
  NOT scrub them — scrubbing a marker would corrupt downstream
  debugging and resolution.
* **Handle pass-through.** ``KMS_HANDLE_<id>`` strings are names, not
  secrets. They pass through unchanged.
* **Fingerprint pass-through.** Fingerprints are ``sha256(plaintext)[:16]``
  hex — one-way hashes, safe to log.
* **Plaintext scrub.** Any plaintext value the store has registered
  (i.e. ``store._iter_registered_plaintexts()``) is replaced with the
  fixed sentinel :data:`SENTINEL`.

Failure mode: if the KMS store is uninitialised or the registry API is
not present (e.g. an older build of ``kms_lite.py`` running alongside
the filter), the filter is a **NO-OP PASS-THROUGH** — it MUST NEVER
crash logging. The acceptance test for "store-unavailable" exercises
this branch.

Handler coverage: install the filter on **every handler reachable from
the root config** — i.e. ``logging.root.handlers`` and every handler
attached to a named logger in ``logging.root.manager.loggerDict``.
:func:`install_kms_redaction_filter` walks both, and
:func:`patch_addhandler_to_install_filter` keeps the coverage live as
handlers are attached later in the boot sequence (notably by
``daemon/api.py`` which configures the root logger with two handlers).
"""

from __future__ import annotations

import logging
import threading

__all__ = [
    "SENTINEL",
    "KMSRedactionFilter",
    "install_kms_redaction_filter",
    "patch_addhandler_to_install_filter",
    "iter_registered_plaintexts",
]


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------

#: Fixed sentinel emitted in place of a redacted plaintext. The string
#: is chosen so it does NOT collide with any ``KMS_HANDLE_`` prefix,
#: any ``__KMS_REF__`` marker envelope, or any plausible plaintext
#: shape (``secrets.token_urlsafe`` is base64-urlsafe; the sentinel
#: contains only ASCII letters and underscores).
SENTINEL = "__REDACTED_KMS_SECRET__"

#: Filter name — used as ``logging.Filter``'s ``name`` attribute and
#: for identity checks during idempotent install.
_FILTER_NAME = "kms_redaction_filter"


# ---------------------------------------------------------------------------
# Registry accessor
# ---------------------------------------------------------------------------


def iter_registered_plaintexts() -> list[str]:
    """Return a snapshot list of all registered plaintexts.

    Public for tests. The redaction filter's only legitimate consumer
    is :class:`KMSRedactionFilter`.

    Returns an empty list when the KMS store is uninitialised or the
    registry API is absent — this is the "no-op pass-through" branch
    of the filter contract.
    """
    try:
        from daemon.services.kms_lite import _get_store
        store = _get_store()
    except Exception:
        return []
    iter_method = getattr(store, "_iter_registered_plaintexts", None)
    if iter_method is None:
        return []
    try:
        snapshot = iter_method()
    except Exception:
        return []
    if not isinstance(snapshot, list):
        try:
            snapshot = list(snapshot)
        except Exception:
            return []
    return snapshot


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class KMSRedactionFilter(logging.Filter):
    """Redact registered KMS plaintexts from log records.

    Filters at the **handler** level: each handler's ``handle()``
    invokes :meth:`filter` immediately before formatting, so the
    record's plaintext-bearing string is scrubbed before any formatter
    serialises it. A record that passes through multiple handlers
    (root → descendant) is scrubbed once per handler — but the
    replacement is idempotent (the sentinel cannot collide with a
    registered plaintext), so the second scrub is a no-op.

    The filter reads the registry via :func:`iter_registered_plaintexts`,
    which returns a snapshot list. The list copy is cheap (handful of
    entries in day-1) and gives the filter a stable view even under
    concurrent mints.
    """

    def __init__(self, name: str = _FILTER_NAME) -> None:
        super().__init__(name=name)
        # Lock guards the cached-sentinel check so two threads don't
        # double-replace under contention. Cheap; only held while we
        # build the rendered string.
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        """Return True (records are never blocked) after scrubbing.

        On a successful scrub, ``record.msg`` is replaced with the
        redacted rendered string and ``record.args`` is cleared. This
        ensures downstream handlers and formatters (e.g. ``%(message)s``
        via ``record.getMessage()``) emit the redacted text — the lazy
        ``%-formatting`` contract is preserved by clearing ``args``
        because the scrubbed message is already fully rendered.
        """
        try:
            plaintexts = iter_registered_plaintexts()
            if not plaintexts:
                return True
            with self._lock:
                rendered = record.getMessage()
                redacted = rendered
                for pt in plaintexts:
                    if not pt:
                        continue
                    if pt in redacted:
                        redacted = redacted.replace(pt, SENTINEL)
                if redacted is not rendered:
                    # Mutate the record so every subsequent handler /
                    # formatter sees the redacted text.
                    record.msg = redacted
                    record.args = ()
        except Exception:
            # Never crash logging. The filter is best-effort; if any
            # step fails, fall through and pass the record through
            # unchanged.
            pass
        return True


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_FILTER_SINGLETON: KMSRedactionFilter | None = None
_FILTER_SINGLETON_LOCK = threading.Lock()


def _get_filter_singleton() -> KMSRedactionFilter:
    """Return the process-wide :class:`KMSRedactionFilter` instance.

    Singleton-by-identity is load-bearing for :func:`install_kms_redaction_filter`'s
    idempotency contract — the install check uses ``filter is
    installed_filter`` to skip already-attached handlers.
    """
    global _FILTER_SINGLETON
    cached = _FILTER_SINGLETON
    if cached is not None:
        return cached
    with _FILTER_SINGLETON_LOCK:
        if _FILTER_SINGLETON is None:
            _FILTER_SINGLETON = KMSRedactionFilter()
        return _FILTER_SINGLETON


def reset_filter_for_tests() -> None:
    """Drop the singleton filter so the next :func:`_get_filter_singleton` mints a fresh one.

    Test-only. Production code MUST NOT call this. Used by tests that
    need to assert identity semantics (e.g. "the second install does
    not duplicate the filter on the same handler").
    """
    global _FILTER_SINGLETON
    with _FILTER_SINGLETON_LOCK:
        _FILTER_SINGLETON = None


# ---------------------------------------------------------------------------
# Install / coverage walker
# ---------------------------------------------------------------------------


def _handler_has_filter(
    handler: logging.Handler, filter_instance: logging.Filter
) -> bool:
    """Return True iff ``filter_instance`` is already attached to ``handler``."""
    for existing in handler.filters:
        if existing is filter_instance:
            return True
    return False


def _attach_filter_to_handler(
    handler: logging.Handler, filter_instance: logging.Filter
) -> bool:
    """Attach ``filter_instance`` to ``handler`` if not already present.

    Returns True iff the filter was newly attached.
    """
    if _handler_has_filter(handler, filter_instance):
        return False
    handler.addFilter(filter_instance)
    return True


def install_kms_redaction_filter(force: bool = False) -> int:
    """Install :class:`KMSRedactionFilter` on every reachable handler.

    Walks ``logging.root.handlers`` AND the handlers of every named
    logger reachable through ``logging.root.manager.loggerDict`` so the
    filter is present on every handler that may emit a record.

    Idempotent: calling multiple times is safe — filters already
    attached (by identity) are not duplicated. Pass ``force=True`` to
    re-attach (only useful after :func:`reset_filter_for_tests`).

    Args:
        force: When True, attach even if the filter is already
            present (used for re-install after a singleton reset).

    Returns:
        Number of handlers the filter was newly attached to.
    """
    flt = _get_filter_singleton()
    attached = 0

    # Root logger handlers.
    root = logging.getLogger()
    for handler in list(root.handlers):
        if force or not _handler_has_filter(handler, flt):
            handler.addFilter(flt)
            attached += 1

    # Descendant logger handlers. ``manager.loggerDict`` contains every
    # named logger created so far, plus ``None`` sentinels for "unset"
    # keys; skip non-Logger entries.
    manager_dict = getattr(root.manager, "loggerDict", {})
    for name, logger_obj in list(manager_dict.items()):
        if not isinstance(logger_obj, logging.Logger):
            continue
        for handler in list(logger_obj.handlers):
            if force or not _handler_has_filter(handler, flt):
                handler.addFilter(flt)
                attached += 1

    return attached


# ---------------------------------------------------------------------------
# Monkey-patch — keep coverage live as handlers are attached later
# ---------------------------------------------------------------------------


_original_logger_add_handler = logging.Logger.addHandler
_patched = False
_patch_lock = threading.Lock()


def _patched_logger_add_handler(
    self: logging.Logger, handler: logging.Handler
) -> None:
    """Drop-in replacement for :meth:`logging.Logger.addHandler`.

    Calls the original ``addHandler`` (so the handler is actually
    attached to the logger), then ensures the KMS redaction filter is
    on the new handler. Idempotent — calling :func:`install_kms_redaction_filter`
    afterwards is still safe.
    """
    _original_logger_add_handler(self, handler)
    flt = _get_filter_singleton()
    if not _handler_has_filter(handler, flt):
        handler.addFilter(flt)


def patch_addhandler_to_install_filter() -> bool:
    """Idempotently monkey-patch :meth:`logging.Logger.addHandler`.

    Call once at logger-configuration time. Subsequent calls are
    no-ops.

    Why this exists: handlers are attached AFTER
    ``daemon/__init__.py`` completes — specifically by
    ``daemon/api.py`` which adds the stderr and rotating-file handlers
    to ``logging.root``. Without this patch, an eager
    ``install_kms_redaction_filter`` at ``__init__`` time walks an
    empty handlers list. The patch closes that gap by ensuring every
    ``addHandler`` call site installs the filter on the just-added
    handler.

    Returns:
        True iff this call performed the patch (False on subsequent
        no-op calls).
    """
    global _patched
    with _patch_lock:
        if _patched:
            return False
        logging.Logger.addHandler = _patched_logger_add_handler  # type: ignore[method-assign]
        _patched = True
        return True


def reset_patch_for_tests() -> None:
    """Restore the original :meth:`logging.Logger.addHandler`.

    Test-only. Production code MUST NOT call this. Tests that need a
    clean logging state across cases call this in a fixture teardown
    to undo the monkey-patch.
    """
    global _patched
    with _patch_lock:
        logging.Logger.addHandler = _original_logger_add_handler  # type: ignore[method-assign]
        _patched = False