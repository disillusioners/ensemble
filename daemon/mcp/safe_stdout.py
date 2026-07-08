"""STDIO MCP protocol protection.

When the MCP (Model Context Protocol) server runs over the ``stdio``
transport, the parent process reads ``sys.stdout`` *exclusively* for
JSON-RPC messages. Any stray ``print()`` call or text write that lands
on ``sys.stdout`` corrupts the JSON-RPC stream and breaks the protocol.

This module provides :class:`_MCPSafeStdout`, a transparent wrapper
that:

* Forwards **binary** writes (``.buffer``) to the real ``sys.stdout``
  so the protocol bytes keep flowing on the original file descriptor.
* Redirects **text** writes (``.write``, ``.writelines``, ``print``)
  to ``sys.stderr`` so diagnostic output never interleaves with the
  JSON-RPC stream.

A bootstrap launcher (see the ``__main__`` block) lets any stdio MCP
server opt-in via::

    python3 -m daemon.mcp.safe_stdout daemon.mcp.kb_server [args...]

The wrapper installs itself **before** importing the target module, so
the target's top-level ``print()`` calls are redirected from the very
first line of execution.
"""

from __future__ import annotations

import argparse
import io
import logging
import runpy
import sys
import traceback
from collections.abc import Iterable

__all__ = ["_MCPSafeStdout", "install_safe_stdout"]

logger = logging.getLogger(__name__)


class _MCPSafeStdout:
    """Stdout wrapper: binary (``.buffer``) → real stdout, text → stderr.

    This wrapper presents the same public surface as a ``TextIOBase``
    stream but splits the byte and text channels: binary writes pass
    through to the real stdout (preserving the JSON-RPC stream), while
    text writes are diverted to stderr so they cannot corrupt the
    protocol.

    Attributes:
        _real: The original ``sys.stdout`` (typically a
            ``TextIOWrapper``). Used for binary writes via ``.buffer``
            and for ``fileno()``.
        _stderr: The ``sys.stderr`` stream. Receives all redirected
            text writes.
    """

    def __init__(self, real_stdout: io.TextIOBase, stderr: io.TextIOBase) -> None:
        """Store the real stdout and stderr streams.

        Args:
            real_stdout: The original ``sys.stdout``. Its ``.buffer``
                attribute is the binary channel that carries JSON-RPC
                bytes and must remain intact.
            stderr: The ``sys.stderr`` stream that will receive all
                redirected text writes.
        """
        self._real = real_stdout
        self._stderr = stderr

    @property
    def buffer(self) -> io.BufferedWriter:
        """Return the underlying binary buffer of the real stdout.

        The MCP stdio transport writes JSON-RPC frames as raw bytes via
        this buffer; diverting it would break the protocol.
        """
        return self._real.buffer

    def fileno(self) -> int:
        """Return the file descriptor of the real stdout."""
        return self._real.fileno()

    def write(self, s: str) -> int:
        """Write ``s`` to stderr and return the number of characters written."""
        return self._stderr.write(s)

    def writelines(self, lines: Iterable[str]) -> None:
        """Write each line in ``lines`` to stderr."""
        return self._stderr.writelines(lines)

    def flush(self) -> None:
        """Flush both stderr (text channel) and real stdout (binary channel)."""
        self._stderr.flush()
        self._real.flush()

    def isatty(self) -> bool:
        """Return whether stderr is attached to a terminal."""
        return self._stderr.isatty()

    @property
    def encoding(self) -> str:
        """Return the encoding used by stderr."""
        return self._stderr.encoding

    @property
    def errors(self) -> str | None:
        """Return the error-handling mode used by stderr."""
        return self._stderr.errors

    @property
    def closed(self) -> bool:
        """Return whether stderr has been closed.

        The wrapper itself never closes the underlying streams, so this
        reflects only the stderr stream's own closed state.
        """
        return self._stderr.closed

    @property
    def mode(self) -> str:
        """Return the file mode string (``'w'`` for text write-only)."""
        return "w"

    @property
    def name(self) -> str:
        """Return the real stdout's name attribute."""
        return self._real.name

    @property
    def newlines(self) -> str | None:
        """Return the newline translation state of stderr, if tracked."""
        return getattr(self._stderr, "newlines", None)

    def readable(self) -> bool:
        """Return ``False`` — this wrapper is write-only."""
        return False

    def writable(self) -> bool:
        """Return ``True`` — this wrapper accepts writes."""
        return True

    def seekable(self) -> bool:
        """Return ``False`` — this wrapper does not support seeking."""
        return False

    def close(self) -> None:
        """No-op close: flush stderr and real stdout but never close the streams.

        Closing the real stdout would terminate the JSON-RPC channel and
        break the MCP protocol. Closing stderr would suppress diagnostic
        output for the remainder of the process. The wrapper therefore
        flushes stderr (so any buffered text is visible) AND the real
        stdout (so any final buffered JSON-RPC frame is delivered) and
        leaves both streams open. The ``closed`` property continues to
        report the underlying stderr state (typically ``False``).
        """
        self._stderr.flush()
        self._real.flush()

    def __getattr__(self, name: str) -> object:
        """Fall back to the stderr stream for any unknown attribute.

        This is only invoked when ``name`` is not found through normal
        attribute lookup, so ``self._real`` and ``self._stderr`` resolve
        via the instance dict and never trigger this fallback.
        """
        return getattr(self._stderr, name)


