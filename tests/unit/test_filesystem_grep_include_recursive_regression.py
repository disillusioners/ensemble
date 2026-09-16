"""Regression pin: ``grep_files`` must be RECURSIVE when ``include`` is set.

Background
----------
The grep_files tool takes a glob ``include`` filter (e.g. ``"*.py"``) and
applies it via ``pathlib.Path.glob``. By pathlib's contract, a bare ``"*.py"``
pattern only matches files DIRECTLY in the search root, NOT subdirectories —
recursive matching requires ``"**/*.py"``.

The current implementation uses ``"**/*"`` for the no-include case (correct)
but ``include`` directly when set (e.g. ``"*.py"``) — so adding an include
filter silently disables recursion. Subdirectory hits vanish. The user-visible
symptom: a token that ONLY exists in subdirs returns ``"No matches found"``.

RED is the expected state of this file until the recursion fix lands. Do NOT
"fix" by weakening assertions — the pins below encode the correct behavior:

  (a) include="*.py" must find matches in SUBDIRECTORIES (not only at root).
  (b) CONTROL: no include, same pattern, same tree → recursive (passes now).
  (c) brace include ("{*.py,*.txt}" or "*.{py,txt}") must find nested hits
      for BOTH extensions. (Brace support itself is a sibling sub-finding —
      pathlib.glob does not natively expand brace forms; if the fix chooses
      a different include syntax, this case should be updated to match the
      tool's documented contract.)
  (d) CONTROL: non-existent path → proper error (passes now).

The repository's daemon code is NOT touched by this test — it only pins
behavior. The fix lives elsewhere; this file guards the regression.

Family conventions followed (mirrors tests/unit/test_filesystem_absolute_path.py):
- Direct import from daemon.tools.filesystem
- ``tool.invoke({...kwargs})`` to call the @tool-decorated StructuredTool
- ``tmp_path`` fixture for hermetic trees
- Absolute paths so workdir is not required
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.tools.filesystem import grep_files


# ---------------------------------------------------------------------------
# Shared hermetic fixture
# ---------------------------------------------------------------------------
#
# Tree shape (deliberately ≥2 levels deep so a non-recursive bug cannot pass
# by accident):
#
#   tmp_path/
#     root_marker.py              (root-only .py — contains ROOT_TOKEN_HERE)
#     root_no_marker.py           (root .py — no special token)
#     nested/
#       deep.py                   (subdir 1 — contains NESTED_TOKEN_HERE)
#       deeper/
#         deepest.py              (subdir 2 — contains DEEPEST_TOKEN_HERE)
#         distractor.log          (not .py — IGNORED by *.py include)
#     txt_branch/
#       root.txt                  (root-level .txt — contains TXT_ROOT_TOKEN)
#       nested/
#         nested.txt              (subdir .txt — contains TXT_NESTED_TOKEN)


@pytest.fixture
def grep_tree(tmp_path: Path) -> Path:
    # Root .py files (note: root_only.py is included to make any non-recursive
    # glob visibly partial — even a buggy "root-only" implementation will find
    # SOMETHING here, but assertions check for SUB-dir hits).
    (tmp_path / "root_marker.py").write_text(
        "ROOT_TOKEN_HERE = 'present at root level'\n"
    )
    (tmp_path / "root_no_marker.py").write_text("x = 1\n")

    # Nested ≥2 levels deep with .py + distractor .log
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.py").write_text(
        "NESTED_TOKEN_HERE = 'present in nested/deep.py'\n"
    )
    deeper = nested / "deeper"
    deeper.mkdir()
    (deeper / "deepest.py").write_text(
        "DEEPEST_TOKEN_HERE = 'present in nested/deeper/deepest.py'\n"
    )
    (deeper / "distractor.log").write_text("DEEPEST_TOKEN_HERE log-only\n")

    # Branch with .txt files for the brace-extension case
    txt_branch = tmp_path / "txt_branch"
    txt_branch.mkdir()
    (txt_branch / "root.txt").write_text(
        "TXT_ROOT_TOKEN = 'present at txt_branch/root.txt'\n"
    )
    (txt_branch / "nested").mkdir()
    (txt_branch / "nested" / "nested.txt").write_text(
        "TXT_NESTED_TOKEN = 'present at txt_branch/nested/nested.txt'\n"
    )

    return tmp_path


def _grep(path: Path, *, pattern: str, include: str = "", **extra) -> str:
    """Invoke grep_files with an absolute path (no workdir)."""
    args = {
        "pattern": pattern,
        "path": str(path),
        "include": include,
        "workdir": None,
        **extra,
    }
    return grep_files.invoke(args)


# ---------------------------------------------------------------------------
# (a) include="*.py" + pattern → MUST find subdir hits (RED under the bug)
# ---------------------------------------------------------------------------
class TestIncludeRecursivePy:
    """include="*.py" must recurse into subdirectories."""

    def test_include_py_finds_nested_deep_py_hit(self, grep_tree: Path):
        """Token unique to nested/deep.py must be found."""
        out = _grep(grep_tree, pattern="NESTED_TOKEN_HERE", include="*.py")
        assert "No matches found" not in out, (
            "grep_files with include='*.py' silently skipped subdirs — got "
            f"empty result. Full output:\n{out}"
        )
        # Must reference the file by its subdir-relative path.
        assert "nested/deep.py" in out or "nested\\deep.py" in out, (
            f"Expected nested/deep.py to appear in output:\n{out}"
        )

    def test_include_py_finds_two_levels_deep_hit(self, grep_tree: Path):
        """Token unique to nested/deeper/deepest.py must be found."""
        out = _grep(grep_tree, pattern="DEEPEST_TOKEN_HERE", include="*.py")
        assert "No matches found" not in out, (
            f"grep_files did not recurse into nested/deeper/:\n{out}"
        )
        # The distractor .log has the same token but should be IGNORED by
        # include='*.py' — only the .py file should be referenced.
        assert "deepest.py" in out, (
            f"Expected deepest.py in output:\n{out}"
        )
        assert "distractor.log" not in out, (
            f"distractor.log should be excluded by include='*.py':\n{out}"
        )

    def test_include_py_ignores_non_py_files_at_subdir(self, grep_tree: Path):
        """include='*.py' must not match .log files even when content matches.

        Pins that the include filter is honored (no false positives), AND that
        subdir .py files are still scanned (no false negatives from recursion
        being skipped).
        """
        out = _grep(grep_tree, pattern="DEEPEST_TOKEN_HERE", include="*.py")
        # Pin the no-false-positive side:
        assert ".log" not in out, (
            f"include='*.py' matched a .log file:\n{out}"
        )
        # Pin the no-false-negative side (recursive):
        assert "No matches found" not in out, (
            f"include='*.py' skipped subdir recursion:\n{out}"
        )


# ---------------------------------------------------------------------------
# (b) CONTROL — no include, same tree, same pattern → recursion works
# ---------------------------------------------------------------------------
class TestNoIncludeBaseline:
    """Without include, recursion must continue to work (control)."""

    def test_no_include_finds_nested_hit(self, grep_tree: Path):
        out = _grep(grep_tree, pattern="NESTED_TOKEN_HERE", include="")
        assert "No matches found" not in out
        assert "nested/deep.py" in out or "nested\\deep.py" in out

    def test_no_include_finds_two_levels_deep_hit(self, grep_tree: Path):
        out = _grep(grep_tree, pattern="DEEPEST_TOKEN_HERE", include="")
        assert "No matches found" not in out
        assert "deepest.py" in out
        # Note: without an include, the .log file ALSO matches — that's the
        # expected no-include behavior (full content search). We do NOT pin
        # the absence of .log here — that pin lives in (a).


# ---------------------------------------------------------------------------
# (c) Brace / multi-extension include — MUST find nested hits for both
# ---------------------------------------------------------------------------
#
# NOTE on brace support: pathlib.Path.glob does NOT natively expand brace
# patterns like "{*.py,*.txt}" or "*.{py,txt}". The tool's own docstring
# (filesystem.py:535) advertises "*.{js,ts}" as an example. If the recursive
# fix lands but brace support requires an explicit expand step, the fix must
# address BOTH so this test passes. If a different multi-extension syntax is
# chosen, update the include strings below to match the tool's documented
# contract.
class TestIncludeBraceRecursive:
    """Brace include must recurse AND match multiple extensions."""

    def test_brace_form_finds_nested_py_and_txt(self, grep_tree: Path):
        # The .txt file at txt_branch/root.txt sits AT root of txt_branch.
        # The .txt at txt_branch/nested/nested.txt sits in a SUBDIR — so this
        # case can only succeed if braces work AND recursion works.
        # Token TXT_NESTED_TOKEN is unique to the SUBDIR .txt.
        out = _grep(
            grep_tree,
            pattern="TXT_NESTED_TOKEN",
            include="{*.py,*.txt}",
        )
        assert "No matches found" not in out, (
            "Brace include '{*.py,*.txt}' returned no matches — either braces "
            f"are not expanded or recursion is skipped.\nFull output:\n{out}"
        )
        # Nested .txt must appear (recursion required for this hit).
        assert "nested.txt" in out, (
            f"Expected nested.txt in output (subdir .txt hit):\n{out}"
        )

    def test_alt_brace_form_finds_nested_py(self, grep_tree: Path):
        # Token NESTED_TOKEN_HERE is unique to nested/deep.py — proving that
        # even when only ONE branch of the brace hits something, the
        # recursion must be active.
        out = _grep(
            grep_tree,
            pattern="NESTED_TOKEN_HERE",
            include="*.{py,txt}",
        )
        assert "No matches found" not in out, (
            "Brace include '*.{py,txt}' returned no matches:\n" + out
        )
        assert "deep.py" in out, (
            f"Expected deep.py in output:\n{out}"
        )


# ---------------------------------------------------------------------------
# (d) CONTROL — non-existent path → proper error
# ---------------------------------------------------------------------------
class TestNonExistentPath:
    """Non-existent paths must surface a clear error, not a silent empty result."""

    def test_missing_dir_returns_error(self, tmp_path: Path):
        missing = tmp_path / "this_subdir_does_not_exist_xyz"
        out = _grep(missing, pattern="anything", include="*.py")
        assert "No matches found" not in out, (
            "Non-existent path silently returned 'No matches found' — must "
            f"surface an error. Got: {out!r}"
        )
        assert "ERROR" in out, f"Expected ERROR prefix, got: {out!r}"
        assert "does not exist" in out, f"Expected 'does not exist' in error: {out!r}"