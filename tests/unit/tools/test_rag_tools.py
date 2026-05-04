"""Tests for RAG knowledge management tools (daemon.tools.rag_tools).

Tests the 16 RAG tools created by create_rag_tools() factory function,
including insert, query, graph operations, and entity management.
"""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.rag.exceptions import RAGError
from daemon.rag.schemas import (
    GraphResponse,
    InsertResponse,
    LabelSearchResponse,
    ListDocsResponse,
    QueryDataResponse,
    QueryResponse,
    TrackStatusResponse,
)
from daemon.tools.rag_tools import create_rag_tools


# =============================================================================
# Fixtures
# =============================================================================


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
def mock_manager():
    """Create a mock InstanceManager."""
    manager = MagicMock()
    # Mock instance with project_id in metadata
    mock_instance = MagicMock()
    mock_instance.instance_metadata = {"project_id": "test-project-123"}
    manager._instance_repository.get.return_value = mock_instance
    # Mock project repository to return project name
    mock_project = MagicMock()
    mock_project.name = "test-project"
    manager._project_repository.get.return_value = mock_project
    return manager


@pytest.fixture
def mock_client():
    """Create a mock RAG client with pre-configured responses."""
    client = AsyncMock()
    client.is_available = True

    # Configure common return values
    client.insert_text = AsyncMock(
        return_value=InsertResponse(track_id="test-track-123", status="processing", message="Text inserted successfully")
    )
    client.insert_texts = AsyncMock(
        return_value=InsertResponse(track_id="test-track-456", status="processing", message="Texts inserted successfully")
    )
    client.query = AsyncMock(
        return_value=QueryResponse(
            response="Test response from knowledge graph."
        )
    )
    client.query_data = AsyncMock(
        return_value=QueryDataResponse(
            status="success",
            message="Query completed",
            data={
                "entities": [
                    {"entity_name": "Alice", "entity_type": "PERSON", "description": "A test entity"},
                ],
                "relationships": [
                    {"src_id": "Alice", "tgt_id": "Bob", "relation_type": "KNOWS", "description": "Friends"},
                ],
            }
        )
    )
    client.search_labels = AsyncMock(
        return_value=LabelSearchResponse(labels=["Person", "Place", "Concept"])
    )
    client.get_graph = AsyncMock(
        return_value=GraphResponse(
            nodes=[
                {"id": "node1", "type": "PERSON"},
                {"id": "node2", "type": "PLACE"},
            ],
            edges=[
                {"source": "node1", "target": "node2", "type": "VISITED"},
            ],
        )
    )
    client.create_entity = AsyncMock(return_value={"status": "created"})
    client.get_entity = AsyncMock(return_value={
        "entity_name": "TestEntity",
        "entity_type": "CONCEPT",
        "description": "A test entity for unit testing",
    })
    client.create_relation = AsyncMock(return_value={"status": "created"})
    client.update_entity = AsyncMock(return_value={"status": "updated"})
    client.merge_entities = AsyncMock(return_value={"status": "merged"})
    client.delete_entity = AsyncMock(return_value={"status": "deleted"})
    client.delete_relation = AsyncMock(return_value={"status": "deleted"})
    client.delete_docs = AsyncMock(return_value={"status": "deleted"})
    client.list_docs = AsyncMock(
        return_value=ListDocsResponse(
            documents=[
                {"id": "doc1", "status": "completed", "name": "test-doc-1.txt"},
                {"id": "doc2", "status": "processing", "name": "test-doc-2.txt"},
            ],
            total=10,
            page=1,
            page_size=50,
        )
    )
    client.track_status = AsyncMock(
        return_value=TrackStatusResponse(
            track_id="test-track-123",
            documents=[
                {"id": "doc1", "status": "completed", "name": "test-doc-1.txt"},
            ],
            total_count=1,
            status_summary={"completed": 1, "processing": 0, "failed": 0},
        )
    )

    return client


@pytest.fixture
def rag_tools(configured_env, mock_manager, mock_client):
    """Create RAG tools with mocked RAG client."""
    # Reset the module-level client singleton to use our mock
    import daemon.tools.rag_tools as rag_tools_module
    rag_tools_module._rag_client = mock_client

    tools = create_rag_tools(mock_manager, "test-instance-id")
    return tools


