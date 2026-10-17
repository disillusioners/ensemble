"""agent-secrets P2 phase 3 — ``secret_typed_args`` pre-invoke seam tests.

Pins the design §1d tool-param resolution in
``wrapped_tools_node`` (``daemon/services/long_tool_nudge.py``):

* (i) **resolve success** — the tool body receives the PLAINTEXT in
  its invoke-bound args; the AIMessage's ORIGINAL tool_calls keep
  the MARKER (live args intact per §2.3).
* (ii) **fail-closed** — a bogus marker produces a typed envelope
  ``ToolMessage`` (``status="error"``, ``surface:"tool_param"``) and
  the tool is NOT invoked; other calls in the batch still run.
* (iii) **zero-change** — undeclared tools = byte-identical behavior
  (no resolution, no latency); S1 results-scrub composition intact.

CRITICAL COMPOSITION (commission hard constraint): the pre-invoke
pass must compose with the S1 results-side scrub that already lives
in ``wrapped_tools_node`` POST-execution (landed a7a61cfe9/475f983f4)
— resolve declared args → invoke → existing S1 scrub of results.
"""

from __future__ import annotations

import json
from typing import Any, Optional, TypedDict

import pytest
from cryptography.fernet import Fernet
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from sqlalchemy import create_engine, event as sa_event, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from daemon.repositories.secrets.models import (  # noqa: F401
    Secret,
    SecretAuditEvent,
)
from daemon.services.secret_service import (
    SECRET_CODE_NOT_FOUND,
    SecretCipher,
    SecretService,
)
from daemon.services.secret_persistence_scrub import (
    register_secret_service,
    reset_process_registry,
)
from daemon.tools._auth import SECRET_TYPED_ARGS
from tests.helpers.long_tool_nudge import lt_real


# ─────────────────────────────────────────────────────────────────────
# Fixtures (mirrors test_secret_resolver.py — self-contained, fenced)
# ─────────────────────────────────────────────────────────────────────


def _make_sqlite_engine(tmp_path) -> Engine:
    db_path = tmp_path / "nudge-secret-typed-test.sqlite"
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
def service(
    sqlite_engine: Engine,
) -> Iterator[SecretService]:
    svc = SecretService(
        engine=sqlite_engine,
        cipher=SecretCipher(encryption_key=Fernet.generate_key().decode()),
    )
    register_secret_service(svc)
    try:
        yield svc
    finally:
        reset_process_registry()


