"""Async HTTP client for the Plane (plane.so) REST API.

Used by the structural sync subsystem to mirror Ensemble projects to
Plane as plain projects (no issues/cycles at v1). The client follows the
existing pattern of constructing a fresh ``httpx.AsyncClient`` per call
(no module-level singleton) to sidestep the event-loop binding hazard.

Endpoint shape::

    {PLANE_BASE_URL}/api/v1/workspaces/{PLANE_MCP_WORKSPACE_SLUG}/projects/

Headers::

    X-Api-Key: {PLANE_API_KEY}
    x-workspace-slug: {PLANE_MCP_WORKSPACE_SLUG}
    Content-Type: application/json

**Authentication scheme.** Plane REST uses the ``X-Api-Key`` header
(``Authorization: Bearer`` is rejected with HTTP 401). ``PLANE_API_KEY``
and ``PLANE_MCP_API_KEY`` are kept as SEPARATE env vars by design — not
because the same key cannot serve both deployments (a live probe on
2026-09-20 confirmed ``PLANE_MCP_API_KEY`` returns HTTP 200 on the REST
endpoint), but to follow separation-of-concerns: a dedicated REST token
gives independent rotation, scoped blast radius (revoking REST does not
disable MCP and vice versa), and a clean audit trail in Plane's API
Tokens UI. We therefore require a dedicated ``PLANE_API_KEY``
(workspace-level REST token minted from Plane's Settings → API Tokens
UI) and **never** silently fall back to ``PLANE_MCP_API_KEY`` — a
silent fallback would mask the exact misconfiguration class Phase 2
fixed (the dead-on-REST key, observed in prod pre-Phase-2).

Feature gating
--------------
The client is feature-gated on ``PLANE_BASE_URL`` + ``PLANE_API_KEY``:
when either env var is unset (or empty), :meth:`is_available` returns
``False`` and :meth:`create` returns ``None``. This lets callers no-op
gracefully without sprinkling ``if`` checks throughout the codebase.

Circuit breaker
---------------
A module-level :class:`CircuitBreaker` (failure_threshold=5,
recovery_timeout=60s) guards every method. The breaker is shared across
all clients so persistent Plane outages trip the circuit for the whole
daemon, not just the first caller.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from daemon.sources.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


# ── Custom errors ──────────────────────────────────────────────────────────


class PlaneAPIError(Exception):
    """Generic Plane API failure (non-auth, non-404 4xx; or 5xx).

    Carries the HTTP ``status_code`` and ``body`` so callers can
    disambiguate response classes without parsing the human-readable
    message text (e.g. identifier-collision detection — see
    :class:`PlaneIdentifierCollisionError`).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class PlaneIdentifierCollisionError(PlaneAPIError):
    """409 — the requested ``identifier`` is already taken in Plane.

    Raised by :meth:`PlaneHttpClient.create_project` when Plane
    returns HTTP 409 (verified live 2026-09-20: second POST of a
    project with a taken ``identifier`` returns ``409 Conflict``).
    Callers drive a deterministic retry (suffix scheme via
    :func:`derive_plane_identifier` with ``attempt > 1``) so the
    same ensemble project always converges to the same identifier,
    even when several legacy projects share a base name.
    """


class PlaneAuthError(PlaneAPIError):
    """401/403 — the configured API key is invalid or lacks permission.

    Raised by the client so callers (sync service) can short-circuit and
    record an ``error`` state without retrying on every call.
    """


class PlaneNotFoundError(PlaneAPIError):
    """404 — the requested Plane resource does not exist.

    Methods that semantically mean "fetch if exists" (``get_project``)
    catch this and return ``None`` instead of propagating.
    """


# ── Module-level circuit breaker ────────────────────────────────────────────
# Shared across all clients so a Plane outage trips once, not per-caller.
_plane_breaker: CircuitBreaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=60.0,
)


# ── Feature gating ──────────────────────────────────────────────────────────


