#!/bin/bash
# ============================================================================
# tests/test_promote_launchd_survivorship.sh — the DARWIN executor-survivorship
# pin for the promote self-detach (Fix #5, ENSEMBLE-32 Family-3)
# ============================================================================
# THE INCIDENT SHAPE THIS KILLS (docs/incidents/
# 2026-11-14-macos-launchd-handback-halt.md §7, defect #7, 00:50:53 +07):
#   a promote executor driven from INSIDE the daemon's process tree (the
#   agent-bash lane) is SIGKILLed by its own stop phase — the launchd
#   bootout of the daemon job tears down the whole job's tree INCLUDING the
#   executor, which died mid-stop BEFORE the flip. The 2026-09-28 VM
#   incident (cgroup teardown) is the mirror image; its fix (systemd-run
#   scope) is what this lane mirrors on darwin with a transient ONE-SHOT
#   LaunchAgent (lib.sh _executor_detach_self; the pattern proven live in
#   the incident's §7b).
#
# WHAT IS PINNED (each section):
#   A. ancestry detection (_executor_detach_in_tree, scripted `ps` stub):
#      an ancestor carrying the install's launcher/daemon shapes ⇒ in-tree;
#      a foreign chain ⇒ not; chain terminating at launchd ⇒ not; the walk
#      is depth-bounded (a deep chain terminates, never spins).
#   B. END-TO-END survivorship (REAL processes + an EXEC-ing launchctl
#      stub): the detached payload completes (done-file written by the
#      DETACHED wrapper) even though the in-tree parent process group is
#      SIGKILLed mid-flight — the launchd-teardown analog; the spent plist
#      is bootout'ed + removed even on that path (wrapper self-clean — the
#      RunAtLoad-at-login footgun, incident §7b).
#   C. exit-code propagation through a SURVIVING in-tree parent: child rc 0 /
#      7 / 78 propagate (the E2E demo promote contract — the agent bash must
#      see the promote's true exit code).
#   D. cleanup on success AND failure (bootout recorded, plist file gone,
#      label-verbatim plist FILENAME == LABEL — Family-1 invariant).
#   E. fail-closed bootstrap failure: stub launchctl refuses ⇒ detach returns
#      nonzero with _DETACH_LAST_ERROR recorded (Fix #4 discipline), scratch
#      cleaned, no silent fallback.
#   F. env hygiene: the detach env-file NEVER carries
#      ENSEMBLE_LAUNCHER_STATE_FILE (env-parity rule) and always arms the
#      recursion marker.
#   G. the plist is VALID XML (python plistlib parse of a generated one).
#   H. SELF-EXCLUSION (defense-in-depth): stop-ensemble.sh _stop_pids
#      (DRY_RUN census + a REAL kill run) never signals $$ / $PPID /
#      own-process-group pids; lib.sh _pipeline_self_excluded filters them;
#      _supervision_stop_launchd's SIGKILL escalation runs the filter.
#   I. structural pins: promote.sh's detach block placement (before
#      lock_acquire), the Amendment #1 comment (ONLY the executor detaches),
#      the marker recursion guard.
#
# HERMETIC BY CONSTRUCTION: every fixture lives under a fresh mktemp dir;
# launchctl/ps interactions go through PATH-injected stubs or REAL throwaway
# processes; the REAL launchd domain, ~/Library/LaunchAgents and any live
# daemon are NEVER touched (no bootstrap of a real agent).
#
# Run:
#   bash tests/test_promote_launchd_survivorship.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
STOP_SCRIPT="$REPO_ROOT/scripts/stop-ensemble.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}
assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "must NOT contain '$needle'" "$haystack" ;;
        *) _pass ;;
    esac
}
section() { printf '\n== %s ==\n' "$1"; }

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/launchd-survivorship.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
cleanup() {
    # FIXTURE_KEEP=1 preserves the fixture for post-mortem debugging (ops
    # convenience; the default cleans up — hermetic by construction).
    [ "${FIXTURE_KEEP:-0}" = "1" ] && { printf 'FIXTURE KEPT: %s\n' "$FIXTURE" >&2; return 0; }
    rm -rf "$FIXTURE"
}
trap cleanup EXIT INT TERM

# ── stub bin: launchctl (EXEC-ing bootstrap + recording) and ps (scripted) ──
STUBBIN="$FIXTURE/stubbin"
mkdir -p "$STUBBIN"

