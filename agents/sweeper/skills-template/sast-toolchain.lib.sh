#!/bin/sh
# sast-toolchain.lib.sh — POSIX-sh installer library for the
# static-analysis lane's scanner suite: semgrep, gitleaks, cppcheck,
# clang-tidy (system toolchain prerequisite — detected, never installed),
# and trivy. Sourced by the owning lane's installer skill, or executed
# directly with a command: probe | install | reversal.
#
# FENCES (tool-agnostic contract; the skill body carries the full block):
#   - NO Docker, NO system packages, NO remote fetch-and-execute
#   - sha256 verification of every fetched release asset BEFORE promotion
#     (an unset pin refuses the install outright)
#   - HONEST-STOP: a missing prerequisite emits HARD_BLOCKER and stops
#   - Idempotent: a warm host takes the verify-only fast path (the
#     install is idempotent — no install call, no state write)
#   - Write-target classes: (i) the persistent user-space toolchain ROOTS
#     at install time — the toolbench root, its state root, its config
#     root and its staging root (a private mktemp -d scratch dir), every
#     one of them env-overridable; (ii) the per-engagement sandbox root
#     at run time — copy-then-build, the target tree is never a write
#     target; (iii) every other user path — the target codebase included
#     — is never written, at any time, by any path
#
# Export contract:
#   aegis_readiness_probe                     0 warm / 1 cold / 2 in-progress-other
#   aegis_verify_sha256 <file> <expected>     0 match / 1 mismatch-or-refusal
#   aegis_verify_install <cfg>                evidence gate before promotion
#   aegis_acquire_install_lock                0 acquired / 1 contended
#   aegis_release_install_lock                0 always; a FOREIGN owner's
#                                            lock is left INTACT (the
#                                            release is ownership-checked)
#   aegis_install_signal <INT|TERM>           terminates the critical section
#                                            (130 / 143) and releases the
#                                            OWNED lock
#   aegis_scan_status_ok <status> <tool>      0 clean / 1 findings (a
#                                            successful scan) / >=2 failure
#   aegis_attempt_allowed / _bump_ / _clear_  self-heal cap of 2 per session
#   aegis_scanner_output_ok <path> <sandbox>  0 sandbox-relative / 1 refused
#   aegis_canonical_output_path <path> <root> CANONICAL path on stdout / 1
#                                            refused (diagnostics on stderr —
#                                            stdout is the data channel)
#   aegis_bin_answers <bin>                   0 evidence query answered / 1 refused
#   aegis_install_all                         installs the toolchain; 0 on success
#   aegis_reversal                            removes the installed toolchain
#
# Companion: sast-toolchain.md (the installer skill body, same directory)

# --- Shell-option discipline ----------------------------------------------
# Sourcing this library must not change the OPTIONS of the calling shell: a
# file-scope `set -eu` leaks -e/-u into the caller (the installer skill body
# sources this file) and turns a missing argument into a CALLER-shell kill —
# the promised HARD_BLOCKER refusal never runs. So the caller's own -e/-u
# flags are captured here, the library's body runs strict, and the caller's
# flags are restored EXACTLY before the file ends.
_aegis_caller_errexit=0
_aegis_caller_nounset=0
case "$-" in *e*) _aegis_caller_errexit=1 ;; esac
case "$-" in *u*) _aegis_caller_nounset=1 ;; esac
set -eu

# --- Sourced-vs-executed detection -----------------------------------------
# A SOURCED library must never `exit`: an exit inside a sourced file kills
# the CALLER's shell, so the promised refusal never runs and the caller
# cannot report anything. A DIRECTLY EXECUTED library must still end
# non-zero. "$0" is this file's own path when the file is executed and the
# CALLER's name when it is sourced — the one portable signal POSIX offers.
# The detection is computed ONCE here and REUSED by the dispatch block at
# the end of the file, so the two can never disagree.
_aegis_lib_executed=0
case "$0" in
    *sast-toolchain.lib.sh) _aegis_lib_executed=1 ;;
esac

# --- Config (class-(i) install-time locations) ---------------------------
AEGIS_TOOLBENCH_ROOT="${AEGIS_TOOLBENCH_ROOT:-$HOME/.local/opt/aegis-toolbench}"
AEGIS_STATE_HOME="${AEGIS_STATE_HOME:-$HOME/.cache/aegis-toolbench}"
AEGIS_CONFIG_HOME="${AEGIS_CONFIG_HOME:-$HOME/.config/aegis-toolbench}"
AEGIS_LIB_CONFIG="sast.env"
AEGIS_LOCK_STALE_SECS="${AEGIS_LOCK_STALE_SECS:-1800}"
AEGIS_ATTEMPT_CAP=2

# --- Pinned release assets (class-(i) install inputs) --------------------
# The sha256 pins are REQUIRED before first install: fill them from the
# vendor's checksum material at pin time. An unset or placeholder pin
# makes aegis_verify_sha256 refuse — an unpinned fetch never promotes.
SEMGREP_VERSION="${SEMGREP_VERSION:-pin-me}"
SEMGREP_URL="${SEMGREP_URL:-pin-me}"
SEMGREP_SHA256="${SEMGREP_SHA256:-pin-before-first-install}"

