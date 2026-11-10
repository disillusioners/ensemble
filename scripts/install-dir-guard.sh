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
# with zero env, zero args, zero intent. This guard is the root-cause fix.
#
# ARMS — the refusal arms in the ORDER THEY FIRE (execution order). The
# labels below are NOT code section numbers: the code's own `── N.` headers
# are a SEPARATE, file-layout index, and each arm names the code section it
# lives in wherever it points at one. Every refusal exits 78 BEFORE any side
# effect; the guard never creates, removes or writes anything. (The last
# entry is not a refusal — it is the canonicalization the classification
# depends on, and it runs earlier, inside the classification.)
#
#   ONE MESSAGE SHAPE: every refusal goes through _refuse_unverifiable() — or
#   an inline heredoc reproducing the same first-line shape AND the same
#   guidance block — so a second message shape is never introduced.
#
#   * INERT DEFAULT — INSTALL_DIR unset/empty → refuse with guidance.
#     There is no default install dir anymore, so bare `make install` /
#     `make uninstall` can never resolve to live by construction.
#     (code section 0)
#
#   * ENVIRONMENT INTEGRITY — HOME unset/empty → refuse. The protected set
#     is derived from $HOME (and SUDO_USER's home); an unset/empty HOME
#     silently shrank that set (F-02) so the live dir stopped being
#     protected and the guard exited 0. Every environment value this script
#     reads is captured up front with ${var:-}: `set -u` aborts only the
#     SUBSHELL that touches an unbound name, and this script deliberately
#     runs WITHOUT `set -e`, so the parent would carry on with an empty
#     value — a silent fail-open.
#     (code section 1)
#
#   * UNVERIFIABLE SPELLING — STRUCTURAL: no confirm var opens it.
#     (a) tilde spellings other than '~/' (leg 1 behavior, unchanged).
#     (b) RAW VALUE REFUSAL (F-01) — the raw INSTALL_DIR must be exactly
#         what the guard verified: no whitespace, no quotes, no shell
#         metacharacters ( ; | & $ ` < > ( ) ), no glob characters, and no
#         difference from its normalized spelling. `make` substitutes
#         $(INSTALL_DIR) TEXTUALLY into the recipe and the shell then
#         re-parses it: a trailing space is stripped by word splitting
#         (the recipe deletes the live tree) and ';' / '|' are true command
#         separators (arbitrary commands run). The guard compares the
#         NORMALIZED path while the recipe consumes the RAW one, so a
#         value the guard cannot verify must never reach the recipe.
#     (code section 2)
#
#   * ABSOLUTE-PATH ARM — STRUCTURAL: no confirm var opens it. INSTALL_DIR
#     must be an ABSOLUTE path (one that starts with '/'). A relative path
#     resolves against whatever directory the recipe happens to run in, so
#     the path the guard checks and the path a recipe would delete can be
#     two DIFFERENT paths: '..' run from a repo root is the repo's parent,
#     'agents' run from a repo root is that repo's own agents/ tree. The
#     arm judges the TILDE-EXPANDED form, so '~/x' (expanded to $HOME/x
#     before the guard sees it) stays absolute and is NOT refused here; '~'
#     and '~user/...' never reach this arm (the UNVERIFIABLE SPELLING arm
#     refuses them first). It runs BEFORE the live/demo classification (code
#     sections 7-8) and before the containment arm (code section 9), so no
#     confirm var can ever open a relative path.
#     (code section 3b)
#
#   * LIVE/DEMO CONFIRM GATE — when the requested dir canonicalizes to a
#     protected install dir, the guard refuses unless the operator set the
#     per-path confirm var:
#       ENSEMBLE_INSTALL_LIVE=1   → confirms actions on any LIVE dir
#       ENSEMBLE_INSTALL_DEMO=1   → confirms actions on the DEMO dir
#
#     Two per-path vars, deliberately NOT interchangeable: confirming the
#     demo must never implicitly confirm live. Naming follows the repo's
#     existing per-family confirm-var convention (deploy.sh:
#     ENSEMBLE_DEPLOY_LIVE; scripts/upgrade/lib.sh: ENSEMBLE_UPGRADE_LIVE
#     — each family owns its own, kept disjoint by design; no collision
#     with the launcher leg's ENSEMBLE_LAUNCHER_PATH / ENSEMBLE_LAUNCHER_
#     STATE_FILE).
#     (code sections 7-8)
#
#   * CONTAINMENT ARM — STRUCTURAL: no confirm var opens it. The target
#     must not BE a protected home, must not be an ANCESTOR of a protected
#     install dir (e.g. $HOME itself, /opt, /) and must not be INSIDE one
#     (e.g. <live>/data) — F-04. `rm -rf` of an ancestor destroys the
#     protected tree; `rm -rf` of a descendant is the same incident one
#     level down. The classification (code sections 7-8) runs FIRST, so a
#     live/demo dir itself is still gated by its confirm var (not by this
#     arm).
#     (code section 9)
#
#   * CANONICALIZATION — case-folded comparison on case-insensitive
#     filesystems (Darwin/APFS) and LONGEST-EXISTING-PREFIX physical
#     resolution. F-05: bash's BUILTIN `pwd -P` echoes the TYPED casing
#     (/USERS/… compared unequal to the real tree), so the absolute
#     /bin/pwd -P is preferred; and a path whose intermediate components do
#     not exist (<home>/ghost/../live) is resolved through its deepest
#     existing ancestor — physically resolved, remaining segments
#     re-appended, '..' folded lexically — so it compares correctly
#     against the protected set.
#     (code section 3)
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
#   * Layer-1 SIBLING (cross-env, lib.sh:898-899): when HOME ends with
#                 EITHER install name, the sibling dir (dirname(HOME)/<name>)
#                 is a candidate too — HOME=…/agents-ensemble-demo makes
#                 …/agents-ensemble a LIVE candidate, and vice versa. Entries are split on ':',
#                 trimmed of surrounding whitespace, empty entries skipped,
#                 globbing disabled (an entry containing '*' cannot expand).
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
# --require-live (OPT-IN mode, additive): some targets write HOST state that
#   the INSTALL_DIR axis cannot express (plist-install / watchdog-install
#   copy the staged plist into $HOME/Library/LaunchAgents REGARDLESS of
#   INSTALL_DIR). The flag adds ONE extra rule: the requested dir must
#   classify as LIVE (and a live dir still needs ENSEMBLE_INSTALL_LIVE=1).
#   It is evaluated AFTER the per-path gate and the containment arm, so the
#   canonical live refusal and the structural containment refusal still fire
#   first; ENSEMBLE_INSTALL_DEMO never opens a demo dir under this mode.
#   Without the flag the guard is byte-for-byte unchanged for every existing
#   consumer.
#
# INSTALL_DIR is read from the environment (the Makefile exports it, so
# env-set and command-line `make install INSTALL_DIR=…` both arrive — and
# the path never crosses a shell-quoting boundary at the call site).
#
# CANONICALIZATION (mirrors scripts/upgrade/lib.sh semantics for the
# existing-shape comparisons — _canon_dir / _install_dir_candidates /
# _is_canonical_install_dir — and extends them for F-05):
#   * existing dirs resolve PHYSICALLY (cd + /bin/pwd -P): trailing slashes,
#     `//`, `./`, `..`, relative paths and SYMLINK ALIASES of a protected
#     tree all normalize onto the same string and are caught;
#   * non-existent paths resolve through their LONGEST EXISTING PREFIX:
#     the deepest existing ancestor is resolved physically, the remaining
#     segments are re-appended and '..' is folded lexically. A
#     not-yet-created live spelling (~/agents-ensemble/) is still refused,
#     and a not-yet-created INTERMEDIATE (~/ghost/../agents-ensemble) can
#     no longer slip past as an unrelated string;
#   * on a case-insensitive filesystem (Darwin) the comparison is
#     case-folded, so /USERS/…/AGENTS-ENSEMBLE matches the real tree;
#   * compared in BOTH directions (requested dir textual + physical vs
#     candidate textual + physical) so symlink aliases are caught whether
#     the alias is the requested path or the protected tree itself.
#
# CONTRACT: exit 78 (the repo's config-refuse code — same as deploy.sh's
# unconfirmed-live refusal) BEFORE any side effect, with the target name,
# the resolved path, the exact confirm var, plain + sudo command forms and
# the sanctioned lanes in the message. Exit 0 silently on pass (targets
# print their own banners). Bash 3.2 / BSD-safe; never creates, removes
# or writes anything.
# ============================================================================

