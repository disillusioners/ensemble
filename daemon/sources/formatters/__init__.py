"""Source-specific output formatters package.

This module provides a registry-based system for converting standard Markdown
output (from LLM) into source-specific formats (e.g., Slack mrkdwn).

Public API:
    OutputFormatter      - Abstract base class for all formatters
    register             - Register a formatter for a source type
    get                  - Lookup a formatter by source type
    get_or_passthrough   - Lookup formatter or return a passthrough
"""

from __future__ import annotations

from daemon.sources.formatters.base import OutputFormatter
from daemon.sources.formatters.registry import register, get, get_or_passthrough

__all__ = ["OutputFormatter", "register", "get", "get_or_passthrough"]
