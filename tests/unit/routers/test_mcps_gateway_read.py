"""BE tests for the remote-hands gateway READ surface (Phase C).

The three read-only endpoints of the OAuth API lane —
``GET /api/mcps-gateway/{machines,sessions,audit}`` — are the FE-facing
contract of the gateway's operator page. This module pins:

* the EXACT payload shape of every endpoint (the field names are the
  contract the frontend types against);
* the AUTH LANE: the OAuth2 middleware owns these routes (unauthenticated
  → the standard 401/302 contract) and every route carries the
  ``mcp:read`` scope (a granted-but-insufficient credential → 403
  SCOPE_INSUFFICIENT);
* the PAGINATION bounds of the audit tail (limit/offset) — the 422
  envelope of the out-of-range values and the empty-tail answer of an
  offset beyond the end;
* the READ-ONLY posture: the family exposes GET and nothing else;
* the DEGRADED posture: the gateway OFF answers an empty payload with
  ``gateway_enabled: false`` (the routes are registered unconditionally),
  and an unwired service answers 503 rather than a silent empty list.

Harness: a real :class:`McpGatewayService` with a PINNED clock, the
in-memory roster and a memory audit sink — no daemon, no DB, no port.
The auth legs build a real ``create_app(auth_config=...)`` (the
production wiring) with the P2 SQLite scratch store.
"""

from __future__ import annotations

import hashlib
import warnings

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from daemon.api import create_app
from daemon.repositories.auth_session.models import BearerToken
from daemon.routers.mcps_gateway_read import (
    AUDIT_DEFAULT_LIMIT,
    AUDIT_MAX_LIMIT,
    router as mcps_gateway_read_router,
)
from daemon.services.mcps_gateway.audit import AuditRecord, MemoryAuditSink
from daemon.services.mcps_gateway.config import GatewayConfig
from daemon.services.mcps_gateway.service import McpGatewayService
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

#: The pinned instant of the fixture clock (epoch seconds).
T0 = 1_700_000_000.0

#: The heartbeat timeout of the fixture config (the default 45 s).
HEARTBEAT_TIMEOUT_MS = 45_000


class _Clock:
    """A pinnable clock (the service's injectable one)."""

    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _build_service(
    *,
    enabled: bool = True,
    audit_sink: MemoryAuditSink | None = None,
) -> tuple[McpGatewayService, _Clock, MemoryAuditSink]:
    """A real gateway service with a pinned clock and a populated state.

    The roster carries three machines (online+busy, offline, stale) and
    one live session — enough for every presence/lock branch.
    """
    clock = _Clock()
    sink = audit_sink if audit_sink is not None else MemoryAuditSink()
    service = McpGatewayService(
        config=GatewayConfig(
            enabled=enabled,
            heartbeat_timeout_ms=HEARTBEAT_TIMEOUT_MS,
        ),
        audit_sink=sink,
        clock=clock,
    )

    # mac-studio-01 — connected, heartbeating, holds the only session.
    service.store.upsert("mac-studio-01", "conn-1", T0)
    service.store.heartbeat("mac-studio-01", T0)
    service.sessions.begin(
        session_id="sess-abc",
        machine_id="mac-studio-01",
        agent_id="leader",
        instance_id="inst-1",
        connection_id="conn-1",
        catalog=[{"name": "computer_click"}, {"name": "computer_type"}],
        now=T0,
    )
    service.store.bind_session("mac-studio-01", "sess-abc", "leader")

    # mac-mini-02 — connected, then the socket died.
    service.store.upsert("mac-mini-02", "conn-2", T0)
    service.store.mark_offline("mac-mini-02", "conn-2")

    # mac-air-03 — connected, silent past the heartbeat timeout.
    service.store.upsert("mac-air-03", "conn-3", T0)

    for index, minute in enumerate((0, 1, 2)):
        sink.record(
            AuditRecord(
                agent_id="leader",
                machine_id="mac-studio-01",
                tool=f"computer_tool_{index}",
                args_hash=hashlib.sha256(f"args-{index}".encode()).hexdigest(),
                outcome="ok" if index != 1 else "error",
                instance_id="inst-1",
                session_id="sess-abc",
                error_code="" if index != 1 else "TOOL_TIMEOUT",
                created_at=f"2026-10-22T07:0{minute}:00.000000+00:00",
            )
        )
    return service, clock, sink


