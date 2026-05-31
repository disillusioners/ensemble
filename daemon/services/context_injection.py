"""Context auto-injection for the Explorer agent.

Matches queries against shared context files and returns tiered injection text.
"""

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Tier thresholds
TIER_HIGH = 0.80
TIER_MEDIUM = 0.60
TIER_LOW = 0.40

# Token limits per tier (individual file limits)
TOKEN_LIMIT_HIGH = 800
TOKEN_LIMIT_MEDIUM = 200
TOKEN_LIMIT_LOW = 50

# Global injection token cap
INJECTION_TOKEN_CAP = 2000

# Max files to inject at high tier
MAX_HIGH_TIER_FILES = 3

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
    "neither", "each", "every", "all", "any", "few", "more", "most", "other",
    "some", "such", "no", "only", "own", "same", "than", "too", "very",
    "just", "because", "if", "when", "where", "how", "what", "which", "who",
})


@dataclass
class MatchedFile:
    """Represents a matched context file with its relevance score."""
    filename: str              # Full filename
    slug: str                  # Slug part only (before timestamp)
    score: float               # Match score [0.0, 1.0]
    sections: dict[str, str]   # Parsed sections from file content
    first_sentence: str        # First sentence of Concise or Answer section


def _tokenize_slug(slug: str) -> set[str]:
    """Split slug on `-`, filter tokens < 2 chars and stop words.

    Args:
        slug: The slug string to tokenize.

    Returns:
        Set of meaningful tokens (length >= 2, not stop words).
    """
    tokens = slug.split("-")
    return {
        t for t in tokens
        if len(t) >= 2 and t.lower() not in _STOP_WORDS
    }


def _tokenize_query(query: str) -> set[str]:
    """Lowercase, replace non-alphanumeric with spaces, split.

    Filter tokens < 2 chars and stop words.

    Args:
        query: The query string to tokenize.

    Returns:
        Set of meaningful tokens (length >= 2, not stop words).
    """
    # Replace non-alphanumeric with spaces, lowercase, split
    normalized = re.sub(r"[^a-zA-Z0-9]", " ", query.lower())
    tokens = normalized.split()
    return {
        t for t in tokens
        if len(t) >= 2 and t not in _STOP_WORDS
    }


def _match_score(query_tokens: set[str], slug_tokens: set[str]) -> float:
    """Compute match score between query and slug tokens.

    Recall-oriented asymmetric scoring: len(intersection) / len(query_tokens).
    Jaccard fallback: when both sets >= 3 tokens, use len(intersection) / len(union).

    Args:
        query_tokens: Tokenized query tokens.
        slug_tokens: Tokenized slug tokens.

    Returns:
        Match score between 0.0 and 1.0.
    """
    if not query_tokens or not slug_tokens:
        return 0.0

    intersection = query_tokens & slug_tokens
    if not intersection:
        return 0.0

    # Use Jaccard fallback when both sets have >= 3 tokens
    if len(query_tokens) >= 3 and len(slug_tokens) >= 3:
        union = query_tokens | slug_tokens
        return len(intersection) / len(union)

    # Default: recall-oriented asymmetric scoring
    return len(intersection) / len(query_tokens)


def _extract_slug_from_filename(filename: str) -> str:
    """Strip `_YYYYMMDD_HHMMSS.md` suffix pattern from filename.

    Args:
        filename: The filename to extract slug from.

    Returns:
        The slug portion of the filename.
    """
    # Pattern: _YYYYMMDD_HHMMSS.md
    pattern = r"_\d{8}_\d{6}\.md$"
    return re.sub(pattern, "", filename)


def _parse_sections(content: str) -> dict[str, str]:
    """Parse markdown into sections by `## Heading` markers.

    Args:
        content: Markdown content to parse.

    Returns:
        Dict mapping heading names to section content.
    """
    sections = {}
    current_heading = None
    current_content = []

    for line in content.split("\n"):
        heading_match = re.match(r"^##\s+(.+)$", line)
        if heading_match:
            # Save previous section
            if current_heading is not None:
                sections[current_heading] = "\n".join(current_content).strip()
            # Start new section
            current_heading = heading_match.group(1).strip()
            current_content = []
        elif current_heading is not None:
            current_content.append(line)

    # Don't forget the last section
    if current_heading is not None:
        sections[current_heading] = "\n".join(current_content).strip()

    return sections


