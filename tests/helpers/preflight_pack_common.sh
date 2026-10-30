#!/usr/bin/env bash
# tests/helpers/preflight_pack_common.sh — fence + shared utilities for the
# promote-preflight-hang packs (wave 1: phases 0 and 1).
#
# FENCE CONTRACT (phase0 T0.7/AC7, canonical text in phase5 §3):
#   * a scratch install root under `mktemp -d` — NEVER a live-shaped path;
#   * a `mktemp -d` FAKE_HOME plus an ESCAPE GUARD on both (the 2026-10-12
#     incident deleted two live installs from an unfenced fixture);
#   * per-variable POSTGRES_* scrub — the `env -u POSTGRES_*` idiom is a
#     SILENT NO-OP in bash (it unsets nothing: bash does not glob env names)
#     and therefore appears NOWHERE in this lane's test code (grep-asserted);
#   * an explicit PORT, no daemon boot, no live promote, no live install
#     resolution, no `git clean`, no `rm -rf` on a path that was not
#     produced by this helper's own mktemp.
#
# The BASE oracle is built from the pinned instrumented rev's *verbatim*
# function bodies (`git show <rev>:scripts/upgrade/lib.sh`), so the parity
# assertion is "new == old", not "new == my reading of old".
#
# Callers: `. "$(dirname "$0")/../helpers/preflight_pack_common.sh"`

# ── the pinned rev the oracle is extracted from ──────────────────────────────
# b1183a637 = v0.27.13 bump = origin/latest at lane open (the incident rev's
# successor, carrying the O(N^2) scanner byte-identically).
PF_BASE_REV="${PF_BASE_REV:-b1183a637649f94c23b94dfc745df44381dc54ab}"

PF_PASS=0
PF_FAIL=0
PF_FAILED_NAMES=""

