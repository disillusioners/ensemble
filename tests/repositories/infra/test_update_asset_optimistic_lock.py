"""M5 fix — optimistic-locking tests for ``update_asset``.

The M5 fix adds a ``version`` column to ``infra_assets`` and a
new ``expected_version`` parameter to
:meth:`SQLModelInfraRepository.update_asset`. When the caller
supplies ``expected_version``, the update is gated by
``WHERE id = :id AND version = :expected_version`` and
``version`` is atomically set to ``version + 1``; a concurrent
modification that moved the version between the caller's
read and write raises ``ValueError`` instead of silently
clobbering the concurrent change.

Tests in this module cover the four cases that matter for the
fix:

1. **Backward compatibility** — callers that do NOT supply
   ``expected_version`` see the old behavior (the update lands,
   ``version`` is incremented). This guards the contract that
   existing tool-layer callers (e.g. ``daemon/tools/infra.py``)
   are not broken by the new column.

2. **Happy path with version check** — caller supplies
   ``expected_version``, the update lands, ``version`` is
   incremented exactly once, the history row reflects the
   pre-update state.

3. **Concurrent modification detected** — caller A reads
   version 1; caller B updates first (version becomes 2);
   caller A's update with stale ``expected_version=1`` raises
   ``ValueError`` and the row is unchanged.

4. **Retry pattern** — caller B's update bumps the version;
   caller A re-reads (now version 2), updates with
   ``expected_version=2``, succeeds.

Together these tests prove that the optimistic-lock semantics
are correct AND that the legacy code path is unchanged.
"""

from __future__ import annotations

import pytest

from daemon.repositories.infra import (
    InfraAsset,
    InfraAssetHistory,
    InfraChangeType,
    SQLModelInfraRepository,
)


