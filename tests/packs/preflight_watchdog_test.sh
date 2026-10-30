#!/usr/bin/env bash
# tests/packs/preflight_watchdog_test.sh — Pack D (promote-preflight-hang
# phase 2: the layered preflight watchdog).
#
# Scope (SC4 / SC4b / SC5 + the phase-2 amendment legs):
#   D1  step breach      — a planted slow step trips within budget+10 s:
#                         ONE halt row `deadline-breach-<step>` carrying
#                         budget/value/elapsed/phase/daemon-state, ONE
#                         stderr WARN, lock dir RELEASED, exit 78.
#   D2  breach ORDER     — on a LIVE-SHAPED path (install root
#                         $FAKE_HOME/agents-ensemble — the splice fence is
#                         ACTIVE there) the row EXISTS after the breach;
#                         a planted release-first reordering LOSES the row
#                         (mutation RED ⇒ GREEN).
#   D3  L1 phase breach  — a step under the per-step budget but past the
#                         phase budget trips `_enforce_deadline` with the
#                         `deadline-breach-preflight` row + budget extra +
#                         exit 78 + lock released.
#   D4  SC4b unlocked    — status.sh breach: exit 1, ZERO journal mutation,
#                         warn present; rollback.sh / restart.sh unlocked
#                         read breaches: exit 78, ZERO journal rows.
#   D5  RuntimeMaxSec    — promote argv carries the DERIVED value (1320
#                         default / 1620 @ SOAK 600 / 1920 @ SOAK 900);
#                         restart argv carries NONE; live-mode SOAK_S>300
#                         promote refuses with a halt row (real promote.sh).
#   D6  SC5 negative     — structural (armed window excludes gate_soak) +
#                         3 healthy preflight runs, 0 trip rows + 0 kills.
#   D7  kill invariant   — a GROUP-wide TERM fires a deferred trap inside
#                         a command substitution; a PID-only TERM does NOT
#                         (the banned trap-bypassing class).
#   D8  _HALT_JOURNALED  — double-TERM during the handler ⇒ exactly ONE
#                         halt row; post-completion TERM ⇒ no second row;
#                         planted guard removal ⇒ 2 rows (mutation RED).
#   D9  env override     — PREFLIGHT_STEP_BUDGET_S=7 override lands in the
#                         breach row as value=7 (value in force journalled).
#   D10 wedged systemctl — a sleeping systemctl stub in the dual-fight
#                         check fails LOUD (row + exit 78), never a silent
#                         "not-active".
#
# The REAL 300 s conservative soak control (SC5's soak leg) is executed by
# preflight_logging_test.sh's soak phase (shared scratch) — see
# SC5-REAL-SOAK.md in the plan evidence dir; this pack pins the structural
# exclusion + the fast healthy legs.
#
# FENCE: tests/helpers/preflight_pack_common.sh (scratch install root,
# FAKE_HOME, escape guard, per-variable POSTGRES_* scrub). NO self-exec /
# mutation-reinvoke: every mutation is a sed'd COPY of lib.sh run in a
# subshell; an external spawn-count watcher watches the whole pack anyway.
#
# Usage: bash tests/packs/preflight_watchdog_test.sh
# Exit codes: 0=PASS, 1=FAIL, 2=FATAL.

set -u
PF_PACK_NAME="preflight-watchdog"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

pf_fence_init
trap pf_fence_cleanup EXIT

J="$INSTALL_DIR/releases/state.json"

# ── the external spawn-count watcher (fork-bomb proof, always on) ──────────
PF_WATCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pf-watch.XXXXXX")"
(
    _max=0
    while [ -d "$PF_WATCH_DIR" ]; do
        _n="$(find /proc -maxdepth 1 -name '[0-9]*' 2>/dev/null | wc -l | tr -d ' ')"
        [ "$_n" -gt "$_max" ] && _max="$_n" && printf '%s\n' "$_max" > "$PF_WATCH_DIR/max"
        sleep 0.3
    done
) &
PF_WATCHER=$!
printf '%s\n' "$(find /proc -maxdepth 1 -name '[0-9]*' 2>/dev/null | wc -l | tr -d ' ')" > "$PF_WATCH_DIR/max"

# ── shared fixture: a minimal healthy journal + release ────────────────────
mkjournal() { # mkjournal [path]
    /usr/bin/python3 - "${1:-$J}" <<'PY'
import sys
dst = sys.argv[1]
open(dst, "wb").write(b'{"current":null,"previous":null,"in_flight":null,'
    b'"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,'
    b'"quarantined":[],"history":[]}')
PY
}

