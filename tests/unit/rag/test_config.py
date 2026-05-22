"""Tests for RAG configuration functions (daemon.rag.config).

Tests auto_test_rag(), is_rag_enabled(), disable_rag(), and enable_rag()
functions, including error handling and flag management.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from daemon.rag import (
    RAGConfig,
    auto_test_rag,
    disable_rag,
    enable_rag,
    is_rag_enabled,
)


# =============================================================================
# Helper Functions
# =============================================================================


def reset_rag_state():
    """Reset the module-level RAG state before each test."""
    enable_rag()


@pytest.fixture(autouse=True)
def clean_rag_state():
    """Ensure RAG state is clean before and after each test."""
    enable_rag()  # Reset to default enabled state
    yield
    enable_rag()  # Reset after test


# =============================================================================
# Tests for is_rag_enabled / disable_rag / enable_rag
# =============================================================================


class TestIsRagEnabled:
    """Tests for is_rag_enabled function."""

    def test_is_rag_enabled_true_when_configured(self, configured_env):
        """Returns True when LIGHTRAG_HOST is set and not disabled."""
        assert is_rag_enabled() is True

    def test_is_rag_enabled_false_when_not_configured(self, unconfigured_env):
        """Returns False when LIGHTRAG_HOST is not set."""
        assert is_rag_enabled() is False

    def test_is_rag_enabled_false_after_disable(self, configured_env):
        """Returns False after disable_rag() is called, even when configured."""
        disable_rag()
        try:
            assert is_rag_enabled() is False
        finally:
            enable_rag()

    def test_is_rag_enabled_true_after_disable_and_enable(self, configured_env):
        """Returns True after disable_rag() followed by enable_rag()."""
        disable_rag()
        enable_rag()
        assert is_rag_enabled() is True


class TestDisableRag:
    """Tests for disable_rag function."""

    def test_disable_rag_sets_flag(self, configured_env):
        """disable_rag() sets the module-level flag to False."""
        disable_rag()
        assert is_rag_enabled() is False

    def test_disable_rag_is_idempotent(self, configured_env):
        """Calling disable_rag() multiple times is safe."""
        disable_rag()
        disable_rag()
        assert is_rag_enabled() is False


class TestEnableRag:
    """Tests for enable_rag function."""

    def test_enable_rag_sets_flag(self, configured_env):
        """enable_rag() sets the module-level flag to True."""
        disable_rag()
        enable_rag()
        assert is_rag_enabled() is True

    def test_enable_rag_is_idempotent(self, configured_env):
        """Calling enable_rag() multiple times is safe."""
        enable_rag()
        enable_rag()
        assert is_rag_enabled() is True


# =============================================================================
# Tests for auto_test_rag
# =============================================================================


class TestAutoTestRagNotConfigured:
    """Tests for auto_test_rag when RAG is not configured."""

    @pytest.mark.asyncio
    async def test_auto_test_rag_skips_when_host_not_set(self, unconfigured_env, caplog):
        """When LIGHTRAG_HOST is not set, auto_test_rag returns False and logs debug."""
        import logging

        caplog.set_level(logging.DEBUG)

        result = await auto_test_rag()

        assert result is False
        # Verify debug log about not configured
        assert any(
            "skipped" in record.message.lower() and "not configured" in record.message.lower()
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_auto_test_rag_does_not_disable_when_not_configured(self, unconfigured_env):
        """When LIGHTRAG_HOST is not set, auto_test_rag does not call disable_rag."""
        # The internal _rag_enabled flag should remain True after skipping
        # (we test this indirectly by checking disable_rag wasn't called)
        enable_rag()

        await auto_test_rag()

        # The module-level _rag_enabled flag should still be True
        # Note: is_rag_enabled() also checks config, so it returns False
        # when not configured, but that's expected behavior
        import daemon.rag.config as rag_config_module
        assert rag_config_module._rag_enabled is True


class TestAutoTestRagSuccess:
    """Tests for auto_test_rag when RAG connection succeeds."""

    @pytest.mark.asyncio
    async def test_auto_test_rag_returns_true_on_success(self, configured_env):
        """When server responds 200, auto_test_rag returns True."""
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await auto_test_rag()

        assert result is True

    @pytest.mark.asyncio
    async def test_auto_test_rag_leaves_rag_enabled_on_success(self, configured_env):
        """When server responds 200, RAG remains enabled."""
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await auto_test_rag()

        assert is_rag_enabled() is True


class TestAutoTestRagTimeout:
    """Tests for auto_test_rag when timeout occurs."""

    @pytest.mark.asyncio
    async def test_auto_test_rag_returns_false_on_timeout(self, configured_env, caplog):
        """When httpx.TimeoutException is raised, auto_test_rag returns False."""
        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await auto_test_rag()

        assert result is False
        assert any("timeout" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_auto_test_rag_disables_on_timeout(self, configured_env):
        """When httpx.TimeoutException is raised, RAG is disabled."""
        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await auto_test_rag()

        assert is_rag_enabled() is False


class TestAutoTestRagHTTPStatusError:
    """Tests for auto_test_rag when HTTP errors occur."""

    @pytest.mark.asyncio
    async def test_auto_test_rag_returns_false_on_401(self, configured_env, caplog):
        """When server returns 401, auto_test_rag returns False."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json = MagicMock(return_value={"detail": "Unauthorized"})
        mock_response.text = ""

        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "401 Unauthorized",
                    request=MagicMock(),
                    response=mock_response,
                )
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await auto_test_rag()

        assert result is False
        assert any("401" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_auto_test_rag_disables_on_401(self, configured_env):
        """When server returns 401, RAG is disabled."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json = MagicMock(return_value={"detail": "Unauthorized"})
        mock_response.text = ""

        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "401 Unauthorized",
                    request=MagicMock(),
                    response=mock_response,
                )
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await auto_test_rag()

        assert is_rag_enabled() is False

    @pytest.mark.asyncio
    async def test_auto_test_rag_returns_false_on_500(self, configured_env, caplog):
        """When server returns 500, auto_test_rag returns False."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json = MagicMock(return_value={"detail": "Internal Server Error"})
        mock_response.text = ""

        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "500 Internal Server Error",
                    request=MagicMock(),
                    response=mock_response,
                )
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await auto_test_rag()

        assert result is False
        assert any("500" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_auto_test_rag_disables_on_500(self, configured_env):
        """When server returns 500, RAG is disabled."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json = MagicMock(return_value={"detail": "Internal Server Error"})
        mock_response.text = ""

        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "500 Internal Server Error",
                    request=MagicMock(),
                    response=mock_response,
                )
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await auto_test_rag()

        assert is_rag_enabled() is False


class TestAutoTestRagConnectError:
    """Tests for auto_test_rag when connection is refused."""

    @pytest.mark.asyncio
    async def test_auto_test_rag_returns_false_on_connect_error(self, configured_env, caplog):
        """When httpx.ConnectError is raised, auto_test_rag returns False."""
        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await auto_test_rag()

        assert result is False
        assert any("connection" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_auto_test_rag_disables_on_connect_error(self, configured_env):
        """When httpx.ConnectError is raised, RAG is disabled."""
        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await auto_test_rag()

        assert is_rag_enabled() is False


class TestAutoTestRagUnexpectedError:
    """Tests for auto_test_rag when unexpected exceptions occur."""

    @pytest.mark.asyncio
    async def test_auto_test_rag_returns_false_on_unexpected_error(self, configured_env, caplog):
        """When a generic Exception is raised, auto_test_rag returns False."""
        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=ValueError("Unexpected error")
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await auto_test_rag()

        assert result is False
        # Should log the exception type and message
        assert any("ValueError" in record.message or "Unexpected error" in record.message
                   for record in caplog.records)

    @pytest.mark.asyncio
    async def test_auto_test_rag_disables_on_unexpected_error(self, configured_env):
        """When a generic Exception is raised, RAG is disabled."""
        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=ValueError("Unexpected error")
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await auto_test_rag()

        assert is_rag_enabled() is False


class TestAutoTestRagIntegration:
    """Integration tests for auto_test_rag with is_rag_enabled."""

    @pytest.mark.asyncio
    async def test_is_rag_enabled_reflects_auto_test_failure(self, configured_env):
        """After auto_test_rag fails, is_rag_enabled() returns False."""
        # Verify initial state
        assert is_rag_enabled() is True

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json = MagicMock(return_value={"detail": "Server Error"})
        mock_response.text = ""

        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "500 Server Error",
                    request=MagicMock(),
                    response=mock_response,
                )
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await auto_test_rag()

        # After failed auto-test, is_rag_enabled should be False
        assert is_rag_enabled() is False

    @pytest.mark.asyncio
    async def test_auto_test_failure_can_be_recovered(self, configured_env):
        """RAG can be re-enabled after auto_test failure via enable_rag()."""
        # First, cause a failure
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json = MagicMock(return_value={"detail": "Server Error"})
        mock_response.text = ""

        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "500 Server Error",
                    request=MagicMock(),
                    response=mock_response,
                )
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await auto_test_rag()

        assert is_rag_enabled() is False

        # Recover by re-enabling
        enable_rag()
        assert is_rag_enabled() is True

    @pytest.mark.asyncio
    async def test_auto_test_succeeds_then_is_rag_enabled_true(self, configured_env):
        """When auto_test succeeds, subsequent is_rag_enabled() calls return True."""
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        with patch("daemon.rag.config.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await auto_test_rag()

        assert result is True
        assert is_rag_enabled() is True
