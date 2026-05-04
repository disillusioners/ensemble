"""RAG knowledge management tools for interacting with LightRAG."""

import hashlib
import logging
import re
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.rag.client import AsyncLightRAGClient
from daemon.rag.config import is_rag_enabled
from daemon.rag.exceptions import RAGError

logger = logging.getLogger(__name__)

CATEGORY_NAME = "RAG"
CATEGORY_DOC = """RAG knowledge management tools for interacting with LightRAG.
These tools allow querying, inserting, and managing knowledge in the RAG system."""

# Module-level client singleton
_rag_client: AsyncLightRAGClient | None = None


def _get_rag_client() -> AsyncLightRAGClient:
    """Get or create the RAG client singleton.

    Returns:
        AsyncLightRAGClient instance.
    """
    global _rag_client
    if _rag_client is None:
        _rag_client = AsyncLightRAGClient()
    return _rag_client


def _get_project_workspace(manager: Any, current_instance_id: str) -> str | None:
    """Extract project name from instance metadata to use as RAG workspace.

    Args:
        manager: The InstanceManager instance.
        current_instance_id: The current instance ID.

    Returns:
        Project name if found and non-empty, project_id as fallback, None otherwise.
    """
    try:
        instance = manager._instance_repository.get(current_instance_id)
        if instance and instance.instance_metadata:
            project_id = instance.instance_metadata.get("project_id")
            if project_id:
                project = manager._project_repository.get(project_id)
                if project and project.name:
                    return project.name
                return project_id
    except Exception:
        pass
    return None


def _slugify(text: str, max_length: int = 50) -> str:
    """Convert text to a URL-safe slug.

    Args:
        text: Text to slugify.
        max_length: Maximum length of the slug.

    Returns:
        Slugified text.
    """
    # Convert to lowercase and replace non-alphanumeric with hyphens
    slug = re.sub(r'[^a-z0-9\s-]', '', text.lower())
    slug = re.sub(r'[\s]+', '-', slug)
    slug = slug.strip('-')
    return slug[:max_length]


def _generate_simple_filename(text: str) -> str:
    """Generate a simple descriptive filename from text content.

    Args:
        text: The text content.

    Returns:
        A short descriptive filename (slugified first line or hash).
    """
    if not text:
        return "untitled"

    # Get first line and extract first meaningful part
    first_line = text.strip().split('\n')[0].strip()
    if len(first_line) >= 5:
        return _slugify(first_line, max_length=40)
    else:
        # Use a short hash of the text if too short
        short_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        return f"doc-{short_hash}"


def _get_project_name_from_instance(manager: Any, instance_id: str) -> str | None:
    """Get project name from instance metadata.

    Args:
        manager: The InstanceManager instance.
        instance_id: The current instance ID.

    Returns:
        Project name if found, None otherwise.
    """
    try:
        instance_meta = manager._instance_repository.get(instance_id)
        if instance_meta and instance_meta.instance_metadata:
            project_id = instance_meta.instance_metadata.get("project_id")
            if project_id:
                project = manager._project_repository.get(project_id)
                if project:
                    return project.name
    except Exception:
        pass
    return None


def _generate_file_source(
    manager: Any,
    instance_id: str,
    category: str,
    text: str,
) -> str:
    """Generate a file_source path for a text insertion.

    Format: projects/<project-name>/docs/<category>/<simple-hash>.md

    Args:
        manager: The InstanceManager instance.
        instance_id: The current instance ID.
        category: Content category (e.g., "general", "architecture").
        text: The text content for generating filename.

    Returns:
        Generated file_source path.
    """
    # Try to get project name, fall back to instance_id
    project_name = _get_project_name_from_instance(manager, instance_id)
    if not project_name:
        project_name = instance_id[:8]

    # Generate descriptive filename
    filename = _generate_simple_filename(text)

    # Sanitize project name for path
    project_path = re.sub(r'[^a-z0-9-]', '-', project_name.lower())
    project_path = re.sub(r'-+', '-', project_path).strip('-')

    return f"projects/{project_path}/docs/{category}/{filename}.md"


