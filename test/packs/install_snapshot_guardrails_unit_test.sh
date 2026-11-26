#!/usr/bin/env bash
# ============================================================================
# install_snapshot_guardrails_unit_test.sh
#
# GUARDRAIL PACK for scripts/protection/ensemble-install-snapshot.sh
# (Layer 3 install-tree hardlink snapshot backstop).
#
# Contract proven, ON FAKES ONLY (never the live install tree, never the live
# snapshot root, never the production alert dir):
#   L1  an under-floor skip does NOT take the unit down   (exit 0, not 1)
#   L2  an under-floor skip deletes NOTHING and writes no snapshot
#   L3  an under-floor skip EMITS THE ALERT on every channel:
#         console broadcast (wall) · journal emerg (systemd-cat) ·
#         journal daemon.crit (logger) · durable marker JSON + counter ·
#         stdout "ALERT" line
#   L4  the happy path still snapshots, still prunes to exactly RETAIN, and
#       CLEARS a standing skip alert (the alert is not sticky)
#   L5  value pins: RETAIN default 48, MIN_FREE_MB default 1024, and the
#       skip path exits 0 (the old fatal `exit 1` shape is gone)
#   L6  the non-root drill escape CANNOT be used against the production
#       snapshot root (the production root stays root-only)
#
# House rules honoured: self-capped budget, RESULT: PASS line, rc 0/1,
# no live-install paths, no PG, no daemon import.
# ============================================================================
set -uo pipefail

BUDGET_S="${INSTALL_SNAPSHOT_GUARDRAILS_BUDGET_S:-240}"
START_S=$(date +%s)
PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PACK_DIR/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/protection/ensemble-install-snapshot.sh"

PASS=0
FAIL=0
declare -a FAILED_LEGS=()

