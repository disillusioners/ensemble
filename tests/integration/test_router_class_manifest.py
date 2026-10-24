"""Router-mount manifest cage — oauth2-auth M-3 merge-gate reference.

**STABLE NAME — the M-3 merge gate.** The P3 review deferred per-route
scope application to P4 (D3.7), but the merge gate needs THIS test to
exist and to fail loudly the moment the route universe drifts without
an explicit class declaration. (The commission referenced a
``test_router_class_manifest.py`` that was believed P3-landed — it was
NEVER committed; this file is that cage, written fresh against the
real ``create_app`` composition.)

What it pins (the M-3 deliverable):

1. **The api_router mount universe + class column** — every
   ``api_router.include_router(...)`` family (28 legacy families in
   the auth-OFF default; +2 auth-gated when ON — the T3.6 keys family
   and the auth-self family), grouped by top-level path segment, with
   the observed auth class set per family (public / module_lane /
   protected / protected_default for untagged). Several routers share
   a top-level prefix (mappings→sources, messages→instances,
   dlq→projects, system_queues→queues, blueprints→projects), so 28
   includes collapse to 22 ``/api/<seg>`` families + webhooks = 23.
2. **The fail-closed default is VISIBLE, not silent** — a new
   ``include_router`` with no explicit class declaration produces a
   family absent from the manifest → the pin test FAILS naming the
   family, and the developer must declare its class in the manifest
   (that declaration IS the scope declaration at P3; per-route scope
   application remains P4 per D3.7).
3. **Tag-vs-manifest consistency** — a route carrying an explicit tag
   must match its manifest column (e.g. re-tagging webhooks public
   fails here).

The manifest is auth-ON (the full universe); the auth-OFF variant is
pinned alongside (the G3.2-adjacent base).
"""

from __future__ import annotations

import os
import warnings
from unittest.mock import patch

import pytest
from pydantic import SecretStr
from starlette.routing import Mount

from daemon.api import create_app
from daemon.auth.route_meta import (
    AUTH_MODULE_LANE_ATTR,
    AUTH_PUBLIC_ATTR,
    AUTH_PROTECTED_ATTR,
)
from daemon.config import AuthConfig

