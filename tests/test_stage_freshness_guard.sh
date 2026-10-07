#!/bin/bash
# ============================================================================
# tests/test_stage_freshness_guard.sh — tests for the stage.sh staleness
# trap family guard (cn 0472b31f; 2026-10-04)
# ============================================================================
#
# THE TRAP. Stage.sh used to reuse dist/ensemble-prod on an EXISTENCE-ONLY
# basis, and frontend/dist on presence-only. The trap materialized TWICE
# in 2026: 2026-09-02 a stale dist/ ensemble-prod nearly staged under a
# new label, 2026-10-02 21 v0.16.9-era FE files would have shipped under
# a v0.16.10 label. Both were caught by luck. The structural fix is the
# provenance sidecar + tip-identity gate, exercised HERE.
#
# SCOPE (cn 0472b31f):
#   (i)   stale/foreign binary in dist/  → stage REFUSES (stale-provenance)
#   (ii)  fresh artifacts matching the staged tree → passes
#   (iii) frontend/ touched without FE rebuild → refuses (FE provenance stale)
#   (iv)  manifest absent (old blind-reuse path) → refuses (provenance-missing)
#   (v)   non-tip staging tree → refuses (non-tip-tree)
#   (vi)  --allow-stale-stage override unlocks + journals the override
#
# SANDBOX discipline (test-strategy.md §5.5 / upgrade-drills.md §1): zero
# side effects outside mktemp dirs. The test builds a throwaway git repo
# with the real scripts + stub payloads, tags it, and runs the real stage.sh
# against the throwaway install dir. HOME is overridden so the live/demo
# install dirs can never resolve from the operator's home. The fake-live
# .env fixture follows the tests/test_release_journal.sh precedent.
#
# Portability: bash 3.2 / BSD-safe. Uses shasum (not sha256sum). Uses
# `git status --porcelain` (BSD/GNU portable). No stat/date gymnastics.
#
#   bash tests/test_stage_freshness_guard.sh
# ============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"

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

# ─── fixture: throwaway repo (real scripts, stub payloads, git-tagged) ──────
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/stage-freshness.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
FAKE_REPO="$FIXTURE/repo"
FAKE_HOME="$FIXTURE/home"
mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
         "$FAKE_REPO/daemon/migrations/versions" \
         "$FAKE_REPO/frontend/dist/frontend/browser" \
         "$FAKE_REPO/plugins/stub_plugin" \
         "$FAKE_HOME/agents-ensemble"

# Fake LIVE install under the fake HOME (PORT staged only) so the live
# target resolves and the live-guard — not the port-resolution failure —
# is what refuses. Nothing of it is ever contacted.
printf 'PORT=4999\n' > "$FAKE_HOME/agents-ensemble/.env"

cp "$UPGRADE_DIR/lib.sh"        "$FAKE_REPO/scripts/upgrade/lib.sh"
cp "$UPGRADE_DIR/stage.sh"      "$FAKE_REPO/scripts/upgrade/stage.sh"
cp "$UPGRADE_DIR/promote.sh"    "$FAKE_REPO/scripts/upgrade/promote.sh"
cp "$UPGRADE_DIR/rollback.sh"   "$FAKE_REPO/scripts/upgrade/rollback.sh"
cp "$UPGRADE_DIR/status.sh"     "$FAKE_REPO/scripts/upgrade/status.sh"
cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$FAKE_REPO/scripts/stop-ensemble.sh"
chmod +x "$FAKE_REPO/scripts/upgrade/"*.sh

# Stub payloads — never actually run, just staged.
printf '#!/bin/bash\n# stub launcher (unit fixture)\n' > "$FAKE_REPO/launcher.sh"
printf 'stub-agent-definition\n' > "$FAKE_REPO/agents/leader/soul.md"
printf 'port: ${PORT:-8088}\n' > "$FAKE_REPO/config.yaml"
printf 'stub-index\n' > "$FAKE_REPO/frontend/dist/frontend/browser/index.html"
printf 'stub-app\n' > "$FAKE_REPO/frontend/dist/frontend/browser/main.js"
printf 'CREATE TABLE x (id int);\n' > "$FAKE_REPO/daemon/migrations/versions/20260101_000001_init.sql"
# Stub plugin payload (added 2026-10-07 for stage.sh's plugins/ precondition;
# the tier-2 plugin subsystem is REQUIRED for native lane, so a stage fixture
# without a plugins/ tree trips the v0.18.0 regression refusal before any
# freshness-guard scenarios can run).
printf 'plugin: stub_plugin\nlicense: Apache-2.0\n' > "$FAKE_REPO/plugins/stub_plugin/MANIFEST.yaml"
printf 'stub-plugin-data\n' > "$FAKE_REPO/plugins/stub_plugin/data.txt"

# A stub binary "serving" nothing — staging only, no daemon in unit tests.
printf '#!/bin/bash\nexit 78\n' > "$FIXTURE/stub-prod"
chmod +x "$FIXTURE/stub-prod"

# Helper: _prod_write <repo_root> <art> <dirty_raw> <tool> — the PRODUCTION
# writer, invoked for real: sources the real lib.sh in a subshell and calls
# the real _provenance_write (with REPO_ROOT pointed at the fixture). No
# hand-rolled JSON anywhere in the fixture setup — the C1 fix round proved
# hand-written sidecars can drift from the producer contract (the old
# scenario (vii) hand-wrote "git_dirty": true and masked the numeric-0/1
# fail-open). Raw dirty values are passed through UNNORMALIZED on purpose:
# the production writer owns normalization, and a round-trip through it is
# the honest producer→consumer contract test.
_prod_write() {  # <repo_root> <art> <head> <dirty_raw> <tool>
    local rr="$1" art="$2" head="$3" dirty="$4" tool="$5"
    REPO_ROOT="$rr" bash -c '
        . "$1/scripts/upgrade/lib.sh"
        _provenance_write "$2" "$3" "$4" "$5"
    ' _prod_write "$rr" "$art" "$head" "$dirty" "$tool" >/dev/null 2>&1
}

# Helper: _git_dirty_porcelain_via <repo_root> — the PRODUCTION dirty
# probe, evaluated against an arbitrary repo (the production function
# reads $REPO_ROOT; this wraps it with the fixture's root). Prints
# true/false — the canonical booleans the writer normalizes and the
# verifier matches (the C1 contract; the pre-fix probe printed bare 0/1).
_git_dirty_porcelain_via() {
    REPO_ROOT="$1" bash -c '
        . "$1/scripts/upgrade/lib.sh"
        _git_dirty_porcelain
    ' _gdp_via "$1"
}

# Helper: _stamp_provenance <art> <head> — PRODUCTION-writer-backed sidecar
# stamper for fixed-state fixtures (stale/foreign heads etc.). Goes through
# the real _provenance_write so the sidecar SHAPE cannot drift from what
# the build path actually produces (S4). Hand-written JSON is reserved for
# the MALFORMED-sidecar cases, where producing broken input IS the test.
_stamp_provenance() {
    local art="$1" head="$2"
    _prod_write "$FAKE_REPO" "$art" "$head" "false" \
        "stub:tests/test_stage_freshness_guard.sh"
}

