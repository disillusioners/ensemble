"""Tests for the ``source`` parameter on ``SQLModelInstanceRepository.list()``.

Covers the dialect-aware source-type filter that matches the ``source``
query param against ``instance_metadata.source_type``:

* ``source == "chat"`` → ``source_type IN (CHAT_SOURCE_TYPES)`` (telegram /
  slack / discord / whatsapp — the registry-backed chat-platform set
  mirroring ``USER_ORIGIN_CHAT_SOURCE_TYPES`` in
  ``daemon/tools/upgrade_journal.py``).
* any other string → ``source_type == <source>`` (exact match).
* ``None`` / empty → no filter (caller gets the full set).

Also pins:

* the ``CHAT_SOURCE_TYPES`` constant membership (telegram/slack/discord/
  whatsapp — excludes webhook/CI/automation).
* the asymmetry: ``CHAT_SOURCE_PREFIXES`` (the THREE-member worker-lane
  prefix tuple) and ``CHAT_SOURCE_TYPES`` (the FOUR-member registry set)
  are different concepts on purpose.
* the helper's behavior under ``include_descendants=True`` (root-only,
  lineage of in-scope roots is preserved).

These tests run against the in-memory SQLite path used by
``test_instance_search.py``. The PG-only live JSONB regression
lives at ``tests/postgres/test_instance_source_filter_pg.py``
(run with ``pytest -m postgres``); that file is the canonical
C1 headline regression (the ``->`` + CAST bug that silently
returned quoted JSON text and zero rows on production).

The dialect-aware compile pin for ``_build_source_condition``
lives HERE (``TestSourceConditionCompileDialect``) -- it compiles
the helper against the PG dialect via a stub engine, so it catches
a future revert of the PG branch to the naive ``->`` + CAST form
WITHOUT requiring a live PG server. The PG-marked file's autouse
``_pg_truncate_tables`` fixture chains the compile pin to the
live ``pg_engine`` probe (skipping when PG is unreachable), so the
no-PG safety net had to live outside the PG-marked file.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlmodel import Session, SQLModel, create_engine

from daemon.repositories.instance import (
    CHAT_SOURCE_TYPES,
    SQLModelInstanceRepository,
)


# ----- fixtures --------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    """In-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repo(engine):
    return SQLModelInstanceRepository(engine)


def _make(repo, instance_id, agent_id, agent_dir, *, metadata=None,
          parent_id=None, project_id=None, status="idle"):
    """Create an instance with the given parameters."""
    return repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        metadata=metadata or {},
        parent_id=parent_id,
        project_id=project_id,
        status=status,
    )


# ----- test data --------------------------------------------------------


@pytest.fixture
def seeded_repo(repo):
    """Seed a handful of instances covering every source_type and the
    no-source_type control.

    Layout (source_type is read from ``instance_metadata.source_type``):

      - "tg1"  : source_type="telegram"
      - "tg2"  : source_type="telegram"  (second telegram to confirm IN)
      - "slk1" : source_type="slack"
      - "dsc1" : source_type="discord"
      - "wat1" : source_type="whatsapp"
      - "web1" : source_type="webhook"   (CI/automation, NOT in chat)
      - "sched": source_type="scheduler" (NOT a chat origin)
      - "noMeta": no source_type key at all

    Behavior we want to exercise:

      - source="chat"      → tg1, tg2, slk1, dsc1, wat1 (5 of 8)
      - source="telegram"  → tg1, tg2 (exact match)
      - source="webhook"   → web1 (exact match, deliberately EXCLUDED from chat)
      - source=None        → all 8 (no filter)
    """
    _make(repo, "tg1", agent_id="developer", agent_dir="agents/developer",
          metadata={"source_type": "telegram", "title": "tg1"})
    _make(repo, "tg2", agent_id="developer", agent_dir="agents/developer",
          metadata={"source_type": "telegram", "title": "tg2"})
    _make(repo, "slk1", agent_id="developer", agent_dir="agents/developer",
          metadata={"source_type": "slack"})
    _make(repo, "dsc1", agent_id="developer", agent_dir="agents/developer",
          metadata={"source_type": "discord"})
    _make(repo, "wat1", agent_id="developer", agent_dir="agents/developer",
          metadata={"source_type": "whatsapp"})
    _make(repo, "web1", agent_id="developer", agent_dir="agents/developer",
          metadata={"source_type": "webhook"})
    _make(repo, "sched", agent_id="scheduler", agent_dir="agents/scheduler",
          metadata={"source_type": "scheduler"})
    _make(repo, "noMeta", agent_id="developer", agent_dir="agents/developer",
          metadata={"title": "no source_type"})
    return repo


