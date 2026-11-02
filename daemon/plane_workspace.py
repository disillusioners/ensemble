"""Plane workspace resolution — the single env-scoped decision point.

User directive (Discord 2026-11-02): the Plane workspace ``nea`` is
**production/live ONLY**; dev + demo + sandbox MUST target the ``e2e``
test workspace for **ALL** Plane operations (REST *and* MCP). This module
is the ONE place that decides which environment the daemon is and which
workspace it is therefore allowed to touch.

Design contract (lane ``plane-pm-issue-tools``, Phase 2):

* **D-1 — lane-local live determination.** The shared env oracle
  (``daemon/tools/upgrade_tools.py::_auto_derive_env``) is fail-OPEN in the
  guard's dangerous direction: it reads the **sibling** live install's
  ``.env`` and answers ``"live"`` for a dev checkout. This module therefore
  implements its OWN predicate and NEVER imports ``upgrade_tools``.
* **P2-A — hardened edges.** An explicit opt-out marker
  (``ENSEMBLE_SELF_ENV`` in ``{0,false,no,off}``) short-circuits NON-LIVE
  **before** the own-DB arm; a garbage marker (non-empty, not in
  ``VALID_ENVS``) is non-live/unclassified **and** refused (the refusal is
  what makes the misconfiguration OBSERVABLE — the boot WARNING is the only
  emitter of a typed reason); an empty marker is ABSENT; the ``VALID_ENVS``
  match is exact and NON-normalizing (whitespace stripped, case NOT folded).
* **P2-B — canonical DB-name input.** ``POSTGRES_DB`` first, then the
  daemon's **own** ``ensemble.json`` (``postgres.db``) — the same precedence
  the daemon's own repository factory uses. Every OTHER filesystem read is
  banned, and so is importing ``daemon.tools.upgrade_tools``.
* **P2-C — one canonical slug normalization** (trim + strip ONE outer
  matching quote pair), consumed by BOTH lanes (REST + MCP) so one env value
  can never target two different workspaces.
* **P2-M — injectable signature + resolve-once threading.** The resolver is
  pure and injectable (``env=`` / ``postgres_db=``) so the gate-(iv) matrix
  can drive it without touching the process env. The daemon seams use
  :func:`current_plane_workspace`, which memoizes on an env fingerprint —
  the env TOCTOU between ``is_available()`` and ``get_base_config()`` is
  closed by resolving ONCE and reusing that resolution.

**R-1 (log ownership): the resolver NEVER logs.** It returns typed reasons
and nothing else. The manager's skip WARNING is the only reason
interpolator (:mod:`daemon.manager`); ``PlaneServerDefinition.is_available``
owns the one-shot envelope notice.

No schema change, no config change, no privileged-category change.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "PlaneWorkspaceResolution",
    "VALID_ENVS",
    "PRODUCTION_WORKSPACE_SLUG",
    "TEST_WORKSPACE_SLUG",
    "PRODUCTION_DB_NAME",
    "REASON_UNSET_SLUG",
    "REASON_PRODUCTION_ONLY",
    "REASON_OTHER_SLUG",
    "REASON_UNCLASSIFIED",
    "REASON_GARBAGE_MARKER",
    "normalize_workspace_slug",
    "resolve_plane_workspace",
    "current_plane_workspace",
    "reset_resolution_cache",
    "_header_value",
]


# ── Vocabulary ───────────────────────────────────────────────────────────────

#: The recognized ``ENSEMBLE_SELF_ENV`` values. Exact, NON-normalizing match
#: (whitespace stripped, case NOT folded) — mirrors the house
#: ``daemon.tools.upgrade_tools.VALID_ENVS`` without importing it.
VALID_ENVS: tuple[str, ...] = ("dev", "demo", "live", "sandbox")

#: Explicit "do not auto-detect" attestations (case-insensitive, trimmed).
_OPT_OUT_FALSES: frozenset[str] = frozenset({"0", "false", "no", "off"})

#: The production workspace — live environments only.
PRODUCTION_WORKSPACE_SLUG: str = "nea"

#: The test workspace — the ONLY workspace a non-live environment may use.
TEST_WORKSPACE_SLUG: str = "e2e"

#: The daemon's own production database name (the own-DB arm's anchor).
PRODUCTION_DB_NAME: str = "ensemble_prod"

#: Own-DB name → environment label (the reverse of the house ``_ENV_DB``).
_DB_NAME_TO_ENV_LABEL: dict[str, str] = {
    "ensemble_prod": "live",
    "ensemble_dev": "dev",
    "ensemble_demo": "demo",
    "ensemble_sandbox": "sandbox",
}


# ── Typed refusal reasons ───────────────────────────────────────────────────
#
# Every reason is a TYPED string (house precedent:
# ``daemon/services/plane_sync_service.py::unavailable_reason``), never a log
# phrase. The manager's skip WARNING interpolates them VERBATIM; no other
# emitter may re-render them.
#
# Two shapes exist: a PLAIN string (interpolated as-is) and a
# ``.format()`` TEMPLATE (``REASON_PRODUCTION_ONLY`` carries ``{label}``,
# ``REASON_OTHER_SLUG`` carries ``{slug}``). The resolver fills the
# template ONCE, at the point of refusal; every DOWNSTREAM emitter — the
# manager's skip WARNING included — receives the ALREADY-FORMATTED string
# and interpolates that VERBATIM. No emitter re-renders a template.
#
# C4 (reviewer-adjudicated tidier must-fix, 2026-11-02): template
# fills ESCAPE operator-controlled values (the slug) via
# :func:`_escape_for_reason` — repr-style control-character rendering —
# so a newline/control character in ``PLANE_MCP_WORKSPACE_SLUG`` can
# never forge a second agent-visible log line. Clean values render
# byte-identically; VALUE case-sensitivity is UNCHANGED (only the
# rendering escapes, never the value compared).

REASON_UNSET_SLUG: str = (
    "PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; "
    "expected 'e2e'"
)

REASON_PRODUCTION_ONLY: str = (
    "workspace 'nea' is production-only; this environment resolves to {label}"
)

REASON_OTHER_SLUG: str = (
    "workspace '{slug}' is not permitted in a non-production environment; "
    "expected 'e2e'"
)

REASON_UNCLASSIFIED: str = (
    "environment could not be classified as production; refusing to target "
    "the 'nea' workspace"
)

REASON_GARBAGE_MARKER: str = (
    "environment marker '{marker}' is not a recognized environment "
    "(expected one of dev, demo, live, sandbox); refusing to classify as "
    "production"
)


def _escape_for_reason(value: str) -> str:
    """Render a value repr-style MINUS the outer quotes (C4).

    ``repr()`` renders control characters as visible escape sequences
    (``\\n``, ``\\r``, ``\\t`` …) — the house F3-notice precedent
    interpolates the persisted slug via ``%r`` for exactly this
    reason. Stripping the OUTER quote pair keeps CLEAN values
    byte-identical inside the templates' own quoting (``'{slug}'``),
    so live/dev messages on legitimate slugs are unchanged; only
    pathological values (newline/control characters) render visibly
    escaped — a forged second log line becomes impossible.
    """
    rendered = repr(value)
    return rendered[1:-1] if len(rendered) >= 2 else rendered

# ── The resolution ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlaneWorkspaceResolution:
    """The outcome of one workspace resolution.

    Attributes:
        env_label: ``'live'`` / ``'dev'`` / ``'demo'`` / ``'sandbox'``, or
            ``None`` when the environment could not be classified.
        env_source: ``'explicit-marker'`` (the operator's attestation),
            ``'own-postgres-db'`` (the daemon's own DB name), or
            ``'unclassified'``.
        slug: the EFFECTIVE workspace slug, or ``None`` when the resolution
            is refused (or, on live, when the slug is unset — the
            account-default workspace).
        allowed: ``True`` when the resolution permits the Plane lanes to
            run.
        reason: the typed refusal reason, or ``None`` when allowed.
    """

    env_label: str | None
    env_source: str
    slug: str | None
    allowed: bool
    reason: str | None


# ── Canonical slug normalization (P2-C) ──────────────────────────────────────


def _header_value(headers: Any, name: str) -> Any:
    """Case-insensitive header NAME lookup — VALUES untouched.

    Commit A (reviewer-adjudicated tidier must-fix, 2026-11-02): the
    four ``x-workspace-slug`` read sites each used an exact-case
    ``dict.get`` / membership test, so a config whose header NAME case
    varies (``X-Workspace-Slug`` — legal HTTP; header names are
    case-insensitive by RFC 9110 §5.1) silently escaped the probe
    guard, the F3 stale-row notice, and the P2-I heal drift
    computation. ONE helper, adopted at all four sites, closes the
    class.

    Semantics:

    * ``name`` is matched case-insensitively against the dict's STRING
      keys (non-string keys are skipped);
    * the VALUE is returned UNTOUCHED — no case-folding, no trimming,
      no normalization (value case-sensitivity is a separate, unchanged
      contract);
    * ``None`` is returned when ``headers`` is not a dict or no
      string key matches — callers treat ``None`` as "the header makes
      no authority claim".

    Args:
        headers: the config ``headers`` mapping (``None`` / non-dict ⇒
            ``None``).
        name: the header name to look up (matched lowercased).

    Returns:
        The header VALUE, or ``None`` when absent.
    """
    if not isinstance(headers, dict):
        return None
    lowered = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == lowered:
            return value
    return None


def normalize_workspace_slug(raw: str | None) -> str:
    """Canonical workspace-slug normalization — ONE implementation, BOTH lanes.

    Trim surrounding whitespace, then strip exactly ONE outer matching
    quote pair (``"..."`` / ``'...'``) and trim again.

    Why one implementation: the REST lane (``plane_http_client._env``) and the
    MCP lane (``daemon.mcp.config._strip_wrapping_quotes``) used to
    normalize differently, so a single env value could target TWO different
    workspaces (the split-brain P2-C closes). Interior quotes are preserved
    (API tokens legitimately contain them).

    Args:
        raw: The raw env value (``None`` → empty string).

    Returns:
        The normalized slug (``""`` when unset).
    """
    if raw is None:
        return ""
    value = str(raw).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


# ── The canonical DB-name input (P2-B) ──────────────────────────────────────


def _own_config_db_name(env_map: Mapping[str, str]) -> str | None:
    """Read ``postgres.db`` from the daemon's OWN ``ensemble.json``.

    The ONLY filesystem read this module is permitted to perform. The data
    directory follows the daemon's own precedence
    (``ENSEMBLE_DATA_DIR`` > ``DATA_DIR`` > ``./data`` — the chain
    ``daemon/api.py`` uses to locate ``ensemble.json``).

    Returns ``None`` when the file is missing/unreadable/malformed — a
    corrupt config must never crash the resolver.
    """
    data_dir = (
        env_map.get("ENSEMBLE_DATA_DIR")
        or env_map.get("DATA_DIR")
        or "./data"
    )
    path = Path(data_dir) / "ensemble.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a missing/corrupt config is not fatal
        return None
    if not isinstance(data, dict):
        return None
    pg = data.get("postgres")
    if not isinstance(pg, dict):
        return None
    db = pg.get("db")
    if isinstance(db, str) and db.strip():
        return db.strip()
    return None


def _canonical_db_name(env_map: Mapping[str, str]) -> str | None:
    """The daemon's own database name — ``POSTGRES_DB`` first, then config.

    The env var wins (the daemon's own precedence — see
    ``daemon/repositories/factory.py``); the own ``ensemble.json`` is the
    fallback so an env-var-less production install still resolves LIVE
    (a live-continuity break otherwise).

    **Zero-file-access contract:** when ``POSTGRES_DB`` is supplied, NO file
    is opened.
    """
    raw = env_map.get("POSTGRES_DB")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _own_config_db_name(env_map)


# ── The resolver (D-1 + P2-A) ────────────────────────────────────────────────


def resolve_plane_workspace(
    env: Mapping[str, str] | None = None,
    *,
    postgres_db: str | None = None,
) -> PlaneWorkspaceResolution:
    """Resolve the environment + the workspace this daemon may target.

    The predicate (D-1, hardened by P2-A):

    1. ``marker := ENSEMBLE_SELF_ENV``
       * opt-out (``0``/``false``/``no``/``off``, case-insensitive) ⇒
         NON-LIVE — short-circuits the own-DB arm;
       * non-empty and NOT in ``VALID_ENVS`` ⇒ NON-LIVE / unclassified and
         **refused** (the misconfiguration must be observable);
       * empty ⇒ ABSENT.
    2. ``marker == "live"`` ⇒ LIVE.
    3. ``marker`` in ``dev``/``demo``/``sandbox`` ⇒ NON-LIVE.
    4. marker ABSENT ⇒ the own-DB arm: ``ensemble_prod`` ⇒ LIVE; another
       known own-DB name ⇒ NON-LIVE with that label; unknown/absent ⇒
       NON-LIVE / unclassified (fail-closed).

    The policy (ADJ-1, live-exempt):

    * **LIVE** ⇒ ANY configured slug is permitted; an unset slug means the
      account-default workspace (byte-identical to today).
    * **NON-LIVE** ⇒ ``e2e`` only; unset / ``nea`` / anything else is
      refused with a typed reason.

    Args:
        env: The environment mapping to read (defaults to ``os.environ``).
            Injected so the gate-(iv) matrix can drive the resolver without
            touching the process env.
        postgres_db: Explicit own-DB name (overrides the env + config
            lookup). Injected for the same reason.

    Returns:
        The :class:`PlaneWorkspaceResolution`. Never raises.
    """
    env_map: Mapping[str, str] = os.environ if env is None else env

    # ── 1. the marker ────────────────────────────────────────────────────
    marker_raw = env_map.get("ENSEMBLE_SELF_ENV")
    marker = marker_raw.strip() if isinstance(marker_raw, str) else None
    if marker == "":
        # Empty marker = ABSENT (house ``_read_marker_value`` semantics).
        marker = None
    is_opt_out = marker is not None and marker.lower() in _OPT_OUT_FALSES
    is_garbage = (
        marker is not None
        and not is_opt_out
        and marker not in VALID_ENVS
    )

    # ── 2. the slug + the canonical DB name ──────────────────────────────
    slug = normalize_workspace_slug(env_map.get("PLANE_MCP_WORKSPACE_SLUG"))
    db_name = (
        postgres_db if postgres_db is not None else _canonical_db_name(env_map)
    )

    # ── 3. the classification ────────────────────────────────────────────
    env_label: str | None
    env_source: str
    classification_reason: str | None = None

    if is_opt_out:
        # Explicit "no auto-detection" attestation — non-live, and the
        # own-DB arm is NEVER consulted.
        env_label, env_source = None, "unclassified"
    elif is_garbage:
        env_label, env_source = None, "unclassified"
        classification_reason = REASON_GARBAGE_MARKER.format(marker=marker)
    elif marker == "live":
        env_label, env_source = "live", "explicit-marker"
    elif marker in ("dev", "demo", "sandbox"):
        env_label, env_source = marker, "explicit-marker"
    elif db_name == PRODUCTION_DB_NAME:
        # The own-DB arm — the daemon's own main DB IS production.
        env_label, env_source = "live", "own-postgres-db"
    elif db_name in _DB_NAME_TO_ENV_LABEL:
        env_label, env_source = _DB_NAME_TO_ENV_LABEL[db_name], "own-postgres-db"
    else:
        env_label, env_source = None, "unclassified"

    # ── 4. the policy ────────────────────────────────────────────────────
    if env_label == "live":
        # ADJ-1 live-exempt: ANY configured slug is permitted; an unset slug
        # keeps today's account-default behavior (byte-identical).
        return PlaneWorkspaceResolution(
            env_label=env_label,
            env_source=env_source,
            slug=slug or None,
            allowed=True,
            reason=None,
        )

    if classification_reason is not None:
        # A garbage marker ⇒ REFUSED. Fail-closed AND observable: the boot
        # WARNING (the only typed-reason interpolator) fires because the
        # server is skipped.
        return PlaneWorkspaceResolution(
            env_label=env_label,
            env_source=env_source,
            slug=None,
            allowed=False,
            reason=classification_reason,
        )

    if slug == TEST_WORKSPACE_SLUG:
        return PlaneWorkspaceResolution(
            env_label=env_label,
            env_source=env_source,
            slug=TEST_WORKSPACE_SLUG,
            allowed=True,
            reason=None,
        )

    if not slug:
        reason = REASON_UNSET_SLUG
    elif slug == PRODUCTION_WORKSPACE_SLUG:
        reason = (
            REASON_UNCLASSIFIED
            if env_label is None
            else REASON_PRODUCTION_ONLY.format(label=env_label)
        )
    else:
        # C4: the operator-controlled slug is ESCAPED repr-style — a
        # newline/control character in the value can never forge a
        # second agent-visible log line. Value case-sensitivity is
        # unchanged (the comparison above is exact); only the RENDERED
        # form escapes.
        reason = REASON_OTHER_SLUG.format(slug=_escape_for_reason(slug))

    return PlaneWorkspaceResolution(
        env_label=env_label,
        env_source=env_source,
        slug=None,
        allowed=False,
        reason=reason,
    )


# ── Resolve-once threading (P2-M) ───────────────────────────────────────────


def _env_fingerprint(env_map: Mapping[str, str]) -> tuple[str, str, str]:
    """The three env inputs the resolution depends on.

    Known limitation (F6, reviewer fold 2026-11-02 — documented, deliberately
    NOT widened): the fingerprint omits ``ENSEMBLE_DATA_DIR`` / ``DATA_DIR`` —
    the inputs that locate the daemon's own ``ensemble.json`` (the P2-B
    fallback DB-name source consulted only when ``POSTGRES_DB`` is absent).
    On an env-var-less install, a change to the own ``ensemble.json`` (or to
    those data-dir vars) mid-process therefore does NOT invalidate the memo:
    :func:`current_plane_workspace` can keep serving a stale resolution until
    a fingerprinted input changes or :func:`reset_resolution_cache` runs.
    Accepted because it is non-exploitable in practice — the daemon does not
    rewrite its own ``ensemble.json`` mid-run, and every seam in one
    operation shares the SAME resolution object (no split-brain within an
    operation, by construction). Keeping the data-dir inputs out also keeps
    the memo path free of filesystem access.
    """
    return (
        str(env_map.get("ENSEMBLE_SELF_ENV") or ""),
        str(env_map.get("PLANE_MCP_WORKSPACE_SLUG") or ""),
        str(env_map.get("POSTGRES_DB") or ""),
    )


_cached_fingerprint: tuple[str, str, str] | None = None
_cached_resolution: PlaneWorkspaceResolution | None = None


def current_plane_workspace() -> PlaneWorkspaceResolution:
    """Resolve the CURRENT process environment, memoized on an env fingerprint.

    The daemon seams (``PlaneServerDefinition.is_available`` /
    ``get_base_config``, the REST client, the sync service, the manager's
    skip site) all call this instead of re-reading the env at every seam —
    the env TOCTOU between ``is_available()`` and ``get_base_config()`` is
    closed by resolving ONCE and reusing that resolution.

    The memo is keyed on the resolution's own inputs, so a changed env
    re-resolves immediately; an unchanged env reuses the same resolution
    object (so every seam in one operation agrees, by construction).

    Tests that mutate the env between calls get a fresh resolution (the
    fingerprint changes). :func:`reset_resolution_cache` exists for tests
    that want an unconditional re-resolve.
    """
    global _cached_fingerprint, _cached_resolution
    fingerprint = _env_fingerprint(os.environ)
    if (
        _cached_resolution is not None
        and _cached_fingerprint == fingerprint
    ):
        return _cached_resolution
    resolution = resolve_plane_workspace(os.environ)
    _cached_fingerprint = fingerprint
    _cached_resolution = resolution
    return resolution


def reset_resolution_cache() -> None:
    """Drop the memoized resolution (test seam)."""
    global _cached_fingerprint, _cached_resolution
    _cached_fingerprint = None
    _cached_resolution = None
