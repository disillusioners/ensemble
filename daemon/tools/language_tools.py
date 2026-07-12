"""Language-related tools for the agent."""
import logging
from langchain_core.tools import tool
from ._tool_registry import register_tool

logger = logging.getLogger(__name__)


@register_tool(
    "language_skip_check",
    category="language",
    short_doc="Skip the language check for the next message.",
    full_doc="""Skip the language preference check for your next response.

Use this when you intentionally need to respond in a different language
(e.g., translating a file, writing a multilingual README, or outputting
code with non-English comments).

The skip applies to ONE message only — the next response will be checked
again normally.

Returns:
    Confirmation message.
""",
)
@tool
def language_skip_check() -> str:
    """Skip the language check for the next message."""
    return "Language check skipped for the next message. The system will not enforce the preferred language on your next response."


def create_language_tools():
    """Create language-related tools for an instance."""
    return [language_skip_check]
