"""Done tool for signaling task completion."""

from langchain_core.tools import tool


@tool
def done() -> str:
    """Task complete. Use this when your assigned task is finished."""
    return '{"status": "done"}'
