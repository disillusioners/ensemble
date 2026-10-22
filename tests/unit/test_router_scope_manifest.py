"""Router-mount scope-declaration cage — oauth2-auth Phase 4 (D4.7,
AMENDED v1.1 / C1, AMEND v1.3 / F3) + G4.5.

**Cross-referenced with the P3 cage.** The P3 merge gate owns
``tests/integration/test_router_class_manifest.py`` (the CLASS column:
public / module_lane / protected / pd). THIS file is the P4 SCOPE
column: every ``api_router.include_router(...)`` mount in
``daemon/api.py`` must carry an explicit scope declaration — either

* decorated routes (``@require_scope(...)`` wrappers or
  ``dependencies=[scope_required(...)]`` per-route dependencies — both
  stamp ``required_scopes`` on their callables, see
  :mod:`daemon.auth.decorators`), or
* an explicit ``*`` WILDCARD entry in the pinned manifest below (the
  D9 default-``*`` posture for families the plan does not scope-map in
  v1 — declared, not silent).

The cage greps ``daemon/api.py`` for the SPECIFIC
``api_router.include_router(`` pattern (NOT the raw
``include_router(`` textual set — the F3-pinned census: 30 textual
matches at the current tree = 28 unconditional mounts + the 2
auth-ON-gated families (mcp-kb keys, auth-self); the residual matches
are the 2 app-level ``app.include_router(`` calls + prose comments,
documented in the P3 cage and NOT in this cage's match set). A NEW
mount that adds routes under a family absent from the manifest (or a
mount whose declaration drifted from the manifest) FAILS LOUDLY,
naming the family — the rogue-router leg proves the failure mode.

Manifest discipline: the scope column here and the class column in
``test_router_class_manifest.py`` are intentionally SEPARATE pins (one
may import the other's manifest for cross-checks; neither duplicates
the other's columns — no drift surface beyond the deliberate
two-column split).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from daemon.api import create_app
from daemon.auth.scopes import SCOPE_TAXONOMY, SCOPE_WILDCARD
from tests.helpers.auth_fixture import on_auth_config

REPO_ROOT = Path(__file__).resolve().parents[2]
API_PY = REPO_ROOT / "daemon" / "api.py"

#: The pinned SCOPE manifest — family (top-2 path segments) → declared
#: scope requirements. A family entry is either a frozenset of scopes
#: (EVERY route in the family must declare one of them) or the literal
#: wildcard ``{"*"}`` (explicitly undeclared-by-decorator; the D9 v1
#: posture). Families NOT listed here fail the cage.
#:
#: Sources of truth: [REQ] §5.4 per-router matrix + §7.3 minimum-scope
#: table + the commission's mapping ("mcp-servers mcp:read;
#: instances/messages/sources/jobs read+write splits; maintenance
#: writes maintenance:read; DB switch/snapshot admin
#: auth:admin/snapshot:write").
PINNED_SCOPE_MANIFEST: dict[str, frozenset[str] | str] = {
    # ── decorated families (scope-mapped per §5.4/§7.3) ─────────────
    "/api/database": "auth:admin",          # §5.4: DB switch admin
    "/api/instances": "instances:read|instances:write|messages:write",
    #   instances router (read/write split) + messages router (POSTs
    #   messages:write; message/event reads ride instances:read) —
    #   both mount under /api/instances.
    "/api/jobs": "jobs:read|jobs:write",    # §5.4: CRUD + mgmt + SSE
    "/api/maintenance": "maintenance:read|*",
    #   §5.4: WRITES carry maintenance:read (the Origin guard composes
    #   per NFR-14); /availability is the PUBLIC canary and /status,
    #   /runs/{id} reads stay undeclared (declared here as the mixed
    #   family's * member).
    "/api/mcp": "mcpkb:admin",              # T3.12 keys family (Mounts module-lane)
    "/api/mcp-servers": "mcp:read",         # §5.4/§7.3 row 7
    "/api/recovery": "auth:admin",          # §5.4: recovery admin
    "/api/settings": "snapshot:write|settings:read|settings:write|*",
    #   §5.4: snapshot-admin paths snapshot:write; all other settings
    #   routes stay undeclared (the family's * member).
    "/api/sources": "sources:write",        # + mappings router; the
    #   family is write-class in the v1 taxonomy (D4.2 has no
    #   sources:read) — reads ride sources:write by declaration.
    "/api/workspace": "workspace:read|*",
    #   §5.4: reads workspace:read; PUT /{project_id}/file stays
    #   undeclared (NO workspace:write exists in the v1 taxonomy —
    #   declared gap, not an omission).
    # ── explicitly wildcarded families (the D9 default-* posture) ───
    "/api/agents": "*",
    "/api/notifications": "*",
    "/api/plane": "*",
    "/api/projects": "*",                   # + dlq + blueprints routers
    "/api/queues": "*",                     # + system_queues router
    "/api/schedules": "*",                  # not scope-mapped in v1
    "/api/skill-bank": "*",
    "/api/skills": "*",
    "/api/snapshots": "*",                  # §5.4 names no scope for the family
    # resource-cost-control P1 (aa95fc438): the /api/resources family
    # mounts 8 routes (GET /live, /history, /self-cost, /traceback,
    # /anomalies, /anomalies/{id}/evidence, /control-capabilities +
    # POST /profile) — none carries an @require_scope decoration
    # (observe-only seam; the degrade-never-503 posture ships
    # unauthenticated same-origin per the commission's house
    # posture), so the family manifests the wildcard D9 posture.
    "/api/resources": "*",
    "/api/tmp_images": "*",
    "/api/webhooks": "*",                   # MODULE-LANE (D19) — never OAuth2-gated
    "/api/work": "*",
    "/api/missions": "*",
    "/api/migration": "*",                  # SSE stream family
    "/api/auth": "*",                       # public-probe family (§5.3)
    "/api/health": "*",                     # PUBLIC
    "/api/info": "*",                       # PUBLIC
    # Phase B / agent-prompt-immutability T5/T6 — the 12-endpoint
    # prompt-version family. Every endpoint carries an explicit
    # @require_scope of one of the five minted prompts:* scopes
    # (T4 / phaseB-plan.md D11) — no wildcarded routes in this
    # family. The cage's _parse_scope_manifest_entry validator
    # confirms each named scope is in SCOPE_TAXONOMY at the
    # dedicated validation leg below.
    "/api/prompts": "prompts:read|prompts:submit|prompts:approve|prompts:audit|prompts:write",
    # Phase C / agent-prompt-immutability F1 (overnight, 2026-10-14):
    # custom-agents REST family (the 4 endpoints POST / GET / GET
    # / DELETE — same as the canonical R-A1/A2 lifecycle surface).
    # The leader F1 directive DELIBERATELY REUSES the prompts:*
    # scope set rather than minting a new scope (the chosen
    # scope-reuse convention is documented in
    # ``daemon/routers/custom_agents.py`` module-level docstring
    # — invokes the F1 reconciliation note). POST + DELETE
    # = prompts:write (write-class); GET list + GET single
    # = prompts:read (read-class). The cage's parsing leg
    # validates the named scopes against SCOPE_TAXONOMY below.
    "/api/custom-agents": "prompts:read|prompts:write",
    # remote-hands center gateway (2026-10-22) — the WebSocket route the
    # controlled machines dial OUT to (SPEC.md v0.1). The route is
    # REGISTERED ONLY when MCPS_GATEWAY_ENABLED is ON, and it is
    # MODULE-LANE: the credential is the module's own bearer enrollment
    # token on the upgrade request (§3.1 / §4.1), never an OAuth2 token.
    # The route carries no OAuth2 scope decoration of its own — the
    # family is declared with the wildcard for that reason (the same
    # posture as the maintenance family's "*" member).
    "/api/mcps-gateway": "*",
}

#: The wildcard sentinel as stored in the manifest.
WILDCARD = SCOPE_WILDCARD


def _parse_scope_manifest_entry(entry: str) -> frozenset[str]:
    """``"a|b|*"`` → frozenset; validates named scopes against the taxonomy."""
    parts = frozenset(p.strip() for p in entry.split("|") if p.strip())
    for p in parts:
        assert p == WILDCARD or p in SCOPE_TAXONOMY, f"unknown scope {p!r}"
    return parts


def _resolve_entry(entry: str | frozenset[str]) -> frozenset[str]:
    if isinstance(entry, str):
        return _parse_scope_manifest_entry(entry)
    return entry


def _route_required_scopes(route: APIRoute) -> frozenset[str] | None:
    """The route's declared scope set (wrapper form OR dependency form)."""
    scopes = getattr(getattr(route, "endpoint", None), "required_scopes", None)
    if scopes:
        return frozenset(scopes)
    for dep in getattr(getattr(route, "dependant", None), "dependencies", None) or []:
        dep_scopes = getattr(dep.call, "required_scopes", None)
        if dep_scopes:
            return frozenset(dep_scopes)
    return None