def create_rag_tools(
    manager: Any,  # InstanceManager, avoid circular import
    current_instance_id: str,
) -> list:
    """Create all RAG tools with proper error handling.

    Args:
        manager: The InstanceManager instance (unused but part of factory signature).
        current_instance_id: The current instance ID (unused but part of factory signature).

    Returns:
        List of RAG tool functions.
    """

    def _get_workspace() -> str | None:
        """Extract project name from instance metadata to use as RAG workspace."""
        return _get_project_workspace(manager, current_instance_id)

    @register_tool_category("rag")
    @tool
    async def rag_insert_text(
        text: str,
        file_source: str | None = None,
        category: str = "general",
    ) -> str:
        """Insert a single text into the RAG knowledge graph.

        Args:
            text: The text content to insert.
            file_source: File source path. LLM should generate this explicitly.
                Format: projects/<project>/docs/<category>/<filename>.md
                If not provided, a fallback path will be auto-generated (warning logged).
            category: Content category for organization (e.g., "general", "architecture", "api", "knowledge", "experience").

        Returns:
            Success message with track ID for tracking async operations.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_insert_text: could not resolve workspace from instance %s project_id. "
                "Query will use default workspace.",
                current_instance_id,
            )
        try:
            if file_source is None:
                logger.warning(
                    "rag_insert_text called with null file_source, using fallback generation. "
                    "LLM should provide file_source explicitly. Text preview: %s...",
                    text[:100]
                )
                file_source = _generate_file_source(
                    manager, current_instance_id, category, text
                )

            result = await client.insert_text(
                text=text,
                file_source=file_source,
                workspace=workspace,
            )
            return f"Text inserted. Track ID: {getattr(result, 'track_id', '')}"
        except RAGError as e:
            return f"RAG error: {e}"

    rag_insert_text._full_doc_ = """Insert a single text into the RAG knowledge graph.

    Args:
        text: The text content to insert.
        file_source: File source path. LLM should generate this explicitly.
            Format: projects/<project>/docs/<category>/<filename>.md
            If not provided, a fallback path will be auto-generated.
        category: Content category for organization (default: "general").
            Common categories: "general", "architecture", "api", "knowledge", "experience"

    Returns:
        Success message with track ID for tracking async operations.
    """

    @register_tool_category("rag")
    @tool
    async def rag_insert_texts(
        texts: list[str],
        file_sources: list[str] | None = None,
    ) -> str:
        """Insert multiple texts into the RAG knowledge graph.

        Args:
            texts: List of text strings to insert.
            file_sources: Optional list of file sources corresponding to texts.

        Returns:
            Success message with track ID for tracking async operations.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_insert_texts: could not resolve workspace from instance %s project_id. "
                "Query will use default workspace.",
                current_instance_id,
            )
        try:
            result = await client.insert_texts(
                texts=texts,
                file_sources=file_sources,
                workspace=workspace,
            )
            return f"{len(texts)} texts inserted. Track ID: {getattr(result, 'track_id', '')}"
        except RAGError as e:
            return f"RAG error: {e}"

    rag_insert_texts._full_doc_ = """Insert multiple texts into the RAG knowledge graph.

    Args:
        texts: List of text strings to insert.
        file_sources: Optional list of file sources corresponding to texts.
            Must match the length of texts if provided.

    Returns:
        Success message with track ID for tracking async operations.
    """

    @register_tool_category("rag")
    @tool
    async def rag_query(
        query: str,
        mode: str = "mix",
        only_need_context: bool = False,
        only_need_prompt: bool = False,
        response_type: str | None = None,
        top_k: int | None = None,
        chunk_top_k: int | None = None,
        max_entity_tokens: int | None = None,
        max_relation_tokens: int | None = None,
        max_total_tokens: int | None = None,
        hl_keywords: list[str] | None = None,
        ll_keywords: list[str] | None = None,
        conversation_history: list[dict] | None = None,
    ) -> str:
        """Query the RAG knowledge graph and get a generated response.

        Args:
            query: The query string to search for.
            mode: Query mode - one of local, global, hybrid, naive, mix (default: mix).
            only_need_context: Return only context without full response.
            only_need_prompt: Return only the generated prompt.
            response_type: Type of response to generate.
            top_k: Number of top results to return.
            chunk_top_k: Number of chunks to retrieve.
            max_entity_tokens: Max tokens for entity retrieval.
            max_relation_tokens: Max tokens for relation retrieval.
            max_total_tokens: Max total tokens for the response.
            hl_keywords: High-level keywords for query enhancement.
            ll_keywords: Low-level keywords for query enhancement.
            conversation_history: List of conversation history turns.

        Returns:
            Generated response text from the knowledge graph.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_query: could not resolve workspace from instance %s project_id. "
                "Query will use default workspace.",
                current_instance_id,
            )
        try:
            result = await client.query(
                query=query,
                mode=mode,
                only_need_context=only_need_context,
                only_need_prompt=only_need_prompt,
                response_type=response_type,
                top_k=top_k,
                chunk_top_k=chunk_top_k,
                max_entity_tokens=max_entity_tokens,
                max_relation_tokens=max_relation_tokens,
                max_total_tokens=max_total_tokens,
                hl_keywords=hl_keywords,
                ll_keywords=ll_keywords,
                conversation_history=conversation_history,
                workspace=workspace,
            )
            return getattr(result, 'response', '')
        except RAGError as e:
            return f"RAG error: {e}"

    rag_query._full_doc_ = """Query the RAG knowledge graph and get a generated response.

    Args:
        query: The query string to search for.
        mode: Query mode - one of local, global, hybrid, naive, mix (default: mix).
        only_need_context: Return only context without full response (default: False).
        only_need_prompt: Return only the generated prompt (default: False).
        response_type: Type of response to generate (optional).
        top_k: Number of top results to return (optional).
        chunk_top_k: Number of chunks to retrieve (optional).
        max_entity_tokens: Max tokens for entity retrieval (optional).
        max_relation_tokens: Max tokens for relation retrieval (optional).
        max_total_tokens: Max total tokens for the response (optional).
        hl_keywords: High-level keywords for query enhancement (optional).
        ll_keywords: Low-level keywords for query enhancement (optional).
        conversation_history: List of conversation history turns (optional).

    Returns:
        Generated response text from the knowledge graph.
    """

    @register_tool_category("rag")
    @tool
    async def rag_query_data(
        query: str,
        mode: str = "mix",
        only_need_context: bool = False,
        only_need_prompt: bool = False,
        response_type: str | None = None,
        top_k: int | None = None,
        chunk_top_k: int | None = None,
        max_entity_tokens: int | None = None,
        max_relation_tokens: int | None = None,
        max_total_tokens: int | None = None,
        hl_keywords: list[str] | None = None,
        ll_keywords: list[str] | None = None,
        conversation_history: list[dict] | None = None,
    ) -> str:
        """Query the RAG knowledge graph and get structured data (entities and relations).

        Args:
            query: The query string to search for.
            mode: Query mode - one of local, global, hybrid, naive, mix (default: mix).
            only_need_context: Return only context without full response.
            only_need_prompt: Return only the generated prompt.
            response_type: Type of response to generate.
            top_k: Number of top results to return.
            chunk_top_k: Number of chunks to retrieve.
            max_entity_tokens: Max tokens for entity retrieval.
            max_relation_tokens: Max tokens for relation retrieval.
            max_total_tokens: Max total tokens for the response.
            hl_keywords: High-level keywords for query enhancement.
            ll_keywords: Low-level keywords for query enhancement.
            conversation_history: List of conversation history turns.

        Returns:
            Formatted string containing entities and relations from the query.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_query_data: could not resolve workspace from instance %s project_id. "
                "Query will use default workspace.",
                current_instance_id,
            )
        try:
            result = await client.query_data(
                query=query,
                mode=mode,
                only_need_context=only_need_context,
                only_need_prompt=only_need_prompt,
                response_type=response_type,
                top_k=top_k,
                chunk_top_k=chunk_top_k,
                max_entity_tokens=max_entity_tokens,
                max_relation_tokens=max_relation_tokens,
                max_total_tokens=max_total_tokens,
                hl_keywords=hl_keywords,
                ll_keywords=ll_keywords,
                conversation_history=conversation_history,
                workspace=workspace,
            )

            output_parts: list[str] = []

            # Try to extract entities from result
            entities = getattr(result, 'entities', []) or []
            if not entities and hasattr(result, 'data'):
                data = result.data or {}
                entities = data.get('entities', []) or []

            if entities:
                output_parts.append(f"## Entities ({len(entities)} found)\n")
                for entity in entities:
                    # Use entity_name and entity_type (LightRAG's actual field names)
                    name = entity.get("entity_name", entity.get("name", "Unknown"))
                    entity_type = entity.get("entity_type", entity.get("type", "UNKNOWN"))
                    desc = entity.get("description", "")
                    # Keep first part before <SEP> separator (LightRAG concatenates descriptions with this)
                    if "<SEP>" in desc:
                        desc = desc.split("<SEP>")[0].strip()
                    output_parts.append(f"- **{name}** ({entity_type}): {desc}")

            # Try to extract relationships from result
            relationships = getattr(result, 'relationships', []) or []
            if not relationships and hasattr(result, 'data'):
                data = result.data or {}
                relationships = data.get('relationships', data.get('relations', [])) or []

            if relationships:
                output_parts.append(f"\n## Relationships ({len(relationships)} found)\n")
                for relationship in relationships:
                    # Use src_id and tgt_id (LightRAG's actual field names)
                    src = relationship.get("src_id", relationship.get("source", "?"))
                    tgt = relationship.get("tgt_id", relationship.get("target", "?"))
                    rel_type = relationship.get("relation_type", relationship.get("type", "RELATED_TO"))
                    desc = relationship.get("description", "")
                    # Keep first part before <SEP>
                    if "<SEP>" in desc:
                        desc = desc.split("<SEP>")[0].strip()
                    # Include keywords if present
                    keywords = relationship.get("keywords")
                    keywords_str = ""
                    if keywords and isinstance(keywords, list) and len(keywords) > 0:
                        keywords_str = f" [{', '.join(keywords)}]"
                    output_parts.append(f"- **{src}** → **{tgt}**: {desc}{keywords_str}")

            # Try to extract chunks from result
            chunks = getattr(result, 'chunks', []) or []
            if not chunks and hasattr(result, 'data'):
                data = result.data or {}
                chunks = data.get('chunks', []) or []

            if chunks:
                output_parts.append(f"\n## Source Chunks ({len(chunks)})\n")
                for i, chunk in enumerate(chunks, 1):
                    content = chunk.get("content", "")
                    file_path = chunk.get("file_path", "")
                    if file_path:
                        output_parts.append(f"### [{i}] {file_path}\n{content}")
                    else:
                        output_parts.append(f"### [{i}]\n{content}")

            # Try to extract references from result
            references = getattr(result, 'references', []) or []
            if not references and hasattr(result, 'data'):
                data = result.data or {}
                references = data.get('references', []) or []

            if references:
                output_parts.append("\n## References")
                for ref in references:
                    file_path = ref.get("file_path", "?")
                    output_parts.append(f"- `{file_path}`")

            if not output_parts:
                return "No entities or relations found for this query."

            return "\n".join(output_parts)
        except RAGError as e:
            return f"RAG error: {e}"

    rag_query_data._full_doc_ = """Query the RAG knowledge graph and get structured data.

    Args:
        query: The query string to search for.
        mode: Query mode - one of local, global, hybrid, naive, mix (default: mix).
        only_need_context: Return only context without full response (default: False).
        only_need_prompt: Return only the generated prompt (default: False).
        response_type: Type of response to generate (optional).
        top_k: Number of top results to return (optional).
        chunk_top_k: Number of chunks to retrieve (optional).
        max_entity_tokens: Max tokens for entity retrieval (optional).
        max_relation_tokens: Max tokens for relation retrieval (optional).
        max_total_tokens: Max total tokens for the response (optional).
        hl_keywords: High-level keywords for query enhancement (optional).
        ll_keywords: Low-level keywords for query enhancement (optional).
        conversation_history: List of conversation history turns (optional).

    Returns:
        Formatted string containing entities and relations from the query.
    """

    @register_tool_category("rag")
    @tool
    async def rag_search_labels(
        query: str,
        max_results: int = 10,
    ) -> str:
        """Search for labels in the RAG knowledge graph.

        Args:
            query: The label query to search for.
            max_results: Maximum number of results to return (default: 10).

        Returns:
            Formatted list of matching labels.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_search_labels: could not resolve workspace from instance %s project_id. "
                "Query will use default workspace.",
                current_instance_id,
            )
        try:
            result = await client.search_labels(q=query, limit=max_results, workspace=workspace)
            labels = getattr(result, 'labels', []) or []
            if not labels:
                return f"No labels found matching: {query}"
            return "Matching labels:\n" + "\n".join(f"- {lbl}" for lbl in labels)
        except RAGError as e:
            return f"RAG error: {e}"

    rag_search_labels._full_doc_ = """Search for labels in the RAG knowledge graph.

    Args:
        query: The label query to search for.
        max_results: Maximum number of results to return (default: 10).

    Returns:
        Formatted list of matching labels.
    """

    @register_tool_category("rag")
    @tool
    async def rag_get_graph(
        label: str | None = None,
        max_depth: int = 3,
        max_nodes: int = 50,
    ) -> str:
        """Get the knowledge graph or a subgraph from RAG.

        Args:
            label: Optional label to filter the graph by.
            max_depth: Maximum depth for graph traversal (default: 3).
            max_nodes: Maximum number of nodes to return (default: 50).

        Returns:
            Formatted graph data with nodes and edges.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_get_graph: could not resolve workspace from instance %s project_id. "
                "Query will use default workspace.",
                current_instance_id,
            )
        try:
            result = await client.get_graph(
                label=label,
                max_depth=max_depth,
                max_nodes=max_nodes,
                workspace=workspace,
            )

            output_parts: list[str] = []

            if label:
                output_parts.append(f"## Graph for label: {label}\n")
            else:
                output_parts.append("## Full Knowledge Graph\n")

            nodes = getattr(result, 'nodes', []) or []
            if nodes:
                output_parts.append(f"### Nodes ({len(nodes)})\n")
                for node in nodes:
                    node_id = node.get("id", node.get("name", "?"))
                    node_type = node.get("type", "UNKNOWN")
                    output_parts.append(f"- {node_id} ({node_type})")

            edges = getattr(result, 'edges', []) or []
            if edges:
                output_parts.append(f"\n### Edges ({len(edges)})\n")
                for edge in edges:
                    source = edge.get("source", "?")
                    target = edge.get("target", "?")
                    edge_type = edge.get("type", "RELATED_TO")
                    output_parts.append(f"- {source} -[{edge_type}]-> {target}")

            if not output_parts:
                return "Empty graph."

            return "\n".join(output_parts)
        except RAGError as e:
            return f"RAG error: {e}"

    rag_get_graph._full_doc_ = """Get the knowledge graph or a subgraph from RAG.

    Args:
        label: Optional label to filter the graph by.
        max_depth: Maximum depth for graph traversal (default: 3).
        max_nodes: Maximum number of nodes to return (default: 50).

    Returns:
        Formatted graph data with nodes and edges.
    """

    @register_tool_category("rag")
    @tool
    async def rag_create_entity(
        name: str,
        entity_type: str = "UNKNOWN",
        description: str = "",
        properties: dict | None = None,
    ) -> str:
        """Create a new entity in the RAG knowledge graph.

        Args:
            name: Name of the entity to create.
            entity_type: Type/category of the entity (default: UNKNOWN).
            description: Optional description of the entity.
            properties: Optional metadata dictionary.

        Returns:
            Success message confirming entity creation.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_create_entity: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            await client.create_entity(
                entity_name=name,
                entity_type=entity_type,
                description=description,
                metadata=properties,
                workspace=workspace,
            )
            return f"Entity '{name}' created."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_create_entity._full_doc_ = """Create a new entity in the RAG knowledge graph.

    Args:
        name: Name of the entity to create.
        entity_type: Type/category of the entity (default: UNKNOWN).
        description: Optional description of the entity.
        properties: Optional metadata dictionary.

    Returns:
        Success message confirming entity creation.
    """

    @register_tool_category("rag")
    @tool
    async def rag_get_entity(name: str) -> str:
        """Get an entity from the RAG knowledge graph by name.

        Args:
            name: Name of the entity to retrieve.

        Returns:
            Formatted entity details including type, description, and properties.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_get_entity: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            result = await client.get_entity(entity_name=name, workspace=workspace)

            output_parts: list[str] = []

            # Extract entity details from result
            entity_name = result.get("entity_name", result.get("name", name))
            entity_type = result.get("entity_type", "UNKNOWN")
            description = result.get("description", "")

            output_parts.append(f"## Entity: {entity_name}")
            output_parts.append(f"**Type:** {entity_type}")

            if description:
                # Keep first part before <SEP> separator
                if "<SEP>" in description:
                    description = description.split("<SEP>")[0].strip()
                output_parts.append(f"\n**Description:**\n{description}")

            # Include any additional properties
            properties = {k: v for k, v in result.items()
                          if k not in ("entity_name", "name", "entity_type", "type", "description")}
            if properties:
                output_parts.append("\n**Properties:**")
                for key, value in properties.items():
                    output_parts.append(f"- {key}: {value}")

            return "\n".join(output_parts)
        except RAGError as e:
            return f"RAG error: {e}"

    rag_get_entity._full_doc_ = """Get an entity from the RAG knowledge graph by name.

    Args:
        name: Name of the entity to retrieve.

    Returns:
        Formatted entity details including type, description, and properties.
    """

    @register_tool_category("rag")
    @tool
    async def rag_create_relation(
        source: str,
        target: str,
        relation_type: str = "RELATED_TO",
        description: str = "",
        properties: dict | None = None,
    ) -> str:
        """Create a relation between two entities in the RAG knowledge graph.

        Args:
            source: Name of the source entity.
            target: Name of the target entity.
            relation_type: Type of the relation (default: RELATED_TO).
            description: Optional description of the relation.
            properties: Optional metadata dictionary.

        Returns:
            Success message confirming relation creation.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_create_relation: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            await client.create_relation(
                source_entity=source,
                target_entity=target,
                relation_type=relation_type,
                description=description,
                metadata=properties,
                workspace=workspace,
            )
            return f"Relation created: {source} -[{relation_type}]-> {target}"
        except RAGError as e:
            return f"RAG error: {e}"

    rag_create_relation._full_doc_ = """Create a relation between two entities in the RAG knowledge graph.

    Args:
        source: Name of the source entity.
        target: Name of the target entity.
        relation_type: Type of the relation (default: RELATED_TO).
        description: Optional description of the relation.
        properties: Optional metadata dictionary.

    Returns:
        Success message confirming relation creation.
    """

    @register_tool_category("rag")
    @tool
    async def rag_update_entity(
        name: str,
        updated_name: str | None = None,
        entity_type: str | None = None,
        description: str | None = None,
        properties: dict | None = None,
        allow_rename: bool = False,
        allow_merge: bool = False,
    ) -> str:
        """Update an existing entity in the RAG knowledge graph.

        Args:
            name: Name of the entity to update.
            updated_name: New name for the entity.
            entity_type: New type/category for the entity.
            description: New description for the entity.
            properties: New metadata dictionary.
            allow_rename: Allow renaming the entity (default: False).
            allow_merge: Allow merging with existing entity (default: False).

        Returns:
            Success message confirming entity update.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_update_entity: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            metadata = properties.copy() if properties else {}
            if updated_name is not None:
                metadata["name"] = updated_name
            await client.update_entity(
                entity_name=name,
                description=description,
                entity_type=entity_type,
                metadata=metadata if metadata else None,
                allow_rename=allow_rename,
                allow_merge=allow_merge,
                workspace=workspace,
            )
            return f"Entity '{name}' updated."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_update_entity._full_doc_ = """Update an existing entity in the RAG knowledge graph.

    Args:
        name: Name of the entity to update.
        updated_name: New name for the entity (use with allow_rename=True).
        entity_type: New type/category for the entity.
        description: New description for the entity.
        properties: New metadata dictionary.
        allow_rename: Allow renaming the entity (default: False).
        allow_merge: Allow merging with existing entity (default: False).

    Returns:
        Success message confirming entity update.
    """

    @register_tool_category("rag")
    @tool
    async def rag_merge_entities(
        source_entities: list[str],
        target_entity_name: str,
    ) -> str:
        """Merge multiple entities into one in the RAG knowledge graph.

        Args:
            source_entities: List of entity names to merge from.
            target_entity_name: Name of the target entity to merge into.

        Returns:
            Success message confirming entity merge.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_merge_entities: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            await client.merge_entities(
                entities_to_change=source_entities,
                entity_to_change_into=target_entity_name,
                workspace=workspace,
            )
            return f"Entities {source_entities} merged into '{target_entity_name}'."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_merge_entities._full_doc_ = """Merge multiple entities into one in the RAG knowledge graph.

    Args:
        source_entities: List of entity names to merge from.
        target_entity_name: Name of the target entity to merge into.

    Returns:
        Success message confirming entity merge.
    """

    @register_tool_category("rag")
    @tool
    async def rag_delete_entity(entity_name: str) -> str:
        """Delete an entity from the RAG knowledge graph.

        Args:
            entity_name: Name of the entity to delete.

        Returns:
            Success message confirming entity deletion.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_delete_entity: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            await client.delete_entity(entity_name=entity_name, workspace=workspace)
            return f"Entity '{entity_name}' deleted."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_delete_entity._full_doc_ = """Delete an entity from the RAG knowledge graph.

    Args:
        entity_name: Name of the entity to delete.

    Returns:
        Success message confirming entity deletion.
    """

    @register_tool_category("rag")
    @tool
    async def rag_delete_relation(
        source: str,
        target: str,
    ) -> str:
        """Delete a relation between entities in the RAG knowledge graph.

        Args:
            source: Name of the source entity.
            target: Name of the target entity.

        Returns:
            Success message confirming relation deletion.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_delete_relation: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            await client.delete_relation(
                source_entity=source,
                target_entity=target,
                workspace=workspace,
            )
            return "Relation deleted."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_delete_relation._full_doc_ = """Delete a relation between entities in the RAG knowledge graph.

    Args:
        source: Name of the source entity.
        target: Name of the target entity.

    Returns:
        Success message confirming relation deletion.
    """

    @register_tool_category("rag")
    @tool
    async def rag_delete_docs(
        doc_ids: list[str],
        delete_file: bool = False,
        delete_llm_cache: bool = False,
    ) -> str:
        """Delete documents by their IDs from the RAG system.

        Args:
            doc_ids: List of document IDs to delete.
            delete_file: Whether to delete the source file (default: False).
            delete_llm_cache: Whether to delete LLM cache entries (default: False).

        Returns:
            Success message with count of deleted documents.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_delete_docs: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            await client.delete_docs(
                doc_ids=doc_ids,
                delete_file=delete_file,
                delete_llm_cache=delete_llm_cache,
                workspace=workspace,
            )
            return f"{len(doc_ids)} documents deleted."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_delete_docs._full_doc_ = """Delete documents by their IDs from the RAG system.

    Args:
        doc_ids: List of document IDs to delete.
        delete_file: Whether to delete the source file (default: False).
        delete_llm_cache: Whether to delete LLM cache entries (default: False).

    Returns:
        Success message with count of deleted documents.
    """

    @register_tool_category("rag")
    @tool
    async def rag_list_docs(
        page: int = 1,
        page_size: int = 50,
        status_filter: str | None = None,
        status_filters: list[str] | None = None,
        sort_field: str = "updated_at",
        sort_direction: str = "desc",
    ) -> str:
        """List documents in the RAG system with pagination.

        Args:
            page: Page number, 1-indexed (default: 1).
            page_size: Number of documents per page (default: 50).
            status_filter: Filter by single document status.
            status_filters: Filter by multiple document statuses.
            sort_field: Field to sort by (default: "updated_at").
            sort_direction: Sort direction - "asc" or "desc" (default: "desc").

        Returns:
            Formatted list of documents with pagination info.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_list_docs: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            result = await client.list_docs(
                page=page,
                page_size=page_size,
                status_filter=status_filter,
                status_filters=status_filters,
                sort_field=sort_field,
                sort_direction=sort_direction,
                workspace=workspace,
            )

            output_parts: list[str] = []
            doc_page = getattr(result, 'page', 1) or 1
            doc_page_size = getattr(result, 'page_size', page_size) or page_size
            doc_total = getattr(result, 'total', 0) or 0
            output_parts.append(f"## Documents (Page {doc_page}/{doc_page_size})")
            output_parts.append(f"Total: {doc_total} documents\n")

            documents = getattr(result, 'documents', []) or []
            if not documents:
                return "No documents found."

            for doc in documents:
                doc_id = doc.get("id", "?")
                doc_status = doc.get("status", "unknown")
                doc_name = doc.get("name", doc.get("file_name", "unnamed"))
                output_parts.append(f"- **{doc_name}** (ID: {doc_id}, Status: {doc_status})")

            return "\n".join(output_parts)
        except RAGError as e:
            return f"RAG error: {e}"

    rag_list_docs._full_doc_ = """List documents in the RAG system with pagination.

    Args:
        page: Page number, 1-indexed (default: 1).
        page_size: Number of documents per page (default: 50).
        status_filter: Filter by single document status (optional).
        status_filters: Filter by multiple document statuses (optional).
        sort_field: Field to sort by (default: "updated_at").
        sort_direction: Sort direction - "asc" or "desc" (default: "desc").

    Returns:
        Formatted list of documents with pagination info.
    """

    @register_tool_category("rag")
    @tool
    async def rag_track_status(track_id: str) -> str:
        """Track the status of an async RAG operation.

        Args:
            track_id: The tracking ID returned from insert operations.

        Returns:
            Formatted status information for the tracked operation.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        workspace = _get_workspace()
        if workspace is None:
            logger.warning(
                "rag_track_status: could not resolve workspace from instance %s project_id. "
                "Operation will use default workspace.",
                current_instance_id,
            )
        try:
            result = await client.track_status(track_id=track_id, workspace=workspace)

            output_parts: list[str] = []
            output_parts.append(f"## Track Status: {getattr(result, 'track_id', track_id)}")
            output_parts.append(f"Status: {getattr(result, 'status', 'unknown')}")

            progress = getattr(result, 'progress', None)
            if progress is not None:
                output_parts.append(f"Progress: {progress * 100:.1f}%")

            message = getattr(result, 'message', '') or ''
            if message:
                output_parts.append(f"Message: {message}")

            return "\n".join(output_parts)
        except RAGError as e:
            return f"RAG error: {e}"

    rag_track_status._full_doc_ = """Track the status of an async RAG operation.

    Args:
        track_id: The tracking ID returned from insert operations.

    Returns:
        Formatted status information for the tracked operation.
    """

    return [
        rag_insert_text,
        rag_insert_texts,
        rag_query,
        rag_query_data,
        rag_search_labels,
        rag_get_graph,
        rag_create_entity,
        rag_get_entity,
        rag_create_relation,
        rag_update_entity,
        rag_merge_entities,
        rag_delete_entity,
        rag_delete_relation,
        rag_delete_docs,
        rag_list_docs,
        rag_track_status,
    ]
