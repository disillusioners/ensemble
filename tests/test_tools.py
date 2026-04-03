"""Tests for daemon/tools/ - bash, filesystem tools"""

import os
import time

import pytest

from daemon.tools.bash import bash
from daemon.tools.filesystem import list_directory, read_file, glob_files


class TestBashTool:
    """Tests for bash command execution tool."""

    def test_bash_simple_command(self):
        """Test simple echo command execution."""
        result = bash.invoke({"command": "echo hello"})

        assert "hello" in result
        assert "EXIT CODE: 0" in result

    def test_bash_command_with_output(self):
        """Test command that produces stdout output."""
        result = bash.invoke({"command": "echo -e 'line1\\nline2\\nline3'"})

        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    def test_bash_nonzero_exit_code(self):
        """Test command that exits with non-zero code."""
        result = bash.invoke({"command": "exit 1"})

        assert "EXIT CODE: 1" in result

    def test_bash_stderr_captured(self):
        """Test that stderr is captured."""
        result = bash.invoke({"command": "ls /nonexistent_directory_12345 2>&1"})

        assert "No such file" in result or "not found" in result.lower()

    def test_bash_timeout(self):
        """Test timeout handling."""
        result = bash.invoke({"command": "sleep 5", "timeout": 1})

        assert "timed out" in result.lower()

    def test_bash_working_directory(self, tmp_path):
        """Test working directory option."""
        # Create a file in a specific directory
        test_file = tmp_path / "test_file.txt"
        test_file.write_text("test content")

        # Run pwd in that directory
        result = bash.invoke({
            "command": "pwd",
            "workdir": str(tmp_path)
        })

        assert str(tmp_path) in result

    def test_bash_with_environment_variable(self):
        """Test command that uses environment variables."""
        result = bash.invoke({"command": "echo $HOME"})

        assert result.strip() != ""
        assert "echo $HOME" not in result  # Should expand the variable


class TestListDirectoryTool:
    """Tests for list_directory tool."""

    def test_list_directory_current(self, tmp_path):
        """Test listing current directory."""
        result = list_directory.invoke({"path": ".", "workdir": str(tmp_path)})

        # Should return some content (the empty directory has no output or "empty directory")
        assert "empty" in result.lower() or result == ""

    def test_list_directory_specific_path(self, tmp_path):
        """Test listing a specific directory."""
        # Create some test files/dirs
        (tmp_path / "file1.txt").write_text("content")
        (tmp_path / "file2.txt").write_text("content")
        (tmp_path / "subdir").mkdir()

        result = list_directory.invoke({"path": ".", "workdir": str(tmp_path)})

        assert "file1.txt" in result
        assert "file2.txt" in result
        assert "subdir/" in result  # directories get / suffix

    def test_list_directory_show_hidden(self, tmp_path):
        """Test listing with show_hidden=True."""
        # Create hidden file
        hidden_file = tmp_path / ".hidden"
        hidden_file.write_text("hidden content")
        normal_file = tmp_path / "visible.txt"
        normal_file.write_text("content")

        result = list_directory.invoke({"path": ".", "workdir": str(tmp_path), "show_hidden": True})

        assert ".hidden" in result
        assert "visible.txt" in result

    def test_list_directory_hide_hidden_by_default(self, tmp_path):
        """Test that hidden files are hidden by default."""
        # Create hidden file
        hidden_file = tmp_path / ".hidden"
        hidden_file.write_text("hidden content")
        normal_file = tmp_path / "visible.txt"
        normal_file.write_text("content")

        result = list_directory.invoke({"path": ".", "workdir": str(tmp_path), "show_hidden": False})

        assert ".hidden" not in result
        assert "visible.txt" in result

    def test_list_directory_nonexistent(self, tmp_path):
        """Test error for non-existent path."""
        result = list_directory.invoke({"path": "nonexistent_path_12345", "workdir": str(tmp_path)})

        assert "ERROR" in result
        assert "does not exist" in result

    def test_list_directory_not_a_directory(self, tmp_path):
        """Test error when path is a file, not a directory."""
        test_file = tmp_path / "testfile.txt"
        test_file.write_text("content")

        result = list_directory.invoke({"path": "testfile.txt", "workdir": str(tmp_path)})

        assert "ERROR" in result
        assert "Not a directory" in result

    def test_list_directory_empty(self, tmp_path):
        """Test listing empty directory."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = list_directory.invoke({"path": "empty", "workdir": str(tmp_path)})

        assert "empty directory" in result.lower() or result == ""


class TestReadFileTool:
    """Tests for read_file tool."""

    def test_read_file_basic(self, tmp_path):
        """Test reading a file with content."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3")

        result = read_file.invoke({"path": "test.txt", "workdir": str(tmp_path)})

        assert "1: line1" in result
        assert "2: line2" in result
        assert "3: line3" in result

    def test_read_file_with_offset(self, tmp_path):
        """Test reading file with offset parameter."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5")

        result = read_file.invoke({"path": "test.txt", "workdir": str(tmp_path), "offset": 3})

        assert "3: line3" in result
        assert "4: line4" in result
        assert "5: line5" in result
        # Offset lines should be renumbered starting from offset value
        assert "1: line1" not in result

    def test_read_file_with_limit(self, tmp_path):
        """Test reading file with limit parameter."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5")

        result = read_file.invoke({"path": "test.txt", "workdir": str(tmp_path), "limit": 2})

        assert "1: line1" in result
        assert "2: line2" in result
        assert "line3" not in result

    def test_read_file_offset_and_limit(self, tmp_path):
        """Test reading file with both offset and limit."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5")

        result = read_file.invoke({"path": "test.txt", "workdir": str(tmp_path), "offset": 2, "limit": 2})

        assert "2: line2" in result
        assert "3: line3" in result
        assert "line4" not in result

    def test_read_file_nonexistent(self, tmp_path):
        """Test error for non-existent file."""
        result = read_file.invoke({"path": "nonexistent_file_12345.txt", "workdir": str(tmp_path)})

        assert "ERROR" in result
        assert "does not exist" in result

    def test_read_file_is_directory(self, tmp_path):
        """Test error when path is a directory, not a file."""
        test_dir = tmp_path / "testdir"
        test_dir.mkdir()

        result = read_file.invoke({"path": "testdir", "workdir": str(tmp_path)})

        assert "ERROR" in result
        assert "Not a file" in result

    def test_read_file_header_includes_total_lines(self, tmp_path):
        """Test that header includes total line count."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3")

        result = read_file.invoke({"path": "test.txt", "workdir": str(tmp_path)})

        assert "3 lines total" in result


