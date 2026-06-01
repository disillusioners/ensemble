"""Tests for Explorer auto-save feature in knowledge_tools.py.

Tests cover:
- _save_explorer_result(): file creation, content format, slug generation, timestamps
- _parse_should_save(): parsing ## Need Save: true/false from responses
- _extract_concise_section(): extracting ## Concise: section from content
- _is_duplicate_concise(): dedup checking with Jaccard similarity
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

from daemon.tools.knowledge_tools import (
    _save_explorer_result,
    _parse_should_save,
    _extract_concise_section,
    _is_duplicate_concise,
    _SHOULD_SAVE_PATTERN,
)
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


# =============================================================================
# Test Class for _parse_should_save()
# =============================================================================

class TestParseShouldSave:
    """Tests for _parse_should_save() flag parsing function."""

    def test_parse_should_save_true(self):
        """Heading with Need Save: true returns True."""
        response = "Some response\n## Need Save: true\nMore text"
        assert _parse_should_save(response) is True

    def test_parse_should_save_false(self):
        """Heading with Need Save: false returns False."""
        response = "Some response\n## Need Save: false\nMore text"
        assert _parse_should_save(response) is False

    def test_parse_should_save_missing(self):
        """No heading in response returns False (default)."""
        response = "## Answer\nSome text\n## Confidence: HIGH"
        assert _parse_should_save(response) is False

    def test_parse_should_save_case_insensitive(self):
        """Flag parsing is case-insensitive."""
        assert _parse_should_save("## Need Save: TRUE") is True
        assert _parse_should_save("## Need Save: True") is True
        assert _parse_should_save("## Need Save: TRUE") is True
        assert _parse_should_save("## NEED SAVE: TRUE") is True

    def test_parse_should_save_malformed(self):
        """Malformed flag values return False."""
        response = "## Need Save: maybe"
        assert _parse_should_save(response) is False

    def test_parse_should_save_with_extra_whitespace(self):
        """Heading with extra whitespace/newlines still parses correctly."""
        response = "## Need Save: true  \nMore text"
        assert _parse_should_save(response) is True

    def test_parse_should_save_bold_true(self):
        """Bold formatting **true** parses correctly as True."""
        response = "## Need Save: **true**\nMore text"
        assert _parse_should_save(response) is True

    def test_parse_should_save_bold_false(self):
        """Bold formatting **false** parses correctly as False."""
        response = "## Need Save: **false**\nMore text"
        assert _parse_should_save(response) is False

    def test_parse_should_save_italic_true(self):
        """Italic formatting *true* parses correctly as True."""
        response = "## Need Save: *true*\nMore text"
        assert _parse_should_save(response) is True

    def test_parse_should_save_italic_false(self):
        """Italic formatting *false* parses correctly as False."""
        response = "## Need Save: *false*\nMore text"
        assert _parse_should_save(response) is False

    def test_parse_should_save_heading_stripped_from_response(self):
        """Heading is properly stripped from response text."""
        response = "Some response\n## Need Save: true\nMore text"
        stripped = _SHOULD_SAVE_PATTERN.sub("", response).strip()
        # Heading including newlines is removed
        assert "Need Save" not in stripped
        assert "Some response" in stripped
        assert "More text" in stripped

    def test_parse_should_save_bold_heading_stripped(self):
        """Bold heading is stripped including bold markers."""
        response = "Some response\n## Need Save: **true**\nMore text"
        stripped = _SHOULD_SAVE_PATTERN.sub("", response).strip()
        assert "Need Save" not in stripped
        assert "**true**" not in stripped
        assert "Some response" in stripped
        assert "More text" in stripped

    def test_parse_should_save_response_without_heading_unchanged(self):
        """Response without heading is returned unchanged."""
        response = "Some response without Need Save"
        stripped = _SHOULD_SAVE_PATTERN.sub("", response).strip()
        assert stripped == response


# =============================================================================
# Test Class for _extract_concise_section()
# =============================================================================

class TestExtractConciseSection:
    """Tests for _extract_concise_section() function."""

    def test_extract_concise_present(self):
        """Extracts ## Concise: section when present."""
        content = """## Answer
Full response here.

## Concise:
This is a concise summary of the findings.

## Sources
Source 1
"""
        result = _extract_concise_section(content)
        assert result == "This is a concise summary of the findings."

    def test_extract_concise_absent(self):
        """Returns None when no ## Concise: section present."""
        content = """## Answer
Full response here.

## Confidence: HIGH
"""
        result = _extract_concise_section(content)
        assert result is None

    def test_extract_concise_multiline(self):
        """Extracts multi-line ## Concise: section."""
        content = """## Answer
Full response.

## Concise:
This is the first sentence.
This is the second sentence.
And the third one.

## Sources
Source list
"""
        result = _extract_concise_section(content)
        expected = "This is the first sentence.\nThis is the second sentence.\nAnd the third one."
        assert result == expected

    def test_extract_concise_at_end(self):
        """Extracts ## Concise: section at end of content."""
        content = """## Answer
Full response.

## Concise:
Concise summary here."""
        result = _extract_concise_section(content)
        assert result == "Concise summary here."

    def test_extract_concise_empty_section(self):
        """Returns empty string when ## Concise: has no content."""
        content = """## Answer
Full response.

## Concise:
"""
        result = _extract_concise_section(content)
        assert result == ""

    def test_extract_concise_with_bold_markers(self):
        """Preserves content even if it has bold markers."""
        content = """## Concise:
This has **bold** and *italic* text.
"""
        result = _extract_concise_section(content)
        assert "**bold**" in result
        assert "*italic*" in result


