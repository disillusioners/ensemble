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

view_link() returns a URL + 6-char passcode for a (root-name,
relative-path) pair in the live-view subsystem. The PUBLIC URL
+ passcode lane is gated daemon-side: an agent calls
view_link, receives the public URL + passcode, and the customer
opens the URL → zero-JS interstitial → passcode entry → view.
The tool mints a fresh passcode row on first call (lazy-mint),
returns the same passcode on every subsequent call (idempotent
default), and bumps the passcode generation on explicit
``refresh=True`` (silently revokes outstanding grant cookies).
The DIRECT URL (``authenticated_url`` in the result) is the
legacy ``/views/<root>/<path>`` shape — unchanged.

view_link has DB side effects (minting writes the encrypted
passcode row + a ``mint`` audit row; ``refresh=True`` writes a
new passcode + bumps generation + clears any active lock +
writes a ``regenerate`` audit row). The privilege scope is the
existing ``view-views`` tool category (allow-listed on ari /
leader / designer); the public lane verify POST is the
unauthenticated inverse direction (the daemon-side check
that consumes the passcode the tool minted).

URL bases (path-relative ``/views/<root>/<path>`` /
``/views/public/<root>/<path>`` when ``live_views.external_base_url``
is unset; fully-qualified ``<base>/...`` when set). Unknown /
disabled roots, malformed paths, reserved-name roots (the
``public`` root is reserved for the public lane), and
subsystem-off all return a typed ``"Error: ..."`` envelope;
never a partial URL, never a crash.
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
    def view_link(
        root_name: str, path: str, refresh: bool = False
    ) -> str:
        """Mint (or re-display) a passcode-gated public URL for a view.

        P2 of live-view-passcode (2026-10-17). The tool is the
        agent-facing minting surface for the public passcode
        lane (architecture-recommendation.md D8 + D10;
        phase2-plan.md T2.8). It mints (or re-displays) the
        passcode row for ``(root_name, path)`` and returns the
        ADR D10 labeled-line shape with FIVE fields:

        * ``Public URL`` — the URL the customer opens without
          any ensemble credential. Path is
          ``/views/public/<root>/<path>`` (FROZEN prefix;
          architecture-recommendation.md D11:128). Fully
          qualified when ``live_views.external_base_url`` is
          configured.
        * ``Passcode`` — the 6-character code from the FROZEN
          D4 alphabet (architecture-recommendation.md D4).
          ALWAYS returned on every call (first-mint /
          idempotent / locked — the owner must see why
          ``refresh=True`` is needed).
        * ``Authenticated URL`` — the legacy direct URL
          (``/views/<root>/<path>``). Unchanged by this
          feature; preserved so an agent that needs the
          behind-OAuth-proxy URL still has it in one call.
        * ``Generation`` — the passcode row's generation
          counter; incremented by ``refresh=True`` (silently
          revokes ALL outstanding grant cookies — ADR D3).
        * ``Locked`` — ``yes`` iff the row is currently
          locked (10+ wrong verify attempts). When locked,
          the result includes a ``refresh=True`` hint line
          (the only recovery path).

        Always-passcode contract (ADR D10): ``Passcode`` is
        present on every call. The tool does NOT return a URL
        without a passcode — the public lane is the whole
        point of this feature.

        Default ``refresh=False`` is IDEMPOTENT (ADR D5
        sharpening — does NOT clear an active lock; the
        owner decides). Two consecutive calls return the
        SAME passcode + generation + locked status. Use
        ``refresh=True`` to bump the generation + clear the
        lock + emit a new passcode.

        Side effects (DOC DEBT — ADR D10:118):

        * ``refresh=False`` first call — INSERT passcode row
          + ``mint`` audit row in one transaction.
        * ``refresh=False`` repeat — no DB writes; the row is
          re-read and the plaintext is decrypted for
          re-display (D6 deviation — plaintext-at-mint is
          forced by the always-re-display contract).
        * ``refresh=True`` — UPDATE passcode (new code +
          generation+1 + counters reset + locked cleared) +
          ``regenerate`` audit row in one transaction.

        URL bases (mirrors ``build_url`` + the new
        :meth:`LiveViewsService.build_public_url`):

        * Path-relative ``/views/<root>/<path>`` +
          ``/views/public/<root>/<path>`` when
          ``live_views.external_base_url`` is unset.
        * Fully-qualified ``<base>/...`` when configured.

        Roots available (config-driven; operator may add or
        disable via ``live_views.roots`` in config.yaml +
        restart). Reserved: ``public`` is reserved for the
        passcode lane (architecture-recommendation.md D1:27)
        and is rejected at config load — the tool returns an
        ``Error: ...`` envelope if a future drift lets one
        slip through.

        * ``designer-artifact`` — the canonical
          ``<project_workdir>/.agents/shared/planning/<feature>/design/mockups/``
          subtree of the project the URL names. URL shape:
          ``/views/designer-artifact/<project_shortname>/<feature>/design/mockups/<file>``.
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
                depends on the root (see the per-root bullets
                above).
            refresh: When ``True``, regenerate the passcode
                (new code, generation+1, counters reset,
                lock cleared). When ``False`` (the default),
                the call is idempotent — existing rows return
                the current plaintext + locked status without
                clearing the lock (ADR D5 sharpening).

        Returns:
            A stable labeled-line string with the five
            ``Public URL`` / ``Passcode`` / ``Authenticated
            URL`` / ``Generation`` / ``Locked`` fields (+ a
            ``refresh=True`` hint line when locked). On any
            rejection (unknown / disabled / malformed /
            subsystem-off / cipher unavailable) the tool
            returns a typed ``"Error: ..."`` string the
            caller can surface to the user. The plaintext
            passcode NEVER crosses any log line (architecture-
            recommendation.md D11:131 — logging hygiene).
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
        from daemon.services.live_views import (
            is_reserved_root_name,
            is_well_formed_rel_path,
            is_well_formed_root_name,
        )
        if not is_well_formed_root_name(root_name):
            return (
                f"Error: malformed root_name {root_name!r} "
                "(must match kebab-case identifier)"
            )
        if is_reserved_root_name(root_name):
            # Three-layer guard mirrors the registry validator
            # (architecture-recommendation.md D1:27); the
            # router already 404s this at request time, but
            # the tool surfaces the typed rejection so the
            # agent knows which name collided.
            return (
                f"Error: root_name {root_name!r} is reserved "
                "(the public passcode lane occupies this "
                "name); rename the root in config and restart."
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
        authenticated_url = service.build_url(root_name, path)
        if authenticated_url is None:
            return f"Error: could not build URL for {root_name!r} / {path!r}"
        public_url = service.build_public_url(root_name, path)
        if public_url is None:
            # Reserved-name guard etc. — typed envelope.
            return f"Error: could not build public URL for {root_name!r} / {path!r}"
        # Passcode mint / refresh path. The service is wired
        # onto ``app.state.view_passcode_service`` at lifespan
        # time; missing here → the subsystem was not wired
        # (cipher missing or DB unreachable) — typed envelope.
        passcode_service = getattr(
            manager, "_view_passcode_service", None
        )
        if passcode_service is None:
            # The InstanceManager exposes the passcode service
            # via the same canonical setter shape as the live-
            # views service (manager facade is the canonical
            # seam tools reach shared per-app state through);
            # it may live on app.state or manager._view_passcode
            # depending on how the lifespan wired it. Both
            # reads collapse to the same typed envelope on
            # absence.
            passcode_service = getattr(
                manager, "view_passcode_service", None
            )
        if passcode_service is None:
            return (
                "Error: view-passcode service not initialized "
                "(missing SYSTEM_ENCRYPTION_KEY or lifespan "
                "did not wire it; check daemon logs)."
            )
        # Mint or refresh — the agent id (Cardinal #2) is the
        # privileged mint actor. ``current_instance_id`` is
        # carried by the tool factory closure (parity with
        # sibling factories) — we use it as the actor label
        # when present so the audit row names the caller.
        actor = (
            current_instance_id
            if isinstance(current_instance_id, str)
            and current_instance_id
            else None
        )
        try:
            if refresh:
                result = passcode_service.refresh(
                    root_name, path, actor
                )
            else:
                result = passcode_service.mint(
                    root_name, path, actor
                )
        except Exception as exc:
            # Cipher rotation-fail (the canonical primitive
            # — architecture-recommendation.md D4:59 + D9:117)
            # or any other service-level rejection. Typed
            # envelope; never a stack trace to the agent.
            from daemon.services.view_passcode_service import (
                ViewPasscodeNotFoundError,
                ViewPasscodeRotationRequired,
            )
            if isinstance(exc, ViewPasscodeRotationRequired):
                return (
                    "Error: stored passcode ciphertext cannot "
                    "be decrypted — the SYSTEM_ENCRYPTION_KEY "
                    "may have been rotated without re-encrypting "
                    "the live_view_passcodes table. The owner "
                    "should regenerate the passcode (operator "
                    "rotation ceremony must include the "
                    "wrapped_passcode re-encrypt walk)."
                )
            if isinstance(exc, ViewPasscodeNotFoundError):
                # ``refresh=True`` on a never-minted path —
                # the lazy-mint posture says mint first, then
                # refresh; surface the typed envelope so the
                # agent knows the order.
                return (
                    f"Error: no passcode row for "
                    f"{root_name!r}/{path!r}; call "
                    f"view_link() once first (lazy-mint) "
                    f"and then refresh=True to regenerate."
                )
            return (
                f"Error: passcode service rejected the "
                f"call: {type(exc).__name__}: {exc}"
            )
        # The 5-field labeled-line shape (ADR D10:118).
        # Stable, greppable, easily diffed. The locked
        # status carries a refresh hint (the only recovery
        # path).
        locked_yes = "yes" if result.locked_status else "no"
        hint = ""
        if result.locked_status:
            hint = (
                f"\nHint: passcode is locked — call "
                f"view_link(root_name=..., path=..., "
                f"refresh=True) to regenerate."
            )
        return (
            f"Public URL: {public_url}\n"
            f"Passcode: {result.passcode}\n"
            f"Authenticated URL: {authenticated_url}\n"
            f"Generation: {result.generation}\n"
            f"Locked: {locked_yes}"
            f"{hint}"
        )

    return [view_link]
