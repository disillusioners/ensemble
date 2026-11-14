#!/bin/bash
# ============================================================================
# tests/test_guidelines_payload.sh — the guideline payload seam (hermetic)
# ============================================================================
# Pins the phase-3 release seam: stage.sh copies docs/guidelines/ into the
# staged payload as the FLAT guidelines/ tree at the release root (the
# runtime guideline reader resolves <exe-dir>/guidelines — the one
# cross-phase contract with the phase-1 reader), the manifest gains
# `guidelines_tree_sha256` (a guidelines tree sha alongside
# agents/frontend/plugins/scripts) + the per-file `guidelines_manifest`
# map, and a stage-local re-hash leg refuses the swap-in on a copy glitch
# (lib.sh integrity_verify loops `agents frontend` only — without the
# local leg the guidelines tree is verified NOWHERE).
#
# Legs (test-matrix.md D1–D7):
#   D1  staged guidelines/ == the source docs/guidelines file set, FLAT at
#       the release root (no docs/ nesting)
#   D2  manifest JSON valid; both guidelines keys present alongside the
#       other four tree shas; `guidelines_tree_sha256` matches an
#       INDEPENDENT recompute replicating the EXACT lib.sh _tree_manifest
#       ordering (find . -type f | sed 's|^\./||' | sort → null-delimited →
#       xargs -0 -n 500 shasum -a 256 → aggregate sha256 over the
#       "<sha>␠␠rel" lines, one trailing newline per line). A per-directory
#       walk sort would FALSE-MISMATCH (bracket-dir/ordering caveat) — the
#       ordering discipline IS the assertion.
#   D3  `guidelines_manifest` composes with lib.sh's _manifest_map_lines
#       verifier; every key tree-relative (flat tree ⇒ no "/"); every
#       value == the recomputed sha256 of the staged file
#   D4  the stage-local re-hash leg runs GREEN on an unmodified stage
#       (stage exits 0, no MISMATCH line — the leg is inside every green
#       stage; D5 proves it actually refuses)
#   D5  NEGATIVE: one staged guidelines file corrupted POST-compute (after
#       manifest.json lands in the staging dir, before the re-hash legs) →
#       stage refuses the swap-in (exit 1, warning names guidelines)
#   D6  NEGATIVE: missing docs/guidelines SOURCE dir → precondition guard
#       refuses (exit 78, warning names docs/guidelines)
#   D7  fixture discipline: mktemp-only fixtures; the live install dir is
#       NEVER read or written; every resolved fixture path stays inside
#       the mktemp root (escape guard)
#
# HERMETIC BY CONSTRUCTION (executor-family header discipline:
# tests/test_executor_family_payload.sh:22-33):
#   * every fixture is built under a fresh mktemp dir under $TMPDIR; the
#     LIVE install dir is NEVER read or written (no ~/agents-ensemble, no
#     make install, no stop-ensemble.sh execution, no daemon lifecycle);
#   * HOME is overridden to a fixture-local fake home on EVERY stage call,
#     so demo/live topology resolution can never touch real host dirs
#     (F1 fake-HOME fence discipline);
#   * the payload is staged by the REAL stage.sh against a THROWAWAY repo
#     fixture (real scripts + stub payload trees + synthetic git history)
#     — the same fixture shape tests/test_release_journal.sh uses, so the
#     pin is on the real shipping logic, not a re-implementation;
#   * the D5 corruption lands inside the throwaway staging dir only (a
#     poller gated on the staging manifest's existence — deterministic:
#     the manifest heredoc precedes the re-hash legs in stage.sh — never
#     a sleep-timed race against an external path).
#
# Exit codes: 0 = all pass, 1 = failures.
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"

# M4: shared helpers + M1 fixture registry (ONE EXIT trap tears the
# mktemp root down).
# shellcheck source=tests/upgrade_test_lib.sh
. "$REPO_ROOT/tests/upgrade_test_lib.sh"

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

