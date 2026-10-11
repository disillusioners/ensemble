"""Authenticated-identity context object — oauth2-auth Phase 3 (T3.1 / D3.6).

The single identity shape every auth lane (cookie session, static bearer,
future lanes) attaches to a successfully authenticated request. Routes and
downstream machinery read it at ``request.state.auth_context`` (HTTP) /
``scope["state"]["auth_context"]`` (raw ASGI) — the attachment point is
set by :class:`daemon.middleware.auth.AuthMiddleware` after validation.

D3.6 shape (NORMATIVE — do not narrow): ``subject``, ``scopes: set[str]``,
``auth_method``, ``expires_at``, and the reserved ``meta: dict[str, Any]``
(v1 always ``{}``). The fixed-closed-dataclass alternative was REJECTED —
``meta`` is the S-1 reserved seam that lets v2 attach per-request extras
(e.g. share-token provenance) without changing every construction site.

P4 grant model (daemon/auth/scopes.py is the authority): ``scopes`` is
the lane's GRANTED set under the real-grant contract — the session lane
persists the OIDC scopes ∪ the operator family (the browser lane IS the
operator); the bearer lane carries exactly its minted scopes. No lane
satisfies a ``@require_scope`` check by identity alone (the P3
session-blanket branch is gone — M-3 close).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Auth-method discriminator values (v1). ``session`` = cookie lane,
#: ``bearer`` = static-token machine lane. The CSRF predicate keys on the
#: lane: only the ambient-authority cookie lane can be CSRF'd.
AUTH_METHOD_SESSION = "session"
AUTH_METHOD_BEARER = "bearer"


@dataclass
class AuthContext:
    """Authenticated identity for one request (D3.6 / [TA] Reserved Seam S-1).

    Attributes:
        subject: Principal identity — OIDC ``sub`` claim for the session
            lane, the owner label (e.g. ``operator-cli``) for bearer rows.
        scopes: GRANTED scope set (P4 real-grant model). The session lane
            grants the configured OIDC scopes ∪ the operator family
            (:data:`daemon.auth.scopes.SESSION_OPERATOR_SCOPES`); the
            bearer lane grants the token row's minted scopes exactly.
            ``@require_scope`` consumes this set (D3.7 + P4).
        auth_method: ``"session"`` | ``"bearer"`` — which lane produced
            the context (drives CSRF enforcement + audit fields).
        expires_at: Credential expiry as ``now_utc_iso()``-format UTC
            ISO-8601 text (the INV-7 house timestamp shape used across
            the auth tables).
        meta: RESERVED v2 seam (S-1) — always ``{}`` in v1. Never read by
            v1 code; construction sites must pass nothing.
    """

    subject: str
    scopes: set[str]
    auth_method: str
    expires_at: str
    meta: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "AUTH_METHOD_BEARER",
    "AUTH_METHOD_SESSION",
    "AuthContext",
]