def _read_app(service: McpGatewayService) -> FastAPI:
    """A bare app with the read router mounted under ``/api``.

    Auth is OFF on this harness (no ``app.state.auth_config``), so the
    ``@require_scope`` decoration is INERT — the auth legs below use the
    real ``create_app`` instead.
    """
    app = FastAPI()
    app.include_router(mcps_gateway_read_router, prefix="/api")
    app.state.mcps_gateway_service = service
    return app


@pytest.fixture()
def read_client() -> tuple[TestClient, _Clock]:
    service, clock, _ = _build_service()
    # The stale machine is silent for 120 s — past the 45 s timeout.
    clock.now = T0 + 120.0
    # …while the busy machine heartbeated just now (it stays ONLINE).
    service.store.heartbeat("mac-studio-01", clock.now)
    return TestClient(_read_app(service)), clock


# ── payload shapes ────────────────────────────────────────────────────────


class TestMachinesShape:
    def test_body_and_item_keys_are_exact(self, read_client):
        client, _ = read_client
        body = client.get("/api/mcps-gateway/machines").json()
        assert set(body.keys()) == {
            "machines",
            "count",
            "gateway_enabled",
            "degraded",
            "error",
        }
        assert body["count"] == 3
        assert body["gateway_enabled"] is True
        # §2.D2.1 #1 — the VALUE is pinned, not the key's presence: a
        # healthy machines read must never report degraded.
        assert body["degraded"] is False
        assert body["error"] is None
        assert set(body["machines"][0].keys()) == {
            "machine_id",
            "presence",
            "online",
            "connected",
            "last_seen_at",
            "session_id",
            "agent_id",
            "busy",
        }

    def test_presence_states_and_session_pointer(self, read_client):
        client, _ = read_client
        body = client.get("/api/mcps-gateway/machines").json()
        by_id = {m["machine_id"]: m for m in body["machines"]}

        studio = by_id["mac-studio-01"]
        assert studio["presence"] == "online"
        assert studio["online"] is True
        assert studio["connected"] is True
        assert studio["session_id"] == "sess-abc"
        assert studio["agent_id"] == "leader"
        assert studio["busy"] is True
        assert studio["last_seen_at"] == "2023-11-14T22:15:20+00:00"

        mini = by_id["mac-mini-02"]
        assert mini["presence"] == "offline"
        assert mini["online"] is False
        assert mini["connected"] is False
        assert mini["busy"] is False
        assert mini["session_id"] is None

        air = by_id["mac-air-03"]
        assert air["presence"] == "stale"
        assert air["online"] is False
        assert air["connected"] is True
        assert air["busy"] is False

    def test_roster_is_empty_when_nothing_enrolled(self):
        clock = _Clock()
        empty = McpGatewayService(
            config=GatewayConfig(enabled=True),
            audit_sink=MemoryAuditSink(),
            clock=clock,
        )
        client = TestClient(_read_app(empty))
        body = client.get("/api/mcps-gateway/machines").json()
        # §2.D2.1 #2 — the exact healthy body, degraded arms included.
        assert body == {
            "machines": [],
            "count": 0,
            "gateway_enabled": True,
            "degraded": False,
            "error": None,
        }


