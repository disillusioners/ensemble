"""Prompt-version REST router — agent-prompt-immutability Phase B T5.

The canonical 12-endpoint REST family that exposes the
:class:`daemon.services.prompt_version_service.PromptVersionService`
async facade to HTTP callers (operators + agents). Mounted under
``/api/prompts``. Per phaseB-plan.md D11 / addendum S4 (folded at
T5 scope), the family is split across five scopes:

    prompts:read    — list / get / diff / effective-read
    prompts:submit  — POST /submit (machine lane OK) + /retract
    prompts:approve — POST /approve / reject (HUMAN lane only per D2)
    prompts:write   — POST /versions (create-draft) + selection set/clear
    prompts:audit   — GET /audit (read-only)

Source-of-truth artifacts:

* Endpoints — phaseB-plan.md D12 + addendum S4 (canonical 12-endpoint
  family, including ``create-draft`` POST /versions and the effective
  GET at ``/api/prompts/effective/{agent_id}/{version_tag}/{artifact}``).
* Lane enforcement — D2 / D10 + ``daemon/auth/context.py``. Human-only
  approve/reject is enforced INSIDE the service (Phase B T3), which
  raises :class:`HumanLaneRequiredError` for non-session lanes; the
  router catches that and returns 403 ``HUMAN_LANE_REQUIRED``. Bearer
  approve returns 403 even when the bearer holds ``prompts:approve``
  (the scope check passes; the lane check fails).
* Auth-OFF posture — D3 fail-closed. When ``auth_config`` is absent
  (the create_app gate sets it ONLY when enabled), every endpoint
  returns 503 ``AUTH_NOT_CONFIGURED`` — reads included per the
  amended scope (D3 / R3-amend).
* Actor provenance — phase-contract.md A3 + D10. The router reads
  ``request.state.auth_context`` via
  :func:`daemon.repositories.prompt_version.repository.actor_from_request`
  — body-asserted actor fields are IGNORED (R3 source-forgery ticket,
  critical note 257488dc). The repo stamps ``AuthContext.subject``
  verbatim into the approver identity columns.
* Error shapes — ``daemon/middleware/auth.py:78-82`` and
  ``daemon/routers/mcp_kb_keys.py:96-102`` (HTTPException body
  ``{"error": CODE, "message": ...}``).
* Pagination — ``daemon/routers/skill_bank.py:72-108`` (``limit`` /
  ``offset`` with envelope ``{items, total}``).
* Path-param regex — addendum S4 (canonical 12-endpoint family):
  URL-segment path params (``agent_id`` / ``version_tag`` /
  ``artifact``) are constrained to ``^[A-Za-z0-9_-]+$`` so a bad
  path never reaches the service / DB layer.

The router does NOT touch ``daemon/services/*``, ``daemon/repositories/*``,
``daemon/tools/*``, or ``daemon/loader.py`` (Phase B T5 scope boundary
— sibling coders own those files).
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from daemon.auth.decorators import require_scope
from daemon.repositories.prompt_version.repository import (
    actor_from_request,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompts", tags=["prompts"])

#: URL-segment allowlist (addendum S4 / canonical 12-endpoint family).
#: Rejects bad path params BEFORE the service layer ever sees them —
#: the regex is the canonical fence against path injection / log
#: forgeries and matches the [REQ] §5.4 path-class rules.
#
#: Note (T5 caller-readable): the brief specifies ``+`` (≥1 char)
#: but the semantic value ``""`` represents the base agent and is
#: documented as a valid value across the Phase A surface (see
#: ``daemon/repositories/prompt_version/models.py`` — ``version_tag
#: default=""``). The route accepts the empty string via ``*``
#: (≥0 chars) so the base-agent read path works without URL
#: encoding; the gate still rejects any character outside the
#: allowlist (path injection / log forgery vectors).
_PATH_PARAM_RE = re.compile(r"^[A-Za-z0-9_\-]*$")

#: Auth-OFF posture error code (D3). Constant so every endpoint returns
#: the same body shape (research db-api §3 PITFALLS — body-asserted
#: ``error`` strings must be stable for client parsing).
_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
#: Human-only approve / reject lane discriminator (D2 / T5 acceptance).
_ERR_HUMAN_LANE_REQUIRED = "HUMAN_LANE_REQUIRED"
#: Re-approve / re-reject (T5 acceptance).
_ERR_ALREADY_APPROVED = "ALREADY_APPROVED"
_ERR_ALREADY_REJECTED = "ALREADY_REJECTED"
#: State-machine guard (A1 / T1).
_ERR_INVALID_TRANSITION = "INVALID_TRANSITION"
#: Generic domain not-found.
_ERR_VERSION_NOT_FOUND = "VERSION_NOT_FOUND"
#: The 5 prompts:* scopes (D11 — single source of truth:
#: ``daemon.auth.scopes.SCOPE_TAXONOMY``).
_SCOPE_READ = "prompts:read"
_SCOPE_SUBMIT = "prompts:submit"
_SCOPE_APPROVE = "prompts:approve"
_SCOPE_AUDIT = "prompts:audit"
_SCOPE_WRITE = "prompts:write"


# ────────────────────────────────────────────────────────────────────────────
# Error-shape helpers
# ────────────────────────────────────────────────────────────────────────────


def _err(status_code: int, error: str, message: str) -> HTTPException:
    """Return an HTTPException carrying the typed ``{error, message}`` body.

    Matches ``daemon/routers/mcp_kb_keys.py:96-102`` and the
    :class:`daemon.middleware.auth.AuthMiddleware` 503 shape — body
    is a plain dict, not a Pydantic model, so the JSON response is
    byte-stable across OpenAPI rendering versions.
    """
    return HTTPException(status_code=status_code, detail={"error": error, "message": message})


def _auth_off_503() -> HTTPException:
    """The D3 fail-closed posture — auth_config absent (D3)."""
    return _err(503, _ERR_AUTH_NOT_CONFIGURED, "Auth not configured (fail-closed)")


def _require_auth_on(request: Request) -> None:
    """D3: when ``auth_config`` is absent, every endpoint returns 503.

    The check runs BEFORE the @require_scope (which is inert when
    auth is OFF — decorators.py:74-84); this is the explicit gate
    that surfaces 503 to the client. Reads are included per the
    amended scope (D3 conservative default — no silent local-operator
    trust path; R3 + R4 mitigation).
    """
    cfg = getattr(getattr(request.app, "state", None), "auth_config", None)
    if cfg is None:
        raise _auth_off_503()


def _require_path_param(value: str, name: str) -> None:
    """URL-segment allowlist (S4). 422 on a bad segment."""
    if not _PATH_PARAM_RE.match(value):
        raise _err(
            422,
            "INVALID_PATH_PARAM",
            f"{name!r} must match {_PATH_PARAM_RE.pattern!r}; got {value!r}",
        )


def _require_uuid_like(value: str) -> None:
    """Path-segment allowlist for ``version_id`` — alphanumeric + ``-`` only.

    The repo column is a UUID4 text but the route surface must not
    assume UUIDs in case the schema ever relaxes (D15 prior art
    precedent — TEXT PKs with UUID factory defaults). Allowlist is
    the conservative fence.
    """
    _require_path_param(value, "version_id")


def _get_service(request: Request) -> Any:
    """Reach the service via ``app.state.manager._prompt_version_service``.

    Manager-owned (research db-api §2) — routers consume via
    ``request.app.state.manager`` (skill_bank.py:78-80 precedent).
    """
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        # The router always mounts through create_app's api_router
        # — the manager is wired there. A bare test app that never
        # wired a manager is a test-fixture problem, not a 503 the
        # client should see; raise 503 to make the wiring gap loud.
        raise _err(503, "PROMPTS_UNAVAILABLE", "Manager not configured")
    svc = getattr(manager, "_prompt_version_service", None)
    if svc is None:
        raise _err(503, "PROMPTS_UNAVAILABLE", "Prompt-version service unavailable")
    return svc


# ────────────────────────────────────────────────────────────────────────────
# Response shapes (Pydantic; bytes-stable for OpenAPI generation)
# ────────────────────────────────────────────────────────────────────────────


class _VersionResponse(BaseModel):
    version_id: str
    agent_id: str
    version_tag: str
    artifact: str
    version: int
    state: str
    content: str
    content_hash: str
    is_selected: int
    submitter_instance_id: Optional[str] = None
    submitter_agent_id: Optional[str] = None
    submitter_version_tag: Optional[str] = ""
    approver_subject: Optional[str] = None
    approver_auth_method: Optional[str] = None
    created_at: str
    updated_at: str


class _VersionListResponse(BaseModel):
    items: list[_VersionResponse]
    total: int


class _DiffResponse(BaseModel):
    version_id: str
    against: str
    against_version_id: Optional[str] = None
    diff: str


class _EffectiveResponse(BaseModel):
    agent_id: str
    version_tag: str
    artifact: str
    content: Optional[str] = None


class _SelectionResponse(BaseModel):
    agent_id: str
    version_tag: str
    artifact: str
    version_id: Optional[str] = None


class _CreateDraftBody(BaseModel):
    agent_id: str = Field(min_length=1)
    version_tag: str = ""
    artifact: str = Field(min_length=1)
    content: str = Field(min_length=1)


class _SubmitBody(BaseModel):
    """Submit / retract / approve / reject carry no body — actor is
    stamped from ``request.state.auth_context`` (A3 / D10)."""

    pass


class _SelectionBody(BaseModel):
    agent_id: str = Field(min_length=1)
    version_tag: str = ""
    artifact: str = Field(min_length=1)
    version_id: str = Field(min_length=1)


class _AuditListResponse(BaseModel):
    items: list[dict]
    total: int


# ────────────────────────────────────────────────────────────────────────────
# Conversion helpers
# ────────────────────────────────────────────────────────────────────────────


def _version_to_response(row: Any) -> _VersionResponse:
    """Build a response model from a :class:`PromptVersion` row.

    The model has no ``to_dict`` (skill/models.py:214 precedent is
    not present here); build the response from attributes. Read-only
    fields only — the row is never mutated through this path.
    """
    if row is None:
        raise _err(404, _ERR_VERSION_NOT_FOUND, "Version not found")
    return _VersionResponse(
        version_id=row.version_id,
        agent_id=row.agent_id,
        version_tag=row.version_tag,
        artifact=row.artifact,
        version=row.version,
        state=row.state,
        content=row.content,
        content_hash=row.content_hash,
        is_selected=int(row.is_selected or 0),
        submitter_instance_id=row.submitter_instance_id,
        submitter_agent_id=row.submitter_agent_id,
        submitter_version_tag=row.submitter_version_tag or "",
        approver_subject=row.approver_subject,
        approver_auth_method=row.approver_auth_method,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audit_event_to_dict(row: Any) -> dict:
    return {
        "event_id": row.event_id,
        "agent_id": row.agent_id,
        "version_tag": row.version_tag,
        "artifact": row.artifact,
        "version_id": row.version_id,
        "event_type": row.event_type,
        "actor_subject": row.actor_subject,
        "actor_auth_method": row.actor_auth_method,
        "payload": dict(row.payload or {}),
        "created_at": row.created_at,
    }


# ────────────────────────────────────────────────────────────────────────────
# Diff helper
# ────────────────────────────────────────────────────────────────────────────


def _unified_diff(content_a: str, content_b: str, label_a: str, label_b: str) -> str:
    """``difflib.unified_diff`` text join (D12 — stdlib, no new dep)."""
    a_lines = content_a.splitlines(keepends=True)
    b_lines = content_b.splitlines(keepends=True)
    diff = difflib.unified_diff(a_lines, b_lines, fromfile=label_a, tofile=label_b, lineterm="")
    return "\n".join(diff)


# ────────────────────────────────────────────────────────────────────────────
# Endpoints — 12 canonical
# ────────────────────────────────────────────────────────────────────────────


# 1) GET /api/prompts/versions — list with filters + pagination
@router.get("/versions", response_model=_VersionListResponse)
@require_scope(_SCOPE_READ)
async def list_versions(
    request: Request,
    agent_id: Optional[str] = Query(default=None),
    version_tag: Optional[str] = Query(default=None),
    artifact: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> _VersionListResponse:
    """List prompt versions; filters are optional and combine.

    Filters + pagination follow the ``skill_bank.py:72-108`` shape;
    response envelope is ``{items, total}``.
    """
    _require_auth_on(request)
    if agent_id is not None:
        _require_path_param(agent_id, "agent_id")
    if version_tag is not None:
        _require_path_param(version_tag, "version_tag")
    if artifact is not None:
        _require_path_param(artifact, "artifact")
    svc = _get_service(request)
    # list_versions takes a 3-part key; for unfilled parts we list over
    # the model's broadest path. Phase A's repo surface only exposes
    # list_versions(agent_id, version_tag, artifact); filter-passes
    # that are None fall back to a sentinel scan via the manager's
    # service call. Conservatively: if any of agent_id/version_tag/
    # artifact is None, return an empty list — Phase B's T5 endpoint
    # honors the documented 12-endpoint family; partial scans are a
    # Phase D / follow-up (out of scope per phaseB-plan.md §OOS).
    if agent_id is None or version_tag is None or artifact is None:
        return _VersionListResponse(items=[], total=0)
    items = await asyncio.to_thread(
        svc.list_versions, agent_id, version_tag, artifact, limit=limit, offset=offset
    )
    # The repo's list_versions returns all-state rows; filter in
    # the router (the repo doesn't carry a state filter — Phase A's
    # A2 surface).
    if state is not None:
        items = [r for r in items if getattr(r, "state", None) == state]
    return _VersionListResponse(items=[_version_to_response(r) for r in items], total=len(items))


# 2) GET /api/prompts/versions/{id}
@router.get("/versions/{version_id}", response_model=_VersionResponse)
@require_scope(_SCOPE_READ)
async def get_version(
    version_id: str = Path(...),
    request: Request = ...,
) -> _VersionResponse:
    _require_auth_on(request)
    _require_uuid_like(version_id)
    svc = _get_service(request)
    row = await asyncio.to_thread(svc.get_version, version_id)
    if row is None:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    return _version_to_response(row)


# 3) GET /api/prompts/versions/{id}/diff
@router.get("/versions/{version_id}/diff", response_model=_DiffResponse)
@require_scope(_SCOPE_READ)
async def diff_version(
    request: Request,
    version_id: str = Path(...),
    against: str = Query(default="effective"),
) -> _DiffResponse:
    """``?against=effective|approved|<version_id>`` — D12.

    Default ``against=effective`` (the version currently in effect);
    ``approved`` means the latest-approved row for the key; an
    explicit ``version_id`` targets a specific row.
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    svc = _get_service(request)
    row = await asyncio.to_thread(svc.get_version, version_id)
    if row is None:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    # Resolve the comparison content (against).
    if against == "effective":
        against_content = await asyncio.to_thread(
            svc.get_effective, row.agent_id, row.version_tag, row.artifact
        )
        against_label = "effective"
        against_version_id = None
    elif against == "approved":
        latest = await asyncio.to_thread(
            svc.get_latest_approved, row.agent_id, row.version_tag, row.artifact
        )
        if latest is None:
            raise _err(404, _ERR_VERSION_NOT_FOUND, "No approved version for key")
        against_content = latest.content
        against_label = f"approved/{latest.version_id}"
        against_version_id = latest.version_id
    else:
        # explicit version_id — also acts as the path-param allowlist
        _require_uuid_like(against)
        target = await asyncio.to_thread(svc.get_version, against)
        if target is None:
            raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {against}")
        against_content = target.content
        against_label = target.version_id
        against_version_id = target.version_id
    diff = _unified_diff(
        against_content or "", row.content, against_label, f"row/{row.version_id}"
    )
    return _DiffResponse(
        version_id=row.version_id,
        against=against,
        against_version_id=against_version_id,
        diff=diff,
    )