# =============================================================================
# Factory Tests
# =============================================================================


class TestRAGToolsFactory:
    """Tests for the create_rag_tools factory function."""

    def test_rag_tools_factory_returns_16_tools(self, configured_env, mock_manager, mock_client):
        """Factory returns exactly 16 tools."""
        import daemon.tools.rag_tools as rag_tools_module
        rag_tools_module._rag_client = mock_client

        tools = create_rag_tools(mock_manager, "test-instance-id")
        assert len(tools) == 16

    def test_rag_tools_have_correct_category(self, rag_tools):
        """All tools have _tool_category == 'rag'."""
        for tool in rag_tools:
            assert hasattr(tool, "_tool_category")
            assert tool._tool_category == "rag"


# =============================================================================
# Insert Text Tests
# =============================================================================


class TestRAGInsertText:
    """Tests for rag_insert_text tool."""

    @pytest.mark.asyncio
    async def test_rag_insert_text_success(self, rag_tools):
        """Verify insert text returns track ID."""
        # Find the insert_text tool
        insert_tool = next(t for t in rag_tools if t.name == "rag_insert_text")

        result = await insert_tool.ainvoke({
            "text": "Test content to insert",
            "description": "A test description",
        })

        assert "Text inserted" in result
        assert "test-track-123" in result

    @pytest.mark.asyncio
    async def test_rag_insert_text_not_configured(self, unconfigured_env, mock_manager):
        """Verify error message when RAG is not configured."""
        # Reset the module-level client singleton
        import daemon.tools.rag_tools as rag_tools_module
        rag_tools_module._rag_client = None

        tools = create_rag_tools(mock_manager, "test-instance-id")
        insert_tool = next(t for t in tools if t.name == "rag_insert_text")

        result = await insert_tool.ainvoke({"text": "Test content"})

        assert "Error" in result
        assert "not configured" in result.lower()

    @pytest.mark.asyncio
    async def test_rag_insert_text_with_category(self, rag_tools, mock_client):
        """Verify category parameter is passed through."""
        insert_tool = next(t for t in rag_tools if t.name == "rag_insert_text")

        await insert_tool.ainvoke({
            "text": "Test content",
            "category": "architecture",
        })

        mock_client.insert_text.assert_called_once()
        call_kwargs = mock_client.insert_text.call_args.kwargs
        assert "/architecture/" in call_kwargs["file_source"]


class TestRAGInsertTexts:
    """Tests for rag_insert_texts tool."""

    @pytest.mark.asyncio
    async def test_rag_insert_texts_success(self, rag_tools):
        """Verify bulk insert returns count and track ID."""
        insert_tool = next(t for t in rag_tools if t.name == "rag_insert_texts")

        result = await insert_tool.ainvoke({
            "texts": ["Text 1", "Text 2", "Text 3"],
        })

        assert "3 texts inserted" in result
        assert "test-track-456" in result


# =============================================================================
# Query Tests
# =============================================================================


class TestRAGQuery:
    """Tests for rag_query tool."""

    @pytest.mark.asyncio
    async def test_rag_query_success(self, rag_tools):
        """Verify query returns response text."""
        query_tool = next(t for t in rag_tools if t.name == "rag_query")

        result = await query_tool.ainvoke({"query": "What is the test?"})

        assert "Test response from knowledge graph" in result

    @pytest.mark.asyncio
    async def test_rag_query_with_mode(self, rag_tools, mock_client):
        """Verify mode parameter is passed through."""
        query_tool = next(t for t in rag_tools if t.name == "rag_query")

        await query_tool.ainvoke({"query": "Test query", "mode": "local"})

        mock_client.query.assert_called_once()
        call_kwargs = mock_client.query.call_args.kwargs
        assert call_kwargs["mode"] == "local"

    @pytest.mark.asyncio
    async def test_rag_query_error_handling(self, configured_env, mock_manager, mock_client):
        """Verify RAGError is caught and returns error string."""
        mock_client.query = AsyncMock(side_effect=RAGError("Query failed"))

        import daemon.tools.rag_tools as rag_tools_module
        rag_tools_module._rag_client = mock_client

        tools = create_rag_tools(mock_manager, "test-instance-id")
        query_tool = next(t for t in tools if t.name == "rag_query")

        result = await query_tool.ainvoke({"query": "Test query"})

        assert "Error" in result or "RAG error" in result


