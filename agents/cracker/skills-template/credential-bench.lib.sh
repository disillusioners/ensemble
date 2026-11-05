#!/bin/sh
# credential-bench.lib.sh — POSIX-sh installer library for the
# protection-strength lane's bench toolchain: hashcat and john. The bench
# runs on SYNTHETIC corpora only — this library has no path that fetches a
# wordlist or a real-world hash set, by design. Sourced by the owning
# lane's installer skill, or executed directly: probe | install | reversal.
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
#   - Synthetic corpora only: no network fetch of any wordlist or hash
#     corpus is possible from this library; the corpus path AND the
#     generator are both RESOLVED (canonicalized) and must land inside
#     the engagement sandbox root — the root and the corpus path's parent
#     directory must exist, and a traversing, symlinked or unresolvable
#     path is refused with HARD_BLOCKER before anything is created
#
# Export contract:
#   aegis_readiness_probe                     0 warm / 1 cold / 2 in-progress-other
#   aegis_verify_sha256 <file> <expected>     0 match / 1 mismatch-or-refusal
#   aegis_verify_install <cfg>                evidence gate before promotion
#   aegis_acquire_install_lock                0 acquired / 1 contended
#   aegis_release_install_lock                always succeeds
#   aegis_attempt_allowed / _bump_ / _clear_  self-heal cap of 2 per session
#   aegis_corpus_path_ok <path> <root>        0 resolved-inside-root / 1 refused
#   aegis_write_synthetic_corpus <path> <root> <generator>
#                                            0 written / 1 refused (HARD_BLOCKER)
#   aegis_bin_answers <bin>                   0 evidence query answered / 1 refused
#   aegis_install_all                         installs the toolchain; 0 on success
#   aegis_reversal                            removes the installed toolchain
#
# Companion: credential-bench.md (the installer skill body, same directory)

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
AEGIS_LIB_CONFIG="credential-bench.env"
AEGIS_LOCK_STALE_SECS="${AEGIS_LOCK_STALE_SECS:-1800}"
AEGIS_ATTEMPT_CAP=2

# --- Pinned release assets (class-(i) install inputs) --------------------
# The sha256 pins are REQUIRED before first install: fill them from the
# vendor's signed checksum material at pin time. An unset or placeholder
# pin makes aegis_verify_sha256 refuse — an unpinned fetch never promotes.
HASHCAT_VERSION="${HASHCAT_VERSION:-6.2.6}"
HASHCAT_URL="${HASHCAT_URL:-https://hashcat.net/files/hashcat-6.2.6.7z}"
HASHCAT_SHA256="${HASHCAT_SHA256:-pin-before-first-install}"

JOHN_VERSION="${JOHN_VERSION:-1.9.0-Jumbo-1}"
JOHN_URL="${JOHN_URL:-https://www.openwall.com/john/k/john-1.9.0-jumbo-1.tar.xz}"
JOHN_SHA256="${JOHN_SHA256:-pin-before-first-install}"

# --- Internals ------------------------------------------------------------
_aegis_lock_dir() { printf '%s\n' "$AEGIS_STATE_HOME/credential-bench-install.lock"; }
_aegis_attempts_file() { printf '%s\n' "$AEGIS_STATE_HOME/credential-bench-session-state"; }

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

# --- Synthetic-corpus discipline (no fetch, sandbox-only inputs) ----------
# The containment check CANONICALIZES both sides before comparing. A raw
# string-prefix test is not a containment test: it accepts every path that
# merely STARTS with the root string, so ".."-traversal and
# symlinked-directory escapes pass it (measured: "$ROOT/../outside/x" was
# accepted and the file landed outside the root; "$ROOT/evil/x" with
# $ROOT/evil a symlink to an outside dir likewise).
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

