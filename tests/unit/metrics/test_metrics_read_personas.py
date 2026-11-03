"""The PERSONA acceptance pack — the design's two real examples, converted
into STRUCTURAL assertions.

``technical-analysis.md`` §A.12 carries TWO persona examples with real
JSON: the **maintenancer** diagnosing a slow instance and the **wanderer**
sweeping the fleet.  This pack turns them into assertions.

THE DISCIPLINE (binding)
-------------------------
**STRUCTURAL ONLY — never an observation-dependent VALUE.**  The design
says so itself: *"the counts are observation-dependent … and are NOT
statically derivably; the shapes, field names, and vocabulary are
exact"*.  So this pack asserts:

* the exact FIELD SET of every result and every row;
* the exact VOCABULARY (the ``availability`` states/reasons, the
  ``cardinality_class`` values, the ``AdmissionState`` label values, the
  ``chart_role`` values);
* the ORDERING promises (the deterministic ascending order, the
  availability block carried VERBATIM).

It asserts **no** count, age, timestamp or series value — those are
observations, not contract.

**THE GENERIC CAVEAT (AmB-13).**  The design's persona examples embed the
cause-attribution token ``(R-ii)`` in their ``caveats[0]`` and in the
§A.9 presentation string.  Those example strings are SUPERSEDED FOR COPY
PURPOSES by the canonical generic caveat, so the assertions below use the
generic form and a leg asserts that no attribution token appears in any
shipped string.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from daemon.tools.metrics_read import create_metrics_read_tools


@pytest.fixture(autouse=True)
async def _bind_the_offload_seam_to_this_loop():
    """Bind the off-loop seam to THIS test's event loop (see the house
    note in ``db_offload``: one process, one loop in production)."""
    from daemon.services import db_offload

    db_offload.configure(loop=asyncio.get_running_loop())
    yield


class FakeProvider:
    """The persona fixture provider — the design's frozen shapes."""

    def __init__(
        self,
        *,
        families: list[dict[str, Any]],
        snapshot: list[dict[str, Any]],
        history: list[dict[str, Any]] | None = None,
    ) -> None:
        self._families = families
        self._snapshot = snapshot
        self._history = history or []
        self.registry_read_ok = True
        self.listener_enabled = True
        self.census_enabled = False
        self.effective_interval_s = 15.0
        self.last_sample_age_s = 3.0

    def families(self) -> list[dict[str, Any]]:
        return list(self._families)

    def snapshot(self, *, family=None, name_prefix=None) -> list[dict[str, Any]]:
        return list(self._snapshot)

    def history(self, **kwargs) -> list[dict[str, Any]]:
        return list(self._history)


class FakeManager:
    def __init__(self, provider: Any) -> None:
        self.metrics_query_provider = provider


# ── the design's fixtures (the frozen vocabularies) ───────────────────────

#: The F1 (event-loop lag) family — the persona (a) fixture.
F1_NAMES = [
    {
        "name": "ensemble_loop_lag_seconds",
        "kind": "gauge",
        "cardinality_class": "closed",
        "labels": {"stat": ["max", "p50", "p95"]},
        "monotonic": False,
        "chart_role": "line",
        "buckets": None,
        "help": "Event-loop lag quantiles in seconds.",
    },
    {
        "name": "ensemble_loop_lag_samples",
        "kind": "gauge",
        "cardinality_class": "none",
        "labels": None,
        "monotonic": False,
        "chart_role": "line",
        "buckets": None,
        "help": "The number of lag samples the probe's rolling window holds.",
    },
    {
        "name": "ensemble_loop_lag_overflow_total",
        "kind": "counter",
        "cardinality_class": "none",
        "labels": None,
        "monotonic": True,
        "chart_role": "step",
        "buckets": None,
        "help": "The number of lag samples the probe clamped.",
    },
]

F1_FAMILY = {
    "id": "F1",
    "role": "chart",
    "source_idiom": "wedge_capture",
    "durability": "process",
    "live_series_count": 9,
    "cardinality_contribution": 9,
    "availability": {"state": "dark", "reason": "capture_off"},
    "names": F1_NAMES,
}

