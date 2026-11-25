"""The D3 reaper-classifier table (self-service-promote Wave 5, §2.4).

THE SUBJECT
-----------
The executor is a ``systemd-run --wait --collect`` transient SERVICE (a
system.slice sibling that survives the daemon unit's teardown), while the
``--wait`` CLIENT is a cgroup child of the ensemble unit that dies WITH
it. Historically the reaper journaled the CLIENT's waitpid code as
``executor_exit`` — a daemon stop mid-ceremony produced a FALSE
``executor_exit (128+SIGTERM)`` for a LIVE ceremony (design §1.5).

Wave 5's fix: the reaper classifies via the UNIT
(``daemon.tools.upgrade_journal.classify_executor_exit``):

  unit ActiveState ∈ {active, activating, …}  ⇒ ``executor_client_exit``
      (client died, ceremony ALIVE — op stays pending, reaper re-arms);
  unit inactive|failed (LOADED)               ⇒ ``executor_exit`` carrying
      the unit's ExecMainStatus (the PAYLOAD's status, never 143);
  unit gone (--collect GC'd / not-found)      ⇒ the journal's own truth:
      heartbeat fresh ⇒ ALIVE; stale + terminal row ⇒ DEAD (the row says
      how); stale + no row ⇒ AMBIGUOUS ⇒ the halt-for-human token
      ``executor_liveness_unverifiable`` (arm NOT closed, lock NOT
      released — bounded by the unit's RuntimeMaxSec);
  probe failure                               ⇒ outcome-UNOBSERVED — NEVER
      a death verdict.

THE INVARIANT (§2.4): ``executor_exit`` may only be emitted from (a) a
payload-observed terminal state or (b) the legacy no-unit path. A client
death alone can NEVER produce it.

PINS (the AC table):
  AC-D3-1  client exit while the unit is active ⇒ executor_client_exit,
           NO executor_exit, op retained.
  AC-D3-2  unit failed ⇒ executor_exit carries the UNIT's ExecMainStatus,
           not the client's 143.
  AC-D3-3  unit gone + heartbeat fresh ⇒ ALIVE; stale + terminal row ⇒
           DEAD; stale + no row ⇒ executor_liveness_unverifiable with the
           arm OPEN and the lock HELD.
  AC-D3-4  probe failure ⇒ never a death verdict.
  (+)      the new event kinds are NON-TERMINAL (never in _TERMINAL_EVENTS
           nor the derived terminal tuples — an executor_client_exit row
           must not close a pending_op via reconcile_pending_op);
  (+)      reconcile_pending_op's bail-on-live-in_flight invariant
           (§9 correction #3 — BINDING) still holds after a
           client_exit row.

Hermetic: the classifier's systemctl probe is INJECTED (no real systemd
contact); journals are scratch tmp dirs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.tools import upgrade_journal as uj


def _probe(props: dict[str, str] | None, err: str = ""):
    """Build an injected probe_fn: ``props=None`` ⇒ probe failure."""
    def _fn(unit: str, bus: str):
        return props, err
    return _fn


def _seed(tmp_path: Path, *, in_flight: dict | None = None,
          heartbeat: int | None = None, terminal: str | None = None,
          armed_at: str | None = None) -> Path:
    install = tmp_path / "install"
    (install / "releases").mkdir(parents=True)
    uj.journal_init(install)
    uj.ensure_extensions(install)
    uj.write_pending_op(
        install,
        uj.PendingOp(
            run_id="r-d3-classify-1", kind="promote", env="demo",
            target="9.9.9", armed_by_instance="inst-d3",
        ),
    )
    data = uj.journal_read(install)
    if heartbeat is not None:
        inf = dict(in_flight or {})
        inf["last_heartbeat"] = heartbeat
        in_flight = inf
    if in_flight is not None:
        data["in_flight"] = in_flight
    if terminal is not None:
        data.setdefault("history", []).append(
            {"event": terminal, "ts": uj.now_iso(), "detail": "fixture row"}
        )
    uj.journal_write(install, data)
    return install


UNIT = "ensemble-upgrade-r-d3-classify-1"
NOW_EPOCH = None  # filled per-test for heartbeat fixtures


class TestClassifierTable:
    """AC-D3-1..4 — the §2.4 classifier table, cell by cell."""

    def test_ac_d3_1_active_unit_means_client_exit_alive(self, tmp_path):
        install = _seed(tmp_path, in_flight={"kind": "promote"})
        cls = uj.classify_executor_exit(
            install, "r-d3-classify-1", UNIT, "system",
            probe_fn=_probe({
                "LoadState": "loaded", "ActiveState": "active",
                "SubState": "running", "Result": "success",
                "ExecMainStatus": "0",
            }),
        )
        assert cls.kind == "client_exit_alive", cls
        assert "ALIVE" in cls.note or "alive" in cls.note

    def test_activating_is_alive_too(self, tmp_path):
        install = _seed(tmp_path, in_flight={"kind": "promote"})
        cls = uj.classify_executor_exit(
            install, "r-d3-classify-1", UNIT, "system",
            probe_fn=_probe({
                "LoadState": "loaded", "ActiveState": "activating",
                "SubState": "start", "Result": "success",
                "ExecMainStatus": "0",
            }),
        )
        assert cls.kind == "client_exit_alive", cls

    def test_ac_d3_2_failed_unit_carries_exec_main_status_not_143(
        self, tmp_path
    ):
        install = _seed(tmp_path, in_flight={"kind": "promote"})
        cls = uj.classify_executor_exit(
            install, "r-d3-classify-1", UNIT, "system",
            probe_fn=_probe({
                "LoadState": "loaded", "ActiveState": "failed",
                "SubState": "failed", "Result": "failure",
                "ExecMainStatus": "78",
            }),
        )
        assert cls.kind == "unit_terminal", cls
        assert cls.exit_code == 78
        assert cls.exit_code != 143

    def test_inactive_loaded_unit_is_terminal(self, tmp_path):
        install = _seed(tmp_path, in_flight={"kind": "promote"})
        cls = uj.classify_executor_exit(
            install, "r-d3-classify-1", UNIT, "system",
            probe_fn=_probe({
                "LoadState": "loaded", "ActiveState": "inactive",
                "SubState": "dead", "Result": "success",
                "ExecMainStatus": "0",
            }),
        )
        assert cls.kind == "unit_terminal", cls

    def test_ac_d3_3_unit_gone_heartbeat_fresh_means_alive(self, tmp_path):
        import time
        install = _seed(
            tmp_path, in_flight={"kind": "promote"},
            heartbeat=int(time.time()) - 5,  # fresh (≤ OWNER_HEARTBEAT_FRESH_S)
        )
        cls = uj.classify_executor_exit(
            install, "r-d3-classify-1", UNIT, "system",
            probe_fn=_probe({
                "LoadState": "not-found", "ActiveState": "inactive",
                "SubState": "dead", "Result": "", "ExecMainStatus": "",
            }),
        )
        assert cls.kind == "client_exit_alive", cls
        assert "heartbeat" in cls.note

    def test_ac_d3_3_unit_gone_stale_plus_terminal_row_means_dead(
        self, tmp_path
    ):
        install = _seed(
            tmp_path, in_flight=None, heartbeat=None, terminal="commit",
        )
        cls = uj.classify_executor_exit(
            install, "r-d3-classify-1", UNIT, "system",
            probe_fn=_probe({
                "LoadState": "not-found", "ActiveState": "inactive",
                "SubState": "dead", "Result": "", "ExecMainStatus": "",
            }),
        )
        assert cls.kind == "dead_terminal_row", cls
        assert cls.terminal_event == "commit"

    def test_ac_d3_3_unit_gone_stale_no_row_is_ambiguous(self, tmp_path):
        install = _seed(tmp_path, in_flight=None)
        cls = uj.classify_executor_exit(
            install, "r-d3-classify-1", UNIT, "system",
            probe_fn=_probe({
                "LoadState": "not-found", "ActiveState": "inactive",
                "SubState": "dead", "Result": "", "ExecMainStatus": "",
            }),
        )
        assert cls.kind == "ambiguous", cls
        assert "unverifiable" in cls.note

    def test_ac_d3_4_probe_failure_is_never_death(self, tmp_path):
        install = _seed(tmp_path, in_flight={"kind": "promote"})
        for err in ("probe timeout after 5.0s", "systemctl show rc=1: boom",
                    "OSError: no systemctl"):
            cls = uj.classify_executor_exit(
                install, "r-d3-classify-1", UNIT, "system",
                probe_fn=_probe(None, err),
            )
            assert cls.kind == "unobserved", (err, cls)

    def test_legacy_no_unit_name_is_the_legacy_path(self, tmp_path):
        install = _seed(tmp_path)
        cls = uj.classify_executor_exit(
            install, "r-d3-classify-1", "", "",
            probe_fn=_probe(None, "must not be called"),
        )
        assert cls.kind == "legacy_no_unit", cls


class TestEventKindHygiene:
    """The new kinds are NON-TERMINAL (they close nothing)."""

    def test_new_kinds_not_in_any_terminal_tuple(self):
        from daemon.tools import upgrade_tools as ut
        for kind in (
            uj.EXECUTOR_CLIENT_EXIT_EVENT,
            uj.EXECUTOR_LIVENESS_UNVERIFIABLE_EVENT,
        ):
            assert kind not in uj._TERMINAL_EVENTS
            assert kind not in uj.RESTART_TERMINAL_EVENTS
            assert kind not in uj.WAKE_TERMINAL_EVENTS
            assert kind not in ut._TERMINAL_OUTCOME_EVENTS

    def test_shared_terminal_set_unchanged(self):
        assert tuple(uj._TERMINAL_EVENTS) == (
            "commit", "rollback", "halt", "sweep_rollback", "sweep",
            "quarantine", "refusal", "executor_exit",
            "boot_sweep_commit_and_continue", "serving_green_commit",
        )


class TestReconcileBailInvariant:
    """§9 correction #3 (BINDING): reconcile_pending_op bails on a LIVE
    in_flight — a client_exit row must NOT close the arm while the txn is
    open."""

    def test_reconcile_still_bails_on_live_in_flight_after_client_exit(
        self, tmp_path
    ):
        install = _seed(tmp_path, in_flight={"kind": "promote"})
        uj.journal_history_append(
            install, uj.EXECUTOR_CLIENT_EXIT_EVENT,
            "run_id=r-d3-classify-1 client died, unit live (fixture)",
        )
        assert uj.reconcile_pending_op(install) is None
        op = uj.read_pending_op(install)
        assert op is not None and op.run_id == "r-d3-classify-1"


class TestWorkerClassification:
    """The reaper WORKER state machine (AC-D3-1/-3/-4 at the journal level):
    drives ``_classify_and_journal_exit`` directly with an injected probe
    and a shrunken poll cadence — no real systemd, no waitpid."""

    @staticmethod
    def _job(install: Path):
        from daemon.services.upgrade_journal_sweep import ReaperJob

        return ReaperJob(
            pid=999999, argv_summary=("promote.sh",),
            install_dir=install, run_id="r-d3-classify-1",
            unit_name=UNIT, unit_bus="system",
        )

    @staticmethod
    def _sweep(install: Path):
        from daemon.services.upgrade_journal_sweep import (
            UpgradeJournalSweepService,
        )

        return UpgradeJournalSweepService(
            install, reconcile_interval_seconds=86400,
            reaper_timeout_seconds=60, manager=None,
        )

    @staticmethod
    def _events(install: Path) -> list[str]:
        return [
            str(e.get("event"))
            for e in (uj.journal_read(install).get("history") or [])
        ]

    def test_client_exit_alive_journals_no_executor_exit(
        self, tmp_path, monkeypatch
    ):
        import asyncio
        install = _seed(tmp_path, in_flight={"kind": "promote"})
        monkeypatch.setenv("ENSEMBLE_EXECUTOR_UNIT_POLL_S", "1")
        monkeypatch.setenv("ENSEMBLE_EXECUTOR_UNIT_REARM_MAX_POLLS", "0")
        monkeypatch.setattr(
            uj, "executor_unit_state_probe",
            _probe({
                "LoadState": "loaded", "ActiveState": "active",
                "SubState": "running", "Result": "success",
                "ExecMainStatus": "0",
            }),
        )
        sweep = self._sweep(install)
        asyncio.run(sweep._classify_and_journal_exit(
            self._job(install), client_exit_code=143,
            signal_name="SIGTERM", signal_num=15,
        ))
        events = self._events(install)
        # AC-D3-1: executor_client_exit present, executor_exit ABSENT —
        # the client's 143 never becomes a death row.
        assert uj.EXECUTOR_CLIENT_EXIT_EVENT in events
        assert "executor_exit" not in events
        # cap-exhausted while alive ⇒ the benign-detach row (existing
        # vocabulary), NOT a death row
        assert "executor_still_running" in events
        # op retained
        op = uj.read_pending_op(install)
        assert op is not None and op.run_id == "r-d3-classify-1"

    def test_unit_terminal_journals_executor_exit_with_payload_status(
        self, tmp_path, monkeypatch
    ):
        import asyncio
        install = _seed(tmp_path, in_flight={"kind": "promote"})
        monkeypatch.setattr(
            uj, "executor_unit_state_probe",
            _probe({
                "LoadState": "loaded", "ActiveState": "failed",
                "SubState": "failed", "Result": "failure",
                "ExecMainStatus": "78",
            }),
        )
        sweep = self._sweep(install)
        asyncio.run(sweep._classify_and_journal_exit(
            self._job(install), client_exit_code=143,
            signal_name=None, signal_num=None,
        ))
        rows = [
            e for e in uj.journal_read(install).get("history") or []
            if e.get("event") == "executor_exit"
        ]
        assert rows, "executor_exit missing for a payload-observed terminal"
        assert "exit_code=78" in rows[-1]["detail"], rows[-1]["detail"]
        assert "exit_code=143" not in rows[-1]["detail"]
        assert "D3 unit classifier" in rows[-1]["detail"]

    def test_ambiguous_journals_halt_for_human_arm_open_lock_held(
        self, tmp_path, monkeypatch
    ):
        import asyncio
        install = _seed(tmp_path, in_flight=None)
        # hold the pipeline lock in this run's name — the AMBIGUOUS verdict
        # must NOT release it
        acquired, _busy = uj.lock_acquire(install, "r-d3-classify-1")
        assert acquired
        monkeypatch.setattr(
            uj, "executor_unit_state_probe",
            _probe({
                "LoadState": "not-found", "ActiveState": "inactive",
                "SubState": "dead", "Result": "", "ExecMainStatus": "",
            }),
        )
        sweep = self._sweep(install)
        asyncio.run(sweep._classify_and_journal_exit(
            self._job(install), client_exit_code=143,
            signal_name=None, signal_num=None,
        ))
        events = self._events(install)
        assert uj.EXECUTOR_LIVENESS_UNVERIFIABLE_EVENT in events
        assert "executor_exit" not in events
        # arm NOT closed, lock NOT released
        assert uj.read_pending_op(install) is not None
        assert uj.lock_dir(install).exists()

    def test_unobserved_at_cap_never_emits_death(
        self, tmp_path, monkeypatch
    ):
        import asyncio
        install = _seed(tmp_path, in_flight={"kind": "promote"})
        monkeypatch.setenv("ENSEMBLE_EXECUTOR_UNIT_POLL_S", "1")
        monkeypatch.setenv("ENSEMBLE_EXECUTOR_UNIT_REARM_MAX_POLLS", "0")
        monkeypatch.setattr(
            uj, "executor_unit_state_probe", _probe(None, "probe timeout"),
        )
        sweep = self._sweep(install)
        asyncio.run(sweep._classify_and_journal_exit(
            self._job(install), client_exit_code=143,
            signal_name=None, signal_num=None,
        ))
        events = self._events(install)
        assert uj.EXECUTOR_LIVENESS_UNVERIFIABLE_EVENT in events
        assert "executor_exit" not in events

    def test_legacy_no_unit_job_keeps_waitpid_code(
        self, tmp_path, monkeypatch
    ):
        import asyncio
        from daemon.services.upgrade_journal_sweep import ReaperJob

        install = _seed(tmp_path, in_flight={"kind": "promote"})
        monkeypatch.setattr(
            uj, "executor_unit_state_probe",
            _probe(None, "must not be called on the legacy path"),
        )
        sweep = self._sweep(install)
        job = ReaperJob(
            pid=999999, argv_summary=("promote.sh",),
            install_dir=install, run_id="r-d3-classify-1",
        )
        asyncio.run(sweep._classify_and_journal_exit(
            job, client_exit_code=143,
            signal_name="SIGTERM", signal_num=15,
        ))
        rows = [
            e for e in uj.journal_read(install).get("history") or []
            if e.get("event") == "executor_exit"
        ]
        assert rows, "legacy path must keep journaling executor_exit"
        assert "exit_code=143" in rows[-1]["detail"]
        assert "SIGTERM" in rows[-1]["detail"]
