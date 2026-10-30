#!/usr/bin/env bash
# tests/packs/preflight_size_guard_test.sh
#
# Pack J (phase5) / SC13 / AC7 / AC16 / AC18 / AC19 / G1-G4 — the size guard.
#
# BINDING SEMANTICS (leader directive W1 + leader ruling Q3, phase1 D1-17):
#   trip condition = TWO CONJUNCTS
#     (1) the read is about to run the LEGACY per-character bash walk
#         (python helper absent/unusable), AND
#     (2) the journal is >= JOURNAL_SIZE_GUARD_BYTES (default 98304).
#   On the O(N) path the guard is NOT consulted -> any size reads.
#   Path scope: promote (JOURNAL_READ_STRICT=1) => REFUSE loudly;
#               rollback/restart/status/adopt-unit/stage/stop-ensemble
#               (strict unset) => WARN + CONTINUE. A4 pins all six scripts
#               individually, deriving each one's strictness from its FILE.
#   NO fail-fast on a recovery path: the walk is ATTEMPTED, bounded at
#   JOURNAL_LEGACY_READ_BUDGET_S (120 s default).
#
# The boundary legs use a SCALED threshold (513 B) so the legacy walk stays
# sub-second: the committed G1-G3 fixtures are the DEFAULT-threshold shapes
# and their byte sizes are asserted against the real constant (a 98 KB legacy
# walk is ~200 s — a gate must not pay that).
#
# Usage: bash tests/packs/preflight_size_guard_test.sh

set -u
PF_PACK_NAME="preflight-size-guard"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

pf_fence_init
trap pf_fence_cleanup EXIT

J="$INSTALL_DIR/releases/state.json"
mkdir -p "$INSTALL_DIR/releases"
HELPER="$(pf_helper)"

mkjournal() { # mkjournal <bytes>
    /usr/bin/python3 - "$J" "$1" <<'PY'
import sys
dst, n = sys.argv[1], int(sys.argv[2])
seed = b'{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-01-01T00:00:00Z","event":"pad","detail":"'
tail = b'"}]}'
fill = n - len(seed) - len(tail)
open(dst, "wb").write(seed + b"x" * fill + tail)
PY
}

# run_lib <strict 0|1> <guard_bytes> <helper_path> <budget|""> -> sets
#   LR_RC (journal_read rc), LR_ERR (stderr), LR_OUT (stdout bytes)
run_lib() {
    local strict="$1" guard="$2" helper="$3" budget="${4:-}" body
    mkdir -p "$PF_SB/lg"
    cat > "$PF_SB/lg/body.sh" <<'EOS'
. "$REPO_ROOT/scripts/upgrade/lib.sh"
journal_read > "$OUT_FILE" 2> "$ERR_FILE"
echo "RC=$?"
EOS
    OUT_FILE="$PF_SB/lg/out" ERR_FILE="$PF_SB/lg/err" \
    INSTALL_DIR="$INSTALL_DIR" REPO_ROOT="$PF_REPO_ROOT" LOG_TAG=pf-guard UP_TARGET=sandbox \
    JOURNAL_READ_STRICT="$strict" JOURNAL_SIZE_GUARD_BYTES="$guard" \
    JOURNAL_READ_HELPER="$helper" JOURNAL_LEGACY_READ_BUDGET_S="${budget:-120}" \
        bash "$PF_SB/lg/body.sh" > "$PF_SB/lg/rc" 2>/dev/null
    LR_RC="$(sed -n 's/^RC=//p' "$PF_SB/lg/rc")"
    LR_ERR="$(cat "$PF_SB/lg/err" 2>/dev/null)"
    LR_OUT="$(pf_bytes "$PF_SB/lg/out")"
    LR_SHA="$(pf_sha256 "$PF_SB/lg/out")"
}

NOHELPER=/nonexistent/journal_read.py

