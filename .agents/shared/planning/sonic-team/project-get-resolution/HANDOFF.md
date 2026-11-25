# HANDOFF — project-get-resolution

**Commission:** `project_get` name resolution (Emperor, 2026-11-22) — agents-ensemble @ /home/nea/ensemble-src
**Lane:** sonic-developer (integration) · 2 sonic-coder modules, ONE dispatch wave
**Integration branch:** `feature/project-get-resolution` · base `8894f86a853c5fcba2173ab29405f02c71c1f3ef` (origin/latest at open)
**Release slot:** v0.31.28 (tag cut at the branch tip; version-bump commit sits directly under the HANDOFF commit)
**Complexity verdict:** `medium` (see the last section)

## Files touched across modules

**module `projres-repo-core`** (branch `feature/projres-repo-core`, commit `8a4ce6899cc6a661652cccfdc8ed5b076bd268d0`, merged `--no-ff` as `8248cb72f51217bcb951e407a78ee4e8db9e102f`):
- `daemon/repositories/project/repository.py` — +273 / -0: new `SQLModelProjectRepository.resolve_project_reference(query, scope="name")` plus the module-level helpers `_dedupe_projects`, `_resolution_candidates`, `_resolution_ambiguous`, `_resolution_hit` and the constant `_RESOLVE_CANDIDATE_CAP = 10`.
- `daemon/tools/project.py` — +48 / -10: `project_get` calls the ladder; `_FULL_DOCS["project_get"]` rewritten to state the ladder truthfully.
- `tests/test_project_resolution.py` — NEW, 442 lines, 34 test methods.

**module `projres-tool-pins`** (branch `feature/projres-tool-pins`, commit `b93def66aeda57a232a2f41ae5c1321b443eb50e`, merged `--no-ff` as `b3a28900885b7b3674888555bbf774df4d1232fa`):
- `tests/test_project_tools.py` — +279 / -0: 2 new classes (`TestProjectGetResolutionLadder`, `TestProjectGetResolutionRegression`), 15 new test methods (file node count 55 → 70).
- `.agents/tester/PACKS.md` — +8 / -0: one appended registration section (`project_get_resolution_ladder`) with the house begin/end markers.

**integration (sonic-developer)**:
- `.agents/shared/planning/sonic-team/project-get-resolution/HANDOFF.md` — this file.

**Merged-tree footprint:** 4 implementation/test files + 1 registry file + this handoff = **6 files**. No schema/migration file, no new dependency, no other package touched.

## What changed

**Before:** `project_get(name=...)` resolved ONLY through `store.get_by_name(name)` — an exact, case-sensitive `projects.name` match. The project's shortnames were never consulted on the name path, so a reference like `"ensemble"` (a shortname of the project named `agents-ensemble`) missed, returned `{"error": "Project not found", "error_code": "NOT_FOUND"}`, and the agent degraded to `project_list()` + a hand-scan of a huge payload — the Emperor's reported pain. The tool's own doc claimed "Get a project by ID, name, or shortname" while the code did not do that: a doc/behavior mismatch.

**After — the resolution ladder (one owner, `resolve_project_reference`)**, applied to the `name` path:

| Tier | Match rule |
|---|---|
| L1 | exact project NAME (case-sensitive) |
| L2 | exact project SHORTNAME (case-sensitive) |
| L3 | case-insensitive exact project NAME |
| L4 | case-insensitive exact project SHORTNAME |
| L5 | case-insensitive SUBSTRING over NAME + SHORTNAMES only |

The explicit `shortname=` parameter runs the **shortname-only** ladder (exact → case-insensitive exact → case-insensitive substring over SHORTNAMES only) and **never** resolves via the project NAME — so a caller that says "this is a shortname" keeps shortname semantics.

