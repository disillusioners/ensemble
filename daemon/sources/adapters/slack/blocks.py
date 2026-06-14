"""Slack Block Kit utilities for formatting messages."""

from __future__ import annotations

from typing import Any


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
                if first_line.strip():
                    common_langs = {
                        'python', 'py', 'js', 'javascript', 'ts', 'typescript',
                        'bash', 'sh', 'shell', 'zsh', 'json', 'yaml', 'yml',
                        'xml', 'html', 'css', 'sql', 'go', 'rust', 'rs',
                        'java', 'c', 'cpp', 'c++', 'c#', 'cs', 'ruby', 'rb',
                        'php', 'swift', 'kotlin', 'kt', 'scala', 'perl', 'r',
                        'lua', 'haskell', 'hs', 'markdown', 'md', 'text', 'txt',
                        'diff', 'dockerfile', 'makefile', 'ini', 'toml',
                    }
                    first_line_lower = first_line.strip().lower()
                    if first_line_lower in common_langs:
                        # It's a language specifier, skip it
                        code_content = rest_content
                    else:
                        # Keep the content as-is (actual code, comment, etc.)
                        code_content = part
                else:
                    # First line is whitespace only; treat the next line as
                    # the start of the actual content.
                    code_content = rest_content

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
    """Split text into chunks, trying to break at line boundaries."""
    chunks: list[str] = []
    
    while len(text) > max_length:
        # Find the last newline before max_length
        split_point = text.rfind('\n', 0, max_length)
        
        if split_point == -1:
            # No newline found, force split at max_length
            split_point = max_length
        
        chunks.append(text[:split_point])
        text = text[split_point:].lstrip('\n')
    
    if text:
        chunks.append(text)
    
    return chunks
