"""Bash execution tool for running shell commands."""

import asyncio
import logging
import os
import signal
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List

from langchain_core.tools import tool
from pydantic import BaseModel

from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)


@dataclass
class BashProcessEntry:
    """A bash subprocess whose PID and PGID were captured at spawn."""

    pid: int
    pgid: int


class BashProcessRegistry:
    """Track bash-spawned subprocess groups by owning instance ID."""

    def __init__(self) -> None:
        self._entries: Dict[str, List[BashProcessEntry]] = {}
        self._lock = asyncio.Lock()

    async def register(self, instance_id: str, pid: int, pgid: int) -> None:
        """Register a bash subprocess under its owning instance."""
        async with self._lock:
            self._entries.setdefault(instance_id, []).append(
                BashProcessEntry(pid=pid, pgid=pgid)
            )

    async def unregister(self, instance_id: str, pid: int) -> None:
        """Remove one subprocess after an explicit ``_kill_process`` call."""
        async with self._lock:
            entries = self._entries.get(instance_id)
            if not entries:
                return
            self._entries[instance_id] = [
                entry for entry in entries if entry.pid != pid
            ]
            if not self._entries[instance_id]:
                del self._entries[instance_id]

    async def cleanup_instance(self, instance_id: str) -> int:
        """Kill all tracked process groups for one instance."""
        async with self._lock:
            entries = self._entries.pop(instance_id, [])

        killed = 0
        for entry in entries:
            try:
                self._kill_group(entry.pgid)
                killed += 1
            except Exception as e:
                logger.warning(
                    f"bash cleanup: killpg({entry.pgid}) failed: "
                    f"{type(e).__name__}: {e}"
                )
        return killed

    async def cleanup_all(self) -> int:
        """Kill every tracked bash process group during daemon shutdown.

        Known limitations:

        * Truly-detached orphans whose child called ``setsid`` sit outside
          the original process group, so ``killpg`` cannot reach them.
        * Crash-recovery leak: this in-memory registry does not survive a
          daemon restart, so processes from a hard crash cannot be enumerated.
        """
        async with self._lock:
            instance_ids = list(self._entries.keys())

        total = 0
        for iid in instance_ids:
            try:
                total += await self.cleanup_instance(iid)
            except Exception as e:
                logger.warning(
                    f"bash cleanup_all: failed for {iid[:8]}: "
                    f"{type(e).__name__}: {e}"
                )
        if total:
            logger.info(f"bash cleanup_all: killed {total} process(es)")
        return total

    @staticmethod
    def _kill_group(pgid: int) -> None:
        """SIGKILL a process group, with a Windows task-tree fallback."""
        if sys.platform != "win32":
            os.killpg(pgid, signal.SIGKILL)
        else:
            import subprocess

            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pgid)],
                capture_output=True,
            )


_bash_process_registry: "BashProcessRegistry | None" = None


def get_bash_process_registry() -> BashProcessRegistry:
    """Return the process-wide bash process registry singleton."""
    global _bash_process_registry
    if _bash_process_registry is None:
        _bash_process_registry = BashProcessRegistry()
    return _bash_process_registry

