#!/usr/bin/env bash
# ============================================================================
# tests/test_tool_usage_artifacts_payload.sh — the tool-usage-artifacts
# stage-level payload pin (hermetic)
# ============================================================================
# WHAT THIS PINS (architecture-recommendation-digest-fix.md §3.9.6):
#   1. the staged payload contains tool-usage-artifacts/{allowlists.json,
#      tool-weights.json} and NOTHING ELSE;
#   2. manifest.json carries `tool_usage_artifacts_tree_sha256` and
#      `tool_usage_artifacts_manifest`, and the per-file map matches a FRESH
#      hash of the staged bytes;
#   3. the stage-local re-hash leg REJECTS a corrupted copy (tamper the
#      staged file → the leg exits non-zero);
#   4. the staged tree passes `_no_env_in_release`;
#   5. D4 pinning assertion (ii): dropping a file into the digest output dir
#      changes NO tree hash. The digest output dir is DERIVED from the DEFAULT
#      frozen resolution against a tmp install (the staged code is invoked with
#      sys.frozen=True and sys.executable pointed at
#      <tmp>/install/releases/<v>/ensemble-prod, with
#      ENSEMBLE_TOOL_USAGE_DIGEST_ROOT UNSET — the env override wins BEFORE any
#      resolution, so it must NOT be used to inject the dir), and the derived
#      dir must equal <tmp>/install/.agents/shared/planning/tool-usage-metrics-p1
#      /digests (the install root that OWNS releases/, one level ABOVE the
#      release dir) BEFORE the file is dropped there.
#
# THE SHIPPED LOGIC IS EXERCISED, NEVER RE-IMPLEMENTED:
#   * stage.sh's tool-usage-artifacts seam lives in a marker-delimited block
#     (TOOL-USAGE-ARTIFACTS-BLOCK-BEGIN/END); this suite EXTRACTS those bytes
#     and calls the same functions stage.sh calls — one implementation, no
#     fork ("one walker, one source of truth", §8.5);
#   * the tree walker / aggregate / map-join / .env guard are lib.sh's OWN
#     helpers (sourced, not copied);
#   * the manifest is rendered by the REAL manifest heredoc, extracted from
#     stage.sh and executed with the computed values.
#
# HERMETIC BY CONSTRUCTION:
#   * every fixture lives under a fresh `mktemp -d` (an escape guard refuses a
#     fixture root outside /tmp or touching /home/nea);
#   * the LIVE install dir is NEVER read or written (no ~/agents-ensemble, no
#     releases/ dir, no promote, no daemon lifecycle, no DB contact);
#   * stage.sh is NEVER executed end-to-end (it assembles a release layout) —
#     the shipped seam is called in isolation instead;
#   * the payload is WRITTEN only under the fixture's stage dir; the
#     committed dev-mode artifact is READ ONLY (the generator's --verify
#     never writes).
#
# Exit codes: 0 = all pass, 1 = failures.
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
STAGE_SH="$UPGRADE_DIR/stage.sh"

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

# house helpers: assert_contains / assert_not_contains + the fixture registry
# (ONE EXIT trap tears the mktemp root down).
# shellcheck source=tests/upgrade_test_lib.sh
. "$REPO_ROOT/tests/upgrade_test_lib.sh"

# ─── 0. syntax gates + the interpreter ──────────────────────────────────────
section "syntax gates"
if bash -n "$STAGE_SH" 2>/dev/null; then _pass; else _fail "stage.sh passes bash -n"; fi
if bash -n "$UPGRADE_DIR/lib.sh" 2>/dev/null; then _pass; else _fail "lib.sh passes bash -n"; fi
if bash -n "${BASH_SOURCE[0]}" 2>/dev/null; then _pass; else _fail "this suite passes bash -n"; fi

PY="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
    printf 'FATAL: the worktree venv interpreter is missing (%s) — this suite needs it (the D4 leg imports the real reader)\n' "$PY" >&2
    exit 1
fi
_pass

# ─── 1. the fixture root + escape guard ─────────────────────────────────────
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/tool-usage-artifacts-payload.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
_register_fixture "$FIXTURE"