# 4) POST /api/prompts/versions/{id}/approve — HUMAN lane only
@router.post("/versions/{version_id}/approve", response_model=_VersionResponse)
@require_scope(_SCOPE_APPROVE)
async def approve_version(
    version_id: str = Path(...),
    request: Request = ...,
) -> _VersionResponse:
    """Approve a pending version. HUMAN lane only (D2).

    Bearer + scope still 403 ``HUMAN_LANE_REQUIRED`` (D2 / R3).
    Session lane with ``prompts:approve`` scope passes the gate.
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        row = await asyncio.to_thread(svc.approve, version_id, actor)
    except _HumanLaneRequiredError:
        raise _err(403, _ERR_HUMAN_LANE_REQUIRED, "Approve requires session lane (D2)")
    except _InvalidTransitionError as exc:
        # The repo's atomic_transition surfaces "not in
        # VALID_TRANSITIONS" — map the common shapes (already
        # approved / already rejected / wrong state) onto typed
        # 409 codes so the SPA can render targeted messages.
        cur = getattr(exc, "current_state", None) or getattr(exc, "from_state", "")
        if cur == "approved":
            raise _err(409, _ERR_ALREADY_APPROVED, "Version already approved")
        if cur == "rejected":
            raise _err(409, _ERR_ALREADY_REJECTED, "Version already rejected")
        raise _err(409, _ERR_INVALID_TRANSITION, str(exc))
    except LookupError:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    return _version_to_response(row)


# 5) POST /api/prompts/versions/{id}/reject — HUMAN lane only
@router.post("/versions/{version_id}/reject", response_model=_VersionResponse)
@require_scope(_SCOPE_APPROVE)
async def reject_version(
    version_id: str = Path(...),
    request: Request = ...,
) -> _VersionResponse:
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        row = await asyncio.to_thread(svc.reject, version_id, actor)
    except _HumanLaneRequiredError:
        raise _err(403, _ERR_HUMAN_LANE_REQUIRED, "Reject requires session lane (D2)")
    except _InvalidTransitionError as exc:
        cur = getattr(exc, "current_state", None) or getattr(exc, "from_state", "")
        if cur == "approved":
            raise _err(409, _ERR_ALREADY_APPROVED, "Version already approved")
        if cur == "rejected":
            raise _err(409, _ERR_ALREADY_REJECTED, "Version already rejected")
        raise _err(409, _ERR_INVALID_TRANSITION, str(exc))
    except LookupError:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    return _version_to_response(row)


# 6) POST /api/prompts/versions/{id}/submit — machine lane OK
@router.post("/versions/{version_id}/submit", response_model=_VersionResponse)
@require_scope(_SCOPE_SUBMIT)
async def submit_version(
    version_id: str = Path(...),
    request: Request = ...,
) -> _VersionResponse:
    """Submit a draft for approval. Bearer + ``prompts:submit`` OK.

    The Phase A repo's ``submit_for_approval`` is the only path that
    transitions ``draft → pending_approval``; the router does no
    extra guard (the lane rule is read+approve + reject; submit is
    the agent-write leg).
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        row = await asyncio.to_thread(svc.submit_for_approval, version_id, actor)
    except _InvalidTransitionError as exc:
        raise _err(409, _ERR_INVALID_TRANSITION, str(exc))
    except LookupError:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    return _version_to_response(row)