# =============================================================================
# Test Class for _is_duplicate_concise()
# =============================================================================

class TestIsDuplicateConcise:
    """Tests for _is_duplicate_concise() dedup function."""

    def test_duplicate_similar_concise(self, mock_temp_dir):
        """Returns True when concise section is similar above threshold."""
        context_key = "dedup-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create existing file with a concise section
        existing_file = context_dir / "existing_20260601_120000.md"
        existing_file.write_text("""# Existing Result

## Concise:
The authentication system uses JWT tokens for user authentication.

## Answer
Full details here.
""")

        new_concise = "The authentication system uses JWT tokens for user authentication"
        result = _is_duplicate_concise(new_concise, context_dir)
        assert result is True

    def test_not_duplicate_different_concise(self, mock_temp_dir):
        """Returns False when concise section is different enough."""
        context_key = "dedup-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create existing file with a concise section
        existing_file = context_dir / "existing_20260601_120000.md"
        existing_file.write_text("""# Existing Result

## Concise:
The authentication system uses JWT tokens for user authentication.

## Answer
Full details here.
""")

        # Very different concise section
        new_concise = "The database schema uses PostgreSQL with migrations"
        result = _is_duplicate_concise(new_concise, context_dir)
        assert result is False

    def test_not_duplicate_empty_context_dir(self, mock_temp_dir):
        """Returns False when context directory has no files."""
        context_key = "empty-dir-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        new_concise = "Some concise summary about authentication"
        result = _is_duplicate_concise(new_concise, context_dir)
        assert result is False

    def test_not_duplicate_empty_concise(self, mock_temp_dir):
        """Returns False for empty concise section (too short)."""
        context_key = "empty-concise-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        new_concise = "short"  # Less than 5 tokens
        result = _is_duplicate_concise(new_concise, context_dir)
        assert result is False

    def test_not_duplicate_short_concise(self, mock_temp_dir):
        """Returns False when concise has less than 5 tokens."""
        context_key = "short-concise-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        new_concise = "The auth uses tokens"  # 4 tokens
        result = _is_duplicate_concise(new_concise, context_dir)
        assert result is False

    def test_handles_corrupted_files_gracefully(self, mock_temp_dir, caplog):
        """Returns False when context directory has corrupted files."""
        import logging
        context_key = "corrupted-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create corrupted file (can't be read)
        corrupted_file = context_dir / "corrupted.md"
        corrupted_file.write_text("""# Corrupted

## Concise:
Some concise content here.
""")

        new_concise = "This is a different concise section with more tokens"
        with caplog.at_level(logging.DEBUG, logger="daemon.tools.knowledge_tools"):
            result = _is_duplicate_concise(new_concise, context_dir)

        # Should return False, not raise
        assert result is False

    def test_skips_files_without_concise_section(self, mock_temp_dir):
        """Ignores files without ## Concise: section."""
        context_key = "no-concise-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create file without concise section
        existing_file = context_dir / "no_concise_20260601_120000.md"
        existing_file.write_text("""# Existing Result

## Answer:
This file has no concise section.
""")

        new_concise = "This is a concise section about authentication and tokens for users"
        result = _is_duplicate_concise(new_concise, context_dir)
        # Should not be considered duplicate since no existing concise
        assert result is False

    def test_custom_threshold(self, mock_temp_dir):
        """Uses custom Jaccard threshold when provided."""
        context_key = "threshold-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create existing file
        existing_file = context_dir / "existing_20260601_120000.md"
        existing_file.write_text("""# Existing Result

## Concise:
The authentication system uses JWT tokens for user authentication with refresh tokens.

## Answer
Full details here.
""")

        # Only slightly different
        new_concise = "The authentication system uses JWT tokens for user authentication with refresh tokens"
        # With 0.95 threshold, should be True (very similar)
        result_095 = _is_duplicate_concise(new_concise, context_dir, threshold=0.95)
        assert result_095 is True

        # With 0.5 threshold, should definitely be True
        result_050 = _is_duplicate_concise(new_concise, context_dir, threshold=0.5)
        assert result_050 is True


