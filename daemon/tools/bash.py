"""Bash execution tool for running shell commands."""

import asyncio
import os
import signal
import sys
import tempfile
from langchain_core.tools import tool
from typing import List

from ._tool_registry import register_tool_category

CATEGORY_NAME = "Shell"
CATEGORY_DOC = """\
Execute shell commands.

**Rules**:
- Always set `workdir` to the project directory. Never omit it.
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
            except (ProcessLookupError, PermissionError):
                pass
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            if is_unix:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            else:
                proc.kill()
            await proc.wait()
    except ProcessLookupError:
        pass


@register_tool_category("bash")
@tool
async def bash(
    command: str | List[str],
    timeout: int | float | None = 1800,
    workdir: str | None = None,
    input: str | None = None,
) -> str:
    """Execute a bash command and return the output. Timeout is in seconds (0-1800, default 1800). Use tool_help("bash") for details."""
    # Validate timeout parameter
    if timeout is not None:
        if timeout < 0:
            return f"ERROR: Timeout must be ≥ 0 seconds. Got: {timeout}s"
        if timeout > 1800:
            return f"ERROR: Timeout must be ≤ 1800 seconds. Got: {timeout}s"
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
        # We use NamedTemporaryFile with delete=False so we can pass the path
        # to the subprocess and unlink it ourselves after reading.
        # Files are opened in read+write binary mode (O_RDWR) so the child
        # shell can write to them while the parent still holds a read-capable
        # handle. (PIPE-style: child writes, parent reads after wait().)
        stdout_fd, stdout_path = tempfile.mkstemp(prefix="bash-stdout-", suffix=".tmp")
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="bash-stderr-", suffix=".tmp")
        # Close the parent's raw fds; we'll reopen via Python file objects.
        os.close(stdout_fd)
        os.close(stderr_fd)

        stdout_file = open(stdout_path, "w+b")
        stderr_file = open(stderr_path, "w+b")

        # If input is provided, write it to a temp file and pass that to the
        # child's stdin (so the shell can read it without blocking on a pipe).
        stdin_file = None
        stdin_path = None
        if input:
            stdin_fd, stdin_path = tempfile.mkstemp(prefix="bash-stdin-", suffix=".tmp")
            os.write(stdin_fd, input.encode())
            os.close(stdin_fd)
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

        try:
            # timeout=0 means "no timeout" — pass None to wait_for
            actual_timeout = None if timeout == 0 else timeout
            await asyncio.wait_for(proc.wait(), timeout=actual_timeout)
        except asyncio.TimeoutError:
            await _kill_process(proc)
            # Best-effort: still try to read whatever was written before the kill.
            try:
                with open(stdout_path, "rb") as f:
                    stdout_bytes = f.read()
            except OSError:
                stdout_bytes = b""
            try:
                with open(stderr_path, "rb") as f:
                    stderr_bytes = f.read()
            except OSError:
                stderr_bytes = b""
            stdout_str = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
            stderr_str = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
            content = ""
            if stdout_str:
                content += f"STDOUT:\n{stdout_str}\n\n"
            if stderr_str:
                content += f"STDERR:\n{stderr_str}\n\n"
            return content + f"ERROR: Command timed out after {timeout} seconds"

        # Read captured output from the temp files.
        try:
            with open(stdout_path, "rb") as f:
                stdout_bytes = f.read()
        except OSError:
            stdout_bytes = b""
        try:
            with open(stderr_path, "rb") as f:
                stderr_bytes = f.read()
        except OSError:
            stderr_bytes = b""

        stdout_str = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr_str = stderr_bytes.decode(errors="replace") if stderr_bytes else ""

        output_parts = []

        if stdout_str:
            output_parts.append(f"STDOUT:\n{stdout_str}")

        if stderr_str:
            output_parts.append(f"STDERR:\n{stderr_str}")

        output_parts.append(f"EXIT CODE: {proc.returncode}")

        content = "\n\n".join(output_parts)

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

    except Exception as e:
        return f"ERROR: {str(e)}"
    finally:
        # Clean up temp files.
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
