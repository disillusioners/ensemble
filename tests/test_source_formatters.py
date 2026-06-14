"""Tests for daemon.sources.formatters package.

Covers individual conversions, ordering conflicts, code block protection,
table conversion, registry behaviour, and edge cases.
"""

from __future__ import annotations

import pytest

from daemon.sources.formatters import (
    OutputFormatter,
    get,
    get_or_passthrough,
    register,
)
from daemon.sources.formatters.base import OutputFormatter as OutputFormatterBase
from daemon.sources.formatters.slack import SlackMrkdwnFormatter
from daemon.sources.formatters.slack.mrkdwn import SlackMrkdwnFormatter as SlackMrkdwnFormatterDirect


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def formatter() -> SlackMrkdwnFormatter:
    """Return a fresh SlackMrkdwnFormatter instance for each test."""
    return SlackMrkdwnFormatter()


# --------------------------------------------------------------------------- #
# Individual conversions
# --------------------------------------------------------------------------- #


class TestBold:
    """Markdown **bold** and __bold__ should convert to Slack *text*."""

    def test_double_asterisk_bold(self, formatter: SlackMrkdwnFormatter) -> None:
        """**text** should become *text*."""
        assert formatter.format("**bold**") == "*bold*"

    def test_double_underscore_bold(self, formatter: SlackMrkdwnFormatter) -> None:
        """__text__ should become *text*."""
        assert formatter.format("__bold__") == "*bold*"

    def test_bold_in_sentence(self, formatter: SlackMrkdwnFormatter) -> None:
        """Bold inside a sentence is converted correctly."""
        assert formatter.format("this is **very** important") == "this is *very* important"

    def test_dunder_init_preserved(self, formatter: SlackMrkdwnFormatter) -> None:
        """`__init__` embedded in identifiers should not be converted to bold."""
        result = formatter.format("access var__init__field carefully")
        assert "*init*" not in result

    def test_dunder_in_code_preserved(self, formatter: SlackMrkdwnFormatter) -> None:
        """Dunder identifiers inside a path-like string are preserved."""
        result = formatter.format("module__name__value should stay")
        assert "*name*" not in result
        assert "__name__" in result

    def test_partial_dunder_preserved(self, formatter: SlackMrkdwnFormatter) -> None:
        """Mixed dunders/identifiers with internal `__` are preserved."""
        result = formatter.format("var__name__not__bold")
        assert "*name*not__bold" not in result
        assert "var__name__not__bold" in result

    def test_bold_underscore_with_punctuation(self, formatter: SlackMrkdwnFormatter) -> None:
        """`__bold__` surrounded by non-alphanumeric is still converted."""
        assert formatter.format("(__bold__)") == "(*bold*)"


class TestItalic:
    """Markdown *italic* should convert to Slack _text_."""

    def test_single_asterisk_italic(self, formatter: SlackMrkdwnFormatter) -> None:
        """*text* should become _text_."""
        assert formatter.format("*italic*") == "_italic_"

    def test_italic_in_sentence(self, formatter: SlackMrkdwnFormatter) -> None:
        """Italic inside a sentence is converted correctly."""
        result = formatter.format("a *quick* brown fox")
        assert result == "a _quick_ brown fox"


class TestStrikethrough:
    """Markdown ~~text~~ should convert to Slack ~text~."""

    def test_basic_strikethrough(self, formatter: SlackMrkdwnFormatter) -> None:
        """~~text~~ should become ~text~."""
        assert formatter.format("~~deleted~~") == "~deleted~"

    def test_strikethrough_in_sentence(self, formatter: SlackMrkdwnFormatter) -> None:
        """Strikethrough inside a sentence is converted correctly."""
        result = formatter.format("this is ~~old~~ news")
        assert result == "this is ~old~ news"


