"""Unit tests for ``daemon.mcp.safe_stdout`` (STDIO MCP protocol protection).

These tests cover the ``_MCPSafeStdout`` wrapper, the
``install_safe_stdout()`` helper, and the ``python -m
daemon.mcp.safe_stdout`` bootstrap launcher. The wrapper's contract
is:

* Text writes (``.write`` / ``.writelines`` / ``print``) are diverted
  to ``sys.stderr`` so they cannot corrupt the JSON-RPC stream that
  flows on the real ``sys.stdout``.
* Binary writes via the ``.buffer`` property keep flowing on the
  real ``sys.stdout`` so the MCP protocol stays intact.
* File-like properties (``fileno``, ``encoding``, ``errors``,
  ``closed``, etc.) delegate sensibly to either ``sys.stderr`` (text
  side) or the real ``sys.stdout`` (binary side, e.g. ``fileno``,
  ``name``).
* ``install_safe_stdout()`` is idempotent: a second call leaves the
  existing wrapper in place and returns ``None``.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from daemon.mcp.safe_stdout import (
    _MCPSafeStdout,
    _main,
    _parse_args,
    install_safe_stdout,
)


# =============================================================================
# Helpers / fakes
# =============================================================================


class _FakeTextStream:
    """A minimal stand-in for ``sys.stdout`` / ``sys.stderr``.

    It exposes the ``.buffer`` attribute that ``_MCPSafeStdout.buffer``
    forwards to, plus the file-like methods exercised by the wrapper.
    Using a real-ish object (instead of a ``MagicMock``) keeps the
    tests honest about the attribute delegation contract.
    """

    def __init__(
        self,
        *,
        name: str = "<fake>",
        fileno_no: int = 1,
        is_tty: bool = False,
        encoding: str = "utf-8",
        errors: str = "strict",
        newlines: str | None = None,
    ) -> None:
        self.buffer = io.BytesIO()
        self._name = name
        self._fileno = fileno_no
        self._is_tty = is_tty
        self._encoding = encoding
        self._errors = errors
        self._newlines = newlines
        self.closed = False
        self.flush_count = 0
        self.close_count = 0
        self.write_log: list[str] = []
        self.writelines_log: list[list[str]] = []

    @property
    def name(self) -> str:
        return self._name

    def fileno(self) -> int:
        return self._fileno

    def flush(self) -> None:
        self.flush_count += 1

    def close(self) -> None:
        self.close_count += 1
        self.closed = True

    def write(self, s: str) -> int:
        self.write_log.append(s)
        return len(s)

    def writelines(self, lines) -> None:  # type: ignore[no-untyped-def]
        self.writelines_log.append(list(lines))

    def isatty(self) -> bool:
        return self._is_tty

    @property
    def encoding(self) -> str:
        return self._encoding

    @property
    def errors(self) -> str | None:
        return self._errors

    @property
    def newlines(self) -> str | None:
        return self._newlines


def _make_pair(
    *, name: str = "<stdout>", is_tty: bool = False
) -> tuple[_FakeTextStream, _FakeTextStream]:
    """Return ``(real_stdout, stderr)`` with a working ``.buffer``."""
    real_stdout = _FakeTextStream(name=name, fileno_no=1, is_tty=is_tty)
    stderr = _FakeTextStream(name="<stderr>", fileno_no=2, is_tty=is_tty)
    return real_stdout, stderr


# =============================================================================
# 1. Text writes redirected to stderr
# =============================================================================


class TestTextWritesRedirectedToStderr:
    """``.write`` and ``.writelines`` must land on stderr, not stdout."""

    def test_write_goes_to_stderr_not_stdout(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        result = wrapper.write("hello")

        # Text write landed on stderr.
        assert stderr.write_log == ["hello"]
        # And NOT on stdout.
        assert real_stdout.write_log == []
        # Return value matches what stderr's write returned.
        assert result == 5

    def test_writelines_goes_to_stderr_not_stdout(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        wrapper.writelines(["a\n", "b\n", "c\n"])

        assert stderr.writelines_log == [["a\n", "b\n", "c\n"]]
        assert real_stdout.writelines_log == []
        assert real_stdout.write_log == []

    def test_multiple_writes_accumulate_on_stderr(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        wrapper.write("first ")
        wrapper.write("second ")
        wrapper.writelines(["third\n"])

        assert stderr.write_log == ["first ", "second "]
        assert stderr.writelines_log == [["third\n"]]
        assert real_stdout.write_log == []
        assert real_stdout.writelines_log == []


# =============================================================================
# 2. Binary buffer stays on stdout
# =============================================================================


class TestBinaryBufferStaysOnStdout:
    """``wrapper.buffer`` must be the real stdout's ``.buffer``."""

    def test_buffer_property_returns_real_stdout_buffer(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        # The wrapper exposes the exact BytesIO that lives on real_stdout.
        assert wrapper.buffer is real_stdout.buffer

    def test_writes_through_buffer_reach_real_stdout_only(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        # Simulate an MCP stdio server writing a JSON-RPC frame as bytes.
        frame = b'{"jsonrpc":"2.0","id":1,"result":"ok"}\n'
        wrapper.buffer.write(frame)
        wrapper.buffer.flush()

        # Bytes went to the real stdout's buffer.
        assert real_stdout.buffer.getvalue() == frame
        # Nothing leaked onto the stderr side as text.
        assert stderr.write_log == []
        assert stderr.writelines_log == []


# =============================================================================
# 3. print() redirected to stderr
# =============================================================================


class TestPrintRedirectedToStderr:
    """``print`` uses ``sys.stdout.write``, so the wrapper must redirect it."""

    def test_print_routed_through_wrapper_lands_on_stderr(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        original_stdout = sys.stdout
        sys.stdout = wrapper
        try:
            print("test")
        finally:
            sys.stdout = original_stdout

        # ``print`` invokes ``write`` twice (the text and the
        # newline) — the wrapper diverts BOTH calls to stderr.
        assert stderr.write_log == ["test", "\n"]
        # And the real stdout saw no text writes.
        assert real_stdout.write_log == []

    def test_multiple_prints_all_land_on_stderr(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        original_stdout = sys.stdout
        sys.stdout = wrapper
        try:
            print("alpha")
            print("beta")
            print("gamma")
        finally:
            sys.stdout = original_stdout

        assert stderr.write_log == ["alpha", "\n", "beta", "\n", "gamma", "\n"]
        assert real_stdout.write_log == []


# =============================================================================
# 4. Transparent when no writes occur
# =============================================================================


class TestTransparentWhenNoWrites:
    """The wrapper must not touch either side when no I/O happens."""

    def test_constructing_wrapper_does_no_io(self):
        real_stdout, stderr = _make_pair()
        _MCPSafeStdout(real_stdout, stderr)

        assert real_stdout.write_log == []
        assert real_stdout.writelines_log == []
        assert real_stdout.flush_count == 0
        assert real_stdout.close_count == 0
        assert stderr.write_log == []
        assert stderr.writelines_log == []
        assert stderr.flush_count == 0
        assert stderr.close_count == 0

    def test_property_accesses_do_no_io(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        # Read-only property access must not flush or write anything.
        _ = wrapper.buffer
        _ = wrapper.encoding
        _ = wrapper.errors
        _ = wrapper.closed
        _ = wrapper.mode
        _ = wrapper.name
        _ = wrapper.newlines
        _ = wrapper.readable()
        _ = wrapper.writable()
        _ = wrapper.seekable()
        _ = wrapper.isatty()
        _ = wrapper.fileno()

        assert real_stdout.write_log == []
        assert real_stdout.writelines_log == []
        assert real_stdout.flush_count == 0
        assert real_stdout.close_count == 0
        assert stderr.write_log == []
        assert stderr.writelines_log == []
        assert stderr.flush_count == 0
        assert stderr.close_count == 0


# =============================================================================
# 5. Property delegation
# =============================================================================


class TestPropertyDelegation:
    """The wrapper must forward file-like properties to the right side."""

    def test_fileno_delegates_to_real_stdout(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        # The implementation deliberately uses ``self._real.fileno()``
        # so the binary channel's fd is exposed.
        assert wrapper.fileno() == 1
        assert wrapper.fileno() == real_stdout._fileno

    def test_flush_flushes_both_stderr_and_real_stdout(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        wrapper.flush()

        assert real_stdout.flush_count == 1
        assert stderr.flush_count == 1

    def test_isatty_delegates_to_stderr(self):
        real_stdout, stderr = _make_pair(is_tty=False)
        wrapper = _MCPSafeStdout(real_stdout, stderr)
        assert wrapper.isatty() is False

        real_stdout2, stderr2 = _make_pair(is_tty=True)
        wrapper2 = _MCPSafeStdout(real_stdout2, stderr2)
        # stderr.isatty() drives the answer.
        stderr2._is_tty = True
        assert wrapper2.isatty() is True

    def test_encoding_delegates_to_stderr(self):
        real_stdout, stderr = _make_pair()
        stderr._encoding = "latin-1"
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        assert wrapper.encoding == "latin-1"

    def test_errors_delegates_to_stderr(self):
        real_stdout, stderr = _make_pair()
        stderr._errors = "replace"
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        assert wrapper.errors == "replace"

    def test_closed_delegates_to_stderr(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)
        assert wrapper.closed is False

        # After the stderr stream is closed externally, the wrapper must
        # reflect that.
        stderr.closed = True
        assert wrapper.closed is True

    def test_mode_is_write(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)
        assert wrapper.mode == "w"

    def test_name_delegates_to_real_stdout(self):
        real_stdout, stderr = _make_pair()
        real_stdout._name = "<custom-stdout>"
        wrapper = _MCPSafeStdout(real_stdout, stderr)
        assert wrapper.name == "<custom-stdout>"

    def test_newlines_uses_stderr_value_when_present(self):
        real_stdout, stderr = _make_pair()
        stderr._newlines = "\n"
        wrapper = _MCPSafeStdout(real_stdout, stderr)
        assert wrapper.newlines == "\n"

    def test_newlines_defaults_to_none_when_stderr_lacks_it(self):
        # A stream with no ``newlines`` attribute should not blow up;
        # the wrapper falls back to ``None`` via ``getattr(..., None)``.
        # We patch the class to raise on attribute access and verify
        # the wrapper's ``getattr(..., None)`` default kicks in.
        real_stdout, stderr = _make_pair()

        class _NoNewlinesStream(_FakeTextStream):
            def __getattr__(self, name: str):
                if name == "newlines":
                    raise AttributeError(name)
                return super().__getattr__(name)

        stderr_no_newlines = _NoNewlinesStream()
        wrapper = _MCPSafeStdout(real_stdout, stderr_no_newlines)  # type: ignore[arg-type]
        assert wrapper.newlines is None

    def test_readable_returns_false(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)
        assert wrapper.readable() is False

    def test_writable_returns_true(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)
        assert wrapper.writable() is True

    def test_seekable_returns_false(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)
        assert wrapper.seekable() is False

    def test_close_does_not_close_underlying_streams(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        wrapper.close()

        # Neither side should be closed by the wrapper — that would
        # tear down the JSON-RPC channel and the diagnostic stream.
        assert real_stdout.close_count == 0
        assert stderr.close_count == 0
        assert real_stdout.closed is False
        assert stderr.closed is False


# =============================================================================
# 6. __getattr__ fallback to stderr
# =============================================================================


class TestGetattrFallback:
    """Unknown attributes on the wrapper should fall through to stderr."""

    def test_unknown_attribute_lands_on_stderr(self):
        real_stdout, stderr = _make_pair()
        # Attach a custom attribute to stderr so we can verify the
        # fallback retrieves it.
        stderr.custom_marker = "from-stderr"  # type: ignore[attr-defined]
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        assert wrapper.custom_marker == "from-stderr"  # type: ignore[attr-defined]

    def test_unknown_method_calls_stderr_method(self):
        # ``tell`` is not defined on the wrapper, so it must come
        # from stderr. A real ``io.StringIO`` exposes ``tell`` and
        # returns the current write position, which is the cleanest
        # way to assert the delegation.
        real_stdout, stderr = _make_pair()
        real_so = io.StringIO()
        err_so = io.StringIO()
        err_so.write("xyz")
        err_so.seek(2)
        wrapper = _MCPSafeStdout(real_so, err_so)  # type: ignore[arg-type]

        assert wrapper.tell() == 2  # type: ignore[attr-defined]

    def test_attribute_missing_on_stderr_raises_attribute_error(self):
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        with pytest.raises(AttributeError):
            _ = wrapper.this_attribute_truly_does_not_exist  # type: ignore[attr-defined]

    def test_detach_raises_attribute_error(self):
        """``detach()`` must NOT fall through to stderr — it would detach
        the wrong stream. The wrapper must raise ``AttributeError`` with a
        message that names the method so callers can diagnose the issue."""
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        with pytest.raises(AttributeError) as excinfo:
            wrapper.detach()  # type: ignore[attr-defined]

        # The error message must name the method so the caller knows
        # which unsupported call to replace.
        assert "detach" in str(excinfo.value)

    def test_reconfigure_raises_attribute_error(self):
        """``reconfigure()`` must NOT fall through to stderr — it would
        reconfigure the wrong stream. The wrapper must raise
        ``AttributeError`` with a message that names the method."""
        real_stdout, stderr = _make_pair()
        wrapper = _MCPSafeStdout(real_stdout, stderr)

        with pytest.raises(AttributeError) as excinfo:
            wrapper.reconfigure()  # type: ignore[attr-defined]

        assert "reconfigure" in str(excinfo.value)


# =============================================================================
# 7. install_safe_stdout() — idempotency and sys.stdout wiring
# =============================================================================


class TestInstallSafeStdout:
    """``install_safe_stdout()`` must wrap ``sys.stdout`` exactly once."""

    def test_first_call_wraps_sys_stdout(self):
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        try:
            result = install_safe_stdout()
            assert isinstance(sys.stdout, _MCPSafeStdout)
            assert result is sys.stdout
            # The wrapper keeps a reference to the *original* stdout.
            assert result._real is original_stdout
            assert result._stderr is original_stderr
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    def test_second_call_is_a_noop(self):
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        try:
            first = install_safe_stdout()
            assert isinstance(sys.stdout, _MCPSafeStdout)
            assert first is sys.stdout

            # Second call: must NOT replace the existing wrapper.
            second = install_safe_stdout()
            assert second is None
            assert sys.stdout is first
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    def test_idempotent_under_pytest_capture(self):
        """Even when pytest has replaced ``sys.stdout`` (so it is no
        longer a plain ``io.TextIOBase``), the wrapper still installs
        and a second call is still a no-op."""
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        try:
            # Force an unusual (non-TextIOBase) stdout, like pytest's
            # capture object.
            sentinel = object()
            sys.stdout = sentinel  # type: ignore[assignment]
            sys.stderr = object()  # type: ignore[assignment]

            first = install_safe_stdout()
            assert isinstance(sys.stdout, _MCPSafeStdout)
            assert first is sys.stdout
            # The wrapper holds onto the sentinel as the "real" stdout.
            assert first._real is sentinel

            # Idempotency holds even with a non-standard stream.
            second = install_safe_stdout()
            assert second is None
            assert sys.stdout is first
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


# =============================================================================
# Bootstrap launcher (argparse + _main)
# =============================================================================


class TestParseArgs:
    """``_parse_args`` only accepts a single ``target`` positional."""

    def test_target_is_parsed(self):
        ns = _parse_args(["daemon.mcp.kb_server"])
        assert ns.target == "daemon.mcp.kb_server"

    def test_missing_target_exits(self):
        with pytest.raises(SystemExit):
            _parse_args([])


class TestMain:
    """``_main`` installs the wrapper, runs the target, returns a code."""

    def test_runs_target_module_via_runpy(self):
        with patch("daemon.mcp.safe_stdout.install_safe_stdout") as mock_install, \
             patch("daemon.mcp.safe_stdout.runpy.run_module") as mock_run:
            mock_run.return_value = None  # no exception

            rc = _main(["some.module"])

        assert rc == 0
        mock_install.assert_called_once_with()
        mock_run.assert_called_once_with("some.module", run_name="__main__")

    def test_module_not_found_returns_code_2(self):
        with patch("daemon.mcp.safe_stdout.install_safe_stdout"), \
             patch(
                 "daemon.mcp.safe_stdout.runpy.run_module",
                 side_effect=ImportError("No module named 'nope'"),
             ):
            rc = _main(["nope"])

        assert rc == 2

    def test_unexpected_exception_returns_code_1(self):
        with patch("daemon.mcp.safe_stdout.install_safe_stdout"), \
             patch(
                 "daemon.mcp.safe_stdout.runpy.run_module",
                 side_effect=RuntimeError("boom"),
             ):
            rc = _main(["weird.module"])

        assert rc == 1

    def test_system_exit_code_is_propagated(self):
        with patch("daemon.mcp.safe_stdout.install_safe_stdout"), \
             patch(
                 "daemon.mcp.safe_stdout.runpy.run_module",
                 side_effect=SystemExit(7),
             ):
            rc = _main(["exitseven.module"])

        assert rc == 7

    def test_system_exit_with_no_code_returns_zero(self):
        with patch("daemon.mcp.safe_stdout.install_safe_stdout"), \
             patch(
                 "daemon.mcp.safe_stdout.runpy.run_module",
                 side_effect=SystemExit(),
             ):
            rc = _main(["silent.module"])

        assert rc == 0

    def test_keyboard_interrupt_is_reraised(self):
        """KeyboardInterrupt from target must propagate, not be swallowed.

        Regression test: ``except BaseException`` was catching KeyboardInterrupt
        (which inherits from BaseException, not Exception) and converting
        Ctrl-C to exit code 1. The fix moves KeyboardInterrupt above the
        generic Exception handler and re-raises it so it propagates.
        """
        with patch("daemon.mcp.safe_stdout.install_safe_stdout"), \
             patch(
                 "daemon.mcp.safe_stdout.runpy.run_module",
                 side_effect=KeyboardInterrupt(),
             ):
            with pytest.raises(KeyboardInterrupt):
                _main(["interrupted.module"])

    def test_system_exit_with_string_code_does_not_crash(self):
        """SystemExit with string code (e.g. ``raise SystemExit("fatal")``)
        must return exit code 1 and write the message to stderr — NOT
        crash with a ValueError from ``int("fatal")`` inside the handler.

        Regression test: the old code did ``int(exc.code)`` blindly, which
        raises ``ValueError`` for string codes. The fix checks ``isinstance``
        and prints the string to stderr with exit code 1.
        """
        with patch("daemon.mcp.safe_stdout.install_safe_stdout"), \
             patch(
                 "daemon.mcp.safe_stdout.runpy.run_module",
                 side_effect=SystemExit("fatal error from module"),
             ):
            rc = _main(["stringy_exit.module"])

        assert rc == 1


# =============================================================================
# 8. Integration: real subprocess exercises the launcher end-to-end
# =============================================================================


# A minimal dummy stdio "MCP" server. The first ``print`` is exactly the
# kind of stray diagnostic that would corrupt a real JSON-RPC stream.
# The second line writes a valid frame directly through ``sys.stdout.buffer``
# — that is the channel the wrapper must preserve.
_DUMMY_SERVER_SOURCE = textwrap.dedent(
    """\
    import sys
    # This is the stray print that breaks the JSON-RPC stream.
    print("corrupting output")
    # This is a valid JSON-RPC frame the parent must be able to read.
    sys.stdout.buffer.write(b'{"jsonrpc":"2.0","id":1,"result":"ok"}\\n')
    sys.stdout.buffer.flush()
    """
)


def _write_dummy_module(tmp_path: Path, module_name: str) -> Path:
    """Drop ``_DUMMY_SERVER_SOURCE`` into a file under ``tmp_path``."""
    module_path = tmp_path / f"{module_name}.py"
    module_path.write_text(_DUMMY_SERVER_SOURCE)
    return module_path


def _run_python(
    *args: str,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess:
    """Run ``python3`` with the given args, capturing stdout and stderr."""
    return subprocess.run(  # noqa: S603 — args are controlled by the test
        [sys.executable, *args],
        capture_output=True,
        cwd=cwd,
        env=env,
        timeout=timeout,
    )


class TestSubprocessLauncherEndToEnd:
    """Real subprocess proves the wrapper protects the JSON-RPC stream."""

    def test_direct_run_corrupts_stdout(self, tmp_path: Path):
        """Sanity check: running the dummy directly mixes the print
        text and the JSON-RPC bytes on the same stdout stream."""
        module_path = _write_dummy_module(tmp_path, "dummy_server_direct")
        # ``python3 <file>.py`` runs the file directly; pass the
        # full path so the lookup is unambiguous.
        proc = _run_python(str(module_path))
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")

        stdout = proc.stdout

        # The corrupting text is on stdout — exactly the broken state
        # we are protecting against.
        assert b"corrupting output" in stdout
        # And so is the JSON-RPC frame.
        assert b'{"jsonrpc":"2.0","id":1,"result":"ok"}' in stdout

    def test_safe_stdout_launcher_keeps_stdout_clean(self, tmp_path: Path):
        """Running the same script via the launcher must leave stdout
        with ONLY the JSON-RPC bytes; the stray print goes to stderr."""
        module_name = "dummy_server_wrapped"
        _write_dummy_module(tmp_path, module_name)

        # Need both the project root (so ``daemon`` is importable) and
        # the tmp dir (so the dummy module is importable) on PYTHONPATH.
        repo_root = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
        )
        # Also add the repo root in case the test is launched from
        # a different working directory.
        existing = env["PYTHONPATH"]
        env["PYTHONPATH"] = (
            str(repo_root) + os.pathsep + str(tmp_path) + os.pathsep + existing
        )

        proc = _run_python(
            "-m",
            "daemon.mcp.safe_stdout",
            module_name,
            cwd=tmp_path,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")

        stdout = proc.stdout
        stderr = proc.stderr

        # The corrupting text must NOT appear on stdout.
        assert b"corrupting output" not in stdout, (
            f"stdout was corrupted by stray print: {stdout!r}"
        )
        # The valid JSON-RPC frame must be present on stdout.
        assert b'{"jsonrpc":"2.0","id":1,"result":"ok"}' in stdout
        # And the stray print must have been diverted to stderr.
        assert b"corrupting output" in stderr

    def test_safe_stdout_launcher_module_not_found_returns_code_2(
        self, tmp_path: Path
    ):
        """Unknown target must exit with code 2 (and an error on stderr)."""
        repo_root = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root)

        proc = _run_python(
            "-m",
            "daemon.mcp.safe_stdout",
            "definitely_not_a_real_module_xyz",
            cwd=tmp_path,
            env=env,
        )

        assert proc.returncode == 2
        assert b"module not found" in proc.stderr
