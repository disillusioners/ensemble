#!/bin/bash
# ============================================================================
# scripts/upgrade/adopt-unit.sh — ONE-TIME migration: graduate an existing
# script-mode Ensemble install to systemd SERVICE MODE (supervision-detection
# commission P4, 2026-09-29). OPT-IN, EXPLICIT OPERATOR ACTION ONLY.
# ============================================================================
# WHAT THIS DOES (the atomic handover sequence — one procedure, fail-loud):
#   (a) REFUSE if any owned pid is alive (adopting over a running daemon
#       mints DUAL_FIGHT — two masters; stop the install first with
#       scripts/stop-ensemble.sh). Pid discovery REUSES the anchored
#       ownership tiers via lib.sh _supervision_owned_pids (the pinned
#       mirror of stop-ensemble.sh's tiers — never a fresh invention).
#   (b) REFUSE on non-Linux / no /run/systemd/system / no resolvable
#       systemctl (the _supervision_host_allows_unit guard family).
#   (c) REFUSE (🔴 adoption blocker) when no pattern-restricted polkit
#       manage-units rule for ensemble-*.service can be verified — the
#       operator installs the rule from docs/runbooks/systemd-adoption.md
#       §Polkit prerequisite first, then re-runs.
#   (d) generate the instance unit from the STATIC BASE template
#       (scripts/systemd/ensemble-daemon.service — template + substitution)
#       reading config the _resolve_wait_s way (directly from the staged
#       INSTALL_DIR/.env, never from ambient env — the stop-ensemble.sh
#       :83-125 precedent), then: install unit (0600) → daemon-reload →
#       stage ENSEMBLE_RESTART_UNIT into .env (ADDITIVE — replaces only
#       that key, never clobbers others) → systemctl enable --now →
#       verify (livez + boot journal UNIT_MANAGED advisory + declared×
#       verified outcome conforming per the A1 map).
#
# ExecStart IS the EXISTING launcher.sh (absolute path) — the launcher's
# exit-code map (0/75/78/crash) and its backoff/burst loop stay IN the loop;
# the unit adds crash-level revival around it, never a second supervisor
# (the architect's unit-inert finding; see the template header).
#
# UNIT NAMING RULE (must match the ensemble-*.service polkit pattern
# ^ensemble-[0-9A-Za-z@._-]+\.service$ — same charset family as the scope
# rule from docs/incidents/2026-09-28-promote-cgroup-teardown.md §5):
#     ensemble-<slug>.service
#     slug = basename(INSTALL_DIR) minus any leading "agents-", sanitized
#     to [0-9A-Za-z@._-]; empty or bare "ensemble" slug → "main".
#   ~/agents-ensemble        → ensemble-main.service
#   ~/agents-ensemble-demo   → ensemble-demo.service
#   /opt/x/ensemble-prod-eu  → ensemble-prod-eu.service
# An explicit 2nd argument (or env ENSEMBLE_UNIT_NAME) overrides and is
# validated against the same pattern. The hand-provisioned ensemble-live.service
# on ensemble-vm does NOT collide (agents-ensemble → ensemble-main).
#
# SEAMS (operator/test surface — mirrors the P2/P3 SYSTEMCTL_BIN seam at
# lib.sh:1555-1560 and stop-ensemble.sh:316-323; P5 drives these):
#   SYSTEMCTL_BIN           systemctl binary (default "systemctl"; PATH-
#                           resolved so PATH stubs work identically)
#   UNIT_DIR                destination dir for the generated unit
#                           (default /etc/systemd/system)
#   POLKIT_RULES_DIR        polkit rules dir scanned by the §(c) check
#                           (default /etc/polkit-1/rules.d)
#   ADOPT_SYSTEMD_RUN_DIR   the /run/systemd/system liveness dir
#                           (default /run/systemd/system)
#   ADOPT_USER              the unit's User= (default: SUDO_USER when under
#                           sudo, else the invoking user)
#   ADOPT_LIVEZ_BUDGET_S    verify-phase livez budget (default 60)
#   DRY_RUN=1               full refusal-check + generation pass, ZERO
#                           mutations (no file installs, no systemctl
#                           calls, no .env staging); prints the sequence
#                           and the generated unit with secret env MASKED.
#
# SECRET HYDROMETRY: POSTGRES_* values (incl. POSTGRES_PASSWORD) are read
# from .env into the generated unit (installed 0600 for exactly that
# reason) but are NEVER echoed to stdout/stderr by this script — logs carry
# key NAMES only, and the DRY_RUN preview masks values.
#
# EXIT CODES
#   0   success (or clean DRY_RUN preview)
#   2   usage error
#   78  REFUSED — precondition failed (owned pid alive / non-systemd host /
#       polkit rule missing / invalid unit name / destination unit exists /
#       launcher missing / PORT unresolvable). Nothing was mutated.
#   1   step FAILURE after generation began (install/reload/stage/enable/
#       verify). The partial-state ledger is printed; rollback is the
#       documented MANUAL procedure (docs/runbooks/systemd-adoption.md
#       §Rollback-of-the-adoption) — this tool NEVER auto-rolls-back.
#
# Bash 3.2 / BSD-tools compatible. Linux-gated before any mutation (step b).
# ============================================================================