section() { printf '\n== %s ==\n' "$1"; }

# ─── fixture root + escape guard (D7 discipline, enforced up front) ─────────
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/guidelines-payload.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
_register_fixture "$FIXTURE"

case "$FIXTURE" in
    /tmp/*) _pass ;;
    *) printf 'FATAL D7: fixture root escapes /tmp (%s) — refusing to run\n' "$FIXTURE" >&2; exit 1 ;;
esac
case "$(cd "$FIXTURE" && pwd -P)" in
    *"/home/nea"*) printf 'FATAL D7: fixture root touches /home/nea (%s) — refusing to run\n' "$FIXTURE" >&2; exit 1 ;;
    *) _pass ;;
esac

FAKE_REPO="$FIXTURE/repo"
FAKE_HOME="$FIXTURE/home"
mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
         "$FAKE_REPO/daemon/migrations/versions" \
         "$FAKE_REPO/daemon/tools" \
         "$FAKE_REPO/daemon/plugin_subsystem" \
         "$FAKE_REPO/frontend/dist/frontend/browser" \
         "$FAKE_REPO/plugins/opendesign" \
         "$FAKE_REPO/docs/guidelines" \
         "$FAKE_HOME/agents-ensemble"

# Fake LIVE install under the fake HOME (PORT staged only) so a live/demo
# target RESOLVES if one were ever requested — nothing of it is ever
# contacted; every stage call in this suite is TARGET=sandbox.
printf 'PORT=4999\n' > "$FAKE_HOME/agents-ensemble/.env"

# Stub plugins/ payload — the dict plugin.name shape (same fixture shape
# as test_release_journal.sh: avoids AttributeError in the staleness
# predicate's manifest.get("plugin").get("name")).
printf 'plugin:\n  name: opendesign\nschema_version: 1.0.3\n' \
    > "$FAKE_REPO/plugins/opendesign/MANIFEST.yaml"

# Stub staleness predicate — unconditionally FRESH (lib.sh refuses
# FAIL-CLOSED with plugin-staleness-unreadable when the module is absent;
# the fixture mirrors the real module's exit contract).
cat > "$FAKE_REPO/daemon/plugin_subsystem/promote_staleness.py" <<'PYEOF'
#!/usr/bin/env python3
"""Stub promote-staleness predicate (FAKE_REPO fixture only)."""
import sys

def main() -> int:
    plugin_dir = sys.argv[1] if len(sys.argv) > 1 else ""
    name = plugin_dir.rstrip("/").split("/")[-1] if plugin_dir else "unknown"
    print(f"PLUGIN-STALENESS=fresh plugin={name}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
PYEOF
chmod +x "$FAKE_REPO/daemon/plugin_subsystem/promote_staleness.py"

# The REAL release scripts — the pin is on the shipping logic.
cp "$UPGRADE_DIR/lib.sh"          "$FAKE_REPO/scripts/upgrade/lib.sh"
cp "$UPGRADE_DIR/journal_splice.py" "$FAKE_REPO/scripts/upgrade/journal_splice.py"
cp "$UPGRADE_DIR/stage.sh"        "$FAKE_REPO/scripts/upgrade/stage.sh"
cp "$UPGRADE_DIR/promote.sh"      "$FAKE_REPO/scripts/upgrade/promote.sh"
cp "$UPGRADE_DIR/rollback.sh"     "$FAKE_REPO/scripts/upgrade/rollback.sh"
cp "$UPGRADE_DIR/status.sh"       "$FAKE_REPO/scripts/upgrade/status.sh"
cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$FAKE_REPO/scripts/stop-ensemble.sh"
# The whole executor family — stage.sh SOURCES executor_family.sh and
# COPIES the family into the payload; without it stage.sh dies at the
# source line and measures nothing (journal-suite fixture rationale).
for _fam in _build_frontend.sh adopt-unit.sh executor_family.sh journal_read.py \
            journal_splice.py ledger_check.py lib.sh promote.sh restart.sh \
            rollback.sh stage.sh status.sh; do
    cp "$UPGRADE_DIR/$_fam" "$FAKE_REPO/scripts/upgrade/$_fam"
done
cp "$REPO_ROOT/scripts/install-dir-guard.sh" "$FAKE_REPO/scripts/install-dir-guard.sh"
chmod +x "$FAKE_REPO/scripts/upgrade/"*.sh

# Stub payloads — the other staged trees.
printf '#!/bin/bash\n# stub launcher (unit fixture)\n' > "$FAKE_REPO/launcher.sh"
printf 'stub-agent-definition\n' > "$FAKE_REPO/agents/leader/soul.md"
printf 'port: ${PORT:-8088}\n' > "$FAKE_REPO/config.yaml"
printf 'stub-index\n' > "$FAKE_REPO/frontend/dist/frontend/browser/index.html"
printf 'stub-app\n' > "$FAKE_REPO/frontend/dist/frontend/browser/main.js"
printf 'CREATE TABLE x (id int);\n' > "$FAKE_REPO/daemon/migrations/versions/20260101_000001_init.sql"

# The REAL guideline library — the exact files this seam ships (data-only
# .md content; safe under the _no_env_in_release whole-tree walk).
cp "$REPO_ROOT"/docs/guidelines/*.md "$FAKE_REPO/docs/guidelines/"

# Mirror the REAL repo's ignore shape — the FE provenance sidecar must be
# invisible to `git status --porcelain` or the honest dirty probe classifies
# the sidecar itself as an uncommitted change (self-referential dirty-build).
printf 'dist/\n*.build-provenance.json\n' > "$FAKE_REPO/.gitignore"

# A stub binary "serving" nothing — staging only, no daemon in unit tests.
printf '#!/bin/bash\nexit 78\n' > "$FIXTURE/stub-prod"
chmod +x "$FIXTURE/stub-prod"

# git-tag the fixture repo so stage.sh's exact-tag guard passes, then pin
# BOTH floor constants to the fixture's own commit (the W-2 gate requires
# PIPELINE_SCRIPTS_REV to be a 40-hex commit the tree CONTAINS and to equal
# PIPELINE_SCRIPTS_FLOOR_COMMIT — the same shape the real release bump
# produces; the journal-suite fixture rationale verbatim).
git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" add -A 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm fixture
FIXTURE_STAMP="$(git -C "$FAKE_REPO" rev-parse HEAD)"
sed -i.bak "s/^PIPELINE_SCRIPTS_REV=.*/PIPELINE_SCRIPTS_REV=$FIXTURE_STAMP/" \
    "$FAKE_REPO/scripts/upgrade/lib.sh"
