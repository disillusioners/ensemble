#!/bin/bash
# ============================================================================
# tests/test_supervision_twins.sh — TWINS-AGREE DRIFT GUARD
# (supervision-detection commission P5, mission matrix items 10 + 12)
# ============================================================================
# The classification ladder + the declared×verified outcome map are
# implemented TWICE — the shell twin (scripts/upgrade/lib.sh
# supervision_classify / supervision_map_outcome, pipeline-side) and the
# python twin (daemon/tools/upgrade_journal.py supervision_detect /
# supervision_outcome, daemon-side). The ~6-line cgroup-leaf parse is
# DELIBERATELY duplicated (cross-language seam — do not "DRY" them). This
# suite fails if EITHER twin drifts:
#
#   A. outcome map — all 19 cells: outcome AND reason STRING byte-identical
#      across supervision_map_outcome (bash) and supervision_outcome
#      (python), plus the DUAL_FIGHT/unknown arms.
#   B. classification ladder — env-matrix cells × synthetic leaves: the
#      shell classifier (with owned-pid/cgroup stubs feeding the SAME
#      synthetic leaf the python leaf-table sees) agrees with
#      _supervision_classify_leaf + the env ladder on (state, unit, mode).
#   C. cgroup-leaf parse — the twins-pinned ~6-line /proc parse: every
#      READABLE pid on this host yields the SAME leaf from both twins'
#      parsers (real-shape drift guard; the shell parse reads
#      /proc/<pid>/cgroup directly, the python parse via Path.read_text),
#      plus a synthetic v1/v2 line matrix driven through the shell parse
#      grammar vs the python parse on the same raw lines.
#
# Run:
#   bash tests/test_supervision_twins.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
PYTWIN="$REPO_ROOT/tests/unit/tools/test_supervision_python.py"

PASS=0
FAIL=0
FAILED_TESTS=""
_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass "$name"; else _fail "$name" "$expected" "$actual"; fi
}
section() { printf '\n== %s ==\n' "$1"; }

HOST_HAS_SYSTEMD=0
[ "$(uname -s)" = "Linux" ] && [ -d /run/systemd/system ] && HOST_HAS_SYSTEMD=1

WORK="$(mktemp -d -t suptwins.XXXXXX)"

# the python twin, loaded the same isolated way the exemplar uses — a tiny
# runner script that prints one CSV line per request on stdin rows
# "declared|verified|unit"
PY_RUNNER="$WORK/outcome_runner.py"
cat > "$PY_RUNNER" <<'PYEOF'
import sys
sys.path.insert(0, "")
import importlib.machinery, importlib.util, types
from pathlib import Path
REPO = Path(sys.argv[1])
m = types.ModuleType("daemon"); m.__path__ = ["daemon"]
c = types.ModuleType("daemon.constants"); c.is_reserved_source = lambda x: False
sys.modules["daemon"] = m; sys.modules["daemon.constants"] = c
loader = importlib.machinery.SourceFileLoader(
    "uj_twins", str(REPO / "daemon" / "tools" / "upgrade_journal.py"))
spec = importlib.util.spec_from_loader("uj_twins", loader)
uj = importlib.util.module_from_spec(spec); sys.modules["uj_twins"] = uj
loader.exec_module(uj)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    declared, verified, unit = (line.split("|") + ["", ""])[:3]
    o = uj.supervision_outcome(declared, verified, unit)
    print(f"{declared}|{verified}|{unit}|{o.outcome}|{o.reason}")
PYEOF

# shell twin: source lib.sh quietly; print the same CSV shape
sh_outcome() { # <declared|verified|unit>
    printf '%s' "$1" | {
        IFS='|' read -r d v u
        out="$(INSTALL_DIR="$WORK" bash -c '
            . "$1" >/dev/null 2>&1
            supervision_map_outcome "$2" "$3" "$4"
        ' _ "$UPGRADE_DIR/lib.sh" "$d" "$v" "$u")"
        printf '%s|%s|%s|%s\n' "$d" "$v" "$u" "${out%%|*}"
        printf 'REASON:%s\n' "${out#*|}"
    }
}

