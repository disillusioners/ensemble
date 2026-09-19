"""Pre-dispatch image-ref → text conversion hook (Phase 2 / clipboard-image-chat).

The :func:`pre_dispatch_image_hook` is the router-seam adapter that runs
BEFORE the existing vision gate at ``daemon/routers/messages.py:222-231`
and BEFORE the slash-command intercept at :233+`. It converts each ref
in ``MessageCreate.image_refs`` to a text description via
:class:`~daemon.services.tmp_image_converter.TmpImageConverter`, builds
a ``[Image N: <description>]`` (or ``[Image N: description unavailable]``
on failure) prefix, prepends it to the message content, clears the
legacy ``images`` field, and KEEPS ``image_refs`` on the request so the
downstream channel persists refs into the message row + checkpoint
kwargs stamp (display channel).

Two-channel separation (round-2 amendment #26 / C1)
---------------------------------------------------

This hook is the seam that enforces the signature separation:

* ``images`` kwarg — legacy data-URI vision path. The hook CLEARS it
  (``request.images = None``) so ``_build_message_content`` at
  ``daemon/services/instance_messaging.py:113-128`` cannot construct
  multimodal content blocks from refs. The vision gate at
  ``messages.py:222-231`` then naturally skips because
  ``message.images`` is ``None``.

* ``image_refs`` kwarg — display channel. NORMALIZED to the
  canonical URL form (``/api/tmp_images/<32hex>``) at this seam
  via :func:`daemon.models.message.normalize_image_ref_to_canonical_url`.
  Both the bare ``<32hex>`` and the ``tmpimg://<32hex>`` ACCEPTED-INPUT
  aliases become ``/api/tmp_images/<32hex>`` here — round-2 amendment
  #26 / §2 enforcement. RETAINED on the (now-normalized) request so
  the router can pass it to ``manager.enqueue_message_job(
  images=message.images, image_refs=message.image_refs, ...)``. The
  canonical URL refs reach the ``MessageQueue.images`` JSONB column
  (audit) AND the ``HumanMessage.additional_kwargs["image_refs"]``
  checkpoint sidecar (display). The agent channel stays text-only —
  the conversion prepended plain-text descriptions to the content.

Fail-fast (architect amendment #7 — must land BEFORE tests are written)
----------------------------------------------------------------------

When ``request.image_refs`` is non-empty AND
``manager.config.llm.model_vision`` is unset, raise
:class:`fastapi.HTTPException` (400, ``ErrorResponse`` shape matching
the existing vision gate at ``messages.py:222-231``). This is the
router-seam guard that closes the round-1 fail-open position: the user
sees the breakage at use time instead of every image silently landing
in a placeholder.

Disconnect mitigation (architect amendment #11)
----------------------------------------------

Between per-image conversions, if ``http_request.is_disconnected()``
returns ``True``, the remaining refs become placeholders — no further
invokes, INFO log, message still enqueues. Per architect §5.4:
**client disconnect = accept-and-document**. Starlette does NOT cancel
the running handler on disconnect, so the conversion completes
naturally and the message enqueues regardless; an aborted FE shows
failure; a user retry duplicates the message. ``asyncio.shield`` is
meaningless (nothing to shield). Documented in the docstring; no
promise of cancellation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from daemon.models.message import normalize_image_ref_to_canonical_url
from daemon.services.tmp_image_converter import TmpImageConverter, ConvertedImage

if TYPE_CHECKING:
    from fastapi import Request

    from daemon.manager import InstanceManager
    from daemon.models.message import MessageCreate
    from daemon.services.tmp_image_store import TmpImageStore

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Public hook
# ─────────────────────────────────────────────────────────────────────────


async def pre_dispatch_image_hook(
    request: "MessageCreate",
    manager: "InstanceManager",
    tmp_image_store: "TmpImageStore",
    http_request: "Request | None" = None,
) -> "MessageCreate":
    """Convert ``request.image_refs`` to a text prefix on the content.

    Steps:
      1. If ``request.image_refs`` is empty, return ``request``
         unchanged.
      2. Construct a :class:`TmpImageConverter` from the manager and
         store. (Constructed per-call so this hook stays a pure
         function — no shared mutable state.)
      3. Await ``convert_refs(refs)`` — per-ref conversion is
         sequential (architect §5.5; the converter's invoke semaphore
         is the per-POST concurrency guard).
      4. Between conversions (per-amendment #11), if
         ``http_request is not None and
         await http_request.is_disconnected()``, treat the rest as
         failures (placeholders); INFO log the disconnect count.
      5. Build a prefix ``[Image 1: <desc>]\n[Image 2: <desc>]\n...``
         (success) or ``[Image 1: description unavailable]\n...``
         (failure). Preserve any existing ``request.content`` text by
         appending the prefix BEFORE it.
      6. Return a NEW ``MessageCreate`` (via ``model_copy``) with:
           * ``content`` = prefix + original content
           * ``images`` = ``None`` (cleared — legacy vision gate
             naturally skips)
           * ``image_refs`` = NORMALIZED to the canonical URL form
             (``/api/tmp_images/<32hex>``) for every entry — this is
             the single seam where the round-2 amendment #26 / §2
             contract (``tmpimg://<32hex>`` and bare ``<32hex>`` are
             ACCEPTED-INPUT aliases only) is enforced. Both the
             ``MessageQueue.images`` row column (audit, written via
             ``enqueue_message_job(images=..., image_refs=...)`` in
             the router seam) and the
             ``HumanMessage.additional_kwargs["image_refs"]``
             checkpoint sidecar inherit the canonical form because
             the router rebinds ``message = await
             pre_dispatch_image_hook(...)`` and threads the
             post-hook ``message.image_refs`` through every leg
             (202 injection FIFO, durable enqueue, PAUSED
             auto-resume, fallback enqueue). See
             ``daemon/models/message.py:33`` for the helper.

    Returns the request unchanged when ``image_refs`` is empty so the
    no-ref path is zero-cost byte-identical to pre-phase-2.
    """
    refs = request.image_refs
    if not refs:
        return request

    converter = TmpImageConverter(manager=manager, tmp_image_store=tmp_image_store)

    # Per-ref conversion with disconnect mitigation between iterations
    # (architect amendment #11). We don't pre-build the entire result
    # list because the disconnect check is BETWEEN conversions, not
    # before all of them — if the client disconnects mid-burst we want
    # to release the invoke lane early and skip the remaining invokes.
    converted: list[ConvertedImage] = []
    remaining_disconnected = False
    for idx, ref in enumerate(refs):
        # Disconnect check BETWEEN conversions (the amendment #11
        # mitigation). We DON'T check before the first conversion
        # because that would always trigger on clients that haven't
        # finished sending the request body yet.
        if idx > 0 and http_request is not None:
            try:
                if await http_request.is_disconnected():
                    if not remaining_disconnected:
                        logger.info(
                            "[TmpImageConversion] client disconnected "
                            "mid-conversion; %d remaining images → "
                            "placeholder, message will still enqueue",
                            len(refs) - idx,
                        )
                        remaining_disconnected = True
                    converted.append(ConvertedImage(ref=ref, description="", ok=False))
                    continue
            except Exception as exc:  # defensive
                # ``is_disconnected`` can raise on a malformed request;
                # we don't want to fail the POST just because the
                # disconnect check is broken — log and continue.
                logger.debug(
                    "[TmpImageConversion] is_disconnected() raised %s; "
                    "treating as not-disconnected",
                    exc,
                )

        # Convert this ref. The converter swallows ALL exceptions
        # (including the legacy ``asyncio.TimeoutError`` shape) and
        # collapses error STRINGS via the ``startswith("Error")`` guard
        # (architect amendment #9).
        result = await converter._convert_one(
            ref,
            question="Describe this image in detail.",
        )
        converted.append(result)

    prefix = _build_image_prefix(converted)

    # Build the new content. Preserve any existing user text by
    # appending the prefix BEFORE it (the agent reads the description
    # first, then the user's text — natural reading order).
    original_content = request.content or ""
    if prefix:
        new_content = f"{prefix}{original_content}"
    else:
        new_content = original_content

    # Build a NEW MessageCreate via model_copy so the wire model stays
    # frozen-shape (the existing content field is a string; no
    # list-typed content here — the converter's output is always text).
    # ``images`` is CLEARED so the legacy vision gate at
    # ``messages.py:222-231`` naturally skips.
    #
    # ``image_refs`` is NORMALIZED to the canonical URL form
    # (``/api/tmp_images/<32hex>``) — round-2 amendment #26 / §2
    # contract. The :func:`normalize_image_ref_to_canonical_url`
    # helper (imported above) is idempotent: a canonical URL passes
    # through unchanged, a ``tmpimg://<32hex>`` alias gets the
    # ``/api/tmp_images/`` prefix, and a bare ``<32hex>`` id is also
    # given the canonical prefix. This single seam covers every leg:
    # the router rebinds ``message = await pre_dispatch_image_hook(...)``
    # at ``daemon/routers/messages.py:269`` and threads the
    # post-hook ``message.image_refs`` through (a) the durable row
    # column via ``enqueue_message_job(image_refs=...)``, (b) the
    # checkpoint kwargs stamp via
    # ``HumanMessage.additional_kwargs["image_refs"]``, (c) the 202
    # injection FIFO entry, and (d) the POST-time echo
    # ``additional_kwargs``. All four channels therefore receive
    # canonical form — bare-hex and ``tmpimg://`` aliases NEVER
    # persist or emit. Without this normalization, the alias forms
    # propagate through every consumer and violate the §2 contract
    # end-to-end (round-2 review MAJOR #1 finding).
    return request.model_copy(
        update={
            "content": new_content,
            "images": None,
            "image_refs": [
                normalize_image_ref_to_canonical_url(r)
                for r in (request.image_refs or [])
            ],
        },
    )


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _build_image_prefix(converted: list[ConvertedImage]) -> str:
    """Build the ``[Image N: <desc>]\\n...`` prefix from conversion results.

    Success: ``[Image 1: a yellow flower on a green stem]``.
    Failure: ``[Image 1: description unavailable]`` (placeholder per
    architect §5.6 — never leaks the raw ``Error: ...`` STRING the
    converter collapsed).
    """
    if not converted:
        return ""
    lines: list[str] = []
    for idx, c in enumerate(converted, start=1):
        if c.ok and c.description:
            # Strip leading/trailing whitespace from the description so
            # the prefix reads cleanly when joined with the user's
            # content. The image-reader agent may emit trailing
            # newlines or stray whitespace.
            desc = c.description.strip()
            lines.append(f"[Image {idx}: {desc}]")
        else:
            lines.append(f"[Image {idx}: description unavailable]")
    return "\n".join(lines) + "\n"


__all__ = ["pre_dispatch_image_hook"]
