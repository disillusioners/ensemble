#!/usr/bin/env bash
# Test Pack: skill_keeper_read_scope_unit_test — Phase-1 workstream B3
#
# The opt-in, default-OFF per-agent READ SCOPE (Phase-1 plan §6 B3,
# DS-15). The skill-keeper holds a read-only filesystem surface, but
# `read_file` / `list_directory` resolve ABSOLUTE paths outside the
# workdir — the credential-hazard residual (repo `.env`, `~/.ssh/*`,
# `/proc/self/environ`, another agent's directory).
#
# What this pack exercises:
#   0. PHASE 0 — the IMPORT GUARD: prove `import daemon` resolves to
#      THIS checkout. A run that silently imported the shared checkout
#      or the live install would report a green result computed
#      against FOREIGN code. The pack fails LOUDLY.
#   1. tests/unit/test_skill_keeper_read_scope.py (NEW) — the pin:
#      the keeper's declared "project" scope is read from
#      `agents/skill-keeper/meta.json`; `read_file` / `list_directory`
#      REFUSE an absolute path outside the project workdir; relative
#      in-workdir reads stay LEGAL; EVERY other agent's read behavior
#      is byte-identical to base (no declaration ⇒ no scope); the scope
#      is NOT agent-controllable; the write path is untouched.
#   2. The MUTATION MATRIX (M-B3a..d) — pack-level plants: plant the
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
#   bash test/packs/skill_keeper_read_scope_unit_test.sh
#   SKILL_KEEPER_READ_SCOPE_MUTATIONS=0 bash test/packs/skill_keeper_read_scope_unit_test.sh
#     — skip the mutation matrix (the pin only).
#   SKILL_KEEPER_READ_SCOPE_BUDGET_S=300 bash test/packs/skill_keeper_read_scope_unit_test.sh
#     — override the internal wall-clock budget (seconds).
#
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. The RESULT label always
# prints — a red leg never kills the pack silently. On SIGTERM/SIGINT
# the trap restores pristine before exiting non-zero.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Test Pack: skill_keeper_read_scope_unit_test ==="

