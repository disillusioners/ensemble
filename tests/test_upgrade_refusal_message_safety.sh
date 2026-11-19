#!/bin/bash
# ============================================================================
# tests/test_upgrade_refusal_message_safety.sh
#
# THE DEFECT (upgrade-script refusal-message quoting lane):
#   scripts/upgrade/lib.sh `_verify_tip_identity` refused a non-tip staging
#   tree with an operator-facing message whose remediation text documented two
#   commands inside DOUBLE-QUOTED strings:
#
#       "or, if the local branch is ahead of origin: `git fetch origin` (a stale "
#       "or `git push origin <branch>:latest` first so the remote-tracking tip ..."
#
#   Bash performs command substitution inside double quotes, so BOTH commands
#   EXECUTED while the refusal was being assembled:
#     * `git fetch origin` really fetched (mutating the operator's repo);
#     * `git push origin <branch>:latest` was re-parsed by the inner shell as
#       `git push origin < branch > :latest` — `<branch>` is an INPUT
#       redirection (stdin) and `> :latest` is an OUTPUT redirection that
#       CREATES a file named `:latest` in the operator's CWD;
#     * and because the substitution's stdout REPLACED the backticked region,
#       the operator was shown a message with BOTH commands STRIPPED OUT.
#
#   A refusal is state-changing on the exact path a promote ceremony walks.
#
# WHAT THIS SUITE PINS
#   (a)  NON-EXECUTION — a planted canary proves the refusal still fires
#        (exit 78) while running NO command and creating NO `:latest` file,
#        and without mutating the fixture repo or its remote.
#   (a') DETECTION POWER — the same harness run against a MUTANT of the real
#        lib.sh (the pre-fix quoting) DOES exhibit those side effects, so (a)
#        is provably non-vacuous.
#   (b)  MESSAGE TEXT — the operator-facing refusal text is pinned
#        byte-for-byte against a golden fixture (SHA values masked), so a
#        future "simplification" cannot silently gut the remediation guidance.
#   (c)  CLASS GATE — a static sweep over scripts/upgrade/**.sh fails if a
#        dangerous command-substitution shape reappears (backtick in an
#        executable context; a mutating `$( ... )` embedded in a double-quoted
#        message; a mutating substitution inside an unquoted heredoc body).
#
# Usage:  bash tests/test_upgrade_refusal_message_safety.sh
#
# SANDBOX: everything lives under a mktemp dir. The git fixture's "remote" is
# a local bare repo inside the SAME scratch dir, and every git invocation is
# fenced with GIT_CONFIG_GLOBAL=/dev/null + GIT_CONFIG_SYSTEM=/dev/null (no
# user/system config → no insteadOf rewriting) and GIT_ALLOW_PROTOCOL=file (no
# network, no non-file transport). No live install, service or release dir is
# touched.
# ============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
GOLDEN="$REPO_ROOT/tests/fixtures/upgrade/non_tip_tree_refusal.golden.txt"
SCANNER="$REPO_ROOT/tests/upgrade_refusal_scan.py"

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
    return 0
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

REAL_GIT="$(command -v git)"
if [ -z "$REAL_GIT" ]; then
    echo "FATAL: git not on PATH" >&2
    exit 2
fi

# ── fixture dirs ────────────────────────────────────────────────────────────
WORK="$(mktemp -d "${TMPDIR:-/tmp}/upg-refusal-msg.XXXXXX")"
cleanup() { rm -rf "$WORK" 2>/dev/null; }
trap cleanup EXIT

