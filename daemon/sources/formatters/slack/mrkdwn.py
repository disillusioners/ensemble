"""Slack mrkdwn formatter for converting standard Markdown to Slack format.

This module implements the conversion pipeline described in the spec. Code
blocks (both fenced and inline) are extracted and protected with placeholders
before any other transformation, then restored at the end.

Two distinct placeholder mechanisms are used to avoid collisions:

* Bold/heading placeholders use ``⟦...⟧`` (matching the convention in
  ``daemon/sources/adapters/slack/blocks.py``) and are later rewritten to
  ``*text*``.
* Code-block placeholders use Unicode noncharacters (``\\uFDD0``/``\\uFDD1``)
  and are restored verbatim at the end. These sentinels are guaranteed never
  to occur in real text and cannot collide with the bold placeholders.
"""

from __future__ import annotations

import re

from daemon.sources.formatters.base import OutputFormatter

# Bold/heading placeholders — match the convention in
# daemon/sources/adapters/slack/blocks.py and are later rewritten to "*text*".
_BOLD_PLACEHOLDER_OPEN = "\u27e6"  # ⟦
_BOLD_PLACEHOLDER_CLOSE = "\u27e7"  # ⟧

# Code-block placeholders — Unicode noncharacters (never appear in real text)
# so they cannot collide with markdown syntax or with bold placeholders.
_CODE_PLACEHOLDER_OPEN = "\uFDD0"
_CODE_PLACEHOLDER_CLOSE = "\uFDD1"