# Helper: _reset_fixture_repo [extra_args...] — rebuild FAKE_REPO from
# scratch (clean working tree, single commit, SBX_V1 tag) so each scenario
# starts from a known state. Idempotent + cheap (mktemp dir is throwaway).
# L1 MODE CONTROL (C3): when $1 = "with-latest", a refs/heads/latest branch
# is created AT HEAD so _tip_sha resolves and L1 runs in its ACTIVE mode
# (gate engaged, HEAD == tip, stage proceeds). The default (no arg) leaves
# NO integration ref — L1 runs in WARN+SKIP mode (its own pinned case).
_reset_fixture_repo() {
    local mode="${1:-}"
    rm -rf "$FAKE_REPO"
    mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
             "$FAKE_REPO/daemon/migrations/versions" \
             "$FAKE_REPO/frontend/dist/frontend/browser" \
             "$FAKE_REPO/plugins/stub_plugin"
    cp "$UPGRADE_DIR/lib.sh"        "$FAKE_REPO/scripts/upgrade/lib.sh"
    cp "$UPGRADE_DIR/stage.sh"      "$FAKE_REPO/scripts/upgrade/stage.sh"
    cp "$UPGRADE_DIR/promote.sh"    "$FAKE_REPO/scripts/upgrade/promote.sh"
    cp "$UPGRADE_DIR/rollback.sh"   "$FAKE_REPO/scripts/upgrade/rollback.sh"
    cp "$UPGRADE_DIR/status.sh"     "$FAKE_REPO/scripts/upgrade/status.sh"
    cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$FAKE_REPO/scripts/stop-ensemble.sh"
    chmod +x "$FAKE_REPO/scripts/upgrade/"*.sh
    printf '#!/bin/bash\n# stub launcher (unit fixture)\n' > "$FAKE_REPO/launcher.sh"
    printf 'stub-agent-definition\n' > "$FAKE_REPO/agents/leader/soul.md"
    printf 'port: ${PORT:-8088}\n' > "$FAKE_REPO/config.yaml"
    printf 'stub-index\n' > "$FAKE_REPO/frontend/dist/frontend/browser/index.html"
    printf 'stub-app\n' > "$FAKE_REPO/frontend/dist/frontend/browser/main.js"
    printf 'CREATE TABLE x (id int);\n' > "$FAKE_REPO/daemon/migrations/versions/20260101_000001_init.sql"
    # Stub plugin payload — see initial setup; the tier-2 plugin
    # subsystem is now REQUIRED for stage.sh, so the reset must
    # recreate it identically (mktemp fixture rebuild = clean slate).
    printf 'plugin: stub_plugin\nlicense: Apache-2.0\n' > "$FAKE_REPO/plugins/stub_plugin/MANIFEST.yaml"
    printf 'stub-plugin-data\n' > "$FAKE_REPO/plugins/stub_plugin/data.txt"
    # Mirror the REAL repo's ignore shape (root .gitignore `dist/` +
    # frontend/.gitignore `/dist`): the FE provenance sidecar written by
    # _stamp_fresh lives under frontend/dist/ and MUST be invisible to the
    # honest dirty probe, or the sidecar would classify itself as tree
    # dirt (self-referential dirty-build). Committed with the fixture so
    # the ignore rules apply from the first probe onward.
    printf 'dist/\n*.build-provenance.json\n' > "$FAKE_REPO/.gitignore"
    git -C "$FAKE_REPO" init -q
    git -C "$FAKE_REPO" add -A 2>/dev/null
    git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -q -m fixture
    SBX_V1="v1.0.0-fresh"
    git -C "$FAKE_REPO" tag "$SBX_V1"
    if [ "$mode" = "with-latest" ]; then
        # L1 ACTIVE-mode fixture: refs/heads/latest AT HEAD. _tip_sha
        # resolves it (full refspec — a tag named latest would NOT be
        # picked up, which is itself the S3 contract), HEAD == tip, and
        # the gate passes the tree through to L2.
        git -C "$FAKE_REPO" branch latest
    fi
}

# Helper: _stamp_fresh — re-stamp both stub artifacts to match the
# CURRENT FAKE_REPO HEAD via the PRODUCTION writer. Called at the start
# of every test that expects the freshness guard to PASS. After the
# call, _head holds the fixture's HEAD sha for assertions.
_stamp_fresh() {
    _head="$(git -C "$FAKE_REPO" rev-parse HEAD)"
    # Honest dirty probe (journal-pack _stamp_provenance_for_repo pattern):
    # the sidecar records the tree's REAL working-tree state, not a
    # hardcoded false. Every call site sits at a clean-tree point (post
    # _reset_fixture_repo, or after the scenario's own dirt is removed),
    # so the probe yields false at each — but by MEASUREMENT, not decree:
    # a future scenario that forgets to clean up fails loudly here instead
    # of stamping a lying sidecar.
    local dirty
    dirty="$(_git_dirty_porcelain_via "$FAKE_REPO")"
    _prod_write "$FAKE_REPO" "$FIXTURE/stub-prod" "$_head" "$dirty" \
        "stub:tests/test_stage_freshness_guard.sh"
    _prod_write "$FAKE_REPO" "$FAKE_REPO/frontend/dist/frontend/browser/index.html" \
        "$_head" "$dirty" "stub:tests/test_stage_freshness_guard.sh"
}

# Sandbox install dir + port for stage.sh invocations.
SBX="$FIXTURE/installsb"
mkdir -p "$SBX"
SBX_PORT=18388   # throwaway; never a real env port

# Tip-identity fixture: a SECOND throwaway repo on a feature branch
# (HEAD != latest) so the tip-identity check has something to refuse.
# The branch is created from a base tag (latest is the prior commit on
# 'latest' branch; HEAD is one commit ahead on feature/stale). The
# payloads are committed in the SAME commit as the tag so `git describe
# --tags --exact-match` resolves the tag to HEAD.
TIP_FIXTURE="$FIXTURE/tip-fixture"
mkdir -p "$TIP_FIXTURE"
git -C "$TIP_FIXTURE" init -q
mkdir -p "$TIP_FIXTURE/agents/leader" "$TIP_FIXTURE/daemon/migrations/versions" \
         "$TIP_FIXTURE/frontend/dist/frontend/browser" \
         "$TIP_FIXTURE/plugins/stub_plugin"
printf 'stub-agent\n' > "$TIP_FIXTURE/agents/leader/soul.md"
printf 'port: 1\n' > "$TIP_FIXTURE/config.yaml"
printf '#!/bin/bash\nexit 0\n' > "$TIP_FIXTURE/launcher.sh"
printf 'CREATE TABLE x (id int);\n' > "$TIP_FIXTURE/daemon/migrations/versions/20260101_init.sql"
printf 'stub-index\n' > "$TIP_FIXTURE/frontend/dist/frontend/browser/index.html"
# plugins/ stub for the tip-identity fixture (mirrors the FAKE_REPO
# stub — see initial setup). The tier-2 plugins/ precondition
# fires before tip-identity / provenance, so the fixture must
# carry a plugins/ dir for the override path to reach L1.
printf 'plugin: stub_plugin\nlicense: Apache-2.0\n' > "$TIP_FIXTURE/plugins/stub_plugin/MANIFEST.yaml"
printf 'stub-plugin-data\n' > "$TIP_FIXTURE/plugins/stub_plugin/data.txt"
git -C "$TIP_FIXTURE" add -A
git -C "$TIP_FIXTURE" -c user.email=t@t -c user.name=t commit -qm tip-base
git -C "$TIP_FIXTURE" tag v0.0.1-base
git -C "$TIP_FIXTURE" branch latest
# Move HEAD to a NEW commit on a feature branch (the tip-identity case).
git -C "$TIP_FIXTURE" checkout -q -b feature/stale
printf 'src-update\n' > "$TIP_FIXTURE/agents/leader/soul.md"
git -C "$TIP_FIXTURE" add -A
git -C "$TIP_FIXTURE" -c user.email=t@t -c user.name=t commit -qm stale-feature
TIP_SBX_V="v0.0.2-stale"
git -C "$TIP_FIXTURE" tag "$TIP_SBX_V"
# Copy the real scripts into TIP_FIXTURE so the tip-identity test can
# run them. The scripts are uncommitted (they're test scaffolding, not
# part of the fixture's "release payload" — the fixture simulates a tree
# that was just tagged with extra files added later that don't yet have
# sidecars; this is the realistic v0.16.13 case).
mkdir -p "$TIP_FIXTURE/scripts/upgrade"
cp "$UPGRADE_DIR/lib.sh"        "$TIP_FIXTURE/scripts/upgrade/lib.sh"
cp "$UPGRADE_DIR/stage.sh"      "$TIP_FIXTURE/scripts/upgrade/stage.sh"
chmod +x "$TIP_FIXTURE/scripts/upgrade/"*.sh

