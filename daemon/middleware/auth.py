"""Auth middleware — the request-path gate (oauth2-auth Phase 3, T3.5).

Pure ASGI (the ``daemon/middleware/host_capture.py`` pattern: constructed
by ``app.add_middleware``, resolves per-request state from
``scope["app"]`` at call time — nothing captured at construction except
the injected ``auth_config``). Registered FOURTH in ``create_app()``
(D3.1/D3.2) so LIFO makes it run OUTERMOST: Auth → HostCapture →
SelectiveAccessLog → CORS.

Three-class derivation (D3.3 AMEND v1.4 / D18 / FR-22a v1.4 — T3.10):

    WS scope → OPTIONS → public → module-lane → [S-19] → [SPA assets]
    → bearer (FR-13 precedence) → cookie (+CSRF) → 302 / 401

The class sets are DERIVED from route attributes at the FIRST HTTP
scope (lifespan-registered Mounts such as /vscode must be visible to
the walk, so deriving at the lifespan scope would miss them), logged
ONCE, and consumed O(1) per request (:func:`daemon.auth.route_meta`
holds the tagging contract). The ordered-resolution list (tagged
routes only, in ``app.routes`` registration order) mirrors Starlette
first-match-wins so the explicitly-protected ``/api/mcp/kb/keys``
family beats the prefixing ``/api/mcp/kb`` module-lane Mount — a
plain prefix match would misclassify the OAuth2-managed key CRUD into
the module lane.

Never-raise (D3.9): every failure inside the gate collapses to a typed
401/302/503 response — internal auth failures never 500, and public
paths are classified by pure set membership BEFORE any I/O.

Audit taxonomy (NFR-12) — every event is a single
``[AuthAudit] event=<name> key=value ...`` log line:

    session_issued / session_invalidated / session_ip_mismatch
        (AuthService — lifecycle)
    request_denied_401 / request_denied_302
        (this module — unauthenticated denial per FR-24)
    csrf_origin_rejected / csrf_token_rejected
        (this module — C1 predicate rejections, WARNING)
    auth_internal_error
        (this module — never-raise fallback fired, ERROR)
    bearer_rejected / bearer_validated
        (this module — machine lane)
"""

from __future__ import annotations

import logging
from typing import Any

from daemon.auth.cookie import (
    SESSION_COOKIE_NAME,
    CSRFOriginPolicy,
    _host_is_localhost,
    _split_host_header as cookie_split_host_header,
    csrf_applies,
    parse_cookies_from_scope,
    parse_operator_audience,
    peer_is_loopback,
    validate_browser_lane_origin,
    validate_csrf_header,
)
from daemon.auth.context import AuthContext
from daemon.auth.route_meta import (
    AUTH_MODULE_LANE_ATTR,
    AUTH_PROTECTED_ATTR,
    AUTH_PUBLIC_ATTR,
    template_matches,
)
from daemon.config import AuthConfig

logger = logging.getLogger(__name__)

#: The SPA PUBLIC-ASSETS carve-out prefixes ([REQ] §5.7) — MUST stay
#: byte-identical to the SPA catch-all's own carve-out at the
#: ``serve_ui_assets`` route (api/ws/vscode/views) so the middleware
#: never classifies as public a path Starlette would 404 to the API.
SPA_CARVE_OUT_PREFIXES = ("api", "ws", "vscode", "views")

#: Methods treated as reads by the SPA-asset rule.
_READ_METHODS = frozenset({"GET", "HEAD"})

#: FR-24 fallback heuristic: Accept ``*/*`` requests to these path
#: families get a 401 (machine contract) instead of the browser 302.
_401_HEURISTIC_PREFIXES = (
    "/api/auth/",
    "/livez",
    "/readyz",
    "/api/health",
    "/api/info",
)

#: Bearer WWW-Authenticate challenge (FR-24 / [TA] §401 Contract).
_WWW_AUTHENTICATE = 'Bearer realm="ensemble"'


#: Control characters the C-1 check rejects outright: the C0 range,
#: DEL (0x7F), plus the unicode line/paragraph-separator class that
#: log forgers and header injectors use because ASCII-only filters
#: miss them (cheap-minor hardening, 2026-10-11 merge gate):
#: U+0085 (NEL), U+2028 (LINE SEPARATOR), U+2029 (PARAGRAPH
#: SEPARATOR). Several log tailers and terminal emitters treat all
#: three as line breaks, so a value carrying them can forge audit
#: lines even though ``ord(c) >= 0x20``.
_LINE_BREAK_CONTROLS = frozenset({0x85, 0x2028, 0x2029})


