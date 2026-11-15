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
#
# p2 Gate-3 extension (2026-11-15 — gap-fixes #2-#5 + new legs a/b/c):
#   * gap-fix #2 — provenance assert: `daemon.__file__` (imported with
#     CWD = the worktree) MUST resolve inside the worktree BEFORE any leg
#     (FATAL 99 otherwise) — the canonical checkout is never the code
#     under test (component-E2 gap 2 / foot-gun 11).
#   * gap-fix #3 — FAKE_HOME guard completed to the full 4-stage shape of
#     tests/upgrade_test_lib.sh:69-102 (export-took / under-/tmp /
#     resolved-live-install-parent-under-/tmp / never-/home/nea).
#   * gap-fix #4 — harness-local scratch config template
#     (scratch-config.yaml, next to this harness) GENERATED into the
#     per-run scratch dir with the leg's ACTUAL port substituted over the
#     literal; exported as ENSEMBLE_CONFIG for every leg. The repo-root
#     config.yaml interpolates ambient PORT (config.yaml:113) and is NOT
#     a fence; it is NOT touched (dev lanes depend on the interpolation).
#   * gap-fix #5 — the happy-shaped legs' fixed sleep is replaced by a
#     bounded readyz poll (budget ~45s, /readyz + /livez) so slow
#     scratch-PG boots don't false-red.
#   * legs (a) tier1-name-refusal — scratch ensemble.json (postgres-
#     shaped, db name "ensemble_prod" as a STRING, host 127.0.0.1 port 1
#     CLOSED) with POSTGRES_* env scrubbed so the json is the sole DSN
#     source (resolved_postgres_db_name is env-wins,
#     daemon/ensemble_config.py:238). The B2 fence fires PRE-TCP in the
#     boot preflight (daemon/__main__.py:268, before any engine exists):
#     expected exit 78 + the shipped tier-1 marker
#     "tier=resolved-dsn-targets-prod" (daemon/ensemble_config.py:347).
#     A guard-miss boot dies at the probe on the closed port with a
#     NON-78 code (75) = leg FAILURE.
#   * leg (b) foreign-holder — scratch socket holder (plain /usr/bin/
#     python3, cwd = a scratch install dir carrying a planted launcher.sh
#     attribution marker) binds the leg's port, CLAIM-LESS. Holder
#     attribution (daemon/services/port_holder.py:239: exe-ancestors for
#     launcher.sh, else cwd with launcher.sh or pyproject.toml+daemon/)
#     lands the scratch dir => foreign. Expected exit 78 + shipped
#     socket-holder refusal "PORT OWNERSHIP REFUSED (socket-holder)"
#     (port_ownership.py:931) with holder_pid/holder_exe/holder_cwd
#     tokens present. Teardown TERMs the holder (verify exit; never
#     SIGKILL).
#   * leg (c) same-install-handover — holder whose cwd == the daemon's
#     own resolved install dir (the worktree; resolve_self_install_dir
#     falls back to _source_tree_root, port_ownership.py:238-240).
#     T9 contract mechanism: SAME-PARENT — holder + daemon are BOTH
#     spawned as children of this harness script (identical parent
#     cmdline => claim-lineage layer reads match, AH-5), AND a v2 claim
#     with the matching recorded lineage (hash of THIS script's cmdline
#     via normalize_lineage_token/lineage_token_hash) is planted so the
#     takeover path (claim taken/overwritten) is exercised positively.
#     Expected NON-refuse: readyz 200, exit {0,143} + graceful log per
#     Fix 3, no refusal marker, claim rewritten with the daemon's pid.
#
# ⛔ OWNERSHIP GUARD (incident 2026-11-15, b740d48c): the harness rm's ONLY
# paths it CREATED (SCRATCH_DIR/FAKE_HOME/CLAIMS_DIR + OWNED_RM_DIRS).
# HOLD_DIR is an ATTRIBUTION surface, never an owned dir — for leg (c) it
# aliases the WORKTREE ITSELF; the pre-extension cleanup `rm -rf
# "$HOLD_DIR"` deleted the whole worktree once. NEVER rm HOLD_DIR.
set -euo pipefail
umask 077

# Worktree root (the code under test) — resolved REALPATH (symlink-proof;
# gap-fix #2 compares against this).
WORKTREE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd -P)"

# --- FAKE_HOME guard, full 4-stage shape (gap-fix #3;
# --- tests/upgrade_test_lib.sh:69-102) ---
TRUE_HOME="${HOME:-}"
FAKE_HOME="${TMPDIR:-/tmp}/rogue-mirror-home-$$"
mkdir -p "$FAKE_HOME"
export HOME="$FAKE_HOME"
# Stage 1: the HOME export must have taken.
if [ "$HOME" = "$TRUE_HOME" ]; then
  echo "FATAL F1-stage1: HOME export did not take (HOME=$HOME == TRUE_HOME=$TRUE_HOME)" >&2
  exit 99
