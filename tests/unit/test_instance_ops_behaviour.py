"""Behavioural tests for the nest-ops act + read categories.

Covers the plan §6.3-§6.6 matrix at the unit level with a FAKE manager
and a SCRATCH SQLite engine for the audit ledger:

* AC-11 / D-17 / D-18 — the protected-target gate ladder;
* AC-23a — a GENUINE direct window on the ACTING instance ADMITS
  (DF-4 / P2 — D-18's "direct refused on purpose" is RETIRED BY
  DESIGN; the gate keys on LIVENESS, not on the window's class);
* AC-23b — the GENUINE delegated chain ADMITS the protected act
  (DF-4 / P3 — this test is pin (ii)). The FORGED chain is unreachable
  through the real sweep path: pin (iii), the REAL-chain e2e
  (``tests/postgres/test_df4_provenance_closure.py``), proves the
  refusal leg. DO NOT "fix" this test to assert a refusal — that
  would re-introduce a false security property (a live window of either
  shape is admitted BY DESIGN).
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

DF-4 TEST RENAMES (the plan's canonical pin names):
  test_direct_window_on_acting_instance_refused
      → test_genuine_direct_window_admits_protected_act  (AC-23a, pin (i))
  test_inherited_window_proceeds_ac23b
      → test_genuine_inherited_window_admits_protected_act (AC-23b, pin (ii))

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
from daemon.repositories.instance.repository import KB_AGENT_IDS
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

    def count_fleet_by_status_and_agent(self, *, exclude_kb: bool = True):
        """The census shape the read tool consumes (F2).

        Mirrors the real repository method over the fake fleet: ONE
        grouped pass + an independent COUNT over the same WHERE.
        """
        from daemon.repositories.instance.repository import FleetCounts

        groups: dict[tuple[str, str], int] = {}
        total = 0
        for row in self.rows.values():
            if exclude_kb and row.agent_id in KB_AGENT_IDS:
                continue
            groups[(row.status, row.agent_id)] = (
                groups.get((row.status, row.agent_id), 0) + 1
            )
            total += 1
        return FleetCounts(
            groups=[(s, a, c) for (s, a), c in sorted(groups.items())],
            total=total,
        )

    def set_metadata(self, iid, key, value, session=None):
        # Test-side stand-in for the repo's dotted-key metadata write
        # (the real one joins `session`; the fake fleet is in-memory).
        self.rows[iid].metadata[key] = value

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
        """F6 (D-8): the provenance leg is APPLY-ONLY.

        A CONFIRMED dry-run preview on a protected target REPORTS the
        requirement as data (``act_gate_would_pass``) — it does not
        refuse, because the operator cannot self-satisfy a live
        user-origin window. The destructive act keeps the gate exactly
        as it was: it refuses with ``no-user-provenance``.

        (Updated, never deleted — the old assertion pinned the refusal
        on the DEFAULT preview path.)
        """
        import json as _json

        iid = self._protected_target(fleet)
        tools = _act_tools(fleet)

        # The preview path: the requirement is REPORTED, not refused.
        preview = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid}
        )
        assert "NO mutation happened (dry-run)" in preview
        payload = _json.loads(preview.split("\nNO mutation happened")[0])
        assert payload["act_gate_would_pass"] == {
            "ok": False,
            "reason": "no-user-provenance",
        }
        # The preview carries TOKENS ONLY — never the raw refusal string.
        assert "confirmed_target_id=" not in preview

        # The apply path: the gate refuses exactly as before.
        applied = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid, "dry_run": False}
        )
        assert "reason=no-user-provenance" in applied

    async def test_genuine_direct_window_admits_protected_act(self, fleet):
        """AC-23a (DF-4 / P2, RE-POINTED) — this test IS pin (i): the
        GENUINE operator-direct path is ADMITTED.

        D-18's "a DIRECT window on the acting instance is refused on
        purpose" property is RETIRED BY DESIGN. It refused the genuine
        operator path (defect B) while never blocking the delegated
        forge — the inherited mint copies the root without inspecting
        the root's own provenance. The gate now keys on LIVENESS; the
        window's class survives as OBSERVABILITY only, recorded in the
        audit row.
        """
        iid = self._protected_target(fleet)
        fleet._user_origin_windows = _stamp_direct("maint-iid", source="api")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid, "dry_run": False}
        )
        # ADMITTED — the act proceeds.
        import json as _json
        assert _json.loads(out)["terminated"] is True
        assert iid in fleet.terminated
        # The VERDICT-level depth is 0 — a direct window IS the root.
        verdict = io.restart_provenance(fleet, "maint-iid")
        assert verdict.ok is True
        assert verdict.pclass == "direct"
        assert verdict.depth == 0
        # …and the committed audit row records the four provenance fields.
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
        assert row.provenance_class == "direct"
        assert row.root_source == "api"
        assert row.root_message_id == "root-msg-1"
        # F2: the write path coerces ``verdict.depth or None`` — a
        # depth-0 verdict is stored as NULL, so the ROW depth is None.
        assert row.provenance_depth is None

    async def test_genuine_inherited_window_admits_protected_act(self, fleet):
        """AC-23b (amended by DF-4 / P3) — this test IS pin (ii): a
        GENUINE delegated chain ADMITS the protected act.

        DF-4 splits the old scenario in two:

        * THIS test — the GENUINE chain: a genuine user-origin window on
          the PARENT is inherited by the delegated child, and the act is
          ADMITTED (audit ``class="inherited"``, ``depth>=1``,
          ``root_source=<genuine>``).
        * pin (iii) — the FORGED chain, the REAL-chain e2e
          (``tests/postgres/test_df4_provenance_closure.py``): a sweep
          wake armed with NO user origin can no longer mint a DIRECT
          root (DF-4 / P1 closed the sweep's fallback), so the delegated
          child inherits NOTHING and the gate REFUSES with
          ``no-user-provenance``.

        This test is NOT flipped to a refusal assertion: a live window of
        either shape is admitted BY DESIGN (the gate keys on liveness).

        DISCLOSURE — the closure claim is SCOPED, not total. A window
        forged through a NON-sweep surface — the REST ingress
        (``daemon/routers/jobs_crud.py`` / ``daemon/routers/messages.py``,
        critical note ``257488dc``) — remains reachable and admitted.
        DF-4 does NOT claim total closure; the compensating controls are
        scopes, the acting-instance allow-list, named-target
        confirmation, the D-16 budget, the two-tier audit ledger and the
        ``ENSEMBLE_INSTANCE_OPS_ENABLED`` kill switch. The nest-ops
        retraction trace (``nest-ops/decisions.md``) remains the
        authoritative history — DF-4 appends, it does not rewrite.
        """
        iid = self._protected_target(fleet)
        # the GENUINE direct window sits on the SPAWNER…
        fleet._user_origin_windows = _stamp_direct("ari-iid", source="api")
        # …and my inherited window was minted from it.
        fleet._inherited_provenance_windows = _stamp_inherited("maint-iid", depth=1, root_source="api")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid, "dry_run": False}
        )
        # ADMITTED — the genuine delegated chain.
        import json as _json
        assert _json.loads(out)["terminated"] is True
        assert iid in fleet.terminated
        # …and the audit row records the five provenance fields for the
        # post-hoc reviewer (the observability half of D-18).
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
        """AC-23b (DF-4 / P2, re-pointed) — with NO live window the gate
        REFUSES with ``no-user-provenance`` on the APPLY path.

        The cleared-window shape is SIMULATED (the inherited store is
        emptied by hand); this test does NOT exercise the dispatch that
        clears it. It asserts the refusal TOKEN only — the refusal TEXT
        that spelled out the "a non-internal_agent delegation CLEARS the
        inherited window" rule lived in the retired ``direct-provenance``-
        ``insufficient`` refusal (DF-4 / P2) and no longer ships.

        (The retired token is spelled in TWO HALVES on purpose — it must
        not survive anywhere under ``daemon/`` or ``tests/``.)

        F6 (D-8) UPDATE (never deleted): the default call is a PREVIEW,
        and the provenance leg is apply-only — the preview REPORTS the
        requirement as ``act_gate_would_pass`` instead of refusing. The
        destructive act still refuses with the token, and that is what
        this pin asserts (plus the preview's data rendering).
        """
        import json as _json

        iid = self._protected_target(fleet)
        fleet._inherited_provenance_windows = {}  # cleared by the dispatch
        tools = _act_tools(fleet)

        preview = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid}
        )
        payload = _json.loads(preview.split("\nNO mutation happened")[0])
        assert payload["act_gate_would_pass"]["reason"] == "no-user-provenance"

        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid, "dry_run": False}
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

    async def test_protected_budget_refuses_second_committed_terminate(self, fleet):
        """D-16 PROTECTED rail (N=1/24h) — the containment behind the
        admitted DELEGATED-FORGE residual (DF-4 / critical note
        ``f4cf9c4f``). A protected target needs the confirmation plus an
        inherited window (the AC-23b fixture) to be terminated at all.

        Counting rule: only ``outcome="committed"`` rows consume the
        budget — a pending or refused row consumes NOTHING.
        """
        # Two PROTECTED targets (the same shape as the ladder fixture).
        fleet.rows["user-chat"] = FakeRow(
            "user-chat", agent_id="leader", parent_id=None,
            metadata={"source_type": "telegram"},
        )
        fleet.rows["user-chat-2"] = FakeRow(
            "user-chat-2", agent_id="leader", parent_id=None,
            metadata={"source_type": "telegram"},
        )
        # Non-committed rows must consume NOTHING: a PENDING
        # protected-flagged row and a REFUSED row.
        repo = InstanceOpsAuditRepository(fleet.engine)
        repo.write_audit_ahead(
            action="instance_terminate",
            target="user-chat-2",
            reason="pre-existing pending",
            before={"protected": True},
        )
        repo.write_refusal(
            action="instance_terminate",
            target="user-chat-2",
            reason="pre-existing refusal",
            error_message="reason=some-previous-refusal",
            before={"protected": True},
        )

        def _arm_windows():
            fleet._user_origin_windows = _stamp_direct("ari-iid", source="api")
            fleet._inherited_provenance_windows = _stamp_inherited(
                "maint-iid", depth=1, root_source="api"
            )

        tools = _act_tools(fleet)
        term = _by_name(tools, "instance_terminate")

        # 1st protected terminate: ADMITTED — the pending/refused rows
        # consumed nothing — and it CONSUMES the N=1 protected budget.
        _arm_windows()
        out = await term.ainvoke(
            {
                "id": "user-chat",
                "reason": "r",
                "confirmed_target_id": "user-chat",
                "dry_run": False,
            }
        )
        import json as _json
        assert _json.loads(out)["terminated"] is True
        assert "user-chat" in fleet.terminated

        # 2nd protected terminate: REFUSED by the protected budget.
        _arm_windows()
        out2 = await term.ainvoke(
            {
                "id": "user-chat-2",
                "reason": "r",
                "confirmed_target_id": "user-chat-2",
                "dry_run": False,
            }
        )
        assert "reason=terminate-budget-exceeded" in out2
        assert "protected-target budget" in out2
        assert "user-chat-2" not in fleet.terminated


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
        # DF-4 / P2 (AC-16, re-pointed): the CURRENT semantics are
        # LIVENESS-ONLY. The class-keyed requirement (D-18's
        # "the window must be INHERITED") is RETIRED — the window's
        # shape is recorded in the audit ledger, not gated on.
        assert "live user-origin" in term
        assert "NOT a gate input" in term
        assert "INHERITED" not in term
        assert "kill switch" in term.lower() or "kill-switch" in term.lower()
        # F6 (D-8) — the apply-only sentence. The gate SPLITS at the
        # dry-run fork: the confirmation conditions run on BOTH paths and
        # ONLY the user-provenance condition is apply-only. A preview
        # reports what apply requires (``act_gate_would_pass``) instead
        # of demanding a window the caller cannot self-satisfy.
        assert "BOTH paths" in term
        assert "(APPLY" in term
        assert "ONLY — a dry-run preview reports" in term
        assert "act_gate_would_pass" in term
        # F6 (D-8) — the apply-only sentence. The gate SPLITS at the
        # dry-run fork: the confirmation conditions run on BOTH paths and
        # ONLY the user-provenance condition is apply-only. A preview
        # reports what apply requires (``act_gate_would_pass``) instead
        # of demanding a window the caller cannot self-satisfy.
        assert "BOTH paths" in term
        assert "(APPLY" in term
        assert "ONLY — a dry-run preview reports" in term
        assert "act_gate_would_pass" in term
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

    async def test_health_report_has_seven_detectors_and_never_repairs(self, fleet):
        """F3 (2026-10-24): the detector list gained the SEVENTH class,
        ``never-dispatched-husk`` — appended LAST so the six existing
        indices stay stable. This pin was ``..._has_six_detectors_...``
        before the nest-ops shakedown patch; the list grew by one
        DETECTED CLASS, not by a reshuffle."""
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
            "never-dispatched-husk",
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


# ── Review fixes (2026-10-21): H1 / M2 / M5 / M8 pins ─────────────────────


class TestGetHonestyReviewFixes:
    """H1: spawned_by must attribute the PARENT, never the target.
    M2: the dotted marker keys must round-trip through instance_get.
    M5: DismissCascadeFailed settles `error`, never rests `pending`.
    M8: both fleet scans surface `scan_truncated`."""

    def _read(self, fleet):
        import daemon.tools.instance_read as ir

        return ir.create_instance_read_tools(
            fleet, current_instance_id="m", agent_id="maintenancer"
        )

    async def test_spawned_by_is_the_parents_agent_not_the_targets(self, fleet):
        import json as _json

        fleet.rows["parent-1"] = FakeRow("parent-1", agent_id="leader")
        fleet.rows["kid-1"] = FakeRow(
            "kid-1", agent_id="worker", parent_id="parent-1"
        )
        out = await _by_name(self._read(fleet), "instance_get").ainvoke(
            {"id": "kid-1"}
        )
        payload = _json.loads(out)
        assert payload["spawned_by"]["parent_id"] == "parent-1"
        # H1: the PARENT's agent id — the old code returned the target's own.
        assert payload["spawned_by"]["agent_id"] == "leader"
        assert payload["spawned_by"]["agent_id"] != payload["agent_id"]

    async def test_spawned_by_root_is_null_with_explicit_marker(self, fleet):
        import json as _json

        out = await _by_name(self._read(fleet), "instance_get").ainvoke(
            {"id": "target-1"}
        )
        payload = _json.loads(out)
        assert payload["spawned_by"]["agent_id"] is None
        assert payload["spawned_by"]["note"] == "no-parent"
        # NEVER the target's own agent, even when unknown.
        assert payload["spawned_by"]["agent_id"] != payload["agent_id"]

    async def test_marker_write_then_get_round_trips_both_markers(self, fleet):
        import json as _json

        tools = _act_tools(fleet)
        for marker in ("protect", "suspect"):
            out = await _by_name(tools, "instance_mark").ainvoke(
                {
                    "id": "old-worker",
                    "marker": marker,
                    "note": "round-trip",
                    "reason": "M2 round-trip verification",
                    "dry_run": False,
                }
            )
            assert "ERROR" not in out, out
            got = _json.loads(
                await _by_name(self._read(fleet), "instance_get").ainvoke(
                    {"id": "old-worker"}
                )
            )
            md = got["instance_metadata"]
            # M2: the DOTTED keys survive the allowlist (the dead
            # top-level "instance_ops" entry never matched them).
            assert f"instance_ops.{marker}" in md, md
            expected_flag = "protected" if marker == "protect" else "suspected"
            assert md[f"instance_ops.{marker}"].get(expected_flag) is True

    async def test_dismiss_cascade_failure_settles_error_not_pending(
        self, fleet, monkeypatch
    ):
        import daemon.services.instance_ops_service as svc

        async def _boom(manager, target_id, live_hub=None):
            raise svc.DismissCascadeFailed("resume cascade exploded")

        monkeypatch.setattr(svc, "dismiss_question_pack", _boom)
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_dismiss").ainvoke(
            {"id": "target-1", "reason": "cascade retry demo", "dry_run": False}
        )
        assert "dismiss-cascade-failed" in out
        assert "ERROR" not in out  # typed refusal, not a generic error
        with Session(fleet.engine) as s:
            rows = [
                r
                for r in s.exec(select(InstanceOpsAudit)).all()
                if r.action == "instance_dismiss"
            ]
        assert rows, "the write-ahead row must exist"
        row = rows[-1]
        assert row.outcome == "error"
        assert row.outcome != "pending"
        assert "dismiss-cascade-failed" in (row.error_message or "")

    async def test_health_and_capacity_surface_scan_truncated(self, fleet):
        import json as _json

        health = _json.loads(
            await _by_name(self._read(fleet), "instance_health").ainvoke({})
        )
        assert health["scan_truncated"] is False
        cap = _json.loads(
            await _by_name(self._read(fleet), "fleet_capacity_summary").ainvoke({})
        )
        assert cap["scan_truncated"] is False


# ── F6 / F7 / F9 / F10 — the ops-side act defects (Phase 3) ──────────────
#
# F6  — the protected gate SPLITS at the dry_run fork (D-8): the
#        confirmation conditions gate BOTH paths; only the
#        user-provenance condition is apply-only.
# F7  — ``already_protected`` means PROTECTED, not "marked"; the three
#        protection layers have ONE home (``_protection_reasons``).
# F9  — the reclaim refusal must not recommend a dispatch.
# F10 — the mark apply echoes the committed audit row id.


class TestGateTokenStructure:
    """The gate's refusal token is RETURNED STRUCTURALLY (tidier fold).

    The pre-fix shape recovered the token by re-parsing the refusal
    string with an ANCHORED regex, and the preview's
    ``would_refuse_with`` carried a HARDCODED token. A regex non-match
    therefore degraded a CONFIRMATION refusal into preview data
    attributed to the provenance token — a silent mislabel.

    This class pins the STRUCTURAL contract:

      * the gate returns ``(refusal, token)``;
      * the token IS the constant that minted the refusal;
      * the token and the refusal string never drift apart (the token
        is EXACTLY the one embedded in the envelope);
      * a passing gate returns ``(None, None)`` — never a token;
      * the preview's ``would_refuse_with`` is DERIVED from the token
        the gate actually returned (it agrees with
        ``act_gate_would_pass.reason``).

    PLANNED MUTATION (one behaviour per run):
      * make the gate return the refusal string only (the pre-fix
        shape) → the structural-arity pin RED;
      * mint the refusal with a DIFFERENT token than the one returned
        → the no-drift pin RED;
      * hardcode ``would_refuse_with`` back to the provenance token
        → the derivation pin RED (on a gate whose token is not that
        one).
    """

    @staticmethod
    def _verdict(ok: bool):
        from daemon.tools.provenance import ProvenanceVerdict

        return ProvenanceVerdict(ok=ok, pclass="direct" if ok else "")

    def _gate(self, *, confirmed, ok, target="target-1"):
        return io._protected_gate(
            target_id=target,
            confirmed_target_id=confirmed,
            verdict=self._verdict(ok),
            label="INSTANCE_TERMINATE",
        )

    def test_a_passing_gate_returns_no_refusal_and_no_token(self):
        refusal, token = self._gate(confirmed="target-1", ok=True)
        assert refusal is None
        assert token is None

    def test_the_gate_returns_a_two_tuple(self):
        """The arity is STRUCTURAL — a bare refusal string is RED."""
        result = self._gate(confirmed=None, ok=True)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_the_confirmation_required_token_is_the_constant(self):
        refusal, token = self._gate(confirmed=None, ok=True)
        assert token == io.GATE_TOKEN_CONFIRMATION_REQUIRED
        assert f"reason={io.GATE_TOKEN_CONFIRMATION_REQUIRED}:" in refusal

    def test_the_confirmation_mismatch_token_is_the_constant(self):
        refusal, token = self._gate(confirmed="other-id", ok=True)
        assert token == io.GATE_TOKEN_CONFIRMATION_MISMATCH
        assert f"reason={io.GATE_TOKEN_CONFIRMATION_MISMATCH}:" in refusal

    def test_the_provenance_token_is_the_constant(self):
        refusal, token = self._gate(confirmed="target-1", ok=False)
        assert token == io.GATE_TOKEN_NO_USER_PROVENANCE
        assert f"reason={io.GATE_TOKEN_NO_USER_PROVENANCE}:" in refusal

    def test_the_refusal_strings_are_byte_identical_to_the_pre_fix_shape(self):
        """The refusal STRING set is unchanged — only its derivation moved.

        The pre-fix strings are reproduced VERBATIM here, so a reworded
        envelope is RED. (The refusal envelope's shape is pinned by the
        house format: ``Error: <label> REFUSED — reason=<token>: <msg>``.)
        """
        refusal, _token = self._gate(confirmed=None, ok=True)
        assert refusal.startswith(
            "Error: INSTANCE_TERMINATE REFUSED — reason="
            f"{io.GATE_TOKEN_CONFIRMATION_REQUIRED}: "
        )
        assert "'target-1' is a protected target." in refusal
        assert "confirmed_target_id='target-1'" in refusal

        refusal, _token = self._gate(confirmed="other-id", ok=True)
        assert refusal.startswith(
            "Error: INSTANCE_TERMINATE REFUSED — reason="
            f"{io.GATE_TOKEN_CONFIRMATION_MISMATCH}: "
        )
        assert "confirmed_target_id='other-id' does not name the target" in refusal

    async def test_the_preview_derives_would_refuse_with_from_the_token(
        self, fleet
    ):
        """``would_refuse_with`` AGREES with the gate's own token.

        The pre-fix shape hardcoded the provenance token there. The
        derivation is now structural, so the two payload fields can
        never disagree.
        """
        import json as _json

        iid = "user-chat"
        fleet.rows[iid] = FakeRow(
            iid, agent_id="leader", metadata={"source_type": "telegram"}
        )
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid}
        )
        payload = _json.loads(out.split("\nNO mutation happened")[0])
        reason = payload["act_gate_would_pass"]["reason"]
        assert payload["act_gate"]["would_refuse_with"] == [reason]
        assert reason == io.GATE_TOKEN_NO_USER_PROVENANCE


class TestF6GateSplit:
    """F6 — the gate split (D-8 minimal reading).

    PLANNED MUTATION (one behaviour per run — T4.4, ops-side §7.2):
      * move the CONFIRMATION conditions below the dry-run fork →
        F6-i(a) RED;
      * move the PROVENANCE condition back above the fork → F6-i(b) RED;
      * delete the gate call on the apply path → F6-ii RED;
      * make the preview write ``outcome="committed"`` → F6-iii RED;
      * swap the guard order so the protected gate runs BEFORE the
        budget/cascade rails → F6-iv RED (the budget token no longer
        wins).
    """

    def _protected_target(self, fleet):
        fleet.rows["user-chat"] = FakeRow(
            "user-chat", agent_id="leader", parent_id=None,
            metadata={"source_type": "telegram"},
        )
        return "user-chat"

    async def test_f6_i_a_unconfirmed_preview_still_refuses(self, fleet):
        """F6-i(a): the CONFIRMATION conditions gate BOTH paths."""
        iid = self._protected_target(fleet)
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r"}
        )
        assert "reason=protected-target-confirmation-required" in out
        # A refusal is NOT a preview body.
        assert "NO mutation happened (dry-run)" not in out

    async def test_f6_i_b_confirmed_preview_reports_the_gate(self, fleet):
        """F6-i(b): a confirmed preview REPORTS the apply requirement."""
        import json as _json

        iid = self._protected_target(fleet)
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid}
        )
        assert "NO mutation happened (dry-run)" in out
        payload = _json.loads(out.split("\nNO mutation happened")[0])
        assert payload["protected"] is True
        assert payload["act_gate_would_pass"] == {
            "ok": False,
            "reason": "no-user-provenance",
        }
        assert payload["act_gate"]["would_refuse_with"] == ["no-user-provenance"]
        # The preview carries TOKENS ONLY — never the raw refusal string.
        assert "REFUSED" not in out
        # The target is untouched.
        assert iid not in fleet.terminated
        # A ``previewed`` row exists (system observability, budget-neutral).
        with Session(fleet.engine) as s:
            rows = [
                r
                for r in s.exec(select(InstanceOpsAudit)).all()
                if r.action == "instance_terminate"
            ]
        assert rows, "the preview row must exist"
        assert rows[-1].outcome == "previewed"

    async def test_f6_ii_apply_still_refuses(self, fleet):
        """F6-ii: the destructive act keeps ALL gates."""
        iid = self._protected_target(fleet)
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r", "confirmed_target_id": iid, "dry_run": False}
        )
        assert "reason=no-user-provenance" in out
        assert iid not in fleet.terminated
        with Session(fleet.engine) as s:
            rows = [
                r
                for r in s.exec(select(InstanceOpsAudit)).all()
                if r.action == "instance_terminate"
            ]
        assert rows[-1].outcome == "refused"

    async def test_f6_iii_preview_is_budget_neutral(self, fleet):
        """F6-iii: a preview does not consume the protected budget."""
        iid = self._protected_target(fleet)
        tools = _act_tools(fleet)
        term = _by_name(tools, "instance_terminate")
        for _ in range(3):
            out = await term.ainvoke(
                {"id": iid, "reason": "r", "confirmed_target_id": iid}
            )
            assert "NO mutation happened (dry-run)" in out
        repo = InstanceOpsAuditRepository(fleet.engine)
        assert repo.count_committed_protected_terminates(hours=24) == 0

    async def test_f6_iv_budget_precedes_the_protected_gate(self, fleet):
        """F6-iv: the precedence inversion — guards 8-9 run FIRST.

        An exhausted budget on a protected target yields the BUDGET
        token, not the confirmation token. Both are refusals — no
        authority is granted either way — but the precedence is a
        behaviour change outside the provenance-only claim, so it is
        pinned.
        """
        iid = self._protected_target(fleet)
        # Exhaust the PROTECTED budget (N=1/24h) with a committed row.
        # Seed a COMMITTED protected-terminate row — the rail counts
        # committed rows whose ``before_snapshot["protected"]`` flag is
        # set (no extra column; the rows are read in Python).
        with Session(fleet.engine) as session:
            row = InstanceOpsAudit(
                action="instance_terminate",
                target=iid,
                reason="pre-existing committed protected terminate",
                dry_run=False,
                before_snapshot={"protected": True},
                outcome="committed",
            )
            session.add(row)
            session.commit()

        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "r"}
        )
        assert "reason=terminate-budget-exceeded" in out
        assert "protected-target-confirmation-required" not in out


class TestF7ProtectionReflection:
    """F7 — one home for the protection determination.

    PLANNED MUTATION (T4.4, ops-side §7.2): revert
    ``_protection_reasons`` to the marker-only expression → F7-i (the
    leader static case) RED; drop the chat-root leg → F7-ii RED; scope
    the marker-already-set refusal to reasons WITHOUT ``marker`` →
    F7-iii RED; refuse instead of succeed-with-disclosure on the
    static+marker apply → F7-iv RED.
    """

    async def test_f7_i_leader_target_previews_as_protected(self, fleet):
        import json as _json

        fleet.rows["leader-row"] = FakeRow("leader-row", agent_id="leader")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_mark").ainvoke(
            {"id": "leader-row", "marker": "protect", "reason": "r"}
        )
        payload = _json.loads(out.split("\nNO mutation happened")[0])
        assert payload["already_protected"] is True
        assert payload["protection_reasons"] == ["leader"]

    async def test_f7_ii_chat_root_target_previews_as_protected(self, fleet):
        import json as _json

        fleet.rows["chat-root"] = FakeRow(
            "chat-root", agent_id="worker", parent_id=None,
            metadata={"source_type": "telegram"},
        )
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_mark").ainvoke(
            {"id": "chat-root", "marker": "protect", "reason": "r"}
        )
        payload = _json.loads(out.split("\nNO mutation happened")[0])
        assert payload["already_protected"] is True
        assert payload["protection_reasons"] == ["chat-root"]

    async def test_f7_iii_marker_target_and_the_idempotent_refusal(self, fleet):
        import json as _json

        fleet.rows["marked"] = FakeRow(
            "marked",
            agent_id="worker",
            metadata={
                "instance_ops.protect": {"protected": True},
            },
        )
        tools = _act_tools(fleet)
        # The DYNAMIC refusal is byte-identical for the dynamic case — it
        # fires on the protect path, preview included (it is not a
        # preview-scoped refusal).
        refused = await _by_name(tools, "instance_mark").ainvoke(
            {"id": "marked", "marker": "protect", "reason": "r"}
        )
        assert "reason=marker-already-set" in refused
        assert "already carries a protect marker" in refused
        # The preview echo (a non-protect marker does not trip the
        # refusal) shows the MARKER layer as the reason.
        out = await _by_name(tools, "instance_mark").ainvoke(
            {"id": "marked", "marker": "suspect", "reason": "r"}
        )
        payload = _json.loads(out.split("\nNO mutation happened")[0])
        assert payload["already_protected"] is True
        assert payload["protection_reasons"] == ["marker"]

    async def test_f7_iv_static_plus_marker_apply_succeeds_with_disclosure(
        self, fleet
    ):
        """F7-iv: protection is MONOTONE — the operator may record the
        intent. The apply SUCCEEDS and DISCLOSES."""
        import json as _json

        fleet.rows["leader-row"] = FakeRow("leader-row", agent_id="leader")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_mark").ainvoke(
            {"id": "leader-row", "marker": "protect", "reason": "r", "dry_run": False}
        )
        payload = _json.loads(out)
        assert payload["marked"] is True
        assert payload["already_protected"] is True
        assert payload["protection_reasons"] == ["leader"]
        # The marker was written.
        assert fleet.rows["leader-row"].metadata["instance_ops.protect"][
            "protected"
        ] is True
        # The disclosure is in the ledger row too.
        with Session(fleet.engine) as s:
            rows = [
                r
                for r in s.exec(select(InstanceOpsAudit)).all()
                if r.action == "instance_mark"
            ]
        assert rows[-1].outcome == "committed"
        assert rows[-1].before_snapshot.get("protection_reasons") == ["leader"]


class TestF10AuditRowId:
    """F10 — the mark apply echoes the committed audit row id.

    PLANNED MUTATION (T4.4, arch §11.3): drop the echoed row id (leave
    the ``write_audit_and_act`` result unassigned) → ALL THREE pins
    here RED.
    """

    async def test_f10_i_protect_echoes_the_row_id(self, fleet):
        import json as _json

        fleet.rows["target-1"] = FakeRow("target-1", agent_id="worker")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_mark").ainvoke(
            {"id": "target-1", "marker": "protect", "reason": "r", "dry_run": False}
        )
        payload = _json.loads(out)
        row_id = payload["audit_row_id"]
        assert row_id, "the apply must echo the committed row id"
        with Session(fleet.engine) as s:
            row = s.get(InstanceOpsAudit, row_id)
        assert row is not None
        assert row.outcome == "committed"
        assert row.action == "instance_mark"
        assert row.target == "target-1"

    async def test_f10_ii_park_echoes_the_row_id(self, fleet):
        import json as _json

        fleet.rows["target-1"] = FakeRow("target-1", agent_id="worker")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_mark").ainvoke(
            {"id": "target-1", "marker": "park", "reason": "r", "dry_run": False}
        )
        payload = _json.loads(out)
        row_id = payload["audit_row_id"]
        assert row_id
        with Session(fleet.engine) as s:
            row = s.get(InstanceOpsAudit, row_id)
        assert row is not None
        assert row.outcome == "committed"

    async def test_f10_iii_suspect_echoes_the_row_id(self, fleet):
        import json as _json

        fleet.rows["target-1"] = FakeRow("target-1", agent_id="worker")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_mark").ainvoke(
            {"id": "target-1", "marker": "suspect", "reason": "r", "dry_run": False}
        )
        payload = _json.loads(out)
        row_id = payload["audit_row_id"]
        assert row_id
        with Session(fleet.engine) as s:
            row = s.get(InstanceOpsAudit, row_id)
        assert row is not None
        assert row.action == "instance_mark"


class TestF9ReclaimCopy:
    """F9 — the reclaim refusal must not recommend a dispatch.

    PLANNED MUTATION (N-4): restore the old 'use instance_nudge'
    refusal copy → F9-i RED (the refusal must not suggest a nudge for a
    non-terminal evidence-free target — a nudge DISPATCHES a
    context-free turn); revert either doc surface → F9-ii RED.
    """

    async def test_f9_i_copy_names_the_real_paths(self, fleet):
        fleet.rows["idle-1"] = FakeRow("idle-1", agent_id="worker")
        tools = _act_tools(fleet)
        out = await _by_name(tools, "instance_reclaim").ainvoke(
            {"id": "idle-1", "message": "wake up", "reason": "r"}
        )
        assert "reason=target-not-terminal" in out
        # The DANGEROUS recommendation is gone.
        assert "instance_nudge" not in out
        # The real paths are named.
        assert "instance_terminate" in out
        assert "instance_health" in out
        assert "instance_get" in out
        assert "instance_mark(marker='park')" in out
        # The hazard clause.
        assert "DISPATCHES a context-free turn" in out
        # The D-20 provenance.
        assert "TERMINAL-ONLY" in out
        assert "D-20" in out

    def test_f9_ii_doc_mirrors_carry_the_warning(self):
        tools = io.create_instance_ops_tools(
            FakeManager({"x": FakeRow("x")}),
            current_instance_id="maint-iid",
            agent_id="maintenancer",
        )
        reclaim_doc = _by_name(tools, "instance_reclaim")._full_doc_
        nudge_doc = _by_name(tools, "instance_nudge")._full_doc_
        assert "TERMINAL-ONLY" in reclaim_doc or "terminal-only" in reclaim_doc
        assert "not a diagnostic" in reclaim_doc or "NOT a diagnostic" in reclaim_doc
        assert "context-free" in nudge_doc
        assert "instance_health" in nudge_doc
        assert "instance_terminate" in nudge_doc