set -u

DRY_RUN="${DRY_RUN:-0}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
POLKIT_RULES_DIR="${POLKIT_RULES_DIR:-/etc/polkit-1/rules.d}"
ADOPT_SYSTEMD_RUN_DIR="${ADOPT_SYSTEMD_RUN_DIR:-/run/systemd/system}"
ADOPT_LIVEZ_BUDGET_S="${ADOPT_LIVEZ_BUDGET_S:-60}"

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_usage() {
    cat >&2 <<'EOF'
Usage: bash scripts/upgrade/adopt-unit.sh <INSTALL_DIR> [<unit-name>]
  INSTALL_DIR   existing install to graduate (script-mode → service-mode)
  unit-name     optional explicit unit name (must match ensemble-*.service;
                default: derived from the install dir — see header)
Env: DRY_RUN=1 SYSTEMCTL_BIN= UNIT_DIR= POLKIT_RULES_DIR= ADOPT_SYSTEMD_RUN_DIR=
     ADOPT_USER= ADOPT_LIVEZ_BUDGET_S=  (seams — see header)
Docs: docs/runbooks/systemd-adoption.md (procedure, polkit snippet, rollback)
EOF
    exit 2
}

[ $# -ge 1 ] && [ $# -le 2 ] || _usage

INSTALL_DIR="${1:-}"
UNIT_NAME_ARG="${2:-${ENSEMBLE_UNIT_NAME:-}}"
[ -n "$INSTALL_DIR" ] || _usage

# Absolute, no trailing slash — mirrors stop-ensemble.sh:91.
INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd)" || {
    echo "adopt-unit: cannot resolve INSTALL_DIR '$1'" >&2
    exit 2
}

# lib.sh reuse: _supervision_owned_pids (anchored tiers), _supervision_
# unit_from_dotenv, supervision_classify, supervision_map_outcome, _now_epoch,
# _log/_warn. lib.sh is SOURCED (never executed) — function defs + globals
# only, the same contract the comp7 suite sources under. UP_TARGET is pinned
# for deterministic log tags (lib.sh _log prints "${UP_TARGET:-lib}"; an
# ambient UP_TARGET from an operator's shell must not leak into the tag).
LOG_TAG="${LOG_TAG:-adopt-unit}"
UP_TARGET="adopt"
# shellcheck disable=SC1091
. "$SELF_DIR/lib.sh" || exit 1

# ── Partial-state ledger (failure reporting — "no partial SILENT state") ────
LEDGER_UNIT_FILE="not installed"
LEDGER_RELOAD="not run"
LEDGER_ENV_STAGED="not staged"
LEDGER_ENABLED="no"

_report_partial_and_fail() {
    # $1 = failing step name; extra prose on $2..
    _warn "STEP '$1' FAILED${2:+: $2}"
    cat >&2 <<EOF
ADOPTION LEFT IN PARTIAL STATE (this tool never auto-rolls-back):
  unit file      : ${LEDGER_UNIT_FILE}
  daemon-reload  : ${LEDGER_RELOAD}
  .env staged    : ${LEDGER_ENV_STAGED}
  unit enabled   : ${LEDGER_ENABLED}
→ Roll back manually per docs/runbooks/systemd-adoption.md §Rollback-of-the-adoption (operator lane), then re-run.
EOF
    exit 1
}

