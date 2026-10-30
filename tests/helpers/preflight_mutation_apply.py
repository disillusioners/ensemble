#!/usr/bin/env python3
"""The promote-preflight-hang MUTATION MATRIX (phase5 §2, T5.10, AC10).

One place for the planted defects M1–M32: every plant is an EXACT-STRING
replacement that must match exactly once (so a drifted source fails the matrix
loudly instead of planting nothing). The runner
(``tests/packs/preflight_mutation_matrix_test.sh``) backs the file up, plants,
runs the named pack, requires the named pin to turn RED, restores, and requires
GREEN.

Usage:
  python3 tests/helpers/preflight_mutation_apply.py --leg M1 --plant
  python3 tests/helpers/preflight_mutation_apply.py --leg M1 --restore-from <backup>
  python3 tests/helpers/preflight_mutation_apply.py --list

LEG STATUS
  * ``executable`` — planted + asserted by the runner.
  * ``deferred``   — the pinned behaviour does not exist yet (Pack I / phase 6).
    The runner records them as DEFERRED with the named symptom; it never
    reports them as passed.

NOTE ON M1/M6 (recorded in implementation-decisions.md D-23/D-24): both ask for
"the O(N) bash scan back". The UNBOUNDED form is the incident itself (~3,415 s
extrapolated on the 455 KB fixture) and cannot complete inside any CI budget,
so each leg executes a bounded PROXY whose semantics are the defect's:
M1 = the guarded scanner is the only path (the bound trips); M6 = the scanner
replaces the guarded path entirely (every size-guard refusal disappears).
"""
from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent

LIB = "scripts/upgrade/lib.sh"
HELPER = "scripts/upgrade/journal_read.py"
STAGE = "scripts/upgrade/stage.sh"
UJ = "daemon/tools/upgrade_journal.py"
MGR = "daemon/manager.py"
CFG = "daemon/config.py"

# ── the plants ──────────────────────────────────────────────────────────────
# leg -> {"file": <repo-relative>, "mode": executable|deferred,
#          "pairs": [(old, new), ...], "why": <what the defect is>}

