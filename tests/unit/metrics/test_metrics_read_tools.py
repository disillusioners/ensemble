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

import ast
import asyncio
import json
from typing import Any

import pytest

from daemon.services.metrics_query.constants import (
    MAX_TOTAL_POINTS,
    RESPONSE_BYTE_CEILING,
)
from daemon.tools.metrics_read import (
    MAX_NAMES_PER_FAMILY,
    MAX_NAMES_TOTAL,
    METRICS_CATEGORY_KEY,
    METRICS_AUTHORIZED_AGENTS,
    TOKEN_INVALID_LABEL_FILTER,
    TOKEN_NOT_AUTHORIZED,
    TOKEN_UNAVAILABLE,
    TOKEN_UNKNOWN_AGENT,
    create_metrics_read_tools,
)

CATEGORY = METRICS_CATEGORY_KEY

#: The nine contract-declared members EVERY history series row carries
#: (phase 2 task 2.4 — they are REQUIRED, not optional). A response
#: schema that depends on the body's SIZE is not a schema.
HISTORY_SERIES_MEMBERS: tuple[str, ...] = (
    "cardinality_class",
    "delta",
    "prev_delta",
    "delta_change",
    "delta_reason",
    "prev_delta_reason",
    "downsampled",
    "last_sample_age_s",
    "points_span_s",
)


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


async def _raw(tool: Any, payload: dict[str, Any] | None = None) -> str:
    """The RAW returned body (the byte ceiling is measured on it)."""
    return await tool.ainvoke(payload or {})


def _module_source() -> str:
    """The tool module's source (the static wiring pin)."""
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[3]
        / "daemon"
        / "tools"
        / "metrics_read.py"
    ).read_text(encoding="utf-8")


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
        # W2 — the bound is read from the SEAM constant, never a
        # hardcoded literal: a duplicated number is exactly the drift
        # class (a seam change would leave this pin green while the door
        # moved).
        assert out["query"]["max_points"] <= MAX_TOTAL_POINTS // 51
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

# ── the output budget (AmM-1(c)) — the byte ceiling + the names bound ──


def _fat_family(fid: str, *, names: int, help_chars: int) -> dict[str, Any]:
    """A family whose projection is deliberately FAT (the A3 amplifier)."""
    return {
        "id": fid,
        "role": "stat",
        "source_idiom": "registry",
        "durability": "process",
        "live_series_count": names,
        "cardinality_contribution": names,
        "availability": {"state": "live"},
        "names": [
            {
                "name": f"ensemble_{fid}_metric_{i:05d}",
                "kind": "gauge",
                "cardinality_class": "none",
                "labels": None,
                "monotonic": False,
                "chart_role": "line",
                "buckets": None,
                "help": "h" * help_chars,
            }
            for i in range(names)
        ],
    }


def _history_rows(count: int, *, fat: bool = False) -> list[dict[str, Any]]:
    """History rows for the shape pins — ``fat`` makes each row huge.

    The fat shape is what trips the byte ceiling, so the SAME body is
    exercised on both the trimmed and the untrimmed path.
    """
    labels = {"k": "v" * 60_000} if fat else {"k": "v"}
    return [
        {
            "name": f"ensemble_shape_{i:05d}",
            "labels": dict(labels),
            "kind": "gauge",
            "monotonic": False,
            "points": [[1_700_000_000, float(i)], [1_700_000_015, float(i) + 1]],
        }
        for i in range(count)
    ]


