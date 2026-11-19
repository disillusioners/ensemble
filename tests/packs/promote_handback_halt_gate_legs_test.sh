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
    section "G4-BASE-1 — the BASE arm can NO LONGER wrongfully roll back a serving-green txn (D5 Wave 3)"
    # self-service-promote Wave 3 UPDATE: the BASE arm's documented defect
    # (a serving-green stale flipped txn WRONGLY rolled back under
    # SWEEP_SERVING_GREEN_ENABLED=0) is CLOSED by the chain head's
    # verify-before-rollback block (V1/V2) — the kill-switch still disables
    # the LADDER's green commit-close arm, but the CHAIN itself refuses to
    # execute a rollback of a serving-green release. The BASE expectation
    # flips from "wrongly rolled back" to "protected: committed via the
    # chain-head green path" (AC-D5-8's protection holds on EVERY path).
    seed
    BPORT="$(free_port)"
    start_green_listener "$BPORT" "${TARGET#v}"
    BOUT="$(run_sweep "$INSTALL_DIR" "$BPORT" 0)"
    BJ="$(cat "$INSTALL_DIR/releases/state.json")"
    stop_listener
    printf '%s\n' "$BOUT" | tail -4
    assert_not_contains "G4-BASE-1 NO wrongful rollback (the chain head protects serving-green)" '"event":"sweep_rollback"' "$BJ"
    assert_contains "G4-BASE-1 the chain-head GREEN PATH committed the txn" '"event":"serving_green_commit"' "$BJ"
    assert_not_re "G4-BASE-1 the target is NOT QUARANTINED" "\"quarantined\": ?\[\"$TARGET\"\]" "$BJ"
    assert_re    "G4-BASE-1 the current STAYS at the target" "\"current\": ?\"$TARGET\"" "$BJ"
    assert_eq    "G4-BASE-1 the 24h rollback counter was NOT incremented" '"24h":0' "$(printf '%s' "$BJ" | grep -oE '"24h": ?[0-9]+' | head -1)"
    assert_re    "G4-BASE-1 the txn was CLOSED" '"in_flight": ?null' "$BJ"
    assert_not_contains "G4-BASE-1 no quarantine history row" '"event":"quarantine"' "$BJ"

    section "G4-BASE-2 — the re-promote is ACCEPTED (no cooldown, no quarantine — the punishment never fired)"
    G="$(drive_gate "$TARGET")"; RC="$(printf '%s' "$G" | sed -n 's/^__RC__=//p')"; OUT="$(printf '%s' "$G" | grep -v '^__RC__=')"
    printf '%s\n' "$OUT" | tail -3
    assert_eq "G4-BASE-2 the gate ACCEPTS (rc=0 — nothing was armed against the promote)" "0" "$RC"
    assert_not_contains "G4-BASE-2 NO cooldown refusal" "rollback cooldown active" "$OUT"
    assert_not_contains "G4-BASE-2 NO quarantine refusal" "is QUARANTINED (prior gate failure)" "$OUT"

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

    if [ "$LEG" = "d6" ]; then
    # ===========================================================================
    # LEG D6 — TRUTH & OBSERVABILITY (self-service-promote Wave 1; changes NO
    # verdict). Three ACs in one leg, because they share ONE fixture (the
    # real unit hand-back with stubbed systemctl/lsof/curl):
    #
    #   AC-D6-1 / AC-D6-2  the is-active probe ANSWERED rc=3 ("not active" —
    #       the EXPECTED pre-start value: the unit is deliberately stopped
    #       pre-flip) ⇒ NO `subprocess_wait_timeout` row with
    #       `site=handback_is_active`, and the OBSERVED STATE WORD is
    #       journaled. The old shape journalled rc=3 as a TIMEOUT (a lie:
    #       _run_bounded maps ONLY 137|143 → 124), which misdirected triage
    #       for ~10h.
    #   AC-D6-6  the halt row carries the VERIFIER-PATH reason — the
    #       attribution refusal detail (attribution arm) or the observed
    #       /livez version (version arms). The launchd HB_ERR channel stays
    #       launchd-only (C-9: the systemd path never writes it) — the
    #       reason rides its OWN channel. "none captured" can never recur
    #       on the systemd path.
    #   AC-OBS-3 / P-6  the VERIFY-PHASE narrative reaches the executor's
    #       PAYLOAD log (the unit's stdio) — not only the shell's narrative
    #       file. F16/C-8: both surviving runs ended at the verify budget
    #       line, because `_warn` routes to the narrative sink while the
    #       payload log is a DIFFERENT file (the unit's stderr). The flush
    #       MECHANISM is asserted (unbuffered stderr + an explicit _warn
    #       before EACH fail-fast arm), not "exactly N lines".
    #
    # The halt row is composed by promote.sh's OWN template — the leg
    # EXTRACTS that template from the shipped script and EXECUTES it
    # against the channel the real hand-back left behind (no grep, no
    # re-implementation: removing the substitution turns the leg RED).
    # ===========================================================================
    leg_sandbox d6

    # The journal SPLICE helper refuses a journal path under
    # "$HOME/agents-ensemble" (the live-install guard —
    # journal_splice.py _is_under_live) unless the pipeline lock is held.
    # leg_sandbox's HOME fence makes that path exactly the sandbox's own
    # install root ⇒ the shell-side journal writer would refuse EVERY row
    # (the g2/g4/g5 legs are unaffected: they drive the LAUNCHER's writer).
    # The sandbox install root therefore takes a different name under the
    # SAME FAKE_HOME — the pack's escape guard (INSTALL_DIR under
    # FAKE_HOME, FAKE_HOME not the live install path) still holds.
    INSTALL_DIR="$FAKE_HOME/installs/agents-ensemble"
    mkdir -p "$INSTALL_DIR/releases"
    D6STUB="$(mktemp -d "/home/nea/.phh-d6-stub.XXXXXX")"
    chmod 700 "$D6STUB"
    trap 'rm -rf "$FAKE_HOME" "$D6STUB"; [ -n "$LISTENER_PID" ] && kill "$LISTENER_PID" 2>/dev/null' EXIT

    # The systemctl stub — `is-active` answers with an INJECTABLE rc (P-3:
    # the stub used to answer rc=0 ALWAYS, so the whole non-timeout rc
    # space was unpinned) and prints the STATE WORD on stdout (as real
    # systemctl does).
    cat > "$D6STUB/systemctl" <<'D6STUBEOF'