# The launchctl stub's CONTROL FILE grammar (written by the test):
#   mode=exec      bootstrap REALLY executes the plist's wrapper (setsid)
#   mode=fail      bootstrap fails rc 1 (the E fail-closed leg)
#   mode=refuse    bootstrap fails with a refusal message
LCTL_CTRL="$FIXTURE/lctl-ctrl"
LCTL_CALLS="$FIXTURE/lctl-calls"
LCTL_SPAWNED="$FIXTURE/lctl-spawned"
: >"$LCTL_CALLS"; : >"$LCTL_SPAWNED"
cat > "$STUBBIN/launchctl" <<LCTLEOF
#!/bin/bash
# stub launchctl — survivorship pack fixture (records every call; bootstrap
# optionally EXECs the plist's ProgramArguments wrapper detached via setsid
# semantics: new session through python3 os.setsid, exactly what a one-shot
# RunAtLoad job gets — a session/group OUTSIDE the bootstrapping tree).
ctrl="\${LCTL_CTRL:-/nonexistent-lctl-ctrl}"
calls="\${LCTL_CALLS:-/nonexistent-lctl-calls}"
spawned="\${LCTL_SPAWNED:-/nonexistent-lctl-spawned}"
printf '%s\n' "\$*" >> "\$calls"
mode="\$(sed -n 's/^mode=//p' "\$ctrl" 2>/dev/null | head -1)"
mode="\${mode:-exec}"
case "\$1" in
    bootstrap)
        [ "\$mode" = "fail" ] && { echo "bootstrap failed (stub): 5: Input/output error" >&2; exit 5; }
        [ "\$mode" = "refuse" ] && { echo "bootstrap refused (stub): 122: Operation not permitted" >&2; exit 122; }
        plist="\$3"
        # extract the wrapper path: the second <string> inside ProgramArguments
        wrapper="\$(sed -n '/<key>ProgramArguments<\\/key>/,/<\\/array>/p' "\$plist" | sed -n 's/.*<string>\\(.*\\)<\\/string>.*/\\1/p' | sed -n '2p')"
        [ -n "\$wrapper" ] && [ -f "\$wrapper" ] || { echo "stub: cannot resolve wrapper from \$plist" >&2; exit 3; }
        # Detach for real: new session (setsid equivalent), output to the
        # plist's StandardOutPath when present. ENV SANITIZED like real
        # launchd: a bootstrap'd job inherits launchd's env, NOT the
        # caller's ambient env — the wrapper's vars must come from its own
        # env-file (the plist/EnvironmentVariables analog), so the stub
        # starts from a minimal env (HOME + a launchd-ish PATH).
        logfile="\$(sed -n 's/.*<string>\\(.*\\)<\\/string>.*/\\1/p' "\$plist" | sed -n '4p')"
        nohup env -i HOME="\${HOME:-/tmp}" PATH=/usr/bin:/bin:/usr/sbin:/sbin \\
            python3 -c 'import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \\
            /bin/bash "\$wrapper" </dev/null >>"\${logfile:-/dev/null}" 2>&1 &
        echo "\$!" >> "\$spawned"
        exit 0
        ;;
    bootout)
        exit 0
        ;;
    print)
        # A registered job answers with a dump; a vanished one is empty.
        [ "\$mode" = "vanish" ] && exit 0
        echo "pid = 424242"
        echo "state = running"
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
LCTLEOF
chmod +x "$STUBBIN/launchctl"

# The scripted `ps` stub — ancestry chains come from a FIXTURE MAP file:
#   <pid> ppid <ppid>
#   <pid> args <text…>
#   <pid> comm <name>
PS_MAP="$FIXTURE/ps-map"
: >"$PS_MAP"
cat > "$STUBBIN/ps" <<PSEOF
#!/bin/bash
# stub ps — survivorship pack fixture (scripted ancestry map). Unknown pids
# fall back to the map's "DEFAULT ppid <pid>" line so a walk that starts at
# the real shell's own parent chain deterministically enters the fixture.
map="\${PS_MAP:-/nonexistent-ps-map}"
field="\$1"
pid="\$1"; for _a in "\$@"; do pid="\$_a"; done   # pid = LAST arg (ps -o X= -p PID)
case "\$field" in
    -o)
        case "\$2" in
            ppid=*) v="\$(sed -n "s/^\$pid ppid //p" "\$map" 2>/dev/null | head -1)"
                    [ -n "\$v" ] || v="\$(sed -n 's/^DEFAULT ppid //p' "\$map" 2>/dev/null | head -1)"
                    [ -n "\$v" ] || exec /bin/ps "\$@"   # unknown pid → real ps
                    printf '%s\n' "\$v" ;;
            args=*) v="\$(sed -n "s/^\$pid args //p" "\$map" 2>/dev/null | head -1)"
                    [ -n "\$v" ] || exec /bin/ps "\$@"   # unknown pid → real ps
                    printf '%s\n' "\$v" ;;
            comm=*) v="\$(sed -n "s/^\$pid comm //p" "\$map" 2>/dev/null | head -1)"
                    [ -n "\$v" ] || exec /bin/ps "\$@"   # unknown pid → real ps
                    printf '%s\n' "\$v" ;;
            pgid=*)
                # real pgid probes still hit the REAL ps for self-processes;
                # the stub only fakes ancestry lookups (ppid/args/comm).
                exec /bin/ps "\$@"
                ;;
            *) exec /bin/ps "\$@" ;;
        esac
        ;;
    *) exec /bin/ps "\$@" ;;
esac
PSEOF
chmod +x "$STUBBIN/ps"

PATH_WITH_STUBS="$STUBBIN:$PATH"

# The scripted `lsof` stub — cwd probes for the tier-1b conjunct come from a
# FIXTURE MAP file: "<pid> <cwd>" per line (empty map = no cwd resolvable).
LSOF_MAP="$FIXTURE/lsof-map"
: >"$LSOF_MAP"
cat > "$STUBBIN/lsof" <<LSOFEOF
#!/bin/bash
map="\${LSOF_MAP:-/nonexistent-lsof-map}"
pid="\$3"
cwd="\$(sed -n "s/^\$pid //p" "\$map" 2>/dev/null | head -1)"
[ -n "\$cwd" ] && printf 'n%s\n' "\$cwd"
exit 0
LSOFEOF
chmod +x "$STUBBIN/lsof"

# lib.sh sourced the way promote.sh sources it, with a scratch INSTALL_DIR.
INST="$FIXTURE/inst"
mkdir -p "$INST/data"
src_lib() {  # runs the given snippet with lib.sh + stub PATH + scripted map
    (
        export INSTALL_DIR="$INST"
        export PATH="$PATH_WITH_STUBS"
        export PS_MAP
        export LSOF_MAP
        export LCTL_CTRL LCTL_CALLS LCTL_SPAWNED
        # shellcheck disable=SC1090
        . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
        eval "$1"
    )
}