mkrelease() { # mkrelease <ver> — a tiny release whose manifest integrity_verify accepts
    local ver="$1" rd f
    rd="$INSTALL_DIR/releases/$ver"
    mkdir -p "$rd/agents/sub" "$rd/frontend/dist"
    printf 'binary-%s\n' "$ver"      > "$rd/ensemble-prod"
    printf '#!/bin/sh\ntrue\n'       > "$rd/launcher.sh"
    printf 'port: 1\n'               > "$rd/config.yaml"
    printf 'a\n'                     > "$rd/agents/sub/a.txt"
    printf 'b\n'                     > "$rd/frontend/dist/b.txt"
    chmod +x "$rd/ensemble-prod" "$rd/launcher.sh"
    f="$(mktemp)"
    # SUBSHELL: `{ cd ...; } > file` alone leaks the cwd into the pack
    ( cd "$rd" || exit 1
      printf '%s  %s\n' "$(shasum -a 256 ensemble-prod | awk '{print $1}')" "ensemble-prod"
      printf '%s  %s\n' "$(shasum -a 256 launcher.sh   | awk '{print $1}')" "launcher.sh"
      printf '%s  %s\n' "$(shasum -a 256 config.yaml   | awk '{print $1}')" "config.yaml"
    ) > "$f"
    # LAYOUT MATTERS (mirrors stage.sh's manifest heredoc): scalars on
    # separate lines, each *_manifest MAP serialized compactly on its OWN
    # line -- _manifest_map_lines (lib.sh) is a LINE-based sed extractor.
    /usr/bin/python3 - "$rd" "$ver" <<'PY'
import hashlib, json, os, sys
rd, ver = sys.argv[1], sys.argv[2]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
def tree(sub):
    base = os.path.join(rd, sub)
    lines = sorted(
        (os.path.relpath(os.path.join(r, n), base), sha(os.path.join(r, n)))
        for r, _d, fs in os.walk(base) for n in fs
    )
    payload = "".join(f"{s}  {p}\n" for p, s in lines)
    return hashlib.sha256(payload.encode()).hexdigest(), lines
a_agg, a_lines = tree("agents")
f_agg, f_lines = tree("frontend")
man = [
    ("version", json.dumps(f"v{ver}")),
    ("binary_version", json.dumps(ver)),
    ("rollback_safe", "true"),
    ("launcher_sha256", json.dumps(sha(os.path.join(rd, "launcher.sh")))),
    ("binary_sha256", json.dumps(sha(os.path.join(rd, "ensemble-prod")))),
    ("config_sha256", json.dumps(sha(os.path.join(rd, "config.yaml")))),
    ("agents_tree_sha256", json.dumps(a_agg)),
    ("frontend_tree_sha256", json.dumps(f_agg)),
]
lines = ["{"]
for key, val in man:
    lines.append(f'  "{key}": {val},')
lines.append('  "agents_manifest": {' + ", ".join(json.dumps(p) + ":" + json.dumps(s) for p, s in a_lines) + "},")
lines.append('  "frontend_manifest": {' + ", ".join(json.dumps(p) + ":" + json.dumps(s) for p, s in f_lines) + "}")
lines.append("}")
with open(os.path.join(rd, "manifest.json"), "w") as fh:
    fh.write("\n".join(lines) + "\n")
PY
}

# The breach harness: acquires the lock on $INSTALL_DIR, arms the phase
# budget, runs the named step (or a planted sleeper), and lets the breach
# machinery fire. Runs as a CHILD so the pack survives the exit 78.
# env: STEP_KIND=sleep|read|entry|integrity  STEP_SLEEP_S=<n>  (budgets via env)
breach_harness() { # breach_harness <logfile>
    cat > "$PF_SB/bh.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-bh
INSTALL_DIR="$BH_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
_trap_install_signal_handlers "promote:preflight"
lock_acquire || exit 78
lock_heartbeat
preflight_deadline=$(( $(_now_epoch) + PREFLIGHT_PHASE_BUDGET_S ))
case "${BH_STEP:-read}" in
    sleep)
        _run_bounded_step planted-slow-step sleep "${BH_SLEEP_S:-60}"
        ;;
    read)
        J="$(_run_bounded_step journal-read journal_read)" || exit 9
        ;;
    entry)
        _run_bounded_step entry-check promote_entry_check v1 || exit 78
        ;;
    integrity)
        _run_bounded_step integrity-target integrity_verify v1 || exit 78
        ;;
esac
rc=$?
_enforce_deadline "$preflight_deadline" preflight "budget=PREFLIGHT_PHASE_BUDGET_S value=${PREFLIGHT_PHASE_BUDGET_S}"
lock_release
exit "$rc"
EOS
    chmod +x "$PF_SB/bh.sh"
    REPO_ROOT="$PF_REPO_ROOT" BH_INSTALL="$INSTALL_DIR" BH_STEP="${BH_STEP:-read}" BH_SLEEP_S="${BH_SLEEP_S:-60}" \
        bash "$PF_SB/bh.sh" > "$PF_SB/bh.out" 2> "$PF_SB/bh.err"
}

count_rows() { # OCCURRENCE count (the journal is one physical line)
    grep -o "\"event\":\"$2\"" "$1" 2>/dev/null | wc -l | tr -d ' '
}

printf '== D1: per-step breach (SC4) ==\n'
mkjournal
BH_STEP=sleep BH_SLEEP_S=60 PREFLIGHT_STEP_BUDGET_S=5 PREFLIGHT_PHASE_BUDGET_S=120 breach_harness
D1_RC=$?
[ "$D1_RC" = "78" ] && pf_ok "breach exits 78 (rc=$D1_RC)" || pf_bad "D1 rc=$D1_RC want 78"
D1_ROWS="$(count_rows "$J" halt)"
[ "$D1_ROWS" = "1" ] && pf_ok "exactly ONE halt row" || pf_bad "D1 halt rows=$D1_ROWS want 1"
D1_DETAIL="$(/usr/bin/python3 -c '
import json,sys
j=json.load(open(sys.argv[1]))
h=[r for r in j["history"] if r.get("event")=="halt"]
print(h[-1]["detail"] if h else "")' "$J")"
case "$D1_DETAIL" in
    deadline-breach-planted-slow-step*"budget=PREFLIGHT_STEP_BUDGET_S value=5 elapsed="*"phase=promote:preflight"*"daemon-state=live")
        pf_ok "row shape: step+budget+value+elapsed+phase+daemon-state present" ;;
    *) pf_bad "D1 row shape wrong: $D1_DETAIL" ;;
