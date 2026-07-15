"""Unit tests for ``daemon.services.skill_meta_parser``.

Covers the two public functions of the meta-tag parser:

* :func:`parse_meta_tag` — strip ``<meta>...</meta>`` blocks from an
  agent message and surface the parsed JSON payload (last valid wins,
  malformed tags stripped silently).
* :func:`extract_load_skill` — pull the ``load_skill`` field out of a
  parsed meta dict, with whitespace stripping and graceful handling
  of missing / non-string / empty values.

The tests are pure-Python string-in / value-out — no DB, no async,
no fixtures. The parser module uses only ``re`` and ``json`` from
the standard library, so the test file stays fast and isolated.
"""

from __future__ import annotations

from daemon.services.skill_meta_parser import (
    extract_load_skill,
    parse_meta_tag,
)


# ============================================================================
# parse_meta_tag — happy paths
# ============================================================================


class TestParseMetaTag:
    """``parse_meta_tag(message)`` returns ``(cleaned, meta)``."""

    def test_basic_parse(self) -> None:
        """Basic: ``<meta>{"load_skill": "unit-test"}</meta>`` extracted."""
        msg = 'run unit tests\n<meta>{"load_skill": "unit-test"}</meta>'
        cleaned, meta = parse_meta_tag(msg)

        assert cleaned == "run unit tests"
        assert meta == {"load_skill": "unit-test"}

    def test_no_meta_tag(self) -> None:
        """No meta tag → returns original text, ``None`` meta."""
        msg = "just a regular message"
        cleaned, meta = parse_meta_tag(msg)

        assert cleaned == msg
        assert meta is None

    def test_nested_json(self) -> None:
        """Nested JSON braces must not truncate early.

        The regex uses ``.*?`` which is non-greedy — a naive
        implementation would stop at the first ``}`` inside the
        payload and leave ``json.loads`` with truncated input → a
        JSONDecodeError → ``meta`` stays ``None``.

        With proper nested-brace handling, parsing succeeds and the
        allow-list filter keeps only the recognized key.
        """
        msg = '<meta>{"load_skill": "unit-test", "opts": {"nested": true}}</meta>'
        cleaned, meta = parse_meta_tag(msg)

        # Parsing succeeded → the regex captured the full object,
        # not a truncated prefix.
        assert meta is not None
        # ``opts`` is filtered by the allow-list; only ``load_skill``
        # survives.
        assert meta == {"load_skill": "unit-test"}
        assert extract_load_skill(meta) == "unit-test"
        # Sanity: tag content fully consumed.
        assert "<meta>" not in cleaned
        assert "</meta>" not in cleaned

    def test_multiline(self) -> None:
        """Multiline JSON inside meta tag — DOTALL flag covers newlines."""
        msg = 'task\n<meta>\n  {"load_skill": "mock-test"}\n</meta>\nmore text'
        cleaned, meta = parse_meta_tag(msg)

        assert extract_load_skill(meta) == "mock-test"
        assert "<meta>" not in cleaned
        assert "</meta>" not in cleaned

    def test_case_insensitive(self) -> None:
        """``<META>`` / ``<Meta>`` tags also match (IGNORECASE)."""
        msg_upper = '<META>{"load_skill": "x"}</META>'
        cleaned, meta = parse_meta_tag(msg_upper)
        assert extract_load_skill(meta) == "x"

        msg_mixed = '<Meta>{"load_skill": "y"}</Meta>'
        cleaned, meta = parse_meta_tag(msg_mixed)
        assert extract_load_skill(meta) == "y"


# ============================================================================
# parse_meta_tag — malformed payloads
# ============================================================================