# ===========================================================================
section "A — outcome map: 19 cells, outcome AND reason byte-identical"

CELLS='
script|SCRIPT_NOHUP|
script|UNIT_MANAGED|
script|SCOPE_SURVIVOR|
unit|UNIT_MANAGED|ensemble-tw.service
unit|UNIT_MANAGED|
unit|SCRIPT_NOHUP|
unit|SCOPE_SURVIVOR|
auto|SCRIPT_NOHUP|
auto|UNIT_MANAGED|ensemble-tw2.service
auto|SCOPE_SURVIVOR|
script|DUAL_FIGHT|
unit|DUAL_FIGHT|ensemble-tw3.service
auto|DUAL_FIGHT|
|SCRIPT_NOHUP|
script|GARBAGE_STATE|
unit|GARBAGE_STATE|
auto|GARBAGE_STATE|
SCRIPT|weird_state|
Unit|UNIT_MANAGED|ensemble-tw4.service
'
PY_LINES="$(printf '%s\n' "$CELLS" | grep -v '^$' | python3 "$PY_RUNNER" "$REPO_ROOT")"

N=0
while IFS= read -r cell; do
    [ -n "$cell" ] || continue
    N=$((N + 1))
    # line-anchored: an empty-declared cell's pattern would otherwise
    # substring-collide with the same-verified named cells
    py_line="$(printf '%s\n' "$PY_LINES" | grep "^$cell|")"
    sh_blk="$(sh_outcome "$cell")"
    sh_line="$(printf '%s\n' "$sh_blk" | head -1)"
    sh_reason="$(printf '%s\n' "$sh_blk" | sed -n 's/^REASON://p')"
    py_reason="${py_line#*|*|*|}"; py_reason="${py_line##*|}"
    py_out="$(printf '%s' "$py_line" | awk -F'|' '{print $(NF-1)}')"
    sh_out="$(printf '%s' "$sh_line" | awk -F'|' '{print $(NF)}')"
    assert_eq "A cell $N [$cell] outcome" "$py_out" "$sh_out"
    assert_eq "A cell $N [$cell] reason byte-identical" "$py_reason" "$sh_reason"
done <<EOF
$CELLS
EOF

# ===========================================================================
section "B — classification ladder agreement (env matrix × synthetic leaves)"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    # python leaf-table expectations for the same synthetic leaves
    py_leaf() { # <leaf> → "state|unit"
        python3 "$WORK/leaf_runner.py" "$1"
    }
    cat > "$WORK/leaf_runner.py" <<'PYEOF'
import importlib.machinery, importlib.util, types, sys
from pathlib import Path
REPO = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/nonexistent")
m = types.ModuleType("daemon"); m.__path__ = ["daemon"]
c = types.ModuleType("daemon.constants"); c.is_reserved_source = lambda x: False
sys.modules["daemon"] = m; sys.modules["daemon.constants"] = c
loader = importlib.machinery.SourceFileLoader(
    "uj_twins2", str(Path(sys.argv[2]) / "daemon" / "tools" / "upgrade_journal.py"))
spec = importlib.util.spec_from_loader("uj_twins2", loader)
uj = importlib.util.module_from_spec(spec); sys.modules["uj_twins2"] = uj
loader.exec_module(uj)
state, unit = uj._supervision_classify_leaf(sys.argv[1])
mode = "unit" if state == "UNIT_MANAGED" else "script"
print(f"{state}|{unit}|{mode}")
PYEOF

    LB_MATRIX='
