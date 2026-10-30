#!/usr/bin/env bash
# tests/packs/preflight_ceremony_readiness_test.sh [--expect-implemented]
#
# Pack I (phase5 §2, new) — CEREMONY READINESS. T5.16 / AC16 / SC12.
# Target behaviour: phase6-plan.md §4.2 (the stamp + the floor check, refuse
# BEFORE the nonce burns) and §4.3 (the watchdog token).
#
# WHAT IT PROVES (the machine-readable table is
# tests/packs/preflight_ceremony_readiness.txt — one row per leg I1–I12):
#   * a staged sandbox arm against a PROVABLY-STALE pinned scripts dir REFUSES
#     BEFORE the nonce is consumed (no nonce burn; the row names the sync
#     command); a synced dir arms cleanly;
#   * the Q1 HYBRID FLOOR: git ancestry is primary (contains ⇒ arm; does not
#     contain ⇒ refuse; missing/unknown object ⇒ refuse), and a non-git dir is
#     WARNING-ONLY (Q2) unless it carries a stamp;
#   * B3: the guard's input is read from <scripts_dir>/lib.sh AT ARM TIME, not
#     from the executor;
#   * W5: the arm row's REQUIRED provenance is git rev + lib.sh sha256;
#   * the watchdog token is allowlisted and promote.sh refuses an unarmed one.
#
# 🔴 EXPECTED STATE BEFORE P6 (wave 4): RED. The guard does not exist yet, so
# the pack must FAIL LOUDLY — never silently pass. Each unimplemented leg
# prints `DEFERRED_RED <id> <named symptom>` and the pack exits 3
# (PF_DEFERRED_RED). `--expect-implemented` (the post-P6 / final-gate mode)
# turns every deferral into a hard failure (exit 1); the default mode exists
# only so this pack can be committed ahead of its implementation without
# masquerading as green.
#
# FENCE (phase5 §3, canonical): scratch install root + FAKE_HOME from mktemp,
# escape guard on BOTH the install root AND the pinned scripts dir (the new
# path surface this pack introduces — the 2026-10-12 live-install class),
# per-variable POSTGRES_* scrub. The pack NEVER runs promote.sh outside this
# fence and NEVER resolves a live-shaped path. The ONLY /home/nea/** literals
# in this file are the escape-guard REFUSALS themselves.
#
# Usage: bash tests/packs/preflight_ceremony_readiness_test.sh [--expect-implemented]

set -u
PF_PACK_NAME="preflight-ceremony-readiness"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

EXPECT_IMPLEMENTED=0
for a in "$@"; do
    case "$a" in
        --expect-implemented) EXPECT_IMPLEMENTED=1 ;;
        *) pf_die "unknown argument: $a" ;;
    esac
done

pf_fence_init
trap pf_fence_cleanup EXIT

DEFERRED=0
deferred_red() { # deferred_red <id> <named symptom>
    DEFERRED=$((DEFERRED + 1))
    printf '  DEFERRED_RED %s %s\n' "$1" "$2"
}

TABLE="$PF_REPO_ROOT/tests/packs/preflight_ceremony_readiness.txt"
PROBE="$PF_REPO_ROOT/tests/helpers/preflight_ceremony_probe.py"
PROMOTE="$PF_REPO_ROOT/scripts/upgrade/promote.sh"
LIB="$PF_REPO_ROOT/scripts/upgrade/lib.sh"
UTOOLS="$PF_REPO_ROOT/daemon/tools/upgrade_tools.py"
UJOURNAL="$PF_REPO_ROOT/daemon/tools/upgrade_journal.py"