# ── Step (a): refuse if any owned pid is alive (DUAL_FIGHT mint-prevention) ─
_step_a_owned_pids() {
    local pids first
    pids="$(_supervision_owned_pids)"
    if [ -n "$pids" ]; then
        first="$(printf '%s\n' "$pids" | head -1)"
        _warn "REFUSED: owned pid(s) alive under INSTALL_DIR=$INSTALL_DIR (first: $first) — adopting over a running daemon mints DUAL_FIGHT (two masters). Stop the install first: bash scripts/stop-ensemble.sh $INSTALL_DIR  — then re-run."
        exit 78
    fi
    _log "step (a): no owned pid alive (anchored tiers vs $INSTALL_DIR) — OK"
}

# ── Step (-1): pipeline lock + Layer (i) settle-check (commission v0.16.6 c2)
#
# Lock: same mkdir-lock as promote/stage/rollback (D5/D-FA5.1) — adopts the
# same mutex semantics so a concurrent promote cannot race the half-staged
# adoption. Bounded 15s wait, stale-break ownership-guarded; release on
# every exit path via the EXIT trap below (lock_release is itself
# ownership-guarded — a non-owner's release is a silent no-op).
#
# Settle-check: pipeline_settled refuses (exit 78) on any of: missing/
# unset journal current, journal/symlink drift, open in_flight txn, non-null
# pending_op, lock-held (the latter is redundant with the explicit
# lock-acquire below but keeps the error surface uniform — the
# lock-busy reason token is distinct from a generic acquire-time rc 1).
#
# NO --force escape on adopt-unit.sh (per spec): adoption already refuses
# on a running daemon (step a); refusing to adopt over a live pipeline is
# the same defense surface, the lock IS the live pipeline.
ADOPT_LOCK_HELD_BY_ME=0
_adopt_unit_exit_trap() {
    if [ "$ADOPT_LOCK_HELD_BY_ME" = "1" ]; then
        lock_release || true
    fi
}
trap '_adopt_unit_exit_trap' EXIT

_step_pre_settle() {
    if ! SETTLE_OUT="$(pipeline_settled)"; then
        _warn "REFUSED (Layer i): pipeline is not settled ($SETTLE_OUT) — adoption cannot proceed over a live mutation; wait for the upgrade pipeline to settle, then re-run"
        exit 78
    fi
    _log "step (pre): pipeline settled — OK (journal/symlink agree, no in_flight, no pending_op, lock free)"
}

_step_lock() {
    if ! lock_acquire; then
        _warn "REFUSED: pipeline lock busy (another promote/stage/rollback in flight) — adopt-unit.sh takes the same lock to serialize against concurrent pipeline mutations; wait for it to settle, then re-run"
        exit 78
    fi
    ADOPT_LOCK_HELD_BY_ME=1
    _log "step (pre): pipeline lock acquired — OK"
}

# ── Step (b): host guards — Linux + live systemd + resolvable systemctl ─────
_step_b_host() {
    if [ "$(uname -s)" != "Linux" ]; then
        _warn "REFUSED: non-Linux host ('$(uname -s)') — systemd adoption is Linux-only; macOS service mode is launchd (documented FUTURE scope — docs/runbooks/systemd-adoption.md §A2 matrix)."
        exit 78
    fi
    if ! [ -d "$ADOPT_SYSTEMD_RUN_DIR" ]; then
        _warn "REFUSED: $ADOPT_SYSTEMD_RUN_DIR absent — no live systemd on this host (script×ubuntu-no-systemd arm; see docs/runbooks/systemd-adoption.md §A2 matrix)."
        exit 78
    fi
    if ! command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; then
        _warn "REFUSED: systemctl not resolvable ('$SYSTEMCTL_BIN') — cannot drive the adoption sequence."
        exit 78
    fi
    _log "step (b): Linux + $ADOPT_SYSTEMD_RUN_DIR + $SYSTEMCTL_BIN resolvable — OK"
}