def _ids(instances):
    return sorted(inst.instance_id for inst in instances)


# ----- constant membership pin ----------------------------------------


class TestChatSourceTypesConstant:
    """Pin the chat-source-type set used by the Projects tab chat filter.

    Mirrors the ``TestChatSourcePrefixesConstant`` family in
    ``tests/unit/routers/test_source_reservation.py`` — the prefix tuple
    is the THREE-member interactive-chat WORKER-LANE set; this class
    pins the FOUR-member REGISTRY source_type set that drives the chat
    tab filter. The asymmetry is documented at
    ``daemon/tools/upgrade_journal.py:2466-2470`` and re-asserted below.
    """

    def test_chat_source_types_membership(self):
        """The constant MUST be the exact four members: telegram, slack,
        discord, whatsapp (forward-compat for the upcoming whatsapp
        adapter — see ``daemon/tools/upgrade_journal.py:2453-2459``)."""
        assert CHAT_SOURCE_TYPES == ("telegram", "slack", "discord", "whatsapp")

    def test_chat_source_types_excludes_webhook(self):
        """``webhook`` MUST be excluded — it is CI/automation, not an
        interactive chat platform. A regression that adds webhook to the
        chat tab would surface non-interactive sources to operators."""
        assert "webhook" not in CHAT_SOURCE_TYPES

    def test_chat_source_types_excludes_scheduler(self):
        """``scheduler`` is daemon-controlled (ADR-012) and must never
        appear on the chat tab — it is a lifecycle-managed source, not
        a human chat channel."""
        assert "scheduler" not in CHAT_SOURCE_TYPES

    def test_chat_source_types_asymmetry_with_prefixes(self):
        """The four-member CHAT_SOURCE_TYPES set is intentionally a
        SUPERSET of the three-member CHAT_SOURCE_PREFIXES worker-lane
        set (the prefixes do not carry whatsapp because no whatsapp
        adapter is registered yet — see upgrade_journal.py:2453-2459).
        The two constants are distinct concepts: prefix-matched ids
        for the worker lane vs. registry source_type values for the
        Projects filter. Do NOT dedup the two — different members,
        different match semantics."""
        from daemon.constants import CHAT_SOURCE_PREFIXES

        prefix_types = {p.rstrip(":") for p in CHAT_SOURCE_PREFIXES}
        # Prefix set is a strict subset of the source_type set
        # (whatsapp is the extra member on the source_type side).
        assert prefix_types.issubset(set(CHAT_SOURCE_TYPES))
        assert prefix_types != set(CHAT_SOURCE_TYPES)
        assert "whatsapp" in CHAT_SOURCE_TYPES
        assert "whatsapp:" not in CHAT_SOURCE_PREFIXES

    def test_chat_source_types_parity_with_user_origin_gate(self):
        """M2 parity pin: ``CHAT_SOURCE_TYPES`` (this module's
        source_type set for the Projects tab filter) MUST equal
        ``USER_ORIGIN_CHAT_SOURCE_TYPES`` (the live-upgrade gate set
        in ``daemon.tools.upgrade_journal.py``). The two constants
        are documented as mirrors of each other but the symmetry
        is semantic — both lists exist for different consumers
        (Projects-tab filter vs upgrade gate) and the asymmetry
        vs ``CHAT_SOURCE_PREFIXES`` is intentional and stays
        separate. This test imports the gate set live and
        asserts exact-set equality so a future divergence
        (e.g. someone adds a fifth chat source to the upgrade
        gate but forgets the Projects filter, or vice versa)
        is caught here."""
        from daemon.tools.upgrade_journal import (
            USER_ORIGIN_CHAT_SOURCE_TYPES,
        )

        assert set(CHAT_SOURCE_TYPES) == set(USER_ORIGIN_CHAT_SOURCE_TYPES)


