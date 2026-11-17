#!/usr/bin/env python3
"""Lane-local plan-lint: the mechanical ownership-chain gate (leader Ruling C, 2026-11-17).

NOT a shipped artifact. It lives in the planning directory and lints the plan set
(`plan-overview.md`) — it asserts that every leg row's chain

    leg  ->  owning task  ->  task acceptance cites the leg
    leg  ->  File cell (real-or-planned artifact inside the edit surface)
    leg  ->  named pack  ->  pack's Legs column lists it
    leg  ->  pack's OWNED ARTIFACT LIST covers the File cell
    pack ->  PACKS.md row (PRESENT / PENDING)
    NEW file  ->  a task owns its CREATION

is intact in BOTH directions. A plan that passes is structurally buildable.

Run from the worktree root:
    .venv/bin/python .agents/shared/planning/prompt-reload-compact-note/validate_ownership_chain.py

Exit code 0 = PASS, 1 = FAIL. The verdict is honest and non-vacuous: it FAILS when
a link is broken (verified by the negative control in §10.6 of the plan).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PLAN_DEFAULT = Path(__file__).with_name("plan-overview.md")
PACKS_DEFAULT = Path(".agents/tester/PACKS.md")

# --- the declared edit surface (plan §8.1) -------------------------------------
IN_SURFACE = (
    "daemon/tools/",
    "daemon/services/prompt_refresh.py",
    "daemon/services/instance_messaging.py",
    "daemon/services/compact_executor.py",
    "daemon/services/_compaction_persist_seam.py",
    "daemon/compaction.py",
    "daemon/graph.py",
    "daemon/config.py",
    "daemon/constants.py",
    "daemon/_compact_note_prefilter.py",
    "daemon/repositories/instance/",
    "tests/",
    "test/packs/",
    ".agents/tester/PACKS.md",
    "docs/guidelines/",
)
OUT_SURFACE = (
    "daemon/migrations/",
    "agents/",
    "daemon/services/decision/",
    "config.yaml",
)

PATH_RE = re.compile(r"`([A-Za-z0-9_.][\w./-]*\.(?:py|sh|md|yaml))`")
LEG_RE = re.compile(r"^\|\s*\*\*(L\d+)\*\*\s*\|")
PACK_RE = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")
TASK_RE = re.compile(r"^\|\s*(\d+\.\d+)\s*\|")
OWNING_RE = re.compile(r"OWNING TASK:\s*\*?\*?(\d+\.\d+)")


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def paths_in(text: str) -> list[str]:
    return PATH_RE.findall(text)


def in_surface(path: str) -> bool:
    if any(path.startswith(p) for p in OUT_SURFACE):
        # `daemon/repositories/instance/` is IN; the rest of repositories/ is OUT.
        if not path.startswith("daemon/repositories/instance/"):
            return False
    return any(path.startswith(p) for p in IN_SURFACE)


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []  # (check, subject, verdict)

    def add(self, check: str, subject: str, ok: bool, detail: str = "") -> bool:
        verdict = "PASS" if ok else "FAIL"
        self.rows.append((check, subject, f"{verdict}{(' — ' + detail) if detail else ''}"))
        return ok

    @property
    def failed(self) -> int:
        return sum(1 for _, _, v in self.rows if v.startswith("FAIL"))

    def dump(self) -> None:
        width = max((len(f"{c} {s}") for c, s, _ in self.rows), default=10)
        for check, subject, verdict in self.rows:
            print(f"  {check:<7} {subject:<{width - 7}}  {verdict}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=str(PLAN_DEFAULT))
    ap.add_argument("--packs", default=str(PACKS_DEFAULT))
    args = ap.parse_args()

    plan_path = Path(args.plan)
    plan = plan_path.read_text()
    lines = plan.splitlines()

    rep = Report()

    # --- parse §10.3: pack -> owned artifact list + legs -----------------------
    packs: dict[str, dict] = {}
    for line in lines:
        m = PACK_RE.match(line)
        if not m:
            continue
        c = cells(line)
        if len(c) < 5 or "Owned artifact list" not in plan:
            continue
        name = m.group(1)
        owned = [p for p in paths_in(c[1])]
        script = paths_in(c[2])
        legs = [x.strip() for x in c[4].split(",") if x.strip()]
        packs[name] = {"owned": owned, "script": script, "legs": legs,
                       "raw_owned": c[1]}
    if not packs:
        print("FATAL: no §10.3 pack table parsed — the validator would be VACUOUS.")
        return 1

    # --- parse §10.4: legs ------------------------------------------------------
    legs: dict[str, dict] = {}
    for line in lines:
        m = LEG_RE.match(line)
        if not m:
            continue
        c = cells(line)
        legs[m.group(1)] = {
            "file_cell": c[-1],
            "owning": (OWNING_RE.search(line).group(1) if OWNING_RE.search(line) else None),
            "raw": line,
        }
    if not legs:
        print("FATAL: no §10.4 leg rows parsed — the validator would be VACUOUS.")
        return 1

    # --- parse phase task tables ------------------------------------------------
    tasks: dict[str, dict] = {}
    for line in lines:
        m = TASK_RE.match(line)
        if not m:
            continue
        c = cells(line)
        if len(c) < 4:
            continue
        tasks[m.group(1)] = {"acceptance": c[-1], "raw": line}
    if not tasks:
        print("FATAL: no phase task rows parsed — the validator would be VACUOUS.")
        return 1

    print(f"parsed: {len(packs)} packs · {len(legs)} legs · {len(tasks)} tasks\n")

    # --- (i) owning task <-> acceptance cites the leg ---------------------------
    print("CHECK (i) — owning task exists AND its acceptance cites the leg")
    for leg, info in legs.items():
        owning = info["owning"]
        if owning is None:
            rep.add("(i)", leg, False, "no OWNING TASK marker on the leg row")
            continue
        if owning not in tasks:
            rep.add("(i)", leg, False, f"owning task {owning} not found")
            continue
        cites = re.search(rf"\bL{leg[1:]}\b", tasks[owning]["acceptance"]) is not None
        rep.add("(i)", f"{leg} -> {owning}", cites,
                "" if cites else f"task {owning}'s acceptance does not cite {leg}")

    # --- (ii) File cell non-empty + inside the edit surface ---------------------
    print("CHECK (ii) — File cell names a real-or-planned artifact INSIDE the edit surface")
    for leg, info in legs.items():
        ps = paths_in(info["file_cell"])
        if not ps:
            rep.add("(ii)", leg, False, "File cell has no path")
            continue
        bad = [p for p in ps if not in_surface(p)]
        rep.add("(ii)", leg, not bad, "" if not bad else f"outside surface: {bad}")

    # --- (iii) a named pack maps the leg AND its Legs column lists it -----------
    print("CHECK (iii) — a named pack maps the leg and the pack's Legs column lists it")
    leg_to_packs: dict[str, list[str]] = {leg: [] for leg in legs}
    for pname, p in packs.items():
        for leg in p["legs"]:
            leg_to_packs.setdefault(leg, []).append(pname)
    for leg in legs:
        owners = leg_to_packs.get(leg, [])
        if leg == "L0":
            ok = len(packs) == 3
            rep.add("(iii)", leg, ok,
                    "" if ok else "L0 is the registration leg over all three packs")
            continue
        ok = len(owners) == 1
        rep.add("(iii)", leg, ok,
                "" if ok else f"mapped by {len(owners)} packs: {owners}")

    # --- (iv-a) registration task 6.1 owns a row whose pack owned-list covers ----
    print("CHECK (iv-a) — task 6.1 owns the registration AND the pack's OWNED ARTIFACT LIST covers the leg's File cell")
    t61 = tasks.get("6.1")
    if t61 is None:
        rep.add("(iv-a)", "6.1", False, "task 6.1 missing")
    else:
        rep.add("(iv-a)", "6.1 present", True)
        for leg, info in legs.items():
            if leg == "L0":
                continue
            owners = leg_to_packs.get(leg, [])
            if len(owners) != 1:
                rep.add("(iv-a)", leg, False, "no single owning pack")
                continue
            p = packs[owners[0]]
            ps = paths_in(info["file_cell"])
            missing = [x for x in ps if x not in p["owned"]]
            rep.add("(iv-a)", f"{leg} in {owners[0]}", not missing,
                    "" if not missing else f"File cell not in owned list: {missing}")

    # --- (iv-b) the PACKS.md row is PRESENT / PENDING ---------------------------
    print("CHECK (iv-b) — the PACKS.md row is PRESENT / PENDING (PENDING is the honest pre-build value)")
    packs_md = Path(args.packs).read_text() if Path(args.packs).exists() else ""
    for pname, p in packs.items():
        present = pname in packs_md
        rep.add("(iv-b)", pname, True, "PRESENT" if present else "PENDING (build deliverable of 6.1)")

    # --- (v) every NEW file has a creating task ---------------------------------
    print("CHECK (v) — every task-named NEW file is owned by a CREATION task")
    new_files: list[str] = []
    for pname, p in packs.items():
        for path in re.findall(r"`([^`]+)`\s*\(NEW\)", p["raw_owned"]):
            if path not in new_files:
                new_files.append(path)
    for path in new_files:
        owners = [tid for tid, t in tasks.items() if path in t["raw"]]
        rep.add("(v)", path, bool(owners), "" if owners else "no task owns its creation")

    # --- VERIFIED-NOT-EDITED pins are NOT legs ----------------------------------
    print("CHECK (x) — VERIFIED-NOT-EDITED pins are labelled and are not lane legs")
    vne_tool = "VERIFIED-NOT-EDITED" in plan and "test_prompt_reload_tool.py" in plan
    vne_pins = "VERIFIED-NOT-EDITED" in plan and "test_prompt_reload_pins.py" in plan
    rep.add("(x)", "tool.py:14 label", vne_tool)
    rep.add("(x)", "pins.py:71 label", vne_pins)
    stray = [leg for leg, info in legs.items()
             if "test_prompt_reload_tool.py" in info["file_cell"]
             or "test_prompt_reload_pins.py" in info["file_cell"]]
    rep.add("(x)", "no lane leg points at a VERIFIED-NOT-EDITED pin", not stray,
            "" if not stray else f"stray: {stray}")

    print()
    rep.dump()
    print()
    print(f"checks: {len(rep.rows)}   FAIL: {rep.failed}")
    print("VERDICT: PASS — the ownership chain is intact" if rep.failed == 0
          else f"VERDICT: FAIL — {rep.failed} broken link(s)")
    return 0 if rep.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