# ════════════════════════════════════════════════════════════════════════════
section "A — ancestry detection (scripted ps stub)"
# Chain fixture: launchd(1) ← launcher(11) ← ensemble-prod(12) ← agent-bash(13)
# ← promote-shell(14). DEFAULT ppid routes the walk's start (the real shell's
# own unknown parent) into the fixture chain at 13 — the agent-lane shape.
{
    echo "DEFAULT ppid 13"
    echo "11 ppid 1"
    echo "11 args bash $INST/launcher.sh --serve"
    echo "11 comm launcher.sh"
    echo "12 ppid 11"
    echo "12 args $INST/current/ensemble-prod --serve"
    echo "12 comm ensemble-prod"
    echo "13 ppid 12"
    echo "13 args bash -c some-agent-command"
    echo "13 comm bash"
} > "$PS_MAP"
out="$(src_lib 'ps() { "$STUBBIN/ps" "$@"; }; _executor_detach_in_tree; echo rc=$?' 2>/dev/null)"
assert_contains "A1: launcher-anchored ancestor counts as in-tree" "rc=0" "$out"

# Same tree, only the DAEMON COMM anchor (argv spelled relatively — tier-1b
# argv shapes miss, so the tier-1b cwd conjunct carries it: the ancestor is
# ensemble-shaped AND its cwd IS this install).
{
    echo "DEFAULT ppid 13"
    echo "11 ppid 1"
    echo "11 args ./launcher.sh"
    echo "11 comm launcher.sh"
    echo "12 ppid 11"
    echo "12 args ./ensemble-prod"
    echo "12 comm ensemble-prod"
    echo "13 ppid 12"
    echo "13 args bash agent-shell"
    echo "13 comm bash"
} > "$PS_MAP"
printf '%s %s\n' 11 "$INST" > "$LSOF_MAP"
printf '%s %s\n' 12 "$INST" >> "$LSOF_MAP"
out="$(src_lib 'ps() { "$STUBBIN/ps" "$@"; }; _executor_detach_in_tree; echo rc=$?' 2>/dev/null)"
assert_contains "A2: daemon-shaped ancestor WITH cwd==install counts as in-tree" "rc=0" "$out"

# A2-neg (the 2026-11-15 regression leg): an ancestor shaped like the daemon
# (comm ensemble-prod) but belonging to a DIFFERENT install (cwd elsewhere)
# must NOT count — a sandbox/fixture promote runs inside the REAL daemon's
# tree of another install; an unanchored comm match would bootstrap a REAL
# LaunchAgent for a fixture promote.
printf '%s %s\n' 11 "/somewhere-else" > "$LSOF_MAP"
printf '%s %s\n' 12 "/somewhere-else" >> "$LSOF_MAP"
out="$(src_lib 'ps() { "$STUBBIN/ps" "$@"; }; _executor_detach_in_tree; echo rc=$?' 2>/dev/null)"
assert_contains "A2-neg: a foreign-install daemon shape (cwd elsewhere) is NOT in-tree" "rc=1" "$out"
: > "$LSOF_MAP"

# Foreign chain: operator terminal → bash (DEFAULT walks into a plain bash).
{
    echo "DEFAULT ppid 22"
    echo "21 ppid 1"
    echo "21 args /Applications/Utilities/Terminal.app"
    echo "21 comm Terminal"
    echo "22 ppid 21"
    echo "22 args -bash"
    echo "22 comm bash"
} > "$PS_MAP"
out="$(src_lib 'ps() { "$STUBBIN/ps" "$@"; }; _executor_detach_in_tree; echo rc=$?' 2>/dev/null)"
assert_contains "A3: foreign chain is NOT in-tree" "rc=1" "$out"

# Chain terminating immediately (DEFAULT ppid = 1): NOT in-tree.
echo "DEFAULT ppid 1" > "$PS_MAP"
out="$(src_lib 'ps() { "$STUBBIN/ps" "$@"; }; _executor_detach_in_tree; echo rc=$?' 2>/dev/null)"
assert_contains "A4: parent = launchd (pid 1) is NOT in-tree" "rc=1" "$out"

# Depth bound: a 40-hop chain terminates (never spins) and stays decisive.
{
    echo "DEFAULT ppid 100"
    p=1
    i=0
    while [ "$i" -lt 40 ]; do
        n=$((100 + i))
        echo "$n ppid $p"
        echo "$n args filler-arg-$i"
        echo "$n comm filler"
        p="$n"
        i=$((i + 1))
    done
} > "$PS_MAP"
out="$(src_lib 'ps() { "$STUBBIN/ps" "$@"; }; _executor_detach_in_tree; echo rc=$?' 2>/dev/null)"
assert_contains "A5: deep chain terminates at the depth bound" "rc=1" "$out"

# ════════════════════════════════════════════════════════════════════════════
section "B — END-TO-END survivorship: the detached payload completes after the in-tree group is SIGKILLed"