def sanitize_return_to(raw: str | None) -> str:
    """D3.8 ``return_to`` sanitization (AMENDED v1.1 / OD-12; C-1 added).

    Order is LOAD-BEARING: backslashes are normalized to slashes FIRST
    (legacy browsers treat ``\\\\``/``//`` interchangeably in a
    ``Location:`` header — ``return_to=\\\\evil.com`` and
    ``return_to=//evil.com`` are the same open-redirect vector, so the
    prefix tests must judge the NORMALIZED value). Then (C-1, 2026-10-11
    security review): ANY control character — ``ord(c) < 0x20``,
    ``0x7f``, or the unicode line-break control set (U+0085, U+2028,
    U+2029 — cheap-minor hardening, 2026-10-11 merge gate) — rejects
    the value outright. The CRLF class
    (``%0D%0A`` decoded or raw) is the header-injection /
    log-forgery vector: because BOTH the redirect call sites AND the
    audit-log leg consume this function's OUTPUT, a rejected value can
    never be logged or forwarded — only the normalized ``/``. Then:
    same-origin path-prefixed only (starts with ``/``; never ``//``,
    never ``/\\``), no scheme, ≤1024 chars; anything else → ``/``.
    """
    value = (raw or "/").replace("\\", "/")
    if any(
        ord(c) < 0x20 or ord(c) == 0x7F or ord(c) in _LINE_BREAK_CONTROLS
        for c in value
    ):
        return "/"
    if len(value) > 1024:
        return "/"
    if not value.startswith("/"):
        return "/"
    if value.startswith("//"):
        return "/"
    return value


def _audit_truncate(value: str, *, cap: int = 256) -> str:
    """Audit-log truncation (minor #5): never emit more than ``cap``
    chars of a client-supplied path, with an explicit marker when the
    value was cut. A >cap value is capped, never dropped — the operator
    still sees the shape."""
    if len(value) <= cap:
        return value
    return value[:cap] + "...[TRUNCATED]"


class _ClassifiedTemplate:
    """One tagged route template in the ordered-resolution list."""

    __slots__ = ("template", "kind", "prefix_match")

    def __init__(self, template: str, kind: str, *, prefix_match: bool) -> None:
        self.template = template
        self.kind = kind  # "public" | "module_lane" | "protected"
        #: Mounts (Starlette ``Mount``) match their path AND everything
        #: under it — the middleware must mirror that or a mount
        #: subpath (e.g. /api/mcp/kb/mcp) misclassifies as protected.
        self.prefix_match = prefix_match


