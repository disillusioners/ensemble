"""Unit tests for absolute path support in filesystem tools.

Covers:
- _is_absolute_path recognises OS-native absolute paths and Windows drive/UNC paths
- _resolve_target_path returns (target, None, None) for absolute paths and skips
  the workdir requirement
- _resolve_target_path still requires workdir for relative paths
- write_file, read_file, list_directory, edit_file, glob_files, grep_files all
  accept an absolute path with no workdir (cross-platform safe).
"""

from pathlib import Path

import pytest

from daemon.tools.filesystem import (
    _is_absolute_path,
    _resolve_target_path,
    _resolve_within_workdir,
    edit_file,
    glob_files,
    grep_files,
    list_directory,
    read_file,
    write_file,
)


# ---------------------------------------------------------------------------
# _is_absolute_path tests
# ---------------------------------------------------------------------------

class TestIsAbsolutePath:
    def test_unix_absolute_path(self):
        assert _is_absolute_path("/abs/path/file.txt") is True
        assert _is_absolute_path("/") is True

    def test_relative_path_returns_false(self):
        assert _is_absolute_path("relative/file.txt") is False
        assert _is_absolute_path("./file.txt") is False
        assert _is_absolute_path("../file.txt") is False
        assert _is_absolute_path("file.txt") is False

    def test_empty_string_returns_false(self):
        assert _is_absolute_path("") is False

    def test_windows_drive_letter_path(self):
        # Cross-platform: should be recognised as absolute even on Unix hosts
        assert _is_absolute_path("C:\\Users\\foo\\file.txt") is True
        assert _is_absolute_path("D:/path/to/file") is True
        assert _is_absolute_path("c:\\lowercase\\drive") is True

    def test_windows_unc_path(self):
        assert _is_absolute_path("\\\\server\\share\\file") is True
        # Forward-slash UNC is also valid on Windows
        assert _is_absolute_path("//server/share/file") is True

    def test_windows_drive_letter_without_separator_is_not_absolute(self):
        # A bare drive letter without a path separator is not a full path
        assert _is_absolute_path("C:") is False
        assert _is_absolute_path("C:file.txt") is False


# ---------------------------------------------------------------------------
# _resolve_target_path tests
# ---------------------------------------------------------------------------

