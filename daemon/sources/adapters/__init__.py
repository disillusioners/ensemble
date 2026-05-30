"""Message source adapters for external platforms."""

from .telegram import TelegramAdapter
from .scheduler import SchedulerAdapter
from .slack import SlackAdapter

__all__ = ["TelegramAdapter", "SchedulerAdapter", "SlackAdapter"]
