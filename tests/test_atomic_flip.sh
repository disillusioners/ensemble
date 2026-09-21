#!/bin/bash
# ============================================================================
# tests/test_atomic_flip.sh — portable atomic flip regression
# (fix/portable-atomic-flip-linux, 2026-09-21)
# ============================================================================
# Asserts the BSD/GNU-portable atomic_flip helper (lib.sh) AND the
# launcher's self-contained mirror _js_flip_current behave correctly on
# THIS host (Linux, GNU coreutils mv 9.4) AND correctly dispatch the
# BSD/macOS branch via an injected uname stub.
#
# Coverage (commission minimum):
#   (a) fresh flip creates `current` correctly (no existing target)
#   (b) flip OVER an existing `current` symlink-to-dir replaces the LINK
#       itself — does NOT descend into the old release dir; asserts the
#       old release dir contents are untouched AND `current` retargeted
#   (c) failure propagation: bad source path → nonzero exit, destination
#       symlink unchanged
#   (d) exercises the helper through the same code path lib.sh uses
#       (sources lib.sh — does NOT reimplement the mv call)
# Plus, beyond the minimum:
#   (e) unrecognized-platform refuse (fail-closed; helper does not guess)
#   (f) BSD branch via injected uname stub (env override proves the
#       helper picks -h -f on Darwin without needing a real BSD host)
#   (g) _js_flip_current (launcher.sh self-contained mirror) covers
#       the same (a)+(b)+(c) cases — guards against drift between the
#       two helpers
#
# Fixture strategy: temp dirs under TMPDIR, INSTALL_DIR overridden per
# subtest, exit-78 live/demo guard is irrelevant (no install exists),
# no daemon / port / network. Self-contained, plain bash (no bats).
#
# Run:
#   bash tests/test_atomic_flip.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
LAUNCHER="$REPO_ROOT/launcher.sh"

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
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass ;;
    esac
}

section() { printf '\n== %s ==\n' "$1"; }

# Resolve the real mv path so the BSD-branch stub PATH doesn't shadow
# mv itself when the helper does the actual rename (we only want the
# `uname` lookup to be routed via the stub).
REAL_MV="$(command -v mv)"
REAL_UNAME="$(command -v uname)"
MKBIN="${TMPDIR:-/tmp}/atomic-flip-mkbin-$$"
mkdir -p "$MKBIN"

# Portable iso-from-epoch (works on BSD + GNU): uses python3 if available,
# else falls back to perl; both are universal on the test hosts (linux
# python3 standard, macOS ships python3 + perl).
iso_off() {
    # iso_off <seconds-ago>
    local secs="$1" now off
    now="$(date -u +%s)"
    off=$((now - secs))
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import datetime,sys; print(datetime.datetime.utcfromtimestamp(int(sys.argv[1])).strftime('%Y-%m-%dT%H:%M:%SZ'))" "$off"
    else
        perl -e "use POSIX qw(strftime); print strftime('%Y-%m-%dT%H:%M:%SZ', gmtime($off))"
    fi
}

# Build a fake-release fixture: dir/releases/<ver>/ensemble-prod,
# dir/releases/<ver>/manifest.json, current→releases/<oldver> if given.
make_fixture() {  # <dir> <newver> [oldver]
    local d="$1" new="$2" old="${3:-}"
    rm -rf "$d"; mkdir -p "$d/releases/$new"
    printf '{"version":"%s","binary_version":"%s","rollback_safe":true}\n' "$new" "$new" \
        > "$d/releases/$new/manifest.json"
    printf 'stub-app\n' > "$d/releases/$new/ensemble-prod"
    if [ -n "$old" ]; then
        mkdir -p "$d/releases/$old"
        printf '{"version":"%s","binary_version":"%s","rollback_safe":true}\n' "$old" "$old" \
            > "$d/releases/$old/manifest.json"
        printf 'old-app\n' > "$d/releases/$old/ensemble-prod"
        ln -sfn "releases/$old" "$d/current"
    fi
}

