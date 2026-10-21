"""``board_link`` tool — mint the passcode-gated public link for the LIVE board.

ari-tools Phase 3, deliverable B. The board is **server-rendered from the
DB on every request** — it is NOT a file, and it is NOT a rewritten static
page. The tool therefore mints exactly ONE thing: the passcode row for
``(board, "index.html")``, plus the two URLs the customer/owner needs.

Why a dedicated tool (and not ``view_link``)
--------------------------------------------
``board`` is a **RESERVED root name** (``daemon/services/live_views.py``
— ``RESERVED_ROOT_NAMES``). The reservation is what makes the board
impossible to shadow:

* an operator cannot declare a root named ``board`` (the config
  validator refuses to boot);
* the direct lane ``/views/board/index.html`` 404s;
* ``view_link("board", …)`` returns the reserved-name envelope — the
  board's link is NOT mintable through the ``view-views`` category
  (which the designer holds).

``board_link`` is the SANCTIONED minting path. Its own module + its own
category (``board-link``) keeps Phase 3 free of any coupling to the
Phase-1 ``board`` data tools (D13) and keeps the audience explicit:
ari may MINT the link; mint ≠ read.

Access posture — THREE layers, ALL required
-------------------------------------------
1. The ``board-link`` category is PRIVILEGED (entry #12 of
   ``PRIVILEGED_TOOL_CATEGORIES``) ⇒ it never joins the empty-allow
   default universe.
2. ``agents/ari/meta.json`` carries the explicit ``"board-link"``
   allow entry — and no other agent does.
3. The tool body calls the shared
   :func:`daemon.tools.views_auth._is_views_authorized` gate — the FIRST
   statement in the body, BEFORE the mint.

Layer 3 is load-bearing: a **literal tool-name** allow entry bypasses
the category gate outright (``daemon/tools/instance.py``), so layers 1 + 2
alone do not enforce the audience. The gate is fail-closed: a blank /
non-``str`` ``agent_id`` is NEVER authorized.

The gate set is ``{ari}`` — the user ruling of 2026-10-20 (Discord,
direct) is BINDING and RETRACTS the earlier ``{ari, leader}``
audience. Both sets live side by side in
``daemon/tools/views_auth.py`` so the audience of each is visible in one
place.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Annotated

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category
from .views_auth import BOARD_LINK_AGENTS, _is_views_authorized

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


CATEGORY_NAME = "Board Link"
CATEGORY_DOC = """\
Mint the passcode-gated public link for the LIVE work board.

Tool in this category:
- ``board_link`` — mint (or regenerate) the public URL + passcode for
  the server-rendered board at ``/views/public/board/index.html``

The board is rendered FRESH FROM THE DATABASE on every request — it is
never a static file Ari rewrites. ``board_link`` mints the ONE
passcode row that authorises reading it; the page itself always shows
the current rows (statuses, tags, due dates, the notes timeline).

``board_link()`` is idempotent: calling it again returns the SAME
passcode and generation. ``board_link(refresh=True)`` issues a NEW
passcode, bumps the generation (revoking every outstanding grant cookie)
and clears a lockout — that is the only recovery path after 10 wrong
attempts.

The board's root name (``board``) is RESERVED: the router owns the URL
and no file root may shadow it. The "Authenticated URL" line is
INFORMATIONAL — the direct lane ``/views/board/index.html`` 404s by
design, because the board is a DB-rendered public surface with no
behind-the-OAuth variant.

