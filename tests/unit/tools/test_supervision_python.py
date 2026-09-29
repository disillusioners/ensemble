#!/usr/bin/env python3
"""Pin tests for the supervision PYTHON twin (daemon/tools/upgrade_journal.py
§supervision + the sweep-service boot advisory) — supervision-detection P5.

Run with:
    python3 tests/unit/tools/test_supervision_python.py

Self-contained: NO daemon dependencies (the exemplar importlib+stub pattern
from tests/unit/tools/test_promote_cgroup_survivorship_python.py).

Coverage (mission matrix items 8, 9 + the python side of 12):
  8. _supervision_detect_fn injection seam + compute-once-per-process memo
     (reset via _supervision_reset_memo) — the seam is honored, the memo
     collapses N calls into ONE detector invocation, the reset re-arms it.
  9. Boot supervision event — the sweep-service startup hook emits the
     advisory `supervision_boot` journal event ONCE at start() (never on
     the periodic tick), install_dir=None emits nothing, a missing/torn
     journal never raises, and the advisory detail carries the A1 outcome.
  12py. supervision_outcome — the full declared×verified cell table (19
     cells) with the twins-pinned reason vocabulary (byte-identical with
     the shell twin — cross-checked cell-for-cell by the SHELL-side twins
     suite tests/test_supervision_twins.sh).
  +  _supervision_classify_leaf pure table + _supervision_read_cgroup_leaf
     against the REAL own-pid /proc read (never-raises contract included).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

PASSED = 0


def ok(msg: str) -> None:
    global PASSED
    PASSED += 1
    print(f"PASS: {msg}")


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(f"PIN FAILED: {msg}")
    ok(msg)


def _load(name: str, path: Path) -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def load_twins() -> tuple[types.ModuleType, types.ModuleType]:
    """(uj, sweep) loaded in isolation with a stubbed daemon.constants."""
    m = types.ModuleType("daemon")
    m.__path__ = ["daemon"]
    constants = types.ModuleType("daemon.constants")
    constants.is_reserved_source = lambda x: False  # type: ignore[attr-defined]
    tools = types.ModuleType("daemon.tools")
    tools.__path__ = ["daemon/tools"]
    services = types.ModuleType("daemon.services")
    services.__path__ = ["daemon/services"]
    sys.modules["daemon"] = m
    sys.modules["daemon.constants"] = constants
    sys.modules["daemon.tools"] = tools
    sys.modules["daemon.services"] = services
    uj = _load("uj_sup_test", REPO_ROOT / "daemon" / "tools" / "upgrade_journal.py")
    sys.modules["daemon.tools.upgrade_journal"] = uj
    sweep = _load(
        "sweep_sup_test",
        REPO_ROOT / "daemon" / "services" / "upgrade_journal_sweep.py",
    )
    return uj, sweep


def main() -> None:
    uj, sweep = load_twins()

    # ── 8. seam + memo ────────────────────────────────────────────────────
    uj._supervision_reset_memo()
    calls = []

    def stub_det(env: dict[str, str] | None = None) -> object:
        calls.append(env)
        return uj.SupervisionDetection("UNIT_MANAGED", unit="stub.service", mode="unit")

    uj._supervision_detect_fn = stub_det
    try:
        d1 = uj.supervision_detect()
        d2 = uj.supervision_detect()
        d3 = uj.supervision_detect({"ENSEMBLE_SUPERVISION": "script"})
        expect(len(calls) == 1, f"8a memo: 3 calls → exactly ONE detector invocation (got {len(calls)})")
        expect(d1.state == "UNIT_MANAGED" and d1.unit == "stub.service",
               "8a memo returns the FIRST detection (env override ignored after first call)")
        expect(d3 is d1 or d3.state == "UNIT_MANAGED",
               "8a memo: the frozen dataclass shape round-trips")

        uj._supervision_reset_memo()
        d4 = uj.supervision_detect({"ENSEMBLE_SUPERVISION": "script"})
        expect(len(calls) == 2, "8b _supervision_reset_memo re-arms the detector (second invocation)")
        expect(d4.state == "UNIT_MANAGED",
               "8b after reset the SAME stub result flows (memo cleared, not the stub)")
    finally:
        uj._supervision_detect_fn = uj._supervision_detect_real
        uj._supervision_reset_memo()
    ok("8c seam restore + memo reset hygiene (finally block)")

    # ── 8d. real detector ladder via the env seam (deterministic cells) ──
    uj._supervision_reset_memo()
    try:
        det = uj._supervision_detect_real({"ENSEMBLE_SUPERVISION": "script"})
        expect(det.state == "SCRIPT_NOHUP" and det.mode == "script",
               "8d-i explicit script → SCRIPT_NOHUP/mode=script")
        for falsey in ("0", "false", "no", "off", "FALSE", "Off"):
            det = uj._supervision_detect_real({"ENSEMBLE_SUPERVISION": falsey})
            expect(det.state == "SCRIPT_NOHUP" and not det.note,
                   f"8d-ii opt-out '{falsey}' → silent SCRIPT_NOHUP")
        det = uj._supervision_detect_real({"ENSEMBLE_SUPERVISION": "banana"})
        expect(det.state == "SCRIPT_NOHUP" and "garbage" in det.note,
               "8d-iii garbage → SCRIPT_NOHUP + WARN-note (fail-toward-script)")
        det = uj._supervision_detect_real({
            "ENSEMBLE_SUPERVISION": "unit",
            "ENSEMBLE_RESTART_UNIT": "ensemble-py.service",
        })
        expect(det.state == "UNIT_MANAGED" and det.unit == "ensemble-py.service" and det.mode == "unit",
               "8d-iv explicit unit + env name → UNIT_MANAGED:name/mode=unit")
        det = uj._supervision_detect_real({"ENSEMBLE_SUPERVISION": "unit"})
        expect(det.state == "UNIT_MANAGED" and det.mode == "unit" and (
            (det.unit == "" and "no unit name resolvable" in det.note
             and "exit-78" in det.note)
            or det.unit.endswith(".service")),
            "8d-v explicit unit unresolved → carried note (shell preflight owns the 78)")
        # auto on a live-systemd host: self-consistent with the leaf helpers
        if sys.platform == "linux" and Path("/run/systemd/system").exists():
            leaf = uj._supervision_read_cgroup_leaf(os.getpid())
            expect(leaf is not None, "8d-vi own /proc cgroup readable on this systemd host")
            state, unit = uj._supervision_classify_leaf(leaf)
            det = uj._supervision_detect_real({"ENSEMBLE_SUPERVISION": "auto"})
            expect(det.state == state,
                   f"8d-vi auto agrees with the leaf table for THIS host's real leaf '{leaf}' → {state}")
    finally:
        uj._supervision_reset_memo()

    # INVOCATION_ID corroboration: present + non-unit leaf → note (§0)
    uj._supervision_reset_memo()
    if sys.platform == "linux" and Path("/run/systemd/system").exists():
        leaf = uj._supervision_read_cgroup_leaf(os.getpid())
        if leaf and not leaf.endswith(".service"):
            det = uj._supervision_detect_real({
                "ENSEMBLE_SUPERVISION": "auto",
                "INVOCATION_ID": "deadbeef" * 4,
            })
            expect(det.state != "UNIT_MANAGED" and "trusting cgroup" in det.note,
                   "8e §0: INVOCATION_ID present + non-unit leaf → cgroup trusted, WARN-note")

    # ── leaf helpers ───────────────────────────────────────────────────────
    state, unit = uj._supervision_classify_leaf("ensemble-live.service")
    expect(state == "UNIT_MANAGED" and unit == "ensemble-live.service",
           "8f leaf table: .service → UNIT_MANAGED + name")
    state, unit = uj._supervision_classify_leaf("ensemble-upgrade-r-x.scope")
    expect(state == "SCOPE_SURVIVOR" and unit == "",
           "8f leaf table: ensemble-upgrade-*.scope → SCOPE_SURVIVOR")
    for leaf in ("session-9.scope", "init.scope", "user.slice", "user-1000.slice",
                 "machine.slice", "foo-machine.slice", "weird-unknown"):
        state, unit = uj._supervision_classify_leaf(leaf)
        expect(state == "SCRIPT_NOHUP" and unit == "",
               f"8f leaf table: '{leaf}' → SCRIPT_NOHUP (conservative default)")
    expect(uj._supervision_read_cgroup_leaf(999999999) is None,
           "8g _supervision_read_cgroup_leaf: nonexistent pid → None (never raises)")
    expect(uj._supervision_read_cgroup_leaf(-1) is None,
           "8g _supervision_read_cgroup_leaf: invalid pid → None (never raises)")
    myleaf = uj._supervision_read_cgroup_leaf(os.getpid())
    if myleaf is not None:
        expect("/" not in myleaf and ":" not in myleaf,
               f"8h leaf is the BASENAME (last path segment): '{myleaf}'")

    # ── 12py. supervision_outcome — the 19-cell table ─────────────────────
    cells = [
        # (declared, verified, unit, expected_outcome)
        ("script", "SCRIPT_NOHUP", "", "conforming"),        # 1
        ("script", "UNIT_MANAGED", "", "degraded"),          # 2 (unreachable today, named)
        ("script", "SCOPE_SURVIVOR", "", "degraded"),        # 3
        ("unit", "UNIT_MANAGED", "u.service", "conforming"), # 4
        ("unit", "UNIT_MANAGED", "", "fault"),               # 5 exit-78 arm
        ("unit", "SCRIPT_NOHUP", "", "fault"),               # 6 (unreachable today)
        ("unit", "SCOPE_SURVIVOR", "", "fault"),             # 7 (unreachable today)
        ("auto", "SCRIPT_NOHUP", "", "conforming"),          # 8
        ("auto", "UNIT_MANAGED", "x.service", "conforming"), # 9 adoption signal
        ("auto", "SCOPE_SURVIVOR", "", "degraded"),          # 10
        ("script", "DUAL_FIGHT", "", "fault"),               # 11
        ("unit", "DUAL_FIGHT", "y.service", "fault"),        # 12 (unit ignored on fault)
        ("auto", "DUAL_FIGHT", "", "fault"),                 # 13
        ("", "SCRIPT_NOHUP", "", "fault"),                   # 14 unknown declared
        ("script", "GARBAGE_STATE", "", "fault"),            # 15 unknown verified
        ("unit", "GARBAGE_STATE", "", "fault"),              # 16
        ("auto", "GARBAGE_STATE", "", "fault"),              # 17
        ("SCRIPT", "script_nohup_lower", "", "fault"),       # 18 case: declared folds; unknown verified fails closed
        ("Unit", "UNIT_MANAGED", "z.service", "conforming"), # 19 case-insensitive declared
    ]
    for declared, verified, unit, want in cells:
        got = uj.supervision_outcome(declared, verified, unit)
        expect(got.outcome == want,
               f"12 cell {declared!r}×{verified!r}×{unit!r} → {want} (got {got.outcome})")
        expect("|" not in got.reason and got.reason != "",
               f"12 cell reason is non-empty and '|' free: {declared}×{verified}")
        expect(got.outcome in ("conforming", "degraded", "fault"),
               "12 outcome vocabulary closed set")
    # spot-check the twins-pinned reason strings (byte-identical with shell)
    o = uj.supervision_outcome("unit", "UNIT_MANAGED", "")
    expect("preflight refuses (exit 78), never silent-degrade" in o.reason,
           "12 twins-pinned reason: exit-78 arm wording")
    o = uj.supervision_outcome("auto", "UNIT_MANAGED", "")
    expect(o.outcome == "conforming", "12 auto×UNIT_MANAGED (no name) = conforming (name only consulted for declared=unit)")
    o = uj.supervision_outcome("any", "DUAL_FIGHT", "")
    expect(o.outcome == "fault" and "two masters" in o.reason,
           "12 DUAL_FIGHT vocabulary in reason")

    # ── 9. boot advisory via the sweep service ────────────────────────────
    with tempfile.TemporaryDirectory(prefix="sup-py-boot.") as td:
        inst = Path(td)
        (inst / "releases").mkdir()
        (inst / "releases" / "state.json").write_text(json.dumps({
            "current": "v1", "previous": None, "in_flight": None,
            "rollback_window_count": {"24h": 0, "window_start": None},
            "cooldown_until": None, "quarantined": [], "history": [],
        }))

        # deterministic detection via the seam (memo consulted by the hook)
        uj._supervision_detect_fn = lambda env=None: uj.SupervisionDetection(
            "UNIT_MANAGED", unit="ensemble-pyboot.service", mode="unit")
        uj._supervision_reset_memo()
        try:
            svc = sweep.UpgradeJournalSweepService(inst)
            svc._emit_supervision_boot_advisory()
            hist = json.loads((inst / "releases" / "state.json").read_text())["history"]
            expect(len(hist) == 1 and hist[0]["event"] == "supervision_boot",
                   "9a one supervision_boot journal event at boot")
            detail = hist[0]["detail"]
            expect("state=UNIT_MANAGED" in detail and "unit=ensemble-pyboot.service" in detail,
                   f"9a detail carries state+unit: '{detail}'")
            expect("outcome=conforming" in detail,
                   "9a detail carries the A1 outcome (unit×UNIT_MANAGED → conforming)")

            # second emit (a second boot) → a SECOND event (per-boot, not once-ever)
            svc._emit_supervision_boot_advisory()
            hist = json.loads((inst / "releases" / "state.json").read_text())["history"]
            expect(len(hist) == 2,
                   "9b one advisory PER BOOT (second start → second event)")

            # degraded shape note rides along
            uj._supervision_detect_fn = lambda env=None: uj.SupervisionDetection(
                "SCOPE_SURVIVOR", unit="", mode="script", note="scope survivor")
            uj._supervision_reset_memo()
            svc._emit_supervision_boot_advisory()
            hist = json.loads((inst / "releases" / "state.json").read_text())["history"]
            detail = hist[-1]["detail"]
            expect("outcome=degraded" in detail and 'note="scope survivor"' in detail,
                   f"9c degraded detail + note: '{detail}'")
        finally:
            uj._supervision_detect_fn = uj._supervision_detect_real
            uj._supervision_reset_memo()

        # install_dir=None → NO journal write, no raise
        svc_none = sweep.UpgradeJournalSweepService(None)
        svc_none._emit_supervision_boot_advisory()
        ok("9d install_dir=None (dev checkout) → silent no-op")

        # missing journal (never-promoted install) → JournalTorn debug path, no raise
        with tempfile.TemporaryDirectory(prefix="sup-py-nojournal.") as td2:
            inst2 = Path(td2)
            (inst2 / "releases").mkdir()   # NO state.json
            uj._supervision_detect_fn = lambda env=None: uj.SupervisionDetection(
                "SCRIPT_NOHUP")
            uj._supervision_reset_memo()
            try:
                sweep.UpgradeJournalSweepService(inst2)._emit_supervision_boot_advisory()
                ok("9e absent journal → advisory degrades silently (never gates boot)")
            finally:
                uj._supervision_detect_fn = uj._supervision_detect_real
                uj._supervision_reset_memo()

    # 9f. BOOT-TIME-ONLY: the emit hook is wired in start(), never in the
    # periodic tick (structural pin — the sweep loop must not re-emit).
    src = (REPO_ROOT / "daemon" / "services" / "upgrade_journal_sweep.py").read_text()
    emit_calls = src.count("self._emit_supervision_boot_advisory()")
    expect(emit_calls == 1,
           f"9f exactly ONE call site (in start()); got {emit_calls}")
    start_seg = src[src.index("def start("):src.index("def _emit_supervision_boot_advisory(")]
    expect("_emit_supervision_boot_advisory()" in start_seg,
           "9f the emit call lives INSIDE start() (boot-only)")
    run_seg = src[src.index("async def _run("):src.index("def stop(")]
    expect("_emit_supervision_boot_advisory" not in run_seg,
           "9f the PERIODIC tick (_run loop) never emits the advisory (no periodic sweep)")

    print(f"\n=== ALL PYTHON SUPERVISION PINS PASSED ({PASSED}) ===")


if __name__ == "__main__":
    main()