class SlackMrkdwnFormatter(OutputFormatter):
    """Converts standard Markdown text to Slack mrkdwn format.

    The conversion pipeline (applied in this exact order):
        1. Extract & protect fenced code blocks (```...```)
        2. Extract & protect inline code (`...`)
        3. Convert ``bold`` to placeholders
        4. Convert ``bold`` to placeholders (word-boundary aware)
        5. Convert ``italic`` to ``_italic_`` (safe - bold is in placeholders)
        6. Replace bold/heading placeholders with ``*text*``
        7. Convert ``strike`` to ``~strike~``
        8. Convert ``[text](url)`` to ``<url|text>`` (parens in URL supported)
        9. Convert headings (````, ````, ````+) to ``*text*`` (already-formatted
           text is left as-is)
       10. Convert list markers (-, ``*``, 1.) to bullets (•)
       11. Convert markdown tables to ASCII tables in code fences
       12. Restore protected code blocks
    """

    def format(self, text: str) -> str:
        """Transform Markdown text to Slack mrkdwn format.

        Args:
            text: Standard Markdown text from LLM.

        Returns:
            Text converted to Slack mrkdwn.
        """
        if not text:
            return text

        # Storage for protected code blocks, indexed by placeholder id.
        protected: list[str] = []

        def _save(match: re.Match[str]) -> str:
            """Store matched code block and return a sentinel placeholder."""
            protected.append(match.group(0))
            idx = len(protected) - 1
            return f"{_CODE_PLACEHOLDER_OPEN}{idx}{_CODE_PLACEHOLDER_CLOSE}"

        # Step 1: Protect fenced code blocks (```...```)
        text = re.sub(r"```[\s\S]*?```", _save, text)

        # Step 2: Protect inline code (`...`)
        text = re.sub(r"`[^`\n]+`", _save, text)

        # Step 3: Convert **bold** to placeholders.
        text = re.sub(
            r"\*\*(.+?)\*\*",
            rf"{_BOLD_PLACEHOLDER_OPEN}\1{_BOLD_PLACEHOLDER_CLOSE}",
            text,
        )

        # Step 4: Convert __bold__ to placeholders (with word boundaries to avoid
        # false positives on dunders like __init__).
        text = re.sub(
            r"(?<![A-Za-z0-9])__(.+?)__(?![A-Za-z0-9])",
            rf"{_BOLD_PLACEHOLDER_OPEN}\1{_BOLD_PLACEHOLDER_CLOSE}",
            text,
        )

        # Step 5: Convert *italic* to _italic_.
        # At this point, all bold/heading markers are inside placeholders,
        # so single * pairs are safe to convert. We still use negative
        # lookbehind/lookahead to be defensive against any leftover single *.
        text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"_\1_", text)

        # Step 6: Restore bold/heading placeholders to *text* (Slack bold).
        text = text.replace(_BOLD_PLACEHOLDER_OPEN, "*").replace(
            _BOLD_PLACEHOLDER_CLOSE, "*"
        )

        # Step 7: Convert ~~strikethrough~~ to ~strikethrough~.
        text = re.sub(r"~~(.+?)~~", r"~\1~", text)

        # Step 8: Convert [text](url) to <url|text>, allowing one level of
        # nested parentheses in URLs (e.g. Wikipedia links).
        text = re.sub(
            r"\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)",
            r"<\2|\1>",
            text,
        )

        # Step 9: Convert headings to Slack bold, running after all inline
        # conversions are complete so heading content is already formatted.
        # If the heading text is already wrapped in *...* (e.g. from inline
        # bold), leave it unchanged; otherwise wrap it in *...*.
        def _heading_repl(match: re.Match[str]) -> str:
            content = match.group(1).strip()
            if content.startswith("*") and content.endswith("*") and len(content) >= 2:
                return content
            return f"*{content}*"

        text = re.sub(r"^#{1,6}[ \t]+(\S(?:.*\S)?)[ \t]*$", _heading_repl, text, flags=re.MULTILINE)

        # Step 10: Convert list markers to bullets.
        # `- ` or `* ` at the start of a line -> `• `.
        text = re.sub(r"^[\-\*][ \t]+", "\u2022 ", text, flags=re.MULTILINE)
        # Numbered lists `1. `, `2. `, etc. -> `• `.
        text = re.sub(r"^\d+\.[ \t]+", "\u2022 ", text, flags=re.MULTILINE)

        # Step 11: Convert markdown tables to ASCII tables in code fences.
        text = self._convert_tables(text)

        # Step 12: Restore protected code blocks.
        def _restore(match: re.Match[str]) -> str:
            idx = int(match.group(1))
            return protected[idx]

        text = re.sub(
            rf"{re.escape(_CODE_PLACEHOLDER_OPEN)}(\d+){re.escape(_CODE_PLACEHOLDER_CLOSE)}",
            _restore,
            text,
        )

        return text

    # ------------------------------------------------------------------ tables

    def _convert_tables(self, text: str) -> str:
        """Convert markdown tables to ASCII tables wrapped in code fences.

        A markdown table consists of a header row, a separator row, and zero or
        more data rows. The separator row is replaced by a dashed line of the
        same column width.

        Args:
            text: Text potentially containing markdown tables.

        Returns:
            Text with tables converted to ASCII art in code fences.
        """
        lines = text.split("\n")
        result: list[str] = []
        i = 0

        while i < len(lines):
            # A table requires a header row + separator row immediately after.
            if (
                self._is_table_row(lines[i])
                and i + 1 < len(lines)
                and self._is_separator_row(lines[i + 1])
            ):
                header_line = lines[i]
                data_lines: list[str] = []
                j = i + 2
                while j < len(lines) and self._is_table_row(lines[j]):
                    data_lines.append(lines[j])
                    j += 1
                result.append(self._build_ascii_table(header_line, data_lines))
                i = j
            else:
                result.append(lines[i])
                i += 1

        return "\n".join(result)

    def _is_table_row(self, line: str) -> bool:
        """Check if a line looks like a markdown table row.

        Args:
            line: Line of text to test.

        Returns:
            True if the line has at least two pipes (start and end).
        """
        s = line.strip()
        return s.startswith("|") and s.endswith("|") and s.count("|") >= 2

    def _is_separator_row(self, line: str) -> bool:
        """Check if a line is a markdown table separator.

        Separator cells are composed of one or more ``-`` characters, with
        optional leading/trailing ``:`` for alignment markers.

        Args:
            line: Line of text to test.

        Returns:
            True if the line is a valid table separator.
        """
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            return False
        inner = s[1:-1]
        cells = [c.strip() for c in inner.split("|")]
        if not cells:
            return False
        for cell in cells:
            if not re.match(r"^:?-+:?$", cell):
                return False
        return True

    def _parse_table_row(self, line: str) -> list[str]:
        """Parse a markdown table row into its cell strings.

        Args:
            line: A markdown table row.

        Returns:
            List of cell contents (stripped of surrounding whitespace).
        """
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    def _build_ascii_table(self, header_line: str, data_lines: list[str]) -> str:
        """Build an ASCII table from parsed markdown table parts.

        Columns are separated by three spaces, and each column is padded with
        spaces to match the widest cell. The separator row uses ``-``
        characters matching each column's width.

        Args:
            header_line: Header row of the markdown table.
            data_lines: Data rows of the markdown table (separator excluded).

        Returns:
            ASCII art table wrapped in triple backticks.
        """
        headers = self._parse_table_row(header_line)
        rows = [self._parse_table_row(line) for line in data_lines]

        ncols = len(headers)
        widths = [len(h) for h in headers]
        for row in rows:
            for k, cell in enumerate(row):
                if k < ncols:
                    widths[k] = max(widths[k], len(cell))

        def _format_row(cells: list[str]) -> str:
            parts: list[str] = []
            for k in range(ncols):
                cell = cells[k] if k < len(cells) else ""
                parts.append(cell.ljust(widths[k]))
            return "   ".join(parts)

        header_text = _format_row(headers)
        sep_text = "   ".join("-" * widths[k] for k in range(ncols))
        data_texts = [_format_row(row) for row in rows]

        body = "\n".join([header_text, sep_text, *data_texts])
        return f"```\n{body}\n```"
