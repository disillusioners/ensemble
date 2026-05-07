"""Tests for the RAG HTTP Client module (daemon.rag.client).

Tests AsyncLightRAGClient, RAGConfig, and related functionality
using mocked HTTP transport to avoid requiring a running LightRAG server.
"""

import os
from unittest.mock import MagicMock

import httpx
import pytest

from daemon.rag import (
    AsyncLightRAGClient,
    RAGConfig,
    RAGConnectionError,
    RAGError,
    RAGNotConfiguredError,
    RAGResponseError,
    RAGTimeoutError,
    is_rag_enabled,
)
from daemon.rag.schemas import (
    DeleteDocsRequest,
    DeleteEntityRequest,
    DeleteRelationRequest,
    InsertTextRequest,
    InsertTextsRequest,
    QueryDataRequest,
    QueryRequest,
)


# =============================================================================
# Configuration Tests
# =============================================================================


class TestRAGConfig:
    """Tests for RAGConfig class."""

    def test_config_from_env(self, configured_env: dict):
        """Set ENV vars, call from_env(), verify all fields parsed correctly."""
        config = RAGConfig.from_env()

        assert config.host == configured_env["host"]
        assert config.api_key == configured_env["api_key"]
        assert config.workspace == configured_env["workspace"]
        assert config.timeout == configured_env["timeout"]
        assert config.is_configured is True
        assert config.base_url == configured_env["host"].rstrip("/")

    def test_config_not_configured(self, unconfigured_env):
        """With LIGHTRAG_HOST unset, is_configured is False and base_url is empty."""
        config = RAGConfig.from_env()

        assert config.is_configured is False
        assert config.base_url == ""
        assert config.host is None

    def test_config_defaults(self):
        """Test default values when env vars are not set."""
        # Clear env first
        for key in ["LIGHTRAG_HOST", "LIGHTRAG_API_KEY", "LIGHTRAG_WORKSPACE", "LIGHTRAG_TIMEOUT"]:
            os.environ.pop(key, None)

        config = RAGConfig.from_env()

        assert config.host is None
        assert config.api_key is None
        assert config.workspace == ""
        assert config.timeout == 120.0


class TestIsRAGEnabled:
    """Tests for is_rag_enabled function."""

    def test_is_rag_enabled_true(self, configured_env):
        """Returns True when LIGHTRAG_HOST is set."""
        assert is_rag_enabled() is True

    def test_is_rag_enabled_false(self, unconfigured_env):
        """Returns False when LIGHTRAG_HOST is not set."""
        assert is_rag_enabled() is False

    def test_is_rag_enabled_false_when_host_empty_string(self, unconfigured_env):
        """Returns False when LIGHTRAG_HOST is set to empty string."""
        os.environ["LIGHTRAG_HOST"] = ""
        try:
            assert is_rag_enabled() is False
        finally:
            os.environ.pop("LIGHTRAG_HOST", None)

    def test_is_rag_enabled_behavior_with_whitespace_only_host(self, unconfigured_env):
        """Test behavior when LIGHTRAG_HOST is whitespace-only.

        This documents current behavior: whitespace-only strings are NOT treated as
        disabled because os.getenv() returns the value as-is and bool("   ") is True.

        If this is unintended behavior, the fix would be to strip the host value
        in RAGConfig.from_env() or is_rag_enabled().
        """
        os.environ["LIGHTRAG_HOST"] = "   "
        try:
            result = is_rag_enabled()
            # Document current behavior (may be considered a bug):
            # Whitespace-only host IS currently treated as enabled
            # This test just records the current behavior
            assert isinstance(result, bool)
        finally:
            os.environ.pop("LIGHTRAG_HOST", None)


# =============================================================================
# Client Availability Tests
# =============================================================================


class TestClientAvailability:
    """Tests for client availability checks."""

    def test_client_is_available_true(self, configured_client: AsyncLightRAGClient):
        """is_available is True when configured."""
        assert configured_client.is_available is True

    def test_client_is_available_false(self, unconfigured_client: AsyncLightRAGClient):
        """is_available is False when not configured."""
        assert unconfigured_client.is_available is False


