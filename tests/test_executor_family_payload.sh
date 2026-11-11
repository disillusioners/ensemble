#!/bin/bash
# ============================================================================
# tests/test_executor_family_payload.sh — the D3 LAYOUT PIN (hermetic)
# ============================================================================
# What this pins (the gap that made the v0.29.16 promote halt possible):
#   NO packaging lane shipped the install dir's scripts/ trees — the staged
#   release payload carried no scripts/ at all and `make install` shipped
#   only scripts/watchdog-watcher.sh. The promote stop step resolves its
#   stop script via TWO legs (lib.sh stop_via_stop_script):
#     (1) the SIBLING one level up of the CALLING script's dir — lib.sh:4849
#         anchors BASH_SOURCE[1] (promote.sh's dir) ⇒ <scripts_dir>/../stop-ensemble.sh
#     (2) [RETIRED by W4] the old $(pwd)/scripts fallback — the SIBLING leg
#         is now the single source of truth; the fixtures below assert the
#         sibling-only world
#   BOTH legs miss in an install layout ⇒ bash: No such file or directory ⇒
#   rc=127 ⇒ the promote aborts before the flip. PR1 (edbf3c92f) closed the
#   DAEMON side (a fail-closed arm preflight); THIS suite pins the SHIPPING
#   side — the executor family must be PRESENT, at the sibling path the stop
#   step resolves, EXECUTABLE, under the release's integrity contract, and
#   with NO plist riding along.
#
# HERMETIC BY CONSTRUCTION:
#   * every fixture is built under a fresh mktemp dir under $TMPDIR — the
#     LIVE install dir is NEVER read or written (no ~/agents-ensemble, no
#     make install, no stop-ensemble.sh execution, no daemon lifecycle);
#   * the payload fixture is built by the SAME function stage.sh calls
#     (executor_family_install) — the pin is on the real shipping logic,
#     not on a re-implementation of it;
#   * the stamp-coverage gate is exercised against a SYNTHETIC git repo
#     (positive + negative legs), never against the live install;
#   * `make -n install` is a DRY RUN: make PRINTS the recipe and executes
#     NOTHING (no install dir is created, no daemon is touched).
#
# Exit codes: 0 = all pass, 1 = failures.
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAMILY="$REPO_ROOT/scripts/upgrade/executor_family.sh"

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
        *"$needle"*) _fail "$name" "must NOT contain '$needle'" "$haystack" ;;
        *) _pass ;;
    esac
}
section() { printf '\n== %s ==\n' "$1"; }

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/exec-family-test.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
cleanup() { rm -rf "$FIXTURE"; }
trap cleanup EXIT INT TERM

# The family library is sourced the way stage.sh sources it.
# shellcheck source=scripts/upgrade/executor_family.sh
. "$FAMILY"

# ─── 0. syntax gates ────────────────────────────────────────────────────────
section "syntax gates"
if bash -n "$FAMILY" 2>/dev/null; then _pass; else _fail "executor_family.sh passes bash -n"; fi
if bash -n "$REPO_ROOT/scripts/upgrade/stage.sh" 2>/dev/null; then _pass; else _fail "stage.sh passes bash -n"; fi
if bash -n "$REPO_ROOT/scripts/deploy.sh" 2>/dev/null; then _pass; else _fail "deploy.sh passes bash -n"; fi

# ─── 1. the canonical family list ───────────────────────────────────────────
section "canonical family list"
FAMILY_PATHS="$(executor_family_paths)"
FAMILY_COUNT="$(printf '%s\n' "$FAMILY_PATHS" | grep -c . || true)"
assert_eq "family list is non-empty" "yes" "$([ "$FAMILY_COUNT" -gt 0 ] && echo yes || echo no)"

# every listed path exists in the source tree
_missing=""
while IFS= read -r p; do
    [ -n "$p" ] || continue
    [ -f "$REPO_ROOT/$p" ] || _missing="$_missing $p"
done <<EOF
$FAMILY_PATHS
EOF
assert_eq "every family path exists in the repo" "" "$_missing"

# the load-bearing members are present in the list
for required in \
    scripts/upgrade/promote.sh \
    scripts/upgrade/lib.sh \
    scripts/upgrade/stage.sh \
    scripts/upgrade/restart.sh \
    scripts/upgrade/rollback.sh \
    scripts/upgrade/status.sh \
    scripts/upgrade/adopt-unit.sh \
    scripts/upgrade/ledger_check.py \
    scripts/upgrade/journal_read.py \
    scripts/upgrade/journal_splice.py \
    scripts/upgrade/_build_frontend.sh \
    scripts/stop-ensemble.sh \
    scripts/install-dir-guard.sh
