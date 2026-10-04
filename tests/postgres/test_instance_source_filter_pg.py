"""PostgreSQL test for the ``source`` parameter on SQLModelInstanceRepository.list().

Why this test exists
--------------------

PostgreSQL is the **PRIMARY** production database for the daemon. The
instance ``source`` filter ships a dialect-aware predicate at
``daemon/repositories/instance/repository.py:_build_source_condition``:

* SQLite path: ``CAST(json_extract(metadata, '$.source_type') AS VARCHAR)``
  (SQLite's ``json_extract`` returns unquoted TEXT for string scalars)
* PostgreSQL path: ``jsonb_extract_path_text(metadata, 'source_type')``
  (PG-native unquoted JSONB text extractor)

The naïve ``metadata->'source_type'`` + ``CAST(... AS VARCHAR)`` form
is BUGGY on PG: ``->`` returns ``jsonb`` and ``CAST(jsonb AS VARCHAR)``
yields **QUOTED** JSON text (``'"telegram"'`` not ``'telegram'``), so
the ``IN`` comparison never matches and the source filter silently
returns 0 rows on production. The SQLite path is correct, which is
why the mirror test in ``tests/test_instance_source_filter.py`` stayed
green while the production feature shipped broken.

This file is the canonical C1 LIVE regression -- it exercises the
PG JSONB path against a real PostgreSQL backend so the production
code path (``jsonb_extract_path_text(metadata, 'source_type')``) is
end-to-end verified, not just compile-time pinned. The portable
compile-dialect pin (catches C1-style regressions WITHOUT requiring
a live PG) lives in the SQLite mirror test at
``tests/test_instance_source_filter.py::TestSourceConditionCompileDialect`` --
that file is unmarked so the pin runs under plain pytest on any
host, regardless of whether PG is reachable.

Run with::

    pytest tests/postgres/test_instance_source_filter_pg.py \\
        -m postgres --override-ini="addopts=" -v

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is not reachable.

What this test covers
---------------------

* **Headline (C1 regression)**: ``source='telegram'`` returns the
  telegram row on PG; the buggy ``-> + CAST`` form would return 0.
* **PG JSONB IN clause**: ``source='chat'`` returns all four
  registry members; the IN binds the unquoted strings.
* **PG exact match**: ``source='webhook'`` returns webhook; the chat
  sentinel does not falsely include webhook.
* **PG no-match**: ``source='rocketchat'`` returns 0 rows without
  raising.
* (Compile-dialect pin covered by the SQLite mirror test at
  ``tests/test_instance_source_filter.py::TestSourceConditionCompileDialect`` --
  moved there per MINOR-2 delta re-review so the no-PG safety net
  is wired into the unmarked suite, not chained to the live
  ``pg_engine`` probe that skips on hosts without PG.)

Out of scope (covered in the SQLite test)
-----------------------------------------

* Whitespace-only ``source=`` behavior — same Python branch on both
  backends (``if not source: return None``), covered in
  tests/test_instance_source_filter.py.
* Constant membership pins for ``CHAT_SOURCE_TYPES`` — covered in
  the mirror SQLite test.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance  # noqa: F401  (registers table)
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.instance import CHAT_SOURCE_TYPES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(pg_repository_factory) -> SQLModelInstanceRepository:
    """Real ``SQLModelInstanceRepository`` bound to the PG engine.

    Uses the standard ``pg_repository_factory`` from
    ``tests/postgres/conftest.py``. Cleanup between tests is handled by
    the autouse ``_pg_truncate_tables`` fixture in the conftest.
    """
    return pg_repository_factory(SQLModelInstanceRepository)


def _make(
    repo: SQLModelInstanceRepository,
    instance_id: str,
    agent_id: str,
    agent_dir: str,
    *,
    metadata: dict | None = None,
    parent_id: str | None = None,
    project_id: str | None = None,
    status: str = "idle",
):
    """Insert an instance via the repository (exercises PG JSONB writes)."""
    return repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        metadata=metadata or {},
        parent_id=parent_id,
        project_id=project_id,
        status=status,
    )


def _ids(instances) -> list[str]:
    return sorted(inst.instance_id for inst in instances)


@pytest.fixture
def seeded_repo(repo):
    """Seed instances covering every chat-source member + controls.

    Mirrors the SQLite ``seeded_repo`` layout so the two files can be
    compared side-by-side. ``exclude_kb`` defaults to True in
    ``list()`` so the developer-agent rows pass; we deliberately do
    not seed a KB agent here (the SQLite test pins the KB-exclusion
    branch).
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


# ---------------------------------------------------------------------------
# Headline test: PG JSONB source_type path
# ---------------------------------------------------------------------------


