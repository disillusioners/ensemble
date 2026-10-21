"""Behavioural tests for the nest-ops act + read categories.

Covers the plan §6.3-§6.6 matrix at the unit level with a FAKE manager
and a SCRATCH SQLite engine for the audit ledger:

* AC-11 / D-17 / D-18 — the protected-target gate ladder;
* AC-23a — a DIRECT window on the ACTING instance refuses (the F2
  forgery class; the GENUINE operator-direct path is ALSO refused —
  INTENDED, L-9);
* AC-23b — THE DELEGATED-FORGE RESIDUAL, PINNED AS TRUTH: a forged
  direct window on the SPAWNER → an inherited window on the
  maintenancer ⇒ the act is ADMITTED. Root-fix lane: DF-4 / critical
  note ``f4cf9c4f``. DO NOT "fix" this test to assert a refusal — that
  would re-introduce a false security property (the gate never
  inspected the root, so refusal is not what the code does).
* AC-24 / D-16 — the terminate budget + the min-age rail;
* AC-12 — self-surgery refusal;
* AC-13 — cascade=False orphans;
* AC-9a — Tier-1 fault injection rolls the act back;
* AC-9b — Tier-2 crash leaves a durable ``pending`` row;
* AC-14 — the kill switch (acts off, reads alive);
* AC-16 — the doc surface states CURRENT semantics;
* AC-25 / D-20 — reclaim terminal-only;
* AC-21 — SQL-side status pagination;
* AC-22 — a parked instance never reads as wedged.

SAFETY FENCE: the audit ledger runs on a ``tempfile`` SQLite database —
no HOME-derived or live-shaped path is ever resolved.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

import daemon.tools.instance_ops as io
from daemon.repositories.instance_ops.models import InstanceOpsAudit
from daemon.repositories.instance_ops.repository import InstanceOpsAuditRepository

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── the fake fleet ────────────────────────────────────────────────────────


@dataclass
class FakeRow:
    instance_id: str
    agent_id: str = "worker"
    parent_id: str | None = None
    status: str = "idle"
    created_at: str = field(
        default_factory=lambda: (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
    )
    last_activity_at: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def title(self):
        return None

    def to_dict(self):
        return {
            "instance_id": self.instance_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_id,
            "parent_id": self.parent_id,
            "status": self.status,
            "title": None,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.created_at,
            "last_activity_at": self.last_activity_at
            or self.created_at,
            "paused_at": None,
        }


class FakeRepo:
    def __init__(self, rows: dict[str, FakeRow]):
        self.rows = rows

    def get(self, iid):
        return self.rows.get(iid)

    def list_child_ids_permanent(self, iid):
        return [k for k, v in self.rows.items() if v.parent_id == iid]

    def get_tree_ids_permanent(self, iid):
        out = {iid}
        changed = True
        while changed:
            changed = False
            for k, v in self.rows.items():
                if v.parent_id in out and k not in out:
                    out.add(k)
                    changed = True
        return list(out)

    def get_max_last_activity_in_instances(self, ids):
        stamps = [
            datetime.fromisoformat(self.rows[i].last_activity_at or self.rows[i].created_at)
            for i in ids
            if i in self.rows
        ]
        stamps = [s.replace(tzinfo=timezone.utc) if s.tzinfo is None else s for s in stamps]
        return max(stamps) if stamps else None


class FakeManager:
    """The surface the nest-ops tools actually touch."""

    def __init__(self, rows: dict[str, FakeRow]):
        self.rows = rows
        self._instance_repository = FakeRepo(rows)
        self._user_origin_windows: dict = {}
        self._inherited_provenance_windows: dict = {}
        self._user_origin_last_stamp: dict = {}
        self._question_manager = None
        self._graph_tasks: dict = {}
        self.terminated: list[str] = []
        self.enqueued: list[tuple[str, str]] = []
        self._tmp = tempfile.mkdtemp(prefix="nestops-audit-")
        self.engine = create_engine(f"sqlite:///{self._tmp}/audit.db")
        SQLModel.metadata.create_all(self.engine)

    # instance resolution: exact-or-KeyError (fuzzy short-circuited)
    async def get_instance(self, iid):
        if iid not in self.rows:
            raise KeyError(iid)
        return iid

    def find_near_instance(self, iid, max_distance=3):
        return []

    def list_instances(self, limit=10, offset=0, project_id=None,
                       exclude_kb=True, include_descendants=False,
                       search=None, order="pinned", source=None, status=None):
        rows = [r.to_dict() for r in self.rows.values()]
        if status is not None:
            rows = [r for r in rows if r["status"] == status]
        rows.sort(key=lambda r: r["instance_id"])
        total = len(rows)
        page = rows[offset : offset + limit]
        for info in page:
            info["children"] = [
                k for k, v in self.rows.items() if v.parent_id == info["instance_id"]
            ]
        return page, total, False

    async def terminate_instance(self, instance_id, terminal_reason="aborted", audit_sink=None):
        self.terminated.append(instance_id)
        if audit_sink is not None:
            with Session(self.engine) as session:
                audit_sink(session)
                session.commit()
        return True

    async def enqueue_message(self, instance_id, message, source="api", **kw):
        self.enqueued.append((instance_id, message))
        return {"ok": True}

    def set_injection(self, instance_id, content, source=None, **kw):
        self.enqueued.append((instance_id, content))


@pytest.fixture()
def fleet() -> FakeManager:
    return FakeManager(
        {
            "target-1": FakeRow("target-1", agent_id="worker"),
            "old-worker": FakeRow(
                "old-worker",
                agent_id="worker",
                created_at=(datetime.now(timezone.utc) - timedelta(hours=8)).isoformat(),
            ),
            "child-a": FakeRow("child-a", agent_id="worker", parent_id="target-1"),
        }
    )


def _act_tools(manager, instance_id="maint-iid", agent="maintenancer"):
    return io.create_instance_ops_tools(
        manager, current_instance_id=instance_id, agent_id=agent
    )


def _by_name(tools, name):
    return next(t for t in tools if t.name == name)


def _z(dt: datetime) -> str:
    """The REAL window timestamp format: ``YYYY-MM-DDTHH:MM:SSZ`` —
    the stammer writes ``iso_plus``/``now_iso`` output, and
    ``parse_iso_utc`` fail-closes on any other spelling."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_direct(iid, source="api"):
    return {
        iid: {
            "source": source,
            "message_id": "root-msg-1",
            "stamped_at": _z(datetime.now(timezone.utc)),
            "expires_at": _z(datetime.now(timezone.utc) + timedelta(hours=1)),
        }
    }


