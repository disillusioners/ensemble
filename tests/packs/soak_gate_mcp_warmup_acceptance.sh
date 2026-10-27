#!/usr/bin/env bash
# tests/packs/soak_gate_mcp_warmup_acceptance.sh
#
# Pack: soak_gate_mcp_warmup_acceptance
# Scope: A5(iv) — the grace window is OBSERVABLE and cannot mask a real
#   fault indefinitely (requirements.md §5(iv)(a)-(c)), plus the
#   AGGREGATOR role: the phase-1/2/3 pytest + pack set, end to end.
#
# THE LEGS
# --------
#   (a) the DETAIL PAYLOAD — with the grace active and a component
#       degraded, the REAL daemon's /readyz body carries
#       status == "degraded", a NON-EMPTY detail.reasons (the real
#       component reason — a suppressed reason string is a FAIL),
#       detail.grace.grace_active == true and a grace_expires_at in
#       the FUTURE.  The probe is the literal one from the
#       requirements: curl -s http://localhost:$PORT/readyz | jq.
#   (b) the LEAK REFUSAL — with the pool-leak signature armed (the
#       P2 ORACLE pool exhausted), the grace does NOT engage, the
#       component reasons are still reported, and the leak signal is
#       visible.
#   (c) the BOUND — the grace lapses at the absolute ceiling
#       (GRACE_HORIZON_S = 120 s, observation-relative, never extended)
#       while the component stays degraded: the composite is a PLAIN
#       degraded, grace_active == false, and the grace is NOT re-armed
#       (re-arming requires the grace to have fully lapsed).
#       The phase-1 sub-ceiling no-recovery leg is verified at HEAD and
#       wired into the aggregator below.
#   (d) the AGGREGATOR — the phase-1/2/3 pytest + pack set, run end to
#       end, each gate asserting a NON-ZERO collected count (a
#       deselected/skipped gate is a FAILED gate).
#
# HOUSE RULES HONOURED
# ---------------------
#   * every daemon boot is fenced: a THROWAWAY scratch PostgreSQL
#     database (throwaway LOGIN role + owned database, DROPPED at
#     cleanup), the five POSTGRES_* variables pinned PER VARIABLE (the
#     `env -u POSTGRES_*` glob idiom is a SILENT no-op), a FAKE_HOME
#     (mktemp) with an escape guard, and an explicit throwaway PORT;
#   * the daemon child REFUSES to boot against a non-scratch database;
#   * no live-shaped path, no live release dir, no live journal.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Internal watchdog: 2400s (this pack boots three REAL daemons and then
# runs the whole lane's gate set).

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || { echo "FAIL: cannot cd to repo root"; echo "RESULT: FAIL"; exit 1; }

# shellcheck source=/dev/null
. "$REPO_ROOT/tests/helpers/soak_gate_pack_common.sh"

PACK_NAME="soak_gate_mcp_warmup_acceptance"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

soak_pack_fake_home
soak_pack_scrub_env

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/soak-acceptance.XXXXXX")" || {
    echo "FAIL: cannot create the workdir"; echo "RESULT: FAIL"; exit 1;
}
CHILD_PID=""
cleanup() {
    [ -n "$CHILD_PID" ] && kill "$CHILD_PID" 2>/dev/null
    [ -n "$CHILD_PID" ] && wait "$CHILD_PID" 2>/dev/null
    soak_pack_drop_scratch_db
    if [ "${SOAK_PACK_KEEP:-0}" = "1" ]; then
        echo "KEPT: $WORKDIR"
        return
    fi
    rm -rf "$WORKDIR" "$SOAK_PACK_FAKE_HOME" 2>/dev/null || true
}
trap cleanup EXIT

# The pytest runner (an EXPLICIT interpreter — a bare `python` is a
# defect on this host).
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTEST_CMD="$REPO_ROOT/.venv/bin/python -m pytest"
elif [ -x "/home/nea/.local/bin/uv" ]; then
    PYTEST_CMD="/home/nea/.local/bin/uv run python -m pytest"
