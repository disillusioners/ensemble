"""Image-to-text conversion for clipboard refs (Phase 2 / clipboard-image-chat).

The :class:`TmpImageConverter` is the seam that bridges the ref-based
display channel on ``POST /instances/{id}/messages`` and the daemon-global
vision routing. It runs in the router thread (synchronous-in-POST,
architect §4 ratification, no env knob per amendment #12) and converts
each ``image_ref`` to a plain-text description by:

  1. Reading the bytes + content_type from the phase-1
     :class:`~daemon.services.tmp_image_store.TmpImageStore`
     (``TmpImageStore.open(image_id)`` returns ``(bytes, mime)``).
  2. Building a ``data:<mime>;base64,<b64>`` data URI.
  3. Invoking the existing ``image-reader`` agent via
     :func:`daemon.utils.invoke_agent_and_wait` with that data URI
     (mirrors :func:`daemon.tools.image_tools.explain_image`).

The data URI is forwarded to image-reader via ``invoke_agent_and_wait``;
the vision-capable model that image-reader is configured with produces
the description. The HTTP wire format is unchanged: the chat agent
receives plain text (a prepended ``[Image N: <description>]`` block per
ref), NOT a multimodal content-block list (the ref-send path never
reaches the legacy vision gate — see
``daemon/routers/messages.py`` Task 4 wiring).

Concurrency
-----------

Per-image conversions run sequentially within a single POST (a
3-image POST acquires the invoke semaphore three times). The invoke
semaphore is the ``max(1, WORKER_POOL_SIZE - 1)`` cap from
:func:`daemon.utils._get_invoke_semaphore` (``daemon/utils.py:566``);
with the default ``WORKER_POOL_SIZE = 5`` the cap is **4** — the
semaphore IS the per-POST concurrency guard (architect §5.5 —
no additional POST-level guard needed). A 3-image POST therefore
saturates at most 3 of the 4 invoke slots; a 4+ image POST is
impossible because the validator caps to 3 refs.

Failure semantics (round-2 amendment #9 — must-fix bug class)
------------------------------------------------------------

``invoke_agent_and_wait`` NEVER raises on timeout / agent error. It
returns a STRING:

* ``"Error: Agent timed out after 90s. Instance <id>... may still be
  running."`` (utils.py:695-697 — timeout STRING)
* ``"Error: Agent failed. <reason>"`` (utils.py:701 — agent errored
  STRING)

These error STRINGS would leak into the agent-facing prefix as a
``[Image 1: Error: Agent timed out after 90s...]`` description, which
is exactly the failure mode amendment #9 closes. The wrapper MUST
collapse via ``ok = result is not None and bool(result) and not
str(result).startswith("Error")`` (mirror ``image_tools.py:530-541``
shape; the actual ``startswith("Error")`` guard is NEW with no
precedent per O2). ``None`` and empty-string are also collapsed so
the fallback placeholder is uniform.

Vanished-file case
------------------

A ref can outlive its backing file: the phase-3 retention sweep
deletes a file whose age exceeds the retention window, OR a
DELETE-by-id request races the POST. The store read runs INSIDE the
per-image ``try`` (amendment #9) and a raised
:class:`~daemon.services.tmp_image_store.TmpImageNotFound`
(``TmpImageStoreError`` subclass — sync, per phase-1 deviation) is
caught and returns ``(ref, "", ok=False)``. ``OSError`` is also
caught defensively (covers ``FileNotFoundError`` if the API ever
loosens) so a vanished file is graceful-by-construction.

Disconnection mitigation
------------------------

The hook (``tmp_image_message_hook.pre_dispatch_image_hook``) passes
the request's ``http_request: Request | None`` here. Between per-image
conversions, the hook checks ``http_request.is_disconnected()`` and
turns any remaining refs into placeholders if the client went away.
This converter does NOT do the disconnect check itself — keeping the
seam narrow keeps the unit test surface tractable.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import TYPE_CHECKING, Any, NamedTuple

from daemon.services.tmp_image_store import (
    TmpImageNotFound,
    TmpImageStore,
)
from daemon.utils import invoke_agent_and_wait

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────────────────────────────────


# Per-image conversion timeout (seconds). Architect amendment #8 — this
# is a CONVERTER-OWNED constant, NOT the ``image_tools.py:521`` 300s
# literal. The tool-context budget (300s, agent turn) and the
# HTTP-context budget (90s, request handler) are independent budgets
# with independent ceilings. 90s ≈ 5× a typical 5–20s conversion; with
# ≤3 images the worst-case POST wait is 270s + overhead, comfortably
# under typical browser / LB request timeouts.
TMP_IMAGE_CONVERSION_TIMEOUT_S: float = 90.0

# C3 (round-2) regex fragment — re-uses the same accepted input forms
# the ``MessageCreate.image_refs`` field validator accepts. Re-declared
# here as a safety belt: the converter is called AFTER the request
# model validator, but the hook defensively checks any ref it didn't
# see through the model layer.
_IMAGE_REF_PATTERN = re.compile(r"^((tmpimg://)|(/api/tmp_images/))?[a-f0-9]{32}$")


# Default question sent to the image-reader agent. Matches the default
# question the legacy ``explain_image`` tool uses, so the converter
# produces descriptions that read identically to those an agent would
# get if it called ``explain_image(image=<url>)`` on the raw bytes.
DEFAULT_CONVERSION_QUESTION = "Describe this image in detail."


# ─────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────


class ConvertedImage(NamedTuple):
    """Outcome of one ref's image→text conversion.

    Attributes:
        ref: The ref string passed in (verbatim — NOT normalized).
        description: The image-reader's description on success; empty
            string on failure. Use ``ok`` to distinguish.
        ok: ``True`` when the conversion produced a non-empty,
            non-error description; ``False`` on timeout, agent error,
            vanished file, or any read failure.
    """

    ref: str
    description: str
    ok: bool


# ─────────────────────────────────────────────────────────────────────────
# Converter
# ─────────────────────────────────────────────────────────────────────────


class TmpImageConverter:
    """Image-to-text conversion for clipboard refs.

    Mirrors the established ``explain_image`` tool pattern: per-image
    ``invoke_agent_and_wait(agent_id="image-reader", images=[data_uri],
    timeout=TMP_IMAGE_CONVERSION_TIMEOUT_S)``. The data URI is the
    canonical form image-reader's vision pipeline expects.

    Construction
    ------------

    Take the manager (for the spawn / enqueue / wait plumbing) and the
    store (for the in-process byte read). Both are wired in the router
    via ``request.app.state`` — the lifespan publishes them at boot
    (see ``daemon/api.py:265-269``).

    Concurrency
    -----------

    Sequential per-ref within one ``convert_refs`` call. Cross-POST
    concurrency is bounded by ``invoke_agent_and_wait``'s semaphore
    (cap = ``max(1, WORKER_POOL_SIZE - 1) = 4``). With ≤3 refs per
    POST (validator cap), a single POST holds at most 3 of the 4
    slots — graceful by construction.
    """

    def __init__(
        self,
        manager: "InstanceManager",
        tmp_image_store: TmpImageStore,
    ) -> None:
        self._manager = manager
        self._store = tmp_image_store

    async def convert_refs(
        self,
        refs: list[str],
        question: str = DEFAULT_CONVERSION_QUESTION,
    ) -> list[ConvertedImage]:
        """Convert each ref to a text description.

        Each ref is processed sequentially: read bytes → build data
        URI → ``invoke_agent_and_wait(agent_id="image-reader",
        images=[data_uri], timeout=TMP_IMAGE_CONVERSION_TIMEOUT_S)``.

        Returns a list of :class:`ConvertedImage` aligned 1:1 with the
        input ``refs`` order. Failures (vanished file, timeout STRING,
        agent-error STRING, any exception) produce
        ``ConvertedImage(ref, "", ok=False)``; the caller (the hook)
        substitutes a ``[Image N: description unavailable]`` placeholder
        for the agent-facing prefix and NEVER sees the raw error string.

        Defensive notes:
          * Empty input → returns ``[]`` (zero-allocation).
          * Any ref that doesn't match the validated regex is logged
            as WARNING and treated as a failure (the validator at
            :class:`daemon.models.message.MessageCreate` already rejects
            malformed refs at the request layer; this is a safety belt
            for tests that call the converter directly).
        """
        if not refs:
            return []

        results: list[ConvertedImage] = []
        for ref in refs:
            results.append(await self._convert_one(ref, question=question))
        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _convert_one(
        self,
        ref: str,
        question: str,
    ) -> ConvertedImage:
        """Convert a single ref. Returns a :class:`ConvertedImage`.

        The read + invoke + collapse runs INSIDE one ``try``/``except``
        block per amendment #9 — any exception (read error, agent
        error, vanished file, OSErrors, etc.) collapses to
        ``(ref, "", ok=False)``. The hook substitutes the fallback
        placeholder.
        """
        try:
            image_id = self._extract_image_id(ref)
            if image_id is None:
                logger.warning(
                    "[TmpImageConversion] ref %r rejected by validator; "
                    "treating as failure",
                    ref,
                )
                return ConvertedImage(ref=ref, description="", ok=False)

            # Read INSIDE the per-image try — amendment #9 (vanished-
            # file case). TmpImageStore.open() is SYNC (phase-1
            # deviation: the plan assumed ``await open()`` / async +
            # FileNotFoundError; the live API is sync and raises
            # TmpImageNotFound). Defensive OSError catch covers
            # concurrent-sweep races where the file vanishes between
            # scandir and open. ``read_bytes`` is fast (≤10MB per
            # validator cap) — running sync on the event loop is OK.
            try:
                content_bytes, content_type = self._store.open(image_id)
            except TmpImageNotFound as exc:
                logger.warning(
                    "[TmpImageConversion] ref %r file not found: %s; "
                    "using placeholder",
                    ref,
                    exc,
                )
                return ConvertedImage(ref=ref, description="", ok=False)
            except OSError as exc:
                logger.warning(
                    "[TmpImageConversion] ref %r read failed: %s; "
                    "using placeholder",
                    ref,
                    exc,
                )
                return ConvertedImage(ref=ref, description="", ok=False)

            data_uri = _build_data_uri(content_bytes, content_type)

            # Invoke the image-reader agent. Mirrors the proven
            # ``explain_image`` template at image_tools.py:498-532.
            # ``invoke_agent_and_wait`` NEVER raises on timeout /
            # agent error — it returns "Error: ..." STRINGs
            # (utils.py:695, :701). The collapse guard below
            # (amendment #9) handles BOTH paths.
            result = await invoke_agent_and_wait(
                manager=self._manager,
                agent_id="image-reader",
                message=question,
                images=[data_uri],
                # NOTE: parent_id + project_id intentionally NOT
                # threaded — image-reader is a system-level tool with
                # its own project context, and threading the caller's
                # project risks leaking project metadata into the
                # sub-invocation. This matches the proven explain_image
                # call shape (image_tools.py:513-523 omits them too).
                timeout=TMP_IMAGE_CONVERSION_TIMEOUT_S,
            )

            # Collapse STRING error paths + None + empty — amendment #9.
            # ``startswith("Error")`` is NEW with no precedent per O2;
            # mirror ``image_tools.py:530-541`` shape but extend with
            # the prefix guard so ``"Error: ..."`` descriptions never
            # leak into the agent-facing prefix.
            ok = (
                result is not None
                and bool(result)
                and not str(result).startswith("Error")
            )
            if not ok:
                logger.warning(
                    "[TmpImageConversion] ref %r conversion returned "
                    "no description (timeout/error/empty); using placeholder",
                    ref,
                )
                return ConvertedImage(ref=ref, description="", ok=False)
            return ConvertedImage(ref=ref, description=str(result), ok=True)

        except Exception as exc:  # pragma: no cover — last-resort guard
            # Per-image one-shot: any unexpected exception (e.g. a
            # future store API change) collapses to a placeholder, NEVER
            # kills the whole POST. Logged with traceback for forensics.
            logger.warning(
                "[TmpImageConversion] unexpected exception for ref %r: "
                "%s: %s; using placeholder",
                ref,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return ConvertedImage(ref=ref, description="", ok=False)

    @staticmethod
    def _extract_image_id(ref: str) -> str | None:
        """Strip the optional ``tmpimg://`` or ``/api/tmp_images/``
        prefix and return the bare 32-hex id.

        Returns ``None`` if the stripped value is not a 32-hex string
        (defensive — the model validator already ran upstream).
        """
        candidate = ref
        if candidate.startswith("tmpimg://"):
            candidate = candidate[len("tmpimg://"):]
        elif candidate.startswith("/api/tmp_images/"):
            candidate = candidate[len("/api/tmp_images/"):]
        if not re.fullmatch(r"[a-f0-9]{32}", candidate):
            return None
        return candidate


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _build_data_uri(content_bytes: bytes, content_type: str) -> str:
    """Build a ``data:<mime>;base64,<b64>`` data URI.

    Matches the legacy ``explain_image`` tool's wire shape
    (image_tools.py) so image-reader's vision pipeline accepts the
    payload unchanged. ``content_type`` is whatever the store
    persisted (validated at upload — the allowlist is 4 types:
    png/jpeg/gif/webp).
    """
    encoded = base64.b64encode(content_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


__all__ = [
    "TMP_IMAGE_CONVERSION_TIMEOUT_S",
    "DEFAULT_CONVERSION_QUESTION",
    "ConvertedImage",
    "TmpImageConverter",
]
