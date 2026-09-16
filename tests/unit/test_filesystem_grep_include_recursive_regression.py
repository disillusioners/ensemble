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


# ---------------------------------------------------------------------------
# W3/W4/W5/S3/S5 — additional pins appended to lock in recursion semantics
# ---------------------------------------------------------------------------
#
# Background: the original recursion fix (commit 3fe667fa) wrapped the
# no-include fallback in sorted() and added recursive + brace expansion to the
# include filter. These pins lock in the semantics so future refactors (e.g.
# stripping the `**`-check, swapping expansion order, removing the sorted
# wrapper, or losing union/dedupe) get caught.
#
# Two access patterns are used here:
#  - ``_grep`` (the public grep_files.invoke entry point) — for tests that
#    pin observable behavior (pagination, match output, dedupe in output).
#  - ``_expand_include_glob`` (the internal helper) — for tests that pin
#    the FILE SET produced by include resolution (W3/W4/W5). Parsing match
#    lines to recover paths is fragile; the helper returns the resolved
#    list directly.


class TestDoubleStarVerbatim:
    """W3: `**`-verbatim passthrough — must return the SAME file set as
    prefix-less ``*.py``.

    A refactor that strips the `**`-check (or makes the prepend
    unconditional) would fail this:

      - Strip the check → ``*.py`` becomes root-only (2 files), but
        ``**/*.py`` is unchanged (4 files). Sets differ → pin fires.
      - Make prepend unconditional → ``**/*.py`` becomes ``**/**/*.py``,
        which pathlib treats as recursive but is clearly wrong intent; a
        future tightening of glob semantics would diverge the two sets.
    """

    def test_double_star_py_same_set_as_prefix_less_py(self, grep_tree: Path):
        from daemon.tools.filesystem import _expand_include_glob

        verbatim = sorted(
            str(p) for p in _expand_include_glob(grep_tree, "**/*.py")
        )
        prefix_less = sorted(
            str(p) for p in _expand_include_glob(grep_tree, "*.py")
        )
        assert verbatim == prefix_less, (
            "include='**/*.py' returned a different file set than include='*.py' "
            "— the `**`-check or the recursion prepend regressed:\n"
            f"  verbatim   = {verbatim}\n"
            f"  prefixless = {prefix_less}"
        )
        # Pin that the recursion is real (>=3 .py files in the fixture).
        assert len(verbatim) >= 3, (
            f"Expected recursive .py match to include ≥3 files, got {len(verbatim)}"
        )


class TestRecursionBraceCombo:
    """W4: ``**/*.{ext1,ext2}`` union — order-of-ops regression guard.

    Pins that brace expansion preserves the ``**/`` prefix in EACH alternate
    (the prepend happens on the WHOLE pre-expansion pattern, not per-alternate).
    The fixture has 4 .py + 2 .txt files, no .ts or .html — we use the
    available extensions; the semantics being pinned is the SAME.
    """

    def test_recursive_brace_brace_union_matches_py_and_txt(self, grep_tree: Path):
        from daemon.tools.filesystem import _expand_include_glob

        py_set = sorted(
            str(p) for p in _expand_include_glob(grep_tree, "**/*.py")
        )
        txt_set = sorted(
            str(p) for p in _expand_include_glob(grep_tree, "**/*.txt")
        )
        brace_set = sorted(
            str(p) for p in _expand_include_glob(grep_tree, "**/*.{py,txt}")
        )

        # Sanity on the fixture shape (4 .py + 2 .txt, no .log).
        assert len(py_set) == 4, (
            f"Expected 4 .py files in fixture, got {len(py_set)}: {py_set}"
        )
        assert len(txt_set) == 2, (
            f"Expected 2 .txt files in fixture, got {len(txt_set)}: {txt_set}"
        )

        # The brace result MUST equal the union of the two single-extension
        # recursive results (deduped, sorted).
        expected = sorted(set(py_set) | set(txt_set))
        assert brace_set == expected, (
            "include='**/*.{py,txt}' result differs from union of "
            "'**/*.py' + '**/*.txt' — brace expansion or recursion prepend "
            "regressed:\n"
            f"  brace    = {brace_set}\n"
            f"  expected = {expected}"
        )
        # No duplicates in the brace result (union dedup worked).
        assert len(brace_set) == len(set(brace_set)), (
            f"Duplicates in brace result (union dedup broken): {brace_set}"
        )


class TestNestedBraceLiteral:
    """W5: nested-brace patterns are passed through VERBATIM to pathlib.

    Pins that the single-level brace expander does NOT touch nested forms
    like ``{a,{b,c}}`` — those reach ``Path.glob`` literally (which itself
    does not expand braces, so it matches no files unless a filename actually
    contains braces). The pin: tool result equals direct ``path.glob()``
    of the same literal pattern.
    """

    def test_nested_brace_literal_equals_direct_pathlib_glob(self, grep_tree: Path):
        from daemon.tools.filesystem import _expand_include_glob

        tool_result = sorted(
            str(p) for p in _expand_include_glob(grep_tree, "{a,{b,c}}")
        )
        direct_glob = sorted(str(p) for p in grep_tree.glob("{a,{b,c}}"))
        assert tool_result == direct_glob, (
            "Nested-brace literal pattern diverged between tool and direct "
            f"pathlib.glob:\n  tool={tool_result}\n  glob={direct_glob}"
        )
        # Pin the fixture's literal-brace surface: no filenames contain
        # literal braces, so both sides return empty.
        assert tool_result == [], (
            f"Expected empty for nested-brace literal on this fixture:\n{tool_result}"
        )


