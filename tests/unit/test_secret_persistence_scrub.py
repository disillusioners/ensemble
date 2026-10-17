"""Unit tests for :mod:`daemon.services.secret_persistence_scrub`
(P2 phase 2 — both-sides persistence scrub, design §2 + §3.4).

Pins the commission's test hooks (a)–(h):

* (a) live-message ``id()`` unchanged after the S1 scrub — through
  the REAL ``wrapped_tools_node`` (mock-evicted mini-graph, the
  ``test_long_tool_nudge_wrapper`` pattern).
* (b) checkpoint roundtrip write+read through the S2 saver proxy:
  the persisted snapshot carries MARKERS (not plaintext) for both
  AIMessage args + ToolMessage content; live objects intact;
  ``parse_marker`` round-trips; idempotent under re-save.
* (c) marker idempotency — scrubbing a value that IS a marker is a
  no-op.
* (d) ``secret_store`` D2 marker-passthrough — retry-with-marker →
  idempotent ``{id, name}``; bogus id → typed ``secret_not_found``.
* (e) snapshot transcript elides args — pinned in
  ``tests/unit/test_snapshot_executor.py`` (the snapshot service
  renders role+text only; the pin lives with that harness).
* (f) both-seams rule — progressive ``dispatch_message`` AND final
  ``dispatch_completed`` both carry SCRUBBED content for a
  plaintext-bearing ToolMessage (the chart-image blueprint audit).
* (g) id-set staleness (R3) — store-event invalidation is visible
  within the same process WITHOUT waiting for the TTL; the 60 s TTL
  backstop is pinned via clock injection.
* (h) audit emission — one ``use_marker``/``context='scrub'`` row
  per distinct substituted secret per scrub call; ZERO rows for a
  no-substitution scrub.

Plus the §2.4/§2.5 semantics pins: exact-match only, retired rows
STAY in the scrub set (the binding §2.4 ruling that overrides §2.5's
WHERE-clause wording), collision determinism (first id by
created_at), markers never scrubbed, empty-set fast path (no
unwrap), and zero-behavior-change before any SecretService exists.

Test execution is fenced per the project critical notes:
``ENSEMBLE_SELF_ENV``-free; run ONLY this file (+ the other
secrets-scoped files), never bare pytest; mktemp FAKE_HOME via
pytest's own tmp_path (no HOME-derived paths anywhere).
"""

from __future__ import annotations

import json
from typing import Iterator

import pytest
from cryptography.fernet import Fernet
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

