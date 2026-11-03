#!/usr/bin/env bash
# ============================================================================
# tests/packs/promote_handback_halt_gate_legs_test.sh
#
# REGISTERED PACK (promote-handback-halt lane, PB-2 finding F2) — the three
# CONSTRUCTED PB-2 legs become a registered pack:
#
#   G2 completion     — the "next promote ACCEPTED" positive + the negative
#                       arms, driven END-TO-END at the REAL gate
#                       (scripts/upgrade/lib.sh promote_entry_check),
#                       including the VERBATIM refusal string
#                       "… is QUARANTINED (prior gate failure) — quarantine is
#                       cleared only by re-staging the version" (lib.sh).
#   G4 BASE arm       — a stale flipped txn + serving-green under the LEGACY
#                       disposition (SWEEP_SERVING_GREEN_ENABLED=0) is WRONGLY
#                       rolled back (sweep_rollback + SILENT quarantine + the
#                       24h counter + current->prev + in_flight closed + the
#                       NEXT PROMOTE REFUSED rc=78), contrasted with the HEAD
#                       arm (the same txn COMMITTED, no punishment).
#   G5 interleaved    — the absent-ceiling is INELIGIBLE when a serving-green
#                       observation intervenes (counter climbs to 2 — NON-
#                       VACUITY — then the GREEN observation COMMITS and
#                       deletes the counter file; NO sweep_rollback ever), and
#                       absent/GREEN/absent*4 with NO intent_flip ⇒ no commit,
#                       no rollback, no counter, the
#                       'green-without-intent-flip' marker, txn stays OPEN.
#
# PROVENANCE: the legs were constructed at /tmp/phh-pb2/{leg_g2_completion,
# leg_g4_base_arm, leg_g5_interleaved}.sh during the PB-2 evidence pass and
# PASSED there (10/0, 20/0, 20/0). This pack folds them VERBATIM IN BEHAVIOUR
# — every assertion name and semantics is preserved; no assertion weakened.
# Deltas vs the /tmp originals (deliberate, behaviour-preserving):
#   * REPO_ROOT is derived from THIS pack's location (the /tmp legs pinned the
#     frozen worktree path) — the pack runs against its own worktree;
#   * the "absent" probe port is an ALLOCATED-THEN-RELEASED loopback ephemeral
#     port instead of the literal 19998 (same behaviour: nothing listens),
#     with a defensive assert that it never lands on a reserved service port;
#   * each leg carries an escape guard: INSTALL_DIR must live under the leg's
#     own FAKE_HOME (the 2026-10-12 live-install-deletion fence).
#
# HOUSE PACK FENCES (all self-enforced):
#   * bash shebang; per-leg timeout INSIDE the pack (`timeout 300` per leg —
#     the dual layer; the registered invocation adds the outer timeout);
#   * per-leg PASS/FAIL counters + an aggregate RESULT line; exit 0/1/124;
#   * FAKE_HOME = mktemp -d (mode 700) exported as HOME — $HOME/agents-ensemble
#     resolves to the SANDBOX, never to /home/nea/agents-ensemble;
#   * EXIT traps remove the sandboxes;
#   * per-variable env scrub INSIDE the pack (the glob form
#     `env -u POSTGRES_*` is a SILENT NO-OP — bash does not glob env names);
#   * fixture ports bound on loopback ephemeral (socket.bind(("127.0.0.1",0)))
#     and released; the never-listening "absent" ports likewise;
#   * NO live-install dependence, NO daemon start, NO 9797/8088/7979.
#
# Run (registered invocation — see .agents/tester/PACKS.md):
#   timeout 600 env -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_USER \
#       -u POSTGRES_PASSWORD -u DATABASE_URL -u PGHOST -u PGPORT \
#       -u PGDATABASE -u PGUSER -u PGPASSWORD \
#       bash tests/packs/promote_handback_halt_gate_legs_test.sh
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIBSH="$REPO_ROOT/scripts/upgrade/lib.sh"
LAUNCHER="$REPO_ROOT/launcher.sh"
PROBE_HELPER="$REPO_ROOT/tests/helpers/serving_probe_fake_listener.py"
PACK_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

# ---- per-variable env scrub INSIDE the pack (belt; the invocation is the braces)
for _SCRUB_V in POSTGRES_DB POSTGRES_HOST POSTGRES_USER POSTGRES_PASSWORD \
                DATABASE_URL PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD; do
    unset "$_SCRUB_V" 2>/dev/null || true