rm -f "$FAKE_REPO/scripts/upgrade/lib.sh.bak"
cat > "$FAKE_REPO/daemon/tools/upgrade_tools.py" <<EOFUP
# fixture stub — only the D3 release-time floor constant is read by stage.sh
PIPELINE_SCRIPTS_FLOOR_COMMIT = "$FIXTURE_STAMP"
EOFUP
git -C "$FAKE_REPO" add -A 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm "pin fixture stamp/floor"
SBX_V1="v1.0.0-guidelines"
git -C "$FAKE_REPO" tag "$SBX_V1"

SBX="$FIXTURE/installsb";   mkdir -p "$SBX"
SBX2="$FIXTURE/installsbx2"; mkdir -p "$SBX2"
SBX_PORT=18521   # fixture-owned throwaway port; never a real env port

# _stage_fresh — re-stamp the stub artifacts' provenance sidecars to the
# CURRENT FAKE_REPO HEAD via the PRODUCTION writer (_provenance_write in
# lib.sh, sourced in a subshell) so the fixture sidecar shape cannot drift
# from the build path (S4; same helper shape as test_release_journal.sh).
_stage_fresh() {
    local head dirty art
    head="$(git -C "$FAKE_REPO" rev-parse HEAD 2>/dev/null)" || return 0
    dirty="$(REPO_ROOT="$FAKE_REPO" bash -c '
        . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh"
        _git_dirty_porcelain
    ' 2>/dev/null)"
    [ -n "$dirty" ] || dirty=false
    for art in "$FIXTURE/stub-prod" \
               "$FAKE_REPO/frontend/dist/frontend/browser/index.html"; do
        [ -f "$art" ] || continue
        REPO_ROOT="$FAKE_REPO" bash -c '
            . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh"
            _provenance_write "$1" "$2" "$3" "stub:tests/test_guidelines_payload.sh"
        ' _stage_stamp "$art" "$head" "$dirty" >/dev/null 2>&1 || {
            printf 'FAIL: _stage_fresh: production writer FAILED for %s\n' "$art" >&2
            FAIL=$((FAIL + 1))
        }
    done
}