def _stamp_inherited(iid, depth=1, root_source="api"):
    return {
        iid: {
            "root_source": root_source,
            "root_message_id": "root-msg-1",
            "depth": depth,
            "root_stamped_at": _z(datetime.now(timezone.utc)),
            "expires_at": _z(datetime.now(timezone.utc) + timedelta(hours=1)),
        }
    }


# ── the gate ladder (AC-11 / D-17 / D-18) ─────────────────────────────────


class TestProtectedGateLadder:
    def _protected_target(self, fleet):
        fleet.rows["user-chat"] = FakeRow(
            "user-chat", agent_id="leader", parent_id=None,
            metadata={"source_type": "telegram"},
        )
        return "user-chat"

    async def test_reason_required(self, fleet):
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": "target-1", "reason": "  "}
        )
        assert "reason=reason-required" in out

    async def test_self_surgery_refused(self, fleet):
        tools = _act_tools(fleet, instance_id="target-1")
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": "target-1", "reason": "r"}
        )
        assert "reason=self-surgery-refused" in out

    async def test_target_too_young(self, fleet):
        fleet.rows["fresh"] = FakeRow("fresh", created_at=datetime.now(timezone.utc).isoformat())
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": "fresh", "reason": "r"}
        )
        assert "reason=target-too-young" in out

    async def test_confirmation_required_then_mismatch(self, fleet):
        iid = self._protected_target(fleet)
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r"}
        )
        assert "reason=protected-target-confirmation-required" in out
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": "someone-else"}
        )
        assert "reason=confirmed-target-mismatch" in out

    async def test_no_user_provenance(self, fleet):
        iid = self._protected_target(fleet)
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid}
        )
        assert "reason=no-user-provenance" in out

    async def test_direct_window_on_acting_instance_refused(self, fleet):
        """AC-23a — the class D-18 GENUINELY closes. The GENUINE
        operator-direct path is ALSO refused (INTENDED — L-9)."""
        iid = self._protected_target(fleet)
        fleet._user_origin_windows = _stamp_direct("maint-iid")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid}
        )
        assert "reason=direct-provenance-insufficient" in out
        assert fleet.terminated == []  # nothing happened

    async def test_inherited_window_proceeds_ac23b(self, fleet):
        """AC-23b — THE DELEGATED-FORGE RESIDUAL, PINNED AS TRUTH.

        A FORGED direct window on the SPAWNER arms an INHERITED window
        on the maintenancer; the gate ADMITS the act because the
        inherited mint never inspects the root's own provenance.

        ROOT-FIX LANE: DF-4 / critical note ``f4cf9c4f`` (the sweep's
        ``or "api"`` fallback). DO NOT "fix" this test to assert a
        refusal — that would certify a property the code does not have;
        the residual is caught by the D-16 budget + post-hoc audit.
        """
        iid = self._protected_target(fleet)
        # the forged direct window sits on the SPAWNER, not on me…
        fleet._user_origin_windows = _stamp_direct("ari-iid", source="api")
        # …and my inherited window was minted from it.
        fleet._inherited_provenance_windows = _stamp_inherited("maint-iid", depth=1, root_source="api")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid, "dry_run": False}
        )
        # ADMITTED — the documented residual.
        import json as _json
        assert _json.loads(out)["terminated"] is True
        assert iid in fleet.terminated
        # …and the audit row records the five provenance fields for the
        # post-hoc reviewer (the speed-bump half of D-18).
        repo = InstanceOpsAuditRepository(fleet.engine)
        with Session(fleet.engine) as s:
            rows = list(
                s.exec(
                    select(InstanceOpsAudit).where(
                        InstanceOpsAudit.action == "instance_terminate",
                        InstanceOpsAudit.outcome == "committed",
                    )
                ).all()
            )
        assert rows, "the admitted act must carry a committed audit row"
        row = rows[-1]
        assert row.confirmed_target_id == iid
        assert row.provenance_class == "inherited"
        assert row.root_source == "api"
        assert row.root_message_id == "root-msg-1"
        assert row.provenance_depth == 1

    async def test_delegated_turn_on_another_lane_clears_the_window(self, fleet):
        """The refusal text must state the non-internal_agent clearing."""
        iid = self._protected_target(fleet)
        fleet._inherited_provenance_windows = {}  # cleared by the dispatch
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid}
        )
        assert "reason=no-user-provenance" in out