def _env(name: str) -> str:
    """Read an env var, stripped of whitespace AND of one outer matching-quote pair.

    Phase-1 sanitizer semantics (mirror, don't import): strip exactly
    ONE outer layer of matching quotes (``"..."`` or ``'...'``); interior
    quotes are preserved. A leading-quote with no matching trailer
    leaves the value unchanged (it's not a wrapping pair). Whitespace
    is trimmed on the OUTSIDE only (matches the pre-existing contract).

    Why: the prod quote-leaking loader (documented in Phase-2 probes)
    emits values like ``"plane_api_xyz"`` — a slug or key with literal
    quote chars on either end yields 403 at the API. One layer of
    unwrapping neutralizes that class without touching interior
    quotes (which can legitimately appear in API tokens).
    """
    raw = (os.environ.get(name, "") or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1].strip()
    return raw


# ── Plane identifier derivation ─────────────────────────────────────────────
#
# Plane project ``identifier`` is REQUIRED (HTTP 400 "This field is required."
# when omitted — verified live 2026-09-20) and capped at 12 characters
# ("Ensure this field has no more than 12 characters." on a 30-char probe).
# Server-side: lowercase input is auto-uppercased (``ensprb3`` → ``ENSPRB3``);
# spaces are preserved. Existing workspace identifiers follow the canonical
# uppercase-alphanumeric pattern with no separators: ``NEA``, ``ENSEMBLE``,
# ``LLMPROXY``. We deliberately derive a strict subset (uppercase A–Z + 0–9
# only, no spaces, ≤ 12 chars) — saner than relying on server-side quirks.
#
# The function is PURE and DETERMINISTIC given its inputs: same ``name``
# + ``attempt`` → same identifier, across processes and restarts. Retries
# bump ``attempt`` and get a suffixed identifier that never collides with
# the first attempt (because the suffix shortens the base to fit).
_IDENTIFIER_MAX_LEN: int = 12
_IDENTIFIER_ALLOWED = re.compile(r"[^A-Z0-9]")
_IDENTIFIER_EMPTY_FALLBACK: str = "ENSEMBLE"


def derive_plane_identifier(name: str, *, attempt: int = 1) -> str:
    """Derive a Plane ``identifier`` from a human-readable name.

    Rules (verified against live plane.ensem.dev 2026-09-20):

    1. Sanitize to uppercase ``[A-Z0-9]`` only (strip spaces, hyphens,
       punctuation). Caller-supplied non-ASCII letters collapse to ``""``
       after the regex; we substitute :data:`_IDENTIFIER_EMPTY_FALLBACK`
       so the payload still has a non-empty ``identifier``.
    2. Truncate to :data:`_IDENTIFIER_MAX_LEN` (12) chars.
    3. On ``attempt > 1`` (collision retry): append the attempt number
       as a deterministic suffix (``ENSEMBLE`` → ``ENSEMBLE2``,
       ``ENSEMBLE3``, ...), truncating the base to fit within 12 chars.
       The suffix is **always** the decimal attempt number — never a
       random hash — so retries converge to the same identifier across
       processes.

    Determinism contract:
        ``derive_plane_identifier(x, attempt=a) ==
         derive_plane_identifier(x, attempt=a)``
        for any ``a >= 1``. Always.

    Args:
        name: Source string — usually the ensemble project's
            ``shortnames[0]`` (preferred; already short and human-set)
            or ``name`` fallback. Empty / non-ASCII names still yield
            a valid identifier via the fallback.
        attempt: 1-indexed attempt counter. Attempt 1 returns the
            unsuffixed base; attempt ≥ 2 returns ``base[:12-len(suffix)] + suffix``.

    Returns:
        A 1-12 character uppercase alphanumeric identifier safe to
        POST as the ``identifier`` field on Plane project creation.
    """
    if attempt < 1:
        # Out-of-range defensive — surface the contract violation
        # instead of silently normalizing (callers must always start
        # at 1).
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    # Sanitize: uppercase → alphanumeric only.
    sanitized = _IDENTIFIER_ALLOWED.sub("", name.upper())
    base = sanitized or _IDENTIFIER_EMPTY_FALLBACK
    if attempt == 1:
        return base[:_IDENTIFIER_MAX_LEN]
    suffix = str(attempt)
    base_len = _IDENTIFIER_MAX_LEN - len(suffix)
    if base_len <= 0:
        # 12-digit attempt — pathological — keep at least one base char
        # by replacing the right-most base char(s) with the suffix.
        base_len = 1
    return (base[:base_len] + suffix)[:_IDENTIFIER_MAX_LEN]


