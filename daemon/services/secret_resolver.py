"""Agent-secrets P2 marker resolver (design §1).

Substitutes :data:`~daemon.services.secret_service.ENS_SECRET_MARKER_PREFIX`-
prefixed markers in env / header / tool-arg dicts to plaintext, in-RAM
ONLY, immediately before the consumer subprocess / tool invocation.

Architecture invariants (mirror the KMS resolver shape at
``daemon/services/kms_resolver.py``):

* Stored configs / args ALWAYS retain markers. Plaintext reaches
  ONLY the consumer's env / args dict at spawn / pre-invoke time.
  Any code path that round-trips the stored form MUST read the
  row fresh from the DB on the next resolve — re-cached plaintext
  rewrites are a regression of the R1 invariant.
* The resolver is invoked at consume time ONLY — never at config-
  load, never at API list-time. The P1 ``read_public`` /
  ``list`` paths stay presentation-only (pubkey-only rule,
  design §5.2) and remain the explicit boundary between
  presentation and resolution.

LANE-2 mirror (design §3.1) — fresh PG read per resolution inside
ONE ``engine.begin()`` transaction (SELECT → decrypt → ``use_marker``
audit row, ``no RAM cache`` at any layer). Markers minted in v0.21.0
MUST keep resolving in P2 — the marker format is FROZEN (design §3.1;
re-mint is not an option).

Boundary:

* The resolver returns NEW dicts (``resolve_secret_markers``)
  or the resolved string (``resolve_marker``). Callers MUST
  pass the consume-time-only dict / value; storing the
  resolver's return value is a regression of the R1 invariant.

* Resolution is the SHARED seam (design §1) — bash subprocess
  env overlay (``daemon/tools/bash.py``), proc subprocess env
  (``daemon/tools/proc_tools.py``), MCP warm-pool + cold-spawns
  (``daemon/mcp/warmup_pool.py`` + ``daemon/mcp/connection_manager.py``),
  and tool-param pre-invoke (``daemon/services/long_tool_nudge.py``).
  Each seam passes an explicit ``surface`` so the raised
  :class:`~daemon.services.secret_service.SecretMarkerResolutionError`
  carries the JSON envelope's ``surface`` field directly.

Do NOT modify this file's import of KMS-Lane regexes — the
sibling module (``kms_resolver.py``) is on the do-not-touch
list (design §5.4 binding constraint).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from daemon.services.secret_service import (
    SECRET_CODE_CIPHER_UNAVAILABLE,
    SECRET_CODE_MARKER_MALFORMED,
    SECRET_CODE_STORE_UNAVAILABLE,
    SECRET_ENVELOPE_SURFACES,
    ENS_SECRET_MARKER_PREFIX,
    ENS_SECRET_MARKER_RE,
    SecretCipherUnavailable,
    SecretMarkerResolutionError,
    SecretService,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Marker regex (consumer-rebuilt; FROZEN)
# ─────────────────────────────────────────────────────────────────────
#
# Mirrors the ``kms_resolver.py:65-79`` convention: the marker
# format is declared as a STRING CONSTANT in
# ``daemon/services/secret_service.py`` (the canonical source
# of truth — both mint and resolve sides import it), and the
# consumer side REBUILDS the compiled regex locally. NEVER
# ``re.compile(MARKER_RE).match()`` (``.fullmatch()`` only —
# partial matches would mangle legitimate plaintext like
# ``LOG_LEVEL=__ENS_SECRET__whatever__hint``). The KMS
# resolver follows the same discipline; this module inherits it.

_MARKER_PATTERN: re.Pattern[str] = re.compile(ENS_SECRET_MARKER_RE)


# ─────────────────────────────────────────────────────────────────────
# Surface ↔ audit-context mapping
# ─────────────────────────────────────────────────────────────────────
#
# The envelope ``surface`` (the per-seam JSON envelope tag) and the
# audit ``context`` (the ``secrets_audit.context`` column) are
# SEMANTICALLY the same seam but visually differ for bash:
#
#     envelope surface:  "bash.env"   (with a DOT)
#     audit context:     "bash_env"   (underscore form)
#
# The other two surfaces (``mcp_spawn`` / ``tool_param``) are
# byte-identical between the two forms. The mapping is fixed and
# lives at this one site so future seams can extend it without
# re-deriving the rule.

_SURFACE_TO_CONTEXT: dict[str, str] = {
    "bash.env": "bash_env",
    "mcp_spawn": "mcp_spawn",
    "tool_param": "tool_param",
}


def _surface_to_context(surface: str) -> str:
    """Map envelope ``surface`` → audit ``context``.

    Raises:
        SecretValidationError: ``surface`` not in the envelope
            surface set (defensive — the public ``resolve_marker``
            / ``resolve_secret_markers`` entry already validates
            against :data:`SECRET_ENVELOPE_SURFACES`, but a
            future internal caller that bypasses the boundary
            gets a clear error rather than a silent mistype).
    """
    # Import here to avoid an import-time cycle if the service
    # ever needs to call back into the resolver (today it does
    # not — the resolver imports the service, never the reverse).
    from daemon.services.secret_service import SecretValidationError

    try:
        return _SURFACE_TO_CONTEXT[surface]
    except KeyError as exc:
        # Defense-in-depth: the public functions reject unknown
        # surfaces at the boundary. This branch is reachable
        # only if an internal caller bypasses the public entry.
        raise SecretValidationError(
            f"surface {surface!r} has no audit-context mapping; "
            f"expected one of {sorted(_SURFACE_TO_CONTEXT)}"
        ) from exc


# ─────────────────────────────────────────────────────────────────────
# Resolver entry — single-value form (tool-param pre-invoke)
# ─────────────────────────────────────────────────────────────────────


def resolve_marker(
    value: str,
    *,
    secret_service: Optional[SecretService] = None,
    actor: str = "<secret_resolver>",
    surface: str = "tool_param",
) -> str:
    """Resolve a single marker string to plaintext.

    Mirrors :func:`kms_resolver._resolve_value` (the
    KMS-Lite single-value form at ``kms_resolver.py:101``)
    but for the agent-secrets marker scheme. The function
    is the tool-param pre-invoke seam (design §1d) — a
    tool's declared ``secret_typed_args`` field is resolved
    via this entry before ``bare.ainvoke`` runs.

    Order of attempts:

      1. **Marker** — fullmatch ``_MARKER_PATTERN``. Resolve
         via ``secret_service.resolve_marker``. Failures map
         to typed envelopes (the service raises
         :class:`SecretMarkerResolutionError`; this entry
         propagates verbatim — the seam formats the JSON).
      2. **Non-marker with prefix** (looks like a marker but
         fails fullmatch — e.g. malformed UUID4) → typed
         ``secret_marker_malformed`` envelope. Detected at
         THIS layer, pre-DB (design §3.2). No audit row
         (we never reached ``engine.begin()``).
      3. **Plaintext** — returned unchanged (back-compat for
         ``api_token``, ``LOG_LEVEL``, etc.). Byte-identical
         passthrough so the resolver is a NO-OP for values
         the seams didn't declare as ``secret_typed_args``.

    Args:
        value: The string to resolve. Empty string is a
            passthrough (no marker, no error).
        secret_service: The SecretService to resolve against.
            REQUIRED — there is no module-level default (zero
            import-time side effects on paths without markers;
            design §0 invariant). When ``None``, this entry
            raises :class:`SecretMarkerResolutionError` with
            ``code=secret_resolver_store_unavailable`` (the
            fail-closed posture; ``secret_service=None``
            signals an un-wired caller, which is a
            configuration error — never silent passthrough).
        actor: Agent id (Cardinal #2 — the calling agent).
            Defaults to ``"<secret_resolver>"`` as a sentinel.
            Agent-side seams (bash §1a / proc §1b / pre-invoke §1d)
            pass the real agent id; the MCP spawn sites (§1c) are
            deliberately SERVER-SCOPED — no agent context exists at
            spawn time, so they pass the sentinel explicitly and the
            resulting audit rows are attributable to the server
            lifecycle, not an agent.
        surface: Envelope ``surface`` tag — one of
            :data:`SECRET_ENVELOPE_SURFACES`. Defaults to
            ``"tool_param"`` (the canonical surface for the
            single-value form; design §1d). Carried on the
            raised :class:`SecretMarkerResolutionError` so
            the seam can format the JSON envelope without
            re-deriving it.

    Returns:
        The plaintext for marker values; byte-identical
        passthrough for non-marker values.

    Raises:
        SecretMarkerResolutionError: typed envelope for the
            marker / resolution failure modes. NEVER carries
            the value — PB-F1 family.
    """
    # Validate ``surface`` at the public boundary so a
    # mistyped seam call fails loud (defensive — the
    # exception class re-validates at construction).
    if surface not in SECRET_ENVELOPE_SURFACES:
        from daemon.services.secret_service import SecretValidationError

        raise SecretValidationError(
            f"surface {surface!r} not in SECRET_ENVELOPE_SURFACES "
            f"({sorted(SECRET_ENVELOPE_SURFACES)})"
        )

    # Plaintext passthrough — the resolver is a NO-OP for
    # values the seams didn't declare as ``secret_typed_args``
    # (design §0: zero import-time side effects on paths
    # without markers — plaintext does NOT require a
    # SecretService). Two sub-cases collapse to passthrough:
    # empty string + values that don't start with the marker
    # prefix. Both return byte-identical.
    if not value or not value.startswith(ENS_SECRET_MARKER_PREFIX):
        return value

    # Value is marker-shaped (starts with the prefix).
    # Two sub-cases: fullmatch (resolve via service) or
    # malformed (raise at this layer, pre-DB). Either way,
    # a SecretService is required.
    if secret_service is None:
        # No module-level default — explicit injection only
        # (zero import-time side effects on paths without
        # markers; design §0). Surface the failure as the
        # store-unavailable envelope: an un-wired caller
        # is the same trust class as a DB outage for the
        # seams (no resolution possible → no spawn).
        raise SecretMarkerResolutionError(
            secret_id="<unconfigured>",
            code=SECRET_CODE_STORE_UNAVAILABLE,
            surface=surface,
        )

    # Marker test — ``fullmatch`` discipline (matches only
    # well-formed ``__ENS_SECRET__<uuid4>__`` values).
    m = _MARKER_PATTERN.fullmatch(value)
    if m is not None:
        secret_id = m.group(1)
        # Service does the SELECT → unwrap → audit inside
        # ONE ``engine.begin()`` (design §1). Failures map
        # to typed envelopes; this entry propagates verbatim
        # — the seam formats the JSON envelope around it.
        # ``SecretCipherUnavailable`` is the ONE raw
        # exception the resolver wrapper translates: it is
        # raised by the service BEFORE any DB work (the
        # cipher check fires first) and carries no envelope
        # code, so the resolver is the seam where
        # ``secret_cipher_unavailable`` is minted
        # (mirrors the design §3.2 mapping).
        try:
            return secret_service.resolve_marker(
                actor=actor,
                secret_id=secret_id,
                context=_surface_to_context(surface),
                surface=surface,
            )
        except SecretCipherUnavailable as exc:
            raise SecretMarkerResolutionError(
                secret_id=secret_id,
                code=SECRET_CODE_CIPHER_UNAVAILABLE,
                surface=surface,
            ) from exc

    # Marker-shaped but fullmatch failed → malformed.
    # Carry the raw string in ``secret_id`` (PB-F1: the
    # raw string is NOT the value — the value is the
    # plaintext the malformed marker would have pointed
    # to, which does not exist. The raw string carries
    # no secret material; it's the operator-side shape
    # that fails the regex. Operator diagnostics, never
    # an agent-visible value).
    raise SecretMarkerResolutionError(
        secret_id=value,
        code=SECRET_CODE_MARKER_MALFORMED,
        surface=surface,
    )


# ─────────────────────────────────────────────────────────────────────
# Resolver entry — dict form (env / header overlay)
# ─────────────────────────────────────────────────────────────────────


def resolve_secret_markers(
    env: Optional[dict[str, str]],
    *,
    secret_service: Optional[SecretService] = None,
    actor: str = "<secret_resolver>",
    surface: str = "bash.env",
) -> Optional[dict[str, str]]:
    """Resolve markers in an env / header dict. Returns a NEW dict.

    Mirrors :func:`kms_resolver.resolve_env` (the KMS-Lite
    dict form at ``kms_resolver.py:149``) but for the
    agent-secrets marker scheme. The function is the
    spawn-time seam (design §1a + §1b + §1c) — bash /
    proc subprocess env overlays AND MCP stdio env.
    Each spawn-time call invokes this entry exactly
    ONCE per env dict; the returned dict is in-RAM ONLY
    and is consumed by the child-process spawn or the
    stdio-config rebuild.

    Fail-closed semantics: if ANY value raises, this
    entry propagates the typed envelope verbatim — the
    caller sees the partial state ONLY in the sense that
    the env dict was never returned (the with-block in the
    iterator is abandoned at the first failure, the
    caller never receives an incomplete dict). The dict
    is never partial; the spawn is aborted by the seam.

    Args:
        env: The stored env dict (``McpStdioConfig.env``,
            bash ``tool_call["env"]`` overlay,
            ``proc.process_env_overlay``, etc.). NEVER
            mutated. ``None`` passes through as ``None``.
        secret_service: The SecretService to resolve
            against. REQUIRED (same fail-closed posture as
            :func:`resolve_marker`).
        actor: Agent id (Cardinal #2). Defaults to the
            sentinel ``"<secret_resolver>"``; Phase 3 seams
            MUST pass the real agent id.
        surface: Envelope ``surface`` tag. Defaults to
            ``"bash.env"`` (the canonical surface for the
            dict form; design §1a). The MCP seams override
            to ``"mcp_spawn"``.

    Returns:
        A new dict suitable for passing to the spawn seam
        (``StdioServerParameters(env=...)``,
        ``asyncio.create_subprocess_exec(env=...)``, etc.).
        ``None`` passes through as ``None``.

    Raises:
        SecretMarkerResolutionError: typed envelope for
            any value that fails resolution. Propagates
            verbatim — the seam catches and formats the
            JSON envelope. NEVER partial env.
    """
    # Validate ``surface`` at the public boundary.
    if surface not in SECRET_ENVELOPE_SURFACES:
        from daemon.services.secret_service import SecretValidationError

        raise SecretValidationError(
            f"surface {surface!r} not in SECRET_ENVELOPE_SURFACES "
            f"({sorted(SECRET_ENVELOPE_SURFACES)})"
        )

    # ``None`` passthrough — mirrors ``kms_resolver.resolve_env``
    # (the env-less case for callers that don't carry an overlay).
    if env is None:
        return None

    # Resolve value-by-value into a NEW dict (R1 invariant;
    # mirrors the kms_resolver.py:169-178 pattern). Iteration
    # is short-circuit: the first value that raises propagates
    # and the caller never receives a partial dict. The
    # ``secret_service`` injection check is LAZY — it fires
    # only when a value needs resolution (markers), so a
    # plaintext-only dict passes through even without a
    # service (design §0 invariant: zero side effects on
    # paths without markers).
    out: dict[str, str] = {}
    for key, value in env.items():
        if isinstance(value, str):
            # The single-value entry handles the marker /
            # plaintext distinction + the typed-envelope
            # translation for the marker path.
            out[key] = resolve_marker(
                value,
                secret_service=secret_service,
                actor=actor,
                surface=surface,
            )
        else:
            # Defensive: stored values are always strings
            # (the SecretService store() validates via
            # SecretValidationError). If a legacy / foreign
            # row contains a non-str (e.g. ``None``), pass it
            # through untouched rather than coercing —
            # mirrors ``kms_resolver.py:174-177``.
            out[key] = value  # type: ignore[assignment]
    return out


# ─────────────────────────────────────────────────────────────────────
# Predicate helpers
# ─────────────────────────────────────────────────────────────────────


def is_secret_marker(value: str) -> bool:
    """Return ``True`` iff ``value`` is a full secret marker.

    Used by callers that want to distinguish marker values from
    plaintext at presentation time without performing the
    lookup. The KMS resolver has the same shape
    (``kms_resolver.is_marker`` at :117-200) — this entry is
    the agent-secrets analogue.

    Note: a value that STARTS WITH the marker prefix but fails
    ``fullmatch`` (e.g. malformed UUID4) is NOT a secret
    marker — it is a malformed marker (caught by
    :func:`resolve_marker` with ``secret_marker_malformed``).
    Callers that need to surface malformed markers should
    branch on ``value.startswith(ENS_SECRET_MARKER_PREFIX)``
    explicitly.
    """
    return _MARKER_PATTERN.fullmatch(value) is not None


__all__ = [
    "resolve_marker",
    "resolve_secret_markers",
    "is_secret_marker",
]