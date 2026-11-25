#!/usr/bin/env bash
# =============================================================================
# GATE PACK: prompt_reload_fresh_swap_e2e
#
# WHAT IT PROVES (defect lane `compact-fresh-swap`, module M2 — the E2E gate):
#
#   After a REAL note-guided compaction (real daemon + real LLM through the
#   dev proxy), the instance's persisted message channel must be EXACTLY
#
#       [ compaction doc , compacted-context note , natural model ack ]
#
#   i.e. the FRESH SWAP, with ZERO pre-compaction survival. The assertions
#   are the commission's contract, verbatim:
#
#     (a) msg[0] = SystemMessage, id startswith "compaction-global-"
#     (b) msg[1] = HumanMessage note: additional_kwargs.context_kind ==
#         "compacted_context", id startswith "compacted-context-note-",
#         content == "[SYSTEM CONTEXT: Compacted Context]\n" + EXACT body
#     (c) ZERO pre-compaction messages survive (no seeded conversation
#         marker, no prompt_reload exchange ToolMessage / tool_call)
#     (d) the last message = AIMessage ack from the natural post-swap pass
#         (non-empty content, no tool_calls)
#     (e) an immediate second instructed prompt_reload(compact,
#         compact_note=...) attempt is refused with the typed refusal
#         prompt_reload_compact_rate_limited (the 900s limiter STAYS)
#     (f) the daemon log carries the settle line outcome=compacted with
#         arrivals_preserved=0
#
#   WHY AN E2E GATE AND NOT UNIT PINS: the Emperor tested the previous
#   version LIVE and it did NOT behave. Unit pins are not the bar. This gate
#   drives a REAL compaction and asserts the REAL post-swap channel shape.
#
# ---------------------------------------------------------------------------
# INVOCATION (verbatim, from the tree root):
#
#   cd <tree-under-test> && bash test/packs/prompt_reload_fresh_swap_e2e.sh
#
#   Optional overrides (all exported by the caller):
#     GATE_TREE=<path>        tree under test (default: the tree this script
#                             lives in — <tree>/test/packs/../..)
#     GATE_PORT=<port>        daemon port (default: first free 19100-19999)
#     GATE_PG_PORT=<port>     scratch-PostgreSQL port (default: first free
#                             19700-19899)
#     GATE_SEED_COUNT=<n>     seeded history turns (default 16 — MUST be
#                             > compaction.recent_message_window (10) or
#                             the engine DEFERS the compaction and no doc
#                             is ever produced)
#     GATE_POLL_TIMEOUT=<s>   post-arm poll for the compaction doc (default
#                             240 — the commission floor is 180)
#     GATE_KEEP_SCRATCH=1     keep the scratch PG cluster + data dir
#
# PREREQUISITES (the INTEGRATOR must satisfy these before running):
#   * <tree>/.venv/bin/python  — the worktree venv (NEVER the main checkout's
#     venv; the tree under test must be the one that imports).
#   * <tree>/.env              — must carry OPENAI_API_KEY + OPENAI_BASE_URL
#     for the DEV PROXY (the gate makes REAL LLM calls; there is no mock).
#   * PostgreSQL server binaries (initdb/pg_ctl/createdb) — the gate builds
#     its OWN disposable cluster; it NEVER touches ensemble_dev/ensemble_prod.
#   * Network reachability to the dev proxy base URL.
#
# FENCES (binding — violating any invalidates the gate):
#   * DISPOSABLE /tmp PostgreSQL cluster: initdb -A trust, started on a
#     free port, `createdb` OVER TCP (host 127.0.0.1 — never the unix
#     socket). Unique $$-suffixed scratch dirs.
#   * The gate's DB is asserted to be NEITHER ensemble_prod NOR ensemble_dev
#     before anything connects (the gate uses ONLY its own cluster).
#   * The ambient POSTGRES_* / DSN family is scrubbed PER VARIABLE (bash
#     does NOT glob env names — `env -u POSTGRES_*` is a SILENT NO-OP).
#   * ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 is PINNED EXPLICITLY in the
#     daemon env (unset => the boot sweep kills LIVE processes).
#   * The whole ENSEMBLE_AUTH_* family is unset per variable (the ambient
#     shell has ENSEMBLE_AUTH_ENABLED=true — an authenticated daemon would
#     refuse every API call in this gate).
#   * ENSEMBLE_DATA_DIR points at the scratch dir and the scratch
#     ensemble.json is pre-written (tier-1 resolution) so the ambient-tier
#     DB guard cannot exit 78 the boot.
#   * The daemon is booted FROM THE TREE UNDER TEST (cwd = tree; the venv's
#     editable .pth points at the same tree) and the tree identity is
#     RE-VERIFIED at runtime (/proc/<pid>/cwd + GET /api/agents agent_dir).
#   * The daemon log is captured under /tmp as a .txt with a $$ suffix.
#   * Teardown kills the daemon and drops the cluster in a trap.
#   * PLANE_SYNC_ENABLED=0 (the gate is not a Plane test; no external
#     project-management traffic from a gate run).
#
# EXIT CODE: 0 = PASS, 1 = FAIL (any phase). The final line is always
# "GATE: PASS" or "GATE: FAIL".
# =============================================================================
set -euo pipefail

