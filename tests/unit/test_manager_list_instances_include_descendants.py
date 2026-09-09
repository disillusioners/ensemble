"""Facade-signature tests: ``InstanceManager.list_instances`` must accept AND
forward the ``include_descendants`` kwarg (poll-spam fix, 2026-09-09).

Background: the badge/header poll on every 8s tick calls
``InstanceService.listInstanceTree(10)`` → ``GET /api/instances?limit=10``,
whose route called ``manager.list_instances(... include_descendants=True)``
HARDCODED. Prod (~6,324 instances) blows past
``MAX_DESCENDANTS_PER_PAGE=1000`` on every tick → ~510 WARN/hr + silent
truncation. The badge fix: add ``include_descendants`` as a route kwarg
(default TRUE for back-compat with every existing consumer), thread it
through facade → service → repository.

This file pins the facade seam with REAL calls against a spied lifecycle
service seam — deliberately NOT ``inspect.getsource`` source-grep
assertions:

  1. the kwarg exists on the facade signature (a real call with
     ``include_descendants=False`` would raise ``TypeError`` otherwise);
  2. the kwarg is forwarded to the lifecycle service with the caller's
     value;
  3. the default forwards ``False`` (existing internal callers
     unchanged);
  4. the facade remains a pass-through for the result tuple (now a
     3-tuple ``(instances, total, truncated)`` after the same fix).

The real end-to-end dispatch (facade → service → repository, plus the
truncated-flag surfacing through the route) lives in
``tests/integration/test_list_instances_include_descendants_facade.py``.
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


class TestFacadeIncludeDescendantsForwarding:
    """The facade must accept ``include_descendants`` and forward it verbatim."""

    def test_accepts_kwarg_and_forwards_false(self):
        """A real call with ``include_descendants=False`` binds (no TypeError —
        the pre-fix blocker) and the lifecycle service receives the new flag
        with the caller's value (False)."""
        manager, spy = _facade_with_lifecycle_spy()

        result = manager.list_instances(
            limit=10,
            offset=0,
            project_id=None,
            exclude_kb=True,
            include_descendants=False,
            search=None,
            order="activity",
        )

        spy.assert_called_once()
        forwarded = spy.call_args.kwargs
        assert forwarded["include_descendants"] is False
        assert forwarded["limit"] == 10
        assert forwarded["offset"] == 0
        assert forwarded["project_id"] is None
        assert forwarded["exclude_kb"] is True
        assert forwarded["search"] is None
        assert forwarded["order"] == "activity"
        # The facade is a pass-through: the caller gets the service's
        # 3-tuple result back, untouched.
        assert result == ([{"instance_id": "i-1"}], 1, False)

    def test_accepts_kwarg_and_forwards_true(self):
        """Default back-compat behavior: ``include_descendants=True`` must
        still bind and forward. This is the existing consumer contract
        (the route layer's hardcoded True, the dedicated tree API)."""
        manager, spy = _facade_with_lifecycle_spy()

        result = manager.list_instances(
            limit=10,
            include_descendants=True,
        )

        forwarded = spy.call_args.kwargs
        assert forwarded["include_descendants"] is True
        # Result tuple is the 3-tuple shape (the new contract).
        assert result == ([{"instance_id": "i-1"}], 1, False)

    def test_omitted_kwarg_forwards_false_default(self):
        """Omitting the kwarg forwards ``include_descendants=False`` — the
        existing internal callers (manager cache cleanup, fuzzy match,
        agent tool ``list_instances``) must be unaffected by the facade
        change.

        Note: ``include_descendants`` is a regular keyword arg (not
        keyword-only). The default is False at the repository layer; the
        route layer overrides to True at the HTTP boundary, which is where
        the back-compat contract lives.
        """
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(limit=5)

        forwarded = spy.call_args.kwargs
        assert forwarded["include_descendants"] is False
        assert forwarded["limit"] == 5

    def test_default_limit_offset_exclude_kb_order_forwarded(self):
        """Sanity: pre-existing keyword neighbours (``limit``, ``offset``,
        ``exclude_kb``, ``search``, ``order``) still forward through the
        facade. The facade change must not disturb the established
        forwarding style."""
        manager, spy = _facade_with_lifecycle_spy()

        manager.list_instances(
            limit=25,
            offset=10,
            project_id="proj-1",
            exclude_kb=False,
            search="refactor",
            order="activity",
            include_descendants=False,
        )

        forwarded = spy.call_args.kwargs
        assert forwarded["limit"] == 25
        assert forwarded["offset"] == 10
        assert forwarded["project_id"] == "proj-1"
        assert forwarded["exclude_kb"] is False
        assert forwarded["search"] == "refactor"
        assert forwarded["order"] == "activity"
        assert forwarded["include_descendants"] is False