# ── Plane name sanitization ─────────────────────────────────────────────
#
# Plane project ``name`` is server-validated (unlike ``identifier``,
# whose validation we pre-empt in :func:`derive_plane_identifier`).
# Live-probed against plane.ensem.dev 2026-09-22 (workspace ``nea``,
# X-Api-Key, throwaway projects created + deleted):
#
# ================  ======  ================================================
# Probe name        Status  Body
# ================  ======  ================================================
# ``probe-hyphen…``  400    ``{"non_field_errors":["Project name cannot
#                           contain special characters."]}``
# ``probe space…``   201    created (name stored verbatim); DELETE → 204
# ``probe_under…``   201    created (name stored verbatim); DELETE → 204
# ``probe (paren)``, 400    same ``non_field_errors`` body (name validation
# ``ünï``                    fires before description is examined)
# ================  ======  ================================================
#
# Pinned acceptance rule for ``name``: letters, digits, spaces and
# underscores are ACCEPTED; hyphens, parentheses and non-ASCII letters
# are REJECTED with HTTP 400. The ``description`` field was NOT
# independently probed with special characters (the P4 rejection was
# name-driven), so description is deliberately NOT sanitized here.
#
# Strategy: every disallowed character RUN collapses to a single space
# (the confirmed-accepted separator) so ``agents-ensemble`` becomes
# ``agents ensemble`` — readable, and idempotent because the output
# alphabet ⊆ accepted set. Empty input (or input that sanitizes to
# nothing) falls back to a fixed accepted-charset name so the payload
# never carries an empty ``name``.
_NAME_ALLOWED = re.compile(r"[^A-Za-z0-9 _]")
_NAME_WHITESPACE_RUN = re.compile(r"\s+")
_PLANE_NAME_EMPTY_FALLBACK: str = "Ensemble Project"


def sanitize_plane_name(name: str) -> str:
    """Sanitize a project name into the Plane-accepted character set.

    Pinned rule (live-probed against plane.ensem.dev 2026-09-22 — see
    the module comment above for the full request/response matrix):

    1. Replace every character NOT in ``[A-Za-z0-9 _]`` with a space.
    2. Collapse whitespace runs to a single space; strip the ends.
    3. If the result is empty (empty input, or input made entirely of
       disallowed characters), return the fixed fallback
       :data:`_PLANE_NAME_EMPTY_FALLBACK` — deterministic, so a
       nameless project converges to the same Plane name on every
       retry instead of flip-flopping between create/update.

    Determinism + idempotency contract:
        ``sanitize_plane_name(x) == sanitize_plane_name(x)`` always,
        and ``sanitize_plane_name(sanitize_plane_name(x)) ==
        sanitize_plane_name(x)`` for every ``x`` — the output alphabet
        is a subset of the accepted set, so the second pass is a no-op.

    Apply at every point a name crosses the Ensemble → Plane boundary:
    the create/update payload, the adoption-by-name match, and the
    drift identity comparison. Comparing RAW Ensemble names against
    SANITIZED Plane-side names (or vice versa) makes hyphenated
    projects perpetually mismatch → duplicate-create attempts and
    false drift.

    Args:
        name: Raw project name (typically the Ensemble project's
            ``name`` column).

    Returns:
        A name containing only accepted characters, safe to POST/PATCH
        as the ``name`` field on Plane projects.
    """
    if not name:
        return _PLANE_NAME_EMPTY_FALLBACK
    cleaned = _NAME_ALLOWED.sub(" ", name)
    cleaned = _NAME_WHITESPACE_RUN.sub(" ", cleaned).strip()
    return cleaned or _PLANE_NAME_EMPTY_FALLBACK