esac
grep -q "PREFLIGHT STEP BUDGET BREACH step=planted-slow-step" "$PF_SB/bh.err" \
    && pf_ok "ONE stderr WARN names the step" || pf_bad "D1 warn missing: $(cat "$PF_SB/bh.err")"
[ ! -d "$INSTALL_DIR/releases/rollback.lock.d" ] \
    && pf_ok "lock dir RELEASED" || pf_bad "D1 lock dir still present"
grep -q "WARN" "$PF_SB/bh.err" && [ "$(grep -c 'BUDGET BREACH' "$PF_SB/bh.err")" = "1" ] \
    && pf_ok "exactly one breach warn line" || pf_bad "D1 warn count wrong"

printf '\n== D2: breach order on a LIVE-SHAPED path (row must survive) ==\n'
# Live-shaped install under FAKE_HOME: journal_splice's _is_under_live()
# resolves $HOME/agents-ensemble — inside the mktemp FAKE_HOME the splice
# FENCE IS ACTIVE (history appends refused while the pipeline lock is not
# held by a live owner). D2-5: journal FIRST, release LAST.
LS="$PF_FAKE_HOME/agents-ensemble"
mkdir -p "$LS/releases"
printf 'PORT=8123\nPOSTGRES_DB=scratch\n' > "$LS/.env"
LJ="$LS/releases/state.json"
mkjournal "$LJ"
cat > "$PF_SB/lh.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-lh
INSTALL_DIR="$LS_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
_trap_install_signal_handlers "promote:preflight"
lock_acquire || exit 78
_budget_breach PREFLIGHT_STEP_BUDGET_S order-probe preflight 1
exit 0
EOS
LS_INSTALL="$LS" REPO_ROOT="$PF_REPO_ROOT" bash "$PF_SB/lh.sh" >/dev/null 2>&1
LS_ROWS="$(count_rows "$LJ" halt)"
[ "$LS_ROWS" = "1" ] && pf_ok "live-shaped: the halt row LANDED (order journal-first holds)" \
                     || pf_bad "live-shaped: row lost (rows=$LS_ROWS) — order broken?"
# mutation: release-first reordering in a sed'd copy must LOSE the row
mkdir -p "$PF_SB/mut/scripts/upgrade"
# Rebuild the mutation precisely: swap the order inside _budget_breach only.
/usr/bin/python3 - "$PF_REPO_ROOT/scripts/upgrade/lib.sh" "$PF_SB/mut/scripts/upgrade/lib.sh" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
start = text.index("_budget_breach() {")
end = text.index("\n}\n", start) + 3
body = text[start:end]
good_j = '    journal_history_append halt "deadline-breach-${step}'
good_r = "    lock_release 2>/dev/null || true\n    exit 78"
mut_j = '    : row-suppressed-by-mutation\n'"    lock_release 2>/dev/null || true\n    exit 78"
assert good_j in body and good_r in body, "anchors missing"
# remove the journal append + warn, keep release+exit FIRST-class citizens
ji = body.index(good_j)
je = body.index("    _warn \"PREFLIGHT STEP BUDGET BREACH", ji)
je = body.index("\n", je) + 1
mutated = body[:ji] + mut_j.split("\n")[0] + "\n" + body[je:]
open(dst, "w", encoding="utf-8").write(text[:start] + mutated + text[end:])
PY
mkjournal "$LJ"
cat > "$PF_SB/lh2.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-lh2
INSTALL_DIR="$LS_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
lock_acquire || exit 78
_budget_breach PREFLIGHT_STEP_BUDGET_S order-probe preflight 1
exit 0
EOS
cat > "$PF_SB/lh2run.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$MUTLIB"
LOG_TAG=pf-lh2
INSTALL_DIR="$LS_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
lock_acquire || exit 78
_budget_breach PREFLIGHT_STEP_BUDGET_S order-probe preflight 1
exit 0
EOS
LS_INSTALL="$LS" MUTLIB="$PF_SB/mut/scripts/upgrade/lib.sh" REPO_ROOT="$PF_REPO_ROOT" bash "$PF_SB/lh2run.sh" >/dev/null 2>&1
MUT_ROWS="$(count_rows "$LJ" halt)"
[ "$MUT_ROWS" = "0" ] && pf_ok "mutation (release-first/no-journal) loses the row — pin is mutation-meaningful" \
                       || pf_bad "mutation did not lose the row (rows=$MUT_ROWS) — pin is dead"

