#!/usr/bin/env bash
# tests/packs/preflight_journal_scale_test.sh
#
# AC4/AC6/SC1 + phase1 §3.4 — the HEAD performance proof.
#
#   * `journal_read` on the 455,783 B incident fixture  < 5 s
#   * `journal_update` (full RMW through lib.sh)        < 5 s
#   * `_json_sub` on the full journal                   < 1 s
#   * scaling T(4N)/T(N) < 6 across 8/32/128/512 KB (linear => ~4x)
#
# The BASE side is NOT re-run here: it is > 60 s at 455 KB and ~300 s at
# 128 KB (measured; see preflight_base_scale_measure.sh and the wave-1
# evidence index). Asserting a 45–61 minute BASE run in a gate is exactly
# what phase0 P0-D2 forbids — the scaling RATIO is the linearity assertion.
#
# Usage: bash tests/packs/preflight_journal_scale_test.sh

set -u
PF_PACK_NAME="preflight-journal-scale"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

pf_fence_init
trap pf_fence_cleanup EXIT

HELPER="$(pf_helper)"
INC="$PF_SB/incident.json"
pf_incident_fixture "$INC"

# ── leg A: helper-level scaling ────────────────────────────────────────────

printf '\n-- A. helper scaling (valid padded journals) --\n'
mk_pad() { # mk_pad <path> <bytes>
    /usr/bin/python3 - "$1" "$2" <<'PY'
import sys
dst, n = sys.argv[1], int(sys.argv[2])
seed = b'{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-01-01T00:00:00Z","event":"pad","detail":"'
tail = b'"}]}'
fill = n - len(seed) - len(tail)
open(dst, "wb").write(seed + b"x" * fill + tail)
PY
}
PREV_MS=0
PREV_N=0
for n in 8192 32768 131072 524288; do
    f="$PF_SB/pad-$n.json"
    mk_pad "$f" "$n"
    t0="$(pf_now_ms)"
    /usr/bin/python3 "$HELPER" verify "$f" > /dev/null 2>&1
    rc=$?
    t1="$(pf_now_ms)"
    ms=$(( t1 - t0 ))
    [ "$rc" = "0" ] && pf_ok "verify n=$n: rc=0 wall=${ms}ms" || pf_bad "verify n=$n: rc=$rc"
    if [ "$PREV_MS" -gt 0 ] && [ "$PREV_N" -gt 0 ]; then
        ratio_x10=$(( ms * 10 / (PREV_MS > 0 ? PREV_MS : 1) ))
        # linear == 4x; sub-quadratic means < 6x. Reserve startup-noise slack
        # for the small-N pair (an interpreter start is ~25 ms flat).
        if [ "$ratio_x10" -lt 60 ] || [ "$PREV_N" -le 8192 ]; then
            pf_ok "scaling ${PREV_N}->$n: ${ratio_x10}/10 x (linear≈4x, cap 6x)"
        else
            pf_bad "scaling ${PREV_N}->$n: ${ratio_x10}/10 x exceeds the 6x sub-quadratic cap"
        fi
    fi
    PREV_MS=$ms
    PREV_N=$n
done

# ── leg B: lib.sh integration on the incident fixture ─────────────────────

printf '\n-- B. lib.sh integration on the 455,783 B incident journal --\n'
mkdir -p "$INSTALL_DIR/releases"
cp "$INC" "$INSTALL_DIR/releases/state.json"
printf '\n' >> "$INSTALL_DIR/releases/state.json"   # a real journal ends in one \n

cat > "$PF_SB/lib_timing.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
ms_now() { /usr/bin/python3 -c 'import time; print(int(time.time()*1000))'; }

t0="$(ms_now)"
J="$(journal_read)" || { echo "journal_read FAILED"; exit 1; }
t1="$(ms_now)"
echo "journal_read_ms=$((t1 - t0)) reader_bytes=${#J}"

t0="$(ms_now)"
sub="$(_json_sub "$J" history)" || { echo "_json_sub FAILED"; exit 1; }
t1="$(ms_now)"
echo "json_sub_ms=$((t1 - t0)) sub_bytes=${#sub}"

t0="$(ms_now)"
journal_update cooldown_until '"2026-12-01T00:00:00Z"' || { echo "journal_update FAILED"; exit 1; }
t1="$(ms_now)"
echo "journal_update_ms=$((t1 - t0))"
grep -q '"cooldown_until":"2026-12-01T00:00:00Z"' "$(journal_path)" || { echo "journal_update did not land"; exit 1; }
echo "journal_bytes_after=$(wc -c < "$(journal_path)" | tr -d ' ')"
EOS

out="$(INSTALL_DIR="$INSTALL_DIR" REPO_ROOT="$PF_REPO_ROOT" LOG_TAG=pf-scale UP_TARGET=sandbox \
        bash "$PF_SB/lib_timing.sh" 2> "$PF_SB/lib_timing.err")"
rc=$?
printf '%s\n' "$out" | sed 's/^/   /'
[ "$rc" = "0" ] && pf_ok "lib.sh integration completed (journal_read/_json_sub/journal_update)" \
                || pf_bad "lib.sh integration failed (rc=$rc): $(head -3 "$PF_SB/lib_timing.err")"

get_ms() { printf '%s\n' "$1" | sed -n "s/^$2=\([0-9]*\).*/\1/p" | head -1; }
READ_MS="$(get_ms "$out" journal_read_ms)"
SUB_MS="$(get_ms "$out" json_sub_ms)"
UPD_MS="$(get_ms "$out" journal_update_ms)"

[ -n "$READ_MS" ] && [ "$READ_MS" -lt 5000 ] \
    && pf_ok "SC1: journal_read on the incident fixture ${READ_MS}ms < 5000ms" \
    || pf_bad "SC1: journal_read ${READ_MS:-?}ms >= 5000ms"
[ -n "$UPD_MS" ] && [ "$UPD_MS" -lt 5000 ] \
    && pf_ok "AC6: journal_update (full RMW) ${UPD_MS}ms < 5000ms" \
    || pf_bad "AC6: journal_update ${UPD_MS:-?}ms >= 5000ms"
[ -n "$SUB_MS" ] && [ "$SUB_MS" -lt 1000 ] \
    && pf_ok "AC6: _json_sub on the full journal ${SUB_MS}ms < 1000ms" \
    || pf_bad "AC6: _json_sub ${SUB_MS:-?}ms >= 1000ms"

# ── leg C: the guard is NOT consulted on the O(N) path (SC13/G4) ----------
printf '\n-- C. the size guard is not consulted on the O(N) path --\n'
JOURNAL_SIZE_GUARD_BYTES=1 JOURNAL_READ_STRICT=1 out2="$( \
    INSTALL_DIR="$INSTALL_DIR" REPO_ROOT="$PF_REPO_ROOT" LOG_TAG=pf-scale UP_TARGET=sandbox \
    JOURNAL_SIZE_GUARD_BYTES=1 JOURNAL_READ_STRICT=1 \
    bash -c '. "$REPO_ROOT/scripts/upgrade/lib.sh"; journal_read >/dev/null && echo READ_OK' 2>&1 )"
case "$out2" in
    *READ_OK*) pf_ok "a 455 KB journal reads fine with the guard set to 1 byte (O(N) path never consults it)" ;;
    *) pf_bad "the O(N) path consulted the size guard: $out2" ;;
esac

pf_summary