# 7) POST /api/prompts/versions/{id}/retract — machine lane OK
@router.post("/versions/{version_id}/retract", response_model=_VersionResponse)
@require_scope(_SCOPE_SUBMIT)
async def retract_version(
    version_id: str = Path(...),
    request: Request = ...,
) -> _VersionResponse:
    """Retract a pending or draft version. Authority (S16) is
    enforced SERVICE-side: a submitter may retract its OWN row; a
    human session may retract any row. The router only maps the
    service's authority errors to 403."""
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        row = await asyncio.to_thread(svc.atomic_transition, version_id, "pending_approval", "draft", actor)
    except _InvalidTransitionError as exc:
        # A retract on a draft must use a different path — service
        # chooses (S16): for simplicity here, treat a draft-row
        # retract as archive via the same path; map both 409s.
        raise _err(409, _ERR_INVALID_TRANSITION, str(exc))
    except LookupError:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    except _RetractAuthorityError:
        raise _err(403, "RETRACT_FORBIDDEN", "Retract authority denied (S16)")
    return _version_to_response(row)


# 8) POST /api/prompts/versions — create-draft
@router.post("/versions", response_model=_VersionResponse, status_code=201)
@require_scope(_SCOPE_WRITE)
async def create_version(
    body: _CreateDraftBody, request: Request
) -> _VersionResponse:
    """Create a new draft. Phase B T1 surfaces ``create_draft``.

    Body MUST carry the (agent_id, version_tag, artifact, content)
    tuple. ``version_tag`` defaults to ``""`` (the base agent).
    """
    _require_auth_on(request)
    _require_path_param(body.agent_id, "agent_id")
    _require_path_param(body.version_tag, "version_tag")
    _require_path_param(body.artifact, "artifact")
    actor = actor_from_request(request)
    svc = _get_service(request)
    row = await asyncio.to_thread(
        svc.create_draft,
        body.agent_id,
        body.version_tag,
        body.artifact,
        body.content,
        actor,
    )
    return _version_to_response(row)


