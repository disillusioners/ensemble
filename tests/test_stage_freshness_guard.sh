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

# A stub binary "serving" nothing — staging only, no daemon in unit tests.
printf '#!/bin/bash\nexit 78\n' > "$FIXTURE/stub-prod"
chmod +x "$FIXTURE/stub-prod"

# Helper: _stamp_provenance <art> <head> <head_short> <build_tool>
# Writes the sidecar in the same shape _provenance_write (lib.sh) does,
# using the same JSON keys the verifier reads. Used to set up FIXED-state
# fixtures (the test's job is to exercise the VERIFIER, not the writer;
# the writer is already covered by the existing journal pack).
_stamp_provenance() {
    local art="$1" head="$2" head_short="$3" tool="$4" \
          dirty="${5:-false}" now sha prov
    sha="$(shasum -a 256 "$art" | awk '{print $1}')"
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    prov="${art}.build-provenance.json"
    cat > "$prov" <<EOF
{
  "git_head": "$head",
  "git_head_short": "$head_short",
  "git_dirty": $dirty,
  "build_at": "$now",
  "build_tool": "$tool",
  "artifact_sha256": "$sha",
  "artifact_path": "stub:tests/test_stage_freshness_guard.sh"
}
EOF
}

# Helper: _reset_fixture_repo [extra_args...] — rebuild FAKE_REPO from
# scratch (clean working tree, single commit, SBX_V1 tag) so each scenario
# starts from a known state. Idempotent + cheap (mktemp dir is throwaway).
_reset_fixture_repo() {
    rm -rf "$FAKE_REPO"
    mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
             "$FAKE_REPO/daemon/migrations/versions" \
             "$FAKE_REPO/frontend/dist/frontend/browser"
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
    git -C "$FAKE_REPO" init -q "$@"
    git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m fixture
    SBX_V1="v1.0.0-fresh"
    git -C "$FAKE_REPO" tag "$SBX_V1"
}

