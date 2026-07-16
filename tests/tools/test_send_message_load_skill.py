"""Targeted tests for the optional ``load_skill`` parameter on
``send_message`` in ``daemon/tools/instance.py``.

Branch: feature/send-message-load-skill — sugar change.

Background
----------
This change adds an optional ``load_skill`` kwarg to ``send_message`` for
clean 1:1 skill attribution when delegating work. When the caller passes
``load_skill="<name>"``, the tool appends a ``<meta>{"load_skill":
"<name>"}</meta>`` tag to the message text before enqueueing. The existing
meta-tag parser (daemon/services/skill_meta_parser.py) and injection
pipeline (daemon/services/instance_messaging.py) consume the tag — this
module does NOT touch those files; it only generates the tag string.

Contract under test
-------------------
1. ``load_skill="unit-test"`` ⇒ the message argument passed to
   ``manager.enqueue_message`` has the meta-tag appended.
2. ``load_skill=None`` ⇒ the message argument is unchanged (no ``<meta>``
   anywhere).
3. ``load_skill`` omitted ⇒ same as ``load_skill=None`` (default-param
   backward compatibility).
4. Whitespace-padded skill names are stripped before being emitted into
   the tag.

These tests invoke the real ``send_message`` closure by:
  1. Calling ``create_instance_tools`` with all heavy factory helpers
     patched out (mirrors the pattern in
     ``tests/tools/test_send_message_status_guard.py`` and
     ``tests/tools/test_send_message_task_repo_guard.py``).
  2. Extracting the ``send_message`` tool from the returned list.
  3. Invoking ``tool.coroutine(instance_id, message, load_skill=...)``
     directly to call the underlying async function.

Note: ``send_message`` does not raise; it RETURNS a tool-response string
starting with ``"ERROR:"`` for rejected instances or
``"Message queued ..."`` on success. Verifying the return value (not the
exception) is the correct contract — see the existing
``test_send_message_status_guard.py`` and
``test_send_message_task_repo_guard.py`` tests.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


def _patch_heavy_helpers():
    """Patch the heavy ``create_instance_tools`` factory helpers so only the
    instance-management tools are built (RAG, knowledge, MCP, project, job,
    mother, OpenCode, DB, infra, context all disabled).

    This is a verbatim copy of the helper used in
    ``tests/tools/test_send_message_status_guard.py`` and
    ``tests/tools/test_send_message_task_repo_guard.py``. The duplication is
    intentional — these are unit tests for an unrelated module path, so
    each test file owns its own self-contained factory. Mirroring the
    existing pattern keeps all three files easy to read in isolation.
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
    ``tests/tools/test_send_message_task_repo_guard.py``.
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
    # daemon/tools/instance.py:752 calls ``await manager.enqueue_message(...)``.
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
# Tests
# =============================================================================


