#!/bin/sh
# fuzzing-toolchain.lib.sh — POSIX-sh installer library for the offensive
# lane's fuzzing toolchain: the libFuzzer primary path (clang/clang++ with
# sanitizer-plus-fuzzer instrumentation) and the AFL++ secondary path for
# non-instrumented targets. Sourced by the owning lane's installer skill,
# or executed directly with a command: probe | install | reversal.
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
#
# Companion: fuzzing-toolchain.md (the installer skill body, same directory)

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
AEGIS_LIB_CONFIG="fuzzing.env"
AEGIS_LOCK_STALE_SECS="${AEGIS_LOCK_STALE_SECS:-1800}"
AEGIS_ATTEMPT_CAP=2

# --- Pinned release assets (class-(i) install inputs) --------------------
# The sha256 pins are REQUIRED before first install: fill them from the
# vendor's checksum material at pin time. An unset or placeholder pin
# makes aegis_verify_sha256 refuse — an unpinned fetch never promotes.
AFLPP_VERSION="${AFLPP_VERSION:-4.10c}"
AFLPP_URL="${AFLPP_URL:-https://github.com/AFLplusplus/AFLplusplus/releases/download/v4.10c/aflplusplus-4.10c.tar.gz}"
AFLPP_SHA256="${AFLPP_SHA256:-pin-before-first-install}"

# --- Internals ------------------------------------------------------------
_aegis_lock_dir() { printf '%s\n' "$AEGIS_STATE_HOME/fuzzing-install.lock"; }
_aegis_attempts_file() { printf '%s\n' "$AEGIS_STATE_HOME/fuzzing-session-state"; }

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

# --- Tool-specific evidence queries (existence is not evidence) -----------
aegis_bin_answers() {
    _aba_bin="$1"
    case "$(basename "$_aba_bin")" in
        clang|clang++|clang-tidy)
            "$_aba_bin" --version >/dev/null 2>&1 ;;
        afl-fuzz)
            "$_aba_bin" -h 2>&1 | grep -q "afl-fuzz" ;;
        *)
            "$_aba_bin" --version >/dev/null 2>&1 ;;
    esac
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

    # prereqs are prerequisites, never install targets
    command -v curl >/dev/null 2>&1 || { echo "HARD_BLOCKER: curl"; exit 1; }
    command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || { echo "HARD_BLOCKER: sha256 tool"; exit 1; }
    command -v clang >/dev/null 2>&1 || { echo "HARD_BLOCKER: clang (libFuzzer primary path needs the system clang)"; exit 1; }
    command -v clang++ >/dev/null 2>&1 || { echo "HARD_BLOCKER: clang++"; exit 1; }

    _aegis_ensure_dirs
    _in_stage="$(mktemp -d)"
    trap 'rm -rf "$_in_stage"; aegis_release_install_lock' EXIT INT TERM

    echo "fetching AFL++@$AFLPP_VERSION ..."
    _aegis_fetch "$AFLPP_URL" "$_in_stage/aflpp.tar.gz"
    # sha256 gate BEFORE any promotion — mismatch aborts, deletes, promotes nothing
    aegis_verify_sha256 "$_in_stage/aflpp.tar.gz" "$AFLPP_SHA256" || {
        rm -rf "$_in_stage"
        echo "VERIFY FAILED — staged asset discarded, nothing promoted"
        exit 1
    }
    tar -xzf "$_in_stage/aflpp.tar.gz" -C "$_in_stage"
    if [ -f "$_in_stage"/aflplusplus-*/afl-fuzz ]; then
        cp "$_in_stage"/aflplusplus-*/afl-fuzz "$AEGIS_TOOLBENCH_ROOT/bin/afl-fuzz"
        chmod 0755 "$AEGIS_TOOLBENCH_ROOT/bin/afl-fuzz"
    else
        rm -rf "$_in_stage"
        echo "HARD_BLOCKER: no verified release binary for afl-fuzz@$AFLPP_VERSION (vendor ships source-only) — never source-build from an unpinned tree"
        exit 1
    fi

    # staged config -> verify -> promote (probe-visible config LAST)
    _in_tmpcfg="$(mktemp)"
    printf 'CLANG_BIN=%s\nCLANGXX_BIN=%s\nAFLPP_BIN=%s\nAFLPP_VERSION=%s\n' \
        "$(command -v clang)" "$(command -v clang++)" \
        "$AEGIS_TOOLBENCH_ROOT/bin/afl-fuzz" "$AFLPP_VERSION" > "$_in_tmpcfg"
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
    echo "fuzzing-toolchain: OK (afl-fuzz=$AEGIS_TOOLBENCH_ROOT/bin/afl-fuzz, clang=$(command -v clang))"
}

# --- Reversal (uninstall — leaves the host as it was found) ---------------
aegis_reversal() {
    rm -f "$AEGIS_CONFIG_HOME/$AEGIS_LIB_CONFIG"
    rm -f "$AEGIS_TOOLBENCH_ROOT/bin/afl-fuzz"
    rm -f "$(_aegis_attempts_file)"
    rm -rf "$(_aegis_lock_dir)"
    echo "fuzzing-toolchain: reversed (system clang/clang++ were prerequisites — untouched)"
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
    *fuzzing-toolchain.lib.sh)
        set -eu
        case "${1:-}" in
            probe)    aegis_readiness_probe; exit $? ;;
            install)  aegis_install_all ;;
            reversal) aegis_reversal ;;
            *) echo "usage: $0 probe|install|reversal" >&2; exit 64 ;;
        esac
        ;;
esac
