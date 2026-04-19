"""Bash execution tool for running shell commands."""

import asyncio
import signal
from langchain_core.tools import tool
from typing import Optional, Union, List

from ._tool_registry import register_tool_category
from ._truncate import truncate_output

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
    """Gracefully kill a process: SIGTERM, wait 5s, then SIGKILL."""
    try:
        proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    except ProcessLookupError:
        pass


@register_tool_category("bash")
@tool
async def bash(
    command: Union[str, List[str]],
    timeout: Optional[int] = 1800,
    workdir: Optional[str] = None,
    input: Optional[str] = None,
    max_output_chars: int = 10000,
) -> str:
    """Execute a bash command and return the output. Use tool_help("bash") for details."""
    try:
        if isinstance(command, list):
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if input else asyncio.subprocess.DEVNULL,
                cwd=workdir,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if input else asyncio.subprocess.DEVNULL,
                cwd=workdir,
            )

        try:
            stdin_bytes = input.encode() if input else None
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await _kill_process(proc)
            return f"ERROR: Command timed out after {timeout} seconds"

        stdout_str = stdout_bytes.decode() if stdout_bytes else ""
        stderr_str = stderr_bytes.decode() if stderr_bytes else ""

        output_parts = []

        if stdout_str:
            output_parts.append(f"STDOUT:\n{stdout_str}")

        if stderr_str:
            output_parts.append(f"STDERR:\n{stderr_str}")

        output_parts.append(f"EXIT CODE: {proc.returncode}")

        content = "\n\n".join(output_parts)

        # Apply truncation to prevent 413 errors
        # Note: 6000 chars is the recommended limit for LLM context safety
        trunc_result = truncate_output(
            content,
            tool_name="bash",
            max_chars=max_output_chars,
            max_lines=100,
        )

        if trunc_result.truncated:
            return trunc_result.content + trunc_result.pagination_hint
        return content

    except Exception as e:
        return f"ERROR: {str(e)}"

bash._full_doc_ = """Execute a bash command and return the output.

**Agent Guidelines:**
- Avoid blocking/long-running commands (e.g., `tail -f`, `watch`, interactive editors) — except via skills that use CLI.
- If output may be large or truncated, write to a file and use `read_file` instead.
- Example for large output: `command > /tmp/output.txt && echo "Saved to /tmp/output.txt"`

Args:
    command: The bash command to execute. Can be:
        - A string (interpreted as shell command with shell=True)
        - A list of strings like ["ls", "-la", "path"] (no shell interpretation)
    timeout: Timeout in seconds (default: 1800, 30 minutes)
    workdir: Working directory for command execution (default: current directory)
    input: Optional string to pass to stdin
    max_output_chars: Maximum output characters to capture (default: 10000, max: 50000)

Returns:
    Command output including stdout, stderr, and exit code.
    Large output may be truncated — write to file for full results.
"""