# ── the harness ─────────────────────────────────────────────────────────────
# _fire_refusal <lib.sh> <workdir>
#   Builds a throwaway git fixture inside <workdir>:
#     <workdir>/origin.git  — bare "remote" (local file transport only)
#     <workdir>/repo        — clone of it, HEAD on feature/stale (≠ tip)
#   Plants a canary file named `branch` in the CWD, installs a git SPY on
#   PATH that logs every invocation, then fires the REAL
#   `_verify_tip_identity` from <lib.sh> with the CWD = the fixture repo.
#   Sets: _RC, _OUT, _SPY_LOG, _REPO_DIR, _REMOTE_DIR, _HEAD_SHA.
_fire_refusal() {
    local lib="$1" work="$2"
    rm -rf "$work"
    mkdir -p "$work"
    _REMOTE_DIR="$work/origin.git"
    _REPO_DIR="$work/repo"
    _SPY_LOG="$work/git-calls.log"

    # bare remote with one commit on `latest`
    git init -q --bare "$_REMOTE_DIR"
    mkdir -p "$work/seed"
    (
        cd "$work/seed" || exit 1
        git init -q
        git -c user.email=t@t -c user.name=t commit -q --allow-empty -m seed
        git branch -M latest
        git push -q "$_REMOTE_DIR" latest
    ) >/dev/null 2>&1

    # clone → gives us a repo with a real `origin` remote + remote-tracking refs
    git clone -q "$_REMOTE_DIR" "$_REPO_DIR" >/dev/null 2>&1
    (
        cd "$_REPO_DIR" || exit 1
        git checkout -q -b feature/stale
        git -c user.email=t@t -c user.name=t commit -q --allow-empty -m stale-feature
        git config push.default matching
    ) >/dev/null 2>&1
    _HEAD_SHA="$(git -C "$_REPO_DIR" rev-parse HEAD)"

    # git SPY — log every invocation, then delegate to the real git
    mkdir -p "$work/bin"
    cat > "$work/bin/git" <<SPY
#!/bin/sh
printf '%s\n' "\$*" >> "\$GIT_SPY_LOG"
exec "$REAL_GIT" "\$@"
SPY
    chmod +x "$work/bin/git"
    : > "$_SPY_LOG"

    # the canary: a file named `branch` in the CWD — the reported trigger
    printf 'CANARY-PAYLOAD\n' > "$_REPO_DIR/branch"

    _OUT_FILE="$work/refusal-output.txt"
    (
        cd "$_REPO_DIR" || exit 1
        PATH="$work/bin:$PATH" \
        GIT_SPY_LOG="$_SPY_LOG" \
        GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_SYSTEM=/dev/null \
        GIT_ALLOW_PROTOCOL=file \
        GIT_TERMINAL_PROMPT=0 \
        REPO_ROOT="$_REPO_DIR" \
        HEAD_SHA="$_HEAD_SHA" \
        INSTALL_DIR="" \
        LOG_TAG="[test]" \
            bash -c '. "$1"; _verify_tip_identity' _ "$lib"
    ) >"$_OUT_FILE" 2>&1
    _RC=$?
    _OUT="$(cat "$_OUT_FILE" 2>/dev/null)"
}

# _spy_ran <subcommand> — did the spy see a git <subcommand> invocation?
_spy_ran() {
    grep -qE "(^| )$1( |$)" "$_SPY_LOG" 2>/dev/null
}

# _mk_mutant <src lib.sh> <dst> — restore the PRE-FIX quoting (the defect).
# Fails loudly (exit 3) when the fix's shape has drifted: the detection-power
# proof must be re-authored, not silently skipped.
_mk_mutant() {
    python3 - "$1" "$2" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
pairs = [
    ("'or, if the local branch is ahead of origin: `git fetch origin` (a stale '",
     '"or, if the local branch is ahead of origin: `git fetch origin` (a stale "'),
    ("'or `git push origin <branch>:latest` first so the remote-tracking tip catches up, then re-check '",
     '"or `git push origin <branch>:latest` first so the remote-tracking tip catches up, then re-check "'),
]
for old, new in pairs:
    n = text.count(old)
    if n != 1:
        sys.stderr.write("MUTANT: expected exactly one occurrence of %r, found %d\n"
                         % (old[:70], n))
        sys.exit(3)
    text = text.replace(old, new)
open(dst, "w", encoding="utf-8").write(text)
PY
}

