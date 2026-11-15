#!/bin/bash
# ============================================================================
# tests/packs/promote_config_compat_test.sh — D-2 (ENSEMBLE-32 repair lane,
# E2E round 1): the promote ENTRY config-compat check.
#
# The defect family (demo promote 2026-11-15): config.yaml does NOT travel
# with releases; a promote whose TARGET release cannot parse the install's
# config.yaml crash-loops the new daemon with a deterministic pydantic
# error AFTER the flip, and the auto-rollback lands on an unbootable
# previous release (halt-for-human). The fix: a promote ENTRY check
# (lib.sh _config_compat_check) validating the install config's KEY SHAPE
# against the TARGET release's config model contract — slice modules
# daemon/plugin_subsystem/config_compat_{check,contract}.py, the contract
# minted at stage time from the target tree — exit-78 refusals in the
# freshness family, FAIL-CLOSED when unrunnable.
#
# Why a NEW pack (brief: "your choice of home — state why"): the promote
# entry-check families that drive promote.sh's preflight
# (promote_plugin_staleness) carry pre-existing darwin reds
# (supervision-preflight sandbox class, 58P/8F); a focused pack keeps the
# new family's green signal clean and its legs host-agnostic (the check
# itself does not depend on the launchd classification).
#
# Legs:
#   C1 the REAL tree mint + the REAL shipped config.yaml → compatible (rc 0)
#   C2 unknown TOP-LEVEL key → rc 3 code=config-incompatible
#   C3 unknown NESTED key inside an accepted section → rc 3
#   C4 PAYLOAD context (staged lib.sh copy) + MISSING contract → refuse 78
#      (fail-closed: legacy payload without a contract = re-stage remedy)
#   C5 PAYLOAD context + MISSING checker module → refuse 78
#   C6 unparseable install config.yaml → refuse 78 (never warn-and-pass)
#   C7 missing install config.yaml → refuse 78 (the daemon cannot boot)
#   C8 CONTRACT SKEW (contract missing a key the config has) → refuse 78
#      config-incompatible (the check bites on shape drift, not just
#      absent contracts)
#   C9 the checker + minter are executor-family members (travel with every
#      payload — structural pin over the authoritative family list)
#
# Plain bash + python3. Self-asserting; nonzero exit on failure.
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIBSH="$REPO_ROOT/scripts/upgrade/lib.sh"
CHECKER="$REPO_ROOT/daemon/plugin_subsystem/config_compat_check.py"
MINTER="$REPO_ROOT/daemon/plugin_subsystem/config_compat_contract.py"
PY="$(command -v python3)"

PASS=0
FAIL=0
FAILED_TESTS=""
_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name (expected [$expected] got [$actual])"; fi
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name (wanted '$2')" ;;
    esac
}
assert_rc() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name (expected rc=$expected, got rc=$actual)"; fi
}

# ── 0. gates ────────────────────────────────────────────────────────────────
bash -n "$LIBSH" && _pass || _fail "lib.sh passes bash -n"
[ -f "$CHECKER" ] && _pass || _fail "checker module present"
[ -f "$MINTER" ] && _pass || _fail "contract minter present"

FIX="$(mktemp -d -t cc-pack.XXXXXX)"
cleanup() { rm -rf "$FIX"; }
trap cleanup EXIT INT TERM

INSTALL="$FIX/inst"
mkdir -p "$INSTALL/releases"

# ── C0. the REAL contract, minted from THIS tree (the target model) ────────
CONTRACT="$FIX/contract.json"
if "$PY" "$MINTER" --repo-root "$REPO_ROOT" > "$CONTRACT" 2>/dev/null && [ -s "$CONTRACT" ]; then
    _pass
else
    _fail "C0: contract mint from the real tree failed (pydantic unavailable?)"
fi

echo "== C1 — the REAL shipped config.yaml against the REAL tree contract =="
# the install MUST carry a config.yaml (the check refuses its absence by
# design — C7); use the REAL shipped config for an end-to-end compatibility
# proof with the REAL tree contract.
cp "$REPO_ROOT/config.yaml" "$INSTALL/config.yaml" 2>/dev/null || \
    { echo "FATAL: repo config.yaml missing" >&2; exit 2; }
C1_OUT="$(INSTALL_DIR="$INSTALL" bash -c '
    . "'"$LIBSH"'" >/dev/null 2>&1
    _config_compat_check "vTEST-C1"
    echo "cc-rc=$?"
' 2>&1)"
assert_contains "C1 the real tree config passes its own contract" "CONFIG-COMPAT=ok" "$C1_OUT"
assert_contains "C1 the check returns 0 (promote proceeds)" "cc-rc=0" "$C1_OUT"