def _rest_base_url() -> str | None:
    """Compose the REST base URL.

    Returns ``None`` when any required env var is missing so callers can
    skip the integration cleanly.
    """
    base = _env("PLANE_BASE_URL")
    workspace = _env("PLANE_MCP_WORKSPACE_SLUG")
    if not base or not workspace:
        return None
    # Strip trailing slash to avoid double-slash when joining paths.
    base = base.rstrip("/")
    return f"{base}/api/v1/workspaces/{workspace}/projects/"


# ── Client ──────────────────────────────────────────────────────────────────


class PlaneHttpClient:
    """Async client for the Plane REST API.

    Each method creates its own ``httpx.AsyncClient`` and closes it before
    returning. There is intentionally no ``__aenter__``/``__aexit__``
    because that would imply long-lived session state — which is exactly
    what we want to avoid (event-loop binding hazard).
    """

    # Sensible default for a SaaS API. Individual calls can override.
    DEFAULT_TIMEOUT_S: float = 30.0

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        workspace_slug: str | None = None,
        breaker: CircuitBreaker | None = None,
        timeout: float | None = None,
    ) -> None:
        """Construct a client.

        Args:
            base_url: REST base URL. Defaults to ``_rest_base_url()``.
            api_key: REST API token. Defaults to ``PLANE_API_KEY``.
                Sent as the ``X-Api-Key`` header. We do **not** fall back
                to ``PLANE_MCP_API_KEY`` — separation-of-concerns
                (independent rotation, scoped blast radius, clean
                audit trail in Plane's API Tokens UI); a silent
                fallback would mask misconfiguration.
            workspace_slug: Workspace slug. Defaults to
                ``PLANE_MCP_WORKSPACE_SLUG``. Used for the
                ``x-workspace-slug`` header.
            breaker: Optional :class:`CircuitBreaker` override (tests).
                Defaults to the module-level shared breaker.
            timeout: Per-request timeout in seconds.
        """
        self._base_url = base_url if base_url is not None else _rest_base_url()
        self._api_key = api_key if api_key is not None else _env("PLANE_API_KEY")
        self._workspace_slug = (
            workspace_slug
            if workspace_slug is not None
            else _env("PLANE_MCP_WORKSPACE_SLUG")
        )
        self._breaker = breaker if breaker is not None else _plane_breaker
        self._timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT_S

    # ── Feature gating ──────────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> bool:
        """Return True when all required env vars are present.

        Gated on ``PLANE_BASE_URL`` + ``PLANE_API_KEY``. We do **not**
        accept ``PLANE_MCP_API_KEY`` as a substitute — separation-of-
        concerns (independent rotation, scoped blast radius, clean
        audit trail in Plane's API Tokens UI); a silent fallback would
        mask misconfiguration (the exact incident class Phase 2 fixed).
        """
        return _rest_base_url() is not None and bool(_env("PLANE_API_KEY"))

    @classmethod
    def create(cls) -> "PlaneHttpClient | None":
        """Factory that returns ``None`` when feature is disabled.

        Use this when callers want to no-op cleanly: ``client = PlaneHttpClient.create()``;
        ``if client is None: return {"status": "disabled"}``.
        """
        if not cls.is_available():
            return None
        return cls()

    # ── Internal helpers ────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        """Build the request headers — never log the API key value.

        Plane REST uses the ``X-Api-Key`` header (not
        ``Authorization: Bearer`` — which is rejected with HTTP 401).
        """
        return {
            "X-Api-Key": self._api_key,
            "x-workspace-slug": self._workspace_slug,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a request under the circuit breaker.

        Translates 4xx/5xx into our typed errors. On success returns the
        parsed JSON body (or ``None`` for 204). Never logs the auth
        header — only the status code and response body on error.

        Raises:
            PlaneAuthError: 401/403.
            PlaneNotFoundError: 404.
            PlaneAPIError: any other non-2xx, or unexpected exception.
        """
        if not await self._breaker.can_execute():
            raise PlaneAPIError(
                "Circuit breaker is OPEN — skipping Plane API call"
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            await self._breaker.record_failure()
            logger.warning(
                "Plane HTTP error on %s %s: %s",
                method,
                url,
                exc,
            )
            raise PlaneAPIError(f"Plane HTTP error: {exc}") from exc

        status = response.status_code
        if 200 <= status < 300:
            await self._breaker.record_success()
            if status == 204:
                return None
            try:
                return response.json()
            except (ValueError, httpx.HTTPError):
                # Some 2xx responses may have empty bodies (defensive).
                logger.debug(
                    "Plane %s %s returned %s with non-JSON body",
                    method,
                    url,
                    status,
                )
                return None

        # Non-2xx — classify.
        body_text: str
        try:
            body_text = response.text
        except Exception:  # noqa: BLE001 — never let logging mask the error
            body_text = "<unreadable>"

        # Auth errors are permanent — record failure but the caller
        # typically short-circuits on the typed exception.
        if status in (401, 403):
            await self._breaker.record_failure()
            logger.warning(
                "Plane auth error %s on %s %s: %s",
                status,
                method,
                url,
                body_text[:500],
            )
            raise PlaneAuthError(
                f"Plane auth error {status}: {body_text[:200]}",
                status_code=status,
                body=body_text[:2000],
            )

        # 404: let caller decide whether to treat as "missing" or raise.
        if status == 404:
            # Do NOT count 404 as a breaker failure — a missing project
            # is not a server-health issue and shouldn't trip the breaker.
            await self._breaker.record_success()
            raise PlaneNotFoundError(
                f"Plane 404 on {url}: {body_text[:200]}",
                status_code=status,
                body=body_text[:2000],
            )

        # 429 — let the caller's retry/backoff layer handle it, but DO
        # count it as a breaker failure so we don't hammer a throttled API.
        if status == 429:
            await self._breaker.record_failure()
            logger.warning(
                "Plane rate-limited (429) on %s %s: %s",
                method,
                url,
                body_text[:500],
            )
            raise PlaneAPIError(
                f"Plane rate-limited: {body_text[:200]}",
                status_code=status,
                body=body_text[:2000],
            )

        # 409 — identifier collision (or other conflict). Typed so
        # ``create_project`` callers can drive a deterministic retry.
        # Verified live 2026-09-20: second POST with a taken
        # ``identifier`` returns 409.
        if status == 409:
            await self._breaker.record_failure()
            logger.warning(
                "Plane conflict (409) on %s %s: %s",
                status,
                method,
                url,
                body_text[:500],
            )
            raise PlaneIdentifierCollisionError(
                f"Plane identifier conflict: {body_text[:200]}",
                status_code=status,
                body=body_text[:2000],
            )

        # Other 4xx — log warning, count as failure (could indicate bad
        # payloads, schema drift, etc.).
        if 400 <= status < 500:
            await self._breaker.record_failure()
            logger.warning(
                "Plane client error %s on %s %s: %s",
                status,
                method,
                url,
                body_text[:500],
            )
            raise PlaneAPIError(
                f"Plane client error {status}: {body_text[:200]}",
                status_code=status,
                body=body_text[:2000],
            )

        # 5xx — server-side problem; count as failure.
        await self._breaker.record_failure()
        logger.warning(
            "Plane server error %s on %s %s: %s",
            status,
            method,
            url,
            body_text[:500],
        )
        raise PlaneAPIError(
            f"Plane server error {status}: {body_text[:200]}",
            status_code=status,
            body=body_text[:2000],
        )

    # ── Public API ──────────────────────────────────────────────────────

    async def create_project(
        self,
        name: str,
        description: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Create a new Plane project.

        Args:
            name: Project name (required).
            description: Optional human-readable description.
            **extra: Forwarded to the Plane API as additional fields
                (``identifier``, ``network``, etc.). Unknown extras are
                passed through verbatim — Plane ignores unknown fields.

        Returns:
            Plane project dict (typically with ``id``, ``name``, ``identifier``).

        Raises:
            PlaneAuthError: 401/403.
            PlaneIdentifierCollisionError: 409 (caller can drive a
                deterministic retry via :func:`derive_plane_identifier`).
            PlaneAPIError: any other non-2xx, or unexpected exception.
        """
        if not self._base_url:
            raise PlaneAPIError("Plane base URL not configured")
        # Plane rejects names containing characters outside
        # [A-Za-z0-9 _] with HTTP 400 ("Project name cannot contain
        # special characters." — live-probed 2026-09-22). Sanitize at
        # the contract boundary so hyphenated Ensemble names sync
        # instead of 400-ing (deterministic + idempotent — see
        # :func:`sanitize_plane_name`).
        body: dict[str, Any] = {"name": sanitize_plane_name(name)}
        if description is not None:
            body["description"] = description
        body.update(extra)
        result = await self._request("POST", self._base_url, json_body=body)
        if not isinstance(result, dict):
            raise PlaneAPIError(
                f"Plane create_project returned non-dict: {type(result).__name__}"
            )
        return result

    async def update_project(
        self,
        plane_id: str,
        name: str | None = None,
        description: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Update an existing Plane project (PATCH semantics).

        Only fields explicitly passed are sent — unknown fields are left
        untouched on the Plane side.

        Args:
            plane_id: Plane's internal UUID for the project.
            name: New name (optional).
            description: New description (optional).
            **extra: Additional fields to PATCH.

        Returns:
            Updated Plane project dict.

        Raises:
            PlaneNotFoundError: project does not exist.
            PlaneAuthError, PlaneAPIError: see :meth:`_request`.
        """
        if not self._base_url:
            raise PlaneAPIError("Plane base URL not configured")
        body: dict[str, Any] = {}
        if name is not None:
            # Same Plane-side rejection rule as create (sanitize at the
            # boundary — see :func:`sanitize_plane_name`).
            body["name"] = sanitize_plane_name(name)
        if description is not None:
            body["description"] = description
        body.update(extra)
        if not body:
            # Nothing to update — fall through to GET to return current state.
            return await self.get_project(plane_id) or {}
        url = f"{self._base_url}{plane_id}/"
        result = await self._request("PATCH", url, json_body=body)
        if not isinstance(result, dict):
            raise PlaneAPIError(
                f"Plane update_project returned non-dict: {type(result).__name__}"
            )
        return result

    async def get_project(self, plane_id: str) -> dict[str, Any] | None:
        """Fetch a Plane project by ID; return ``None`` on 404.

        Args:
            plane_id: Plane's internal UUID.

        Returns:
            Project dict, or ``None`` if Plane returned 404.

        Raises:
            PlaneAuthError, PlaneAPIError: see :meth:`_request`.
        """
        if not self._base_url:
            raise PlaneAPIError("Plane base URL not configured")
        url = f"{self._base_url}{plane_id}/"
        try:
            result = await self._request("GET", url)
        except PlaneNotFoundError:
            return None
        if not isinstance(result, dict):
            raise PlaneAPIError(
                f"Plane get_project returned non-dict: {type(result).__name__}"
            )
        return result

    async def list_projects(self) -> list[dict[str, Any]]:
        """List all Plane projects in the workspace.

        Returns:
            List of project dicts (may be empty).

        Raises:
            PlaneAuthError, PlaneAPIError: see :meth:`_request`.
        """
        if not self._base_url:
            raise PlaneAPIError("Plane base URL not configured")
        result = await self._request("GET", self._base_url)
        if result is None:
            return []
        if isinstance(result, list):
            return result
        # Plane sometimes wraps results — accept dict with a results key.
        if isinstance(result, dict):
            for key in ("results", "projects", "data"):
                if isinstance(result.get(key), list):
                    return list(result[key])
        logger.warning(
            "Plane list_projects returned unexpected shape: %s",
            type(result).__name__,
        )
        return []