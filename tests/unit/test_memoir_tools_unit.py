"""The memoir tools — behaviors, the refusal matrix, the gate order.

Phase 4 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase4-plan.md`` AC1/AC5/AC7;
pack ``test/packs/memoir_tools_unit_test.sh``).

What this suite pins:

* the AUTH-FIRST gate order (D4.2/AM-16): a non-ari/blank caller is
  refused with ``memoir_agent_unknown`` / ``memoir_not_authorized``
  BEFORE the knob is read and BEFORE any DB touch — a probe manager
  counts knob reads and engine touches so the order is proven
  BEHAVIORALLY, not just by source indices;
* the kill-switch refusal at call time (D4.16) NAMING the variable;
* the typed refusal matrix: every reachable field code surfaces with the
  exact envelope ``Error: {tool} REFUSED — reason={code}: {message}``;
* the behaviors (AC5): keepsake-at-write enforces reason+actor+admission;
  the cap rejection names the pins; ``memoir_pin`` re-pins with a note;
  ``memoir_unpin`` on a non-keepsake is an idempotent no-op and clears
  the WHOLE pin column set; the recall result ships the budget footer
  VERBATIM; the timeline signature carries ``since``/``until``/``q`` and
  NO ``k``; the timeline writes no stats;
* the write purity (AC7/D4.11): the module holds no embedder symbol and
  the write body performs exactly ONE ``insert_episode``;
* the decorator order is load-bearing (``@register_tool_category``
  ABOVE ``@tool``).

The MUTATION legs live in the pack: the gate order inverted, the actor
made optional, the admission bypassed at the tool layer, the budget
re-check removed, and an inline embed planted in the write body.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session, StaticPool, create_engine, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services import memoir_retrieval
from daemon.tools.memoir import (
    MEMOIR_AUTHORIZED_AGENT,
    MEMOIR_CODE_ACTOR_REQUIRED,
    MEMOIR_CODE_BODY_TOO_LONG,
    MEMOIR_CODE_DISABLED,
    MEMOIR_CODE_INVALID_ACTOR,
    MEMOIR_CODE_INVALID_EVENT_AT,
    MEMOIR_CODE_INVALID_WINDOW,
    MEMOIR_CODE_KEEPSAKE_CAP_REACHED,
    MEMOIR_CODE_K_OUT_OF_RANGE,
    MEMOIR_CODE_NOT_AUTHORIZED,
    MEMOIR_CODE_NOT_FOUND,
    MEMOIR_CODE_QUERY_REQUIRED,
    MEMOIR_CODE_REASON_REQUIRED,
    MEMOIR_CODE_TITLE_TOO_LONG,
    MEMOIR_CODES,
    MEMOIR_CATEGORY_KEY,
    MEMOIR_TOOL_NAMES,
    create_memoir_tools,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "daemon" / "tools" / "memoir.py"

ENVELOPE_RE = (
    r"^Error: (?P<label>memoir_\w+) REFUSED — reason=(?P<code>memoir_\w+): "
)


@pytest.fixture()
def engine():
    # Cross-thread: the tool bodies run their DB touches under
    # asyncio.to_thread, so the in-memory SQLite connection must be
    # shareable across threads (StaticPool + check_same_thread=False).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


class ProbeManager:
    """A manager double that COUNTS knob reads and engine touches.

    The gate-order proof is behavioral: the auth gate must precede the
    knob read, which must precede any engine touch (D4.2). A Mock would
    satisfy any access order silently; this double records it.
    """

    def __init__(self, engine=None, knob_on: bool = True) -> None:
        self._engine_handle = engine
        self._knob_on = knob_on
        self.knob_reads = 0
        self.engine_touches = 0

    @property
    def config(self):  # noqa: D401 — the knob's config tier
        self.knob_reads += 1
        return SimpleNamespace(memoir=SimpleNamespace(enabled=self._knob_on))

    @property
    def _engine(self):
        self.engine_touches += 1
        return self._engine_handle


@pytest.fixture()
def mgr(repo):
    return ProbeManager(engine=repo.engine)


@pytest.fixture()
def tools(mgr):
    return {
        tool_obj.name: tool_obj
        for tool_obj in create_memoir_tools(
            mgr, current_instance_id="inst-1", agent_id=MEMOIR_AUTHORIZED_AGENT
        )
    }


def _assert_envelope(result: str, label: str, code: str) -> None:
    text = result if isinstance(result, str) else str(result)
    match = re.match(ENVELOPE_RE, text)
    assert match is not None, f"not a refusal envelope: {text!r}"
    assert match.group("label") == label, text
    assert match.group("code") == code, text


# ── the surface ─────────────────────────────────────────────────────────


class TestTheSurface:
    def test_the_factory_builds_exactly_the_five_blessed_tools(self, tools):
        assert set(tools) == set(MEMOIR_TOOL_NAMES) == {
            "memoir_write",
            "memoir_pin",
            "memoir_unpin",
            "memoir_recall",
            "memoir_timeline",
        }

    def test_the_decorator_order_is_load_bearing(self):
        """``@register_tool_category`` sits ABOVE ``@tool`` on every tool."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert source.count("@register_tool_category(MEMOIR_CATEGORY_KEY)") == 5
        for schema in (
            "MemoirWriteInput",
            "MemoirPinInput",
            "MemoirUnpinInput",
            "MemoirRecallInput",
            "MemoirTimelineInput",
        ):
            dec_idx = source.index("@register_tool_category(MEMOIR_CATEGORY_KEY)")
            tool_idx = source.index(f"@tool(args_schema={schema})")
            assert dec_idx < tool_idx, f"{schema}: decorator order inverted"

    def test_the_import_time_assertion_freezes_nineteen_codes(self):
        assert len(MEMOIR_CODES) == 19


