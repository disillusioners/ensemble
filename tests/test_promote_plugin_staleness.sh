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
# MATRIX (the dispatch minimum — slice-⑥ baseline + allow-stale flip
# 2026-10-07 default + STRICT opt-in):
#   (1) fresh                         → gate passes (rc 0), no override
#                                       consumed
#   (2) fresh + override              → passes identically (override
#                                       unused)
#   (3) stale default                 → PROCEEDS rc 0 + journaled
#                                       `plugin_staleness_observed` with
#                                       the predicate's reason token
#                                       (default; allow-stale flip)
#   (4) stale + override              → PROCEEDS rc 0 + journaled
#                                       `plugin_staleness_override`
#                                       (audit-continuity back-compat —
#                                       the OLD slice-⑥ event kind;
#                                       distinct from the default
#                                       observed event)
#   (5) escalation default            → PROCEEDS rc 0 + journaled
#                                       observed (default behavior);
#                                       inside-window does NOT add an
#                                       escalation refusal since the
#                                       predicate no longer fails open
#   (5a) escalation + STRICT          → refuses 78 + journals
#                                       `refusal` event with the
#                                       predicate's code token
#   (6) no plugins tree               → gate passes (no spurious block
#                                       on a plugin-less repo)
#   (7a) e2e stale default            → PROCEEDS + journaled observed
#                                       on the install dir
#   (7b) e2e stale + --allow-stale    → PROCEEDS + journaled override
#   (7c) e2e stale + --block-on-stale → refuses 78 + journaled refusal
#                                       on the install dir
#   (8) unevaluable gate              → refuses 78 (fail-closed on
#                                       predicate unmissing; allow-stale
#                                       flip keeps this loud — stale
#                                       pins are the steady state,
#                                       unknown tooling is NOT)
#   (9) fresh default                 → no observed event journaled
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

assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "does NOT contain '$needle'" "$haystack" ;;
        *) _pass ;;
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

section "(3) stale default — PROCEEDS + journals plugin_staleness_observed"
# Fresh install dir per case so the journal is isolated (default
# observe-and-proceed needs an install dir to durably land the
# observed event — the helper is gated on `[ -d "$INSTALL_DIR" ]`).
STALE_DEFAULT_INSTALL="$FIXTURE/install-stale-default"
mkdir -p "$STALE_DEFAULT_INSTALL"
out="$(
    { export PLUGIN_STALENESS_PLUGINS_ROOT="$STALE_REPO/plugins"
    export PLUGIN_STALENESS_PYTHON="$PRED_PY"
    export PROMOTE_STALENESS_OVERRIDE="0"
    export PROMOTE_STRICT_STALENESS="0"
    export INSTALL_DIR="$STALE_DEFAULT_INSTALL"
    SCRIPT_DIR="$UPGRADE_DIR"
    # shellcheck disable=SC1090
    . "$UPGRADE_DIR/lib.sh"
    promote_plugin_staleness_check
    } 2>&1
)"
rc=$?
assert_eq "stale default rc" "0" "$rc"
# The gate's external surface is the OBSERVED warn + the journaled
# `plugin_staleness_observed` event (the predicate's raw output is
# consumed inside the function and is NOT in the gate's stdout —
# that's by design, to avoid leaking predicate internals into the
# operator's stdout when the default observe-and-proceed is taken).
case "$out" in
    *"PLUGIN-STALENESS OBSERVED"*) _pass "stale default warns observed" ;;
    *) _fail "stale default warns observed" "PLUGIN-STALENESS OBSERVED" "$out" ;;
esac
journal="$(cat "$STALE_DEFAULT_INSTALL/releases/state.json" 2>/dev/null || true)"
assert_contains "stale default journals observed event" "plugin_staleness_observed" "$journal"
assert_contains "stale default journaled predicate code" "pin-stale" "$journal"
assert_contains "stale default journaled plugin name" "plugin=$STALE_REPO/plugins/demo" "$journal"
assert_not_contains "stale default does NOT journal override" "plugin_staleness_override" "$journal"

