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

    def test_no_matching_files_falls_back_to_highest(self, tmp_path):
        """When no file passes the threshold, fall back to the highest-
        scoring file (even if 0%) so the pre-loaded block is never empty
        when the dir has any files.

        This honors the "highest match is always pre-loaded" rule.
        """
        file = tmp_path / "unrelated.md"
        file.write_text("## Answer\nUnrelated content.\n")

        result = _match_context_files("zzzzzzz yyyyy", tmp_path)
        assert len(result) == 1
        assert result[0].filename == "unrelated.md"
        # The fallback's score must be below the threshold (otherwise the
        # fallback branch wouldn't have triggered).
        assert result[0].score < MATCH_THRESHOLD
        # But the file content is still parsed so the agent can use it.
        assert result[0].sections.get("Answer", "").strip() == "Unrelated content."

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
        result = _format_injection([], context_key="test-key")
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
        assert "# Shared Context" in result
        # The pre-loaded header uses the FULL on-disk filename (timestamp +
        # .md) so agents can copy it directly into read_context_file without
        # a "file not found" error.
        assert "## low-score-file_20260531_120000.md (15% match)" in result
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
        assert "## match1_20260531_120000.md (90% match)" in result
        assert "## match2_20260531_120001.md (65% match)" in result
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
        assert "## match1_20260531_120000.md (90% match)" in result
        assert "## match2_20260531_120001.md" not in result  # Second match should not appear
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
        assert "## match1_20260531_120000.md (90% match)" in result
        assert "## match2_20260531_120001.md (70% match)" in result
        assert "## match3_20260531_120002.md (65% match)" in result
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
        assert "## match1_20260531_120000.md (90% match)" in result
        assert "## match2_20260531_120001.md (70% match)" in result
        assert "## match3_20260531_120002.md" not in result  # Third match should not appear
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
        assert "## match1_20260531_120001.md (90% match)" in result
        assert "## match2_20260531_120002.md (75% match)" in result
        assert "## match3_20260531_120003.md (70% match)" in result
        assert "## match4_20260531_120004.md" not in result  # Fourth match should not appear

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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
        # Match 1 should be present (full filename including timestamp + .md)
        assert "## large1_20260531_120000.md (90% match)" in result
        # Match 2 should be present but truncated
        assert "## large2_20260531_120001.md (70% match)" in result
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
        # Pre-loaded content should appear
        assert "# Shared Context" in result
        assert "# Pre-loaded Context (auto-matched)" in result
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
        result = _format_injection([], context_key="test-key", context_dir=context_dir)
        # Header should show (2 files) since no pre-loaded files exist
        assert "(2 files)" in result
        # Both files should appear in index
        assert "| gamma-delta_20260531_120001.md |" in result
        assert "| alpha-beta_20260531_120000.md |" in result

    def test_increase_heading_levels(self, tmp_path):
        """Pre-loaded file headings are increased by two levels."""
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
        result = _format_injection(matched, context_key="test-key", context_dir=context_dir)
        # Headings should be increased by two levels
        assert "### Title" in result  # # -> ###
        assert "#### Subtitle" in result  # ## -> ####
        assert "##### Sub-subtitle" in result  # ### -> #####
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
        assert "context_key: test-key" in result
        assert "# Pre-loaded Context (auto-matched)" in result
        assert "auth-module" in result

    def test_returns_empty_format_for_empty_dir(self, tmp_path):
        """Empty context directory returns empty format string."""
        context_dir = tmp_path / "ensemble" / "context" / "empty-key"
        context_dir.mkdir(parents=True)

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("empty-key", "auth module")

        assert result is not None
        assert "# Shared Context" in result
        assert "context_key: empty-key" in result
        assert "# Pre-loaded Context (auto-matched)" in result
        assert "There is no context yet" in result

    def test_returns_fallback_injection_when_no_threshold_match(self, tmp_path):
        """Directory with files but no threshold match still gets a
        pre-loaded block — the highest-scoring file is included as
        Match 1 (even at 0%) per the "always include the highest match"
        rule. The "There is no context yet." line is NOT shown because
        the dir is not empty.
        """
        context_dir = tmp_path / "ensemble" / "context" / "no-match-key"
        context_dir.mkdir(parents=True)

        # Create unrelated file
        unrelated = context_dir / "unrelated_20260531.md"
        unrelated.write_text("## Answer\nUnrelated content.\n")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("no-match-key", "zzzzz yyyyy")

        assert result is not None
        assert "# Shared Context" in result
        assert "context_key: no-match-key" in result
        # Highest-match fallback: the file is pre-loaded as Match 1.
        assert "## unrelated_20260531.md (0% match)" in result
        assert "Unrelated content." in result
        # The misleading "no context yet" line is gone because the dir
        # has files.
        assert "There is no context yet" not in result

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
        assert "context_key: test-project" in result
        assert "# Pre-loaded Context (auto-matched)" in result
        assert "auth-module" in result
        # Since auth-module is pre-loaded, it should not appear in index
        assert "## Available Context Files" in result
        # Only other-file should appear in index
        assert "| other-info_20260531_231256.md |" in result
        assert "| auth-module_20260531_231255.md |" not in result