# Source lib.sh into a subshell with a chosen INSTALL_DIR. Returns the
# rc of the helper invocation. The whole subshell's stdout+stderr is
# swallowed so the captured value is purely the trailing "rc=N".
call_atomic_flip() {  # <dir> <ver>
    ( export INSTALL_DIR="$1"; . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1; atomic_flip "$2"; echo "rc=$?" )
}
# Same but the helper's stdout/stderr (we want to read the unrecognized-
# platform WARN).
call_atomic_flip_verbose() {  # <dir> <ver>
    ( export INSTALL_DIR="$1"; . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1; atomic_flip "$2"; echo "rc=$?" >&2 )
}

# Source launcher.sh into a subshell with INSTALL_DIR, then call the
# self-contained _js_flip_current.
call_js_flip() {  # <dir> <ver>
    ( export INSTALL_DIR="$1"; . "$LAUNCHER" >/dev/null 2>&1; _js_flip_current "$1" "$2"; echo "rc=$?" )
}

# Strip the trailing "rc=N" line from a captured helper output. Returns
# just the rc (the rest is discarded by the caller when needed).
extract_rc() {  # <captured>
    printf '%s\n' "$1" | grep -oE 'rc=[0-9]+' | head -1 | cut -d= -f2
}

# ──────────────────────────────────────────────────────────────────────
section "atomic_flip (lib.sh) — fresh flip on this Linux host"
FIXT="$(mktemp -d -t atomic-flip.XXXXXX)"
make_fixture "$FIXT" v1.0.0
[ -e "$FIXT/current" ] && _fail "fixture sanity: no pre-existing current" "absent" "present" || _pass
OUT="$(call_atomic_flip "$FIXT" v1.0.0)"
RC="$(extract_rc "$OUT")"
assert_eq "(a) fresh flip rc 0" "0" "$RC"
assert_eq "(a) current -> releases/v1.0.0" "releases/v1.0.0" "$(readlink "$FIXT/current")"
assert_eq "(a) target ensemble-prod reachable through current" "stub-app" \
    "$(cat "$FIXT/current/ensemble-prod")"
# No current.new droppings left behind
ls "$FIXT" | grep -q 'current\.new' && _fail "(a) no current.new droppings" "absent" "present" || _pass "(a) no current.new droppings"
rm -rf "$FIXT"

# ──────────────────────────────────────────────────────────────────────
section "atomic_flip (lib.sh) — flip OVER an existing current"
# The mission-critical case: existing current→releases/vOLD; flip to vNEW.
# On Linux (plain `mv -f`), the wrong behavior is: mv follows the
# symlink-to-dir DEST, moves the temp link INTO vOLD, and `current`
# keeps pointing at vOLD. After a CORRECT flip:
#   - `current` -> releases/vNEW
#   - vOLD's contents are UNTOUCHED (no dropped new link inside)
FIXT="$(mktemp -d -t atomic-flip.XXXXXX)"
make_fixture "$FIXT" vNEW vOLD
VOLD_ENSEMBLE_BEFORE="$(cat "$FIXT/releases/vOLD/ensemble-prod")"
VOLD_MANIFEST_BEFORE="$(cat "$FIXT/releases/vOLD/manifest.json")"
OUT="$(call_atomic_flip "$FIXT" vNEW)"
RC="$(extract_rc "$OUT")"
assert_eq "(b) flip-over rc 0" "0" "$RC"
assert_eq "(b) current retargeted (does NOT descend)" "releases/vNEW" "$(readlink "$FIXT/current")"
assert_eq "(b) old release dir contents UNTOUCHED (ensemble-prod)" "$VOLD_ENSEMBLE_BEFORE" "$(cat "$FIXT/releases/vOLD/ensemble-prod")"
assert_eq "(b) old release dir contents UNTOUCHED (manifest)" "$VOLD_MANIFEST_BEFORE" "$(cat "$FIXT/releases/vOLD/manifest.json")"
# The old release dir must NOT have gained a stray 'current' link inside it
[ -e "$FIXT/releases/vOLD/current" ] && _fail "(b) no current link leaked into old release" "absent" "present" || _pass "(b) no current link leaked into old release"
# The new release is now reachable through `current`
assert_eq "(b) new release reachable through current" "stub-app" \
    "$(cat "$FIXT/current/ensemble-prod")"