# ── AC-24: the budget rails ───────────────────────────────────────────────


class TestBudgetRails:
    async def test_fourth_terminate_in_24h_refuses(self, fleet):
        tools = _act_tools(fleet)
        term = _by_name(tools, "instance_terminate")
        for i in range(3):
            fleet.rows[f"t-{i}"] = FakeRow(f"t-{i}", created_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
            out = await term.ainvoke({"id": f"t-{i}", "reason": "r", "dry_run": False})
            assert "terminated" in out
        fleet.rows["t-3"] = FakeRow("t-3", created_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
        out = await term.ainvoke({"id": "t-3", "reason": "r", "dry_run": False})
        assert "reason=terminate-budget-exceeded" in out
        assert "t-3" not in fleet.terminated


# ── AC-13: cascade orphans ────────────────────────────────────────────────


class TestCascadeOrphans:
    async def test_cascade_false_with_live_descendants_refuses(self, fleet):
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": "target-1", "reason": "r", "cascade": False}
        )
        assert "reason=cascade-false-would-orphan-descendants" in out
        assert "child-a" in out  # the refusal NAMES the descendants

    async def test_allow_orphan_names_them_in_preview_and_row(self, fleet):
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": "target-1", "reason": "r", "cascade": False,
             "allow_orphan_descendants": True}
        )
        assert "child-a" in out and "NO mutation happened (dry-run)" in out
        repo = InstanceOpsAuditRepository(fleet.engine)
        with Session(fleet.engine) as s:
            rows = list(
                s.exec(
                    select(InstanceOpsAudit).where(
                        InstanceOpsAudit.action == "instance_terminate"
                    )
                ).all()
            )
        assert rows and rows[-1].dry_run is True
        assert rows[-1].outcome == "previewed"
        assert "child-a" in (rows[-1].before_snapshot or {}).get("orphans", [])


