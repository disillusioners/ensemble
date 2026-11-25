#!/bin/bash
# ============================================================================
# tests/test_handback_start_timeout_journal_row_test.sh
#
# C-1 runtime leg — the hand-back START-timeout journal row must LAND with
# its full detail, and the executor must NOT die.
#
# THE DEFECT (C-1, refresh merge 5ba3223ed)
# -----------------------------------------
# In scripts/upgrade/lib.sh `_supervision_handback_unit()`, the start-timeout
# arm used to read:
#
#     journal_history_append subprocess_wait_timeout \
#         # RESOLVED H3 (...)                       <-- comment INSIDE the list
#         "site=handback_start kind=budget reason=..." \
#         || _warn "subprocess_wait_timeout journal event append FAILED ..."
#
# Bash removes a backslash-newline during TOKENIZATION, BEFORE comment
# recognition — so the logical line ENDS AT THE COMMENT.  The quoted argument
# line becomes a SEPARATE COMMAND ("command not found", rc=127) and the
# `|| _warn` tail attaches to it, so the misleading "append FAILED" warning
# fires.  The `journal_history_append` call itself receives ONLY $1; `$2` is
# unset, so:
#
#   * under `set -u` (EVERY production upgrade script runs `set -u`:
#     promote.sh:69 / stage.sh:117 / rollback.sh:32 / restart.sh:63 /
#     status.sh:26) the executor shell ABORTS with "$2: unbound variable";
#   * without `set -u` the row is appended with an EMPTY detail — the
#     `site=handback_start ... NOT terminal` record is silently LOST.
#
# `bash -n` returns 0 on the broken construct (it is syntactically LEGAL) —
# that is why every text-diff gate walked past it.
#
# WHAT THIS TEST PINS
#   L1  the FIXED tree: the `site=handback_start ... NOT terminal` row lands
#       with its FULL detail (site / kind / reason / unit / rc / NOT terminal),
#       the detail is NOT empty, and the executor does NOT abort with an
#       unbound-variable error.
#   L2  NON-VACUITY: the SAME assertions are run against a scratch copy of
#       lib.sh with the PRE-FIX shape reconstructed, and they MUST FAIL
#       there.  A harness that cannot fail is worthless; this leg proves the
#       harness can detect the defect it claims to detect.
#
# FENCES
#   - The install dir is a `mktemp -d` FAKE under /tmp; an escape guard
#     refuses any path that is not under /tmp or that names a live install.
#     (Two live installs were deleted by an unfenced HOME-derived fixture on
#     2026-10-12 — never resolve a live-shaped path here.)
#   - POSTGRES_* is scrubbed ONE VARIABLE AT A TIME (a `POSTGRES_*` glob in
#     `env -u` is a SILENT NO-OP — bash does not glob env-var names).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 is pinned EXPLICITLY (never
#     `env -u`: stripping the ambient guard lets the boot sweep SIGTERM /
#     SIGKILL live processes).
#   - The live install, the `current` symlink, state.json and the production
#     journal are NEVER touched: the journal under test is the fake
#     install's own releases/state.json.
#   - NO daemon child is spawned (the systemctl stub is a plain script), but
#     the boot-sweep guard is pinned anyway.
#
# Run:  bash tests/test_handback_start_timeout_journal_row_test.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"

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
    return 0
}
assert_contains() {
    case "$3" in
        *"$2"*) _pass "$1" ;;
        *) _fail "$1" "contains '$2'" "$3" ;;
    esac
}
assert_not_contains() {
    case "$3" in
        *"$2"*) _fail "$1" "does NOT contain '$2'" "$3" ;;
        *) _pass "$1" ;;
    esac
}
assert_eq() {
    if [ "$2" = "$3" ]; then _pass "$1"; else _fail "$1" "$2" "$3"; fi
}

# ── fences ─────────────────────────────────────────────────────────────────
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT 2>/dev/null || true

SCRATCH="$(mktemp -d -t hb-start-row.XXXXXX)"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