fi
# Stage 2: FAKE_HOME must live under /tmp.
case "$HOME" in
  /tmp/*) ;;
  *) echo "FATAL F1-stage2: FAKE_HOME escapes /tmp (HOME=$HOME)" >&2; exit 99 ;;
esac
# Stage 3: the RESOLVED live-install parent must stay under /tmp
# (canonicalize via the fake root — resolves symlinks on /tmp).
LIVE_PARENT_TARGET="$(cd "$HOME" && pwd -P)/agents-ensemble"
case "$LIVE_PARENT_TARGET" in
  /tmp/*) ;;
  *) echo "FATAL F1-stage3: resolved LIVE_PARENT escapes /tmp ($LIVE_PARENT_TARGET)" >&2; exit 99 ;;
esac
# Stage 4: must never touch the real user home (literal /home/nea pinned,
# byte-identical to the reference lib — see tests/upgrade_test_lib.sh:95-98).
case "$LIVE_PARENT_TARGET" in
  *"/home/nea"*) echo "FATAL F1-stage4: resolved LIVE_PARENT touches /home/nea ($LIVE_PARENT_TARGET)" >&2; exit 99 ;;
esac

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

# --- scratch port (gate-3 band 18790-18796; gate-5 band 18800-18806;
# --- never 9797/9798/7979/8079) ---
PORT="${ROGUE_MIRROR_PORT:-18790}"
case "$PORT" in
  1879[0-6]|1880[0-6]) ;;
  *) echo "FATAL: scratch port '$PORT' outside the sanctioned bands (18790-18796, 18800-18806)" >&2; exit 99 ;;
esac
for _fp in 9797 9798 7979 8079; do
  if [ "$PORT" = "$_fp" ]; then echo "FATAL: forbidden port $_fp" >&2; exit 99; fi
done
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

# --- gap-fix #4: harness-local scratch config, LITERAL port ---
# The shipped template (scratch-config.yaml, next to this harness) is
# GENERATED into the per-run scratch dir with THIS leg's actual port
# substituted; the generated copy is exported as ENSEMBLE_CONFIG. Never
# ${PORT:-} interpolation (repo-root config.yaml:113 interpolates and is
# NOT a fence); the repo-root config.yaml is NOT touched.
HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TEMPLATE_SRC="$HARNESS_DIR/scratch-config.yaml"
if [ ! -f "$TEMPLATE_SRC" ]; then
  echo "FATAL: scratch-config template missing: $TEMPLATE_SRC" >&2
  exit 99
fi
SCRATCH_CONFIG="$SCRATCH_DIR/scratch-config.yaml"
sed "s/^\(\s*port:\s*\)[0-9][0-9]*/\1$PORT/" "$TEMPLATE_SRC" > "$SCRATCH_CONFIG"
if ! grep -Eq "^\s*port:\s*$PORT\$" "$SCRATCH_CONFIG"; then
  echo "FATAL: scratch-config generation failed (literal $PORT not baked)" >&2
  exit 99
fi
if grep -qF '${PORT' "$SCRATCH_CONFIG"; then
  echo "FATAL: scratch-config still carries \${PORT interpolation — not a fence" >&2
  exit 99
fi
export ENSEMBLE_CONFIG="$SCRATCH_CONFIG"
echo "[mirror] ENSEMBLE_CONFIG=$SCRATCH_CONFIG (gap-fix #4: literal port $PORT, generated from template)"

# --- socket-holder script (legs b/c) — generated into the scratch dir ---
# Plain-stdlib python; binds 0.0.0.0:$PORT and LISTENS. With a claims_watch
# file arg it releases the port once the claim's pid differs from its own
# (the daemon's takeover rewrote it) — the deterministic handover signal
# for leg (c). Run under /usr/bin/python3 with cwd = the dir whose
# attribution is under test (exe-ancestors of /usr/bin carry no
# launcher.sh, so port_holder.attribute_install_dir falls to the cwd rule).
HOLDER_PY="$SCRATCH_DIR/scratch_holder.py"
cat > "$HOLDER_PY" <<'HOLDER_EOF'
import json
import os
import socket
import sys
import time

port = int(sys.argv[1])
release_after = float(sys.argv[2])  # 0 => hold until TERMed
claims_watch = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "-" else None
my_pid = os.getpid()

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", port))
s.listen(1)
print(f"[holder] pid={my_pid} listening on 0.0.0.0:{port} watch={claims_watch} release_after={release_after}", flush=True)

if claims_watch is not None:
    deadline = time.monotonic() + 25.0
    takeover = False
    while time.monotonic() < deadline:
        try:
            with open(claims_watch, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("pid") not in (None, my_pid):
                takeover = True  # the daemon rewrote the claim with its pid
                break
        except Exception:
            pass
        time.sleep(0.25)
    if not takeover:
        print("[holder] watch deadline hit — releasing anyway", flush=True)
    else:
        print(f"[holder] takeover observed (claim pid != {my_pid})", flush=True)
    s.close()
    print(f"[holder] pid={my_pid} RELEASED port {port}", flush=True)
    time.sleep(60)  # stay alive + TERM-able for the harness teardown
elif release_after > 0:
    time.sleep(release_after)
    s.close()
    print(f"[holder] pid={my_pid} RELEASED port {port} (release_after)", flush=True)
    time.sleep(60)
else:
    # HOLD FOREVER until TERMed (leg (b): the holder must still be
    # LISTENING when the daemon's gate discovers holders).
    while True:
        time.sleep(3600)
HOLDER_EOF

# --- run the daemon; capture redacted output ---
echo "[mirror] PORT=$PORT DATA_DIR=$ENSEMBLE_DATA_DIR HOME=$HOME SWEEP=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
echo "[mirror] scratch DB=$POSTGRES_DB (must not be a forbidden name)"
echo "[mirror] backend=postgres://${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB} (password redacted)"
case "$POSTGRES_DB" in ensemble_prod|ensemble_dev|ensemble_prod_test) echo "FATAL: forbidden DB name"; exit 99 ;; esac