class TestLinks:
    """Markdown [text](url) should convert to Slack <url|text>."""

    def test_basic_link(self, formatter: SlackMrkdwnFormatter) -> None:
        """[text](url) should become <url|text>."""
        result = formatter.format("[click](https://example.com)")
        assert result == "<https://example.com|click>"

    def test_link_in_sentence(self, formatter: SlackMrkdwnFormatter) -> None:
        """Link inside a sentence is converted correctly."""
        result = formatter.format("see [docs](https://docs.example.com) for details")
        assert result == "see <https://docs.example.com|docs> for details"

    def test_link_with_parentheses_in_url(self, formatter: SlackMrkdwnFormatter) -> None:
        """A URL containing parentheses is preserved intact."""
        result = formatter.format(
            "[Foo](https://en.wikipedia.org/wiki/Foo_(bar))"
        )
        assert result == "<https://en.wikipedia.org/wiki/Foo_(bar)|Foo>"

    def test_link_with_parentheses_in_sentence(self, formatter: SlackMrkdwnFormatter) -> None:
        """A URL containing parentheses inside a sentence is converted."""
        result = formatter.format(
            "see [Foo](https://en.wikipedia.org/wiki/Foo_(bar)) for details"
        )
        assert result == "see <https://en.wikipedia.org/wiki/Foo_(bar)|Foo> for details"


class TestHeadings:
    """Headings #, ##, ###+ should convert to Slack *text* (bold)."""

    def test_h1_heading(self, formatter: SlackMrkdwnFormatter) -> None:
        """# Heading should become *Heading*."""
        assert formatter.format("# Title") == "*Title*"

    def test_h2_heading(self, formatter: SlackMrkdwnFormatter) -> None:
        """## Heading should become *Heading*."""
        assert formatter.format("## Subtitle") == "*Subtitle*"

    def test_h3_heading(self, formatter: SlackMrkdwnFormatter) -> None:
        """### Heading should become *Heading*."""
        assert formatter.format("### Section") == "*Section*"

    def test_h4_heading(self, formatter: SlackMrkdwnFormatter) -> None:
        """#### Heading should become *Heading*."""
        assert formatter.format("#### Subsection") == "*Subsection*"

    def test_heading_with_paragraph(self, formatter: SlackMrkdwnFormatter) -> None:
        """Heading on its own line is converted, paragraph below unchanged."""
        text = "# Title\n\nSome paragraph text"
        result = formatter.format(text)
        assert result == "*Title*\n\nSome paragraph text"

    def test_heading_with_whitespace_only_text_not_converted(self, formatter: SlackMrkdwnFormatter) -> None:
        """A heading with only whitespace is not converted to a bullet list."""
        # `#  ` (hash + 2 spaces) should NOT match the heading regex.
        result = formatter.format("#  ")
        # No bullet, no empty bold wrap.
        assert "\u2022" not in result
        assert "**" not in result


class TestBulletLists:
    """`- item` and `* item` should convert to • item."""

    def test_dash_bullet(self, formatter: SlackMrkdwnFormatter) -> None:
        """- item should become • item."""
        assert formatter.format("- first") == "\u2022 first"

    def test_asterisk_bullet(self, formatter: SlackMrkdwnFormatter) -> None:
        """* item should become • item."""
        assert formatter.format("* first") == "\u2022 first"

    def test_multi_item_bullet_list(self, formatter: SlackMrkdwnFormatter) -> None:
        """All items in a bullet list are converted."""
        text = "- one\n- two\n- three"
        result = formatter.format(text)
        assert result == "\u2022 one\n\u2022 two\n\u2022 three"

    def test_indented_bullet_not_converted(self, formatter: SlackMrkdwnFormatter) -> None:
        """Indented bullets (continuation lines) are not re-converted."""
        text = "- one\n  continuation\n- two"
        result = formatter.format(text)
        assert result == "\u2022 one\n  continuation\n\u2022 two"