section "(4) stale + override — passes AND journals the override (NOT the observed)"
OVERRIDE_INSTALL="$FIXTURE/install-override"
mkdir -p "$OVERRIDE_INSTALL"
out="$(
    { export PLUGIN_STALENESS_PLUGINS_ROOT="$STALE_REPO/plugins"
    export PLUGIN_STALENESS_PYTHON="$PRED_PY"
    export PROMOTE_STALENESS_OVERRIDE="1"
    export PROMOTE_STRICT_STALENESS="0"
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
# Audit-continuity invariant: when the EXPLICIT override path is
# taken, the journal records the OLD plugin_staleness_override event
# kind — NOT the new plugin_staleness_observed default. Operators
# grepping the journal can distinguish "operator explicitly accepted
# the stale promote" from "stale observed unattended".
assert_not_contains "override path does NOT journal observed" "plugin_staleness_observed" "$journal"

section "(3a) stale + STRICT env — refuses 78 + journals refusal"
STALE_STRICT_INSTALL="$FIXTURE/install-stale-strict"
mkdir -p "$STALE_STRICT_INSTALL"
# Run via subprocess so `_freshness_refuse`'s `exit 78` doesn't
# terminate the test harness shell. The subprocess's stdout is the
# captured value (the refuse helper doesn't print), the stderr
# captures the WARN line. The harness reads $? AFTER the
# substitution to get the subprocess rc.
cat >"$FIXTURE/strict-runner.sh" <<RUNNER
#!/bin/bash
set +e
export PLUGIN_STALENESS_PLUGINS_ROOT="$STALE_REPO/plugins"
export PLUGIN_STALENESS_PYTHON="$PRED_PY"
export PROMOTE_STALENESS_OVERRIDE="0"
export PROMOTE_STRICT_STALENESS="1"
export INSTALL_DIR="$STALE_STRICT_INSTALL"
export SCRIPT_DIR="$UPGRADE_DIR"
# shellcheck disable=SC1090
. "$UPGRADE_DIR/lib.sh"
promote_plugin_staleness_check
RUNNER
chmod +x "$FIXTURE/strict-runner.sh"
set +e
strict_rc=0
bash "$FIXTURE/strict-runner.sh" 2>/tmp/stale-strict.err
strict_rc=$?
set -u
strict_err="$(cat /tmp/stale-strict.err 2>/dev/null || true)"
assert_eq "stale+strict rc" "78" "$strict_rc"
assert_contains "stale+strict warns strict mode" "strict staleness mode" "$strict_err"
assert_contains "stale+strict carries predicate code" "pin-stale" "$strict_err"
strict_journal="$(cat "$STALE_STRICT_INSTALL/releases/state.json" 2>/dev/null || true)"
assert_contains "stale+strict journals refusal event" '"event":"refusal"' "$strict_journal"
assert_contains "stale+strict refusal carries code" "pin-stale" "$strict_journal"
# Strict must NOT also emit the observed/override events on the
# refusal path.
assert_not_contains "stale+strict does NOT journal observed" "plugin_staleness_observed" "$strict_journal"
assert_not_contains "stale+strict does NOT journal override" "plugin_staleness_override" "$strict_journal"

section "(5) unowned-alarm escalation default — PROCEEDS + journals observed"
# Fresh install dirs so the journal lands durably.
ESC_DEFAULT_INSTALL="$FIXTURE/install-escalate-default"
ESC_FRESH_INSTALL="$FIXTURE/install-escalate-fresh-default"
mkdir -p "$ESC_DEFAULT_INSTALL" "$ESC_FRESH_INSTALL"
out="$(
    { export PLUGIN_STALENESS_PLUGINS_ROOT="$ESCALATE_REPO/plugins"
    export PLUGIN_STALENESS_PYTHON="$PRED_PY"
    export PROMOTE_STALENESS_OVERRIDE="0"
    export PROMOTE_STRICT_STALENESS="0"
    export INSTALL_DIR="$ESC_DEFAULT_INSTALL"
    SCRIPT_DIR="$UPGRADE_DIR"
    # shellcheck disable=SC1090
    . "$UPGRADE_DIR/lib.sh"
    promote_plugin_staleness_check
    } 2>&1
)"
rc=$?
assert_eq "escalated default rc" "0" "$rc"
case "$out" in
    *"PLUGIN-STALENESS OBSERVED"*) _pass "escalated default warns observed" ;;
    *) _fail "escalated default warns observed" "PLUGIN-STALENESS OBSERVED" "$out" ;;
esac
esc_journal="$(cat "$ESC_DEFAULT_INSTALL/releases/state.json" 2>/dev/null || true)"
assert_contains "escalated default journals observed" "plugin_staleness_observed" "$esc_journal"
assert_contains "escalated default journaled code" "alarm-owner-escalation" "$esc_journal"