#: The PINNED manifest: family → observed class set. ``pd`` =
#: protected-default (UNTAGGED — the D3.7 legacy protected posture,
#: deliberate and declared HERE; P4 applies per-route scopes).
#: Structural exceptions live in ``_STRUCTURAL_EXCEPTIONS`` with their
#: justification.
PINNED_MANIFEST_AUTH_ON: dict[str, frozenset[str]] = {
    # ── /api/<seg> families (23 distinct prefixes over the 28 legacy
    #    includes + the 2 auth-gated ones) ────────────────────────────
    "/api/agents": frozenset({"pd"}),
    "/api/auth": frozenset({"public"}),  # T3.6 auth-self (§5.3)
    "/api/database": frozenset({"pd"}),
    "/api/health": frozenset({"public"}),
    "/api/info": frozenset({"public"}),
    "/api/instances": frozenset({"pd"}),  # + messages_router
    "/api/jobs": frozenset({"pd"}),
    "/api/maintenance": frozenset({"public", "pd"}),  # canary exact-public
    "/api/mcp": frozenset({"module_lane", "protected"}),  # KB mounts + keys
    "/api/mcp-servers": frozenset({"pd"}),
    "/api/missions": frozenset({"pd"}),
    "/api/migration": frozenset({"pd"}),
    "/api/notifications": frozenset({"pd"}),
    "/api/plane": frozenset({"pd"}),
    "/api/projects": frozenset({"pd"}),  # + dlq_router + blueprints_router
    "/api/queues": frozenset({"pd"}),  # + system_queues_router
    "/api/recovery": frozenset({"pd"}),
    "/api/schedules": frozenset({"pd"}),
    "/api/settings": frozenset({"pd"}),
    "/api/skill-bank": frozenset({"pd"}),
    "/api/skills": frozenset({"pd"}),
    "/api/snapshots": frozenset({"pd"}),
    # resource-cost-control P1 (aa95fc438): the resources_router
    # mounts /api/resources (8 routes: GET /live, /history,
    # /self-cost, /traceback, /anomalies, /anomalies/{id}/evidence,
    # /control-capabilities + POST /profile). No route carries an
    # explicit class tag (no public probe / module-lane / protected
    # decoration) — the family classifies protected-default ("pd"),
    # the D3.7 legacy posture, declared HERE. Unauthenticated
    # requests get the JSON 401 contract via the middleware's
    # default-deny dispatch (same as every other untagged /api
    # family).
    "/api/resources": frozenset({"pd"}),
    "/api/sources": frozenset({"pd"}),  # + mappings_router
    "/api/tmp_images": frozenset({"pd"}),
    # wedge-capture (2026-10-22): the wedge_capture_router mounts at
    # /api/wedge-captures (4 routes: GET "" (list), GET /status,
    # POST /capture, GET /{capture_id}). Every route carries an
    # explicit scope (``maintenance:read``), so the family classifies
    # protected-default ("pd") — the D3.7 legacy posture, declared
    # HERE. The POST additionally carries the trusted-Origin guard.
    "/api/wedge-captures": frozenset({"pd"}),
    # Phase B / agent-prompt-immutability T6 — the prompts_router
    # mounts at /api/prompts (the 15-endpoint family: the canonical
    # 12 per phaseB-plan.md addendum S4 + the 3 USER-DIRECTIVE-#2
    # deltas 2026-10-14 — PATCH/DELETE /versions/{id},
    # GET /pending-count). Every route carries an explicit
    # @require_scope so the family classifies as protected-default
    # ("pd"); no public/module_lane carve-out (D3 fail-closed posture
    # guards auth-OFF via per-endpoint 503).
    "/api/prompts": frozenset({"pd"}),
    # Phase C / agent-prompt-immutability F1 (overnight, 2026-10-14):
    # the custom_agents_router mounts at /api/custom-agents (the
    # canonical R-A1/R-A2 4-endpoint family: POST + GET (list) + GET
    # (single) + DELETE). Every endpoint carries an explicit
    # @require_scope prompt:read|write AND an inline 503 AUTH_NOT_
    # CONFIGURED gate (mirrors the prompts-family S8/D3 posture),
    # so the family classifies as protected-default ("pd") — the
    # D3 fail-closed posture is honored at every endpoint.
    "/api/custom-agents": frozenset({"pd"}),
    # remote-hands center gateway (2026-10-22) — the WebSocket route the
    # controlled machines dial OUT to (SPEC.md v0.1). MODULE-LANE: the
    # credential is the module's OWN bearer enrollment token on the
    # upgrade request (§3.1 / §4.1) — the OAuth2 middleware must never
    # own this path, or every machine would be refused with a 401 and
    # would re-enroll forever. The route is registered ONLY when
    # MCPS_GATEWAY_ENABLED is ON; the AUTH_ON leg of this cage boots the
    # app with the gateway ON so the family is observed.
    #
    # Phase C added the OAUTH READ LANE to the same family: the three
    # read-only routes (GET /machines + GET /sessions + GET /audit)
    # are ordinary /api routers — untagged, so they classify as
    # protected-default ("pd"), exactly like the other read families.
    # The family therefore carries BOTH classes.
    "/api/mcps-gateway": frozenset({"module_lane", "pd"}),
    "/api/webhooks": frozenset({"module_lane"}),  # FR-40 module lane
    "/api/work": frozenset({"pd"}),
    "/api/workspace": frozenset({"pd"}),
    # ── non-/api families ────────────────────────────────────────────
    "/": frozenset({"public"}),  # SPA shell
    "/docs": frozenset({"public"}),  # OD-1 trio (exact; redirects see below)
    "/docs/oauth2-redirect": frozenset({"pd"}),  # FastAPI-internal asset
    "/livez": frozenset({"public"}),
    "/openapi.json": frozenset({"public"}),
    "/readyz": frozenset({"public"}),
    "/redoc": frozenset({"public"}),
    "/views": frozenset({"public", "pd"}),  # /views/livez exact-public
    "/{path:path}": frozenset({"pd"}),  # SPA catch-all (§5.7 rule, untagged)
}

#: Auth-OFF: no auth-self family, no keys family (module/protected
#: columns collapse to module_lane-only).
#:
#: The remote-hands gateway family IS part of the auth-OFF universe —
#: but for a DIFFERENT reason than the auth-self family: the Phase C
#: READ routes are registered UNCONDITIONALLY (the kill switch governs
#: the TRANSPORT, not the read surface), so the family exists in the
#: auth-OFF app with ONLY the read routes in it. The module-lane
#: WebSocket route is absent there (the gateway is explicitly OFF in
#: that leg), so the family classifies protected-default ("pd") — the
#: same class it has for the read routes in the auth-ON universe.
PINNED_MANIFEST_AUTH_OFF: dict[str, frozenset[str]] = {
    k: (
        frozenset({"module_lane"})
        if k == "/api/mcp"
        else frozenset({"pd"})
        if k == "/api/mcps-gateway"
        else v
    )
    for k, v in PINNED_MANIFEST_AUTH_ON.items()
    if k != "/api/auth"
}