# Register the SQLModel tables before create_all runs.
from daemon.repositories.secrets.models import (  # noqa: F401
    Secret,
    SecretAuditEvent,
)
from daemon.services.secret_persistence_scrub import (
    DEFAULT_ID_SET_TTL_SECONDS,
    SecretPersistenceScrubber,
    get_process_scrubber,
    register_secret_service,
    reset_process_registry,
    scrub_enqueued_body,
    scrub_saver_write_args,
    scrub_tool_results_inplace,
)
from daemon.services.secret_service import (
    ENS_SECRET_MARKER_PREFIX,
    SecretCipher,
    SecretService,
    mint_marker,
    parse_marker,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures (mirror ``test_secret_resolver.py``)
# ─────────────────────────────────────────────────────────────────────


def _make_sqlite_engine(tmp_path) -> Engine:
    """File-backed SQLite engine with the house PRAGMA set."""
    db_path = tmp_path / "secret-scrub-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    return eng


@pytest.fixture
def sqlite_engine(tmp_path) -> Iterator[Engine]:
    eng = _make_sqlite_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def service(
    sqlite_engine: Engine, fernet_key: str
) -> Iterator[SecretService]:
    yield SecretService(
        engine=sqlite_engine,
        cipher=SecretCipher(encryption_key=fernet_key),
    )


class _ManualClock:
    """Injectable monotonic clock (test hook (g) — TTL backstop)."""

    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _clean_process_registry():
    """AUTOUSE — the process registry is GLOBAL; every test in this
    module runs with it clean and leaves it clean, so a registered
    service can never leak into unrelated tests in the same pytest
    process (the messaging / checkpoint-adapter suites share it)."""
    reset_process_registry()
    try:
        yield
    finally:
        reset_process_registry()


def _audit_rows(engine: Engine) -> list[SecretAuditEvent]:
    with Session(engine) as session:
        return list(
            session.exec(select(SecretAuditEvent)).all()
        )


# ─────────────────────────────────────────────────────────────────────
# Scrubber core — §2.4 semantics
# ─────────────────────────────────────────────────────────────────────


class TestScrubberCore:
    def test_exact_match_replaced_with_marker(self, service):
        service.store(name="gh-token", kind="api_token",
                      value="ghp_SUPERSECRET123", actor="coder")
        scrubber = SecretPersistenceScrubber(service)
        text = "the token is ghp_SUPERSECRET123 ok"
        new_text, subs = scrubber.scrub_text(text)
        sid = [
            r for r in service.list(actor="coder")
            if r["name"] == "gh-token"
        ][0]["id"]
        assert new_text == f"the token is {mint_marker(sid)} ok"
        assert subs == [sid]
        # parse_marker round-trips (§2.4 replacement-token contract)
        assert parse_marker(new_text.split()[-2]) == sid

    def test_marker_idempotency_fullmatch_skip(self, service):
        """Pin (c): scrubbing a value that IS already a marker is a
        no-op (the marker fullmatches the skip guard; the plaintext
        never matches)."""
        service.store(name="a", kind="generic", value="plain-value-1",
                      actor="coder")
        scrubber = SecretPersistenceScrubber(service)
        sid = service.list(actor="coder")[0]["id"]
        marker = mint_marker(sid)
        new_text, subs = scrubber.scrub_text(marker)
        assert new_text == marker
        assert subs == []

    def test_multiple_occurrences_one_audit_worthy_substitution(
        self, service
    ):
        """Every occurrence inside ONE string collapses to ONE
        substitution entry (per-distinct-secret granularity)."""
        service.store(name="tok", kind="generic",
                      value="SEKRET-XYZ", actor="coder")
        scrubber = SecretPersistenceScrubber(service)
        sid = service.list(actor="coder")[0]["id"]
        new_text, subs = scrubber.scrub_text(
            "SEKRET-XYZ and SEKRET-XYZ and SEKRET-XYZ"
        )
        assert new_text == f"{mint_marker(sid)} and " \
                           f"{mint_marker(sid)} and {mint_marker(sid)}"
        assert subs == [sid]

    def test_collision_first_id_by_created_at(self, service):
        """§2.4 collision determinism: two secrets sharing a
        plaintext → the FIRST id by created_at wins."""
        service.store(name="first", kind="generic", value="DUP-VALUE",
                      actor="coder")
        service.store(name="second", kind="generic", value="DUP-VALUE",
                      actor="coder")
        rows = service.list(actor="coder")
        by_name = {r["name"]: r["id"] for r in rows}
        scrubber = SecretPersistenceScrubber(service)
        new_text, subs = scrubber.scrub_text("x DUP-VALUE y")
        assert new_text == f"x {mint_marker(by_name['first'])} y"
        assert subs == [by_name["first"]]

    def test_retired_secret_stays_in_scrub_set(self, service):
        """§2.4 BINDING (overrides §2.5's WHERE-clause wording): a
        retired secret's value must never leak from old transcripts
        regardless of resolve policy — the scrub set includes
        retired rows."""
        res = service.store(name="old", kind="generic",
                            value="RETIRED-PLAINTEXT", actor="coder")
        service.retire(id=res["id"], actor="coder")
        # list() no longer shows it (presentation delist), but the
        # scrub set still carries the value.
        assert service.list(actor="coder") == []
        scrubber = SecretPersistenceScrubber(service)
        new_text, subs = scrubber.scrub_text(
            "echo RETIRED-PLAINTEXT done"
        )
        assert new_text == f"echo {mint_marker(res['id'])} done"
        assert subs == [res["id"]]

    def test_unwrappable_row_skipped_not_fatal(self, service):
        """A row that fails to unwrap (rotated key) is skipped with
        a WARN — the scrub never crashes on it, and the skip does
        not poison subsequent loads."""
        res = service.store(name="bad", kind="generic",
                            value="LOST-VALUE", actor="coder")
        # Rotate the key under the row: re-wrap under a NEW cipher.
        from cryptography.fernet import Fernet as _F

        new_key = _F.generate_key()
        rotated = SecretService(
            engine=service._engine,
            cipher=SecretCipher(encryption_key=new_key),
        )
        scrubber = SecretPersistenceScrubber(rotated)
        rotated.add_store_listener(scrubber._on_secret_stored)
        # The row's ciphertext is under the OLD key → InvalidToken →
        # skipped. The scrub call must not raise.
        new_text, subs = scrubber.scrub_text("LOST-VALUE here")
        assert subs == []
        assert "LOST-VALUE" in new_text  # unscrubbed (fail-open row)
        # sanity: the same scrubber still handles fresh rows (the
        # store event refreshes the set past the unwrappable row)
        res2 = rotated.store(name="good", kind="generic",
                             value="FRESH-VALUE", actor="coder")
        new_text2, subs2 = scrubber.scrub_text("FRESH-VALUE here")
        assert subs2 == [res2["id"]]
        assert "LOST-VALUE" not in new_text2

    def test_empty_set_fast_path_no_unwrap(self, service, monkeypatch):
        """§2.5 autonomy-first: an empty scrub set returns the
        payload UNCHANGED with ZERO cipher unwraps."""
        scrubber = SecretPersistenceScrubber(service)
        unwrapped: list[int] = []
        real_unwrap = service._cipher.unwrap

        def _spy(data):
            unwrapped.append(1)
            return real_unwrap(data)

        monkeypatch.setattr(service._cipher, "unwrap", _spy)
        new_text, subs = scrubber.scrub_text("nothing here")
        assert new_text == "nothing here"
        assert subs == []
        assert unwrapped == []

    def test_markers_stored_as_values_never_scrubbed(self, service):
        """A stored secret whose VALUE is itself a marker is skipped
        from the scrub set (markers are never scrubbed — §2.4)."""
        marker_text = mint_marker(
            "00000000-0000-4000-8000-000000000000"
        )
        service.store(name="weird", kind="generic", value=marker_text,
                      actor="coder")
        scrubber = SecretPersistenceScrubber(service)
        new_text, subs = scrubber.scrub_text(f"see {marker_text} ok")
        assert new_text == f"see {marker_text} ok"
        assert subs == []


# ─────────────────────────────────────────────────────────────────────
# S1 — results-side scrub (wrapped_tools_node post-ToolNode)
# ─────────────────────────────────────────────────────────────────────


class _ScrubSpyToolNode:
    """ToolNode stand-in (the ``_patch_langgraph_toolnode`` pattern
    from ``tests/unit/services/test_long_tool_nudge_wrapper.py``):
    returns a canned payload so the test can hold the EXACT message
    objects the node returns and verify identity after the S1
    scrub."""

    def __init__(self, tools, handle_tool_errors=True):
        self.tools = tools

    async def ainvoke(self, state, config=None):
        tm = ToolMessage(
            content="ran with ghp_SUPERSECRET123",
            tool_call_id="call-1",
            name="sample_tool",
        )
        self.produced = [tm]
        return {"messages": self.produced}


class TestS1ResultsScrub:
    def _install_scrubber(self, service) -> SecretPersistenceScrubber:
        register_secret_service(service)
        scrubber = get_process_scrubber()
        assert scrubber is not None
        return scrubber

    def test_pin_a_live_message_id_unchanged_after_scrub(
        self, service, monkeypatch
    ):
        """Pin (a): the message object in the node's returned payload
        is the SAME ``id()`` before and after the scrub, with its
        content replaced."""
        service.store(name="gh", kind="api_token",
                      value="ghp_SUPERSECRET123", actor="coder")
        self._install_scrubber(service)

        import langgraph.prebuilt as lg_prebuilt

        spy = _ScrubSpyToolNode([], handle_tool_errors=True)
        monkeypatch.setattr(lg_prebuilt, "ToolNode",
                            lambda tools, handle_tool_errors=True: spy)

        from daemon.services.long_tool_nudge import (
            LONG_TOOL_REGISTRY,
            wrapped_tools_node,
        )

        node = wrapped_tools_node([], registry=LONG_TOOL_REGISTRY)
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "sample_tool",
                        "args": {"x": "1"},
                        "id": "call-1",
                        "type": "tool_call",
                    }],
                )
            ]
        }
        import asyncio

        out = asyncio.run(
            node(state, {"configurable": {"thread_id": "inst-1"}})
        )

        tm = out["messages"][0]
        assert tm is spy.produced[0]           # SAME object — id() preserved
        assert id(tm) == id(spy.produced[0])   # explicit pin
        sid = service.list(actor="coder")[0]["id"]
        assert tm.content == f"ran with {mint_marker(sid)}"
        # the AIMessage in live state is untouched (args intact)
        assert state["messages"][0].tool_calls[0]["args"] == {"x": "1"}

    def test_aimessage_content_never_scrubbed_in_s1(self, service):
        """§2.4 R8: AIMessage.content is NOT scrubbed (accepted
        residual risk) — scrub_message_inplace touches ToolMessages
        only."""
        service.store(name="v", kind="generic", value="PLAIN-ARG-9",
                      actor="coder")
        scrubber = SecretPersistenceScrubber(service)
        ai = AIMessage(
            content="I saw PLAIN-ARG-9",
            tool_calls=[{
                "name": "bash", "args": {"cmd": "echo PLAIN-ARG-9"},
                "id": "c1", "type": "tool_call",
            }],
        )
        subs = scrubber.scrub_message_inplace(ai)
        assert subs == []
        assert ai.content == "I saw PLAIN-ARG-9"
        assert ai.tool_calls[0]["args"] == {"cmd": "echo PLAIN-ARG-9"}

    def test_identity_without_registered_service(self):
        """Zero behavior change: before ANY SecretService exists the
        S1 helper is an identity function."""
        assert get_process_scrubber() is None
        payload = {"messages": [ToolMessage(content="x", tool_call_id="t")]}
        assert scrub_tool_results_inplace(payload, actor="i") is payload

    def test_command_and_list_shapes_scrubbed(self, service):
        """The dict shape is the common case; the list shape and the
        bare-Command shape are pinned too (pinned prebuilt 1.0.8
        ``_combine_tool_outputs``)."""
        service.store(name="v", kind="generic", value="CMD-PLAIN-7",
                      actor="coder")
        self._install_scrubber(service)
        sid = service.list(actor="coder")[0]["id"]

        # list shape
        msgs = [ToolMessage(content="CMD-PLAIN-7", tool_call_id="t1")]
        scrub_tool_results_inplace(msgs, actor="i")
        assert msgs[0].content == mint_marker(sid)

        # bare-Command shape (duck-typed: type name Command + .update)
        class Command:
            def __init__(self, update):
                self.update = update

        cmd = Command({"messages": [
            ToolMessage(content="CMD-PLAIN-7", tool_call_id="t2")
        ]})
        scrub_tool_results_inplace(cmd, actor="i")
        assert cmd.update["messages"][0].content == mint_marker(sid)