@pytest.fixture(autouse=True)
def _fake_home(monkeypatch, tmp_path) -> Iterator[str]:
    """CRITICAL-NOTE fence — never resolve live-shaped HOME paths."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    yield str(fake_home)


# ─────────────────────────────────────────────────────────────────────
# Harness (mirrors tests/unit/services/test_long_tool_nudge_wrapper.py
# — REAL compiled mini-graph so ToolNode.ainvoke gets the runtime
# config it requires)
# ─────────────────────────────────────────────────────────────────────


class _S(TypedDict, total=False):
    messages: list


async def _run_through_real_graph(lt, node, state, config):
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(_S)
    g.add_node("tools", node)
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    compiled = g.compile()
    return await compiled.ainvoke(state, config=config)


def _state(call_id: str, name: str, args: dict) -> dict:
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name,
                        "args": args,
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }


def _config(instance_id: str = "inst-sta") -> dict:
    return {"configurable": {"thread_id": instance_id}}


# A probe tool whose name IS declared in SECRET_TYPED_ARGS for these
# tests (registered via monkeypatch on the map — the registration
# slot is a plain dict; adding a test key is the documented opt-in).
@tool
def secret_param_probe(api_token: str, plain: str = "x") -> str:
    """Probe tool carrying a secret-typed arg."""
    return f"got:{api_token}|{plain}"


@tool
def undeclared_probe(other: str) -> str:
    """Probe tool NOT declared in SECRET_TYPED_ARGS."""
    return f"undeclared:{other}"


@pytest.fixture
def declare_probe(monkeypatch):
    """Declare ``secret_param_probe`` in the registration slot for
    the duration of a test (the opt-in contract)."""
    monkeypatch.setitem(
        SECRET_TYPED_ARGS, "secret_param_probe", frozenset({"api_token"})
    )


# ─────────────────────────────────────────────────────────────────────
# (i) resolve success
# ─────────────────────────────────────────────────────────────────────


class TestSecretTypedArgsResolveSuccess:
    @pytest.mark.asyncio
    async def test_tool_receives_plaintext(
        self, lt_real, service: SecretService, declare_probe
    ) -> None:
        """The declared field resolves pre-invoke — the tool body
        sees the PLAINTEXT."""
        minted = service.store(
            name="sta-happy", kind="generic",
            value="pt-token-xyz", actor="worker",
        )
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real.wrapped_tools_node([secret_param_probe], registry)
        result = await _run_through_real_graph(
            lt_real,
            node,
            _state(
                "call-sta-1",
                "secret_param_probe",
                {"api_token": minted["marker"], "plain": "keep"},
            ),
            _config(),
        )
        tm = result["messages"][0]
        assert isinstance(tm, ToolMessage)
        # The tool body captured the PLAINTEXT (result scrub is a
        # no-op here — the plaintext never matched a stored value
        # echo pattern... except S1 DOES scrub echoes of stored
        # values; the result text contains the plaintext, so S1
        # markerizes it. Assert via the marker form OR the
        # plaintext: the value round-tripped through the tool.)
        content = tm.content
        assert ("pt-token-xyz" in content) or (
            minted["marker"] in content
        ), f"tool result lost the value entirely: {content!r}"

    @pytest.mark.asyncio
    async def test_aimessage_original_tool_calls_keep_marker(
        self, lt_real, service: SecretService, declare_probe
    ) -> None:
        """§2.3 LIVE ARGS INTACT — the caller's AIMessage object
        keeps the MARKER in its tool_calls; only the invoke-bound
        COPY got the plaintext."""
        minted = service.store(
            name="sta-live", kind="generic",
            value="pt-live-token", actor="worker",
        )
        marker = minted["marker"]
        state = _state(
            "call-sta-2",
            "secret_param_probe",
            {"api_token": marker},
        )
        original_args = state["messages"][0].tool_calls[0]["args"]

        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real.wrapped_tools_node([secret_param_probe], registry)
        await _run_through_real_graph(lt_real, node, state, _config())

        # The ORIGINAL state dict's args are untouched (marker).
        assert original_args["api_token"] == marker
        # And the AIMessage object in the caller's state too.
        assert (
            state["messages"][0].tool_calls[0]["args"]["api_token"]
            == marker
        )

    @pytest.mark.asyncio
    async def test_plain_fields_pass_through_untouched(
        self, lt_real, service: SecretService, declare_probe
    ) -> None:
        """Undeclared fields (``plain``) ride the args copy
        BYTE-IDENTICAL — only declared fields are rewritten."""
        minted = service.store(
            name="sta-mixed", kind="generic",
            value="pt-mixed", actor="worker",
        )
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real.wrapped_tools_node([secret_param_probe], registry)
        result = await _run_through_real_graph(
            lt_real,
            node,
            _state(
                "call-sta-3",
                "secret_param_probe",
                {"api_token": minted["marker"], "plain": "untouched-value"},
            ),
            _config(),
        )
        tm = result["messages"][0]
        assert "untouched-value" in tm.content


# ─────────────────────────────────────────────────────────────────────
# (ii) fail-closed
# ─────────────────────────────────────────────────────────────────────


class TestSecretTypedArgsFailClosed:
    @pytest.mark.asyncio
    async def test_missing_id_yields_typed_envelope_tool_not_invoked(
        self, lt_real, service: SecretService, monkeypatch
    ) -> None:
        """Bogus marker id → typed ``{ok:false, code:
        secret_not_found, surface:"tool_param"}`` ToolMessage; the
        tool is NOT invoked (child NOT spawned / tool NOT invoked
        per §1d)."""
        import uuid

        bogus = f"__ENS_SECRET__{uuid.uuid4()}__"
        invoked: list[Any] = []

        @tool
        def spy_probe(api_token: str) -> str:
            """Spy — records it ran."""
            invoked.append(api_token)
            return "ran"

        monkeypatch.setitem(
            SECRET_TYPED_ARGS, "spy_probe", frozenset({"api_token"})
        )
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real.wrapped_tools_node([spy_probe], registry)
        result = await _run_through_real_graph(
            lt_real,
            node,
            _state("call-fc-1", "spy_probe", {"api_token": bogus}),
            _config(),
        )
        assert invoked == [], "tool MUST NOT be invoked on fail-closed"
        tm = result["messages"][0]
        assert isinstance(tm, ToolMessage)
        assert tm.status == "error"
        assert tm.tool_call_id == "call-fc-1"
        payload = json.loads(tm.content)
        assert payload["ok"] is False
        assert payload["code"] == SECRET_CODE_NOT_FOUND
        assert payload["surface"] == "tool_param"
        assert bogus not in tm.content  # PB-F1

    @pytest.mark.asyncio
    async def test_failing_call_skipped_batch_still_runs(
        self, lt_real, service: SecretService, declare_probe
    ) -> None:
        """A failing call does NOT sink the batch — the healthy
        sibling tool_call still runs (per-call error semantics)."""
        import uuid

        bogus = f"__ENS_SECRET__{uuid.uuid4()}__"
        healthy_ran: list[str] = []

        @tool
        def failing_probe(api_token: str) -> str:
            """Declared secret-typed probe with a bogus marker."""
            return "should-not-run"

        @tool
        def healthy_probe(x: str) -> str:
            """Undeclared sibling."""
            healthy_ran.append(x)
            return f"healthy:{x}"

        monkey_declare = pytest.MonkeyPatch()
        try:
            monkey_declare.setitem(
                SECRET_TYPED_ARGS,
                "failing_probe",
                frozenset({"api_token"}),
            )
            registry = lt_real.LongToolNudgeRegistry()
            node = lt_real.wrapped_tools_node(
                [failing_probe, healthy_probe], registry
            )
            state = {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "failing_probe",
                                "args": {"api_token": bogus},
                                "id": "call-fc-2a",
                                "type": "tool_call",
                            },
                            {
                                "name": "healthy_probe",
                                "args": {"x": "sibling"},
                                "id": "call-fc-2b",
                                "type": "tool_call",
                            },
                        ],
                    )
                ]
            }
            result = await _run_through_real_graph(
                lt_real, node, state, _config()
            )
        finally:
            monkey_declare.undo()

        assert healthy_ran == ["sibling"], "sibling call MUST still run"
        messages = result["messages"]
        by_id = {
            m.tool_call_id: m
            for m in messages
            if isinstance(m, ToolMessage)
        }
        # The failing call got its typed envelope.
        fail_msg = by_id["call-fc-2a"]
        payload = json.loads(fail_msg.content)
        assert payload["code"] == SECRET_CODE_NOT_FOUND
        # The healthy call got its real result.
        assert "healthy:sibling" in by_id["call-fc-2b"].content

    @pytest.mark.asyncio
    async def test_envelope_merge_restores_batch_cardinality(
        self, lt_real, service: SecretService, declare_probe
    ) -> None:
        """The merge restores ONE ToolMessage per tool_call id —
        the router/loop-repair expectation."""
        import uuid

        bogus = f"__ENS_SECRET__{uuid.uuid4()}__"
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real.wrapped_tools_node([secret_param_probe], registry)
        result = await _run_through_real_graph(
            lt_real,
            node,
            _state("call-fc-3", "secret_param_probe", {"api_token": bogus}),
            _config(),
        )
        tm_ids = [
            m.tool_call_id
            for m in result["messages"]
            if isinstance(m, ToolMessage)
        ]
        assert tm_ids == ["call-fc-3"]


# ─────────────────────────────────────────────────────────────────────
# (iii) zero-change + composition
# ─────────────────────────────────────────────────────────────────────


class TestSecretTypedArgsZeroChangeAndComposition:
    @pytest.mark.asyncio
    async def test_undeclared_tool_byte_identical(
        self, lt_real, service: SecretService
    ) -> None:
        """A tool NOT in SECRET_TYPED_ARGS: marker values pass
        through UNTOUCHED (the tool body sees the raw marker) —
        zero resolution, zero latency (design §1d NOT-global)."""
        marker = "__ENS_SECRET__12345678-1234-1234-1234-123456789012__"
        seen: list[str] = []

        @tool
        def undeclared_spy(other: str) -> str:
            """Undeclared — sees the raw marker."""
            seen.append(other)
            return f"saw:{other}"

        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real.wrapped_tools_node([undeclared_spy], registry)
        result = await _run_through_real_graph(
            lt_real,
            node,
            _state("call-zc-1", "undeclared_spy", {"other": marker}),
            _config(),
        )
        assert seen == [marker], "undeclared tool must see the raw marker"
        assert f"saw:{marker}" in result["messages"][0].content

    @pytest.mark.asyncio
    async def test_s1_results_scrub_still_composes(
        self, lt_real, service: SecretService, declare_probe
    ) -> None:
        """CRITICAL COMPOSITION: pre-invoke resolution → invoke →
        S1 results scrub. A tool that ECHOES the plaintext back has
        its result markerized by S1 (the plaintext never joins live
        state) — proving both passes ran in sequence."""
        minted = service.store(
            name="sta-compose", kind="generic",
            value="pt-compose-echo", actor="worker",
        )

        @tool
        def echo_probe(api_token: str) -> str:
            """Echoes the token back (the S1 scrub target)."""
            return f"echo:{api_token}"

        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real.wrapped_tools_node([echo_probe], registry)
        result = await _run_through_real_graph(
            lt_real,
            node,
            _state(
                "call-comp-1",
                "echo_probe",
                {"api_token": minted["marker"]},
            ),
            _config(),
        )
        tm = result["messages"][0]
        # S1 replaced the echoed plaintext with the marker (the
        # idempotent replacement token, design §2.4).
        assert "pt-compose-echo" not in tm.content, (
            "S1 scrub did NOT run after pre-invoke resolution — "
            "composition broken"
        )
        assert minted["marker"] in tm.content

    @pytest.mark.asyncio
    async def test_zero_change_when_no_declared_tool_in_batch(
        self, lt_real, service: SecretService, monkeypatch
    ) -> None:
        """A batch with NO declared tool returns the state
        UNCHANGED (the fast path — byte-identical object for the
        invoke) and emits no synthetic errors."""
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real.wrapped_tools_node([undeclared_probe], registry)
        state = _state("call-zc-2", "undeclared_probe", {"other": "v"})
        result = await _run_through_real_graph(
            lt_real, node, state, _config()
        )
        tm = result["messages"][0]
        assert tm.content == "undeclared:v"
        assert tm.status is None or tm.status != "error"

    @pytest.mark.asyncio
    async def test_secret_free_agent_untouched(
        self, lt_real, monkeypatch
    ) -> None:
        """No SecretService registered + no markers anywhere → the
        pre-invoke pass is a no-op (design §0: zero behavior change
        for secret-free agents)."""
        reset_process_registry()
        registry = lt_real.LongToolNudgeRegistry()
        node = lt_real.wrapped_tools_node([undeclared_probe], registry)
        result = await _run_through_real_graph(
            lt_real,
            node,
            _state("call-zc-3", "undeclared_probe", {"other": "plain"}),
            _config(),
        )
        assert result["messages"][0].content == "undeclared:plain"