ensemble-live.service
ensemble-upgrade-r-abc.scope
session-1.scope
init.scope
user.slice
user-1000.slice
machine.slice
foo-machine.slice
unknown-leaf-shape
'
    while IFS= read -r leaf; do
        [ -n "$leaf" ] || continue
        want="$(python3 "$WORK/leaf_runner.py" "$leaf" "$REPO_ROOT")"
        want_state="$(printf '%s' "$want" | cut -d'|' -f1)"
        want_unit="$(printf '%s' "$want" | cut -d'|' -f2)"
        want_mode="$(printf '%s' "$want" | cut -d'|' -f3)"
        # shell classifier with the synthetic leaf stubbed at the same
        # seam the classify suite uses
        got="$(
            INSTALL_DIR="$WORK" ENSEMBLE_SUPERVISION=auto bash -c '
                . "$1" >/dev/null 2>&1
                _supervision_owned_pids() { printf "%s\n" 424242; }
                _supervision_pid_cgroup_leaf() { printf "%s\n" "$SUP_TWINS_LEAF"; return 0; }
                export SUP_TWINS_LEAF="'"$leaf"'"
                supervision_classify >/dev/null 2>&1
                printf "%s|%s|%s\n" "${SUPERVISION_MODE:-}" "${SUPERVISION_STATE:-}" "${SUPERVISION_UNIT:-}"
            ' _ "$UPGRADE_DIR/lib.sh"
        )"
        got_mode="$(printf '%s' "$got" | cut -d'|' -f1)"
        got_state="$(printf '%s' "$got" | cut -d'|' -f2)"
        got_unit="$(printf '%s' "$got" | cut -d'|' -f3)"
        assert_eq "B leaf [$leaf] state" "$want_state" "$got_state"
        assert_eq "B leaf [$leaf] unit" "$want_unit" "$got_unit"
        assert_eq "B leaf [$leaf] mode" "$want_mode" "$got_mode"
    done <<EOF
$LB_MATRIX
EOF

    # env-ladder top agreement (deterministic, no /proc): the resolved
    # (state, unit) for explicit/opt-out/garbage must match the python
    # env-seam detector cell-for-cell
    env_cell() { # <ENSEMBLE_SUPERVISION=value> <extra python env k=v>
        local sup="$1" extra="$2"
        local py sh
        py="$(python3 - "$REPO_ROOT" "$sup" "$extra" <<'PYEOF'
import importlib.machinery, importlib.util, types, sys
from pathlib import Path
m = types.ModuleType("daemon"); m.__path__ = ["daemon"]
c = types.ModuleType("daemon.constants"); c.is_reserved_source = lambda x: False
sys.modules["daemon"] = m; sys.modules["daemon.constants"] = c
loader = importlib.machinery.SourceFileLoader(
    "uj_twins3", str(Path(sys.argv[1]) / "daemon" / "tools" / "upgrade_journal.py"))
spec = importlib.util.spec_from_loader("uj_twins3", loader)
uj = importlib.util.module_from_spec(spec); sys.modules["uj_twins3"] = uj
loader.exec_module(uj)
env = {}
if sys.argv[2] != "-":
    env["ENSEMBLE_SUPERVISION"] = sys.argv[2]
if sys.argv[3] != "-":
    k, _, v = sys.argv[3].partition("=")
    env[k] = v
det = uj._supervision_detect_real(env)
print(f"{det.state}|{det.unit}|{det.mode}")
PYEOF
)"
        sh="$(INSTALL_DIR="$WORK" bash -c '
            . "$1" >/dev/null 2>&1
            _supervision_owned_pids() { return 0; }
            _supervision_pid_cgroup_leaf() { return 1; }
            [ "$2" = "-" ] || export "$2"
            [ -z "${3:-}" ] || [ "$3" = "-" ] || export "$3"
            supervision_classify >/dev/null 2>&1
            printf "%s|%s|%s\n" "${SUPERVISION_STATE:-}" "${SUPERVISION_UNIT:-}" "${SUPERVISION_MODE:-}"
        ' _ "$UPGRADE_DIR/lib.sh" "ENSEMBLE_SUPERVISION=${sup}" "${extra:+$extra}")"
        assert_eq "B env [$sup ${extra:-}] twins agree" "$py" "$sh"
    }
    env_cell unit 'ENSEMBLE_RESTART_UNIT=ensemble-agree.service'
    env_cell script -
    env_cell 0 -
    env_cell false -
    env_cell no -
    env_cell off -
    env_cell garbage -
