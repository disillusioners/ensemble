"""Unit tests for project_normalizer.normalize_project_id()."""

import pytest

from daemon.services import project_normalizer
from daemon import constants


# ── Fixtures ────────────────────────────────────────────────────────────────────

TEST_SYSTEM_PROJECT_ID = "test-system-project-id"


@pytest.fixture(autouse=True)
def setup_system_project_id():
    """Set up SYSTEM_DEFAULT_PROJECT_ID before each test and reset after."""
    # Both modules have their own binding of SYSTEM_DEFAULT_PROJECT_ID
    # due to the import in project_normalizer.py
    original_in_constants = constants.SYSTEM_DEFAULT_PROJECT_ID
    original_in_normalizer = project_normalizer.SYSTEM_DEFAULT_PROJECT_ID

    constants.SYSTEM_DEFAULT_PROJECT_ID = TEST_SYSTEM_PROJECT_ID
    project_normalizer.SYSTEM_DEFAULT_PROJECT_ID = TEST_SYSTEM_PROJECT_ID

    yield

    constants.SYSTEM_DEFAULT_PROJECT_ID = original_in_constants
    project_normalizer.SYSTEM_DEFAULT_PROJECT_ID = original_in_normalizer


# ── Tests ───────────────────────────────────────────────────────────────────────

class TestNormalizeProjectId:
    """Tests for normalize_project_id() function."""

    def test_normalize_none_returns_system_id(self):
        """normalize_project_id(None) returns SYSTEM_DEFAULT_PROJECT_ID."""
        result = project_normalizer.normalize_project_id(None)
        assert result == TEST_SYSTEM_PROJECT_ID

    def test_normalize_empty_string_returns_system_id(self):
        """normalize_project_id('') returns SYSTEM_DEFAULT_PROJECT_ID."""
        result = project_normalizer.normalize_project_id("")
        assert result == TEST_SYSTEM_PROJECT_ID

    def test_normalize_whitespace_returns_system_id(self):
        """normalize_project_id('   ') returns SYSTEM_DEFAULT_PROJECT_ID."""
        result = project_normalizer.normalize_project_id("   ")
        assert result == TEST_SYSTEM_PROJECT_ID

    def test_normalize_null_string_returns_system_id(self):
        """normalize_project_id('null') returns SYSTEM_DEFAULT_PROJECT_ID."""
        result = project_normalizer.normalize_project_id("null")
        assert result == TEST_SYSTEM_PROJECT_ID

    def test_normalize_none_string_returns_system_id(self):
        """normalize_project_id('none') returns SYSTEM_DEFAULT_PROJECT_ID."""
        result = project_normalizer.normalize_project_id("none")
        assert result == TEST_SYSTEM_PROJECT_ID

    def test_normalize_case_insensitive(self):
        """NULL, None, NONE all return SYSTEM_DEFAULT_PROJECT_ID."""
        assert project_normalizer.normalize_project_id("NULL") == TEST_SYSTEM_PROJECT_ID
        assert project_normalizer.normalize_project_id("None") == TEST_SYSTEM_PROJECT_ID
        assert project_normalizer.normalize_project_id("NONE") == TEST_SYSTEM_PROJECT_ID
        assert project_normalizer.normalize_project_id("Null") == TEST_SYSTEM_PROJECT_ID
        assert project_normalizer.normalize_project_id("nOne") == TEST_SYSTEM_PROJECT_ID

    def test_normalize_valid_uuid_unchanged(self):
        """normalize_project_id('some-valid-uuid') returns 'some-valid-uuid'."""
        result = project_normalizer.normalize_project_id("some-valid-uuid")
        assert result == "some-valid-uuid"

    def test_normalize_valid_uuid_preserves_original(self):
        """normalize_project_id preserves original input for valid UUIDs."""
        # The function returns the original input, not stripped
        original = "  some-uuid-with-spaces  "
        result = project_normalizer.normalize_project_id(original)
        assert result == original

    def test_raises_when_system_id_not_set(self):
        """With SYSTEM_DEFAULT_PROJECT_ID = None, calling normalize_project_id() raises RuntimeError."""
        # Temporarily set to None to test the error case
        # Must update both bindings since project_normalizer has its own import
        constants.SYSTEM_DEFAULT_PROJECT_ID = None
        project_normalizer.SYSTEM_DEFAULT_PROJECT_ID = None
        with pytest.raises(RuntimeError, match="before system default project was initialized"):
            project_normalizer.normalize_project_id("any-project-id")