# ── AC1: the enforced gate order ────────────────────────────────────────


class TestTheGateOrder:
    @pytest.mark.asyncio
    async def test_an_unauthorized_caller_is_refused_before_any_knob_or_db(
        self, tools, mgr
    ):
        """The DENIED toolset: built for a non-ari agent. Every refusal
        fires with ZERO knob reads and ZERO engine touches — the
        unauthorized caller learns nothing about the knob and touches
        nothing (D4.2, behavioral)."""
        denied = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                mgr, current_instance_id="inst-1", agent_id="developer"
            )
        }
        for name, tool_obj in denied.items():
            mgr.knob_reads = 0
            mgr.engine_touches = 0
            result = await tool_obj.ainvoke(self._args_for(name))
            _assert_envelope(result, name, MEMOIR_CODE_NOT_AUTHORIZED)
            assert mgr.knob_reads == 0, f"{name}: the knob was read"
            assert mgr.engine_touches == 0, f"{name}: the DB was touched"

    @pytest.mark.asyncio
    async def test_a_blank_caller_is_refused_as_agent_unknown(self, mgr):
        blank_tools = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                mgr, current_instance_id="inst-1", agent_id=""
            )
        }
        result = await blank_tools["memoir_write"].ainvoke({"text": "hello"})
        _assert_envelope(result, "memoir_write", "memoir_agent_unknown")

    @pytest.mark.asyncio
    async def test_a_non_ari_caller_never_learns_the_knob_state(
        self, mgr, monkeypatch
    ):
        """The refusal must not differ when the knob is OFF — the
        unauthorized caller learns nothing about the knob (D4.2)."""
        monkeypatch.setenv("ENSEMBLE_MEMOIR_ENABLED", "false")
        denied = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                mgr, current_instance_id="inst-1", agent_id="developer"
            )
        }
        result = await denied["memoir_write"].ainvoke({"text": "hello"})
        _assert_envelope(result, "memoir_write", MEMOIR_CODE_NOT_AUTHORIZED)
        assert MEMOIR_AUTHORIZED_AGENT in result
        # The envelope (everything before "reason=") carries no knob hint.
        assert (
            "ENSEMBLE_MEMOIR_ENABLED"
            not in result.split("reason=", 1)[0]
        )

    @staticmethod
    def _args_for(name: str) -> dict:
        if name == "memoir_write":
            return {"text": "a memory body"}
        if name == "memoir_pin":
            return {"id": "x", "reason": "r", "actor": "empress"}
        if name == "memoir_unpin":
            return {"id": "x"}
        if name == "memoir_recall":
            return {"query": "q"}
        return {}