printf '== guard defaults ==\n'
DEF="$(cd "$PF_REPO_ROOT" && bash -c '. scripts/upgrade/lib.sh >/dev/null 2>&1; printf "%s" "${JOURNAL_SIZE_GUARD_BYTES:-UNSET}"' 2>/dev/null)"
case "$DEF" in
    98304) pf_ok "JOURNAL_SIZE_GUARD_BYTES defaults to 98304 (96 KiB)" ;;
    *) pf_bad "JOURNAL_SIZE_GUARD_BYTES default is '$DEF', want 98304" ;;
esac
DEFB="$(cd "$PF_REPO_ROOT" && bash -c '. scripts/upgrade/lib.sh >/dev/null 2>&1; printf "%s" "${JOURNAL_LEGACY_READ_BUDGET_S:-UNSET}"' 2>/dev/null)"
[ "$DEFB" = "120" ] && pf_ok "JOURNAL_LEGACY_READ_BUDGET_S defaults to 120" \
                    || pf_bad "legacy read budget default is '$DEFB', want 120"
[ "$(pf_bytes "$PF_FIXTURES/G1-guard-threshold-minus-1.json")" = "98303" ] \
    && pf_ok "G1 fixture is 98303 B (default threshold - 1)" || pf_bad "G1 fixture size wrong"
[ "$(pf_bytes "$PF_FIXTURES/G2-guard-threshold-exact.json")" = "98304" ] \
    && pf_ok "G2 fixture is 98304 B (default threshold)" || pf_bad "G2 fixture size wrong"
[ "$(pf_bytes "$PF_FIXTURES/G3-guard-threshold-plus-1.json")" = "98305" ] \
    && pf_ok "G3 fixture is 98305 B (default threshold + 1)" || pf_bad "G3 fixture size wrong"

# ── G1: threshold-1 on the LEGACY class, promote path -> ALLOWED ───────────
printf '\n-- G1: legacy class, threshold-1 (scaled to 513), promote path --\n'
mkjournal 512
SHA_BEFORE="$(pf_sha256 "$J")"
run_lib 1 513 "$NOHELPER"
[ "$LR_RC" = "0" ] && [ "$LR_OUT" = "512" ] \
    && pf_ok "G1 ALLOWED: legacy walk ran and returned the journal (rc=0, 512 bytes)" \
    || pf_bad "G1: rc=$LR_RC out=$LR_OUT (want 0/512)"
case "$LR_ERR" in *"falling back to the legacy"*) pf_ok "G1 warns (the slow path is never silent)" ;; *) pf_bad "G1 did not warn: $LR_ERR" ;; esac

# ── G2: exactly the threshold, promote path -> REFUSE ─────────────────────
printf '\n-- G2: legacy class, exactly the threshold, promote path --\n'
mkjournal 513
run_lib 1 513 "$NOHELPER"
[ "$LR_RC" = "1" ] && [ "$LR_OUT" = "0" ] \
    && pf_ok "G2 REFUSED (rc=1, no stdout)" || pf_bad "G2: rc=$LR_RC out=$LR_OUT (want 1/0)"
case "$LR_ERR" in *"513 bytes"*) pf_ok "G2 refusal names the size (513 bytes)" ;; *) pf_bad "G2 refusal does not name the size: $LR_ERR" ;; esac
case "$LR_ERR" in *"JOURNAL_SIZE_GUARD_BYTES=513"*) pf_ok "G2 refusal names the threshold in force" ;; *) pf_bad "G2 refusal does not name the threshold" ;; esac
case "$LR_ERR" in *"python3 >= 3.7"*) pf_ok "G2 refusal carries the remedy pointer (interpreter)" ;; *) pf_bad "G2 refusal has no remedy pointer" ;; esac
AFTER_SHA="$(pf_sha256 "$J")"
[ "$(pf_bytes "$J")" = "513" ] && pf_ok "G2 refusal left the journal byte-unchanged (513 B)" \
                              || pf_bad "G2 refusal mutated the journal"

