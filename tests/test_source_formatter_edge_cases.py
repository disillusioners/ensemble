"""Edge case tests for the source output formatter layer.

These tests probe the ``SlackMrkdwnFormatter`` and the higher-level
``markdown_to_slack_blocks()`` entry point with realistic LLM output patterns
that the base tests in ``test_source_formatters.py`` do not cover. The goal
is twofold:

* Lock in the correct expected behavior for tricky conversions (mixed
  formatting, nested emphasis, code-block protection, tables, links with
  special characters, etc.).
* Surface false positives and false negatives in the conversion pipeline as
  failing tests that document the bug.

Run with::

    pytest tests/test_source_formatter_edge_cases.py -v
"""

from __future__ import annotations

import pytest

from daemon.sources.adapters.slack.blocks import markdown_to_slack_blocks
from daemon.sources.formatters.slack.mrkdwn import SlackMrkdwnFormatter


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def formatter() -> SlackMrkdwnFormatter:
    """Return a fresh SlackMrkdwnFormatter instance for each test."""
    return SlackMrkdwnFormatter()


# --------------------------------------------------------------------------- #
# 1. Mixed formatting on the same line
# --------------------------------------------------------------------------- #


class TestMixedFormattingSameLine:
    """Bold, italic and strikethrough combined on one line."""

    def test_bold_italic_strike_in_one_line(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """`**bold** and *italic* and ~~strike~~` → all three convert correctly.

        Bold becomes ``*bold*`` (Slack bold), italic becomes ``_italic_`` and
        strikethrough becomes ``~strike~``. Each conversion is independent
        and must not interfere with the others.
        """
        text = "**bold** and *italic* and ~~strike~~"
        result = formatter.format(text)
        assert result == "*bold* and _italic_ and ~strike~"

    def test_bold_italic_strike_split_with_text(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """The same trio embedded in a longer sentence also works."""
        text = "The report is **final**, *confidential*, and ~~public~~."
        result = formatter.format(text)
        assert result == "The report is *final*, _confidential_, and ~public~."


# --------------------------------------------------------------------------- #
# 2. Nested formatting
# --------------------------------------------------------------------------- #


class TestNestedFormatting:
    """Bold wrapping italic (``**bold *italic* text**``)."""

    def test_bold_wrapping_italic(self, formatter: SlackMrkdwnFormatter) -> None:
        """`**bold *bold-italic* text**` → ``*bold _bold-italic_ text*``.

        The outer ``**...**`` is converted to a bold placeholder, which is
        later restored to ``*...*``. While inside the placeholder, the
        inner ``*...*`` is converted to ``_..._``. The expected result has
        a single pair of ``*...*`` wrapping the now-italicised content.
        """
        text = "**bold *bold-italic* text**"
        result = formatter.format(text)
        assert result == "*bold _bold-italic_ text*"

    def test_italic_inside_bold_word(self, formatter: SlackMrkdwnFormatter) -> None:
        """A shorter variant: ``**foo *bar* baz**``."""
        text = "**foo *bar* baz**"
        result = formatter.format(text)
        assert result == "*foo _bar_ baz*"


# --------------------------------------------------------------------------- #
# 3. Headings with inline formatting
# --------------------------------------------------------------------------- #


class TestHeadingsWithFormatting:
    """Headings containing bold or italic syntax."""

    def test_h1_with_bold(self, formatter: SlackMrkdwnFormatter) -> None:
        """`# **Bold Heading**` → `*Bold Heading*` (single bold wrap, no `**`)."""
        result = formatter.format("# **Bold Heading**")
        assert result == "*Bold Heading*"
        # The double-asterisk form must not survive.
        assert "**" not in result

    def test_h2_with_italic(self, formatter: SlackMrkdwnFormatter) -> None:
        """`## *Italic Sub*` → `*Italic Sub*` (italic converted inside heading)."""
        result = formatter.format("## *Italic Sub*")
        assert result == "*_Italic Sub_*"

    def test_h3_with_strike(self, formatter: SlackMrkdwnFormatter) -> None:
        """`### ~~Deprecated~~` → `*~Deprecated~*`."""
        result = formatter.format("### ~~Deprecated~~")
        # The strike uses ~text~ which is a single tilde; the heading wraps
        # the result in *...*.
        assert "*~Deprecated~*" in result


# --------------------------------------------------------------------------- #
# 4. Code block protection (markdown inside code)
# --------------------------------------------------------------------------- #


class TestCodeBlockProtectionMarkdown:
    """Markdown syntax inside fenced code must be preserved verbatim."""

    def test_comment_and_fake_bold_in_code(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """A python code block with a `#` comment and `**not bold**` is preserved.

        The `#` line is a Python comment, not a heading. The `**not bold**`
        string inside a string literal must not be converted to ``*not bold*``
        (i.e. the double-asterisk form must survive intact).
        """
        text = '```python\n# This is a comment\nx = "**not bold**"\n```'
        result = formatter.format(text)
        # The entire code block must be returned unchanged.
        assert result == text
        # Sanity assertions on the contents.
        assert "# This is a comment" in result
        # The double-asterisk form must survive intact (not be collapsed to *).
        assert '**not bold**' in result
        # The comment must NOT be turned into a heading bold.
        assert "*This is a comment*" not in result
        # The string literal must NOT be turned into Slack single-asterisk bold.
        # (Note: '**not bold**' contains '*not bold*' as a substring, so we
        # check that the *outer* double-asterisk pair is still present.)
        assert '"**not bold**"' in result
        # The string literal must NOT be converted to '"*not bold*"' either.
        assert '"*not bold*"' not in result

    def test_code_block_with_link_syntax_inside(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """`[text](url)` inside a code block stays as literal markdown."""
        text = "```\n[text](https://example.com)\n```"
        result = formatter.format(text)
        assert result == text
        assert "[text](https://example.com)" in result

    def test_code_block_with_heading_syntax_inside(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """`#` and `##` inside a code block are not converted to headings."""
        text = "```\n# This is a comment, not a heading\n## neither is this\n```"
        result = formatter.format(text)
        assert result == text
        # No bold wrapping.
        assert "*This is a comment" not in result
        assert "*neither is this" not in result


# --------------------------------------------------------------------------- #
# 5. Inline code protection
# --------------------------------------------------------------------------- #


class TestInlineCodeProtectionEdge:
    """Markdown inside inline backticks must be preserved."""

    def test_inline_code_with_bold(self, formatter: SlackMrkdwnFormatter) -> None:
        """``Use `**not bold**` here`` → unchanged."""
        result = formatter.format("Use `**not bold**` here")
        assert result == "Use `**not bold**` here"

    def test_inline_code_with_link(self, formatter: SlackMrkdwnFormatter) -> None:
        """``See `[docs](url)` here`` → unchanged."""
        result = formatter.format("See `[docs](https://example.com)` here")
        assert result == "See `[docs](https://example.com)` here"

    def test_inline_code_with_underscore_bold(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """``Use `__not_bold__` here`` → unchanged (inline code protects ``__``)."""
        result = formatter.format("Use `__not_bold__` here")
        assert result == "Use `__not_bold__` here"

    def test_inline_code_then_bold_outside(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """``**bold** and `**not bold**` mixed`` → bold converts, code stays."""
        result = formatter.format("**bold** and `**not bold**`")
        assert result == "*bold* and `**not bold**`"


# --------------------------------------------------------------------------- #
# 6. Tables with varying cell widths
# --------------------------------------------------------------------------- #


class TestTablesVaryingWidths:
    """Markdown tables with short and long cells are padded to column widths."""

    def test_short_and_long_headers(self, formatter: SlackMrkdwnFormatter) -> None:
        """A 2-col table with `Name` and a longer data cell is padded correctly."""
        text = "| Name | Age |\n|------|-----|\n| Bo | 99 |\n| Alexander | 7 |"
        result = formatter.format(text)

        # Must be wrapped in triple backticks.
        assert result.startswith("```\n")
        assert result.endswith("\n```")

        # Strip fences to inspect the body.
        body = result.strip("`").strip()
        lines = body.split("\n")
        # 1 header + 1 separator + 2 data rows = 4 lines.
        assert len(lines) == 4

        # The Name column should be padded to the longest data cell (9 chars:
        # "Alexander"). Header is "Name" (4 chars), so it must be padded with
        # 5 spaces to reach 9 characters.
        header_line = lines[0]
        name_end = header_line.index("Name") + len("Name")
        # The cells are separated by 3 spaces, so "Name" padding ends at the
        # 3-space separator.
        assert header_line[name_end:name_end + 3] == "   "

        # Longest data cell "Alexander" appears unchanged.
        assert "Alexander" in body
        # The Age data is preserved.
        assert "99" in body
        assert "7" in body
        # Separator line uses dashes; with 9-char Name column, the first
        # segment is 9 dashes.
        assert lines[1].startswith("---------")

    def test_three_column_with_mixed_widths(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """A 3-col table where one column is much wider than the others."""
        text = (
            "| K | Description | V |\n"
            "|---|-------------|---|\n"
            "| a | short | 1 |\n"
            "| b | a much longer description | 2 |"
        )
        result = formatter.format(text)

        assert result.startswith("```\n")
        assert result.endswith("\n```")

        body = result.strip("`").strip()
        # All cell text must be present.
        assert "Description" in body
        assert "a much longer description" in body
        assert "K" in body
        assert "V" in body


# --------------------------------------------------------------------------- #
# 7. Links with special characters in the URL
# --------------------------------------------------------------------------- #


class TestLinksWithSpecialChars:
    """URLs containing `?`, `&`, `=`, etc. are preserved in the Slack link."""

    def test_query_string(self, formatter: SlackMrkdwnFormatter) -> None:
        """`?`, `=` and `&` in a URL survive the conversion."""
        text = "[Click here](https://example.com/path?v=1&x=2)"
        result = formatter.format(text)
        assert result == "<https://example.com/path?v=1&x=2|Click here>"

    def test_fragment(self, formatter: SlackMrkdwnFormatter) -> None:
        """`#fragment` in a URL is preserved."""
        text = "[Section](https://example.com/docs#section-1)"
        result = formatter.format(text)
        assert result == "<https://example.com/docs#section-1|Section>"

    def test_link_in_sentence(self, formatter: SlackMrkdwnFormatter) -> None:
        """A complex URL inside a sentence converts correctly."""
        text = "see [the spec](https://example.com/api?version=2&mode=fast) please"
        result = formatter.format(text)
        assert (
            result
            == "see <https://example.com/api?version=2&mode=fast|the spec> please"
        )


# --------------------------------------------------------------------------- #
# 8. Multiple headings in sequence
# --------------------------------------------------------------------------- #


class TestMultipleHeadingsInSequence:
    """H1, H2, H3 stacked in the same text."""

    def test_h1_h2_h3_stack(self, formatter: SlackMrkdwnFormatter) -> None:
        """Three consecutive headings all convert to bold."""
        text = "# First Heading\n## Second Heading\n### Third Heading"
        result = formatter.format(text)
        assert result == "*First Heading*\n*Second Heading*\n*Third Heading*"

    def test_headings_separated_by_paragraph(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """Headings separated by a blank line and a paragraph."""
        text = (
            "# Title\n\n"
            "Some intro text.\n\n"
            "## Subtitle\n\n"
            "More text here."
        )
        result = formatter.format(text)
        assert "*Title*" in result
        assert "*Subtitle*" in result
        # Body text is unchanged.
        assert "Some intro text." in result
        assert "More text here." in result


# --------------------------------------------------------------------------- #
# 9. Bold using `__`
# --------------------------------------------------------------------------- #


class TestUnderscoreBold:
    """`__bold__` should convert to Slack ``*bold*``."""

    def test_standalone_underbold(self, formatter: SlackMrkdwnFormatter) -> None:
        """`__underbold__` → `*underbold*`."""
        assert formatter.format("__underbold__") == "*underbold*"

    def test_underbold_in_sentence(self, formatter: SlackMrkdwnFormatter) -> None:
        """`__underbold__` surrounded by other text converts correctly."""
        result = formatter.format("this is __important__ text")
        assert result == "this is *important* text"

    def test_underbold_with_punctuation(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """`__bold__` next to punctuation still converts."""
        result = formatter.format("(__bold__)")
        assert result == "(*bold*)"


# --------------------------------------------------------------------------- #
# 10. False positives: dunder identifiers
# --------------------------------------------------------------------------- #


# BUG: The ``__bold__`` regex in ``SlackMrkdwnFormatter`` cannot distinguish
# a Markdown ``__word__`` bold span from a Python dunder identifier like
# ``__init__``. The CommonMark spec says ``__word__`` IS bold, but in
# practice LLMs emit Python dunders far more often than ``__bold__`` syntax.
# The tests below are marked xfail to document the bug while keeping the
# suite green; they will xpass (unexpected pass) when the bug is fixed.
_DUNDER_BUG_REASON = (
    "BUG: __init__ and other dunder identifiers are incorrectly converted "
    "to *init* by the __bold__ regex. The regex cannot reliably "
    "distinguish a Python dunder from a Markdown __bold__ span."
)


class TestDunderNotBold:
    """Python dunders like `__init__` must NOT be treated as bold.

    This is a known limitation: the ``__bold__`` heuristic in the formatter
    cannot reliably distinguish between a Markdown bold word and a Python
    dunder identifier. The CommonMark spec says ``__word__`` IS bold, but
    for Slack output, dunder identifiers are far more common in LLM output
    than double-underscore bold.

    These tests assert the desired behavior (preserved) and are marked
    ``xfail`` to document the bug. When the bug is fixed, the xfail markers
    should be removed and the tests will pass.
    """

    @pytest.mark.xfail(
        reason=_DUNDER_BUG_REASON, strict=False, raises=AssertionError
    )
    def test_standalone_dunder_init(self, formatter: SlackMrkdwnFormatter) -> None:
        """Standalone `__init__` must remain `__init__`, not become `*init*`."""
        # EXPECTED (correct) behavior: the dunder is preserved.
        assert formatter.format("__init__") == "__init__"

    @pytest.mark.xfail(
        reason=_DUNDER_BUG_REASON, strict=False, raises=AssertionError
    )
    def test_dunder_in_sentence(self, formatter: SlackMrkdwnFormatter) -> None:
        """`the __init__ method` must keep `__init__` intact."""
        # EXPECTED (correct) behavior: the dunder is preserved.
        assert formatter.format("the __init__ method") == "the __init__ method"

    @pytest.mark.xfail(
        reason=_DUNDER_BUG_REASON, strict=False, raises=AssertionError
    )
    def test_dunder_followed_by_parens(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """`call __init__() now` must keep `__init__` intact.

        The trailing `()` makes the dunder unmistakable.
        """
        # EXPECTED (correct) behavior: the dunder is preserved.
        result = formatter.format("call __init__() now")
        assert result == "call __init__() now"

    @pytest.mark.xfail(
        reason=_DUNDER_BUG_REASON, strict=False, raises=AssertionError
    )
    def test_other_dunders(self, formatter: SlackMrkdwnFormatter) -> None:
        """Other common dunders (`__str__`, `__name__`) must also be preserved."""
        for dunder in ("__str__", "__name__", "__repr__", "__class__"):
            text = f"the {dunder} attribute"
            # EXPECTED (correct) behavior: the dunder is preserved.
            assert formatter.format(text) == text, (
                f"Formatter incorrectly converted dunder: {dunder}"
            )


# --------------------------------------------------------------------------- #
# 11. Empty / near-empty inputs
# --------------------------------------------------------------------------- #


class TestEmptyAndNearEmptyInputs:
    """Empty strings, whitespace, lone delimiters."""

    def test_empty_string(self, formatter: SlackMrkdwnFormatter) -> None:
        """`""` returns `""`."""
        assert formatter.format("") == ""

    def test_whitespace_only(self, formatter: SlackMrkdwnFormatter) -> None:
        """Whitespace-only text is unchanged (no conversions apply)."""
        assert formatter.format("   ") == "   "
        assert formatter.format("\n\n") == "\n\n"
        assert formatter.format("  \t  \n  ") == "  \t  \n  "

    def test_lone_hash(self, formatter: SlackMrkdwnFormatter) -> None:
        """A single `#` is not a heading (heading requires `# word`)."""
        assert formatter.format("#") == "#"

    def test_lone_double_asterisk(self, formatter: SlackMrkdwnFormatter) -> None:
        """`**` alone is not bold (bold requires content between markers)."""
        assert formatter.format("**") == "**"

    def test_lone_double_underscore(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """`__` alone is not bold."""
        assert formatter.format("__") == "__"

    def test_hash_with_space_no_text(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """`# ` (hash + space, no text) is not a heading."""
        # The heading regex requires at least one non-space char of content.
        result = formatter.format("# ")
        # Should NOT produce "*" (no empty bold wrap).
        assert "*" not in result


# --------------------------------------------------------------------------- #
# 12. Realistic LLM output
# --------------------------------------------------------------------------- #


class TestRealisticLLMOutput:
    """A typical LLM response with mixed headings, paragraphs, code, lists, tables, links."""

    def test_full_mixed_message(self, formatter: SlackMrkdwnFormatter) -> None:
        """An LLM-style report converts end-to-end without losing structure."""
        text = (
            "# Project Status\n"
            "\n"
            "The **deployment** was *successful* on 2024-01-15.\n"
            "\n"
            "## Key Metrics\n"
            "\n"
            "| Metric | Value | Change |\n"
            "|--------|-------|--------|\n"
            "| Uptime | 99.9% | +0.1% |\n"
            "| Latency | 45ms | -5ms |\n"
            "\n"
            "### Next Steps\n"
            "\n"
            "1. Monitor the system for *anomalies*\n"
            "2. Update the [runbook](https://docs.example.com/runbook)\n"
            "3. ~~Old step~~ removed\n"
            "\n"
            "See `deployment_log.txt` for details.\n"
        )
        result = formatter.format(text)

        # Headings: all three converted to Slack bold.
        assert "*Project Status*" in result
        assert "*Key Metrics*" in result
        assert "*Next Steps*" in result
        # Bold word: "deployment" → "*deployment*".
        assert "*deployment*" in result
        # Italic: "successful" → "_successful_".
        assert "_successful_" in result
        # Italic inside list: "anomalies" → "_anomalies_".
        assert "_anomalies_" in result
        # Numbered list markers → bullets.
        assert "\u2022 Monitor" in result
        assert "\u2022 Update" in result
        assert "\u2022 ~Old step~" in result
        # Link conversion.
        assert "<https://docs.example.com/runbook|runbook>" in result
        # Strikethrough.
        assert "~Old step~" in result
        # Table: wrapped in code fences with header preserved.
        assert "```" in result
        assert "Metric" in result
        assert "Uptime" in result
        assert "99.9%" in result
        # Inline code preserved.
        assert "`deployment_log.txt`" in result

    def test_realistic_message_paragraph_breaks_preserved(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """The same realistic message preserves paragraph structure (blank lines)."""
        text = (
            "# Project Status\n"
            "\n"
            "The **deployment** was *successful*.\n"
            "\n"
            "## Metrics\n"
        )
        result = formatter.format(text)
        # Blank lines between sections must remain.
        assert "\n\n" in result
        # Original paragraph break count is preserved.
        assert result.count("\n\n") == text.count("\n\n")

    def test_realistic_message_no_lost_content(
        self, formatter: SlackMrkdwnFormatter
    ) -> None:
        """The realistic message does not lose any visible character."""
        text = (
            "# Status\n\n"
            "System is **online**.\n\n"
            "## Notes\n\n"
            "- Item one\n"
            "- Item two with *emphasis*\n"
            "- ~~Old note~~ removed\n\n"
            "See [docs](https://example.com/docs) for more.\n"
        )
        result = formatter.format(text)
        # The union of all visible text tokens must appear in the output.
        for token in (
            "Status",
            "online",
            "Notes",
            "Item one",
            "Item two with",
            "emphasis",
            "Old note",
            "removed",
            "docs",
            "https://example.com/docs",
        ):
            assert token in result, f"Lost token: {token!r}"


# --------------------------------------------------------------------------- #
# End-to-end: blocks.py wraps the formatter output
# --------------------------------------------------------------------------- #


class TestMarkdownToSlackBlocksEdgeCases:
    """The ``markdown_to_slack_blocks`` entry point also handles the edge cases."""

    def test_realistic_message_produces_block_sections(self) -> None:
        """A realistic LLM response yields one or more section blocks."""
        text = (
            "# Status\n\n"
            "System is **online**.\n\n"
            "```python\nprint('hi')\n```\n\n"
            "See [docs](https://example.com) for more.\n"
        )
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) >= 1
        # All blocks are sections of mrkdwn type.
        for block in blocks:
            assert block["type"] == "section"
            assert block["text"]["type"] == "mrkdwn"

    @pytest.mark.xfail(
        reason=_DUNDER_BUG_REASON, strict=False, raises=AssertionError
    )
    def test_dunder_message_through_blocks(self) -> None:
        """`the __init__ method` goes through the blocks path (documents the bug)."""
        text = "the __init__ method"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        # The block's text field shows the (buggy) conversion.
        assert blocks[0]["type"] == "section"
        # EXPECTED (correct) behavior: "__init__" is preserved in the block.
        assert "__init__" in blocks[0]["text"]["text"]
        # The bug: "*init*" appears where "__init__" should be.
        # (This is an informational comment, not a separate assertion.)
