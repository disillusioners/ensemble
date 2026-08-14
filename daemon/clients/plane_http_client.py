"""Async HTTP client for the Plane (plane.so) REST API.

Used by the structural sync subsystem to mirror Ensemble projects to
Plane as plain projects (no issues/cycles at v1). The client follows the
existing pattern of constructing a fresh ``httpx.AsyncClient`` per call
(no module-level singleton) to sidestep the event-loop binding hazard.

Endpoint shape::

    {PLANE_BASE_URL}/api/v1/workspaces/{PLANE_MCP_WORKSPACE_SLUG}/projects/

Headers::

    Authorization: Bearer {PLANE_MCP_API_KEY}
    x-workspace-slug: {PLANE_MCP_WORKSPACE_SLUG}
    Content-Type: application/json

Feature gating
--------------
The client is feature-gated on ``PLANE_BASE_URL``: when the URL env var
is unset (or empty), :meth:`is_available` returns ``False`` and
:meth:`create` returns ``None``. This lets callers no-op gracefully
without sprinkling ``if`` checks throughout the codebase.

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
from typing import Any

import httpx

from daemon.sources.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


# ── Custom errors ──────────────────────────────────────────────────────────


class PlaneAPIError(Exception):
    """Generic Plane API failure (non-auth, non-404 4xx; or 5xx)."""


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
    """Read an env var, stripped of whitespace; empty string if missing."""
    return (os.environ.get(name, "") or "").strip()


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
            api_key: Bearer token. Defaults to ``PLANE_MCP_API_KEY``.
            workspace_slug: Workspace slug. Defaults to
                ``PLANE_MCP_WORKSPACE_SLUG``. Used for the
                ``x-workspace-slug`` header.
            breaker: Optional :class:`CircuitBreaker` override (tests).
                Defaults to the module-level shared breaker.
            timeout: Per-request timeout in seconds.
        """
        self._base_url = base_url if base_url is not None else _rest_base_url()
        self._api_key = api_key if api_key is not None else _env("PLANE_MCP_API_KEY")
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
        """Return True when all required env vars are present."""
        return _rest_base_url() is not None and bool(_env("PLANE_MCP_API_KEY"))

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
        """Build the request headers — never log the Authorization value."""
        return {
            "Authorization": f"Bearer {self._api_key}",
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
                f"Plane auth error {status}: {body_text[:200]}"
            )

        # 404: let caller decide whether to treat as "missing" or raise.
        if status == 404:
            # Do NOT count 404 as a breaker failure — a missing project
            # is not a server-health issue and shouldn't trip the breaker.
            await self._breaker.record_success()
            raise PlaneNotFoundError(f"Plane 404 on {url}: {body_text[:200]}")

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
            raise PlaneAPIError(f"Plane rate-limited: {body_text[:200]}")

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
                f"Plane client error {status}: {body_text[:200]}"
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
        raise PlaneAPIError(f"Plane server error {status}: {body_text[:200]}")

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
            PlaneAuthError, PlaneAPIError: see :meth:`_request`.
        """
        if not self._base_url:
            raise PlaneAPIError("Plane base URL not configured")
        body: dict[str, Any] = {"name": name}
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
            body["name"] = name
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