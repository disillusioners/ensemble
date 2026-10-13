#!/bin/bash
# ============================================================================
# tests/upgrade_test_lib.sh — shared helpers for the upgrade-system packs
# ============================================================================
# M4 (2026-10-12, Batch-2 tidier): ONE canonical home for the prologue
# helpers the packs used to carry as drifted inline copies:
#   - assert_contains      (was: 4 identical copies + launcher's own)
#   - assert_not_contains  (was: 3 copies; the release pack CALLED it
#                           without ever defining it — a latent no-op
#                           assertion, fixed by this lib)
#   - fixture registry     (M1: register-at-creation + ONE EXIT trap)
#   - F1 fake-HOME fence   (was: one inline copy in test_journal_splice.sh)
#
# SOURCE-ONLY — never execute this file. Packs do:
#   . "$(dirname "${BASH_SOURCE[0]}")/upgrade_test_lib.sh"
# and then call the helpers. Nothing here sets shell options (the
# packs keep their own `set -u` discipline — forcing it from the lib
# would change results for the packs that opted out).
#
# Behavior contract: the helper bodies are byte-for-byte the union of
# the pre-lib inline copies (the one cosmetic divergence — boot_sweep's
# `_pass "$name"` vs bare `_pass` — is behavior-free: every pack's
# _pass ignores its arguments).
# ============================================================================

# ── M1 fixture registry ─────────────────────────────────────────────────────
# Every mktemp fixture dir registers here; ONE top-level EXIT trap tears
# the whole registry down. Register at creation, never replace the trap:
# a pack that needs its own EXIT teardown (e.g. adaptive_soak's orphan
# port reaping) installs its trap AFTER sourcing this lib and calls
# _cleanup_fixtures from inside it.
_FIXTURE_DIRS=""
_register_fixture() { _FIXTURE_DIRS="$_FIXTURE_DIRS $1"; }
_cleanup_fixtures() {
    local d
    for d in $_FIXTURE_DIRS; do rm -rf "$d" 2>/dev/null; done
}
trap _cleanup_fixtures EXIT

# ── assertions ──────────────────────────────────────────────────────────────
# assert_contains <name> <needle> <haystack>
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}

# assert_not_contains <name> <needle> <haystack>
assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass ;;
    esac
}

# ── F1 fence: fake-HOME live-refusal fixture ───────────────────────────────
# Pins a pack section inside a throwaway $HOME so live-install refusal
# logic can be exercised WITHOUT any real live contact. setup_fake_home
# saves TRUE_HOME, mints FAKE_HOME under /tmp, exports HOME, and
# registers the dir with the fixture registry. preflight_guard runs the
# 4-stage FENCE checks (abort loudly BEFORE any creating/destructive op
# if the export failed, the resolved target escapes /tmp, or it touches
# the real user home). teardown_fake_home restores HOME, removes
# FAKE_HOME (the registry trap would too — this restores HOME eagerly so
# later sections see the real environment).
setup_fake_home() {
    TRUE_HOME="$HOME"
    FAKE_HOME="$(mktemp -d /tmp/ens-upglib-home.XXXXXX)"
    _register_fixture "$FAKE_HOME"
    export HOME="$FAKE_HOME"
}

preflight_guard() {
    # Stage 1: the HOME export must have taken.
    if [ "$HOME" = "$TRUE_HOME" ]; then
        printf 'FATAL F1: HOME export did not take (HOME=%s == TRUE_HOME=%s) — refusing to run live-install cells\n' "$HOME" "$TRUE_HOME" >&2
        exit 1
    fi
    # Stage 2: FAKE_HOME must live under /tmp.
    case "$HOME" in
        /tmp/*) ;;
        *) printf 'FATAL F1: FAKE_HOME escapes /tmp (HOME=%s) — refusing to run live-install cells\n' "$HOME" >&2; exit 1 ;;
    esac
    # Stage 3: the RESOLVED live-install parent must stay under /tmp
    # (canonicalize via the fake root — resolves symlinks on /tmp).
    local live_parent_target
    live_parent_target="$(cd "$HOME" && pwd -P)/agents-ensemble"
    case "$live_parent_target" in
        /tmp/*) ;;
        *) printf 'FATAL F1: resolved LIVE_PARENT escapes /tmp (%s) — refusing to run live-install cells\n' "$live_parent_target" >&2; exit 1 ;;
    esac
    # Stage 4: must never touch the real user home. The pre-lib inline
    # copy pinned the literal /home/nea — kept byte-identical here on
    # purpose (generalizing to $TRUE_HOME would be a behavior change;
    # revisit only with the pack owners).
    case "$live_parent_target" in
        *"/home/nea"*) printf 'FATAL F1: resolved LIVE_PARENT touches /home/nea (%s) — refusing to run live-install cells\n' "$live_parent_target" >&2; exit 1 ;;
    esac
}

teardown_fake_home() {
    rm -rf "$FAKE_HOME"
    export HOME="$TRUE_HOME"
}