# Initial fixture: clean tip + initial commit (SBX_V1 tag).
_reset_fixture_repo

# ─── 1. syntax gates (the freshness-guard refactor must not break parsing) ─
section "syntax gates"
for s in lib stage promote rollback status; do
    if bash -n "$FAKE_REPO/scripts/upgrade/$s.sh" 2>/dev/null; then
        _pass
    else
        _fail "$s.sh passes bash -n"
    fi
done

# ─── 2. argv parsing for the new flag ────────────────────────────────────────
section "argv parsing for --allow-stale-stage"
# (a) --allow-stale-stage is a sticky bool, no value (refuses `--allow-stale-stage X`
#     as an unknown flag — the pattern matches --skip-build's no-value
#     sticky-bool shape).
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --allow-stale-stage dummy 2>&1)"; rc=$?
assert_eq "argv parsing: --allow-stale-stage is sticky bool, no value" "78" "$rc"
# (b) --help shows the new flag documentation (sentinel: the FRESHNESS GUARD block).
out="$(HOME="$FAKE_HOME" bash "$FAKE_REPO/scripts/upgrade/stage.sh" --help 2>&1)"
assert_contains "argv parsing: --help documents the freshness guard" "FRESHNESS GUARD" "$out"
assert_contains "argv parsing: --help names the override flag" "--allow-stale-stage" "$out"

# ─── 3. scenario (i): stale/foreign binary in dist/ → refuse ────────────────
section "(i) stale/foreign binary → refuse with stale-provenance"
# Setup: stamp the stub-prod provenance with a FOREIGN head (a different
# SHA that the current tree's HEAD won't match). The guard must refuse
# with the stale-provenance token. Sidecars go through the PRODUCTION
# writer (_prod_write) — the fixture cannot drift from the real shape.
FOREIGN_HEAD="$(printf '%040d' 0)"   # 40 zeros — a sha that no real commit has
_stamp_provenance "$FIXTURE/stub-prod" "$FOREIGN_HEAD"
# Also stamp FE for FAKE_REPO so the FE check doesn't fire first.
_stamp_provenance "$FAKE_REPO/frontend/dist/frontend/browser/index.html" \
    "$(git -C "$FAKE_REPO" rev-parse HEAD)"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(i) stale binary → exit 78" "78" "$rc"
assert_contains "(i) stale binary → refusal token: stale-provenance" "stale-provenance" "$out"
assert_contains "(i) stale binary → remedy text names the rebuild path" \
    "rebuild from the current tree" "$out"
# Verify the failure did NOT stage anything (no .staging leftover, no
# release dir created).
if [ -d "$SBX/releases/$SBX_V1" ]; then
    _fail "(i) stale binary → no release staged on refusal" "absent" "present"
else
    _pass
fi

# ─── 4. scenario (ii): fresh artifacts matching → pass ──────────────────────
section "(ii) fresh artifacts matching the staged tree → pass"
# TWO L1 MODES are pinned here (C3 fix — the old pack only ever exercised
# WARN+SKIP because the fixture carried no integration ref, so the ACTIVE
# pass path was untested):
#
#   (ii-a) WARN+SKIP mode — NO latest/origin ref resolves. L1 warns loudly
#          and skips; L2 (provenance) is the sole gate; stage proceeds.
#   (ii-b) ACTIVE mode — refs/heads/latest EXISTS at HEAD. The gate is
#          ENGAGED, matches, and passes silently; stage proceeds with NO
#          skip-warn on stderr.

# (ii-a) WARN+SKIP mode (the default fixture has no integration ref).
_stamp_fresh
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(ii-a WARN+SKIP) fresh artifacts → exit 0" "0" "$rc"
assert_contains "(ii-a WARN+SKIP) L1 skip warn is VISIBLE (never silent)" \
    "tip-identity check SKIPPED" "$out"
assert_contains "(ii-a WARN+SKIP) skip warn names the L2 sole-gate fallback" \
    "sole gate" "$out"
assert_contains "(ii-a WARN+SKIP) stage announces NO flip" "NO flip" "$out"
# Manifest should be present.
[ -f "$SBX/releases/$SBX_V1/manifest.json" ] && _pass || _fail "(ii-a WARN+SKIP) manifest exists"
# Seed the current symlink so --verify has something to check (status.sh
# --verify requires `INSTALL_DIR/current` to point at a release).
ln -sfn "releases/$SBX_V1" "$SBX/current"
# The release should pass integrity verify (status.sh --verify).
verify_out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/status.sh" sandbox --verify 2>&1)"; verify_rc=$?
assert_eq "(ii-a WARN+SKIP) status.sh --verify clean exit 0" "0" "$verify_rc"
assert_contains "(ii-a WARN+SKIP) status.sh --verify says integrity OK" "integrity OK" "$verify_out"

# (ii-b) ACTIVE mode — rebuild the fixture WITH refs/heads/latest at HEAD
# so _tip_sha resolves (full refspec; a tag named latest would NOT be
# resolved — that's the S3 contract, pinned in scenario (ix) below).
_reset_fixture_repo with-latest
_stamp_fresh
rm -rf "$SBX" && mkdir -p "$SBX"   # virgin install dir for the active run
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(ii-b ACTIVE) fresh artifacts at tip → exit 0" "0" "$rc"
assert_not_contains "(ii-b ACTIVE) NO skip warn when the gate is engaged" \
    "tip-identity check SKIPPED" "$out"
assert_contains "(ii-b ACTIVE) stage announces NO flip" "NO flip" "$out"
[ -f "$SBX/releases/$SBX_V1/manifest.json" ] && _pass || _fail "(ii-b ACTIVE) manifest exists"
# The tip the gate resolved must be exactly the fixture's HEAD (the gate
# compared the right pair of shas — a refspec regression would show here).
tip_resolved="$(git -C "$FAKE_REPO" rev-parse refs/heads/latest 2>/dev/null)"
assert_eq "(ii-b ACTIVE) refs/heads/latest == fixture HEAD" "$(git -C "$FAKE_REPO" rev-parse HEAD)" "$tip_resolved"
# integrity on the active-mode release too
ln -sfn "releases/$SBX_V1" "$SBX/current"
verify_out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/status.sh" sandbox --verify 2>&1)"; verify_rc=$?
assert_eq "(ii-b ACTIVE) status.sh --verify clean exit 0" "0" "$verify_rc"
# FIXTURE-MODE ISOLATION: scenarios (iii)+ exercise L2 (provenance) as the
# sole gate, so they must run on a NO-latest fixture — otherwise the
# with-latest branch left behind by (ii-b) engages L1 and any HEAD drift
# fires non-tip-tree BEFORE the provenance token under test.
_reset_fixture_repo

