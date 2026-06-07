"""Context auto-injection for the Explorer agent.

Matches queries against shared context files and returns top-2 injection text.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .context_tools import resolve_context_dir

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
    """Increase all markdown heading levels by 2 to avoid clashing with template headings.

    The injection template uses ``#`` for ``Shared Context`` / ``Pre-loaded
    Context (auto-matched)`` and ``##`` for each pre-loaded entry, so the
    file's own headings must be pushed deeper (2 levels) to keep a clean
    visual hierarchy.
    """
    return re.sub(r'^(#+)', r'##\1', content, flags=re.MULTILINE)


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


def _build_matched_file(file_path: Path, score: float) -> MatchedFile | None:
    """Build a :class:`MatchedFile` from a file path.

    Reads the content, parses the ``## `` sections, and extracts the
    first sentence from the ``## Concise`` or ``## Answer`` section. Returns
    ``None`` on any failure so the caller can silently skip the file.
    """
    try:
        slug = _extract_slug_from_filename(file_path.name)
        content = file_path.read_text(encoding="utf-8", errors="replace")
        sections = _parse_sections(content)
        first_sentence = ""
        if "Concise" in sections:
            first_sentence = _extract_first_sentence(sections["Concise"])
        elif "Answer" in sections:
            first_sentence = _extract_first_sentence(sections["Answer"])
        return MatchedFile(
            filename=file_path.name,
            slug=slug,
            score=score,
            sections=sections,
            first_sentence=first_sentence,
        )
    except Exception as e:
        logger.debug(f"[Explorer] _build_matched_file: Error for {file_path.name}: {e}")
        return None


def _match_context_files(query: str, context_dir: Path) -> list[MatchedFile]:
    """Find and score context files matching the query.

    Return [] if context_dir is not a directory or the dir has no .md files.
    Per-file try/except: individual file errors are caught, file is skipped.

    Rule: as long as the directory has at least one readable .md file, the
    file with the highest score is always returned — even if the score is
    ``0%``. This honors the pre-loaded context invariant that "the highest
    file match is always included" (which ``_format_injection`` enforces
    for Match 1), so the pre-loaded block is never empty when the dir
    has files. When multiple files pass the threshold, all of them are
    returned (caller picks the top 3).

    Args:
        query: Query string to match against.
        context_dir: Directory containing context files.

    Returns:
        List of MatchedFile objects sorted by score descending. Contains at
        least one entry when the dir has any readable ``.md`` files; empty
        only when the dir is missing or empty.
    """
    logger.debug("[Explorer] _match_context_files: context_dir=%s", context_dir)

    if not context_dir.is_dir():
        logger.debug("[Explorer] _match_context_files: context_dir is not a directory")
        return []

    query_tokens = _tokenize_query(query)
    logger.debug("[Explorer] _match_context_files: query_tokens=%s", query_tokens)

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

    if not md_files:
        return []

    # Score every file. Track all candidates so we can fall back to the
    # highest-scoring one when nothing passes the threshold — this keeps
    # the pre-loaded block non-empty whenever the dir has files.
    scored: list[tuple[float, Path]] = []
    above_threshold: list[MatchedFile] = []
    best_below: tuple[float, Path] | None = None

    for file_path in md_files:
        try:
            slug = _extract_slug_from_filename(file_path.name)
            slug_tokens = _tokenize_slug(slug)
            if not slug_tokens:
                logger.debug("[Explorer] _match_context_files: file %s has no slug tokens", file_path.name)
                continue

            if query_tokens:
                score = _match_score(query_tokens, slug_tokens)
            else:
                # No usable query tokens — score is 0 but we still want
                # the most recent file pre-loaded.
                score = 0.0
            scored.append((score, file_path))
            logger.debug(
                "[Explorer] _match_context_files: file %s score=%.2f (threshold=%.2f)",
                file_path.name, score, MATCH_THRESHOLD,
            )
        except Exception as e:
            logger.debug(f"[Explorer] _match_context_files: Error scoring {file_path.name}: {e}")
            continue

    if not scored:
        return []

    # Build MatchedFile objects for everything above the threshold.
    for score, file_path in scored:
        if score < MATCH_THRESHOLD:
            continue
        matched = _build_matched_file(file_path, score)
        if matched is not None:
            above_threshold.append(matched)
            logger.debug(
                "[Explorer] _match_context_files: added %s with score %.2f",
                matched.slug, score,
            )

    if above_threshold:
        above_threshold.sort(key=lambda m: m.score, reverse=True)
        logger.info(
            "[Explorer] _match_context_files: returning %d matched files",
            len(above_threshold),
        )
        return above_threshold

    # No file passed the threshold — fall back to the highest-scoring
    # file (ties broken by mtime, which the list is already sorted by).
    best_score, best_path = max(scored, key=lambda sp: sp[0])
    logger.info(
        "[Explorer] _match_context_files: no file above threshold; "
        "falling back to highest-scoring file %s (score=%.2f)",
        best_path.name, best_score,
    )
    fallback = _build_matched_file(best_path, best_score)
    return [fallback] if fallback is not None else []


def _need_more_hint(audience: str) -> str:
    """Return the ``Need more?`` line, or ``""`` if there is nothing to suggest.

    Internal agents (LangChain) call ``list_context`` / ``read_context``.
    External agent systems reach the same data through the hosted MCP
    server using ``ensemble_context_list`` / ``ensemble_context_read``.

    The returned string is a single bullet line with no leading or trailing
    newline so callers can compose multiple hints under one heading.
    """
    if audience == "external":
        return (
            "- Need more? Call `ensemble_context_list(context_key)` to "
            "enumerate, `ensemble_context_read(context_key, filename)` to read."
        )
    return (
        "- Need more? Call `list_context(context_key)` to enumerate, "
        "`read_context(context_key, filename)` to read."
    )


# Cap the number of critical notes surfaced into the MCP RAG hint so the
# hint block itself stays small even if a project has many notes.
_MCP_RAG_CRITICAL_NOTES_CAP = 5
_MCP_RAG_CRITICAL_NOTE_SUMMARY_CAP = 100

_CRITICAL_NOTE_PRIORITY_ICON = {
    "critical": "🔴",
    "high": "🟡",
    "medium": "🟢",
}


def _format_critical_note(note: dict) -> str | None:
    """Format a single critical-note dict for the MCP RAG hint.

    Returns ``None`` when the dict is missing the required ``summary`` field
    so the caller can skip it silently. Mirrors the visual style used in
    :func:`daemon.manager.format_project_context` (priority icon + bracketed
    category + summary, with an optional reference suffix) so an external
    agent sees the same shape in the hint as it would in a full project
    context block.
    """
    summary = note.get("summary")
    if not summary or not isinstance(summary, str):
        return None
    summary = summary.strip()
    if not summary:
        return None
    if len(summary) > _MCP_RAG_CRITICAL_NOTE_SUMMARY_CAP:
        summary = summary[: _MCP_RAG_CRITICAL_NOTE_SUMMARY_CAP - 1].rstrip() + "…"
    priority = str(note.get("priority", "")).lower()
    icon = _CRITICAL_NOTE_PRIORITY_ICON.get(priority, "⚪")
    category = str(note.get("category", "")).strip()
    category_str = f"**[{category}]** " if category else ""
    reference = note.get("reference")
    ref_str = f" *(ref: {reference})*" if reference else ""
    return f"- {icon} {category_str}{summary}{ref_str}"


def _mcp_rag_hint(
    audience: str,
    project_id: str | None = None,
    project_name: str | None = None,
    critical_notes: list[dict] | None = None,
) -> str:
    """Return the MCP RAG-tools hint for external audiences, or ``""`` otherwise.

    External agent systems that reach us via the hosted MCP server get a
    one-line summary of the RAG-shaped tools we expose (``ensemble_kb_explore``
    / ``ensemble_kb_experience``) so they can call them directly instead of
    going through a slower round-trip. Project discovery was intentionally
    dropped — callers are expected to know which project they belong to.

    When ``project_id`` / ``project_name`` are provided, a follow-up bullet
    surfaces them as scoping hints so the external agent can pass them to
    the MCP RAG tools to scope results to the current project. If
    ``critical_notes`` is provided, the top :data:`_MCP_RAG_CRITICAL_NOTES_CAP`
    notes are appended as sub-bullets so the agent also sees the project's
    pinned warnings / conventions before calling the tools.

    Returns ``""`` for internal audiences — the LangChain tool names are
    already in their system prompt.
    """
    if audience != "external":
        return ""
    body = (
        "- You are working under master agent system named Ensemble.\n"
        "- MCP RAG tools (provided by Ensemble,should use them if available): "
        "`ensemble_kb_explore(query)` to search the knowledge base, "
        "`ensemble_kb_experience(text)` to record new knowledge."
    )
    project_bits: list[str] = []
    if project_id:
        project_bits.append(f"project_id=\"{project_id}\"")
    if project_name:
        project_bits.append(f"project_name=\"{project_name}\"")
    if project_bits or critical_notes:
        body += (
            f"\n- Current project context: {', '.join(project_bits) or '(no project id)'} "
            "(pass these to MCP RAG tools to scope results to this project)."
        )
        if critical_notes:
            rendered = [
                line for note in critical_notes
                if isinstance(note, dict)
                for line in [_format_critical_note(note)]
                if line is not None
            ]
            if rendered:
                total = len(rendered)
                shown = rendered[:_MCP_RAG_CRITICAL_NOTES_CAP]
                more = total - len(shown)
                body += "\n  - ⚡ Critical notes" + (
                    f" (showing {len(shown)} of {total}):" if more else ":"
                )
                body += "\n    " + "\n    ".join(shown)
                if more > 0:
                    body += f"\n    - …and {more} more"
    return body


def _context_guidelines(
    audience: str,
    project_id: str | None = None,
    project_name: str | None = None,
    critical_notes: list[dict] | None = None,
) -> str:
    """Assemble the ``## Context Guidelines:`` section, or ``""`` if empty.

    Combines :func:`_need_more_hint` and :func:`_mcp_rag_hint` under a single
    heading. Each hint is an independent unit that returns ``""`` when not
    applicable, so this composer is the only place that decides whether the
    guidelines block as a whole should appear — callers can safely include
    the return value verbatim and trust it to disappear when there is
    nothing to say (e.g. the empty / no-context state).

    ``project_id`` / ``project_name`` / ``critical_notes`` are forwarded to
    :func:`_mcp_rag_hint` so the external hint can show which project to
    scope RAG calls to (and which pinned warnings apply).
    """
    hints = [
        h for h in (
            _need_more_hint(audience),
            _mcp_rag_hint(audience, project_id, project_name, critical_notes),
        ) if h
    ]
    if not hints:
        return ""
    return "## Context Guidelines:\n" + "\n".join(hints) + "\n"


def _format_injection(
    matched_files: list[MatchedFile],
    context_key: str,
    context_dir: Path | None = None,
    audience: str = "internal",
    project_id: str | None = None,
    project_name: str | None = None,
    critical_notes: list[dict] | None = None,
) -> str:
    """Format matched files into injection string.

    Pre-loads top 3 matches only:
    - Match 1 (highest score): ALWAYS included, full content (truncated to cap if alone exceeds it).
    - Match 2 (2nd highest): ONLY if score > 60%, full content truncated to remaining budget.
    - Match 3 (3rd highest): ONLY if score > 60%, full content truncated to remaining budget.

    Files 4+ are NOT pre-loaded but appear in the Available Context Files index.

    Args:
        matched_files: List of matched files to format (sorted by score desc).
        context_key: The context key (tree-root instance id) to display.
        context_dir: Directory containing context files (for file index).
        audience: ``"internal"`` (default) shows the LangChain tool names;
            ``"external"`` shows the hosted MCP tool names.
        project_id: Optional project UUID — surfaced in the external MCP RAG
            hint so the agent can scope tool calls.
        project_name: Optional human-readable project name — surfaced in the
            external MCP RAG hint alongside ``project_id``.
        critical_notes: Optional list of project critical-note dicts (each
            with ``priority`` / ``category`` / ``summary`` / optional
            ``reference``). Forwarded to the external MCP RAG hint so the
            top few pinned warnings are visible alongside the project id.

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
        entries.append(f"## {matched.slug}.md ({score_pct}% match)\n{content}\n")
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
    lines.append(f"context_key: {context_key}\n")
    lines.append("\n# Pre-loaded Context (auto-matched)\n")

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

    # Append the "Context Guidelines" hint at the very end so the LLM sees
    # the pre-loaded content first and only then the pointer to fetch more.
    # _context_guidelines() returns "" when there is nothing to say.
    lines.append("\n")
    guidelines = _context_guidelines(audience, project_id, project_name, critical_notes)
    if guidelines:
        lines.append(guidelines)

    return "".join(lines)