echo "== C2 — unknown TOP-LEVEL key → incompatible =="
CFG_BAD_TOP="$FIX/cfg-bad-top.yaml"
printf 'totally_unknown_section:\n  key: 1\nport: 99999\n' > "$CFG_BAD_TOP"
C2_OUT="$("$PY" "$CHECKER" "$CONTRACT" "$CFG_BAD_TOP")"
C2_RC=$?
assert_rc "C2 unknown top-level key rc" "3" "$C2_RC"
assert_contains "C2 the verdict names config-incompatible" "code=config-incompatible" "$C2_OUT"
assert_contains "C2 the verdict names the offending key" "totally_unknown_section" "$C2_OUT"

echo "== C3 — unknown NESTED key inside an accepted section → incompatible =="
CFG_BAD_NESTED="$FIX/cfg-bad-nested.yaml"
printf 'daemon:\n  section_that_v030_never_had: true\n' > "$CFG_BAD_NESTED"
C3_OUT="$("$PY" "$CHECKER" "$CONTRACT" "$CFG_BAD_NESTED")"
C3_RC=$?
assert_rc "C3 unknown nested key rc" "3" "$C3_RC"
assert_contains "C3 the verdict names the nested offender" "section_that_v030_never_had" "$C3_OUT"

# ── payload-context harness: a MINIMAL staged payload (scripts/upgrade copy
# + the slice pair) so _config_compat_check's BASH_SOURCE resolution lands in
# the PAYLOAD (no tree fallback) — the release-staged promote shape.
make_payload() {
    local root="$1" with_contract="$2" with_checker="$3"
    mkdir -p "$root/scripts/upgrade" "$root/daemon/plugin_subsystem" "$root/releases"
    cp "$LIBSH" "$root/scripts/upgrade/lib.sh"
    cp "$REPO_ROOT/scripts/upgrade/promote.sh" "$root/scripts/upgrade/promote.sh" 2>/dev/null || true
    if [ "$with_checker" = "1" ]; then
        cp "$CHECKER" "$root/daemon/plugin_subsystem/config_compat_check.py"
    fi
    if [ "$with_contract" = "1" ]; then
        cp "$CONTRACT" "$root/daemon/plugin_subsystem/config_compat_contract.json"
    fi
    printf 'port: 99999\n' > "$root/config.yaml"
    mkdir -p "$root/releases/vTEST/releases"
    # the check reads INSTALL_DIR/config.yaml — the INSTALL is the payload
    # root in these legs (release-staged promote = payload root IS the
    # install root for the sandbox shape).
}

echo "== C4 — PAYLOAD context, MISSING contract → refuse 78 (fail-closed) =="
C4_FIX="$FIX/payload-nocontract"
make_payload "$C4_FIX" 0 1
C4_OUT="$(cd /tmp && INSTALL_DIR="$C4_FIX" VERSION="v0.10.8-p2.3-b7cyc1" bash "$C4_FIX/scripts/upgrade/promote_entry_probe.sh" 2>/dev/null)"
C4_RC=$?
# (the probe shim below — a payload lib.sh source + the check call — is the
#  release-staged shape without running the whole promote preflight.)
make_probe() {
    local root="$1"
    cat > "$root/promote_entry_probe.sh" <<PROBE
#!/bin/bash
set -u
export UP_TARGET=demo   # the gate guards demo/live; sandbox skips by design
. "$root/scripts/upgrade/lib.sh"
_config_compat_check "\${1:-vTEST}"
echo "post-refuse-reached rc=\$?"
PROBE
    chmod +x "$root/promote_entry_probe.sh"
}
make_probe "$C4_FIX"
C4_OUT="$(INSTALL_DIR="$C4_FIX" VERSION="v0.10.8-p2.3-b7cyc1" bash "$C4_FIX/promote_entry_probe.sh" 2>&1)"
C4_RC=$?
assert_rc "C4 missing contract: the gate refuses 78" "78" "$C4_RC"
assert_contains "C4 the refusal names config-compat-unrunnable" "config-compat-unrunnable" "$C4_OUT"
assert_contains "C4 the refusal carries the RE-STAGE remedy" "re-stage" "$C4_OUT"

echo "== C5 — PAYLOAD context, MISSING checker module → refuse 78 =="
C5_FIX="$FIX/payload-nochecker"
make_payload "$C5_FIX" 0 0
make_probe "$C5_FIX"
C5_OUT="$(INSTALL_DIR="$C5_FIX" VERSION="vTEST" bash "$C5_FIX/promote_entry_probe.sh" 2>&1)"
C5_RC=$?
assert_rc "C5 missing checker: the gate refuses 78" "78" "$C5_RC"
assert_contains "C5 the refusal names config-compat-unrunnable" "config-compat-unrunnable" "$C5_OUT"