class TestSessionsShape:
    def test_body_and_item_keys_are_exact(self, read_client):
        client, _ = read_client
        body = client.get("/api/mcps-gateway/sessions").json()
        assert set(body.keys()) == {
            "sessions",
            "count",
            "gateway_enabled",
            "degraded",
            "error",
        }
        assert body["count"] == 1
        assert body["gateway_enabled"] is True
        # §2.D2.1 #3 — the in-memory lane is permanently degraded:false.
        assert body["degraded"] is False
        assert body["error"] is None
        assert set(body["sessions"][0].keys()) == {
            "session_id",
            "machine_id",
            "agent_id",
            "instance_id",
            "state",
            "machine_locked",
            "started_at",
            "last_activity_at",
            "expires_at",
            "tools",
        }

    def test_session_fields(self, read_client):
        client, _ = read_client
        session = client.get("/api/mcps-gateway/sessions").json()["sessions"][0]
        assert session["session_id"] == "sess-abc"
        assert session["machine_id"] == "mac-studio-01"
        assert session["agent_id"] == "leader"
        assert session["instance_id"] == "inst-1"
        assert session["state"] == "live"
        # The §8.3 lock is held by this session → the machine is BUSY.
        assert session["machine_locked"] is True
        assert session["tools"] == ["computer_click", "computer_type"]
        assert session["started_at"].endswith("+00:00")
        assert session["expires_at"] > session["started_at"]


class TestAuditShape:
    def test_body_and_item_keys_are_exact(self, read_client):
        client, _ = read_client
        body = client.get("/api/mcps-gateway/audit").json()
        assert set(body.keys()) == {
            "audit",
            "count",
            "limit",
            "offset",
            "gateway_enabled",
            "degraded",
            "error",
        }
        assert body["count"] == 3
        assert body["limit"] == AUDIT_DEFAULT_LIMIT
        assert body["offset"] == 0
        assert body["gateway_enabled"] is True
        # §2.D2.1 #4 — the healthy tail is degraded:false, by VALUE.
        assert body["degraded"] is False
        assert body["error"] is None
        assert set(body["audit"][0].keys()) == {
            "timestamp",
            "agent_id",
            "instance_id",
            "machine_id",
            "tool",
            "outcome",
            "session_id",
            "error_code",
            "args_hash",
        }

    def test_newest_first(self, read_client):
        client, _ = read_client
        rows = client.get("/api/mcps-gateway/audit").json()["audit"]
        assert [r["timestamp"] for r in rows] == [
            "2026-10-22T07:02:00.000000+00:00",
            "2026-10-22T07:01:00.000000+00:00",
            "2026-10-22T07:00:00.000000+00:00",
        ]
        assert rows[0]["tool"] == "computer_tool_2"
        assert rows[1]["outcome"] == "error"
        assert rows[1]["error_code"] == "TOOL_TIMEOUT"
        assert rows[0]["args_hash"]


# ── pagination bounds ─────────────────────────────────────────────────────


class TestAuditPagination:
    @pytest.mark.parametrize("limit", [0, -1, AUDIT_MAX_LIMIT + 1, 10_000])
    def test_out_of_range_limit_is_422(self, read_client, limit):
        client, _ = read_client
        r = client.get(f"/api/mcps-gateway/audit?limit={limit}")
        assert r.status_code == 422

    def test_negative_offset_is_422(self, read_client):
        client, _ = read_client
        assert client.get("/api/mcps-gateway/audit?offset=-1").status_code == 422

    def test_offset_beyond_the_end_is_an_empty_tail(self, read_client):
        client, _ = read_client
        body = client.get("/api/mcps-gateway/audit?offset=999").json()
        assert body["audit"] == []
        assert body["count"] == 0
        assert body["offset"] == 999

    def test_limit_and_offset_walk_the_tail(self, read_client):
        client, _ = read_client
        first = client.get("/api/mcps-gateway/audit?limit=1").json()
        assert first["count"] == 1
        assert first["audit"][0]["tool"] == "computer_tool_2"

        second = client.get("/api/mcps-gateway/audit?limit=1&offset=1").json()
        assert second["count"] == 1
        assert second["audit"][0]["tool"] == "computer_tool_1"
        assert second["offset"] == 1

    def test_max_limit_is_accepted(self, read_client):
        client, _ = read_client
        r = client.get(f"/api/mcps-gateway/audit?limit={AUDIT_MAX_LIMIT}")
        assert r.status_code == 200
        assert r.json()["count"] == 3


# ── read-only posture ─────────────────────────────────────────────────────


