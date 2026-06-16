"""Unit tests for ``daemon.services.context_tools`` (low-level FS helpers)."""

import json
from pathlib import Path
from unittest.mock import patch

from daemon.services.context_tools import (
    list_context_files,
    read_context_file,
    resolve_context_dir,
)


def _make_context_dir(tmp_path: Path, context_key: str) -> Path:
    context_dir = tmp_path / "ensemble" / "context" / context_key
    context_dir.mkdir(parents=True, exist_ok=True)
    return context_dir


# ─── resolve_context_dir ────────────────────────────────────────────────────────


class TestResolveContextDir:
    def test_returns_expected_path(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = resolve_context_dir("ctx-1")
        assert result == tmp_path / "ensemble" / "context" / "ctx-1"

    def test_does_not_raise_on_gettempdir_error(self):
        with patch("daemon.services.context_tools.tempfile.gettempdir", side_effect=OSError):
            result = resolve_context_dir("any")
        # Falls back to a Path under "/unknown" — never raises.
        assert isinstance(result, Path)

    def test_none_context_key_yields_empty_segment(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = resolve_context_dir(None)
        assert result == tmp_path / "ensemble" / "context" / ""


# ─── list_context_files ────────────────────────────────────────────────────────


class TestListContextFiles:
    def test_nonexistent_dir_returns_empty(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("does-not-exist")
        assert result == []

    def test_empty_dir_returns_empty(self, tmp_path):
        _make_context_dir(tmp_path, "ctx-empty")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-empty")
        assert result == []

    def test_populated_dir_returns_metadata(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-pop")
        (context_dir / "auth-flow_20260601_120000.md").write_text(
            "First line of the file.\nMore content.\n"
        )
        (context_dir / "notes_20260602_130000.md").write_text(
            "Just a one-liner.\n"
        )

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-pop")

        assert len(result) == 2
        # Result is sorted alphabetically by filename
        assert result[0]["filename"] == "auth-flow_20260601_120000.md"
        assert result[1]["filename"] == "notes_20260602_130000.md"

        first = result[0]
        assert first["slug"] == "auth-flow"
        assert isinstance(first["size_bytes"], int)
        assert first["size_bytes"] > 0
        assert first["modified_at"]  # ISO 8601 timestamp, non-empty
        # Multi-line preview: first 2 non-empty lines joined with "\n"
        assert first["concise_preview"] == "First line of the file.\nMore content."

        # One-liner file — preview is just that single line.
        assert result[1]["slug"] == "notes"
        assert result[1]["concise_preview"] == "Just a one-liner."

    def test_non_md_files_are_skipped(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-mixed")
        (context_dir / "good.md").write_text("ok")
        (context_dir / "ignored.txt").write_text("skip me")
        (context_dir / "ignored.json").write_text("{}")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-mixed")

        assert [r["filename"] for r in result] == ["good.md"]

    def test_subdirectories_are_skipped(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-subdirs")
        (context_dir / "subdir").mkdir()
        (context_dir / "subdir" / "nested.md").write_text("nested")
        (context_dir / "top.md").write_text("top")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-subdirs")

        assert [r["filename"] for r in result] == ["top.md"]

    def test_corrupt_file_does_not_crash_scan(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-corrupt")
        (context_dir / "good.md").write_text("ok")
        (context_dir / "bad.md").write_text("seed")

        original_open = Path.open

        def flaky_open(self, *args, **kwargs):
            if self.name == "bad.md":
                raise OSError("disk error")
            return original_open(self, *args, **kwargs)

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)), \
             patch.object(Path, "open", flaky_open):
            result = list_context_files("ctx-corrupt")

        # Both files appear in the listing — the bad one has an empty preview
        # because the per-file read error is caught and logged, not raised.
        filenames = [r["filename"] for r in result]
        assert sorted(filenames) == ["bad.md", "good.md"]
        by_name = {r["filename"]: r for r in result}
        assert by_name["good.md"]["concise_preview"] == "ok"
        assert by_name["bad.md"]["concise_preview"] == ""

    def test_preview_truncated_to_300_chars(self, tmp_path):
        """Rich preview is truncated to ~300 chars with an ellipsis suffix."""
        context_dir = _make_context_dir(tmp_path, "ctx-trunc")
        # Build content where the joined first-5 non-empty lines exceed 300 chars.
        long_line_a = "a" * 80
        long_line_b = "b" * 80
        long_line_c = "c" * 80
        long_line_d = "d" * 80
        (context_dir / "long.md").write_text(
            f"{long_line_a}\n{long_line_b}\n{long_line_c}\n{long_line_d}\n"
        )

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-trunc")

        assert len(result) == 1
        preview = result[0]["concise_preview"]
        # Truncated to max 300 chars including the trailing "..." suffix.
        assert len(preview) == 300
        assert preview.endswith("...")

    def test_preview_includes_heading_and_content_lines(self, tmp_path):
        """The first heading is kept and followed by real content lines."""
        context_dir = _make_context_dir(tmp_path, "ctx-heading")
        (context_dir / "doc.md").write_text(
            "# Auth Flow\n"
            "\n"
            "How users log in and renew sessions.\n"
            "Token expiry is 24h.\n"
            "Refresh uses a sliding window.\n"
            "Logout clears the cookie.\n"
        )

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-heading")

        preview = result[0]["concise_preview"]
        # Heading is preserved, then the first few content lines.
        assert preview.startswith("# Auth Flow")
        assert "How users log in and renew sessions." in preview
        assert "Token expiry is 24h." in preview
        # Blank lines are skipped, headings are kept as content lines.
        lines = preview.split("\n")
        assert "# Auth Flow" in lines
        assert len(lines) <= 5

    def test_preview_skips_blank_lines(self, tmp_path):
        """Blank/whitespace-only lines are stripped before joining."""
        context_dir = _make_context_dir(tmp_path, "ctx-blank")
        (context_dir / "doc.md").write_text(
            "First real line.\n"
            "\n"
            "   \n"
            "Second real line.\n"
            "\n"
        )

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-blank")

        preview = result[0]["concise_preview"]
        assert preview == "First real line.\nSecond real line."

    def test_preview_caps_at_five_lines(self, tmp_path):
        """At most 5 non-empty lines are included regardless of file length."""
        context_dir = _make_context_dir(tmp_path, "ctx-cap")
        (context_dir / "doc.md").write_text(
            "line-1\nline-2\nline-3\nline-4\nline-5\nline-6\nline-7\n"
        )

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-cap")

        preview = result[0]["concise_preview"]
        assert preview == "line-1\nline-2\nline-3\nline-4\nline-5"

    def test_empty_file_has_no_preview(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-empty-file")
        (context_dir / "empty.md").write_text("")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-empty-file")

        assert len(result) == 1
        assert result[0]["concise_preview"] == ""


# ─── read_context_file ─────────────────────────────────────────────────────────


class TestReadContextFile:
    def test_happy_path(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-rd")
        (context_dir / "doc.md").write_text("hello world")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = read_context_file("ctx-rd", "doc.md")

        assert result == "hello world"

    def test_missing_file_returns_none(self, tmp_path):
        _make_context_dir(tmp_path, "ctx-missing")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-missing", "nope.md") is None

    def test_nonexistent_dir_returns_none(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("no-such-dir", "anything.md") is None

    def test_path_traversal_rejected_forward_slash(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-trav")
        (context_dir / "real.md").write_text("real")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-trav", "../etc/passwd") is None
            assert read_context_file("ctx-trav", "sub/file.md") is None

    def test_path_traversal_rejected_backslash(self, tmp_path):
        _make_context_dir(tmp_path, "ctx-trav2")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-trav2", "..\\etc\\passwd") is None
            assert read_context_file("ctx-trav2", "sub\\file.md") is None

    def test_non_md_extension_rejected(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-ext")
        (context_dir / "secret.txt").write_text("should not leak")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-ext", "secret.txt") is None

    def test_empty_filename_rejected(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("any", "") is None
            assert read_context_file("any", None) is None

    def test_filename_with_dotdot_component_rejected(self, tmp_path):
        _make_context_dir(tmp_path, "ctx-dot")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-dot", "..file.md") is None
            assert read_context_file("ctx-dot", "file..md") is None

    def test_subdirectory_escape_rejected(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-sub")
        (context_dir / "sub").mkdir()
        (context_dir / "sub" / "deep.md").write_text("deep")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            # This filename contains a path separator → rejected outright.
            assert read_context_file("ctx-sub", "sub/deep.md") is None

    def test_result_is_valid_json_when_round_tripped(self, tmp_path):
        """`list_context_files` output JSON-decodes cleanly."""
        context_dir = _make_context_dir(tmp_path, "ctx-json")
        (context_dir / "alpha_20260601_000000.md").write_text("alpha content")
        (context_dir / "beta_20260602_000000.md").write_text("beta content")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-json")

        decoded = json.loads(json.dumps(result))
        assert isinstance(decoded, list)
        assert len(decoded) == 2


# ─── list_context_files query/filter ───────────────────────────────────────────


class TestListContextFilesQuery:
    """Tests for the optional ``query`` filter on :func:`list_context_files`."""

    def _make_populated_dir(self, tmp_path: Path) -> Path:
        context_dir = _make_context_dir(tmp_path, "ctx-q")
        (context_dir / "auth-flow_20260601_120000.md").write_text(
            "# Auth Flow\n\nHow users log in.\n"
        )
        (context_dir / "notes_20260602_130000.md").write_text(
            "Meeting notes from last week.\nThe team decided to migrate to Postgres.\n"
        )
        (context_dir / "deployment_20260603_090000.md").write_text(
            "Production deployment guide.\nUse the runbook in the wiki.\n"
        )
        return context_dir

    def test_empty_query_returns_all_files(self, tmp_path):
        self._make_populated_dir(tmp_path)
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-q", query="")
        assert len(result) == 3
        assert {r["filename"] for r in result} == {
            "auth-flow_20260601_120000.md",
            "notes_20260602_130000.md",
            "deployment_20260603_090000.md",
        }

    def test_no_query_arg_returns_all_files(self, tmp_path):
        """Backward compatibility: omitting ``query`` returns all files."""
        self._make_populated_dir(tmp_path)
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-q")
        assert len(result) == 3

    def test_query_matches_filename(self, tmp_path):
        self._make_populated_dir(tmp_path)
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-q", query="deployment")
        assert [r["filename"] for r in result] == ["deployment_20260603_090000.md"]

    def test_query_matches_slug(self, tmp_path):
        """Match against the slug (filename minus timestamp and extension)."""
        self._make_populated_dir(tmp_path)
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-q", query="auth-flow")
        assert [r["filename"] for r in result] == ["auth-flow_20260601_120000.md"]

    def test_query_matches_preview_content(self, tmp_path):
        """Match against the concise_preview text."""
        self._make_populated_dir(tmp_path)
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-q", query="migrate")
        assert [r["filename"] for r in result] == ["notes_20260602_130000.md"]

    def test_query_matches_file_body_beyond_preview(self, tmp_path):
        """Terms that appear only after the 5-line preview still match."""
        context_dir = _make_context_dir(tmp_path, "ctx-body")
        (context_dir / "spec_20260601_000000.md").write_text(
            "Line 1.\nLine 2.\nLine 3.\nLine 4.\nLine 5.\n"
            "Line 6 — contains the rare token zorblax.\nLine 7.\n"
        )
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-body", query="zorblax")
        assert [r["filename"] for r in result] == ["spec_20260601_000000.md"]

    def test_query_no_match_returns_empty(self, tmp_path):
        self._make_populated_dir(tmp_path)
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-q", query="no-such-token-anywhere")
        assert result == []

    def test_query_is_case_insensitive(self, tmp_path):
        self._make_populated_dir(tmp_path)
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-q", query="AUTH-FLOW")
        assert [r["filename"] for r in result] == ["auth-flow_20260601_120000.md"]

    def test_query_filters_against_nonexistent_dir(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("does-not-exist", query="anything")
        assert result == []

    def test_query_with_multiple_matches(self, tmp_path):
        self._make_populated_dir(tmp_path)
        # "2026" appears in every filename.
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-q", query="2026")
        assert len(result) == 3

    def test_body_search_is_case_insensitive(self, tmp_path):
        """S1: The BODY content (not just metadata) is searched case-insensitively.

        A file whose body contains 'The API uses OAuth Tokens' should match
        queries in any case (e.g. 'oauth tokens', 'OAUTH TOKENS', 'oAuth TokenS').
        This guards against regressions where body search is re-introduced
        with `in` against the original-case content.
        """
        context_dir = _make_context_dir(tmp_path, "ctx-bsci")
        # Five non-empty preview lines, then the body term far below.
        (context_dir / "spec_20260601_000000.md").write_text(
            "Line 1.\nLine 2.\nLine 3.\nLine 4.\nLine 5.\n"
            "The API uses OAuth Tokens for service-to-service calls.\n"
            "Tokens are short-lived and rotated daily.\n"
        )

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            # Lowercase query against mixed-case body
            result = list_context_files("ctx-bsci", query="oauth tokens")
            assert [r["filename"] for r in result] == ["spec_20260601_000000.md"]

            # All-uppercase query
            result = list_context_files("ctx-bsci", query="OAUTH TOKENS")
            assert [r["filename"] for r in result] == ["spec_20260601_000000.md"]

            # Mixed-case query
            result = list_context_files("ctx-bsci", query="oAuth TokenS")
            assert [r["filename"] for r in result] == ["spec_20260601_000000.md"]

    def test_query_with_regex_metacharacters_is_literal(self, tmp_path):
        """S2: Regex metacharacters in queries are treated as literal characters.

        The matcher uses Python's `in` operator (substring), NOT a regex engine.
        Queries like '.*', '[', '(' must match literal occurrences in the body
        and must not raise or match every file.
        """
        context_dir = _make_context_dir(tmp_path, "ctx-regex")
        # File 1: contains ONLY the literal ".*" token
        (context_dir / "literal-dotstar_20260601_000000.md").write_text(
            "Doc body.\nThe pattern is .* a placeholder.\nMore content.\n"
        )
        # File 2: contains ONLY literal square brackets
        (context_dir / "literal-brackets_20260602_000000.md").write_text(
            "Doc body.\nIndexed like [first] for the head item.\nMore.\n"
        )
        # File 3: contains ONLY literal parentheses
        (context_dir / "literal-paren_20260603_000000.md").write_text(
            "Doc body.\nCall foo(bar) to compute the value.\nMore.\n"
        )
        # File 4: a control file that should NOT match any of the queries
        (context_dir / "unrelated_20260604_000000.md").write_text(
            "Unrelated content.\nNothing special here.\nMore.\n"
        )

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            # `.*` must match the literal ".*" only — not act as a wildcard.
            result = list_context_files("ctx-regex", query=".*")
            assert {r["filename"] for r in result} == {"literal-dotstar_20260601_000000.md"}

            # `[` should match the literal bracket only.
            result = list_context_files("ctx-regex", query="[")
            assert {r["filename"] for r in result} == {"literal-brackets_20260602_000000.md"}

            # `(` should match the literal parenthesis only.
            result = list_context_files("ctx-regex", query="(")
            assert {r["filename"] for r in result} == {"literal-paren_20260603_000000.md"}

    def test_unicode_content_in_preview_and_body_search(self, tmp_path):
        """S3: Unicode (non-ASCII) content round-trips through preview and body search.

        Both the preview extraction AND the body search must work correctly
        with multi-byte UTF-8 characters. Tests with both CJK and accented
        Latin characters.
        """
        context_dir = _make_context_dir(tmp_path, "ctx-uni")
        # File 1: Japanese content (CJK, 3-byte UTF-8 chars)
        (context_dir / "japanese_20260601_000000.md").write_text(
            "# 日本語タイトル\n"
            "\n"
            "これは日本語のテストです。\n"
            "ファイルの本文に日本語のテキストが含まれます。\n"
            "OAuth トークンの説明もここにあります。\n"
        )
        # File 2: French accented content (2-byte UTF-8 chars)
        (context_dir / "french_20260602_000000.md").write_text(
            "# Café résumé\n"
            "\n"
            "Le naïveté de l'approche est discutable.\n"
        )

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-uni")

        by_name = {r["filename"]: r for r in result}
        assert len(by_name) == 2

        # Preview round-trips Unicode unchanged.
        jp_preview = by_name["japanese_20260601_000000.md"]["concise_preview"]
        assert "日本語タイトル" in jp_preview
        assert "これは日本語のテストです。" in jp_preview

        fr_preview = by_name["french_20260602_000000.md"]["concise_preview"]
        assert "Café résumé" in fr_preview
        assert "naïveté" in fr_preview

        # Body search matches Unicode queries (case-insensitive).
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            # Japanese term found in body (not just preview)
            result = list_context_files("ctx-uni", query="OAuth トークン")
            assert {r["filename"] for r in result} == {"japanese_20260601_000000.md"}

            # Accented Latin term
            result = list_context_files("ctx-uni", query="naïveté")
            assert {r["filename"] for r in result} == {"french_20260602_000000.md"}

            # Case-insensitive Unicode search
            result = list_context_files("ctx-uni", query="CAFÉ")
            assert {r["filename"] for r in result} == {"french_20260602_000000.md"}

    def test_file_with_only_blank_lines(self, tmp_path):
        """S4: A file containing only whitespace/blank lines is handled gracefully.

        Different from an empty file (which is already covered by
        `test_empty_file_has_no_preview`): this file has content (newlines,
        spaces, tabs) but no real non-empty lines. Preview extraction must
        return an empty string, not crash on a join of zero lines.
        """
        context_dir = _make_context_dir(tmp_path, "ctx-blankonly")
        # Five lines, all whitespace — must not crash, preview must be empty.
        (context_dir / "blank_20260601_000000.md").write_text("\n\n   \n\t\n   \t  \n")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-blankonly")

        assert len(result) == 1
        entry = result[0]
        # Preview is empty because every line was blank/whitespace.
        assert entry["concise_preview"] == ""
        # Internal _content must NOT leak into the public output.
        assert "_content" not in entry
        # And a query for a term in the body still works (file is read once
        # and stored as a string, even if its preview is empty).
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-blankonly", query="anything")
        # No real content in the file → no match.
        assert result == []