# ── AC-9a: Tier-1 fault injection ─────────────────────────────────────────


class TestTier1FaultInjection:
    async def test_audit_failure_rolls_the_terminate_back(self, fleet):
        class BoomManager(FakeManager):
            async def terminate_instance(self, instance_id, terminal_reason="aborted", audit_sink=None):
                self.terminated.append(instance_id)
                with Session(self.engine) as session:
                    audit_sink(session)  # this RAISES below
                    session.commit()

        boom = BoomManager(fleet.rows)
        tools = _act_tools(boom)

        def _bad_sink(session):
            session.add(InstanceOpsAudit(action="x", target="y", outcome="committed"))
            raise RuntimeError("audit INSERT failed")

        # inject the failure by monkey-patching the repository writer
        orig = InstanceOpsAuditRepository.write_audit_and_act
        InstanceOpsAuditRepository.write_audit_and_act = (
            lambda self, session, **kw: (_ for _ in ()).throw(RuntimeError("audit INSERT failed"))
        )
        try:
            out = await _by_name(tools, "instance_terminate").ainvoke(
                {"id": "target-1", "reason": "r", "dry_run": False}
            )
        finally:
            InstanceOpsAuditRepository.write_audit_and_act = orig
        assert "rolled back" in out
        # and NO committed row survived
        with Session(boom.engine) as s:
            rows = list(
                s.exec(
                    select(InstanceOpsAudit).where(
                        InstanceOpsAudit.outcome == "committed"
                    )
                ).all()
            )
        assert rows == []


# ── AC-9b: Tier-2 crash leaves a durable pending row ─────────────────────


class TestTier2WriteAhead:
    async def test_crash_mid_act_leaves_pending(self, fleet):
        class CrashManager(FakeManager):
            async def enqueue_message(self, instance_id, message, source="api", **kw):
                raise RuntimeError("simulated crash mid-act")

        crash = CrashManager(fleet.rows)
        crash.rows["dead-1"] = FakeRow("dead-1", status="completed")
        tools = _act_tools(crash)
        out = await _by_name(tools, "instance_reclaim").ainvoke(
            {"id": "dead-1", "message": "revive", "reason": "r", "dry_run": False}
        )
        assert "ERROR" in out
        with Session(crash.engine) as s:
            rows = list(s.exec(select(InstanceOpsAudit)).all())
        pendings = [r for r in rows if r.outcome == "pending"]
        assert pendings, "a crash mid-act must leave a durable pending row"
        assert pendings[-1].action == "instance_reclaim"
        assert pendings[-1].target == "dead-1"
        assert pendings[-1].reason == "r"


# ── AC-14: the kill switch ────────────────────────────────────────────────


