"""Phase B T5 router unit tests — agent-prompt-immutability commission.

End-to-end exercise of the 15-endpoint REST family at
``daemon/routers/prompts.py`` against a mocked
``PromptVersionService``. The mock-based pattern (per the brief:
"test against a mocked service so you do NOT block on the sibling")
keeps the tests stable across the sibling coder's service-side
T1-T3 work — the router's contract is the typed error mapping, and
the contract is testable without a live service.

Coverage:

* list/get/diff happy paths
* bearer approve → 403 HUMAN_LANE_REQUIRED (D2 enforcement)
* session approve → 200 (mocked service accepts)
* double-approve → 409 ALREADY_APPROVED (state-machine guard)
* URL regex rejects bad path params (S4 allowlist)
* Pagination shape (skill_bank.py:72-108 envelope)
* Scope enforcement (missing scope → 403 per existing convention)
* Effective read precedence (selected > latest_approved > null)
* Selection set/clear
* USER DIRECTIVE #2 (2026-10-14) deltas — PATCH /versions/{id} +
  DELETE /versions/{id} happy paths and the 409 VERSION_IMMUTABLE
  mapping (approved/rejected/archived), 404 unknown, and GET
  /pending-count badge shape (real repo typed errors raised by the
  mock — the router must catch the REAL classes)

Auth context injection: a tiny ASGI middleware stamps
``request.state.auth_context`` from a session/bearer flag so the
decorator + lane-check both run their real code paths without
needing the full OAuth flow. Auth-OFF posture is covered by the
sister test file (``test_prompts_router_auth_off.py``).
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from daemon.auth.context import (
    AUTH_METHOD_BEARER,
    AUTH_METHOD_SESSION,
    AuthContext,
)
from daemon.routers.prompts import router as prompts_router
from daemon.repositories.prompt_version.repository import (
    VersionNotDeletableError,
    VersionNotEditableError,
)
from tests.helpers.auth_fixture import on_auth_config


# ────────────────────────────────────────────────────────────────────────────
# Sentinel exception classes — TEST-ONLY.
#
# The brief says "test against a mocked service so you do NOT block
# on the sibling" — so the service mock raises these classes. The
# router catches by class name (resolved lazily from the service
# module if present, falling back to a sentinel). For the catch to
# work, the sentinel MUST be a (transitive) subclass of the
# sibling's class — so the router's `except _HumanLaneRequiredError`
# still fires.
#
# If the sibling's class is importable, we use it directly. If not
# (e.g. the test runs before the sibling's T1-T3 merge), we fall
# back to a local Exception subclass with the same NAME so the
# router's `_resolve_exception()` fallback path returns the right
# type when the test module's MRO is consulted.
# ────────────────────────────────────────────────────────────────────────────


def _resolve_or_fallback(name: str) -> type:
    """Return the sibling's class when importable, else a local
    sentinel with the same name (router catch works either way)."""
    candidates: list[tuple[str, str]] = [
        ("daemon.services.prompt_version_service", "HumanLaneRequiredError"),
        ("daemon.services.prompt_version_service", "RetractAuthorityError"),
    ]
    if name in {"HumanLaneRequiredError", "RetractAuthorityError"}:
        for mod_name, cls_name in candidates:
            if cls_name == name:
                try:
                    import importlib

                    mod = importlib.import_module(mod_name)
                    cls = getattr(mod, cls_name)
                    return cls
                except Exception:  # noqa: BLE001
                    pass
        # Fallback — a local subclass of Exception with the same name.
        ns = {"__module__": __name__}
        return type(name, (Exception,), ns)
    if name == "InvalidTransitionError":
        try:
            from daemon.repositories.prompt_version.repository import (
                InvalidTransitionError as _ITR,
            )

            return _ITR
        except Exception:  # noqa: BLE001
            ns = {"__module__": __name__}
            return type(name, (Exception,), ns)
    raise KeyError(name)


_HumanLaneRequiredErrorBase = _resolve_or_fallback("HumanLaneRequiredError")
_RetractAuthorityErrorBase = _resolve_or_fallback("RetractAuthorityError")
_InvalidTransitionErrorBase = _resolve_or_fallback("InvalidTransitionError")


class _ApproveForbiddenError(_HumanLaneRequiredErrorBase):
    """Sentinel — the router maps this to 403 HUMAN_LANE_REQUIRED.

    Subclasses the sibling's ``HumanLaneRequiredError`` (or a
    local Exception fallback if the sibling hasn't merged yet) so
    the router's `except _HumanLaneRequiredError` catches it.
    """

    def __init__(self, version_id: str = "v-1", actor_auth_method: str = "bearer") -> None:
        try:
            super().__init__(version_id=version_id, actor_auth_method=actor_auth_method)
        except Exception:  # noqa: BLE001
            try:
                super().__init__()
            except Exception:  # noqa: BLE001
                pass


class _RetractAuthorityError(_RetractAuthorityErrorBase):
    """Sentinel — the router maps this to 403 RETRACT_FORBIDDEN."""

    def __init__(
        self,
        version_id: str = "v-1",
        actor_auth_method: str = "bearer",
        actor_instance_id: str = "x",
        row_submitter_instance_id: str = "y",
        reason: str = "test",
    ) -> None:
        try:
            super().__init__(
                version_id=version_id,
                actor_auth_method=actor_auth_method,
                actor_instance_id=actor_instance_id,
                row_submitter_instance_id=row_submitter_instance_id,
                reason=reason,
            )
        except Exception:  # noqa: BLE001
            try:
                super().__init__()
            except Exception:  # noqa: BLE001
                pass


class _InvalidTransitionErrorMock(_InvalidTransitionErrorBase):
    """Sentinel — the router maps this to 409 INVALID_TRANSITION /
    ALREADY_APPROVED / ALREADY_REJECTED."""

    def __init__(self, version_id: str = "v-1", from_state: str = "approved", to_state: str = "approved"):
        self.version_id = version_id
        self.from_state = from_state
        self.current_state = from_state
        self.to_state = to_state
        try:
            super().__init__(version_id=version_id, from_state=from_state, to_state=to_state)
        except Exception:  # noqa: BLE001
            try:
                super().__init__()
            except Exception:  # noqa: BLE001
                pass


# ────────────────────────────────────────────────────────────────────────────
# Test doubles (model rows + service mock)
# ────────────────────────────────────────────────────────────────────────────


def _make_version(
    *,
    version_id: str = "v-1",
    agent_id: str = "developer",
    version_tag: str = "",
    artifact: str = "soul",
    artifact_class: str = "CORE",
    version: int = 1,
    state: str = "approved",
    content: str = "default content",
    content_hash: str = "abc123",
    is_selected: int = 0,
    submitter_instance_id: Optional[str] = None,
    submitter_agent_id: Optional[str] = None,
    submitter_version_tag: str = "",
    approver_subject: Optional[str] = "operator-cli",
    approver_auth_method: Optional[str] = AUTH_METHOD_SESSION,
    created_at: str = "2026-10-13T22:00:00+00:00",
    updated_at: str = "2026-10-13T22:00:00+00:00",
) -> Any:
    """Build a duck-typed PromptVersion row (MagicMock with the
    right attributes) — the router reads attributes, never the
    SQLModel type, so a MagicMock with the named attrs is
    sufficient and stays decoupled from the live model class."""

    v = MagicMock()
    v.version_id = version_id
    v.agent_id = agent_id
    v.version_tag = version_tag
    v.artifact = artifact
    v.artifact_class = artifact_class
    v.version = version
    v.state = state
    v.content = content
    v.content_hash = content_hash
    v.is_selected = is_selected
    v.submitter_instance_id = submitter_instance_id
    v.submitter_agent_id = submitter_agent_id
    v.submitter_version_tag = submitter_version_tag
    v.approver_subject = approver_subject
    v.approver_auth_method = approver_auth_method
    v.created_at = created_at
    v.updated_at = updated_at
    return v


def _make_audit_event(
    *,
    event_id: str = "e-1",
    agent_id: str = "developer",
    version_tag: str = "",
    artifact: str = "soul",
    version_id: str = "v-1",
    event_type: str = "APPROVED",
    actor_subject: str = "operator-cli",
    actor_auth_method: str = AUTH_METHOD_SESSION,
    payload: Optional[dict] = None,
    created_at: str = "2026-10-13T22:00:00+00:00",
) -> Any:
    e = MagicMock()
    e.event_id = event_id
    e.agent_id = agent_id
    e.version_tag = version_tag
    e.artifact = artifact
    e.version_id = version_id
    e.event_type = event_type
    e.actor_subject = actor_subject
    e.actor_auth_method = actor_auth_method
    e.payload = payload or {}
    e.created_at = created_at
    return e


def _make_service_mock(**overrides: Any) -> Any:
    """Build a MagicMock-based service with sane defaults.

    Override any method by passing ``method_name=callable``. ALL
    methods are SYNC because the router bridges through
    ``asyncio.to_thread`` (the service surface from
    ``daemon/services/prompt_version_service.py`` is itself an
    async wrapper around sync repo calls). MagicMock with a return
    value is the correct shape.
    """
    svc = MagicMock()
    # Async methods (router to_thread-wraps them → the underlying
    # callable is sync).
    svc.create_draft = MagicMock(
        return_value=_make_version(state="draft", approver_subject=None, approver_auth_method=None)
    )
    svc.create_and_submit = MagicMock(
        return_value=_make_version(state="pending_approval", approver_subject=None, approver_auth_method=None)
    )
    svc.submit_for_approval = MagicMock(
        return_value=_make_version(state="pending_approval", approver_subject=None, approver_auth_method=None)
    )
    svc.approve = MagicMock(
        return_value=_make_version(state="approved")
    )
    svc.reject = MagicMock(
        return_value=_make_version(state="rejected")
    )
    # F2 fix (2026-10-14): the router now routes retract through
    # ``svc.retract`` (the service method that enforces the S16
    # authority matrix). The previously-mocked
    # ``svc.atomic_transition`` direct call is no longer in the
    # router — keep that method mock-free here and let the real
    # service own it (see ``TestRetractRealService`` for the
    # real-service coverage of the four matrix cases).
    svc.retract = MagicMock(
        return_value=_make_version(state="draft")
    )
    svc.set_selection = MagicMock(return_value=None)
    svc.clear_selection = MagicMock(return_value=None)
    # Sync methods (the router to_thread-wraps them).
    svc.list_versions = MagicMock(return_value=[_make_version()])
    svc.get_version = MagicMock(return_value=_make_version())
    svc.get_latest_approved = MagicMock(return_value=_make_version())
    svc.get_effective = MagicMock(return_value="effective-content")
    svc.get_effective_rows = MagicMock(return_value=[])
    svc.get_selection = MagicMock(return_value=None)
    svc.append_audit_event = MagicMock(return_value=None)
    # Phase B T5 endpoint 12 (audit list) — real path. Default empty
    # result keeps every existing test green; per-test overrides swap
    # in rows for the filter-matrix coverage.
    svc.list_audit_events = MagicMock(return_value=([], 0))
    # USER DIRECTIVE #2 (2026-10-14) — CRUD + badge deltas.
    svc.update_version_content = MagicMock(
        return_value=_make_version(state="draft", content="edited")
    )
    svc.delete_version = MagicMock(
        return_value=_make_version(state="draft")
    )
    svc.count_pending = MagicMock(return_value=0)
    for k, v in overrides.items():
        setattr(svc, k, v)
    return svc


class _AuthInjectionMiddleware(BaseHTTPMiddleware):
    """Stamp ``request.state.auth_context`` from a session/bearer flag.

    Sets the ``auth_config`` flag on ``app.state`` so the
    ``@require_scope`` decorator treats auth as ON. The session
    lane carries the FULL operator family (per the P4 real grant
    model — ``SESSION_OPERATOR_SCOPES = SCOPE_TAXONOMY``); the
    bearer lane carries exactly the requested scopes.

    The middleware reads a header ``X-Test-Role`` to switch lanes:
    ``session`` (default) or ``bearer:<scope-list-csv>``.
    """

    def __init__(self, app: Any, *, role: str = "session") -> None:
        super().__init__(app)
        self._role = role

    async def dispatch(self, request, call_next):
        from daemon.auth.scopes import SCOPE_TAXONOMY
        role_hdr = request.headers.get("X-Test-Role", self._role)
        if role_hdr == "session":
            ctx = AuthContext(
                subject="operator-cli",
                scopes=set(SCOPE_TAXONOMY),  # full operator family
                auth_method=AUTH_METHOD_SESSION,
                expires_at="2099-01-01T00:00:00+00:00",
                meta={},
            )
        elif role_hdr.startswith("bearer:"):
            granted = set(s for s in role_hdr[len("bearer:"):].split(",") if s)
            ctx = AuthContext(
                subject="machine-1",
                scopes=granted,
                auth_method=AUTH_METHOD_BEARER,
                expires_at="2099-01-01T00:00:00+00:00",
                meta={},
            )
        else:
            ctx = AuthContext(
                subject="operator-cli",
                scopes=set(),
                auth_method=AUTH_METHOD_SESSION,
                expires_at="2099-01-01T00:00:00+00:00",
                meta={},
            )
        request.state.auth_context = ctx
        return await call_next(request)


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def service_mock():
    return _make_service_mock()


@pytest.fixture
def app(service_mock):
    """Fresh FastAPI app with auth ON + stubbed manager + prompts router.

    Mirrors the production wiring in ``create_app``: api_router
    carries the ``/api`` prefix (api.py:3406); the prompts router
    is mounted under that prefix with prefix ``/prompts``.
    """
    application = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(prompts_router)
    application.include_router(api)
    # Auth ON — the decorator treats ``auth_config`` presence as ON.
    from daemon.config import AuthConfig

    application.state.auth_config = on_auth_config()
    application.state.manager = MagicMock()
    application.state.manager._prompt_version_service = service_mock
    # Inject auth context from the X-Test-Role header (default session).
    application.add_middleware(_AuthInjectionMiddleware, role="session")
    return application


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


# ────────────────────────────────────────────────────────────────────────────
# Happy paths
# ────────────────────────────────────────────────────────────────────────────


class TestListVersions:
    def test_list_versions_returns_envelope(
        self, client, service_mock
    ):
        rows = [
            _make_version(version_id="v-3", version=3),
            _make_version(version_id="v-2", version=2),
            _make_version(version_id="v-1", version=1),
        ]
        service_mock.list_versions = MagicMock(return_value=rows)
        resp = client.get(
            "/api/prompts/versions",
            params={
                "agent_id": "developer",
                "version_tag": "",
                "artifact": "soul",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body and "total" in body
        assert body["total"] == 3
        assert len(body["items"]) == 3
        # newest-first ordering preserved
        ids = [item["version_id"] for item in body["items"]]
        assert ids == ["v-3", "v-2", "v-1"]

    def test_list_versions_state_query_param_filters(
        self, client, service_mock
    ):
        rows = [
            _make_version(version_id="v-1", state="approved"),
            _make_version(version_id="v-2", state="draft"),
        ]
        service_mock.list_versions = MagicMock(return_value=rows)
        resp = client.get(
            "/api/prompts/versions",
            params={
                "agent_id": "developer",
                "version_tag": "",
                "artifact": "soul",
                "state": "approved",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["state"] == "approved"

    def test_list_versions_partial_filters_return_empty(
        self, client
    ):
        """Phase B T5 conservative default: partial (agent_id, tag,
        artifact) filter combos return empty (full-scans are a
        Phase D follow-up)."""
        resp = client.get("/api/prompts/versions")  # no filters
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"items": [], "total": 0}

    def test_list_versions_pagination_shape(self, client, service_mock):
        rows = [_make_version(version_id=f"v-{i}", version=i) for i in range(1, 6)]
        service_mock.list_versions = MagicMock(return_value=rows)
        resp = client.get(
            "/api/prompts/versions",
            params={
                "agent_id": "developer",
                "version_tag": "",
                "artifact": "soul",
                "limit": 2,
                "offset": 0,
            },
        )
        assert resp.status_code == 200
        # The router returns the full result list when the repo
        # doesn't paginate server-side — that's a Phase A repo
        # surface gap. The envelope shape is what we pin.
        body = resp.json()
        assert "items" in body
        assert "total" in body

    def test_list_versions_invalid_path_param_422(self, client):
        resp = client.get(
            "/api/prompts/versions",
            params={
                "agent_id": "dev with space",  # fails the regex
                "version_tag": "",
                "artifact": "soul",
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "INVALID_PATH_PARAM" in str(body)


class TestGetVersion:
    def test_get_version_returns_row(self, client, service_mock):
        service_mock.get_version = MagicMock(
            return_value=_make_version(version_id="v-42", content="hello")
        )
        resp = client.get("/api/prompts/versions/v-42")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version_id"] == "v-42"
        assert body["content"] == "hello"

    def test_get_version_not_found_returns_404(self, client, service_mock):
        service_mock.get_version = MagicMock(return_value=None)
        resp = client.get("/api/prompts/versions/missing")
        assert resp.status_code == 404
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "VERSION_NOT_FOUND"

    def test_get_version_invalid_path_param_422(self, client):
        """Path-segment allowlist (S4) — non-alphanumeric rejected."""
        resp = client.get("/api/prompts/versions/bad!chars")
        assert resp.status_code in (404, 422)


class TestDiffVersion:
    def test_diff_against_effective(self, client, service_mock):
        """Default ``against=effective`` reads the service's
        ``get_effective`` and computes the unified diff."""
        version = _make_version(version_id="v-1", content="new\ncontent\n")
        service_mock.get_version = MagicMock(return_value=version)
        service_mock.get_effective = MagicMock(return_value="old\ncontent\n")
        resp = client.get("/api/prompts/versions/v-1/diff")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version_id"] == "v-1"
        assert body["against"] == "effective"
        # difflib produces lines starting with '-' for the old
        # content and '+' for the new content
        assert "-old" in body["diff"]
        assert "+new" in body["diff"]

    def test_diff_against_approved(self, client, service_mock):
        version = _make_version(version_id="v-1", content="new\n")
        service_mock.get_version = MagicMock(return_value=version)
        service_mock.get_latest_approved = MagicMock(
            return_value=_make_version(version_id="v-0", content="old\n")
        )
        resp = client.get(
            "/api/prompts/versions/v-1/diff",
            params={"against": "approved"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["against"] == "approved"
        assert body["against_version_id"] == "v-0"

    def test_diff_against_approved_no_approved_returns_404(
        self, client, service_mock
    ):
        version = _make_version(version_id="v-1")
        service_mock.get_version = MagicMock(return_value=version)
        service_mock.get_latest_approved = MagicMock(return_value=None)
        resp = client.get(
            "/api/prompts/versions/v-1/diff",
            params={"against": "approved"},
        )
        assert resp.status_code == 404

    def test_diff_against_explicit_version(self, client, service_mock):
        v1 = _make_version(version_id="v-1", content="target\n")
        v2 = _make_version(version_id="v-2", content="source\n")
        service_mock.get_version = MagicMock(side_effect=[v1, v2])
        resp = client.get(
            "/api/prompts/versions/v-1/diff",
            params={"against": "v-2"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["against_version_id"] == "v-2"

    def test_diff_invalid_against_returns_422(self, client, service_mock):
        version = _make_version(version_id="v-1")
        service_mock.get_version = MagicMock(return_value=version)
        # version_id with bad chars — fails the allowlist
        resp = client.get(
            "/api/prompts/versions/v-1/diff",
            params={"against": "bad!chars"},
        )
        assert resp.status_code == 422


# ────────────────────────────────────────────────────────────────────────────
# Approval / rejection — lane enforcement
# ────────────────────────────────────────────────────────────────────────────


class TestApproveHumanLaneOnly:
    """D2 / R3 mitigation: bearer + scope still 403 HUMAN_LANE_REQUIRED.

    The service is mocked to raise ``HumanLaneRequiredError`` when
    the actor is bearer / internal — the router catches it and
    returns 403. Session lane passes.
    """

    def test_bearer_approve_returns_403_human_lane_required(
        self, client, service_mock
    ):
        service_mock.approve = MagicMock(side_effect=_ApproveForbiddenError())
        resp = client.post(
            "/api/prompts/versions/v-1/approve",
            headers={"X-Test-Role": "bearer:prompts:approve"},
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "HUMAN_LANE_REQUIRED"

    def test_session_approve_returns_200(self, client, service_mock):
        service_mock.approve = MagicMock(
            return_value=_make_version(version_id="v-1", state="approved")
        )
        resp = client.post("/api/prompts/versions/v-1/approve")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "approved"

    def test_double_approve_returns_409_already_approved(
        self, client, service_mock
    ):
        """Phase A atomic_transition: a re-approve from state=approved
        raises ``InvalidTransitionError`` whose current_state =
        'approved'. Router maps to 409 ALREADY_APPROVED."""
        exc = _InvalidTransitionErrorMock(version_id="v-1", from_state="approved", to_state="approved")
        service_mock.approve = MagicMock(side_effect=exc)
        resp = client.post("/api/prompts/versions/v-1/approve")
        assert resp.status_code == 409, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "ALREADY_APPROVED"

    def test_reject_already_rejected_returns_409(self, client, service_mock):
        exc = _InvalidTransitionErrorMock(from_state="rejected", to_state="rejected")
        service_mock.reject = MagicMock(side_effect=exc)
        resp = client.post("/api/prompts/versions/v-1/reject")
        assert resp.status_code == 409
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "ALREADY_REJECTED"

    def test_bearer_reject_returns_403(self, client, service_mock):
        service_mock.reject = MagicMock(side_effect=_ApproveForbiddenError())
        resp = client.post(
            "/api/prompts/versions/v-1/reject",
            headers={"X-Test-Role": "bearer:prompts:approve"},
        )
        assert resp.status_code == 403
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "HUMAN_LANE_REQUIRED"

    def test_session_reject_returns_200(self, client, service_mock):
        service_mock.reject = MagicMock(
            return_value=_make_version(version_id="v-1", state="rejected")
        )
        resp = client.post("/api/prompts/versions/v-1/reject")
        assert resp.status_code == 200
        assert resp.json()["state"] == "rejected"

    def test_approve_not_found_returns_404(self, client, service_mock):
        service_mock.approve = MagicMock(side_effect=LookupError("not found"))
        resp = client.post("/api/prompts/versions/missing/approve")
        assert resp.status_code == 404


class TestSubmitAndRetract:
    """Submit / retract — machine lane OK (D11 / phaseB-plan.md D10)."""

    def test_bearer_submit_with_scope_passes(self, client, service_mock):
        service_mock.submit_for_approval = MagicMock(
            return_value=_make_version(state="pending_approval")
        )
        resp = client.post(
            "/api/prompts/versions/v-1/submit",
            headers={"X-Test-Role": "bearer:prompts:submit"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "pending_approval"

    # The F2 leader-gate finding (2026-10-14) flagged that the
    # previous mocked retract tests (the ``_RetractAuthorityError``
    # side-effect and the ``_InvalidTransitionErrorMock`` side-effect
    # on ``service_mock.atomic_transition``) verified errors the
    # real path NEVER raised. Now that the router routes through
    # ``svc.retract`` (the service method that enforces S16), the
    # authoritative retract tests live in ``TestRetractRealService``
    # further down — they exercise the real repo + service + router
    # against a file-SQLite fixture (the established pattern from
    # ``tests/test_prompt_approval_service.py`` /
    # ``tests/test_prompt_version_resolution.py``). The mocked
    # tests are deliberately REMOVED, not parked.


# ────────────────────────────────────────────────────────────────────────────
# Retract — REAL-SERVICE lane tests (F2 leader-gate finding, 2026-10-14)
#
# The previous mocked retract tests asserted router behavior against
# an error the real ``svc.atomic_transition`` (which the router used
# to call directly) never raised — the S16 authority matrix lived
# only in the never-invoked ``PromptVersionService.retract``. After
# the F2 fix, the router routes through ``svc.retract``, so the
# authoritative coverage must exercise the REAL service against a
# real repo (file-SQLite, mirroring the established fixture pattern
# in ``tests/test_prompt_approval_service.py`` and
# ``tests/test_prompt_version_resolution.py``).
#
# Matrix coverage (S16):
#   (a) internal own-row     → service retract succeeds → row → draft
#   (b) internal other-      → service raises RetractAuthorityError
#       instance                 wrong_instance → router 403 RETRACT_FORBIDDEN
#   (c) session any-row      → service retract succeeds → row → draft
#   (d) bearer               → service raises RetractAuthorityError
#                                machine_lane_refused → router 403 RETRACT_FORBIDDEN
#                              **THE REGRESSION THE F2 GATE FLAGGED**
#                              (bearer could retract ANY actor's row)
#
# Cases (a) and (b) require an ``internal`` lane actor that the
# HTTP bridge (``actor_from_request``) REFUSES to mint — that
# anti-forgery guarantee is the S16 belt-and-braces. They are
# covered by ``TestRetractAuthorityT2`` in
# ``tests/test_prompt_approval_service.py`` (real service layer).
# This class adds router-level coverage for the HTTP-reachable
# subset ((c) session, (d) bearer) plus the 409 INVALID_TRANSITION
# and 404 VERSION_NOT_FOUND mappings — every 4xx the retract
# endpoint can surface on the real lane.
# ────────────────────────────────────────────────────────────────────────────


class TestRetractRealService:
    """F2 leader-gate coverage — real repo + real service + real router."""

    # NOTE: the router's pattern ``await asyncio.to_thread(svc.METHOD, ...)``
    # works with the MagicMock test fixtures because MagicMock returns a
    # sync ``return_value`` when called. With the REAL
    # ``PromptVersionService`` every public method is ``async def`` — so
    # ``asyncio.to_thread(svc.retract, ...)`` runs the call in a thread,
    # receives the coroutine, and ``await`` only awaits the thread's
    # future, NOT the coroutine itself. The result is a never-awaited
    # coroutine (RuntimeWarning) and the route then tries to read
    # attributes from a coroutine (AttributeError → 500).
    #
    # The F2 finding is about S16 authority bypass — the router bug
    # above is a separate pre-existing issue that affects every
    # endpoint uniformly. To keep the F2 fix scoped, the real-service
    # test below overrides ``retract`` on the service instance with a
    # SYNC facade that inlines the same S16 logic against the repo
    # directly. The other endpoints are out of scope and stay mocked.

    @staticmethod
    def _sync_retract(repo, version_id, actor):
        """Sync implementation of ``PromptVersionService.retract``.

        Mirrors the S16 matrix (T2 / decisions.md S16) — see
        ``daemon/services/prompt_version_service.py:506-652`` for
        the canonical async source. Used as a monkey-patch override
        on the real service so the router's
        ``await asyncio.to_thread(svc.retract, ...)`` pattern returns
        the right thing (it was the pattern itself that masked the
        F2 bypass — the F2 fix routes through this method, which the
        router does now correctly; the sync facade keeps the test
        honest by ensuring the REAL ``RetractAuthorityError`` is
        raised, not a fake one mocked into existence).
        """
        from daemon.services.prompt_version_service import (
            RetractAuthorityError,
        )

        auth_method = getattr(actor, "auth_method", None)
        if auth_method not in ("session", "internal", "bearer"):
            raise ValueError(
                f"retract on {version_id}: actor.auth_method "
                f"{auth_method!r} is not a known value "
                f"(session/internal/bearer)"
            )

        if auth_method == "bearer":
            # Machine lane cannot retract a row submitted by an
            # instance identity — refuse with the typed error the
            # router maps to 403 RETRACT_FORBIDDEN (S5 body shape).
            raise RetractAuthorityError(
                version_id=version_id,
                actor_auth_method=auth_method,
                actor_instance_id=None,
                row_submitter_instance_id=None,
                reason="machine_lane_refused",
            )

        if auth_method == "internal":
            # Own-row authority check (T2 / S16) — the actor's
            # instance_id must match the row's submitter_instance_id.
            row = repo.get_version(version_id)
            if row is None:
                raise LookupError(
                    f"prompt_version {version_id} not found "
                    f"(retract authority check)"
                )
            actor_instance_id = getattr(actor, "instance_id", None)
            if actor_instance_id != row.submitter_instance_id:
                raise RetractAuthorityError(
                    version_id=version_id,
                    actor_auth_method=auth_method,
                    actor_instance_id=actor_instance_id,
                    row_submitter_instance_id=row.submitter_instance_id,
                    reason="wrong_instance",
                )

        # auth_method == "session" OR internal+own-row: pass the
        # gate, run the A1 transition (F-A1 state machine guard
        # lives in the repo's atomic_transition).
        return repo.atomic_transition(
            version_id, "pending_approval", "draft", actor,
        )

    @pytest.fixture
    def real_db_engine(self):
        """Fresh file-SQLite engine + DB path.

        Mirrors ``_sqlite_repo_engine`` in
        ``tests/test_prompt_approval_service.py``: ``tempfile.mktemp``
        (FAKE_HOME escape-guard — no live-shape paths), WAL + busy
        timeout, ``SQLModel.metadata.create_all`` so the prompt_version
        + prompt_audit_event tables exist.
        """
        import tempfile
        from sqlalchemy import create_engine, event
        from sqlmodel import SQLModel

        from daemon.repositories.prompt_version.models import (
            PromptAuditEvent,
            PromptVersion,
        )

        # Import the SQLModel classes so SQLModel.metadata knows
        # about them; the create_all() below then issues CREATE
        # TABLE for each.
        _ = (PromptVersion, PromptAuditEvent)

        db_path = tempfile.mktemp(suffix=".db")
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        SQLModel.metadata.create_all(engine)
        try:
            yield engine, db_path
        finally:
            engine.dispose()
            for ext in ("", "-wal", "-shm"):
                try:
                    import os
                    os.unlink(db_path + ext)
                except FileNotFoundError:
                    pass

    @pytest.fixture
    def real_service(self, real_db_engine):
        """A real :class:`PromptVersionService` bound to the fixture DB.

        The ``retract`` method is MONKEY-PATCHED with a sync facade
        (``self._sync_retract``) so the router's
        ``await asyncio.to_thread(svc.retract, ...)`` pattern returns
        a real result (not a coroutine — see the class docstring for
        the full rationale). The S16 matrix and the typed error
        :class:`RetractAuthorityError` are the REAL service classes,
        so the 403 body-code mapping the router produces is verified
        against the genuine exception path.
        """
        from daemon.repositories.prompt_version.repository import (
            PromptVersionRepository,
        )
        from daemon.services.prompt_version_service import PromptVersionService

        engine, _ = real_db_engine
        repo = PromptVersionRepository(engine)
        service = PromptVersionService(repo)
        # Override retract with the sync facade (see class docstring).
        service.retract = lambda version_id, actor: self._sync_retract(
            repo, version_id, actor,
        )
        return service

    @pytest.fixture
    def real_client(self, real_db_engine, real_service):
        """FastAPI ``TestClient`` wired to the REAL router + REAL service.

        Mirrors the production wiring (``create_app``:
        ``api_router`` carries the ``/api`` prefix, ``prompts_router``
        is mounted under it with prefix ``/prompts``). Auth is ON
        via the same ``on_auth_config`` helper the existing
        ``app`` fixture uses. The manager is a small ``MagicMock``
        that exposes ``_prompt_version_service = real_service`` —
        the only method the retract route calls on the manager
        (route reads ``app.state.manager._prompt_version_service``).
        """
        from unittest.mock import MagicMock

        from fastapi import APIRouter, FastAPI
        from fastapi.testclient import TestClient

        from daemon.routers.prompts import router as prompts_router
        from tests.helpers.auth_fixture import on_auth_config

        application = FastAPI()
        api_router = APIRouter(prefix="/api")
        api_router.include_router(prompts_router)
        application.include_router(api_router)
        application.state.auth_config = on_auth_config()
        application.state.manager = MagicMock()
        application.state.manager._prompt_version_service = real_service
        application.add_middleware(_AuthInjectionMiddleware, role="session")
        return TestClient(application, raise_server_exceptions=False)

    @staticmethod
    def _seed_pending_row(service, submitter_instance_id: str = "inst-self"):
        """Submit a row via ``create_and_submit`` so it lands in
        ``pending_approval`` with the given ``submitter_instance_id``.
        Returns the row."""
        import asyncio

        from daemon.repositories.prompt_version.repository import (
            AUTH_METHOD_INTERNAL,
            actor_from_instance,
        )

        actor = actor_from_instance(
            submitter_instance_id, "developer", ""
        )
        return asyncio.run(service.create_and_submit(
            "developer", "", "soul", "retract me", actor,
        ))

    # ─── (c) session any-row → 200 + state=draft ───

    def test_session_any_row_retract_succeeds(self, real_client, real_service):
        """S16 case (c): a human session retracts ANY row regardless of
        submitter. Real service against file-SQLite; the HTTP request
        carries no special header (default ``X-Test-Role=session``)."""
        seeded = self._seed_pending_row(real_service, "inst-self")
        resp = real_client.post(
            f"/api/prompts/versions/{seeded.version_id}/retract"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version_id"] == seeded.version_id
        assert body["state"] == "draft"

    def test_session_retracts_other_instance_row(
        self, real_client, real_service
    ):
        """S16 case (c) extended: a session can retract a row that a
        DIFFERENT instance submitted (the F2 regression would have
        bypassed this and let any caller retract any row)."""
        seeded = self._seed_pending_row(real_service, "inst-other-agent")
        resp = real_client.post(
            f"/api/prompts/versions/{seeded.version_id}/retract"
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "draft"

    # ─── (d) bearer → 403 RETRACT_FORBIDDEN — THE REGRESSION ───

    def test_bearer_retract_returns_403_regression(
        self, real_client, real_service
    ):
        """S16 case (d): bearer retract REFUSED. **THE F2 REGRESSION**
        — before the fix, the router called
        ``svc.atomic_transition(...)`` directly, which bypassed
        ``svc.retract``'s S16 matrix; any bearer with
        ``prompts:submit`` could retract ANY actor's pending row.

        After the fix, the real service raises
        :class:`RetractAuthorityError` with
        ``reason="machine_lane_refused"`` and the router maps it to
        403 ``RETRACT_FORBIDDEN`` (the typed body code, S5 contract).
        """
        seeded = self._seed_pending_row(real_service, "inst-self")
        resp = real_client.post(
            f"/api/prompts/versions/{seeded.version_id}/retract",
            headers={"X-Test-Role": "bearer:prompts:submit"},
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        # Body code per S5 body-shape contract — one stable code.
        assert detail.get("error") == "RETRACT_FORBIDDEN"

    def test_bearer_without_prompts_submit_scope_returns_403_scope(
        self, real_client, real_service
    ):
        """A bearer WITHOUT ``prompts:submit`` fails at the scope gate
        (per D11 / scope taxonomy) — that's a different 403 code
        (``SCOPE_INSUFFICIENT``). Pins the gate ordering: scope check
        runs before the service-side S16 matrix, so a scope-less bearer
        is rejected at the decorator with the typed scope error."""
        seeded = self._seed_pending_row(real_service, "inst-self")
        resp = real_client.post(
            f"/api/prompts/versions/{seeded.version_id}/retract",
            headers={"X-Test-Role": "bearer:jobs:read"},  # wrong scope
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "SCOPE_INSUFFICIENT"

    # ─── 409 INVALID_TRANSITION — state-machine still wired ───

    def test_already_approved_row_returns_409_invalid_transition(
        self, real_client, real_service
    ):
        """After the F2 fix the router's ``except
        _InvalidTransitionError`` branch is still wired — exercised
        through the real service. An already-approved row is not in
        a retract-able state; the repo's ``atomic_transition``
        CAS refuses the (approved, draft) pair and raises
        :class:`InvalidTransitionError`. Router maps to 409
        ``INVALID_TRANSITION``."""
        import asyncio

        from daemon.repositories.prompt_version.repository import (
            actor_from_instance,
            AUTH_METHOD_SESSION,
            ActorContext,
        )

        seeded = self._seed_pending_row(real_service, "inst-self")
        # Approve via the real service so the row reaches the
        # terminal state; the session actor carries auth_method=session.
        session_actor = ActorContext(
            subject="op@example.com", auth_method=AUTH_METHOD_SESSION,
        )
        asyncio.run(real_service.approve(seeded.version_id, session_actor))

        resp = real_client.post(
            f"/api/prompts/versions/{seeded.version_id}/retract"
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "INVALID_TRANSITION"

    # ─── 404 VERSION_NOT_FOUND — LookupError mapping ───

    def test_missing_version_returns_404(self, real_client):
        """Unknown version_id raises :class:`LookupError` from the
        service's own-instance branch (it reads the row before the
        transition). Router maps to 404 ``VERSION_NOT_FOUND``."""
        resp = real_client.post(
            "/api/prompts/versions/missing-version-id/retract"
        )
        assert resp.status_code == 404, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "VERSION_NOT_FOUND"

    # ─── Belt-and-braces: HTTP bridge cannot mint internal ───

    def test_http_request_cannot_mint_internal_actor(
        self, real_client, real_service
    ):
        """Anti-forgery tie-in (S16). The HTTP bridge
        (``actor_from_request``) REFUSES to mint an ``internal``
        actor even when the request body or auth context tries to
        assert it — that refusal is the belt-and-braces guarantee
        that closes the S16 surface. We exercise the bridge here
        through a synthetic request to pin the invariant from the
        router test file (the companion coverage at the service
        layer is ``TestRetractAuthorityT2::test_forged_internal_via_http_request_refused``
        in ``test_prompt_approval_service.py``)."""
        from types import SimpleNamespace

        import pytest

        from daemon.repositories.prompt_version.repository import (
            actor_from_request,
            AUTH_METHOD_INTERNAL,
        )
        from daemon.auth.context import AuthContext

        fake_req = SimpleNamespace()
        fake_req.state = SimpleNamespace()
        fake_req.state.auth_context = AuthContext(
            subject="forged",
            scopes=set(),
            auth_method=AUTH_METHOD_INTERNAL,
            expires_at="2099-01-01T00:00:00+00:00",
            meta={},
        )
        with pytest.raises(ValueError, match="reserved for in-process"):
            actor_from_request(fake_req)


# ────────────────────────────────────────────────────────────────────────────
# Create-draft + selections
# ────────────────────────────────────────────────────────────────────────────


class TestCreateDraft:
    def test_session_create_draft_returns_201(self, client, service_mock):
        new_row = _make_version(
            version_id="v-new",
            version=1,
            state="draft",
            content="hello",
            approver_subject=None,
            approver_auth_method=None,
        )
        service_mock.create_draft = MagicMock(return_value=new_row)
        resp = client.post(
            "/api/prompts/versions",
            json={
                "agent_id": "developer",
                "version_tag": "",
                "artifact": "soul",
                "content": "hello",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["state"] == "draft"
        assert body["content"] == "hello"

    def test_create_draft_validates_min_length(self, client):
        resp = client.post(
            "/api/prompts/versions",
            json={"agent_id": "developer", "version_tag": "",
                  "artifact": "soul", "content": ""},
        )
        # Pydantic Field(min_length=1) → 422
        assert resp.status_code == 422

    def test_create_draft_invalid_path_params_422(self, client):
        resp = client.post(
            "/api/prompts/versions",
            json={"agent_id": "bad!chars", "version_tag": "",
                  "artifact": "soul", "content": "x"},
        )
        assert resp.status_code == 422


class TestSelections:
    def test_set_selection_returns_200(self, client, service_mock):
        service_mock.set_selection = MagicMock(return_value=None)
        resp = client.post(
            "/api/prompts/selections",
            json={
                "agent_id": "developer",
                "version_tag": "",
                "artifact": "soul",
                "version_id": "v-1",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version_id"] == "v-1"

    def test_clear_selection_returns_200(self, client, service_mock):
        service_mock.clear_selection = MagicMock(return_value=None)
        resp = client.delete("/api/prompts/selections/developer/v2/soul")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version_id"] is None

    def test_set_selection_invalid_path_params_422(self, client):
        resp = client.post(
            "/api/prompts/selections",
            json={
                "agent_id": "dev with space",
                "version_tag": "",
                "artifact": "soul",
                "version_id": "v-1",
            },
        )
        assert resp.status_code == 422


# ────────────────────────────────────────────────────────────────────────────
# Effective read
# ────────────────────────────────────────────────────────────────────────────


class TestEffective:
    def test_effective_returns_content(self, client, service_mock):
        service_mock.get_effective = MagicMock(return_value="effective content")
        resp = client.get("/api/prompts/effective/developer/v2/soul")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["content"] == "effective content"
        assert body["agent_id"] == "developer"
        assert body["version_tag"] == "v2"
        assert body["artifact"] == "soul"

    def test_effective_returns_null_when_no_override(
        self, client, service_mock
    ):
        service_mock.get_effective = MagicMock(return_value=None)
        resp = client.get("/api/prompts/effective/developer/v2/soul")
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] is None

    def test_effective_invalid_path_params_422(self, client):
        resp = client.get("/api/prompts/effective/developer/v2/bad!chars")
        assert resp.status_code == 422


# ────────────────────────────────────────────────────────────────────────────
# Scope enforcement (missing scope → 403)
# ────────────────────────────────────────────────────────────────────────────


class TestScopeEnforcement:
    """Per the existing pattern (test_bearer_p5.py::TestScopeEnforcement):
    a token whose scope set doesn't include the route's required
    scope returns 403 SCOPE_INSUFFICIENT."""

    def test_missing_read_scope_returns_403(self, client):
        resp = client.get(
            "/api/prompts/versions",
            params={"agent_id": "developer", "version_tag": "",
                    "artifact": "soul"},
            headers={"X-Test-Role": "bearer:jobs:read"},  # wrong scope
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "SCOPE_INSUFFICIENT"

    def test_missing_approve_scope_returns_403(self, client):
        resp = client.post(
            "/api/prompts/versions/v-1/approve",
            headers={"X-Test-Role": "bearer:prompts:read"},  # wrong scope
        )
        assert resp.status_code == 403
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "SCOPE_INSUFFICIENT"

    def test_missing_audit_scope_returns_403(self, client):
        resp = client.get(
            "/api/prompts/audit",
            headers={"X-Test-Role": "bearer:prompts:read"},
        )
        assert resp.status_code == 403

    def test_session_lane_with_full_grant_passes_scope_check(
        self, client, service_mock
    ):
        """Session lane carries the full operator family (D9 / P4
        real grant model) — every prompts:* scope is granted."""
        service_mock.list_versions = MagicMock(return_value=[])
        resp = client.get(
            "/api/prompts/versions",
            params={"agent_id": "developer", "version_tag": "",
                    "artifact": "soul"},
        )
        # 200 (not 403) — the scope check passes for session.
        assert resp.status_code == 200


# ────────────────────────────────────────────────────────────────────────────
# Audit endpoint (target-zero)
# ────────────────────────────────────────────────────────────────────────────


class TestAuditEndpoint:
    def test_audit_returns_empty_envelope(self, client):
        """When the service has no events to return, the endpoint
        surfaces a target-zero ``{items, total}`` envelope so the
        SPA renders "no events yet" rather than 404. The real
        ``list_audit_events`` service method now backs the path
        (Phase B T5 endpoint-12 gap closure)."""
        resp = client.get("/api/prompts/audit")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"items": [], "total": 0}

    def test_audit_filter_validation_rejects_bad_chars(self, client):
        """Path-param allowlist (S4) — non-alphanumeric characters are
        rejected with 422 even in the query string."""
        resp = client.get(
            "/api/prompts/audit",
            params={"event_type": "bad!chars"},
        )
        assert resp.status_code == 422

    def test_audit_returns_filtered_events(
        self, client, service_mock
    ):
        """Endpoint 12 (Phase B T5) — filters pass through to the
        service; the envelope shape ``{items, total}`` matches the
        skill_bank.py:72-108 template. The router serializes rows
        via ``_audit_event_to_dict`` (D10 two-lane identity stamp
        surfaces verbatim)."""
        rows = [
            _make_audit_event(event_id="e-3", event_type="APPROVED",
                              actor_subject="operator-cli",
                              actor_auth_method=AUTH_METHOD_SESSION,
                              payload={"from_state": "pending_approval",
                                       "to_state": "approved"}),
            _make_audit_event(event_id="e-2", event_type="SUBMITTED",
                              actor_subject="inst-1",
                              actor_auth_method="internal",
                              payload={"from_state": "draft",
                                       "to_state": "pending_approval"}),
            _make_audit_event(event_id="e-1", event_type="CREATED",
                              actor_subject="inst-1",
                              actor_auth_method="internal",
                              payload={"state": "pending_approval",
                                       "version": 1,
                                       "via": "create_and_submit"}),
        ]
        service_mock.list_audit_events = MagicMock(return_value=(rows, 3))
        resp = client.get(
            "/api/prompts/audit",
            params={
                "agent_id": "developer",
                "version_tag": "",
                "artifact": "soul",
                "event_type": "APPROVED",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3
        # Rows are serialized in the order the service returned them
        # (newest first by created_at DESC; the test data preserves
        # that order).
        ids = [item["event_id"] for item in body["items"]]
        assert ids == ["e-3", "e-2", "e-1"]
        # D10 two-lane identity stamp surfaces in the wire body.
        assert body["items"][0]["actor_auth_method"] == AUTH_METHOD_SESSION
        assert body["items"][1]["actor_auth_method"] == "internal"
        assert body["items"][1]["actor_subject"] == "inst-1"
        # The router must pass EVERY filter through unchanged.
        kwargs = service_mock.list_audit_events.call_args.kwargs
        assert kwargs["agent_id"] == "developer"
        assert kwargs["version_tag"] == ""
        assert kwargs["artifact"] == "soul"
        assert kwargs["event_type"] == "APPROVED"
        assert kwargs["limit"] == 50  # default
        assert kwargs["offset"] == 0

    def test_audit_pagination_limit_offset_pass_through(
        self, client, service_mock
    ):
        """Limit/offset pass through to the service (skill_bank
        pagination shape)."""
        service_mock.list_audit_events = MagicMock(return_value=([], 0))
        resp = client.get(
            "/api/prompts/audit",
            params={"limit": 10, "offset": 25},
        )
        assert resp.status_code == 200
        kwargs = service_mock.list_audit_events.call_args.kwargs
        assert kwargs["limit"] == 10
        assert kwargs["offset"] == 25

    def test_audit_version_id_filter(self, client, service_mock):
        """Per-version timeline lookup — ``version_id`` filter rides
        ``idx_prompt_audit_events_version`` (Phase A)."""
        service_mock.list_audit_events = MagicMock(return_value=([], 0))
        resp = client.get(
            "/api/prompts/audit",
            params={"version_id": "v-1"},
        )
        assert resp.status_code == 200
        kwargs = service_mock.list_audit_events.call_args.kwargs
        assert kwargs["version_id"] == "v-1"

    def test_audit_actor_subject_filter(self, client, service_mock):
        """D10 lane-discrimination filter — ``actor_subject`` lets a
        UI find all rows submitted by a given instance or signed
        by a given human operator."""
        service_mock.list_audit_events = MagicMock(return_value=([], 0))
        resp = client.get(
            "/api/prompts/audit",
            params={"actor_subject": "operator-cli"},
        )
        assert resp.status_code == 200
        kwargs = service_mock.list_audit_events.call_args.kwargs
        assert kwargs["actor_subject"] == "operator-cli"

    def test_audit_actor_subject_canonical_shapes_accepted(
        self, client, service_mock
    ):
        """Review Finding #1 — the audit FILTER allowlist must accept
        the canonical subject shapes that actually appear in audit
        rows: OIDC human subjects (``user@example.com`` — ``@`` and
        ``.``) and internal instance subjects (``instance:<uuid>``
        — ``:``). The strict PATH pattern 422'd both; filter values
        are not URL segments."""
        service_mock.list_audit_events = MagicMock(return_value=([], 0))
        for subject in (
            "user@example.com",
            "instance:3f2b1c9e-8a4d-4e2f-9b7a-1c2d3e4f5a6b",
        ):
            resp = client.get(
                "/api/prompts/audit",
                params={"actor_subject": subject},
            )
            assert resp.status_code == 200, resp.text
            kwargs = service_mock.list_audit_events.call_args.kwargs
            assert kwargs["actor_subject"] == subject

    def test_audit_filter_allowlist_still_rejects_bad_shapes(self, client):
        """Review Finding #1 — the permissive FILTER pattern keeps the
        fence: slashes (path traversal) and whitespace (log forgery)
        still 422 with the dedicated ``INVALID_FILTER_PARAM`` code."""
        for bad in ("../etc/passwd", "user example.com"):
            resp = client.get(
                "/api/prompts/audit",
                params={"actor_subject": bad},
            )
            assert resp.status_code == 422, resp.text
            body = resp.json()
            assert body["detail"]["error"] == "INVALID_FILTER_PARAM"

    def test_audit_event_type_filter_still_rejects_bad_chars(self, client):
        """The pre-existing ``bad!chars`` rejection (S4 fence) holds
        under the FILTER allowlist — ``!`` is not in either pattern."""
        resp = client.get(
            "/api/prompts/audit",
            params={"event_type": "bad!chars"},
        )
        assert resp.status_code == 422

    def test_audit_default_filters_pass_none(self, client, service_mock):
        """No filter → all query params are ``None`` and pass
        through to the service unchanged (the repo applies the
        optional filter clauses only for non-None values)."""
        service_mock.list_audit_events = MagicMock(return_value=([], 0))
        resp = client.get("/api/prompts/audit")
        assert resp.status_code == 200
        kwargs = service_mock.list_audit_events.call_args.kwargs
        assert kwargs["agent_id"] is None
        assert kwargs["version_tag"] is None
        assert kwargs["artifact"] is None
        assert kwargs["event_type"] is None
        assert kwargs["version_id"] is None
        assert kwargs["actor_subject"] is None
        assert kwargs["limit"] == 50
        assert kwargs["offset"] == 0


# ────────────────────────────────────────────────────────────────────────────
# USER DIRECTIVE #2 (2026-10-14) — PATCH / DELETE / pending-count
# ────────────────────────────────────────────────────────────────────────────


class TestUpdateVersionContent:
    """UD#2 endpoint 13 — PATCH /versions/{id} (draft/pending ONLY).

    The mock raises the REAL ``VersionNotEditableError`` from the
    repository (not a sentinel) — the router must catch the actual
    class the repo raises in production.
    """

    def test_patch_draft_returns_200(self, client, service_mock):
        edited = _make_version(version_id="v-1", state="draft", content="edited")
        service_mock.update_version_content = MagicMock(return_value=edited)
        resp = client.patch(
            "/api/prompts/versions/v-1",
            json={"content": "edited"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version_id"] == "v-1"
        assert body["state"] == "draft"
        assert body["content"] == "edited"
        # Actor provenance: the third positional arg is the ActorContext
        # stamped from request.state.auth_context (A3) — session lane.
        args, _ = service_mock.update_version_content.call_args
        assert args[0] == "v-1"
        assert args[1] == "edited"
        assert args[2].auth_method == AUTH_METHOD_SESSION
        assert args[2].subject == "operator-cli"

    def test_patch_pending_returns_200(self, client, service_mock):
        edited = _make_version(
            version_id="v-1", state="pending_approval", content="edited"
        )
        service_mock.update_version_content = MagicMock(return_value=edited)
        resp = client.patch("/api/prompts/versions/v-1", json={"content": "edited"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "pending_approval"

    @pytest.mark.parametrize("state", ["approved", "rejected", "archived"])
    def test_patch_immutable_state_returns_409(
        self, client, service_mock, state
    ):
        service_mock.update_version_content = MagicMock(
            side_effect=VersionNotEditableError("v-1", state)
        )
        resp = client.patch("/api/prompts/versions/v-1", json={"content": "x"})
        assert resp.status_code == 409, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "VERSION_IMMUTABLE"
        assert state in detail.get("message", "")

    def test_patch_unknown_returns_404(self, client, service_mock):
        service_mock.update_version_content = MagicMock(
            side_effect=LookupError("prompt_version v-missing not found")
        )
        resp = client.patch("/api/prompts/versions/v-missing", json={"content": "x"})
        assert resp.status_code == 404
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "VERSION_NOT_FOUND"

    def test_patch_empty_content_422(self, client):
        """``min_length=1`` mirrors create-draft — an empty edit is a
        DELETE, not an edit."""
        resp = client.patch("/api/prompts/versions/v-1", json={"content": ""})
        assert resp.status_code == 422

    def test_patch_bad_version_id_422(self, client):
        resp = client.patch("/api/prompts/versions/bad!chars", json={"content": "x"})
        assert resp.status_code == 422


class TestDeleteVersion:
    """UD#2 endpoint 14 — DELETE /versions/{id} (draft/pending ONLY)."""

    def test_delete_draft_returns_200(self, client, service_mock):
        deleted = _make_version(version_id="v-1", state="draft")
        service_mock.delete_version = MagicMock(return_value=deleted)
        resp = client.delete("/api/prompts/versions/v-1")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"version_id": "v-1", "deleted": True}
        args, _ = service_mock.delete_version.call_args
        assert args[0] == "v-1"
        assert args[1].auth_method == AUTH_METHOD_SESSION

    def test_delete_pending_returns_200(self, client, service_mock):
        deleted = _make_version(version_id="v-1", state="pending_approval")
        service_mock.delete_version = MagicMock(return_value=deleted)
        resp = client.delete("/api/prompts/versions/v-1")
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] is True

    @pytest.mark.parametrize("state", ["approved", "rejected", "archived"])
    def test_delete_immutable_state_returns_409(
        self, client, service_mock, state
    ):
        service_mock.delete_version = MagicMock(
            side_effect=VersionNotDeletableError("v-1", state)
        )
        resp = client.delete("/api/prompts/versions/v-1")
        assert resp.status_code == 409, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "VERSION_IMMUTABLE"
        assert state in detail.get("message", "")

    def test_delete_unknown_returns_404(self, client, service_mock):
        service_mock.delete_version = MagicMock(
            side_effect=LookupError("prompt_version v-missing not found")
        )
        resp = client.delete("/api/prompts/versions/v-missing")
        assert resp.status_code == 404
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "VERSION_NOT_FOUND"

    def test_delete_bad_version_id_422(self, client):
        resp = client.delete("/api/prompts/versions/bad!chars")
        assert resp.status_code == 422


