#!/usr/bin/env bash
# Test Pack: skill_id_verbatim_contract_unit_test — wi-76d89e11
#
# The skill id-emission CONTRACT pin: every agent-facing skill id the
# skill tools EMIT must be an id ``skill_view`` ACCEPTS VERBATIM — the
# full uuid4 for DB skills, the ``pf:<ns>:<name>`` ADDRESS for
# project-file skills.
#
# What this pack exercises:
#   0. PHASE 0 — the IMPORT GUARD (mandatory, pin-spec.md §H): prove
#      ``import daemon`` resolves to THIS checkout. A run that silently
#      imported the shared checkout or the live install would report a
#      green result computed against FOREIGN code. The pack fails LOUDLY.
#   1. tests/tools/test_skill_id_verbatim_contract.py (NEW) — the pin:
#      list → view → identity per source, value equality per source,
#      BOTH search render paths (fused + file-only degraded), the
#      skill_create success line, the injection body segment, the
#      project-file DISCRIMINATOR PARITY battery (W2: the two
#      duplicated ``_is_project_file_skill`` helpers must agree on
#      every discriminating shape), and the safety invariants (the
#      pseudo-id stays the internal key, the embedding rows stay
#      reachable, the ``pf:``/``pf-`` feedback routing stays intact).
#   2. tests/tools/test_skill_tools_pf_surface.py — the sibling corpus
#      (the folded W4 pin: the ``— source: db`` suffix additivity and
#      the knob-OFF byte-shape).
#   3. The MUTATION MATRIX (M1-M6 + M2b + M7-M9) — pack-level plants:
#      plant the
#      defect → the discriminating leg must go RED → restore → the same
#      leg must go GREEN, with the planted file's sha256 verified
#      identical after the restore. A pin that cannot fail is worse than
#      no pin, so a NON-DISCRIMINATING leg fails the pack.
#
# Fences (house):
#   - Per-variable env scrub (the ``env -u POSTGRES_*`` glob is a SILENT
#     NO-OP — bash does not glob env names; an ambient
#     POSTGRES_DB=ensemble_prod would survive it).
#   - FAKE_HOME (mktemp) — no live-shaped path resolution; removed on exit.
#   - Scratch SQLite INSIDE the pytest run (tmp_path) — never the live DB.
#   - Interpreter: the checkout's own .venv when present, else the
#     certified main-checkout interpreter (worktrees carry no .venv);
#     cwd is the checkout root, so ``import daemon`` resolves HERE.
#
# Usage:
#   bash test/packs/skill_id_verbatim_contract_unit_test.sh
#   SKILL_ID_VERBATIM_MUTATIONS=0 bash test/packs/skill_id_verbatim_contract_unit_test.sh
#     — skip the mutation matrix (the pin only).
#
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. The RESULT label always
# prints — a red leg never kills the pack silently.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Test Pack: skill_id_verbatim_contract_unit_test ==="

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
FAKE_HOME="$(mktemp -d /tmp/skill-id-verbatim-pack-XXXXXX)"
PLANTED_BACKUP_DIR="$FAKE_HOME/backups"
PLANTED_TARGET_DIR="$FAKE_HOME/targets"
mkdir -p "$PLANTED_BACKUP_DIR" "$PLANTED_TARGET_DIR"