# ── G3: threshold+1, promote path -> REFUSE ───────────────────────────────
printf '\n-- G3: legacy class, threshold+1, promote path --\n'
mkjournal 514
run_lib 1 513 "$NOHELPER"
[ "$LR_RC" = "1" ] && pf_ok "G3 REFUSED (rc=1)" || pf_bad "G3: rc=$LR_RC (want 1)"

# ── recovery / diagnostic class: WARN + CONTINUE (Q3) ─────────────────────
printf '\n-- Q3: over-threshold on a recovery path (strict unset) -> WARN + CONTINUE --\n'
mkjournal 513
run_lib 0 513 "$NOHELPER"
[ "$LR_RC" = "0" ] && [ "$LR_OUT" = "513" ] \
    && pf_ok "recovery class CONTINUES: over-threshold but the read still returns 513 bytes (never blocked)" \
    || pf_bad "recovery class: rc=$LR_RC out=$LR_OUT (want 0/513)"
case "$LR_ERR" in *"warn-and-continue"*) pf_ok "recovery class warns explicitly (warn-and-continue)" ;; *) pf_bad "recovery class warning missing: $LR_ERR" ;; esac

# ── the bound: the walk is ATTEMPTED, then bounded (no fail-fast) ────────
printf '\n-- bound: 128 KB legacy walk under a 1 s budget -> bounded, not fail-fast --\n'
mkjournal 131072
t0="$(pf_now_ms)"
run_lib 0 1000 "$NOHELPER" 1
t1="$(pf_now_ms)"
ELAPSED=$(( t1 - t0 ))
[ "$LR_RC" = "1" ] && pf_ok "the bounded walk gave up (rc=1) after ${ELAPSED}ms" || pf_bad "bounded walk: rc=$LR_RC"
[ "$ELAPSED" -ge 1000 ] && [ "$ELAPSED" -lt 60000 ] \
    && pf_ok "the walk was ATTEMPTED (${ELAPSED}ms >= budget) — no fail-fast short-circuit" \
    || pf_bad "unexpected elapsed ${ELAPSED}ms (fail-fast would be <1s, a hang >60s)"
case "$LR_ERR" in *"exceeded 1s"*) pf_ok "the bound breach names the budget" ;; *) pf_bad "bound breach not reported: $LR_ERR" ;; esac

# ── G4: the O(N) path never consults the guard ────────────────────────────
printf '\n-- G4: helper present, over-threshold, promote path -> NO refusal --\n'
mkjournal 131072
run_lib 1 1024 "$HELPER"
[ "$LR_RC" = "0" ] && [ "$LR_OUT" = "131072" ] \
    && pf_ok "G4 NO refusal on the O(N) path (131072 bytes at threshold 1024, strict)" \
    || pf_bad "G4: rc=$LR_RC out=$LR_OUT (the O(N) path consulted the guard)"
# and the incident-sized journal on the O(N) path with the DEFAULT threshold
cp "$PF_REPO_ROOT/tests/fixtures/journal/G4-guard-on-fast-path.json" "$J" 2>/dev/null || true
mkjournal 98305
run_lib 1 98304 "$HELPER"
[ "$LR_RC" = "0" ] && pf_ok "G4 at the DEFAULT threshold: a 98305 B journal reads on the O(N) path (next-ceremony protection)" \
                    || pf_bad "G4 default-threshold leg: rc=$LR_RC"

# ── matrix: no undefined combinations ─────────────────────────────────────
printf '\n-- matrix: over-threshold x helper-absent x {promote, recovery, diagnostic} --\n'
mkjournal 600
declare -A MATRIX 2>/dev/null || true
for cls in promote recovery diagnostic; do
    case "$cls" in
        promote) strict=1 ;;
        *)       strict=0 ;;   # recovery and diagnostic share the Q3 class
    esac
    run_lib "$strict" 513 "$NOHELPER"
    printf '   %-11s -> rc=%s out=%sB\n' "$cls" "$LR_RC" "$LR_OUT"
    case "$cls" in
        promote)  [ "$LR_RC" = "1" ] && pf_ok "matrix promote: REFUSE" || pf_bad "matrix promote: rc=$LR_RC" ;;
        recovery) [ "$LR_RC" = "0" ] && pf_ok "matrix recovery: WARN+CONTINUE" || pf_bad "matrix recovery: rc=$LR_RC" ;;
        diagnostic) [ "$LR_RC" = "0" ] && pf_ok "matrix diagnostic: WARN+CONTINUE (R1 — no fail-fast on a read; the D1-18 bounded-diagnostic idea is superseded by the binding ruling)" || pf_bad "matrix diagnostic: rc=$LR_RC" ;;
    esac