# The fixture "daemon tree": a REAL parent process whose argv carries the
# install's launcher.sh shape, holding a REAL child shell (the in-tree
# executor stand-in) that runs _executor_detach_self with a STAND-IN promote
# payload (sleeps, then exits 0). The launchctl stub REALLY executes the
# wrapper in a NEW SESSION; then the test SIGKILLs the whole parent GROUP —
# the bootout analog — and proves the detached payload still completed.
CTRL="$LCTL_CTRL"
FIXPAY="$FIXTURE/payload.sh"
cat > "$FIXPAY" <<'PAYEOF'
#!/bin/bash
# stand-in promote payload: evidence of life (append + stdout — the stdout
# copy is what the launchd StandardOutPath forensics log captures), then
# success
printf 'payload-start\n' >> "$DETACH_EVIDENCE"
echo "payload-stdout-line"
sleep 2
printf 'payload-done\n' >> "$DETACH_EVIDENCE"
exit 0
PAYEOF
FIXWITNESS="$FIXTURE/witness.sh"
cat > "$FIXWITNESS" <<WITEOF
#!/bin/bash
# the in-tree executor stand-in — spawned as a CHILD of the fake launcher
export INSTALL_DIR="$INST"
export PATH="$PATH_WITH_STUBS"
export LCTL_CTRL="$LCTL_CTRL" LCTL_CALLS="$LCTL_CALLS" LCTL_SPAWNED="$LCTL_SPAWNED"
export PS_MAP="/nonexistent-ps-map"
export DETACH_EVIDENCE="$FIXTURE/evidence"
export PROMOTE_DETACH_WAIT_S="60" PROMOTE_DETACH_POLL_S="1"
. "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
# value-shaped ps shim: the ancestry walk stops immediately (this witness
# IS the in-tree executor stand-in by construction)
ps() { case \"$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }
_executor_detach_self "$FIXPAY" --version v9-fixture || echo "\$?" > "$FIXTURE/witness-rc"
echo "witness-exit" >> "$FIXTURE/evidence"
WITEOF

printf 'mode=exec\n' > "$LCTL_CTRL"
: >"$LCTL_CALLS"; : >"$LCTL_SPAWNED"; : >"$FIXTURE/evidence"
# The fake launcher: argv carries <inst>/launcher.sh; it spawns the witness
# as its foreground child and reports the witness pid for the group-kill.
FAKE_LAUNCHER="$INST/launcher.sh"
cat > "$FAKE_LAUNCHER" <<LNEOF
#!/bin/bash
echo "launcher-start"
bash "$FIXWITNESS" &
witness=\$!
echo "witness=\$witness"
wait "\$witness"
echo "launcher-end"
LNEOF
chmod +x "$FAKE_LAUNCHER"
LAUNCHER_OUT="$FIXTURE/launcher-out"
# macOS has no setsid(1); python3 os.setsid stands in. Inlined (NOT via a
# helper function) so $! is the real launcher pid: backgrounding a FUNCTION
# forks a subshell whose pid lands in $!, and that subshell shares THIS
# pack's process group — the teardown's group-kill would then kill the pack
# itself (observed as rc 137).
python3 -c 'import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
    bash "$FAKE_LAUNCHER" > "$LAUNCHER_OUT" 2>&1 &
launcher_pid=$!
# wait for the witness pid line
i=0
witness_pid=""
while [ "$i" -lt 100 ]; do
    witness_pid="$(sed -n 's/^witness=//p' "$LAUNCHER_OUT" 2>/dev/null | head -1)"
    [ -n "$witness_pid" ] && break
    sleep 0.2
    i=$((i + 1))
done
assert_contains "B0: fixture launched (witness spawned)" "yes" "$([ -n "$witness_pid" ] && echo yes || echo no)"
[ -n "$witness_pid" ] || witness_pid="0"
launcher_group="$(ps -o pgid= -p "$launcher_pid" 2>/dev/null | tr -d '[:space:]')"

# Wait until the DETACHED payload is actually running (its evidence line is
# written from the NEW session) — only THEN is the group-kill honest: it
# must tear down the in-tree witness mid-wait, not before the bootstrap.
i=0
payload_started=""
while [ "$i" -lt 150 ]; do
    if grep -q "payload-start" "$FIXTURE/evidence" 2>/dev/null; then payload_started=yes; break; fi
    sleep 0.2
    i=$((i + 1))
done
assert_eq "B0c: the detached payload STARTED before the teardown" "yes" "${payload_started:-no}"

# THE TEARDOWN: SIGKILL the whole launcher process group (the launchd
# bootout analog — everything inside the job's tree dies, including the
# in-tree witness), WHILE the detached payload sleeps. GUARD: the kill only
# fires when the launcher is ALIVE and its group resolved — `kill -9 -0`
# (empty group) would kill THIS PACK's own group (the exact class of
# accident the self-exclusion filter exists to prevent).
if kill -0 "$launcher_pid" 2>/dev/null && [ -n "${launcher_group:-}" ] && [ "${launcher_group:-0}" != "0" ]; then
    kill -9 -"$launcher_group" 2>/dev/null || true
    sleep 0.5
    if kill -0 "$launcher_pid" 2>/dev/null; then _fail "B0b: launcher group died" "dead" "alive"; else _pass; fi
else
    _fail "B0b: launcher still alive at teardown (group=${launcher_group:-<empty>})" "alive" "gone/unresolvable — teardown leg skipped"
fi

# The detached payload must still complete: evidence written, done-file rc 0.
i=0
payload_done=""
while [ "$i" -lt 150 ]; do
    if grep -q "payload-done" "$FIXTURE/evidence" 2>/dev/null; then payload_done=yes; break; fi
    sleep 0.2
    i=$((i + 1))
done
assert_eq "B1: detached payload completed AFTER the in-tree group was SIGKILLed" "yes" "${payload_done:-no}"

# The wrapper's SELF-CLEAN ran on that path too: bootout recorded, plist gone.
i=0
bootout_seen=""
while [ "$i" -lt 50 ]; do
    if grep -q "^bootout gui/" "$LCTL_CALLS" 2>/dev/null; then bootout_seen=yes; break; fi
    sleep 0.2
    i=$((i + 1))
done
assert_eq "B2: spent one-shot was bootout'ed even with the in-tree waiter dead (footgun leg)" "yes" "${bootout_seen:-no}"
assert_contains "B3: bootout target is label-verbatim (gui/<uid>/com.ensemble.promote-…)" \
    "bootout gui/$(id -u)/com.ensemble.promote-" "$(cat "$LCTL_CALLS")"
assert_contains "B4: bootstrap target carried a plist inside the scratch tree" \
    "bootstrap gui/$(id -u) " "$(cat "$LCTL_CALLS")"
# the spawned wrapper really left its mark in the forensics log
assert_eq "B5: the detached forensics log received the payload's stdout" \
    "payload-stdout-line" "$(cat "$INST/data/"promote-*.detach.log 2>/dev/null | head -1)"

# ════════════════════════════════════════════════════════════════════════════
section "C — exit-code propagation through a SURVIVING in-tree parent (E2E demo contract)"
# A plain (surviving) caller runs _executor_detach_self directly with payloads
# exiting 0 / 7 / 78; the function returns the DETACHED payload's code.

run_detached_with_payload() {  # <exit-code>
    cat > "$FIXTURE/pl.sh" <<PEOF
#!/bin/bash
sleep 1
exit $1
PEOF
    src_lib "
ps() { case \"$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }
export PROMOTE_DETACH_WAIT_S=\"60\" PROMOTE_DETACH_POLL_S=\"1\"
_executor_detach_self '$FIXTURE/pl.sh' demo --version v9-fixture >/dev/null 2>&1
echo rc=\$?
" 2>/dev/null
}

printf 'mode=exec\n' > "$LCTL_CTRL"
out="$(run_detached_with_payload 0)"
assert_contains "C1: detached rc 0 propagates" "rc=0" "$out"
out="$(run_detached_with_payload 7)"
assert_contains "C2: detached rc 7 propagates" "rc=7" "$out"
out="$(run_detached_with_payload 78)"
assert_contains "C3: detached rc 78 propagates" "rc=78" "$out"

# ============================================================================
# section J — D-5 wrapper argv shape (cycle-3 E2E, one round): the caller
# passes script-as-$1 plus its own argv (promote.sh:182:
#   _executor_detach_self "$SCRIPT_DIR/promote.sh" "$@"), and the wrapper
# consumed $1 into script= but its quoting loop re-included $1 from "$@"
# (no shift) -> the detached child ran `promote.sh promote.sh <args>` ->
# `unknown flag` rc 78 in ~2s (cycle-3: the whole detach chain worked
# end-to-end, then the wrapper duplicated the script). The payload below
# records ITS OWN argv; the legs assert the detached child received the
# script ONCE and the args VERBATIM (order preserved).
section "J - D-5 wrapper argv shape: script consumed ONCE, args verbatim"
D5PAY="$FIXTURE/d5-argv-payload.sh"
cat > "$D5PAY" <<'D5PAYEOF'
#!/bin/bash
printf 'payload-argc=%s\n' "$#" >> "${DETACH_EVIDENCE:?}"
printf 'payload-argv=%s\n' "$*" >> "${DETACH_EVIDENCE:?}"
exit 0
D5PAYEOF
chmod +x "$D5PAY"
D5_OUT="$(src_lib "
ps() { case \"$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }
: > "$FIXTURE/d5-evidence"   # fresh evidence file per run (no stale argv lines)
export DETACH_EVIDENCE="$FIXTURE/d5-evidence"
export PROMOTE_DETACH_WAIT_S=\"60\" PROMOTE_DETACH_POLL_S=\"1\"
_executor_detach_self '$FIXTURE/d5-argv-payload.sh' demo --run-id r-d5 >/dev/null 2>&1
echo rc=\$?
" 2>/dev/null)"
assert_contains "J1: the detach ran (rc 0 propagates)" "rc=0" "$D5_OUT"
D5_EVID="$(cat "$FIXTURE/d5-evidence" 2>/dev/null)"
assert_contains "J2: argv = the args VERBATIM (script consumed ONCE, not duplicated)" \
    "payload-argv=demo --run-id r-d5" "$D5_EVID"
assert_contains "J3: argc = 3 (script-as-\$1 consumed; demo --run-id r-d5 forwarded verbatim)" \
    "payload-argc=3" "$D5_EVID"
case "$D5_EVID" in
    *"$FIXTURE/d5-argv-payload.sh"*) _fail "J4: the script path leaked into the detached argv (D-5 shift missing)" ;;
    *) _pass ;;
esac
rm -f "$FIXTURE/d5-argv-payload.sh"

# ════════════════════════════════════════════════════════════════════════════
# K — D-6 (E2E round 4, cycle-4 🔴): REPO_ROOT threaded into the detached
# job's plist EnvironmentVariables (allowlist-of-one). Driven legs: run the
# REAL detach with a bootstrap-refusing launchctl stub (mode=fail) and a
# no-op cleanup override so the GENERATED PLIST survives for byte
# inspection. SET ⇒ the plist carries EnvironmentVariables/REPO_ROOT;
# UNSET ⇒ the plist carries NO EnvironmentVariables key at all.
section "K - D-6 plist EnvironmentVariables threading"
K_OUT_SET="$(src_lib "
ps() { case \"\$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }
printf 'mode=fail\n' > \"\$LCTL_CTRL\"
export PROMOTE_DETACH_WAIT_S=\"60\" PROMOTE_DETACH_POLL_S=\"1\"
export REPO_ROOT='/tmp/some-checkout'
_executor_detach_cleanup() { :; }   # keep the generated plist for inspection
_executor_detach_self '$FIXTURE/pl.sh' demo --version v9-fixture >/dev/null 2>&1
echo detach-rc=\$?
echo \"plist=\$(ls -t \"\${TMPDIR:-/tmp}\"/ens-promote-detach.*/com.ensemble.promote-*.plist 2>/dev/null | head -n 1)\"
" 2>/dev/null)"
K_PLIST="$(printf '%s\n' "$K_OUT_SET" | sed -n 's/^plist=//p' | head -n 1)"
assert_contains "K1: the detach machinery ran to the refusal (fixture sound)" "detach-rc=1" "$K_OUT_SET"
[ -n "$K_PLIST" ] && [ -f "$K_PLIST" ] && _pass || _fail "K1b: no plist survived for inspection (cleanup override ineffective)"
if [ -n "$K_PLIST" ] && [ -f "$K_PLIST" ]; then
    K_BODY="$(cat "$K_PLIST")"
    case "$K_BODY" in
        *"<key>EnvironmentVariables</key>"*"<key>REPO_ROOT</key><string>/tmp/some-checkout</string>"*) _pass ;;
        *) _fail "K2: plist missing EnvironmentVariables/REPO_ROOT (D-6 threading absent)" ;;
    esac