set -u

# ── environment capture (set -u safety — see the ENVIRONMENT INTEGRITY arm
#    in the header) ───────────────────────────────────────────────────────────
# Every variable this script reads from the environment is bound HERE, before
# any logic runs. `set -u` aborts only the subshell that touches an unbound
# name; with no `set -e` the parent would continue with an empty value.
# Capture + refuse explicitly is the fail-CLOSED alternative.
HOME="${HOME:-}"
SUDO_USER="${SUDO_USER:-}"
ENSEMBLE_LIVE_INSTALL_DIRS="${ENSEMBLE_LIVE_INSTALL_DIRS:-}"
ENSEMBLE_INSTALL_LIVE="${ENSEMBLE_INSTALL_LIVE:-0}"
ENSEMBLE_INSTALL_DEMO="${ENSEMBLE_INSTALL_DEMO:-0}"

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

_refuse_unverifiable() {
    # _refuse_unverifiable <detail line>… — 78, message to stderr.
    # The ONE "the spelling cannot be verified" refusal: shared by the
    # tilde arm (2a) and the raw-value arm (2b), so both keep the same
    # first-line shape and the same guidance block.
    local detail
    printf 'make %s: REFUSING — INSTALL_DIR spelling '"'"'%s'"'"' cannot be verified safely.\n' \
        "${TARGET:-<unknown>}" "$(_display_spelling "$INSTALL_DIR_ENV")" >&2
    for detail in "$@"; do
        printf '  %s\n' "$detail" >&2
    done
    cat >&2 <<EOF
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
}