# ─── 5. scenario (iii): frontend/ touched without FE rebuild → refuse ────────
section "(iii) frontend/ touched without FE rebuild → refuse"
# Setup: stamp both artifacts fresh. Then TOUCH frontend/ source files
# (the tracked source, not the dist) and commit. The guard must refuse
# the FE provenance as stale because the commit moved the tracked tree
# (HEAD changed), so the sidecar's git_head no longer matches.
_stamp_fresh
printf 'src-update\n' > "$FAKE_REPO/frontend/src-index.html"
git -C "$FAKE_REPO" add frontend/src-index.html
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm frontend-touched
# Re-tag with the SAME version (the test's version is a fixed string) —
# actually NO, we need a NEW tag for the new commit because the
# VERSION discipline check requires an exact tag at HEAD. Use a new tag.
FE_SBX_V="v1.0.1-fetouch"
git -C "$FAKE_REPO" tag "$FE_SBX_V"
# The provenance is now stale (HEAD changed, sidecar still records the
# OLD commit). Stage must refuse.
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    VERSION="$FE_SBX_V" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(iii) FE source touched w/o rebuild → exit 78" "78" "$rc"
# The refusal token may be either stale-provenance (binary) OR FE
# provenance (whichever fires first in the script's order). Both
# prove the guard is doing its job.
case "$out" in
    *"stale-provenance"*|*"provenance-missing"*|*"provenance-hash-mismatch"*|*"dirty-build"*)
        _pass
        ;;
    *)
        _fail "(iii) FE source touched → refusal token from provenance taxonomy" "stale-provenance|provenance-missing|provenance-hash-mismatch|dirty-build" "$(printf '%s' "$out" | tail -3)"
        ;;
esac
# Reset the fixture to the original SBX_V1 tag for the next tests.
rm -f "$FAKE_REPO/frontend/src-index.html"
git -C "$FAKE_REPO" add -A
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm "reset: remove src-index" 2>/dev/null
git -C "$FAKE_REPO" tag -d "$FE_SBX_V" 2>/dev/null
# Stash the touched-commit under SBX_V1's HEAD by force-moving the tag
# back. The point is to restore the original state for the next test.
git -C "$FAKE_REPO" tag -f "$SBX_V1" HEAD 2>/dev/null
_stamp_fresh

# ─── 6. scenario (iv): manifest absent (old blind-reuse path) → refuse ───────
section "(iv) no .build-provenance.json sidecar → refuse (provenance-missing)"
# Setup: do NOT stamp provenance for the stub-prod. The guard must
# refuse with provenance-missing. The OLD blind-reuse path was:
# dist/ensemble-prod present, no provenance, stage passes. That path
# is now STRUCTURALLY IMPOSSIBLE.
rm -rf "$SBX" && mkdir -p "$SBX"   # fresh install dir for a clean check
rm -f "$FIXTURE/stub-prod.build-provenance.json"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(iv) missing provenance → exit 78" "78" "$rc"
assert_contains "(iv) missing provenance → refusal token: provenance-missing" \
    "provenance-missing" "$out"
assert_contains "(iv) missing provenance → remedy text names the rebuild path" \
    "rebuild:" "$out"
# Verify the binary was NOT staged (no manifest, no release dir).
if [ -d "$SBX/releases/$SBX_V1" ]; then
    _fail "(iv) no release dir on refusal" "absent" "present"
else
    _pass
fi

# ─── 7. scenario (v): tip-identity gate (non-tip tree → refuse) ──────────────
section "(v) non-tip staging tree → refuse (non-tip-tree)"
# Use TIP_FIXTURE where HEAD is on a feature branch and refs/heads/latest
# points to the prior commit (the base tag's commit). _tip_sha resolves
# refs/heads/latest (full refspec — no bare-DWIM `latest` that could pick
# up a tag). HEAD is on feature/stale — a different SHA → REFUSE.
# Stamp artifacts against the FEATURE-BRANCH HEAD via the PRODUCTION
# writer so L2 provenance PASSES and L1 (tip identity) is what fires.
tip_head="$(git -C "$TIP_FIXTURE" rev-parse HEAD)"
# Copy stub-prod to TIP_FIXTURE so the sidecar lives in the fixture repo.
cp "$FIXTURE/stub-prod" "$TIP_FIXTURE/stub-prod"
chmod +x "$TIP_FIXTURE/stub-prod"
_prod_write "$TIP_FIXTURE" "$TIP_FIXTURE/stub-prod" "$tip_head" "false" \
    "stub:tests/test_stage_freshness_guard.sh"
# FE: stamp against the feature-branch HEAD.
_prod_write "$TIP_FIXTURE" "$TIP_FIXTURE/frontend/dist/frontend/browser/index.html" \
    "$tip_head" "false" "stub:tests/test_stage_freshness_guard.sh"
TIP_SBX="$FIXTURE/tip-sbx"
mkdir -p "$TIP_SBX"
TIP_SBX_PORT=18389
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$TIP_SBX" PORT="$TIP_SBX_PORT" \
    VERSION="$TIP_SBX_V" bash "$TIP_FIXTURE/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$TIP_FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(v) non-tip tree → exit 78" "78" "$rc"
assert_contains "(v) non-tip tree → refusal token: non-tip-tree" "non-tip-tree" "$out"
assert_contains "(v) non-tip tree → refusal cites the v0.16.13 case" \
    "v0.16.13" "$out"
assert_contains "(v) non-tip tree → remedy text names the tip-merge path" \
    "merge your work into the integration branch" "$out"

# ─── 8. scenario (vi): --allow-stale-stage override unlocks + journals ───────
section "(vi) --allow-stale-stage override unlocks + journals"
# (a) Override on the tip-identity case: with the flag, stage PASSES
#     and journals the override event. Initialize the install dir's
#     journal first (the override's best-effort append needs a journal
#     to land on).
TIP_SBX2="$FIXTURE/tip-sbx-override"
mkdir -p "$TIP_SBX2/releases"
# Initialize the journal so the override event lands on it (the exact
# shape journal_init writes — one line, compact, no trailing whitespace).
cat > "$TIP_SBX2/releases/state.json" \
    <<'EOF'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
EOF
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$TIP_SBX2" PORT="$TIP_SBX_PORT" \
    VERSION="$TIP_SBX_V" bash "$TIP_FIXTURE/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$TIP_FIXTURE/stub-prod" --allow-stale-stage 2>&1)"; rc=$?
assert_eq "(vi) override on non-tip tree → exit 0" "0" "$rc"
# Verify the override is journaled (best-effort).
JREF="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
print(":".join(h.get("event","") for h in d.get("history",[])))' \
    "$TIP_SBX2/releases/state.json" 2>/dev/null || true)"
assert_contains "(vi) override event journaled on the install dir" \
    "stage_freshness_override" "$JREF"
# The journaled event must carry the reason token.
JREF2="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
for h in d.get("history",[]):
    if h.get("event") == "stage_freshness_override":
        print(h.get("detail",""))' \
    "$TIP_SBX2/releases/state.json" 2>/dev/null || true)"
assert_contains "(vi) override event reason=non-tip-tree" "reason=non-tip-tree" "$JREF2"
assert_contains "(vi) override event operator_accepted=true" \
    "operator_accepted=true" "$JREF2"

