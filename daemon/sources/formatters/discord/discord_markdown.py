"""Discord markdown formatter for converting standard Markdown to Discord format.

This module implements the conversion pipeline described in the spec. Code
blocks (both fenced and inline) are extracted and protected with placeholders
before any other transformation, then restored at the end. This prevents
tables and headings inside code blocks from being converted.

Discord natively renders a substantial subset of standard Markdown — bold
(``**text**``), italic (``*text*`` / ``_text_``), inline code, fenced code
blocks, strikethrough (``~~text~~``), ordered/unordered lists, links
(``[text](url)``), and blockquotes (``> ...``) — so this formatter only
converts the elements Discord does NOT handle: ATX headers and markdown
tables.

Code-block placeholders use Unicode noncharacters (``\\uFDD0``/``\\uFDD1``)
which are guaranteed never to occur in real text and cannot collide with
any markdown syntax Discord interprets natively.
"""

from __future__ import annotations

import re

from daemon.sources.formatters.base import OutputFormatter

# Code-block placeholders — Unicode noncharacters (never appear in real
# text) so they cannot collide with markdown syntax. Later restored verbatim.
_CODE_PLACEHOLDER_OPEN = "\ufdd0"
_CODE_PLACEHOLDER_CLOSE = "\ufdd1"

# ATX header regex: matches lines that START with 1-6 '#' characters
# followed by a space or tab, capture the content (must start and end with
# a non-whitespace character), and end at line boundary.
_HEADER_RE = re.compile(
    r"^#{1,6}[ \t]+(\S(?:.*\S)?)[ \t]*$",
    flags=re.MULTILINE,
)


class DiscordFormatter(OutputFormatter):
    """Converts standard Markdown text to Discord-flavored markdown.

    The conversion pipeline (applied in this exact order):

        1. Extract & protect fenced code blocks (```...```)
        2. Extract & protect inline code (`...`)
        3. Convert ATX headers (``#``..``######``) to ``**text**`` (Discord
           bold) — only if the content is not already wrapped in ``**...**``
        4. Convert markdown tables to ASCII art wrapped in triple backticks
           (Discord renders code blocks as monospace, which keeps the
           column alignment intact)
        5. Restore protected code blocks

    All other standard Markdown is passed through unchanged: ``**bold**``,
    ``*italic*`` / ``_italic_``, ``~~strike~~``, ``[text](url)``,
    ``> blockquote``, and lists all render natively in Discord.
    """

    def format(self, text: str) -> str:
        """Transform Markdown text to Discord format.

        Args:
            text: Standard Markdown text from LLM.

        Returns:
            Text with tables and ATX headers converted; everything else
            passed through unchanged for Discord to render natively.
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

        # Step 1: Protect fenced code blocks (```...```).
        text = re.sub(r"```[\s\S]*?```", _save, text)

        # Step 2: Protect inline code (`...`).
        text = re.sub(r"`[^`\n]+`", _save, text)

        # Step 3: Convert ATX headers to Discord bold (**text**).
        # If the heading text is already wrapped in **...** (e.g. the LLM
        # wrote "## **Bold Heading**"), leave it unchanged to avoid
        # double-wrapping.
        def _heading_repl(match: re.Match[str]) -> str:
            content = match.group(1).strip()
            if (
                content.startswith("**")
                and content.endswith("**")
                and len(content) >= 4
            ):
                return content
            return f"**{content}**"

        text = _HEADER_RE.sub(_heading_repl, text)

        # Step 4: Convert markdown tables to ASCII art in code fences.
        text = self._convert_tables(text)

        # Step 5: Restore protected code blocks.
        def _restore(match: re.Match[str]) -> str:
            idx = int(match.group(1))
            # Bounds-check: if the index is out of range, leave the original
            # sentinel untouched instead of crashing with IndexError. This
            # guards against any text in the input that happens to look like
            # a sentinel (e.g. literal "\uFDD00\uFDD1").
            return protected[idx] if 0 <= idx < len(protected) else match.group(0)

        text = re.sub(
            rf"{re.escape(_CODE_PLACEHOLDER_OPEN)}(\d+){re.escape(_CODE_PLACEHOLDER_CLOSE)}",
            _restore,
            text,
        )

        return text

    # ------------------------------------------------------------------ tables

    def _convert_tables(self, text: str) -> str:
        """Convert markdown tables to ASCII tables wrapped in code fences.

        A markdown table consists of a header row, a separator row, and zero
        or more data rows. Discord does not render markdown tables, so we
        convert them to aligned ASCII art inside triple-backtick code blocks
        (which Discord renders as monospace, preserving column alignment).

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

        Cells are split on ``|`` characters that are neither escaped
        (``\\|``) nor the separator inside a Slack-style link
        (``<url|text>``). The angle-bracket guard is harmless for Discord
        (where standard markdown ``[text](url)`` passes through natively)
        but kept so the parser also tolerates the rare ``<url|text>`` form
        if the LLM emits it.

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

        # Walk character by character so we can ignore pipes that are
        # either backslash-escaped or sit inside ``<...>`` (Slack link
        # delimiters). Plain ``re.split`` cannot express the "not inside
        # angle brackets" condition cleanly.
        cells: list[str] = []
        current: list[str] = []
        in_angle = False
        i = 0
        n = len(s)
        while i < n:
            ch = s[i]
            if ch == "<":
                in_angle = True
                current.append(ch)
            elif ch == ">":
                in_angle = False
                current.append(ch)
            elif ch == "\\" and i + 1 < n and s[i + 1] == "|":
                # Escaped pipe — keep both characters inside the cell.
                current.append("\\|")
                i += 1
            elif ch == "|" and not in_angle:
                cells.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
            i += 1
        cells.append("".join(current).strip())
        return cells

    def _build_ascii_table(self, header_line: str, data_lines: list[str]) -> str:
        """Build an ASCII table from parsed markdown table parts.

        Columns are separated by three spaces, and each column is padded
        with spaces to match the widest cell. The separator row uses ``-``
        characters matching each column's width.

        Args:
            header_line: Header row of the markdown table.
            data_lines: Data rows of the markdown table (separator excluded).

        Returns:
            ASCII art table wrapped in triple backticks.
        """
        headers = self._parse_table_row(header_line)
        rows = [self._parse_table_row(line) for line in data_lines]

        # Widen the column count to the maximum seen across ALL rows so that
        # data rows with extra columns are not silently truncated.
        ncols = max(len(headers), max((len(r) for r in rows), default=0))
        widths = [0] * ncols
        # Initialise widths from the header first.
        for k, h in enumerate(headers):
            widths[k] = len(h)
        # Then widen using all data rows (including any columns beyond header).
        for row in rows:
            for k, cell in enumerate(row):
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