# Interpreter: THIS tree's venv, resolved from the script's own location
# (the harness lives at .agents/shared/planning/rogue-containment/harness/
# => five levels up is the repo root). Override with MIRROR_PY=...
# Never the main checkout's .venv.
MIRROR_PY="${MIRROR_PY:-$WORKTREE_ROOT/.venv/bin/python}"
if [ ! -x "$MIRROR_PY" ]; then
  echo "FATAL: mirror interpreter not executable: $MIRROR_PY" >&2
  exit 99
fi

# --- gap-fix #2: daemon-under-test provenance assert (BEFORE any leg) ---
# `-m daemon` resolves CWD-dependently; with CWD = the worktree the
# imported `daemon.__file__` MUST resolve inside THIS worktree — never the
# canonical checkout /home/nea/ensemble-src (component-E2 gap 2 / foot-gun
# 11; venue discipline of the mutation matrix).
DAEMON_FILE="$(cd "$WORKTREE_ROOT" && "$MIRROR_PY" -c 'import daemon, sys; sys.stdout.write(daemon.__file__)')"
DAEMON_FILE_REAL="$(readlink -f "$DAEMON_FILE" 2>/dev/null || echo "$DAEMON_FILE")"
WORKTREE_REAL="$(cd "$WORKTREE_ROOT" && pwd -P)"
case "$DAEMON_FILE_REAL" in
  "$WORKTREE_REAL"/*) ;;
  *)
    echo "FATAL: provenance assert FAILED — daemon under test resolves OUTSIDE the worktree:" >&2
    echo "       daemon.__file__=$DAEMON_FILE_REAL  worktree=$WORKTREE_REAL" >&2
    exit 99
    ;;
esac
echo "[mirror] provenance OK: daemon.__file__=$DAEMON_FILE_REAL (inside $WORKTREE_REAL)"

# Caller supplies the daemon invocation via ROGUE_MIRROR_CMD
# (default: the mirror interpreter + `python -m daemon`; the MODULE
# resolves CWD-dependently, so run with CWD = the tree under test —
# the worktree-provenance ASSERT itself is a later lane, gap 2).
ROGUE_MIRROR_CMD="${ROGUE_MIRROR_CMD:-$MIRROR_PY -m daemon}"

# --- gate-5 fence: pre-exec fd closerange (ROGUE_FD_CLOSERANGE=1) ---
# POK dryrun-fence: a scratch daemon launched from an agent bash INHERITS
# the live daemon's listener fds (fd=6 == 9797); `env -i` does NOT close
# fds. This wrapper closes fds 3..4096 THEN execv's the daemon-under-test,
# so the boot starts with a clean fd table. execv REPLACES the wrapper
# image in-place: the daemon pid IS the wrapper pid (harness pid-tracking
# and daemon_descendant_pid are unaffected).
if [ "${ROGUE_FD_CLOSERANGE:-0}" = "1" ]; then
  FD_WRAPPER_PY="$SCRATCH_DIR/fd_closerange_wrapper.py"
  cat > "$FD_WRAPPER_PY" <<'FDW_EOF'
import os
import sys

# Pre-exec fd closerange (POK dryrun-fd-inheritance-fence): close every
# inherited descriptor above stderr, then exec the daemon-under-test.
# fds 0/1/2 are PRESERVED (the harness's run.log redirect lives on 1/2).
os.closerange(3, 4096)
os.execv(sys.argv[1], [sys.argv[1], "-m", "daemon"])
FDW_EOF
  ROGUE_MIRROR_CMD="$MIRROR_PY $FD_WRAPPER_PY $MIRROR_PY"
  echo "[mirror] FD-CLOSERANGE wrapper ON: closerange(3,4096) then execv $MIRROR_PY -m daemon"
fi
echo "[mirror] CMD=$ROGUE_MIRROR_CMD"

# --- live-untouched invariant probes (component-E2 spec line 97: asserted
# --- before AND after; any drift between the snapshots FAILS the leg) ---
live_ports_snapshot() {
  # Stable shape only: `ss -tlnp`'s users:(...) column carries the probing
  # pipeline's own transient pids (observed false-drift on leg 1), so the
  # DRIFT COMPARISON normalizes it away — address:port is the invariant.
  ss -tlnp 2>/dev/null | grep -E ':(9797|9798|7979)[[:space:]]' | sed 's/users:.*//' | sort || true
}
LIVE_PORTS_BEFORE="$(live_ports_snapshot)"
echo "[mirror] live-untouched BEFORE: $(echo "$LIVE_PORTS_BEFORE" | grep -c . || true) live port line(s) snapped"

