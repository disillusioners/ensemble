"""Deterministic in-process chat model for attestation graph tests.

The production graph builds its LLM instances in
``daemon.graph.build_instance_llms``.  Tests patch that function with the
model returned by this module, so the same scripted sequence is exercised by
the real graph assembly and node wiring without opening a socket or depending
on provider timing.

Unlike LangChain's upstream fake, this model deliberately does **not** cycle
its responses.  Running out of script is a test failure, not a silent
assumption that another canned turn exists.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(FakeMessagesListChatModel):
    """A consuming ``FakeMessagesListChatModel`` for multi-turn graph tests.

    ``responses`` is a sequence of messages indexed by invocation.  The first
    invocation returns ``responses[0]``; each later invocation returns the
    next message.  Calling beyond the sequence raises ``IndexError`` loudly.

    The model is already tool-bound in the test seam (the graph's
    ``build_instance_llms`` patch returns it for both LLM slots), so
    ``bind_tools`` intentionally returns ``self``.
    """

    def bind_tools(self, tools: list[Any] | tuple[Any, ...] | None = None, **kwargs: Any):  # noqa: D401
        """Keep the scripted model as the bound model used by the graph."""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.i >= len(self.responses):
            raise IndexError(
                "ScriptedChatModel exhausted: the graph requested an "
                "additional LLM turn; add the next BaseMessage to the test script"
            )
        response = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    @property
    def calls_made(self) -> int:
        """Number of responses consumed so far."""
        return int(self.i)

    def assert_all_responses_consumed(self) -> None:
        """Raise if the test supplied responses that were never exercised."""
        if self.i != len(self.responses):
            raise AssertionError(
                f"script under-consumed: consumed {self.i} of {len(self.responses)}"
            )


def script_responses(*messages: BaseMessage) -> list[BaseMessage]:
    """Build a response script while retaining a useful static type."""
    return list(messages)