# _run_stage <install_dir> — the real stage.sh, TARGET=sandbox, fake HOME,
# pre-stamped artifacts. Everything hermetic; echoes combined output.
_run_stage() {
    local inst="$1"
    HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$inst" PORT="$SBX_PORT" \
        VERSION="$SBX_V1" \
        bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox \
        --skip-build "$FIXTURE/stub-prod" 2>&1
}

# ─── 0. syntax gates ────────────────────────────────────────────────────────
section "syntax gates"
if bash -n "$FAKE_REPO/scripts/upgrade/stage.sh" 2>/dev/null; then _pass; else _fail "stage.sh passes bash -n"; fi
if bash -n "$FAKE_REPO/scripts/upgrade/lib.sh" 2>/dev/null; then _pass; else _fail "lib.sh passes bash -n"; fi

# ─── 1. (D1+D2+D3+D4) green stage: payload, manifest keys, hashes ──────────
section "green stage: guidelines/ payload + manifest + hashes"
_stage_fresh
stage_out="$(_run_stage "$SBX")"
stage_rc=$?
assert_eq "green stage exits 0" "0" "$stage_rc"
assert_contains "stage announces NO flip" "NO flip" "$stage_out"
assert_not_contains "D4: no guidelines tree-hash MISMATCH on a clean stage" \
    "guidelines/ tree hash MISMATCH" "$stage_out"

REL_DIR="$SBX/releases/$SBX_V1"
MANIFEST="$REL_DIR/manifest.json"
[ -f "$MANIFEST" ] && _pass || _fail "manifest.json exists"

# D1: the staged guidelines/ tree == the source file set, FLAT at the root.
GUIDELINES_STAGE="$REL_DIR/guidelines"
[ -d "$GUIDELINES_STAGE" ] && _pass || _fail "staged payload contains guidelines/ (flat at release root)"
[ ! -e "$REL_DIR/docs" ] && _pass || _fail "payload has NO docs/ nesting (flat guidelines/, not docs/guidelines/)"
src_list="$(cd "$FAKE_REPO/docs/guidelines" && find . -type f | sed 's|^\./||' | sort)"
stg_list="$(cd "$GUIDELINES_STAGE" && find . -type f | sed 's|^\./||' | sort)"
assert_eq "D1: staged guidelines/ file set == source docs/guidelines" "$src_list" "$stg_list"
src_count="$(printf '%s\n' "$src_list" | grep -c . || true)"
stg_count="$(printf '%s\n' "$stg_list" | grep -c . || true)"
assert_eq "D1: staged guidelines count == source count ($src_count)" "$src_count" "$stg_count"
for f in README.md TEMPLATE.md guideline-live-views-passcode.md \
         guideline-memoir-reflection-lane.md guideline-notify-user-bounds.md; do
    [ -f "$GUIDELINES_STAGE/$f" ] && _pass || _fail "D1: staged guidelines/ carries $f"
done
# no staging residue inside the swapped payload
[ ! -e "$REL_DIR/.map_frag.guidelines" ] && _pass || _fail "no .map_frag.guidelines leftover in the payload"

