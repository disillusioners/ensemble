#!/usr/bin/env python3
"""Leg F — engine mutation matrix (Engine Arc Phase 1, P7).

THE ARTIFACT behind the Phase-7 mutation claim.  Enumerates every leg in
code: plant the defect -> the discriminating pin must go RED -> restore ->
the same pin must go GREEN (file sha verified identical to the original).
Nothing here is narrated — the per-leg line + final RESULT is the evidence.

Legs
----
A.  The committed C-1 recipe (``test/packs/engine_c1_mutation_recipe.sh``),
    invoked in its documented disposable-clone mode (the recipe mutates the
    tree IN PLACE, so it runs inside a ``git archive`` extract).
B.  The P7-proven in-tree predicate mutations (B1..B10), reproduced
    exactly as verified 2026-11-22.  B2 is a CONTROL (sibling-edit) leg
    that asserts the pin stays GREEN.
C.  M-leg mapping (test-strategy.md §2.2, M-01a..M-14c) — enumerated in
    ``M_LEG_MAPPING`` below, mapped to where each is proven today (this
    harness's B-legs, the committed C-1 recipe, or phase-gate evidence).
    Legs covered only by phase-gate evidence are listed as
    COVERED-ELSEWHERE with the pointer — never faked as harness legs.

Mechanics
---------
Per leg: fresh disposable extract (one ``git archive`` of HEAD, then
``cp -a`` per leg); plant the mutation; run the pin via the extract with
the worktree venv; assert the EXPECTED red node appears (rc==1, not just
nonzero); discard the extract; GREEN baseline per pin ONCE at HEAD.

Exit 0 only when EVERY selected leg is RED-with-defect AND GREEN-after-
restore.  A leg GREEN with the defect planted is reported NON-DISCRIMINATING
and fails the run — a pin that cannot fail is worse than no pin.

Env fence: per-variable scrub (``env -u POSTGRES_*`` is a silent no-op),
ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned explicitly, dev DB only.

Wall-clock guard: internal 280s cap (the wrapper's outer 300 wraps it).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: The tree THIS harness lives in — i.e. the tree under test.  Derived from
#: the harness's own location, NEVER from a fixed lane path: the base
#: extract (``git archive HEAD`` of this tree) and the GREEN baseline
#: (``cwd=WT``) then bind to the checkout the pack actually runs in, so a
#: clean-checkout ceremony leg exercises the pinned sha instead of the
#: lane worktree (POK ceremony-clean-checkout-03-wt-binding).
_HERE = Path(__file__).resolve()
WT = str(
    next(
        p
        for p in _HERE.parents
        if (p / "pyproject.toml").is_file() and (p / "daemon").is_dir()
    )
)
PY = os.path.join(WT, ".venv", "bin", "python")

SCRUB_VARS = [
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "DATABASE_URL",
    "DATABASE_URL_POSTGRES",
    "ENSEMBLE_DB_DSN",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "OPENAI_API_KEY",
]

INTERNAL_TIMEOUT = 280


# ---------------------------------------------------------------------------
# M-leg mapping (test-strategy.md §2.2) — 25 rows (14 families)
# ---------------------------------------------------------------------------
M_LEG_MAPPING: list[tuple[str, str, str]] = [
    # (leg id, family summary, where proven today)
    ("M-01a", "terminal transition stamps last_run_at+last_outcome (success path)", "phase-gate P2/P4 evidence (COVERED-ELSEWHERE)"),
    ("M-01b", "terminal transition stamps (skip/timeout path)", "phase-gate P2/P4 evidence (COVERED-ELSEWHERE)"),
    ("M-02", "stamp-on-skip advances last_run_at (stranded-due trap)", "phase-gate P3 evidence (COVERED-ELSEWHERE)"),
    ("M-03a", "due boundary: exactly-at-interval IS due", "phase-gate P3 evidence (COVERED-ELSEWHERE)"),
    ("M-03b", "due boundary: just-below is NOT due", "phase-gate P3 evidence (COVERED-ELSEWHERE)"),
    ("M-04", "single-flight overlap -> SKIPPED_OVERLAP, never queued", "C-1 recipe (engine_c1_mutation_recipe.sh)"),
    ("M-05a", "backoff progression on consecutive idle-skips", "phase-gate P3 evidence (COVERED-ELSEWHERE)"),
    ("M-05b", "backoff reset on success", "phase-gate P3 evidence (COVERED-ELSEWHERE)"),
    ("M-06a", "timeout_policy=cancel -> TIMED_OUT + cancelled", "phase-gate P4 evidence (COVERED-ELSEWHERE)"),
    ("M-06b", "timeout_policy=extend -> recorded, task finishes", "phase-gate P4 evidence (COVERED-ELSEWHERE)"),
    ("M-07", "retry exhaustion -> FAILED_BACKOFF terminal", "phase-gate P4 evidence (COVERED-ELSEWHERE)"),
    ("M-08", "getter-at-first-tick (no ctor capture) -> born-dead", "B9/B10-adjacent phase-gate evidence (COVERED-ELSEWHERE)"),
    ("M-09", "boot wiring assertion fails LOUD", "phase-gate P5 evidence (COVERED-ELSEWHERE)"),
    ("M-10a", "dead=true false-negative (never set)", "phase-gate evidence (COVERED-ELSEWHERE)"),
    ("M-10b", "dead=true false-positive (set at 1x interval)", "phase-gate evidence (COVERED-ELSEWHERE)"),
    ("M-11a", "per-job kill_switch_key OFF -> no execution", "phase-gate P6 evidence (COVERED-ELSEWHERE)"),
    ("M-11b", "ENSEMBLE_ENGINE_ENABLED OFF -> nothing executes", "phase-gate P6 evidence (COVERED-ELSEWHERE)"),
    ("M-11c", "FAIL-TO-LEGACY: unreadable switch -> legacy path", "phase-gate P6 evidence (COVERED-ELSEWHERE)"),
    ("M-12a", "SERVICES_*_USE_ENGINE OFF -> engine unregistered + legacy runs", "phase-gate P6 evidence (COVERED-ELSEWHERE)"),
    ("M-12b", "SERVICES_*_USE_ENGINE ON -> engine runs, legacy suppressed", "phase-gate P6 evidence (COVERED-ELSEWHERE)"),
    ("M-13a", "per-kind firing counts increment on the right kind", "B1/B3/B4 (this harness)"),
    ("M-13b", "metrics labels = service_tag+category", "phase-gate P5 evidence (COVERED-ELSEWHERE)"),
    ("M-14a", "spec missing service_tag REJECTED at registration", "phase-gate P1 evidence (COVERED-ELSEWHERE)"),
    ("M-14b", "spec missing category REJECTED at registration", "phase-gate P1 evidence (COVERED-ELSEWHERE)"),
    ("M-14c", "max_runtime=0 / two-trigger spec REJECTED", "phase-gate P1 evidence (COVERED-ELSEWHERE)"),
]


# ---------------------------------------------------------------------------
# B legs
# ---------------------------------------------------------------------------
@__import__("dataclasses").dataclass
class BLeg:
    id: str
    path: str
    old: str
    new: str
    node: str
    control: bool = False  # True => expect GREEN with the plant (no-false-RED)
    note: str = ""
    #: ADDITIONAL (old, new) plant sites in the SAME file (multi-site
    #: mutations — each must occur exactly once, else the leg FAILs).
    extra: tuple = ()


BLEGS: list[BLeg] = [
    BLeg(
        id="B1",
        path="daemon/services/event_bus.py",
        old='        # Notify SSE listeners\n        self.notify(instance_id)\n',
        new='        # Notify SSE listeners\n        x = 1\n        self.notify(instance_id)\n',
        node="tests/unit/services/event_registry/test_bridges.py::test_create_event_function_source_is_pinned_gap15",
        note="create_event body edit -> GAP-15 pin RED",
    ),
    BLeg(
        id="B2",
        path="daemon/services/event_bus.py",
        old='        """Create a generic error lifecycle event."""\n        await self.create_event(\n',
        new='        """Create a generic error lifecycle event."""\n        # sibling control plant\n        await self.create_event(\n',
        node="tests/unit/services/event_registry/test_bridges.py::test_create_event_function_source_is_pinned_gap15",
        control=True,
        note="SIBLING CONTROL: edit create_error_event -> GAP-15 pin stays GREEN",
    ),
    BLeg(
        id="B3",
        path="daemon/services/room_delivery.py",
        old='''                _event_registry_record("event_bus:room_tag_delivery",
                                       instance_id=parent_iid,
                                       room_id=room_id, seq=seq,
                                       outcome="parked_paused")''',
        new="",
        node="tests/unit/services/event_registry/test_bridges.py::test_multi_site_kinds_carry_the_measured_per_site_counts",
        note="delete one room_tag_delivery site (line ~907) -> per-site pin RED",
    ),
    BLeg(
        id="B4",
        path="daemon/services/room_delivery.py",
        old="from daemon.services.event_registry import record as _event_registry_record",
        new="from daemon.services.event_registry import record as _rec",
        node="tests/unit/services/event_registry/test_bridges.py::test_record_is_never_imported_under_a_non_canonical_alias",
        note="alias swap -> non-canonical alias pin RED",
    ),
    BLeg(
        id="B5",
        path="daemon/services/event_registry/catalog.py",
        old="from daemon.repositories.event.models import EventKind\n",
        new="from daemon.repositories.event.models import EventKind\nfrom daemon.services import engine\n",
        node="tests/unit/services/event_registry/test_bridges.py::test_registry_package_has_no_engine_import",
        note="D5.11 plant: engine import in catalog -> no-engine-import pin RED",
    ),
    BLeg(
        id="B6",
        path="daemon/services/event_registry/metrics.py",
        # B6 RE-TARGETED at 6373ce830 (the real D5.4 second live source
        # landed): the ORIGINAL plant string (`live_source == "event_bus"
        # or live_source == "dependency_watchers"`) no longer exists — the
        # dispatch is now a LIVE_SOURCES registry + a per-consumer lane
        # resolver.  The SAME defect class (a dispatch branch with NO real
        # extractor, i.e. the "D5.4 lie") is planted in the new shape:
        #   site 1 — the honesty short-circuit (a kind whose declared
        #            live_source is NOT implemented is unverifiable) is
        #            weakened so a kind WITH consumers is still dispatched;
        #   site 2 — the "dependency_bus" source is answered from the
        #            EVENT-BUS subscriber set instead of the dependency
        #            bus's own lane extractor.
        # Result: the probe kind (declared live_source "dependency_watchers",
        # consumer "dependency_bus:child_keyed_waiters") gets a REAL verdict
        # (verified=True / live=False) instead of None ⇒ the pin's Leg-1
        # "D5.4 lie" assertion REDs — the SAME assertion the original B6
        # reddened.
        old='''    if declared_source is not None and declared_source not in LIVE_SOURCES:
        return (
            [{"consumer": c, "live": None} for c in spec.consumers],
            False,
            False,
        )''',
        new='''    if declared_source is not None and declared_source not in LIVE_SOURCES and not spec.consumers:
        return (
            [{"consumer": c, "live": None} for c in spec.consumers],
            False,
            False,
        )''',
        extra=(
            (
                '''        if source == "dependency_bus":
            live_ids = extractor(dependency_bus)''',
                '''        if source == "dependency_bus":
            live_ids = _live_subscriber_ids(bus)''',
            ),
        ),
        node="tests/unit/services/event_registry/test_metrics.py::test_live_source_dispatch_covers_exactly_the_event_bus_source",
        note=(
            "D5.4 fake dispatch, RE-TARGETED to the landed dispatch shape: a "
            "non-bus source answered from the BUS's subscriber set (a "
            "dispatch branch without a real extractor) -> live-source pin "
            "RED (Leg-1 'D5.4 lie')"
        ),
    ),
    BLeg(
        id="B7",
        path="daemon/services/engine/engine.py",
        old='''            # F2 re-arm: refresh spec from the registry.
            if self.registry is not None:
                try:
                    new_spec = self.registry.cached_spec(job_id)
                    if new_spec is not None:
                        cache["spec"] = new_spec
                except Exception:  # noqa: BLE001
                    pass
            # F2 re-arm: refresh persisted_dead from the DB row.
            try:
                row = self.repository.get_job(job_id)
                if row is not None:
                    cache["persisted_dead"] = bool(getattr(row, "dead", 0))
            except Exception:  # noqa: BLE001
                pass''',
        new="",
        node="tests/unit/services/test_engine_supervisor_reregistration.py::test_f2_cancel_refreshes_spec_and_persisted_dead",
        note="F2 refresh removal -> F2 pin RED",
    ),
    BLeg(
        id="B8",
        path="daemon/services/engine/engine.py",
        old='            cache["pending_event"] = False\n',
        new="",
        node="tests/unit/services/test_engine_supervisor_reregistration.py::test_f3_cancel_clears_pending_event_and_registry_owns_no_latch",
        note="F3 latch removal -> F3 pin RED",
    ),
    BLeg(
        id="B9",
        path="daemon/repositories/engine/repository.py",
        old='            existing.dead = 0\n            session.add(existing)',
        new='            existing.dead = 0\n            existing.version = existing.version + 1\n            session.add(existing)',
        node="tests/unit/services/test_engine_jobspec_registration.py::TestRegistrationContract::test_f4_echo_revival_of_dead_job",
        note="F4 revive bump -> echo-revival pin RED",
    ),
    BLeg(
        id="B10",
        path="daemon/services/engine/watchdog.py",
        old="if getattr(job, \"dead\", 0) and not getattr(job, \"last_outcome\", None):",
        new="if getattr(job, \"dead\", 0):",
        node="tests/unit/services/engine/test_watchdog_drill.py::TestDirectDbRearmPredicate::test_f1_escalated_row_is_not_rearmed",
        note="S-2 conjunct removal -> escalated-not-rearmed pin RED",
    ),
]


def _sha_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _run_pytest(cwd: str, node: str, env_extra: dict | None = None) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB_VARS}
    env["ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"] = "0"
    env["ENSEMBLE_SELF_ENV"] = "dev"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["POSTGRES_HOST"] = "127.0.0.1"
    env["POSTGRES_PORT"] = "5432"
    env["POSTGRES_DB"] = "ensemble_dev"
    env["PYTHONPATH"] = cwd
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [PY, "-m", "pytest", node, "-q", "--tb=line", "-p", "no:randomly", "--timeout=30"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode, out


def _run_pytest_nodes(cwd: str, nodes: list[str]) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB_VARS}
    env["ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"] = "0"
    env["ENSEMBLE_SELF_ENV"] = "dev"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["POSTGRES_HOST"] = "127.0.0.1"
    env["POSTGRES_PORT"] = "5432"
    env["POSTGRES_DB"] = "ensemble_dev"
    env["PYTHONPATH"] = cwd
    proc = subprocess.run(
        [PY, "-m", "pytest", *nodes, "-q", "--tb=line", "-p", "no:randomly", "--timeout=30"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode, out


def _make_base_extract() -> str:
    d = tempfile.mkdtemp(prefix="engmm-base-")
    p = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=WT, stdout=subprocess.PIPE, check=True,
    )
    subprocess.run(["tar", "-x", "-C", d], input=p.stdout, check=True)
    return d


def _copy_extract(base: str) -> str:
    d = tempfile.mkdtemp(prefix="engmm-leg-")
    # remove the empty dir created by mkdtemp to allow cp -a
    os.rmdir(d)
    shutil.copytree(base, d, symlinks=True)
    return d


def run_leg_a(base: str) -> tuple[str, bool, float]:
    t0 = time.time()
    extract = _copy_extract(base)
    # symlink venv
    venv_link = os.path.join(extract, ".venv")
    if not os.path.exists(venv_link):
        os.symlink(os.path.join(WT, ".venv"), venv_link)
    os.makedirs(os.path.join(extract, "test", "packs"), exist_ok=True)
    shutil.copy2(os.path.join(WT, "test", "packs", "engine_c1_mutation_recipe.sh"),
                 os.path.join(extract, "test", "packs", "engine_c1_mutation_recipe.sh"))
    # run recipe
    proc = subprocess.run(
        ["bash", "test/packs/engine_c1_mutation_recipe.sh"],
        cwd=extract,
        capture_output=True,
        text=True,
        timeout=300,
    )
    dt = time.time() - t0
    shutil.rmtree(extract, ignore_errors=True)
    ok = (proc.returncode == 0)
    verdict = "RED-confirmed" if ok else "FAIL"
    # Leg A is a recipe that internally does red+green; treat success as RED-confirmed
    return verdict, ok, dt


def run_b_leg(leg: BLeg, base: str) -> tuple[str, bool, float, dict]:
    t0 = time.time()
    extract = _copy_extract(base)
    target = os.path.join(extract, leg.path)
    original = open(target, encoding="utf-8").read()
    before_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()

    info = {"red_rc": None, "green_rc": None, "sha_ok": False}

    # plant
    if leg.old not in original:
        shutil.rmtree(extract, ignore_errors=True)
        return "FAIL", False, time.time() - t0, info
    planted = original.replace(leg.old, leg.new, 1)
    # additional (multi-site) plant sites — each must occur exactly once
    for extra_old, extra_new in leg.extra:
        if planted.count(extra_old) != 1:
            shutil.rmtree(extract, ignore_errors=True)
            return "FAIL", False, time.time() - t0, info
        planted = planted.replace(extra_old, extra_new, 1)
    open(target, "w", encoding="utf-8").write(planted)

    # run pin (expect RED)
    red_rc, red_out = _run_pytest(extract, leg.node)
    info["red_rc"] = red_rc
    red_ok = (red_rc == 1) and ("FAILED" in red_out or leg.node.split("::")[-1] in red_out)

    # restore
    open(target, "w", encoding="utf-8").write(original)
    after_sha = _sha_file(target)
    info["sha_ok"] = (after_sha == before_sha)

    # run pin (expect GREEN)
    green_rc, green_out = _run_pytest(extract, leg.node)
    info["green_rc"] = green_rc
    green_ok = (green_rc == 0)

    shutil.rmtree(extract, ignore_errors=True)
    dt = time.time() - t0

    if leg.control:
        # expect GREEN with the plant; we already ran red leg expecting red,
        # but control means the plant should NOT red. Re-evaluate: for control,
        # success = plant run was green. We ran red_rc expecting 1 but got?
        # Adjust: control leg is GREEN-with-sibling-edit. Our "red_rc" above
        # is actually the plant run; for control it should be 0, not 1.
        plant_green = (red_rc == 0)
        restored_green = (green_rc == 0)
        ok = plant_green and restored_green and info["sha_ok"]
        verdict = "CONTROL-green" if ok else "FAIL"
        return verdict, ok, dt, info

    ok = red_ok and green_ok and info["sha_ok"]
    verdict = "RED-confirmed" if ok else "FAIL"
    return verdict, ok, dt, info


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legs", default="", help="comma-separated leg ids (A,B1,...)")
    args = parser.parse_args(argv)

    start = time.time()
    base = _make_base_extract()

    # Green baseline per unique pin ONCE at HEAD
    unique_nodes = []
    for leg in BLEGS:
        if leg.node not in unique_nodes:
            unique_nodes.append(leg.node)
    # Leg A has no single node; skip baseline for it.
    baseline_ok = True
    print("=== GREEN BASELINE (HEAD) ===")
    rc, out = _run_pytest_nodes(WT, unique_nodes)
    if rc == 0:
        for node in unique_nodes:
            print(f"BASELINE {node.split('::')[-1][:40]:42s} GREEN-confirmed (rc=0)")
    else:
        # identify which
        for node in unique_nodes:
            r2, _ = _run_pytest(WT, node)
            status = "GREEN-confirmed" if r2 == 0 else "FAIL"
            print(f"BASELINE {node.split('::')[-1][:40]:42s} {status} (rc={r2})")
            if r2 != 0:
                baseline_ok = False
        baseline_ok = False

    # Select legs
    all_legs = ["A"] + [b.id for b in BLEGS]
    selected_ids = [x.strip() for x in args.legs.split(",") if x.strip()] if args.legs else all_legs

    failures = 0
    lines = []
    leg_times = {}

    # Leg A
    if "A" in selected_ids:
        if time.time() - start > INTERNAL_TIMEOUT:
            print("RESULT: TIMEOUT")
            shutil.rmtree(base, ignore_errors=True)
            return 124
        verdict, ok, dt = run_leg_a(base)
        leg_times["A"] = dt
        print(f"LEG A: {verdict} ({dt:.1f}s)")
        lines.append(f"A\t{verdict}\t{ok}")
        if not ok:
            failures += 1

    # B legs
    for bleg in BLEGS:
        if bleg.id not in selected_ids:
            continue
        if time.time() - start > INTERNAL_TIMEOUT:
            print("RESULT: TIMEOUT")
            shutil.rmtree(base, ignore_errors=True)
            return 124
        verdict, ok, dt, info = run_b_leg(bleg, base)
        leg_times[bleg.id] = dt
        print(f"LEG {bleg.id}: {verdict} ({dt:.1f}s) red_rc={info['red_rc']} green_rc={info['green_rc']} sha_ok={info['sha_ok']}")
        lines.append(f"{bleg.id}\t{verdict}\t{ok}")
        if not ok:
            failures += 1

    shutil.rmtree(base, ignore_errors=True)

    print("\n=== M-LEG MAPPING (test-strategy.md §2.2) ===")
    print(f"enumerated {len(M_LEG_MAPPING)} rows (14 families)")
    covered_harness = sum(1 for _, _, w in M_LEG_MAPPING if "this harness" in w or "C-1 recipe" in w)
    covered_elsewhere = len(M_LEG_MAPPING) - covered_harness
    print(f"  covered by this harness / C-1 recipe: {covered_harness}")
    print(f"  covered-elsewhere (phase-gate evidence): {covered_elsewhere}")

    print("\n=== LEG WALL TIMES ===")
    for lid, dt in leg_times.items():
        print(f"  {lid}: {dt:.1f}s")
    print(f"  total: {time.time()-start:.1f}s")

    if not baseline_ok:
        failures += 1
        print("BASELINE: NOT ALL GREEN")

    if failures == 0:
        print("\nRESULT: PASS")
        return 0
    print(f"\nRESULT: FAIL ({failures} failing)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
