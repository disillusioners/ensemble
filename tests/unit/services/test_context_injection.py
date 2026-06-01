"""Comprehensive unit tests for the context injection service."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from daemon.services.context_injection import (
    MatchedFile,
    INJECTION_TOKEN_CAP,
    MATCH_THRESHOLD,
    _tokenize_slug,
    _tokenize_query,
    _match_score,
    _extract_slug_from_filename,
    _parse_sections,
    _extract_first_sentence,
    _truncate_to_tokens,
    _match_context_files,
    _format_injection,
    _increase_heading_levels,
    get_shared_context,
)


# ─── TestTokenization ────────────────────────────────────────────────────────────


class TestTokenization:
    """Tests for _tokenize_slug and _tokenize_query functions."""

    def test_tokenize_slug_basic(self):
        """Basic slug tokenization splits on hyphen."""
        result = _tokenize_slug("auth-module-jwt-tokens")
        assert result == {"auth", "module", "jwt", "tokens"}

    def test_tokenize_slug_removes_stopwords(self):
        """Stop words like 'how', 'the', 'does', 'work' are filtered."""
        result = _tokenize_slug("how-does-the-auth-work")
        assert result == {"auth", "work"}

    def test_tokenize_slug_filters_short(self):
        """Tokens with length < 2 are filtered."""
        result = _tokenize_slug("a-b-c-real")
        assert result == {"real"}

    def test_tokenize_slug_empty(self):
        """Empty slug returns empty set."""
        result = _tokenize_slug("")
        assert result == set()

    def test_tokenize_slug_all_stopwords(self):
        """Slug with only stop words returns empty set."""
        result = _tokenize_slug("the-a-is-of")
        assert result == set()

    def test_tokenize_query_basic(self):
        """Query tokenization filters stop words and short tokens."""
        result = _tokenize_query("How does the auth system work?")
        # 'how', 'the', 'does' are stop words, 'auth', 'system', 'work' remain
        assert "auth" in result
        assert "system" in result
        assert "work" in result
        assert "how" not in result
        assert "the" not in result
        assert "does" not in result

    def test_tokenize_query_with_special_chars(self):
        """Query with special characters is normalized correctly."""
        result = _tokenize_query("knowledge_tools.py explore() function")
        assert "knowledge" in result
        assert "tools" in result  # 'py' is 2 chars but lowercase
        assert "explore" in result
        assert "function" in result


# ─── TestMatchScore ──────────────────────────────────────────────────────────────


class TestMatchScore:
    """Tests for _match_score function."""

    def test_identical_sets(self):
        """Identical sets return 1.0."""
        result = _match_score({"a", "b"}, {"a", "b"})
        assert result == 1.0

    def test_no_overlap(self):
        """Disjoint sets return 0.0."""
        result = _match_score({"a", "b"}, {"c", "d"})
        assert result == 0.0

    def test_short_query_full_recall(self):
        """Short query fully contained in slug returns 1.0 (key asymmetric case)."""
        result = _match_score({"auth", "module"}, {"auth", "module", "jwt", "tokens"})
        assert result == 1.0

    def test_short_query_partial_recall(self):
        """Partial overlap uses recall: intersection / len(query)."""
        result = _match_score({"auth", "system"}, {"auth", "module", "jwt"})
        # intersection = {"auth"}, len(query) = 2
        assert result == 0.5

    def test_long_sets_use_recall(self):
        """Sets with 3+ tokens both sides use recall-oriented scoring."""
        # All sets use recall: len(intersection) / len(query)
        query_tokens = {"auth", "module", "system"}
        slug_tokens = {"auth", "module", "jwt", "tokens"}
        intersection = {"auth", "module"}  # 2 elements
        expected = len(intersection) / len(query_tokens)  # 2/3 = 0.666...
        result = _match_score(query_tokens, slug_tokens)
        assert result == expected

    def test_empty_query_returns_zero(self):
        """Empty query tokens returns 0.0."""
        result = _match_score(set(), {"a", "b"})
        assert result == 0.0

    def test_empty_slug_returns_zero(self):
        """Empty slug tokens returns 0.0."""
        result = _match_score({"a", "b"}, set())
        assert result == 0.0

    def test_both_empty_returns_zero(self):
        """Both empty returns 0.0."""
        result = _match_score(set(), set())
        assert result == 0.0


# ─── TestExtractSlugFromFilename ────────────────────────────────────────────────


class TestExtractSlugFromFilename:
    """Tests for _extract_slug_from_filename function."""

    def test_standard_filename(self):
        """Standard filename with timestamp is parsed correctly."""
        result = _extract_slug_from_filename("auth-module-jwt_20260531_231255.md")
        assert result == "auth-module-jwt"

    def test_no_timestamp(self):
        """Filename without timestamp just strips .md extension."""
        result = _extract_slug_from_filename("auth-module-jwt.md")
        assert result == "auth-module-jwt"

    def test_long_slug(self):
        """Long slug names are preserved."""
        result = _extract_slug_from_filename("very-long-context-filename_20260531_231255.md")
        assert result == "very-long-context-filename"


# ─── TestParseSections ───────────────────────────────────────────────────────────


class TestParseSections:
    """Tests for _parse_sections function."""

    def test_parse_standard_response(self):
        """Standard response with Confidence, Concise, Answer, Sources."""
        content = """## Confidence
