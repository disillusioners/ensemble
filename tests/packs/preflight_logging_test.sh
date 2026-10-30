#!/usr/bin/env bash
# tests/packs/preflight_logging_test.sh — Pack E (promote-preflight-hang
# phase 3: logging that survives anything).
#
# Scope (SC6 / SC7 + AC1-AC16):
#   E1  file sink        — env set: exactly ONE copy per line in the per-run
#                          NARRATIVE file, timestamped, stdout EMPTY; env
#                          unset: byte-identical legacy stdout behaviour.
#   E2  write failure    — read-only narrative => visible stderr line + ONE
#                          journal `logwrite_failed` row; the caller continues.
#   E3  SC7 markers      — a real preflight harness emits >= 6 timestamped
#                          markers carrying phase/step/elapsed.
#   E4  timestamps       — every narrative line carries an ISO-8601 UTC stamp.
#   E5  atomicity        — 4 KiB cap + `payload elided, N bytes` + embedded
#                          newlines yield ONE physical line; a planted
#                          >= 8 KiB raw line is DETECTED (scanner red-proven).
#   E6  SC6 survival     — SIGKILL the parent client mid-run; the narrative
#                          keeps every line written before the kill.
#   E7  two-file sink    — unit argv targets the PAYLOAD file; the shell env
#                          carries the NARRATIVE path; the pointer names
#                          both; resolution is by run_id (a stale pointer
#                          still resolves the RIGHT file); env scoping.
#   E8  R7 fail-closed   — the client-stderr-to-FILE redirect does NOT
#                          silence the systemd-unavailable refusal; a
#                          signature-free stderr still takes journal-truth.
#   E9  EPIPE            — the `head -1` helper call sites carry 2>/dev/null;
#                          the helper's stderr is empty on a piped call.
#   E10 retention (R8)   — <= 20 kept for the RUNNING OWNER; the five fences
#                          each survive; `stat -f '%m'` absent from the fn.
#   E11 redaction        — a sandbox narrative carries no secret-shaped text.
#   E12 cross-env        — scope derivation + per-env path separation.
#   E13 sweep            — still_running carries narrative/last/elapsed; the
#                          660 s threshold + no-kill contract unchanged.
#   E14 reader           — _upgrade_log_tail resolves the per-run file by
#                          run_id, falls back to the aggregate.
#
# FENCE: tests/helpers/preflight_pack_common.sh. NO self-exec / mutation
# reinvoke (mutations are sed'd/python'd COPIES run in subshells); an
# external spawn-count watcher watches the whole pack.
#
# Usage: bash tests/packs/preflight_logging_test.sh
# Exit codes: 0=PASS, 1=FAIL, 2=FATAL.

set -u
PF_PACK_NAME="preflight-logging"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

pf_fence_init
trap pf_fence_cleanup EXIT

J="$INSTALL_DIR/releases/state.json"
mkjournal() {
    /usr/bin/python3 - "${1:-$J}" <<'PY'
import sys
open(sys.argv[1], "wb").write(b'{"current":null,"previous":null,"in_flight":null,'
    b'"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,'
    b'"quarantined":[],"history":[]}')
PY
}
count_rows() { grep -o "\"event\":\"$2\"" "$1" 2>/dev/null | wc -l | tr -d ' '; }
PYV=/home/nea/ensemble-src/.venv/bin/python
[ -x "$PYV" ] || PYV="$(command -v python3)"

# ── spawn-count watcher (fork-bomb proof, always on) ───────────────────────
PF_WATCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pf-watch.XXXXXX")"
( while [ -d "$PF_WATCH_DIR" ]; do sleep 0.3; done ) &
PF_WATCHER=$!
sync

