"""Pydantic schemas for LightRAG REST API.

This module defines request and response models for interacting with the LightRAG
knowledge graph and retrieval system via its HTTP API. All models use Pydantic v2
style with proper type hints, defaults, and documentation.

Reference: LightRAG REST API endpoints for text insertion, querying, graph
operations, and document management.
"""

from typing import Any

from pydantic import BaseModel, Field


# =============================================================================
# Request Models
# =============================================================================


class InsertTextRequest(BaseModel):
    """Request model for inserting a single text into the knowledge graph.

    Attributes:
        text: The text content to insert.
        file_source: Optional file source/path for the text.
    """

    text: str = Field(..., description="Text content to insert into the knowledge graph")
    file_source: str | None = Field(default=None, description="Optional file source path")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class InsertTextsRequest(BaseModel):
    """Request model for inserting multiple texts into the knowledge graph.

    Attributes:
        texts: List of text strings to insert.
        file_sources: Optional list of file sources corresponding to texts.
    """

    texts: list[str] = Field(..., description="List of text strings to insert")
    file_sources: list[str] | None = Field(default=None, description="Optional file sources")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class QueryRequest(BaseModel):
    """Request model for querying the knowledge graph.

    Attributes:
        query: The query string to search for.
        mode: Query mode - one of local, global, hybrid, naive, mix.
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
    """

    query: str = Field(..., description="Query string to search for")
    mode: str = Field(
        default="mix",
        description="Query mode",
    )
    only_need_context: bool = Field(default=False, description="Return only context")
    only_need_prompt: bool = Field(default=False, description="Return only the prompt")
    response_type: str | None = Field(default=None, description="Response type")
    top_k: int | None = Field(default=None, description="Number of top results")
    hl_keywords: list[str] | None = Field(default=None, description="High-level keywords")
    ll_keywords: list[str] | None = Field(default=None, description="Low-level keywords")
    stream: bool | None = Field(default=None, description="Enable streaming response")
    chunk_top_k: int | None = Field(default=None, description="Number of chunks to retrieve")
    max_entity_tokens: int | None = Field(default=None, description="Max tokens for entities")
    max_relation_tokens: int | None = Field(default=None, description="Max tokens for relations")
    max_total_tokens: int | None = Field(default=None, description="Max total tokens")
    conversation_history: list[dict] | None = Field(default=None, description="Conversation history")
    user_prompt: str | None = Field(default=None, description="User prompt")
    enable_rerank: bool | None = Field(default=None, description="Enable reranking")
    include_references: bool | None = Field(default=None, description="Include references")
    include_chunk_content: bool | None = Field(default=None, description="Include chunk content")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


# Alias for QueryDataRequest - both endpoints use the same request model
QueryDataRequest = QueryRequest
"""Request model for querying knowledge graph data - alias for QueryRequest."""