# 9) POST /api/prompts/selections — set selection pointer
@router.post("/selections", response_model=_SelectionResponse)
@require_scope(_SCOPE_WRITE)
async def set_selection(
    body: _SelectionBody, request: Request
) -> _SelectionResponse:
    """Set the per-key one-selection-pointer (D13 / F5 / R3).

    The target version MUST be in state ``approved`` — service-side
    check; a non-approved row surfaces as 409 INVALID_TRANSITION
    via the repo's atomic_transition CAS error.
    """
    _require_auth_on(request)
    _require_path_param(body.agent_id, "agent_id")
    _require_path_param(body.version_tag, "version_tag")
    _require_path_param(body.artifact, "artifact")
    _require_uuid_like(body.version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        await asyncio.to_thread(
            svc.set_selection,
            body.agent_id,
            body.version_tag,
            body.artifact,
            body.version_id,
            actor,
        )
    except LookupError:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {body.version_id}")
    except _InvalidTransitionError as exc:
        raise _err(409, _ERR_INVALID_TRANSITION, str(exc))
    return _SelectionResponse(
        agent_id=body.agent_id,
        version_tag=body.version_tag,
        artifact=body.artifact,
        version_id=body.version_id,
    )


# 10) DELETE /api/prompts/selections/{agent_id}/{version_tag}/{artifact}
@router.delete("/selections/{agent_id}/{version_tag}/{artifact}")
@require_scope(_SCOPE_WRITE)
async def clear_selection(
    agent_id: str = Path(...),
    version_tag: str = Path(...),
    artifact: str = Path(...),
    request: Request = ...,
) -> _SelectionResponse:
    """Clear the per-key one-selection-pointer."""
    _require_auth_on(request)
    _require_path_param(agent_id, "agent_id")
    _require_path_param(version_tag, "version_tag")
    _require_path_param(artifact, "artifact")
    actor = actor_from_request(request)
    svc = _get_service(request)
    await asyncio.to_thread(
        svc.clear_selection, agent_id, version_tag, artifact, actor
    )
    return _SelectionResponse(
        agent_id=agent_id, version_tag=version_tag, artifact=artifact, version_id=None
    )


# 11) GET /api/prompts/effective/{agent_id}/{version_tag}/{artifact}
@router.get("/effective/{agent_id}/{version_tag}/{artifact}", response_model=_EffectiveResponse)
@require_scope(_SCOPE_READ)
async def get_effective(
    agent_id: str = Path(...),
    version_tag: str = Path(...),
    artifact: str = Path(...),
    request: Request = ...,
) -> _EffectiveResponse:
    """Return the effective prompt content (selected > latest_approved > null).

    The fallback to the template file is the loader hook's job, NOT
    this endpoint — the loader consults the file when this returns
    ``content=null``. The router's contract is the DB-effective view.
    """
    _require_auth_on(request)
    _require_path_param(agent_id, "agent_id")
    _require_path_param(version_tag, "version_tag")
    _require_path_param(artifact, "artifact")
    svc = _get_service(request)
    content = await asyncio.to_thread(
        svc.get_effective, agent_id, version_tag, artifact
    )
    return _EffectiveResponse(
        agent_id=agent_id, version_tag=version_tag, artifact=artifact, content=content
    )


# 12) GET /api/prompts/audit — audit log read
@router.get("/audit", response_model=_AuditListResponse)
@require_scope(_SCOPE_AUDIT)
async def list_audit(
    request: Request,
    agent_id: Optional[str] = Query(default=None),
    version_tag: Optional[str] = Query(default=None),
    artifact: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> _AuditListResponse:
    """Audit log read with filters + pagination.

    Phase A's service facade does NOT carry a list_audit method —
    adding one is Phase D / follow-up (out of scope per
    phaseB-plan.md §OOS). The endpoint keeps the
    ``{items, total}`` envelope so the SPA can render a target-zero
    state until the follow-up lands. The router never 500s.
    """
    _require_auth_on(request)
    if agent_id is not None:
        _require_path_param(agent_id, "agent_id")
    if version_tag is not None:
        _require_path_param(version_tag, "version_tag")
    if artifact is not None:
        _require_path_param(artifact, "artifact")
    if event_type is not None:
        # Audit event types are UPPERCASE per the addendum S3/O-12;
        # the path allowlist is the conservative fence.
        _require_path_param(event_type, "event_type")
    # No list_audit on the Phase A service facade yet — return an
    # empty envelope (target-zero). The endpoint is registered so
    # the SPA can show "no events yet" rather than a 404.
    return _AuditListResponse(items=[], total=0)


# ────────────────────────────────────────────────────────────────────────────
# Defensive exception aliases
#
# The router is the contract surface between the HTTP layer and the
# service / repo. The Phase B T3 service raises
# ``HumanLaneRequiredError`` and ``RetractAuthorityError``; the repo
# raises ``InvalidTransitionError`` and ``LookupError``. To avoid a
# hard import (the sibling coder adds these service-side classes as
# PART of their T1-T3 work — we MUST NOT block on them), reference
# them by name and resolve at first call. If the service raises
# something else, the ``except _HumanLaneRequiredError`` etc. blocks
# will fall through (NameError) and the FASTAPI default exception
# handler emits a 500 — which is the CORRECT pre-merge posture
# (surfaces the wiring gap loudly, never silently mishandles it).
#
# We resolve the names lazily in each handler via a tiny shim that
# returns ``None`` when the class isn't yet importable, then the
# except block re-raises the original exception to the FASTAPI
# handler. This keeps the router importable today even if the
# sibling coder hasn't shipped their T3 yet.
# ────────────────────────────────────────────────────────────────────────────


def _resolve_exception(name: str) -> type | None:
    """Best-effort lookup of service-side exception classes.

    Returns ``None`` when the class isn't importable yet — the
    router's ``except`` blocks then no-op (the original exception
    propagates to the default handler). Returns the class when it
    IS importable, so the ``except`` block can map to a typed 4xx.
    """
    # The repo is the only file we can safely import (Phase B T5
    # boundary — sibling coders own daemon/services/*; we must not
    # import the service module directly to avoid boot-order cycles).
    try:
        from daemon.repositories.prompt_version.repository import InvalidTransitionError as _ITRE  # noqa: E501

        if name == "InvalidTransitionError":
            return _ITRE
    except Exception:  # noqa: BLE001
        return None
    try:
        # Service-side classes — optional import. If the sibling
        # coder has shipped them, we resolve; if not, we no-op.
        from daemon.services.prompt_version_service import (
            HumanLaneRequiredError as _HLRE,
            RetractAuthorityError as _RAE,
        )

        if name == "HumanLaneRequiredError":
            return _HLRE
        if name == "RetractAuthorityError":
            return _RAE
    except Exception:  # noqa: BLE001
        return None
    return None


# Lazily-resolved exception classes (refreshed at call time so a
# sibling coder's mid-session edit becomes effective without a router
# restart). Module-level aliases:
_HumanLaneRequiredError = _resolve_exception("HumanLaneRequiredError") or Exception
_InvalidTransitionError = _resolve_exception("InvalidTransitionError") or Exception
_RetractAuthorityError = _resolve_exception("RetractAuthorityError") or Exception


__all__ = ["router"]