_TAG_TO_CLASS = {
    AUTH_PUBLIC_ATTR: "public",
    AUTH_MODULE_LANE_ATTR: "module_lane",
    AUTH_PROTECTED_ATTR: "protected",
}


def _route_class(route) -> str:
    if getattr(route, AUTH_PUBLIC_ATTR, False):
        return "public"
    if getattr(route, AUTH_MODULE_LANE_ATTR, False):
        return "module_lane"
    if getattr(route, AUTH_PROTECTED_ATTR, False):
        return "protected"
    return "pd"


def _family_of(path: str) -> str:
    if path.startswith("/api/"):
        parts = path.split("/")
        return "/api/" + (parts[2] if len(parts) > 2 else "")
    if path.startswith("/views"):
        return "/views"
    return path


def _observe(app) -> dict[str, frozenset[str]]:
    observed: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        if isinstance(route, Mount) and path not in _family_of(path):
            pass  # mounts classify at their own path (family_of covers it)
        observed.setdefault(_family_of(path), set()).add(_route_class(route))
    return {k: frozenset(v) for k, v in observed.items()}


#: The route path the contract fixes (SPEC.md v0.1, the daemon side).
CONTRACT_GATEWAY_PATH = "/api/mcps-gateway/ws"

#: The NAME of the gateway's WebSocket route — the identity the
#: module-lane tag is applied to (the path is configurable, so the tag
#: must NOT be a path prefix).
GATEWAY_ROUTE_NAME = "mcp_gateway_ws"


def _auth_on_config() -> AuthConfig:
    return AuthConfig(
        enabled=True,
        client_id="manifest",
        client_secret=SecretStr("manifest-secret"),
        issuer="https://idp.test",
        allow_insecure_cookie=True,
        allowed_origins=["http://testserver"],
        external_redirect_base="http://testserver",
    )