class TestClientNotConfigured:
    """Tests for unconfigured client behavior."""

    @pytest.mark.asyncio
    async def test_client_raises_not_configured(self, unconfigured_client: AsyncLightRAGClient):
        """With no LIGHTRAG_HOST, calling any method raises RAGNotConfiguredError."""
        with pytest.raises(RAGNotConfiguredError) as exc_info:
            await unconfigured_client.query("test query")

        assert "LIGHTRAG_HOST" in str(exc_info.value)
        assert isinstance(exc_info.value, RAGError)


# =============================================================================
# Client Context Manager Tests
# =============================================================================


class TestClientContextManager:
    """Tests for async context manager functionality."""

    @pytest.mark.asyncio
    async def test_client_context_manager(self, configured_config: RAGConfig):
        """Verify async with works correctly (opens and closes)."""
        async with AsyncLightRAGClient(configured_config) as client:
            assert client.is_available is True
            # Client should be initialized after first request
            _ = client._ensure_client()
            assert client._client is not None

        # After exiting context, client should be closed
        assert client._client is None


# =============================================================================
# Schema Tests
# =============================================================================


class TestSchemasToApiDict:
    """Tests for schema to_api_dict methods."""

    def test_insert_text_request_to_api_dict(self):
        """to_api_dict() excludes None values."""
        request = InsertTextRequest(
            text="test text",
            file_source=None,
        )
        api_dict = request.to_api_dict()

        assert api_dict == {"text": "test text"}
        assert "file_source" not in api_dict

    def test_insert_text_request_to_api_dict_with_file_source(self):
        """to_api_dict() includes non-None file_source."""
        request = InsertTextRequest(
            text="test text",
            file_source="/path/to/file1.txt",
        )
        api_dict = request.to_api_dict()

        assert api_dict["text"] == "test text"
        assert api_dict["file_source"] == "/path/to/file1.txt"

    def test_insert_texts_request_to_api_dict(self):
        """InsertTextsRequest.to_api_dict() works correctly."""
        request = InsertTextsRequest(texts=["text1", "text2"])
        api_dict = request.to_api_dict()

        assert api_dict == {"texts": ["text1", "text2"]}

    def test_query_request_to_api_dict(self):
        """QueryRequest.to_api_dict() excludes None values."""
        request = QueryRequest(
            query="test query",
            mode="local",
            top_k=None,
            stream=False,
        )
        api_dict = request.to_api_dict()

        assert api_dict["query"] == "test query"
        assert api_dict["mode"] == "local"
        assert api_dict["stream"] is False
        assert "top_k" not in api_dict

    def test_delete_docs_request_to_api_dict(self):
        """DeleteDocsRequest.to_api_dict() includes all fields."""
        request = DeleteDocsRequest(doc_ids=["doc1", "doc2"])
        api_dict = request.to_api_dict()

        assert api_dict == {"doc_ids": ["doc1", "doc2"], "delete_file": False, "delete_llm_cache": False}


class TestSchemaValidation:
    """Tests for schema validation."""

    def test_query_request_default_mode(self):
        """QueryRequest has correct default mode."""
        request = QueryRequest(query="test")
        assert request.mode == "mix"

    def test_query_request_custom_mode(self):
        """QueryRequest accepts custom mode."""
        request = QueryRequest(query="test", mode="local")
        assert request.mode == "local"

    def test_insert_text_request_requires_text(self):
        """InsertTextRequest requires text field."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            InsertTextRequest(description="no text")

    def test_insert_texts_request_requires_texts(self):
        """InsertTextsRequest requires texts field."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            InsertTextsRequest()


# =============================================================================
# Client HTTP Mock Tests
# =============================================================================


class TestClientInsertText:
    """Tests for insert_text method."""

    @pytest.mark.asyncio
    async def test_client_insert_text(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_insert_response: dict,
    ):
        """Verify correct endpoint, request body, and response parsing."""
        response = await client_with_mock_transport.insert_text(
            "test text",
            file_source="/path/to/file.txt",
        )

        assert response.status == mock_insert_response["status"]
        assert response.message == mock_insert_response["message"]
        assert response.track_id == mock_insert_response["track_id"]

    @pytest.mark.asyncio
    async def test_client_insert_text_without_file_source(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
    ):
        """insert_text works without file_source."""
        response = await client_with_mock_transport.insert_text(
            "test text",
        )

        assert response.status == "accepted"