High confidence.

## Concise
This is the concise answer.

## Answer
This is the full answer.

## Sources
- Source 1
- Source 2
"""
        result = _parse_sections(content)
        assert "Confidence" in result
        assert result["Confidence"] == "High confidence."
        assert "Concise" in result
        assert result["Concise"] == "This is the concise answer."
        assert "Answer" in result
        assert result["Answer"] == "This is the full answer."
        assert "Sources" in result
        assert "Source 1" in result["Sources"]

    def test_parse_no_concise_section(self):
        """Old format without Concise section still parses Answer."""
        content = """## Answer
This is the answer without concise.

## Confidence
Medium
"""
        result = _parse_sections(content)
        assert "Answer" in result
        assert "This is the answer without concise." in result["Answer"]
        assert "Concise" not in result

    def test_parse_with_file_header(self):
        """File with "# Explorer Result:" header is handled."""
        content = """# Explorer Result:

## Answer
The answer starts here.

## Confidence
High
"""
        result = _parse_sections(content)
        # Should still parse ## headings
        assert "Answer" in result
        assert "Confidence" in result

    def test_parse_empty_content(self):
        """Empty content returns empty dict."""
        result = _parse_sections("")
        assert result == {}


# ─── TestExtractFirstSentence ────────────────────────────────────────────────────


class TestExtractFirstSentence:
    """Tests for _extract_first_sentence function."""

    def test_basic_sentence(self):
        """Basic sentence extraction splits on period."""
        result = _extract_first_sentence("This is the first sentence. This is the second.")
        assert result == "This is the first sentence."

    def test_single_sentence(self):
        """Single sentence without ending returns full text."""
        result = _extract_first_sentence("This is a single sentence")
        assert result == "This is a single sentence"

    def test_empty_string(self):
        """Empty string returns empty string."""
        result = _extract_first_sentence("")
        assert result == ""

    def test_leading_whitespace(self):
        """Leading whitespace is stripped."""
        result = _extract_first_sentence("  First sentence here. Second sentence.")
        assert result == "First sentence here."


# ─── TestTruncateToTokens ────────────────────────────────────────────────────────


class TestTruncateToTokens:
    """Tests for _truncate_to_tokens function."""

    def test_short_text_not_truncated(self):
        """Short text under limit is returned unchanged."""
        text = "This is a short text."
        result = _truncate_to_tokens(text, 100)
        assert result == text

    def test_long_text_truncated(self):
        """Long text is truncated and ends with '...'."""
        # 50 tokens * 4 chars = 200 char limit
        text = "This is a very long sentence. " * 20
        result = _truncate_to_tokens(text, 50)
        assert result.endswith("...")
        assert len(result) < len(text)

    def test_exact_limit(self):
        """Text exactly at limit is returned unchanged."""
        # Create text of exactly 80 chars (20 tokens * 4)
        text = "a" * 80
        result = _truncate_to_tokens(text, 20)
        assert result == text


# ─── TestMatchContextFiles ───────────────────────────────────────────────────────


class TestMatchContextFiles:
    """Tests for _match_context_files function."""

    def test_no_context_dir(self, tmp_path):
        """Nonexistent directory returns empty list."""
        nonexistent = tmp_path / "nonexistent_dir"
        result = _match_context_files("auth module", nonexistent)
        assert result == []

    def test_empty_context_dir(self, tmp_path):
        """Empty directory returns empty list."""
        result = _match_context_files("auth module", tmp_path)
        assert result == []

    def test_matching_files_found(self, tmp_path):
        """Matching files are found and scored."""
        # Create matching file
        auth_file = tmp_path / "auth-module-jwt_20260531_231255.md"
        auth_file.write_text("""## Answer
