#!/usr/bin/env bash
# Test Pack: skill_keeper_confinement_behavioral_unit_test — Phase-1 workstream B1
#
# The BEHAVIORAL confinement proof for the skill-keeper (Phase-1 plan
# §6 B1). The shipped evidence for the keeper's confinement was an
# allow-list INSPECTION; the commission asks for a behavioral REFUSAL
# proof: the `b7da3ed0` shape (run 2026-10-21 17:23:02Z — 24
# `edit_file` calls across 10 files, a git stash dance, ruff, pytest,
# `git add` + `git commit` → commit 660e73453) must be REFUSED, not
# merely discouraged by prose.
#
# What this pack exercises:
#   0. PHASE 0 — the IMPORT GUARD: prove `import daemon` resolves to
#      THIS checkout. A run that silently imported the shared checkout
#      or the live install would report a green result computed
#      against FOREIGN code. The pack fails LOUDLY.
#   1. tests/unit/test_skill_keeper_confinement.py (NEW) — the pin:
#      B1a the RESOLVER leg (the production factory + the production
#      resolver against the production registry; the assertion is by
#      CAPABILITY derived from each tool's IMPLEMENTATION, never by
#      tool NAME) and B1b the INVOCATION leg (the `b7da3ed0` sequence
#      actually ATTEMPTED through LangGraph's ToolNode — the dispatch
#      node daemon/graph.py binds — with a directory digest before/after
#      and a git-cleanliness assertion in a scratch tree).
#   2. The MUTATION MATRIX (M-B1a..e) — pack-level plants: plant the
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
# MUTATION SEMANTICS (architecture amendment #7 — do not get this
# wrong): adding `edit_file` to tools.allow WHILE KEEPING it in
# tools.deny leaves the deny-wins structural pin GREEN (M-B1b is a
# GUARD leg, not a RED leg). That pin goes RED only when the ordering
# breaks — M-B1c (allow-after-deny) → RED. The red→green EXIT leg is
# M-B1a: `edit_file` becomes reachable (allow + deny removed) → the
# invocation leg WRITES a file → RED; restore → refusal → GREEN.
#
# M-B1e is an ORACLE mutation: the capability classifier lives in the
# pin file (the production registry carries no capability metadata to
# consume), so the leg replaces the classifier's implementation-based
# check with a NAME-based one. The pin's self-test — a synthetic tool
# with a NOVEL name and a write-shaped body — must still classify it
# as write-capable, so the name-based classifier REDs it.
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
#   - CONCURRENCY FENCE: flock keyed on the checkout's realpath.
#
# Usage:
#   bash test/packs/skill_keeper_confinement_behavioral_unit_test.sh
#   SKILL_KEEPER_CONFINEMENT_MUTATIONS=0 bash test/packs/skill_keeper_confinement_behavioral_unit_test.sh
#     — skip the mutation matrix (the pin only).
#   SKILL_KEEPER_CONFINEMENT_BUDGET_S=300 bash test/packs/skill_keeper_confinement_behavioral_unit_test.sh
#     — override the internal wall-clock budget (seconds).
#
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. The RESULT label always
# prints — a red leg never kills the pack silently. On SIGTERM/SIGINT
# the trap restores pristine before exiting non-zero.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Test Pack: skill_keeper_confinement_behavioral_unit_test ==="

