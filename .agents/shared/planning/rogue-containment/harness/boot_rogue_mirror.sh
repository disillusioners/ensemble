#!/usr/bin/env bash
# Fenced mirror harness for rogue-containment RED legs (E-ii / E-iii / DB-refusal).
# Modeled on boot_v2.sh + tests/support/wedge_smoke.py.
# NEVER touches live 9797/9798/ensemble_prod. Scratch only.
#
# p2 E2 sequencing step 0 (BLOCKING PRECONDITION fix) + gap-fix #1:
#   * claims are planted WHERE THE GATE READS: ENSEMBLE_PORT_CLAIMS_DIR is
#     pinned to a per-run tmp dir for EVERY leg (same shape as the conftest
#     session fence, tests/conftest.py:912-919), and the refuse-foreign
#     claim uses the B1-normalized bucket name ("any-<port>.json" —
#     _normalize_claim_host collapses 0.0.0.0/loopback to "any") at that
#     resolved override path. The pre-B1 shape (install-scoped
#     data/port-claims/0.0.0.0-<port>.json, dead-planter pid) was invisible
#     to the shipped gate — the leg was silently green.
#   * the claim is held by a LIVE holder process (is_live_foreign refuses
#     only on pid-alive + starttime match + foreign install_dir; a dead
#     pid reads STALE and never refuses).
#   * per-leg expected-exit + marker assertion; ANY mismatch => exit 1
#     naming the leg (the unconditional exit 0 is gone). Marker substrings
#     are the SHIPPED vocab, not invented:
#       "PORT OWNERSHIP REFUSED"  (daemon/services/port_ownership.py:624)
#       "DB RESOLUTION REFUSED"   (daemon/ensemble_config.py:184)
set -euo pipefail
umask 077