do
    if printf '%s\n' "$FAMILY_PATHS" | grep -qx "$required"; then _pass; else
        _fail "family list contains $required" "listed" "absent"
    fi
done

# NO PLIST in the family (property, not accident — the plists are operator-side)
if printf '%s\n' "$FAMILY_PATHS" | grep -q '\.plist$'; then
    _fail "family list carries NO *.plist" "no plist" "plist listed"
else
    _pass
fi

# ANTI-DRIFT: every file that currently lives in scripts/upgrade/ must be in
# the list. A new executor script that nobody added to the list would ship
# OUT of the payload — the exact "the family is what the list says" failure
# this lane exists to close.
_unlisted=""
while IFS= read -r f; do
    [ -n "$f" ] || continue
    rel="scripts/upgrade/$f"
    printf '%s\n' "$FAMILY_PATHS" | grep -qx "$rel" || _unlisted="$_unlisted $rel"
done <<EOF
$(ls "$REPO_ROOT/scripts/upgrade" | sort)
EOF
assert_eq "no scripts/upgrade/ file is missing from the family list" "" "$_unlisted"

# ─── 2. the hermetic PAYLOAD fixture (the real shipping logic) ──────────────
section "payload fixture (executor_family_install — the stage.sh path)"
PAYLOAD="$FIXTURE/releases/v-test"
mkdir -p "$PAYLOAD"
if executor_family_install "$REPO_ROOT" "$PAYLOAD"; then _pass; else
    _fail "executor_family_install succeeds into a fresh payload root"
fi

# 2a. the payload-relative paths the promote stop step + arm gate need
[ -f "$PAYLOAD/scripts/upgrade/promote.sh" ] && _pass || _fail "payload: scripts/upgrade/promote.sh present"
[ -f "$PAYLOAD/scripts/upgrade/lib.sh" ] && _pass || _fail "payload: scripts/upgrade/lib.sh present"
[ -f "$PAYLOAD/scripts/upgrade/stage.sh" ] && _pass || _fail "payload: scripts/upgrade/stage.sh present"
[ -x "$PAYLOAD/scripts/stop-ensemble.sh" ] && _pass || _fail "payload: scripts/stop-ensemble.sh present AND executable"
[ -f "$PAYLOAD/scripts/install-dir-guard.sh" ] && _pass || _fail "payload: scripts/install-dir-guard.sh present"
[ -x "$PAYLOAD/scripts/install-dir-guard.sh" ] && _pass || _fail "payload: scripts/install-dir-guard.sh executable"

# 2b. THE SIBLING PATH — computed exactly as the promote stop step resolves it
#     (lib.sh:4849: dirname(BASH_SOURCE[1] of the calling script) + "/../").
#     Here the calling script is the payload's own promote.sh, so the leg is
#     <payload>/scripts/upgrade/../stop-ensemble.sh. PR1's arm gate requires
#     the resolved file to exist AND carry the exec bit.
STOP_LEG_SIBLING="$PAYLOAD/scripts/upgrade/../stop-ensemble.sh"
[ -f "$STOP_LEG_SIBLING" ] && _pass || _fail "stop leg 1 (sibling): $STOP_LEG_SIBLING present"
[ -x "$STOP_LEG_SIBLING" ] && _pass || _fail "stop leg 1 (sibling): executable (PR1 arm-gate predicate)"

# 2c. the sibling file at its PAYLOAD-ROOT path — scripts/stop-ensemble.sh is
#     the SAME file 2b's sibling leg resolves to (…/upgrade/../stop-ensemble.sh);
#     asserted at the root path too because the family list ships it there.
#     (W4 deleted the old $(pwd)/scripts fallback — there is no second leg.)
[ -f "$PAYLOAD/scripts/stop-ensemble.sh" ] && _pass || _fail "payload root: scripts/stop-ensemble.sh present (the sibling target)"

# 2d. NO PLIST anywhere in the payload tree (the operator-side plists must
#     never ride along with a release payload)
_plists="$(find "$PAYLOAD" -name '*.plist' 2>/dev/null | tr '\n' ' ')"
assert_eq "no *.plist anywhere in the payload" "" "$_plists"