# a runner helper: sources lib.sh in a child with a controlled env
run_lib() { # run_lib <env-file-to-log> <script-body-file>
    local lg="$1" body="$2"
    ENSEMBLE_UPGRADE_LOG_FILE="$lg" REPO_ROOT="$PF_REPO_ROOT" INSTALL_DIR="$INSTALL_DIR" \
        UP_TARGET=sandbox LOG_TAG=pf-log bash "$body"
}

printf '== E1: file sink (primary) vs legacy stdout ==\n'
mkdir -p "$PF_SB/logs"
cat > "$PF_SB/e1.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
_log "hello one"
_log "hello two"
_warn "warn one"
EOS
OUT="$(run_lib "$PF_SB/logs/run1.log" "$PF_SB/e1.sh" 2> "$PF_SB/e1.err")"
LG="$PF_SB/logs/run1.log"
[ -z "$OUT" ] && pf_ok "stdout is EMPTY when the narrative sink succeeds (no duplication)" \
              || pf_bad "E1 stdout not empty: $OUT"
N1="$(grep -c 'hello one' "$LG" 2>/dev/null || true)"
N2="$(grep -c 'hello two' "$LG" 2>/dev/null || true)"
NW="$(grep -c 'WARN: warn one' "$LG" 2>/dev/null || true)"
[ "$N1" = "1" ] && [ "$N2" = "1" ] && [ "$NW" = "1" ] \
    && pf_ok "exactly ONE copy per line in the narrative file" \
    || pf_bad "E1 copy counts: one=$N1 two=$N2 warn=$NW"
[ -s "$PF_SB/e1.err" ] && pf_bad "E1 stderr not empty on a successful write: $(cat "$PF_SB/e1.err")" \
                       || pf_ok "stderr empty on success"
# legacy (env unset) must be BYTE-IDENTICAL
OUT2="$(ENSEMBLE_UPGRADE_LOG_FILE= REPO_ROOT="$PF_REPO_ROOT" INSTALL_DIR="$INSTALL_DIR" \
        UP_TARGET=sandbox LOG_TAG=pf-log bash -c '. "$REPO_ROOT/scripts/upgrade/lib.sh"; _log "legacy one"' 2>/dev/null)"
[ "$OUT2" = "pf-log[sandbox]: legacy one" ] && pf_ok "legacy stdout byte-identical when the env is unset" \
                                            || pf_bad "E1 legacy stdout changed: [$OUT2]"

printf '\n== E2: write failure is REPORTED (stderr + one journal row) ==\n'
mkjournal
mkdir -p "$PF_SB/ro" && chmod 500 "$PF_SB/ro"
cat > "$PF_SB/e2.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
lock_acquire || exit 78
_log "line that cannot be written"
_log "second failing line"
lock_release 2>/dev/null || true
EOS
run_lib "$PF_SB/ro/nope.log" "$PF_SB/e2.sh" > "$PF_SB/e2.out" 2> "$PF_SB/e2.err"
chmod 700 "$PF_SB/ro"
grep -q "line that cannot be written" "$PF_SB/e2.err" \
    && pf_ok "failing line visible on stderr (unredirected)" || pf_bad "E2 stderr missing: $(cat "$PF_SB/e2.err")"
FW="$(count_rows "$J" warn)"
[ "$FW" = "1" ] && pf_ok "exactly ONE logwrite_failed journal row per run" \
                || pf_bad "E2 logwrite_failed rows=$FW want 1"
grep -q "logwrite_failed" "$J" && pf_ok "the row carries the logwrite_failed marker" \
                               || pf_bad "E2 marker missing"