class AuthMiddleware:
    """The OAuth2 gate (see module docstring for the full contract)."""

    def __init__(self, app: Any, *, auth_config: AuthConfig | None = None) -> None:
        self.app = app
        self._auth_config = auth_config
        # Derived once at the FIRST HTTP scope (NOT the lifespan scope —
        # lifespan-registered Mounts such as /vscode must be visible to
        # the walk; the first request through the gate sees the final
        # route table). Pure route-metadata walk, zero I/O.
        self._derived = False
        self._public_exact: frozenset[str] = frozenset()
        self._ordered: list[_ClassifiedTemplate] = []
        self._public_paths: frozenset[str] = frozenset()
        self._module_lane_paths: frozenset[str] = frozenset()
        self._anonymous_view_paths: tuple[str, ...] = ()
        if auth_config is not None:
            self._anonymous_view_paths = tuple(auth_config.anonymous_view_paths)

    # ── ASGI entry ──────────────────────────────────────────────────

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # FR-21: WebSocket scopes pass through untouched (v1 seam
            # reserved — S-7 websocket-auth branch).
            await self.app(scope, receive, send)
            return
        # AUTH PHASE — wrapped by the D3.9 never-raise contract: any
        # failure in credential resolution collapses to a 401. This
        # phase never touches the inner app.
        try:
            action = await self._authorize(scope)
        except Exception as exc:  # noqa: BLE001 — D3.9 never-raise
            logger.error(
                "[AuthAudit] event=auth_internal_error path=%s method=%s "
                "error=%s:%s",
                scope.get("path", "?"),
                scope.get("method", "?"),
                type(exc).__name__,
                exc,
            )
            await self._respond_401(send, reason="internal_error")
            return
        # DISPATCH PHASE — NOT wrapped: exceptions from the inner app
        # belong to FastAPI/ServerErrorMiddleware; swallowing them here
        # would double-respond (a second response.start after the
        # handler's). Public/protected classification is decision-only.
        kind = action[0]
        if kind == "pass":
            await self.app(scope, receive, send)
        elif kind == "dispatch":
            scope.setdefault("state", {})["auth_context"] = action[1]
            await self.app(scope, receive, send)
        elif kind == "401":
            await self._respond_401(send, reason=action[1], www_authenticate=action[2])
        elif kind == "403":
            await self._respond_403(send, action[1])
        else:  # "302"
            await _send_redirect(send, 302, action[1])

    # ── derivation (D18 / D3.3 v1.4) ───────────────────────────────

    def _ensure_derived(self, scope: dict[str, Any]) -> None:
        if self._derived:
            return
        self._derived = True
        from starlette.routing import Mount

        app_ref = scope.get("app")
        routes = getattr(app_ref, "routes", None) or []
        public_all: set[str] = set()
        module_lane_all: set[str] = set()
        ordered: list[_ClassifiedTemplate] = []
        public_exact: set[str] = set()
        for route in routes:
            template = getattr(route, "path", None)
            if not isinstance(template, str):
                continue
            is_mount = isinstance(route, Mount)
            is_public = bool(getattr(route, AUTH_PUBLIC_ATTR, False))
            is_module = bool(getattr(route, AUTH_MODULE_LANE_ATTR, False))
            if is_public and is_module:
                # Classes are DISJOINT by contract (T3.10); a conflict
                # fails CLOSED to the protected default + loud ERROR.
                logger.error(
                    "[Auth] route %r carries BOTH public-probe and "
                    "module-lane tags — treating as protected (fail-closed)",
                    template,
                )
                ordered.append(
                    _ClassifiedTemplate(
                        template, "protected", prefix_match=is_mount
                    )
                )
                continue
            if is_public:
                public_all.add(template)
                if is_mount or "{" in template:
                    # Mounts and parametrized public templates resolve
                    # through the ordered list (boundary / prefix
                    # semantics); static paths ride the O(1) fast set.
                    ordered.append(
                        _ClassifiedTemplate(
                            template, "public", prefix_match=is_mount
                        )
                    )
                else:
                    public_exact.add(template)
            elif is_module:
                module_lane_all.add(template)
                ordered.append(
                    _ClassifiedTemplate(
                        template, "module_lane", prefix_match=is_mount
                    )
                )
            elif getattr(route, AUTH_PROTECTED_ATTR, False):
                ordered.append(
                    _ClassifiedTemplate(
                        template, "protected", prefix_match=is_mount
                    )
                )
        self._public_exact = frozenset(public_exact)
        self._public_paths = frozenset(public_all)
        self._module_lane_paths = frozenset(module_lane_all)
        self._ordered = ordered
        # D18 / T3.10: BOTH derived sets are logged at startup —
        # reviewable in promote transcripts.
        logger.info(
            "[Auth] derived public_path_set (%d): %s",
            len(self._public_paths),
            sorted(self._public_paths),
        )
        logger.info(
            "[Auth] derived module_lane_path_set (%d): %s",
            len(self._module_lane_paths),
            sorted(self._module_lane_paths),
        )

    # ── http authorization decision (D3.4 v1.4 order) ──────────────

    async def _authorize(self, scope: dict[str, Any]) -> tuple[Any, ...]:
        """Classify one http scope into an action tuple (see __call__).

        Decision-only: never touches the inner app, never sends. Action
        shapes: ("pass",) | ("dispatch", ctx) | ("401", reason, wa) |
        ("403", error) | ("302", location).
        """
        self._ensure_derived(scope)
        path = scope.get("path", "")
        method = (scope.get("method") or "GET").upper()

        # FR-20: OPTIONS preflight traverses — never cookies.
        if method == "OPTIONS":
            return ("pass",)

        # Public fast path — O(1), zero I/O, BEFORE anything else
        # (D11 structural guarantee for /livez, /readyz, ...).
        if path in self._public_exact:
            return ("pass",)

        # Ordered tagged-route resolution (Starlette first-match-wins
        # mirror; tagged entries only — untagged = protected default).
        resolved = self._resolve_tagged(path)
        if resolved in ("module_lane", "public"):
            # FR-39/FR-42: module-lane passes UNTOUCHED — the module
            # owns this path's auth; OAuth2 never gates it and never
            # inspects credentials.
            return ("pass",)

        # S-19 (v1.1): anonymous_view_paths consulted BEFORE session
        # validation — the reserved branch point for share tokens /
        # password-gated views. v1 ships empty (pass-body regression
        # cage).
        for prefix in self._anonymous_view_paths:
            if prefix and path.startswith(prefix):
                return ("pass",)

        # SPA PUBLIC-ASSETS rule ([REQ] §5.7): the SPA shell and its
        # assets cannot be auth-gated (the redirect chain would break).
        # Mirrors the catch-all route's own carve-out prefixes exactly.
        if method in _READ_METHODS and self._is_spa_asset(path):
            return ("pass",)

        headers = _header_dict(scope)
        cookies = parse_cookies_from_scope(scope)

        # Bearer lane (FR-13 precedence): header present ⇒ the bearer
        # lane OWNS the request — an invalid token is a loud 401, never
        # a silent fall-through to the cookie.
        auth_header = headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            ctx = await self._verify_bearer(scope, token)
            if ctx is not None:
                logger.info(
                    "[AuthAudit] event=bearer_validated subject=%s path=%s",
                    ctx.subject,
                    path,
                )
                return ("dispatch", ctx)
            logger.warning(
                "[AuthAudit] event=bearer_rejected path=%s method=%s",
                path,
                method,
            )
            return (
                "401",
                "invalid_bearer",
                # Cheap-minor taxonomy fix (2026-10-11 merge gate): the
                # bearer lane's challenge text was session-lane
                # vocabulary ("Session expired or invalid") — the 401
                # taxonomy is lane-neutral now (no credential-class
                # hint; enumeration-resistance-friendly, T4.3).
                'Bearer realm="ensemble", error="invalid_token", '
                'error_description="Credential expired or invalid"',
            )

        # Cookie lane.
        session_id = cookies.get(SESSION_COOKIE_NAME, "")
        if session_id:
            ctx = await self._validate_cookie(scope, session_id)
            if ctx is not None:
                # C1 CSRF predicate — cookie lane only (bearer lane is
                # exempt above; this lane carries ambient authority).
                csrf_verdict = self._csrf_verdict(scope, headers, cookies, method)
                if csrf_verdict is not None:
                    return csrf_verdict
                return ("dispatch", ctx)
            # A stale/invalid cookie falls through to the unauthenticated
            # contract below (the browser will re-login).

        return self._unauthenticated_action(scope, method, path, headers)

    # ── classification helpers ─────────────────────────────────────

    def _resolve_tagged(self, path: str) -> str | None:
        """First tagged-template match in registration order (or None)."""
        for entry in self._ordered:
            if entry.prefix_match:
                # Mount boundary semantics: the mount path itself or any
                # subpath (mirrors Starlette Mount.matches).
                template = entry.template
                if path == template or path.startswith(
                    template if template.endswith("/") else template + "/"
                ):
                    return entry.kind
            elif template_matches(entry.template, path):
                return entry.kind
        return None

    @staticmethod
    def _is_spa_asset(path: str) -> bool:
        """SPA shell / asset shape ([REQ] §5.7).

        The carve-out prefixes are compared against the path WITHOUT its
        leading slash — exactly how the SPA catch-all route sees it
        (Starlette hands ``/{path:path}`` the path minus the root
        slash, and ``serve_ui_assets`` tests ``path.startswith('api')``).
        Comparing with the slash included would classify ``/api/...``
        as public assets — the AC-23 smuggle class.
        """
        if path == "/":
            return True
        stripped = path[1:] if path.startswith("/") else path
        return not stripped.startswith(SPA_CARVE_OUT_PREFIXES)

    # ── lane validators (resolved from app.state at request time) ──

    def _auth_service(self, scope: dict[str, Any]) -> Any:
        app_ref = scope.get("app")
        return getattr(getattr(app_ref, "state", None), "auth_service", None)

    async def _verify_bearer(self, scope: dict[str, Any], token: str) -> AuthContext | None:
        service = self._auth_service(scope)
        if service is None or not token:
            return None
        return await service.verify_static_token(token)

    async def _validate_cookie(
        self, scope: dict[str, Any], session_id: str
    ) -> AuthContext | None:
        service = self._auth_service(scope)
        if service is None:
            return None
        client = scope.get("client")
        client_ip = client[0] if client else None
        return await service.validate_session(session_id, client_ip=client_ip)

    def _csrf_verdict(
        self,
        scope: dict[str, Any],
        headers: dict[str, str],
        cookies: dict[str, str],
        method: str,
    ) -> tuple[Any, ...] | None:
        """C1 predicate (D8 v1.2) + C-2 comparator (2026-10-11 review).

        Origin/Referer same-origin check against a SERVER-derived
        :class:`CSRFOriginPolicy` (never a bare client-Host comparison —
        C-2a); plus the token-echo check ONLY when ``X-CSRF-Token`` is
        present (OD-15 timing). Returns a ("403", error) action on
        rejection, None when the request may proceed.
        """
        if not csrf_applies(method, headers):
            return None
        policy = self._csrf_origin_policy(scope, headers)
        if not validate_browser_lane_origin(headers, policy=policy):
            logger.warning(
                "[AuthAudit] event=csrf_origin_rejected path=%s method=%s",
                scope.get("path", "?"),
                method,
            )
            return ("403", "CSRF_ORIGIN_MISMATCH")
        if "x-csrf-token" in headers and not validate_csrf_header(headers, cookies):
            logger.warning(
                "[AuthAudit] event=csrf_token_rejected path=%s method=%s",
                scope.get("path", "?"),
                method,
            )
            return ("403", "CSRF_TOKEN_MISMATCH")
        return None

    # ── C-2 policy derivation (server-side facts only) ──────────────

    def _operator_audience(
        self, external_base: str | None
    ) -> tuple[frozenset[str], frozenset[str]]:
        """The C-2a operator audience ``(hosts, origins)``, cached.

        Cache key includes the live-views fallback value: the lifespan
        one-shot copy lands on the SAME AuthConfig object this
        middleware holds, so a cache formed before the copy would go
        stale — re-derive whenever the fallback value changes.
        """
        cached = getattr(self, "_audience_cache", None)
        if cached is not None and cached[0] == (external_base or ""):
            return cached[1], cached[2]
        hosts, origins = parse_operator_audience(self._auth_config)
        self._audience_cache = ((external_base or ""), hosts, origins)
        return hosts, origins

    def _csrf_origin_policy(
        self, scope: dict[str, Any], headers: dict[str, str]
    ) -> "CSRFOriginPolicy":
        """Build the per-request comparator policy (C-2a/b/c)."""
        cfg = self._auth_config
        host_header = (headers.get("host") or "").strip().lower()
        dev = bool(cfg is not None and cfg.allow_insecure_cookie)
        peer_loopback = peer_is_loopback(scope.get("client"))
        # (c) Trust switch — ALIGNED WITH live-views: the configured
        # ``live_views.external_base_url`` (its one-shot AuthConfig
        # copy) is the operator declaring a proxy front. NO parallel
        # flag; unset ⇒ scope scheme only (fail-closed behind proxies
        # that do not declare).
        external_base = (
            getattr(cfg, "live_view_external_base_url_fallback", None)
            if cfg is not None
            else None
        )
        scheme = _effective_request_scheme(scope, headers, external_base)
        audience_hosts, audience_origins = self._operator_audience(
            external_base
        )
        host_name = _split_host_header(host_header)
        host_trusted = host_name in audience_hosts or (
            dev and peer_loopback and _host_is_localhost(host_name)
        )
        return CSRFOriginPolicy(
            request_host=host_header,
            request_scheme=scheme,
            host_is_trusted=host_trusted,
            localhost_carve_allowed=dev and peer_loopback,
            allowlisted_origins=audience_origins,
        )

    # ── responses ──────────────────────────────────────────────────

    def _unauthenticated_action(
        self,
        scope: dict[str, Any],
        method: str,
        path: str,
        headers: dict[str, str],
    ) -> tuple[Any, ...]:
        """FR-24: 302 for browsers, 401 + WWW-Authenticate otherwise."""
        accept = headers.get("accept", "")
        wants_html = "text/html" in accept.lower()
        if not wants_html and _looks_like_browser(accept):
            # Accept: */* fallback — the FR-24 path-family heuristic
            # (per [TA] §401 Contract: the known machine path families
            # get the 401 contract; everything else under */* is
            # treated as a browser).
            wants_html = not any(
                path.startswith(prefix) or path == prefix.rstrip("/")
                for prefix in _401_HEURISTIC_PREFIXES
            )
        if wants_html:
            raw_path = path
            query_string = scope.get("query_string", b"")
            if query_string:
                try:
                    raw_path = f"{path}?{query_string.decode('latin-1')}"
                except UnicodeDecodeError:
                    raw_path = path
            return_to = sanitize_return_to(_raw_return_to(scope) or raw_path)
            location = f"/api/auth/login?return_to={_quote(return_to)}"
            logger.info(
                "[AuthAudit] event=request_denied_302 path=%s method=%s "
                "return_to=%s",
                path,
                method,
                # Minor #5: the audit leg logs the SANITIZED value
                # (≤1024 by construction) truncated at 256 — a long
                # client-supplied path can never ride the log whole.
                _audit_truncate(return_to),
            )
            return ("302", location)
        logger.info(
            "[AuthAudit] event=request_denied_401 path=%s method=%s "
            "reason=no_credentials",
            path,
            method,
        )
        return ("401", "no_credentials", _WWW_AUTHENTICATE)

    async def _respond_401(
        self,
        send: Any,
        *,
        reason: str,
        www_authenticate: str = _WWW_AUTHENTICATE,
    ) -> None:
        await _send_json(
            send,
            401,
            {"error": "AUTH_REQUIRED", "message": "Authentication required"},
            headers={"WWW-Authenticate": www_authenticate},
        )

    async def _respond_403(self, send: Any, error: str) -> None:
        await _send_json(
            send,
            403,
            {"error": error, "message": "Request rejected by CSRF guard"},
        )