aegis_corpus_path_ok() {
    # usage: aegis_corpus_path_ok <path> <sandbox-root>
    # The corpus input must live INSIDE the engagement sandbox — the
    # library never creates corpora from anywhere else, and nothing is
    # ever fetched. A corpus path outside the sandbox is refused. An
    # UNRESOLVABLE path (root or corpus parent missing) is refused with
    # the precondition NAMED — never misdiagnosed as "outside the
    # sandbox".
    _cp_path="${1:-}"
    _cp_root="${2:-}"
    if [ -z "$_cp_path" ] || [ -z "$_cp_root" ]; then
        echo "HARD_BLOCKER: corpus path and engagement sandbox root are both required — synthetic benches read sandbox inputs only"
        return 1
    fi
    # non-normalized tails are refused with the problem NAMED, never left
    # to the kernel's EISDIR after the prefix comparison already passed
    case "$_cp_path" in
        */.|*/..|*/)
            echo "HARD_BLOCKER: corpus path ends in a directory segment ('/'), a self segment ('/.'), or a parent segment ('/..') — normalize the path"
            return 1
            ;;
    esac
    _cp_canon_root=$(cd -P "$_cp_root" 2>/dev/null && pwd -P) || {
        echo "HARD_BLOCKER: engagement sandbox root unresolvable — the root must exist before the corpus write"
        return 1
    }
    _cp_canon_path=$(_aegis_resolve_path "$_cp_path") || {
        echo "HARD_BLOCKER: corpus path unresolvable — the sandbox root and the corpus path's parent directory must both exist before the corpus write"
        return 1
    }
    case "$_cp_canon_path" in
        "$_cp_canon_root"/*) return 0 ;;
    esac
    echo "HARD_BLOCKER: corpus path outside the engagement sandbox — synthetic benches read sandbox inputs only"
    return 1
}

aegis_write_synthetic_corpus() {
    # usage: aegis_write_synthetic_corpus <corpus-path> <sandbox-root> <generator>
    # The ONLY sanctioned corpus write. Both the corpus path AND the
    # generator that produces it are RESOLVED and checked against the
    # engagement sandbox root BEFORE anything is written: an out-of-sandbox
    # path (or a generator from outside the sandbox) is a HARD_BLOCKER —
    # nothing is created and the bench does not run. This is what makes
    # "the corpus input path is always inside the sandbox" an enforced rule
    # rather than a promise; a corpus path alone cannot smuggle in material
    # that did not come from the lane's own generator.
    #
    # A missing argument is REFUSED, never fatal: the caller's shell must
    # survive so the refusal can be reported.
    #
    # C-1 (round 3): every path is resolved KERNEL-FAITHFULLY exactly
    # once, up front, and the CANONICAL forms are what the checks compare
    # AND what the write acts on — the raw caller path is never handed
    # back to the kernel at the effectful site. A lexical check and a
    # kernel write disagree exactly on the compound class (symlinked
    # parent + ".."): lexically the ".." cancels the symlink, physically
    # the kernel climbs out of the symlink's TARGET — one level outward.
    # Acting on the canonical path also closes the check-then-act window:
    # there is no second, independent resolution between check and write.
    _wc_path="${1:-}"
    _wc_root="${2:-}"
    _wc_gen="${3:-}"
    if [ -z "$_wc_path" ] || [ -z "$_wc_root" ] || [ -z "$_wc_gen" ]; then
        echo "HARD_BLOCKER: corpus path, engagement sandbox root and generator are all required — refusing the corpus write"
        return 1
    fi
    # non-normalized tails are refused with the problem NAMED, never left
    # to the kernel's EISDIR after the prefix comparison already passed
    case "$_wc_path" in
        */.|*/..|*/)
            echo "HARD_BLOCKER: corpus path ends in a directory segment ('/'), a self segment ('/.'), or a parent segment ('/..') — normalize the path"
            return 1
            ;;
    esac
    case "$_wc_gen" in
        */.|*/..|*/)
            echo "HARD_BLOCKER: corpus generator path ends in a directory segment ('/'), a self segment ('/.'), or a parent segment ('/..') — normalize the path"
            return 1
            ;;
    esac
    _wc_canon_root=$(cd -P "$_wc_root" 2>/dev/null && pwd -P) || {
        echo "HARD_BLOCKER: engagement sandbox root unresolvable — the root must exist before the corpus write; refusing the corpus write"
        return 1
    }
    _wc_canon_path=$(_aegis_resolve_path "$_wc_path") || {
        echo "HARD_BLOCKER: corpus path unresolvable — the sandbox root and the corpus path's parent directory must both exist before the corpus write"
        return 1
    }
    _wc_canon_gen=$(_aegis_resolve_path "$_wc_gen") || {
        echo "HARD_BLOCKER: corpus generator unresolvable — the generator's parent directory must exist inside the sandbox; refusing the corpus write"
        return 1
    }
    aegis_corpus_path_ok "$_wc_canon_path" "$_wc_canon_root" || return 1
    if ! _aegis_path_in_root "$_wc_canon_gen" "$_wc_canon_root"; then
        echo "HARD_BLOCKER: corpus generator outside the engagement sandbox — only generator-produced material enters the bench"
        return 1
    fi
    [ -f "$_wc_canon_gen" ] || {
        echo "HARD_BLOCKER: corpus generator not found — never source a corpus from elsewhere"
        return 1
    }
    sh "$_wc_canon_gen" > "$_wc_canon_path"
}