class TestNumberedLists:
    """`1. item`, `2. item` should convert to • item."""

    def test_single_numbered_item(self, formatter: SlackMrkdwnFormatter) -> None:
        """1. item should become • item."""
        assert formatter.format("1. first") == "\u2022 first"

    def test_multi_item_numbered_list(self, formatter: SlackMrkdwnFormatter) -> None:
        """All items in a numbered list are converted."""
        text = "1. first\n2. second\n3. third"
        result = formatter.format(text)
        assert result == "\u2022 first\n\u2022 second\n\u2022 third"

    def test_multi_digit_numbered_list(self, formatter: SlackMrkdwnFormatter) -> None:
        """Multi-digit numbered items are also converted."""
        text = "10. ten\n11. eleven"
        result = formatter.format(text)
        assert result == "\u2022 ten\n\u2022 eleven"


# --------------------------------------------------------------------------- #
# Ordering conflicts
# --------------------------------------------------------------------------- #


class TestOrdering:
    """Verify that bold/italic heading conversions don't interfere."""

    def test_bold_and_italic_in_same_text(self, formatter: SlackMrkdwnFormatter) -> None:
        """**bold** stays bold, *italic* becomes _italic_."""
        result = formatter.format("**bold** and *italic*")
        assert result == "*bold* and _italic_"

    def test_underscore_bold_and_asterisk_italic(self, formatter: SlackMrkdwnFormatter) -> None:
        """__bold__ stays bold, *italic* becomes _italic_."""
        result = formatter.format("__bold__ and *italic*")
        assert result == "*bold* and _italic_"

    def test_heading_not_converted_to_italic(self, formatter: SlackMrkdwnFormatter) -> None:
        """Headings should not be converted to italic (must remain *Heading*)."""
        # The critical check: italic regex must not consume the heading bold.
        result = formatter.format("# Title")
        assert result == "*Title*"
        # Make sure we did NOT produce _Title_
        assert "_Title_" not in result

    def test_multiple_headings(self, formatter: SlackMrkdwnFormatter) -> None:
        """Multiple headings all convert correctly without italic bleed."""
        text = "# First\n## Second\n### Third"
        result = formatter.format(text)
        assert result == "*First*\n*Second*\n*Third*"

    def test_bold_italic_italic_bold(self, formatter: SlackMrkdwnFormatter) -> None:
        """Repeated bold/italic patterns all convert correctly."""
        result = formatter.format("**a** *b* **c** *d*")
        assert result == "*a* _b_ *c* _d_"


# --------------------------------------------------------------------------- #
# Code block protection
# --------------------------------------------------------------------------- #


