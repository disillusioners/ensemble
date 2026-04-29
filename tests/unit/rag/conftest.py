"""Pytest fixtures for RAG module tests."""

import os
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio

from daemon.rag import AsyncLightRAGClient, RAGConfig


@pytest.fixture
def configured_env():
    """Set up environment variables for configured RAG client."""
    os.environ["LIGHTRAG_HOST"] = "http://localhost:8724"
    os.environ["LIGHTRAG_API_KEY"] = "test-api-key"
    os.environ["LIGHTRAG_WORKSPACE"] = "test-workspace"
    os.environ["LIGHTRAG_TIMEOUT"] = "60"

    yield {
        "host": "http://localhost:8724",
        "api_key": "test-api-key",
        "workspace": "test-workspace",
        "timeout": 60.0,
    }

    # Cleanup
    for key in ["LIGHTRAG_HOST", "LIGHTRAG_API_KEY", "LIGHTRAG_WORKSPACE", "LIGHTRAG_TIMEOUT"]:
        os.environ.pop(key, None)


@pytest.fixture
def unconfigured_env():
    """Ensure no RAG environment variables are set."""
    for key in ["LIGHTRAG_HOST", "LIGHTRAG_API_KEY", "LIGHTRAG_WORKSPACE", "LIGHTRAG_TIMEOUT"]:
        os.environ.pop(key, None)
    yield


@pytest.fixture
def configured_config(configured_env) -> RAGConfig:
    """Create a configured RAGConfig instance."""
    return RAGConfig.from_env()


@pytest.fixture
def unconfigured_config(unconfigured_env) -> RAGConfig:
    """Create an unconfigured RAGConfig instance."""
    return RAGConfig.from_env()


@pytest.fixture
def configured_client(configured_config) -> AsyncLightRAGClient:
    """Create a configured AsyncLightRAGClient instance."""
    return AsyncLightRAGClient(configured_config)


@pytest.fixture
def unconfigured_client(unconfigured_env) -> AsyncLightRAGClient:
    """Create an unconfigured AsyncLightRAGClient instance."""
    return AsyncLightRAGClient()


# =============================================================================
# Mock Response Fixtures
# =============================================================================


@pytest.fixture
def mock_insert_response() -> dict[str, Any]:
    """Mock response for text insertion."""
    return {
        "status": "accepted",
        "message": "Text inserted successfully",
        "track_id": "track-12345",
    }


@pytest.fixture
def mock_query_response() -> dict[str, Any]:
    """Mock response for query."""
    return {
        "response": "This is a test response from the knowledge graph.",
        "metadata": {
            "mode": "hybrid",
            "sources": ["doc1", "doc2"],
        },
    }


@pytest.fixture
def mock_query_data_response() -> dict[str, Any]:
    """Mock response for query_data."""
    return {
        "status": "success",
        "message": "Query completed successfully",
        "data": {
            "entities": [
                {"entity_name": "Entity1", "entity_type": "PERSON", "description": "First entity"},
                {"entity_name": "Entity2", "entity_type": "PLACE", "description": "Second entity"},
            ],
            "relationships": [
                {"src_id": "Entity1", "tgt_id": "Entity2", "relation_type": "RELATED_TO"},
            ],
            "metadata": {"count": 2},
        },
    }


@pytest.fixture
def mock_label_search_response() -> dict[str, Any]:
    """Mock response for label search."""
    return {
        "labels": ["Person", "Place", "Organization", "Concept"],
    }


@pytest.fixture
def mock_graph_response() -> dict[str, Any]:
    """Mock response for get_graph."""
    return {
        "nodes": [
            {"id": "node1", "label": "Person", "properties": {"name": "Alice"}},
            {"id": "node2", "label": "Place", "properties": {"name": "Paris"}},
        ],
        "edges": [
            {"id": "edge1", "source": "node1", "target": "node2", "type": "VISITED"},
        ],
        "metadata": {"total_nodes": 2, "total_edges": 1},
    }


@pytest.fixture
def mock_track_status_response() -> dict[str, Any]:
    """Mock response for track_status."""
    return {
        "track_id": "track-12345",
        "status": "completed",
        "progress": 1.0,
        "message": "Processing complete",
    }


@pytest.fixture
def mock_list_docs_response() -> dict[str, Any]:
    """Mock response for list_docs."""
    return {
        "documents": [
            {"doc_id": "doc1", "status": "completed", "chunks_count": 5},
            {"doc_id": "doc2", "status": "processing", "chunks_count": 3},
        ],
        "total": 100,
        "page": 1,
        "page_size": 50,
    }


@pytest.fixture
def mock_pipeline_status_response() -> dict[str, Any]:
    """Mock response for pipeline_status."""
    return {
        "status": "running",
        "progress": 0.75,
        "message": "Pipeline is processing",
        "queued": 10,
        "processing": 3,
    }


# =============================================================================
# Mock Transport Handler
# =============================================================================


def create_mock_transport(responses: dict[str, Any]) -> httpx.MockTransport:
    """Create a mock transport that returns configured responses.

    Args:
        responses: Dict mapping URL paths to response dicts.

    Returns:
        Configured httpx.MockTransport instance.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in responses:
            return httpx.Response(
                status_code=200,
                json=responses[path],
            )
        # Handle track_status with path variable
        if "/track_status/" in path:
            track_id = path.split("/")[-1]
            if "/track_status/" in str(responses):
                return httpx.Response(
                    status_code=200,
                    json=responses["/track_status/"],
                )
            return httpx.Response(
                status_code=200,
                json={"track_id": track_id, "status": "completed", "progress": 1.0},
            )
        return httpx.Response(
            status_code=404,
            json={"detail": f"Endpoint not found: {path}"},
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_transport(mock_insert_response, mock_query_response, mock_query_data_response, mock_label_search_response, mock_graph_response, mock_track_status_response, mock_list_docs_response, mock_pipeline_status_response) -> httpx.MockTransport:
    """Create a mock transport with standard responses."""
    responses = {
        "/documents/text": mock_insert_response,
        "/documents/texts": mock_insert_response,
        "/query": mock_query_response,
        "/query/data": mock_query_data_response,
        "/graph/label/search": mock_label_search_response,
        "/graphs": mock_graph_response,
        "/graph/entity/create": {"status": "created"},
        "/graph/entity/edit": {"status": "updated"},
        "/graph/entities/merge": {"status": "merged"},
        "/graph/relation/create": {"status": "created"},
        "/documents/delete_entity": {"status": "deleted"},
        "/documents/delete_relation": {"status": "deleted"},
        "/documents/delete_document": {"status": "deleted"},
        "/documents/paginated": mock_list_docs_response,
        "/documents/pipeline_status": mock_pipeline_status_response,
    }
    return create_mock_transport(responses)


@pytest_asyncio.fixture
async def client_with_mock_transport(
    configured_config: RAGConfig,
    mock_transport: httpx.MockTransport,
) -> AsyncIterator[AsyncLightRAGClient]:
    """Create a client with a mock transport attached."""
    client = AsyncLightRAGClient(configured_config)
    # Replace the client with a mocked version
    client._client = httpx.AsyncClient(
        base_url=configured_config.base_url,
        timeout=httpx.Timeout(configured_config.timeout),
        headers=client._build_headers(),
        transport=mock_transport,
    )
    yield client
    await client.close()
