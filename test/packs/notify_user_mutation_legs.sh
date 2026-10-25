#!/usr/bin/env bash
# Test Pack: notify_user mutation legs — the W10 security-property
#             mutation proof (ari-notify-user commission, 2026-10-25,
#             base 7af1d2632d).
# Timeout: 6 minutes (360s) — target < 2 min runtime
#
# WHAT THIS PACK PROVES
# ---------------------
# Each W10 security-property test is MEANINGFUL: the pack plants a
# specific defect in the production source, asserts the corresponding
# test turns RED, restores the file byte-exactly, and asserts GREEN
# again. A test that cannot detect its own defect is not a security
# property test.
#
#   leg 1  gate concurrency      -> the B2 per-destination lock removed
#                                   (the concurrency test uses a
#                                   STRUCTURAL barrier — every count
#                                   waits for all 12 inserts (bounded
#                                   250ms) — so the no-lock mutant
#                                   over-refuses deterministically)
#   leg 2  cross-instance cap    -> the W1 target hash de-qualified
#                                   (per-session instead of per-destination)
#   leg 3  budget predicate      -> the N3 accepted-class filter dropped
#                                   (refusal rows would consume budget;
#                                   the probe is the ALLOW-direction
#                                   test — six refused rows must leave
#                                   the hour legal)
#   leg 4  body-never-cleartext  -> the D8 body digest extended with the
#                                   cleartext body
#   leg 5  doctrine phrase-pin   -> the falsified absolute ("there is NO
#                                   ping") planted back into the
#                                   self_alarm _full_doc_
#   leg 6  raw-env knob bypass   -> the invalid env knob ACCEPTED
#                                   (a negative interval defeats the
#                                   rate gate) — item 3
#   leg 7  D10 timeout conflation-> the adapter-internal TimeoutError
#                                   re-raised as the tool-bound expiry
#                                   (the retry is lost) — item 2
#   leg 8  ledger never-block    -> the bounded ledger write made
#                                   unbounded (a hung DB holds the
#                                   turn) — item 1
#
# EVERY mutation is applied to a BACKUP-AND-RESTORE copy of the target
# file; the trap restores the original even on an abnormal exit, and
# the pack verifies every mutated file is byte-identical to its
# PRE-RUN content (hash snapshot, not HEAD — the lane may carry
# legitimate uncommitted work) before it reports PASS.
#
# STALE-BYTECODE FENCE: Python validates cached bytecode by mtime
# SECONDS, so a mutant written in the same second as the previous
# compile would silently run the OLD bytecode (observed live during
# the lane — a leg went green on a mutant because of exactly this).
# Every mutation write is followed by an mtime reset that the cache
# cannot match, plus a notify-local __pycache__ purge.
#
# FENCES (binding):
#   * runs in the CALLER's checkout — the pack REFUSES to run inside the
#     main checkout (/home/nea/ensemble-src — foreign WIP) or the live
#     install dir (~/agents-ensemble);
#   * never touches the live daemon, port 9797, or any DB;
#   * the test files use a temp-file SQLite engine — no PG, no live DB.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 600`
#   - Layer 2 (script-internal): `timeout 180s` per pytest leg
#
# No deselection: the legs run against explicit node ids (the pytest
# addopts deselects integration/postgres/slow — these nodes are none
# of those).

set -uo pipefail

# ─── SSL cleanup ──────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: notify_user mutation legs ==="

cd "$PROJECT_DIR"

# ─── fences ───────────────────────────────────────────────────────────────
case "$PROJECT_DIR" in
  /home/nea/ensemble-src|/home/nea/agents-ensemble)
    echo "FENCE: refusing to mutate sources in $PROJECT_DIR (main checkout / live install)"
    echo "RESULT: FAIL"
    exit 1
    ;;
esac

if [ ! -x ".venv/bin/pytest" ]; then
  echo "FENCE: no .venv in the worktree — run 'uv sync' first"
  echo "RESULT: FAIL"
  exit 1
fi

PYTEST=".venv/bin/pytest"
BACKUP_DIR="$(mktemp -d -t notify-mut-XXXXXX)"
RESTORE_LIST=""

cleanup() {
  for entry in $RESTORE_LIST; do
    target="${entry%%::*}"
    backup="${entry##*::}"
    if [ -f "$backup" ]; then
      cp -p "$backup" "$target"
      touch "$target"
    fi
  done
  rm -rf "$BACKUP_DIR"
}
trap cleanup EXIT INT TERM

FAILED=0

sha_of() {
  sha256sum "$1" | cut -d' ' -f1
}

# Snapshot the pre-run state of every TRACKED file the legs may touch
# (content hashes, not HEAD — the lane may legitimately carry
# uncommitted work; the pack must prove it restored ITS OWN mutations,
# not that the tree was clean).
PRE_HASHES="$(mktemp)"
git ls-files -- daemon/tools/notify_user.py daemon/tools/self_alarm.py daemon/repositories/notify_audit/repository.py daemon/config.py | \
  while read -r _f; do echo "$(sha_of "$_f")  $_f"; done > "$PRE_HASHES"

