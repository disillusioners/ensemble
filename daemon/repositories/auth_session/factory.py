"""Factory for :class:`AuthSessionRepository` — oauth2-auth Phase 2.

Mirrors the ``daemon/repositories/factory.py`` per-domain ``create_*``
convention (engine takes precedence over config; ``create_tables``
opt-in — the production path creates the auth tables via the manager's
``SQLModel.metadata.create_all`` after the model-registration block, so
the default here is ``False``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.engine import Engine

from .repository import AuthSessionRepository

if TYPE_CHECKING:
    from daemon.repositories.factory import DatabaseConfig

logger = logging.getLogger(__name__)


def create_auth_session_repository(
    config: "DatabaseConfig | None" = None,
    engine: Engine | None = None,
    create_tables: bool = False,
) -> AuthSessionRepository:
    """Create an :class:`AuthSessionRepository` from config or engine.

    Args:
        config: Database configuration (required if engine not provided).
        engine: Shared engine instance (recommended — avoids lock
            contention; this is what the lifespan wiring passes).
        create_tables: When True, run
            ``SQLModel.metadata.create_all(engine, tables=[auth tables])``
            first (tests / standalone scripts). The production daemon
            path leaves this False — the manager's global ``create_all``
            covers the four auth tables via the model-registration
            block.

    Returns:
        Configured repository.
    """
    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        from daemon.repositories.factory import create_engine_from_config

        engine = create_engine_from_config(config)

    if create_tables:
        from sqlmodel import SQLModel

        from .models import (
            AuthBootstrapConsumption,
            AuthSession,
            AuthState,
            BearerToken,
        )

        SQLModel.metadata.create_all(
            engine,
            tables=[
                AuthSession.__table__,
                AuthState.__table__,
                BearerToken.__table__,
                AuthBootstrapConsumption.__table__,
            ],
        )

    return AuthSessionRepository(engine)


__all__ = ["create_auth_session_repository"]