printf '\n== D3: L1 phase-budget breach between steps ==\n'
mkjournal
BH_STEP=sleep BH_SLEEP_S=8 PREFLIGHT_STEP_BUDGET_S=60 PREFLIGHT_PHASE_BUDGET_S=5 breach_harness
D3_RC=$?
[ "$D3_RC" = "78" ] && pf_ok "phase breach exits 78" || pf_bad "D3 rc=$D3_RC want 78"
D3_DETAIL="$(/usr/bin/python3 -c '
import json,sys
j=json.load(open(sys.argv[1]))
h=[r for r in j["history"] if r.get("event")=="halt"]
print(h[-1]["detail"] if h else "")' "$J")"
case "$D3_DETAIL" in
    deadline-breach-preflight*"budget=PREFLIGHT_PHASE_BUDGET_S value=5"*"daemon-state=live")
        pf_ok "L1 row: deadline-breach-preflight + budget extra + daemon-state" ;;
    *) pf_bad "D3 row shape wrong: $D3_DETAIL" ;;
esac
[ ! -d "$INSTALL_DIR/releases/rollback.lock.d" ] && pf_ok "phase breach released the lock" \
                                                  || pf_bad "D3 lock still held"

printf '\n== D4: SC4b unlocked-path breaches ==\n'
# status.sh: a breached read (oversized journal + no helper + tiny legacy
# budget) → warn + exit 1, journal byte-unchanged, NEVER a journal write.
mkjournal
/usr/bin/python3 - "$J" <<'PY'
import sys
dst = sys.argv[1]
open(dst, "wb").write(b'{"current":null,"x":"' + b"y" * 100000 + b'"}')
PY
SB_BEFORE="$(pf_sha256 "$J")"
UP_TARGET=sandbox INSTALL_DIR="$INSTALL_DIR" REPO_ROOT="$PF_REPO_ROOT" LOG_TAG=pf-st PORT=8455 \
    JOURNAL_READ_HELPER=/bin/false \
    JOURNAL_SIZE_GUARD_BYTES=512 JOURNAL_LEGACY_READ_BUDGET_S=2 \
    bash "$PF_REPO_ROOT/scripts/upgrade/status.sh" sandbox > "$PF_SB/st.out" 2> "$PF_SB/st.err"
ST_RC=$?
[ "$ST_RC" = "1" ] && pf_ok "status.sh exits 1 on a breached read (never 78)" \
                    || pf_bad "status.sh rc=$ST_RC want 1 [err: $(head -2 "$PF_SB/st.err" 2>/dev/null | tr '\n' ' ')]"
[ "$(pf_sha256 "$J")" = "$SB_BEFORE" ] && pf_ok "status.sh left the journal byte-unchanged" \
                                          || pf_bad "status.sh MUTATED the journal"
grep -q "UNREADABLE" "$PF_SB/st.err" && pf_ok "status.sh warns (per-run log/stderr only)" \
                                         || pf_bad "status.sh warn missing"

# rollback.sh / restart.sh unlocked reads: their refusal branches exit 78
# with ZERO journal rows (an unlocked path cannot and must not journal).
for SCR in rollback restart; do
    mkjournal
    /usr/bin/python3 - "$J" <<'PY'
import sys
open(sys.argv[1], "wb").write(b'{"current":null,"x":"' + b"y" * 100000 + b'"}')
PY
    RB_BEFORE="$(pf_sha256 "$J")"
    if [ "$SCR" = "rollback" ]; then
        UP_TARGET=sandbox INSTALL_DIR="$INSTALL_DIR" REPO_ROOT="$PF_REPO_ROOT" LOG_TAG=pf-rb PORT=8455 \
            JOURNAL_READ_HELPER=/bin/false \
            JOURNAL_SIZE_GUARD_BYTES=512 JOURNAL_LEGACY_READ_BUDGET_S=2 \
            bash "$PF_REPO_ROOT/scripts/upgrade/rollback.sh" sandbox --to v9 > "$PF_SB/rb.out" 2> "$PF_SB/rb.err"
    else
        UP_TARGET=sandbox INSTALL_DIR="$INSTALL_DIR" REPO_ROOT="$PF_REPO_ROOT" LOG_TAG=pf-rs PORT=8455 \
            JOURNAL_READ_HELPER=/bin/false \
            JOURNAL_SIZE_GUARD_BYTES=512 JOURNAL_LEGACY_READ_BUDGET_S=2 \
            bash "$PF_REPO_ROOT/scripts/upgrade/restart.sh" sandbox --run-id rx --grace-s 1 > "$PF_SB/rs.out" 2> "$PF_SB/rs.err"
    fi
    SCR_RC=$?
    [ "$SCR_RC" = "78" ] && pf_ok "$SCR.sh unlocked breach exits 78 (script refusal semantics)" \
                         || pf_bad "$SCR.sh rc=$SCR_RC want 78"
    [ "$(pf_sha256 "$J")" = "$RB_BEFORE" ] && pf_ok "$SCR.sh unlocked breach wrote ZERO journal rows" \
                                            || pf_bad "$SCR.sh MUTATED the journal on an unlocked path"
done

