"""
E2E test for MCP KB Server tools.

Requires a running dev server at http://localhost:8079.
Run with: RUN_E2E_TESTS=1 pytest tests/e2e/test_mcp_kb_e2e.py -v

The test connects via MCP StreamableHTTP transport to validate:
1. Server initialization
2. Tool listing (ensemble_kb_explore, ensemble_kb_experience)
3. ensemble_kb_explore tool execution
4. ensemble_kb_experience tool execution
"""

import os
import pytest
import pytest_asyncio

# All tests in this file require live LLM infrastructure (real OpenAI API + MCP),
# so they are excluded from the default non-integration test gate via the
# `integration` marker defined in pyproject.toml.
pytestmark = [
    pytest.mark.integration,
    # Skip all tests if RUN_E2E_TESTS is not set to "1"
    pytest.mark.skipif(
        os.environ.get("RUN_E2E_TESTS") != "1",
        reason="E2E tests require RUN_E2E_TESTS=1 and a running dev server at http://localhost:8079"
    ),
]

# Configuration
MCP_HTTP_URL = "http://localhost:8079/api/mcp/kb/mcp"
MCP_SSE_URL = "http://localhost:8079/api/mcp/kb/sse/sse"
TEST_PROJECT_ID = os.environ.get("E2E_PROJECT_ID", "")


@pytest_asyncio.fixture
async def mcp_client():
    """Create MCP client connected to the dev server via StreamableHTTP."""
    import httpx
    from mcp.client.streamable_http import streamable_http_client
    from mcp import ClientSession

    # First verify server is reachable
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://localhost:8079/api/health",
                timeout=5
            )
            if response.status_code != 200:
                pytest.skip("Dev server health check failed")
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        pytest.skip(f"Dev server not reachable: {e}")

    # Create MCP client
    async with streamable_http_client(MCP_HTTP_URL) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


@pytest.mark.asyncio
async def test_server_initialization(mcp_client):
    """Test that the MCP server initializes and responds to initialize."""
    # If we get here, initialization worked (fixture does it)
    assert mcp_client is not None


@pytest.mark.asyncio
async def test_tools_listed(mcp_client):
    """Test that all KB tools are discoverable."""
    tools_result = await mcp_client.list_tools()
    tool_names = [t.name for t in tools_result.tools]

    expected_tools = [
        "ensemble_kb_explore",
        "ensemble_kb_experience",
        "ensemble_kb_list_projects",
        "ensemble_kb_search_projects",
    ]
    for tool in expected_tools:
        assert tool in tool_names, f"{tool} not found in {tool_names}"


@pytest.mark.asyncio
async def test_explore_tool(mcp_client):
    """Test ensemble_kb_explore returns a response (may be error if RAG not configured)."""
    if not TEST_PROJECT_ID:
        pytest.skip("E2E_PROJECT_ID not set")

    result = await mcp_client.call_tool(
        "ensemble_kb_explore",
        {"query": "test query", "project_id": TEST_PROJECT_ID, "mode": "hybrid"}
    )

    # Result should be a CallToolResult with content
    assert result is not None
    assert result.content is not None
    # The response is text content
    text_content = [c for c in result.content if hasattr(c, "text")]
    assert len(text_content) > 0


@pytest.mark.asyncio
async def test_experience_tool(mcp_client):
    """Test ensemble_kb_experience returns confirmation (may be error if RAG not configured)."""
    if not TEST_PROJECT_ID:
        pytest.skip("E2E_PROJECT_ID not set")

    result = await mcp_client.call_tool(
        "ensemble_kb_experience",
        {"text": "E2E test knowledge entry", "project_id": TEST_PROJECT_ID}
    )

    assert result is not None
    assert result.content is not None
    text_content = [c for c in result.content if hasattr(c, "text")]
    assert len(text_content) > 0


@pytest.mark.asyncio
async def test_list_projects_tool(mcp_client):
    """Test listing projects via MCP tool."""
    result = await mcp_client.call_tool("ensemble_kb_list_projects", {})

    assert result is not None
    assert result.content is not None
    text_content = [c for c in result.content if hasattr(c, "text")]
    assert len(text_content) > 0
    # Should return JSON array
    import json
    data = json.loads(text_content[0].text)
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_search_projects_tool(mcp_client):
    """Test searching projects via MCP tool."""
    result = await mcp_client.call_tool("ensemble_kb_search_projects", {"query": "agents"})

    assert result is not None
    assert result.content is not None
    text_content = [c for c in result.content if hasattr(c, "text")]
    assert len(text_content) > 0
    import json
    data = json.loads(text_content[0].text)
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_explore_with_project_name(mcp_client):
    """Test explore with project_name instead of project_id."""
    # Skip if no project configured
    if not TEST_PROJECT_ID:
        pytest.skip("E2E_PROJECT_ID not set")

    result = await mcp_client.call_tool("ensemble_kb_explore", {
        "query": "architecture",
        "project_name": "agents-ensemble",
    })

    assert result is not None
    assert result.content is not None
