#!/bin/bash
# ============================================================================
# scripts/install-dir-guard.sh — shared live/demo confirm gate for Makefile
# mutating targets (incident 2026-11-09, commission wi-52666630 leg 1;
# operator directive #2: cross-platform + extensible live set + sudo form)
# ============================================================================
# THE INCIDENT. `INSTALL_DIR ?= $(HOME)/agents-ensemble` made the LIVE prod
# install the DEFAULT target of every make invocation: a stray `make
# uninstall` from a worktree would `rm -rf` the live tree, and `make
# install` would kill the live daemon via scripts/stop-ensemble.sh — all
# with zero env, zero args, zero intent. This guard is the root-cause fix:
#
#   1. INERT DEFAULT — the Makefile now ships `INSTALL_DIR ?=` (empty).
#      Every mutating target runs this guard as its FIRST recipe side
#      effect; an empty INSTALL_DIR is refused with guidance. Bare `make
#      install` / `make uninstall` can never resolve to live by
#      construction: there IS no default anymore.
#   2. LIVE-CONFIRM GATE — when INSTALL_DIR (env, make arg — both reach
#      this script via the Makefile's `export INSTALL_DIR`) canonicalizes
#      to a protected install dir, the guard refuses unless the operator
#      set the per-path confirm var:
#
#        ENSEMBLE_INSTALL_LIVE=1   → confirms actions on any LIVE dir
#        ENSEMBLE_INSTALL_DEMO=1   → confirms actions on the DEMO dir
#
#      Two per-path vars, deliberately NOT interchangeable: confirming the
#      demo must never implicitly confirm live. Naming follows the repo's
#      existing per-family confirm-var convention (deploy.sh:
#      ENSEMBLE_DEPLOY_LIVE; scripts/upgrade/lib.sh: ENSEMBLE_UPGRADE_LIVE
#      — each family owns its own, kept disjoint by design; no collision
#      with the launcher leg's ENSEMBLE_LAUNCHER_PATH / ENSEMBLE_LAUNCHER_
#      STATE_FILE).
#      (2026-11-09: the DEPLOY and UPGRADE lanes additionally honor
#      ENSEMBLE_INSTALL_DEMO for their DEMO target — one var per PATH
#      (live/demo), not one per lane, so the demo confirm means the same
#      thing in every lane; their LIVE vars stay lane-owned.)
#
# PROTECTED LIVE SET (extensible, operator directive #2.2):
#   * built-ins:  $HOME/agents-ensemble  (both Layer-1 HOME shapes — below)
#                 /opt/ensemble          (Stage-1 Mac relocation target; the
#                                         same Makefile serves an Ubuntu VM
#                                         where $HOME/agents-ensemble is the
#                                         live systemd install — refusing
#                                         there is CORRECT and desired)
#   * extension:  ENSEMBLE_LIVE_INSTALL_DIRS — colon-separated extra live
#                 dirs (same syntax as PATH). This EXTENDS the built-in
#                 set; it never replaces it, so zero-config behavior is
#                 unchanged and the HOME candidates cannot be dropped by a
#                 bad override. Extension entries are exact paths (no ~ or
#                 Layer-1 expansion); each is confirmed by the same
#                 ENSEMBLE_INSTALL_LIVE=1 var.
#   The demo dir stays HOME-derived only ($HOME/agents-ensemble-demo, both
#   HOME shapes) — the one protected demo path.
#
# LAYER-1 HOME SHAPES (scripts/protection/ensemble-promote runs the
# pipeline as `env HOME="$INSTALL_DIR" bash …`, so HOME can BE the install
# dir): legacy HOME=/home/user → $HOME/<name>; Layer-1 HOME=/…/agents-
# ensemble(-demo) → $HOME itself IS that install.
#
# SUDO / ROOT (operator directive #2.1b — DECISION: the confirm var is the
# ONLY gate and it SUFFICES when the recipe runs as root). Reasoning: the
# guard verifies operator INTENT — a token a human typed in a shell — not
# privilege. euid=0 neither strengthens nor weakens intent, and privilege
# auditing here would be unenforceable theater (root can bypass any
# guard). What the guard MUST do instead is keep gating UNDER sudo: sudo
# resets HOME to the target user's home, which would silently ungate the
# operator's real live dir in exactly the scenario where accidents are
# most destructive (a root `rm -rf`). Therefore SUDO_USER's home is
# resolved (getent → dscl → /home/<user> convention, fail-closed) and its
# candidates are protected alongside $HOME's.
# Env vars do not survive plain `sudo make …` (env_reset), so the refusal
# prints the sudo-correct form that DOES work with this implementation:
#
#     sudo env ENSEMBLE_INSTALL_LIVE=1 make <target> INSTALL_DIR='…'
#
# (`sudo env VAR=… make` needs no sudoers env_keep entry; `sudo -E` would,
# so the `env` form is the one printed and tested.)
#
# CONSUMERS — the $(INSTALL_DIR)-mutating Makefile targets ONLY (operator
# directive #2.4; the deploy lane's gate lives in scripts/deploy.sh and
# the upgrade lane's in scripts/upgrade/lib.sh — both left alone):
#   install, uninstall, install-deps, stop, stop-by-port, plist-install,
#   watchdog-install (start/dev inherit the gate via their `stop`
#   prerequisite). deploy-live / deploy-demo / stage-demo and
#   upgrade-stage / promote / rollback / status are NOT this guard's
#   consumers.
#
#   bash scripts/install-dir-guard.sh --target install
#
# --require-live (OPT-IN mode, additive; incident 2026-11-09 analysis):
#   bash scripts/install-dir-guard.sh --target plist-install --require-live
#   Some targets write HOST state that the INSTALL_DIR axis cannot express:
#   plist-install / watchdog-install copy the staged plist into
#   $HOME/Library/LaunchAgents/ REGARDLESS of INSTALL_DIR — a sandbox (or
#   demo) INSTALL_DIR would silently overwrite the running prod launchd unit
#   with one pointing at the sandbox. The flag adds ONE extra rule for those
#   targets: the requested dir must classify as LIVE, and (as always) a live
#   dir additionally needs ENSEMBLE_INSTALL_LIVE=1. It is evaluated AFTER the
#   per-path gate, so the canonical live refusal still fires first for an
#   unconfirmed live dir, and ENSEMBLE_INSTALL_DEMO never opens a demo dir
#   under this mode. WITHOUT the flag the guard is byte-for-byte unchanged
#   for every existing consumer.
#
# INSTALL_DIR is read from the environment (the Makefile exports it, so
# env-set and command-line `make install INSTALL_DIR=…` both arrive — and
# the path never crosses a shell-quoting boundary at the call site).
#
# CANONICALIZATION (mirrors scripts/upgrade/lib.sh semantics exactly —
# _canon_dir / _install_dir_candidates / _is_canonical_install_dir):
#   * existing dirs resolve PHYSICALLY (cd + pwd -P): trailing slashes,
#     `//`, `./`, `..`, relative paths and SYMLINK ALIASES of a protected
#     tree all normalize onto the same string and are caught;
#   * non-existent paths compare textually after `~` expansion, slash
#     normalization and `./`-segment removal — a not-yet-created live
#     spelling (`~/agents-ensemble/`) is still refused;
#   * compared in BOTH directions (requested dir textual + physical vs
#     candidate textual + physical) so symlink aliases are caught whether
#     the alias is the requested path or the protected tree itself.
#   * the candidate set mirrors lib.sh's _install_dir_candidates: the legacy
#     shape <home>/<name>, the Layer-1 shape (HOME itself IS the install
#     when it ends with the name) and the Layer-1 SIBLING
#     (dirname(<home>)/<name> when <home> ends with EITHER install name —
#     cross-env resolution, lib.sh:898-899).
#
# ROOT-OR-INSIDE MATCHING (E1, 2026-11-09): a protected root AND every path
# INSIDE it (any depth) share its class — `~/agents-ensemble/data` and
# `~/agents-ensemble/data/deep/x` are LIVE and refuse exactly like the root
# does (`rm -rf $HOME/agents-ensemble/data` destroys the live install's
# data). The match is PATH-COMPONENT aware: a NAME-PREFIX SIBLING
# (`~/agents-ensemble-backup`, `~/agents-ensembleX`) is a SEPARATE tree and
# stays allowed BY DESIGN — only the tree INSIDE a protected root is gated,
# never a neighbour whose name merely starts with the same prefix.
# This only ever ADDS refusals: a path allowed before stays allowed unless
# it is inside a protected root or a Layer-1 sibling.
#
# CONTRACT: exit 78 (the repo's config-refuse code — same as deploy.sh's
# unconfirmed-live refusal) BEFORE any side effect, with the target name,
# the resolved path, the exact confirm var, plain + sudo command forms and
# the sanctioned lanes in the message. Exit 0 silently on pass (targets
# print their own banners). Bash 3.2 / BSD-safe; never creates, removes
# or writes anything.
# ============================================================================

