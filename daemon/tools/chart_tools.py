"""Chart generation tools for producing validated Mermaid diagrams.

Mirrors the closure-injection pattern of ``daemon.tools.knowledge_tools``:
``create_chart_tools(manager, current_instance_id)`` is invoked from
``create_instance_tools`` to assemble the per-instance tool list. The
generated ``generate_chart`` tool delegates to the ``charter`` agent via
``invoke_agent_and_wait`` and returns the validated Mermaid output.
"""

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.utils import invoke_agent_and_wait

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Chart"
CATEGORY_DOC = """\
Chart generation tools for producing validated Mermaid diagrams.

generate_chart() delegates to the Charter agent which produces
syntax-validated Mermaid diagrams across flowchart, sequence, class,
er, state, and gantt types.
"""


def create_chart_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create chart generation tools with injected manager reference.

    Args:
        manager: The InstanceManager instance to use for operations.
        current_instance_id: The ID of the current instance (used as parent
            for the spawned charter instance).

    Returns:
        List of tool functions: [generate_chart]
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

    @register_tool_category("chart")
    @tool
    async def generate_chart(
        description: str,
        diagram_type: str = "flowchart",
        project_id: str | None = None,
    ) -> str:
        """Generate a validated Mermaid diagram by delegating to the Charter agent.

        Sends a structured request to the Charter agent, which produces
        syntax-validated Mermaid diagrams and returns them in a fenced
        ```` ```mermaid ```` code block with a brief explanation. Use this for
        any structural artifact: architectures, process flows, state machines,
        data models, timelines.

        Args:
            description: What the diagram should show — the subject, scope,
                and structure to visualize. Be specific: name the nodes /
                actors / entities and the relationships between them.
            diagram_type: Type of Mermaid diagram to produce — one of
                "flowchart", "sequence", "class", "er", "state", "gantt".
                Defaults to "flowchart".
            project_id: Optional project ID. Auto-detected from context
                if not provided.

        Returns:
            The Charter agent's response containing a validated ```` ```mermaid ````
            fenced code block and a brief explanation.
        """
        pid = project_id or _get_project_id()

        # Construct a structured prompt for the charter agent. Mirrors the
        # ``explore()`` style at knowledge_tools.py — short label-style header
        # lines so the agent has explicit context for type and project scope.
        chart_message = (
            f"Create a {diagram_type} diagram.\n\n"
            f"Description: {description}\n"
        )
        if pid:
            chart_message += f"Project: {pid}\n"

        # Invoke charter agent synchronously — the tool waits for the
        # validated Mermaid output. Always returns ``(content, instance_id)``
        # tuple when ``return_instance_id=True``.
        result, child_instance_id = await invoke_agent_and_wait(
            manager=manager,
            agent_id="charter",
            message=chart_message,
            project_id=pid,
            parent_id=current_instance_id,
            instance_name=f"chart-{description[:30]}",
            timeout=300.0,
            return_instance_id=True,
        )

        # Handle error results — ``invoke_agent_and_wait`` returns
        # ``"Error: ..."`` on failure / timeout when ``return_instance_id`` is
        # True we still get the tuple; collapse to a single string for the
        # tool response. A ``None`` content means the agent never produced a
        # result (e.g. hard timeout during cleanup).
        if result is None:
            return "Error: Charter agent timed out or failed. Try a simpler description."
        return result

    generate_chart._full_doc_ = """\
Generate a validated Mermaid diagram by delegating to the Charter agent.

Sends a structured request to the Charter agent, which produces
syntax-validated Mermaid diagrams and returns them in a fenced code
block. The agent is responsible for:

1. Selecting the appropriate diagram type (if ``diagram_type`` is left
   implicit by the caller, charter will infer from the description).
2. Drafting the Mermaid syntax.
3. Validating via ``npx -y @mermaid-js/mermaid-cli``.
4. Returning the validated diagram with a brief explanation.

The tool blocks until the agent produces its final response (default
``timeout`` = 300s) and returns the agent's text — a ```` ```mermaid ````
fenced block plus explanation — directly to the caller. Paste it into
your response without re-wrapping or stripping the fence.

Args:
    description: What the diagram should show — the subject, scope, and
        structure to visualize. Be specific: name the nodes / actors /
        entities and the relationships between them. Example:
        "Create a flowchart TD showing the authentication request flow.
        Include: User, Auth Service, Token Store, Protected Resource, and
        the decision branches for valid/invalid tokens."
    diagram_type: Type of Mermaid diagram — one of "flowchart",
        "sequence", "class", "er", "state", "gantt". Defaults to
        "flowchart".
    project_id: Optional project ID. Auto-detected from current instance
        context if not provided.

Returns:
    Charter agent's response containing a single ```` ```mermaid ```` fenced
    code block (the validated diagram) and a brief explanation. On
    timeout or failure the tool returns a short ``"Error: ..."`` string.
"""

    return [generate_chart]