printf '\n== D5: RuntimeMaxSec derivation + kind scoping + live soak refusal ==\n'
PYV=/home/nea/ensemble-src/.venv/bin/python
[ -x "$PYV" ] || PYV="$(command -v python3)"
D5_OUT="$(cd "$PF_REPO_ROOT" && "$PYV" - <<'PY'
import sys
sys.path.insert(0, ".")
import daemon.tools.upgrade_journal as uj
from pathlib import Path
ok = []
ok.append(("default1320", uj.derived_runtime_max_sec() == 1320))
ok.append(("soak600", uj.derived_runtime_max_sec(soak_s=600) == 1620))
ok.append(("soak900", uj.derived_runtime_max_sec(soak_s=900) == 1920))
ok.append(("floor", uj.derived_runtime_max_sec(soak_s=0, total_deadline_s=100) == 1200))
argv = uj.build_service_argv(["promote.sh", "live"], "r-20261030-010101-abc", "user",
                             {"A": "1"}, Path("/tmp/x"),
                             unit_properties=[f"RuntimeMaxSec={uj.derived_runtime_max_sec()}"])
ok.append(("argv-carries", "--property=RuntimeMaxSec=1320" in argv))
plain = uj.build_service_argv(["restart.sh"], "r-20261030-010101-def", "user", {}, Path("/tmp/x"))
ok.append(("restart-none", not any("RuntimeMaxSec" in a for a in plain)))
try:
    uj.build_service_argv(["x"], "r-20261030-010101-ghi", "user", {}, Path("/tmp/x"),
                          unit_properties=["Restart=always"])
    ok.append(("restart-guard", False))
except ValueError:
    ok.append(("restart-guard", True))
import inspect
src = inspect.getsource(uj.spawn_executor)
ok.append(("plumbed", "unit_properties" in src))
for name, good in ok:
    print(("PASS " if good else "FAIL ") + name)
PY
)"
echo "$D5_OUT" | sed 's/^/  /'
echo "$D5_OUT" | grep -q "FAIL" && pf_bad "D5 python pins failed" || pf_ok "derivation 1320/1620/1920 + floor + argv + restart-none + guard + plumbing"
grep -n "RuntimeMaxSec={_uj.derived_runtime_max_sec()}" "$PF_REPO_ROOT/daemon/manager.py" >/dev/null \
    && pf_ok "manager arms the derived bound at the promote call site" \
    || pf_bad "manager call site does not carry the derived RuntimeMaxSec"

# live-mode SOAK refusal: a REAL promote.sh run against the live-shaped
# scratch root. ENSEMBLE_UPGRADE_LIVE=1 + sandbox-shaped files; the refusal
# fires inside the locked window (halt row + exit 78).
mkjournal "$LJ"
LSOAK_OUT="$(UP_TARGET=live TARGET=live ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_PROMOTE_SOAK_S=600 \
    INSTALL_DIR="$LS" PORT=8123 REPO_ROOT="$PF_REPO_ROOT" LOG_TAG=pf-lsoak VERSION=vnone \
    bash "$PF_REPO_ROOT/scripts/upgrade/promote.sh" live --f2-verified-closed 2>&1)"
LSOAK_RC=$?
[ "$LSOAK_RC" = "78" ] && pf_ok "live promote with SOAK_S=600 refuses (exit 78)" \
                        || pf_bad "live soak refusal rc=$LSOAK_RC want 78"
case "$LSOAK_OUT" in *"ENSEMBLE_PROMOTE_SOAK_S=600 > 300 refused"*)
    pf_ok "refusal names the value in force" ;; *) pf_bad "refusal text wrong: $LSOAK_OUT" ;; esac
LSOAK_ROWS="$(count_rows "$LJ" halt)"
[ "$LSOAK_ROWS" -ge 1 ] && pf_ok "live soak refusal wrote a halt row" \
                        || pf_bad "live soak refusal wrote no row"
[ ! -d "$LS/releases/rollback.lock.d" ] && pf_ok "live soak refusal released the lock" \
                                        || pf_bad "live soak refusal left the lock held"

printf '\n== D6: SC5 structural exclusion + healthy negative control ==\n'
# Structural: every budget-check site lives between the arming line and the
# txn-open; gate_soak is NOT inside the armed window.
PROMOTE_SRC="$PF_REPO_ROOT/scripts/upgrade/promote.sh"
ARM_LINE="$(grep -n 'preflight_deadline=\$(( \$(_now_epoch) + PREFLIGHT_PHASE_BUDGET_S ))' "$PROMOTE_SRC" | head -1 | cut -d: -f1)"
TXN_LINE="$(grep -n 'journal_open_txn "promote"' "$PROMOTE_SRC" | head -1 | cut -d: -f1)"
SOAK_LINE="$(grep -n 'gate_soak "\$SOAK_S"' "$PROMOTE_SRC" | head -1 | cut -d: -f1)"
[ -n "$ARM_LINE" ] && [ -n "$TXN_LINE" ] && [ -n "$SOAK_LINE" ] \
    && [ "$ARM_LINE" -lt "$TXN_LINE" ] && [ "$SOAK_LINE" -gt "$TXN_LINE" ] \
    && pf_ok "armed window [$ARM_LINE..$TXN_LINE] excludes gate_soak (line $SOAK_LINE)" \
    || pf_bad "structural exclusion failed (arm=$ARM_LINE txn=$TXN_LINE soak=$SOAK_LINE)"
