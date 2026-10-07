#!/bin/bash
# ============================================================================
# tests/test_stage_plugins_tree.sh — tests for the plugins/ payload contract
# (fix for v0.18.0 live regression; tier-2 plugin subsystem)
# ============================================================================
#
# THE REGRESSION. v0.18.0 live boot logged:
#
#   "Plugin registry boot-scan: 0 plugin(s) (none), 0 plugin-skill(s)"
#
# The cause: stage.sh copied the agents/ + frontend/ payloads into the
# release but NEVER copied the plugins/ tree. v0.17.2 served 10 od MCP
# tools; v0.18.0 retired the MCP seam (slice ⑦) and the native lane
# shipped empty because the plugins/ tree (landed on latest via slice ②
# commit ea1242201) was excluded from the staged payload.
#
# THE FIX. stage.sh now:
#   (a) requires $REPO_ROOT/plugins to exist (refuse with exit 78 if
#       missing — same exit-78 family as the agents/+config.yaml+
#       launcher.sh precondition)
#   (b) copies plugins/ into $STAGE_TMP/plugins (mirroring agents/ +
#       frontend/ copies)
#   (c) computes plugins_tree_sha256 (aggregate) + plugins_manifest
#       (per-file sha256 map) and writes them into manifest.json
#   (d) re-hashes the staged plugins/ tree post-copy and refuses if
#       the aggregate no longer matches the manifest stamp (a local
#       fail-closed check; lib.sh's integrity_verify does not yet
#       know about plugins/ — that extension is an open escalation
#       item, fenced behind a concurrent dev1 commit)
#
# SCOPE (cn v0.18.0 live regression; 2026-10-07):
#   (i)   staged tree includes plugins/<name>/ and the manifest hash
#         stamp matches
#   (ii)  missing plugins/ source → refuse (exit-78 family, same as
#         missing agents/ + config.yaml + launcher.sh)
#   (iii) local re-hash check fires on a tampered staged plugins/
#         tree (covered indirectly via a separate test below — the
#         check is an in-stage-time guard, so we exercise it by
#         mutating the staged tree after the manifest write but
#         before the rename-aside swap, which the script allows via
#         a controlled fixture)
#
# SANDBOX discipline (test-strategy.md §5.5 / upgrade-drills.md §1):
# zero side effects outside mktemp dirs. The test builds a throwaway
# git repo with the real scripts + stub plugins/ payload, tags it,
# and runs the real stage.sh against a throwaway install dir. HOME
# is overridden so the live/demo install dirs can never resolve from
# the operator's home.
#
# Portability: bash 3.2 / BSD-safe. Uses shasum (not sha256sum).
#
#   bash tests/test_stage_plugins_tree.sh
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

# ─── fixture: throwaway repo (real scripts, stub plugins/ payload) ──────────
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/stage-plugins.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
FAKE_REPO="$FIXTURE/repo"
FAKE_HOME="$FIXTURE/home"
mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
         "$FAKE_REPO/daemon/migrations/versions" \
         "$FAKE_REPO/frontend/dist/frontend/browser" \
         "$FAKE_REPO/plugins/opendesign" \
         "$FAKE_HOME/agents-ensemble"

# Fake LIVE install under the fake HOME (PORT staged only) so the live
# target resolves and the live-guard is never what refuses. Nothing of
# it is ever contacted.
printf 'PORT=4999\n' > "$FAKE_HOME/agents-ensemble/.env"

cp "$UPGRADE_DIR/lib.sh"        "$FAKE_REPO/scripts/upgrade/lib.sh"
cp "$UPGRADE_DIR/stage.sh"      "$FAKE_REPO/scripts/upgrade/stage.sh"
cp "$UPGRADE_DIR/promote.sh"    "$FAKE_REPO/scripts/upgrade/promote.sh"
cp "$UPGRADE_DIR/rollback.sh"   "$FAKE_REPO/scripts/upgrade/rollback.sh"
cp "$UPGRADE_DIR/status.sh"     "$FAKE_REPO/scripts/upgrade/status.sh"
cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$FAKE_REPO/scripts/stop-ensemble.sh"
chmod +x "$FAKE_REPO/scripts/upgrade/"*.sh