# Helper: _stamp_fresh — re-stamp both stub artifacts to match the
# CURRENT FAKE_REPO HEAD. Called at the start of every test that expects
# the freshness guard to PASS so the sidecars stay in sync. After the
# call, _head / _head_short are exported for the test to read.
_stamp_fresh() {
    _head="$(git -C "$FAKE_REPO" rev-parse HEAD)"
    _head_short="${_head:0:12}"
    _stamp_provenance "$FIXTURE/stub-prod" \
        "$_head" "$_head_short" \
        "stub:tests/test_stage_freshness_guard.sh"
    _stamp_provenance "$FAKE_REPO/frontend/dist/frontend/browser/index.html" \
        "$_head" "$_head_short" \
        "stub:tests/test_stage_freshness_guard.sh"
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
         "$TIP_FIXTURE/frontend/dist/frontend/browser"
printf 'stub-agent\n' > "$TIP_FIXTURE/agents/leader/soul.md"
printf 'port: 1\n' > "$TIP_FIXTURE/config.yaml"
printf '#!/bin/bash\nexit 0\n' > "$TIP_FIXTURE/launcher.sh"
printf 'CREATE TABLE x (id int);\n' > "$TIP_FIXTURE/daemon/migrations/versions/20260101_init.sql"
printf 'stub-index\n' > "$TIP_FIXTURE/frontend/dist/frontend/browser/index.html"
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
# with the stale-provenance token.
FOREIGN_HEAD="$(printf '%040d' 0)"   # 40 zeros — a sha that no real commit has
_stamp_provenance "$FIXTURE/stub-prod" \
    "$FOREIGN_HEAD" "000000000000" \
    "stub:tests/test_stage_freshness_guard.sh"
# Also stamp FE for FAKE_REPO so the FE check doesn't fire first.
_stamp_provenance "$FAKE_REPO/frontend/dist/frontend/browser/index.html" \
    "$(git -C "$FAKE_REPO" rev-parse HEAD)" "$(git -C "$FAKE_REPO" rev-parse HEAD | cut -c1-12)" \
    "stub:tests/test_stage_freshness_guard.sh"
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
# Re-stamp stub artifacts to match the CURRENT FAKE_REPO HEAD. The guard
# must accept (rc 0, manifest written).
_stamp_fresh
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(ii) fresh artifacts → exit 0" "0" "$rc"
assert_contains "(ii) fresh artifacts → stage announces NO flip" "NO flip" "$out"
# Manifest should be present.
[ -f "$SBX/releases/$SBX_V1/manifest.json" ] && _pass || _fail "(ii) manifest exists"
# Seed the current symlink so --verify has something to check (status.sh
# --verify requires `INSTALL_DIR/current` to point at a release).
ln -sfn "releases/$SBX_V1" "$SBX/current"
# The release should pass integrity verify (status.sh --verify).
verify_out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/status.sh" sandbox --verify 2>&1)"; verify_rc=$?
assert_eq "(ii) status.sh --verify clean exit 0" "0" "$verify_rc"
assert_contains "(ii) status.sh --verify says integrity OK" "integrity OK" "$verify_out"

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
# Use TIP_FIXTURE where HEAD is on a feature branch and 'latest' points
# to the prior commit. stage.sh's _tip_sha() returns the SHA of 'latest'
# (origin/latest is unset in this throwaway, so the helper falls back
# to local 'latest'). HEAD is on feature/stale — a different SHA.
# Stamp artifacts fresh against the FEATURE-BRANCH HEAD so the L2
# provenance check passes (L1 should fire FIRST).
tip_head="$(git -C "$TIP_FIXTURE" rev-parse HEAD)"
tip_head_short="${tip_head:0:12}"
# Copy stub-prod to TIP_FIXTURE so the sidecar lives in the fixture repo.
cp "$FIXTURE/stub-prod" "$TIP_FIXTURE/stub-prod"
chmod +x "$TIP_FIXTURE/stub-prod"
_stamp_provenance "$TIP_FIXTURE/stub-prod" \
    "$tip_head" "$tip_head_short" \
    "stub:tests/test_stage_freshness_guard.sh"
# FE: stamp against the feature-branch HEAD.
_stamp_provenance "$TIP_FIXTURE/frontend/dist/frontend/browser/index.html" \
    "$tip_head" "$tip_head_short" \
    "stub:tests/test_stage_freshness_guard.sh"
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

# ─── 9. scenario (vii): dirty-build refuses ────────────────────────────────
section "(vii) dirty-build sidecar → refuse (dirty-build)"
# Stamp the sidecar with git_dirty=true to simulate a build on a
# dirty tree. The guard must refuse with the dirty-build token.
_stamp_fresh
# Manually rewrite the sidecar with dirty=true (override the helper's
# hard-coded false).
head="$(git -C "$FAKE_REPO" rev-parse HEAD)"
head_short="${head:0:12}"
sha="$(shasum -a 256 "$FIXTURE/stub-prod" | awk '{print $1}')"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$FIXTURE/stub-prod.build-provenance.json" <<EOF
{
  "git_head": "$head",
  "git_head_short": "$head_short",
  "git_dirty": true,
  "build_at": "$now",
  "build_tool": "stub:tests/test_stage_freshness_guard.sh",
  "artifact_sha256": "$sha",
  "artifact_path": "stub:tests/test_stage_freshness_guard.sh"
}
EOF
DIRTY_SBX="$FIXTURE/dirty-sbx"
mkdir -p "$DIRTY_SBX/releases"
cat > "$DIRTY_SBX/releases/state.json" \
    <<'EOF'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
EOF
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$DIRTY_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(vii) dirty-build sidecar → exit 78" "78" "$rc"
assert_contains "(vii) dirty-build sidecar → refusal token: dirty-build" \
    "dirty-build" "$out"
# Override unlocks.
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$DIRTY_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" --allow-stale-stage 2>&1)"; rc=$?
assert_eq "(vii) override on dirty-build → exit 0" "0" "$rc"
# The override event carries reason=dirty-build.
JREF4="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
for h in d.get("history",[]):
    if h.get("event") == "stage_freshness_override":
        print(h.get("detail",""))' \
    "$DIRTY_SBX/releases/state.json" 2>/dev/null || true)"
assert_contains "(vii) override reason=dirty-build journaled" \
    "reason=dirty-build" "$JREF4"

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