# --- holder spawn helper (legs b/c) ---
# $1 = holder cwd (the attribution surface), $2 = watch claims file ( "-"
# = none), $3 = release_after seconds (0 = hold until TERMed). The holder
# is a CHILD of this harness script (same-parent T9 mechanism for leg c:
# holder + daemon share this script's cmdline as their parent lineage).
spawn_holder() {
  local hcwd="$1" watch="$2" rel="$3"
  ( cd "$hcwd" && exec /usr/bin/python3 "$HOLDER_PY" "$PORT" "$rel" "$watch" ) \
    > "$SCRATCH_DIR/holder.log" 2>&1 &
  ROGUE_HOLDER_PID=$!
  HOLD_DIR="$hcwd"
}

# Leg selector
LEG="${ROGUE_MIRROR_LEG:-happy}"
HOLD_DIR=""
ROGUE_HOLDER_PID=""
OWNED_RM_DIRS=""
case "$LEG" in
  refuse-foreign)
    # Plant a foreign claim where the gate reads it (B1 host-global
    # namespace; normalized bucket "any-<port>.json"), held by a LIVE
    # holder process this harness owns (a scratch sleeper with a scratch
    # cwd — TERMed in teardown, SIGTERM only). is_live_foreign refuses
    # ONLY on pid-alive + starttime match + foreign install_dir; a dead
    # pid reads STALE and never refuses.
    HOLD_DIR="${TMPDIR:-/tmp}/rogue-mirror-holder-$$"
    OWNED_RM_DIRS="$OWNED_RM_DIRS $HOLD_DIR"
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
  tier1-name-refusal)
    # Leg (a): scratch ensemble.json, postgres-SHAPED, db name
    # "ensemble_prod" as a STRING only; DSN host is 127.0.0.1 port 1 —
    # CLOSED — so the refusal must fire PRE-TCP and even a guard-miss
    # boot can only die on connect-refused (non-78 = leg FAILURE).
    # POSTGRES_* env is scrubbed BELOW because resolved_postgres_db_name
    # is env-wins (daemon/ensemble_config.py:238) — the json must be the
    # sole DSN source or the resolved name never carries the prod string.
    rm -f "$ENSEMBLE_DATA_DIR/ensemble.json"
    mkdir -p "$ENSEMBLE_DATA_DIR"
    cat > "$ENSEMBLE_DATA_DIR/ensemble.json" <<JSON
{
  "database": "postgres",
  "postgres": {"host": "127.0.0.1", "port": 1, "db": "ensemble_prod", "user": "rogue_mirror_scratch", "password": "scratch-not-a-secret"},
  "sqlite": {"instances_db": "./data/instances.db", "checkpoints_db": "./data/checkpoints.db"}
}
JSON
    chmod 600 "$ENSEMBLE_DATA_DIR/ensemble.json"
    unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
    export ENSEMBLE_DB_AMBIENT_ALLOW=1
    echo "[mirror] leg planted scratch ensemble.json (db name = prod STRING, host 127.0.0.1:1 CLOSED)"
    ;;
  foreign-holder)
    # Leg (b): claim-LESS socket holder binds the scratch port; its cwd is
    # a SCRATCH install dir carrying a planted launcher.sh attribution
    # marker (port_holder.attribute_install_dir:239 — exe-ancestors first
    # [/usr/bin carries none], else cwd with launcher.sh => install root =
    # the scratch dir => FOREIGN vs the worktree). The exe is deliberately
    # /usr/bin/python3 (NOT the worktree venv): the worktree root itself
    # has launcher.sh, so a venv-exe holder would exe-walk its way to the
    # worktree and read same-install.
    rm -f "$ENSEMBLE_DATA_DIR/ensemble.json"
    HOLD_DIR="$SCRATCH_DIR/foreign-install"
    mkdir -p "$HOLD_DIR"
    : > "$HOLD_DIR/launcher.sh"
    chmod 600 "$HOLD_DIR/launcher.sh"
    spawn_holder "$HOLD_DIR" "-" 0
    export ENSEMBLE_DB_AMBIENT_ALLOW=1
    ;;
  same-install-handover)
    # Leg (c): holder cwd == the daemon's own resolved install dir (the
    # worktree). T9 mechanism: SAME-PARENT — holder and daemon are BOTH
    # spawned as children of this harness script, so the claim-lineage
    # layer reads match (AH-5); ADDITIONALLY a v2 claim with the matching
    # recorded lineage (hash of THIS script's cmdline) is planted so the
    # takeover path ("claim taken/overwritten") is exercised positively.
    # Expected NON-refuse: readyz 200, exit {0,143} + graceful log, no
    # refusal marker; the daemon rewrites the claim with its own pid and
    # the holder (watching that pid change) releases the port for the
    # bind-retry handover.
    # ⛔ HOLD_DIR aliases the WORKTREE here — attribution surface ONLY;
    #   the cleanup NEVER rm's it (ownership guard, incident 2026-11-15).
    rm -f "$ENSEMBLE_DATA_DIR/ensemble.json"
    HOLD_DIR="$WORKTREE_ROOT"
    spawn_holder "$HOLD_DIR" "$ENSEMBLE_PORT_CLAIMS_DIR/any-$PORT.json" 0
    if ! ( cd "$WORKTREE_ROOT" && "$MIRROR_PY" - "$ENSEMBLE_PORT_CLAIMS_DIR" "$PORT" "$$" "$WORKTREE_ROOT" "$ROGUE_HOLDER_PID" <<'PY'
import json
import os
import sys
import time

from daemon.services.port_ownership import (
    _cmdline_head,
    _read_starttime,
    lineage_token_hash,
    read_parent_cmdline,
)

claims_dir, port = sys.argv[1], int(sys.argv[2])
harness_pid, worktree, holder_pid = int(sys.argv[3]), sys.argv[4], int(sys.argv[5])
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
argv = read_parent_cmdline(harness_pid)
sha = lineage_token_hash(argv)
head = _cmdline_head("/proc", harness_pid)
claim = {
    "install_dir": worktree,
    "pid": holder_pid,
    "starttime": st,
    "port": port,
    "host": "0.0.0.0",
    "claimed_at": "now",
    "ppid": harness_pid,
    "ppid_cmdline_sha": sha,
    "ppid_cmdline_head": head,
}
path = os.path.join(claims_dir, f"any-{port}.json")
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(claim, f, indent=2)
    f.write("\n")
print(f"{path} (lineage_sha={sha} parent_head={head})")
PY
    ); then
      echo "FATAL: same-install v2 claim planter failed" >&2
      exit 99
    fi
    if [ ! -f "$ENSEMBLE_PORT_CLAIMS_DIR/any-$PORT.json" ]; then
      echo "FATAL: claim not planted at $ENSEMBLE_PORT_CLAIMS_DIR/any-$PORT.json" >&2
      exit 99
    fi
    export ENSEMBLE_DB_AMBIENT_ALLOW=1
    ;;
  *)
    echo "FATAL: unknown leg '$LEG' (registered: refuse-foreign db-poison happy tier1-name-refusal foreign-holder same-install-handover)" >&2
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
  refuse-foreign)        EXPECT_RC=78; EXPECT_MARKER="PORT OWNERSHIP REFUSED" ;;
  db-poison)             EXPECT_RC=78; EXPECT_MARKER="DB RESOLUTION REFUSED" ;;
  tier1-name-refusal)    EXPECT_RC=78; EXPECT_MARKER="tier=resolved-dsn-targets-prod" ;;
  foreign-holder)        EXPECT_RC=78; EXPECT_MARKER="PORT OWNERSHIP REFUSED (socket-holder)" ;;
  happy)                 EXPECT_RC=0;  EXPECT_MARKER="" ;;
  same-install-handover) EXPECT_RC=0;  EXPECT_MARKER="" ;;