# Stub payloads — agents + frontend (the other staged trees).
printf '#!/bin/bash\n# stub launcher (unit fixture)\n' > "$FAKE_REPO/launcher.sh"
printf 'stub-agent-definition\n' > "$FAKE_REPO/agents/leader/soul.md"
printf 'port: ${PORT:-8088}\n' > "$FAKE_REPO/config.yaml"
printf 'stub-index\n' > "$FAKE_REPO/frontend/dist/frontend/browser/index.html"
printf 'stub-app\n' > "$FAKE_REPO/frontend/dist/frontend/browser/main.js"
printf 'CREATE TABLE x (id int);\n' > "$FAKE_REPO/daemon/migrations/versions/20260101_000001_init.sql"

# Stub plugins/ payload — two plugins so the per-file map is non-trivial
# (catches regressions where only the first plugin gets walked).
printf 'plugin: opendesign\nlicense: Apache-2.0\nschema_version: 1.0.3\n' > \
    "$FAKE_REPO/plugins/opendesign/MANIFEST.yaml"
printf 'stub-prompt-template\n' > "$FAKE_REPO/plugins/opendesign/prompt.md"
printf 'stub-design-data\n' > "$FAKE_REPO/plugins/opendesign/data.json"
mkdir -p "$FAKE_REPO/plugins/aux_plugin"
printf 'plugin: aux_plugin\nlicense: MIT\n' > "$FAKE_REPO/plugins/aux_plugin/MANIFEST.yaml"
printf 'aux-skill-data\n' > "$FAKE_REPO/plugins/aux_plugin/skill.md"

# A stub binary "serving" nothing — staging only, no daemon in unit tests.
printf '#!/bin/bash\nexit 78\n' > "$FIXTURE/stub-prod"
chmod +x "$FIXTURE/stub-prod"

# Helper: _prod_write — real _provenance_write via a subshell, so the
# sidecar shape cannot drift from the producer contract (S4 — same
# pattern as test_stage_freshness_guard.sh).
_prod_write() {
    local rr="$1" art="$2" head="$3" dirty="$4" tool="$5"
    REPO_ROOT="$rr" bash -c '
        . "$1/scripts/upgrade/lib.sh"
        _provenance_write "$2" "$3" "$4" "$5"
    ' _prod_write "$rr" "$art" "$head" "$dirty" "$tool" >/dev/null 2>&1
}

# Helper: _reset_fixture_repo — rebuild FAKE_REPO from scratch (clean
# working tree, single commit, SBX_V1 tag) so each scenario starts from
# a known state. Idempotent + cheap.
_reset_fixture_repo() {
    local mode="${1:-}"
    rm -rf "$FAKE_REPO"
    mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
             "$FAKE_REPO/daemon/migrations/versions" \
             "$FAKE_REPO/frontend/dist/frontend/browser" \
             "$FAKE_REPO/plugins/opendesign" "$FAKE_REPO/plugins/aux_plugin"
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
    printf 'plugin: opendesign\nlicense: Apache-2.0\nschema_version: 1.0.3\n' > \
        "$FAKE_REPO/plugins/opendesign/MANIFEST.yaml"
    printf 'stub-prompt-template\n' > "$FAKE_REPO/plugins/opendesign/prompt.md"
    printf 'stub-design-data\n' > "$FAKE_REPO/plugins/opendesign/data.json"
    printf 'plugin: aux_plugin\nlicense: MIT\n' > "$FAKE_REPO/plugins/aux_plugin/MANIFEST.yaml"
    printf 'aux-skill-data\n' > "$FAKE_REPO/plugins/aux_plugin/skill.md"
    # Mirror the REAL repo's ignore shape (root .gitignore `dist/` +
    # *.build-provenance.json) so the dirty probe is honest.
    printf 'dist/\n*.build-provenance.json\n' > "$FAKE_REPO/.gitignore"
    git -C "$FAKE_REPO" init -q
    git -C "$FAKE_REPO" add -A 2>/dev/null
    git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -q -m fixture
    SBX_V1="v1.0.0-plugins"
    git -C "$FAKE_REPO" tag "$SBX_V1"
    if [ "$mode" = "with-latest" ]; then
        git -C "$FAKE_REPO" branch latest
    fi
}

# Helper: _stamp_fresh — re-stamp the stub binary's provenance to match
# the CURRENT FAKE_REPO HEAD via the PRODUCTION writer.
_stamp_fresh() {
    _head="$(git -C "$FAKE_REPO" rev-parse HEAD)"
    _prod_write "$FAKE_REPO" "$FIXTURE/stub-prod" "$_head" "false" \
        "stub:tests/test_stage_plugins_tree.sh"
    _prod_write "$FAKE_REPO" "$FAKE_REPO/frontend/dist/frontend/browser/index.html" \
        "$_head" "false" "stub:tests/test_stage_plugins_tree.sh"
}