echo "== C6 — unparseable install config.yaml → refuse 78 (fail-closed) =="
C6_FIX="$FIX/payload-badyaml"
make_payload "$C6_FIX" 1 1
printf 'daemon: [unclosed\n' > "$C6_FIX/config.yaml"
make_probe "$C6_FIX"
C6_OUT="$(INSTALL_DIR="$C6_FIX" VERSION="vTEST" bash "$C6_FIX/promote_entry_probe.sh" 2>&1)"
C6_RC=$?
assert_rc "C6 unparseable config: the gate refuses 78" "78" "$C6_RC"
assert_contains "C6 the refusal names config-compat-unrunnable" "config-compat-unrunnable" "$C6_OUT"

echo "== C7 — missing install config.yaml → refuse 78 (the daemon cannot boot) =="
C7_FIX="$FIX/payload-noconfig"
make_payload "$C7_FIX" 1 1
rm -f "$C7_FIX/config.yaml"
make_probe "$C7_FIX"
C7_OUT="$(INSTALL_DIR="$C7_FIX" VERSION="vTEST" bash "$C7_FIX/promote_entry_probe.sh" 2>&1)"
C7_RC=$?
assert_rc "C7 missing config.yaml: the gate refuses 78" "78" "$C7_RC"
assert_contains "C7 the refusal names the missing file + the boot consequence" "refuses to boot" "$C7_OUT"

echo "== C8 — CONTRACT SKEW (contract missing a key the config has) → refuse 78 config-incompatible =="
C8_FIX="$FIX/payload-skew"
make_payload "$C8_FIX" 1 1
# a contract that only knows `port`; the install config carries a `daemon:`
# section the skew contract does not accept → the unknown-key refusal
printf '{"port": null}' > "$C8_FIX/daemon/plugin_subsystem/config_compat_contract.json"
printf 'port: 99999\ndaemon:\n  extra_key_added_by_newer_config: true\n' > "$C8_FIX/config.yaml"
make_probe "$C8_FIX"
C8_OUT="$(INSTALL_DIR="$C8_FIX" VERSION="vTEST" bash "$C8_FIX/promote_entry_probe.sh" 2>&1)"
C8_RC=$?
assert_rc "C8 contract skew: the gate refuses 78" "78" "$C8_RC"
assert_contains "C8 the refusal is config-INCOMPATIBLE (the check bites on shape drift)" "config-incompatible" "$C8_OUT"
assert_contains "C8 the refusal carries the lane's remedy message" "migrate config or pin a compatible release" "$C8_OUT"

echo "== C8b — sandbox target: the check SKIPS (the free exercise lane; D-2 scope) =="
C8B_FIX="$FIX/payload-sandbox"
make_payload "$C8B_FIX" 1 0   # NO contract — a sandbox promote must still proceed
cat > "$C8B_FIX/promote_entry_probe.sh" <<PROBE
#!/bin/bash
set -u
export UP_TARGET=sandbox
. "$C8B_FIX/scripts/upgrade/lib.sh"
_config_compat_check "\${1:-vTEST}"
echo "post-refuse-reached rc=\$?"
PROBE
chmod +x "$C8B_FIX/promote_entry_probe.sh"
C8B_OUT="$(INSTALL_DIR="$C8B_FIX" VERSION="vTEST" bash "$C8B_FIX/promote_entry_probe.sh" 2>&1)"
C8B_RC=$?
assert_rc "C8b sandbox target: the check skips (rc 0, no contract needed)" "0" "$C8B_RC"
assert_contains "C8b the skip is LOUD (named, not silent)" "entry check skipped" "$C8B_OUT"

echo "== C9 — the checker + minter are executor-family members (travel with every payload) =="
FAM_OUT="$(bash -c '. "'"$REPO_ROOT"'/scripts/upgrade/executor_family.sh"; executor_family_paths' 2>/dev/null)"
assert_contains "C9 config_compat_check.py is a family member" "daemon/plugin_subsystem/config_compat_check.py" "$FAM_OUT"
assert_contains "C9 config_compat_contract.py is a family member" "daemon/plugin_subsystem/config_compat_contract.py" "$FAM_OUT"

# ── summary ────────────────────────────────────────────────────────────────
echo
echo "========================================"
if [ "$FAIL" -eq 0 ]; then
    echo "config-compat tests: $PASS passed, 0 failed — ALL PASS"
    exit 0
else
    echo "config-compat tests: $PASS passed, $FAIL failed"
    echo "$FAILED_TESTS"
    exit 1
fi