class TestSharedContextHints:
    """The preloaded section teaches agents how to read more via the context tools.

    The hint is wrapped in a ``## Context Guidelines:`` section at the very
    end of the injection so the LLM sees the pre-loaded content first and
    only then the pointer to fetch more. The section header also gives future
    guidelines a stable place to live.
    """

    def test_happy_path_includes_tool_hint(self, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "hint-key"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("hint-key", "doc")

        assert "## Context Guidelines:" in result
        assert "list_context(context_key)" in result
        assert "read_context(context_key, filename)" in result
        # Hint appears at the END, after the pre-loaded content.
        assert result.rstrip().endswith(
            "read_context(context_key, filename)` to read."
        )
        # The pre-loaded header appears BEFORE the guidelines section.
        # Uses the FULL on-disk filename (timestamp + .md) so it resolves
        # cleanly via read_context_file's exact-match lookup.
        assert result.index("## doc_20260601_000000.md") < result.index("## Context Guidelines:")

    def test_empty_format_omits_tool_hint(self, tmp_path):
        """Internal audience: the 'Need more?' hint must NOT appear for an
        empty context dir.

        Suggesting ``list_context`` / ``read_context`` to internal agents for
        an empty context directory is misleading — the LangChain tool names
        are already in their system prompt and there is nothing to fetch.
        """
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("empty-hint-key", "anything")

        assert "There is no context yet" in result
        assert "## Context Guidelines:" not in result
        assert "list_context(context_key)" not in result
        assert "read_context(context_key, filename)" not in result

    def test_empty_format_external_keeps_guidelines(self, tmp_path):
        """External audience: guidelines block MUST still be present even
        when the context dir is empty.

        External sessions reach the system through MCP, so they need the
        tool names (``ensemble_context_list`` / ``ensemble_context_read``),
        the MCP RAG tool names, and the project context every time — even
        when there is nothing pre-loaded. Hiding them behind an empty dir
        would leave the remote agent with no usable path forward.
        """
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context(
                "empty-ext-key",
                "anything",
                audience="external",
                project_id="proj-123",
                project_name="My Project",
                critical_notes=[
                    {
                        "priority": "high",
                        "category": "warning",
                        "summary": "Always test the auth path.",
                    }
                ],
            )

        assert "There is no context yet" in result
        # The guidelines block is still appended so the remote agent can
        # call the right MCP tools.
        assert "## Context Guidelines:" in result
        assert "ensemble_context_list(context_key)" in result
        assert "ensemble_context_read(context_key, filename)" in result
        assert "MCP RAG tools" in result
        # And the project context / critical notes are surfaced too.
        assert 'project_id="proj-123"' in result
        assert 'project_name="My Project"' in result
        assert "Always test the auth path." in result

    def test_empty_format_internal_omits_external_only_project_info(self, tmp_path):
        """Internal audience: project metadata is NOT injected into the
        empty format. Internal agents get the bare message — they don't
        need the MCP RAG hint and they shouldn't be told about an external
        project context they have no way to use.
        """
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context(
                "empty-int-key",
                "anything",
                project_id="proj-123",
                project_name="My Project",
                critical_notes=[
                    {
                        "priority": "high",
                        "category": "warning",
                        "summary": "Internal must not see this.",
                    }
                ],
            )

        assert "There is no context yet" in result
        assert "## Context Guidelines:" not in result
        assert "MCP RAG tools" not in result
        assert 'project_id="proj-123"' not in result
        assert "Internal must not see this." not in result

    def test_no_matches_format_preloads_fallback_and_shows_index(self, tmp_path):
        """When the query has no threshold match but the dir has files,
        the highest-scoring file is pre-loaded as Match 1 (even at 0%),
        the file index is shown, and the "Need more?" hint is included.

        Guards the rule "highest match is always pre-loaded when the dir
        has files" so the pre-loaded block is never empty in that case.
        """
        context_dir = tmp_path / "ensemble" / "context" / "no-match-hint"
        context_dir.mkdir(parents=True)
        (context_dir / "unrelated_20260601_000000.md").write_text("## Answer\nfoo bar\n")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("no-match-hint", "zzzzz yyyyy")

        # Match 1 fallback is present (0% match is acceptable).
        # The header uses the FULL on-disk filename (timestamp + .md).
        assert "## unrelated_20260601_000000.md (0% match)" in result
        # The "no context yet" line is gone — the dir has files.
        assert "There is no context yet" not in result
        # And the "Need more?" hint is included so the agent knows how to
        # fetch more context if Match 1's content isn't enough.
        assert "## Context Guidelines:" in result
        assert "list_context(context_key)" in result
        assert "read_context(context_key, filename)" in result

    def test_always_include_highest_match_even_at_zero(self, tmp_path):
        """Regression test for the "always include highest match" rule.

        When the dir has files, the pre-loaded block is never empty:
        the file with the highest score is always pre-loaded as Match 1,
        even if the score is 0%. This ensures the pre-loaded context is
        non-empty whenever the dir has files, and that the agent always
        sees *some* file content (not just the file index).
        """
        context_dir = tmp_path / "ensemble" / "context" / "always-highest"
        context_dir.mkdir(parents=True)
        # Two unrelated files — neither matches the query.
        (context_dir / "alpha-topic_20260601_000000.md").write_text("## Answer\nalpha stuff\n")
        (context_dir / "beta-topic_20260602_000000.md").write_text("## Answer\nbeta stuff\n")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("always-highest", "completely unrelated query")

        # Match 1 is pre-loaded with the highest-scoring file (0% in this
        # case), so the pre-loaded block contains real content.
        assert "(0% match)" in result
        # The "no context yet" message is gone.
        assert "There is no context yet" not in result
        # The most recent (highest mtime) file wins the fallback — beta was
        # written second, so it gets pre-loaded as Match 1, and alpha stays
        # in the file index. The header uses the FULL on-disk filename.
        assert "## beta-topic_20260602_000000.md (0% match)" in result
        assert "alpha-topic_20260601_000000.md" in result

    def test_external_audience_uses_mcp_tool_names(self, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ext-hint-key"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("ext-hint-key", "doc", audience="external")

        assert "## Context Guidelines:" in result
        # Hosted MCP tool names appear in the hint, internal names do not.
        assert "ensemble_context_list(context_key)" in result
        assert "ensemble_context_read(context_key, filename)" in result
        # And the internal `list_context(` form is NOT present (as a standalone
        # tool name, not a substring of the MCP one).
        assert "list_context(context_key)" not in result
        assert "read_context(context_key, filename)" not in result
        # "Need more?" line precedes the MCP RAG hint line, and the block is
        # still the very last thing in the output.
        need_more_idx = result.index("Need more?")
        rag_idx = result.index("MCP RAG tools")
        assert need_more_idx < rag_idx
        # Preamble sits above the MCP RAG hint bullet.
        preamble_idx = result.index("master agent system named Ensemble")
        assert preamble_idx < rag_idx
        # `ensemble_kb_explore` / `ensemble_kb_experience` are still listed;
        # `ensemble_kb_list_projects` and `ensemble_kb_search_projects` were
        # removed because callers are expected to know their current project.
        assert "ensemble_kb_explore" in result
        assert "ensemble_kb_experience" in result
        assert "ensemble_kb_list_projects" not in result
        assert "ensemble_kb_search_projects" not in result

    def test_external_audience_includes_mcp_rag_hint(self, tmp_path):
        """External audience must see the MCP RAG-tools hint bullet."""
        context_dir = tmp_path / "ensemble" / "context" / "ext-rag-hint"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("ext-rag-hint", "doc", audience="external")

        # The preamble names the master agent system, then the RAG hint
        # label tells the agent that Ensemble provides the tools.
        assert "You are working under master agent system named Ensemble" in result
        assert "MCP RAG tools (provided by Ensemble,should use them if available):" in result
        # The remaining RAG-shaped tools are listed by name so external
        # agents can call them directly. The project discovery tools were
        # intentionally removed — callers are expected to know their project.
        for tool in ("ensemble_kb_explore", "ensemble_kb_experience"):
            assert tool in result, f"Expected {tool} in MCP RAG hint"
        for tool in ("ensemble_kb_list_projects", "ensemble_kb_search_projects"):
            assert tool not in result, f"{tool} should not appear in MCP RAG hint"

    def test_internal_audience_omits_mcp_rag_hint(self, tmp_path):
        """Internal audience must NOT see the MCP RAG-tools hint.

        The MCP RAG hint is only useful for external systems; internal agents
        already have the LangChain tool names in their system prompt and the
        extra bullet would just be noise.
        """
        context_dir = tmp_path / "ensemble" / "context" / "int-no-rag-hint"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("int-no-rag-hint", "doc")

        assert "## Context Guidelines:" in result
        assert "MCP RAG tools" not in result
        for tool in (
            "ensemble_kb_explore",
            "ensemble_kb_experience",
            "ensemble_kb_list_projects",
            "ensemble_kb_search_projects",
        ):
            assert tool not in result, f"Internal hint must not mention {tool}"

    def test_external_audience_includes_project_context(self, tmp_path):
        """External audience sees project_id / project_name in the RAG hint."""
        context_dir = tmp_path / "ensemble" / "context" / "ext-proj-ctx"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context(
                "ext-proj-ctx",
                "doc",
                audience="external",
                project_id="proj-abc-123",
                project_name="My Cool Project",
            )

        # Both pieces of project context appear in the hint bullet so the
        # external agent can scope MCP RAG tool calls.
        assert 'project_id="proj-abc-123"' in result
        assert 'project_name="My Cool Project"' in result
        assert "Current project context:" in result

    def test_external_audience_includes_critical_notes(self, tmp_path):
        """External audience sees top-N critical notes in the RAG hint."""
        context_dir = tmp_path / "ensemble" / "context" / "ext-crit-notes"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        critical_notes = [
            {
                "priority": "critical",
                "category": "warning",
                "summary": "Always run the auth check before any DB call.",
                "reference": "doc.md",
            },
            {
                "priority": "high",
                "category": "convention",
                "summary": "Use snake_case for function names.",
            },
            {
                "priority": "medium",
                "category": "tip",
                "summary": "Prefer pure functions in the data layer.",
            },
        ]

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context(
                "ext-crit-notes",
                "doc",
                audience="external",
                project_id="proj-xyz",
                project_name="Demo",
                critical_notes=critical_notes,
            )

        # Section header and each note's summary appear.
        assert "⚡ Critical notes" in result
        assert "Always run the auth check before any DB call." in result
        assert "Use snake_case for function names." in result
        assert "Prefer pure functions in the data layer." in result
        # Priority icon for the critical-priority entry is surfaced.
        assert "🔴" in result
        # Reference is shown as a parenthetical suffix.
        assert "(ref: doc.md)" in result

    def test_external_audience_critical_notes_cap(self, tmp_path):
        """Critical notes are capped to avoid blowing up the hint block."""
        context_dir = tmp_path / "ensemble" / "context" / "ext-cap"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        # 8 notes — more than the cap of 5.
        notes = [
            {
                "priority": "medium",
                "category": "tip",
                "summary": f"Note number {i}",
            }
            for i in range(8)
        ]

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context(
                "ext-cap",
                "doc",
                audience="external",
                critical_notes=notes,
            )

        # The first 5 are shown, the rest are summarized.
        assert "Note number 0" in result
        assert "Note number 4" in result
        assert "Note number 5" not in result
        assert "Note number 7" not in result
        assert "…and 3 more" in result
        assert "showing 5 of 8" in result

    def test_external_audience_critical_notes_skips_malformed(self, tmp_path):
        """Critical-note entries missing a summary are silently skipped."""
        context_dir = tmp_path / "ensemble" / "context" / "ext-skip"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        notes = [
            {"priority": "high", "category": "x", "summary": "good note"},
            {"priority": "high", "category": "x", "summary": ""},
            {"priority": "high", "category": "x"},
            "not-a-dict",
            {"priority": "high", "category": "x", "summary": "another good note"},
        ]

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context(
                "ext-skip",
                "doc",
                audience="external",
                critical_notes=notes,
            )

        assert "good note" in result
        assert "another good note" in result
        # No stray "…" overflow from a missing summary.
        assert "…" not in result

    def test_external_audience_critical_notes_truncate_long_summary(self, tmp_path):
        """Long critical-note summaries are truncated with an ellipsis."""
        context_dir = tmp_path / "ensemble" / "context" / "ext-trunc"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        long_summary = "x" * 250
        notes = [{"priority": "high", "category": "x", "summary": long_summary}]

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context(
                "ext-trunc",
                "doc",
                audience="external",
                critical_notes=notes,
            )

        # The full 250-char string must not appear verbatim — it gets
        # truncated to <= 100 chars + ellipsis.
        assert long_summary not in result
        assert "…" in result

    def test_internal_audience_ignores_critical_notes(self, tmp_path):
        """Critical notes are never surfaced to the internal audience."""
        context_dir = tmp_path / "ensemble" / "context" / "int-ignores-cn"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        notes = [
            {
                "priority": "critical",
                "category": "warning",
                "summary": "Internal must not see this.",
            }
        ]

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context(
                "int-ignores-cn",
                "doc",
                project_id="proj",
                project_name="P",
                critical_notes=notes,
            )

        assert "Critical notes" not in result
        assert "Internal must not see this." not in result

    def test_external_audience_empty_format_keeps_tool_hint(self, tmp_path):
        """External audience: empty context still includes the MCP tool hint
        (no project metadata here, but the basic MCP context tool names are
        part of the always-on guidelines)."""
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("ext-empty", "x", audience="external")

        assert "There is no context yet" in result
        # The "Need more?" bullet pointing at ensemble_context_* is always on
        # for external audiences — even with no project metadata at all.
        assert "## Context Guidelines:" in result
        assert "ensemble_context_list(context_key)" in result
        assert "ensemble_context_read(context_key, filename)" in result

    def test_internal_audience_is_default(self, tmp_path):
        """When ``audience`` is omitted, internal tool names are used."""
        context_dir = tmp_path / "ensemble" / "context" / "default-aud"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hi")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("default-aud", "doc")

        assert "## Context Guidelines:" in result
        assert "list_context(context_key)" in result
        # And the external form is not present.
        assert "ensemble_context_list(context_key)" not in result
        assert "ensemble_context_read(context_key, filename)" not in result


# ─── TestRoundTripFilenameLookup (REGRESSION) ──────────────────────────────────


class TestRoundTripFilenameLookup:
    """Regression tests for the pre-loaded header → read_context_file round-trip.

    The pre-loaded context header previously displayed ``{matched.slug}.md`` —
    a stripped name that does NOT exist on disk (the actual file is
    ``{slug}_{YYYYMMDD_HHMMSS}.md``). Agents copy-pasted the displayed name,
    fed it into ``read_context_file``, and got a "file not found" error.

    The fix is to display ``matched.filename`` (the full on-disk name) so the
    displayed name round-trips through ``read_context_file`` cleanly.

    These tests exercise the round-trip end-to-end:
    1. A file is saved to disk with timestamp suffix.
    2. The pre-loaded header surfaces the FULL filename.
    3. ``read_context_file`` resolves that displayed name back to the file.
    """

    def test_preloaded_header_displays_full_filename(self, tmp_path):
        """The pre-loaded header uses the full on-disk filename, not the slug."""
        context_dir = tmp_path / "ensemble" / "context" / "rt-header"
        context_dir.mkdir(parents=True)

        # Use a realistic timestamp format: {slug}_{YYYYMMDD_HHMMSS}.md
        filename = "context-injection-test_20260620_110000.md"
        (context_dir / filename).write_text("## Answer\nround-trip content\n")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("rt-header", "context injection test")

        assert result is not None
        # The header MUST display the full filename so read_context_file works.
        assert f"## {filename}" in result
        # And it MUST NOT display the stripped slug form (the bug).
        assert "## context-injection-test.md" not in result

    def test_preloaded_filename_resolves_via_read_context_file(self, tmp_path):
        """The displayed filename round-trips through read_context_file.

        This is the core regression: take the name shown to the agent in the
        pre-loaded header, hand it straight to ``read_context_file``, and
        confirm it resolves to the on-disk file (not a "file not found" error).
        """
        from daemon.services.context_tools import read_context_file

        context_dir = tmp_path / "ensemble" / "context" / "rt-resolve"
        context_dir.mkdir(parents=True)

        filename = "round-trip-lookup_20260620_120000.md"
        body = "## Answer\nround-trip body content\n"
        (context_dir / filename).write_text(body)

        # Both get_shared_context and read_context_file resolve the context
        # dir through tempfile.gettempdir(), so we patch it for the whole
        # round-trip — otherwise the second call would look in the real tmp.
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("rt-resolve", "round trip lookup")

            assert result is not None

            # Extract the filename the agent would copy from the pre-loaded
            # header. The header format is "## {filename} ({score_pct}% match)".
            import re
            match = re.search(
                r"^## (\S+_20\d{6}_\d{6}\.md) \(\d+% match\)",
                result,
                re.MULTILINE,
            )
            assert match is not None, (
                "Expected the pre-loaded header to expose a full timestamped "
                "filename, but the regex did not match the injection output:\n"
                + result
            )
            displayed_filename = match.group(1)

            # The displayed name MUST equal the on-disk filename.
            assert displayed_filename == filename

            # And the displayed name MUST resolve through read_context_file.
            contents = read_context_file("rt-resolve", displayed_filename)
            assert contents is not None, (
                f"read_context_file returned None for the displayed filename "
                f"{displayed_filename!r} — the round-trip is broken"
            )
            assert "round-trip body content" in contents

    def test_preloaded_filename_round_trip_against_slug_fails(self, tmp_path):
        """Sanity check: passing the bare slug to read_context_file MUST fail.

        Guards the bug: if anyone re-introduces the old behaviour of showing
        the slug, this test makes it obvious that the slug form does not
        resolve on disk — so the regression test (above) catches it.
        """
        from daemon.services.context_tools import read_context_file

        context_dir = tmp_path / "ensemble" / "context" / "rt-slug-fails"
        context_dir.mkdir(parents=True)

        # File on disk uses the timestamped name; the slug has no timestamp.
        filename = "slug-only_20260620_130000.md"
        (context_dir / filename).write_text("## Answer\nfoo\n")

        # Hand the bare slug form (what the buggy display used to show) to
        # read_context_file and confirm it does NOT resolve.
        contents = read_context_file("rt-slug-fails", "slug-only.md")
        assert contents is None, (
            "Expected read_context_file to fail when given the slug form, "
            "but it returned content. The slug form must not exist on disk."
        )
