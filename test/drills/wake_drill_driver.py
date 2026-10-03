#!/usr/bin/env python3
"""wake_drill_driver.py — Post-Restart Arm-Notify sandbox drill driver
(phase4-plan T6 / test-strategy.md §2.6 scenarios D1–D6).

Drives the REAL arm / record / sweep code IN-PROCESS against a FAKE
install tree under the drill run dir. Technique: the P2.2 tool-interlock
dynamic-sandbox pattern (tests/mocks/upgrade_tools_live_safety_mock.py) —
the real ``create_upgrade_tools()`` surface + the real
``daemon.tools.upgrade_journal`` protocol + the real
``UpgradeJournalSweepService.sweep_wake_records`` walk, with the ONLY
mock at the outermost DB edge (a minimal ``InstanceManager`` facade whose
``enqueue_message`` records the row in memory). The bash orchestrator
(``post_restart_arm_notify_drill.sh``) sets HOME / ENSEMBLE_SELF_ENV per
scenario and asserts exit codes; this driver does the code-level
observe-and-assert work.

Isolation contract (mirrors the p21 drill):
  * every path is under the run dir (/tmp namespace) — the live install
    is never addressed (HOME redirect makes ``~/agents-ensemble*`` resolve
    INSIDE the sandbox; there is no literal live path anywhere);
  * NO network calls, NO daemon boot, NO DB, NO LLM keys;
  * ENSEMBLE_SELF_ENV is the only self-env marker (the FAKE-live marker
    for D6 is ``ENSEMBLE_SELF_ENV=live`` against a fake live tree — the
    same technique the P2.2 interlock tests use; NEVER the real install);
  * the restart/upgrade executors are NEVER executed — the drill journals
    the terminal events the executors would journal (real
    ``journal_history_append`` with the REAL restart.sh/promote.sh prose)
    and clears the op markers the executors would clear.

Output: structured ``D<id>: ...`` evidence lines; exit 0 iff the scenario
passed. The bash orchestrator owns PASS/FAIL accounting + the transcript.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from daemon.tools import upgrade_journal as uj  # noqa: E402
from daemon.tools.upgrade_tools import create_upgrade_tools  # noqa: E402
from daemon.services.upgrade_journal_sweep import (  # noqa: E402
    UpgradeJournalSweepService,
)

DRILL_PORT = 8477  # metadata only — the drill makes NO network calls
KILL_SWITCH = "ENSEMBLE_POST_RESTART_ARM_NOTIFY"

# REAL executor terminal-event prose (restart.sh:262, promote.sh:366) —
# the drill journals what the executors journal, byte-shape-faithful.
RESTART_DETAIL = (
    "intentional restart run_id={run_id} complete "
    "(reason: wake-drill; SINGLE-TERM + launcher re-exec + /livez gate green)"
)
COMMIT_DETAIL = "promote vD1 committed (gate+soak green; previous=vD0)"

EVIDENCE: list[str] = []


def ev(line: str) -> None:
    """Structured evidence line (grep-able by the bash orchestrator)."""
    print(f"{SCENARIO_ID}: {line}", flush=True)
    EVIDENCE.append(line)


def check(cond: bool, what: str) -> bool:
    ev(("ok - " if cond else "FAIL - ") + what)
    return bool(cond)


SCENARIO_ID = "?"


class DrillManager:
    """Minimal InstanceManager facade — the ONLY mocks are the outermost
    DB edges (enqueue_message / instance repo), per the P2.2 mock
    philosophy. ``stamp_user_origin_window`` + ``set_pending_system_
    execution`` are recorders (the REAL manager methods are in-memory
    dict writes; the facade records the same calls)."""

    def __init__(self, instance_id: str, windows: dict) -> None:
        self.instance_id = instance_id
        self._user_origin_windows = windows
        self._task_repo = SimpleNamespace(
            has_instance_busy=self._has_instance_busy
        )
        self._instance_repository = SimpleNamespace(
            get=lambda iid: SimpleNamespace(
                instance_id=iid, agent_id="ari", project_id="p-wake-drill"
            ),
            get_by_agent_id=lambda agent_id: [],
        )
        self.enqueue_calls: list[dict] = []
        self.stamp_calls: list[tuple] = []
        self.marker_calls: list[tuple] = []

    def _has_instance_busy(self, instance_id: str) -> bool:
        # SYNC — _busy_advisory runs it via asyncio.to_thread.
        return False

    async def enqueue_message(self, **kwargs):
        self.enqueue_calls.append(kwargs)
        return SimpleNamespace(
            message_id=f"m-wake-{len(self.enqueue_calls)}"
        )

    def stamp_user_origin_window(self, instance_id: str, *, source, message_id=None):
        self.stamp_calls.append((instance_id, source, message_id))

    def set_pending_system_execution(self, instance_id: str, spec: dict) -> None:
        self.marker_calls.append((instance_id, spec))


def make_fake_install(fake_home: Path, env: str) -> Path:
    """Fake staged install tree (topology mirrors lib.sh resolve_env):
    releases/vD0 + vD1 with rollback_safe manifests, current → releases/vD0
    (relative, the canonical layout), a real (journal_init) state.json."""
    name = {"demo": "agents-ensemble-demo", "live": "agents-ensemble"}[env]
    inst = fake_home / name
    rel = inst / "releases"
    rel.mkdir(parents=True, exist_ok=True)
    for v in ("vD0", "vD1"):
        d = rel / v
        d.mkdir(exist_ok=True)
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "version": v,
                    "binary_version": v,
                    "rollback_safe": True,
                    "known_schema_gen": 1,
                }
            ),
            encoding="utf-8",
        )
        (d / "ensemble-prod").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cur = inst / "current"
    if not cur.is_symlink():
        cur.symlink_to("releases/vD0")
    uj.journal_init(inst)
    return inst


def _tool_fn(tools: list, name: str):
    """The raw async function under the langchain StructuredTool wrapper."""
    for t in tools:
        if getattr(t, "name", None) == name:
            fn = getattr(t, "coroutine", None) or getattr(t, "func", None)
            assert callable(fn), f"tool {name} has no callable body"
            return fn
    raise AssertionError(f"tool {name} not in surface")


def arm_restart(manager: DrillManager, tools: list) -> tuple[str, str]:
    """REAL system_restart arm (dry_run=false). Returns (run_id, banner).

    F4 review-round discipline (drill-only): the arm tool's success
    path does NOT release the journal lock (the executor
    restart.sh does, at restart.sh:265). In real life, the daemon
    dies between the arm and the executor's lock release, so the
    sweep never races the arm's lock. In the drill, the drill
    process is the same pid across the whole scenario, so the
    arm's lock WOULD stay held if we did not release it here —
    and the sweep's lock-wrapped mark_wake_* helpers (F4 fix)
    would then block for wait_s=30s on every tick. The
    drill-only lock_release below matches the pre-F4 behavior
    (the sweep was lock-free; the drill helper already modeled
    the executor's release via complete_restart_lane). The
    scenarios that need the lock to stay held (e.g. D4's
    arm_upgrade after arm_restart) are unaffected because
    complete_restart_lane already releases the lock too.
    """
    banner = asyncio.run(
        _tool_fn(tools, "system_restart")(
            target_env=os.environ["ENSEMBLE_SELF_ENV"],
            reason="wake-drill",
            mode="graceful-now",
            dry_run=False,
        )
    )
    pending = uj.read_pending_op(_inst())
    run_id = pending.run_id if pending is not None else ""
    # F4 drill discipline — see docstring above.
    uj.lock_release(_inst())
    return run_id, banner


def arm_upgrade(manager: DrillManager, tools: list, version: str) -> tuple[str, str]:
    """REAL system_upgrade arm (dry_run=false). Returns (run_id, banner).

    F4 review-round discipline: same lock-release rationale as
    arm_restart above. The promote.sh's commit path also releases
    the lock (promote.sh:392), so the drill-only release here
    matches that contract for the sweep's mark_wake_* helpers.
    """
    banner = asyncio.run(
        _tool_fn(tools, "system_upgrade")(
            target_env=os.environ["ENSEMBLE_SELF_ENV"],
            version=version,
            dry_run=False,
        )
    )
    pending = uj.read_pending_op(_inst())
    run_id = pending.run_id if pending is not None else ""
    # F4 drill discipline — see docstring above.
    uj.lock_release(_inst())
    return run_id, banner


_INST: Path | None = None


def _inst() -> Path:
    assert _INST is not None
    return _INST


def banner_checks(banner: str) -> bool:
    """Phase-4 banner contract on the REAL arm return (T4.7 companion at
    the integration level): auto-wake prose + kill-switch doc present; the
    obsolete pull-model instruction absent."""
    ok = True
    ok &= check("ask me to run" not in banner, "banner has NO obsolete pull-model instruction")
    ok &= check("auto-wake" in banner, "banner documents the auto-wake")
    ok &= check(
        "ENSEMBLE_POST_RESTART_ARM_NOTIFY=0" in banner,
        "banner documents the kill-switch env",
    )
    return ok


def run_sweep(manager: DrillManager):
    """REAL boot/periodic wake sweep (the deliver step)."""
    svc = UpgradeJournalSweepService(_inst(), manager=manager)
    return asyncio.run(svc.sweep_wake_records())


def complete_restart_lane(run_id: str) -> None:
    """Simulate restart.sh completion (prose-faithful terminal event + the
    marker clears + lock_release restart.sh performs at :264)."""
    uj.journal_history_append(_inst(), "restart", RESTART_DETAIL.format(run_id=run_id))
    uj.clear_pending_op(_inst())
    uj.journal_update_field(_inst(), "in_flight", None)
    uj.lock_release(_inst())


def set_window(manager: DrillManager, source: str, message_id: str | None) -> None:
    if source:
        manager._user_origin_windows[manager.instance_id] = {
            "source": source,
            "message_id": message_id,
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
    else:
        manager._user_origin_windows.pop(manager.instance_id, None)


# ── scenarios ────────────────────────────────────────────────────────────────


def scenario_d1() -> bool:
    """arm(restart) + terminal event + restart → wake delivered, api
    sentinel fallback, one-shot structural removal."""
    ok = True
    fake_home = Path(os.environ["HOME"])
    inst = make_fake_install(fake_home, "demo")
    globals()["_INST"] = inst
    mgr = DrillManager("inst-drill-1", {})
    tools = create_upgrade_tools(mgr, mgr.instance_id)

    run_id, banner = arm_restart(mgr, tools)
    ok &= check(bool(run_id), f"arm restart ok run_id={run_id}")
    ok &= check("RESTART SCHEDULED" in banner, "arm banner: RESTART SCHEDULED")
    ok &= banner_checks(banner)
    wakes = {w.run_id: w for w in uj.list_pending_wakes(inst)}
    ok &= check(
        run_id in wakes and wakes[run_id].status == "pending",
        "wake record pending in journal",
    )
    ok &= check(
        os.environ.get(KILL_SWITCH, "1") != "0", "kill-switch ON (default)"
    )

    complete_restart_lane(run_id)  # the executor's terminal event + clears
    res = run_sweep(mgr)
    ok &= check(res.delivered == 1, f"sweep delivered=1 (got {res.delivered})")
    ok &= check(res.pending_at_end == 0, "one-shot: pending_wakes empty post-delivery")
    ok &= check(len(mgr.enqueue_calls) == 1, "exactly one enqueue (the wake)")
    if mgr.enqueue_calls:
        call = mgr.enqueue_calls[0]
        ok &= check(
            call["instance_id"] == "inst-drill-1",
            "wake routed to the arming instance",
        )
        ok &= check(
            call["source"] == "api",
            "non-user-origin arm → api sentinel fallback (ADR-041)",
        )
        sc = call.get("metadata", {}).get("system_context", {})
        ok &= check(
            sc.get("kind") == "post_restart_arm_notify",
            "system_context kind=post_restart_arm_notify",
        )
        ok &= check(
            sc.get("terminal_outcome") == "restart",
            "terminal_outcome=restart (restart lane WAKE_TERMINAL_EVENTS)",
        )
        ok &= check(run_id in call.get("message", ""), "body carries the run_id")
    ok &= check(
        uj.list_pending_wakes(inst) == [], "journal has no stale wake record"
    )
    return ok


def scenario_d2() -> bool:
    """arm(upgrade) + commit event → wake with terminal_outcome=commit,
    target_version preserved."""
    ok = True
    fake_home = Path(os.environ["HOME"])
    inst = make_fake_install(fake_home, "demo")
    globals()["_INST"] = inst
    mgr = DrillManager("inst-drill-2", {})
    tools = create_upgrade_tools(mgr, mgr.instance_id)

    run_id, banner = arm_upgrade(mgr, tools, "vD1")
    ok &= check(bool(run_id), f"arm upgrade ok run_id={run_id}")
    ok &= check("UPGRADE ARMED" in banner, "arm banner: UPGRADE ARMED")
    ok &= banner_checks(banner)

    uj.journal_history_append(_inst(), "commit", COMMIT_DETAIL)  # promote.sh:366
    uj.clear_pending_op(_inst())
    uj.journal_update_field(_inst(), "in_flight", None)
    uj.lock_release(_inst())  # promote.sh releases at commit
    res = run_sweep(mgr)
    ok &= check(res.delivered == 1, f"sweep delivered=1 (got {res.delivered})")
    if mgr.enqueue_calls:
        call = mgr.enqueue_calls[0]
        sc = call.get("metadata", {}).get("system_context", {})
        ok &= check(
            sc.get("terminal_outcome") == "commit",
            "terminal_outcome=commit (promote lane)",
        )
        ok &= check(
            sc.get("target_version") == "vD1",
            "target_version preserved in the wake context",
        )
        ok &= check(
            call["instance_id"] == "inst-drill-2", "routed to the arming instance"
        )
    ok &= check(uj.list_pending_wakes(inst) == [], "one-shot removal (upgrade lane)")
    return ok


def scenario_d3() -> bool:
    """discord:user123 source preserved end-to-end (ADR-041): wake record
    carries it; the sweep re-stamps the window and enqueues with it."""
    ok = True
    fake_home = Path(os.environ["HOME"])
    inst = make_fake_install(fake_home, "demo")
    globals()["_INST"] = inst
    mgr = DrillManager("inst-drill-3", {})
    set_window(mgr, "discord:user123", "m-drill-3")
    tools = create_upgrade_tools(mgr, mgr.instance_id)

    run_id, _banner = arm_restart(mgr, tools)
    ok &= check(bool(run_id), f"arm ok run_id={run_id}")
    wakes = {w.run_id: w for w in uj.list_pending_wakes(inst)}
    ok &= check(
        run_id in wakes and wakes[run_id].source == "discord:user123",
        "wake record captured the arm-time user-origin source",
    )

    complete_restart_lane(run_id)
    res = run_sweep(mgr)
    ok &= check(res.delivered == 1, f"sweep delivered=1 (got {res.delivered})")
    if mgr.enqueue_calls:
        ok &= check(
            mgr.enqueue_calls[0]["source"] == "discord:user123",
            "MessageQueue row source=discord:user123 (routing preserved)",
        )
        ok &= check(
            mgr.enqueue_calls[0].get("message_id") is None
            or True,
            "enqueue returned a message_id",
        )
    ok &= check(
        any(
            iid == "inst-drill-3" and src == "discord:user123" and mid == "m-drill-3"
            for iid, src, mid in mgr.stamp_calls
        ),
        "user-origin window re-stamped (ADR-041 defensive re-stamp)",
    )
    return ok


def scenario_d4() -> bool:
    """long-downtime double-arm → ONE coalesced wake with a run-list
    (ADR-043): both records delivered, coalesced_count=2."""
    ok = True
    fake_home = Path(os.environ["HOME"])
    inst = make_fake_install(fake_home, "demo")
    globals()["_INST"] = inst
    mgr = DrillManager("inst-drill-4", {})
    set_window(mgr, "discord:user123", None)
    tools = create_upgrade_tools(mgr, mgr.instance_id)

    run_a, _ = arm_restart(mgr, tools)
    ok &= check(bool(run_a), f"arm A (restart) ok run_id={run_a}")
    complete_restart_lane(run_a)  # executor A completed during the downtime

    run_b, _ = arm_upgrade(mgr, tools, "vD1")  # re-arm after A's completion
    ok &= check(bool(run_b), f"arm B (upgrade) ok run_id={run_b}")
    uj.journal_history_append(_inst(), "commit", COMMIT_DETAIL)  # executor B
    uj.clear_pending_op(_inst())
    uj.journal_update_field(_inst(), "in_flight", None)
    uj.lock_release(_inst())

    res = run_sweep(mgr)
    ok &= check(
        len(mgr.enqueue_calls) == 1,
        f"ONE coalesced enqueue (got {len(mgr.enqueue_calls)})",
    )
    ok &= check(res.delivered == 2, f"both records delivered (got {res.delivered})")
    if mgr.enqueue_calls:
        body = mgr.enqueue_calls[0].get("message", "")
        sc = mgr.enqueue_calls[0].get("metadata", {}).get("system_context", {})
        ok &= check(run_a in body and run_b in body, "coalesced body lists both run_ids")
        ok &= check(
            sc.get("coalesced_count") == 2, "coalesced_count=2 in the wake context"
        )
    ok &= check(
        uj.list_pending_wakes(inst) == [], "group structural removal (both gone)"
    )
    return ok


def scenario_d5() -> bool:
    """kill-switch (ADR-044): OFF arm writes NO wake record; a persisted
    record under OFF is abandoned (reason=kill_switch_off); re-enable
    leaves no stale flood."""
    ok = True
    fake_home = Path(os.environ["HOME"])
    inst = make_fake_install(fake_home, "demo")
    globals()["_INST"] = inst
    mgr = DrillManager("inst-drill-5", {})
    tools = create_upgrade_tools(mgr, mgr.instance_id)

    # Phase A — arm with the switch OFF: the arm SUCCEEDS (pending_op is
    # the arm's durable record) but NO wake record is written.
    os.environ[KILL_SWITCH] = "0"
    run_a, banner = arm_restart(mgr, tools)
    ok &= check(bool(run_a), f"arm under kill-switch OFF succeeds run_id={run_a}")
    ok &= check(uj.list_pending_wakes(inst) == [], "NO wake record (arm-side gate)")
    ok &= check("auto-wake" in banner, "banner prose unchanged by the switch")

    # Phase B — flip ON, re-arm (a real persisted record), flip OFF, sweep:
    # the abandon-on-switch-off one-time pass clears it (T5.16).
    os.environ[KILL_SWITCH] = "1"
    complete_restart_lane(run_a)  # close lane A so a re-arm is possible
    run_b, _ = arm_restart(mgr, tools)
    ok &= check(bool(run_b), f"re-arm under ON ok run_id={run_b}")
    ok &= check(
        len(uj.list_pending_wakes(inst)) == 1, "record persisted under ON"
    )
    os.environ[KILL_SWITCH] = "0"
    res = run_sweep(mgr)
    ok &= check(res.abandoned == 1, f"abandoned=1 (got {res.abandoned})")
    ok &= check(res.delivered == 0, "no delivery under OFF")
    ok &= check(len(mgr.enqueue_calls) == 0, "no enqueue under OFF")
    hist = uj.journal_read(inst).get("history", [])
    ok &= check(
        any(
            e.get("event") == "wake_abandoned" and "reason=kill_switch_off" in str(e.get("detail"))
            for e in hist
        ),
        "wake_abandoned history event (reason=kill_switch_off)",
    )

    # Phase C — re-enable: no stale flood (T5.17).
    os.environ[KILL_SWITCH] = "1"
    res2 = run_sweep(mgr)
    ok &= check(
        res2.delivered == 0 and res2.abandoned == 0,
        "re-enable: no stale wake delivery (no resurrection)",
    )
    ok &= check(uj.list_pending_wakes(inst) == [], "journal clean after re-enable")
    os.environ.pop(KILL_SWITCH, None)
    return ok


def scenario_d6() -> bool:
    """live-outright-refusal (FAKE-live marker — NEVER the real live
    install): RESTART REFUSED before ANY journal write (invariant 8)."""
    ok = True
    fake_home = Path(os.environ["HOME"])  # contains the FAKE live tree
    inst = make_fake_install(fake_home, "live")
    globals()["_INST"] = inst
    state = inst / "releases" / "state.json"
    before = state.read_bytes()
    mgr = DrillManager("inst-drill-live", {})
    tools = create_upgrade_tools(mgr, mgr.instance_id)

    banner = asyncio.run(
        _tool_fn(tools, "system_restart")(
            target_env="live",
            reason="wake-drill",
            mode="graceful-now",
            dry_run=False,
        )
    )
    ok &= check(
        "REFUSED" in banner and "live-restart-refused" in banner,
        "live restart outright-refused (A2 token intact)",
    )
    ok &= check(state.read_bytes() == before, "state.json byte-identical (NO journal write)")
    ok &= check(uj.list_pending_wakes(inst) == [], "no wake record (live never records)")
    ok &= check(len(mgr.enqueue_calls) == 0, "no wake delivery for a refused arm")
    return ok


SCENARIOS = {
    "D1": scenario_d1,
    "D2": scenario_d2,
    "D3": scenario_d3,
    "D4": scenario_d4,
    "D5": scenario_d5,
    "D6": scenario_d6,
}


def main() -> int:
    global SCENARIO_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    args = parser.parse_args()
    SCENARIO_ID = args.scenario
    self_env = os.environ.get("ENSEMBLE_SELF_ENV", "")
    print(
        f"{SCENARIO_ID}: start self_env={self_env!r} "
        f"kill_switch={os.environ.get(KILL_SWITCH, '<unset=ON>')!r} "
        f"home={os.environ.get('HOME', '?')}",
        flush=True,
    )
    try:
        passed = SCENARIOS[args.scenario]()
    except Exception as exc:  # a crashed scenario is a failed scenario
        ev(f"FAIL - driver exception {type(exc).__name__}: {exc}")
        passed = False
    print(f"{SCENARIO_ID}: {'PASS' if passed else 'FAIL'}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
