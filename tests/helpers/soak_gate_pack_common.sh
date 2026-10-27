#!/usr/bin/env bash
# tests/helpers/soak_gate_pack_common.sh
#
# Shared helpers for the four soak-gate-mcp-warmup A5 packs
# (tests/packs/promote_soak_grace_burst_test.sh,
#  tests/packs/promote_soak_never_green_test.sh,
#  tests/packs/promote_soak_fail_fast_integration_test.sh,
#  tests/packs/soak_gate_mcp_warmup_acceptance.sh).
#
# WHAT THIS FILE OWNS
# -------------------
# 1. THE FENCE. Every pack that boots a daemon goes through
#    ``soak_pack_fence``:
#      * a FAKE_HOME (mktemp) with an ESCAPE GUARD that refuses to run
#        when the resolved path is under the REAL home (the house rule:
#        a test must NEVER resolve a live-shaped HOME-derived path — two
#        live installs were deleted that way);
#      * a PER-VARIABLE POSTGRES_* scrub — ``env -u POSTGRES_*`` is a
#        SILENT NO-OP (bash does not glob env names), so every variable
#        is unset by NAME;
#      * an explicit PORT (never the ambient DAEMON_PORT);
#      * a PRE-BOOT ASSERTION: the pack refuses to start unless the
#        resolved database name is SCRATCH-SHAPED (the degrade-alone
#        integration test's refuses-non-scratch-DB guard, reused).
# 2. THE SCRATCH DATABASE. A throwaway LOGIN role + an owned database
#    created over TCP (never a /tmp socket path — psycopg would
#    mis-parse "path:port" as a dbname) and DROPPED at cleanup.
# 3. THE FAKE DAEMON. A scripted HTTP stub (tests/helpers/
#    soak_gate_fake_daemon.py) — start/stop + the request log.
#
# HOUSE RULES HONOURED HERE
# --------------------------
#   * no bare ``python`` (this host has only /usr/bin/python3; the
#     worktree venv is at $REPO_ROOT/.venv when present);
#   * every daemon boot is fenced and the fence is ASSERTED INSIDE the
#     harness (the daemon itself refuses a non-scratch database);
#   * nothing here ever resolves a live install path, a live release
#     directory, the live promote journal or the live daemon's port.

# ── the repo root ────────────────────────────────────────────────────
SOAK_PACK_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SOAK_PACK_REPO_ROOT

# ── the interpreter (never a bare ``python``) ────────────────────────
if [ -x "$SOAK_PACK_REPO_ROOT/.venv/bin/python" ]; then
    SOAK_PACK_PYTHON="$SOAK_PACK_REPO_ROOT/.venv/bin/python"
elif [ -x "/home/nea/.local/bin/uv" ]; then
    SOAK_PACK_PYTHON="/home/nea/.local/bin/uv run python"
else
    SOAK_PACK_PYTHON="/usr/bin/python3"
fi
export SOAK_PACK_PYTHON

# ── the pass/fail counters ───────────────────────────────────────────
SOAK_PACK_PASS=0
SOAK_PACK_FAIL=0

soak_pack_report() {
    # soak_pack_report <name> <ok:1|0> [detail]
    if [ "$2" = "1" ]; then
        SOAK_PACK_PASS=$(( SOAK_PACK_PASS + 1 ))
        printf 'PASS: %s%s\n' "$1" "${3:+ — ${3}}"
    else
        SOAK_PACK_FAIL=$(( SOAK_PACK_FAIL + 1 ))
        printf 'FAIL: %s%s\n' "$1" "${3:+ — ${3}}"
    fi
}

soak_pack_summary() {
    # soak_pack_summary <min-assertions> — exits 0 on PASS, 1 on FAIL.
    echo
    echo "======================================"
    echo "PASS: ${SOAK_PACK_PASS}  FAIL: ${SOAK_PACK_FAIL}"
    echo "======================================"
    if [ "$SOAK_PACK_FAIL" -gt 0 ]; then
        echo "RESULT: FAIL"
        exit 1
    fi
    if [ "$SOAK_PACK_PASS" -lt "${1:-1}" ]; then
        echo "RESULT: FAIL (vacuous — only ${SOAK_PACK_PASS} assertions ran)"
        exit 1
    fi
    echo "RESULT: PASS"
    exit 0
}

