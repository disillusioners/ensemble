#!/usr/bin/env python3
# ============================================================================
# scripts/upgrade/ledger_check.py — journal-derived N-clean-cycles ledger
# checker (P2.3 B2)
# ============================================================================
# READ-ONLY consumer of a releases/state.json journal (ADR-034: this checker
# never writes or rewrites the journal). Derives the per-cycle ledger and the
# N-clean-cycles gate verdict that feeds runbook §7's human-copy table
# (docs/runbooks/upgrade-drills.md — "the machine-checkable source of truth
# is the demo journal + the ledger checker; the human table is DERIVED from
# checker output — when they disagree, the journal wins").
#
# Stdlib only (no dependencies). Explicit inputs ONLY — no ambient-env
# sniffing: the journal path and the F2 forge-lane state are always supplied
# by the caller (gate design: F2 state comes from the caller's knowledge,
# never derived).
#
# Semantics (test-strategy.md §4 + ADR-021 + runbook §9):
#   * cycle     = one committed promote txn in journal history (terminal
#                 `commit` event). The journal writes no explicit txn id —
#                 the commit event's `ts` is the unique per-cycle handle.
#   * window    = from that cycle's commit event to the next cycle's commit
#                 event (history records no txn-open event; the commit IS
#                 the cycle start marker), or journal end for the last one.
#   * CLEAN     = window contains ZERO `rollback` / `sweep_rollback` /
#                 `halt` events; any such event ⇒ VIOLATION (cause kept).
#   * staleness = cycles count toward the consecutive run ONLY within the
#                 SAME release version; a version change resets the run and
#                 marks the older cycles SUPERSEDED (test-strategy §4.3).
#   * streak    = the trailing run of CLEAN cycles at the same version. A
#                 VIOLATION cycle BREAKS the run but does NOT erase history
#                 (older cycles stay listed) and does NOT zero the ledger —
#                 reset-to-zero is reserved for staleness. This is the
#                 conservative gate-safe reading of ADR-021's "failed cycles
#                 do NOT reset to zero automatically" (open reset-on-fix
#                 question — decisions.md ADR-021; caller may override).
#   * gate      = ELIGIBLE iff consecutive-clean ≥ 3 (ADR-021 N=3 user-
#                 ruled) AND --f2-state closed. F2-open ⇒ BLOCKED (reason
#                 F2-open) REGARDLESS of count — hard block, runbook §9.
#
# Coverage note (test-strategy §4.1): this checker covers the journal-
# checkable clauses. §4.1 clauses 2–5 (restart-cycle-clean, readiness
# log-scan, work-loss resume evidence, live-pid checkpoint) are EXTERNAL
# evidence audited in RESULTS files — the gate consumer folds both.
#
# Live-path refusal: refuses (exit 78) when the RESOLVED journal path is
# under the live install root (~/agents-ensemble), compared after
# expanduser+resolve so symlinks cannot smuggle a live path in, and
# (MINOR-2) case-folded on BOTH sides + samefile-resolved for existing
# paths, so a mixed-case spelling cannot dodge it on case-insensitive
# APFS. The live dir
# NAME is a substring of the demo dir name (~/agents-ensemble-demo) — name
# matching is never used, only the resolved literal install-root comparison,
# so the demo install and any checkout directory that merely shares the name
# are always accepted. Refusal fires on path shape BEFORE any read attempt.
# This file contains no port literals; where prose must refer to the live
# port it writes the words "live port" (runbook port-literal rule).
#
# Exit codes: 0 = checker ran (verdict is data — BLOCKED/NOT-READY also
# exit 0) · 1 = unreadable/invalid journal · 78 = live-path refusal ·
# 2 = CLI usage error (argparse default).
# ============================================================================
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

N_REQUIRED = 3  # ADR-021 (user-ruled 2026-08-22): N = 3 clean cycles
VIOLATION_EVENTS = ("rollback", "sweep_rollback", "halt")
LIVE_INSTALL_DIRNAME = "agents-ensemble"  # literal live install root: ~/agents-ensemble

COVERAGE_NOTE = (
    "coverage: journal-checkable clauses of test-strategy.md 4.1 only; "
    "clauses 2-5 (restart-cycle-clean, readiness log-scan, work-loss "
    "resume evidence, live-pid checkpoint) are external evidence audited "
    "in RESULTS files — the gate consumer folds both"
)