STEP_WRAPS="$(awk -v a="$ARM_LINE" -v t="$TXN_LINE" 'NR>a && NR<t && /_run_bounded_step|_df_systemctl|_enforce_deadline "\$preflight_deadline"/' "$PROMOTE_SRC" | wc -l | tr -d ' ')"
[ "$STEP_WRAPS" -ge 8 ] && pf_ok "≥8 bounded/checkpoint sites inside the armed window ($STEP_WRAPS)" \
                       || pf_bad "too few bounded sites in window ($STEP_WRAPS)"
# no budget-check sites between txn-open and script end (disarmed by construction)
AFTER="$(awk -v t="$TXN_LINE" 'NR>t && (/_run_bounded_step/ || /preflight_deadline/)' "$PROMOTE_SRC" | wc -l | tr -d ' ')"
[ "$AFTER" = "0" ] && pf_ok "zero watchdog sites after the txn opens (soak/commit never armed)" \
                   || pf_bad "$AFTER watchdog sites after txn-open"

# Healthy runs: N=3, each exercises the REAL wrapped steps against a real
# release fixture; assert 0 breach rows, 0 non-78 exits, lock released.
mkrelease v1
mkjournal
HEALTHY=0
for i in 1 2 3; do
    BH_STEP=integrity PREFLIGHT_STEP_BUDGET_S=120 PREFLIGHT_PHASE_BUDGET_S=300 breach_harness
    HRC=$?
    HROWS="$(count_rows "$J" halt)"
    HLOCK=0; [ -d "$INSTALL_DIR/releases/rollback.lock.d" ] && HLOCK=1
    if [ "$HRC" = "0" ] && [ "$HROWS" = "0" ] && [ "$HLOCK" = "0" ]; then
        HEALTHY=$((HEALTHY + 1))
    fi
done
[ "$HEALTHY" = "3" ] && pf_ok "3/3 healthy preflight runs: 0 trip rows, 0 kills, lock released" \
                     || pf_bad "healthy runs: $HEALTHY/3 (rc=$HRC rows=$HROWS lock=$HLOCK)"

printf '\n== D7: cgroup-wide kill invariant (group TERM fires; PID-only does not) ==\n'
cat > "$PF_SB/d7a.sh" <<'EOS'
#!/usr/bin/env bash
set -u
trap_handler_fired=0
term_handler() { trap_handler_fired=1; }
trap term_handler TERM
out="$(sleep 30)"
echo "substituted:$out"
EOS
chmod +x "$PF_SB/d7a.sh"
bash "$PF_SB/d7a.sh" >/dev/null 2>&1 &
D7_PID=$!
sleep 0.6
kill -TERM "$D7_PID" 2>/dev/null      # PID-only: bash defers the trap mid-substitution
sleep 1
ALIVE_PIDONLY=0; kill -0 "$D7_PID" 2>/dev/null && ALIVE_PIDONLY=1
kill -KILL "$D7_PID" 2>/dev/null; wait "$D7_PID" 2>/dev/null
case "$ALIVE_PIDONLY" in
    1) pf_ok "PID-only TERM leaves the shell alive (trap deferred) -- the banned trap-bypassing class, probe confirms" ;;
    0) pf_note "PID-only TERM fired the trap on this bash (deference not observed -- version-dependent; the group leg is the invariant)" ;;
esac
cat > "$PF_SB/d7b.sh" <<'EOS'
#!/usr/bin/env bash
set -u
h() { echo "trapfired" > "$D7B_FLAG"; }
trap h TERM
out="$(sleep 30)"
echo "sub:$out"
EOS
chmod +x "$PF_SB/d7b.sh"
D7B_FLAG="$PF_SB/d7b.flag"; export D7B_FLAG
rm -f "$D7B_FLAG"
# setsid: the probe MUST get its own process group -- a non-interactive
# script's background jobs share the SCRIPT's pgroup, so a group TERM
# would kill this pack (learned the hard way).
setsid bash "$PF_SB/d7b.sh" >/dev/null 2>&1 &
D7B_PID=$!
sleep 0.8
D7B_PGID="$(ps -o pgid= -p "$D7B_PID" | tr -d ' ')"
kill -TERM -- "-$D7B_PGID" 2>/dev/null || kill -TERM "$D7B_PID" 2>/dev/null
for w in 1 2 3 4 5; do kill -0 "$D7B_PID" 2>/dev/null || break; sleep 0.4; done
kill -KILL "$D7B_PID" 2>/dev/null; wait "$D7B_PID" 2>/dev/null
[ -f "$D7B_FLAG" ] && grep -q trapfired "$D7B_FLAG" \
    && pf_ok "group-wide TERM fired the deferred trap (the RuntimeMaxSec stop path)" \
    || pf_bad "group TERM did not fire the trap"
printf '\n== D8: _HALT_JOURNALED double-TERM + post-completion ==\n'
mkjournal
cat > "$PF_SB/dt.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-dt
INSTALL_DIR="$DT_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
_trap_install_signal_handlers "promote:preflight"
lock_acquire || exit 78
# Simulate a TERM arriving while the handler is mid-journalling: call the
# handler, then call it AGAIN (the re-entrancy the guard collapses).
_term_sent=0
kill -TERM $$
sleep 3
lock_release 2>/dev/null || true
exit 0
EOS
DT_INSTALL="$INSTALL_DIR" REPO_ROOT="$PF_REPO_ROOT" bash "$PF_SB/dt.sh" >/dev/null 2>&1
DT_ROWS="$(count_rows "$J" halt)"
[ "$DT_ROWS" = "1" ] && pf_ok "TERM during the run journals exactly ONE halt row (guard holds)" \
                     || pf_bad "D8 halt rows=$DT_ROWS want 1"