ok()   { PASS=$((PASS+1)); echo "  PASS: $1"; }
bad()  { FAIL=$((FAIL+1)); FAILED_LEGS+=("$1"); echo "  FAIL: $1"; }
leg()  { echo "== $1"; }
within_budget() {
  local now; now=$(date +%s)
  if [ $((now-START_S)) -ge "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (budget ${BUDGET_S}s exceeded)"; exit 1
  fi
}

echo "== pack: install_snapshot_guardrails_unit_test"
echo "   repo_root=$REPO_ROOT"
echo "   script=$SCRIPT"

if [ ! -f "$SCRIPT" ]; then
  echo "RESULT: FAIL (script under test not found: $SCRIPT)"; exit 1
fi
if ! bash -n "$SCRIPT"; then
  echo "RESULT: FAIL (bash -n rejected the script under test)"; exit 1
fi

# --- hermetic fixture --------------------------------------------------------
TMP="$(mktemp -d "${TMPDIR:-/tmp}/snap-guardrails.XXXXXX")"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
FAKE_SRC="$TMP/src"; FAKE_ROOT="$TMP/snaproot"; FAKE_ALERTS="$TMP/alerts"
STUB_BIN="$TMP/bin"; STUB_LOG="$TMP/stub.log"
mkdir -p "$FAKE_SRC" "$FAKE_ROOT" "$FAKE_ALERTS" "$STUB_BIN"
printf 'alpha\n' > "$FAKE_SRC/file-a.txt"
printf 'beta\n'  > "$FAKE_SRC/file-b.txt"
mkdir -p "$FAKE_SRC/sub"; printf 'gamma\n' > "$FAKE_SRC/sub/file-c.txt"

# Stub the three notification channels so the pack can assert on them.
for tool in logger systemd-cat wall; do
  cat > "$STUB_BIN/$tool" <<STUB
#!/usr/bin/env bash
printf '%s' "$tool" >> "$STUB_LOG"
for a in "\$@"; do printf ' %s' "\$a" >> "$STUB_LOG"; done
printf '\n' >> "$STUB_LOG"
exit 0
STUB
  chmod +x "$STUB_BIN/$tool"
done

run_snap() {  # run_snap <extra env assignments...>  -> sets RC/OUT
  OUT="$(env PATH="$STUB_BIN:$PATH" \
      ENSEMBLE_SNAPSHOT_ROOT="$FAKE_ROOT" \
      ENSEMBLE_SNAPSHOT_SRC="$FAKE_SRC" \
      ENSEMBLE_SNAPSHOT_ALERT_DIR="$FAKE_ALERTS" \
      ENSEMBLE_SNAPSHOT_ALLOW_NONROOT=1 \
      "$@" \
      timeout 60 bash "$SCRIPT" 2>&1)"
  RC=$?
}

# ── L1 / L2 / L3 — under-floor skip ─────────────────────────────────────────
leg "L1/L2/L3 under-floor skip: green unit, nothing deleted, alert on every channel"
# Seed three pre-existing snapshots that must SURVIVE the skip.
for s in 20260101T000000Z 20260101T003000Z 20260101T010000Z; do
  mkdir -p "$FAKE_ROOT/snap-$s"; printf 'keep\n' > "$FAKE_ROOT/snap-$s/keepme.txt"
done
BEFORE_SEEDED=3
: > "$STUB_LOG"

run_snap MIN_FREE_MB=999999999
within_budget

if [ "$RC" -eq 0 ]; then ok "L1 under-floor skip exits 0 (unit stays green, rc=$RC)"
else bad "L1 under-floor skip must exit 0, got rc=$RC :: $OUT"; fi

SURVIVED=0
for s in 20260101T000000Z 20260101T003000Z 20260101T010000Z; do
  [ -f "$FAKE_ROOT/snap-$s/keepme.txt" ] && SURVIVED=$((SURVIVED+1))
done
if [ "$SURVIVED" -eq "$BEFORE_SEEDED" ]; then ok "L2 all $BEFORE_SEEDED pre-existing snapshots survived the skip"
else bad "L2 skip deleted snapshots (survived $SURVIVED/$BEFORE_SEEDED)"; fi

NEW_SNAPS="$(find "$FAKE_ROOT" -maxdepth 1 -name 'snap-*' | wc -l | tr -d ' ')"
if [ "$NEW_SNAPS" -eq "$BEFORE_SEEDED" ]; then ok "L2 skip wrote no new snapshot"
else bad "L2 snapshot count changed by $((NEW_SNAPS-BEFORE_SEEDED)) (expected 0)"; fi
if [ -z "$(find "$FAKE_ROOT" -maxdepth 1 -name '.incoming-*' 2>/dev/null)" ]; then ok "L2 skip left no staging dir"
else bad "L2 skip left a .incoming-* staging dir"; fi

# alert channels
if grep -q '^logger .*daemon\.crit' "$STUB_LOG"; then ok "L3 alert on journal daemon.crit (logger)"
else bad "L3 no daemon.crit logger call :: $(cat "$STUB_LOG")"; fi
if grep -q '^systemd-cat .*emerg' "$STUB_LOG"; then ok "L3 alert on journal emerg (systemd-cat)"
else bad "L3 no emerg systemd-cat call :: $(cat "$STUB_LOG")"; fi
if grep -qE '^wall( |$)' "$STUB_LOG"; then ok "L3 alert on console broadcast (wall)"
else bad "L3 no console broadcast :: $(cat "$STUB_LOG")"; fi
if printf '%s' "$OUT" | grep -q 'ALERT'; then ok "L3 alert on stdout (ALERT line)"
else bad "L3 stdout carried no ALERT line :: $OUT"; fi
MARK="$FAKE_ALERTS/install-snapshot-skip.json"
if [ -f "$MARK" ]; then ok "L3 durable marker written"
else bad "L3 no durable marker at $MARK"; fi
if [ -f "$MARK" ]; then
  for field in '"event":"snapshot_skipped"' '"severity":"crit"' '"unit_state":"green"' '"floor_mb":999999999'; do
    if grep -q -- "$field" "$MARK"; then ok "L3 marker carries $field"
    else bad "L3 marker missing $field :: $(cat "$MARK")"; fi
  done
fi
if [ "$(cat "$FAKE_ALERTS/install-snapshot-skip.count" 2>/dev/null)" = "1" ]; then ok "L3 skip counter = 1"
else bad "L3 skip counter wrong :: $(cat "$FAKE_ALERTS/install-snapshot-skip.count" 2>/dev/null)"; fi
# second skip increments the counter (a patrol can see repetition)
run_snap MIN_FREE_MB=999999999
if [ "$(cat "$FAKE_ALERTS/install-snapshot-skip.count" 2>/dev/null)" = "2" ]; then ok "L3 repeated skip increments the counter (2)"
else bad "L3 repeated skip counter wrong :: $(cat "$FAKE_ALERTS/install-snapshot-skip.count" 2>/dev/null)"; fi

# ── L4 — happy path: snapshot, retention prune, alert cleared ───────────────
leg "L4 happy path: snapshot written, retention keeps exactly RETAIN, alert cleared"
run_snap MIN_FREE_MB=1 RETAIN=2
within_budget
if [ "$RC" -eq 0 ]; then ok "L4 happy path exits 0"
else bad "L4 happy path rc=$RC :: $OUT"; fi
if [ -n "$(find "$FAKE_ROOT" -maxdepth 1 -name 'snap-*' | head -1)" ]; then ok "L4 a snapshot was published"
else bad "L4 no snapshot published"; fi
if [ ! -f "$FAKE_ALERTS/install-snapshot-skip.json" ]; then ok "L4 standing skip alert CLEARED after a successful snapshot"
else bad "L4 skip alert still standing after success"; fi
# run twice more with RETAIN=2 -> exactly 2 snapshot dirs must remain
run_snap MIN_FREE_MB=1 RETAIN=2
run_snap MIN_FREE_MB=1 RETAIN=2
within_budget
KEPT="$(find "$FAKE_ROOT" -maxdepth 1 -name 'snap-*' | wc -l | tr -d ' ')"
if [ "$KEPT" -eq "2" ]; then ok "L4 retention pruned to exactly RETAIN=2 (kept $KEPT)"
else bad "L4 retention kept $KEPT dirs, expected 2"; fi

# ── L5 — value pins ─────────────────────────────────────────────────────────
leg "L5 value pins"
if grep -q 'RETAIN="\${RETAIN:-48}"' "$SCRIPT"; then ok "L5 RETAIN default is 48"
else bad "L5 RETAIN default is not 48"; fi
if grep -q 'MIN_FREE_MB="\${MIN_FREE_MB:-1024}"' "$SCRIPT"; then ok "L5 MIN_FREE_MB default is 1024"
else bad "L5 MIN_FREE_MB default is not 1024"; fi
if awk '/^if \[ "\$free_mb" -lt "\$MIN_FREE_MB" \]; then/{f=1} f&&/exit 0/{found=1} f&&/^fi$/{f=0} END{exit !found}' "$SCRIPT"; then
  ok "L5 floor-skip block exits 0 (fatal exit 1 shape is gone)"
else
  bad "L5 floor-skip block does not exit 0"
fi
if grep -q 'exit 1' <<<"$(awk '/^if \[ "\$free_mb" -lt "\$MIN_FREE_MB" \]; then/{f=1} f{print} f&&/^fi$/{exit}' "$SCRIPT")"; then
  bad "L5 floor-skip block still contains a fatal exit 1"
else
  ok "L5 floor-skip block has no fatal exit 1"
fi

# ── L6 — the non-root drill escape cannot reach the production root ─────────
leg "L6 non-root drill escape is refused for the production snapshot root"
PROD_OUT="$(env PATH="$STUB_BIN:$PATH" \
  ENSEMBLE_SNAPSHOT_ROOT=/home/nea/.install-snapshots \
  ENSEMBLE_SNAPSHOT_SRC="$FAKE_SRC" \
  ENSEMBLE_SNAPSHOT_ALERT_DIR="$FAKE_ALERTS" \
  ENSEMBLE_SNAPSHOT_ALLOW_NONROOT=1 \
  timeout 30 bash "$SCRIPT" 2>&1)"
PROD_RC=$?
if [ "$PROD_RC" -ne 0 ] && printf '%s' "$PROD_OUT" | grep -q 'must run as root'; then
  ok "L6 production snapshot root refuses a non-root drilled run (rc=$PROD_RC)"
else
  bad "L6 production root did not refuse the non-root run (rc=$PROD_RC) :: $PROD_OUT"
fi

echo
echo "RESULT: $([ "$FAIL" -eq 0 ] && echo PASS || echo FAIL) (passed=$PASS failed=$FAIL)"
if [ "$FAIL" -ne 0 ]; then
  printf '  failed legs:\n'
  for f in "${FAILED_LEGS[@]}"; do printf '    - %s\n' "$f"; done
  exit 1
fi
exit 0