set -u

usage() {
    echo "usage: install-dir-guard.sh --target <make-target> [--require-live]  (INSTALL_DIR via env)" >&2
}

TARGET=""
REQUIRE_LIVE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --target)
            [ $# -ge 2 ] || { usage; exit 78; }
            TARGET="$2"; shift 2 ;;
        --require-live)
            REQUIRE_LIVE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "install-dir-guard: unknown arg '$1'" >&2; usage; exit 78 ;;
    esac
done

refuse() {
    # refuse <first-line>… — 78, message to stderr
    printf 'make %s: REFUSING — %s\n' "${TARGET:-<unknown>}" "$1" >&2
    shift
    printf '%s\n' "$@" >&2
    exit 78
}

INSTALL_DIR_ENV="${INSTALL_DIR:-}"

# ── 1. inert default: no INSTALL_DIR at all ──────────────────────────────────
if [ -z "$INSTALL_DIR_ENV" ]; then
    cat >&2 <<EOF
make ${TARGET:-<unknown>}: REFUSING — INSTALL_DIR is not set.
  The Makefile no longer assumes an install dir: the old default
  \$(HOME)/agents-ensemble IS the live production install, and assuming it
  by default is what let a stray make invocation hit live
  (incident 2026-11-09).
  Pass an explicit dir:  make ${TARGET:-<unknown>} INSTALL_DIR=/path/to/install
  Protected live dirs (\$HOME/agents-ensemble, /opt/ensemble, plus any
  ENSEMBLE_LIVE_INSTALL_DIRS entries) additionally require
  ENSEMBLE_INSTALL_LIVE=1; the demo dir (\$HOME/agents-ensemble-demo)
  requires ENSEMBLE_INSTALL_DEMO=1.
