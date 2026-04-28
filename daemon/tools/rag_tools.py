"""RAG knowledge management tools for interacting with LightRAG."""

import logging
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


def create_rag_tools(
    manager: TYPE_CHECKING.ANY,  # InstanceManager, avoid circular import
    current_instance_id: str,
) -> list:
    """Create all RAG tools with proper error handling.

    Args:
        manager: The InstanceManager instance (unused but part of factory signature).
        current_instance_id: The current instance ID (unused but part of factory signature).

    Returns:
        List of RAG tool functions.
    """

    @register_tool_category("rag")
    @tool
    async def rag_insert_text(
        text: str,
        description: str = "",
        file_paths: list[str] | None = None,
    ) -> str:
        """Insert a single text into the RAG knowledge graph.

        Args:
            text: The text content to insert.
            description: Optional description or metadata for the text.
            file_paths: Optional list of file paths associated with the text.

        Returns:
            Success message with track ID for tracking async operations.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            result = await client.insert_text(
                text=text,
                description=description,
                file_paths=file_paths,
            )
            return f"Text inserted. Track ID: {getattr(result, 'track_id', '')}"
        except RAGError as e:
            return f"RAG error: {e}"

    rag_insert_text._full_doc_ = """Insert a single text into the RAG knowledge graph.

    Args:
        text: The text content to insert.
        description: Optional description or metadata for the text.
        file_paths: Optional list of file paths associated with the text.

    Returns:
        Success message with track ID for tracking async operations.
    """

    @register_tool_category("rag")
    @tool
    async def rag_insert_texts(texts: list[str]) -> str:
        """Insert multiple texts into the RAG knowledge graph.

        Args:
            texts: List of text strings to insert.

        Returns:
            Success message with track ID for tracking async operations.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            result = await client.insert_texts(texts=texts)
            return f"{len(texts)} texts inserted. Track ID: {getattr(result, 'track_id', '')}"
        except RAGError as e:
            return f"RAG error: {e}"

    rag_insert_texts._full_doc_ = """Insert multiple texts into the RAG knowledge graph.

    Args:
        texts: List of text strings to insert.

    Returns:
        Success message with track ID for tracking async operations.
    """

    @register_tool_category("rag")
    @tool
    async def rag_query(
        query: str,
        mode: str = "hybrid",
    ) -> str:
        """Query the RAG knowledge graph and get a generated response.

        Args:
            query: The query string to search for.
            mode: Query mode - one of local, global, hybrid, naive, mix (default: hybrid).

        Returns:
            Generated response text from the knowledge graph.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            result = await client.query(query=query, mode=mode)
            return getattr(result, 'response', '')
        except RAGError as e:
            return f"RAG error: {e}"

    rag_query._full_doc_ = """Query the RAG knowledge graph and get a generated response.

    Args:
        query: The query string to search for.
        mode: Query mode - one of local, global, hybrid, naive, mix (default: hybrid).

    Returns:
        Generated response text from the knowledge graph.
    """

    @register_tool_category("rag")
    @tool
    async def rag_query_data(
        query: str,
        mode: str = "hybrid",
    ) -> str:
        """Query the RAG knowledge graph and get structured data (entities and relations).

        Args:
            query: The query string to search for.
            mode: Query mode - one of local, global, hybrid, naive, mix (default: hybrid).

        Returns:
            Formatted string containing entities and relations from the query.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            result = await client.query_data(query=query, mode=mode)

            output_parts: list[str] = []

            entities = getattr(result, 'entities', []) or []
            if entities:
                output_parts.append("## Entities\n")
                for entity in entities:
                    name = entity.get("name", "Unknown")
                    entity_type = entity.get("type", "UNKNOWN")
                    desc = entity.get("description", "")
                    output_parts.append(f"- **{name}** ({entity_type}): {desc}")

            relationships = getattr(result, 'relationships', []) or []
            if relationships:
                output_parts.append("\n## Relationships\n")
                for relationship in relationships:
                    source = relationship.get("source", "?")
                    target = relationship.get("target", "?")
                    rel_type = relationship.get("type", "RELATED_TO")
                    desc = relationship.get("description", "")
                    output_parts.append(f"- {source} -[{rel_type}]-> {target}: {desc}")

            if not output_parts:
                return "No entities or relations found for this query."

            return "\n".join(output_parts)
        except RAGError as e:
            return f"RAG error: {e}"

    rag_query_data._full_doc_ = """Query the RAG knowledge graph and get structured data.

    Args:
        query: The query string to search for.
        mode: Query mode - one of local, global, hybrid, naive, mix (default: hybrid).

    Returns:
        Formatted string containing entities and relations from the query.
    """

    @register_tool_category("rag")
    @tool
    async def rag_search_labels(
        label: str,
        max_results: int = 10,
    ) -> str:
        """Search for labels in the RAG knowledge graph.

        Args:
            label: The label to search for.
            max_results: Maximum number of results to return (default: 10).

        Returns:
            Formatted list of matching labels.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            result = await client.search_labels(label=label, max_results=max_results)
            labels = getattr(result, 'labels', []) or []
            if not labels:
                return f"No labels found matching: {label}"
            return "Matching labels:\n" + "\n".join(f"- {lbl}" for lbl in labels)
        except RAGError as e:
            return f"RAG error: {e}"

    rag_search_labels._full_doc_ = """Search for labels in the RAG knowledge graph.

    Args:
        label: The label to search for.
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
        try:
            result = await client.get_graph(
                label=label,
                max_depth=max_depth,
                max_nodes=max_nodes,
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
        try:
            await client.create_entity(
                entity_name=name,
                entity_type=entity_type,
                description=description,
                metadata=properties,
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
        try:
            await client.create_relation(
                source_entity=source,
                target_entity=target,
                relation_type=relation_type,
                description=description,
                metadata=properties,
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
    ) -> str:
        """Update an existing entity in the RAG knowledge graph.

        Args:
            name: Name of the entity to update.
            updated_name: New name for the entity.
            entity_type: New type/category for the entity.
            description: New description for the entity.
            properties: New metadata dictionary.

        Returns:
            Success message confirming entity update.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            await client.update_entity(
                entity_name=name,
                entity_type=entity_type,
                description=description,
                metadata=properties,
            )
            return f"Entity '{name}' updated."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_update_entity._full_doc_ = """Update an existing entity in the RAG knowledge graph.

    Args:
        name: Name of the entity to update.
        updated_name: New name for the entity.
        entity_type: New type/category for the entity.
        description: New description for the entity.
        properties: New metadata dictionary.

    Returns:
        Success message confirming entity update.
    """

    @register_tool_category("rag")
    @tool
    async def rag_merge_entities(
        source: str,
        target: str,
        target_entity_name: str,
        entity_type: str | None = None,
        description: str | None = None,
        properties: dict | None = None,
    ) -> str:
        """Merge multiple entities into one in the RAG knowledge graph.

        Args:
            source: Name of the source entity to merge from.
            target: Another entity name to merge from.
            target_entity_name: Name of the target entity to merge into.
            entity_type: Optional new type for the merged entity.
            description: Optional new description for the merged entity.
            properties: Optional new metadata dictionary.

        Returns:
            Success message confirming entity merge.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            await client.merge_entities(
                source_entities=[source, target],
                target_entity=target_entity_name,
                entity_type=entity_type,
                description=description,
                metadata=properties,
            )
            return f"Entities merged into '{target_entity_name}'."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_merge_entities._full_doc_ = """Merge multiple entities into one in the RAG knowledge graph.

    Args:
        source: Name of the source entity to merge from.
        target: Another entity name to merge from.
        target_entity_name: Name of the target entity to merge into.
        entity_type: Optional new type for the merged entity.
        description: Optional new description for the merged entity.
        properties: Optional new metadata dictionary.

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
        try:
            await client.delete_entity(entity_name=entity_name)
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
        relation: str | None = None,
    ) -> str:
        """Delete a relation between entities in the RAG knowledge graph.

        Args:
            source: Name of the source entity.
            target: Name of the target entity.
            relation: Optional relation type to delete (deletes all if not specified).

        Returns:
            Success message confirming relation deletion.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            await client.delete_relation(
                source_entity=source,
                target_entity=target,
                relation_type=relation,
            )
            return "Relation deleted."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_delete_relation._full_doc_ = """Delete a relation between entities in the RAG knowledge graph.

    Args:
        source: Name of the source entity.
        target: Name of the target entity.
        relation: Optional relation type to delete (deletes all if not specified).

    Returns:
        Success message confirming relation deletion.
    """

    @register_tool_category("rag")
    @tool
    async def rag_delete_docs(doc_ids: list[str]) -> str:
        """Delete documents by their IDs from the RAG system.

        Args:
            doc_ids: List of document IDs to delete.

        Returns:
            Success message with count of deleted documents.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            await client.delete_docs(doc_ids=doc_ids)
            return f"{len(doc_ids)} documents deleted."
        except RAGError as e:
            return f"RAG error: {e}"

    rag_delete_docs._full_doc_ = """Delete documents by their IDs from the RAG system.

    Args:
        doc_ids: List of document IDs to delete.

    Returns:
        Success message with count of deleted documents.
    """

    @register_tool_category("rag")
    @tool
    async def rag_list_docs(
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
    ) -> str:
        """List documents in the RAG system with pagination.

        Args:
            page: Page number, 1-indexed (default: 1).
            page_size: Number of documents per page (default: 50).
            status: Optional filter by document status.

        Returns:
            Formatted list of documents with pagination info.
        """
        if not is_rag_enabled():
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        client = _get_rag_client()
        try:
            result = await client.list_docs(
                page=page,
                page_size=page_size,
                status=status,
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
        status: Optional filter by document status.

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
        try:
            result = await client.track_status(track_id=track_id)

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
        rag_create_relation,
        rag_update_entity,
        rag_merge_entities,
        rag_delete_entity,
        rag_delete_relation,
        rag_delete_docs,
        rag_list_docs,
        rag_track_status,
    ]