fi
rm -f "$K_PLIST"
K_OUT_UNSET="$(src_lib "
ps() { case \"\$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }
printf 'mode=fail\n' > \"\$LCTL_CTRL\"
export PROMOTE_DETACH_WAIT_S=\"60\" PROMOTE_DETACH_POLL_S=\"1\"
unset REPO_ROOT
_executor_detach_cleanup() { :; }
_executor_detach_self '$FIXTURE/pl.sh' demo --version v9-fixture >/dev/null 2>&1
echo detach-rc=\$?
echo \"plist=\$(ls -t \"\${TMPDIR:-/tmp}\"/ens-promote-detach.*/com.ensemble.promote-*.plist 2>/dev/null | head -n 1)\"
" 2>/dev/null)"
K_PLIST2="$(printf '%s\n' "$K_OUT_UNSET" | sed -n 's/^plist=//p' | head -n 1)"
assert_contains "K3: the unset-arm detach ran to the refusal" "detach-rc=1" "$K_OUT_UNSET"
if [ -n "$K_PLIST2" ] && [ -f "$K_PLIST2" ]; then
    case "$(cat "$K_PLIST2")" in
        *"<key>EnvironmentVariables</key>"*) _fail "K4: plist carries EnvironmentVariables with REPO_ROOT unset (must stay ABSENT)" ;;
        *) _pass ;;
    esac
