"""Facade-signature tests: ``InstanceManager.list_instances`` must accept AND
forward the new ``source`` kwarg to the lifecycle service.

Background: the Projects tab added a special "Chat" tab that filters
instances to chat-source roots (telegram / slack / discord / whatsapp).
The wire-level parameter is a ``source`` query arg on ``GET /api/instances``
(``"chat"`` is the special sentinel; any other string is a single
``source_type`` value). This file pins the facade seam with REAL calls
against a spied lifecycle service — deliberately NOT
``inspect.getsource`` source-grep assertions:

  1. the kwarg exists on the facade signature (a real call with
     ``source="chat"`` would raise ``TypeError`` otherwise);
  2. the kwarg is forwarded to the lifecycle service with the caller's
     value (mirrors the ``include_descendants`` pin in
     ``test_manager_list_instances_include_descendants.py``);
  3. the default forwards ``None`` so existing internal callers are
     unaffected by the facade change (back-compat contract);
  4. the facade remains a pass-through for the result tuple (3-tuple
     ``(instances, total, truncated)`` after the ``include_descendants``
     fix).

``TestFacadeStatusForwarding`` (F12, nest-ops-shakedown defect patch)
pins the SAME seam for the ``status`` kwarg: the facade forwards the
caller's status verbatim, and the default forwards ``None``. The
manager→lifecycle seam was already covered for ``source``; the
lifecycle→REPOSITORY seam (where the status filter was actually
DROPPED) carries its own first-ever pin in
``tests/unit/test_instance_read_shakedown_defects.py``.

Pattern parallels ``tests/unit/test_manager_list_instances_include_descendants.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from daemon.manager import InstanceManager


def _facade_with_lifecycle_spy() -> tuple[InstanceManager, MagicMock]:
    """Build a bare facade whose lifecycle service seam is spied.

    ``InstanceManager.__new__`` skips ``__init__`` entirely (the
    established pattern in
    ``tests/unit/test_manager_enqueue_message_work_id_required.py`` and
    ``tests/unit/test_phase4_manager_decomposition.py``). The spy replaces
    the whole ``_lifecycle_service`` OBJECT, with a
    :class:`~unittest.mock.MagicMock` standing in for its
    ``list_instances`` method — the facade calls
    ``self._lifecycle_service.list_instances(...)``, so that method-level
    mock is what receives (and records) the forwarded kwargs.
    """
    manager = InstanceManager.__new__(InstanceManager)
    service = MagicMock()
    list_instances_sentinel = ([{"instance_id": "i-1"}], 1, False)
    service.list_instances = MagicMock(return_value=list_instances_sentinel)
    manager._lifecycle_service = service
    return manager, service.list_instances


class TestFacadeSourceForwarding:
    """The facade must accept ``source`` and forward it verbatim."""

    def test_accepts_source_chat_and_forwards(self):
        """A real call with ``source="chat"`` binds (no TypeError) and
        the lifecycle service receives the new kwarg with the caller's
        value."""
        manager, spy = _facade_with_lifecycle_spy()

        result = manager.list_instances(
            limit=10,
            offset=0,
            project_id=None,
            exclude_kb=True,
            include_descendants=False,
            search=None,
            order="activity",
            source="chat",
        )

        spy.assert_called_once()
        forwarded = spy.call_args.kwargs
        assert forwarded["source"] == "chat"
        # Result tuple is unchanged — same 3-tuple shape.
        assert result == ([{"instance_id": "i-1"}], 1, False)

    def test_accepts_specific_source_and_forwards(self):
        """A real call with a specific ``source_type`` value (not the
        special ``"chat"`` sentinel) binds and forwards verbatim —
        the Projects tab surface is a UI affordance, but the BE also
        accepts direct source filters (e.g. agent tools)."""
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(source="telegram")

        forwarded = spy.call_args.kwargs
        assert forwarded["source"] == "telegram"

    def test_omitted_source_forwards_none_default(self):
        """Omitting the kwarg forwards ``source=None`` — the existing
        internal callers (manager cache cleanup, fuzzy match, project
        delete, agent tool ``list_instances``) MUST be unaffected by
        the facade change. A regression that defaulted to ``"chat"``
        or to any specific value would surface here."""
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(limit=5)

        forwarded = spy.call_args.kwargs
        assert forwarded["source"] is None
        # Existing neighbours still forwarded (defense-in-depth).
        assert forwarded["limit"] == 5
        assert forwarded["include_descendants"] is False  # facade default

    def test_explicit_none_forwards_none(self):
        """Explicit ``source=None`` is the same as omitting — both
        mean "no source filter". A regression that treated explicit
        None as the "chat" sentinel would surface here."""
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(limit=10, source=None)

        forwarded = spy.call_args.kwargs
        assert forwarded["source"] is None

    def test_source_forwards_alongside_all_neighbours(self):
        """Sanity: pre-existing keyword neighbours (``limit``,
        ``offset``, ``project_id``, ``exclude_kb``, ``include_descendants``,
        ``search``, ``order``) still forward through the facade. The
        new ``source`` kwarg must NOT disturb the established
        forwarding style."""
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(
            limit=25,
            offset=10,
            project_id="proj-1",
            exclude_kb=False,
            include_descendants=True,
            search="refactor",
            order="activity",
            source="chat",
        )

        forwarded = spy.call_args.kwargs
        assert forwarded["limit"] == 25
        assert forwarded["offset"] == 10
        assert forwarded["project_id"] == "proj-1"
        assert forwarded["exclude_kb"] is False
        assert forwarded["include_descendants"] is True
        assert forwarded["search"] == "refactor"
        assert forwarded["order"] == "activity"
        assert forwarded["source"] == "chat"


class TestFacadeStatusForwarding:
    """F12 — the facade must accept ``status`` and forward it verbatim.

    PLANNED MUTATION (executed by the tester's Phase-4 mutation cycle):
    drop ``status=status`` from the facade's ``list_instances`` call into
    the lifecycle service — this class goes RED (the forwarded kwarg is
    missing / ``None``).

    Why it matters: the ``status`` filter is the ONLY server-side filter
    the ``instance_read`` census exposes. The facade already forwarded it;
    the LIFECYCLE dropped it on the way to the repository (F12's real
    defect). This class pins the half that was already true, so the two
    halves cannot drift apart again — the lifecycle→repository half is
    pinned in ``tests/unit/test_instance_read_shakedown_defects.py``.
    """

    def test_accepts_status_and_forwards(self):
        """A real call with ``status="running"`` binds (no TypeError) and
        the lifecycle service receives the caller's value."""
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(status="running")

        forwarded = spy.call_args.kwargs
        assert forwarded["status"] == "running"

    def test_omitted_status_forwards_none_default(self):
        """Omitting the kwarg forwards ``status=None`` — the existing
        internal callers (manager cache cleanup, fuzzy match, project
        delete, the census) MUST be unaffected by the facade contract."""
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(limit=5)

        forwarded = spy.call_args.kwargs
        assert forwarded["status"] is None

    def test_explicit_none_forwards_none(self):
        """Explicit ``status=None`` is the same as omitting."""
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(status=None)

        forwarded = spy.call_args.kwargs
        assert forwarded["status"] is None

    def test_status_forwards_alongside_all_neighbours(self):
        """Sanity: the status filter travels with the established keyword
        neighbours and never replaces one of them."""
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(
            limit=25,
            offset=10,
            project_id="proj-1",
            exclude_kb=False,
            include_descendants=True,
            search="refactor",
            order="activity",
            source="chat",
            status="paused",
        )

        forwarded = spy.call_args.kwargs
        assert forwarded["limit"] == 25
        assert forwarded["offset"] == 10
        assert forwarded["project_id"] == "proj-1"
        assert forwarded["exclude_kb"] is False
        assert forwarded["include_descendants"] is True
        assert forwarded["search"] == "refactor"
        assert forwarded["order"] == "activity"
        assert forwarded["source"] == "chat"
        assert forwarded["status"] == "paused"