PLANTS: dict[str, dict] = {
    "M1": {
        "file": LIB, "mode": "executable",
        "why": "journal_read reverted to the bash scan (guarded fallback only)",
        "pairs": [(
            '    if _journal_read_probe; then\n        "$_JR_PY" "$_JR_HELPER" verify "$jp"',
            '    if false; then  # PLANT M1: the O(N) helper is never used\n'
            '        "$_JR_PY" "$_JR_HELPER" verify "$jp"',
        )],
    },
    "M2": {
        "file": HELPER, "mode": "executable",
        "why": "the balance scanner no longer honours a backslash escape",
        "pairs": [(
            "        if esc:\n            esc = 0\n            continue",
            "        if False:  # PLANT M2: escape skip dropped\n            esc = 0\n            continue",
        )],
    },
    "M3": {
        "file": HELPER, "mode": "executable",
        "why": "the end-of-file in_str term is broken (the EOF string-state check no longer holds)",
        "pairs": [(
            "    return ob == cb and os_ == cs and in_str == 0",
            "    return ob == cb and os_ == cs and in_str == 1  # PLANT M3: the in_str EOF term broken",
        )],
    },
    "M4": {
        "file": HELPER, "mode": "executable",
        "why": "an EMPTY journal is accepted instead of refused (torn write trusted)",
        "pairs": [(
            "    if not data:\n        _warn(",
            "    if False:  # PLANT M4: torn/empty accepted\n        _warn(",
        )],
    },
    "M5": {
        "file": HELPER, "mode": "executable",
        "why": "journal_update writes a different byte (payload parity broken)",
        "pairs": [(
            '    sys.stdout.buffer.write(out)\n    sys.stdout.buffer.write(b"\\x1f")',
            '    sys.stdout.buffer.write(out + b" ")  # PLANT M5\n'
            '    sys.stdout.buffer.write(b"\\x1f")',
        )],
    },
    "M6": {
        "file": LIB, "mode": "executable",
        "why": "the O(N) scanner replaces the guarded read path entirely",
        "pairs": [
            (
                '    if _journal_read_probe; then\n        "$_JR_PY" "$_JR_HELPER" verify "$jp"',
                '    if false; then  # PLANT M6: probe bypassed\n'
                '        "$_JR_PY" "$_JR_HELPER" verify "$jp"',
            ),
            (
                '    _journal_read_fallback "$jp"\n}',
                '    _journal_read_legacy "$jp"  # PLANT M6: guard + bound bypassed\n}',
            ),
        ],
    },
    "M7": {
        "file": HELPER, "mode": "executable",
        "why": "the helper reads the journal TWICE (the D1-16 single-open contract)",
        "pairs": [(
            '    try:\n        with open(path, "rb") as fh:\n            raw = fh.read()',
            '    try:\n'
            '        with open(path, "rb") as fh:  # PLANT M7: a second open()\n'
            '            _ = fh.read()\n'
            '        with open(path, "rb") as fh:\n            raw = fh.read()',
        )],
    },
    "M8": {
        "file": HELPER, "mode": "executable",
        "why": "the incident cost reintroduced in the read path (a 6 s stall)",
        "pairs": [(
            "def cmd_verify(path):\n    data = _read_processed(path)",
            "def cmd_verify(path):\n"
            "    import time as _t; _t.sleep(6)  # PLANT M8: the symptom, re-planted\n"
            "    data = _read_processed(path)",
        )],
    },
    "M9": {
        "file": LIB, "mode": "executable",
        "why": "the per-step budget is raised past the planted slow step",
        "pairs": [(
            '    _run_bounded "$PREFLIGHT_STEP_BUDGET_S" "$@"',
            '    _run_bounded 600 "$@"  # PLANT M9: the budget no longer binds',
        )],
    },
    "M10": {
        "file": LIB, "mode": "executable",
        "why": "the breach handler releases the lock BEFORE journalling (the load-bearing order broken)",
        "pairs": [(
            '    journal_history_append halt "deadline-breach-${step} (budget=${budget} value=${value} elapsed=${elapsed}s phase=${phase}) daemon-state=${daemon_state}" \\\n'
            '                          || true   # a torn journal must not block the halt; the exit below is the contract\n'
            '    _warn "PREFLIGHT STEP BUDGET BREACH step=${step}: exceeded budget=${budget} value=${value}s elapsed=${elapsed}s phase=${phase} — halting (deadline-breach-${step}, exit 78; lock released)"\n'
            '    lock_release 2>/dev/null || true',
            '    lock_release 2>/dev/null || true  # PLANT M10: release FIRST (order broken)\n'
            '    journal_history_append halt "deadline-breach-${step} (budget=${budget} value=${value} elapsed=${elapsed}s phase=${phase}) daemon-state=${daemon_state}" \\\n'
            '                          || true\n'
            '    _warn "PREFLIGHT STEP BUDGET BREACH step=${step}: exceeded budget=${budget} value=${value}s elapsed=${elapsed}s phase=${phase} — halting (deadline-breach-${step}, exit 78; lock released)"',
        )],
    },
    "M11": {
        "file": LIB, "mode": "executable",
        "why": "the phase exclusion is widened to cover the preflight (the watchdog never fires)",
        "pairs": [(
            '    if [ "$rc" -eq 124 ]; then\n'
            '        _budget_breach PREFLIGHT_STEP_BUDGET_S "$step" "${_PIPELINE_PHASE:-preflight}" "$(( $(_now_epoch) - t0 ))"',
            '    case "${_PIPELINE_PHASE:-preflight}" in *preflight*) rc=0;; esac  # PLANT M11\n'
            '    if [ "$rc" -eq 124 ]; then\n'
            '        _budget_breach PREFLIGHT_STEP_BUDGET_S "$step" "${_PIPELINE_PHASE:-preflight}" "$(( $(_now_epoch) - t0 ))"',
        )],
    },
    "M12": {
        "file": LIB, "mode": "executable",
        "why": "the shell goes back to stdout-only (the per-run sink is dead)",
        "pairs": [(
            '_log()  {\n    if [ -n "${ENSEMBLE_UPGRADE_LOG_FILE:-}" ]; then',
            '_log()  {\n    if false; then  # PLANT M12: sink disabled',
        )],
    },
    "M13": {
        "file": LIB, "mode": "executable",
        "why": "the write-failure fallback is removed (a full disk is silent)",
        "pairs": [(
            '        _log_write_failed "${LOG_TAG}[${UP_TARGET:-lib}]: $*"\n        return 0\n    fi\n    printf \'%s[%s]: %s\\n\'',
            '        : # PLANT M13: write-failure fallback removed\n        return 0\n    fi\n    printf \'%s[%s]: %s\\n\'',
        )],
    },
    "M14": {
        "file": UJ, "mode": "executable",
        "why": "the unit's append capture points back at the AGGREGATE log",
        "pairs": [(
            "            argv, run_id, bus_kind, env, payload_path, unit_prefix=unit_prefix,",
            "            argv, run_id, bus_kind, env, log, unit_prefix=unit_prefix,  # PLANT M14",
        )],
    },
    "M15": {
        "file": LIB, "mode": "executable",
        "why": "the stale-lock reclaim stops journaling its row",
        "pairs": [(
            "                journal_history_append lock-stale-break \\",
            "                : \\",
        )],
    },
    "M16": {
        "file": STAGE, "mode": "executable",
        "why": "the success path no longer releases the lock explicitly",
        "pairs": [(
            "\nlock_release\nexit 0\n",
            "\n: # PLANT M16: explicit success-path release removed\nexit 0\n",
        )],
    },
    "M17": {
        "file": LIB, "mode": "executable",
        "why": "a per-character walk is planted OUTSIDE a legacy fallback",
        "pairs": [(
            'journal_write() {\n    local json="$1" jp tmp',
            'journal_write() {\n'
            '    local _plant_s="x" _plant_i=0 _plant_c="${json:i:1}"  # PLANT M17: a per-character walk\n'
            '    local json="$1" jp tmp',
        )],
    },
    "M18": {
        "file": LIB, "mode": "executable",
        "why": "a cited anchor construct shifts out of the working tree",
        "pairs": [("SOAK_S_DEFAULT=300", "SOAK_S_DEFAULT=301  # PLANT M18")],
    },
    "M19": {
        "file": MGR, "mode": "executable",
        "why": "the promote arm drops RuntimeMaxSec (no L3 backstop)",
        "pairs": [(
            "            _promote_unit_props: list[str] | None = None\n            if kind != \"restart\":",
            "            _promote_unit_props: list[str] | None = None\n            if False:  # PLANT M19",
        )],
    },
    "M20": {
        "file": UJ, "mode": "executable",
        "why": "the executor CLIENT goes back to a PIPE",
        "pairs": [(
            "            stderr=(client_err_fh if client_err_fh is not None\n                    else subprocess.DEVNULL),",
            "            stderr=subprocess.PIPE,  # PLANT M20",
        )],
    },
    "M21": {
        "file": CFG, "mode": "executable",
        "why": "the reaper's 660 s benign-detach threshold is moved",
        "pairs": [(
            "    upgrade_journal_reaper_timeout_seconds: int = Field(\n        default=660,",
            "    upgrade_journal_reaper_timeout_seconds: int = Field(\n        default=300,  # PLANT M21",
        )],
    },
    "M29": {
        "file": LIB, "mode": "executable",
        "why": "the guard refuses on the recovery/diagnostic classes too",
        "pairs": [(
            '        if [ "$JOURNAL_READ_STRICT" = "1" ]; then',
            '        if true; then  # PLANT M29: every class refuses',
        )],
    },
    "M30": {
        "file": LIB, "mode": "executable",
        "why": "the threshold is consulted on the O(N) path (next ceremony blocked)",
        "pairs": [(
            "journal_read() {\n    local jp\n    jp=\"$(journal_path)\"\n    if [ ! -f \"$jp\" ]; then\n        return 1\n    fi",
            "journal_read() {\n    local jp\n    jp=\"$(journal_path)\"\n    if [ ! -f \"$jp\" ]; then\n        return 1\n    fi\n"
            "    local _psz; _psz=\"$(wc -c < \"$jp\" | tr -d ' ')\"\n"
            "    if [ \"$_psz\" -ge \"$JOURNAL_SIZE_GUARD_BYTES\" ]; then  # PLANT M30\n"
            "        _warn \"planted: threshold consulted on the O(N) path\"\n        return 1\n    fi",
        )],
    },
    "M31": {
        "file": LIB, "mode": "executable",
        "why": "the fallback trips on the legacy class alone (a healthy small read is refused)",
        "pairs": [(
            '    if [ "$size" -ge "$JOURNAL_SIZE_GUARD_BYTES" ]; then',
            '    if true; then  # PLANT M31: size conjunct dropped',
        )],
    },
}

