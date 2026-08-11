"""Discord adapter package.

Exports :class:`DiscordAdapter`, the canonical entry point for the
registry, mapper, router, and tests.
"""

from __future__ import annotations

from .adapter import DiscordAdapter, DiscordAPIError

__all__ = ["DiscordAdapter", "DiscordAPIError"]