# ── the extra fence for THIS pack: the pinned scripts dir ───────────────────
pf_assert_not_live_scripts() {
    case "${1:-}" in
        "") pf_die "pf_assert_not_live_scripts: empty path" ;;
        /home/nea/ensemble-src|/home/nea/ensemble-src/*)
            pf_die "REFUSING the live pinned checkout as a scripts dir: $1" ;;
        /home/nea/agents-ensemble|/home/nea/agents-ensemble/*)
            pf_die "REFUSING a live install scripts dir: $1" ;;
    esac
    return 0
}

printf '== fence: INSTALL_DIR=%s HOME=%s ==\n' "$INSTALL_DIR" "$HOME"

# ── Section A — the fence's own self-test (MUST pass even pre-P6) ───────────
printf '\n== A. fence self-test (the new scripts-dir surface) ==\n'
SCRATCH_SCRIPTS="$HOME/scratch-scripts/upgrade"
mkdir -p "$SCRATCH_SCRIPTS"
( pf_assert_not_live_scripts "$SCRATCH_SCRIPTS" ) \
    && pf_ok "A1 a scratch scripts dir under FAKE_HOME is accepted" \
    || pf_bad "A1 a scratch scripts dir was refused (fence too tight)"
if ( pf_assert_not_live_scripts "/home/nea/ensemble-src/scripts/upgrade" >/dev/null 2>&1 ); then
    pf_bad "A2 the escape guard ACCEPTED the live pinned checkout (fence breach)"
else
    pf_ok "A2 the escape guard refuses the live pinned checkout"
fi
if ( pf_assert_not_live "/home/nea/agents-ensemble/releases" >/dev/null 2>&1 ); then
    pf_bad "A3 the shared escape guard ACCEPTED a live install root"
else
    pf_ok "A3 the shared escape guard refuses a live install root"
fi
# AC1 (lane-wide): the idiom as an ACTUAL INVOCATION (not a comment that
# documents its ban) must appear nowhere in the lane's test code.
BANNED="$(grep -rnE '(^|[;|&(])[[:space:]]*env[[:space:]]+-u[[:space:]]+POSTGRES_\*' \
    "$PF_REPO_ROOT/tests" 2>/dev/null || true)"
[ -z "$BANNED" ] \
    && pf_ok "A4 the banned 'env -u POSTGRES_*' invocation appears nowhere in tests/" \
    || pf_bad "A4 the banned 'env -u POSTGRES_*' invocation appears: $BANNED"

# ── Section B — the table is complete and non-vacuous ──────────────────────
printf '\n== B. scenario table ==\n'
ROWS="$(grep -c '^I[0-9]' "$TABLE" 2>/dev/null || true)"
[ "$ROWS" = "12" ] \
    && pf_ok "B1 the table carries all 12 legs (I1–I12)" \
    || pf_bad "B1 the table carries $ROWS legs, expected 12"
IDS_OK=1
for i in $(seq 1 12); do
    grep -qE "^I$i[[:space:]]" "$TABLE" \
        || { IDS_OK=0; pf_bad "B2 table row I$i is missing"; }
done
[ "$IDS_OK" = "1" ] && pf_ok "B2 every id I1–I12 is present"

# ── Section C — the static contract (phase6 §4.2/§4.3) ────────────────────
printf '\n== C. static contract ==\n'

# C1 — the stamp in lib.sh (PIPELINE_SCRIPTS_REV=<this lane's merge commit>).
if grep -q '^PIPELINE_SCRIPTS_REV=' "$LIB" 2>/dev/null; then
    pf_ok "C1 lib.sh carries the PIPELINE_SCRIPTS_REV stamp"
else
    deferred_red "C1" "lib.sh has no PIPELINE_SCRIPTS_REV stamp (phase6 T6.4)"
fi

# C2 — the floor constant on the daemon side (NOT the superseded pre-B1 name).
if grep -rq 'PIPELINE_SCRIPTS_FLOOR_COMMIT' "$PF_REPO_ROOT/daemon" 2>/dev/null; then
    pf_ok "C2 the daemon side defines PIPELINE_SCRIPTS_FLOOR_COMMIT"
else
    deferred_red "C2" "no daemon-side PIPELINE_SCRIPTS_FLOOR_COMMIT constant (phase6 T6.9)"
fi
if grep -rq 'MIN_PIPELINE_SCRIPTS_REV' "$PF_REPO_ROOT/daemon" "$PF_REPO_ROOT/scripts" 2>/dev/null; then
    pf_bad "C3 the SUPERSEDED identifier MIN_PIPELINE_SCRIPTS_REV was minted"
else
    pf_ok "C3 the superseded identifier MIN_PIPELINE_SCRIPTS_REV is absent"
fi

# C4 — the guard's input is read from <scripts_dir>/lib.sh (B3), i.e. the arm
# path opens the FILE rather than trusting the executor's echo.
if grep -q 'lib\.sh' "$UTOOLS" 2>/dev/null \
   && grep -Eq 'read_text|open\(|read_bytes' "$UTOOLS" 2>/dev/null; then
    pf_ok "C4 the arm path reads the pinned lib.sh from the file"
else
    deferred_red "C4" "the arm path never reads <scripts_dir>/lib.sh (phase6 T6.10 / B3)"
fi

# C5 — I9: the guard call precedes consume_pending_action (no nonce burn).
if /usr/bin/python3 - "$UTOOLS" <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
burn = src.find("consume_pending_action(")
if burn < 0:
    sys.exit(1)
guard = None
for pat in (
    r"scripts_dir_provenance_verdict\(",
    r"check_scripts_dir_provenance\(",
    r"scripts_dir_staleness_check\(",
    r"_scripts_dir_provenance_verdict\(",
    r"_scripts_dir_staleness_gate\(",
    r"_arm_scripts_provenance_check\(",
):
    m = re.search(pat, src)
    if m:
        guard = m.start() if guard is None else min(guard, m.start())
sys.exit(0 if guard is not None and guard < burn else 1)
PY
then
    pf_ok "C5 the provenance guard is called BEFORE consume_pending_action"
else
    deferred_red "C5" "no arm-time guard call precedes consume_pending_action (phase6 T6.5 / D6-4)"
fi

# C6 — I10: the arm row's required provenance fields (W5).
MISSING_FIELDS=""
for field in '"scripts_dir"' 'sha256' 'git_rev' 'dirty'; do
    grep -q -- "$field" "$UTOOLS" || MISSING_FIELDS="$MISSING_FIELDS $field"
done
if [ -z "$MISSING_FIELDS" ]; then
    pf_ok "C6 the arm row's provenance fields are constructible (scripts_dir/sha256/git rev/dirty)"
else
    deferred_red "C6" "arm-row provenance incomplete:$MISSING_FIELDS (phase6 T6.3/T6.13 / W5)"
fi

# C7 — I12: the refusal row NAMES the sync command.
if grep -q 'sync' "$UTOOLS" 2>/dev/null \
   && grep -Eq 'git (pull|fetch|checkout)|re-?sync' "$UTOOLS" 2>/dev/null; then
    pf_ok "C7 the stale-scripts refusal names the sync command"
else
    deferred_red "C7" "no refusal path names the sync command (phase6 T6.5 / AC5)"
fi

# C8 — I11a: the watchdog token is allowlisted.
if grep -q 'ENSEMBLE_PROMOTE_WATCHDOG' "$UJOURNAL" 2>/dev/null; then
    pf_ok "C8 ENSEMBLE_PROMOTE_WATCHDOG is threaded through the executor env"
else
    deferred_red "C8" "ENSEMBLE_PROMOTE_WATCHDOG is not in the executor env allowlist (phase6 T6.6)"
fi

# ── Section D — the behavioural guard probe (I1–I8) ────────────────────────
printf '\n== D. behaviour: the Q1 hybrid floor against planted pinned dirs ==\n'

# The daemon-side surface needs an interpreter that can import the daemon
# package (the worktree has no .venv — house convention: use a working
# interpreter, never `uv run`).
PF_PYTHON=""
# Candidates, in order: an explicit override, the certified interpreter of a
# sibling checkout (house convention: a linked worktree has no .venv), this
# checkout's own .venv, then the ambient python3.13 / python3.
for cand in "${PF_PYTHON_OVERRIDE:-}" \
            "$PF_REPO_ROOT/.venv/bin/python" \
            "$PF_REPO_ROOT/../ensemble-src/.venv/bin/python" \
            "$(command -v python3.13 || true)" \
            "$(command -v python3 || true)"; do
    [ -n "$cand" ] || continue
    if ( cd "$PF_REPO_ROOT" && "$cand" -c 'import daemon.tools.upgrade_tools' ) >/dev/null 2>&1; then
        PF_PYTHON="$cand"; break
    fi
done
if [ -z "$PF_PYTHON" ]; then
    printf '\nDEFERRED-ENV: no interpreter can import daemon.tools.upgrade_tools; a gate MUST NOT treat that as a pass\n' >&2
    pf_bad "D0 no interpreter can import the daemon package (cannot exercise Pack I behaviourally)"
else
    pf_ok "D0 interpreter resolves the daemon package: $PF_PYTHON"
    OUT="$( cd "$PF_REPO_ROOT" && "$PF_PYTHON" "$PROBE" --root "$PF_SB/ceremony-fixtures" 2>&1 )"
    PRC=$?
    printf '%s\n' "$OUT" | sed 's/^/  ·    /'
    case "$PRC" in
        0)
            while IFS= read -r line; do
                case "$line" in
                    "SCENARIO "*) pf_ok "${line#SCENARIO }" ;;
                esac
            done <<< "$OUT"
            ;;
        3)
            sym="$(printf '%s\n' "$OUT" | grep -m1 '^PF_MISSING_GUARD:' | cut -d: -f2- | sed 's/^ //')"
            for id in I1 I2 I3 I4 I5 I6 I7 I8; do
                deferred_red "$id" "guard absent — ${sym:-PF_MISSING_GUARD}"
            done
            ;;
        *)
            for id in I1 I2 I3 I4 I5 I6 I7 I8; do
                ln="$(printf '%s\n' "$OUT" | grep -m1 "^SCENARIO $id " || true)"
                if [ -n "$ln" ]; then
                    printf '%s\n' "$ln" | grep -q ' PASS ' && pf_ok "${ln#SCENARIO }" \
                        || pf_bad "${ln#SCENARIO }"
                else
                    deferred_red "$id" "probe produced no verdict for this leg"
                fi
            done
            ;;
    esac
fi

# ── Section E — I11b: the token refusal is EXECUTED against promote.sh ─────
printf '\n== E. the watchdog token refused by promote.sh (sandbox) ==\n'
LIVE_ROOT="$HOME/agents-ensemble"
mkdir -p "$LIVE_ROOT/releases" "$PF_SB/tmp"
printf 'PORT=8079\n' > "$LIVE_ROOT/.env"
pf_assert_not_live_scripts "$LIVE_ROOT/scripts" 2>/dev/null || true
case "$LIVE_ROOT" in
    "$HOME"/*) : ;;
    *) pf_die "E-fence: the live-lane root escaped FAKE_HOME: $LIVE_ROOT" ;;
esac

# fence assertion BEFORE the invocation: resolve_env live must land under HOME
RESOLVED="$( cd "$PF_SB" && HOME="$HOME" PORT=8079 bash -c \
    '. "$1/scripts/upgrade/lib.sh"; resolve_env live; printf "%s" "$INSTALL_DIR"' _ "$PF_REPO_ROOT" 2>/dev/null )"
case "$RESOLVED" in
    "$HOME"/*) pf_ok "E1 the live-lane install root resolves inside the sandbox ($RESOLVED)" ;;
    *) pf_die "E-fence: refusing to invoke promote.sh — live root resolved OUTSIDE the sandbox: '$RESOLVED'" ;;
esac

# a THROWAWAY sandbox target (the sandbox branch refuses a HOME-shaped dir)
TOK_TARGET="$PF_SB/ceremony-target"
mkdir -p "$TOK_TARGET/releases"
pf_assert_not_live "$TOK_TARGET"

# PHASE-6 AMENDMENT (implementation-decisions D-33): the committed E2 ran the
# REAL promote.sh and expected the token refusal — but phase 2 ARMED the real
# tree's watchdog (the L1/L2a machinery is in lib.sh by construction), so the
# real script can only ever take the HONORED branch. The refusal leg now runs
# a SCRATCH COPY of the scripts dir with the watchdog machinery stripped (a
# genuinely-unarmed mixed-generation fixture — new token semantics, no
# watchdog); the armed control (E2b) runs the REAL promote.sh. Both under the
# same asserted fence.
UNARMED_DIR="$PF_SB/unarmed-scripts/upgrade"
mkdir -p "$UNARMED_DIR"
cp "$PROMOTE" "$UNARMED_DIR/promote.sh"
cp "$LIB" "$UNARMED_DIR/lib.sh"
# strip the phase-2 watchdog machinery: rename the L1/L2a function
# definitions so `declare -F` cannot resolve them (the exact predicate
# promote.sh's token check uses). Anchored at column 0 — only the
# definitions move; nothing at source time calls them.
sed -i -E 's/^(_enforce_deadline|_budget_breach|_run_bounded_step|_run_bounded)\(\) \{/\1_UNARMED_FIXTURE() {/' \
    "$UNARMED_DIR/lib.sh"
pf_assert_not_live_scripts "$UNARMED_DIR"
if ( bash -c '. "$1/lib.sh"; declare -F _budget_breach >/dev/null' _ "$UNARMED_DIR" 2>/dev/null ); then
    pf_bad "E2-fix the unarmed fixture still resolves the watchdog machinery (sed strip failed)"
else
    pf_ok "E2-fix the unarmed fixture genuinely lacks the watchdog machinery"
fi

TOK_OUT="$PF_SB/token.out"
( cd "$PF_SB" && env -i PATH="$PATH" HOME="$HOME" TMPDIR="$PF_SB/tmp" PORT=18079 \
      LOG_TAG=pf-pack-i ENSEMBLE_PROMOTE_WATCHDOG=1 INSTALL_DIR="$TOK_TARGET" \
      timeout -k 5 120 bash "$UNARMED_DIR/promote.sh" sandbox --version v0.0.1-test \
      > "$TOK_OUT" 2>&1 )
TOK_RC=$?
if grep -qi 'watchdog' "$TOK_OUT" && [ "$TOK_RC" = "78" ]; then
    pf_ok "E2 promote.sh refuses the unarmed watchdog token (rc=78, names the watchdog)"
else
    deferred_red "I11" "the token is not refused (rc=$TOK_RC; stderr last: $(tail -1 "$TOK_OUT" | cut -c1-140)) — phase6 T6.6"
fi

# E2b — the ARMED control: the REAL promote.sh with the token present must
# take the HONORED branch (the refusal must be reachable ONLY from the
# unarmed state — a check that refused an armed tree would brick every
# ceremony from a new daemon).
ARM_OUT="$PF_SB/armed.out"
( cd "$PF_SB" && env -i PATH="$PATH" HOME="$HOME" TMPDIR="$PF_SB/tmp" PORT=18080 \
      LOG_TAG=pf-pack-i ENSEMBLE_PROMOTE_WATCHDOG=1 INSTALL_DIR="$TOK_TARGET" \
      timeout -k 5 120 bash "$PROMOTE" sandbox --version v0.0.1-test \
      > "$ARM_OUT" 2>&1 )
ARM_RC=$?
if grep -q 'watchdog token honored' "$ARM_OUT" \
   && ! grep -q 'NOT armed' "$ARM_OUT"; then
    pf_ok "E2b the ARMED real promote.sh honors the token (machinery verified, rc=$ARM_RC from later gates)"
else
    deferred_red "I11b" "the armed control did not take the honored branch (rc=$ARM_RC; last: $(tail -1 "$ARM_OUT" | cut -c1-140))"
fi

# ── the state report: NEVER a silent pass ─────────────────────────────────
printf '\n-- deferrals: %s --\n' "$DEFERRED"
if [ "$DEFERRED" -ne 0 ]; then
    if [ "$EXPECT_IMPLEMENTED" = "1" ]; then
        pf_bad "PF_EXPECTED_IMPLEMENTED: $DEFERRED leg(s) are still unimplemented (deferrals are failures in this mode)"
    else
        printf 'PF_DEFERRED_RED %s legs awaiting phase 6 (the guard is not implemented yet)\n' "$DEFERRED"
    fi
fi

pf_summary
SUMMARY_RC=$?
if [ "$DEFERRED" -ne 0 ] && [ "$EXPECT_IMPLEMENTED" = "0" ] && [ "$SUMMARY_RC" = "0" ]; then
    printf 'exit: 3 (PF_DEFERRED_RED — expected until P6 lands)\n'
    exit 3
fi
exit "$SUMMARY_RC"
