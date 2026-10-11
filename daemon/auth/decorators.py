"""Route authorization decorator — oauth2-auth Phase 3/4 (T3.2 / D3.7+P4).

``@require_scope(*required)`` is THE v1 policy surface: it reads
``request.state.auth_context`` (attached by
:class:`daemon.middleware.auth.AuthMiddleware`) and enforces scope
possession. The MIDDLEWARE is the authentication gate (every non-public
path is authenticated or 401/302'd before dispatch); this module is the
per-route AUTHORIZATION layer on top (D3.7 / FR-18 / P4 T4.4).

Scope-satisfaction semantics (P4 REAL GRANT MODEL — the M-3 close;
supersedes the P3 "session-valid-is-enough" branch):

* **NO lane satisfies scopes by identity.** Every lane — session
  (cookie operator) and bearer (machine) alike — satisfies a
  requirement only when its GRANTED scope set contains one of the
  required scopes (or the ``*`` wildcard). What each lane grants is
  the grant model in :mod:`daemon.auth.scopes`:

  - the session lane is PERSISTED with the operator family (the
    browser lane IS the operator; the SPA consumes every control-plane
    family and NFR-18 keeps the SPA unchanged) — so the SPA keeps
    working, but through a visible, auditable grant rather than a
    blanket code branch;
  - the bearer lane carries EXACTLY its minted scopes (§7.3
    per-consumer scope table; OD-14 bootstrap default ``auth:admin``).

* **Inert when auth is OFF (T4.4 contract — "decorator lines only,
  inert when auth OFF").** The decorator/dependency reads
  ``request.app.state.auth_config``: absent (auth OFF — ``create_app``
  only sets it when enabled) ⇒ the check PASSES without consulting
  any context. Auth-OFF behavior stays byte-identical to the pre-auth
  build (FR-2 / G3.2).

* **Fail-closed when auth is ON.** Auth enabled but no/insufficient
  context ⇒ ``403 SCOPE_INSUFFICIENT`` (never 404 — the caller is
  authenticated-but-unauthorized, or a decorated route was reached
  without the middleware, which is a bug, not a hide).

Two application forms, one semantic core:

* ``@require_scope("mcp:read")`` — the wrapper form (T3.12 keys
  family). The handler must take ``request: Request``.
* ``scope_required("instances:read")`` — the FastAPI dependency form
  (``dependencies=[scope_required(...)]`` on the route decorator) for
  the P4 per-route application across router families: no handler
  signature changes, one decorator-line edit per route.

Both forms stamp ``required_scopes`` onto their callable so the D4.7
router-scope manifest cage can introspect every decorated route's
declaration.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable

from fastapi import Depends, HTTPException, Request

from daemon.auth.context import AuthContext
from daemon.auth.scopes import SCOPE_WILDCARD

logger = logging.getLogger(__name__)

#: The 403 body (D4.4: insufficient scope is a 403, never a 404).
_SCOPE_403_DETAIL = {
    "error": "SCOPE_INSUFFICIENT",
    "message": "Token lacks required scope",
}


def _auth_enabled(request: Request) -> bool:
    """True when the app was built with auth ON.

    ``create_app`` sets ``app.state.auth_config`` ONLY when
    ``auth.enabled`` — its absence is the authoritative OFF signal
    (the middleware is likewise not-registered, so no context could
    ever exist). Routes may also be mounted on bare test apps where
    the attribute was never set — same OFF semantics (inert).
    """
    app_state = getattr(request.app, "state", None)
    return getattr(app_state, "auth_config", None) is not None


def _context_satisfies(ctx: AuthContext | None, required: tuple[str, ...]) -> bool:
    """Evaluate the P4 REAL GRANT contract.

    Grant-set membership for EVERY lane: the context's granted scopes
    must contain at least one required scope (or the ``*`` wildcard).
    ``None`` context never satisfies (fail-closed).
    """
    if ctx is None:
        return False
    if SCOPE_WILDCARD in ctx.scopes:
        return True
    return any(scope in ctx.scopes for scope in required)


def _enforce(request: Request, required: tuple[str, ...]) -> None:
    """The shared decision: inert OFF, else grant-check (403 on miss)."""
    if not _auth_enabled(request):
        return
    ctx = getattr(request.state, "auth_context", None)
    if not _context_satisfies(ctx, required):
        raise HTTPException(status_code=403, detail=dict(_SCOPE_403_DETAIL, required=list(required)))


def require_scope(*required: str) -> Callable[[Any], Any]:
    """Require the caller's granted scopes to cover one of ``required``.

    Usage (the T3.12 shape)::

        @router.get("/keys")
        @require_scope("mcpkb:admin")
        async def list_keys(request: Request): ...

    The decorated callable MUST take ``request: Request`` (FastAPI
    injects it; the wrapper reads ``request.state.auth_context``).
    Async and sync handlers are both supported. Inert when auth is
    OFF; 403 SCOPE_INSUFFICIENT when the grant set misses (see module
    docstring for the P4 grant model).
    """

    def decorator(fn: Any) -> Any:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                request = _find_request(args, kwargs)
                _enforce(request, required)
                return await fn(*args, **kwargs)

            wrapper: Any = async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                request = _find_request(args, kwargs)
                _enforce(request, required)
                return fn(*args, **kwargs)

            wrapper = sync_wrapper
        # D4.7 introspection tag: the manifest cage reads the
        # declaration off the mounted endpoint.
        wrapper.required_scopes = tuple(required)
        return wrapper

    return decorator


def scope_required(*required: str) -> Depends:
    """The FastAPI dependency form of :func:`require_scope`.

    Usage (the P4 T4.4 per-route shape — one decorator-line edit, no
    handler signature change)::

        @router.get("", response_model=InstanceListResponse,
                    dependencies=[scope_required("instances:read")])
        async def list_instances(...): ...

    Returns a ``Depends(...)`` wrapping an internal ``Request``-injected
    checker, so the handler does NOT need a request parameter. Same
    semantics as the wrapper form: inert when auth OFF, 403
    SCOPE_INSUFFICIENT on a grant miss. The inner checker carries the
    ``required_scopes`` tag for the manifest cage (the cage reads it
    off ``route.dependant.dependencies[*].call``).
    """

    def dependency(request: Request) -> None:
        _enforce(request, required)

    # Introspection tag + a stable name for 500-trace readability.
    dependency.required_scopes = tuple(required)  # type: ignore[attr-defined]
    dependency.__name__ = f"scope_required[{'|'.join(required)}]"
    return Depends(dependency)


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
        "parameter (request: Request) — or use the scope_required "
        "dependency form"
    )


__all__ = ["require_scope", "scope_required"]
