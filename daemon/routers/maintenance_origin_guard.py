"""Maintenance Console Origin guard — AM-1 / INV-10.

Sole browser-borne defense layer for the destructive endpoints
(/status, /dry-run, /execute, /runs/{id}). ``/availability`` is
EXEMPT (the FE gear-menu probe must see disabled state cleanly; it
is non-destructive).

Rule order (fail-closed at the end):

    1. No ``Origin`` header → allow (curl, systemd, programmatic).
    2. Same-origin (Origin matches the daemon's own external origin
       derived from request Host + scheme at request time) → allow.
       Browsers attach Origin even on same-origin POSTs; deny-all
       would 403 the daemon-served SPA's own execute — the shipped
       product's primary flow. ``X-Forwarded-*`` is untrusted and
       NOT consulted (no proxy chain assumed in v1 [R-7]).
    3. ``Origin`` host ∈ localhost-family (``localhost``,
       ``127.0.0.1``, ``[::1]``, any port, http/https) → allow.
       Zero-config dev: FE dev server on :4199 proxies to :8079;
       the daemon sees ``Origin: http://localhost:4199``.
    4. ``Origin ∈ MAINTENANCE_TRUSTED_ORIGINS`` (CSV env, default
       empty) → allow (LAN-browser opt-in).
    5. Anything else (incl. ``Origin: null`` from sandboxed iframes /
       ``file://``) → 403 ``origin_not_trusted``.

CORS caveat (architect §C-1 — AM-1): under CORS
``allow_origins=["*"]`` + ``allow_credentials=True``
(``daemon/api.py:2613-2619``), non-credentialed cross-origin
fetches can read response bodies. The Origin guard is the ONLY
browser-side gate in v1 — it complements the server-side 6-gate
mistake/staleness model (the gates catch operator error; the guard
catches hostile browser choreography).

Every 403 refusal logs ONE INFO line [R-21, v3 fix pass] —
(origin, peer_ip, request path) — forensics for near-miss /
suspect-origin requests without spamming WARNING / ERROR lanes.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Iterable
from urllib.parse import urlparse

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Localhost-family suffix set (lowercase). The full origin host is
# matched after a ``lower()`` and any IPv6 brackets are stripped.
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Module-level parsed set, refreshed on first use + on env-touch (the
# repo does NOT watch the env live — the operator restarts the daemon
# after editing ``MAINTENANCE_TRUSTED_ORIGINS``; consistent with the
# boot-read kill-switch).
_trusted_origins_cache: tuple[frozenset[str], int] | None = None
# (frozen-set-of-lowercase-origins, monotonic-counter-version)


def _parse_trusted_origins() -> frozenset[str]:
    """Parse ``MAINTENANCE_TRUSTED_ORIGINS`` into a lowercased allow-set.

    Accepts a CSV (``"http://a, http://b"``) or empty string. Trailing
    whitespace + empty entries are filtered; values are lowercased.
    Edge cases (fold-in (c)): an EMPTY or whitespace-only value yields
    ``frozenset()`` (no explicit opt-ins beyond localhost auto-trust),
    and MALFORMED entries are inert — they can never authorize a
    request because the guard's rule 4 comparison runs after URL host
    extraction, and a non-URL ``Origin`` never reaches it (fail-closed
    by construction, not by rejecting the entry at parse time).
    """
    raw = os.environ.get("MAINTENANCE_TRUSTED_ORIGINS", "")
    out: set[str] = set()
    for entry in raw.split(","):
        e = entry.strip().lower()
        if not e:
            continue
        # Tolerate malformed entries (no scheme, garbage chars) by
        # using the literal as-is — the comparison is string-level.
        out.add(e)
    return frozenset(out)


def get_trusted_origins() -> frozenset[str]:
    """Return the cached ``MAINTENANCE_TRUSTED_ORIGINS`` set.

    Caches the parsed set on first read; the env is NOT watched
    live (the operator restarts the daemon after edits; the
    boot-read kill-switch is the precedent).
    """
    global _trusted_origins_cache
    if _trusted_origins_cache is None:
        _trusted_origins_cache = (_parse_trusted_origins(), 1)
    return _trusted_origins_cache[0]


def reset_trusted_origins_cache() -> None:
    """Test hook — clear the parsed-allow-set cache.

    Tests that mutate ``MAINTENANCE_TRUSTED_ORIGINS`` mid-process
    call this to force a re-parse on the next ``get_trusted_origins``
    read.
    """
    global _trusted_origins_cache
    _trusted_origins_cache = None


def _host_is_localhost(host: str) -> bool:
    """True when ``host`` is one of localhost-family names.

    IPv6 hosts arrive bracketed (``[::1]``) — strip brackets before
    comparison.
    """
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h in _LOCALHOST_HOSTS:
        return True
    # Loopback range (127.0.0.0/8) — broader localhost match for
    # LAN misconfigs. IPv4 only (IPv6 has its own ``::1`` form).
    try:
        ip = ipaddress.ip_address(h)
        if isinstance(ip, ipaddress.IPv4Address) and ip.is_loopback:
            return True
    except ValueError:
        pass
    return False


def _scheme_host(origin: str) -> tuple[str, str]:
    """Best-effort scheme+host extraction (lowercased)."""
    parsed = urlparse(origin)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    return scheme, host


def _request_origin(request: Request) -> str | None:
    """Return the request ``Origin`` header (or ``None``).

    FastAPI's ``Request.headers.get('origin')`` is case-insensitive
    per HTTP/1.1; the raw header is preserved for the audit log
    (case-preserving).
    """
    return request.headers.get("origin")


def _normalize_origin(origin: str) -> tuple[str, str, int | None] | None:
    """Normalize an origin URL to (scheme, host, effective-port).

    The port is defaulted per scheme (80/http, 443/https) so
    ``http://host`` and ``http://host:80`` compare equal, and
    non-default ports are preserved — a same-origin match is a full
    scheme+host+port match (an origin IS scheme+host+port). Returns
    ``None`` for unparseable origins.
    """
    from urllib.parse import urlparse as _urlparse

    try:
        parsed = _urlparse(origin)
    except ValueError:
        return None
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def _same_origin(request: Request, origin: str) -> bool:
    """True when ``origin`` matches the daemon's own external origin.

    The daemon derives its external origin from the request ``Host``
    header + the request scheme (ASGI scope) at request time. The
    Host header carries the host:port the operator addressed; the
    scheme comes from ``request.url.scheme``. ``X-Forwarded-*``
    headers are UNTRUSTED and NOT consulted in v1 (no proxy chain
    assumed — R-7). The comparison is scheme+host+PORT (defaults
    normalized per scheme) — dropping the port would let a page
    served on a different port of the same host masquerade as
    same-origin.
    """
    normalized = _normalize_origin(origin)
    if normalized is None:
        return False
    origin_scheme, origin_host, origin_port = normalized
    host_header = (request.headers.get("host") or "").strip().lower()
    if not host_header:
        return False
    request_scheme = (request.url.scheme or "http").lower()
    req_normalized = _normalize_origin(f"{request_scheme}://{host_header}")
    if req_normalized is None:
        return False
    req_scheme, req_host, req_port = req_normalized
    return (
        origin_scheme == req_scheme
        and origin_host == req_host
        and origin_port == req_port
    )


async def require_trusted_origin(request: Request) -> None:
    """FastAPI dependency — enforce the AM-1 / INV-10 Origin guard.

    Applied FIRST on /status, /dry-run, /execute, /runs/{run_id};
    /availability is EXEMPT (the router does not include this
    dependency on the availability route — see
    ``daemon/routers/maintenance.py``).

    Raises ``HTTPException(403, detail={"error": "origin_not_trusted",
    "message": ...})`` on every non-match. The body shape matches
    the FROZEN structured-dict pattern (A-8 RATIFIED, plane.py).
    """
    origin = _request_origin(request)
    # Rule 1 — no Origin header (curl, systemd, programmatic).
    if origin is None:
        return
    origin_lc = origin.strip().lower()
    _scheme, host = _scheme_host(origin_lc)
    if not host:
        # ``Origin: null`` from sandboxed iframes / ``file://`` — rule 5.
        _emit_refusal_log(request, origin)
        raise HTTPException(
            status_code=403,
            detail={
                "error": "origin_not_trusted",
                "message": "Origin guard refused the request",
            },
        )
    # Rule 2 — same-origin (scheme+host+port match against the
    # request's derived external origin; the FULL origin string is
    # compared so ports are honored).
    if _same_origin(request, origin_lc):
        return
    # Rule 3 — localhost-family (any port, http/https).
    if _host_is_localhost(host):
        return
    # Rule 4 — explicit opt-in via ``MAINTENANCE_TRUSTED_ORIGINS``.
    trusted = get_trusted_origins()
    if origin_lc in trusted:
        return
    # Rule 5 — fail-closed.
    _emit_refusal_log(request, origin)
    raise HTTPException(
        status_code=403,
        detail={
            "error": "origin_not_trusted",
            "message": "Origin guard refused the request",
        },
    )


def _emit_refusal_log(request: Request, origin: str | None) -> None:
    """One INFO line per refused Origin — R-21, v3 fix pass."""
    try:
        peer_ip = request.client.host if request.client else None
    except Exception:  # noqa: BLE001
        peer_ip = None
    logger.info(
        "Origin guard refused: origin=%r peer_ip=%s path=%s",
        origin,
        peer_ip,
        request.url.path,
    )


__all__ = ["require_trusted_origin", "get_trusted_origins", "reset_trusted_origins_cache"]