This is about auth modules.

## Confidence
High
""")
        # Create non-matching file
        other_file = tmp_path / "unrelated-stuff.md"
        other_file.write_text("## Answer\nUnrelated content.\n")

        result = _match_context_files("auth module", tmp_path)
        assert len(result) == 1
        assert result[0].slug == "auth-module-jwt"
        assert result[0].score >= MATCH_THRESHOLD

    def test_no_matching_files(self, tmp_path):
        """No files matching query return empty list."""
        file = tmp_path / "unrelated.md"
        file.write_text("## Answer\nUnrelated content.\n")

        result = _match_context_files("zzzzzzz yyyyy", tmp_path)
        assert result == []

    def test_files_without_concise_use_answer(self, tmp_path):
        """Files without Concise section use Answer for first_sentence."""
        file = tmp_path / "useful-info_20260531_231255.md"
        file.write_text("""## Answer
This is the answer section with useful info.

## Confidence
Medium
""")

        result = _match_context_files("useful info", tmp_path)
        assert len(result) == 1
        assert "This is the answer section" in result[0].first_sentence

    def test_corrupt_file_skipped_gracefully(self, tmp_path):
        """One file with OSError doesn't stop scan of other files."""
        # Create good file that matches query
        good_file = tmp_path / "auth-module_20260531_231255.md"
        good_file.write_text("## Answer\nAuth content.\n")
        # Create corrupt file with a slug that would match by score
        corrupt_file = tmp_path / "auth-something_20260531_231255.md"
        corrupt_file.write_text("## Answer\nCorrupt content.\n")

        # Force read_text to raise OSError only for the corrupt file
        original_read_text = Path.read_text

        def mock_read_text(self, encoding=None, errors=None):
            if "auth-something" in str(self):
                raise OSError("Simulated read error")
            return original_read_text(self, encoding=encoding, errors=errors)

        with patch("pathlib.Path.read_text", mock_read_text):
            result = _match_context_files("auth module", tmp_path)

        # Good file should be matched despite corrupt file failing
        assert len(result) == 1
        assert result[0].slug == "auth-module"
        assert result[0].score >= MATCH_THRESHOLD

    def test_short_query_matches_long_slug(self, tmp_path):
        """Short 2-token query matches 4-token slug with full recall."""
        file = tmp_path / "auth-module-jwt-tokens_20260531_231255.md"
        file.write_text("## Answer\nContent about auth and modules.\n")

        # 2 tokens vs 4 tokens -> recall = 2/2 = 1.0
        result = _match_context_files("auth module", tmp_path)
        assert len(result) == 1
        assert result[0].score == 1.0


# ─── TestFormatInjection ────────────────────────────────────────────────────────