case "$FIXTURE" in
    /tmp/*) _pass ;;
    *) printf 'FATAL: fixture root escapes /tmp (%s) — refusing to run\n' "$FIXTURE" >&2; exit 1 ;;
esac
case "$(cd "$FIXTURE" && pwd -P)" in
    *"/home/nea"*) printf 'FATAL: fixture root touches /home/nea (%s) — refusing to run\n' "$FIXTURE" >&2; exit 1 ;;
    *) _pass ;;
esac

STAGE="$FIXTURE/stage"
mkdir -p "$STAGE"

# ─── 2. extract the SHIPPED seam from stage.sh (no fork) ───────────────────
section "the shipped seam (extracted from stage.sh — not re-implemented)"
BLOCK="$(awk '
    /^# >>> TOOL-USAGE-ARTIFACTS-BLOCK-BEGIN$/ { f=1; next }
    /^# <<< TOOL-USAGE-ARTIFACTS-BLOCK-END$/   { f=0 }
    f { print }
' "$STAGE_SH")"
JOIN_MAP_FUNC="$(awk '
    /^_stage_join_map\(\) \{$/ { f=1 }
    f { print }
    f && /^\}$/ { exit }
' "$STAGE_SH")"
MANIFEST_BLOCK="$(sed -n '/^cat > "\$STAGE_TMP\/manifest.json" <<EOF$/,/^EOF$/p' "$STAGE_SH")"

if [ -n "$BLOCK" ]; then _pass; else _fail "stage.sh carries the tool-usage-artifacts block (markers found)"; fi
for fn in _stage_tool_usage_artifacts_generate _stage_tool_usage_artifacts_drift_report \
          _stage_tool_usage_artifacts_rehash _stage_tool_usage_artifacts_map; do
    if printf '%s\n' "$BLOCK" | grep -q "^${fn}()"; then _pass; else _fail "the shipped block defines $fn" "defined" "absent"; fi
done
if [ -n "$JOIN_MAP_FUNC" ]; then _pass; else _fail "stage.sh carries _stage_join_map (the map join)"; fi
if [ -n "$MANIFEST_BLOCK" ]; then _pass; else _fail "stage.sh carries the manifest heredoc (the manifest renderer)"; fi

# A4: the generator runs under the RESOLVED interpreter — never a hardcoded
# python3 (a different interpreter could fail on a host whose python3 lacks
# the generator's dependencies).
if printf '%s\n' "$BLOCK" | grep -q 'if ! "\$py" "\$generator" --agents-dir "\$repo_root/agents" --out "\$payload_dir/allowlists.json"; then'; then
    _pass
else
    _fail "the shipped block invokes the generator through the resolved interpreter"
fi
if printf '%s\n' "$BLOCK" | grep -v '^[[:space:]]*#' | grep -q 'python3'; then
    _fail "the shipped block has NO hardcoded python3 (A4)" "no python3 in code" "python3 found"
else
    _pass
fi

# ─── 3. the harness (lib.sh + the shipped bytes + the mode dispatch) ───────
HARNESS="$FIXTURE/harness.sh"
{
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' 'set -u'
    printf '%s\n' '. "$TUA_LIB/lib.sh"'
    printf '%s\n' "$BLOCK"
    printf '%s\n' "$JOIN_MAP_FUNC"
    cat <<'HARNESS_BODY'
mode="$1"; shift
case "$mode" in
    build)    _stage_tool_usage_artifacts_generate "$1" "$2" "$3" ;;
    drift)    _stage_tool_usage_artifacts_drift_report "$1" "$2" ;;
    rehash)   _stage_tool_usage_artifacts_rehash "$1" "$2" ;;
    map)      _stage_tool_usage_artifacts_map "$1" "$2" ;;
    lines)    _tree_manifest "$1" "" ;;
    treehash) _tree_hash_of_lines "$1" ;;
    noenv)    _no_env_in_release "$1" ;;
    *)        echo "harness: unknown mode '$mode'" >&2; exit 2 ;;
esac
rc=$?
echo "HARNESS_RC=$rc" >&2
exit "$rc"
HARNESS_BODY
} > "$HARNESS"
if bash -n "$HARNESS" 2>/dev/null; then _pass; else _fail "the harness (shipped bytes + dispatch) passes bash -n"; fi

# ─── 4. the payload is BUILT by the shipped generate leg ───────────────────
section "the staged payload (built by the shipped generate leg)"
BUILD_OUT="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" build "$REPO_ROOT" "$STAGE" "$PY" 2>&1)"
BUILD_RC=$?
assert_eq "the shipped generate leg succeeds (rc 0)" "0" "$BUILD_RC"
printf '%s\n' "$BUILD_OUT" | sed 's/^/    | /'

PAYLOAD="$STAGE/tool-usage-artifacts"
[ -d "$PAYLOAD" ] && _pass || _fail "the staged payload dir exists (tool-usage-artifacts/)"
[ -f "$PAYLOAD/allowlists.json" ] && _pass || _fail "the staged payload carries allowlists.json"
[ -f "$PAYLOAD/tool-weights.json" ] && _pass || _fail "the staged payload carries tool-weights.json"

# assertion 1 — the payload carries EXACTLY the two JSON files, nothing else
PAYLOAD_FILES="$(cd "$PAYLOAD" && find . -type f | sed 's|^\./||' | sort)"
EXPECTED_FILES="$(printf 'allowlists.json\ntool-weights.json')"
assert_eq "assertion 1: the staged payload contains EXACTLY allowlists.json + tool-weights.json" \
    "$EXPECTED_FILES" "$PAYLOAD_FILES"

# the weight artifact is copied VERBATIM (byte-identical to the committed one)
if cmp -s "$PAYLOAD/tool-weights.json" "$REPO_ROOT/.agents/shared/planning/tool-usage-metrics-p1/tool-weights.json"; then
    _pass
else
    _fail "the staged tool-weights.json is byte-identical to the committed artifact"
fi

# the allow-lists are DERIVED (schema-2, and NOT a copy of the committed file's
# stamp): the payload must parse and declare schema_version 2
if "$PY" -c '
import json, sys
d = json.load(open(sys.argv[1]))
assert d["schema_version"] == 2, d.get("schema_version")
assert isinstance(d.get("agents"), dict) and d["agents"], "no agents"
print("schema2-ok")
' "$PAYLOAD/allowlists.json" >/dev/null 2>&1; then
    _pass
else
    _fail "the staged allowlists.json is a valid schema-2 artifact"
fi

# the derivation is FROM THE TREE BEING STAGED (F1 — never copied): the
# staged artifact's agents map must match the repo's agents/*/meta.json
if "$PY" -c '
import json, sys
staged = json.load(open(sys.argv[1]))
src = {}
for name in sorted(__import__("pathlib").Path(sys.argv[2]).glob("*/meta.json")):
    meta = json.loads(name.read_text())
    tools = meta.get("tools") or {}
    allow = tools.get("allow") if isinstance(tools, dict) else tools
    deny = tools.get("deny") if isinstance(tools, dict) else []
    src[name.parent.name] = {
        "allow": sorted(set(str(x) for x in (allow or []))),
        "deny": sorted(set(str(x) for x in (deny or []))),
        "innate": sorted(set(str(x) for x in (meta.get("innate_skills") or []))),
    }