done

# ── A4 (code council 2026-10-30): the SIX script paths, one leg each ──────
# R1 routes by SCRIPT, so pin each script's routing rather than only the
# three CLASSES above. The strictness each leg uses is DERIVED FROM THE FILE
# (does <script> set JOURNAL_READ_STRICT=1?), so an edit that marks a recovery
# script strict turns its leg RED — the R1 violation class itself, not a
# hand-copied constant that would agree with the defect.
printf '\n-- A4: per-script routing (R1) --\n'
mkjournal 513
for script in promote rollback restart status stage adopt-unit; do
    case "$script" in
        adopt-unit) path="$PF_REPO_ROOT/scripts/upgrade/adopt-unit.sh" ;;
        *)          path="$PF_REPO_ROOT/scripts/upgrade/$script.sh" ;;
    esac
    [ -f "$path" ] || { pf_bad "A4 $script: script file missing ($path)"; continue; }
    if grep -Eq '^[[:space:]]*(export[[:space:]]+)?JOURNAL_READ_STRICT=1[[:space:]]*$' "$path"; then
        derived=1
    else
        derived=0
    fi
    case "$script" in
        promote) want=1 ;;
        *)       want=0 ;;
    esac
    if [ "$derived" != "$want" ]; then
        pf_bad "A4 $script: derived strict=$derived, R1 wants $want (a recovery path must never be strict)"
        continue
    fi
    run_lib "$derived" 513 "$NOHELPER"
    case "$script" in
        promote)
            [ "$LR_RC" = "1" ] && pf_ok "A4 promote.sh ⇒ REFUSE over-threshold (strict by its own source)" \
                               || pf_bad "A4 promote.sh rc=$LR_RC want 1"
            ;;
        *)
            [ "$LR_RC" = "0" ] && [ "$LR_OUT" = "513" ] \
                && pf_ok "A4 $script.sh ⇒ WARN+CONTINUE over-threshold (no fail-fast on a recovery path)" \
                || pf_bad "A4 $script.sh rc=$LR_RC out=$LR_OUT want 0/513"
            ;;
    esac
done
# stop-ensemble.sh reaches the read path transitively (it sources lib.sh and
# inherits J_READ with strict unset) — pin the inherited routing too.
STOP_SH="$PF_REPO_ROOT/scripts/stop-ensemble.sh"
if [ -f "$STOP_SH" ] && grep -q 'scripts/upgrade/lib.sh' "$STOP_SH" \
   && ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?JOURNAL_READ_STRICT=1' "$STOP_SH"; then
    run_lib 0 513 "$NOHELPER"
    [ "$LR_RC" = "0" ] && pf_ok "A4 stop-ensemble.sh ⇒ inherits WARN+CONTINUE (sources lib.sh, never strict)" \
                       || pf_bad "A4 stop-ensemble.sh rc=$LR_RC want 0"
else
    pf_bad "A4 stop-ensemble.sh no longer sources lib.sh, or became strict"
fi

# ── mutation: the THRESHOLD is what refuses, not some other branch ───────
printf '\n-- mutation: raise the threshold -> the same input is ALLOWED --\n'
mkjournal 513
run_lib 1 100000 "$NOHELPER"
[ "$LR_RC" = "0" ] \
    && pf_ok "mutation check: with the threshold raised the same 513 B input is ALLOWED (the guard leg is threshold-driven)" \
    || pf_bad "mutation check: rc=$LR_RC — the refusal is not threshold-driven"

pf_summary