class TestOutputBudget:
    """AmM-1(c) — the output budget is ENFORCED, not declared.

    The council's finding was that the byte-ceiling helpers were DEAD
    CODE: the module carried a trim that no body ever called, so the
    "bounded by clamp + byte ceiling" claim in the fold record was
    FALSE. These pins make the wiring observable.
    """

    def test_the_byte_ceiling_trim_is_wired_into_all_three_bodies(self) -> None:
        """THE STATIC PIN — the trim is not dead code.

        The helper exists in all three builders (one call site each), so
        a body that stops trimming reds this pin before any behavioural
        leg can be fooled.

        THE PIN WALKS THE AST — a source-substring check is satisfied by
        a COMMENT that merely contains the literal (the delta re-review's
        advisory 5(c): the previous form of this pin would have stayed
        green with the call site commented out).
        """
        tree = ast.parse(_module_source())
        builders = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for builder in ("_build_families", "_build_snapshot", "_build_history"):
            calls = [
                node
                for node in ast.walk(builders[builder])
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_enforce_byte_ceiling"
            ]
            assert len(calls) == 1, (
                f"{builder} must call the byte-ceiling enforcement exactly "
                f"once (found {len(calls)}) — an unwired trim is dead code "
                "and the budget claim is false"
            )
            keywords = {kw.arg for kw in calls[0].keywords}
            assert "items" in keywords, (
                f"{builder} must hand the ceiling its SHIPPED items "
                "(`items=`) — a trim measured against a different object "
                "than the body ships is not a bound"
            )

    def test_the_names_projection_is_bounded(self) -> None:
        """THE STATIC PIN — the names projection carries its own bound."""
        source = _module_source()
        assert "MAX_NAMES_PER_FAMILY" in source
        assert "MAX_NAMES_TOTAL" in source

    async def test_a_fat_catalog_is_trimmed_to_the_byte_ceiling(self) -> None:
        """A fat catalog MUST come back inside the seam's own ceiling."""
        provider = FakeProvider(
            families=[
                _fat_family(f"F{i}", names=MAX_NAMES_PER_FAMILY, help_chars=2000)
                for i in range(12)
            ],
            snapshot=[],
        )
        raw = await _raw(_tools(provider=provider)["metrics_families"], {"details": True})
        assert len(raw.encode("utf-8")) <= RESPONSE_BYTE_CEILING, (
            "the returned body must fit the seam's byte ceiling"
        )

    async def test_the_byte_ceiling_trim_is_never_silent(self) -> None:
        """The trim surfaces: ``truncated`` + the counts + a caveat."""
        provider = FakeProvider(
            families=[
                _fat_family(f"F{i}", names=MAX_NAMES_PER_FAMILY, help_chars=2000)
                for i in range(12)
            ],
            snapshot=[],
        )
        out = await _call(_tools(provider=provider)["metrics_families"], {"details": True})
        assert out["truncated"] is True
        assert out["returned_count"] < out["matched_count"]
        assert out["dropped_count"] == out["matched_count"] - out["returned_count"]
        assert any("byte ceiling" in caveat for caveat in out["caveats"]), (
            "the byte-ceiling trim must be NAMED in a caveat"
        )
        assert out["narrow_hint"], "the trim must name a concrete next filter"

    async def test_the_names_bound_is_surfaced(self) -> None:
        """A family whose declared names exceed the projection bound says
        so — the bound is never silent."""
        provider = FakeProvider(
            families=[_fat_family("F1", names=MAX_NAMES_PER_FAMILY + 50, help_chars=4)],
            snapshot=[],
        )
        out = await _call(_tools(provider=provider)["metrics_families"], {"details": True})
        assert out["truncated"] is True
        assert any("names" in caveat for caveat in out["caveats"]), (
            "the names bound must be NAMED in a caveat"
        )
        assert out["narrow_hint"], "the names bound must name a concrete next filter"
        # The TRUE name count is preserved (the field is the family's
        # declared size) — the PROJECTION is the bounded list.
        assert out["families"][0]["name_count"] == MAX_NAMES_PER_FAMILY + 50
        assert len(out["families"][0]["names"]) == MAX_NAMES_PER_FAMILY

    async def test_the_total_names_bound_is_surfaced(self) -> None:
        """The across-the-response bound fires too (the catalog is
        runtime-extensible)."""
        provider = FakeProvider(
            families=[
                _fat_family(f"F{i}", names=MAX_NAMES_PER_FAMILY, help_chars=4)
                for i in range(20)
            ],
            snapshot=[],
        )
        out = await _call(_tools(provider=provider)["metrics_families"], {"details": True})
        assert out["truncated"] is True
        projected = sum(len(f["names"]) for f in out["families"])
        assert projected <= MAX_NAMES_TOTAL

    async def test_both_bounds_on_one_response_keep_both_hints(self) -> None:
        """THE COMBINED-CASE PIN — two bounds on ONE response must not
        erase each other's actionable text.

        5b-②: the names bound AND the byte ceiling can fire on the SAME
        families body, and they share ONE ``narrow_hint`` member. The
        second bound used to OVERWRITE the first one's text, so the
        agent lost the lever it could actually use (the names-bound
        hint) — executed shape: 12 families × 250 names.
        """
        provider = FakeProvider(
            families=[
                _fat_family(f"F{i}", names=250, help_chars=4000)
                for i in range(12)
            ],
            snapshot=[],
        )
        out = await _call(
            _tools(provider=provider)["metrics_families"], {"details": True}
        )
        assert out["truncated"] is True
        hint = out["narrow_hint"]
        assert "projection bound" in hint, (
            "the names-bound hint must SURVIVE the byte-ceiling bound — "
            "one member carries both, so the second must not erase the "
            "first"
        )
        assert "byte ceiling" in hint, "the byte-ceiling hint must be present too"
        caveats = " ".join(out["caveats"])
        assert "more metric names than one response projects" in caveats
        assert "byte ceiling" in caveats

    async def test_the_byte_ceiling_disclosure_names_only_accepted_filters(self) -> None:
        """THE PER-BODY DISCLOSURE PIN (5b-③) — the byte-ceiling text
        must name filters the CALLING BODY accepts.

        The families tool accepts NEITHER ``name_prefix=`` NOR ``top_n=``,
        and the history tool accepts NO ``name_prefix=``. A disclosure
        that names an unaccepted filter is an instruction the agent
        cannot act on.
        """
        fat_families = [
            _fat_family(f"F{i}", names=MAX_NAMES_PER_FAMILY, help_chars=2000)
            for i in range(12)
        ]
        # the FAMILIES body — its levers are `family=` + `details=`
        out = await _call(
            _tools(provider=FakeProvider(families=fat_families, snapshot=[]))[
                "metrics_families"
            ],
            {"details": True},
        )
        families_caveat = next(c for c in out["caveats"] if "byte ceiling" in c)
        assert "name_prefix" not in families_caveat, (
            "metrics_families accepts no `name_prefix` — the disclosure "
            "must not name a filter the caller cannot pass"
        )
        assert "top_n" not in families_caveat, (
            "metrics_families accepts no `top_n` — same rule"
        )
        assert "family" in families_caveat

        # the HISTORY body — its levers are `names=`/`series=`,
        # `window_s=` and `top_n=` (NO `name_prefix=`)
        history_rows = [
            {
                "name": f"ensemble_fat_{i:05d}",
                "labels": {"k": "v" * 60_000},
                "kind": "gauge",
                "monotonic": False,
                "points": [[1_700_000_000, float(i)], [1_700_000_015, float(i) + 1]],
            }
            for i in range(50)
        ]
        out = await _call(
            _tools(
                provider=FakeProvider(
                    families=[_fat_family("F10", names=1, help_chars=4)],
                    snapshot=[],
                    history=history_rows,
                )
            )["metrics_history"],
            {"names": ["m"], "top_n": 50},
        )
        history_caveat = next(c for c in out["caveats"] if "byte ceiling" in c)
        assert "name_prefix" not in history_caveat, (
            "metrics_history accepts no `name_prefix` — same rule"
        )
        assert "window_s" in history_caveat and "top_n" in history_caveat

        # the SNAPSHOT body — it DOES accept `name_prefix=` + `top_n=`
        snapshot_rows = [
            {
                "name": f"ensemble_fat_{i:05d}",
                "labels": {"k": "v" * 60_000},
                "kind": "gauge",
                "monotonic": False,
                "last_publish_age_s": 1.0,
                "value": i,
            }
            for i in range(50)
        ]
        out = await _call(
            _tools(
                provider=FakeProvider(
                    families=[_fat_family("F10", names=1, help_chars=4)],
                    snapshot=snapshot_rows,
                )
            )["metrics_snapshot"],
            {"top_n": 50},
        )
        snapshot_caveat = next(c for c in out["caveats"] if "byte ceiling" in c)
        assert "name_prefix" in snapshot_caveat, (
            "metrics_snapshot DOES accept `name_prefix` — its disclosure "
            "must name it"
        )

    async def test_the_names_bound_caveat_agrees_with_its_hint(self) -> None:
        """THE AGREEMENT PIN (5b-①) — the names-bound CAVEAT must not
        contradict the narrow_hint.

        The caveat used to say "Ask for one family at a time, or use
        `details=false` for the small projection" — exactly what the
        corrected hint DISOWNS (neither lifts the per-family bound). A
        caveat that contradicts the hint is a SECOND, wrong instruction.
        """
        provider = FakeProvider(
            families=[_fat_family("F1", names=MAX_NAMES_PER_FAMILY + 50, help_chars=4)],
            snapshot=[],
        )
        out = await _call(
            _tools(provider=provider)["metrics_families"], {"details": True}
        )
        names_caveat = next(
            c
            for c in out["caveats"]
            if "more metric names than one response projects" in c
        )
        assert "Ask for one family at a time" not in names_caveat, (
            "the caveat must not present the disowned advice — asking for "
            "one family at a time does NOT lift the per-family bound"
        )
        assert "for the small projection" not in names_caveat, (
            "the caveat must not present `details=false` as the lever — it "
            "only shrinks each name"
        )
        assert "does NOT lift" in names_caveat, (
            "the caveat must state the correction, not the old advice"
        )
        assert "name_count" in names_caveat, (
            "the caveat must point at the family's DECLARED count"
        )
        assert "name_prefix=" in out["narrow_hint"], (
            "the hint must name the lever that actually works"
        )

    async def test_a_fat_snapshot_is_trimmed_to_the_byte_ceiling(self) -> None:
        """The SNAPSHOT body carries the trim too."""
        # The tool's own ``top_n`` cap is 50, so a FAT body needs FAT
        # ROWS — the byte ceiling is the backstop for a pathological
        # row, not for a large row COUNT.
        rows = [
            {
                "name": f"ensemble_fat_{i:05d}",
                "labels": {"k": "v" * 60_000},
                "kind": "gauge",
                "monotonic": False,
                "last_publish_age_s": 1.0,
                "value": i,
            }
            for i in range(50)
        ]
        provider = FakeProvider(families=[_fat_family("F10", names=1, help_chars=4)], snapshot=rows)
        raw = await _raw(_tools(provider=provider)["metrics_snapshot"], {"top_n": 50})
        assert len(raw.encode("utf-8")) <= RESPONSE_BYTE_CEILING
        out = json.loads(raw)
        assert out["truncated"] is True
        assert any("byte ceiling" in caveat for caveat in out["caveats"])

    async def test_a_fat_history_is_trimmed_to_the_byte_ceiling(self) -> None:
        """The HISTORY body carries the trim too (and its counts stay
        honest — ``matched_count``/``dropped_count`` stay ``None``)."""
        rows = [
            {
                "name": f"ensemble_fat_{i:05d}",
                "labels": {"k": "v" * 60_000},
                "kind": "gauge",
                "monotonic": False,
                "points": [[1_700_000_000, float(i)], [1_700_000_015, float(i) + 1]],
            }
            for i in range(50)
        ]
        provider = FakeProvider(
            families=[_fat_family("F10", names=1, help_chars=4)], snapshot=[], history=rows
        )
        raw = await _raw(
            _tools(provider=provider)["metrics_history"],
            {"names": ["m"], "top_n": 50},
        )
        assert len(raw.encode("utf-8")) <= RESPONSE_BYTE_CEILING
        out = json.loads(raw)
        assert out["truncated"] is True
        assert out["matched_count"] is None
        assert out["dropped_count"] is None
        assert out["more_available"] is True
        assert any("byte ceiling" in caveat for caveat in out["caveats"])

    async def test_the_history_series_shape_is_the_same_on_both_paths(self) -> None:
        """THE SHAPE PIN — the trimmed path carries the SAME series shape
        as the untrimmed path.

        The C1 regression: the trim was handed the RAW provider rows
        while the payload carried the PROJECTION, so on the trimmed path
        every history row LOST the nine contract-declared members (phase
        2 task 2.4 declares them REQUIRED) — a SIZE-DEPENDENT response
        schema, where the same call answered two different shapes
        depending on how fat the answer was.

        The fat-history pin above asserted the size, the truncated flag
        and the caveat — but NOT the shape, which is exactly why the
        regression slipped through it.
        """
        # the UNTRIMMED path
        provider = FakeProvider(
            families=[_family("F10")], snapshot=[], history=_history_rows(3)
        )
        out = await _call(
            _tools(provider=provider)["metrics_history"], {"names": ["m"], "top_n": 3}
        )
        assert out["truncated"] is False
        assert out["series"]
        for row in out["series"]:
            missing = [m for m in HISTORY_SERIES_MEMBERS if m not in row]
            assert not missing, f"the untrimmed history row is missing {missing}"

        # the TRIMMED path — the same body, fat enough to trip the ceiling
        provider = FakeProvider(
            families=[_family("F10")], snapshot=[], history=_history_rows(50, fat=True)
        )
        out = await _call(
            _tools(provider=provider)["metrics_history"], {"names": ["m"], "top_n": 50}
        )
        assert out["truncated"] is True, "the fat body must trip the ceiling"
        assert out["series"], "the trimmed body must still carry rows"
        for row in out["series"]:
            missing = [m for m in HISTORY_SERIES_MEMBERS if m not in row]
            assert not missing, (
                f"the TRIMMED history row is missing {missing} — the trim "
                "must not change the response schema"
            )

    async def test_the_snapshot_series_shape_is_the_same_on_both_paths(self) -> None:
        """THE SHAPE PIN — the snapshot's trimmed path keeps its rows."""
        provider = FakeProvider(
            families=[_family("F10", names=[])],
            snapshot=[
                {
                    "name": f"ensemble_shape_{i:05d}",
                    "labels": {"k": "v" * 60_000},
                    "kind": "gauge",
                    "monotonic": False,
                    "last_publish_age_s": 1.0,
                    "value": i,
                }
                for i in range(50)
            ],
        )
        out = await _call(
            _tools(provider=provider)["metrics_snapshot"], {"top_n": 50}
        )
        assert out["truncated"] is True
        assert out["series"]
        for row in out["series"]:
            assert "name" in row and "labels" in row, (
                "the trimmed snapshot row must keep the row's own members"
            )
            assert "__family__" not in row, (
                "the internal family marker must stay stripped on BOTH paths"
            )