assert staged["agents"] == src, "the staged allow-lists are not the tree being staged"
print("derived-ok")
' "$PAYLOAD/allowlists.json" "$REPO_ROOT/agents" >/dev/null 2>&1; then
    _pass
else
    _fail "F1: the staged allow-lists are DERIVED from the tree being staged"
fi

# ─── 4b. the lane-presence branches (the payload is REQUIRED iff the tree
#         carries the tool-usage lane) ───────────────────────────────────────
section "the lane-presence branches (required iff the tree has the lane)"
# (a) a tree with NO generator and NO committed artifact has NO tool-usage
#     lane → nothing to derive, nothing to ship (WARN, never silent)
NO_LANE="$FIXTURE/no-lane-repo"
NO_LANE_STAGE="$FIXTURE/no-lane-stage"
mkdir -p "$NO_LANE" "$NO_LANE_STAGE"
NO_LANE_OUT="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" build "$NO_LANE" "$NO_LANE_STAGE" "$PY" 2>&1)"
NO_LANE_RC=$?
assert_eq "a tree with NO tool-usage lane stages no payload (rc 0)" "0" "$NO_LANE_RC"
if [ -d "$NO_LANE_STAGE/tool-usage-artifacts" ]; then _pass; else _fail "the lane-absent path still creates the (empty) payload dir"; fi
NO_LANE_FILES="$(cd "$NO_LANE_STAGE/tool-usage-artifacts" 2>/dev/null && find . -type f | wc -l | tr -d ' ')"
assert_eq "the lane-absent payload dir is EMPTY" "0" "$NO_LANE_FILES"
assert_contains "the lane-absent path says so out loud" "carries NO tool-usage lane" "$NO_LANE_OUT"

