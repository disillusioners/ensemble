"""Canonical daemon logging wiring — dual-level handlers (log-noise-hygiene).

The SINGLE home for the daemon's root logging setup. Extracted from
``daemon/api.py``'s module top (it ran inline there from the original PR);
``daemon/api.py`` now calls :func:`install_root_logging` at the same
point, so import-time semantics are unchanged.

**The level model (console/file split):**

* Per-surface handler levels — the NEW knobs:
  - stderr/console (the LCD surface): ``ENSEMBLE_LOG_CONSOLE_LEVEL``
    (default ``INFO``). Plain text, ANSI stripped.
  - ``ensemble.log`` file (rotation policy UNCHANGED: 10 MB × 5):
    ``ENSEMBLE_LOG_FILE_LEVEL`` (default ``DEBUG``). ANSI stripped so
    the file stays clean text.
* Logger-level gates — the back-compat knobs, semantics preserved:
  - ``LOG_LEVEL`` gates the root logger (third-party libs).
  - ``LOG_LEVEL_DAEMON`` gates the ``daemon`` logger hierarchy.

  LANDMINE (why the loggers cannot simply sit at the env values): the
  root/``daemon`` logger level is evaluated BEFORE the handlers see the
  record — a root logger at ``INFO`` discards DEBUG records outright
  and a console=INFO / file=DEBUG split is structurally impossible.
  Therefore the logger gate opens to the MIN of the two handler levels
  (``DEBUG`` under defaults), and the per-handler levels do the split.

  Composition (:func:`_logger_gate_level`):
  - env value TIGHTER than the default band (warning/error) — honored
    EXACTLY as today: the logger sits at the env level, so nothing
    below it reaches ANY surface (identical to the pre-split daemon);
  - env value in the default band (unset, ``info``, ``debug`` —
    including the explicit ``LOG_LEVEL=info`` that shipped .env files
    carry) — the logger opens to ``min(handlers_min, env)`` so the
    verbose file handler actually receives DEBUG records. The LCD
    posture is unchanged (the console handler still filters at its own
    level); the FILE deliberately becomes the verbose surface.

**ANSI.** Both handlers format through an ANSI-stripping formatter:
no escape sequence reaches the LCD or the log file regardless of
emitter (the access-log middleware composes plain text as well).
"""
from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler

# ── back-compat logger gates (read once at import, as before) ───────────────
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
_DAEMON_LEVEL_ENV = os.environ.get("LOG_LEVEL_DAEMON", "info").upper()
_root_log_level = getattr(logging, _LOG_LEVEL, logging.INFO)
_daemon_log_level = getattr(logging, _DAEMON_LEVEL_ENV, logging.INFO)

# ── per-surface handler levels (the split) ──────────────────────────────────


def _level_from_env(var: str, default: int) -> int:
    """Parse a handler-level env var; unset/garbage falls back to default."""
    raw = os.environ.get(var)
    if raw is None or not raw.strip():
        return default
    return getattr(logging, raw.strip().upper(), default)


_CONSOLE_LEVEL = _level_from_env("ENSEMBLE_LOG_CONSOLE_LEVEL", logging.INFO)
_FILE_LEVEL = _level_from_env("ENSEMBLE_LOG_FILE_LEVEL", logging.DEBUG)

# Resolve log directory from env directly (no DaemonConfig import — avoids
# circular import, no dead config field).
_LOG_DIR = os.environ.get("DAEMON_LOG_DIR", "./data/logs")

# Shared format for all handlers (unchanged).
_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# M1 (log-noise-hygiene review cycle 1): handlers installed by this
# module carry this attribute so a re-install (module reload, second
# in-process boot) can REPLACE them instead of stacking a second pair
# (which double-logged every record). Untagged handlers (e.g. pytest's
# caplog) are never touched.
_HANDLER_TAG = "_ensemble_logging_setup_handler"