Load-bearing properties of the shipped implementation:
- **The DESCRIPTION is never searched.** L5 covers NAME + SHORTNAMES only — otherwise `"ensemble"` would be ambiguous across every project whose prose mentions it.
- **Never silently pick one of several.** Any tier matching >1 distinct project returns the typed refusal `error_code="AMBIGUOUS_PROJECT"` with `candidates` (each row: `project_id`, `name`, `shortnames`), `candidate_count` (the TOTAL, not the capped length), and an error message that enumerates each candidate. The refusal carries NO top-level `project_id`/`name` — no project is returned.
- **Zero candidates** returns `{"error": "Project not found", "error_code": "NOT_FOUND", "query": ..., "candidates": []}`. The substring "not found" and `error_code` `NOT_FOUND` are preserved (an existing test pins them); only fields were ADDED.
- **No status filter** — a paused/archived/completed project stays resolvable. The pre-existing `match_by_keywords` helper (which filters `status=active`) was NOT copied and NOT modified.
- **Cross-dialect parity**: the case-insensitive tiers use explicit `func.lower(col).contains(query.lower())`; plain `contains()` is case-sensitive on PostgreSQL and case-insensitive on SQLite, so relying on it would have split the two engines. Exact tiers use plain `==` (case-sensitive on both).
- The ladder lives in **ONE repository method**; the tool CALLS it and never re-implements it. The returned project is ENRICHED (`_enrich_project`), so `.shortnames`/`.tags` are populated.
- An unknown `scope` raises `ValueError` (fail-closed) so a bad scope can never widen the ladder to the project NAME.
- Candidate lists are **capped at 10** (`_RESOLVE_CANDIDATE_CAP`) while `candidate_count` reports the true total — a refusal can never become the context-bloat payload this commission set out to remove.

**Tool surface:** `project_get(project_id=..., name=..., shortname=...)` keeps its precedence (`project_id` → `name` → `shortname`) and its no-args message verbatim (`"Must provide either project_id, name, or shortname"`). The success path returns `project.to_dict()` with an unchanged shape (no new keys). The `project_id` path is untouched apart from the refusal now also carrying `query`/`candidates`.

**Sibling seams deliberately NOT touched** (surveyed, reported to the leader):
- `daemon/mcp/kb_server.py` (its own difflib-based fuzzy project resolution) — a different surface, already fuzzy.
- `daemon/api.py` `_resolve_project_workdir_by_shortname` — a deliberately STRICT shortname-only anchor for live-views URLs (unknown shortname → uniform 404). Scope/security-shaped; loosening it would widen a URL scope.
- `daemon/routers/projects.py` `GET /{project_id}` — ID-only by contract with a typed 404; not a name-resolution surface.
- `daemon/tools/project_future.py` and `daemon/tools/project_history.py` — take `project_id`, not a name.

## What is UNTESTED

- **No live-daemon exercise.** The tool was never invoked through a running daemon; the daemon was never booted; port 9797 and the live install were never touched. The pin surface is the real `SQLModelProjectRepository` over in-memory SQLite, not a live process.
- **No MCP / REST integration.** `daemon/mcp/kb_server.py` and the `/api/projects` router were not exercised with the new ladder (they were not changed either) — a reference that resolves through `project_get` may still resolve differently (or not at all) through the MCP kb surface. Cross-surface consistency is NOT tested.
- **No PostgreSQL write path.** The ladder is read-only; PostgreSQL was exercised READ-ONLY (a coder-run dialect smoke against `ensemble_dev`, not integrator-reproduced — see below). No PG write, no PG transaction, no PG lock behaviour was exercised.
- **Non-ASCII case folding is not pinned.** SQLite's `lower()` is ASCII-only; PostgreSQL's `lower()` is full Unicode. The case-insensitive tiers therefore fold differently for non-ASCII references across the two engines. No test covers a non-ASCII reference.
- **L5 wildcard widening is not pinned.** `%` and `_` in a query act as SQL wildcards (the fenced form `func.lower(col).contains(q)` does not autoescape). The only consequence is a WIDER match set — which can only end in the AMBIGUOUS refusal, never a silent pick — but no test pins that.
- **Untrimmed queries are not pinned.** The query is matched raw, so `" ensemble "` (with surrounding spaces) does not resolve. No test pins the trimming behaviour either way.
- **The wider project-tool neighbourhood was not fully exercised.** The full test suite was not run — the gate was scoped to the touched surface. Pre-existing failures in `tests/test_project_future.py` / `tests/test_project_history.py` / `tests/test_project_history_api.py` / `tests/unit/test_project_*` / `tests/unit/test_system_project_bootstrap.py` are PRE-EXISTING: the coder re-ran the same nodes on a pristine `git archive` of base `8894f86a8` in `/tmp` and got an identical failure set (28 across that wider set). The integrator re-ran the 4-file subset on the MERGED tree and observed 26 failed / 65 passed (the 2 extra coder-side failures live in the `tests/unit/test_project_*` files the integrator did not re-run); the integrator did NOT re-derive the base-side comparison.
- **The live DB's shortname-uniqueness was not checked.** The dev DB has 0 duplicate shortnames (queried read-only). The LIVE DB was NOT queried — the commission forbids ensemble_prod contact — so a live shortname shared by two projects (possible: the `project_shortnames` primary key is the composite `(project_id, shortname)`, so a shortname string is NOT globally unique) would change behaviour for an existing caller. See "Known risks".
- **The release was NOT promoted.** No live promote, no daemon restart, no release-symlink move. The staged payload was assembled but never promoted; the Emperor owns that ceremony.