GATE_NAME="prompt_reload_fresh_swap_e2e"
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
TREE="${GATE_TREE:-$(cd "$(dirname "$SCRIPT_PATH")/../.." && pwd)}"
PY="$TREE/.venv/bin/python"
ENVFILE="$TREE/.env"

SEED_COUNT="${GATE_SEED_COUNT:-16}"
POLL_TIMEOUT="${GATE_POLL_TIMEOUT:-240}"
TURN_TIMEOUT="${GATE_TURN_TIMEOUT:-180}"
KEEP_SCRATCH="${GATE_KEEP_SCRATCH:-0}"

PG_BIN="${GATE_PG_BIN:-/usr/lib/postgresql/16/bin}"
STAMP="$$"
SCRATCH="/tmp/prompt-reload-fresh-swap-e2e-$STAMP"
PGROOT="$SCRATCH/pg"
PGDATA="$PGROOT/data"
PGSOCK="$PGROOT/sock"
DATADIR="$SCRATCH/data"
LOGDIR="$SCRATCH/logs"
DAEMON_LOG="/tmp/prompt_reload_fresh_swap_e2e_daemon-$STAMP.txt"
PG_DB="fsp_gate_$STAMP"
PG_USER="$(id -un)"
PG_PASSWORD="fsp_gate_pw"
DAEMON_PID=""
PG_STARTED=0
FINAL_LINE_PRINTED=0

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
say()  { printf '%s\n' "$*"; }
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; }
info() { printf 'INFO  %s\n' "$*"; }