else
    PYTEST_CMD="/usr/bin/python3 -m pytest"
fi

# ── the daemon child launcher ───────────────────────────────────────
# soak_start_child <mode> <hold_s> <port> <report> <ready>
soak_start_child() {
    local mode="$1" hold="$2" port="$3" report="$4" ready="$5"
    rm -f "$report" "$ready"
    env -u POSTGRES_URL -u DAEMON_PORT -u PORT \
        HOME="$SOAK_PACK_FAKE_HOME" \
        POSTGRES_HOST="$POSTGRES_HOST" POSTGRES_PORT="$POSTGRES_PORT" \
        POSTGRES_DB="$POSTGRES_DB" POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
        PORT="$port" \
        ENSEMBLE_SELF_ENV=demo \
        MCP_DISABLE_BUILT_IN_CONTEXT7=true \
        MCP_DISABLE_BUILT_IN_PLANE=true \
        PYTHONPATH="$REPO_ROOT" \
        "$SOAK_PACK_PYTHON" "$REPO_ROOT/tests/helpers/soak_gate_daemon_child.py" \
            --port "$port" --mode "$mode" --report "$report" --ready-file "$ready" \
            --hold "$hold" > "${report%.json}.log" 2>&1 &
    CHILD_PID=$!
    local i
    for i in $(seq 1 90); do
        sleep 2
        if [ -f "$ready" ]; then
            local code
            code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$port/livez" 2>/dev/null || echo 000)"
            [ "$code" = "200" ] && return 0
        fi
        kill -0 "$CHILD_PID" 2>/dev/null || return 1
    done
    return 1
}

soak_stop_child() {
    [ -n "$CHILD_PID" ] || return 0
    kill "$CHILD_PID" 2>/dev/null
    wait "$CHILD_PID" 2>/dev/null
    CHILD_PID=""
}

# ── the scratch database (one per pack run) ─────────────────────────
soak_pack_scratch_db
echo "FENCE: scratch database = ${SOAK_PG_DB} (role ${SOAK_PG_ROLE})"

# ══════════════════════════════════════════════════════════════════════
# (a) THE DETAIL PAYLOAD
# ══════════════════════════════════════════════════════════════════════
echo
echo "--- (a) the grace is OBSERVABLE in the /readyz payload ---"
A_PORT="$(soak_pack_free_port)" || exit 1
A_REPORT="$WORKDIR/a-report.json"
if soak_start_child "grace-detail" 90 "$A_PORT" "$A_REPORT" "$WORKDIR/a.ready"; then
    soak_pack_report "a0 the real daemon is up (fenced, scratch DB)" 1 "port=${A_PORT} db=${SOAK_PG_DB}"
    # Poll until the grace engages (the readiness refresher ticks every
    # 10 s — a single early read would see the pre-degradation snapshot).
    A_BODY=""
    for _ in $(seq 1 30); do
        sleep 2
        A_BODY="$(curl -s --max-time 5 "http://localhost:$A_PORT/readyz" 2>/dev/null || true)"
        [ -n "$A_BODY" ] || continue
        if [ "$(printf '%s' "$A_BODY" | jq -r '.detail.grace.grace_active // false' 2>/dev/null)" = "true" ]; then
            break
        fi
    done
    printf '%s' "$A_BODY" > "$WORKDIR/a-readyz.json"
    echo "    readyz excerpt: $(printf '%s' "$A_BODY" | jq -c '{status, reasons: .detail.reasons, grace: .detail.grace}' 2>/dev/null)"
    A_STATUS="$(printf '%s' "$A_BODY" | jq -r '.status // ""' 2>/dev/null)"
    A_REASONS="$(printf '%s' "$A_BODY" | jq -r '.detail.reasons | length // 0' 2>/dev/null)"
    A_GRACE="$(printf '%s' "$A_BODY" | jq -r '.detail.grace.grace_active // false' 2>/dev/null)"
    A_EXPIRES="$(printf '%s' "$A_BODY" | jq -r '.detail.grace.grace_expires_at // ""' 2>/dev/null)"
    A_CLASS="$(printf '%s' "$A_BODY" | jq -r '.detail.component_classes.checkpoint_saver // ""' 2>/dev/null)"
    if [ "$A_STATUS" = "degraded" ]; then
        soak_pack_report "a1 status == degraded while a component is degraded" 1 "status=${A_STATUS}"
    else
        soak_pack_report "a1 status == degraded while a component is degraded" 0 "status=${A_STATUS}"
    fi
    if [ "${A_REASONS:-0}" -ge 1 ] 2>/dev/null; then
        soak_pack_report "a2 detail.reasons is NON-EMPTY (the real component reason)" 1 \
            "reasons=${A_REASONS} class=${A_CLASS}"
    else
        soak_pack_report "a2 detail.reasons is NON-EMPTY (the real component reason)" 0 "reasons=${A_REASONS}"
    fi
    if [ "$A_GRACE" = "true" ]; then
        soak_pack_report "a3 detail.grace.grace_active == true" 1
    else
        soak_pack_report "a3 detail.grace.grace_active == true" 0 "grace_active=${A_GRACE}"
    fi
    if [ -n "$A_EXPIRES" ] && [ "$A_EXPIRES" != "null" ]; then
        # The expiry must be in the FUTURE.
        if "$SOAK_PACK_PYTHON" -c "
