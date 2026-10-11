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
    "/api/settings": "snapshot:write|*",
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
    "/api/tmp_images": "*",
    "/api/webhooks": "*",                   # MODULE-LANE (D19) — never OAuth2-gated
    "/api/work": "*",
    "/api/missions": "*",
    "/api/migration": "*",                  # SSE stream family
    "/api/auth": "*",                       # public-probe family (§5.3)
    "/api/health": "*",                     # PUBLIC
    "/api/info": "*",                       # PUBLIC
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
        """The textual census: 30 ``api_router.include_router(`` matches
        at the current tree = 28 unconditional + 2 auth-ON-gated
        (mcp-kb keys, auth-self). A drift here fails the cage and
        forces a manifest review."""
        source = API_PY.read_text()
        matches = re.findall(r"api_router\.include_router\(", source)
        assert len(matches) == 30, (
            f"api_router.include_router( census drifted: {len(matches)} "
            "(pinned 30 = 28 unconditional + 2 auth-ON-gated). New mount? "
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
        """The decoration census: 71 P4 T4.4 decorations + the 7 P3
        T3.12 mcpkb:admin keys routes = 78 declared routes. A drift is
        a decoration added/removed without a manifest row change."""
        declared = [
            r
            for r in app_routes
            if isinstance(r, APIRoute)
            and getattr(r, "path", "").startswith("/api/")
            and _route_required_scopes(r) is not None
        ]
        assert len(declared) == 78, (
            f"declared-route census drifted: {len(declared)} (pinned 78)"
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
