"""Tests for HTTP exception helper functions."""

import pytest
from fastapi import HTTPException

from daemon.utils import (
    raise_not_found,
    raise_service_unavailable,
    raise_bad_request,
)


class TestRaiseNotFound:
    """Tests for raise_not_found helper."""

    def test_raises_404_status_code(self):
        """Should raise HTTPException with status_code 404."""
        with pytest.raises(HTTPException) as exc_info:
            raise_not_found()
        
        assert exc_info.value.status_code == 404

    def test_default_detail_message(self):
        """Should use default detail message when none provided."""
        with pytest.raises(HTTPException) as exc_info:
            raise_not_found()
        
        assert exc_info.value.detail == "Resource not found"

    def test_custom_detail_message(self):
        """Should use custom detail message when provided."""
        custom_message = "Agent not found: my-agent"
        
        with pytest.raises(HTTPException) as exc_info:
            raise_not_found(custom_message)
        
        assert exc_info.value.detail == custom_message


class TestRaiseServiceUnavailable:
    """Tests for raise_service_unavailable helper."""

    def test_raises_503_status_code(self):
        """Should raise HTTPException with status_code 503."""
        with pytest.raises(HTTPException) as exc_info:
            raise_service_unavailable()
        
        assert exc_info.value.status_code == 503

    def test_default_detail_message(self):
        """Should use default detail message when none provided."""
        with pytest.raises(HTTPException) as exc_info:
            raise_service_unavailable()
        
        assert exc_info.value.detail == "Service not initialized"

    def test_custom_detail_message(self):
        """Should use custom detail message when provided."""
        custom_message = "Registry not initialized"
        
        with pytest.raises(HTTPException) as exc_info:
            raise_service_unavailable(custom_message)
        
        assert exc_info.value.detail == custom_message


class TestRaiseBadRequest:
    """Tests for raise_bad_request helper."""

    def test_raises_400_status_code(self):
        """Should raise HTTPException with status_code 400."""
        with pytest.raises(HTTPException) as exc_info:
            raise_bad_request()
        
        assert exc_info.value.status_code == 400

    def test_default_detail_message(self):
        """Should use default detail message when none provided."""
        with pytest.raises(HTTPException) as exc_info:
            raise_bad_request()
        
        assert exc_info.value.detail == "Bad request"

    def test_custom_detail_message(self):
        """Should use custom detail message when provided."""
        custom_message = "Invalid agent ID format"
        
        with pytest.raises(HTTPException) as exc_info:
            raise_bad_request(custom_message)
        
        assert exc_info.value.detail == custom_message


class TestHttpExceptionHelpersEdgeCases:
    """Edge case tests for HTTP exception helpers."""

    def test_empty_string_detail(self):
        """Should accept empty string as detail."""
        with pytest.raises(HTTPException) as exc_info:
            raise_not_found("")
        
        assert exc_info.value.detail == ""

    def test_multiline_detail(self):
        """Should accept multiline detail message."""
        multiline = "Line 1\nLine 2\nLine 3"
        
        with pytest.raises(HTTPException) as exc_info:
            raise_bad_request(multiline)
        
        assert exc_info.value.detail == multiline

    def test_unicode_detail(self):
        """Should accept unicode characters in detail."""
        unicode_message = "Agent not found: 我的代理"
        
        with pytest.raises(HTTPException) as exc_info:
            raise_not_found(unicode_message)
        
        assert exc_info.value.detail == unicode_message