GITLEAKS_VERSION="${GITLEAKS_VERSION:-pin-me}"
GITLEAKS_URL="${GITLEAKS_URL:-pin-me}"
GITLEAKS_SHA256="${GITLEAKS_SHA256:-pin-before-first-install}"

CPPCHECK_VERSION="${CPPCHECK_VERSION:-pin-me}"
CPPCHECK_URL="${CPPCHECK_URL:-pin-me}"
CPPCHECK_SHA256="${CPPCHECK_SHA256:-pin-before-first-install}"

TRIVY_VERSION="${TRIVY_VERSION:-pin-me}"
TRIVY_URL="${TRIVY_URL:-pin-me}"
TRIVY_SHA256="${TRIVY_SHA256:-pin-before-first-install}"

# --- Internals ------------------------------------------------------------
_aegis_lock_dir() { printf '%s\n' "$AEGIS_STATE_HOME/sast-install.lock"; }
_aegis_attempts_file() { printf '%s\n' "$AEGIS_STATE_HOME/sast-session-state"; }

_aegis_ensure_dirs() {
    mkdir -p "$AEGIS_TOOLBENCH_ROOT/bin" "$AEGIS_STATE_HOME" "$AEGIS_CONFIG_HOME"
}

# --- Install-flow state (staging root, promoted assets) -------------------
# The staging root and the assets THIS run promoted. The state is what
# makes a refusal or a signal leave NOTHING half-promoted behind.
_AEGIS_STAGE_DIR=""
_AEGIS_PROMOTED_PATHS=""

_aegis_discard_stage() {
    # usage: _aegis_discard_stage
    # Removes the staging root this run created. A failed removal is
    # REPORTED, never swallowed: a staging root that survives a refusal is
    # a class-(i) path left behind on the host.
    if [ -n "$_AEGIS_STAGE_DIR" ]; then
        if [ -d "$_AEGIS_STAGE_DIR" ]; then
            if ! rm -rf "$_AEGIS_STAGE_DIR" 2>/dev/null; then
                echo "WARN: staging root could not be discarded ($_AEGIS_STAGE_DIR)"
            fi
        fi
        _AEGIS_STAGE_DIR=""
    fi
}

_aegis_record_promotion() {
    # usage: _aegis_record_promotion <path>
    # Records an asset this run is ABOUT to promote, so a later refusal or
    # signal can roll it back. Only assets this run CREATES are recorded:
    # a path that already existed belongs to a previous install, and
    # removing it on rollback would destroy a working toolchain that this
    # run merely overwrote.
    if [ -e "$1" ]; then
        return 0
    fi
    _AEGIS_PROMOTED_PATHS="$_AEGIS_PROMOTED_PATHS$1
"
}

_aegis_rollback_promoted() {
    # A refusal or a signal must never leave a half-installed toolchain:
    # every asset this run created is removed again. The probe-visible
    # config is promoted LAST, so a rollback leaves the probe cold rather
    # than advertising a toolchain that is not there.
    if [ -z "$_AEGIS_PROMOTED_PATHS" ]; then
        return 0
    fi
    printf '%s' "$_AEGIS_PROMOTED_PATHS" | while IFS= read -r _rb_path; do
        if [ -n "$_rb_path" ]; then
            rm -f "$_rb_path" 2>/dev/null || true
        fi
    done
    _AEGIS_PROMOTED_PATHS=""
}

_aegis_install_fail() {
    # usage: _aegis_install_fail <message>
    # The ONE refusal path of the install flow: the message is emitted, the
    # assets this run promoted are rolled back, the staging root is
    # discarded, the OWNED lock is released, and the flow ends non-zero.
    # In a SOURCED library the flow RETURNS (the caller's shell survives);
    # in a DIRECTLY EXECUTED library the process exits non-zero.
    echo "$1"
    _aegis_rollback_promoted
    _aegis_discard_stage
    aegis_release_install_lock
    trap - EXIT INT TERM
    if [ "$_aegis_lib_executed" -eq 1 ]; then
        exit 1
    fi
    return 1
}

# --- Signal handling (terminate the critical section) ---------------------
aegis_install_signal() {
    # usage: aegis_install_signal <INT|TERM>
    # A signal during the install MUST terminate the critical section. The
    # old handler only released the lock and let the install CONTINUE, so
    # the installer ran on holding NO lock at all and a second installer
    # could promote into the shared toolbench while the first was still
    # writing to it. This handler rolls back what this run promoted,
    # discards the staging root, releases the OWNED lock (a foreign
    # owner's lock survives intact) and exits non-zero: 130 for INT, 143
    # for TERM.
    _ais_sig="$1"
    _ais_rc=130
    if [ "$_ais_sig" = "TERM" ]; then
        _ais_rc=143
    fi
    echo "HARD_BLOCKER: $_ais_sig received — install critical section terminated, nothing promoted"
    _aegis_rollback_promoted
    _aegis_discard_stage
    aegis_release_install_lock
    trap - EXIT INT TERM
    exit "$_ais_rc"
}

# --- sha256 verification (EXECUTED before every promotion) ---------------
_aegis_sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        return 1
    fi
}

