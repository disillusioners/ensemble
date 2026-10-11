"""Auth-self routes — oauth2-auth Phase 3 (T3.6 / [REQ] §5.3).

The four PUBLIC auth endpoints (login / callback / logout+alias / me).
They are PUBLIC per §5.3 (login and callback are chicken-and-egg; /me's
401 path IS the public "am I logged in" probe; logout works with or
without a session) — tagged ``public-probe`` at registration so the
D18 derivation classifies them without any prefix rule (the
``/api/auth/admin/*`` reserved-seam family stays UNTAGGED = protected,
§5.3).

Route-collision note (T3.6 / phase3-plan §6): ``DELETE /api/auth/session``
is auth-NAMESPACED and distinct from the ``/api/sessions`` family —
asserted by the T3.6 route-collision test.

Error contract ([REQ] callback matrix):
    503 LOGIN_NOT_CONFIGURED  — D3.5 chain resolved no absolute base
    503 IDP_UNREACHABLE       — discovery/token endpoint unreachable
    400 STATE_REPLAYED        — CAS says consumed-or-unknown
    400 STATE_EXPIRED         — CAS won but past TTL
    401 IDP_REJECTED          — exchange or id_token validation failed
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from daemon.auth.cookie import (
    clear_session_cookies,
    set_csrf_cookie,
    set_session_cookie,
)
from daemon.middleware.auth import sanitize_return_to
from daemon.services.auth_service import (
    AuthService,
    IdpRejectedError,
    IdpUnreachableError,
    LoginNotConfigured,
    StateExpiredError,
    StateReplayedError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        # The routes only register when auth is enabled (create_app
        # gating); a missing service means the lifespan did not run or
        # construction failed — fail closed, never a 500.
        raise HTTPException(
            status_code=503,
            detail={"error": "AUTH_UNAVAILABLE", "message": "Auth service unavailable"},
        )
    return service


def _error(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "message": message},
    )


@router.get("/login")
async def login(request: Request, return_to: str = "/") -> Any:
    """Start the OIDC Authorization-Code + PKCE flow (FR-4/5/6, FR-28).

    302 to the IdP authorize URL, or 503 LOGIN_NOT_CONFIGURED (the body
    names the exact env vars) / 503 IDP_UNREACHABLE.
    """
    service = _get_auth_service(request)
    try:
        redirect = await service.build_login_url(return_to=return_to)
    except LoginNotConfigured as exc:
        return _error(503, exc.code, str(exc))
    except IdpUnreachableError as exc:
        return _error(503, exc.code, str(exc))
    return RedirectResponse(url=redirect.authorize_url, status_code=302)


@router.get("/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None) -> Any:
    """Finish the flow: CAS-consume state → exchange → issue session
    (FR-7/8/9). Success sets ``ensemble_sid`` (+ readable
    ``ensemble_csrf``) and 302s to the sanitized ``return_to``."""
    service = _get_auth_service(request)
    client = request.scope.get("client")
    client_ip = client[0] if client else None
    user_agent = request.headers.get("user-agent")
    try:
        result = await service.handle_callback(
            code=code,
            state=state,
            client_ip=client_ip,
            user_agent=user_agent,
        )
    except StateReplayedError as exc:
        return _error(400, exc.code, str(exc))
    except StateExpiredError as exc:
        return _error(400, exc.code, str(exc))
    except IdpUnreachableError as exc:
        return _error(503, exc.code, str(exc))
    except IdpRejectedError as exc:
        logger.warning(
            "[AuthAudit] event=callback_rejected reason=%s", exc.code
        )
        return _error(401, exc.code, str(exc))

    auth_config = request.app.state.auth_config
    return_to = sanitize_return_to(
        request.query_params.get("return_to") or result.return_to
    )
    response = RedirectResponse(url=return_to, status_code=302)
    set_session_cookie(response, result.session_id, auth_config=auth_config)
    set_csrf_cookie(response, result.session_id, auth_config=auth_config)
    logger.info(
        "[AuthAudit] event=callback_success subject=%s return_to=%s",
        result.session.subject,
        return_to,
    )
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    """Logout (FR-14/15) — works with or without a session (OD-10).

    204 + clear-cookie. The row DELETE is the P2 single-CAS delete.
    """
    service = _get_auth_service(request)
    session_id = request.cookies.get("ensemble_sid", "")
    if session_id:
        try:
            await service.invalidate_session(session_id)
        except Exception:  # noqa: BLE001 — logout never fails the browser
            logger.warning("[AuthAudit] event=logout_invalidate_error", exc_info=True)
    auth_config = request.app.state.auth_config
    clear_session_cookies(response, auth_config=auth_config)
    logger.info("[AuthAudit] event=logout had_session=%s", bool(session_id))
    return None


@router.delete("/session", status_code=204)
async def delete_session_alias(request: Request, response: Response):
    """``DELETE /api/auth/session`` — the OD-10 alias for POST /logout.

    Distinct namespace from the ``/api/sessions`` instance-router family
    (route-collision test in T3.6).
    """
    return await logout(request, response)


@router.get("/me")
async def me(request: Request) -> Any:
    """Context probe (FR-15 / AC-15): the AuthContext when a session is
    valid, 401 when not — the PUBLIC 401 is the contract (§5.3)."""
    ctx = getattr(request.state, "auth_context", None)
    if ctx is None:
        # No context from the middleware (no/invalid cookie) — probe the
        # cookie directly so the response reflects the session store.
        service = _get_auth_service(request)
        session_id = request.cookies.get("ensemble_sid", "")
        if session_id:
            client = request.scope.get("client")
            ctx = await service.validate_session(
                session_id, client_ip=client[0] if client else None
            )
    if ctx is None:
        return _error(401, "AUTH_REQUIRED", "Authentication required")
    return {
        "subject": ctx.subject,
        "scopes": sorted(ctx.scopes),
        "auth_method": ctx.auth_method,
        "expires_at": ctx.expires_at,
    }


__all__ = ["router"]

# ── RESERVED route-shape seams (requirements §5.3 / S-7, S-11) ──────
#
# The admin route family is OUT OF SCOPE for v1 implementation, but
# §5.3 mandates "the route shape MUST be in the protected set". The
# shape below is the v2 contract — these paths are UNTAGGED (=
# oauth2-protected by the D18 default-deny derivation) and, when built,
# carry the ``auth:admin`` scope via @require_scope:
#
#     /api/auth/admin/tokens/issue    (S-11 — P4 CLI/management mint)
#     /api/auth/admin/tokens/revoke   (S-7 — token revocation)
#
# The reserved_seams regression cage (tests/test_reserved_seams.py)
# greps this block.
#
# RESERVED (v2): /api/auth/admin/tokens/issue
# RESERVED (v2): /api/auth/admin/tokens/revoke
