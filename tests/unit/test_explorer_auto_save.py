"""Tests for Explorer auto-save feature in knowledge_tools.py.

Tests cover:
- _save_explorer_result(): file creation, content format, slug generation, timestamps
- append_context_key(): placeholder resolution ({{ENSEMBLE_CONTEXT_KEY}}, {{ENSEMBLE_SHARED_CONTEXT_DIR}})
- Fire-and-forget safety: exceptions don't crash explore()
- Edge cases: empty queries, special characters, long content
"""

import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import tempfile
import re

from daemon.tools.knowledge_tools import _save_explorer_result
from daemon.services.instance_lifecycle import append_context_key


@pytest.fixture
def mock_temp_dir(tmp_path):
    """Provide a temp directory and mock tempfile.gettempdir."""
    with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
        mock_tempfile.gettempdir.return_value = str(tmp_path)
        yield tmp_path


# =============================================================================
# Test Class for _save_explorer_result()
# =============================================================================

class TestSaveExplorerResult:
    """Tests for _save_explorer_result() function."""

    def test_happy_path_creates_file(self, mock_temp_dir):
        """File is created at correct path with expected content."""
        query = "how does auth work"
        result = "Auth is handled by the auth module."
        context_key = "test-context-key"

        _save_explorer_result(query, result, context_key)

        # Verify file was created in correct directory
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        assert context_dir.exists(), f"Context directory not created: {context_dir}"
        assert context_dir.is_dir()

        # Find the created file (should match slug_timestamp.md pattern)
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1, f"Expected 1 .md file, found {len(files)}: {files}"

        file_path = files[0]
        content = file_path.read_text()

        # Verify content format
        assert "# Explorer Result: how does auth work" in content
        assert "**Time**:" in content
        assert "**Project**:" in content
        assert "**Mode**:" in content
        assert "Auth is handled by the auth module." in content

    def test_content_format_includes_metadata(self, mock_temp_dir):
        """Verify metadata header structure: query, time, project, mode."""
        query = "test query"
        result = "Test result content"
        context_key = "my-context"
        project_name = "TestProject"
        mode = "local"

        _save_explorer_result(
            query, result, context_key,
            project_name=project_name, mode=mode
        )

        # Find and read the file
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        file_path = list(context_dir.glob("*.md"))[0]
        content = file_path.read_text()

        # Check metadata header
        assert "# Explorer Result: test query" in content
        assert "**Time**:" in content
        assert "**Project**: TestProject" in content
        assert "**Mode**: local" in content
        # Result should be at the end (after metadata)
        assert "Test result content" in content

    def test_slug_generation_normal_query(self, mock_temp_dir):
        """Normal query produces sensible slug."""
        query = "how does auth work"
        result = "Auth explanation"
        context_key = "test"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1

        file_path = files[0]
        # Filename should start with slug
        assert file_path.name.startswith("how-does-auth-work_")

    def test_slug_generation_special_characters(self, mock_temp_dir):
        """Special characters are replaced with hyphens in slug."""
        query = "what is @#$%?"
        result = "Result"
        context_key = "test"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1

        file_path = files[0]
        # Special chars should be replaced, slug should be clean
        assert "@" not in file_path.name
        assert "#" not in file_path.name
        assert "$" not in file_path.name
        assert "%" not in file_path.name
        assert "?" not in file_path.name

    def test_slug_generation_only_non_alphanumeric(self, mock_temp_dir):
        """Query with only non-alphanumeric chars falls back to 'query'."""
        query = "@#$%"
        result = "Result"
        context_key = "test"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1

        file_path = files[0]
        # Should fall back to "query" slug
        assert file_path.name.startswith("query_")

    def test_slug_generation_long_query_truncated(self, mock_temp_dir):
        """Very long query is truncated to 80 chars in slug."""
        query = "a" * 150  # 150 char query
        result = "Result"
        context_key = "test"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1

        file_path = files[0]
        # Extract slug (everything before the timestamp)
        slug_part = file_path.name.split("_")[0]
        # Slug should be max 80 chars
        assert len(slug_part) <= 80, f"Slug too long: {len(slug_part)} chars"

    def test_timestamp_consistency_filename_and_content(self, mock_temp_dir):
        """Same timestamp is used in filename AND content metadata."""
        query = "test query"
        result = "Result"
        context_key = "test"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1

        file_path = files[0]
        content = file_path.read_text()

        # Extract timestamp from filename - filename is like "slug_YYYYMMDD_HHMMSS.md"
        # The timestamp is the part after the last underscore in stem
        # But slug may contain underscores, so find pattern YYYYMMDD_HHMMSS at end
        filename_pattern = re.search(r'_(\d{8}_\d{6})\.md$', file_path.name)
        assert filename_pattern, f"Could not find timestamp in filename: {file_path.name}"
        filename_ts = filename_pattern.group(1)

        # Extract timestamp from content (ISO format in **Time**: line)
        time_match = re.search(r"\*\*Time\*\*: (.+)", content)
        assert time_match, "Time not found in content"
        content_ts = time_match.group(1)

        # Both timestamps should be from the same moment
        # Filename uses strftime %Y%m%d_%H%M%S (e.g., "20260530_232613")
        # Content uses isoformat (e.g., "2026-05-30T23:26:13.667148")
        # Compare date parts: filename[0:8] vs content[0:10] (YYYYMMDD vs YYYY-MM-DD)
        filename_date = filename_ts[:8]  # "20260530"
        content_date = content_ts[:10].replace("-", "")  # "20260530"
        assert filename_date == content_date, \
            f"Date mismatch: filename={filename_ts}, content={content_ts}"

    def test_context_key_in_path(self, mock_temp_dir):
        """context_key is used in the directory path."""
        query = "test"
        result = "Result"
        context_key = "my-special-key-123"

        _save_explorer_result(query, result, context_key)

        # Verify path includes context_key
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        assert context_dir.exists(), f"Path should include context_key: {context_dir}"

    def test_default_context_key(self, mock_temp_dir):
        """When context_key='default', it's used directly (not special handling)."""
        query = "test"
        result = "Result"
        context_key = "default"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / "default"
        assert context_dir.exists()

    def test_directory_creation_if_not_exists(self, mock_temp_dir):
        """Directory is created if it doesn't exist."""
        query = "test"
        result = "Result"
        context_key = "brand-new-context-key"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        assert context_dir.exists()
        assert context_dir.is_dir()

    def test_empty_query_uses_fallback_slug(self, mock_temp_dir):
        """Empty query string falls back to 'query' slug."""
        query = ""
        result = "Result"
        context_key = "test"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1

        file_path = files[0]
        # Empty query should fall back to "query"
        assert file_path.name.startswith("query_")

    def test_whitespace_only_query(self, mock_temp_dir):
        """Query with only whitespace falls back to 'query' slug."""
        query = "   "
        result = "Result"
        context_key = "test"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1

        file_path = files[0]
        # Whitespace-only should fall back to "query"
        assert file_path.name.startswith("query_")

    def test_long_result_content_saved_correctly(self, mock_temp_dir):
        """Very long result content is saved correctly without truncation."""
        query = "test"
        result = "x" * 100000  # 100KB of content
        context_key = "test"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1

        file_path = files[0]
        content = file_path.read_text()
        # Content should include the full result
        assert content.endswith(result)

    def test_fire_and_forget_swallows_exceptions(self, mock_temp_dir, caplog):
        """Exceptions are logged and swallowed, not raised."""
        import logging
        query = "test"
        result = "Result"
        context_key = "test"

        # Set caplog to capture DEBUG level since the code uses logger.debug
        caplog.set_level(logging.DEBUG, logger="daemon.tools.knowledge_tools")

        # Patch at the actual location where Path.write_text is used
        with patch("daemon.tools.knowledge_tools.Path.write_text") as mock_write:
            mock_write.side_effect = IOError("Disk full")

            # Should NOT raise
            _save_explorer_result(query, result, context_key)

        # Should have logged the error at debug level
        assert any(
            "Failed to save explorer result" in record.message
            for record in caplog.records
        ), f"No debug log found. Records: {[(r.levelname, r.message) for r in caplog.records]}"