# ----- source="chat" ----------------------------------------------------


class TestSourceChatSentinel:
    """``source="chat"`` is the special Projects-tab sentinel. It maps
    to the four-member registry source_type set and EXCLUDES webhook +
    scheduler."""

    def test_chat_matches_all_four_registry_members(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="chat")
        assert total == 5
        assert _ids(instances) == ["dsc1", "slk1", "tg1", "tg2", "wat1"]

    def test_chat_excludes_webhook(self, seeded_repo):
        """A regression that adds webhook to CHAT_SOURCE_TYPES would
        surface here — the chat tab must NEVER include webhook-sourced
        instances (CI/automation vs interactive chat)."""
        instances, _, _ = seeded_repo.list(source="chat")
        ids = _ids(instances)
        assert "web1" not in ids

    def test_chat_excludes_scheduler(self, seeded_repo):
        instances, _, _ = seeded_repo.list(source="chat")
        ids = _ids(instances)
        assert "sched" not in ids

    def test_chat_excludes_no_source_type(self, seeded_repo):
        """Instances with no ``source_type`` key in metadata must not
        be included in the chat tab — only platform-stamped instances
        are chat origins."""
        instances, _, _ = seeded_repo.list(source="chat")
        ids = _ids(instances)
        assert "noMeta" not in ids

    def test_chat_returns_zero_when_no_chat_instances(self, repo):
        """An instance set with no chat-source instances must return
        an empty page, not every row."""
        _make(repo, "a", agent_id="developer", agent_dir="agents/developer",
              metadata={"source_type": "webhook"})
        _make(repo, "b", agent_id="developer", agent_dir="agents/developer",
              metadata={"title": "no source_type"})
        instances, total, _ = repo.list(source="chat")
        assert total == 0
        assert instances == []


# ----- source=specific value ------------------------------------------


class TestSourceExactMatch:
    """Any non-``"chat"`` string is matched as a single ``source_type``
    value (exact equality)."""

    def test_source_telegram_exact_match(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="telegram")
        assert total == 2
        assert _ids(instances) == ["tg1", "tg2"]

    def test_source_slack_exact_match(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="slack")
        assert total == 1
        assert _ids(instances) == ["slk1"]

    def test_source_discord_exact_match(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="discord")
        assert total == 1
        assert _ids(instances) == ["dsc1"]

    def test_source_whatsapp_exact_match(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="whatsapp")
        assert total == 1
        assert _ids(instances) == ["wat1"]

    def test_source_webhook_exact_match(self, seeded_repo):
        """Sanity: even though webhook is excluded from ``source="chat"``,
        a caller asking for ``source="webhook"`` directly (e.g. a future
        webhook-specific tab) still gets the webhook instances. The
        exclusion is a CHAT-TAB concept, not a global block on webhook."""
        instances, total, _ = seeded_repo.list(source="webhook")
        assert total == 1
        assert _ids(instances) == ["web1"]

    def test_source_scheduler_exact_match(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="scheduler")
        assert total == 1
        assert _ids(instances) == ["sched"]

    def test_source_unknown_returns_empty(self, seeded_repo):
        """An unknown source type is a valid input — it just matches
        no rows. The helper does not raise."""
        instances, total, _ = seeded_repo.list(source="rocketchat")
        assert total == 0
        assert instances == []


# ----- source=None / empty (no filter) --------------------------------