# (b) Override on the missing-provenance case: the flag unlocks provenance-
#     missing refusal. Stamp the sidecar as MISSING, pass the override.
PROV_SBX="$FIXTURE/prov-sbx"
mkdir -p "$PROV_SBX/releases"
cat > "$PROV_SBX/releases/state.json" \
    <<'EOF'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
EOF
rm -f "$FIXTURE/stub-prod.build-provenance.json"   # ensure absent
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$PROV_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" --allow-stale-stage 2>&1)"; rc=$?
assert_eq "(vi) override on missing provenance → exit 0" "0" "$rc"
# Verify the override event journaled with reason=provenance-missing.
JREF3="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
for h in d.get("history",[]):
    if h.get("event") == "stage_freshness_override":
        print(h.get("detail",""))' \
    "$PROV_SBX/releases/state.json" 2>/dev/null || true)"
assert_contains "(vi) override reason=provenance-missing journaled" \
    "reason=provenance-missing" "$JREF3"

# (c) Without the override flag, the same invocation REFUSES. This pins
#     the override's necessity — no silent bypasses.
rm -rf "$PROV_SBX" && mkdir -p "$PROV_SBX"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$PROV_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(vi) NO override → exit 78 (no silent bypass)" "78" "$rc"
assert_contains "(vi) NO override → refusal token: provenance-missing" \
    "provenance-missing" "$out"

# ─── 9. scenario (vii): dirty-build refuses — REAL writer→verifier round-trip ─
section "(vii) dirty-build refuses (REAL producer round-trip + legacy-vocabulary pin)"
# C1 REGRESSION PIN. The old pack HAND-WROTE `"git_dirty": true` here,
# which concealed the real producer's contract: _git_dirty_porcelain used
# to print bare 0/1, the writer emitted them verbatim, and the verifier
# (matching only True/true) silently PASSED a dirty-tree build. This
# scenario now exercises the ACTUAL producer path end-to-end:
#
#   dirty fixture tree → _git_dirty_porcelain (production dirty probe)
#   → _provenance_write (production writer, raw probe output passed
#     through UNNORMALIZED at the call site — exactly what stage.sh:224
#     and _build_frontend.sh:105 do) → stage → verifier MUST refuse.
#
# A regression in ANY of those three links re-fails this scenario.
_stamp_fresh   # baseline: clean-tree sidecars for FE etc.
# Make the FIXTURE TREE genuinely dirty (uncommitted change), exactly the
# trap-family scenario: a hotfix built without committing.
printf 'uncommitted-hotfix\n' > "$FAKE_REPO/UNCOMMITTED_HOTFIX"
_prod_write "$FAKE_REPO" "$FIXTURE/stub-prod" "$(git -C "$FAKE_REPO" rev-parse HEAD)" \
    "$(_git_dirty_porcelain_via "$FAKE_REPO")" \
    "stub:tests/test_stage_freshness_guard.sh"
# The sidecar the production writer just wrote must carry the CANONICAL
# boolean (writer-side normalization contract).
grep -q '"git_dirty": true' "$FIXTURE/stub-prod.build-provenance.json" \
    && _pass || _fail "(vii) production writer emits canonical git_dirty:true" \
        '"git_dirty": true' "$(grep 'git_dirty' "$FIXTURE/stub-prod.build-provenance.json")"
DIRTY_SBX="$FIXTURE/dirty-sbx"
mkdir -p "$DIRTY_SBX/releases"
cat > "$DIRTY_SBX/releases/state.json" \
    <<'EOF'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
EOF
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$DIRTY_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(vii) dirty build (real producer round-trip) → exit 78" "78" "$rc"
assert_contains "(vii) dirty build → refusal token: dirty-build" \
    "dirty-build" "$out"
# Restore the clean tree + fresh sidecars for the remaining scenarios.
rm -f "$FAKE_REPO/UNCOMMITTED_HOTFIX"
_stamp_fresh
# (vii-b) LEGACY-VOCABULARY pin: a sidecar written by the PRE-FIX writer
#         (numeric git_dirty: 1) must ALSO refuse — the verifier accepts
#         the full dirty vocabulary true/True/1 so old sidecars never
#         silently pass (the C1 hole was exactly this shape).
_prod_write "$FAKE_REPO" "$FIXTURE/stub-prod" "$(git -C "$FAKE_REPO" rev-parse HEAD)" \
    "1" "stub:tests/test_stage_freshness_guard.sh"
rm -rf "$DIRTY_SBX" && mkdir -p "$DIRTY_SBX/releases"
cat > "$DIRTY_SBX/releases/state.json" \
    <<'EOF'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
EOF
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$DIRTY_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(vii-b) legacy numeric git_dirty:1 sidecar → exit 78" "78" "$rc"
assert_contains "(vii-b) legacy numeric sidecar → dirty-build (no silent pass)" \
    "dirty-build" "$out"
_stamp_fresh
# (vii-c) Override on the round-trip dirty case unlocks + journals.
_prod_write "$FAKE_REPO" "$FIXTURE/stub-prod" "$(git -C "$FAKE_REPO" rev-parse HEAD)" \
    "true" "stub:tests/test_stage_freshness_guard.sh"
rm -rf "$DIRTY_SBX" && mkdir -p "$DIRTY_SBX/releases"
cat > "$DIRTY_SBX/releases/state.json" \
    <<'EOF'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
EOF
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$DIRTY_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" --allow-stale-stage 2>&1)"; rc=$?
assert_eq "(vii-c) override on dirty-build → exit 0" "0" "$rc"
# The override event carries reason=dirty-build.
JREF4="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
for h in d.get("history",[]):
    if h.get("event") == "stage_freshness_override":
        print(h.get("detail",""))' \
    "$DIRTY_SBX/releases/state.json" 2>/dev/null || true)"
assert_contains "(vii-c) override reason=dirty-build journaled" \
    "reason=dirty-build" "$JREF4"
_stamp_fresh