def _extract_first_sentence(text: str) -> str:
    """Extract first sentence from text.

    Split on '.', '!', '?' followed by space or end of string.

    Args:
        text: Text to extract first sentence from.

    Returns:
        First sentence found, or empty string if none.
    """
    # Match sentence ending: . ! ? followed by space or end of string
    match = re.match(r"^[^.!?]*[.!?](?:\s|$)", text)
    if match:
        return match.group(0).strip()
    # No sentence ending found, return as much as possible
    return text.strip().split("\n")[0] if text.strip() else ""


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Rough token estimation with sentence-aware truncation.

    Try truncating at sentence boundary first (. ! ?).
    Fallback: hard cut at word boundary (last space before char limit).
    Append '...' if truncated.

    Args:
        text: Text to truncate.
        max_tokens: Maximum tokens allowed.

    Returns:
        Truncated text with '...' if cut.
    """
    char_limit = max_tokens * 4  # ~4 chars per token

    if len(text) <= char_limit:
        return text

    # Try sentence boundary first
    truncated = text[:char_limit]
    last_sentence_end = max(
        truncated.rfind(". "),
        truncated.rfind("! "),
        truncated.rfind("? "),
    )

    if last_sentence_end > char_limit // 2:
        return text[:last_sentence_end + 1].strip() + "..."

    # Fallback: word boundary
    last_space = truncated.rfind(" ")
    if last_space > char_limit // 2:
        return text[:last_space].strip() + "..."

    return truncated.strip() + "..."


def _match_context_files(query: str, context_dir: Path) -> list[MatchedFile]:
    """Find and score context files matching the query.

    Return [] if context_dir is not a directory or query has no tokens.
    Get all .md files sorted by mtime (most recent first), cap at 50.
    Per-file try/except: individual file errors are caught, file is skipped.

    Args:
        query: Query string to match against.
        context_dir: Directory containing context files.

    Returns:
        List of MatchedFile objects sorted by score descending.
    """
    if not context_dir.is_dir():
        return []

    query_tokens = _tokenize_query(query)
    if not query_tokens:
        return []

    # Get all .md files sorted by mtime (most recent first), cap at 50
    try:
        md_files = sorted(
            context_dir.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )[:50]
    except OSError as e:
        logger.debug(f"Failed to list context files: {e}")
        return []

    matched_files: list[MatchedFile] = []

    for file_path in md_files:
        try:
            # Extract slug and compute score
            slug = _extract_slug_from_filename(file_path.name)
            slug_tokens = _tokenize_slug(slug)
            score = _match_score(query_tokens, slug_tokens)

            # Skip if below low tier threshold
            if score < TIER_LOW:
                continue

            # Read content
            content = file_path.read_text(errors="replace")

            # Parse sections
            sections = _parse_sections(content)

            # Extract first sentence (backward compat: Concise -> Answer)
            first_sentence = ""
            if "Concise" in sections:
                first_sentence = _extract_first_sentence(sections["Concise"])
            elif "Answer" in sections:
                first_sentence = _extract_first_sentence(sections["Answer"])

            matched_files.append(MatchedFile(
                filename=file_path.name,
                slug=slug,
                score=score,
                sections=sections,
                first_sentence=first_sentence,
            ))
        except Exception as e:
            logger.debug(f"Error processing file {file_path.name}: {e}")
            continue

    # Sort by score descending
    matched_files.sort(key=lambda m: m.score, reverse=True)
    return matched_files


def _format_injection(matched_files: list[MatchedFile]) -> str:
    """Format matched files into injection string.

    HIGH (>=0.80): Answer section, truncated to TOKEN_LIMIT_HIGH, max MAX_HIGH_TIER_FILES files
    MEDIUM (>=0.60): Concise section, truncated to TOKEN_LIMIT_MEDIUM
    LOW (>=0.40): First sentence only, capped at TOKEN_LIMIT_LOW * 4 chars

    Global token cap tracked via estimated tokens (len(content) // 4).
    File index table (up to 30 files) does NOT count toward cap.

    Args:
        matched_files: List of matched files to format.

    Returns:
        Formatted injection string, or empty string if no matches.
    """
    if not matched_files:
        return ""

    # Track token budget
    remaining_budget = INJECTION_TOKEN_CAP
    entries: list[str] = []
    file_index_entries: list[str] = []

    # Count high tier files (limit to MAX_HIGH_TIER_FILES)
    high_tier_count = 0

    for matched in matched_files:
        # Stop if budget exhausted
        if remaining_budget <= 0:
            break

        # Determine tier and content
        content = ""
        if matched.score >= TIER_HIGH and high_tier_count < MAX_HIGH_TIER_FILES:
            high_tier_count += 1
            if "Answer" in matched.sections:
                content = _truncate_to_tokens(
                    matched.sections["Answer"],
                    TOKEN_LIMIT_HIGH
                )
            else:
                # Use entire content if no Answer section
                content = _truncate_to_tokens(
                    matched.sections.get("", ""),
                    TOKEN_LIMIT_HIGH
                )
            limit = TOKEN_LIMIT_HIGH
        elif matched.score >= TIER_MEDIUM:
            if "Concise" in matched.sections:
                content = _truncate_to_tokens(
                    matched.sections["Concise"],
                    TOKEN_LIMIT_MEDIUM
                )
            else:
                content = _truncate_to_tokens(
                    matched.sections.get("", ""),
                    TOKEN_LIMIT_MEDIUM
                )
            limit = TOKEN_LIMIT_MEDIUM
        elif matched.score >= TIER_LOW:
            # First sentence only, char limit only
            first = matched.first_sentence
            char_limit = TOKEN_LIMIT_LOW * 4
            if len(first) > char_limit:
                first = first[:char_limit].rsplit(" ", 1)[0] + "..."
            content = first
            limit = TOKEN_LIMIT_LOW
        else:
            continue

        if not content:
            continue

        # Estimate tokens and adjust if approaching budget limit
        estimated_tokens = len(content) // 4
        if estimated_tokens > remaining_budget:
            # Proportionally reduce
            if remaining_budget < limit:
                limit = max(10, remaining_budget)  # At least 10 tokens
            content = _truncate_to_tokens(content, limit)
            estimated_tokens = len(content) // 4

        if estimated_tokens > remaining_budget:
            continue  # Still over budget, skip this file

        # Add entry
        score_pct = int(matched.score * 100)
        entries.append(f"### {matched.slug} ({score_pct}% match)\n{content}\n")
        remaining_budget -= estimated_tokens

        # Add to file index (up to 30 files)
        if len(file_index_entries) < 30:
            summary = matched.first_sentence[:80]
            file_index_entries.append(f"| {matched.filename} | {summary} |")

    if not entries:
        return ""

    # Build final output
    lines = ["## Pre-loaded Context (auto-matched)\n"]

    # Add entries
    lines.extend(entries)

    # Add file index (does NOT count toward cap)
    if file_index_entries:
        lines.append("\n### File Index\n")
        lines.append("| File | Summary |\n")
        lines.append("|------|----------|\n")
        lines.extend(file_index_entries)

    return "\n".join(lines)


def get_shared_context(context_key: str, query: str) -> str | None:
    """Get shared context for a given context key and query.

    Resolves context dir: {tempdir}/ensemble/context/{context_key}
    Matches files against query and returns tiered injection text.

    Args:
        context_key: The context key identifying the context directory.
        query: Query string to match against context files.

    Returns:
        Injection string on success, None on failure or no matches.
    """
    try:
        context_dir = Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key

        matched = _match_context_files(query, context_dir)
        if not matched:
            return None

        injection = _format_injection(matched)
        if not injection:
            return None

        query_snippet = query[:50] + "..." if len(query) > 50 else query
        logger.debug(f"Context injection: {len(matched)} matches for query '{query_snippet}'")

        return injection
    except Exception as e:
        logger.debug(f"Error in get_shared_context: {e}")
        return None
