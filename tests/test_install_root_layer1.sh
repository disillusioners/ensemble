#!/bin/bash
# ============================================================================
# tests/test_install_root_layer1.sh — Layer-1 ownership-separation HOME-shape
# tests for the shell-side install-root resolution
# (protection-plan-install-dir-20261012.md §Layer 1, 2026-10-12)
# ============================================================================
#
# THE TRAP. The live service migrates to a dedicated service user whose
# systemd unit carries Environment=HOME=<install dir> (e.g.
# HOME=/home/nea/agents-ensemble) while agents stay on the operator user.
# Every shell resolution that spelled the install root as
# $HOME/agents-ensemble would WRONGLY NEST under that shape
# (/home/nea/agents-ensemble/agents-ensemble) — breaking the live-target
# table AND, worse, BLINDING the sandbox refusal guard (a sandbox
# INSTALL_DIR pointing at the real tree would no longer match the
# canonical comparison). This pack proves BOTH shapes resolve correctly:
#
#   (a) legacy      HOME=<fake-home>            → $HOME/<name>
#   (b) post-Layer-1 HOME=<fake-home>/agents-ensemble(-demo) → HOME itself
#       (+ the sibling <name> next door for cross-env resolution)
#
# Scope: scripts/upgrade/lib.sh (_install_dir_candidates /
# _resolve_install_root / _is_canonical_install_dir / resolve_env sandbox
# refusal + live-port cross-check), scripts/deploy.sh
# (_deploy_install_root mirror), scripts/stop-ensemble.sh
# (_default_install_dir mirror).
#
# SANDBOX discipline: zero side effects outside mktemp dirs; HOME is always
# overridden to a fixture dir, so no real install dir can ever resolve.
# Portability: bash 3.2 / BSD-safe (mktemp -d, basename/dirname only).
#
#   bash tests/test_install_root_layer1.sh
# ============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$REPO_ROOT/scripts/upgrade/lib.sh"
DEPLOY="$REPO_ROOT/scripts/deploy.sh"
STOP="$REPO_ROOT/scripts/stop-ensemble.sh"

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

# ─── fixture ────────────────────────────────────────────────────────────────
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/layer1-roots.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
LEGACY_HOME="$FIXTURE/fake-home"           # shape (a): operator user
LAYER1_LIVE_HOME="$FIXTURE/agents-ensemble"     # shape (b): live service user
LAYER1_DEMO_HOME="$FIXTURE/agents-ensemble-demo" # shape (b): demo service user
mkdir -p "$LEGACY_HOME" "$LAYER1_LIVE_HOME" "$LAYER1_DEMO_HOME"

cleanup() { rm -rf "$FIXTURE"; }
trap cleanup EXIT INT TERM

# Source ONLY the pure helpers under test (lib.sh top level is constants +
# function defs only — verified — but pin HOME to the fixture anyway; the
# resolve_env refusals run in their own subshells below).
# shellcheck source=scripts/upgrade/lib.sh
HOME="$LEGACY_HOME"
. "$LIB"
HOME="$LEGACY_HOME"
export HOME

# ─── _install_dir_candidates ────────────────────────────────────────────────
section "_install_dir_candidates — both HOME shapes"

out="$(HOME="$LEGACY_HOME" _install_dir_candidates agents-ensemble | tr '\n' '|')"
assert_eq "legacy live candidates" "$LEGACY_HOME/agents-ensemble|" "$out"

out="$(HOME="$LEGACY_HOME" _install_dir_candidates agents-ensemble-demo | tr '\n' '|')"
assert_eq "legacy demo candidates" "$LEGACY_HOME/agents-ensemble-demo|" "$out"

out="$(HOME="$LAYER1_LIVE_HOME" _install_dir_candidates agents-ensemble | tr '\n' '|')"
assert_eq "layer1 live: HOME-self first, then nested spelling" \
    "$LAYER1_LIVE_HOME|$LAYER1_LIVE_HOME/agents-ensemble|" "$out"

out="$(HOME="$LAYER1_DEMO_HOME" _install_dir_candidates agents-ensemble-demo | tr '\n' '|')"
assert_eq "layer1 demo: HOME-self first, then nested spelling" \
    "$LAYER1_DEMO_HOME|$LAYER1_DEMO_HOME/agents-ensemble-demo|" "$out"

