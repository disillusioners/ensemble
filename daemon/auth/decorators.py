"""Route authorization decorator — oauth2-auth Phase 3 (T3.2 / D3.7).

``@require_scope(*required)`` is THE v1 policy surface: it reads
``request.state.auth_context`` (attached by
:class:`daemon.middleware.auth.AuthMiddleware`) and enforces scope
possession. The MIDDLEWARE is the authentication gate (every non-public
path is authenticated or 401/302'd before dispatch); this decorator is
the per-route AUTHORIZATION layer on top (D3.7 / FR-18).

Scope-satisfaction semantics (v1 contract — citable):

* **Session lane (cookie, human operator)** — session-valid-is-enough
  (D3.7 default-``*`` philosophy extended to decorated routes): a
  session context satisfies ANY required scope. Rationale: v1 sessions
  carry the configured OIDC scopes (``openid profile email``); the SPA
  has no scope-grant surface (NFR-18: zero ``frontend/src/**`` changes),
  so binding session users to machine-lane scope names would 403 the
  human lane on day 1. Per-route scope application for the session lane
  begins in P4 with the C1 regression cage.
* **Bearer lane (machine)** — the token's scope set must contain at
  least ONE required scope, or the literal ``*``. Bearer tokens are the
  scoped surface ([REQ] §7.3 per-consumer scope table).
* Missing/absent context (route reached without the middleware — e.g.
  auth disabled at runtime) is FAIL-CLOSED: 403 SCOPE_INSUFFICIENT.

Insufficient scope → ``403 SCOPE_INSUFFICIENT`` (not 401 — the caller
IS authenticated but not authorized; [TA] §Machine-Client Lane: "the
route is in /api, so 403 not 404").
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable

from fastapi import HTTPException, Request

from daemon.auth.context import AUTH_METHOD_SESSION, AuthContext

logger = logging.getLogger(__name__)


def _context_satisfies(ctx: AuthContext | None, required: tuple[str, ...]) -> bool:
    """Evaluate the v1 scope-satisfaction contract (see module docstring).

    Fail-closed on ``None`` context; session lane always satisfies;
    bearer lane needs an intersecting scope or ``*``.
    """
    if ctx is None:
        return False
    if ctx.auth_method == AUTH_METHOD_SESSION:
        return True
    if "*" in ctx.scopes:
        return True
    return any(scope in ctx.scopes for scope in required)


def require_scope(*required: str) -> Callable[[Any], Any]:
    """Require the caller to hold one of ``required`` scopes (D3.7).

    Usage (the T3.12 shape)::

        @router.get("/keys")
        @require_scope("mcpkb:admin")
        async def list_keys(request: Request): ...

    The decorated callable MUST take ``request: Request`` (FastAPI
    injects it; the wrapper reads ``request.state.auth_context``).
    Async and sync handlers are both supported.
    """

    def decorator(fn: Any) -> Any:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                request = _find_request(args, kwargs)
                ctx = getattr(request.state, "auth_context", None)
                if not _context_satisfies(ctx, required):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "SCOPE_INSUFFICIENT",
                            "message": "Token lacks required scope",
                            "required": list(required),
                        },
                    )
                return await fn(*args, **kwargs)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            request = _find_request(args, kwargs)
            ctx = getattr(request.state, "auth_context", None)
            if not _context_satisfies(ctx, required):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "SCOPE_INSUFFICIENT",
                        "message": "Token lacks required scope",
                        "required": list(required),
                    },
                )
            return fn(*args, **kwargs)

        return sync_wrapper

    return decorator


def _find_request(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Request:
    """Locate the FastAPI ``Request`` among the call arguments.

    FastAPI injects ``Request`` by annotation — it arrives in ``kwargs``
    on the normal route path, or positionally when the handler takes it
    first. Fail loudly (ValueError) on a mis-decorated handler: the
    403-fail-closed contract requires the request object.
    """
    candidate = kwargs.get("request")
    if isinstance(candidate, Request):
        return candidate
    for arg in args:
        if isinstance(arg, Request):
            return arg
    raise ValueError(
        "@require_scope requires the handler to take a FastAPI Request "
        "parameter (request: Request)"
    )


__all__ = ["require_scope"]
