"""Slack Block Kit utilities for formatting messages."""

from __future__ import annotations

from typing import Any


# Common language specifiers recognised at the start of a fenced code block.
# Kept as a module-level constant so the same set is used everywhere in this
# file and tests can introspect it if needed.
_COMMON_LANGS: frozenset[str] = frozenset(
    {
        'python', 'py', 'js', 'javascript', 'ts', 'typescript',
        'bash', 'sh', 'shell', 'zsh', 'json', 'yaml', 'yml',
        'xml', 'html', 'css', 'sql', 'go', 'rust', 'rs',
        'java', 'c', 'cpp', 'c++', 'c#', 'cs', 'ruby', 'rb',
        'php', 'swift', 'kotlin', 'kt', 'scala', 'perl', 'r',
        'lua', 'haskell', 'hs', 'markdown', 'md', 'text', 'txt',
        'diff', 'dockerfile', 'makefile', 'ini', 'toml',
    }
)


def _strip_language_specifier(first_line: str, rest_content: str) -> str:
    """Return the code body with a leading language specifier (if any) removed.

    Only the *first whitespace-delimited token* of ``first_line`` is matched
    against the known language set. This avoids stripping legitimate content
    that just happens to start with a language-like word (e.g. ``python # noqa``)
    or with a ``#`` comment / shebang.

    Args:
        first_line: The first line of the code block (no trailing newline).
        rest_content: Everything after that first line and its trailing newline.

    Returns:
        The code content with the language specifier (if any) removed.
    """
    stripped = first_line.strip()
    if not stripped:
        # First line is whitespace only: keep ``rest_content`` as-is.
        return rest_content

    # Only the first TOKEN of the first line is tested. This is the key fix
    # for ``python # noqa`` style inputs where the line is not purely a
    # language name.
    first_token = stripped.split()[0].lower()
    if first_token in _COMMON_LANGS:
        # Strip the first token plus any following whitespace from the
        # original first_line, then re-attach the rest.
        remaining_first_line = stripped[len(first_token):].lstrip()
        if remaining_first_line:
            return remaining_first_line + "\n" + rest_content
        return rest_content

    # Not a known language specifier: keep the original content untouched.
    return first_line + "\n" + rest_content if rest_content else first_line


def markdown_to_slack_blocks(text: str) -> list[dict[str, Any]]:
    """Convert Markdown text to Slack Block Kit sections.

    Args:
        text: Markdown-formatted text to convert.

    Returns:
        List of Slack Block Kit section dictionaries.
    """
    if not text or not text.strip():
        return []

    # Parse text into blocks (sections and code blocks)
    blocks = _parse_blocks(text)

    result_blocks: list[dict[str, Any]] = []

    for block in blocks:
        if block["type"] == "code":
            # Code block - wrap in section with code block formatting
            code_content = block["content"]
            result_blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{code_content}```"}
            })
        else:
            # Section - convert markdown formatting
            converted = _convert_markdown(block["content"])
            if converted.strip():
                result_blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": converted}
                })

    # Split any blocks that exceed 3000 characters
    return _split_large_blocks(result_blocks)


def _parse_blocks(text: str) -> list[dict[str, Any]]:
    """Parse text into sections and code blocks.

    Args:
        text: The text to parse.

    Returns:
        List of blocks with type ('section' or 'code') and content.
    """
    blocks: list[dict[str, Any]] = []
    current_section_lines: list[str] = []

    # Split on ``` delimiters to find code blocks
    parts = text.split("```")

    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Regular text section (between code blocks, or whole text if no code blocks)
            lines = part.split('\n')
            for line in lines:
                current_section_lines.append(line)
        else:
            # Code block content (odd indices after split)
            # Flush current section
            if current_section_lines:
                section_text = '\n'.join(current_section_lines)
                if section_text.strip():
                    blocks.append({"type": "section", "content": section_text})
                current_section_lines = []

            # Strip language specifier from first line ONLY if it is a
            # known language keyword (alphanumeric token in the common langs
            # set). Lines that happen to start with `#` (comments, shebangs)
            # are NOT language specifiers and must be preserved.
            code_content = part
            first_newline = code_content.find('\n')
            if first_newline != -1:
                # Has content after opening delimiter
                first_line = code_content[:first_newline]
                rest_content = code_content[first_newline + 1:]
                code_content = _strip_language_specifier(first_line, rest_content)
            else:
                # No newline inside the code block: a single-line code fence
                # such as ```` ```python print("hi")``` ````. The whole
                # payload is the first_line; rest is empty. The language-
                # specifier check still applies and must not crash.
                first_line = code_content
                code_content = _strip_language_specifier(first_line, "")

            # Remove trailing newline from closing delimiter if present
            if code_content.endswith('\n'):
                code_content = code_content[:-1]

            if code_content.strip():
                blocks.append({"type": "code", "content": code_content})

    # Flush remaining section
    if current_section_lines:
        section_text = '\n'.join(current_section_lines)
        if section_text.strip():
            blocks.append({"type": "section", "content": section_text})

    return blocks


