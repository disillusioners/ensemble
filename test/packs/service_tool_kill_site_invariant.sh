#!/usr/bin/env bash
# test/packs/service_tool_kill_site_invariant.sh
#
# Pack: service_tool_kill_site_invariant
# A7 CI grep-gate (service-tool Phase 1 merge gate, task 1.MG.1a —
# moved forward from Phase 3 task 3.B.4).
#
# Exit codes: 0=PASS, 1=FAIL (new kill site outside the allowlist).
#
# ─────────────────────────────────────────────────────────────────
# FAIL CONDITION (the binding contract)
#
# Any occurrence of a kill primitive token
#     os.killpg(   os.kill(   killpg(0)   /proc/[0-9]   pkill   process_iter
# in daemon/**/*.py OUTSIDE the allowlisted files below → exit 1 and
# the merge gate FAILS. A new PR that introduces a signal/proc site in
# an un-inventoried file must either (a) not exist, or (b) extend this
# allowlist in the SAME PR with a justification comment — the review
# then sees the new kill site explicitly. The gate exists because the
# service-tool research proves the daemon-wide kill-site inventory is
# registry-scoped TODAY but nothing stopped a FUTURE site from
# appearing silently (the docs-only fence had no enforcement; the
# CODEOWNERS file is still a TODO — no .github/CODEOWNERS exists).
#
# ─────────────────────────────────────────────────────────────────
# ALLOWLIST (binding; each entry carries its justification)
#
# The ORIGINAL privileged kill-site trio (architect-verified sweep,
# 2026-09-15 — the only daemon files holding signal/proc sites):
#   daemon/tools/bash.py                    — bash tool teardown (orphan-reap
#                                             guard, pipe-hang discipline).
#   daemon/tools/proc_tools.py              — proc_* tool surface
#                                             (_verify_pid_ownership,
#                                             _attempt_kill_signal,
#                                             stop_process SIGTERM→5s→SIGKILL).
#   daemon/services/vscode_server_manager.py — code-server lifecycle
#                                             (stop with SIGTERM→5s→SIGKILL).
# Benign sig-0 probe (liveness ping, sends NO signal):
#   daemon/tools/upgrade_journal.py         — os.kill(p, 0) liveness ping
#                                             (:559) + /proc read.
#
# Plan-sanctioned service-tool surface (1.B landed d5001641 +
# 1.A store 9d2ab405 — this allowlist was extended at 1.C time,
# exactly these paths, per the A1-sanctioned mechanism):
#   daemon/tools/service_spawner.py         — service_spawner.stop:
#                                             os.killpg(pid, sig) process-
#                                             GROUP signals (A1 BLOCKING
#                                             amendment — pgid == pid for
#                                             setsid leaders; reaches
#                                             fork-children with zero added
#                                             reachability) + get_process_
#                                             start_time /proc/<pid>/stat
#                                             read (D5 PID-reuse defense).
#   daemon/tools/service_tools.py           — docstring-only mentions of
#                                             the sanctioned os.killpg
#                                             mechanism (no executable
#                                             signal site).
#   daemon/services/service_tool_manager.py — docstring-only mention of
#                                             the os.kill(pid, 0) liveness
#                                             ping (no executable site).
#   daemon/repositories/service_tool/       — 1.A store: docstring-only
#                                             F2/F8/A13 mentions of the
#                                             liveness ping (no executable
#                                             signal site). Listed as a
#                                             directory: the store is part
#                                             of the service-tool surface
#                                             and its docstrings quote the
#                                             mechanism.
#
# NOTE: allowlisting a file means "signal sites here are REVIEWED and
# INVENTORIED", not "anything goes". The Phase-2 13-site matrix
# (behavioral tests per site) is the companion enforcement.
# ─────────────────────────────────────────────────────────────────

PACK_NAME="service_tool_kill_site_invariant"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DAEMON_DIR="${REPO_ROOT}/daemon"

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    ${REPO_ROOT}"
echo "Scope:   daemon/**/*.py"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

if [ ! -d "${DAEMON_DIR}" ]; then
    echo "FAIL: daemon/ directory not found at ${DAEMON_DIR}"
    echo "RESULT: FAIL"
    exit 1
fi

# Kill-primitive tokens (ERE). /proc/[0-9] catches /proc/<pid> path
# reads; pkill and process_iter catch proc-walk kill sweeps.
TOKENS='os\.killpg\(|os\.kill\(|killpg\(0\)|/proc/[0-9]|pkill|process_iter'

# Allowlist — repo-relative paths (directories allowlist their subtree).
ALLOWLIST=(
    "daemon/tools/bash.py"
    "daemon/tools/proc_tools.py"
    "daemon/services/vscode_server_manager.py"
    "daemon/tools/upgrade_journal.py"
    "daemon/tools/service_spawner.py"
    "daemon/tools/service_tools.py"
    "daemon/services/service_tool_manager.py"
    "daemon/repositories/service_tool/"
)

is_allowlisted() {
    _rel="$1"
    for _entry in "${ALLOWLIST[@]}"; do
        case "${_entry}" in
            */)
                # Directory entry — prefix match.
                case "${_rel}/" in
                    "${_entry}"*) return 0 ;;
                esac
                ;;
            *)
                [ "${_rel}" = "${_entry}" ] && return 0
                ;;
        esac
    done
    return 1
}

# Collect hits (file:line:match), normalize to repo-relative paths.
HITS_FILE="$(mktemp)"
trap 'rm -f "${HITS_FILE}"' EXIT
grep -rn -E "${TOKENS}" "${DAEMON_DIR}" --include="*.py" \
    | sed "s|^${REPO_ROOT}/||" > "${HITS_FILE}" || true

OFFENDING="$(mktemp)"
trap 'rm -f "${HITS_FILE}" "${OFFENDING}"' EXIT

while IFS= read -r line; do
    [ -z "${line}" ] && continue
    rel_path="${line%%:*}"
    if ! is_allowlisted "${rel_path}"; then
        echo "${line}" >> "${OFFENDING}"
    fi
done < "${HITS_FILE}"

TOTAL_HITS="$(wc -l < "${HITS_FILE}" | tr -d ' ')"
ALLOWED_HITS="$(grep -c . "${HITS_FILE}" || true)"
ALLOWED_HITS=$((ALLOWED_HITS - $(wc -l < "${OFFENDING}" | tr -d ' ')))

echo "Token scan: ${TOTAL_HITS} hit(s) across daemon/; ${ALLOWED_HITS} inside the allowlist."
echo

if [ -s "${OFFENDING}" ]; then
    echo "FAIL: kill-primitive site(s) OUTSIDE the allowlist:"
    cat "${OFFENDING}"
    echo
    echo "A new signal/proc site in an un-inventoried file breaks the"
    echo "daemon-wide kill-site inventory (service-tool A7 gate)."
    echo "Either remove the site, or extend the allowlist in"
    echo "test/packs/service_tool_kill_site_invariant.sh IN THE SAME PR"
    echo "with a justification comment so review sees the new site."
    echo "RESULT: FAIL"
    exit 1
fi

echo "PASS: zero kill-primitive sites outside the allowlist."
echo "Allowlisted surfaces: ${#ALLOWLIST[@]} entries (see header for justifications)."
echo "RESULT: PASS"
exit 0
