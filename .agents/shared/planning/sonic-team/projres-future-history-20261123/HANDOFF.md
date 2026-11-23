# HANDOFF — projres-future-history-20261123 (v0.31.38, staged-only)

Commission: extend name/shortname project resolution to the project_future_* and
project_history_* tool families (Emperor-approved follow-up to the project_get fix,
Discord 2026-11-23 "Number 2, you can do that"). Base 1b70a58ca; integration branch
sonic/projres-fh-20261123; tag v0.31.38 at tip; STAGED ONLY — no promote (Emperor's
ceremony owns promote).

## Files touched across modules

- projres-fh-foldins (slot 1, 38de1b87a): daemon/tools/project.py,
  tests/test_project_resolution.py, .agents/tester/PACKS.md
- projres-fh-future (slot 2, 0b4493258): daemon/tools/project_future.py,
  tests/unit/test_project_future_tools.py, tests/unit/test_project_future_resolution.py (NEW)
- projres-fh-history (slot 3, a84bbc51c): daemon/tools/project_history.py,
  tests/unit/test_project_history_tools.py, tests/unit/test_project_history_resolution.py (NEW)
- projres-fh-release (slot 4, b1b4ed55b): CHANGELOG.md, pyproject.toml,
  daemon/__init__.py, tests/test_api.py, uv.lock
- integration (this lane): .agents/shared/planning/sonic-team/projres-future-history-20261123/HANDOFF.md

## What changed

All 7 tools (project_future_add/list/remove; project_history_add/list/search/delete)
now accept the project_id/name/shortname triad exactly like project_get: id path is
store.get() ONLY (never calls the resolver — byte-compatible, mock-store compatible);
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
disclosure) preserved. Release: CHANGELOG [v0.31.38] section + 5-file bump commit
(chore(release): bump version to v0.31.38, house format). Zero migrations in the delta.

## What is UNTESTED

- No live-daemon / HTTP / REST exercise of the 7 tools. tests/test_project_history_api.py
  is untouched; its 26 failures are PRE-EXISTING at base (proven by re-running on a
  git-archive extraction of base 1b70a58ca — all HTTP 302 auth redirects), not introduced.
- No PostgreSQL-dialect run of the resolution ladder (in-memory SQLite only). The
  ladder's ASCII-vs-Unicode case-folding divergence across engines is inherited
  pre-existing seam behavior, unchanged by this lane.
- No LLM-facing render/eyeball of the new tool JSON schemas (schema probes assert
  required-sets programmatically: future add required=[summary], remove=[entry_id],
  list=[]; history add=[entry_type,summary], list=[], search=[query], delete=[entry_id]).
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
- id path returns the REQUESTED id verbatim in refusals/threading (byte-compat with
  pre-change behavior); the resolver path threads res["project"].project_id — identical
  for the real repository.
- A store lacking resolve_project_reference raises AttributeError on the name/shortname
  paths only (id path unaffected) — mock-store test idiom unaffected.
- Ladder caveats inherited from the seam (pre-existing, unchanged): % and _ act as SQL
  wildcards on the L5 substring tier; untrimmed queries do not resolve.
- Merge/integration seams: none new — all 4 merges were conflict-free (disjoint file
  sets); the refusal-shape divergence the future module flagged vs base project_get is
  resolved UNIFORM on the merged tree (all surfaces carry query).

## How to verify

On the merged tree (integration worktree / any checkout of tag v0.31.38):

1. Registered gate pack `project_future_history_resolution` (PACKS.md row, this lane):
   env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST \
     -u POSTGRES_PORT -u DATABASE_URL ENSEMBLE_SELF_ENV=dev PYTHONPATH=$PWD \
     .venv/bin/python -m pytest tests/test_project_resolution.py \
     tests/test_project_tools.py tests/unit/test_project_future_tools.py \
     tests/unit/test_project_history_tools.py \
     tests/unit/test_project_future_resolution.py \
     tests/unit/test_project_history_resolution.py -q --tb=short -p no:randomly --timeout=60
   => 218 passed / 0 failed (this lane's merged-tree run, 24.55s).
2. Riders: ... -m pytest tests/unit/test_project_future_registration.py  => 8P/0F;
   ... -m pytest tests/services/test_blueprint_phase3_hooks.py (C8 hook, resolved-id
   threading) => 20P/0F.
3. Triad census: grep -c "project_id: str | None = None" over daemon/tools/project.py
   (1), project_future.py (3), project_history.py (4).
4. Version: .venv/bin/python -c "import daemon; print(daemon.__version__)" => 0.31.38.

## Bash-raises used

none (every command ran inside the 600s default; stage runs under proc background)

## Complexity self-assessment

LOW (assessed at plan-open, §2.5; no override, no handover raised). Mechanical
seam-threading of a landed resolver across 2 tool files with an in-tree reference
consumer; no migrations; no coupling; 4 disjoint modules merged conflict-free.