#!/bin/bash
echo "$*" >> "${SC_LOG:-/dev/null}"
sub="$1"; shift
case "$sub" in
    is-active)
        echo "${SC_IS_ACTIVE:-inactive}"
        exit "${SC_IS_ACTIVE_RC:-0}" ;;
    show)
        prop=""; props=""
        while [ $# -gt 0 ]; do
            case "$1" in -p) prop="$2"; props="${props} $2"; shift 2 ;; --value) shift ;; *) shift ;; esac
        done
        # D6 (self-service-promote Wave 2): the verify's bounded 3-field
        # state probe answers in the ASSIGNMENT form.
        case "$props" in
            *" ActiveState "*)
                echo "ActiveState=${SC_ACTIVESTATE:-active}"
                echo "SubState=${SC_SUBSTATE:-running}"
                echo "MainPID=${SC_MAINPID:-0}"
                exit 0 ;;
        esac
        case "$prop" in
            MainPID) echo "${SC_MAINPID:-0}" ;;
            *) echo "" ;;
        esac
        exit 0 ;;
    start) exit "${SC_START_RC:-0}" ;;
    *) exit 0 ;;
esac
D6STUBEOF
    chmod +x "$D6STUB/systemctl"

    # The lsof stub — the LISTEN holder of the probe port. Two modes:
    # OWNED (424242 — the fixture IS the daemon) and FOREIGN (999999 — the
    # attribution-refusal cell).
    cat > "$D6STUB/lsof" <<'D6STUBEOF'