class TestSourceByJsonb:
    """Match against ``jsonb_extract_path_text(metadata, 'source_type')``
    (PG-native unquoted JSONB text extractor).

    The C1 bug is reproduced if the repository falls back to
    ``metadata -> 'source_type'`` + ``CAST AS VARCHAR`` — on PG this
    returns ``'"telegram"'`` (quoted JSON text) and the IN comparison
    yields 0 rows silently.
    """

    def test_source_telegram_matches_on_pg_jsonb(self, seeded_repo):
        """Headline C1 regression: a real PG must return the telegram
        row when ``source='telegram'``. Pre-fix, this returned 0 rows
        because the predicate compared ``'"telegram"' IN ('telegram')``
        on the quoted-JSON-text path."""
        instances, total, _ = seeded_repo.list(source="telegram")
        assert total == 2
        assert _ids(instances) == ["tg1", "tg2"]

    def test_source_slack_matches_on_pg_jsonb(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="slack")
        assert total == 1
        assert _ids(instances) == ["slk1"]

    def test_source_discord_matches_on_pg_jsonb(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="discord")
        assert total == 1
        assert _ids(instances) == ["dsc1"]

    def test_source_whatsapp_matches_on_pg_jsonb(self, seeded_repo):
        """Whatsapp is the ``forward-compat`` fourth member — must
        still match on PG even though no whatsapp adapter is
        registered. Regression-pin that the unquoted-text extractor
        works for the new member too."""
        instances, total, _ = seeded_repo.list(source="whatsapp")
        assert total == 1
        assert _ids(instances) == ["wat1"]

    def test_source_webhook_matches_on_pg_jsonb(self, seeded_repo):
        """Webhook is excluded from ``source='chat'`` but is a valid
        exact-match value. The PG path must return the webhook row
        for ``source='webhook'``."""
        instances, total, _ = seeded_repo.list(source="webhook")
        assert total == 1
        assert _ids(instances) == ["web1"]

    def test_source_scheduler_matches_on_pg_jsonb(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="scheduler")
        assert total == 1
        assert _ids(instances) == ["sched"]

    def test_source_unknown_returns_empty(self, seeded_repo):
        """An unknown source value is a valid input — just matches
        no rows. Must not raise on PG."""
        instances, total, _ = seeded_repo.list(source="rocketchat")
        assert total == 0
        assert instances == []


# ---------------------------------------------------------------------------
# source="chat" sentinel on PG
# ---------------------------------------------------------------------------


class TestSourceChatSentinelPg:
    """``source='chat'`` on PG must IN-bind all four CHAT_SOURCE_TYPES
    members and EXCLUDE webhook + scheduler + no-source rows."""

    def test_chat_returns_five_registry_rows_on_pg(self, seeded_repo):
        """``source='chat'`` on PG must IN-bind all four registry
        members and total 5: two telegram rows (tg1, tg2) plus one
        each of slack / discord / whatsapp. Pre-C1 this returned 0
        rows because the predicate compared the four unquoted
        literals against the QUOTED-JSON-text metadata side."""
        instances, total, _ = seeded_repo.list(source="chat")
        assert total == 5
        ids = _ids(instances)
        # All five chat rows must be present (whatsapp is the
        # forward-compat fourth member; webhook + scheduler + noMeta
        # are deliberately excluded).
        assert set(ids) == {"tg1", "tg2", "slk1", "dsc1", "wat1"}
        assert "web1" not in ids
        assert "sched" not in ids
        assert "noMeta" not in ids

    def test_chat_excludes_webhook_on_pg(self, seeded_repo):
        instances, _, _ = seeded_repo.list(source="chat")
        assert "web1" not in _ids(instances)

    def test_chat_excludes_scheduler_on_pg(self, seeded_repo):
        instances, _, _ = seeded_repo.list(source="chat")
        assert "sched" not in _ids(instances)

    def test_chat_excludes_no_source_type_on_pg(self, seeded_repo):
        """Instances without a ``source_type`` key are not chat
        origins and must be excluded on PG too."""
        instances, _, _ = seeded_repo.list(source="chat")
        assert "noMeta" not in _ids(instances)


# ---------------------------------------------------------------------------
# Disabled / no-filter cases (defensive — Python branch is dialect-free)
# ---------------------------------------------------------------------------


class TestSourceDisabledPg:
    """``None`` / empty ``source=`` apply no filter on PG either."""

    def test_source_none_returns_all(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source=None)
        assert total == 8

    def test_source_empty_returns_all(self, seeded_repo):
        instances, total, _ = seeded_repo.list(source="")
        assert total == 8