class TestFormatInjection:
    """Tests for _format_injection function."""

    def test_empty_matches(self):
        """Empty matches return empty string."""
        result = _format_injection([])
        assert result == ""

    def test_top_match_always_included(self, tmp_path):
        """Match 1 is ALWAYS included regardless of score."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create file with low score - should still be included as #1
        file = context_dir / "low-score-file_20260531_120000.md"
        file.write_text("This is the full content of the file.")

        matched = [
            MatchedFile(
                filename="low-score-file_20260531_120000.md",
                slug="low-score-file",
                score=0.15,  # Very low score
                sections={},
                first_sentence="First sentence.",
            )
        ]
        result = _format_injection(matched, context_dir=context_dir)
        assert "# Shared Context" in result
        assert "### low-score-file (15% match)" in result
        assert "This is the full content of the file." in result

    def test_top_match_truncated_only_if_exceeds_cap(self, tmp_path):
        """Match 1 is truncated only if its content alone exceeds INJECTION_TOKEN_CAP."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create file with large content (~2500+ tokens)
        large_content = "Word " * 4000  # ~20000 chars ~= 2500 tokens
        file = context_dir / "large-file_20260531_120000.md"
        file.write_text(large_content)

        matched = [
            MatchedFile(
                filename="large-file_20260531_120000.md",
                slug="large-file",
                score=0.90,
                sections={},
                first_sentence="Large content.",
            )
        ]
        result = _format_injection(matched, context_dir=context_dir)
        assert "# Shared Context" in result
        # Content should be truncated (ends with ...)
        assert "..." in result
        # Full content should NOT be present
        assert len(result) < len(large_content) + 200  # Allow for header

    def test_second_match_included_only_if_score_gt_60(self, tmp_path):
        """Match 2 is ONLY included if score > 60%."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create two files
        file1 = context_dir / "match1_20260531_120000.md"
        file1.write_text("Content of first match.")
        file2 = context_dir / "match2_20260531_120001.md"
        file2.write_text("Content of second match.")

        matched = [
            MatchedFile(
                filename="match1_20260531_120000.md",
                slug="match1",
                score=0.90,
                sections={},
                first_sentence="First.",
            ),
            MatchedFile(
                filename="match2_20260531_120001.md",
                slug="match2",
                score=0.65,  # Exactly 65% - should be included (strictly > 60%)
                sections={},
                first_sentence="Second.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        assert "### match1 (90% match)" in result
        assert "### match2 (65% match)" in result
        assert "Content of first match." in result
        assert "Content of second match." in result

    def test_second_match_not_included_if_score_lte_60(self, tmp_path):
        """Match 2 is NOT included if score <= 60%."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create two files
        file1 = context_dir / "match1_20260531_120000.md"
        file1.write_text("Content of first match.")
        file2 = context_dir / "match2_20260531_120001.md"
        file2.write_text("Content of second match - should not appear.")

        matched = [
            MatchedFile(
                filename="match1_20260531_120000.md",
                slug="match1",
                score=0.90,
                sections={},
                first_sentence="First.",
            ),
            MatchedFile(
                filename="match2_20260531_120001.md",
                slug="match2",
                score=0.60,  # Exactly 60% - should NOT be included
                sections={},
                first_sentence="Second.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        assert "### match1 (90% match)" in result
        assert "### match2" not in result  # Second match should not appear
        assert "Content of second match" not in result

    def test_third_match_included_only_if_score_gt_60(self, tmp_path):
        """Match 3 is ONLY included if score > 60%."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create three files
        file1 = context_dir / "match1_20260531_120000.md"
        file1.write_text("Content of first match.")
        file2 = context_dir / "match2_20260531_120001.md"
        file2.write_text("Content of second match.")
        file3 = context_dir / "match3_20260531_120002.md"
        file3.write_text("Content of third match.")

        matched = [
            MatchedFile(
                filename="match1_20260531_120000.md",
                slug="match1",
                score=0.90,
                sections={},
                first_sentence="First.",
            ),
            MatchedFile(
                filename="match2_20260531_120001.md",
                slug="match2",
                score=0.70,  # > 60%, should be included
                sections={},
                first_sentence="Second.",
            ),
            MatchedFile(
                filename="match3_20260531_120002.md",
                slug="match3",
                score=0.65,  # Exactly 65% - should be included (strictly > 60%)
                sections={},
                first_sentence="Third.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        assert "### match1 (90% match)" in result
        assert "### match2 (70% match)" in result
        assert "### match3 (65% match)" in result
        assert "Content of first match." in result
        assert "Content of second match." in result
        assert "Content of third match." in result

    def test_third_match_not_included_if_score_lte_60(self, tmp_path):
        """Match 3 is NOT included if score <= 60%."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create three files
        file1 = context_dir / "match1_20260531_120000.md"
        file1.write_text("Content of first match.")
        file2 = context_dir / "match2_20260531_120001.md"
        file2.write_text("Content of second match.")
        file3 = context_dir / "match3_20260531_120002.md"
        file3.write_text("Content of third match - should not appear.")

        matched = [
            MatchedFile(
                filename="match1_20260531_120000.md",
                slug="match1",
                score=0.90,
                sections={},
                first_sentence="First.",
            ),
            MatchedFile(
                filename="match2_20260531_120001.md",
                slug="match2",
                score=0.70,  # > 60%, should be included
                sections={},
                first_sentence="Second.",
            ),
            MatchedFile(
                filename="match3_20260531_120002.md",
                slug="match3",
                score=0.60,  # Exactly 60% - should NOT be included
                sections={},
                first_sentence="Third.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        assert "### match1 (90% match)" in result
        assert "### match2 (70% match)" in result
        assert "### match3" not in result  # Third match should not appear
        assert "Content of third match" not in result

    def test_fourth_match_never_included(self, tmp_path):
        """Match 4 and beyond are NEVER included in pre-loaded context."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create four files
        for i in range(1, 5):
            file = context_dir / f"match{i}_20260531_12000{i}.md"
            file.write_text(f"Content of match {i}.")

        matched = [
            MatchedFile(
                filename="match1_20260531_120001.md",
                slug="match1",
                score=0.90,
                sections={},
                first_sentence="First.",
            ),
            MatchedFile(
                filename="match2_20260531_120002.md",
                slug="match2",
                score=0.75,  # > 60%, should be included
                sections={},
                first_sentence="Second.",
            ),
            MatchedFile(
                filename="match3_20260531_120003.md",
                slug="match3",
                score=0.70,  # > 60%, should be included
                sections={},
                first_sentence="Third.",
            ),
            MatchedFile(
                filename="match4_20260531_120004.md",
                slug="match4",
                score=0.50,  # <= 60%, should NOT be included
                sections={},
                first_sentence="Fourth - should not appear.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        assert "### match1 (90% match)" in result
        assert "### match2 (75% match)" in result
        assert "### match3 (70% match)" in result
        assert "### match4" not in result  # Fourth match should not appear

    def test_full_file_content_used_not_section_based(self, tmp_path):
        """Full file content is used, not section-based extraction."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create file with sections
        file = context_dir / "sectioned-file_20260531_120000.md"
        file.write_text("""## Concise
Concise section content.

## Answer
Full answer section content that is different.

## Confidence
High
""")

        matched = [
            MatchedFile(
                filename="sectioned-file_20260531_120000.md",
                slug="sectioned-file",
                score=0.90,
                sections={"Concise": "Concise section content.", "Answer": "Full answer section content that is different."},
                first_sentence="Concise section content.",
            )
        ]
        result = _format_injection(matched, context_dir=context_dir)
        # Full file content should be used, including all sections
        assert "## Concise" in result
        assert "## Answer" in result
        assert "## Confidence" in result

    def test_file_index_appended(self, tmp_path):
        """File index table is appended to output with pre-loaded exclusion and new header format."""
        # Create context directory with files
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create matching files (1 high score that matches, 1 low score that doesn't pre-load, 1 unmatched)
        # Use proper timestamp format: _YYYYMMDD_HHMMSS
        auth_file = context_dir / "auth-module_20260531_120000.md"
        auth_file.write_text("Auth content.")
        other_file = context_dir / "other-file_20260531_120001.md"
        other_file.write_text("Other content.")
        unmatched_file = context_dir / "unrelated_20260531_120002.md"
        unmatched_file.write_text("Unrelated content.")

        matched = [
            MatchedFile(
                filename="auth-module_20260531_120000.md",
                slug="auth-module",
                score=0.70,
                sections={},
                first_sentence="Auth content.",
            ),
            MatchedFile(
                filename="other-file_20260531_120001.md",
                slug="other-file",
                score=0.50,  # <= 60%, won't be pre-loaded but appears in index (above threshold)
                sections={},
                first_sentence="Other content.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        assert "# Shared Context" in result
        assert "## Available Context Files" in result
        # Header format: (remaining files, pre-loaded files)
        # other-file (50%) and unrelated (0%) = 2 remaining, 1 pre-loaded (auth-module)
        assert "(2 files, 1 pre-loaded)" in result
        assert "| File | Match | Summary |" in result
        # Pre-loaded file should NOT appear in index
        assert "| auth-module_20260531_120000.md |" not in result
        # other-file and unrelated should appear in index
        assert "| other-file_20260531_120001.md |" in result
        assert "| unrelated_20260531_120002.md |" in result
        # Separator should be present
        assert "\n---\n\n" in result

    def test_global_token_cap_enforced(self, tmp_path):
        """When total content exceeds INJECTION_TOKEN_CAP, second match is truncated."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create files with large content (~1200+ tokens each)
        # "Word " * 2000 = ~10000 chars ~= 1250 tokens per file
        large_content = "Word " * 2000
        file1 = context_dir / "large1_20260531_120000.md"
        file1.write_text(large_content)
        file2 = context_dir / "large2_20260531_120001.md"
        file2.write_text(large_content)

        matched = [
            MatchedFile(
                filename="large1_20260531_120000.md",
                slug="large1",
                score=0.90,
                sections={},
                first_sentence="Large content file.",
            ),
            MatchedFile(
                filename="large2_20260531_120001.md",
                slug="large2",
                score=0.70,  # > 60%
                sections={},
                first_sentence="Large content file 2.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        # Match 1 should be present
        assert "### large1 (90% match)" in result
        # Match 2 should be present but truncated
        assert "### large2 (70% match)" in result
        # Total content should fit within cap - second should be truncated
        assert "..." in result  # Some truncation occurred

    def test_all_files_preloaded_skips_index(self, tmp_path):
        """When all files in context_dir are pre-loaded, index section is skipped."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create only matching files (both will be pre-loaded)
        # Use proper timestamp format: _YYYYMMDD_HHMMSS
        auth_file = context_dir / "auth-module_20260531_120000.md"
        auth_file.write_text("Auth content.")
        other_file = context_dir / "other-file_20260531_120001.md"
        other_file.write_text("Other content.")

        matched = [
            MatchedFile(
                filename="auth-module_20260531_120000.md",
                slug="auth-module",
                score=0.90,
                sections={},
                first_sentence="Auth content.",
            ),
            MatchedFile(
                filename="other-file_20260531_120001.md",
                slug="other-file",
                score=0.75,  # > 60%
                sections={},
                first_sentence="Other content.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        # Pre-loaded content should appear
        assert "# Shared Context" in result
        assert "## Pre-loaded Context" in result
        assert "auth-module" in result
        assert "other-file" in result
        # Index should be skipped (all files were pre-loaded)
        assert "## Available Context Files" not in result

    def test_no_files_preloaded_simple_header(self, tmp_path):
        """When no files are pre-loaded, header shows simple count (no pre-loaded mention)."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create files that don't match the query (so they won't be pre-loaded)
        # Use proper timestamp format: _YYYYMMDD_HHMMSS
        file1 = context_dir / "alpha-beta_20260531_120000.md"
        file1.write_text("Alpha beta content.")
        file2 = context_dir / "gamma-delta_20260531_120001.md"
        file2.write_text("Gamma delta content.")

        # Don't create matched files - test the index with no pre-loaded files
        result = _format_injection([], context_dir=context_dir)
        # Header should show (2 files) since no pre-loaded files exist
        assert "(2 files)" in result
        # Both files should appear in index
        assert "| gamma-delta_20260531_120001.md |" in result
        assert "| alpha-beta_20260531_120000.md |" in result

    def test_increase_heading_levels(self, tmp_path):
        """Pre-loaded file headings are increased by one level."""
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create file with headings
        file1 = context_dir / "match1_20260531_120000.md"
        file1.write_text("""# Title
Some content

## Subtitle
More content

### Sub-subtitle
Even more content

Regular paragraph without heading.
""")

        matched = [
            MatchedFile(
                filename="match1_20260531_120000.md",
                slug="match1",
                score=0.90,
                sections={},
                first_sentence="Title.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        # Headings should be increased by one level
        assert "## Title" in result  # # -> ##
        assert "### Subtitle" in result  # ## -> ###
        assert "#### Sub-subtitle" in result  # ### -> ####
        # Regular content should remain unchanged
        assert "Regular paragraph without heading." in result


# ─── TestGetSharedContext (PUBLIC API) ──────────────────────────────────────────


class TestGetSharedContext:
    """Tests for get_shared_context public API."""

    def test_returns_injection_for_matching_files(self, tmp_path):
        """Happy path: returns injection string when files match."""
        context_dir = tmp_path / "ensemble" / "context" / "test-key"
        context_dir.mkdir(parents=True)

        # Create test file
        auth_file = context_dir / "auth-module_20260531_231255.md"
        auth_file.write_text("""## Answer
This is about auth modules.

## Confidence
High
""")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("test-key", "auth module")

        assert result is not None
        assert "# Shared Context" in result
        assert "## Context dir:" in result
        assert "## Pre-loaded Context" in result
        assert "auth-module" in result

    def test_returns_empty_format_for_empty_dir(self, tmp_path):
        """Empty context directory returns empty format string."""
        context_dir = tmp_path / "ensemble" / "context" / "empty-key"
        context_dir.mkdir(parents=True)

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("empty-key", "auth module")

        assert result is not None
        assert "# Shared Context" in result
        assert "## Context dir:" in result
        assert "## Pre-loaded Context" in result
        assert "There is no context yet" in result

    def test_returns_empty_format_for_no_matches(self, tmp_path):
        """Directory with no matching files returns empty format string."""
        context_dir = tmp_path / "ensemble" / "context" / "no-match-key"
        context_dir.mkdir(parents=True)

        # Create unrelated file
        unrelated = context_dir / "unrelated_20260531.md"
        unrelated.write_text("## Answer\nUnrelated content.\n")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("no-match-key", "zzzzz yyyyy")

        assert result is not None
        assert "# Shared Context" in result
        assert "## Context dir:" in result
        assert "There is no context yet" in result

    def test_returns_empty_format_on_error(self):
        """OSError during file operations returns empty format string."""
        with patch("tempfile.gettempdir", side_effect=OSError("Permission denied")):
            result = get_shared_context("test-key", "auth module")

        assert result is not None
        assert "# Shared Context" in result
        assert "There is no context yet" in result

    def test_returns_empty_format_for_nonexistent_context_key(self, tmp_path):
        """Nonexistent context key returns empty format string."""
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("nonexistent-key-12345", "auth module")

        assert result is not None
        assert "# Shared Context" in result
        assert "There is no context yet" in result

    def test_returns_empty_format_for_none_context_key(self):
        """None context_key returns empty format string gracefully."""
        result = get_shared_context(None, "auth module")
        assert result is not None
        assert "# Shared Context" in result
        assert "There is no context yet" in result

    def test_happy_path_real_filesystem(self, tmp_path):
        """Integration test with real file operations."""
        context_dir = tmp_path / "ensemble" / "context" / "test-project"
        context_dir.mkdir(parents=True)

        # Create matching file
        auth_file = context_dir / "auth-module_20260531_231255.md"
        auth_file.write_text("""## Concise
Auth module provides authentication.

## Answer
Full answer about authentication module with detailed information.

## Confidence
High
""")
        # Create an additional file to ensure index is shown
        other_file = context_dir / "other-info_20260531_231256.md"
        other_file.write_text("""## Concise
Other information.
""")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("test-project", "auth module")

        assert result is not None
        assert "# Shared Context" in result
        assert "## Context dir:" in result
        assert "## Pre-loaded Context" in result
        assert "auth-module" in result
        # Since auth-module is pre-loaded, it should not appear in index
        assert "## Available Context Files" in result
        # Only other-file should appear in index
        assert "| other-info_20260531_231256.md |" in result
        assert "| auth-module_20260531_231255.md |" not in result