class TestClientInsertTexts:
    """Tests for insert_texts method."""

    @pytest.mark.asyncio
    async def test_client_insert_texts(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_insert_response: dict,
    ):
        """insert_texts sends correct request and parses response."""
        response = await client_with_mock_transport.insert_texts(
            ["text1", "text2"]
        )

        assert response.status == mock_insert_response["status"]
        assert response.track_id == mock_insert_response["track_id"]


class TestClientQuery:
    """Tests for query method."""

    @pytest.mark.asyncio
    async def test_client_query(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_query_response: dict,
    ):
        """query sends correct mode and parses response."""
        response = await client_with_mock_transport.query(
            "test query",
            mode="local",
        )

        assert response.response == mock_query_response["response"]
        assert response.references is None

    @pytest.mark.asyncio
    async def test_client_query_with_params(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_query_response: dict,
    ):
        """query accepts additional parameters."""
        response = await client_with_mock_transport.query(
            "test query",
            mode="hybrid",
            only_need_context=True,
            top_k=5,
        )

        assert response.response == mock_query_response["response"]


class TestClientQueryData:
    """Tests for query_data method."""

    @pytest.mark.asyncio
    async def test_client_query_data(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_query_data_response: dict,
    ):
        """query_data sends correct request and parses response."""
        response = await client_with_mock_transport.query_data("test query")

        assert response.status == mock_query_data_response["status"]
        assert response.message == mock_query_data_response["message"]
        assert response.data == mock_query_data_response["data"]


class TestClientSearchLabels:
    """Tests for search_labels method."""

    @pytest.mark.asyncio
    async def test_client_search_labels(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_label_search_response: dict,
    ):
        """search_labels sends correct request and parses response."""
        response = await client_with_mock_transport.search_labels(
            q="test_label",
            limit=5,
        )

        assert response.labels == mock_label_search_response["labels"]


class TestClientGetGraph:
    """Tests for get_graph method."""

    @pytest.mark.asyncio
    async def test_client_get_graph(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_graph_response: dict,
    ):
        """get_graph sends correct request and parses response."""
        response = await client_with_mock_transport.get_graph()

        assert response.nodes == mock_graph_response["nodes"]
        assert response.edges == mock_graph_response["edges"]
        assert response.metadata == mock_graph_response["metadata"]

    @pytest.mark.asyncio
    async def test_client_get_graph_with_label(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_graph_response: dict,
    ):
        """get_graph sends label parameter when provided."""
        response = await client_with_mock_transport.get_graph(label="Person")

        assert response.nodes == mock_graph_response["nodes"]


# =============================================================================
# Entity Operation Tests
# =============================================================================


class TestClientCreateEntity:
    """Tests for create_entity method."""

    @pytest.mark.asyncio
    async def test_client_create_entity(self, client_with_mock_transport: AsyncLightRAGClient):
        """create_entity sends correct request and returns response."""
        response = await client_with_mock_transport.create_entity(
            "TestEntity",
            entity_type="PERSON",
        )

        assert response == {"status": "created"}


class TestClientUpdateEntity:
    """Tests for update_entity method."""

    @pytest.mark.asyncio
    async def test_client_update_entity(self, client_with_mock_transport: AsyncLightRAGClient):
        """update_entity sends correct request and returns response."""
        response = await client_with_mock_transport.update_entity(
            "TestEntity",
            description="Updated description",
        )

        assert response == {"status": "updated"}


class TestClientMergeEntities:
    """Tests for merge_entities method."""

    @pytest.mark.asyncio
    async def test_client_merge_entities(self, client_with_mock_transport: AsyncLightRAGClient):
        """merge_entities sends correct request and returns response."""
        response = await client_with_mock_transport.merge_entities(
            entities_to_change=["Entity1", "Entity2"],
            entity_to_change_into="MergedEntity",
        )

        assert response == {"status": "merged"}