class TestReadOnlyPosture:
    def test_the_family_exposes_get_only(self):
        methods = {
            method
            for route in mcps_gateway_read_router.routes
            for method in getattr(route, "methods", set())
        }
        assert methods == {"GET"}

    def test_no_write_route_exists(self, read_client):
        client, _ = read_client
        for path in ("/api/mcps-gateway/machines", "/api/mcps-gateway/sessions"):
            assert client.post(path, json={}).status_code == 405
            assert client.delete(path).status_code == 405
        assert client.post("/api/mcps-gateway/audit", json={}).status_code == 405
        assert client.put("/api/mcps-gateway/audit").status_code == 405


# ── degraded / disabled postures ──────────────────────────────────────────


class TestDegradedPostures:
    def test_gateway_disabled_answers_an_empty_payload(self):
        """A daemon that booted with the gateway OFF never enrolls a
        machine (the transport does not exist) — the payload is the
        empty shape and the flag says WHY."""
        clock = _Clock()
        service = McpGatewayService(
            config=GatewayConfig(enabled=False),
            audit_sink=MemoryAuditSink(),
            clock=clock,
        )
        client = TestClient(_read_app(service))
        machines = client.get("/api/mcps-gateway/machines").json()
        # §2.D2.1 #5 — the disabled-gateway shape. This pin carries the
        # disabled ≠ degraded orthogonality (AD4): the transport being
        # OFF is not a degraded read.
        assert machines == {
            "machines": [],
            "count": 0,
            "gateway_enabled": False,
            "degraded": False,
            "error": None,
        }

        sessions = client.get("/api/mcps-gateway/sessions").json()
        # §2.D2.1 #6 — as #5, for the sessions envelope.
        assert sessions == {
            "sessions": [],
            "count": 0,
            "gateway_enabled": False,
            "degraded": False,
            "error": None,
        }

    def test_a_disabled_gateway_still_reports_the_flag_honestly(self):
        """The flag describes the TRANSPORT, not the payload: a roster
        that survived a kill-switch flip still reports the transport as
        disabled."""
        service, _clock, _ = _build_service(enabled=False)
        client = TestClient(_read_app(service))
        body = client.get("/api/mcps-gateway/machines").json()
        assert body["gateway_enabled"] is False

    def test_unwired_service_is_503_not_a_silent_empty_list(self):
        app = FastAPI()
        app.include_router(mcps_gateway_read_router, prefix="/api")
        client = TestClient(app)
        r = client.get("/api/mcps-gateway/machines")
        assert r.status_code == 503
        assert r.json()["detail"]["error"] == "gateway_unavailable"


# ── the degraded audit lane (D2 — the outage shape, as a BODY) ────────────


#: The deterministic unreachable DSN (plan §4 construction 3): port 1
#: refuses the connection instantly — no external service, no
#: flaky-port race.
#:
#: The earlier ``engine.dispose()`` construction was a FALSE PREMISE
#: (dispose merely refreshes the pool; the next read succeeds) — this
#: replaces it.
_UNREACHABLE_DSN = "postgresql+psycopg://127.0.0.1:1/ensemble_dev_test_only"

#: The exact degraded body of the audit lane (the ONE shape the outage
#: takes — 200, never 500 and never 503).
_DEGRADED_AUDIT_BODY = {
    "audit": [],
    "count": 0,
    "limit": AUDIT_DEFAULT_LIMIT,
    "offset": 0,
    "gateway_enabled": True,
    "degraded": True,
    "error": "audit_unavailable",
}


def _service_with_sink(sink, *, read_timeout_ms: int = 5_000) -> McpGatewayService:
    return McpGatewayService(
        config=GatewayConfig(
            enabled=True,
            heartbeat_timeout_ms=HEARTBEAT_TIMEOUT_MS,
            read_timeout_ms=read_timeout_ms,
        ),
        audit_sink=sink,
        clock=_Clock(),
    )