# ── module-level pure helpers ────────────────────────────────────────


def _effective_request_scheme(
    scope: dict[str, Any],
    headers: dict[str, str],
    external_base: str | None,
) -> str:
    """C-2c scheme resolution — live-views-aligned trust chain.

    * External base configured (the operator declared a proxy front via
      ``live_views.external_base_url``): a well-formed
      ``X-Forwarded-Proto`` (first hop of a comma list) is TRUSTED for
      that audience; without one, the operator-declared base scheme is
      the trusted fallback (BaseURLResolver tier-1 semantics).
    * No external base: ``scope["scheme"]`` — an XFP header from a
      client nobody declared is UNTRUSTED (fail-closed: an https Origin
      behind an undeclared TLS proxy 403s until the operator sets the
      base — the runbook mandate, not a new flag).
    """
    if external_base:
        xfp = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if xfp in ("http", "https"):
            return xfp
        from urllib.parse import urlsplit

        base_scheme = urlsplit(external_base.strip()).scheme.lower()
        if base_scheme in ("http", "https"):
            return base_scheme
    return (scope.get("scheme") or "http").lower()


def _split_host_header(host_header: str) -> str:
    """Hostname part of a ``Host`` header value (brackets + port off).

    Delegates to the cookie-module parser — one implementation of the
    Host-header shape rule for both middleware and policy legs."""
    return cookie_split_host_header(host_header)