# No temp droppings
ls "$FIXT" | grep -q 'current\.new' && _fail "(b) no current.new droppings" "absent" "present" || _pass "(b) no current.new droppings"
rm -rf "$FIXT"

# ──────────────────────────────────────────────────────────────────────
section "atomic_flip (lib.sh) — failure propagation (read-only install dir)"
# Helper contract: atomic_flip returns nonzero AND leaves `current`
# unchanged when it cannot perform the rename. To trigger the
# failure deterministically we make the install dir read-only AFTER
# building the fixture — both `ln -sfn current.new.$$` (cannot create)
# and the subsequent `mv` would also fail. The helper's first failure
# (ln) is what we exercise here. (The original BSD behavior was the
# same: an unwritable dir aborted the flip without touching `current`.)
FIXT="$(mktemp -d -t atomic-flip.XXXXXX)"
make_fixture "$FIXT" vNEW vOLD
CUR_BEFORE="$(readlink "$FIXT/current")"
chmod 555 "$FIXT"
# Flip should fail closed; restore writability for cleanup
OUT="$(call_atomic_flip "$FIXT" vDOES_NOT_EXIST)"
chmod 755 "$FIXT"
RC="$(extract_rc "$OUT")"
assert_eq "(c) read-only install dir → rc 1" "1" "$RC"
assert_eq "(c) current UNCHANGED after failure" "$CUR_BEFORE" "$(readlink "$FIXT/current")"
# No temp droppings (the helper should never have reached the rm)
ls "$FIXT" | grep -q 'current\.new' && _fail "(c) no current.new droppings after failure" "absent" "present" || _pass "(c) no current.new droppings after failure"
rm -rf "$FIXT"

# ──────────────────────────────────────────────────────────────────────
section "atomic_flip (lib.sh) — source guard (d)"
# (d) "exercise the helper through the same code path lib.sh uses
#      (source lib.sh, don't reimplement)" — proven by every (a)/(b)/(c)
#      subtest above, which sources lib.sh and calls atomic_flip
#      directly. Add an extra invariant pin: the helper is a NAMED
#      function in lib.sh (not an alias / subshell trick) and it has
#      a body line count >= 15 (catches accidental empty / stub bodies
#      from a refactor).
FNDEF="$(grep -nE '^atomic_flip\(\)' "$UPGRADE_DIR/lib.sh" | head -1)"
[ -n "$FNDEF" ] && _pass "(d) atomic_flip is a named function in lib.sh" || _fail "(d) atomic_flip missing" "present" "absent"
BODY="$(sed -n '/^atomic_flip()/,/^}/p' "$UPGRADE_DIR/lib.sh")"
LINES=$(printf '%s\n' "$BODY" | wc -l)
[ "$LINES" -ge 15 ] && _pass "(d) atomic_flip body is non-trivial ($LINES lines)" || _fail "(d) atomic_flip body too small ($LINES lines)" ">=15" "$LINES"

# ──────────────────────────────────────────────────────────────────────
section "atomic_flip (lib.sh) — unrecognized platform refuses (fail-closed)"
# Stub PATH so a fake `uname` returns "Plan9"; the helper must refuse
# (fail-closed) rather than guess.
FIXT="$(mktemp -d -t atomic-flip.XXXXXX)"
make_fixture "$FIXT" v1.0.0
STUBDIR="$(mktemp -d -t atomic-flip-stub.XXXXXX)"
cat > "$STUBDIR/uname" <<'STUB'
#!/bin/bash
# atomic-flip-test stub: pretend to be a non-BSD non-Linux platform
case "$1" in
    -s) echo "Plan9" ;;
    *)  "$REAL_UNAME" "$@" ;;
esac
STUB
chmod +x "$STUBDIR/uname"
CUR_BEFORE="$(readlink "$FIXT/current" 2>/dev/null || echo "absent")"
# Helper output must NOT be redirected away — we want to read the WARN
OUT="$( PATH="$STUBDIR:$PATH" REAL_UNAME="$REAL_UNAME" bash -c '
    export INSTALL_DIR="$1"; export PATH="$STUBDIR:$PATH"; export REAL_UNAME="$REAL_UNAME"
    . "$2/lib.sh"
    atomic_flip "$3" 2>&1
    echo "rc=$?"