# ── Step (c): polkit prerequisite — pattern-restricted manage-units rule ────
_step_c_polkit() {
    local dir="$POLKIT_RULES_DIR" found=""
    if ! [ -d "$dir" ]; then
        _warn "REFUSED: 🔴 POLKIT BLOCKER — polkit rules dir $dir does not exist. A pattern-restricted manage-units rule for ensemble-*.service is a HARD adoption prerequisite (enable --now cannot be authorized without it). Install the rule from docs/runbooks/systemd-adoption.md §Polkit prerequisite (operator, one time), then re-run."
        exit 78
    fi
    # Presence heuristic (conservative): a *.rules file that grants the
    # manage-units action AND name-restricts to the ensemble service pattern.
    # The authoritative gate remains systemctl itself (a missing grant makes
    # enable --now fail loud in step d); this check refuses EARLY so the
    # operator fixes polkit before any unit file lands in /etc.
    local f
    for f in "$dir"/*.rules; do
        [ -f "$f" ] || continue
        if grep -q 'org\.freedesktop\.systemd1\.manage-units' "$f" 2>/dev/null \
           && grep -Eq 'ensemble-[^"'"'"']*\.service' "$f" 2>/dev/null; then
            # Scanner regex is DELIBERATELY LOOSER than _unit_name's strict
            # ^ensemble-[0-9A-Za-z@._-]+\.service$ validator — polkit rule
            # source may use a different regex flavor (anchors, char classes,
            # optional quoting). The authoritative gate is systemctl itself
            # (step d); this scanner only refuses on a clearly-absent rule.
            found="$f"
            break
        fi
    done
    if [ -z "$found" ]; then
        _warn "REFUSED: 🔴 POLKIT BLOCKER — no rule under $dir grants org.freedesktop.systemd1.manage-units restricted to the ensemble-*.service pattern (scanned: *.rules). Adoption cannot proceed without it. Install the snippet from docs/runbooks/systemd-adoption.md §Polkit prerequisite (operator, one time), then re-run."
        exit 78
    fi
    _log "step (c): polkit manage-units rule for the ensemble service pattern found ($found) — OK"
}

# ── Config read — the _resolve_wait_s way (staged .env, never ambient) ──────
_dotenv_value() {
    # $1 = env file, $2 = key → value on stdout (empty + rc 1 when absent)
    local raw
    raw="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}'"$2"'[[:space:]]*=[[:space:]]*//p' "$1" 2>/dev/null | head -1)"
    raw="${raw%$'\r'}"
    case "$raw" in
        \"*\") raw="${raw#\"}"; raw="${raw%\"}" ;;
        \'*\') raw="${raw#\'}"; raw="${raw%\'}" ;;
    esac
    printf '%s' "$raw"
    [ -n "$raw" ] || return 1
    return 0
}

ADOPT_USER="${ADOPT_USER:-${SUDO_USER:-$(id -un)}}"
ENV_FILE="$INSTALL_DIR/.env"

_read_config() {
    local key val
    LAUNCHER="$INSTALL_DIR/launcher.sh"
    if [ ! -x "$LAUNCHER" ]; then
        _warn "REFUSED: existing launcher not found/executable at $LAUNCHER — adoption keeps the launcher as ExecStart (the unit-inert design); nothing to adopt without it."
        exit 78
    fi
    PORT="$(_dotenv_value "$ENV_FILE" PORT || true)"
    if ! printf '%s' "$PORT" | grep -Eq '^[0-9]+$'; then
        _warn "REFUSED: no valid PORT in $ENV_FILE (found: '${PORT:-<absent>}') — fail-closed, the ADR-014 rule (PORT is staged env state, never a script constant)."
        exit 78
    fi
    # PORT + POSTGRES_* only (never OPENAI_* etc. — minimal secret surface).
    ENV_KEYS=""
    ENV_LINES=""
    for key in PORT $( [ -f "$ENV_FILE" ] && sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}\(POSTGRES_[A-Za-z0-9_]\{1,\}\)[[:space:]]*=.*/\2/p' "$ENV_FILE" | awk '!seen[$0]++' ); do
        val="$(_dotenv_value "$ENV_FILE" "$key" || true)"
        ENV_KEYS="$ENV_KEYS $key"
        ENV_LINES="${ENV_LINES}Environment=$key=$(_systemd_env_quote "$val")
"
    done
    [ -n "$ENV_LINES" ] || {
        _warn "REFUSED: no PORT/POSTGRES_* env lines could be derived from $ENV_FILE"
        exit 78
    }
    _log "config: user=$ADOPT_USER port=$PORT env keys(names only):$ENV_KEYS launcher=$LAUNCHER"
}

# systemd Environment= value quoting: bare when in the safe charset, else
# single-quoted with '\'' escaping (systemd.syntax(7) — the unit env parser
# is NOT a shell).
_systemd_env_quote() {
    case "$1" in
        ''|*[!A-Za-z0-9_@%+=:,./-]*)
            printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
            ;;
        *)  printf '%s' "$1" ;;
    esac
}

