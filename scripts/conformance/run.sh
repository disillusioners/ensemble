#!/usr/bin/env bash
# The remote-hands CONFORMANCE KIT entry point (SPEC §10 area runner).
#
#   ./scripts/conformance/run.sh                                # self-consistency (in-kit center)
#   CONFORMANCE_CENTER_URL=wss://host:port/api/mcps-gateway/ws \
#   CONFORMANCE_TOKEN=... CONFORMANCE_CA_PEM=/path/ca.pem \
#   CONFORMANCE_MACHINE_ID=... CONFORMANCE_EXPECTED_AGENT=... \
#     ./scripts/conformance/run.sh                              # pointed at YOUR center
#
# The five pointing keys (+ the two begin-hook keys and the strict knob —
# eight in total, pinned by the kit) are documented in README.md.
#
# WHY --confcutdir=tests/conformance IS LOAD-BEARING: the repo-root
# conftest installs an mcp-SDK ``sys.modules`` mock and an autouse
# ``State`` patch.  The kit must not inherit the daemon lane's
# scaffolding — with the cutdir, none of the root conftest is collected.
#
# WHY NO GNU `timeout(1)` (QA B8): it is ABSENT on stock macOS/BSD —
# the kit used to die with exit 127 on the maintainers' own platform,
# reading as "tests failed".  The bounded run below is the repo's
# `_run_bounded` semantics (see `scripts/upgrade/lib.sh`): background +
# sleep + TERM + grace + KILL watcher, portable over bash 3.2…5.x, with
# GNU `timeout`'s RETURN-CODE CONVENTION preserved — 0 = clean,
# non-zero = the child's own rc, 124 = the budget fired (TERM/KILL
# collapsed per GNU: 137/143 → 124 — and ONLY when the watcher
# actually fired, so a child's own 137/143 death is not masked).
#
# Teardown: the kit is IN-PROCESS (the self-host center lives inside the
# pytest process), so the trap kills the pytest process (and, when the
# operator's begin hook spawns an external center, its process group) on
# TERM/INT/EXIT.  Nothing is left listening.  The trap is installed
# BEFORE the launch so a TERM arriving in the launch window cannot leak
# the child; the old post-hoc lsof listener probe is GONE — it was
# unreachable (it ran after `wait` had reaped the pid) and it targeted
# the wrapper pid rather than any listener.
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1

RUN_TIMEOUT="${CONFORMANCE_RUN_TIMEOUT:-600}"

# QA F2: a malformed knob (e.g. ``10m``) used to reach the watcher's
# ``sleep``, fail on BSD sleep (stderr swallowed), and the watcher —
# which has no ``set -e`` — fell straight through to the KILL path: the
# kit died at elapsed=0s wearing 124 ("budget fired") with NO
# diagnostic.  Validate ONCE, here, before any child is launched; 125
# is GNU timeout's own "bad operand" convention.
if [[ ! "$RUN_TIMEOUT" =~ ^[0-9]+$ ]]; then
  echo "conformance kit: CONFORMANCE_RUN_TIMEOUT must be an integer number of seconds, got: $RUN_TIMEOUT" >&2
  exit 125
fi

if [[ -n "${CONFORMANCE_CENTER_URL:-}" ]]; then
  echo "conformance kit: POINTED at ${CONFORMANCE_CENTER_URL}"
  if [[ -z "${CONFORMANCE_BEGIN_CMD:-}" && -z "${CONFORMANCE_BEGIN_URL:-}" ]]; then
    echo "conformance kit: NO begin hook — the session-dependent areas will FAIL FAST (B8a)."
    echo "                 Machine-facing areas run normally. See README.md 'Begin hook'."
  fi
else
  echo "conformance kit: SELF-CONSISTENCY MODE — no CONFORMANCE_CENTER_URL set."
  echo "                 The kit boots its own IN-KIT reference center and tests ITSELF."
  echo "                 This proves the kit, NOT your center. Point it with CONFORMANCE_CENTER_URL."
fi

# The house invocation: uv run python -m pytest (bare pytest is broken in this repo).
ARGS=(tests/conformance -p no:cacheprovider --confcutdir=tests/conformance -m conformance)

