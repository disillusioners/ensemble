#!/usr/bin/env bash
# Test Pack: skill_tools_display_hygiene_unit_test — Phase-1 workstream E3
#
# Board item `wi-d340f16c` (plan §9 E3, §18 rows 19-20). The item
# contains EXACTLY THREE bullets, all DISPLAY-ONLY (*"skill_view
# consumption is NOT affected"*):
#
#   1. `skill_fix`    echoed a bare 8-hex token (`skill_id[:8]`)
#   2. `skill_feedback` echoed the same bare 8-hex token
#   3. `_render_pf_view` printed the internal `pf-<sha16>` pseudo-id on
#      the agent-facing `- id:` line
#
# The 8-hex form is NOT `skill_view`-consumable, so an agent that copied
# it back into `skill_view` failed (the wi-76d89e11 verbatim-id
# contract). The pseudo-id is an INTERNAL key — it must not be the
# agent-facing id, but it must stay available ADDITIVELY (`pseudo_id`).
#
# What this pack exercises:
#   0. PHASE 0 — the IMPORT GUARD: prove `import daemon` resolves to
#      THIS checkout. A run that silently imported the shared checkout
#      or the live install would report a green result computed
#      against FOREIGN code. The pack fails LOUDLY.
#   1. tests/unit/test_skill_tools_display_hygiene.py (NEW) — the pins:
#      the fix echo carries the full id; the feedback echo carries the
#      full id; the pf view's `- id:` line carries the ADDRESS while the
#      pseudo-id stays reachable under `- pseudo_id:`; and the emitted
#      id ROUND-TRIPS back through `skill_view` (the verbatim contract,
#      which is what a truncated id breaks). A vacuity control proves
#      the "bare token absent" shape is detectable at all.
#   2. The MUTATION MATRIX (M-E3a..c) — pack-level plants: plant the
#      defect → the discriminating leg must go RED → restore → the same
#      leg must go GREEN. RESTORE SEMANTICS: every mutable file's
#      PRISTINE content+sha256 is captured BEFORE phase 2 (with a
#      fail-loud dirty-tree pre-check — the pack NEVER baselines
#      contamination), each leg restores to PRISTINE (not to
#      leg-start), an idempotent EXIT/TERM/INT trap restores pristine
#      on ANY exit path, and after the final leg every mutable file's
#      sha is asserted EQUAL to the pristine capture. A pin that
#      cannot fail is worse than no pin, so a NON-DISCRIMINATING leg
#      fails the pack.
#
# Fences (house):
#   - Per-variable env scrub (the `env -u POSTGRES_*` glob is a SILENT
#     NO-OP — bash does not glob env names).
#   - FAKE_HOME (mktemp) — no live-shaped path resolution; removed on exit.
#   - Interpreter: the checkout's own .venv when present, else the
#     certified main-checkout interpreter; cwd is the checkout root, so
#     `import daemon` resolves HERE.
#   - INTERNAL TIMER: the pack asserts its own wall-clock budget after
#     every phase and every mutation leg; overrun prints
#     RESULT: TIMEOUT and exits 124.
#   - CONCURRENCY FENCE: flock keyed on the checkout's realpath — a
#     second concurrent run against the SAME checkout fails loud.
#
# Usage:
#   bash test/packs/skill_tools_display_hygiene_unit_test.sh
#   SKILL_TOOLS_DISPLAY_HYGIENE_MUTATIONS=0 bash test/packs/skill_tools_display_hygiene_unit_test.sh
#     — skip the mutation matrix (the pins only).
#   SKILL_TOOLS_DISPLAY_HYGIENE_BUDGET_S=300 bash test/packs/skill_tools_display_hygiene_unit_test.sh
#     — override the internal wall-clock budget (seconds).
#
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. The RESULT label always
# prints — a red leg never kills the pack silently. On SIGTERM/SIGINT
# the trap restores pristine before exiting non-zero.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Test Pack: skill_tools_display_hygiene_unit_test ==="

# --- internal wall-clock budget ------------------------------------------
PACK_START="$(date +%s)"
BUDGET_S="${SKILL_TOOLS_DISPLAY_HYGIENE_BUDGET_S:-300}"
elapsed_s() { echo $(( $(date +%s) - PACK_START )); }
check_budget() {
  local elapsed label="${1:-}"
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (internal timer: wall ${elapsed}s > SKILL_TOOLS_DISPLAY_HYGIENE_BUDGET_S=${BUDGET_S}s ${label})"
    exit 124
  fi
}