# D2: manifest JSON valid; both keys present alongside the other four trees.
if python3 -m json.tool "$MANIFEST" >/dev/null 2>&1; then _pass; else _fail "manifest.json is valid JSON (python3 -m json.tool)"; fi
m_keys="$(python3 -c '
import json, sys
m = json.load(open(sys.argv[1]))
print("\n".join(sorted(m.keys())))
' "$MANIFEST")"
for k in agents_tree_sha256 frontend_tree_sha256 plugins_tree_sha256 \
         scripts_tree_sha256 guidelines_tree_sha256 agents_manifest \
         frontend_manifest plugins_manifest scripts_manifest guidelines_manifest; do
    printf '%s\n' "$m_keys" | grep -qx "$k" && _pass || _fail "D2: manifest declares $k"
done

# D2: INDEPENDENT tree-sha recompute — EXACT lib.sh _tree_manifest ordering:
# find . -type f | sed 's|^\./||' | sort → null-delimited → xargs -0 -n 500
# shasum -a 256 (output order == argv order == sorted-list order; chunked
# output is byte-identical to the whole-list form) → "<sha>␠␠rel" lines →
# aggregate sha256 over those lines, one trailing newline per line
# (_tree_hash_of_lines). Do NOT "simplify" this to a per-directory walk —
# a different ordering FALSE-MISMATCHES by construction.
recomp_lines="$(cd "$GUIDELINES_STAGE" && find . -type f | sed 's|^\./||' | sort \
    | while IFS= read -r rel; do printf '%s\0' "$rel"; done \
    | xargs -0 -n 500 shasum -a 256 2>/dev/null)"
recomp_tree="$(printf '%s\n' "$recomp_lines" | shasum -a 256 | awk '{print $1}')"
m_guidelines_tree="$(python3 -c '
import json, sys
print(json.load(open(sys.argv[1]))["guidelines_tree_sha256"])
' "$MANIFEST")"
assert_eq "D2: guidelines_tree_sha256 == independent recompute (exact lib.sh ordering)" \
    "$recomp_tree" "$m_guidelines_tree"

# D3: guidelines_manifest composes with lib.sh's verifier; keys
# tree-relative; every value == recomputed per-file sha.
map_lines="$(REPO_ROOT="$FAKE_REPO" bash -c '
    . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh"
    _manifest_map_lines "$(cat '"$MANIFEST"')" guidelines
' 2>/dev/null)"
[ -n "$map_lines" ] && _pass || _fail "D3: _manifest_map_lines parses the guidelines map (lib.sh verifier composes)"
bad_keys="$(printf '%s\n' "$map_lines" | awk '/^\// || $1 ~ /\// {print $1}')"
assert_eq "D3: every map key tree-relative (flat tree ⇒ no path separator)" "" "$bad_keys"
mismatches=""
while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    m_rel="${entry%% *}"; m_sha="${entry#* }"
    actual_sha="$(shasum -a 256 "$GUIDELINES_STAGE/$m_rel" 2>/dev/null | awk '{print $1}')"
    [ "$m_sha" = "$actual_sha" ] || mismatches="$mismatches $m_rel"
done <<EOF
$map_lines
EOF
assert_eq "D3: every map value == recomputed per-file sha256" "" "${mismatches# }"
# the map covers EXACTLY the staged file set (no phantom/missing rows)
map_files="$(printf '%s\n' "$map_lines" | awk '{print $1}' | sort)"
assert_eq "D3: map key set == staged file set" "$stg_list" "$map_files"