# ── the bounded run (portable; NO GNU timeout dependency) ──────────────
# Watcher: sleep the budget, stamp the timed-out flag, TERM the child,
# grace 5 s, KILL.  The watcher is killed on BOTH paths — the normal
# exit reaps it EXPLICITLY below before the traps are cleared, and the
# trap path kills it via ``cleanup``.  (The comment here used to claim
# the child finishing first left a harmless one-syscall kill behind —
# FALSE: without the explicit reap the watcher survived EVERY
# successful run as an orphan (PPID=1), slept the whole budget, then
# fired TERM/KILL at an already-reaped pid — a PID-recycle hazard plus
# a stray process per run.  QA F1.)
#
# The watcher is TWO processes: the subshell AND its budget ``sleep``
# child.  Killing the subshell alone orphans the sleep (a bare `kill`
# never reaches a grandchild), so the subshell PUBLISHES the sleep's
# pid to a file and the reaper kills BOTH pids — no stray `sleep`
# survives a successful run either.
pytest_pid=""
watcher_pid=""
# The OOM-masking guard: ONLY a watcher-fired death may wear 124.  The
# watcher stamps this flag BEFORE its first kill, so a child that dies
# 137/143 for its own reasons (an OOM kill, a pytest bug) keeps its own
# exit code.  A per-pid path — no mktemp dependency, bash 3.2-safe.
timedout_flag="${TMPDIR:-/tmp}/conformance-run.$$.timedout"
sleep_pid_file="${TMPDIR:-/tmp}/conformance-run.$$.sleep"
rm -f "${timedout_flag}" "${sleep_pid_file}" 2>/dev/null || true

# Reap the watcher tree: subshell first (so it cannot proceed to its
# kill stage), then its published budget-sleep child, then wait.
kill_watcher() {
  local sleep_pid=""
  if [[ -n "${watcher_pid}" ]]; then
    kill -KILL "${watcher_pid}" 2>/dev/null || true
  fi
  if [[ -s "${sleep_pid_file}" ]]; then
    sleep_pid="$(cat "${sleep_pid_file}" 2>/dev/null || true)"
    if [[ -n "${sleep_pid}" ]]; then
      kill -KILL "${sleep_pid}" 2>/dev/null || true
    fi
  fi
  if [[ -n "${watcher_pid}" ]]; then
    wait "${watcher_pid}" 2>/dev/null || true
  fi
  rm -f "${timedout_flag}" "${sleep_pid_file}" 2>/dev/null || true
  watcher_pid=""
}

cleanup() {
  local rc=$?
  kill_watcher
  if [[ -n "${pytest_pid}" ]]; then
    # Kill the whole pytest process group when we own it (the begin
    # hook's spawned center, if any, lives under it); fall back to the
    # pid itself when the group kill finds no such process group.
    kill -- "-${pytest_pid}" 2>/dev/null || kill "${pytest_pid}" 2>/dev/null || true
    wait "${pytest_pid}" 2>/dev/null || true
  fi
  exit "${rc}"
}
trap cleanup INT TERM EXIT

uv run python -m pytest "${ARGS[@]}" "$@" &
pytest_pid=$!

(
  sleep "$RUN_TIMEOUT" 2>/dev/null &
  echo $! > "${sleep_pid_file}" 2>/dev/null
  wait "$!" 2>/dev/null
  rm -f "${sleep_pid_file}" 2>/dev/null
  : > "${timedout_flag}" 2>/dev/null
  kill -TERM "${pytest_pid}" 2>/dev/null
  # The grace wait is ALSO a killable child: publish its pid so the
  # reaper can reap it when the child died inside the grace window
  # (otherwise the 5 s sleep is orphaned on the 124 path).
  sleep 5 2>/dev/null &
  echo $! > "${sleep_pid_file}" 2>/dev/null
  wait "$!" 2>/dev/null
  rm -f "${sleep_pid_file}" 2>/dev/null
  kill -KILL "${pytest_pid}" 2>/dev/null
) >/dev/null 2>&1 &
watcher_pid=$!

wait "${pytest_pid}"
rc=$?
# GNU `timeout` convention: TERM (143) / KILL (137) from the WATCHDOG
# collapse to 124 — the caller's "timed out" sentinel.  The flag is
# checked so that ONLY a watcher-fired death wears 124 (an OOM kill is
# not masked as a timeout).  pytest's own exit code propagates
# untouched (its failures must NOT wear 124).
case "$rc" in
  137|143)
    if [[ -e "${timedout_flag}" ]]; then
      rc=124
    fi
    ;;
esac
# QA F1 — the NORMAL path: reap the watcher tree NOW, before the traps
# are cleared.  cleanup() only ever ran on the trap path, and ``trap -
# ... EXIT`` below used to remove it BEFORE the exit, orphaning the
# watcher (subshell AND its sleep) on every successful run.
kill_watcher
trap - INT TERM EXIT
exit "$rc"