# --- internal wall-clock budget ------------------------------------------
PACK_START="$(date +%s)"
BUDGET_S="${SKILL_KEEPER_READ_SCOPE_BUDGET_S:-300}"
elapsed_s() { echo $(( $(date +%s) - PACK_START )); }
check_budget() {
  local elapsed label="${1:-}"
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (internal timer: wall ${elapsed}s > SKILL_KEEPER_READ_SCOPE_BUDGET_S=${BUDGET_S}s ${label})"
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
FAKE_HOME="$(mktemp -d /tmp/skill-keeper-read-scope-pack-XXXXXX)"
PRISTINE_DIR="$FAKE_HOME/pristine"
mkdir -p "$PRISTINE_DIR"

# The COMPLETE plant surface of the mutation matrix.
MUTABLE_FILES=(
  "daemon/tools/filesystem.py"
  "daemon/tools/instance.py"
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
LOCK_FILE="/tmp/skill-keeper-read-scope-pack-${LOCK_KEY}.lock"
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

PIN_FILE="tests/unit/test_skill_keeper_read_scope.py"

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
echo "--- phase 1: the read-scope pin ---"
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

  # A MULTI-PLANT leg. Some defects are COMPOSITE — they need SEVERAL
  # edits across SEVERAL files to reproduce faithfully. The leg plants
  # every spec, runs the probe (the probe MUST go RED), then restores
  # EVERY touched file to PRISTINE and re-runs it (the probe MUST go
  # GREEN again). A leg that starts from a contaminated file, a plant
  # that does not apply, or a restore that does not land is a LOUD
  # failure — never a silent pass.
  leg_multi() {
    local id="$1" node="$2"; shift 2
    local -a specs=("$@")
    local n=${#specs[@]} i path old new
    local key want sha_before sha_after red green

    # 1. every touched file must be PRISTINE at leg start
    for ((i=0; i<n; i+=3)); do
      path="${specs[$i]}"
      key="$(_pristine_key "$path")"
      want="$(cat "$PRISTINE_DIR/$key.sha")"
      sha_before="$(sha256sum "$path" | cut -d' ' -f1)"
      if [ "$sha_before" != "$want" ]; then
        printf '%s\tLEG-START-CONTAMINATED\t-\t%s\n' "$id" "$sha_before" >>"$TSV"
        MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
        restore_pristine
        return
      fi
    done

    # 2. plant every spec
    for ((i=0; i<n; i+=3)); do
      path="${specs[$i]}"
      old="${specs[$((i+1))]}"
      new="${specs[$((i+2))]}"
      if ! plant "$path" "$old" "$new"; then
        printf '%s\tPLANT-FAILED\t-\t%s\n' "$id" "$path" >>"$TSV"
        MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
        restore_pristine
        return
      fi
    done

    # 3. the probe must go RED
    if run_pytest "$node" >"$FAKE_HOME/$id.red.log" 2>&1; then
      red="NO"
    else
      red="YES"
    fi

    # 4. restore to PRISTINE (every touched file)
    restore_pristine
    for ((i=0; i<n; i+=3)); do
      path="${specs[$i]}"
      key="$(_pristine_key "$path")"
      want="$(cat "$PRISTINE_DIR/$key.sha")"
      sha_after="$(sha256sum "$path" | cut -d' ' -f1)"
      if [ "$sha_after" != "$want" ]; then
        printf '%s\tRESTORE-MISMATCH\t-\t%s\n' "$id" "$want" >>"$TSV"
        MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
        restore_pristine
        return
      fi
    done

    # 5. the same probe must go GREEN again
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

  # M-B3a — the scope branch is REMOVED from the resolver. The keeper's
  # absolute read is allowed again → RED.
  leg "M-B3a" "daemon/tools/filesystem.py" \
'    scope = declared_read_scope(scope_agent_id)
    if scope:
        # A SCOPED agent reads RELATIVE paths against the TRUSTED
        # daemon-resolved anchor only. The `workdir` ARGUMENT is never
        # consulted here — it is agent-supplied and must not be able to
        # move the boundary (D1).
        return _resolve_within_scope(path, scope)

' \
'' \
"$PIN_FILE::TestKeeperReadScopeEnforcement::test_read_file_refuses_an_absolute_path_outside_the_workdir_without_a_workdir_argument"

  # M-B3b — the scope check applied to RELATIVE paths too. A relative
  # in-workdir read must stay LEGAL → RED.
  leg "M-B3b" "daemon/tools/filesystem.py" \
'    if _is_absolute_path(path):
        return None, (
            f"ERROR: read scope {scope!r} permits RELATIVE reads only, "
' \
'    if True:
        return None, (
            f"ERROR: read scope {scope!r} permits RELATIVE reads only, "
' \
"$PIN_FILE::TestKeeperReadScopeEnforcement::test_read_file_allows_a_relative_in_workdir_path"

  # M-B3c — the default scope becomes "project" for EVERY agent. The
  # no-other-agent-changes pin → RED.
  leg "M-B3c" "daemon/tools/filesystem.py" \
'    scope, path, mtime_ns, size, ctime_ns = _load_declared_read_scope(agent_id)
' \
'    scope, path, mtime_ns, size, ctime_ns = _load_declared_read_scope(agent_id)
    scope = scope or READ_SCOPE_PROJECT
' \
"$PIN_FILE::TestOtherAgentsAreUnchanged::test_another_agent_reads_an_absolute_path_outside_the_workdir"

  # M-B3d — the keeper is exempted from the scope. The refusal pins →
  # RED.
  leg "M-B3d" "daemon/tools/filesystem.py" \
'    if not agent_id:
        return None
    cached = _READ_SCOPE_CACHE.get(agent_id)
' \
'    if not agent_id or agent_id == "skill-keeper":
        return None
    cached = _READ_SCOPE_CACHE.get(agent_id)
' \
"$PIN_FILE::TestKeeperReadScopeEnforcement::test_read_file_refuses_an_absolute_in_workdir_path"

  # M-B3e (B3 fix round 1 — D4) — THE D1 ANCHOR DEFECT.
  #
  # The defect is COMPOSITE: the scope's base must never come from the
  # agent-supplied ``workdir`` ARGUMENT. The fix has TWO independent
  # enforcement points, so the faithful defect plant removes BOTH:
  #
  #   (1) the tool FACTORY stops rejecting an agent-supplied workdir
  #       for a scoped agent (``daemon/tools/instance.py``), and
  #   (2) the resolver takes its base from that argument and allows an
  #       absolute path inside it (``daemon/tools/filesystem.py``) —
  #       i.e. the PRE-FIX semantics.
  #
  # With the defect planted, a scoped agent that passes
  # ``workdir="/"`` resolves ``../../etc/passwd`` against "/" and is
  # HANDED /etc/passwd — the reviewed bypass, live. The bypass probe
  # MUST go RED.
  leg_multi "M-B3e" \
"$PIN_FILE::TestTrustedAnchor::test_a_relative_escape_cannot_leave_the_anchor" \
"daemon/tools/instance.py" \
'        if supplied_workdir is None or not str(supplied_workdir).strip():
            return None
' \
'        if True:  # M-B3e PLANT: the agent-supplied workdir is accepted
            return None
' \
"daemon/tools/filesystem.py" \
'def _resolve_within_scope(
    path: str,
    scope: str,
) -> tuple[Path | None, str | None]:
' \
'def _resolve_within_scope(
    path: str,
    scope: str,
    workdir: str | None = None,
) -> tuple[Path | None, str | None]:
' \
"daemon/tools/filesystem.py" \
'        return _resolve_within_scope(path, scope)

    if _is_absolute_path(path):
' \
'        return _resolve_within_scope(path, scope, workdir)

    if _is_absolute_path(path):
' \
"daemon/tools/filesystem.py" \
'    anchor = _trusted_scope_anchor()
' \
'    anchor = (
        Path(workdir).expanduser().resolve()
        if workdir
        else _trusted_scope_anchor()
    )
' \
"daemon/tools/filesystem.py" \
'    if _is_absolute_path(path):
        return None, (
            f"ERROR: read scope {scope!r} permits RELATIVE reads only, "
' \
'    if _is_absolute_path(path):
        if WorkspaceGuard._normed_contains(
            anchor, Path(path).expanduser().resolve()
        ):
            return Path(path).expanduser(), None
        return None, (
            f"ERROR: read scope {scope!r} permits RELATIVE reads only, "
'

  # M-B3f (iteration 2 — D-N1) — the WALK tools lose the scope binding.
  #
  # glob_files / grep_files were bound through `_make_workdir_aware` ONLY,
  # so `_resolve_search_root` bounded the walk against the AGENT-SUPPLIED
  # `workdir` — the same anchor bug D1 had, on the keeper's own bound
  # surface. `grep_files(pattern=<canary>, path=<out-of-anchor>,
  # workdir="/")` returned LIVE CONTENT. Unwrapping the two tools restores
  # that hole ⇒ the content-exfiltration pin MUST go RED.
  leg "M-B3f" "daemon/tools/instance.py" \
'    glob_files_aware = _make_read_scope_aware(
        _make_workdir_aware(glob_files, get_current_workdir),
        get_current_caller_agent_id,
        get_trusted_workdir,
    )
    grep_files_aware = _make_read_scope_aware(
        _make_workdir_aware(grep_files, get_current_workdir),
        get_current_caller_agent_id,
        get_trusted_workdir,
    )
' \
'    glob_files_aware = _make_workdir_aware(glob_files, get_current_workdir)
    grep_files_aware = _make_workdir_aware(grep_files, get_current_workdir)
' \
"$PIN_FILE::TestWalkToolsAreScoped::test_no_content_exfiltration_via_any_walk_vector"

  # M-B3g (iteration 2 — D-N2) — the credential-hazard basename rule is
  # DROPPED. The anchor IS the project repo, so "relative-only" alone
  # permits `read_file(".env")` — the exact file the scope exists to deny
  # (it returned the live credential block in the re-adjudication).
  leg "M-B3g" "daemon/tools/filesystem.py" \
'_SCOPE_DENIED_BASENAMES: frozenset[str] = frozenset({".env"})
' \
'_SCOPE_DENIED_BASENAMES: frozenset[str] = frozenset()
' \
"$PIN_FILE::TestCredentialBasenameRule::test_read_file_refuses_the_repo_dotenv_relative"

  # M-B3h (final pass — D-N7) — the `list_directory` NAME-enumeration
  # filter is removed. The read surface denied `.env` but the LISTING
  # surface still printed the name (`show_hidden=True`) — names only,
  # never content, but the name is itself the hazard: it tells the agent
  # the credential file exists and where. The enumeration pin MUST go RED.
  leg "M-B3h" "daemon/tools/filesystem.py" \
'            # D-N7: a SCOPED agent never sees a denied basename — not
            # even as a NAME (and regardless of ``show_hidden``).
            if list_scope and _is_scope_denied_basename(entry.name):
                continue

' \
'' \
"$PIN_FILE::TestListDirectoryDoesNotEnumerateDeniedBasenames::test_a_scoped_agent_does_not_see_dotenv_names_with_show_hidden"

  # M-B3i (code-review council F-1) — the SYMLINK drop is removed from
  # the scoped candidate filter. A file symlink inside the anchor points
  # OUTSIDE it; the walker enumerates it and grep READS it, so the
  # exfil content comes back. The grep pin MUST go RED.
  leg "M-B3i" "daemon/tools/filesystem.py" \
'    return [
        f
        for f in files
        if not _is_scope_denied_basename(f.name) and not f.is_symlink()
    ]
' \
'    return [f for f in files if not _is_scope_denied_basename(f.name)]
' \
"$PIN_FILE::TestSymlinkExfiltrationIsClosed::test_grep_returns_nothing_from_an_outward_symlink"

  # M-B3j (code-review council F-5) — the scope cache stops keying on the
  # meta.json IDENTITY (it reverts to a plain value cache, never
  # invalidated). A scope declared AFTER the first lookup stays invisible
  # ⇒ the fence fails OPEN. The pin MUST go RED.
  leg "M-B3j" "daemon/tools/filesystem.py" \
'    cached = _READ_SCOPE_CACHE.get(agent_id)
    if cached is not None:
        scope, path, mtime_ns, size, ctime_ns = cached
        if _meta_identity_matches(path, mtime_ns, size, ctime_ns):
            return scope
' \
'    cached = _READ_SCOPE_CACHE.get(agent_id)
    if cached is not None:
        return cached[0]
' \
"$PIN_FILE::TestScopeCacheInvalidatesOnEdit::test_a_newly_declared_scope_is_honoured_without_a_restart"

  # M-B3k (micro-pass — D-N14) — the `list_directory` SYMLINK skip is
  # removed. The F-1 fix dropped symlinks from the WALK tools, but the
  # LISTING surface still enumerated the symlink's NAME (`link.txt@`) for
  # a scoped agent — the sibling surface violated the F-1 principle. The
  # name-visibility pin MUST go RED.
  leg "M-B3k" "daemon/tools/filesystem.py" \
'            if list_scope and entry.is_symlink():
                continue

' \
'' \
"$PIN_FILE::TestListDirectoryDoesNotEnumerateSymlinks::test_a_scoped_agent_does_not_see_the_symlink_name"

  # M-B3l (re-aim) — the anchor stops consulting the factory binding.
  #
  # The plant is BEHAVIOURAL: the actual current_trusted_workdir() CALL is
  # removed and the base is hardcoded, while the literal string
  # current_trusted_workdir() is KEPT in a comment in the SAME function.
  # Under the OLD raw-text pin this plant stays GREEN (the hole the
  # re-aim closes); under the re-aimed AST pin it MUST go RED.
  #
  # CODE-ONLY plant text (no apostrophes — the round-5 lesson).
  leg "M-B3l" "daemon/tools/filesystem.py" \
'    trusted = current_trusted_workdir()
'    # M-B3l PLANT: the behavioural defect — the anchor no longer
'    # consults the factory binding; the base is hardcoded. The literal
'    # current_trusted_workdir() stays in this comment on purpose: the
'    # OLD raw-text pin would still be GREEN here — that is the hole the
'    # re-aimed AST pin closes.
'    trusted = "/tmp/hardcoded-anchor"
' \
'' \
"$PIN_FILE::TestTrustedAnchor::test_the_trusted_anchor_never_comes_from_the_tool_arguments"

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
if [ "${SKILL_KEEPER_READ_SCOPE_MUTATIONS:-1}" = "0" ]; then
  echo "--- phase 2: SKIPPED (SKILL_KEEPER_READ_SCOPE_MUTATIONS=0) ---"
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
  echo "RESULT: FAIL (phase 1: the read-scope pin is RED — rc=$PHASE1_RC)"
  exit 1
elif [ "$MUTATION_RC" -ne 0 ]; then
  echo "RESULT: FAIL (mutation matrix: $MUTATION_RC leg(s) non-discriminating)"
  exit 1
else
  echo "RESULT: PASS"
  exit 0
fi