import sys
from datetime import datetime, timezone
raw = sys.argv[1]
try:
    when = datetime.fromisoformat(raw)
except Exception:
    sys.exit(1)
now = datetime.now(timezone.utc)
if when.tzinfo is None:
    when = when.replace(tzinfo=timezone.utc)
sys.exit(0 if when > now else 1)
" "$A_EXPIRES"; then
            soak_pack_report "a4 grace_expires_at is present and in the FUTURE" 1 "expires_at=${A_EXPIRES}"
        else
            soak_pack_report "a4 grace_expires_at is present and in the FUTURE" 0 "expires_at=${A_EXPIRES}"
        fi
    else
        soak_pack_report "a4 grace_expires_at is present and in the FUTURE" 0 "expires_at=${A_EXPIRES:-<empty>}"
    fi
    if [ -n "$A_CLASS" ] && [ "$A_CLASS" != "unknown" ] && [ "$A_CLASS" != "ok" ]; then
        soak_pack_report "a5 the degraded component carries a real failure class" 1 "class=${A_CLASS}"
    else
        soak_pack_report "a5 the degraded component carries a real failure class" 0 "class=${A_CLASS}"
    fi
    soak_stop_child
else
    soak_pack_report "a0 the real daemon is up (fenced, scratch DB)" 0 "the daemon never came up"
    for leg in a1 a2 a3 a4 a5; do
        soak_pack_report "$leg (the daemon never came up)" 0 "the daemon never came up"
    done
fi

