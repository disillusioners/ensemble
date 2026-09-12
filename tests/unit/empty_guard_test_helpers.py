"""Shared scaffolding for the empty-response-guard test files.

Holds the autouse config-reset fixture and the two message helpers
previously duplicated across ``test_empty_response_guard.py``,
``test_response_validation.py``, and ``test_empty_guard_config.py``
(T2, 2026-09-12 tidy pass). The ``_tool_calling_ai`` variants are
deliberately NOT unified (dict-shaped vs ToolCall-shaped tool_calls).
"""

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from daemon.response_validation import _reset_empty_guard_config_for_tests


@pytest.fixture(autouse=True)
def _restore_empty_guard_defaults():
    """The importing test module starts and ends on the documented defaults."""
    _reset_empty_guard_config_for_tests()
    yield
    _reset_empty_guard_config_for_tests()


def _real_human(text="Do the thing"):
    return HumanMessage(content=text)


def _tool_result():
    return ToolMessage(content="tool output", tool_call_id="call_1")