class TestKillSwitch:
    async def test_off_acts_refuse_reads_live(self, fleet, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_INSTANCE_OPS_ENABLED", "0")
        act_tools = _act_tools(fleet)
        out = await _by_name(act_tools, "instance_terminate").ainvoke(
            {"id": "target-1", "reason": "r"}
        )
        assert out == io.KILL_SWITCH_REFUSAL
        assert "No mutation happened" in out
        # the reads still work
        import daemon.tools.instance_read as ir

        read_tools = ir.create_instance_read_tools(
            fleet, current_instance_id="maint-iid", agent_id="maintenancer"
        )
        out = await _by_name(read_tools, "instance_list").ainvoke({})
        assert '"rows"' in out


# ── AC-16: the doc surface ────────────────────────────────────────────────


class TestDocSurface:
    """The D-V2 lesson: the doc surface must state CURRENT semantics.

    For every act tool: the docstring/``_full_doc_``/``args_schema``
    must state the ``dry_run`` default TRUE, the ``reason`` requirement
    and the kill-switch behaviour.
    """

    def _tools(self):
        return io.create_instance_ops_tools(
            FakeManager({}), current_instance_id="m", agent_id="maintenancer"
        )

    def test_every_act_doc_states_dry_run_default_true(self):
        # Review advisory A2: per-tool default-TRUE doc pin — assert the
        # ACTUAL token each act doc carries (terminate-strength form),
        # not the bare "dry_run" substring. All six shipped act docs
        # state ``dry_run: True (default) = preview, no mutation.``;
        # terminate also carries the ``defaults to TRUE`` phrasing.
        for t in self._tools():
            doc = t._full_doc_ or ""
            assert (
                "dry_run: True (default) = preview, no mutation." in doc
                or "dry_run` defaults to TRUE" in doc
                or "dry_run` defaults to `True" in doc
            ), t.name

    def test_terminate_doc_states_the_current_semantics(self):
        tools = self._tools()
        term = _by_name(tools, "instance_terminate")._full_doc_
        assert "dry_run` defaults to TRUE" in term or "dry_run` defaults to `True" in term or "defaults to TRUE" in term
        assert "reason` is required" in term
        assert "INHERITED" in term  # D-18 current semantics
        assert "kill switch" in term.lower() or "kill-switch" in term.lower()
        fields = _by_name(tools, "instance_terminate").args_schema.model_json_schema()["properties"]
        assert "dry_run" in fields
        assert "confirmed_target_id" in fields  # D-17 (not user_confirmed)
        assert "user_confirmed" not in fields

    def test_reclaim_doc_states_terminal_only(self):
        doc = _by_name(self._tools(), "instance_reclaim")._full_doc_
        assert "TERMINAL" in doc and "target-not-terminal" in doc

    def test_mark_doc_states_the_tiers(self):
        doc = _by_name(self._tools(), "instance_mark")._full_doc_
        assert "Tier-1" in doc or "TIER-1" in doc
        assert "Tier-2" in doc or "TIER-2" in doc
        assert "There is NO `unprotect`" in doc or "NO `unprotect`" in doc

    def test_answer_doc_states_the_parity(self):
        doc = _by_name(self._tools(), "instance_answer")._full_doc_
        for token in ("no-pending-question", "question-pack-lost", "stale-question-pack"):
            assert token in doc


# ── AC-25 / D-20: reclaim ─────────────────────────────────────────────────


class TestReclaim:
    async def test_non_terminal_refuses(self, fleet):
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_reclaim").ainvoke(
            {"id": "target-1", "message": "m", "reason": "r"}
        )
        assert "reason=target-not-terminal" in out

    async def test_terminal_revives_with_message(self, fleet):
        fleet.rows["done-1"] = FakeRow("done-1", status="completed")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_reclaim").ainvoke(
            {"id": "done-1", "message": "welcome back", "reason": "r", "dry_run": False}
        )
        assert "revived" in out
        assert ("done-1", "welcome back") in fleet.enqueued

    async def test_self_refuses(self, fleet):
        tools = _act_tools(fleet, instance_id="done-1")
        fleet.rows["done-1"] = FakeRow("done-1", status="completed")
        out = await _by_name(tools, "instance_reclaim").ainvoke(
            {"id": "done-1", "message": "m", "reason": "r"}
        )
        assert "reason=self-surgery-refused" in out


# ── AC-21 / AC-22: the read tools ─────────────────────────────────────────


