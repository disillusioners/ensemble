"""Context auto-injection for the Explorer agent.

Matches queries against shared context files and returns top-2 injection text.
"""

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Global injection token cap
INJECTION_TOKEN_CAP = 2000

# Minimum score threshold for a file to appear in the file index
MATCH_THRESHOLD = 0.10

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


def _increase_heading_levels(content: str) -> str:
    """Increase all markdown heading levels by 1 to avoid clashing with template headings."""
    return re.sub(r'^(#+)', r'#\1', content, flags=re.MULTILINE)


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
    This measures what fraction of the query tokens are found in the slug,
    which is appropriate for query-to-slug matching.

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

    # Recall-oriented: what fraction of query tokens match slug tokens
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
    slug = re.sub(pattern, "", filename)
    # Fallback: strip .md if still present (for non-timestamp filenames)
    if slug.endswith(".md"):
        slug = slug.removesuffix(".md")
    return slug


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
    logger.debug("[Explorer] _match_context_files: context_dir=%s", context_dir)

    if not context_dir.is_dir():
        logger.debug("[Explorer] _match_context_files: context_dir is not a directory")
        return []

    query_tokens = _tokenize_query(query)
    logger.debug("[Explorer] _match_context_files: query_tokens=%s", query_tokens)
    if not query_tokens:
        logger.debug("[Explorer] _match_context_files: no query tokens after filtering")
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

    logger.info("[Explorer] _match_context_files: found %d .md files", len(md_files))

    matched_files: list[MatchedFile] = []

    for file_path in md_files:
        try:
            # Extract slug and compute score
            slug = _extract_slug_from_filename(file_path.name)
            slug_tokens = _tokenize_slug(slug)
            if not slug_tokens:
                logger.debug("[Explorer] _match_context_files: file %s has no slug tokens", file_path.name)
                continue
            score = _match_score(query_tokens, slug_tokens)
            logger.debug("[Explorer] _match_context_files: file %s score=%.2f (threshold=%.2f)", file_path.name, score, MATCH_THRESHOLD)

            # Skip if below match threshold
            if score < MATCH_THRESHOLD:
                continue

            # Read content
            content = file_path.read_text(encoding="utf-8", errors="replace")

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
            logger.debug("[Explorer] _match_context_files: added %s with score %.2f", slug, score)
        except Exception as e:
            logger.debug(f"[Explorer] _match_context_files: Error processing file {file_path.name}: {e}")
            continue

    # Sort by score descending
    matched_files.sort(key=lambda m: m.score, reverse=True)
    logger.info("[Explorer] _match_context_files: returning %d matched files", len(matched_files))
    return matched_files


