"""Static / dataclass-fields tests: ``IncomingMessage`` has NO
``image_refs`` field (Phase 2 / freeze-list A8).

The chat-source safe-by-construction invariant: sources MUST NOT
mint ``image_refs``. ``IncomingMessage`` is the type sources
populate — its field set defines what adapters can carry. Adding
``image_refs`` here would re-open the live-injection gate bypass
(``registry.py:94-95`` only checks ``msg.images``, so a future
contributor adding ``msg.image_refs`` would let sources mint refs
that ride the live-injection lane — the agent channel would
surface refs in its HumanMessage, and the existing chat-source
invariant breaks).

The static ``dataclasses.fields`` assertion pins this — when a
future PR adds the field, this test fails loudly with a clear
"DO NOT ADD image_refs to IncomingMessage" message.

Also pinned: the guard comment at ``daemon/sources/base.py:25``
(the IncomingMessage docstring is refreshed to call out the
invariant — see round-2 amendment #38, A8).
"""

from __future__ import annotations

import dataclasses
import inspect

from daemon.sources.base import IncomingMessage


class TestIncomingMessageNoImageRefsField:
    def test_image_refs_not_a_field(self):
        """``IncomingMessage`` MUST NOT have an ``image_refs`` field."""
        field_names = {f.name for f in dataclasses.fields(IncomingMessage)}
        assert "image_refs" not in field_names, (
            "DO NOT ADD 'image_refs' to IncomingMessage. "
            "Refs are POST-only on MessageCreate. Sources mint "
            "text and the legacy 'images' field. Adding 'image_refs' "
            "here would re-open the chat-source live-injection "
            "invariant bypass (sources could mint refs that ride "
            "the FIFO lane — see phase2-plan.md Task 11, "
            "round-2 amendment #38, freeze-list A8)."
        )

    def test_legacy_image_field_still_present(self):
        """The legacy images field stays so sources can still carry
        legacy attachments (Discord HTTPS URLs, etc.)."""
        field_names = {f.name for f in dataclasses.fields(IncomingMessage)}
        assert "images" in field_names


class TestIncomingMessageGuardComment:
    def test_docstring_calls_out_invariant(self):
        """The IncomingMessage docstring is refreshed to call out the
        POST-only invariant for ``image_refs`` (round-2 amend #38)."""
        doc = inspect.getdoc(IncomingMessage) or ""
        assert "image_refs" in doc, (
            "IncomingMessage docstring MUST mention the 'image_refs' "
            "POST-only invariant (round-2 amendment #38)."
        )
        assert "POST-only" in doc or "MUST NOT" in doc, (
            "IncomingMessage docstring MUST be unambiguous about "
            "refs being POST-only — partial wording leaves room for "
            "a future contributor to misinterpret."
        )
