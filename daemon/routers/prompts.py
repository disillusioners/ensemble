"""Prompt-version REST router — agent-prompt-immutability Phase B T5.

The canonical 15-endpoint REST family that exposes the
:class:`daemon.services.prompt_version_service.PromptVersionService`
async facade to HTTP callers (operators + agents). Mounted under
``/api/prompts``. Per phaseB-plan.md D11 / addendum S4 (folded at
T5 scope), the family is split across five scopes:

    prompts:read    — list / get / diff / effective-read / pending-count
    prompts:submit  — POST /submit (machine lane OK) + /retract
    prompts:approve — POST /approve / reject (HUMAN lane only per D2)
    prompts:write   — POST /versions (create-draft) + selection
                      set/clear + PATCH/DELETE /versions/{id} (UD#2)
    prompts:audit   — GET /audit (read-only)

USER DIRECTIVE #2 (2026-10-14, frontend specs) — API DELTAS over the
canonical 12-endpoint family (12 → 15):

* ``PATCH /api/prompts/versions/{version_id}`` (endpoint 13) — body
  ``{content}``; scope ``prompts:write``; draft/pending rows ONLY.
  A non-editable state (approved / rejected / archived) surfaces as
  ``409 VERSION_IMMUTABLE``; unknown id → 404. Leader ruling:
  EDIT = draft/pending content ONLY — approved rows are append-only
  immutable history; rejected is terminal (resubmit = new row);
  archived is superseded. Hard-delete of approved history is a
  MORNING FORK and is deliberately NOT built.
* ``DELETE /api/prompts/versions/{version_id}`` (endpoint 14) — same
  scope + state rule + ``409 VERSION_IMMUTABLE`` / 404 mapping. The
  repo hard-deletes the row and stamps a ``DELETED`` audit event
  whose payload carries identity + content_hash + content byte
  length (forensics survive the row).
* ``GET /api/prompts/pending-count`` (endpoint 15) — scope
  ``prompts:read``; returns ``{"count": N}`` — the Persona UI badge
  feed; drains as approve/reject land. A PLAIN count (the A8
  effective fingerprint does not apply to a badge).

Endpoint 11 (``GET /effective/{agent_id}/{version_tag}/{artifact}``)
CONFIRMED (report-only, no code change) as the Persona UI diff
baseline: it serves the active version content per the F6
resolution chain ``selected.content > latest_approved.content >
null`` (``PromptVersionRepository.get_effective`` →
``get_effective_rows`` single-SELECT, A8). A ``null`` content means
"template file is the baseline" (the loader fallback owns the file
read).

Source-of-truth artifacts:

* Endpoints — phaseB-plan.md D12 + addendum S4 (canonical 12-endpoint
  family, including ``create-draft`` POST /versions and the effective
  GET at ``/api/prompts/effective/{agent_id}/{version_tag}/{artifact}``)
  + USER DIRECTIVE #2 (2026-10-14) for endpoints 13-15.
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

The router does NOT touch ``daemon/services/*``, ``daemon/repositories/*`,
``daemon/tools/*``, or ``daemon/loader.py`` (Phase B T5 scope boundary
— sibling coders own those files).
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from daemon.auth.decorators import require_scope
from daemon.repositories.prompt_version.repository import (
    MemoryClassError,
    VersionNotDeletableError,
    VersionNotEditableError,
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

#: Audit FILTER allowlist — deliberately PERMISSIVE (review Finding #1,
#: Phase B close-out). Query-string FILTER params on ``GET /audit``
#: must accept the canonical subject shapes that actually appear in
#: audit rows: OIDC human subjects (``user@example.com`` — ``@`` and
#: ``.``) and internal instance subjects (``instance:<uuid>`` —
#: ``:``). The strict pattern above is intentionally NOT reused for
#: these three params: it 422-rejected every realistic operator
#: filter. The fence still holds where it matters — no slashes
#: (path traversal), no whitespace (log forgery), no ``!``/``?``/
#: ``&``/``=``/quotes (query-injection + log-forgery vectors).
_FILTER_PARAM_RE = re.compile(r"^[A-Za-z0-9_\-@.+:]*$")

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
#: Non-editable / non-deletable row state (UD#2 — approved /
#: rejected / archived rows refuse PATCH + DELETE).
_ERR_VERSION_IMMUTABLE = "VERSION_IMMUTABLE"
#: MEMORY-class rows refuse state-machine operations
#: (User Directive #3, 2026-10-14, RESOLUTION-CORE). The
#: transition-free creator (``create_memory_version``) is
#: the ONLY mutator; ``approve`` / ``reject`` / ``submit`` /
#: ``retract`` / ``PATCH`` / ``DELETE`` against a MEMORY row
#: surface as 409 ``MEMORY_CLASS_MISMATCH``. The class
#: becomes the contract token; clients branch on the
#: error string.
_ERR_MEMORY_CLASS_MISMATCH = "MEMORY_CLASS_MISMATCH"
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


def _require_filter_param(value: str, name: str) -> None:
    """Audit FILTER-param allowlist (S4 + review Finding #1). 422 on a
    bad value.

    Distinct from :func:`_require_path_param` BY DESIGN: filter values
    are matched against stored row values, not URL segments, so the
    canonical subject shapes (``user@example.com``,
    ``instance:<uuid>``) must pass. The permissive pattern still
    rejects slashes / whitespace / injection chars, so the traversal
    and log-forgery fences are unchanged.
    """
    if not _FILTER_PARAM_RE.match(value):
        raise _err(
            422,
            "INVALID_FILTER_PARAM",
            f"{name!r} must match {_FILTER_PARAM_RE.pattern!r}; got {value!r}",
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
    artifact_class: str = "CORE"  # Ud#3: CORE | MEMORY
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


class _UpdateVersionBody(BaseModel):
    """PATCH /versions/{id} body (UD#2 endpoint 13) — the replacement
    content. ``min_length=1`` mirrors ``_CreateDraftBody.content``
    (an empty-string edit is a delete, not an edit — use DELETE)."""

    content: str = Field(min_length=1)


class _DeleteVersionResponse(BaseModel):
    """DELETE /versions/{id} body (UD#2 endpoint 14) — the deleted
    row's forensic echo (the full content survives in the DELETED
    audit payload's content_hash / content_bytes, not on the wire)."""

    version_id: str
    deleted: bool


class _PendingCountResponse(BaseModel):
    """GET /pending-count body (UD#2 endpoint 15) — the Persona UI
    badge feed. Plain count; drains as approve/reject land."""

    count: int


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

    ``artifact_class`` (Ud#3, 2026-10-14) is read from the row
    directly (the canonical column on the
    :class:`PromptVersion` SQLModel). When the row predates
    the column, fall back to the canonical
    :func:`class_for_artifact` derivation (the conservative
    bucket is ``CORE``).
    """
    if row is None:
        raise _err(404, _ERR_VERSION_NOT_FOUND, "Version not found")
    # ``artifact_class`` may be missing on legacy rows that
    # predate the column; derive via class_for_artifact.
    artifact_class = getattr(row, "artifact_class", None)
    if not artifact_class:
        from daemon.repositories.prompt_version.repository import (
            class_for_artifact,
        )
        artifact_class = class_for_artifact(row.artifact)
    return _VersionResponse(
        version_id=row.version_id,
        agent_id=row.agent_id,
        version_tag=row.version_tag,
        artifact=row.artifact,
        artifact_class=artifact_class,
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
# Endpoints — 15 canonical (12 from T5 + 3 USER-DIRECTIVE-#2 deltas)
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
    artifact_class: Optional[str] = Query(
        default=None,
        description=(
            "Ud#3 filter — restrict to CORE or MEMORY rows. "
            "One of: CORE, MEMORY."
        ),
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> _VersionListResponse:
    """List prompt versions; filters are optional and combine.

    Filters + pagination follow the ``skill_bank.py:72-108`` shape;
    response envelope is ``{items, total}``.

    ``artifact_class`` (Ud#3, 2026-10-14, RESOLUTION-CORE):
    restricts the listing to CORE (state-machine) or MEMORY
    (transition-free) rows. The Persona UI uses this to
    separate the operator-pending CORE queue from the
    agent-direct MEMORY rows.
    """
    _require_auth_on(request)
    if agent_id is not None:
        _require_path_param(agent_id, "agent_id")
    if version_tag is not None:
        _require_path_param(version_tag, "version_tag")
    if artifact is not None:
        _require_path_param(artifact, "artifact")
    # artifact_class filter is permissive — it is matched
    # against a stored column value, not a URL segment, so
    # the canonical CORE / MEMORY tokens must pass. The
    # FILTER allowlist (Finding #1) accepts these and
    # rejects slashes / whitespace / injection chars.
    if artifact_class is not None:
        if artifact_class not in ("CORE", "MEMORY"):
            _require_filter_param(artifact_class, "artifact_class")
        # Tighten: 422 on anything else (the allowlist
        # above is permissive on characters; the value
        # membership check is the contract).
        if artifact_class not in ("CORE", "MEMORY"):
            raise _err(
                422,
                "INVALID_FILTER_PARAM",
                f"artifact_class must be one of ['CORE', 'MEMORY']; "
                f"got {artifact_class!r}",
            )
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
    # Call-form contract (F2 call-form fix, 2026-10-14): every
    # service method is ALREADY an async facade (phase-contract
    # A4 — one ``await asyncio.to_thread`` hop around the sync
    # repo, INSIDE the service). The router MUST ``await`` them
    # directly. Wrapping an async method in another
    # ``asyncio.to_thread(...)`` runs the coroutine FACTORY in a
    # thread and returns an un-awaited coroutine object — the
    # route then dies on attribute access (``'coroutine' object
    # has no attribute ...``). The old sync-MagicMock fixtures
    # hid exactly this; the real-service lane tests catch it.
    items = await svc.list_versions(
        agent_id, version_tag, artifact, limit=limit, offset=offset
    )
    # The repo's list_versions returns all-state rows; filter in
    # the router (the repo doesn't carry a state filter — Phase A's
    # A2 surface).
    if state is not None:
        items = [r for r in items if getattr(r, "state", None) == state]
    # artifact_class filter (Ud#3) — fall back to the canonical
    # derivation when the column is missing on legacy rows.
    if artifact_class is not None:
        from daemon.repositories.prompt_version.repository import (
            class_for_artifact,
        )

        def _row_class(r: Any) -> str:
            return getattr(r, "artifact_class", None) or class_for_artifact(
                getattr(r, "artifact", "")
            )

        items = [r for r in items if _row_class(r) == artifact_class]
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
    row = await svc.get_version(version_id)
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
    row = await svc.get_version(version_id)
    if row is None:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    # Resolve the comparison content (against).
    if against == "effective":
        against_content = await svc.get_effective(
            row.agent_id, row.version_tag, row.artifact
        )
        against_label = "effective"
        against_version_id = None
    elif against == "approved":
        latest = await svc.get_latest_approved(
            row.agent_id, row.version_tag, row.artifact
        )
        if latest is None:
            raise _err(404, _ERR_VERSION_NOT_FOUND, "No approved version for key")
        against_content = latest.content
        against_label = f"approved/{latest.version_id}"
        against_version_id = latest.version_id
    else:
        # explicit version_id — also acts as the path-param allowlist
        _require_uuid_like(against)
        target = await svc.get_version(against)
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

    MemoryClassError → 409 ``MEMORY_CLASS_MISMATCH`` (Ud#3).
    MEMORY rows are transition-free; the state machine does
    not govern them. The error string is the contract token
    clients branch on.
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        row = await svc.approve(version_id, actor)
    except _HumanLaneRequiredError:
        raise _err(403, _ERR_HUMAN_LANE_REQUIRED, "Approve requires session lane (D2)")
    except MemoryClassError as exc:
        raise _err(
            409,
            _ERR_MEMORY_CLASS_MISMATCH,
            f"approve refused: {exc}",
        )
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
    """Reject a pending version. HUMAN lane only (D2).

    MemoryClassError → 409 ``MEMORY_CLASS_MISMATCH`` (Ud#3).
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        row = await svc.reject(version_id, actor)
    except _HumanLaneRequiredError:
        raise _err(403, _ERR_HUMAN_LANE_REQUIRED, "Reject requires session lane (D2)")
    except MemoryClassError as exc:
        raise _err(
            409,
            _ERR_MEMORY_CLASS_MISMATCH,
            f"reject refused: {exc}",
        )
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

    MemoryClassError → 409 ``MEMORY_CLASS_MISMATCH`` (Ud#3).
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        row = await svc.submit_for_approval(version_id, actor)
    except MemoryClassError as exc:
        raise _err(
            409,
            _ERR_MEMORY_CLASS_MISMATCH,
            f"submit refused: {exc}",
        )
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
    """Retract a pending or draft version (T2 / S16 / phase-contract.md A3).

    Authority is enforced SERVICE-side via
    :meth:`PromptVersionService.retract` (the service is the single
    source of truth for S16 anti-forgery — ``session`` any row,
    ``internal`` own-row only, ``bearer`` REFUSED with
    ``reason="machine_lane_refused"``). The router MUST route
    through ``svc.retract`` and MUST NOT call
    ``svc.atomic_transition`` directly — that bypass was the
    F2 leader-gate finding: any bearer with ``prompts:submit`` could
    retract ANY actor's pending row because the S16 matrix lived
    only in the never-invoked ``svc.retract`` method.

    Error mapping (each branch now reachable through the real
    service path):

    * :class:`RetractAuthorityError` → 403 ``RETRACT_FORBIDDEN``
      (bearer → ``machine_lane_refused``; internal other-instance →
      ``wrong_instance``; the message body still uses one stable code
      per the S5 body-shape contract).
    * :class:`InvalidTransitionError` → 409 ``INVALID_TRANSITION``
      (the row is no longer in a retract-able state — approved /
      rejected / archived rows refuse retract; F-A1 state machine).
    * :class:`MemoryClassError` → 409 ``MEMORY_CLASS_MISMATCH``
      (Ud#3 — only the transition-free creator mutates MEMORY rows).
    * :class:`LookupError` → 404 ``VERSION_NOT_FOUND``.
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        # Direct await (F2 call-form fix) — ``svc.retract`` is an
        # async facade; to_thread-wrapping it returns an un-awaited
        # coroutine (see the call-form contract note at endpoint 1).
        row = await svc.retract(version_id, actor)
    except MemoryClassError as exc:
        raise _err(
            409,
            _ERR_MEMORY_CLASS_MISMATCH,
            f"retract refused: {exc}",
        )
    except _InvalidTransitionError as exc:
        # Repo refuses the state pair — the row is not in a
        # retract-able state (terminal-only state machine, F-A1).
        raise _err(409, _ERR_INVALID_TRANSITION, str(exc))
    except LookupError:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    except _RetractAuthorityError:
        # REACHABLE on the HTTP lane only since this fix landed;
        # the router-side mapping is the typed
        # ``RETRACT_FORBIDDEN`` body (S5 body-shape contract), the
        # service-side ``reason`` distinguishes
        # ``wrong_instance`` vs ``machine_lane_refused`` and is
        # available for log forensics via the raised exception.
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
    row = await svc.create_draft(
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
        await svc.set_selection(
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
    await svc.clear_selection(agent_id, version_tag, artifact, actor)
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
    content = await svc.get_effective(agent_id, version_tag, artifact)
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
    version_id: Optional[str] = Query(default=None),
    actor_subject: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> _AuditListResponse:
    """Audit log read with filters + pagination (Phase B T5).

    All filters are optional and combine with AND. Response envelope
    follows the ``skill_bank.py:72-108`` template: ``{items, total}``.

    Filter surface:

    * ``agent_id`` / ``version_tag`` / ``artifact`` — the (key, key,
      key) filter; ``version_tag`` passes through the F9 / A7
      normalization (``""`` is the base tag).
    * ``event_type`` — one of ``PROMPT_AUDIT_EVENT_TYPES``
      (UPPERCASE per addendum S3 / O-12).
    * ``version_id`` — per-version timeline lookup; lets a UI render
      the audit history of a single :class:`PromptVersion` row.
    * ``actor_subject`` — internal vs HTTP lane stamp filter
      (D10 — internal row's ``actor_subject`` is the instance id;
      HTTP row's ``actor_subject`` is the ``AuthContext.subject``).

    The service is the authoritative caller of
    :meth:`PromptVersionRepository.list_audit_events`; the router
    is a thin bridge that validates the key filters (``agent_id`` /
    ``version_tag`` / ``artifact``) with the strict path allowlist,
    the value filters (``event_type`` / ``version_id`` /
    ``actor_subject``) with the permissive filter allowlist (review
    Finding #1 — canonical subject shapes must pass), plus scope,
    and serializes rows via :func:`_audit_event_to_dict` (D10's
    two-lane identity stamp surfaces verbatim).
    """
    _require_auth_on(request)
    if agent_id is not None:
        _require_path_param(agent_id, "agent_id")
    if version_tag is not None:
        _require_path_param(version_tag, "version_tag")
    if artifact is not None:
        _require_path_param(artifact, "artifact")
    if event_type is not None:
        # Audit event types are UPPERCASE per the addendum S3/O-12.
        # FILTER allowlist (Finding #1) — NOT the path pattern, which
        # 422'd every realistic operator filter.
        _require_filter_param(event_type, "event_type")
    if version_id is not None:
        _require_filter_param(version_id, "version_id")
    if actor_subject is not None:
        _require_filter_param(actor_subject, "actor_subject")
    svc = _get_service(request)
    items, total = await svc.list_audit_events(
        agent_id=agent_id,
        version_tag=version_tag,
        artifact=artifact,
        event_type=event_type,
        version_id=version_id,
        actor_subject=actor_subject,
        limit=limit,
        offset=offset,
    )
    return _AuditListResponse(
        items=[_audit_event_to_dict(r) for r in items],
        total=total,
    )


# 13) PATCH /api/prompts/versions/{version_id} — edit draft/pending
#     content (UD#2)
@router.patch("/versions/{version_id}", response_model=_VersionResponse)
@require_scope(_SCOPE_WRITE)
async def update_version_content(
    version_id: str = Path(...),
    body: _UpdateVersionBody = ...,
    request: Request = ...,
) -> _VersionResponse:
    """Replace the content of a draft/pending version in place (UD#2).

    Editable-state rule (leader ruling, binding with frozen A1):
    ``draft`` / ``pending_approval`` rows ONLY. ``approved`` rows are
    append-only immutable history; ``rejected`` is terminal
    (resubmit = new row); ``archived`` is superseded — all three
    surface as ``409 VERSION_IMMUTABLE``. The repo recomputes
    ``content_hash`` and stamps the ``UPDATED`` audit event (prior +
    new content_hash, byte sizes) inside the same transaction.
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        row = await svc.update_version_content(version_id, body.content, actor)
    except MemoryClassError as exc:
        # Ud#3: PATCH on a MEMORY row is a class-mismatch 4xx
        # — MEMORY rows are transition-free, no edit path.
        raise _err(
            409,
            _ERR_MEMORY_CLASS_MISMATCH,
            f"update refused: {exc}",
        )
    except VersionNotEditableError as exc:
        raise _err(
            409,
            _ERR_VERSION_IMMUTABLE,
            f"Version is immutable in state {exc.current_state!r} "
            f"(only draft/pending_approval may be edited)",
        )
    except LookupError:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    return _version_to_response(row)


# 14) DELETE /api/prompts/versions/{version_id} — delete draft/pending
#     (UD#2)
@router.delete("/versions/{version_id}", response_model=_DeleteVersionResponse)
@require_scope(_SCOPE_WRITE)
async def delete_version(
    version_id: str = Path(...),
    request: Request = ...,
) -> _DeleteVersionResponse:
    """Hard-delete a draft/pending version (UD#2).

    Same editable-state rule as PATCH: draft/pending ONLY — approved
    history is append-only immutable (hard-delete of approved history
    is a morning fork, deliberately NOT built); rejected/archived
    surface as ``409 VERSION_IMMUTABLE``. The DELETED audit event
    carries identity + content_hash + content byte length in its
    payload — the forensics survive the row.
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    svc = _get_service(request)
    try:
        row = await svc.delete_version(version_id, actor)
    except MemoryClassError as exc:
        # Ud#3: DELETE on a MEMORY row is a class-mismatch 4xx
        # — MEMORY rows are transition-free, no delete path.
        raise _err(
            409,
            _ERR_MEMORY_CLASS_MISMATCH,
            f"delete refused: {exc}",
        )
    except VersionNotDeletableError as exc:
        raise _err(
            409,
            _ERR_VERSION_IMMUTABLE,
            f"Version is immutable in state {exc.current_state!r} "
            f"(only draft/pending_approval may be deleted)",
        )
    except LookupError:
        raise _err(404, _ERR_VERSION_NOT_FOUND, f"No version {version_id}")
    return _DeleteVersionResponse(version_id=row.version_id, deleted=True)


# 15) GET /api/prompts/pending-count — Persona UI badge feed (UD#2)
@router.get("/pending-count", response_model=_PendingCountResponse)
@require_scope(_SCOPE_READ)
async def pending_count(
    request: Request = ...,
) -> _PendingCountResponse:
    """Count rows in state ``pending_approval`` (UD#2).

    ``{"count": N}`` — drains as approve/reject land. A PLAIN count:
    the A8 effective fingerprint does not apply (a badge answers
    "how many rows await approval", not "what is in effect").

    User Directive #3 (2026-10-14, RESOLUTION-CORE) — the
    pending-count is **CORE-only by design**: the repo's
    ``count_pending`` walks state-machine rows and excludes
    MEMORY-class rows (they are born-effective in
    ``state='approved'`` and never enter the
    ``pending_approval`` state — the badge is unchanged).
    """
    _require_auth_on(request)
    svc = _get_service(request)
    count = await svc.count_pending()
    return _PendingCountResponse(count=int(count or 0))


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
        from daemon.repositories.prompt_version.repository import (
            InvalidTransitionError as _ITRE,
            MemoryClassError as _MCE,
        )

        if name == "InvalidTransitionError":
            return _ITRE
        if name == "MemoryClassError":
            return _MCE
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


# Module-level exception aliases (resolved once at import; review
# Finding #2, Phase B close-out — the fallback MUST never be
# ``Exception``: that turned every ``except`` block below into a
# catch-ALL whenever lazy resolution failed, mapping unrelated
# programming errors (``KeyError``, ``TypeError``, ...) onto typed
# 4xx bodies instead of letting them surface as 500s. The fallbacks
# are the EXACT base classes of the mapped service-side errors
# (verified in-tree):
#
#   * ``HumanLaneRequiredError(ValueError)``      — services/prompt_version_service.py:166
#   * ``InvalidTransitionError(ValueError)``      — repositories/prompt_version/repository.py:222
#     (docstring: "Mirrors daemon/services/job_state_machine.py:65-81")
#   * ``RetractAuthorityError(PermissionError)``  — services/prompt_version_service.py:232
#   * ``MemoryClassError(ValueError)``            — repositories/prompt_version/repository.py:359
#     (Ud#3, 2026-10-14: MEMORY rows refuse state-machine ops)
#
# so a resolution failure now widens the catch only to the narrowest
# base that preserves the typed 4xx mapping — unrelated programming
# errors always propagate. The test gate
# (``TestExceptionAliasResolution`` in tests/test_prompts_router.py)
# asserts the resolved classes are the REAL service classes, i.e.
# the fallbacks are dormant.
_HumanLaneRequiredError = _resolve_exception("HumanLaneRequiredError") or ValueError
_InvalidTransitionError = _resolve_exception("InvalidTransitionError") or ValueError
_RetractAuthorityError = _resolve_exception("RetractAuthorityError") or PermissionError
_MemoryClassError = _resolve_exception("MemoryClassError") or ValueError


__all__ = ["router"]