# =============================================================================
# Test Class for append_context_key() placeholder resolution
# =============================================================================

class TestAppendContextKeyPlaceholderResolution:
    """Tests for placeholder resolution in append_context_key()."""

    def test_ensemble_context_key_replacement(self):
        """{{ENSEMBLE_CONTEXT_KEY}} is replaced with actual context_key value."""
        system_prompt = "Context key: {{ENSEMBLE_CONTEXT_KEY}}"
        instance_id = "root-instance-123"
        instance_repository = MagicMock()

        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id=None
        )

        assert "{{ENSEMBLE_CONTEXT_KEY}}" not in result
        assert "root-instance-123" in result

    def test_ensemble_shared_context_dir_replacement(self):
        """{{ENSEMBLE_SHARED_CONTEXT_DIR}} is replaced with actual path."""
        system_prompt = "Dir: {{ENSEMBLE_SHARED_CONTEXT_DIR}}"
        instance_id = "my-instance-456"
        instance_repository = MagicMock()

        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id=None
        )

        assert "{{ENSEMBLE_SHARED_CONTEXT_DIR}}" not in result
        # Should contain the temp dir path pattern
        assert "ensemble" in result
        assert "context" in result
        assert "my-instance-456" in result

    def test_both_placeholders_in_string(self):
        """String containing both placeholders gets both replaced."""
        system_prompt = "Key={{ENSEMBLE_CONTEXT_KEY}}, Dir={{ENSEMBLE_SHARED_CONTEXT_DIR}}"
        instance_id = "combined-test"
        instance_repository = MagicMock()

        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id=None
        )

        assert "{{ENSEMBLE_CONTEXT_KEY}}" not in result
        assert "{{ENSEMBLE_SHARED_CONTEXT_DIR}}" not in result
        assert "combined-test" in result
        # Verify path structure exists
        assert "ensemble/context" in result

    def test_no_placeholders_unchanged(self):
        """String without placeholders is returned unchanged (except context key appended)."""
        system_prompt = "You are a helpful assistant."
        instance_id = "no-placeholder-instance"
        instance_repository = MagicMock()

        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id=None
        )

        assert "You are a helpful assistant." in result
        assert "{{ENSEMBLE_CONTEXT_KEY}}" not in result
        assert "{{ENSEMBLE_SHARED_CONTEXT_DIR}}" not in result
        # Context key section should still be appended
        assert "CONTEXT_KEY:" in result

    def test_placeholder_middle_of_text(self):
        """Placeholder in middle of text is replaced correctly."""
        system_prompt = "Prefix {{ENSEMBLE_CONTEXT_KEY}} Suffix"
        instance_id = "middle-placeholder"
        instance_repository = MagicMock()

        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id=None
        )

        assert "{{ENSEMBLE_CONTEXT_KEY}}" not in result
        assert "Prefix" in result
        assert "middle-placeholder" in result
        assert "Suffix" in result

    def test_context_key_from_tree_root(self):
        """When parent_id is set, context_key comes from tree root."""
        system_prompt = "Key: {{ENSEMBLE_CONTEXT_KEY}}"
        instance_id = "child-instance"
        instance_repository = MagicMock()
        instance_repository.get_tree_root_id.return_value = "root-from-tree"

        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id="parent-id"
        )

        # Should use the tree root, not the instance_id
        assert "root-from-tree" in result
        instance_repository.get_tree_root_id.assert_called_once_with("parent-id")

    def test_shared_context_dir_uses_root_id(self):
        """{{ENSEMBLE_SHARED_CONTEXT_DIR}} uses tree root ID when parent_id is set."""
        system_prompt = "Dir: {{ENSEMBLE_SHARED_CONTEXT_DIR}}"
        instance_id = "child"
        instance_repository = MagicMock()
        instance_repository.get_tree_root_id.return_value = "tree-root-id"

        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id="parent"
        )

        # Directory should use tree root
        assert "tree-root-id" in result


