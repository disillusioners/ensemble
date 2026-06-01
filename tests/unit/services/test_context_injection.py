"""Comprehensive unit tests for the context injection service."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from daemon.services.context_injection import (
    MatchedFile,
    TIER_HIGH,
    TIER_MEDIUM,
    TIER_LOW,
    INJECTION_TOKEN_CAP,
    MAX_HIGH_TIER_FILES,
    _tokenize_slug,
    _tokenize_query,
    _match_score,
    _extract_slug_from_filename,
    _parse_sections,
    _extract_first_sentence,
    _truncate_to_tokens,
    _match_context_files,
    _format_injection,
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
        assert result[0].score >= TIER_LOW

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
        assert result[0].score >= TIER_LOW

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

    def test_high_tier_injection(self):
        """High tier (>=0.80) uses Answer section."""
        matched = [
            MatchedFile(
                filename="auth-module_20260531.md",
                slug="auth-module",
                score=0.87,
                sections={"Answer": "This is the full answer about authentication."},
                first_sentence="This is the full answer.",
            )
        ]
        result = _format_injection(matched)
        assert "### auth-module (87% match)" in result
        assert "This is the full answer" in result
        assert "## Pre-loaded Context" in result

    def test_medium_tier_uses_concise(self):
        """Medium tier (>=0.60) uses Concise section, not Answer."""
        matched = [
            MatchedFile(
                filename="auth-module_20260531.md",
                slug="auth-module",
                score=0.65,
                sections={
                    "Concise": "This is the concise answer.",
                    "Answer": "This is the long full answer section.",
                },
                first_sentence="This is the concise answer.",
            )
        ]
        result = _format_injection(matched)
        assert "This is the concise answer" in result
        assert "This is the long full answer" not in result

    def test_low_tier_first_sentence_only(self):
        """Low tier (>=0.40) uses only first sentence."""
        matched = [
            MatchedFile(
                filename="auth-module_20260531.md",
                slug="auth-module",
                score=0.45,
                sections={
                    "Concise": "This is the first sentence. This is the second sentence that should be cut.",
                },
                first_sentence="This is the first sentence.",
            )
        ]
        result = _format_injection(matched)
        assert "This is the first sentence" in result
        # Second sentence should not appear
        assert "This is the second sentence" not in result

    def test_file_index_appended(self, tmp_path):
        """File index table is appended to output with new 3-column format."""
        # Create context directory with files
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        # Create matching files
        auth_file = context_dir / "auth-module_20260531.md"
        auth_file.write_text("""## Concise
Concise content.
""")
        other_file = context_dir / "other-file_20260531.md"
        other_file.write_text("""## Concise
Other content.
""")

        matched = [
            MatchedFile(
                filename="auth-module_20260531.md",
                slug="auth-module",
                score=0.70,
                sections={"Concise": "Concise content."},
                first_sentence="Concise content.",
            ),
            MatchedFile(
                filename="other-file_20260531.md",
                slug="other-file",
                score=0.50,
                sections={"Concise": "Other content."},
                first_sentence="Other content.",
            ),
        ]
        result = _format_injection(matched, context_dir=context_dir)
        assert "## Available Context Files" in result
        assert "(2 files total, 2 matched)" in result
        assert "| File | Match | Summary |" in result
        assert "| auth-module_20260531.md |" in result
        assert "| other-file_20260531.md |" in result
        assert "70%" in result  # Match score for auth-module
        assert "50%" in result  # Match score for other-file

    def test_high_tier_file_limit_enforced(self):
        """When more than MAX_HIGH_TIER_FILES exist, output is capped by file count."""
        # Create 5 high-tier matches with short content
        matched = []
        for i in range(5):
            matched.append(
                MatchedFile(
                    filename=f"file{i}_20260531.md",
                    slug=f"file{i}",
                    score=0.90,  # High tier
                    sections={"Answer": "This is answer number " + str(i) + "."},
                    first_sentence="Answer sentence.",
                )
            )
        result = _format_injection(matched)
        # Should include header and at least one entry
        assert "## Pre-loaded Context" in result
        # But limited by MAX_HIGH_TIER_FILES = 3
        entries = result.count("### file")  # Count file entries
        assert entries <= MAX_HIGH_TIER_FILES

    def test_global_token_cap_enforced(self):
        """When total content exceeds INJECTION_TOKEN_CAP, output is truncated."""
        # Create files with large content (~2500+ tokens each)
        # "Word " * 4000 = ~20000 chars ~= 2500 tokens per file
        large_content = "Word " * 4000
        matched = []
        for i in range(4):
            matched.append(
                MatchedFile(
                    filename=f"large{i}_20260531.md",
                    slug=f"large{i}",
                    score=0.85,
                    sections={"Answer": large_content},
                    first_sentence="Large content file.",
                )
            )
        result = _format_injection(matched)
        # Total ~10000 tokens exceeds 2000 cap, output should be truncated
        assert "## Pre-loaded Context" in result
        # Not all 4 files should appear in full - content is too large
        # Check that result is smaller than sum of all inputs
        total_input = sum(len(m.sections["Answer"]) for m in matched)
        assert len(result) < total_input


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
        assert "## Pre-loaded Context" in result
        assert "auth-module" in result

    def test_returns_none_for_empty_dir(self, tmp_path):
        """Empty context directory returns None."""
        context_dir = tmp_path / "ensemble" / "context" / "empty-key"
        context_dir.mkdir(parents=True)

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("empty-key", "auth module")

        assert result is None

    def test_returns_none_for_no_matches(self, tmp_path):
        """Directory with no matching files returns None."""
        context_dir = tmp_path / "ensemble" / "context" / "no-match-key"
        context_dir.mkdir(parents=True)

        # Create unrelated file
        unrelated = context_dir / "unrelated_20260531.md"
        unrelated.write_text("## Answer\nUnrelated content.\n")

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("no-match-key", "zzzzz yyyyy")

        assert result is None

    def test_returns_none_on_error(self):
        """OSError during file operations returns None."""
        with patch("tempfile.gettempdir", side_effect=OSError("Permission denied")):
            result = get_shared_context("test-key", "auth module")

        assert result is None

    def test_returns_none_for_nonexistent_context_key(self, tmp_path):
        """Nonexistent context key returns None."""
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("nonexistent-key-12345", "auth module")

        assert result is None

    def test_context_key_none(self):
        """None context_key returns None gracefully."""
        result = get_shared_context(None, "auth module")
        assert result is None

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

        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("test-project", "auth module")

        assert result is not None
        assert "## Pre-loaded Context" in result
        assert "auth-module" in result
        assert "Available Context Files" in result