# inside-window: the OPEN divergence itself still flags (and is
# journaled as observed); the predicate does not add an escalation
# refusal — the gate cannot know which token to grep, so it picks
# the first one (per the existing capture discipline).
out="$(
    { export PLUGIN_STALENESS_PLUGINS_ROOT="$ESCALATE_FRESH_REPO/plugins"
    export PLUGIN_STALENESS_PYTHON="$PRED_PY"
    export PROMOTE_STALENESS_OVERRIDE="0"
    export PROMOTE_STRICT_STALENESS="0"
    export INSTALL_DIR="$ESC_FRESH_INSTALL"
    SCRIPT_DIR="$UPGRADE_DIR"
    # shellcheck disable=SC1090
    . "$UPGRADE_DIR/lib.sh"
    promote_plugin_staleness_check
    } 2>&1
)"
rc=$?
assert_eq "inside-window default rc" "0" "$rc"
case "$out" in
    *"PLUGIN-STALENESS OBSERVED"*) _pass "inside-window default warns observed" ;;
    *) _fail "inside-window default" "PLUGIN-STALENESS OBSERVED warn" "$out" ;;
esac
inside_journal="$(cat "$ESC_FRESH_INSTALL/releases/state.json" 2>/dev/null || true)"
assert_contains "inside-window default journals observed" "plugin_staleness_observed" "$inside_journal"

section "(5a) unowned-alarm escalation + STRICT — refuses 78"
ESC_STRICT_INSTALL="$FIXTURE/install-escalate-strict"
mkdir -p "$ESC_STRICT_INSTALL"
cat >"$FIXTURE/escalate-strict-runner.sh" <<RUNNER
#!/bin/bash
set +e
export PLUGIN_STALENESS_PLUGINS_ROOT="$ESCALATE_REPO/plugins"
export PLUGIN_STALENESS_PYTHON="$PRED_PY"
export PROMOTE_STALENESS_OVERRIDE="0"
export PROMOTE_STRICT_STALENESS="1"
export INSTALL_DIR="$ESC_STRICT_INSTALL"
export SCRIPT_DIR="$UPGRADE_DIR"
# shellcheck disable=SC1090
. "$UPGRADE_DIR/lib.sh"
promote_plugin_staleness_check
RUNNER
chmod +x "$FIXTURE/escalate-strict-runner.sh"
set +e
esc_strict_rc=0
bash "$FIXTURE/escalate-strict-runner.sh" 2>/tmp/escalate-strict.err
esc_strict_rc=$?
set -u
esc_strict_err="$(cat /tmp/escalate-strict.err 2>/dev/null || true)"
assert_eq "escalation+strict rc" "78" "$esc_strict_rc"
assert_contains "escalation+strict warns strict mode" "strict staleness mode" "$esc_strict_err"
esc_strict_journal="$(cat "$ESC_STRICT_INSTALL/releases/state.json" 2>/dev/null || true)"
assert_contains "escalation+strict journals refusal" '"event":"refusal"' "$esc_strict_journal"
assert_contains "escalation+strict refusal code" "alarm-owner-escalation" "$esc_strict_journal"

section "(6) no plugins tree — gate passes (no spurious block)"
out="$(_run_gate "$FIXTURE/empty-repo" 0)"
rc=$?
assert_eq "plugin-less rc" "0" "$rc"
assert_contains "plugin-less log" "no plugins tree" "$out"

section "(8) unevaluable gate — refuse carries the PyYAML detail (audit trail reconstructable)"
# Force the no-interpreter path: PLUGIN_STALENESS_PYTHON dead, REPO_ROOT
# dead (kills the .venv candidate), PATH dead (kills the python3/python
# fallbacks). The refuse WARN line must carry the detail fragment — the
# review confirmed $localdetail expanded EMPTY here (audit-trail hole).
UNEVAL_OUT="$(
    {
        export PLUGIN_STALENESS_PLUGINS_ROOT="$FRESH_REPO/plugins"
        export PLUGIN_STALENESS_PYTHON="$FIXTURE/no-such-python"
        export REPO_ROOT="/nonexistent"
        export PATH="/nonexistent"
        export PROMOTE_STALENESS_OVERRIDE="0"
        export PROMOTE_STRICT_STALENESS="0"
        export INSTALL_DIR="$FIXTURE/install-nopython"
        SCRIPT_DIR="$UPGRADE_DIR"
        # shellcheck disable=SC1090
        . "$UPGRADE_DIR/lib.sh"
        promote_plugin_staleness_check
    } 2>&1
)"
UNEVAL_RC=$?
assert_eq "unevaluable rc" "78" "$UNEVAL_RC"
assert_contains "unevaluable token" "plugin-staleness-unreadable" "$UNEVAL_OUT"
assert_contains "unevaluable carries the detail" "no python with PyYAML" "$UNEVAL_OUT"