# (b) a tree that SHIPS the committed artifact but has NO generator is
#     BROKEN → the stage REFUSES (fail-closed; F1 — a committed artifact can
#     never reach a release)
BROKEN_LANE="$FIXTURE/broken-lane-repo"
BROKEN_LANE_STAGE="$FIXTURE/broken-lane-stage"
mkdir -p "$BROKEN_LANE/.agents/shared/planning/tool-usage-metrics-p1" "$BROKEN_LANE_STAGE"
printf '{}\n' > "$BROKEN_LANE/.agents/shared/planning/tool-usage-metrics-p1/allowlists.json"
BROKEN_OUT="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" build "$BROKEN_LANE" "$BROKEN_LANE_STAGE" "$PY" 2>&1)"
BROKEN_RC=$?
assert_eq "a tree with the committed artifact but NO generator REFUSES (rc 78)" "78" "$BROKEN_RC"
assert_contains "the broken-tree refusal says it refuses" "refusing to stage" "$BROKEN_OUT"

# (c) a tree with the generator but WITHOUT the weight artifact is BROKEN →
#     the stage REFUSES (the payload can never be half-built)
GEN_ONLY="$FIXTURE/gen-only-repo"
GEN_ONLY_STAGE="$FIXTURE/gen-only-stage"
mkdir -p "$GEN_ONLY/scripts" "$GEN_ONLY_STAGE"
cp "$REPO_ROOT/scripts/gen_tool_usage_allowlists.py" "$GEN_ONLY/scripts/gen_tool_usage_allowlists.py"
GEN_ONLY_OUT="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" build "$GEN_ONLY" "$GEN_ONLY_STAGE" "$PY" 2>&1)"
GEN_ONLY_RC=$?
assert_eq "a tree with the generator but NO weight artifact REFUSES (rc 78)" "78" "$GEN_ONLY_RC"
assert_contains "the missing-weight refusal names the weight artifact" "weight artifact missing" "$GEN_ONLY_OUT"

# ─── 5. the manifest (rendered by the REAL manifest heredoc) ───────────────
section "the manifest (rendered by the real manifest heredoc)"
LINES="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" lines "$PAYLOAD" 2>/dev/null)"
TREE="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" treehash "$LINES" 2>/dev/null)"
MAP="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" map "$STAGE" "$LINES" 2>/dev/null)"
assert_eq "the tree aggregate is a 64-hex sha256" "64" "${#TREE}"
if [ -n "$MAP" ]; then _pass; else _fail "the shipped map leg produced a per-file map"; fi

MANIFEST_SCRIPT="$FIXTURE/manifest_render.sh"
printf '%s\n' "$MANIFEST_BLOCK" > "$MANIFEST_SCRIPT"
if bash -n "$MANIFEST_SCRIPT" 2>/dev/null; then _pass; else _fail "the extracted manifest heredoc parses"; fi

env \
    STAGE_TMP="$STAGE" \
    VERSION="v0.0.0-payload-pin" \
    BINARY_VERSION="0.0.0-payload-pin" \
    STAGED_AT="2026-01-01T00:00:00Z" \
    SOURCE_COMMIT="0000000000000000000000000000000000000000" \
    SCRIPTS_PIN_COMMIT="0000000000000000000000000000000000000000" \
    FLOOR_COMMIT="0000000000000000000000000000000000000000" \
    STAMP_REV="0000000000000000000000000000000000000000" \
    KNOWN_SCHEMA_GEN="20260101_000001_init.sql" \
    CONTAINS_CONTRACT_PHASE="true" \
    ROLLBACK_SAFE="false" \
    LAUNCHER_SHA="1111111111111111111111111111111111111111111111111111111111111111" \
    BIN_SHA="2222222222222222222222222222222222222222222222222222222222222222" \
    CONFIG_SHA="3333333333333333333333333333333333333333333333333333333333333333" \
    AGENTS_TREE="aaaa" FRONTEND_TREE="bbbb" PLUGINS_TREE="cccc" \
    SCRIPTS_TREE="dddd" GUIDELINES_TREE="eeee" DAEMON_SLICE_TREE="ffff" \
    TOOL_USAGE_ARTIFACTS_TREE="$TREE" \
    agents_map='"a.md":"1111"' \
    frontend_map='"index.html":"2222"' \
    plugins_map='"p/MANIFEST.yaml":"3333"' \
    guidelines_map='"README.md":"4444"' \
    scripts_map='"upgrade/promote.sh":"5555"' \
    daemon_slice_map='"daemon/plugin_subsystem/promote_staleness.py":"6666"' \
    tool_usage_artifacts_map="$MAP" \
    bash "$MANIFEST_SCRIPT" >/dev/null 2>&1
