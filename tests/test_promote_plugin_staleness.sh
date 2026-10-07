#!/bin/bash
# ============================================================================
# tests/test_promote_plugin_staleness.sh — slice ⑥ promote-staleness gate
# (REC §1.2 component 13: promote_staleness_check, wired to promote.sh)
# ============================================================================
# THE TRAP. Plugin staleness used to be invisible at promote time: a pin
# could sit months past its upstream tag with open divergences and an
# unowned alarm, and promote.sh would flip it anyway ("4.5 months
# silent"). The structural fix is the plugin-staleness predicate in the
# preflight — pin age > 14d OR unresolved divergence OR unowned-alarm
# escalation ⇒ refuse (exit 78, journaled reason token), argv-only
# override (--allow-stale-plugins) journaled on the install dir. The
# matrix is exercised HERE through the REAL lib.sh function and the REAL
# promote.sh.
#
# MATRIX (the dispatch minimum — all four + escalation):
#   (1) fresh            → gate passes (rc 0), no override consumed
#   (2) fresh + override → passes identically (override unused)
#   (3) stale            → REFUSES exit 78 with the predicate's reason
#                          token (pin-stale / staleness-unknown /
#                          divergence-unresolved / alarm-owner-escalation)
#   (4) stale + override → gate passes; the override is JOURNALED on the
#                          install dir (plugin_staleness_override record)
#   (5) escalation row   → unowned alarm beyond N days ⇒ refuses with
#                          alarm-owner-escalation; inside the window it
#                          does NOT add an escalation refusal
#   (6) no plugins tree  → gate passes (no spurious block on a
#                          plugin-less repo)
#   (7) e2e promote.sh   → stale fixture refuses 78 with the plugin
#                          token in stderr + journal; with the flag the
#                          promote gets PAST the plugin gate (refuses
#                          later for an unrelated, non-plugin reason)
#
# SANDBOX discipline (test-strategy.md §5.5): zero side effects outside
# mktemp dirs; HOME overridden so no live/demo install dir can resolve;
# no daemon, no port, no network. The predicate runs via python3 + PyYAML
# (PLUGIN_STALENESS_PYTHON pinned to the venv python when available).
#
# Run:
#   bash tests/test_promote_plugin_staleness.sh
# ============================================================================

set -u

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

section() { printf '\n== %s ==\n' "$1"; }

# ─── Fixture: a manifest-bearing plugin tree ────────────────────────────────

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/promote-staleness.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
trap 'rm -rf "$FIXTURE"' EXIT

# Pin the interpreter: prefer the repo venv python (has PyYAML), fall
# back to system python3.
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PRED_PY="$REPO_ROOT/.venv/bin/python"
else
    PRED_PY="$(command -v python3)"
fi

_write_manifest() {  # <dir> <pin_age_days> <register_status> <snapshot_alarm_owner>
    local dir="$1" staleness="$2" status="$3" owner="$4"
    mkdir -p "$dir/copy_freely/data" "$dir/snapshot_with_drift_alarm/prompts"
    printf 'x\n' > "$dir/copy_freely/data/a.txt"
    {
        printf 'schema_version: "1.0.0"\n'
        printf 'plugin:\n  name: demo\n  license: "Apache-2.0"\n'
        printf '  upstream:\n    repo: "%s"\n' "$FIXTURE/upstream"
        printf '    tag_pin_per_class:\n      copy_freely: "v1.0.0"\n'
        printf '  integration_path: "C"\n  execution_mode: "resource-only"\n'
        printf 'copy_freely:\n  paths: ["copy_freely/data/"]\n'
        printf '  alarm_owner: "owner-data"\n'
        printf '  escalation: "block-promote-after-days"\n'
        printf 'snapshot_with_drift_alarm:\n  paths: ["snapshot_with_drift_alarm/prompts/"]\n'
        printf '  alarm_owner: "%s"\n' "$owner"
        printf '  escalation: "block-promote-after-days"\n'
        printf '  divergence_register:\n    - id: 1\n'
        printf '      files: ["snapshot_with_drift_alarm/prompts/x.ts"]\n'
        printf '      delta: "seeded marker"\n      rationale: "fixture"\n'
        printf '      pinning_test: "test_x.py::test_y"\n'
        printf '      status: "%s"\n' "$status"
        printf 'parity_boundary:\n  intentionally_not_vendored: []\n  not_executed: []\n'
    } > "$dir/MANIFEST.yaml"
    # trail lines for BOTH classes, staleness computed at recorded_at = now
    printf '{"recorded_at": "%s", "sync_result": {"plugin": "demo", "target_class": "copy_freely", "upstream_tag": "v1.0.0", "action": "no_change", "diff_summary": {"files_added": 0, "files_modified": 0, "files_removed": 0}, "staleness_age_days": %s}}\n' \
        "$("$PRED_PY" -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())')" \
        "$staleness" > "$dir/sync_trail.jsonl"
    printf '{"recorded_at": "%s", "sync_result": {"plugin": "demo", "target_class": "snapshot_with_drift_alarm", "upstream_tag": "v1.0.0", "action": "no_change", "diff_summary": {"files_added": 0, "files_modified": 0, "files_removed": 0}, "staleness_age_days": %s}}\n' \
        "$("$PRED_PY" -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())')" \
        "$staleness" >> "$dir/sync_trail.jsonl"
}