out="$(HOME="$LAYER1_LIVE_HOME" _install_dir_candidates agents-ensemble-demo | tr '\n' '|')"
assert_eq "layer1 live HOME resolving demo: nested then sibling next door" \
    "$LAYER1_LIVE_HOME/agents-ensemble-demo|$FIXTURE/agents-ensemble-demo|" "$out"

# ─── _resolve_install_root ──────────────────────────────────────────────────
section "_resolve_install_root — verified pick + legacy fallback"

# Nothing exists → legacy fallback spelling (historical behavior verbatim).
out="$(HOME="$LEGACY_HOME" _resolve_install_root agents-ensemble)"
assert_eq "legacy fallback when nothing exists" \
    "$LEGACY_HOME/agents-ensemble" "$out"

# Post-flip live: .env at HOME-self → HOME itself, never nested.
mkdir -p "$LAYER1_LIVE_HOME/releases"
printf 'POSTGRES_DB=ensemble_prod\nPORT=9797\n' > "$LAYER1_LIVE_HOME/.env"
out="$(HOME="$LAYER1_LIVE_HOME" _resolve_install_root agents-ensemble)"
assert_eq "layer1 live resolves HOME-self" "$LAYER1_LIVE_HOME" "$out"

# Post-flip live resolving demo via the sibling next door (verified .env).
printf 'POSTGRES_DB=ensemble_demo\nPORT=7979\n' > "$LAYER1_DEMO_HOME/.env"
out="$(HOME="$LAYER1_LIVE_HOME" _resolve_install_root agents-ensemble-demo)"
assert_eq "layer1 live resolves demo sibling next door" \
    "$LAYER1_DEMO_HOME" "$out"

# Legacy live verified.
mkdir -p "$LEGACY_HOME/agents-ensemble/releases"
printf 'POSTGRES_DB=ensemble_prod\nPORT=9797\n' > "$LEGACY_HOME/agents-ensemble/.env"
out="$(HOME="$LEGACY_HOME" _resolve_install_root agents-ensemble)"
assert_eq "legacy live verified" "$LEGACY_HOME/agents-ensemble" "$out"

# ─── resolve_env live — the refusal-check lane ─────────────────────────────
section "resolve_env live — INSTALL_DIR + PORT under both shapes"

rv="$(HOME="$LAYER1_LIVE_HOME" TARGET= UP_TARGET= INSTALL_DIR= PORT= POSTGRES_DB= \
    bash -c ". '$LIB'; resolve_env live; printf 'rc=%s dir=%s port=%s' \"\$?\" \"\$INSTALL_DIR\" \"\$PORT\"" 2>/dev/null)"
assert_eq "layer1 live resolve_env" \
    "rc=0 dir=$LAYER1_LIVE_HOME port=9797" "$rv"

rv="$(HOME="$LEGACY_HOME" TARGET= UP_TARGET= INSTALL_DIR= PORT= POSTGRES_DB= \
    bash -c ". '$LIB'; resolve_env live; printf 'rc=%s dir=%s port=%s' \"\$?\" \"\$INSTALL_DIR\" \"\$PORT\"" 2>/dev/null)"
assert_eq "legacy live resolve_env" \
    "rc=0 dir=$LEGACY_HOME/agents-ensemble port=9797" "$rv"

# ─── resolve_env sandbox refusal — the guard that must not go blind ────────
section "resolve_env sandbox — throwaway-dir refusal under both shapes"

# (b) post-flip: HOME IS the live tree; a sandbox pointing at it MUST be
# refused. Pre-fix the nested comparison missed the real tree entirely.
out="$(HOME="$LAYER1_LIVE_HOME" INSTALL_DIR="$LAYER1_LIVE_HOME" PORT=8577 \
    bash -c ". '$LIB'; resolve_env sandbox" 2>&1)"; rc=$?
assert_eq "layer1 sandbox-at-live refused (rc 78)" "78" "$rc"
assert_contains "layer1 sandbox-at-live refusal text" \
    "must be a throwaway dir" "$out"

# (a) legacy: the historical refusal preserved verbatim.
out="$(HOME="$LEGACY_HOME" INSTALL_DIR="$LEGACY_HOME/agents-ensemble" PORT=8577 \
    bash -c ". '$LIB'; resolve_env sandbox" 2>&1)"; rc=$?
assert_eq "legacy sandbox-at-live refused (rc 78)" "78" "$rc"
assert_contains "legacy sandbox-at-live refusal text" \
    "must be a throwaway dir" "$out"