# ── Unit name derivation + validation ───────────────────────────────────────
_derive_unit_name() {
    local base
    base="$(basename "$INSTALL_DIR")"
    case "$base" in agents-*) base="${base#agents-}" ;; esac
    # avoid a double prefix: agents-ensemble-demo → ensemble-demo (not
    # ensemble-ensemble-demo); a BARE ensemble dir falls to "main" below.
    case "$base" in ensemble-*) base="${base#ensemble-}" ;; esac
    base="$(printf '%s' "$base" | sed -e 's/[^0-9A-Za-z@._-]/-/g' -e 's/^[-.]*//' -e 's/[-.]*$//')"
    if [ -z "$base" ] || [ "$base" = "ensemble" ]; then
        base="main"
    fi
    printf 'ensemble-%s.service' "$base"
}

_unit_name() {
    # sets UNIT_NAME; returns 1 (after a loud warn) on an invalid name.
    # Convention asymmetry: _step_* helpers (a/b/c/d) call `exit 78` directly
    # because they own the failure surface (one terminal path each); this
    # helper `return 1`s after _warn and the central caller at line ~457
    # converts the failure to `exit 78`. Same refuse surface, different
    # control-flow ownership.
    local name="${UNIT_NAME_ARG:-}"
    [ -n "$name" ] || name="$(_derive_unit_name)"
    printf '%s' "$name" | grep -Eq '^ensemble-[0-9A-Za-z@._-]+\.service$' || {
        _warn "REFUSED: unit name '$name' does not match the polkit pattern ^ensemble-[0-9A-Za-z@._-]+\\.service\$ (see the naming rule in the header + docs/runbooks/systemd-adoption.md)."
        return 1
    }
    UNIT_NAME="$name"
    return 0
}

# ── Template resolution (repo layout, then cwd fallback — the
#     stop_via_stop_script resolution shape) ─────────────────────────────────
_resolve_template() {
    # sets TEMPLATE_PATH; returns 1 (after a loud warn) when not found.
    local t="$SELF_DIR/../systemd/ensemble-daemon.service"
    [ -f "$t" ] || t="$SELF_DIR/../../scripts/systemd/ensemble-daemon.service"
    [ -f "$t" ] || t="$(pwd)/scripts/systemd/ensemble-daemon.service"
    [ -f "$t" ] || {
        _warn "REFUSED: STATIC BASE template not found (scripts/systemd/ensemble-daemon.service)"
        return 1
    }
    TEMPLATE_PATH="$t"
    return 0
}

# literal token→value splice (stdin→stdout; no regex semantics — safe for
# arbitrary paths/env values). Values travel via EXPORTED environment vars,
# not awk -v (-v assignment processes escape sequences — a backslash in a
# value would corrupt the splice; ENVIRON passes bytes literally). This
# function only ever runs as a pipeline element (a subshell), so the
# exports never leak past the splice.
_splice_line() {
    SPLICE_TOK="$1"
    SPLICE_VAL="$2"
    export SPLICE_TOK SPLICE_VAL
    awk '
        {
            out = ""; s = $0
            while ((i = index(s, ENVIRON["SPLICE_TOK"])) > 0) {
                out = out substr(s, 1, i - 1) ENVIRON["SPLICE_VAL"]
                s = substr(s, i + length(ENVIRON["SPLICE_TOK"]))
            }
            print out s
        }'
}