else
    _skip() { :; }
    printf 'SKIP: B ladder agreement — FENCE: no /run/systemd/system on this host\n' >&2
fi

# ===========================================================================
section "C — cgroup-leaf parse twins (the ~6-line duplication)"

# C1. REAL-shape agreement: every readable /proc/<pid>/cgroup on this host
#     yields the same leaf from both twins' parsers.
#     M2 (review cycle 1 fixback — de-flake): the leg used to let each
#     twin's parser do its OWN /proc/<pid>/cgroup read; a pid EXITING
#     between the python and shell reads produced leaf-vs-<none> and
#     counted as DRIFT (observed 1/168). The pid-taking twin functions
#     hardcode /proc and admit no content injection (stubbing them would
#     void the pin), so the same-bytes guarantee is enforced AROUND the
#     leg instead: the file is snapshotted BEFORE, both REAL parsers run,
#     and the file is re-read AFTER — a vanished pid or a changed cgroup
#     mid-leg is SKIP-not-drift (the drift guard must not cry wolf); only
#     a stable-content disagreement between the real parsers counts.
#     Both-parse-empty normalizes to agreement (shell "" and python
#     "<none>" are the same no-leaf verdict on identical bytes).
py_leaf_of_pid() { # <pid>
    python3 - "$REPO_ROOT" "$1" <<'PYEOF'
import importlib.machinery, importlib.util, types, sys
from pathlib import Path
m = types.ModuleType("daemon"); m.__path__ = ["daemon"]
c = types.ModuleType("daemon.constants"); c.is_reserved_source = lambda x: False
sys.modules["daemon"] = m; sys.modules["daemon.constants"] = c
loader = importlib.machinery.SourceFileLoader(
    "uj_twins4", str(Path(sys.argv[1]) / "daemon" / "tools" / "upgrade_journal.py"))
spec = importlib.util.spec_from_loader("uj_twins4", loader)
uj = importlib.util.module_from_spec(spec); sys.modules["uj_twins4"] = uj
loader.exec_module(uj)
leaf = uj._supervision_read_cgroup_leaf(int(sys.argv[2]))
print(leaf if leaf else "<none>")
PYEOF
}
sh_leaf_of_pid() { # <pid> — the shell twin's parse, verbatim from lib.sh
    INSTALL_DIR="$WORK" bash -c '
        . "$1" >/dev/null 2>&1
        _supervision_pid_cgroup_leaf "$2" || true
    ' _ "$UPGRADE_DIR/lib.sh" "$1"
}

AGREE=0; DISAGREE=0; UNREADABLE=0; VANISHED=0
for pid in $(pgrep -u "$(id -u)" | head -60); do
    snap_before="$(cat "/proc/$pid/cgroup" 2>/dev/null || true)"
    if [ -z "$snap_before" ]; then
        UNREADABLE=$((UNREADABLE + 1)); continue
    fi
    pl="$(py_leaf_of_pid "$pid")"
    sl="$(sh_leaf_of_pid "$pid")"
    snap_after="$(cat "/proc/$pid/cgroup" 2>/dev/null || true)"
    if [ -z "$snap_after" ] || [ "$snap_before" != "$snap_after" ]; then
        # the pid left (or its cgroup migrated) between the parsers'
        # reads — the parsers did NOT necessarily see the same bytes;
        # not a drift signal
        VANISHED=$((VANISHED + 1)); continue
    fi
    [ -n "$pl" ] || pl="<none>"
    [ -n "$sl" ] || sl="<none>"
    if [ "$pl" = "$sl" ]; then
        AGREE=$((AGREE + 1))
    else
        DISAGREE=$((DISAGREE + 1))
        printf 'C1 DRIFT at pid %s (stable content): python=%s shell=%s\n' "$pid" "$pl" "$sl" >&2
    fi
