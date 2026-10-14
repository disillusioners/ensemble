"""Custom-agent lifecycle REST endpoints (Phase C).

User Directive #5A (2026-10-14, overnight, R-A1/A2/B1-B4 ruling).
Sits beside the predefined-agent endpoints in
``daemon/routers/agents.py`` (which own the template-copy
creation path for the legacy ``_baby_template`` flow — that path
is unchanged). The custom-agent flow is R-A1/R-A2:

* **R-A1 — v1 lands APPROVED.** The creating user IS the human
  approver; the v1 prompt_versions rows land directly in
  ``state='approved'`` + ``is_selected=1`` with
  ``approver_subject`` = the HTTP actor subject.
* **R-A2 — NO template file.** Custom agents are DB-born; the
  router writes ONLY to the ``custom_agent_configs`` +
  ``prompt_versions`` tables — never creates a directory or
  ``meta.json`` file under ``agents/``.

The router is mounted under the same ``/api/...`` namespace as
the predefined flow. Endpoints:

* ``POST /api/custom-agents`` — create (201) + v1 versions.
  Required: ``name`` + ``soul_md``. Optional: ``rule_md`` /
  ``workflow_md`` / ``tools_note_md`` / ``tools_md``. The
  config fields (display, tools, model, team, innate_skills,
  capabilities) and the per-agent knobs are also optional.
* ``GET /api/custom-agents`` — list all LIVE configs (the
  ``/api/agents`` listing unifies file-defined + DB-resident
  rows in the hybrid discovery commit).
* ``GET /api/custom-agents/{user_name}`` — single config +
  v1 versions.
* ``DELETE /api/custom-agents/{user_name}`` — soft-delete (R-A2
  conservative: rows are kept; ``deleted_at`` is set; the
  ``prompt_versions`` history is untouched).

Errors map to the existing daemon-typed envelope (``error`` +
``message`` per ``research-findings-db-api.md`` §1 API shape).

**Authorization (overnight F1 gate — 2026-10-14, R-A1/A2 ruling,
leader REJECT → APPROVED).** The family shipped with ZERO
authorization; the F1 finding requires it to mirror the
prompts-family pattern (``daemon/routers/prompts.py`` is the
canonical exemplar). Each endpoint carries a
``@require_scope`` decorator AND an inline ``_require_auth_on``
503 gate that fires BEFORE the decorator — the decorator is
inert when auth is OFF (decorators.py:74-84), so the explicit
gate is the fail-closed posture.

Scope mapping (no NEW scope minted; the leader's F1 directive
REUSES ``prompts:read`` / ``prompts:write`` from the prompt-
immutability Phase B T4 taxonomy — the directive explicitly
forbids inventing new scopes and labels the choice as a
deliberate scope-reuse convention until a custom-agents
boundary is opened in the taxonomy):

* ``prompts:read``  — GET (list + single)
* ``prompts:write`` — POST (create) + DELETE (soft-delete)

These are NOT a permissions slip: a request that carries the
wider ``prompts:write`` already crosses the same privilege
boundary by definition. Custom-agent lifecycle operates
inside the same v1 scope family as the prompt-version REST
surface because it issues prompt-version writes
(``create_custom_agent`` writes v1 APPROVED rows under R-A1;
``soft_delete_custom_agent`` keeps the rows but flips
``deleted_at`` on the config row). The reuse will be revisited
the first time the custom-agents surface requires a scope the
prompts family does not (per the §8.4 register entry).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from daemon.auth.decorators import require_scope
from daemon.repositories.custom_agent_configs import CustomAgentIdError
from daemon.repositories.custom_agent_configs.repository import (
    CustomAgentCollisionError,
)
from daemon.services.custom_agent_lifecycle_service import (
    CustomAgentCreationError,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/custom-agents",
    tags=["custom-agents"],
)


# =========================================================================
# Pydantic body model — M2 / M17 fix-cycle.
#
# ``dict[str, Any]`` was the M17 source of 500s from un-coercible
# numerics (``int("yes")`` / ``float("xyz")``). Pydantic Field
# constraints catch the same shape issues at the framework boundary
# (a 422 with a structured error envelope replaces a 500). Field
# names match the service's ``create_custom_agent`` keyword signature
# verbatim so the handler body stays a 1:1 field pass-through.
# =========================================================================


class _CustomAgentCreate(BaseModel):
    """POST /api/custom-agents request body (M2 + M17).

    Required:

    * ``user_name`` — bare agent id (alphanumeric + hyphen +
      underscore; the router adds the ``custom:`` prefix internally).
    * ``name`` — display name (non-empty).
    * ``soul_md`` — soul content (non-empty; R-A1 + R-A2 require it).

    Optional — every other prompt artifact + per-tool shape. Defaults
    mirror the prior handler: ``description=""``, ``color`` and
    ``icon`` unset at the schema level so the service falls through
    to its own defaults (``"🤖"`` / ``"accent-blue"``).
    """

    user_name: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9_\-]+$",
    )
    name: str = Field(min_length=1)
    soul_md: str = Field(min_length=1)
    rule_md: Optional[str] = None
    workflow_md: Optional[str] = None
    tools_note_md: Optional[str] = None
    tools_md: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    llm_model: Optional[str] = None
    llm_models: Optional[list[Any]] = None
    team_members: Optional[list[str]] = None
    innate_skills: Optional[list[str]] = None
    capabilities: Optional[list[str]] = None
    skill_injection: Optional[int] = None
    snapshot_enabled: Optional[int] = None
    blueprint_inactive: Optional[int] = None
    inject_allowed_models: Optional[int] = None
    recursion_limit_multiplier: Optional[float] = None
    recursion_limit: Optional[int] = None
    context_injection_heuristic_match_shared_md_files: Optional[int] = None
    tools_allow: Optional[list[str]] = None
    tools_deny: Optional[list[str]] = None
    tools_deny_spawn: Optional[list[str]] = None


# =========================================================================
# Authorization constants + 503 gate — leader F1 finding (overnight,
# 2026-10-14). Mirrors the prompts-family pattern verbatim
# (daemon/routers/prompts.py:160-199). The gate MUST run BEFORE the
# @require_scope (which is inert when auth is OFF — decorators.py:74-84).
# =========================================================================

#: POST / DELETE — write-class surface (the F1 directive deliberately
#: reuses the prompts:write scope rather than minting a new one).
_SCOPE_WRITE = "prompts:write"
#: GET (list + single) — read-class surface (F1 reuse: prompts:read).
_SCOPE_READ = "prompts:read"
#: Stable error code token for the 503 body (client-stable — do NOT
#: change without a coordinated FE toast update).
_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
#: Stable error code token for the 401 body (auth-ON but AuthContext
#: missing — middleware gap; see _actor_from_request below).
_ERR_AUTH_CONTEXT_MISSING = "AUTH_CONTEXT_MISSING"
#: Stable error code token for the 404 body on GET-single / DELETE
#: when no live custom-agent row matches the wire id. Mirrors the
#: prompts family token ``VERSION_NOT_FOUND`` — the H3 envelope
#: contract is shape-consistent across routers.
_ERR_CUSTOM_AGENT_NOT_FOUND = "CUSTOM_AGENT_NOT_FOUND"
#: Stable error code token for the 400 body on user-supplied
#: validation errors (mirrors the prompts family token
#: ``INVALID_REQUEST`` — reused deliberately per the F1 scope-
#: reuse convention).
_ERR_INVALID_REQUEST = "INVALID_REQUEST"
#: Stable error code token for the 503 body when the manager or
#: the lifecycle service is not wired (test-fixture gap; NOT a
#: client-facing failure).
_ERR_INTERNAL_ERROR = "INTERNAL_ERROR"


def _err(status_code: int, error: str, message: str) -> HTTPException:
    """Plain-dict error envelope for the auth posture (F1).

    Body is ``{"error": CODE, "message": ...}`` — byte-stable across
    OpenAPI rendering versions, mirrors ``daemon/routers/prompts.py``
    line 180 verbatim so the auth-class contract is shape-consistent
    across both routers (FE toasts on ``error``).
    """
    return HTTPException(
        status_code=status_code,
        detail={"error": error, "message": message},
    )


def _require_auth_on(request: Request) -> None:
    """The F1 503 gate — auth-OFF fail-closed posture (D3 / F1).

    Runs BEFORE the @require_scope decorator. Reads the same
    ``request.app.state.auth_config`` attribute the decorator
    consults; raises 503 ``AUTH_NOT_CONFIGURED`` when absent.
    Mirrors the prompts-family contract exactly
    (``daemon/routers/prompts.py:188-199``).

    The §8.4 anonymous-dev ruling (the auth-OFF actor-fallback on
    the POST path) SUPERSEDED for THIS router by the F1 directive:
    the 503 fires first; the fallback is dead in practice here
    but the code path is kept so future surfaces can opt back in
    via NOT calling this gate.
    """
    cfg = getattr(getattr(request.app, "state", None), "auth_config", None)
    if cfg is None:
        raise _err(503, _ERR_AUTH_NOT_CONFIGURED, "Auth not configured (fail-closed)")


# =========================================================================
# Helpers
# =========================================================================


def _actor_from_request(request: Request) -> Any:
    """Build an ActorContext from the request (HTTP-lane stamp).

    The custom-agent POST endpoint mandates an HTTP-lane actor
    (the R-A1 invariant: "the creating user IS the human
    approver"). The request-shape reader mirrors the
    prompt-version repo's ``actor_from_request`` (S16 anti-
    forgery): the body is IGNORED; identity comes ONLY from
    ``request.state.auth_context``.

    **F1 reconciliation note.** The original implementation fell
    back to a deterministic ``anonymous-dev`` ActorContext when
    the auth-context was missing — the §8.4 register entry. The
    F1 directive places the ``_require_auth_on(request)`` 503
    gate BEFORE this helper on every endpoint of THIS router,
    so auth-OFF never reaches this branch (the F1 503 fires
    first). The §8.4 fallback code path is RETAINED so any
    FUTURE router surface that opts in (by NOT calling the 503
    gate explicitly) inherits the auth-OFF conservative
    posture. Import-repair note: ``AuthContextMissingError``
    lives in the service layer (PB-A,
    ``daemon/services/prompt_version_service.py:226``); the
    prior import from the REPO module was wrong — every POST
    was 500-ing on the missing-class ImportError. The fixed
    import is from the service layer.
    """
    from daemon.repositories.prompt_version.repository import (
        actor_from_request,
    )
    from daemon.services.prompt_version_service import (
        AuthContextMissingError,
    )

    try:
        return actor_from_request(request)
    except AuthContextMissingError:
        # Future-surface auth-OFF fallback (§8.4 anonymous-dev
        # register entry — preserved verbatim; the F1 gate
        # blocks this branch on the current router).
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
        )

        return ActorContext(
            subject="anonymous-dev",
            auth_method="session",
            instance_id=None,
            agent_id=None,
            version_tag="",
        )


def _lifecycle_service(request: Request) -> Any:
    """Reach the custom-agent lifecycle service on the manager.

    Mirrors ``daemon/routers/prompts.py:_get_service`` (the
    request.app.state.manager precedent — see also
    skill_bank.py:78-80). Lifetimes are app-scoped via the
    daemon/api.py lifespan's ``app.state.manager`` wiring.

    Args:
        request: The FastAPI request (the manager lives on
            ``request.app.state.manager`` per the daemon's
            lifespan wiring).

    Returns:
        Configured :class:`CustomAgentLifecycleService`
        instance.

    Raises:
        HTTPException(503): When the manager is not wired (a
            test-fixture gap, NOT a client-facing failure).
    """
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        raise _err(
            503,
            _ERR_INTERNAL_ERROR,
            (
                "Custom-agent lifecycle service unavailable "
                "(manager not configured)"
            ),
        )
    svc = getattr(manager, "_custom_agent_lifecycle_service", None)
    if svc is None:
        raise _err(
            503,
            _ERR_INTERNAL_ERROR,
            (
                "Custom-agent lifecycle service unavailable "
                "(_custom_agent_lifecycle_service missing)"
            ),
        )
    return svc


# =========================================================================
# Endpoints
# =========================================================================


@router.post("", status_code=201)
@require_scope(_SCOPE_WRITE)
async def create_custom_agent(
    request: Request,
    body: _CustomAgentCreate,
):
    """Create a custom agent + v1 approved prompt rows (R-A1).

    Required body fields:

    * ``user_name`` — the bare agent id (e.g. ``"myhelper"``).
      Alphanumeric + hyphen + underscore; the router adds the
      ``custom:`` prefix internally.
    * ``name`` — display name (non-empty).
    * ``soul_md`` — soul content (non-empty; required by R-A1 +
      R-A2 + the empty-state posture: a custom agent with no
      soul content is rejected by ``NoPromptContentError`` on
      every spawn).

    Optional body fields:

    * ``rule_md`` / ``workflow_md`` / ``tools_note_md`` /
      ``tools_md`` — additional v1 contents (non-empty =
      includes the v1 row; absent / empty = no row written).
    * Display: ``description`` / ``icon`` / ``color``.
    * Model: ``llm_model`` / ``llm_models``.
    * Team: ``team_members`` / ``innate_skills`` /
      ``capabilities``.
    * Knobs: ``skill_injection`` / ``snapshot_enabled`` /
      ``blueprint_inactive`` / ``inject_allowed_models`` /
      ``recursion_limit_multiplier`` / ``recursion_limit``.
    * Tools: ``tools_allow`` / ``tools_deny`` /
      ``tools_deny_spawn``.
    * Context: ``context_injection_heuristic_match_shared_md_files``.

    **Authorization (F1).** ``@require_scope("prompts:write")`` +
    the inline ``_require_auth_on(request)`` 503 gate (the F1
    directive — the decorator is inert when auth is OFF, so
    the gate fires first; the prompts-family pattern).
    """
    _require_auth_on(request)
    actor = _actor_from_request(request)

    # NOTE: we look up the lifecycle service AFTER the body
    # validation (now Pydantic-driven → 422 at the framework
    # boundary) so a 400 / 422 (client fixable) surfaces before the
    # 503 / manager wiring check (server misconfigured).
    svc = _lifecycle_service(request)

    try:
        result = await svc.create_custom_agent(
            user_name=body.user_name,
            name=body.name,
            actor=actor,
            soul_md=body.soul_md,
            rule_md=body.rule_md,
            workflow_md=body.workflow_md,
            tools_note_md=body.tools_note_md,
            tools_md=body.tools_md,
            description=body.description or "",
            icon=body.icon or "🤖",
            color=body.color or "accent-blue",
            llm_model=body.llm_model,
            llm_models=body.llm_models,
            team_members=body.team_members,
            innate_skills=body.innate_skills,
            capabilities=body.capabilities,
            skill_injection=body.skill_injection or 0,
            snapshot_enabled=body.snapshot_enabled or 0,
            blueprint_inactive=body.blueprint_inactive or 0,
            inject_allowed_models=body.inject_allowed_models or 0,
            recursion_limit_multiplier=(
                body.recursion_limit_multiplier
                if body.recursion_limit_multiplier is not None
                else 1.0
            ),
            recursion_limit=body.recursion_limit,
            context_injection_heuristic_match_shared_md_files=(
                body.context_injection_heuristic_match_shared_md_files or 0
            ),
            tools_allow=body.tools_allow,
            tools_deny=body.tools_deny,
            tools_deny_spawn=body.tools_deny_spawn,
        )
    except CustomAgentIdError as e:
        raise _err(400, _ERR_INVALID_REQUEST, str(e))
    except CustomAgentCollisionError as e:
        # Pre-checked race-free in the service (H5): a duplicate
        # ``agent_id`` is detected by a SELECT before the INSERT,
        # so 409 maps directly from this typed exception with no
        # substring sniffing.
        raise _err(409, _ERR_INVALID_REQUEST, str(e))
    except CustomAgentCreationError as e:
        # Genuine creation failure: empty ``soul_md``, FK
        # violation, NOT-NULL / CHECK constraint, or the
        # IntegrityError race window where another writer
        # slipped between the pre-SELECT and the INSERT. The
        # service preserves the original ``from e`` chain for
        # forensics.
        raise _err(400, _ERR_INVALID_REQUEST, str(e))

    return result


@router.get("")
@require_scope(_SCOPE_READ)
async def list_custom_agents(request: Request):
    """List all LIVE custom-agent configs.

    **Authorization (F1).** ``@require_scope("prompts:read")`` +
    the inline ``_require_auth_on(request)`` 503 gate.
    """
    _require_auth_on(request)
    svc = _lifecycle_service(request)
    items = await svc.list_active()
    return {"items": items, "total": len(items)}


@router.get("/{user_name}")
@require_scope(_SCOPE_READ)
async def get_custom_agent(request: Request, user_name: str):
    """Get one custom agent's config + versions.

    **Authorization (F1).** ``@require_scope("prompts:read")`` +
    the inline ``_require_auth_on(request)`` 503 gate.
    """
    _require_auth_on(request)
    svc = _lifecycle_service(request)
    try:
        config = await svc.get_by_agent_id(user_name)
    except CustomAgentIdError as e:
        raise _err(400, _ERR_INVALID_REQUEST, str(e))
    if config is None:
        raise _err(
            404,
            _ERR_CUSTOM_AGENT_NOT_FOUND,
            f"Custom agent not found: {user_name}",
        )
    return config


@router.delete("/{user_name}")
@require_scope(_SCOPE_WRITE)
async def delete_custom_agent(request: Request, user_name: str):
    """Soft-delete a custom agent (R-A2 conservative: rows kept).

    **Authorization (F1).** ``@require_scope("prompts:write")`` +
    the inline ``_require_auth_on(request)`` 503 gate.
    """
    _require_auth_on(request)
    svc = _lifecycle_service(request)
    try:
        deleted = await svc.soft_delete_custom_agent(
            user_name=user_name,
        )
    except CustomAgentIdError as e:
        raise _err(400, _ERR_INVALID_REQUEST, str(e))
    if not deleted:
        raise _err(
            404,
            _ERR_CUSTOM_AGENT_NOT_FOUND,
            f"Custom agent not found: {user_name}",
        )
    return {
        "deleted": True,
        "user_name": user_name,
        "soft_deleted": True,
    }


__all__ = ["router"]