class TestDegradedAuditLane:
    def test_audit_read_degraded_on_unreachable_engine(self):
        """Plan §4 construction 1+3 — the sink is the REAL repository
        on an unreachable engine. The repository no longer swallows
        (AD5), the service classifies, and the endpoint answers the
        degraded body — 200, not 500 and not 503."""
        from sqlalchemy import create_engine

        from daemon.repositories.mcps_gateway.repository import (
            McpGatewayAuditRepository,
        )

        engine = create_engine(_UNREACHABLE_DSN)
        try:
            service = _service_with_sink(McpGatewayAuditRepository(engine))
            client = TestClient(_read_app(service))
            r = client.get("/api/mcps-gateway/audit")
            assert r.status_code == 200
            assert r.json() == _DEGRADED_AUDIT_BODY
        finally:
            engine.dispose()

    def test_machines_and_sessions_stay_healthy_on_the_same_outage(self):
        """Orthogonality + truthful symmetry: the machines/sessions
        lanes are IN-MEMORY, so the DB outage that degrades the audit
        lane leaves them healthy — permanently ``degraded: false``,
        with ``gateway_enabled`` unchanged."""
        from sqlalchemy import create_engine

        from daemon.repositories.mcps_gateway.repository import (
            McpGatewayAuditRepository,
        )

        engine = create_engine(_UNREACHABLE_DSN)
        try:
            service = _service_with_sink(McpGatewayAuditRepository(engine))
            client = TestClient(_read_app(service))
            machines = client.get("/api/mcps-gateway/machines").json()
            assert machines["degraded"] is False
            assert machines["gateway_enabled"] is True
            sessions = client.get("/api/mcps-gateway/sessions").json()
            assert sessions["degraded"] is False
            assert sessions["gateway_enabled"] is True
        finally:
            engine.dispose()

    def test_audit_read_degraded_on_raising_reader(self, monkeypatch):
        """Plan §4 construction 2 — a monkeypatched reader. The SAME
        body arrives through the service classification path, proving
        the classification lives in ``audit_tail`` — and the raw
        exception string (which carries a secret) never leaks."""
        sink = MemoryAuditSink()
        service = _service_with_sink(sink)

        def _raising(**_kwargs):
            raise RuntimeError("boom postgresql://secret-user:pw@host/db")

        monkeypatch.setattr(sink, "list_recent", _raising)
        client = TestClient(_read_app(service))
        r = client.get("/api/mcps-gateway/audit")
        assert r.status_code == 200
        assert r.json() == _DEGRADED_AUDIT_BODY
        assert "secret-user" not in r.text

    async def test_audit_read_degraded_on_hung_reader(self, monkeypatch):
        """Plan §4 construction 4 — the class constructions 1-3 do NOT
        cover: a reader that HANGS instead of raising. The read is
        bounded by ``MCPS_GATEWAY_READ_TIMEOUT_MS``; the answer is the
        SAME degraded body, and the wall clock is bounded by the
        timeout — not by the reader's sleep.

        Harness note: this one test uses ``httpx.AsyncClient`` with
        ``ASGITransport`` (the repo's established pattern,
        ``tests/test_daemon_settings_api.py``) instead of ``TestClient``
        — starlette's blocking portal JOINS the loop's default executor
        on request teardown, so a ``TestClient`` wall clock would wait
        for the sleeping reader thread and measure the JOIN, not the
        bound. The portal-free transport returns when the HANDLER
        returns, which is the production behaviour (uvicorn never joins
        the executor per request).
        """
        import time

        import httpx

        sink = MemoryAuditSink()
        service = _service_with_sink(sink, read_timeout_ms=200)

        def _hanging(**_kwargs):
            time.sleep(2.0)  # 10x the read timeout
            return []

        monkeypatch.setattr(sink, "list_recent", _hanging)
        transport = httpx.ASGITransport(app=_read_app(service))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            started = time.monotonic()
            r = await client.get("/api/mcps-gateway/audit")
            elapsed = time.monotonic() - started
        assert r.status_code == 200
        body = r.json()
        assert body["degraded"] is True
        assert body["error"] == "audit_unavailable"
        assert body["audit"] == []
        assert elapsed < 1.5, f"the read must be bounded, took {elapsed:.2f}s"

    def test_a_write_only_sink_is_not_degraded(self):
        """Negative control — a write-only sink (audit disabled /
        ``NullAuditSink``) is NOT an outage: an empty tail with
        ``degraded: false``."""
        from daemon.services.mcps_gateway.audit import NullAuditSink

        service = _service_with_sink(NullAuditSink())
        rows, degraded, reason = service.audit_tail(limit=10, offset=0)
        assert rows == []
        assert degraded is False
        assert reason is None


