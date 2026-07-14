"""Image analysis tools for vision-capable agents.

Mirrors the closure-injection pattern of ``daemon.tools.chart_tools`` and
``daemon.tools.knowledge_tools``: ``create_image_tools(manager, current_instance_id)``
is invoked from ``create_instance_tools`` to assemble the per-instance tool list.
The generated ``explain_image`` tool delegates to the ``image-reader`` agent via
``invoke_agent_and_wait`` and returns the vision model's analysis of the image.
"""

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.utils import invoke_agent_and_wait

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Image"
CATEGORY_DOC = """\
Image analysis tools for vision-capable agents.

explain_image() delegates to the image-reader agent which analyzes
images (URLs or local paths) and answers questions about them using a
vision-capable LLM.
"""


def create_image_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create image analysis tools with injected manager reference.

    Args:
        manager: The InstanceManager instance to use for operations.
        current_instance_id: The ID of the current instance (used as parent
            for the spawned image-reader instance).

    Returns:
        List of tool functions: [explain_image]
    """

    def _get_project_id() -> str | None:
        """Auto-inject project_id from instance context."""
        try:
            # Use _instance_repository directly - get_instance() returns
            # CompiledStateGraph, not metadata.
            instance_meta = manager._instance_repository.get(current_instance_id)
            if instance_meta and instance_meta.project_id:
                return instance_meta.project_id
        except Exception:
            pass
        return None

    @register_tool_category("image")
    @tool
    async def explain_image(
        image: str,
        question: str = "Describe this image in detail",
    ) -> str:
        """Analyze an image by delegating to the image-reader agent.

        Sends an image URL or local path and a question to the
        image-reader agent, which uses a vision-capable model to interpret
        the image and answer the question.

        Args:
            image: URL or local file path to the image to analyze.
            question: The question to answer about the image. Defaults to
                "Describe this image in detail" which produces a thorough
                description; pass a more specific question to elicit a
                targeted answer.

        Returns:
            The image-reader agent's response analyzing the image.
        """
        pid = _get_project_id()

        # Construct a structured prompt for the image-reader agent. Mirrors
        # the ``explore()`` / ``generate_chart()`` style — short label-style
        # header lines so the agent has explicit context for the image and
        # optional project scope.
        image_message = f"Image: {image}\n\nQuestion: {question}"
        if pid:
            image_message += f"\nProject: {pid}"

        # Invoke image-reader agent synchronously — the tool waits for the
        # vision model's analysis. Always returns ``(content, instance_id)``
        # tuple when ``return_instance_id=True``.
        result, child_instance_id = await invoke_agent_and_wait(
            manager=manager,
            agent_id="image-reader",
            message=image_message,
            project_id=pid,
            parent_id=current_instance_id,
            instance_name=f"image-{image[:30]}",
            timeout=300.0,
            return_instance_id=True,
        )

        # Handle error results — ``invoke_agent_and_wait`` returns
        # ``"Error: ..."`` on failure / timeout when ``return_instance_id``
        # is True we still get the tuple; collapse to a single string for
        # the tool response. A ``None`` content means the agent never
        # produced a result (e.g. hard timeout during cleanup).
        if result is None:
            return "Error: image-reader agent timed out or failed. Try a different image or question."
        return result

    explain_image._full_doc_ = """\
Analyze an image by delegating to the image-reader agent.

Sends an image URL or local path along with a question to the
image-reader agent. The image-reader uses a vision-capable LLM to
interpret the image and answer the question. Use this tool whenever
the user supplies an image and asks about its contents — visual
content cannot be inferred from text alone.

The tool blocks until the agent produces its final response (default
``timeout`` = 300s) and returns the agent's text — a description,
answer, or analysis — directly to the caller.

Args:
    image: URL or local file path to the image to analyze. URLs are
        fetched by the image-reader agent; local paths must be
        accessible to the agent process.
    question: The question to answer about the image. Defaults to
        "Describe this image in detail" which produces a thorough
        description. Try more specific questions to elicit targeted
        answers ("What is the error message on line 3?", "How many
        people are in this diagram?", "Summarize the architecture in
        this flowchart.").

Returns:
    image-reader agent's response analyzing the image. On timeout or
    failure the tool returns a short ``"Error: ..."`` string.
"""

    return [explain_image]
