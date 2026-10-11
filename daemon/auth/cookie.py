"""Cookie + CSRF layer — oauth2-auth Phase 3 (T3.4 / D8 AMEND v1.2 C1).

Two cookies, one lane decision:

* ``ensemble_sid`` — the session credential. ``HttpOnly`` ALWAYS;
  ``Secure`` unless ``auth.allow_insecure_cookie`` (the explicit LAN
  opt-out); ``SameSite`` per ``auth.cookie_samesite`` (default Lax —
  Strict breaks the top-level POST callback redirect, D8); ``Path=/``;
  ``Max-Age=auth.session_ttl_seconds``.
* ``ensemble_csrf`` — the READABLE (not HttpOnly) double-submit token,
  value = SHA-256 hex of the session id (deterministic derivation —
  no schema change to ``auth_sessions``; the double-submit pattern only
  needs cookie-vs-header equality, so the value needs no server-side
  storage). The unchanged SPA never echoes it (NFR-18); the echo check
  is defense-in-depth for the SPA-with-explicit-token future (OD-15).

CSRF predicate (D8 v1.2 C1 — CRITICAL, presence-based branching):
enforcement triggers ONLY when the request carries ``Origin`` or
``Referer`` (the browser lane), mirroring the rule-1 precedent at
``daemon/routers/maintenance_origin_guard.py:24-43`` ("No Origin header
→ allow"). For browser-lane state-changing requests the origin must be
same-origin (or localhost-family — the maintenance guard's rule-3 dev
posture). Headerless machine/bearer requests are EXEMPT — no ambient
cookie authority means the CSRF surface does not exist. ``SameSite=Lax``
covers the residual cross-site risk. This is what makes AC-39 hold BY
CONSTRUCTION: the unchanged SPA's mutations carry Origin (same-origin)
and no CSRF header, and succeed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import urlsplit

from daemon.config import AuthConfig

logger = logging.getLogger(__name__)

#: Session-cookie name — the value IS the ``auth_sessions.id`` row id.
SESSION_COOKIE_NAME = "ensemble_sid"
#: Readable double-submit cookie name (CSRF echo target).
CSRF_COOKIE_NAME = "ensemble_csrf"
#: State-changing methods the CSRF predicate applies to
#: (GET/SSE are exempt — [TA] §Session Model CSRF).
CSRF_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: ASGI header keys the predicate consults (lowercase).
_ORIGIN_HEADER = "origin"
_REFERER_HEADER = "referer"
_CSRF_HEADER = "x-csrf-token"

#: Localhost-family hosts accepted as "same enough" — the maintenance
#: origin-guard rule-3 dev posture (FE dev server on :4199 proxying to
#: :8079). Exact-match set + the 127.0.0.0/8 range check in
#: :func:`_host_is_localhost` (mirrors maintenance_origin_guard.py).
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def csrf_token_for_session(session_id: str) -> str:
    """Deterministic double-submit token: SHA-256 hex of the session id.

    The token is never stored server-side; validation is the pure
    cookie-vs-header equality (double-submit). Knowing the cookie value
    requires already being same-origin (the browser only exposes it to
    the origin that set it), which is the property the pattern defends.
    """
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def set_session_cookie(
    response: Any,
    session_id: str,
    *,
    auth_config: AuthConfig,
) -> None:
    """Set ``ensemble_sid`` with the D8 flag set (T3.4)."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=auth_config.session_ttl_seconds,
        path="/",
        secure=not auth_config.allow_insecure_cookie,
        httponly=True,
        samesite=auth_config.cookie_samesite.lower(),
    )


def set_csrf_cookie(
    response: Any,
    session_id: str,
    *,
    auth_config: AuthConfig,
) -> None:
    """Set the readable ``ensemble_csrf`` echo-target cookie (T3.4).

    NOT HttpOnly (the SPA must be able to read it to echo it); SameSite
    and Secure mirror the session cookie so the pair travels together.
    """
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token_for_session(session_id),
        max_age=auth_config.session_ttl_seconds,
        path="/",
        secure=not auth_config.allow_insecure_cookie,
        httponly=False,
        samesite=auth_config.cookie_samesite.lower(),
    )


