"""Tests for daemon.sources.formatters.discord package.

Covers table conversion, header conversion, code block protection, native
markdown pass-through, and mixed content handling. Mirrors the style of
``tests/test_source_formatters.py`` for the Slack formatter, but scoped
to the Discord-specific conversion surface.

Run with::

    pytest daemon/tests/sources/formatters/test_discord_markdown.py -v
"""

from __future__ import annotations

import pytest

from daemon.sources.formatters.discord import DiscordFormatter
from daemon.sources.formatters.discord.discord_markdown import (
    DiscordFormatter as DiscordFormatterDirect,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def formatter() -> DiscordFormatter:
    """Return a fresh DiscordFormatter instance for each test."""
    return DiscordFormatter()


# --------------------------------------------------------------------------- #
# Table conversion
# --------------------------------------------------------------------------- #


class TestTableConversion:
    """Markdown tables should convert to ASCII art in triple backticks."""

    def test_simple_two_column_table(self, formatter: DiscordFormatter) -> None:
        """A simple 2-col table is converted to ASCII art."""
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = formatter.format(text)
        # Should be wrapped in triple backticks.
        assert result.startswith("```\n")
        assert result.endswith("\n```")
        # Header and cells preserved.
        assert "A" in result
        assert "B" in result
        assert "1" in result
        assert "2" in result
        # Separator line uses '-' characters.
        assert "-" in result

    def test_table_with_alignment_markers(self, formatter: DiscordFormatter) -> None:
        """Alignment markers (:---, :--:, ---:) on the separator are accepted."""
        text = (
            "| Left | Center | Right |\n"
            "|:-----|:------:|------:|\n"
            "| a    | b      | c     |"
        )
        result = formatter.format(text)
        # Should be wrapped in triple backticks.
        assert result.startswith("```\n")
        assert result.endswith("\n```")
        # All cell content preserved.
        assert "Left" in result
        assert "Center" in result
        assert "Right" in result
        assert "a" in result
        assert "b" in result
        assert "c" in result
        # The separator line is between header and data; it should be a
        # row of dashes (with the 3-space column gaps that the ASCII
        # builder uses between every column).
        body = result.strip("`").strip()
        lines = body.split("\n")
        # The middle line is the separator.
        sep_line = lines[1]
        assert sep_line.replace("   ", "").replace(" ", "") == "-" * (
            # The widest column is "Center" / "Right" = 6 chars; 3 columns
            # of that width. The two 3-space gaps total 6 spaces.
            sum(len(c) for c in ["Left", "Center", "Right"])
        )
        # Or, more simply: every non-space character in the sep is a dash.
        for ch in sep_line:
            assert ch in {"-", " "}

    def test_three_column_table(self, formatter: DiscordFormatter) -> None:
        """A 3-col table is converted correctly."""
        text = "| H1 | H2 | H3 |\n|----|----|----|\n| a | b | c |"
        result = formatter.format(text)
        assert result.startswith("```\n")
        assert result.endswith("\n```")
        assert "H1" in result
        assert "H2" in result
        assert "H3" in result
        assert "a" in result
        assert "b" in result
        assert "c" in result

    def test_table_preserved_structure(self, formatter: DiscordFormatter) -> None:
        """The converted table has 3 lines between code fences (header, sep, data)."""
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = formatter.format(text)
        body = result.strip("`").strip()
        lines = body.split("\n")
        # header, sep, 1 data row = 3 lines.
        assert len(lines) == 3

    def test_text_around_table(self, formatter: DiscordFormatter) -> None:
        """Text before and after a table is preserved."""
        text = "Before\n| A | B |\n|---|---|\n| 1 | 2 |\nAfter"
        result = formatter.format(text)
        assert result.startswith("Before\n```\n")
        assert result.endswith("\n```\nAfter")

    def test_multiple_tables(self, formatter: DiscordFormatter) -> None:
        """Multiple tables in the same text are each converted."""
        text = (
            "| A | B |\n|---|---|\n| 1 | 2 |\nmiddle\n| X | Y |\n|---|---|\n| 9 | 8 |"
        )
        result = formatter.format(text)
        # Should contain two code-fenced tables.
        assert result.count("```") == 4  # 2 tables * 2 fences
        assert "middle" in result
        assert "A" in result
        assert "X" in result

    def test_non_table_pipe_lines_not_converted(
        self, formatter: DiscordFormatter
    ) -> None:
        """Lines that look pipe-ish but aren't tables are not converted."""
        text = "a | b | c"  # Single line, no separator
        result = formatter.format(text)
        # Should NOT be wrapped in code fences.
        assert "```" not in result


# --------------------------------------------------------------------------- #
# Header conversion
# --------------------------------------------------------------------------- #


class TestHeaderConversion:
    """ATX headers #..###### should convert to Discord **bold**."""

    def test_h1(self, formatter: DiscordFormatter) -> None:
        """# Heading → **Heading**."""
        assert formatter.format("# H1") == "**H1**"

    def test_h2(self, formatter: DiscordFormatter) -> None:
        """## Heading → **Heading**."""
        assert formatter.format("## H2") == "**H2**"

    def test_h3(self, formatter: DiscordFormatter) -> None:
        """### Heading → **Heading**."""
        assert formatter.format("### H3") == "**H3**"

    def test_h4(self, formatter: DiscordFormatter) -> None:
        """#### Heading → **Heading**."""
        assert formatter.format("#### H4") == "**H4**"

    def test_h5(self, formatter: DiscordFormatter) -> None:
        """##### Heading → **Heading**."""
        assert formatter.format("##### H5") == "**H5**"

    def test_h6(self, formatter: DiscordFormatter) -> None:
        """###### Heading → **Heading**."""
        assert formatter.format("###### H6") == "**H6**"

    def test_hashtag_without_space_not_converted(
        self, formatter: DiscordFormatter
    ) -> None:
        """`#hashtag` (no space) is NOT converted to bold."""
        result = formatter.format("see #hashtag for details")
        assert "**" not in result
        assert "#hashtag" in result

    def test_heading_inside_fenced_code_block_not_converted(
        self, formatter: DiscordFormatter
    ) -> None:
        """A heading inside a fenced code block is left alone."""
        text = "```\n# not a heading\n```"
        result = formatter.format(text)
        # The # line is inside code, so should NOT be converted.
        assert "**not a heading**" not in result
        assert "# not a heading" in result

    def test_heading_inside_inline_code_not_converted(
        self, formatter: DiscordFormatter
    ) -> None:
        """A heading inside inline code is left alone."""
        text = "see `# not a heading` for syntax"
        result = formatter.format(text)
        # The inline code is preserved verbatim.
        assert "`# not a heading`" in result
        assert "**not a heading**" not in result

    def test_heading_with_paragraph(self, formatter: DiscordFormatter) -> None:
        """Heading on its own line is converted; paragraph below unchanged."""
        text = "# Title\n\nSome paragraph text"
        result = formatter.format(text)
        assert result == "**Title**\n\nSome paragraph text"

    def test_already_bold_heading_not_double_wrapped(
        self, formatter: DiscordFormatter
    ) -> None:
        """A heading whose content is already wrapped in **...** is left alone.

        The ``##`` marker is consumed (Discord does not render it), but the
        ``**...**`` content is preserved as-is rather than wrapped a second
        time into ``****...****``.
        """
        text = "## **Already Bold**"
        result = formatter.format(text)
        # The ## marker is consumed and the bold content is preserved.
        assert result == "**Already Bold**"
        # Critically, NOT double-wrapped.
        assert "****" not in result

    def test_heading_with_tab_separator(self, formatter: DiscordFormatter) -> None:
        """Tabs between # and content are accepted (matching Slack's regex)."""
        assert formatter.format("#\tTitle") == "**Title**"


# --------------------------------------------------------------------------- #
# Code block protection
# --------------------------------------------------------------------------- #


class TestCodeBlockProtection:
    """Code blocks (fenced and inline) must pass through unchanged."""

    def test_fenced_code_block_passes_through(
        self, formatter: DiscordFormatter
    ) -> None:
        """A fenced code block is not touched."""
        text = "```\nsome code\nwith # not a heading\n```"
        result = formatter.format(text)
        # Code block is preserved verbatim — no ** wrapping.
        assert "**" not in result
        assert "# not a heading" in result
        assert "```" in result

    def test_inline_code_passes_through(self, formatter: DiscordFormatter) -> None:
        """An inline code span is not touched."""
        text = "use `#hash` as a tag"
        result = formatter.format(text)
        # Inline code is preserved verbatim.
        assert "`#hash`" in result
        assert "**hash**" not in result

    def test_table_inside_fenced_code_block_not_converted(
        self, formatter: DiscordFormatter
    ) -> None:
        """A markdown table inside a fenced code block is left alone."""
        text = "Example:\n```\n| A | B |\n|---|---|\n| 1 | 2 |\n```"
        result = formatter.format(text)
        # The table inside the code block is not converted to ASCII art
        # wrapped in ``` (that would mean double-nested code fences).
        # The original code fence with the table inside stays intact.
        assert result.count("```") == 2
        # The pipe-only markdown table lines are preserved verbatim
        # inside the code block.
        assert "| A | B |" in result
        assert "| 1 | 2 |" in result

    def test_multiple_fenced_blocks_preserved(
        self, formatter: DiscordFormatter
    ) -> None:
        """Multiple fenced code blocks are all preserved."""
        text = "```\n# a\n```\nmid\n```\n# b\n```"
        result = formatter.format(text)
        # Neither # line is converted.
        assert "**a**" not in result
        assert "**b**" not in result
        assert "# a" in result
        assert "# b" in result
        # Original code fences survive.
        assert result.count("```") == 4


# --------------------------------------------------------------------------- #
# Native markdown pass-through
# --------------------------------------------------------------------------- #


class TestNativeMarkdownPassthrough:
    """Discord-native markdown is left untouched by the formatter."""

    def test_bold_preserved(self, formatter: DiscordFormatter) -> None:
        """**bold** passes through unchanged."""
        assert formatter.format("**bold**") == "**bold**"

    def test_italic_preserved(self, formatter: DiscordFormatter) -> None:
        """*italic* passes through unchanged."""
        assert formatter.format("*italic*") == "*italic*"

    def test_underscore_italic_preserved(self, formatter: DiscordFormatter) -> None:
        """_italic_ passes through unchanged."""
        assert formatter.format("_italic_") == "_italic_"

    def test_strikethrough_preserved(self, formatter: DiscordFormatter) -> None:
        """~~strike~~ passes through unchanged."""
        assert formatter.format("~~strike~~") == "~~strike~~"

    def test_link_preserved(self, formatter: DiscordFormatter) -> None:
        """[text](url) passes through unchanged (Discord native)."""
        text = "see [docs](https://example.com) for details"
        assert formatter.format(text) == text

    def test_blockquote_preserved(self, formatter: DiscordFormatter) -> None:
        """> quote passes through unchanged."""
        assert formatter.format("> a quote") == "> a quote"

    def test_dash_list_preserved(self, formatter: DiscordFormatter) -> None:
        """- item passes through unchanged (Discord renders natively)."""
        text = "- one\n- two\n- three"
        assert formatter.format(text) == text

    def test_numbered_list_preserved(self, formatter: DiscordFormatter) -> None:
        """1. item passes through unchanged (Discord renders natively)."""
        text = "1. first\n2. second"
        assert formatter.format(text) == text

    def test_inline_code_preserved(self, formatter: DiscordFormatter) -> None:
        """`code` passes through unchanged."""
        assert formatter.format("use `foo()` here") == "use `foo()` here"


# --------------------------------------------------------------------------- #
# Mixed content
# --------------------------------------------------------------------------- #


class TestMixedContent:
    """Headers + tables + native markdown in one message."""

    def test_headers_tables_and_native_in_one_message(
        self, formatter: DiscordFormatter
    ) -> None:
        """Only headers and tables are converted; rest passes through."""
        text = (
            "# Title\n"
            "\n"
            "Some **bold** and *italic* text.\n"
            "\n"
            "| Col1 | Col2 |\n"
            "|------|------|\n"
            "| a    | b    |\n"
            "\n"
            "- list item\n"
            "- another\n"
            "\n"
            "> a quote\n"
            "\n"
            "see [link](https://example.com) here\n"
        )
        result = formatter.format(text)

        # Header converted.
        assert "**Title**" in result
        # Bold/italic/link/blockquote/list untouched.
        assert "**bold**" in result
        assert "*italic*" in result
        assert "[link](https://example.com)" in result
        assert "> a quote" in result
        assert "- list item" in result
        # Table converted to code-fenced ASCII.
        assert "```\n" in result
        assert "Col1" in result
        assert "a" in result

    def test_multiple_headers(self, formatter: DiscordFormatter) -> None:
        """All headers in a multi-line message are converted."""
        text = "# H1\n## H2\n### H3"
        result = formatter.format(text)
        assert "**H1**" in result
        assert "**H2**" in result
        assert "**H3**" in result

    def test_empty_input(self, formatter: DiscordFormatter) -> None:
        """Empty input returns empty output."""
        assert formatter.format("") == ""

    def test_plain_text_unchanged(self, formatter: DiscordFormatter) -> None:
        """Plain text without any markdown passes through."""
        text = "just some plain text with no formatting"
        assert formatter.format(text) == text


# --------------------------------------------------------------------------- #
# Registry integration
# --------------------------------------------------------------------------- #


class TestRegistryIntegration:
    """DiscordFormatter must be auto-registered under 'discord'."""

    def test_discord_registered(self) -> None:
        """The registry has a formatter under 'discord'."""
        from daemon.sources.formatters.registry import get

        registered = get("discord")
        assert registered is not None
        # It should be an instance of DiscordFormatter.
        assert isinstance(registered, DiscordFormatter)
        # And re-exported under the same symbol used in registry.
        assert isinstance(registered, DiscordFormatterDirect)

    def test_get_or_passthrough_returns_discord(
        self,
    ) -> None:
        """get_or_passthrough('discord') returns the real formatter."""
        from daemon.sources.formatters.registry import get_or_passthrough

        formatter = get_or_passthrough("discord")
        assert isinstance(formatter, DiscordFormatter)