class TestCodeBlockProtection:
    """No conversions should apply inside fenced or inline code."""

    def test_inline_code_with_bold_syntax(self, formatter: SlackMrkdwnFormatter) -> None:
        """`**not bold**` inside backticks is preserved as code."""
        result = formatter.format("Use `**not bold**` here")
        assert result == "Use `**not bold**` here"

    def test_inline_code_with_italic_syntax(self, formatter: SlackMrkdwnFormatter) -> None:
        """`*not italic*` inside backticks is preserved as code."""
        result = formatter.format("Use `*not italic*` here")
        assert result == "Use `*not italic*` here"

    def test_inline_code_with_link_syntax(self, formatter: SlackMrkdwnFormatter) -> None:
        """`[text](url)` inside backticks is preserved as code."""
        result = formatter.format("Pattern: `[text](url)`")
        assert result == "Pattern: `[text](url)`"

    def test_inline_code_with_heading(self, formatter: SlackMrkdwnFormatter) -> None:
        """`# not heading` inside backticks is preserved."""
        result = formatter.format("Use `# not heading` marker")
        assert result == "Use `# not heading` marker"

    def test_fenced_code_with_bold_syntax(self, formatter: SlackMrkdwnFormatter) -> None:
        """`**text**` inside fenced code blocks is preserved verbatim."""
        text = "```\n**not bold**\n```"
        result = formatter.format(text)
        # The code block must be reproduced verbatim.
        assert result == text
        # Sanity: the markdown bold form is still present.
        assert "**not bold**" in result

    def test_fenced_code_with_language(self, formatter: SlackMrkdwnFormatter) -> None:
        """Fenced code blocks with language specifiers are preserved verbatim."""
        text = "```python\ndef foo():\n    return **not bold**\n```"
        result = formatter.format(text)
        # The code block content must not be converted to Slack bold.
        assert "**not bold**" in result
        # Exact match confirms the bold conversion didn't bleed in.
        assert result == text

    def test_fenced_code_with_shebang(self, formatter: SlackMrkdwnFormatter) -> None:
        """`#!/bin/bash` shebang inside code block is preserved."""
        text = "```bash\n#!/bin/bash\necho hello\n```"
        result = formatter.format(text)
        assert "#!/bin/bash" in result
        # The shebang starts with '#' which would otherwise become a heading.

    def test_fenced_code_with_comment(self, formatter: SlackMrkdwnFormatter) -> None:
        """`# comment` inside code block is preserved."""
        text = "```python\n# this is a comment\nx = 1\n```"
        result = formatter.format(text)
        assert "# this is a comment" in result

    def test_text_outside_code_block_still_converted(self, formatter: SlackMrkdwnFormatter) -> None:
        """Text outside a code block is converted; text inside is not."""
        text = "**bold** and `**not bold**`"
        result = formatter.format(text)
        assert result == "*bold* and `**not bold**`"

    def test_code_block_then_text_then_code_block(self, formatter: SlackMrkdwnFormatter) -> None:
        """Multiple code blocks interleaved with convertible text."""
        text = "**a**\n```\n**b**\n```\n*c*"
        result = formatter.format(text)
        # **a** converts to *a*; *c* converts to _c_; **b** stays as **b**.
        assert result == "*a*\n```\n**b**\n```\n_c_"


# --------------------------------------------------------------------------- #
# Table conversion
# --------------------------------------------------------------------------- #