class TestRouterClassManifest:
    """The cage — run these on EVERY route-universe change."""

    def test_auth_on_universe_matches_manifest(self, monkeypatch):
        warnings.filterwarnings("ignore")
        # The remote-hands gateway route is registered ONLY when
        # MCPS_GATEWAY_ENABLED is ON — the manifest declares its family,
        # so the leg must boot the app with the gateway ON.
        monkeypatch.setenv("MCPS_GATEWAY_ENABLED", "1")
        # The PATH is configurable (MCPS_GATEWAY_PATH): pin it to the
        # contract path so the family key of the manifest is
        # deterministic and does not depend on the ambient environment.
        monkeypatch.setenv("MCPS_GATEWAY_PATH", CONTRACT_GATEWAY_PATH)
        app = create_app(auth_config=_auth_on_config())
        observed = _observe(app)
        missing = sorted(set(PINNED_MANIFEST_AUTH_ON) - set(observed))
        undeclared = sorted(set(observed) - set(PINNED_MANIFEST_AUTH_ON))
        assert not missing, (
            "Route families DISAPPEARED from the universe "
            f"(manifest expects them): {missing}"
        )
        assert not undeclared, (
            "NEW route family(ies) mounted WITHOUT an explicit class "
            "declaration in tests/integration/"
            "test_router_class_manifest.py::PINNED_MANIFEST_AUTH_ON — "
            "declare the family + its auth class (public / module_lane "
            "/ protected / pd[protected-default]) there: "
            f"{undeclared}"
        )
        drifted = {
            k: (sorted(observed[k]), sorted(PINNED_MANIFEST_AUTH_ON[k]))
            for k in PINNED_MANIFEST_AUTH_ON
            if k in observed and observed[k] != PINNED_MANIFEST_AUTH_ON[k]
        }
        assert not drifted, (
            "Auth CLASS column drifted for family(ies) — update the "
            f"manifest DELIBERATELY (family: observed vs pinned): {drifted}"
        )

    def test_auth_off_universe_matches_manifest(self, monkeypatch):
        warnings.filterwarnings("ignore")
        # The gateway is explicitly OFF here: its route is not registered
        # and its family is not part of the AUTH-OFF universe.
        monkeypatch.setenv("MCPS_GATEWAY_ENABLED", "0")
        monkeypatch.setenv("MCPS_GATEWAY_PATH", CONTRACT_GATEWAY_PATH)
        app = create_app()  # default OFF config
        observed = _observe(app)
        undeclared = sorted(set(observed) - set(PINNED_MANIFEST_AUTH_OFF))
        missing = sorted(set(PINNED_MANIFEST_AUTH_OFF) - set(observed))
        assert not undeclared, (
            "NEW route family(ies) mounted WITHOUT an explicit class "
            f"declaration (auth-OFF manifest): {undeclared}"
        )
        assert not missing, (
            f"Route families DISAPPEARED (auth-OFF manifest): {missing}"
        )

    def test_a_custom_gateway_path_is_still_module_lane(self):
        """R3: the module-lane tag follows the ROUTE, not the path.

        The path is configurable (``MCPS_GATEWAY_PATH``), so the tag
        must be applied by ROUTE IDENTITY (the route name) — a path
        prefix would either miss a custom path or, with an empty
        prefix, match EVERY route in the app.
        """
        warnings.filterwarnings("ignore")
        custom_path = "/api/remote-hands/v1"
        assert custom_path != CONTRACT_GATEWAY_PATH
        with patch.dict(
            os.environ,
            {"MCPS_GATEWAY_ENABLED": "1", "MCPS_GATEWAY_PATH": custom_path},
        ):
            app = create_app(auth_config=_auth_on_config())
        tagged = [
            route
            for route in app.routes
            if getattr(route, "name", None) == GATEWAY_ROUTE_NAME
        ]
        assert tagged, "the gateway route was not registered at the custom path"
        for route in tagged:
            assert getattr(route, AUTH_MODULE_LANE_ATTR, False) is True, (
                "the gateway route lost its module-lane tag at a custom path"
            )
        assert any(
            getattr(route, "path", "") == custom_path for route in tagged
        ), "the route was not mounted at the configured path"

    def test_undeclared_mount_makes_the_cage_fire(self):
        """THE cage property: include_router with NO explicit class
        declaration must FAIL this manifest — proven by adding a rogue
        router to the real app and asserting the walk flags it."""
        warnings.filterwarnings("ignore")
        from fastapi import APIRouter

        app = create_app()  # auth OFF — the stable base
        rogue = APIRouter(prefix="/api/rogue-undeclared")

        @rogue.get("")
        async def _rogue():
            return {"ok": True}

        app.include_router(rogue)
        observed = _observe(app)
        undeclared = sorted(set(observed) - set(PINNED_MANIFEST_AUTH_OFF))
        assert "/api/rogue-undeclared" in undeclared
        # The pristine universe stays clean — the cage is the delta.
        del observed["/api/rogue-undeclared"]
        assert not (set(observed) - set(PINNED_MANIFEST_AUTH_OFF))

    def test_manifest_covers_every_api_family_with_a_class(self):
        """Every /api/<seg> family in the manifest declares exactly the
        class vocabulary — no free-form strings creep in."""
        vocabulary = {"public", "module_lane", "protected", "pd"}
        for family, classes in PINNED_MANIFEST_AUTH_ON.items():
            assert classes <= vocabulary, (
                f"{family}: unknown class tokens {classes - vocabulary}"
            )
        api_families = [f for f in PINNED_MANIFEST_AUTH_ON if f.startswith("/api/")]
        # The family COUNT is part of the pin: 32 include_router calls
        # (30 unconditional + the 2 auth-gated T3.6 families) collapse
        # onto 27 api_router-owned prefixes (5 routers share a prefix —
        # see the docstring); /api/health and /api/info are app-level
        # probes and /api/mcp also carries the two app-level KB Mounts.
        # Phase B / agent-prompt-immutability T6 added the
        # /api/prompts family — the api_families count grew from 27
        # to 28.
        # Phase C / agent-prompt-immutability F1 added the
        # /api/custom-agents family — the api_families count grew
        # from 28 to 29.
        # resource-cost-control P1 (aa95fc438) added the
        # /api/resources family — the api_families count grew
        # from 29 to 30.
        # remote-hands center gateway (2026-10-22) added the
        # /api/mcps-gateway family — the api_families count grew
        # from 30 to 31.
        # wedge-capture (2026-10-22) added the /api/wedge-captures
        # family — the api_families count grew from 31 to 32.
        assert len(api_families) == 32, sorted(api_families)