class TestTheKnobGate:
    @pytest.mark.asyncio
    async def test_knob_off_refuses_naming_the_variable_before_any_db(
        self, mgr, tools, monkeypatch
    ):
        monkeypatch.setenv("ENSEMBLE_MEMOIR_ENABLED", "false")
        mgr.knob_reads = 0
        mgr.engine_touches = 0
        for name in ("memoir_write", "memoir_pin", "memoir_unpin",
                     "memoir_recall", "memoir_timeline"):
            result = await tools[name].ainvoke(
                TestTheGateOrder._args_for(name)
            )
            _assert_envelope(result, name, MEMOIR_CODE_DISABLED)
            assert "ENSEMBLE_MEMOIR_ENABLED" in result
        assert mgr.engine_touches == 0, "a knob-off call touched the DB"

    @pytest.mark.asyncio
    async def test_a_garbage_knob_value_fails_closed_the_same_way(
        self, mgr, tools, monkeypatch
    ):
        monkeypatch.setenv("ENSEMBLE_MEMOIR_ENABLED", "banana")
        result = await tools["memoir_write"].ainvoke({"text": "hello"})
        _assert_envelope(result, "memoir_write", MEMOIR_CODE_DISABLED)

    @pytest.mark.asyncio
    async def test_an_argument_error_never_touches_the_db(self, mgr, tools):
        mgr.engine_touches = 0
        result = await tools["memoir_write"].ainvoke({"text": "   "})
        _assert_envelope(result, "memoir_write", MEMOIR_CODE_BODY_TOO_LONG)
        assert mgr.engine_touches == 0


# ── AC1: the typed refusal matrix (the field codes) ─────────────────────


