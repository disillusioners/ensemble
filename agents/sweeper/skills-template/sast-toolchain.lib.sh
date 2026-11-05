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
#   aegis_release_install_lock                always succeeds
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
aegis_acquire_install_lock() {
    _ail_lock="$(_aegis_lock_dir)"
    mkdir -p "$(dirname "$_ail_lock")"
    if mkdir "$_ail_lock" 2>/dev/null; then
        printf '%s\n' "$$" > "$_ail_lock/owner"
        return 0
    fi
    # staleness: a lock older than the window is a crashed install — abandon it
    _ail_stale_min=$(( (AEGIS_LOCK_STALE_SECS + 59) / 60 ))
    if [ -n "$(find "$_ail_lock" -type d -mmin +"$_ail_stale_min" 2>/dev/null)" ]; then
        echo "WARN: stale install lock (older than ${AEGIS_LOCK_STALE_SECS}s) — treating as abandoned"
        rm -rf "$_ail_lock"
        if mkdir "$_ail_lock" 2>/dev/null; then
            printf '%s\n' "$$" > "$_ail_lock/owner"
            return 0
        fi
    fi
    return 1
}

aegis_release_install_lock() {
    rm -rf "$(_aegis_lock_dir)" 2>/dev/null || true
}

_aegis_lock_held() {
    _lh_lock="$(_aegis_lock_dir)"
    [ -e "$_lh_lock" ] || return 1
    _lh_stale_min=$(( (AEGIS_LOCK_STALE_SECS + 59) / 60 ))
    if [ -n "$(find "$_lh_lock" -type d -mmin +"$_lh_stale_min" 2>/dev/null)" ]; then
        rm -rf "$_lh_lock" 2>/dev/null || true
        return 1
    fi
    return 0
}

# --- Self-heal attempt cap (2 per session) --------------------------------
_aegis_read_attempts() {
    _ra_file="$(_aegis_attempts_file)"
    if [ -f "$_ra_file" ]; then
        cat "$_ra_file" 2>/dev/null || echo 0
    else
        echo 0
    fi
}

aegis_attempt_allowed() {
    [ "$(_aegis_read_attempts)" -lt "$AEGIS_ATTEMPT_CAP" ]
}

_aegis_bump_attempt() {
    _ba_now="$(_aegis_read_attempts)"
    _ba_next=$(( _ba_now + 1 ))
    printf '%s\n' "$_ba_next" > "$(_aegis_attempts_file)"
}