class LabelSearchRequest(BaseModel):
    """Request model for searching by label (GET with query params).

    Attributes:
        q: The label query to search for.
        limit: Maximum number of results to return.
    """

    q: str = Field(..., description="Label query to search for")
    limit: int = Field(default=50, description="Maximum number of results")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class EntityCreateRequest(BaseModel):
    """Request model for creating a new entity in the knowledge graph.

    Attributes:
        entity_name: Name of the entity to create.
        entity_data: Dictionary containing entity data (description, entity_type, metadata, etc.).
    """

    entity_name: str = Field(..., description="Name of the entity")
    entity_data: dict = Field(default_factory=dict, description="Entity data dictionary")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class RelationCreateRequest(BaseModel):
    """Request model for creating a relation between entities.

    Attributes:
        source_entity: Name of the source entity.
        target_entity: Name of the target entity.
        relation_data: Dictionary containing relation data (description, relation_type, metadata, weight, etc.).
    """

    source_entity: str = Field(..., description="Source entity name")
    target_entity: str = Field(..., description="Target entity name")
    relation_data: dict = Field(default_factory=dict, description="Relation data dictionary")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class EntityUpdateRequest(BaseModel):
    """Request model for updating an existing entity.

    Attributes:
        entity_name: Name of the entity to update.
        updated_data: Dictionary containing updated entity data.
        allow_rename: Allow renaming the entity.
        allow_merge: Allow merging with existing entity.
    """

    entity_name: str = Field(..., description="Name of the entity to update")
    updated_data: dict = Field(default_factory=dict, description="Updated entity data")
    allow_rename: bool = Field(default=False, description="Allow renaming the entity")
    allow_merge: bool = Field(default=False, description="Allow merging with existing entity")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class EntityMergeRequest(BaseModel):
    """Request model for merging multiple entities into one.

    Attributes:
        entities_to_change: List of entity names to merge from.
        entity_to_change_into: Name of the target entity to merge into.
    """

    entities_to_change: list[str] = Field(..., description="Entities to merge")
    entity_to_change_into: str = Field(..., description="Target entity name")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class DeleteDocsRequest(BaseModel):
    """Request model for deleting documents by IDs.

    Attributes:
        doc_ids: List of document IDs to delete.
        delete_file: Whether to delete the source file.
        delete_llm_cache: Whether to delete LLM cache entries.
    """

    doc_ids: list[str] = Field(..., description="Document IDs to delete")
    delete_file: bool = Field(default=False, description="Delete the source file")
    delete_llm_cache: bool = Field(default=False, description="Delete LLM cache entries")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class DeleteEntityRequest(BaseModel):
    """Request model for deleting an entity.

    Attributes:
        entity_name: Name of the entity to delete.
    """

    entity_name: str = Field(..., description="Name of the entity to delete")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class DeleteRelationRequest(BaseModel):
    """Request model for deleting a relation.

    Attributes:
        source_entity: Name of the source entity.
        target_entity: Name of the target entity.
    """

    source_entity: str = Field(..., description="Source entity name")
    target_entity: str = Field(..., description="Target entity name")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class RelationUpdateRequest(BaseModel):
    """Request model for updating an existing relation.

    Attributes:
        source_id: ID of the source entity.
        target_id: ID of the target entity.
        updated_data: Dictionary containing updated relation data.
    """

    source_id: str = Field(..., description="Source entity ID")
    target_id: str = Field(..., description="Target entity ID")
    updated_data: dict = Field(default_factory=dict, description="Updated relation data")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class DocumentsRequest(BaseModel):
    """Request model for listing documents with pagination.

    Attributes:
        page: Page number (1-indexed).
        page_size: Number of documents per page.
        status_filter: Filter by single status.
        status_filters: Filter by multiple statuses.
        sort_field: Field to sort by.
        sort_direction: Sort direction (asc/desc).
    """

    page: int = Field(default=1, description="Page number (1-indexed)")
    page_size: int = Field(default=50, description="Documents per page")
    status_filter: str | None = Field(default=None, description="Filter by status")
    status_filters: list[str] | None = Field(default=None, description="Filter by multiple statuses")
    sort_field: str = Field(default="updated_at", description="Field to sort by")
    sort_direction: str = Field(default="desc", description="Sort direction")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


# =============================================================================
# Response Models
# =============================================================================


class InsertResponse(BaseModel):
    """Response model for text insertion operations.

    Attributes:
        status: Status of the insertion.
        message: Response message from the server.
        track_id: Tracking ID for async operations.
    """

    status: str = Field(..., description="Insertion status")
    message: str = Field(..., description="Response message")
    track_id: str = Field(..., description="Tracking ID for async operations")


class QueryResponse(BaseModel):
    """Response model for query operations.

    Attributes:
        response: The generated response text.
        references: Optional list of reference dictionaries.
    """

    response: str = Field(..., description="Generated response text")
    references: list[dict] | None = Field(default=None, description="Response references")