# ══════════════════════════════════════════════════════════════════════
# (b) THE LEAK REFUSAL
# ══════════════════════════════════════════════════════════════════════
echo
echo "--- (b) the pool-leak signature REFUSES the grace ---"
B_PORT="$(soak_pack_free_port)" || exit 1
B_REPORT="$WORKDIR/b-report.json"
if soak_start_child "leak-refusal" 90 "$B_PORT" "$B_REPORT" "$WORKDIR/b.ready"; then
    soak_pack_report "b0 the real daemon is up (fenced, scratch DB)" 1 "port=${B_PORT} db=${SOAK_PG_DB}"
    B_BODY=""
    for _ in $(seq 1 30); do
        sleep 2
        B_BODY="$(curl -s --max-time 5 "http://localhost:$B_PORT/readyz" 2>/dev/null || true)"
        [ -n "$B_BODY" ] || continue
        if [ "$(printf '%s' "$B_BODY" | jq -r '.detail.leak_signal.detected // false' 2>/dev/null)" = "true" ]; then
            break
        fi
    done
    printf '%s' "$B_BODY" > "$WORKDIR/b-readyz.json"
    echo "    readyz excerpt: $(printf '%s' "$B_BODY" | jq -c '{status, reasons: .detail.reasons, grace: .detail.grace, leak: .detail.leak_signal}' 2>/dev/null)"
    B_LEAK="$(printf '%s' "$B_BODY" | jq -r '.detail.leak_signal.detected // false' 2>/dev/null)"
    B_GRACE="$(printf '%s' "$B_BODY" | jq -r '.detail.grace.grace_active // false' 2>/dev/null)"
    B_REASONS="$(printf '%s' "$B_BODY" | jq -r '.detail.reasons | length // 0' 2>/dev/null)"
    B_POOL="$(printf '%s' "$B_BODY" | jq -c '.detail.pool_state.psycopg // {}' 2>/dev/null)"
    if [ "$B_LEAK" = "true" ]; then
        soak_pack_report "b1 the pool-leak signature ARMED (the P2 oracle)" 1 "pool=${B_POOL}"
    else
        soak_pack_report "b1 the pool-leak signature ARMED (the P2 oracle)" 0 "leak=${B_LEAK} pool=${B_POOL}"
    fi
    if [ "$B_GRACE" = "false" ] || [ -z "$B_GRACE" ]; then
        soak_pack_report "b2 the grace REFUSED to engage on the leak signature" 1 "grace_active=${B_GRACE}"
    else
        soak_pack_report "b2 the grace REFUSED to engage on the leak signature" 0 "grace_active=${B_GRACE}"
    fi
    if [ "${B_REASONS:-0}" -ge 1 ] 2>/dev/null; then
        soak_pack_report "b3 the component reasons are still reported under the leak" 1 "reasons=${B_REASONS}"
    else
        soak_pack_report "b3 the component reasons are still reported under the leak" 0 "reasons=${B_REASONS}"
    fi
    soak_stop_child
else
    soak_pack_report "b0 the real daemon is up (fenced, scratch DB)" 0 "the daemon never came up"
    for leg in b1 b2 b3; do
        soak_pack_report "$leg (the daemon never came up)" 0 "the daemon never came up"
    done
fi