EOF
    exit 78
fi

# ── 2. canonicalize the requested dir ────────────────────────────────────────
# The confirm var opens paths positively identified as live; it must not open paths the guard cannot canonicalize.
case "$INSTALL_DIR_ENV" in
    "~/"*)
        ;;
    "~"*)
        cat >&2 <<EOF
make ${TARGET:-<unknown>}: REFUSING — INSTALL_DIR spelling '$INSTALL_DIR_ENV' cannot be verified safely.
  Tilde spellings other than '~/' (such as '~user/...' or bare '~') cannot
  be reliably canonicalized by this guard prior to shell expansion.
  Spell the path absolutely:  make ${TARGET:-<unknown>} INSTALL_DIR=/path/to/install
  Protected live dirs (\$HOME/agents-ensemble, /opt/ensemble, plus any
  ENSEMBLE_LIVE_INSTALL_DIRS entries) additionally require
  ENSEMBLE_INSTALL_LIVE=1; the demo dir (\$HOME/agents-ensemble-demo)
  requires ENSEMBLE_INSTALL_DEMO=1.
  Under sudo (env vars do not survive plain sudo), the sudo-correct form:
    sudo env ENSEMBLE_INSTALL_LIVE=1 make ${TARGET:-<unknown>} INSTALL_DIR=/path/to/install
  Sanctioned lanes (prefer these):
    deploy :  ENSEMBLE_DEPLOY_LIVE=1 scripts/deploy.sh live
    upgrade:  ENSEMBLE_UPGRADE_LIVE=1 scripts/upgrade/promote.sh live --f2-verified-closed
EOF
        exit 78
        ;;
esac

# Textual pre-normalization: expand a leading `~/`, collapse `//`, strip
# trailing slashes, drop `./` segments (bash 3.2-safe; `..` is left for the
# physical pass — any existing dir resolves through cd+pwd -P below).
_norm() {
    local p="$1"
    case "$p" in
        "~/"*) p="$HOME${p#\~}" ;;
    esac
    p="$(printf '%s' "$p" | sed -e 's://*:/:g' -e 's:/*$::')"
    while :; do
        case "$p" in
            *"/./"*) p="${p%%/./*}/${p#*/./}" ;;
            "./"*)   p="${p#./}" ;;
            *) break ;;
        esac
    done
    printf '%s' "$p"
}
# Physical form of an EXISTING path (cd + pwd -P, mirrors lib.sh _canon_dir);
# falls back to the (normalized) raw string when the path does not exist.
_canon_dir() {
    (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"
}

DIR_NORM="$(_norm "$INSTALL_DIR_ENV")"
DIR_CANON="$(_canon_dir "$DIR_NORM")"

# ── 3. homes under protection: $HOME + SUDO_USER's real home ─────────────────
# Under sudo, HOME points at the TARGET user's home (env_reset), which
# would silently ungate the operator's real live dir — see the SUDO/ROOT
# decision block above. Resolve SUDO_USER's home and protect its candidates
# too. Resolution: getent (Linux) → dscl (macOS) → /home/<user> (Linux
# convention; fail-CLOSED: a wrong guess only widens the protected set).
_sudo_user_home() {
    [ -n "${SUDO_USER:-}" ] || return 1
    case "$SUDO_USER" in root) return 1 ;; esac
    local cand=""
    if command -v getent >/dev/null 2>&1; then
        cand="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6 | head -1)"
    fi
    if [ -z "$cand" ] && command -v dscl >/dev/null 2>&1; then
        cand="$(dscl -q . -read "/Users/$SUDO_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}' | head -1)"
    fi
    if [ -z "$cand" ]; then
        cand="/home/$SUDO_USER"
    fi
    printf '%s' "$cand"
}
HOME_ALT="$(_sudo_user_home)"

