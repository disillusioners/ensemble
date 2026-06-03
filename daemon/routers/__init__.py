"""Daemon routers module."""

from .agents import router as agents_router
from .instances import router as instances_router
from .jobs import router as jobs_router
from .messages import router as messages_router
from .mappings import router as mappings_router
from .schedules import router as schedules_router
from .sources import router as sources_router
from .webhooks import router as webhooks_router
from .projects import router as projects_router
from .queues import router as queues_router
from .dlq import router as dlq_router
from .mcp_servers import router as mcp_servers_router
from .notifications import router as notifications_router
from .migration import router as migration_router

__all__ = [
    "agents_router",
    "instances_router",
    "jobs_router",
    "messages_router",
    "mappings_router",
    "schedules_router",
    "sources_router",
    "webhooks_router",
    "projects_router",
    "queues_router",
    "dlq_router",
    "mcp_servers_router",
    "notifications_router",
    "migration_router",
]