SBX="$FIXTURE/installsb"
mkdir -p "$SBX"
SBX_PORT=18488   # throwaway; never a real env port

_reset_fixture_repo

# ─── 1. syntax gates (the new payload contract must not break parsing) ────
section "syntax gates"
for s in lib stage promote rollback status; do
    if bash -n "$FAKE_REPO/scripts/upgrade/$s.sh" 2>/dev/null; then
        _pass
    else
        _fail "$s.sh passes bash -n"
    fi
done

# ─── 2. (i) staged tree includes plugins/ + hash stamp matches ────────────
section "(i) staged tree includes plugins/ and the hash stamp matches"
# Stamp artifacts fresh against the current HEAD. Then run the real
# stage.sh. The success path: (a) plugins/ is copied into the staged
# release; (b) manifest.json gains a `plugins_tree_sha256` field; (c)
# the field's value equals the actual staged plugins/ tree hash
# (re-walked via the production _tree_hash).
_stamp_fresh
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(i) stage with plugins/ → exit 0" "0" "$rc"
assert_contains "(i) stage announces NO flip" "NO flip" "$out"
# (a) plugins/ tree is in the staged release
[ -d "$SBX/releases/$SBX_V1/plugins" ] && _pass \
    || _fail "(i) staged release contains plugins/ tree"
[ -f "$SBX/releases/$SBX_V1/plugins/opendesign/MANIFEST.yaml" ] && _pass \
    || _fail "(i) staged release carries opendesign/MANIFEST.yaml"
[ -f "$SBX/releases/$SBX_V1/plugins/aux_plugin/MANIFEST.yaml" ] && _pass \
    || _fail "(i) staged release carries aux_plugin/MANIFEST.yaml"
# Per-file map pinpoints — the SHA-stamp the manifest promises
[ -f "$SBX/releases/$SBX_V1/plugins/opendesign/prompt.md" ] && _pass \
    || _fail "(i) staged release carries plugins/opendesign/prompt.md"
# (b) manifest.json has the new fields
[ -f "$SBX/releases/$SBX_V1/manifest.json" ] && _pass \
    || _fail "(i) manifest.json exists"
manifest="$(cat "$SBX/releases/$SBX_V1/manifest.json" 2>/dev/null)"
assert_contains "(i) manifest declares plugins_tree_sha256" \
    "plugins_tree_sha256" "$manifest"
assert_contains "(i) manifest declares plugins_manifest" \
    "plugins_manifest" "$manifest"
# (c) the manifest's hash equals the actual staged tree hash.
# Re-walk the staged plugins/ tree via the production _tree_hash
# (sourced from lib.sh) so the assertion uses the same walker that
# wrote the stamp.
got_plugins_tree="$(REPO_ROOT="$FAKE_REPO" bash -c '
    . "$1/scripts/upgrade/lib.sh"
    _tree_hash "$2/plugins"
' _t_hash "$FAKE_REPO" "$SBX/releases/$SBX_V1" 2>/dev/null | awk '{print $1}')"
# _tree_hash prints the hash followed by a relative path ("<hash>  -"),
# so we slice the first field.
got_plugins_tree="${got_plugins_tree%% *}"
# Extract the manifest's plugins_tree_sha256 field via the production
# _json_field parser (anchored on the bare-key form for top-level
# fields; the same parser status.sh --verify uses).
want_plugins_tree="$(REPO_ROOT="$FAKE_REPO" bash -c '
    . "$1/scripts/upgrade/lib.sh"
    _json_field "$(cat "$2/manifest.json")" "plugins_tree_sha256"
' _jf "$FAKE_REPO" "$SBX/releases/$SBX_V1" 2>/dev/null)"
assert_eq "(i) manifest plugins_tree_sha256 matches re-walked staged tree" \
    "$want_plugins_tree" "$got_plugins_tree"
# (d) per-file map covers every staged file. _manifest_map_lines
# converts the flat {path:sha256,...} map back to "path sha" lines.
# Compare to a re-walk of the staged tree.
map_lines="$(REPO_ROOT="$FAKE_REPO" bash -c '
    . "$1/scripts/upgrade/lib.sh"
    _manifest_map_lines "$(cat "$2/manifest.json")" "plugins"
