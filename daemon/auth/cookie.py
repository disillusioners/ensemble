"""Cookie + CSRF layer — oauth2-auth Phase 3 (T3.4 / D8 AMEND v1.2 C1).

Two cookies, one lane decision:

* ``ensemble_sid`` — the session credential. ``HttpOnly`` ALWAYS;
  ``Secure`` per ``auth.cookie_secure`` (the flag actually DRIVES the
  attribute — the security-review minor #1: ``allow_insecure_cookie``
  gates config VALIDATION, not the attribute; the
  ``cookie_secure=false + allow_insecure_cookie=true`` LAN opt-in
  legitimately ships no Secure flag, the default ships Secure);
  ``SameSite`` per ``auth.cookie_samesite`` (default Lax —
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
→ allow"). For browser-lane state-changing requests the origin must
pass the C-2 comparator (below). Headerless machine/bearer requests are
EXEMPT — no ambient cookie authority means the CSRF surface does not
exist. ``SameSite=Lax`` covers the residual cross-site risk. This is
what makes AC-39 hold BY CONSTRUCTION: the unchanged SPA's mutations
carry Origin (same-origin) and no CSRF header, and succeed.

C-2 comparator (2026-10-11 security review — composite (a)+(b)+(c)):
the v1 comparator compared client ``Host`` vs client ``Origin`` —
tautological under DNS rebinding — and its localhost carve accepted the
whole 127.0.0.0/8 range unconditionally. The rewritten
:func:`validate_browser_lane_origin` consumes a server-derived
:class:`CSRFOriginPolicy`:

* (a) the same-origin exact match only VOUCHES when the request Host is
  allowlisted server-side (operator audience ∪ dev loopback, the
  ``maintenance_origin_guard._host_is_allowed`` precedent), and the
  dev loopback carve is additionally gated on the ASGI
  ``scope["client"]`` TCP peer being loopback (a remote client can
  spoof ``Host: localhost``; it cannot spoof its peer address);
* (b) the localhost carve is the EXACT set ``{localhost, 127.0.0.1,
  ::1}`` — the 127.0.0.0/8 blanket is gone — behind the dev gate
  (``auth.allow_insecure_cookie``, the existing dev-identity class);
* (c) the effective scheme honors ``X-Forwarded-Proto`` when the
  live-views trust signal is present (``external_base_url`` configured
  ⇒ the operator declared a proxy front — the same signal chain
  ``live_views.BaseURLResolver`` uses; NO parallel trust flag).
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import ipaddress
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

#: Localhost EXACT-match set — the C-2b dev carve universe. The
#: 127.0.0.0/8 RANGE match is REMOVED (security review 2026-10-11):
#: only these three spellings qualify, and only behind the dev gate +
#: loopback-peer gate (see :class:`CSRFOriginPolicy`).
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
    """Set ``ensemble_sid`` with the D8 flag set (T3.4).

    Minor #1 (2026-10-11 security review): ``Secure`` keys off
    ``auth_config.cookie_secure`` — the flag that EXISTS to drive this
    attribute. The previous ``not allow_insecure_cookie`` keying made
    ``cookie_secure=false + allow_insecure_cookie=true`` still ship a
    Secure cookie, silently contradicting the LAN opt-in.
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=auth_config.session_ttl_seconds,
        path="/",
        secure=auth_config.cookie_secure,
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
    and Secure mirror the session cookie so the pair travels together
    (same ``cookie_secure`` keying — minor #1).
    """
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token_for_session(session_id),
        max_age=auth_config.session_ttl_seconds,
        path="/",
        secure=auth_config.cookie_secure,
        httponly=False,
        samesite=auth_config.cookie_samesite.lower(),
    )


def clear_session_cookies(response: Any, *, auth_config: AuthConfig) -> None:
    """Logout: clear both cookies (``Max-Age=0`` per [TA] §Logout).

    Attribute mirror of the setters (incl. the ``cookie_secure``
    keying) — a delete whose attributes mismatch the set cannot
    overwrite the cookie in every browser."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=auth_config.cookie_secure,
        httponly=True,
        samesite=auth_config.cookie_samesite.lower(),
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        secure=auth_config.cookie_secure,
        httponly=False,
        samesite=auth_config.cookie_samesite.lower(),
    )


