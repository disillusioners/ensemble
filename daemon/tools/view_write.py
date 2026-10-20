"""``view_write`` tool — publish a rich HTML page as a passcode-gated public link.

ari-tools Phase 3, deliverable A. The tool writes ONE HTML document under
the daemon-global ``reports`` live-view root and returns the
passcode-gated PUBLIC URL + the 6-char passcode **in a single call** —
the agent never has to assemble a URL by hand and never has to make a
second call to mint the passcode.

Storage decision (phase3-plan §5)
---------------------------------
Plain ``.html`` files under ``<data_dir>/reports/<rel>.html``. The served
artefact IS an HTML file, so the existing ``filesystem`` root resolver
already serves it with the right MIME, the extension allowlist, the
containment check and the 32 MiB cap — ZERO new serving code. (The
blob+sidecar substrate solves a different problem: opaque binaries whose
type cannot be inferred from the name, with a 30-day sweep that would
make a "published" link vanish.)

Access posture — THREE layers, ALL required
-------------------------------------------
1. The ``view-write`` category is PRIVILEGED (entry #11 of
   ``PRIVILEGED_TOOL_CATEGORIES``) ⇒ it never joins the empty-allow
   default universe.
2. ``agents/ari/meta.json`` carries the explicit ``"view-write"`` allow
   entry — and ONLY ari (the binding ARI-ONLY pre-ruling).
3. The tool body calls the shared
   :func:`daemon.tools.views_auth._is_views_authorized` gate — the FIRST
   statement in the body, BEFORE path validation, BEFORE the write and
   BEFORE the passcode mint.

Layer 3 is load-bearing, not decorative: a **literal tool-name** allow
entry bypasses the category gate outright (``daemon/tools/instance.py``
— an allow entry that is not a category key is added verbatim), so
layers 1 + 2 alone do NOT enforce "ari-only". The gate is fail-closed:
a blank / non-``str`` ``agent_id`` is NEVER authorized.

House rules honored here
-------------------------
* Tools NEVER raise — every failure returns a typed ``"Error: ..."``
  string. The envelope convention is ``Error:`` (the ``view_link``
  family convention), NOT phase 1's ``ERROR:``.
* The tool NEVER mutates the caller's bytes: the served page is
  byte-for-byte what the agent wrote.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import TYPE_CHECKING, Annotated

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category
from .views_auth import VIEW_WRITE_AGENTS, _is_views_authorized

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


CATEGORY_NAME = "Live Views (Write)"
CATEGORY_DOC = """\
Publish a rich HTML page as a passcode-gated public link — in ONE call.

Tool in this category:
- ``view_write`` — write an HTML page under the daemon-global ``reports``
  root and return its public URL + 6-char passcode

``view_write(path, html, title)`` writes ONE HTML document and returns
the PUBLIC URL (``/views/public/reports/<path>``), the PASSCODE, the
authenticated URL, the generation, the locked status, the title, the
byte count and whether an existing page was overwritten. The customer
opens the public URL → zero-JS interstitial → enters the passcode →
sees the page.

Rules that matter:
* ``path`` is RELATIVE to the ``reports`` root; ``.html`` is appended
  when the path carries no extension. Only ``.html`` pages are
  accepted.
* A page larger than the 32 MiB served-page cap is REFUSED before
  anything is written — a page that cannot be served is never written.
* An existing path is REFUSED unless ``overwrite=True``. An overwrite
  does NOT change the passcode or the URL (the link stays stable);
  ``refresh=True`` is the separate, explicit generation-bump path.
* The page is written ATOMICALLY and durably (same-directory temp
  file + fsync + rename) — a reader never sees a torn page.
* The tool NEVER mutates the HTML you pass it; the served bytes are
  exactly your bytes.