## Known risks

1. **A shortname shared by 2+ projects now REFUSES where the old code silently picked one.** The old `get_by_shortname` used `.first()` (an arbitrary pick among duplicates). Per the commission's "never silently pick one of several candidates" constraint, the ladder now returns the AMBIGUOUS refusal instead. This is intended, but it IS a behaviour change on a path that previously "worked" (arbitrarily). The refusal is recoverable — it lists each candidate's `project_id` — but an existing live caller that relied on the arbitrary pick will now get a refusal. Live data was not inspected (prod contact forbidden).
2. **The AMBIGUOUS message is the frozen sentence PLUS a ` Candidates: ...` suffix.** A `startswith`/`in` assertion passes; a bare exact-equality assertion on the whole message would not.
3. **`project_get(project_id=<missing>)` now also carries `query`/`candidates` in its refusal.** Additive only — the pinned "not found" and "NOT_FOUND" strings are intact.
4. **The `shortname=` path gained a substring tier** (today it was exact-only). Every input that resolved before still resolves to the SAME project (exact tiers are unchanged and come first); only inputs that previously returned NOT_FOUND can now resolve.
5. **Cross-surface inconsistency (accepted, not fixed here).** `daemon/mcp/kb_server.py` keeps its own difflib resolution with different thresholds (0.6/0.7/0.8/0.85) and its own "did you mean" UX. The same reference can resolve one way through `project_get` and another way through the MCP kb surface. Unifying them is a RECOMMENDED follow-up lane, not implemented here.
6. **`project_future_*` / `project_history_*` still require `project_id`.** An agent that only knows the shortname cannot drive them without first resolving via `project_get`. RECOMMENDED follow-up: accept a name/shortname reference on those tools through the same repository method.
7. **The PACKS.md row describes "13 new nodes"; the file actually carries 15 new test methods** (55 → 70 nodes in `tests/test_project_tools.py`). The 13 enumerates distinct BEHAVIOURS; two of them are covered by two methods each. The row's Invocation / Last run / Status cells are exact.
8. **The registered pack command runs in the integration worktree path** `/home/nea/ensemble-src-wt-project-get` (stated in the row). If the branch is checked out elsewhere, re-point the path.
9. **26–28 pre-existing failures** in the wider project-history/future surface remain open (26 on the integrator's 4-file re-check; 28 in the coder's wider set) — NOT lane-attributable (proven identical on a pristine base archive by the coder), but they are still red in the tree.
10. **Sibling-lane concurrency** was watched: `origin/latest` was still `8894f86a8` at merge time and at the pre-push re-check; a sibling lane (`feature/pilot-orphanwatcher-engine`) was active on the same base during the lane.

## How to verify

All commands run from a worktree of the branch with the worktree's OWN `.venv` (never the main checkout's), and with `daemon.__file__` proven to resolve inside the worktree.

