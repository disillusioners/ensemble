"""Async HTTP client for LightRAG API."""

import logging
from typing import Any

import httpx

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
    CreateEntityRequest,
    CreateRelationRequest,
    DeleteDocsRequest,
    DeleteEntityRequest,
    DeleteRelationRequest,
    GraphResponse,
    InsertResponse,
    InsertTextRequest,
    InsertTextsRequest,
    LabelSearchRequest,
    LabelSearchResponse,
    ListDocsResponse,
    MergeEntitiesRequest,
    PipelineStatusResponse,
    QueryDataRequest,
    QueryDataResponse,
    QueryRequest,
    QueryResponse,
    TrackStatusResponse,
    UpdateEntityRequest,
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
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an HTTP request with error handling and retry.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.).
            path: API endpoint path.
            **kwargs: Additional arguments to pass to httpx request.

        Returns:
            Parsed JSON response dictionary.

        Raises:
            RAGNotConfiguredError: If LightRAG is not configured.
            RAGConnectionError: If connection to server fails.
            RAGTimeoutError: If request times out.
            RAGResponseError: If server returns an error response.
        """
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
        description: str = "",
        file_paths: list[str] | None = None,
    ) -> InsertResponse:
        """Insert a single text into the knowledge graph.

        Args:
            text: The text content to insert.
            description: Optional description or metadata for the text.
            file_paths: Optional list of file paths associated with the text.

        Returns:
            InsertResponse with status and optional track_id.
        """
        request = InsertTextRequest(
            text=text,
            description=description,
            file_paths=file_paths,
        )
        data = await self._request("POST", INSERT_TEXT, json=request.to_api_dict())
        return InsertResponse(**data)

    async def insert_texts(self, texts: list[str]) -> InsertResponse:
        """Insert multiple texts into the knowledge graph.

        Args:
            texts: List of text strings to insert.

        Returns:
            InsertResponse with status and optional track_id.
        """
        request = InsertTextsRequest(texts=texts)
        data = await self._request("POST", INSERT_TEXTS, json=request.to_api_dict())
        return InsertResponse(**data)

    # -------------------------------------------------------------------------
    # Querying
    # -------------------------------------------------------------------------

    async def query(
        self,
        query: str,
        mode: str = "hybrid",
        only_need_context: bool = False,
        only_need_prompt: bool = False,
        response_type: str | None = None,
        top_k: int | None = None,
        max_token_for_text_unit: int | None = None,
        max_token_for_global_context: int | None = None,
        max_token_for_local_context: int | None = None,
        hl_keywords: list[str] | None = None,
        ll_keywords: list[str] | None = None,
        stream: bool = False,
        history_turns: int | None = None,
    ) -> QueryResponse:
        """Query the knowledge graph.

        Args:
            query: The query string to search for.
            mode: Query mode (local, global, hybrid, naive, mix).
            only_need_context: Return only context without full response.
            only_need_prompt: Return only the generated prompt.
            response_type: Type of response to generate.
            top_k: Number of top results to return.
            max_token_for_text_unit: Max tokens for text unit retrieval.
            max_token_for_global_context: Max tokens for global context.
            max_token_for_local_context: Max tokens for local context.
            hl_keywords: High-level keywords for query enhancement.
            ll_keywords: Low-level keywords for query enhancement.
            stream: Enable streaming response.
            history_turns: Number of conversation turns to include.

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
            max_token_for_text_unit=max_token_for_text_unit,
            max_token_for_global_context=max_token_for_global_context,
            max_token_for_local_context=max_token_for_local_context,
            hl_keywords=hl_keywords,
            ll_keywords=ll_keywords,
            stream=stream,
            history_turns=history_turns,
        )
        data = await self._request("POST", QUERY, json=request.to_api_dict())
        return QueryResponse(**data)

    async def query_data(
        self,
        query: str,
        mode: str = "hybrid",
        only_need_context: bool = False,
        only_need_prompt: bool = False,
        response_type: str | None = None,
        top_k: int | None = None,
        max_token_for_text_unit: int | None = None,
        max_token_for_global_context: int | None = None,
        max_token_for_local_context: int | None = None,
        hl_keywords: list[str] | None = None,
        ll_keywords: list[str] | None = None,
        stream: bool = False,
        history_turns: int | None = None,
    ) -> QueryDataResponse:
        """Query knowledge graph data (entities and relations).

        Args:
            query: The query string to search for.
            mode: Query mode (local, global, hybrid, naive, mix).
            only_need_context: Return only context without full response.
            only_need_prompt: Return only the generated prompt.
            response_type: Type of response to generate.
            top_k: Number of top results to return.
            max_token_for_text_unit: Max tokens for text unit retrieval.
            max_token_for_global_context: Max tokens for global context.
            max_token_for_local_context: Max tokens for local context.
            hl_keywords: High-level keywords for query enhancement.
            ll_keywords: Low-level keywords for query enhancement.
            stream: Enable streaming response.
            history_turns: Number of conversation turns to include.

        Returns:
            QueryDataResponse with entities and relations.
        """
        request = QueryDataRequest(
            query=query,
            mode=mode,
            only_need_context=only_need_context,
            only_need_prompt=only_need_prompt,
            response_type=response_type,
            top_k=top_k,
            max_token_for_text_unit=max_token_for_text_unit,
            max_token_for_global_context=max_token_for_global_context,
            max_token_for_local_context=max_token_for_local_context,
            hl_keywords=hl_keywords,
            ll_keywords=ll_keywords,
            stream=stream,
            history_turns=history_turns,
        )
        data = await self._request("POST", QUERY_DATA, json=request.to_api_dict())
        return QueryDataResponse(**data.get("data", data))  # unwrap the "data" wrapper

    # -------------------------------------------------------------------------
    # Graph Operations
    # -------------------------------------------------------------------------

    async def search_labels(
        self,
        label: str,
        max_results: int = 10,
    ) -> LabelSearchResponse:
        """Search for labels in the knowledge graph.

        Args:
            label: The label to search for.
            max_results: Maximum number of results to return.

        Returns:
            LabelSearchResponse with matching labels.
        """
        request = LabelSearchRequest(label=label, max_results=max_results)
        data = await self._request("POST", SEARCH_LABELS, json=request.to_api_dict())
        return LabelSearchResponse(**data)

    async def get_graph(
        self,
        label: str | None = None,
        max_depth: int = 3,
        max_nodes: int = 50,
    ) -> GraphResponse:
        """Get the knowledge graph or subgraph.

        Args:
            label: Optional label to filter the graph.
            max_depth: Maximum depth for graph traversal.
            max_nodes: Maximum number of nodes to return.

        Returns:
            GraphResponse with nodes and edges.
        """
        params: dict[str, Any] = {"max_depth": max_depth, "max_nodes": max_nodes}
        if label is not None:
            params["label"] = label
        data = await self._request("GET", GET_GRAPH, params=params)
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
    ) -> dict[str, Any]:
        """Create a new entity in the knowledge graph.

        Args:
            entity_name: Name of the entity to create.
            description: Optional description of the entity.
            entity_type: Type/category of the entity.
            metadata: Optional metadata dictionary.

        Returns:
            API response as dictionary.
        """
        request = CreateEntityRequest(
            entity_name=entity_name,
            description=description,
            entity_type=entity_type,
            metadata=metadata,
        )
        return await self._request("POST", CREATE_ENTITY, json=request.to_api_dict())

    async def update_entity(
        self,
        entity_name: str,
        description: str | None = None,
        entity_type: str | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Update an existing entity.

        Args:
            entity_name: Name of the entity to update.
            description: New description for the entity.
            entity_type: New type for the entity.
            metadata: New metadata for the entity.

        Returns:
            API response as dictionary.
        """
        request = UpdateEntityRequest(
            entity_name=entity_name,
            description=description,
            entity_type=entity_type,
            metadata=metadata,
        )
        return await self._request("POST", UPDATE_ENTITY, json=request.to_api_dict())

    async def merge_entities(
        self,
        source_entities: list[str],
        target_entity: str,
        description: str | None = None,
        entity_type: str | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Merge multiple entities into one.

        Args:
            source_entities: List of entity names to merge from.
            target_entity: Name of the target entity to merge into.
            description: Optional new description for the merged entity.
            entity_type: Optional new type for the merged entity.
            metadata: Optional new metadata for the merged entity.

        Returns:
            API response as dictionary.
        """
        request = MergeEntitiesRequest(
            source_entities=source_entities,
            target_entity=target_entity,
            description=description,
            entity_type=entity_type,
            metadata=metadata,
        )
        return await self._request("POST", MERGE_ENTITIES, json=request.to_api_dict())

    async def delete_entity(self, entity_name: str) -> dict[str, Any]:
        """Delete an entity from the knowledge graph.

        Args:
            entity_name: Name of the entity to delete.

        Returns:
            API response as dictionary.
        """
        request = DeleteEntityRequest(entity_name=entity_name)
        return await self._request("POST", DELETE_ENTITY, json=request.to_api_dict())

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
    ) -> dict[str, Any]:
        """Create a relation between entities.

        Args:
            source_entity: Name of the source entity.
            target_entity: Name of the target entity.
            description: Optional description of the relation.
            relation_type: Type of the relation.
            metadata: Optional metadata dictionary.
            weight: Optional weight value for the relation.

        Returns:
            API response as dictionary.
        """
        request = CreateRelationRequest(
            source_entity=source_entity,
            target_entity=target_entity,
            description=description,
            relation_type=relation_type,
            metadata=metadata,
            weight=weight,
        )
        return await self._request("POST", CREATE_RELATION, json=request.to_api_dict())

    async def delete_relation(
        self,
        source_entity: str,
        target_entity: str,
        relation_type: str | None = None,
    ) -> dict[str, Any]:
        """Delete a relation between entities.

        Args:
            source_entity: Name of the source entity.
            target_entity: Name of the target entity.
            relation_type: Type of relation to delete (optional).

        Returns:
            API response as dictionary.
        """
        request = DeleteRelationRequest(
            source_entity=source_entity,
            target_entity=target_entity,
            relation_type=relation_type,
        )
        return await self._request("POST", DELETE_RELATION, json=request.to_api_dict())

    # -------------------------------------------------------------------------
    # Document Operations
    # -------------------------------------------------------------------------

    async def delete_docs(self, doc_ids: list[str]) -> dict[str, Any]:
        """Delete documents by IDs.

        Args:
            doc_ids: List of document IDs to delete.

        Returns:
            API response as dictionary.
        """
        request = DeleteDocsRequest(doc_ids=doc_ids)
        return await self._request("POST", DELETE_DOCS, json=request.to_api_dict())

    async def list_docs(
        self,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
    ) -> ListDocsResponse:
        """List documents with pagination.

        Args:
            page: Page number (1-indexed).
            page_size: Number of documents per page.
            status: Optional filter by document status.

        Returns:
            ListDocsResponse with paginated documents.
        """
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if status is not None:
            params["status"] = status
        data = await self._request("GET", LIST_DOCS, params=params)
        return ListDocsResponse(**data)

    # -------------------------------------------------------------------------
    # Status Operations
    # -------------------------------------------------------------------------

    async def track_status(self, track_id: str) -> TrackStatusResponse:
        """Track the status of an async operation.

        Args:
            track_id: The tracking ID for the operation.

        Returns:
            TrackStatusResponse with current status.
        """
        path = TRACK_STATUS.format(track_id=track_id)
        data = await self._request("GET", path)
        return TrackStatusResponse(**data)

    async def pipeline_status(self) -> PipelineStatusResponse:
        """Get the overall pipeline status.

        Returns:
            PipelineStatusResponse with pipeline status.
        """
        data = await self._request("GET", PIPELINE_STATUS)
        return PipelineStatusResponse(**data)