class TestSendMessageLoadSkill:
    """Tests for the optional ``load_skill`` parameter on ``send_message``.

    Verifies that the meta-tag sugar is appended exactly when ``load_skill``
    is provided, that the default-param path (no kwarg) is unchanged, and
    that whitespace around the skill name is stripped before being emitted
    into the tag.
    """

    async def test_load_skill_appends_meta_tag(self):
        """``load_skill="unit-test"`` ⇒ the enqueued message includes the
        meta-tag appended after the original text.

        This is the primary happy-path test for the sugar change. The exact
        tag shape — a newline followed by ``<meta>{"load_skill":
        "<name>"}</meta>`` — must match what the existing parser expects
        (see ``daemon/services/skill_meta_parser.py``).
        """
        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "child-instance-001",
            "hello from parent",
            load_skill="unit-test",
        )

        # ``enqueue_message`` was called exactly once (no spurious retries).
        manager.enqueue_message.assert_awaited_once()

        # The message argument passed to ``enqueue_message`` must be the
        # original text with the meta-tag appended, exactly. The exact
        # tag string is part of the contract with the downstream parser.
        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs
        assert _kwargs["message"] == (
            'hello from parent\n<meta>{"load_skill": "unit-test"}</meta>'
        ), f"Unexpected message body: {_kwargs['message']!r}"

        # The result must be the success string, NOT an ERROR — proves the
        # meta-tag sugar did not perturb the downstream guards (status /
        # in-progress / task-repo).
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert result.startswith("Message queued"), (
            f"Success path expected; got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"load_skill must not introduce an ERROR; got: {result!r}"
        )

    async def test_load_skill_none_omits_meta_tag(self):
        """``load_skill=None`` ⇒ the message argument is exactly the
        original text, with NO ``<meta>`` anywhere.

        This is the explicit-None backward-compatibility test. The
        parameter is optional and ``None`` must be a no-op, even if the
        caller passes the kwarg explicitly.
        """
        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "child-instance-002",
            "hello from parent",
            load_skill=None,
        )

        # ``enqueue_message`` was called exactly once.
        manager.enqueue_message.assert_awaited_once()

        # The message argument must be the original text, untouched, with
        # no ``<meta>`` tag anywhere in the body. This is the strict
        # backward-compatibility guarantee for ``load_skill=None``.
        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs
        assert _kwargs["message"] == "hello from parent", (
            f"load_skill=None must leave the message untouched; "
            f"got: {_kwargs['message']!r}"
        )
        assert "<meta>" not in _kwargs["message"], (
            f"No <meta> tag should be present when load_skill=None; "
            f"got: {_kwargs['message']!r}"
        )

        # Success path.
        assert isinstance(result, str)
        assert result.startswith("Message queued"), (
            f"load_skill=None should hit the success path; got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"load_skill=None must not introduce an ERROR; got: {result!r}"
        )

    async def test_load_skill_default_param_omits_meta_tag(self):
        """Omitting the ``load_skill`` kwarg entirely ⇒ the message
        argument is unchanged.

        Mirrors ``test_load_skill_none_omits_meta_tag`` but exercises the
        default-parameter path (the kwarg is never passed). This proves
        the ``= None`` default on the function signature produces the same
        behavior as explicitly passing ``None``.
        """
        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        # Note: NO ``load_skill`` kwarg.
        result = await send_message.coroutine(
            "child-instance-003",
            "hello from parent",
        )

        manager.enqueue_message.assert_awaited_once()

        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs
        assert _kwargs["message"] == "hello from parent", (
            f"Default-param path must leave the message untouched; "
            f"got: {_kwargs['message']!r}"
        )
        assert "<meta>" not in _kwargs["message"], (
            f"No <meta> tag should be present when load_skill is omitted; "
            f"got: {_kwargs['message']!r}"
        )

        # Success path.
        assert isinstance(result, str)
        assert result.startswith("Message queued"), (
            f"Default-param path should hit the success path; got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"Default-param path must not introduce an ERROR; got: {result!r}"
        )

    async def test_load_skill_strips_whitespace(self):
        """Whitespace around the skill name is stripped before being
        emitted into the meta-tag.

        The implementation guards with ``str(load_skill).strip()`` so
        callers can pass ``"  unit-test  "`` without breaking the JSON
        payload inside the ``<meta>`` tag (leading/trailing whitespace
        would make the JSON non-canonical and may break parsers).
        """
        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "child-instance-004",
            "hello from parent",
            load_skill="  unit-test  ",
        )

        manager.enqueue_message.assert_awaited_once()

        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs
        # The exact meta-tag shape, with whitespace stripped from the skill
        # name. The surrounding whitespace inside the JSON quotes must be
        # gone — only the bare skill name remains.
        assert '<meta>{"load_skill": "unit-test"}</meta>' in _kwargs["message"], (
            f"Whitespace around skill name must be stripped; "
            f"got: {_kwargs['message']!r}"
        )
        # And no leading/trailing whitespace leaked into the JSON value.
        assert '"  unit-test  "' not in _kwargs["message"], (
            f"Skill name in JSON payload must be stripped; "
            f"got: {_kwargs['message']!r}"
        )

        # Success path.
        assert isinstance(result, str)
        assert result.startswith("Message queued"), (
            f"Whitespace-padded load_skill should hit the success path; "
            f"got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"Whitespace-padded load_skill must not introduce an ERROR; "
            f"got: {result!r}"
        )

    async def test_load_skill_empty_string_is_no_op(self):
        """``load_skill=""`` (empty string) is a no-op — no meta-tag
        appended.

        The implementation guards with ``str(load_skill).strip()`` and only
        appends the meta-tag when the stripped value is truthy. An empty
        string strips to ``""`` which is falsy, so the guard must short-
        circuit and leave the message body untouched. This guarantees that
        callers can safely pass ``""`` (e.g., from a config that may
        default to empty) without producing a malformed meta-tag.
        """
        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        result = await send_message.coroutine(
            "child-instance-005",
            "hello from parent",
            load_skill="",
        )

        # ``enqueue_message`` was called exactly once (no spurious retries).
        manager.enqueue_message.assert_awaited_once()

        # The message argument must be the original text, untouched, with
        # no ``<meta>`` tag anywhere. An empty-string ``load_skill`` is
        # treated identically to ``None`` — the ``.strip()`` guard catches it.
        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs
        assert _kwargs["message"] == "hello from parent", (
            f"load_skill='' must leave the message untouched; "
            f"got: {_kwargs['message']!r}"
        )
        assert "<meta>" not in _kwargs["message"], (
            f"No <meta> tag should be present when load_skill=''; "
            f"got: {_kwargs['message']!r}"
        )

        # Success path.
        assert isinstance(result, str)
        assert result.startswith("Message queued"), (
            f"load_skill='' should hit the success path; got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"load_skill='' must not introduce an ERROR; got: {result!r}"
        )

    async def test_load_skill_with_quote_is_valid_json(self):
        """A skill name containing a special character (e.g., a double
        quote) is emitted as properly-escaped JSON inside the meta-tag.

        This is the regression test for the JSON-escaping defect: the
        previous f-string interpolation produced
        ``<meta>{"load_skill": "has"quote"}</meta>`` for the input
        ``"has\"quote"``, which is malformed JSON and would have been
        silently dropped by the meta-tag parser. After the fix using
        ``json.dumps()``, the payload must be valid JSON that decodes back
        to the original skill name (and no raw quote characters leak into
        the tag unescaped).
        """
        manager = _make_manager(status="idle")
        send_message = _get_send_message_tool(manager)

        # A skill name containing a literal double quote. The naive
        # f-string would produce ``{"load_skill": "has"quote"}`` which is
        # not valid JSON (un-escaped quote inside string value). With
        # ``json.dumps()``, the quote is escaped to ``\"``.
        weird_skill = 'has"quote'
        result = await send_message.coroutine(
            "child-instance-006",
            "hello from parent",
            load_skill=weird_skill,
        )

        # ``enqueue_message`` was called exactly once.
        manager.enqueue_message.assert_awaited_once()

        _call = manager.enqueue_message.await_args
        assert _call is not None
        _kwargs = _call.kwargs
        message_body: str = _kwargs["message"]

        # The message body must contain a meta-tag (the guard is not
        # tripped — ``str('has"quote').strip()`` is truthy).
        assert message_body.startswith("hello from parent\n<meta>"), (
            f"Meta-tag must be appended after a newline; "
            f"got: {message_body!r}"
        )
        assert message_body.endswith("</meta>"), (
            f"Meta-tag must be terminated; got: {message_body!r}"
        )

        # Extract the JSON payload between the tags and verify it is
        # valid JSON. Use ``json.loads`` to prove the parser can decode
        # it — this is the exact downstream behavior we are guarding.
        _tag_open = "<meta>"
        _tag_close = "</meta>"
        _open_idx = message_body.index(_tag_open)
        _close_idx = message_body.rindex(_tag_close)
        _payload = message_body[_open_idx + len(_tag_open):_close_idx]

        # The payload MUST be valid JSON; this is the regression assertion.
        _parsed = json.loads(_payload)
        assert _parsed == {"load_skill": weird_skill}, (
            f"Parsed JSON payload must round-trip the original skill name; "
            f"got: {_parsed!r}"
        )

        # No un-escaped raw quote leaks between the ``<meta>`` and
        # ``</meta>`` markers. ``json.dumps`` escapes ``"`` to ``\"`` so
        # the literal sequence ``"has"quote"`` must not appear inside
        # the tag body — only the escaped form does.
        assert '"has"quote"' not in _payload, (
            f"Un-escaped quote leaked into meta-tag payload; "
            f"got: {_payload!r}"
        )
        # The escaped form (backslash + quote) IS present — proves the
        # escaping was actually applied.
        assert r'\"' in _payload, (
            f"Expected escaped quote in JSON payload; got: {_payload!r}"
        )

        # Success path.
        assert isinstance(result, str)
        assert result.startswith("Message queued"), (
            f"load_skill with quote should hit the success path; "
            f"got: {result!r}"
        )
        assert not result.startswith("ERROR"), (
            f"load_skill with quote must not introduce an ERROR; "
            f"got: {result!r}"
        )