# A fresh fixture: pin 5 days old, owned alarm, no open divergences.
FRESH_REPO="$FIXTURE/repo-fresh"
mkdir -p "$FRESH_REPO/plugins"
_write_manifest "$FRESH_REPO/plugins/demo" 5 registered owner-a

# A stale fixture: pin 30 days old.
STALE_REPO="$FIXTURE/repo-stale"
mkdir -p "$STALE_REPO/plugins"
_write_manifest "$STALE_REPO/plugins/demo" 30 registered owner-a

# An escalation fixture: pin fresh BUT the divergence is OPEN and the
# alarm is UNOWNED, observed 30 days ago.
ESCALATE_REPO="$FIXTURE/repo-escalate"
mkdir -p "$ESCALATE_REPO/plugins"
_write_manifest "$ESCALATE_REPO/plugins/demo" 5 open ""
"$PRED_PY" - "$ESCALATE_REPO/plugins/demo" <<'PYEOF'
import datetime, json, sys
from pathlib import Path
p = Path(sys.argv[1]) / "sync_trail.jsonl"
old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat()
lines = p.read_text().splitlines()
out = []
for line in lines:
    entry = json.loads(line)
    entry["recorded_at"] = old  # age the evidence (both classes)
    out.append(json.dumps(entry))
p.write_text("\n".join(out) + "\n")
PYEOF

# An escalation-inside-window fixture: same but observed 5 days ago.
ESCALATE_FRESH_REPO="$FIXTURE/repo-escalate-fresh"
mkdir -p "$ESCALATE_FRESH_REPO/plugins"
_write_manifest "$ESCALATE_FRESH_REPO/plugins/demo" 5 open ""

# ─── The gate function, exercised through the REAL lib.sh ───────────────────

_run_gate_inner() {  # <repo_root_with_plugins> [PROMOTE_STALENESS_OVERRIDE]
    local repo="$1" override="${2:-0}"
    (
        export PLUGIN_STALENESS_PLUGINS_ROOT="$repo/plugins"
        export PLUGIN_STALENESS_PYTHON="$PRED_PY"
        export PROMOTE_STALENESS_OVERRIDE="$override"
        export INSTALL_DIR="$FIXTURE/install-$$"   # journal landing (fresh dir per call)
        SCRIPT_DIR="$UPGRADE_DIR"
        # shellcheck disable=SC1090
        . "$UPGRADE_DIR/lib.sh"
        promote_plugin_staleness_check
    )
}
_run_gate() {  # <repo_root_with_plugins> [PROMOTE_STALENESS_OVERRIDE]
    _run_gate_inner "$@" 2>&1
}

section "(1)+(2) fresh — passes; override unconsumed"
out="$(_run_gate "$FRESH_REPO" 0)"
rc=$?
assert_eq "fresh rc" "0" "$rc"
assert_contains "fresh verdict line" "PLUGIN-STALENESS=fresh" "$out"

out="$(_run_gate "$FRESH_REPO" 1)"
rc=$?
assert_eq "fresh+override rc" "0" "$rc"

section "(3) stale — refuses 78 with the predicate token"
out="$(_run_gate "$STALE_REPO" 0)"
rc=$?
assert_eq "stale rc" "78" "$rc"
assert_contains "stale token" "pin-stale" "$out"

section "(4) stale + override — passes AND journals the override"
OVERRIDE_INSTALL="$FIXTURE/install-override"
out="$(
    { export PLUGIN_STALENESS_PLUGINS_ROOT="$STALE_REPO/plugins"
    export PLUGIN_STALENESS_PYTHON="$PRED_PY"
    export PROMOTE_STALENESS_OVERRIDE="1"
    export INSTALL_DIR="$OVERRIDE_INSTALL"
    SCRIPT_DIR="$UPGRADE_DIR"
    # shellcheck disable=SC1090
    . "$UPGRADE_DIR/lib.sh"
    journal_init >/dev/null 2>&1 || true
    promote_plugin_staleness_check
    } 2>&1
)"
rc=$?
assert_eq "stale+override rc" "0" "$rc"
assert_contains "override warn" "PLUGIN-STALENESS OVERRIDE" "$out"
journal="$(cat "$OVERRIDE_INSTALL/releases/state.json" 2>/dev/null || true)"
assert_contains "override journaled" "plugin_staleness_override" "$journal"
assert_contains "override journal token" "pin-stale" "$journal"
assert_contains "override journal operator_accepted" "operator_accepted=true" "$journal"