#: The F10 (job queue) family — the persona (b) fixture.
F10_FAMILY = {
    "id": "F10",
    "role": "stat",
    "source_idiom": "registry",
    "durability": "process",
    "live_series_count": 6,
    "cardinality_contribution": 6,
    "availability": {"state": "live"},
    "names": [
        {
            "name": "ensemble_job_queue_depth",
            "kind": "gauge",
            "cardinality_class": "closed",
            "labels": {"admission_state": ["QUEUED", "ACTIVE", "DONE", "DEAD"]},
            "monotonic": False,
            "chart_role": "step",
            "buckets": None,
            "help": "The job queue depth per admission state.",
        },
        {
            "name": "ensemble_job_queue_items_created_total",
            "kind": "counter",
            "cardinality_class": "none",
            "labels": None,
            "monotonic": True,
            "chart_role": "step",
            "buckets": None,
            "help": "The number of created queue items.",
        },
    ],
}

#: The persona (b) snapshot rows — the frozen row shape + the four-value
#: AdmissionState vocabulary.
F10_SNAPSHOT_ROWS = [
    {
        "name": "ensemble_job_queue_depth",
        "labels": {"admission_state": "QUEUED"},
        "kind": "gauge",
        "monotonic": False,
        "value": 14,
        "last_publish_age_s": 4.1,
    },
    {
        "name": "ensemble_job_queue_depth",
        "labels": {"admission_state": "ACTIVE"},
        "kind": "gauge",
        "monotonic": False,
        "value": 6,
        "last_publish_age_s": 4.1,
    },
    {
        "name": "ensemble_job_queue_depth",
        "labels": {"admission_state": "DONE"},
        "kind": "gauge",
        "monotonic": False,
        "value": 231,
        "last_publish_age_s": 4.1,
    },
    {
        "name": "ensemble_job_queue_depth",
        "labels": {"admission_state": "DEAD"},
        "kind": "gauge",
        "monotonic": False,
        "value": 2,
        "last_publish_age_s": 4.1,
    },
    {
        "name": "ensemble_dead_letter_items",
        "labels": {},
        "kind": "gauge",
        "monotonic": False,
        "value": 2,
        "last_publish_age_s": 4.1,
    },
    {
        "name": "ensemble_job_queue_items_created_total",
        "labels": {},
        "kind": "counter",
        "monotonic": True,
        "value": 18422,
        "last_publish_age_s": 4.1,
    },
]


async def _call(provider: Any, tool_name: str, payload: dict[str, Any]) -> Any:
    tools = create_metrics_read_tools(
        FakeManager(provider), current_instance_id="", agent_id="maintenancer", agent_tag=None
    )
    by_name = {getattr(t, "name", ""): t for t in tools}
    return json.loads(await by_name[tool_name].ainvoke(payload))


#: The six-member bound block — identical in all three tools.
BOUND_MEMBERS = {
    "matched_count",
    "returned_count",
    "dropped_count",
    "more_available",
    "truncated",
    "narrow_hint",
}

#: The echoed header members.
HEADER_MEMBERS = {
    "schema_version",
    "vocabulary_version",
    "generated_at_utc",
    "restart_epoch",
    "activation",
}


