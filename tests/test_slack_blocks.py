"""Tests for Slack Block Kit utilities."""

import pytest
from daemon.sources.adapters.slack.blocks import markdown_to_slack_blocks


class TestMarkdownToSlackBlocks:
    """Tests for markdown_to_slack_blocks function."""

    def test_empty_text_returns_empty_list(self):
        """Empty or whitespace-only text should return empty list."""
        assert markdown_to_slack_blocks("") == []
        assert markdown_to_slack_blocks("   ") == []
        assert markdown_to_slack_blocks(None) == []

    def test_simple_text_conversion(self):
        """Simple text should be wrapped in section block."""
        blocks = markdown_to_slack_blocks("Hello, world!")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        assert blocks[0]["text"]["type"] == "mrkdwn"
        assert blocks[0]["text"]["text"] == "Hello, world!"

    def test_bold_conversion(self):
        """Markdown bold **text** should convert to Slack *text*."""
        blocks = markdown_to_slack_blocks("This is **bold** text")
        assert len(blocks) == 1
        assert "*bold*" in blocks[0]["text"]["text"]

    def test_italic_conversion(self):
        """Markdown italic *text* should convert to Slack _text_."""
        blocks = markdown_to_slack_blocks("This is *italic* text")
        assert len(blocks) == 1
        assert "_italic_" in blocks[0]["text"]["text"]

    def test_link_conversion(self):
        """Markdown links [text](url) should convert to Slack <url|text>."""
        blocks = markdown_to_slack_blocks("Check [this](https://example.com)")
        assert len(blocks) == 1
        assert "<https://example.com|this>" in blocks[0]["text"]["text"]

    def test_bullet_list_conversion(self):
        """Markdown bullet lists should convert to Slack bullets."""
        blocks = markdown_to_slack_blocks("- Item 1\n- Item 2\n- Item 3")
        assert len(blocks) == 1
        text = blocks[0]["text"]["text"]
        assert "• Item 1" in text
        assert "• Item 2" in text
        assert "• Item 3" in text

    def test_numbered_list_conversion(self):
        """Markdown numbered lists should convert to Slack bullets."""
        blocks = markdown_to_slack_blocks("1. First\n2. Second\n3. Third")
        assert len(blocks) == 1
        text = blocks[0]["text"]["text"]
        assert "• First" in text
        assert "• Second" in text
        assert "• Third" in text

    def test_strikethrough_conversion(self):
        """Markdown strikethrough ~~text~~ should convert to Slack ~text~."""
        blocks = markdown_to_slack_blocks("This is ~~deleted~~ text")
        assert len(blocks) == 1
        text = blocks[0]["text"]["text"]
        assert "~deleted~" in text
        # Double tildes should not remain
        assert "~~deleted~~" not in text

    def test_heading_conversion(self):
        """Markdown heading # Heading should convert to Slack *Heading* (bold)."""
        blocks = markdown_to_slack_blocks("# My Heading")
        assert len(blocks) == 1
        text = blocks[0]["text"]["text"]
        assert "*My Heading*" in text
        # The leading '#' should not be present
        assert text.strip().startswith("*")
        assert "# My Heading" not in text

    def test_heading_level_two_conversion(self):
        """Markdown heading ## Sub should convert to Slack *Sub* (bold)."""
        blocks = markdown_to_slack_blocks("## Sub Heading")
        assert len(blocks) == 1
        text = blocks[0]["text"]["text"]
        assert "*Sub Heading*" in text
        assert "## " not in text

    def test_table_conversion(self):
        """Markdown tables should be converted to ASCII art in a code block."""
        table_text = (
            "| Name | Age |\n"
            "|------|-----|\n"
            "| Alice | 30 |\n"
            "| Bob | 25 |"
        )
        blocks = markdown_to_slack_blocks(table_text)
        assert len(blocks) == 1
        text = blocks[0]["text"]["text"]
        # The conversion wraps the table in triple backticks
        assert text.startswith("```")
        assert text.endswith("```")
        # Header cells should be present (without pipe characters)
        assert "Name" in text
        assert "Age" in text
        # Data should be present
        assert "Alice" in text
        assert "Bob" in text
        # Separator row uses dashes
        assert "---" in text