aegis_verify_sha256() {
    # usage: aegis_verify_sha256 <file> <expected-hex>
    # Computes the asset's sha256 and compares against the pinned digest.
    # Mismatch or unset pin => non-zero, staged asset stays unpromoted.
    _avs_file="$1"
    _avs_expected="$2"
    if [ -z "$_avs_expected" ] || [ "$_avs_expected" = "pin-before-first-install" ]; then
        echo "HARD_BLOCKER: missing sha256 pin for $_avs_file"
        return 1
    fi
    _avs_actual="$(_aegis_sha256_of "$_avs_file")" || {
        echo "HARD_BLOCKER: no sha256 tool available (need sha256sum or shasum)"
        return 1
    }
    if [ "$_avs_actual" != "$_avs_expected" ]; then
        echo "CHECKSUM MISMATCH: $_avs_file (expected $_avs_expected, got $_avs_actual)"
        return 1
    fi
    return 0
}

# --- Locking (mkdir-based, BSD-portable, no hard flock) -------------------
# The lock directory carries ONE owner record: the pid of the run that took
# it AND a per-run NONCE minted at acquire time, written as "<pid> <nonce>".
#
#   * the NONCE makes the release OWNERSHIP-CHECKED — a run may remove only
#     a lock whose nonce is its own. A FOREIGN owner's lock (a concurrent
#     installer's) survives INTACT: removing it would let a second
#     installer promote into the shared toolbench while the first is still
#     installing.
#   * the PID makes the stale reclaim SAFE — a lock is reclaimed only when
#     its recorded owner pid is PROVEN DEAD. A LIVE owner's lock is never
#     removed, however old the directory looks: an install that runs longer
#     than the staleness window is still an install in progress.
_AEGIS_OWNED_NONCE=""

_aegis_lock_owner_file() { printf '%s\n' "$(_aegis_lock_dir)/owner"; }

_aegis_mint_nonce() {
    # A per-run token: the pid, the wall clock and — where the kernel
    # offers it — random bytes. The pid alone would not distinguish two
    # runs of a recycled pid.
    _mn_nonce="$$-$(date +%s 2>/dev/null || echo 0)"
    if [ -r /dev/urandom ]; then
        _mn_rand=$(od -An -N6 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')
        if [ -n "$_mn_rand" ]; then
            _mn_nonce="$_mn_nonce-$_mn_rand"
        fi
    fi
    printf '%s\n' "$_mn_nonce"
}

_aegis_lock_owner_state() {
    # usage: _aegis_lock_owner_state -> "alive" | "dead" | "unknown"
    # The owner record is "<pid> <nonce>"; only the pid decides liveness.
    # Death is never guessed: "kill -0" cannot tell "no such process" from
    # "permission denied", so /proc settles it where it exists. Where it
    # does not, the state stays "unknown" — death UNPROVEN, and an
    # unproven death never authorises a reclaim.
    _los_file="$(_aegis_lock_owner_file)"
    if [ ! -f "$_los_file" ]; then
        echo "unknown"
        return 0
    fi
    _los_pid=""
    while read -r _los_first _los_rest; do
        _los_pid="$_los_first"
        break
    done < "$_los_file"
    case "$_los_pid" in
        ''|*[!0-9]*)
            echo "unknown"
            return 0
            ;;
    esac
    if kill -0 "$_los_pid" 2>/dev/null; then
        echo "alive"
        return 0
    fi
    if [ -d /proc ]; then
        if [ -d "/proc/$_los_pid" ]; then
            echo "alive"
            return 0
        fi
        echo "dead"
        return 0
    fi
    echo "unknown"
    return 0
}

_aegis_lock_reclaimable() {
    # usage: _aegis_lock_reclaimable  (0 = the lock may be reclaimed, 1 = it may not)
    # A lock is reclaimable only when it is older than the staleness window
    # AND its owner is not alive:
    #   * a recorded owner that is ALIVE -> never reclaimable, however old
    #     the directory looks (an install that runs longer than the window
    #     is still an install in progress);
    #   * a recorded owner that is PROVEN dead -> reclaimable;
    #   * an UNRECORDED owner -> reclaimable only once the window has
    #     passed: the only way to reach it is an installer that died
    #     between taking the lock and recording its ownership, and no live
    #     installer stays there for the whole window.
    _lrc_lock="$(_aegis_lock_dir)"
    _lrc_stale_min=$(( (AEGIS_LOCK_STALE_SECS + 59) / 60 ))
    if [ -z "$(find "$_lrc_lock" -type d -mmin +"$_lrc_stale_min" 2>/dev/null)" ]; then
        return 1
    fi
    case "$(_aegis_lock_owner_state)" in
        dead)
            return 0
            ;;
        unknown)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

aegis_acquire_install_lock() {
    _ail_lock="$(_aegis_lock_dir)"
    mkdir -p "$(dirname "$_ail_lock")"
    _ail_nonce="$(_aegis_mint_nonce)"
    if mkdir "$_ail_lock" 2>/dev/null; then
        printf '%s %s\n' "$$" "$_ail_nonce" > "$_ail_lock/owner"
        _AEGIS_OWNED_NONCE="$_ail_nonce"
        return 0
    fi
    # A lock exists. It may be reclaimed ONLY when it is reclaimable — an
    # owner that is alive is never removed, however old the lock looks.
    if _aegis_lock_reclaimable; then
        echo "WARN: stale install lock (owner not alive, older than ${AEGIS_LOCK_STALE_SECS}s) — treating as abandoned"
        rm -rf "$_ail_lock"
        if mkdir "$_ail_lock" 2>/dev/null; then
            printf '%s %s\n' "$$" "$_ail_nonce" > "$_ail_lock/owner"
            _AEGIS_OWNED_NONCE="$_ail_nonce"
            return 0
        fi
    fi
    return 1
}