# =============================================================================
# Integration-style tests for _save_explorer_result
# =============================================================================

class TestSaveExplorerResultIntegration:
    """Integration-style tests for _save_explorer_result with explore tool context."""

    def test_result_includes_project_name_when_provided(self, mock_temp_dir):
        """File content includes project name when project_name is provided."""
        query = "test"
        result = "Result"
        context_key = "test"
        project_name = "MyTestProject"

        _save_explorer_result(
            query, result, context_key, project_name=project_name
        )

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        file_path = list(context_dir.glob("*.md"))[0]
        content = file_path.read_text()

        assert "**Project**: MyTestProject" in content

    def test_result_uses_unknown_project_when_not_provided(self, mock_temp_dir):
        """File content shows 'unknown' when project_name is None."""
        query = "test"
        result = "Result"
        context_key = "test"

        _save_explorer_result(query, result, context_key, project_name=None)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        file_path = list(context_dir.glob("*.md"))[0]
        content = file_path.read_text()

        assert "**Project**: unknown" in content

    def test_mode_parameter_in_content(self, mock_temp_dir):
        """Mode parameter is included in metadata header."""
        query = "test"
        result = "Result"
        context_key = "test"
        mode = "global"

        _save_explorer_result(query, result, context_key, mode=mode)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        file_path = list(context_dir.glob("*.md"))[0]
        content = file_path.read_text()

        assert "**Mode**: global" in content

    def test_multiple_calls_create_separate_files(self, mock_temp_dir):
        """Multiple calls create separate timestamped files."""
        context_key = "test"

        # Call twice
        _save_explorer_result("query1", "result1", context_key)
        _save_explorer_result("query2", "result2", context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))

        assert len(files) == 2, f"Expected 2 files, got {len(files)}: {[f.name for f in files]}"

        # Both should exist with different content
        contents = [f.read_text() for f in files]
        assert any("result1" in c for c in contents)
        assert any("result2" in c for c in contents)

    def test_different_context_keys_create_separate_directories(self, mock_temp_dir):
        """Different context_keys create separate directories."""
        _save_explorer_result("query", "result1", "context-a")
        _save_explorer_result("query", "result2", "context-b")

        # Both directories should exist
        assert (mock_temp_dir / "ensemble" / "context" / "context-a").exists()
        assert (mock_temp_dir / "ensemble" / "context" / "context-b").exists()

    def test_unicode_query_handled(self, mock_temp_dir):
        """Unicode characters in query are handled without crashing."""
        query = "café résumé"  # Unicode characters
        result = "Result"
        context_key = "unicode-test"

        # Should not raise
        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1