# ─── 10. scenario (viii): malformed sidecar → provenance-malformed (C2 pin) ──
section "(viii) malformed sidecar → provenance-malformed (no field skips a check)"
# C2 REGRESSION PIN. The old verifier guarded every field with
# `[ -n "$field" ] &&` — a sidecar MISSING a field silently skipped that
# check and returned 0. Now: a sidecar that exists must carry all three
# load-bearing fields, syntactically valid; anything else refuses
# provenance-malformed. Hand-written broken sidecars are the POINT here —
# there is no production writer for malformed output.
_malformed_case() {  # <name> <sidecar-body-file>
    local name="$1" body="$2"
    cp "$body" "$FIXTURE/stub-prod.build-provenance.json"
    local sbx="$FIXTURE/malformed-sbx"
    rm -rf "$sbx" && mkdir -p "$sbx"
    local o rc
    o="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$sbx" PORT="$SBX_PORT" \
        VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
        --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
    assert_eq "($name) malformed sidecar → exit 78" "78" "$rc"
    assert_contains "($name) refusal token: provenance-malformed" \
        "provenance-malformed" "$o"
    assert_not_contains "($name) NO silent pass (not exit 0)" "NO flip" "$o"
}
_head="$(git -C "$FAKE_REPO" rev-parse HEAD)"
_sha="$(shasum -a 256 "$FIXTURE/stub-prod" | awk '{print $1}')"
# (a) git_dirty key MISSING entirely (the exact C2 fail-open shape).
cat > "$FIXTURE/mal-a.json" <<EOF
{
  "git_head": "$_head",
  "git_head_short": "${_head:0:12}",
  "build_at": "2026-10-04T00:00:00Z",
  "build_tool": "stub",
  "artifact_sha256": "$_sha",
  "artifact_path": "stub"
}
EOF
_malformed_case "viii-a missing git_dirty" "$FIXTURE/mal-a.json"
# (b) git_dirty present but UNRECOGNIZED value (garbage).
cat > "$FIXTURE/mal-b.json" <<EOF
{
  "git_head": "$_head",
  "git_head_short": "${_head:0:12}",
  "git_dirty": "yes",
  "build_at": "2026-10-04T00:00:00Z",
  "build_tool": "stub",
  "artifact_sha256": "$_sha",
  "artifact_path": "stub"
}
EOF
_malformed_case "viii-b garbage git_dirty" "$FIXTURE/mal-b.json"
# (c) artifact_sha256 MISSING (the other old silent-skip shape).
cat > "$FIXTURE/mal-c.json" <<EOF
{
  "git_head": "$_head",
  "git_head_short": "${_head:0:12}",
  "git_dirty": false,
  "build_at": "2026-10-04T00:00:00Z",
  "build_tool": "stub",
  "artifact_path": "stub"
}
EOF
_malformed_case "viii-c missing artifact_sha256" "$FIXTURE/mal-c.json"
# (d) git_head MISSING (the third old silent-skip shape).
cat > "$FIXTURE/mal-d.json" <<EOF
{
  "git_head_short": "${_head:0:12}",
  "git_dirty": false,
  "build_at": "2026-10-04T00:00:00Z",
  "build_tool": "stub",
  "artifact_sha256": "$_sha",
  "artifact_path": "stub"
}
EOF
_malformed_case "viii-d missing git_head" "$FIXTURE/mal-d.json"
# (e) EMPTY sidecar file (torn write class).
printf '' > "$FIXTURE/mal-e.json"
_malformed_case "viii-e empty sidecar" "$FIXTURE/mal-e.json"
# (g) git_head_short PRESENT-but-garbage (finding #4): informational
#     field, gates no comparison, but garbage free text is a corruption
#     signal — refuses provenance-malformed instead of free-text pass.
#     (Absent/empty stays tolerated: it is not load-bearing.)
cat > "$FIXTURE/mal-g.json" <<EOF
{
  "git_head": "$_head",
  "git_head_short": "<corrupted>",
  "git_dirty": false,
  "build_at": "2026-10-04T00:00:00Z",
  "build_tool": "stub",
  "artifact_sha256": "$_sha",
  "artifact_path": "stub"
}
EOF
_malformed_case "viii-g garbage git_head_short" "$FIXTURE/mal-g.json"
# (h) git_head_short ABSENT → tolerated (the #4 gate is when-present only).
cat > "$FIXTURE/mal-h.json" <<EOF
{
  "git_head": "$_head",
  "git_dirty": false,
  "build_at": "2026-10-04T00:00:00Z",
  "build_tool": "stub",
  "artifact_sha256": "$_sha",
  "artifact_path": "stub"
}
EOF
_sbxfresh="$FIXTURE/short-absent-sbx"
rm -rf "$_sbxfresh" && mkdir -p "$_sbxfresh"
cp "$FIXTURE/mal-h.json" "$FIXTURE/stub-prod.build-provenance.json"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$_sbxfresh" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(viii-h) absent git_head_short tolerated → exit 0" "0" "$rc"
assert_not_contains "(viii-h) NO malformed refusal for absent short-hash" \
    "provenance-malformed" "$out"
_stamp_fresh

# (f) Override DOES unlock malformed (consistent with the other tokens)
#     and journals reason=provenance-malformed.
cp "$FIXTURE/mal-a.json" "$FIXTURE/stub-prod.build-provenance.json"
MAL_SBX="$FIXTURE/malformed-ovr-sbx"
mkdir -p "$MAL_SBX/releases"
cat > "$MAL_SBX/releases/state.json" \
    <<'EOF'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
EOF
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$MAL_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" --allow-stale-stage 2>&1)"; rc=$?
assert_eq "(viii-f) override on malformed → exit 0" "0" "$rc"
JREF5="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
for h in d.get("history",[]):
    if h.get("event") == "stage_freshness_override":
        print(h.get("detail",""))' \
    "$MAL_SBX/releases/state.json" 2>/dev/null || true)"
assert_contains "(viii-f) override reason=provenance-malformed journaled" \
    "reason=provenance-malformed" "$JREF5"
_stamp_fresh

# ─── 10a. scenario (viii-i/ii/iii): M1 laundering pin (review round 2) ─────
section "(viii-i) M1 invalid-JSON decoy git_dirty → refuse (laundering pin)"
# The M1 laundering class: a hand-crafted sidecar with an unescaped-
# quote decoy `"git_dirty": false` inside a non-key field's value.
# Without the M1 fix, `_json_field_quoted`'s first-occurrence search
# would grab the LEAKED `false` (not the real `true`), the shape gate
# would canonicalize it as clean, and stage would exit 0 where
# dirty-build should refuse. With the M1 fix (balance scan + key
# uniqueness in _provenance_read), the file is rejected BEFORE any
# field extraction runs. The reviewer's U16/U16b probes demonstrated
# the rc=0 path empirically (python3 also rejects as invalid JSON).
cp "$FIXTURE/mal-g.json" "$FIXTURE/stub-prod.build-provenance.json"  # viii-g short-hash; reset
cat > "$FIXTURE/mal-m1-dirty.json" <<EOF
{
  "note": "leaked "git_dirty": false more text",
  "git_dirty": true,
  "git_head": "0123456789abcdef0123456789abcdef01234567",
  "git_head_short": "01234567",
  "build_at": "2026-10-04T00:00:00Z",
  "build_tool": "stub",
  "artifact_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "artifact_path": "stub"
}
EOF
_malformed_case "viii-i invalid-JSON decoy git_dirty" "$FIXTURE/mal-m1-dirty.json"
# d-1 REGRESSION PIN (review round 3): the specific laundering message
# MUST appear. If this assertion ever fails, d-1 has regressed — the
# case dispatch is dead again and the shape gate is reaching operators
# for a laundering failure (the exact d-1 symptom). Asserts on the
# per-state MESSAGE, not just the token — the pre-d-1 pack asserted
# tokens only, which is precisely why d-1 went undetected.
_malformed_msg() {  # <name> <sidecar-body-file>
    local name="$1" body="$2"
    cp "$body" "$FIXTURE/stub-prod.build-provenance.json"
    local sbx="$FIXTURE/d1-sbx"
    rm -rf "$sbx" && mkdir -p "$sbx"
    local o rc
    o="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$sbx" PORT="$SBX_PORT" \
        VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
        --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
    assert_eq "($name d-1) exit 78" "78" "$rc"
    assert_contains "($name d-1) reaches the MALFORMED branch" \
        "is MALFORMED" "$o"
    assert_contains "($name d-1) carries the laundering-signature text" \
        "laundering signature" "$o"
    assert_not_contains "($name d-1) does NOT fall back to the shape-gate message" \
        "git_head missing/empty" "$o"
}
_malformed_msg "viii-i" "$FIXTURE/mal-m1-dirty.json"
# Sanity: python3 ALSO rejects this content (independent ground truth).
if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$FIXTURE/mal-m1-dirty.json" 2>/dev/null; then
    _fail "(viii-i) fixture sanity: python3 must reject the laundering shape" "rejected" "accepted"
else
    _pass
fi
_stamp_fresh