def _die_unreadable(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"ledger_check: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _refuse_live(path: Path) -> None:
    print(
        f"ledger_check: REFUSED: journal path '{path}' resolves under the "
        f"live install root — the live environment is out of bounds for this "
        f"checker (read-only refusal on path shape; no read attempted)",
        file=sys.stderr,
    )
    sys.exit(78)


def live_install_root() -> Path:
    """The literal live install root, resolved lazily (HOME override works).

    NIT-8 (P2.3 review cycle 1): ``Path.home()`` raises ``RuntimeError``
    when HOME is unset — degrade to a clean ``_die_unreadable``-style exit
    (message + exit 1), NEVER a traceback. The live-path refusal baseline
    is not derivable without a home; refusing to run is the fail-closed
    behavior."""
    try:
        home = Path.home()
    except RuntimeError as exc:
        _die_unreadable(
            f"cannot determine the live install root for the live-path "
            f"refusal: {exc} (HOME unset?) — refusing to run without the "
            f"refusal baseline"
        )
    return (home / LIVE_INSTALL_DIRNAME).resolve(strict=False)


def check_live_path(raw_journal: str) -> Path:
    """Resolve the journal path; refuse (exit 78) if under the live install
    root. Resolve+compare against the literal live install path — never a
    name-substring match (the demo dir name contains the live dir name).

    MINOR-2 (P2.3 review cycle 1 — case-fold hole): APFS is
    case-INSENSITIVE by default, and ``os.path.normcase`` is IDENTITY on
    POSIX — so the literal compare above alone would let a mixed-case
    spelling of the live root dodge the refusal. BOTH sides are therefore
    normcase'd (and explicitly case-folded — normcase alone is a no-op on
    POSIX) before the containment compare, and existing paths are
    ADDITIONALLY resolved via ``os.path.samefile`` (symlink/case-variant
    and Unicode-normalization spellings the string compares cannot see)."""
    try:
        resolved = Path(raw_journal).expanduser().resolve(strict=False)
    except RuntimeError as exc:
        # NIT-8: expanduser raises RuntimeError when a "~"-prefixed path
        # is given with HOME unset — same clean exit as live_install_root.
        _die_unreadable(
            f"cannot expand journal path '{raw_journal}': {exc} (HOME unset?)"
        )
    live = live_install_root()
    if resolved == live or live in resolved.parents:
        _refuse_live(resolved)
    # case-folded containment: fold BOTH sides (normcase + casefold —
    # normcase is identity on POSIX, and APFS is case-insensitive by
    # default), existence-agnostic: equal-or-under refuses.
    resolved_folded = os.path.normcase(str(resolved)).casefold()
    live_folded = os.path.normcase(str(live)).casefold()
    if resolved_folded == live_folded or resolved_folded.startswith(
        live_folded + os.sep
    ):
        _refuse_live(resolved)
    # samefile resolution for EXISTING paths (symlink / case-variant /
    # Unicode-normalization spellings): if any existing component of the
    # journal path is the same filesystem object as the live install
    # root, refuse. Absent components simply skip — the string compares
    # above already ran.
    if live.exists():
        probe: Optional[Path] = resolved
        while probe is not None and str(probe) != probe.anchor:
            try:
                if probe.exists() and os.path.samefile(probe, live):
                    _refuse_live(resolved)
            except OSError:
                pass  # vanished mid-walk / permission — string compares ran
            probe = probe.parent
    return resolved


def load_journal(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _die_unreadable(f"cannot read journal at {path}: {exc}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _die_unreadable(f"journal is not valid JSON (torn write?): {exc}")
    if not isinstance(data, dict):
        _die_unreadable("journal root is not a JSON object")
    return data


_COMMIT_VERSION_RE = re.compile(r"^promote\s+(\S+)\s+committed\b")


def event_version(event: dict) -> str:
    """Cycle version: explicit `version` field if a future writer adds one,
    else parsed from the commit detail ('promote <ver> committed (...)'),
    else 'unknown'."""
    explicit = event.get("version")
    if isinstance(explicit, str) and explicit:
        return explicit
    match = _COMMIT_VERSION_RE.match(str(event.get("detail", "")))
    return match.group(1) if match else "unknown"


def _ev(event: dict, key: str) -> str:
    value = event.get(key)
    return str(value) if value is not None else "?"


def derive_cycles(history: list) -> list:
    """Cycles from commit events, oldest → newest. Window = [this commit,
    next commit) — events between a cycle's commit and the next cycle's
    commit belong to that cycle's window (history is newest-last)."""
    commit_indices = [
        i for i, e in enumerate(history)
        if isinstance(e, dict) and e.get("event") == "commit"
    ]
    cycles = []
    for n, start in enumerate(commit_indices):
        end = commit_indices[n + 1] if n + 1 < len(commit_indices) else len(history)
        window = history[start:end]
        causes = [
            f"{_ev(e, 'event')}@{_ev(e, 'ts')}"
            for e in window
            if isinstance(e, dict) and e.get("event") in VIOLATION_EVENTS
        ]
        commit = history[start]
        cycles.append({
            "cycle": n + 1,
            "version": event_version(commit),
            "txn_id": _ev(commit, "ts"),
            "timestamp": _ev(commit, "ts"),
            "causes": causes,
        })
    return cycles


def classify(cycles: list) -> dict:
    """Per-cycle verdicts (CLEAN/VIOLATION/SUPERSEDED), the trailing
    consecutive-clean run, and staleness state. Walk newest → oldest:
    a version change ends counting and supersedes that cycle and every
    older one; within the current-version run, a VIOLATION ends counting
    but keeps history visible (no reset-to-zero)."""
    n = len(cycles)
    verdicts = ["CLEAN" if not c["causes"] else "VIOLATION" for c in cycles]
    current_version: Optional[str] = cycles[-1]["version"] if cycles else None
    run = 0
    counting = True
    staleness_hit = False
    superseded: list = []
    reset: Optional[dict] = None
    for i in range(n - 1, -1, -1):
        if staleness_hit:
            verdicts[i] = "SUPERSEDED"
            superseded.append(cycles[i]["cycle"])
            continue
        if cycles[i]["version"] != current_version:
            staleness_hit = True
            verdicts[i] = "SUPERSEDED"
            superseded.append(cycles[i]["cycle"])
            reset = {
                "reset": True,
                "entered_cycle": cycles[i]["cycle"] + 1,  # first cycle of the current version
                "from_version": cycles[i]["version"],
                "to_version": current_version,
            }
            continue
        if counting:
            if verdicts[i] == "CLEAN":
                run += 1
            else:
                counting = False
    superseded.reverse()
    if reset is None:
        reset = {"reset": False}
    return {
        "verdicts": verdicts,
        "consecutive_clean_count": run,
        "current_version": current_version,
        "staleness": reset,
        "superseded_cycles": superseded,
    }


def gate(count: int, f2_state: str, current_version: Optional[str]) -> dict:
    """Runbook §9: F2-open ⇒ BLOCKED regardless of count (hard block);
    F2 closed + count ≥ N(=3) ⇒ ELIGIBLE; F2 closed + count < N ⇒
    NOT-READY with how many more are needed."""
    if f2_state == "open":
        return {
            "verdict": "BLOCKED",
            "reasons": [
                "F2-open: the unauthenticated loopback API user-origin forge "
                "lane is open — gate hard-blocked regardless of cycle count "
                "(runbook §9)"
            ],
            "clean_needed": max(0, N_REQUIRED - count),
        }
    reasons = []
    if count >= N_REQUIRED:
        reasons.append(
            f"{count} consecutive clean cycles at version {current_version} "
            f"(≥ N={N_REQUIRED}, ADR-021)"
        )
        reasons.append("F2 closed (caller-supplied)")
        return {
            "verdict": "ELIGIBLE",
            "reasons": reasons,
            "clean_needed": 0,
        }
    needed = N_REQUIRED - count
    if count == 0:
        reasons.append(f"no clean cycle credited (consecutive-clean 0 < N={N_REQUIRED})")
    else:
        reasons.append(
            f"consecutive-clean {count} < N={N_REQUIRED} (ADR-021)"
        )
    reasons.append(f"{needed} more clean cycle(s) at version {current_version} needed")
    return {"verdict": "NOT-READY", "reasons": reasons, "clean_needed": needed}


def build_report(resolved: Path, f2_state: str, data: dict) -> dict:
    history = data.get("history")
    if not isinstance(history, list):
        _die_unreadable("journal 'history' missing or not a list")
    cycles = derive_cycles(history)
    cls = classify(cycles)
    verdict = gate(cls["consecutive_clean_count"], f2_state, cls["current_version"])
    rows = []
    for c, v in zip(cycles, cls["verdicts"]):
        rows.append({
            "cycle": c["cycle"],
            "version": c["version"],
            "txn_id": c["txn_id"],
            "timestamp": c["timestamp"],
            "verdict": v,
            "causes": list(c["causes"]),
        })
    journal_current = data.get("current")
    return {
        "journal": str(resolved),
        "f2_state": f2_state,
        "cycle_count": len(rows),
        "cycles": rows,
        "consecutive_clean_count": cls["consecutive_clean_count"],
        "current_version": cls["current_version"],
        "journal_current": str(journal_current) if journal_current is not None else None,
        "staleness": cls["staleness"],
        "superseded_cycles": cls["superseded_cycles"],
        "gate": verdict,
        "n_required": N_REQUIRED,
        "coverage_note": COVERAGE_NOTE,
    }


def render_plain(report: dict) -> str:
    lines = []
    lines.append(f"ledger-check: journal={report['journal']}")
    lines.append(f"f2-state: {report['f2_state']}")
    lines.append(f"cycles: {report['cycle_count']}")
    for row in report["cycles"]:
        line = (
            f"cycle {row['cycle']}: version={row['version']} "
            f"txn={row['txn_id']} verdict={row['verdict']}"
        )
        if row["causes"]:
            line += f" cause={','.join(row['causes'])}"
        lines.append(line)
    staleness = report["staleness"]
    if staleness.get("reset"):
        lines.append(
            "staleness: reset — count re-entered at cycle {entered_cycle} "
            "(version changed {from_version} → {to_version}); superseded "
            "cycles: {superseded}".format(
                entered_cycle=staleness["entered_cycle"],
                from_version=staleness["from_version"],
                to_version=staleness["to_version"],
                superseded=",".join(str(c) for c in report["superseded_cycles"]) or "none",
            )
        )
    else:
        lines.append("staleness: none (all cycles at the current version)")
    lines.append(f"current version: {report['current_version']}")
    if report["journal_current"] is not None:
        lines.append(f"journal current: {report['journal_current']}")
    lines.append(
        f"consecutive clean: {report['consecutive_clean_count']} "
        f"(need {report['n_required']}, ADR-021)"
    )
    lines.append(f"gate verdict: {report['gate']['verdict']}")
    for reason in report["gate"]["reasons"]:
        lines.append(f"  - {reason}")
    lines.append(f"note: {report['coverage_note']}")
    return "\n".join(lines)


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ledger_check.py",
        description=(
            "Journal-derived N-clean-cycles ledger checker (P2.3 B2). "
            "READ-ONLY: reads releases/state.json, never writes it (ADR-034). "
            "Derives per-cycle ledger rows and the live-promotion gate verdict "
            "(runbook §7 human table is derived from this output; when they "
            "disagree, the journal wins)."
        ),
        epilog=(
            "semantics:\n"
            "  cycle      one committed promote txn in journal history (a\n"
            "             terminal `commit` event); the journal writes no txn\n"
            "             id — the commit event's ts is the per-cycle handle.\n"
            "  window     from that cycle's commit event to the next cycle's\n"
            "             commit event (or journal end); history records no\n"
            "             txn-open event, so the commit IS the start marker.\n"
            "  CLEAN      window has zero rollback / sweep_rollback / halt\n"
            "             events; any such event ⇒ VIOLATION with cause.\n"
            "             NOTE — §4.1 clauses 2-5 (restart-cycle-clean,\n"
            "             readiness log-scan, work-loss resume evidence,\n"
            "             live-pid checkpoint) are EXTERNAL evidence audited\n"
            "             in RESULTS files; this checker covers the\n"
            "             journal-checkable clauses, the gate consumer folds\n"
            "             both.\n"
            "  staleness  cycles count ONLY within the same release version;\n"
            "             a version change resets the run and marks older\n"
            "             cycles SUPERSEDED (§4.3).\n"
            "  streak     trailing run of CLEAN cycles at one version. A\n"
            "             VIOLATION breaks the run WITHOUT erasing history —\n"
            "             reset-to-zero is reserved for staleness (conservative\n"
            "             gate-safe reading of ADR-021 'failed cycles do NOT\n"
            "             reset to zero automatically'; reset-on-fix question\n"
            "             still open in decisions.md ADR-021).\n"
            "  gate       ELIGIBLE iff consecutive-clean ≥ 3 (ADR-021 N=3\n"
            "             user-ruled) AND --f2-state closed. F2-open ⇒ BLOCKED\n"
            "             with reason F2-open REGARDLESS of count (runbook §9\n"
            "             hard block). Otherwise NOT-READY with needed-count.\n"
            "  live paths REFUSED (exit 78) when the resolved journal path is\n"
            "             under the live install root — resolved-path compare,\n"
            "             never name matching (the live dir name is a substring\n"
            "             of the demo dir name); fires before any read.\n"
            "  port rule this file contains no port literals; where prose must\n"
            "             refer to the live port it writes the words 'live port'\n"
            "             (runbook port-literal rule — the operator resolves the\n"
            "             value from the live install's own config at runtime).\n"
            "\n"
            "exit codes: 0 = ran (BLOCKED/NOT-READY are data, not errors) ·\n"
            "1 = unreadable/invalid journal · 78 = live-path refusal ·\n"
            "2 = CLI usage error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--journal",
        required=True,
        help="path to a releases/state.json journal file (required)",
    )
    parser.add_argument(
        "--f2-state",
        required=True,
        choices=("open", "closed"),
        help=(
            "F2 forge-lane state as an EXPLICIT caller-supplied input "
            "(required, no default) — per gate design, F2 state comes from "
            "the caller's knowledge, never derived"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable JSON output (same content as plain mode)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> None:
    args = parse_args(argv)
    resolved = check_live_path(args.journal)  # refusal fires before any read
    data = load_journal(resolved)
    report = build_report(resolved, args.f2_state, data)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_plain(report))


if __name__ == "__main__":
    main()