aegis_release_install_lock() {
    # Releases the install lock — but ONLY the lock THIS run owns: the
    # lock's nonce must match the nonce this run minted at acquire time.
    # A FOREIGN owner's lock is left INTACT and reported.
    _arl_lock="$(_aegis_lock_dir)"
    if [ ! -d "$_arl_lock" ]; then
        _AEGIS_OWNED_NONCE=""
        return 0
    fi
    _arl_nonce=""
    if [ -f "$_arl_lock/owner" ]; then
        while read -r _arl_first _arl_second; do
            _arl_nonce="$_arl_second"
            break
        done < "$_arl_lock/owner"
    fi
    if [ -z "$_AEGIS_OWNED_NONCE" ] || [ "$_arl_nonce" != "$_AEGIS_OWNED_NONCE" ]; then
        echo "WARN: install lock is owned by another run — left intact"
        return 0
    fi
    if ! rm -rf "$_arl_lock" 2>/dev/null; then
        echo "WARN: install lock removal failed — the lock dir may survive"
    fi
    _AEGIS_OWNED_NONCE=""
    return 0
}

_aegis_lock_held() {
    # The PROBE's view of the lock: a lock whose owner is alive (or whose
    # owner cannot be proven dead) is HELD — the probe reports "install in
    # progress" (rc=2). The probe NEVER removes a lock: a probe that
    # deleted a live install's lock would let a second installer promote
    # into the shared toolbench. The probe also never ACQUIRES the lock,
    # so it can never block the installer it is watching.
    _lh_lock="$(_aegis_lock_dir)"
    if [ ! -e "$_lh_lock" ]; then
        return 1
    fi
    if _aegis_lock_reclaimable; then
        return 1
    fi
    return 0
}

# --- Self-heal attempt cap (2 per session) --------------------------------
# The counter is VALIDATED before it is compared. A malformed counter
# (empty, garbage, negative, or out of range) is a HARD_BLOCKER, never a
# silent "no attempts used": an unvalidated counter lets a negative value
# straight through the cap, and a self-heal loop with no bound is exactly
# what the cap exists to prevent.
_aegis_read_attempts() {
    _ra_file="$(_aegis_attempts_file)"
    if [ -f "$_ra_file" ]; then
        _ra_content=$(cat "$_ra_file" 2>/dev/null) || _ra_content=""
        printf '%s' "$_ra_content"
    else
        echo 0
    fi
}

_aegis_attempts_valid() {
    # usage: _aegis_attempts_valid <raw-counter>  (0 = usable, 1 = malformed)
    _av_raw=$(printf '%s' "${1:-}" | tr -d ' \t\n\r')
    if [ -z "$_av_raw" ]; then
        return 1
    fi
    case "$_av_raw" in
        *[!0-9]*) return 1 ;;
    esac
    if [ "$_av_raw" -gt "$AEGIS_ATTEMPT_CAP" ]; then
        return 1
    fi
    return 0
}

aegis_attempt_allowed() {
    # usage: aegis_attempt_allowed  (0 = one attempt is still allowed)
    _aa_raw="$(_aegis_read_attempts)"
    if ! _aegis_attempts_valid "$_aa_raw"; then
        echo "HARD_BLOCKER: malformed self-heal attempt counter ('$_aa_raw') — refusing the attempt; the cap is not a licence to run unbounded"
        return 1
    fi
    _aa_count=$(printf '%s' "$_aa_raw" | tr -d ' \t\n\r')
    if [ "$_aa_count" -lt "$AEGIS_ATTEMPT_CAP" ]; then
        return 0
    fi
    return 1
}

_aegis_bump_attempt() {
    _ba_raw="$(_aegis_read_attempts)"
    if ! _aegis_attempts_valid "$_ba_raw"; then
        echo "HARD_BLOCKER: malformed self-heal attempt counter ('$_ba_raw') — refusing the attempt"
        return 1
    fi
    _ba_now=$(printf '%s' "$_ba_raw" | tr -d ' \t\n\r')
    _ba_next=$(( _ba_now + 1 ))
    printf '%s\n' "$_ba_next" > "$(_aegis_attempts_file)"
}

_aegis_clear_attempts() {
    _ca_file="$(_aegis_attempts_file)"
    if [ -f "$_ca_file" ]; then
        if ! rm -f "$_ca_file" 2>/dev/null; then
            echo "WARN: self-heal attempt counter could not be cleared ($_ca_file)"
        fi
    fi
}