class TestTheRefusalMatrix:
    @pytest.mark.asyncio
    async def test_keepsake_without_reason_is_refused(self, tools):
        result = await tools["memoir_write"].ainvoke(
            {"text": "body", "keepsake": True}
        )
        _assert_envelope(
            result, "memoir_write", MEMOIR_CODE_REASON_REQUIRED
        )

    @pytest.mark.asyncio
    async def test_keepsake_without_actor_is_refused(self, tools):
        result = await tools["memoir_write"].ainvoke(
            {"text": "body", "keepsake": True, "reason": "core"}
        )
        _assert_envelope(
            result, "memoir_write", MEMOIR_CODE_ACTOR_REQUIRED
        )

    @pytest.mark.asyncio
    async def test_a_wrong_actor_claim_is_refused(self, tools):
        result = await tools["memoir_write"].ainvoke(
            {
                "text": "body",
                "keepsake": True,
                "reason": "core",
                "actor": "ari",
            }
        )
        _assert_envelope(result, "memoir_write", MEMOIR_CODE_INVALID_ACTOR)

    @pytest.mark.asyncio
    async def test_pin_without_reason_is_refused(self, tools):
        result = await tools["memoir_pin"].ainvoke(
            {"id": "abc", "reason": "", "actor": "empress"}
        )
        _assert_envelope(result, "memoir_pin", MEMOIR_CODE_REASON_REQUIRED)

    @pytest.mark.asyncio
    async def test_pin_requires_the_actor_claim(self, tools):
        result = await tools["memoir_pin"].ainvoke(
            {"id": "abc", "reason": "core", "actor": "system"}
        )
        _assert_envelope(result, "memoir_pin", MEMOIR_CODE_INVALID_ACTOR)

    @pytest.mark.asyncio
    async def test_bad_event_at_is_refused(self, tools):
        result = await tools["memoir_write"].ainvoke(
            {"text": "body", "event_at": "not a timestamp"}
        )
        _assert_envelope(result, "memoir_write", "memoir_invalid_event_at")

    @pytest.mark.asyncio
    async def test_oversized_title_is_refused(self, tools, repo):
        result = await tools["memoir_write"].ainvoke(
            {"text": "body", "title": "x" * 300}
        )
        _assert_envelope(result, "memoir_write", MEMOIR_CODE_TITLE_TOO_LONG)

    @pytest.mark.asyncio
    async def test_out_of_range_importance_is_refused(self, tools):
        result = await tools["memoir_write"].ainvoke(
            {"text": "body", "importance": 11}
        )
        _assert_envelope(
            result, "memoir_write", "memoir_importance_out_of_range"
        )

    @pytest.mark.asyncio
    async def test_oversized_body_is_refused(self, tools):
        result = await tools["memoir_write"].ainvoke({"text": "word " * 2100})
        _assert_envelope(result, "memoir_write", MEMOIR_CODE_BODY_TOO_LONG)

    @pytest.mark.asyncio
    async def test_oversized_star_list_is_refused(self, tools):
        result = await tools["memoir_write"].ainvoke(
            {"text": "body", "stars": [f"@p{i}" for i in range(20)]}
        )
        _assert_envelope(
            result, "memoir_write", "memoir_stars_out_of_bounds"
        )

    @pytest.mark.asyncio
    async def test_recall_k_bounds_are_refused_not_clamped(self, tools):
        for bad in (0, -1, 21, 999):
            result = await tools["memoir_recall"].ainvoke(
                {"query": "q", "k": bad}
            )
            _assert_envelope(
                result, "memoir_recall", MEMOIR_CODE_K_OUT_OF_RANGE
            )
            assert "clamped" in result

    @pytest.mark.asyncio
    async def test_timeline_inverted_window_is_refused(self, tools):
        result = await tools["memoir_timeline"].ainvoke(
            {"since": "2026-11-05T12:00:00+00:00",
             "until": "2026-11-04T12:00:00+00:00"}
        )
        _assert_envelope(
            result, "memoir_timeline", MEMOIR_CODE_INVALID_WINDOW
        )

    @pytest.mark.asyncio
    async def test_timeline_unparseable_bound_names_the_parameter(
        self, tools
    ):
        result = await tools["memoir_timeline"].ainvoke(
            {"since": "march-ish"}
        )
        _assert_envelope(
            result, "memoir_timeline", "memoir_invalid_event_at"
        )
        assert "since:" in result

    @pytest.mark.asyncio
    async def test_pin_of_a_missing_episode_is_refused(self, tools):
        result = await tools["memoir_pin"].ainvoke(
            {"id": "no-such", "reason": "core", "actor": "empress"}
        )
        _assert_envelope(result, "memoir_pin", MEMOIR_CODE_NOT_FOUND)

    @pytest.mark.asyncio
    async def test_unpin_of_a_missing_episode_is_refused(self, tools):
        result = await tools["memoir_unpin"].ainvoke({"id": "no-such"})
        _assert_envelope(result, "memoir_unpin", MEMOIR_CODE_NOT_FOUND)

    @pytest.mark.asyncio
    async def test_a_blank_recall_query_refuses_with_query_required(
        self, mgr, tools
    ):
        """V-1 (user-ruled 2026-11-05): the honest code for an
        empty/whitespace ``memoir_recall`` query is an INPUT error —
        ``memoir_query_required``, identity-mapped from the retrieval
        service's ``query_required``. It is NOT the catch-all: calling a
        user's empty string an ``internal error`` was a permanent lie in
        the contract (audit: 0 of the 18 predecessor codes honest)."""
        result = await tools["memoir_recall"].ainvoke({"query": "   "})
        _assert_envelope(
            result, "memoir_recall", MEMOIR_CODE_QUERY_REQUIRED
        )
        assert "empty" in result


# ── AC5: the behaviors ──────────────────────────────────────────────────


