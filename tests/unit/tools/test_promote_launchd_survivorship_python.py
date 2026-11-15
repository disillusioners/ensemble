"""Pin tests for the DARWIN executor-survivorship fix (Fix #5, ENSEMBLE-32
Family-3) — Python twin of tests/test_promote_launchd_survivorship.sh.

Run with:
    python3 tests/unit/tools/test_promote_launchd_survivorship_python.py

Self-contained: NO daemon dependencies. Drives the REAL lib.sh detach
machinery through subprocess bash with PATH-injected launchctl/ps stubs
(the SYSTEMCTL_BIN / LAUNCHCTL_BIN house seam discipline — never a real
launchd domain, never ~/Library/LaunchAgents), and pins the structural
wiring of promote.sh / lib.sh / stop-ensemble.sh.

Coverage (mirrors the shell pack's sections):
  1. plist generation is VALID XML (plistlib parse: Label label-verbatim,
     RunAtLoad true, KeepAlive false, ProgramArguments = [/bin/bash, wrapper],
     StandardOutPath present) — the incident §7b footgun surface.
  2. the recursion marker + env-parity contract: the generated env snapshot
     arms ENSEMBLE_PROMOTE_SELF_DETACHED=1 and NEVER carries
     ENSEMBLE_LAUNCHER_STATE_FILE (env-parity rule).
  3. exit-code propagation: a detached payload exiting 7 propagates rc 7
     through _executor_detach_self (the E2E demo promote contract).
  4. bootstrap failure is fail-closed: stub launchctl refusing ⇒ nonzero rc
     + _DETACH_LAST_ERROR recorded (Fix #4 discipline — never silent).
  5. ancestry detection: an ensemble-shaped ancestor chain ⇒ in-tree; a
     plain operator chain ⇒ not (scripted ps stub, subprocess bash).
  6. structural pins: promote.sh's detach block runs BEFORE lock_acquire;
     the Amendment #1 boundary is stated at both detach sites (ONLY the
     executor detaches — the DAEMON keeps the no-nohup rule); the incident
     (2026-11-14 §7 defect #7) and the VM mirror (2026-09-28 cgroup
     teardown) are cited; stop-ensemble.sh _stop_pids and lib.sh
     _supervision_stop_launchd run the self-exclusion filter.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_SH = REPO_ROOT / "scripts" / "upgrade" / "lib.sh"
PROMOTE_SH = REPO_ROOT / "scripts" / "upgrade" / "promote.sh"
STOP_SH = REPO_ROOT / "scripts" / "stop-ensemble.sh"

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS: {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"FAIL: {name} {('— ' + detail) if detail else ''}")


LAUNCHCTL_STUB = textwrap.dedent(
    """\
    #!/bin/bash
    ctrl="${LCTL_CTRL:-/nonexistent-lctl-ctrl}"
    calls="${LCTL_CALLS:-/nonexistent-lctl-calls}"
    printf '%s\\n' "$*" >> "$calls"
    mode="$(sed -n 's/^mode=//p' "$ctrl" 2>/dev/null | head -1)"
    mode="${mode:-exec}"
    case "$1" in
        bootstrap)
            [ "$mode" = "fail" ] && { echo "bootstrap failed (stub): 5: Input/output error" >&2; exit 5; }
            plist="$3"
            wrapper="$(sed -n '/<key>ProgramArguments<\\/key>/,/<\\/array>/p' "$plist" | sed -n 's/.*<string>\\(.*\\)<\\/string>.*/\\1/p' | sed -n '2p')"
            [ -n "$wrapper" ] && [ -f "$wrapper" ] || { echo "stub: cannot resolve wrapper" >&2; exit 3; }
            logfile="$(sed -n 's/.*<string>\\(.*\\)<\\/string>.*/\\1/p' "$plist" | sed -n '4p')"
            nohup env -i HOME="${HOME:-/tmp}" PATH=/usr/bin:/bin:/usr/sbin:/sbin \\
                python3 -c 'import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \\
                /bin/bash "$wrapper" </dev/null >>"${logfile:-/dev/null}" 2>&1 &
            exit 0
            ;;
        bootout) exit 0 ;;
        print) echo "pid = 424242"; echo "state = running"; exit 0 ;;
        *) exit 0 ;;
    esac
    """
)

PS_STUB = textwrap.dedent(
    """\
    #!/bin/bash
    map="${PS_MAP:-/nonexistent-ps-map}"
    field="$1"
    pid="$1"; for _a in "$@"; do pid="$_a"; done
    case "$field" in
        -o)
            case "$2" in
                ppid=*) v="$(sed -n "s/^$pid ppid //p" "$map" 2>/dev/null | head -1)"
                        [ -n "$v" ] || v="$(sed -n 's/^DEFAULT ppid //p' "$map" 2>/dev/null | head -1)"
                        [ -n "$v" ] || exec /bin/ps "$@"
                        printf '%s\\n' "$v" ;;
                args=*) v="$(sed -n "s/^$pid args //p" "$map" 2>/dev/null | head -1)"
                        [ -n "$v" ] || exec /bin/ps "$@"
                        printf '%s\\n' "$v" ;;
                comm=*) v="$(sed -n "s/^$pid comm //p" "$map" 2>/dev/null | head -1)"
                        [ -n "$v" ] || exec /bin/ps "$@"
                        printf '%s\\n' "$v" ;;
                *) exec /bin/ps "$@" ;;
            esac
            ;;
        *) exec /bin/ps "$@" ;;
    esac
    """
)

LSOF_STUB = textwrap.dedent(
    """\
    #!/bin/bash
    map="${LSOF_MAP:-/nonexistent-lsof-map}"
    pid="$3"
    cwd="$(sed -n "s/^$pid //p" "$map" 2>/dev/null | head -1)"
    [ -n "$cwd" ] && printf 'n%s\\n' "$cwd"
    exit 0
    """
)


def make_stub_env(tmp: Path) -> dict[str, str]:
    stubbin = tmp / "stubbin"
    stubbin.mkdir(exist_ok=True)
    (stubbin / "launchctl").write_text(LAUNCHCTL_STUB)
    (stubbin / "launchctl").chmod(0o755)
    (stubbin / "ps").write_text(PS_STUB)
    (stubbin / "ps").chmod(0o755)
    (stubbin / "lsof").write_text(LSOF_STUB)
    (stubbin / "lsof").chmod(0o755)
    (tmp / "lctl-ctrl").write_text("mode=exec\n")
    (tmp / "lctl-calls").write_text("")
    (tmp / "ps-map").write_text("DEFAULT ppid 1\n")
    (tmp / "lsof-map").write_text("")
    env = dict(os.environ)
    env["PATH"] = f"{stubbin}:{env.get('PATH', '')}"
    env["LCTL_CTRL"] = str(tmp / "lctl-ctrl")
    env["LCTL_CALLS"] = str(tmp / "lctl-calls")
    env["LCTL_SPAWNED"] = str(tmp / "lctl-spawned")
    env["PS_MAP"] = str(tmp / "ps-map")
    env["LSOF_MAP"] = str(tmp / "lsof-map")
    env["PROMOTE_DETACH_WAIT_S"] = "60"
    env["PROMOTE_DETACH_POLL_S"] = "1"
    return env


def bash_env_snippet(tmp: Path, env: dict, snippet: str) -> str:
    """Run a bash snippet with lib.sh sourced under the stub env; stdout back."""
    script = (
        f"export INSTALL_DIR={tmp / 'inst'}\n"
        f"mkdir -p {tmp / 'inst' / 'data'}\n"
        f". '{LIB_SH}' >/dev/null 2>&1\n"
        f"{snippet}\n"
    )
    proc = subprocess.run(
        ["/bin/bash", "-c", script],
        env=env, capture_output=True, text=True, timeout=90,
    )
    return proc.stdout + proc.stderr


def wait_for(path: Path, needle: str, budget_s: float = 45.0) -> bool:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        try:
            if needle in path.read_text(errors="replace"):
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def main() -> None:  # noqa: C901 — a linear pin script, mirrors the shell pack
    with tempfile.TemporaryDirectory(prefix="launchd-surv-py.") as td:
        tmp = Path(td).resolve()
        env = make_stub_env(tmp)

        # ── 1+2. plist validity + env contract, in ONE detach run ───────────
        # The payload dumps its own env and sleeps long enough for the test
        # to capture the generated plist from the stub's call log mid-flight.
        (tmp / "probe.sh").write_text(
            "#!/bin/bash\nenv > '%s'\nsleep 4\nexit 7\n" % (tmp / "detached-env.txt")
        )
        out = bash_env_snippet(
            tmp, env,
            "ps() { case \"$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }\n"
            f"_executor_detach_self '{tmp}/probe.sh' demo --version v9-py &\n"
            "bgpid=$!\n"
            "found=''\n"
            "i=0\n"
            "while [ $i -lt 60 ]; do\n"
            "    found=$(sed -n 's/^bootstrap gui\\/[0-9]* //p' \"$LCTL_CALLS\" | tail -1)\n"
            "    [ -n \"$found\" ] && [ -f \"$found\" ] && break\n"
            "    sleep 0.2; i=$((i + 1))\n"
            "done\n"
            "[ -n \"$found\" ] && cp \"$found\" '%s' 2>/dev/null\n"
            "echo original_name=$(basename \"${found:-NONE}\")\n"
            "wait $bgpid; echo rc=$?\n" % (tmp / "captured.plist"),
        )
        check("1: the detach ran and rc propagated (payload exit 7)",
              "rc=7" in out, out[-200:])
        plist_path = tmp / "captured.plist"
        original_name = re.search(r"original_name=(\S+)", out or "")
        check("1a: the generated plist was captured mid-flight",
              plist_path.is_file() and original_name is not None
              and original_name.group(1).startswith("com.ensemble.promote-"))
        if plist_path.is_file():
            with plist_path.open("rb") as fh:
                plist = plistlib.load(fh)
            check("1b: plistlib parses the generated plist", isinstance(plist, dict))
            check("1c: Label is label-verbatim (com.ensemble.promote-…)",
                  str(plist.get("Label", "")).startswith("com.ensemble.promote-"),
                  str(plist.get("Label")))
            args = plist.get("ProgramArguments") or []
            check("1d: ProgramArguments = [/bin/bash, <wrapper>]",
                  len(args) == 2 and args[0] == "/bin/bash", str(args))
            check("1e: RunAtLoad true (one-shot at load)", plist.get("RunAtLoad") is True)
            check("1f: KeepAlive false (no respawn)", plist.get("KeepAlive") is False)
            check("1g: StandardOutPath present (forensics log)",
                  bool(plist.get("StandardOutPath")))
            check("1h: the plist FILENAME == LABEL (Family-1 invariant)",
                  original_name is not None
                  and original_name.group(1) == f"{plist.get('Label')}.plist",
                  f"file={original_name.group(1) if original_name else 'NONE'} "
                  f"label={plist.get('Label')}")

        env_txt = (tmp / "detached-env.txt").read_text(errors="replace")
        check("2: the recursion marker is armed in the detached env",
              "ENSEMBLE_PROMOTE_SELF_DETACHED=1" in env_txt)
        check("2a: ENSEMBLE_LAUNCHER_STATE_FILE NEVER reaches the detached env",
              "ENSEMBLE_LAUNCHER_STATE_FILE" not in env_txt)

        # ── 3. exit-code propagation (payload exit 7 asserted above) ────────
        # ── 4. bootstrap failure ⇒ fail-closed + diagnosis recorded ────────
        (tmp / "lctl-ctrl").write_text("mode=fail\n")
        out = bash_env_snippet(
            tmp, env,
            "ps() { case \"$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }\n"
            "_executor_detach_self '%s' >/dev/null 2>&1\n"
            "echo rc=$?\n"
            "echo err=${_DETACH_LAST_ERROR:-UNSET}\n" % (tmp / "probe.sh"),
        )
        check("4: bootstrap failure returns NONZERO", "rc=1" in out, out[-200:])
        check("4a: _DETACH_LAST_ERROR records the launchctl diagnosis",
              "err=launchctl bootstrap gui/" in out, out[-200:])

        # ── 5. ancestry detection (scripted ps stub) ────────────────────────
        (tmp / "ps-map").write_text(
            "DEFAULT ppid 13\n"
            "11 ppid 1\n"
            f"11 args bash {tmp / 'inst' / 'launcher.sh'} --serve\n"
            "11 comm launcher.sh\n"
            "12 ppid 11\n"
            f"12 args {tmp / 'inst' / 'current' / 'ensemble-prod'} --serve\n"
            "12 comm ensemble-prod\n"
            "13 ppid 12\n"
            "13 args bash -c agent-command\n"
            "13 comm bash\n"
        )
        out = bash_env_snippet(tmp, env, "echo in_tree=$(_executor_detach_in_tree; echo $?)")
        check("5: an ensemble-shaped ancestor chain IS in-tree", "in_tree=0" in out, out[-120:])
        # 5a-neg: the cwd-anchored comm branch — a foreign-install daemon shape
        # (cwd elsewhere) must NOT count (a fixture promote runs inside the
        # REAL daemon's tree of another install).
        (tmp / "ps-map").write_text(
            "DEFAULT ppid 13\n"
            "11 ppid 1\n"
            "11 args ./launcher.sh\n"
            "11 comm launcher.sh\n"
            "12 ppid 11\n"
            "12 args ./ensemble-prod\n"
            "12 comm ensemble-prod\n"
            "13 ppid 12\n"
            "13 args bash -c agent-command\n"
            "13 comm bash\n"
        )
        (tmp / "lsof-map").write_text(
            f"11 {tmp / 'elsewhere'}\n12 {tmp / 'elsewhere'}\n"
        )
        out = bash_env_snippet(tmp, env, "echo in_tree=$(_executor_detach_in_tree; echo $?)")
        check("5a: a foreign-install daemon shape (cwd elsewhere) is NOT in-tree",
              "in_tree=1" in out, out[-120:])
        (tmp / "lsof-map").write_text(
            f"11 {tmp / 'inst'}\n12 {tmp / 'inst'}\n"
        )
        # relative-argv daemon (tier-1b shape): comm carries it, cwd==install
        (tmp / "ps-map").write_text(
            "DEFAULT ppid 13\n"
            "11 ppid 1\n"
            "11 args ./launcher.sh\n"
            "11 comm launcher.sh\n"
            "12 ppid 11\n"
            "12 args ./ensemble-prod\n"
            "12 comm ensemble-prod\n"
            "13 ppid 12\n"
            "13 args bash -c agent-command\n"
            "13 comm bash\n"
        )
        out = bash_env_snippet(tmp, env, "echo in_tree=$(_executor_detach_in_tree; echo $?)")
        check("5b: a relative-argv daemon WITH cwd==install IS in-tree",
              "in_tree=0" in out, out[-120:])
        (tmp / "ps-map").write_text(
            "DEFAULT ppid 22\n"
            "21 ppid 1\n"
            "21 args /Applications/Utilities/Terminal.app\n"
            "21 comm Terminal\n"
            "22 ppid 21\n"
            "22 args -bash\n"
            "22 comm bash\n"
        )
        (tmp / "lsof-map").write_text("")
        out = bash_env_snippet(tmp, env, "echo in_tree=$(_executor_detach_in_tree; echo $?)")
        check("5c: a plain operator chain is NOT in-tree", "in_tree=1" in out, out[-120:])

    # ── 6. structural pins (source text — the wiring, not the behavior) ────
    promote_src = PROMOTE_SH.read_text()
    lib_src = LIB_SH.read_text()
    stop_src = STOP_SH.read_text()

    detach_line = next((i for i, l in enumerate(promote_src.splitlines())
                        if "_executor_detach_self \"$SCRIPT_DIR/promote.sh\"" in l), None)
    lock_line = next((i for i, l in enumerate(promote_src.splitlines())
                      if "if ! lock_acquire; then" in l), None)
    check("6: the detach block runs BEFORE any lock acquisition",
          detach_line is not None and lock_line is not None and detach_line < lock_line,
          f"detach={detach_line} lock={lock_line}")
    check("6a: the marker recursion guard is armed in promote.sh",
          "ENSEMBLE_PROMOTE_SELF_DETACHED" in promote_src)
    check("6b: Amendment #1 stated at the promote detach site (ONLY the executor detaches)",
          "AMENDMENT #1 UNTOUCHED" in promote_src)
    check("6c: Amendment #1 stated at the lib.sh detach machinery",
          "AMENDMENT #1 UNTOUCHED" in lib_src)
    check("6d: the 2026-11-14 incident (defect #7) is cited in lib.sh",
          "2026-11-14" in lib_src)
    check("6e: the 2026-09-28 cgroup mirror (VM guarantee) is cited in lib.sh",
          "2026-09-28-promote-cgroup-teardown" in lib_src)
    check("6f: _supervision_stop_launchd escalation filters self/group (Fix #5)",
          'for pid in $(_pipeline_self_excluded "$(_supervision_owned_pids '
          '2>/dev/null)")' in lib_src)
    check("6g: stop-ensemble.sh _stop_pids applies the self-exclusion guard",
          "_stop_self_excluded_in_tree" in stop_src and "$SELF_PID" in stop_src)
    check("6h: the ancestor walk is depth-bounded",
          re.search(r"depth.*-lt \d+", lib_src) is not None)

    print(f"\nPASS: {PASS}  FAIL: {FAIL}")
    if FAIL:
        print("failed:")
        for f in FAILED:
            print(f"  ✗ {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