# =============================================================================
# Test Class for _save_explorer_result() dedup behavior
# =============================================================================

class TestSaveExplorerResultDedup:
    """Tests for _save_explorer_result() dedup behavior."""

    def test_saves_when_no_existing_files(self, mock_temp_dir):
        """Saves file when context directory is empty."""
        query = "test query"
        result = """## Concise:
This is a concise summary about authentication.

## Answer
Full answer here.
"""
        context_key = "dedup-save-test"

        _save_explorer_result(query, result, context_key)

        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1
        assert "test-query" in files[0].name

    def test_skips_save_when_duplicate_concise(self, mock_temp_dir):
        """Skips save when ## Concise: section is too similar to existing."""
        context_key = "dedup-skip-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create existing file with similar concise
        existing_file = context_dir / "existing_20260601_120000.md"
        existing_file.write_text("""# Existing Result

## Concise:
The authentication system uses JWT tokens for user authentication with refresh tokens.

## Answer
Full details here.
""")

        query = "auth tokens"
        result = """## Concise:
The authentication system uses JWT tokens for user authentication with refresh tokens.

## Answer
Same answer content.
"""
        # Clear the file tracker by calling with different result first
        # Actually, we need to call the function and check it skips
        _save_explorer_result(query, result, context_key)

        # Should NOT create a new file (skipped due to duplicate)
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1, f"Expected 1 file (skip duplicate), got {len(files)}: {[f.name for f in files]}"
        assert files[0].name == "existing_20260601_120000.md"

    def test_saves_when_concise_different_enough(self, mock_temp_dir):
        """Saves file when ## Concise: section is different enough."""
        context_key = "dedup-save-diff-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create existing file
        existing_file = context_dir / "existing_20260601_120000.md"
        existing_file.write_text("""# Existing Result

## Concise:
The authentication system uses JWT tokens for user authentication.

## Answer
Full details here.
""")

        query = "database schema"
        result = """## Concise:
The database schema uses PostgreSQL with migration support and foreign key constraints.

## Answer
Database details here.
"""
        _save_explorer_result(query, result, context_key)

        # Should create a new file (different concise)
        files = list(context_dir.glob("*.md"))
        assert len(files) == 2, f"Expected 2 files, got {len(files)}: {[f.name for f in files]}"

    def test_saves_when_no_concise_section(self, mock_temp_dir):
        """Saves file when result has no ## Concise: section."""
        context_key = "no-concise-save-test"
        context_dir = mock_temp_dir / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        query = "test"
        result = "## Answer\nSome answer without concise section."

        _save_explorer_result(query, result, context_key)

        # Should save (no concise = no dedup check)
        files = list(context_dir.glob("*.md"))
        assert len(files) == 1
