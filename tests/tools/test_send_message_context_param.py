"""Targeted tests for the optional ``context`` parameter on
``send_message`` and the ``_format_task_context()`` helper in
``daemon/tools/instance.py``.

Branch: feature/context-param-send-message — context threading.

Background
----------
This change adds an optional ``context`` kwarg to ``send_message`` so callers
can attach structured context (file lists, notes, plan refs, conventions)
alongside the free-form ``message`` text. When provided as a non-empty dict,
the tool:

1. Formats the dict into a ``[SYSTEM CONTEXT: Task Context]`` markdown block
   via ``_format_task_context`` (``daemon/tools/instance.py:47``).
2. Threads the formatted text through ``enqueue_message`` via
   ``metadata={"task_context": <text>}`` (line 1613).
3. The downstream injection pipeline (``_process_message_with_tracking``)
   reads the metadata and prepends a synthetic ``HumanMessage`` to the
   recipient's context, BEFORE the actual task message.

Contract under test
-------------------
**Part A — ``_format_task_context()``** (pure function, no manager):

A1. Typical dict (list + string values) → header, bulleted list, text block,
    blank lines between sections.
A2. Empty dict → ONLY the header line ``[SYSTEM CONTEXT: Task Context]``.
A3. ``None`` → function raises ``AttributeError`` (function expects a dict;
    the ``send_message`` tool MUST guard against ``None`` upstream; that
    guard is verified by Part B test B2).
A4. Non-string scalar values (int, float, bool) → ``str(value)`` rendering.
A5. Nested dict values → ``str(value)`` rendering (no recursion).
A6. Keys with special characters → title-case header via
    ``key.replace("_", " ").title()``.
A7. Values with embedded newlines / tabs / angle brackets → verbatim
    passthrough (no escaping).
A8. Multiple keys → insertion order preserved (Python 3.7+ dict guarantee).
A9. Single-element list value → single bullet (no special-casing).
A10. Unicode/emoji values → UTF-8 verbatim passthrough.

**Part B — ``send_message(context=...)``** tool flow:

B1. ``context={"key": "value"}`` ⇒ ``metadata["task_context"]`` contains
    the formatted block.
B2. ``context=None`` ⇒ ``metadata`` is ``None`` (no ``task_context`` key).
B3. ``context={}`` ⇒ ``metadata`` is ``None`` (empty dict → falsy guard).
B4. ``context={"a": "b"}, load_skill="unit-test"`` ⇒ both kwargs work
    together: ``metadata`` has ``task_context`` AND the message body has
    the ``<meta>{"load_skill": "unit-test"}</meta>`` tag appended.

These tests invoke the real ``send_message`` closure by:
  1. Calling ``create_instance_tools`` with all heavy factory helpers
     patched out (mirrors the pattern in
     ``tests/tools/test_send_message_load_skill.py``).
  2. Extracting the ``send_message`` tool from the returned list.
  3. Invoking ``tool.coroutine(instance_id, message, context=...)`` directly
     to call the underlying async function.

Note: ``send_message`` does not raise; it RETURNS a tool-response string
starting with ``"ERROR:"`` for rejected instances or
``"Message queued ..."`` on success. Verifying the return value (not the
exception) is the correct contract — see the existing
``test_send_message_status_guard.py`` and
``test_send_message_task_repo_guard.py`` tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# =============================================================================
# Test helpers (verbatim copy of the helpers in test_send_message_load_skill.py
# so this file is self-contained).
# =============================================================================


def _patch_heavy_helpers():
    """Patch the heavy ``create_instance_tools`` factory helpers so only the
    instance-management tools are built (RAG, knowledge, MCP, project, job,
    mother, OpenCode, DB, infra, context all disabled).

    This is a verbatim copy of the helper used in
    ``tests/tools/test_send_message_load_skill.py``,
    ``tests/tools/test_send_message_status_guard.py``, and
    ``tests/tools/test_send_message_task_repo_guard.py``. The duplication is
    intentional — these are unit tests for an unrelated module path, so
    each test file owns its own self-contained factory.
    """
    from unittest.mock import patch

    return [
        patch("daemon.tools.instance.is_rag_enabled", return_value=False),
        patch("daemon.tools.instance.create_rag_tools", return_value=[]),
        patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
        patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_project_tools", return_value=[]),
        patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]),
        patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]),
        patch("daemon.tools.instance.create_project_history_tools", return_value=[]),
        patch("daemon.tools.instance.create_opencode_tools", return_value=[]),
        patch("daemon.tools.instance.create_db_tools", return_value=[]),
        patch("daemon.tools.instance.create_infra_tools", return_value=[]),
        patch("daemon.tools.instance.create_context_tools", return_value=[]),
        patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
        patch("daemon.tools.instance.scan_tools_for_full_docs"),
        patch("daemon.tools.instance._apply_tool_filter", side_effect=lambda tools, *a, **kw: tools),
    ]


def _make_manager(*, status: str = "idle") -> MagicMock:
    """Build a mock manager wired for the ``send_message`` happy path.

    Defaults to ``status="idle"`` so the status guard does not fire, and
    wires up ``task_repo`` (``MagicMock`` with ``get_by_message`` returning
    a MagicMock with ``.id = 42``) so the success path runs all the way
    through. This is modeled after the helpers in
    ``tests/tools/test_send_message_status_guard.py`` and
    ``tests/tools/test_send_message_load_skill.py``.
    """
    manager = MagicMock()

    # _resolve_instance_id calls get_instance (async) and find_near_instance.
    async def _get_instance(instance_id):
        return MagicMock(instance_id=instance_id)

    manager.get_instance = _get_instance
    manager.find_near_instance = MagicMock(return_value=[])  # no fuzzy matches

    # Status guard reads status from get_instance_info.
    manager.get_instance_info = MagicMock(return_value={"status": status})

    # Live-instance path: no in-flight messages, enqueue succeeds.
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    # ``send_message`` dispatches via ``enqueue_message`` (NOT
    # ``enqueue_message_job``). Production ``send_message`` at
    # daemon/tools/instance.py:1609 calls ``await manager.enqueue_message(...)``.
    # Awaiting a plain ``MagicMock`` raises
    # ``TypeError: object MagicMock can't be used in 'await' expression``,
    # so this attribute must be an ``AsyncMock``.
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )
    # ``enqueue_message_job`` is the public/external path (POST /messages,
    # chat adapters, scheduler) and is NOT called by ``send_message``.
    # Kept as a MagicMock so any straggling read doesn't accidentally
    # invoke the real implementation, but NOT asserted against.
    manager.enqueue_message_job = MagicMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )

    # Production code touches these for the post-enqueue path.
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    manager._live_hub = MagicMock()

    # Wire ``_task_repo`` so the success path runs to completion (otherwise
    # the function returns an explicit ERROR before any assertions can run).
    # The tool only reads ``child_task.id``, so a MagicMock with ``.id = 42``
    # is sufficient. Use ``object.__setattr__`` to bypass MagicMock's
    # auto-attr so the production ``getattr(manager, "_task_repo", None)``
    # sees the wired-up repo.
    child_task = MagicMock()
    child_task.id = 42
    task_repo = MagicMock()
    task_repo.get_by_message = MagicMock(return_value=child_task)
    object.__setattr__(manager, "_task_repo", task_repo)
    return manager


def _get_send_message_tool(manager: MagicMock):
    """Build the instance tools and return the ``send_message`` tool object.

    The tool object exposes a ``.coroutine`` attribute that is the actual
    async function decorated by ``@tool``. Invoking it directly bypasses
    Pydantic schema validation (we already know our inputs are valid).
    """
    from daemon.tools.instance import create_instance_tools

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(manager, "parent-instance", "developer")
    finally:
        for p in reversed(patches):
            p.stop()

    # Find the send_message tool by name.
    for t in tools:
        if getattr(t, "name", None) == "send_message":
            return t
    raise RuntimeError(
        "send_message tool not found in create_instance_tools output; "
        f"got {[getattr(t, 'name', None) for t in tools]}"
    )


# =============================================================================
# Part A: _format_task_context() function tests
# =============================================================================


class TestFormatTaskContext:
    """Unit tests for the pure ``_format_task_context`` helper.

    The function is at module scope in ``daemon/tools/instance.py:47`` and
    has no side effects — every test exercises it directly without any
    manager / repo / asyncio machinery.
    """

    def test_typical_dict_files_and_notes(self):
        """A1: Typical dict with a list value and a string value.

        Input: ``{"files": ["a.py:1-10", "b.py:20-30"], "notes": "root cause is X"}``
        Output contract:
          * Header ``[SYSTEM CONTEXT: Task Context]`` is the first line.
          * List values render as ``- <item>`` bullets.
          * String values render verbatim.
          * Each key becomes a ``## <Title Case>`` header.
          * Blank line separates sections.
          * Trailing blank line is acceptable (matches the production
            ``\\n.join(lines)`` shape).
        """
        from daemon.tools.instance import _format_task_context

        result = _format_task_context(
            {
                "files": ["a.py:1-10", "b.py:20-30"],
                "notes": "root cause is X",
            }
        )

        # Header is the first line, exactly.
        assert result.startswith("[SYSTEM CONTEXT: Task Context]"), (
            f"Header must be the first line; got: {result!r}"
        )

        # Each key gets a title-case ``## `` header.
        assert "## Files" in result, f"Expected '## Files' header; got: {result!r}"
        assert "## Notes" in result, f"Expected '## Notes' header; got: {result!r}"

        # List values are emitted as ``- <item>`` bullets in order.
        assert "- a.py:1-10" in result, (
            f"Expected first bullet; got: {result!r}"
        )
        assert "- b.py:20-30" in result, (
            f"Expected second bullet; got: {result!r}"
        )
        # The list must be bulleted — the raw string value should NOT appear
        # as a free-floating line outside the bullets.
        assert "a.py:1-10\n" in result, (
            f"Expected bulleted line to end with newline; got: {result!r}"
        )

        # String values are emitted verbatim.
        assert "root cause is X" in result, (
            f"Expected verbatim notes value; got: {result!r}"
        )

        # Blank line between sections. The two sections are separated by an
        # empty line (i.e. ``\n\n``).
        assert "\n\n## Notes" in result, (
            f"Expected blank line between Files and Notes sections; "
            f"got: {result!r}"
        )

    def test_empty_dict_returns_only_header(self):
        """A2: Empty dict → ONLY the header line.

        An empty dict means the caller explicitly passed ``{}`` (or an empty
        expression that evaluates to ``{}``). The ``send_message`` tool
        short-circuits on empty dicts (B3) so this code path is not normally
        reached in production, but the function itself must still produce a
        well-formed block — just the header, no extra sections.
        """
        from daemon.tools.instance import _format_task_context

        result = _format_task_context({})

        # The output must be EXACTLY the header line — no trailing
        # whitespace, no extra sections.
        assert result == "[SYSTEM CONTEXT: Task Context]", (
            f"Empty dict must return ONLY the header line; got: {result!r}"
        )

        # And there must be no headers or bullets at all.
        assert "##" not in result, (
            f"Empty dict must not emit any section headers; got: {result!r}"
        )
        assert "- " not in result, (
            f"Empty dict must not emit any bullets; got: {result!r}"
        )

    def test_none_raises_attribute_error_documented_contract(self):
        """A3: ``None`` raises ``AttributeError`` — function expects a dict.

        The function does NOT guard against ``None`` (its signature says
        ``dict[str, Any]``); ``None.items()`` raises ``AttributeError``. The
        ``send_message`` tool is responsible for guarding — see the upstream
        guard at ``daemon/tools/instance.py:1569``:
            ``if context is not None and isinstance(context, dict) and context:``

        This test documents the contract: the function itself does NOT
        tolerate ``None``; the caller MUST guard. Part B test B2 verifies
        the caller actually does guard.
        """
        from daemon.tools.instance import _format_task_context

        with pytest.raises(AttributeError) as exc_info:
            _format_task_context(None)  # type: ignore[arg-type]

        # The failure is on ``None.items()`` — the AttributeError message
        # will reference ``items``. We do not assert the exact message
        # (it is implementation-defined), only that the failure mode is
        # AttributeError, proving the function is dict-only.
        assert exc_info.value is not None
        assert "items" in str(exc_info.value) or "'NoneType' object" in str(
            exc_info.value
        ), (
            f"Expected AttributeError referencing items or NoneType; "
            f"got: {exc_info.value!r}"
        )

    def test_non_string_scalar_values_uses_str_conversion(self):
        """A4: Non-string scalar values (int, float, bool) are rendered via
        ``str(value)``.

        The function's contract is:
          * ``isinstance(value, list)`` → bulleted
          * ``isinstance(value, str)`` → verbatim
          * else → ``str(value)``

        This test exercises the else branch with int, float, and bool.
        """
        from daemon.tools.instance import _format_task_context

        result = _format_task_context(
            {"count": 42, "ratio": 3.14, "enabled": True}
        )

        # Header present.
        assert result.startswith("[SYSTEM CONTEXT: Task Context]"), (
            f"Header must be present; got: {result!r}"
        )

        # Each scalar is rendered via ``str()``.
        assert "## Count" in result, f"Expected '## Count' header; got: {result!r}"
        assert "42" in result, f"Expected '42' for int value; got: {result!r}"
        assert "## Ratio" in result, f"Expected '## Ratio' header; got: {result!r}"
        assert "3.14" in result, f"Expected '3.14' for float; got: {result!r}"
        assert "## Enabled" in result, (
            f"Expected '## Enabled' header; got: {result!r}"
        )
        # ``str(True)`` is ``"True"`` — verify the bool is stringified.
        assert "True" in result, f"Expected 'True' for bool value; got: {result!r}"

        # ``str(True)`` must NOT appear as ``"- True"`` (a list bullet) — the
        # else branch uses ``lines.append(str(value))``, not the bulleted
        # branch. This is the discriminator that proves the else branch
        # fired and not the list branch.
        assert "- True" not in result, (
            f"Bool value must NOT be rendered as a bullet; got: {result!r}"
        )
        # Likewise for int / float.
        assert "- 42" not in result, (
            f"Int value must NOT be rendered as a bullet; got: {result!r}"
        )
        assert "- 3.14" not in result, (
            f"Float value must NOT be rendered as a bullet; got: {result!r}"
        )

    def test_nested_dict_value_uses_str_conversion(self):
        """A5: Nested dicts are NOT recursively formatted — they fall
        through to the ``str(value)`` else branch.

        The function has no recursion. A dict value is rendered via
        Python's default ``str(dict)`` (which uses single quotes and the
        ``{...}`` repr shape). The test pins down the EXACT rendering so
        a future change to recursion would surface here as a deliberate
        contract change.
        """
        from daemon.tools.instance import _format_task_context

        result = _format_task_context({"config": {"nested": True}})

        assert result.startswith("[SYSTEM CONTEXT: Task Context]"), (
            f"Header must be present; got: {result!r}"
        )
        assert "## Config" in result, (
            f"Expected '## Config' header; got: {result!r}"
        )
        # ``str({"nested": True})`` is exactly ``"{'nested': True}"`` in
        # CPython — the assertion is intentionally literal so a switch to
        # recursion (which would emit ``## Nested`` + ``True``) would fail.
        assert "{'nested': True}" in result, (
            f"Nested dict must be rendered via str(); got: {result!r}"
        )
        # No recursion: a nested ``## Nested`` header must NOT appear.
        assert "## Nested" not in result, (
            f"Nested dict must NOT be recursed; got: {result!r}"
        )

    def test_special_characters_in_keys(self):
        """A6: Keys with underscores / slashes / mixed case are converted
        via ``key.replace("_", " ").title()``.

        The contract:
          * underscores are replaced with spaces BEFORE title-casing, so
            ``"key_with_underscore"`` becomes ``"Key With Underscore"``.
          * ``str.title()`` treats non-alphabetic characters (e.g. ``/``)
            as word boundaries, so ``"path/to_file"`` becomes
            ``"Path/To_File"`` after the underscore-replacement step.
        """
        from daemon.tools.instance import _format_task_context

        result = _format_task_context(
            {
                "key_with_underscore": "v1",
                "path/to_file": "v2",
                "MixedCaseKey": "v3",
            }
        )

        # Underscores become spaces, then title-cased.
        assert "## Key With Underscore" in result, (
            f"Underscores must be replaced with spaces; got: {result!r}"
        )
        # The function does ``key.replace("_", " ").title()`` — the
        # underscore is replaced with a space BEFORE title-casing, so the
        # space persists in the output. ``"path/to_file"`` becomes
        # ``"Path/To File"`` (space, not underscore) because the original
        # underscore is gone by the time ``.title()`` runs.
        assert "## Path/To File" in result, (
            f"Slash must be a word boundary; underscore is replaced with "
            f"space before title(); got: {result!r}"
        )
        # MixedCase → each word boundary is capitalized. ``MixedCaseKey``
        # becomes ``"Mixedcasekey"`` after title-casing (the boundary
        # detection in CPython's ``str.title()`` is purely
        # case-transition-based, so camelCase collapses to one word).
        # We assert the exact behavior so a future change to the
        # key-conversion function (e.g. switching to ``inflection`` or
        # custom logic) surfaces here as a contract change.
        assert "## Mixedcasekey" in result, (
            f"MixedCaseKey must title-case to 'Mixedcasekey' (single word "
            f"per CPython's title() semantics); got: {result!r}"
        )

    def test_special_characters_in_values_verbatim(self):
        """A7: Values with embedded newlines / tabs / angle brackets are
        passed through verbatim — no escaping, no markdown normalization.

        The function does no value transformation beyond:
          * list → bullets
          * str → verbatim
          * else → ``str(value)``

        Newlines, tabs, and ``<`` / ``>`` are preserved literally. The
        downstream markdown renderer is responsible for any escape
        decisions; ``_format_task_context`` is a pure formatter.
        """
        from daemon.tools.instance import _format_task_context

        raw_value = "line1\nline2\t<>"
        result = _format_task_context({"data": raw_value})

        assert "## Data" in result, f"Expected '## Data' header; got: {result!r}"
        # The value must appear verbatim — same newline, same tab, same
        # angle brackets, with no extra escaping (e.g. no ``\\n``,
        # no ``&lt;``).
        assert "line1\nline2\t<>" in result, (
            f"Special characters must be passed through verbatim; "
            f"got: {result!r}"
        )
        # And no spurious escaping leaked in.
        assert "line1\\n" not in result, (
            f"Newline must not be escaped; got: {result!r}"
        )
        assert "&lt;" not in result, (
            f"Angle bracket must not be HTML-escaped; got: {result!r}"
        )

    def test_multiple_keys_preserve_insertion_order(self):
        """A8: Multiple keys preserve dict insertion order (Python 3.7+).

        ``_format_task_context`` iterates ``context.items()`` directly,
        so the output sections appear in the same order as the input
        dict. CPython 3.7+ guarantees dict insertion order; this test
        pins the contract so a future refactor (e.g. to ``sorted()``)
        fails loudly.
        """
        from daemon.tools.instance import _format_task_context

        # Deliberately non-alphabetic key order.
        result = _format_task_context(
            {
                "zebra": "z",
                "alpha": "a",
                "mango": "m",
            }
        )

        # Find the position of each header in the output and assert the
        # ordering matches the dict insertion order (zebra, alpha, mango),
        # NOT alphabetical order.
        pos_zebra = result.index("## Zebra")
        pos_alpha = result.index("## Alpha")
        pos_mango = result.index("## Mango")

        assert pos_zebra < pos_alpha < pos_mango, (
            f"Insertion order must be preserved (zebra < alpha < mango); "
            f"positions: zebra={pos_zebra}, alpha={pos_alpha}, "
            f"mango={pos_mango}; full result: {result!r}"
        )

    def test_single_element_list_value(self):
        """A9: Single-element list renders as a single bullet.

        No special-casing for length-1 lists — the same ``for item in
        value:`` loop runs and emits exactly one ``- <item>`` line.
        """
        from daemon.tools.instance import _format_task_context

        result = _format_task_context({"only_file": ["a.py:1-5"]})

        assert "## Only File" in result, (
            f"Expected '## Only File' header; got: {result!r}"
        )
        assert "- a.py:1-5" in result, (
            f"Expected single bullet for single-element list; got: {result!r}"
        )
        # And no other bullets.
        assert result.count("- ") == 1, (
            f"Expected exactly one bullet; got: {result!r}"
        )

    def test_unicode_and_emoji_values(self):
        """A10: Unicode and emoji values pass through verbatim.

        The function does no encoding / decoding — the result is whatever
        Python's ``str`` concatenation produces, which is UTF-8-ready
        text. Emoji, accented Latin characters, and CJK ideographs must
        all round-trip without corruption.
        """
        from daemon.tools.instance import _format_task_context

        result = _format_task_context(
            {"emoji": "🚀 \u00e9", "cjk": "\u6d4b\u8bd5"}
        )

        assert "## Emoji" in result, f"Expected '## Emoji' header; got: {result!r}"
        assert "## Cjk" in result, f"Expected '## Cjk' header; got: {result!r}"

        # Rocket emoji, é (U+00E9), and 测试 (U+6D4B U+8BD5) all present,
        # byte-for-byte, in the output.
        assert "\U0001f680" in result, (
            f"Rocket emoji must round-trip; got: {result!r}"
        )
        assert "\u00e9" in result, (
            f"Accented Latin char must round-trip; got: {result!r}"
        )
        assert "\u6d4b\u8bd5" in result, (
            f"CJK ideographs must round-trip; got: {result!r}"
        )


# =============================================================================
# Part B: send_message(context=...) tool flow tests
# =============================================================================


class TestSendMessageContextParam:
    """Tool-level flow tests for the ``context`` parameter on ``send_message``.

    These tests exercise the real ``send_message`` closure with a mock
    manager and assert that the ``metadata`` kwarg passed to
    ``manager.enqueue_message`` is shaped correctly for each branch:
      * non-empty dict → ``metadata={"task_context": <formatted>}``
      * ``None`` → ``metadata=None``
      * ``{}`` → ``metadata=None``
      * combined with ``load_skill`` → BOTH kwargs work together
    """

    async def test_context_dict_populates_task_context_metadata(self):
        """B1: ``context={"key": "value"}`` ⇒ ``metadata["task_context"]``
        contains the formatted block.

        The exact formatted text is asserted by re-running
        ``_format_task_context`` and comparing — the tool's contract is
        that the metadata key is the literal output of that helper.
        """
        from daemon.tools.instance import _format_task_context

        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        context = {"key": "value", "files": ["a.py:1-10"]}
        expected_task_context = _format_task_context(context)

        result = await send_message.coroutine(
            "child-instance-001",
            "do the thing",
            context=context,
        )

        # ``enqueue_message`` was called exactly once.
        manager.enqueue_message.assert_awaited_once()

        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs

        # The original message must be passed through verbatim — ``context``
        # is orthogonal to the message body.
        assert _kwargs["message"] == "do the thing", (
            f"Original message must be untouched; got: {_kwargs['message']!r}"
        )
        # No ``<meta>`` tag in this test (we did not pass ``load_skill``).
        assert "<meta>" not in _kwargs["message"], (
            f"No <meta> tag should appear without load_skill; "
            f"got: {_kwargs['message']!r}"
        )

        # The metadata kwarg must be a dict (not None) and must contain
        # ``task_context`` with the exact formatted block.
        assert _kwargs["metadata"] is not None, (
            f"metadata must not be None when context is non-empty; "
            f"got: {_kwargs['metadata']!r}"
        )
        assert "task_context" in _kwargs["metadata"], (
            f"metadata must contain 'task_context' key; "
            f"got: {list(_kwargs['metadata'].keys())!r}"
        )
        assert _kwargs["metadata"]["task_context"] == expected_task_context, (
            f"task_context must match _format_task_context output; "
            f"expected: {expected_task_context!r}; "
            f"got: {_kwargs['metadata']['task_context']!r}"
        )

        # And the formatted block starts with the contract header.
        assert _kwargs["metadata"]["task_context"].startswith(
            "[SYSTEM CONTEXT: Task Context]"
        ), (
            f"task_context must start with the system-context header; "
            f"got: {_kwargs['metadata']['task_context']!r}"
        )

        # Success path — proves the metadata-threading did not perturb
        # the downstream guards (status / in-progress / task-repo).
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.startswith("Message queued"), (
            f"Success path expected; got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"context kwarg must not introduce an ERROR; got: {result!r}"
        )

    async def test_context_none_yields_none_metadata(self):
        """B2: ``context=None`` ⇒ ``metadata`` is ``None``.

        The upstream guard at ``daemon/tools/instance.py:1569``
        (``if context is not None and isinstance(context, dict) and context``)
        short-circuits, so ``task_context_text`` stays ``None`` and the
        ``if task_context_text else None`` ternary on line 1613 returns
        ``None`` for ``metadata``. This is the strict backward-compat
        contract for callers that never pass ``context``.
        """
        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "child-instance-002",
            "do the thing",
            context=None,
        )

        manager.enqueue_message.assert_awaited_once()

        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs

        # The original message is unchanged.
        assert _kwargs["message"] == "do the thing", (
            f"Original message must be untouched; got: {_kwargs['message']!r}"
        )

        # metadata MUST be None — no ``task_context`` key anywhere.
        assert _kwargs["metadata"] is None, (
            f"context=None must produce metadata=None; got: {_kwargs['metadata']!r}"
        )

        # Success path.
        assert isinstance(result, str)
        assert result.startswith("Message queued"), (
            f"context=None should hit the success path; got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"context=None must not introduce an ERROR; got: {result!r}"
        )

    async def test_context_empty_dict_yields_none_metadata(self):
        """B3: ``context={}`` ⇒ ``metadata`` is ``None``.

        The truthy guard (``and context``) treats an empty dict as
        "no context" and short-circuits before calling
        ``_format_task_context``. Same observable result as ``context=None``:
        ``metadata=None``, message body untouched, no ``task_context`` key
        in the enqueue call.
        """
        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "child-instance-003",
            "do the thing",
            context={},
        )

        manager.enqueue_message.assert_awaited_once()

        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs

        # The original message is unchanged.
        assert _kwargs["message"] == "do the thing", (
            f"Original message must be untouched; got: {_kwargs['message']!r}"
        )

        # metadata MUST be None — empty dict is treated as no context.
        assert _kwargs["metadata"] is None, (
            f"context={{}} must produce metadata=None; got: {_kwargs['metadata']!r}"
        )

        # Success path.
        assert isinstance(result, str)
        assert result.startswith("Message queued"), (
            f"context={{}} should hit the success path; got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"context={{}} must not introduce an ERROR; got: {result!r}"
        )

    async def test_context_and_load_skill_work_together(self):
        """B4: ``context={"a": "b"}, load_skill="unit-test"`` ⇒ BOTH work.

        The two sugar kwargs are orthogonal:
          * ``load_skill`` mutates the message body (appends a ``<meta>``
            tag).
          * ``context`` flows through ``metadata`` (sets
            ``task_context`` key).

        A regression in one path must not silently break the other. This
        test asserts both observations in a single ``enqueue_message``
        call to prove the two features compose correctly.
        """
        from daemon.tools.instance import _format_task_context

        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        context = {"a": "b", "files": ["x.py"]}
        expected_task_context = _format_task_context(context)

        result = await send_message.coroutine(
            "child-instance-004",
            "do the thing",
            load_skill="unit-test",
            context=context,
        )

        manager.enqueue_message.assert_awaited_once()

        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs

        # ``load_skill`` path: the message body has the meta-tag appended.
        assert _kwargs["message"] == (
            'do the thing\n<meta>{"load_skill": "unit-test"}</meta>'
        ), (
            f"load_skill must append meta-tag; got: {_kwargs['message']!r}"
        )

        # ``context`` path: the metadata carries the formatted block.
        assert _kwargs["metadata"] is not None, (
            f"context must populate metadata; got: {_kwargs['metadata']!r}"
        )
        assert _kwargs["metadata"]["task_context"] == expected_task_context, (
            f"task_context must match _format_task_context output; "
            f"expected: {expected_task_context!r}; "
            f"got: {_kwargs['metadata']['task_context']!r}"
        )

        # The two features must NOT cross-contaminate: the meta-tag must
        # be in the message body, not in the task_context text, and
        # vice versa.
        assert "<meta>" not in _kwargs["metadata"]["task_context"], (
            f"task_context must not contain <meta> tag; "
            f"got: {_kwargs['metadata']['task_context']!r}"
        )
        assert "[SYSTEM CONTEXT: Task Context]" not in _kwargs["message"], (
            f"Message body must not contain the system-context header; "
            f"got: {_kwargs['message']!r}"
        )

        # Success path.
        assert isinstance(result, str)
        assert result.startswith("Message queued"), (
            f"Combined kwargs should hit the success path; got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"Combined kwargs must not introduce an ERROR; got: {result!r}"
        )