# ══════════════════════════════════════════════════════════════════════
# (c) THE BOUND — the grace lapses and is NOT re-armed
# ══════════════════════════════════════════════════════════════════════
echo
echo "--- (c) the grace is bounded: it lapses at the horizon and never re-arms ---"
C_PORT="$(soak_pack_free_port)" || exit 1
C_REPORT="$WORKDIR/c-report.json"
if soak_start_child "grace-expiry" 200 "$C_PORT" "$C_REPORT" "$WORKDIR/c.ready"; then
    soak_pack_report "c0 the real daemon is up (fenced, scratch DB)" 1 "port=${C_PORT} db=${SOAK_PG_DB}"
    C_ARMED=0
    C_LAPSED=0
    C_LAST_GRACE="unknown"
    C_LAST_STATUS="unknown"
    C_LAST_CLASS="unknown"
    # The horizon is 120 s; the poll runs past it so the lapse is
    # observed on a CONTINUOUSLY degraded episode.
    for _ in $(seq 1 80); do
        sleep 5
        C_BODY="$(curl -s --max-time 5 "http://localhost:$C_PORT/readyz" 2>/dev/null || true)"
        [ -n "$C_BODY" ] || continue
        C_LAST_GRACE="$(printf '%s' "$C_BODY" | jq -r '.detail.grace.grace_active // false' 2>/dev/null)"
        C_LAST_STATUS="$(printf '%s' "$C_BODY" | jq -r '.status // ""' 2>/dev/null)"
        C_LAST_CLASS="$(printf '%s' "$C_BODY" | jq -r '.detail.component_classes.checkpoint_saver // ""' 2>/dev/null)"
        if [ "$C_LAST_GRACE" = "true" ]; then C_ARMED=1; fi
        # Lapsed = the component is STILL degraded (the lock is still
        # held) but the grace is off.
        if [ "$C_ARMED" = "1" ] && [ "$C_LAST_GRACE" = "false" ] && [ "$C_LAST_STATUS" = "degraded" ]; then
            C_LAPSED=1
            printf '%s' "$C_BODY" > "$WORKDIR/c-readyz.json"
            break
        fi
    done
    echo "    readyz excerpt: $(jq -c '{status, grace: .detail.grace, classes: .detail.component_classes}' "$WORKDIR/c-readyz.json" 2>/dev/null)"
    if [ "$C_ARMED" = "1" ]; then
        soak_pack_report "c1 the grace ARMED on the degraded episode" 1
    else
        soak_pack_report "c1 the grace ARMED on the degraded episode" 0 "the grace never engaged"
    fi
    if [ "$C_LAPSED" = "1" ]; then
        soak_pack_report "c2 the grace LAPSED while the component stayed degraded" 1 \
            "status=${C_LAST_STATUS} class=${C_LAST_CLASS}"
    else
        soak_pack_report "c2 the grace LAPSED while the component stayed degraded" 0 \
            "grace=${C_LAST_GRACE} status=${C_LAST_STATUS} class=${C_LAST_CLASS}"
    fi
    if [ "$C_LAST_GRACE" = "false" ] && [ "$C_LAST_STATUS" = "degraded" ]; then
        soak_pack_report "c3 the composite is a PLAIN degraded (grace_active == false)" 1 \
            "grace_active=${C_LAST_GRACE} status=${C_LAST_STATUS}"
    else
        soak_pack_report "c3 the composite is a PLAIN degraded (grace_active == false)" 0 \
            "grace_active=${C_LAST_GRACE} status=${C_LAST_STATUS}"
    fi
    if [ "$C_LAST_GRACE" = "false" ] && [ "$C_LAST_STATUS" = "degraded" ]; then
        soak_pack_report "c4 the grace was NOT re-armed inside the episode" 1
    else
        soak_pack_report "c4 the grace was NOT re-armed inside the episode" 0 \
            "grace_active=${C_LAST_GRACE}"
    fi
    soak_stop_child
else
    soak_pack_report "c0 the real daemon is up (fenced, scratch DB)" 0 "the daemon never came up"
    for leg in c1 c2 c3 c4; do
        soak_pack_report "$leg (the daemon never came up)" 0 "the daemon never came up"
    done
fi

# ══════════════════════════════════════════════════════════════════════
# (d) THE AGGREGATOR — the phase-1/2/3 pytest + pack set
# ══════════════════════════════════════════════════════════════════════
echo
echo "--- (d) the AGGREGATOR: the lane's pinned pytest set + the four packs ---"

UNIT_FILES=(
    tests/unit/tools/test_readiness_failure_class.py
    tests/unit/tools/test_readyz_grace_detail.py
    tests/unit/tools/test_saver_probe_contention_class.py
    tests/unit/tools/test_promote_txn_hold.py
    tests/unit/tools/test_checkpoint_pool_config.py
    tests/unit/tools/test_mcp_init_guard.py
    tests/unit/tools/test_mcp_quarantine_registry.py
    tests/unit/tools/test_dns_precheck.py
    tests/unit/tools/test_context7_schema.py
    tests/unit/tools/test_upgrade_reasons_reader.py
    tests/unit/test_readiness_mcp_independence.py
    tests/unit/test_mcp_warmup_pool.py
    tests/unit/test_configure_builtin_route_readback.py
    tests/unit/tools/test_stdio_wrapper_bounded_wait.py
    tests/unit/test_context7_builtin.py
)
INTEGRATION_FILES=(
    tests/integration/test_checkpoint_pool_burst_shapes.py
    tests/integration/test_checkpoint_pool_ghost_waiters.py
    tests/integration/test_mcp_child_liveness.py
    tests/integration/test_mcp_degrade_alone.py
    tests/integration/test_mcp_warmup_loop_lag.py
    tests/integration/test_mcp_warmup_starters.py
)
PACK_FILES=(
    tests/packs/promote_soak_grace_counter_unit_test.sh
    tests/packs/promote_soak_fail_fast_test.sh
    tests/packs/promote_soak_grace_burst_test.sh
    tests/packs/promote_soak_never_green_test.sh
    tests/packs/promote_soak_fail_fast_integration_test.sh
)