class TestUpdateAssetOptimisticLock:
    """M5 fix — ``update_asset`` optimistic-lock semantics."""

    def test_update_asset_legacy_increments_version(
        self, infra_repository, seed_projects, project_id
    ):
        """Backward compatibility: callers that do not pass
        ``expected_version`` see the old read-modify-write
        behavior, and the ``version`` column is incremented
        monotonically.

        This is the contract the existing tool layer
        (``daemon/tools/infra.py``) relies on — the call site
        there does not pass ``expected_version``, so we must
        not change the externally visible behavior. The
        version increment is the only observable difference,
        and it's invisible to callers that don't read
        ``asset.version``.
        """
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"cpu": 4},
        )
        # Fresh row — version starts at the model default (1).
        assert asset.version == 1

        # First legacy update (no expected_version).
        updated = infra_repository.update_asset(
            asset.id,
            name="web-01-renamed",
            attributes={"cpu": 8},
        )
        assert updated is not None
        assert updated.name == "web-01-renamed"
        assert updated.attributes == {"cpu": 8}
        assert updated.version == 2, (
            "Legacy path must bump version so a caller that later "
            "opts into expected_version sees a meaningful counter"
        )

        # Second legacy update — version advances again.
        updated2 = infra_repository.update_asset(
            asset.id,
            attributes={"cpu": 16},
        )
        assert updated2 is not None
        assert updated2.version == 3

    def test_update_asset_with_expected_version_succeeds(
        self, infra_repository, seed_projects, project_id
    ):
        """Atomic path: caller passes the version it read; the
        update lands and ``version`` is incremented by exactly 1.
        """
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-02",
        )
        assert asset.version == 1

        # Caller passes the version they just read.
        updated = infra_repository.update_asset(
            asset.id,
            expected_version=asset.version,
            name="web-02-renamed",
            updated_by="agent-7",
        )

        assert updated is not None
        assert updated.name == "web-02-renamed"
        assert updated.version == 2
        assert updated.updated_by == "agent-7"

        # A history row was written for the update (M5 must
        # preserve the audit trail).
        history = infra_repository.get_history(asset.id)
        assert len(history) == 2  # created + updated
        update_history = next(h for h in history if h.change_type == InfraChangeType.UPDATED.value)
        assert update_history.snapshot is not None
        # The pre-update snapshot must reflect the OLD name.
        assert update_history.snapshot["name"] == "web-02"
        assert update_history.old_values == {"name": "web-02"}
        assert update_history.new_values == {"name": "web-02-renamed"}

    def test_update_asset_concurrent_modification_raises(
        self, infra_repository, seed_projects, project_id
    ):
        """M5 core test: two callers read version 1, caller A
        updates first (version becomes 2), caller B's update
        with stale ``expected_version=1`` raises ``ValueError``
        and the row is unchanged.

        Simulates the lost-update race that motivated the M5
        fix. Without the version check, caller B's
        ``name="hijacked"`` would silently overwrite caller A's
        ``name="primary"``.
        """
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="shared",
        )
        # Both callers have read version 1.
        version_before_both = asset.version
        assert version_before_both == 1

        # Caller A: lands the "primary" update first.
        primary = infra_repository.update_asset(
            asset.id,
            expected_version=version_before_both,
            name="primary",
        )
        assert primary is not None
        assert primary.name == "primary"
        assert primary.version == 2

        # Caller B: still has the stale version it read before
        # A's update. Its update must FAIL with ValueError so
        # the caller knows to re-read.
        with pytest.raises(ValueError) as exc_info:
            infra_repository.update_asset(
                asset.id,
                expected_version=version_before_both,  # STALE — should be 2
                name="hijacked",
            )
        # The error message must mention the version mismatch
        # so the caller can diagnose. We don't pin the exact
        # wording (operators iterate on it) but the salient
        # facts — "Concurrent" and "expected_version" — must be
        # present.
        msg = str(exc_info.value)
        assert "Concurrent" in msg
        assert "expected_version" in msg

        # The row was NOT mutated by caller B's failed attempt.
        fetched = infra_repository.get_asset(asset.id)
        assert fetched is not None
        assert fetched.name == "primary", (
            "Failed optimistic-lock attempt must not have applied "
            "any field changes"
        )
        assert fetched.version == 2, (
            "Failed optimistic-lock attempt must not have bumped "
            "the version counter"
        )

        # Only ONE "updated" history row exists — the one from
        # caller A's successful update. Caller B's failed
        # attempt did not write history.
        history = infra_repository.get_history(asset.id)
        update_history_rows = [
            h for h in history if h.change_type == InfraChangeType.UPDATED.value
        ]
        assert len(update_history_rows) == 1
        assert update_history_rows[0].new_values == {"name": "primary"}

    def test_update_asset_retry_pattern_after_concurrent_modification(
        self, infra_repository, seed_projects, project_id
    ):
        """The retry pattern callers use after a stale
        ``expected_version`` error: re-read → pass the new
        version → update succeeds.

        This is the positive counterpart to the concurrent-
        modification test: it proves the API supports the
        standard "retry with refreshed version" workflow that
        optimistic locking exists to enable.
        """
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="retry-target",
        )
        initial_version = asset.version
        assert initial_version == 1

        # Caller A wins first.
        first = infra_repository.update_asset(
            asset.id,
            expected_version=initial_version,
            name="first-writer",
        )
        assert first is not None
        assert first.version == 2

        # Caller B has the stale version — fails.
        with pytest.raises(ValueError):
            infra_repository.update_asset(
                asset.id,
                expected_version=initial_version,  # STALE
                name="second-writer",
            )

        # Caller B re-reads (gets version=2), retries with the
        # fresh expected_version — succeeds.
        fresh = infra_repository.get_asset(asset.id)
        assert fresh is not None
        assert fresh.version == 2
        retried = infra_repository.update_asset(
            asset.id,
            expected_version=fresh.version,
            name="second-writer",
        )
        assert retried is not None
        assert retried.name == "second-writer"
        assert retried.version == 3

        # Final state: name is the second writer's, version=3.
        final = infra_repository.get_asset(asset.id)
        assert final is not None
        assert final.name == "second-writer"
        assert final.version == 3
