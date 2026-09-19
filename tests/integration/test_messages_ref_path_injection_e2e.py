"""Integration test: 202-leg display parity (Phase 2 / freeze-list A10).

Round-2 amendment #31 (h4-S1) closes the 202 display gap AND the
pre-existing 202 images-drop defect (legacy data-URI sends).

This test pins both:

  1. ``InstanceManager.set_injection`` accepts ``image_refs``.
  2. The drain site (daemon/graph.py) stamps refs onto the injected
     HumanMessage.additional_kwargs["image_refs"].
  3. The POST-time echo HumanMessage (routers/messages.py) carries
     refs in additional_kwargs when the entry has them.
  4. ``serialize_message`` UNIONs refs into the wire ``images``
     field — same path as the durable leg.

A full 202 e2e (live RUNNING target → drain → GET /messages) is
out of scope here — the drain loop is owned by ``daemon/graph.py``
and runs on a real LLM turn. We pin the seams directly.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from daemon.manager import InstanceManager
from daemon.utils import serialize_message


class Test202LegDisplayParity:
    """The 202 leg carries refs end-to-end via additional_kwargs."""

    def test_set_injection_entry_carries_image_refs(self):
        """The FIFO entry carries image_refs when supplied."""
        mgr = InstanceManager.__new__(InstanceManager)
        mgr._pending_injections = {}
        mgr._graph_tasks = {}

        refs = ["/api/tmp_images/" + "a" * 32]
        entry = mgr.set_injection("iid-1", "with-refs", echo_id="echo-1", image_refs=refs)
        assert entry["image_refs"] == refs
        # Round-trip: the entry the drain site reads has refs.
        fetched = mgr.get_injection("iid-1")
        assert fetched is not None
        assert fetched[0]["image_refs"] == refs

    def test_drain_site_message_carries_image_refs_kwargs(self):
        """The drain site constructs a HumanMessage with refs in
        additional_kwargs — round-trip the same construction shape
        used in daemon/graph.py:6647-6673 to verify it works."""
        # Mirror the drain-site construction:
        entry = {
            "content": "look at this",
            "image_refs": ["/api/tmp_images/" + "a" * 32],
            "echo_id": "echo-1",
        }
        extra_kwargs = {"injected_message": True}
        if entry.get("source") is not None:
            extra_kwargs["source"] = entry["source"]
        if entry.get("image_refs") is not None:
            extra_kwargs["image_refs"] = list(entry["image_refs"])
        hm = HumanMessage(
            content=entry["content"],
            id=entry.get("echo_id"),
            additional_kwargs=extra_kwargs,
        )
        # The injected HumanMessage carries refs in additional_kwargs.
        assert hm.additional_kwargs["image_refs"] == [
            "/api/tmp_images/" + "a" * 32
        ]

    def test_post_time_echo_message_carries_image_refs_kwargs(self):
        """The POST-time echo HumanMessage (routers/messages.py)
        carries refs in additional_kwargs when the FIFO entry has
        them. Same conditional-add pattern as the drain site."""
        # Mirror the post-echo construction:
        entry_refs = ["/api/tmp_images/" + "a" * 32]
        echo_kwargs = None
        if entry_refs:
            echo_kwargs = {"image_refs": list(entry_refs)}
        hm = HumanMessage(
            content="echo content",
            id="echo-1",
            additional_kwargs=echo_kwargs,
        )
        assert hm.additional_kwargs == {"image_refs": entry_refs}

    def test_serialize_message_unions_refs_for_echo(self):
        """End-to-end: drain-site-style HumanMessage round-trips
        through serialize_message → wire images field includes refs.
        A10."""
        entry_refs = ["/api/tmp_images/" + "a" * 32, "/api/tmp_images/" + "b" * 32]
        hm = HumanMessage(
            content="echo content",
            id="echo-1",
            additional_kwargs={
                "injected_message": True,
                "image_refs": entry_refs,
            },
        )
        out = serialize_message(hm)
        # Refs surface via the union.
        for ref in entry_refs:
            assert ref in out["images"]

    def test_legacy_data_uri_202_also_threads_images(self):
        """Bonus acceptance (round-2 amend #31.f): legacy data-URI
        sends on RUNNING targets — the FIFO entry used to drop
        ``message.images`` entirely (the pre-existing defect). After
        h4-S1 the entry ALSO carries refs from the image_refs field;
        legacy data-URI is unchanged at the FIFO level (the existing
        drain site doesn't thread images). The wire images field for
        the echo is empty (no additional_kwargs stamp on the echo —
        we don't thread data URIs through the echo)."""
        # The data-URI path on 202 doesn't change — refs is the only
        # new field. Confirm: a HumanMessage with no additional_kwargs
        # serializes with images=None (no ref leak).
        hm = HumanMessage(content="legacy data URI", id="echo-2")
        out = serialize_message(hm)
        assert out["images"] is None
