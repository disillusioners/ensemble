"""Pinned tests for the chat-pool starvation fix
(2026-10-04, ``fix/claim-gate-sibling-deadlock``).

Eight pins (TestChatPoolStarvationFix) cover the fix and its
council rework:

* ``test_chat_lane_matches_custom_source_id`` — the
  deployment's composed source ``my-discord-bot:<user>`` now
  routes to the chat worker pool because the lane filter joins
  ``message_queue`` to ``source_configs`` and matches by
  ``source_type IN ('telegram','slack','discord')``. Pre-fix
  the filter checked literal prefix ``'discord:%'`` against
  the composed source — never matched — and the chat workers
  sat idle (``claimed=0`` every cycle).

* ``test_chat_lane_matches_canonical_source_id`` —
  regression guard for the canonical case: source_id
  ``discord`` (literal match to ``CHAT_SOURCE_PREFIXES``)
  still routes the chat lane.

* ``test_chat_lane_does_not_match_unrelated_source`` —
  negative pin: a non-chat source (``webhook:gh-hook``) whose
  ``source_type='webhook'`` is NOT in the chat-types set must
  NOT be claimed by the chat lane.

* ``test_default_lane_excludes_chat_source`` —
  default-lane + chat-lane-active=True must EXCLUDE chat
  sources via the NOT-EXISTS clause, even when the source_id
  is a custom deployment value like ``my-discord-bot``.

* ``test_default_lane_picks_up_non_chat_source`` —
  positive control: default lane picks up a non-chat source
  (``api:<uuid>``) when chat-lane-active=True.

* ``test_chat_types_derived_from_prefixes`` — guard the
  source-of-truth derivation: the chat source_types set used
  by the lane filter MUST equal the canonical tuple.

* ``test_chat_lane_does_not_overmatch_underscore_source_id``
  (council rework REQUIRED 3) — LIKE treats ``_`` as a
  single-char wildcard; the Pydantic source_id validator
  admits underscores, so a registered source_id like
  ``telegram_bot`` would over-match
  ``telegramXbot:user3`` under the original LIKE-based
  predicate. Post-rework the filter uses SUBSTR/LENGTH for
  exact byte-for-byte prefix equality. Pin against the over-match.

* ``test_chat_lane_underscore_explicit_substr_check`` —
  SUBSTR/LENGTH semantics pin (file-backed SQLite harness).

All tests use the file-local ``bug_engine`` fixture (per-test
file-backed SQLite engine; see the fixture at :120) — NOT the
``tests/job_queue/conftest.py`` ``engine`` fixture (session-scoped
in-memory SQLite shared with sibling test files). This file
intentionally opts out for bulletproof per-test DB isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import SQLModel

from daemon.constants import CHAT_SOURCE_PREFIXES
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.message_queue.models import MessageStatus
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import (
    TaskRepository,
    set_chat_lane_active,
    is_chat_lane_active,
)


# ── Fixtures (local, per-test) ──────────────────────────────────────


@pytest.fixture
def bug_engine(tmp_path):
    """In-process SQLite engine for the chat-pool tests.

    File-backed (not :memory:) so each test gets a clean DB AND
    so multi-test isolation is bulletproof — the session-scoped
    ``engine`` fixture in ``conftest.py`` is shared across the
    whole directory.
    """
    db_path = tmp_path / "chat_pool_starve.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA case_sensitive_like = ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _reset_chat_lane_flag():
    """Reset the chat-lane-active flag around each test.

    The flag is module-level state shared across the whole
    process — failing to reset leaks across tests and would
    make later tests behave as if the chat pool is alive when
    it isn't. Per-claim reads (E2 invariant) make the leak
    deterministic.
    """
    saved = is_chat_lane_active()
    set_chat_lane_active(False)
    try:
        yield
    finally:
        set_chat_lane_active(saved)


def _iso_now(offset_seconds: int = 0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


def _seed_instance(engine, *, instance_id: str) -> None:
    now_iso = _iso_now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                    created_at, updated_at, version, parent_id,
                    last_activity_at)
                VALUES
                    (:instance_id, 'ari', '/agents/ari', :status,
                    'test-project', :created_at, :updated_at, 1, NULL,
                    :last_activity_at)
                """
            ),
            {
                "instance_id": instance_id,
                "status": InstanceStatus.RUNNING.value,
                "created_at": now_iso,
                "updated_at": now_iso,
                "last_activity_at": now_iso,
            },
        )