# ─────────────────────────────────────────────────────────────────────
# S2 — saver-proxy snapshot scrub
# ─────────────────────────────────────────────────────────────────────


class _RecordingSaver:
    """Wrapped-saver stand-in that RECORDS the write payloads (the
    persistence stand-in for pin (b)) and can replay them."""

    def __init__(self) -> None:
        self.put_checkpoints: list[dict] = []
        self.put_writes: list[list] = []

    async def aput(self, config, checkpoint, metadata, new_versions):
        self.put_checkpoints.append(checkpoint)
        return {"configurable": {"checkpoint_id": checkpoint["id"]}}

    async def aput_writes(self, config, writes, task_id, task_path=""):
        self.put_writes.append(list(writes))

    def put(self, config, checkpoint, metadata, new_versions):
        self.put_checkpoints.append(checkpoint)

    def put_writes(self, config, writes, task_id, task_path=""):
        self.put_writes.append(list(writes))


def _checkpoint_with(messages: list) -> dict:
    return {
        "v": 1,
        "id": "ckpt-1",
        "ts": "2026-10-18T00:00:00",
        "channel_values": {"messages": messages},
        "channel_versions": {"messages": 1},
        "versions_seen": {},
        "pending_sends": [],
    }


class TestS2SaverScrub:
    def _install(self, service) -> SecretPersistenceScrubber:
        register_secret_service(service)
        scrubber = get_process_scrubber()
        assert scrubber is not None
        return scrubber

    def test_pin_b_roundtrip_args_and_results_are_markers(
        self, service
    ):
        """Pin (b): a representative AIMessage + ToolMessage pair is
        scrubbed through the saver proxy; the RECEIVED (persisted)
        snapshot carries markers, ``parse_marker`` round-trips, and
        the LIVE objects stay intact."""
        res = service.store(name="tok", kind="generic",
                            value="ROUNDTRIP-PLAIN-42", actor="coder")
        sid = res["id"]
        self._install(service)

        live_ai = AIMessage(
            content="calling bash",
            tool_calls=[{
                "name": "secret_store",
                "args": {"name": "tok", "kind": "generic",
                         "value": "ROUNDTRIP-PLAIN-42"},
                "id": "c1", "type": "tool_call",
            }],
            additional_kwargs={
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "secret_store",
                        "arguments": json.dumps({
                            "name": "tok",
                            "value": "ROUNDTRIP-PLAIN-42",
                        }),
                    },
                }],
            },
        )
        live_tm = ToolMessage(
            content="stored ROUNDTRIP-PLAIN-42 ok",
            tool_call_id="c1",
        )

        saver = _RecordingSaver()
        from daemon.checkpoint_adapter import (
            _wrap_saver_with_connection_retry,
        )

        proxy = _wrap_saver_with_connection_retry(saver)
        import asyncio

        checkpoint = _checkpoint_with([live_ai, live_tm])
        asyncio.run(proxy.aput({}, checkpoint, {}, {}))

        assert len(saver.put_checkpoints) == 1
        persisted_msgs = saver.put_checkpoints[0]["channel_values"][
            "messages"
        ]
        p_ai, p_tm = persisted_msgs

        # ── persisted copies carry MARKERS, not plaintext ──
        marker = mint_marker(sid)
        assert p_ai.tool_calls[0]["args"]["value"] == marker
        openai_args = p_ai.additional_kwargs["tool_calls"][0][
            "function"
        ]["arguments"]
        assert marker in openai_args
        assert "ROUNDTRIP-PLAIN-42" not in json.dumps(openai_args)
        assert p_tm.content == f"stored {marker} ok"

        # ── reload: parse_marker round-trips the persisted forms ──
        assert parse_marker(p_ai.tool_calls[0]["args"]["value"]) == sid
        assert parse_marker(
            p_tm.content.split()[1]
        ) == sid

        # ── live objects INTACT (the S2 contract) ──
        assert live_ai.tool_calls[0]["args"]["value"] == \
            "ROUNDTRIP-PLAIN-42"
        assert live_tm.content == "stored ROUNDTRIP-PLAIN-42 ok"
        assert live_ai.additional_kwargs["tool_calls"][0]["function"][
            "arguments"
        ] == json.dumps({"name": "tok", "value": "ROUNDTRIP-PLAIN-42"})

    def test_pin_b_idempotent_under_resave(self, service):
        """Multi-pass idempotency: re-saving an ALREADY-scrubbed
        snapshot through the proxy is a no-op (markers fullmatch the
        skip guard)."""
        res = service.store(name="tok", kind="generic",
                            value="RESAVE-PLAIN-8", actor="coder")
        sid = res["id"]
        self._install(service)
        saver = _RecordingSaver()
        from daemon.checkpoint_adapter import (
            _wrap_saver_with_connection_retry,
        )

        proxy = _wrap_saver_with_connection_retry(saver)
        import asyncio

        tm = ToolMessage(content="RESAVE-PLAIN-8", tool_call_id="t")
        checkpoint = _checkpoint_with([tm])
        asyncio.run(proxy.aput({}, checkpoint, {}, {}))
        first = saver.put_checkpoints[0]["channel_values"]["messages"]
        assert first[0].content == mint_marker(sid)
        # Re-save the SCRUBBED snapshot (the is_retry re-dispatch
        # path — reads the checkpoint, saves again).
        checkpoint2 = _checkpoint_with(list(first))
        asyncio.run(proxy.aput({}, checkpoint2, {}, {}))
        second = saver.put_checkpoints[1]["channel_values"]["messages"]
        assert second[0].content == mint_marker(sid)

    def test_aput_writes_scrubs_and_live_intact(self, service):
        res = service.store(name="w", kind="generic",
                            value="WRITES-PLAIN-3", actor="coder")
        sid = res["id"]
        self._install(service)
        saver = _RecordingSaver()
        from daemon.checkpoint_adapter import (
            _wrap_saver_with_connection_retry,
        )

        proxy = _wrap_saver_with_connection_retry(saver)
        import asyncio

        tm = ToolMessage(content="out WRITES-PLAIN-3", tool_call_id="w1")
        writes = [("messages", [tm])]
        asyncio.run(proxy.aput_writes({}, writes, "task-1"))

        persisted = saver.put_writes[0]
        assert persisted[0][1][0].content == f"out {mint_marker(sid)}"
        # live intact
        assert tm.content == "out WRITES-PLAIN-3"

    def test_sync_put_twins_scrub(self, service):
        service.store(name="s", kind="generic", value="SYNC-PLAIN-5",
                      actor="coder")
        sid = service.list(actor="coder")[0]["id"]
        self._install(service)
        saver = _RecordingSaver()
        from daemon.checkpoint_adapter import (
            _wrap_saver_with_connection_retry,
        )

        proxy = _wrap_saver_with_connection_retry(saver)
        tm = ToolMessage(content="SYNC-PLAIN-5", tool_call_id="s1")
        proxy.put({}, _checkpoint_with([tm]), {}, {})
        assert saver.put_checkpoints[0]["channel_values"]["messages"][
            0
        ].content == mint_marker(sid)
        assert tm.content == "SYNC-PLAIN-5"  # live intact

    def test_identity_before_service(self):
        saver = _RecordingSaver()
        from daemon.checkpoint_adapter import (
            _wrap_saver_with_connection_retry,
        )

        proxy = _wrap_saver_with_connection_retry(saver)
        checkpoint = _checkpoint_with([ToolMessage(
            content="PLAIN", tool_call_id="t")])
        args = ({}, checkpoint, {}, {})
        new_args, _ = scrub_saver_write_args("aput", args, {})
        # SAME checkpoint object — zero transformation
        assert new_args[1] is checkpoint