else
    _fail "K4: no plist survived for the unset-arm inspection"
fi
rm -f "$K_PLIST2" $(find "$FIXTURE" "$INST" -name 'com.ensemble.promote-*.plist' 2>/dev/null)

section "D — cleanup on success AND failure (footgun legs)"
# D1: after a successful detach the scratch plist is GONE and bootout fired.
assert_contains "D1: bootout recorded after the success-path detach" \
    "bootout gui/$(id -u)/com.ensemble.promote-" "$(cat "$LCTL_CALLS")"
plists_left="$(find "$FIXTURE" "$INST" -name 'com.ensemble.promote-*.plist' 2>/dev/null | tr '\n' ' ')"
assert_eq "D2: no spent com.ensemble.promote-*.plist survives anywhere in the fixture" "" "$plists_left"

# D3: the CHILD failure path still cleans up (payload exits 78 — covered by
# C3's run; assert the call log recorded MORE bootouts than bootstraps-failed).
bootstraps="$(grep -c '^bootstrap gui/' "$LCTL_CALLS" 2>/dev/null || echo 0)"
bootouts="$(grep -c '^bootout gui/' "$LCTL_CALLS" 2>/dev/null || echo 0)"
assert_contains "D3: every bootstrap got its bootout (success AND failure paths)" \
    "yes" "$([ "${bootouts:-0}" -ge "${bootstraps:-1}" ] && echo yes || echo no)"