class TestRAGQueryData:
    """Tests for rag_query_data tool."""

    @pytest.mark.asyncio
    async def test_rag_query_data_success(self, rag_tools):
        """Verify structured query returns entities and relations."""
        query_tool = next(t for t in rag_tools if t.name == "rag_query_data")

        result = await query_tool.ainvoke({"query": "Find entities"})

        assert "## Entities" in result
        assert "Alice" in result
        assert "## Relationships" in result
        assert "Bob" in result


# =============================================================================
# Search and Graph Tests
# =============================================================================


class TestRAGSearchLabels:
    """Tests for rag_search_labels tool."""

    @pytest.mark.asyncio
    async def test_rag_search_labels_success(self, rag_tools):
        """Verify label search returns matching labels."""
        search_tool = next(t for t in rag_tools if t.name == "rag_search_labels")

        result = await search_tool.ainvoke({"query": "Person"})

        assert "Matching labels" in result
        assert "Person" in result
        assert "Place" in result

    @pytest.mark.asyncio
    async def test_rag_search_labels_no_results(self, configured_env, mock_manager, mock_client):
        """Verify empty result handling."""
        mock_client.search_labels = AsyncMock(return_value=LabelSearchResponse(labels=[]))

        import daemon.tools.rag_tools as rag_tools_module
        rag_tools_module._rag_client = mock_client

        tools = create_rag_tools(mock_manager, "test-instance-id")
        search_tool = next(t for t in tools if t.name == "rag_search_labels")

        result = await search_tool.ainvoke({"query": "NonExistent"})

        assert "No labels found" in result


class TestRAGGetGraph:
    """Tests for rag_get_graph tool."""

    @pytest.mark.asyncio
    async def test_rag_get_graph_success(self, rag_tools):
        """Verify graph retrieval returns nodes and edges."""
        graph_tool = next(t for t in rag_tools if t.name == "rag_get_graph")

        result = await graph_tool.ainvoke({})

        assert "## Full Knowledge Graph" in result
        assert "node1" in result
        assert "node2" in result
        assert "VISITED" in result

    @pytest.mark.asyncio
    async def test_rag_get_graph_with_label(self, rag_tools, mock_client):
        """Verify label parameter is passed through."""
        graph_tool = next(t for t in rag_tools if t.name == "rag_get_graph")

        await graph_tool.ainvoke({"label": "Person"})

        mock_client.get_graph.assert_called_once()
        call_kwargs = mock_client.get_graph.call_args.kwargs
        assert call_kwargs["label"] == "Person"


# =============================================================================
# Entity Operation Tests
# =============================================================================


class TestRAGCreateEntity:
    """Tests for rag_create_entity tool."""

    @pytest.mark.asyncio
    async def test_rag_create_entity_success(self, rag_tools):
        """Verify entity creation returns confirmation."""
        create_tool = next(t for t in rag_tools if t.name == "rag_create_entity")

        result = await create_tool.ainvoke({
            "name": "TestEntity",
            "entity_type": "PERSON",
            "description": "A test entity",
        })

        assert "TestEntity" in result
        assert "created" in result.lower()


class TestRAGGetEntity:
    """Tests for rag_get_entity tool."""

    @pytest.mark.asyncio
    async def test_rag_get_entity_success(self, rag_tools, mock_client):
        """Verify entity retrieval returns formatted details."""
        get_tool = next(t for t in rag_tools if t.name == "rag_get_entity")

        result = await get_tool.ainvoke({"name": "TestEntity"})

        assert "TestEntity" in result
        assert "CONCEPT" in result
        mock_client.get_entity.assert_called_once()
        call_kwargs = mock_client.get_entity.call_args.kwargs
        assert call_kwargs["entity_name"] == "TestEntity"

    @pytest.mark.asyncio
    async def test_rag_get_entity_not_configured(self, configured_env, mock_manager, unconfigured_env):
        """Verify get entity returns error when RAG is not configured."""
        import daemon.tools.rag_tools as rag_tools_module
        rag_tools_module._rag_client = None

        tools = create_rag_tools(mock_manager, "test-instance-id")
        get_tool = next(t for t in tools if t.name == "rag_get_entity")

        result = await get_tool.ainvoke({"name": "TestEntity"})

        assert "not configured" in result.lower()