def _convert_markdown(text: str) -> str:
    """Convert Markdown formatting to Slack-compatible format.

    Delegates mrkdwn conversion to the registered Slack formatter
    (``daemon.sources.formatters.slack.mrkdwn.SlackMrkdwnFormatter``).
    The import is lazy to avoid pulling the formatters package in at module
    import time, which keeps the Block Kit builder usable in isolation.
    """
    from daemon.sources.formatters.registry import get_or_passthrough

    formatter = get_or_passthrough("slack")
    return formatter.format(text)


def _split_large_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split blocks that exceed 3000 characters into smaller blocks."""
    result: list[dict[str, Any]] = []
    
    for block in blocks:
        text = block.get("text", {}).get("text", "")
        
        if len(text) <= 3000:
            result.append(block)
        else:
            # Split text into chunks of 3000 chars, trying to break at newlines
            chunks = _split_text_chunk(text, 3000)
            for chunk in chunks:
                result.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": chunk}
                })
    
    return result


def _split_text_chunk(text: str, max_length: int) -> list[str]:
    """Split text into chunks, trying to break at line boundaries.

    Tracks the ````` fence state while scanning for split points so a
    split never lands inside an open code fence (which would leave one
    chunk with an unbalanced opening ``````` and Slack would render the
    rest of the message as raw code).

    If the chunk is so large that no safe (outside-fence) newline exists
    in the scan window, the function force-splits at ``max_length`` and
    re-balances the fence delimiters across the two halves so each chunk
    has an even (balanced) count of fences.
    """
    chunks: list[str] = []

    while len(text) > max_length:
        # Find the last newline at or before max_length that lies OUTSIDE
        # an open code fence. We track how many fence delimiters (`````)
        # we have seen so far in the scan window: an even count means we
        # are outside a fence, an odd count means we are inside one.
        split_point = -1
        fence_count = 0
        scan_end = min(len(text), max_length + 1)
        i = 0
        while i < scan_end:
            if text[i:i + 3] == "```":
                fence_count += 1
                i += 3
                continue
            if text[i] == "\n" and fence_count % 2 == 0:
                split_point = i
            i += 1

        if split_point == -1:
            # No safe split point in the window (either no newlines or every
            # newline is inside a code fence). Force a split at max_length
            # to make progress. If we are mid-fence, re-balance the fences
            # across the two halves so each chunk has an even count of ```.
            if fence_count % 2 == 1:
                chunk = text[:max_length] + "\n```"
                remainder = "```\n" + text[max_length:]
            else:
                chunk = text[:max_length]
                remainder = text[max_length:]
            chunks.append(chunk)
            text = remainder.lstrip("\n")
            continue

        chunks.append(text[:split_point])
        text = text[split_point:].lstrip("\n")

    if text:
        chunks.append(text)

    return chunks
