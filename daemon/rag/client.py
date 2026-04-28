"""Async HTTP client for LightRAG API."""

import logging
import re
from typing import Any

import httpx


def _sanitize_workspace(workspace: str) -> str:
    """Match LightRAG's workspace sanitization: alphanumeric + underscore only."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', workspace)

from .config import RAGConfig
from .endpoints import (
    CREATE_ENTITY,
    CREATE_RELATION,
    DELETE_DOCS,
    DELETE_ENTITY,
    DELETE_RELATION,
    GET_GRAPH,
    INSERT_TEXT,
    INSERT_TEXTS,
    LIST_DOCS,
    MERGE_ENTITIES,
    PIPELINE_STATUS,
    QUERY,
    QUERY_DATA,
    SEARCH_LABELS,
    TRACK_STATUS,
    UPDATE_ENTITY,
)
from .exceptions import (
    RAGConnectionError,
    RAGNotConfiguredError,
    RAGResponseError,
    RAGTimeoutError,
)
from .schemas import (
    DeleteDocsRequest,
    DeleteEntityRequest,
    DeleteRelationRequest,
    EntityCreateRequest,
    EntityMergeRequest,
    EntityUpdateRequest,
    GraphResponse,
    InsertResponse,
    InsertTextRequest,
    InsertTextsRequest,
    LabelSearchResponse,
    ListDocsResponse,
    PipelineStatusResponse,
    QueryDataRequest,
    QueryDataResponse,
    QueryRequest,
    QueryResponse,
    RelationCreateRequest,
    RelationUpdateRequest,
    TrackStatusResponse,
)

logger = logging.getLogger(__name__)


class AsyncLightRAGClient:
    """Async HTTP client for LightRAG API.

    Provides an async interface for interacting with LightRAG's knowledge graph
    and retrieval capabilities. Supports text insertion, querying, graph operations,
    and document management.

    Args:
        config: RAGConfig instance with connection settings.

    Example:
        ```python
        config = RAGConfig.from_env()
        async with AsyncLightRAGClient(config) as client:
            result = await client.insert_text("Hello, world!")
            response = await client.query("What was inserted?")
        ```
    """

    def __init__(self, config: RAGConfig | None = None) -> None:
        """Initialize the RAG client.

        Args:
            config: RAG configuration. If None, loads from environment.
        """
        self._config = config or RAGConfig.from_env()
        self._client: httpx.AsyncClient | None = None

    @property
    def is_available(self) -> bool:
        """Check if the client is properly configured and ready.

        Returns:
            True if LIGHTRAG_HOST is configured.
        """
        return self._config.is_configured

    def _build_headers(self) -> dict[str, str]:
        """Build default headers for API requests.

        Returns:
            Dictionary of headers including X-API-Key and LIGHTRAG-WORKSPACE.
        """
        headers: dict[str, str] = {
            "LIGHTRAG-WORKSPACE": self._config.workspace,
        }
        if self._config.api_key:
            headers["X-API-Key"] = self._config.api_key
        return headers

    def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create and return the HTTP client.

        Returns:
            Configured httpx.AsyncClient instance.

        Raises:
            RAGNotConfiguredError: If LIGHTRAG_HOST is not set.
        """
        if not self._config.is_configured:
            raise RAGNotConfiguredError()
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=httpx.Timeout(self._config.timeout),
                headers=self._build_headers(),
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        workspace: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an HTTP request with error handling and retry.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.).
            path: API endpoint path.
            workspace: Optional workspace override for this request.
            **kwargs: Additional arguments to pass to httpx request.

        Returns:
            Parsed JSON response dictionary.

        Raises:
            RAGNotConfiguredError: If LightRAG is not configured.
            RAGConnectionError: If connection to server fails.
            RAGTimeoutError: If request times out.
            RAGResponseError: If server returns an error response.
        """
        if workspace is not None:
            # Merge with client's default headers to preserve headers like X-API-Key
            headers = {**self._build_headers(), **kwargs.pop("headers", {})}
            headers["LIGHTRAG-WORKSPACE"] = _sanitize_workspace(workspace)
            kwargs["headers"] = headers

        client = self._ensure_client()

        # First attempt
        try:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as e:
            logger.warning("LightRAG request timed out: %s %s", method, path)
            raise RAGTimeoutError() from e
        except httpx.ConnectError as e:
            logger.warning("LightRAG connection failed, retrying: %s %s", method, path)
            try:
                # Retry once on connection errors
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
            except httpx.ConnectError:
                logger.warning("LightRAG retry failed: %s %s", method, path)
                raise RAGConnectionError() from e
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                error_data = e.response.json()
                detail = error_data.get("detail", str(error_data))
            except Exception:
                detail = e.response.text or str(e)
            logger.warning(
                "LightRAG returned error status %d: %s",
                e.response.status_code,
                detail,
            )
            raise RAGResponseError(
                status_code=e.response.status_code,
                detail=detail,
            ) from e

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncLightRAGClient":
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context manager and close client."""
        await self.close()

    # -------------------------------------------------------------------------
    # Text Insertion
    # -------------------------------------------------------------------------

    async def insert_text(
        self,
        text: str,
        file_source: str | None = None,
        workspace: str | None = None,
    ) -> InsertResponse:
        """Insert a single text into the knowledge graph.

        Args:
            text: The text content to insert.
            file_source: Optional file source path for the text.
            workspace: Optional workspace override.

        Returns:
            InsertResponse with status and track_id.
        """
        request = InsertTextRequest(
            text=text,
            file_source=file_source,
        )
        data = await self._request("POST", INSERT_TEXT, json=request.to_api_dict(), workspace=workspace)
        return InsertResponse(**data)

    async def insert_texts(
        self,
        texts: list[str],
        file_sources: list[str] | None = None,
        workspace: str | None = None,
    ) -> InsertResponse:
        """Insert multiple texts into the knowledge graph.

        Args:
            texts: List of text strings to insert.
            file_sources: Optional list of file sources corresponding to texts.
            workspace: Optional workspace override.

        Returns:
            InsertResponse with status and track_id.
        """
        request = InsertTextsRequest(
            texts=texts,
            file_sources=file_sources,
        )
        data = await self._request("POST", INSERT_TEXTS, json=request.to_api_dict(), workspace=workspace)
        return InsertResponse(**data)

    # -------------------------------------------------------------------------
    # Querying
    # -------------------------------------------------------------------------

    async def query(
        self,
        query: str,
        mode: str = "mix",
        only_need_context: bool = False,
        only_need_prompt: bool = False,
        response_type: str | None = None,
        top_k: int | None = None,
        hl_keywords: list[str] | None = None,
        ll_keywords: list[str] | None = None,
        stream: bool | None = None,
        chunk_top_k: int | None = None,
        max_entity_tokens: int | None = None,
        max_relation_tokens: int | None = None,
        max_total_tokens: int | None = None,
        conversation_history: list[dict] | None = None,
        user_prompt: str | None = None,
        enable_rerank: bool | None = None,
        include_references: bool | None = None,
        include_chunk_content: bool | None = None,
        workspace: str | None = None,
    ) -> QueryResponse:
        """Query the knowledge graph.

        Args:
            query: The query string to search for.
            mode: Query mode (local, global, hybrid, naive, mix).
            only_need_context: Return only context without full response.
            only_need_prompt: Return only the generated prompt.
            response_type: Type of response to generate.
            top_k: Number of top results to return.
            hl_keywords: High-level keywords for query enhancement.
            ll_keywords: Low-level keywords for query enhancement.
            stream: Enable streaming response.
            chunk_top_k: Number of chunks to retrieve.
            max_entity_tokens: Max tokens for entity retrieval.
            max_relation_tokens: Max tokens for relation retrieval.
            max_total_tokens: Max total tokens for the response.
            conversation_history: List of conversation history turns.
            user_prompt: User prompt for the query.
            enable_rerank: Enable reranking of results.
            include_references: Include references in the response.
            include_chunk_content: Include chunk content in the response.
            workspace: Optional workspace override.

        Returns:
            QueryResponse with generated response text.
        """
        request = QueryRequest(
            query=query,
            mode=mode,
            only_need_context=only_need_context,
            only_need_prompt=only_need_prompt,
            response_type=response_type,
            top_k=top_k,
            hl_keywords=hl_keywords,
            ll_keywords=ll_keywords,
            stream=stream,
            chunk_top_k=chunk_top_k,
            max_entity_tokens=max_entity_tokens,
            max_relation_tokens=max_relation_tokens,
            max_total_tokens=max_total_tokens,
            conversation_history=conversation_history,
            user_prompt=user_prompt,
            enable_rerank=enable_rerank,
            include_references=include_references,
            include_chunk_content=include_chunk_content,
        )
        data = await self._request("POST", QUERY, json=request.to_api_dict(), workspace=workspace)
        return QueryResponse(**data)

    async def query_data(
        self,
        query: str,
        mode: str = "mix",
        only_need_context: bool = False,
        only_need_prompt: bool = False,
        response_type: str | None = None,
        top_k: int | None = None,
        hl_keywords: list[str] | None = None,
        ll_keywords: list[str] | None = None,
        stream: bool | None = None,
        chunk_top_k: int | None = None,
        max_entity_tokens: int | None = None,
        max_relation_tokens: int | None = None,
        max_total_tokens: int | None = None,
        conversation_history: list[dict] | None = None,
        user_prompt: str | None = None,
        enable_rerank: bool | None = None,
        include_references: bool | None = None,
        include_chunk_content: bool | None = None,
        workspace: str | None = None,
    ) -> QueryDataResponse:
        """Query knowledge graph data (entities and relations).

        Args:
            query: The query string to search for.
            mode: Query mode (local, global, hybrid, naive, mix).
            only_need_context: Return only context without full response.
            only_need_prompt: Return only the generated prompt.
            response_type: Type of response to generate.
            top_k: Number of top results to return.
            hl_keywords: High-level keywords for query enhancement.
            ll_keywords: Low-level keywords for query enhancement.
            stream: Enable streaming response.
            chunk_top_k: Number of chunks to retrieve.
            max_entity_tokens: Max tokens for entity retrieval.
            max_relation_tokens: Max tokens for relation retrieval.
            max_total_tokens: Max total tokens for the response.
            conversation_history: List of conversation history turns.
            user_prompt: User prompt for the query.
            enable_rerank: Enable reranking of results.
            include_references: Include references in the response.
            include_chunk_content: Include chunk content in the response.
            workspace: Optional workspace override.

        Returns:
            QueryDataResponse with status, message, and data.
        """
        request = QueryDataRequest(
            query=query,
            mode=mode,
            only_need_context=only_need_context,
            only_need_prompt=only_need_prompt,
            response_type=response_type,
            top_k=top_k,
            hl_keywords=hl_keywords,
            ll_keywords=ll_keywords,
            stream=stream,
            chunk_top_k=chunk_top_k,
            max_entity_tokens=max_entity_tokens,
            max_relation_tokens=max_relation_tokens,
            max_total_tokens=max_total_tokens,
            conversation_history=conversation_history,
            user_prompt=user_prompt,
            enable_rerank=enable_rerank,
            include_references=include_references,
            include_chunk_content=include_chunk_content,
        )
        data = await self._request("POST", QUERY_DATA, json=request.to_api_dict(), workspace=workspace)
        return QueryDataResponse(**data)

    # -------------------------------------------------------------------------
    # Graph Operations
    # -------------------------------------------------------------------------

    async def search_labels(
        self,
        q: str,
        limit: int = 50,
        workspace: str | None = None,
    ) -> LabelSearchResponse:
        """Search for labels in the knowledge graph.

        Args:
            q: The label query to search for.
            limit: Maximum number of results to return.
            workspace: Optional workspace override.

        Returns:
            LabelSearchResponse with matching labels.
        """
        params = {"q": q, "limit": limit}
        data = await self._request("GET", SEARCH_LABELS, params=params, workspace=workspace)
        # LightRAG API returns a plain list of labels, not a dict
        if isinstance(data, list):
            return LabelSearchResponse(labels=data)
        return LabelSearchResponse(**data)

    async def get_graph(
        self,
        label: str | None = None,
        max_depth: int = 3,
        max_nodes: int = 50,
        workspace: str | None = None,
    ) -> GraphResponse:
        """Get the knowledge graph or subgraph.

        Args:
            label: Optional label to filter the graph.
            max_depth: Maximum depth for graph traversal.
            max_nodes: Maximum number of nodes to return.
            workspace: Optional workspace override.

        Returns:
            GraphResponse with nodes and edges.
        """
        params: dict[str, Any] = {"max_depth": max_depth, "max_nodes": max_nodes}
        if label is not None:
            params["label"] = label
        data = await self._request("GET", GET_GRAPH, params=params, workspace=workspace)
        return GraphResponse(**data)

    # -------------------------------------------------------------------------
    # Entity Operations
    # -------------------------------------------------------------------------

    async def create_entity(
        self,
        entity_name: str,
        description: str = "",
        entity_type: str = "UNKNOWN",
        metadata: dict | None = None,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Create a new entity in the knowledge graph.

        Args:
            entity_name: Name of the entity to create.
            description: Optional description of the entity.
            entity_type: Type/category of the entity.
            metadata: Optional metadata dictionary.
            workspace: Optional workspace override.

        Returns:
            API response as dictionary.
        """
        entity_data: dict[str, Any] = {}
        if description:
            entity_data["description"] = description
        if entity_type:
            entity_data["entity_type"] = entity_type
        if metadata:
            entity_data.update(metadata)

        request = EntityCreateRequest(
            entity_name=entity_name,
            entity_data=entity_data,
        )
        return await self._request("POST", CREATE_ENTITY, json=request.to_api_dict(), workspace=workspace)

    async def update_entity(
        self,
        entity_name: str,
        description: str | None = None,
        entity_type: str | None = None,
        metadata: dict | None = None,
        allow_rename: bool = False,
        allow_merge: bool = False,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing entity.

        Args:
            entity_name: Name of the entity to update.
            description: New description for the entity.
            entity_type: New type for the entity.
            metadata: New metadata for the entity.
            allow_rename: Allow renaming the entity.
            allow_merge: Allow merging with existing entity.
            workspace: Optional workspace override.

        Returns:
            API response as dictionary.
        """
        updated_data: dict[str, Any] = {}
        if description is not None:
            updated_data["description"] = description
        if entity_type is not None:
            updated_data["entity_type"] = entity_type
        if metadata is not None:
            updated_data.update(metadata)

        request = EntityUpdateRequest(
            entity_name=entity_name,
            updated_data=updated_data,
            allow_rename=allow_rename,
            allow_merge=allow_merge,
        )
        return await self._request("POST", UPDATE_ENTITY, json=request.to_api_dict(), workspace=workspace)

    async def merge_entities(
        self,
        entities_to_change: list[str],
        entity_to_change_into: str,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Merge multiple entities into one.

        Args:
            entities_to_change: List of entity names to merge from.
            entity_to_change_into: Name of the target entity to merge into.
            workspace: Optional workspace override.

        Returns:
            API response as dictionary.
        """
        request = EntityMergeRequest(
            entities_to_change=entities_to_change,
            entity_to_change_into=entity_to_change_into,
        )
        return await self._request("POST", MERGE_ENTITIES, json=request.to_api_dict(), workspace=workspace)

    async def delete_entity(
        self,
        entity_name: str,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Delete an entity from the knowledge graph.

        Args:
            entity_name: Name of the entity to delete.
            workspace: Optional workspace override.

        Returns:
            API response as dictionary.
        """
        request = DeleteEntityRequest(entity_name=entity_name)
        return await self._request("DELETE", DELETE_ENTITY, json=request.to_api_dict(), workspace=workspace)

    # -------------------------------------------------------------------------
    # Relation Operations
    # -------------------------------------------------------------------------

    async def create_relation(
        self,
        source_entity: str,
        target_entity: str,
        description: str = "",
        relation_type: str = "RELATED_TO",
        metadata: dict | None = None,
        weight: float | None = None,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Create a relation between entities.

        Args:
            source_entity: Name of the source entity.
            target_entity: Name of the target entity.
            description: Optional description of the relation.
            relation_type: Type of the relation.
            metadata: Optional metadata dictionary.
            weight: Optional weight value for the relation.
            workspace: Optional workspace override.

        Returns:
            API response as dictionary.
        """
        relation_data: dict[str, Any] = {}
        if description:
            relation_data["description"] = description
        if relation_type:
            relation_data["relation_type"] = relation_type
        if metadata:
            relation_data.update(metadata)
        if weight is not None:
            relation_data["weight"] = weight

        request = RelationCreateRequest(
            source_entity=source_entity,
            target_entity=target_entity,
            relation_data=relation_data,
        )
        return await self._request("POST", CREATE_RELATION, json=request.to_api_dict(), workspace=workspace)

    async def update_relation(
        self,
        source_id: str,
        target_id: str,
        description: str | None = None,
        relation_type: str | None = None,
        metadata: dict | None = None,
        weight: float | None = None,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing relation.

        Args:
            source_id: ID of the source entity.
            target_id: ID of the target entity.
            description: New description for the relation.
            relation_type: New type for the relation.
            metadata: New metadata for the relation.
            weight: New weight for the relation.
            workspace: Optional workspace override.

        Returns:
            API response as dictionary.
        """
        updated_data: dict[str, Any] = {}
        if description is not None:
            updated_data["description"] = description
        if relation_type is not None:
            updated_data["relation_type"] = relation_type
        if metadata is not None:
            updated_data.update(metadata)
        if weight is not None:
            updated_data["weight"] = weight

        request = RelationUpdateRequest(
            source_id=source_id,
            target_id=target_id,
            updated_data=updated_data,
        )
        return await self._request("POST", "/graph/relation/edit", json=request.to_api_dict(), workspace=workspace)

    async def delete_relation(
        self,
        source_entity: str,
        target_entity: str,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Delete a relation between entities.

        Args:
            source_entity: Name of the source entity.
            target_entity: Name of the target entity.
            workspace: Optional workspace override.

        Returns:
            API response as dictionary.
        """
        request = DeleteRelationRequest(
            source_entity=source_entity,
            target_entity=target_entity,
        )
        return await self._request("DELETE", DELETE_RELATION, json=request.to_api_dict(), workspace=workspace)

    # -------------------------------------------------------------------------
    # Document Operations
    # -------------------------------------------------------------------------

    async def delete_docs(
        self,
        doc_ids: list[str],
        delete_file: bool = False,
        delete_llm_cache: bool = False,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Delete documents by IDs.

        Args:
            doc_ids: List of document IDs to delete.
            delete_file: Whether to delete the source file.
            delete_llm_cache: Whether to delete LLM cache entries.
            workspace: Optional workspace override.

        Returns:
            API response as dictionary.
        """
        request = DeleteDocsRequest(
            doc_ids=doc_ids,
            delete_file=delete_file,
            delete_llm_cache=delete_llm_cache,
        )
        return await self._request("DELETE", DELETE_DOCS, json=request.to_api_dict(), workspace=workspace)

    async def list_docs(
        self,
        page: int = 1,
        page_size: int = 50,
        status_filter: str | None = None,
        status_filters: list[str] | None = None,
        sort_field: str = "updated_at",
        sort_direction: str = "desc",
        workspace: str | None = None,
    ) -> ListDocsResponse:
        """List documents with pagination.

        Args:
            page: Page number (1-indexed).
            page_size: Number of documents per page.
            status_filter: Filter by single status.
            status_filters: Filter by multiple statuses.
            sort_field: Field to sort by.
            sort_direction: Sort direction (asc/desc).
            workspace: Optional workspace override.

        Returns:
            ListDocsResponse with paginated documents.
        """
        body: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "sort_field": sort_field,
            "sort_direction": sort_direction,
        }
        if status_filter is not None:
            body["status_filter"] = status_filter
        if status_filters is not None:
            body["status_filters"] = status_filters

        data = await self._request("POST", LIST_DOCS, json=body, workspace=workspace)
        return ListDocsResponse(**data)

    # -------------------------------------------------------------------------
    # Status Operations
    # -------------------------------------------------------------------------

    async def track_status(
        self,
        track_id: str,
        workspace: str | None = None,
    ) -> TrackStatusResponse:
        """Track the status of an async operation.

        Args:
            track_id: The tracking ID for the operation.
            workspace: Optional workspace override.

        Returns:
            TrackStatusResponse with documents, total_count, and status_summary.
        """
        path = TRACK_STATUS.format(track_id=track_id)
        data = await self._request("GET", path, workspace=workspace)
        return TrackStatusResponse(**data)

    async def pipeline_status(
        self,
        workspace: str | None = None,
    ) -> PipelineStatusResponse:
        """Get the overall pipeline status.

        Args:
            workspace: Optional workspace override.

        Returns:
            PipelineStatusResponse with pipeline status.
        """
        data = await self._request("GET", PIPELINE_STATUS, workspace=workspace)
        return PipelineStatusResponse(**data)
