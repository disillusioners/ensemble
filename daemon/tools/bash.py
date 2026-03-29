"""Bash execution tool for running shell commands."""

import subprocess
from langchain_core.tools import tool
from typing import Optional, Union, List


@tool
def bash(
    command: Union[str, List[str]],
    timeout: Optional[int] = 1800,
    workdir: Optional[str] = None,
    input: Optional[str] = None,
) -> str:
    """Execute a bash command and return the output. Use tool_help("bash") for details."""
    try:
        if isinstance(command, list):
            # Use exec (no shell) when command is a list
            result = subprocess.run(
                command,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
                input=input,
            )
        else:
            # Fall back to shell for string commands
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
                input=input,
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
    command: The bash command to execute. Can be:
        - A string (interpreted as shell command with shell=True)
        - A list of strings like ["ls", "-la", "path"] (no shell interpretation)
    timeout: Timeout in seconds (default: 1800, 30 minutes)
    workdir: Working directory for command execution (default: current directory)
    input: Optional string to pass to stdin

Returns:
    Command output including stdout, stderr, and exit code
"""
