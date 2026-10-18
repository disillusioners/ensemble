"""T2 binding pin — auth-ON exemption for ``/views/public/*``.

P2 of live-view-passcode (2026-10-17). The State-B exemption
mechanism is the code-seeded PUBLIC prefix tag
(``tag_routes_by_path(app, path_prefix="/views/public")`` called
at the create_app registration site AFTER ``include_router`` —
architecture-recommendation.md D3:43-50 + D-A1 fold). This file
exercises the mechanism with auth middleware ON: the public
sub-routes must pass ``AuthMiddleware`` while every other
``/views`` path stays default-deny.

**P4 D19 cage fold-in disposition:** this file is RETAINED as
**complementary HTTP-surface coverage** alongside the canonical
S-19 cage extension at
``tests/integration/test_auth_middleware_dispatch.py::
TestS19AnonymousViewPaths::test_public_lane_code_seeded_prefix_tag_passes_auth``
(phase4-plan.md D19 / phase4 T4.1 gap closure #2). The S-19 cage
test pins the SAME core assertions (anonymous_view_paths empty,
public lane passes via code-seeded prefix tag, direct lane stays
default-deny, ``/views/livez`` + ``/views/assets/*`` unaffected,
over-broad-tag exclusion pinned) inside the canonical S-19
regression cage. This file adds the complementary HTTP-surface
assertions below that the S-19 cage does NOT cover:

* public asset alias (``/views/public/assets/mermaid/...``) passes
  AuthMiddleware (T2.10 — the alias is the gate-free vendored
  bundle mirrored for granted customers);
* bare ``/views`` (no /public prefix) stays default-deny;
* the PUBLIC-tagged route count assertion (>=5 routes carry the
  AUTH_PUBLIC_ATTR — a canary that catches a future drift where
  a route is added without the tag).

Both homes are kept in sync: any change here MUST land in the
S-19 cage test too (the canonical assertions); any change in
the S-19 cage test is the binding update. Cross-reference is
recorded here and in the P4 coverage matrix at
``.agents/shared/planning/live-view-passcode/governor-review-pack.md``.

Binding assertions (architecture-recommendation.md T2):

* ``/views/public/{root}/{rel}`` passes the middleware with the
  code-seeded tag — the response is the PUBLIC LANE's own
  response (interstitial / 404 from the gate), NOT the auth
  401 contract.
* ``/views/{root}/{rel}`` (non-public) stays default-deny —
  the auth 401 contract.
* ``/views/livez`` and ``/views/assets/*`` are unaffected
  (the former is the PUBLIC exact-tag from the existing loop;
  the latter stays protected — the exemption is exact-prefix
  on ``/views/public`` only).
* ``auth.anonymous_view_paths`` stays EMPTY in every test (the
  knob is NOT the mechanism — ADR D3:48).

The over-broad-tag exclusion pin (architecture-recommendation.md
T3) lives in the same section: the legacy parametrized template
``/views/{root_name}/{rel_path:path}`` truncates at its first
``{`` to ``/views/`` under ``_template_prefix_matches``, which
can never be swept into a ``/views/public`` prefix tag.
"""

from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from daemon.auth.route_meta import (
    AUTH_PUBLIC_ATTR,
    _template_prefix_matches,
)
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub


@pytest.fixture()
def on_app(tmp_path):
    """A real ``create_app(auth ON)`` with the synthetic auth service."""
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    # The knob stays EMPTY — the code-seeded prefix tag is the
    # mechanism (ADR D3; the operator knob was REJECTED).
    assert cfg.anonymous_view_paths == ()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    return app


def fresh_client(app) -> TestClient:
    return TestClient(
        app, base_url="http://testserver", follow_redirects=False
    )


def _is_auth_401(resp) -> bool:
    """The auth 401 contract (the default-deny envelope)."""
    return resp.status_code == 401 and "AUTH_REQUIRED" in resp.text


