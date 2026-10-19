"""db-backed-settings Phase 4 (t3) — daemon settings API layer.

FastAPI tests for GET/PUT/DELETE ``/api/settings/daemon[/{key}]``
(``daemon/routers/settings.py``) wired through the router's module DI
(``set_settings_service`` + ``set_settings_resolution_snapshot``) with a
temp-SQLite-backed ``SettingsService``.

Fixture shape: modeled on the ``tests/test_scheduler_api.py:22-143``
app/client idiom, with two adaptations documented here as deviations
from the phase-4 plan text:

* the app fixture builds a FRESH FastAPI app mounting ONLY the settings
  router under the production ``/api`` prefix (``daemon/api.py:3928``),
  instead of reusing the ``daemon.api.app`` singleton — the singleton's
  middleware stack is built lazily on first request and cannot take the
  auth-context injection middleware the scope-gate cases require (the
  ``tests/test_prompts_router.py:383-408`` precedent builds fresh
  sub-apps for exactly this reason; no manager is needed on this
  surface — the DI is the only wiring).
* the auth-on fixture is the prompts-family ``_AuthInjectionMiddleware``
  idiom (``tests/test_prompts_router.py:327-381``) +
  ``tests.helpers.auth_fixture.on_auth_config`` — scope gates are inert
  when auth is OFF, so the 403/200 matrix NEEDS auth-on.

Contract under test (frozen in phase-2 t1): GET returns
``DaemonSettingsResponse{settings:[DaemonSettingEntry],
restart_required}`` with ``value`` = boot-applied snapshot truth and
``source`` = LIVE re-resolve (divergence = honest restart-required
reporting); PUT/DELETE return the BARE ``DaemonSettingEntry``; tier
detail fields are OMITTED-when-absent (never nulled) and omitted
ENTIRELY for ``sensitive`` keys.

Fence discipline: all temp state under ``tmp_path``; zero HOME-derived
paths. Env discipline: ``LIMITS_LLM_CONCURRENCY`` is not in the
conftest tracked-env set, so every tier test pins it explicitly via
monkeypatch.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel
from starlette.middleware.base import BaseHTTPMiddleware

from daemon.auth.context import (
    AUTH_METHOD_BEARER,
    AUTH_METHOD_SESSION,
    AuthContext,
)
from daemon.auth.scopes import SCOPE_TAXONOMY
from daemon.constants import CHAT_WORKER_POOL_SIZE, WORKER_POOL_SIZE
from daemon.models.app_settings import AppSetting
from daemon.routers import settings as routers_settings
from daemon.routers.settings import router as daemon_settings_router
from daemon.services import settings_service
from daemon.services.settings_service import MANAGED_KEYS
from tests.helpers.auth_fixture import on_auth_config

KEY = "limits.llm_concurrency"
URL = "/api/settings/daemon"
KEY_URL = f"{URL}/{KEY}"
UNKNOWN_URL = f"{URL}/limits.does_not_exist"

_AUDIT_LOGGER = "daemon.routers.settings"


def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIMITS_LLM_CONCURRENCY", raising=False)


class _AuthInjectionMiddleware(BaseHTTPMiddleware):
    """Stamp ``request.state.auth_context`` from the ``X-Test-Role`` header.

    ``session`` → full operator family (SCOPE_TAXONOMY — includes both
    settings scopes); ``bearer:<csv>`` → exactly the listed scopes;
    anything else → an authenticated context with ZERO scopes (the
    403-without case — never a missing context, which would conflate
    with auth-off).
    """

    async def dispatch(self, request, call_next):
        role = request.headers.get("X-Test-Role", "session")
        if role == "session":
            ctx = AuthContext(
                subject="operator-cli",
                scopes=set(SCOPE_TAXONOMY),
                auth_method=AUTH_METHOD_SESSION,
                expires_at="2099-01-01T00:00:00+00:00",
                meta={},
            )
        elif role.startswith("bearer:"):
            granted = {s for s in role[len("bearer:"):].split(",") if s}
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


def _build_app(*, auth_on: bool) -> FastAPI:
    application = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(daemon_settings_router)
    application.include_router(api)
    if auth_on:
        application.state.auth_config = on_auth_config()
        application.add_middleware(_AuthInjectionMiddleware)
    return application


def _reset_di() -> None:
    """Unwire BOTH module globals (service + snapshot)."""
    routers_settings._settings_service = None
    settings_service._resolution_snapshot = None


def _wire(
    engine,
    *,
    snapshot: dict[str, dict[str, Any]] | None = None,
) -> None:
    if snapshot is None:
        snapshot = {
            KEY: {"default": 10, "env_value": None, "yaml_value": None, "applied_value": 10}
        }
    routers_settings.set_settings_service(settings_service.SettingsService(engine))
    routers_settings.set_settings_resolution_snapshot(snapshot)


def _read_row(engine) -> AppSetting | None:
    with Session(engine) as session:
        return session.get(AppSetting, KEY)


def _install_fake_sensitive_key(
    monkeypatch: pytest.MonkeyPatch,
) -> settings_service.ManagedKeySpec:
    """Register the ``sensitive=True`` fake key for a test.

    Builds the spec AND applies BOTH registry patches — the service
    module's ``MANAGED_KEYS`` and the router's BY-VALUE import of it
    (the endpoint iterates the router's binding) — then returns the
    spec so the test can assert against ``fake.key``.
    """
    fake = settings_service.ManagedKeySpec(
        key="limits.fake_secret",
        config_path="limits.fake_secret",
        env_var="LIMITS_FAKE_SECRET",
        value_type="int",
        default=4,
        ge=1,
        sensitive=True,
        description="fake secret-bearing key (test-only)",
    )
    monkeypatch.setattr(settings_service, "MANAGED_KEYS", {**MANAGED_KEYS, fake.key: fake})
    monkeypatch.setattr(routers_settings, "MANAGED_KEYS", {**MANAGED_KEYS, fake.key: fake})
    return fake


# ────────────────────────────────────────────────────────────────────────────
# Fixtures (scheduler-idiom app/client pairs, auth-off + auth-on variants)
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def settings_engine(tmp_path):
    """Temp-SQLite engine with the app_settings table created."""
    engine = create_engine(f"sqlite:///{tmp_path}/settings_api.db")
    SQLModel.metadata.create_all(engine, tables=[AppSetting.__table__])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def app_off():
    """Auth-OFF app (no auth_config → scope gates inert)."""
    application = _build_app(auth_on=False)
    try:
        yield application
    finally:
        _reset_di()


@pytest.fixture
def app_on():
    """Auth-ON app (auth_config set + context-injection middleware)."""
    application = _build_app(auth_on=True)
    try:
        yield application
    finally:
        _reset_di()


@pytest_asyncio.fixture
async def client_off(app_off, settings_engine):
    _wire(settings_engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_off), base_url="http://test"
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def client_on(app_on, settings_engine):
    _wire(settings_engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_on), base_url="http://test"
    ) as ac:
        yield ac


# ────────────────────────────────────────────────────────────────────────────
# GET — shape + per-tier correctness + value-vs-source divergence
# ────────────────────────────────────────────────────────────────────────────


class TestGetDaemonSettings:
    @pytest.mark.asyncio
    async def test_get_200_shape(self, client_off, monkeypatch):
        _no_env(monkeypatch)
        resp = await client_off.get(URL)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"settings", "restart_required"}
        assert body["restart_required"] is False
        entries = body["settings"]
        # Union merge (origin/latest → feature/resource-cost-control,
        # 2026-10-19): registry-driven entry count + explicit SIX-key
        # membership (a silent drop from either lane is caught). Insertion
        # order — the original ``limits.llm_concurrency`` leads (v1
        # convention; preserved across the union).
        assert len(entries) == len(MANAGED_KEYS)
        keys = [e["key"] for e in entries]
        assert keys[0] == KEY  # insertion order — the original key leads
        # v1 (db-backed-settings) original.
        assert "limits.llm_concurrency" in keys
        # settings-worker-slots lane (latest).
        assert "limits.worker_pool_size" in keys
        assert "limits.chat_worker_pool_size" in keys
        # resource-cost-control O3 (instance-graph cache bounding).
        assert "limits.instance_cache_ttl_hours" in keys
        assert "limits.instance_cache_max_entries" in keys
        # resource-cost-control O2 (chromium render gate).
        assert "limits.chart_render_concurrency" in keys
        entry = entries[0]
        assert entry["key"] == KEY
        assert entry["value"] == 10  # snapshot applied_value (boot truth)
        assert entry["source"] == "default"
        assert entry["default"] == 10
        assert entry["requires_restart"] is True
        assert entry["sensitive"] is False
        assert isinstance(entry["description"], str) and entry["description"]
        # Omitted-when-absent (never nulled): no tier detail keys at all.
        for absent in ("env_value", "yaml_value", "db_override"):
            assert absent not in entry

    @pytest.mark.asyncio
    async def test_get_env_tier(self, client_off, monkeypatch):
        monkeypatch.setenv("LIMITS_LLM_CONCURRENCY", "6")
        resp = await client_off.get(URL)
        assert resp.status_code == 200
        entry = resp.json()["settings"][0]
        assert entry["source"] == "env"
        assert entry["env_value"] == 6
        assert entry["value"] == 10  # boot-applied truth unchanged

    @pytest.mark.asyncio
    async def test_get_yaml_tier(self, client_off, monkeypatch, settings_engine):
        _no_env(monkeypatch)
        _wire(
            settings_engine,
            snapshot={KEY: {"default": 10, "env_value": None, "yaml_value": 9, "applied_value": 9}},
        )
        resp = await client_off.get(URL)
        assert resp.status_code == 200
        entry = resp.json()["settings"][0]
        assert entry["source"] == "yaml"
        assert entry["yaml_value"] == 9

    @pytest.mark.asyncio
    async def test_get_db_tier_with_restart_flag(self, client_off, monkeypatch):
        _no_env(monkeypatch)
        put = await client_off.put(KEY_URL, json={"value": 6})
        assert put.status_code == 200, put.text
        resp = await client_off.get(URL)
        body = resp.json()
        entry = body["settings"][0]
        assert entry["source"] == "db"
        assert entry["db_override"] == 6
        assert body["restart_required"] is True
        # value stays the BOOT-APPLIED truth even with a live db row
        # written post-boot with a different number.
        assert entry["value"] == 10

    @pytest.mark.asyncio
    async def test_get_value_vs_source_divergence_post_delete(
        self, client_off, monkeypatch, settings_engine
    ):
        """THE divergence case: boot applied 20 (snapshot carriage); the
        override is deleted at runtime → value STILL reports the boot-applied
        20 while source live-re-resolves to the new lower tier."""
        _no_env(monkeypatch)
        _wire(
            settings_engine,
            snapshot={KEY: {"default": 10, "env_value": None, "yaml_value": None, "applied_value": 20}},
        )
        put = await client_off.put(KEY_URL, json={"value": 20})
        assert put.status_code == 200
        got = await client_off.get(URL)
        entry = got.json()["settings"][0]
        assert entry["value"] == 20 and entry["source"] == "db"

        deleted = await client_off.delete(KEY_URL)
        assert deleted.status_code == 200
        entry = (await client_off.get(URL)).json()["settings"][0]
        assert entry["value"] == 20  # boot-applied truth carried by the snapshot
        assert entry["source"] == "default"  # live re-resolve fell down the ladder
        assert "db_override" not in entry


# ────────────────────────────────────────────────────────────────────────────
# Scope gates (auth-on fixture)
# ────────────────────────────────────────────────────────────────────────────


class TestScopeGates:
    def test_both_scopes_registered_in_taxonomy(self):
        assert "settings:read" in SCOPE_TAXONOMY
        assert "settings:write" in SCOPE_TAXONOMY

    @pytest.mark.asyncio
    async def test_get_403_without_read_scope_200_with(self, client_on, monkeypatch):
        _no_env(monkeypatch)
        denied = await client_on.get(URL, headers={"X-Test-Role": "empty-scopes"})
        assert denied.status_code == 403
        assert denied.json()["detail"]["error"] == "SCOPE_INSUFFICIENT"
        allowed = await client_on.get(URL, headers={"X-Test-Role": "session"})
        assert allowed.status_code == 200
        # The bearer lane with EXACTLY settings:read also passes.
        bearer = await client_on.get(URL, headers={"X-Test-Role": "bearer:settings:read"})
        assert bearer.status_code == 200

    @pytest.mark.asyncio
    async def test_put_delete_403_without_write_scope(self, client_on, monkeypatch):
        _no_env(monkeypatch)
        put = await client_on.put(KEY_URL, json={"value": 6}, headers={"X-Test-Role": "empty-scopes"})
        assert put.status_code == 403
        delete = await client_on.delete(KEY_URL, headers={"X-Test-Role": "empty-scopes"})
        assert delete.status_code == 403
        # …and pass with exactly settings:write on the bearer lane.
        ok_put = await client_on.put(
            KEY_URL, json={"value": 6}, headers={"X-Test-Role": "bearer:settings:write"}
        )
        assert ok_put.status_code == 200
        ok_delete = await client_on.delete(
            KEY_URL, headers={"X-Test-Role": "bearer:settings:write"}
        )
        assert ok_delete.status_code == 200

    @pytest.mark.asyncio
    async def test_read_scope_does_not_grant_write(self, client_on, monkeypatch):
        _no_env(monkeypatch)
        put = await client_on.put(
            KEY_URL, json={"value": 6}, headers={"X-Test-Role": "bearer:settings:read"}
        )
        assert put.status_code == 403


# ────────────────────────────────────────────────────────────────────────────
# Sensitive masking + v1 pin
# ────────────────────────────────────────────────────────────────────────────


class TestSensitiveMasking:
    @pytest.mark.asyncio
    async def test_sensitive_key_omits_tier_detail_fields(
        self, client_off, monkeypatch, settings_engine
    ):
        """A ``sensitive=True`` registry entry yields a GET payload
        OMITTING env_value/yaml_value/db_override (omitted-not-nulled)
        even when those tiers are POPULATED (env set + db row written)."""
        _no_env(monkeypatch)
        fake = _install_fake_sensitive_key(monkeypatch)
        monkeypatch.setenv("LIMITS_FAKE_SECRET", "9")

        get = await client_off.get(URL)
        assert get.status_code == 200, get.text
        by_key = {e["key"]: e for e in get.json()["settings"]}
        masked = by_key["limits.fake_secret"]
        assert masked["sensitive"] is True
        assert masked["source"] == "env"  # tier NAME still reported…
        for absent in ("env_value", "yaml_value", "db_override"):
            assert absent not in masked  # …but the tier VALUES are omitted

        # Write a db override for the sensitive key via the API…
        put = await client_off.put("/api/settings/daemon/limits.fake_secret", json={"value": 5})
        assert put.status_code == 200, put.text
        assert "db_override" not in put.json()  # masked on the write response too
        # …and the row lands (source=db) with db_override STILL omitted.
        get2 = await client_off.get(URL)
        masked2 = {e["key"]: e for e in get2.json()["settings"]}["limits.fake_secret"]
        assert masked2["source"] == "db"
        for absent in ("env_value", "yaml_value", "db_override"):
            assert absent not in masked2

        # v1 pin: the REAL managed key is sensitive=False and UNMASKED.
        v1 = by_key[KEY]
        assert v1["sensitive"] is False

    @pytest.mark.asyncio
    async def test_put_audit_line_redacts_sensitive_value(
        self, client_off, monkeypatch, settings_engine, caplog
    ):
        """The PUT audit line redacts a SENSITIVE key's value term to
        the literal ``<redacted>`` (grep-able ``value=`` term kept)
        while STILL carrying the subject — the audit-log surface of
        the ``sensitive=True`` tripwire (registry docstring)."""
        _no_env(monkeypatch)
        fake = _install_fake_sensitive_key(monkeypatch)

        with caplog.at_level(logging.INFO, logger=_AUDIT_LOGGER):
            put = await client_off.put(
                "/api/settings/daemon/limits.fake_secret", json={"value": 5}
            )
        assert put.status_code == 200, put.text
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        audit = [m for m in infos if "db override set" in m and fake.key in m]
        assert len(audit) == 1
        # Subject still logged; value term present but REDACTED — the
        # raw parsed value never reaches the log line.
        assert "auth-off" in audit[0]
        assert "<redacted>" in audit[0]
        assert "value=5" not in audit[0]

        # Contrast: the NON-sensitive real key keeps the raw value=%r.
        with caplog.at_level(logging.INFO, logger=_AUDIT_LOGGER):
            put2 = await client_off.put(KEY_URL, json={"value": 6})
        assert put2.status_code == 200, put2.text
        infos2 = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        audit2 = [m for m in infos2 if "db override set" in m and KEY in m]
        assert len(audit2) == 1
        assert "value=6" in audit2[0]

    @pytest.mark.asyncio
    async def test_v1_registry_entry_pins(self):
        spec = MANAGED_KEYS[KEY]
        assert spec.sensitive is False
        assert spec.env_var == "LIMITS_LLM_CONCURRENCY"
        assert spec.default == 10
        assert spec.ge == 1
        assert spec.requires_restart is True

    @pytest.mark.asyncio
    async def test_worker_slot_registry_entry_pins(self):
        """settings-worker-slots (D1/D3): the two new pool keys mirror their
        ``LimitsConfig`` Fields — env_var / default / ge / requires_restart /
        sensitive. ``effective_max`` is REMOVED (D3) — no entry carries it."""
        worker = MANAGED_KEYS["limits.worker_pool_size"]
        assert worker.env_var == "LIMITS_WORKER_POOL_SIZE"
        assert worker.config_path == "limits.worker_pool_size"
        assert worker.value_type == "int"
        assert worker.default == WORKER_POOL_SIZE
        assert worker.ge == 1
        assert worker.requires_restart is True
        assert worker.sensitive is False
        assert not hasattr(worker, "effective_max")

        chat = MANAGED_KEYS["limits.chat_worker_pool_size"]
        assert chat.env_var == "LIMITS_CHAT_WORKER_POOL_SIZE"
        assert chat.config_path == "limits.chat_worker_pool_size"
        assert chat.value_type == "int"
        assert chat.default == CHAT_WORKER_POOL_SIZE
        assert chat.ge == 1
        assert chat.requires_restart is True
        assert chat.sensitive is False
        assert not hasattr(chat, "effective_max")

    @pytest.mark.asyncio
    async def test_two_worker_slot_keys_ladder(
        self, client_off, monkeypatch, settings_engine
    ):
        """F4/row 17 — the two new keys ride the SAME resolver ladder:
        ``limits.worker_pool_size`` DB override beats env; env-only
        ``limits.chat_worker_pool_size`` reports source="env".

        Registry-driven — the generic machinery resolves the new keys with
        ZERO new code (proves the move-together chain constants → Field →
        registry → resolver)."""
        # worker_pool_size: BOTH a DB override AND an env var set → DB wins.
        monkeypatch.setenv("LIMITS_WORKER_POOL_SIZE", "9")
        put = await client_off.put(
            "/api/settings/daemon/limits.worker_pool_size", json={"value": 7}
        )
        assert put.status_code == 200, put.text
        # chat_worker_pool_size: env only → env wins.
        monkeypatch.setenv("LIMITS_CHAT_WORKER_POOL_SIZE", "3")

        body = (await client_off.get(URL)).json()
        by_key = {e["key"]: e for e in body["settings"]}

        worker = by_key["limits.worker_pool_size"]
        assert worker["source"] == "db"
        assert worker["db_override"] == 7
        assert worker["env_value"] == 9  # env present but LOWER tier

        chat = by_key["limits.chat_worker_pool_size"]
        assert chat["source"] == "env"
        assert chat["env_value"] == 3

        assert body["restart_required"] is True  # a db-tier key is set


# ────────────────────────────────────────────────────────────────────────────
# PUT / DELETE
# ────────────────────────────────────────────────────────────────────────────


class TestPutDelete:
    @pytest.mark.asyncio
    async def test_put_happy_persists_row_auth_off(
        self, client_off, monkeypatch, settings_engine, caplog
    ):
        _no_env(monkeypatch)
        with caplog.at_level(logging.INFO, logger=_AUDIT_LOGGER):
            resp = await client_off.put(KEY_URL, json={"value": 6})
        assert resp.status_code == 200, resp.text
        # BARE entry response (frozen write shape — no wrapper).
        body = resp.json()
        assert body["key"] == KEY
        assert body["source"] == "db"
        assert body["db_override"] == 6
        assert "settings" not in body and "restart_required" not in body
        # Row persisted with NULL actor (auth-off — no raise, no empty str).
        row = _read_row(settings_engine)
        assert row is not None
        assert row.value_json == "6"
        assert row.value_type == "int"
        assert row.updated_by is None
        # Audit line: INFO with key + "auth-off" for the NULL actor.
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("db override set" in m and KEY in m and "auth-off" in m for m in infos)
        # GET now reports source="db".
        got = await client_off.get(URL)
        assert got.json()["settings"][0]["source"] == "db"

    @pytest.mark.asyncio
    async def test_put_happy_actor_capture_auth_on(
        self, client_on, monkeypatch, settings_engine, caplog
    ):
        _no_env(monkeypatch)
        with caplog.at_level(logging.INFO, logger=_AUDIT_LOGGER):
            resp = await client_on.put(
                KEY_URL, json={"value": 6}, headers={"X-Test-Role": "session"}
            )
        assert resp.status_code == 200, resp.text
        row = _read_row(settings_engine)
        assert row is not None
        assert row.updated_by == "operator-cli"  # the auth subject
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any(
            "db override set" in m and KEY in m and "operator-cli" in m for m in infos
        )

    @pytest.mark.asyncio
    async def test_put_actor_bearer_subject(self, client_on, monkeypatch, settings_engine):
        _no_env(monkeypatch)
        resp = await client_on.put(
            KEY_URL, json={"value": 6}, headers={"X-Test-Role": "bearer:settings:write"}
        )
        assert resp.status_code == 200
        assert _read_row(settings_engine).updated_by == "machine-1"

    @pytest.mark.asyncio
    async def test_put_unknown_key_404(self, client_off):
        resp = await client_off.put(UNKNOWN_URL, json={"value": 6})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_put_value_zero_422(self, client_off):
        resp = await client_off.put(KEY_URL, json={"value": 0})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail.get("failure_class") == "validation"
        assert detail.get("constraint") == "ge=1"

    @pytest.mark.asyncio
    async def test_put_non_int_422(self, client_off):
        resp = await client_off.put(KEY_URL, json={"value": "seven"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_clears_override_falls_down_ladder(
        self, client_off, monkeypatch, settings_engine, caplog
    ):
        _no_env(monkeypatch)
        assert (await client_off.put(KEY_URL, json={"value": 6})).status_code == 200
        with caplog.at_level(logging.INFO, logger=_AUDIT_LOGGER):
            resp = await client_off.delete(KEY_URL)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "default"  # fell down the ladder
        assert "db_override" not in body
        assert _read_row(settings_engine) is None  # override gone
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("db override cleared" in m and KEY in m for m in infos)
        # Idempotent: deleting again still 200 with the lower tier.
        again = await client_off.delete(KEY_URL)
        assert again.status_code == 200
        assert again.json()["source"] == "default"

    @pytest.mark.asyncio
    async def test_delete_unknown_key_404(self, client_off):
        resp = await client_off.delete(UNKNOWN_URL)
        assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────────────
# 503-unwired postures
# ────────────────────────────────────────────────────────────────────────────


class TestUnwired:
    @pytest.mark.asyncio
    async def test_get_503_when_service_unwired(self, app_off, settings_engine, monkeypatch):
        _no_env(monkeypatch)
        _reset_di()  # nothing wired
        routers_settings.set_settings_resolution_snapshot(
            {KEY: {"default": 10, "env_value": None, "yaml_value": None, "applied_value": 10}}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_off), base_url="http://test"
        ) as ac:
            resp = await ac.get(URL)
        assert resp.status_code == 503
        assert "not initialized" in resp.json()["detail"]["error"]

    @pytest.mark.asyncio
    async def test_get_503_when_snapshot_unwired(self, app_off, settings_engine, monkeypatch):
        _no_env(monkeypatch)
        _reset_di()
        routers_settings.set_settings_service(
            settings_service.SettingsService(settings_engine)
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_off), base_url="http://test"
        ) as ac:
            resp = await ac.get(URL)
        assert resp.status_code == 503
        assert "not initialized" in resp.json()["detail"]["error"]