class TestRingStateSignal:
    """AmM-2ii — the ring-state signal is BEHAVIOURAL, not a field that
    is always present and always False."""

    async def test_an_empty_ring_reports_the_ring_state(self) -> None:
        """A provider whose newest sample is 0 s old (the post-restart
        shape) MUST report ``trend_empty_since_restart``."""
        provider = FakeProvider(families=[_family("F1")], snapshot=[])
        provider.last_sample_age_s = 0.0
        out = await _call(_tools(provider=provider)["metrics_families"])
        assert out["read_status"]["trend_empty_since_restart"] is True, (
            "an empty ring must be REPORTED — the agent must never read "
            "'fresh but empty'"
        )

    async def test_a_live_ring_does_not_report_the_ring_state(self) -> None:
        provider = FakeProvider(families=[_family("F1")], snapshot=[])
        provider.last_sample_age_s = 3.0
        out = await _call(_tools(provider=provider)["metrics_families"])
        assert out["read_status"]["trend_empty_since_restart"] is False

    async def test_the_ring_empty_flag_is_the_ages_heuristic_not_a_fact(self) -> None:
        """THE DISCLOSURE PIN — the ring-empty flag is the AGE HEURISTIC,
        and the direction of its error is a FALSE POSITIVE.

        The leader's ruling (2026-11-03, advisory 5(d), Branch B): the
        precise signal (``last_sample_timestamp() is None`` ⟺ the ring is
        empty) is NOT reachable from this lane — it is not on the
        consumed Protocol, and the points array is not in hand in two of
        the three bodies (the catalog and the published values never
        touch the ring). The age heuristic therefore stays, and its
        known imprecision is pinned HERE so it cannot rot silently:
        ``age == 0.0`` reads "the ring is empty" EVEN WHEN a fresh
        sample exists — the FALSE-POSITIVE direction the disclosure
        names.

        This is a PIN OF A KNOWN IMPRECISION, not of a desired
        behaviour: when the seam-level reader lands (central-metrics-v1
        OWNS the seam), this pin is UPDATED DELIBERATELY.
        """
        provider = FakeProvider(
            families=[_family("F1")],
            snapshot=[
                {
                    "name": "ensemble_fresh_00000",
                    "labels": {},
                    "kind": "gauge",
                    "monotonic": False,
                    "last_publish_age_s": 0.0,
                    "value": 1,
                }
            ],
        )
        # The age reads exactly zero WHILE the call observes a FRESH
        # sample — the shape the disclosure calls out.
        provider.last_sample_age_s = 0.0
        out = await _call(_tools(provider=provider)["metrics_families"])
        assert out["read_status"]["trend_empty_since_restart"] is True, (
            "the age heuristic reads 'empty' at age 0.0 — INCLUDING when a "
            "fresh sample exists (the FALSE-POSITIVE direction the "
            "disclosure names); the precise signal needs the seam's own "
            "reader, which belongs to the seam's owning lane"
        )
        assert isinstance(out["read_status"]["trend_empty_since_restart"], bool), (
            "the flag stays a BOOL — the disclosure changes no wire shape"
        )

    async def test_an_absent_fact_is_never_reported_as_empty(self) -> None:
        """An absent age fact must NEVER be reported as "the ring is
        empty" — the tool asserts only what it can verify.

        THE LEG, RESTATED (the delta re-review's W3 residual — a
        LEG-RESTATEMENT, not a wire change): the wire value is a BOOL,
        so the UNKNOWN state is INTERNAL (the -1.0 sentinel) and is NOT
        observable on the wire. What IS observable — and what this pin
        asserts — is that an absent fact reads as False ("not empty"),
        never as True ("the ring is empty"). The tool never emits null
        on this field.
        """

        class _NoAgeProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(families=[_family("F1")], snapshot=[])
                del self.__dict__["last_sample_age_s"]

        provider = _NoAgeProvider()
        out = await _call(_tools(provider=provider)["metrics_families"])
        assert out["read_status"]["trend_empty_since_restart"] is False, (
            "an absent age fact must never be reported as 'the ring is "
            "empty' — the tool asserts only what it can verify"
        )
        assert isinstance(out["read_status"]["trend_empty_since_restart"], bool), (
            "the field is a BOOL — an unknown state is never put on the wire"
        )