# (viii-ii) M1 invalid-JSON decoy git_head — variant on the head key,
# same laundering signature. Without the M1 fix, the first-occurrence
# `"git_head":` extraction would grab the leaked head sha and the stale-
# provenance comparison (against the real current HEAD) would either
# match (false-pass: nothing changes) or mismatch (false-fail: wrong
# commit named in the WARN). Either way: invalid JSON accepted.
cat > "$FIXTURE/mal-m1-head.json" <<EOF
{
  "note": "leaked "git_head": "0000000000000000000000000000000000000000" more text",
  "git_head": "0123456789abcdef0123456789abcdef01234567",
  "git_dirty": true,
  "git_head_short": "01234567",
  "build_at": "2026-10-04T00:00:00Z",
  "build_tool": "stub",
  "artifact_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "artifact_path": "stub"
}
EOF
_malformed_case "viii-ii invalid-JSON decoy git_head" "$FIXTURE/mal-m1-head.json"
_malformed_msg "viii-ii" "$FIXTURE/mal-m1-head.json"
_stamp_fresh

# (viii-iii) M1 regression: balanced-and-semantic-wrong sidecar still
# routes through the EXISTING shape gate (no viii-series regression).
# Garbage `git_dirty: "yes"` is a balanced, parseable-invalid value that
# the M1 fix MUST accept as content and the load-bearing shape gate
# MUST reject as `provenance-malformed`. If M1 were to short-circuit
# balanced JSON with shape issues, this test would change behavior.
cat > "$FIXTURE/mal-m1-regression.json" <<EOF
{
  "git_head": "$_head",
  "git_head_short": "${_head:0:12}",
  "git_dirty": "yes",
  "build_at": "2026-10-04T00:00:00Z",
  "build_tool": "stub",
  "artifact_sha256": "$_sha",
  "artifact_path": "stub"
}
EOF
# Expect the same token as viii-b (garbage git_dirty) — proves the M1
# gate doesn't interfere with the existing shape-gate rejection of
# balanced-but-semantic-wrong sidecars. CRITICAL: the shape-gate message
# is the EXPECTED diagnostic here (M1's balance+uniqueness gate accepts
# balanced content; the shape gate's "git_dirty not a recognized
# boolean" message is the operator-visible reason). This is the
# inversion of viii-i: viii-i MUST hit laundering; viii-iii MUST hit
# the shape gate.
_malformed_case "viii-iii balanced garbage git_dirty (M1 regression)" "$FIXTURE/mal-m1-regression.json"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$FIXTURE/d1-sbx" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(viii-iii d-1 inversion) balanced-garbage → exit 78" "78" "$rc"
assert_contains "(viii-iii d-1 inversion) shape-gate diagnostic (NOT laundering)" \
    "git_dirty not a recognized boolean" "$out"
assert_not_contains "(viii-iii d-1 inversion) does NOT misroute to laundering branch" \
    "laundering signature" "$out"
_stamp_fresh

# (viii-iv) M1 unreadable-state pin (review round 3). Pin the SPECIFIC
#     unreadable-branch MESSAGE so a regression of d-1 (or a future
#     refactor that confuses unreadable with malformed) is caught
#     loudly. The state is reached by chmod 000 on the sidecar: `[ -f
#     "$prov" ]` returns TRUE (regular file, no permission check), but
#     `cat "$prov"` returns EACCES → reader sets status=unreadable. (The
#     reviewer noted root-bypass — on a root test run, chmod 000 doesn't
#     deny. This host is non-root, so the state is genuinely reached;
#     CI under root would need a different mechanism, out of scope
#     for this round.)
cp "$FIXTURE/mal-m1-regression.json" "$FIXTURE/stub-prod.build-provenance.json"
chmod 000 "$FIXTURE/stub-prod.build-provenance.json"
[ -f "$FIXTURE/stub-prod.build-provenance.json" ] \
    && _pass || _fail "(viii-iv) fixture sanity: file present under chmod 000" "present" "absent"
UNR_SBX="$FIXTURE/unr-sbx"
rm -rf "$UNR_SBX" && mkdir -p "$UNR_SBX"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$UNR_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
chmod 644 "$FIXTURE/stub-prod.build-provenance.json"   # restore before any subsequent touch
assert_eq "(viii-iv unreadable) chmod-000 sidecar → exit 78" "78" "$rc"
# Token: the reader can't distinguish "absent" from "unreadable" by
# status alone — both the missing and unreadable branches carry the
# provenance-missing token (one is a file-presence failure class;
# the other is a file-readability failure class). The DISCRIMINATOR
# is the message — the unreadable branch specifically says "exists but
# is unreadable" while the absent branch says "no build provenance".
assert_contains "(viii-iv unreadable) reaches the unreadable branch message" \
    "exists but is unreadable" "$out"
assert_not_contains "(viii-iv unreadable) does NOT misroute to the absent branch" \
    "no build provenance for" "$out"
assert_not_contains "(viii-iv unreadable) does NOT misroute to the shape gate" \
    "git_head missing/empty" "$out"
_stamp_fresh

# ─── 10c. scenario (xii): G3 guard (review round 3) — the bare rm -f at ─────
#   stage.sh :392 that excludes the FE provenance sidecar from the release
#   payload must be guarded with the same `|| { rm -rf STAGE_TMP; exit 1; }`
#   pattern as its neighbors (:379/:381/:393/:394). Without the guard, a
#   rm failure would silently ship the sidecar inside the web-servable
#   payload and perturb the FE tree hash.
#
#   PIN DESIGN (FE verifier precedes the cp+rm at :381/:392, so the
#   guard's failure case can't be reached end-to-end via stage.sh with
#   the verifier passing — the verifier would refuse provenance-missing
#   on a missing/malformed sidecar BEFORE the cp runs). The reviewer's
#   allowed approaches are "chmod-000 the parent dir or make the sidecar
#   path a directory" — the directory path is the portable one (no
#   root). We test the guard PATTERN in isolation: the exact 4-line
#   fragment stage.sh uses at :392, run on a path that is a directory
#   (rm -f fails with "Is a directory"). The guard's `if !` branch
#   fires — exit code != 0, the cleanup message lands on stderr.
section "(xii) G3 guard catches rm -f failure on a directory sidecar path"
G3_TMP="$(mktemp -d)"
mkdir -p "$G3_TMP/frontend/dist/frontend/browser"
mkdir "$G3_TMP/frontend/dist/frontend/browser/index.html.build-provenance.json"   # dir — rm fails
G3_OUT="$(bash -c '
    G3_TMP="$1"
    # Verbatim fragment of stage.sh:392 (the guard body) — call it in a
    # subshell so we can capture the exit code without aborting the test.
    STAGE_TMP="$G3_TMP"
    rm -f "$STAGE_TMP/frontend/dist/frontend/browser/index.html.build-provenance.json" || {
        _warn() { printf "WARN: %s\n" "$*" >&2; }
        _warn "stage: FAILED to remove the FE provenance sidecar (target was a directory; the sidecar would ship inside the web-servable payload). Aborting stage to keep the release payload consistent."
        rm -rf "$STAGE_TMP"
        exit 1
    }
    echo "GUARD DID NOT FIRE — BUG"
' _ "$G3_TMP" 2>&1)"; G3_RC=$?
assert_eq "(xii G3) guard rc=1 on rm -f failure" "1" "$G3_RC"
assert_contains "(xii G3) guard's WARN names the failure" \
    "FAILED to remove the FE provenance sidecar" "$G3_OUT"