free_port() {
    # $1 = first port of the range, $2 = last port of the range
    local p
    for p in $(seq "$1" "$2"); do
        if ! (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then
            printf '%s' "$p"
            return 0
        fi
        exec 3>&- 2>/dev/null || true
    done
    return 1
}

cleanup() {
    set +e
    if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        kill -TERM "$DAEMON_PID" 2>/dev/null
        for _ in $(seq 1 30); do
            kill -0 "$DAEMON_PID" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$DAEMON_PID" 2>/dev/null
    fi
    if [ "$PG_STARTED" = "1" ]; then
        "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null 2>&1
    fi
    if [ "$KEEP_SCRATCH" != "1" ]; then
        rm -rf "$SCRATCH"
    else
        say "INFO  scratch kept: $SCRATCH"
    fi
    return 0
}

# The final line is printed on EVERY exit path (including an early
# preflight/boot refusal) and the exit code matches it.
finalize() {
    local rc="$1"
    if [ "$FINAL_LINE_PRINTED" = "0" ]; then
        if [ "$rc" -eq 0 ]; then
            say "GATE: PASS"
        else
            say "GATE: FAIL"
        fi
        FINAL_LINE_PRINTED=1
    fi
    return $rc
}

trap 'rc=$?; cleanup; finalize "$rc"' EXIT

# ---------------------------------------------------------------------------
# PHASE 0 — preflight
# ---------------------------------------------------------------------------
say "=== GATE $GATE_NAME — phase 0: preflight ==="
[ -x "$PY" ] || { fail "missing $PY (the worktree venv is a hard prerequisite)"; exit 1; }
[ -f "$ENVFILE" ] || { fail "missing $ENVFILE (the gate needs the dev-proxy OPENAI_* values)"; exit 1; }
for bin in initdb pg_ctl createdb; do
    [ -x "$PG_BIN/$bin" ] || { fail "missing $PG_BIN/$bin (PostgreSQL server binaries are a hard prerequisite)"; exit 1; }
done

# The .env is the reference for the dev-proxy LLM values. Sourced in a
# subshell so the values never leak into the gate's own environment.
OPENAI_API_KEY="$(bash -c 'set -a; . "$1"; printf "%s" "${OPENAI_API_KEY:-}"' _ "$ENVFILE")"
OPENAI_BASE_URL="$(bash -c 'set -a; . "$1"; printf "%s" "${OPENAI_BASE_URL:-}"' _ "$ENVFILE")"
OPENAI_MODEL="$(bash -c 'set -a; . "$1"; printf "%s" "${OPENAI_MODEL:-agentic}"' _ "$ENVFILE")"
[ -n "$OPENAI_API_KEY" ] || { fail "$ENVFILE carries no OPENAI_API_KEY (a real LLM call is required)"; exit 1; }
[ -n "$OPENAI_BASE_URL" ] || { fail "$ENVFILE carries no OPENAI_BASE_URL (a real LLM call is required)"; exit 1; }
info "tree under test : $TREE"
info "git rev         : $(git -C "$TREE" rev-parse HEAD 2>/dev/null || echo 'unknown')"
info "dev proxy       : $OPENAI_BASE_URL (model=$OPENAI_MODEL)"

GATE_PORT="${GATE_PORT:-$(free_port 19100 19999 || echo '')}"
[ -n "$GATE_PORT" ] || { fail "no free daemon port in 19100-19999"; exit 1; }
PG_PORT="${GATE_PG_PORT:-$(free_port 19700 19899 || echo '')}"
[ -n "$PG_PORT" ] || { fail "no free PostgreSQL port in 19700-19899"; exit 1; }
info "daemon port     : $GATE_PORT"
info "scratch pg port : $PG_PORT"

# The gate NEVER touches a shared DB.
case "$PG_DB" in
    ensemble_prod|ensemble_dev|ensemble_test|"")
        fail "the scratch database name is not acceptable ($PG_DB)"; exit 1 ;;
esac
pass "phase 0: preflight (scratch db=$PG_DB — neither ensemble_prod nor ensemble_dev)"

# ---------------------------------------------------------------------------
# PHASE 1 — disposable PostgreSQL cluster
# ---------------------------------------------------------------------------
say "=== GATE $GATE_NAME — phase 1: disposable PostgreSQL cluster ==="
mkdir -p "$PGDATA" "$PGSOCK" "$DATADIR" "$LOGDIR"
chmod 700 "$PGDATA"
"$PG_BIN/initdb" -A trust -U "$PG_USER" -D "$PGDATA" >"$LOGDIR/initdb.log" 2>&1 \
    || { fail "initdb failed (see $LOGDIR/initdb.log)"; exit 1; }
"$PG_BIN/pg_ctl" -D "$PGDATA" -l "$LOGDIR/pg.log" \
    -o "-p $PG_PORT -h 127.0.0.1 -k $PGSOCK" -w start >>"$LOGDIR/pg.log" 2>&1 \
    || { fail "pg_ctl start failed (see $LOGDIR/pg.log)"; exit 1; }
PG_STARTED=1
"$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" "$PG_DB" \
    || { fail "createdb over TCP failed"; exit 1; }
# The role carries a password too (trust auth ignores it; a password keeps
# the DSN well-formed for every driver).
"$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
    -c "ALTER ROLE \"$PG_USER\" WITH PASSWORD '$PG_PASSWORD';" >/dev/null 2>&1 || true