printf '\n== E3/E4: SC7 markers + ISO timestamps (real preflight harness) ==\n'
mkjournal
mkdir -p "$PF_SB/narr"
cat > "$PF_SB/pre.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-pre
INSTALL_DIR="$H_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
_trap_install_signal_handlers "promote:preflight"
lock_acquire || exit 78
_PREFLIGHT_T0="$(_now_epoch)"
preflight_deadline=$(( $(_now_epoch) + PREFLIGHT_PHASE_BUDGET_S ))
_log_phase_marker "journal-read"
J="$(_run_bounded_step journal-read journal_read)" || exit 9
_log_phase_marker "adopt-txn"
_enforce_deadline "$preflight_deadline" preflight "budget=PREFLIGHT_PHASE_BUDGET_S value=${PREFLIGHT_PHASE_BUDGET_S}"
_log_phase_marker "entry-check"
_run_bounded_step entry-check promote_entry_check v1 || exit 78
_log_phase_marker "plugin-staleness"
_log_phase_marker "integrity-current"
_log_phase_marker "integrity-target"
_log_phase_marker "txn-open"
lock_release
exit 0
EOS
ENSEMBLE_UPGRADE_LOG_FILE="$PF_SB/narr/run.log" REPO_ROOT="$PF_REPO_ROOT" H_INSTALL="$INSTALL_DIR" \
    bash "$PF_SB/pre.sh" > /dev/null 2> "$PF_SB/pre.err"
NARR="$PF_SB/narr/run.log"
MK="$(grep -c 'phase-marker:' "$NARR" 2>/dev/null || true)"
[ "${MK:-0}" -ge 6 ] && pf_ok ">=6 phase markers in the narrative ($MK)" \
                     || pf_bad "E3 markers=$MK want >=6 [err: $(tail -2 "$PF_SB/pre.err")]"
BAD_MARK="$(grep 'phase-marker:' "$NARR" | grep -vc 'phase=[^ ]* step=[^ ]* elapsed=[0-9]*s' || true)"
[ "$BAD_MARK" = "0" ] && pf_ok "every marker carries phase + step + elapsed" \
                      || pf_bad "$BAD_MARK markers missing fields"
BAD_TS="$(grep -vcE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z ' "$NARR" || true)"
[ "$BAD_TS" = "0" ] && pf_ok "every narrative line carries an ISO-8601 UTC stamp" \
                    || pf_bad "$BAD_TS narrative lines lack the timestamp"

printf '\n== E5: 4 KiB cap + elision + one-physical-line + corruption pin ==\n'
cat > "$PF_SB/e5.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
BIG="$(head -c 10000 /dev/zero | tr '\0' 'x')"
_log "$BIG"
_logv '{"a": 1,
"b": 2}'
_log "multi
line payload"
EOS
run_lib "$PF_SB/logs/e5.log" "$PF_SB/e5.sh" > /dev/null 2>&1
E5="$PF_SB/logs/e5.log"
grep -qE "payload elided, 1[0-9]{4} bytes" "$E5" && pf_ok "10 KiB payload yields the elision marker" \
                                    || pf_bad "E5 elision marker missing"
MAXLEN="$(awk '{ if (length($0) > m) m = length($0) } END { print m+0 }' "$E5")"
[ "$MAXLEN" -le 4200 ] && pf_ok "no physical line exceeds the 4 KiB cap + stamp (max ${MAXLEN}B)" \
                       || pf_bad "E5 a physical line is ${MAXLEN}B (cap violated)"
# the multi-line payload became ONE physical line: the file's line count
# equals the number of _log calls (3)
LC="$(wc -l < "$E5" | tr -d ' ')"
[ "$LC" = "3" ] && pf_ok "multi-line payloads sanitize to ONE physical line each (3 lines total)" \
                || pf_bad "E5 physical line count=$LC want 3"
# corruption scanner: 0 over-cap lines in real output; a PLANTED one is DETECTED
scan_corrupt() { awk 'length($0) > 4096 { n++ } END { print n+0 }' "$1"; }
[ "$(scan_corrupt "$E5")" = "0" ] && pf_ok "scanner: real output has 0 over-cap lines" \
                                  || pf_bad "E5 scanner flags real output"