class TestAuthOnPublicLaneExemption:
    """T2 — the public lane passes AuthMiddleware with the
    code-seeded tag; every other /views path stays default-deny.
    """

    def test_anonymous_view_paths_stays_empty(self, on_app):
        """The knob ships EMPTY and stays empty (ADR D3:48) — the
        code-seeded prefix tag is the mechanism; the operator
        knob is NOT consulted for the public lane. The binding
        passthrough is proven by the tests below (no knob entry
        required); this test pins the knob default itself."""
        cfg = on_auth_config()
        assert cfg.anonymous_view_paths == ()

    def test_public_lane_get_passes_auth_middleware(self, on_app):
        """/views/public/... with NO credential → the public
        lane's own response (NOT the auth 401 contract).

        The specific shape (interstitial vs uniform 404) is the
        public gate's business (no passcode service wired in
        this fixture → uniform 404); what this test pins is
        that the MIDDLEWARE let the request through.
        """
        client = fresh_client(on_app)
        r = client.get(
            "/views/public/planning/ens/feat/design/mockups/landing.html",
            headers={"Accept": "application/json"},
        )
        assert not _is_auth_401(r), (
            f"/views/public/* was auth-gated — the code-seeded "
            f"prefix tag did not pass the lane: {r.status_code} "
            f"{r.text[:200]}"
        )

    def test_public_lane_post_passes_auth_middleware(self, on_app):
        """/views/public/... POST (the verify endpoint) passes
        the middleware too — the ONLY POST on /views."""
        client = fresh_client(on_app)
        r = client.post(
            "/views/public/planning/ens/feat/design/mockups/landing.html",
            data={"code": "anycod"},
            headers={"Accept": "application/json"},
        )
        assert not _is_auth_401(r)

    def test_non_public_direct_lane_stays_default_deny(self, on_app):
        """/views/{root}/{rel} (non-public) stays the auth
        401 contract — the exemption is exact-prefix."""
        client = fresh_client(on_app)
        r = client.get(
            "/views/planning/ens/feat/design/mockups/landing.html",
            headers={"Accept": "application/json"},
        )
        assert _is_auth_401(r), (
            f"/views/<root>/<rel> must stay default-deny with "
            f"auth ON; got {r.status_code}"
        )

    def test_views_root_stays_default_deny(self, on_app):
        """Bare /views (no public prefix) stays default-deny."""
        client = fresh_client(on_app)
        r = client.get("/views", headers={"Accept": "application/json"})
        assert r.status_code in (401, 404)
        # 404 from the SPA-vs-views dispatch is fine — the
        # assertion is NOT-the-public-lane passthrough.

    def test_views_assets_stays_default_deny(self, on_app):
        """/views/assets/* (the DIRECT-lane mermaid asset) is
        NOT swept into the public prefix tag — the exemption is
        exact-prefix on /views/public; the direct asset path
        stays protected."""
        client = fresh_client(on_app)
        r = client.get(
            "/views/assets/mermaid/mermaid.min.js",
            headers={"Accept": "application/json"},
        )
        assert _is_auth_401(r), (
            f"/views/assets/* must stay default-deny (the "
            f"exemption is exact-prefix /views/public); got "
            f"{r.status_code}"
        )

    def test_public_asset_alias_passes_auth_middleware(self, on_app):
        """/views/public/assets/... IS inside the public prefix
        — the gate-free alias rides the same exemption (T2.10)."""
        client = fresh_client(on_app)
        r = client.get(
            "/views/public/assets/mermaid/mermaid.min.js",
            headers={"Accept": "application/json"},
        )
        assert not _is_auth_401(r)

    def test_views_livez_unchanged_public_exact_tag(self, on_app):
        """/views/livez keeps its existing PUBLIC exact-path tag
        (from the pre-existing loop at daemon/api.py:3536) — the
        second prefix-tag call neither adds nor removes it."""
        client = fresh_client(on_app)
        r = client.get(
            "/views/livez", headers={"Accept": "application/json"}
        )
        assert not _is_auth_401(r)


class TestTagWiring:
    """The tag is actually ON the public routes (not just
    effectively-passing)."""

    def test_public_routes_carry_public_attr(self, on_app):
        tagged = [
            getattr(r, "path")
            for r in on_app.routes
            if getattr(r, AUTH_PUBLIC_ATTR, False)
            and str(getattr(r, "path", "")).startswith("/views/public")
        ]
        # The public route family (GET/HEAD/POST on the view
        # path + the asset alias) — at least these five.
        assert len(tagged) >= 5, (
            f"expected >=5 /views/public routes tagged PUBLIC; "
            f"got {tagged}"
        )
        for path in (
            "/views/public/{root_name}/{rel_path:path}",
            "/views/public/assets/mermaid/mermaid.min.js",
        ):
            assert path in tagged, (
                f"{path} missing from the PUBLIC tag set: {tagged}"
            )

    def test_legacy_parametrized_route_not_tagged_by_prefix_call(
        self, on_app
    ):
        """T3 — over-broad-tag exclusion. The legacy
        ``/views/{root_name}/{rel_path:path}`` template must NOT
        carry the PUBLIC tag from the ``/views/public`` prefix
        call (the ``_template_prefix_matches`` truncation at the
        first ``{`` yields ``/views/`` which neither equals nor
        starts-with ``/views/public``)."""
        # The matcher semantics — pinned directly (shared code
        # that can evolve; this is the canary).
        assert _template_prefix_matches(
            "/views/{root_name}/{rel_path:path}", "/views/public"
        ) is False
        assert _template_prefix_matches(
            "/views/public/{root_name}/{rel_path:path}", "/views/public"
        ) is True
        assert _template_prefix_matches(
            "/views/assets/mermaid/mermaid.min.js", "/views/public"
        ) is False
        # And the application state: the legacy route is NOT
        # tagged PUBLIC.
        legacy = [
            r
            for r in on_app.routes
            if getattr(r, "path", "")
            == "/views/{root_name}/{rel_path:path}"
        ]
        assert legacy, "legacy parametrized route missing from app.routes"
        for route in legacy:
            assert not getattr(route, AUTH_PUBLIC_ATTR, False), (
                "the legacy /views/{root}/{rel} route was swept "
                "into the /views/public prefix tag — the "
                "over-broad-tag exclusion broke (matcher "
                "evolution canary)"
            )