def get_shared_context(
    context_key: str,
    query: str,
    audience: str = "internal",
    *,
    project_id: str | None = None,
    project_name: str | None = None,
    critical_notes: list[dict] | None = None,
) -> str | None:
    """Get shared context for a given context key and query.

    Resolves context dir: {tempdir}/ensemble/context/{context_key}
    Matches files against query and returns tiered injection text.

    Args:
        context_key: The context key identifying the context directory.
        query: Query string to match against context files.
        audience: ``"internal"`` (default) shows the LangChain tool names in
            the "Need more?" hint. Pass ``"external"`` to show the hosted MCP
            tool names — used when the injection is being prepended to a
            message that an external agent system (e.g. opencode via MCP)
            will see.
        project_id: Optional project UUID forwarded to the external MCP RAG
            hint so the agent can scope ``ensemble_kb_*`` tool calls. Ignored
            for the internal audience.
        project_name: Optional human-readable project name forwarded to the
            external MCP RAG hint alongside ``project_id``. Ignored for the
            internal audience.
        critical_notes: Optional list of project critical-note dicts
            (``priority`` / ``category`` / ``summary`` / optional
            ``reference``) forwarded to the external MCP RAG hint so the
            top few pinned warnings are surfaced. Ignored for the internal
            audience.

    Returns:
        Injection string on success, None on failure or no matches.
    """
    logger.info(
        "[Explorer] get_shared_context called: context_key=%s, query=%s, audience=%s, project_id=%s, critical_notes=%d",
        context_key, query[:100], audience, project_id, len(critical_notes) if critical_notes else 0,
    )

    # Resolve context_dir once and reuse across all branches. The resolved path
    # is not leaked into the agent-visible output — only ``context_key`` is.
    context_dir = resolve_context_dir(context_key)

    def _empty() -> str:
        """Build the "no context" payload.

        Internal callers see the bare "There is no context yet." line — the
        LangChain tool names are already in their system prompt, so adding
        the guidelines block here would be noise.

        External callers ALWAYS get the ``## Context Guidelines:`` block too:
        even when the dir is empty, the project id / name / critical notes
        and the MCP RAG tool names are needed for the remote session to
        know how to talk back to us, so hiding them behind an empty context
        dir would leave the agent with no usable path forward.
        """
        body = (
            f"# Shared Context\ncontext_key: {context_key}\n\n"
            "# Pre-loaded Context (auto-matched)\nThere is no context yet.\n"
        )
        if audience == "external":
            guidelines = _context_guidelines(
                audience, project_id, project_name, critical_notes,
            )
            if guidelines:
                body += "\n" + guidelines
        return body

    try:
        logger.debug("[Explorer] Context dir: %s", context_dir)
        logger.debug("[Explorer] Context dir exists: %s", context_dir.exists())
        if not context_dir.exists():
            logger.info("[Explorer] Context dir does not exist, returning empty format")
            return _empty()

        matched = _match_context_files(query, context_dir)
        logger.info("[Explorer] _match_context_files returned %d matches", len(matched))

        # Always call _format_injection — even with zero matches — because
        # the "Available Context Files" index is built from the directory
        # contents, not from the matched set. Without this, a query that
        # doesn't match the file slug would hide the fact that files exist
        # in the dir, leaving the agent with no hint that it can fetch more.
        # _format_injection returns "" only when the dir is truly empty.
        injection = _format_injection(
            matched,
            context_key=context_key,
            context_dir=context_dir,
            audience=audience,
            project_id=project_id,
            project_name=project_name,
            critical_notes=critical_notes,
        )
        logger.debug("[Explorer] _format_injection returned length: %d", len(injection) if injection else 0)

        if not injection:
            if matched:
                logger.debug(
                    "Context auto-injection: no injection content for query '%s' (matched %d files)",
                    query[:50], len(matched),
                )
            else:
                logger.debug(
                    "Context auto-injection: no matches and no file index for query '%s'",
                    query[:50],
                )
            return _empty()

        logger.debug("Context auto-injection: %d files matched for query '%s'", len(matched), query[:50])
        logger.info("[Explorer] Returning injection of length %d", len(injection))
        return injection
    except Exception as e:
        logger.debug(f"[Explorer] Error in get_shared_context: {e}")
        return _empty()