printf '%s\n' "$(head -c 9000 /dev/zero | tr '\0' 'y')" >> "$E5"
[ "$(scan_corrupt "$E5")" = "1" ] && pf_ok "scanner DETECTS a planted >=8 KiB line (corruption is not absorbed)" \
                                  || pf_bad "E5 planted corruption not detected — scanner dead"

printf '\n== E6: SC6 narrative survives a parent SIGKILL ==\n'
mkdir -p "$PF_SB/surv"
cat > "$PF_SB/e6child.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-surv
INSTALL_DIR="$H_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
_PREFLIGHT_T0="$(_now_epoch)"
for s in alpha bravo charlie; do
    _log_phase_marker "step-$s"
    sleep 0.3
done
sleep 30
EOS
ENSEMBLE_UPGRADE_LOG_FILE="$PF_SB/surv/run.log" REPO_ROOT="$PF_REPO_ROOT" H_INSTALL="$INSTALL_DIR" \
    bash "$PF_SB/e6child.sh" > /dev/null 2>&1 &
SURV=$!
sleep 1.5
kill -KILL "$SURV" 2>/dev/null; wait "$SURV" 2>/dev/null
SURV_N="$(grep -c 'phase-marker:' "$PF_SB/surv/run.log" 2>/dev/null || true)"
[ "${SURV_N:-0}" -ge 3 ] && pf_ok "all 3 pre-kill markers survive the SIGKILL (no pipe-boundary truncation)" \
                        || pf_bad "E6 surviving markers=$SURV_N want 3"

printf '\n== E7/E12/E14: two-file sink + pointer-by-run_id + env scope + reader ==\n'
E7_OUT="$(cd "$PF_REPO_ROOT" && "$PYV" - "$PF_SB" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
import daemon.tools.upgrade_journal as uj
import daemon.tools.upgrade_tools as ut
sb = Path(sys.argv[1])
inst = sb / "inst"
inst.mkdir(exist_ok=True)
ok = []
# two distinct paths; env scoping separates dirs
n_live, p_live = uj.executor_run_log_paths(inst, "r-aaa", "live")
n_sbx, p_sbx = uj.executor_run_log_paths(inst, "r-aaa", "sandbox")
ok.append(("paths-split", n_live != p_live and n_live.name.endswith(".log") and p_live.name.endswith(".payload.log")))
ok.append(("env-scope", n_live.parent.name == "live" and n_sbx.parent.name == "sandbox"))
ok.append(("scope-derive", uj._ceremony_env_scope(["promote.sh", "live"]) == "live"
           and uj._ceremony_env_scope(["promote.sh"]) == "demo"))
# argv targets the PAYLOAD file
argv = uj.build_service_argv(["promote.sh", "sandbox"], "r-aaa", "user", {"K": "V"},
                             p_sbx, unit_properties=None)
ok.append(("argv-payload", f"--property=StandardOutput=append:{p_sbx.absolute()}" in argv))
ok.append(("argv-payload-err", f"--property=StandardError=append:{p_sbx.absolute()}" in argv))
# the narrative path travels in the env (threading shape)
env = uj.executor_env({"ENSEMBLE_UPGRADE_LOG_FILE": str(n_sbx)})
ok.append(("env-threads", env.get("ENSEMBLE_UPGRADE_LOG_FILE") == str(n_sbx)))
# pointer line names BOTH files, and resolution is BY run_id (stale pointer)
n_sbx.parent.mkdir(parents=True, exist_ok=True)
n_sbx.write_text("2026-01-01 run A\n")
n_b, p_b = uj.executor_run_log_paths(inst, "r-bbb", "sandbox")
n_b.write_text("2026-01-02 run B\n")
agg = uj.executor_log_path(inst)
uj._append_pointer_line(agg, "r-aaa", n_sbx, p_sbx)
uj._append_pointer_line(agg, "r-bbb", n_b, p_b)
ptxt = agg.read_text()
ok.append(("pointer-both", "r-aaa" in ptxt and str(n_sbx) in ptxt and str(p_sbx) in ptxt))
ok.append(("resolve-by-id", "run A" in ut._upgrade_log_tail(inst, run_id="r-aaa")))
ok.append(("aggregate-fallback", "run_id=r-bbb" in ut._upgrade_log_tail(inst, run_id="r-zzz")))
# cross-env isolation: a LIVE run's file exists only under live/, never
# under sandbox/ (distinct run_ids -- file NAMES are identical by shape)
n_l, p_l = uj.executor_run_log_paths(inst, "r-ccc", "live")
n_l.parent.mkdir(parents=True, exist_ok=True)
n_l.write_text("live only\n")
ok.append(("no-cross-env", (not (n_sbx.parent / n_l.name).exists())
           and (not (n_l.parent / n_sbx.name).exists())))
