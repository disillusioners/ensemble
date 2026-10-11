"""Route auth-classification metadata — oauth2-auth Phase 3 (T3.8/T3.10).

The D18 / D3.3-AMEND-v1.4 three-class derivation substrate. Routes carry
CLASS ATTRIBUTES set at registration time by the helpers below; the
middleware construction (:class:`daemon.middleware.auth.AuthMiddleware`)
walks ``app.routes`` and derives the O(1) path-class sets from these
attributes — the runtime sets are never hand-listed (the hand-listed
frozenset was REJECTED per D18: drift hazard).

Three classes (FR-22a v1.4 / D19):

* ``public-probe``   → :func:`tag_public` — reachable with no credential
  at all (health probes, auth-self routes, docs, SPA shell/assets).
* ``module-lane``    → :func:`tag_module_lane` — the path's auth is OWNED
  BY ITS MODULE (webhook ``X-Webhook-Secret``, MCP-KB keys); the OAuth2
  middleware passes it through UNTOUCHED in BOTH auth states
  (FR-39/FR-42). Semantically distinct from public: module-lane is NOT
  "no auth anywhere", it is "auth invisible to OAuth2".
* untagged           → oauth2-protected (DEFAULT-DENY). Any route a
  future feature adds without a tag is protected — the safe default
  (AC-23 anti-smuggle).

Template matching semantics (uniform for all classes): a route template
without parameters matches exact; a parametrized template
(``/a/{x}/b``) matches its static prefix with a path-segment boundary
(``/a`` or ``/a/...``) — the same first-match-wins shape Starlette
resolves with, kept derivation-time and cheap.
"""

from __future__ import annotations

from typing import Any

#: Attribute names set on Starlette/FastAPI route objects.
AUTH_PUBLIC_ATTR = "auth_public_probe"
AUTH_MODULE_LANE_ATTR = "auth_module_lane"
#: Explicit-protected tag — routes that must beat an EARLIER-prefix
#: module-lane Mount at the ordered-resolution step (the
#: ``/api/mcp/kb/keys`` family vs the ``/api/mcp/kb`` Mount).
AUTH_PROTECTED_ATTR = "auth_protected"


def tag_public(route: Any) -> Any:
    """Mark a route object PUBLIC (``public-probe`` tag, D18)."""
    setattr(route, AUTH_PUBLIC_ATTR, True)
    return route


def tag_module_lane(route: Any) -> Any:
    """Mark a route object MODULE-LANE (v1.4, D19 — module owns auth)."""
    setattr(route, AUTH_MODULE_LANE_ATTR, True)
    return route


def tag_protected(route: Any) -> Any:
    """Explicitly mark a route OAUTH2-PROTECTED (T3.12 keys family).

    Same semantics as the untagged default, but registered in the
    ordered-resolution list so the route wins over a module-lane Mount
    whose path is a prefix of it (Starlette first-match-wins mirror).
    """
    setattr(route, AUTH_PROTECTED_ATTR, True)
    return route


def tag_routes_by_path(
    app: Any,
    *,
    path_prefix: str | None = None,
    exact_path: str | None = None,
    module_lane: bool = False,
    protected: bool = False,
) -> int:
    """Tag already-registered routes by path prefix / exact template.

    Used at the registration sites in ``create_app`` where the route
    objects are not held individually (``include_router`` families,
    ``app.mount`` Mounts). Returns the number of routes tagged.
    Mounts (Starlette ``Mount``) have a plain ``.path`` prefix — the
    boundary rule below applies unchanged. Exactly one of ``module_lane``
    / ``protected`` may be set; the default tags PUBLIC.
    """
    tagged = 0
    for route in getattr(app, "routes", []) or []:
        route_path = getattr(route, "path", None)
        if not isinstance(route_path, str):
            continue
        if exact_path is not None and route_path != exact_path:
            continue
        if path_prefix is not None:
            if not _template_prefix_matches(route_path, path_prefix):
                continue
        if module_lane:
            tag_module_lane(route)
        elif protected:
            tag_protected(route)
        else:
            tag_public(route)
        tagged += 1
    return tagged


def template_matches(template: str, path: str) -> bool:
    """Does a request ``path`` match a route ``template``?

    * Static template (no ``{param}``): exact string equality.
    * Parametrized template: the template truncated at the first ``{``
      is the static base; ``path`` must equal the base (with the trailing
      slash dropped) or extend it on a segment boundary.
    """
    if "{" not in template:
        return template == path
    base = template.split("{", 1)[0]
    if base.endswith("/"):
        return path.startswith(base)
    return path == base or path.startswith(base + "/")


def _template_prefix_matches(template: str, prefix: str) -> bool:
    """Template-vs-prefix test used by :func:`tag_routes_by_path`."""
    if "{" in template:
        template = template.split("{", 1)[0]
    return template == prefix or template.startswith(prefix)


__all__ = [
    "AUTH_MODULE_LANE_ATTR",
    "AUTH_PROTECTED_ATTR",
    "AUTH_PUBLIC_ATTR",
    "tag_module_lane",
    "tag_protected",
    "tag_public",
    "tag_routes_by_path",
    "template_matches",
]