# ─── 2. (D5) NEGATIVE: corrupt one staged file POST-compute → refuse ───────
section "D5 negative: corrupt staged guidelines file post-compute"
CORRUPT_FLAG="$FIXTURE/corrupt.done"
rm -f "$CORRUPT_FLAG"
(
    # Corruption poller — fires when the staging manifest.json APPEARS.
    # Deterministic by construction: stage.sh writes the manifest heredoc
    # BEFORE the post-stage re-hash legs, so "manifest exists" pins the
    # moment to strictly after the compute walks and strictly before the
    # guidelines re-hash leg. The corruption lands inside the throwaway
    # .staging. dir only; the poller dies with the suite (explicit kill +
    # registry teardown).
    while [ ! -f "$CORRUPT_FLAG" ]; do
        for m in "$SBX2"/releases/.staging.*/manifest.json; do
            [ -f "$m" ] || continue
            _stg="$(cd "$(dirname "$m")" && pwd)"
            if [ -f "$_stg/guidelines/README.md" ]; then
                printf 'CORRUPTED-POST-COMPUTE\n' >> "$_stg/guidelines/README.md"
                : > "$CORRUPT_FLAG"
                break
            fi
        done
        sleep 0.05
    done
) &
POLLER=$!
_stage_fresh
corrupt_out="$(_run_stage "$SBX2")"
corrupt_rc=$?
kill "$POLLER" 2>/dev/null || true
wait "$POLLER" 2>/dev/null || true
[ -f "$CORRUPT_FLAG" ] && _pass || _fail "D5: corruption poller fired (corruption landed pre-re-hash)"
assert_eq "D5: corrupt-post-compute stage refuses (exit 1)" "1" "$corrupt_rc"
assert_contains "D5: refusal names guidelines" "guidelines" "$corrupt_out"
assert_contains "D5: refusal is a guidelines tree hash MISMATCH" \
    "guidelines/ tree hash MISMATCH" "$corrupt_out"
[ ! -d "$SBX2/releases/$SBX_V1" ] && _pass || _fail "D5: NO release dir swapped in on refusal"
_leftovers5=()
for _d in "$SBX2"/releases/.staging.*; do [ -e "$_d" ] && _leftovers5+=("$_d"); done
[ ${#_leftovers5[@]} -eq 0 ] && _pass || _fail "D5: no .staging leftover after refusal (rm -rf ran)" \
    "none" "${_leftovers5[*]}"

# ─── 3. (D6) NEGATIVE: missing docs/guidelines SOURCE → exit 78 ────────────
section "D6 negative: missing docs/guidelines source dir"
rm -rf "$FAKE_REPO/docs/guidelines"
git -C "$FAKE_REPO" add -A 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm "d6: remove guidelines source"
# re-tag so stage.sh's exact-tag guard passes at the NEW HEAD (the removal
# commit), else the refusal would come from the tag gate, not the guard.
git -C "$FAKE_REPO" tag -f "$SBX_V1" >/dev/null 2>&1
_stage_fresh
d6_out="$(_run_stage "$SBX2")"
d6_rc=$?
assert_eq "D6: missing docs/guidelines refused (exit 78)" "78" "$d6_rc"
assert_contains "D6: refusal names docs/guidelines" "docs/guidelines" "$d6_out"
[ ! -d "$SBX2/releases/$SBX_V1" ] && _pass || _fail "D6: NO release dir created on refusal"
_leftovers6=()
for _d in "$SBX2"/releases/.staging.*; do [ -e "$_d" ] && _leftovers6+=("$_d"); done
[ ${#_leftovers6[@]} -eq 0 ] && _pass || _fail "D6: no .staging leftover after refusal" \
    "none" "${_leftovers6[*]}"

# ─── 4. (D7) fixture discipline: containment proof ─────────────────────────
section "D7 fixture discipline: everything inside the mktemp root"
for p in "$FAKE_REPO" "$FAKE_HOME" "$SBX" "$SBX2" "$FIXTURE/stub-prod"; do
    _rp="$(cd "$(dirname "$p")" && pwd -P)/$(basename "$p")"
    case "$_rp" in
        "$FIXTURE"/*) _pass ;;
        *) _fail "D7: fixture path escapes the mktemp root: $p (resolved $_rp)" ;;
    esac
done

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n== summary: %s passed, %s failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed tests:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