for name, good in ok:
    print(("PASS " if good else "FAIL ") + name)
PY
)"
echo "$E7_OUT" | sed 's/^/  /'
echo "$E7_OUT" | grep -q "^FAIL" && pf_bad "E7/E12/E14 python pins failed" \
    || pf_ok "two-file sink + pointer-by-run_id + env scope + reader fallback"

printf '\n== E8: R7 — client stderr FILE keeps the systemd-unavailable refusal ==\n'
E8_OUT="$(cd "$PF_REPO_ROOT" && "$PYV" - "$PF_SB" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, ".")
import daemon.tools.upgrade_journal as uj
sb = Path(sys.argv[1])
inst = sb / "r7inst"
inst.mkdir(exist_ok=True)
SIG = b"Failed to start transient service unit: Unit ensemble-upgrade-x already exists."
class FakeProc:
    pid = 4242
    def __init__(self, argv, **kw):
        self._w = kw.get("stderr")
        if hasattr(self._w, "write"):
            self._w.write(SIG); self._w.flush()
    def wait(self, timeout=None):
        return 1
def fake_detect(env):
    return ("service", "user", "ok")
uj._service_detect_fn = fake_detect
uj._reset_failed_unit = lambda *a, **k: None
uj.subprocess.Popen = FakeProc
raised = False
try:
    uj.spawn_executor(["promote.sh", "sandbox"], inst, run_id="r7-a")
except uj.ExecutorSystemdUnavailable:
    raised = True
print(("PASS " if raised else "FAIL ") + "refusal-fires-from-file")
# negative: NO signature => no refusal (journal-truth note, pid returned)
class FakeProc2(FakeProc):
    def __init__(self, argv, **kw):
        self._w = kw.get("stderr")
        if hasattr(self._w, "write"):
            self._w.write(b"some unrelated diagnostic"); self._w.flush()
uj.subprocess.Popen = FakeProc2
try:
    pid, note = uj.spawn_executor(["promote.sh", "sandbox"], inst, run_id="r7-b")
    print(("PASS " if pid == 4242 and "no systemd failure signature" in note else "FAIL ") + "signature-free-journal-truth")
except uj.ExecutorSystemdUnavailable:
    print("FAIL signature-free-was-refused")
PY
)"
echo "$E8_OUT" | sed 's/^/  /'
echo "$E8_OUT" | grep -q "^FAIL" && pf_bad "E8 R7 pin failed" \
    || pf_ok "R7: the stderr redirect does NOT silence _refuse_systemd_unavailable"

printf '\n== E9: EPIPE class closed (2>/dev/null at the head -1 call sites) ==\n'
EP_SITES="$(grep -c '_supervision_owned_pids 2>/dev/null | head -1' "$PF_REPO_ROOT/scripts/upgrade/lib.sh")"
[ "${EP_SITES:-0}" = "2" ] && pf_ok "both head -1 call sites carry 2>/dev/null" \
                            || pf_bad "EPIPE call-site redirects found=$EP_SITES want 2"