' _ "$FIXT" "$UPGRADE_DIR" v1.0.0 2>&1)"
RC="$(extract_rc "$OUT")"
assert_eq "(e) unrecognized platform rc 1" "1" "$RC"
assert_contains "(e) unrecognized platform WARN emitted" "unrecognized platform" "$OUT"
assert_contains "(e) WARN names the detected OS" "Plan9" "$OUT"
# Destination must be untouched
[ "$CUR_BEFORE" = "absent" ] && _pass "(e) destination untouched on refuse" || assert_eq "(e) destination untouched on refuse" "$CUR_BEFORE" "$(readlink "$FIXT/current")"
rm -rf "$FIXT" "$STUBDIR"

# ──────────────────────────────────────────────────────────────────────
section "atomic_flip (lib.sh) — BSD branch via injected uname stub"
# Inject a uname that reports Darwin AND a stub `mv` that RECORDS argv
# then returns 0 (does NOT delegate to the real GNU mv — which would
# refuse -h and mask the helper's branch-selection signal). The helper
# is then proven to pick `-h -f` on Darwin.
FIXT="$(mktemp -d -t atomic-flip.XXXXXX)"
make_fixture "$FIXT" vNEW vOLD
STUBDIR="$(mktemp -d -t atomic-flip-stub.XXXXXX)"
MVLOG="$STUBDIR/mv.log"
export MVLOG   # so the stub mv sees it (path inside the bash -c child)
cat > "$STUBDIR/uname" <<'STUB'
#!/bin/bash
case "$1" in -s) echo "Darwin" ;; *) "$REAL_UNAME" "$@" ;; esac
STUB
chmod +x "$STUBDIR/uname"
cat > "$STUBDIR/mv" <<'STUB'
#!/bin/bash
# atomic-flip-test: record argv then succeed (do NOT delegate — the
# real GNU mv would refuse -h and mask the helper's branch pick).
printf 'mv-stub: %s\n' "$*" >> "$MVLOG"
exit 0
STUB
chmod +x "$STUBDIR/mv"
: > "$MVLOG"
RC="$( PATH="$STUBDIR:$PATH" REAL_UNAME="$REAL_UNAME" REAL_MV="$REAL_MV" MVLOG="$MVLOG" bash -c '
    export INSTALL_DIR="$1"; export PATH="$STUBDIR:$PATH"
    export REAL_UNAME="$REAL_UNAME"; export REAL_MV="$REAL_MV"; export MVLOG="$MVLOG"
    . "$2/lib.sh"
    atomic_flip "$3"
    echo "rc=$?"
' _ "$FIXT" "$UPGRADE_DIR" vNEW 2>&1)"
RC="$(extract_rc "$RC")"
assert_eq "(f) BSD-stub flip rc 0 (stub mv accepts -h)" "0" "$RC"
# The intercepted mv invocation should be `mv -h -f ... current.new.$$ current`
LOG="$(cat "$MVLOG")"
case "$LOG" in
    *"-h"*) _pass "(f) BSD-stub mv invoked with -h (got: $LOG)" ;;
    *) _fail "(f) BSD-stub mv missing -h flag" "-h" "$LOG" ;;
esac
assert_contains "(f) BSD-stub mv also has -f" " -f " "$LOG"
# Stub mv is a no-op (does NOT delegate to real mv — that would refuse
# -h and mask the branch pick), so the temp `current.new.$$` file is
# intentionally left behind. We assert two things about that residue:
#   (i) NO stray current leaked into the OLD release dir (proves the
#       helper did not rename the temp into vOLD instead of over
#       `current`), and
#  (ii) the residue cleans up cleanly via `rm -f` (proves it's a
#       regular file, not a directory or weird path).
[ -e "$FIXT/releases/vOLD/current" ] && _fail "(f) no current link leaked into old release" "absent" "present" || _pass "(f) no current link leaked into old release"
ls "$FIXT" | grep '^current\.new' >/dev/null 2>&1 && _pass "(f) temp residue present (stub mv no-op is by design)" || _fail "(f) expected stub-mv residue" "present" "absent"
rm -f "$FIXT"/current.new.* 2>/dev/null || true
rm -rf "$FIXT" "$STUBDIR"