esac
HAPPY_SHAPED=0
case "$LEG" in happy|same-install-handover) HAPPY_SHAPED=1 ;; esac

# Run with timeout; capture exit code
set +e

# --- Fix 2 (residue incident 2026-11-14): eval + "&" can interpose a wrapper
# shell between the harness and the daemon, so TERM on $DPID may kill only the
# WRAPPER and ORPHAN the real daemon (observed: daemon survived holding the
# scratch port, LISTEN fd inherited by its MCP children). Resolve the actual
# daemon pid (the DPID descendant whose cmdline runs `-m daemon`) and aim
# every TERM at THAT pid. Never SIGKILL — TERM only.
daemon_descendant_pid() {
  local parent="$1" p c
  for p in $(pgrep -P "$parent" 2>/dev/null); do
    if tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q -- '-m daemon'; then
      echo "$p"; return 0
    fi
    for c in $(pgrep -P "$p" 2>/dev/null); do
      if tr '\0' ' ' < "/proc/$c/cmdline" 2>/dev/null | grep -q -- '-m daemon'; then
        echo "$c"; return 0
      fi
    done
  done
  echo "$parent"   # eval exec'd directly: DPID IS the daemon
}

# --- Fix 3 (TERM disposition): the daemon's SIGTERM disposition on this host
# is exit 143 AFTER a graceful shutdown — the unit template codifies this as a
# SUCCESS disposition: SuccessExitStatus=143 (scripts/systemd/ensemble-daemon.service:111,
# comment :48 "SIGTERM-forwarded graceful stop"). The old "exit 0" expectation
# was aspirational; the happy leg accepts {0,143} and, for 143, REQUIRES the
# graceful-shutdown log line ("Graceful shutdown complete") as the
# with-graceful-log half of the contract.

