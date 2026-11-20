"""Router pin for the compact-fresh-swap continuation."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from daemon.graph import _fresh_compact_context_continuation


def _note():
    return HumanMessage(
        content="[SYSTEM CONTEXT: Compacted Context]\nhi",
        additional_kwargs={"injected_message": True, "context_kind": "compacted_context"},
    )


def test_note_last_fires():
    msgs = [HumanMessage(content="x"), _note()]
    assert _fresh_compact_context_continuation(msgs) is True


def test_ack_aimessage_last_no_fire():
    msgs = [_note(), AIMessage(content="ack")]
    assert _fresh_compact_context_continuation(msgs) is False


def test_plain_trailing_human_no_fire():
    msgs = [HumanMessage(content="x"), HumanMessage(content="y")]
    assert _fresh_compact_context_continuation(msgs) is False


def test_arrivals_humans_between_note_and_end_fire():
    note = _note()
    msgs = [note, HumanMessage(content="a"), HumanMessage(content="b")]
    assert _fresh_compact_context_continuation(msgs) is True


def test_toolmessage_last_no_fire():
    msgs = [_note(), ToolMessage(content="t", name="x", tool_call_id="1")]
    assert _fresh_compact_context_continuation(msgs) is False