class TestResolveTargetPath:
    def test_absolute_path_skips_workdir(self, tmp_path):
        abs_path = str(tmp_path / "file.txt")
        target, base, err = _resolve_target_path(abs_path, workdir=None)
        assert err is None
        assert base is None
        assert target == Path(abs_path)

    def test_absolute_path_ignores_workdir(self, tmp_path):
        abs_path = str(tmp_path / "file.txt")
        # Even with a workdir provided, absolute paths use themselves
        target, base, err = _resolve_target_path(abs_path, workdir="/some/other/dir")
        assert err is None
        assert base is None
        assert target == Path(abs_path)

    def test_relative_path_requires_workdir(self):
        target, base, err = _resolve_target_path("file.txt", workdir=None)
        assert err is not None
        assert "workdir is required" in err
        assert target is None
        assert base is None

    def test_relative_path_with_empty_workdir_errors(self):
        target, base, err = _resolve_target_path("file.txt", workdir="")
        assert err is not None
        assert "workdir is required" in err
        assert target is None

    def test_relative_path_with_whitespace_workdir_errors(self):
        target, base, err = _resolve_target_path("file.txt", workdir="   ")
        assert err is not None
        assert "workdir is required" in err
        assert target is None

    def test_relative_path_resolves_against_workdir(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        target, base, err = _resolve_target_path("sub/file.txt", workdir=str(tmp_path))
        assert err is None
        assert base == tmp_path.resolve()
        assert target == (sub / "file.txt").resolve()

    def test_relative_path_with_nonexistent_workdir_errors(self):
        """A typo'd / hallucinated workdir surfaces a workdir-specific error.

        Regression: previously this returned a misleading "File does not
        exist" downstream. Now the resolver itself reports that the workdir
        itself is missing, using the *original* workdir string the caller
        passed in (so the agent can spot its own typo).
        """
        typo_workdir = "/Users/ngienminhkha/projects/agents-ensemble"  # missing 'u'
        target, base, err = _resolve_target_path("foo.txt", workdir=typo_workdir)

        assert err is not None
        assert target is None
        assert base is None
        assert "Working directory does not exist" in err
        assert "check the workdir path" in err
        # Original workdir echoed verbatim, so the agent sees its own typo.
        assert typo_workdir in err

    def test_relative_path_nonexistent_workdir_does_not_fall_through_to_file_error(self, tmp_path):
        """When the workdir is missing, the resolver must NOT return a valid target.

        Otherwise downstream tools would see a 'valid' target path and report
        a misleading "File does not exist" instead of the real workdir issue.
        """
        # Use a path under tmp_path that definitely does not exist.
        ghost = tmp_path / "nope" / "not_here"
        target, base, err = _resolve_target_path("file.txt", workdir=str(ghost))

        assert err is not None
        assert target is None
        assert base is None
        assert "Working directory does not exist" in err


class TestResolveWithinWorkdir:
    def test_absolute_path_works_no_boundary_check(self, tmp_path):
        abs_path = str(tmp_path / "file.txt")
        target, err = _resolve_within_workdir(abs_path, workdir=None)
        assert err is None
        assert target == Path(abs_path)

    def test_relative_path_inside_workdir_allowed(self, tmp_path):
        sub = tmp_path / "inside"
        sub.mkdir()
        target, err = _resolve_within_workdir("file.txt", workdir=str(sub))
        assert err is None
        assert target == (sub / "file.txt").resolve()

    def test_relative_path_with_nonexistent_workdir_errors(self, tmp_path):
        """_resolve_within_workdir surfaces the workdir-does-not-exist error.

        Bug class: caller passes a typo'd workdir, the wrapper used to return
        a resolved target inside a non-existent directory. Downstream tools
        then reported "File does not exist" against a path the agent never
        intended. Now the error fires at the resolver.
        """
        ghost = tmp_path / "definitely_not_here"
        target, err = _resolve_within_workdir("file.txt", workdir=str(ghost))

        assert err is not None
        assert target is None
        assert "Working directory does not exist" in err
        # Original (unresolved) workdir string preserved in the message.
        assert str(ghost) in err


# ---------------------------------------------------------------------------
# Tool-level tests (parametrized across all 6 filesystem tools)
# ---------------------------------------------------------------------------

# Each entry: (tool_fn, kwargs_for_abs, success_substring) where
# kwargs_for_abs(absolute_target_path) returns the kwargs dict to pass to the
# tool. The same entry is reused for the "relative path without workdir
# errors" test, which uses a different kwargs dict.
#
# The "target" passed in is the path the tool should operate on:
#   - write_file / read_file / edit_file: a file path
#   - list_directory / glob_files / grep_files: a directory path
_FILE_TOOL_CASES = [
    pytest.param(
        write_file,
        lambda p: {"content": "x", "path": p},
        "SUCCESS",
        id="write_file",
    ),
    pytest.param(
        read_file,
        lambda p: {"path": p},
        None,  # success substring is the file content "fixture"
        id="read_file",
    ),
    pytest.param(
        edit_file,
        lambda p: {"path": p, "old_string": "old", "new_string": "new"},
        "SUCCESS",
        id="edit_file",
    ),
]

_DIR_TOOL_CASES = [
    pytest.param(
        list_directory,
        lambda p: {"path": p},
        "fixture.txt",
        id="list_directory",
    ),
    pytest.param(
        glob_files,
        lambda p: {"pattern": "*.txt", "path": p},
        "fixture.txt",
        id="glob_files",
    ),
    pytest.param(
        grep_files,
        lambda p: {"pattern": "fixture", "path": p},
        "fixture",
        id="grep_files",
    ),
]


@pytest.mark.parametrize("tool_fn,kwargs_for_abs,expected", _FILE_TOOL_CASES + _DIR_TOOL_CASES)
def test_absolute_path_no_workdir(tool_fn, kwargs_for_abs, expected, tmp_path):
    """All 6 filesystem tools accept an absolute path with no workdir."""
    if tool_fn in (write_file, read_file, edit_file):
        # File-targeting: pre-create the file (write_file also creates it).
        target = tmp_path / "fixture.txt"
        target.write_text("fixture content")
        if tool_fn is edit_file:
            target.write_text("old")
    else:
        # Directory-targeting: target IS the directory.
        target = tmp_path
        (tmp_path / "fixture.txt").write_text("fixture content")

    result = tool_fn.invoke(kwargs_for_abs(str(target)))

    assert "ERROR" not in result
    if expected is not None:
        assert expected in result


@pytest.mark.parametrize(
    "tool_fn,kwargs_for_rel",
    [(c.values[0], c.values[1]) for c in _FILE_TOOL_CASES + _DIR_TOOL_CASES],
)
def test_relative_path_without_workdir_errors(tool_fn, kwargs_for_rel):
    """All 6 filesystem tools still require workdir for relative paths."""
    result = tool_fn.invoke(kwargs_for_rel("file.txt"))
    assert "ERROR" in result
    assert "workdir is required" in result


# ---------------------------------------------------------------------------
# write_file-only behaviour worth covering in isolation
# ---------------------------------------------------------------------------

class TestWriteFileAbsolutePath:
    def test_absolute_path_with_workdir_still_works(self, tmp_path):
        target = tmp_path / "out2.txt"
        result = write_file.invoke({
            "content": "x",
            "path": str(target),
            "workdir": "/some/other/ignored/dir",
        })
        assert "SUCCESS" in result
        assert target.read_text() == "x"

    def test_absolute_path_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "file.md"
        result = write_file.invoke({
            "content": "ok",
            "path": str(target),
        })
        assert "SUCCESS" in result
        assert target.read_text() == "ok"