#!/bin/bash
printf '%s\n' "${D6_HOLDER:-424242}"
exit 0
D6STUBEOF
    chmod +x "$D6STUB/lsof"

    # The curl stub — /livez answers 2xx with a VERSION.
    cat > "$D6STUB/curl" <<'D6STUBEOF'
#!/bin/bash
out=""
while [ $# -gt 0 ]; do
    case "$1" in -o) out="$2"; shift 2 ;; -w) shift ;; --max-time) shift ;; -sS|-s|-S) shift ;; *) shift ;; esac
done
[ -n "$out" ] && printf '{"status":"alive","version":"%s"}\n' "${D6_VERSION:-0.28.5}" > "$out"
printf '200'
D6STUBEOF
    chmod +x "$D6STUB/curl"

    DPATH="$D6STUB:$PATH"
    DPORT="$(absent_port)"
    SC_LOG="$INSTALL_DIR/sc.log"

    # seed_journal — the sandbox journal (the shape the splice helper edits).
    d6_seed() {
        cat > "$INSTALL_DIR/releases/state.json" <<'JSON'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JSON
    }
    d6_seed

    # d6_run <extra-shell-setup> — the REAL unit hand-back (the same call
    # the ceremony makes: restart_via_launcher) in a scrubbed shell.
    d6_run() {
        PATH="$DPATH" INSTALL_DIR="$INSTALL_DIR" SYSTEMCTL_BIN="$D6STUB/systemctl" \
        SC_LOG="$SC_LOG" PORT="$DPORT" \
        PROMOTE_EXPECTED_VERSION=0.28.5 HANDBACK_VERIFY_BUDGET_S=4 \
        D6_HOLDER="${D6_HOLDER:-424242}" D6_VERSION="${D6_VERSION:-0.28.5}" \
        bash -c '
            unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
            . "'"$LIBSH"'" >/dev/null 2>&1
            _supervision_owned_pids() { printf "%s\n" 424242; }
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-d6.service; SUPERVISION_PRESTOP_MAINPID=777
            '"$1"'
            restart_via_launcher
            echo "rvl-rc=$?"
            echo "REASON=[$(_handback_verify_reason_text)]"
        ' 2>&1
    }

    # ── D6-A — the is-active probe ANSWERED rc=3 (AC-D6-1 / AC-D6-2) ─────
    section "D6-A — the is-active answer rc=3 is NOT a timeout (AC-D6-1 / AC-D6-2)"
    d6_seed
    D6A_OUT="$(d6_run 'export SC_IS_ACTIVE=inactive SC_IS_ACTIVE_RC=3')"
    D6A_J="$(cat "$INSTALL_DIR/releases/state.json")"
    assert_eq "D6-A1 the hand-back continues into the start attempt (rc=1: the verify, not the probe, decides)" \
        "rvl-rc=1" "$(printf '%s' "$D6A_OUT" | grep -o 'rvl-rc=[0-9]*' | head -1)"
    assert_eq "D6-A2 NO subprocess_wait_timeout row with site=handback_is_active (rc=3 is not a timeout)" \
        "0" "$(printf '%s' "$D6A_J" | grep -F 'subprocess_wait_timeout' | grep -c 'site=handback_is_active' || true)"
    assert_contains "D6-A3 the OBSERVED state word is journaled" "state=inactive" "$D6A_J"
    assert_contains "D6-A4 the row is the STATE kind (a new, additive, non-terminal kind)" "handback_is_active_state" "$D6A_J"
    assert_contains "D6-A5 the observed rc is journaled" "rc=3" "$D6A_J"

    # ── D6-B — the halt row carries the verifier-path reason (AC-D6-6) ────
    section "D6-B — the halt row carries the reason (AC-D6-6, version arm)"
    # The REAL promote.sh halt template — extracted verbatim and EXECUTED.
    # The extraction takes the FIRST `if ! restart_via_launcher; then` block
    # only (promote.sh has a SECOND one in its rollback section — the
    # awk stops at the first `fi` so the body is exactly the promote
    # halt block, with no stray `fi` left to break the shell parse).
    D6_HALT_BODY="$(awk '
        /^if ! restart_via_launcher; then$/ && !seen { inblock=1; seen=1; next }
        inblock && /^fi$/ { exit }
        inblock { print }
    ' "$REPO_ROOT/scripts/upgrade/promote.sh")"
    if [ -z "$D6_HALT_BODY" ]; then
        _fail "D6-B0 the promote.sh halt template is extractable" "non-empty" "empty"
    else
        _pass "D6-B0 the promote.sh halt template is extractable"
    fi
    d6_seed
    # The hand-back MUST reach the SERVING probe for the version arm to
    # fire: the stub reports a NEW MainPID (≠ the pre-stop pid) so the
    # verify proceeds to the /livez conjunct (MainPID=0 would only ever
    # produce the "not confirmed" verdict).
    D6B_OUT="$(PATH="$DPATH" INSTALL_DIR="$INSTALL_DIR" SYSTEMCTL_BIN="$D6STUB/systemctl" \
        SC_LOG="$SC_LOG" PORT="$DPORT" PROMOTE_EXPECTED_VERSION=0.28.5 \
        HANDBACK_VERIFY_BUDGET_S=4 D6_HOLDER=424242 D6_VERSION=0.0.0 SC_MAINPID=9999 \
        bash -c '
            unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
            . "'"$LIBSH"'" >/dev/null 2>&1
            _supervision_owned_pids() { printf "%s\n" 424242; }
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-d6.service; SUPERVISION_PRESTOP_MAINPID=777
            VERSION=v9.9.9-d6
            restart_via_launcher >/dev/null; echo "rvl-rc=$?"
            '"$D6_HALT_BODY"'
        ' 2>&1)"
    D6B_J="$(cat "$INSTALL_DIR/releases/state.json")"
    assert_contains "D6-B1 the hand-back FAILED (the version-mismatch fail-fast)" "rvl-rc=1" "$D6B_OUT"
    assert_contains "D6-B2 the halt row carries the OBSERVED /livez version" "observed=0.0.0" "$D6B_J"
    assert_contains "D6-B3 the halt row names the EXPECTED version too" "expected=0.28.5" "$D6B_J"
    assert_not_contains "D6-B4 the halt row NEVER reads 'none captured' on the systemd path" \
        "none captured" "$D6B_J"
    assert_contains "D6-B5 the halt row is still a halt-for-human (the vocabulary is unchanged)" \
        "halt-for-human, txn left open" "$D6B_J"

    # ── D6-C — the verify-phase narrative reaches the PAYLOAD log ─────────
    # (AC-OBS-3 / P-6). The executor unit's stdio is
    # StandardOutput/StandardError=append:<run_id>.payload.log — emulated
    # here by redirecting the shell's stderr into the payload file, with
    # the narrative sink set exactly as spawn_executor threads it
    # (ENSEMBLE_UPGRADE_LOG_FILE). The two files are DIFFERENT sinks; the
    # bug (F16) was that the verify-phase diagnostics only ever reached the
    # narrative one, so the payload log ended at the budget line.
    section "D6-C — the verify-phase narrative reaches the payload log (AC-OBS-3 / P-6)"
    d6_seed
    D6NARR="$INSTALL_DIR/data/upgrade/demo/d6.log"
    D6PAY="$INSTALL_DIR/data/upgrade/demo/d6.payload.log"
    mkdir -p "$(dirname "$D6NARR")"
    PATH="$DPATH" INSTALL_DIR="$INSTALL_DIR" SYSTEMCTL_BIN="$D6STUB/systemctl" \
    SC_LOG="$SC_LOG" PORT="$DPORT" PROMOTE_EXPECTED_VERSION=0.28.5 \
    HANDBACK_VERIFY_BUDGET_S=4 D6_HOLDER=424242 D6_VERSION=0.0.0 \
    ENSEMBLE_UPGRADE_LOG_FILE="$D6NARR" \
    bash -c '
        unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
        . "'"$LIBSH"'" >/dev/null 2>&1
        _supervision_owned_pids() { printf "%s\n" 424242; }
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-d6.service; SUPERVISION_PRESTOP_MAINPID=777
        restart_via_launcher >/dev/null || true
    ' >"$D6PAY" 2>&1
    assert_contains "D6-C1 the payload log carries the verify BUDGET line" \
        "hand-back verify: budget effective=" "$(cat "$D6PAY")"
    assert_contains "D6-C2 the payload log carries a verify-phase DIAGNOSTIC (the F16 flush mechanism)" \
        "WARN: hand-back verify:" "$(cat "$D6PAY")"
    # The truncation repro (C-8): the payload log used to END at the budget
    # line. The diagnostic must come AFTER it.
    D6_BUDGET_LINE="$(grep -n 'hand-back verify: budget effective=' "$D6PAY" | head -1 | cut -d: -f1)"
    D6_DIAG_LINE="$(grep -n 'WARN: hand-back verify:' "$D6PAY" | head -1 | cut -d: -f1)"
    if [ -n "$D6_BUDGET_LINE" ] && [ -n "$D6_DIAG_LINE" ] && [ "$D6_DIAG_LINE" -gt "$D6_BUDGET_LINE" ]; then
        _pass "D6-C3 the payload log does NOT truncate at the budget line (the diagnostic follows it)"
    else
        _fail "D6-C3 the payload log does NOT truncate at the budget line (the diagnostic follows it)" \
            "diag line > budget line" "budget=${D6_BUDGET_LINE:-none} diag=${D6_DIAG_LINE:-none}"
    fi
    assert_contains "D6-C4 the narrative sink still carries the same diagnostic (the _warn twin)" \
        "WARN: hand-back verify:" "$(cat "$D6NARR")"

    leg_summary
    exit $?
    fi

    # ---------------------------------------------------------------------------
    if [ "$LEG" = "d5" ]; then
    # ===========================================================================
    # LEG D5 (self-service-promote Wave 3) — the 4-DISTINCT pack seed variant
    # (P-2's second half, AC-D5-3) + the resolved-target assertions + the
    # credits (AC-D5-10) + the green-evidence protection (AC-D5-8).
    # ===========================================================================
    leg_sandbox d5
    TARGET="v9.9.9-d5p-target"
    VA="v9.9.9-d5p-prevA"     # journal.previous    (R4)
    VB="v9.9.9-d5p-dispB"     # displaced_version  (R3)
    VC="v9.9.9-d5p-curC"      # journal.current    (R2)
    VD="v9.9.9-d5p-capD"      # captured_previous  (R0) — MUST win

    # seed4 — the FOUR-DISTINCT variant: every rollback-target source is a
    # DIFFERENT release, so a wrong-target rollback is VISIBLE (every
    # legacy pack seed set previous == displaced == current, which is why
    # the C-4 wrong-rollback defect was invisible — blind pin P-2).
    seed4() {
      mkdir -p "$INSTALL_DIR/releases/$TARGET" "$INSTALL_DIR/releases/$VA" \
               "$INSTALL_DIR/releases/$VB" "$INSTALL_DIR/releases/$VC" \
               "$INSTALL_DIR/releases/$VD" "$INSTALL_DIR/data"
      local v
      for v in "$TARGET" "$VA" "$VB" "$VC" "$VD"; do
        printf '{"manifest_version":"1.0.0","binary_version":"%s","rollback_safe":true}\n' "$v" \
          > "$INSTALL_DIR/releases/$v/manifest.json"
      done
      ln -sfn "releases/$TARGET" "$INSTALL_DIR/current"
      cat > "$INSTALL_DIR/releases/state.json" <<EOF
{"current":"$VC","previous":"$VA","in_flight":{"kind":"promote","target":"$TARGET","started_at":"2026-10-07T09:00:00Z","flipped":true,"owner_pid":99999,"last_heartbeat":1728000000,"displaced_version":"$VB","captured_previous":"$VD"},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-10-07T09:00:00Z","event":"open_txn","detail":"d5p seeded"},{"ts":"2026-10-07T10:00:00Z","event":"intent_flip","detail":"preflight: intentional promote target $TARGET — integrity verified; operator-initiated"}]}
EOF
    }

    run_sweep4() {   # <install> <port> [ceiling]
      local fix="$1" port="$2" ceil="${3:-1}"
      INSTALL_DIR="$fix" PORT="$port" SWEEP_DEFER_CEILING="$ceil" \
      SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0 LOCK_HEARTBEAT_S=30 LOCK_STALE_S=300 \
      SWEEP_SERVING_GREEN_ENABLED="${SWEEP_SERVING_GREEN_ENABLED:-1}" \
      bash -c '
        . "'"$LAUNCHER"'" >/dev/null 2>&1
        _journal_sweep "$INSTALL_DIR"
        echo "sweep-rc=$?"
      ' 2>&1
    }

    section "D5-A — 4 DISTINCT sources ⇒ repoint to the CAPTURE (vD), source=captured (AC-D5-3, P-2)"
    seed4
    AOUT="$(run_sweep4 "$INSTALL_DIR" 19999 1)"
    AJ="$(cat "$INSTALL_DIR/releases/state.json")"
    printf '%s\n' "$AOUT" | tail -3
    assert_eq "D5-A the repoint went to the CAPTURE vD — NOT previous/displaced/current" \
        "releases/$VD" "$(readlink "$INSTALL_DIR/current")"
    assert_contains "D5-A the sweep_rollback detail names the resolved target + source=captured" \
        "rolled back to $VD source=captured" "$AJ"
    assert_eq "D5-A journal current = the capture (not vA/vB/vC)" "$VD" \
        "$(printf '%s' "$AJ" | grep -oE '"current": ?"[^"]+"' | head -1 | sed 's/.*"current": *"//;s/"//')"
    assert_re "D5-A the txn was CLOSED" '"in_flight": ?null' "$AJ"
    assert_eq "D5-A the 24h counter = 1 (ONE credit for the executed repoint — AC-D5-10 Tier-B)" \
        '"24h": 1' "$(printf '%s' "$AJ" | grep -oE '"24h": ?[0-9]+' | head -1)"
    assert_contains "D5-A the evidence tier is journaled distinctly (tierB — ceiling-executed)" \
        "evidence=tierB" "$AJ"
    assert_not_contains "D5-A the FAILED target is quarantined only, never the capture" \
        "\"quarantined\": ?\[\"$VD\"" "$AJ"
    assert_re "D5-A the failed target IS quarantined" "\"quarantined\": ?\[.*\"$TARGET\"" "$AJ"

    section "D5-B — halt / resolution-failure ⇒ rollback_window_count stays 0 (AC-D5-10)"
    seed4
    # force the AMBIGUITY halt: drop the capture, leave current ≠ displaced
    python3 - "$INSTALL_DIR/releases/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p))
d["in_flight"].pop("captured_previous", None)
json.dump(d, open(p,"w"), separators=(",",":"))
PY
    BOUT="$(run_sweep4 "$INSTALL_DIR" 19999 1)"
    BJ="$(cat "$INSTALL_DIR/releases/state.json")"
    printf '%s\n' "$BOUT" | tail -3
    assert_contains "D5-B the ambiguity halt fired (no capture + current≠displaced)" "rollback_target_ambiguous" "$BJ"
    assert_eq "D5-B rollback_window_count == 0 (a halt consumes NO credit)" \
        '"24h":0' "$(printf '%s' "$BJ" | grep -oE '"24h": ?[0-9]+' | head -1)"
    assert_not_re "D5-B txn OPEN" '"in_flight": ?null' "$BJ"

    section "D5-C — green-evidence + a rollback_requested ⇒ the GREEN PATH (AC-D5-8)"
    seed4
    # a serving_green_commit row for the target + a rollback_requested row:
    # V1 must take the green path — no sweep_rollback, no quarantine entry,
    # no counter increment.
    python3 - "$INSTALL_DIR/releases/state.json" "$TARGET" <<'PY'
import json,sys
p=sys.argv[1]; t=sys.argv[2]; d=json.load(open(p))
d["history"].append({"ts":"2026-10-07T11:00:00Z","event":"serving_green_commit",
    "detail":f"target {t} run_id {t}@2026-10-07T09:00:00Z prev v9.9.9-x — serving green"})
d["history"].append({"ts":"2026-10-07T11:01:00Z","event":"rollback_requested",
    "detail":f"target {t} run_id {t}@2026-10-07T09:00:00Z reason not-ready to none to_source none"})
json.dump(d, open(p,"w"), separators=(",",":"))
PY
    CPORT="$(free_port)"
    start_green_listener "$CPORT" "${TARGET#v}"
    COUT="$(run_sweep4 "$INSTALL_DIR" "$CPORT" 1)"
    CJ="$(cat "$INSTALL_DIR/releases/state.json")"
    stop_listener
    printf '%s\n' "$COUT" | tail -3
    assert_not_contains "D5-C NO sweep_rollback (the green path is taken)" '"event":"sweep_rollback"' "$CJ"
    assert_not_re "D5-C NO quarantine entry (the target was never punished)" "\"quarantined\": ?\[.*\"$TARGET\"" "$CJ"
    assert_eq "D5-C the counter NOT incremented" '"24h":0' "$(printf '%s' "$CJ" | grep -oE '"24h": ?[0-9]+' | head -1)"
    assert_re "D5-C the txn was COMMIT-CLOSED (the green disposition)" '"in_flight": ?null' "$CJ"
    assert_re "D5-C the current stays at the target" "\"current\": ?\"$TARGET\"" "$CJ"

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
EXPECTED_ASSERTS_G4=19
# G5: the verbatim fold carries 20 asserts + 1 ADDED fixture-guard assert
# (the absent-probe port is never a reserved service port) = 21.
EXPECTED_ASSERTS_G5=21
# D5 (self-service-promote Wave 3): 17 asserts — D5-A (8: the 4-distinct
# seed repoints to the CAPTURE with source=captured + tierB credit), D5-B
# (3: the ambiguity halt consumes NO credit), D5-C (5: green evidence +
# a request take the green path).
EXPECTED_ASSERTS_D5=16
# D6 (self-service-promote Wave 1): 15 asserts — D6-A (5: the rc=3 answer
# is not a timeout, the state word is journaled), D6-B (6: the halt row
# carries the verifier-path reason and never reads "none captured"), D6-C
# (4: the verify-phase narrative reaches the payload log).
EXPECTED_ASSERTS_D6=15

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
run_one_leg d5 "$EXPECTED_ASSERTS_D5" || true
run_one_leg d6 "$EXPECTED_ASSERTS_D6" || true

RESULT_RC=0
if [ "$TIMED_OUT" -gt 0 ]; then
    RESULT_RC=124
elif [ "$LEGS_FAILED" -gt 0 ]; then
    RESULT_RC=1
fi

printf '\n'
printf 'RESULT pack=promote_handback_halt_gate_legs_test legs=5 legs_failed=%s timed_out=%s asserts=%s asserts_failed=%s expected_asserts=%s rc=%s\n' \
    "$LEGS_FAILED" "$TIMED_OUT" "$TOTAL_ASSERTS" "$TOTAL_FAILED_ASSERTS" \
    "$((EXPECTED_ASSERTS_G2 + EXPECTED_ASSERTS_G4 + EXPECTED_ASSERTS_G5 + EXPECTED_ASSERTS_D5 + EXPECTED_ASSERTS_D6))" "$RESULT_RC"
printf 'logs: %s\n' "$LEG_LOGDIR"
exit "$RESULT_RC"