Access: ``view-write`` is a PRIVILEGED category (ari only) AND the tool
body re-checks the calling agent. Every failure returns a typed
``"Error: ..."`` string — never a partial URL, never a crash.
"""

#: The daemon-global live-view root this tool writes into. The root is
#: INJECTED by the lifespan (``daemon/api.py``) — an operator-declared
#: ``reports`` entry is honoured and never overridden.
REPORTS_ROOT_NAME = "reports"

#: The extension gate — the ``reports`` root is served with
#: ``allowed_extensions=["html"]``, so only HTML may be written.
REQUIRED_EXTENSION = "html"

#: The title is ECHOED, never persisted and never injected into the
#: HTML (D9). 1..200 chars.
MAX_TITLE_LENGTH = 200


def _error(msg: str) -> str:
    """Phase-3 house error envelope — tools never raise.

    The convention is ``Error:`` (the ``view_link`` family convention
    at ``daemon/tools/live_views.py``), NOT phase 1's ``ERROR:``.
    """
    return f"Error: {msg}"


def _resolve_passcode_service(manager: "InstanceManager"):
    """Return the per-app passcode service, or None.

    Mirrors the lookup ``view_link`` performs (``daemon/tools/
    live_views.py``): the manager facade is the canonical seam the
    tools reach shared per-app state through; both the private
    (``_view_passcode_service``) and the public
    (``view_passcode_service``) shapes collapse to the same typed
    envelope on absence.
    """
    service = getattr(manager, "_view_passcode_service", None)
    if service is None:
        service = getattr(manager, "view_passcode_service", None)
    return service


def create_view_write_tools(
    manager: "InstanceManager",
    *,
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    """Create the ``view_write`` tool for one instance/agent.

    Args:
        manager: The ``InstanceManager`` instance. Used for its
            ``live_views_service`` and passcode-service properties.
        current_instance_id: The caller's instance id. Stamped onto the
            passcode mint as the audit actor.
        agent_id: The CALLING agent's id — the layer-3 in-body gate's
            input. Defaults to ``""`` ⇒ FAIL-CLOSED: a construction
            site that forgets to thread it yields a dead tool, never an
            open one.

    Returns:
        A list of one tool: ``[view_write]``.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""

    class ViewWriteInput(BaseModel):
        """Input schema for view_write."""

        path: Annotated[
            str,
            Field(
                description=(
                    "Relative path under the reports root, e.g. "
                    "'2026-10-19/board.html'. '.html' is appended when the "
                    "path carries no extension. No leading '/', no '..' "
                    "segments, no backslashes, no control characters."
                )
            ),
        ]
        html: Annotated[
            str,
            Field(
                description=(
                    "The full HTML document (UTF-8). Served byte-for-byte; "
                    "never mutated, never wrapped."
                )
            ),
        ]
        title: Annotated[
            str,
            Field(
                description=(
                    "Human label (1-200 chars). Echoed in the result; NOT "
                    "persisted and NOT injected into the HTML."
                )
            ),
        ]
        overwrite: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Replace an existing page. Without it, an existing path "
                    "is an error. An overwrite does NOT change the passcode "
                    "or the URL."
                ),
            ),
        ]
        refresh: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Bump the passcode generation (new code, revokes "
                    "outstanding grants). This is the lockout-recovery path."
                ),
            ),
        ]

    @register_tool_category("view-write")
    @tool(args_schema=ViewWriteInput)
    async def view_write(
        path: Annotated[
            str,
            Field(
                description=(
                    "Relative path under the reports root, e.g. "
                    "'2026-10-19/board.html'. '.html' is appended when the "
                    "path carries no extension."
                )
            ),
        ],
        html: Annotated[
            str,
            Field(
                description=(
                    "The full HTML document (UTF-8). Served byte-for-byte; "
                    "never mutated."
                )
            ),
        ],
        title: Annotated[
            str,
            Field(
                description=(
                    "Human label (1-200 chars). Echoed in the result; NOT "
                    "persisted and NOT injected into the HTML."
                )
            ),
        ],
        overwrite: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Replace an existing page; without it an existing path "
                    "is an error."
                ),
            ),
        ] = False,
        refresh: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Bump the passcode generation (new code, revokes grants)."
                ),
            ),
        ] = False,
    ) -> str:
        """Publish an HTML page as a passcode-gated public link.

        Writes ONE HTML document under the daemon-global ``reports``
        root and returns the PUBLIC URL + the 6-char passcode in a
        single call. The customer opens the public URL, enters the
        passcode, and sees the page.

        Args:
            path: Relative path under the ``reports`` root. ``.html`` is
                appended when the path carries no extension; any other
                extension is refused.
            html: The full HTML document. Served byte-for-byte — the
                tool never wraps, mutates or sanitises it.
            title: Human label (1-200 chars). Echoed only.
            overwrite: Replace an existing page. Without it, an
                existing path is an error and the original bytes stay.
            refresh: Bump the passcode generation — new code, outstanding
                grants revoked. The lockout-recovery path.

        Returns:
            A stable labeled-line string: ``Public URL`` / ``Passcode``
            / ``Authenticated URL`` / ``Generation`` / ``Locked`` (the
            five ``view_link``-compatible lines) followed by ``Title`` /
            ``Bytes`` / ``Overwrote``. On any rejection, a typed
            ``"Error: ..."`` string — never a partial URL, never a
            crash.
        """
        # ── LAYER 3: the in-body privilege gate ──────────────────
        # FIRST statement in the body — BEFORE path validation,
        # BEFORE the write, BEFORE the passcode mint. An
        # unauthorized caller gets NO file written and NO passcode
        # row minted (zero side effects).
        if not _is_views_authorized(caller_agent_id, VIEW_WRITE_AGENTS):
            return _error(
                "view_write is restricted to the 'ari' agent — "
                f"this call came from agent {caller_agent_id!r}."
            )

        from daemon.services.live_views import (
            MAX_SERVED_BYTES,
            PUBLIC_NAMESPACE,
            extension_for,
            is_well_formed_rel_path,
        )

        # ── 1. service wiring ────────────────────────────────────
        service = getattr(manager, "live_views_service", None)
        if service is None:
            return _error(
                "live-views service not initialized "
                "(lifespan did not wire it; check daemon logs)."
            )
        if not service.enabled():
            return _error("live-views subsystem is disabled")
        reports_root = service.root_path(REPORTS_ROOT_NAME)
        if reports_root is None:
            return _error(
                "reports storage is not configured (no daemon data_dir, "
                "the live-views subsystem is off, or the reports root is "
                "missing/relative)"
            )

        # ── 2. argument shape ────────────────────────────────────
        if not isinstance(path, str) or not path.strip():
            return _error("path must be a non-empty string")
        if not isinstance(html, str) or not html.strip():
            return _error("html must be a non-empty HTML document")
        if not isinstance(title, str) or not title.strip():
            return _error("title must be a non-empty string")
        if len(title) > MAX_TITLE_LENGTH:
            return _error(
                f"title is {len(title)} chars — the limit is "
                f"{MAX_TITLE_LENGTH}"
            )

        # ── 3. path normalisation ─────────────────────────────────
        # ONE validator for the write path AND the serve path
        # (``is_well_formed_rel_path``): no leading '/', no '..'
        # segments, no backslashes, no control chars (C0 + DEL + C1),
        # no null bytes.
        rel = path.strip()
        if not is_well_formed_rel_path(rel):
            return _error(
                f"malformed path {rel!r} (relative only — no leading '/', "
                f"no '..' segments, no backslashes, no control chars)"
            )
        ext = extension_for(rel)
        if ext == "":
            rel = f"{rel}.{REQUIRED_EXTENSION}"
        elif ext != REQUIRED_EXTENSION:
            return _error(
                f"only .{REQUIRED_EXTENSION} pages can be written to the "
                f"reports root (got {rel!r})"
            )

        # ── 4. containment (resolved vs resolved) ─────────────────
        # Mirrors ``_resolve_under_root``: BOTH sides are resolved, so
        # a symlink inside the reports tree cannot point outside it.
        try:
            root_resolved = reports_root.resolve()
            target = (root_resolved / rel).resolve()
        except OSError:
            return _error(
                "could not resolve the reports storage path "
                "(filesystem error)"
            )
        try:
            target.relative_to(root_resolved)
        except ValueError:
            return _error(
                f"path {rel!r} resolves outside the reports root — refused"
            )

        # ── 5. size cap (write time) ─────────────────────────────
        # A page that cannot be served must NEVER be written.
        payload = html.encode("utf-8")
        size = len(payload)
        if size > MAX_SERVED_BYTES:
            return _error(
                f"page is {size} bytes — the served-page cap is 32 MiB "
                f"({MAX_SERVED_BYTES} bytes); nothing was written"
            )

        # ── 6. existing page ─────────────────────────────────────
        existed = target.exists()
        if existed and not overwrite:
            return _error(
                f"{rel} already exists — pass overwrite=True to replace it"
            )

        # ── 7. atomic + durable write ────────────────────────────
        # Same-directory temp file (a /tmp temp ⇒ EXDEV on replace),
        # flush + fsync BEFORE the rename (os.replace is atomic w.r.t.
        # the NAMESPACE, not durability), temp unlinked in a finally.
        try:
            parent = target.parent
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(parent), prefix=".view_write-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, target)
            finally:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning(
                        "[view_write] could not unlink temp file %s",
                        tmp_name,
                    )
        except OSError as exc:
            logger.warning("[view_write] write failed: %s", exc)
            return _error(
                f"could not write the page to the reports root "
                f"(filesystem error)"
            )

        # ── 8. passcode mint / refresh ───────────────────────────
        # The passcode service is SYNCHRONOUS (DB-backed) ⇒ in this
        # async body the call MUST be wrapped in ``to_thread`` —
        # exactly ONE crossing around the single sync call, never one
        # per field.
        passcode_service = _resolve_passcode_service(manager)
        if passcode_service is None:
            return _error(
                "view-passcode service not initialized "
                "(missing SYSTEM_ENCRYPTION_KEY or lifespan did not wire "
                "it; check daemon logs). The page was written but is NOT "
                "published."
            )
        actor = caller_instance_id or None
        from daemon.services.view_passcode_service import (
            ViewPasscodeNotFoundError,
            ViewPasscodeRotationRequired,
        )

        try:
            if refresh:
                try:
                    result = await asyncio.to_thread(
                        passcode_service.refresh, REPORTS_ROOT_NAME, rel, actor
                    )
                except ViewPasscodeNotFoundError:
                    # Lazy-mint posture: ``refresh`` on a path that was
                    # never minted has no row to bump. Mint it (the
                    # fresh link IS the recovery), so a first call with
                    # refresh=True works in ONE call.
                    result = await asyncio.to_thread(
                        passcode_service.mint, REPORTS_ROOT_NAME, rel, actor
                    )
            else:
                result = await asyncio.to_thread(
                    passcode_service.mint, REPORTS_ROOT_NAME, rel, actor
                )
        except Exception as exc:
            if isinstance(exc, ViewPasscodeRotationRequired):
                return _error(
                    "stored passcode ciphertext cannot be decrypted — the "
                    "SYSTEM_ENCRYPTION_KEY may have been rotated without "
                    "re-encrypting the live_view_passcodes table. The owner "
                    "should regenerate the passcode."
                )
            logger.warning("[view_write] passcode mint failed: %s", exc)
            return _error(
                f"passcode service rejected the call: "
                f"{type(exc).__name__}"
            )

        # ── 9. URLs ──────────────────────────────────────────────
        public_url = service.build_public_url(REPORTS_ROOT_NAME, rel)
        if public_url is None:
            public_url = (
                f"{service.public_base()}{PUBLIC_NAMESPACE}/"
                f"{REPORTS_ROOT_NAME}/{rel}"
            )
        authenticated_url = service.build_url(REPORTS_ROOT_NAME, rel)
        if authenticated_url is None:
            authenticated_url = f"/views/{REPORTS_ROOT_NAME}/{rel}"

        # ── the return shape ─────────────────────────────────────
        # The first five lines are byte-compatible with ``view_link``'s
        # shape (same consumer contract, grep-able); the last three are
        # additions.
        locked_yes = "yes" if result.locked_status else "no"
        out = (
            f"Public URL: {public_url}\n"
            f"Passcode: {result.passcode}\n"
            f"Authenticated URL: {authenticated_url}\n"
            f"Generation: {result.generation}\n"
            f"Locked: {locked_yes}\n"
            f"Title: {title}\n"
            f"Bytes: {size}\n"
            f"Overwrote: {'yes' if existed else 'no'}"
        )
        if result.locked_status:
            out += (
                "\nHint: passcode is locked — call "
                "view_write(path=..., html=..., title=..., refresh=True) "
                "to regenerate."
            )
        if "<title" not in html:
            # Advisory only — the tool NEVER mutates the caller's
            # bytes (D9); the note just tells the agent the page has
            # no document title.
            out += (
                "\nNote: the document has no <title> element — the browser "
                "tab will show the URL. The bytes were written verbatim."
            )
        return out

    view_write._full_doc_ = """Publish an HTML page as a passcode-gated public link.

Writes ONE HTML document under the daemon-global ``reports`` live-view
root and returns the public URL + 6-char passcode in a SINGLE call. The
customer opens the public URL → zero-JS interstitial → enters the
passcode → sees the page. The served bytes are byte-for-byte the bytes
you passed in (the tool never wraps, mutates or sanitises them).

Args:
    path: Relative path under the ``reports`` root, e.g.
        ``2026-10-19/board.html``. ``.html`` is appended when the path
        carries no extension; any other extension is refused. The path
        must be RELATIVE — no leading ``/``, no ``..`` segments, no
        backslashes, no control characters.
    html: The full HTML document (UTF-8). Served byte-for-byte.
    title: Human label (1-200 chars). Echoed in the result — NOT
        persisted and NOT injected into the HTML.
    overwrite: Replace an existing page. Without it an existing path is
        an error and the original bytes stay intact. An overwrite does
        NOT bump the passcode generation: the URL stays stable.
    refresh: Bump the passcode generation — a NEW passcode, and every
        outstanding grant cookie is revoked. This is the lockout-
        recovery path (10 wrong attempts lock the row).

Returns:
    A stable labeled-line string::

        Public URL: /views/public/reports/2026-10-19/board.html
        Passcode: 7KQ2ZM
        Authenticated URL: /views/reports/2026-10-19/board.html
        Generation: 1
        Locked: no
        Title: Q3 status board
        Bytes: 18422
        Overwrote: no

    The first five lines are byte-compatible with ``view_link``'s shape.
    A ``Hint:`` line is appended when the passcode is locked; a
    ``Note:`` line when the document carries no ``<title>``.

    Every rejection returns a typed ``"Error: ..."`` string — never a
    partial URL, never a crash. The typed envelopes are:
    service-not-wired · subsystem-disabled · reports-not-configured ·
    bad-path · bad-extension · too-large · exists-without-overwrite ·
    write-IO-failure · passcode-service-missing · passcode-rejected.

Notes:
    * A page larger than the 32 MiB served-page cap is REFUSED before
      anything is written — a page that cannot be served is never
      written.
    * The write is ATOMIC and durable: a same-directory temp file,
      fsync, then rename. A reader never sees a torn page.
    * ``view_write`` is ARI-ONLY. The category is privileged, the
      allow-list is ari-only, and the tool body re-checks the calling
      agent — an unauthorized caller writes NOTHING and mints NO
      passcode row.
"""

    return [view_write]


__all__ = ["create_view_write_tools"]