pf_die() { printf 'FATAL: %s\n' "$*" >&2; exit 2; }
pf_ok()  { PF_PASS=$((PF_PASS + 1)); printf '  ok   %s\n' "$*"; }
pf_bad() { PF_FAIL=$((PF_FAIL + 1)); PF_FAILED_NAMES="$PF_FAILED_NAMES
  - $*"; printf '  FAIL %s\n' "$*"; }
pf_note() { printf '  ·    %s\n' "$*"; }

pf_summary() {
    printf '\n%s: %d passed, %d failed\n' "${PF_PACK_NAME:-pack}" "$PF_PASS" "$PF_FAIL"
    if [ "$PF_FAIL" -ne 0 ]; then
        printf 'failures:%s\n' "$PF_FAILED_NAMES"
        return 1
    fi
    return 0
}

pf_sha256() { shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'; }
pf_bytes()  { wc -c < "$1" 2>/dev/null | tr -d ' '; }

# ── the fence ───────────────────────────────────────────────────────────────

pf_assert_not_live() {
    # Refuse to operate on a live-shaped install root. The worktree itself
    # (/home/nea/ensemble-src-wt-*) is legitimate; the MAIN checkout and the
    # live install are not.
    case "${1:-}" in
        "") pf_die "pf_assert_not_live: empty path" ;;
        /home/nea/agents-ensemble|/home/nea/agents-ensemble/*)
            pf_die "REFUSING a live install path: $1" ;;
        /home/nea/ensemble-src|/home/nea/ensemble-src/*)
            pf_die "REFUSING the main checkout path: $1" ;;
        */.git|*/.git/*)
            pf_die "REFUSING a path inside .git: $1" ;;
    esac
    if [ -n "${PF_REAL_HOME:-}" ] && [ -n "${1:-}" ]; then
        case "$1" in
            "$PF_REAL_HOME"/agents-ensemble|"$PF_REAL_HOME"/agents-ensemble/*)
                pf_die "REFUSING a HOME-derived live install path: $1" ;;
        esac
    fi
    return 0
}

pf_fence_init() {
    PF_REAL_HOME="${HOME:-/nonexistent}"
    PF_SB="$(mktemp -d "${TMPDIR:-/tmp}/pf-sandbox.XXXXXX")" || pf_die "mktemp -d failed"
    PF_FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/pf-home.XXXXXX")" || pf_die "mktemp -d failed"
    pf_assert_not_live "$PF_SB"
    pf_assert_not_live "$PF_FAKE_HOME"

    # per-variable scrub (NOT `env -u POSTGRES_*` — silent NO-OP)
    unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
    unset DATABASE_URL DATABASE_URL_RO
    unset ENSEMBLE_SELF_ENV ENSEMBLE_UPGRADE_LIVE ENSEMBLE_RESTART_LIVE
    unset ENSEMBLE_UPGRADE_ALLOW_STALE
    unset JOURNAL_READ_STRICT JOURNAL_SIZE_GUARD_BYTES JOURNAL_READ_HELPER
    unset SPLICE_PYTHON
    unset UP_TARGET LOG_TAG

    export HOME="$PF_FAKE_HOME"
    export PORT=8079
    export LOG_TAG=pf-pack
    export INSTALL_DIR="$PF_SB/install"
    mkdir -p "$INSTALL_DIR/releases" || pf_die "cannot create scratch install root"

    # Escape guard self-test: a live-shaped path MUST be refused.
    if ( pf_assert_not_live /home/nea/agents-ensemble/releases >/dev/null 2>&1 ); then
        pf_die "escape guard failed its own self-test (a live path was accepted)"
    fi
    return 0
}

pf_fence_cleanup() {
    # Only ever removes paths this helper created (both are mktemp products,
    # already asserted non-live at creation).
    [ -n "${PF_SB:-}" ] && case "$PF_SB" in "${TMPDIR:-/tmp}"/pf-sandbox.*) rm -rf "$PF_SB" ;; esac
    [ -n "${PF_FAKE_HOME:-}" ] && case "$PF_FAKE_HOME" in "${TMPDIR:-/tmp}"/pf-home.*) rm -rf "$PF_FAKE_HOME" ;; esac
}

# ── the BASE oracle (verbatim extraction from git, never re-typed) ──────────

_pf_extract_fns() {
    # _pf_extract_fns <lib.sh-file> <fn> [<fn>...] -> prints the bodies
    local lib="$1"; shift
    /usr/bin/python3 - "$lib" "$@" <<'PY'
import re, sys
path = sys.argv[1]
names = sys.argv[2:]
text = open(path, "r", encoding="utf-8", errors="surrogateescape").read()
lines = text.splitlines(keepends=True)
for name in names:
    pat = re.compile(r"^%s\(\) \{\s*$" % re.escape(name))
    out = []
    for i, line in enumerate(lines):
        if pat.match(line):
            out = [line]
            for nxt in lines[i + 1:]:
                out.append(nxt)
                if nxt.rstrip("\n") == "}":
                    break
            break
    if not out:
        sys.stderr.write("extract: function %s not found in %s\n" % (name, path))
        sys.exit(1)
    sys.stdout.write("".join(out) + "\n")
PY
}

# pf_base_oracle <out-path> — write the BASE oracle script.
# The ONLY harness substitution is `journal_path()`, replaced with a fixed
# value: it is a one-line formatter over $INSTALL_DIR, so pinning the path is
# behaviourally identical and makes the oracle path-agnostic.
pf_base_oracle() {
    local out="$1" lib tmp
    lib="$PF_SB/base-lib.sh"
    ( cd "$PF_REPO_ROOT" && git show "$PF_BASE_REV:scripts/upgrade/lib.sh" > "$lib" ) \
        || pf_die "cannot read $PF_BASE_REV:scripts/upgrade/lib.sh (git objects required)"
    tmp="$PF_SB/oracle-fns.sh"
    _pf_extract_fns "$lib" journal_read journal_write _json_has journal_update _json_sub > "$tmp" \
        || pf_die "oracle extraction failed"
    {
        printf '%s\n' '#!/usr/bin/env bash'
        printf '%s\n' '# GENERATED — verbatim BASE bodies from '"$PF_BASE_REV"':scripts/upgrade/lib.sh'
        printf '%s\n' 'set -u'
        printf '%s\n' 'LOG_TAG="${LOG_TAG:-oracle}"'
        printf '%s\n' 'UP_TARGET="${UP_TARGET:-lib}"'
        printf '%s\n' '_warn() { printf '"'"'%s[%s]: WARN: %s\n'"'"' "$LOG_TAG" "${UP_TARGET:-lib}" "$*" >&2; }'
        printf '%s\n' 'JR_PATH=""'
        printf '%s\n' 'journal_path() { printf '"'"'%s'"'"' "$JR_PATH"; }'
        cat "$tmp"
        cat <<'DISPATCH'
op="${1:-}"; shift || true
case "$op" in
    verify)
        JR_PATH="$1"
        journal_read
        ;;
    sub)
        JR_PATH=/dev/null
        json="$(cat)"
        _json_sub "$json" "$1"
        ;;
    update)
        # full BASE RMW: read -> splice -> atomic write, then echo the file
        JR_PATH="$1"
        journal_update "$2" "$3" || exit $?
        cat "$JR_PATH"
        ;;
    *)
        echo "usage: oracle verify <file> | sub <key> | update <file> <field> <raw>" >&2
        exit 2
        ;;
esac
DISPATCH
    } > "$out"
    chmod +x "$out"
    return 0
}

# ── bounded runner (house primitive shape: TERM -> 5 s -> KILL -> 124) ──────

pf_run_bounded() {
    local timeout_s="$1"; shift
    [ "${1:-}" = "--" ] && shift
    local pid watcher rc=0
    "$@" &
    pid=$!
    ( sleep "$timeout_s" 2>/dev/null
      kill -TERM "$pid" 2>/dev/null
      sleep 5 2>/dev/null
      kill -KILL "$pid" 2>/dev/null ) >/dev/null 2>&1 &
    watcher=$!
    if wait "$pid" 2>/dev/null; then rc=0; else rc=$?; fi
    case "$rc" in 137|143) rc=124 ;; esac
    kill -KILL "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    return "$rc"
}

# ── repo/root resolution + fixture helpers ──────────────────────────────────

PF_PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PF_REPO_ROOT="$(cd "$PF_PACK_DIR/../.." && pwd)"
PF_FIXTURES="$PF_REPO_ROOT/tests/fixtures/journal"

pf_incident_fixture() {
    # pf_incident_fixture <out-path> — deterministic 455,783 B / 2,949-entry
    # incident-shaped journal; asserts the recorded sha256 (loud on drift).
    local out="$1"
    /usr/bin/python3 "$PF_REPO_ROOT/tests/helpers/journal_fixture_incident.py" --out "$out" >/dev/null \
        || pf_die "incident fixture generation failed"
    local want
    want="$(cat "$PF_FIXTURES/INCIDENT.sha256" 2>/dev/null | awk '{print $1}')"
    if [ -n "$want" ]; then
        local got; got="$(pf_sha256 "$out")"
        [ "$got" = "$want" ] || pf_die "incident fixture sha256 drift: got $got want $want"
    fi
    return 0
}

pf_corpus_fixtures() {
    # prints the corpus fixture paths, one per line, sorted
    find "$PF_FIXTURES" -maxdepth 1 -name '*.json' -type f | sort
}

# stderr class normalisation: the NUL-drop warning carries a script name and
# a line number (bash's wording varies by version), so it is classified, not
# compared byte-for-byte; every OTHER stderr line is compared verbatim (the
# `_warn` prefix is reproduced by the helper).
pf_stderr_class() {
    local f="$1"
    if [ ! -s "$f" ]; then printf 'none'; return; fi
    if grep -q 'ignored null byte' "$f" 2>/dev/null; then printf 'nul-warning'; return; fi
    if grep -q 'is EMPTY (torn write?)' "$f" 2>/dev/null; then printf 'empty'; return; fi
    if grep -q 'is MALFORMED (torn write: unbalanced json)' "$f" 2>/dev/null; then printf 'malformed'; return; fi
    printf 'other:%s' "$(head -1 "$f")"
}

pf_stderr_without_nul_warning() {
    grep -v 'ignored null byte' "$1" 2>/dev/null || true
}

# pf_now_ms — epoch milliseconds (BSD-safe: `date +%s%N` is GNU-only).
pf_now_ms() { /usr/bin/python3 -c 'import time; print(int(time.time() * 1000))'; }

# pf_helper — the O(N) read helper path.
pf_helper() { printf '%s' "$PF_REPO_ROOT/scripts/upgrade/journal_read.py"; }
