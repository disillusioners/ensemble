"""Unit tests for truncation utilities."""

import pytest
from daemon.tools._truncate import truncate_output, truncate_dict_result


class TestTruncateOutput:
    """Tests for truncate_output function."""

    def test_empty_content_not_truncated(self):
        """Empty content returns not truncated."""
        result = truncate_output("")
        assert result.truncated is False
        assert result.content == ""

    def test_content_under_limits_not_truncated(self):
        """Content under both limits is not truncated."""
        content = "line1\nline2\nline3"
        result = truncate_output(content, max_chars=1000, max_lines=100)
        assert result.truncated is False
        assert result.content == content

    def test_content_exactly_at_limit_not_truncated(self):
        """Content exactly at limit is NOT marked as truncated."""
        content = "x" * 100
        result = truncate_output(content, max_chars=100, max_lines=100)
        assert result.truncated is False

    def test_truncates_by_lines(self):
        """Content exceeding line limit is truncated."""
        lines = [f"line{i}" for i in range(200)]
        content = "\n".join(lines)
        result = truncate_output(content, max_chars=10000, max_lines=50)
        
        assert result.truncated is True
        assert result.total_items == 200
        assert result.shown_items == 50
        assert "Results truncated" in result.pagination_hint

    def test_truncates_by_chars(self):
        """Content exceeding char limit is truncated."""
        content = "x" * 10000
        result = truncate_output(content, max_chars=1000, max_lines=10000)
        
        assert result.truncated is True
        assert len(result.content) <= 1000 + 10  # Allow for truncation marker

    def test_truncation_type_lines(self):
        """Truncation type is 'lines' when only line limit exceeded."""
        lines = [f"line{i}" for i in range(200)]
        content = "\n".join(lines)
        result = truncate_output(content, max_chars=100000, max_lines=50)
        
        assert result.truncation_type == "lines"

    def test_truncation_type_chars(self):
        """Truncation type is 'chars' when only char limit exceeded."""
        content = "x" * 10000
        result = truncate_output(content, max_chars=1000, max_lines=10000)
        
        assert result.truncation_type == "chars"

    def test_truncation_type_both(self):
        """Truncation type is 'both' when both limits exceeded."""
        lines = [f"line{i}" for i in range(200)]
        content = "\n".join(lines)
        result = truncate_output(content, max_chars=1000, max_lines=50)
        
        assert result.truncation_type == "both"

    def test_pagination_hint_shows_offset(self):
        """Pagination hint includes correct next offset."""
        lines = [f"line{i}" for i in range(200)]
        content = "\n".join(lines)
        result = truncate_output(content, max_chars=10000, max_lines=50)
        
        assert "offset=50" in result.pagination_hint

    def test_tool_name_in_hint(self):
        """Hint includes the tool name."""
        content = "\n".join([f"line{i}" for i in range(200)])
        result = truncate_output(content, max_chars=10000, max_lines=50, tool_name="my_tool")
        
        assert "my_tool" in result.pagination_hint

    def test_preserves_line_structure_for_grep(self):
        """Grep output format (file:line:content) remains parseable."""
        grep_output = "\n".join([f"file{i}.py:{j}: content" for i in range(200) for j in range(3)])
        result = truncate_output(grep_output, max_chars=6000, max_lines=100)
        
        # First line should be complete
        first_line = result.content.split("\n")[0]
        assert ":" in first_line  # file:line:content format preserved

    def test_unicode_handled_correctly(self):
        """Unicode characters are handled without errors."""
        content = "Hello 你好 🌍\nline2\nline3"
        result = truncate_output(content, max_chars=100, max_lines=100)
        
        assert result.truncated is False  # Under limits
        assert "你好" in result.content

    def test_newlines_preserved(self):
        """Newlines in content are preserved in output."""
        content = "line1\nline2\nline3"
        result = truncate_output(content, max_chars=1000, max_lines=10)
        
        assert "\n" in result.content


class TestTruncateDictResult:
    """Tests for truncate_dict_result function."""

    def test_non_dict_raises_error(self):
        """Non-dict input raises AttributeError (function expects dict)."""
        with pytest.raises(AttributeError):
            truncate_dict_result(["item1", "item2"], list_key="items")

    def test_dict_without_list_returned_unchanged(self):
        """Dict without the specified list key is returned unchanged."""
        result = truncate_dict_result({"name": "test"}, list_key="items")
        assert result == {"name": "test"}

    def test_list_under_limit_not_truncated(self):
        """List under limit is not truncated."""
        data = {"items": [{"id": i} for i in range(5)]}
        result = truncate_dict_result(data, list_key="items", limit=50)
        
        assert result == data
        assert "_pagination" not in result

    def test_list_over_limit_truncated(self):
        """List over limit is truncated with pagination metadata."""
        data = {"items": [{"id": i} for i in range(100)]}
        result = truncate_dict_result(data, list_key="items", limit=50)
        
        assert len(result["items"]) == 50
        assert result["_pagination"]["truncated"] is True
        assert result["_pagination"]["total"] == 100
        assert result["_pagination"]["shown"] == 50

    def test_pagination_hint_in_result(self):
        """Pagination hint is included in _pagination."""
        data = {"items": [{"id": i} for i in range(100)]}
        result = truncate_dict_result(data, list_key="items", limit=50)
        
        assert "offset=50" in result["_pagination"]["hint"]

    def test_preserves_other_dict_keys(self):
        """Other dictionary keys are preserved."""
        data = {
            "name": "test",
            "count": 100,
            "items": [{"id": i} for i in range(100)]
        }
        result = truncate_dict_result(data, list_key="items", limit=50)
        
        assert result["name"] == "test"
        assert result["count"] == 100

    def test_project_list_format(self):
        """Works with project_list response format."""
        data = {
            "projects": [{"id": f"proj_{i}"} for i in range(100)]
        }
        result = truncate_dict_result(data, list_key="projects", limit=50)
        
        assert len(result["projects"]) == 50
        assert result["_pagination"]["truncated"] is True

    def test_job_list_format(self):
        """Works with job_list response format."""
        data = {
            "jobs": [{"id": f"job_{i}"} for i in range(100)],
            "count": 100
        }
        result = truncate_dict_result(data, list_key="jobs", limit=50)
        
        assert len(result["jobs"]) == 50
        assert result["count"] == 100  # Preserved
        assert result["_pagination"]["truncated"] is True