def install_safe_stdout() -> _MCPSafeStdout | None:
    """Wrap ``sys.stdout`` with :class:`_MCPSafeStdout` in-place.

    Idempotent: if ``sys.stdout`` is already a :class:`_MCPSafeStdout`,
    the existing wrapper is left in place and ``None`` is returned.

    If ``sys.stdout`` has been replaced by something other than a
    :class:`io.TextIOBase` and not a :class:`_MCPSafeStdout` (for
    example, by ``pytest``'s capture mechanism), the wrapper is still
    installed and a DEBUG-level log entry is emitted so operators can
    see the unusual condition without disrupting the process.

    The wrapper is assigned to ``sys.stdout`` as a side effect; callers
    do not need to do the assignment themselves.

    Returns:
        The freshly installed wrapper, or ``None`` if installation was
        skipped because ``sys.stdout`` was already wrapped.
    """
    if isinstance(sys.stdout, _MCPSafeStdout):
        logger.debug("safe_stdout already installed; skipping")
        return None

    if not isinstance(sys.stdout, io.TextIOBase):
        logger.debug(
            "sys.stdout is %r (not a TextIOBase); installing wrapper anyway",
            type(sys.stdout).__name__,
        )

    wrapper = _MCPSafeStdout(sys.stdout, sys.stderr)
    sys.stdout = wrapper
    logger.debug("installed _MCPSafeStdout wrapper on sys.stdout")
    return wrapper


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the bootstrap launcher.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        The parsed namespace with a single ``target`` attribute holding
        the dotted module name to execute as ``__main__``.
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m daemon.mcp.safe_stdout",
        description=(
            "Bootstrap launcher that installs _MCPSafeStdout on "
            "sys.stdout, then runs the target module as __main__. "
            "Use this to wrap any stdio MCP server so its stray "
            "print() calls cannot corrupt the JSON-RPC stream."
        ),
    )
    parser.add_argument(
        "target",
        help=(
            "Dotted module name to execute as __main__ "
            "(e.g. 'daemon.mcp.kb_server')."
        ),
    )
    return parser.parse_args(argv)


def _main(argv: list[str] | None = None) -> int:
    """Entry point for the ``python3 -m daemon.mcp.safe_stdout`` launcher.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``2`` if the target module
        cannot be found, ``1`` for any other error.
    """
    args = _parse_args(argv)
    target = args.target

    install_safe_stdout()
    logger.info("[safe_stdout] protecting stdio for %s", target)

    try:
        runpy.run_module(target, run_name="__main__")
    except ImportError as exc:
        # ``ImportError`` is the parent of ``ModuleNotFoundError``; we catch
        # the parent because ``runpy._get_module_details`` raises a plain
        # ``ImportError`` (not ``ModuleNotFoundError``) when the spec is
        # missing — depending on Python version.
        print(
            f"[safe_stdout] module not found: {target} ({exc})",
            file=sys.stderr,
        )
        return 2
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        # String or other code: print message to stderr, exit 1
        print(f"[safe_stdout] {target}: {code}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        raise  # never swallow interrupts
    except Exception:
        traceback.print_exc()
        logger.exception("[safe_stdout] target %s raised", target)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())