# ── Pack I legs: pinned by the ceremony-readiness contract, implemented in
# phase 6 (wave 4). Recorded as DEFERRED — never as passed.
DEFERRED_LEGS = {
    "M22": "plant a stale stamp ⇒ Pack I refuses (phase6 T6.4/T6.5)",
    "M23": "move the refusal after consume_pending_action ⇒ the nonce burns (phase6 D6-4)",
    "M24": "drop the arm-row provenance ⇒ Pack I C6/I10 reddens (phase6 T6.3)",
    "M25": "take the stamp from the executor instead of <dir>/lib.sh ⇒ Pack I I6 reddens (phase6 B3)",
    "M26": "plant a non-ancestor pinned dir ⇒ Pack I I2 reddens (phase6 D6-10)",
    "M27": "plant a missing floor object ⇒ Pack I I3 reddens (phase6 D6-10)",
    "M28": "make the non-git branch refuse ⇒ Pack I I5 reddens (phase6 D6-11 / Q2)",
    "M32": "make promote.sh ignore ENSEMBLE_PROMOTE_WATCHDOG ⇒ Pack I I11 reddens (phase6 T6.6)",
}


def _apply_pairs(rel: str, pairs: list[tuple[str, str]]) -> None:
    path = REPO / rel
    text = path.read_text(encoding="utf-8")
    for i, (old, new) in enumerate(pairs, 1):
        n = text.count(old)
        if n != 1:
            raise SystemExit(
                f"PLANT-FAILED: {rel} pair#{i} matched {n} times (want exactly 1) — "
                "the source drifted; the mutation leg would be vacuous"
            )
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg")
    ap.add_argument("--plant", action="store_true")
    ap.add_argument("--restore-from")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--file", action="store_true", help="print the leg's file")
    ap.add_argument("--mode", action="store_true", help="print the leg's mode")
    ap.add_argument("--why", action="store_true", help="print the leg's symptom")
    args = ap.parse_args(argv[1:])

    if args.list:
        for leg in sorted(PLANTS, key=lambda s: int(s[1:])):
            print(f"{leg}\t{PLANTS[leg]['mode']}\t{PLANTS[leg]['file']}\t{PLANTS[leg]['why']}")
        for leg in sorted(DEFERRED_LEGS, key=lambda s: int(s[1:])):
            print(f"{leg}\tdeferred\t-\t{DEFERRED_LEGS[leg]}")
        return 0

    if not args.leg:
        raise SystemExit("--leg is required")

    if args.leg in DEFERRED_LEGS:
        if args.mode:
            print("deferred")
            return 0
        if args.why:
            print(DEFERRED_LEGS[args.leg])
            return 0
        raise SystemExit(f"{args.leg} is DEFERRED: {DEFERRED_LEGS[args.leg]}")

    spec = PLANTS[args.leg]
    if args.file:
        print(spec["file"])
        return 0
    if args.mode:
        print(spec["mode"])
        return 0
    if args.why:
        print(spec["why"])
        return 0
    if args.restore_from:
        src = pathlib.Path(args.restore_from)
        (REPO / spec["file"]).write_bytes(src.read_bytes())
        return 0
    if args.plant:
        _apply_pairs(spec["file"], spec["pairs"])
        print(f"PLANTED {args.leg} in {spec['file']} ({len(spec['pairs'])} pair(s))")
        return 0
    raise SystemExit("one of --plant / --restore-from / --list is required")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