def _format_injection(
    matched_files: list[MatchedFile],
    context_dir: Path | None = None,
) -> str:
    """Format matched files into injection string.

    Pre-loads top 3 matches only:
    - Match 1 (highest score): ALWAYS included, full content (truncated to cap if alone exceeds it).
    - Match 2 (2nd highest): ONLY if score > 60%, full content truncated to remaining budget.
    - Match 3 (3rd highest): ONLY if score > 60%, full content truncated to remaining budget.

    Files 4+ are NOT pre-loaded but appear in the Available Context Files index.

    Args:
        matched_files: List of matched files to format (sorted by score desc).
        context_dir: Directory containing context files (for file index).

    Returns:
        Formatted injection string, or empty string if no entries and no file index.
    """
    logger.debug("[Explorer] _format_injection called with %d matched files", len(matched_files))

    remaining_budget = INJECTION_TOKEN_CAP
    entries: list[str] = []
    injected_slugs: set[str] = set()
    matched_by_slug: dict[str, MatchedFile] = {m.slug: m for m in matched_files}

    for i, matched in enumerate(matched_files[:3]):  # Only top 3
        if i == 0:
            # Match 1: ALWAYS include, full content (read from file)
            file_path = context_dir / matched.filename if context_dir else None
            if file_path is None or not file_path.exists():
                logger.debug("[Explorer] _format_injection: context_dir not available for match 1, skipping")
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                content = _increase_heading_levels(content)
            except Exception as e:
                logger.debug("[Explorer] _format_injection: error reading match 1: %s", e)
                continue
            if not content:
                continue
            # Safety: truncate to cap if alone exceeds it
            estimated_tokens = len(content) // 4
            if estimated_tokens > INJECTION_TOKEN_CAP:
                content = _truncate_to_tokens(content, INJECTION_TOKEN_CAP)
                estimated_tokens = INJECTION_TOKEN_CAP
            remaining_budget -= estimated_tokens
        elif i == 1:
            # Match 2: ONLY if score > 60%
            if matched.score <= 0.60:
                break
            file_path = context_dir / matched.filename if context_dir else None
            if file_path is None or not file_path.exists():
                logger.debug("[Explorer] _format_injection: context_dir not available for match 2, skipping")
                break
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                content = _increase_heading_levels(content)
            except Exception as e:
                logger.debug("[Explorer] _format_injection: error reading match 2: %s", e)
                break
            if not content:
                break
            estimated_tokens = len(content) // 4
            if estimated_tokens > remaining_budget:
                content = _truncate_to_tokens(content, remaining_budget)
                estimated_tokens = len(content) // 4
            if estimated_tokens > remaining_budget:
                break
            remaining_budget -= estimated_tokens
        elif i == 2:
            # Match 3: ONLY if score > 60%
            if matched.score <= 0.60:
                break
            file_path = context_dir / matched.filename if context_dir else None
            if file_path is None or not file_path.exists():
                logger.debug("[Explorer] _format_injection: context_dir not available for match 3, skipping")
                break
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                content = _increase_heading_levels(content)
            except Exception as e:
                logger.debug("[Explorer] _format_injection: error reading match 3: %s", e)
                break
            if not content:
                break
            estimated_tokens = len(content) // 4
            if estimated_tokens > remaining_budget:
                content = _truncate_to_tokens(content, remaining_budget)
                estimated_tokens = len(content) // 4
            if estimated_tokens > remaining_budget:
                break
            remaining_budget -= estimated_tokens
        else:
            break

        score_pct = int(matched.score * 100)
        entries.append(f"### {matched.slug} ({score_pct}% match)\n{content}\n")
        injected_slugs.add(matched.slug)

    # Build file index from files NOT already injected (up to 30)
    file_index_entries: list[tuple[float, str, str]] = []  # (score, filename, summary)
    preloaded_in_context_count = 0  # Count of pre-loaded files that are in context_dir
    if context_dir is not None and context_dir.is_dir():
        try:
            md_files = sorted(
                context_dir.glob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )[:50]  # Scan up to 50 files for index

            for file_path in md_files[:30]:  # Index cap at 30
                try:
                    # Extract slug to check if already injected and for lookups
                    slug = _extract_slug_from_filename(file_path.name)
                    # Skip files that were already injected into Pre-loaded Context
                    if slug in injected_slugs:
                        preloaded_in_context_count += 1
                        continue

                    # Get matched file if exists (lookup by slug)
                    matched = matched_by_slug.get(slug)
                    if matched is not None:
                        score = matched.score
                        summary = matched.first_sentence[:80] if matched.first_sentence else matched.slug
                    else:
                        # Score unmatched files
                        slug_tokens = _tokenize_slug(slug)
                        if not slug_tokens:
                            continue
                        # For unmatched files in the index, we default to 0%
                        # since we don't have access to query tokens here
                        score = 0.0

                        # Extract summary from file content
                        content = file_path.read_text(encoding="utf-8", errors="replace")
                        sections = _parse_sections(content)
                        if "Concise" in sections:
                            summary = _extract_first_sentence(sections["Concise"])[:80]
                        elif "Answer" in sections:
                            summary = _extract_first_sentence(sections["Answer"])[:80]
                        else:
                            summary = slug

                    file_index_entries.append((score, file_path.name, summary))
                except Exception:
                    continue
        except OSError:
            pass

        # Sort by score descending
        file_index_entries.sort(key=lambda x: x[0], reverse=True)

    if not entries and not file_index_entries:
        return ""

    # Build final output
    lines = ["# Shared Context\n"]
    lines.append(f"## Context dir: {context_dir}\n")
    lines.append("\n## Pre-loaded Context (auto-matched)\n")

    # Add entries
    lines.extend(entries)

    # Add separator before file index
    lines.append("\n---\n\n")

    # Add file index (does NOT count toward cap) - only if there are remaining files
    if file_index_entries:
        remaining_count = len(file_index_entries)
        lines.append("## Available Context Files ")
        # Format: "(N files)" or "(N files, M pre-loaded)"
        if preloaded_in_context_count == 0:
            lines.append(f"({remaining_count} files)\n")
        else:
            lines.append(f"({remaining_count} files, {preloaded_in_context_count} pre-loaded)\n")
        lines.append("> ⚠️ Match scores are heuristic-based and may not reflect true relevance. Do not fully trust them — verify with your own judgment.\n")
        lines.append("| File | Match | Summary |\n")
        lines.append("|------|-------|----------|\n")
        for score, filename, summary in file_index_entries:
            score_pct = int(score * 100)
            lines.append(f"| {filename} | {score_pct}% | {summary} |")

    return "".join(lines)


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
    logger.info("[Explorer] get_shared_context called: context_key=%s, query=%s", context_key, query[:100])

    # Define context_dir outside try block so it's available in exception handler
    # Wrap in try-except to handle OSError from tempfile.gettempdir()
    try:
        context_dir = Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key
    except Exception:
        # Use a placeholder path for error reporting
        context_dir = Path("/unknown") / "ensemble" / "context" / str(context_key)

    try:
        logger.debug("[Explorer] Context dir: %s", context_dir)
        logger.debug("[Explorer] Context dir exists: %s", context_dir.exists())
        if not context_dir.exists():
            logger.info("[Explorer] Context dir does not exist, returning empty format")
            return f"# Shared Context\n## Context dir: {context_dir}\n\n## Pre-loaded Context\nThere is no context yet."

        matched = _match_context_files(query, context_dir)
        logger.info("[Explorer] _match_context_files returned %d matches", len(matched))

        if not matched:
            logger.debug("Context auto-injection: no matches for query '%s'", query[:50])
            return f"# Shared Context\n## Context dir: {context_dir}\n\n## Pre-loaded Context\nThere is no context yet."

        injection = _format_injection(matched, context_dir=context_dir)
        logger.debug("[Explorer] _format_injection returned length: %d", len(injection) if injection else 0)

        if not injection:
            logger.debug("Context auto-injection: no injection content for query '%s' (matched %d files)", query[:50], len(matched))
            return f"# Shared Context\n## Context dir: {context_dir}\n\n## Pre-loaded Context\nThere is no context yet."

        logger.debug("Context auto-injection: %d files matched for query '%s'", len(matched), query[:50])
        logger.info("[Explorer] Returning injection of length %d", len(injection))
        return injection
    except Exception as e:
        logger.debug(f"[Explorer] Error in get_shared_context: {e}")
        return f"# Shared Context\n## Context dir: {context_dir}\n\n## Pre-loaded Context\nThere is no context yet."
