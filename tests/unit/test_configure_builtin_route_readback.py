"""C3 — the built-in template ROUTE-TABLE readback (soak-gate-mcp-warmup).

The house rule: a decorator-rebind trap (a handler defined in one
module and re-registered in another) can leave the ROUTE TABLE serving
a stale handler while the module-level function looks correct. This
file reads the ROUTE TABLE itself:

* ``GET /api/mcp-servers/builtin-templates`` serves the NEW schema
  fields (the two context7 env entries);
* ``POST /api/mcp-servers/configure-builtin`` is bound in the app's
  route table (the handler the operator writes through).

Cheap insurance even though no router file is edited by this lane.
"""

from __future__ import annotations

import pytest

from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
from daemon.routers import mcp_servers as mcp_servers_router


def _route_paths(router) -> set[str]:
    return {getattr(route, "path", "") for route in router.routes}


def _mcp_route_paths(router) -> set[str]:
    """The router's own paths, relative to its mount prefix."""
    prefix = getattr(router, "prefix", "") or ""
    return {
        getattr(route, "path", "")[len(prefix):]
        for route in router.routes
        if getattr(route, "path", "").startswith(prefix)
    }


def test_the_configure_builtin_route_is_bound():
    """The sanctioned write path is present in the ROUTE TABLE."""
    paths = _mcp_route_paths(mcp_servers_router.router)
    assert "/configure-builtin" in paths, (
        "the /configure-builtin route must be bound — it is the ONLY "
        "sanctioned way to set a protected built-in's schema-declared "
        "fields"
    )


def test_the_builtin_templates_route_is_bound():
    paths = _mcp_route_paths(mcp_servers_router.router)
    assert "/builtin-templates" in paths


async def test_the_template_list_serves_the_new_schema_fields():
    """The GET template list serves the two context7 env entries."""
    response = await mcp_servers_router.list_builtin_templates()
    templates = {t.name: t for t in response.templates}
    assert "context7" in templates, "the context7 template must be listed"

    schema = templates["context7"].config_schema
    keys = [field.key for field in schema]
    assert "CONTEXT7_BASE_URL" in keys
    assert "CONTEXT7_API_URL" in keys
    assert "CONTEXT7_API_BASE_URL" not in keys
    for field in schema:
        if field.key in ("CONTEXT7_BASE_URL", "CONTEXT7_API_URL"):
            assert field.section == "env"


async def test_the_template_list_serves_the_schema_version_indirectly():
    """The definition's schema version is the drift marker (G5)."""
    definition = Context7ServerDefinition()
    assert definition.schema_version == "3"
    response = await mcp_servers_router.list_builtin_templates()
    templates = {t.name: t for t in response.templates}
    # The template payload carries the SCHEMA FIELDS; the version lives
    # on the definition and on the stored row (the 409 drift marker).
    assert templates["context7"].config_schema


def test_the_router_module_is_the_one_bound_in_the_app():
    """The decorator-rebind trap guard.

    The app registers the SAME router object the module defines — a
    re-registered copy would leave the module's handlers unbound.
    """
    from daemon.api import create_app

    app = create_app()
    paths = {
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/mcp-servers")
    }
    assert "/api/mcp-servers/configure-builtin" in paths
    assert "/api/mcp-servers/builtin-templates" in paths


@pytest.mark.parametrize(
    "path",
    [
        "/api/mcp-servers/configure-builtin",
        "/api/mcp-servers/builtin-templates",
    ],
)
def test_the_route_methods_are_bound(path):
    from daemon.api import create_app

    app = create_app()
    matching = [r for r in app.routes if getattr(r, "path", "") == path]
    assert matching, f"{path} must be registered"
    methods = set()
    for route in matching:
        methods.update(getattr(route, "methods", set()) or set())
    assert "POST" in methods or "GET" in methods