# 2e. the payload carries the RELEASE's stamp (not a stale out-of-band copy):
#     the arm-time guard parses PIPELINE_SCRIPTS_REV out of <scripts_dir>/lib.sh
REPO_STAMP="$(sed -n 's/^PIPELINE_SCRIPTS_REV="\{0,1\}\([0-9A-Za-z._/-]*\)"\{0,1\}[[:space:]]*$/\1/p' "$REPO_ROOT/scripts/upgrade/lib.sh" 2>/dev/null | head -1 || true)"
PAYLOAD_STAMP="$(sed -n 's/^PIPELINE_SCRIPTS_REV="\{0,1\}\([0-9A-Za-z._/-]*\)"\{0,1\}[[:space:]]*$/\1/p' "$PAYLOAD/scripts/upgrade/lib.sh" 2>/dev/null | head -1 || true)"
assert_eq "payload lib.sh carries the repo's PIPELINE_SCRIPTS_REV" "$REPO_STAMP" "$PAYLOAD_STAMP"
assert_eq "the stamp is a 40-hex commit sha (the daemon parses it as one)" "40" "${#REPO_STAMP}"

# ─── 3. manifest / sha coverage over the SHIPPED set ───────────────────────
section "manifest coverage over the shipped set"
LINES="$(executor_family_manifest_lines "$PAYLOAD/scripts")"
TREE_HASH="$(executor_family_tree_hash "$LINES")"
assert_eq "the tree aggregate is a 64-hex sha256" "64" "${#TREE_HASH}"

# 3a. every shipped file is covered by the per-file map (the map is a 1:1
#     transform of these lines — the agents/frontend/plugins manifest shape)
SHIPPED_COUNT="$(find "$PAYLOAD/scripts" -type f | wc -l | tr -d ' ')"
MAPPED_COUNT="$(printf '%s\n' "$LINES" | grep -c . || true)"
assert_eq "the per-file map covers every shipped script" "$SHIPPED_COUNT" "$MAPPED_COUNT"

# 3b. the per-file sha values MATCH the bytes on disk (spot-check all of them)
_badmap=""
while IFS= read -r line; do
    [ -n "$line" ] || continue
    sha="${line%%  *}"; rel="${line#*  }"
    actual="$(shasum -a 256 "$PAYLOAD/scripts/$rel" 2>/dev/null | awk '{print $1}')"
    [ "$sha" = "$actual" ] || _badmap="$_badmap $rel"
done <<EOF
$LINES
EOF
assert_eq "every mapped sha256 matches the staged bytes" "" "$_badmap"

# 3c. the aggregate recomputes IDENTICALLY (content-derived, deterministic)
TREE_HASH2="$(executor_family_tree_hash "$(executor_family_manifest_lines "$PAYLOAD/scripts")")"
assert_eq "the tree aggregate recomputes identically" "$TREE_HASH" "$TREE_HASH2"

# 3d. BYTE-COMPAT with lib.sh's house walker (_tree_manifest / _tree_hash_of_lines)
#     — a lib.sh-side verifier must compute the same aggregate for the same
#     tree, or the manifest value would be unverifiable.
_COMPAT="$(bash -c '
set +u
. "'"$REPO_ROOT"'/scripts/upgrade/lib.sh"
. "'"$REPO_ROOT"'/scripts/upgrade/executor_family.sh"
mine="$(executor_family_manifest_lines "'"$PAYLOAD"'/scripts")"
theirs="$(_tree_manifest "'"$PAYLOAD"'/scripts" "")"
if [ "$mine" != "$theirs" ]; then echo "LINES-DIFFER"; exit 1; fi
if [ "$(executor_family_tree_hash "$mine")" != "$(_tree_hash_of_lines "$theirs")" ]; then echo "HASH-DIFFERS"; exit 1; fi
echo "COMPAT"
' 2>/dev/null)"
assert_eq "byte-compatible with lib.sh _tree_manifest/_tree_hash_of_lines" "COMPAT" "$_COMPAT"

# ─── 4. the stamp-coverage gate (truthfulness over the shipped set) ─────────
# The gate is exercised against a SYNTHETIC git repo (never the live tree):
# the stamp is the commit the shipped scripts claim to contain, so a family
# member absent at the stamped commit makes the stamp a lie.
section "stamp-coverage gate (synthetic repo — positive + negative legs)"
GITREPO="$FIXTURE/stamp-repo"
mkdir -p "$GITREPO/scripts/upgrade"
cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$GITREPO/scripts/stop-ensemble.sh"
cp "$REPO_ROOT/scripts/install-dir-guard.sh" "$GITREPO/scripts/install-dir-guard.sh"
while IFS= read -r p; do
    [ -n "$p" ] || continue
    mkdir -p "$GITREPO/$(dirname "$p")"
    cp "$REPO_ROOT/$p" "$GITREPO/$p"