class TestMarkdownToSlackBlocksCodeBlocks:
    """Tests for code block handling in markdown_to_slack_blocks."""

    def test_simple_code_block(self):
        """Simple code block should be wrapped in triple backticks."""
        text = "```\ndef hello():\n    print('world')\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        assert blocks[0]["text"]["text"].startswith("```")
        assert blocks[0]["text"]["text"].endswith("```")

    def test_code_block_with_language_specifier_python(self):
        """Code block with python language specifier should strip the specifier."""
        text = "```python\nprint('hello')\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        # Should NOT contain 'python' as code content
        content = blocks[0]["text"]["text"]
        # The content should start with ``` and end with ```
        assert content.startswith("```")
        assert content.endswith("```")
        # Content between backticks should not have 'python'
        inner = content[3:-3]
        assert "python" not in inner
        assert "print('hello')" in inner

    def test_code_block_with_language_specifier_javascript(self):
        """Code block with js language specifier should strip the specifier."""
        text = "```js\nconsole.log('hello');\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        content = blocks[0]["text"]["text"]
        inner = content[3:-3]
        assert "js" not in inner
        assert "console.log" in inner

    def test_code_block_with_language_specifier_bash(self):
        """Code block with bash language specifier should strip the specifier."""
        text = "```bash\necho 'hello'\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        content = blocks[0]["text"]["text"]
        inner = content[3:-3]
        assert "bash" not in inner
        assert "echo" in inner

    def test_multiline_code_block(self):
        """Multi-line code block should preserve all lines."""
        text = """```
line 1
line 2
line 3
```"""
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        content = blocks[0]["text"]["text"]
        assert "line 1" in content
        assert "line 2" in content
        assert "line 3" in content

    def test_code_block_with_language_specifier_multiline(self):
        """Multi-line code block with language should preserve all lines."""
        text = """```python
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)
```"""
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        content = blocks[0]["text"]["text"]
        inner = content[3:-3]
        # Should not have 'python'
        assert "python" not in inner
        # Should have the code
        assert "def fibonacci" in inner
        assert "fibonacci(n-1)" in inner

    def test_text_before_code_block(self):
        """Text before code block should be separate section."""
        text = "Here is some code:\n```\ncode here\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 2
        # First block is the text
        assert blocks[0]["type"] == "section"
        assert "Here is some code" in blocks[0]["text"]["text"]
        # Second block is the code
        assert blocks[1]["type"] == "section"
        assert "```" in blocks[1]["text"]["text"]

    def test_text_after_code_block(self):
        """Text after code block should be separate section."""
        text = "```\ncode here\n```\nThis is after the code"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 2
        # First block is the code
        assert "```" in blocks[0]["text"]["text"]
        # Second block is the text
        assert blocks[1]["type"] == "section"
        assert "This is after the code" in blocks[1]["text"]["text"]

    def test_text_before_and_after_code_block(self):
        """Text before and after code block should all be separate."""
        text = "Before\n```\ncode\n```\nAfter"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 3
        assert "Before" in blocks[0]["text"]["text"]
        assert "```" in blocks[1]["text"]["text"]
        assert "After" in blocks[2]["text"]["text"]

    def test_multiple_code_blocks(self):
        """Multiple code blocks should each be separate."""
        text = "```\ncode 1\n```\nSome text\n```\ncode 2\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 3
        assert "code 1" in blocks[0]["text"]["text"]
        assert "Some text" in blocks[1]["text"]["text"]
        assert "code 2" in blocks[2]["text"]["text"]

    def test_code_block_with_hash_comment_first_line_preserved(self):
        """Code block whose first line is a `#` comment must keep the comment.

        Regression test: prior to the fix, ``not line.startswith('#')`` was
        treated as a signal to strip the first line, which incorrectly removed
        legitimate comments, shebangs, and `#!` headers.
        """
        text = "```\n# This is a comment\nprint('hello')\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        content = blocks[0]["text"]["text"]
        inner = content[3:-3]
        # The leading comment line must remain intact in the inner content.
        assert "# This is a comment" in inner
        assert "print('hello')" in inner

    def test_code_block_with_shebang_preserved(self):
        """A code block starting with ``#!/bin/bash`` must keep the shebang line.

        ``#!`` looks like a comment but is part of a real shell script; the
        language-specifier heuristic must not strip it.
        """
        text = "```\n#!/bin/bash\necho hello\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        content = blocks[0]["text"]["text"]
        inner = content[3:-3]
        assert "#!/bin/bash" in inner
        assert "echo hello" in inner

    def test_code_block_with_known_language_strips_specifier_only(self):
        """When a known language is given, only the specifier is stripped.

        For ``\\`\\`\\`bash\\n#!/bin/bash\\necho\\n\\`\\`\\`\\``, the specifier
        ``bash`` is stripped, but the shebang on the next line is preserved.
        """
        text = "```bash\n#!/bin/bash\necho hello\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        content = blocks[0]["text"]["text"]
        inner = content[3:-3]
        # The inner content must start with the shebang (so the "bash"
        # specifier is no longer its own first line) and end with echo.
        assert inner.startswith("#!/bin/bash")
        assert inner.endswith("echo hello")
        # The first line on its own must be the shebang, not a "bash" line.
        first_inner_line = inner.split("\n", 1)[0]
        assert first_inner_line == "#!/bin/bash"


class TestMarkdownToSlackBlocksSplitting:
    """Tests for large block splitting."""

    def test_short_text_not_split(self):
        """Text under 3000 chars should not be split."""
        short_text = "x" * 1000
        blocks = markdown_to_slack_blocks(short_text)
        assert len(blocks) == 1
        assert len(blocks[0]["text"]["text"]) == 1000

    def test_code_block_split_by_newlines(self):
        """Large text should be split at newlines when possible."""
        # Create text that's over 3000 chars with newlines
        long_line = "x" * 100
        lines = [long_line for _ in range(50)]  # 50 * 100 = 5000 chars
        text = "\n".join(lines)  # Plus 49 newlines = 5049 total

        blocks = markdown_to_slack_blocks(text)

        # Should be split into multiple blocks
        total_text = "".join(b["text"]["text"] for b in blocks)
        # Total should be preserved (5000 chars + newlines)
        assert len(total_text) >= 5000


class TestMarkdownToSlackBlocksEdgeCases:
    """Tests for edge cases in markdown_to_slack_blocks."""

    def test_inline_code_not_converted(self):
        """Inline code `code` should remain as `code`."""
        blocks = markdown_to_slack_blocks("Use `print()` function")
        assert "`print()`" in blocks[0]["text"]["text"]

    def test_empty_code_block(self):
        """Empty code block should not create empty block."""
        text = "Before\n```\n\n```\nAfter"
        blocks = markdown_to_slack_blocks(text)
        # Should have at least 2 blocks (before and after)
        assert len(blocks) >= 2

    def test_only_code_block(self):
        """Text with only a code block should return that block."""
        text = "```\nonly code\n```"
        blocks = markdown_to_slack_blocks(text)
        assert len(blocks) == 1
        assert "only code" in blocks[0]["text"]["text"]
