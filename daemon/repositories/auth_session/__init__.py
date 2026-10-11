"""Auth-session persistence package — oauth2-auth Phase 2.

Models (:mod:`.models`) + repository (:mod:`.repository`). Registered
into ``SQLModel.metadata`` by the ``daemon/manager.py`` import block
BEFORE ``create_all`` (snapshot precedent) so the four auth tables
materialize on BOTH drivers.
"""

from .models import AuthBootstrapConsumption, AuthSession, AuthState, BearerToken
from .repository import AuthSessionRepository, StateConsumeOutcome

__all__ = [
    "AuthBootstrapConsumption",
    "AuthSession",
    "AuthSessionRepository",
    "AuthState",
    "BearerToken",
    "StateConsumeOutcome",
]