class TestSourceDisabled:
    """``source=None`` and ``source=""`` apply no filter — every row
    is returned, mirroring the pre-feature behavior."""

    def test_source_none_returns_everything(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source=None)
        assert total == 8
        assert _ids(instances) == [
            "dsc1", "noMeta", "sched", "slk1", "tg1", "tg2", "wat1", "web1"
        ]

    def test_source_empty_string_returns_everything(self, seeded_repo):
        """Empty string is treated as "no filter" (mirrors the
        ``is_chat_source`` None/empty boundary contract at
        ``daemon.constants``)."""
        instances, total, _ = seeded_repo.list(source="")
        assert total == 8

    def test_source_omitted_returns_everything(self, seeded_repo):
        """Omitting the kwarg entirely applies no filter (default)."""
        instances, total, _ = seeded_repo.list()
        assert total == 8

    def test_source_whitespace_only_treated_as_literal(self, repo):
        """NIT: pin the whitespace-only ``source=`` behavior. The
        helper's truthy check is ``if not source: return None`` —
        ``"   "`` is truthy in Python so it is NOT a no-op sentinel
        and is passed through as a single-value IN clause. The
        clause matches zero rows because no source_type equals
        ``"   "`` (defensive pin: a future change to
        ``if not source.strip()`` would break here, which is the
        desired loud-fail behavior)."""
        _make(repo, "ws-a", agent_id="developer", agent_dir="agents/developer",
              metadata={"source_type": "telegram"})
        _make(repo, "ws-b", agent_id="developer", agent_dir="agents/developer",
              metadata={"source_type": "slack"})

        instances, total, _ = repo.list(source="   ")
        # Whitespace is NOT the no-op sentinel — it's a real filter
        # value. No source_type equals "   " → zero matches.
        assert total == 0
        assert instances == []

    def test_source_helper_whitespace_returns_clause_not_none(self, repo):
        """NIT: companion pin to ``test_source_whitespace_only_treated_as_literal``
        at the helper level. ``"   "`` must NOT be treated as
        ``None`` — it must produce an IN clause (a regression that
        switches ``if not source:`` to ``if not source.strip():``
        would surface here as a None return value)."""
        from sqlmodel import Session
        with Session(repo.engine) as s:
            assert repo._build_source_condition(s, "   ") is not None


# ----- combination with other filters --------------------------------


class TestSourceCombinations:
    """The source filter composes with the other filters on ``list()``
    (project_id, exclude_kb, include_descendants) exactly like the
    existing project_id filter does."""

    def test_source_combines_with_exclude_kb(self, repo):
        """KB agents are excluded even when source=chat. The source
        filter does NOT bypass the KB exclusion."""
        _make(repo, "tg-kb", agent_id="kb-writer", agent_dir="agents/kb-writer",
              metadata={"source_type": "telegram"})
        _make(repo, "tg-dev", agent_id="developer", agent_dir="agents/developer",
              metadata={"source_type": "telegram"})

        # exclude_kb=True (default) → KB agent filtered out.
        instances, total, _ = repo.list(source="chat", exclude_kb=True)
        assert total == 1
        assert _ids(instances) == ["tg-dev"]

        # exclude_kb=False → KB agent included.
        instances, total, _ = repo.list(source="chat", exclude_kb=False)
        assert total == 2
        assert _ids(instances) == ["tg-dev", "tg-kb"]

    def test_source_combines_with_project_id(self, repo):
        """source and project_id both apply. Only the intersection is
        returned."""
        _make(repo, "tg-p1", agent_id="developer", agent_dir="agents/developer",
              metadata={"source_type": "telegram"}, project_id="p1")
        _make(repo, "tg-p2", agent_id="developer", agent_dir="agents/developer",
              metadata={"source_type": "telegram"}, project_id="p2")
        _make(repo, "slk-p1", agent_id="developer", agent_dir="agents/developer",
              metadata={"source_type": "slack"}, project_id="p1")

        instances, total, _ = repo.list(source="chat", project_id="p1")
        assert total == 2
        assert _ids(instances) == ["slk-p1", "tg-p1"]

    def test_source_root_only_under_descendant_loading(self, repo):
        """include_descendants=True: source filter applies to ROOTS only;
        descendants of in-scope roots are returned regardless of their
        own source_type. Mirrors the project_id lineage rule."""
        # root tg with a child having source_type=api (would be filtered
        # out if the source filter applied mid-BFS).
        _make(repo, "root-tg", agent_id="developer", agent_dir="agents/developer",
              metadata={"source_type": "telegram"})
        _make(repo, "child-api", agent_id="developer", agent_dir="agents/developer",
              parent_id="root-tg", metadata={"source_type": "api"})

        # Flat pagination (default) — every row is a root in this seed;
        # both should be returned because BFS doesn't run.
        instances, total, _ = repo.list(source="chat", include_descendants=False)
        # child-api has source_type=api, NOT in chat set → filtered out
        assert total == 1
        assert _ids(instances) == ["root-tg"]

        # With descendant loading: child is returned as a descendant of
        # the in-scope root, even though its own source_type would be
        # excluded if the filter applied to the BFS child query.
        instances, total, truncated = repo.list(
            source="chat", include_descendants=True, limit=10
        )
        assert total == 1  # only root-tg is a root that matches
        # child-api is returned as a descendant of root-tg despite its
        # own source_type=api.
        assert _ids(instances) == ["child-api", "root-tg"]


