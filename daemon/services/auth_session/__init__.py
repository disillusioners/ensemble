"""Auth-session background services — oauth2-auth Phase 2.

Sweeper (T2.4) + lifespan wiring (T2.8). The ``OAuthClient`` lives at
:mod:`daemon.services.oauth_client` (service-layer sibling).
"""

from .lifecycle import (
    AUTH_STATE_ATTRS,
    start_auth_background,
    stop_auth_background,
)
from .sweeper import AuthSessionSweeper

__all__ = [
    "AUTH_STATE_ATTRS",
    "AuthSessionSweeper",
    "start_auth_background",
    "stop_auth_background",
]