# A legitimate throwaway sandbox under the post-flip HOME still resolves.
mkdir -p "$FIXTURE/sbx"
out="$(HOME="$LAYER1_LIVE_HOME" INSTALL_DIR="$FIXTURE/sbx" PORT=8577 \
    bash -c ". '$LIB'; resolve_env sandbox; printf 'rc=%s dir=%s db=%s' \"\$?\" \"\$INSTALL_DIR\" \"\$POSTGRES_DB\"" 2>/dev/null)"; rc=$?
assert_eq "layer1 throwaway sandbox accepted" \
    "rc=0 dir=$FIXTURE/sbx db=ensemble_sandbox" "$out"

# Live-port cross-check stays live under the flipped shape: sandbox PORT
# equal to the live .env PORT (parsed from HOME-self/.env) must refuse.
out="$(HOME="$LAYER1_LIVE_HOME" INSTALL_DIR="$FIXTURE/sbx" PORT=9797 \
    bash -c ". '$LIB'; resolve_env sandbox" 2>&1)"; rc=$?
assert_eq "layer1 live-port collision refused (rc 78)" "78" "$rc"
assert_contains "layer1 live-port collision refusal text" \
    "equals the LIVE install's staged port" "$out"

# ─── deploy.sh mirror (_deploy_install_root) ───────────────────────────────
section "deploy.sh — _deploy_install_root mirror (both shapes)"

DEPLOY_FN="$(sed -n '/^_deploy_install_root()/,/^}/p' "$DEPLOY")"
[ -n "$DEPLOY_FN" ] && _pass || _fail "deploy mirror extractable"

eval "$DEPLOY_FN"
out="$(HOME="$LEGACY_HOME" _deploy_install_root agents-ensemble)"
assert_eq "deploy mirror legacy" "$LEGACY_HOME/agents-ensemble" "$out"
out="$(HOME="$LEGACY_HOME" _deploy_install_root agents-ensemble-demo)"
assert_eq "deploy mirror legacy demo" "$LEGACY_HOME/agents-ensemble-demo" "$out"
out="$(HOME="$LAYER1_LIVE_HOME" _deploy_install_root agents-ensemble)"
assert_eq "deploy mirror layer1 live → HOME itself" "$LAYER1_LIVE_HOME" "$out"
out="$(HOME="$LAYER1_LIVE_HOME" _deploy_install_root agents-ensemble-demo)"
assert_eq "deploy mirror layer1 live → demo sibling" "$LAYER1_DEMO_HOME" "$out"
out="$(HOME="$LAYER1_DEMO_HOME" _deploy_install_root agents-ensemble-demo)"
assert_eq "deploy mirror layer1 demo → HOME itself" "$LAYER1_DEMO_HOME" "$out"

# ─── stop-ensemble.sh mirror (_default_install_dir) ────────────────────────
section "stop-ensemble.sh — _default_install_dir mirror (both shapes)"

STOP_FN="$(sed -n '/^_default_install_dir()/,/^}/p' "$STOP")"
[ -n "$STOP_FN" ] && _pass || _fail "stop mirror extractable"

eval "$STOP_FN"
out="$(HOME="$LEGACY_HOME" _default_install_dir)"
assert_eq "stop default legacy" "$LEGACY_HOME/agents-ensemble" "$out"
out="$(HOME="$LAYER1_LIVE_HOME" _default_install_dir)"
assert_eq "stop default layer1 live → HOME itself" "$LAYER1_LIVE_HOME" "$out"
out="$(HOME="$LAYER1_DEMO_HOME" _default_install_dir)"
assert_eq "stop default layer1 demo → HOME itself" "$LAYER1_DEMO_HOME" "$out"

# ─── syntax gates ───────────────────────────────────────────────────────────
section "syntax gates"
bash -n "$LIB" && _pass || _fail "lib.sh bash -n"
bash -n "$DEPLOY" && _pass || _fail "deploy.sh bash -n"
bash -n "$STOP" && _pass || _fail "stop-ensemble.sh bash -n"

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n'
if [ "$FAIL" -eq 0 ]; then
    printf 'install-root layer1: PASS (%s assertions)\n' "$PASS"
    exit 0
fi
printf 'install-root layer1: FAIL — %s failed, %s passed\n' "$FAIL" "$PASS"
printf 'failed:%s\n' "$FAILED_TESTS"
exit 1
