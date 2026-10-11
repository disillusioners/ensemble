"""Auth-subsystem lifespan wiring — oauth2-auth Phase 2 (T2.8 / D5).

The single construction site for the auth background machinery. When
``auth.enabled`` is FALSE this constructs NOTHING — no repository, no
``OAuthClient``, no sweeper task, and (the G2.3 introspection contract)
no ``auth_*`` attribute ever lands on ``app.state`` — FR-2's
zero-constructed-objects surface extended to the service layer (D5
not-registered posture).

When TRUE: constructs the repository (shared engine), the OAuthClient,
and the sweeper task following the readiness-refresher pattern — the
task is created here and cancelled AND awaited by
:func:`stop_auth_background` on shutdown. The boot-time discovery
fetch rides the sweeper task's boot hook (WARN-not-fatal, NFR-7/C4) so
an unreachable IdP never stalls boot or adds a second exit-78 site —
the P1 ``_boot_auth_config_preflight`` remains the single exit-78
owner.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.engine import Engine

from daemon.config import AuthConfig
from daemon.repositories.auth_session.repository import AuthSessionRepository
from daemon.services.auth_session.sweeper import AuthSessionSweeper
from daemon.services.oauth_client import OAuthClient

if TYPE_CHECKING:
    from starlette.datastructures import State

logger = logging.getLogger(__name__)

#: ``app.state`` attribute names owned by this module (the G2.3
#: introspection contract — with auth OFF, NONE of these exist).
AUTH_STATE_ATTRS: tuple[str, ...] = (
    "auth_session_repository",
    "oauth_client",
    "auth_sweeper",
    "auth_sweeper_task",
)


async def start_auth_background(
    app_state: "State | Any",
    auth_config: AuthConfig,
    engine: Engine,
) -> tuple[AuthSessionRepository, OAuthClient, AuthSessionSweeper] | None:
    """Construct + start the auth background machinery (auth ON only).

    Returns ``None`` (and touches ``app_state`` in NO way) when
    ``auth.enabled`` is false — the OFF branch is a single early
    return BEFORE any object construction (FR-2 / D5 not-registered).

    Returns the ``(repository, client, sweeper)`` triple otherwise, and
    stores all four ``AUTH_STATE_ATTRS`` on ``app_state``.
    """
    if not auth_config.enabled:
        # T2.8 / G2.3: NOTHING constructs — not even a disabled client.
        return None

    repository = AuthSessionRepository(engine)
    client = OAuthClient(auth_config)
    sweeper = AuthSessionSweeper(
        repository,
        boot_hook=client.discovery,
    )
    task = asyncio.create_task(sweeper.run(), name="auth-session-sweeper")

    app_state.auth_session_repository = repository
    app_state.oauth_client = client
    app_state.auth_sweeper = sweeper
    app_state.auth_sweeper_task = task

    logger.info(
        "[Auth] background machinery started: sweeper task created "
        "(boot discovery fetch rides the first tick, WARN-not-fatal)"
    )
    return repository, client, sweeper


async def stop_auth_background(app_state: "State | Any") -> None:
    """Shutdown: cancel AND await the sweeper task (readiness-refresher
    pattern — never fire-and-forget). Safe to call in ANY state: the
    OFF deployment (or a partial boot before the start call) has no
    attributes, and every missing attribute is skipped."""
    task = getattr(app_state, "auth_sweeper_task", None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 — shutdown never raises
            logger.warning("[Auth] sweeper task shutdown error: %s", exc)
    for attr in AUTH_STATE_ATTRS:
        if hasattr(app_state, attr):
            setattr(app_state, attr, None)


__all__ = [
    "AUTH_STATE_ATTRS",
    "start_auth_background",
    "stop_auth_background",
]