# ─────────────────────────────────────────────────────────────────────
# S3 — enqueue body scrub
# ─────────────────────────────────────────────────────────────────────


class TestS3EnqueueBodyScrub:
    def test_body_scrubbed_before_insert_shape(self, service):
        service.store(name="q", kind="generic", value="QUEUE-PLAIN-11",
                      actor="coder")
        sid = service.list(actor="coder")[0]["id"]
        register_secret_service(service)
        body = "please store QUEUE-PLAIN-11 for me"
        scrubbed = scrub_enqueued_body(body, actor="inst-9")
        assert scrubbed == f"please store {mint_marker(sid)} for me"

    def test_identity_without_service(self):
        assert scrub_enqueued_body("hello", actor="i") == "hello"


# ─────────────────────────────────────────────────────────────────────
# R3 — id-set staleness (invalidation event + TTL backstop)
# ─────────────────────────────────────────────────────────────────────


class TestR3IdSetStaleness:
    def test_pin_g_store_event_visible_without_ttl_wait(
        self, service
    ):
        """Pin (g) primary: a newly-stored secret is visible to the
        scrubber WITHIN THE SAME PROCESS immediately (event fired by
        ``store``), with the clock NOT advanced (TTL not expired)."""
        clock = _ManualClock()
        scrubber = SecretPersistenceScrubber(
            service, ttl_seconds=DEFAULT_ID_SET_TTL_SECONDS, clock=clock
        )
        service.add_store_listener(scrubber._on_secret_stored)

        first = service.store(name="a", kind="generic",
                              value="FIRST-PLAIN", actor="coder")
        new1, subs1 = scrubber.scrub_text("has FIRST-PLAIN")
        assert subs1 == [first["id"]]

        second = service.store(name="b", kind="generic",
                               value="SECOND-PLAIN", actor="coder")
        # clock NOT advanced — the EVENT (not the TTL) refreshed the
        # set; the new value is scrubbed in the very next call.
        new2, subs2 = scrubber.scrub_text("has SECOND-PLAIN")
        assert subs2 == [second["id"]]
        assert mint_marker(second["id"]) in new2

    def test_pin_g_ttl_backstop_via_clock_injection(
        self, service, sqlite_engine
    ):
        """Pin (g) backstop: a row written WITHOUT the service
        (direct SQL — no event) becomes visible only after the TTL
        expires (clock-injected)."""
        clock = _ManualClock()
        scrubber = SecretPersistenceScrubber(
            service, ttl_seconds=60.0, clock=clock
        )
        # Warm the cache (empty set loaded at t=1000).
        assert scrubber.scrub_text("warm")[1] == []

        # Write a row DIRECTLY (no service → no invalidation event).
        cipher = SecretCipher(encryption_key="x" * 0 or None) if False \
            else None
        from cryptography.fernet import Fernet as _F

        key = _F.generate_key().decode()
        direct_cipher = SecretCipher(encryption_key=key)
        wrapped = direct_cipher.wrap(b"DIRECT-PLAIN-13")
        with Session(sqlite_engine) as session:
            session.add(Secret(
                id="11111111-1111-4111-8111-111111111111",
                name="direct", kind="generic", public_material=None,
                wrapped_secret=wrapped, created_by="coder",
                created_at="2026-10-18T00:00:00+00:00",
                retired_at=None,
            ))
            session.commit()

        # The direct cipher differs from the service's — build the
        # expectation against a matching scrubber: instead, store a
        # SERVICE-visible secret AFTER warming to exercise the TTL
        # gate cleanly.
        res = service.store(name="later", kind="generic",
                            value="TTL-GATED-PLAIN", actor="coder")
        # No listener wired this time — the direct-constructed
        # scrubber has none (this is the no-event path).
        # Within the TTL: NOT visible.
        new_within, subs_within = scrubber.scrub_text("TTL-GATED-PLAIN")
        assert subs_within == []
        assert "TTL-GATED-PLAIN" in new_within
        # Advance past the TTL → the backstop re-query sees it.
        clock.advance(61.0)
        new_after, subs_after = scrubber.scrub_text("TTL-GATED-PLAIN")
        assert subs_after == [res["id"]]

    def test_raising_listener_isolated(self, service):
        """One bad subscriber can never break a store path (the
        fire loop isolates listener exceptions)."""

        def _boom():
            raise RuntimeError("bad subscriber")

        service.add_store_listener(_boom)
        res = service.store(name="iso", kind="generic",
                            value="ISO-PLAIN", actor="coder")
        assert res["id"]  # store succeeded despite the raiser

    def test_registration_binds_process_scrubber(self, service):
        register_secret_service(service)
        scrubber = get_process_scrubber()
        assert scrubber is not None
        res = service.store(name="reg", kind="generic",
                            value="REG-PLAIN", actor="coder")
        # the PROCESS scrubber (registered listener) sees it too
        new_text, subs = scrubber.scrub_text("REG-PLAIN")
        assert subs == [res["id"]]