def clear_session_cookies(response: Any, *, auth_config: AuthConfig) -> None:
    """Logout: clear both cookies (``Max-Age=0`` per [TA] §Logout)."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=not auth_config.allow_insecure_cookie,
        httponly=True,
        samesite=auth_config.cookie_samesite.lower(),
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        secure=not auth_config.allow_insecure_cookie,
        httponly=False,
        samesite=auth_config.cookie_samesite.lower(),
    )


def parse_cookies_from_scope(scope: Any) -> dict[str, str]:
    """Parse the ASGI ``Cookie`` headers into a dict (lowercase names).

    Never raises: malformed pairs are skipped (the middleware's
    never-raise contract, D3.9).
    """
    headers = _header_dict(scope)
    raw = headers.get("cookie", "")
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name:
            cookies[name] = value.strip()
    return cookies


def _header_dict(scope: Any) -> dict[str, str]:
    """ASGI ``scope['headers']`` → ``{lowercase-name: value}`` dict."""
    headers: dict[str, str] = {}
    for raw_key, raw_value in scope.get("headers", []) or []:
        try:
            headers[raw_key.decode("latin-1").lower()] = raw_value.decode(
                "latin-1"
            )
        except (UnicodeDecodeError, AttributeError):
            continue
    return headers


# ── CSRF predicate (D8 v1.2 C1) ─────────────────────────────────────


def csrf_applies(method: str, headers: dict[str, str]) -> bool:
    """The C1 predicate: is this request inside the CSRF surface?

    True only for state-changing methods on the BROWSER lane (``Origin``
    or ``Referer`` present). GET/SSE exempt; headerless machine/bearer
    requests exempt (rule-1 mirror: "No Origin header → allow").
    """
    if method.upper() not in CSRF_STATE_CHANGING_METHODS:
        return False
    return _ORIGIN_HEADER in headers or _REFERER_HEADER in headers


def validate_browser_lane_origin(
    headers: dict[str, str],
    *,
    request_host: str,
    request_scheme: str,
) -> bool:
    """Same-origin validation for a browser-lane state-changing request.

    Checks ``Origin`` first, then ``Referer``. Allowed when the origin
    matches ``{request_scheme}://{request_host}`` exactly, or when the
    origin host is localhost-family (the maintenance guard's rule-3
    zero-config dev posture). Fail-closed otherwise.
    """
    origin = headers.get(_ORIGIN_HEADER) or headers.get(_REFERER_HEADER)
    if not origin:
        # Predicate says the lane exists (one of the two was present);
        # a present-but-empty header fails closed.
        return False
    parts = urlsplit(origin)
    if not parts.scheme or not parts.netloc:
        return False
    origin_host = (parts.hostname or "").lower()
    if parts.port:
        origin_host = f"{origin_host}:{parts.port}"
    expected = f"{request_scheme}://{request_host}".lower()
    supplied = f"{parts.scheme.lower()}://{origin_host}"
    if hmac.compare_digest(supplied, expected):
        return True
    return _host_is_localhost(parts.hostname or "")


def validate_csrf_header(
    headers: dict[str, str],
    cookies: dict[str, str],
) -> bool:
    """Token-echo check — ONLY called when ``X-CSRF-Token`` is present.

    Double-submit: the header must equal the readable ``ensemble_csrf``
    cookie value (constant-time compare). Absent cookie ⇒ fail.
    """
    header_token = headers.get(_CSRF_HEADER, "")
    cookie_token = cookies.get(CSRF_COOKIE_NAME, "")
    if not header_token or not cookie_token:
        return False
    return hmac.compare_digest(header_token, cookie_token)


def _host_is_localhost(host: str) -> bool:
    """Exact localhost names, or any 127.0.0.0/8 IPv4 loopback address
    (mirror of ``maintenance_origin_guard._host_is_localhost``)."""
    lowered = (host or "").lower()
    if lowered in _LOCALHOST_HOSTS:
        return True
    if lowered.startswith("127."):
        parts = lowered.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return True
    return False


__all__ = [
    "CSRF_COOKIE_NAME",
    "CSRF_STATE_CHANGING_METHODS",
    "SESSION_COOKIE_NAME",
    "clear_session_cookies",
    "csrf_applies",
    "csrf_token_for_session",
    "parse_cookies_from_scope",
    "set_csrf_cookie",
    "set_session_cookie",
    "validate_browser_lane_origin",
    "validate_csrf_header",
]