class TestPaginationAndOrderPins:
    """S3: pagination + W1 deterministic-order pins.

    Pins three observable behaviors:
      (a) limit= truncates the FINAL output (after union/dedupe), not the
          per-glob scan.
      (b) offset=1, limit=1 skips the first sorted match and surfaces a
          next-page hint at offset=2.
      (c) Empty-include path returns matches in deterministically sorted
          order across runs — pins the W1 resolution.
    """

    def test_limit_truncation_applied_after_union_dedupe(self, grep_tree: Path):
        """limit=N produces exactly N matches from the union result."""
        # Pattern "=" appears once per line in 4 .py + 2 .txt = 6 lines total
        # under include="*.{py,txt}" (brace union).
        out = _grep(grep_tree, pattern="=", include="*.{py,txt}", limit=3)
        match_lines = [
            line
            for line in out.split("\n")
            if line and not line.startswith("---") and not line.startswith("Showing")
        ]
        assert len(match_lines) == 3, (
            f"Expected exactly 3 matches with limit=3 on brace union, got "
            f"{len(match_lines)}:\n{out}"
        )

    def test_offset_one_skips_first_sorted_match(self, grep_tree: Path):
        """offset=1, limit=1 returns the SECOND match in sorted order.

        Pins that offset acts on the match list (post sort) — the FIRST
        match (lowest-path .py file) is skipped.
        """
        first = _grep(
            grep_tree,
            pattern="TOKEN_HERE",
            include="*.py",
            limit=1,
            offset=0,
        )
        second = _grep(
            grep_tree,
            pattern="TOKEN_HERE",
            include="*.py",
            limit=1,
            offset=1,
        )
        assert "No matches found" not in first, (
            f"offset=0 returned no matches (fixture broken?):\n{first}"
        )
        assert "No matches found" not in second, (
            f"offset=1 returned no matches — pagination ate past available "
            f"results:\n{second}"
        )
        # The two calls must return DIFFERENT matches (offset shifted by 1).
        assert first != second, (
            "offset=1 returned the same match as offset=0 — pagination "
            f"did not advance:\n  first={first!r}\n  second={second!r}"
        )
        # Pin sort order: offset=0 returns the lex-smallest match (nested/
        # deep.py), offset=1 returns the next-smallest (nested/deeper/
        # deepest.py). Fixture has 3 TOKEN_HERE matches in 3 distinct .py
        # files.
        assert "nested/deep.py:1" in first, (
            f"Expected offset=0 to surface 'nested/deep.py' (sorted first):\n{first}"
        )
        assert "nested/deeper/deepest.py:1" in second, (
            f"Expected offset=1 to surface 'nested/deeper/deepest.py' "
            f"(next in sorted order):\n{second}"
        )

    def test_empty_include_returns_deterministic_sorted_order(self, grep_tree: Path):
        """Empty-include path matches deterministically across runs.

        Pins the W1 resolution: the no-include fallback returns a sorted
        file list, so match iteration is stable.
        """
        first_run = _grep(grep_tree, pattern="=", include="", limit=10)
        second_run = _grep(grep_tree, pattern="=", include="", limit=10)
        assert first_run == second_run, (
            "Empty-include path produced non-deterministic output across "
            "runs — the sorted() wrapper was lost:\n"
            f"  run1: {first_run!r}\n"
            f"  run2: {second_run!r}"
        )
        # Pin that the FIRST match comes from the lex-smallest absolute path.
        # With sorted str(path), 'nested/...' < 'root_...' < 'txt_branch/...'
        # ('n' < 'r' < 't'), so nested/deep.py must surface first.
        assert "nested/deep.py:1" in first_run, (
            f"Expected first match from 'nested/deep.py' (sorted first):\n{first_run}"
        )


class TestOverlappingAlternateDedupe:
    """S5: overlapping alternates in a brace pattern must dedupe.

    Pins that the union step collapses repeated files: include
    ``{*.py,**/*.py}`` should scan each .py file exactly once even though
    the two alternates overlap (``**/*.py`` covers everything ``*.py``
    covers and more).
    """

    def test_overlapping_py_alternates_scan_each_file_once(self, grep_tree: Path):
        out = _grep(
            grep_tree,
            pattern="=",
            include="{*.py,**/*.py}",
            limit=100,
        )
        # Parse match lines into (path, line_num, content) and count per path.
        # Substring matching is unreliable here because match CONTENT also
        # contains the filename (e.g. line content "x = 1" doesn't, but
        # "NESTED_TOKEN_HERE = 'present in nested/deep.py'" does — substring
        # count of 'nested/deep.py' would double-count).
        expected_files = [
            "root_marker.py",
            "root_no_marker.py",
            "nested/deep.py",
            "nested/deeper/deepest.py",
        ]
        path_counts: dict[str, int] = {f: 0 for f in expected_files}
        for line in out.split("\n"):
            if not line or line.startswith("---") or line.startswith("Showing"):
                continue
            # Format: "{abs_path}:{line_num}: {content}". Split on first ':'
            # after the absolute-path prefix — paths here have no ':'.
            parts = line.split(":", 2)
            if len(parts) < 2:
                continue
            abs_path = parts[0]
            for fname in expected_files:
                if abs_path.endswith("/" + fname) or abs_path.endswith(fname):
                    path_counts[fname] += 1
                    break
        for fname, count in path_counts.items():
            assert count == 1, (
                f"File {fname} appeared {count} times in output (expected 1) "
                f"— overlapping-alternate dedupe broken:\n{out}"
            )
        # 4 matches total (one per .py file). No pagination hint expected
        # (total matches < limit and content < 6000 chars).
        match_lines = [
            line
            for line in out.split("\n")
            if line and not line.startswith("---") and not line.startswith("Showing")
        ]
        assert len(match_lines) == 4, (
            f"Expected 4 matches from overlapping dedupe, got {len(match_lines)}:\n{out}"
        )