# --- interpreter ----------------------------------------------------------
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PY="$PROJECT_DIR/.venv/bin/python"
elif [ -x "${ENSEMBLE_MAIN_VENV:-/home/nea/ensemble-src/.venv/bin/python}" ]; then
  PY="${ENSEMBLE_MAIN_VENV:-/home/nea/ensemble-src/.venv/bin/python}"
else
  PY="$(command -v python3 || command -v python)"
fi
echo "[pack] interpreter: $PY"

# --- fences ---------------------------------------------------------------
FAKE_HOME="$(mktemp -d /tmp/skill-tools-display-hygiene-pack-XXXXXX)"
PRISTINE_DIR="$FAKE_HOME/pristine"
mkdir -p "$PRISTINE_DIR"

# The COMPLETE plant surface of the mutation matrix.
MUTABLE_FILES=(
  "daemon/tools/skill_tools.py"
)

_pristine_key() { printf '%s' "$1" | sha256sum | cut -d' ' -f1 | cut -c1-16; }

restore_pristine() {
  local meta key path
  for meta in "$PRISTINE_DIR"/*.meta; do
    [ -e "$meta" ] || continue
    key="$(basename "$meta" .meta)"
    path="$(cat "$meta")"
    cp "$PRISTINE_DIR/$key.content" "$path"
  done
}

PACK_CLEANED=0
PACK_LOCK_HELD=0
cleanup() {
  [ "$PACK_CLEANED" = 1 ] && return 0
  PACK_CLEANED=1
  local meta key path cur want drift=0
  if [ -d "$PRISTINE_DIR" ]; then
    for meta in "$PRISTINE_DIR"/*.meta; do
      [ -e "$meta" ] || continue
      key="$(basename "$meta" .meta)"
      path="$(cat "$meta")"
      cur="$(sha256sum "$path" 2>/dev/null | cut -d' ' -f1)"
      want="$(cat "$PRISTINE_DIR/$key.sha")"
      [ "$cur" = "$want" ] || drift=1
    done
    restore_pristine
  fi
  rm -rf "$FAKE_HOME"
  if [ "$PACK_LOCK_HELD" = 1 ]; then
    flock -u 8 2>/dev/null || true
    PACK_LOCK_HELD=0
  fi
  if [ "$drift" = 1 ]; then
    echo "RESULT: FAIL (left-behind plant detected on exit — pristine restored)"
    exit 1
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 130' INT

# Concurrency fence: one pack run per checkout.
LOCK_KEY="$(_pristine_key "$PROJECT_DIR")"
LOCK_FILE="/tmp/skill-tools-display-hygiene-pack-${LOCK_KEY}.lock"
exec 8>"$LOCK_FILE"
if ! flock -n 8; then
  echo "RESULT: FAIL (another pack run holds this checkout's lock: $LOCK_FILE)"
  exit 1
fi
PACK_LOCK_HELD=1

# Per-variable scrub — NEVER the ``env -u POSTGRES_*`` glob.
SCRUB=(
  env
  -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_HOST -u POSTGRES_PORT
  -u DATABASE_URL -u PORT -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG
  -u ENSEMBLE_DATA_DIR
  -u PGPASSWORD -u PGHOST -u PGPORT -u PGUSER -u PGDATABASE
)

run_pytest() {
  "${SCRUB[@]}" \
    HOME="$FAKE_HOME" \
    XDG_CACHE_HOME="$FAKE_HOME/cache" \
    PYTHONPATH="$PROJECT_DIR" \
    "$PY" -m pytest "$@" -q --tb=line --override-ini="addopts=" -p no:randomly
}

PIN_FILE="tests/unit/test_skill_tools_display_hygiene.py"

# --- phase 0: the IMPORT GUARD ------------------------------------------
echo "--- phase 0: the import guard ---"
IMPORT_GUARD_LOG="$FAKE_HOME/import-guard.log"
"${SCRUB[@]}" \
  HOME="$FAKE_HOME" \
  XDG_CACHE_HOME="$FAKE_HOME/cache" \
  PYTHONPATH="$PROJECT_DIR" \
  "$PY" - "$PROJECT_DIR" >"$IMPORT_GUARD_LOG" 2>&1 <<'GUARDEOF'
import os
import sys

project_dir = os.path.realpath(sys.argv[1])
try:
    import daemon
except Exception as exc:  # pragma: no cover — the guard must speak up
    sys.stderr.write(f"IMPORT GUARD: FAIL — 'import daemon' raised: {exc!r}\n")
    sys.exit(1)

pkg_dir = os.path.realpath(os.path.dirname(daemon.__file__))
expected = os.path.join(project_dir, "daemon")
sys.stderr.write(f"daemon.__file__={daemon.__file__}\n")
sys.stderr.write(f"expected package dir={expected}\n")
if pkg_dir != expected:
    sys.stderr.write(
        "IMPORT GUARD: FAIL — 'import daemon' did NOT resolve under "
        f"{project_dir}\n"
        "  (the run would have tested the FOREIGN tree)\n"
    )
    sys.exit(1)
sys.stderr.write("IMPORT GUARD: PASS — import daemon resolved under "
                 f"{project_dir}\n")
GUARDEOF
IMPORT_GUARD_RC=$?
cat "$IMPORT_GUARD_LOG"
if [ "$IMPORT_GUARD_RC" -ne 0 ]; then
  echo "RESULT: FAIL (phase 0: import guard)"
  exit 1
fi
echo "[timer] phase 0 done: $(elapsed_s)s elapsed / budget ${BUDGET_S}s"
check_budget "after phase 0"

# --- phase 1: the pins ---------------------------------------------------
echo "--- phase 1: the display-hygiene pins ---"
PHASE1_LOG="$FAKE_HOME/phase1.log"
run_pytest "$PIN_FILE" >"$PHASE1_LOG" 2>&1
PHASE1_RC=$?
tail -3 "$PHASE1_LOG"
echo "[timer] phase 1 done: $(elapsed_s)s elapsed / budget ${BUDGET_S}s"
check_budget "after phase 1"

# --- phase 2: the mutation matrix ---------------------------------------
MUTATION_FAILURES=0
run_mutations() {
  echo "--- phase 2: the mutation matrix (plant → RED → pristine-restore → GREEN) ---"
  TSV="$FAKE_HOME/mutation-matrix.tsv"
  : >"$TSV"

  echo "--- phase 2 pre-check: pristine capture ---"
  local f key dirty
  for f in "${MUTABLE_FILES[@]}"; do
    if [ ! -f "$f" ]; then
      echo "RESULT: FAIL (phase 2 pre-check: mutable file missing: $f)"
      exit 1
    fi
    dirty="$(git status --porcelain -- "$f" 2>/dev/null)"
    if [ -n "$dirty" ]; then
      echo "RESULT: FAIL (phase 2 pre-check: mutable file DIRTY at pack start — refusing to baseline contamination: $f)"
      exit 1
    fi
    key="$(_pristine_key "$f")"
    cp "$f" "$PRISTINE_DIR/$key.content"
    printf '%s' "$f" >"$PRISTINE_DIR/$key.meta"
    sha256sum "$f" | cut -d' ' -f1 >"$PRISTINE_DIR/$key.sha"
    echo "[pack] pristine: $f sha=$(cat "$PRISTINE_DIR/$key.sha")"
  done

  plant() {
    "$PY" - "$1" "$2" "$3" <<'PYEOF'
import pathlib
import sys

path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
s = p.read_text()
n = s.count(old)
if n != 1:
    sys.stderr.write(f"PLANT-ERROR: {path}: expected 1 occurrence, found {n}\n")
    sys.exit(3)
p.write_text(s.replace(old, new))
PYEOF
  }

  leg() {
    local id="$1" path="$2" old="$3" new="$4" node="$5"
    local key want sha_before sha_after red green
    key="$(_pristine_key "$path")"
    want="$(cat "$PRISTINE_DIR/$key.sha")"
    sha_before="$(sha256sum "$path" | cut -d' ' -f1)"
    if [ "$sha_before" != "$want" ]; then
      printf '%s\tLEG-START-CONTAMINATED\t-\t%s\n' "$id" "$sha_before" >>"$TSV"
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
      cp "$PRISTINE_DIR/$key.content" "$path"
      return
    fi

    if ! plant "$path" "$old" "$new"; then
      printf '%s\tPLANT-FAILED\t-\t%s\n' "$id" "$sha_before" >>"$TSV"
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
      cp "$PRISTINE_DIR/$key.content" "$path"
      return
    fi

    if run_pytest "$node" >"$FAKE_HOME/$id.red.log" 2>&1; then
      red="NO"
    else
      red="YES"
    fi

    cp "$PRISTINE_DIR/$key.content" "$path"
    sha_after="$(sha256sum "$path" | cut -d' ' -f1)"
    if [ "$sha_after" != "$want" ]; then
      printf '%s\tRESTORE-MISMATCH\t-\t%s\n' "$id" "$want" >>"$TSV"
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
      return
    fi

    if run_pytest "$node" >"$FAKE_HOME/$id.green.log" 2>&1; then
      green="YES"
    else
      green="NO"
    fi

    printf '%s\t%s\t%s\t%s\n' "$id" "$red" "$green" "$want" >>"$TSV"
    if [ "$red" != "YES" ] || [ "$green" != "YES" ]; then
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    fi
    check_budget "after leg $id"
  }

  # M-E3a — the `skill_fix` echo goes back to the bare 8-hex token.
  leg "M-E3a" "daemon/tools/skill_tools.py" \
'        echo_id = skill_id if isinstance(skill_id, str) else str(skill_id)
        lines: list[str] = [
' \
'        echo_id = skill_id[:8] if isinstance(skill_id, str) else skill_id
        lines: list[str] = [
' \
"$PIN_FILE::TestE31SkillFixEchoesTheFullId::test_the_echo_carries_the_full_uuid4"

  # M-E3b — the `skill_feedback` echo goes back to the bare 8-hex token.
  leg "M-E3b" "daemon/tools/skill_tools.py" \
'        echo_id = skill_id if isinstance(skill_id, str) and skill_id else str(skill_id)
' \
'        echo_id = skill_id[:8] if isinstance(skill_id, str) and skill_id else str(skill_id)
' \
"$PIN_FILE::TestE32SkillFeedbackEchoesTheFullId::test_the_recorded_line_carries_the_full_uuid4"

  # M-E3c — the pf view's `- id:` line prints the INTERNAL pseudo-id again.
  leg "M-E3c" "daemon/tools/skill_tools.py" \
"        lines.append(f\"- id: {getattr(file_skill, 'address', '')}\")" \
"        lines.append(f\"- id: {getattr(file_skill, 'id', '')}\")" \
"$PIN_FILE::TestE33PfViewEchoesTheAddress::test_the_id_line_carries_the_address"

  # M-E3d (tidier H2) — the structural ``except SkillFixBurstCapRefused``
  # is removed, so the blanket catch swallows the DESIGNED refusal again
  # and renders the generic note — including the UNTRUE "request is still
  # recorded above for the next skill-keeper run". The false-durability
  # pin MUST go RED.
  #
  # CODE-ONLY plant text (no apostrophes — the round-5 lesson).
  leg "M-E3d" "daemon/tools/skill_tools.py" \
'                except SkillFixBurstCapRefused as e:
                    # H2 (tidier) — the DESIGNED refusal, caught BEFORE
                    # the blanket catch. The blanket catch swallowed it
                    # and rendered the generic note, including the
                    # UNTRUE "request is still recorded above for the next
                    # skill-keeper run": the cap REFUSED the dispatch, so
                    # NO job exists and NOTHING will pick it up. An
                    # agent told "your request is recorded" believes a
                    # fix is pending when nothing was recorded.
                    #
                    # The REST route already handles this exception
                    # structurally (HTTP 429, M2-pinned) and reads the
                    # stable token off the refusal class (``e.reason``)
                    # rather than hard-coding a literal — the agent-facing
                    # envelope mirrors that vocabulary, so the two can
                    # never drift (M11: one token per concept).
                    logger.warning(
                        "skill_fix burst cap refused for skill %s: %s",
                        skill_id,
                        e,
                    )
                    burst_cap_refusal = e
' \
'' \
"$PIN_FILE::TestH2BurstCapRefusalIsTruthful::test_the_false_durability_sentence_is_absent"
  echo "leg	red	green	sha256(planted-file, PRISTINE)"
  cat "$TSV"
  echo "mutation failures: $MUTATION_FAILURES"

  local cur want
  for f in "${MUTABLE_FILES[@]}"; do
    key="$(_pristine_key "$f")"
    cur="$(sha256sum "$f" | cut -d' ' -f1)"
    want="$(cat "$PRISTINE_DIR/$key.sha")"
    if [ "$cur" != "$want" ]; then
      echo "PRISTINE-DRIFT at final assert: $f ($cur != $want) — restoring"
      cp "$PRISTINE_DIR/$key.content" "$f"
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    fi
  done
}

MUTATION_RC=0
if [ "${SKILL_TOOLS_DISPLAY_HYGIENE_MUTATIONS:-1}" = "0" ]; then
  echo "--- phase 2: SKIPPED (SKILL_TOOLS_DISPLAY_HYGIENE_MUTATIONS=0) ---"
else
  run_mutations
  MUTATION_RC=$MUTATION_FAILURES
fi

# --- verdict --------------------------------------------------------------
echo "[timer] total wall: $(elapsed_s)s / budget ${BUDGET_S}s"
check_budget "at verdict"
if [ "$PHASE1_RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$PHASE1_RC" -ne 0 ]; then
  echo "RESULT: FAIL (phase 1: the display-hygiene pin is RED — rc=$PHASE1_RC)"
  exit 1
elif [ "$MUTATION_RC" -ne 0 ]; then
  echo "RESULT: FAIL (mutation matrix: $MUTATION_RC leg(s) non-discriminating)"
  exit 1
else
  echo "RESULT: PASS"
  exit 0
fi