restore_plants() {
  local f
  for f in "$PLANTED_BACKUP_DIR"/*; do
    [ -e "$f" ] || continue
    local target
    target="$(cat "$PLANTED_TARGET_DIR/$(basename "$f").target" 2>/dev/null || true)"
    [ -n "$target" ] || continue
    cp "$f" "$target"
  done
}
cleanup() {
  restore_plants
  rm -rf "$FAKE_HOME"
}
trap cleanup EXIT

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
    "$PY" -m pytest "$@" -q --tb=line --override-ini="addopts="
}

PIN_FILE="tests/tools/test_skill_id_verbatim_contract.py"
SIBLING_FILE="tests/tools/test_skill_tools_pf_surface.py"

# --- phase 0: the IMPORT GUARD (mandatory — pin-spec.md §H, W5) -----------
# The pack MUST prove that ``import daemon`` resolves to THIS checkout
# and not to the shared checkout / the live install. Without the guard a
# run from a worktree can silently import the FOREIGN tree (the
# ``PYTHONPATH`` prepend is a belt, not a proof — an editable install or
# a ``.pth`` entry can still win). The pack fails LOUDLY rather than
# reporting a green result computed against the wrong code.
#
# The comparison is an EXACT realpath match of the package directory
# against ``$PROJECT_DIR/daemon`` — NOT a ``str.startswith`` prefix
# test. The prefix form has a sibling-prefix trap: the worktree
# ``/home/nea/ensemble-src-wt-<slug>`` is a STRING PREFIX extension of
# the shared checkout ``/home/nea/ensemble-src``, so a prefix test
# would report PASS while the import had resolved from the FOREIGN
# tree. (Verified: the trap is real — see the fold receipt.)
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
        "  (the run would have tested the FOREIGN tree — shared checkout "
        "or live install)\n"
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

# --- phase 1: the pin + the sibling corpus --------------------------------
echo "--- phase 1: the contract pin ---"
PHASE1_LOG="$FAKE_HOME/phase1.log"
run_pytest "$PIN_FILE" "$SIBLING_FILE" >"$PHASE1_LOG" 2>&1
PHASE1_RC=$?
tail -3 "$PHASE1_LOG"

# --- phase 2: the mutation matrix -----------------------------------------
MUTATION_FAILURES=0
run_mutations() {
  echo "--- phase 2: the mutation matrix (plant → RED → restore → GREEN) ---"
  TSV="$FAKE_HOME/mutation-matrix.tsv"
  : >"$TSV"

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
    local backup="$PLANTED_BACKUP_DIR/$id.$(basename "$path")"
    cp "$path" "$backup"
    printf '%s' "$path" >"$PLANTED_TARGET_DIR/$(basename "$backup").target"
    local sha_before
    sha_before="$(sha256sum "$path" | cut -d' ' -f1)"

    if ! plant "$path" "$old" "$new"; then
      printf '%s\tPLANT-FAILED\t-\t%s\n' "$id" "$sha_before" >>"$TSV"
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
      cp "$backup" "$path"
      return
    fi

    local red green
    if run_pytest "$node" >"$FAKE_HOME/$id.red.log" 2>&1; then
      red="NO"
    else
      red="YES"
    fi

    cp "$backup" "$path"
    local sha_after
    sha_after="$(sha256sum "$path" | cut -d' ' -f1)"
    if [ "$sha_before" != "$sha_after" ]; then
      printf '%s\tRESTORE-MISMATCH\t-\t%s\n' "$id" "$sha_before" >>"$TSV"
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
      return
    fi

    if run_pytest "$node" >"$FAKE_HOME/$id.green.log" 2>&1; then
      green="YES"
    else
      green="NO"
    fi

    printf '%s\t%s\t%s\t%s\n' "$id" "$red" "$green" "$sha_before" >>"$TSV"
    if [ "$red" != "YES" ] || [ "$green" != "YES" ]; then
      MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    fi
  }

  # M1 — skill_list's DB bracket token truncated back to 8 chars.
  leg "M1" "daemon/tools/skill_tools.py" \
    'short_id = skill_id if isinstance(skill_id, str) else "????????"' \
    'short_id = skill_id[:8] if isinstance(skill_id, str) else "????????"' \
    "$PIN_FILE::TestListVerbatimContract::test_db_bracket_token_is_the_stored_full_uuid"

  # M2 — the file-only search payload's "id" re-keyed back to the pseudo-id.
  leg "M2" "daemon/tools/skill_tools.py" \
    '"id": getattr(file_skill, "address", None),' \
    '"id": getattr(file_skill, "id", None),' \
    "$PIN_FILE::TestSearchPayloadContract::test_file_only_path_file_entry_id_is_consumable"

  # M2b — the FUSED seam: to_dict() renders the pseudo-id as the id.
  leg "M2b" "daemon/services/project_skill_source/model.py" \
    'd["id"] = self.address  # the agent-facing, verbatim-consumable id' \
    'd["id"] = d["pseudo_id"]  # MUTANT: the internal key as the agent id' \
    "$PIN_FILE::TestSearchPayloadContract::test_fused_path_file_entry_id_is_consumable"

  # M3 — the injection body renders the pseudo-id again.
  leg "M3" "daemon/services/skill_injection_service.py" \
    'skill_id = getattr(skill, "address", None) or skill_id' \
    'skill_id = getattr(skill, "id", None)  # MUTANT' \
    "$PIN_FILE::TestInjectionBodyContract::test_file_injection_body_renders_the_address"

  # M4 — skill_create's success line truncated again.
  leg "M4" "daemon/tools/skill_tools.py" \
    '                created.id
' \
    '                created.id[:8]
' \
    "$PIN_FILE::TestSkillCreateContract::test_create_line_id_is_the_stored_uuid_and_resolves"

  # M5 — the UNSAFE rewrite: the file skill's id ATTRIBUTE remapped to the
  # address (the embeddings rows become unreachable — silent degradation).
  # The plant targets the OBJECT construction (``_to_file_skill``) — the
  # ROW construction (``meta_to_row``) keeps the pseudo-id key.
  leg "M5" "daemon/services/project_skill_source/service.py" \
    '        return ProjectFileSkill(
            id=meta.pseudo_id,' \
    '        return ProjectFileSkill(
            id=meta.address,' \
    "$PIN_FILE::TestSafetyInvariants::test_embeddings_stay_keyed_by_the_pseudo_id"

  # M6 — the legacy-transcript ``pf-<hex16>`` feedback branch removed.
  leg "M6" "daemon/tools/skill_tools.py" \
    '            skill_id.startswith(ADDRESS_PREFIX)
            or skill_id.startswith(PSEUDO_ID_PREFIX)
' \
    '            skill_id.startswith(ADDRESS_PREFIX)
' \
    "$PIN_FILE::TestSafetyInvariants::test_feedback_accepts_both_forms_and_keys_by_pseudo_id"

  # M7 — the DISCRIMINATOR PARITY (council W2): the INJECTION-side
  # helper drops its source-tag fallback. The two helpers are
  # duplicated on purpose (approver N6) — a one-sided divergence is
  # exactly the defect class this lane fixed, and it must be caught.
  leg "M7" "daemon/services/skill_injection_service.py" \
    '    return getattr(skill, "source", None) == SOURCE_PROJECT_FILE
' \
    '    return False  # MUTANT: the source-tag fallback dropped
' \
    "$PIN_FILE::TestProjectFileDiscriminatorParity"

  # M8 — the same one-sided divergence on the SEARCH side.
  leg "M8" "daemon/services/skill_search_service.py" \
    '    return getattr(skill, "source", None) == SOURCE_PROJECT_FILE
' \
    '    return False  # MUTANT: the source-tag fallback dropped
' \
    "$PIN_FILE::TestProjectFileDiscriminatorParity"

  # M9 — the address-prefix branch dropped (the OTHER half of the
  # two-step predicate). The constant stays equal, so a
  # constant-equality-only assertion would NOT catch this.
  leg "M9" "daemon/services/skill_injection_service.py" \
    '    if isinstance(address, str) and address.startswith(ADDRESS_PREFIX):
        return True
' \
    '    if False:  # MUTANT: the address-prefix branch dropped
        return True
' \
    "$PIN_FILE::TestProjectFileDiscriminatorParity"

  echo "leg	red	green	sha256(planted-file, pre-plant)"
  cat "$TSV"
  echo "mutation failures: $MUTATION_FAILURES"
}

MUTATION_RC=0
if [ "${SKILL_ID_VERBATIM_MUTATIONS:-1}" = "0" ]; then
  echo "--- phase 2: SKIPPED (SKILL_ID_VERBATIM_MUTATIONS=0) ---"
else
  run_mutations
  MUTATION_RC=$MUTATION_FAILURES
fi

# --- verdict --------------------------------------------------------------
if [ "$PHASE1_RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$PHASE1_RC" -ne 0 ]; then
  echo "RESULT: FAIL (phase 1: the contract pin is RED — rc=$PHASE1_RC)"
  exit 1
elif [ "$MUTATION_RC" -ne 0 ]; then
  echo "RESULT: FAIL (mutation matrix: $MUTATION_RC leg(s) non-discriminating)"
  exit 1
else
  echo "RESULT: PASS"
  exit 0
fi