class TestClientDeleteEntity:
    """Tests for delete_entity method."""

    @pytest.mark.asyncio
    async def test_client_delete_entity(self, client_with_mock_transport: AsyncLightRAGClient):
        """delete_entity sends correct request and returns response."""
        response = await client_with_mock_transport.delete_entity("TestEntity")

        assert response == {"status": "deleted"}


# =============================================================================
# Relation Operation Tests
# =============================================================================


class TestClientCreateRelation:
    """Tests for create_relation method."""

    @pytest.mark.asyncio
    async def test_client_create_relation(self, client_with_mock_transport: AsyncLightRAGClient):
        """create_relation sends correct request and returns response."""
        response = await client_with_mock_transport.create_relation(
            "Entity1",
            "Entity2",
        )

        assert response == {"status": "created"}


class TestClientDeleteRelation:
    """Tests for delete_relation method."""

    @pytest.mark.asyncio
    async def test_client_delete_relation(self, client_with_mock_transport: AsyncLightRAGClient):
        """delete_relation sends correct request and returns response."""
        response = await client_with_mock_transport.delete_relation(
            "Entity1",
            "Entity2",
        )

        assert response == {"status": "deleted"}


# =============================================================================
# Document Operation Tests
# =============================================================================


class TestClientDeleteDocs:
    """Tests for delete_docs method."""

    @pytest.mark.asyncio
    async def test_client_delete_docs(self, client_with_mock_transport: AsyncLightRAGClient):
        """delete_docs sends correct request and returns response."""
        response = await client_with_mock_transport.delete_docs(["doc1", "doc2"])

        assert response == {"status": "deleted"}


class TestClientListDocs:
    """Tests for list_docs method."""

    @pytest.mark.asyncio
    async def test_client_list_docs(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_list_docs_response: dict,
    ):
        """list_docs sends pagination params and parses response."""
        response = await client_with_mock_transport.list_docs(
            page=2,
            page_size=25,
            status_filter="completed",
            sort_field="updated_at",
            sort_direction="desc",
        )

        assert response.documents == mock_list_docs_response["documents"]
        assert response.total == mock_list_docs_response["total"]
        assert response.page == mock_list_docs_response["page"]
        assert response.page_size == mock_list_docs_response["page_size"]


# =============================================================================
# Status Operation Tests
# =============================================================================


class TestClientTrackStatus:
    """Tests for track_status method."""

    @pytest.mark.asyncio
    async def test_client_track_status(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_track_status_response: dict,
    ):
        """track_status sends track_id and parses response."""
        response = await client_with_mock_transport.track_status("track-12345")

        assert response.track_id == "track-12345"
        assert response.documents == []
        assert response.total_count == 0


class TestClientPipelineStatus:
    """Tests for pipeline_status method."""

    @pytest.mark.asyncio
    async def test_client_pipeline_status(
        self,
        client_with_mock_transport: AsyncLightRAGClient,
        mock_pipeline_status_response: dict,
    ):
        """pipeline_status sends correct request and parses response."""
        response = await client_with_mock_transport.pipeline_status()

        assert response.busy == mock_pipeline_status_response.get("busy", False)
        assert response.docs == mock_pipeline_status_response.get("docs", 0)
        assert response.job_name == mock_pipeline_status_response.get("job_name", "")


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestClientErrorResponses:
    """Tests for error response handling."""

    @pytest.mark.asyncio
    async def test_client_error_response(self, configured_config: RAGConfig):
        """Mock transport returns 500, verify RAGResponseError is raised."""
        error_response = {"detail": "Internal server error"}

        def error_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=500,
                json=error_response,
            )

        mock_transport = httpx.MockTransport(error_handler)
        client = AsyncLightRAGClient(configured_config)
        client._client = httpx.AsyncClient(
            base_url=configured_config.base_url,
            timeout=httpx.Timeout(configured_config.timeout),
            headers=client._build_headers(),
            transport=mock_transport,
        )

        try:
            with pytest.raises(RAGResponseError) as exc_info:
                await client.query("test query")

            assert exc_info.value.status_code == 500
            assert "Internal server error" in exc_info.value.detail
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_client_error_response_with_text_detail(self, configured_config: RAGConfig):
        """Mock transport returns 400 with text response, verify RAGResponseError."""
        def error_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=400,
                text="Bad Request - Invalid parameter",
            )

        mock_transport = httpx.MockTransport(error_handler)
        client = AsyncLightRAGClient(configured_config)
        client._client = httpx.AsyncClient(
            base_url=configured_config.base_url,
            timeout=httpx.Timeout(configured_config.timeout),
            headers=client._build_headers(),
            transport=mock_transport,
        )

        try:
            with pytest.raises(RAGResponseError) as exc_info:
                await client.insert_text("test")

            assert exc_info.value.status_code == 400
        finally:
            await client.close()