# Generate the instance unit into UNIT_CONTENT. Sets WORKING_DIRECTORY /
# UNIT_LABEL for logging; refuses torn substitutions.
_generate_unit() {
    # sets UNIT_CONTENT; returns 1 (after a loud warn) on a torn substitution.
    # NOT called in a command substitution for the refusal path — the caller
    # checks rc (an `exit` here would only kill a substitution subshell and
    # the refusal would be silently swallowed).
    local note line out
    WORKING_DIRECTORY="$INSTALL_DIR"
    case "$INSTALL_DIR" in
        "$HOME")   WORKING_DIRECTORY="%h" ;;
        "$HOME"/*) WORKING_DIRECTORY="%h${INSTALL_DIR#"$HOME"}" ;;
    esac
    UNIT_LABEL="$UNIT_NAME at $INSTALL_DIR"
    note="# Generated by scripts/upgrade/adopt-unit.sh (supervision-detection P4) on $(date '+%Y-%m-%dT%H:%M:%S%z') from scripts/systemd/ensemble-daemon.service — REGENERATE, do not hand-edit; rollback: docs/runbooks/systemd-adoption.md"
    _resolve_template || return 1
    out=""
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            *__ENV_LINES__*)
                out="${out}${ENV_LINES}"
                ;;
            *)
                out="${out}$(printf '%s\n' "$line" \
                    | _splice_line '__GENERATED_NOTE__' "$note" \
                    | _splice_line '__UNIT_LABEL__' "$UNIT_LABEL" \
                    | _splice_line '__USER__' "$ADOPT_USER" \
                    | _splice_line '__WORKING_DIRECTORY__' "$WORKING_DIRECTORY" \
                    | _splice_line '__LAUNCHER__' "$LAUNCHER")
"
                ;;
        esac
    done < "$TEMPLATE_PATH"
    # torn-substitution guard: no __TOKEN__ may survive
    if printf '%s\n' "$out" | grep -Eq '__[A-Z][A-Z0-9_]*__'; then
        _warn "REFUSED: unsubstituted template token(s) survived generation — refusing to install a torn unit"
        return 1
    fi
    UNIT_CONTENT="$out"
    return 0
}

# ── Additive .env staging (never clobbers other keys) ───────────────────────
_stage_restart_unit_env() {
    local tmp
    tmp="$(mktemp "${TMPDIR:-/tmp}/adopt-env.XXXXXX")" || return 1
    if [ -f "$ENV_FILE" ]; then
        # delete ONLY the ENSEMBLE_RESTART_UNIT assignment grammar that
        # _supervision_unit_from_dotenv reads (lib.sh:2081-2095); every other
        # line — comments included — is carried byte-for-byte.
        sed '/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}ENSEMBLE_RESTART_UNIT[[:space:]]*=/d' \
            "$ENV_FILE" > "$tmp" || { rm -f "$tmp"; return 1; }
    fi
    printf '\n# staged by scripts/upgrade/adopt-unit.sh (P4 adoption — service mode)\nENSEMBLE_RESTART_UNIT=%s\n' \
        "$UNIT_NAME" >> "$tmp" || { rm -f "$tmp"; return 1; }
    # cat > preserves the existing file's inode/owner/mode (.env is 0600-class)
    cat "$tmp" > "$ENV_FILE" || { rm -f "$tmp"; return 1; }
    rm -f "$tmp"
    # verified write (W3 discipline — never report an unverified staging)
    [ "$(_supervision_unit_from_dotenv)" = "$UNIT_NAME" ] || return 1
    return 0
}

# ── Verify (the mission's three checks) ─────────────────────────────────────
_verify_adoption() {
    local deadline now body declared outcome detail jp
    # 1. livez
    deadline=$(( $(_now_epoch) + ADOPT_LIVEZ_BUDGET_S ))
    while :; do
        body="$(curl -fsS --max-time 5 "http://localhost:$PORT/livez" 2>/dev/null)" && break
        now="$(_now_epoch)"
        if [ "$now" -ge "$deadline" ]; then
            _report_partial_and_fail "verify/livez" "no 200 on http://localhost:$PORT/livez within ${ADOPT_LIVEZ_BUDGET_S}s (last: ${body:-<none>})"
        fi
        sleep 2
    done
    _log "verify: livez serving :$PORT — ${body:-<empty body>}"
    # 2. operator-side classification (shell twin) — expect UNIT_MANAGED:<unit>.
    #    Called DIRECTLY (not in a command substitution): it sets the
    #    SUPERVISION_* globals in THIS shell and prints its one machine line
    #    to stdout (the P1 contract — the preflight/stop sites print it too).
    supervision_classify || true
    if [ "${SUPERVISION_STATE:-}" != "UNIT_MANAGED" ] || [ "${SUPERVISION_UNIT:-}" != "$UNIT_NAME" ]; then
        _report_partial_and_fail "verify/classify" "expected UNIT_MANAGED:$UNIT_NAME, got ${SUPERVISION_STATE:-<unset>}:${SUPERVISION_UNIT:-<unset>}"
    fi
    _log "verify: classification UNIT_MANAGED:$SUPERVISION_UNIT — OK"
    # 3. declared-mode × verified map (A1) — must land on conforming
    declared="$(_dotenv_value "$ENV_FILE" ENSEMBLE_SUPERVISION || printf 'auto')"
    outcome="$(supervision_map_outcome "$declared" "$SUPERVISION_STATE" "$SUPERVISION_UNIT")"
    case "$outcome" in
        conforming*) _log "verify: declared '$declared' × verified ${SUPERVISION_STATE}:${SUPERVISION_UNIT} → ${outcome%%|*} — OK" ;;
        *) _report_partial_and_fail "verify/declared-mode" "declared '$declared' × verified ${SUPERVISION_STATE}:${SUPERVISION_UNIT} → ${outcome%%|*} (${outcome#*|})" ;;
    esac
    # 4. boot-journal advisory — the daemon-side twin must have seen the unit
    #    (supervision_boot event, upgrade_journal_sweep.py P1 §5(c)). Last
    #    event wins (one boot per adoption). Missing journal degrades to a
    #    WARN — classification (check 2) remains the hard gate.
    jp="$INSTALL_DIR/releases/state.json"
    if [ -f "$jp" ]; then
        detail="$(sed -n 's/.*"event": *"supervision_boot", *"detail": *"\([^"]*\)".*/\1/p' "$jp" | tail -1)"
        case "$detail" in
            "state=UNIT_MANAGED mode=unit unit=$UNIT_NAME "*|"state=UNIT_MANAGED mode=unit unit=$UNIT_NAME")
                _log "verify: boot journal advisory — $detail — OK"
                ;;
            "")
                _warn "verify: journal $jp has NO supervision_boot advisory yet — the daemon-side twin event was not found (pre-dates the advisory build, or boot not flushed). Classification (check 2) remains the verified gate; inspect journalctl -u $UNIT_NAME if in doubt."
                ;;
            *)
                _report_partial_and_fail "verify/boot-journal" "last supervision_boot advisory is '$detail' — expected state=UNIT_MANAGED ... unit=$UNIT_NAME (the daemon did NOT boot under the adopted unit)"
                ;;
        esac
    else
        _warn "verify: no releases/state.json under $INSTALL_DIR — boot advisory unverifiable on a never-promoted install (documented degradation; classification remains the gate)"
    fi
}