```bash
cd /home/nea/ensemble-src-wt-project-get

# 1) The REGISTERED pack command (see .agents/tester/PACKS.md -> project_get_resolution_ladder).
#    Expected: 104 passed, 0 failed.
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
  ENSEMBLE_SELF_ENV=dev PYTHONPATH=/home/nea/ensemble-src-wt-project-get \
  .venv/bin/python -m pytest tests/test_project_tools.py tests/test_project_resolution.py \
  -q --tb=short -p no:randomly --timeout=60

# 2) The FULL touched surface (the pack + the pre-existing store/repository surface).
#    Expected: 267 passed, 0 failed.  Pre-fix baseline on the same tree was 218 passed.
PYTHONPATH=/home/nea/ensemble-src-wt-project-get \
  .venv/bin/python -m pytest tests/test_project_tools.py tests/test_project_resolution.py \
  tests/test_project_store.py tests/test_project_store_sqlmodel.py tests/test_project_repository_atomic.py \
  -q --tb=short -p no:randomly --timeout=60

# 3) The wider project-tool neighbourhood — expect PRE-EXISTING failures ONLY
#    (integrator-measured on this merged tree: 26 failed / 65 passed; the coder's wider
#    set, which also included tests/unit/test_project_*, counted 28 — proven identical
#    on a pristine `git archive` of base 8894f86a8).
PYTHONPATH=/home/nea/ensemble-src-wt-project-get \
  .venv/bin/python -m pytest tests/test_project_future.py tests/test_project_history.py \
  tests/test_project_history_api.py tests/unit/test_system_project_bootstrap.py -q --tb=no -p no:randomly --timeout=60

# 4) Behavioural spot-check of the Emperor's exact case (in-memory SQLite, no daemon):
#    create a project named "agents-ensemble", give it the shortnames ["ens","ensemble"],
#    then project_get(name="ensemble") must return THAT project_id (it used to return NOT_FOUND).

# 5) Release identity — the tag MUST match daemon/__init__.py __version__ or the promote
#    version-verify gate fails closed:
git show v0.31.28:daemon/__init__.py | grep __version__      # -> "0.31.28"
git show v0.31.28:pyproject.toml  | grep -m1 '^version'       # -> version = "0.31.28"
```

The PostgreSQL dialect check reported by the coder (a read-only smoke against `ensemble_dev`, asserting `current_database() == ensemble_dev`, covering L1 `name_exact`, L3 `name_exact_ci`, L5 `name_substring` and `scope="shortname"` → NOT_FOUND) was run from a scratch script that was NOT committed; the integrator did not re-run it. Treat it as coder-reported evidence, not an integrator-reproduced result.

## Bash-raises used

**none.** Every bash command stayed inside the default 600 s ceiling; no raise was requested from sonic-leader and none was consumed. The three long operations were run as BACKGROUND processes (`proc_run`), never as blocking bash calls: the three `uv sync` venv builds (one per worktree) and the pytest gate runs (86–90 s each, four runs total, plus the intermediate check between merges). The `--timeout=60` per-test timeout was used on every pytest invocation; no run hit the 1800 s tool cap.

## Complexity self-assessment

**`medium`** — the authoritative verdict, assessed after the plan-open explore and the module split, and BEFORE any coder was dispatched. No handover was raised (the band was medium, not high/extremely-high), and nothing was overridden.

Why medium rather than low:
- The change is a NEW resolution ALGORITHM with an ordered 5-tier ladder and two scopes, not a lookup tweak.
- It introduces a typed-refusal CONTRACT on a surface agents parse (`AMBIGUOUS_PROJECT` + candidate enumeration + a candidate cap), i.e. a new public shape with an ambiguity policy attached.
- It carries a cross-dialect SQL parity requirement (explicit `func.lower()`, no reliance on `contains()` case semantics) and a hard constraint set (no description matching, no status filter, never silently pick, preserve the "not found" substring and the "Must provide either" message).
- Two disjoint modules had to agree on ONE frozen dict contract across a merge seam; the seam was real and it held.

Why not high: no schema/migration change, no new dependency, no architectural coupling, no live/release surface touched, the whole diff is 6 files, and the touched surface is verifiable in ~90 s on an in-memory SQLite repository.

**Process note (honest):** the ONE bounded `explore()` at plan-open **timed out** (explorer agent timeout after 300 s, instance terminated). No stored experience was therefore available to distill into the module briefs; the briefs were built from the commission brief plus direct source verification of every anchor. The plan did not depend on the knowledge base.