Access: ``board-link`` is a PRIVILEGED category (ari-only) AND the
tool body re-checks the calling agent. Every failure returns a typed
``"Error: ..."`` string — never a partial URL, never a crash.
"""

#: The reserved root name the board lives under.
BOARD_ROOT_NAME = "board"

#: The ONE relative path the board is served at — the passcode key is
#: exactly ``(board, "index.html")``. Never per-filter rows: a
#: passcode authorises "may this person see the board", not "may they
#: see a filtered subset" (D11).
BOARD_REL_PATH = "index.html"


def _error(msg: str) -> str:
    """Phase-3 house error envelope — tools never raise.

    The convention is ``Error:`` (the ``view_link`` family convention),
    NOT phase 1's ``ERROR:``.
    """
    return f"Error: {msg}"


def create_board_link_tools(
    manager: "InstanceManager",
    *,
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    """Create the ``board_link`` tool for one instance/agent.

    Args:
        manager: The ``InstanceManager`` instance. Used for its
            ``live_views_service`` and passcode-service properties.
        current_instance_id: The caller's instance id. Stamped onto the
            passcode mint as the audit actor.
        agent_id: The CALLING agent's id — the layer-3 in-body gate's
            input. Defaults to ``""`` ⇒ FAIL-CLOSED.

    Returns:
        A list of one tool: ``[board_link]``.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""

    class BoardLinkInput(BaseModel):
        """Input schema for board_link."""

        refresh: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Issue a NEW passcode: bump the generation (revoking "
                    "every outstanding grant cookie) and clear a lockout. "
                    "This is the only recovery path after 10 wrong attempts."
                ),
            ),
        ]

    @register_tool_category("board-link")
    @tool(args_schema=BoardLinkInput)
    async def board_link(
        refresh: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Issue a NEW passcode: bump the generation (revoking "
                    "every outstanding grant cookie) and clear a lockout."
                ),
            ),
        ] = False,
    ) -> str:
        """Mint (or regenerate) the passcode-gated public link for the board.

        The board at ``/views/public/board/index.html`` is rendered FRESH
        FROM THE DATABASE on every request — it always shows the current
        rows, never a stale snapshot. This tool mints the ONE passcode
        row that authorises reading it.

        Args:
            refresh: Issue a NEW passcode. The generation is bumped, every
                outstanding grant cookie is revoked, and a lockout is
                cleared. This is the only recovery path after 10 wrong
                attempts. The default (``False``) is IDEMPOTENT — the same
                passcode and generation come back.

        Returns:
            The five-line labeled shape::

                Public URL: /views/public/board/index.html
                Passcode: 7KQ2ZM
                Authenticated URL: /views/board/index.html
                Generation: 1
                Locked: no

            The ``Authenticated URL`` line is INFORMATIONAL — the direct
            lane 404s by design (the board is a DB-rendered public
            surface). On any rejection, a typed ``"Error: ..."`` string.
        """
        # ── LAYER 3: the in-body privilege gate ──────────────────
        # FIRST statement in the body — BEFORE the mint. An
        # unauthorized caller mints NO row.
        if not _is_views_authorized(caller_agent_id, BOARD_LINK_AGENTS):
            return _error(
                "board_link is restricted to the 'ari' agent "
                f"— this call came from agent {caller_agent_id!r}."
            )

        from daemon.services.live_views import PUBLIC_NAMESPACE

        # ── service wiring ───────────────────────────────────────
        service = getattr(manager, "live_views_service", None)
        if service is None:
            return _error(
                "live-views service not initialized "
                "(lifespan did not wire it; check daemon logs)."
            )
        if not service.enabled():
            # The board GET would be a permanent 404 while the row
            # exists — never hand out a dead URL (A-36).
            return _error("live-views subsystem is disabled")
        passcode_service = getattr(manager, "_view_passcode_service", None)
        if passcode_service is None:
            passcode_service = getattr(manager, "view_passcode_service", None)
        if passcode_service is None:
            return _error(
                "view-passcode service not initialized "
                "(missing SYSTEM_ENCRYPTION_KEY or lifespan did not wire "
                "it; check daemon logs)."
            )

        # ── mint / refresh ───────────────────────────────────────
        # The passcode service is SYNCHRONOUS (DB-backed) ⇒ in this
        # async body the call MUST be wrapped in ``to_thread`` —
        # exactly ONE crossing around the single sync call.
        from daemon.services.view_passcode_service import (
            ViewPasscodeNotFoundError,
            ViewPasscodeRotationRequired,
        )

        actor = caller_instance_id or None
        try:
            if refresh:
                try:
                    result = await asyncio.to_thread(
                        passcode_service.refresh,
                        BOARD_ROOT_NAME,
                        BOARD_REL_PATH,
                        actor,
                    )
                except ViewPasscodeNotFoundError:
                    # Nothing to regenerate yet — mint the fresh link
                    # instead, so a FIRST call with refresh=True works
                    # in one call (the lazy-mint posture).
                    result = await asyncio.to_thread(
                        passcode_service.mint,
                        BOARD_ROOT_NAME,
                        BOARD_REL_PATH,
                        actor,
                    )
            else:
                result = await asyncio.to_thread(
                    passcode_service.mint,
                    BOARD_ROOT_NAME,
                    BOARD_REL_PATH,
                    actor,
                )
        except Exception as exc:
            if isinstance(exc, ViewPasscodeRotationRequired):
                return _error(
                    "stored passcode ciphertext cannot be decrypted — the "
                    "SYSTEM_ENCRYPTION_KEY may have been rotated without "
                    "re-encrypting the live_view_passcodes table. The owner "
                    "should regenerate the passcode."
                )
            logger.warning("[board_link] passcode mint failed: %s", exc)
            return _error(
                f"passcode service rejected the call: {type(exc).__name__}"
            )

        # ── URLs ─────────────────────────────────────────────────
        # ``board`` is RESERVED and NOT a registered root ⇒
        # ``build_public_url`` / ``build_url`` both refuse it. The tool
        # composes BOTH lines itself (A-36 / D17). The base is the
        # Host-captured value; setting ``live_views.external_base_url``
        # yields a stable pasted link.
        base = service.public_base()
        public_url = f"{base}{PUBLIC_NAMESPACE}/{BOARD_ROOT_NAME}/{BOARD_REL_PATH}"
        authenticated_url = f"{base}/views/{BOARD_ROOT_NAME}/{BOARD_REL_PATH}"

        locked_yes = "yes" if result.locked_status else "no"
        out = (
            f"Public URL: {public_url}\n"
            f"Passcode: {result.passcode}\n"
            f"Authenticated URL: {authenticated_url}\n"
            f"Generation: {result.generation}\n"
            f"Locked: {locked_yes}"
        )
        if result.locked_status:
            out += (
                "\nHint: passcode is locked — call board_link(refresh=True) "
                "to regenerate."
            )
        return out

    board_link._full_doc_ = """Mint the passcode-gated public link for the LIVE board.

The board at ``/views/public/board/index.html`` is rendered FRESH FROM THE
DATABASE on every request — statuses, tags, due dates and the notes
timeline are always current; it is never a static file Ari rewrites.
This tool mints the ONE passcode row that authorises reading it.

Args:
    refresh: Issue a NEW passcode. The generation is bumped (every
        outstanding grant cookie is revoked), the strike counter is reset
        and a lockout is cleared. This is the ONLY recovery path after 10
        wrong attempts. The default (``False``) is IDEMPOTENT — the same
        passcode and generation come back.

Returns:
    The five-line labeled shape::

        Public URL: /views/public/board/index.html
        Passcode: 7KQ2ZM
        Authenticated URL: /views/board/index.html
        Generation: 1
        Locked: no

    The ``Authenticated URL`` line is INFORMATIONAL: the board's root name
    (``board``) is RESERVED, so the direct lane ``/views/board/index.html``
    404s by design — there is no behind-the-OAuth variant of the board.

    On any rejection a typed ``"Error: ..."`` string is returned — never a
    partial URL, never a crash. The typed envelopes are:
    service-not-wired · subsystem-disabled · passcode-service-missing ·
    passcode-rejected.

Notes:
    * The URL base is the Host-captured value; set
      ``live_views.external_base_url`` in the config for a stable pasted
      link.
    * A concurrent ``board_link(refresh=True)`` revokes outstanding grants
      by design — a reader bounces back to the passcode interstitial.
    * ``board_link`` is ari-only. The category is privileged, the
      allow-list is ari-only, and the tool body re-checks the calling
      agent — an unauthorized caller mints NO row.
"""

    return [board_link]


__all__ = ["create_board_link_tools"]
