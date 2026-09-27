"""KMS marker resolver (P3-WP6 — designer-agent mission).

Substitutes :data:`~daemon.services.kms_lite.KMS_MARKER_PREFIX`-prefixed
markers in MCP server ``env`` / ``headers`` dicts to plaintext, in-RAM
ONLY, immediately before the MCP subprocess is spawned.

Architecture invariant (R1 — closed here):

* The stored config ALWAYS retains markers. Plaintext reaches ONLY the
  subprocess environment via ``StdioServerParameters(...)`` (stdio
  transport) or the ``sse_client`` / ``streamablehttp_client`` headers
  dict (HTTP / SSE transports).
* Any code path that round-trips ``mcp_servers.config`` MUST read the
  row fresh from the DB. A cached plaintext rewrite of the row would
  destroy the marker. (See ``tests/unit/services/test_kms_resolver_marker_roundtrip.py``
  — the cached-rewrite invariant test detects a regression of this rule
  by simulating the failure mode.)
* The resolver is invoked at spawn time ONLY — never at config-load,
  never at API list-time. ``redact_secrets`` at
  ``daemon/routers/mcp_servers.py:57-111`` stays presentation-only and
  remains the explicit boundary between presentation and resolution.

Boundary:
* The resolver takes a config dict and returns a NEW dict. Callers MUST
  pass the spawned-time-only dict; storing the resolver's return value
  is a regression.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from daemon.services.kms_lite import (
    KMS_HANDLE_PREFIX,
    KMS_MARKER_PREFIX,
    KMS_MARKER_SUFFIX,
    kms_resolve_handle,
)

logger = logging.getLogger(__name__)


#: Compiled regex matching a single marker value. The handle portion is
#: captured as group 1. A fullmatch (not partial) is required so that
#: legitimate plaintext values like ``LOG_LEVEL=__KMS_REF__whatever__hint``
#: (rare, but possible in custom env vars) are not mangled.
_MARKER_PATTERN = re.compile(
    re.escape(KMS_MARKER_PREFIX)
    + r"("
    + re.escape(KMS_HANDLE_PREFIX)
    + r"[A-Za-z0-9_-]+)"
    + re.escape(KMS_MARKER_SUFFIX)
)


class KMSMarkerResolutionError(RuntimeError):
    """Raised when a stored marker references an unknown handle.

    In day-1, the KMS store is in-memory and process-wide. If a marker
    row was written in a previous process lifetime and the daemon
    restarted, the resolver will encounter ``kms_resolve_handle`` → ``None``
    for that handle. Day-1 semantics: this is a configuration error —
    raise rather than silently fall through to the marker (which would
    leak the marker literal to the subprocess env, an obvious bug).
    """


def _resolve_value(value: str) -> str:
    """Resolve a single env/header value.

    Marker → plaintext (looked up from KMS). Non-marker → returned
    unchanged (back-compat for ``LOG_LEVEL`` etc.).
    """
    m = _MARKER_PATTERN.fullmatch(value)
    if not m:
        return value
    handle = m.group(1)
    plaintext = kms_resolve_handle(handle)
    if plaintext is None:
        raise KMSMarkerResolutionError(
            f"KMS marker references unknown handle: {handle}"
        )
    return plaintext


def resolve_env(env: dict[str, str] | None) -> dict[str, str] | None:
    """Resolve markers in a stdio ``env`` dict. Returns a NEW dict.

    Args:
        env: The stored ``McpStdioConfig.env`` dict, as loaded from
            ``mcp_servers.config`` in the DB. NEVER mutated.

    Returns:
        A new dict suitable for passing to
        ``StdioServerParameters(env=...)``. ``None`` passes through as
        ``None``.

    Raises:
        KMSMarkerResolutionError: if any marker references an unknown
            handle. Callers should fail-closed on this — spawning the
            subprocess with a literal marker in its env would be a bug.
    """
    if env is None:
        return None
    out: dict[str, str] = {}
    for key, value in env.items():
        if isinstance(value, str):
            out[key] = _resolve_value(value)
        else:
            # Defensive: stored values are always strings, but if a
            # legacy row contains a non-str (e.g. ``None``), pass it
            # through untouched rather than coercing.
            out[key] = value
    return out


def resolve_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    """Resolve markers in an HTTP/SSE ``headers`` dict.

    Same contract as :func:`resolve_env` — the underlying mechanism is
    identical. Kept as a separate symbol so call sites at the HTTP/SSE
    transport seams read clearly.
    """
    return resolve_env(headers)


def is_marker(value: str) -> bool:
    """Return ``True`` iff ``value`` is a full KMS marker.

    Used by callers that want to distinguish marker values from
    plaintext at presentation time without performing the lookup.
    """
    return _MARKER_PATTERN.fullmatch(value) is not None