backup_file() {
  local target="$1"
  local backup="$BACKUP_DIR/$(echo "$target" | tr '/' '_').bak"
  cp -p "$target" "$backup"
  RESTORE_LIST="$RESTORE_LIST $target::$backup"
}

# run_leg <leg-name> <file> <expected-red-node> <python-mutation-snippet>
run_leg() {
  local name="$1"; shift
  local target="$1"; shift
  local node="$1"; shift
  local snippet="$1"; shift

  local before
  before="$(sha_of "$target")"
  backup_file "$target"

  echo ""
  echo "--- leg: $name ---"
  python3 - "$target" <<PYEOF
import sys
path = sys.argv[1]
src = open(path).read()
$snippet
open(path, "w").write(src)
PYEOF
  if [ $? -ne 0 ]; then
    echo "leg $name: MUTATION PLANT FAILED"
    FAILED=1
    return
  fi
  # Invalidate the __pycache__ deterministically: Python validates the
  # cached bytecode by mtime SECONDS, so a mutant written in the same
  # second as the previous compile would silently run the OLD bytecode
  # (observed live during the lane — a leg went green on a mutant
  # because of exactly this).
  touch -t 202001010000 "$target"
  find "$PROJECT_DIR/daemon" -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
  if [ "$(sha_of "$target")" = "$before" ]; then
    echo "leg $name: MUTATION DID NOT CHANGE THE FILE — the leg is vacuous"
    FAILED=1
    return
  fi

  if [ "${NOTIFY_MUT_DEBUG:-0}" = "1" ]; then
    timeout 180s $PYTEST "$node" -q -p no:warnings
  else
    timeout 180s $PYTEST "$node" -q -p no:warnings >/dev/null 2>&1
  fi
  local red_rc=$?
  if [ $red_rc -eq 0 ]; then
    echo "leg $name: EXPECTED RED but the test PASSED with the defect planted"
    FAILED=1
  else
    echo "leg $name: RED as expected (pytest rc=$red_rc)"
  fi

  # restore + assert byte-identical + refresh the mtime (the backup
  # copy carries the pre-mutation mtime; a fresh mtime guarantees the
  # next pytest recompiles the restored source).
  local backup
  backup="$(echo "$RESTORE_LIST" | tr ' ' '\n' | grep -F "$target::" | tail -1 | cut -d: -f3-)"
  cp -p "$backup" "$target"
  touch "$target"
  find "$PROJECT_DIR/daemon" -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
  if [ "$(sha_of "$target")" != "$before" ]; then
    echo "leg $name: RESTORE MISMATCH — the file is not byte-identical"
    FAILED=1
    return
  fi

  timeout 180s $PYTEST "$node" -q -p no:warnings >/dev/null 2>&1
  local green_rc=$?
  if [ $green_rc -ne 0 ]; then
    echo "leg $name: EXPECTED GREEN after restore but the test FAILED (rc=$green_rc)"
    FAILED=1
  else
    echo "leg $name: GREEN after restore"
  fi
}

# ── leg 1 — the B2 per-destination lock removed ──────────────────────────
run_leg "gate-concurrency" \
  "daemon/tools/notify_user.py" \
  "tests/unit/test_notify_user_tool.py::TestSecurityProperties::test_w10_1_gate_concurrency_admits_exactly_the_cap" \
  '
old = """async def _destination_lock(target_hash: str) -> asyncio.Lock:
    \"\"\"The B2 primitive — the per-destination-key asyncio.Lock.\"\"\"
    table, guard = _lock_table()
    async with guard:
        lock = table.get(target_hash)
        if lock is None:
            lock = asyncio.Lock()
            table[target_hash] = lock
        return lock"""
new = """class _NoLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def _destination_lock(target_hash: str) -> asyncio.Lock:
    \"\"\"MUTANT — the B2 lock removed.\"\"\"
    return _NoLock()"""
assert src.count(old) == 1, "leg-1 anchor not found"
src = src.replace(old, new)
'

# ── leg 2 — the W1 target hash de-qualified (per-session cap) ────────────
run_leg "cross-instance-shared-cap" \
  "daemon/tools/notify_user.py" \
  "tests/unit/test_notify_user_tool.py::TestSecurityProperties::test_w10_2_cross_instance_shared_cap" \
  '
old = "    target_hash = _target_hash(source_id, external_user_id)"
new = "    target_hash = _target_hash(source_id, external_user_id + instance_id)"
assert src.count(old) == 1, "leg-2 anchor not found"
src = src.replace(old, new)
'

# ── leg 3 — the N3 accepted-class budget filter dropped ─────────────────
run_leg "budget-predicate" \
  "daemon/repositories/notify_audit/repository.py" \
  "tests/unit/test_notify_user_tool.py::TestRateGates::test_refusal_rows_alone_never_trigger_the_cap" \
  '
old = """            .where(
                NotifyAudit.outcome.in_(list(ACCEPTED_CLASS_OUTCOMES))
            )
            .where(NotifyAudit.created_at > since_iso)"""
