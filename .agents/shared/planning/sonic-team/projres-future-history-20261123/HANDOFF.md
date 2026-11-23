# HANDOFF — projres-future-history-20261123 (v0.31.39, staged-only)

Commission: extend name/shortname project resolution to the project_future_* and
project_history_* tool families (Emperor-approved follow-up to the project_get fix,
Discord 2026-11-23 "Number 2, you can do that"). Base 1b70a58ca; integration branch
sonic/projres-fh-20261123; tag v0.31.39 at tip; STAGED ONLY — no promote (Emperor's
ceremony owns promote).

This revision supersedes the v0.31.38 handoff (449163f17). It covers the fix wave
merged on top of it: the 4 module branches re-merged --no-ff in the order
pack -> future -> history -> release (e25919fde, a1ac79356, b51c63397, 072a57beb),
plus two truthfulness corrections to the v0.31.39 CHANGELOG section and this
artifact. Two claims carried by the v0.31.38 handoff were FALSE and are corrected
below (the "byte-compatible" id-path claim, and the "schema required-sets are only
probe-asserted" claim) — both are now stated in their true form.

## Files touched across modules

Wave 1 (the v0.31.38 feature lane, already merged):

- projres-fh-foldins (slot 1, 38de1b87a): daemon/tools/project.py,
  tests/test_project_resolution.py, .agents/tester/PACKS.md
- projres-fh-future (slot 2, 0b4493258): daemon/tools/project_future.py,
  tests/unit/test_project_future_tools.py, tests/unit/test_project_future_resolution.py (NEW)
- projres-fh-history (slot 3, a84bbc51c): daemon/tools/project_history.py,
  tests/unit/test_project_history_tools.py, tests/unit/test_project_history_resolution.py (NEW)
- projres-fh-release (slot 4, b1b4ed55b): CHANGELOG.md, pyproject.toml,
  daemon/__init__.py, tests/test_api.py, uv.lock

Wave 2 (the v0.31.39 fix lane, merged in this pass):

- projres-fh2-pack (slot 1, e25919fde): .agents/tester/PACKS.md
- projres-fh2-future (slot 2, a1ac79356): tests/unit/test_project_future_resolution.py
- projres-fh2-history (slot 3, b51c63397 + 2085303b7): daemon/tools/project_history.py,
  tests/unit/test_project_history_resolution.py
- projres-fh2-release (slot 4, 072a57beb): CHANGELOG.md, pyproject.toml,
  daemon/__init__.py, tests/test_api.py, uv.lock

Integration (this lane):

- .agents/shared/planning/sonic-team/projres-future-history-20261123/HANDOFF.md
- CHANGELOG.md (the two v0.31.39 bullet corrections — committed ALONE)
- .agents/tester/RESULTS/sonic-projres-future-history-20261123-results.md,
  test/packs/project_resolution_triad_runtime_proof.sh (tester artifacts landed at
  ec2e84217, before this lane's merges)

Union of the merged delta (1b70a58ca..HEAD), 17 paths: .agents/shared/planning/sonic-team/projres-future-history-20261123/HANDOFF.md, .agents/tester/PACKS.md, .agents/tester/RESULTS/sonic-projres-future-history-20261123-results.md, CHANGELOG.md, daemon/__init__.py, daemon/tools/project.py, daemon/tools/project_future.py, daemon/tools/project_history.py, pyproject.toml, test/packs/project_resolution_triad_runtime_proof.sh, tests/test_api.py, tests/test_project_resolution.py, tests/unit/test_project_future_resolution.py, tests/unit/test_project_future_tools.py, tests/unit/test_project_history_resolution.py, tests/unit/test_project_history_tools.py, uv.lock. ZERO migration files in the delta.

## What changed

All 7 tools (project_future_add/list/remove; project_history_add/list/search/delete)
now accept the project_id/name/shortname triad exactly like project_get: id path is
store.get() ONLY (never calls the resolver — mock-store compatible) and threads the
REQUESTED id; BUT its not-found refusal is NOT byte-compatible with the pre-ladder
tool — it now carries the TYPED NOT_FOUND shape
{"error": "Project not found", "error_code": "NOT_FOUND", "query": <requested id>,
"candidates": []}, where the pre-ladder tool returned the untyped
"Project '<id>' not found". No in-tree consumer matched that old string;
name -> resolve_project_reference(name, scope="name") full ladder; shortname ->
scope="shortname" strict ladder; none -> {"error": "Must provide either project_id,
name, or shortname"}. Matching logic lives ONLY in the repository seam — zero
duplication; each tool file has a thin dispatch helper. Typed refusals on every tool:
NOT_FOUND {error, error_code, query, candidates:[]} and AMBIGUOUS_PROJECT
{error, error_code, query, candidates, candidate_count}. The RESOLVED project_id is
threaded into all store calls, entry-not-found messages, and (history_add) the C8
Blueprint pending-queue hook (get_metadata/enqueue use the resolved id; the
SYSTEM_DEFAULT_PROJECT_NAME guard is unchanged). Fold-ins: project_get's AMBIGUOUS
refusal now carries "query" (parity with NOT_FOUND) and its stale "-> dict | None"
annotation is corrected to "-> dict". Docs: _FULL_DOCS Args document the triad on all
7 verbs; project_future doc-substance tokens (cap "EXACTLY 32", eviction/evicted
disclosure) preserved.

Fix wave (v0.31.39):

- Schema required-set regression pins COMMITTED on BOTH sides — the future verbs in
  tests/unit/test_project_future_resolution.py (class TestSchemaRequiredSets — add
  required=[summary], remove=[entry_id], list=[]) and the history verbs in
  tests/unit/test_project_history_resolution.py (class TestTriadSchemaPins — add
  required=[entry_type,summary], list=[], search=[query], delete=[entry_id]). Every
  one of the 7 verbs asserts that the triad params (project_id/name/shortname) are
  NEVER required.
- History verb doc precision FIXED (b51c63397): every project_history_* verb's `name`
  Args now reads "The project name or shortname (used if project_id is not provided).
  Resolved via scope=\"name\" — the FULL ladder (L1-L5): may match the project NAME or
  any of its SHORTNAMES." The v0.31.38 residual ("closing it needs a source edit to
  daemon/tools/project_history.py, outside this release lane's file fence") is CLOSED.
- Runtime gate pack project_resolution_triad_runtime_proof registered in
  .agents/tester/PACKS.md (it is a RUNTIME driver, not a pytest replay): 7 tools x 5
  behaviours = 35 checks.

Release: CHANGELOG [v0.31.39] section + 5-file bump commit
(chore(release): bump version to v0.31.39, house format). Two v0.31.39 bullets were
FALSE and are corrected in this lane: the "required-set assertions are NOT yet
committed as regression pins" bullet and the "OPEN residual: closing it needs a source
edit to daemon/tools/project_history.py" bullet. Zero migrations in the delta.

## What is UNTESTED

- No live-daemon / HTTP / REST exercise of the 7 tools. tests/test_project_history_api.py
  is untouched; its 26 failures are PRE-EXISTING at base (proven by re-running on a
  git-archive extraction of base 1b70a58ca — all HTTP 302 auth redirects), not introduced.
- No PostgreSQL-dialect run of the resolution ladder (in-memory SQLite only). The
  ladder's ASCII-vs-Unicode case-folding divergence across engines is inherited
  pre-existing seam behavior, unchanged by this lane.
- No LLM-facing render/eyeball of the new tool JSON schemas. The required-set
  assertions are no longer probe-only — they are COMMITTED regression pins on BOTH
  sides (tests/unit/test_project_future_resolution.py class TestSchemaRequiredSets for
  the future verbs; tests/unit/test_project_history_resolution.py class
  TestTriadSchemaPins for the history verbs), so the required-set CONTRACT is
  exercised on every run. What remains unexercised is the LLM-facing RENDER (an
  actual model's view of the emitted schema), not the required-set contract.
- MCP kb_server resolver NOT unified (explicitly out of scope — parked for a future lane).
- No daemon boot smoke carrying the change; no promote (staged-only lane).

## Known risks

- Keyword-only signatures: after the defaulted triad, remaining required params
  (future: summary, entry_id; history: entry_type, summary, query, entry_id) are
  KEYWORD-ONLY (`*`) so the JSON schema keeps them required. Raw Python positional
  calls now raise TypeError; ZERO in-tree callers exist (verified by grep at
  origin/latest for both families, code files only; the inner_soul.py mentions are
  docstring strings already using the keyword idiom; tools are invoked via .invoke(dict)).
- All AMBIGUOUS_PROJECT refusals (including project_get) now carry "query" — a consumer
  doing exact-dict-equality (not key-subset) would see the extra key; none found in-tree.
- The id path threads the REQUESTED id verbatim into refusals/threading (the store
  looked the project up BY that id, so this part is unchanged); the resolver path
  threads res["project"].project_id — identical for the real repository. It is NOT
  byte-compatible with the pre-ladder tool in ONE respect: the id path's not-found
  refusal is now the TYPED NOT_FOUND shape {error, error_code, query, candidates:[]}
  where it was the untyped "Project '<id>' not found". No in-tree consumer matched
  the old string — the other "Project ... not found" strings in the tree belong to
  daemon/mcp/kb_server.py, daemon/routers/projects.py and daemon/tools/critical_notes.py,
  which are different surfaces with their own contracts.
- A store lacking resolve_project_reference raises AttributeError on the name/shortname
  paths only (id path unaffected) — mock-store test idiom unaffected.
- Ladder caveats inherited from the seam (pre-existing, unchanged): % and _ act as SQL
  wildcards on the L5 substring tier; untrimmed queries do not resolve.
- Merge/integration seams: none new — all 4 merges were conflict-free (disjoint file
  sets); the refusal-shape divergence the future module flagged vs base project_get is
  resolved UNIFORM on the merged tree (all surfaces carry query).
- Sibling-slot hazard: another lane was concurrently staging a release during this
  pass and could take the v0.31.39 tag slot first. The slot was re-derived live
  (git ls-remote --tags origin 'v0.31.*') immediately before the tag was created;
  a pushed tag is never re-cut and never force-pushed.

## How to verify

On the merged tree (integration worktree / any checkout of tag v0.31.39):

1. Registered gate pack `project_future_history_resolution` (PACKS.md row, this lane):
   cd /home/nea/ensemble-src-wt-projres-fh-integ && env -u POSTGRES_DB -u POSTGRES_USER \
     -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
     ENSEMBLE_SELF_ENV=dev PYTHONPATH=/home/nea/ensemble-src-wt-projres-fh-integ \
     .venv/bin/python -m pytest tests/test_project_resolution.py \
     tests/test_project_tools.py tests/unit/test_project_future_tools.py \
     tests/unit/test_project_history_tools.py \
     tests/unit/test_project_future_resolution.py \
     tests/unit/test_project_history_resolution.py -q --tb=short -p no:randomly --timeout=60
   => 225 passed / 0 failed, rc=0, 22.16s, 0 skipped on the v0.31.39 merged tree.
   (The registered 218P/0F baseline was measured at the v0.31.38 tree 449163f17; the
   +7 nodes are exactly the newly committed schema pins — 3 future + 4 history.)
   The POSTGRES_* family is scrubbed PER VARIABLE — NEVER the `env -u POSTGRES_*` glob
   form, a silent NO-OP because bash does not glob env names.
2. Registered runtime pack `project_resolution_triad_runtime_proof` (PACKS.md row):
   cd /home/nea/ensemble-src-wt-projres-fh-integ && timeout 300 \
     ./test/packs/project_resolution_triad_runtime_proof.sh
   => 35/35 checks PASS, `DRIVER RESULT: PASS` + `RESULT: PASS`, rc=0. It is
   self-fencing (per-variable POSTGRES_*/DATABASE_URL scrub + ENSEMBLE_SELF_ENV=dev;
   worktree .venv only; dual-layer timeout: script-internal 110s + command-level 300s).
3. Riders: ... -m pytest tests/unit/test_project_future_registration.py  => 8P/0F;
   ... -m pytest tests/services/test_blueprint_phase3_hooks.py (C8 hook, resolved-id
   threading) => 20P/0F.
4. Triad census: grep -c "project_id: str | None = None" over daemon/tools/project.py
   (1), project_future.py (3), project_history.py (4).
5. Version: .venv/bin/python -c "import daemon; print(daemon.__version__)" => 0.31.39.

## Bash-raises used

none — every merge, pack and verification command in this lane ran inside the 600 s
default (the 6-file pack took 22.16 s; the runtime pack completed well inside its
300 s command-level timeout; the 4 merges were conflict-free and instantaneous).
The v0.31.39 staging run (npm ci + frontend build + stage.sh live) happens AFTER the
tag is cut and is NOT merge machinery; its own raise accounting is reported in the
release-lane report, not here.

## Complexity self-assessment

LOW (assessed at plan-open, §2.5; no override, no handover raised). Mechanical
seam-threading of a landed resolver across 2 tool files with an in-tree reference
consumer; no migrations; no coupling; 4 disjoint modules merged conflict-free. The
fix wave added 2 committed schema-pin files and 1 docstring edit; the release-lane
choreography (merge, CHANGELOG truthfulness, HANDOFF refresh, 2 packs, push, tag,
stage) is release mechanics, not commission complexity.