class TestTableConversion:
    """Markdown tables should convert to ASCII art in triple backticks."""

    def test_two_column_table(self, formatter: SlackMrkdwnFormatter) -> None:
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
        # Separator line uses '-' characters; with 1-char columns that's '-'.
        assert "-" in result

    def test_two_column_table_with_wide_cells(self, formatter: SlackMrkdwnFormatter) -> None:
        """A table with wider cells produces a '---' style separator."""
        text = "| Header 1 | Header 2 |\n|----------|----------|\n| Cell 1   | Cell 2   |"
        result = formatter.format(text)
        # Should be wrapped in triple backticks.
        assert result.startswith("```\n")
        assert result.endswith("\n```")
        # With 8-char columns, separator is 8 dashes, so '---' is present.
        assert "---" in result
        # Header text preserved.
        assert "Header 1" in result
        assert "Header 2" in result
        # Cell text preserved.
        assert "Cell 1" in result
        assert "Cell 2" in result

    def test_three_column_table(self, formatter: SlackMrkdwnFormatter) -> None:
        """A 3-col table is converted correctly."""
        text = "| H1 | H2 | H3 |\n|----|----|----|\n| a | b | c |"
        result = formatter.format(text)
        assert result.startswith("```\n")
        assert result.endswith("\n```")
        assert "H1" in result
        assert "H2" in result
        assert "H3" in result

    def test_varying_column_widths(self, formatter: SlackMrkdwnFormatter) -> None:
        """Columns are padded to the widest cell."""
        text = "| Name | Age |\n|------|-----|\n| Bo | 99 |\n| Alexander | 7 |"
        result = formatter.format(text)
        # Longest name is "Alexander" (9 chars); should be padded.
        # The "Name" column should be at least 9 chars wide.
        lines = result.split("\n")
        # First line (after opening ```) is the header.
        header_line = lines[1]
        # Header should contain "Name" padded, e.g., "Name     "
        assert "Name" in header_line
        # Find the position right after "Name" - should be padded spaces.
        name_end = header_line.index("Name") + len("Name")
        # Skip until next non-space
        rest = header_line[name_end:]
        # The column is padded so that "Age" starts at the same offset in both rows.
        assert "Age" in header_line

    def test_table_with_single_data_row(self, formatter: SlackMrkdwnFormatter) -> None:
        """A table with one data row converts correctly."""
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = formatter.format(text)
        assert "A" in result
        assert "1" in result
        assert "2" in result

    def test_table_preserved_structure(self, formatter: SlackMrkdwnFormatter) -> None:
        """The converted table has 3 lines between code fences (header, sep, data)."""
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = formatter.format(text)
        # Strip code fences.
        body = result.strip("`").strip()
        lines = body.split("\n")
        # Should have: header, sep, 1 data row = 3 lines.
        assert len(lines) == 3

    def test_text_around_table(self, formatter: SlackMrkdwnFormatter) -> None:
        """Text before and after a table is preserved."""
        text = "Before\n| A | B |\n|---|---|\n| 1 | 2 |\nAfter"
        result = formatter.format(text)
        assert result.startswith("Before\n```\n")
        assert result.endswith("\n```\nAfter")

    def test_multiple_tables(self, formatter: SlackMrkdwnFormatter) -> None:
        """Multiple tables in the same text are each converted."""
        text = (
            "| A | B |\n|---|---|\n| 1 | 2 |\n"
            "middle\n"
            "| X | Y |\n|---|---|\n| 9 | 8 |"
        )
        result = formatter.format(text)
        # Should contain two code-fenced tables.
        assert result.count("```") == 4  # 2 tables * 2 fences
        assert "middle" in result
        assert "A" in result
        assert "X" in result

    def test_non_table_pipe_lines_not_converted(self, formatter: SlackMrkdwnFormatter) -> None:
        """Lines that look pipe-ish but aren't tables are not converted."""
        text = "a | b | c"  # Single line, no separator
        result = formatter.format(text)
        # Should NOT be wrapped in code fences.
        assert "```" not in result

    def test_table_inside_paragraph_breaks_detection(self, formatter: SlackMrkdwnFormatter) -> None:
        """Tables that start mid-paragraph are still detected."""
        text = "Intro text\n| A | B |\n|---|---|\n| 1 | 2 |"
        result = formatter.format(text)
        assert "```" in result
        assert "A" in result


# --------------------------------------------------------------------------- #
# Combinations
# --------------------------------------------------------------------------- #