# --- Shared containment core (byte-identical with the cracker library) ---
# The containment check CANONICALIZES both sides before comparing. A raw
# string-prefix test is not a containment test: it accepts every path that
# merely STARTS with the root string, so ".."-traversal,
# symlinked-directory AND compound symlink-then-".." escapes all pass it.
#
# PRECONDITION (fail-closed): the root and the TARGET'S PARENT must both
# exist — an unresolvable path is refused, never guessed.
_aegis_resolve_path() {
    # usage: _aegis_resolve_path <path>   -> canonical path on stdout
    # Resolves "..", "." and symlinks KERNEL-FAITHFULLY, in the final
    # component and in every parent component: "cd -P" hands the whole
    # parent path to the kernel, so a symlinked parent followed by ".."
    # lands where the KERNEL lands it (one level above the symlink's
    # target) — never where a lexical ".." cancellation would put it.
    # Returns non-zero when the path cannot be resolved (never guesses).
    _rp_path="${1:-}"
    _rp_hops=0
    while [ -n "$_rp_path" ]; do
        # A non-normalized TAIL — the path ends in '/', '/.' or '/..' —
        # resolves below to a NON-canonical string ("<canon>/", "<canon>/."
        # or "<canon>/..") that PASSES the root prefix comparison yet can
        # never be a write target: the kernel answers EISDIR and the guard
        # has attributed nothing. Refused here, BY NAME, before any
        # comparison. The diagnostic goes to STDERR so a caller's command
        # substitution still captures path-or-nothing on stdout.
        case "$_rp_path" in
            */.|*/..|*/)
                echo "HARD_BLOCKER: path ends in a directory segment ('/'), a self segment ('/.'), or a parent segment ('/..') — normalize the path: $_rp_path" >&2
                return 1
                ;;
            */*) : ;;
            *) return 1 ;;
        esac
        _rp_dir="${_rp_path%/*}"
        _rp_base="${_rp_path##*/}"
        [ -n "$_rp_dir" ] || _rp_dir=/
        _rp_canon=$(cd -P "$_rp_dir" 2>/dev/null && pwd -P) || return 1
        if [ "$_rp_canon" = "/" ]; then
            _rp_full="/$_rp_base"
        else
            _rp_full="$_rp_canon/$_rp_base"
        fi
        if [ ! -L "$_rp_full" ]; then
            printf '%s\n' "$_rp_full"
            return 0
        fi
        # the final component is a symlink: follow it (bounded) and resolve
        # the target the same way, so writing THROUGH it cannot escape
        _rp_hops=$(( _rp_hops + 1 ))
        [ "$_rp_hops" -le 8 ] || return 1
        _rp_link=$(readlink "$_rp_full" 2>/dev/null) || return 1
        case "$_rp_link" in
            /*) _rp_path="$_rp_link" ;;
            *)  _rp_path="$_rp_canon/$_rp_link" ;;
        esac
    done
    return 1
}

_aegis_path_in_root() {
    # usage: _aegis_path_in_root <path> <root>   (0 inside / 1 outside)
    # The comparison is made between the CANONICAL target and the
    # CANONICAL root — never between raw strings — and both sides resolve
    # KERNEL-FAITHFULLY: "cd" without -P processes ".." lexically, so a
    # symlinked parent followed by ".." is never traversed by the check
    # while the kernel resolves it physically at the write (the compound
    # symlink-then-".." escape class).
    [ -n "${1:-}" ] && [ -n "${2:-}" ] || return 1
    _pir_root=$(cd -P "$2" 2>/dev/null && pwd -P) || return 1
    _pir_target=$(_aegis_resolve_path "$1") || return 1
    case "$_pir_target" in
        "$_pir_root"/*) return 0 ;;
        *) return 1 ;;
    esac
}

# --- Scanner-output discipline (sandbox-relative destinations only) -------
aegis_scanner_output_ok() {
    # usage: aegis_scanner_output_ok <path> <sandbox-root>
    # Scanner output lands inside the engagement sandbox via script
    # redirection; a destination outside the sandbox is refused. Both the
    # destination AND the sandbox root are resolved kernel-faithfully and
    # compared canonically through the shared core — a raw string-prefix
    # test accepts every path that merely STARTS with the root string
    # (".."-traversal, symlinked-parent and compound symlink-then-".."
    # escapes included).
    _so_path="${1:-}"
    _so_root="${2:-}"
    if [ -z "$_so_path" ] || [ -z "$_so_root" ]; then
        echo "HARD_BLOCKER: scanner output destination and engagement sandbox root are both required — scanner output lands in the sandbox only"
        return 1
    fi
    # non-normalized tails are refused with the problem NAMED, never left
    # to the kernel's EISDIR after the prefix comparison already passed
    case "$_so_path" in
        */.|*/..|*/)
            echo "HARD_BLOCKER: scanner output destination ends in a directory segment ('/'), a self segment ('/.'), or a parent segment ('/..') — normalize the path"
            return 1
            ;;
    esac
    if _aegis_path_in_root "$_so_path" "$_so_root"; then
        return 0
    fi
    echo "HARD_BLOCKER: scanner output destination outside the engagement sandbox — scanner output lands in the sandbox only"
    return 1
}

