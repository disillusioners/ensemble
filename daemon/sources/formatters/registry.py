"""Formatter registry mapping source types to OutputFormatter instances."""

from __future__ import annotations

from daemon.sources.formatters.base import OutputFormatter


class _PassthroughFormatter(OutputFormatter):
    """A formatter that returns its input text unchanged.

    Used as a default when no formatter is registered for a given source type.
    """

    def format(self, text: str) -> str:
        """Return the input text without modification.

        Args:
            text: Text to pass through.

        Returns:
            The input text unchanged.
        """
        return text


# Module-level registry mapping source_type -> OutputFormatter instance.
_registry: dict[str, OutputFormatter] = {}


def register(source_type: str, formatter: OutputFormatter) -> None:
    """Register a formatter for a given source type.

    If a formatter is already registered for the given source type, it will be
    replaced. Passing ``None`` removes any existing registration.

    Args:
        source_type: Identifier of the source (e.g., ``"slack"``).
        formatter: The formatter instance to register, or ``None`` to clear.
    """
    if formatter is None:
        _registry.pop(source_type, None)
        return
    _registry[source_type] = formatter


def get(source_type: str) -> OutputFormatter | None:
    """Return the registered formatter for ``source_type`` or ``None``.

    Args:
        source_type: Identifier of the source to look up.

    Returns:
        The registered ``OutputFormatter`` instance, or ``None`` if not found.
    """
    return _registry.get(source_type)


def get_or_passthrough(source_type: str) -> OutputFormatter:
    """Return the registered formatter, or a passthrough if not registered.

    The returned passthrough is a singleton-safe ``_PassthroughFormatter``
    that returns text unchanged.

    Args:
        source_type: Identifier of the source to look up.

    Returns:
        A registered ``OutputFormatter`` or a passthrough formatter instance.
    """
    formatter = _registry.get(source_type)
    if formatter is None:
        return _PassthroughFormatter()
    return formatter


# Auto-register built-in formatters on import.
# We import lazily to avoid circular import issues during package init.
from daemon.sources.formatters.slack import SlackMrkdwnFormatter  # noqa: E402

register("slack", SlackMrkdwnFormatter())
