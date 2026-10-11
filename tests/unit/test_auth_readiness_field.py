"""Readiness auth sub-field tests — oauth2-auth Phase 3 (T3.9 / G3.7).

Asserts the plan §4 row: ``ReadyzResponse.auth_dependency_status``
present with the FR-30 enum values; ``ok`` when healthy, ``degraded``
when the IdP is unreachable (verdict STAYS 200), ``unconfigured``
semantics; the field ABSENT when auth is disabled (OFF-state body
byte-identical, NFR-1); and the verdict NEVER flips due to auth state
(OD-9 / AC-31 / NFR-15).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import SecretStr

from daemon.config import AuthConfig
from daemon.models.common import ReadyzResponse
from daemon.services import readiness as readiness_mod
from daemon.services.readiness import (
    ReadinessComposite,
    apply_forced_degradation,
    compute_readiness_composite,
    refresh_readiness_composite,
)
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

FR30_ENUM = {"ok", "degraded", "unconfigured"}


class TestSchema:
    def test_field_present_on_readyz_response(self):
        assert "auth_dependency_status" in ReadyzResponse.model_fields

    def test_values_constrained_to_fr30_enum(self, tmp_path):
        """Every value the provider can emit is inside the enum."""
        from daemon.services.auth_service import AuthService

        stub_ok = FakeOidcStub()
        cfg = on_auth_config()
        from fastapi import FastAPI

        app = FastAPI()
        service = attach_auth_state(app, cfg, stub_ok, tmp_path)
        assert service.get_readiness_state() in FR30_ENUM
        service._oauth_client._last_discovery_reachable = False
        assert service.get_readiness_state() == "degraded"
        dead = AuthService(
            config=on_auth_config(external_redirect_base=None),
            repository=app.state.auth_session_repository,
            oauth_client=app.state.oauth_client,
            bind_host=None,
            bind_port=None,
            host_recorder=None,
        )
        assert dead.get_readiness_state() == "unconfigured"
        assert {"ok", "degraded", "unconfigured"} == FR30_ENUM


class TestCompositeWiring:
    def test_composite_carries_advisory_field(self):
        composite = compute_readiness_composite(
            database_ok=True,
            queue_fresh_ok=True,
            services_ok=True,
            queue_max_age_seconds=None,
            auth_dependency_status="degraded",
        )
        assert composite.auth_dependency_status == "degraded"
        # Advisory only: verdict unaffected.
        assert composite.ready is True

    def test_to_payload_top_level_and_detail(self):
        composite = compute_readiness_composite(
            database_ok=True,
            queue_fresh_ok=True,
            services_ok=True,
            queue_max_age_seconds=None,
            auth_dependency_status="ok",
        )
        payload = composite.to_payload()
        assert payload["auth_dependency_status"] == "ok"
        assert payload["detail"]["auth_dependency_status"] == "ok"
        assert payload["status"] == "ready"

    def test_absent_when_auth_off(self):
        """auth OFF → field ABSENT (not null) — OFF-state /readyz body
        stays byte-identical to the pre-build contract (NFR-1)."""
        composite = compute_readiness_composite(
            database_ok=True,
            queue_fresh_ok=True,
            services_ok=True,
            queue_max_age_seconds=None,
            auth_dependency_status=None,
        )
        payload = composite.to_payload()
        assert "auth_dependency_status" not in payload
        assert "auth_dependency_status" not in payload["detail"]
        assert json.dumps(payload)  # serializable

    def test_refresh_threading(self):
        async def _db():
            return True

        async def _queue():
            return None

        composite = asyncio.run(
            refresh_readiness_composite(
                db_probe=lambda: True,
                queue_probe=lambda: None,
                services_ok=True,
                queue_freshness_threshold_seconds=60.0,
                auth_dependency_status="degraded",
            )
        )
        assert composite.auth_dependency_status == "degraded"
        # degraded auth NEVER degrades readiness (OD-9).
        assert composite.ready is True
        assert composite.reasons == []

    def test_forced_degradation_preserves_auth_field(self):
        composite = compute_readiness_composite(
            database_ok=True,
            queue_fresh_ok=True,
            services_ok=True,
            queue_max_age_seconds=None,
            auth_dependency_status="ok",
        )
        drilled = apply_forced_degradation(composite, env_value="1")
        assert drilled.ready is False  # the drill knob DID degrade
        assert drilled.auth_dependency_status == "ok"  # field survives


class TestVerdictFrozen:
    def test_degraded_auth_never_flips_verdict(self):
        """AC-31: degraded/unconfigured rides as a body sub-field — the
        status stays 'ready' when every REAL component passes."""
        for state in ("ok", "degraded", "unconfigured"):
            composite = compute_readiness_composite(
                database_ok=True,
                queue_fresh_ok=True,
                services_ok=True,
                queue_max_age_seconds=None,
                auth_dependency_status=state,
            )
            assert composite.ready is True, state
            assert composite.to_payload()["status"] == "ready", state

    def test_auth_state_cannot_mask_real_degradation(self):
        """The inverse: auth 'ok' never heals a failing component."""
        composite = compute_readiness_composite(
            database_ok=False,
            queue_fresh_ok=True,
            services_ok=True,
            queue_max_age_seconds=None,
            auth_dependency_status="ok",
        )
        assert composite.ready is False
        assert any("database" in reason for reason in composite.reasons)