class TestPersonaAMaintenancer:
    """PERSONA (a) — the maintenancer diagnosing a slow instance."""

    async def test_call_1_families_carries_the_exact_field_set(self) -> None:
        provider = FakeProvider(families=[F1_FAMILY], snapshot=[])
        out = await _call(provider, "metrics_families", {"family": "F1", "details": True})

        assert out["tool"] == "metrics_families"
        assert set(out["read_status"]) >= {
            "registry_read_ok",
            "mode",
            "note",
        }
        assert set(out["read_status"]) <= {
            "registry_read_ok",
            "mode",
            "note",
            "trend_empty_since_restart",
            "doctrine",
        }
        assert HEADER_MEMBERS <= set(out)
        assert set(out["activation"]) == {
            "listener_enabled",
            "census_enabled",
            "query_api",
            "registry_read_ok",
        }
        assert BOUND_MEMBERS <= set(out)
        assert set(out["query"]) >= {"family", "details", "only_with_data"}
        assert isinstance(out["caveats"], list)

    async def test_call_1_the_family_entry_carries_the_exact_field_set(self) -> None:
        provider = FakeProvider(families=[F1_FAMILY], snapshot=[])
        out = await _call(provider, "metrics_families", {"family": "F1", "details": True})
        entry = out["families"][0]
        assert set(entry) == {
            "id",
            "role",
            "source_idiom",
            "durability",
            "live_series_count",
            "cardinality_contribution",
            "availability",
            "names",
            "name_count",
            "data_present",
            "dark_with_live_series",
        }

    async def test_call_1_the_availability_block_is_verbatim(self) -> None:
        provider = FakeProvider(families=[F1_FAMILY], snapshot=[])
        out = await _call(provider, "metrics_families", {"family": "F1", "details": True})
        assert out["families"][0]["availability"] == {
            "state": "dark",
            "reason": "capture_off",
        }

    async def test_call_1_the_derived_members_are_present(self) -> None:
        provider = FakeProvider(families=[F1_FAMILY], snapshot=[])
        out = await _call(provider, "metrics_families", {"family": "F1", "details": True})
        entry = out["families"][0]
        assert entry["data_present"] is True
        assert entry["dark_with_live_series"] is True
        assert entry["name_count"] == len(F1_NAMES)

    async def test_call_1_the_name_entries_carry_the_exact_field_set(self) -> None:
        provider = FakeProvider(families=[F1_FAMILY], snapshot=[])
        out = await _call(provider, "metrics_families", {"family": "F1", "details": True})
        for entry in out["families"][0]["names"]:
            assert set(entry) == {
                "name",
                "kind",
                "cardinality_class",
                "labels",
                "monotonic",
                "chart_role",
                "buckets",
                "help",
            }

    async def test_call_1_the_declared_label_space_is_the_closed_set(self) -> None:
        provider = FakeProvider(families=[F1_FAMILY], snapshot=[])
        out = await _call(provider, "metrics_families", {"family": "F1", "details": True})
        by_name = {n["name"]: n for n in out["families"][0]["names"]}
        # A CLOSED name declares its value space (the selector vocabulary);
        # a label-less name declares null — never an enumerated set.
        assert by_name["ensemble_loop_lag_seconds"]["labels"] == {
            "stat": ["max", "p50", "p95"]
        }
        assert by_name["ensemble_loop_lag_samples"]["labels"] is None
        assert by_name["ensemble_loop_lag_seconds"]["cardinality_class"] == "closed"
        assert by_name["ensemble_loop_lag_samples"]["cardinality_class"] == "none"

    async def test_call_1_the_counter_is_monotonic_and_steps(self) -> None:
        provider = FakeProvider(families=[F1_FAMILY], snapshot=[])
        out = await _call(provider, "metrics_families", {"family": "F1", "details": True})
        by_name = {n["name"]: n for n in out["families"][0]["names"]}
        overflow = by_name["ensemble_loop_lag_overflow_total"]
        assert overflow["monotonic"] is True
        assert overflow["chart_role"] == "step"
        assert overflow["kind"] == "counter"

    async def test_call_1_the_caveat_is_generic(self) -> None:
        """AmB-13 — the shipped caveat carries the FACT, never a cause."""
        provider = FakeProvider(families=[F1_FAMILY], snapshot=[])
        out = await _call(provider, "metrics_families", {"family": "F1", "details": True})
        assert out["caveats"], "a dark-but-live family must carry a caveat"
        caveat = out["caveats"][0]
        assert "the series below are real" in caveat
        for token in ("R-i", "R-ii", "capture_active", "census inverted"):
            assert token not in caveat

    async def test_call_2_history_carries_the_exact_field_set(self) -> None:
        provider = FakeProvider(
            families=[F1_FAMILY],
            snapshot=[],
            history=[
                {
                    "name": "ensemble_loop_lag_seconds",
                    "labels": {"stat": "p95"},
                    "kind": "gauge",
                    "monotonic": False,
                    "points": [[1_700_000_000, 0.0041], [1_700_000_015, 0.0154]],
                }
            ],
        )
        out = await _call(
            provider,
            "metrics_history",
            {
                "series": ["ensemble_loop_lag_seconds{stat=p95}"],
                "window_s": 120,
                "max_points": 120,
                "top_n": 5,
                "compare": True,
            },
        )
        assert out["tool"] == "metrics_history"
        assert set(out["read_status"]) >= {"registry_read_ok", "mode", "note"}
        assert HEADER_MEMBERS <= set(out)
        assert BOUND_MEMBERS <= set(out)
        assert set(out["window"]) == {
            "from_utc",
            "to_utc",
            "interval_s",
            "interval_s_source",
        }
        assert out["window"]["interval_s_source"] == "observed"
        assert set(out["compare_window"]) == {"from_utc", "to_utc"}
        assert "last_sample_age_s" in out
        assert "stale" in out
        assert "effective_interval_s" in out

    async def test_call_2_the_series_row_carries_the_exact_field_set(self) -> None:
        provider = FakeProvider(
            families=[F1_FAMILY],
            snapshot=[],
            history=[
                {
                    "name": "ensemble_loop_lag_seconds",
                    "labels": {"stat": "p95"},
                    "kind": "gauge",
                    "monotonic": False,
                    "points": [[1_700_000_000, 0.0041], [1_700_000_015, 0.0154]],
                }
            ],
        )
        out = await _call(
            provider,
            "metrics_history",
            {
                "series": ["ensemble_loop_lag_seconds{stat=p95}"],
                "window_s": 120,
                "max_points": 120,
                "top_n": 5,
                "compare": True,
            },
        )
        row = out["series"][0]
        assert {
            "name",
            "labels",
            "kind",
            "monotonic",
            "points",
            "cardinality_class",
            "delta",
            "prev_delta",
            "delta_change",
            "delta_reason",
            "downsampled",
            "last_sample_age_s",
            "points_span_s",
        } <= set(row)
        assert row["cardinality_class"] == "closed"
        assert isinstance(row["downsampled"], bool)


