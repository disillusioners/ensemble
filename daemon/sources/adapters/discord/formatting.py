"""Formatting helpers for the Discord source adapter.

* `_strip_llm_artifact_tags` is a verbatim copy of
  `daemon/sources/adapters/telegram.py:37-71`. Discord renders Markdown
  natively, so LLM-think tags would leak into the user's view unless
  stripped before send.
* `_clean_discord_text` strips Discord mention/embed tokens so the agent
  sees clean user intent instead of `<@123>` / `<#456>` syntax.
"""

from __future__ import annotations

import re


def _strip_llm_artifact_tags(content: str) -> str:
    """Strip LLM thinking/reasoning artifact tags from content.

    Some LLMs output tags like <think>...</think>, <reasoning>...</reasoning>
    which Discord would render as raw text.

    Args:
        content: Raw message content that may contain artifact tags.

    Returns:
        Content with artifact tags removed.
    """
    # Known artifact tag names to strip
    artifact_tags = ["think", "scratchpad", "reflection", "reasoning"]

    for tag in artifact_tags:
        # Remove full blocks with content first (more specific, must come before opening tag removal)
        # Pattern: <tag optional_attrs>content</tag> with case-insensitive matching
        content = re.sub(
            rf"<{tag}(?:\s[^>]*)?>.*?</{tag}\s*>",
            "",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # Remove self-closing tags (e.g., <think/> or <think attr="val" />)
        content = re.sub(rf"<{tag}(?:\s[^>]*)?\s*/>", "", content, flags=re.IGNORECASE)
        # Remove orphan opening tags (e.g., <think> or <think attr="val">)
        content = re.sub(rf"<{tag}(?:\s[^>]*)?>", "", content, flags=re.IGNORECASE)
        # Remove orphan closing tags (e.g., </think>)
        content = re.sub(rf"</{tag}\s*>", "", content, flags=re.IGNORECASE)

    # Clean up empty lines left behind
    content = re.sub(r"\n{3,}", "\n\n", content)

    return content.strip()


# Discord user mention: <@123> or <@!123> (nickname) or <@123|name>
_USER_MENTION_RE = re.compile(r"<@!?\d+(?:\|[^>]+)?>")

# Discord role mention: <@&123>
_ROLE_MENTION_RE = re.compile(r"<@&\d+>")

# Discord channel mention: <#123> or <#123|name>
_CHANNEL_MENTION_RE = re.compile(r"<#\d+(?:\|[^>]+)?>")

# Discord custom emoji / timestamp / slash-command placeholders, kept here for
# completeness — they are stripped on inbound because they're noise for the
# LLM, but preserved on outbound (the bot intends to send them).
_SLASH_COMMAND_RE = re.compile(r"</[a-z_]+:[0-9]+>")
_CUSTOM_EMOJI_RE = re.compile(r"<a?:[a-zA-Z0-9_]+:[0-9]+>")
_TIMESTAMP_RE = re.compile(r"<t:\d+(?::[a-zA-Z])?>")


def _clean_discord_text(text: str) -> str:
    """Strip Discord mention/embed tokens from inbound text.

    Discord formats user mentions as ``<@user_id>`` (or ``<@!user_id>`` for
    nickname mentions and ``<@user_id|display>`` for display overrides),
    role mentions as ``<@&role_id>``, and channel mentions as
    ``<#channel_id>``. These tokens are noise for the LLM and would leak
    into agent context. Pattern mirrors `_clean_message_text` in
    `slack/adapter.py:702-752`.

    Args:
        text: Raw Discord message text.

    Returns:
        Cleaned text with mention tokens removed and whitespace collapsed.
    """
    if not text:
        return text

    text = _USER_MENTION_RE.sub("", text)
    text = _ROLE_MENTION_RE.sub("", text)
    text = _CHANNEL_MENTION_RE.sub("", text)
    text = _SLASH_COMMAND_RE.sub("", text)
    text = _CUSTOM_EMOJI_RE.sub("", text)
    text = _TIMESTAMP_RE.sub("", text)

    # Collapse extra whitespace and trim
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text


def _build_attachment_placeholder(attachments) -> str:
    """Build a human-readable placeholder when message content is empty.

    Args:
        attachments: A list-like of discord.py Attachment objects with a
            ``filename`` attribute.

    Returns:
        ``"[Image attachment: foo.png]"`` for one attachment, or
        ``"[N image attachment(s)]"`` for N>1. Image attachments use the
        ``[Image attachment: ...]`` form; other attachments use
        ``[File attachment: ...]`` / ``[N file attachment(s)]``.
    """
    n = len(attachments)
    if n == 0:
        return ""
    if n == 1:
        first = attachments[0]
        # ``content_type`` may be missing on mocks; default to file semantics.
        ct = getattr(first, "content_type", None) or ""
        if ct.startswith("image/"):
            return f"[Image attachment: {first.filename}]"
        return f"[File attachment: {first.filename}]"
    # Multiple — count images vs files for accuracy
    image_count = sum(
        1 for a in attachments if (getattr(a, "content_type", None) or "").startswith("image/")
    )
    if image_count == n:
        return f"[{n} image attachment(s)]"
    if image_count == 0:
        return f"[{n} file attachment(s)]"
    return f"[{image_count} image / {n - image_count} file attachment(s)]"