# ── the auth lane ─────────────────────────────────────────────────────────


def _mint_bearer(engine, *, label: str, scopes: str) -> str:
    token = f"test-token-{label}"
    row = BearerToken(
        id_hash=hashlib.sha256(token.encode()).hexdigest(),
        subject=label,
        scopes=scopes,
        label=label,
        created_at="2026-10-11T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
        last_used_at=None,
        revoked_at=None,
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()
    return token


@pytest.fixture()
def auth_client(tmp_path):
    """A real create_app(auth ON) with a wired gateway service."""
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    service, clock, _ = _build_service()
    app.state.mcps_gateway_service = service
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)
    return client, app


class TestAuthLane:
    def test_unauthenticated_json_request_is_401(self, auth_client):
        client, _ = auth_client
        for path in (
            "/api/mcps-gateway/machines",
            "/api/mcps-gateway/sessions",
            "/api/mcps-gateway/audit",
        ):
            r = client.get(path, headers={"Accept": "application/json"})
            assert r.status_code == 401, path
            assert r.json() == {
                "error": "AUTH_REQUIRED",
                "message": "Authentication required",
            }
            assert r.headers["www-authenticate"] == 'Bearer realm="ensemble"'

    def test_unauthenticated_browser_request_is_redirected_to_login(
        self, auth_client
    ):
        client, _ = auth_client
        r = client.get("/api/mcps-gateway/machines")
        assert r.status_code == 302
        assert r.headers["location"].startswith("/api/auth/login?return_to=")

    def test_bearer_with_mcp_read_is_200(self, auth_client):
        client, app = auth_client
        token = _mint_bearer(
            app.state.auth_session_repository._engine,
            label="gateway-reader",
            scopes="mcp:read",
        )
        r = client.get(
            "/api/mcps-gateway/machines",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["count"] == 3

    def test_bearer_without_mcp_read_is_403(self, auth_client):
        client, app = auth_client
        token = _mint_bearer(
            app.state.auth_session_repository._engine,
            label="jobs-reader",
            scopes="jobs:read",
        )
        r = client.get(
            "/api/mcps-gateway/machines",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "SCOPE_INSUFFICIENT"
        assert r.json()["detail"]["required"] == ["mcp:read"]

    def test_invalid_bearer_is_401(self, auth_client):
        client, _ = auth_client
        r = client.get(
            "/api/mcps-gateway/sessions",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert r.status_code == 401

    def test_the_scope_is_declared_on_every_route(self):
        for route in mcps_gateway_read_router.routes:
            assert getattr(route.endpoint, "required_scopes", None) == (
                "mcp:read",
            ), route.path


def test_the_service_read_accessors_are_public() -> None:
    """The read surface reaches the center through the PUBLIC seams."""
    service, _clock, sink = _build_service()
    assert service.now() == T0
    # §2.D2.1 #12 — audit_tail returns the classified TRIPLE now; the
    # rows themselves are unchanged.
    rows, degraded, reason = service.audit_tail(limit=10, offset=0)
    assert len(rows) == 3
    assert rows[0]["created_at"] == "2026-10-22T07:02:00.000000+00:00"
    assert degraded is False
    assert reason is None
    page, page_degraded, _ = service.audit_tail(limit=1, offset=1)
    assert page[0]["tool"] == "computer_tool_1"
    assert page_degraded is False
    tail, tail_degraded, _ = service.audit_tail(limit=10, offset=99)
    assert tail == []
    assert tail_degraded is False
    assert sink.records  # the fake sink is the one behind the service