class TestWritePinUnpin:
    @pytest.mark.asyncio
    async def test_the_write_appends_one_episode_and_reports_pending(
        self, mgr, tools, repo
    ):
        result = await tools["memoir_write"].ainvoke(
            {"text": "we chose the hybrid ranker", "title": "ranker",
             "importance": 7}
        )
        assert isinstance(result, dict)
        assert result["is_keepsake"] is False
        assert result["semantic_status"] == "pending"
        assert result["event_at"] == result["created_at"]
        episodes = repo.fetch_latest(MEMOIR_AUTHORIZED_AGENT, None)
        assert len(episodes) == 1
        assert episodes[0].id == result["episode_id"]
        assert episodes[0].body == "we chose the hybrid ranker"
        assert episodes[0].importance == 7

    @pytest.mark.asyncio
    async def test_a_keepsake_write_records_the_claim_and_provenance(
        self, mgr, tools, repo
    ):
        result = await tools["memoir_write"].ainvoke(
            {
                "text": "the emperor set the doctrine",
                "keepsake": True,
                "reason": "binding doctrine",
                "actor": "emperor",
            }
        )
        assert isinstance(result, dict)
        assert result["is_keepsake"] is True
        assert result["pinned_by"] == "emperor"
        assert "provenance_class" in result  # "" on a windowless manager
        row = repo.fetch_latest(MEMOIR_AUTHORIZED_AGENT, None)[0]
        assert row.is_keepsake is True
        assert row.pinned_by == "emperor"
        assert row.pinned_reason == "binding doctrine"

    @pytest.mark.asyncio
    async def test_the_cap_refusal_names_the_current_pins(self, tools, repo):
        for index in range(12):
            repo.insert_episode(
                agent_id=MEMOIR_AUTHORIZED_AGENT,
                body=f"keepsake number {index}",
                is_keepsake=True,
                pinned_by="empress",
                pinned_reason=f"kept {index}",
            )
        result = await tools["memoir_pin"].ainvoke(_pin_args(repo))
        _assert_envelope(
            result, "memoir_pin", MEMOIR_CODE_KEEPSAKE_CAP_REACHED
        )
        assert "cap" in result

    @pytest.mark.asyncio
    async def test_pinning_an_already_pinned_episode_re_pins_with_a_note(
        self, mgr, tools, repo
    ):
        episode_id = repo.insert_episode(
            agent_id=MEMOIR_AUTHORIZED_AGENT, body="the choice"
        )
        first = await tools["memoir_pin"].ainvoke(
            {"id": episode_id, "reason": "core", "actor": "empress"}
        )
        assert isinstance(first, dict) and first["re_pinned"] is False
        second = await tools["memoir_pin"].ainvoke(
            {"id": episode_id, "reason": "still core", "actor": "emperor"}
        )
        assert isinstance(second, dict)
        assert second["re_pinned"] is True
        assert "re-pinned" in second.get("note", "")
        assert second["pinned_by"] == "emperor"
        row = repo.fetch_latest(MEMOIR_AUTHORIZED_AGENT, None)[0]
        assert row.pinned_by == "emperor"
        assert row.pinned_reason == "still core"

    @pytest.mark.asyncio
    async def test_unpin_clears_the_whole_claim_and_keeps_the_episode(
        self, mgr, tools, repo
    ):
        episode_id = repo.insert_episode(
            agent_id=MEMOIR_AUTHORIZED_AGENT, body="temporary keepsake"
        )
        await tools["memoir_pin"].ainvoke(
            {"id": episode_id, "reason": "core", "actor": "empress"}
        )
        result = await tools["memoir_unpin"].ainvoke({"id": episode_id})
        assert isinstance(result, dict) and result["unpinned"] is True
        with Session(repo.engine) as session:
            row = session.exec(
                select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
            ).first()
            assert row is not None, "unpin deleted the episode"
            assert row.is_keepsake is False
            assert row.pinned_by is None
            assert row.pinned_reason is None
            assert row.pinned_at is None

    @pytest.mark.asyncio
    async def test_unpin_of_a_non_keepsake_is_an_idempotent_noop(
        self, mgr, tools, repo
    ):
        episode_id = repo.insert_episode(
            agent_id=MEMOIR_AUTHORIZED_AGENT, body="plain episode"
        )
        result = await tools["memoir_unpin"].ainvoke({"id": episode_id})
        assert isinstance(result, dict)
        assert result["unpinned"] is False
        assert "idempotent no-op" in result["note"]
        assert repo.fetch_latest(MEMOIR_AUTHORIZED_AGENT, None)[0].id == (
            episode_id
        )


def _pin_args(repo) -> dict:
    """The id of an UNPINNED episode (the pin subject for cap tests)."""
    plain = repo.insert_episode(
        agent_id=MEMOIR_AUTHORIZED_AGENT, body="the 13th candidate"
    )
    return {"id": plain, "reason": "one too many", "actor": "empress"}