MANIFEST="$STAGE/manifest.json"
[ -f "$MANIFEST" ] && _pass || _fail "the manifest heredoc wrote manifest.json"

if "$PY" -m json.tool "$MANIFEST" >/dev/null 2>&1; then _pass; else _fail "the rendered manifest.json is valid JSON"; fi

M_KEYS="$("$PY" -c '
import json, sys
m = json.load(open(sys.argv[1]))
print("\n".join(sorted(m.keys())))
' "$MANIFEST" 2>/dev/null)"
for k in tool_usage_artifacts_tree_sha256 tool_usage_artifacts_manifest \
         agents_tree_sha256 frontend_tree_sha256 plugins_tree_sha256 \
         scripts_tree_sha256 guidelines_tree_sha256 daemon_slice_tree_sha256 \
         agents_manifest frontend_manifest plugins_manifest guidelines_manifest \
         scripts_manifest daemon_slice_manifest; do
    printf '%s\n' "$M_KEYS" | grep -qx "$k" && _pass || _fail "assertion 2: the manifest declares $k"
done

M_TREE="$("$PY" -c '
import json, sys
print(json.load(open(sys.argv[1]))["tool_usage_artifacts_tree_sha256"])
' "$MANIFEST" 2>/dev/null)"
assert_eq "assertion 2: tool_usage_artifacts_tree_sha256 == the fresh tree aggregate" "$TREE" "$M_TREE"

# the per-file map composes with lib.sh's OWN verifier and every value is the
# FRESH sha256 of the staged bytes
MAP_LINES="$(TUA_LIB="$UPGRADE_DIR" bash -c '
    . "$1/lib.sh"
    _manifest_map_lines "$(cat "$2")" tool_usage_artifacts
' _ "$UPGRADE_DIR" "$MANIFEST" 2>/dev/null)"
if [ -n "$MAP_LINES" ]; then _pass; else _fail "assertion 2: _manifest_map_lines parses the tool_usage_artifacts map"; fi
assert_eq "assertion 2: the map covers EXACTLY the two staged files" \
    "allowlists.json
tool-weights.json" "$(printf '%s\n' "$MAP_LINES" | awk '{print $1}' | sort)"
BAD_MAP=""
while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    m_rel="${entry%% *}"; m_sha="${entry#* }"
    actual_sha="$(shasum -a 256 "$PAYLOAD/$m_rel" 2>/dev/null | awk '{print $1}')"
    [ "$m_sha" = "$actual_sha" ] || BAD_MAP="$BAD_MAP $m_rel"
done <<EOF
$MAP_LINES
EOF
assert_eq "assertion 2: every map value == the fresh sha256 of the staged bytes" "" "${BAD_MAP# }"

# ─── 6. the stage-local re-hash leg (the corruption rejection) ─────────────
section "the stage-local re-hash leg (rejects a corrupted copy)"
REHASH_BEFORE="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" rehash "$STAGE" "$TREE" 2>&1)"
REHASH_BEFORE_RC=$?
assert_eq "assertion 3 (pre-tamper): the re-hash leg PASSES on the untampered tree" "0" "$REHASH_BEFORE_RC"
printf '    | re-hash leg BEFORE the tamper: rc=%s\n' "$REHASH_BEFORE_RC"

printf 'CORRUPTED-POST-COMPUTE\n' >> "$PAYLOAD/allowlists.json"
REHASH_AFTER="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" rehash "$STAGE" "$TREE" 2>&1)"
REHASH_AFTER_RC=$?
if [ "$REHASH_AFTER_RC" -ne 0 ]; then _pass; else _fail "assertion 3: the re-hash leg REJECTS the corrupted copy (rc != 0)" "non-zero" "0"; fi
printf '    | re-hash leg AFTER  the tamper: rc=%s\n' "$REHASH_AFTER_RC"
printf '%s\n' "$REHASH_AFTER" | sed 's/^/    | /'
assert_contains "assertion 3: the refusal names the tree-hash MISMATCH" \
    "tool-usage-artifacts/ tree hash MISMATCH" "$REHASH_AFTER"

