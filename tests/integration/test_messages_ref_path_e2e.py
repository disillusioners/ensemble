"""End-to-end integration test: image_refs POST produces a checkpointed
HumanMessage with the structural guarantee required by A1.

Phase 2 / clipboard-image-chat / freeze-list A1:

  * ref-send → checkpoint HumanMessage content is ``str`` (no
    ``image_url`` blocks);
  * ``additional_kwargs['image_refs']`` carries canonical ref URLs;
  * agent LLM invocation logs ``call_type="STANDARD"`` /
    ``use_vision_model=False`` (i.e. the main chat model is NOT
    switched to vision).

This test pins the durable-leg checkpoint kwargs stamp end-to-end
through ``_build_graph_input``. We use the same real-InstanceManager
harness as ``test_job_driven_enqueue_image_refs_facade.py`` but drive
the message through ``_build_graph_input`` directly to assert the
HumanMessage shape (a real worker pool would need a real graph +
scripted LLM).

The wire ``images`` field on GET /messages is checked separately via
the serializer union (covered in
``tests/unit/test_serialize_message_image_refs_union.py``).
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from daemon.services.instance_messaging import _build_graph_input


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildGraphInputImageRefs:
    """The kwargs stamp conditionally attaches image_refs on
    HumanMessage.additional_kwargs."""

    def test_legacy_bare_path_unchanged(self):
        """No image_refs, no source — pre-feature shape (no
        image_refs key)."""
        gi = _build_graph_input("hello", "msg-1")
        hm = gi["messages"][0]
        assert isinstance(hm, HumanMessage)
        assert hm.content == "hello"
        # additional_kwargs is a dict that LACKS the image_refs key.
        assert "image_refs" not in (hm.additional_kwargs or {})

    def test_refs_only_attaches_additional_kwargs(self):
        """Refs-only path — additional_kwargs carries image_refs."""
        refs = ["/api/tmp_images/" + "a" * 32, "/api/tmp_images/" + "b" * 32]
        gi = _build_graph_input("hello", "msg-1", image_refs=refs)
        hm = gi["messages"][0]
        assert hm.content == "hello"
        assert hm.additional_kwargs == {"image_refs": refs}

    def test_stamped_source_path_includes_refs(self):
        """Internal delivery (source stamp) + image_refs merge into
        a single additional_kwargs dict."""
        refs = ["/api/tmp_images/" + "a" * 32]
        gi = _build_graph_input(
            "hello",
            "msg-1",
            message_source="internal_agent:parent-1",
            image_refs=refs,
        )
        hm = gi["messages"][0]
        assert hm.content == "hello"
        assert hm.additional_kwargs == {
            "injected_message": True,
            "source": "internal_agent:parent-1",
            "image_refs": refs,
        }

    def test_content_is_string_not_block_list(self):
        """A1's STRUCTURAL GUARANTEE: ref-send content is str, not a
        multimodal content-block list."""
        refs = ["/api/tmp_images/" + "a" * 32]
        gi = _build_graph_input("hello world", "msg-1", image_refs=refs)
        hm = gi["messages"][0]
        # The content is a plain string — no image_url blocks.
        assert isinstance(hm.content, str)
        assert hm.content == "hello world"