eval "$ROGUE_MIRROR_CMD" > "$SCRATCH_DIR/run.log" 2>&1 &
DPID=$!
READYZ=""
LIVEZ=""
if [ "$EXPECT_RC" -ne 0 ]; then
  # Refusal legs: the daemon self-exits 78 at the gate (pre-bind, early
  # in boot). Wait for that exit (bounded ~75s) instead of a fixed sleep
  # — TERM-ing mid-boot would mask the verdict. (The happy-shaped readyz
  # bounded poll landed with gap-fix #5 below.)
  ALIVE=1
  for _ in $(seq 1 150); do
    if ! kill -0 "$DPID" 2>/dev/null; then ALIVE=0; break; fi
    sleep 0.5
  done
  DAEMON_PID="$(daemon_descendant_pid "$DPID")"
  if [ "$ALIVE" -eq 1 ]; then
    echo "[mirror] refusal leg: daemon STILL ALIVE after ~45s (gate never fired) -- TERMing real pid $DAEMON_PID"
    kill -TERM "$DAEMON_PID" 2>/dev/null
  fi
  wait "$DPID" 2>/dev/null
  RC=$?
  # Fix 2 orphan check: the REAL daemon must be gone after the wait/TERM.
  for _ in $(seq 1 20); do
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then DAEMON_GONE=1; break; fi
    sleep 0.5
  done
  if [ "${DAEMON_GONE:-0}" -ne 1 ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
    echo "[mirror] ORPHAN WARNING: daemon pid $DAEMON_PID still alive after TERM (leg will fail)"
    FAIL_ORPHAN="daemon_pid=$DAEMON_PID alive_after_TERM"
  fi
else
  # gap-fix #5: bounded readyz poll (budget ~45s). History: the fixed 8s
  # false-redded the happy leg on a slow scratch-PG boot (2026-11-14
  # smoke — TERM mid-startup => 143, readyz never reached); the 25s bump
  # was a stopgap. Poll /readyz every 1s until 200 or budget; break
  # early if the daemon died (a pre-poll crash must not burn the
  # budget). /readyz and /livez are the SHIPPED public health routes
  # (daemon/api.py; auth-middleware public set, daemon/middleware/auth.py:87).
  READY_BUDGET=45
  for _i in $(seq 1 "$READY_BUDGET"); do
    READYZ="$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/readyz" 2>/dev/null)" || READYZ="no-readyz"
    if [ "$READYZ" = "200" ]; then break; fi
    if ! kill -0 "$DPID" 2>/dev/null; then
      echo "[mirror] daemon exited before readyz went 200 (poll iteration $_i/$READY_BUDGET)"
      break
    fi
    sleep 1
  done
  LIVEZ="$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/livez" 2>/dev/null)" || LIVEZ="no-livez"
  # leg (c): the takeover must have rewritten the claim with the DAEMON's
  # pid — asserted AFTER readyz 200 and BEFORE the TERM (the claim file
  # outlives the daemon by design — the daemon never releases its own
  # claim; stale claims self-heal on next boot).
  if [ "$LEG" = "same-install-handover" ] && [ "$READYZ" = "200" ]; then
    CLAIM_FILE="$ENSEMBLE_PORT_CLAIMS_DIR/any-$PORT.json"
    if [ -f "$CLAIM_FILE" ]; then
      CLAIM_PID="$("$MIRROR_PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["pid"])' "$CLAIM_FILE" 2>/dev/null || echo unreadable)"
      DAEMON_PID="$(daemon_descendant_pid "$DPID")"
      echo "[mirror] same-install claim: file=$CLAIM_FILE recorded_pid=$CLAIM_PID daemon_pid=$DAEMON_PID"
      if [ "$CLAIM_PID" != "$DAEMON_PID" ]; then
        FAIL_CLAIM="claim pid=$CLAIM_PID != daemon_pid=$DAEMON_PID (takeover did not record the daemon)"
      fi
    else
      FAIL_CLAIM="claim file missing after readyz 200: $CLAIM_FILE"
    fi
  fi
  # gate-5 fd-verify (ROGUE_FD_VERIFY=1; POK dryrun-fd-inheritance-fence):
  # with the daemon UP (readyz 200), the scratch pid must appear ONLY on
  # its own scratch-port row (as binder) — NEVER on the live 9797/9798/
  # 7979 rows, where an inherited live listener fd would surface it as an
  # extra owner in the users:(...) token list.
  if [ "${ROGUE_FD_VERIFY:-0}" = "1" ] && [ "$READYZ" = "200" ]; then
    DAEMON_PID="$(daemon_descendant_pid "$DPID")"
    SS_SNAP="$(ss -ltnp 2>/dev/null)"
    LIVE_ROWS="$(echo "$SS_SNAP" | grep -E ':(9797|9798|7979)[[:space:]]' || true)"
    SCRATCH_ROW="$(echo "$SS_SNAP" | grep -E ":$PORT[[:space:]]" || true)"
    echo "[mirror] fd-verify: daemon pid=$DAEMON_PID scratch port=$PORT"
    echo "[mirror] fd-verify RAW live rows (9797/9798/7979):"
    echo "$LIVE_ROWS"
    echo "[mirror] fd-verify RAW scratch row (: $PORT):"
    echo "$SCRATCH_ROW"
    if echo "$LIVE_ROWS" | grep -Eq "pid=$DAEMON_PID[,)]"; then
      echo "[mirror] FD-VERIFY FAIL: scratch pid $DAEMON_PID present on LIVE rows (inherited fd leaked)"
      FAIL_FD="scratch_pid=$DAEMON_PID on_live_rows"
    else
      echo "[mirror] fd-verify: live rows clean (scratch pid absent) ✓"
    fi
    if [ -z "$SCRATCH_ROW" ]; then
      echo "[mirror] FD-VERIFY FAIL: no ss row for scratch port $PORT (positive control missing)"
      FAIL_FD="${FAIL_FD:+$FAIL_FD }no_scratch_row"
    elif ! echo "$SCRATCH_ROW" | grep -Eq "pid=$DAEMON_PID[,)]"; then
      echo "[mirror] FD-VERIFY FAIL: scratch pid $DAEMON_PID is not the binder of port $PORT"
      FAIL_FD="${FAIL_FD:+$FAIL_FD }scratch_row_not_bound_by_daemon"
    else
      echo "[mirror] fd-verify: positive control ✓ (scratch port bound by daemon pid)"
    fi
    if [ -z "${FAIL_FD:-}" ]; then
      echo "[mirror] FD-VERIFY PASS: scratch pid absent from live ports; bound only on its own port"
    fi
    # claim-taken evidence while the daemon is up (gate-5 shape (i))
    CLAIM_DURING="$ENSEMBLE_PORT_CLAIMS_DIR/any-$PORT.json"
    if [ -f "$CLAIM_DURING" ]; then
      echo "[mirror] claim state (daemon up): $(grep -o '"pid": [0-9]*' "$CLAIM_DURING" | head -1)"
    else
      echo "[mirror] claim state (daemon up): NO claim file"
    fi
  fi
  # teardown: SIGTERM only — at the REAL daemon pid (Fix 2), never the
  # possible eval wrapper and never SIGKILL.
  DAEMON_PID="$(daemon_descendant_pid "$DPID")"
  echo "[mirror] TERM target (real daemon pid): $DAEMON_PID"
  kill -TERM "$DAEMON_PID" 2>/dev/null
  wait "$DPID" 2>/dev/null
  RC=$?
  # Fix 2 orphan check: the REAL daemon must be gone after the TERM.
  DAEMON_GONE=0
  for _ in $(seq 1 20); do
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then DAEMON_GONE=1; break; fi
    sleep 0.5
  done
  if [ "$DAEMON_GONE" -ne 1 ]; then
    echo "[mirror] ORPHAN WARNING: daemon pid $DAEMON_PID still alive after TERM (leg will fail)"
    FAIL_ORPHAN="daemon_pid=$DAEMON_PID alive_after_TERM"
  fi