CURRENT_DB="$("$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -tAc 'SELECT current_database();' 2>/dev/null | tr -d '[:space:]')"
[ "$CURRENT_DB" = "$PG_DB" ] || { fail "current_database() is '$CURRENT_DB', expected '$PG_DB'"; exit 1; }
pass "phase 1: disposable cluster up (db=$PG_DB @ 127.0.0.1:$PG_PORT, createdb over TCP)"

# tier-1 ensemble.json in the scratch data dir: the ambient-tier DB guard
# (which refuses an ambient POSTGRES_*-driven FIRST boot with exit 78) must
# not fire, and the resolved DSN must be the scratch cluster.
cat >"$DATADIR/ensemble.json" <<EOF
{"database":"postgres","postgres":{"host":"127.0.0.1","port":$PG_PORT,"db":"$PG_DB","user":"$PG_USER","password":"$PG_PASSWORD"},"sqlite":{"instances_db":"./data/instances.db","checkpoints_db":"./data/checkpoints.db"}}
EOF

# ---------------------------------------------------------------------------
# PHASE 2 — boot the daemon FROM THE TREE UNDER TEST
# ---------------------------------------------------------------------------
say "=== GATE $GATE_NAME — phase 2: daemon boot (tree under test) ==="
cd "$TREE"

set -a
# shellcheck disable=SC1091
. "$ENVFILE"
set +a

env -u ENSEMBLE_AUTH_ENABLED \
    -u ENSEMBLE_AUTH_CLIENT_ID \
    -u ENSEMBLE_AUTH_CLIENT_SECRET \
    -u ENSEMBLE_AUTH_ISSUER \
    -u ENSEMBLE_AUTH_DISCOVERY_URL \
    -u ENSEMBLE_AUTH_ALLOWED_ORIGINS \
    -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
    -u DATABASE_URL -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD \
    -u POSTGRES_URL -u SSL_CERT_FILE -u SSL_CERT_DIR \
    PORT="$GATE_PORT" DAEMON_PORT="$GATE_PORT" \
    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="$PG_PORT" POSTGRES_DB="$PG_DB" \
    POSTGRES_USER="$PG_USER" POSTGRES_PASSWORD="$PG_PASSWORD" \
    ENSEMBLE_SELF_ENV=dev \
    ENSEMBLE_DATA_DIR="$DATADIR" \
    DAEMON_LOG_DIR="$LOGDIR" \
    PLANE_SYNC_ENABLED=0 \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    "$PY" -m uvicorn daemon.api:app --host 127.0.0.1 --port "$GATE_PORT" \
        --log-level info --no-access-log >"$DAEMON_LOG" 2>&1 &
DAEMON_PID=$!

READY=0
for _ in $(seq 1 120); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$GATE_PORT/livez" 2>/dev/null || true)"
    if [ "$code" = "200" ]; then READY=1; break; fi
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then break; fi
    sleep 1
done
if [ "$READY" != "1" ]; then
    fail "the daemon did not come up (see $DAEMON_LOG)"
    tail -n 40 "$DAEMON_LOG" 2>/dev/null || true
    exit 1
fi
RCODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$GATE_PORT/readyz" 2>/dev/null || true)"
if [ "$RCODE" != "200" ]; then
    fail "readyz is $RCODE (expected 200)"
    exit 1
fi
pass "phase 2: daemon up (livez 200, readyz 200) — log: $DAEMON_LOG"

# PROVENANCE (LESSONS 2026-11-17): re-verify the RUNNING daemon's tree —
# never assume the tree you think you booted.
DAEMON_CWD="$(readlink -f "/proc/$DAEMON_PID/cwd" 2>/dev/null || echo 'unknown')"
AGENT_DIRS="$(curl -s "http://127.0.0.1:$GATE_PORT/api/agents" 2>/dev/null \
    | "$PY" -c 'import sys,json
try:
    data=json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit(0)
items=data if isinstance(data,list) else (data.get("agents") or [])
print("\n".join(sorted({str(a.get("agent_dir","")) for a in items if a.get("agent_dir")})))' 2>/dev/null || true)"
AGENT_ROOT="$(printf '%s' "$AGENT_DIRS" | head -n 1)"
DAEMON_VERSION="$(curl -s "http://127.0.0.1:$GATE_PORT/livez" 2>/dev/null \
    | "$PY" -c 'import sys,json