# post-completion no-op: second handler entry after the first journalled
mkjournal
cat > "$PF_SB/dt2.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-dt2
INSTALL_DIR="$DT_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
trap_install_done=1
lock_acquire || exit 78
_signal_journal_halt TERM
_signal_journal_halt TERM
_signal_journal_halt TERM
lock_release 2>/dev/null || true
exit 0
EOS
DT_INSTALL="$INSTALL_DIR" REPO_ROOT="$PF_REPO_ROOT" bash "$PF_SB/dt2.sh" >/dev/null 2>&1
DT2_ROWS="$(count_rows "$J" halt)"
[ "$DT2_ROWS" = "1" ] && pf_ok "post-completion re-entry is a no-op (first-row-wins)" \
                     || pf_bad "D8 post-completion rows=$DT2_ROWS want 1"
# mutation: strip the guard from a copy → duplicate rows (RED proof).
# A precise python edit of the COPY (a blanket sed removing `return 0`s
# breaks unrelated functions -- learned).
/usr/bin/python3 - "$PF_REPO_ROOT/scripts/upgrade/lib.sh" "$PF_SB/mutguard.sh" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
old_guard = ('    if [ "${_HALT_JOURNALED:-0}" = "1" ]; then\n'
             '        return 0\n'
             '    fi\n')
assert old_guard in text, "guard anchor missing — mutation pack needs updating"
open(dst, "w", encoding="utf-8").write(text.replace(old_guard, "", 1))
PY
mkjournal
cat > "$PF_SB/dt3.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$MUTLIB"
LOG_TAG=pf-dt3
INSTALL_DIR="$DT_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
lock_acquire || exit 78
_signal_journal_halt TERM
_signal_journal_halt TERM
lock_release 2>/dev/null || true
exit 0
EOS
DT_INSTALL="$INSTALL_DIR" MUTLIB="$PF_SB/mutguard.sh" REPO_ROOT="$PF_REPO_ROOT" bash "$PF_SB/dt3.sh" > "$PF_SB/dt3.out" 2> "$PF_SB/dt3.err"
DT3_ROWS="$(count_rows "$J" halt)"
[ "$DT3_ROWS" -ge 2 ] && pf_ok "guard-stripped mutation journals ${DT3_ROWS} rows — pin mutation-meaningful" \
                       || pf_bad "mutation did not duplicate rows ($DT3_ROWS) — pin dead [err: $(head -2 "$PF_SB/dt3.err" 2>/dev/null | tr '\n' ' ')] [tail: $(grep -o '\"event\":\"[a-z_]*\"' "$J" | sort | uniq -c | tr '\n' ' ')]"

printf '\n== D9: env override lands in the row (value in force journalled) ==\n'
mkjournal
BH_STEP=sleep BH_SLEEP_S=30 PREFLIGHT_STEP_BUDGET_S=7 PREFLIGHT_PHASE_BUDGET_S=120 breach_harness
D9_RC=$?
D9_DETAIL="$(/usr/bin/python3 -c '
import json,sys
j=json.load(open(sys.argv[1]))
h=[r for r in j["history"] if r.get("event")=="halt"]
print(h[-1]["detail"] if h else "")' "$J")"
case "$D9_DETAIL" in *budget=PREFLIGHT_STEP_BUDGET_S\ value=7*) pf_ok "override value=7 journalled" ;; *) pf_bad "D9 row: $D9_DETAIL" ;; esac

printf '\n== D10: wedged systemctl probe fails LOUD ==\n'
mkjournal
mkdir -p "$PF_SB/bin"
printf '#!/bin/sh\nsleep 60\n' > "$PF_SB/bin/systemctl"
chmod +x "$PF_SB/bin/systemctl"
cat > "$PF_SB/wd.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-wd
INSTALL_DIR="$WD_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
SYSTEMCTL_BIN="$WD_STUB" SUPERVISION_MODE=unit SUPERVISION_UNIT=ensemble-fake.service
export SYSTEMCTL_BIN SUPERVISION_MODE SUPERVISION_UNIT
lock_acquire || exit 78
supervision_dualfight_check
echo "silent-not-active"
lock_release
exit 0
EOS
WD_INSTALL="$INSTALL_DIR" WD_STUB="$PF_SB/bin/systemctl" REPO_ROOT="$PF_REPO_ROOT" \
    PATH="$PF_SB/bin:$PATH" bash "$PF_SB/wd.sh" > "$PF_SB/wd.out" 2> "$PF_SB/wd.err"
WD_RC=$?
[ "$WD_RC" = "78" ] && pf_ok "wedged probe exits 78 (loud)" || pf_bad "D10 rc=$WD_RC want 78"
WD_ROWS="$(count_rows "$J" halt)"
[ "$WD_ROWS" -ge 1 ] && pf_ok "wedged probe wrote a halt row (never a silent not-active)" \
                     || pf_bad "D10 no halt row"
