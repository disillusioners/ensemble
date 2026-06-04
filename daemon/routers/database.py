"""Database management API endpoints.

Exposes a small REST surface for switching the active database backend
recorded in ``ensemble.json``. The current process keeps using the
engine it was started with — the switch only rewrites the config file
and a restart is required for the new backend to take effect.

Endpoints::

    POST /api/database/switch

The endpoint resolves the live :class:`EnsembleConfig` and the data
directory from ``app.state`` (both wired up during the FastAPI
lifespan). It uses :meth:`EnsembleConfig.save` to persist the change,
which already performs an atomic write via ``.tmp`` + ``os.replace``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from daemon.ensemble_config import EnsembleConfig

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/database", tags=["database"])


# ── Models ───────────────────────────────────────────────────────────────────


class DatabaseSwitchRequest(BaseModel):
    """Request body for ``POST /api/database/switch``."""

    database: Literal["sqlite", "postgres"] = Field(
        ...,
        description="Target database backend to switch to.",
    )


class DatabaseSwitchResponse(BaseModel):
    """Response body for a successful database switch."""

    message: str = Field(
        ...,
        description="Human-readable summary of the action performed.",
    )
    requires_restart: bool = Field(
        ...,
        description=(
            "Always True for this endpoint — the new backend only takes "
            "effect after the daemon is restarted."
        ),
    )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post(
    "/switch",
    response_model=DatabaseSwitchResponse,
    responses={
        200: {"description": "Database switched; restart required"},
        400: {
            "description": (
                "Invalid target, no-op (already on target), or PG ENV "
                "vars not configured"
            )
        },
        500: {"description": "Ensemble config not initialized"},
    },
)
async def switch_database(
    payload: DatabaseSwitchRequest,
    request: Request,
) -> DatabaseSwitchResponse:
    """Switch the active database backend recorded in ``ensemble.json``.

    Validates the target, refuses no-op switches, and — for PostgreSQL
    targets — checks the PG ENV vars are present before rewriting the
    config file. The current process continues using the engine it was
    started with; the operator must restart the daemon for the change
    to take effect.

    Args:
        payload: Request body specifying the target backend.
        request: FastAPI request (used to reach ``app.state``).

    Returns:
        A :class:`DatabaseSwitchResponse` with ``requires_restart=True``.

    Raises:
        HTTPException: 500 if ``ensemble_config``/``data_dir`` are not
            wired up on ``app.state`` (lifespan did not run).
        HTTPException: 400 if the target is the same as the current
            backend, the target is invalid (defense in depth — Pydantic
            already enforces this at the schema level), or PG ENV vars
            are missing for a ``postgres`` target.
    """
    ensemble_config: EnsembleConfig | None = getattr(
        request.app.state, "ensemble_config", None
    )
    data_dir: Path | None = getattr(request.app.state, "data_dir", None)

    if ensemble_config is None or data_dir is None:
        raise HTTPException(
            status_code=500,
            detail="Ensemble config not initialized",
        )

    target = payload.database
    current = ensemble_config.database

    # No-op: already on the requested backend.
    if target == current:
        raise HTTPException(
            status_code=400,
            detail=f"Already on '{target}'; no switch needed",
        )

    # Switching to PostgreSQL requires the connection ENV vars.
    if target == "postgres" and not ensemble_config.postgres_env_available:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot switch to postgres: POSTGRES_HOST and POSTGRES_DB "
                "environment variables are not set"
            ),
        )

    # SQLite is always available — no env preconditions.

    # Persist the new backend selection atomically. We mutate a copy of
    # the config so the in-memory object can be observed by other code
    # paths (e.g. health check) without the daemon having to restart
    # first.
    ensemble_config.database = target
    try:
        ensemble_config.save(data_dir)
    except Exception as exc:
        logger.exception("Failed to persist database switch to %s", target)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist database switch: {exc}",
        ) from exc

    logger.info(
        "Database switch requested: %s -> %s (restart required)",
        current,
        target,
    )

    return DatabaseSwitchResponse(
        message=f"Database switched to {target}. Restart required.",
        requires_restart=True,
    )


__all__ = ["router", "DatabaseSwitchRequest", "DatabaseSwitchResponse"]