try: print(json.load(sys.stdin).get("version",""))
except Exception: print("")' 2>/dev/null || true)"
info "daemon pid      : $DAEMON_PID (version=$DAEMON_VERSION)"
info "daemon cwd      : $DAEMON_CWD"
info "daemon agents   : $(printf '%s' "$AGENT_DIRS" | wc -l) agent dirs under $(printf '%s' "$AGENT_DIRS" | head -n 1)"
case "$DAEMON_CWD" in
    "$TREE") : ;;
    *) fail "the running daemon's cwd is '$DAEMON_CWD', not the tree under test '$TREE'"; exit 1 ;;
esac
# EVERY resolved agent dir must live under the tree under test.
OUTSIDE=0
while IFS= read -r adir; do
    [ -n "$adir" ] || continue
    case "$adir" in
        "$TREE"/*) : ;;
        *) OUTSIDE=1; fail "the running daemon resolves an agent outside the tree under test: $adir" ;;
    esac
done <<<"$AGENT_DIRS"
[ "$OUTSIDE" = "0" ] || exit 1
pass "phase 2: provenance verified (daemon cwd + every agent_dir are the tree under test)"

# ---------------------------------------------------------------------------
# PHASE 3 — drive the REAL compaction and assert the post-swap channel
# ---------------------------------------------------------------------------
say "=== GATE $GATE_NAME — phase 3: drive + assert (real LLM) ==="
DRIVER="$SCRATCH/driver.py"
cat >"$DRIVER" <<'PYEOF'
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:%s" % os.environ["GATE_PORT"]
INSTANCE_AGENT = os.environ.get("GATE_AGENT", "ari")
SEED_COUNT = int(os.environ.get("GATE_SEED_COUNT", "16"))
POLL_TIMEOUT = int(os.environ.get("GATE_POLL_TIMEOUT", "240"))
TURN_TIMEOUT = int(os.environ.get("GATE_TURN_TIMEOUT", "180"))
DAEMON_LOG = os.environ.get("GATE_DAEMON_LOG", "")

# The commission's EXACT body. Two candidates only: the brief's literal with
# and without the single trailing space (the brief's sentence ends "user. ;"
# — the trailing space is ambiguous). NOTHING else is tolerated.
NOTE_PREFIX = "[SYSTEM CONTEXT: Compacted Context]\n"
NOTE_BODY = (
    "You just compacted the previous conversation into the 'Compacted "
    "context' summary above. You are ready for the next task. Do not run "
    "anything and do not call any tools. End your turn now and wait for "
    "the next message from the user."
)
NOTE_CANDIDATES = (NOTE_PREFIX + NOTE_BODY, NOTE_PREFIX + NOTE_BODY + " ")

FAILURES = []
MARKERS = []
# consecutive unchanged polls before a channel is considered settled
STABLE_POLLS = 5


def api(path, payload=None, timeout=120):
    url = "%s%s" % (BASE, path)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw else None


def all_messages():
    return api("/api/instances/%s/messages" % INSTANCE_ID)


def persisted():
    """The PERSISTED checkpoint messages (the synthetic system/context
    entries the read endpoint rebuilds on demand are NOT part of the
    channel and are excluded)."""
    return [m for m in all_messages() if not m.get("is_synthetic")]


def check(ok, label, detail=""):
    if ok:
        print("PASS  %s" % label, flush=True)
    else:
        print("FAIL  %s%s" % (label, (" — " + detail) if detail else ""), flush=True)
        FAILURES.append(label)
    return ok


def ai_count():
    return sum(1 for m in persisted() if m.get("role") == "assistant")


def send(content, timeout=TURN_TIMEOUT, label=""):
    before = ai_count()
    api("/api/instances/%s/messages" % INSTANCE_ID, {"content": content})
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(2)
        if ai_count() > before:
            return True
    print("FAIL  turn did not complete within %ss (%s)" % (timeout, label), flush=True)
    FAILURES.append("turn timeout: %s" % label)
    return False


def find_doc(msgs):
    for m in msgs:
        if str(m.get("message_id") or "").startswith("compaction-global-"):
            return m
    return None


def stable_snapshot(settle_secs):
    """Snapshot the channel once the post-swap state has settled.

    The compaction doc appears the moment the swap lands, but the natural
    post-swap model pass (the ack) lands a few seconds later — snapshotting
    at the doc's first sighting would race the ack and report a half-built
    channel. The snapshot is taken when EITHER

      * the channel already carries the ack (an assistant message right
        after the compacted-context note), OR
      * the channel has been unchanged for STABLE_POLLS consecutive polls.

    The doc must be present in both cases.
    """
    deadline = time.time() + settle_secs
    previous = None
    unchanged = 0
    while time.time() < deadline:
        msgs = persisted()
        if find_doc(msgs) is not None and ack_index(msgs) is not None:
            time.sleep(2)
            return persisted()
        if msgs == previous:
            unchanged += 1
            if unchanged >= STABLE_POLLS:
                return msgs
        else:
            unchanged = 0
            previous = msgs
        time.sleep(2)
    return persisted()


def note_index(msgs):
    for i, m in enumerate(msgs):
        if m.get("context_kind") == "compacted_context":
            return i
    return None


def ack_index(msgs):
    """The index of the natural post-swap ack — the first assistant message
    AFTER the compacted-context note."""
    ni = note_index(msgs)
    if ni is None:
        return None
    for i in range(ni + 1, len(msgs)):
        m = msgs[i]
        if m.get("role") == "assistant":
            return i
    return None


def compose(msgs):
    out = []
    for i, m in enumerate(msgs):
        out.append(
            "    [%d] role=%s id=%s context_kind=%s tool_calls=%s len=%s"
            % (
                i,
                m.get("role"),
                m.get("message_id"),
                m.get("context_kind"),
                [t.get("name") for t in (m.get("tool_calls") or [])],
                len(m.get("content") or ""),
            )
        )
    return "\n".join(out)


# ── 1. instance ────────────────────────────────────────────────────────────
inst = api("/api/instances", {"agent_id": INSTANCE_AGENT})
INSTANCE_ID = inst["instance_id"]
print("INFO  instance=%s agent=%s" % (INSTANCE_ID, INSTANCE_AGENT), flush=True)

# ── 2. seeded history (> compaction.recent_message_window, else the engine
#       DEFERS the compaction and no doc is ever produced) ─────────────────
run = uuid.uuid4().hex[:8]
for n in range(1, SEED_COUNT + 1):
    marker = "MARKER-%d-%s" % (n, run)
    MARKERS.append(marker)
    ok = send(
        "Reply with exactly: ACK-%d (session marker %s). Nothing else." % (n, marker),
        label="seed %d" % n,
    )
    if not ok:
        break
print("INFO  seeded %d history turns" % len(MARKERS), flush=True)

# ── 3. the arm: ONE message instructing the model to call prompt_reload ──
note = (
    "Seeded marker turns only; the user asked to compact the conversation. "
    "The next task arrives later."
)
send(
    "Use the prompt_reload tool right now with compact=true and "
    'compact_note="%s". Call the tool once, then stop.' % note,
    label="instruct",
)

# ── 4. poll for the compaction doc; the wake-up dispatch is the fallback
#       trigger when the arm has not landed yet ────────────────────────────
snapshot = None
t0 = time.time()
while time.time() - t0 < POLL_TIMEOUT:
    msgs = persisted()
    doc = find_doc(msgs)
    if doc is not None:
        snapshot = stable_snapshot(60)
        print(
            "INFO  compaction doc appeared after %.0fs (channel settled)"
            % (time.time() - t0),
            flush=True,
        )
        break
    time.sleep(3)

if snapshot is None:
    print("INFO  no compaction doc yet — sending the wake-up dispatch", flush=True)
    send("Continue. Reply with a short acknowledgement only.", label="wakeup")
    t0 = time.time()
    while time.time() - t0 < POLL_TIMEOUT:
        msgs = persisted()
        doc = find_doc(msgs)
        if doc is not None:
            snapshot = stable_snapshot(60)
            print(
                "INFO  compaction doc appeared %.0fs after the wake-up dispatch "
                "(channel settled)" % (time.time() - t0),
                flush=True,
            )
            break
        time.sleep(3)

if snapshot is None:
    print("FAIL  no compaction doc (compaction-global-*) appeared in the channel", flush=True)
    print("INFO  final channel:\n%s" % compose(persisted()), flush=True)
    sys.exit(1)

print("INFO  post-swap channel:\n%s" % compose(snapshot), flush=True)

# ── 5. assertions (a)-(d) on the FIRST observation of the doc ─────────────
doc_idx = next(
    i
    for i, m in enumerate(snapshot)
    if str(m.get("message_id") or "").startswith("compaction-global-")
)
doc = snapshot[doc_idx]

# (a) msg[0] = the compaction doc
check(
    doc_idx == 0
    and doc.get("role") == "system"
    and str(doc.get("message_id") or "").startswith("compaction-global-"),
    "(a) msg[0] is the compaction doc (SystemMessage, id startswith compaction-global-)",
    "doc found at index %d (role=%s)" % (doc_idx, doc.get("role")),
)

# (b) msg[1] = the compacted-context note, EXACT content
note_msg = snapshot[1] if len(snapshot) > 1 else {}
content = note_msg.get("content") or ""
check(
    note_msg.get("role") == "user"
    and note_msg.get("context_kind") == "compacted_context"
    and str(note_msg.get("message_id") or "").startswith("compacted-context-note-")
    and content in NOTE_CANDIDATES,
    "(b) msg[1] is the compacted-context note (context_kind/id/content exact)",
    "role=%s id=%s context_kind=%s content_prefix=%r"
    % (
        note_msg.get("role"),
        note_msg.get("message_id"),
        note_msg.get("context_kind"),
        content[:60],
    ),
)
if content in NOTE_CANDIDATES and content != NOTE_CANDIDATES[0]:
    print("INFO  the note body matched the trailing-space variant of the contract", flush=True)

# (c) ZERO pre-compaction survival
survivors = []
for i, m in enumerate(snapshot):
    if i == doc_idx:
        continue  # the doc is model-authored; a marker echoed INTO the doc
    # is not a surviving message (disclosed, reported below).
    for marker in MARKERS:
        if marker in (m.get("content") or ""):
            survivors.append("index %d carries marker %s" % (i, marker))
    for tc in (m.get("tool_calls") or []):
        if tc.get("name") == "prompt_reload":
            survivors.append("index %d carries the prompt_reload tool_call" % i)
    if m.get("role") == "tool":
        survivors.append("index %d is a ToolMessage" % i)
check(
    not survivors,
    "(c) zero pre-compaction messages survive (no marker, no prompt_reload exchange)",
    "; ".join(survivors[:5]),
)
echoed = [mk for mk in MARKERS if mk in (doc.get("content") or "")]
if echoed:
    print(
        "INFO  the compaction doc echoes %d seeded marker(s) (model-authored content, "
        "not a surviving message)" % len(echoed),
        flush=True,
    )

# the swap census: exactly ONE doc and exactly ONE compacted-context note
n_docs = sum(
    1 for m in snapshot if str(m.get("message_id") or "").startswith("compaction-global-")
)
n_notes = sum(
    1 for m in snapshot if m.get("context_kind") == "compacted_context"
)
check(
    n_docs == 1 and n_notes == 1,
    "(c2) the channel carries exactly ONE compaction doc and ONE compacted-context note",
    "docs=%d notes=%d" % (n_docs, n_notes),
)

# (d) the natural post-swap pass: the channel ends with a non-empty AIMessage
#     ack that carries no tool_calls, and THAT ack is the assistant message
#     that follows the compacted-context note (the post-swap pass — not a
#     reply to a spliced-in pre-compaction user turn).
note_i = note_index(snapshot)
ack_i = ack_index(snapshot)
ack = snapshot[ack_i] if ack_i is not None else {}
check(
    ack_i is not None
    and (ack.get("content") or "").strip() != ""
    and not (ack.get("tool_calls") or []),
    "(d-i) an assistant ack follows the compacted-context note (the post-swap pass)",
    "note_index=%r ack_index=%r role=%s len=%s tool_calls=%s"
    % (
        note_i,
        ack_i,
        ack.get("role"),
        len(ack.get("content") or ""),
        ack.get("tool_calls"),
    ),
)
last = snapshot[-1]
check(
    last.get("role") == "assistant"
    and (last.get("content") or "").strip() != ""
    and not (last.get("tool_calls") or []),
    "(d-ii) the LAST message is a non-empty AIMessage with no tool_calls",
    "role=%s len=%s tool_calls=%s"
    % (last.get("role"), len(last.get("content") or ""), last.get("tool_calls")),
)
between = snapshot[note_i + 1 : ack_i] if (note_i is not None and ack_i is not None) else []
check(
    all(m.get("role") == "user" for m in between),
    "(d-iii) everything between the note and the ack is a post-swap user arrival",
    "roles=%s" % [m.get("role") for m in between],
)

# the EXACT-list contract: [doc, note, ack] and nothing else
check(
    len(snapshot) == 3,
    "(d2) the post-swap channel is EXACTLY [compaction doc, compacted-context note, ack]",
    "channel length=%d" % len(snapshot),
)

# ── 6. (e) the second instructed attempt is refused (the 900s limiter stays)
send(
    "Use the prompt_reload tool right now with compact=true and "
    'compact_note="A second deliberate compaction attempt." Call the tool once.',
    label="second attempt",
)
refusal_hits = []
for m in persisted():
    for tc in (m.get("tool_calls") or []):
        out = tc.get("output") or ""
        if "prompt_reload_compact_rate_limited" in out:
            refusal_hits.append(out.strip().splitlines()[0][:160])
    if "prompt_reload_compact_rate_limited" in (m.get("content") or ""):
        refusal_hits.append("assistant content")
check(
    bool(refusal_hits),
    "(e) the second instructed attempt is refused with prompt_reload_compact_rate_limited",
    "no refusal envelope found in the channel",
)
if refusal_hits:
    print("INFO  refusal: %s" % refusal_hits[0], flush=True)

# ── 7. (f) the daemon log's settle line ───────────────────────────────────
settled = False
arrivals = None
if DAEMON_LOG and os.path.exists(DAEMON_LOG):
    with open(DAEMON_LOG, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "outcome=compacted" in line and "prompt_reload_compact_note" in line:
                settled = True
                for token in line.split():
                    if token.startswith("arrivals_preserved="):
                        arrivals = token.split("=", 1)[1]
check(settled, "(f) the daemon log carries the outcome=compacted settle line")
check(
    arrivals == "0",
    "(f2) the settle line carries arrivals_preserved=0",
    "arrivals_preserved=%r" % arrivals,
)

print("INFO  channel after the second attempt:\n%s" % compose(persisted()), flush=True)
if FAILURES:
    print("DRIVER: FAIL (%d failing assertion(s))" % len(FAILURES), flush=True)
    sys.exit(1)
print("DRIVER: PASS", flush=True)
sys.exit(0)
PYEOF

set +e
GATE_PORT="$GATE_PORT" \
GATE_SEED_COUNT="$SEED_COUNT" \
GATE_POLL_TIMEOUT="$POLL_TIMEOUT" \
GATE_TURN_TIMEOUT="$TURN_TIMEOUT" \
GATE_DAEMON_LOG="$DAEMON_LOG" \
    "$PY" "$DRIVER"
DRIVER_RC=$?
set -e

if [ "$DRIVER_RC" -ne 0 ]; then
    fail "phase 3: the post-swap channel contract is NOT satisfied"
    say "GATE: FAIL"
    FINAL_LINE_PRINTED=1
    exit 1
fi
pass "phase 3: the post-swap channel is EXACTLY [compaction doc, compacted-context note, ack]"

say "GATE: PASS"
FINAL_LINE_PRINTED=1
exit 0