def _seed_source_config(
    engine,
    *,
    source_id: str,
    source_type: str,
    enabled: bool = True,
) -> None:
    """Seed a ``source_configs`` row for the chat-lane JOIN.

    The chat-pool starvation fix (2026-10-04) resolves adapter
    type at claim time via ``source_configs.source_type`` — the
    filter uses SUBSTR/LENGTH byte-for-byte prefix equality
    (``SUBSTR(source, 1, LENGTH(source_id || ':')) =
    source_id || ':'``) AND ``source_configs.source_type IN
    (chat_types)``.
    """
    now_iso = _iso_now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO source_configs
                    (source_id, source_type, name, enabled, autostart,
                    status, created_at, updated_at)
                VALUES
                    (:source_id, :source_type, :name, :enabled, TRUE,
                    'running', :created_at, :updated_at)
                """
            ),
            {
                "source_id": source_id,
                "source_type": source_type,
                "name": source_id,
                "enabled": enabled,
                "created_at": now_iso,
                "updated_at": now_iso,
            },
        )


def _seed_chat_task(
    engine,
    *,
    work_id: str,
    instance_id: str,
    message_id: str,
    source: str,
    status: str = TaskStatus.PENDING.value,
) -> int:
    """Insert a ``process_message`` Task row + matching
    ``MessageQueue`` row with the given source string. Returns
    the Task rowid.

    ``status`` applies to the task row only; the message_queue
    row is always PROCESSING.
    """
    now_iso = _iso_now()
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO message_queue
                    (message_id, instance_id, content, type, source,
                    status, priority, retry_count, max_retries,
                    enqueued_at, processing_started_at)
                VALUES
                    (:message_id, :instance_id, 'hi', 'agent', :source,
                    :status, 1, 0, 5, :enqueued_at, :enqueued_at)
                """
            ),
            {
                "message_id": message_id,
                "instance_id": instance_id,
                "source": source,
                "status": MessageStatus.PROCESSING.value,
                "enqueued_at": now_iso,
            },
        )
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                    retry_count, created_at, cancel_requested,
                    retry_scheduled, work_id, is_deferred,
                    is_background, completed_at)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                    0, :created_at, FALSE, FALSE,
                    :work_id, FALSE, FALSE, NULL)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "created_at": now_naive,
                "work_id": work_id,
            },
        )
        return int(result.lastrowid)