class TestReadTools:
    def _read(self, fleet):
        import daemon.tools.instance_read as ir

        return ir.create_instance_read_tools(
            fleet, current_instance_id="m", agent_id="maintenancer"
        )

    async def test_unknown_status_is_a_typed_refusal(self, fleet):
        out = await _by_name(self._read(fleet), "instance_list").ainvoke(
            {"status": "zombie"}
        )
        assert "reason=unknown-status-value" in out

    async def test_invalid_limit_is_a_typed_refusal(self, fleet):
        out = await _by_name(self._read(fleet), "instance_list").ainvoke({"limit": 0})
        assert "reason=invalid-limit" in out

    async def test_sql_side_status_filter_paginates_correctly(self, fleet):
        """AC-21 — the status filter is applied to the WHOLE fleet, so
        page 2 continues correctly (never a pinned-ordered subset)."""
        for i in range(5):
            fleet.rows[f"run-{i}"] = FakeRow(f"run-{i}", status="running")
        out = await _by_name(self._read(fleet), "instance_list").ainvoke(
            {"status": "running", "limit": 2, "offset": 0}
        )
        import json as _json

        page1 = _json.loads(out)
        assert page1["count"] == 2 and page1["total_matching"] == 5
        assert all(r["status"] == "running" for r in page1["rows"])
        out2 = await _by_name(self._read(fleet), "instance_list").ainvoke(
            {"status": "running", "limit": 2, "offset": 4}
        )
        page3 = _json.loads(out2)
        assert page3["count"] == 1  # 5 total: 2 + 2 + 1

    async def test_census_row_has_no_hydration(self, fleet):
        import json as _json

        out = await _by_name(self._read(fleet), "instance_list").ainvoke({})
        rows = _json.loads(out)["rows"]
        assert rows
        for r in rows:
            assert "current_task" not in r
            assert "last_messages" not in r
            assert "liveness" in r and "stale" in r and "pause_marker" in r

    async def test_parked_instance_never_reads_as_wedged(self, fleet):
        """AC-22 — a DELIBERATELY PARKED instance reports its marker and
        its liveness value does NOT change with staleness."""
        fleet.rows["parked"] = FakeRow(
            "parked",
            status="paused",
            created_at=(datetime.now(timezone.utc) - timedelta(hours=8)).isoformat(),
            last_activity_at=(datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
            metadata={
                "question_paused": {
                    "deliberate": True,
                    "cleared_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        import json as _json

        out = await _by_name(self._read(fleet), "instance_list").ainvoke({"status": "paused"})
        row = next(r for r in _json.loads(out)["rows"] if r["instance_id"] == "parked")
        assert row["pause_marker"] and row["pause_marker"].get("deliberate") is True
        assert row["stale"] is True
        assert row["liveness"] == "paused"  # unchanged by staleness

    async def test_health_report_has_six_detectors_and_never_repairs(self, fleet):
        import json as _json

        out = await _by_name(self._read(fleet), "instance_health").ainvoke({})
        payload = _json.loads(out)
        names = [d["detector"] for d in payload["detectors"]]
        assert names == [
            "stale-running",
            "waiting-on-a-dead-parent",
            "question-paused-without-task-row",
            "zombie-pending",
            "orphans",
            "failed-with-unreported-result",
        ]
        # disagreement pins: 2 and 5 observable; 1 and 6 honest gaps
        by = {d["detector"]: d for d in payload["detectors"]}
        assert by["waiting-on-a-dead-parent"]["disagreement_pin"] == "WaitingChildrenWatchdog.wedge_episodes()"
        assert by["orphans"]["disagreement_pin"] == "OrphanWatcherSweepService.counters()"
        assert by["stale-running"]["disagreement_pin"] is None
        assert by["failed-with-unreported-result"]["disagreement_pin"] is None
        # no mutation happened anywhere
        assert fleet.terminated == [] and fleet.enqueued == []

    async def test_capacity_summary_includes_audit_pending(self, fleet):
        import json as _json

        InstanceOpsAuditRepository(fleet.engine).write_audit_ahead(
            action="instance_nudge", target="t", reason="r"
        )
        out = await _by_name(self._read(fleet), "fleet_capacity_summary").ainvoke({})
        payload = _json.loads(out)
        assert payload["audit_pending"]["count"] >= 1
        assert "generated_at" in payload

    async def test_unauthorized_agent_gets_nothing(self, fleet):
        import daemon.tools.instance_read as ir

        tools = ir.create_instance_read_tools(fleet, agent_id="leader")
        out = await _by_name(tools, "instance_list").ainvoke({})
        assert "not permitted" in out