# ─── 7. the .env guard over the staged tree ───────────────────────────────
section "the .env guard over the staged tree"
NOENV="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" noenv "$STAGE" 2>&1)"
NOENV_RC=$?
assert_eq "assertion 4: the staged tree passes _no_env_in_release" "0" "$NOENV_RC"

# negative leg — the guard is not vacuous
ENVFIX="$FIXTURE/envfix"
mkdir -p "$ENVFIX/tool-usage-artifacts"
cp "$PAYLOAD/tool-weights.json" "$ENVFIX/tool-usage-artifacts/tool-weights.json"
printf 'SECRET=1\n' > "$ENVFIX/tool-usage-artifacts/.env"
NOENV_NEG="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" noenv "$ENVFIX" 2>&1)"
NOENV_NEG_RC=$?
if [ "$NOENV_NEG_RC" -ne 0 ]; then _pass; else _fail "the .env guard REJECTS a tree carrying a .env (rc != 0)" "non-zero" "0"; fi

# ─── 8. D4 (ii): a file dropped into the digest dir changes NO tree hash ───
section "D4 (ii): the digest output dir is outside every hashed tree"
INSTALL="$FIXTURE/install"
REL="$INSTALL/releases/v0.0.0-payload-pin"
mkdir -p "$REL"
# the frozen release layout: the payload sits in the release dir, the binary
# is <install>/releases/<v>/ensemble-prod
cp -R "$STAGE/tool-usage-artifacts" "$REL/tool-usage-artifacts"
printf '#!/bin/bash\nexit 78\n' > "$REL/ensemble-prod"
chmod +x "$REL/ensemble-prod"
# the sibling trees the manifest also covers (representative stubs — each with
# DISTINCT content so their aggregates differ)
for t in agents frontend plugins guidelines scripts; do
    mkdir -p "$REL/$t"
    printf 'stub-%s\n' "$t" > "$REL/$t/stub.md"
done

DERIVE="$FIXTURE/derive_digest_root.py"
cat > "$DERIVE" <<'PYEOF'
"""Derive the digest output dir from the DEFAULT frozen resolution.

The env override wins BEFORE any resolution, so it must NOT be used to
inject the dir — it is popped here and the DEFAULT resolution is used.
"""
import importlib
import os
import sys

exe = sys.argv[1]
os.environ.pop("ENSEMBLE_TOOL_USAGE_DIGEST_ROOT", None)
os.environ.pop("ENSEMBLE_TOOL_USAGE_ARTIFACT_DIR", None)

mod = importlib.import_module("daemon.services.tool_usage_digest")
print("MODULE_FILE=" + str(mod.__file__))
sys.frozen = True
sys.executable = exe
print("DIGEST_ROOT=" + str(mod.digest_root()))
print("ARTIFACT_DIR=" + str(mod._artifact_dir()))
PYEOF