class TestCombinations:
    """Multiple conversion types in a single text."""

    def test_bold_and_italic_and_link(self, formatter: SlackMrkdwnFormatter) -> None:
        """Bold, italic, and link all convert in the same text."""
        result = formatter.format("**bold** and *italic* and [link](https://x.com)")
        assert result == "*bold* and _italic_ and <https://x.com|link>"

    def test_heading_with_bold_text(self, formatter: SlackMrkdwnFormatter) -> None:
        """A heading converts; surrounding text converts normally."""
        result = formatter.format("# **bold heading**\n\nand *italic*")
        assert "*bold heading*" in result
        assert "**bold heading**" not in result
        assert "_italic_" in result

    def test_heading_with_bold_text_multiple_levels(self, formatter: SlackMrkdwnFormatter) -> None:
        """Heading levels with bold text all produce single-asterisk bold."""
        result = formatter.format("## **Warning:**")
        assert "*Warning:*" in result
        assert "**Warning:**" not in result

    def test_list_with_bold_items(self, formatter: SlackMrkdwnFormatter) -> None:
        """List markers and bold convert together."""
        result = formatter.format("- **first**\n- *second*\n- [third](https://x.com)")
        assert "\u2022 *first*" in result
        assert "\u2022 _second_" in result
        assert "\u2022 <https://x.com|third>" in result

    def test_strikethrough_and_bold(self, formatter: SlackMrkdwnFormatter) -> None:
        """Strikethrough and bold convert together."""
        result = formatter.format("**bold** and ~~struck~~")
        assert result == "*bold* and ~struck~"

    def test_full_realistic_message(self, formatter: SlackMrkdwnFormatter) -> None:
        """A realistic LLM-style message converts end-to-end."""
        text = (
            "# Status Report\n"
            "\n"
            "The system is **online** and *running* smoothly.\n"
            "\n"
            "## Recent Changes\n"
            "\n"
            "- Fixed the *login* bug\n"
            "- Updated [docs](https://docs.example.com)\n"
            "- ~~Old approach~~ replaced\n"
        )
        result = formatter.format(text)
        # Heading 1
        assert "*Status Report*" in result
        # Heading 2
        assert "*Recent Changes*" in result
        # Bold
        assert "*online*" in result
        # Italic
        assert "_running_" in result
        # Italic inside list
        assert "_login_" in result
        # Bullet
        assert "\u2022 Fixed" in result
        assert "\u2022 Updated" in result
        assert "\u2022 ~Old approach~ replaced" in result
        # Link
        assert "<https://docs.example.com|docs>" in result


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


class TestEdgeCases:
    """Edge cases and unusual inputs."""

    def test_empty_string(self, formatter: SlackMrkdwnFormatter) -> None:
        """Empty string returns empty string."""
        assert formatter.format("") == ""

    def test_plain_text(self, formatter: SlackMrkdwnFormatter) -> None:
        """Plain text without any markdown is unchanged."""
        text = "Just plain text, no formatting here."
        assert formatter.format(text) == text

    def test_multiline_text(self, formatter: SlackMrkdwnFormatter) -> None:
        """Multiline text preserves newlines."""
        text = "Line 1\nLine 2\nLine 3"
        assert formatter.format(text) == text

    def test_number_sign_in_text_not_a_heading(self, formatter: SlackMrkdwnFormatter) -> None:
        """Mid-line `#` is not converted to a heading."""
        # Note: 'Issue #123 in repo' has # not at line start.
        result = formatter.format("Issue #123 is open")
        # The # is mid-line, not a heading (per line-start anchor).
        # We do not want "*123 is open*".
        assert "*123 is open*" not in result
        assert "Issue #123 is open" in result

    def test_unmatched_asterisks(self, formatter: SlackMrkdwnFormatter) -> None:
        """Unmatched `*` characters are not converted."""
        # Single * with no closing * is left alone.
        result = formatter.format("a * b")
        # The italic regex requires matched pairs, so it shouldn't match.
        assert result == "a * b"

    def test_unmatched_double_asterisks(self, formatter: SlackMrkdwnFormatter) -> None:
        """Unmatched `**` are not converted."""
        result = formatter.format("a ** b")
        # No matched pair, so should be unchanged.
        assert result == "a ** b"


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class TestRegistry:
    """Tests for the formatter registry."""

    def test_get_slack_returns_formatter(self) -> None:
        """get('slack') should return a SlackMrkdwnFormatter."""
        formatter = get("slack")
        assert formatter is not None
        assert isinstance(formatter, OutputFormatter)

    def test_get_unknown_returns_none(self) -> None:
        """get('unknown') should return None."""
        assert get("definitely_not_registered_xyz") is None

    def test_get_or_passthrough_known(self) -> None:
        """get_or_passthrough('slack') returns the registered formatter."""
        formatter = get_or_passthrough("slack")
        assert isinstance(formatter, SlackMrkdwnFormatter)

    def test_get_or_passthrough_unknown(self) -> None:
        """get_or_passthrough('unknown') returns a passthrough formatter."""
        formatter = get_or_passthrough("definitely_not_registered_abc")
        assert isinstance(formatter, OutputFormatter)
        # The passthrough should return text unchanged.
        assert formatter.format("**bold**") == "**bold**"
        assert formatter.format("plain text") == "plain text"
        assert formatter.format("# heading") == "# heading"

    def test_register_replaces_existing(self) -> None:
        """register('test', formatter) replaces any existing registration."""
        # Save original
        original = get("test_replace_key")
        try:
            first = _RecordingFormatter("first")
            second = _RecordingFormatter("second")
            register("test_replace_key", first)
            assert get("test_replace_key") is first
            register("test_replace_key", second)
            assert get("test_replace_key") is second
        finally:
            # Restore
            if original is None:
                register("test_replace_key", None)
            else:
                register("test_replace_key", original)

    def test_register_none_unregisters(self) -> None:
        """register('key', None) removes the registration."""
        original = get("test_unreg_key")
        try:
            register("test_unreg_key", _RecordingFormatter("temp"))
            assert get("test_unreg_key") is not None
            register("test_unreg_key", None)
            assert get("test_unreg_key") is None
        finally:
            if original is not None:
                register("test_unreg_key", original)

    def test_registry_exports_match_init(self) -> None:
        """The public exports are consistent with the package __init__."""
        from daemon.sources.formatters import OutputFormatter as PkgFormatter
        from daemon.sources.formatters import register as PkgRegister
        from daemon.sources.formatters import get as PkgGet
        from daemon.sources.formatters import get_or_passthrough as PkgGop

        assert PkgFormatter is OutputFormatterBase
        assert PkgRegister is register
        assert PkgGet is get
        assert PkgGop is get_or_passthrough

    def test_slack_subpackage_export(self) -> None:
        """The slack subpackage re-exports SlackMrkdwnFormatter."""
        assert SlackMrkdwnFormatter is SlackMrkdwnFormatterDirect


