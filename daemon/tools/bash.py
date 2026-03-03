"""Bash execution tool for running shell commands."""

import subprocess
from langchain_core.tools import tool
from typing import Optional


@tool
def bash(
    command: str,
    timeout: Optional[int] = 1800,
    workdir: Optional[str] = None
) -> str:
    """Execute a bash command and return the output. Use tool_help("bash") for details."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir
        )
        
        output_parts = []
        
        if result.stdout:
            output_parts.append(f"STDOUT:\n{result.stdout}")
        
        if result.stderr:
            output_parts.append(f"STDERR:\n{result.stderr}")
        
        output_parts.append(f"EXIT CODE: {result.returncode}")
        
        return "\n\n".join(output_parts)
        
    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after {timeout} seconds"
    except Exception as e:
        return f"ERROR: {str(e)}"

bash._full_doc_ = """Execute a bash command and return the output.

Args:
    command: The bash command to execute
    timeout: Timeout in seconds (default: 1800, 30 minutes)
    workdir: Working directory for command execution (default: current directory)

Returns:
    Command output including stdout, stderr, and exit code
"""