def _family_of(path: str) -> str:
    """Top-2 path segments (``/api/instances/{id}/todos`` →
    ``/api/instances``) — the same grouping the P3 class manifest
    uses (several routers share a top-level prefix by design)."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        return "/" + "/".join(parts[:2])
    return "/" + (parts[0] if parts else "")


def cage_violations(
    routes: list,
    manifest: dict[str, str | frozenset[str]],
) -> list[str]:
    """The cage core: every /api route's family must be declared, and
    every declared route's scopes must be a SUBSET of its family's
    manifest entry. Returns violation strings (empty = green).

    This is the function the rogue-router leg exercises: a mount whose
    family is absent from the manifest yields a violation — the cage
    FAILS LOUDLY on omission (D4.7).
    """
    violations: list[str] = []
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        family = _family_of(path)
        if family not in manifest:
            violations.append(
                f"UNDECLARED FAMILY: {family} (route {path}) is mounted "
                "without a scope declaration in the D4.7 manifest — "
                "declare it (scopes or '*') in "
                "tests/unit/test_router_scope_manifest.py"
            )
            continue
        allowed = _resolve_entry(manifest[family])
        required = _route_required_scopes(route)
        if required is None:
            if allowed != {WILDCARD} and WILDCARD not in allowed:
                violations.append(
                    f"UNDECLARED ROUTE: {route.methods} {path} carries no "
                    f"scope decoration but family {family} is scope-pinned "
                    f"({sorted(allowed)}) — decorate it or widen the "
                    "manifest entry explicitly"
                )
            continue
        illegal = required - allowed
        if illegal:
            violations.append(
                f"SCOPE DRIFT: {route.methods} {path} declares "
                f"{sorted(required)} but family {family} manifests "
                f"{sorted(allowed)}"
            )
    return violations


class TestSourceMatchTarget:
    """The F3-pinned cage match target: ``api_router.include_router(``."""

    def test_census_pinned(self):
        """The textual census: 33 ``api_router.include_router(`` matches
        at the current tree = 31 unconditional + 2 auth-ON-gated
        (mcp-kb keys, auth-self). A drift here fails the cage and
        forces a manifest review.

        Phase B / agent-prompt-immutability T6 added the prompts_router
        (29 unconditional; was 28 pre-T6) — the cage must reflect this
        with the new /api/prompts family pinned in the manifest above.

        Phase C / agent-prompt-immutability F1 added the
        custom_agents_router (30 unconditional; was 29 pre-F1) — the
        cage must reflect this with the new /api/custom-agents
        family pinned in the manifest above.

        resource-cost-control P1 (aa95fc438) added the
        resources_router (31 unconditional; was 30 pre-P1) — the
        cage reflects this with the /api/resources family pinned in
        the manifest above.
        """
        source = API_PY.read_text()
        matches = re.findall(r"api_router\.include_router\(", source)
        assert len(matches) == 34, (
            f"api_router.include_router( census drifted: {len(matches)} "
            "(pinned 34 = 32 unconditional + 2 auth-ON-gated). New mount? "
            "Declare its family in PINNED_SCOPE_MANIFEST above."
        )


class TestScopeManifestCage:
    """The live-tree cage (auth-ON app — the full universe)."""

    @pytest.fixture(scope="class")
    def app_routes(self):
        import warnings

        warnings.filterwarnings("ignore")
        app = create_app(auth_config=on_auth_config())
        return app.routes

    def test_every_declared_route_within_manifest(self, app_routes):
        violations = cage_violations(list(app_routes), PINNED_SCOPE_MANIFEST)
        assert not violations, "\n".join(violations)

    def test_declared_route_count_pinned(self, app_routes):
        """The decoration census: 75 P4 T4.4 decorations + the 7 P3
        T3.12 mcpkb:admin keys routes + the 12 Phase B T5 prompts
        routes + the 3 USER-DIRECTIVE-#2 (2026-10-14) prompts deltas
        (PATCH /versions/{id}, DELETE /versions/{id}, GET
        /pending-count — every one @require_scope-decorated) = 97
        declared routes. A drift is a decoration added/removed
        without a manifest row change.

        Phase B / agent-prompt-immutability T5 added 12
        @require_scope-decorated routes under /api/prompts (the
        canonical 12-endpoint family per phaseB-plan.md addendum
        S4 / T5 acceptance) — the cage count was 78 pre-T6. The UD#2
        deltas (2026-10-14) grew it 90 → 93; the /api/prompts family
        entry itself is unchanged (same five prompts:* scopes).

        Phase C / agent-prompt-immutability F1 added 4
        @require_scope-decorated routes under /api/custom-agents
        (the canonical R-A1/R-A2 4-endpoint family: POST
        /api/custom-agents + GET (list) + GET (single) + DELETE)
        growing the cage count 93 → 97. The /api/custom-agents
        family entry delegates to the prompts:* scope set (the
        F1 deliberate scope-reuse convention; the family entry
        declares ``prompts:read|prompts:write`` and the actual
        scope per route maps to it — see
        ``daemon/routers/custom_agents.py`` module docstring).
        """
        declared = [
            r
            for r in app_routes
            if isinstance(r, APIRoute)
            and getattr(r, "path", "").startswith("/api/")
            and _route_required_scopes(r) is not None
        ]
        assert len(declared) == 100, (
            f"declared-route census drifted: {len(declared)} (pinned 100)"
        )

    def test_manifest_entries_valid(self):
        """Every manifest entry parses and names only taxonomy scopes
        (or the wildcard)."""
        for family, entry in PINNED_SCOPE_MANIFEST.items():
            scopes = _resolve_entry(entry)
            assert scopes, f"{family}: empty manifest entry"
            for scope in scopes:
                assert scope == WILDCARD or scope in SCOPE_TAXONOMY, (
                    f"{family}: unknown scope {scope!r}"
                )

    def test_decorated_families_actually_declared(self, app_routes):
        """Consistency both ways: a family the manifest scope-pins must
        have at least one decorated route; a wildcarded family must
        have none."""
        for family, entry in PINNED_SCOPE_MANIFEST.items():
            allowed = _resolve_entry(entry)
            decorated = _family_declared_scopes(app_routes, family)
            if allowed == {WILDCARD}:
                assert not decorated, (
                    f"{family} manifests '*' but carries decorated routes: "
                    f"{sorted(decorated)}"
                )
            else:
                named = allowed - {WILDCARD}
                assert decorated, (
                    f"{family} manifests scopes {sorted(named)} but no "
                    "route declares any — decoration missing"
                )

    def test_rogue_router_fails_loudly(self):
        """THE G4.5 negative leg: a mount whose family is absent from
        the manifest produces a cage violation NAMING the family (the
        failure mode for the next undeclared ``include_router``)."""
        rogue = APIRoute(
            "/api/rogue/items",
            lambda: None,
            methods=["GET"],
        )

        class _RogueFamily:
            pass

        violations = cage_violations([rogue], dict(PINNED_SCOPE_MANIFEST))
        assert any("UNDECLARED FAMILY" in v and "/api/rogue" in v for v in violations), (
            "the cage must fail loudly on an undeclared mount"
        )

    def test_scope_drift_detected(self):
        """A route declaring a scope OUTSIDE its family's manifest
        entry is flagged (declaration drift, not just omission)."""
        route = APIRoute(
            "/api/schedules/{id}",
            lambda: None,
            methods=["PUT"],
        )
        # Simulate the wrapper-form declaration tag on the endpoint.
        route.endpoint.required_scopes = ("instances:write",)
        violations = cage_violations([route], dict(PINNED_SCOPE_MANIFEST))
        assert any("SCOPE DRIFT" in v for v in violations)


def _family_declared_scopes(routes: list, family: str) -> dict[str, frozenset[str]]:
    """path → declared scopes for every decorated route in ``family``."""
    out: dict[str, frozenset[str]] = {}
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        if _family_of(getattr(route, "path", "")) != family:
            continue
        required = _route_required_scopes(route)
        if required is not None:
            out[route.path] = required
    return out
