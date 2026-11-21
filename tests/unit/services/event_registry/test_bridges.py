"""P5 pack — the bridges (T5.3-T5.6, AC2 / AC4 / F-1 / F-3 / F-11 / F-19).

Static-source discipline, house style
``tests/job_queue/test_terminal_write_census.py``: the bridge census scans
the daemon tree for the registry's record-call sites and enforces the
bidirectional contract:

1. every literal kind passed to ``record()`` IS in the catalog
   (mutation: add a record with an unregistered kind ⇒ RED);
2. every catalog kind except (a) the ``deferred_v2`` entry and (b) the
   DOCUMENTED ``BRIDGE_PENDING`` family (the T5.4 open question) has ≥1
   bridge (mutation: delete a bridge ⇒ RED);
3. the class-1 bridge adds no second fire / CAS / probe (F-19) and the
   class-3 bridge adds no write to either state machine;
4. F-1 (the HARD leg): the metrics family census is UNCHANGED — the
   registry registers zero families;
5. migration delta from P5 == 0 (D5.2/D5.3).
"""

from __future__ import annotations

import re
from pathlib import Path

from daemon.services.event_registry import (
    BRIDGE_PENDING,
    CATALOG,
    KIND_INDEX,
    DEFERRED_V2_KINDS,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DAEMON_ROOT = REPO_ROOT / "daemon"

_RECORD_CALL_RE = re.compile(
    r"_event_registry_record\(\s*[\"']([A-Za-z0-9_:<>*]+)[\"']"
)


def _daemon_python_files():
    return [
        p for p in DAEMON_ROOT.rglob("*.py") if "__pycache__" not in p.parts
    ]


def _registry_importing_files():
    files = {}
    for path in _daemon_python_files():
        src = path.read_text(encoding="utf-8", errors="replace")
        if "event_registry import record" in src:
            files[path] = src
    return files


def _recorded_literal_kinds():
    found: dict[str, list[str]] = {}
    for path, src in _registry_importing_files().items():
        for kind in _RECORD_CALL_RE.findall(src):
            found.setdefault(kind, []).append(str(path.relative_to(REPO_ROOT)))
    return found


# ── AC2 — the bidirectional bridge census ────────────────────────────────


def test_every_recorded_kind_is_in_the_catalog():
    # Mutation leg: a bridge call with an unregistered kind ⇒ RED.
    recorded = _recorded_literal_kinds()
    assert recorded, "the census found no bridge sites at all"
    unknown = {
        kind: files for kind, files in recorded.items() if kind not in KIND_INDEX
    }
    assert not unknown, f"unregistered kinds recorded: {unknown}"


def test_every_bridged_kind_is_a_registered_v1_kind():
    recorded = _recorded_literal_kinds()
    for kind in recorded:
        assert kind not in DEFERRED_V2_KINDS, (
            f"{kind} is deferred_v2 — no bridge may fire it (F-18)"
        )


def test_every_catalog_kind_has_a_bridge_or_a_pinned_pending_reason():
    # AC2, leader ruling L-9 end state: every catalog kind except the
    # deferred_v2 entry is either BRIDGED (a literal record() site in the
    # daemon tree) or present in BRIDGE_PENDING with a pinned reason
    # (zero-producer enum member, or a producer outside the L-9 site
    # list). The LOCKSTEP both ways: no bridged kind may sit in
    # BRIDGE_PENDING, and no pending kind may have a bridge.
    recorded = set(_recorded_literal_kinds())
    from daemon.services.event_registry import BRIDGE_PENDING

    bridged = set()
    for spec in CATALOG:
        if spec.metadata.get("deferred_v2"):
            continue  # the class-5 pointer never fires in v1 (F-18)
        if spec.kind_id in recorded:
            bridged.add(spec.kind_id)
            assert spec.kind_id not in BRIDGE_PENDING, (
                f"{spec.kind_id} is bridged AND pending — lockstep violated"
            )
        else:
            assert spec.kind_id in BRIDGE_PENDING, (
                f"{spec.kind_id} has no bridge and no pinned pending "
                f"reason — AC2 violated"
            )
    # Every BRIDGE_PENDING entry is a real catalog kind (no stale rows).
    for kind_id in BRIDGE_PENDING:
        assert kind_id in KIND_INDEX, kind_id
    # The T5.4 bridge sites: 10 L-9 sites / 7 files PLUS the two
    # in-map completion-row kinds (child_reports is a P5-owned file).
    expected_bridged_durable = {
        "event_bus:question_answered",
        "event_bus:job_completed",
        "event_bus:instance_lifecycle",
        "event_bus:question_requested",
        "event_bus:midflight_report",
        "event_bus:stuck_awaiting_answer",
        "event_bus:child_question_still_pending",
        "event_bus:ram_limit_exceeded",
        "event_bus:task_recovery",
        "event_bus:instance_completed",
        "event_bus:child_completed",
    }
    assert expected_bridged_durable <= bridged
    # Zero-producer kinds carry the pinned enum-only reason.
    from daemon.services.event_registry import ZERO_PRODUCER_KINDS

    for kind_id in ZERO_PRODUCER_KINDS:
        assert "enum-only:" in BRIDGE_PENDING[kind_id]


def test_no_literal_event_bus_kind_is_ever_recorded_with_drift():
    # event_bus kinds must be recorded as literals whose value is a
    # REAL EventKind member value (or the pinned non-enum task_recovery
    # family) — a typo'd literal would silently never match a producer.
    recorded = _recorded_literal_kinds()
    from daemon.repositories.event.models import EventKind

    enum_values = {ek.value for ek in EventKind}
    for kind in recorded:
        if not kind.startswith("event_bus:"):
            continue
        suffix = kind[len("event_bus:"):]
        assert suffix in enum_values or suffix == "task_recovery", kind


def test_class2_bridges_cover_the_l9_ten_site_census():
    # Leader ruling L-9: 10 durable-kind emission sites across 7 files.
    # Each site's file must carry at least one class-2 record() call.
    expected_files = {
        "daemon/routers/answer_helper.py": "event_bus:question_answered",
        "daemon/services/job_recovery_service.py": "event_bus:job_completed",
        "daemon/services/event_publisher.py": "event_bus:instance_lifecycle",
        "daemon/services/midflight_qa.py": None,  # 4 sites
        "daemon/services/job_feedback_observer.py": "event_bus:job_completed",
        "daemon/services/ram_guard_events.py": "event_bus:ram_limit_exceeded",
        "daemon/services/stale_task_recovery.py": "event_bus:task_recovery",
    }
    for rel_path, expected_kind in expected_files.items():
        src = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        kinds = _RECORD_CALL_RE.findall(src)
        assert kinds, f"no record() bridge in {rel_path}"
        if expected_kind is not None:
            assert expected_kind in kinds, (rel_path, kinds)
    # midflight_qa carries the four QA sites.
    qa_kinds = set(
        _RECORD_CALL_RE.findall(
            (REPO_ROOT / "daemon/services/midflight_qa.py").read_text(
                encoding="utf-8"
            )
        )
    )
    assert qa_kinds == {
        "event_bus:question_requested",
        "event_bus:midflight_report",
        "event_bus:stuck_awaiting_answer",
        "event_bus:child_question_still_pending",
    }, qa_kinds


def test_create_event_itself_carries_no_bridge():
    # L-9 option (b) REJECTED: create_event is the GAP-15 fan-out
    # primitive this phase is fenced from touching — the file must not
    # import or call the registry.
    src = (DAEMON_ROOT / "services" / "event_bus.py").read_text(encoding="utf-8")
    assert "event_registry" not in src


def test_the_deferred_kind_has_no_bridge_anywhere():
    recorded = _recorded_literal_kinds()
    assert "turn_transition" not in recorded


# ── AC4 / F-19 — the class-1 bridge is pure observation ──────────────────


def _child_reports_source():
    path = DAEMON_ROOT / "services" / "child_reports.py"
    return path.read_text(encoding="utf-8")


def _wrapper_source(src: str) -> str:
    start = src.index("async def _fire_terminal_child_and_deliver")
    next_def = src.find("\n    async def ", start + 10)
    return src[start:next_def]


def test_class1_wrapper_has_exactly_one_fire_and_no_cas():
    src = _child_reports_source()
    wrapper = _wrapper_source(src)
    assert wrapper.count("fire_for_terminal_child(") == 1, (
        "F-19: the wrapper must hold EXACTLY ONE bus fire"
    )
    assert "transition_state(" not in wrapper, (
        "F-19: the bridge must add no second CAS"
    )


def test_class1_bridge_block_is_synchronous_observation_only():
    src = _child_reports_source()
    wrapper = _wrapper_source(src)
    bridge_start = wrapper.index("class-1 bridge (T5.3)")
    bridge_end = wrapper.index("if not fired:", bridge_start)
    bridge = wrapper[bridge_start:bridge_end]
    assert "_event_registry_record(" in bridge
    assert "await" not in bridge, (
        "the class-1 bridge must be sync (D5.7) — no await in the bridge"
    )
    for forbidden in ("fire_for_terminal_child", "transition_state",
                      "enqueue", "to_thread"):
        assert forbidden not in bridge, forbidden


# ── T5.6 — the attestation-bridge caller sites exist and follow ──────────


def test_attestation_bridges_present_at_all_five_caller_sites():
    graph = (DAEMON_ROOT / "graph.py").read_text(encoding="utf-8")
    child = _child_reports_source()
    # graph.py completion gate: the three ledger writes, each followed by
    # its observation.
    gate = graph.index("def create_attestation_gate_node")
    assert "epoch_action=\"deny_increment\"" in graph[gate:]
    assert graph[gate:].index("safe_increment(") < graph[gate:].index(
        "epoch_action=\"deny_increment\""
    )
    assert graph[gate:].index("safe_set_escalated_and_reset(") < graph[
        gate:].index("epoch_action=\"escalated_and_reset\"")
    assert graph[gate:].rindex("safe_reset(") < graph[gate:].index(
        "epoch_action=\"reset\""
    )
    # child_reports FP4-L6 siblings (riding under the class-1 file row).
    assert "epoch_action=\"deny_increment\"" in child
    assert "path=\"fp4_l6\"" in child
    assert child.index("safe_increment(") < child.index("path=\"fp4_l6\"")
    assert child.index("safe_set_escalated_and_reset(") < child.index(
        "path=\"fp4_l6_release\""
    )


def test_attestation_ledger_module_is_untouched_by_the_registry():
    # The ledger's fail-open posture is OBSERVED/labeled, never changed:
    # the registry package must not IMPORT anything from
    # attestation_ledger (observation at callers only). Describing the
    # substrate in module_info strings is fine and intended.
    pkg = DAEMON_ROOT / "services" / "event_registry"
    import_re = re.compile(
        r"^\s*(from|import)\s+\.?[\w.]*attestation_ledger", re.MULTILINE
    )
    for path in pkg.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        hits = import_re.findall(src)
        assert not hits, (path, hits)


# ── T5.5 — the class-3 bridge adds NO write to the state machines ────────


def test_class3_lane_reads_observe_after_the_read():
    src = (DAEMON_ROOT / "services" / "report_delivery_recovery.py").read_text(
        encoding="utf-8"
    )
    deferred_read = src.index("find_deferred_for_parent_all(")
    pending_read = src.index("find_pending_past_age(")
    first_obligation = src.index('"report_injection_obligation"')
    second_obligation = src.index('"report_injection_obligation"',
                                  first_obligation + 1)
    assert deferred_read < first_obligation < pending_read < second_obligation


def test_class3_transitions_observe_after_the_guarded_write():
    src = (DAEMON_ROOT / "services" / "report_delivery_recovery.py").read_text(
        encoding="utf-8"
    )
    abandon_write = src.index("service.abandon_commission(")
    satisfy_write = src.index("service.satisfy_commission(")
    abandoned_obs = src.index('transition="abandoned"')
    satisfied_obs = src.index('transition="satisfied_on_evidence"')
    assert abandon_write < abandoned_obs
    assert satisfy_write < satisfied_obs


def test_class3_bridge_is_synchronous_and_writeless():
    src = (DAEMON_ROOT / "services" / "report_delivery_recovery.py").read_text(
        encoding="utf-8"
    )
    assert "await _event_registry_record" not in src
    helper_start = src.index("def _p5_row_age_seconds")
    helper_end = src.index("# ── F-2", helper_start)
    helper = src[helper_start:helper_end]
    for forbidden in ("self.", "repo", "execute", "update", "insert",
                      "commit"):
        assert forbidden not in helper, forbidden


# ── AC8 / F-1 — the metrics family census is UNCHANGED (HARD leg) ────────


def test_zero_metrics_families_registered_by_the_registry():
    # F-1: the census is CLOSED (28 verdicts / 18 registered). Importing
    # the registry must not add a single family — and no family id may
    # even mention the registry (the P6 T6.3 ceremony owns any future
    # registration).
    from daemon.services.metrics_query.wiring import registry

    families_before = set(registry.registered_families())
    import daemon.services.event_registry  # noqa: F401 — the import IS the test

    families_after = set(registry.registered_families())
    assert families_before == families_after
    for family_id in families_after:
        assert "event_registry" not in str(family_id).lower()


# ── AC8 — migration delta from P5 == 0 (D5.2/D5.3) ───────────────────────


def test_no_migration_file_mentions_the_registry():
    migrations = DAEMON_ROOT / "migrations" / "versions"
    for path in migrations.glob("*.sql"):
        src = path.read_text(encoding="utf-8", errors="replace")
        assert "event_registry" not in src.lower(), path


def test_registry_package_declares_no_table():
    pkg = DAEMON_ROOT / "services" / "event_registry"
    for path in pkg.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "SQLModel" not in src, path
        assert "CREATE TABLE" not in src.upper() or path.name == "__init__.py"
        assert "migrations" not in src or path.name == "__init__.py", path


def test_registry_package_has_no_engine_import():
    # D5.11 one-way dependency: the registry must not import the engine
    # (P2/P3/P4 compile and test without P5, and vice versa).
    pkg = DAEMON_ROOT / "services" / "event_registry"
    for path in pkg.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "services.engine" not in src.replace(
            "daemon/services/engine/", ""
        ).replace("engine/spec.py", ""), path
        assert not re.search(
            r"^\s*(from|import)\s+\.{0,3}[\w.]*\.?services\.engine",
            src,
            re.MULTILINE,
        ), path


# ── F-3 / F-11 — the dim-name fence ──────────────────────────────────────


def test_no_forbidden_dim_names_in_the_registry_or_bridges():
    # F-3: the registry's dim/column names must not be action_by /
    # acted_at. Exact-token match (word boundary) — a pre-existing
    # ``compacted_at`` in graph.py is NOT a violation.
    forbidden_re = re.compile(r"\b(action_by|acted_at)\b")
    scan_files = list((DAEMON_ROOT / "services" / "event_registry").rglob("*.py"))
    for name in (
        "services/child_reports.py",
        "services/report_delivery_recovery.py",
        "graph.py",
        # T5.4 class-2 bridge files (L-9)
        "routers/answer_helper.py",
        "services/job_recovery_service.py",
        "services/event_publisher.py",
        "services/midflight_qa.py",
        "services/job_feedback_observer.py",
        "services/ram_guard_events.py",
        "services/stale_task_recovery.py",
    ):
        scan_files.append(DAEMON_ROOT / name)
    for path in scan_files:
        src = path.read_text(encoding="utf-8", errors="replace")
        hits = forbidden_re.findall(src)
        assert not hits, (path, hits)
        if "resource_pid_map" in src:
            # F-11: no string literal may combine resource_pid_map with
            # "action" — a coarse adjacency check on the same line.
            for line in src.splitlines():
                if "resource_pid_map" in line:
                    assert "action" not in line, (path, line)