done
unset _SCRUB_V

# Reserved service ports the fixtures must never touch (defensive pin).
is_reserved_port() {
    case "$1" in
        9797|8088|7979) return 0 ;;
        *) return 1 ;;
    esac
}

# free_port — a loopback EPHEMERAL port, bound then released.
free_port() {
    python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'
}

# absent_port — a port NOTHING listens on (allocated then released), never a
# reserved service port.
absent_port() {
    local p
    while :; do
        p="$(free_port)"
        is_reserved_port "$p" || { printf '%s' "$p"; return 0; }
    done
}

# ---- shared assertion helpers (identical semantics in every leg) -----------
PASS=0; FAIL=0; FAILED=""
_pass() { PASS=$((PASS+1)); }
_fail() { FAIL=$((FAIL+1)); FAILED="$FAILED
  x $1"; printf 'FAIL: %s\n' "$1" >&2; [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2; return 0; }
assert_eq() { if [ "$2" = "$3" ]; then _pass; else _fail "$1" "$2" "$3"; fi; }
assert_re() { if printf '%s' "$3" | grep -qE "$2"; then _pass; else _fail "$1" "matches /$2/" "$(printf '%s' "$3" | head -c 300)"; fi; }
assert_not_re() { if printf '%s' "$3" | grep -qE "$2"; then _fail "$1" "NOT matches /$2/" "$(printf '%s' "$3" | head -c 300)"; else _pass; fi; }
assert_contains() { if printf '%s' "$3" | grep -qF "$2"; then _pass; else _fail "$1" "contains '$2'" "$(printf '%s' "$3" | head -c 300)"; fi; }
assert_not_contains() { if printf '%s' "$3" | grep -qF "$2"; then _fail "$1" "NOT contains '$2'" "$(printf '%s' "$3" | head -c 300)"; else _pass; fi; }
section() { printf '\n== %s ==\n' "$1"; }

# leg_sandbox TAG — a self-contained FAKE_HOME sandbox (mode 700) exported as
# HOME, with the EXIT trap that removes it. INSTALL_DIR must resolve INSIDE it
# (escape guard — the 2026-10-12 live-install-deletion fence).
leg_sandbox() {
    local tag="$1"
    FAKE_HOME="$(mktemp -d "/home/nea/.phh-gate-legs-${tag}.XXXXXX")"
    chmod 700 "$FAKE_HOME"
    export HOME="$FAKE_HOME"
    INSTALL_DIR="$FAKE_HOME/agents-ensemble"
    mkdir -p "$INSTALL_DIR/releases"
    LISTENER_PID=""
    trap 'rm -rf "$FAKE_HOME"; [ -n "$LISTENER_PID" ] && kill "$LISTENER_PID" 2>/dev/null' EXIT
    case "$INSTALL_DIR" in
        "$FAKE_HOME"/*) : ;;
        *) printf 'FATAL: INSTALL_DIR escaped the sandbox: %s\n' "$INSTALL_DIR" >&2; exit 99 ;;
    esac
    case "$(cd "$FAKE_HOME" && pwd -P)" in
        /home/nea/agents-ensemble|/home/nea/agents-ensemble/*) printf 'FATAL: sandbox collided with the live install path\n' >&2; exit 99 ;;
    esac
}

# start_green_listener PORT VERSION — the serving-probe fake listener (green),
# tracked in tests/helpers/. Returns via LISTENER_PID.
start_green_listener() {
    python3 "$PROBE_HELPER" --port "$1" --mode green --version "$2" >/dev/null 2>&1 &
    LISTENER_PID=$!
    sleep 1
}

# stop_listener — release the fixture listener.
stop_listener() {
    if [ -n "${LISTENER_PID:-}" ]; then
        kill "$LISTENER_PID" 2>/dev/null || true
        wait "$LISTENER_PID" 2>/dev/null || true
    fi
    LISTENER_PID=""
}

# ---- leg summary ------------------------------------------------------------
leg_summary() {
    printf 'LEG_PASS=%s LEG_FAIL=%s\n' "$PASS" "$FAIL"
    if [ "$FAIL" -gt 0 ]; then
        printf 'FAILED:\n%s\n' "$FAILED"
        return 1
    fi
    return 0
}

################################################################################
################################################################################
if [ "${1:-}" = "--leg" ]; then
    LEG="$2"
    # ---------------------------------------------------------------------------
    if [ "$LEG" = "g2" ]; then
    # ===========================================================================
    # LEG G2 — completion: the gate ACCEPTS a cleared quarantine; REFUSES an
    # uncleared one with the VERBATIM refusal string; the clearance is
    # target-scoped; not-quarantined passes; prefix-collision near-miss refuses.
    # ===========================================================================
    leg_sandbox g2
    TARGET="v9.9.9-pb2-target"
    OTHER="v9.9.9-pb2-other"

    # seed <quarantined-array> <history-json>
    seed() {
      local quar="$1" hist="$2"
      cat > "$INSTALL_DIR/releases/state.json" <<EOF
{"current":"v9.9.9-pb2-prev","previous":"v9.9.9-pb2-prev","in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":$quar,"history":$hist}
EOF
    }

    # drive the REAL gate: source lib.sh and call promote_entry_check.
    # promote_entry_check exits 78 on refusal (the _refuse helper), so it runs
    # in a subshell and the rc + stderr are captured.
    drive_gate() {
      local ver="$1" out rc
      out="$(INSTALL_DIR="$INSTALL_DIR" bash -c '
        . "'"$LIBSH"'" >/dev/null 2>&1
        promote_entry_check "'"$ver"'"
      ' 2>&1)"
      rc=$?
      printf '%s\n__RC__=%s\n' "$out" "$rc"
    }

    section "G2-C1 POSITIVE — quarantined AND carrying quarantine_cleared_serving_green ⇒ ACCEPT"
    seed "[\"$TARGET\"]" "[{\"ts\":\"2026-11-01T21:31:00Z\",\"event\":\"quarantine_cleared_serving_green\",\"detail\":\"target $TARGET run_id r-1 serving_green_commit prev v9.9.9-pb2-prev\"}]"
    G1="$(drive_gate "$TARGET")"
    G1RC="$(printf '%s' "$G1" | sed -n 's/^__RC__=//p')"
    G1OUT="$(printf '%s' "$G1" | grep -v '^__RC__=')"
    assert_eq "G2-C1 the gate ACCEPTS the cleared version (rc=0)" "0" "$G1RC"
    assert_contains "G2-C1 the gate logs the explicit clearance acceptance" "quarantine_cleared_serving_green event for it — accepting" "$G1OUT"
    assert_not_contains "G2-C1 NO quarantine refusal" "is QUARANTINED (prior gate failure)" "$G1OUT"

    section "G2-C2 NEGATIVE — quarantined WITHOUT a clearance event ⇒ the VERBATIM refusal"
    seed "[\"$TARGET\"]" "[]"
    G2="$(drive_gate "$TARGET")"
    G2RC="$(printf '%s' "$G2" | sed -n 's/^__RC__=//p')"
    G2OUT="$(printf '%s' "$G2" | grep -v '^__RC__=')"
    assert_eq "G2-C2 the gate REFUSES (rc=78)" "78" "$G2RC"
    assert_contains "G2-C2 the refusal is the frozen verbatim message" "promote refused: version '$TARGET' is QUARANTINED (prior gate failure) — quarantine is cleared only by re-staging the version" "$G2OUT"
    assert_contains "G2-C2 the refusal carries its reason token" "reason=quarantine" "$G2OUT"

    section "G2-C3 NON-VACUITY — a clearance event naming a DIFFERENT version does NOT clear"
    seed "[\"$TARGET\"]" "[{\"ts\":\"2026-11-01T21:31:00Z\",\"event\":\"quarantine_cleared_serving_green\",\"detail\":\"target $OTHER run_id r-1 serving_green_commit prev v9.9.9-pb2-prev\"}]"
    G3="$(drive_gate "$TARGET")"
    G3RC="$(printf '%s' "$G3" | sed -n 's/^__RC__=//p')"
    G3OUT="$(printf '%s' "$G3" | grep -v '^__RC__=')"
    assert_eq "G2-C3 the gate still REFUSES (rc=78) — the reader is target-scoped" "78" "$G3RC"
    assert_contains "G2-C3 the refusal is the frozen verbatim message" "is QUARANTINED (prior gate failure)" "$G3OUT"

    section "G2-C4 NON-VACUITY — a version NOT in quarantined[] passes regardless"
    seed "[]" "[{\"ts\":\"2026-11-01T21:31:00Z\",\"event\":\"quarantine_cleared_serving_green\",\"detail\":\"target $OTHER run_id r-1 serving_green_commit prev v9.9.9-pb2-prev\"}]"
    G4="$(drive_gate "$TARGET")"
    G4RC="$(printf '%s' "$G4" | sed -n 's/^__RC__=//p')"
    assert_eq "G2-C4 the gate ACCEPTS an un-quarantined version (rc=0)" "0" "$G4RC"

    section "G2-C5 NON-VACUITY — a PREFIX-COLLISION near-miss does NOT clear"
    seed "[\"$TARGET\"]" "[{\"ts\":\"2026-11-01T21:31:00Z\",\"event\":\"quarantine_cleared_serving_green\",\"detail\":\"target ${TARGET}-EXTRA run_id r-1 serving_green_commit prev v9.9.9-pb2-prev\"}]"
    G5="$(drive_gate "$TARGET")"
    G5RC="$(printf '%s' "$G5" | sed -n 's/^__RC__=//p')"
    assert_eq "G2-C5 the gate still REFUSES (rc=78) — no prefix-collision clearance" "78" "$G5RC"

    leg_summary
    exit $?
    fi

    # ---------------------------------------------------------------------------
    if [ "$LEG" = "g4" ]; then
    # ===========================================================================
    # LEG G4 — the BASE arm (SWEEP_SERVING_GREEN_ENABLED=0): a SERVING-GREEN
    # stale flipped txn is WRONGLY rolled back + SILENTLY quarantined + the
    # next promote REFUSED; the HEAD arm: the SAME txn is COMMITTED, not
    # punished, and the next promote is ACCEPTED.
    # ===========================================================================
    leg_sandbox g4
    TARGET="v9.9.9-pb2-target"
    PREV="v9.9.9-pb2-prev"

    # seed <quarantined-array> — a stale flipped promote txn, owner dead,
    # intent_flip present, displaced_version set, prev rollback_safe:true.
    seed() {
      local quar="${1:-[]}"
      mkdir -p "$INSTALL_DIR/releases/$TARGET" "$INSTALL_DIR/releases/$PREV" "$INSTALL_DIR/data"
      printf '{"manifest_version":"1.0.0","binary_version":"%s","rollback_safe":true}\n' "$TARGET" > "$INSTALL_DIR/releases/$TARGET/manifest.json"
      printf '{"manifest_version":"1.0.0","binary_version":"%s","rollback_safe":true}\n' "$PREV"   > "$INSTALL_DIR/releases/$PREV/manifest.json"
      ln -sfn "releases/$TARGET" "$INSTALL_DIR/current"
      cat > "$INSTALL_DIR/releases/state.json" <<EOF
{"current":"$TARGET","previous":"$PREV","in_flight":{"kind":"promote","target":"$TARGET","started_at":"2026-10-07T09:00:00Z","flipped":true,"owner_pid":99999,"last_heartbeat":1728000000,"displaced_version":"$PREV"},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":$quar,"history":[{"ts":"2026-10-07T09:00:00Z","event":"open_txn","detail":"pb2 seeded"},{"ts":"2026-10-07T10:00:00Z","event":"intent_flip","detail":"preflight: intentional promote target $TARGET — integrity verified; operator-initiated"}]}
EOF
    }

    run_sweep() {   # <install> <port> [switch]
      local fix="$1" port="$2" sw="${3:-1}"
      INSTALL_DIR="$fix" PORT="$port" SWEEP_SERVING_GREEN_ENABLED="$sw" \
      SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0 LOCK_HEARTBEAT_S=30 LOCK_STALE_S=300 \
      SWEEP_DEFER_CEILING="${SWEEP_DEFER_CEILING:-3}" \
      bash -c '
        . "'"$LAUNCHER"'" >/dev/null 2>&1
        _journal_sweep "$INSTALL_DIR"
        echo "sweep-rc=$?"
      ' 2>&1
    }

    drive_gate() {   # <ver> -> "<output>\n__RC__=<rc>"
      local ver="$1" out rc
      out="$(INSTALL_DIR="$INSTALL_DIR" bash -c '
        . "'"$LIBSH"'" >/dev/null 2>&1
        promote_entry_check "'"$ver"'"
      ' 2>&1)"
      rc=$?
      printf '%s\n__RC__=%s\n' "$out" "$rc"
    }

    ################################################################################
    section "G4-BASE-1 — the BASE-arm: a SERVING-GREEN stale flipped txn is WRONGLY rolled back"
    seed
    BPORT="$(free_port)"
    start_green_listener "$BPORT" "${TARGET#v}"
    BOUT="$(run_sweep "$INSTALL_DIR" "$BPORT" 0)"
    BJ="$(cat "$INSTALL_DIR/releases/state.json")"
    stop_listener
    printf '%s\n' "$BOUT" | tail -4
    assert_contains "G4-BASE-1 the wrongful rollback FIRED (sweep_rollback row)" '"event":"sweep_rollback"' "$BJ"
    assert_contains "G4-BASE-1 the disposition names the serving-green-disabled arm" "disposition=serving_green_disabled" "$BJ"
    assert_re    "G4-BASE-1 the target is QUARANTINED" "\"quarantined\": ?\[\"$TARGET\"\]" "$BJ"
    assert_re    "G4-BASE-1 the current was REPOINTED to prev" "\"current\": ?\"$PREV\"" "$BJ"
    assert_eq    "G4-BASE-1 the 24h rollback counter was INCREMENTED" '"24h": 1' "$(printf '%s' "$BJ" | grep -oE '"24h": ?[0-9]+' | head -1)"
    assert_re    "G4-BASE-1 the txn was CLOSED" '"in_flight": ?null' "$BJ"
    assert_not_contains "G4-BASE-1 the quarantine is SILENT (no 'quarantine' history row)" '"event":"quarantine"' "$BJ"

    section "G4-BASE-2 — the re-promote is REFUSED at the REAL gate (cooldown still armed)"
    G="$(drive_gate "$TARGET")"; RC="$(printf '%s' "$G" | sed -n 's/^__RC__=//p')"; OUT="$(printf '%s' "$G" | grep -v '^__RC__=')"
    printf '%s\n' "$OUT" | tail -3
    assert_eq "G4-BASE-2 the gate REFUSES (rc=78)" "78" "$RC"
    assert_contains "G4-BASE-2 the FIRST refusal is the armed cooldown (gate ordering)" "rollback cooldown active" "$OUT"

    section "G4-BASE-3 — the re-promote is REFUSED with the VERBATIM quarantine message (cooldown elapsed)"
    python3 - "$INSTALL_DIR/releases/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["cooldown_until"]=None
json.dump(d, open(p,"w"), separators=(",",":"))
PY
    G="$(drive_gate "$TARGET")"; RC="$(printf '%s' "$G" | sed -n 's/^__RC__=//p')"; OUT="$(printf '%s' "$G" | grep -v '^__RC__=')"
    printf '%s\n' "$OUT" | tail -3
    assert_eq "G4-BASE-3 the gate REFUSES (rc=78)" "78" "$RC"
    assert_contains "G4-BASE-3 the refusal is the VERBATIM quarantine message" "promote refused: version '$TARGET' is QUARANTINED (prior gate failure) — quarantine is cleared only by re-staging the version" "$OUT"

    ################################################################################
    section "G4-HEAD-1 — the HEAD-arm: the SAME serving-green txn is COMMITTED, not punished"
    # tonight's exact shape: the wrongful quarantine IS already applied (the
    # BASE arm's output shape), then the serving-green observation clears it.
    seed "[\"$TARGET\"]"
    HPORT="$(free_port)"
    start_green_listener "$HPORT" "${TARGET#v}"
    HOUT="$(run_sweep "$INSTALL_DIR" "$HPORT" 1)"
    HJ="$(cat "$INSTALL_DIR/releases/state.json")"
    stop_listener
    printf '%s\n' "$HOUT" | tail -4
    assert_contains "G4-HEAD-1 the commit-close landed (serving_green_commit)" '"event":"serving_green_commit"' "$HJ"
    assert_contains "G4-HEAD-1 the quarantine-clearance event was written" '"event":"quarantine_cleared_serving_green"' "$HJ"
    assert_not_contains "G4-HEAD-1 NO wrongful rollback" '"event":"sweep_rollback"' "$HJ"
    assert_not_re "G4-HEAD-1 NO quarantine punishment (the array was rewritten)" "\"quarantined\": ?\[\"$TARGET\"\]" "$HJ"
    assert_re    "G4-HEAD-1 the current STAYS at the target" "\"current\": ?\"$TARGET\"" "$HJ"
    assert_re    "G4-HEAD-1 the txn was CLOSED" '"in_flight": ?null' "$HJ"
    assert_eq    "G4-HEAD-1 the 24h rollback counter was NOT incremented" '"24h":0' "$(printf '%s' "$HJ" | grep -oE '"24h": ?[0-9]+' | head -1)"

    section "G4-HEAD-2 — the next promote is ACCEPTED at the REAL gate"
    G="$(drive_gate "$TARGET")"; RC="$(printf '%s' "$G" | sed -n 's/^__RC__=//p')"; OUT="$(printf '%s' "$G" | grep -v '^__RC__=')"
    printf '%s\n' "$OUT" | tail -3
    assert_eq "G4-HEAD-2 the gate ACCEPTS (rc=0)" "0" "$RC"
    assert_not_contains "G4-HEAD-2 NO quarantine refusal" "is QUARANTINED (prior gate failure)" "$OUT"

    leg_summary
    exit $?
    fi

    # ---------------------------------------------------------------------------
    if [ "$LEG" = "g5" ]; then
    # ===========================================================================
    # LEG G5 — interleaved: (i) absent,absent,GREEN,absent,absent ⇒ the counter
    # climbs to 2 (one below the ceiling of 3 — NON-VACUITY), the green
    # observation COMMITS, the counter file is deleted on commit, and NO
    # sweep_rollback is EVER written; (ii) absent,GREEN,absent×4 with NO
    # intent_flip ⇒ no commit, no rollback, no counter, the
    # 'green-without-intent-flip' marker, the txn stays OPEN.
    # ===========================================================================
    leg_sandbox g5
    TARGET="v9.9.9-pb2-target"
    PREV="v9.9.9-pb2-prev"
    ABSENT_PORT="$(absent_port)"
    assert_eq "G5 fixture the absent-probe port is not a reserved service port" "no" "$(is_reserved_port "$ABSENT_PORT" && printf yes || printf no)"

    # seed <intent 0|1>
    seed() {
      local intent="${1:-1}" intent_row=""
      mkdir -p "$INSTALL_DIR/releases/$TARGET" "$INSTALL_DIR/releases/$PREV"
      printf '{"manifest_version":"1.0.0","binary_version":"%s","rollback_safe":true}\n' "$TARGET" > "$INSTALL_DIR/releases/$TARGET/manifest.json"
      printf '{"manifest_version":"1.0.0","binary_version":"%s","rollback_safe":true}\n' "$PREV"   > "$INSTALL_DIR/releases/$PREV/manifest.json"
      ln -sfn "releases/$TARGET" "$INSTALL_DIR/current"
      if [ "$intent" = "1" ]; then
        intent_row=',{"ts":"2026-10-07T10:00:00Z","event":"intent_flip","detail":"preflight: intentional promote target '"$TARGET"' — integrity verified; operator-initiated"}'
      fi
      cat > "$INSTALL_DIR/releases/state.json" <<EOF
{"current":"$TARGET","previous":"$PREV","in_flight":{"kind":"promote","target":"$TARGET","started_at":"2026-10-07T09:00:00Z","flipped":true,"owner_pid":99999,"last_heartbeat":1728000000,"displaced_version":"$PREV"},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-10-07T09:00:00Z","event":"open_txn","detail":"pb2 seeded"}$intent_row]}
EOF
    }

    # run_sweep <port> — the pack's own harness pattern
    # (promote_handback_halt_serving_green_test.sh:113-124)
    run_sweep() {
      local port="$1"
      INSTALL_DIR="$INSTALL_DIR" PORT="$port" \
      SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0 LOCK_HEARTBEAT_S=30 LOCK_STALE_S=300 \
      SWEEP_DEFER_CEILING="${SWEEP_DEFER_CEILING:-3}" \
      bash -c '
        . "'"$LAUNCHER"'" >/dev/null 2>&1
        _journal_sweep "$INSTALL_DIR"
        echo "sweep-rc=$?"
      ' 2>&1
    }

    counter_absent() {   # the launcher-local counter's absent= value (0 if no file)
      local f
      for f in "$INSTALL_DIR"/.sweep-defer-*; do
        [ -e "$f" ] || continue
        sed -n 's/^absent=//p' "$f" | head -1
        return 0
      done
      printf '0'
    }
    counter_file_present() {
      ls "$INSTALL_DIR"/.sweep-defer-* >/dev/null 2>&1 && printf 'yes' || printf 'no'
    }

    ################################################################################
    section "G5-I1 — INTERLEAVED (intent_flip present): absent, absent, GREEN, absent, absent"
    seed 1
    # observation 1: ABSENT (nothing listens on the probe port)
    run_sweep "$ABSENT_PORT" >/dev/null 2>&1
    J1="$(cat "$INSTALL_DIR/releases/state.json")"
    assert_contains "G5-I1 obs1 (absent) ⇒ the observation DEFERS" '"event":"sweep_deferred"' "$J1"
    assert_not_contains "G5-I1 obs1 (absent) ⇒ NO rollback yet" '"event":"sweep_rollback"' "$J1"
    assert_eq "G5-I1 obs1 ⇒ the counter climbed to absent=1" "1" "$(counter_absent)"
    # observation 2: ABSENT again — the counter must reach 2 (ONE BELOW the ceiling of 3)
    run_sweep "$ABSENT_PORT" >/dev/null 2>&1
    J2="$(cat "$INSTALL_DIR/releases/state.json")"
    assert_eq "G5-I1 obs2 ⇒ the counter climbed to absent=2 (NON-VACUITY: the ceiling was ARMING)" "2" "$(counter_absent)"
    assert_not_contains "G5-I1 obs2 ⇒ still NO rollback (2 < ceiling 3)" '"event":"sweep_rollback"' "$J2"

    # observation 3: GREEN — the intervening serving-green observation
    GPORT="$(free_port)"
    start_green_listener "$GPORT" "${TARGET#v}"
    run_sweep "$GPORT" >/dev/null 2>&1
    stop_listener
    J3="$(cat "$INSTALL_DIR/releases/state.json")"
    assert_contains "G5-I1 obs3 (GREEN) ⇒ the serving-green commit-close landed" '"event":"serving_green_commit"' "$J3"
    assert_not_contains "G5-I1 obs3 (GREEN) ⇒ NO rollback" '"event":"sweep_rollback"' "$J3"
    assert_eq "G5-I1 obs3 ⇒ the counter file is DELETED on commit (P-29)" "no" "$(counter_file_present)"

    # observations 4+5: ABSENT again — the ceiling must NEVER fire
    run_sweep "$ABSENT_PORT" >/dev/null 2>&1
    run_sweep "$ABSENT_PORT" >/dev/null 2>&1
    J5="$(cat "$INSTALL_DIR/releases/state.json")"
    assert_not_contains "G5-I1 obs4/5 ⇒ the absent-ceiling NEVER fired" '"event":"sweep_rollback"' "$J5"
    assert_re "G5-I1 the 24h rollback counter was NEVER incremented" '"24h": ?0' "$J5"
    assert_re "G5-I1 the current STAYS at the target" "\"current\": ?\"$TARGET\"" "$J5"

    ################################################################################
    section "G5-I2 — INTERLEAVED (NO intent_flip): absent, GREEN, absent, absent, absent, absent"
    seed 0
    run_sweep "$ABSENT_PORT" >/dev/null 2>&1
    J1="$(cat "$INSTALL_DIR/releases/state.json")"
    assert_contains "G5-I2 obs1 (absent, no intent_flip) ⇒ DEFERS" '"event":"sweep_deferred"' "$J1"
    assert_not_contains "G5-I2 obs1 ⇒ NO rollback" '"event":"sweep_rollback"' "$J1"
    assert_eq "G5-I2 obs1 ⇒ a NON-QUALIFYING observation writes NO counter" "0" "$(counter_absent)"

    GPORT2="$(free_port)"
    start_green_listener "$GPORT2" "${TARGET#v}"
    run_sweep "$GPORT2" >/dev/null 2>&1
    stop_listener
    J2="$(cat "$INSTALL_DIR/releases/state.json")"
    assert_not_contains "G5-I2 obs2 (GREEN, no intent_flip) ⇒ NO commit (unauthorized)" '"event":"serving_green_commit"' "$J2"
    assert_contains "G5-I2 obs2 ⇒ the greppable green-without-intent-flip marker" 'green-without-intent-flip' "$J2"
    assert_not_contains "G5-I2 obs2 ⇒ NO rollback" '"event":"sweep_rollback"' "$J2"

    # four MORE absent observations — one past the ceiling of 3
    for _ in 1 2 3 4; do run_sweep "$ABSENT_PORT" >/dev/null 2>&1; done
    J6="$(cat "$INSTALL_DIR/releases/state.json")"
    assert_not_contains "G5-I2 obs3-6 ⇒ the absent-ceiling NEVER fired" '"event":"sweep_rollback"' "$J6"
    assert_re "G5-I2 the 24h rollback counter was NEVER incremented" '"24h": ?0' "$J6"
    assert_re "G5-I2 the txn stayed OPEN (never committed, never rolled back)" '"in_flight": ?\{' "$J6"

    leg_summary
    exit $?
    fi

    printf 'unknown leg: %s\n' "$LEG" >&2
    exit 64
fi

################################################################################
# PARENT MODE — drive the three legs, each under its OWN timeout (the dual
# layer), collect per-leg PASS/FAIL counters, emit the aggregate RESULT line.
################################################################################
LEG_LOGDIR="$(mktemp -d /tmp/phh-gate-legs-logs.XXXXXX)"
trap 'rm -rf "$LEG_LOGDIR"' EXIT

EXPECTED_ASSERTS_G2=10
EXPECTED_ASSERTS_G4=20
# G5: the verbatim fold carries 20 asserts + 1 ADDED fixture-guard assert
# (the absent-probe port is never a reserved service port) = 21.
EXPECTED_ASSERTS_G5=21

FENCE=(env -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_USER -u POSTGRES_PASSWORD
       -u DATABASE_URL -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD)

LEG_TIMEOUT_S=300
LEGS_FAILED=0
TIMED_OUT=0
TOTAL_ASSERTS=0
TOTAL_FAILED_ASSERTS=0

run_one_leg() {
    local name="$1" expect="$2" log rc legpass legfail
    log="$LEG_LOGDIR/leg_${name}.log"
    printf '%s\n' "== leg $name (expected asserts: $expect, timeout: ${LEG_TIMEOUT_S}s) =="
    timeout "$LEG_TIMEOUT_S" "${FENCE[@]}" bash "$PACK_SELF" --leg "$name" >"$log" 2>&1
    rc=$?
    legpass="$(sed -n 's/^LEG_PASS=//p' "$log" | tail -1 | awk '{print $1}')"
    legfail="$(sed -n 's/^LEG_FAIL=//p' "$log" | tail -1 | awk '{print $1}')"
    if [ "$rc" -eq 124 ]; then
        TIMED_OUT=$((TIMED_OUT + 1))
        LEGS_FAILED=$((LEGS_FAILED + 1))
        printf '   leg %s: TIMEOUT (rc=124) after %ss\n' "$name" "$LEG_TIMEOUT_S"
        printf '   ---- last lines ----\n'; tail -5 "$log" | sed 's/^/   | /'
        return 1
    fi
    if [ -z "$legpass" ]; then legpass=0; fi
    if [ -z "$legfail" ]; then legfail=0; fi
    TOTAL_ASSERTS=$((TOTAL_ASSERTS + legpass + legfail))
    TOTAL_FAILED_ASSERTS=$((TOTAL_FAILED_ASSERTS + legfail))
    if [ "$rc" -ne 0 ] || [ "$legfail" -gt 0 ]; then
        LEGS_FAILED=$((LEGS_FAILED + 1))
        printf '   leg %s: FAIL (rc=%s, asserts %s passed / %s failed)\n' "$name" "$rc" "$legpass" "$legfail"
        printf '   ---- failing legs tail ----\n'; tail -20 "$log" | sed 's/^/   | /'
        return 1
    fi
    printf '   leg %s: PASS (rc=0, asserts %s/%s)\n' "$name" "$legpass" "$legpass"
    return 0
}

run_one_leg g2 "$EXPECTED_ASSERTS_G2" || true
run_one_leg g4 "$EXPECTED_ASSERTS_G4" || true
run_one_leg g5 "$EXPECTED_ASSERTS_G5" || true

RESULT_RC=0
if [ "$TIMED_OUT" -gt 0 ]; then
    RESULT_RC=124
elif [ "$LEGS_FAILED" -gt 0 ]; then
    RESULT_RC=1
fi

printf '\n'
printf 'RESULT pack=promote_handback_halt_gate_legs_test legs=3 legs_failed=%s timed_out=%s asserts=%s asserts_failed=%s expected_asserts=%s rc=%s\n' \
    "$LEGS_FAILED" "$TIMED_OUT" "$TOTAL_ASSERTS" "$TOTAL_FAILED_ASSERTS" \
    "$((EXPECTED_ASSERTS_G2 + EXPECTED_ASSERTS_G4 + EXPECTED_ASSERTS_G5))" "$RESULT_RC"
printf 'logs: %s\n' "$LEG_LOGDIR"
exit "$RESULT_RC"