aegis_canonical_output_path() {
    # usage: aegis_canonical_output_path <path> <sandbox-root>
    #   -> echoes the CANONICAL destination path on stdout (rc=0)
    # Path-returning companion to aegis_scanner_output_ok: the caller's
    # redirect acts on the RESOLVED form, so no second, independent kernel
    # resolution runs between the check and the write — a swap of the raw
    # path after the guard cannot redirect the write (the N-1 companion;
    # the boolean guard stays the cheap pre-check, this adds canonical
    # substitution). Stdout is the DATA channel — the path alone — so
    # every refusal is emitted on STDERR and fails closed (rc=1, nothing
    # echoed, the caller's capture stays empty).
    _cop_path="${1:-}"
    _cop_root="${2:-}"
    if [ -z "$_cop_path" ] || [ -z "$_cop_root" ]; then
        echo "HARD_BLOCKER: scanner output destination and engagement sandbox root are both required — scanner output lands in the sandbox only" >&2
        return 1
    fi
    case "$_cop_path" in
        */.|*/..|*/)
            echo "HARD_BLOCKER: scanner output destination ends in a directory segment ('/'), a self segment ('/.'), or a parent segment ('/..') — normalize the path" >&2
            return 1
            ;;
    esac
    _cop_canon_root=$(cd -P "$_cop_root" 2>/dev/null && pwd -P) || {
        echo "HARD_BLOCKER: engagement sandbox root unresolvable — the root must exist before the sweep runs" >&2
        return 1
    }
    _cop_canon_path=$(_aegis_resolve_path "$_cop_path") || {
        echo "HARD_BLOCKER: scanner output destination unresolvable — the sandbox root and the destination's parent directory must both exist before the sweep runs" >&2
        return 1
    }
    case "$_cop_canon_path" in
        "$_cop_canon_root"/*) ;;
        *)
            echo "HARD_BLOCKER: scanner output destination outside the engagement sandbox — scanner output lands in the sandbox only" >&2
            return 1
            ;;
    esac
    printf '%s\n' "$_cop_canon_path"
}

# --- Scanner exit-status mapping (findings are NOT errors) ---------------
# The scanner suite reports FINDINGS through its exit status: gitleaks
# exits 1 when it finds a secret, cppcheck exits 1 when it finds an error,
# semgrep exits 1 when it finds a match under --error. That is a
# SUCCESS-with-findings, NOT a failed scan. The two must stay distinct:
# treating a findings-exit as a failure makes every real finding look like
# a broken tool (and would abort the sweep on the first true positive),
# while treating a real failure as findings would report a broken scanner
# as a clean bill of health. The mapping is explicit:
#
#   0      -> a clean scan
#   1      -> findings (a successful scan that found something)
#   >= 2   -> a real failure (including 127 = the scanner is absent)
#
# NOTE: clang-tidy is NOT in this family — its exit status is an ERROR
# COUNT, not a status class, so the mapping does not apply to it.
aegis_scan_status_ok() {
    # usage: aegis_scan_status_ok <exit-status> <tool-name>
    _aso_status="$1"
    _aso_tool="$2"
    case "$_aso_status" in
        0)
            return 0
            ;;
        1)
            echo "scanner findings reported by $_aso_tool (exit 1) — a findings-exit is a successful scan, not a failure"
            return 0
            ;;
        *)
            echo "HARD_BLOCKER: $_aso_tool failed (exit $_aso_status) — the scan did not complete"
            return 1
            ;;
    esac
}

# --- Tool-specific evidence queries (existence is not evidence) -----------
aegis_bin_answers() {
    _aba_bin="$1"
    "$_aba_bin" --version >/dev/null 2>&1
}

# --- Verify (evidence not claims — gates config promotion) ---------------
aegis_verify_install() {
    # usage: aegis_verify_install <cfg>
    # Every recorded binary must be absolute, executable, and answer its
    # evidence query. Any failure => non-zero; the caller discards the
    # staged config and the probe-visible config is never written.
    _vi_cfg="$1"
    [ -f "$_vi_cfg" ] || return 1
    _vi_found=0
    while IFS='=' read -r _vi_k _vi_v; do
        case "$_vi_k" in
            *_BIN)
                _vi_found=$(( _vi_found + 1 ))
                [ -n "$_vi_v" ] || return 1
                case "$_vi_v" in
                    /*) ;;
                    *) return 1 ;;
                esac
                [ -x "$_vi_v" ] || return 1
                aegis_bin_answers "$_vi_v" || return 1
                ;;
        esac
    done < "$_vi_cfg"
    [ "$_vi_found" -ge 1 ] || return 1
    return 0
}

# --- Readiness probe (0 warm / 1 cold / 2 install-in-progress-other) ------
aegis_readiness_probe() {
    if _aegis_lock_held; then
        return 2
    fi
    _rp_cfg="$AEGIS_CONFIG_HOME/$AEGIS_LIB_CONFIG"
    [ -f "$_rp_cfg" ] || return 1
    if aegis_verify_install "$_rp_cfg"; then
        # warm: export every recorded path for the caller
        while IFS='=' read -r _rp_k _rp_v; do
            export "$_rp_k=$_rp_v"
        done < "$_rp_cfg"
        return 0
    fi
    return 1
}

# --- Fetch (download only — verification is always a separate gate) -------
# The fetch is GUARDED: a transport failure, a non-2xx HTTP status and an
# empty body all REFUSE — the caller sees a non-zero status and NOTHING is
# promoted. "curl -f" alone is not enough: without an explicit status
# check a 404 body is a perfectly good file, and the sha256 gate would
# then be the only thing standing between an error page and a promotion.
_aegis_fetch() {
    _af_url="$1"
    _af_dest="$2"
    command -v curl >/dev/null 2>&1 || {
        echo "HARD_BLOCKER: curl absent — release asset download impossible"
        return 1
    }
    _af_code=$(curl -sS -L -o "$_af_dest" -w '%{http_code}' "$_af_url" 2>/dev/null) || _af_code=""
    case "$_af_code" in
        2??) : ;;
        *)
            echo "HARD_BLOCKER: fetch failed (HTTP ${_af_code:-none}) for $_af_url — nothing promoted"
            rm -f "$_af_dest" 2>/dev/null || true
            return 1
            ;;
    esac
    if [ ! -s "$_af_dest" ]; then
        echo "HARD_BLOCKER: fetch failed (empty body) for $_af_url — nothing promoted"
        rm -f "$_af_dest" 2>/dev/null || true
        return 1
    fi
    return 0
}

# --- Per-tool install: download -> sha256 gate -> promote -----------------
# A tool with an unset pin or a failed fetch is a per-tool gap (reported),
# never a blanket failure and never an unpinned promotion.
_aegis_install_tool() {
    # usage: _aegis_install_tool <name> <version> <url> <sha256> <stage-dir>
    _it_name="$1"; _it_version="$2"; _it_url="$3"; _it_sha="$4"; _it_stage="$5"
    if [ "$_it_url" = "pin-me" ] || [ "$_it_sha" = "pin-before-first-install" ]; then
        echo "HARD_BLOCKER: missing sha256 pin for $_it_name@$_it_version — tool not installed (per-tool gap)"
        return 1
    fi
    echo "fetching $_it_name@$_it_version ..."
    _aegis_fetch "$_it_url" "$_it_stage/$_it_name.tar.gz" || {
        echo "HARD_BLOCKER: fetch failed for $_it_name@$_it_version (per-tool gap)"
        return 1
    }
    # sha256 gate BEFORE any promotion — mismatch aborts, deletes, promotes nothing
    aegis_verify_sha256 "$_it_stage/$_it_name.tar.gz" "$_it_sha" || {
        rm -f "$_it_stage/$_it_name.tar.gz"
        echo "VERIFY FAILED — staged asset discarded, nothing promoted ($_it_name)"
        return 1
    }
    if ! tar -xzf "$_it_stage/$_it_name.tar.gz" -C "$_it_stage"; then
        echo "HARD_BLOCKER: $_it_name archive did not unpack — tool not installed (per-tool gap)"
        return 1
    fi
    if [ -f "$_it_stage/$_it_name" ]; then
        _aegis_record_promotion "$AEGIS_TOOLBENCH_ROOT/bin/$_it_name"
        cp "$_it_stage/$_it_name" "$AEGIS_TOOLBENCH_ROOT/bin/$_it_name"
        chmod 0755 "$AEGIS_TOOLBENCH_ROOT/bin/$_it_name"
        return 0
    fi
    echo "HARD_BLOCKER: no verified release binary for $_it_name@$_it_version — never source-build"
    return 1
}

# --- Install flow (class-(i) writes only; trap releases the lock) ---------
aegis_install_all() {
    # The install lock is taken BEFORE the first mutation and released on
    # EVERY exit path — success, refusal, signal. The INT/TERM handlers
    # TERMINATE the critical section: the old handler only released the
    # lock and let the install CONTINUE, so the installer ran on holding
    # NO lock at all and a second installer could promote into the shared
    # toolbench while the first was still writing to it.
    aegis_attempt_allowed || {
        echo "HARD_BLOCKER: self-heal attempt cap reached (${AEGIS_ATTEMPT_CAP}) — report, do not retry"
        return 1
    }
    # The signal handlers are armed BEFORE the lock is taken: a signal that
    # lands in the window between "lock taken" and "handlers armed" would
    # kill the install with the default disposition, and the lock would
    # survive with an owner that never released it.
    trap 'aegis_install_signal INT' INT
    trap 'aegis_install_signal TERM' TERM
    aegis_acquire_install_lock || {
        echo "install in progress elsewhere — degrade this turn, retry next"
        trap - INT TERM
        return 1
    }
    trap 'aegis_release_install_lock' EXIT
    if ! _aegis_bump_attempt; then
        return 1
    fi

    if ! command -v curl >/dev/null 2>&1; then
        _aegis_install_fail "HARD_BLOCKER: curl absent — release asset download impossible"
        return 1
    fi
    if ! command -v sha256sum >/dev/null 2>&1; then
        if ! command -v shasum >/dev/null 2>&1; then
            _aegis_install_fail "HARD_BLOCKER: no sha256 tool available (need sha256sum or shasum)"
            return 1
        fi
    fi

    _aegis_ensure_dirs
    _in_stage="$(mktemp -d)"
    _AEGIS_STAGE_DIR="$_in_stage"

    # clang-tidy ships with the system clang toolchain — DETECTED, never installed
    _in_ct=""
    if command -v clang-tidy >/dev/null 2>&1; then
        _in_ct="$(command -v clang-tidy)"
    fi

    _in_semgrep=0; _in_gitleaks=0; _in_cppcheck=0; _in_trivy=0
    if _aegis_install_tool semgrep  "$SEMGREP_VERSION"  "$SEMGREP_URL"  "$SEMGREP_SHA256"  "$_in_stage"; then
        _in_semgrep=1
    fi
    if _aegis_install_tool gitleaks "$GITLEAKS_VERSION" "$GITLEAKS_URL" "$GITLEAKS_SHA256" "$_in_stage"; then
        _in_gitleaks=1
    fi
    if _aegis_install_tool cppcheck "$CPPCHECK_VERSION" "$CPPCHECK_URL" "$CPPCHECK_SHA256" "$_in_stage"; then
        _in_cppcheck=1
    fi
    if _aegis_install_tool trivy    "$TRIVY_VERSION"    "$TRIVY_URL"    "$TRIVY_SHA256"    "$_in_stage"; then
        _in_trivy=1
    fi

    if [ $(( _in_semgrep + _in_gitleaks + _in_cppcheck + _in_trivy )) -eq 0 ] && [ -z "$_in_ct" ]; then
        _aegis_install_fail "HARD_BLOCKER: no scanner promoted and no system clang-tidy — nothing to probe"
        return 1
    fi

    # staged config -> verify -> promote (probe-visible config LAST).
    # The staged config lives INSIDE the mktemp -d staging root (a
    # class-(i) path) — a bare `mktemp` in /tmp would be a class-(iii)
    # write, which the FENCES contract forbids ("never written, at any
    # time, by any path").
    _in_tmpcfg="$_in_stage/staged-config"
    : > "$_in_tmpcfg"
    if [ "$_in_semgrep" -eq 1 ]; then
        printf 'SEMGREP_BIN=%s\nSEMGREP_VERSION=%s\n'   "$AEGIS_TOOLBENCH_ROOT/bin/semgrep"  "$SEMGREP_VERSION"  >> "$_in_tmpcfg"
    fi
    if [ "$_in_gitleaks" -eq 1 ]; then
        printf 'GITLEAKS_BIN=%s\nGITLEAKS_VERSION=%s\n' "$AEGIS_TOOLBENCH_ROOT/bin/gitleaks" "$GITLEAKS_VERSION" >> "$_in_tmpcfg"
    fi
    if [ "$_in_cppcheck" -eq 1 ]; then
        printf 'CPPCHECK_BIN=%s\nCPPCHECK_VERSION=%s\n' "$AEGIS_TOOLBENCH_ROOT/bin/cppcheck" "$CPPCHECK_VERSION" >> "$_in_tmpcfg"
    fi
    if [ "$_in_trivy" -eq 1 ]; then
        printf 'TRIVY_BIN=%s\nTRIVY_VERSION=%s\n'       "$AEGIS_TOOLBENCH_ROOT/bin/trivy"    "$TRIVY_VERSION"    >> "$_in_tmpcfg"
    fi
    if [ -n "$_in_ct" ]; then
        printf 'CLANG_TIDY_BIN=%s\n' "$_in_ct" >> "$_in_tmpcfg"
    fi
    if ! aegis_verify_install "$_in_tmpcfg"; then
        _aegis_install_fail "VERIFY FAILED — config discarded, probe stays cold"
        return 1
    fi
    if ! mv "$_in_tmpcfg" "$AEGIS_CONFIG_HOME/$AEGIS_LIB_CONFIG"; then
        rm -f "$_in_tmpcfg"
        _aegis_install_fail "HARD_BLOCKER: config promote failed — probe stays cold"
        return 1
    fi

    _aegis_discard_stage
    _aegis_clear_attempts
    trap - EXIT INT TERM
    aegis_release_install_lock
    echo "sast-toolchain: OK (semgrep=$_in_semgrep gitleaks=$_in_gitleaks cppcheck=$_in_cppcheck trivy=$_in_trivy clang-tidy=${_in_ct:-absent})"
}

# --- Reversal (uninstall — leaves the host as it was found) ---------------
aegis_reversal() {
    rm -f "$AEGIS_CONFIG_HOME/$AEGIS_LIB_CONFIG"
    rm -f "$AEGIS_TOOLBENCH_ROOT/bin/semgrep"
    rm -f "$AEGIS_TOOLBENCH_ROOT/bin/gitleaks"
    rm -f "$AEGIS_TOOLBENCH_ROOT/bin/cppcheck"
    rm -f "$AEGIS_TOOLBENCH_ROOT/bin/trivy"
    rm -f "$(_aegis_attempts_file)"
    rm -rf "$(_aegis_lock_dir)"
    echo "sast-toolchain: reversed (system clang-tidy was a prerequisite — untouched)"
}

# --- Restore the sourcing shell's options (exactly) -----------------------
# The library's strict mode is scoped to this file: a shell that SOURCED it
# keeps its own -e/-u flags and gains none. Direct execution re-enables
# strict mode in the dispatch block below.
if [ "$_aegis_caller_errexit" = 1 ]; then set -e; else set +e; fi
if [ "$_aegis_caller_nounset" = 1 ]; then set -u; else set +u; fi
unset _aegis_caller_errexit _aegis_caller_nounset

# --- Dispatch when executed directly (sourcing defines functions only) ----
# The sourced/executed detection computed at the top of this file is
# REUSED here, so the dispatch decision and the refusal path can never
# disagree about how this library was entered.
if [ "$_aegis_lib_executed" -eq 1 ]; then
    set -eu
    case "${1:-}" in
        probe)    aegis_readiness_probe; exit $? ;;
        install)  aegis_install_all ;;
        reversal) aegis_reversal ;;
        *) echo "usage: $0 probe|install|reversal" >&2; exit 64 ;;
    esac
fi
