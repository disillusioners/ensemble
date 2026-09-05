"""Contract tests for the Phase 5 in-process scripted chat-model seam."""

from langchain_core.messages import AIMessage

from tests.support.scripted_chat_model import ScriptedChatModel


def test_consumes_multi_turn_script_and_raises_on_under_scripting():
    model = ScriptedChatModel(
        responses=[AIMessage(content="one"), AIMessage(content="two")],
        i=0,
    )
    assert model.invoke([]).content == "one"
    assert model.invoke([]).content == "two"
    assert model.calls_made == 2
    try:
        model.invoke([])
    except IndexError as exc:
        assert "exhausted" in str(exc)
    else:  # pragma: no cover - assertion fallback
        raise AssertionError("under-scripting did not fail loudly")
