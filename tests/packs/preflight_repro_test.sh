#!/usr/bin/env bash
# tests/packs/preflight_repro_test.sh
#
# T0.4 / AC4 / SC11 — the BASE→HEAD reproduction of the original symptom, in
# one fenced sandbox, on the same fixture, with the same command.
#
#   BASE  : `journal_read` (verbatim, from git) does NOT complete within a
#           60 s budget on the 455,783-byte incident fixture  -> rc 124.
#   HEAD  : the O(N) helper completes the same read in < 5 s and returns the
#           WHOLE journal (2,949 history entries).
#
# The BASE leg is deliberately bounded: running it to completion is a 45–61
# minute CI job (phase0 P0-D2). The exact BASE wall is measured ONCE by
# tests/packs/preflight_base_scale_measure.sh; the ASSERTION here is
# "did not complete", which is the symptom.
#
# Usage: bash tests/packs/preflight_repro_test.sh

set -u
PF_PACK_NAME="preflight-repro"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

pf_fence_init
trap pf_fence_cleanup EXIT

BASE_BUDGET_S="${BASE_BUDGET_S:-60}"
ORACLE="$PF_SB/oracle.sh"
pf_base_oracle "$ORACLE"
INC="$PF_SB/incident.json"
pf_incident_fixture "$INC"
HELPER="$(pf_helper)"

printf '== fixture: %s bytes, sha256 %s ==\n' "$(pf_bytes "$INC")" "$(pf_sha256 "$INC")"
printf '== fence: INSTALL_DIR=%s HOME=%s ==\n' "$INSTALL_DIR" "$HOME"

# ── BASE leg: the symptom ───────────────────────────────────────────────────

printf '\n-- BASE: journal_read on the incident fixture, bounded at %ss --\n' "$BASE_BUDGET_S"
t0="$(pf_now_ms)"
rc=0
pf_run_bounded "$BASE_BUDGET_S" -- "$ORACLE" verify "$INC" > "$PF_SB/base.out" 2> "$PF_SB/base.err" || rc=$?
t1="$(pf_now_ms)"
BASE_ELAPSED_MS=$(( t1 - t0 ))
printf '   rc=%s elapsed=%sms outbytes=%s\n' "$rc" "$BASE_ELAPSED_MS" "$(pf_bytes "$PF_SB/base.out")"

[ "$rc" = "124" ] \
    && pf_ok "BASE did NOT complete within ${BASE_BUDGET_S}s (rc=124 — the hang reproduces)" \
    || pf_bad "BASE completed (rc=$rc) — the fixture no longer reproduces the symptom"
[ "$(pf_bytes "$PF_SB/base.out")" = "0" ] \
    && pf_ok "BASE emitted no journal (the scan never reached its printf)" \
    || pf_bad "BASE emitted $(( $(pf_bytes "$PF_SB/base.out") )) bytes — unexpected for a hung scan"
[ "$BASE_ELAPSED_MS" -ge 55000 ] \
    && pf_ok "BASE consumed the full budget (${BASE_ELAPSED_MS}ms) — a CPU-bound scan, not a fast refusal" \
    || pf_bad "BASE returned early (${BASE_ELAPSED_MS}ms)"

# ── HEAD leg: closed ───────────────────────────────────────────────────────

printf '\n-- HEAD: the same read through the O(N) helper --\n'
t0="$(pf_now_ms)"
h_rc=0
/usr/bin/python3 "$HELPER" verify "$INC" > "$PF_SB/head.out" 2> "$PF_SB/head.err" || h_rc=$?
t1="$(pf_now_ms)"
HEAD_ELAPSED_MS=$(( t1 - t0 ))
printf '   rc=%s elapsed=%sms outbytes=%s\n' "$h_rc" "$HEAD_ELAPSED_MS" "$(pf_bytes "$PF_SB/head.out")"

[ "$h_rc" = "0" ] \
    && pf_ok "HEAD completed the incident read (rc=0)" \
    || pf_bad "HEAD failed on the incident fixture (rc=$h_rc): $(head -3 "$PF_SB/head.err")"
[ "$HEAD_ELAPSED_MS" -lt 5000 ] \
    && pf_ok "HEAD wall ${HEAD_ELAPSED_MS}ms < 5000ms (SC1)" \
    || pf_bad "HEAD wall ${HEAD_ELAPSED_MS}ms >= 5000ms (SC1)"
/usr/bin/python3 - "$PF_SB/head.out" <<'PY' && pf_ok "HEAD returned the WHOLE journal (valid JSON, 2,949 history entries, in_flight null)" || pf_bad "HEAD output is not the whole journal"
import json, sys
doc = json.load(open(sys.argv[1], "rb"))
assert doc["in_flight"] is None, doc["in_flight"]
assert len(doc["history"]) == 2949, len(doc["history"])
assert doc["current"] == "v0.27.11", doc["current"]
PY

# the same bytes the BASE leg WOULD have printed: the fixture minus its
# trailing newline and any NUL (this fixture has neither).
/usr/bin/python3 - "$INC" "$PF_SB/expected.out" <<'PY'
import sys
raw = open(sys.argv[1], "rb").read().replace(b"\x00", b"").rstrip(b"\n")
open(sys.argv[2], "wb").write(raw)
PY
[ "$(pf_sha256 "$PF_SB/head.out")" = "$(pf_sha256 "$PF_SB/expected.out")" ] \
    && pf_ok "HEAD stdout is byte-identical to the processed fixture (command-substitution contract)" \
    || pf_bad "HEAD stdout differs from the processed fixture"

printf '\nBASE: > %ss (did not complete)  ->  HEAD: %sms  (closed loop)\n' "$BASE_BUDGET_S" "$HEAD_ELAPSED_MS"

pf_summary
