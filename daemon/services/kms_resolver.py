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

LANE-2 (env-ref) — 2026-10-02 bridge commission
(``feature/od-self-provisioning``): a second marker shape
``__KMS_ENV__<VARNAME>__`` resolves to ``os.environ[<VARNAME>]`` at
spawn time. Same R1/disk-only invariant: the marker carries NO secret
material at rest; plaintext exists ONLY in the daemon's environment
and surfaces ONLY in the subprocess env at spawn. Fail-closed on a
missing var (clear error naming the var, never a value, never a silent
empty-string fallback). Two marker lanes are greppably distinct by
prefix — :data:`KMS_MARKER_PREFIX` (``__KMS_REF__``) vs
:data:`KMS_ENV_MARKER_PREFIX` (``__KMS_ENV__``).

Boundary:
* The resolver takes a config dict and returns a NEW dict. Callers MUST
  pass the spawned-time-only dict; storing the resolver's return value
  is a regression.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from daemon.services.kms_lite import (
    KMS_ENV_MARKER_PREFIX,
    KMS_ENV_MARKER_RE,
    KMS_ENV_MARKER_SUFFIX,
    KMS_HANDLE_PREFIX,
    KMS_MARKER_PREFIX,
    KMS_MARKER_SUFFIX,
    kms_resolve_handle,
)

logger = logging.getLogger(__name__)


#: Compiled regex matching a single LANE-1 marker value. The handle
#: portion is captured as group 1. A fullmatch (not partial) is
#: required so that legitimate plaintext values like
#: ``LOG_LEVEL=__KMS_REF__whatever__hint`` (rare, but possible in
#: custom env vars) are not mangled.
_MARKER_PATTERN = re.compile(
    re.escape(KMS_MARKER_PREFIX)
    + r"("
    + re.escape(KMS_HANDLE_PREFIX)
    + r"[A-Za-z0-9_-]+)"
    + re.escape(KMS_MARKER_SUFFIX)
)

#: Compiled regex matching a single LANE-2 env-ref marker value. The
#: variable-name portion is captured as group 1. Same fullmatch
#: discipline as :data:`_MARKER_PATTERN` — partial matches are
#: passed through (preserves the LANE-1 invariant and gives the
#: caller a sane behaviour for ``LOG_LEVEL=__KMS_ENV__FOO__hint``
#: composite values).
_ENV_MARKER_PATTERN = re.compile(KMS_ENV_MARKER_RE)


class KMSMarkerResolutionError(RuntimeError):
    """Raised when a stored marker references an unknown handle,
    OR when an env-ref marker names a missing env var.

    In day-1, the KMS store is in-memory and process-wide. If a marker
    row was written in a previous process lifetime and the daemon
    restarted, the resolver will encounter ``kms_resolve_handle`` → ``None``
    for that handle. Day-1 semantics: this is a configuration error —
    raise rather than silently fall through to the marker (which would
    leak the marker literal to the subprocess env, an obvious bug).

    For LANE-2 (env-ref): the equivalent failure is a ``__KMS_ENV__X__``
    marker whose named ``X`` is absent from ``os.environ``. Failure mode
    is identical (configuration error), and the error message names
    the VAR — never the value (there is no value at rest; the var was
    simply not present at spawn time).
    """


def _resolve_value(value: str) -> str:
    """Resolve a single env/header value.

    Order of attempts (LANE-1 first, LANE-2 second):

    1. LANE-1: ``__KMS_REF__<HANDLE>__`` → KMS-Lite store plaintext.
       Unknown handle → :class:`KMSMarkerResolutionError`.
    2. LANE-2: ``__KMS_ENV__<VAR>__`` → ``os.environ[<VAR>]``.
       Missing var → :class:`KMSMarkerResolutionError` (fail-closed;
       a literal-marker fallback would land a ``__KMS_ENV__X__`` string
       in the subprocess env, an obvious bug).
    4. Plaintext — returned unchanged (back-compat for ``LOG_LEVEL``
       etc.).
    """
    m1 = _MARKER_PATTERN.fullmatch(value)
    if m1:
        handle = m1.group(1)
        plaintext = kms_resolve_handle(handle)
        if plaintext is None:
            raise KMSMarkerResolutionError(
                f"KMS marker references unknown handle: {handle}"
            )
        return plaintext

    m2 = _ENV_MARKER_PATTERN.fullmatch(value)
    if m2:
        var_name = m2.group(1)
        # ``os.environ.get`` returns ``None`` only if the var was
        # absent — an empty-string value (deliberate or accidental) is
        # returned as ``""``. The day-1 contract is "name only, fail
        # closed on missing"; an empty value still passes through to
        # the subprocess env where the MCP server decides what to do
        # with it (mirrors how a plain ``mcp_set_env`` write of an
        # empty value would behave). We do NOT silent-fallback to a
        # literal marker (the bug class this contract closes).
        if var_name not in os.environ:
            raise KMSMarkerResolutionError(
                f"KMS env-ref marker names a missing env var: {var_name}"
            )
        # The var was present at the moment of the marker resolution;
        # the value lives only here in the NEW dict and is passed to
        # the spawn seam (R1 + LANE-2 invariant — plaintext is in-RAM
        # only, NEVER stored).
        return os.environ[var_name]

    return value


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
            handle (LANE-1) or names a missing env var (LANE-2).
            Callers should fail-closed on this — spawning the
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
    """Return ``True`` iff ``value`` is a full KMS marker (LANE-1 or LANE-2).

    Used by callers that want to distinguish marker values from
    plaintext at presentation time without performing the lookup.
    """
    return (
        _MARKER_PATTERN.fullmatch(value) is not None
        or _ENV_MARKER_PATTERN.fullmatch(value) is not None
    )


def is_env_marker(value: str) -> bool:
    """Return ``True`` iff ``value`` is a full LANE-2 env-ref marker.

    Narrower than :func:`is_marker` — discriminates LANE-2 from LANE-1
    so presentation layers (the redact_secrets helper, audit logs) can
    shape their output accordingly (e.g. echo the var NAME for LANE-2
    vs. the handle for LANE-1).
    """
    return _ENV_MARKER_PATTERN.fullmatch(value) is not None