new = """            .where(NotifyAudit.created_at > since_iso)"""
assert src.count(old) == 1, "leg-3 anchor not found"
src = src.replace(old, new)
'

# ── leg 4 — the D8 body digest extended with the cleartext body ─────────
run_leg "body-never-cleartext" \
  "daemon/tools/notify_user.py" \
  "tests/unit/test_notify_user_tool.py::TestSecurityProperties::test_w10_4_the_body_is_never_stored_as_cleartext" \
  '
old = """def _body_digest(message: str) -> tuple[int, str]:
    \"\"\"The D8 body record — length + sha256, never cleartext.\"\"\"
    return len(message), hashlib.sha256(message.encode(\"utf-8\")).hexdigest()"""
new = """def _body_digest(message: str) -> tuple[int, str]:
    \"\"\"MUTANT — the cleartext body is stored.\"\"\"
    return len(message), hashlib.sha256(message.encode(\"utf-8\")).hexdigest() + message"""
assert src.count(old) == 1, "leg-4 anchor not found"
src = src.replace(old, new)
'

# ── leg 5 — the falsified doctrine absolute planted back ────────────────
run_leg "doctrine-phrase-pin" \
  "daemon/tools/self_alarm.py" \
  "tests/unit/test_self_alarm_registration.py::TestToolDocObligations::test_obligation_2_the_board_is_the_surface_never_an_automatic_ping" \
  '
old = "The board is the user-visible surface — no AUTOMATIC ping"
new = "The board is the user-visible surface — there is NO ping"
assert src.count(old) == 1, "leg-5 anchor not found"
src = src.replace(old, new)
'

# ── leg 6 — the raw-env knob bypass (the model's bound removed) ─────────
# The Pydantic model is the SINGLE authority: an invalid knob must be
# rejected loudly and the validated fallback used. The mutant removes
# the model's OWN bound (ge=0) on the interval → the negative value
# validates → it flows through the tool → the rate gate is defeated.
run_leg "raw-env-knob-bypass" \
  "daemon/config.py" \
  "tests/unit/test_notify_user_tool.py::TestKnobAuthority::test_an_invalid_env_knob_is_rejected_loudly" \
  '
old = """    min_interval_seconds: int = Field(
        default=60,
        ge=0,"""
new = """    min_interval_seconds: int = Field(
        default=60,
        ge=-1000,  # MUTANT — the bound is gone"""
assert src.count(old) == 1, "leg-6 anchor not found"
src = src.replace(old, new)
'

# ── leg 7 — the D10 timeout conflation planted back ─────────────────────
# An adapter-INTERNAL TimeoutError must be a TRANSIENT adapter failure
# (delivery-failed class, retry-eligible). The mutant re-raises it as
# the tool-bound expiry → the retry is lost and the cause is false.
run_leg "d10-timeout-conflation" \
  "daemon/tools/notify_user.py" \
  "tests/unit/test_notify_user_tool.py::TestFailurePaths::test_an_adapter_internal_timeout_is_a_transient_failure" \
  '
old = """                last_cause = \"the adapter raised %s\" % type(exc).__name__
                ok = False"""
new = """                if isinstance(exc, asyncio.TimeoutError):
                    raise asyncio.TimeoutError  # MUTANT — the conflation
                last_cause = \"the adapter raised %s\" % type(exc).__name__
                ok = False"""
assert src.count(old) == 1, "leg-7 anchor not found"
src = src.replace(old, new)
'

# ── leg 8 — the never-block bound on the ledger writes removed ──────────
# The pre-flight refusal ledgering is BOUNDED (W4): a hung database must
# not hold the turn. The mutant makes the write unbounded → the turn is
# held for the whole hang.
run_leg "ledger-write-never-block" \
  "daemon/tools/notify_user.py" \
  "tests/unit/test_notify_user_tool.py::TestFailurePaths::test_a_hung_ledger_write_never_holds_the_turn" \
  '
old = """    await asyncio.wait_for(
        asyncio.to_thread(func, *args, **kwargs),
        timeout=max(0.25, float(timeout_seconds)),
    )"""
new = """    await asyncio.to_thread(func, *args, **kwargs)  # MUTANT — unbounded"""
assert src.count(old) == 1, "leg-8 anchor not found"
src = src.replace(old, new)
'

echo ""
echo "=== notify_user mutation legs: verifying the tree is byte-clean ==="
DRIFT=0
while IFS= read -r line; do
  _expected="${line%%  *}"
  _file="${line##*  }"
  if [ "$(sha_of "$_file")" != "$_expected" ]; then
    echo "RESTORE DRIFT: $_file differs from its pre-run content"
    DRIFT=1
  fi
done < "$PRE_HASHES"
rm -f "$PRE_HASHES"
if [ "$DRIFT" -eq 0 ]; then
  echo "RESTORE CHECK: clean (every mutated file byte-identical to its pre-run content)"
else
  FAILED=1
fi

if [ $FAILED -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
fi
echo "RESULT: FAIL"
exit 1