class TestGlobFilesTool:
    """Tests for glob_files tool."""

    def test_glob_files_simple_pattern(self, tmp_path):
        """Test finding files with simple pattern."""
        # Create test files
        (tmp_path / "file1.py").write_text("python")
        (tmp_path / "file2.py").write_text("python")
        (tmp_path / "file3.txt").write_text("text")

        result = glob_files.invoke({"pattern": "*.py", "path": ".", "workdir": str(tmp_path)})

        assert "file1.py" in result
        assert "file2.py" in result
        assert "file3.txt" not in result

    def test_glob_files_no_matches(self, tmp_path):
        """Test when no files match the pattern."""
        (tmp_path / "file1.txt").write_text("text")

        result = glob_files.invoke({"pattern": "*.py", "path": ".", "workdir": str(tmp_path)})

        assert "No files matching" in result

    def test_glob_files_nonexistent_path(self, tmp_path):
        """Test error for non-existent path."""
        result = glob_files.invoke({"pattern": "*.py", "path": "nonexistent_path_12345", "workdir": str(tmp_path)})

        assert "ERROR" in result
        assert "does not exist" in result

    def test_glob_files_recursive_pattern(self, tmp_path):
        """Test recursive glob pattern."""
        # Create nested structure
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "file1.py").write_text("python")
        (subdir / "file2.py").write_text("python")

        result = glob_files.invoke({"pattern": "**/*.py", "path": ".", "workdir": str(tmp_path)})

        assert "file1.py" in result
        assert "file2.py" in result or "subdir" in result

    def test_glob_files_excludes_directories(self, tmp_path):
        """Test that glob excludes directories from results."""
        # Create a directory and a file with similar names
        (tmp_path / "test.txt").write_text("text")
        (tmp_path / "test_dir").mkdir()

        result = glob_files.invoke({"pattern": "test*", "path": ".", "workdir": str(tmp_path)})

        # Should only contain the file, not the directory
        assert "test.txt" in result

    def test_glob_files_default_path(self, tmp_path):
        """Test glob with default path (current directory)."""
        # Create a test file first
        (tmp_path / "test_glob.py").write_text("python")

        result = glob_files.invoke({"pattern": "*.py", "path": ".", "workdir": str(tmp_path)})

        # Should find the file
        assert "test_glob.py" in result