# ----- helper unit pin -------------------------------------------------


class TestBuildSourceConditionHelper:
    """The repository's private ``_build_source_condition`` helper is
    the dialect-aware predicate builder. Pin its return shape so
    regressions in the source-set assembly surface here, not as
    silent over/under-matching in the higher-level tests."""

    def test_helper_returns_none_for_empty_source(self, repo):
        from sqlmodel import Session
        with Session(repo.engine) as s:
            assert repo._build_source_condition(s, None) is None
            assert repo._build_source_condition(s, "") is None

    def test_helper_returns_clause_for_chat(self, repo):
        from sqlmodel import Session
        with Session(repo.engine) as s:
            cond = repo._build_source_condition(s, "chat")
            assert cond is not None
            # The clause must be an IN against the four-member set.
            # We don't introspect SQLAlchemy's compiled SQL here — the
            # behavioural tests above (TestSourceChatSentinel) cover
            # the actual matching; the helper test just pins that
            # "chat" returns a non-None clause.
            assert hasattr(cond, "right")  # ColumnElement

    def test_helper_returns_clause_for_specific_source(self, repo):
        from sqlmodel import Session
        with Session(repo.engine) as s:
            cond = repo._build_source_condition(s, "telegram")
            assert cond is not None
            assert hasattr(cond, "right")

    def test_helper_returns_in_clause_for_non_string_input(self, repo):
        """NIT: rename of the old ``test_helper_rejects_non_string_source``
        — the helper does NOT reject and does NOT stringify; the value
        flows verbatim into ``expr.in_(values)``. For ``source=42`` the
        clause is ``CAST(... AS VARCHAR) IN (42)`` which compiles
        cleanly but matches zero rows (the source_type column is TEXT,
        not INT). The route layer enforces ``str | None`` via
        FastAPI's type binding, so the helper's leniency is a
        defense-in-depth contract, not a normalization site.

        The new name says WHAT THE TEST OBSERVES (an IN clause is
        produced) rather than what it does NOT (rejection /
        stringification)."""
        from sqlmodel import Session
        with Session(repo.engine) as s:
            # Helper does not raise on a non-string; it passes the value
            # verbatim into expr.in_(values) (see repository.py:381).
            # This keeps
            # the helper's contract simple: ``source`` is a value, the
            # caller is responsible for typing it.
            cond = repo._build_source_condition(s, 42)  # type: ignore[arg-type]
            assert cond is not None
            assert hasattr(cond, "right")

