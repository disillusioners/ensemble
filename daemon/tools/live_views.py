"""``view_link`` tool — agent-facing URL minter for the live-view subsystem.

Phase 1 of the live-view subsystem (2026-10-07). The tool is the
AGENT-FACING minting surface: given a root-name + relative path,
return a URL an agent can drop into a spec / implement-brief /
chat message. The HTTP route family at ``/views/<root>/<path>`` is
the OTHER side of the same coin — the tool mints, the route serves.

DESIGN DECISIONS (cross-referenced in the report):

* **RESTRICTED first-release visibility (REWORK 2026-10-07, M1,
  user refinement #1).** ``view_link`` is in ``KNOWN_TOOL_NAMES``
  (inventory, NOT the gate) AND its ``view-views`` category is in
  ``PRIVILEGED_TOOL_CATEGORIES`` (the gate). The empty-allow
  inherit universe must NOT auto-grant it. The three
  commissioned users (ari, leader, designer) opt in explicitly
  via ``tools.allow: ["view-views"]`` in their meta.json. The
  designer-side entry pre-existed; the ari + leader entries are
  the REWORK additions. Route stays general, extensibility =
  per-agent allow entries + per-root config (not a code change).
* **Read-only.** The tool mints URLs; it does NOT touch the
  filesystem, the database, or any service beyond the
  per-app ``LiveViewsService`` registry. The
  ``view-views`` category's presence in the privileged set is
  the user-driven visibility decision, NOT a tool-side
  capability flag — the same convention as the public-by-obscurity
  file-serving shape of ``image_get``.
* **URL base resolution.** Path-relative ``/views/<root>/<path>``
  by default; fully-qualified ``<external_base_url>/views/...``
  when ``config.live_views.external_base_url`` is set. The
  default is the safe one because the daemon has no canonical
  public hostname; the operator opts INTO the qualified form
  for an OAuth-fronted deployment.
* **Failure mode.** Unknown / disabled root, malformed
  relative path, or subsystem-off → the tool returns a typed
  ``"Error: ..."`` string. Never a partial URL, never a
  raise, never a crash. Mirrors the ``image_get`` tool's
  "fail closed with a typed envelope" pattern.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


CATEGORY_NAME = "Live Views"
CATEGORY_DOC = """\
Live-view subsystem URL minter.

view_link() returns a URL for a (root-name, relative-path) pair
in the live-view subsystem. The same URL is served by the
GET/HEAD ``/views/<root>/<path>`` HTTP route family; the tool is
the agent-facing minting surface. Read-only — no filesystem or
database side effects.