# ── Main sequence ────────────────────────────────────────────────────────────
# Refusal order is the mission's atomic-handover order: (a) owned pids,
# (b) host guards, (c) polkit — THEN config/name/generation/sequence.
# Commission v0.16.6 component 2 (Layer i+ii) inserts (pre) settle-check
# + lock BEFORE step (a) so the refusal surface is uniform with promote.sh:
# adopt cannot race a live mutation (lock) and cannot adopt over a
# half-staged state (settle). The settle+lock pair is OPT-IN equivalent
# to the promote's preflight (lib.sh promote_entry_check + lock_acquire).
_step_pre_settle
# Layer-ii lock: ONLY in the mutation path. The DRY_RUN preview must stay
# side-effect-free end-to-end — no rollback.lock.d held (runbook §3 §3c),
# no marker written. _step_pre_settle is read-only and safe in both modes.
if [ "$DRY_RUN" != "1" ]; then
    _step_lock
fi
_step_a_owned_pids
_step_b_host
_step_c_polkit
_read_config
_unit_name || exit 78

DEST="$UNIT_DIR/$UNIT_NAME"
if [ -e "$DEST" ]; then
    _warn "REFUSED: $DEST already exists — adoption is a ONE-TIME migration and never clobbers an existing unit (hand-provisioned or previously generated). Remove it via the rollback procedure first if it is truly stale."
    exit 78
fi

_generate_unit || exit 78