# ESCAPE GUARD — the fake install must live under /tmp and must never name a
# live install.  Two live installs were deleted by an unfenced HOME-derived
# fixture on 2026-10-12; this guard is the fence that prevents a repeat.
case "$SCRATCH" in
    /tmp/*) ;;
    *) printf 'FENCE ABORT: scratch dir is not under /tmp: %s\n' "$SCRATCH" >&2; exit 78 ;;
esac
case "$SCRATCH" in
    *agents-ensemble*) printf 'FENCE ABORT: scratch dir names a live install: %s\n' "$SCRATCH" >&2; exit 78 ;;
esac

# ── the systemctl stub (hangs on `start` so the bounded wait returns 124) ──
STUBDIR="$SCRATCH/stub"
mkdir -p "$STUBDIR"
cat > "$STUBDIR/systemctl" <<'STUB'
#!/bin/bash
echo "$*" >> "${SC_LOG:-/dev/null}"
sub="$1"; shift
case "$sub" in
    start)
        if [ "${SC_HANG:-0}" = "1" ] && [ "${SC_HANG_SUB:-start}" = "start" ]; then
            exec sleep 999
        fi
        exit "${SC_START_RC:-0}" ;;
    is-active) echo "${SC_IS_ACTIVE:-inactive}"; exit "${SC_IS_ACTIVE_RC:-0}" ;;
    show)
        props=""; unit=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -p) props="$props $2"; shift 2 ;;
                --value) shift ;;
                *) unit="$1"; shift ;;
            esac
        done
        case "$props" in
            *" ActiveState "*)
                printf 'ActiveState=%s\n' "${SC_ACTIVESTATE:-active}"
                printf 'SubState=%s\n' "${SC_SUBSTATE:-running}"
                printf 'MainPID=%s\n' "${SC_MAINPID:-0}"
                exit 0 ;;
        esac
        case "$1" in
            MainPID) echo "${SC_MAINPID:-0}" ;;
            *) echo "" ;;
        esac
        exit 0 ;;
    reset-failed|stop|kill|daemon-reload|enable) exit 0 ;;
    *) exit 0 ;;
esac
STUB
chmod +x "$STUBDIR/systemctl"

# a PATH-stub lsof + curl so the hand-back verify is not the subject here
LSOF_OK="$SCRATCH/lsofok"
mkdir -p "$LSOF_OK"
printf '#!/bin/bash\nexit 0\n' > "$LSOF_OK/lsof"
cat > "$LSOF_OK/curl" <<'CURL'
#!/bin/bash
out=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; -w) shift;; --max-time) shift;; -sS) shift;; *) shift;; esac; done
[ -n "$out" ] && printf '{"status":"alive","version":"0.28.5"}\n' > "$out"
printf '200'
CURL
chmod +x "$LSOF_OK/lsof" "$LSOF_OK/curl"

# ── the fake install fixture ──────────────────────────────────────────────
make_fixture() {
    local d="$1"
    rm -rf "$d"; mkdir -p "$d/releases" "$d/data"
    printf '#!/bin/bash\nexit 0\n' > "$d/launcher.sh"
    chmod +x "$d/launcher.sh"
    cat > "$d/releases/state.json" <<'JSON'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JSON
}

# ── the driver ─────────────────────────────────────────────────────────────
# `set -u` is INSIDE the driver: every production upgrade script runs it, and
# that is the posture under which the defect was an executor ABORT.
run_handback() {
    local fixdir="$1" libdir="$2" setup="$3"
    PATH="$LSOF_OK:$PATH" bash -c '
        set -u
        export INSTALL_DIR="'"$fixdir"'"
        export SYSTEMCTL_BIN="'"$STUBDIR"'/systemctl"
        export SC_LOG="'"$fixdir"'/sc.log"
        export PORT=19987
        export LIVEZ_BUDGET_S=2
        export HANDBACK_VERIFY_BUDGET_S=2
        export HANDBACK_START_BUDGET_S=2
        unset ENSEMBLE_SUPERVISION 2>/dev/null || true
        . "'"$libdir"'/lib.sh"
        '"$setup"'
        _supervision_handback_unit "ensemble-hang.service" "9999" "0" "/dev/null"
        echo "hb-rc=$?"
    ' 2>&1
}

START_SETUP='SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED'

# ===========================================================================
# L1 — the FIXED tree
# ===========================================================================
CFIX="$SCRATCH/fixed"
make_fixture "$CFIX"
SC_HANG=1 SC_HANG_SUB=start; export SC_HANG SC_HANG_SUB
L1_OUT="$(run_handback "$CFIX" "$UPGRADE_DIR" "$START_SETUP")"
unset SC_HANG SC_HANG_SUB
L1_JOURNAL="$(cat "$CFIX/releases/state.json")"

assert_contains "L1: the executor reached the end of the run (no abort)" "hb-rc=" "$L1_OUT"
assert_not_contains "L1: NO unbound-variable abort in the executor" "unbound variable" "$L1_OUT"
assert_not_contains "L1: NO misleading 'journal event append FAILED' warning" \
    "journal event append FAILED" "$L1_OUT"
assert_contains "L1: the site=handback_start row LANDED" "site=handback_start" "$L1_JOURNAL"
assert_contains "L1: the row carries kind=budget" "kind=budget" "$L1_JOURNAL"
assert_contains "L1: the row carries the DYNAMIC reason" \
    "reason=child: timeout(HANDBACK_START_BUDGET_S=2)" "$L1_JOURNAL"
assert_contains "L1: the row carries the TRUE rc" "rc=124" "$L1_JOURNAL"
assert_contains "L1: the row carries the unit" "unit=ensemble-hang.service" "$L1_JOURNAL"
assert_contains "L1: the row carries the NOT-terminal tail" "NOT terminal" "$L1_JOURNAL"
assert_not_contains "L1: the row detail is NOT empty" '"detail":""' "$L1_JOURNAL"

# ===========================================================================
# L2 — NON-VACUITY: the SAME assertions against the PRE-FIX shape
# ===========================================================================
# A scratch copy of lib.sh with the comment block moved back INTO the
# argument list (the exact pre-fix byte structure).  The harness MUST fail
# there — otherwise it cannot detect the defect it claims to detect.
PREFIX_DIR="$SCRATCH/prefix"
mkdir -p "$PREFIX_DIR"
cp "$UPGRADE_DIR/lib.sh" "$PREFIX_DIR/lib.sh"
cp "$UPGRADE_DIR/journal_splice.py" "$PREFIX_DIR/journal_splice.py"
python3 - "$PREFIX_DIR/lib.sh" <<'PYEOF'
import re
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    src = fh.read()

# 1. Is the shipped tree ALREADY in the pre-fix shape (the command line
#    immediately followed by the RESOLVED H3 comment block)?  If so there is
#    nothing to reconstruct — the subject is already the broken construct.
already_broken = re.search(
    r"(?m)^[ \t]*journal_history_append subprocess_wait_timeout \\\n"
    r"(?:[ \t]*#[^\n]*\n)*?[ \t]*#[^\n]*RESOLVED H3[^\n]*\n",
    src,
)
if already_broken:
    print("the shipped tree is ALREADY in the pre-fix shape — using it as-is")
    sys.exit(0)

# 2. Otherwise reconstruct it: find the FIXED shape (the comment block
#    directly ABOVE the command line) and move the command line back ABOVE
#    the comment block.
pat = re.compile(
    r"(?m)^(?P<cmt>(?:[ \t]*#[^\n]*\n)*?)(?P<cmd>[ \t]*journal_history_append subprocess_wait_timeout \\\n)"
)
done = False
for m in pat.finditer(src):
    if "RESOLVED H3" not in m.group("cmt"):
        continue
    # PRE-FIX byte structure: the command line FIRST, then the comment block
    # INSIDE its argument list.
    src = src[: m.start()] + m.group("cmd") + m.group("cmt") + src[m.end() :]
    done = True
    break
if not done:
    sys.exit("FATAL: the pre-fix shape could not be reconstructed")
with open(path, "w", encoding="utf-8") as fh:
    fh.write(src)
print("pre-fix shape reconstructed")
PYEOF
PRE_SHAPE_OK=$?

if [ "$PRE_SHAPE_OK" -ne 0 ]; then
    _fail "L2: the pre-fix shape could be reconstructed" "reconstruction succeeds" "reconstruction failed"
else
    CFIX2="$SCRATCH/prefixfix"
    make_fixture "$CFIX2"
    SC_HANG=1 SC_HANG_SUB=start; export SC_HANG SC_HANG_SUB
    L2_OUT="$(run_handback "$CFIX2" "$PREFIX_DIR" "$START_SETUP")"
    unset SC_HANG SC_HANG_SUB
    L2_JOURNAL="$(cat "$CFIX2/releases/state.json")"

    # The defect must be VISIBLE on the pre-fix shape.  Under `set -u` — the
    # posture EVERY production upgrade script runs — the pre-fix construct
    # ABORTS the executor INSIDE `journal_history_append` (its `$2` is
    # unset), so the row is never written AT ALL: the `site=handback_start
    # ... NOT terminal` record is LOST, not merely emptied.
    L2_DETECTED=0
    case "$L2_OUT" in
        *"unbound variable"*) L2_DETECTED=1 ;;
    esac
    case "$L2_JOURNAL" in
        *"site=handback_start"*) : ;;
        *) L2_DETECTED=1 ;;
    esac
    if [ "$L2_DETECTED" = "1" ]; then
        _pass "L2 (non-vacuity): the pre-fix shape is DETECTED by this harness"
    else
        _fail "L2 (non-vacuity): the pre-fix shape is detected" \
            "the pre-fix run shows the defect" "the pre-fix run looked clean"
    fi
    assert_contains "L2 (non-vacuity): the pre-fix executor ABORTS with an unbound-variable error" \
        "unbound variable" "$L2_OUT"
    assert_not_contains "L2 (non-vacuity): the pre-fix journal carries NO site=handback_start row" \
        "site=handback_start" "$L2_JOURNAL"
fi

# ── summary ────────────────────────────────────────────────────────────────
echo
echo "== summary =="
printf 'PASS=%d FAIL=%d\n' "$PASS" "$FAIL"
if [ "$FAIL" -ne 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