# ── the reads: recall + timeline ────────────────────────────────────────


class TestTheReads:
    @pytest.mark.asyncio
    async def test_recall_ships_the_footer_verbatim(self, mgr, tools, repo):
        repo.insert_episode(
            agent_id=MEMOIR_AUTHORIZED_AGENT, body="the zebra choice", title="z"
        )
        service_result = None
        from daemon.services.memoir_retrieval import MemoirRetrievalService

        direct = MemoirRetrievalService(
            repo, config=getattr(mgr, "config", None)
        ).recall(MEMOIR_AUTHORIZED_AGENT, "zebra", 5)
        result = await tools["memoir_recall"].ainvoke({"query": "zebra"})
        assert isinstance(result, str)
        assert result.endswith(direct.footer), (
            "the tool result must carry the service's budget footer VERBATIM"
        )
        assert "stats_updated=true" in direct.footer

    @pytest.mark.asyncio
    async def test_recall_marks_exactly_what_it_ships(self, tools, repo):
        for index in range(3):
            repo.insert_episode(
                agent_id=MEMOIR_AUTHORIZED_AGENT,
                body=f"memory {index} about zebras",
            )
        result = await tools["memoir_recall"].ainvoke({"query": "zebra"})
        episodes = repo.fetch_latest(MEMOIR_AUTHORIZED_AGENT, None)
        shipped_ids = set(re.findall(r"- id: (\w+)", result))
        marked = [e for e in episodes if e.last_accessed_at is not None]
        assert len(marked) == len(shipped_ids)
        assert {e.id for e in marked} == shipped_ids

    @pytest.mark.asyncio
    async def test_timeline_carries_since_until_q_and_no_k(self, tools):
        schema = tools["memoir_timeline"].args_schema.model_fields
        assert set(schema) == {"since", "until", "q"}
        write_schema = tools["memoir_write"].args_schema.model_fields
        assert set(write_schema) == {
            "text", "title", "importance", "event_at", "stars",
            "keepsake", "reason", "actor",
        }
        pin_schema = tools["memoir_pin"].args_schema.model_fields
        assert set(pin_schema) == {"id", "reason", "actor"}
        unpin_schema = tools["memoir_unpin"].args_schema.model_fields
        assert set(unpin_schema) == {"id"}
        recall_schema = tools["memoir_recall"].args_schema.model_fields
        assert set(recall_schema) == {"query", "k"}

    @pytest.mark.asyncio
    async def test_timeline_writes_no_stats_and_the_footer_says_so(
        self, mgr, tools, repo
    ):
        base = "2026-11-01T00:00:00+00:00"
        repo.insert_episode(
            agent_id=MEMOIR_AUTHORIZED_AGENT, body="march memory",
            event_at=base,
        )
        result = await tools["memoir_timeline"].ainvoke(
            {"since": "2026-10-31T00:00:00+00:00",
             "until": "2026-11-02T00:00:00+00:00"}
        )
        assert isinstance(result, str)
        assert "stats_updated=false" in result
        episodes = repo.fetch_latest(MEMOIR_AUTHORIZED_AGENT, None)
        assert all(e.last_accessed_at is None for e in episodes)

    @pytest.mark.asyncio
    async def test_the_timeline_window_bounds_are_inclusive(
        self, mgr, tools, repo
    ):
        repo.insert_episode(
            agent_id=MEMOIR_AUTHORIZED_AGENT, body="edge memory",
            event_at="2026-11-01T00:00:00+00:00",
        )
        result = await tools["memoir_timeline"].ainvoke(
            {"since": "2026-11-01T00:00:00+00:00",
             "until": "2026-11-01T00:00:00+00:00"}
        )
        assert isinstance(result, str)
        assert "edge memory" in result

    @pytest.mark.asyncio
    async def test_the_footer_tips_the_envelope_and_the_tool_says_so(
        self, monkeypatch, mgr, tools, repo
    ):
        """The tool result carries the POST-recheck footer: when the
        footer tips the envelope over, the tail is dropped again (the
        mutation leg plants the re-check removal — the footer bytes
        change and this test goes RED)."""
        from datetime import datetime, timedelta, timezone

        from daemon.loader import estimate_tokens
        from daemon.services.memoir_retrieval import (
            MEMOIR_RECALL_TOKEN_BUDGET,
            MemoirRetrievalService,
            render_episode,
        )

        fixed = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        base = fixed - timedelta(days=10)
        first = repo.insert_episode(
            agent_id=MEMOIR_AUTHORIZED_AGENT, body="zebra " * 200,
            title="first", created_at=base,
        )
        second = repo.insert_episode(
            agent_id=MEMOIR_AUTHORIZED_AGENT, body="zebra " * 200,
            title="second", created_at=base + timedelta(minutes=1),
        )

        def _size(episode_id: str) -> int:
            with Session(repo.engine) as session:
                row = session.exec(
                    select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
                ).first()
            return estimate_tokens(
                render_episode(MemoirRetrievalService(repo)._project(row))
            )

        episodes_fit = _size(first) + _size(second)
        monkeypatch.setattr(
            memoir_retrieval, "MEMOIR_RECALL_TOKEN_BUDGET",
            max(episodes_fit, MEMOIR_RECALL_TOKEN_BUDGET // 1000),
        )
        result = await tools["memoir_recall"].ainvoke({"query": "zebra"})
        assert isinstance(result, str)
        assert "dropped=1" in result, (
            "the footer must disclose the post-recheck tail drop"
        )
        assert "episodes=1" in result


# ── AC7 / D4.11: the write purity ───────────────────────────────────────


class TestTheWritePurity:
    @staticmethod
    def _source_without_docstrings() -> str:
        """The module source with every docstring stripped (AST pass).

        The prose MUST be allowed to NAME the forbidden symbols (the pin
        documents what it forbids); the pin is about REFERENCES. Stripping
        docstrings before the needle scan keeps the pin honest without
        making the documentation unspeakable.
        """
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)

        class _Strip(ast.NodeTransformer):
            def _strip(self, node):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    node.body = node.body[1:] or [ast.Pass()]
                self.generic_visit(node)
                return node

            def visit_Module(self, node):
                return self._strip(node)

            def visit_FunctionDef(self, node):
                return self._strip(node)

            def visit_AsyncFunctionDef(self, node):
                return self._strip(node)

            def visit_ClassDef(self, node):
                return self._strip(node)

        return ast.unparse(_Strip().visit(tree))

    def test_the_module_holds_no_embedder_symbol(self):
        code = self._source_without_docstrings()
        for needle in (
            "store_embedding",
            "client.embeddings",
            "memoir_embedding_worker",
            "EmbedFn",
            "_default_embed",
            "def _embed",
            "embed(",
            "embedder",
        ):
            assert needle not in code, f"purity violation: {needle!r}"

    def test_the_module_imports_no_embedding_lane_module(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "embedding" not in alias.name, alias.name
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "embedding" not in module, module

    def test_the_write_body_performs_exactly_one_insert(self):
        code = self._source_without_docstrings()
        assert code.count("repo.insert_episode") == 1
        assert code.count("repo.set_pin") == 1
        assert code.count("repo.clear_pin") == 1

    @pytest.mark.asyncio
    async def test_no_await_of_the_worker_appears_in_the_module(self):
        code = self._source_without_docstrings()
        assert "await embed" not in code
        assert "memoir_worker" not in code


# ── the degraded boot (D4.6) ────────────────────────────────────────────


class TestTheDegradedBoot:
    @pytest.mark.asyncio
    async def test_a_degraded_boot_keeps_the_tools_present_and_loud(
        self, tools
    ):
        manager = ProbeManager(engine=None)
        degraded = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                manager, current_instance_id="i", agent_id=MEMOIR_AUTHORIZED_AGENT
            )
        }
        assert set(degraded) == set(MEMOIR_TOOL_NAMES)
        result = await degraded["memoir_write"].ainvoke({"text": "hello"})
        _assert_envelope(
            result, "memoir_write", "memoir_internal_error"
        )
        assert "no engine" in result

    def test_the_factory_signature_captures_the_identity(self, mgr):
        tools = create_memoir_tools(
            mgr, current_instance_id="inst-9", agent_id=MEMOIR_AUTHORIZED_AGENT
        )
        assert len(tools) == 5