# ════════════════════════════════════════════════════════════════════════════
section "(a) refusal fires WITHOUT executing its own message (real lib.sh)"
# ════════════════════════════════════════════════════════════════════════════
_fire_refusal "$UPGRADE_DIR/lib.sh" "$WORK/real"

assert_eq "(a) refusal still exits 78 (guard semantics unchanged)" "78" "$_RC"
assert_contains "(a) refusal token still emitted" "non-tip-tree" "$_OUT"

# The core assertions: the message ran NOTHING.
# NOTE: _SPY_LOG is the PATH of the spy log — the assertions must read its
# CONTENT (a path-string check would be vacuous: it can never contain
# "fetch"/"push" and would pass for any implementation, broken or not).
_SPY_CONTENT="$(cat "$_SPY_LOG" 2>/dev/null)"
assert_not_contains "(a) NO 'git fetch' was executed by the refusal" \
    "fetch" "$_SPY_CONTENT"
assert_not_contains "(a) NO 'git push' was executed by the refusal" \
    "push" "$_SPY_CONTENT"
# the spy must have been IN PATH at all (else the two assertions above are
# vacuous again): the tip-resolution rev-parse calls prove it was used.
assert_contains "(a) the git spy was in PATH (proves the log is authoritative)" \
    "rev-parse --verify refs/remotes/origin/latest" "$_SPY_CONTENT"
if [ -e "$_REPO_DIR/:latest" ]; then
    _fail "(a) NO ':latest' file in the CWD" "absent" "present"
else
    _pass
fi
# the remote must be untouched (no push landed)
if git --git-dir="$_REMOTE_DIR" for-each-ref --format='%(refname)' | grep -q 'feature/stale'; then
    _fail "(a) remote NOT mutated by the refusal" "no feature/stale on origin" \
        "$(git --git-dir="$_REMOTE_DIR" for-each-ref --format='%(refname)')"
else
    _pass
fi
# the canary file is untouched (no stdin redirection ate it)
assert_eq "(a) canary file intact (no stdin redirection consumed it)" \
    "CANARY-PAYLOAD" "$(cat "$_REPO_DIR/branch" 2>/dev/null)"
# nothing new in the CWD
assert_eq "(a) no stray files created in the CWD" \
    "branch" "$(ls -1 "$_REPO_DIR" | grep -v '^\.git$' | tr '\n' ' ' | sed 's/ $//')"

# ════════════════════════════════════════════════════════════════════════════
section "(a') detection power — the MUTANT (pre-fix quoting) DOES misbehave"
# ════════════════════════════════════════════════════════════════════════════
# If the mutant does NOT exhibit the side effects, the (a) assertions above
# are vacuous (they would pass for any implementation, including a broken one).
MUTANT_LIB="$WORK/mutant-lib.sh"
if _mk_mutant "$UPGRADE_DIR/lib.sh" "$MUTANT_LIB"; then
    _fire_refusal "$MUTANT_LIB" "$WORK/mutant"
    if _spy_ran "fetch"; then
        _pass
    else
        _fail "(a') mutant DOES execute 'git fetch' (harness has detection power)" \
            "a fetch invocation in the spy log" "$(cat "$_SPY_LOG")"
    fi
    if _spy_ran "push"; then
        _pass
    else
        _fail "(a') mutant DOES execute 'git push' (harness has detection power)" \
            "a push invocation in the spy log" "$(cat "$_SPY_LOG")"
    fi
    if [ -e "$_REPO_DIR/:latest" ]; then
        _pass
    else
        _fail "(a') mutant DOES create the ':latest' file (harness has detection power)" \
            "':latest' present" "absent"
    fi
    # and the mutant's message LOSES the commands (the operator-visible damage)
    assert_not_contains "(a') mutant message loses the documented commands" \
        "git fetch origin" "$_OUT"