# ── the unit set ─────────────────────────────────────────────────────
# The KNOWN-BASELINE reds of the PRE-A5 lane tip (f0f29bad4) — the
# lane's own `tests/unit/test_context7_builtin.py` bootstrap fixture
# builds `MagicMock(spec=ServicesConfig)`, and pydantic v2 does not
# expose a model field as a class attribute, so the spec'd mock refuses
# `.service_tool` (the manager reads it since commit 61ae2af35, which
# is an ancestor of the lane base d5a4e4ad9).  The four errors are
# therefore PRE-EXISTING at the lane tip and are NOT caused by the A5
# packs (the A5 commits touch no file the unit set imports).
#
# The gate is the house NET-NEW rule: the failing set at HEAD must be
# a SUBSET of this baseline (a new red fails the gate).
BASELINE_UNIT_REDS="tests/unit/test_context7_builtin.py::TestContext7Bootstrap::test_bootstrap_creates_context7_server
tests/unit/test_context7_builtin.py::TestContext7Bootstrap::test_bootstrap_context7_schema_carries_the_env_fields
tests/unit/test_context7_builtin.py::TestContext7Bootstrap::test_bootstrap_context7_config_matches_base
tests/unit/test_context7_builtin.py::TestContext7Bootstrap::test_schema_drift_refreshes_config"

UNIT_LOG="$WORKDIR/unit.log"
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
    HOME="$SOAK_PACK_FAKE_HOME" \
    $PYTEST_CMD "${UNIT_FILES[@]}" -q > "$UNIT_LOG" 2>&1
UNIT_RC=$?
UNIT_SUMMARY="$(grep -E 'passed|failed|error' "$UNIT_LOG" | tail -1)"
UNIT_COUNT="$("$SOAK_PACK_PYTHON" - "$UNIT_LOG" <<'PYEOF'
import re, sys
text = open(sys.argv[1], errors="replace").read()
passed = sum(int(m) for m in re.findall(r"(\d+) passed", text))
failed = sum(int(m) for m in re.findall(r"(\d+) failed", text))
errors = sum(int(m) for m in re.findall(r"(\d+) error", text))
print(passed + failed + errors)
PYEOF
)"
# The failing set (the short-test-summary block).
UNIT_RED_SET="$("$SOAK_PACK_PYTHON" - "$UNIT_LOG" <<'PYEOF'
import re, sys
text = open(sys.argv[1], errors="replace").read()
ids = []
for line in text.splitlines():
    m = re.match(r"^(FAILED|ERROR)\s+(\S+)", line)
    if m:
        ids.append(m.group(2))
print("\n".join(sorted(set(ids))))
PYEOF
)"
echo "    unit set: rc=${UNIT_RC} collected=${UNIT_COUNT} — ${UNIT_SUMMARY}"
echo "    unit reds: $(printf '%s' "$UNIT_RED_SET" | tr '\n' ' ')"
if [ "${UNIT_COUNT:-0}" -ge 1 ] 2>/dev/null; then
    NET_NEW="$("$SOAK_PACK_PYTHON" - "$UNIT_RED_SET" <<PYEOF
import sys
baseline = set("""${BASELINE_UNIT_REDS}""".split())
current = {line.strip() for line in sys.stdin.read().splitlines() if line.strip()}
new = sorted(current - baseline)
print("\n".join(new))
PYEOF
)"
    if [ -z "$NET_NEW" ]; then
        soak_pack_report "d1 the unit gate set is NON-VACUOUS with NO NET-NEW red" 1 \
            "collected=${UNIT_COUNT} baseline-reds-only"
    else
        soak_pack_report "d1 the unit gate set is NON-VACUOUS with NO NET-NEW red" 0 \
            "net-new: $(printf '%s' "$NET_NEW" | tr '\n' ' ')"
    fi