bash -c '
. "$1/scripts/upgrade/lib.sh"
INSTALL_DIR=/nonexistent-install UP_TARGET=sandbox LOG_TAG=pf-ep
_out="$(_supervision_owned_pids 2>/dev/null | head -1)"
' _ "$PF_REPO_ROOT" 2> "$PF_SB/ep.err" >/dev/null
[ ! -s "$PF_SB/ep.err" ] && pf_ok "helper invocation on a pipe emits nothing on stderr" \
                         || pf_bad "E9 stderr: $(cat "$PF_SB/ep.err")"

printf '\n== E10: retention (R8) — <=20 kept for the RUNNING OWNER + fences ==\n'
LOGDIR="$PF_SB/ret"
mkdir -p "$LOGDIR"
# 25 OLD files (mtime 30 d ago) + 4 fenced survivors
for i in $(seq 1 25); do
    printf 'old %s\n' "$i" > "$LOGDIR/run-old-$i.log"
    touch -d '2 days ago' "$LOGDIR/run-old-$i.log" 2>/dev/null || true
done
# ONE ancient file proves the 14 d age cap (must be deleted regardless of count)
printf 'ancient\n' > "$LOGDIR/run-ancient.log"
touch -d '30 days ago' "$LOGDIR/run-ancient.log" 2>/dev/null || true
printf 'recent\n' > "$LOGDIR/run-recent.log"                 # <6 h fence
printf 'current\n' > "$LOGDIR/run-current.log"               # current-run fence
printf 'inflight\n' > "$LOGDIR/run-inflight.log"             # in_flight fence
printf 'pointer\n' > "$LOGDIR/run-pointer.log"               # pointer fence
mkjournal
/usr/bin/python3 - "$J" <<'PY'
import json, sys
p = sys.argv[1]
j = json.load(open(p))
j["in_flight"] = {"kind": "promote", "target": "v1", "run_id": "run-inflight"}
json.dump(j, open(p, "w"))
PY
mkdir -p "$INSTALL_DIR/data"
printf '%s\n' "2026-01-01T00:00:00Z run_id=run-pointer narrative=$LOGDIR/run-pointer.log payload=$LOGDIR/run-pointer.payload.log" > "$INSTALL_DIR/data/upgrade.log"
cat > "$PF_SB/ret.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-ret
INSTALL_DIR="$R_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
lock_acquire || exit 78
ENSEMBLE_UPGRADE_LOG_FILE="$R_DIR/run-current.log" _run_log_retention "$R_DIR"
lock_release
EOS
R_INSTALL="$INSTALL_DIR" R_DIR="$LOGDIR" REPO_ROOT="$PF_REPO_ROOT" bash "$PF_SB/ret.sh" > "$PF_SB/ret.out" 2> "$PF_SB/ret.err"
TOTAL="$(find "$LOGDIR" -maxdepth 1 -name '*.log' -type f | wc -l | tr -d ' ')"
OLD_LEFT="$(find "$LOGDIR" -maxdepth 1 -name 'run-old-*.log' -type f | wc -l | tr -d ' ')"
[ "$OLD_LEFT" = "20" ] && pf_ok "running owner actually deletes: 20 old files kept of 25 (R8, not a no-op)" \
                       || pf_bad "E10 old files kept=$OLD_LEFT want 20"
[ ! -f "$LOGDIR/run-ancient.log" ] && pf_ok "age cap: the 30-day file was deleted (14 d cap)" \
                                   || pf_bad "E10 the 30-day file survived (age cap dead)"
for f in run-recent.log run-current.log run-inflight.log run-pointer.log; do
    [ -f "$LOGDIR/$f" ] && pf_ok "fence survives: $f" || pf_bad "E10 fence LOST: $f"
done
grep -q "stat -f '%m'" <(awk '/^_run_log_retention\(\)/,/^}/' "$PF_REPO_ROOT/scripts/upgrade/lib.sh") \
    && pf_bad "E10 stat -f %m present in _run_log_retention (GNU garbage)" \
    || pf_ok "portable primitives only (no stat -f %m in the retention fn)"