URL base: path-relative ``/views/<root>/<path>`` when
``live_views.external_base_url`` is unset (the default —
recommended for behind-OAuth-proxy deployments); fully-qualified
``<external_base_url>/views/...`` when set. Unknown / disabled
roots, malformed paths, and subsystem-off all return a typed
``"Error: ..."`` envelope; never a partial URL, never a crash.
"""


def create_live_view_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create the ``view_link`` tool with the manager's live-views service.

    Args:
        manager: The InstanceManager instance. Used for its
            ``live_views_service`` property (Phase 1 — wired by
            the lifespan at daemon/api.py). Tests that
            construct ``InstanceManager`` directly may pass a
            manager whose service is ``None``; the tool fails
            closed with a typed envelope.
        current_instance_id: The current instance ID. Accepted
            for parity with other factories; not used by
            ``view_link`` (URL minting is project-agnostic by
            design — the URL is the same regardless of which
            instance minted it).

    Returns:
        A list of one tool: ``[view_link]``.
    """

    @register_tool_category("view-views")
    @tool
    def view_link(root_name: str, path: str) -> str:
        """Mint a URL for a live-view (root, path) pair.

        Read-only. Returns a URL string the caller can embed in
        a spec, implement-brief, chat message, or anywhere else
        a human or downstream tool will resolve the artifact.

        URL shape:

        * Path-relative ``/views/<root>/<path>`` when
          ``config.live_views.external_base_url`` is unset
          (the default — recommended for behind-OAuth-proxy
          deployments; the daemon has no canonical public
          hostname, so a path-relative URL is the safe mint).
        * Fully-qualified ``<external_base_url>/views/<root>/<path>``
          when ``external_base_url`` is configured.

        Roots available in Phase 1 (config-driven; operator
        may add or disable via ``live_views.roots`` in
        config.yaml + restart):

        * ``designer-artifact`` — the canonical
          ``<project_workdir>/.agents/shared/planning/<feature>/design/mockups/``
          subtree of the project the URL names. URL shape:
          ``/views/designer-artifact/<project_shortname>/<feature>/design/mockups/<file>``
          (project-scoped; the first URL segment after the
          root is the project shortname, exactly like
          ``planning``). REWORK 2026-10-07 (M2): the root is
          project_scoped so an anonymous browser hit resolves
          without a per-instance workdir binding.
        * ``planning`` — every project's
          ``.agents/shared/planning/`` tree. URL shape:
          ``/views/planning/<project_shortname>/<path>``.
        * ``tmp-images`` — the daemon tmp-image substrate
          (sidecar-MIME; blobs are extensionless). URL
          shape: ``/views/tmp-images/<32hex>``.

        Visibility (REWORK 2026-10-07, M1). The
        ``view-views`` category is RESTRICTED at the gate
        (privileged entry; empty-allow agents do NOT get
        this tool). The commissioned users are ari, leader,
        and designer — they carry ``view-views`` in their
        ``tools.allow``. Other agents calling this tool get
        a typed ``"Error: tool not in this agent's allow
        list"`` envelope from the tool-resolver layer
        (returned upstream of this function — by the time
        the code below runs, the agent is already one of
        the three).

        Args:
            root_name: The registered root name (e.g.
                ``"designer-artifact"``, ``"planning"``,
                ``"tmp-images"``).
            path: The relative path under the root. Shape
                depends on the root:

                * ``designer-artifact`` — REWORK 2026-10-07
                  (M2): the root is project_scoped, so the
                  path is ``<project_shortname>/<feature>/design/mockups/<file>``,
                  i.e. the project shortname + the row's
                  ``path`` value verbatim (e.g.
                  ``"ens/feat/design/mockups/landing.html"``).
                  The M3 ``required_rel_subpath``
                  (``["design", "mockups"]``) is enforced
                  server-side — passing a path missing the
                  trailing ``design/mockups/`` segment yields
                  a uniform 404.
                * ``planning`` — the first path segment is the
                  project shortname and the rest is the path
                  under that project's planning tree
                  (e.g. ``"ens/feat/plan.md"``).
                * ``tmp-images`` — the bare 32-hex image id
                  (sidecar-MIME; blobs are extensionless).

        Returns:
            The canonical URL string on success. On any
            rejection (unknown / disabled root, malformed
            path, subsystem off) returns a typed ``"Error: ..."``
            string the caller can surface to the user.
        """
        service = manager.live_views_service
        if service is None:
            return (
                "Error: live-views service not initialized "
                "(lifespan did not wire it; check daemon logs)."
            )
        if not service.enabled():
            return "Error: live-views subsystem is disabled"
        if not isinstance(root_name, str) or not root_name.strip():
            return "Error: root_name must be a non-empty string"
        if not isinstance(path, str) or not path.strip():
            return "Error: path must be a non-empty string"
        # Defense-in-depth: the service has the same checks,
        # but echoing them here keeps the tool surface
        # self-contained and gives the agent a typed error
        # with the exact name that failed.
        from daemon.services.live_views import is_well_formed_root_name, is_well_formed_rel_path
        if not is_well_formed_root_name(root_name):
            return (
                f"Error: malformed root_name {root_name!r} "
                "(must match kebab-case identifier)"
            )
        if not is_well_formed_rel_path(path):
            return (
                f"Error: malformed path {path!r} "
                "(no '..' segments, no leading '/', no control chars)"
            )
        if not service.has_root(root_name):
            return (
                f"Error: unknown or disabled root {root_name!r} "
                f"(configured: {service.root_names() or '[]'})"
            )
        url = service.build_url(root_name, path)
        if url is None:
            # A misconfigured root or other unexpected miss —
            # keep the envelope typed.
            return f"Error: could not build URL for {root_name!r} / {path!r}"
        return url

    return [view_link]