section "(8a) unevaluable + STRICT — still refuses 78 (strict does NOT bypass unevaluable)"
# The allow-stale flip keeps the unevaluable gate fail-closed (only
# an actual STALE verdict flips the default — unknown tooling stays
# loud). The OVERRIDE flag is the ONLY way through an unevaluable gate;
# STRICT is irrelevant (no predicate output to be strict about).
UNEVAL_STRICT_OUT="$(
    {
        export PLUGIN_STALENESS_PLUGINS_ROOT="$FRESH_REPO/plugins"
        export PLUGIN_STALENESS_PYTHON="$FIXTURE/no-such-python"
        export REPO_ROOT="/nonexistent"
        export PATH="/nonexistent"
        export PROMOTE_STALENESS_OVERRIDE="0"
        export PROMOTE_STRICT_STALENESS="1"
        export INSTALL_DIR="$FIXTURE/install-nopython-strict"
        SCRIPT_DIR="$UPGRADE_DIR"
        # shellcheck disable=SC1090
        . "$UPGRADE_DIR/lib.sh"
        promote_plugin_staleness_check
    } 2>&1
)"
UNEVAL_STRICT_RC=$?
assert_eq "unevaluable+strict rc" "78" "$UNEVAL_STRICT_RC"
assert_contains "unevaluable+strict token" "plugin-staleness-unreadable" "$UNEVAL_STRICT_OUT"

section "(9) fresh default — NO plugin_staleness_observed journaled"
# Sanity check: the new event kind must NOT fire for fresh pins. The
# fresh case is the steady-state steady — emitting an observed event
# for every fresh promote would pollute the audit trail.
FRESH_DEFAULT_INSTALL="$FIXTURE/install-fresh-default"
mkdir -p "$FRESH_DEFAULT_INSTALL"
out="$(
    { export PLUGIN_STALENESS_PLUGINS_ROOT="$FRESH_REPO/plugins"
    export PLUGIN_STALENESS_PYTHON="$PRED_PY"
    export PROMOTE_STALENESS_OVERRIDE="0"
    export PROMOTE_STRICT_STALENESS="0"
    export INSTALL_DIR="$FRESH_DEFAULT_INSTALL"
    SCRIPT_DIR="$UPGRADE_DIR"
    # shellcheck disable=SC1090
    . "$UPGRADE_DIR/lib.sh"
    promote_plugin_staleness_check
    } 2>&1
)"
rc=$?
assert_eq "fresh default rc" "0" "$rc"
assert_contains "fresh default verdict line" "PLUGIN-STALENESS=fresh" "$out"
case "$out" in
    *"PLUGIN-STALENESS OBSERVED"*) _fail "fresh default" "no PLUGIN-STALENESS OBSERVED" "$out" ;;
    *) _pass "fresh default does not emit OBSERVED warn" ;;
esac
fresh_journal="$(cat "$FRESH_DEFAULT_INSTALL/releases/state.json" 2>/dev/null || true)"
assert_not_contains "fresh default does NOT journal observed" "plugin_staleness_observed" "$fresh_journal"
assert_not_contains "fresh default does NOT journal override" "plugin_staleness_override" "$fresh_journal"

# ─── (7) End-to-end through the REAL promote.sh ──────────────────────────────

section "(7a) promote.sh e2e stale default — plugin gate PROCEEDS + journals observed"

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
# After the allow-stale flip, the plugin gate PROCEEDS by default.
# The downstream preflight stages (e.g. integrity of the stub
# release, missing manifest.json) WILL refuse 78 — that's expected
# and INTENTIONAL here (the test fixture is deliberately minimal;
# the assertion is that the refusal is NOT a plugin-staleness
# refusal, i.e. the plugin gate was successfully passed).
case "$e2e_out" in
    *"PLUGIN-STALENESS OBSERVED"*) _pass "e2e stale default emits OBSERVED warn" ;;
    *) _fail "e2e stale default" "PLUGIN-STALENESS OBSERVED warn" "$e2e_out" ;;