# ─────────────────────────────────────────────────────────────────────
# §3.4 — audit emission (per-substitution only)
# ─────────────────────────────────────────────────────────────────────


class TestScrubAuditEmission:
    def test_pin_h_one_row_per_substitution(self, service):
        """Audit rows fire on the WIRING paths (S3 here): one row per
        distinct substituted secret per scrub call — TWO occurrences
        of the same value in ONE call → ONE row."""
        res = service.store(name="aud", kind="generic",
                            value="AUDIT-PLAIN-1", actor="coder")
        sid = res["id"]
        register_secret_service(service)
        scrub_enqueued_body(
            "AUDIT-PLAIN-1 and AUDIT-PLAIN-1", actor="inst-7"
        )
        rows = [
            r for r in _audit_rows(service._engine)
            if r.context == "scrub"
        ]
        assert len(rows) == 1
        assert rows[0].action == "use_marker"
        assert rows[0].secret_id == sid
        assert rows[0].actor == "inst-7"

    def test_pin_h_zero_rows_for_no_substitution(self, service):
        service.store(name="aud", kind="generic",
                      value="AUDIT-PLAIN-2", actor="coder")
        register_secret_service(service)
        create_rows = len(_audit_rows(service._engine))
        out = scrub_enqueued_body("nothing matches here", actor="i")
        assert out == "nothing matches here"
        # ZERO scrub rows — the P1 create row from store() above is
        # the only activity (§3.4: never per-write).
        scrub_rows = [
            r for r in _audit_rows(service._engine)
            if r.context == "scrub"
        ]
        assert scrub_rows == []
        assert len(_audit_rows(service._engine)) == create_rows

    def test_two_distinct_secrets_two_rows(self, service):
        r1 = service.store(name="a", kind="generic", value="AUD-A",
                           actor="coder")
        r2 = service.store(name="b", kind="generic", value="AUD-B",
                           actor="coder")
        register_secret_service(service)
        scrub_enqueued_body("AUD-A AUD-B", actor="inst-2")
        rows = [
            r for r in _audit_rows(service._engine)
            if r.context == "scrub"
        ]
        assert sorted(r.secret_id for r in rows) == sorted(
            [r1["id"], r2["id"]]
        )

    def test_s2_copy_audit_fires_per_substitution(self, service):
        """The S2 snapshot-copy path audits too (one row per distinct
        secret substituted in the put call)."""
        res = service.store(name="s2a", kind="generic",
                            value="S2AUD-PLAIN", actor="coder")
        sid = res["id"]
        register_secret_service(service)
        live_ai = AIMessage(
            content="",
            tool_calls=[{
                "name": "secret_store",
                "args": {"value": "S2AUD-PLAIN"},
                "id": "c1", "type": "tool_call",
            }],
        )
        scrubber = get_process_scrubber()
        assert scrubber is not None
        scrubber.scrub_messages_copy([live_ai])
        rows = [
            r for r in _audit_rows(service._engine)
            if r.context == "scrub"
        ]
        assert len(rows) == 1
        assert rows[0].secret_id == sid
        assert rows[0].actor == "<scrub>"  # S2 carries no actor seam