fi
# teardown: SIGTERM only — the scratch holder is ours too, never SIGKILL.
# The holder's exit is VERIFIED (leg (b)/(c) contract); a holder that
# survives TERM fails the leg.
if [ -n "$ROGUE_HOLDER_PID" ]; then
  echo "[mirror] holder log: $(grep -c . "$SCRATCH_DIR/holder.log" 2>/dev/null || echo 0) line(s), sha256=$(sha256sum "$SCRATCH_DIR/holder.log" 2>/dev/null | awk '{print $1}')"
  kill -TERM "$ROGUE_HOLDER_PID" 2>/dev/null || true
  wait "$ROGUE_HOLDER_PID" 2>/dev/null || true
  if kill -0 "$ROGUE_HOLDER_PID" 2>/dev/null; then
    echo "[mirror] HOLDER ORPHAN WARNING: pid $ROGUE_HOLDER_PID still alive after TERM (leg will fail)"
    FAIL_ORPHAN="${FAIL_ORPHAN:-} holder_pid=$ROGUE_HOLDER_PID alive_after_TERM"
  else
    echo "[mirror] holder pid $ROGUE_HOLDER_PID exited on TERM (verified)"
  fi
fi

# live-untouched AFTER probe — drift vs the BEFORE snapshot fails the leg
LIVE_PORTS_AFTER="$(live_ports_snapshot)"
if [ "$LIVE_PORTS_BEFORE" != "$LIVE_PORTS_AFTER" ]; then
  echo "[mirror] LIVE-UNTOUCHED DRIFT detected on 9797/9798/7979:"
  diff <(echo "$LIVE_PORTS_BEFORE") <(echo "$LIVE_PORTS_AFTER") || true
  FAIL_PORTS_DRIFT=1
fi
# claim lifecycle at teardown (gate-5 shape (i)): the shipped B3 design
# RETAINS the claim file post-shutdown (release_claim has no daemon
# callers — port_ownership.py:619-625); stale claims self-heal via
# pid+starttime liveness on the NEXT boot. Record state, never assert
# absence (that would assert a design the tree does not ship).
CLAIM_AFTER="$ENSEMBLE_PORT_CLAIMS_DIR/any-$PORT.json"
if [ -f "$CLAIM_AFTER" ]; then
  echo "[mirror] claim state (post-shutdown): file persisted per shipped B3 design, pid=$(grep -o '"pid": [0-9]*' "$CLAIM_AFTER" | grep -o '[0-9]*' | head -1)"