CATEGORY_NAME = "Shell"
CATEGORY_DOC = """\
Execute shell commands.

**Rules**:
- Always set `workdir` to the project directory. Never omit it.
- If you need to access files outside the project directory, do so by passing
  explicit absolute paths inside the command (e.g. `/abs/path/to/file`) — the
  `workdir` setting still applies as the command's `cwd`.
- Avoid blocking commands (e.g., `tail -f`, `watch`, interactive editors) — except via skills that use CLI.
- For large output, redirect to file and read with `read_file`:
  ```
  command > /tmp/output.txt && echo "Done"
  ```
"""


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Gracefully kill a process: SIGTERM, wait 5s, then SIGKILL.

    On Unix, kills the entire process group so backgrounded children
    (e.g., `nohup ... &`) are terminated too. On Windows, falls back
    to killing the immediate process.
    """
    is_unix = sys.platform != "win32"
    try:
        if is_unix:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except OSError:
                pass
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            if is_unix:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    pass
            else:
                proc.kill()
            await proc.wait()
    except OSError:
        pass


def _read_file_bytes(path):
    """Read file contents as bytes; return b'' if path is None or read fails."""
    if path is None:
        return b""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


class BashInputSchema(BaseModel):
    """LLM-visible arguments for the bash tool."""

    command: str | List[str]
    timeout: int | float | None = 1800
    workdir: str | None = None
    input: str | None = None


@register_tool_category("bash")
# ``instance_id`` is runtime-injected ownership metadata and must not be
# controllable through LLM-visible tool arguments.
@tool(args_schema=BashInputSchema)
async def bash(
    command: str | List[str],
    timeout: int | float | None = 1800,
    workdir: str | None = None,
    input: str | None = None,
    instance_id: str | None = None,
) -> str:
    """Execute a bash command and return the output. Timeout is in seconds (0-1800, default 1800). Use tool_help("bash") for details."""
    # Validate timeout parameter
    if timeout is not None:
        if timeout < 0:
            return f"ERROR: Timeout must be ≥ 0 seconds. Got: {timeout}s"
        if timeout > 1800:
            return f"ERROR: Timeout must be ≤ 1800 seconds. Got: {timeout}s"
    # Initialize temp-file variables before the try block so the function-level
    # finally can safely close/unlink them even if an exception occurs mid-setup
    # (e.g., the second mkstemp fails after the first succeeds).
    stdout_path = None
    stderr_path = None
    stdin_path = None
    stdout_file = None
    stderr_file = None
    stdin_file = None
    proc: asyncio.subprocess.Process | None = None
    try:
        # start_new_session=True (Unix) creates a new process group so that
        # backgrounded children (e.g., `nohup ... &`) can be killed together
        # with the parent if we time out. Not supported on Windows.
        #
        # Output capture uses temp FILES (not pipes) to avoid the
        # backgrounded-child-keeps-pipe-open hang. When a shell spawns
        # `sleep 10 &`, the backgrounded grandchild inherits the shell's
        # stdout/stderr FDs. With pipes, that grandchild keeps the pipe
        # write end open, and `proc.communicate()` waits forever for EOF.
        # With regular files, the grandchild still inherits the FDs, but
        # nothing waits for the file to be "closed" — we just read the
        # file's contents after the shell exits. The grandchild holding
        # the file FD open is harmless.
        subproc_kwargs: dict = {}
        if sys.platform != "win32":
            subproc_kwargs["start_new_session"] = True

        # Open temp files for stdout and stderr capture.
        # Use tempfile.mkstemp so we get the fd and path directly. We close the
        # raw fd and reopen via Python file objects, then pass those to the
        # subprocess and unlink ourselves after reading.
        # Files are opened in read+write binary mode (O_RDWR) so the child
        # shell can write to them while the parent still holds a read-capable
        # handle.
        # w+b: binary mode since asyncio.subprocess writes raw bytes.
        stdout_fd, stdout_path = tempfile.mkstemp(prefix="bash-stdout-", suffix=".tmp")
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="bash-stderr-", suffix=".tmp")
        # Close the parent's raw fds; we'll reopen via Python file objects.
        os.close(stdout_fd)
        os.close(stderr_fd)

        stdout_file = open(stdout_path, "w+b")
        stderr_file = open(stderr_path, "w+b")

        # If input is provided, write it to a temp file and pass that to the
        # child's stdin (so the shell can read it without blocking on a pipe).
        if input:
            stdin_fd, stdin_path = tempfile.mkstemp(prefix="bash-stdin-", suffix=".tmp")
            # Use a Python file object so partial writes are handled internally.
            with os.fdopen(stdin_fd, 'wb') as f:
                f.write(input.encode())
            stdin_file = open(stdin_path, "rb")

        try:
            stdin_arg = stdin_file if stdin_file is not None else asyncio.subprocess.DEVNULL
            if isinstance(command, list):
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=stdin_arg,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=workdir,
                    **subproc_kwargs,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdin=stdin_arg,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=workdir,
                    **subproc_kwargs,
                )
        finally:
            # Parent must close its copies — the child has its own handles via
            # the inherited FDs. Closing the parent's copies ensures that when
            # the child also exits, the file is fully released.
            stdout_file.close()
            stderr_file.close()
            if stdin_file is not None:
                stdin_file.close()

        # REGISTER after spawn (always) — D4: eager PGID capture
        if instance_id is not None and proc is not None:
            try:
                pgid = os.getpgid(proc.pid)
            except (ProcessLookupError, OSError):
                pgid = proc.pid  # start_new_session=True → PGID == PID
            await get_bash_process_registry().register(instance_id, proc.pid, pgid)
        elif instance_id is None:
            logger.warning("bash: instance_id is None; skipping process registration")

        # timeout=0 means "no timeout" — pass None to wait_for
        actual_timeout = None if timeout == 0 else timeout
        try:
            await asyncio.wait_for(proc.wait(), timeout=actual_timeout)
            timed_out = False
        except asyncio.TimeoutError:
            await _kill_process(proc)
            if instance_id is not None:
                await get_bash_process_registry().unregister(instance_id, proc.pid)
            timed_out = True

        # Read captured output from the temp files (best-effort: also try on
        # timeout to surface whatever was written before the kill).
        stdout_bytes = _read_file_bytes(stdout_path)
        stderr_bytes = _read_file_bytes(stderr_path)
        stdout_str = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr_str = stderr_bytes.decode(errors="replace") if stderr_bytes else ""

        output_parts = []
        if stdout_str:
            output_parts.append(f"STDOUT:\n{stdout_str}")
        if stderr_str:
            output_parts.append(f"STDERR:\n{stderr_str}")
        if not timed_out:
            output_parts.append(f"EXIT CODE: {proc.returncode}")
        content = "\n\n".join(output_parts)

        if timed_out:
            return content + f"ERROR: Command timed out after {timeout} seconds"

        # Apply character limit only (no line limit)
        if len(content) > 150000:
            truncated = content[:150000] + "\n\n--- OUTPUT TRUNCATED ---"
            hint = """