grep -q "silent-not-active" "$PF_SB/wd.out" && pf_bad "D10 reached the silent fallthrough" \
                                            || pf_ok "no silent fallthrough"
grep -q "deadline-breach-systemctl" "$J" && pf_ok "row names the wedged step (systemctl-*)" \
                                        || pf_bad "D10 row does not name the step"

printf '\n== D11: command-substituted step breach → ONE row + rc propagation (A-4) ==\n'
# A-4 (verifier 2026-10-30). `J="$(_run_bounded_step journal-read journal_read)"`
# and `CUR_JSON="$(... journal-read-current ...)"` run inside a COMMAND
# SUBSTITUTION, so `_budget_breach`'s `exit` ends only the subshell: the
# caller sees the handler's rc (BUDGET_BREACH_RC=78, never the wrapper's 124)
# while the row/WARN/lock-release already happened. The old `|| {` arm then
# appended a SECOND halt row mislabelling the breach as a read-path refusal
# ("journal read REFUSED (size guard …)"), and the CURRENT-read site simply
# continued with an empty string. This leg runs the REAL promote.sh (from a
# scratch COPY of the scripts tree whose journal_read is slow, the only
# deterministic way to trip a step budget on a command-substituted read)
# against a staged scratch target and counts the halt rows.
A4_SCRIPTS="$PF_SB/a4-scripts"
cp -a "$PF_REPO_ROOT/scripts" "$A4_SCRIPTS"
/usr/bin/python3 - "$A4_SCRIPTS/upgrade/lib.sh" <<'PYA4'
import sys
p = sys.argv[1]
text = open(p, encoding="utf-8").read()
old = "journal_read() {\n    local jp\n"
assert old in text, "A-4 fixture: journal_read anchor missing"
new = ("journal_read() {\n"
       "    sleep 6   # A-4 fixture: slow read so the step budget trips\n"
       "    local jp\n")
open(p, "w", encoding="utf-8").write(text.replace(old, new, 1))
PYA4
A4_TARGET="$INSTALL_DIR/releases/v0.0.1-test"
mkdir -p "$A4_TARGET"
printf 'binary\n' > "$A4_TARGET/ensemble-prod"
mkjournal
( cd "$PF_SB" && env -i PATH="$PATH" HOME="$HOME" TMPDIR="$PF_SB/tmp" PORT=18081 \
      LOG_TAG=pf-a4 INSTALL_DIR="$INSTALL_DIR" PREFLIGHT_STEP_BUDGET_S=3 \
      timeout -k 5 180 bash "$A4_SCRIPTS/upgrade/promote.sh" sandbox --version v0.0.1-test \
      > "$PF_SB/a4.out" 2> "$PF_SB/a4.err" )
A4_RC=$?
A4_ROWS="$(count_rows "$J" halt)"
[ "$A4_RC" = "78" ] && pf_ok "A4 the step-budget breach HALTS the promote (rc=$A4_RC)" \
                    || pf_bad "A4 rc=$A4_RC want 78 (tail: $(tail -1 "$PF_SB/a4.err" 2>/dev/null | cut -c1-160))"
A4_DETAIL="$(/usr/bin/python3 -c '
import json,sys
j=json.load(open(sys.argv[1]))
h=[r for r in j["history"] if r.get("event")=="halt"]
print(h[-1]["detail"] if h else "")' "$J")"
case "$A4_DETAIL" in
    deadline-breach-journal-read*) pf_ok "the single row names the breach (deadline-breach-journal-read)" ;;
    *) pf_bad "A4 row is not the breach row: $A4_DETAIL" ;;
esac
[ "$A4_ROWS" = "1" ] && pf_ok "A4 halt rows=1 (the caller wrote no second, mislabelled row)" \
                     || pf_bad "A4 halt rows=$A4_ROWS want 1 (double halt row — the A-4 defect)"
grep -q "journal read REFUSED" "$J" \
    && pf_bad "A4 the caller's mislabelled 'journal read REFUSED' row was written" \
    || pf_ok "A4 no mislabelled 'journal read REFUSED' row"
grep -q "journal unreadable/TORN" "$PF_SB/a4.err" \
    && pf_bad "A4 the caller's torn-journal WARN fired on a budget breach" \
    || pf_ok "A4 the caller's torn-journal WARN stays silent on a budget breach"
[ "$(grep -c 'BUDGET BREACH' "$PF_SB/a4.err" 2>/dev/null || echo 0)" = "1" ] \
    && pf_ok "A4 exactly ONE breach WARN" || pf_bad "A4 breach WARN count=$(grep -c 'BUDGET BREACH' "$PF_SB/a4.err" 2>/dev/null || echo 0)"
[ ! -d "$INSTALL_DIR/releases/rollback.lock.d" ] \
    && pf_ok "A4 lock dir RELEASED by the breach handler" \
    || pf_bad "A4 lock dir still present after the breach"

# ── spawn-watcher proof ─────────────────────────────────────────────────────
kill "$PF_WATCHER" 2>/dev/null; wait "$PF_WATCHER" 2>/dev/null
PF_MAX_PROCS="$(cat "$PF_WATCH_DIR/max" 2>/dev/null || echo '?')"
rm -rf "$PF_WATCH_DIR"
pf_note "spawn-watcher: max concurrent /proc entries observed = ${PF_MAX_PROCS}"

pf_summary
