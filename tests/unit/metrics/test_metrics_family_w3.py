"""The W3 FAMILY PACK — F17, F18, F21.

The registered pack ids of this file:

* ``metrics_family_f17`` — the MCP health family;
* ``metrics_family_f18_partial`` — the gateway sessions / tombstones /
  audit;
* ``metrics_family_f21_partial`` — the journal twin's three gauges.

WHAT EACH PACK PROVES
---------------------
* the family renders with the catalog's canonical names and labels, and
  every label key declares ``allowed_values`` (AM-2);
* the F18 audit vocabulary is EXACTLY ``{ok, error, timeout}`` — the
  ``timeout`` outcome is its OWN label and ``denied`` is NOT in the
  vocabulary (AD4(a), the P4-M6 mutation);
* the F21 PHASE families are ABSENT — the conditionality verdict
  (AD4(b) / D-4, the P4-M7 mutation).
"""

from __future__ import annotations

from typing import Any

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.wiring import sources as wiring_sources
from daemon.services.metrics_query.wiring import vocab
from daemon.services.metrics_query.wiring import w3


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.sql = sql  # type: ignore[attr-defined]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []
        self.queries: list[str] = []
        self.closed = False

    def cursor(self) -> _FakeCursor:
        outer = self

        class _C(_FakeCursor):
            def __init__(self) -> None:
                super().__init__([])

            def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
                outer.queries.append(sql)
                self._rows = outer._rows

        return _C()

    def close(self) -> None:
        self.closed = True


class _FakeStore:
    def __init__(self, machines: int) -> None:
        self._machines = machines

    def roster(self) -> list[Any]:
        return [object() for _ in range(self._machines)]


class _FakeTombstones:
    def __init__(self, count: int) -> None:
        self._count = count

    def __len__(self) -> int:
        return self._count


class _FakeGatewayService:
    def __init__(self, machines: int = 2, tombstones: int = 1) -> None:
        self._store = _FakeStore(machines)
        self._tombstones = _FakeTombstones(tombstones)

    def store(self) -> Any:
        return self._store

    def tombstones(self) -> Any:
        return self._tombstones


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    cm.reset_for_tests()
    yield
    cm.reset_for_tests()
    wiring_sources.unbind_sources()


class TestF17McpHealth:
    """``metrics_family_f17`` — the MCP health family."""

    def test_the_snapshot_is_read_and_never_re_probed(self) -> None:
        snapshot = {
            "context7": {"state": "ok", "init_attempts": 0},
            "plane": {"state": "quarantined", "init_attempts": 2},
        }
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(mcp_snapshot=lambda: snapshot)
        )
        w3.adapt_f17(None)
        assert w3.mcp_server_state.get(server="context7", state="ok") == 1.0
        assert w3.mcp_server_state.get(server="context7", state="quarantined") == 0.0
        assert w3.mcp_server_state.get(server="plane", state="quarantined") == 1.0
        assert w3.mcp_failures_total.get(server="plane", kind="initialize") == 2.0

    def test_a_user_created_server_folds_into_custom(self) -> None:
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                mcp_snapshot=lambda: {"my-server": {"state": "ok", "init_attempts": 0}}
            )
        )
        w3.adapt_f17(None)
        assert w3.mcp_server_state.get(server="custom", state="ok") == 1.0

    def test_no_snapshot_renders_nothing(self) -> None:
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(mcp_snapshot=lambda: None)
        )
        w3.adapt_f17(None)
        assert w3.mcp_server_state.series_keys() == []


class TestF18Gateway:
    """``metrics_family_f18_partial`` — the gateway family."""

    def test_the_sessions_and_tombstones_are_published(self) -> None:
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(mcps_gateway_service=_FakeGatewayService(3, 2))
        )
        w3.adapt_f18(None)
        assert w3.mcps_gateway_sessions.get() == 3.0
        assert w3.mcps_gateway_tombstones.get() == 2.0

    def test_the_audit_vocabulary_is_exactly_three_outcomes(self) -> None:
        """AD4(a) — ``timeout`` is its OWN label; ``denied`` is absent."""
        assert w3.mcps_gateway_audit_total.allowed_values["outcome"] == frozenset(
            {"ok", "error", "timeout"}
        )
        assert "denied" not in w3.mcps_gateway_audit_total.allowed_values["outcome"]

    def test_the_timeout_outcome_is_never_folded_into_error(self) -> None:
        """P4-M6 — the mutation folds ``timeout`` into ``error``."""
        conn = _FakeConnection([("ok", 5), ("error", 2), ("timeout", 1)])
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                dsn="postgresql://fake", db_connect=lambda dsn, **kwargs: conn
            )
        )
        w3.adapt_f18(None)
        assert w3.mcps_gateway_audit_total.get(outcome="timeout") == 1.0
        assert w3.mcps_gateway_audit_total.get(outcome="error") == 2.0

    def test_the_session_total_counter_is_absent(self) -> None:
        names = {m.name for m in cm.registered_metrics()}
        assert "ensemble_mcps_gateway_session_total" not in names


class TestF21Journal:
    """``metrics_family_f21_partial`` — the journal twin."""

    def test_the_three_gauges_are_registered(self) -> None:
        assert w3.journal_file_bytes.kind == "gauge"
        assert w3.journal_history_entries.kind == "gauge"
        assert w3.upgrade_release_current.kind == "gauge"
        assert "version" in w3.upgrade_release_current.allowed_values

    def test_the_phase_families_are_absent(self) -> None:
        """P4-M7 — the conditionality verdict: the phases stay OUT."""
        names = {m.name for m in cm.registered_metrics()}
        assert "ensemble_upgrade_phase_seconds" not in names
        assert "ensemble_upgrade_phase_total" not in names
        assert "ensemble_journal_op_seconds" not in names
        assert "ensemble_journal_ops_total" not in names

    def test_the_conditionality_evidence_is_recorded_in_the_plan(self) -> None:
        """The verdict's evidence: the history entries carry ALERT-CLASS
        events, never promote phases."""
        import inspect

        from daemon.tools import upgrade_journal

        source = inspect.getsource(upgrade_journal.journal_history_append)
        assert '"event": event' in source
        kinds = upgrade_journal.ALERT_KIND_BY_EVENT
        assert set(kinds) == {
            "halt",
            "refusal",
            "rollback",
            "sweep_rollback",
            "quarantine",
        }

    def test_no_install_dir_renders_nothing(self) -> None:
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(install_dir=lambda: None)
        )
        w3.adapt_f21(None)
        assert w3.journal_file_bytes.series_keys() == []