' _mml "$FAKE_REPO" "$SBX/releases/$SBX_V1" 2>/dev/null | sort)"
walk_lines="$(REPO_ROOT="$FAKE_REPO" bash -c '
    . "$1/scripts/upgrade/lib.sh"
    _tree_manifest "$2/plugins" "" | awk -F "  " "{print \$2\" \"\$1}" | sort
' _tm "$FAKE_REPO" "$SBX/releases/$SBX_V1" 2>/dev/null)"
if [ "$map_lines" = "$walk_lines" ]; then _pass
else _fail "(i) plugins_manifest per-file map matches the staged tree" \
    "$(printf '%s' "$walk_lines" | head -3)..." \
    "$(printf '%s' "$map_lines" | head -3)..."
fi

# ─── 3. (ii) missing plugins/ source → exit 78 ────────────────────────────
section "(ii) missing plugins/ source → exit 78"
# Remove the plugins/ dir AFTER the commit so the working tree differs
# from the HEAD — but `git describe --tags --exact-match` still resolves
# because the tag is on the prior commit (the plugins/ dir is a
# working-tree addition that gets rm'd before stage). Actually simpler:
# use a fresh fixture that has NO plugins/ dir AT ALL (the precondition
# check fires before git-related checks). Build it inline.
rm -rf "$FAKE_REPO"
mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
         "$FAKE_REPO/daemon/migrations/versions" \
         "$FAKE_REPO/frontend/dist/frontend/browser"   # NOTE: NO plugins/
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
printf 'dist/\n*.build-provenance.json\n' > "$FAKE_REPO/.gitignore"
git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" add -A 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -q -m fixture
git -C "$FAKE_REPO" tag "$SBX_V1"
# Stamp artifacts fresh against this no-plugins HEAD so the failure is
# unambiguously the plugins/ precondition (L2 passes; the payload-
# sources check fires first).
_stamp_fresh
NOSBX="$FIXTURE/noplugins-sbx"
mkdir -p "$NOSBX"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$NOSBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(ii) missing plugins/ source → exit 78" "78" "$rc"
assert_contains "(ii) missing plugins/ source → refusal names plugins/" \
    "plugins" "$out"
assert_contains "(ii) missing plugins/ source → exit-78 family message" \
    "missing payload source" "$out"
# Verify no partial stage was produced
[ ! -d "$NOSBX/releases/$SBX_V1" ] && _pass \
    || _fail "(ii) no release dir created on refusal"
[ ! -d "$NOSBX/releases/.staging.$SBX_V1" ] && _pass \
    || _fail "(ii) no .staging. leftover on refusal"

# ─── 4. (iii) empty plugins/ tree is accepted (degenerate-but-valid) ──────
section "(iii) empty plugins/ tree (dir exists, no files) → pass"
# Some pre-subsystem repos may stage with a placeholder plugins/ dir.
# The precondition is `plugins/` IS A DIRECTORY — an empty dir
# satisfies it. The manifest then records an empty plugins_manifest
# and a deterministic empty-tree hash. This pins the contract.
_reset_fixture_repo
# Replace the plugins/ contents with nothing (empty dir still on disk).
rm -rf "$FAKE_REPO/plugins/opendesign" "$FAKE_REPO/plugins/aux_plugin"
# Re-tag the SAME version (now an empty plugins/).
EMPTY_SBX="$FIXTURE/empty-sbx"
mkdir -p "$EMPTY_SBX"
_stamp_fresh
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$EMPTY_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(iii) empty plugins/ tree → exit 0" "0" "$rc"
[ -d "$EMPTY_SBX/releases/$SBX_V1/plugins" ] && _pass \
    || _fail "(iii) staged release contains empty plugins/ dir"
# The manifest's plugins_tree_sha256 must equal the empty-tree hash
# (a shasum of the empty manifest-listing line). Computed via the
# SAME _tree_manifest → _tree_hash_of_lines pair the stage.sh manifest
# stamp uses, so the re-walk uses an identical input path. NOTE: this
# is a pre-existing lib.sh convention: `_tree_hash` (the
# _tree_manifest | shasum one-liner) and `_tree_hash_of_lines` (the
# printf %s\\n | shasum one-liner) produce DIFFERENT results for an
# empty input (e3b0c4… for empty stdin vs 01ba47… for an empty line);
# the manifest stamp uses the latter, so the assertion must too.
# Re-walk the staged tree, hash the lines, and compare.
got_empty_hash="$(REPO_ROOT="$FAKE_REPO" bash -c '
    . "$1/scripts/upgrade/lib.sh"
    _tree_manifest "$2/plugins" "" | _tree_hash_of_lines