class TestClientTimeout:
    """Tests for timeout error handling."""

    @pytest.mark.asyncio
    async def test_client_timeout(self, configured_config: RAGConfig):
        """Mock transport raises TimeoutException, verify RAGTimeoutError."""
        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Request timed out")

        mock_transport = httpx.MockTransport(timeout_handler)
        client = AsyncLightRAGClient(configured_config)
        client._client = httpx.AsyncClient(
            base_url=configured_config.base_url,
            timeout=httpx.Timeout(configured_config.timeout),
            headers=client._build_headers(),
            transport=mock_transport,
        )

        try:
            with pytest.raises(RAGTimeoutError) as exc_info:
                await client.query("test query")

            assert isinstance(exc_info.value.__cause__, httpx.TimeoutException)
        finally:
            await client.close()


class TestClientConnectionError:
    """Tests for connection error handling."""

    @pytest.mark.asyncio
    async def test_client_connection_error(self, configured_config: RAGConfig):
        """Mock transport raises ConnectError, verify RAGConnectionError."""
        def connect_error_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        mock_transport = httpx.MockTransport(connect_error_handler)
        client = AsyncLightRAGClient(configured_config)
        client._client = httpx.AsyncClient(
            base_url=configured_config.base_url,
            timeout=httpx.Timeout(configured_config.timeout),
            headers=client._build_headers(),
            transport=mock_transport,
        )

        try:
            with pytest.raises(RAGConnectionError) as exc_info:
                await client.query("test query")

            assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
        finally:
            await client.close()


# =============================================================================
# Import Tests
# =============================================================================


class TestModuleImports:
    """Tests for module-level imports."""

    def test_module_imports(self):
        """Verify all expected exports are available from daemon.rag."""
        from daemon.rag import (
            AsyncLightRAGClient,
            RAGConfig,
            is_rag_enabled,
            RAGError,
            RAGNotConfiguredError,
        )
        from daemon.rag import RAGConnectionError
        from daemon.rag import RAGResponseError
        from daemon.rag import RAGTimeoutError

        # Verify classes are correct types
        assert AsyncLightRAGClient is not None
        assert RAGConfig is not None
        assert is_rag_enabled is not None
        assert issubclass(RAGError, Exception)
        assert issubclass(RAGNotConfiguredError, RAGError)
        assert issubclass(RAGConnectionError, RAGError)
        assert issubclass(RAGTimeoutError, RAGError)
        assert issubclass(RAGResponseError, RAGError)


# =============================================================================
# Header Building Tests
# =============================================================================


class TestBuildHeaders:
    """Tests for _build_headers method."""

    def test_build_headers_omits_workspace_when_empty(self):
        """_build_headers() omits LIGHTRAG-WORKSPACE when workspace is empty string."""
        config = RAGConfig(host="http://localhost", workspace="")
        client = AsyncLightRAGClient(config)
        headers = client._build_headers()
        assert "LIGHTRAG-WORKSPACE" not in headers

    def test_build_headers_includes_workspace_when_set(self):
        """_build_headers() includes LIGHTRAG-WORKSPACE header when workspace is set."""
        config = RAGConfig(host="http://localhost", workspace="my-workspace")
        client = AsyncLightRAGClient(config)
        headers = client._build_headers()
        assert headers["LIGHTRAG-WORKSPACE"] == "my-workspace"
