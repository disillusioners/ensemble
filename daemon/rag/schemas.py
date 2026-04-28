"""Pydantic schemas for LightRAG REST API.

This module defines request and response models for interacting with the LightRAG
knowledge graph and retrieval system via its HTTP API. All models use Pydantic v2
style with proper type hints, defaults, and documentation.

Reference: LightRAG REST API endpoints for text insertion, querying, graph
operations, and document management.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Request Models
# =============================================================================


class InsertTextRequest(BaseModel):
    """Request model for inserting a single text into the knowledge graph.

    Attributes:
        text: The text content to insert.
        description: Optional description or metadata for the text.
        file_paths: Optional list of file paths associated with the text.
    """

    text: str = Field(..., description="Text content to insert into the knowledge graph")
    description: str = Field(default="", description="Optional description for the text")
    file_paths: list[str] | None = Field(default=None, description="Optional file paths")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class InsertTextsRequest(BaseModel):
    """Request model for inserting multiple texts into the knowledge graph.

    Attributes:
        texts: List of text strings to insert.
    """

    texts: list[str] = Field(..., description="List of text strings to insert")

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
        max_token_for_text_unit: Max tokens for text unit retrieval.
        max_token_for_global_context: Max tokens for global context.
        max_token_for_local_context: Max tokens for local context.
        hl_keywords: High-level keywords for query enhancement.
        ll_keywords: Low-level keywords for query enhancement.
        stream: Enable streaming response.
        history_turns: Number of conversation turns to include.
    """

    query: str = Field(..., description="Query string to search for")
    mode: str = Field(
        default="hybrid",
        description="Query mode",
    )
    only_need_context: bool = Field(default=False, description="Return only context")
    only_need_prompt: bool = Field(default=False, description="Return only the prompt")
    response_type: str | None = Field(default=None, description="Response type")
    top_k: int | None = Field(default=None, description="Number of top results")
    max_token_for_text_unit: int | None = Field(default=None, description="Max tokens for text unit")
    max_token_for_global_context: int | None = Field(default=None, description="Max tokens for global context")
    max_token_for_local_context: int | None = Field(default=None, description="Max tokens for local context")
    hl_keywords: list[str] | None = Field(default=None, description="High-level keywords")
    ll_keywords: list[str] | None = Field(default=None, description="Low-level keywords")
    stream: bool = Field(default=False, description="Enable streaming response")
    history_turns: int | None = Field(default=None, description="Conversation history turns")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class QueryDataRequest(BaseModel):
    """Request model for querying knowledge graph data (entities and relations).

    Attributes:
        query: The query string to search for.
        mode: Query mode - one of local, global, hybrid, naive, mix.
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
    """

    query: str = Field(..., description="Query string to search for")
    mode: str = Field(default="hybrid", description="Query mode")
    only_need_context: bool = Field(default=False, description="Return only context")
    only_need_prompt: bool = Field(default=False, description="Return only the prompt")
    response_type: str | None = Field(default=None, description="Response type")
    top_k: int | None = Field(default=None, description="Number of top results")
    max_token_for_text_unit: int | None = Field(default=None, description="Max tokens for text unit")
    max_token_for_global_context: int | None = Field(default=None, description="Max tokens for global context")
    max_token_for_local_context: int | None = Field(default=None, description="Max tokens for local context")
    hl_keywords: list[str] | None = Field(default=None, description="High-level keywords")
    ll_keywords: list[str] | None = Field(default=None, description="Low-level keywords")
    stream: bool = Field(default=False, description="Enable streaming response")
    history_turns: int | None = Field(default=None, description="Conversation history turns")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class LabelSearchRequest(BaseModel):
    """Request model for searching by label.

    Attributes:
        label: The label to search for.
        max_results: Maximum number of results to return.
    """

    label: str = Field(..., description="Label to search for")
    max_results: int = Field(default=10, description="Maximum number of results")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class CreateEntityRequest(BaseModel):
    """Request model for creating a new entity in the knowledge graph.

    Attributes:
        entity_name: Name of the entity to create.
        description: Optional description of the entity.
        entity_type: Type/category of the entity (default: UNKNOWN).
        metadata: Optional metadata dictionary for the entity.
    """

    entity_name: str = Field(..., description="Name of the entity")
    description: str = Field(default="", description="Description of the entity")
    entity_type: str = Field(default="UNKNOWN", description="Type of the entity")
    metadata: dict | None = Field(default=None, description="Entity metadata")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class CreateRelationRequest(BaseModel):
    """Request model for creating a relation between entities.

    Attributes:
        source_entity: Name of the source entity.
        target_entity: Name of the target entity.
        description: Optional description of the relation.
        relation_type: Type of the relation (default: RELATED_TO).
        metadata: Optional metadata dictionary for the relation.
        weight: Optional weight value for the relation.
    """

    source_entity: str = Field(..., description="Source entity name")
    target_entity: str = Field(..., description="Target entity name")
    description: str = Field(default="", description="Relation description")
    relation_type: str = Field(default="RELATED_TO", description="Type of relation")
    metadata: dict | None = Field(default=None, description="Relation metadata")
    weight: float | None = Field(default=None, description="Relation weight")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class UpdateEntityRequest(BaseModel):
    """Request model for updating an existing entity.

    Attributes:
        entity_name: Name of the entity to update.
        description: New description for the entity.
        entity_type: New type for the entity.
        metadata: New metadata for the entity.
    """

    entity_name: str = Field(..., description="Name of the entity to update")
    description: str | None = Field(default=None, description="New description")
    entity_type: str | None = Field(default=None, description="New entity type")
    metadata: dict | None = Field(default=None, description="New metadata")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class MergeEntitiesRequest(BaseModel):
    """Request model for merging multiple entities into one.

    Attributes:
        source_entities: List of entity names to merge from.
        target_entity: Name of the target entity to merge into.
        description: Optional new description for the merged entity.
        entity_type: Optional new type for the merged entity.
        metadata: Optional new metadata for the merged entity.
    """

    source_entities: list[str] = Field(..., description="Entities to merge")
    target_entity: str = Field(..., description="Target entity name")
    description: str | None = Field(default=None, description="New description")
    entity_type: str | None = Field(default=None, description="New entity type")
    metadata: dict | None = Field(default=None, description="New metadata")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class DeleteDocsRequest(BaseModel):
    """Request model for deleting documents by IDs.

    Attributes:
        doc_ids: List of document IDs to delete.
    """

    doc_ids: list[str] = Field(..., description="Document IDs to delete")

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
        relation_type: Type of relation to delete (optional, deletes all if None).
    """

    source_entity: str = Field(..., description="Source entity name")
    target_entity: str = Field(..., description="Target entity name")
    relation_type: str | None = Field(default=None, description="Relation type to delete")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


# Aliases for Entity operations
EntityCreateRequest = CreateEntityRequest
"""Alias for CreateEntityRequest."""
EntityUpdateRequest = UpdateEntityRequest
"""Alias for UpdateEntityRequest."""
EntityMergeRequest = MergeEntitiesRequest
"""Alias for MergeEntitiesRequest."""


class RelationCreateRequest(CreateRelationRequest):
    """Alias for CreateRelationRequest for relation creation."""

    pass


class RelationUpdateRequest(BaseModel):
    """Request model for updating an existing relation.

    Attributes:
        source_entity: Name of the source entity.
        target_entity: Name of the target entity.
        description: New description for the relation.
        relation_type: New type for the relation.
        metadata: New metadata for the relation.
        weight: New weight for the relation.
    """

    source_entity: str = Field(..., description="Source entity name")
    target_entity: str = Field(..., description="Target entity name")
    description: str | None = Field(default=None, description="New description")
    relation_type: str | None = Field(default=None, description="New relation type")
    metadata: dict | None = Field(default=None, description="New metadata")
    weight: float | None = Field(default=None, description="New weight")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class DocumentsRequest(BaseModel):
    """Request model for listing documents with pagination.

    Attributes:
        page: Page number (1-indexed).
        page_size: Number of documents per page.
        status: Optional filter by document status.
    """

    page: int = Field(default=1, description="Page number (1-indexed)")
    page_size: int = Field(default=50, description="Documents per page")
    status: str | None = Field(default=None, description="Filter by status")

    def to_api_dict(self) -> dict[str, Any]:
        """Return dictionary with None values excluded for API calls."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