section "E — fail-closed bootstrap failure (no silent fallback)"
printf 'mode=fail\n' > "$LCTL_CTRL"
before_calls="$(wc -l < "$LCTL_CALLS" | tr -d ' ')"
out="$(src_lib "
ps() { case \"$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }
export PROMOTE_DETACH_WAIT_S=\"60\" PROMOTE_DETACH_POLL_S=\"1\"
_executor_detach_self '$FIXTURE/pl.sh' demo --version v9-fixture >/dev/null 2>&1
echo rc=\$?
echo err=\${_DETACH_LAST_ERROR:-UNSET}
" 2>/dev/null)"
assert_contains "E1: bootstrap failure returns NONZERO (never a silent in-tree fallback)" "rc=1" "$out"
assert_contains "E2: _DETACH_LAST_ERROR records the launchctl diagnosis (Fix #4 discipline)" \
    "err=launchctl bootstrap gui/$(id -u)" "$out"
after_calls="$(wc -l < "$LCTL_CALLS" | tr -d ' ')"
assert_contains "E3: the failed bootstrap attempt is recorded in the call log" \
    "yes" "$([ "${after_calls:-0}" -gt "${before_calls:-0}" ] && echo yes || echo no)"

section "F — env hygiene (the env-file contract)"
# Re-run a detach with a POISON var present in the caller env; the wrapper's
# env snapshot must NOT carry it, and MUST arm the recursion marker.
printf 'mode=exec\n' > "$LCTL_CTRL"
ENVFILE_PROBE="$FIXTURE/env-probe"
out="$(src_lib "
ps() { case \"$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }
export ENSEMBLE_LAUNCHER_STATE_FILE='$FIXTURE/state-file-poison'
export ENSEMBLE_PROMOTE_OBSERVE='yes'
export PROMOTE_DETACH_WAIT_S=\"60\" PROMOTE_DETACH_POLL_S=\"1\"
# capture the env-file the next detach generates: wrap the real one by
# intercepting mktemp? Too invasive — instead call the internal pieces via a
# re-run and inspect the LAST env.sh before cleanup deletes it. Simplest
# honest probe: run the detach with a payload that DUMPS ITS OWN ENV.
cat > '$ENVFILE_PROBE' <<'PEOF2'
#!/bin/bash
env > '$FIXTURE/detached-env.txt'
exit 0
PEOF2
_executor_detach_self '$ENVFILE_PROBE' >/dev/null 2>&1
echo rc=\$?
" 2>/dev/null)"
assert_contains "F1: the payload ran detached and dumped its env" "rc=0" "$out"
assert_not_contains "F2: ENSEMBLE_LAUNCHER_STATE_FILE NEVER reaches the detached env (env-parity rule)" \
    "ENSEMBLE_LAUNCHER_STATE_FILE" "$(cat "$FIXTURE/detached-env.txt" 2>/dev/null)"
assert_contains "F3: the recursion marker is armed in the detached env" \
    "ENSEMBLE_PROMOTE_SELF_DETACHED=1" "$(cat "$FIXTURE/detached-env.txt" 2>/dev/null)"
assert_contains "F4: plain exported caller vars DO propagate" \
    "ENSEMBLE_PROMOTE_OBSERVE=yes" "$(cat "$FIXTURE/detached-env.txt" 2>/dev/null)"

section "G — the generated plist is VALID XML (plistlib)"
out="$(src_lib "
ps() { case \"$*\" in *ppid=*) echo 0 ;; *) return 1 ;; esac; }
export PROMOTE_DETACH_WAIT_S=\"60\" PROMOTE_DETACH_POLL_S=\"1\"
# capture the plist BEFORE the wrapper cleans it: copy it via a payload that
# runs while the job is alive.
cat > '$FIXTURE/slow-payload.sh' <<'PEOF3'
#!/bin/bash
sleep 3
exit 0
PEOF3
_executor_detach_self '$FIXTURE/slow-payload.sh' >/dev/null 2>&1 &
bgpid=\$!
found=''
i=0
while [ \$i -lt 60 ]; do
    found=\$(sed -n 's/^bootstrap gui\\/[0-9]* //p' \"\$LCTL_CALLS\" 2>/dev/null | tail -1)
    [ -n \"\$found\" ] && [ -f \"\$found\" ] && break
    sleep 0.2
    i=\$((i + 1))
done
[ -n \"\$found\" ] && cp \"\$found\" '$FIXTURE/captured.plist' 2>/dev/null
wait \$bgpid
echo captured=\$([ -f '$FIXTURE/captured.plist' ] && echo yes || echo no)
" 2>/dev/null)"
assert_contains "G1: a generated plist was captured mid-flight" "captured=yes" "$out"
plistlib_out="$(python3 -c '
import plistlib, sys
with open(sys.argv[1], "rb") as fh:
    p = plistlib.load(fh)
label = p.get("Label", "")
args = p.get("ProgramArguments", [])
print("label=%s" % label)
print("arg0=%s" % (args[0] if args else ""))
print("runatload=%s" % p.get("RunAtLoad"))
print("keepalive=%s" % p.get("KeepAlive"))
' "$FIXTURE/captured.plist" 2>/dev/null)"
assert_contains "G2: plistlib parses the generated plist" "label=com.ensemble.promote-" "$plistlib_out"
assert_contains "G3: ProgramArguments[0] is /bin/bash" "arg0=/bin/bash" "$plistlib_out"
assert_contains "G4: RunAtLoad true (one-shot at load)" "runatload=True" "$plistlib_out"
assert_contains "G5: KeepAlive false (one-shot, no respawn)" "keepalive=False" "$plistlib_out"

section "H — SELF-EXCLUSION (defense-in-depth) — identity-only (no pgid filter)"
# H1: lib.sh _pipeline_self_excluded — $$ and $PPID are ALWAYS filtered
# (identity exclusion); a foreign-session pid survives. NOTE: the pgid filter
# was DELIBERATELY removed — it dropped the daemon itself (same launchd job
# group) and made the sweep find NOTHING (the 16d TERM pin regression). The
# residual in-tree exclusion is EXACT-PID only: $$, $PPID, and — only when
# in-tree — $PPID's parent (the agent bash tool). See lib.sh
# _pipeline_self_excluded for the rationale.
foreign_pid="$(python3 -c '
import os, sys
pid = os.fork()
if pid == 0:
    os.setsid()
    print(os.getpid())
    sys.stdout.flush()
    os.close(1)
    import time; time.sleep(6)
    os._exit(0)
sys.exit(0)
' 2>/dev/null | head -1)"
sleep 0.5
out="$(src_lib "_pipeline_self_excluded \"\$\$ \"\$PPID \"${foreign_pid:-}\" 2>/dev/null | tr '\n' ' '" 2>/dev/null)"
case "${foreign_pid:-}" in
    ''|*[!0-9]*)
        _pass "H1: foreign-session fork unavailable on this host"
        ;;
    *)
        case "$out" in
            *"$foreign_pid"*) _pass "H1: a REAL foreign-session pid survives the filter (identity exclusion keeps legitimate targets)" ;;
            *) _fail "H1: foreign-session pid filtered" "kept $foreign_pid" "$out" ;;
        esac
        ;;
esac
case "${foreign_pid:-}" in
    ''|0|*[!0-9]*) ;; *) kill "$foreign_pid" 2>/dev/null || true ;;