_display_spelling() {
    # Render a spelling for the message without letting a control character
    # break the one-line first-line shape.
    printf '%s' "$1" | tr -c '[:print:]' '?'
}

INSTALL_DIR_ENV="${INSTALL_DIR:-}"

# ── 0. inert default: no INSTALL_DIR at all ──────────────────────────────────
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

# ── 1. environment integrity: HOME must be known (F-02) ──────────────────────
# The protected set is HOME-derived. An unset/empty HOME does not merely
# "widen" the guard — it SILENTLY SHRINKS the protected set: the $HOME
# candidates vanish, the live dir is no longer classified, and the script
# falls through to the "not a protected dir — proceed" branch and exits 0.
# Fail CLOSED instead.
if [ -z "$HOME" ]; then
    cat >&2 <<EOF
make ${TARGET:-<unknown>}: REFUSING — HOME is unset or empty, so the protected install set cannot be determined.
  The protected live/demo set is derived from \$HOME (and SUDO_USER's home).
  With HOME unset the \$HOME candidates would be silently dropped, the
  protected set would shrink, and the live install would no longer be
  recognised — the guard would exit 0 and let the recipe run.
  Set HOME to the operator's home and retry:
    HOME=/Users/<you> make ${TARGET:-<unknown>} INSTALL_DIR=/path/to/install
  Under sudo (env vars do not survive plain sudo), the sudo-correct form:
    sudo env HOME=/Users/<you> ENSEMBLE_INSTALL_LIVE=1 make ${TARGET:-<unknown>} INSTALL_DIR=/path/to/install
  Sanctioned lanes (prefer these):
    deploy :  ENSEMBLE_DEPLOY_LIVE=1 scripts/deploy.sh live
    upgrade:  ENSEMBLE_UPGRADE_LIVE=1 scripts/upgrade/promote.sh live --f2-verified-closed
EOF
    exit 78
fi

# ── 2. unverifiable spelling — STRUCTURAL (no confirm var opens it) ─────────
# 2a. tilde spellings other than '~/' cannot be canonicalized prior to
#     shell expansion.
case "$INSTALL_DIR_ENV" in
    "~/"*)
        ;;
    "~"*)
        _refuse_unverifiable \
            "Tilde spellings other than '~/' (such as '~user/...' or bare '~') cannot" \
            "be reliably canonicalized by this guard prior to shell expansion."
        ;;