class TestParseMetaTagMalformed:
    """Malformed JSON inside the tag is stripped, meta stays ``None``."""

    def test_malformed_json(self) -> None:
        """Malformed JSON → tag stripped, ``meta is None``."""
        msg = '<meta>{bad json}</meta>'
        cleaned, meta = parse_meta_tag(msg)

        assert cleaned == ""  # tag stripped, only whitespace remains
        assert meta is None

    def test_non_dict_json(self) -> None:
        """Non-dict JSON (array / scalar) → stripped, ``meta is None``."""
        array_msg = '<meta>["array"]</meta>'
        cleaned, meta = parse_meta_tag(array_msg)
        assert meta is None
        assert "<meta>" not in cleaned

        scalar_msg = '<meta>"just a string"</meta>'
        cleaned, meta = parse_meta_tag(scalar_msg)
        assert meta is None
        assert "<meta>" not in cleaned

    def test_all_tags_stripped_even_malformed(self) -> None:
        """Even malformed tags are removed from the visible message.

        A message can mix one malformed and one valid tag; the
        cleaned message MUST lose both tags, and the parser returns
        the valid tag's payload.
        """
        msg = '<meta>{bad}</meta>\ntask\n<meta>{"load_skill": "ok"}</meta>'
        cleaned, meta = parse_meta_tag(msg)

        assert "<meta>" not in cleaned
        assert "</meta>" not in cleaned
        assert extract_load_skill(meta) == "ok"


# ============================================================================
# parse_meta_tag — multi-tag and cleansing behavior
# ============================================================================


class TestParseMetaTagMultipleTags:
    """Multiple ``<meta>`` tags: last valid wins, all are stripped."""

    def test_multiple_tags_last_wins(self) -> None:
        """Two valid tags → second one wins, both are stripped from text."""
        msg = (
            '<meta>{"load_skill": "first"}</meta>\n'
            'task\n'
            '<meta>{"load_skill": "second"}</meta>'
        )
        cleaned, meta = parse_meta_tag(msg)

        assert extract_load_skill(meta) == "second"
        assert "<meta>" not in cleaned
        assert "</meta>" not in cleaned


class TestParseMetaTagCleansing:
    """Tag stripping and schema filtering behavior."""

    def test_message_cleaned(self) -> None:
        """After parsing, no ``<meta>`` artifacts remain in the message."""
        msg = 'start\n<meta>{"load_skill": "x"}</meta>\nend'
        cleaned, meta = parse_meta_tag(msg)

        assert "<meta>" not in cleaned
        assert "</meta>" not in cleaned
        assert meta == {"load_skill": "x"}

    def test_unknown_keys_ignored(self) -> None:
        """Unknown keys logged + dropped, known keys preserved.

        The parser enforces an allow-list schema; unknown keys must
        not leak into the returned payload even if the JSON is valid.
        """
        msg = '<meta>{"load_skill": "unit-test", "evil": "hack"}</meta>'
        cleaned, meta = parse_meta_tag(msg)

        assert extract_load_skill(meta) == "unit-test"
        assert meta is not None
        assert "evil" not in meta
        # Only the allowed key survives.
        assert set(meta.keys()) == {"load_skill"}


# ============================================================================
# extract_load_skill
# ============================================================================


class TestExtractLoadSkill:
    """``extract_load_skill(meta)`` — defensive reader for ``load_skill``."""

    def test_present(self) -> None:
        """Plain present value is returned unchanged."""
        assert extract_load_skill({"load_skill": "unit-test"}) == "unit-test"

    def test_absent(self) -> None:
        """Dict without the key → ``None``."""
        assert extract_load_skill({"other": "value"}) is None

    def test_none_input(self) -> None:
        """``None`` input → ``None`` (the parser signals no-meta)."""
        assert extract_load_skill(None) is None

    def test_whitespace_stripped(self) -> None:
        """Surrounding whitespace is stripped from the value."""
        assert extract_load_skill({"load_skill": "  unit-test  "}) == "unit-test"

    def test_empty_string(self) -> None:
        """Empty / whitespace-only string → ``None`` (falsy)."""
        assert extract_load_skill({"load_skill": ""}) is None
        assert extract_load_skill({"load_skill": "   "}) is None

    def test_non_string(self) -> None:
        """Non-string values (int, list, None) → ``None``."""
        assert extract_load_skill({"load_skill": 123}) is None
        assert extract_load_skill({"load_skill": ["a"]}) is None
        assert extract_load_skill({"load_skill": None}) is None