esac
case "$e2e_out" in
    *"pin-stale"*)
        if printf '%s' "$e2e_out" | grep -q "PLUGIN-STALENESS OBSERVED"; then
            _pass "e2e stale default got past the plugin gate (observed warn present)"
        else
            _fail "e2e stale default" "plugin gate passed via observe-and-proceed" "$e2e_out"
        fi
        ;;
    *) _pass "e2e stale default got past the plugin gate (no pin-stale in output)" ;;
esac
journal1="$(cat "$E2E_HOME/agents-ensemble-demo/releases/state.json" 2>/dev/null || true)"
assert_contains "e2e stale default journals observed event" "plugin_staleness_observed" "$journal1"
assert_contains "e2e stale default journaled code" "pin-stale" "$journal1"
assert_not_contains "e2e stale default does NOT journal override" "plugin_staleness_override" "$journal1"
# Use e2e_rc to avoid shellcheck unused-var warning.
[ "$e2e_rc" = "0" ] || [ "$e2e_rc" = "78" ] || _fail "e2e stale default rc sanity" "0 or 78 (downstream refusal)" "$e2e_rc"

section "(7b) promote.sh e2e stale + --allow-stale-plugins — PROCEEDS + journals override (audit-continuity)"
# second fixture home so the override run has a virgin journal
E2E_HOME2="$FIXTURE/home2"
_make_install_fixture "$E2E_HOME2" "v9.9.9"
e2e_out="$(_run_promote_home "$FIXTURE/repo-stale" "$E2E_HOME2" --allow-stale-plugins)"
e2e_rc=$?
# The explicit override lets it PAST the plugin gate: the refusal
# (if any, from later preflight stages like the stub release's
# integrity) must NOT be a plugin-staleness token.
case "$e2e_out" in
    *"PLUGIN-STALENESS OVERRIDE"*) _pass "e2e override emits OVERRIDE warn (audit-continuity)" ;;
    *) _fail "e2e override" "PLUGIN-STALENESS OVERRIDE warn" "$e2e_out" ;;
esac
case "$e2e_out" in
    *"pin-stale"*)
        if printf '%s' "$e2e_out" | grep -q "PLUGIN-STALENESS OVERRIDE"; then
            _pass "e2e override got past the plugin gate (override warn present)"
        else
            _fail "e2e override" "plugin gate passed via override" "$e2e_out"
        fi
        ;;
    *) _pass "e2e override got past the plugin gate (no pin-stale in output)" ;;
esac
journal2="$(cat "$E2E_HOME2/agents-ensemble-demo/releases/state.json" 2>/dev/null || true)"
assert_contains "e2e override journals override event" "plugin_staleness_override" "$journal2"
# Audit-continuity invariant: explicit override → OLD override event,
# NOT the new observed event. Operators grepping the journal can
# distinguish "operator explicitly accepted stale" from "stale
# observed unattended".
assert_not_contains "e2e override does NOT journal observed" "plugin_staleness_observed" "$journal2"

section "(7c) promote.sh e2e stale + --block-on-stale — refuses 78 + journals refusal"
# third fixture home so the strict run has a virgin journal
E2E_HOME3="$FIXTURE/home3"
_make_install_fixture "$E2E_HOME3" "v9.9.9"
# Strict refuses at the gate — subshell wrapper so we can capture the
# rc without the script bailing out.
e2e_strict_rc="$(
    { _run_promote_home "$FIXTURE/repo-stale" "$E2E_HOME3" --block-on-stale; } >/tmp/e2e-strict.out 2>&1
    echo $?
)"
e2e_strict_out="$(cat /tmp/e2e-strict.out)"
assert_eq "e2e strict rc" "78" "$e2e_strict_rc"
case "$e2e_strict_out" in
    *"strict staleness mode"*) _pass "e2e strict warns strict mode" ;;
    *) _fail "e2e strict" "strict staleness mode warn" "$e2e_strict_out" ;;
esac
assert_contains "e2e strict predicate code" "pin-stale" "$e2e_strict_out"
journal3="$(cat "$E2E_HOME3/agents-ensemble-demo/releases/state.json" 2>/dev/null || true)"
assert_contains "e2e strict journals refusal event" '"event":"refusal"' "$journal3"
assert_contains "e2e strict refusal code" "pin-stale" "$journal3"
assert_not_contains "e2e strict does NOT journal observed" "plugin_staleness_observed" "$journal3"
assert_not_contains "e2e strict does NOT journal override" "plugin_staleness_override" "$journal3"

# ─── Summary ─────────────────────────────────────────────────────────────────

printf '\n== Summary ==\n'
printf 'PASS: %s  FAIL: %s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'Failed tests:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