esac

# 2b. RAW VALUE REFUSAL (F-01). `make` substitutes $(INSTALL_DIR) TEXTUALLY
#     into the recipe and the shell re-parses it, so the recipe sees a
#     different string than the guard verified:
#       * a trailing space is stripped by word splitting → `rm -rf` hits the
#         live tree the guard thought was a different path;
#       * ';' / '|' / '&' are true command separators → arbitrary commands
#         run inside a make recipe;
#       * quotes, '$', '`', '<', '>', '(' , ')' and glob characters change
#         the parse again.
#     The guard compares the NORMALIZED path; the recipe consumes the RAW
#     one. A value that differs from its normalized form, or that carries
#     characters a shell would re-parse, cannot be verified — so it is
#     refused STRUCTURALLY: no confirm var opens it.
_norm_spelling() {
    # Textual normalization WITHOUT tilde expansion — the raw-form
    # comparison must treat the supported '~/' prefix as spelled.
    local p="$1"
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

if [ "$(_norm_spelling "$INSTALL_DIR_ENV")" != "$INSTALL_DIR_ENV" ]; then
    _refuse_unverifiable \
        "The path is not in normalized form (redundant '//', a trailing slash, or" \
        "'./' segments): the guard verifies the normalized spelling while the make" \
        "recipe consumes the raw value, so the two would not be the same path."
fi

# Character class: everything except a conservative path alphabet is refused.
# The leading '~' of a supported '~/' spelling is checked separately above.
_RAW_CHECK="$INSTALL_DIR_ENV"
case "$_RAW_CHECK" in
    "~/"*) _RAW_CHECK="${_RAW_CHECK#\~}" ;;
esac
case "$_RAW_CHECK" in
    *[!A-Za-z0-9_./+=:@%^,-]*)
        _refuse_unverifiable \
            "The path contains whitespace, quotes, shell metacharacters" \
            "( ; | & \$ \` < > ( ) ), glob characters or other characters a" \
            "shell would re-parse. make expands the value into the recipe" \
            "textually, so the recipe could run more than the path you named."
        ;;
esac

# ── 3. canonicalize the requested dir ────────────────────────────────────────
# The confirm var opens paths positively identified as live; it must not open paths the guard cannot canonicalize.
_norm() {
    local p="$1"
    case "$p" in
        "~/"*) p="$HOME${p#\~}" ;;
    esac
    _norm_spelling "$p"
}

# Physical form of a path: the deepest EXISTING ancestor is resolved
# physically (cd + /bin/pwd -P), the remaining segments are re-appended and
# '..' is folded lexically. Mirrors scripts/upgrade/lib.sh _canon_dir for
# existing paths and extends it for paths whose intermediates do not exist
# (F-05: <home>/ghost/../live used to compare as an unrelated string).
_pwd_phys() {
    # /bin/pwd -P (BSD) is preferred over bash's builtin `pwd -P`, which
    # echoes the TYPED casing on a case-insensitive filesystem.
    if [ -x /bin/pwd ]; then
        /bin/pwd -P 2>/dev/null
    else
        pwd -P
    fi
}