# foreign live owner => hands off entirely
for i in $(seq 1 5); do printf 'x\n' > "$LOGDIR/run-f-$i.log"; touch -t 202601010000 "$LOGDIR/run-f-$i.log"; done
BEFORE="$(find "$LOGDIR" -name '*.log' -type f | wc -l | tr -d ' ')"
( sleep 20 ) & FOREIGN=$!
mkdir -p "$INSTALL_DIR/releases/rollback.lock.d"
printf '%s\n' "$FOREIGN" > "$INSTALL_DIR/releases/rollback.lock.d/owner"
sed -i "s|lock_acquire || exit 78||" "$PF_SB/ret.sh" 2>/dev/null || true
R_INSTALL="$INSTALL_DIR" R_DIR="$LOGDIR" REPO_ROOT="$PF_REPO_ROOT" bash "$PF_SB/ret.sh" >/dev/null 2>&1
AFTER="$(find "$LOGDIR" -name '*.log' -type f | wc -l | tr -d ' ')"
[ "$BEFORE" = "$AFTER" ] && pf_ok "foreign LIVE owner => retention hands off (no deletion)" \
                        || pf_bad "E10 foreign-owner leg deleted files ($BEFORE -> $AFTER)"
kill -KILL "$FOREIGN" 2>/dev/null; wait "$FOREIGN" 2>/dev/null
rm -rf "$INSTALL_DIR/releases/rollback.lock.d"

printf '\n== E11: redaction — no secret-shaped strings in a narrative ==\n'
if grep -Eq 'PASSWORD=|API_KEY=|SECRET=' "$NARR" 2>/dev/null; then
    pf_bad "E11 narrative carries secret-shaped text"
else
    pf_ok "sandbox narrative carries no PASSWORD=/API_KEY=/SECRET= patterns"
fi

printf '\n== E13: sweep enrichment + unchanged kill contract ==\n'
E13_OUT="$(cd "$PF_REPO_ROOT" && "$PYV" - "$PF_SB" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, ".")
import daemon.services.upgrade_journal_sweep as sw
import daemon.tools.upgrade_journal as uj
ok = []
ok.append(("threshold-660", sw.DEFAULT_REAPER_TIMEOUT_SECONDS == 660))
src = open("daemon/services/upgrade_journal_sweep.py", encoding="utf-8").read()
ok.append(("no-kill-contract", "benign-detach (no kill" in src))
ok.append(("enrichment", "narrative={narr_s}; last={last_phase};" in src.replace("\\\n", "")))
# behavioral: _per_run_observation resolves the narrative + last marker
inst = Path(sys.argv[1]) / "swinst"
narr = inst / "data" / "upgrade" / "sandbox" / "run-sw.log"
narr.parent.mkdir(parents=True, exist_ok=True)
narr.write_text("2026-01-01T00:00:00Z t[sandbox]: phase-marker: phase=promote:preflight step=integrity-current elapsed=2s\n")
class Dummy:
    _per_run_narrative_path = staticmethod(sw.UpgradeJournalSweepService._per_run_narrative_path)
    _per_run_observation = sw.UpgradeJournalSweepService._per_run_observation
obs = Dummy()._per_run_observation(inst, "run-sw")
ok.append(("obs-path", str(narr) in obs[0]))
ok.append(("obs-phase", "step=integrity-current" in obs[1]))
for name, good in ok:
    print(("PASS " if good else "FAIL ") + name)
PY
)"
echo "$E13_OUT" | sed 's/^/  /'
echo "$E13_OUT" | grep -q "^FAIL" && pf_bad "E13 pins failed" || pf_ok "still_running enrichment + 660/no-kill unchanged"

kill "$PF_WATCHER" 2>/dev/null; wait "$PF_WATCHER" 2>/dev/null
rm -rf "$PF_WATCH_DIR"

pf_summary