done
[ "$DISAGREE" -eq 0 ] && _pass "C1 real-pid parse agreement ($AGREE pids agree, $UNREADABLE unreadable, $VANISHED vanished-mid-leg skipped)" \
    || _fail "C1 real-pid parse agreement" "0 drifts" "$DISAGREE drifts"
[ "$AGREE" -gt 0 ] && _pass "C1 at least one real pid exercised" \
    || _fail "C1 at least one real pid exercised" ">0" "0"

# C2. synthetic line-shape matrix: the shell twin's parse grammar (tail -n1,
#     ${line##*:}, ${path##*/}) vs the python parse (last line, split(":",2)
#     [-1], rsplit("/",1)[-1]) on the same RAW lines. The shell grammar is
#     exercised through lib.sh's own function against a REAL pid whose
#     cgroup file IS that shape when available; synthetic lines drive the
#     documented grammar extraction — anchored to the load-bearing steps so
#     a lib.sh parse edit that changes semantics breaks the anchor.
LIB_PARSE_ANCHORED=0
if grep -q 'path="${line##\*:}"' "$UPGRADE_DIR/lib.sh" \
   && grep -q 'leaf="${path##\*/}"' "$UPGRADE_DIR/lib.sh"; then
    LIB_PARSE_ANCHORED=1
    _pass "C2 lib.sh parse steps anchored (path/leaf expansions present)"
else
    _fail "C2 lib.sh parse steps anchored" "present" "MISSING — the twins-pinned parse was edited"
fi
PY_PARSE_ANCHORED=0
if grep -q 'split(":", 2)\[-1\]' "$REPO_ROOT/daemon/tools/upgrade_journal.py" \
   && grep -q 'rsplit("/", 1)\[-1\]' "$REPO_ROOT/daemon/tools/upgrade_journal.py"; then
    PY_PARSE_ANCHORED=1
    _pass "C2 python parse steps anchored (split/rsplit present)"
else
    _fail "C2 python parse steps anchored" "present" "MISSING — the twins-pinned parse was edited"
fi
if [ "$LIB_PARSE_ANCHORED" = "1" ] && [ "$PY_PARSE_ANCHORED" = "1" ]; then
    # drive BOTH grammars on the same synthetic raw lines and compare
    while IFS= read -r raw; do
        [ -n "$raw" ] || continue
        sl="$(printf '%s\n' "$raw" | bash -c '
            line="$(cat)"; path="${line##*:}"; leaf="${path##*/}"
            [ -n "$leaf" ] && printf "%s" "$leaf" || printf "<none>"
        ')"
        pl="$(printf '%s\n' "$raw" | python3 -c '
import sys
raw = sys.stdin.read().strip()
lines = raw.splitlines()
line = lines[-1] if lines else ""
path = line.split(":", 2)[-1]
leaf = path.rstrip("/").rsplit("/", 1)[-1]
print(leaf if leaf else "<none>")
')"
        assert_eq "C2 parse of [$raw]" "$pl" "$sl"
    done <<'EOF'
0::/system.slice/ensemble-live.service
0::/system.slice/ensemble-upgrade-r-20260929-abc.scope
0::/user.slice/user-1000.slice/session-3.scope
3:name=systemd:/system.slice/ensemble-v1.service
2:cpu:/system.slice/foo.scope
0::/init.scope
0::/
1:name=systemd:/
EOF
fi

rm -rf "$WORK"

# ===========================================================================
section "summary"
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '\n=== ALL TESTS PASSED ===\n'