_fold_dots() {
    # Lexically fold '.' and '..' segments of an absolute path.
    local p="$1" seg stack="" oldifs="$IFS"
    IFS='/'
    set -f
    for seg in $p; do
        case "$seg" in
            ''|'.') continue ;;
            '..')
                case "$stack" in
                    */*) stack="${stack%/*}" ;;
                    *) stack="" ;;
                esac
                ;;
            *) stack="$stack/$seg" ;;
        esac
    done
    set +f
    IFS="$oldifs"
    if [ -z "$stack" ]; then
        printf '/'
    else
        printf '%s' "$stack"
    fi
}

_canon_dir() {
    local p="$1" rest="" cur="" base out=""
    case "$p" in
        /*) ;;
        '') printf '%s' "$p"; return 0 ;;
        *) p="$( (cd . 2>/dev/null && _pwd_phys) )/$p" ;;
    esac
    cur="$p"
    while [ -n "$cur" ]; do
        if [ -e "$cur" ]; then break; fi
        case "$cur" in
            /) cur=""; break ;;
        esac
        rest="/$(basename "$cur")$rest"
        cur="$(dirname "$cur")"
    done
    if [ -z "$cur" ]; then
        # nothing on the path exists: lexical fold of the whole path
        _fold_dots "$p"
        return 0
    fi
    base="$( (cd "$cur" 2>/dev/null && _pwd_phys) )"
    [ -n "$base" ] || base="$cur"
    out="$(_fold_dots "$base$rest")"
    printf '%s' "$out"
}

DIR_NORM="$(_norm "$INSTALL_DIR_ENV")"

# ── 3b. ABSOLUTE-PATH ARM — STRUCTURAL: no confirm var opens it ─────────────
# A RELATIVE INSTALL_DIR is refused outright. A relative path resolves
# against whatever directory the recipe happens to run in, so the path the
# guard checks and the path a recipe would delete can be two DIFFERENT
# paths: '..' run from a repo root is the repo's parent, 'agents' run from a
# repo root is the repo's own agents/ tree. The guard therefore requires an
# absolute path — one that starts with '/'.
#
# The arm judges the TILDE-EXPANDED form ($DIR_NORM), not the raw spelling:
# '~/x' expands to $HOME/x before the guard sees it, so it stays absolute
# and is NOT refused here (it proceeds to the live/demo classification
# exactly as before). '~' and '~user/...' never reach this arm — the
# UNVERIFIABLE SPELLING arm (code section 2) refuses them first, with its
# own wording.
#
# It runs BEFORE the classification (code section 7) and before the
# containment arm (code section 9), so no confirm var can ever open a
# relative path.
case "$DIR_NORM" in
    /*) ;;
    *)
        cat >&2 <<EOF
make ${TARGET:-<unknown>}: REFUSING — INSTALL_DIR '$INSTALL_DIR_ENV' is not an absolute path.
  The guard requires an absolute path (one that starts with '/'). A relative
  path resolves against whatever directory the recipe happens to run in, so
  the path the guard checks and the path a recipe would delete can be two
  different paths — '..' in one directory is the parent of another, and
  'agents' in a repo root is that repo's own agents/ tree.
  A leading '~/' IS accepted: it is expanded to \$HOME before the guard sees
  it, so '~/agents-ensemble' is judged as the absolute \$HOME/agents-ensemble.
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

DIR_CANON="$(_canon_dir "$DIR_NORM")"

# ── 4. comparison helpers (case-folded on case-insensitive filesystems) ──────
_CASE_FOLD=0
if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
    _CASE_FOLD=1
fi

_fold() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

_eq() {
    # _eq <a> <b> — equality, case-folded on Darwin.
    if [ "$_CASE_FOLD" = "1" ]; then
        [ "$(_fold "$1")" = "$(_fold "$2")" ]
    else
        [ "$1" = "$2" ]
    fi
}

_is_ancestor_of() {
    # _is_ancestor_of <ancestor> <target> — true when <ancestor> is a
    # STRICT ancestor of <target> (path-component aware, case-folded on
    # Darwin).
    local a="$1" t="$2"
    if [ "$_CASE_FOLD" = "1" ]; then
        a="$(_fold "$a")"
        t="$(_fold "$t")"
    fi
    [ -n "$a" ] || return 1
    [ "$a" != "$t" ] || return 1
    case "$a" in
        /) return 0 ;;
    esac
    case "$t" in
        "$a"/*) return 0 ;;
    esac
    return 1
}

# ── 5. homes under protection: $HOME + SUDO_USER's real home ─────────────────
# Under sudo, HOME points at the TARGET user's home (env_reset), which
# would silently ungate the operator's real live dir — see the SUDO/ROOT
# decision block above. Resolve SUDO_USER's home and protect its candidates
# too. Resolution: getent (Linux) → dscl (macOS) → /home/<user> (Linux
# convention; fail-CLOSED: a wrong guess only widens the protected set).
_sudo_user_home() {
    [ -n "$SUDO_USER" ] || return 1
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

# ── 6. the extensible live set (colon-separated, PATH syntax) ───────────────
# The old form was `for ext in /opt/ensemble "$(printf '%s' "$VAR" | tr ':' '\n')"`
# — the QUOTED command substitution collapsed the whole list into ONE word,
# so no extension entry ever matched and the documented knob protected
# NOTHING (F-03). Split properly here: one entry per line, surrounding
# whitespace trimmed, empty entries skipped, globbing disabled so an entry
# containing '*' cannot expand.
_live_ext_list() {
    local rest="$ENSEMBLE_LIVE_INSTALL_DIRS" entry trimmed
    set -f
    while [ -n "$rest" ]; do
        entry="${rest%%:*}"
        case "$rest" in
            *:*) rest="${rest#*:}" ;;
            *) rest="" ;;
        esac
        trimmed="${entry#"${entry%%[![:space:]]*}"}"
        trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
        [ -n "$trimmed" ] || continue
        printf '%s\n' "$trimmed"
    done
    set +f
}

# ── 7. classify the requested dir against the protected set ──────────────────
# _cand_class <normalized-or-physical-path> — echoes live|demo when the path
# matches a protected install candidate for ANY protected home (textually or
# physically); empty output otherwise.
_cand_class() {
    local want="$1" h base name cand cand_n cand_c ext ext_n ext_c
    for h in "$HOME" "$HOME_ALT"; do
        [ -n "$h" ] || continue
        for name in agents-ensemble agents-ensemble-demo; do
            # legacy shape: <home>/<name>
            cand="$h/$name"
            cand_n="$(_norm "$cand")"
            cand_c="$(_canon_dir "$cand_n")"
            if _eq "$want" "$cand_n" || _eq "$want" "$cand_c"; then
                case "$name" in
                    agents-ensemble)      printf 'live\n' ;;
                    agents-ensemble-demo) printf 'demo\n' ;;
                esac
                return 0
            fi
            # Layer-1 shape: home ends with the name → home itself IS it
            base="$(basename "$h")"
            case "$base" in
                "$name")
                    if _eq "$want" "$(_norm "$h")" || _eq "$want" "$(_canon_dir "$h")"; then
                        case "$name" in
                            agents-ensemble)      printf 'live\n' ;;
                            agents-ensemble-demo) printf 'demo\n' ;;
                        esac
                        return 0
                    fi
                    ;;
            esac
            # Layer-1 SIBLING shape (lib.sh:898-899, :907-914): when <home>
            # ends with EITHER install name, the cross-env sibling is a
            # candidate too (HOME=…/agents-ensemble-demo ⇒ …/agents-ensemble
            # is a LIVE candidate, and vice versa).
            case "$base" in
                agents-ensemble|agents-ensemble-demo)
                    if [ "$base" != "$name" ]; then
                        cand="$(dirname "$h")/$name"
                        cand_n="$(_norm "$cand")"
                        cand_c="$(_canon_dir "$cand_n")"
                        if _eq "$want" "$cand_n" || _eq "$want" "$cand_c"; then
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
    for ext in /opt/ensemble; do
        ext_n="$(_norm "$ext")"
        ext_c="$(_canon_dir "$ext_n")"
        if _eq "$want" "$ext_n" || _eq "$want" "$ext_c"; then
            printf 'live\n'
            return 0
        fi
    done
    while IFS= read -r ext; do
        [ -n "$ext" ] || continue
        ext_n="$(_norm "$ext")"
        ext_c="$(_canon_dir "$ext_n")"
        if _eq "$want" "$ext_n" || _eq "$want" "$ext_c"; then
            printf 'live\n'
            return 0
        fi
    done <<EOF
$(_live_ext_list)
EOF
    return 1
}

# Compare BOTH forms of the requested dir (physical first — it is the one
# that catches symlink aliases; then textual — it catches protected-path
# spellings that do not exist yet).
CLASS="$(_cand_class "$DIR_CANON")"
if [ -z "$CLASS" ]; then
    CLASS="$(_cand_class "$DIR_NORM")"
fi

# ── 8. per-path confirm gate ────────────────────────────────────────────────
case "$CLASS" in
    live)
        if [ "$ENSEMBLE_INSTALL_LIVE" != "1" ]; then
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
        if [ "$ENSEMBLE_INSTALL_DEMO" != "1" ]; then
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
        # Not a protected dir — the containment arm below still applies.
        ;;
esac

# ── 9. containment arm (F-04) — STRUCTURAL: no confirm var opens it ────────
# A recursive delete of an ANCESTOR of a protected install dir destroys the
# protected tree; a recursive delete of a DESCENDANT is the same incident
# one level down. Both are refused regardless of any confirm var — the
# confirm vars open a path that IS the protected install dir, not a path
# that swallows it.
#
# The classification (code sections 7-8) runs FIRST: a live/demo dir itself
# stays gated by its confirm var (ENSEMBLE_INSTALL_LIVE / ENSEMBLE_INSTALL_DEMO),
# exactly as before.
_contains_refusal() {
    # _contains_refusal <relation> <target> <anchor>
    local relation="$1" target="$2" anchor="$3"
    cat >&2 <<EOF
make ${TARGET:-<unknown>}: REFUSING — INSTALL_DIR resolves to $target, which $relation the protected install dir $anchor.
  A recursive delete of that path would destroy the protected install tree
  $anchor — the incident class this guard exists for (2026-11-09).
  This refusal is STRUCTURAL: no confirm var opens it (ENSEMBLE_INSTALL_LIVE /
  ENSEMBLE_INSTALL_DEMO do not apply here — they open a path that IS the
  protected install dir, not one that swallows it).
  Choose an install dir that is neither an ancestor of, nor inside, a
  protected install dir:
    make ${TARGET:-<unknown>} INSTALL_DIR=/path/to/install
  Sanctioned lanes (prefer these):
    deploy :  ENSEMBLE_DEPLOY_LIVE=1 scripts/deploy.sh live
    upgrade:  ENSEMBLE_UPGRADE_LIVE=1 scripts/upgrade/promote.sh live --f2-verified-closed
EOF
    exit 78
}


# Protected HOME anchors ($HOME, SUDO_USER's home) and protected install
# anchors (live/demo dirs). The ancestor (CONTAINS) arm applies to both;
# the descendant (INSIDE) arm applies to the install anchors only (a plain
# subdir of a HOME is a normal, safe install location).
_protected_home_paths() {
    local h
    for h in "$HOME" "$HOME_ALT"; do
        [ -n "$h" ] || continue
        printf '%s\n' "$(_norm "$h")"
    done
}

_protected_install_paths() {
    local h name base ext
    for h in "$HOME" "$HOME_ALT"; do
        [ -n "$h" ] || continue
        for name in agents-ensemble agents-ensemble-demo; do
            printf '%s\n' "$(_norm "$h/$name")"
        done
        base="$(basename "$h")"
        case "$base" in
            agents-ensemble|agents-ensemble-demo)
                printf '%s\n' "$(_norm "$h")"
                # Layer-1 SIBLING (lib.sh:898-899): cross-env candidate —
                # dirname(<home>)/<name> for the OTHER install name.
                for name in agents-ensemble agents-ensemble-demo; do
                    [ "$(basename "$h")" != "$name" ] || continue
                    printf '%s\n' "$(_norm "$(dirname "$h")/$name")"
                done
                ;;
        esac
    done
    printf '%s\n' "/opt/ensemble"
    while IFS= read -r ext; do
        [ -n "$ext" ] || continue
        printf '%s\n' "$(_norm "$ext")"
    done <<EOF
$(_live_ext_list)
EOF
}

# The requested dir, in both forms, for comparison against the anchors.
for _form in "$DIR_CANON" "$DIR_NORM"; do
    [ -n "$_form" ] || continue
    # ── CONTAINS arm: the requested path is a STRICT ANCESTOR of a protected
    #    install dir (e.g. $HOME, /opt, /) → its recursive delete would take
    #    the protected tree with it.
    while IFS= read -r _anchor; do
        [ -n "$_anchor" ] || continue
        _anchor_n="$(_norm "$_anchor")"
        _anchor_c="$(_canon_dir "$_anchor_n")"
        if _is_ancestor_of "$_form" "$_anchor_c" || _is_ancestor_of "$_form" "$_anchor_n"; then
            _contains_refusal "CONTAINS" "$_form" "$_anchor_c"
        fi
    done <<EOF
$(_protected_install_paths)
EOF
    # ── INSIDE arm: the requested path is INSIDE a protected install dir
    #    (e.g. <live>/data) → same incident one level down.
    while IFS= read -r _anchor; do
        [ -n "$_anchor" ] || continue
        _anchor_n="$(_norm "$_anchor")"
        _anchor_c="$(_canon_dir "$_anchor_n")"
        if _is_ancestor_of "$_anchor_c" "$_form" || _is_ancestor_of "$_anchor_n" "$_form"; then
            _contains_refusal "is INSIDE" "$_form" "$_anchor_c"
        fi
    done <<EOF
$(_protected_install_paths)
EOF
    # ── HOME anchors: the requested path IS a protected home, or is a strict
    #    ancestor of one. (A home always holds a protected install dir inside
    #    it, so the CONTAINS arm above normally fires first — this is the
    #    backstop that names the home itself.)
    while IFS= read -r _anchor; do
        [ -n "$_anchor" ] || continue
        _anchor_n="$(_norm "$_anchor")"
        _anchor_c="$(_canon_dir "$_anchor_n")"
        if _eq "$_form" "$_anchor_n" || _eq "$_form" "$_anchor_c" \
           || _is_ancestor_of "$_form" "$_anchor_c" || _is_ancestor_of "$_form" "$_anchor_n"; then
            _contains_refusal "CONTAINS" "$_form" "$_anchor_c"
        fi
    done <<EOF
$(_protected_home_paths)
EOF
done

# ── --require-live mode (opt-in; additive) ──────────────────────────────────
# The plist-install / watchdog-install targets write HOST launchd state
# ($HOME/Library/LaunchAgents/ensemble-prod.plist) REGARDLESS of INSTALL_DIR
# — a sandbox or demo INSTALL_DIR would silently overwrite the running prod
# unit with one pointing at that tree. The flag adds ONE extra rule for
# those targets: the requested dir must classify as LIVE (and a live dir
# still needs ENSEMBLE_INSTALL_LIVE=1, via the per-path gate above).
# Evaluated AFTER the per-path gate and the containment arm, so the canonical
# live refusal and the structural containment refusal still fire first, and
# ENSEMBLE_INSTALL_DEMO never opens a demo dir under this mode. Without the
# flag the guard is byte-for-byte unchanged for every existing consumer.
if [ "$REQUIRE_LIVE" = "1" ] && [ "$CLASS" != "live" ]; then
    cat >&2 <<EOF
make ${TARGET:-<unknown>}: REFUSING — INSTALL_DIR does not resolve to a LIVE install dir ($DIR_CANON).
  This target writes the HOST launchd unit
  ($HOME/Library/LaunchAgents/ensemble-prod.plist) — the prod unit must point
  at the live install, never at a sandbox or demo tree (a sandbox INSTALL_DIR
  would silently overwrite the running prod unit).
  To install the prod launchd unit:
    ENSEMBLE_INSTALL_LIVE=1 make ${TARGET:-<unknown>} INSTALL_DIR='$INSTALL_DIR_ENV'
  Under sudo (env vars do not survive plain sudo), the sudo-correct form:
    sudo env ENSEMBLE_INSTALL_LIVE=1 make ${TARGET:-<unknown>} INSTALL_DIR='$INSTALL_DIR_ENV'
  The demo launchd agent is a hand-duplicate with its own Label
  (com.ensemble.watchdog.demo) — see the Makefile's demo-usage note.
EOF
    exit 78
fi

exit 0