# ── 4. classify the requested dir against the protected set ──────────────────
# _cand_hit <want> <cand> — true when want IS cand or lies INSIDE it.
# Both spellings are compared (normalized + physical): the physical form
# catches a SYMLINK ALIAS pointing INTO the protected tree, the textual form
# catches a not-yet-created spelling. The containment test is PATH-COMPONENT
# aware ("cand" + "/"): a name-prefix sibling ("…/agents-ensemble-backup")
# is NOT inside the protected root.
_cand_hit() {
    local want="$1" cand="$2" cn cc
    [ -n "$cand" ] || return 1
    cn="$(_norm "$cand")"
    cc="$(_canon_dir "$cn")"
    [ "$want" = "$cn" ] && return 0
    [ "$want" = "$cc" ] && return 0
    case "$want" in
        "$cn"/*) return 0 ;;
        "$cc"/*) return 0 ;;
    esac
    return 1
}

# _cand_class <normalized-or-physical-path> — echoes live|demo when the path
# IS a protected install candidate for ANY protected home (textually or
# physically) OR lies INSIDE one (any depth); empty output otherwise.
# The candidate set mirrors scripts/upgrade/lib.sh:902-917
# (_install_dir_candidates): the legacy shape <home>/<name>, the Layer-1
# shape (HOME itself IS the install when it ends with the name) and the
# Layer-1 SIBLING (dirname(<home>)/<name> when <home> ends with EITHER
# install name — cross-env resolution, lib.sh:898-899).
_cand_class() {
    local want="$1" h base name cand sib
    for h in "$HOME" "$HOME_ALT"; do
        [ -n "$h" ] || continue
        base="$(basename "$h")"
        for name in agents-ensemble agents-ensemble-demo; do
            # legacy shape: <home>/<name>
            cand="$h/$name"
            if _cand_hit "$want" "$cand"; then
                case "$name" in
                    agents-ensemble)      printf 'live\n' ;;
                    agents-ensemble-demo) printf 'demo\n' ;;
                esac
                return 0
            fi
            # Layer-1 shape: home ends with the name → home itself IS it
            if [ "$base" = "$name" ]; then
                if _cand_hit "$want" "$h"; then
                    case "$name" in
                        agents-ensemble)      printf 'live\n' ;;
                        agents-ensemble-demo) printf 'demo\n' ;;
                    esac
                    return 0
                fi
            fi
            # Layer-1 SIBLING shape (lib.sh:898-899, :907-914): when <home>
            # ends with EITHER install name, the cross-env sibling is a
            # candidate too — HOME=…/agents-ensemble-demo ⇒ …/agents-ensemble
            # is a LIVE candidate, and vice versa.
            case "$base" in
                agents-ensemble|agents-ensemble-demo)
                    if [ "$base" != "$name" ]; then
                        sib="$(dirname "$h")/$name"
                        if _cand_hit "$want" "$sib"; then
                            case "$name" in
                                agents-ensemble)      printf 'live\n' ;;
                                agents-ensemble-demo) printf 'demo\n' ;;
                            esac
                            return 0
                        fi
                    fi
                    ;;
            esac
        done
    done
    # Built-in non-HOME live dirs + the extensible set (colon-separated,
    # exact paths, live class only).
    for ext in /opt/ensemble "$(printf '%s' "${ENSEMBLE_LIVE_INSTALL_DIRS:-}" | tr ':' '\n')"; do
        [ -n "$ext" ] || continue
        if _cand_hit "$want" "$ext"; then
            printf 'live\n'
            return 0
        fi
    done
    return 1
}

# Compare BOTH forms of the requested dir (physical first — it is the one
# that catches symlink aliases; then textual — it catches protected-path
# spellings that do not exist yet).
CLASS="$(_cand_class "$DIR_CANON")"
if [ -z "$CLASS" ]; then
    CLASS="$(_cand_class "$DIR_NORM")"
fi

# ── 5. per-path confirm gate ────────────────────────────────────────────────
case "$CLASS" in
    live)
        if [ "${ENSEMBLE_INSTALL_LIVE:-0}" != "1" ]; then
            cat >&2 <<EOF
make ${TARGET:-<unknown>}: REFUSING — INSTALL_DIR resolves to a LIVE install dir ($DIR_CANON).
  Live installs are the running production orchestrator; mutating them by
  accident is the incident class this guard exists for (2026-11-09).
  Protected live set: \$HOME/agents-ensemble, /opt/ensemble, plus any
  ENSEMBLE_LIVE_INSTALL_DIRS entries (colon-separated).
  To proceed deliberately:
    ENSEMBLE_INSTALL_LIVE=1 make ${TARGET:-<unknown>} INSTALL_DIR='$INSTALL_DIR_ENV'
  Under sudo (env vars do not survive plain sudo), the sudo-correct form:
    sudo env ENSEMBLE_INSTALL_LIVE=1 make ${TARGET:-<unknown>} INSTALL_DIR='$INSTALL_DIR_ENV'
  Sanctioned lanes (prefer these):
    deploy :  ENSEMBLE_DEPLOY_LIVE=1 scripts/deploy.sh live
    upgrade:  ENSEMBLE_UPGRADE_LIVE=1 scripts/upgrade/promote.sh live --f2-verified-closed
EOF
            exit 78
        fi
        ;;
    demo)
        if [ "${ENSEMBLE_INSTALL_DEMO:-0}" != "1" ]; then
            cat >&2 <<EOF
make ${TARGET:-<unknown>}: REFUSING — INSTALL_DIR resolves to the DEMO install ($DIR_CANON).
  Demo mutations are gated behind an explicit confirm (same incident class).
  To proceed deliberately:
    ENSEMBLE_INSTALL_DEMO=1 make ${TARGET:-<unknown>} INSTALL_DIR='$INSTALL_DIR_ENV'
  Under sudo (env vars do not survive plain sudo), the sudo-correct form:
    sudo env ENSEMBLE_INSTALL_DEMO=1 make ${TARGET:-<unknown>} INSTALL_DIR='$INSTALL_DIR_ENV'
  Sanctioned demo-deploy lane (prefer this): scripts/deploy.sh demo
EOF
            exit 78
        fi
        ;;
    *)
        # Not a protected dir — proceed silently; the target's own recipe
        # prints its banners.
        ;;
esac

# ── 6. --require-live mode (opt-in; additive) ───────────────────────────────
# The plist-install / watchdog-install targets write HOST launchd state
# (${HOME}/Library/LaunchAgents/ensemble-prod.plist): the prod-named unit must
# point at the LIVE install, never at a sandbox or demo tree — a sandbox
# INSTALL_DIR would silently overwrite the running prod unit with one that
# points at the sandbox (the dir axis alone cannot express that: the host
# write happens regardless of INSTALL_DIR).
#
# Evaluated AFTER the per-path gate above, so the canonical live refusal
# (ENSEMBLE_INSTALL_LIVE) still fires FIRST for an unconfirmed live dir, and
# ENSEMBLE_INSTALL_DEMO never opens a demo dir under this mode. Without the
# flag the guard behaves exactly as before (REQUIRE_LIVE=0 → no effect).
if [ "$REQUIRE_LIVE" = "1" ] && [ "$CLASS" != "live" ]; then
    cat >&2 <<EOF
make ${TARGET:-<unknown>}: REFUSING — INSTALL_DIR does not resolve to a LIVE install dir (${DIR_CANON}).
  This target writes the HOST launchd unit
  (${HOME}/Library/LaunchAgents/ensemble-prod.plist) — the prod unit must point
  at the live install, never at a sandbox or demo tree (a sandbox INSTALL_DIR
  would silently overwrite the running prod unit).
  To install the prod launchd unit:
    ENSEMBLE_INSTALL_LIVE=1 make ${TARGET:-<unknown>} INSTALL_DIR='${INSTALL_DIR_ENV}'
  Under sudo (env vars do not survive plain sudo), the sudo-correct form:
    sudo env ENSEMBLE_INSTALL_LIVE=1 make ${TARGET:-<unknown>} INSTALL_DIR='${INSTALL_DIR_ENV}'
  The demo launchd agent is a hand-duplicate with its own Label
  (com.ensemble.watchdog.demo) — see the Makefile's demo-usage note.
EOF
    exit 78
fi

exit 0