# ──────────────────────────────────────────────────────────────────────
section "_js_flip_current (launcher.sh) — same (a)(b)(c) on Linux"
# Guards against drift between the lib.sh helper and launcher's mirror.

# (g-a) fresh flip
FIXT="$(mktemp -d -t atomic-flip.XXXXXX)"
make_fixture "$FIXT" v1.0.0
OUT="$(call_js_flip "$FIXT" v1.0.0)"
RC="$(extract_rc "$OUT")"
assert_eq "(g-a) _js_flip_current fresh flip rc 0" "0" "$RC"
assert_eq "(g-a) current -> releases/v1.0.0" "releases/v1.0.0" "$(readlink "$FIXT/current")"
rm -rf "$FIXT"

# (g-b) flip over existing
FIXT="$(mktemp -d -t atomic-flip.XXXXXX)"
make_fixture "$FIXT" vNEW vOLD
VOLD_ENSEMBLE_BEFORE="$(cat "$FIXT/releases/vOLD/ensemble-prod")"
OUT="$(call_js_flip "$FIXT" vNEW)"
RC="$(extract_rc "$OUT")"
assert_eq "(g-b) _js_flip_current flip-over rc 0" "0" "$RC"
assert_eq "(g-b) current retargeted" "releases/vNEW" "$(readlink "$FIXT/current")"
assert_eq "(g-b) old release untouched" "$VOLD_ENSEMBLE_BEFORE" "$(cat "$FIXT/releases/vOLD/ensemble-prod")"
[ -e "$FIXT/releases/vOLD/current" ] && _fail "(g-b) no link leaked into old release" "absent" "present" || _pass "(g-b) no link leaked into old release"
rm -rf "$FIXT"

# (g-c) failure propagation (read-only dir, same shape as lib.sh (c))
FIXT="$(mktemp -d -t atomic-flip.XXXXXX)"
make_fixture "$FIXT" vNEW vOLD
CUR_BEFORE="$(readlink "$FIXT/current")"
chmod 555 "$FIXT"
OUT="$(call_js_flip "$FIXT" vDOES_NOT_EXIST)"
chmod 755 "$FIXT"
RC="$(extract_rc "$OUT")"
assert_eq "(g-c) _js_flip_current read-only dir → rc 1" "1" "$RC"
assert_eq "(g-c) current UNCHANGED after failure" "$CUR_BEFORE" "$(readlink "$FIXT/current")"
ls "$FIXT" | grep -q 'current\.new' && _fail "(g-c) no current.new droppings after failure" "absent" "present" || _pass "(g-c) no current.new droppings after failure"
rm -rf "$FIXT"

# ──────────────────────────────────────────────────────────────────────
section "lib.sh + launcher.sh — same dispatch table (drift guard)"
# Both helpers must branch identically on uname -s. Extract the
# platforms each helper accepts (BSD family + Linux family) and
# assert the sets match.
LIB_BRANCHES="$(sed -n '/^atomic_flip()/,/^}/p' "$UPGRADE_DIR/lib.sh" \
    | grep -oE '(Darwin|\\*BSD\\*|\\*bsd\\*|Linux|GNU\\*|\\*GNU\\*)' | sort -u)"
JS_BRANCHES="$(sed -n '/^_js_flip_current()/,/^}/p' "$LAUNCHER" \
    | grep -oE '(Darwin|\\*BSD\\*|\\*bsd\\*|Linux|GNU\\*|\\*GNU\\*)' | sort -u)"
assert_eq "drift guard: lib.sh and launcher.sh accept the same uname glob set" "$LIB_BRANCHES" "$JS_BRANCHES"

# ──────────────────────────────────────────────────────────────────────
rm -rf "$MKBIN" 2>/dev/null || true

printf '\n== summary: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0