"""MCP Server Repository implementation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select, col

from .models import McpServer


logger = logging.getLogger(__name__)


class SQLModelMcpServerRepository:
    """SQLModel-based MCP Server repository for CRUD operations."""

    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    def create_mcp_server(
        self,
        name: str,
        description: str | None = None,
        config: dict[str, Any] | None = None,
        is_active: bool = True,
    ) -> McpServer:
        """Create a new MCP server configuration.

        Args:
            name: Unique server name.
            description: Optional description.
            config: Server configuration dictionary.
            is_active: Whether the server is active.

        Returns:
            Created McpServer instance.
        """
        with Session(self.engine) as session:
            now = datetime.now(timezone.utc).isoformat()

            mcp_server = McpServer(
                id=str(uuid.uuid4()),
                name=name,
                description=description,
                config=config or {},
                is_active=is_active,
                created_at=now,
                updated_at=None,
            )

            session.add(mcp_server)
            session.commit()
            session.refresh(mcp_server)

            logger.info(f"Created MCP server: id={mcp_server.id}, name={name}")
            return mcp_server

    def get_mcp_server(self, server_id: str) -> McpServer | None:
        """Get an MCP server by ID.

        Args:
            server_id: The server ID.

        Returns:
            McpServer instance or None if not found.
        """
        with Session(self.engine) as session:
            return session.get(McpServer, server_id)

    def get_mcp_server_by_name(self, name: str) -> McpServer | None:
        """Get an MCP server by name.

        Args:
            name: The server name.

        Returns:
            McpServer instance or None if not found.
        """
        with Session(self.engine) as session:
            stmt = select(McpServer).where(McpServer.name == name)
            return session.exec(stmt).first()

    def list_mcp_servers(
        self,
        limit: int = 100,
        offset: int = 0,
        is_active: bool | None = None,
    ) -> list[McpServer]:
        """List MCP servers with optional filters.

        Args:
            limit: Maximum number of results.
            offset: Number of results to skip.
            is_active: Optional filter by active status.

        Returns:
            List of McpServer instances.
        """
        with Session(self.engine) as session:
            stmt = select(McpServer)

            if is_active is not None:
                stmt = stmt.where(McpServer.is_active == is_active)

            stmt = stmt.order_by(col(McpServer.created_at).desc()).offset(offset).limit(limit)
            return list(session.exec(stmt))

    def update_mcp_server(
        self,
        server_id: str,
        name: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
        is_active: bool | None = None,
    ) -> McpServer | None:
        """Update an MCP server configuration.

        Args:
            server_id: The server ID.
            name: New name (optional).
            description: New description (optional).
            config: New config dictionary (optional).
            is_active: New active status (optional).

        Returns:
            Updated McpServer instance or None if not found.
        """
        with Session(self.engine) as session:
            mcp_server = session.get(McpServer, server_id)
            if mcp_server is None:
                return None

            if name is not None:
                mcp_server.name = name
            if description is not None:
                mcp_server.description = description
            if config is not None:
                mcp_server.config = config
            if is_active is not None:
                mcp_server.is_active = is_active

            mcp_server.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            session.refresh(mcp_server)

            logger.info(f"Updated MCP server: id={server_id}")
            return mcp_server

    def delete_mcp_server(self, server_id: str) -> dict[str, Any]:
        """Delete an MCP server.

        Args:
            server_id: The server ID.

        Returns:
            Dict with deleted status and info.
        """
        with Session(self.engine) as session:
            mcp_server = session.get(McpServer, server_id)
            if mcp_server is None:
                logger.warning(f"MCP server not found for deletion: id={server_id}")
                return {"deleted": False, "id": server_id, "error": "Not found"}

            session.delete(mcp_server)
            session.commit()

            logger.info(f"Deleted MCP server: id={server_id}")
            return {"deleted": True, "id": server_id}
