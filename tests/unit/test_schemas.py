"""Tests for Pydantic schemas in daemon.routers.schemas.

Verifies JobCreateRequest field validators, especially project_id normalization.
"""

import pytest
from daemon import constants
from daemon.routers.schemas import JobCreateRequest
from daemon.services import project_normalizer


# Valid UUID for testing (not real, but correctly formatted)
VALID_UUID = "12345678-1234-1234-1234-123456789abc"
TEST_DEFAULT_PROJECT_ID = "test-system-default-project-id"


class TestJobCreateRequestProjectIdNormalization:
    """Tests for JobCreateRequest.project_id field normalization."""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Set up test value before tests, reset after."""
        original_value = constants.SYSTEM_DEFAULT_PROJECT_ID
        constants.SYSTEM_DEFAULT_PROJECT_ID = TEST_DEFAULT_PROJECT_ID
        yield
        constants.SYSTEM_DEFAULT_PROJECT_ID = original_value

    def _make_request(self, **kwargs):
        """Helper to create JobCreateRequest with defaults for required fields."""
        defaults = {"agent_id": "test-agent", "message": "Test job message"}
        defaults.update(kwargs)
        return JobCreateRequest(**defaults)

    def test_job_create_request_none_project_id_normalized(self):
        """project_id=None should be normalized to SYSTEM_DEFAULT_PROJECT_ID."""
        request = self._make_request(project_id=None)
        assert request.project_id == TEST_DEFAULT_PROJECT_ID

    def test_job_create_request_empty_project_id_normalized(self):
        """project_id='' should be normalized to SYSTEM_DEFAULT_PROJECT_ID."""
        request = self._make_request(project_id="")
        assert request.project_id == TEST_DEFAULT_PROJECT_ID

    def test_job_create_request_valid_uuid_unchanged(self):
        """A valid UUID project_id should pass through unchanged."""
        request = self._make_request(project_id=VALID_UUID)
        assert request.project_id == VALID_UUID

    def test_job_create_request_whitespace_normalized(self):
        """project_id='   ' (whitespace-only) should be normalized."""
        request = self._make_request(project_id="   ")
        assert request.project_id == TEST_DEFAULT_PROJECT_ID


class TestJobCreateRequestOtherFields:
    """Tests for other JobCreateRequest fields and validators."""

    def _make_request(self, **kwargs):
        """Helper to create JobCreateRequest with defaults for required fields."""
        defaults = {"agent_id": "test-agent", "message": "Test job message"}
        defaults.update(kwargs)
        return JobCreateRequest(**defaults)

    def test_job_create_request_default_priority(self):
        """Default priority should be 5."""
        request = self._make_request()
        assert request.priority == 5

    def test_job_create_request_default_source(self):
        """Default source should be 'api'."""
        request = self._make_request()
        assert request.source == "api"

    def test_job_create_request_priority_valid_values(self):
        """Priority values 1-10 should be accepted."""
        for p in [1, 5, 10]:
            request = self._make_request(priority=p)
            assert request.priority == p

    def test_job_create_request_priority_too_low_rejected(self):
        """Priority < 1 should be rejected."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            self._make_request(priority=0)

    def test_job_create_request_priority_too_high_rejected(self):
        """Priority > 10 should be rejected."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            self._make_request(priority=11)