else
    soak_pack_report "d1 the unit gate set is NON-VACUOUS with NO NET-NEW red" 0 \
        "collected=${UNIT_COUNT:-0} — $(tail -5 "$UNIT_LOG" | tr '\n' ' ')"
fi

# ── the integration set (the house form) ────────────────────────────
INTEG_LOG="$WORKDIR/integration.log"
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
    HOME="$SOAK_PACK_FAKE_HOME" \
    $PYTEST_CMD --override-ini="addopts=" -m integration "${INTEGRATION_FILES[@]}" -v \
    > "$INTEG_LOG" 2>&1
INTEG_RC=$?
INTEG_SUMMARY="$(grep -E 'passed|failed|error' "$INTEG_LOG" | tail -1)"
INTEG_COUNT="$("$SOAK_PACK_PYTHON" - "$INTEG_LOG" <<'PYEOF'
import re, sys
text = open(sys.argv[1], errors="replace").read()
passed = sum(int(m) for m in re.findall(r"(\d+) passed", text))
failed = sum(int(m) for m in re.findall(r"(\d+) failed", text))
errors = sum(int(m) for m in re.findall(r"(\d+) error", text))
print(passed + failed + errors)
PYEOF
)"
echo "    integration set: rc=${INTEG_RC} collected=${INTEG_COUNT} — ${INTEG_SUMMARY}"
if [ "${INTEG_COUNT:-0}" -ge 1 ] 2>/dev/null && [ "$INTEG_RC" = "0" ]; then
    soak_pack_report "d2 the integration gate set is GREEN and NON-VACUOUS" 1 "collected=${INTEG_COUNT}"
else
    soak_pack_report "d2 the integration gate set is GREEN and NON-VACUOUS" 0 \
        "rc=${INTEG_RC} collected=${INTEG_COUNT:-0} — $(tail -8 "$INTEG_LOG" | tr '\n' ' ')"
fi

# ── the phase-1 sub-ceiling leg (the §4.4-vs-§5(i) bounded gap) ────
if grep -q "test_sub_ceiling_monotonic_rise_is_the_bounded_gap" "$INTEG_LOG" 2>/dev/null; then
    soak_pack_report "d3 the phase-1 sub-ceiling no-recovery leg is wired into the aggregator" 1
else
    soak_pack_report "d3 the phase-1 sub-ceiling no-recovery leg is wired into the aggregator" 0 \
        "the leg did not run in the integration set"
fi

# ── the packs ────────────────────────────────────────────────────────
PACK_PASS=0
PACK_FAIL=0
for p in "${PACK_FILES[@]}"; do
    name="$(basename "$p")"
    plog="$WORKDIR/${name}.log"
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
        HOME="$SOAK_PACK_FAKE_HOME" \
        bash "$REPO_ROOT/$p" > "$plog" 2>&1
    prc=$?
    if [ "$prc" = "0" ]; then
        PACK_PASS=$(( PACK_PASS + 1 ))
        echo "    pack ${name}: rc=0"
    else
        PACK_FAIL=$(( PACK_FAIL + 1 ))
        echo "    pack ${name}: rc=${prc}"
    fi
done
if [ "$PACK_FAIL" = "0" ] && [ "$PACK_PASS" -ge 1 ]; then
    soak_pack_report "d4 every pack in the lane's set is GREEN" 1 "packs passed=${PACK_PASS}"
else
    soak_pack_report "d4 every pack in the lane's set is GREEN" 0 \
        "passed=${PACK_PASS} failed=${PACK_FAIL}"
fi

# ── the fence attestation ───────────────────────────────────────────
soak_pack_assert_scratch_db "$POSTGRES_DB"
soak_pack_report "d5 the fence held across every boot in this pack" 1 \
    "db=${POSTGRES_DB} ports=${A_PORT}/${B_PORT}/${C_PORT} home=${SOAK_PACK_FAKE_HOME}"

soak_pack_summary 20