if [ "$DRY_RUN" = "1" ]; then
    _log "DRY RUN — zero mutations; sequence preview:"
    _log "  1. install generated unit → $DEST (mode 0600)"
    _log "  2. $SYSTEMCTL_BIN daemon-reload"
    _log "  3. stage ENSEMBLE_RESTART_UNIT=$UNIT_NAME into $ENV_FILE (additive)"
    _log "  4. $SYSTEMCTL_BIN enable --now $UNIT_NAME"
    _log "  5. verify: livez :$PORT + classify UNIT_MANAGED:$UNIT_NAME + declared-mode conforming + boot-journal advisory"
    echo "──────── generated unit (secret env value masked) ────────"
    printf '%s' "$UNIT_CONTENT" | sed -E 's/^(Environment=POSTGRES_PASSWORD=).+$/\1***masked***/'
    echo "──────── end generated unit ────────"
    _log "DRY RUN complete — re-run without DRY_RUN=1 to execute (operator lane)"
    exit 0
fi

# (d) — the atomic handover sequence, fail-loud at every step.
# Commission v0.16.6 component 2 (Layer ii, promote-side symmetric
# refusal): write the adoption-in-progress marker IMMEDIATELY before any
# mutation — a concurrent promote whose restart routes through systemctl
# (lib.sh keys off ENSEMBLE_RESTART_UNIT directly) would misroute onto a
# half-staged unit. The marker makes promote_entry_check refuse (78) on
# every preflight until the marker is cleared (verify success path) or
# removed by the operator (stale-recovery; see the runbook).
if ! adoption_marker_write; then
    _report_partial_and_fail "adoption-marker" "could not write $INSTALL_DIR/releases/.adoption_in_progress (filesystem permissions? the releases/ dir is the lock-holder's territory)"
fi
umask 077
UNIT_TMP="$(mktemp "${TMPDIR:-/tmp}/adopt-unit.XXXXXX")" \
    || _report_partial_and_fail "generate/tmpfile"
printf '%s' "$UNIT_CONTENT" > "$UNIT_TMP" \
    || { rm -f "$UNIT_TMP"; _report_partial_and_fail "generate/write"; }
if ! install -m 0600 "$UNIT_TMP" "$DEST"; then
    rm -f "$UNIT_TMP"
    _warn "unit-file install to $DEST failed — typically a permissions issue: the install step needs write access to $UNIT_DIR (rerun via sudo, keeping ADOPT_USER=<service user>; polkit then authorizes reload/enable/start for the ensemble-*.service pattern)."
    _report_partial_and_fail "install-unit-file"
fi
rm -f "$UNIT_TMP"
LEDGER_UNIT_FILE="installed at $DEST (0600)"
_log "step (d).1: unit installed → $DEST (0600 — carries secret env)"

"$SYSTEMCTL_BIN" daemon-reload \
    || _report_partial_and_fail "daemon-reload"
LEDGER_RELOAD="done"
_log "step (d).2: daemon-reload — OK"

_stage_restart_unit_env \
    || _report_partial_and_fail "stage-env" "additive ENSEMBLE_RESTART_UNIT staging into $ENV_FILE failed verification"
LEDGER_ENV_STAGED="ENSEMBLE_RESTART_UNIT=$UNIT_NAME"
_log "step (d).3: .env staged (additive; verified re-read) — OK"

"$SYSTEMCTL_BIN" enable --now "$UNIT_NAME" \
    || _report_partial_and_fail "enable-now" "$SYSTEMCTL_BIN enable --now $UNIT_NAME failed (polkit denial? see §Polkit prerequisite in the runbook)"
LEDGER_ENABLED="yes"
_log "step (d).4: enable --now $UNIT_NAME — issued"

_verify_adoption

# Layer ii closure: clear the adoption-in-progress marker on verify success
# — the next promote preflight may now proceed. Marker-removal failure is
# best-effort WARN-only here (the verification already succeeded; a
# follow-up promote refusing because of a stale marker is recoverable
# via the documented manual removal path).
if ! adoption_marker_clear; then
    _warn "adoption marker CLEAR FAILED — next promote will refuse until manually removed (see runbook §Adoption-marker stale-recovery): bash -c 'rm -f $INSTALL_DIR/releases/.adoption_in_progress'"
fi

_log "ADOPTION COMPLETE: $INSTALL_DIR is now SERVICE MODE under $UNIT_NAME (launcher stays ExecStart; declared×verified conforming per the A1 map)."
_log "logs: journalctl -u $UNIT_NAME -f  (unit journal — service-mode ADDS structured journald capture)"
exit 0