done <<EOF
$FAMILY_PATHS
EOF
(
    cd "$GITREPO" || exit 1
    git init -q .
    git config user.email "family@test.invalid"
    git config user.name "family pin"
    git add -A >/dev/null 2>&1
    git commit -q -m "family" >/dev/null 2>&1
) >/dev/null 2>&1
FULL_STAMP="$(git -C "$GITREPO" rev-parse HEAD 2>/dev/null || true)"

# positive leg: the stamp covers the whole shipped set → NO output, rc 0
COVER_OUT="$(executor_family_verify_stamp_coverage "$GITREPO" "$FULL_STAMP" 2>&1)"
COVER_RC=$?
assert_eq "gate rc 0 when the stamp covers every shipped path" "0" "$COVER_RC"
assert_eq "gate reports nothing when the stamp covers the set" "" "$COVER_OUT"

# negative leg: drop one member from the stamped commit → the gate names it
PARTIAL="$FIXTURE/partial-repo"
mkdir -p "$PARTIAL/scripts/upgrade"
cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$PARTIAL/scripts/stop-ensemble.sh"
while IFS= read -r p; do
    [ -n "$p" ] || continue
    mkdir -p "$PARTIAL/$(dirname "$p")"
    cp "$REPO_ROOT/$p" "$PARTIAL/$p"
done <<EOF
$FAMILY_PATHS
EOF
rm -f "$PARTIAL/scripts/install-dir-guard.sh"
(
    cd "$PARTIAL" || exit 1
    git init -q .
    git config user.email "family@test.invalid"
    git config user.name "family pin"
    git add -A >/dev/null 2>&1
    git commit -q -m "partial family" >/dev/null 2>&1
) >/dev/null 2>&1
PARTIAL_STAMP="$(git -C "$PARTIAL" rev-parse HEAD 2>/dev/null || true)"
PARTIAL_OUT="$(executor_family_verify_stamp_coverage "$PARTIAL" "$PARTIAL_STAMP" 2>&1)"
PARTIAL_RC=$?
assert_eq "gate rc 1 when a shipped path is absent at the stamp" "1" "$PARTIAL_RC"
assert_eq "gate names the uncovered shipped path" "scripts/install-dir-guard.sh" "$PARTIAL_OUT"

# ─── 5. the deploy model — the family must REACH an install dir ─────────────
section "deploy model wiring"
# 5a. the Makefile install target. `make -n` is a DRY RUN: make prints the
#     recipe and EXECUTES NOTHING (no install dir is created, nothing is
#     installed, no daemon is touched).
MAKE_OUT="$(cd "$REPO_ROOT" && make -n install INSTALL_DIR="$FIXTURE/make-install-probe" 2>&1)"
assert_contains "Makefile install stages the executor family" "executor_family.sh --install" "$MAKE_OUT"
assert_contains "Makefile install targets the scratch INSTALL_DIR" "$FIXTURE/make-install-probe" "$MAKE_OUT"
if [ -e "$FIXTURE/make-install-probe" ]; then
    _fail "make -n created NO install dir (dry-run must not execute)"
else
    _pass
fi

# 5b. deploy.sh phase 2 — the flat-install carrier (the behavioral proof for
#     this lane lives in tests/test_deploy.sh §6d-bis: a REAL staging run
#     against a throwaway fixture asserts the family lands in the install dir).
DEPLOY_SRC="$(cat "$REPO_ROOT/scripts/deploy.sh")"
assert_contains "deploy.sh stages the executor family" "executor_family.sh" "$DEPLOY_SRC"
assert_contains "deploy.sh stages it into the install dir" '--install "$REPO_ROOT" "$INSTALL_DIR"' "$DEPLOY_SRC"

# ─── 6. the stage.sh wiring (the payload carrier) ───────────────────────────
section "stage.sh wiring"
STAGE_SRC="$(cat "$REPO_ROOT/scripts/upgrade/stage.sh")"
assert_contains "stage.sh sources the family library" ". \"\$SCRIPT_DIR/executor_family.sh\"" "$STAGE_SRC"
assert_contains "stage.sh installs the family into the payload root" 'executor_family_install "$REPO_ROOT" "$STAGE_TMP"' "$STAGE_SRC"
assert_contains "stage.sh records the scripts tree aggregate" '"scripts_tree_sha256"' "$STAGE_SRC"
assert_contains "stage.sh records the scripts per-file map" '"scripts_manifest"' "$STAGE_SRC"
assert_contains "stage.sh records the shipped stamp" '"pipeline_scripts_rev"' "$STAGE_SRC"
assert_contains "stage.sh gates on the stamp covering the shipped set" "executor_family_verify_stamp_coverage" "$STAGE_SRC"

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n========================================\n'
printf 'executor family payload tests: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf 'ALL PASS\n'
exit 0