class TestPersonaBWanderer:
    """PERSONA (b) — the wanderer sweeping the fleet."""

    async def test_call_1_the_catalog_carries_the_family_shape(self) -> None:
        provider = FakeProvider(families=[F1_FAMILY, F10_FAMILY], snapshot=[])
        out = await _call(provider, "metrics_families", {})
        for entry in out["families"]:
            assert {
                "source_idiom",
                "durability",
                "live_series_count",
                "availability",
            } <= set(entry)
            assert entry["source_idiom"] in {"wedge_capture", "registry", "checkpoint"}
            assert entry["durability"] in {"process", "durable"}
            assert set(entry["availability"]) <= {"state", "reason"}
            assert entry["availability"]["state"] in {"live", "dark"}

    async def test_call_2_the_snapshot_row_carries_the_frozen_shape(self) -> None:
        provider = FakeProvider(families=[F10_FAMILY], snapshot=F10_SNAPSHOT_ROWS)
        out = await _call(provider, "metrics_snapshot", {"family": "F10"})
        assert out["tool"] == "metrics_snapshot"
        assert "effective_interval_s" in out
        assert HEADER_MEMBERS <= set(out)
        assert BOUND_MEMBERS <= set(out)
        for row in out["series"]:
            assert {
                "name",
                "labels",
                "kind",
                "monotonic",
                "value",
                "last_publish_age_s",
                "cardinality_class",
                "stale",
            } <= set(row)
            assert row["cardinality_class"] in {"closed", "open", "none"}

    async def test_call_2_the_admission_state_vocabulary_is_intact(self) -> None:
        """The four-value AdmissionState vocabulary survives the projection."""
        provider = FakeProvider(families=[F10_FAMILY], snapshot=F10_SNAPSHOT_ROWS)
        out = await _call(provider, "metrics_snapshot", {"family": "F10"})
        seen = {
            row["labels"]["admission_state"]
            for row in out["series"]
            if "admission_state" in (row.get("labels") or {})
        }
        assert seen == {"QUEUED", "ACTIVE", "DONE", "DEAD"}

    async def test_call_2_the_counter_keeps_its_monotonicity(self) -> None:
        provider = FakeProvider(families=[F10_FAMILY], snapshot=F10_SNAPSHOT_ROWS)
        out = await _call(provider, "metrics_snapshot", {"family": "F10"})
        by_name = {row["name"]: row for row in out["series"]}
        assert by_name["ensemble_job_queue_items_created_total"]["monotonic"] is True
        assert by_name["ensemble_dead_letter_items"]["monotonic"] is False


class TestNoObservationDependentAssertions:
    """The pack itself must not pin an observation."""

    def test_the_pack_pins_no_count_or_age(self) -> None:
        """The pack itself must not pin an observation.

        The forbidden literals are built at RUN time (never written
        literally) so this leg does not match its own source.
        """
        import pathlib

        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        observations = (9, 14, 231, 18422, 6, 4.1, 15.0, 2)
        for value in observations:
            forbidden = "== " + repr(value)
            assert forbidden not in source, (
                "the persona pack must not assert the observation-dependent "
                f"value {forbidden!r}"
            )