class QueryDataResponse(BaseModel):
    """Response model for data query operations (entities and relationships).

    Attributes:
        status: Status of the query.
        message: Response message.
        data: Query result data.
        metadata: Optional metadata about the query.
    """

    status: str = Field(..., description="Query status")
    message: str = Field(..., description="Response message")
    data: dict = Field(default_factory=dict, description="Query result data")
    metadata: dict | None = Field(default=None, description="Query metadata")


class LabelSearchResponse(BaseModel):
    """Response model for label search operations.

    Attributes:
        labels: List of matching labels.
    """

    labels: list[str] = Field(default_factory=list, description="Matching labels")


class GraphResponse(BaseModel):
    """Response model for graph queries.

    Attributes:
        nodes: List of node dictionaries.
        edges: List of edge dictionaries.
        metadata: Optional metadata about the graph.
    """

    nodes: list[dict] | None = Field(default=None, description="Graph nodes")
    edges: list[dict] | None = Field(default=None, description="Graph edges")
    metadata: dict | None = Field(default=None, description="Graph metadata")


class TrackStatusResponse(BaseModel):
    """Response model for tracking async operation status.

    Attributes:
        track_id: The tracking ID for the operation.
        documents: List of document dictionaries.
        total_count: Total number of documents.
        status_summary: Summary of document statuses.
    """

    track_id: str = Field(..., description="Tracking ID")
    documents: list[dict] = Field(default_factory=list, description="Document list")
    total_count: int = Field(default=0, description="Total document count")
    status_summary: dict = Field(default_factory=dict, description="Status summary")


class PaginatedDocsResponse(BaseModel):
    """Response model for paginated document listing.

    Attributes:
        documents: List of document dictionaries.
        total: Total number of documents (for backwards compatibility).
        page: Current page number (for backwards compatibility).
        page_size: Number of documents per page (for backwards compatibility).
        pagination: Pagination info dict with page, page_size, total_count,
            total_pages, has_next, has_prev fields.
        status_counts: Status counts dictionary.
    """

    documents: list[dict] = Field(default_factory=list, description="List of documents")
    total: int = Field(default=0, description="Total document count")
    page: int = Field(default=1, description="Current page")
    page_size: int = Field(default=50, description="Documents per page")
    pagination: dict | None = Field(default=None, description="Pagination metadata")
    status_counts: dict | None = Field(default=None, description="Status counts")


# Alias for backwards compatibility
ListDocsResponse = PaginatedDocsResponse
"""Alias for PaginatedDocsResponse."""


class DocStatusResponse(BaseModel):
    """Response model for document status queries.

    Attributes:
        doc_id: Document ID.
        status: Current processing status.
        chunks_count: Number of chunks in the document.
        metadata: Optional document metadata.
    """

    doc_id: str = Field(default="", description="Document ID")
    status: str = Field(default="", description="Processing status")
    chunks_count: int = Field(default=0, description="Number of chunks")
    metadata: dict | None = Field(default=None, description="Document metadata")


class PipelineStatusResponse(BaseModel):
    """Response model for pipeline status queries.

    Attributes:
        autoscanned: Whether autoscan is enabled.
        busy: Whether the pipeline is busy.
        job_name: Current job name.
        job_start: Job start time.
        docs: Number of documents.
        batchs: Number of batches.
        cur_batch: Current batch number.
        request_pending: Whether a request is pending.
        latest_message: Latest status message.
        history_messages: List of historical messages.
        update_status: Update status dictionary.
    """

    autoscanned: bool = Field(default=False, description="Whether autoscan is enabled")
    busy: bool = Field(default=False, description="Whether the pipeline is busy")
    job_name: str = Field(default="", description="Current job name")
    job_start: str | None = Field(default=None, description="Job start time")
    docs: int = Field(default=0, description="Number of documents")
    batchs: int = Field(default=0, description="Number of batches")
    cur_batch: int = Field(default=0, description="Current batch number")
    request_pending: bool = Field(default=False, description="Whether a request is pending")
    latest_message: str = Field(default="", description="Latest status message")
    history_messages: list[str] | None = Field(default=None, description="Historical messages")
    update_status: dict | None = Field(default=None, description="Update status")