# --- internal wall-clock budget ------------------------------------------
PACK_START="$(date +%s)"
BUDGET_S="${SKILL_KEEPER_CONFINEMENT_BUDGET_S:-300}"
elapsed_s() { echo $(( $(date +%s) - PACK_START )); }
check_budget() {
  local elapsed label="${1:-}"
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (internal timer: wall ${elapsed}s > SKILL_KEEPER_CONFINEMENT_BUDGET_S=${BUDGET_S}s ${label})"
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
FAKE_HOME="$(mktemp -d /tmp/skill-keeper-confinement-pack-XXXXXX)"
PRISTINE_DIR="$FAKE_HOME/pristine"
mkdir -p "$PRISTINE_DIR"

# The COMPLETE plant surface of the mutation matrix.
MUTABLE_FILES=(
  "agents/skill-keeper/meta.json"
  "daemon/tools/instance.py"
  "tests/unit/test_skill_keeper_confinement.py"
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
LOCK_FILE="/tmp/skill-keeper-confinement-pack-${LOCK_KEY}.lock"
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

PIN_FILE="tests/unit/test_skill_keeper_confinement.py"

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

# --- phase 1: the pin ----------------------------------------------------
echo "--- phase 1: the behavioral confinement pin ---"
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

  # A GUARD leg: the planted state must leave the node GREEN. M-B1b adds
  # `edit_file` to tools.allow while KEEPING it in tools.deny — the
  # deny-wins structural pin asserts deny-wins and must stay GREEN.
  # Its sensitivity is proven by M-B1c (the same node REDs when the
  # ordering breaks).
  guard_leg() {
    local id="$1" path="$2" old="$3" new="$4" node="$5"
    local key want sha_before sha_after green
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

    if run_pytest "$node" >"$FAKE_HOME/$id.green.log" 2>&1; then
      green="YES"
    else
      green="NO"
    fi

    cp "$PRISTINE_DIR/$key.content" "$path"
    sha_after="$(sha256sum "$path" | cut -d' ' -f1)"
    if [ "$sha_after" != "$want" ]; then
      printf '%s\tRESTORE-MISMATCH\t-\t%s\n' "$id" "$want" >>"$TSV"
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
      return
    fi

    printf '%s\tGUARD\t%s\t%s\n' "$id" "$green" "$want" >>"$TSV"
    if [ "$green" != "YES" ]; then
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    fi
    check_budget "after guard leg $id"
  }

  # M-B1a — the write tool becomes REACHABLE (allow gains edit_file,
  # deny loses it). The resolved set now holds a write-capable tool and
  # the invocation leg WRITES → RED.
  leg "M-B1a" "agents/skill-keeper/meta.json" \
'    "allow": ["read_file", "list_directory", "glob_files", "grep_files", "self", "help", "knowledge", "dynamic-skill", "skill-evolution", "shared_meta_kv", "pok", "nest-report"],
    "deny": ["bash", "proc", "service", "write_file", "edit_file", "inner_soul"]
' \
'    "allow": ["read_file", "list_directory", "glob_files", "grep_files", "self", "help", "knowledge", "dynamic-skill", "skill-evolution", "shared_meta_kv", "pok", "nest-report", "edit_file"],
    "deny": ["bash", "proc", "service", "write_file", "inner_soul"]
' \
"$PIN_FILE::TestResolverLeg::test_the_resolved_set_has_no_forbidden_capability"

  # M-B1b — GUARD: allow gains edit_file, deny KEEPS it. Deny wins ⇒
  # the structural pin must stay GREEN.
  guard_leg "M-B1b" "agents/skill-keeper/meta.json" \
'    "allow": ["read_file", "list_directory", "glob_files", "grep_files", "self", "help", "knowledge", "dynamic-skill", "skill-evolution", "shared_meta_kv", "pok", "nest-report"],
' \
'    "allow": ["read_file", "list_directory", "glob_files", "grep_files", "self", "help", "knowledge", "dynamic-skill", "skill-evolution", "shared_meta_kv", "pok", "nest-report", "edit_file"],
' \
"$PIN_FILE::TestResolverLeg::test_deny_wins_over_allow"

  # M-B1c — the resolution becomes allow-after-deny: a tool in BOTH
  # lists survives. The structural deny pin → RED.
  leg "M-B1c" "daemon/tools/instance.py" \
'        allowed_tools -= denied_tools
' \
'        allowed_tools -= (denied_tools - set(allow))
' \
"$PIN_FILE::TestResolverLeg::test_deny_wins_over_allow"

  # M-B1d — the tool factory is stubbed to return a PERMISSIVE set (the
  # resolver is no longer the production one). The invocation leg must
  # catch it: the sequence WRITES → RED.
  leg "M-B1d" "daemon/tools/instance.py" \
'    allowed_tools = resolve_tool_filter(
        allow=effective_allow,
        deny=agent_meta.tools.deny,
        all_tool_names=all_tool_names,
    )
' \
'    allowed_tools = set(all_tool_names)
' \
"$PIN_FILE::TestInvocationLeg::test_the_b7da3ed0_sequence_is_refused"

  # M-B1e — ORACLE mutation: the capability check becomes NAME-based.
  # The pin's self-test (a synthetic tool with a NOVEL name and a
  # write-shaped body) must still classify it as write-capable → the
  # name-based classifier REDs it.
  leg "M-B1e" "tests/unit/test_skill_keeper_confinement.py" \
'    project_writes, temp_writes = _write_profile(scope_nodes)
' \
'    project_writes = getattr(tool, "name", "") in {"write_file", "edit_file"}
    temp_writes = False
' \
"$PIN_FILE::TestResolverLeg::test_the_classifier_catches_a_renamed_write_tool"

  # M-B1f (B1 fix round 1 — D2) — the REACHABILITY edge that hides a
  # write: a helper PASSED AS AN ARGUMENT to a thread-offload call
  # (``asyncio.to_thread``). The edge is removed → ``experience``'s
  # write becomes invisible → the ENUMERATED writer set loses a member
  # → RED. (The old classifier reached only DIRECTLY CALLED helpers,
  # which is why the pin asserted a convenient subset — ``explore``
  # alone — instead of the truth.)
  leg "M-B1f" "tests/unit/test_skill_keeper_confinement.py" \
'            if fn.attr in _THREAD_OFFLOAD_NAMES:
' \
'            if False and fn.attr in _THREAD_OFFLOAD_NAMES:
' \
"$PIN_FILE::TestResolverLeg::test_the_temp_confined_writers_are_enumerated"

  # M-B1g (B1 fix round 1 — D2) — the temp-vs-project decision
  # degrades to "the body mentions gettempdir" (the OLD behaviour). A
  # tool that derives a scratch path from the temp dir and then writes
  # a PROJECT path is DOWNGRADED to the temp class → the pin's
  # self-test REDs it.
  leg "M-B1g" "tests/unit/test_skill_keeper_confinement.py" \
'            derivations = _target_derivations(target, scope_nodes)
            if any(
                marker in text
                for text in derivations
                for marker in _TEMP_TARGET_MARKERS
            ):
                temp = True
' \
'            derivations = _target_derivations(target, scope_nodes)
            body = ast.unparse(fnode)
            if any(marker in body for marker in _TEMP_TARGET_MARKERS):
                temp = True
' \
"$PIN_FILE::TestResolverLeg::test_a_project_write_is_not_downgraded_by_a_gettempdir_mention"

  # M-B1h (B1 fix round 1 — D2) — the write PRIMITIVES stop being
  # recognised at all → the classifier cannot see a write → the
  # ``open``/``print(file=)`` self-test REDs it.
  leg "M-B1h" "tests/unit/test_skill_keeper_confinement.py" \
'    out: list[tuple[Any, ast.Call]] = []
' \
'    out: list[tuple[Any, ast.Call]] = []
    return out  # M-B1h PLANT: the write primitives are not recognised
' \
"$PIN_FILE::TestResolverLeg::test_the_classifier_catches_an_open_and_print_write"

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
if [ "${SKILL_KEEPER_CONFINEMENT_MUTATIONS:-1}" = "0" ]; then
  echo "--- phase 2: SKIPPED (SKILL_KEEPER_CONFINEMENT_MUTATIONS=0) ---"
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
  echo "RESULT: FAIL (phase 1: the confinement pin is RED — rc=$PHASE1_RC)"
  exit 1
elif [ "$MUTATION_RC" -ne 0 ]; then
  echo "RESULT: FAIL (mutation matrix: $MUTATION_RC leg(s) non-discriminating)"
  exit 1
else
  echo "RESULT: PASS"
  exit 0
fi