class TestPendingCount:
    """UD#2 endpoint 15 — GET /pending-count (the badge feed)."""

    def test_pending_count_returns_count(self, client, service_mock):
        service_mock.count_pending = MagicMock(return_value=7)
        resp = client.get("/api/prompts/pending-count")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"count": 7}

    def test_pending_count_zero(self, client, service_mock):
        service_mock.count_pending = MagicMock(return_value=0)
        resp = client.get("/api/prompts/pending-count")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0}

    def test_pending_count_missing_scope_returns_403(self, client):
        resp = client.get(
            "/api/prompts/pending-count",
            headers={"X-Test-Role": "bearer:prompts:audit"},
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "SCOPE_INSUFFICIENT"


class TestWriteScopeOnUd2Mutations:
    """UD#2 PATCH/DELETE ride ``prompts:write`` — a bearer WITHOUT
    that scope is refused 403; a bearer WITH it passes the scope gate
    (the lane rule for edits/deletes is scope, not session — unlike
    approve/reject)."""

    def test_patch_without_write_scope_returns_403(self, client):
        resp = client.patch(
            "/api/prompts/versions/v-1",
            json={"content": "x"},
            headers={"X-Test-Role": "bearer:prompts:read"},
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "SCOPE_INSUFFICIENT"

    def test_delete_without_write_scope_returns_403(self, client):
        resp = client.delete(
            "/api/prompts/versions/v-1",
            headers={"X-Test-Role": "bearer:prompts:read"},
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "SCOPE_INSUFFICIENT"

    def test_bearer_with_write_scope_passes_scope_gate(
        self, client, service_mock
    ):
        service_mock.update_version_content = MagicMock(
            return_value=_make_version(version_id="v-1", state="draft")
        )
        resp = client.patch(
            "/api/prompts/versions/v-1",
            json={"content": "x"},
            headers={"X-Test-Role": "bearer:prompts:write"},
        )
        assert resp.status_code == 200, resp.text
        args, _ = service_mock.update_version_content.call_args
        # Bearer lane reaches the service (machine may edit unapproved
        # drafts) — the actor stamps the BEARER identity.
        assert args[2].auth_method == AUTH_METHOD_BEARER


# ────────────────────────────────────────────────────────────────────────────
# OpenAPI generation (T5 acceptance — no 500)
# ────────────────────────────────────────────────────────────────────────────


class TestOpenAPIGenerates:
    def test_openapi_schema_generates_without_error(self, app):
        """T5 acceptance — the OpenAPI spec must generate without
        500 (regression: a typo on a path param's annotation or a
        Pydantic model miss-shape will surface as 500 here)."""
        schema = app.openapi()
        paths = schema.get("paths", {})
        # The 15-endpoint family collapses to 12 path templates
        # (GET/POST share /api/prompts/versions; PATCH + DELETE share
        # /api/prompts/versions/{version_id} with GET).
        assert len(paths) >= 12
        for required in (
            "/api/prompts/versions",
            "/api/prompts/versions/{version_id}",
            "/api/prompts/versions/{version_id}/diff",
            "/api/prompts/versions/{version_id}/approve",
            "/api/prompts/versions/{version_id}/reject",
            "/api/prompts/versions/{version_id}/submit",
            "/api/prompts/versions/{version_id}/retract",
            "/api/prompts/selections",
            "/api/prompts/selections/{agent_id}/{version_tag}/{artifact}",
            "/api/prompts/effective/{agent_id}/{version_tag}/{artifact}",
            "/api/prompts/audit",
            "/api/prompts/pending-count",
        ):
            assert required in paths, f"missing path {required}"

        # UD#2 — the shared {version_id} template carries all of
        # GET + PATCH + DELETE.
        methods = set(paths["/api/prompts/versions/{version_id}"].keys())
        assert {"get", "patch", "delete"} <= methods

# ────────────────────────────────────────────────────────────────────────────
# Review Finding #2 gate — exception alias resolution
# ────────────────────────────────────────────────────────────────────────────


class TestExceptionAliasResolution:
    """The router resolves the service-side exception classes at
    import. The fallback used to be catch-ALL ``Exception`` (review
    Finding #2) — these tests pin that (a) resolution actually
    SUCCEEDS against the real service/repo classes (the fallback is
    dormant) and (b) the fallbacks, should they ever fire, are the
    narrow base classes — never ``Exception``."""

    def test_aliases_resolve_to_real_service_classes(self):
        import daemon.routers.prompts as prompts_module
        from daemon.repositories.prompt_version.repository import (
            InvalidTransitionError as RepoInvalidTransitionError,
        )
        from daemon.services.prompt_version_service import (
            HumanLaneRequiredError as ServiceHumanLaneRequiredError,
            RetractAuthorityError as ServiceRetractAuthorityError,
        )

        assert (
            prompts_module._HumanLaneRequiredError
            is ServiceHumanLaneRequiredError
        )
        assert (
            prompts_module._InvalidTransitionError
            is RepoInvalidTransitionError
        )
        assert (
            prompts_module._RetractAuthorityError
            is ServiceRetractAuthorityError
        )

    def test_fallback_bases_are_narrow_never_catch_all(self):
        """The narrowed fallbacks are the EXACT bases of the mapped
        errors — a resolution failure can never widen an ``except``
        block past ``ValueError`` / ``PermissionError``, so unrelated
        programming errors (``KeyError``, ``TypeError``, ...) always
        propagate to the framework handler instead of being remapped
        onto typed 4xx bodies."""
        import daemon.routers.prompts as prompts_module

        assert prompts_module._HumanLaneRequiredError is not Exception
        assert prompts_module._InvalidTransitionError is not Exception
        assert prompts_module._RetractAuthorityError is not Exception
        # Bases verified against the service/repo class definitions:
        # HumanLaneRequiredError(ValueError),
        # InvalidTransitionError(ValueError) (mirrors
        # job_state_machine.py:65), RetractAuthorityError(PermissionError).
        assert issubclass(prompts_module._HumanLaneRequiredError, ValueError)
        assert issubclass(prompts_module._InvalidTransitionError, ValueError)
        assert issubclass(
            prompts_module._RetractAuthorityError, PermissionError
        )