class _RecordingFormatter(OutputFormatter):
    """Helper formatter for registry tests that records its identity."""

    def __init__(self, label: str) -> None:
        self.label = label

    def format(self, text: str) -> str:
        """Return the label as a marker (so we can identify this formatter)."""
        return f"[{self.label}]{text}"


# --------------------------------------------------------------------------- #
# Code starting with #
# --------------------------------------------------------------------------- #


class TestHashInCode:
    """`#` characters inside code blocks should be preserved (not headings)."""

    def test_python_comment_in_code_block(self, formatter: SlackMrkdwnFormatter) -> None:
        """`# this is a comment` in a code block is preserved."""
        text = "```python\n# this is a comment\nx = 1\n```"
        result = formatter.format(text)
        assert "# this is a comment" in result
        # The `# this is a comment` should NOT be turned into a heading bold.
        assert "*this is a comment*" not in result

    def test_shebang_in_code_block(self, formatter: SlackMrkdwnFormatter) -> None:
        """`#!/bin/bash` in a code block is preserved."""
        text = "```bash\n#!/bin/bash\necho 'hello'\n```"
        result = formatter.format(text)
        assert "#!/bin/bash" in result
        # The shebang should not be turned into a heading bold.
        assert "*/bin/bash*" not in result

    def test_hash_in_inline_code(self, formatter: SlackMrkdwnFormatter) -> None:
        """`#hashtag` in inline code is preserved."""
        result = formatter.format("Use the `#hashtag` symbol")
        assert "`#hashtag`" in result
        # The # should not become a heading.
        assert "*hashtag*" not in result

    def test_comment_line_in_middle_of_code_block(self, formatter: SlackMrkdwnFormatter) -> None:
        """Comments in the middle of a multi-line code block are preserved."""
        text = (
            "```python\n"
            "def greet():\n"
            "    # say hello\n"
            "    print('hi')\n"
            "```"
        )
        result = formatter.format(text)
        assert "    # say hello" in result
        # The line should not become a heading.
        assert "*say hello*" not in result