_aegis_clear_attempts() {
    rm -f "$(_aegis_attempts_file)" 2>/dev/null || true
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
_aegis_fetch() {
    _af_url="$1"
    _af_dest="$2"
    command -v curl >/dev/null 2>&1 || {
        echo "HARD_BLOCKER: curl absent — release asset download impossible"
        return 1
    }
    curl -fsSL -o "$_af_dest" "$_af_url"
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
    tar -xzf "$_it_stage/$_it_name.tar.gz" -C "$_it_stage"
    if [ -f "$_it_stage/$_it_name" ]; then
        cp "$_it_stage/$_it_name" "$AEGIS_TOOLBENCH_ROOT/bin/$_it_name"
        chmod 0755 "$AEGIS_TOOLBENCH_ROOT/bin/$_it_name"
        return 0
    fi
    echo "HARD_BLOCKER: no verified release binary for $_it_name@$_it_version — never source-build"
    return 1
}

# --- Install flow (class-(i) writes only; trap releases the lock) ---------
aegis_install_all() {
    aegis_attempt_allowed || {
        echo "HARD_BLOCKER: self-heal attempt cap reached (${AEGIS_ATTEMPT_CAP}) — report, do not retry"
        return 1
    }
    aegis_acquire_install_lock || {
        echo "install in progress elsewhere — degrade this turn, retry next"
        return 1
    }
    trap 'aegis_release_install_lock' EXIT INT TERM
    _aegis_bump_attempt

    command -v curl >/dev/null 2>&1 || { echo "HARD_BLOCKER: curl"; exit 1; }
    command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || { echo "HARD_BLOCKER: sha256 tool"; exit 1; }

    _aegis_ensure_dirs
    _in_stage="$(mktemp -d)"
    trap 'rm -rf "$_in_stage"; aegis_release_install_lock' EXIT INT TERM

    # clang-tidy ships with the system clang toolchain — DETECTED, never installed
    _in_ct="$(command -v clang-tidy || true)"

    _in_semgrep=0; _in_gitleaks=0; _in_cppcheck=0; _in_trivy=0
    _aegis_install_tool semgrep  "$SEMGREP_VERSION"  "$SEMGREP_URL"  "$SEMGREP_SHA256"  "$_in_stage"  && _in_semgrep=1  || true
    _aegis_install_tool gitleaks "$GITLEAKS_VERSION" "$GITLEAKS_URL" "$GITLEAKS_SHA256" "$_in_stage" && _in_gitleaks=1 || true
    _aegis_install_tool cppcheck "$CPPCHECK_VERSION" "$CPPCHECK_URL" "$CPPCHECK_SHA256" "$_in_stage" && _in_cppcheck=1 || true
    _aegis_install_tool trivy    "$TRIVY_VERSION"    "$TRIVY_URL"    "$TRIVY_SHA256"    "$_in_stage" && _in_trivy=1    || true

    if [ $(( _in_semgrep + _in_gitleaks + _in_cppcheck + _in_trivy )) -eq 0 ] && [ -z "$_in_ct" ]; then
        rm -rf "$_in_stage"
        echo "HARD_BLOCKER: no scanner promoted and no system clang-tidy — nothing to probe"
        exit 1
    fi

    # staged config -> verify -> promote (probe-visible config LAST).
    # The staged config lives INSIDE the mktemp -d staging root (a
    # class-(i) path) — a bare `mktemp` in /tmp would be a class-(iii)
    # write, which the FENCES contract forbids ("never written, at any
    # time, by any path").
    _in_tmpcfg="$_in_stage/staged-config"
    : > "$_in_tmpcfg"
    [ "$_in_semgrep"  -eq 1 ] && printf 'SEMGREP_BIN=%s\nSEMGREP_VERSION=%s\n'   "$AEGIS_TOOLBENCH_ROOT/bin/semgrep"  "$SEMGREP_VERSION"  >> "$_in_tmpcfg" || true
    [ "$_in_gitleaks" -eq 1 ] && printf 'GITLEAKS_BIN=%s\nGITLEAKS_VERSION=%s\n' "$AEGIS_TOOLBENCH_ROOT/bin/gitleaks" "$GITLEAKS_VERSION" >> "$_in_tmpcfg" || true
    [ "$_in_cppcheck" -eq 1 ] && printf 'CPPCHECK_BIN=%s\nCPPCHECK_VERSION=%s\n' "$AEGIS_TOOLBENCH_ROOT/bin/cppcheck" "$CPPCHECK_VERSION" >> "$_in_tmpcfg" || true
    [ "$_in_trivy"    -eq 1 ] && printf 'TRIVY_BIN=%s\nTRIVY_VERSION=%s\n'       "$AEGIS_TOOLBENCH_ROOT/bin/trivy"    "$TRIVY_VERSION"    >> "$_in_tmpcfg" || true
    [ -n "$_in_ct" ] && printf 'CLANG_TIDY_BIN=%s\n' "$_in_ct" >> "$_in_tmpcfg" || true
    aegis_verify_install "$_in_tmpcfg" || {
        rm -f "$_in_tmpcfg"
        rm -rf "$_in_stage"
        echo "VERIFY FAILED — config discarded, probe stays cold"
        exit 1
    }
    mv "$_in_tmpcfg" "$AEGIS_CONFIG_HOME/$AEGIS_LIB_CONFIG"

    rm -rf "$_in_stage"
    _aegis_clear_attempts
    trap - EXIT
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
case "$0" in
    *sast-toolchain.lib.sh)
        set -eu
        case "${1:-}" in
            probe)    aegis_readiness_probe; exit $? ;;
            install)  aegis_install_all ;;
            reversal) aegis_reversal ;;
            *) echo "usage: $0 probe|install|reversal" >&2; exit 64 ;;
        esac
        ;;
esac