# --- Tool-specific evidence queries (existence is not evidence) -----------
aegis_bin_answers() {
    _aba_bin="$1"
    case "$(basename "$_aba_bin")" in
        john)
            "$_aba_bin" 2>&1 | grep -q "John the Ripper" ;;
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

    command -v curl >/dev/null 2>&1 || { echo "HARD_BLOCKER: curl"; exit 1; }
    command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || { echo "HARD_BLOCKER: sha256 tool"; exit 1; }

    _aegis_ensure_dirs
    _in_stage="$(mktemp -d)"
    trap 'rm -rf "$_in_stage"; aegis_release_install_lock' EXIT INT TERM

    echo "fetching hashcat@$HASHCAT_VERSION ..."
    _aegis_fetch "$HASHCAT_URL" "$_in_stage/hashcat.7z"
    # sha256 gate BEFORE any promotion — mismatch aborts, deletes, promotes nothing
    aegis_verify_sha256 "$_in_stage/hashcat.7z" "$HASHCAT_SHA256" || {
        rm -rf "$_in_stage"
        echo "VERIFY FAILED — staged asset discarded, nothing promoted"
        exit 1
    }
    command -v 7z >/dev/null 2>&1 || { rm -rf "$_in_stage"; echo "HARD_BLOCKER: 7z unpacker absent — never install one; report"; exit 1; }
    7z x -o"$_in_stage/hashcat" "$_in_stage/hashcat.7z" >/dev/null 2>&1
    if [ -f "$_in_stage"/hashcat/hashcat ]; then
        cp "$_in_stage"/hashcat/hashcat "$AEGIS_TOOLBENCH_ROOT/bin/hashcat"
        chmod 0755 "$AEGIS_TOOLBENCH_ROOT/bin/hashcat"
    else
        rm -rf "$_in_stage"
        echo "HARD_BLOCKER: no verified release binary for hashcat@$HASHCAT_VERSION — never source-build from an unpinned tree"
        exit 1
    fi

    # john: verified binary releases are vendor-dependent — an absent binary
    # is a per-tool HARD_BLOCKER (never a source build, never a real-corpus
    # workaround; the bench degrades to the tools that DID promote)
    _in_john_ok=0
    if [ "$JOHN_SHA256" != "pin-before-first-install" ]; then
        echo "fetching john@$JOHN_VERSION ..."
        if _aegis_fetch "$JOHN_URL" "$_in_stage/john.tar.xz" \
           && aegis_verify_sha256 "$_in_stage/john.tar.xz" "$JOHN_SHA256"; then
            tar -xJf "$_in_stage/john.tar.xz" -C "$_in_stage" 2>/dev/null || true
            if [ -f "$_in_stage"/john-*/run/john ]; then
                cp "$_in_stage"/john-*/run/john "$AEGIS_TOOLBENCH_ROOT/bin/john"
                chmod 0755 "$AEGIS_TOOLBENCH_ROOT/bin/john"
                _in_john_ok=1
            fi
        fi
    fi
    if [ "$_in_john_ok" -eq 0 ]; then
        echo "HARD_BLOCKER: no verified release binary for john@$JOHN_VERSION — bench degrades to hashcat only; never source-build"
    fi

    # staged config -> verify -> promote (probe-visible config LAST).
    # The staged config lives INSIDE the mktemp -d staging root (a
    # class-(i) path) — a bare `mktemp` in /tmp would be a class-(iii)
    # write, which the FENCES contract forbids ("never written, at any
    # time, by any path").
    _in_tmpcfg="$_in_stage/staged-config"
    printf 'HASHCAT_BIN=%s\nHASHCAT_VERSION=%s\n' \
        "$AEGIS_TOOLBENCH_ROOT/bin/hashcat" "$HASHCAT_VERSION" > "$_in_tmpcfg"
    if [ "$_in_john_ok" -eq 1 ]; then
        printf 'JOHN_BIN=%s\nJOHN_VERSION=%s\n' \
            "$AEGIS_TOOLBENCH_ROOT/bin/john" "$JOHN_VERSION" >> "$_in_tmpcfg"
    fi
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
    echo "credential-bench: OK (hashcat=$AEGIS_TOOLBENCH_ROOT/bin/hashcat, john=$_in_john_ok)"
}

# --- Reversal (uninstall — leaves the host as it was found) ---------------
aegis_reversal() {
    rm -f "$AEGIS_CONFIG_HOME/$AEGIS_LIB_CONFIG"
    rm -f "$AEGIS_TOOLBENCH_ROOT/bin/hashcat"
    rm -f "$AEGIS_TOOLBENCH_ROOT/bin/john"
    rm -f "$(_aegis_attempts_file)"
    rm -rf "$(_aegis_lock_dir)"
    echo "credential-bench: reversed"
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
    *credential-bench.lib.sh)
        set -eu
        case "${1:-}" in
            probe)    aegis_readiness_probe; exit $? ;;
            install)  aegis_install_all ;;
            reversal) aegis_reversal ;;
            *) echo "usage: $0 probe|install|reversal" >&2; exit 64 ;;
        esac
        ;;
esac
