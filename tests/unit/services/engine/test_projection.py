"""P6 unit pack — the engine observability read model (T6.1/T6.2, AC1/AC2).

Pure functions over injected sources: no DB, no engine runtime, no
network. The pack pins the FROZEN key sets, the chart-hint derivation,
zero-run honesty, the cursor walk (no dup / no omission), the
convergence block with per-field sources, the resolved kill-switch
block, and the bounded runs tail.

Mutation legs (invariants whose violation is the named defect):

* ``test_chart_hint_derives_only_from_the_trigger_kind`` — a guessed
  hint for an unknown trigger ⇒ RED;
* ``test_zero_run_job_renders_honestly`` — a fabricated ``last_run_at``
  for a never-run job ⇒ RED;
* ``test_cursor_walk_has_no_duplication_or_omission`` — an off-by-one
  page boundary ⇒ RED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from daemon.services.engine import projection


# ── fixtures: duck-typed row / snapshot stand-ins ────────────────────────


@dataclass
class Row:
    id: str
    service_tag: str = "svc"
    service_name: str | None = "Service"
    category: str = "reconciler"
    module_info: str | None = "daemon/services/x.py"
    description: str | None = "does things"
    enabled: int = 1
    trigger_kind: str = "interval"
    interval_seconds: float | None = 90.0
    jitter_seconds: float | None = None
    first_tick_kind: str | None = "immediate"
    first_tick_offset_seconds: float | None = None
    event_kind: str | None = None
    priority: int = 5
    concurrency_class: str = "DB_OFFLOAD"
    max_runtime_s: float = 60.0
    timeout_policy: str = "cancel"
    retry_max: int = 0
    kill_switch_key: str | None = None
    version: int = 1
    last_run_at: str | None = None
    last_outcome: str | None = None
    last_skip_reason: str | None = None
    consecutive_skips: int = 0
    dead: int = 0
    registered_at: str = "2026-11-21T00:00:00+00:00"
    updated_at: str = "2026-11-21T00:00:00+00:00"
    job_metadata: dict[str, Any] = field(default_factory=dict)


class Resolver:
    """A stand-in for EngineSwitchResolver (same three methods)."""

    def __init__(self, *, engine: bool = True, shadow: bool = True,
                 killed_keys: tuple[str, ...] = ()):
        self._engine = engine
        self._shadow = shadow
        self._killed = set(killed_keys)

    def engine_enabled(self) -> bool:
        return self._engine

    def shadow_mode(self) -> bool:
        return self._shadow

    def job_killed(self, key: str | None) -> bool:
        return bool(key) and key in self._killed


# ── AC1 — frozen view shapes ─────────────────────────────────────────────


def test_job_row_has_the_frozen_key_set():
    row = projection.job_row(Row(id="j1"))
    assert set(row) == projection.JOB_ROW_KEYS
    assert set(row["schedule"]) == projection.SCHEDULE_KEYS
    assert set(row["health"]) == projection.HEALTH_KEYS
    assert set(row["estate"]) == projection.ESTATE_KEYS


def test_detail_view_has_the_frozen_key_set():
    detail = projection.detail_view(
        Row(id="j1"), runs=[], resolver=Resolver()
    )
    assert set(detail) == projection.DETAIL_KEYS
    assert set(detail["switches"]) == projection.SWITCH_KEYS


def test_event_view_has_the_frozen_key_set():
    payload = projection.event_view(
        snapshot_fn=lambda bus=None: {
            "kinds": {"k": {}},
            "counters": {},
            "generated_at": "t",
        }
    )
    assert set(payload) == projection.EVENT_VIEW_KEYS


def test_list_view_has_the_frozen_key_set():
    payload = projection.list_view([Row(id="a"), Row(id="b")])
    assert set(payload) == projection.LIST_VIEW_KEYS


def test_schedule_block_per_trigger_kind():
    interval = projection.job_row(
        Row(id="i", trigger_kind="interval", interval_seconds=90.0)
    )["schedule"]
    assert interval["kind"] == "interval"
    assert interval["interval_seconds"] == 90.0
    assert interval["event_kind"] is None
    event = projection.job_row(
        Row(id="e", trigger_kind="event", interval_seconds=None,
            event_kind="instance_terminalized")
    )["schedule"]
    assert event["kind"] == "event"
    assert event["event_kind"] == "instance_terminalized"
    assert event["interval_seconds"] is None


# ── AC1 — chart_hint derives ONLY from the trigger kind ─────────────────


def test_chart_hint_derives_only_from_the_trigger_kind():
    assert projection.chart_hint_for("interval") == "gauge"
    assert projection.chart_hint_for("event") == "counter"
    assert projection.chart_hint_for("bogus") is None
    assert projection.chart_hint_for(None) is None
    # The row-level projection uses the same derivation (no guessing).
    assert projection.job_row(Row(id="x"))["chart_hint"] == "gauge"
    assert (
        projection.job_row(Row(id="x", trigger_kind="event"))["chart_hint"]
        == "counter"
    )


# ── AC1 — zero-run honesty ───────────────────────────────────────────────


def test_zero_run_job_renders_honestly():
    row = projection.job_row(Row(id="never-run", last_run_at=None))
    health = row["health"]
    assert health["last_run_at"] is None
    assert health["last_outcome"] is None
    assert health["last_skip_reason"] is None
    assert health["consecutive_skips"] == 0
    assert health["skips_by_reason"] == {}
    # The estate block does not fabricate either.
    assert row["estate"]["last_success_ts"]["value"] is None


def test_health_reads_the_snapshot_when_present():
    snap = {
        "j1": {
            "next_due": "2026-11-21T12:00:00+00:00",
            "running": True,
            "dead": False,
            "would_fire": True,
            "skips_by_reason": {"IDLE": 3},
        }
    }
    health = projection.job_row(Row(id="j1"), snapshot=snap)["health"]
    assert health["running"] is True
    assert health["next_due"] == "2026-11-21T12:00:00+00:00"
    assert health["would_fire"] is True
    assert health["skips_by_reason"] == {"IDLE": 3}


# ── AC2 — convergence union with per-field sources ───────────────────────


def test_convergence_sources_maintenance_fields():
    merged = {
        "checkpoint-cleanup": {
            "last_success_ts": "2026-11-21T10:00:00+00:00",
            "consecutive_skips": 7,
            "next_due": "2026-11-21T12:00:00+00:00",
        }
    }
    row = projection.job_row(
        Row(id="checkpoint-cleanup"),
        merged=merged,
        maintenance_job_ids={"checkpoint-cleanup"},
    )
    estate = row["estate"]
    assert estate["last_success_ts"] == {
        "value": "2026-11-21T10:00:00+00:00",
        "source": "maintenance",
    }
    assert estate["consecutive_skips"] == {"value": 7, "source": "maintenance"}


def test_convergence_sources_engine_fields_for_engine_only_jobs():
    row = projection.job_row(
        Row(id="engine-only"),
        merged={"engine-only": {"last_success_ts": None, "consecutive_skips": 2}},
        maintenance_job_ids=set(),
    )
    estate = row["estate"]
    assert estate["last_success_ts"]["source"] == "engine"
    assert estate["consecutive_skips"]["source"] == "engine"


def test_a_maintenance_job_appears_once_in_the_inventory():
    rows = [Row(id="checkpoint-cleanup"), Row(id="orphan-watcher")]
    merged = {
        "checkpoint-cleanup": {
            "last_success_ts": "2026-11-21T10:00:00+00:00",
            "consecutive_skips": 7,
        }
    }
    payload = projection.list_view(
        rows, merged=merged, maintenance_job_ids={"checkpoint-cleanup"}
    )
    ids = [j["id"] for j in payload["jobs"]]
    assert ids.count("checkpoint-cleanup") == 1
    assert payload["returned"] == 2


# ── D6.9 — paging: no duplication, no omission ───────────────────────────


def test_cursor_walk_has_no_duplication_or_omission():
    rows = [Row(id=f"job-{i:02d}", priority=i % 3) for i in range(25)]
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = projection.list_view(rows, limit=7, cursor=cursor)
        seen.extend(j["id"] for j in page["jobs"])
        pages += 1
        assert pages < 10, "cursor walk did not terminate"
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 25, "duplication or omission"
    # Stable (priority, id) ordering across the whole walk.
    expected = [
        r.id
        for r in sorted(rows, key=lambda r: (r.priority, r.id))
    ]
    assert seen == expected


def test_limit_default_and_cap():
    rows = [Row(id=f"job-{i:03d}") for i in range(600)]
    page = projection.list_view(rows)
    assert page["limit"] == projection.DEFAULT_LIMIT == 100
    assert projection.clamp_limit(10_000) == projection.MAX_LIMIT == 500
    assert projection.clamp_limit("bogus") == 100
    assert projection.clamp_limit(None) == 100


def test_invalid_cursor_raises_the_typed_error():
    with pytest.raises(projection.InvalidCursor):
        projection.list_view([Row(id="a")], cursor="not-a-cursor")
    with pytest.raises(projection.InvalidCursor):
        projection.list_view(
            [Row(id="a")],
            cursor=projection.encode_cursor({"v": 999, "o": 0}),
        )


def test_filters_narrow_the_inventory():
    rows = [
        Row(id="a", category="reconciler", service_tag="t1", enabled=1),
        Row(id="b", category="probe", service_tag="t2", enabled=0),
    ]
    assert [j["id"] for j in projection.list_view(rows, category="probe")["jobs"]] == ["b"]
    assert [j["id"] for j in projection.list_view(rows, service_tag="t1")["jobs"]] == ["a"]
    assert [j["id"] for j in projection.list_view(rows, enabled=False)["jobs"]] == ["b"]


# ── D6.7 — the resolved switch block ─────────────────────────────────────


def test_switches_block_resolves_job_kill():
    resolver = Resolver(killed_keys=("SWITCH_X",))
    block = projection.switches_block(resolver, "SWITCH_X")
    assert block["job_killed"] is True
    assert block["should_run"] is False
    assert block["skip_reason"] == "KILL_SWITCHED"
    ok = projection.switches_block(resolver, "SWITCH_UNSET")
    assert ok["job_killed"] is False
    assert ok["should_run"] is True
    assert ok["skip_reason"] is None


def test_switches_block_engine_wide_off_blocks_everything():
    resolver = Resolver(engine=False)
    block = projection.switches_block(resolver, None)
    assert block["engine_enabled"] is False
    assert block["should_run"] is False
    assert block["skip_reason"] == "KILL_SWITCHED"


def test_switches_block_unwired_resolver_is_honest():
    block = projection.switches_block(None, "K")
    assert all(v is None for v in block.values())


# ── D6.10 — the bounded runs tail ────────────────────────────────────────


def test_runs_tail_is_bounded_to_twenty():
    runs = [
        {"started_at": f"t{i}", "outcome": "SUCCEEDED"} for i in range(50)
    ]
    detail = projection.detail_view(
        Row(id="j1"), runs=runs, resolver=Resolver()
    )
    assert len(detail["runs"]) == projection.RUNS_TAIL_LIMIT == 20
    assert set(detail["runs"][0]) == projection.RUN_ROW_KEYS


# ── T6.3 — the deferral record is present and names the path ─────────────


def test_deferral_record_is_present_in_the_module_docstring():
    doc = projection.__doc__ or ""
    assert "T6.3" in doc
    assert "DEFERRED" in doc
    assert "UNFENCING PATH" in doc
    # The five ceremony elements are named (an unfencing cannot be vague).
    for element in (
        "CATALOG_FAMILY_VERDICTS",
        "REGISTERED_FAMILIES",
        "FAMILY_SERIES_BANDS",
        "ALLOWED_DAEMON_DIFF",
        "census-count pin",
    ):
        assert element in doc, element


def test_projection_registers_zero_metrics_families():
    # F-1: the read model never touches the metrics registry.
    from daemon.services.metrics_query.wiring import registry

    families_before = set(registry.registered_families())
    import daemon.services.engine.projection  # noqa: F401 — the import IS the probe

    assert set(registry.registered_families()) == families_before
    src = (projection.__file__ or "")
    if src:
        from pathlib import Path

        text = Path(src).read_text(encoding="utf-8")
        assert "register_family_metric" not in text
        assert "from daemon.services.metrics_query.wiring import registry" not in text


# ── import discipline (dispatch ruling) ─────────────────────────────────


def test_projection_imports_only_pure_engine_seams():
    # The module may import exactly P2's project_merged + P3's resolver
    # from the engine package — never the runtime service modules.
    from pathlib import Path

    text = Path(projection.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "daemon.services.engine.engine",
        "daemon.services.engine.execution",
        "daemon.services.engine.state",
        "daemon.services.engine.locks",
        "daemon.services.engine.dispatch",
        "daemon.services.engine.registry",
    ):
        assert forbidden not in text, forbidden


def test_router_imports_projection_only_from_the_engine_package():
    from pathlib import Path

    import daemon.routers.engine as router_mod

    text = Path(router_mod.__file__).read_text(encoding="utf-8")
    assert "from daemon.services.engine import projection" in text
    for forbidden in (
        "from daemon.services.engine.engine",
        "from daemon.services.engine.metrics",
        "from daemon.services.engine.killswitch",
        "from daemon.services.engine.execution",
        "from daemon.services.engine.state",
    ):
        assert forbidden not in text, forbidden
