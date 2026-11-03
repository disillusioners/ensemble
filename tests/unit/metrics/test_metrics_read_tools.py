"""The ``metrics-read`` TOOL pack — the three tools' behaviour.

The registration pack proves the SEAM (the category is registered, the
gate is two-layer, the tools are present).  This pack proves the
BEHAVIOUR: what each tool answers, what it refuses, and what it never
fabricates.

The rules under test (the design's binding clauses):

* the AVAILABILITY block is carried VERBATIM — never re-derived, never
  re-ordered, never substituted;
* a BOUND that drops something is NEVER silent — the six-member bound
  block is present and semantically correct in all three tools;
* deltas are never fabricated — a window with fewer than two points
  yields ``null`` + ``delta_reason: "insufficient_points"``, NEVER ``0``;
* the ladder is fail-closed and ORDERED — authorization fires BEFORE the
  substrate check, which fires BEFORE any provider read (a refusal
  never touches the provider);
* the HARD FLOOR — a history call with no selector at all is refused,
  never an unbounded match;
* the COMBINED selector cap is enforced by the tool (the router's
  formula, mirrored exactly);
* the BUDGET clamp is never silent — the effective bound is echoed and
  the clamp is named in a caveat;
* NO cursor is accepted or returned anywhere.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from daemon.tools.metrics_read import (
    METRICS_CATEGORY_KEY,
    METRICS_AUTHORIZED_AGENTS,
    TOKEN_INVALID_LABEL_FILTER,
    TOKEN_NOT_AUTHORIZED,
    TOKEN_UNAVAILABLE,
    TOKEN_UNKNOWN_AGENT,
    create_metrics_read_tools,
)

CATEGORY = METRICS_CATEGORY_KEY


@pytest.fixture(autouse=True)
async def _bind_the_offload_seam_to_this_loop():
    """Bind the off-loop seam to THIS test's event loop.

    The ``db_offload`` seam captures its loop ONCE — correct in production
    (one process, one loop) but not under pytest-asyncio, where every
    async test gets its OWN loop.  ``configure(loop=...)`` is the seam's
    own public re-configuration entry point (the lifespan uses it), so
    the test binds it to the running loop rather than reaching into the
    module's globals.
    """
    from daemon.services import db_offload

    db_offload.configure(loop=asyncio.get_running_loop())
    yield


class FakeProvider:
    """A duck-typed provider that RECORDS what the tool read."""

    def __init__(
        self,
        *,
        families: list[dict[str, Any]] | None = None,
        snapshot: list[dict[str, Any]] | None = None,
        history: list[dict[str, Any]] | None = None,
        registry_read_ok: bool = True,
        last_sample_age_s: float = 3.0,
        effective_interval_s: float = 15.0,
    ) -> None:
        self._families = list(families or [])
        self._snapshot = list(snapshot or [])
        self._history = list(history or [])
        self.registry_read_ok = registry_read_ok
        self.listener_enabled = True
        self.census_enabled = True
        self.effective_interval_s = effective_interval_s
        self.last_sample_age_s = last_sample_age_s
        self.reads: list[str] = []
        self.history_calls: list[dict[str, Any]] = []

    def families(self) -> list[dict[str, Any]]:
        self.reads.append("families")
        return list(self._families)

    def snapshot(self, *, family=None, name_prefix=None) -> list[dict[str, Any]]:
        self.reads.append("snapshot")
        return list(self._snapshot)

    def history(self, **kwargs) -> list[dict[str, Any]]:
        self.reads.append("history")
        self.history_calls.append(dict(kwargs))
        return list(self._history)


class FakeManager:
    """The manager handle the factory receives."""

    def __init__(self, provider: Any = None) -> None:
        self.metrics_query_provider = provider


def _family(
    fid: str,
    *,
    names: list[dict[str, Any]] | None = None,
    live: int = 1,
    state: str = "live",
    reason: str | None = None,
) -> dict[str, Any]:
    availability: dict[str, Any] = {"state": state}
    if reason is not None:
        availability["reason"] = reason
    return {
        "id": fid,
        "role": "stat",
        "source_idiom": "registry",
        "durability": "process",
        "live_series_count": live,
        "cardinality_contribution": live,
        "availability": availability,
        "names": list(
            names
            or [
                {
                    "name": f"ensemble_{fid}_metric",
                    "kind": "gauge",
                    "cardinality_class": "none",
                    "labels": None,
                    "monotonic": False,
                    "chart_role": "line",
                    "buckets": None,
                    "help": "help",
                }
            ]
        ),
    }


def _tools(agent_id: str = "maintenancer", provider: Any = None) -> dict[str, Any]:
    tools = create_metrics_read_tools(
        FakeManager(provider),
        current_instance_id="",
        agent_id=agent_id,
        agent_tag=None,
    )
    return {getattr(t, "name", ""): t for t in tools}


async def _call(tool: Any, payload: dict[str, Any] | None = None) -> Any:
    raw = await tool.ainvoke(payload or {})
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


# ── metrics_families ───────────────────────────────────────────────────────


class TestFamilies:
    async def test_the_catalog_is_answered_from_the_provider(self) -> None:
        provider = FakeProvider(families=[_family("F1"), _family("F10")])
        out = await _call(_tools(provider=provider)["metrics_families"])
        assert out["tool"] == "metrics_families"
        assert out["matched_count"] == 2
        assert out["returned_count"] == 2
        assert out["dropped_count"] == 0
        assert out["more_available"] is False
        assert out["truncated"] is False
        assert out["narrow_hint"] is None

    async def test_the_echoed_header_carries_the_versions(self) -> None:
        out = await _call(_tools(provider=FakeProvider())["metrics_families"])
        assert out["schema_version"] == 1
        assert "vocabulary_version" in out
        assert "generated_at_utc" in out
        assert "restart_epoch" in out
        assert out["activation"]["query_api"] == "auth-gated-always-on"

    async def test_the_availability_block_is_carried_verbatim(self) -> None:
        availability = {"state": "dark", "reason": "census_gated"}
        catalog = [_family("F1", state="dark", reason="census_gated")]
        out = await _call(_tools(provider=FakeProvider(families=catalog))["metrics_families"])
        assert out["families"][0]["availability"] == availability, (
            "the availability block must be carried UNCHANGED — never "
            "re-derived, never re-ordered, never substituted"
        )

    async def test_the_derived_members_are_present(self) -> None:
        catalog = [_family("F1", live=9, state="dark", reason="capture_off")]
        out = await _call(_tools(provider=FakeProvider(families=catalog))["metrics_families"])
        entry = out["families"][0]
        assert entry["name_count"] == 1
        assert entry["data_present"] is True
        assert entry["dark_with_live_series"] is True

    async def test_the_dark_with_live_caveat_is_generic(self) -> None:
        catalog = [_family("F1", live=9, state="dark", reason="capture_off")]
        out = await _call(_tools(provider=FakeProvider(families=catalog))["metrics_families"])
        assert out["caveats"], "a dark-but-live family must carry a caveat"
        caveat = out["caveats"][0]
        assert "the series below are real" in caveat
        for token in ("R-i", "R-ii", "capture_active", "census inverted"):
            assert token not in caveat, (
                "the shipped caveat must not attribute a cause — it asserts "
                "only the runtime-verifiable fact"
            )

    async def test_details_false_is_the_small_projection(self) -> None:
        provider = FakeProvider(families=[_family("F1")])
        out = await _call(_tools(provider=provider)["metrics_families"])
        name_entry = out["families"][0]["names"][0]
        assert set(name_entry) == {"name", "kind", "cardinality_class"}

    async def test_details_true_adds_the_declared_members(self) -> None:
        provider = FakeProvider(families=[_family("F1")])
        out = await _call(
            _tools(provider=provider)["metrics_families"], {"details": True}
        )
        name_entry = out["families"][0]["names"][0]
        assert set(name_entry) == {
            "name",
            "kind",
            "cardinality_class",
            "labels",
            "monotonic",
            "chart_role",
            "buckets",
            "help",
        }

    async def test_only_with_data_filters_on_the_live_count(self) -> None:
        catalog = [_family("F1", live=9), _family("F2", live=0)]
        provider = FakeProvider(families=catalog)
        out = await _call(
            _tools(provider=provider)["metrics_families"], {"only_with_data": True}
        )
        assert out["matched_count"] == 1
        assert out["families"][0]["id"] == "F1"

    async def test_an_unknown_family_is_not_an_error(self) -> None:
        provider = FakeProvider(families=[_family("F1"), _family("F10")])
        out = await _call(
            _tools(provider=provider)["metrics_families"], {"family": "F17x"}
        )
        assert out["families"] == []
        assert out["matched_count"] == 0
        assert out["narrow_hint"], "an unknown family must name the known ids"
        assert "F1" in out["narrow_hint"] and "F10" in out["narrow_hint"]


# ── metrics_snapshot ───────────────────────────────────────────────────────


class TestSnapshot:
    async def test_one_provider_call_for_a_multi_family_filter(self) -> None:
        provider = FakeProvider(
            families=[_family("F10"), _family("F13")],
            snapshot=[
                {"name": "ensemble_F10_metric", "labels": {}, "kind": "gauge",
                 "monotonic": False, "last_publish_age_s": 1.0, "value": 3},
                {"name": "ensemble_F13_metric", "labels": {}, "kind": "gauge",
                 "monotonic": False, "last_publish_age_s": 1.0, "value": 4},
                {"name": "ensemble_other_metric", "labels": {}, "kind": "gauge",
                 "monotonic": False, "last_publish_age_s": 1.0, "value": 5},
            ],
        )
        out = await _call(
            _tools(provider=provider)["metrics_snapshot"], {"families": ["F10", "F13"]}
        )
        assert provider.reads.count("snapshot") == 1, (
            "the multi-family list is filtered TOOL-SIDE over ONE provider "
            "read — never one read per family"
        )
        assert {row["name"] for row in out["series"]} == {
            "ensemble_F10_metric",
            "ensemble_F13_metric",
        }

    async def test_the_rows_sort_before_the_cut(self) -> None:
        provider = FakeProvider(
            families=[_family("F1"), _family("F10")],
            snapshot=[
                {"name": "z_metric", "labels": {}, "kind": "gauge",
                 "monotonic": False, "last_publish_age_s": 1.0, "value": 1},
                {"name": "a_metric", "labels": {}, "kind": "gauge",
                 "monotonic": False, "last_publish_age_s": 1.0, "value": 2},
            ],
        )
        out = await _call(_tools(provider=provider)["metrics_snapshot"])
        names = [row["name"] for row in out["series"]]
        assert names == sorted(names), "the ascending promise is tool-enforced"

    async def test_the_derived_members_are_present(self) -> None:
        provider = FakeProvider(
            families=[_family("F10")],
            snapshot=[
                {"name": "ensemble_F10_metric", "labels": {}, "kind": "gauge",
                 "monotonic": False, "last_publish_age_s": 100.0, "value": 3},
            ],
        )
        out = await _call(_tools(provider=provider)["metrics_snapshot"])
        row = out["series"][0]
        assert row["cardinality_class"] == "none"
        assert row["stale"] is True, (
            "100 s of publish age at a 15 s observed spacing is stale "
            "(the 3x rule)"
        )
        assert out["effective_interval_s"] == 15.0

    async def test_the_top_n_cut_reports_the_drop(self) -> None:
        rows = [
            {"name": f"metric_{i:02d}", "labels": {}, "kind": "gauge",
             "monotonic": False, "last_publish_age_s": 1.0, "value": i}
            for i in range(30)
        ]
        provider = FakeProvider(families=[_family("F10")], snapshot=rows)
        out = await _call(
            _tools(provider=provider)["metrics_snapshot"], {"top_n": 10}
        )
        assert out["returned_count"] == 10
        assert out["matched_count"] == 30
        assert out["dropped_count"] == 20
        assert out["truncated"] is True
        assert out["narrow_hint"], "a dropped bound must name a next filter"


# ── metrics_history ────────────────────────────────────────────────────────


class TestHistory:
    async def test_the_hard_floor_refuses_an_unbounded_match(self) -> None:
        provider = FakeProvider(families=[_family("F1")])
        raw = await _tools(provider=provider)["metrics_history"].ainvoke({})
        assert "invalid_label_filter" in raw
        assert "history" not in provider.reads, (
            "the floor fires BEFORE the history read — never an unbounded "
            "match (the catalog read is inherent: the refusal NAMES the "
            "family ids)"
        )

    async def test_an_unknown_family_is_refused_not_unbounded(self) -> None:
        """An UNKNOWN family resolves to no names — it must NOT become an
        unbounded match (the floor is RE-CHECKED after the family
        resolution)."""
        provider = FakeProvider(families=[_family("F1")])
        raw = await _tools(provider=provider)["metrics_history"].ainvoke(
            {"family": "F99x"}
        )
        assert "invalid_label_filter" in raw
        assert "history" not in provider.reads, (
            "an unknown family must never reach the history read"
        )

    async def test_the_combined_selector_cap_is_enforced(self) -> None:
        provider = FakeProvider(families=[_family("F1")])
        raw = await _tools(provider=provider)["metrics_history"].ainvoke(
            {"names": [f"m{i}" for i in range(40)], "series": [f"s{i}" for i in range(20)]}
        )
        assert "invalid_label_filter" in raw
        assert "history" not in provider.reads, (
            "the cap fires BEFORE the history read"
        )

    async def test_both_provider_calls_are_anchored(self) -> None:
        provider = FakeProvider(
            families=[_family("F10")],
            history=[
                {"name": "ensemble_F10_metric", "labels": {}, "kind": "gauge",
                 "monotonic": False, "points": [[1, 1.0]]},
            ],
        )
        await _call(
            _tools(provider=provider)["metrics_history"],
            {"family": "F10", "window_s": 300},
        )
        assert len(provider.history_calls) == 2, "the compare leg is a second read"
        now_current = provider.history_calls[0]["now"]
        now_previous = provider.history_calls[1]["now"]
        assert now_current - now_previous == 300, (
            "the compare leg anchors exactly one window back"
        )

    async def test_the_probe_entry_never_leaks(self) -> None:
        provider = FakeProvider(
            families=[_family("F10")],
            history=[
                {"name": f"m{i}", "labels": {}, "kind": "gauge", "monotonic": False,
                 "points": [[1, 1.0]]}
                for i in range(3)
            ],
        )
        out = await _call(
            _tools(provider=provider)["metrics_history"], {"family": "F10", "top_n": 2}
        )
        assert out["returned_count"] == 2
        assert out["more_available"] is True, (
            "the top_n+1 probe entry tells the tool that more exist"
        )
        assert len(out["series"]) == 2

    async def test_an_insufficient_window_yields_null_never_zero(self) -> None:
        provider = FakeProvider(
            families=[_family("F10")],
            history=[
                {"name": "m", "labels": {}, "kind": "gauge", "monotonic": False,
                 "points": [[1, 5.0]]},
            ],
        )
        out = await _call(_tools(provider=provider)["metrics_history"], {"names": ["m"]})
        row = out["series"][0]
        assert row["delta"] is None
        assert row["delta_reason"] == "insufficient_points"
        assert row["delta"] != 0, "a zero delta would assert 'nothing changed'"

    async def test_the_per_series_freshness_is_present(self) -> None:
        provider = FakeProvider(
            families=[_family("F10")],
            history=[
                {"name": "m", "labels": {}, "kind": "gauge", "monotonic": False,
                 "points": [[100, 1.0], [160, 2.0]]},
            ],
        )
        out = await _call(_tools(provider=provider)["metrics_history"], {"names": ["m"]})
        row = out["series"][0]
        assert "last_sample_age_s" in row
        assert row["points_span_s"] == 60.0

    async def test_the_budget_clamp_is_not_silent(self) -> None:
        provider = FakeProvider(
            families=[_family("F10")],
            history=[
                {"name": "m", "labels": {}, "kind": "gauge", "monotonic": False,
                 "points": [[1, 1.0]]},
            ],
        )
        out = await _call(
            _tools(provider=provider)["metrics_history"],
            {"names": ["m"], "top_n": 50, "max_points": 240},
        )
        assert out["query"]["max_points"] <= 12000 // 51
        assert out["query"]["max_points_requested"] == 240
        assert any("clamped" in caveat for caveat in out["caveats"]), (
            "a clamp that reduces the caller's bound must be SURFACED"
        )

    async def test_the_compare_leg_is_declared_separately(self) -> None:
        provider = FakeProvider(
            families=[_family("F10")],
            history=[
                {"name": "m", "labels": {}, "kind": "gauge", "monotonic": False,
                 "points": [[1, 1.0], [2, 3.0]]},
            ],
        )
        out = await _call(_tools(provider=provider)["metrics_history"], {"names": ["m"]})
        assert "compare_window" in out
        assert out["query"]["compare"] is True
        row = out["series"][0]
        assert row["delta"] == 2.0
        assert row["delta_change"] is not None

    async def test_no_cursor_is_accepted_or_returned(self) -> None:
        provider = FakeProvider(families=[_family("F10")])
        out = await _call(
            _tools(provider=provider)["metrics_history"], {"names": ["m"]}
        )
        assert "cursor" not in out
        assert "next_cursor" not in out
        schema = _tools(provider=provider)["metrics_history"].args_schema
        assert "cursor" not in schema.model_fields


# ── the fail-closed ladder ─────────────────────────────────────────────────


class TestLadder:
    @pytest.mark.parametrize("agent_id", ["", None, 123])
    async def test_an_unknown_agent_is_refused(self, agent_id: Any) -> None:
        provider = FakeProvider(families=[_family("F1")])
        raw = await _tools(agent_id=agent_id, provider=provider)["metrics_families"].ainvoke({})
        assert TOKEN_UNKNOWN_AGENT in raw
        assert provider.reads == [], "a refusal never touches the provider"

    async def test_a_non_holder_is_refused(self) -> None:
        provider = FakeProvider(families=[_family("F1")])
        raw = await _tools(agent_id="ari", provider=provider)["metrics_families"].ainvoke({})
        assert TOKEN_NOT_AUTHORIZED in raw
        assert provider.reads == []

    async def test_an_unmounted_manager_is_refused(self) -> None:
        raw = await _tools(agent_id="maintenancer", provider=None)["metrics_families"].ainvoke({})
        assert TOKEN_UNAVAILABLE in raw

    async def test_authorization_fires_before_the_substrate_check(self) -> None:
        """An unauthorized caller must NOT learn whether the substrate is
        mounted (the D-SCOPE-4 ordering)."""
        raw = await _tools(agent_id="ari", provider=None)["metrics_families"].ainvoke({})
        assert TOKEN_NOT_AUTHORIZED in raw
        assert TOKEN_UNAVAILABLE not in raw, (
            "the substrate refusal must not leak to an unauthorized caller"
        )

    async def test_the_malformed_selector_is_typed_not_raised(self) -> None:
        provider = FakeProvider(families=[_family("F10")])
        raw = await _tools(provider=provider)["metrics_history"].ainvoke(
            {"series": ["bad{selector"]}
        )
        assert TOKEN_INVALID_LABEL_FILTER in raw
        assert "Traceback" not in raw
        assert provider.reads == [], (
            "the selector is parsed by the TOOL — the refusal is "
            "deterministic and fires before ANY provider read"
        )

    async def test_the_holders_are_the_three(self) -> None:
        assert METRICS_AUTHORIZED_AGENTS == frozenset(
            {"maintenancer", "devops", "wanderer"}
        )


# ── the degraded read ──────────────────────────────────────────────────────


class TestDegradedRead:
    async def test_a_failed_read_is_not_an_empty_read(self) -> None:
        provider = FakeProvider(families=[], registry_read_ok=False)
        out = await _call(_tools(provider=provider)["metrics_families"])
        assert out["read_status"]["registry_read_ok"] is False
        assert out["read_status"]["mode"] == "degraded"
        assert out["read_status"]["note"], "a degraded read must say so"
        assert any("INCOMPLETE" in caveat for caveat in out["caveats"])

    async def test_the_flag_is_read_per_call(self) -> None:
        provider = FakeProvider(families=[_family("F1")], registry_read_ok=True)
        tools = _tools(provider=provider)
        first = await _call(tools["metrics_families"])
        assert first["read_status"]["registry_read_ok"] is True
        provider.registry_read_ok = False
        second = await _call(tools["metrics_families"])
        assert second["read_status"]["registry_read_ok"] is False, (
            "the flag is read PER CALL — never cached"
        )

    async def test_the_doctrine_states_the_racy_bool_semantics(self) -> None:
        out = await _call(_tools(provider=FakeProvider())["metrics_families"])
        assert out["read_status"]["doctrine"], "the read_status doctrine is stated"
        assert "mutable" in out["read_status"]["doctrine"]