# ── 1. THE FAKE_HOME FENCE ──────────────────────────────────────────
soak_pack_fake_home() {
    # Creates $SOAK_PACK_FAKE_HOME (a mktemp dir) + refuses to continue
    # when it resolves under the REAL home.
    local real_home="${HOME:-/root}"
    SOAK_PACK_FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/soak-pack-home.XXXXXX")" || {
        echo "FAIL: cannot create the FAKE_HOME"; exit 1;
    }
    local resolved real_resolved
    resolved="$(cd "$SOAK_PACK_FAKE_HOME" && pwd -P)"
    real_resolved="$(cd "$real_home" && pwd -P)"
    case "$resolved" in
        "$real_resolved"/*)
            echo "FAIL: the FAKE_HOME ($resolved) resolves under the REAL home — refusing to run"
            exit 1
            ;;
    esac
    case "$resolved" in
        *agents-ensemble*)
            echo "FAIL: the FAKE_HOME ($resolved) is shaped like a live install — refusing to run"
            exit 1
            ;;
    esac
    export HOME="$SOAK_PACK_FAKE_HOME"
    export SOAK_PACK_FAKE_HOME
}

# ── 2. THE PER-VARIABLE ENV SCRUB ───────────────────────────────────
soak_pack_scrub_env() {
    # `env -u POSTGRES_*` is a SILENT NO-OP (bash does not glob env
    # names) — every variable is unset BY NAME, one by one.
    unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
    unset POSTGRES_URL
    unset DAEMON_PORT
    unset PORT
    unset ENSEMBLE_UPGRADE_LIVE ENSEMBLE_SELF_ENV
    unset OLD_CUR
}

# ── 3. THE SCRATCH POSTGRESQL DATABASE ──────────────────────────────
soak_pack_scratch_db() {
    # Creates a throwaway LOGIN role + an owned database on the LOCAL
    # PostgreSQL and exports the five POSTGRES_* variables PER VARIABLE.
    # The name is scratch-shaped by construction and the refusal guard
    # below re-checks it before anything boots.
    local host="${PG_TEST_HOST:-127.0.0.1}"
    local port="${PG_TEST_PORT:-5432}"
    local admin="${PG_TEST_USER:-ensemble}"
    local admin_pw="${PG_TEST_PASSWORD:-ensemble_dev}"
    local token role db pw
    token="$(date +%s)$$"
    role="soak_gate_${token}"
    db="soak_gate_test_${token}"
    pw="SoakGate${token}"
    export PGPASSWORD="$admin_pw"
    if ! psql -h "$host" -p "$port" -U "$admin" -d postgres -q \
            -c "CREATE ROLE \"${role}\" LOGIN PASSWORD '${pw}'" >/dev/null 2>&1; then
        echo "FAIL: cannot create the throwaway LOGIN role against ${host}:${port}"
        unset PGPASSWORD
        exit 1
    fi
    if ! psql -h "$host" -p "$port" -U "$admin" -d postgres -q \
            -c "CREATE DATABASE \"${db}\" OWNER \"${role}\"" >/dev/null 2>&1; then
        echo "FAIL: cannot create the scratch database against ${host}:${port}"
        psql -h "$host" -p "$port" -U "$admin" -d postgres -q \
            -c "DROP ROLE IF EXISTS \"${role}\"" >/dev/null 2>&1
        unset PGPASSWORD
        exit 1
    fi
    unset PGPASSWORD
    SOAK_PG_ROLE="$role"
    SOAK_PG_DB="$db"
    SOAK_PG_PASSWORD="$pw"
    export SOAK_PG_ROLE SOAK_PG_DB SOAK_PG_PASSWORD
    export PG_TEST_HOST="$host" PG_TEST_PORT="$port"
    # The PER-VARIABLE pin (never the glob idiom).
    export POSTGRES_HOST="$host"
    export POSTGRES_PORT="$port"
    export POSTGRES_DB="$db"
    export POSTGRES_USER="$role"
    export POSTGRES_PASSWORD="$pw"
    unset POSTGRES_URL
    soak_pack_assert_scratch_db "$db"
}

soak_pack_assert_scratch_db() {
    # THE PRE-BOOT ASSERTION — a pack refuses to start against a
    # database that is not scratch-shaped (the degrade-alone guard).
    local db="${1:-$POSTGRES_DB}"
    case "$db" in
        ensemble_prod|ensemble_dev|ensemble_sandbox|"")
            echo "FAIL: refusing to boot against a live/dev database (${db:-<empty>})"
            exit 1
            ;;
    esac
    case "$db" in
        *test*|*scratch*) : ;;
        *)
            echo "FAIL: refusing to boot against a non scratch-shaped database (${db})"
            exit 1
            ;;
    esac
}

soak_pack_drop_scratch_db() {
    [ -n "${SOAK_PG_DB:-}" ] || return 0
    local host="${PG_TEST_HOST:-127.0.0.1}"
    local port="${PG_TEST_PORT:-5432}"
    local admin="${PG_TEST_USER:-ensemble}"
    export PGPASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}"
    psql -h "$host" -p "$port" -U "$admin" -d postgres -q \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${SOAK_PG_DB}' AND pid <> pg_backend_pid()" >/dev/null 2>&1
    psql -h "$host" -p "$port" -U "$admin" -d postgres -q \
        -c "DROP DATABASE IF EXISTS \"${SOAK_PG_DB}\" WITH (FORCE)" >/dev/null 2>&1
    psql -h "$host" -p "$port" -U "$admin" -d postgres -q \
        -c "DROP ROLE IF EXISTS \"${SOAK_PG_ROLE}\"" >/dev/null 2>&1
    unset PGPASSWORD
}

# ── 4. A THROWAWAY PORT (never the live 9797 / dev 8079) ───────────
soak_pack_free_port() {
    # Picks a free high port and refuses the live/dev ports outright.
    local port
    for _ in $(seq 1 40); do
        port=$(( 19000 + RANDOM % 2000 ))
        case "$port" in
            9797|8079|7979) continue ;;
        esac
        if ! (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
            echo "$port"
            return 0
        fi
        exec 3>&- 2>/dev/null || true
    done
    echo "FAIL: cannot find a free port" >&2
    return 1
}

# ── 5. THE FAKE DAEMON ──────────────────────────────────────────────
soak_pack_start_fake_daemon() {
    # soak_pack_start_fake_daemon <mode> <port> <log> <ready> [install_dir] [bad_version]
    local mode="$1" port="$2" log="$3" ready="$4" install_dir="${5:-}" bad_version="${6:-}"
    : > "$log"
    rm -f "$ready"
    "$SOAK_PACK_PYTHON" "$SOAK_PACK_REPO_ROOT/tests/helpers/soak_gate_fake_daemon.py" \
        --port "$port" --mode "$mode" --log "$log" --ready-file "$ready" \
        ${install_dir:+--install-dir "$install_dir"} \
        ${bad_version:+--bad-version "$bad_version"} \
        >"${log}.out" 2>&1 &
    SOAK_FAKE_PID=$!
    local i
    for i in $(seq 1 50); do
        sleep 0.2
        if [ -f "$ready" ]; then
            return 0
        fi
        if ! kill -0 "$SOAK_FAKE_PID" 2>/dev/null; then
            echo "FAIL: the fake daemon died on startup"
            cat "${log}.out" 2>/dev/null || true
            return 1
        fi
    done
    echo "FAIL: the fake daemon never became ready"
    return 1
}

soak_pack_stop_fake_daemon() {
    [ -n "${SOAK_FAKE_PID:-}" ] || return 0
    kill "$SOAK_FAKE_PID" 2>/dev/null || true
    wait "$SOAK_FAKE_PID" 2>/dev/null || true
    SOAK_FAKE_PID=""
}

# ── 6. THE SCRATCH JOURNAL (the real lib.sh journal helpers) ───────
soak_pack_journal_field() {
    # soak_pack_journal_field <install_dir> <field> — reads the scratch
    # journal with the REAL lib.sh accessor (never a hand-rolled parse).
    local install_dir="$1" field="$2"
    (
        export INSTALL_DIR="$install_dir"
        # shellcheck source=/dev/null
        . "$SOAK_PACK_REPO_ROOT/scripts/upgrade/lib.sh" >/dev/null 2>&1
        _json_field "$(journal_read)" "$field" 2>/dev/null
    )
}

soak_pack_journal_sub() {
    # soak_pack_journal_sub <install_dir> <key> — the raw JSON fragment.
    local install_dir="$1" key="$2"
    (
        export INSTALL_DIR="$install_dir"
        # shellcheck source=/dev/null
        . "$SOAK_PACK_REPO_ROOT/scripts/upgrade/lib.sh" >/dev/null 2>&1
        _json_sub "$(journal_read)" "$key" 2>/dev/null
    )
}