def parse_cookies_from_scope(scope: Any) -> dict[str, str]:
    """Parse the ASGI ``Cookie`` headers into a dict.

    Only the header KEY lookup is lowercase (ASGI requires lowercase
    header names); cookie NAMES are preserved verbatim as sent — the
    parser does not case-fold them (``ensemble_sid`` /
    ``ensemble_csrf`` are lowercase by convention, not by coercion).
    Never raises: malformed pairs are skipped (the middleware's
    never-raise contract, D3.9).

    Duplicate-name policy (minor-6, 2026-10-11 merge gate — was
    residual last-wins): **FIRST occurrence wins**, per the RFC 6265
    §5.4 cookie-header algorithm ("if the cookie-name ... already
    appears in the list, ignore this pair") — the browser-sent order
    is preserved by every major client, so the first pair is the one
    the cookie jar considered authoritative. Deterministic either way;
    first-wins is the RFC posture and closes the last-wins ambiguity
    where a smuggled second ``ensemble_sid=`` pair would silently
    override the legitimate one. (Blast radius under double-submit is
    low — the session id must still validate server-side — but the
    parsing must not be attacker-steerable.)
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
            cookies.setdefault(name, value.strip())
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
    policy: "CSRFOriginPolicy",
) -> bool:
    """C-2 comparator (2026-10-11 security review) — browser-lane origin.

    Consumes a server-derived :class:`CSRFOriginPolicy` (the middleware
    builds it per request; NOTHING in it comes from a client header
    alone). ``Origin`` first, then ``Referer``; a present-but-empty
    header fails closed. Three allow legs, in order:

    1. Same-origin exact match — only when the request Host is trusted
       server-side (``policy.host_is_trusted``: operator-audience
       membership, or the dev loopback carve). The bare Host-vs-Origin
       comparison this leg used to run unconditionally was satisfiable
       by a DNS-rebinding pair (their Origin and rebound Host agree by
       construction) — the allowlist breaks the tautology.
    2. Operator-named origin — an exact normalized match against the
       explicit audience list (``allowed_origins`` / external bases;
       the maintenance guard's rule-4 opt-in precedent). Covers the
       SPA-served-from-a-different-origin deployment.
    3. Dev loopback carve — EXACT ``{localhost, 127.0.0.1, ::1}`` only
       (the 127.0.0.0/8 blanket is GONE, C-2b), behind BOTH the dev
       gate (``allow_insecure_cookie``) and the loopback TCP-peer gate
       (C-2a — a remote client can send ``Host: localhost`` and
       ``Origin: http://localhost:4199``; it cannot make its peer
       address loopback).
    """
    origin = headers.get(_ORIGIN_HEADER) or headers.get(_REFERER_HEADER)
    if not origin:
        # Predicate says the lane exists (one of the two was present);
        # a present-but-empty header fails closed.
        return False
    parts = urlsplit(origin)
    if not parts.scheme or not parts.netloc:
        return False
    supplied = _normalize_origin(
        parts.scheme, parts.hostname or "", parts.port
    )
    if supplied is None:
        return False
    # Leg 1 — same-origin exact, vouched only by a trusted Host. The
    # Host header's port (when explicitly sent) participates, mirroring
    # the browser's own Origin composition (port only when non-default).
    # Cheap-minor hardening (2026-10-11 merge gate): the comparison is
    # a constant-time BYTES compare (hmac.compare_digest over the
    # normalized UTF-8 encodings) — the comparator's allow decisions
    # are textual equality checks, and the 401/403-noise taxonomy they
    # feed should never be able to leak match-prefix timing.
    if policy.host_is_trusted:
        expected = _normalize_origin(
            policy.request_scheme,
            _split_host_header(policy.request_host),
            _port_of_host_header(policy.request_host),
        )
        if expected is not None and _origin_bytes_eq(supplied, expected):
            return True
    # Leg 2 — explicit operator-named origin. Constant-time over the
    # allowlist entries (same cheap-minor discipline as leg 1); length
    # differences still gate (compare_digest is length-sensitive by
    # design — a mismatched length is a non-match, never a prefix hint
    # that matters here: the allowlist is server-configured, not
    # secret-bearing).
    for allowed in sorted(policy.allowlisted_origins):
        if _origin_bytes_eq(supplied, allowed):
            return True
    # Leg 3 — dev-gated, peer-gated EXACT loopback carve.
    if policy.localhost_carve_allowed and _host_is_localhost(
        parts.hostname or ""
    ):
        return True
    return False


@dataclasses.dataclass(frozen=True)
class CSRFOriginPolicy:
    """Everything :func:`validate_browser_lane_origin` may trust.

    Built per request by the middleware from SERVER-side facts only:
    the config-declared audience, the ASGI peer address, the live-views
    trust signal — never from a client header in isolation (C-2a).
    """

    #: The client ``Host`` header (lowercased, as sent, port included).
    #: Only vouches for leg 1 when ``host_is_trusted`` is True.
    request_host: str
    #: Effective request scheme — ``scope["scheme"]``, or the trusted
    #: ``X-Forwarded-Proto`` value / declared base scheme when the
    #: live-views external base is configured (C-2c).
    request_scheme: str
    #: Host header ∈ operator audience ∪ (dev ∧ loopback-peer) loopback.
    host_is_trusted: bool
    #: Dev gate (``allow_insecure_cookie``) ∧ ASGI peer is loopback.
    localhost_carve_allowed: bool
    #: Normalized ``scheme://host[:port]`` strings the operator named
    #: (``allowed_origins`` + external bases) — leg 2 universe.
    allowlisted_origins: frozenset[str]


def parse_operator_audience(
    auth_config: AuthConfig | None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Derive the C-2a operator audience: ``(hosts, origins)``.

    Sources (every parseable one joins; malformed entries are inert —
    fail-closed by construction, never authoritative):

    * ``auth.allowed_origins`` — REQUIRED non-empty when auth is ON
      (§6.3 row 5 / D15), so the audience is never empty in production.
    * ``auth.external_redirect_base`` — the tier-1 canonical base; an
      operator-set base declares that origin as the daemon's audience.
    * ``auth.live_view_external_base_url_fallback`` — the one-shot copy
      of ``live_views.external_base_url`` (AMEND v1.1/D2): the SAME
      signal the C-2c XFP trust derives from, and the same
      "operator declares a proxy front" declaration live-views uses.
    """
    hosts: set[str] = set()
    origins: set[str] = set()
    if auth_config is None:
        return frozenset(hosts), frozenset(origins)

    def _absorb(value: str | None) -> None:
        if not value:
            return
        parts = urlsplit(value.strip())
        if not parts.scheme or not parts.hostname:
            return
        normalized = _normalize_origin(
            parts.scheme, parts.hostname, parts.port
        )
        if normalized is None:
            return
        origins.add(normalized)
        hosts.add(parts.hostname.lower())

    for entry in auth_config.allowed_origins or []:
        _absorb(entry)
    _absorb(auth_config.external_redirect_base)
    _absorb(getattr(auth_config, "live_view_external_base_url_fallback", None))
    return frozenset(hosts), frozenset(origins)


def peer_is_loopback(client: Any) -> bool:
    """True when the ASGI ``scope["client"]`` peer is a loopback address.

    Handles IPv4-mapped IPv6 (``::ffff:127.0.0.1`` → the mapped IPv4
    address, then loopback — the minor #7 normalization discipline).
    A missing/unparseable peer fails CLOSED (False) — a client address
    we cannot interpret never counts as loopback.
    """
    if not client:
        return False
    raw = client[0] if isinstance(client, (tuple, list)) else client
    if not isinstance(raw, str) or not raw:
        return False
    try:
        addr = ipaddress.ip_address(raw.split("%")[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def _origin_bytes_eq(left: str, right: str) -> bool:
    """Constant-time equality over normalized origin strings.

    Cheap-minor (2026-10-11 merge gate): the C-2 comparator's textual
    equality checks (leg-1 same-origin, leg-2 allowlist) compare the
    UTF-8 encodings via ``hmac.compare_digest`` instead of ``str ==
``/set membership, so the 401/403 rejection taxonomy the comparator
    feeds cannot leak match-prefix timing from short-circuiting string
    comparison.
    """
    return hmac.compare_digest(
        left.encode("utf-8"), right.encode("utf-8")
    )


def _normalize_origin(
    scheme: str, host: str, port: int | None
) -> str | None:
    """Normalize one origin to ``scheme://host[:port]`` (lowercase).

    IPv6 hosts are re-bracketed; the port rides only when explicitly
    present (the same composition the browser uses in ``Origin``).
    Returns None for a hostless value.
    """
    scheme = (scheme or "").lower()
    host = (host or "").lower()
    if not scheme or not host:
        return None
    if ":" in host:  # bare IPv6 literal — re-bracket
        host = f"[{host}]"
    if port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def _split_host_header(host_header: str) -> str:
    """Hostname part of a ``Host`` header value (brackets + port off)."""
    value = (host_header or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = f"//{value}"
    return urlsplit(value).hostname or ""


def _port_of_host_header(host_header: str) -> int | None:
    """Explicit port of a ``Host`` header value (None when default/absent)."""
    value = (host_header or "").strip().lower()
    if not value:
        return None
    if "://" not in value:
        value = f"//{value}"
    try:
        return urlsplit(value).port
    except ValueError:
        return None


def _host_is_localhost(host: str) -> bool:
    """EXACT localhost names only (C-2b): ``localhost``, ``127.0.0.1``,
    ``::1``. IPv6 arrives bracketed — strip brackets before comparison.

    The previous 127.0.0.0/8 range check is REMOVED (2026-10-11
    security review): a blanket /8 widened the dev carve to 16M
    addresses unconditionally. Anything outside the exact set —
    including other 127.x.x.x spellings — fails closed here.
    """
    h = (host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h in _LOCALHOST_HOSTS


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


__all__ = [
    "CSRF_COOKIE_NAME",
    "CSRF_STATE_CHANGING_METHODS",
    "CSRFOriginPolicy",
    "SESSION_COOKIE_NAME",
    "clear_session_cookies",
    "csrf_applies",
    "csrf_token_for_session",
    "parse_cookies_from_scope",
    "parse_operator_audience",
    "peer_is_loopback",
    "set_csrf_cookie",
    "set_session_cookie",
    "validate_browser_lane_origin",
    "validate_csrf_header",
]