assert_contains "(xii G3) guard's WARN explains the payload risk" \
    "web-servable payload" "$G3_OUT"
# Cleanup happened (the STAGE_TMP dir is gone after the guard's cleanup).
if [ -d "$G3_TMP" ]; then
    _fail "(xii G3) guard's cleanup removed STAGE_TMP" "absent" "present"
else
    _pass
fi
rm -rf "$G3_TMP"
_stamp_fresh

# ─── 11. scenario (ix): S3 — a TAG named latest must NOT satisfy the tip ─────
section "(ix) tag named 'latest' never satisfies the tip gate (S3)"
# _tip_sha resolves refs/remotes/origin/latest then refs/heads/latest —
# full refspecs only. A repo whose ONLY `latest`-ish ref is the TAG
# refs/tags/latest (here parked on an ORPHAN commit so it never sits at
# HEAD — a tag at HEAD would hijack `git describe --exact-match` and break
# the VERSION discipline check instead, which would test the wrong gate)
# must stay in WARN+SKIP mode: the tag is not the integration branch.
_reset_fixture_repo   # no branch ref, no origin
BASE_BRANCH="$(git -C "$FAKE_REPO" rev-parse --abbrev-ref HEAD)"
git -C "$FAKE_REPO" checkout -q --orphan ghost-tip
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m ghost-tip-base
git -C "$FAKE_REPO" tag latest
git -C "$FAKE_REPO" checkout -q "$BASE_BRANCH"
# Sanity: the trap tag exists but NOT at HEAD, and no branch/latest.
[ "$(git -C "$FAKE_REPO" rev-parse refs/tags/latest 2>/dev/null)" != "$(git -C "$FAKE_REPO" rev-parse HEAD)" ] \
    && _pass || _fail "(ix) fixture sanity: trap tag is NOT at HEAD" "different" "same"
[ -z "$(git -C "$FAKE_REPO" rev-parse --verify refs/heads/latest 2>/dev/null)" ] \
    && _pass || _fail "(ix) fixture sanity: no refs/heads/latest" "absent" "present"
_stamp_fresh
TAGTRAP_SBX="$FIXTURE/tagtrap-sbx"
mkdir -p "$TAGTRAP_SBX"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$TAGTRAP_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(ix) tag-only latest → stage still passes (WARN+SKIP, not tag-as-tip)" "0" "$rc"
assert_contains "(ix) tag-only latest → L1 in WARN+SKIP (tag NOT treated as tip)" \
    "tip-identity check SKIPPED" "$out"
assert_not_contains "(ix) tag-only latest → NO non-tip-tree refusal fired" \
    "non-tip-tree" "$out"
_stamp_fresh

# ─── 12. scenario (x): S1 — override journals DURABLY on a VIRGIN install dir ─
section "(x) override event lands durably on a virgin install dir (S1)"
# The old pack pre-seeded releases/state.json for every journal assertion,
# which masked the S1 gap: on a VIRGIN install dir the append used to fail
# (journal_read on a missing file) and the override event was LOST. Now
# journal_init runs (gated on the install dir existing) before the append.
# The override must actually FIRE here: the binary sidecar is removed so
# provenance-missing triggers, and the override path journals it.
_reset_fixture_repo
_stamp_fresh
rm -f "$FIXTURE/stub-prod.build-provenance.json"   # make an override fire
VIRGIN_SBX="$FIXTURE/virgin-sbx"
mkdir -p "$VIRGIN_SBX"   # install dir EXISTS; releases/ deliberately absent
if [ -e "$VIRGIN_SBX/releases/state.json" ]; then
    _fail "(x) virgin install dir really has no journal (fixture sanity)" "absent" "present"
else
    _pass
fi
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$VIRGIN_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" --allow-stale-stage 2>&1)"; rc=$?
assert_eq "(x) override stage on virgin install dir → exit 0" "0" "$rc"
[ -f "$VIRGIN_SBX/releases/state.json" ] \
    && _pass || _fail "(x) journal DURABLY created on virgin dir" "present" "absent"
JREF6="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
print(":".join(h.get("event","") for h in d.get("history",[])))' \
    "$VIRGIN_SBX/releases/state.json" 2>/dev/null || true)"
assert_contains "(x) override event IS in the durably-created journal" \
    "stage_freshness_override" "$JREF6"
# And the unseeded REFUSAL path lands durably too (same gate on
# _freshness_refuse): refuse on a virgin dir → journal exists + event.
VIRGIN2_SBX="$FIXTURE/virgin2-sbx"
mkdir -p "$VIRGIN2_SBX"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$VIRGIN2_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(x) refusal on virgin install dir → exit 78" "78" "$rc"
[ -f "$VIRGIN2_SBX/releases/state.json" ] \
    && _pass || _fail "(x) refusal journal DURABLY created on virgin dir" "present" "absent"
JREF7="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
print(":".join(h.get("event","") for h in d.get("history",[])))' \
    "$VIRGIN2_SBX/releases/state.json" 2>/dev/null || true)"
assert_contains "(x) refusal event IS in the durably-created journal" \
    "refusal" "$JREF7"
_stamp_fresh

# ─── 10b. scenario (xi): M3 — _provenance_write refuses LOUDLY when ─────────
#   shasum is missing/unavailable (review round 2). Hermetic: source the
#   real lib.sh in a subshell, then monkey-patch `_sha256` to return
#   empty (simulates the shasum-less host without mucking with $PATH —
#   $PATH manipulation risks cascading failures in lib.sh's other
#   tool calls). The writer's M3 guard fires LOUDLY and exits 1; no
#   sidecar is written (artifact_sha256 would have been empty — the
#   exact misdirect M3 prevents).
section "(xi) M3 writer refusal on shasum-less host (empty sha)"
M3_ART="$FIXTURE/m3-art"
printf 'artifact-bytes\n' > "$M3_ART"
M3_OUT="$(REPO_ROOT="$FAKE_REPO" bash -c '
    rr="$1"; art="$2"
    . "$rr/scripts/upgrade/lib.sh"
    # Monkey-patch _sha256 to simulate the shasum-less host class
    # (missing tool, not on PATH, or failing for this artifact). The
    # real _sha256 returns a 64-hex digest; the shim returns empty.
    _sha256() { :; }
    _provenance_write "$art" "$(git -C "$rr" rev-parse HEAD)" "false" "test"
' _ "$FAKE_REPO" "$M3_ART" 2>&1)"; M3_RC=$?
assert_eq "(xi) M3 shasum-less writer → non-zero exit" "1" "$M3_RC"
assert_contains "(xi) M3 LOUD refusal names the missing tooling" "shasum" "$M3_OUT"
assert_contains "(xi) M3 names the remedy (install shasum)" "Install shasum" "$M3_OUT"
# CRITICAL: no sidecar was written (artifact_sha256:"" is the exact
# misdirect M3 prevents; verify the file is absent).
if [ -f "${M3_ART}.build-provenance.json" ]; then
    _fail "(xi) M3: NO sidecar written when shasum is empty" "absent" "present"
else
    _pass
fi
_stamp_fresh

# ─── summary ────────────────────────────────────────────────────────────────
echo
echo "== summary: $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ] || {
    echo "failed:"
    printf '%s\n' "$FAILED_TESTS"
}
# Cleanup: mktemp dirs are throwaway by construction, but be explicit
# (operators sometimes inspect /tmp after a failed run).
# shellcheck disable=SC2154
[ -n "$FIXTURE" ] && rm -rf "$FIXTURE" 2>/dev/null || true
exit "$FAIL"