section "(5) unowned-alarm escalation"
out="$(_run_gate "$ESCALATE_REPO" 0)"
rc=$?
assert_eq "escalated rc" "78" "$rc"
assert_contains "escalation token" "alarm-owner-escalation" "$out"
out="$(_run_gate "$ESCALATE_FRESH_REPO" 0)"
rc=$?
# inside the window: no ESCALATION refusal — but the OPEN divergence
# itself still refuses (divergence-unresolved)
assert_eq "inside-window rc" "78" "$rc"
case "$out" in
    *"alarm-owner-escalation"*) _fail "inside-window escalation" "no alarm-owner-escalation" "$out" ;;
    *) _pass ;;
esac
assert_contains "inside-window still flags the open divergence" "divergence-unresolved" "$out"

section "(6) no plugins tree — gate passes (no spurious block)"
out="$(_run_gate "$FIXTURE/empty-repo" 0)"
rc=$?
assert_eq "plugin-less rc" "0" "$rc"
assert_contains "plugin-less log" "no plugins tree" "$out"

# ─── (7) End-to-end through the REAL promote.sh ──────────────────────────────

section "(7) promote.sh e2e — stale refuses 78; override gets past the plugin gate"

# The demo target resolves its install dir as $HOME/agents-ensemble-demo
# (resolve_env D-FA4.6 rules — INSTALL_DIR is a SANDBOX-only override), so
# the fixture homes the install under the sandboxed HOME.
_make_install_fixture() {  # <home_dir> <version>
    local home="$1" version="$2"
    local install="$home/agents-ensemble-demo"
    mkdir -p "$install/releases/$version"
    printf 'stub\n' > "$install/releases/$version/ensemble-prod"
}

_run_promote() {  # <repo_root> [extra flags...] — HOME fixed to the first e2e home
    local repo="$1"
    shift
    (
        cd "$repo" || exit 99
        export HOME="$E2E_HOME"
        export PORT="4999"
        export PLUGIN_STALENESS_PYTHON="$PRED_PY"
        mkdir -p "$HOME"
        VERSION="v9.9.9" bash scripts/upgrade/promote.sh demo "$@" 2>&1
    )
}

_run_promote_home() {  # <repo_root> <home> [extra flags...]
    local repo="$1" home="$2"
    shift 2
    (
        cd "$repo" || exit 99
        export HOME="$home"
        export PORT="4999"
        export PLUGIN_STALENESS_PYTHON="$PRED_PY"
        mkdir -p "$HOME"
        VERSION="v9.9.9" bash scripts/upgrade/promote.sh demo "$@" 2>&1
    )
}

# The e2e fixture repo carries the REAL upgrade scripts (the
# stage-freshness-harness pattern): the fixture repo IS the promote
# cwd, so scripts/upgrade must exist there.
mkdir -p "$FIXTURE/repo-stale/scripts"
cp -R "$UPGRADE_DIR" "$FIXTURE/repo-stale/scripts/upgrade"
cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$FIXTURE/repo-stale/scripts/stop-ensemble.sh" 2>/dev/null || \
    printf '#!/bin/sh\nexit 0\n' > "$FIXTURE/repo-stale/scripts/stop-ensemble.sh"
chmod +x "$FIXTURE/repo-stale/scripts/upgrade/"*.sh "$FIXTURE/repo-stale/scripts/stop-ensemble.sh"
# the predicate module travels with the checkout the same way (single
# self-contained stdlib+PyYAML file — no daemon package import needed)
mkdir -p "$FIXTURE/repo-stale/daemon/plugin_subsystem"
cp "$REPO_ROOT/daemon/plugin_subsystem/promote_staleness.py" \
   "$FIXTURE/repo-stale/daemon/plugin_subsystem/promote_staleness.py"

E2E_HOME="$FIXTURE/home"
_make_install_fixture "$E2E_HOME" "v9.9.9"
e2e_out="$(_run_promote "$FIXTURE/repo-stale")"
e2e_rc=$?
assert_eq "e2e stale rc" "78" "$e2e_rc"
assert_contains "e2e stale token" "pin-stale" "$e2e_out"

# second fixture home so the override run has a virgin journal
E2E_HOME2="$FIXTURE/home2"
_make_install_fixture "$E2E_HOME2" "v9.9.9"
e2e_out="$(_run_promote_home "$FIXTURE/repo-stale" "$E2E_HOME2" --allow-stale-plugins)"
e2e_rc=$?
# The override let it PAST the plugin gate: the refusal (if any) must
# NOT be a plugin-staleness token — the promote proceeds to the next
# preflight stage (integrity of the stub release, etc.).
case "$e2e_out" in
    *"pin-stale"*)
        if printf '%s' "$e2e_out" | grep -q "PLUGIN-STALENESS OVERRIDE"; then
            _pass "e2e override got past the plugin gate (override warn present)"
        else
            _fail "e2e override" "plugin gate passed via override" "$e2e_out"
        fi
        ;;
    *) _pass "e2e override got past the plugin gate" ;;
esac
journal2="$(cat "$E2E_HOME2/agents-ensemble-demo/releases/state.json" 2>/dev/null || true)"
assert_contains "e2e override journaled" "plugin_staleness_override" "$journal2"

# ─── Summary ─────────────────────────────────────────────────────────────────

printf '\n== Summary ==\n'
printf 'PASS: %s  FAIL: %s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'Failed tests:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