class TestRAGCreateRelation:
    """Tests for rag_create_relation tool."""

    @pytest.mark.asyncio
    async def test_rag_create_relation_success(self, rag_tools):
        """Verify relation creation returns confirmation."""
        create_tool = next(t for t in rag_tools if t.name == "rag_create_relation")

        result = await create_tool.ainvoke({
            "source": "Entity1",
            "target": "Entity2",
            "relation_type": "KNOWS",
        })

        assert "Entity1" in result
        assert "Entity2" in result
        assert "KNOWS" in result


class TestRAGUpdateEntity:
    """Tests for rag_update_entity tool."""

    @pytest.mark.asyncio
    async def test_rag_update_entity_success(self, rag_tools):
        """Verify entity update returns confirmation."""
        update_tool = next(t for t in rag_tools if t.name == "rag_update_entity")

        result = await update_tool.ainvoke({
            "name": "ExistingEntity",
            "description": "Updated description",
        })

        assert "ExistingEntity" in result
        assert "updated" in result.lower()


class TestRAGMergeEntities:
    """Tests for rag_merge_entities tool."""

    @pytest.mark.asyncio
    async def test_rag_merge_entities_success(self, rag_tools):
        """Verify entity merge returns confirmation."""
        merge_tool = next(t for t in rag_tools if t.name == "rag_merge_entities")

        result = await merge_tool.ainvoke({
            "source_entities": ["Entity1", "Entity2"],
            "target_entity_name": "MergedEntity",
        })

        assert "MergedEntity" in result
        assert "merged" in result.lower()


class TestRAGDeleteEntity:
    """Tests for rag_delete_entity tool."""

    @pytest.mark.asyncio
    async def test_rag_delete_entity_success(self, rag_tools):
        """Verify entity deletion returns confirmation."""
        delete_tool = next(t for t in rag_tools if t.name == "rag_delete_entity")

        result = await delete_tool.ainvoke({"entity_name": "ToDelete"})

        assert "ToDelete" in result
        assert "deleted" in result.lower()


# =============================================================================
# Relation Operation Tests
# =============================================================================


class TestRAGDeleteRelation:
    """Tests for rag_delete_relation tool."""

    @pytest.mark.asyncio
    async def test_rag_delete_relation_success(self, rag_tools):
        """Verify relation deletion returns confirmation."""
        delete_tool = next(t for t in rag_tools if t.name == "rag_delete_relation")

        result = await delete_tool.ainvoke({
            "source": "Entity1",
            "target": "Entity2",
        })

        assert "deleted" in result.lower()


# =============================================================================
# Document Operation Tests
# =============================================================================


class TestRAGDeleteDocs:
    """Tests for rag_delete_docs tool."""

    @pytest.mark.asyncio
    async def test_rag_delete_docs_success(self, rag_tools):
        """Verify document deletion returns count."""
        delete_tool = next(t for t in rag_tools if t.name == "rag_delete_docs")

        result = await delete_tool.ainvoke({"doc_ids": ["doc1", "doc2", "doc3"]})

        assert "3 documents deleted" in result


class TestRAGListDocs:
    """Tests for rag_list_docs tool."""

    @pytest.mark.asyncio
    async def test_rag_list_docs_success(self, rag_tools):
        """Verify document listing returns formatted output."""
        list_tool = next(t for t in rag_tools if t.name == "rag_list_docs")

        result = await list_tool.ainvoke({})

        assert "## Documents" in result
        assert "test-doc-1.txt" in result
        assert "test-doc-2.txt" in result
        assert "completed" in result


# =============================================================================
# Status Operation Tests
# =============================================================================


class TestRAGTrackStatus:
    """Tests for rag_track_status tool."""

    @pytest.mark.asyncio
    async def test_rag_track_status_success(self, rag_tools):
        """Verify status tracking returns formatted output."""
        track_tool = next(t for t in rag_tools if t.name == "rag_track_status")

        result = await track_tool.ainvoke({"track_id": "test-track-123"})

        assert "test-track-123" in result
        assert "## Track Status:" in result
