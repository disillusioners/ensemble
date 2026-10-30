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
PROMOTE = "scripts/upgrade/promote.sh"
UJ = "daemon/tools/upgrade_journal.py"
MGR = "daemon/manager.py"
CFG = "daemon/config.py"
UTOOLS = "daemon/tools/upgrade_tools.py"

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
            "def cmd_verify(path: str) -> int:\n    data = _read_processed(path)",
            "def cmd_verify(path: str) -> int:\n"
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
    },    "M18": {
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

    # ── phase 6 (wave 4): the ceremony-readiness legs, now executable — the
    # guard they pin landed (upgrade_tools.scripts_dir_provenance_verdict +
    # the lib.sh stamp + the promote.sh watchdog token). Pack I runs in
    # --expect-implemented mode (the final-gate mode) for every leg.
    "M22": {
        "file": LIB, "mode": "executable",
        "why": "a stale tree predates the stamp: the PIPELINE_SCRIPTS_REV line is absent from lib.sh (the arm-time guard's B3 input)",
        "pairs": [(
            "PIPELINE_SCRIPTS_REV=unreleased-fix-promote-preflight-hang",
            "# PLANT M22: stamp line removed (a stale tree predates the stamp)",
        )],
    },
    "M23": {
        "file": UTOOLS, "mode": "executable",
        "why": "the provenance guard no longer precedes consume_pending_action (the D6-4 sequencing broken — the nonce would burn first)",
        "pairs": [(
            '            prov_verdict, prov_detail = scripts_dir_provenance_verdict(\n'
            '                scripts_dir,\n'
            '                floor_commit=(PIPELINE_SCRIPTS_FLOOR_COMMIT or None),\n'
            '                env=os.environ,\n'
            '            )',
            '            prov_verdict = "arm"  # PLANT M23: provenance guard neutralized pre-burn (D6-4 sequencing broken)\n'
            '            prov_detail = "PLANT M23: the guard no longer precedes consume_pending_action"',
        )],
    },
    "M24": {
        "file": UTOOLS, "mode": "executable",
        "why": "the arm-row provenance is dropped (the git revision + local-edit state leave the journal row)",
        "pairs": [(
            '        prov_git = (\n'
            '            f"{provenance} git_rev={rev} dirty={\'yes\' if edited else \'no\'}"\n'
            '        )',
            '        prov_git = f"{provenance}"  # PLANT M24: arm-row provenance dropped (T6.3/W5)',
        )],
    },
    "M25": {
        "file": UTOOLS, "mode": "executable",
        "why": "the stamp is taken from the executor env echo instead of <dir>/lib.sh (B3 broken)",
        "pairs": [(
            '    stamp, lib_sha = _pipeline_stamp_and_sha(path)',
            '    stamp = e.get("PIPELINE_SCRIPTS_REV")  # PLANT M25: the executor echo, not the file (B3 broken)\n'
            '    lib_sha = _pipeline_stamp_and_sha(path)[1]',
        )],
    },
    "M26": {
        "file": UTOOLS, "mode": "executable",
        "why": "the ancestry outcome is ignored — a non-ancestor pinned dir arms (D6-10 broken)",
        "pairs": [(
            '        if anc_rc == 0:\n'
            '            return (\n'
            '                "arm",',
            '        if True:  # PLANT M26: ancestry outcome ignored (D6-10 broken)\n'
            '            return (\n'
            '                "arm",',
        )],
    },
    "M27": {
        "file": UTOOLS, "mode": "executable",
        "why": "a missing/unknown floor object no longer refuses (D6-10 broken)",
        "pairs": [(
            '        return (\n'
            '            "refuse",\n'
            '            "floor-object-unknown: git merge-base --is-ancestor "',
            '        return (\n'
            '            "warn",  # PLANT M27: missing/unknown floor object no longer refuses (D6-10 broken)\n'
            '            "floor-object-unknown: git merge-base --is-ancestor "',
        )],
    },
    "M28": {
        "file": UTOOLS, "mode": "executable",
        "why": "the non-git branch refuses (Q2/D6-11 broken)",
        "pairs": [(
            '    return (\n'
            '        "warn",\n'
            '        f"non-git-no-stamp: WARNING only, never a refusal (Q2/D6-11); "',
            '    return (\n'
            '        "refuse",  # PLANT M28: the non-git branch refuses (Q2/D6-11 broken)\n'
            '        f"non-git-no-stamp: WARNING only, never a refusal (Q2/D6-11); "',
        )],
    },
    "M32": {
        "file": PROMOTE, "mode": "executable",
        "why": "promote.sh ignores ENSEMBLE_PROMOTE_WATCHDOG (the mixed-generation token never checked)",
        "pairs": [(
            'if [ "${ENSEMBLE_PROMOTE_WATCHDOG:-}" = "1" ]; then',
            'if false; then  # PLANT M32: promote.sh ignores ENSEMBLE_PROMOTE_WATCHDOG (T6.6 broken)',
        )],
    },
    # ── verifier-fix wave (2026-10-30, FIX ITERATION 1): M33–M40 pin the five
    # verifier findings' fixes. Each leg is the DEFECT RE-INTRODUCED, exactly
    # as the verifier described it.
    "M33": {
        "file": UJ, "mode": "executable",
        "why": "bounded_git_probe has NO exception guard (W-1: an absent/hung git unwinds the arm's generic handler, which never releases the pipeline lock)",
        "pairs": [
            (
                '    try:\n        proc = subprocess.run(\n            ["git", "-C", str(cwd), *args],',
                '    if True:  # PLANT M33: the W-1 guard removed (probe exceptions escape)\n        proc = subprocess.run(\n            ["git", "-C", str(cwd), *args],',
            ),
            (
                '    except (OSError, subprocess.SubprocessError) as exc:\n        return GIT_PROBE_ERROR_RC, "", f"{type(exc).__name__}: {exc}"\n',
                '    # PLANT M33: the except arm deleted\n',
            ),
        ],
    },
    "M34": {
        "file": UTOOLS, "mode": "executable",
        "why": "the probe-error class is dead — a failed git probe is folded into the non-git branch, where a stamped dir ARMS on an unverified ancestry (W-1 verdict half)",
        "pairs": [(
            '    if rev_rc == uj.GIT_PROBE_ERROR_RC:',
            '    if False:  # PLANT M34: probe-error class removed (falls through to the non-git branch)',
        )],
    },
    "M35": {
        "file": UTOOLS, "mode": "executable",
        "why": "the scripts-dir-unusable guard is dead — a dir with no lib.sh is judged as a stamped/non-git dir instead of refusing (W-3 branch 3)",
        "pairs": [(
            '    if not path.is_dir() or not (path / "lib.sh").is_file():',
            '    if False:  # PLANT M35: scripts-dir-unusable guard removed (W-3c branch dead)',
        )],
    },
    "M36": {
        "file": PROMOTE, "mode": "executable",
        "why": "the command-substituted journal-read caller no longer recognises the breach rc — a step-budget breach falls into the read-failure arm and writes a SECOND, mislabelled halt row (A-4; the defect is the CONSEQUENCE, `|| {` vs `; rc=$?` is the mechanism)",
        "pairs": [(
            '    if [ "$J_READ_RC" -eq "$BUDGET_BREACH_RC" ]; then',
            '    if false; then  # PLANT M36: the breach rc is not recognised (second row written)',
        )],
    },
    "M37": {
        "file": LIB, "mode": "executable",
        "why": "the stage-time stamp gate no longer requires a 40-hex sha — the pre-release placeholder ships (W-2)",
        "pairs": [(
            '''    if ! printf '%s' "$stamp" | grep -Eq '^[0-9a-f]{40}$'; then''',
            '''    if false; then  # PLANT M37: the 40-hex requirement dropped (W-2 broken)''',
        )],
    },
    "M38": {
        "file": LIB, "mode": "executable",
        "why": "the stage-time stamp gate no longer requires the stamp to EQUAL the floor (D6-8's 'the two travel together' broken)",
        "pairs": [(
            '    if [ -n "$floor" ] && [ "$stamp" != "$floor" ]; then',
            '    if false; then  # PLANT M38: floor-equality dropped (D6-8 broken)',
        )],
    },
    "M39": {
        "file": UTOOLS, "mode": "executable",
        "why": "an UNSET floor no longer refuses fail-closed on a git-shaped pinned dir (D-31/D6-10 command-error class weakened to a warning)",
        "pairs": [(
            '''        if not floor_commit:\n            return (\n                "refuse",''',
            '''        if not floor_commit:\n            return (\n                "warn",  # PLANT M39: floor-unset downgraded from refuse''',
        )],
    },
    "M40": {
        "file": UTOOLS, "mode": "executable",
        "why": "the caller's fallback-dir carve-out is gone — a refusal on an UNPINNED (fallback) scripts dir bricks backward-compatible arms (W-3 branch 2)",
        "pairs": [(
            '            if prov_verdict == "refuse" and not os.environ.get(\n                "ENSEMBLE_UPGRADE_SCRIPTS_DIR", ""\n            ).strip():',
            '            if False:  # PLANT M40: fallback-dir warn downgrade removed',
        )],
    },
    # ── FIX CYCLE 2 (code council 2026-10-30, 3 WARNINGs + advisories):
    # M41–M46 pin the fixes. Each leg is the DEFECT RE-INTRODUCED, exactly as
    # the council/advisory described it.
    "M41": {
        "file": UTOOLS, "mode": "executable",
        "why": "the git-shaped 128 band is dead — `git rev-parse` answering 128 (dubious ownership / corrupt .git / pruned gitfile) falls into the non-git branch, where a self-asserted stamp ARMS (W2 fail-open)",
        "pairs": [(
            '    if rev_rc != 0 and rev_rc != uj.GIT_PROBE_ERROR_RC and (path / ".git").exists():',
            '    if False:  # PLANT M41: the 128-on-git-shaped refuse arm removed (W2 fail-open)',
        )],
    },
    "M42": {
        "file": LIB, "mode": "executable",
        "why": "the supervision probe's budget breach is not kind-scoped — a wedged D-Bus fail-fast halts restart.sh (a RECOVERY-class script, R1 violation) instead of warning and continuing (W3)",
        "pairs": [(
            "            ''|promote:*)\n",
            "            ''|promote:*|restart:*)  # PLANT M42: the recovery phase is not scoped out (W3)\n",
        )],
    },
    "M43": {
        "file": PROMOTE, "mode": "executable",
        "why": "the adopt-txn arm no longer propagates the step's own exit-78 — `_refuse txn-busy` appends a SECOND, contradicting refusal row over the adopt diagnostics' halt row (W1)",
        # NOTE: no `# PLANT` comment is possible here — it would comment out
        # the rest of the one-line arm (`_refuse ...; }`) and turn the defect
        # into a syntax error. Deleting the propagation token reproduces the
        # W1 defect exactly: the caller falls straight through to `_refuse`.
        "pairs": [(
            '{ _arc=$?; [ "$_arc" -eq 78 ] && exit 78; _refuse txn-busy',
            '{ _refuse txn-busy',
        )],
    },
    "M44": {
        "file": LIB, "mode": "executable",
        "why": "the current run's `<run>.client.log` is no longer fenced — retention keeps it only via the <6 h mtime fence, so the running run's client log can be deleted (A2)",
        "pairs": [(
            '"${cur_ptr_run}.client.log"|"${cur_ptr_run}.payload.client.log") continue ;; esac ;;',
            '"${cur_ptr_run}.payload.client.log") continue ;; esac ;;  # PLANT M44: <run>.client.log unfenced (A2)',
        )],
    },
    "M45": {
        "file": LIB, "mode": "executable",
        "why": "a garbage numeric knob passes through unvalidated — `PREFLIGHT_PHASE_BUDGET_S=abc` dies cryptically in `$(( ))` and `PREFLIGHT_STEP_BUDGET_S=abc` trips the step watcher instantly at elapsed=0 (A5)",
        "pairs": [(
            '    case "$__raw" in\n        *[!0-9]*)',
            '    case "$__raw" in\n        *NOPE*)  # PLANT M45: non-numeric values pass through (A5)',
        )],
    },
    "M46": {
        "file": LIB, "mode": "executable",
        "why": "the tree-walk worker gets no forwarding TERM — a budget kill orphans the `xargs … shasum` batch and leaks the mktemp pair (A1)",
        "pairs": [(
            '    trap \'{ [ -n "$_tm_sub" ] && kill -TERM "$_tm_sub" 2>/dev/null; [ -n "$_tm_sub" ] && wait "$_tm_sub" 2>/dev/null; exit 143; }\' TERM INT HUP',
            '    trap \'exit 143\' TERM INT HUP  # PLANT M46: no forwarding TERM to the worker (A1)',
        )],
    },
}

# ── Pack I legs (phase 6 / wave 4): previously deferred, now EXECUTABLE in
# PLANTS above — the guard they pin landed. The list stays EMPTY; the runner
# treats an empty list as "no deferrals remain" (the full 32/32 span).
DEFERRED_LEGS: dict[str, str] = {}


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