# --- FAKE_HOME escape guard (tests/upgrade_test_lib.sh:69-102) ---
FAKE_HOME="${TMPDIR:-/tmp}/rogue-mirror-home-$$"
mkdir -p "$FAKE_HOME"
export HOME="$FAKE_HOME"
case "$HOME" in /tmp/*) ;; *) echo "FATAL: HOME not under /tmp"; exit 99 ;; esac
case "$HOME" in /home/nea*) echo "FATAL: HOME under /home/nea"; exit 99 ;; esac

# --- per-variable scrub (NEVER `env -u POSTGRES_*` glob) ---
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DATABASE_URL PGHOST PGPORT PGUSER PGPASSWORD
# pin sweep guard explicitly (never env -u on it)
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0

# rogue-containment iter-1 D5: refuse to run without a scratch postgres
# (silent SQLite fallback is the bug this guard prevents).
if [ -z "${PG_TEST_HOST:-}" ]; then
  echo "FATAL: PG_TEST_HOST is required (scratch postgres). Refusing to fall back to SQLite." >&2
  exit 99
fi

# --- scratch port (re-roll off 9797/9798/7979/8079) ---
PORT="${ROGUE_MIRROR_PORT:-18790}"
export PORT HOST=0.0.0.0
export DAEMON_PORT="$PORT"

# --- scratch PG (PG_TEST_* -> POSTGRES_*) ---
export PG_TEST_DB_NAME="${PG_TEST_DB_NAME:-ens_e2e_scrub_probe}"
# Caller must provide PG_TEST_* vars; we translate:
if [ -n "${PG_TEST_HOST:-}" ]; then export POSTGRES_HOST="$PG_TEST_HOST"; fi
if [ -n "${PG_TEST_PORT:-}" ]; then export POSTGRES_PORT="$PG_TEST_PORT"; fi
if [ -n "${PG_TEST_USER:-}" ]; then export POSTGRES_USER="$PG_TEST_USER"; fi
if [ -n "${PG_TEST_PASSWORD:-}" ]; then export POSTGRES_PASSWORD="$PG_TEST_PASSWORD"; fi
export POSTGRES_DB="$PG_TEST_DB_NAME"

# --- scratch config (literal port, never ${PORT:-}) ---
SCRATCH_DIR="${TMPDIR:-/tmp}/rogue-mirror-$$"
mkdir -p "$SCRATCH_DIR/data"
export ENSEMBLE_DATA_DIR="$SCRATCH_DIR/data"
export ENSEMBLE_SELF_ENV=sandbox

# --- port-claims dir (p2 step-0 BLOCKING PRECONDITION fix) ---
# The shipped gate reads resolve_claims_dir() (port_ownership.py:108-125):
# ENSEMBLE_PORT_CLAIMS_DIR override > XDG_RUNTIME_DIR >
# /tmp/ensemble-port-claims-<uid>. Pin the override to a per-run tmp dir
# for EVERY leg so the planted claim and the gate MEET, and no boot
# writes the shared/live claims location.
CLAIMS_DIR="${TMPDIR:-/tmp}/rogue-mirror-claims-$$"
mkdir -p "$CLAIMS_DIR"
chmod 700 "$CLAIMS_DIR"
export ENSEMBLE_PORT_CLAIMS_DIR="$CLAIMS_DIR"

# --- run the daemon; capture redacted output ---
echo "[mirror] PORT=$PORT DATA_DIR=$ENSEMBLE_DATA_DIR HOME=$HOME SWEEP=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
echo "[mirror] scratch DB=$POSTGRES_DB (must not be a forbidden name)"
echo "[mirror] backend=postgres://${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB} (password redacted)"
case "$POSTGRES_DB" in ensemble_prod|ensemble_dev|ensemble_prod_test) echo "FATAL: forbidden DB name"; exit 99 ;; esac

# Interpreter: THIS tree's venv, resolved from the script's own location
# (the harness lives at .agents/shared/planning/rogue-containment/harness/
# => five levels up is the repo root). Override with MIRROR_PY=...
# Never the main checkout's .venv.
MIRROR_PY="${MIRROR_PY:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)/.venv/bin/python}"
if [ ! -x "$MIRROR_PY" ]; then
  echo "FATAL: mirror interpreter not executable: $MIRROR_PY" >&2
  exit 99
fi

# Caller supplies the daemon invocation via ROGUE_MIRROR_CMD
# (default: the mirror interpreter + `python -m daemon`; the MODULE
# resolves CWD-dependently, so run with CWD = the tree under test —
# the worktree-provenance ASSERT itself is a later lane, gap 2).
ROGUE_MIRROR_CMD="${ROGUE_MIRROR_CMD:-$MIRROR_PY -m daemon}"
echo "[mirror] CMD=$ROGUE_MIRROR_CMD"

# Leg selector
LEG="${ROGUE_MIRROR_LEG:-happy}"
HOLD_DIR=""
ROGUE_HOLDER_PID=""
case "$LEG" in
  refuse-foreign)
    # Plant a foreign claim where the gate reads it (B1 host-global
    # namespace; normalized bucket "any-<port>.json"), held by a LIVE
    # holder process this harness owns (a scratch sleeper with a scratch
    # cwd — TERMed in teardown, SIGTERM only). is_live_foreign refuses
    # ONLY on pid-alive + starttime match + foreign install_dir; a dead
    # pid reads STALE and never refuses.
    HOLD_DIR="${TMPDIR:-/tmp}/rogue-mirror-holder-$$"
    mkdir -p "$HOLD_DIR"
    ( cd "$HOLD_DIR" && exec sleep 300 ) &
    ROGUE_HOLDER_PID=$!
    if ! "$MIRROR_PY" - "$ENSEMBLE_PORT_CLAIMS_DIR" "$PORT" "$ROGUE_HOLDER_PID" <<'PY'
import json
import os
import sys
import time

from daemon.services.port_ownership import _read_starttime

claims_dir, port, holder_pid = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
st = None
for _ in range(20):
    st = _read_starttime(holder_pid)
    if st is not None:
        break
    time.sleep(0.05)
if st is None:
    sys.stderr.write(
        f"holder /proc/{holder_pid}/stat unreadable - refusing to plant a vacuous claim\n"
    )
    sys.exit(97)
path = os.path.join(claims_dir, f"any-{port}.json")
claim = {
    "install_dir": "foreign-install-root",
    "pid": holder_pid,
    "starttime": st,
    "port": port,
    "host": "0.0.0.0",
    "claimed_at": "now",
}
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(claim, f, indent=2)
    f.write("\n")
print(path)
PY
    then
      echo "FATAL: claim planter failed" >&2
      exit 99
    fi
    if [ ! -f "$ENSEMBLE_PORT_CLAIMS_DIR/any-$PORT.json" ]; then
      echo "FATAL: claim not planted at $ENSEMBLE_PORT_CLAIMS_DIR/any-$PORT.json" >&2
      exit 99
    fi
    export ENSEMBLE_DB_AMBIENT_ALLOW=1
    ;;
  db-poison)
    # Ensure no ensemble.json (tier 2) and ambient POSTGRES_* present (already).
    rm -f "$ENSEMBLE_DATA_DIR/ensemble.json"
    unset ENSEMBLE_DB_AMBIENT_ALLOW
    ;;
  happy)
    rm -f "$ENSEMBLE_DATA_DIR/ensemble.json"
    export ENSEMBLE_DB_AMBIENT_ALLOW=1
    ;;
  *)
    echo "FATAL: unknown leg '$LEG' (registered: refuse-foreign db-poison happy)" >&2
    exit 99
    ;;
esac
echo "[mirror] leg=$LEG claims_dir=$ENSEMBLE_PORT_CLAIMS_DIR"
if [ -n "$ROGUE_HOLDER_PID" ]; then
  echo "[mirror] foreign holder_pid=$ROGUE_HOLDER_PID claim=$CLAIMS_DIR/any-$PORT.json"
fi

# --- per-leg expected verdicts (gap-fix #1: the unconditional exit 0 is
# GONE). Marker substrings are the SHIPPED vocab (verified this lane):
#   daemon/services/port_ownership.py:624  "PORT OWNERSHIP REFUSED"
#   daemon/ensemble_config.py:184          "DB RESOLUTION REFUSED"
case "$LEG" in
  refuse-foreign) EXPECT_RC=78; EXPECT_MARKER="PORT OWNERSHIP REFUSED" ;;
  db-poison)      EXPECT_RC=78; EXPECT_MARKER="DB RESOLUTION REFUSED" ;;
  happy)          EXPECT_RC=0;  EXPECT_MARKER="" ;;
esac

# Run with timeout; capture exit code
set +e
eval "$ROGUE_MIRROR_CMD" > "$SCRATCH_DIR/run.log" 2>&1 &
DPID=$!
READYZ=""
if [ "$EXPECT_RC" -ne 0 ]; then
  # Refusal legs: the daemon self-exits 78 at the gate (pre-bind, early
  # in boot). Wait for that exit (bounded ~45s) instead of a fixed sleep
  # — TERM-ing mid-boot would mask the verdict. (The happy-leg readyz
  # bounded poll is a LATER lane — deliberately not built here.)
  ALIVE=1
  for _ in $(seq 1 90); do
    if ! kill -0 "$DPID" 2>/dev/null; then ALIVE=0; break; fi
    sleep 0.5
  done
  if [ "$ALIVE" -eq 1 ]; then
    echo "[mirror] refusal leg: daemon STILL ALIVE after ~45s (gate never fired) -- TERMing"
    kill -TERM "$DPID" 2>/dev/null
  fi
  wait "$DPID" 2>/dev/null
  RC=$?
else
  # wait for boot. Budget 25s: the fixed 8s false-redded the happy leg on
  # a slow scratch-PG boot (2026-11-14 smoke — TERM mid-startup => 143,
  # readyz never reached). The bounded readyz POLL itself is a LATER
  # lane (gap 5) — this is only a budget bump, not the poll.
  sleep 25
  # probe readyz
  READYZ="$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/api/health/readyz" 2>/dev/null)"
  CURL_RC=$?
  if [ "$CURL_RC" -ne 0 ]; then READYZ="no-readyz"; fi
  # teardown: SIGTERM only
  kill -TERM "$DPID" 2>/dev/null
  wait "$DPID" 2>/dev/null
  RC=$?
fi
# teardown: SIGTERM only — the scratch holder is ours too, never SIGKILL
if [ -n "$ROGUE_HOLDER_PID" ]; then
  kill -TERM "$ROGUE_HOLDER_PID" 2>/dev/null
  wait "$ROGUE_HOLDER_PID" 2>/dev/null
fi
echo "[mirror] exit_code=$RC"
if [ -n "$READYZ" ]; then echo "[mirror] readyz=$READYZ"; fi
echo "[mirror] --- tail of run.log ---"
tail -30 "$SCRATCH_DIR/run.log"
# redacted capture: sha256 of log only
echo "[mirror] log_sha256=$(sha256sum "$SCRATCH_DIR/run.log" | awk '{print $1}')"

# --- leg verdict (BLOCKING: any mismatch => exit 1 naming the leg) ---
FAIL=""
if [ "$RC" -ne "$EXPECT_RC" ]; then
  FAIL="exit_code=$RC expected=$EXPECT_RC"
fi
if [ -n "$EXPECT_MARKER" ]; then
  if ! grep -q "$EXPECT_MARKER" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL marker_missing:'$EXPECT_MARKER'"
  fi
  if [ "$READYZ" = "200" ]; then
    FAIL="$FAIL refusal_leg_reached_readyz_200 (gate did not refuse)"
  fi
else
  if [ "$READYZ" != "200" ]; then
    FAIL="$FAIL happy_leg_readyz=$READYZ expected=200"
  fi
  if grep -qE "PORT OWNERSHIP REFUSED|DB RESOLUTION REFUSED" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL happy_leg_saw_refusal_marker"
  fi
fi

# cleanup
rm -rf "$SCRATCH_DIR" "$FAKE_HOME" "$CLAIMS_DIR"
if [ -n "$HOLD_DIR" ]; then rm -rf "$HOLD_DIR"; fi

if [ -n "$FAIL" ]; then
  echo "[mirror] LEG FAILED: $LEG -- $FAIL"
  exit 1
fi
echo "[mirror] LEG PASS: $LEG (exit_code=$RC readyz=${READYZ:-n/a})"
exit 0