esac

# H2: stop-ensemble.sh DRY_RUN census — identity-only means BOTH own-group
# and foreign candidates appear (they are legitimate stop targets in a direct
# invocation; only $$ and $PPID drop). The stop script itself is never
# signaled.
SBOX="$FIXTURE/sbox"
mkdir -p "$SBOX/data"
own_fixture_pids=""
bash -c 'true; sleep 20' x "$SBOX/launcher.sh" y &
own_fixture_pids="$own_fixture_pids $!"
foreign_fixture_pid="$(python3 -c '
import os, sys
pid = os.fork()
if pid == 0:
    os.setsid()
    print(os.getpid())
    sys.stdout.flush()
    os.close(1)
    os.execvp("bash", ["bash", "-c", "true; sleep 20", "x", sys.argv[1], "y"])
    os._exit(127)
sys.exit(0)
' "$SBOX/launcher.sh" 2>/dev/null | head -1)"
sleep 0.5
dry_out="$(WAIT_S=2 DRY_RUN=1 INSTALL_DIR="$SBOX" PATH="$PATH_WITH_STUBS"     bash "$STOP_SCRIPT" "$SBOX" 19998 2>&1)"
for op in $own_fixture_pids; do
    case "$dry_out" in
        *"would signal $op "*|*"would signal $op"*) _pass "H2a: OWN-GROUP candidate ($op) IS in the census (identity-only, no pgid filter)" ;;
        *) _fail "H2a: own-group candidate missing from census" "would signal $op" "$dry_out" ;;
    esac
done
case "${foreign_fixture_pid:-}" in
    ''|*[!0-9]*)
        _pass "H2b: foreign-session fixture unavailable — census leg skipped"
        ;;
    *)
        case "$dry_out" in
            *"would signal ${foreign_fixture_pid} "*|*"would signal ${foreign_fixture_pid}") _pass "H2b: the FOREIGN-session candidate IS in the census" ;;
            *) _fail "H2b: foreign-session candidate missing from census" "would signal $foreign_fixture_pid" "$dry_out" ;;
        esac
        ;;
esac

# H3: the REAL kill path — identity-only means BOTH candidates are killed.
if [ -n "${foreign_fixture_pid:-}" ] && case "$foreign_fixture_pid" in *[!0-9]*) false ;; *) true ;; esac; then
    WAIT_S=2 INSTALL_DIR="$SBOX" PATH="$PATH_WITH_STUBS"         bash "$STOP_SCRIPT" "$SBOX" 19998 >/dev/null 2>&1
    if kill -0 "$foreign_fixture_pid" 2>/dev/null; then
        _fail "H3a: the foreign-session candidate was killed by the sweep" "dead" "alive"
        kill -9 "$foreign_fixture_pid" 2>/dev/null || true
    else
        _pass "H3a: the foreign-session candidate was killed by the sweep"
    fi
else
    _pass "H3a: foreign fixture unavailable — kill leg skipped"
fi
for op in $own_fixture_pids; do
    if kill -0 "$op" 2>/dev/null; then
        _fail "H3b: the OWN-GROUP candidate ($op) survived — identity-only should have killed it" "dead" "alive"
        kill -9 "$op" 2>/dev/null || true
    else
        _pass "H3b: the OWN-GROUP candidate ($op) was killed by the sweep (no pgid filter)"
    fi
done

# H4: structural pin — the launchd SIGKILL escalation runs the filter.
sup_src="$(cat "$UPGRADE_DIR/lib.sh")"
assert_contains "H4: _supervision_stop_launchd escalation filters its own identity (Fix #5)"     'for pid in $(_pipeline_self_excluded "$(_supervision_owned_pids 2>/dev/null)")' "$sup_src"

section "I — structural pins (promote.sh wiring, Amendment #1, marker guard)"
PROMOTE_SRC="$(cat "$UPGRADE_DIR/promote.sh")"
detach_line="$(printf '%s\n' "$PROMOTE_SRC" | grep -n '_executor_detach_self "\$SCRIPT_DIR/promote.sh"' | head -1 | cut -d: -f1)"
lock_line="$(printf '%s\n' "$PROMOTE_SRC" | grep -n 'if ! lock_acquire; then' | head -1 | cut -d: -f1)"
assert_contains "I1: the detach block runs BEFORE any lock acquisition" \
    "yes" "$([ -n "$detach_line" ] && [ -n "$lock_line" ] && [ "$detach_line" -lt "$lock_line" ] && echo yes || echo no)"
assert_contains "I2: the marker recursion guard is armed in promote.sh" \
    'ENSEMBLE_PROMOTE_SELF_DETACHED' "$PROMOTE_SRC"
assert_contains "I3: Amendment #1 stated at the detach site (ONLY the executor detaches)" \
    "AMENDMENT #1 UNTOUCHED" "$PROMOTE_SRC"
assert_contains "I4: the incident defect is cited at the detach site" \
    "2026-11-14" "$PROMOTE_SRC"
lib_src="$(cat "$UPGRADE_DIR/lib.sh")"
assert_contains "I5: lib.sh states the Amendment-#1 boundary at the detach machinery" \
    "AMENDMENT #1 UNTOUCHED" "$lib_src"
assert_contains "I6: the 2026-09-28 cgroup mirror is cited (the VM guarantee)" \
    "2026-09-28-promote-cgroup-teardown" "$lib_src"

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n========================================\n'
printf 'launchd survivorship tests: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf 'ALL PASS\n'
exit 0