# =============================================================================
# Response Models
# =============================================================================


class InsertResponse(BaseModel):
    """Response model for text insertion operations.

    Attributes:
        status: Status of the insertion (e.g., "accepted").
        message: Optional message from the server.
        track_id: Optional tracking ID for async operations.
    """

    status: str = Field(default="accepted", description="Insertion status")
    message: str = Field(default="", description="Response message")
    track_id: str | None = Field(default=None, description="Tracking ID for async operations")


class QueryResponse(BaseModel):
    """Response model for query operations.

    Attributes:
        response: The generated response text.
        metadata: Optional metadata about the query results.
    """

    response: str = Field(default="", description="Generated response text")
    metadata: dict | None = Field(default=None, description="Response metadata")


class QueryDataResponse(BaseModel):
    """Response model for data query operations (entities and relationships).

    Attributes:
        entities: List of entity dictionaries.
        relationships: List of relationship dictionaries.
        metadata: Optional metadata about the query.
    """

    entities: list[dict] | None = Field(default=None, description="Retrieved entities")
    relationships: list[dict] | None = Field(default=None, description="Retrieved relationships")
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
        status: Current status of the operation.
        progress: Progress percentage (0-1).
        message: Optional status message.
    """

    track_id: str = Field(default="", description="Tracking ID")
    status: str = Field(default="", description="Operation status")
    progress: float | None = Field(default=None, description="Progress (0-1)")
    message: str | None = Field(default=None, description="Status message")


class ListDocsResponse(BaseModel):
    """Response model for document listing with pagination.

    Attributes:
        documents: List of document dictionaries.
        total: Total number of documents.
        page: Current page number.
        page_size: Number of documents per page.
    """

    documents: list[dict] = Field(default_factory=list, description="List of documents")
    total: int = Field(default=0, description="Total document count")
    page: int = Field(default=1, description="Current page")
    page_size: int = Field(default=50, description="Documents per page")


# Alias for ListDocsResponse
PaginatedDocsResponse = ListDocsResponse
"""Alias for ListDocsResponse."""


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
        status: Overall pipeline status.
        progress: Overall progress (0-1).
        message: Optional status message.
        queued: Number of items queued.
        processing: Number of items currently processing.
    """

    status: str = Field(default="", description="Pipeline status")
    progress: float | None = Field(default=None, description="Progress (0-1)")
    message: str | None = Field(default=None, description="Status message")
    queued: int | None = Field(default=None, description="Items in queue")
    processing: int | None = Field(default=None, description="Items processing")