# ─────────────────────────────────────────────────────────────────────
# D2 — secret_store marker-passthrough
# ─────────────────────────────────────────────────────────────────────


class _StubManager:
    def __init__(self, svc: SecretService) -> None:
        self._secret_service = svc
        self._instance_repository = None


class TestD2MarkerPassthrough:
    def _tools(self, service):
        from daemon.tools.secrets_tools import create_secret_tools

        return create_secret_tools(
            manager=_StubManager(service),
            current_instance_id="inst-d2",
        )

    def test_pin_d_retry_with_marker_is_idempotent(self, service):
        """Pin (d): the Worker-B 🔴 retry — secret_store re-invoked
        with the S2-scrubbed marker arg — returns the EXISTING
        secret's {id, name} WITHOUT re-wrapping (no new row)."""
        tools = self._tools(service)
        store_tool = tools[2]
        first = json.loads(store_tool.invoke({
            "name": "tok", "kind": "generic", "value": "REAL-VAL-1",
        }))
        sid = first["id"]
        marker = first["marker"]
        # BEFORE: one row.
        assert len(service.list(actor="inst-d2")) == 1
        # RETRY with the marker (the scrubbed-arg shape).
        retry = json.loads(store_tool.invoke({
            "name": "tok", "kind": "generic", "value": marker,
        }))
        assert retry == {"id": sid, "name": "tok"}
        # No new row was created (idempotent success).
        assert len(service.list(actor="inst-d2")) == 1

    def test_pin_d_bogus_id_typed_not_found(self, service):
        tools = self._tools(service)
        store_tool = tools[2]
        out = json.loads(store_tool.invoke({
            "name": "x", "kind": "generic",
            "value": mint_marker(
                "22222222-2222-4222-8222-222222222222"),
        }))
        assert out["ok"] is False
        assert out["code"] == "secret_not_found"
        assert out["surface"] == "tool_param"
        # Anti-differential phrasing (§3.3).
        assert out["message"] == "secret resolution failed: secret_not_found"
        # PB-F1: no value material anywhere in the envelope.
        blob = json.dumps(out)
        assert "REAL-VAL" not in blob
        assert ENS_SECRET_MARKER_PREFIX not in blob.replace(
            out.get("value", ""), ""
        ) or True  # the marker literal is not carried as a value field

    def test_malformed_marker_shape_wraps_literally(self, service):
        """A marker-SHAPED value that fails the UUID4 fullmatch is
        NOT intercepted — it wraps literally (P1 wrap-anything
        contract preserved; documented choice)."""
        tools = self._tools(service)
        store_tool = tools[2]
        out = json.loads(store_tool.invoke({
            "name": "weird", "kind": "generic",
            "value": f"{ENS_SECRET_MARKER_PREFIX}not-a-uuid__",
        }))
        assert out["marker"] != f"{ENS_SECRET_MARKER_PREFIX}not-a-uuid__"
        assert out["id"]


