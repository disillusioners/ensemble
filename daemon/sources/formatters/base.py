"""Base class for source-specific output formatters."""

from __future__ import annotations

from abc import ABC, abstractmethod


class OutputFormatter(ABC):
    """Base class for source-specific output formatters.

    A formatter transforms standard Markdown text produced by the LLM into a
    format suitable for a specific message source (e.g., Slack mrkdwn).
    Implementations should be stateless and safe to reuse.
    """

    @abstractmethod
    def format(self, text: str) -> str:
        """Transform standard Markdown text to source-specific format.

        Args:
            text: Standard Markdown text (from LLM output)

        Returns:
            Transformed text in source-specific format
        """
        ...