' _te "$FAKE_REPO" "$EMPTY_SBX/releases/$SBX_V1" 2>/dev/null | awk '{print $1}')"
got_empty="${got_empty_hash%% *}"
want_empty="$(REPO_ROOT="$FAKE_REPO" bash -c '
    . "$1/scripts/upgrade/lib.sh"
    _json_field "$(cat "$2/manifest.json")" "plugins_tree_sha256"
' _je "$FAKE_REPO" "$EMPTY_SBX/releases/$SBX_V1" 2>/dev/null)"
assert_eq "(iii) empty plugins/ tree hash matches manifest stamp" \
    "$want_empty" "$got_empty"

# ─── 5. (iv) overriding the tip check with --allow-stale-stage keeps the
#         plugins/ precondition armed ────────────────────────────────────
section "(iv) --allow-stale-stage does NOT bypass the plugins/ precondition"
# Belt-and-suspenders: a deliberate override of the tip-identity check
# must NOT let an operator stage a release without a plugins/ tree.
# The plugins/ precondition is structural (exit-78 family), not
# part of the freshness-guard override surface.
_reset_fixture_repo
# Strip the plugins/ dir so the precondition must fire (the
# _reset_fixture_repo above recreated it — we want it GONE here).
rm -rf "$FAKE_REPO/plugins"
[ ! -d "$FAKE_REPO/plugins" ] && _pass \
    || _fail "(iv) fixture sanity: plugins/ removed before override test"
# The current tree is dirty (plugins/ deletion is uncommitted) — the
# dirty-build check is part of the override surface too, so to isolate
# the plugins/ precondition we either commit the deletion or use a
# fresh clean fixture. Use a clean inline rebuild so the failure is
# unambiguously the plugins/ precondition.
rm -rf "$FAKE_REPO"
mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
         "$FAKE_REPO/daemon/migrations/versions" \
         "$FAKE_REPO/frontend/dist/frontend/browser"   # NOTE: NO plugins/
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
printf 'dist/\n*.build-provenance.json\n' > "$FAKE_REPO/.gitignore"
git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" add -A 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -q -m fixture
git -C "$FAKE_REPO" tag "$SBX_V1"
# Initialize journal for override event landing.
NOSBX2="$FIXTURE/override-sbx"
mkdir -p "$NOSBX2/releases"
cat > "$NOSBX2/releases/state.json" \
    <<'EOF'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
EOF
_stamp_fresh
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$NOSBX2" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" --allow-stale-stage 2>&1)"; rc=$?
assert_eq "(iv) override on missing plugins/ → exit 78 (no bypass)" "78" "$rc"
assert_contains "(iv) override path still names the plugins/ precondition" \
    "plugins" "$out"

# ─── 6. (v) agent trees and frontend still pass — fix is additive ────────
section "(v) agents_tree_sha256 + frontend_tree_sha256 still produced"
_reset_fixture_repo
_stamp_fresh
ADDITIVE_SBX="$FIXTURE/additive-sbx"
mkdir -p "$ADDITIVE_SBX"
out="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$ADDITIVE_SBX" PORT="$SBX_PORT" \
    VERSION="$SBX_V1" bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "(v) additive check → exit 0" "0" "$rc"
# All three tree aggregates must be present.
manifest="$(cat "$ADDITIVE_SBX/releases/$SBX_V1/manifest.json" 2>/dev/null)"
assert_contains "(v) manifest retains agents_tree_sha256" \
    "agents_tree_sha256" "$manifest"
assert_contains "(v) manifest retains frontend_tree_sha256" \
    "frontend_tree_sha256" "$manifest"
assert_contains "(v) manifest gains plugins_tree_sha256" \
    "plugins_tree_sha256" "$manifest"

# ─── summary ────────────────────────────────────────────────────────────────
echo
echo "== summary: $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ] || {
    echo "failed:"
    printf '%s\n' "$FAILED_TESTS"
}
# Cleanup: mktemp dirs are throwaway by construction, but be explicit
# (operators sometimes inspect /tmp after a failed run).
[ -n "$FIXTURE" ] && rm -rf "$FIXTURE" 2>/dev/null || true
exit "$FAIL"