def _looks_like_browser(accept: str) -> bool:
    """True for ``Accept: */*`` / missing Accept (the heuristic's
    fallback trigger). An explicit non-HTML Accept (e.g.
    ``application/json``) is a machine client — no heuristic."""
    lowered = (accept or "").lower()
    if "text/html" in lowered:
        return False
    if "application/json" in lowered:
        return False
    return lowered.strip() in ("", "*/*")


def _raw_return_to(scope: dict[str, Any]) -> str | None:
    """The inbound ``return_to`` query parameter (if any) — sanitized by
    the caller; used when the login bounce already carries one."""
    from urllib.parse import parse_qs, urlsplit

    query = scope.get("query_string", b"")
    try:
        params = parse_qs(urlsplit("?" + query.decode("latin-1")).query)
    except (UnicodeDecodeError, ValueError):
        return None
    values = params.get("return_to")
    return values[0] if values else None


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="/")


def _header_dict(scope: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_key, raw_value in scope.get("headers", []) or []:
        try:
            headers[raw_key.decode("latin-1").lower()] = raw_value.decode(
                "latin-1"
            )
        except (UnicodeDecodeError, AttributeError):
            continue
    return headers


async def _send_redirect(send: Any, status: int, location: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"location", location.encode("latin-1")),
                (b"content-length", b"0"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


async def _send_json(
    send: Any,
    status: int,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> None:
    import json

    body = json.dumps(payload).encode("utf-8")
    raw_headers = [(b"content-type", b"application/json")]
    for key, value in (headers or {}).items():
        raw_headers.append((key.encode("latin-1"), value.encode("latin-1")))
    raw_headers.append((b"content-length", str(len(body)).encode("latin-1")))
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": raw_headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = ["AuthMiddleware", "SPA_CARVE_OUT_PREFIXES", "sanitize_return_to"]