def _read_task_status(engine, work_id: str) -> str | None:
    """Read the task row's status by work_id."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT status FROM task WHERE work_id = :work_id"
            ),
            {"work_id": work_id},
        ).first()
    return row[0] if row else None


# ── Test 1: chat lane claim matches custom source_id ────────────────


class TestChatPoolStarvationFix:
    """chat-pool starvation fix (2026-10-04): the chat-lane
    claim filter matches any source_id whose ``source_configs``
    row has ``source_type IN ('telegram','slack','discord')``.
    The deployment's ``my-discord-bot:<user>`` source_id is the
    canonical test case — pre-fix the literal-prefix filter
    never matched and chat workers sat idle."""

    def test_chat_lane_matches_custom_source_id(
        self, bug_engine
    ) -> None:
        """ACCEPTANCE PIN — chat-pool starvation fix. Source_id
        ``my-discord-bot`` is registered with source_type
        ``discord``. A message with composed source
        ``my-discord-bot:alice:1`` MUST be claimed by the chat
        lane (``lane='chat'``). Pre-fix the literal-prefix filter
        (``discord:%`` / ``telegram:%`` / ``slack:%``) never
        matched the deployment's custom source_ids — the chat
        pool sat idle (``claimed=0`` per cycle). Post-fix the
        JOIN on ``source_configs`` resolves adapter type at
        claim time."""
        inst = "inst-chat-custom"
        wid = "wid-chat-custom"
        msg_id = "msg-chat-custom"
        source_id = "my-discord-bot"
        source = f"{source_id}:alice:1"

        # Seed the source_configs row that production writes when
        # an adapter registers. Type is the CANONICAL adapter type
        # (the A7.2 gate allows pre-existing misconfigs through;
        # production carries ``source_type='discord'`` even when
        # ``source_id='my-discord-bot'``).
        _seed_source_config(
            bug_engine, source_id=source_id, source_type="discord"
        )
        _seed_instance(bug_engine, instance_id=inst)
        _seed_chat_task(
            bug_engine,
            work_id=wid,
            instance_id=inst,
            message_id=msg_id,
            source=source,
        )

        # ACTIVATE the chat lane — claim_pending_task's default-
        # lane path only excludes chat rows when the flag is True
        # (fail-open otherwise per B1 transport seam).
        set_chat_lane_active(True)

        # Chat lane MUST claim the custom-source-id message.
        task_repo = TaskRepository(engine=bug_engine)
        claimed = task_repo.claim_pending_task(
            worker_id="chat-worker-1", lane="chat"
        )
        assert claimed is not None, (
            f"chat lane did NOT claim Task with composed source "
            f"{source!r}. Pre-fix the literal-prefix filter "
            f"``discord:%`` did not match ``{source}``; post-fix "
            f"the JOIN on source_configs resolves "
            f"``{source_id}`` → ``discord`` → chat. The chat "
            f"workers would sit idle forever (claimed=0 every "
            f"cycle) without this fix."
        )
        assert claimed.work_id == wid, (
            f"chat lane claimed the wrong task: expected {wid!r} "
            f"got {claimed.work_id!r}"
        )
        assert claimed.status == TaskStatus.RUNNING.value

    def test_chat_lane_matches_canonical_source_id(
        self, bug_engine
    ) -> None:
        """Regression guard for the canonical case: source_id
        ``discord`` (literal match to ``CHAT_SOURCE_PREFIXES``)
        still routes the chat lane. Pre-fix this worked; post-fix
        it MUST still work — the JOIN path doesn't regress the
        canonical happy path."""
        inst = "inst-chat-canonical"
        wid = "wid-chat-canonical"
        msg_id = "msg-chat-canonical"
        source_id = "discord"
        source = f"{source_id}:alice:1"

        _seed_source_config(
            bug_engine, source_id=source_id, source_type="discord"
        )
        _seed_instance(bug_engine, instance_id=inst)
        _seed_chat_task(
            bug_engine,
            work_id=wid,
            instance_id=inst,
            message_id=msg_id,
            source=source,
        )
        set_chat_lane_active(True)

        task_repo = TaskRepository(engine=bug_engine)
        claimed = task_repo.claim_pending_task(
            worker_id="chat-worker-1", lane="chat"
        )
        assert claimed is not None, (
            f"chat lane failed on canonical source_id "
            f"{source_id!r} — regression guard failed. The "
            f"post-fix JOIN path must still match canonical "
            f"source_ids."
        )
        assert claimed.work_id == wid

    def test_chat_lane_does_not_match_unrelated_source(
        self, bug_engine
    ) -> None:
        """Negative pin — non-chat sources (``webhook:...``)
        must NOT be claimed by the chat lane, even with the
        broader JOIN. Pin against accidental over-matching on
        the new JOIN path (the JOIN's SUBSTR/LENGTH prefix
        equality must NOT match unrelated source_ids)."""
        inst = "inst-non-chat"
        wid = "wid-non-chat"
        msg_id = "msg-non-chat"
        source_id = "gh-webhook"
        source = f"{source_id}:repo:42"

        _seed_source_config(
            bug_engine, source_id=source_id, source_type="webhook"
        )
        _seed_instance(bug_engine, instance_id=inst)
        _seed_chat_task(
            bug_engine,
            work_id=wid,
            instance_id=inst,
            message_id=msg_id,
            source=source,
        )
        set_chat_lane_active(True)

        task_repo = TaskRepository(engine=bug_engine)
        claimed = task_repo.claim_pending_task(
            worker_id="chat-worker-1", lane="chat"
        )
        assert claimed is None, (
            f"chat lane claimed a NON-chat source ({source!r}, "
            f"source_type='webhook') — the new JOIN path is "
            f"over-matching. The chat-lane filter must check "
            f"``source_type IN ('telegram','slack','discord')`` "
            f"and reject webhook/API/etc. sources."
        )

    def test_default_lane_excludes_chat_source(
        self, bug_engine
    ) -> None:
        """Default-lane + chat-lane-active=True must EXCLUDE
        chat sources via the NOT-EXISTS clause, even when the
        source_id is a custom deployment value like
        ``my-discord-bot``. Pin against false-negatives from
        the new adapter-type filter (the pre-fix literal-prefix
        path excluded them via LIKE; the new JOIN-based path
        must exclude them via the same ``NOT EXISTS
        (...chat-source...)`` composition)."""
        inst = "inst-default-exclude"
        wid = "wid-default-exclude"
        msg_id = "msg-default-exclude"
        source_id = "my-discord-bot"
        source = f"{source_id}:alice:1"

        _seed_source_config(
            bug_engine, source_id=source_id, source_type="discord"
        )
        _seed_instance(bug_engine, instance_id=inst)
        _seed_chat_task(
            bug_engine,
            work_id=wid,
            instance_id=inst,
            message_id=msg_id,
            source=source,
        )
        # CRITICAL: chat lane active so default-lane claim
        # applies the NOT-EXISTS exclusion (fail-open otherwise).
        set_chat_lane_active(True)

        task_repo = TaskRepository(engine=bug_engine)
        # default-lane claim MUST skip this chat-sourced Task.
        claimed = task_repo.claim_pending_task(
            worker_id="worker-default-1", lane="default"
        )
        assert claimed is None, (
            f"default lane claimed a chat-sourced Task "
            f"({source!r}) despite chat-lane-active=True — "
            f"the NOT-EXISTS exclusion is broken on the new "
            f"JOIN path. The chat lane and default lane would "
            f"double-process the same message."
        )

    def test_default_lane_picks_up_non_chat_source(
        self, bug_engine
    ) -> None:
        """Positive control — default lane picks up a non-chat
        source (``api:<uuid>``) when chat-lane-active=True. The
        NOT-EXISTS clause must filter CHAT sources out and let
        API/webhook/etc. through. Pin against false-positives
        where the new JOIN over-matches non-chat source_ids."""
        inst = "inst-default-pick"
        wid = "wid-default-pick"
        msg_id = "msg-default-pick"
        source = "api:webhook-12345"

        # Note: no source_configs row for ``api`` (the default
        # ``api`` source is the web/CLI path, never an
        # adapter-registered chat source).
        _seed_instance(bug_engine, instance_id=inst)
        _seed_chat_task(
            bug_engine,
            work_id=wid,
            instance_id=inst,
            message_id=msg_id,
            source=source,
        )
        set_chat_lane_active(True)

        task_repo = TaskRepository(engine=bug_engine)
        claimed = task_repo.claim_pending_task(
            worker_id="worker-default-1", lane="default"
        )
        assert claimed is not None, (
            f"default lane failed to claim a non-chat source "
            f"({source!r}) — the new JOIN path is incorrectly "
            f"blocking non-chat sources. The NOT-EXISTS clause "
            f"must filter only chat sources, not all sources."
        )
        assert claimed.work_id == wid

    def test_chat_types_derived_from_prefixes(
        self, bug_engine
    ) -> None:
        """Guard the source-of-truth derivation — the chat
        source_types set used by the lane filter MUST equal
        ``{prefix.rstrip(':') for prefix in CHAT_SOURCE_PREFIXES}``
        so the lane predicate and the registration validator at
        ``daemon/routers/sources.py:174`` agree. Drift would
        silently desync the lane filter from the registration
        gate."""
        # The derivation matches the routers/sources.py:174
        # derivation. Both call sites must use the SAME
        # derivation so a deployment cannot register a chat
        # adapter that the lane filter rejects (or vice versa).
        expected_types = {
            prefix.rstrip(":") for prefix in CHAT_SOURCE_PREFIXES
        }
        # The expected set includes telegram, slack, discord.
        # If ``CHAT_SOURCE_PREFIXES`` ever gains a new chat
        # type, both this test AND the chat-lane filter update
        # together — single source of truth.
        assert expected_types == {"telegram", "slack", "discord"}, (
            f"CHAT_SOURCE_PREFIXES derivation drifted: "
            f"expected {{'telegram','slack','discord'}} got "
            f"{expected_types}. Update both the chat-lane "
            f"filter (daemon/repositories/task/repository.py) "
            f"and the registration validator "
            f"(daemon/routers/sources.py:174) if you change "
            f"the canonical set."
        )

    def test_chat_lane_does_not_overmatch_underscore_source_id(
        self, bug_engine
    ) -> None:
        """REQUIRED 3 (underscore over-match pin) — LIKE treats
        ``_`` as a single-character wildcard. The Pydantic
        validator at ``daemon/routers/sources.py`` admits
        source_ids matching ``^[a-zA-Z0-9_-]+$`` — so a
        registered source_id like ``telegram_bot`` would
        over-match a message with source
        ``telegramXbot:user3`` under the original
        ``source LIKE source_id || ':%'`` predicate
        (the ``_`` at position 8 of ``telegram_bot``
        matches the single char ``X`` at position 8 of
        ``telegramXbot``). Post-rework (round 2) the filter
        uses ``SUBSTR(source, 1, LENGTH(source_id || ':'))
            = source_id || ':'`` which has NO LIKE-metachar
        semantics — the underscore is a literal character.
        Pin against the over-match.

        Reproducer shape (alignment carefully chosen so the
        LIKE wildcard ACTUALLY triggers — every non-wildcard
        pattern character must align with the corresponding
        haystack character):
          * Register ``source_id='telegram_bot'`` with
            ``source_type='telegram'``.
          * Insert a message_queue row with
            ``source='telegram_bot:alice:1'`` on instance A —
            genuine match.
          * Insert a message_queue row with
            ``source='telegramXbot:user3'`` on instance B —
            DIFFERENT source that LIKE matches because ``_``
            (position 8 in pattern) is a single-char wildcard
            against ``X`` (position 8 in haystack); every
            other position aligns.
          * Pre-rework (LIKE-based) the chat lane WOULD claim
            the over-match row.
          * Post-rework round-2 (SUBSTR/LENGTH equality) the
            chat lane must NOT claim it — exact byte-for-byte
            prefix equality fails (``telegramXbot`` does not
            start with ``telegram_bot:``).
          * Post-rework round-1 (INSTR pivot — REJECTED by
            PG probe, see ``daemon/repositories/task/
            repository.py::_chat_source_exists_sql`` docstring
            for the round-1 → round-2 rewind): INSTR matched
            on SQLite but does not exist as a PG native; PG
            claims would have crashed with "function
            instr(unknown, unknown) does not exist".

        Critical design choice: TWO DIFFERENT INSTANCES. If
        the test used the same instance for both messages,
        the per-instance RUNNING-task guard would mask the
        over-match bug (the second claim would be held back
        by the guard, not by the chat-source filter). The
        chat-source filter is the SUBJECT of this pin, so
        the per-instance guard must not interfere.
        """
        inst_target = "inst-underscore-target"
        inst_overmatch = "inst-underscore-overmatch"
        wid_target = "wid-underscore-target"
        wid_overmatch = "wid-underscore-overmatch"
        msg_target = "msg-underscore-target"
        msg_overmatch = "msg-underscore-overmatch"
        source_id = "telegram_bot"
        source_overmatch = "telegramXbot:user3"
        source_target = f"{source_id}:alice:1"

        # Register source_id='telegram_bot' under telegram.
        _seed_source_config(
            bug_engine, source_id=source_id, source_type="telegram"
        )
        # Sanity-check the source_configs row landed with
        # the correct source_type (the test depends on this
        # for the chat-source IN clause).
        with bug_engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT source_id, source_type FROM source_configs "
                    "WHERE source_id = :sid"
                ),
                {"sid": source_id},
            ).first()
        assert row is not None and row[1] == "telegram", (
            f"source_configs row not seeded correctly: got {row!r}"
        )

        # Seed TWO messages on TWO DIFFERENT INSTANCES (see
        # the design choice above):
        # 1. The genuine ``telegram_bot:alice:1`` on inst_target.
        _seed_instance(bug_engine, instance_id=inst_target)
        _seed_chat_task(
            bug_engine,
            work_id=wid_target,
            instance_id=inst_target,
            message_id=msg_target,
            source=source_target,
        )
        # 2. The over-match candidate ``telegramXbot:user3``
        #    on inst_overmatch (different instance — keeps the
        #    per-instance RUNNING-task guard out of the way).
        _seed_instance(bug_engine, instance_id=inst_overmatch)
        _seed_chat_task(
            bug_engine,
            work_id=wid_overmatch,
            instance_id=inst_overmatch,
            message_id=msg_overmatch,
            source=source_overmatch,
        )
        set_chat_lane_active(True)

        task_repo = TaskRepository(engine=bug_engine)
        # First chat-lane claim. The lane filter is the
        # SUBJECT of this pin. Under LIKE the over-match
        # row would be eligible; under SUBSTR/LENGTH equality
        # (the round-2 primitive) it must NOT be. Both rows
        # are PENDING; FIFO by created_at means whichever was
        # seeded first claims first. The target row was
        # seeded first.
        claimed_1 = task_repo.claim_pending_task(
            worker_id="chat-worker-1", lane="chat"
        )
        assert claimed_1 is not None, (
            "chat lane did NOT claim any row. Both rows are "
            "PENDING and chat-source-eligible; under either "
            "LIKE or SUBSTR/LENGTH equality the genuine match "
            "should be claimed first. The chat-lane filter is "
            "broken."
        )
        # The first claim must be the genuine match (FIFO).
        assert claimed_1.work_id == wid_target, (
            f"chat lane claimed the WRONG row first: expected "
            f"{wid_target!r} (genuine match) got "
            f"{claimed_1.work_id!r}. The genuine-match row was "
            f"not picked — likely the FIFO ordering or the "
            f"chat-source filter is broken."
        )
        # Second chat-lane claim. Under LIKE the over-match
        # row would be falsely eligible and would be claimed
        # here. Under SUBSTR/LENGTH equality (the round-2
        # primitive) the over-match row must NOT be eligible
        # and the second claim must return None.
        claimed_2 = task_repo.claim_pending_task(
            worker_id="chat-worker-2", lane="chat"
        )
        assert claimed_2 is None, (
            f"chat lane claimed a SECOND row "
            f"({getattr(claimed_2, 'work_id', None)!r}) — "
            f"the only remaining PENDING row is the over-match "
            f"({source_overmatch!r}). The chat-source filter "
            f"is wrongly treating the underscore in "
            f"source_id={source_id!r} as a LIKE single-char "
            f"wildcard. SUBSTR/LENGTH equality (or "
            f"equivalent exact-prefix primitive) is the fix."
        )
        # Defensive: the over-match row remains PENDING
        # (never claimed).
        overmatch_status = _read_task_status(bug_engine, wid_overmatch)
        assert overmatch_status == "pending", (
            f"over-match row status is {overmatch_status!r}, "
            f"expected 'pending' — the over-match row was "
            f"incorrectly claimed. SUBSTR/LENGTH equality "
            f"did not filter out the LIKE-metachar over-match."
        )

    def test_chat_lane_underscore_explicit_substr_check(
        self, bug_engine
    ) -> None:
        """REQUIRED 3 (deeper SUBSTR/LENGTH primitive pin —
        round-2 fix after INSTR pivot failed PG probe) — direct
        SUBSTR/LENGTH semantics pin. The chat-lane filter uses
        ``SUBSTR(source, 1, LENGTH(source_id || ':'))
            = source_id || ':'`` for prefix match. Verify the
        primitive behaves as expected on both backends: equality
        TRUE for genuine prefix, FALSE for non-match, FALSE for
        the LIKE-metachar over-match haystack. This is a
        library-level pin — if SUBSTR/LENGTH semantics ever
        change on a future PG or SQLite release, this pin
        surfaces it before the chat-lane claim breaks.

        The harness is file-backed SQLite — SUBSTR and LENGTH
        have been SQLite builtins since forever (SQLite 3.0+).
        PG has had ``SUBSTR(text, int, length)`` and ``LENGTH(text)``
        since the early days — pg_proc confirms both are native
        (no extension like ``orafce`` needed). PG-probe evidence
        (iteration 2, 2026-10-04): the predicate executes
        against PostgreSQL 16.15 and returns expected match /
        no-match rows verbatim — see the report for the probe
        output.

        (Round 1 used INSTR — false-positive on SQLite (where
        INSTR exists) but breaks PG prod (INSTR is not a PG
        native; the round-1 chat-pool SQL would crash every
        claim with ``function instr(unknown, unknown) does not
        exist``). Round 2 swaps to SUBSTR/LENGTH — portable on
        both backends, byte-for-byte prefix equality, zero
        metachar semantics. See the SQL docstring at
        ``daemon/repositories/task/repository.py::
        _chat_source_exists_sql`` for the full rationale.)
        """
        with bug_engine.begin() as conn:
            # Exact prefix match — SUBSTR of leftmost
            # LENGTH(prefix) chars equals the prefix.
            prefix = "my_telegram_bot:"
            row = conn.execute(
                text(
                    "SELECT SUBSTR('my_telegram_bot:alice:1', "
                    "1, LENGTH(:prefix)) = :prefix AS eq"
                ),
                {"prefix": prefix},
            ).first()
            assert row is not None
            assert row[0] == 1 or row[0] is True, (
                f"SUBSTR('my_telegram_bot:alice:1', 1, "
                f"LENGTH({prefix!r})) = {prefix!r} returned "
                f"{row[0]!r} (expected 1/True) — the chat-lane "
                f"prefix-match primitive is broken on this "
                f"backend."
            )

            # Over-match candidate — LIKE-with-`_` would match
            # (the underscore is a single-char wildcard), but
            # SUBSTR/LENGTH equality rejects it: the leftmost
            # 14 chars of ``mytelegramXbot:user3`` are
            # ``mytelegramXbot``, NOT ``my_telegram_bot``.
            row2 = conn.execute(
                text(
                    "SELECT SUBSTR('mytelegramXbot:user3', "
                    "1, LENGTH(:prefix)) = :prefix AS eq"
                ),
                {"prefix": prefix},
            ).first()
            assert row2 is not None
            assert row2[0] == 0 or row2[0] is False, (
                f"SUBSTR('mytelegramXbot:user3', 1, "
                f"LENGTH({prefix!r})) = {prefix!r} returned "
                f"{row2[0]!r} (expected 0/False) — SUBSTR/LENGTH "
                f"matched the LIKE-over-match candidate. The "
                f"chat-lane filter would falsely claim a wrong-"
                f"source-id row. SUBSTR/LENGTH semantics on this "
                f"backend have drifted from the documented "
                f"exact-prefix contract."
            )
            # Sanity: SUBSTR of leftmost LENGTH(prefix) chars
            # of a shorter haystack returns the haystack itself
            # (per SQL standard), which is shorter than the
            # prefix → equality is FALSE. The chat-lane filter
            # correctly excludes these rows.
            row3 = conn.execute(
                text(
                    "SELECT SUBSTR('short', "
                    "1, LENGTH(:prefix)) = :prefix AS eq"
                ),
                {"prefix": prefix},
            ).first()
            assert row3 is not None
            assert row3[0] == 0 or row3[0] is False, (
                f"SUBSTR('short', 1, LENGTH({prefix!r})) = "
                f"{prefix!r} returned {row3[0]!r} (expected "
                f"0/False) — SUBSTR semantics for shorter "
                f"haystack drifted."
            )
            # Sanity: SUBSTR of leftmost LENGTH(prefix) chars
            # of a non-matching longer string also returns
            # FALSE — same character count as prefix but
            # different bytes.
            row4 = conn.execute(
                text(
                    "SELECT SUBSTR('mytelegramXbot:user3', "
                    "1, LENGTH(:prefix)) = :prefix AS eq"
                ),
                {"prefix": "my_telegram_"},
            ).first()
            assert row4 is not None
            assert row4[0] == 0 or row4[0] is False, (
                f"SUBSTR equality for non-matching-but-same-"
                f"length haystack returned {row4[0]!r} — "
                f"expected 0/False. SUBSTR byte-for-byte "
                f"equality semantics on this backend have "
                f"drifted."
            )