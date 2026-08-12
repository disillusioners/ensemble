"""Message source adapters for external platforms."""

from .telegram import TelegramAdapter
from .scheduler import SchedulerAdapter
from .slack import SlackAdapter
from .discord import DiscordAdapter

__all__ = ["TelegramAdapter", "SchedulerAdapter", "SlackAdapter", "DiscordAdapter"]