# ------------------------------------------------------------------------------#
# Compile-dialect pin (no live PG required)
# ------------------------------------------------------------------------------#
#
# Relocated from tests/postgres/test_instance_source_filter_pg.py
# (MINOR-2 delta re-review): the PG-marked file's autouse
# _pg_truncate_tables fixture chains this class to the live pg_engine
# probe, so on hosts without PG the entire pin SKIPS -- defeating the
# no-PG safety net this class is supposed to provide. The pin now
# lives HERE (the unmarked SQLite suite) and runs under plain pytest.
# The test semantics are unchanged: build a stub PG engine against an
# unreachable port, compile the helper expression against the PG
# dialect, assert the safe jsonb_extract_path_text form is present and
# the buggy metadata -> 'source_type' + CAST shape is not.

class TestSourceConditionCompileDialect:
    """Compile the helper against the PG dialect and assert the safe
    ``jsonb_extract_path_text`` form is present and the buggy
    ``metadata -> 'source_type'`` + ``CAST AS VARCHAR`` shape is not.

    This is the regression pin that catches a future change that
    reverts the PG branch to the naive ``->`` + CAST form -- which
    silently returns 0 rows on production while keeping the SQLite
    path green. It does NOT need a live PG: it builds a
    ``postgresql.dialect()`` instance and compiles the helper
    expression against it via a stub engine.
    """

    def test_pg_dialect_uses_jsonb_extract_path_text(self):
        """The PG branch of ``_build_source_condition`` must compile
        to ``jsonb_extract_path_text(metadata, 'source_type')`` --
        the unquoted-text PG-native extractor. The naive
        ``metadata -> 'source_type'`` + ``CAST AS VARCHAR`` shape
        is the C1 bug and must not appear.

        The engine URL points at an unreachable port so it is never
        actually connected -- the test only compiles the SQLAlchemy
        expression against the PG dialect (no live PG required for
        the compile-time pin)."""
        # Unreachable port on purpose: we only need the dialect
        # metadata, not a live connection. The helper builds a
        # SQLAlchemy expression object without touching the network.
        stub_engine = create_engine(
            "postgresql+psycopg://stub:stub@127.0.0.1:1/stub"
        )
        repo = SQLModelInstanceRepository(stub_engine)
        with Session(stub_engine) as session:
            cond = repo._build_source_condition(session, "telegram")
        assert cond is not None
        compiled = str(
            cond.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        # Safe form: jsonb_extract_path_text returns unquoted TEXT.
        assert "jsonb_extract_path_text" in compiled, (
            f"Expected jsonb_extract_path_text in compiled SQL, got: {compiled}"
        )
        # The buggy form would compare against quoted JSON text:
        #   CAST((metadata -> 'source_type') AS VARCHAR) IN ('telegram')
        # which on PG yields '"telegram"' != 'telegram'. If the
        # compiled SQL contains this pattern, the C1 regression is
        # back -- fail loud.
        assert "metadata -> 'source_type'" not in compiled, (
            f"C1 regression: PG branch fell back to metadata -> 'source_type' "
            f"+ CAST (returns quoted JSON text). Compiled SQL: {compiled}"
        )

    def test_pg_dialect_chat_sentinel_includes_four_members(self):
        """``source='chat'`` on PG must IN-bind the four
        CHAT_SOURCE_TYPES members as unquoted literals. The
        buggy form would still bind the four members -- but the
        comparison would fail because the metadata side returns
        QUOTED text. This test pins the bind shape: literal
        text members, no quotes inside the strings."""
        stub_engine = create_engine(
            "postgresql+psycopg://stub:stub@127.0.0.1:1/stub"
        )
        repo = SQLModelInstanceRepository(stub_engine)
        with Session(stub_engine) as session:
            cond = repo._build_source_condition(session, "chat")
        assert cond is not None
        compiled = str(
            cond.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        # The four registry members must be bound as literals.
        for member in CHAT_SOURCE_TYPES:
            # Members are unquoted TEXT, not quoted JSON.
            assert f"'{member}'" in compiled, (
                f"Expected literal '{member}' in compiled SQL, got: {compiled}"
            )
        # No doubled-quote artifact of the C1 bug (the buggy form
        # would compare to '"telegram"' with embedded quotes; here
        # we just sanity-check the bind form).
        assert "jsonb_extract_path_text" in compiled