else
    _fail "(a') mutant could not be derived from the current lib.sh" \
        "the pre-fix quoting shape is present exactly once" "shape drift — re-author the mutant"
fi

# ════════════════════════════════════════════════════════════════════════════
section "(b) operator-facing message text is preserved (golden)"
# ════════════════════════════════════════════════════════════════════════════
# The golden holds the refusal payload with the two SHA values masked, so the
# pin is stable across fixtures while still catching ANY text drift.
MASKED="$(sed -E 's/\(HEAD=[0-9a-f]{12} tip=[0-9a-f]{12}\)/(HEAD=<HEAD12> tip=<TIP12>)/' \
    "$WORK/real/refusal-output.txt" | grep 'WARN: stale-artifact stage refused' \
    | sed -E 's/^.*WARN: //')"
if [ ! -f "$GOLDEN" ]; then
    _fail "(b) golden fixture exists" "$GOLDEN" "MISSING"
elif [ "$MASKED" = "$(cat "$GOLDEN")" ]; then
    _pass
else
    _fail "(b) refusal text matches the golden fixture" \
        "$(cat "$GOLDEN")" "$MASKED"
fi

# semantic assertions — the guidance must stay ACTIONABLE
assert_contains "(b) text carries the 'git fetch origin' remedy" \
    "git fetch origin" "$MASKED"
assert_contains "(b) text carries the 'git push origin <branch>:latest' remedy" \
    "git push origin <branch>:latest" "$MASKED"
assert_contains "(b) text carries the refusal token" "non-tip-tree" "$MASKED"
assert_contains "(b) text carries the merge/re-tag remedy" \
    "merge your work into the integration branch" "$MASKED"
assert_contains "(b) text carries the v0.16.13 case reference" "v0.16.13" "$MASKED"
assert_contains "(b) text carries the --allow-stale-stage remedy" \
    "--allow-stale-stage" "$MASKED"
# the PRE-FIX damage: the commands had been stripped out of the message
assert_not_contains "(b) text is NOT the pre-fix stripped form" \
    "ahead of origin:  (a stale" "$MASKED"

# ════════════════════════════════════════════════════════════════════════════
section "(c) class gate over scripts/upgrade/**.sh"
# ════════════════════════════════════════════════════════════════════════════
if python3 "$SCANNER" "$REPO_ROOT" > "$WORK/scan-current.txt" 2>&1; then
    _pass
else
    _fail "(c) scripts/upgrade/**.sh has NO dangerous command-substitution shape" \
        "scanner rc=0" "$(cat "$WORK/scan-current.txt")"
fi
assert_contains "(c) census reports zero double-quoted backticks" \
    "backtick_double        0" "$(cat "$WORK/scan-current.txt")"

# the class gate must FIRE on the pre-fix shape (mutation proof)
MUTANT_TREE="$WORK/mutant-tree"
mkdir -p "$MUTANT_TREE/scripts/upgrade"
for f in "$UPGRADE_DIR"/*.sh; do
    cp "$f" "$MUTANT_TREE/scripts/upgrade/"
done
cp "$MUTANT_LIB" "$MUTANT_TREE/scripts/upgrade/lib.sh"
if python3 "$SCANNER" "$MUTANT_TREE" > "$WORK/scan-mutant.txt" 2>&1; then
    _fail "(c) class gate FIRES on the pre-fix shape (mutation proof)" \
        "scanner rc!=0" "$(cat "$WORK/scan-mutant.txt")"
else
    _pass
fi
assert_contains "(c) class gate names the pre-fix backtick sites" \
    "[R1] scripts/upgrade/lib.sh" "$(cat "$WORK/scan-mutant.txt")"

# ── summary ─────────────────────────────────────────────────────────────────
printf '\n'
printf 'passed: %d\n' "$PASS"
printf 'failed: %d\n' "$FAIL"
if [ "$FAIL" -ne 0 ]; then
    printf 'failed tests:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