DERIVE_OUT="$(PYTHONPATH="$REPO_ROOT" "$PY" "$DERIVE" "$REL/ensemble-prod" 2>"$FIXTURE/derive.err")"
DERIVE_RC=$?
assert_eq "the digest dir derives from the default frozen resolution (rc 0)" "0" "$DERIVE_RC"
M_MODULE="$(printf '%s\n' "$DERIVE_OUT" | grep '^MODULE_FILE=' | head -1 | cut -d= -f2-)"
M_DIGEST="$(printf '%s\n' "$DERIVE_OUT" | grep '^DIGEST_ROOT=' | head -1 | cut -d= -f2-)"
M_ARTIFACT="$(printf '%s\n' "$DERIVE_OUT" | grep '^ARTIFACT_DIR=' | head -1 | cut -d= -f2-)"
case "$M_MODULE" in
    "$REPO_ROOT"/*) _pass ;;
    *) _fail "the reader was imported from THIS worktree (not the main checkout)" "$REPO_ROOT/*" "$M_MODULE" ;;
esac
assert_eq "assertion 5: the derived digest dir == <install>/.agents/shared/planning/tool-usage-metrics-p1/digests" \
    "$INSTALL/.agents/shared/planning/tool-usage-metrics-p1/digests" "$M_DIGEST"
printf '    | DERIVED digest dir: %s\n' "$M_DIGEST"
printf '    | DERIVED artifact dir: %s\n' "$M_ARTIFACT"
assert_eq "the reader's artifact dir points at the staged payload" \
    "$REL/tool-usage-artifacts" "$M_ARTIFACT"

# the digest dir is NOT a descendant of any hashed tree root (D4 (i) at stage
# level)
DIGEST_IN_TREE=""
for t in tool-usage-artifacts agents frontend plugins guidelines scripts; do
    case "$M_DIGEST/" in
        "$REL/$t/"*) DIGEST_IN_TREE="$DIGEST_IN_TREE $t" ;;
    esac
done
assert_eq "the digest dir is NOT inside any hashed tree root" "" "${DIGEST_IN_TREE# }"

# the historical defect class — the shipped READ-ONLY artifacts and the
# WRITABLE digest output sharing ONE parent directory — is eliminated BY
# CONSTRUCTION: the payload rides the release dir, the digests ride the
# install root's .agents/, and the two parents differ.
PAYLOAD_PARENT="$(dirname "$REL/tool-usage-artifacts")"
DIGEST_PARENT="$(dirname "$M_DIGEST")"
if [ "$PAYLOAD_PARENT" != "$DIGEST_PARENT" ]; then _pass; else
    _fail "the shipped payload and the writable digest output do NOT share one parent dir" \
        "different parents" "both under $PAYLOAD_PARENT"
fi
case "$M_DIGEST/" in
    "$INSTALL/.agents/"*) _pass ;;
    *) _fail "the digest output dir lives under the install root's .agents/" "$INSTALL/.agents/*" "$M_DIGEST" ;;
esac

# the tree hashes BEFORE the drop
HASHES_BEFORE=""
for t in tool-usage-artifacts agents frontend plugins guidelines scripts; do
    h="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" treehash "$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" lines "$REL/$t" 2>/dev/null)" 2>/dev/null)"
    HASHES_BEFORE="$HASHES_BEFORE$t=$h
"
done
if [ -n "$HASHES_BEFORE" ]; then _pass; else _fail "the staged tree hashes were computed"; fi

# DROP a file into the derived digest output dir
mkdir -p "$M_DIGEST"
printf 'digest output\n' > "$M_DIGEST/2026-01-01.md"

# the tree hashes AFTER the drop — IDENTICAL
HASHES_AFTER=""
for t in tool-usage-artifacts agents frontend plugins guidelines scripts; do
    h="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" treehash "$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" lines "$REL/$t" 2>/dev/null)" 2>/dev/null)"
    HASHES_AFTER="$HASHES_AFTER$t=$h
"
done
assert_eq "assertion 5: dropping a file into the digest dir changes NO tree hash" \
    "$HASHES_BEFORE" "$HASHES_AFTER"
printf '    | tree hashes BEFORE the drop:\n%s' "$HASHES_BEFORE"
printf '    | tree hashes AFTER  the drop:\n%s' "$HASHES_AFTER"

# ─── 9. the F3 drift report is a REPORT, never a refusal ───────────────────
section "the F3 drift report (warn, never refuse)"
DRIFT_OUT="$(TUA_LIB="$UPGRADE_DIR" bash "$HARNESS" drift "$REPO_ROOT" "$PY" 2>&1)"
DRIFT_RC=$?
assert_eq "the drift report never refuses (rc 0)" "0" "$DRIFT_RC"
printf '%s\n' "$DRIFT_OUT" | sed 's/^/    | /'

# ─── 10. the stage.sh wiring (the static pins) ─────────────────────────────
section "the stage.sh wiring"
STAGE_SRC="$(cat "$STAGE_SH")"
assert_contains "stage.sh records the tree aggregate" '"tool_usage_artifacts_tree_sha256"' "$STAGE_SRC"
assert_contains "stage.sh records the per-file map" '"tool_usage_artifacts_manifest"' "$STAGE_SRC"
assert_contains "stage.sh keeps the pre-existing guidelines tree sha" '"guidelines_tree_sha256": "$GUIDELINES_TREE",' "$STAGE_SRC"
assert_contains "stage.sh keeps the pre-existing guidelines map" '"guidelines_manifest": {$guidelines_map},' "$STAGE_SRC"
assert_contains "stage.sh walks the tool-usage-artifacts tree" '_stage_tree_walk tool-usage-artifacts' "$STAGE_SRC"
assert_contains "stage.sh re-hashes the tool-usage-artifacts tree post-stage" '_stage_tool_usage_artifacts_rehash' "$STAGE_SRC"
assert_contains "stage.sh refuses the swap-in on a re-hash mismatch" 'post-stage tool-usage-artifacts/ tree hash MISMATCH' "$STAGE_SRC"
assert_contains "stage.sh generates the allow-lists through the resolved interpreter" '_stage_tool_usage_artifacts_generate "$REPO_ROOT" "$STAGE_TMP" "$_stage_cc_py"' "$STAGE_SRC"
assert_contains "stage.sh reports (never refuses) the committed-artifact drift" '_stage_tool_usage_artifacts_drift_report' "$STAGE_SRC"

# the ORDER of the seam's call sites (a structural pin: the payload must be
# built BEFORE the walk, the walk BEFORE the map, the map BEFORE the manifest
# heredoc, and the re-hash leg AFTER it)
L_GENERATE="$(grep -n '^_stage_tool_usage_artifacts_generate "\$REPO_ROOT"' "$STAGE_SH" | head -1 | cut -d: -f1)"
L_WALK="$(grep -n '_stage_tree_walk tool-usage-artifacts' "$STAGE_SH" | head -1 | cut -d: -f1)"
L_MAP="$(grep -n '^tool_usage_artifacts_map=' "$STAGE_SH" | head -1 | cut -d: -f1)"
L_MANIFEST="$(grep -n '"tool_usage_artifacts_tree_sha256"' "$STAGE_SH" | head -1 | cut -d: -f1)"
L_REHASH="$(grep -n '^if ! _stage_tool_usage_artifacts_rehash' "$STAGE_SH" | head -1 | cut -d: -f1)"
if [ -n "$L_GENERATE" ] && [ -n "$L_WALK" ] && [ -n "$L_MAP" ] && [ -n "$L_MANIFEST" ] && [ -n "$L_REHASH" ]; then _pass; else
    _fail "every tool-usage-artifacts call site is present" "5 sites" "generate=$L_GENERATE walk=$L_WALK map=$L_MAP manifest=$L_MANIFEST rehash=$L_REHASH"
fi
if [ "$L_GENERATE" -lt "$L_WALK" ] && [ "$L_WALK" -lt "$L_MAP" ] && [ "$L_MAP" -lt "$L_MANIFEST" ] && [ "$L_MANIFEST" -lt "$L_REHASH" ]; then
    _pass
else
    _fail "the seam's call sites are ORDERED (generate < walk < map < manifest < re-hash)" \
        "ordered" "generate=$L_GENERATE walk=$L_WALK map=$L_MAP manifest=$L_MANIFEST rehash=$L_REHASH"
fi

# ─── 11. fixture discipline ────────────────────────────────────────────────
section "fixture discipline: everything inside the mktemp root"
for p in "$STAGE" "$FIXTURE/install" "$FIXTURE/harness.sh"; do
    _rp="$(cd "$(dirname "$p")" && pwd -P)/$(basename "$p")"
    case "$_rp" in
        "$FIXTURE"/*) _pass ;;
        *) _fail "fixture path escapes the mktemp root: $p (resolved $_rp)" ;;
    esac
done
# the suite NEVER names the live install dir (no read, no write, no delete) and
# never invokes a promote/stage of the live tree. The pin lines themselves are
# excluded from the scan (they carry the needle).
SUITE_SRC="$(cat "${BASH_SOURCE[0]}")"
LIVE_LEAKS="$(printf '%s\n' "$SUITE_SRC" | grep -v 'assert_not_contains' | grep -c '/home/nea/agents-ensemble' || true)"
assert_eq "the suite never names the live install dir (outside the pin line itself)" "0" "$LIVE_LEAKS"
PROMOTE_LEAKS="$(printf '%s\n' "$SUITE_SRC" | grep -v 'assert_not_contains' | grep -c 'promote\.sh live' || true)"
assert_eq "the suite never runs a promote of the live tree (outside the pin line itself)" "0" "$PROMOTE_LEAKS"
# the suite never EXECUTES stage.sh end-to-end (it reads it as source text only)
STAGE_EXEC="$(printf '%s\n' "$SUITE_SRC" | grep -v 'assert_not_contains' | grep -c 'bash "\$STAGE_SH"' || true)"
assert_eq "the suite never executes stage.sh end-to-end" "0" "$STAGE_EXEC"

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n========================================\n'
printf 'tool usage artifacts payload tests: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf 'ALL PASS\n'
exit 0