**⚠️ Output truncated at 150,000 characters.**

For full output, redirect to file:
  `command > /tmp/output.txt` then use `read_file`
"""
            return truncated + hint
        return content

    except asyncio.CancelledError:
        # Cancellation at EITHER await point (spawn or wait_for).
        if proc is not None:
            # Clear sticky cancellation so _kill_process's internal awaits
            # don't immediately re-raise CancelledError (Python 3.11+).
            task = asyncio.current_task()
            if task is not None and hasattr(task, "uncancel"):
                task.uncancel()
            try:
                # shield protects against concurrent second cancel
                await asyncio.shield(_kill_process(proc))
                if instance_id is not None:
                    await asyncio.shield(
                        get_bash_process_registry().unregister(instance_id, proc.pid)
                    )
            except BaseException:
                pass  # best-effort during cancellation
        raise  # ALWAYS re-propagate the cancellation
    except Exception as e:
        return f"ERROR: {str(e)}"
    finally:
        # Clean up temp files.
        for f in (stdout_file, stderr_file, stdin_file):
            if f is None:
                continue
            try:
                f.close()
            except OSError:
                pass
        for path in (stdout_path, stderr_path, stdin_path):
            if path is None:
                continue
            try:
                os.unlink(path)
            except OSError:
                pass

bash._full_doc_ = """Execute a bash command and return the output.

**Agent Guidelines:**
- Avoid blocking/long-running commands (e.g., `tail -f`, `watch`, interactive editors) — except via skills that use CLI.
- If output may be large or truncated, write to a file and use `read_file` instead.
- Example for large output: `command > /tmp/output.txt && echo "Saved to /tmp/output.txt"`

Args:
    command: The bash command to execute. Can be:
        - A string (interpreted as shell command with shell=True)
        - A list of strings like ["ls", "-la", "path"] (no shell interpretation)
    timeout: Maximum time to wait (in seconds). Must be 0-1800. Default: 1800 (30 minutes)
    workdir: Working directory for command execution (default: current directory)
    input: Optional string to pass to stdin

Returns:
    Command output including stdout, stderr, and exit code.
    Output is truncated at 150,000 characters — redirect to file for full results.
"""
