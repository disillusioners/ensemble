"""Message source adapters for external platforms."""

from .telegram import TelegramAdapter
from .scheduler import SchedulerAdapter

__all__ = ["TelegramAdapter", "SchedulerAdapter"]