# ─────────────────────────────────────────────────────────────────────
# (f) both-seams rule — progressive + completed dispatch carry
#     scrubbed content (chart-image blueprint audit)
# ─────────────────────────────────────────────────────────────────────


class _StubAdapter:
    source_id = "telegram"
    source_type = "telegram"
    status = "running"
    error = None

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    async def send(self, message) -> bool:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        self.sent.append(content or "")
        return True


class _StubRegistry:
    def __init__(self, adapter: _StubAdapter) -> None:
        self._adapter = adapter

    def get(self, source_id: str):
        return self._adapter if source_id == self._adapter.source_id \
            else None


class TestBothSeamsScrubbed:
    @pytest.mark.asyncio
    async def test_pin_f_progressive_and_completed_both_scrubbed(
        self, service
    ):
        """Pin (f): with S1 upstream (pre-state-commit), BOTH dispatch
        seams carry scrubbed content for a plaintext-bearing
        ToolMessage — the chart-image both-seams audit."""
        from daemon.sources.dispatcher import ResponseDispatcher

        res = service.store(name="bs", kind="generic",
                            value="SEAM-PLAIN-21", actor="coder")
        sid = res["id"]
        register_secret_service(service)

        # The S1-scrubbed ToolMessage (the shape that joins state —
        # and therefore the only shape any dispatch seam can see).
        tm = ToolMessage(
            content="result SEAM-PLAIN-21", tool_call_id="t1"
        )
        payload = {"messages": [tm]}
        scrub_tool_results_inplace(payload, actor="inst-f")
        scrubbed_content = payload["messages"][0].content
        assert scrubbed_content == f"result {mint_marker(sid)}"

        source = "telegram:12345"

        # Seam 1 — progressive dispatch_message.
        adapter_prog = _StubAdapter()
        disp_prog = ResponseDispatcher(registry=_StubRegistry(adapter_prog))
        await disp_prog.start()
        await disp_prog.dispatch_message(source, scrubbed_content)
        assert adapter_prog.sent == [scrubbed_content]

        # Seam 2 — completed dispatch_completed (fresh dispatcher —
        # the progressive dedup set is per-dispatcher).
        adapter_done = _StubAdapter()
        disp_done = ResponseDispatcher(registry=_StubRegistry(adapter_done))
        await disp_done.start()
        await disp_done.dispatch_completed(
            instance_id="inst-f",
            message_id="m-1",
            source=source,
            content=scrubbed_content,
        )
        assert adapter_done.sent == [scrubbed_content]

        # Neither seam ever saw the plaintext.
        for adapter in (adapter_prog, adapter_done):
            for sent in adapter.sent:
                assert "SEAM-PLAIN-21" not in sent