else
  echo "[mirror] claim state (post-shutdown): no claim file"
fi
echo "[mirror] exit_code=$RC"
if [ -n "$READYZ" ]; then echo "[mirror] readyz=$READYZ"; fi
if [ -n "$LIVEZ" ]; then echo "[mirror] livez=$LIVEZ"; fi
if [ "$HAPPY_SHAPED" = "1" ]; then
  echo "[mirror] graceful_log_lines=$(grep -c 'Graceful shutdown complete' "$SCRATCH_DIR/run.log") (TERM disposition contract: SuccessExitStatus=143 — scripts/systemd/ensemble-daemon.service:111)"
fi
echo "[mirror] --- tail of run.log ---"
tail -30 "$SCRATCH_DIR/run.log"
# redacted capture: sha256 of log only
echo "[mirror] log_sha256=$(sha256sum "$SCRATCH_DIR/run.log" | awk '{print $1}')"

# --- leg verdict (BLOCKING: any mismatch => exit 1 naming the leg) ---
FAIL=""
if [ -n "${FAIL_ORPHAN:-}" ]; then
  FAIL="$FAIL orphan:$FAIL_ORPHAN"
fi
if [ -n "${FAIL_CLAIM:-}" ]; then
  FAIL="$FAIL claim:$FAIL_CLAIM"
fi
if [ -n "${FAIL_PORTS_DRIFT:-}" ]; then
  FAIL="$FAIL live_ports_drift_9797_9798_7979"
fi
if [ -n "${FAIL_FD:-}" ]; then
  FAIL="$FAIL fd_verify:$FAIL_FD"
fi
if [ "$HAPPY_SHAPED" = "1" ]; then
  # Fix 3: {0,143} accepted (SuccessExitStatus=143 precedent —
  # scripts/systemd/ensemble-daemon.service:111); 143 MUST carry the
  # graceful-shutdown log line.
  if [ "$RC" -ne 0 ] && [ "$RC" -ne 143 ]; then
    FAIL="exit_code=$RC expected={0,143}"
  fi
  if [ "$RC" -eq 143 ] && ! grep -q "Graceful shutdown complete" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL rc143_without_graceful_shutdown_log"
  fi
else
  if [ "$RC" -ne "$EXPECT_RC" ]; then
    FAIL="exit_code=$RC expected=$EXPECT_RC"
  fi
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
  if [ "$LIVEZ" != "200" ]; then
    FAIL="$FAIL happy_leg_livez=$LIVEZ expected=200"
  fi
  if grep -qE "PORT OWNERSHIP REFUSED|DB RESOLUTION REFUSED" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL happy_leg_saw_refusal_marker"
  fi
fi
# leg (b) extra asserts: the shipped socket-holder marker line must carry
# the holder-identity tokens (pid/exe/cwd), and the pid/cwd/install
# values must be THIS leg's holder (not recycled /proc noise).
if [ "$LEG" = "foreign-holder" ]; then
  if ! grep -q "PORT OWNERSHIP REFUSED (socket-holder)" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL socket_holder_marker_missing"
  fi
  if ! grep -Eq "holder_pid=[0-9]+ holder_exe=" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL holder_pid_exe_tokens_missing"
  fi
  if ! grep -Fq "holder_pid=$ROGUE_HOLDER_PID" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL holder_pid_value_mismatch (expected $ROGUE_HOLDER_PID on the marker line)"
  fi
  if ! grep -Fq "holder_cwd=$HOLD_DIR" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL holder_cwd_token_missing (expected cwd=$HOLD_DIR)"
  fi
  if ! grep -Fq "holder_install=$HOLD_DIR" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL holder_install_token_missing (expected install=$HOLD_DIR)"
  fi
fi
# leg (a) extra assert: the generic DB-resolution refusal must name the
# TIER-1 fence (the resolved-DSN prod-target shape), not the ambient tier.
if [ "$LEG" = "tier1-name-refusal" ]; then
  if ! grep -q "DB RESOLUTION REFUSED" "$SCRATCH_DIR/run.log"; then
    FAIL="$FAIL tier1_generic_refusal_marker_missing"
  fi
fi

# cleanup — OWNERSHIP GUARD (incident 2026-11-15, report b740d48c):
# rm ONLY paths this harness CREATED. HOLD_DIR is an attribution surface
# and MAY alias the worktree itself (leg (c)) — the pre-extension
# `rm -rf "$HOLD_DIR"` line deleted a whole worktree once; it is GONE.
# OWNED_RM_DIRS carries only harness-created holder dirs (refuse-foreign).
rm -rf "$SCRATCH_DIR" "$FAKE_HOME" "$CLAIMS_DIR"
# shellcheck disable=SC2086
rm -rf $OWNED_RM_DIRS

if [ -n "$FAIL" ]; then
  echo "[mirror] LEG FAILED: $LEG -- $FAIL"
  exit 1
fi
echo "[mirror] LEG PASS: $LEG (exit_code=$RC readyz=${READYZ:-n/a})"
exit 0