# ANSI escape sequences (CSI … final-byte) — stripped in both surfaces.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# Module-level handle — re-exported so ``daemon.api``'s
# ``from daemon.logging_setup import daemon_logger`` keeps working (the
# level is applied by :func:`install_root_logging`).
daemon_logger = logging.getLogger("daemon")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a formatted log line."""
    return _ANSI_ESCAPE_RE.sub("", text)


class _AnsiStrippingFormatter(logging.Formatter):
    """Formatter that strips ANSI escape sequences from the emitted line."""

    def format(self, record: logging.LogRecord) -> str:
        return _strip_ansi(super().format(record))


def _logger_gate_level(env_level: int, handlers_min: int) -> int:
    """The logger-level gate composition — see module docstring.

    Tighter-than-default env values (warning/error) act exactly as
    today; default-band values open the logger to the most verbose
    handler so the split can work (the LANDMINE).
    """
    if env_level > logging.INFO:
        return env_level
    return min(handlers_min, env_level)


def install_root_logging() -> None:
    """Wire the root logger + ``daemon`` logger with the dual-level handlers.

    Runs at ``daemon.api`` import time (as the inline setup did).

    Idempotent (M1, log-noise-hygiene review cycle 1): a second call
    REPLACES the previously installed (tagged) handler pair via
    remove-before-add, so handler count and emission multiplicity stay
    exactly as after the first install. Untagged handlers are left
    untouched — the FIRST install behaves byte-identically to before
    the guard.
    """
    handlers_min = min(_CONSOLE_LEVEL, _FILE_LEVEL)

    # Stderr handler (the LCD surface) — level INFO by default, plain text.
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(_CONSOLE_LEVEL)
    stderr_handler.setFormatter(_AnsiStrippingFormatter(
        fmt=_LOG_FORMAT,
        datefmt='%H:%M:%S',
    ))
    setattr(stderr_handler, _HANDLER_TAG, True)

    # File handler — level DEBUG by default; rotation policy UNCHANGED
    # (10 MB × 5). Wrapped in try/except so an unwritable log dir does
    # NOT crash the daemon (graceful degradation, as before).
    file_handler = None
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=os.path.join(_LOG_DIR, "ensemble.log"),
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(_FILE_LEVEL)
        file_handler.setFormatter(_AnsiStrippingFormatter(
            fmt=_LOG_FORMAT,
            datefmt='%H:%M:%S',
        ))
        setattr(file_handler, _HANDLER_TAG, True)
    except OSError as exc:
        # Log to stderr (the only handler guaranteed to work) and continue.
        print(
            f"WARNING: Could not set up file logging in {_LOG_DIR}: {exc}",
            file=sys.stderr,
        )

    # Configure root logger by adding handlers explicitly (NOT basicConfig).
    root_logger = logging.getLogger()
    # M1 re-install guard: strip handlers THIS module installed on an
    # earlier call before adding the fresh pair (replace-before-add —
    # the tagged pair from a prior install is discarded, and its file
    # handle closed). Untagged handlers (caplog, app-added) survive.
    for existing in list(root_logger.handlers):
        if getattr(existing, _HANDLER_TAG, False):
            root_logger.removeHandler(existing)
            try:
                existing.close()
            except Exception:  # noqa: BLE001 — a close failure must not break install
                pass
    root_logger.setLevel(_logger_gate_level(_root_log_level, handlers_min))
    root_logger.addHandler(stderr_handler)
    if file_handler is not None:
        root_logger.addHandler(file_handler)

    # Daemon logger hierarchy: respects LOG_LEVEL_DAEMON (back-compat),
    # opened to the handler split under the default band.
    daemon_logger.setLevel(_logger_gate_level(_daemon_log_level, handlers_min))

    # Suppress uvicorn access logs by default (our SelectiveAccessLogMiddleware
    # handles selective logging).
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.setLevel(logging.WARNING)


__all__ = [
    "daemon_logger",
    "install_root_logging",
]
